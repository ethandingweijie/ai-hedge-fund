"""
src/research_ideas/complacency/qualitative.py
==============================================
LLM-based qualitative scoring layer for the Complacency screener.

For tickers flagged Strong-Short or Watch, the agent scores 10 qualitative
indicators (themes A/B/C/D from the spec) on a 0-5 rubric and cites
verbatim evidence from filings / transcripts / news.

  Model:    qwen3.6-max (Alibaba DashScope) — set via DEEP_RESEARCH_API_KEY
  Cache:    7-day TTL per (ticker, indicator)
  Sources:  10-K risk factors, earnings transcripts, FMP news (90-day window)
  Trust:    Single high-quality source (10-K, transcript) is sufficient;
            otherwise require 2 independent sources.

v1 — 3 indicators implemented end-to-end:
  A2  Catalyst proximity
  C2  Accounting aggressiveness
  D1  Management quality red flags

v2 — extends to remaining 7 (A1, A3, B1, B2, B3, C1, D2).
"""
from __future__ import annotations

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from pydantic import BaseModel, Field

from src.llm.models import ModelProvider, get_model
from src.tools.api import _fmp_get, _safe_float, _STABLE
from src.research_ideas.complacency.evidence_sources import (
    fetch_sec_10k_sections,
    fetch_sec_recent_8k, format_8k_filings_for_prompt,
    fetch_sec_def14a_excerpt,
    fetch_sec_recent_form144, format_form144_for_prompt,
    fetch_sec_10q_diff, format_10q_diff_for_prompt,
    fetch_price_target_consensus,
    fetch_price_target_summary,
    tavily_search,
    fetch_stock_news,
    fetch_press_releases,
    compute_financial_signals,
    format_financial_signals_for_prompt,
    compute_quarterly_trends,
    format_quarterly_trends_for_prompt,
)
from src.research_ideas.complacency.peer_benchmarks import (
    get_peer_context,
    format_peer_context_for_prompt,
)
from src.research_ideas.complacency.web_research import (
    fetch_earnings_qa, format_earnings_qa_for_prompt,
    deep_research_indicator,
)
from src.research_ideas.complacency.schemas import (
    QualitativeAssessment,
    QualIndicatorScore,
    QualEvidence,
    QualConvictionLabel,
)


logger = logging.getLogger(__name__)


# ─── Model config ──────────────────────────────────────────────────────────

QUAL_MODEL_NAME = os.environ.get("COMPLACENCY_QUAL_MODEL", "qwen3.6-plus")
QUAL_MODEL_PROVIDER = ModelProvider.ALIBABA
QUAL_TEMPERATURE = 0.2   # low — we want consistent, conservative scoring
QUAL_CACHE_TTL_DAYS = 7


# ─── Pydantic output model for LLM ────────────────────────────────────────


class _LLMEvidence(BaseModel):
    source: str = Field(description="Source label, e.g. '10-K 2024 risk factors' or 'Q3 2024 earnings transcript'")
    quote: str = Field(description="Verbatim quote from the source (≤ 300 chars). Trim ellipses if longer.")
    date: Optional[str] = Field(default=None, description="ISO date yyyy-mm-dd of the source if known")
    url: Optional[str] = Field(default=None, description="URL to the source if available")


class _LLMExtractedFact(BaseModel):
    """A single fact the LLM extracted from the evidence, mapped to a rubric anchor.

    Forcing the LLM to enumerate facts BEFORE scoring (chain-of-thought
    scaffold) gives most of the benefit of a two-stage extract→score flow
    without paying for two LLM round-trips.
    """
    fact: str = Field(description="Concise factual statement extracted from the evidence (≤ 200 chars).")
    rubric_anchor: str = Field(
        description=(
            "Which rubric score (0-5) this fact most directly supports, "
            "e.g. 'score 3: Goodwill > 60% of equity' or 'score 0: clean accounting'."
        )
    )
    source_tier: int = Field(
        ge=1, le=3,
        description="Source-quality tier (1=SEC/Bloomberg/WSJ, 2=sell-side/CNBC, 3=Tavily/Seeking Alpha).",
    )


class _LLMIndicatorOutput(BaseModel):
    """JSON-mode output from the qualitative scorer agent (with chain-of-thought scaffold)."""
    extracted_facts: list[_LLMExtractedFact] = Field(
        default_factory=list,
        description=(
            "Step 1: Before scoring, extract 3-5 relevant facts from the evidence "
            "and tag each with the rubric anchor it supports and the source tier. "
            "If you can't extract any facts, the score should be 0 with confidence < 0.4."
        ),
    )
    score: int = Field(ge=0, le=5, description="Step 3: Final score 0-5 per the rubric, justified by the extracted_facts.")
    confidence: float = Field(
        ge=0.0, le=1.0,
        description=(
            "Step 4: 0=no confidence, 1=certain. Cap at 0.50 if all facts are "
            "Tier-3 (Tavily blogs, opinion pieces). Use < 0.4 if extracted_facts is empty."
        ),
    )
    summary: str = Field(description="Step 5: One-line takeaway, ≤ 200 chars")
    evidence: list[_LLMEvidence] = Field(
        default_factory=list,
        description="Step 2: Verbatim source quotes backing the extracted_facts. Required unless score=0."
    )


# ─── Evidence gatherers (cheap; called BEFORE the LLM) ────────────────────


def _fetch_recent_news(ticker: str, days: int = 90, limit: int = 8) -> list[dict]:
    """Recent news articles from FMP /news/general. Returns trimmed snippets."""
    today = date.today()
    since = today - timedelta(days=days)
    data = _fmp_get(
        f"{_STABLE}/news/stock",
        {"symbols": ticker, "from": since.isoformat(), "to": today.isoformat(), "limit": limit},
        api_key=None,
        uncap=True,
    )
    if not isinstance(data, list):
        return []
    out: list[dict] = []
    for row in data[:limit]:
        out.append({
            "source": f"News — {row.get('site') or 'unknown'}",
            "date": (row.get("publishedDate") or "")[:10],
            "title": row.get("title") or "",
            "url": row.get("url"),
            "text_snippet": (row.get("text") or "")[:400],
        })
    return out


def _fetch_latest_transcript(ticker: str) -> Optional[dict]:
    """
    Most recent earnings call transcript. FMP's transcript endpoint requires
    year + quarter, so we first hit /earning-call-transcript-dates to find
    the latest available, then fetch that quarter's transcript.
    """
    # 1) Find the latest transcript date
    dates = _fmp_get(
        f"{_STABLE}/earning-call-transcript-dates",
        {"symbol": ticker},
        api_key=None,
        uncap=True,
    )
    if not isinstance(dates, list) or not dates:
        return None
    # Endpoint returns [{date, year, quarter, ...}] — pick the latest by date
    rows_with_date = [r for r in dates if r.get("date")]
    if not rows_with_date:
        return None
    latest = sorted(rows_with_date, key=lambda r: r["date"], reverse=True)[0]
    year = latest.get("year")
    quarter = latest.get("quarter")
    if year is None or quarter is None:
        return None

    # 2) Fetch the transcript content
    data = _fmp_get(
        f"{_STABLE}/earning-call-transcript",
        {"symbol": ticker, "year": year, "quarter": quarter},
        api_key=None,
        uncap=True,
    )
    if not isinstance(data, list) or not data:
        return None
    r = data[0]
    content = r.get("content") or ""
    if not content:
        return None
    return {
        "source": f"Q{quarter} {year} earnings transcript",
        "date": (r.get("date") or latest.get("date") or "")[:10],
        "content_snippet": content[:8000],
    }


