"""
src/tools/edgar_earnings.py
===========================
Workstream R1 channel 1 — company earnings material, auto-fetched.

Finds the latest EARNINGS press release for a ticker straight from SEC
EDGAR, with zero manual deposits:

  * Foreign private issuers (BABA, JD, PDD, NIO, …) file NO 8-K and NO
    10-Q — their earnings live ONLY in 6-K + EX-99.1.  A plain "latest
    6-K" pick is wrong: FPIs file many non-earnings 6-Ks (AGM notices,
    corporate updates).  We therefore walk the ~6 most recent 6-K cover
    pages and pick the first whose EXHIBITS line mentions results /
    earnings keywords.
  * Domestic filers: latest 8-K carrying Item 2.02 (Results of Operations
    and Financial Condition).

Selection then clicks through the filing-index HTML page to the EX-99.1
attachment and strips it to plain text (tables PRESERVED as delimited
text — financial tables are the payload here, unlike evidence_sources'
_strip_html which decomposes them).

Verified mechanics (2026-08-24, live against SEC):
  * submissions JSON  https://data.sec.gov/submissions/CIK##########.json
    lists recent filings (form / filingDate / accessionNumber /
    primaryDocument / items).
  * Filing index      .../{acc-no-dashes}/{acc-dashes}-index.htm  is
    ALWAYS present; the -index.json variant is NOT guaranteed (404 on
    the BABA June-quarter filing) — we parse the HTML.
  * All requests carry _EDGAR_UA + a 0.12 s courtesy sleep (SEC policy:
    < 10 req/s, mandatory User-Agent).

Soft-fail contract: every public function returns None / {} on any
problem — a missing press release must never break a pipeline run.
"""
from __future__ import annotations

import re
import time
from typing import Optional

import requests

from src.tools.api import _EDGAR_UA, _edgar_get, _get_cik

_THROTTLE_S = 0.12
_MAX_COVERS_TO_WALK = 6          # 6-K covers to inspect (BABA filed 8 in 6 weeks)
_MAX_EXHIBIT_CHARS = 60_000      # press releases can be long; cap the payload

# Cover-page keywords that mark an EARNINGS 6-K (case-insensitive).
_EARNINGS_COVER_RE = re.compile(
    r"(financial\s+results|earnings|quarter(?:ly)?\s+results|"
    r"results\s+of\s+operations|announces.{0,40}results|"
    r"(?:june|march|september|december)\s+quarter)",
    re.IGNORECASE,
)

# Exhibit filenames we accept, in preference order.
_EXHIBIT_PREF_RE = [
    re.compile(r"ex[-_ ]?99[-._ ]?1.*\.(?:htm|html|txt)$", re.IGNORECASE),
    re.compile(r"ex[-_ ]?99.*\.(?:htm|html|txt)$", re.IGNORECASE),
    re.compile(r"ex[-_ ]?99.*", re.IGNORECASE),
]


def _edgar_get_text(url: str) -> Optional[str]:
    """GET a non-JSON EDGAR resource (cover page / index / exhibit) as text."""
    try:
        resp = requests.get(url, headers={"User-Agent": _EDGAR_UA}, timeout=20)
        time.sleep(_THROTTLE_S)
        if resp.status_code == 200:
            resp.encoding = resp.apparent_encoding or "utf-8"
            return resp.text
        print(f"  [edgar_earnings] {resp.status_code} {url[:90]}")
        return None
    except Exception as exc:
        print(f"  [edgar_earnings] network error: {exc}")
        return None


def _submissions(cik: str) -> Optional[dict]:
    data = _edgar_get(f"https://data.sec.gov/submissions/CIK{cik.zfill(10)}.json")
    return data if isinstance(data, dict) else None


