"""
src/research_ideas/complacency/evidence_sources.py
====================================================
Better evidence sources for the qualitative scorer. Replaces the
generic /news/stock + table-only financial-reports-json gatherers
that produced 0/5 scores on most indicators.

Three new sources:

  1. SEC EDGAR direct (FREE)
       Pulls full 10-K HTML, extracts Item 1A (Risk Factors), Item 7
       (MD&A), Item 9A (Controls), and Critical Audit Matters as prose.
       This is the actual narrative content the LLM rubrics need.

  2. Tavily targeted search (already wired via TAVILY_API_KEY)
       Per-indicator focused queries with relevance-ranked snippets.
       D1: 'CEO OR CFO scandal OR departure OR investigation'
       C1: 'restatement OR auditor change OR going concern'
       etc.

  3. Computed financial signals (deterministic, no LLM)
       Goodwill/equity %, DSO trend, deferred revenue mismatch, etc.
       Passed as numeric inputs alongside prose so the LLM can score
       mechanically against rubric anchors.
"""
from __future__ import annotations

import logging
import os
import re
import time
from datetime import date, timedelta
from typing import Optional

import requests

from src.tools.api import _fmp_get, _safe_float, _STABLE


logger = logging.getLogger(__name__)


# Required SEC User-Agent (free; SEC requires identification).
_SEC_USER_AGENT = os.environ.get(
    "SEC_USER_AGENT",
    "AI Hedge Fund / Complacency Scorer research@example.com",
)


# ─── 1. SEC EDGAR — full 10-K text extraction ─────────────────────────────