def _fetch_10k_risk_factors(ticker: str) -> Optional[dict]:
    """
    Pulls the most recent 10-K via /financial-reports-json and returns a
    text snippet of the MD&A / Risk Factors / Critical Audit Matters
    sections — anything containing meaningful narrative prose. Falls back
    to scanning ALL sections and stitching long-text content if specific
    section names aren't present (FMP truncates section keys to ~32 chars).
    """
    today_year = date.today().year
    for try_year in (today_year, today_year - 1, today_year - 2):
        report = _fmp_get(
            f"{_STABLE}/financial-reports-json",
            {"symbol": ticker, "year": try_year, "period": "FY"},
            api_key=None,
            uncap=True,
        )
        # FMP started returning a dict instead of a single-item list around
        # mid-2026; normalise both shapes.
        if isinstance(report, list):
            r = report[0] if report else None
        elif isinstance(report, dict):
            r = report
        else:
            r = None
        if not r or not isinstance(r, dict):
            continue

        # Try to find sections by keyword match in the truncated section name.
        # FMP truncates names like "Risk Factors (Tables)" to 32 chars max.
        candidates: list[tuple[str, list]] = []
        for section_name, rows in r.items():
            if not isinstance(rows, list):
                continue
            name_lc = str(section_name).lower()
            if any(k in name_lc for k in (
                "risk", "critical", "going concern", "mda", "management",
                "litigation", "contingenc", "subseq", "going-concern",
            )):
                candidates.append((section_name, rows))

        # If no keyword match (financial-reports-json sometimes only exposes
        # statement sections), fall back to ALL sections — we'll still get
        # the income-statement / balance-sheet / cash-flow rows which the
        # LLM can use to spot accounting irregularities.
        if not candidates:
            candidates = [(s, rows) for s, rows in r.items() if isinstance(rows, list)]

        text_parts: list[str] = []
        for section_name, rows in candidates[:10]:   # cap depth so prompt stays small
            text_parts.append(f"## SECTION: {section_name}")
            for row in rows:
                if isinstance(row, dict):
                    for k, v in row.items():
                        # Stitch in any string-valued lines OR list values
                        if isinstance(v, list):
                            for x in v:
                                if isinstance(x, str) and 30 < len(x) < 1500:
                                    text_parts.append(f"  {k}: {x}")
                                elif isinstance(x, (int, float)):
                                    text_parts.append(f"  {k}: {x:,.0f}" if isinstance(x, (int, float)) else f"  {k}: {x}")
                        elif isinstance(v, str) and len(v) > 30:
                            text_parts.append(f"  {k}: {v}")

        if not text_parts:
            continue
        return {
            "source": f"10-K FY{try_year}",
            "date": f"{try_year}-12-31",
            "content_snippet": "\n".join(text_parts)[:8000],
        }
    return None


def _fetch_analyst_recommendations(ticker: str) -> Optional[dict]:
    """
    FMP /grades-consensus returns aggregate sell-side rating tallies
    (strongBuy / buy / hold / sell / strongSell). Used by A3 (consensus
    uniformity). Returns None when the endpoint is plan-gated or empty.
    """
    data = _fmp_get(
        f"{_STABLE}/grades-consensus",
        {"symbol": ticker},
        api_key=None,
        uncap=True,
    )
    if not isinstance(data, list) or not data:
        return None
    r = data[0]
    total = sum([
        int(r.get("strongBuy") or 0), int(r.get("buy") or 0),
        int(r.get("hold") or 0), int(r.get("sell") or 0),
        int(r.get("strongSell") or 0),
    ])
    if total == 0:
        return None
    return {
        "strong_buy": int(r.get("strongBuy") or 0),
        "buy": int(r.get("buy") or 0),
        "hold": int(r.get("hold") or 0),
        "sell": int(r.get("sell") or 0),
        "strong_sell": int(r.get("strongSell") or 0),
        "total": total,
        "pct_buy_or_strong": (int(r.get("strongBuy") or 0) + int(r.get("buy") or 0)) / total,
    }


def _fetch_insider_recent_trades(ticker: str, days: int = 180, limit: int = 40) -> list[dict]:
    """
    Recent Form 4 insider transactions with owner type (CEO/CFO/Director).
    Used by D2 (insider behavior depth — WHO is selling, not just A/D ratio).
    """
    today = date.today()
    since = today - timedelta(days=days)
    data = _fmp_get(
        f"{_STABLE}/insider-trading/search",
        {"symbol": ticker, "page": 0, "limit": limit},
        api_key=None,
        uncap=True,
    )
    if not isinstance(data, list):
        return []
    out: list[dict] = []
    for r in data:
        trans_date = (r.get("transactionDate") or "")[:10]
        if trans_date and trans_date < since.isoformat():
            continue
        out.append({
            "date": trans_date,
            "owner_name": r.get("reportingName") or "",
            "owner_type": r.get("typeOfOwner") or "",
            "transaction_type": r.get("transactionType") or "",
            "acq_disp": r.get("acquisitionOrDisposition") or "",
            "shares": _safe_float(r.get("securitiesTransacted")) or 0,
            "price": _safe_float(r.get("price")) or 0,
            "value": (_safe_float(r.get("securitiesTransacted")) or 0) * (_safe_float(r.get("price")) or 0),
        })
    return out


def _fetch_earnings_calendar(ticker: str) -> Optional[dict]:
    """Next earnings date — proxy for catalyst proximity."""
    data = _fmp_get(
        f"{_STABLE}/earnings-calendar",
        {"symbol": ticker, "limit": 1},
        api_key=None,
        uncap=True,
    )
    if not isinstance(data, list) or not data:
        return None
    # Find the first future date
    today_iso = date.today().isoformat()
    for r in sorted(data, key=lambda x: x.get("date", "")):
        if r.get("date", "") >= today_iso:
            return {
                "next_earnings_date": r.get("date"),
                "eps_estimate": r.get("epsEstimated"),
                "revenue_estimate": r.get("revenueEstimated"),
            }
    return None


# ─── Qwen call wrapper (bypasses call_llm because we have no AgentState) ───


def _call_qwen_indicator(
    system_prompt: str,
    user_prompt: str,
) -> tuple[Optional[_LLMIndicatorOutput], float]:
    """
    Call qwen3.6-max for a single indicator. Returns (parsed_output, cost_usd).
    cost_usd is approximate (Qwen pricing varies).

    Uses get_model() directly with explicit env-var key (DEEP_RESEARCH_API_KEY).
    """
    api_keys = None  # let get_model resolve from env (DEEP_RESEARCH_API_KEY)
    llm = get_model(QUAL_MODEL_NAME, QUAL_MODEL_PROVIDER, api_keys)
    if llm is None:
        logger.warning(
            "Qwen LLM unavailable (DEEP_RESEARCH_API_KEY missing?). "
            "Qualitative scoring disabled."
        )
        return None, 0.0

    # Bind structured output (json_mode) to enforce the schema.
    structured = llm.with_structured_output(_LLMIndicatorOutput, method="json_mode")

    messages = [
        ("system", system_prompt),
        ("human", user_prompt),
    ]
    try:
        result = structured.invoke(messages)
    except Exception as exc:
        # Qwen DashScope returns flat JSON; try second attempt with non-structured
        logger.warning("Qwen structured-output failed (%s); retrying raw.", exc)
        try:
            raw = llm.invoke(messages)
            text = raw.content if hasattr(raw, "content") else str(raw)
            # Try to extract a JSON block
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                blob = json.loads(text[start : end + 1])
                result = _LLMIndicatorOutput(**blob)
            else:
                return None, 0.0
        except Exception as exc2:
            logger.exception("Qwen JSON extraction failed: %s", exc2)
            return None, 0.0

    # Cost approximation — Qwen Max ~ $4/M input, $12/M output.
    # Rough estimate based on prompt size.
    approx_input_tokens = len(system_prompt + user_prompt) // 3
    approx_output_tokens = 600
    cost = (approx_input_tokens * 4e-6) + (approx_output_tokens * 12e-6)

    return result, cost


# ─── Indicator definitions (rubrics + evidence-gathering hooks) ────────────


