"""Primary filings for Tokyo listings -- the Japan counterpart of the EDGAR / HKEXnews resolver.

Three sources, in the order the resolver tries them:

1. SEC EDGAR Form 20-F, for the Tokyo names with a US ADR that files one (japan.ADR_20F) -- but ONLY when the 20-F's
   accounting basis equals the Japanese filing's. MUFG files a US-GAAP 20-F while its Tokyo statements (and FMP's)
   are J-GAAP; citing the 20-F for those figures would label J-GAAP numbers with a US-GAAP document.
2. TDnet (適時開示情報閲覧サービス), the exchange's timely-disclosure feed: the latest 決算短信 (kessan tanshin) PDF.
   No API and no key -- the daily list pages are public HTML (https://www.release.tdnet.info/inbs/I_list_NNN_YYYYMMDD
   .html, newest first, ~100 rows a page) and TDnet keeps about one month. Scanning a whole month is ~150-300 page
   loads, so the scan is aimed: only the days FMP's earnings calendar names (plus the next day, for releases after
   the UTC date turns). One day is ~5-25 pages, fetched in parallel, cached per day for the process.
3. EDINET (金融庁 electronic disclosure), the statutory 有価証券報告書 (annual securities report). EDINET API v2 needs a
   free subscription key (EDINET_API_KEY); without one this source is skipped. With one, the filing window after the
   fiscal year end (75-100 days: late June for a March close) is scanned for docTypeCode 120.

Everything here is best effort: any failure returns None and the run proceeds on FMP attribution, as SG does.
JP_TDNET=off disables the TDnet scan (golden replays and tests run without network).
"""
from __future__ import annotations

import html
import logging
import os
import re
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from typing import Callable, Optional

from src.markets import japan

logger = logging.getLogger(__name__)

TDNET_BASE = "https://www.release.tdnet.info/inbs/"
TDNET_HOME = TDNET_BASE + "I_main_00.html"
EDINET_API = "https://api.edinet-fsa.go.jp/api/v2/documents.json"
EDINET_VIEWER = "https://disclosure2.edinet-fsa.go.jp/WZEK0040.aspx?{doc_id}"
_UA = "Mozilla/5.0 (research client; filings resolver)"
_MAX_PAGES = 40
_DAY_CACHE: dict[str, tuple[float, list[dict]]] = {}
_CACHE_LOCK = threading.Lock()
_TODAY_TTL_S = 1800.0

_ROW_RE = re.compile(r"<tr>(.*?)</tr>", re.S)
_CELL_RE = re.compile(r'class="[^"]*\bkj(Time|Code|Name|Title|Xbrl|Place)\b[^"]*"[^>]*>(.*?)</td>', re.S)
_HREF_RE = re.compile(r'href="([^"]+)"')
_TAG_RE = re.compile(r"<[^>]+>")
_PAGER_RE = re.compile(r"I_list_(\d{3})_\d{8}\.html")
#: "2027年3月期 第2四半期（中間期）決算短信〔IFRS〕（連結)" -> FY end 2027-03, quarter 2, basis IFRS
_PERIOD_RE = re.compile(r"(\d{4})年(\d{1,2})月期(?:\s*第(\d)四半期)?")
_BASIS = (("IFRS", "IFRS"), ("米国基準", "US GAAP"), ("日本基準", "J-GAAP"))


def tdnet_enabled() -> bool:
    return os.environ.get("JP_TDNET", "on").strip().lower() not in ("0", "off", "false", "no")


def _http_get(url: str, timeout: float = 15.0) -> Optional[str]:
    try:
        import requests
        r = requests.get(url, headers={"User-Agent": _UA}, timeout=timeout)
        if r.status_code != 200:
            return None
        r.encoding = r.encoding if r.encoding and r.encoding.lower() != "iso-8859-1" else "utf-8"
        return r.text
    except Exception:                                      # noqa: BLE001
        return None


def _clean(cell: str) -> str:
    return unicodedata.normalize("NFKC", html.unescape(_TAG_RE.sub("", cell))).strip()


def parse_tdnet_page(body: str, day: date) -> tuple[list[dict], int]:
    """(rows, page count) from one TDnet list page. Rows: {date, time, code5, ticker, name, title, pdf_url, xbrl_url,
    exchanges}. The page count is the highest pager link (1 when the day fits on one page)."""
    rows = []
    for tr in _ROW_RE.findall(body or ""):
        cells = {k: v for k, v in _CELL_RE.findall(tr)}
        if "Code" not in cells or "Title" not in cells:
            continue
        href = _HREF_RE.search(cells["Title"])
        xbrl = _HREF_RE.search(cells.get("Xbrl", ""))
        code5 = _clean(cells["Code"])
        rows.append({
            "date": day.isoformat(), "time": _clean(cells.get("Time", "")), "code5": code5,
            "ticker": japan.from_local_code(code5), "name": _clean(cells.get("Name", "")),
            "title": _clean(cells["Title"]),
            "pdf_url": (TDNET_BASE + href.group(1)) if href else None,
            "xbrl_url": (TDNET_BASE + xbrl.group(1)) if xbrl else None,
            "exchanges": _clean(cells.get("Place", "")),
        })
    pages = [int(p) for p in _PAGER_RE.findall(body or "")]
    return rows, max(pages) if pages else 1


