"""The segment sum-of-the-parts, after the refiner audit (owner, 2026-09-20).

What it used to do: multiply segment REVENUE by a multiple picked from the
segment's NAME. Marathon's "Refining And Marketing" matched the word
"marketing", drew the ADVERTISING multiple of 6.5x, and the leg published
$2,621 a share against a $425 quote on an implied enterprise value of $832.9bn.
Valero's "Refining" matched nothing at all and took the 3.0x default. Phillips
66's segmentation was 61% an accounting line called "Consolidation,
Eliminations", valued at 3.0x like a business.

What it does now: identifies the business, estimates segment EBITDA from the
peer margin, and applies the owner's through-cycle EV/EBITDA band.
"""
from __future__ import annotations

import pytest

from src.agents.analysis import dcf_agent as d


class TestSegmentIdentification:
    @pytest.mark.parametrize("name,expected", [
        ("Refining & Marketing", "refining"),
        ("Refining And Marketing", "refining"),
        ("Refining", "refining"),
        ("Midstream", "midstream"),
        ("Chemicals", "chemicals"),
        ("Renewable Diesel", "renewable_fuels"),
        ("Neat SAF", "renewable_fuels"),
        ("Ethanol", "ethanol"),
        ("M&S", "fuel_marketing"),
        ("Marketing and Specialties", "fuel_marketing"),
    ])
    def test_energy_segments_are_identified_by_business(self, name, expected):
        assert d._classify_energy_segment(name) == expected

    def test_refining_and_marketing_is_not_advertising(self):
        """The $807.6bn line: 'marketing' in a refiner's segment name matched
        the advertising rule, which carries a 6.5x REVENUE multiple."""
        assert d._classify_energy_segment("Refining And Marketing") == "refining"
        # and the generic classifier, which is what it used to reach, still says
        # advertising -- so the energy pass has to run first, not instead.
        assert d._classify_segment("Refining And Marketing")[0] == "advertising"

    def test_the_xbrl_member_identifies_a_product_row(self):
        """Valero's note breaks refining into product lines whose names say
        nothing; the member they carry says which segment they are."""
        assert d._classify_energy_segment(
            "Gasoline and Blendstocks", "us-gaap_StatementBusinessSegmentsAxis=vlo_RefiningMember"
        ) == "refining"

    def test_a_non_energy_segment_is_left_to_the_generic_table(self):
        assert d._classify_energy_segment("iPhone") is None
        assert d._classify_energy_segment("Cloud Services") is None


class TestAccountingLines:
    @pytest.mark.parametrize("name", [
        "Consolidation, Eliminations", "Intersegment eliminations",
        "Corporate and Other", "Unallocated",
    ])
    def test_accounting_lines_are_not_businesses(self, name):
        assert d._is_non_business_segment(name) is True

    def test_a_real_segment_is_not_excluded(self):
        assert d._is_non_business_segment("Midstream") is False

    def test_an_elimination_line_is_valued_at_nothing_and_labelled(self):
        parts = d._sotp_parts({"Consolidation, Eliminations": 55.8e9, "Refining": 74.9e9})
        elim = next(p for p in parts if p["type"] == "non_business")
        assert elim["ev"] == 0.0
        assert "not a business" in elim["note"]


class TestValuation:
    def test_a_segment_is_priced_on_ebitda_not_revenue(self):
        parts = d._sotp_parts({"Refining": 100e9})
        p = parts[0]
        assert p["basis"] == "ev_ebitda"
        # revenue x peer margin = estimated EBITDA, then the owner's band
        assert p["ebitda_estimated"] == pytest.approx(100e9 * p["ebitda_margin"])
        assert p["ev"] == pytest.approx(p["ebitda_estimated"] * p["multiple"])
        # and the result is nothing like a 3x revenue multiple
        assert p["ev"] < 100e9

    def test_the_multiple_sits_at_the_owner_s_chosen_end_of_the_band(self, monkeypatch):
        """The STATIC path. An owner-accepted dynamic multiple overrides it
        (refining is at 4.50x since 2026-09-21); that path is covered in
        tests/test_dynamic_multiples.py, so it is switched off here."""
        from src.data import dynamic_multiples as dm
        monkeypatch.setattr(dm, "accepted", lambda t: None)
        p = d._sotp_parts({"Refining": 100e9})[0]
        lo, hi = p["band"]
        assert p["multiple"] == pytest.approx(d._band_multiple((lo, hi)))
        assert p["band_position"] == d._SEGMENT_BAND_POSITION

    def test_band_position_is_selectable(self):
        assert d._band_multiple((5.0, 6.5), "low") == 5.0
        assert d._band_multiple((5.0, 6.5), "mid") == 5.75
        assert d._band_multiple((5.0, 6.5), "high") == 6.5

    def test_midstream_is_worth_more_per_unit_of_ebitda_than_refining(self):
        """Fee-based infrastructure against the crack spread."""
        mid = d._sotp_parts({"Midstream": 10e9})[0]
        ref = d._sotp_parts({"Refining": 10e9})[0]
        assert mid["multiple"] > ref["multiple"]
        assert mid["ebitda_margin"] > ref["ebitda_margin"]

    def test_an_estimated_margin_says_it_is_an_estimate(self):
        """A margin with no peer basket behind it must not read as measured."""
        est = d._sotp_parts({"M&S": 80e9})[0]
        assert "estimate" in est["ebitda_margin_source"]
        measured = d._sotp_parts({"Refining": 80e9})[0]
        assert "n=" in measured["ebitda_margin_source"]

    def test_an_equity_accounted_segment_is_held_at_book(self):
        """Phillips 66's CPChem JV consolidates no revenue; a revenue multiple
        misses it entirely."""
        parts = d._sotp_parts({"Chemicals": 0.0}, assets={"Chemicals": 7.9e9})
        p = next(p for p in parts if p["segment"] == "Chemicals")
        assert p["basis"] == "carrying_value"
        assert p["ev"] == pytest.approx(7.9e9)
        assert p["multiple"] is None

    def test_a_zero_revenue_segment_with_no_assets_is_dropped(self):
        assert d._sotp_parts({"Chemicals": 0.0}) == []


