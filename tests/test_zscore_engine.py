"""Tests for V4-β Z-Score Engine — peer-cohort normalisation."""
from __future__ import annotations

import pytest

from src.data.zscore_engine import (
    compute_z_scores,
    z_tier_kicker,
    augment_metrics_with_z_scores,
    _median,
    _mad,
)


# ── Pure stat helpers ─────────────────────────────────────────────────────────

class TestMedian:
    def test_odd_length(self):
        assert _median([1.0, 2.0, 3.0]) == 2.0

    def test_even_length(self):
        assert _median([1.0, 2.0, 3.0, 4.0]) == 2.5

    def test_unsorted_input(self):
        assert _median([3.0, 1.0, 2.0]) == 2.0


class TestMad:
    def test_zero_when_all_equal(self):
        assert _mad([5.0, 5.0, 5.0], 5.0) == 0.0

    def test_symmetric_distribution(self):
        # values [1, 3, 5, 7, 9], median=5, deviations=[4,2,0,2,4], MAD=2
        assert _mad([1.0, 3.0, 5.0, 7.0, 9.0], 5.0) == 2.0


# ── compute_z_scores ──────────────────────────────────────────────────────────

class TestComputeZScores:
    def test_top_decile_value(self):
        cohort = {"nrr_pct": [1.05, 1.10, 1.15, 1.20, 1.25, 1.30]}
        result = compute_z_scores("Growth SaaS", {"nrr_pct": 1.40}, cohort=cohort)
        assert "nrr_pct" in result
        assert result["nrr_pct"]["z"] > 1.5
        assert result["nrr_pct"]["cohort_size"] == 6

    def test_below_median_value(self):
        cohort = {"rule_of_40_score": [30, 40, 50, 60, 70]}
        result = compute_z_scores("Growth SaaS", {"rule_of_40_score": 35}, cohort=cohort)
        assert "rule_of_40_score" in result
        assert result["rule_of_40_score"]["z"] < 0

    def test_skip_when_cohort_too_small(self):
        cohort = {"nrr_pct": [1.10, 1.15]}  # only 2 peers
        result = compute_z_scores("Growth SaaS", {"nrr_pct": 1.30}, cohort=cohort)
        assert "nrr_pct" not in result

    def test_skip_when_mad_zero(self):
        cohort = {"nrr_pct": [1.10, 1.10, 1.10, 1.10]}  # all identical → MAD=0
        result = compute_z_scores("Growth SaaS", {"nrr_pct": 1.20}, cohort=cohort)
        assert "nrr_pct" not in result

    def test_skip_non_numeric_value(self):
        cohort = {"region": [1.0, 2.0, 3.0]}
        result = compute_z_scores("Growth SaaS", {"region": "NA"}, cohort=cohort)
        assert "region" not in result

    def test_skip_underscore_metadata_keys(self):
        cohort = {"_completeness_score": [0.5, 0.6, 0.7, 0.8]}
        result = compute_z_scores("Insurance", {"_completeness_score": 0.9}, cohort=cohort)
        assert "_completeness_score" not in result

    def test_handles_none_value(self):
        cohort = {"combined_ratio": [0.95, 0.97, 0.99, 1.01]}
        result = compute_z_scores("Insurance", {"combined_ratio": None}, cohort=cohort)
        assert "combined_ratio" not in result

    def test_kpi_not_in_cohort_skipped(self):
        cohort = {"nrr_pct": [1.10, 1.15, 1.20]}
        result = compute_z_scores("Growth SaaS", {"unrelated_kpi": 50.0}, cohort=cohort)
        assert "unrelated_kpi" not in result


# ── z_tier_kicker ─────────────────────────────────────────────────────────────

