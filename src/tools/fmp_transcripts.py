"""
src/tools/fmp_transcripts.py
============================
Workstream R1 channel 2 — earnings-call transcripts, auto-fetched.

Generalizes complacency/qualitative.py::_fetch_latest_transcript (which
caps content at 8k chars and is diag-only): full content, prepared-remarks
vs Q&A split, and quarter selection via /earning-call-transcript-dates.

Global coverage (2026-08): FMP serves HKEX and SGX transcripts as well as
US — Tencent back to 2013 (50 quarters), Alibaba 48, DBS back to 2020.
Symbols are normalised through the per-market to_fmp_code() helpers because
FMP rejects the repo's 5-digit HK canonical form (00700.HK) and requires
the 4-digit one (0700.HK).

Soft-fail contract: everything returns None / {} on any problem.
"""
from __future__ import annotations

import re
from typing import Optional

from src.tools.api import _STABLE, _fmp_get

# ── Symbol normalisation ────────────────────────────────────────────────────


def to_fmp_symbol(ticker: str) -> str:
    """Route a ticker to the symbol form FMP's transcript endpoints accept.

    US passes through untouched; HK goes 00700.HK -> 0700.HK; SG is already
    in FMP's form. Imports are local so this module stays importable when a
    market helper is unavailable.
    """
    t = (ticker or "").strip().upper()
    if not t:
        return t
    try:
        from src.tools.hk.ticker import is_hk_ticker, to_fmp_code as _hk_fmp
        if is_hk_ticker(t):
            return _hk_fmp(t)
    except Exception:
        pass
    try:
        from src.tools.sg.ticker import is_sg_ticker, to_fmp_code as _sg_fmp
        if is_sg_ticker(t):
            return _sg_fmp(t)
    except Exception:
        pass
    return t


