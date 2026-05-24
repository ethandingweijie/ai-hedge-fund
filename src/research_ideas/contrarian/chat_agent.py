"""
src/research_ideas/contrarian/chat_agent.py
=============================================
Discussion agent — lets the user refine and stress-test a generated idea
through back-and-forth chat. The agent stays grounded in the original
hypothesis card (passed as context) and uses web search to look up new
data when the user asks.

LLM: same Qwen3.6-plus + native web search. Cost: ~$0.005-0.01 per turn.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


def _qwen_client():
    """OpenAI-compatible client for DashScope (chat-style, with optional web search)."""
    try:
        from openai import OpenAI
    except ImportError:
        return None
    api_key = os.environ.get("DEEP_RESEARCH_API_KEY")
    if not api_key:
        return None
    base_url = os.environ.get(
        "DEEP_RESEARCH_SEARCH_BASE_URL",
        "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    )
    return OpenAI(api_key=api_key, base_url=base_url, timeout=180)


_SYSTEM_PROMPT = """\
You are a contrarian deep-value investment analyst discussing a specific
research idea with the user. The user has pre-existing context: the idea
card with hypothesis, deep-value / asymmetric / contrarian angles,
catalyst, and risks. Your job is to help them STRESS-TEST the thesis.

GUIDELINES:
  • Always stay grounded in the original hypothesis card.
  • When the user asks for new data (recent earnings, sell-side note,
    competitor metric), use web search to fetch it — don't hallucinate.
  • Be intellectually honest: if the user surfaces a fact that weakens
    the thesis, ACKNOWLEDGE IT. Don't defend the original conviction
    score if new evidence undermines it.
  • Keep responses focused. Maximum 400 words per response. Use bullet
    points for clarity.
  • End each response with a probing question or a "what would change
    your mind" frame to encourage deeper analysis.
  • If asked for a price target, justify it with concrete multiples.
"""


def _format_idea_context(idea: dict) -> str:
    """Render the idea card as compact context for the LLM."""
    sources_str = "; ".join(
        f"{s.get('title','?')[:60]} ({s.get('date','?')})"
        for s in (idea.get("sources") or [])[:5]
    ) or "(none)"
    return f"""\
═══════════════════════════════════════════════════════════════════════
CURRENT IDEA CARD (DO NOT INVENT NEW FIELDS — these are ground truth)
═══════════════════════════════════════════════════════════════════════
Ticker        : {idea.get('ticker', '?')}
Company       : {idea.get('company_name', '?')}
Sector        : {idea.get('sector', '?')}
Market cap    : ${(idea.get('market_cap_usd') or 0) / 1e9:.1f}B

Hypothesis    : {idea.get('hypothesis', '')}

Deep value    : {idea.get('deep_value_angle', '')}
Asymmetric    : {idea.get('asymmetric_angle', '')}
Contrarian    : {idea.get('contrarian_angle', '')}

Catalyst      : {idea.get('primary_catalyst', '')}
Timeline      : {idea.get('catalyst_timeline', '')}
Key risks     : {chr(10).join('  • ' + r for r in (idea.get('key_risks') or []))}

Conviction    : {idea.get('conviction_score', '?')}/10
  Deep value  : {idea.get('deep_value_score', '?')}/10
  Asymmetry   : {idea.get('asymmetry_score', '?')}/10
  Contrarian  : {idea.get('contrarian_score', '?')}/10

