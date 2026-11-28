"""DART (dart.fss.or.kr) annual-report resolver for Korean listings -- the KRX counterpart of the EDGAR / HKEXnews
resolvers in src/agents/industry/edgar_hkex_resolver.py.

DART is the FSS electronic disclosure system: every KRX issuer files its Business Report (사업보고서, the annual
report, K-IFRS consolidated and separate statements), half-year and quarterly reports there. The OpenAPI
(opendart.fss.or.kr) needs a free API key (``DART_API_KEY``, the ``crtfc_key`` parameter) that the OWNER has to
register for; without one this resolver returns {} and the pipeline uses FMP attribution, exactly as SGX does.

Two calls:
  1. ``corpCode.xml`` -- a zip of every filer's 8-digit corp_code keyed by 6-digit stock code. Cached on disk for
     7 days (it changes only when issuers list or delist).
  2. ``list.json`` -- the issuer's periodic reports (pblntf_detail_ty=A001: Business Report), newest first.

Never raises: a missing key, a failed call or no filing all return {}.
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
import threading
import time
import zipfile
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from src.markets import korea

_log = logging.getLogger(__name__)

API = "https://opendart.fss.or.kr/api"
VIEWER = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"
SEARCH = "https://dart.fss.or.kr/dsab007/main.do"
_TIMEOUT = 20
_CORP_TTL_S = 7 * 86400
_CACHE_DIR = Path(os.environ.get("DART_CACHE_DIR", Path(__file__).resolve().parents[2] / ".cache" / "dart"))
_LOCK = threading.Lock()
_MEM: dict = {"corp": None, "at": 0.0}

#: "사업보고서 (2025.12)" -> period 2025-12
_PERIOD = re.compile(r"\((\d{4})\.(\d{2})\)")


def api_key() -> str:
    return (os.environ.get("DART_API_KEY") or "").strip()


def _get(path: str, params: dict):
    import requests
    return requests.get(f"{API}/{path}", params={"crtfc_key": api_key(), **params}, timeout=_TIMEOUT)


def parse_corp_codes(xml_bytes: bytes) -> dict[str, dict]:
    """{stock_code: {corp_code, corp_name}} from CORPCODE.xml (listed filers only)."""
    import xml.etree.ElementTree as ET
    out: dict[str, dict] = {}
    root = ET.fromstring(xml_bytes)
    for node in root.iter("list"):
        sc = (node.findtext("stock_code") or "").strip()
        cc = (node.findtext("corp_code") or "").strip()
        if sc and cc:
            out[sc] = {"corp_code": cc, "corp_name": (node.findtext("corp_name") or "").strip()}
    return out


def _corp_codes() -> dict[str, dict]:
    with _LOCK:
        if _MEM["corp"] is not None and time.time() - _MEM["at"] < _CORP_TTL_S:
            return _MEM["corp"]
    cf = _CACHE_DIR / "corp_codes.json"
    try:
        if cf.exists() and time.time() - cf.stat().st_mtime < _CORP_TTL_S:
            data = json.loads(cf.read_text(encoding="utf-8"))
            with _LOCK:
                _MEM.update(corp=data, at=time.time())
            return data
    except Exception:                                      # noqa: BLE001
        pass
    if not api_key():
        return {}
    try:
        resp = _get("corpCode.xml", {})
        if resp.status_code != 200:
            return {}
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            name = next((n for n in zf.namelist() if n.lower().endswith(".xml")), None)
            if not name:
                return {}
            data = parse_corp_codes(zf.read(name))
    except Exception as exc:                               # noqa: BLE001
        _log.warning("DART corpCode fetch failed: %s", exc)
        return {}
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cf.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:                                      # noqa: BLE001
        pass
    with _LOCK:
        _MEM.update(corp=data, at=time.time())
    return data


def pick_annual_report(rows: list[dict]) -> Optional[dict]:
    """The newest ORIGINAL or amended Business Report among list.json rows (corrections '[기재정정]' are kept: they
    replace the original); None when there is none."""
    reports = [r for r in rows or [] if isinstance(r, dict) and "사업보고서" in str(r.get("report_nm") or "")]
    if not reports:
        return None
    return max(reports, key=lambda r: str(r.get("rcept_dt") or ""))


def to_filing_ref(row: dict, ticker: str, corp: dict) -> dict:
    """The edgar_filing_refs-compatible dict (exchange='DART') for one list.json row."""
    rcept = str(row.get("rcept_no") or "")
    rdt = str(row.get("rcept_dt") or "")
    m = _PERIOD.search(str(row.get("report_nm") or ""))
    period = f"{m.group(1)}-{m.group(2)}" if m else ""
    url = VIEWER.format(rcept_no=rcept) if rcept else ""
    return {
        "exchange": "DART",
        "company_name": str(row.get("corp_name") or corp.get("corp_name") or ticker),
        "corp_code": corp.get("corp_code"),
        "filing_type": "Business Report (사업보고서)",
        "accession_number": rcept,
        "filing_date": f"{rdt[:4]}-{rdt[4:6]}-{rdt[6:8]}" if len(rdt) == 8 else rdt,
        "period_of_report": period,
        "fiscal_year": m.group(1) if m else "",
        "filing_url": url,
        "viewer_url": url or SEARCH,
        "is_foreign": False,
    }


def get_dart_filing_refs(ticker: str, today: Optional[date] = None) -> dict:
    """The latest Business Report for a Korean listing, or {} (no key, not Korean, nothing filed, any failure).
    A preferred line resolves through its common line's stock code (DART files by issuer)."""
    common = korea.common_of(ticker)
    if not common or not api_key():
        return {}
    corp = _corp_codes().get(common[:6])
    if not corp:
        return {}
    end = today or date.today()
    try:
        resp = _get("list.json", {"corp_code": corp["corp_code"],
                                  "bgn_de": (end - timedelta(days=800)).strftime("%Y%m%d"),
                                  "end_de": end.strftime("%Y%m%d"),
                                  "pblntf_ty": "A", "pblntf_detail_ty": "A001", "page_count": 20})
        if resp.status_code != 200:
            return {}
        body = resp.json() or {}
        if str(body.get("status")) != "000":
            return {}
        row = pick_annual_report(body.get("list") or [])
    except Exception as exc:                               # noqa: BLE001
        _log.warning("DART list fetch failed for %s: %s", ticker, exc)
        return {}
    return to_filing_ref(row, ticker, corp) if row else {}
