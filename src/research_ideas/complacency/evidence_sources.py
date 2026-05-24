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


# ─── 3b. SEC EDGAR — recent 8-K filings (current events) ─────────────────


_8K_ITEM_DESCRIPTIONS = {
    "1.01": "Material Definitive Agreement",
    "1.02": "Termination of Material Agreement",
    "1.03": "Bankruptcy or Receivership",
    "2.01": "Acquisition or Disposition of Assets",
    "2.04": "Triggering Events Acceleration / Direct Financial Obligation",
    "2.05": "Material Costs from Exit / Disposal",
    "2.06": "Material Impairments",
    "3.01": "Notice of Delisting / Failure to Satisfy Listing Standard",
    "4.01": "Auditor Change",
    "4.02": "Non-Reliance on Previously Issued Financial Statements (RESTATEMENT)",
    "5.02": "Departure / Election of Directors / Officers",
    "5.03": "Amendments to Articles / Bylaws",
    "7.01": "Regulation FD Disclosure",
    "8.01": "Other Events",
}


def fetch_sec_recent_8k(ticker: str, days: int = 180, limit: int = 12) -> list[dict]:
    """
    Pull recent 8-K filings (current events) from SEC EDGAR. Each return
    item is a dict with the items disclosed (e.g., Item 5.02 = exec
    departure, Item 4.02 = restatement of prior financials).

    Returns [] on fetch failure or no CIK match.
    """
    cik = _sec_get_cik(ticker)
    if not cik:
        return []
    time.sleep(0.15)
    cutoff = (date.today() - timedelta(days=days)).isoformat()

    try:
        r = requests.get(
            f"https://data.sec.gov/submissions/CIK{cik}.json",
            headers={"User-Agent": _SEC_USER_AGENT},
            timeout=15,
        )
        if r.status_code != 200:
            return []
        data = r.json()
    except Exception as exc:
        logger.warning("SEC 8-K fetch for %s failed: %s", ticker, exc)
        return []

    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accessions = recent.get("accessionNumber", [])
    primary_docs = recent.get("primaryDocument", [])
    dates = recent.get("filingDate", [])
    items_list = recent.get("items", [])

    out: list[dict] = []
    for i, form in enumerate(forms):
        if form != "8-K":
            continue
        filed = dates[i] if i < len(dates) else ""
        if filed < cutoff:
            continue
        accession = accessions[i] if i < len(accessions) else ""
        primary = primary_docs[i] if i < len(primary_docs) else ""
        items_raw = items_list[i] if i < len(items_list) else ""

        # Parse comma-separated item codes (e.g., "5.02,9.01")
        items_parsed: list[dict] = []
        for code in (items_raw or "").split(","):
            code = code.strip()
            if not code:
                continue
            label = _8K_ITEM_DESCRIPTIONS.get(code, code)
            items_parsed.append({"code": code, "label": label})

        acc_no_dashes = accession.replace("-", "")
        url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_no_dashes}/{primary}" if primary else None
        out.append({
            "filed_date": filed,
            "items": items_parsed,
            "url": url,
        })
        if len(out) >= limit:
            break
    return out


def format_8k_filings_for_prompt(filings: list[dict]) -> str:
    """Render 8-K list as a compact bullet pack for the LLM."""
    if not filings:
        return ""
    lines = ["RECENT 8-K FILINGS (last ~6 months — SEC EDGAR):"]
    for f in filings:
        items_str = "; ".join(f"{it['code']} ({it['label']})" for it in f.get("items", [])) or "—"
        lines.append(f"  {f['filed_date']}  items: {items_str}")
    return "\n".join(lines)


# ─── 3c. SEC EDGAR — DEF 14A proxy statement (compensation + governance) ──