def _sec_get_cik(ticker: str) -> Optional[str]:
    """Resolve ticker → CIK via SEC's ticker mapping. Cached in-memory."""
    cik = _CIK_CACHE.get(ticker.upper())
    if cik:
        return cik
    try:
        r = requests.get(
            "https://www.sec.gov/files/company_tickers.json",
            headers={"User-Agent": _SEC_USER_AGENT},
            timeout=15,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        for _idx, row in data.items():
            if str(row.get("ticker", "")).upper() == ticker.upper():
                cik_int = int(row["cik_str"])
                cik_padded = f"{cik_int:010d}"
                _CIK_CACHE[ticker.upper()] = cik_padded
                return cik_padded
    except Exception as exc:
        logger.warning("SEC CIK lookup for %s failed: %s", ticker, exc)
    return None


_CIK_CACHE: dict[str, str] = {}


def _sec_latest_10k_url(cik: str) -> Optional[tuple[str, str]]:
    """Returns (accession_number, primary_doc_url) for the latest 10-K."""
    try:
        r = requests.get(
            f"https://data.sec.gov/submissions/CIK{cik}.json",
            headers={"User-Agent": _SEC_USER_AGENT},
            timeout=15,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        recent = data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        accessions = recent.get("accessionNumber", [])
        primary_docs = recent.get("primaryDocument", [])
        dates = recent.get("filingDate", [])
        for i, form in enumerate(forms):
            if form != "10-K":
                continue
            accession = accessions[i]
            primary = primary_docs[i]
            filed = dates[i]
            acc_no_dashes = accession.replace("-", "")
            url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_no_dashes}/{primary}"
            return accession, url, filed
    except Exception as exc:
        logger.warning("SEC submissions for CIK %s failed: %s", cik, exc)
    return None


def _strip_html(html: str) -> str:
    """Crude but fast HTML → plain text."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "table", "img"]):
            tag.decompose()
        text = soup.get_text(" ", strip=True)
        # Collapse multiple whitespace
        return re.sub(r"\s+", " ", text)
    except Exception:
        # Fallback: strip tags with regex
        return re.sub(r"<[^>]+>", " ", html)


_SECTION_REGEX = {
    "risk_factors": re.compile(
        r"item\s*1a[\s\.\)]+risk\s+factors(.*?)(?:item\s*1b|item\s*2[\s\.\)])",
        re.IGNORECASE | re.DOTALL,
    ),
    "mda": re.compile(
        r"item\s*7[\s\.\)]+\s*management.{0,80}discussion(.*?)(?:item\s*7a|item\s*8[\s\.\)])",
        re.IGNORECASE | re.DOTALL,
    ),
    "controls": re.compile(
        r"item\s*9a[\s\.\)]+controls(.*?)(?:item\s*9b|item\s*10[\s\.\)])",
        re.IGNORECASE | re.DOTALL,
    ),
    "kam": re.compile(
        r"(?:critical\s+audit\s+matter|key\s+audit\s+matter)(.*?)(?:\/s\/|other\s+matter)",
        re.IGNORECASE | re.DOTALL,
    ),
}


def fetch_sec_10k_sections(
    ticker: str,
    max_section_chars: int = 6000,
) -> dict[str, str]:
    """
    Pull the actual 10-K HTML from EDGAR and extract the prose sections
    that matter for qualitative scoring. Cached per ticker (in-memory).

    Returns dict mapping {section_key: text_snippet}. Empty dict on failure.
    Sections: risk_factors, mda, controls, kam, _filed_date, _source_url.
    """
    cached = _SEC_10K_CACHE.get(ticker.upper())
    if cached is not None:
        return cached

    cik = _sec_get_cik(ticker)
    if not cik:
        _SEC_10K_CACHE[ticker.upper()] = {}
        return {}
    time.sleep(0.15)  # SEC rate limits: 10 req/sec max

    meta = _sec_latest_10k_url(cik)
    if not meta:
        _SEC_10K_CACHE[ticker.upper()] = {}
        return {}
    accession, doc_url, filed_date = meta
    time.sleep(0.15)

    try:
        r = requests.get(
            doc_url,
            headers={"User-Agent": _SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"},
            timeout=30,
        )
        if r.status_code != 200:
            logger.warning("SEC doc fetch %s -> %d", doc_url, r.status_code)
            _SEC_10K_CACHE[ticker.upper()] = {}
            return {}
        html = r.text
    except Exception as exc:
        logger.warning("SEC doc fetch %s failed: %s", doc_url, exc)
        _SEC_10K_CACHE[ticker.upper()] = {}
        return {}

    text = _strip_html(html)

    sections: dict[str, str] = {
        "_filed_date": filed_date,
        "_source_url": doc_url,
    }
    for key, pat in _SECTION_REGEX.items():
        m = pat.search(text)
        if m:
            snippet = m.group(1).strip()[:max_section_chars]
            if len(snippet) > 200:
                sections[key] = snippet

    _SEC_10K_CACHE[ticker.upper()] = sections
    return sections


_SEC_10K_CACHE: dict[str, dict] = {}


# ─── 2. Tavily targeted search ────────────────────────────────────────────


def _get_tavily_client():
    key = os.environ.get("TAVILY_API_KEY")
    if not key:
        return None
    try:
        from tavily import TavilyClient
        return TavilyClient(api_key=key)
    except Exception as exc:
        logger.warning("Tavily client init failed: %s", exc)
        return None


def tavily_search(
    query: str,
    days: int = 90,
    max_results: int = 5,
    search_depth: str = "basic",
) -> list[dict]:
    """
    Targeted news/web search via Tavily. Returns a list of:
      [{title, url, content, published_date, score}, ...]
    Returns [] on failure (key missing, network error).
    """
    client = _get_tavily_client()
    if client is None:
        return []
    try:
        result = client.search(
            query=query,
            search_depth=search_depth,
            max_results=max_results,
            days=days,
            include_answer=False,
            include_raw_content=False,
        )
        out: list[dict] = []
        for r in result.get("results", []) or []:
            out.append({
                "title": r.get("title", ""),
                "url": r.get("url"),
                "content": (r.get("content") or "")[:1000],
                "published_date": r.get("published_date") or "",
                "score": r.get("score") or 0.0,
            })
        return out
    except Exception as exc:
        logger.warning("Tavily search failed for %r: %s", query, exc)
        return []


# ─── 3. Computed financial signals ────────────────────────────────────────


def compute_financial_signals(ticker: str) -> dict:
    """
    Deterministic per-ticker financial signals derived from FMP statements.
    Used as numeric inputs alongside prose evidence for accounting + pricing
    indicators (C2, B3, B1).

    Returns dict of named ratios, all Optional[float]:
      goodwill_to_equity, intangibles_to_equity, dso_days, dso_delta_3y,
      gross_margin_pct, gross_margin_delta_3y, deferred_rev_to_revenue,
      revenue_cagr_3y, capex_to_revenue, capitalized_software_to_revenue,
      cfo_to_ni_ratio (cash conversion).
    """
    out: dict[str, Optional[float]] = {
        "goodwill_to_equity": None,
        "intangibles_to_equity": None,
        "dso_days": None,
        "dso_delta_3y": None,
        "gross_margin_pct": None,
        "gross_margin_delta_3y": None,
        "deferred_rev_to_revenue": None,
        "revenue_cagr_3y": None,
        "capex_to_revenue": None,
        "cfo_to_ni_ratio": None,
    }

    # Pull last 4 years of statements
    income = _fmp_get(
        f"{_STABLE}/income-statement",
        {"symbol": ticker, "period": "annual", "limit": 4},
        api_key=None, uncap=True,
    ) or []
    balance = _fmp_get(
        f"{_STABLE}/balance-sheet-statement",
        {"symbol": ticker, "period": "annual", "limit": 4},
        api_key=None, uncap=True,
    ) or []
    cashflow = _fmp_get(
        f"{_STABLE}/cash-flow-statement",
        {"symbol": ticker, "period": "annual", "limit": 4},
        api_key=None, uncap=True,
    ) or []

    if not income or not balance:
        return out
    income.sort(key=lambda r: r.get("date", ""))    # oldest → newest
    balance.sort(key=lambda r: r.get("date", ""))
    cashflow.sort(key=lambda r: r.get("date", ""))

    latest_inc = income[-1]
    latest_bal = balance[-1]
    latest_cf = cashflow[-1] if cashflow else {}

    # Goodwill / Intangibles vs equity
    goodwill = _safe_float(latest_bal.get("goodwill"))
    intangibles = _safe_float(latest_bal.get("intangibleAssets"))
    equity = _safe_float(latest_bal.get("totalStockholdersEquity"))
    if goodwill is not None and equity and equity > 0:
        out["goodwill_to_equity"] = goodwill / equity
    if intangibles is not None and equity and equity > 0:
        out["intangibles_to_equity"] = intangibles / equity

    # DSO and 3yr trend
    revenue = _safe_float(latest_inc.get("revenue"))
    receivables = _safe_float(latest_bal.get("accountsReceivables") or latest_bal.get("netReceivables"))
    if revenue and receivables and revenue > 0:
        out["dso_days"] = receivables / revenue * 365
    if len(income) >= 4 and len(balance) >= 4:
        old_rev = _safe_float(income[0].get("revenue"))
        old_rec = _safe_float(balance[0].get("accountsReceivables") or balance[0].get("netReceivables"))
        if old_rev and old_rec and old_rev > 0:
            old_dso = old_rec / old_rev * 365
            if out["dso_days"]:
                out["dso_delta_3y"] = out["dso_days"] - old_dso

    # Gross margin + trend
    gp = _safe_float(latest_inc.get("grossProfit"))
    if gp is not None and revenue and revenue > 0:
        out["gross_margin_pct"] = gp / revenue
    if len(income) >= 4:
        old_inc = income[0]
        old_gp = _safe_float(old_inc.get("grossProfit"))
        old_rev = _safe_float(old_inc.get("revenue"))
        if old_gp is not None and old_rev and old_rev > 0:
            old_gm = old_gp / old_rev
            if out["gross_margin_pct"] is not None:
                out["gross_margin_delta_3y"] = out["gross_margin_pct"] - old_gm

    # Deferred revenue / revenue
    deferred = _safe_float(
        latest_bal.get("deferredRevenue") or latest_bal.get("deferredRevenueNonCurrent")
    )
    if deferred is not None and revenue and revenue > 0:
        out["deferred_rev_to_revenue"] = deferred / revenue

    # Revenue CAGR 3y
    if len(income) >= 4 and revenue:
        old_rev = _safe_float(income[0].get("revenue"))
        if old_rev and old_rev > 0:
            years = 3
            out["revenue_cagr_3y"] = (revenue / old_rev) ** (1 / years) - 1

    # Capex / revenue
    capex = _safe_float(latest_cf.get("capitalExpenditure"))
    if capex is not None and revenue and revenue > 0:
        out["capex_to_revenue"] = abs(capex) / revenue

    # CFO / NI cash conversion
    cfo = _safe_float(latest_cf.get("operatingCashFlow") or latest_cf.get("netCashProvidedByOperatingActivities"))
    ni = _safe_float(latest_inc.get("netIncome"))
    if cfo is not None and ni and ni != 0:
        out["cfo_to_ni_ratio"] = cfo / ni

    return out


# ─── 4. FMP Stock News (per-ticker, fuller text than legacy /news/stock) ──


def fetch_stock_news(ticker: str, days: int = 90, limit: int = 8) -> list[dict]:
    """
    /stable/news/stock — ticker-filtered news with title + full snippet + URL.
    Richer than the legacy stub feed; used for A1 / A2 / A3 / C1 / C2 to give
    the LLM real article text instead of headlines only.
    """
    today = date.today()
    since = today - timedelta(days=days)
    data = _fmp_get(
        f"{_STABLE}/news/stock",
        {"symbols": ticker, "from": since.isoformat(), "to": today.isoformat(), "limit": limit},
        api_key=None, uncap=True,
    )
    if not isinstance(data, list):
        return []
    out: list[dict] = []
    for r in data[:limit]:
        out.append({
            "source": f"FMP Stock News — {r.get('publisher') or r.get('site') or 'unknown'}",
            "date": (r.get("publishedDate") or "")[:10],
            "title": r.get("title") or "",
            "url": r.get("url"),
            "text": (r.get("text") or "")[:1200],
        })
    return out


# ─── 5. FMP Press Releases (M&A / class-action / earnings releases) ───────


def fetch_press_releases(ticker: str, days: int = 270, limit: int = 8) -> list[dict]:
    """
    /stable/news/press-releases — ticker-filtered official corporate
    press releases. Includes class-action investigations, M&A
    announcements, earnings releases, governance changes. Often the
    earliest signal for C1 (disclosure deterioration) and D1 (management
    red flags). Default 270-day window since corporate actions are slower-
    moving than general news.
    """
    today = date.today()
    since = today - timedelta(days=days)
    data = _fmp_get(
        f"{_STABLE}/news/press-releases",
        {"symbols": ticker, "from": since.isoformat(), "to": today.isoformat(), "limit": limit},
        api_key=None, uncap=True,
    )
    if not isinstance(data, list):
        return []
    out: list[dict] = []
    for r in data[:limit]:
        out.append({
            "source": f"Press release — {r.get('publisher') or r.get('site') or 'unknown'}",
            "date": (r.get("publishedDate") or "")[:10],
            "title": r.get("title") or "",
            "url": r.get("url"),
            "text": (r.get("text") or "")[:1500],
        })
    return out


def format_financial_signals_for_prompt(sig: dict) -> str:
    """Render financial signals as a compact bullet list for the LLM prompt."""
    lines = ["DERIVED FINANCIAL SIGNALS (from FMP statements, last 4 fiscal years):"]
    def f(v, fmt):
        return fmt.format(v) if v is not None else "n/a"
    lines.append(f"  Goodwill / Equity         : {f(sig.get('goodwill_to_equity'),    '{:.1%}')}")
    lines.append(f"  Intangibles / Equity      : {f(sig.get('intangibles_to_equity'), '{:.1%}')}")
    lines.append(f"  DSO (days)                : {f(sig.get('dso_days'),              '{:.0f} days')}")
    lines.append(f"  DSO Δ 3y                  : {f(sig.get('dso_delta_3y'),          '{:+.0f} days')}")
    lines.append(f"  Gross margin              : {f(sig.get('gross_margin_pct'),      '{:.1%}')}")
    lines.append(f"  Gross margin Δ 3y         : {f(sig.get('gross_margin_delta_3y'), '{:+.1%}')}")
    lines.append(f"  Deferred rev / Revenue    : {f(sig.get('deferred_rev_to_revenue'),'{:.1%}')}")
    lines.append(f"  Revenue CAGR 3y           : {f(sig.get('revenue_cagr_3y'),       '{:.1%}')}")
    lines.append(f"  Capex / Revenue           : {f(sig.get('capex_to_revenue'),      '{:.1%}')}")
    lines.append(f"  CFO / NI (cash conversion): {f(sig.get('cfo_to_ni_ratio'),       '{:.2f}x')}")
    return "\n".join(lines)
