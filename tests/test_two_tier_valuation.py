"""Two-tier valuation (owner decisions 2026-09-19).

Tier 1: the fundamental IV is DCF + peer-median multiples.
Tier 2: the 12m target converges from spot toward the IV, one rule for every
name. The quality x risk x commodity composite is retired from both tiers,
code and all. Legs that carry no weight are published as cross-checks.
"""
import inspect

import pytest

from src.agents.analysis import dcf_agent as d


# ── cross-checks ──────────────────────────────────────────────────────────

def test_unweighted_legs_are_cross_checks():
    table = {"EV/EBITDA": 142.47, "DCF (FCF+)": 109.49, "P/E (norm)": 136.04,
             "EV/Revenue": 63.79, "Forward P/E": 88.67, "SOTP (segments)": 137.19}
    ew = [{"method": "EV/EBITDA", "value_key": "EV/EBITDA"},
          {"method": "DCF (FCF+)", "value_key": "DCF (FCF+)"},
          {"method": "P/E (norm)", "value_key": "P/E (norm)"},
          {"method": "Brand Val", "value_key": "EV/Revenue"}]
    assert d._cross_check_methods(table, ew) == ["Forward P/E", "SOTP (segments)"]


# ── engine wiring ─────────────────────────────────────────────────────────

def _run_src():
    return inspect.getsource(d.run_dcf_agent)


def test_the_composite_is_gone_from_the_engine_and_the_framework():
    import src.data.sector_kpi_framework as f
    src = inspect.getsource(d)
    for name in ("composite_mult", "_composite_bridge", "_peer_bounded_premium",
                 "_premium_adjusted_iv", "iv_multi_post", "intrinsic_value_pre_composite",
                 "composite_applied", "_is_bank_for_composite"):
        assert name not in src, name
    assert "composite_mult" not in inspect.signature(d._blend_methods).parameters
    for name in ("composite_adjustment", "_quality_multiplier", "_risk_multiplier",
                 "_commodity_multiplier", "render_tier_driver_block"):
        assert not hasattr(f, name), name
    for spec in f.SECTOR_KPI_FRAMEWORK.values():
        assert not ({"quality_tiers", "risk_adjustment", "commodity_uplift",
                     "derived_kpis"} & set(spec)), spec.get("sector")


def test_the_z_score_engine_is_gone():
    import importlib.util
    assert importlib.util.find_spec("src.data.zscore_engine") is None


def test_the_target_is_pure_convergence_toward_the_iv():
    src = _run_src()
    assert "_convergence_bound(_siv, float(_spot_for_cap), _max_capture)" in src
    assert '"rule": "target = spot + capture x (IV - spot)"' in src


def test_unified_target_supersedes_the_independent_target_guards():
    src = _run_src()
    i_rule = src.index("12m target: one rule for every name")
    assert i_rule < src.index("12m PT vs DCF IV divergence guard")
    assert "if not _pt_unified and _base_iv" in src
    assert "if _band_breaches and not _pt_unified:" in src
    assert '"pt_bridge":          _pt_bridge' in src


@pytest.mark.parametrize("iv,spot", [(86.42, 104.85), (21.20, 31.20), (5269.0, 1839.0), (39.7, 93.4)])
def test_converged_target_lies_between_spot_and_iv(iv, spot):
    for cap in (0.20, 0.35, 0.50):
        t = d._convergence_bound(iv, spot, cap)
        assert min(iv, spot) <= t <= max(iv, spot)


# ── comps: only known multiples are read ──────────────────────────────────

def test_rows_under_unknown_field_names_are_ignored(monkeypatch):
    """Quartile rows written for the retired premium (`ev_ebitda__p25`) are
    still in production until they age out; they must never read as a field."""
    from src.data import regional_comps as rc
    stores = {("industry", "all"): {
        "ev_ebitda": {"value": 10.0, "peer_count": 6, "min_market_cap": 0.0},
        "ev_ebitda__p25": {"value": 8.0, "peer_count": 6, "min_market_cap": 0.0}}}
    monkeypatch.setattr(rc, "load_comps",
                        lambda ex, level, key, cohort, age: stores.get((level, cohort), {}))
    out = rc.get_regional_multiples("US", "Apparel", "Consumer")
    assert set(out) == {"ev_ebitda"}


# ── scenario-aware analyst SOTP (option A+) ───────────────────────────────

_JD_TABLE = {
    "rows": [{"name": "JD Retail", "value": 52248868893.94},
             {"name": "JD Logistics", "value": 5849489702.59},
             {"name": "New Businesses", "value": 5780459159.17}],
    "segment_value": 63878817755.70, "associates": 2.0e9, "net_cash": 17403261970.32,
    "nav": 83282079726.02, "holdco_discount_pct": 0.15, "holdco_discount": 12492311958.90,
    "final": 70789767767.12, "per_share_reporting": 186.4685,
}
_JD_TREES = {
    "JD Retail": {"scenarios": [{"prob": .25, "rate": -.04}, {"prob": .55, "rate": .05}, {"prob": .20, "rate": .09}]},
    "JD Logistics": {"scenarios": [{"prob": .20, "rate": .12}, {"prob": .55, "rate": .22}, {"prob": .25, "rate": .30}]},
    "New Businesses": {"scenarios": [{"prob": .30, "rate": -.15}, {"prob": .50, "rate": .05}, {"prob": .20, "rate": .18}]},
}


def test_jd_bear_sotp_flexes_segments_by_tree_and_band():
    bear = d._sotp_scenario_from_trees(_JD_TABLE, _JD_TREES, "bear", 0.75)
    bull = d._sotp_scenario_from_trees(_JD_TABLE, _JD_TREES, "bull", 1.25)
    assert bear["trees_matched"] == 3
    # Production run 2026-09-19: HK$186.47 in every scenario before.
    assert bear["per_share_reporting"] == pytest.approx(141.92, abs=0.05)
    assert bull["per_share_reporting"] == pytest.approx(233.61, abs=0.05)


def test_net_cash_and_associates_do_not_flex():
    flat = {"JD Retail": {"scenarios": [{"prob": 1.0, "rate": 0.05}]}}
    zero = dict(_JD_TABLE, rows=[{"name": "JD Retail", "value": 0.0}], segment_value=0.0,
                nav=_JD_TABLE["associates"] + _JD_TABLE["net_cash"])
    zero["holdco_discount"] = zero["nav"] * 0.15
    zero["final"] = zero["nav"] * 0.85
    a = d._sotp_scenario_from_trees(zero, flat, "bear", 0.75)["per_share_reporting"]
    assert a == pytest.approx(zero["per_share_reporting"])


def test_alibaba_segment_names_match_their_trees_and_generic_names_do_not():
    rows = ["Alibaba China E-commerce Group", "All others", "Cloud intelligence group",
            "Alibaba International Digital Commerce Group"]
    m = d._match_segment_trees(rows, {"Cloud / AI": {"n": 1}, "E-commerce": {"n": 2},
                                      "Quick Commerce": {"n": 3}})
    assert m == {"Alibaba China E-commerce Group": {"n": 2}, "Cloud intelligence group": {"n": 1}}


def test_tree_factor_is_relative_to_the_probability_weighted_rate():
    tree = _JD_TREES["JD Retail"]
    ref = .25 * -.04 + .55 * .05 + .20 * .09
    assert d._tree_factor(tree, "bear") == pytest.approx(0.96 / (1 + ref))
    assert d._tree_factor(tree, "bull") == pytest.approx(1.09 / (1 + ref))
    assert d._tree_factor(tree, "base") == 1.0 and d._tree_factor(None, "bear") == 1.0