def fetch_sec_def14a_excerpt(ticker: str, max_chars: int = 8000) -> Optional[dict]:
    """
    Latest DEF 14A (annual proxy statement). Contains executive
    compensation tables, CEO pay ratio, related-party transactions,
    board independence — critical for D1.
    """
    cached = _DEF14A_CACHE.get(ticker.upper())
    if cached is not None:
        return cached

    cik = _sec_get_cik(ticker)
    if not cik:
        _DEF14A_CACHE[ticker.upper()] = None
        return None
    time.sleep(0.15)

    try:
        r = requests.get(
            f"https://data.sec.gov/submissions/CIK{cik}.json",
            headers={"User-Agent": _SEC_USER_AGENT},
            timeout=15,
        )
        if r.status_code != 200:
            _DEF14A_CACHE[ticker.upper()] = None
            return None
        data = r.json()
    except Exception as exc:
        logger.warning("SEC DEF 14A submissions for %s failed: %s", ticker, exc)
        _DEF14A_CACHE[ticker.upper()] = None
        return None

    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accessions = recent.get("accessionNumber", [])
    primary_docs = recent.get("primaryDocument", [])
    dates = recent.get("filingDate", [])
    target_idx = None
    for i, form in enumerate(forms):
        if form == "DEF 14A":
            target_idx = i
            break
    if target_idx is None:
        _DEF14A_CACHE[ticker.upper()] = None
        return None

    accession = accessions[target_idx]
    primary = primary_docs[target_idx]
    filed = dates[target_idx]
    acc_no_dashes = accession.replace("-", "")
    url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_no_dashes}/{primary}"
    time.sleep(0.15)

    try:
        r = requests.get(
            url,
            headers={"User-Agent": _SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"},
            timeout=30,
        )
        if r.status_code != 200:
            _DEF14A_CACHE[ticker.upper()] = None
            return None
        text = _strip_html(r.text)
    except Exception as exc:
        logger.warning("SEC DEF 14A doc fetch %s failed: %s", url, exc)
        _DEF14A_CACHE[ticker.upper()] = None
        return None

    # Extract sections of interest by keyword anchors.
    sections: dict[str, str] = {}
    for key, pat in {
        "executive_compensation": re.compile(
            r"(executive\s+compensation.{0,40}(?:overview|discussion|tables?))(.{0,8000})",
            re.IGNORECASE | re.DOTALL,
        ),
        "ceo_pay_ratio": re.compile(
            r"(ceo\s+pay\s+ratio|chief\s+executive\s+officer\s+pay\s+ratio)(.{0,2500})",
            re.IGNORECASE | re.DOTALL,
        ),
        "related_party": re.compile(
            r"(related[-\s]?party\s+transactions?|certain\s+relationships)(.{0,4000})",
            re.IGNORECASE | re.DOTALL,
        ),
        "board_independence": re.compile(
            r"(director\s+independence|board\s+composition)(.{0,3000})",
            re.IGNORECASE | re.DOTALL,
        ),
    }.items():
        m = pat.search(text)
        if m:
            snippet = (m.group(1) + m.group(2)).strip()[:max_chars // 3]
            if len(snippet) > 200:
                sections[key] = snippet

    result = {
        "_filed_date": filed,
        "_source_url": url,
        "sections": sections,
    }
    _DEF14A_CACHE[ticker.upper()] = result
    return result


_DEF14A_CACHE: dict[str, Optional[dict]] = {}


# ─── 3d. SEC EDGAR — Form 144 (proposed insider sales, leading indicator) ──


def fetch_sec_recent_form144(ticker: str, days: int = 90, limit: int = 10) -> list[dict]:
    """
    Form 144 = NOTICE of proposed insider sale (filed BEFORE the sale).
    Strong leading indicator vs Form 4 (post-trade). When CEO/CFO files
    144 for a large dollar amount, that's the canary.

    Note: Form 144 filings are filed under the INSIDER's own EDGAR entry,
    not the company's. Per-issuer queries via EDGAR full-text search
    are heavy. v1: pull from company submissions JSON where they appear
    via 13F-related filings — typically empty for small/mid cap, useful
    for mega caps. Returns [] gracefully when no 144s under the company CIK.
    """
    cik = _sec_get_cik(ticker)
    if not cik:
        return []
    time.sleep(0.15)
    cutoff = (date.today() - timedelta(days=days)).isoformat()

    try:
        r = requests.get(
            f"https://data.sec.gov/submissions/CIK{cik}.json",
            headers={"User-Agent": _SEC_USER_AGENT},
            timeout=15,
        )
        if r.status_code != 200:
            return []
        data = r.json()
    except Exception as exc:
        logger.warning("SEC Form 144 fetch for %s failed: %s", ticker, exc)
        return []

    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accessions = recent.get("accessionNumber", [])
    dates = recent.get("filingDate", [])
    primary_docs = recent.get("primaryDocument", [])

    out: list[dict] = []
    for i, form in enumerate(forms):
        if form != "144":
            continue
        filed = dates[i] if i < len(dates) else ""
        if filed < cutoff:
            continue
        accession = accessions[i] if i < len(accessions) else ""
        primary = primary_docs[i] if i < len(primary_docs) else ""
        acc_no_dashes = accession.replace("-", "")
        url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_no_dashes}/{primary}" if primary else None
        out.append({"filed_date": filed, "url": url})
        if len(out) >= limit:
            break
    return out


def format_form144_for_prompt(filings: list[dict]) -> str:
    if not filings:
        return ""
    lines = [f"FORM 144 (proposed insider sales, ~90 days, leading indicator over Form 4):"]
    for f in filings:
        lines.append(f"  {f['filed_date']}  url={f.get('url')}")
    return "\n".join(lines)


# ─── 3e. SEC EDGAR — 10-Q section diff (new risk-factor language) ────────


_10Q_SECTION_REGEX = {
    "risk_factors": re.compile(
        r"item\s*1a[\s\.\)]+risk\s+factors(.*?)(?:item\s*[2-6][\s\.\)]|signatures)",
        re.IGNORECASE | re.DOTALL,
    ),
    "mda": re.compile(
        r"item\s*2[\s\.\)]+\s*management.{0,80}discussion(.*?)(?:item\s*3[\s\.\)]|item\s*4[\s\.\)])",
        re.IGNORECASE | re.DOTALL,
    ),
}


def _sec_recent_filings_of_type(
    cik: str, form_type: str, limit: int = 5
) -> list[tuple[str, str, str]]:
    """Returns list of (accession, primary_doc_url, filed_date) for latest N filings of the given form."""
    try:
        r = requests.get(
            f"https://data.sec.gov/submissions/CIK{cik}.json",
            headers={"User-Agent": _SEC_USER_AGENT},
            timeout=15,
        )
        if r.status_code != 200:
            return []
        data = r.json()
    except Exception:
        return []

    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accessions = recent.get("accessionNumber", [])
    primary_docs = recent.get("primaryDocument", [])
    dates = recent.get("filingDate", [])

    out: list[tuple[str, str, str]] = []
    for i, form in enumerate(forms):
        if form != form_type:
            continue
        accession = accessions[i]
        primary = primary_docs[i]
        filed = dates[i]
        acc_no_dashes = accession.replace("-", "")
        url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_no_dashes}/{primary}"
        out.append((accession, url, filed))
        if len(out) >= limit:
            break
    return out


