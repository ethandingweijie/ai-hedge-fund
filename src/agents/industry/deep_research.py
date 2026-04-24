"""
Phase 3.5 — Deep Research Agent

Three-tier search strategy (each tier falls through to the next on any failure):

  Tier 1 — Anthropic native web search (web_search_20250305)
    Claude calls Anthropic's built-in search tool (beta header required).
    Best quality: live sources, current prices, recent filings.

  Tier 2 — Tavily agentic loop (TAVILY_API_KEY required)
    Claude drives an iterative search loop; each query executed via TavilyClient.
    Good quality: live web, but client-side execution.

  Tier 3 — Knowledge-only (no external keys required)
    Claude writes the Section 2 report from training knowledge (≤ early 2025).
    Always works as long as ANTHROPIC_API_KEY is valid.  This is the pre-Aug 2025
    baseline that produced the original 230-line report.

Fallback chain:
  Tier 1 fails → Tier 2 (if TAVILY_API_KEY set) → Tier 3
  Tier 1 fails + no Tavily key              → Tier 3
  All three fail                            → deep_research = "" (pipeline continues on financials only)
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime

import anthropic

from src.graph.state import AgentState
from src.utils.progress import progress
from src.utils.company_name import fetch_company_name as _fetch_company_name


logger = logging.getLogger(__name__)


def _call_llm_with_rate_retry(
    sdk_client,
    *,
    extractor_name: str,
    ticker: str = "",
    max_retries: int = 5,
    base_backoff: float = 3.0,
    max_wait: float = 60.0,
    **create_kwargs,
):
    """Wrapper around sdk_client.messages.create() with DashScope rate-limit retry.

    Qwen via DashScope returns HTTP 403 with message "Rate limit exceeded"
    when the account hits its TPM/RPM ceiling — instead of the standard
    HTTP 429. The Anthropic SDK's built-in max_retries only retries on
    408/409/429/500+ so 403 RateLimit errors fall through silently, the
    extractor's except-catch returns {}, and the user sees empty KPIs.

    Defaults (v2 — 2026-04-24):
      max_retries=5, base_backoff=3.0, max_wait=60.0
      Budget (worst case): 3 + 6 + 12 + 24 + 48 = 93s
      Cap per wait: 60s (so 48s → 48s, no cap yet)

    Features:
      * Catches any exception whose message matches rate-limit / quota /
        throttle — covers both anthropic SDK (PermissionDeniedError) and
        openai SDK (RateLimitError, PermissionDeniedError) errors.
      * Respects HTTP Retry-After header when the exception carries an
        upstream response object. Respects server's guidance OR uses
        our computed backoff — whichever is larger (so we don't hammer
        sooner than the server requested).
      * Exponential backoff: 3s → 6s → 12s → 24s → 48s (capped at max_wait).

    All other exceptions propagate immediately — auth errors, bad request,
    server errors handled by SDK's own retry mechanism.

    Usage: drop-in replacement for sdk_client.messages.create(**kwargs).
    Same return type.
    """
    import time as _time

    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            return sdk_client.messages.create(**create_kwargs)
        except Exception as exc:
            msg = str(exc).lower()
            is_rate_limit = (
                "rate limit" in msg
                or "ratelimit" in msg
                or "rate_limit" in msg
                or "quota" in msg
                or "throttl" in msg
                or "accessdenied" in msg
                or "access_denied" in msg
            )
            if not is_rate_limit:
                # Not a rate limit — propagate (auth errors, bad request,
                # server errors handled by SDK retry)
                raise
            last_exc = exc
            if attempt == max_retries:
                # Exhausted retries — propagate
                print(
                    f"  [llm_rate_retry] {extractor_name}{' ' + ticker if ticker else ''} "
                    f"exhausted {max_retries} retries: {type(exc).__name__}: {str(exc)[:200]}"
                )
                raise

            # Exponential backoff
            computed_wait = min(base_backoff * (2 ** attempt), max_wait)

            # Respect server's Retry-After if present (openai SDK exposes
            # this on APIStatusError.response.headers; anthropic SDK has
            # it on BadRequestError.response too but naming varies)
            retry_after_seconds: float | None = None
            try:
                response = getattr(exc, "response", None)
                if response is not None:
                    headers = getattr(response, "headers", None)
                    if headers is not None:
                        ra = headers.get("Retry-After") or headers.get("retry-after")
                        if ra:
                            retry_after_seconds = float(ra)
            except (ValueError, TypeError, AttributeError):
                retry_after_seconds = None

            if retry_after_seconds is not None:
                # Take the larger of (server guidance, our backoff) — we
                # never hammer sooner than the server requested, but we
                # also never wait less than our minimum backoff.
                wait = max(retry_after_seconds, computed_wait)
                wait_source = f"Retry-After={retry_after_seconds:.0f}s"
            else:
                wait = computed_wait
                wait_source = "exponential"

            print(
                f"  [llm_rate_retry] {extractor_name}{' ' + ticker if ticker else ''} "
                f"rate-limited (attempt {attempt + 1}/{max_retries + 1}), "
                f"sleeping {wait:.0f}s [{wait_source}]..."
            )
            _time.sleep(wait)
    # Should never reach here, but mypy comfort
    if last_exc:
        raise last_exc
    raise RuntimeError("Unreachable: exited retry loop without result or exception")


def _parse_llm_json(raw: str, extractor_name: str = "") -> dict | list | None:
    """Robust JSON parser for LLM extractor responses.

    The previous approach was `json.loads(raw.strip())` with only ```-fence
    stripping. This broke silently when Qwen / Claude added any of:

    1. Preamble text  : "Here's the extraction:\n{...}"
    2. Postamble text : "{...}\n\nNote: all figures approximate."
    3. Mixed fences   : "Some text\n```json\n{...}\n```\nExplanation..."
    4. Trailing junk  : "{...}\n<end_of_turn>"

    All of these raised JSONDecodeError → caught by `except Exception: return {}`
    in each extractor → user saw empty KPI tiles + empty commentary cards
    despite Qwen having produced well-formed data in Section 2F.

    Observed on DDOG (Growth SaaS, Qwen 3.6-plus via DashScope): full 2F with
    NRR 120%, Rule of 40 57%, Magic Number 0.61, CAC Payback 14-16mo all
    present in text but saas_metrics came back as {} because Qwen added
    a 3-line preamble before the JSON.

    Strategy (most-specific → most-permissive):
      1. Try raw as-is after trimming whitespace.
      2. Strip surrounding ```fences``` (any language tag).
      3. Find first { ... last } (for objects) or first [ ... last ] (for arrays)
         and parse that substring.
      4. Return None on all failures — caller decides whether to return empty
         dict / empty list / default.

    Returns dict or list on success, None on failure. Logs a truncated preview
    to stdout so Railway shows which extractors are losing data and why.
    """
    if not raw or not isinstance(raw, str):
        return None
    text = raw.strip()

    # Strategy 1 — try as-is (fast path for compliant responses)
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass

    # Strategy 2 — strip triple-backtick fences
    stripped = text
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z]*\n?", "", stripped)
    if stripped.endswith("```"):
        stripped = re.sub(r"\n?```$", "", stripped)
    stripped = stripped.strip()
    if stripped != text:
        try:
            return json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            pass

    # Strategy 3a — substring between first { and last } (object case)
    first_brace = text.find("{")
    last_brace  = text.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        candidate = text[first_brace : last_brace + 1]
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass

    # Strategy 3b — substring between first [ and last ] (array case)
    first_brack = text.find("[")
    last_brack  = text.rfind("]")
    if first_brack != -1 and last_brack > first_brack:
        # Only prefer array when brackets come before any braces (else it's a
        # nested array inside an object that failed 3a already).
        if first_brace == -1 or first_brack < first_brace:
            candidate = text[first_brack : last_brack + 1]
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, list):
                    return parsed
            except (json.JSONDecodeError, ValueError):
                pass

    # All strategies failed — log truncated preview for ops visibility
    _preview = text[:300].replace("\n", " ⏎ ")
    print(
        f"  [llm_json_parse] ⚠ {extractor_name or 'extractor'} failed to parse "
        f"(len={len(text)}): {_preview}..."
    )
    return None


MAX_SEARCHES     = 10     # searches for both Tier 1 (max_uses) and Tier 2 (loop cap)
MAX_RESULTS      = 5      # Tavily results per query
# max_tokens controls OUTPUT tokens (the generated response), not the context window.
# The Anthropic API ceiling for claude-sonnet-4-6 is 64,000 output tokens.
# Setting this at the model ceiling ensures the synthesis pass never gets cut off
# mid-report due to a token budget exhaustion.
MAX_TOKENS       = 64000
# Root cause of Tier 1 → Tier 2 dropout:
# The system prompt requires ≥8 web searches before synthesis. Each search is
# executed server-side by Anthropic and takes 15–30 s. 10 searches + synthesis
# = 3–6 minutes, which regularly exceeded the previous 300 s (5 min) limit.
# anthropic.APITimeoutError was raised after the searches completed (tokens
# consumed) but before synthesis finished → caught by except → fell to Tavily.
# 720 s (12 min) gives comfortable headroom for 10 searches + full synthesis.
CLIENT_TIMEOUT   = 720.0

# Application-level Tier 1 retry settings.
# APIConnectionError / APITimeoutError are transient — retry before falling to Tier 2.
# Non-retryable errors (AuthenticationError, BadRequestError) bypass immediately.
_T1_MAX_RETRIES = 2     # extra attempts beyond the first before giving up on Tier 1
_T1_RETRY_WAIT  = 20.0  # seconds to wait between Tier 1 attempts

# ── Archive-first cache constants ─────────────────────────────────────────────
# If a live-search run for this ticker exists in run_archive.db and is newer
# than _FRESH_DAYS, skip or reduce the search pass.
_FRESH_DAYS          = 14   # reuse window (days); runs older than this → full fresh search
_CACHE_NO_DELTA_DAYS = 3.0  # age below which zero searches are needed (cache inject only)
_DELTA_MAX_SEARCHES  = 4    # searches in delta pass (vs 12 full)
# Age < 3d  → pure cache (0 searches)
# Age 3–14d → delta path: 4 targeted web searches since last run date
#              + Phase 2.5 news_sentiment injected as "recent_news" section via LLM synthesis
# Age > 14d → full fresh search (12 searches)

# web_search_20260209 — generally available (no beta header required).
# Adds dynamic filtering on Opus 4.6 / Sonnet 4.6: Claude writes and executes
# code to filter search result HTML before loading into context, keeping only
# relevant content.  This lowers token consumption and improves accuracy for
# deep financial research.  The previous "web_search_20250305" required the
# anthropic-beta header and emitted "tool_use" blocks; the new version uses
# "server_tool_use" blocks and needs no extra headers.
_WEB_SEARCH_TOOL_VERSION = "web_search_20260209"

# ── Narration filter ──────────────────────────────────────────────────────────
# Patterns that identify LLM meta-commentary rather than research content.
# These appear when the model narrates its own process instead of writing findings.
# Applied to Tier 1 output before it is stored or passed to the specialist.
_NARRATION_PATTERNS = [
    re.compile(r"(?im)^.*code[_\s-]?execution tool.*$"),
    re.compile(r"(?im)^.*the (web_?search|search) tool appears.*$"),
    re.compile(r"(?im)^.*(rate[_\s-]?limit|hitting limits|hitting the limit).*$"),
    re.compile(r"(?im)^.*let me (run|search|use|wait|try|compile|conduct).*$"),
    re.compile(r"(?im)^.*I'?ll (conduct|run|search|try|compile|use|gather|perform).*$"),
    re.compile(r"(?im)^.*simultane(ous|ly).*$"),
    re.compile(r"(?im)^.*different approach.*$"),
    re.compile(r"(?im)^.*compile (all|the) data (already )?collected.*$"),
    re.compile(r"(?im)^.*before writing the report.*$"),
    re.compile(r"(?im)^.*remaining essential searches.*$"),
]


def _strip_narration(text: str) -> str:
    """Remove LLM meta-commentary lines from a research report text block."""
    if not text:
        return text
    lines = text.splitlines()
    cleaned = [ln for ln in lines if not any(p.match(ln) for p in _NARRATION_PATTERNS)]
    # Collapse runs of more than two consecutive blank lines left by removals
    result: list[str] = []
    blank_run = 0
    for ln in cleaned:
        if ln.strip() == "":
            blank_run += 1
            if blank_run <= 2:
                result.append(ln)
        else:
            blank_run = 0
            result.append(ln)
    return "\n".join(result)


# ── Tier 2: Tavily search helper ───────────────────────────────────────────────

def _search_web(
    query: str,
    tavily_api_key: str,
    citation_sink: list | None = None,
) -> str:
    """
    Execute a single Tavily search and return formatted snippets.

    citation_sink: if provided (a list), each search result's {title, url, date}
    is appended so the caller can build a Tavily citation registry.
    """
    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=tavily_api_key)
        response = client.search(
            query=query,
            max_results=MAX_RESULTS,
            search_depth="advanced",
        )
        snippets = []
        for r in response.get("results", []):
            title     = r.get("title", "")
            content   = r.get("content", "")[:500]
            url       = r.get("url", "")
            published = r.get("published_date", "")
            date_tag  = f" [{published[:10]}]" if published else ""
            snippets.append(f"**{title}**{date_tag}\n{content}\nSource: {url}")
            # Capture URL so it can be added to the citation seed registry
            if citation_sink is not None and url:
                citation_sink.append({
                    "title":      title,
                    "url":        url,
                    "cited_text": content[:150],
                    "date":       published[:10] if published else "",
                })
        return "\n\n---\n\n".join(snippets) if snippets else "No results found."
    except Exception as e:
        return f"Search error: {e}"


# ── Tier 2: Tavily tool schema (client-side execution) ─────────────────────────

_TAVILY_TOOL = {
    "name": "search_web",
    "description": (
        "Search the web for current financial news, industry data, M&A activity, "
        "earnings reports, regulatory filings, and competitive intelligence. "
        "Use specific, targeted queries for best results."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query to execute.",
            }
        },
        "required": ["query"],
    },
}


# ── Tier 1: Anthropic native web search tool schema (server-side) ──────────────

_WEB_SEARCH_TOOL = {
    "type": _WEB_SEARCH_TOOL_VERSION,  # "web_search_20260209" — no beta header needed
    "name": "web_search",
    "max_uses": MAX_SEARCHES,
}

# Minimum searches required before we consider Tier 1 "live".
# Even 1 confirmed live web_search call proves the tool ran and the model
# touched the open web — far better than falling through to training data.
# Previous value of 3 caused over-aggressive fallback to knowledge_only when
# the API completed quickly or returned few tool calls on straightforward queries.
_MIN_LIVE_SEARCHES = 1


# ── Section extractor ──────────────────────────────────────────────────────────

def _extract_sections(report_text: str) -> dict[str, str]:
    """
    Parse the Section 2 report into individual sub-sections keyed by id.
    Returns {"2a": text, "2b": text, ..., "2f": text}.
    Falls back to {"full": report_text} if no section headers are found.

    Downstream consumers:
      2b → scenario_agent     (competitive landscape → scenario assumptions)
      2c → power_law_agent    (moat analysis → category leadership scoring)
      2d → value_trap_agent   (cycle position → structural decline check)
      2e → scenario_agent     (disruption vectors → bear case)
      2f → investor agents    (KPI framework → anchor KPI monitoring)
    """
    ids = ["2F", "2E", "2D", "2C", "2B", "2A"]  # kept for reference
    # Widened to tolerate LLM formatting variants: `2A.`, `2A:`, `2A—`, `2A-`,
    # `2A)`, `2A ` — with optional markdown markers (`##`, `###`, `**`, `*`,
    # `>`) prefixing. The `2[A-F]` anchor remains to avoid over-matching
    # arbitrary numbers. Without this, runs where the LLM emitted `**2A:**`
    # or `### 2A —` fell through to `{"full": text}` and downstream consumers
    # (scenario_agent, power_law_agent, investor agents) never saw the
    # per-section breakdown → `deep_research_sections` ended up empty in
    # stored state.
    #
    # Further broadened 2026-04: DDOG (and likely other Qwen runs) emit 2F with
    # prefixes the old char-class `[ \t#─>*]` didn't cover — specifically list
    # markers (`-`, `•`), divider bars (`═`, `─`, `=`), or `Section 2F:` prose
    # form. The old regex silently dropped these, producing a dict with only
    # 2A-2E and missing 2F → frontend commentary card hid entirely even though
    # Qwen had written the 2F content. Fix: (a) permit any non-word, non-newline
    # prefix chars (catches list markers, dividers, bullets, ascii-art); (b)
    # accept "Section" / "Part" prose prefix before the 2X token.
    boundary = re.compile(
        r"(?:^|\n)[^\w\n]*\*{0,2}(?:section\s+|part\s+)?\b(2[A-F])\b[\.\:—\-\)\*\s]",
        re.IGNORECASE | re.MULTILINE,
    )
    positions: list[tuple[str, int]] = []
    for m in boundary.finditer(report_text):
        key = m.group(1).lower()
        if key not in [k for k, _ in positions]:
            positions.append((key, m.start()))

    positions.sort(key=lambda x: x[1])
    if not positions:
        return {"full": report_text}

    sections: dict[str, str] = {}
    for i, (key, start) in enumerate(positions):
        end = positions[i + 1][1] if i + 1 < len(positions) else len(report_text)
        sections[key] = report_text[start:end].strip()

    return sections


# ── FMP number formatter ──────────────────────────────────────────────────────
# Module-level so it can be imported and tested independently.

def _fmt_fmp(val) -> str:
    """Format a raw FMP number into a readable abbreviated string.

    Handles integers, floats, pre-formatted strings, and None/empty.
    Examples:
        391035000000  → '$391.0B'
        -78959000000  → '-$79.0B'
        500000000     → '$500M'
        1500000       → '$2M'
        '$391.0B'     → '$391.0B'   (already formatted — pass through)
        None          → '?'
    """
    if val is None or val == "?" or str(val).strip() == "":
        return "?"
    try:
        n = float(str(val).replace(",", "").replace("$", ""))
    except (ValueError, TypeError):
        return str(val)   # already formatted string — return as-is
    sign = "-" if n < 0 else ""
    n = abs(n)
    if n >= 1e9:
        return f"{sign}${n / 1e9:.1f}B"
    if n >= 1e6:
        return f"{sign}${n / 1e6:.0f}M"
    if n >= 1e3:
        return f"{sign}${n / 1e3:.0f}K"
    return f"{sign}${n:.0f}"


# ── Management guidance extractor ───────────────────────────────────────────

def _extract_management_guidance(report_text: str) -> dict:
    """Parse management guidance from deep research report text.

    Scans for patterns like:
      - "EBITDA guidance: $6.8B–$7.6B" or "EBITDA guidance of $6.8 billion to $7.6 billion"
      - "revenue guidance: $18B–$20B"
      - "capex guidance: $3B"
      - "EBITDA guidance (midpoint $7.2B)"

    Returns dict with keys: ebitda_guidance_low, ebitda_guidance_high,
    ebitda_guidance_mid, revenue_guidance_mid, capex_guidance.
    All values in raw dollars (not billions). Returns empty dict if nothing found.
    """
    import re

    result: dict = {}
    text = report_text.lower()

    def _parse_dollar(s: str) -> float | None:
        """Parse '$6.8B' or '$6.8 billion' or '$6,800M' to raw dollars."""
        s = s.strip().replace(",", "")
        m = re.match(r'\$?([\d.]+)\s*(b|billion|bn|t|trillion|m|million|mn|k|thousand)?', s, re.IGNORECASE)
        if not m:
            return None
        val = float(m.group(1))
        unit = (m.group(2) or "").lower()
        if unit in ("b", "billion", "bn"):
            return val * 1e9
        if unit in ("t", "trillion"):
            return val * 1e12
        if unit in ("m", "million", "mn"):
            return val * 1e6
        if unit in ("k", "thousand"):
            return val * 1e3
        # If val > 100, assume millions (e.g. "$6800" = $6.8B is unlikely)
        if val > 1000:
            return val * 1e6
        return val * 1e9  # bare number like "6.8" probably means billions

    # EBITDA guidance range
    ebitda_patterns = [
        r'ebitda\s+guidance[:\s]+\$?([\d.,]+\s*(?:b|billion|bn|m|million)?)\s*(?:[-–to]+)\s*\$?([\d.,]+\s*(?:b|billion|bn|m|million)?)',
        r'(?:adj(?:usted)?\.?\s+)?ebitda\s+(?:of|to|between|range)?\s*\$?([\d.,]+\s*(?:b|billion|bn|m|million)?)\s*(?:[-–to]+)\s*\$?([\d.,]+\s*(?:b|billion|bn|m|million)?)',
        r'ebitda\s+guidance.*?(?:midpoint|mid)\s*(?:of)?\s*\$?([\d.,]+\s*(?:b|billion|bn|m|million)?)',
    ]
    for pat in ebitda_patterns:
        m = re.search(pat, text)
        if m:
            groups = m.groups()
            if len(groups) >= 2:
                low = _parse_dollar(groups[0])
                high = _parse_dollar(groups[1])
                if low and high:
                    result["ebitda_guidance_low"] = low
                    result["ebitda_guidance_high"] = high
                    result["ebitda_guidance_mid"] = (low + high) / 2
                    break
            elif len(groups) == 1:
                mid = _parse_dollar(groups[0])
                if mid:
                    result["ebitda_guidance_mid"] = mid
                    break

    # Revenue guidance range
    rev_patterns = [
        r'revenue\s+guidance[:\s]+\$?([\d.,]+\s*(?:b|billion|bn|m|million)?)\s*(?:[-–to]+)\s*\$?([\d.,]+\s*(?:b|billion|bn|m|million)?)',
        r'revenue\s+(?:outlook|forecast|target)[:\s]+\$?([\d.,]+\s*(?:b|billion|bn|m|million)?)',
    ]
    for pat in rev_patterns:
        m = re.search(pat, text)
        if m:
            groups = m.groups()
            if len(groups) >= 2:
                low = _parse_dollar(groups[0])
                high = _parse_dollar(groups[1])
                if low and high:
                    result["revenue_guidance_mid"] = (low + high) / 2
                    break
            elif len(groups) == 1:
                val = _parse_dollar(groups[0])
                if val:
                    result["revenue_guidance_mid"] = val
                    break

    # Capex guidance
    capex_pat = r'cap(?:ital\s+)?ex(?:penditure)?\s+(?:guidance|budget|forecast|plan)[:\s]+\$?([\d.,]+\s*(?:b|billion|bn|m|million)?)'
    m = re.search(capex_pat, text)
    if m:
        val = _parse_dollar(m.group(1))
        if val:
            result["capex_guidance"] = val

    # ── Cross-validation: EBITDA cannot exceed revenue ─────────────────────
    # The EBITDA regex can mis-capture a revenue figure (e.g. "$100B revenue
    # target" picked up because "EBITDA" appeared earlier in the same paragraph).
    # If both are present and EBITDA > revenue, drop the EBITDA extraction.
    # If only EBITDA is present and it looks suspiciously large (> $500B or
    # implausible margin > 60% when revenue is available), also drop it.
    _ebitda_mid = result.get("ebitda_guidance_mid")
    _rev_mid = result.get("revenue_guidance_mid")
    if _ebitda_mid and _rev_mid and _ebitda_mid > _rev_mid * 0.60:
        # EBITDA > 60% of revenue is implausible for any sector
        result.pop("ebitda_guidance_mid", None)
        result.pop("ebitda_guidance_low", None)
        result.pop("ebitda_guidance_high", None)
    elif _ebitda_mid and not _rev_mid and _ebitda_mid > 500e9:
        # Standalone EBITDA > $500B is almost certainly a mis-parse
        result.pop("ebitda_guidance_mid", None)
        result.pop("ebitda_guidance_low", None)
        result.pop("ebitda_guidance_high", None)

    return result