_RUBRIC_SHARED_INSTRUCTIONS = """
You are a forensic short-seller scoring ONE qualitative indicator on a 0-5 scale.

STRICT RULES:
1. Use ONLY the evidence provided to you. Do NOT make up facts.
2. Score must be supported by at least one direct quote. If you cannot find evidence in the provided context, return score=0 with confidence < 0.4.
3. Trust a single high-quality citation (10-K direct quote, 8-K filing, DEF 14A, official press release) at face value.
   Otherwise require 2 independent sources.
4. Evidence quotes must be VERBATIM (copy-paste from the provided text).
5. Be conservative — when in doubt, score lower and set lower confidence.

SOURCE QUALITY TIERS (weight your confidence accordingly):
  Tier 1 (high trust)  : SEC filings (10-K, 10-Q, 8-K, DEF 14A, Form 4/144),
                          FMP /grades-consensus, official corporate press releases
                          (PR Newsweek, BusinessWire from the company itself),
                          WSJ, Bloomberg, Reuters, FT.
  Tier 2 (medium trust): sell-side equity research, FMP earnings calendar,
                          mainstream business news (CNBC, MarketWatch).
  Tier 3 (low trust)   : Tavily web results from unknown blogs, Seeking Alpha
                          opinion articles (NOT analyst-curated Seeking Alpha
                          Pro), Instagram/X excerpts, market-commentary sites.

When evidence is exclusively Tier 3, cap confidence at 0.50 and add a
"speculative — needs Tier-1 corroboration" note to your summary. When you
have a Tier-1 source contradicting Tier-3 chatter, prefer the Tier-1 view.

REASONING PROCESS (must follow this order — chain-of-thought scaffold):

  Step 1 — EXTRACT 3-5 facts from the provided evidence into `extracted_facts`.
           For each fact: state it concisely, tag the rubric score (0-5) it
           most directly supports, AND tag its source tier (1/2/3).
           If you cannot extract any facts → score = 0, confidence < 0.4.

  Step 2 — Copy the VERBATIM source quote backing each fact into `evidence`.

  Step 3 — Score 0-5 by selecting the rubric anchor that BEST matches the
           weight of facts in `extracted_facts`. Use the strongest-supported
           anchor; do not split the difference.

  Step 4 — Set `confidence`:
             • If every fact is Tier-1 and they corroborate → 0.80-0.95
             • If mixed Tier-1 / Tier-2 → 0.60-0.80
             • If only Tier-3 → cap at 0.50
             • If facts contradict each other → 0.30-0.50
             • If `extracted_facts` is empty → < 0.40

  Step 5 — Write a one-line `summary` (≤ 200 chars).

OUTPUT FORMAT (JSON only):
{
  "extracted_facts": [
    {"fact": "<≤ 200 chars>", "rubric_anchor": "score N: <reason>", "source_tier": 1|2|3}
  ],
  "evidence": [
    {"source": "<source label>", "quote": "<verbatim ≤ 300 chars>", "date": "yyyy-mm-dd"}
  ],
  "score": <0-5 integer>,
  "confidence": <0-1 float>,
  "summary": "<≤ 200 char takeaway>"
}
"""


def _build_user_prompt(
    ticker: str,
    name: str,
    sector: str | None,
    indicator_code: str,
    indicator_rubric: str,
    evidence_packs: list[dict],
) -> str:
    """Assemble the user-side prompt with rubric + gathered evidence."""
    parts = [
        f"TICKER: {ticker}  ({name})",
        f"SECTOR: {sector or 'unknown'}",
        "",
        f"INDICATOR: {indicator_code}",
        "",
        "RUBRIC:",
        indicator_rubric.strip(),
        "",
        "EVIDENCE PROVIDED:",
    ]
    if not evidence_packs:
        parts.append("(none — return score=0, confidence < 0.4)")
    else:
        for i, pack in enumerate(evidence_packs, 1):
            parts.append(f"--- Source {i}: {pack.get('source','?')} ({pack.get('date','?')}) ---")
            parts.append(pack.get("text", "").strip())
            parts.append("")
    parts.append("Now return the JSON score per the rubric.")
    return "\n".join(parts)


# ── A2 — Catalyst proximity ───────────────────────────────────────────────


A2_RUBRIC = """
Score the proximity of a near-term catalyst that could re-rate the stock down.

0  No identifiable catalyst within 12 months.
1  Soft catalyst (industry conference, sell-side day) within 6 months.
2  Concrete earnings event in 3-6 months.
3  Concrete earnings event in <3 months PLUS guidance pressure noted in recent transcript.
4  Multiple stacked catalysts (earnings + regulatory + contract expiry) within 6 months.
5  Imminent earnings (<6 weeks) + management has acknowledged downside risk on prior call.
"""


def _tavily_packs(query: str, days: int, max_results: int = 5) -> list[dict]:
    """Tavily search results normalised to the gatherer pack shape."""
    out: list[dict] = []
    for r in tavily_search(query, days=days, max_results=max_results):
        out.append({
            "source": f"Tavily — {r.get('title','')[:80]}",
            "date": (r.get("published_date") or "")[:10],
            "text": f"{r.get('title','')}\n\n{r.get('content','')}",
        })
    return out


def _fmp_news_packs(rows: list[dict]) -> list[dict]:
    """Normalize fetch_stock_news / fetch_press_releases output to pack shape."""
    return [{
        "source": r["source"],
        "date": r["date"],
        "text": f"{r['title']}\n\n{r['text']}",
    } for r in rows]


def _earnings_qa_pack(ticker: str, focus_topics: set[str] | None = None) -> Optional[dict]:
    """
    Fetch earnings call Q&A via Qwen web search (no Tavily) and return
    a single evidence pack. Cached 30 days.

    If `focus_topics` is provided, prepend a one-line note that the
    indicator cares about those topics — helps the LLM scoring focus.
    """
    qa = fetch_earnings_qa(ticker, n_quarters=2)
    if not qa or not qa.get("digest"):
        return None
    text = format_earnings_qa_for_prompt(qa)
    if focus_topics:
        relevant_topics = focus_topics & set(qa.get("topics_flagged") or [])
        if relevant_topics:
            text = (
                f"  [↑ This Q&A surfaced topics relevant to this indicator: "
                f"{', '.join(sorted(relevant_topics))}]\n"
                + text
            )
    return {
        "source": f"Earnings-call Q&A (Qwen web search; {qa.get('source_hint','?')})",
        "date": qa.get("fetched_at", "")[:10],
        "text": text,
    }


def _gather_evidence_A2(ticker: str) -> list[dict]:
    """Catalyst proximity: earnings cal + Tavily query for upcoming-event hedge language."""
    packs: list[dict] = []
    cal = _fetch_earnings_calendar(ticker)
    if cal:
        days = None
        try:
            d = date.fromisoformat(cal["next_earnings_date"])
            days = (d - date.today()).days
        except Exception:
            pass
        packs.append({
            "source": "FMP earnings calendar",
            "date": cal["next_earnings_date"],
            "text": f"Next earnings date: {cal['next_earnings_date']} "
                    f"({days} days from today). EPS est: {cal.get('eps_estimate')}, "
                    f"Revenue est: {cal.get('revenue_estimate')}",
        })
    # Tavily: catalyst-specific signals (guidance pressure, contract loss, regulatory ruling)
    packs.extend(_tavily_packs(
        f'"{ticker}" earnings preview OR guidance OR catalyst OR contract loss OR regulatory',
        days=60, max_results=4,
    ))
    # FMP stock news + press releases — official corporate actions / earnings previews
    packs.extend(_fmp_news_packs(fetch_stock_news(ticker, days=45, limit=4)))
    packs.extend(_fmp_news_packs(fetch_press_releases(ticker, days=60, limit=3)))
    # Priority 2: Earnings call Q&A — captures mgmt acknowledging downside
    # risk on prior call (rubric anchor for score 5) or downgrade rhetoric.
    qa_pack = _earnings_qa_pack(ticker, focus_topics={"guidance", "regulatory", "restatement"})
    if qa_pack:
        packs.append(qa_pack)
    return packs


# ── C2 — Accounting aggressiveness ────────────────────────────────────────


C2_RUBRIC = """
Score how aggressive the company's accounting / disclosure practices are.

0  Clean accounting. Conservative revenue recognition. Low goodwill / intangibles relative to equity.
1  Minor concerns (small acquired goodwill, normal deferred revenue patterns).
2  Goodwill 30-60% of equity OR rising DSO trend OR multiple small restatements.
3  Goodwill > 60% of equity OR aggressive revenue recognition flagged in 10-K KAMs OR pattern of acquisitions hiding organic decline.
4  Large unamortized goodwill > 100% of equity AND capitalized expenses growing rapidly AND deferred-revenue / cash-collection mismatch.
5  Severe accounting red flags: restated financials in last 2 years, KAMs explicitly flagging valuation/impairment risk, channel-stuffing signals, OR auditor change.
"""