def _fetch_10q_sections(doc_url: str, max_section_chars: int = 8000) -> dict[str, str]:
    """Extract risk_factors and mda from a 10-Q HTML doc."""
    try:
        r = requests.get(
            doc_url,
            headers={"User-Agent": _SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"},
            timeout=30,
        )
        if r.status_code != 200:
            return {}
        text = _strip_html(r.text)
    except Exception:
        return {}

    sections: dict[str, str] = {}
    for key, pat in _10Q_SECTION_REGEX.items():
        m = pat.search(text)
        if m:
            snippet = m.group(1).strip()[:max_section_chars]
            if len(snippet) > 200:
                sections[key] = snippet
    return sections


def _paragraphize(text: str, min_chars: int = 80) -> list[str]:
    """Split prose into paragraph-like chunks for diffing."""
    if not text:
        return []
    # Heuristic: split on sentence terminators followed by capital-letter starts,
    # and on common section delimiters. Then filter short fragments.
    raw = re.split(r"(?<=[.!?])\s+(?=[A-Z(])", text)
    return [p.strip() for p in raw if p and len(p.strip()) >= min_chars]


def _shingle(p: str, n: int = 6) -> set[str]:
    """Word-level n-gram shingle for near-duplicate detection."""
    words = re.findall(r"[A-Za-z]+", p.lower())
    if len(words) < n:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i:i + n]) for i in range(len(words) - n + 1)}