def _recent_filings(subs: dict, form: str, limit: int) -> list[dict]:
    """Recent filings of one form, newest first, from the submissions JSON."""
    recent = (subs.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    dates = recent.get("filingDate") or []
    accs = recent.get("accessionNumber") or []
    primaries = recent.get("primaryDocument") or []
    items = recent.get("items") or []
    out = []
    for i, f in enumerate(forms):
        if f != form:
            continue
        out.append({
            "form": f,
            "filed": (dates[i] if i < len(dates) else "") or "",
            "accession": (accs[i] if i < len(accs) else "") or "",
            "primary": (primaries[i] if i < len(primaries) else "") or "",
            "items": (items[i] if i < len(items) else "") or "",
        })
        if len(out) >= limit:
            break
    return out


def _index_url(cik: str, accession: str) -> str:
    acc_nd = accession.replace("-", "")
    return (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
            f"{acc_nd}/{accession}-index.htm")


def _resolve_href(href: str, base_url: str) -> str:
    """SEC index hrefs come in three forms: absolute http(s) URLs,
    site-root-relative paths (/Archives/...), and bare filenames relative
    to the index directory.  Resolve each correctly."""
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return f"https://www.sec.gov{href}"
    return f"{base_url.rsplit('/', 1)[0]}/{href}"


def _exhibit_from_index(index_html: str, base_url: str) -> Optional[str]:
    """Pick the best EX-99.x attachment URL from a filing-index page.

    The index lists attachments as <a href="name.htm"> cells; hrefs are
    usually relative to the index directory but can be site-root-relative
    (seen live on the BABA June-quarter filing).
    """
    hrefs = re.findall(r'<a\s+href="([^"]+)"', index_html, re.IGNORECASE)
    for pref in _EXHIBIT_PREF_RE:
        for href in hrefs:
            name = href.rsplit("/", 1)[-1]
            if pref.search(name):
                return _resolve_href(href, base_url)
    return None


def _html_to_text(html: str) -> str:
    """HTML → plain text PRESERVING table structure (cells → ' | ',
    rows → newlines).  Press-release financial tables are the payload."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()
        for br in soup.find_all("br"):
            br.replace_with("\n")
        for row in soup.find_all("tr"):
            row.append("\n")
        for cell in soup.find_all(["td", "th"]):
            cell.append(" | ")
        for block in soup.find_all(["p", "div", "h1", "h2", "h3", "h4", "li"]):
            block.append("\n")
        text = soup.get_text()
        text = re.sub(r"[ \t\xa0]+", " ", text)
        text = re.sub(r" ?\n ?", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        # Lines that are nothing but ' | ' separators (empty table rows)
        text = "\n".join(
            ln for ln in text.split("\n") if ln.strip(" |")
        )
        return text.strip()
    except Exception:
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def _fetch_6k_press_release(cik: str, ticker: str) -> Optional[dict]:
    """FPI path: walk recent 6-K covers for the earnings one, click through."""
    subs = _submissions(cik)
    if not subs:
        return None
    sixks = _recent_filings(subs, "6-K", _MAX_COVERS_TO_WALK)
    for filing in sixks:
        primary = filing.get("primary") or ""
        acc_nd = filing["accession"].replace("-", "")
        base = (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}"
                f"/{acc_nd}")
        cover = _edgar_get_text(f"{base}/{primary}") if primary else None
        cover_text = _html_to_text(cover) if cover else ""
        if not _EARNINGS_COVER_RE.search(cover_text or ""):
            continue  # AGM / corporate-update 6-K — keep walking
        index = _edgar_get_text(_index_url(cik, filing["accession"]))
        if not index:
            continue
        exhibit_url = _exhibit_from_index(index, _index_url(cik, filing["accession"]))
        if not exhibit_url:
            continue
        exhibit_html = _edgar_get_text(exhibit_url)
        if not exhibit_html:
            continue
        text = _html_to_text(exhibit_html)[:_MAX_EXHIBIT_CHARS]
        if len(text.strip()) < 500:
            continue  # stub / redirect exhibit — not the press release
        title_m = _EARNINGS_COVER_RE.search(cover_text)
        return {
            "ticker": ticker,
            "form": "6-K",
            "filed": filing["filed"],
            "accession": filing["accession"],
            "exhibit_url": exhibit_url,
            "title_hint": (cover_text[:200] if cover_text else ""),
            "text": text,
            "source": "edgar_6k_ex99",
        }
    return None


def _fetch_8k_press_release(cik: str, ticker: str) -> Optional[dict]:
    """Domestic path: latest 8-K with Item 2.02 → EX-99.1."""
    subs = _submissions(cik)
    if not subs:
        return None
    eightks = _recent_filings(subs, "8-K", 8)
    for filing in eightks:
        items = filing.get("items") or ""
        if "2.02" not in items:
            continue
        index = _edgar_get_text(_index_url(cik, filing["accession"]))
        if not index:
            continue
        exhibit_url = _exhibit_from_index(index, _index_url(cik, filing["accession"]))
        if not exhibit_url:
            continue
        exhibit_html = _edgar_get_text(exhibit_url)
        if not exhibit_html:
            continue
        text = _html_to_text(exhibit_html)[:_MAX_EXHIBIT_CHARS]
        if len(text.strip()) < 500:
            continue
        return {
            "ticker": ticker,
            "form": "8-K",
            "filed": filing["filed"],
            "accession": filing["accession"],
            "exhibit_url": exhibit_url,
            "title_hint": f"8-K Item 2.02 ({items})",
            "text": text,
            "source": "edgar_8k_ex99",
        }
    return None


def get_earnings_press_release(ticker: str) -> Optional[dict]:
    """Latest earnings press release text for a ticker (soft-fail → None).

    Tries the FPI 6-K path first when the ticker has EDGAR presence and
    files 6-Ks; otherwise the domestic 8-K Item 2.02 path.  HK/SG-only
    listings have no CIK → clean None (the Drive-deposit channel covers
    those names instead).
    """
    try:
        cik = _get_cik(ticker)
        if not cik:
            return None
        subs = _submissions(cik)
        if not subs:
            return None
        forms = ((subs.get("filings") or {}).get("recent") or {}).get("form") or []
        if "6-K" in forms:
            got = _fetch_6k_press_release(cik, ticker)
            if got:
                return got
        if "8-K" in forms:
            return _fetch_8k_press_release(cik, ticker)
        return None
    except Exception as exc:
        print(f"  [edgar_earnings] {ticker}: {type(exc).__name__}: {exc}")
        return None


def get_reported_period_hint(ticker: str) -> Optional[str]:
    """Best-effort fiscal-period label from the latest press release title
    (e.g. 'June Quarter 2026'). Used to label assumption rows when the
    extractor cannot infer fiscal year/quarter itself."""
    pr = get_earnings_press_release(ticker)
    if not pr:
        return None
    hint = pr.get("title_hint") or ""
    m = re.search(
        r"((?:first|second|third|fourth|1st|2nd|3rd|4th|q[1-4])\s+(?:quarter|qtr)?|"
        r"(?:june|march|september|december)\s+quarter)[^.\n]{0,30}",
        hint, re.IGNORECASE)
    return m.group(1).strip() if m else (hint[:80] or None)