def _gather_evidence_C2(ticker: str) -> list[dict]:
    """Accounting aggressiveness: SEC EDGAR 10-K (KAM + Risk Factors) + computed ratios + Tavily."""
    packs: list[dict] = []
    sec = fetch_sec_10k_sections(ticker)
    if sec.get("kam"):
        packs.append({
            "source": f"SEC 10-K Critical Audit Matters (filed {sec.get('_filed_date','?')})",
            "date": sec.get("_filed_date", ""),
            "text": sec["kam"],
        })
    if sec.get("risk_factors"):
        packs.append({
            "source": f"SEC 10-K Risk Factors (filed {sec.get('_filed_date','?')})",
            "date": sec.get("_filed_date", ""),
            "text": sec["risk_factors"][:4000],
        })
    # Computed financial signals — direct numeric evidence
    sig = compute_financial_signals(ticker)
    if any(v is not None for v in sig.values()):
        packs.append({
            "source": "Derived financial signals (FMP 4yr statements)",
            "date": date.today().isoformat(),
            "text": format_financial_signals_for_prompt(sig),
        })
    # Tavily for any narrative around restatements / accounting concerns
    packs.extend(_tavily_packs(
        f'"{ticker}" restatement OR goodwill impairment OR channel stuffing OR accounting concerns',
        days=180, max_results=3,
    ))
    # FMP press releases — direct corporate-action evidence (write-downs, restatements)
    packs.extend(_fmp_news_packs(fetch_press_releases(ticker, days=180, limit=4)))
    return packs


# ── D1 — Management quality red flags ─────────────────────────────────────


D1_RUBRIC = """
Score management-quality red flags.

0  Long-tenured CEO/CFO, consistent guidance history, comp tied to operating metrics.
1  Minor red flag (one missed guide in last 4 quarters).
2  Recent CFO change OR pattern of EPS beats driven by tax / non-op items OR promotional CEO language.
3  CEO and CFO changes within 24 months OR repeated guidance misses (3+) OR related-party transactions disclosed.
4  Recent senior departures (CEO, CFO, COO) AND comp tied to vanity metrics (TAM, narrative KPIs) AND aggressive forward narrative.
5  Active scandals, SEC inquiry, recent restatement, OR CEO comp > 50× CFO with little oversight.
"""


def _gather_evidence_D1(ticker: str) -> list[dict]:
    """Management red flags: 8-K Item 5.02 (exec departures) + DEF 14A (comp + related-party)
    + Tavily for scandal/turnover news + 10-K Controls (material weaknesses)."""
    packs: list[dict] = []

    # Tier 1: SEC 8-K filings — Item 5.02 catches director/officer departures
    # before the news cycle (CRWD has 2 such events in last 6 months).
    filings_8k = fetch_sec_recent_8k(ticker, days=180, limit=12)
    if filings_8k:
        # Focus on 5.02 (exec change), 5.03 (bylaws), 1.01 (material agreements).
        # items is a list[dict] with code/label keys.
        relevant = [f for f in filings_8k if any(
            (it.get("code", "") if isinstance(it, dict) else str(it)).startswith(
                ("5.0", "5.1", "1.01")
            )
            for it in (f.get("items") or [])
        )]
        if relevant:
            packs.append({
                "source": "SEC 8-K filings (last 180 days, exec/governance items)",
                "date": filings_8k[0].get("filed_date", ""),
                "text": format_8k_filings_for_prompt(relevant),
            })

    # Tier 1: DEF 14A proxy — exec compensation, CEO pay ratio, related-party deals
    proxy = fetch_sec_def14a_excerpt(ticker)
    if proxy:
        sections = proxy.get("sections") or {}
        body_parts = []
        for key in ("ceo_pay_ratio", "related_party", "executive_compensation"):
            snip = sections.get(key)
            if snip:
                body_parts.append(f"## {key.upper()}\n{snip[:1500]}")
        if body_parts:
            packs.append({
                "source": f"SEC DEF 14A proxy (filed {proxy.get('_filed_date','?')})",
                "date": proxy.get("_filed_date", ""),
                "text": "\n\n".join(body_parts),
            })

    # Tier 1/3 mix: Tavily targeted query
    packs.extend(_tavily_packs(
        f'"{ticker}" CEO OR CFO OR executive scandal OR departure OR investigation OR lawsuit OR resign',
        days=180, max_results=6,
    ))

    # Tier 1: SEC 10-K Controls (material weaknesses, segregation-of-duties failures)
    sec = fetch_sec_10k_sections(ticker)
    if sec.get("controls"):
        packs.append({
            "source": f"SEC 10-K Controls & Procedures (filed {sec.get('_filed_date','?')})",
            "date": sec.get("_filed_date", ""),
            "text": sec["controls"][:3000],
        })
    # Priority 2: Earnings call Q&A — analysts asking pointed questions about
    # CEO/CFO turnover, exec-comp shifts, or board changes is a leading signal
    # for D1. Targeted topic filter on exec_departure / restatement / regulatory.
    qa_pack = _earnings_qa_pack(
        ticker,
        focus_topics={"exec_departure", "restatement", "regulatory"},
    )
    if qa_pack:
        packs.append(qa_pack)
    return packs


# ══════════════════════════════════════════════════════════════════════════
# v2 indicators (7 more — completing themes A/B/C/D per the spec)
# ══════════════════════════════════════════════════════════════════════════


# ── A1 — Single-thesis dependence ────────────────────────────────────────


A1_RUBRIC = """
Score how much the bull case depends on ONE assumption.

0  Multi-driver business with diversified bull narratives (3+ independent drivers).
1  Two clear bull drivers; loss of one would not collapse the thesis.
2  One dominant bull driver but supported by tangible secondary drivers.
3  Single dominant assumption (e.g., 'TAM is infinite', 'hyperscaler capex never stops').
4  Single dominant assumption AND that assumption is itself contested in news / analyst notes.
5  Single high-conviction narrative AND mounting evidence the assumption is breaking.
"""


def _gather_evidence_A1(ticker: str) -> list[dict]:
    """Single-thesis: Tavily framing + FMP news + earnings-call Q&A (management
    defending the bull narrative is the strongest signal of thesis fragility)."""
    packs: list[dict] = []
    packs.extend(_tavily_packs(
        f'"{ticker}" bull case OR investment thesis OR TAM OR AI moat OR hyperscaler',
        days=120, max_results=4,
    ))
    packs.extend(_fmp_news_packs(fetch_stock_news(ticker, days=90, limit=6)))
    # Priority 2: Earnings call Q&A — analysts probing the bull thesis,
    # mgmt rhetorical patterns when defending it, AI/TAM dependency.
    qa_pack = _earnings_qa_pack(ticker, focus_topics={"ai_threat", "competition", "guidance"})
    if qa_pack:
        packs.append(qa_pack)
    return packs


# ── A3 — Consensus uniformity ────────────────────────────────────────────


A3_RUBRIC = """
Score how crowded / uniform the long view is on Wall Street.

0  Mixed coverage (Buy ≤ 50%, balanced Sell/Hold tails).
1  Buy 50-65% of analysts.
2  Buy 65-80% of analysts.
3  Buy > 80% of analysts AND price-target dispersion compressed.
4  Buy > 90% AND zero Sell ratings AND retail/news sentiment uniformly bullish.
5  Sell-side unanimity + retail euphoria + meme-stock-style price-target chasing.
"""