def fetch_sec_10q_diff(
    ticker: str,
    max_new_chunks: int = 12,
    overlap_threshold: float = 0.30,
) -> Optional[dict]:
    """
    Compare the LATEST 10-Q's risk-factor + MD&A sections against the PRIOR
    10-Q and return paragraphs that are substantively NEW (low n-gram
    overlap with anything in the prior quarter).

    New risk-factor language is one of the strongest leading indicators
    of disclosure deterioration — companies don't usually ADD risk text
    unless something materially changed.

    Returns {
      _curr_filed_date, _prev_filed_date, _curr_url, _prev_url,
      new_risk_factors: [str], new_mda: [str]
    } or None on failure / no prior filing.
    """
    cached = _10Q_DIFF_CACHE.get(ticker.upper())
    if cached is not None:
        return cached

    cik = _sec_get_cik(ticker)
    if not cik:
        _10Q_DIFF_CACHE[ticker.upper()] = None
        return None
    time.sleep(0.15)

    filings = _sec_recent_filings_of_type(cik, "10-Q", limit=3)
    if len(filings) < 2:
        _10Q_DIFF_CACHE[ticker.upper()] = None
        return None

    curr = filings[0]
    prev = filings[1]
    time.sleep(0.15)
    curr_sec = _fetch_10q_sections(curr[1])
    time.sleep(0.15)
    prev_sec = _fetch_10q_sections(prev[1])

    if not curr_sec or not prev_sec:
        _10Q_DIFF_CACHE[ticker.upper()] = None
        return None

    result: dict = {
        "_curr_filed_date": curr[2],
        "_prev_filed_date": prev[2],
        "_curr_url": curr[1],
        "_prev_url": prev[1],
    }

    for sec_key, out_key in (("risk_factors", "new_risk_factors"), ("mda", "new_mda")):
        curr_text = curr_sec.get(sec_key, "")
        prev_text = prev_sec.get(sec_key, "")
        if not curr_text or not prev_text:
            result[out_key] = []
            continue

        prev_paras = _paragraphize(prev_text)
        prev_shingles: set[str] = set()
        for p in prev_paras:
            prev_shingles |= _shingle(p)

        new_chunks: list[str] = []
        for p in _paragraphize(curr_text):
            p_sh = _shingle(p)
            if not p_sh:
                continue
            overlap = len(p_sh & prev_shingles) / max(len(p_sh), 1)
            if overlap < overlap_threshold:
                new_chunks.append(p[:400])  # cap each chunk
            if len(new_chunks) >= max_new_chunks:
                break
        result[out_key] = new_chunks

    _10Q_DIFF_CACHE[ticker.upper()] = result
    return result


_10Q_DIFF_CACHE: dict[str, Optional[dict]] = {}


def format_10q_diff_for_prompt(diff: dict) -> str:
    if not diff:
        return ""
    lines = [
        f"10-Q DIFF — new language vs prior quarter:",
        f"  current 10-Q  filed {diff.get('_curr_filed_date','?')}",
        f"  prior 10-Q    filed {diff.get('_prev_filed_date','?')}",
    ]
    nrf = diff.get("new_risk_factors") or []
    if nrf:
        lines.append(f"  NEW RISK FACTORS ({len(nrf)} paragraphs):")
        for p in nrf[:8]:
            lines.append(f"    • {p}")
    else:
        lines.append(f"  NEW RISK FACTORS: (none detected)")
    nmda = diff.get("new_mda") or []
    if nmda:
        lines.append(f"  NEW MD&A LANGUAGE ({len(nmda)} paragraphs):")
        for p in nmda[:6]:
            lines.append(f"    • {p}")
    return "\n".join(lines)


# ─── 6. FMP price-target consensus (dispersion for A3) ────────────────────