class TestTheRecord:
    def test_every_part_carries_its_working(self):
        p = d._sotp_parts({"Midstream": 21e9})[0]
        for k in ("segment", "type", "basis", "revenue", "ebitda_margin",
                  "ebitda_margin_source", "ebitda_estimated", "band", "multiple", "ev"):
            assert k in p, k

    def test_the_block_the_report_reads_mirrors_the_leg(self):
        parts = d._sotp_parts({"Refining": 74.9e9, "Midstream": 21.2e9})
        total = sum(x["ev"] for x in parts)
        base = {"leg_inputs": {"SOTP (segments)": {
            "segments": parts, "metric_value": total, "value": 123.45,
            "priced_share_of_revenue": 1.0}}}
        blk = d._segment_sotp_block(base, 400e6, "USD")
        assert blk["value_per_share"] == 123.45
        assert blk["total_ev"] == pytest.approx(total)
        assert sum(r["share_of_ev"] for r in blk["segments"]) == pytest.approx(1.0)
        assert "not a disclosed figure" in blk["basis_note"]

    def test_no_segments_means_no_block(self):
        assert d._segment_sotp_block({"leg_inputs": {}}, 1e6, "USD") is None


class TestItSumsTo100:
    """A sum of the parts has to sum (owner, 2026-09-20).

    Two independent ways it can fail, and arithmetic only sees one of them.
    """

    def _block(self, parts, priced=1.0):
        total = sum(p["ev"] for p in parts)
        return d._segment_sotp_block(
            {"leg_inputs": {"SOTP (segments)": {
                "segments": parts, "metric_value": total, "value": 100.0,
                "priced_share_of_revenue": priced}}}, 400e6, "USD")

    def test_a_complete_sotp_sums_to_100_and_says_nothing(self):
        parts = d._sotp_parts({"Refining": 74.9e9, "Midstream": 21.2e9})
        blk = self._block(parts)
        assert blk["checks"]["share_of_ev_sums_to_100"] is True
        assert blk["checks"]["share_of_ev_sum"] == pytest.approx(1.0)
        assert blk["checks"]["revenue_fully_priced"] is True
        assert blk["checks"]["reminders"] == []

    def test_partly_priced_revenue_is_reminded_even_though_the_shares_sum(self):
        """Phillips 66 before its bands were set: the parts summed to exactly
        100% of a total covering 51% of the company. The shares cannot see it."""
        parts = d._sotp_parts({"Refining": 74.9e9, "Midstream": 21.2e9})
        blk = self._block(parts, priced=0.51)
        assert blk["checks"]["share_of_ev_sums_to_100"] is True
        assert blk["checks"]["revenue_fully_priced"] is False
        assert any("51%" in r for r in blk["checks"]["reminders"])

    def test_the_reminder_names_the_unpriced_segments(self):
        parts = d._sotp_parts({"Refining": 74.9e9, "Hydrogen ventures": 5e9})
        blk = self._block(parts, priced=0.88)
        joined = " ".join(blk["checks"]["reminders"])
        assert "Hydrogen ventures" in joined or "carries a multiple" in joined

    def test_shares_that_do_not_sum_are_reported(self):
        parts = [{"segment": "A", "revenue": 1e9, "ev": 6e9, "type": "refining",
                  "basis": "ev_ebitda", "multiple": 6.0},
                 {"segment": "B", "revenue": 1e9, "ev": -1e9, "type": "refining",
                  "basis": "ev_ebitda", "multiple": 6.0}]
        blk = d._segment_sotp_block(
            {"leg_inputs": {"SOTP (segments)": {
                "segments": parts, "metric_value": 6e9, "value": 10.0,
                "priced_share_of_revenue": 1.0}}}, 1e6, "USD")
        assert blk["checks"]["share_of_ev_sums_to_100"] is False
        assert any("not 100%" in r for r in blk["checks"]["reminders"])