# ── DCF calibration signal extractor ─────────────────────────────────────────

def _extract_dcf_calibration(
    sdk_client,
    model_name: str,
    sections: dict[str, str],
    ticker: str,
) -> dict:
    """
    Lightweight LLM pass over deep research sections 2D (industry cycle) and 2F
    (KPI framework) to extract structured signals for the DCF engine.

    Returns a dict with:
        growth_rate_adj   — float delta applied to growth_base (e.g. -0.02 = reduce 2pp)
                            None if the research provides no directional signal
        margin_direction  — "expanding" | "stable" | "compressing"
        risk_flag         — "HIGH" | "MEDIUM" | "LOW"  (WACC loading)
        notes             — one-sentence justification
    """
    section_2d = sections.get("2d") or sections.get("2D") or ""
    section_2f = sections.get("2f") or sections.get("2F") or ""
    combined   = (section_2d + "\n\n" + section_2f).strip()

    if not combined:
        return {"growth_rate_adj": None, "margin_direction": "stable",
                "risk_flag": "MEDIUM", "notes": "No deep research sections available."}

    try:
        resp = _call_llm_with_rate_retry(
            sdk_client,
            extractor_name="dcf_calibration",
            ticker=ticker,
            model=model_name,
            max_tokens=400,
            system=(
                "You are a financial analyst assistant. Read the provided research excerpts "
                "and extract structured signals for a DCF model. Respond ONLY with valid JSON, "
                "no commentary. JSON keys: growth_rate_adj (number or null), margin_direction "
                "(\"expanding\"|\"stable\"|\"compressing\"), risk_flag (\"HIGH\"|\"MEDIUM\"|\"LOW\"), "
                "notes (string ≤80 chars).\n\n"
                "growth_rate_adj rules:\n"
                "  - If research indicates SECULAR growth inflection (AI supercycle, platform shift, "
                "    regulatory mandate like grid upgrade, new blockbuster drug category): use +0.05 to +0.10\n"
                "  - If research indicates above-consensus growth (strong tailwind, market share gain, "
                "    re-acceleration, large backlog build): use +0.02 to +0.05\n"
                "  - If research indicates modest tailwind or stable growth: use +0.01 to +0.02\n"
                "  - If research indicates below-consensus growth (structural headwind, late-cycle, "
                "    competitive pressure, demand destruction): use -0.01 to -0.05\n"
                "  - If neutral or insufficient evidence: use null\n"
                "  IMPORTANT: for companies with explicit forward revenue guidance substantially above "
                "  historical CAGR (e.g. backlog doubling, TAM expansion), use the higher end of the range.\n\n"
                "risk_flag rules: HIGH = binary event risk / regulatory overhang / leverage concern; "
                "MEDIUM = normal business risk; LOW = visible cash flow, long contracts, high moat."
            ),
            messages=[{
                "role": "user",
                "content": (
                    f"Ticker: {ticker}\n\n"
                    f"Section 2D — Industry Cycle Position:\n{section_2d[:1200]}\n\n"
                    f"Section 2F — KPI Framework:\n{section_2f[:1200]}"
                ),
            }],
        )
        raw = "".join(b.text for b in resp.content if hasattr(b, "text"))
        parsed = _parse_llm_json(raw, extractor_name="dcf_calibration")
        if parsed is None or not isinstance(parsed, dict):
            return {"growth_rate_adj": None, "margin_direction": "stable",
                    "risk_flag": "MEDIUM", "notes": "Extraction failed: unparseable JSON"}
        return {
            "growth_rate_adj":  parsed.get("growth_rate_adj"),
            "margin_direction": parsed.get("margin_direction", "stable"),
            "risk_flag":        parsed.get("risk_flag", "MEDIUM"),
            "notes":            parsed.get("notes", ""),
        }
    except Exception as exc:
        return {"growth_rate_adj": None, "margin_direction": "stable",
                "risk_flag": "MEDIUM", "notes": f"Extraction failed: {exc}"}


# ── Segment-scenario extractor (probabilistic SOTP 12m) ──────────────────────

def _extract_segment_scenarios(
    sdk_client,
    model_name: str,
    sections: dict[str, str],
    deep_research: str,
    ticker: str,
) -> dict:
    """
    LLM pass over the deep research report to produce per-segment 12-month
    growth SCENARIO TREES (probabilities + rates + evidence + confidence).

    The output is consumed by the probabilistic SOTP 12m method: each segment
    draws a scenario weighted by its probability during Monte Carlo, so the
    distribution of IVs captures right-tail hypergrowth (NVDA data center,
    biotech launches) and left-tail contraction (AI capex pullback).

    NO CLAMP on rates — the LLM is trusted to reason over the research
    evidence and produce defensible scenarios, including >100% or <-30%
    when the evidence supports it. The evidence field surfaces that logic
    in the audit trail.

    Returns a dict mapping segment-name → scenario block:
        {
          "<segment>": {
             "scenarios": [{"prob": float, "rate": float, "label": str}, ...],
             "evidence":  str,
             "confidence": "low"|"medium"|"high",
          },
          ...
        }

    Invalid outputs (prob sum out-of-tolerance, missing fields, model error)
    collapse to {} so the downstream SOTP falls back to deterministic mode.
    """
    if not deep_research and not sections:
        return {}

    # Prefer structured sections; fall back to full report text
    section_2d = sections.get("2d") or sections.get("2D") or ""
    section_2f = sections.get("2f") or sections.get("2F") or ""
    section_2e = sections.get("2e") or sections.get("2E") or ""
    combined = (section_2d + "\n\n" + section_2e + "\n\n" + section_2f).strip()
    if not combined:
        # Use first 6000 chars of the full report as context
        combined = (deep_research or "")[:6000]
    if not combined:
        return {}

    try:
        resp = _call_llm_with_rate_retry(
            sdk_client,
            extractor_name="segment_scenarios",
            ticker=ticker,
            model=model_name,
            max_tokens=1500,
            system=(
                "You are a sector research analyst producing probabilistic 12-month "
                "growth scenarios for each major revenue segment. Read the provided "
                "research excerpts and output ONLY valid JSON — no commentary, no "
                "markdown fences.\n\n"
                "Schema: an object mapping segment-name to a scenario block. Each "
                "segment block has:\n"
                "  - scenarios: array of 2-6 items, each with {prob, rate, label}\n"
                "      prob  — float 0..1, all probs in one segment MUST sum to 1.0 (±0.02)\n"
                "      rate  — 12-month revenue growth rate as decimal (0.15 = 15%);\n"
                "              NO CAP — use high values (>1.0) when evidence supports\n"
                "              hypergrowth, negative values when research indicates\n"
                "              contraction. Defend the rate in evidence.\n"
                "      label — short narrative name for the scenario (≤40 chars)\n"
                "  - evidence: one-sentence justification citing the research signal\n"
                "                (≤200 chars)\n"
                "  - confidence: 'low' | 'medium' | 'high'\n\n"
                "Rules:\n"
                "  * Segment names must match the exact terminology used in the research "
                "    (e.g. 'Services', 'iPhone', 'AWS', 'Intelligent Cloud', 'Data center').\n"
                "  * Produce scenarios only for major segments mentioned in the research.\n"
                "    If the research doesn't substantiate a forecast for a segment, OMIT it.\n"
                "  * Scenarios should span realistic outcomes — include at least one downside, "
                "    one base, one upside when evidence permits.\n"
                "  * For high-uncertainty segments (binary drug approvals, new-product launches), "
                "    include an explicit tail scenario with appropriate probability.\n"
                "  * confidence='high' only when research explicitly supports the distribution\n"
                "    (management guidance, clear analyst consensus); 'medium' is the default\n"
                "    when directionally supported; 'low' when inferred.\n\n"
                "If the research is too thin for ANY segment, return {} (empty object)."
            ),
            messages=[{
                "role": "user",
                "content": (
                    f"Ticker: {ticker}\n\n"
                    f"Research excerpts (cycle + risk + KPI sections):\n{combined[:20000]}"
                ),
            }],
        )
        raw = "".join(b.text for b in resp.content if hasattr(b, "text"))
        parsed = _parse_llm_json(raw, extractor_name="segment_scenarios")
        if parsed is None or not isinstance(parsed, dict):
            return {}

        # Validate each segment block — invalid blocks dropped silently
        out: dict[str, dict] = {}
        for seg_name, block in parsed.items():
            if not isinstance(block, dict):
                continue
            scenarios = block.get("scenarios")
            if not isinstance(scenarios, list) or len(scenarios) < 1:
                continue
            cleaned: list[dict] = []
            prob_sum = 0.0
            for s in scenarios:
                if not isinstance(s, dict):
                    continue
                try:
                    p = float(s.get("prob"))
                    r = float(s.get("rate"))
                except (TypeError, ValueError):
                    continue
                if not (0.0 <= p <= 1.0):
                    continue
                cleaned.append({
                    "prob":  p,
                    "rate":  r,
                    "label": str(s.get("label", ""))[:60],
                })
                prob_sum += p
            # Probabilities must sum to ~1.0 (±0.02 tolerance)
            if not cleaned or abs(prob_sum - 1.0) > 0.02:
                continue
            out[str(seg_name)] = {
                "scenarios":  cleaned,
                "evidence":   str(block.get("evidence", ""))[:300],
                "confidence": str(block.get("confidence", "medium")).lower(),
            }
        return out
    except Exception:
        return {}


# ── SaaS metrics extractor (Rule of 40 + NRR + CAC + Magic Number) ───────────

def _extract_saas_metrics(
    sdk_client,
    model_name: str,
    sections: dict[str, str],
    deep_research: str,
    ticker: str,
) -> dict:
    """
    LLM pass to extract SaaS-specific KPIs that drive Tech valuation overlays:
    NRR, gross retention, CAC payback, Rule of 40, magic number, RPO growth.

    Consumed by:
      * Rule of 40 method in dcf_agent._compute_method_value (tier multiplier)
      * NRR confidence weight on EV/Revenue weight in Growth SaaS blend
      * Growth premium modifier for NRR > 120% (best-in-class retention)

    Returns {} when ticker isn't a SaaS / tech company or research too thin.
    Numeric fields validated with clamps.
    """
    if not deep_research and not sections:
        return {}

    section_2a = sections.get("2a") or sections.get("2A") or ""
    section_2d = sections.get("2d") or sections.get("2D") or ""
    section_2f = sections.get("2f") or sections.get("2F") or ""
    # Focus on 2F — the sector-specific KPI framework is where NRR / CAC /
    # Rule of 40 / Magic Number / Capital ratios / Pipeline assets / REIT
    # metrics / Bank metrics all live. 2A (profit pool) and 2D (cycle) are
    # context but don't contain the structured KPIs the extractor needs;
    # including them diluted the LLM's attention and hit the truncation
    # ceiling on rich reports (DDOG 2A+2D = 7800 chars → 2F starved to 200
    # chars → only RPO extracted from 2A preload data, missing NRR/CAC/
    # Magic/R40 that sit in 2F). Use 2F only. Falls back to full deep
    # research text when 2F is missing or too short.
    combined = section_2f.strip()
    if len(combined) < 500:
        combined = (section_2a + "\n\n" + section_2d + "\n\n" + section_2f).strip()
    if not combined or len(combined) < 500:
        combined = (deep_research or "")[:20000]
    if not combined:
        return {}

    try:
        resp = _call_llm_with_rate_retry(
            sdk_client,
            extractor_name="saas_metrics",
            ticker=ticker,
            model=model_name,
            max_tokens=500,
            system=(
                "You are a SaaS / tech-company analyst. Extract structured KPIs "
                "from the research and return ONLY valid JSON (no markdown fences, "
                "no commentary).\n\n"
                "Schema (all fields OPTIONAL — omit if not substantiated by research):\n"
                "  nrr_pct:              float (0.80-1.50, net revenue retention decimal)\n"
                "  gross_retention_pct:  float (0.80-1.00, gross retention decimal)\n"
                "  cac_payback_months:   float (3-60, CAC payback in months)\n"
                "  ltv_cac_ratio:        float (1-15, LTV:CAC ratio)\n"
                "  rule_of_40_score:     float (-30 to 120, growth% + FCF margin%)\n"
                "  magic_number:         float (0.1-3.0, new ARR / prior-qtr S&M)\n"
                "  rpo_growth_yoy:       float (-0.20 to 0.80, remaining perf obligation growth)\n"
                "  billings_growth_yoy:  float (-0.20 to 0.80)\n"
                "  evidence:             string ≤300 chars citing research source\n\n"
                "Rules:\n"
                "  * Return {} if the company isn't a SaaS / subscription business.\n"
                "  * Convert percentages to decimals (120% NRR → 1.20; 40 Rule of 40 score → 40).\n"
                "  * NRR: look for phrases like 'NRR', 'net retention', 'net dollar retention',\n"
                "    '$NRR', 'net expansion' — cited directly from earnings call.\n"
                "  * Rule of 40: sum of revenue growth % + FCF margin %. E.g. 35% growth + 25%\n"
                "    FCF margin = 60.\n"
                "Extraction / derivation hints when numbers aren't directly stated:\n"
                "  * NRR: look for phrases like 'NRR', 'net retention', 'net dollar retention',\n"
                "    '$NRR', 'net expansion', 'dollar-based net retention'. Usually cited\n"
                "    on earnings calls as a single number (e.g. '126%').\n"
                "  * Gross Retention: sometimes disclosed as 'gross retention' or 'gross\n"
                "    dollar retention'. If NRR is stated but gross retention isn't, check\n"
                "    for explicit expansion rate disclosure: GR = NRR - expansion_pct.\n"
                "    Only derive if both inputs cited in the research text.\n"
                "  * CAC Payback: if disclosed, report directly. If not, derive from\n"
                "    S&M spend + net new ARR + gross margin using formula:\n"
                "    (S&M_annual × 12) / (net_new_ARR_annual × gross_margin). Require all\n"
                "    three inputs to be cited.\n"
                "  * LTV/CAC: compute ONLY when the 4 inputs are all explicitly in the\n"
                "    research: ACV, gross_margin, annual_churn (or gross_retention), CAC.\n"
                "    Formula: (ACV × gross_margin / annual_churn) / CAC. Never derive\n"
                "    without all 4 inputs present.\n"
                "  * Magic Number: if disclosed (phrases: 'sales efficiency', 'magic number',\n"
                "    'new ARR per $ S&M'), report directly. Otherwise derive from:\n"
                "    net_new_ARR_quarter / S&M_prior_quarter.\n"
                "  * When deriving, cite BOTH the formula AND the input values in the\n"
                "    'evidence' field so the audit trail is traceable.\n"
                "  * Prefer a derived value WITH clear input citations over null when the\n"
                "    inputs are clearly present.\n"
            ),
            messages=[{
                "role": "user",
                "content": (
                    f"Ticker: {ticker}\n\n"
                    f"Research excerpts:\n{combined[:20000]}"
                ),
            }],
        )
        raw = "".join(b.text for b in resp.content if hasattr(b, "text"))
        parsed = _parse_llm_json(raw, extractor_name="saas_metrics")

        # Diagnostic: shows raw response length + parsed state so ops can
        # distinguish (a) Qwen returned unparseable text (b) Qwen returned
        # valid JSON with no KPIs (c) KPIs came back but failed clamp
        # validation. Helps triage "saas_metrics empty" reports without
        # re-running the full pipeline.
        _raw_preview = (raw or "")[:200].replace("\n", " ⏎ ")
        _parsed_keys = sorted(parsed.keys()) if isinstance(parsed, dict) else []
        print(
            f"  [saas_metrics {ticker}] input={len(combined)} chars · "
            f"raw_response={len(raw or '')} chars · "
            f"parsed_type={type(parsed).__name__} · "
            f"parsed_keys={_parsed_keys} · "
            f"preview={_raw_preview!r}"
        )

        if parsed is None or not isinstance(parsed, dict):
            return {}

        out: dict = {}
        _clamps = {
            "nrr_pct":              (0.80, 1.50),
            "gross_retention_pct":  (0.80, 1.00),
            "cac_payback_months":   (3, 60),
            "ltv_cac_ratio":        (1, 15),
            "rule_of_40_score":     (-30, 120),
            "magic_number":         (0.1, 3.0),
            "rpo_growth_yoy":       (-0.20, 0.80),
            "billings_growth_yoy":  (-0.20, 0.80),
        }
        # Track clamp rejections so ops can see when the LLM returned the
        # right key but out-of-range value (e.g. "120" instead of 1.20 for NRR).
        _dropped = []
        for k, (lo, hi) in _clamps.items():
            v = parsed.get(k)
            if v is None:
                continue
            if isinstance(v, (int, float)) and lo <= v <= hi:
                out[k] = float(v)
            else:
                _dropped.append(f"{k}={v!r}(range {lo}-{hi})")
        if _dropped:
            print(f"  [saas_metrics {ticker}] dropped (out-of-range or wrong type): {_dropped}")
        if "evidence" in parsed:
            out["evidence"] = str(parsed["evidence"])[:300]
        return out
    except Exception as _exc:
        # Surface the real error — previously silent "return {}" hid Qwen
        # rate limits, auth errors, and API timeouts.
        print(f"  [saas_metrics {ticker}] ⚠ extractor FAILED: {type(_exc).__name__}: {_exc}")
        return {}


def _compute_saas_metrics_fallback(
    raw_financials: dict | None,
    existing_saas_metrics: dict | None,
) -> dict:
    """
    Fill null saas_metrics fields with FMP-computed values where possible.

    LLM extraction (from research text) runs first via _extract_saas_metrics;
    this function runs AFTER and only populates fields that came back empty.
    LLM-extracted values always take precedence — we never overwrite them.

    Self-computable metrics (require only FMP line items):
      - rule_of_40_score     → revenue_growth_% + FCF_margin_%
      - magic_number         → (revenue YoY $) / selling_and_marketing_expense
      - cac_payback_months   → S&M × 12 / (revenue_growth × gross_margin)
      - billings_growth_yoy  → Δ (revenue + deferred_revenue) YoY

    Research-only metrics (not computable without cohort / customer data):
      - nrr_pct
      - gross_retention_pct
      - ltv_cac_ratio
      - rpo_growth_yoy  (unless FMP exposes RPO; currently skipped)

    Returns a new dict — does not mutate `existing_saas_metrics`.
    """
    out: dict = dict(existing_saas_metrics or {})

    if not raw_financials or not isinstance(raw_financials, dict):
        return out

    # Latest and prior-year FY rows
    fy_keys = sorted(
        k for k in raw_financials
        if isinstance(raw_financials.get(k), dict)
    )
    if len(fy_keys) < 2:
        return out

    latest = raw_financials[fy_keys[-1]]
    prev = raw_financials[fy_keys[-2]]

    def _num(d: dict, key: str) -> float | None:
        v = d.get(key)
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    revenue = _num(latest, "revenue")
    prev_revenue = _num(prev, "revenue")
    fcf = _num(latest, "free_cash_flow")
    sm_spend = _num(latest, "selling_and_marketing_expense")
    gross_profit = _num(latest, "gross_profit")
    deferred_rev = _num(latest, "deferred_revenue")
    prev_deferred_rev = _num(prev, "deferred_revenue")

    # ── Rule of 40 ────────────────────────────────────────────────────────────
    # Always computable when revenue + FCF available across 2 FYs
    if out.get("rule_of_40_score") is None and all(
        v is not None and v > 0 for v in [revenue, prev_revenue]
    ) and fcf is not None:
        rev_growth_pct = (revenue / prev_revenue - 1) * 100
        fcf_margin_pct = (fcf / revenue) * 100
        score = rev_growth_pct + fcf_margin_pct
        # Apply same clamp range the extractor uses
        if -30 <= score <= 120:
            out["rule_of_40_score"] = round(score, 1)

    # ── Magic Number ──────────────────────────────────────────────────────────
    # (Revenue YoY $ change) / S&M spend. Approximates net_new_ARR / S&M.
    if out.get("magic_number") is None and all(
        v is not None for v in [revenue, prev_revenue, sm_spend]
    ) and sm_spend and abs(sm_spend) > 0:
        net_new_ar = revenue - prev_revenue
        mn = net_new_ar / abs(sm_spend)
        if 0.1 <= mn <= 3.0:
            out["magic_number"] = round(mn, 2)

    # ── CAC Payback ───────────────────────────────────────────────────────────
    # S&M × 12 / (revenue_growth × gross_margin). Months.
    if out.get("cac_payback_months") is None and all(
        v is not None for v in [revenue, prev_revenue, sm_spend, gross_profit]
    ):
        net_new_ar = revenue - prev_revenue
        gross_margin_ratio = gross_profit / revenue if revenue > 0 else 0
        if net_new_ar > 0 and gross_margin_ratio > 0 and sm_spend:
            cac_payback = (abs(sm_spend) * 12) / (net_new_ar * gross_margin_ratio)
            if 3 <= cac_payback <= 60:
                out["cac_payback_months"] = round(cac_payback, 1)

    # ── Billings Growth YoY ──────────────────────────────────────────────────
    # Δ (revenue + deferred_revenue) YoY. Leading indicator for ARR growth.
    if out.get("billings_growth_yoy") is None and all(
        v is not None for v in [revenue, prev_revenue, deferred_rev, prev_deferred_rev]
    ) and prev_revenue > 0:
        billings_latest = revenue + deferred_rev
        billings_prev = prev_revenue + prev_deferred_rev
        if billings_prev > 0:
            growth = (billings_latest / billings_prev) - 1
            if -0.20 <= growth <= 0.80:
                out["billings_growth_yoy"] = round(growth, 3)

    # Evidence field — note computed fallback for audit trail
    if out.get("evidence"):
        existing_ev = out["evidence"]
        if "computed" not in existing_ev.lower():
            out["evidence"] = f"{existing_ev}  [+FMP fallback: filled missing fields from raw_financials]"
    elif len(out) > 0 and "rule_of_40_score" in out:
        out["evidence"] = "FMP fallback: Rule of 40 / Magic Number / CAC Payback / Billings computed from revenue, FCF, S&M, deferred_revenue across FYs"

    return out


