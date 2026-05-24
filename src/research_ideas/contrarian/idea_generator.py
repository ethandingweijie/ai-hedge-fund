"""
src/research_ideas/contrarian/idea_generator.py
=================================================
Generates a single "Research Idea of the Day" — a deep-value, asymmetric,
contrarian investment hypothesis backed by live web search.

LLM: Qwen3.6-plus with native web search (DashScope enable_search=True).
Cost: ~$0.01 per generated idea (~5K input + 2K output tokens).
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from src.research_ideas.complacency.web_research import qwen_web_search

logger = logging.getLogger(__name__)


# ─── System prompt — encodes the methodology ──────────────────────────────


_SYSTEM_PROMPT = """\
You are a contrarian deep-value research analyst in the tradition of Marty
Whitman, Howard Marks, and Seth Klarman. You generate ONE investment idea
per day that meets ALL THREE pillars:

  1. DEEP VALUE  — trading at a meaningful discount to tangible book / NAV
     / private-market value, with concrete margin-of-safety. Cite specific
     multiples (EV/EBITDA, P/B, P/TBV, FCF yield) versus sector and the
     stock's own history.

  2. ASYMMETRIC RETURN  — upside meaningfully larger than downside.
     Downside is BOUNDED by tangible assets, net cash, or franchise floor.
     Upside scenario produces a 2-5x return on a 2-4 year horizon. Use
     concrete price targets.

  3. CONTRARIAN  — actively avoided by retail and/or sell-side. Sell/Hold
     ratings dominate, short interest is high, narrative is "broken" or
     "in decline". You ARE NOT looking for: AI-hype names, mega-cap tech,
     crowded longs, hot sectors, memes, or anything trending on r/wsb or
     fintwit.

EXCLUSIONS — DO NOT recommend:
  • Mega-caps (market cap > $200B) — too crowded for retail-investor edge
  • Names that have rallied > 50% in the last 6 months
  • AI/data-center/semiconductor narrative plays
  • Crypto, SPACs, recent IPOs (< 2 years)
  • Companies in active SEC fraud investigations

PREFERRED HUNTING GROUNDS:
  • Spin-offs and post-spin orphans (forced selling = mispricing)
  • Cyclicals at trough earnings (steel, shipping, drillers, autos)
  • Regulated industries hated by ESG flows (tobacco, defense, fossil-fuels)
  • Post-bankruptcy survivors with cleaned-up balance sheets
  • Small/mid-cap dark stocks with thin sell-side coverage
  • Family-controlled / founder-owned companies trading below NAV
  • Distressed credit / restructurings with equity stub upside

OUTPUT: STRICT JSON matching the schema. Every claim cited via web search
must include a Tier-1 source (SEC filing, Reuters, Bloomberg, WSJ, FT)
where possible. Tier-3 (random blogs) sources cap conviction at 6.
"""


def _build_user_prompt(exclude_tickers: list[str] | None = None) -> str:
    excl_text = ""
    if exclude_tickers:
        excl_text = (
            f"\n\nDO NOT pick any of these tickers (already shown today): "
            f"{', '.join(exclude_tickers[:50])}."
        )

    return f"""\
TASK: Use web search to find ONE current contrarian deep-value opportunity
in the US equity market. Today's date is {datetime.now(timezone.utc).strftime('%Y-%m-%d')}.

PROCESS (do this in order):
  1. Search the web for: "deep value contrarian 2025 2026 hated stocks
     spin-off cyclical bottom small-cap insider buying" or similar.
     Look for actual investment writeups, not generic listicles.
  2. Pick ONE high-conviction name that genuinely fits all three pillars.
  3. Verify with additional searches: market cap, recent price action,
     sell-side ratings, insider activity, latest 10-K/10-Q disclosures,
     and any active catalysts.
  4. Build the full hypothesis card with CONCRETE numbers and Tier-1
     citations.{excl_text}

OUTPUT: STRICT JSON only, no commentary, matching this schema EXACTLY:

