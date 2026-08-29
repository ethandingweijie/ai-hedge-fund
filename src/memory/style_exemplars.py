"""Retrieval-grounded writing style for the LLM summary.

The house-style rules in the PM prompt are hand-written: someone read the
deposited notes and encoded what they saw. Nothing about the register is
*learned*, so a new note in the archive changes nothing.

This module closes that loop the cheap way. It retrieves two or three
deposited notes that resemble the situation being written about and hands
their prose to the model as voice references.

Three design decisions, each forced by what the corpus actually is:

**No vector database.** There is no embeddings infrastructure here, and at
~24 notes a vector store discriminates worse than an exact tag match on
(sector, note type). Tag match is deterministic, debuggable and free.
Revisit at several hundred notes.

**Note type is derived, not stored.** `doc_path` holds a content-hashed
filename, so there is no title to read, and re-extracting the corpus to add
a column costs LLM calls for something `revisions_json` already answers: a
revision whose field is the rating IS an upgrade or a downgrade, revisions
without one are an estimate revision, and no revisions at all is a
maintenance note. 17 of 24 rows carry revisions.

**Exemplars never come from the subject company.** A note on the ticker we
are writing about is the one document whose numbers and conclusion could be
laundered into our own view without looking foreign. `get_analyst_thesis`
already supplies that note deliberately, as a view to argue with. This is a
different job — the exemplars are here for register only, and excluding the
subject makes any leaked figure obviously out of place.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

# ── House normalisation ──────────────────────────────────────────────────
# The store holds four spellings of one firm ("Phillip Securities Research",
# "PHILLIP SECURITIES RESEARCH", "PHILLIP SECURITIES RESEARCH (S" — truncated
# mid-"(SINGAPORE)" — and "Phillip Capital"), which fragments any grouping by
# house voice.
_HOUSE_CANON: list[tuple[str, str]] = [
    (r"phillip",            "Phillip Securities Research"),
    (r"goldman",            "Goldman Sachs"),
    (r"\bdbs\b",            "DBS Group Research"),
    (r"ocbc",               "OCBC Group Research"),
    (r"uob\s*kay\s*hian",   "UOB Kay Hian"),
    (r"maybank",            "Maybank Research"),
    (r"cgs",                "CGS International"),
    (r"morgan\s*stanley",   "Morgan Stanley"),
    (r"j\.?p\.?\s*morgan",  "J.P. Morgan"),
    (r"citi",               "Citi Research"),
    (r"nomura",             "Nomura"),
    (r"jefferies",          "Jefferies"),
]


def normalise_house(raw: Optional[str]) -> str:
    """Collapse spelling variants onto one canonical house name."""
    if not raw:
        return ""
    low = raw.strip().lower()
    for pattern, canon in _HOUSE_CANON:
        if re.search(pattern, low):
            return canon
    # Unknown house: title-case it and drop a dangling open paren from a
    # truncated "(SINGAPORE)".
    cleaned = re.sub(r"\s*\([^)]*$", "", raw.strip())
    return cleaned.title() if cleaned.isupper() else cleaned


# ── Note type ────────────────────────────────────────────────────────────

NOTE_TYPES = (
    "upgrade", "downgrade", "estimate-revision", "maintenance", "initiation",
)

_RATING_FIELD = re.compile(r"\brating\b|\brecommendation\b", re.I)


def classify_note_type(revisions: Any, rating: Optional[str] = None) -> str:
    """Derive the note's stance from its recorded revisions.

    A tonal match matters as much as a sector match: an upgrade and a
    maintenance note on the same company are written in different registers,
    and the summary being generated has a stance of its own.
    """
    rows: list = []
    if isinstance(revisions, str) and revisions.strip():
        try:
            rows = json.loads(revisions)
        except (ValueError, TypeError):
            rows = []
    elif isinstance(revisions, list):
        rows = revisions

    if not isinstance(rows, list) or not rows:
        # No revisions recorded at all — an initiation reads like a
        # maintenance note for our purposes, so do not guess between them.
        return "maintenance"

    for row in rows:
        if not isinstance(row, dict):
            continue
        if _RATING_FIELD.search(str(row.get("field", ""))):
            direction = str(row.get("direction", "")).strip().lower()
            if "upgrade" in direction or direction in {"up", "raise", "lift"}:
                return "upgrade"
            if "downgrade" in direction or direction in {"down", "cut"}:
                return "downgrade"
            # A rating line with an unreadable direction is still a change.
            if row.get("prior_value") and row.get("new_value") \
                    and row["prior_value"] != row["new_value"]:
                return "upgrade" if _is_more_positive(
                    str(row["new_value"]), str(row["prior_value"])) else "downgrade"

    return "estimate-revision"


_RATING_RANK = {
    "sell": 0, "reduce": 1, "underweight": 1, "underperform": 1,
    "neutral": 2, "hold": 2, "equal-weight": 2,
    "accumulate": 3, "add": 3, "overweight": 3, "outperform": 3,
    "buy": 4, "strong buy": 5,
}


def _is_more_positive(new: str, prior: str) -> bool:
    def rank(v: str) -> int:
        low = v.strip().lower()
        for name, score in sorted(_RATING_RANK.items(), key=lambda kv: -len(kv[0])):
            if name in low:
                return score
        return 2
    return rank(new) > rank(prior)


# ── Retrieval ────────────────────────────────────────────────────────────

def _sector_of(ticker: str) -> tuple[str, str]:
    try:
        from src.data.sector_profiles import get_wacc_profile_for_ticker
        sector, profile = get_wacc_profile_for_ticker(ticker)
        return sector or "", profile or ""
    except Exception:
        return "", ""


def _load_rows() -> list[dict]:
    """Every deposited note that carries usable prose."""
    try:
        from src.data import db
        rows = db.query(
            "SELECT ticker, house, analyst, report_date, rating, "
            "thesis_json, revisions_json FROM analyst_reports "
            "WHERE thesis_json IS NOT NULL"
        )
    except Exception:
        return []
    out: list[dict] = []
    for r in rows or []:
        d = dict(r) if not isinstance(r, dict) else r
        try:
            thesis = json.loads(d.get("thesis_json") or "{}")
        except (ValueError, TypeError):
            continue
        if not isinstance(thesis, dict) or not thesis.get("points"):
            continue
        d["thesis"] = thesis
        out.append(d)
    return out


def get_style_exemplars(
    ticker: str,
    note_type: Optional[str] = None,
    limit: int = 2,
) -> list[dict]:
    """Return up to `limit` notes to use as voice references.

    Ranked by how closely the situation matches: same sector and same
    stance first, then same sector, then same stance, then anything. The
    subject ticker is always excluded.
    """
    rows = _load_rows()
    if not rows:
        return []

    want_sector, _ = _sector_of(ticker)
    subject = (ticker or "").upper()

    scored: list[tuple[int, dict]] = []
    seen_tickers: set[str] = set()
    for row in rows:
        rt = (row.get("ticker") or "").upper()
        if not rt or rt == subject:
            continue
        row_sector, _ = _sector_of(rt)
        row_type = classify_note_type(row.get("revisions_json"), row.get("rating"))

        score = 0
        if want_sector and row_sector == want_sector:
            score += 2
        if note_type and row_type == note_type:
            score += 1
        scored.append((score, {
            "ticker":    rt,
            "house":     normalise_house(row.get("house")),
            "analyst":   row.get("analyst") or "",
            "as_of":     row.get("report_date") or "",
            "rating":    row.get("rating") or "",
            "note_type": row_type,
            "sector":    row_sector,
            "thesis":    row["thesis"],
        }))

    # Highest score first; stable within a score so output is deterministic.
    scored.sort(key=lambda sd: -sd[0])

    picked: list[dict] = []
    for _score, ex in scored:
        if ex["ticker"] in seen_tickers:
            continue          # one exemplar per company — variety over depth
        seen_tickers.add(ex["ticker"])
        picked.append(ex)
        if len(picked) >= max(1, limit):
            break
    return picked


# ── Prompt block ─────────────────────────────────────────────────────────

_STYLE_INSTRUCTION = (
    "  These are WRITING SAMPLES, not evidence. Copy the register only: how "
    "a claim opens, how a figure is woven into a sentence, how a risk is "
    "conceded. Their companies, numbers, ratings and conclusions are NOT "
    "about the company you are writing on and must never appear in your "
    "output."
)


def format_exemplar_block(exemplars: list[dict], max_points: int = 3) -> str:
    """Render exemplars for the prompt, prose only.

    Catalysts and risks are included because conceding a risk in one clause
    is exactly the move the register is being borrowed for.
    """
    if not exemplars:
        return "  (no style exemplars available)"
    lines = [_STYLE_INSTRUCTION, ""]
    for ex in exemplars:
        header = " ".join(x for x in (ex.get("house"), ex.get("as_of")) if x)
        lines.append(
            f"  --- sample: {ex['ticker']} ({header or 'sell-side'}) "
            f"[{ex.get('note_type', 'note')}] ---"
        )
        thesis = ex.get("thesis") or {}
        for label, key, cap in (("", "points", max_points),
                                ("Catalyst: ", "catalysts", 1),
                                ("Risk: ", "risks", 1)):
            for item in (thesis.get(key) or [])[:cap]:
                lines.append(f"    {label}{str(item)[:300]}")
        lines.append("")
    return "\n".join(lines).rstrip()
