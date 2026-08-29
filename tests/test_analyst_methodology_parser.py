"""The methodology line is the only structured record of what the street applied.

`pt_methodology_json` is stored as a plain string and re-parsed at read time
by `parse_pt_methodology`. Anything it fails to capture is simply lost: the
valuation falls back to a profile default and no audit line records that a
stated assumption was dropped.

Three failures were found by testing it against real broker lines:

  * Micron's "18X multiple applied to our normalized EPS estimate of $62"
    captured the 18x but left `method` unset — "EPS" is a P/E basis written
    without the letters P and E.
  * Flutter's "11.75x NTM+4 EBITDA" likewise, because the EV/EBITDA pattern
    demanded a literal "EV/".
  * Birkenstock's "a 9.5% WACC and 2.5% terminal growth rate" dropped the
    WACC while parsing the terminal growth sitting beside it in the same
    sentence — the WACC regex only matched the label-then-number order. That
    one is not cosmetic: WACC feeds the valuation.

Table-driven over the exact lines, so the next parser change has to keep
every one of them working.
"""

from __future__ import annotations

import pytest

from src.memory.analyst_basis import parse_pt_methodology

# (label, methodology line, expected subset of the parsed dict)
CASES = [
    (
        "micron-normalised-eps",
        "18X multiple (unchanged) applied to our normalized EPS estimate of $62",
        {"method": "pe", "target_multiple": 18.0, "multiple_basis": "pe"},
    ),
    (
        "hynix-averaged-pe",
        "Our 2026E/27E avg. P/E-based 12m TP is W3,500,000",
        {"method": "pe"},
    ),
    (
        "lululemon-ev-ebitda",
        "our target now based on 4.50x Q5-Q8 EV/EBITDA vs. 4.75x prior",
        {"method": "ev_ebitda", "target_multiple": 4.50,
         "multiple_basis": "ev_ebitda"},
    ),
    (
        "birkenstock-leading-wacc",
        "It is based on a DCF methodology with a 9.5% WACC and 2.5% "
        "terminal growth rate",
        {"method": "dcf", "wacc": 0.095, "terminal_growth": 0.025},
    ),
    (
        "flutter-bare-ebitda",
        "lowering our US target multiple to 11.75x NTM+4 EBITDA "
        "(vs. 12.0x previously)",
        {"method": "ev_ebitda", "target_multiple": 11.75,
         "multiple_basis": "ev_ebitda"},
    ),
    (
        "draftkings-ebitda-exit-dcf",
        "an equal blend of (1) EV/Sales applied to our NTM+1 estimates and "
        "(2) a modified DCF using an EV/GAAP EBITDA multiple",
        # A DCF with an EBITDA exit is a DCF; `dcf` is matched before
        # `ev_ebitda` precisely so this does not read as a multiple method.
        {"method": "dcf"},
    ),
    (
        "samsung-ev-ebitda-sotp",
        "Our 12m 2026-2027E EV/EBITDA-based SOTP target price is W490,000",
        {"method": "sotp"},
    ),
]


@pytest.mark.parametrize("label, line, expected",
                         CASES, ids=[c[0] for c in CASES])
def test_methodology_line_parses(label, line, expected):
    got = parse_pt_methodology(line)
    for field, want in expected.items():
        assert got.get(field) == want, (
            f"{label}: {field} parsed as {got.get(field)!r}, expected {want!r} "
            f"— from {line!r}"
        )


# ── Regressions the broadened patterns could have caused ─────────────────

def test_eps_pattern_does_not_hijack_a_dcf_line():
    """`\\beps\\b` is broad; `dcf` must still win when both appear."""
    got = parse_pt_methodology(
        "DCF valuation cross-checked against 20x FY27E EPS")
    assert got["method"] == "dcf"


def test_bare_ebitda_does_not_hijack_a_sotp_line():
    got = parse_pt_methodology(
        "SOTP valuation, segments valued on 8x EBITDA")
    assert got["method"] == "sotp"


def test_a_passing_mention_of_ebitda_is_not_a_method():
    """The bare-EBITDA branch requires a multiple beside it, so prose that
    merely names the metric must not claim the method."""
    got = parse_pt_methodology(
        "Margins are reported on an EBITDA basis throughout")
    assert got.get("method") != "ev_ebitda"


def test_no_multiple_is_invented_when_none_is_stated():
    got = parse_pt_methodology("Our 2026E/27E avg. P/E-based 12m TP is W3,500,000")
    assert "target_multiple" not in got


def test_empty_line_yields_no_assumptions():
    got = parse_pt_methodology("")
    assert got.get("method") is None
    assert "wacc" not in got and "target_multiple" not in got


@pytest.mark.parametrize("line, field, value", [
    ("Gordon Growth Model (COE: 8.6%, g: 3.3%)", "terminal_growth", 0.033),
    ("DCF (WACC 6.8%, Terminal g 4%)", "wacc", 0.068),
    ("DDM (Cost of Equity: 6.83%; Terminal g: 1.75%)", "cost_of_equity", 0.0683),
    ("DCF with 2.75% terminal growth rate", "terminal_growth", 0.0275),
])
def test_existing_corpus_lines_still_parse(line, field, value):
    """Real lines already in the archive — the broadened patterns must not
    disturb what was working."""
    assert parse_pt_methodology(line).get(field) == pytest.approx(value)