# ── Bank metrics extractor (RI target ROE + CET1 override) ───────────────────

def _extract_bank_metrics(
    sdk_client,
    model_name: str,
    sections: dict[str, str],
    deep_research: str,
    ticker: str,
) -> dict:
    """
    LLM pass to extract bank-specific metrics that override DCF engine defaults:
    CET1 ratio, NIM, efficiency ratio, NPL, management target ROE, LDR.

    Consumed by:
      * _compute_excess_capital via most_recent["_bank_cet1_research"]
      * _compute_residual_income_2stage via most_recent["_bank_target_roe_research"]
      * AFFO-gated sustainability signal in audit

    Returns {} when ticker isn't a bank or research too thin. Invalid numeric
    fields are dropped silently (clamped to safe ranges).
    """
    if not deep_research and not sections:
        return {}

    section_2a = sections.get("2a") or sections.get("2A") or ""
    section_2d = sections.get("2d") or sections.get("2D") or ""
    section_2f = sections.get("2f") or sections.get("2F") or ""
    # Focus on 2F — the sector-specific KPI framework is where NRR / CAC /
    # Rule of 40 / Magic Number / Capital ratios / Pipeline assets / REIT
    # metrics / Bank metrics all live. 2A (profit pool) and 2D (cycle) are
    # context but don't contain the structured KPIs the extractor needs;
    # including them diluted the LLM's attention and hit the truncation
    # ceiling on rich reports (DDOG 2A+2D = 7800 chars → 2F starved to 200
    # chars → only RPO extracted from 2A preload data, missing NRR/CAC/
    # Magic/R40 that sit in 2F). Use 2F only. Falls back to full deep
    # research text when 2F is missing or too short.
    combined = section_2f.strip()
    if len(combined) < 500:
        combined = (section_2a + "\n\n" + section_2d + "\n\n" + section_2f).strip()
    if not combined or len(combined) < 500:
        combined = (deep_research or "")[:20000]
    if not combined:
        return {}

    try:
        resp = _call_llm_with_rate_retry(
            sdk_client,
            extractor_name="bank_metrics",
            ticker=ticker,
            model=model_name,
            max_tokens=600,
            system=(
                "You are a bank / financial institution analyst. Extract structured "
                "metrics from the research and return ONLY valid JSON (no markdown "
                "fences, no commentary).\n\n"
                "Schema (all fields OPTIONAL — omit if not substantiated by research):\n"
                "  cet1_ratio:              float (0.05-0.25, latest CET1 as decimal)\n"
                "  nim_pct:                 float (0.005-0.08, last-quarter NIM decimal)\n"
                "  efficiency_ratio:        float (0.30-0.80, op_exp / total income)\n"
                "  npl_ratio:               float (0.0-0.10, non-performing loan %)\n"
                "  npl_coverage_ratio:      float (0.30-3.00, loan-loss reserves / NPLs;\n"
                "                                  e.g. OCBC reports 150% → 1.50)\n"
                "  net_charge_offs_pct:     float (0.0-0.05, annualized NCO / avg loans)\n"
                "  management_target_roe:   float (0.05-0.25, through-cycle ROE/ROTCE\n"
                "                                  target cited in earnings calls)\n"
                "  loan_to_deposit_ratio:   float (0.40-1.20)\n"
                "  dividend_payout_ratio:   float (0.10-0.90)\n"
                "  loan_growth_yoy:         float (-0.10 to 0.30, most recent FY)\n"
                "  deposit_growth_yoy:      float (-0.10 to 0.30)\n"
                "  management_overlays_bn:  float (0-50, management overlay / general\n"
                "                                  provisions in BILLIONS of reporting currency;\n"
                "                                  e.g. OCBC 'S$700m in mgmt overlays' → 0.70)\n"
                "  nim_rate_sensitivity_bps: float (0-30, bps NIM change per 1 bp rate\n"
                "                                   change; e.g. DBS on OCBC '11 bps' → 11.0)\n"
                "  forward_loan_growth_guidance: string ≤200 chars (mgmt forward guidance\n"
                "                                   quote; e.g. 'mid-single digit for FY26F')\n"
                "  forward_nim_guidance:    string ≤200 chars (mgmt forward NIM commentary;\n"
                "                                   e.g. 'NIM pressure to continue into FY26F')\n"
                "  evidence:                string ≤300 chars citing the source\n\n"
                "Rules:\n"
                "  * Return {} if the research doesn't discuss a bank / lender.\n"
                "  * Only include fields EXPLICITLY substantiated by the research.\n"
                "  * cet1_ratio: reported in bank regulatory filings & earnings calls.\n"
                "    Convert to decimal (15.3% → 0.153).\n"
                "  * management_target_roe: look for phrases like 'targets X% ROE/ROTCE',\n"
                "    'through-the-cycle ROE target', 'aspires to Y% ROTCE'. Convert to\n"
                "    decimal (17% → 0.17).\n"
                "  * efficiency_ratio: lower is better; reported as decimal (55% → 0.55).\n"
                "  * npl_coverage_ratio: reported as multiple/percentage of provisions to NPL.\n"
                "    150% → 1.50. Above 1.0 = over-provisioned (conservative); below 1.0 =\n"
                "    under-provisioned (aggressive).\n"
                "  * management_overlays_bn: specific disclosed management overlay / general\n"
                "    provision buffer. Report in billions (700m → 0.70; 2.3bn → 2.30).\n"
                "  * nim_rate_sensitivity_bps: reported in analyst models / bank disclosures as\n"
                "    'X bps NIM sensitivity per 1 bp rate change'. Report the X value directly.\n"
                "  * forward_*_guidance: verbatim or near-verbatim management quote describing\n"
                "    forward NIM / loan growth expectations. Keep ≤200 chars.\n"
            ),
            messages=[{
                "role": "user",
                "content": (
                    f"Ticker: {ticker}\n\n"
                    f"Research excerpts:\n{combined[:20000]}"
                ),
            }],
        )
        raw = "".join(b.text for b in resp.content if hasattr(b, "text"))
        parsed = _parse_llm_json(raw, extractor_name="bank_metrics")
        if parsed is None or not isinstance(parsed, dict):
            return {}

        out: dict = {}
        _clamps = {
            "cet1_ratio":             (0.05, 0.25),
            "nim_pct":                (0.005, 0.08),
            "efficiency_ratio":       (0.30, 0.80),
            "npl_ratio":              (0.0, 0.15),
            "npl_coverage_ratio":     (0.30, 3.00),
            "net_charge_offs_pct":    (0.0, 0.05),
            "management_target_roe":  (0.05, 0.25),
            "loan_to_deposit_ratio":  (0.40, 1.20),
            "dividend_payout_ratio":  (0.0, 1.0),
            "loan_growth_yoy":        (-0.30, 0.40),
            "deposit_growth_yoy":     (-0.30, 0.40),
            "management_overlays_bn": (0.0, 50.0),
            "nim_rate_sensitivity_bps": (0.0, 30.0),
        }
        for k, (lo, hi) in _clamps.items():
            v = parsed.get(k)
            if isinstance(v, (int, float)) and lo <= v <= hi:
                out[k] = float(v)

        # String fields — truncate to bound
        for k, max_len in (
            ("evidence", 300),
            ("forward_loan_growth_guidance", 200),
            ("forward_nim_guidance", 200),
        ):
            if k in parsed and isinstance(parsed[k], str) and parsed[k].strip():
                out[k] = str(parsed[k])[:max_len]

        return out
    except Exception:
        return {}


# ── REIT metrics extractor (NAV cap-rate override) ───────────────────────────

def _extract_reit_metrics(
    sdk_client,
    model_name: str,
    sections: dict[str, str],
    deep_research: str,
    ticker: str,
) -> dict:
    """
    LLM pass over the deep research report to extract REIT-specific metrics
    that override DCF engine defaults: portfolio cap rate, occupancy, WALE,
    sub-type mix, geographic mix, DPU-to-AFFO coverage.

    Consumed by the NAV (Cap Rates) method in dcf_agent.py via
    most_recent["cap_rate_market"] (overrides the sub-type default cap rate)
    and by the AFFO-gated DDM via most_recent["sustainable_dpu"].

    Returns a dict. Empty dict when the report doesn't substantiate any REIT
    metrics (non-REIT ticker or thin research).

    Schema:
        {
          "cap_rate_market": float,     # portfolio weighted-average cap rate
                                         # (e.g. 0.075 = 7.5%)
          "occupancy_rate": float,      # 0-1 (e.g. 0.95 = 95%)
          "wale_years": float,          # weighted-avg lease expiry
          "subtype_mix": {
            "office": 0.6, "industrial": 0.2, ...
          },
          "geographic_mix": {
            "Singapore": 0.4, "India": 0.3, "Australia": 0.3
          },
          "dpu_cents": float,           # distribution per unit (local cents)
          "affo_per_unit_cents": float, # AFFO per unit (local cents)
          "leverage_ratio": float,      # debt / NAV (e.g. 0.37 = 37%)
          "evidence": str,              # ≤300 chars — source / justification
        }

    Fields missing from the research are omitted (not present in the dict),
    so the caller checks with .get() and falls through to engine defaults.
    """
    if not deep_research and not sections:
        return {}

    # 2F (KPI framework) is the primary section REITs get analyzed in; 2A
    # (moat/product) carries portfolio composition; 2D (cycle) has rates context
    section_2a = sections.get("2a") or sections.get("2A") or ""
    section_2d = sections.get("2d") or sections.get("2D") or ""
    section_2f = sections.get("2f") or sections.get("2F") or ""
    # Focus on 2F — the sector-specific KPI framework is where NRR / CAC /
    # Rule of 40 / Magic Number / Capital ratios / Pipeline assets / REIT
    # metrics / Bank metrics all live. 2A (profit pool) and 2D (cycle) are
    # context but don't contain the structured KPIs the extractor needs;
    # including them diluted the LLM's attention and hit the truncation
    # ceiling on rich reports (DDOG 2A+2D = 7800 chars → 2F starved to 200
    # chars → only RPO extracted from 2A preload data, missing NRR/CAC/
    # Magic/R40 that sit in 2F). Use 2F only. Falls back to full deep
    # research text when 2F is missing or too short.
    combined = section_2f.strip()
    if len(combined) < 500:
        combined = (section_2a + "\n\n" + section_2d + "\n\n" + section_2f).strip()
    if not combined or len(combined) < 500:
        combined = (deep_research or "")[:20000]
    if not combined:
        return {}

    try:
        resp = _call_llm_with_rate_retry(
            sdk_client,
            extractor_name="reit_metrics",
            ticker=ticker,
            model=model_name,
            max_tokens=800,
            system=(
                "You are a REIT / real estate analyst. Extract structured metrics from the "
                "provided research excerpts and return ONLY valid JSON (no markdown fences, "
                "no commentary).\n\n"
                "Schema (all fields OPTIONAL — omit if not substantiated by the research):\n"
                "  cap_rate_market:    float (0.03-0.12, portfolio weighted-avg cap rate)\n"
                "  occupancy_rate:     float (0.5-1.0, portfolio-weighted occupancy)\n"
                "  wale_years:         float (1-15, weighted-avg lease expiry in years)\n"
                "  subtype_mix:        object mapping ASSET CLASS to fraction of portfolio\n"
                "                      (NOI- or GAV-weighted; sum ≈ 1.0). Extract at the\n"
                "                      FINEST granularity the research discloses — do NOT\n"
                "                      force-collapse disclosed sub-categories into broad\n"
                "                      buckets. Use any snake_case key that reflects the\n"
                "                      research's wording verbatim. Examples:\n"
                "                        office, retail, industrial, logistics, warehouse,\n"
                "                        data_center, interconnection, colocation, lab,\n"
                "                        healthcare, medical_office, senior_housing,\n"
                "                        skilled_nursing, residential, student_housing,\n"
                "                        co_living, hospitality, lodging, self_storage,\n"
                "                        infrastructure, business_park, it_park, flex_office,\n"
                "                        co_working, net_lease, single_tenant, triple_net,\n"
                "                        ground_lease, farmland, timberland, cell_tower,\n"
                "                        mixed_use, other\n"
                "  geographic_mix:     object mapping country/region/city to fraction\n"
                "                      (revenue-weighted OR GAV-weighted). Sub-regions OK\n"
                "                      (e.g. 'us_west', 'bangalore', 'india', 'emea').\n"
                "  dpu_cents:          float (distribution per unit, LOCAL cents/pennies)\n"
                "  affo_per_unit_cents: float (AFFO per unit, same unit as dpu_cents)\n"
                "  leverage_ratio:     float (debt/NAV or aggregate leverage, 0-0.60)\n"
                "  evidence:           string ≤300 chars citing the research source\n\n"
                "Rules:\n"
                "  * Return {} if the research doesn't discuss real estate / property assets.\n"
                "  * Only include fields the research EXPLICITLY substantiates. Don't infer\n"
                "    or guess; missing fields signal the DCF engine to use its defaults.\n"
                "  * cap_rate_market should come from cited valuations (CBRE/JLL/Knight Frank),\n"
                "    acquisition cap rates, or implied cap rate from reported NAV. Report as\n"
                "    decimal (0.075 = 7.5%), NOT percentage.\n"
                "  * For SGX/HK REITs, the annual report typically discloses a portfolio\n"
                "    valuation table with per-property cap rates — report the weighted avg.\n"
                "  * dpu_cents and affo_per_unit_cents must be in the SAME LOCAL UNIT (both\n"
                "    Singapore cents, or both Hong Kong cents, or both US pennies).\n"
                "  * subtype_mix examples:\n"
                "    - Research: 'CapitaLand India Trust: 61% IT parks, 11% industrial &\n"
                "      logistics, 8% data centers, 20% other'\n"
                "      → {\"it_park\": 0.61, \"industrial\": 0.11, \"data_center\": 0.08,\n"
                "         \"other\": 0.20}\n"
                "    - Research: 'Realty Income: 100%% single-tenant net-lease retail'\n"
                "      → {\"net_lease\": 1.0}\n"
                "    - Research: 'DLR: 95%% wholesale data center, 5%% interconnection'\n"
                "      → {\"data_center\": 0.95, \"interconnection\": 0.05}\n"
                "    Do NOT force 'IT parks' → 'office' or 'net-lease' → 'retail'. Preserve\n"
                "    the disclosed category taxonomy.\n"
            ),
            messages=[{
                "role": "user",
                "content": (
                    f"Ticker: {ticker}\n\n"
                    f"Research excerpts (moat + cycle + KPI sections):\n{combined[:20000]}"
                ),
            }],
        )
        raw = "".join(b.text for b in resp.content if hasattr(b, "text"))
        parsed = _parse_llm_json(raw, extractor_name="reit_metrics")
        if parsed is None or not isinstance(parsed, dict):
            return {}

        # Validate + clamp numeric fields into safe ranges (prevent LLM
        # hallucinations from corrupting the DCF). Fields that fail validation
        # are dropped silently.
        out: dict = {}

        _cap = parsed.get("cap_rate_market")
        if isinstance(_cap, (int, float)) and 0.02 < _cap < 0.20:
            out["cap_rate_market"] = float(_cap)

        _occ = parsed.get("occupancy_rate")
        if isinstance(_occ, (int, float)) and 0.3 < _occ <= 1.0:
            out["occupancy_rate"] = float(_occ)

        _wale = parsed.get("wale_years")
        if isinstance(_wale, (int, float)) and 0.5 < _wale < 30:
            out["wale_years"] = float(_wale)

        _lev = parsed.get("leverage_ratio")
        if isinstance(_lev, (int, float)) and 0 <= _lev < 0.80:
            out["leverage_ratio"] = float(_lev)

        _dpu = parsed.get("dpu_cents")
        if isinstance(_dpu, (int, float)) and 0 < _dpu < 500:
            out["dpu_cents"] = float(_dpu)

        _affo = parsed.get("affo_per_unit_cents")
        if isinstance(_affo, (int, float)) and 0 < _affo < 500:
            out["affo_per_unit_cents"] = float(_affo)

        _sub = parsed.get("subtype_mix")
        if isinstance(_sub, dict) and _sub:
            cleaned_sub = {}
            for k, v in _sub.items():
                if isinstance(v, (int, float)) and 0 <= v <= 1.0:
                    cleaned_sub[str(k).lower()] = float(v)
            if cleaned_sub and abs(sum(cleaned_sub.values()) - 1.0) < 0.10:
                out["subtype_mix"] = cleaned_sub

        _geo = parsed.get("geographic_mix")
        if isinstance(_geo, dict) and _geo:
            cleaned_geo = {}
            for k, v in _geo.items():
                if isinstance(v, (int, float)) and 0 <= v <= 1.0:
                    cleaned_geo[str(k)] = float(v)
            if cleaned_geo and abs(sum(cleaned_geo.values()) - 1.0) < 0.10:
                out["geographic_mix"] = cleaned_geo

        if "evidence" in parsed:
            out["evidence"] = str(parsed["evidence"])[:300]

        return out
    except Exception:
        return {}


# ── Pipeline-asset extractor (rNPV input) ────────────────────────────────────

def _extract_pipeline_assets(
    sdk_client,
    model_name: str,
    sections: dict[str, str],
    deep_research: str,
    ticker: str,
) -> list[dict]:
    """
    LLM pass over the deep research report to extract individual biopharma
    pipeline assets (drugs / therapies / devices) with their current clinical
    phase and peak-sales forecasts.

    Consumed by _compute_rnpv() in dcf_agent.py. Each asset is risk-adjusted
    by phase-appropriate PoS and discounted to today, producing an asset-level
    NPV that is summed across the pipeline.

    Returns a list of asset dicts — an empty list means rNPV falls back to
    its DCF proxy. Invalid individual assets are silently dropped; we trust
    the research but validate field structure.

    Asset schema:
        {
          "name":            str,    # drug/therapy name or indication shorthand
          "phase":           str,    # preclinical | phase_1 | phase_2 | phase_3 |
                                    #  filed | approved (or alias handled by
                                    #  normalize_phase in sector_profiles.py)
          "peak_sales_usd":  float,  # forecast annual peak sales in USD
          "launch_year":     int,    # expected first-year commercial launch
          "indication":      str,    # disease area (optional, informational)
          "evidence":        str,    # source fragment from research (≤200 chars)
        }

    The extractor is intentionally permissive on peak_sales — for pre-approval
    assets with no analyst consensus, the LLM may extract management guidance,
    TAM × reasonable penetration, or comparable-drug sales. Evidence field
    surfaces the reasoning in the audit trail.
    """
    if not deep_research and not sections:
        return []

    # Biopharma pipeline content lives mostly in 2A (moat/product), 2D (cycle)
    # and 2F (KPI framework — which for biopharma is typically trial data /
    # readouts). Fall back to the whole report if sections are thin.
    section_2a = sections.get("2a") or sections.get("2A") or ""
    section_2d = sections.get("2d") or sections.get("2D") or ""
    section_2f = sections.get("2f") or sections.get("2F") or ""
    # Focus on 2F — the sector-specific KPI framework is where NRR / CAC /
    # Rule of 40 / Magic Number / Capital ratios / Pipeline assets / REIT
    # metrics / Bank metrics all live. 2A (profit pool) and 2D (cycle) are
    # context but don't contain the structured KPIs the extractor needs;
    # including them diluted the LLM's attention and hit the truncation
    # ceiling on rich reports (DDOG 2A+2D = 7800 chars → 2F starved to 200
    # chars → only RPO extracted from 2A preload data, missing NRR/CAC/
    # Magic/R40 that sit in 2F). Use 2F only. Falls back to full deep
    # research text when 2F is missing or too short.
    combined = section_2f.strip()
    if len(combined) < 500:
        combined = (section_2a + "\n\n" + section_2d + "\n\n" + section_2f).strip()
    if not combined or len(combined) < 500:
        combined = (deep_research or "")[:20000]
    if not combined:
        return []

    try:
        resp = _call_llm_with_rate_retry(
            sdk_client,
            extractor_name="pipeline_assets",
            ticker=ticker,
            model=model_name,
            max_tokens=2000,
            system=(
                "You are a biopharma pipeline analyst. Read the provided deep research "
                "excerpts and extract the company's drug/therapy/device pipeline as "
                "structured JSON. Respond ONLY with a valid JSON array — no commentary, "
                "no markdown code fences.\n\n"
                "Schema: array of asset objects, each with:\n"
                "  - name:            string (drug name, therapy code, or indication shorthand)\n"
                "  - phase:           one of: 'preclinical', 'phase_1', 'phase_2', 'phase_3', "
                "'filed', 'approved' (use these exact snake_case keys)\n"
                "  - peak_sales_usd:  number (annual peak sales in USD; use analyst "
                "consensus if cited, otherwise management-disclosed TAM × reasonable "
                "penetration, or comparable-drug sales)\n"
                "  - launch_year:     integer (4-digit year of expected commercial launch; "
                "use current year if already approved/launched)\n"
                "  - indication:      string (short disease/use-case tag; can be empty)\n"
                "  - evidence:        string (≤200 chars, one-sentence source from the research)\n\n"
                "Rules:\n"
                "  * Extract ONLY assets explicitly mentioned in the research. If the research "
                "does not mention a pipeline, return [].\n"
                "  * Include approved/marketed drugs AS WELL AS clinical-stage assets — "
                "the rNPV model values the whole portfolio.\n"
                "  * For approved drugs, use recent annual revenue as peak_sales_usd (or "
                "consensus peak if analysts expect further growth).\n"
                "  * For early-stage assets with no peak-sales forecast, estimate from TAM "
                "× peak market share (e.g. $5B TAM × 15% share = $750M peak). Explain in "
                "evidence.\n"
                "  * Non-biopharma companies: return [].\n"
                "  * If the company is a biopharma but no pipeline details are discussed, "
                "return [].\n"
                "  * Maximum 15 assets — focus on the most material.\n"
            ),
            messages=[{
                "role": "user",
                "content": (
                    f"Ticker: {ticker}\n\n"
                    f"Research excerpts (moat + cycle + KPI sections):\n{combined[:10000]}"
                ),
            }],
        )
        raw = "".join(b.text for b in resp.content if hasattr(b, "text"))
        parsed = _parse_llm_json(raw, extractor_name="pipeline_assets")
        if parsed is None or not isinstance(parsed, list):
            return []

        out: list[dict] = []
        for a in parsed:
            if not isinstance(a, dict):
                continue
            name = str(a.get("name", "")).strip()[:120]
            phase = str(a.get("phase", "")).strip().lower()
            try:
                peak = float(a.get("peak_sales_usd", 0))
            except (TypeError, ValueError):
                continue
            try:
                launch = int(a.get("launch_year", 0))
            except (TypeError, ValueError):
                launch = 0
            # Basic sanity: name, phase, positive peak sales
            if not name or not phase or peak <= 0:
                continue
            # Reject peak sales so large they're implausible (>$100B/yr single-asset)
            if peak > 100e9:
                continue
            out.append({
                "name":           name,
                "phase":          phase,
                "peak_sales_usd": peak,
                "launch_year":    launch if 1990 <= launch <= 2060 else 0,
                "indication":     str(a.get("indication", ""))[:80],
                "evidence":       str(a.get("evidence", ""))[:300],
            })
        return out[:15]
    except Exception:
        return []


