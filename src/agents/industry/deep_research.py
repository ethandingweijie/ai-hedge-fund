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
import os
import re
import time
from datetime import datetime

import anthropic

from src.graph.state import AgentState
from src.utils.progress import progress
from src.utils.company_name import fetch_company_name as _fetch_company_name


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
    boundary = re.compile(
        r"(?:^|\n)[ \t#─]*\b(2[A-F])[\.\s]", re.IGNORECASE
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
        resp = sdk_client.messages.create(
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
        raw = "".join(b.text for b in resp.content if hasattr(b, "text")).strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)
        parsed = json.loads(raw)
        return {
            "growth_rate_adj":  parsed.get("growth_rate_adj"),
            "margin_direction": parsed.get("margin_direction", "stable"),
            "risk_flag":        parsed.get("risk_flag", "MEDIUM"),
            "notes":            parsed.get("notes", ""),
        }
    except Exception as exc:
        return {"growth_rate_adj": None, "margin_direction": "stable",
                "risk_flag": "MEDIUM", "notes": f"Extraction failed: {exc}"}


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

def _build_research_system(year: str) -> str:
    """Return the deep research system prompt with dynamic year references.

    year — 4-digit string, e.g. "2026".  Computes ym1 (year-1) and y1 (year+1)
    for the search sequence so all queries target the correct calendar window.
    The search sequence is pre-notified about FMP pre-loaded data so the LLM
    does not waste search slots on revenue/insider data already in state.
    """
    ym1 = str(int(year) - 1)   # e.g. "2025" when year="2026"
    y1  = str(int(year) + 1)   # e.g. "2027" when year="2026"
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

──────────────────────────────────────────
2F. INDUSTRY-SPECIFIC KPI FRAMEWORK
──────────────────────────────────────────
Purpose: Every industry has 3–5 metrics that actually predict forward
performance. Generic financial metrics miss what matters.

2F.1 The anchor KPI — the single number that best predicts this company's
revenue 12 months forward (e.g. Bookings / Backlog / GMV / AUM / MWh
contracted / NRR / ARR / same-store-sales / pipeline $).
Trend over last 6 quarters. Consensus expectation vs your expectation.
Why they differ.

2F.2 The leading indicator — the metric that predicts the anchor KPI
2–3 quarters in advance (e.g. web traffic, trial signups, pilot contract
count, permit filings, IEA demand data). Current reading and implication.

2F.3 The margin indicator — the metric that best predicts EBITDA margin
12 months forward (e.g. mix shift %, utilisation rate, headcount per $M
revenue, gross retention, take rate, MLR, hedge ratio).

2F.4 The risk indicator — the early warning signal for competitive
deterioration (e.g. NRR below 100%, churn acceleration, win rate vs key
competitor, DSO expanding, guidance cuts). Current reading and threshold
that would trigger re-evaluation.

2F.5 Industry data sources — the 3–5 best external data sources specific
to this industry (not just company filings): government/regulatory data,
industry association data, third-party trackers, supply chain / alternative
data.

2F.6 Management Guidance & Forward Estimates — CRITICAL for downstream DCF.
Extract the most recent quantitative forward guidance from earnings calls,
investor presentations, or press releases. Report as exact dollar figures:
  • FY revenue guidance: $XX.XB – $XX.XB (midpoint $XX.XB)
  • FY EBITDA guidance: $XX.XB – $XX.XB (midpoint $XX.XB)
  • Capex guidance: $XX.XB
  • Margin targets: XX% – XX%
  • Any one-time items excluded from guidance (M&A, restructuring, etc.)
If no explicit guidance is available, state "No quantitative guidance found"
and note the date of the most recent earnings call.

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
        ).strip()

        # ── Robust JSON array extraction ──────────────────────────────────────
        # The LLM may wrap output in markdown code fences (```json\n...\n```).
        # Strip them first, then locate the outermost [ ... ] array.
        # Use rfind("]") so we get the closing bracket of the full array even
        # if nested objects contain their own brackets.
        _clean = re.sub(r"```(?:json)?\s*", "", text).strip().rstrip("`").strip()
        start = _clean.find("[")
        end   = _clean.rfind("]") + 1
        if start < 0 or end <= start:
            print(f"  [citation_registry] No JSON array found for {ticker} — LLM response: {_clean[:200]}")
            return []

        raw: list = json.loads(_clean[start:end])
        if not isinstance(raw, list):
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

    _research_system = _build_research_system(year)

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

        if reasoning:
            progress.update_status(
                agent_id, ticker,
                f"Deep research complete: {len(reasoning):,} chars thinking + {len(text):,} chars report"
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

    # ── DCF calibration signals from 2D + 2F ─────────────────────────────────
    dcf_calibration = _extract_dcf_calibration(sdk_client, _synthesis_model, sections, ticker)
    progress.update_status(
        agent_id, ticker,
        f"DCF calibration: growth_adj={dcf_calibration.get('growth_rate_adj')}, "
        f"margin={dcf_calibration.get('margin_direction')}, "
        f"risk={dcf_calibration.get('risk_flag')}"
    )

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

    def _task(t: str) -> tuple[str, dict]:
        from src.tools.hk.ticker import is_hk_ticker
        _sector    = sectors_map.get(t, primary_sector)
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
