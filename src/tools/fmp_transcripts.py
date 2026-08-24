"""
src/tools/fmp_transcripts.py
============================
Workstream R1 channel 2 — earnings-call transcripts, auto-fetched.

Generalizes complacency/qualitative.py::_fetch_latest_transcript (which
caps content at 8k chars and is diag-only): full content, prepared-remarks
vs Q&A split, and quarter selection via /earning-call-transcript-dates.

Soft-fail contract: everything returns None / {} on any problem.
"""
from __future__ import annotations

import re
from typing import Optional

from src.tools.api import _STABLE, _fmp_get

# FMP content markers seen across transcripts (verified 2026-08):
# "Prepared Remarks:" / "Questions and Answers:" headers, plus [Operator]
# interjections.  Split leniently — if no marker exists the whole text
# rides in prepared_remarks with qa=None (consumers handle both).
_QA_SPLIT_RE = re.compile(
    r"^\s*(?:questions?\s+and\s+answers?|q\s*&\s*a|question-and-answer\s+session)"
    r"\s*:?\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def get_transcript_dates(ticker: str) -> list[dict]:
    """All quarters FMP has a transcript for, newest first.

    Rows: {date, year, quarter, ...}.
    """
    rows = _fmp_get(
        f"{_STABLE}/earning-call-transcript-dates",
        {"symbol": ticker},
        api_key=None,
        uncap=True,
    )
    if not isinstance(rows, list):
        return []
    dated = [r for r in rows if isinstance(r, dict) and r.get("date")]
    return sorted(dated, key=lambda r: r["date"], reverse=True)


def _split_prepared_vs_qa(content: str) -> tuple[str, Optional[str]]:
    m = _QA_SPLIT_RE.search(content)
    if not m:
        return content, None
    return content[: m.start()].strip(), content[m.end():].strip()


def fetch_earnings_transcript(
    ticker: str,
    year: Optional[int] = None,
    quarter: Optional[int] = None,
) -> Optional[dict]:
    """One earnings-call transcript (default: latest available).

    Returns:
        {ticker, year, quarter, date, content, prepared_remarks, qa,
         source}   — qa is None when no Q&A section marker was found.
    """
    if year is None or quarter is None:
        dates = get_transcript_dates(ticker)
        if not dates:
            return None
        latest = dates[0]
        year = year or latest.get("year")
        quarter = quarter or latest.get("quarter")
        call_date = (latest.get("date") or "")[:10]
    else:
        call_date = ""
    if year is None or quarter is None:
        return None

    rows = _fmp_get(
        f"{_STABLE}/earning-call-transcript",
        {"symbol": ticker, "year": year, "quarter": quarter},
        api_key=None,
        uncap=True,
    )
    if not isinstance(rows, list) or not rows:
        return None
    row = rows[0]
    content = (row.get("content") or "").strip()
    if not content:
        return None
    prepared, qa = _split_prepared_vs_qa(content)
    date_str = (row.get("date") or call_date or "")[:10]
    return {
        "ticker": ticker,
        "year": int(year),
        "quarter": int(quarter),
        "date": date_str,
        "content": content,
        "prepared_remarks": prepared,
        "qa": qa,
        "source": f"Q{quarter} {year} earnings transcript (FMP)",
    }


def fetch_recent_transcripts(ticker: str, n: int = 4) -> list[dict]:
    """The n most recent transcripts (for trend/supersede checks)."""
    out = []
    for row in get_transcript_dates(ticker)[:n]:
        got = fetch_earnings_transcript(
            ticker, year=row.get("year"), quarter=row.get("quarter"))
        if got:
            out.append(got)
    return out