def fetch_price_target_summary(ticker: str) -> Optional[dict]:
    """
    /stable/price-target-summary returns rolling-window averages:
      lastMonth / lastQuarter / lastYear / allTime  (count + avg)
    plus a 'publishers' JSON-encoded list.

    Key A3 signal: when lastMonthAvgPriceTarget >> allTimeAvgPriceTarget
    (e.g. +50%), sell-side is CHASING the stock — a textbook complacency
    signature. Complements the consensus (high/low/median) endpoint.
    """
    data = _fmp_get(
        f"{_STABLE}/price-target-summary",
        {"symbol": ticker},
        api_key=None, uncap=True,
    )
    if not isinstance(data, list) or not data:
        return None
    r = data[0]

    last_month_avg = _safe_float(r.get("lastMonthAvgPriceTarget"))
    last_quarter_avg = _safe_float(r.get("lastQuarterAvgPriceTarget"))
    last_year_avg = _safe_float(r.get("lastYearAvgPriceTarget"))
    all_time_avg = _safe_float(r.get("allTimeAvgPriceTarget"))

    # Chasing ratios: how much higher is the recent window vs all-time?
    # > 1.20 = chasing; > 1.50 = aggressive chasing.
    def _ratio(num, den):
        if num is None or not den or den <= 0:
            return None
        return num / den

    publishers_raw = r.get("publishers") or "[]"
    try:
        import json as _json
        publishers = _json.loads(publishers_raw) if isinstance(publishers_raw, str) else publishers_raw
    except Exception:
        publishers = []

    return {
        "symbol": ticker,
        "last_month_count": int(r.get("lastMonthCount") or 0),
        "last_month_avg": last_month_avg,
        "last_quarter_count": int(r.get("lastQuarterCount") or 0),
        "last_quarter_avg": last_quarter_avg,
        "last_year_count": int(r.get("lastYearCount") or 0),
        "last_year_avg": last_year_avg,
        "all_time_count": int(r.get("allTimeCount") or 0),
        "all_time_avg": all_time_avg,
        "chase_ratio_month_vs_alltime": _ratio(last_month_avg, all_time_avg),
        "chase_ratio_quarter_vs_year": _ratio(last_quarter_avg, last_year_avg),
        "publishers": publishers,
    }


def fetch_price_target_consensus(ticker: str) -> Optional[dict]:
    """
    /stable/price-target-consensus returns targetHigh / targetLow /
    targetConsensus / targetMedian — lets us compute coefficient-of-
    variation as a proxy for sell-side uniformity.
    Lower CV = tighter consensus = more crowded long view = A3 flag.
    """
    data = _fmp_get(
        f"{_STABLE}/price-target-consensus",
        {"symbol": ticker},
        api_key=None, uncap=True,
    )
    if not isinstance(data, list) or not data:
        return None
    r = data[0]
    high = _safe_float(r.get("targetHigh"))
    low = _safe_float(r.get("targetLow"))
    avg = _safe_float(r.get("targetConsensus") or r.get("targetAverage"))
    median = _safe_float(r.get("targetMedian"))
    if not avg or avg <= 0:
        return None
    cv = None
    if high is not None and low is not None and avg > 0:
        # Rough proxy: (high - low) / (2 × avg) — half-range over mean.
        cv = (high - low) / (2 * avg)
    return {
        "target_high": high, "target_low": low, "target_avg": avg, "target_median": median,
        "cv_estimate": cv,
    }


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


