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


class _LLMIndicatorOutput(BaseModel):
    """JSON-mode output from the qualitative scorer agent."""
    score: int = Field(ge=0, le=5, description="Score 0-5 per the rubric in the prompt")
    confidence: float = Field(ge=0.0, le=1.0, description="0=no confidence, 1=certain. Use < 0.4 if evidence is thin.")
    summary: str = Field(description="One-line takeaway, ≤ 200 chars")
    evidence: list[_LLMEvidence] = Field(
        default_factory=list,
        description="Verbatim evidence supporting the score. Required unless score=0."
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
3. Trust a single high-quality citation (10-K direct quote, transcript Q&A) at face value.
   Otherwise require 2 independent sources.
4. Evidence quotes must be VERBATIM (copy-paste from the provided text).
5. Be conservative — when in doubt, score lower and set lower confidence.

OUTPUT FORMAT (JSON only):
{
  "score": <0-5 integer>,
  "confidence": <0-1 float>,
  "summary": "<≤ 200 char takeaway>",
  "evidence": [
    {"source": "<source label>", "quote": "<verbatim ≤ 300 chars>", "date": "yyyy-mm-dd"}
  ]
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


def _gather_evidence_A2(ticker: str) -> list[dict]:
    """Catalyst proximity needs: next earnings date, recent transcript Q&A (if available),
    recent news. Transcript endpoint is plan-gated; degrades to more news."""
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
    transcript = _fetch_latest_transcript(ticker)
    if transcript:
        packs.append({
            "source": transcript["source"],
            "date": transcript["date"],
            "text": transcript["content_snippet"],
        })
        news_limit = 4
    else:
        # Compensate for missing transcript with more news context.
        news_limit = 8
    news = _fetch_recent_news(ticker, days=90, limit=news_limit)
    for n in news:
        packs.append({
            "source": n["source"],
            "date": n["date"],
            "text": f"{n['title']}\n\n{n['text_snippet']}",
        })
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
    """Accounting aggressiveness: 10-K sections + transcript (if available) + news fallback."""
    packs: list[dict] = []
    risk = _fetch_10k_risk_factors(ticker)
    if risk:
        packs.append({
            "source": risk["source"],
            "date": risk["date"],
            "text": risk["content_snippet"],
        })
    transcript = _fetch_latest_transcript(ticker)
    if transcript:
        packs.append({
            "source": transcript["source"],
            "date": transcript["date"],
            "text": transcript["content_snippet"],
        })
    # Fallback: filtered news with accounting keywords.
    if not risk and not transcript:
        for n in _fetch_recent_news(ticker, days=180, limit=10):
            body = (n["title"] + " " + n["text_snippet"]).lower()
            if any(k in body for k in (
                "restat", "audit", "goodwill", "impair", "guidance",
                "deferred", "channel stuffing", "sec inquiry", "accounting",
                "cfo", "10-k", "non-gaap", "revenue recogn",
            )):
                packs.append({
                    "source": n["source"], "date": n["date"],
                    "text": f"{n['title']}\n\n{n['text_snippet']}",
                })
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
    packs: list[dict] = []
    transcript = _fetch_latest_transcript(ticker)
    if transcript:
        packs.append({
            "source": transcript["source"],
            "date": transcript["date"],
            "text": transcript["content_snippet"],
        })
    news = _fetch_recent_news(ticker, days=90, limit=6)
    for n in news:
        # Prioritize news with management / executive keywords
        body = (n["title"] + " " + n["text_snippet"]).lower()
        if any(k in body for k in ("ceo", "cfo", "executive", "resign", "depart", "restat", "sec", "lawsuit", "scandal", "investig")):
            packs.append({
                "source": n["source"],
                "date": n["date"],
                "text": f"{n['title']}\n\n{n['text_snippet']}",
            })
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
    """Single-thesis: news (sell-side narrative quotes) + transcript if available."""
    packs: list[dict] = []
    for n in _fetch_recent_news(ticker, days=90, limit=8):
        packs.append({
            "source": n["source"], "date": n["date"],
            "text": f"{n['title']}\n\n{n['text_snippet']}",
        })
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
    for n in _fetch_recent_news(ticker, days=60, limit=4):
        packs.append({
            "source": n["source"], "date": n["date"],
            "text": f"{n['title']}\n\n{n['text_snippet']}",
        })
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
    """B1 — risk-factor section often discloses material customers; news fills gaps."""
    packs: list[dict] = []
    risk = _fetch_10k_risk_factors(ticker)
    if risk:
        packs.append({
            "source": risk["source"], "date": risk["date"],
            "text": risk["content_snippet"],
        })
    for n in _fetch_recent_news(ticker, days=90, limit=4):
        body = (n["title"] + " " + n["text_snippet"]).lower()
        if any(k in body for k in ("customer", "client", "concentration", "contract", "rfp", "renewal", "churn")):
            packs.append({
                "source": n["source"], "date": n["date"],
                "text": f"{n['title']}\n\n{n['text_snippet']}",
            })
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
    """B2 — news (competitor mentions) + 10-K Competition section if present."""
    packs: list[dict] = []
    for n in _fetch_recent_news(ticker, days=120, limit=8):
        body = (n["title"] + " " + n["text_snippet"]).lower()
        if any(k in body for k in (
            "competitor", "competition", "ai-native", "disrupt", "replac",
            "alternative", "open-source", "open source", "hyperscaler",
            "agent", "automat",
        )):
            packs.append({
                "source": n["source"], "date": n["date"],
                "text": f"{n['title']}\n\n{n['text_snippet']}",
            })
    risk = _fetch_10k_risk_factors(ticker)
    if risk:
        packs.append({
            "source": risk["source"], "date": risk["date"],
            "text": risk["content_snippet"][:4000],
        })
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
    packs: list[dict] = []
    for n in _fetch_recent_news(ticker, days=120, limit=6):
        body = (n["title"] + " " + n["text_snippet"]).lower()
        if any(k in body for k in (
            "pricing", "price cut", "discount", "nrr", "net retention",
            "renewal", "guidance", "billings", "deferred revenue", "ARR",
        )):
            packs.append({
                "source": n["source"], "date": n["date"],
                "text": f"{n['title']}\n\n{n['text_snippet']}",
            })
    risk = _fetch_10k_risk_factors(ticker)
    if risk:
        packs.append({
            "source": risk["source"], "date": risk["date"],
            "text": risk["content_snippet"][:4000],
        })
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
    packs: list[dict] = []
    risk = _fetch_10k_risk_factors(ticker)
    if risk:
        packs.append({
            "source": risk["source"], "date": risk["date"],
            "text": risk["content_snippet"],
        })
    for n in _fetch_recent_news(ticker, days=270, limit=6):
        body = (n["title"] + " " + n["text_snippet"]).lower()
        if any(k in body for k in (
            "restat", "auditor", "10-k", "kam", "going concern",
            "sec inquiry", "sec subpoena", "subpoena", "cfo",
            "non-gaap", "segment reporting", "material weakness",
        )):
            packs.append({
                "source": n["source"], "date": n["date"],
                "text": f"{n['title']}\n\n{n['text_snippet']}",
            })
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
    """D2 — needs structured insider trade detail with owner role + transaction type."""
    packs: list[dict] = []
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


def score_indicator(
    ticker: str,
    name: str,
    sector: str | None,
    indicator_code: str,
    force_refresh: bool = False,
) -> Optional[QualIndicatorScore]:
    """
    Score one indicator for one ticker. Uses 7-day cache unless force_refresh.
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
            return QualIndicatorScore(
                indicator=indicator_code,
                score=cached["score"],
                confidence=cached["confidence"],
                summary=cached["summary"],
                evidence=[QualEvidence(**e) for e in cached["evidence"]],
                scored_at=cached["scored_at"],
                model_used=cached["model_used"],
            )

    # Gather evidence
    cfg = INDICATORS[indicator_code]
    try:
        evidence_packs = cfg["gather"](ticker)
    except Exception as exc:
        logger.warning("Evidence gather for %s/%s failed: %s", ticker, indicator_code, exc)
        evidence_packs = []

    # LLM call
    user_prompt = _build_user_prompt(
        ticker, name, sector, indicator_code, cfg["rubric"], evidence_packs
    )
    output, cost = _call_qwen_indicator(_RUBRIC_SHARED_INSTRUCTIONS, user_prompt)
    if output is None:
        return None

    scored_at = datetime.now(timezone.utc).isoformat()
    # Persist to cache
    try:
        save_qualitative_score(
            ticker=ticker,
            indicator=indicator_code,
            score=output.score,
            confidence=output.confidence,
            summary=output.summary,
            evidence=[e.model_dump() for e in output.evidence],
            model_used=QUAL_MODEL_NAME,
            cost_usd=cost,
            scored_at=scored_at,
        )
    except Exception as exc:
        logger.warning("Persist qualitative score failed: %s", exc)

    return QualIndicatorScore(
        indicator=indicator_code,
        score=output.score,
        confidence=output.confidence,
        summary=output.summary,
        evidence=[QualEvidence(**e.model_dump()) for e in output.evidence],
        scored_at=scored_at,
        model_used=QUAL_MODEL_NAME,
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
    max_workers: int = 3,
) -> QualitativeAssessment:
    """
    Score all (or a subset of) qualitative indicators for one ticker and
    derive the composite + conviction label.
    """
    indicator_codes = indicators or list(INDICATORS.keys())

    scored: dict[str, QualIndicatorScore] = {}
    total_cost = 0.0
    incomplete = False

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {
            ex.submit(score_indicator, ticker, name, sector, code, force_refresh): code
            for code in indicator_codes
        }
        for fut in as_completed(futures):
            code = futures[fut]
            try:
                result = fut.result()
            except Exception as exc:
                logger.exception("score_indicator %s failed: %s", code, exc)
                incomplete = True
                continue
            if result is None:
                incomplete = True
                continue
            scored[code] = result

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
