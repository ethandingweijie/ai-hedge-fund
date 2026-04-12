"""
src/tools/hkex_api.py
─────────────────────
Resolve HKEX annual-report filing metadata for HK-listed stocks.

How it works
────────────
HKEXnews (www1.hkexnews.hk) exposes two lightweight public endpoints:

1. JSONP autocomplete (prefix.do)
   Resolves a 5-digit stock code → internal stockId + company name.
   URL: https://www1.hkexnews.hk/search/prefix.do
   Params: lang=EN, type=A (active) / I (inactive), name=<code>,
           market=SEHK, callback=cb

2. Filing search (titleSearchServlet.do)
   Returns actual annual-report filing metadata including the direct
   PDF/document URL (FILE_LINK).
   URL: https://www1.hkexnews.hk/search/titleSearchServlet.do
   Key params:
     market=SEHK, stockId=<from prefix.do>,
     searchType=1  (filings from 2006 onwards)
     t1code=40000  (Financial Statements/ESG Information)
     t2code=40100  (Annual Report / 年報)
     t2Gcode=-1    (no sub-group filter)
     category=0    (all security types)
     documentType=-2  (use tier codes, not doc type)
     sortByOptions=DateTime, sortDir=desc
     lang=EN, rowRange=10
   Result: JSON with "result" array; each item has FILE_LINK = PDF URL.

Returns a filing-ref dict keyed by exchange="HKEX" compatible with
state["data"]["edgar_filing_refs"].

Compatible schema:
  exchange          "HKEX"
  stock_code        5-digit zero-padded HKEX code, e.g. "06862"
  company_name      company display name (from JSONP)
  filing_type       "Annual Report" (年報)
  filing_date       date string "YYYY-MM-DD" (from filing metadata)
  period_of_report  fiscal year-end date (estimated from filing date)
  fiscal_year       str, e.g. "2024"
  filing_url        direct PDF/document URL from FILE_LINK
  viewer_url        HKEXnews search-results page for this stock
  is_foreign        True
  cik               None
  accession_number  None
  is_stub           False if PDF found; True if only viewer URL available
  is_ipo_prospectus False
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta

from src.tools.hk.ticker import to_akshare_code

_log = logging.getLogger(__name__)

_BASE_URL      = "https://www1.hkexnews.hk"
_SEARCH_URL    = f"{_BASE_URL}/search/titlesearch.xhtml"
_PREFIX_URL    = f"{_BASE_URL}/search/prefix.do"
_SERVLET_URL   = f"{_BASE_URL}/search/titleSearchServlet.do"

DOC_ANNUAL  = "40"    # 年報 — Annual Report (legacy param; we use t2code instead)
DOC_INTERIM = "1301"  # 中期報告 — Interim Report

# Tier category codes (from /ncms/script/eds/tierone_e.json + tiertwo_e.json)
_T1_FINANCIAL   = "40000"   # Financial Statements/ESG Information
_T2_ANNUAL      = "40100"   # Annual Report / 年報
_T2_GROUP_ALL   = "-1"      # No sub-group filter

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-US,en;q=0.5",
    "X-Requested-With": "XMLHttpRequest",
    "Referer":         _SEARCH_URL,
}

# JSONP wrapper: callback({...});
_JSONP_RE = re.compile(r"[a-zA-Z_$][a-zA-Z0-9_$]*\s*\(\s*(\{.*\})\s*\)\s*;?\s*$", re.DOTALL)


def _resolve_stock(stock_code: str) -> dict | None:
    """
    Resolve a 5-digit HKEX stock code to its internal stockId and name.

    Uses the JSONP prefix.do endpoint.
    Returns {"stockId": int, "code": str, "name": str} or None on failure.
    """
    try:
        import requests as _req
    except ImportError:
        return None

    for stock_type in ("A", "I"):   # A = active, I = inactive / delisted
        try:
            resp = _req.get(
                _PREFIX_URL,
                params={
                    "lang":     "EN",
                    "type":     stock_type,
                    "name":     stock_code,
                    "market":   "SEHK",
                    "callback": "cb",
                },
                headers={**_HEADERS, "Accept": "*/*"},
                timeout=10,
            )
            resp.raise_for_status()
            text = resp.text.strip()

            m = _JSONP_RE.match(text)
            if not m:
                continue

            data = json.loads(m.group(1))
            for item in data.get("stockInfo", []):
                if str(item.get("code", "")).zfill(5) == stock_code:
                    return item   # {"stockId": ..., "code": ..., "name": ...}
        except Exception as exc:
            _log.debug("prefix.do lookup failed for %s (type=%s): %s", stock_code, stock_type, exc)

    return None


def _search_filings(
    stock_id: int,
    stock_code: str,
    years_back: int = 4,
) -> list[dict]:
    """
    Query titleSearchServlet.do for Annual Report filings.

    Parameters
    ----------
    stock_id   : internal HKEX stockId (from prefix.do)
    stock_code : 5-digit HKEX code (for logging)
    years_back : search window — from (today - years_back years) to today

    Returns
    -------
    List of raw result dicts from the "result" JSON array.
    Each dict contains: DATE_TIME, STOCK_CODE, STOCK_NAME, SHORT_TEXT,
    TITLE, FILE_LINK, FILE_INFO, FILE_TYPE, DOD_WEB_PATH, etc.
    Empty list on failure or no results.
    """
    try:
        import requests as _req
    except ImportError:
        return []

    today     = datetime.today()
    from_date = (today - timedelta(days=years_back * 366)).strftime("%Y%m%d")
    to_date   = today.strftime("%Y%m%d")

    params = {
        "sortDir":       "0",           # "0" = descending (JS convention, not "desc")
        "sortByOptions": "DateTime",
        "category":      "0",           # all security types
        "market":        "SEHK",
        "stockId":       str(stock_id),
        "documentType":  "-2",          # use tier codes, not legacy doc type
        "fromDate":      from_date,
        "toDate":        to_date,
        "title":         "",
        "searchType":    "1",           # 1 = filings from 2006 onwards
        "t1code":        _T1_FINANCIAL,
        "t2Gcode":       _T2_GROUP_ALL,
        "t2code":        _T2_ANNUAL,
        "rowRange":      "10",
        "lang":          "E",           # "E" not "EN" — server normalises to this
    }

    try:
        # Initialise a session — the servlet checks for a valid JSESSIONID cookie
        sess = _req.Session()
        sess.headers.update(_HEADERS)
        try:
            sess.get(_SEARCH_URL + "?lang=EN", timeout=8)
        except Exception:
            pass  # session init failure is non-fatal; proceed anyway

        resp = sess.get(
            _SERVLET_URL,
            params=params,
            timeout=15,
        )
        resp.raise_for_status()

        data = resp.json()
        record_cnt  = int(data.get("recordCnt", 0))
        result_raw  = data.get("result", "[]")
        # HKEXnews returns "result" as a JSON-encoded string (e.g. '[{...}]'), not a native array
        if isinstance(result_raw, str):
            results = json.loads(result_raw) if result_raw not in (None, "null", "[]", "") else []
        else:
            results = result_raw or []

        _log.info(
            "HKEXnews titleSearchServlet: stockId=%s code=%s → recordCnt=%d",
            stock_id, stock_code, record_cnt,
        )

        return results

    except Exception as exc:
        _log.warning("titleSearchServlet.do failed for stockId=%s: %s", stock_id, exc)
        return []


def _parse_filing_date(date_time_str: str) -> str | None:
    """
    Parse HKEXnews DATE_TIME field to "YYYY-MM-DD".

    Observed formats:
      "24/04/2025 16:46"        — DD/MM/YYYY HH:MM  (primary format from API)
      "20240328180000"           — YYYYMMDDHHMMSS
      "2024/03/28 18:00:00"     — YYYY/MM/DD HH:MM:SS

    Returns None on parse failure.
    """
    if not date_time_str:
        return None
    s = str(date_time_str).strip()
    # "DD/MM/YYYY HH:MM" — primary format returned by titleSearchServlet.do
    m = re.match(r"^(\d{2})/(\d{2})/(\d{4})", s)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    # "YYYY/MM/DD" or "YYYY-MM-DD"
    m2 = re.match(r"^(\d{4})[/\-](\d{2})[/\-](\d{2})", s)
    if m2:
        return f"{m2.group(1)}-{m2.group(2)}-{m2.group(3)}"
    # "YYYYMMDD..." — 8+ leading digits
    if len(s) >= 8 and s[:8].isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return None


def _build_filing_url(file_link: str) -> str | None:
    """
    Expand a relative FILE_LINK to an absolute URL.

    HKEXnews FILE_LINK values are either already absolute or root-relative:
      "/listedco/listconews/SEHK/2024/.../...pdf"
    """
    if not file_link:
        return None
    if file_link.startswith("http"):
        return file_link
    return _BASE_URL + file_link


def get_hkex_filing_refs(
    ticker: str,
    doc_type: str = DOC_ANNUAL,
    max_years_back: int = 4,
) -> dict:
    """
    Return HKEX annual-report filing metadata for a given HK ticker.

    Resolution steps:
    1. JSONP prefix.do → company name + stockId
    2. titleSearchServlet.do → most recent Annual Report filing
       (t1code=40000 / t2code=40100), extracts FILE_LINK (PDF URL)
    3. Construct HKEXnews viewer URL for attribution

    Parameters
    ----------
    ticker        : HK ticker in any valid format ("06862", "6862", "06862.HK")
    doc_type      : legacy param, unused (tier codes used instead)
    max_years_back: search window in years (default 4)

    Returns
    -------
    dict compatible with edgar_filing_refs schema (exchange="HKEX"),
    or {} if the stock code cannot be resolved.
    """
    stock_code = to_akshare_code(ticker)  # always 5-digit, e.g. "06862"

    # ── 1. Resolve company name + stockId via JSONP ───────────────────────────
    stock_info = _resolve_stock(stock_code)

    if stock_info is None:
        _log.warning(
            "HKEXnews: could not resolve stock code %s via prefix.do", stock_code
        )
        return {}

    company_name = stock_info.get("name") or f"SEHK:{stock_code}"
    stock_id     = stock_info.get("stockId")

    # ── 2. Search for Annual Report filings ───────────────────────────────────
    filing_url   = None
    filing_date  = None
    fiscal_year  = str(datetime.today().year - 1)  # default: prior year

    if stock_id is not None:
        results = _search_filings(stock_id, stock_code, years_back=max_years_back)

        if results:
            # Take the most recent Annual Report (results are sorted desc by date)
            latest = results[0]

            raw_link    = latest.get("FILE_LINK", "")
            filing_url  = _build_filing_url(raw_link)

            date_str    = _parse_filing_date(latest.get("DATE_TIME", ""))
            filing_date = date_str

            # Fiscal year: extract from title "Annual Report YYYY" if present;
            # otherwise infer as (filing year - 1) e.g. filed Apr 2025 → FY 2024
            title_str = latest.get("TITLE", "")
            fy_from_title = re.search(r"\b(20\d{2})\b", title_str)
            if fy_from_title:
                fiscal_year = fy_from_title.group(1)
            elif date_str:
                filing_year = int(date_str[:4])
                fiscal_year = str(filing_year - 1)

            _log.info(
                "HKEXnews: found Annual Report for %s — %s filed %s → %s",
                stock_code, company_name, filing_date, filing_url,
            )
        else:
            _log.info(
                "HKEXnews: no Annual Report found for %s (stockId=%s) "
                "— viewer URL only",
                stock_code, stock_id,
            )
    else:
        _log.warning("HKEXnews: stockId missing for %s", stock_code)

    # ── 3. Construct HKEXnews viewer URL (for attribution / fallback) ─────────
    viewer_url = (
        f"{_SEARCH_URL}?lang=EN&market=SEHK&searchType=1"
        f"&t1code={_T1_FINANCIAL}&t2code={_T2_ANNUAL}&stockCode={stock_code}"
    )

    _log.info(
        "HKEXnews: resolved %s → %s (stockId=%s), filing_url=%s",
        stock_code, company_name, stock_id, filing_url,
    )

    return {
        "exchange":          "HKEX",
        "stock_code":        stock_code,
        "company_name":      company_name,
        "filing_type":       "Annual Report",
        "filing_date":       filing_date,
        "period_of_report":  f"{fiscal_year}-12-31",
        "fiscal_year":       fiscal_year,
        # filing_url = direct PDF URL from HKEXnews (None if not found)
        # viewer_url = HKEXnews annual report search page (always set)
        "filing_url":        filing_url,
        "viewer_url":        viewer_url,
        "is_foreign":        True,
        "cik":               None,
        "accession_number":  None,
        "is_stub":           filing_url is None,
        "is_ipo_prospectus": False,
    }