# ── Delta research helpers ────────────────────────────────────────────────────

def _build_delta_system(year: str, last_run_date: str) -> str:
    """
    Lightweight system prompt for the delta research pass.

    Instructs Claude to run exactly _DELTA_MAX_SEARCHES date-scoped searches
    covering only events since `last_run_date`, then output section-tagged
    amendment paragraphs (not a full 2A-2F report).

    year          — 4-digit string for the current calendar year ("2026").
    last_run_date — analysis_date from the cached run (e.g. "2026-03-24").
    """
    return f"""
You are issuing a delta update to an existing equity deep research report.
Base research was completed on {last_run_date}. Today is {year}.

Run exactly {_DELTA_MAX_SEARCHES} web searches.
Every search query MUST include "since {last_run_date}" to scope results to
new developments only — do not retrieve information that predates {last_run_date}.

SEARCH TARGETS:
  1. [Company] earnings results revenue guidance since {last_run_date}
  2. [Company] M&A acquisition merger deal announcement since {last_run_date}
  3. [Company] regulatory government policy ruling since {last_run_date}
  4. [Company] analyst upgrade downgrade price target revision since {last_run_date}

OUTPUT FORMAT — produce exactly one line per section:
  [2A] <2–4 sentence amendment describing what is NEW since {last_run_date}>
  [2B] NO CHANGE

Cover all six sections: 2A, 2B, 2C, 2D, 2E, 2F.
Output "NO CHANGE" for any section where nothing material has occurred.

Rules:
- Do NOT repeat or paraphrase the base research — only report new facts.
- Cite inline: (Source Name, Month Year) — e.g. (Reuters, March 2026).
- If a search returns no results dated after {last_run_date}, mark that section NO CHANGE.
""".strip()


def _merge_delta_into_sections(
    base_sections: dict[str, str],
    delta_text: str,
    today: str,
) -> tuple[dict[str, str], str]:
    """
    Parse the delta LLM output and append amendments to the relevant base sections.

    Lines tagged "[2X] NO CHANGE" are silently ignored.
    Lines tagged "[2X] <amendment>" are appended to the matching section as a
    clearly dated "DELTA UPDATE" block.

    Returns (merged_sections_dict, merged_full_text).
    merged_full_text is a plain-text reconstruction of all sections in order —
    same format consumed by specialist.py via state["data"]["deep_research"].
    """
    delta_pat = re.compile(
        r"\[2([A-F])\]\s*(.*?)(?=\n\[2[A-F]\]|\Z)",
        re.DOTALL | re.IGNORECASE,
    )
    merged = dict(base_sections)
    for m in delta_pat.finditer(delta_text):
        key  = "2" + m.group(1).lower()
        body = m.group(2).strip()
        if not body or body.upper() == "NO CHANGE":
            continue
        update_block = f"\n\n── DELTA UPDATE [{today}] ──\n{body}"
        if key in merged:
            merged[key] = merged[key] + update_block
        else:
            merged[key] = update_block.lstrip()

    # Reconstruct full report text in section order
    merged_full = "\n\n".join(
        f"{k.upper()}.\n{v}" for k, v in sorted(merged.items())
    )
    return merged, merged_full


# ── News supplement helper ───────────────────────────────────────────────────────

def _build_news_supplement(
    client: "anthropic.Anthropic",
    model_name: str,
    news_sentiment: dict,
    ticker: str,
    as_of: str,
    since_date: str | None = None,
) -> str:
    """
    Synthesize Phase 2.5 news_sentiment data into a structured 'Recent Market
    Developments' section via a single lightweight LLM call.

    Data flow:
      - `news_sentiment` is already in state["data"] from Phase 2.5 news_sentiment_agent
        (FMP API, no additional fetch needed).
      - The delta path's 4 web searches already ran before this is called.
      - One LLM call (~900 token output) synthesizes the filtered headlines into
        an analytical section for injection as deep_research_sections["recent_news"].

    since_date: filters top_headlines to only those published AFTER the last
                archived run date, so the LLM focuses exclusively on new developments.
    Returns "" if no news_sentiment data is available.
    """
    if not news_sentiment or not news_sentiment.get("article_count"):
        return ""

    signal        = news_sentiment.get("signal", "NEUTRAL")
    score         = news_sentiment.get("composite_score", 0.0)
    volume_spike  = news_sentiment.get("volume_spike", False)
    top_headlines = news_sentiment.get("top_headlines") or []
    analysis_note = news_sentiment.get("analysis_note", "")

    # Filter to post-cache-date headlines only
    if since_date:
        top_headlines = [h for h in top_headlines if h.get("date", "") > since_date]

    spike_note = " [VOLUME SPIKE — unusual activity]" if volume_spike else ""

    if not top_headlines:
        # No new headlines — return a brief no-change note without calling LLM
        return (
            f"RECENT NEWS SUPPLEMENT — {ticker} (as of {as_of})\n"
            f"Signal: {signal} | Score: {score:+.3f}\n"
            f"No new headlines found since {since_date}. News environment appears unchanged."
        )

    headlines_text = "\n".join(
        f"  [{h.get('date','')}] {h.get('title','')} (score: {h.get('score',0):+.3f})"
        for h in top_headlines[:10]
    )

    prompt = (
        f"Ticker: {ticker}\n"
        f"News since: {since_date or 'recent'} | As of: {as_of}\n"
        f"Sentiment signal: {signal}{spike_note} | Composite score: {score:+.3f}\n"
        f"FMP analysis note: {analysis_note}\n\n"
        f"Recent headlines:\n{headlines_text}\n\n"
        f"Write a concise 'Recent Market Developments' section (2–3 paragraphs, ~300 words). "
        f"Focus on material events since {since_date} — earnings, guidance, regulatory changes, "
        f"M&A, or competitive developments that could affect the investment thesis. "
        f"Reference specific headlines. Start directly with the content, no header needed."
    )

    try:
        resp = client.messages.create(
            model=model_name,
            max_tokens=900,
            system=(
                "You are a financial analyst writing a research supplement. "
                "Synthesize the provided recent news headlines into a concise, "
                "investment-relevant 'Recent Market Developments' section."
            ),
            messages=[{"role": "user", "content": prompt}],
        )
        synthesis = resp.content[0].text.strip() if resp.content else ""
    except Exception as _exc:
        # Graceful fallback — emit headlines as plain text if LLM call fails
        synthesis = "\n".join(
            f"• [{h.get('date','')}] {h.get('title','')}" for h in top_headlines[:5]
        )

    return (
        f"RECENT NEWS SUPPLEMENT — {ticker} (as of {as_of})\n"
        f"Signal: {signal} | Score: {score:+.3f} | New articles: {len(top_headlines)}{spike_note}\n\n"
        + synthesis
    )


# ── System prompt ───────────────────────────────────────────────────────────────

def _build_research_system(
    year: str,
    sector: str = "",
    profile_name: str = "",
    reit_subtype: str | None = None,
) -> str:
    """Return the deep research system prompt with dynamic year references.

    Args:
        year: 4-digit string, e.g. "2026". Computes ym1 (year-1) and y1 (year+1)
            for the search sequence so all queries target the correct calendar
            window.
        sector: strategic router classification (e.g. "RealEstate", "Financials",
            "Tech", "Biopharma"). Drives section 2F template selection. Empty
            string defaults to generic 2F.
        profile_name: sub-profile from strategic router (e.g. "Money Center Bank",
            "R.E.I.T.", "Hyperscaler / Tech Conglomerate"). Allows finer routing
            within a sector (e.g. Money Center Bank KPIs vs Payment Network KPIs
            within Financials).
        reit_subtype: REIT classifier output (e.g. "net_lease", "data_center").
            Only applies when sector is RealEstate/REIT; enables finest
            specialization for the 2F block.

    Sector-aware 2F selection lives in src/agents/industry/sector_prompts.py.
    Callers that don't supply sector/profile get the generic 2F block (backward-
    compatible).
    """
    from src.agents.industry.sector_prompts import get_kpi_prompt
    ym1 = str(int(year) - 1)   # e.g. "2025" when year="2026"
    y1  = str(int(year) + 1)   # e.g. "2027" when year="2026"
    kpi_framework_block = get_kpi_prompt(sector, profile_name, reit_subtype)
    return f"""
TOOL ENVIRONMENT (read first — non-negotiable):
- You have exactly ONE tool: web_search. That is the only tool in this context.
- There is NO code_execution tool. Do not reference it, attempt to use it, or
  assume it exists.
- The web_search tool has NO rate limits. Execute searches one after another
  without waiting or pausing.
- Do NOT write any narration about tools, rate limits, search progress, or your
  own process. Do not use phrases like "let me search", "I'll look up", "the
  tool appears", "rate-limited", "hitting limits", "simultaneously", or
  "compile data collected".
- Begin searches immediately. Write ONLY structured research findings.
- Do not explain what you are about to do. Just do it.

You are a buy-side research analyst conducting pre-brief intelligence gathering
for an investment committee. You have access to a live web search tool.

MANDATORY: You MUST call web_search at least 8 times BEFORE writing any report
text. Do not produce the Section 2 report until all searches are complete.

Two data sources are available to you — use both:
  [A] PRE-LOADED FMP DATA (in the user message): revenue, net income, FCF, capex,
      net debt, insider activity for up to 5 years. These are already cited as
      (Financial Data API). Integrate them directly into 2A quantitative analysis —
      compute trends, ratios, and CAGR. Do NOT re-search for them.
  [B] WEB SEARCH (your tool): everything FMP cannot provide — management commentary,
      competitive intelligence, regulatory filings, industry forecasts, analyst views,
      contract wins/losses, and the qualitative narrative behind the numbers.

All claims that are NOT in the pre-loaded FMP data must be grounded in a web
search result. If a search returns poor results, reformulate and retry.

SOURCE QUALITY STANDARDS (mandatory — applies to every claim in this report):
- PREFERRED primary sources: SEC EDGAR filings (10-K, 10-Q, 8-K, S-1, DEF 14A),
  company investor relations pages, official earnings call transcripts, government
  regulatory databases (EIA, IRS, NRC, FERC, FDA, FCC, CFTC, OCC), Bloomberg,
  Reuters, Financial Times, Wall Street Journal, S&P Global, Moody's, Fitch,
  and named sell-side research reports.
- ACCEPTABLE secondary sources: IDC, Gartner, Forrester, CB Insights, PitchBook,
  Wood Mackenzie, IHS Markit, IoT Analytics, S&P Global Market Intelligence, Bloomberg
  Intelligence, Reuters, Financial Times, and recognised industry trade publications.
  ALWAYS include: publisher name, publication date, and URL or report title when citing
  these sources. Format: "[figure] (Source: [Publisher], [Month Year], [URL or report title if known])".
  These figures are valuable and MUST be included — proper attribution is mandatory, omission is not.
- LOWER-TIER sources (use only if no Tier 1/2 source available, and flag explicitly):
  Cognitive Market Research, Allied Market Research, Grand View Research, Market Research Future,
  and other market sizing firms without named analysts or verifiable methodology. If you cite
  these, write: "(Source: [Publisher], [date] — lower-tier publisher; cross-validation with
  Gartner/IDC recommended)". Never cite them as authoritative standalone figures.
- PROHIBITED sources (do NOT cite): Wikipedia, student theses or course papers,
  personal blogs, Reddit, Quora, consumer review sites (Trustpilot, G2, Yelp),
  press release aggregators with no attributed authorship, or any site whose
  primary audience is retail consumers rather than institutional investors.
- If a search returns only prohibited sources, reformulate the query to target
  SEC EDGAR or the company's official IR page directly.
- All regulatory, grid, energy, drug, or spectrum data MUST cite the relevant
  government agency (EIA, NRC, FERC, FDA, FCC) — NOT a third-party summary.

Suggested search sequence (adapt as findings dictate):
Note: revenue, margins, FCF, capex, and insider trade data are PRE-LOADED above
from Financial Datasets API. Do NOT search for those — use all searches for
qualitative intelligence that FMP cannot provide.
Year guidance: searches marked [RECENT] use {ym1}–{year} to capture both the
prior year and current year. Searches marked [FORWARD] use {year}–{y1} for
outlook data. Do not restrict results to a single year — older context is fine.

  1. "[Ticker] CEO management commentary strategy outlook {ym1} {year} earnings call"   [RECENT]
  2. "[Ticker] market share competitive landscape {ym1} {year}"                         [RECENT]
  3. "[Ticker] earnings call transcript key quotes guidance {ym1} {year}"               [RECENT]
  4. "[Ticker] competitor analysis market positioning {year} {y1}"                      [FORWARD]
  5. "[Industry] market size growth rate {year} {y1} IDC Gartner"                      [FORWARD]
  6. "[Ticker] product launches AI strategy new products {ym1} {year}"                  [RECENT]
  7. "[Ticker] regulatory government policy ruling {ym1} {year}"                        [RECENT]
  8. "[Ticker] analyst price target consensus upgrade downgrade {ym1} {year}"           [RECENT]
     → When reporting analyst price targets, always include the date of the most recent
       revision. Flag any consensus PT that has not been updated within the last 6 months
       as STALE — stale targets may not reflect the current price level or recent earnings.
  9. "[Ticker] material event impairment restructuring asset sale write-down {ym1} {year}" [RECENT]
 10. "[Industry] M&A acquisition merger deal completed EV EBITDA comparable multiple {ym1} {year}" [RECENT]
 11. "[Ticker] GAAP revenue vs adjusted revenue reconciliation {ym1} {year} 10-K"      [RECENT]
 12. "[Ticker] customer wins losses major contract partnership {ym1} {year}"            [RECENT]
 13. "[Ticker] management guidance EBITDA revenue outlook forecast FY{y1} {year}"      [FORWARD]
     → CRITICAL: Extract any quantitative forward guidance from earnings calls, investor
       presentations, or press releases. Report EBITDA guidance range (low/mid/high),
       revenue guidance, capex guidance, and margin targets as exact dollar figures.
       Format as: "EBITDA guidance: $X.XB–$X.XB (mid $X.XB)" so DCF agent can parse it.
  + additional searches to fill gaps in any sub-section

Your output feeds directly into downstream valuation and risk agents:
  2A (Profit pool)   → informs variant perception analysis
  2B (Competition)   → informs scenario assumptions (bull/base/bear)
  2C (Moat)          → informs WACC and terminal growth rate
  2D (Cycle)         → informs mid-cycle normalisation
  2E (Disruption)    → informs bear case scenario
  2F (KPIs)          → informs anchor KPI monitoring in the valuation agent

Quote specific figures with dates and source names in the report.
If a search returns nothing useful, try a different angle.

════════════════════════════════════════════
SECTION 2 — INDUSTRY STRUCTURE
════════════════════════════════════════════

──────────────────────────────────────────
2A. PROFIT POOL MAP
──────────────────────────────────────────
Purpose: Establish WHERE the money is made in this industry before analysing
competition. Many companies compete fiercely for low-margin segments while
ignoring high-margin ones.

2A.1 Draw the value chain — every step from raw input to end customer.
For each step estimate: gross margin range (%), who controls it (concentrated /
fragmented), whether margin is expanding/stable/compressing, and capital
intensity (asset-heavy / asset-light).

2A.2 Where does this company sit in the chain? Is it moving up or down
(vertical integration trend)? What is its stated rationale — margin capture,
data control, customer lock-in, or defensive reaction?

2A.3 Material one-time events: Flag any facility fires, regulatory shutdowns,
asset write-downs, or force-majeure events in the last 18 months. For each,
estimate the EBITDA impact ($ and % of total), note the recovery/rebuild
timeline if publicly disclosed, and state whether the event is reflected in
current consensus estimates or guidance. Cite the specific event name, date,
and primary source (8-K filing, earnings call, regulatory notice).

2A.4 Revenue definition check: If multiple revenue figures appear across
sources (e.g. GAAP vs. Adjusted, segment vs. consolidated, FX-adjusted vs.
reported), state both figures with dates and identify which definition is used
in the official 10-K. Flag as REVENUE DISCREPANCY if the gap exceeds 5% of
reported revenue and provide the SEC filing reference for reconciliation.
IMPORTANT — Always use GAAP Total Revenue (consolidated, as reported in the
10-K/10-Q) for all YoY growth rate calculations. Never use product-segment
revenue, adjusted revenue, or constant-currency revenue as the primary growth
figure unless explicitly stated as such. If the report uses any non-GAAP revenue
basis for growth, label it "(non-GAAP)" and provide the GAAP equivalent.

2A.3 Profit pool shift: Has the dominant margin layer shifted in the last
5–10 years? What caused it and where is it moving next?

2A.4 Identify the "toll booth" in this industry — the single chokepoint where
a player can extract rent regardless of who wins downstream. Who owns it?
Does this company?

──────────────────────────────────────────
2B. COMPETITIVE LANDSCAPE
──────────────────────────────────────────
Purpose: Understand the actual competitive dynamics — not just who the
competitors are, but the nature of the competition.

2B.1 Market structure classification: monopoly / duopoly / oligopoly /
fragmented / winner-take-most / regulated oligopoly.

2B.2 Market share map — top 5 players with share %, 3-year trend, and
competitive basis (price / product / distribution).
Key question: Is share movement driven by price or product differentiation?
Price-driven gains are unsustainable; product-driven gains are durable.
LABELING RULES (mandatory):
- Private-company revenues (e.g. Deloitte, McKinsey, Accenture Federal Services):
  explicitly label as "estimated" and name the source (e.g. "~$29B estimated, per Gartner 2024").
  These firms do not publish audited public financials.
- Undisclosed business segments: if the company does not formally report a named sub-segment
  (e.g. a government services division), any margin or revenue figure for that sub-segment is
  an analyst ESTIMATE inferred from disclosed data. Write: "~X% operating margin (analyst estimate
  based on [disclosed segment], not a formally reported segment financial)."
- Market share figures from third-party research: always include the publisher name and
  publication date (e.g. "~7% GenAI services market share as of January 2025, per IoT Analytics").
  Do NOT attribute the figure to the company's SEC filing period.

2B.3 Basis of competition — what do customers actually buy on? Rank:
price, product performance, reliability/uptime, switching cost/ecosystem,
brand/trust, regulatory compliance, speed/delivery.
If price is #1, explain how this company avoids commodity pricing.

2B.4 Competitive response profile: when this company gains share, do
competitors respond with price cuts / product investment / M&A /
regulatory challenge / no response?

2B.5 New entrant threat — for each type (AI-native attacker, adjacent
industry player, vertically integrated customer, low-cost geographic
entrant, PE roll-up) rate threat as High/Medium/Low. For High threats:
realistic timeline to material revenue impact, and the structural barrier
that has prevented entry so far.

──────────────────────────────────────────
2C. MOAT ANALYSIS
──────────────────────────────────────────
Purpose: Determine whether this company's competitive advantage is durable,
widening, or eroding. Moats are not binary — they have direction and velocity.

2C.1 Moat type — identify the PRIMARY source (one only):
network effects / switching costs / cost advantage / intangible assets /
efficient scale.

2C.2 Moat evidence — the test (choose appropriate for moat type):
- Network effects: LTV trend, CAC trend (3-year data)
- Switching costs: churn rate, what customer loses by switching, price
  increase test results
- Cost advantage: unit cost vs competitor over 3 years, widening or narrowing
- Intangible assets: patent expiry, licence terms, NPS / brand value trend,
  data proprietary and monetised?
- Efficient scale: minimum efficient scale, could a second entrant earn CoC?

2C.3 Moat direction — score each over 3 years (widening + / stable = / narrowing −):
gross margin trend, customer retention trend, pricing power realised,
market share trend, ROIC vs WACC spread.
Flag "Moat Erosion Risk" if 3+ narrowing; "Moat Expansion" if 3+ widening.

2C.4 Moat stress test — what specific scenario destroys the primary moat?
Describe the attack vector for each moat type.

──────────────────────────────────────────
2D. INDUSTRY CYCLE POSITIONING
──────────────────────────────────────────
Purpose: Most valuation errors are timing errors. Identify where in the
cycle this industry is today.

2D.1 Industry lifecycle stage: emergence / growth / shakeout / maturity /
decline. Cite data (industry revenue CAGR, competitor count trend, M&A).

2D.2 Cyclical vs structural demand: What % of current revenue is cyclically
elevated vs normalised? What is the mid-cycle revenue / EBITDA?
For cyclical industries: Trough / Recovery / Mid-cycle / Late-cycle / Peak.

2D.3 Capacity and supply dynamics: current utilisation %, new capacity
announced, time to bring online, utilisation rate at which pricing power
emerges. Is new supply entering fast enough to cap pricing?

2D.4 Inventory cycle (product companies): inventory days vs industry,
channel inventory level, destocking or restocking underway, typical duration.

──────────────────────────────────────────
2E. DISRUPTION & STRUCTURAL CHANGE VECTORS
──────────────────────────────────────────
Purpose: Identify forces reshaping this industry over a 3–7 year horizon.

2E.1 Technology disruption vectors — for each relevant technology:
impact (Enhances / Neutral / Threatens), timeline (<2yr / 2–5yr / >5yr),
probability (High / Medium / Low), company response (Leading / Following /
Ignoring).

2E.2 Regulatory and policy change vectors: active proceedings affecting
pricing or market structure; policy tailwinds (subsidies, mandates);
policy headwinds (antitrust, tariffs, ESG mandates); geopolitical exposure
by revenue / supply chain geography — flag China / Taiwan / Russia / MENA.

2E.3 Business model disruption: is a new business model (not just product)
attacking this industry? Subscription replacing transactional, platform
replacing linear chain, D2C replacing distribution, outcome-based pricing.
For each: penetration %, company position (leading / matching / trailing),
impact on unit economics and cash flow timing.

2E.4 3-year industry structure question: In 3 years, will there be more or
fewer credible competitors? Higher or lower industry gross margins? Tighter
or looser regulation? Different dominant cost structure?
For each: the single evidence point supporting your view AND the single data
point that would change your mind.

{kpi_framework_block}

════════════════════════════════════════════
OUTPUT FORMAT & CITATION REQUIREMENTS
════════════════════════════════════════════
After completing your searches, write the full Section 2 report using the
sub-section headers above (2A through 2F). Each sub-section must be populated
with real data from your searches — do not leave placeholders. Do NOT include
a BUY/SELL recommendation.

INLINE CITATION FORMAT (mandatory for every factual claim):
Every figure, statistic, market share estimate, management quote, or forecast
must carry a numbered footnote marker inline: e.g. "~7% GenAI services market
share [1]" or "revenue grew 13% YoY to $69.7B [2]".

At the end of EACH sub-section (2A through 2F), append a REFERENCES block:

  REFERENCES
  [1] IoT Analytics — GenAI Services Market Share Report, January 2025
      URL: https://iot-analytics.com/... (or "URL unavailable — paywalled")
  [2] Accenture Form 10-K FY2025, SEC EDGAR, acc: 0001467373-25-000082
      URL: https://www.sec.gov/Archives/edgar/...
  [3] Jensen Huang, CEO — Q3 FY2026 Earnings Call, 20 November 2025
      URL: https://investor.nvidia.com/...

CITATION RULES:
- Every [n] marker in the text must have a matching entry in that section's REFERENCES block
- SEC filings: include accession number and EDGAR URL
- Third-party research (IDC, Gartner, IoT Analytics, etc.): include publisher name,
  report title, publication month/year, and URL if publicly accessible (note "paywalled" if not)
- Management quotes: include speaker full name + title, event name, and date
- Competitor / private-company estimates: label explicitly as "estimated" in both the text
  and the reference (e.g. "[3] Deloitte revenue ~$29B estimated — Gartner IT Services 2024")
- Undisclosed segment margins: label as "analyst estimate" in both text and reference
  (e.g. "[4] AFS operating margin ~10–12% — analyst estimate inferred from H&PS segment,
  not a formally disclosed Accenture segment financial")
- Lower-tier market research publishers (Cognitive Market Research, Allied Market Research,
  Grand View Research, etc.): add "(lower-tier publisher — cross-validate with Gartner/IDC)"
  to the reference entry
- If a claim comes from your training knowledge with no searchable source, write:
  "[n] Knowledge base — no primary source retrieved; verify before publishing"
""".strip()