def compute_quarterly_trends(ticker: str, n_quarters: int = 8) -> dict:
    """
    Quarter-by-quarter trajectory for the signals that matter to B3 (pricing
    power erosion) and B1 (concentration). Catches "consistent deterioration"
    that a single-period snapshot would miss.

    Returns dict with:
      quarters: list of {date, revenue, gross_margin_pct, dso_days,
                         deferred_rev_to_revenue, cfo_to_ni_ratio}
      trends: {gross_margin_delta_4q, dso_delta_4q, deferred_rev_delta_4q,
               revenue_qoq_4q_pct} — change from 4 quarters ago to latest
    """
    income = _fmp_get(
        f"{_STABLE}/income-statement",
        {"symbol": ticker, "period": "quarter", "limit": n_quarters},
        api_key=None, uncap=True,
    ) or []
    balance = _fmp_get(
        f"{_STABLE}/balance-sheet-statement",
        {"symbol": ticker, "period": "quarter", "limit": n_quarters},
        api_key=None, uncap=True,
    ) or []
    cashflow = _fmp_get(
        f"{_STABLE}/cash-flow-statement",
        {"symbol": ticker, "period": "quarter", "limit": n_quarters},
        api_key=None, uncap=True,
    ) or []

    if not income:
        return {"quarters": [], "trends": {}}

    income.sort(key=lambda r: r.get("date", ""))
    balance.sort(key=lambda r: r.get("date", ""))
    cashflow.sort(key=lambda r: r.get("date", ""))

    # Index balance + cf by date for O(1) join
    bal_by_date = {b.get("date"): b for b in balance if b.get("date")}
    cf_by_date = {c.get("date"): c for c in cashflow if c.get("date")}

    quarters: list[dict] = []
    for inc in income:
        d = inc.get("date")
        if not d:
            continue
        bal = bal_by_date.get(d, {})
        cf = cf_by_date.get(d, {})

        revenue = _safe_float(inc.get("revenue"))
        gp = _safe_float(inc.get("grossProfit"))
        gm = (gp / revenue) if (gp is not None and revenue and revenue > 0) else None

        recv = _safe_float(bal.get("accountsReceivables") or bal.get("netReceivables"))
        # Quarterly DSO uses 90 days (not 365)
        dso = (recv / revenue * 90) if (recv and revenue and revenue > 0) else None

        deferred = _safe_float(bal.get("deferredRevenue") or bal.get("deferredRevenueNonCurrent"))
        dr_to_rev = (deferred / revenue) if (deferred is not None and revenue and revenue > 0) else None

        cfo = _safe_float(cf.get("operatingCashFlow") or cf.get("netCashProvidedByOperatingActivities"))
        ni = _safe_float(inc.get("netIncome"))
        cash_conv = (cfo / ni) if (cfo is not None and ni and ni != 0) else None

        quarters.append({
            "date": d,
            "revenue": revenue,
            "gross_margin_pct": gm,
            "dso_days": dso,
            "deferred_rev_to_revenue": dr_to_rev,
            "cfo_to_ni_ratio": cash_conv,
        })

    trends: dict = {
        "gross_margin_delta_4q": None,
        "dso_delta_4q": None,
        "deferred_rev_delta_4q": None,
        "revenue_qoq_4q_pct": None,
    }
    if len(quarters) >= 5:
        latest = quarters[-1]
        prior4 = quarters[-5]
        def _delta(k):
            a, b = latest.get(k), prior4.get(k)
            return (a - b) if (a is not None and b is not None) else None
        trends["gross_margin_delta_4q"] = _delta("gross_margin_pct")
        trends["dso_delta_4q"] = _delta("dso_days")
        trends["deferred_rev_delta_4q"] = _delta("deferred_rev_to_revenue")
        a_rev, b_rev = latest.get("revenue"), prior4.get("revenue")
        if a_rev and b_rev and b_rev > 0:
            trends["revenue_qoq_4q_pct"] = (a_rev / b_rev) - 1

    return {"quarters": quarters, "trends": trends}


def format_quarterly_trends_for_prompt(qt: dict) -> str:
    """Compact quarterly-trend rendering for the LLM prompt (B3 pricing-power)."""
    quarters = qt.get("quarters") or []
    trends = qt.get("trends") or {}
    if not quarters:
        return ""
    lines = [f"QUARTERLY TRENDS (last {len(quarters)} quarters):"]
    lines.append(f"  {'date':<11s} {'rev($M)':>10s} {'GM%':>7s} {'DSO':>6s} {'DefRv/R':>8s} {'CFO/NI':>7s}")
    for q in quarters:
        rev_m = (q['revenue'] / 1e6) if q['revenue'] else None
        def f(v, fmt):
            return fmt.format(v) if v is not None else "n/a"
        lines.append(
            f"  {q['date']:<11s} {f(rev_m,'{:>10,.0f}')} "
            f"{f(q['gross_margin_pct'],'{:>6.1%}')} "
            f"{f(q['dso_days'],'{:>5.0f}d')} "
            f"{f(q['deferred_rev_to_revenue'],'{:>7.1%}')} "
            f"{f(q['cfo_to_ni_ratio'],'{:>6.2f}x')}"
        )
    lines.append("")
    lines.append("  4-QUARTER DELTAS (latest minus 4Q ago):")
    def _fd(v, fmt):
        return fmt.format(v) if v is not None else "n/a"
    lines.append(f"    Gross margin Δ 4Q          : {_fd(trends.get('gross_margin_delta_4q'), '{:+.1%}')}")
    lines.append(f"    DSO Δ 4Q                   : {_fd(trends.get('dso_delta_4q'), '{:+.0f} days')}")
    lines.append(f"    Deferred-rev/Revenue Δ 4Q  : {_fd(trends.get('deferred_rev_delta_4q'), '{:+.1%}')}")
    lines.append(f"    Revenue growth 4Q YoY      : {_fd(trends.get('revenue_qoq_4q_pct'), '{:+.1%}')}")
    return "\n".join(lines)


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
