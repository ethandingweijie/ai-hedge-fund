"""Reported segment profit -> operating profit, shared across markets.

ASC 280 and IFRS 8 report whatever the CODM reviews, and it is usually not
EBIT: Tencent's segment measure is GROSS PROFIT, Alibaba's ADJUSTED EBITA,
Kingboard's "segment results". The engine computes `ebit x (1-tax) x multiple`
on whatever it is handed, so any of those passed through unconverted
overstates -- Alibaba's segments sum to CNY 84.0bn of adjusted EBITA against
CNY 59.7bn of actual group operating profit, a 29% overstatement.
"""
import pytest

from src.tools.segment_normalize import normalize_segment_profit


def _segs():
    return [{"name": "A", "revenue": 600.0, "profit": 300.0, "margin": 0.5},
            {"name": "B", "revenue": 400.0, "profit": 100.0, "margin": 0.25}]


class TestReconciliation:
    def test_derived_profits_sum_to_group_operating_profit(self):
        segs = _segs()
        d = normalize_segment_profit(segs, 150.0)
        assert d["central_cost"] == pytest.approx(250.0)
        assert sum(s["operating_profit"] for s in segs) == pytest.approx(150.0)

    def test_the_rule_does_not_need_to_know_the_measure(self):
        """Gross profit, adjusted EBITA and a bespoke "segment result" are the
        same arithmetic: the gap is whatever stands between the sum of the
        reported measure and the group total."""
        for label in ("Gross profit", "Adjusted EBITA", "Segment results"):
            segs = _segs()
            d = normalize_segment_profit(segs, 150.0, reported_label=label)
            assert d["reported_measure"] == label
            assert sum(s["operating_profit"] for s in segs) == pytest.approx(150.0)

    def test_a_negative_central_cost_is_legitimate(self):
        """Kingboard's "segment results" sum BELOW its group operating profit,
        because the measure excludes income the group total includes. Margins
        must move up, not be clamped at zero."""
        segs = _segs()
        d = normalize_segment_profit(segs, 500.0)
        assert d["central_cost"] == pytest.approx(-100.0)
        assert segs[0]["operating_profit"] > segs[0]["reported_profit"]
        assert sum(s["operating_profit"] for s in segs) == pytest.approx(500.0)


class TestProvenance:
    def test_reported_figures_are_never_overwritten(self):
        """A derived number that cannot be traced back to what the filer
        actually said is worse than no number."""
        segs = _segs()
        normalize_segment_profit(segs, 150.0, reported_label="Gross profit")
        assert segs[0]["reported_profit"] == 300.0
        assert segs[0]["reported_margin"] == 0.5
        assert segs[0]["profit"] != 300.0

    def test_the_split_is_labelled(self):
        assert normalize_segment_profit(_segs(), 150.0)["basis"] == "pro_rata_revenue"
        assert normalize_segment_profit(
            _segs(), 150.0, weights={"A": 3, "B": 1})["basis"] == "supplied_weights"


class TestSplit:
    def test_pro_rata_revenue_by_default(self):
        segs = _segs()
        normalize_segment_profit(segs, 150.0)
        assert segs[0]["operating_profit"] == pytest.approx(300.0 - 150.0)
        assert segs[1]["operating_profit"] == pytest.approx(100.0 - 100.0)

    def test_supplied_weights_override(self):
        segs = _segs()
        normalize_segment_profit(segs, 150.0, weights={"A": 3, "B": 1})
        assert segs[0]["operating_profit"] == pytest.approx(300.0 - 187.5)
        assert sum(s["operating_profit"] for s in segs) == pytest.approx(150.0)

    def test_partial_weights_fall_back_rather_than_half_apply(self):
        segs = _segs()
        assert normalize_segment_profit(
            segs, 150.0, weights={"A": 3})["basis"] == "pro_rata_revenue"


class TestRefusals:
    def test_no_group_operating_profit_means_no_derivation(self):
        assert normalize_segment_profit(_segs(), None) is None

    def test_a_single_priced_segment_is_not_enough(self):
        segs = [{"name": "A", "revenue": 600.0, "profit": 300.0},
                {"name": "B", "revenue": 400.0, "profit": None}]
        assert normalize_segment_profit(segs, 150.0) is None

    def test_zero_revenue_base_is_refused(self):
        segs = [{"name": "A", "revenue": 0.0, "profit": 1.0},
                {"name": "B", "revenue": 0.0, "profit": 1.0}]
        assert normalize_segment_profit(segs, 1.0) is None