# ── Citation Registry Extraction ─────────────────────────────────────────────

_REGISTRY_EXTRACTION_PROMPT = """
You are a research citation extractor. Read the following investment research report
and extract a structured citation registry.

EXTRACTION PRIORITY ORDER:
1. FOOTNOTE-FIRST: The report uses numbered footnote markers [1], [2], etc. with REFERENCES
   blocks at the end of each section. Extract from these REFERENCES blocks first — they are
   pre-structured and accurate. Map each [n] marker in the text to its matching reference entry.
2. INLINE FALLBACK: For claims without a [n] marker, extract the claim and set
   source_type="knowledge_base", verified=false — these are the only ones needing audit work.

For each extracted item, provide these exact fields:
- ref_id: integer starting at 1, incrementing
- claim: the specific claim or figure — include the complete number, do NOT truncate digits or mask decimals
  Examples: "Data center revenue $30.8B Q3 FY2026", "~7% GenAI services market share [1]"
- source_name: from the REFERENCES block if [n] marker present; "unknown" if no source in text
  — do NOT invent a source
- source_type: one of exactly: "10-K", "10-Q", "20-F", "earnings_transcript",
  "press_release", "third_party_research", "regulatory_filing", "web_search",
  "management_guidance", "knowledge_base"
  Use "knowledge_base" when no source is cited in the text.
- date: from REFERENCES block if available, else fiscal period from context, else ""
- speaker: full name and title if a direct quote, else ""
- quote: exact direct quote (include in full, do not truncate) or ""
- url: URL from REFERENCES block if present, else ""
- section: "2a", "2b", "2c", "2d", "2e", "2f", or "unknown"
- verified: true ONLY if source_name is a specific named document AND date is present
  (this includes named third-party publishers like "IDC", "Gartner", "IoT Analytics"
  with a publication date — these ARE verified even without a URL);
  false for "knowledge_base", "unknown", or vague sources ("industry sources", "analysts say")

Rules:
- Extract ALL quantitative claims: revenue, margins, growth rates, market share %, KPIs,
  headcount, contract values, product pricing, CAGR, industry size
- Extract ALL management quotes and forward guidance
- Maximum 35 entries — if more exist, prioritise: (1) financial metrics with [n] markers,
  (2) management quotes, (3) market structure claims with [n] markers, (4) unlabelled claims
- Return ONLY the raw JSON array — no prose, no markdown code fences, no explanation

REPORT:
""".strip()


def _extract_citation_registry(
    sdk_client,
    model_name: str,
    report_text: str,
    ticker: str,
    edgar_filing_ref: dict | None = None,
) -> list[dict]:
    """
    Second-pass lightweight extraction: reads the Section 2 research report
    and emits a structured citation registry as a list of dicts.

    This is a cheap call (no web tools, ~2000 tokens output max) that runs
    after the main research report is written.  The registry feeds:
      - citation_auditor.py  (Phase 1 sourced/unsourced classification)
      - specialist.py        (footnote tagging in the Industry Brief)
      - pdf_report.py        (footnote block at bottom of brief page)

    edgar_filing_ref: if provided, injected as a context header so the LLM
      knows the company's SEC filing type and can properly attribute financial
      statement metrics (revenue, net income, etc.) to the actual 20-F/10-K.

    Returns [] on any failure — pipeline continues without citation data.
    """
    if not report_text or not report_text.strip():
        return []
    if sdk_client is None:
        print(f"  [citation_registry] sdk_client is None for {ticker} — skipping LLM extraction")
        return []

    # Build filing context header to inject before the prompt
    _edgar   = edgar_filing_ref or {}
    _acc     = _edgar.get("accession_number")
    _is_ipo  = _edgar.get("is_ipo_prospectus", False)
    _is_stub = _edgar.get("is_stub", False)
    _is_hkex = _edgar.get("exchange") == "HKEX"

    if _is_hkex and _edgar.get("filing_url"):
        # HKEX Annual Report — filing_url is the direct PDF from HKEXnews
        _fy           = _edgar.get("fiscal_year", "")
        _period       = _edgar.get("period_of_report", "")
        _co_name      = _edgar.get("company_name", ticker)
        _filing_url   = _edgar.get("filing_url", "")
        _viewer_url_ctx = _edgar.get("viewer_url", "")
        _filing_ctx   = (
            f"HKEX PRIMARY SOURCE FOR {ticker}:\n"
            f"  Company: {_co_name}\n"
            f"  Annual Report FY{_fy}"
            + (f" | Period: {_period}" if _period else "")
            + f"\n  Annual Report PDF: {_filing_url}\n"
            + (f"  HKEXnews annual report search: {_viewer_url_ctx}\n" if _viewer_url_ctx else "")
            + f"\n"
            f"ATTRIBUTION RULES — apply these for {ticker}:\n"
            f"  • Revenue, net income, free cash flow, capex, net debt, EPS, margins → "
            f"source_type='Annual Report',\n"
            f"    source_name='{_co_name} Annual Report FY{_fy}',\n"
            f"    url='{_filing_url}', verified=true\n"
            f"  • Business overview, risk factors, strategy → source_type='Annual Report', verified=true\n"
            f"  • Market share estimates, analyst targets → verified=false\n\n"
        )
    elif _edgar and (_acc or _is_stub):
        _filing_type = _edgar.get("filing_type", "20-F")
        _accession   = _acc or ""
        _filing_date = _edgar.get("filing_date", "")
        _period      = _edgar.get("period_of_report", "")
        _fy          = _edgar.get("fiscal_year", "") or (_period[:4] if _period else "")
        _co_name     = _edgar.get("company_name", ticker)
        _filing_url  = _edgar.get("filing_url", "") or ""
        _viewer_url  = _edgar.get("viewer_url", "") or ""
        _url_for_attr = _filing_url or _viewer_url

        if _is_stub:
            # CIK known but no filing found — use EDGAR company page for attribution
            _filing_ctx = (
                f"EDGAR COMPANY CONTEXT FOR {ticker}:\n"
                f"  Company: {_co_name} (CIK: {_edgar.get('cik', '')})\n"
                f"  No annual report yet filed on EDGAR (very recent IPO or pending).\n"
                f"  EDGAR company page: {_viewer_url}\n\n"
                f"ATTRIBUTION RULES — apply these for {ticker}:\n"
                f"  • All financial figures → source_type='FMP_API',\n"
                f"    source_name='{_co_name} (via Financial Data API)', verified=false\n"
                f"  • Flag as 'pre-annual-report' — no 20-F/10-K filed yet\n\n"
            )
        elif _is_ipo:
            # IPO prospectus (F-1/S-1) — more informative than FMP fallback
            _filing_ctx = (
                f"FILING CONTEXT FOR {ticker} (IPO REGISTRATION STATEMENT):\n"
                f"  Company: {_co_name}\n"
                f"  SEC Form: {_filing_type} | Accession: {_accession}\n"
                f"  Filed: {_filing_date}\n"
                f"  URL: {_url_for_attr}\n"
                f"  NOTE: This company recently completed its IPO. No annual report (20-F/10-K)\n"
                f"  has been filed yet. All pre-IPO financial data originates from the\n"
                f"  registration statement prospectus.\n\n"
                f"ATTRIBUTION RULES — apply these for {ticker}:\n"
                f"  • Revenue, net income, margins, cash flow, GMV → source_type='{_filing_type}',\n"
                f"    source_name='{_co_name} Form {_filing_type} (IPO Prospectus, SEC EDGAR)',\n"
                f"    url='{_url_for_attr}', verified=true\n"
                f"  • IPO proceeds, ADS price, listing details → source_type='{_filing_type}',\n"
                f"    source_name='{_co_name} Form {_filing_type} — Offering Details', verified=true\n"
                f"  • Company history, business model, risk factors → source_type='{_filing_type}',\n"
                f"    source_name='{_co_name} Form {_filing_type} — Business Overview', verified=true\n"
                f"  • Analyst targets, market share estimates → verified=false\n\n"
            )
        else:
            # Normal annual report (20-F or 10-K)
            _filing_ctx = (
                f"FILING CONTEXT FOR {ticker}:\n"
                f"  Company: {_co_name}\n"
                f"  SEC Form: {_filing_type} | Accession: {_accession}\n"
                f"  Filed: {_filing_date} | Period: {_period} (FY{_fy})\n"
                f"  URL: {_url_for_attr}\n\n"
                f"ATTRIBUTION RULES — apply these for {ticker}:\n"
                f"  • Revenue, net income, operating/free cash flow, capex, net debt, EPS,\n"
                f"    gross/net margin, EBITDA → source_type='{_filing_type}',\n"
                f"    source_name='{_co_name} Form {_filing_type} FY{_fy}',\n"
                f"    url='{_url_for_attr}', verified=true\n"
                f"  • IPO proceeds, listing date, ADS price → source_type='press_release',\n"
                f"    source_name='Form F-1/424B4 Final Prospectus (SEC EDGAR)', verified=true\n"
                f"  • Company founding, history, business overview → source_type='{_filing_type}',\n"
                f"    source_name='{_co_name} Form {_filing_type} — Business Overview', verified=true\n"
                f"  • Store count, GMV, active users (if disclosed in filing) → source_type='{_filing_type}', verified=true\n"
                f"  • Industry margin estimates, analyst targets, market share → verified=false\n\n"
            )
    else:
        _filing_ctx = ""

    try:
        _prompt = (
            (_filing_ctx + _REGISTRY_EXTRACTION_PROMPT)
            if _filing_ctx else _REGISTRY_EXTRACTION_PROMPT
        )
        response = sdk_client.messages.create(
            model=model_name,
            max_tokens=8000,
            messages=[{
                "role": "user",
                "content": _prompt + "\n\n" + report_text[:20000],
            }],
        )
        text = "".join(
            block.text for block in response.content if hasattr(block, "text")
        )

        # Use the shared _parse_llm_json helper for consistency with the 6
        # sector extractors — same preamble / postamble / fence / reasoning-
        # trace resilience. The helper tries raw → fence-stripped → substring
        # between brackets and returns None on total failure.
        raw = _parse_llm_json(text, extractor_name="citation_registry")
        if raw is None or not isinstance(raw, list):
            return []

        registry: list[dict] = []
        for i, entry in enumerate(raw):
            if not isinstance(entry, dict):
                continue
            registry.append({
                "ref_id":       int(entry.get("ref_id", i + 1)),
                "claim":        str(entry.get("claim", ""))[:120],
                "source_name":  str(entry.get("source_name", "")),
                "source_type":  str(entry.get("source_type", "knowledge_base")),
                "date":         str(entry.get("date", "")),
                "speaker":      str(entry.get("speaker", "")),
                "quote":        str(entry.get("quote", ""))[:200],
                "url":          str(entry.get("url", "")),
                "section":      str(entry.get("section", "unknown")),
                "verified":     bool(entry.get("verified", False)),
            })

        return registry

    except json.JSONDecodeError as exc:
        print(f"  [citation_registry] JSON parse error for {ticker}: {exc}")
        print(f"  [citation_registry] Raw text snippet: {text[:300] if 'text' in dir() else 'N/A'}")
        return []
    except Exception as exc:
        print(f"  [citation_registry] Unexpected error for {ticker} ({type(exc).__name__}): {exc}")
        return []


# ── Per-ticker research worker ────────────────────────────────────────────────