def _gather_evidence_A3(ticker: str) -> list[dict]:
    """Consensus uniformity: analyst tally + PT dispersion + Tavily for downgrade news."""
    packs: list[dict] = []
    recs = _fetch_analyst_recommendations(ticker)
    if recs:
        packs.append({
            "source": "FMP analyst consensus (/grades-consensus)",
            "date": date.today().isoformat(),
            "text": (
                f"Sell-side ratings tally: "
                f"strongBuy={recs['strong_buy']} buy={recs['buy']} "
                f"hold={recs['hold']} sell={recs['sell']} strongSell={recs['strong_sell']}. "
                f"Total {recs['total']} analysts; {recs['pct_buy_or_strong']*100:.0f}% Buy+StrongBuy."
            ),
        })
    pt = fetch_price_target_consensus(ticker)
    if pt and pt.get("cv_estimate") is not None:
        cv = pt["cv_estimate"]
        crowded = (
            "extremely uniform (≤ 10%)" if cv <= 0.10
            else "uniform (10-20%)"        if cv <= 0.20
            else "moderate dispersion (20-30%)" if cv <= 0.30
            else "wide dispersion (> 30%)"
        )
        packs.append({
            "source": "FMP price-target consensus (/price-target-consensus)",
            "date": date.today().isoformat(),
            "text": (
                f"Sell-side price targets: high=${pt['target_high']}, low=${pt['target_low']}, "
                f"avg=${pt['target_avg']:.2f}, median=${pt['target_median']}. "
                f"Half-range / mean = {cv*100:.1f}% — {crowded}. "
                f"(Low CV = tight target clustering = crowded long view)"
            ),
        })
    # /price-target-summary: time-windowed averages.  When the last-month
    # avg PT is meaningfully higher than the all-time avg, sell-side is
    # CHASING (textbook A3 melt-up complacency signature).
    pts = fetch_price_target_summary(ticker)
    if pts and pts.get("all_time_avg"):
        chase_m = pts.get("chase_ratio_month_vs_alltime")
        chase_q = pts.get("chase_ratio_quarter_vs_year")
        def _chase_label(r):
            if r is None: return "n/a"
            if r >= 1.50: return "AGGRESSIVE CHASING (+50% vs baseline)"
            if r >= 1.20: return "CHASING (+20-50%)"
            if r >= 1.05: return "drifting up (+5-20%)"
            if r >= 0.95: return "stable (±5%)"
            if r >= 0.80: return "drifting down (-5-20%)"
            return "RESETTING DOWN (>-20%)"
        text = (
            f"Sell-side price-target time series:\n"
            f"  last month   : ${pts.get('last_month_avg')}  ({pts.get('last_month_count')} analysts)\n"
            f"  last quarter : ${pts.get('last_quarter_avg')}  ({pts.get('last_quarter_count')} analysts)\n"
            f"  last year    : ${pts.get('last_year_avg')}  ({pts.get('last_year_count')} analysts)\n"
            f"  all-time avg : ${pts.get('all_time_avg')}  ({pts.get('all_time_count')} analysts)\n"
            f"  chase ratio month/alltime    : "
            f"{chase_m:.2f}x → {_chase_label(chase_m)}\n"
            if chase_m is not None else
            f"Sell-side price-target time series: last month=${pts.get('last_month_avg')} "
            f"alltime=${pts.get('all_time_avg')}; chase-ratio n/a.\n"
        )
        if chase_q is not None:
            text += f"  chase ratio quarter/year     : {chase_q:.2f}x → {_chase_label(chase_q)}\n"
        publishers = pts.get("publishers") or []
        if publishers:
            text += f"  publishers ({len(publishers)})           : {', '.join(publishers[:8])}"
            if len(publishers) > 8:
                text += f" +{len(publishers)-8} more"
        packs.append({
            "source": "FMP price-target summary (/price-target-summary)",
            "date": date.today().isoformat(),
            "text": text,
        })
    packs.extend(_tavily_packs(
        f'"{ticker}" analyst downgrade OR price target OR initiates coverage OR upgrade',
        days=60, max_results=3,
    ))
    # FMP stock news — analyst-coverage articles often appear here with full text
    packs.extend(_fmp_news_packs(fetch_stock_news(ticker, days=45, limit=4)))
    return packs


# ── B1 — Customer / revenue concentration ────────────────────────────────


B1_RUBRIC = """
Score customer or revenue-segment concentration.

0  Diversified revenue, no top-3 dependency disclosed.
1  Top-3 < 35% of revenue.
2  Top-3 35-50% OR single platform dependency (AWS/Apple/Google ecosystem).
3  Top-3 50-70% OR one customer > 30%.
4  Top-3 > 70% OR one customer > 30% AND that customer is publicly evaluating alternatives.
5  Top-1 > 50% AND that customer is mid-RFP for replacement.
"""


def _gather_evidence_B1(ticker: str) -> list[dict]:
    """Customer concentration: SEC 10-K Risk Factors + peer-context for deferred-rev
    (high deferred-rev/revenue → customer stickiness; low → easier substitution)."""
    packs: list[dict] = []
    sec = fetch_sec_10k_sections(ticker)
    if sec.get("risk_factors"):
        packs.append({
            "source": f"SEC 10-K Risk Factors (filed {sec.get('_filed_date','?')})",
            "date": sec.get("_filed_date", ""),
            "text": sec["risk_factors"][:6000],
        })
    # 2B: Peer context — deferred-rev/revenue & revenue growth vs cohort
    sig = compute_financial_signals(ticker)
    if any(v is not None for v in sig.values()):
        ctx = get_peer_context(ticker, sig)
        if ctx:
            packs.append({
                "source": "Peer benchmark (complacency cohort medians)",
                "date": date.today().isoformat(),
                "text": format_peer_context_for_prompt(ctx),
            })
    packs.extend(_tavily_packs(
        f'"{ticker}" largest customer OR customer concentration OR top customer OR loses contract',
        days=180, max_results=3,
    ))
    return packs


# ── B2 — Competitive disintermediation ───────────────────────────────────


B2_RUBRIC = """
Score competitive disintermediation risk (overlaps with AICT but more granular).

0  Defensible product moat; no credible AI-native / vertical-integration competitor.
1  Emerging competitors in narrow segments; core business unchanged.
2  AI-native competitor exists at scale; product overlap < 30%.
3  AI-native or hyperscaler competitor with material product overlap (30-60%).
4  Hyperscaler bundles the product for free / near-free; customer migration evidence emerging.
5  Existential disintermediation in progress (e.g., Snowflake displacing Teradata, AI agents displacing per-seat SaaS).
"""


def _gather_evidence_B2(ticker: str) -> list[dict]:
    """Competitive disintermediation: SEC 10-K Risk Factors + peer-context (revenue
    growth & gross margin trajectory vs peers reveals competitive pressure) +
    Tavily on AI-native rivals."""
    packs: list[dict] = []
    sec = fetch_sec_10k_sections(ticker)
    if sec.get("risk_factors"):
        packs.append({
            "source": f"SEC 10-K Risk Factors (filed {sec.get('_filed_date','?')})",
            "date": sec.get("_filed_date", ""),
            "text": sec["risk_factors"][:5000],
        })
    # 2B: Peer context — revenue CAGR + gross margin vs cohort medians.
    # Below-cohort growth + below-cohort GM is the disintermediation signature.
    sig = compute_financial_signals(ticker)
    if any(v is not None for v in sig.values()):
        ctx = get_peer_context(ticker, sig)
        if ctx:
            packs.append({
                "source": "Peer benchmark (complacency cohort medians)",
                "date": date.today().isoformat(),
                "text": format_peer_context_for_prompt(ctx),
            })
    packs.extend(_tavily_packs(
        f'"{ticker}" competitor OR AI-native OR disrupt OR hyperscaler bundle OR open-source alternative',
        days=120, max_results=5,
    ))
    # Priority 2: Earnings Q&A — analyst questions on competition + mgmt's
    # rebuttals reveal disintermediation pressure better than Tavily snippets.
    qa_pack = _earnings_qa_pack(ticker, focus_topics={"competition", "ai_threat", "pricing"})
    if qa_pack:
        packs.append(qa_pack)
    return packs


# ── B3 — Pricing power erosion ───────────────────────────────────────────


B3_RUBRIC = """
Score pricing-power erosion signals.

0  Pricing power intact: rising ASP, NRR > 110%, expanding deferred revenue.
1  NRR steady (100-110%) but ASP flat; deferred revenue normalising.
2  NRR slipping toward 100%; ASP compression in some segments.
3  NRR < 100% (net contraction) OR persistent ASP discounting in earnings notes.
4  Multi-quarter NRR < 95% AND deferred revenue growth lagging billings.
5  Active pricing war (free / freemium pressure) AND management has cut take-rate guidance.
"""


def _gather_evidence_B3(ticker: str) -> list[dict]:
    """Pricing power erosion: annual signals + 8-quarter trend (catches consistent
    deterioration vs single-quarter blip) + Tavily on NRR/ARR commentary."""
    packs: list[dict] = []
    sig = compute_financial_signals(ticker)
    if any(v is not None for v in sig.values()):
        packs.append({
            "source": "Derived financial signals (FMP 4yr statements)",
            "date": date.today().isoformat(),
            "text": format_financial_signals_for_prompt(sig),
        })
    # 2C: Quarter-by-quarter trajectory for the last 8 quarters.
    # Differentiates "one weak quarter" (score 1-2) from "consistent
    # deterioration over 4+ quarters" (score 3-5).
    qt = compute_quarterly_trends(ticker, n_quarters=8)
    if qt.get("quarters"):
        packs.append({
            "source": "Derived quarterly trends (FMP 8-quarter trajectory)",
            "date": date.today().isoformat(),
            "text": format_quarterly_trends_for_prompt(qt),
        })
    # 2B: Peer context — gross margin & DSO trends vs cohort medians.
    # Below-cohort gross margin + above-cohort DSO is pricing-power erosion.
    if any(v is not None for v in sig.values()):
        ctx = get_peer_context(ticker, sig)
        if ctx:
            packs.append({
                "source": "Peer benchmark (complacency cohort medians)",
                "date": date.today().isoformat(),
                "text": format_peer_context_for_prompt(ctx),
            })
    packs.extend(_tavily_packs(
        f'"{ticker}" pricing power OR net retention OR NRR OR billings OR deferred revenue OR ARR',
        days=120, max_results=4,
    ))
    return packs