# ── Prepared-remarks vs Q&A splitting ───────────────────────────────────────
#
# FMP transcripts are flat speaker-turn text — "Tim Cook: ..." — with no
# section headers. The header regex below therefore never matched on any real
# transcript (verified against AAPL, 0700.HK and D05.SI), so `qa` was always
# None and the whole call rode in prepared_remarks. It is kept as the first
# strategy because a minority of sources do emit an explicit header.
_QA_SPLIT_RE = re.compile(
    r"^\s*(?:questions?\s+and\s+answers?|q\s*&\s*a|question-and-answer\s+session)"
    r"\s*:?\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# A speaker turn: "Name Surname: " at the start of a line. Names are title
# case, may carry periods/hyphens/apostrophes, and are capped at 40 chars so
# a mid-sentence colon cannot masquerade as a turn.
_TURN_RE = re.compile(r"^([A-Z][A-Za-z.\-'’ ]{1,40}):[ \t]", re.MULTILINE)

# Short turns by the host/operator are hand-off patter ("Operator, may we
# have the first question, please?") and belong with the Q&A that follows,
# not with the prepared remarks that precede it.
_HANDOFF_MAX_CHARS = 400

_MODERATOR_NAMES = ("operator", "moderator", "conference operator")


def parse_turns(content: str) -> list[dict]:
    """Split a transcript into speaker turns.

    Returns [{speaker, start, end, text}, ...] in document order. Empty list
    when the text has no recognisable turn structure.
    """
    if not content:
        return []
    marks = [(m.group(1).strip(), m.start(), m.end())
             for m in _TURN_RE.finditer(content)]
    turns: list[dict] = []
    for i, (speaker, start, body_start) in enumerate(marks):
        end = marks[i + 1][1] if i + 1 < len(marks) else len(content)
        turns.append({
            "speaker": speaker,
            "start": start,
            "end": end,
            "text": content[body_start:end].strip(),
        })
    return turns


def _is_moderator(speaker: str, host: str) -> bool:
    s = (speaker or "").strip().lower()
    return s in _MODERATOR_NAMES or s == (host or "").strip().lower()


def _qa_boundary(content: str, turns: list[dict]) -> Optional[int]:
    """Character offset where Q&A begins, or None when undetectable.

    The discriminator is the first *outsider question*: a turn that asks
    something, by a speaker who did not open the call and has not spoken
    before, who is answered later by someone who had. Verified against three
    genuinely different shapes — AAPL (operator-driven, ~24.5k), 0700.HK
    (IR-moderated, ~22.7k), and D05.SI (an analyst briefing that is Q&A
    almost from the top, ~0.2k). An offset-percentage guard would have
    mis-split D05.SI, so there deliberately is not one.
    """
    if len(turns) < 3:
        return None
    host = turns[0]["speaker"]

    for i, turn in enumerate(turns):
        if i == 0 or "?" not in turn["text"]:
            continue
        speaker = turn["speaker"]
        if _is_moderator(speaker, host):
            continue
        earlier = {t["speaker"] for t in turns[:i]}
        if speaker in earlier:
            continue
        # An outsider's question only opens Q&A if someone who spoke during
        # the prepared remarks answers it.
        if not any(t["speaker"] in earlier for t in turns[i + 1:]):
            continue

        # Back up over host/operator hand-off patter immediately before the
        # question, but never past the opening turn — the introduction
        # belongs with the prepared remarks even on a Q&A-only call.
        idx = i
        while idx > 1:
            prev = turns[idx - 1]
            if (_is_moderator(prev["speaker"], host)
                    and len(prev["text"]) <= _HANDOFF_MAX_CHARS):
                idx -= 1
                continue
            break
        return turns[idx]["start"]
    return None


def split_sections(content: str) -> tuple[str, Optional[str]]:
    """(prepared_remarks, qa) — qa is None when no boundary was found.

    Soft-fails to the historical behaviour (everything in prepared_remarks)
    rather than guessing at a split it cannot justify.
    """
    if not content:
        return content, None

    header = _QA_SPLIT_RE.search(content)
    if header:
        return content[:header.start()].strip(), content[header.end():].strip()

    cut = _qa_boundary(content, parse_turns(content))
    if cut is None:
        return content, None
    return content[:cut].strip(), content[cut:].strip()


# Retained under the original private name for any existing importer.
_split_prepared_vs_qa = split_sections


# ── Fetch ───────────────────────────────────────────────────────────────────

def get_transcript_dates(ticker: str) -> list[dict]:
    """All quarters FMP has a transcript for, newest first.

    Rows: {date, fiscalYear, quarter}. FMP names the year field `fiscalYear`;
    `year` is accepted as a fallback for other response shapes.
    """
    rows = _fmp_get(
        f"{_STABLE}/earning-call-transcript-dates",
        {"symbol": to_fmp_symbol(ticker)},
        api_key=None,
        uncap=True,
    )
    if not isinstance(rows, list):
        return []
    dated = [r for r in rows if isinstance(r, dict) and r.get("date")]
    return sorted(dated, key=lambda r: r["date"], reverse=True)


def _row_year(row: dict) -> Optional[int]:
    """FMP returns `fiscalYear`; tolerate `year` from other shapes."""
    val = row.get("fiscalYear", row.get("year"))
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def fetch_earnings_transcript(
    ticker: str,
    year: Optional[int] = None,
    quarter: Optional[int] = None,
) -> Optional[dict]:
    """One earnings-call transcript (default: latest available).

    Returns:
        {ticker, symbol, year, quarter, date, content, prepared_remarks, qa,
         speakers, source}   — qa is None when no Q&A boundary was found.
    """
    symbol = to_fmp_symbol(ticker)
    call_date = ""
    if year is None or quarter is None:
        dates = get_transcript_dates(ticker)
        if not dates:
            return None
        latest = dates[0]
        year = year if year is not None else _row_year(latest)
        quarter = quarter if quarter is not None else latest.get("quarter")
        call_date = (latest.get("date") or "")[:10]
    if year is None or quarter is None:
        return None

    rows = _fmp_get(
        f"{_STABLE}/earning-call-transcript",
        {"symbol": symbol, "year": year, "quarter": quarter},
        api_key=None,
        uncap=True,
    )
    if not isinstance(rows, list) or not rows:
        return None
    row = rows[0]
    content = (row.get("content") or "").strip()
    if not content:
        return None
    prepared, qa = split_sections(content)
    date_str = (row.get("date") or call_date or "")[:10]
    turns = parse_turns(content)
    return {
        "ticker": ticker,
        "symbol": symbol,
        "year": int(year),
        "quarter": int(quarter),
        "date": date_str,
        "content": content,
        "prepared_remarks": prepared,
        "qa": qa,
        # Ordered, de-duplicated speaker list — the extraction layer uses it
        # to tell management apart from covering analysts.
        "speakers": list(dict.fromkeys(t["speaker"] for t in turns)),
        "source": f"Q{quarter} {year} earnings transcript (FMP)",
    }


def fetch_recent_transcripts(ticker: str, n: int = 4) -> list[dict]:
    """The n most recent transcripts (for trend/supersede checks)."""
    out = []
    for row in get_transcript_dates(ticker)[:n]:
        got = fetch_earnings_transcript(
            ticker, year=_row_year(row), quarter=row.get("quarter"))
        if got:
            out.append(got)
    return out