def _research_one_ticker(
    ticker: str,
    sector: str,
    end_date: str,
    anthropic_key: str,
    model_name: str,
    raw_financials: dict,
    insider_summary: str,
    edgar_filing_ref: dict | None = None,
    news_sentiment_data: dict | None = None,
    base_url: str | None = None,
    synthesis_model: str | None = None,
    profile_name: str = "",
) -> dict:
    """
    Run the full archive-gate + three-tier research pipeline for a single ticker.

    This is a pure function — it does NOT read or write AgentState.
    It is called by run_deep_research_agent, either directly (single ticker)
    or via ThreadPoolExecutor (multiple tickers in parallel).

    Returns a dict with keys:
        deep_research, deep_research_sections, research_tier,
        citation_registry, web_intelligence,
        cache_hit, cache_age_days, cache_run_id
    """
    agent_id = "deep_research"

    # synthesis_model: model used for ALL sdk_client (Anthropic-compatible endpoint) calls.
    # For HK tickers, model_name = qwen3.6-plus (OpenAI-compat web search only) while
    # synthesis_model = qwen3-max (available on the Anthropic-compat endpoint).
    # For US tickers both are the same Claude model.
    _synthesis_model = synthesis_model or model_name

    # ── Archive-first gate ────────────────────────────────────────────────────
    try:
        from src.memory.run_archive import get_recent_research as _get_recent
        _cached = _get_recent(ticker, max_age_days=_FRESH_DAYS)
    except Exception:
        _cached = None

    # Never reuse knowledge_only results from cache — they lack live web data
    # and should be re-run with the current model (now Qwen with web search).
    if _cached is not None and _cached.get("research_tier") == "knowledge_only":
        progress.update_status(
            agent_id, ticker,
            f"Cache contains knowledge_only result ({_cached['age_days']:.1f}d old) "
            f"— discarding, will run fresh with live web search"
        )
        _cached = None

    if _cached is not None:
        _age = _cached["age_days"]

        # ── Pure cache hit: age < 2 days ──────────────────────────────────────
        if _age < _CACHE_NO_DELTA_DAYS:
            progress.update_status(
                agent_id, ticker,
                f"Cache HIT ({_age:.1f}d old, tier={_cached['research_tier']}) "
                f"— reusing base research, 0 searches"
            )
            # Re-extract citation registry and DCF calibration from cached text/sections.
            # citation_registry is never persisted to the archive DB — only deep_research_text
            # is stored. We rebuild it so the citation auditor gets structured source metadata
            # (URLs, speakers, dates) rather than falling back to raw text only.
            _cal_client = anthropic.Anthropic(api_key=anthropic_key, base_url=base_url, timeout=60.0, max_retries=1)
            _dcf_cal = _extract_dcf_calibration(
                _cal_client, _synthesis_model, _cached["deep_research_sections"], ticker
            )
            _citations = _extract_citation_registry(
                _cal_client, _synthesis_model, _cached["deep_research_text"], ticker,
                edgar_filing_ref=edgar_filing_ref,
            )
            # Re-annotate cached text with rebuilt citation markers so the
            # frontend DeepResearchPanel can render inline [n] hyperlinks.
            _cached_text = _cached["deep_research_text"]
            _annotated_cached = _cached_text
            _used_cached: set = set()
            for _entry in sorted(
                (_e for _e in _citations if _e.get("quote") and len(_e["quote"]) > 20),
                key=lambda _e: len(_e.get("quote", "")),
                reverse=True,
            ):
                _ref_n = _entry.get("ref_id")
                if not _ref_n or _ref_n in _used_cached:
                    continue
                _phrase = _entry["quote"][:120].strip()
                _pos = _annotated_cached.find(_phrase)
                if _pos >= 0:
                    _insert = _pos + len(_phrase)
                    _annotated_cached = (
                        _annotated_cached[:_insert] + f"[{_ref_n}]" + _annotated_cached[_insert:]
                    )
                    _used_cached.add(_ref_n)
            return {
                "deep_research":            _cached_text,
                "deep_research_annotated":  _annotated_cached,
                "deep_research_sections":   _cached["deep_research_sections"],
                "research_tier":            "anthropic_web_cached",
                "citation_registry":        _citations,
                "web_intelligence":         {},
                "cache_hit":                True,
                "cache_age_days":           _age,
                "cache_run_id":             _cached["run_id"],
                "dcf_calibration":          _dcf_cal,
            }

        # ── Delta hit: 2–7 days old ───────────────────────────────────────────
        progress.update_status(
            agent_id, ticker,
            f"Cache HIT ({_age:.1f}d old) — running delta pass "
            f"({_DELTA_MAX_SEARCHES} searches since {_cached['analysis_date']})"
        )
        try:
            _today = end_date or datetime.now().strftime("%Y-%m-%d")
            _year  = _today[:4]
            _company_name    = _fetch_company_name(ticker)
            _company_display = (
                f"{_company_name} (ticker: {ticker})"
                if _company_name != ticker else ticker
            )
            _delta_client = anthropic.Anthropic(
                api_key=anthropic_key,
                base_url=base_url,
                timeout=CLIENT_TIMEOUT,
                max_retries=4,
            )
            _delta_resp = _delta_client.messages.create(
                model=_synthesis_model,
                max_tokens=8000,
                tools=[{
                    "type": _WEB_SEARCH_TOOL_VERSION,
                    "name": "web_search",
                    "max_uses": _DELTA_MAX_SEARCHES,
                }],
                system=_build_delta_system(_year, _cached["analysis_date"]),
                messages=[{
                    "role": "user",
                    "content": (
                        f"Company: {_company_display}\n"
                        f"Sector: {sector}\n"
                        f"Base research date: {_cached['analysis_date']}\n"
                        f"Today: {_today}\n\n"
                        f"Run {_DELTA_MAX_SEARCHES} targeted searches for material "
                        f"developments since {_cached['analysis_date']} and produce "
                        f"section-tagged amendments."
                    ),
                }],
            )
            _delta_text = "".join(
                b.text for b in _delta_resp.content if hasattr(b, "text")
            ).strip()
            _delta_text = _strip_narration(_delta_text)

            _merged_sections, _merged_full = _merge_delta_into_sections(
                _cached["deep_research_sections"], _delta_text, _today
            )
            progress.update_status(
                agent_id, ticker,
                f"Delta complete — {len(_delta_text):,} chars of amendments; "
                f"{sum(1 for s in _merged_sections.values() if 'DELTA UPDATE' in s)} "
                f"section(s) updated"
            )
            # Re-extract citation registry from merged text and DCF calibration from
            # merged sections — both use the already-open _delta_client.
            # citation_registry is not stored in the archive; rebuild from merged text
            # so the citation auditor receives structured source metadata for new delta
            # amendments as well as the original base research.
            _dcf_cal_d = _extract_dcf_calibration(
                _delta_client, _synthesis_model, _merged_sections, ticker
            )
            _citations_d = _extract_citation_registry(
                _delta_client, _synthesis_model, _merged_full, ticker,
                edgar_filing_ref=edgar_filing_ref,
            )
            # Inject Phase 2.5 news sentiment as a "recent_news" section (post-cache-date only)
            _ns_client = anthropic.Anthropic(api_key=anthropic_key, base_url=base_url, timeout=60.0, max_retries=1)
            _supplement = _build_news_supplement(
                client=_ns_client,
                model_name=_synthesis_model,
                news_sentiment=news_sentiment_data or {},
                ticker=ticker,
                as_of=_today,
                since_date=_cached.get("analysis_date"),
            )
            if _supplement:
                _merged_sections["recent_news"] = _supplement
                # Append supplement to full text
                _merged_full = _merged_full + "\n\nRECENT NEWS SUPPLEMENT.\n" + _supplement

            return {
                "deep_research":          _merged_full,
                "deep_research_sections": _merged_sections,
                "research_tier":          "archive_news_delta",
                "citation_registry":      _citations_d,
                "web_intelligence":       {},
                "cache_hit":              True,
                "cache_age_days":         _age,
                "cache_run_id":           _cached["run_id"],
                "dcf_calibration":        _dcf_cal_d,
            }

        except Exception as _delta_err:
            progress.update_status(
                agent_id, ticker,
                f"Delta pass failed ({_delta_err!r}) — falling through to full research"
            )
            # Fall through to full research below

    # ── Full research path ────────────────────────────────────────────────────

    sdk_client = anthropic.Anthropic(
        api_key=anthropic_key,
        base_url=base_url,
        timeout=CLIENT_TIMEOUT,
        max_retries=4,
    )

    today = end_date or datetime.now().strftime("%Y-%m-%d")
    year  = today[:4]

    company_name    = _fetch_company_name(ticker)
    company_display = f"{company_name} (ticker: {ticker})" if company_name != ticker else ticker

    _base_context = (
        f"Company: {company_display}\n"
        f"Sector: {sector}\n"
        f"Analysis date: {today}\n\n"
    )

    # ── Filing reference (EDGAR for US/ADR; HKEX for HK-listed) ─────────────────
    _edgar        = edgar_filing_ref or {}
    _is_hkex      = _edgar.get("exchange") == "HKEX"
    _filing_type  = _edgar.get("filing_type", "Annual Report")
    _accession    = _edgar.get("accession_number") or ""
    _filing_date  = _edgar.get("filing_date", "")
    _period       = _edgar.get("period_of_report", "")
    _fy           = _edgar.get("fiscal_year", "") or (_period[:4] if _period else "")
    _co_name      = _edgar.get("company_name", ticker)
    _filing_url   = _edgar.get("filing_url", "") or ""
    _viewer_url   = _edgar.get("viewer_url", "") or ""
    _is_ipo       = _edgar.get("is_ipo_prospectus", False)
    _is_stub      = _edgar.get("is_stub", False)
    _url_for_attr = _filing_url or _viewer_url

    # Build citation strings used throughout the prompt
    if _is_hkex and _filing_url:
        # HKEX Annual Report — cite HKEXnews PDF directly
        _fin_cite_label = (
            f"[PRIMARY SOURCE: {_co_name} {_filing_type} FY{_fy} "
            f"(HKEXnews, filed: {_filing_date}) — {_filing_url}]"
        )
        _fin_cite_as        = f"({_co_name} {_filing_type} FY{_fy}, HKEXnews)"
        _insider_cite_label = "[source: HKEX disclosure filings / AKShare insider data]"
        _insider_cite_as    = "(HKEX disclosure filings)"
    elif _edgar and _accession and not _is_stub:
        if _is_ipo:
            # IPO prospectus — valid primary source, different label
            _fin_cite_label = (
                f"[PRIMARY SOURCE: {_co_name} Form {_filing_type} IPO Prospectus "
                f"(SEC EDGAR, acc: {_accession}, filed: {_filing_date})]"
            )
            _fin_cite_as = f"({_co_name} Form {_filing_type} IPO Prospectus, SEC EDGAR)"
        else:
            # Standard annual report (20-F / 10-K)
            _fin_cite_label = (
                f"[PRIMARY SOURCE: {_co_name} Form {_filing_type} "
                f"(SEC EDGAR, acc: {_accession}, filed: {_filing_date}, period: {_period})]"
            )
            _fin_cite_as = f"({_co_name} Form {_filing_type} FY{_fy}, SEC EDGAR)"
        _insider_cite_label = "[source: SEC EDGAR Form 4 filings / FMP insider-trading API]"
        _insider_cite_as    = "(SEC EDGAR Form 4)"
    elif _edgar and _is_stub and _viewer_url:
        # CIK known but no filing — cite EDGAR company page
        _fin_cite_label    = (
            f"[EDGAR COMPANY: {_co_name} (CIK {_edgar.get('cik', '')}, no annual report yet) "
            f"— verify figures via {_viewer_url}]"
        )
        _fin_cite_as       = f"({_co_name} via Financial Data API — no annual EDGAR filing yet)"
        _insider_cite_label = "[source: FMP insider-trading API]"
        _insider_cite_as   = "(FMP insider-trading API)"
    else:
        # No filing ref at all — fall back to generic attribution
        _fin_cite_label    = "[source: Financial Datasets API — cite as (Financial Data API)]"
        _fin_cite_as       = "(Financial Data API)"
        _insider_cite_label = "[source: Financial Datasets API — cite as (Financial Data API)]"
        _insider_cite_as   = "(Financial Data API)"

    # ── Inject filing context block ───────────────────────────────────────────
    if _is_hkex and _filing_url:
        _base_context += (
            f"\nHKEX PRIMARY SOURCE — {_co_name}:\n"
            f"  {_filing_type} FY{_fy}"
            + (f" | Period: {_period}" if _period else "")
            + f"\n  Annual Report PDF: {_filing_url}\n"
            f"  HKEXnews annual report search: {_viewer_url}\n"
            f"  → Fetch and read the Annual Report PDF above for detailed financial statements.\n"
            f"  → Cite ALL financial statement figures as:\n"
            f"    {_fin_cite_as}\n\n"
            f"  NOTE: Use PREFERRED sources: Annual Report PDF ({_filing_url}), "
            f"HKEXnews annual reports (年報) at the search link above, "
            f"and HKEX exchange filings. SEC EDGAR is NOT applicable for this HKEX-listed company.\n\n"
        )
    elif _edgar and _accession and not _is_stub:
        _base_context += (
            f"\nSEC EDGAR {'IPO PROSPECTUS' if _is_ipo else 'PRIMARY SOURCE'} — {_co_name}:\n"
            f"  Form {_filing_type} | Accession: {_accession}\n"
            f"  Filed: {_filing_date}"
            + (f" | Period covered: {_period} (FY{_fy})" if not _is_ipo else " (IPO Registration Statement)")
            + f"\n  Filing index: {_url_for_attr}\n"
            f"  EDGAR browser: {_viewer_url}\n"
            f"  → {'IPO prospectus is the primary source. No 20-F/10-K filed yet.' if _is_ipo else 'This is the primary source.'} "
            f"Cite ALL financial statement figures as:\n"
            f"    {_fin_cite_as}\n\n"
        )
    elif _edgar and _is_stub and _viewer_url:
        _base_context += (
            f"\nSEC EDGAR COMPANY (no annual report yet) — {_co_name}:\n"
            f"  CIK: {_edgar.get('cik', '')} | EDGAR page: {_viewer_url}\n"
            f"  No 20-F or 10-K has been filed yet. Use FMP data with FMP attribution.\n\n"
        )

    # ── Inject FMP pre-loaded data ────────────────────────────────────────────
    _fmp_block = ""
    if raw_financials:
        _rows = []
        _sorted_fin_years = sorted(raw_financials.keys())  # oldest → newest
        for _yk in reversed(_sorted_fin_years[-5:]):        # show newest 5, descending
            _yd = raw_financials[_yk]
            if not isinstance(_yd, dict):
                continue
            _rows.append(
                f"  {_yk}: Rev={_fmt_fmp(_yd.get('revenue'))}  "
                f"NI={_fmt_fmp(_yd.get('net_income'))}  "
                f"FCF={_fmt_fmp(_yd.get('free_cash_flow'))}  "
                f"Capex={_fmt_fmp(_yd.get('capital_expenditure'))}  "
                f"NetDebt={_fmt_fmp(_yd.get('net_debt'))}"
            )
        # Pre-compute revenue CAGR to avoid LLM arithmetic errors
        _cagr_note = ""
        _rev_years = [
            y for y in _sorted_fin_years
            if isinstance(raw_financials.get(y), dict)
            and raw_financials[y].get("revenue") and raw_financials[y]["revenue"] > 0
        ]
        if len(_rev_years) >= 2:
            _y0, _y1 = _rev_years[0], _rev_years[-1]
            _r0 = raw_financials[_y0]["revenue"]
            _r1 = raw_financials[_y1]["revenue"]
            # Strip any non-numeric prefix (e.g. "FY2025" → "2025")
            def _to_year_int(y: str) -> int:
                import re
                m = re.search(r'\d{4}', str(y))
                return int(m.group()) if m else 0
            _n = _to_year_int(_y1) - _to_year_int(_y0)
            if _n > 0:
                _cagr_val = (_r1 / _r0) ** (1 / _n) - 1
                _cagr_note = (
                    f"\nPRE-COMPUTED Revenue CAGR ({_y0}–{_y1}, n={_n}yr): "
                    f"{_cagr_val:.1%}  "
                    f"[Formula: ({_fmt_fmp(_r1)} / {_fmt_fmp(_r0)})^(1/{_n}) − 1 = {_cagr_val:.1%}]\n"
                    f"DO NOT recompute this — use the pre-computed figure above to avoid arithmetic errors.\n"
                )
        if _rows:
            _fmp_block += (
                f"PRE-LOADED FINANCIAL DATA {_fin_cite_label}:\n"
                + "\n".join(_rows) + "\n"
                + _cagr_note
            )
    if insider_summary:
        _fmp_block += (
            f"\nPRE-LOADED INSIDER ACTIVITY {_insider_cite_label}:\n"
            + insider_summary + "\n"
        )
    if _fmp_block:
        _base_context += (
            "\nPRE-LOADED FINANCIAL DATA"
            " (already sourced — use the citation label shown above for each figure):\n"
            "INSTRUCTIONS FOR USING THIS DATA:\n"
            "  - USE these numbers as your quantitative foundation. Compute revenue CAGR,\n"
            "    FCF conversion rate (FCF/NI), capex intensity (Capex/Rev), net debt trend,\n"
            f"    and year-on-year growth rates directly from the figures. Cite each figure as:\n"
            f"    {_fin_cite_as}\n"
            "  - DO NOT re-search for revenue, net income, FCF, capex, net debt, or insider\n"
            "    trades — those search slots are wasted. The data is already here.\n"
            "  - Web searches should explain WHAT HAPPENED behind the numbers: why did FCF\n"
            "    diverge from net income? What drove the capex spike? What did management\n"
            "    say about the margin trajectory? What regulatory or competitive event caused\n"
            "    the revenue inflection? Those qualitative answers cannot come from this data.\n"
            + _fmp_block + "\n"
        )

    # Pre-compute REIT sub-type for finer-grained Section 2F prompt selection.
    # For non-REIT tickers this is None and the sector/profile_name routing alone
    # is used. For REITs the sub-type (net_lease, data_center, data_center_premium)
    # drives a specialization layer on top of (sector, profile_name).
    _reit_subtype_for_prompt = None
    if sector in {"RealEstate", "REIT"} or "REIT" in (profile_name or ""):
        try:
            from src.agents.analysis.dcf_agent import _classify_reit_subtype
            from src.data.sector_profiles import TICKER_SECTOR_LOOKUP as _TSL
            from src.data.sector_profiles import SGX_TICKER_SECTOR_LOOKUP as _SGX_TSL
            _lookup = _TSL.get(ticker.upper()) or _SGX_TSL.get(ticker.upper())
            _notes = _lookup[3] if (_lookup and len(_lookup) >= 4) else ""
            _reit_subtype_for_prompt = _classify_reit_subtype(ticker, _notes)
        except Exception:
            _reit_subtype_for_prompt = None

    _research_system = _build_research_system(
        year, sector=sector, profile_name=profile_name, reit_subtype=_reit_subtype_for_prompt,
    )
    # Log which sector-aware 2F block was selected. Visible in Railway logs so
    # operators can verify the (sector, profile_name, reit_subtype) routing
    # fires correctly for each ticker. The KPI block length gives a quick
    # signal for whether a specialized template (longer) or the generic
    # fallback (shorter) was used.
    try:
        from src.agents.industry.sector_prompts import get_kpi_prompt
        _kpi_block = get_kpi_prompt(sector, profile_name, _reit_subtype_for_prompt)
        _kpi_len = len(_kpi_block)
        # Signature detection: look for a distinctive token in the selected block
        _kpi_signature = (
            "biopharma_pipeline"       if "PIPELINE (MANDATORY" in _kpi_block else
            "bank_golden_ratio"         if "GOLDEN RATIO" in _kpi_block else
            "hyperscaler_ai_capex"      if "CLOUD REVENUE CAPTURE" in _kpi_block else
            "growth_saas_ltvcac"        if "LTV/CAC ratio — calculated as" in _kpi_block else
            "mature_saas_postsbc"       if "POST-SBC FCF" in _kpi_block else
            "reit_net_lease"            if "TENANT INDUSTRY" in _kpi_block.upper() else
            "reit_data_center"          if "Capacity mix: MW" in _kpi_block else
            "reit_generic"              if "PORTFOLIO COMPOSITION (MANDATORY" in _kpi_block else
            "asset_manager"             if "FRE vs CARRY" in _kpi_block else
            "insurance"                 if "P/BV and BV/sh growth for P&C" in _kpi_block else
            "payment_networks"          if "cross-border volume" in _kpi_block.lower() else
            "tech_generic"              if "R&D % of revenue" in _kpi_block and "Gross margin" in _kpi_block else
            "generic"
        )
        progress.update_status(
            agent_id, ticker,
            f"Deep research KPI block: {_kpi_signature} "
            f"(sector={sector}, profile={profile_name or '(none)'}, "
            f"reit_sub={_reit_subtype_for_prompt or '(n/a)'}, len={_kpi_len})"
        )
    except Exception:
        pass   # logging only — must not block pipeline

    human_msg = (
        _base_context
        + f"Research {company_display} ({sector} sector) using the web_search tool and produce "
        f"the full Section 2 — Industry Structure report (sub-sections 2A through 2F). "
        f"Focus on information from {int(year)-2}–{year}, with priority on {int(year)-1}–{year}. "
        f"IMPORTANT: This analysis is specifically about {company_display} — not any other "
        f"company that shares a similar ticker symbol. Confirm you are researching the correct "
        f"company in your first search.\n\n"
        f"QUANTITATIVE FOUNDATION (section 2A): The pre-loaded FMP data above gives you "
        f"5 years of revenue, net income, FCF, capex, and net debt. Use those numbers to compute "
        f"revenue CAGR, FCF conversion, capex intensity, and net debt trajectory in 2A — show "
        f"your calculations. Cite each figure as (Financial Data API).\n\n"
        f"WEB SEARCH FOCUS: Use searches to explain the qualitative story the numbers cannot tell — "
        f"management strategy and guidance, competitive dynamics and market share shifts, "
        f"regulatory and policy developments, industry structure and cycle position, "
        f"analyst consensus and price targets, and any material one-off events (write-downs, "
        f"outages, contract wins/losses). Cite all web-sourced claims with source name and date.\n\n"
        f"Use at least 8 searches — start broad on industry structure, then drill into the most "
        f"material findings for each sub-section. After your searches, write the complete "
        f"Section 2 report with all sub-sections 2A through 2F fully populated."
    )
    human_msg_kb = (
        _base_context
        + f"Produce the full Section 2 — Industry Structure report (sub-sections 2A through 2F) "
        f"for {company_display} ({sector} sector). "
        f"IMPORTANT: This analysis is specifically about {company_display} — not any other "
        f"company that shares a similar ticker symbol.\n\n"
        f"QUANTITATIVE FOUNDATION (section 2A): The pre-loaded FMP data above gives you "
        f"5 years of revenue, net income, FCF, capex, and net debt. Use those numbers to compute "
        f"revenue CAGR, FCF conversion, capex intensity, and net debt trajectory in 2A — show "
        f"your calculations. Cite each figure as (Financial Data API).\n\n"
        f"Draw on your training knowledge through early 2025; focus on the {int(year)-2}–{year} "
        f"window where possible. Populate every sub-section with specific figures, trends, and "
        f"named competitors. Do not leave placeholders."
    )

    final_report = ""
    search_count  = 0

    def _run_with_web_search() -> tuple[str, int]:
        """
        Attempt research using Anthropic native web_search_20260209 (GA, no beta header).
        Returns (report_text, search_count, server_citations).

        server_citations — list of dicts extracted from the API's own citation objects
        on each text block.  web_search_20260209 always attaches citations; each one has:
            url, title, cited_text (≤150 chars), encrypted_index (for multi-turn).
        These are server-verified (Anthropic resolved the URL and extracted the snippet)
        so they are more reliable than the secondary LLM extraction pass and will be
        stored with verified=True in the citation_registry.

        web_search_20260209 is a server-side tool — the API handles search
        execution internally.  tool_choice is not passed (it can cause API
        errors with server tools).  The prompt instructs the model to search.

        n_searches may be 0 if the SDK emits unexpected block types; in that
        case the caller logs a warning but still uses the returned text.
        """
        # web_search_20260209 is GA — no anthropic-beta header required.
        # Dynamic filtering active on Sonnet 4.6 / Opus 4.6: filters HTML before
        # loading into context, reducing token use and improving accuracy.
        # tool_choice is intentionally omitted for web_search_20260209.
        # It is a server-side tool — the API schedules and executes searches
        # internally. Passing tool_choice={"type":"any"} can cause API errors
        # or unexpected response shapes depending on the SDK version.
        # The human_msg prompt already instructs the model to search first.
        response = sdk_client.messages.create(
            model=model_name,
            max_tokens=MAX_TOKENS,
            system=_research_system,
            tools=[_WEB_SEARCH_TOOL],
            messages=[{"role": "user", "content": human_msg}],
        )
        # Log stop_reason immediately — "max_tokens" here means the synthesis
        # was cut off; "end_turn" is the healthy completion path.
        _stop = getattr(response, "stop_reason", "unknown")
        if _stop != "end_turn":
            progress.update_status(
                agent_id, ticker,
                f"Tier 1 API stop_reason={_stop!r} — response may be incomplete"
            )
        text = ""
        n_searches = 0
        _seen_urls: set[str] = set()   # deduplicate citations by URL
        server_citations: list[dict] = []

        for block in response.content:
            btype = getattr(block, "type", None)

            # ── Count live searches ───────────────────────────────────────────
            # web_search_20260209 emits "server_tool_use" blocks (not "tool_use").
            # Accept both for resilience across SDK versions.
            if btype in ("server_tool_use", "tool_use") and getattr(block, "name", None) == "web_search":
                n_searches += 1
                query = getattr(block, "input", {}).get("query", "")
                progress.update_status(
                    agent_id, ticker,
                    f"Web search {n_searches}/{MAX_SEARCHES}: {query[:60]}",
                    partial_data={"live_search_query": {
                        "index": n_searches,
                        "total": MAX_SEARCHES,
                        "query": query[:80],
                    }}
                )

            # ── Extract text ──────────────────────────────────────────────────
            if hasattr(block, "text"):
                text += block.text

                # ── Extract server-verified citations from this text block ────
                # Citations are always enabled for web_search_20260209.
                # Each web_search_result_location has: url, title, cited_text,
                # encrypted_index.  cited_text, title, url do NOT count as tokens.
                _new_cits = 0
                for cit in getattr(block, "citations", []) or []:
                    cit_type = getattr(cit, "type", "") or ""
                    if cit_type != "web_search_result_location":
                        continue
                    url        = getattr(cit, "url",        "") or ""
                    title      = getattr(cit, "title",      "") or ""
                    cited_text = getattr(cit, "cited_text", "") or ""
                    if not url or url in _seen_urls:
                        continue
                    _seen_urls.add(url)
                    server_citations.append({
                        "url":        url,
                        "title":      title,
                        "cited_text": cited_text[:150],
                    })
                    _new_cits += 1

                # Stream new sources to frontend as they're discovered
                if _new_cits > 0:
                    progress.update_status(
                        agent_id, ticker,
                        f"Found {len(server_citations)} sources",
                        partial_data={"live_search_sources": [
                            {"url": c["url"], "title": c["title"]}
                            for c in server_citations
                        ]}
                    )

        # NOTE: we do NOT raise here for low/zero n_searches.
        # If the API call completed and returned text, that text is used — even if
        # the server_tool_use blocks weren't detected (e.g. SDK version differences).
        # Raising here previously caused all Tier 1 results to be discarded and
        # re-run through Tavily, wasting tokens. The _is_live flag (set by the
        # caller based on n_searches >= _MIN_LIVE_SEARCHES) handles the labelling.
        if n_searches == 0:
            progress.update_status(
                agent_id, ticker,
                "Tier 1: no web_search blocks detected in response — text may be "
                "training-based (block type/name mismatch); using result anyway"
            )
        text = _strip_narration(text)

        # ── Synthesis nudge ───────────────────────────────────────────────────
        # If searches ran but the model never emitted a text block (max_uses
        # exhausted or max_tokens hit mid-tool-loop), continue the conversation
        # without the search tool to force synthesis from the gathered results.
        # This avoids the 20s retry wait and reuses the already-paid-for searches.
        # If the nudge itself fails (API error or still empty), text stays ""
        # and the caller's ValueError → normal retry/Tier-2 path fires as before.
        if not text.strip() and n_searches > 0:
            progress.update_status(
                agent_id, ticker,
                f"Tier 1: {n_searches} searches completed but no synthesis — "
                "nudging model to write report from gathered results..."
            )
            try:
                nudge_resp = sdk_client.messages.create(
                    model=model_name,
                    max_tokens=MAX_TOKENS,
                    system=_research_system,
                    # No tools: forces text-only output. Passing the prior
                    # assistant turn (which contains server_tool_use and
                    # web_search_tool_result blocks) gives the model full
                    # context of every search result it gathered.
                    messages=[
                        {"role": "user",      "content": human_msg},
                        {"role": "assistant", "content": response.content},
                        {"role": "user",      "content": (
                            "All web searches are complete. Now write the full "
                            "Section 2 research report — all sub-sections 2A "
                            "through 2F — using the search results above. "
                            "Do not perform any additional searches."
                        )},
                    ],
                )
                nudge_text = _strip_narration(
                    "".join(b.text for b in nudge_resp.content if hasattr(b, "text"))
                )
                if nudge_text.strip():
                    # Harvest citations attached to the nudge synthesis
                    for blk in nudge_resp.content:
                        if not hasattr(blk, "text"):
                            continue
                        for cit in getattr(blk, "citations", []) or []:
                            if getattr(cit, "type", "") != "web_search_result_location":
                                continue
                            url = getattr(cit, "url", "") or ""
                            if not url or url in _seen_urls:
                                continue
                            _seen_urls.add(url)
                            server_citations.append({
                                "url":        url,
                                "title":      getattr(cit, "title",      "") or "",
                                "cited_text": (getattr(cit, "cited_text", "") or "")[:150],
                            })
                    text = nudge_text
                    progress.update_status(
                        agent_id, ticker,
                        f"Tier 1: synthesis nudge succeeded ({len(text):,} chars, "
                        f"{len(server_citations)} citations)"
                    )
            except anthropic.BadRequestError as _nudge_err:
                # 400 from the nudge call — the multi-turn payload with
                # server_tool_use blocks in the assistant turn was rejected.
                # Re-raise immediately so the outer except catches it and
                # falls through to Tier 2 without burning a retry cycle.
                progress.update_status(
                    agent_id, ticker,
                    f"Tier 1: synthesis nudge rejected (400 BadRequest) "
                    "— skipping to Tier 2..."
                )
                raise
            except Exception as _nudge_err:
                # Transient nudge failure (timeout, connection reset, etc.) —
                # leave text="" so caller raises ValueError → normal retry path.
                progress.update_status(
                    agent_id, ticker,
                    f"Tier 1: synthesis nudge failed "
                    f"({type(_nudge_err).__name__}: {str(_nudge_err)[:60]}) "
                    "— falling back to retry"
                )

        return text, n_searches, server_citations

    def _run_with_qwen_web_search() -> tuple[str, int, list[dict]]:
        """
        Tier 1 for HK tickers: Qwen native web search via OpenAI-compatible API.

        Qwen handles web search internally — no tool loop required.
        A single chat.completions call with enable_search=True + search_strategy="agent"
        causes the model to run multiple searches before synthesising the report.

        Returns (report_text, n_searches, citations).
        Note: OpenAI-compatible protocol does not return search source URLs,
        so citations is always [] — Tier 2 Tavily citations are richer.
        """
        try:
            from openai import OpenAI as _OpenAI
        except ImportError:
            raise RuntimeError("openai package not installed — cannot use Qwen web search")

        _search_base_url = os.environ.get(
            "DEEP_RESEARCH_SEARCH_BASE_URL",
            "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        )
        _qwen_search_client = _OpenAI(
            api_key=anthropic_key,
            base_url=_search_base_url,
            timeout=CLIENT_TIMEOUT,
        )

        # Qwen handles search automatically — no "use the web_search tool" instruction
        human_msg_qwen = (
            _base_context
            + f"Research {company_display} ({sector} sector) using your web search capability "
            f"and produce the full Section 2 — Industry Structure report "
            f"(sub-sections 2A through 2F). "
            f"Focus on information from {int(year)-2}–{year}, with priority on "
            f"{int(year)-1}–{year}. "
            f"IMPORTANT: This analysis is specifically about {company_display} — not any "
            f"other company that shares a similar ticker symbol. Confirm you are researching "
            f"the correct company before writing.\n\n"
            f"QUANTITATIVE FOUNDATION (section 2A): The pre-loaded financial data above "
            f"gives you 5 years of revenue, net income, FCF, capex, and net debt. Use "
            f"those numbers to compute revenue CAGR, FCF conversion, capex intensity, and "
            f"net debt trajectory in 2A — show your calculations. Cite each figure as "
            f"(Financial Data API).\n\n"
            f"WEB SEARCH FOCUS: Use searches to explain the qualitative story the numbers "
            f"cannot tell — management strategy and guidance, competitive dynamics and "
            f"market share shifts, regulatory and policy developments, industry structure "
            f"and cycle position, analyst consensus and price targets, and any material "
            f"one-off events (write-downs, outages, contract wins/losses). Cite all "
            f"web-sourced claims with source name and date.\n\n"
            f"Search broadly on industry structure first, then drill into the most material "
            f"findings for each sub-section. Write the complete Section 2 report with all "
            f"sub-sections 2A through 2F fully populated."
        )

        progress.update_status(agent_id, ticker, "Tier 1 — Qwen native web search (live)...")

        # Qwen web search requires streaming mode — non-streaming returns 400.
        # qwen3.6-plus is a thinking model by default — it returns
        # reasoning_content naturally during deep research without needing
        # enable_thinking=True. Do NOT combine enable_thinking with
        # enable_search — they are incompatible and cause API errors.
        _stream = _qwen_search_client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": _research_system},
                {"role": "user",   "content": human_msg_qwen},
            ],
            extra_body={
                "enable_search": True,
                "search_options": {"search_strategy": "agent"},
            },
            stream=True,
        )

        import time as _time
        text = ""
        reasoning = ""
        is_answering = False
        _chunk_count = 0
        _last_update = _time.time()

        for _chunk in _stream:
            _delta = getattr(_chunk.choices[0] if _chunk.choices else None, "delta", None)
            if not _delta:
                continue

            now = _time.time()

            # ── Thinking phase (reasoning_content) ───────────────────────
            rc = getattr(_delta, "reasoning_content", None)
            if rc:
                reasoning += rc
                _chunk_count += 1
                # Stream thinking every 20 chunks OR every 15 seconds (SSE keepalive)
                if _chunk_count % 20 == 0 or (now - _last_update) > 15:
                    _snippet = reasoning[-150:].replace("\n", " ").strip()
                    progress.update_status(
                        agent_id, ticker,
                        f"Thinking: {_snippet}",
                        partial_data={"deep_research_thinking": reasoning[-300:]}
                    )
                    _last_update = now

            # ── Response phase (content) ─────────────────────────────────
            c = getattr(_delta, "content", None)
            if c:
                if not is_answering:
                    is_answering = True
                    progress.update_status(
                        agent_id, ticker,
                        f"Writing research report ({len(reasoning):,} chars of reasoning complete)..."
                    )
                    _last_update = now
                text += c
                _chunk_count += 1
                # Stream writing progress every 50 chunks OR every 15 seconds
                if _chunk_count % 50 == 0 or (now - _last_update) > 15:
                    progress.update_status(
                        agent_id, ticker,
                        f"Writing report ({len(text):,} chars so far)..."
                    )
                    _last_update = now

            # ── SSE keepalive: if no update in 30s, send heartbeat ───────
            if (now - _last_update) > 30:
                progress.update_status(
                    agent_id, ticker,
                    f"Deep research in progress ({len(reasoning):,} thinking + {len(text):,} content chars)..."
                )
                _last_update = now

        text = text.strip()

        # ─── Qwen empty-content retry (parallel-load mitigation) ───────────────
        # Observed failure: Qwen's enable_search+stream=True path can emit many
        # reasoning_content chunks but zero content chunks (the final-answer
        # phase never starts), leaving `text=""` while `reasoning` holds the
        # full deliberation. Manifests under concurrent pipeline runs —
        # DashScope's streaming layer drops the content phase silently. The
        # Anthropic tool-use path (line 2656+) has a "synthesis nudge" that
        # recovers; the Qwen path lacked an equivalent until now.
        #
        # Recovery strategy: send the captured reasoning back as an assistant
        # turn, ask for the final Section 2 report, with search disabled and
        # streaming off for deterministic output. Roughly mirrors the
        # Anthropic nudge pattern but adapted to OpenAI-compat chat format.
        if not text and reasoning.strip():
            progress.update_status(
                agent_id, ticker,
                f"⚠ Qwen stream emitted {len(reasoning):,} reasoning chars but 0 content. "
                "Retrying synthesis (non-streaming, no search)..."
            )
            # Small backoff in case a transient DashScope streaming glitch
            # caused the drop — cheap insurance, avoids retry storm.
            import time as _retry_time
            _retry_time.sleep(0.5)
            try:
                safe_reasoning = reasoning[:28_000]  # guard against context overflow
                retry_resp = _qwen_search_client.chat.completions.create(
                    model=model_name,
                    messages=[
                        {"role": "system",    "content": _research_system},
                        {"role": "user",      "content": human_msg_qwen},
                        {"role": "assistant", "content": safe_reasoning},
                        {"role": "user", "content": (
                            "Your reasoning above is excellent. Now write the complete "
                            "Section 2 report — all sub-sections 2A through 2F fully "
                            "populated. Do not perform additional searches. Output "
                            "only the final report content (no meta-commentary)."
                        )},
                    ],
                    extra_body={"enable_search": False},  # prevent recursive search loops
                    stream=False,                          # deterministic fallback
                    temperature=0.3,                       # low variance on retry
                    max_tokens=8192,                       # explicit content-phase budget
                )
                text = (retry_resp.choices[0].message.content or "").strip()
                if text:
                    progress.update_status(
                        agent_id, ticker,
                        f"✓ Qwen synthesis retry succeeded ({len(text):,} content chars)"
                    )
                else:
                    progress.update_status(
                        agent_id, ticker,
                        f"⚠ Qwen retry also returned empty — falling through to Tier 2"
                    )
            except Exception as _retry_err:
                progress.update_status(
                    agent_id, ticker,
                    f"✗ Qwen retry failed: {type(_retry_err).__name__}: "
                    f"{str(_retry_err)[:100]} — falling through to Tier 2"
                )
                logger.warning(
                    "[deep_research qwen retry] %s for ticker=%s: %s",
                    type(_retry_err).__name__, ticker, _retry_err
                )

        if reasoning:
            progress.update_status(
                agent_id, ticker,
                f"Deep research complete: {len(reasoning):,} chars thinking + {len(text):,} chars report"
            )

        # Observability: log prompt + output budget so ops can spot token
        # pressure (per Qwen review recommendation). If system+user exceeds
        # ~12k tokens consistently, consider splitting 2F into a follow-up call.
        logger.info(
            "[deep_research qwen] ticker=%s prompt_system=%d prompt_user=%d "
            "reasoning=%d content=%d",
            ticker, len(_research_system), len(human_msg_qwen),
            len(reasoning), len(text)
        )

        # Search count not available in streaming mode — use proxy of 1
        return text, 1, []   # citations not available from OpenAI-compat streaming

    def _run_knowledge_only() -> str:
        """
        Fallback: produce the Section 2 report from Claude's training knowledge.
        No tool attached — uses human_msg_kb which makes no mention of web search,
        so Claude writes directly from training data rather than apologising about
        a missing tool.  Produces the same 2A–2F structure as the web-search path.
        """
        response = sdk_client.messages.create(
            model=_synthesis_model,
            max_tokens=MAX_TOKENS,
            system=_research_system,
            messages=[{"role": "user", "content": human_msg_kb}],
        )
        return "".join(
            block.text for block in response.content if hasattr(block, "text")
        )

    def _run_with_tavily(tavily_key: str) -> tuple[str, int, list[dict]]:
        """
        Tier 2: agentic Claude + Tavily loop (client-side search execution).
        Returns (report_text, search_count, tavily_citations).

        tavily_citations — list of {title, url, cited_text, date} for every
        Tavily search result consumed during this run.  Added to the seed
        citation registry with verified=True so Phase 7f can trace which web
        pages informed the research.
        """
        msgs: list[dict] = [{"role": "user", "content": human_msg}]
        text = ""
        n_searches = 0
        tavily_citations: list[dict] = []
        while n_searches < MAX_SEARCHES:
            resp = sdk_client.messages.create(
                model=_synthesis_model,
                max_tokens=MAX_TOKENS,
                system=_research_system,
                tools=[_TAVILY_TOOL],
                messages=msgs,
            )
            msgs.append({"role": "assistant", "content": resp.content})
            if resp.stop_reason == "end_turn":
                for blk in resp.content:
                    if hasattr(blk, "text"):
                        text += blk.text
                break
            if resp.stop_reason == "tool_use":
                tool_results = []
                for blk in resp.content:
                    if blk.type == "tool_use":
                        # Guard: stop processing tool calls if cap already reached.
                        # Claude can batch multiple tool_use blocks in one response;
                        # without this check n_searches can exceed MAX_SEARCHES
                        # (observed as "12/10" when 3 calls arrive simultaneously).
                        if n_searches >= MAX_SEARCHES:
                            break
                        query = blk.input.get("query", "")
                        n_searches += 1
                        progress.update_status(
                            agent_id, ticker,
                            f"Tavily search {n_searches}/{MAX_SEARCHES}: {query[:60]}"
                        )
                        result = _search_web(query, tavily_key,
                                             citation_sink=tavily_citations)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": blk.id,
                            "content": result,
                        })
                # Only append tool results if there are any — avoids sending an
                # empty content list which triggers an API validation error when
                # the MAX_SEARCHES cap fires before any tool calls were processed.
                if tool_results:
                    msgs.append({"role": "user", "content": tool_results})
                if n_searches >= MAX_SEARCHES:
                    msgs.append({
                        "role": "user",
                        "content": (
                            "You have reached the search limit. Write the complete "
                            "Section 2 — Industry Structure report now, covering all "
                            "sub-sections 2A through 2F with the data gathered."
                        ),
                    })
                    # CRITICAL: do NOT pass tools here. If the tool schema is still
                    # present the model can respond with tool_use instead of text,
                    # leaving the text accumulator empty → ValueError downstream.
                    # Removing tools forces stop_reason="end_turn" (text output).
                    final = sdk_client.messages.create(
                        model=_synthesis_model,
                        max_tokens=MAX_TOKENS,
                        system=_research_system,
                        messages=msgs,
                    )
                    for blk in final.content:
                        if hasattr(blk, "text"):
                            text += blk.text
                    break
            else:
                for blk in resp.content:
                    if hasattr(blk, "text"):
                        text += blk.text
                break
        return text, n_searches, tavily_citations

    # ── Three-tier fallback chain ──────────────────────────────────────────────
    # US  tickers: Tier 1 (Anthropic web search) → Tier 2 (Tavily) → Tier 3
    # HK  tickers: Tier 1 (Qwen native search)   → Tier 2 (Tavily) → Tier 3
    # Any failure at a tier falls through to the next; full error printed to console.

    tavily_key = os.environ.get("TAVILY_API_KEY")
    research_tier = "none"   # will be overwritten on success
    server_citations: list[dict] = []   # Tier 1 server-verified citations (verified=True)

    # Route Tier 1 by model family:
    #   Claude  → Anthropic web_search_20260209 (server-side tool)
    #   Qwen    → Qwen native web search via OpenAI-compatible API (enable_search=True)
    _use_qwen_search = not model_name.startswith("claude")

    try:
        if _use_qwen_search:
            # ── Tier 1 (HK): Qwen native web search ──────────────────────────
            final_report, search_count, server_citations = _run_with_qwen_web_search()
            if not final_report.strip():
                raise ValueError("Qwen web search returned no text — falling to Tier 2")
            research_tier = "qwen_web"
            progress.update_status(
                agent_id, ticker,
                f"Tier 1 complete: Qwen web search "
                f"({search_count} search(es), {len(final_report):,} chars)"
            )
        else:
            # ── Tier 1 (US): Anthropic native web search (LIVE — no training cutoff).
            # Application-level retry loop for TRANSIENT NETWORK FAULTS ONLY.
            #
            # Only APIConnectionError (TCP reset, DNS blip, SSL drop) and APITimeoutError
            # are retried — these are the only errors that are likely to self-resolve.
            #
            # Everything else (AttributeError from SDK/Pydantic parsing, AuthenticationError,
            # BadRequestError, ValueError, etc.) escapes the inner try immediately and is
            # caught by the outer except below, falling to Tier 2 with no wasted wait.
            _t1_attempt  = 0
            _t1_last_err = None
            _t1_success  = False

            while _t1_attempt <= _T1_MAX_RETRIES:
                try:
                    progress.update_status(
                        agent_id, ticker,
                        "Tier 1 — Anthropic web search (live)..."
                        if _t1_attempt == 0
                        else f"Tier 1 — retry {_t1_attempt}/{_T1_MAX_RETRIES} (Anthropic web search)..."
                    )
                    final_report, search_count, server_citations = _run_with_web_search()
                    if not final_report.strip():
                        raise ValueError("Anthropic web search returned no text")
                    research_tier = "anthropic_web"
                    progress.update_status(
                        agent_id, ticker,
                        f"Tier 1 complete — anthropic_web ({search_count} searches, LIVE | "
                        f"{len(server_citations)} server citations)"
                    )
                    _t1_success = True
                    break  # success — exit retry loop

                except (anthropic.APIConnectionError, anthropic.APITimeoutError, ValueError) as _attempt_err:
                    # Retry transient network faults AND empty-text responses (ValueError).
                    # Empty-text can occur when stop_reason != "end_turn" (e.g. max_tokens
                    # exhausted before synthesis, or server tool loop didn't emit a text block).
                    # All other exceptions (SDK parsing errors, auth, bad request, etc.)
                    # propagate out of the while loop to the outer except → Tier 2.
                    _t1_last_err = _attempt_err
                    _t1_attempt += 1
                    if _t1_attempt <= _T1_MAX_RETRIES:
                        print(
                            f"\n  [deep_research] Tier 1 attempt {_t1_attempt} failed for "
                            f"{ticker}: {type(_attempt_err).__name__}: {_attempt_err} "
                            f"— retrying in {_T1_RETRY_WAIT:.0f}s"
                            + (" (empty synthesis — likely max_tokens or stop_reason!=end_turn)"
                               if isinstance(_attempt_err, ValueError) else "")
                            + "..."
                        )
                        progress.update_status(
                            agent_id, ticker,
                            f"Tier 1 attempt {_t1_attempt} failed "
                            f"({type(_attempt_err).__name__}: {str(_attempt_err)[:80]}) "
                            f"— retrying in {_T1_RETRY_WAIT:.0f}s..."
                        )
                        time.sleep(_T1_RETRY_WAIT)

            if not _t1_success and _t1_last_err is not None:
                # Network retries exhausted — propagate to Tier 2/3 handler below
                raise _t1_last_err  # type: ignore[misc]

    except Exception as t1_err:
        print(f"\n  [deep_research] Tier 1 failed for {ticker}: {type(t1_err).__name__}: {t1_err}")
        progress.update_status(
            agent_id, ticker,
            f"Tier 1 failed ({type(t1_err).__name__}: {str(t1_err)[:120]})"
            + (" — trying Tier 2 Tavily..." if tavily_key else " — falling to Tier 3 knowledge-only...")
        )
        if tavily_key:
            try:
                # Tier 2: Tavily agentic loop (LIVE — no training cutoff)
                progress.update_status(agent_id, ticker, "Tier 2 — Tavily search loop (live)...")
                final_report, search_count, _tavily_cits = _run_with_tavily(tavily_key)
                if not final_report.strip():
                    raise ValueError("Tavily loop returned no text")
                research_tier = "tavily"
                # Merge Tavily source URLs into server_citations so they appear in the
                # seed citation registry with verified=True (consumed during research).
                _seen_tavily = {e["url"] for e in server_citations if e.get("url")}
                for _tc in _tavily_cits:
                    if _tc.get("url") and _tc["url"] not in _seen_tavily:
                        server_citations.append(_tc)
                        _seen_tavily.add(_tc["url"])
                progress.update_status(
                    agent_id, ticker,
                    f"Tier 2 complete — tavily ({search_count} searches, LIVE, "
                    f"{len(_tavily_cits)} source URLs captured)"
                )

            except Exception as t2_err:
                print(f"\n  [deep_research] Tier 2 failed for {ticker}: {type(t2_err).__name__}: {t2_err}")
                progress.update_status(
                    agent_id, ticker,
                    f"Tier 2 failed ({type(t2_err).__name__}: {str(t2_err)[:100]}) — falling to Tier 3 knowledge-only..."
                )
                try:
                    # Tier 3: knowledge-only (training cutoff ~early 2025)
                    final_report = _run_knowledge_only()
                    research_tier = "knowledge_only"
                    search_count = 0
                except Exception as kb_err:
                    progress.update_status(agent_id, ticker, f"All tiers failed ({kb_err}) — skipping deep research")
                    return {
                        "deep_research": "", "deep_research_sections": {},
                        "research_tier": "none", "citation_registry": [],
                        "web_intelligence": {}, "cache_hit": False,
                        "cache_age_days": None, "cache_run_id": None,
                    }
        else:
            try:
                # Tier 3: knowledge-only (no Tavily key configured)
                final_report = _run_knowledge_only()
                research_tier = "knowledge_only"
                search_count = 0
            except Exception as kb_err:
                progress.update_status(agent_id, ticker, f"All tiers failed ({kb_err}) — skipping deep research")
                return {
                    "deep_research": "", "deep_research_sections": {},
                    "research_tier": "none", "citation_registry": [],
                    "web_intelligence": {}, "cache_hit": False,
                    "cache_age_days": None, "cache_run_id": None,
                }

    _is_live = research_tier in ("anthropic_web", "tavily", "qwen_web") and search_count >= _MIN_LIVE_SEARCHES
    progress.update_status(
        agent_id, ticker,
        f"Deep research complete — tier={research_tier} | {search_count} web searches | "
        f"{sum(1 for l in final_report.splitlines() if l.strip())} non-empty lines"
        + (" [LIVE WEB DATA]" if _is_live else " [TRAINING DATA — no live searches]")
    )

    sections = _extract_sections(final_report)

    # Trace silent parser failures so future regressions are visible. The
    # widened regex above tolerates most LLM variants, but if a model emits
    # a wholly non-canonical format (e.g. "Section 2A" spelled out) the parser
    # drops to {"full": text} and downstream consumers (scenario_agent,
    # power_law_agent, investor agents) never see per-section data.
    if (not sections or set(sections.keys()) == {"full"}) and final_report.strip():
        logger.info(
            "[deep_research] Section parser matched NO 2A-2F headers for %s — "
            "LLM output may use a non-canonical header format. First 200 chars: %s",
            ticker, final_report[:200].replace(chr(10), ' | '),
        )

    # ── Citation Registry extraction ──────────────────────────────────────────
    progress.update_status(agent_id, ticker, "Extracting citation registry from report...")

    _server_urls: set[str] = {e["url"] for e in server_citations if e.get("url")}
    seed_registry: list[dict] = [
        {
            "ref_id":       i + 1,
            "claim":        (e.get("cited_text") or e.get("title") or "")[:120],
            "source_name":  e.get("title") or "",
            "source_type":  "web_search",
            "date":         "",
            "speaker":      "",
            "quote":        (e.get("cited_text") or "")[:200],
            "url":          e.get("url") or "",
            "section":      "unknown",
            "verified":     True,
        }
        for i, e in enumerate(server_citations)
        if e.get("url")
    ]

    llm_registry = _extract_citation_registry(
        sdk_client, _synthesis_model, final_report, ticker,
        edgar_filing_ref=edgar_filing_ref,
    )
    ref_offset = len(seed_registry)
    llm_deduped: list[dict] = []
    for entry in llm_registry:
        if entry.get("url") and entry["url"] in _server_urls:
            continue
        entry["ref_id"] = ref_offset + len(llm_deduped) + 1
        llm_deduped.append(entry)

    citation_registry = seed_registry + llm_deduped
    n_verified = sum(1 for e in citation_registry if e.get("verified"))

    # ── Annotate report text with inline [n] citation markers ─────────────────
    # Match cited_text (server-verified exact quotes) against the report text and
    # insert [ref_id] immediately after each match.  Longest matches first to
    # prevent a short phrase from matching inside a longer quote.
    annotated_report = final_report
    _used_ids: set = set()
    _sortable = sorted(
        (e for e in citation_registry if e.get("quote") and len(e["quote"]) > 20),
        key=lambda e: len(e.get("quote", "")),
        reverse=True,
    )
    for entry in _sortable:
        ref_n = entry["ref_id"]
        if ref_n in _used_ids:
            continue
        search_phrase = entry["quote"][:120].strip()  # Use first 120 chars of quote
        idx = annotated_report.find(search_phrase)
        if idx >= 0:
            insert_pos = idx + len(search_phrase)
            annotated_report = (
                annotated_report[:insert_pos]
                + f"[{ref_n}]"
                + annotated_report[insert_pos:]
            )
            _used_ids.add(ref_n)

    progress.update_status(
        agent_id, ticker,
        f"Citation registry: {len(citation_registry)} entries | {n_verified} verified "
        f"({len(seed_registry)} server-verified + {len(llm_deduped)} LLM-extracted) | "
        f"{len(_used_ids)} inline markers inserted"
    )

    # ── Parallel extractor fan-out ───────────────────────────────────────────
    # Five independent LLM passes over the finished report + sections (DCF
    # calibration, segment scenarios, pipeline assets, REIT metrics, bank
    # metrics). Each makes a single sdk_client.messages.create call — no
    # shared state, no ordering dependency. Run in parallel to cut extractor
    # latency from ~5 × 20-30s sequential (75-150s) down to ~30s wall time.
    #
    # Previously these ran sequentially and pushed JPM / bank deep-research
    # past the 720s client timeout on Qwen — manifested as UI stuck on
    # "deep research" for minutes after Qwen had actually finished synthesis.
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from src.agents.industry.sector_prompts import needs_extractor

    # Sector-aware extractor gating — skips extractors that would almost
    # certainly return {} for the given (sector, profile_name). Saves
    # ~800 tokens × skipped extractor. A tech ticker runs 3 extractors
    # (dcf_calibration, segment_scenarios, saas_metrics) instead of 6.
    _all_extractors: dict[str, callable] = {
        "dcf_calibration":   lambda: _extract_dcf_calibration(sdk_client, _synthesis_model, sections, ticker),
        "segment_scenarios": lambda: _extract_segment_scenarios(sdk_client, _synthesis_model, sections, final_report, ticker),
        "pipeline_assets":   lambda: _extract_pipeline_assets(sdk_client, _synthesis_model, sections, final_report, ticker),
        "reit_metrics":      lambda: _extract_reit_metrics(sdk_client, _synthesis_model, sections, final_report, ticker),
        "bank_metrics":      lambda: _extract_bank_metrics(sdk_client, _synthesis_model, sections, final_report, ticker),
        "saas_metrics":      lambda: _extract_saas_metrics(sdk_client, _synthesis_model, sections, final_report, ticker),
    }
    _extractor_tasks = {
        name: fn for name, fn in _all_extractors.items()
        if needs_extractor(name, sector, profile_name, ticker=ticker)
    }
    # Railway stdout visibility: which extractors will fire for this run.
    # Gated extractors (e.g. saas_metrics skipped for Bank tickers) are
    # listed so user can spot unexpectedly missing extractions.
    _skipped = [n for n in _all_extractors if n not in _extractor_tasks]
    print(
        f"  Extractors fan-out ({ticker} · sector={sector!r} · "
        f"profile={profile_name!r}): running {sorted(_extractor_tasks)} | "
        f"skipped {sorted(_skipped)}"
    )

    _results: dict = {}
    with ThreadPoolExecutor(max_workers=6) as _ex:
        _futures = {_ex.submit(fn): name for name, fn in _extractor_tasks.items()}
        for _fut in as_completed(_futures):
            _name = _futures[_fut]
            try:
                _out = _fut.result()
                _results[_name] = _out
                # Per-extractor result summary — visible in Railway stdout so
                # user can see which extractors returned data vs empty.
                if isinstance(_out, list):
                    _summary = f"list[{len(_out)}]"
                elif isinstance(_out, dict):
                    _populated = {k: v for k, v in _out.items() if v not in (None, "", [], {})}
                    _summary = (
                        f"{len(_populated)}/{len(_out)} fields populated: "
                        f"{sorted(_populated)}" if _populated
                        else f"EMPTY dict ({len(_out)} null fields)"
                    )
                else:
                    _summary = f"{type(_out).__name__}"
                _status_icon = "✓" if _out else "⚠ EMPTY"
                print(f"  Extractor [{_name}] {_status_icon} → {_summary}")
            except Exception as _exc:
                progress.update_status(agent_id, ticker,
                                       f"Extractor {_name} failed: {_exc}")
                _results[_name] = {} if _name != "pipeline_assets" else []
                # Traceback to stdout so the real error is visible, not just
                # the "failed: X" summary.
                print(f"  Extractor [{_name}] ✗ FAILED: {type(_exc).__name__}: {_exc}")
                import traceback as _tb
                _tb.print_exc()

    dcf_calibration   = _results.get("dcf_calibration", {})
    segment_scenarios = _results.get("segment_scenarios", {})
    pipeline_assets   = _results.get("pipeline_assets", [])
    reit_metrics      = _results.get("reit_metrics", {})
    bank_metrics      = _results.get("bank_metrics", {})
    saas_metrics      = _results.get("saas_metrics", {})

    # Apply FMP self-compute fallback to fill null SaaS metric fields from
    # raw financials (Rule of 40, Magic Number, CAC Payback, Billings Growth).
    # LLM-extracted values are preserved; only nulls get filled. Source-level
    # write so downstream consumers see the enriched dict in the returned state.
    _saas_before_fallback = dict(saas_metrics) if isinstance(saas_metrics, dict) else {}
    saas_metrics = _compute_saas_metrics_fallback(raw_financials, saas_metrics)
    # Show which fields the FMP fallback filled (vs what the LLM extracted)
    if isinstance(saas_metrics, dict):
        _llm_fields = {k for k, v in _saas_before_fallback.items() if v not in (None, "", [], {})}
        _final_fields = {k for k, v in saas_metrics.items() if v not in (None, "", [], {})}
        _filled_by_fallback = sorted(_final_fields - _llm_fields)
        print(
            f"  SaaS metrics ({ticker}): LLM={sorted(_llm_fields)} | "
            f"FMP-fallback added={_filled_by_fallback} | "
            f"final={sorted(_final_fields)}"
        )

    progress.update_status(
        agent_id, ticker,
        f"DCF calibration: growth_adj={dcf_calibration.get('growth_rate_adj')}, "
        f"margin={dcf_calibration.get('margin_direction')}, "
        f"risk={dcf_calibration.get('risk_flag')}"
    )
    if segment_scenarios:
        progress.update_status(
            agent_id, ticker,
            f"Segment scenarios: {len(segment_scenarios)} segments"
        )
    if pipeline_assets:
        progress.update_status(
            agent_id, ticker,
            f"Pipeline assets: {len(pipeline_assets)} extracted for rNPV"
        )
    if reit_metrics:
        progress.update_status(
            agent_id, ticker,
            f"REIT metrics: {sorted(reit_metrics.keys())}"
        )
    if bank_metrics:
        progress.update_status(
            agent_id, ticker,
            f"Bank metrics: {sorted(bank_metrics.keys())}"
        )
    if saas_metrics:
        progress.update_status(
            agent_id, ticker,
            f"SaaS metrics: {sorted(saas_metrics.keys())}"
        )

    # ── Pipeline summary (stdout, visible in Railway logs) ────────────────────
    # One-shot overview so ops can grep a single line to see whether the
    # research pipeline succeeded end-to-end for each ticker.
    _sec_keys = sorted(sections.keys()) if isinstance(sections, dict) else []
    _report_len = len(final_report) if final_report else 0
    _status = "✓ OK" if _report_len > 1000 and _sec_keys else (
        "⚠ EMPTY report" if _report_len == 0 else
        "⚠ sections missing" if not _sec_keys else "⚠ short"
    )
    print(
        f"  Research summary ({ticker}): {_status} | "
        f"tier={research_tier!r} | report={_report_len:,} chars | "
        f"sections={_sec_keys} | citations={len(citation_registry)}"
    )

    # Per-section length breakdown + 2F preview — helps diagnose commentary
    # card failures on the frontend. If sections["2f"] is short or malformed,
    # the ResearchNarrativeCard subsection extraction (2F.N regex) or the
    # full-section fallback (min 80 chars) can both hide silently.
    if isinstance(sections, dict):
        _sec_lengths = {k: len(v) if isinstance(v, str) else 0 for k, v in sections.items()}
        print(f"  Section lengths ({ticker}): {_sec_lengths}")
        _s2f = sections.get("2f") or sections.get("2F") or ""
        if _s2f:
            # Preview: first 400 chars + any subsection headings found
            import re as _re_diag
            _subs = _re_diag.findall(r"(?:^|\n)\s*(?:\*{0,2})(2?[A-F]\.\d+)", _s2f, _re_diag.IGNORECASE)
            _subs_unique = sorted(set(s.upper() for s in _subs))
            _preview = _s2f[:400].replace("\n", " ⏎ ")
            print(
                f"  Section 2F preview ({ticker}, {len(_s2f)} chars, "
                f"subsections={_subs_unique}): {_preview}..."
            )
        else:
            print(f"  Section 2F MISSING ({ticker}) — no '2f' or '2F' key in sections dict")

    return {
        "deep_research":            final_report,
        "deep_research_annotated":  annotated_report,
        "deep_research_sections":   sections,
        "research_tier":          research_tier,
        "citation_registry":      citation_registry,
        "web_intelligence":       {},
        "cache_hit":              False,
        "cache_age_days":         None,
        "cache_run_id":           None,
        "dcf_calibration":        dcf_calibration,
        "segment_scenarios":      segment_scenarios,
        "pipeline_assets":        pipeline_assets,
        "reit_metrics":           reit_metrics,
        "bank_metrics":           bank_metrics,
        "saas_metrics":           saas_metrics,
    }