# ── C1 — Disclosure deterioration ────────────────────────────────────────


C1_RUBRIC = """
Score disclosure-quality deterioration.

0  Stable disclosure: same KAMs YoY, no restatements, long-tenured auditor + CFO.
1  Minor disclosure change (one new KAM added, segment definition tweaked).
2  Recent CFO change (last 18 months) OR auditor change OR one restatement in last 2 years.
3  Multiple KAMs added in last 10-K AND aggressive non-GAAP adjustments.
4  Auditor change + CFO turnover within 24 months + new KAMs flagging valuation risk.
5  Active restatement, SEC comment letter, or KAM explicitly flagging going-concern uncertainty.
"""


def _gather_evidence_C1(ticker: str) -> list[dict]:
    """Disclosure deterioration: SEC 8-K (Item 4.01/4.02 = auditor change/restatement) +
    10-K Controls + KAM + Tavily for restatement/auditor news + press releases."""
    packs: list[dict] = []

    # Tier 1: SEC 10-Q DIFF — new risk-factor language vs prior quarter.
    # Companies rarely ADD risk factors unless something materially changed.
    # This catches the C1 "Multiple KAMs added in last 10-K" rubric anchor
    # at quarterly cadence (10-Q is 3x faster than 10-K cycle).
    q_diff = fetch_sec_10q_diff(ticker)
    if q_diff and ((q_diff.get("new_risk_factors") or []) or (q_diff.get("new_mda") or [])):
        packs.append({
            "source": (
                f"SEC 10-Q diff (current {q_diff.get('_curr_filed_date','?')} "
                f"vs prior {q_diff.get('_prev_filed_date','?')})"
            ),
            "date": q_diff.get("_curr_filed_date", ""),
            "text": format_10q_diff_for_prompt(q_diff),
        })

    # Tier 1: SEC 8-K — Item 4.01 (auditor change) and 4.02 (non-reliance / restatement)
    # are the GOLD-standard signals for disclosure deterioration.
    filings_8k = fetch_sec_recent_8k(ticker, days=270, limit=15)
    if filings_8k:
        # items is a list[dict] with code/label keys.
        disclosure_relevant = [f for f in filings_8k if any(
            (it.get("code", "") if isinstance(it, dict) else str(it)).startswith(
                ("4.0", "8.01", "2.06")   # 4.01/4.02 + Item 8.01 other + 2.06 impairment
            )
            for it in (f.get("items") or [])
        )]
        if disclosure_relevant:
            packs.append({
                "source": "SEC 8-K filings (last 270 days, accounting/disclosure items)",
                "date": filings_8k[0].get("filed_date", ""),
                "text": format_8k_filings_for_prompt(disclosure_relevant),
            })

    sec = fetch_sec_10k_sections(ticker)
    if sec.get("controls"):
        packs.append({
            "source": f"SEC 10-K Controls & Procedures (filed {sec.get('_filed_date','?')})",
            "date": sec.get("_filed_date", ""),
            "text": sec["controls"][:3000],
        })
    if sec.get("kam"):
        packs.append({
            "source": f"SEC 10-K Critical Audit Matters (filed {sec.get('_filed_date','?')})",
            "date": sec.get("_filed_date", ""),
            "text": sec["kam"],
        })
    packs.extend(_tavily_packs(
        f'"{ticker}" restatement OR auditor change OR SEC inquiry OR material weakness OR going concern OR CFO change',
        days=270, max_results=3,
    ))
    # FMP press releases — earliest channel for securities class-action investigations,
    # CFO appointments, auditor changes, etc.
    packs.extend(_fmp_news_packs(fetch_press_releases(ticker, days=270, limit=6)))
    return packs


# ── D2 — Insider behavior depth ──────────────────────────────────────────


D2_RUBRIC = """
Score WHO is selling, not just the bulk A/D ratio.

0  Insiders are net buyers OR sales are exclusively automated 10b5-1 schedules at small sizes.
1  Routine 10b5-1 sales; no CEO/CFO discretionary activity.
2  Modest discretionary sales by VP/Director-level insiders.
3  CEO or CFO sold > $5M outside 10b5-1 in last 180 days.
4  CEO AND CFO both sold > $10M discretionary in last 180 days.
5  C-suite cluster-selling (3+ executives) > $25M total in last 90 days, with recent 10b5-1 modifications increasing sale rate.
"""


def _gather_evidence_D2(ticker: str) -> list[dict]:
    """D2 — needs structured insider trade detail with owner role + transaction type.
    Adds SEC Form 144 (proposed sales, a LEADING indicator vs. Form 4 settled trades)."""
    packs: list[dict] = []

    # Tier 1: SEC Form 144 — filed BEFORE the sale, so it's a leading indicator.
    # CRWD has shown 10 such filings in 90 days = signal.
    form144 = fetch_sec_recent_form144(ticker, days=90, limit=15)
    if form144:
        packs.append({
            "source": "SEC Form 144 — proposed insider sales (last 90 days)",
            "date": form144[0].get("filed_date", ""),
            "text": format_form144_for_prompt(form144),
        })

    trades = _fetch_insider_recent_trades(ticker, days=180, limit=40)
    if trades:
        # Summarize by owner type
        ceo_cfo = [t for t in trades if any(k in (t["owner_type"] or "").lower() for k in (
            "chief executive", "chief financial", "ceo", "cfo",
        ))]
        directors = [t for t in trades if "director" in (t["owner_type"] or "").lower()]
        plan_marker = sum(1 for t in trades if "10b5-1" in (t.get("transaction_type") or "").lower())
        total_value_disp = sum(t["value"] for t in trades if t["acq_disp"] == "D")
        total_value_acq = sum(t["value"] for t in trades if t["acq_disp"] == "A")
        text = (
            f"Last 180 days insider trades for {ticker}:\n"
            f"  total transactions: {len(trades)} (CEO/CFO: {len(ceo_cfo)}, Directors: {len(directors)})\n"
            f"  total Disposition $: ${total_value_disp:,.0f}\n"
            f"  total Acquisition $: ${total_value_acq:,.0f}\n"
            f"  10b5-1 flagged: {plan_marker}\n\n"
            f"Top 10 transactions by value:\n"
        )
        top = sorted(trades, key=lambda t: t["value"], reverse=True)[:10]
        for t in top:
            text += (
                f"  {t['date']:<11s} {t['owner_name'][:25]:<25s} ({t['owner_type'][:25]:<25s}) "
                f"{t['acq_disp']} {t['shares']:>10,.0f} @ ${t['price']:.2f} = ${t['value']:,.0f}\n"
            )
        packs.append({
            "source": "FMP /insider-trading/search (180-day window)",
            "date": date.today().isoformat(),
            "text": text,
        })
    return packs


# ── Indicator registry (all 10) ────────────────────────────────────────────


INDICATORS: dict[str, dict] = {
    "A1_single_thesis_dependence": {
        "rubric": A1_RUBRIC, "gather": _gather_evidence_A1,
        "theme": "A — Narrative fragility", "label": "Single-thesis dependence",
    },
    "A2_catalyst_proximity": {
        "rubric": A2_RUBRIC, "gather": _gather_evidence_A2,
        "theme": "A — Narrative fragility", "label": "Catalyst proximity",
    },
    "A3_consensus_uniformity": {
        "rubric": A3_RUBRIC, "gather": _gather_evidence_A3,
        "theme": "A — Narrative fragility", "label": "Consensus uniformity",
    },
    "B1_customer_concentration": {
        "rubric": B1_RUBRIC, "gather": _gather_evidence_B1,
        "theme": "B — Business-model fragility", "label": "Customer concentration",
    },
    "B2_competitive_disintermediation": {
        "rubric": B2_RUBRIC, "gather": _gather_evidence_B2,
        "theme": "B — Business-model fragility", "label": "Competitive disintermediation",
    },
    "B3_pricing_power_erosion": {
        "rubric": B3_RUBRIC, "gather": _gather_evidence_B3,
        "theme": "B — Business-model fragility", "label": "Pricing power erosion",
    },
    "C1_disclosure_deterioration": {
        "rubric": C1_RUBRIC, "gather": _gather_evidence_C1,
        "theme": "C — Disclosure & accounting", "label": "Disclosure deterioration",
    },
    "C2_accounting_aggressiveness": {
        "rubric": C2_RUBRIC, "gather": _gather_evidence_C2,
        "theme": "C — Disclosure & accounting", "label": "Accounting aggressiveness",
    },
    "D1_management_red_flags": {
        "rubric": D1_RUBRIC, "gather": _gather_evidence_D1,
        "theme": "D — Management & insider", "label": "Management red flags",
    },
    "D2_insider_behavior_depth": {
        "rubric": D2_RUBRIC, "gather": _gather_evidence_D2,
        "theme": "D — Management & insider", "label": "Insider behavior depth",
    },
}


