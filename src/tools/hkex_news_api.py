"""
HKEXnews company announcements API — search filings by stock code.

Uses the HKEXnews title search form (JSF POST) to find announcements,
annual reports, and press releases for HKEX-listed companies.

Usage:
    from src.tools.hkex_news_api import search_hkex_announcements
    results = search_hkex_announcements("9988")  # Alibaba
    results = search_hkex_announcements("1211")  # BYD
"""

import json
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)


def search_hkex_announcements(
    stock_code: str,
    category: str = "-2",    # -2 = all categories
    max_results: int = 20,
) -> list[dict]:
    """
    Search HKEXnews for company announcements by stock code.

    Parameters
    ----------
    stock_code : str — HKEX stock code (e.g. "9988", "01211", "6862")
    category : str — t1code filter. -2 = all, 40000 = annual/interim reports
    max_results : int — maximum results to return

    Returns
    -------
    List of dicts with: date, code, name, category, title, url, ext, size
    """
    import requests
    from bs4 import BeautifulSoup

    session = requests.Session()
    session.headers["User-Agent"] = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    )

    # ── Step 1: GET page to obtain JSF ViewState ─────────────────────────
    try:
        r1 = session.get(
            "https://www1.hkexnews.hk/search/titlesearch.xhtml?lang=en",
            timeout=15,
        )
        soup1 = BeautifulSoup(r1.text, "html.parser")
    except Exception as e:
        logger.warning("HKEXnews page load failed: %s", e)
        return []

    # Extract all hidden form fields
    form = soup1.find("form")
    data: dict[str, str] = {}
    if form:
        for inp in form.find_all("input"):
            name = inp.get("name", "")
            val = inp.get("value", "")
            if name:
                data[name] = val

    if "javax.faces.ViewState" not in data:
        logger.warning("HKEXnews: no ViewState found")
        return []

    # ── Step 2: Resolve stock ID via autocomplete ────────────────────────
    raw_code = stock_code.replace(".HK", "").lstrip("0") or "0"
    try:
        r_auto = session.get(
            "https://www1.hkexnews.hk/search/prefix.do",
            params={"callback": "cb", "lang": "EN", "type": "A",
                    "name": raw_code, "market": "SEHK"},
            timeout=10,
        )
        auto_text = r_auto.text.strip()
        json_str = auto_text[auto_text.index("["):auto_text.rindex("]") + 1]
        auto_data = json.loads(json_str)
    except Exception as e:
        logger.warning("HKEXnews autocomplete failed for %s: %s", stock_code, e)
        return []

    if not auto_data:
        logger.info("HKEXnews: no match for stock code %s", stock_code)
        return []

    stock_id = str(auto_data[0].get("stockId", ""))
    stock_name = auto_data[0].get("name", "")

    # ── Step 3: POST search ──────────────────────────────────────────────
    data.update({
        "lang": "EN",
        "category": "0",
        "market": "SEHK",
        "searchType": "0",
        "documentType": "-1",
        "t1code": str(category),
        "t2Gcode": "-2",
        "t2code": "-2",
        "stockId": stock_id,
        "from": "",
        "to": "",
    })

    try:
        r2 = session.post(
            "https://www1.hkexnews.hk/search/titlesearch.xhtml?lang=en",
            data=data,
            timeout=15,
        )
        soup2 = BeautifulSoup(r2.text, "html.parser")
    except Exception as e:
        logger.warning("HKEXnews search POST failed: %s", e)
        return []

    # ── Step 4: Parse results ────────────────────────────────────────────
    results: list[dict] = []
    for tr in soup2.select("table tbody tr")[:max_results]:
        tds = tr.find_all("td")
        if len(tds) < 4:
            continue

        # Extract date
        date_text = tds[0].get_text(strip=True)
        # Clean "Release Time:" prefix
        date_text = re.sub(r"^Release\s+Time:\s*", "", date_text)

        # Extract stock code
        code_text = tds[1].get_text(strip=True)
        code_text = re.sub(r"^Stock\s+Code:\s*", "", code_text)

        # Extract company name
        name_text = tds[2].get_text(strip=True) if len(tds) > 2 else ""
        name_text = re.sub(r"^Stock\s+Short\s+Name:\s*", "", name_text)

        # Extract document info
        doc_td = tds[3]
        doc_text = doc_td.get_text(" ", strip=True)
        doc_text = re.sub(r"^Document:\s*", "", doc_text)

        # Extract PDF/HTM link
        links = [a.get("href", "") for a in doc_td.find_all("a") if a.get("href")]
        pdf_url = ""
        for link in links:
            if ".pdf" in link or ".htm" in link or "/listedco/" in link:
                pdf_url = link if link.startswith("http") else f"https://www1.hkexnews.hk{link}"
                break

        # Parse category and title from doc_text
        # Format: "Category TITLE (SIZE)"
        cat_match = re.match(r"^([\w\s&]+(?:-\s*\[.*?\])?\s*)", doc_text)
        category_name = ""
        title = doc_text
        size_match = re.search(r"\(\s*[\d.]+\s*[KMG]?B\s*\)", doc_text)
        size = size_match.group(0).strip("() ") if size_match else ""

        results.append({
            "date": date_text,
            "code": code_text[:5],
            "name": name_text or stock_name,
            "category": category_name,
            "title": title[:200],
            "url": pdf_url,
            "size": size,
        })

    logger.info("HKEXnews: found %d announcements for %s", len(results), stock_code)
    return results


def get_hkex_annual_reports(stock_code: str, limit: int = 5) -> list[dict]:
    """Search for annual/interim reports only (t1code=40000)."""
    return search_hkex_announcements(stock_code, category="40000", max_results=limit)


def get_hkex_filing_ref(stock_code: str) -> Optional[dict]:
    """Get the most recent filing reference for deep research citation."""
    reports = get_hkex_annual_reports(stock_code, limit=1)
    if not reports:
        return None
    r = reports[0]
    return {
        "exchange": "HKEX",
        "filing_type": "Annual Report",
        "filing_url": r["url"],
        "filing_date": r["date"],
        "company_name": r["name"],
        "stock_code": r["code"],
    }