# ── Public entry point ────────────────────────────────────────────────────────

def run_deep_research_agent(state: AgentState) -> AgentState:
    """
    Phase 3.5: Deep research for ALL tickers in the query, run in parallel.

    Single ticker  → _research_one_ticker called directly (no thread overhead).
    Multiple tickers → ThreadPoolExecutor, max 4 concurrent workers.

    Reads:
        state["data"]["tickers"]        — full ticker list
        state["data"]["sectors"]        — per-ticker sector map (built by strategic_router)
        state["data"]["sector"]         — primary ticker sector (fallback)
        state["data"]["primary_ticker"] — primary ticker
        state["data"]["raw_financials"] — FMP pre-load for primary ticker
        state["data"]["insider_summary"]— insider text for primary ticker

    Writes:
        state["data"]["deep_research_map"]       — {ticker: result_dict} for all tickers
        state["data"]["deep_research"]           — primary ticker full report (backward-compat)
        state["data"]["deep_research_sections"]  — primary ticker sections (backward-compat)
        state["data"]["research_tier"]           — primary ticker tier (backward-compat)
        state["data"]["citation_registry"]       — primary ticker citations (backward-compat)
        state["data"]["web_intelligence"]        — {} (specialist uses deep_research)
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    agent_id = "deep_research"

    # US tickers → Anthropic (claude-sonnet-4-6, no base_url override)
    # HK tickers → Qwen3-max via Alibaba Cloud (DEEP_RESEARCH_* env vars)
    _us_key: str | None = os.environ.get("ANTHROPIC_API_KEY")
    _hk_key: str | None = os.environ.get("DEEP_RESEARCH_API_KEY") or _us_key
    _hk_base_url: str | None = os.environ.get("DEEP_RESEARCH_BASE_URL") or None
    _hk_model: str | None = os.environ.get("DEEP_RESEARCH_MODEL") or None

    # Require at least the US key to proceed
    anthropic_key = _us_key or _hk_key
    if not anthropic_key:
        progress.update_status(agent_id, "all", "ANTHROPIC_API_KEY not set — skipping deep research")
        state["data"]["deep_research"]          = ""
        state["data"]["deep_research_sections"] = {}
        state["data"]["deep_research_map"]      = {}
        return state

    tickers        = state["data"]["tickers"]
    primary_ticker = state["data"].get("primary_ticker", tickers[0])
    primary_sector = state["data"].get("sector", "Tech")
    sectors_map    = state["data"].get("sectors", {})
    end_date       = state["data"]["end_date"]

    # Default model for US tickers.
    # Priority: DEEP_RESEARCH_MODEL env var (set by analysis_service when user
    # selects Qwen — structured pipeline runs on Claude but deep research should
    # still use Qwen for superior web search) → pipeline model → claude-sonnet-4-6.
    _us_model: str = (
        os.environ.get("DEEP_RESEARCH_MODEL")
        or state["metadata"].get("model_name")
        or "claude-sonnet-4-6"
    )

    # FMP data is fetched by strategic_router for the primary ticker only.
    # Secondary tickers run without pre-loaded financials (web searches pick up the gap).
    primary_raw_fin    = state["data"].get("raw_financials") or {}
    primary_insider    = state["data"].get("insider_summary") or ""
    edgar_refs_map     = state["data"].get("edgar_filing_refs") or {}
    news_sentiment_map = state["data"].get("news_sentiment") or {}

    profile_names_map = state["data"].get("profile_names", {}) or {}

    def _task(t: str) -> tuple[str, dict]:
        from src.tools.hk.ticker import is_hk_ticker
        _sector    = sectors_map.get(t, primary_sector)
        _profile   = profile_names_map.get(t, "")
        _raw_fin   = primary_raw_fin if t == primary_ticker else {}
        _insider   = primary_insider if t == primary_ticker else ""
        _edgar_ref = edgar_refs_map.get(t) or {}
        _ns_data   = news_sentiment_map.get(t) or {}
        # Route: HK → Qwen (web search) + qwen3-max (synthesis), US → Anthropic
        if is_hk_ticker(t):
            _key              = _hk_key
            _base_url         = _hk_base_url
            _model            = _hk_model or "qwen3.6-plus"   # OpenAI-compat web search
            _synthesis_model  = os.environ.get("DEEP_RESEARCH_SYNTHESIS_MODEL") or "qwen3-max"  # Anthropic-compat synthesis
        else:
            # When user selects a Qwen model for US tickers, use the DashScope
            # key and base URL (same as HK path) instead of the Anthropic key.
            if _us_model.startswith("qwen"):
                _key              = _hk_key
                _base_url         = _hk_base_url
                _model            = _us_model
                _synthesis_model  = os.environ.get("DEEP_RESEARCH_SYNTHESIS_MODEL") or "qwen3-max"
            else:
                _key              = _us_key or anthropic_key
                _base_url         = None
                _model            = _us_model
                _synthesis_model  = None   # same as _model for US
        result = _research_one_ticker(
            ticker=t,
            sector=_sector,
            profile_name=_profile,
            end_date=end_date,
            anthropic_key=_key,
            model_name=_model,
            raw_financials=_raw_fin,
            insider_summary=_insider,
            edgar_filing_ref=_edgar_ref,
            news_sentiment_data=_ns_data,
            base_url=_base_url,
            synthesis_model=_synthesis_model,
        )
        return t, result

    deep_research_map: dict[str, dict] = {}

    if len(tickers) == 1:
        t, result = _task(tickers[0])
        deep_research_map[t] = result
    else:
        max_workers = min(len(tickers), 4)
        progress.update_status(
            agent_id, primary_ticker,
            f"Launching parallel deep research for {len(tickers)} tickers "
            f"(max {max_workers} concurrent)..."
        )
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_task, t): t for t in tickers}
            for fut in as_completed(futures):
                try:
                    t, result = fut.result()
                    deep_research_map[t] = result
                except Exception as exc:
                    t = futures[fut]
                    progress.update_status(agent_id, t, f"Research worker failed: {exc}")
                    deep_research_map[t] = {
                        "deep_research": "", "deep_research_sections": {},
                        "research_tier": "none", "citation_registry": [],
                        "web_intelligence": {}, "cache_hit": False,
                        "cache_age_days": None, "cache_run_id": None,
                    }

    state["data"]["deep_research_map"] = deep_research_map

    # ── Per-ticker DCF calibration signals ────────────────────────────────────
    dcf_calibration_signals: dict[str, dict] = {}
    for t, res in deep_research_map.items():
        cal = res.get("dcf_calibration")
        if cal:
            dcf_calibration_signals[t] = cal
    state["data"]["dcf_calibration_signals"] = dcf_calibration_signals

    # ── Per-ticker segment scenarios (for probabilistic SOTP 12m) ─────────────
    segment_scenarios_all: dict[str, dict] = {}
    for t, res in deep_research_map.items():
        scen = res.get("segment_scenarios")
        if scen:
            segment_scenarios_all[t] = scen
    state["data"]["segment_scenarios"] = segment_scenarios_all

    # ── Per-ticker pipeline assets (for Biopharma rNPV method) ───────────────
    pipeline_assets_all: dict[str, list[dict]] = {}
    for t, res in deep_research_map.items():
        assets = res.get("pipeline_assets")
        if assets:
            pipeline_assets_all[t] = assets
    state["data"]["pipeline_assets"] = pipeline_assets_all

    # ── Per-ticker REIT metrics (cap rate override + DPU coverage) ───────────
    reit_metrics_all: dict[str, dict] = {}
    for t, res in deep_research_map.items():
        rm = res.get("reit_metrics")
        if rm:
            reit_metrics_all[t] = rm
    state["data"]["reit_metrics"] = reit_metrics_all

    # ── Per-ticker bank metrics (CET1 + target ROE overrides) ────────────────
    bank_metrics_all: dict[str, dict] = {}
    for t, res in deep_research_map.items():
        bm = res.get("bank_metrics")
        if bm:
            bank_metrics_all[t] = bm
    state["data"]["bank_metrics"] = bank_metrics_all

    # ── Per-ticker SaaS metrics (NRR + Rule of 40 + CAC payback) ─────────────
    saas_metrics_all: dict[str, dict] = {}
    for t, res in deep_research_map.items():
        sm = res.get("saas_metrics")
        if sm:
            saas_metrics_all[t] = sm
    state["data"]["saas_metrics"] = saas_metrics_all

    # ── Per-ticker management guidance extraction ─────────────────────────────
    mgmt_guidance: dict[str, dict] = {}
    for t, res in deep_research_map.items():
        _report = res.get("deep_research", "")
        if _report:
            mgmt_guidance[t] = _extract_management_guidance(_report)
    state["data"]["management_guidance"] = mgmt_guidance

    # ── Backward-compatible flat keys from primary ticker ─────────────────────
    primary = deep_research_map.get(primary_ticker) or deep_research_map.get(tickers[0]) or {}
    state["data"]["deep_research"]            = primary.get("deep_research", "")
    state["data"]["deep_research_annotated"]  = primary.get("deep_research_annotated", "")
    state["data"]["deep_research_sections"]   = primary.get("deep_research_sections", {})
    state["data"]["research_tier"]            = primary.get("research_tier", "none")
    state["data"]["citation_registry"]        = primary.get("citation_registry", [])
    state["data"]["web_intelligence"]       = {}
    if primary.get("cache_hit"):
        state["data"]["research_cache_hit"]      = True
        state["data"]["research_cache_age_days"] = primary.get("cache_age_days")
        state["data"]["research_cache_run_id"]   = primary.get("cache_run_id")

    return state
