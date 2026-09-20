"""The owner-set valuation constants and their calibration rule.

The constant exists because `Distributable CF Yield` capitalised (OCF -
maintenance capex) at the peer FREE cash flow yield, which is net of total
capex. Energy Transfer exposed it: $1.32bn of audited maintenance capex against
$5.68bn of D&A doubled the numerator and the leg priced ET at $49 on a $21
quote. These tests hold the two halves of the fix -- the basis is now the
owner's, and the owner's number moves only on a decision.

Offline: every feed is stubbed.
"""
from __future__ import annotations

import json
from datetime import date

import pytest

from src.data import valuation_constants as vc


@pytest.fixture()
def doc(tmp_path):
    p = tmp_path / "vc.json"
    p.write_text(json.dumps({
        "version": 1,
        "profiles": {
            "Midstream / Pipelines": {
                "target_dcf_yield": 0.0975,
                "benchmark": "AMZI_coverage_adjusted",
                "benchmark_detail": {"proxy_symbol": "AMLP", "index_yield": 0.07,
                                     "coverage_factor": 1.4},
                "tolerance_band": [0.085, 0.11],
                "inertia_bps": 50,
                "macro_triggers": {"ten_year_move_bps": 75, "index_yield_move_bps": 100},
                "observed_at_last_review": {"ten_year": 0.0501, "index_yield": 0.07},
                "last_reviewed": "2026-09-20",
                "effective_until": "2026-11-15",
            }
        }}), encoding="utf-8")
    return json.loads(p.read_text(encoding="utf-8")), p


MID = "Midstream / Pipelines"


class TestTheStoredConstant:
    def test_reads_the_profile_yield(self, doc):
        assert vc.target_dcf_yield(MID, doc[0]) == pytest.approx(0.0975)

    def test_an_unauthored_profile_is_none_not_a_default(self, doc):
        """None must stay None. A caller that substitutes a number here would
        reintroduce exactly the basis mismatch this module exists to end."""
        assert vc.target_dcf_yield("Refining & Marketing", doc[0]) is None
        assert vc.target_dcf_yield(None, doc[0]) is None

    def test_detail_carries_the_audit_trail(self, doc):
        d = vc.detail(MID, doc[0], today=date(2026, 9, 21))
        assert d["benchmark"] == "AMZI_coverage_adjusted"
        assert d["tolerance_band"] == [0.085, 0.11]
        assert d["review_due"] is False


class TestTheReviewClock:
    def test_scheduled_review_comes_due_after_the_quarter(self, doc):
        due, why = vc.review_due(MID, doc=doc[0], today=date(2026, 11, 16))
        assert due and "scheduled review passed" in why[0]

    def test_not_due_inside_the_quarter(self, doc):
        due, why = vc.review_due(MID, doc=doc[0], today=date(2026, 10, 1))
        assert not due and why == []

    def test_a_rate_move_triggers_out_of_cycle(self, doc):
        """Pipeline distributions compete with fixed income, so a 75bps move in
        the 10-year is its own trigger regardless of the calendar."""
        due, why = vc.review_due(MID, doc=doc[0], today=date(2026, 10, 1),
                                 ten_year=0.0501 + 0.008)
        assert due and "10-year Treasury moved" in why[0]

    def test_a_rate_move_below_the_trigger_does_not(self, doc):
        due, _ = vc.review_due(MID, doc=doc[0], today=date(2026, 10, 1),
                               ten_year=0.0501 + 0.005)
        assert not due

    def test_index_rerating_triggers_out_of_cycle(self, doc):
        due, why = vc.review_due(MID, doc=doc[0], today=date(2026, 10, 1),
                                 index_yield=0.07 + 0.011)
        assert due and "benchmark index yield moved" in why[0]


class TestCalibration:
    def test_the_formula_is_yield_times_coverage(self, doc):
        p = vc.calibrate(MID, index_yield=0.07, coverage_factor=1.4, doc=doc[0])
        assert p["raw_candidate"] == pytest.approx(0.098)

    def test_inertia_holds_a_small_move(self, doc):
        """A 25bps drift is noise; a cash-flow anchor that tracks it is price."""
        p = vc.calibrate(MID, index_yield=0.0714, coverage_factor=1.4, doc=doc[0])
        assert p["held_by_inertia"] is True
        assert p["proposed"] == pytest.approx(0.0975)
        assert p["changed"] is False

    def test_a_move_past_inertia_is_proposed(self, doc):
        p = vc.calibrate(MID, index_yield=0.074, coverage_factor=1.4, doc=doc[0])
        assert p["held_by_inertia"] is False
        assert p["proposed"] == pytest.approx(0.1036)
        assert p["move_bps"] == pytest.approx(61.0, abs=1.0)

    def test_outside_the_band_is_clamped_and_flagged(self, doc):
        """Clamping silently would hide the thing worth knowing: the benchmark
        has left the range the owner authored."""
        p = vc.calibrate(MID, index_yield=0.10, coverage_factor=1.4, doc=doc[0])
        assert p["raw_candidate"] == pytest.approx(0.14)
        assert p["proposed"] == pytest.approx(0.11)
        assert p["outside_band"] is True

    def test_calibrate_never_writes(self, doc):
        before = json.loads(doc[1].read_text(encoding="utf-8"))
        vc.calibrate(MID, index_yield=0.10, coverage_factor=1.4, doc=doc[0])
        assert json.loads(doc[1].read_text(encoding="utf-8")) == before

    def test_unknown_profile_raises(self, doc):
        with pytest.raises(KeyError):
            vc.calibrate("Nowhere", index_yield=0.07, doc=doc[0])


class TestAcceptance:
    def test_applying_records_value_clock_and_history(self, doc):
        d, p_path = doc
        prop = vc.calibrate(MID, index_yield=0.074, coverage_factor=1.4,
                            ten_year=0.0512, doc=d, today=date(2026, 11, 16))
        e = vc.apply_proposal(prop, reviewer="owner", doc=d, path=p_path)
        assert e["target_dcf_yield"] == pytest.approx(0.1036)
        assert e["last_reviewed"] == "2026-11-16"
        assert e["effective_until"] > "2026-11-16"
        assert e["observed_at_last_review"]["ten_year"] == pytest.approx(0.0512)
        assert len(e["history"]) == 1 and e["history"][0]["reviewer"] == "owner"
        # and the clock is reset by the acceptance
        assert vc.review_due(MID, doc=d, today=date(2026, 11, 17))[0] is False


class TestEnvOverride:
    def test_override_is_named_per_profile(self, monkeypatch):
        monkeypatch.setenv("DCF_YIELD_MIDSTREAM___PIPELINES", "0.12")
        assert vc.env_override(MID) == pytest.approx(0.12)
        assert vc.env_override("Refining & Marketing") is None

    def test_garbage_is_ignored_rather_than_raising(self, monkeypatch):
        monkeypatch.setenv("DCF_YIELD_MIDSTREAM___PIPELINES", "ten percent")
        assert vc.env_override(MID) is None


class TestTheShippedConfig:
    """The file the engine actually reads."""

    def test_midstream_is_authored_and_inside_its_own_band(self):
        d = vc.detail(MID)
        assert d is not None, "the engine's only DCF-yield profile must be authored"
        lo, hi = d["tolerance_band"]
        assert lo <= d["value"] <= hi

    def test_the_note_records_why_the_constant_exists(self):
        assert "maintenance capex" in (vc.entry(MID) or {}).get("note", "")
