"""
tests/test_fmp_transcripts.py
=============================
Regression cover for src/tools/fmp_transcripts.py.

Two bugs made this module return None on every default call — for US as
well as the newly-covered HK/SG markets:

  1. FMP's /earning-call-transcript-dates response names the year field
     `fiscalYear`. The code read `year`, got None, and bailed. So
     fetch_earnings_transcript(ticker) with no explicit year/quarter always
     returned None, and fetch_recent_transcripts always returned [].
  2. Real FMP transcripts are flat speaker-turn text with no "Questions and
     Answers:" header, so the header regex never matched and `qa` was always
     None — the whole call rode in prepared_remarks.

Fixtures are real transcripts captured 2026-08-27, covering three
structurally different call shapes. Tests are offline (conftest strips live
keys); only the parsing/normalisation layer is exercised.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from src.tools.fmp_transcripts import (
    _row_year,
    parse_turns,
    split_sections,
    to_fmp_symbol,
)

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "transcripts"

# (fixture stem, expected Q&A boundary offset, minimum distinct speakers)
#
# AAPL    — US operator-driven call; prepared remarks run half the document.
# 0700.HK — HK IR-moderated call; same shape, different moderator idiom.
# D05.SI  — SGX analyst briefing that is Q&A from the second turn. This is
#           the case that kills any "Q&A must start after N% of the text"
#           heuristic, and the reason the splitter has no such guard.
SHAPES = [
    ("aapl_2026q3", 24466, 10),
    ("tencent_0700hk_2026q1", 22720, 12),
    ("dbs_d05si_2026q1", 193, 10),
]


def _load(stem: str) -> str:
    return (FIXTURES / f"{stem}.txt").read_text(encoding="utf-8")


# ── Bug 1: the fiscalYear key ───────────────────────────────────────────────

class TestRowYear:
    def test_reads_fiscal_year(self):
        """The shape FMP actually returns today."""
        assert _row_year({"quarter": 3, "fiscalYear": 2026, "date": "2026-07-30"}) == 2026

    def test_falls_back_to_year(self):
        """Older/other response shapes must keep working."""
        assert _row_year({"quarter": 3, "year": 2019}) == 2019

    def test_prefers_fiscal_year_when_both_present(self):
        assert _row_year({"fiscalYear": 2026, "year": 2025}) == 2026

    def test_missing_year_is_none_not_crash(self):
        assert _row_year({"quarter": 3}) is None
        assert _row_year({"fiscalYear": None}) is None
        assert _row_year({"fiscalYear": "not-a-year"}) is None

    def test_real_dates_fixture_has_no_year_key(self):
        """Guards the root cause: if FMP ever restores `year`, this fails and
        tells us the fallback is now load-bearing rather than vestigial."""
        rows = json.loads((FIXTURES / "aapl_dates.json").read_text(encoding="utf-8"))
        assert rows, "dates fixture is empty"
        assert "year" not in rows[0]
        assert "fiscalYear" in rows[0]
        assert _row_year(rows[0]) == 2026


# ── Bug 2: prepared-remarks vs Q&A splitting ────────────────────────────────

class TestSplitSections:
    @pytest.mark.parametrize("stem,expected,_min_speakers", SHAPES)
    def test_boundary_matches_known_offset(self, stem, expected, _min_speakers):
        content = _load(stem)
        prepared, qa = split_sections(content)
        assert qa is not None, f"{stem}: no Q&A section found (the old bug)"
        boundary = content.find(qa[:60])
        assert abs(boundary - expected) <= 50, (
            f"{stem}: Q&A boundary at {boundary}, expected ~{expected}"
        )

    @pytest.mark.parametrize("stem,_expected,_min_speakers", SHAPES)
    def test_sections_partition_the_content(self, stem, _expected, _min_speakers):
        """Nothing is dropped or duplicated across the split."""
        content = _load(stem)
        prepared, qa = split_sections(content)
        assert len(prepared) + len(qa) <= len(content)
        assert prepared in content
        assert qa in content
        assert content.index(prepared) < content.index(qa)

    def test_dbs_is_qa_dominant(self):
        """The shape that breaks percentage-based heuristics: an analyst
        briefing whose Q&A starts at the second turn."""
        prepared, qa = split_sections(_load("dbs_d05si_2026q1"))
        assert len(prepared) < 500
        assert len(qa) > 30_000

    def test_explicit_header_wins(self):
        """A source that does emit a header still splits on it."""
        content = "Alice Smith: Welcome.\n\nQuestions and Answers:\n\nBob Jones: My question?\n"
        prepared, qa = split_sections(content)
        assert "Welcome" in prepared
        assert "My question?" in qa

    def test_no_structure_soft_fails(self):
        """Unrecognisable text keeps the documented contract: everything in
        prepared_remarks, qa=None — never a guessed split."""
        prepared, qa = split_sections("Just some prose with no speaker turns at all.")
        assert qa is None
        assert prepared.startswith("Just some prose")

    def test_empty_content(self):
        assert split_sections("") == ("", None)

    def test_monologue_has_no_qa(self):
        """One speaker, no outsider question — must not invent a boundary."""
        content = "Alice Smith: " + "Prepared remarks. " * 50
        prepared, qa = split_sections(content)
        assert qa is None


class TestParseTurns:
    @pytest.mark.parametrize("stem,_expected,min_speakers", SHAPES)
    def test_turns_and_speakers(self, stem, _expected, min_speakers):
        turns = parse_turns(_load(stem))
        assert len(turns) > 20
        speakers = {t["speaker"] for t in turns}
        assert len(speakers) >= min_speakers

    def test_turns_are_ordered_and_contiguous(self):
        turns = parse_turns(_load("aapl_2026q3"))
        for a, b in zip(turns, turns[1:]):
            assert a["end"] == b["start"]
            assert a["start"] < b["start"]

    def test_midsentence_colon_is_not_a_turn(self):
        """A colon inside prose must not be read as a speaker label."""
        content = ("Alice Smith: Here is the point: revenue grew.\n"
                   "Bob Jones: Thanks?\n"
                   "Alice Smith: Sure.\n")
        speakers = [t["speaker"] for t in parse_turns(content)]
        assert speakers == ["Alice Smith", "Bob Jones", "Alice Smith"]

    def test_empty_content(self):
        assert parse_turns("") == []


# ── Symbol normalisation ────────────────────────────────────────────────────

class TestToFmpSymbol:
    @pytest.mark.parametrize("raw,expected", [
        # US passes through
        ("AAPL", "AAPL"),
        ("aapl", "AAPL"),
        # HK: FMP rejects the repo's 5-digit canonical form and needs 4-digit
        ("00700.HK", "0700.HK"),
        ("0700.HK", "0700.HK"),
        ("700", "0700.HK"),
        ("9988", "9988.HK"),
        ("09988.HK", "9988.HK"),
        # genuine 5-digit HK codes (RMB counters) must survive intact
        ("80700", "80700.HK"),
        ("80700.HK", "80700.HK"),
        # SG already matches FMP's form
        ("D05", "D05.SI"),
        ("D05.SI", "D05.SI"),
        ("d05.si", "D05.SI"),
    ])
    def test_normalisation(self, raw, expected):
        assert to_fmp_symbol(raw) == expected

    def test_empty_is_passthrough(self):
        assert to_fmp_symbol("") == ""
        assert to_fmp_symbol(None) == ""