{{
  "ticker": "<US ticker symbol>",
  "company_name": "<full legal name>",
  "sector": "<GICS sector>",
  "industry": "<industry>",
  "market_cap_usd": <number — current market cap in USD>,
  "hypothesis": "<≤200 char one-sentence thesis — why now + asymmetric setup>",
  "deep_value_angle": "<≤400 chars: concrete multiples vs sector + history>",
  "asymmetric_angle": "<≤400 chars: explicit upside/downside price targets>",
  "contrarian_angle": "<≤400 chars: sell-side stance + retail narrative>",
  "primary_catalyst": "<concrete trigger>",
  "catalyst_timeline": "<e.g. 'Q2 2026 earnings' / 'H2 2026 spin-off'>",
  "key_risks": ["<risk 1>", "<risk 2>", "<risk 3>"],
  "conviction_score": <1-10 int>,
  "deep_value_score": <1-10 int>,
  "asymmetry_score": <1-10 int>,
  "contrarian_score": <1-10 int>,
  "sources": [
    {{"title": "<publisher/headline>", "url": "<url>", "date": "<yyyy-mm-dd>"}}
  ]
}}

VALIDATION CHECKLIST (your output will be rejected if it fails):
  ✗ Mega-cap (>$200B): reject
  ✗ Up > 50% in last 6 months: reject
  ✗ AI / semiconductor / data-center hype name: reject
  ✗ Generic prose without specific multiples: reject
  ✗ Fewer than 2 sources cited: reject
  ✓ Bounded downside with explicit assets/cash floor
  ✓ Sell/Hold-heavy or thinly-covered name
  ✓ Identifiable catalyst with timeline
"""


def generate_idea_of_the_day(
    exclude_tickers: list[str] | None = None,
    max_retries: int = 2,
) -> Optional[dict]:
    """
    Asks Qwen-with-web-search to find and write up ONE contrarian deep-value
    idea. Returns a dict matching the ContrarianIdea schema, or None on
    failure.

    Pass `exclude_tickers` to avoid duplicates with recently-generated ideas.
    """
    user_prompt = _build_user_prompt(exclude_tickers=exclude_tickers)

    raw = None
    for attempt in range(max_retries + 1):
        raw = qwen_web_search(
            user_prompt=user_prompt,
            system_prompt=_SYSTEM_PROMPT,
            max_chars=10000,
        )
        if raw and raw.strip():
            break

    if not raw:
        logger.error("generate_idea_of_the_day: Qwen returned empty after %d attempts", max_retries + 1)
        return None

    # Extract JSON
    try:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            logger.error("generate_idea_of_the_day: no JSON in Qwen response")
            return None
        parsed = json.loads(raw[start : end + 1])
    except Exception as exc:
        logger.error("generate_idea_of_the_day: JSON parse failed: %s", exc)
        return None

    # Validate minimum fields
    required = ("ticker", "hypothesis", "deep_value_angle", "asymmetric_angle", "contrarian_angle")
    missing = [f for f in required if not parsed.get(f)]
    if missing:
        logger.error("generate_idea_of_the_day: missing required fields: %s", missing)
        return None

    # Normalize + clamp
    try:
        parsed["ticker"] = str(parsed["ticker"]).upper().strip()
        parsed["conviction_score"] = max(1, min(10, int(parsed.get("conviction_score") or 5)))
        parsed["deep_value_score"] = max(1, min(10, int(parsed.get("deep_value_score") or 5)))
        parsed["asymmetry_score"] = max(1, min(10, int(parsed.get("asymmetry_score") or 5)))
        parsed["contrarian_score"] = max(1, min(10, int(parsed.get("contrarian_score") or 5)))
    except Exception as exc:
        logger.warning("generate_idea_of_the_day: score normalisation failed: %s", exc)

    # Fill meta
    parsed["idea_id"] = uuid.uuid4().hex[:16]
    parsed["generated_at"] = datetime.now(timezone.utc).isoformat()
    parsed["model_used"] = "qwen3.6-plus"
    parsed["cost_usd"] = 0.01  # rough estimate for Qwen + search

    # Coerce optional list fields
    parsed.setdefault("sources", [])
    parsed.setdefault("key_risks", [])
    if not isinstance(parsed["sources"], list):
        parsed["sources"] = []
    if not isinstance(parsed["key_risks"], list):
        parsed["key_risks"] = []

    return parsed


if __name__ == "__main__":
    import sys
    from dotenv import load_dotenv
    load_dotenv(".env.local")
    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    print("Generating today's contrarian idea via Qwen web search...")
    idea = generate_idea_of_the_day()
    if not idea:
        print("FAILED")
    else:
        print(json.dumps(idea, indent=2)[:3000])