class TestZTierKicker:
    def test_top_decile(self):
        mult, label = z_tier_kicker(2.0, direction="higher_better")
        assert mult == 1.30
        assert "top-decile" in label

    def test_top_quartile(self):
        mult, label = z_tier_kicker(1.2, direction="higher_better")
        assert mult == 1.15
        assert "top-quartile" in label

    def test_above_median(self):
        mult, _ = z_tier_kicker(0.7, direction="higher_better")
        assert mult == 1.05

    def test_near_median(self):
        mult, _ = z_tier_kicker(0.0, direction="higher_better")
        assert mult == 1.00

    def test_bottom_decile(self):
        mult, label = z_tier_kicker(-2.0, direction="higher_better")
        assert mult == 0.70
        assert "bottom-decile" in label

    def test_lower_better_inverts_sign(self):
        # combined_ratio z=-2.0 means MUCH BETTER than peers (lower CR is good)
        mult, label = z_tier_kicker(-2.0, direction="lower_better")
        assert mult == 1.30
        assert "top-decile" in label

    def test_lower_better_high_z_is_bad(self):
        # combined_ratio z=+2.0 means WORSE than peers (higher CR is bad)
        mult, _ = z_tier_kicker(2.0, direction="lower_better")
        assert mult == 0.70


# ── augment_metrics_with_z_scores ─────────────────────────────────────────────

class TestAugmentMetrics:
    def test_no_profile_is_noop(self):
        m = {"nrr_pct": 1.20}
        out = augment_metrics_with_z_scores("", "DDOG", m)
        assert "_z_scores" not in out

    def test_no_ticker_is_noop(self):
        m = {"nrr_pct": 1.20}
        out = augment_metrics_with_z_scores("Growth SaaS", "", m)
        assert "_z_scores" not in out

    def test_handles_none_metrics(self):
        out = augment_metrics_with_z_scores("Growth SaaS", "DDOG", None)
        assert isinstance(out, dict)

    def test_silent_on_db_failure(self, monkeypatch):
        # Simulate DB connection failure → should not raise
        from src.data import zscore_engine
        monkeypatch.setattr(zscore_engine, "_connect_ro", lambda: None)
        m = {"nrr_pct": 1.20}
        out = augment_metrics_with_z_scores("Growth SaaS", "DDOG", m)
        assert "_z_scores" not in out  # no cohort → no z-scores
        assert out["nrr_pct"] == 1.20  # original metrics preserved


# ── Integration with composite_adjustment ─────────────────────────────────────

class TestCompositeIntegration:
    def test_z_score_drives_quality_when_present(self):
        from src.data.sector_kpi_framework import composite_adjustment
        # Insurance schema: combined_ratio is the quality KPI (lower_better)
        metrics = {
            "combined_ratio":     0.86,  # very good
            "solvency_ratio_scr": 2.20,
            "_z_scores": {
                "combined_ratio": {"z": -2.0, "cohort_size": 8, "median": 0.95, "mad": 0.04},
            },
        }
        _mult, bridge = composite_adjustment("Insurance", "Financials", metrics)
        # Z-driven path should have written quality_z to bridge
        assert bridge.get("quality_z") == -2.0
        assert bridge.get("quality_cohort") == 8
        # Note text should include the z signature
        assert "z=" in bridge["quality_note"]

    def test_band_fallback_when_no_z(self):
        from src.data.sector_kpi_framework import composite_adjustment
        metrics = {
            "combined_ratio":     0.86,
            "solvency_ratio_scr": 2.20,
        }
        _mult, bridge = composite_adjustment("Insurance", "Financials", metrics)
        # No z-scores → quality_z should be None
        assert bridge.get("quality_z") is None
        # Band-based note should NOT contain z= signature
        assert "z=" not in bridge["quality_note"]

    def test_z_score_drives_risk_when_present(self):
        from src.data.sector_kpi_framework import composite_adjustment
        metrics = {
            "combined_ratio":     0.92,
            "solvency_ratio_scr": 2.50,
            "_z_scores": {
                "solvency_ratio_scr": {"z": 1.8, "cohort_size": 6, "median": 1.80, "mad": 0.20},
            },
        }
        _mult, bridge = composite_adjustment("Insurance", "Financials", metrics)
        assert bridge.get("risk_z") == 1.8
        assert bridge.get("risk_cohort") == 6
        assert "z=" in bridge["risk_note"]