# ─── Single-indicator scorer (cache-aware) ────────────────────────────────


# Priority 3 escalation threshold: if the first-pass scorer returns confidence
# at or below this, kick off a deep-research second pass via Qwen native web
# search. Tunable per-indicator if needed.
DEEP_RESEARCH_CONF_FLOOR = float(os.environ.get("COMPLACENCY_DEEP_RESEARCH_FLOOR", "0.45"))

# Hard cap on deep-research escalations per assess_qualitative() run. Each
# escalation costs ~$0.05 and 30-120s wall-clock. Without a cap, a ticker
# with many low-conf first-pass scores could trigger 6 escalations × 120s
# = 12 min just for deep research, on top of the 10 first-pass calls.
# 3 is a tight budget that prioritises the lowest-conf indicators.
DEEP_RESEARCH_MAX_PER_TICKER = int(os.environ.get("COMPLACENCY_DEEP_RESEARCH_MAX", "3"))

# Indicators where deep research is most valuable. Skip for indicators that
# are deterministically scored from numeric inputs (B3 pricing trends,
# C2 goodwill ratio, D2 insider trade totals) — the floor doesn't help if the
# answer is in the numbers already.
DEEP_RESEARCH_INDICATORS = {
    "A1_single_thesis_dependence",
    "A2_catalyst_proximity",
    "B1_customer_concentration",
    "B2_competitive_disintermediation",
    "C1_disclosure_deterioration",
    "D1_management_red_flags",
}

# Per-ticker shared counter for deep-research escalations. Has to be a
# GLOBAL shared count (with lock) so the cap is enforced across the
# ThreadPoolExecutor's 3 worker threads — threading.local would give each
# worker its own budget, defeating the cap.
#
# Reset at the start of every assess_qualitative() call. Concurrent
# assess_qualitative() calls (e.g. cohort refresh hitting multiple
# Strong-Short tickers in parallel) WILL share this counter — that's a
# minor over-application of the cap but harmless: worst case the cohort
# refresh skips deep research on some indicators across the whole run.
# For the user's actual force-rescore flow (1 ticker at a time), the cap
# works as intended.
import threading as _threading_mod
_DEEP_RESEARCH_COUNT_LOCK = _threading_mod.Lock()
_DEEP_RESEARCH_COUNT = 0


def _deep_research_count_get() -> int:
    with _DEEP_RESEARCH_COUNT_LOCK:
        return _DEEP_RESEARCH_COUNT


def _deep_research_count_inc() -> int:
    global _DEEP_RESEARCH_COUNT
    with _DEEP_RESEARCH_COUNT_LOCK:
        _DEEP_RESEARCH_COUNT += 1
        return _DEEP_RESEARCH_COUNT


def _deep_research_count_reset() -> None:
    global _DEEP_RESEARCH_COUNT
    with _DEEP_RESEARCH_COUNT_LOCK:
        _DEEP_RESEARCH_COUNT = 0


def score_indicator(
    ticker: str,
    name: str,
    sector: str | None,
    indicator_code: str,
    force_refresh: bool = False,
    enable_deep_research: bool = True,
) -> Optional[QualIndicatorScore]:
    """
    Score one indicator for one ticker. Uses 7-day cache unless force_refresh.

    When the first-pass scorer returns confidence ≤ DEEP_RESEARCH_CONF_FLOOR
    AND `enable_deep_research=True`, a second-pass deep-research call via Qwen
    native web search runs. If it produces a higher-confidence finding, the
    deep score replaces the first-pass — the cache stores the BETTER result.

    Returns None if the LLM is unavailable.
    """
    if indicator_code not in INDICATORS:
        logger.warning("Unknown indicator: %s", indicator_code)
        return None

    # Check cache
    from app.backend.services.qualitative_storage import (
        get_latest_qualitative_score, save_qualitative_score,
    )
    if not force_refresh:
        cached = get_latest_qualitative_score(ticker, indicator_code, QUAL_CACHE_TTL_DAYS)
        if cached:
            # Auto-bypass cache for low-conf entries that NEVER ran through
            # the deep-research path. Without this, scores cached BEFORE the
            # deep-research feature shipped (or any low-conf miss) are
            # returned as-is forever — defeating the new path entirely.
            #
            # Bypass conditions (ALL must hold):
            #   1. Indicator is eligible for deep research
            #   2. Cached confidence ≤ the deep-research floor
            #   3. The cached score's model_used does NOT already record
            #      a deep-research attempt (otherwise we'd loop forever
            #      retrying when the deep path itself can't improve conf).
            already_deep = "deep" in (cached.get("model_used") or "").lower()
            eligible = (
                enable_deep_research
                and indicator_code in DEEP_RESEARCH_INDICATORS
                and (cached.get("confidence") or 0.0) <= DEEP_RESEARCH_CONF_FLOOR
                and not already_deep
            )
            if not eligible:
                return QualIndicatorScore(
                    indicator=indicator_code,
                    score=cached["score"],
                    confidence=cached["confidence"],
                    summary=cached["summary"],
                    evidence=[QualEvidence(**e) for e in cached["evidence"]],
                    scored_at=cached["scored_at"],
                    model_used=cached["model_used"],
                )
            logger.info(
                "Cached %s/%s conf %.2f ≤ floor %.2f & no deep-attempt — "
                "bypassing cache to fire deep-research path.",
                ticker, indicator_code,
                cached.get("confidence") or 0.0, DEEP_RESEARCH_CONF_FLOOR,
            )

    # Gather evidence
    cfg = INDICATORS[indicator_code]
    try:
        evidence_packs = cfg["gather"](ticker)
    except Exception as exc:
        logger.warning("Evidence gather for %s/%s failed: %s", ticker, indicator_code, exc)
        evidence_packs = []

    # First-pass LLM call (Qwen, single-shot, no web search)
    user_prompt = _build_user_prompt(
        ticker, name, sector, indicator_code, cfg["rubric"], evidence_packs
    )
    output, cost = _call_qwen_indicator(_RUBRIC_SHARED_INSTRUCTIONS, user_prompt)
    if output is None:
        return None

    final_score = output.score
    final_conf = output.confidence
    final_summary = output.summary
    final_evidence = [e.model_dump() for e in output.evidence]
    model_used = QUAL_MODEL_NAME

    # ── Priority 3: deep-research escalation for low-confidence outcomes ─
    # Hard cap: skip if we've already done DEEP_RESEARCH_MAX_PER_TICKER
    # escalations for this assess_qualitative() run. Without the cap, a
    # ticker with many low-conf indicators (NVDA hit this) burns 10-15+ min
    # cumulatively in deep research and ties up the parallel pool.
    if (
        enable_deep_research
        and indicator_code in DEEP_RESEARCH_INDICATORS
        and final_conf <= DEEP_RESEARCH_CONF_FLOOR
        and _deep_research_count_get() < DEEP_RESEARCH_MAX_PER_TICKER
    ):
        n = _deep_research_count_inc()
        logger.info(
            "Indicator %s/%s first-pass conf %.2f ≤ floor %.2f — escalating to "
            "deep-research (Qwen web search) [budget %d/%d].",
            ticker, indicator_code, final_conf, DEEP_RESEARCH_CONF_FLOOR,
            n, DEEP_RESEARCH_MAX_PER_TICKER,
        )
        try:
            deep = deep_research_indicator(
                ticker=ticker,
                name=name,
                sector=sector,
                indicator_code=indicator_code,
                indicator_label=cfg.get("label", indicator_code),
                rubric=cfg["rubric"],
                initial_score=final_score,
                initial_confidence=final_conf,
                initial_summary=final_summary,
                initial_evidence=final_evidence,
            )
            if deep and deep.get("confidence", 0.0) > final_conf:
                # Adopt the deeper finding (higher confidence)
                logger.info(
                    "Deep-research for %s/%s improved conf %.2f → %.2f "
                    "(score %d → %d).",
                    ticker, indicator_code, final_conf, deep["confidence"],
                    final_score, deep["score"],
                )
                final_score = deep["score"]
                final_conf = deep["confidence"]
                final_summary = (
                    f"{deep.get('summary','')} "
                    f"[deep-research: {deep.get('reasoning','')[:120]}]"
                ).strip()
                # Convert deep evidence to the QualEvidence shape
                final_evidence = [
                    {
                        "source": e.get("source", "web search"),
                        "quote": e.get("quote", ""),
                        "date": e.get("date"),
                        "url": e.get("url"),
                    }
                    for e in (deep.get("evidence") or [])
                ]
                model_used = f"{QUAL_MODEL_NAME}+deep_web_search"
                cost += 0.05  # rough Qwen deep-search call estimate
            elif deep:
                logger.info(
                    "Deep-research for %s/%s did not improve conf "
                    "(deep %.2f ≤ first-pass %.2f) — keeping first pass.",
                    ticker, indicator_code, deep.get("confidence", 0.0), final_conf,
                )
        except Exception as exc:
            logger.warning(
                "Deep-research escalation for %s/%s failed: %s — falling back.",
                ticker, indicator_code, exc,
            )

    scored_at = datetime.now(timezone.utc).isoformat()
    # Persist to cache (always store the BEST result we have)
    try:
        save_qualitative_score(
            ticker=ticker,
            indicator=indicator_code,
            score=final_score,
            confidence=final_conf,
            summary=final_summary,
            evidence=final_evidence,
            model_used=model_used,
            cost_usd=cost,
            scored_at=scored_at,
        )
    except Exception as exc:
        logger.warning("Persist qualitative score failed: %s", exc)

    return QualIndicatorScore(
        indicator=indicator_code,
        score=final_score,
        confidence=final_conf,
        summary=final_summary,
        evidence=[QualEvidence(**e) for e in final_evidence],
        scored_at=scored_at,
        model_used=model_used,
    )