def tdnet_day(day: date, fetch: Callable[[str], Optional[str]] = _http_get, workers: int = 8) -> list[dict]:
    """Every disclosure TDnet lists for `day` (cached; today's list for 30 minutes)."""
    key = day.isoformat()
    now = time.time()
    with _CACHE_LOCK:
        hit = _DAY_CACHE.get(key)
        if hit and (day < date.today() or now - hit[0] < _TODAY_TTL_S):
            return hit[1]
    stamp = day.strftime("%Y%m%d")
    first = fetch(f"{TDNET_BASE}I_list_001_{stamp}.html")
    if not first:
        return []
    rows, pages = parse_tdnet_page(first, day)
    pages = min(pages, _MAX_PAGES)
    if pages > 1:
        urls = [f"{TDNET_BASE}I_list_{p:03d}_{stamp}.html" for p in range(2, pages + 1)]
        with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            for body in ex.map(fetch, urls):
                if body:
                    rows.extend(parse_tdnet_page(body, day)[0])
    with _CACHE_LOCK:
        _DAY_CACHE[key] = (now, rows)
    return rows


def classify_title(title: str) -> dict:
    """{is_tanshin, is_correction, is_forecast_revision, fy_end, quarter, basis} from a TDnet title."""
    t = unicodedata.normalize("NFKC", title or "")
    m = _PERIOD_RE.search(t)
    fy_end = f"{int(m.group(1)):04d}-{int(m.group(2)):02d}" if m else None
    quarter = int(m.group(3)) if m and m.group(3) else (4 if m and "決算短信" in t else None)
    basis = next((b for k, b in _BASIS if k in t), None)
    return {"is_tanshin": "決算短信" in t, "is_correction": "訂正" in t,
            "is_forecast_revision": "業績予想" in t and "修正" in t,
            "fy_end": fy_end, "quarter": quarter, "basis": basis}


def tdnet_filings(ticker: str, days: list[date], fetch: Callable[[str], Optional[str]] = _http_get) -> list[dict]:
    """This company's TDnet rows on the given days, newest first, each with classify_title() merged in."""
    code5 = japan.local_code5(ticker)
    if not code5:
        return []
    out = []
    for d in sorted(set(days), reverse=True):
        for r in tdnet_day(d, fetch):
            if r.get("code5") == code5:
                out.append({**r, **classify_title(r.get("title", ""))})
    return out


def _candidate_days(earnings_dates: list[str], today: Optional[date] = None) -> list[date]:
    """FMP earnings dates inside TDnet's ~31-day window, each with the following day (a 15:00 JST release is
    06:00 UTC the same day, but FMP occasionally stamps the next one)."""
    today = today or date.today()
    out = []
    for s in earnings_dates or []:
        try:
            d = date.fromisoformat(str(s)[:10])
        except ValueError:
            continue
        for x in (d, d + timedelta(days=1)):
            if timedelta(0) <= today - x <= timedelta(days=31):
                out.append(x)
    return out


def _fmp_earnings_dates(ticker: str) -> list[str]:
    try:
        from src.tools.api import get_earnings_surprises
        rows = get_earnings_surprises(ticker, (date.today() + timedelta(days=1)).isoformat(), limit=3) or []
        return [r["date"] for r in rows if isinstance(r, dict) and r.get("date")]
    except Exception:                                      # noqa: BLE001
        return []


def latest_tanshin(ticker: str, earnings_dates: Optional[list[str]] = None,
                   fetch: Callable[[str], Optional[str]] = _http_get, today: Optional[date] = None) -> Optional[dict]:
    """The latest 決算短信 on TDnet (originals before corrections), or None."""
    if not japan.is_jp_ticker(ticker) or not tdnet_enabled():
        return None
    days = _candidate_days(earnings_dates if earnings_dates is not None else _fmp_earnings_dates(ticker), today)
    if not days:
        return None
    rows = [r for r in tdnet_filings(ticker, days, fetch) if r.get("is_tanshin")]
    rows.sort(key=lambda r: (r["date"], r.get("time") or "", not r["is_correction"]), reverse=True)
    originals = [r for r in rows if not r["is_correction"]]
    return (originals or rows or [None])[0]


# ── EDINET (needs EDINET_API_KEY) ──────────────────────────────────────────

