"""cninfo (巨潮资讯网) -- the CSRC-designated disclosure site for every SSE and SZSE company.

The China equivalent of the EDGAR / HKEXnews resolvers: the latest annual report (年度报告) as a direct PDF, and the
dates of the periodic reports (annual / half-year / Q1 / Q3) for the earnings-freshness trigger.

Why a dedicated client and not FMP: FMP's A-share statements stamp `filingDate` and `acceptedDate` with the PERIOD
END (600519.SS FY2025: filingDate 2025-12-31; the report was actually filed 2026-04-17), so FMP cannot say when
results became public. cninfo can.

Endpoints (public, no key; verified 2026-11-28):
  POST /new/information/topSearch/query  keyWord=<code>        -> [{code, orgId, zwjc (Chinese short name), ...}]
  POST /new/hisAnnouncement/query        stock=<code>,<orgId>  -> {announcements: [{announcementTitle,
       announcementTime (ms, Beijing midnight), adjunctUrl ("finalpage/2026-04-17/1225114741.PDF")}]}
PDFs are served from https://static.cninfo.com.cn/<adjunctUrl>.

Never raises: every failure returns {} / None / [] and the caller falls back to FMP attribution, as it does for SG.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional

_log = logging.getLogger(__name__)

_BASE = "http://www.cninfo.com.cn"
_STATIC = "https://static.cninfo.com.cn/"
_TIMEOUT = 12
_BEIJING = timezone(timedelta(hours=8))
_HEADERS = {"User-Agent": "Mozilla/5.0 (research client)",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"}

CATEGORIES = {
    "annual": "category_ndbg_szsh",     # 年度报告
    "interim": "category_bndbg_szsh",   # 半年度报告
    "q1": "category_yjdbg_szsh",        # 一季度报告
    "q3": "category_sjdbg_szsh",        # 三季度报告
}

_LOCK = threading.Lock()
_ORG_CACHE: dict[str, Optional[dict]] = {}
_ANN_CACHE: dict[tuple, tuple[float, list]] = {}
_ANN_TTL_S = 6 * 3600


def _post(path: str, data: dict) -> Optional[object]:
    try:
        import requests
        r = requests.post(_BASE + path, data=data, headers=_HEADERS, timeout=_TIMEOUT)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception as exc:                               # noqa: BLE001
        _log.info("cninfo %s failed: %s", path, exc)
        return None


def _code(ticker: str) -> Optional[str]:
    from src.markets.china import normalize
    t = normalize(ticker)
    return t[:6] if t else None


def resolve_org(ticker: str) -> Optional[dict]:
    """{code, org_id, name_zh} for an A-share, or None."""
    code = _code(ticker)
    if not code:
        return None
    with _LOCK:
        if code in _ORG_CACHE:
            return _ORG_CACHE[code]
    rows = _post("/new/information/topSearch/query", {"keyWord": code, "maxNum": 10})
    hit = None
    for r in rows if isinstance(rows, list) else []:
        if isinstance(r, dict) and r.get("code") == code and r.get("category") == "A股" and r.get("orgId"):
            hit = {"code": code, "org_id": r["orgId"], "name_zh": r.get("zwjc")}
            break
    if rows is not None:                                   # do not cache a transport failure
        with _LOCK:
            _ORG_CACHE[code] = hit
    return hit


def _announced(ms) -> Optional[str]:
    try:
        return datetime.fromtimestamp(float(ms) / 1000.0, tz=_BEIJING).date().isoformat()
    except (TypeError, ValueError, OSError):
        return None


def periodic_reports(ticker: str, kinds: tuple = ("annual", "interim", "q1", "q3"), years_back: int = 3,
                     today: Optional[date] = None) -> list[dict]:
    """Full-text periodic reports, newest first: [{kind, title, date, url}]. Summaries (摘要), English versions
    (英文版) and corrections are excluded -- the Chinese full text is the primary document."""
    from src.markets.china import normalize
    t = normalize(ticker)
    org = resolve_org(ticker) if t else None
    if not org:
        return []
    d = today or date.today()
    key = (t, tuple(kinds), years_back, d.isoformat())
    with _LOCK:
        hit = _ANN_CACHE.get(key)
        if hit and time.time() - hit[0] < _ANN_TTL_S:
            return list(hit[1])
    sh = t.endswith(".SS")
    data = {"stock": f"{org['code']},{org['org_id']}", "tabName": "fulltext", "pageSize": 30, "pageNum": 1,
            "column": "sse" if sh else "szse", "plate": "sh" if sh else "sz",
            "category": ";".join(CATEGORIES[k] for k in kinds),
            "seDate": f"{(d - timedelta(days=366 * years_back)).isoformat()}~{d.isoformat()}", "isHLtitle": "true"}
    res = _post("/new/hisAnnouncement/query", data)
    if not isinstance(res, dict):
        return []
    out = []
    for a in res.get("announcements") or []:
        title = str(a.get("announcementTitle") or "")
        # "中国平安：平安银行股份有限公司2026年第三季度报告" is a listed subsidiary's report filed under the parent.
        if any(x in title for x in ("摘要", "英文", "更正", "取消", "补充", "：", ":")):
            continue
        kind = None
        if "半年度报告" in title or "中期报告" in title:      # insurers and some banks title it 中期报告
            kind = "interim"
        elif "年度报告" in title:
            kind = "annual"
        elif "一季度" in title or "第一季度" in title:
            kind = "q1"
        elif "三季度" in title or "第三季度" in title:
            kind = "q3"
        if kind is None or kind not in kinds:
            continue
        when = _announced(a.get("announcementTime"))
        url = a.get("adjunctUrl")
        if not when or not url:
            continue
        out.append({"kind": kind, "title": title, "date": when, "url": _STATIC + str(url).lstrip("/")})
    out.sort(key=lambda r: r["date"], reverse=True)
    with _LOCK:
        _ANN_CACHE[key] = (time.time(), out)
    return list(out)


def get_cninfo_filing_refs(ticker: str) -> dict:
    """The latest annual report in the edgar_filing_refs shape (exchange="CNINFO"), or {}."""
    from src.markets.china import normalize, TOP_A_SHARES
    t = normalize(ticker)
    org = resolve_org(ticker) if t else None
    if not org:
        return {}
    reports = [r for r in periodic_reports(t, kinds=("annual",)) if r["kind"] == "annual"]
    latest = reports[0] if reports else None
    fy = None
    if latest:
        import re
        m = re.search(r"(20\d{2})年", latest["title"])
        fy = m.group(1) if m else str(int(latest["date"][:4]) - 1)
    name = (TOP_A_SHARES.get(t) or {}).get("name") or org.get("name_zh") or t
    viewer = (f"https://www.cninfo.com.cn/new/disclosure/stock?stockCode={org['code']}&orgId={org['org_id']}")
    return {
        "exchange": "CNINFO",
        "venue_label": "cninfo (CSRC-designated disclosure site)",
        "stock_code": org["code"],
        "company_name": name,
        "company_name_zh": org.get("name_zh"),
        "filing_type": "Annual Report (年度报告)",
        "filing_date": latest["date"] if latest else None,
        "period_of_report": f"{fy}-12-31" if fy else None,
        "fiscal_year": fy,
        "filing_url": latest["url"] if latest else None,
        "viewer_url": viewer,
        "is_foreign": True,
        "cik": None,
        "accession_number": None,
        "is_stub": latest is None,
        "is_ipo_prospectus": False,
    }


def latest_report_date(ticker: str, today: Optional[date] = None) -> Optional[str]:
    """Date (YYYY-MM-DD) the newest periodic report was published, or None. Feeds the earnings-freshness trigger
    (memory.assumption_extract.latest_earnings_event) for A-shares."""
    rows = periodic_reports(ticker, years_back=1, today=today)
    return rows[0]["date"] if rows else None
