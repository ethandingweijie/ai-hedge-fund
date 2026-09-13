"""SOTP (segments) is promoted per ticker, copy-on-write, at a co-equal weight.

Promoting a method changes the blend for every name that shares the profile,
so the pilot is an explicit list rather than "any ticker whose filing parses".
"""
import pytest

from src.agents.analysis.dcf_agent import (
    _promote_segment_sotp, _SEGMENT_SOTP_TICKERS, _SEGMENT_SOTP_WEIGHT)

PROFILE = {"methods": [{"name": "EV/EBITDA", "weight": 0.6, "anchor": True,
                        "implementable": True},
                       {"name": "DCF", "weight": 0.4, "implementable": True}],
           "excluded": ["P/BV"]}


class TestPromotion:
    def test_adds_the_method_at_the_declared_weight(self):
        out, fired = _promote_segment_sotp(PROFILE, "00700.HK", True)
        assert fired is True
        by = {m["name"]: m["weight"] for m in out["methods"]}
        assert by["SOTP (segments)"] == _SEGMENT_SOTP_WEIGHT

    def test_weights_still_sum_to_one(self):
        out, _ = _promote_segment_sotp(PROFILE, "00700.HK", True)
        assert sum(m["weight"] for m in out["methods"]) == pytest.approx(1.0)

    def test_relative_ordering_of_existing_methods_is_preserved(self):
        """Scaling down keeps the ordering the profile author chose."""
        out, _ = _promote_segment_sotp(PROFILE, "00700.HK", True)
        by = {m["name"]: m["weight"] for m in out["methods"]}
        assert by["EV/EBITDA"] / by["DCF"] == pytest.approx(0.6 / 0.4)

    def test_copy_on_write_never_mutates_the_shared_profile(self):
        """Profile dicts are references into INDUSTRY_VALUATION_PROFILES; a
        mutation leaks the method into every later ticker sharing it."""
        before = [dict(m) for m in PROFILE["methods"]]
        out, _ = _promote_segment_sotp(PROFILE, "00700.HK", True)
        assert PROFILE["methods"] == before
        assert out is not PROFILE

    def test_other_keys_survive(self):
        out, _ = _promote_segment_sotp(PROFILE, "00700.HK", True)
        assert out["excluded"] == ["P/BV"]


class TestPromotionIsRefused:
    def test_a_ticker_outside_the_pilot_is_untouched(self):
        out, fired = _promote_segment_sotp(PROFILE, "AAPL", True)
        assert fired is False and out is PROFILE

    def test_no_segment_breakdown_means_no_promotion(self):
        out, fired = _promote_segment_sotp(PROFILE, "00700.HK", False)
        assert fired is False and out is PROFILE

    def test_a_profile_that_already_has_it_is_not_double_counted(self):
        p = {"methods": [{"name": "SOTP (segments)", "weight": 1.0}]}
        out, fired = _promote_segment_sotp(p, "00700.HK", True)
        assert fired is False and out is p

    def test_genscript_is_excluded_on_purpose(self):
        """Legend Biotech is an ASSOCIATE since the Oct-2024 deconsolidation,
        so it contributes no revenue segment: a segment SOTP omits 45.03% of
        its market value by construction. The look-through is its instrument."""
        assert "01548.HK" not in _SEGMENT_SOTP_TICKERS

    def test_the_pilot_is_the_three_names_whose_filings_carry_profit(self):
        assert _SEGMENT_SOTP_TICKERS == {"00700.HK", "01810.HK", "03690.HK"}

    def test_weight_is_co_equal_not_dominant(self):
        """The ANALYST SOTP carries 3.0 (=75%). A filing-derived map is better
        evidence, but this path still values each segment on a revenue
        multiple keyed off its name, so it earns a seat, not the table."""
        assert _SEGMENT_SOTP_WEIGHT == 0.40
