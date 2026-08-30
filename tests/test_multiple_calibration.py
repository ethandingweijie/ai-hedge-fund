"""Street multiple against peer median — the observation, and what breaks it.

The system held both halves of this comparison and never joined them.
`multiple_calibration` records the join so a deposited report leaves evidence
behind instead of being read once for a Slack line.

Two rules the store exists to enforce:

  * an observation needs BOTH halves. A street multiple with no comp set, or
    a comp set with no report, is half an observation; storing it with a null
    would quietly corrupt any later aggregate.
  * an implausible spread is a parse error, not a view. A target price
    mistaken for a multiple would otherwise be banked as a 40x outlier and
    drag the industry median forever.

No network: both lookups are stubbed.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.memory import multiple_calibration as mc
from src.memory.analyst_basis import parse_pt_methodology

BASIS = {"house": "Goldman Sachs", "as_of": "2026-06", "method": "pe",
         "target_multiple": 18.0, "multiple_basis": "pe"}
CLASS = {"exchange": "NASDAQ", "industry": "Semiconductors",
         "sector": "Technology"}
COMPS = {"pe": {"value": 12.4, "peer_count": 14, "basis": "industry"}}


def _build(basis=BASIS, comps=COMPS, classification=CLASS):
    with patch("src.memory.analyst_basis.get_analyst_basis",
               return_value=basis), \
         patch("src.data.regional_comps.get_fmp_classification",
               return_value=classification), \
         patch("src.data.regional_comps.get_regional_multiples",
               return_value=comps):
        return mc.build_observation("MU")


# ── The observation ──────────────────────────────────────────────────────

def test_the_join_is_recorded_with_both_sides():
    obs = _build()
    assert obs["street_multiple"] == 18.0
    assert obs["peer_median"] == 12.4
    assert obs["peer_count"] == 14
    assert obs["peer_basis"] == "industry"
    assert obs["spread_pct"] == pytest.approx(18.0 / 12.4 - 1.0)
    assert obs["industry"] == "Semiconductors"


def test_no_observation_without_a_street_multiple():
    """A DCF or GGM note states a method and rates, not a multiple. That is
    not a failure — it simply is not a multiple observation."""
    assert _build(basis={"method": "dcf", "wacc": 0.095}) is None


def test_no_observation_without_a_peer_set():
    """Half an observation must not be stored as a null-sided row."""
    assert _build(comps={}) is None


def test_no_observation_for_an_unclassifiable_ticker():
    assert _build(classification={}) is None


def test_a_basis_with_no_comparable_field_is_skipped():
    """`sotp` has no comps column to be judged against."""
    assert _build(basis={**BASIS, "multiple_basis": "sotp"}) is None


def test_an_implausible_spread_is_treated_as_a_parse_error():
    """A target price captured as a multiple: 490x against a 12.4x median.
    Banking it would poison the industry median permanently."""
    assert _build(basis={**BASIS, "target_multiple": 490.0}) is None


def test_lookups_that_raise_yield_no_observation():
    with patch("src.memory.analyst_basis.get_analyst_basis",
               side_effect=RuntimeError("db down")):
        assert mc.build_observation("MU") is None


# ── Basis attribution: the DraftKings regression ─────────────────────────

def test_a_blended_line_pairs_the_multiple_with_the_nearest_metric():
    """DraftKings names two metrics in one sentence:

        "2.0x EV/Sales applied to our NTM+1 estimates and (2) a modified DCF
         using an EV/GAAP EBITDA multiple"

    The 2.0x is a sales multiple. Fixed pattern order matched EBITDA first
    and booked it as EV/EBITDA 2.0x against a 10.1x peer median — a
    fabricated -80% observation banked as real evidence.
    """
    got = parse_pt_methodology(
        "Equal blend of (1) 2.0x EV/Sales applied to our NTM+1 estimates and "
        "(2) a modified DCF using an EV/GAAP EBITDA multiple")
    assert got["target_multiple"] == 2.0
    assert got["multiple_basis"] == "p_s", "EBITDA sits further from the 2.0x"


@pytest.mark.parametrize("line, multiple, basis", [
    ("4.50x Q5-Q8 EV/EBITDA vs. 4.75x prior", 4.50, "ev_ebitda"),
    ("18X multiple applied to our normalized EPS estimate", 18.0, "pe"),
    ("lowering our US target multiple to 11.75x NTM+4 EBITDA", 11.75, "ev_ebitda"),
    ("28x P/E multiple to SNTM adjusted EPS", 28.0, "pe"),
    ("EV/Adj. EBITDA@9x FY25e + DPN book value", 9.0, "ev_ebitda"),
])
def test_single_metric_lines_are_unaffected(line, multiple, basis):
    got = parse_pt_methodology(line)
    assert got["target_multiple"] == multiple
    assert got["multiple_basis"] == basis


# ── Aggregation ──────────────────────────────────────────────────────────

def test_industry_calibration_reports_its_own_evidence_count():
    """The observation count is not decoration: a median from two notes and
    one from forty must not look alike to a caller."""
    rows = [{"spread_pct": 0.45}, {"spread_pct": 0.11}, {"spread_pct": -0.20}]
    with patch.object(mc, "ensure_calibration_table", return_value=None), \
         patch.object(mc.db, "query", return_value=rows):
        out = mc.get_industry_calibration("US", "Semiconductors", "pe")
    assert out["observations"] == 3
    assert out["median_spread_pct"] == pytest.approx(0.11)
    assert out["min_spread_pct"] == pytest.approx(-0.20)
    assert out["max_spread_pct"] == pytest.approx(0.45)


def test_no_calibration_from_an_empty_industry():
    with patch.object(mc, "ensure_calibration_table", return_value=None), \
         patch.object(mc.db, "query", return_value=[]):
        assert mc.get_industry_calibration("US", "Nothing", "pe") is None


def test_aggregation_never_raises_on_a_dead_store():
    with patch.object(mc, "ensure_calibration_table",
                      side_effect=RuntimeError("no db")):
        assert mc.get_industry_calibration("US", "Semiconductors", "pe") is None
        assert mc.calibration_summary() == []


def test_record_observations_skips_what_it_cannot_build():
    with patch.object(mc, "record_observation",
                      side_effect=[None, {"ticker": "MU"}, None]):
        out = mc.record_observations(["A", "MU", "C"])
    assert out == [{"ticker": "MU"}]


# ── The ratios table, which is where most multiples actually live ────────

AAPL_TABLE = [
    {"fiscal_year_label": "FY24", "pe": "50.8", "pb": "83.6",
     "ev_ebitda": "35.6", "dividend_yield": "0.3%"},
    {"fiscal_year_label": "FY25", "pe": "41.4", "pb": "62.9",
     "ev_ebitda": "32.3", "dividend_yield": "0.3%"},
    {"fiscal_year_label": "FY26e", "pe": "35.6", "pb": "51.0",
     "ev_ebitda": "27.6", "dividend_yield": "0.4%"},
    {"fiscal_year_label": "FY27e", "pe": "31.3", "pb": "39.0",
     "ev_ebitda": "23.7", "dividend_yield": "0.4%"},
]


def _with_table(rows):
    return patch("src.memory.assumption_store.get_analyst_reports",
                 return_value=[{"valuation_ratios": rows}])


def test_the_first_forward_column_is_used_not_the_historic_one():
    """Apple's real table. FY24/FY25 are historic; FY26e is the nearest
    estimate and the like-for-like against a forward peer median."""
    with _with_table(AAPL_TABLE):
        got = mc._forward_table_multiples("AAPL")
    assert got["target_multiple"] == 35.6, "FY26e P/E, not FY24's 50.8"
    assert got["multiple_basis"] == "pe"
    assert "FY26e" in got["source"]


def test_the_table_beats_the_methodology_sentence():
    """Apple's methodology line is "DCF, WACC 6.3%, g 3.5%" — no multiple at
    all, so this report yielded no observation before the table was read."""
    with patch("src.memory.analyst_basis.get_analyst_basis",
               return_value={"method": "dcf", "wacc": 0.063, "as_of": "2026-08"}), \
         _with_table(AAPL_TABLE), \
         patch("src.data.regional_comps.get_fmp_classification",
               return_value={"exchange": "NASDAQ", "industry": "Consumer Electronics",
                             "sector": "Technology"}), \
         patch("src.data.regional_comps.get_regional_multiples",
               return_value={"pe": {"value": 28.0, "peer_count": 35,
                                    "basis": "sector"}}):
        obs = mc.build_observation("AAPL")
    assert obs is not None, "a DCF note with a ratios table is still an observation"
    assert obs["street_multiple"] == 35.6
    assert obs["source"].startswith("ratios table")


@pytest.mark.parametrize("cell, expected", [
    ("36.5", 36.5), ("(2.3)", -2.3), ("1.9%", 1.9), ("1,250", 1250.0),
    ("NM", None), ("—", None), ("", None), (None, None), ("n/a", None),
])
def test_ratio_cells_parse_as_printed(cell, expected):
    """Cells arrive exactly as the note prints them, brackets and all."""
    assert mc._num(cell) == expected


def test_a_table_with_no_forward_column_yields_nothing():
    with _with_table([{"fiscal_year_label": "FY24", "pe": "50.8"}]):
        assert mc._forward_table_multiples("AAPL") is None


def test_a_forward_column_with_no_usable_metric_yields_nothing():
    with _with_table([{"fiscal_year_label": "FY26e", "pe": "NM",
                       "pb": "", "ev_ebitda": "—"}]):
        assert mc._forward_table_multiples("AAPL") is None


# ── Plausibility, learned from the first live backfill ───────────────────

@pytest.mark.parametrize("field, multiple, label", [
    ("pe", 0.7,  "CapitaLand India Trust — a P/E of 0.7 is not a P/E"),
    ("pe", 250.0, "above the pe band ceiling"),
    ("pb", 45.0,  "above the pb band ceiling"),
])
def test_an_implausible_street_multiple_is_rejected(field, multiple, label):
    """A ratios table is a grid, and a mis-read column produces a number that
    is arithmetically fine and financially impossible. Both real examples are
    REITs quoting P/NAV or DPU yield in the row taken for P/E; banked, they
    would have dragged a REIT median toward zero permanently."""
    basis = {"house": "X", "as_of": "2026-06", "method": "pe",
             "target_multiple": multiple, "multiple_basis": field
             if field == "pe" else "p_b"}
    comps = {field: {"value": 15.0, "peer_count": 12, "basis": "industry"}}
    assert _build(basis=basis, comps=comps) is None, label


def test_a_low_but_in_band_multiple_is_kept_and_that_is_a_known_limit():
    """Keppel DC REIT came back at "P/E 1.3x" — almost certainly P/NAV read
    from the P/E row, since REITs quote P/NAV and DPU yield rather than P/E.

    It is NOT rejected, and deliberately so: 1.3 clears the band and a -91%
    spread is within tolerance, and nothing mechanical distinguishes it from
    a genuinely distressed name at that magnitude. Rejecting it would need a
    rule that also discards real distress. The defence is statistical instead
    — medians over a peer floor, which is why the aggregate reports its own
    observation count.
    """
    basis = {**BASIS, "target_multiple": 1.3, "multiple_basis": "pe"}
    comps = {"pe": {"value": 14.7, "peer_count": 8, "basis": "industry"}}
    assert _build(basis=basis, comps=comps) is not None


def test_a_plausible_multiple_at_the_band_edge_is_kept():
    basis = {**BASIS, "target_multiple": 1.5, "multiple_basis": "pe"}
    comps = {"pe": {"value": 15.0, "peer_count": 12, "basis": "industry"}}
    assert _build(basis=basis, comps=comps) is not None


def test_the_spread_guard_is_tight_enough_for_a_misread_cell():
    """DKNG came back at +444% on the first backfill. A genuine street-vs-peer
    disagreement beyond 3x is vanishingly rare; a parse error is not."""
    from src.memory.multiple_calibration import _MAX_PLAUSIBLE_SPREAD
    assert _MAX_PLAUSIBLE_SPREAD <= 3.0
    basis = {**BASIS, "target_multiple": 97.5}
    comps = {"pe": {"value": 17.9, "peer_count": 13, "basis": "industry"}}
    assert _build(basis=basis, comps=comps) is None


# ── REITs quote P/NAV, not P/E ───────────────────────────────────────────

def test_a_reit_pnav_in_the_pe_column_is_re_labelled():
    """Keppel DC REIT's note shows "1.32" beside a 5.1% dividend yield and no
    P/B row at all. That is P/NAV — confirmed against the note — and read as
    a P/E it looks like a 91% discount to the peer median.

    Recovered rather than discarded: 1.32x against the REIT P/B median is a
    real observation, and Singapore has the thinnest coverage of any market.
    """
    fixed = mc._repair_reit_pe("AJBU.SI", {"pe": "1.32", "dividend_yield": "5.1%"})
    assert fixed["pe"] == ""
    assert mc._num(fixed["pb"]) == pytest.approx(1.32)


def test_an_identical_pe_and_pb_is_one_number_wearing_two_labels():
    """CapitaLand India Trust came back with pe 0.7 AND pb 0.7 — the
    extractor copied P/NAV into both slots. The P/B is right; the P/E is not,
    and needs no profile lookup to detect."""
    fixed = mc._repair_reit_pe("CY6U.SI", {"pe": "0.7", "pb": "0.7",
                                           "ev_ebitda": "17.1"})
    assert fixed["pe"] == ""
    assert mc._num(fixed["pb"]) == pytest.approx(0.7)
    assert fixed["ev_ebitda"] == "17.1", "other metrics must be untouched"


def test_a_real_pe_is_left_alone():
    row = {"pe": "20.2", "pb": "3.1"}
    assert mc._repair_reit_pe("MSFT", row) == row


def test_a_genuinely_low_pe_on_a_non_reit_survives():
    """The repair keys on REIT-ness or an exact pe/pb duplicate, not on
    lowness alone — a cheap industrial must keep its P/E."""
    row = {"pe": "4.2", "pb": "0.9"}
    assert mc._repair_reit_pe("D05.SI", row) == row


def test_the_repair_runs_inside_the_forward_lookup():
    rows = [{"fiscal_year_label": "FY26e", "pe": "1.32",
             "dividend_yield": "5.1%"}]
    with _with_table(rows):
        got = mc._forward_table_multiples("AJBU.SI")
    assert got["multiple_basis"] == "p_b", "must not be booked as a P/E"
    assert got["target_multiple"] == pytest.approx(1.32)