# ─── Full assessment (parallelized across indicators) ─────────────────────


def assess_qualitative(
    ticker: str,
    name: str,
    sector: str | None,
    quant_passes_gate: bool,
    quant_composite: float,
    indicators: Optional[list[str]] = None,
    force_refresh: bool = False,
    max_workers: int = 4,
    per_indicator_timeout_s: int = 240,   # 4 min — first-pass (2 min) + 1 deep escalation (up to 3 min) capped
) -> QualitativeAssessment:
    """
    Score all (or a subset of) qualitative indicators for one ticker and
    derive the composite + conviction label.

    Per-indicator timeout enforced via concurrent.futures.TimeoutError so
    a single hung Qwen call can't stall the entire parallel pool. Per-
    indicator INFO log emitted on each completion so the user can see
    live progress (one log line per indicator).
    """
    import time as _time
    indicator_codes = indicators or list(INDICATORS.keys())
    started_at_ts = _time.time()

    # Reset the per-ticker deep-research escalation counter at the start
    # of every assessment, so DEEP_RESEARCH_MAX_PER_TICKER is enforced
    # per-ticker rather than cumulatively across the process lifetime.
    _deep_research_count_reset()

    scored: dict[str, QualIndicatorScore] = {}
    total_cost = 0.0
    incomplete = False

    # Use explicit try/finally instead of `with ThreadPoolExecutor() as ex`
    # — the context manager's __exit__ calls shutdown(wait=True) which
    # blocks until all RUNNING futures complete. With force_qual hitting
    # the per-indicator timeout, we want to return PARTIAL results
    # immediately, not wait an extra 2-3 min for stragglers to fail
    # their own internal timeouts.
    ex = ThreadPoolExecutor(max_workers=max_workers)
    try:
        futures = {
            ex.submit(score_indicator, ticker, name, sector, code, force_refresh): code
            for code in indicator_codes
        }
        try:
            # Total cap = per_indicator_timeout_s * ceil(N / max_workers)
            # i.e. 240s × ceil(10/4) = 240 × 3 = 720s = 12 min worst case
            total_timeout = per_indicator_timeout_s * (
                (len(futures) + max_workers - 1) // max_workers
            )
            for fut in as_completed(futures, timeout=total_timeout):
                code = futures[fut]
                try:
                    result = fut.result(timeout=per_indicator_timeout_s)
                except Exception as exc:
                    logger.exception(
                        "score_indicator %s failed: %s", code, exc,
                    )
                    incomplete = True
                    continue
                if result is None:
                    incomplete = True
                    continue
                scored[code] = result
                elapsed_min = (_time.time() - started_at_ts) / 60
                deep_marker = " ★DEEP" if "deep" in (result.model_used or "").lower() else ""
                logger.info(
                    "Indicator %d/%d done — %s/%s = %d/5 conf %.0f%%%s "
                    "(elapsed %.1f min, %d remaining)",
                    len(scored), len(indicator_codes),
                    ticker, code,
                    result.score, result.confidence * 100, deep_marker,
                    elapsed_min,
                    len(indicator_codes) - len(scored),
                )
        except Exception as exc:
            logger.warning(
                "assess_qualitative for %s hit outer timeout after %.1f min "
                "(%d/%d indicators scored): %s — abandoning unfinished",
                ticker, (_time.time() - started_at_ts) / 60,
                len(scored), len(indicator_codes), exc,
            )
            incomplete = True
    finally:
        # cancel_futures cancels PENDING futures (not yet picked up).
        # wait=False returns immediately; orphaned worker threads finish
        # on their own and write to the cache when their internal call-
        # level timeout fires (120s ChatOpenAI / 180s OpenAI client).
        # Those late writes are harmless — they just populate the cache
        # for the next force-rescore.
        try:
            ex.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            ex.shutdown(wait=False)  # pre-3.9 fallback

    if not scored:
        return QualitativeAssessment(
            indicators={},
            composite=0,
            max_possible=0,
            composite_normalized=0.0,
            conviction_label="PASS",
            assessed_at=datetime.now(timezone.utc).isoformat(),
            cost_usd=0.0,
            incomplete=True,
        )

    composite = sum(s.score for s in scored.values())
    max_possible = 5 * len(scored)
    normalized = composite / max_possible if max_possible else 0.0

    # Map onto the 5-bucket conviction label.
    # Thresholds calibrated for the 3-indicator v1 (max=15); future-proofed
    # by using normalized values rather than raw cutoffs.
    # qual_strong  ≥ 0.70   (10/15 or 35/50)
    # qual_med     ≥ 0.50   ( 8/15 or 25/50)
    if quant_passes_gate and normalized >= 0.70:
        label: QualConvictionLabel = "EXCEPTIONAL"
    elif quant_passes_gate and normalized >= 0.50:
        label = "BOTH"
    elif quant_passes_gate:
        label = "QUANT-ONLY"
    elif normalized >= 0.70:
        label = "QUAL-ONLY"
    else:
        label = "PASS"

    return QualitativeAssessment(
        indicators=scored,
        composite=composite,
        max_possible=max_possible,
        composite_normalized=normalized,
        conviction_label=label,
        assessed_at=datetime.now(timezone.utc).isoformat(),
        cost_usd=total_cost,
        incomplete=incomplete,
    )


if __name__ == "__main__":
    # Manual smoke:
    #   $env:FMP_API_KEY = "..."
    #   $env:DEEP_RESEARCH_API_KEY = "..."   # for Qwen
    #   .\.venv\Scripts\python.exe -m src.research_ideas.complacency.qualitative CRWD
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    tkr = sys.argv[1] if len(sys.argv) > 1 else "CRWD"
    print(f"\nAssessing {tkr} on {len(INDICATORS)} qualitative indicators...\n")
    a = assess_qualitative(
        ticker=tkr,
        name=tkr,
        sector="Technology",
        quant_passes_gate=True,    # assume gate-passer for smoke
        quant_composite=7.0,
    )
    print(f"COMPOSITE: {a.composite}/{a.max_possible}  ({a.composite_normalized:.0%})")
    print(f"CONVICTION: {a.conviction_label}")
    print(f"Incomplete: {a.incomplete}\n")
    for code, s in a.indicators.items():
        print(f"  {code:<35s} {s.score}/5   conf={s.confidence:.2f}   {s.summary}")
        for ev in s.evidence[:2]:
            print(f"    └─ [{ev.source}] {ev.quote[:120]}...")