Sources cited : {sources_str}
═══════════════════════════════════════════════════════════════════════
"""


def chat_turn(
    idea: dict,
    history: list[dict],
    user_message: str,
    enable_search: bool = True,
    max_chars: int = 6000,
) -> Optional[dict]:
    """
    Run one chat turn. Returns {content, cost_usd, error?} or None when the
    Qwen client cannot be constructed at all (e.g. openai package missing).

    On Qwen failure, returns {content: "(error: ...)", cost_usd: 0, error: ...}
    so the route can surface the ACTUAL reason instead of a generic message.

    `history` is the existing conversation as a list of {role, content} dicts
    (oldest first). `user_message` is the new message to respond to.
    """
    if not os.environ.get("DEEP_RESEARCH_API_KEY"):
        return {
            "content": (
                "(agent unavailable — DEEP_RESEARCH_API_KEY is not set "
                "in this deployment's environment. Add it to Railway "
                "Variables and redeploy.)"
            ),
            "cost_usd": 0.0,
            "error": "missing_env_var:DEEP_RESEARCH_API_KEY",
        }

    client = _qwen_client()
    if client is None:
        return {
            "content": (
                "(agent unavailable — Qwen client could not be constructed. "
                "Verify the openai package is installed in the backend.)"
            ),
            "cost_usd": 0.0,
            "error": "qwen_client_init_failed",
        }

    # Compose messages. CRITICAL: Qwen DashScope rejects requests with more
    # than ONE system message ("Agent invalid parameter: The input messages
    # must contain no more than one system message"). Merge our forensic-
    # analyst system prompt and the idea-card context into a single system
    # message — this was the actual root cause of "(agent unavailable —
    # DEEP_RESEARCH_API_KEY missing or Qwen failed)" on production: the
    # key WAS set, but every call returned 400.
    merged_system = _SYSTEM_PROMPT + "\n\n" + _format_idea_context(idea)
    messages = [{"role": "system", "content": merged_system}]

    # Trim history to last 12 turns to keep cost bounded
    for m in (history or [])[-12:]:
        role = m.get("role", "user")
        if role not in ("user", "assistant"):
            continue
        messages.append({"role": role, "content": m.get("content", "")})
    messages.append({"role": "user", "content": user_message})

    extra_body = {}
    if enable_search:
        extra_body = {
            "enable_search": True,
            "search_options": {"search_strategy": "agent"},
        }

    text = ""
    last_exc_msg: Optional[str] = None
    try:
        stream = client.chat.completions.create(
            model=os.environ.get("CONTRARIAN_CHAT_MODEL", "qwen3.6-plus"),
            messages=messages,
            extra_body=extra_body if enable_search else None,
            stream=enable_search,   # web search requires streaming
        )
        if enable_search:
            for chunk in stream:
                delta = getattr(chunk.choices[0] if chunk.choices else None, "delta", None)
                if not delta:
                    continue
                c = getattr(delta, "content", None)
                if c:
                    text += c
        else:
            text = (stream.choices[0].message.content or "").strip()
    except Exception as exc:
        logger.exception("chat_turn LLM call failed: %s", exc)
        last_exc_msg = f"{type(exc).__name__}: {str(exc)[:300]}"

    text = (text or "").strip()
    if not text:
        # Retry without web search (sometimes streaming drops content phase)
        if enable_search:
            logger.warning("chat_turn: empty stream — retrying without web search")
            try:
                resp = client.chat.completions.create(
                    model=os.environ.get("CONTRARIAN_CHAT_MODEL", "qwen3.6-plus"),
                    messages=messages,
                    stream=False,
                    temperature=0.4,
                )
                text = (resp.choices[0].message.content or "").strip()
            except Exception as exc:
                logger.exception("chat_turn fallback failed: %s", exc)
                last_exc_msg = (
                    last_exc_msg or
                    f"fallback {type(exc).__name__}: {str(exc)[:300]}"
                )

    if not text:
        # Both paths failed — return the specific error rather than None so
        # the user sees the ACTUAL reason in the chat panel.
        err = last_exc_msg or "Qwen returned empty content"
        return {
            "content": f"(agent error: {err})",
            "cost_usd": 0.0,
            "error": err,
        }

    # Rough cost estimate
    input_chars = sum(len(m["content"]) for m in messages)
    cost_usd = (input_chars / 3 / 1e6 * 0.5) + (len(text) / 3 / 1e6 * 2.0)

    return {
        "content": text[:max_chars],
        "cost_usd": round(cost_usd, 5),
    }