def edinet_latest_annual(ticker: str, fy_end: Optional[str], api_key: Optional[str] = None,
                         fetch_json: Optional[Callable[[str, dict], Optional[dict]]] = None) -> Optional[dict]:
    """The 有価証券報告書 (docTypeCode 120) filed for the fiscal year ending `fy_end` (YYYY-MM-DD), or None.
    Scans the statutory filing window (75-100 days after the year end; the legal deadline is 3 months)."""
    key = api_key or os.environ.get("EDINET_API_KEY")
    code5 = japan.local_code5(ticker)
    if not key or not code5 or not fy_end:
        return None
    try:
        end = date.fromisoformat(str(fy_end)[:10])
    except ValueError:
        return None

    def _default(url: str, params: dict) -> Optional[dict]:
        try:
            import requests
            r = requests.get(url, params=params, headers={"User-Agent": _UA}, timeout=20)
            return r.json() if r.status_code == 200 else None
        except Exception:                                  # noqa: BLE001
            return None

    getter = fetch_json or _default
    for off in range(75, 101):
        d = end + timedelta(days=off)
        if d > date.today():
            break
        if d.weekday() >= 5:
            continue
        doc = getter(EDINET_API, {"date": d.isoformat(), "type": 2, "Subscription-Key": key}) or {}
        for r in doc.get("results") or []:
            if str(r.get("secCode") or "") == code5 and str(r.get("docTypeCode") or "") == "120":
                return {"doc_id": r.get("docID"), "filing_date": d.isoformat(),
                        "period_end": r.get("periodEnd"), "description": r.get("docDescription"),
                        "filer": r.get("filerName"), "viewer_url": EDINET_VIEWER.format(doc_id=r.get("docID"))}
    return None


# ── The resolver entry point ───────────────────────────────────────────────

def resolve_filing_ref(ticker: str, edgar_lookup: Optional[Callable[[str], dict]] = None,
                       tanshin_lookup: Optional[Callable[[str], Optional[dict]]] = None) -> dict:
    """A filing reference in the shape the research phase reads (edgar_filing_refs[ticker]).

    - an ADR 20-F on the same basis as the Tokyo filing -> the EDGAR ref, plus `exchange`="TSE", `adr`;
    - else the latest TDnet 決算短信 -> {exchange: "TSE", source: "TDnet", filing_type: "決算短信 (kessan tanshin)",
      filing_url (PDF), viewer_url (TDnet), filing_date, fiscal_year, period_of_report, basis, quarter};
    - else {} (FMP attribution, as for SG)."""
    t = japan.to_canonical(ticker)
    adr = japan.adr_filer(t)
    if adr and adr.get("basis_20f") == adr.get("basis_jp"):
        try:
            if edgar_lookup is None:
                from src.tools.api import get_edgar_filing_refs as edgar_lookup
            ref = dict(edgar_lookup(adr["adr"]) or {})
        except Exception:                                  # noqa: BLE001
            ref = {}
        if ref.get("accession_number") and not ref.get("is_stub"):
            ref.update({"exchange_listing": "TSE", "adr": adr["adr"], "basis": adr["basis_20f"],
                        "source": f"SEC EDGAR Form {ref.get('filing_type') or '20-F'} (ADR {adr['adr']})"})
            return ref
    try:
        row = (tanshin_lookup or latest_tanshin)(t)
    except Exception:                                      # noqa: BLE001
        row = None
    if row:
        fy = (row.get("fy_end") or "")[:4]
        return {"exchange": "TSE", "source": "TDnet", "filing_type": "決算短信 (kessan tanshin)",
                "company_name": row.get("name"), "filing_url": row.get("pdf_url"), "viewer_url": TDNET_HOME,
                "xbrl_url": row.get("xbrl_url"), "filing_date": row.get("date"), "fiscal_year": fy,
                "period_of_report": row.get("fy_end"), "quarter": row.get("quarter"), "basis": row.get("basis"),
                "title": row.get("title")}
    return {}


def filing_context(ref: Optional[dict]) -> str:
    """Prompt block naming the TDnet primary source and how to cite it ('' unless `ref` is a TDnet ref)."""
    r = ref or {}
    if r.get("source") != "TDnet" or not r.get("filing_url"):
        return ""
    q = r.get("quarter")
    period = (f"Q{q} of the fiscal year ending {r.get('period_of_report')}" if q and q != 4
              else f"fiscal year ending {r.get('period_of_report')}")
    return (
        f"\nTSE PRIMARY SOURCE (TDnet) -- {r.get('title')}\n"
        f"  Released {r.get('filing_date')} | {period} | accounting basis {r.get('basis') or 'see title'}\n"
        f"  PDF: {r.get('filing_url')}\n"
        f"  -> Read it for the results and the company's full-year forecast (通期業績予想).\n"
        f"  -> Cite statement figures as (決算短信 {r.get('filing_date')}, TDnet) with url='{r.get('filing_url')}'.\n\n"
    )
