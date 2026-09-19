"""Two-tier valuation (owner decision 2026-09-19).

Tier 1: the fundamental IV is DCF + peer-median multiples, no composite.
Tier 2: the 12m target converges from spot toward the IV, with the composite
applied only as a premium to the multiples leg, bounded by the peers' own
interquartile range. Legs that carry no weight are published as cross-checks.
"""
import inspect

import pytest

from src.agents.analysis import dcf_agent as d
from src.data import regional_comps as rc


def _peer(med=10.0, q1=8.0, q3=13.0, field="ev_ebitda"):
    return {field: med, "_comp_basis": {field: {"basis": "industry", "p25": q1, "p75": q3,
                                                "peer_count": 12}}}


# ── premium bound ─────────────────────────────────────────────────────────

def test_premium_is_bounded_by_the_peer_interquartile_range():
    hi = d._peer_bounded_premium(1.85, _peer(), "EV/EBITDA")
    assert hi["applied"] == pytest.approx(1.3) and hi["bound"] == "p75"
    lo = d._peer_bounded_premium(0.50, _peer(), "EV/EBITDA")
    assert lo["applied"] == pytest.approx(0.8) and lo["bound"] == "p25"
    mid = d._peer_bounded_premium(1.10, _peer(), "EV/EBITDA")
    assert mid["applied"] == pytest.approx(1.10) and mid["bound"] is None


def test_no_quartiles_means_no_premium():
    out = d._peer_bounded_premium(1.85, {"ev_ebitda": 10.0, "_comp_basis": {"ev_ebitda": {}}},
                                  "EV/EBITDA")
    assert out["applied"] == 1.0 and out["field"] is None
    assert "no peer quartiles" in out["basis"]


def test_premium_uses_the_anchor_multiple_then_falls_back():
    peer = {**_peer(20.0, 15.0, 30.0, "pe"), "ev_ebitda": 10.0}
    peer["_comp_basis"]["ev_ebitda"] = {"p25": 9.0, "p75": 11.0}
    assert d._peer_bounded_premium(1.85, peer, "P/E (norm)")["field"] == "pe"
    assert d._peer_bounded_premium(1.85, peer, "EV/EBITDA")["field"] == "ev_ebitda"
    assert d._peer_bounded_premium(1.85, peer, "DCF")["field"] == "ev_ebitda"


def test_premium_touches_only_the_multiples_leg():
    sc = {"intrinsic_value": 70.0, "weight_dcf": 0.3, "weight_multi": 0.7,
          "iv_dcf": 50.0, "iv_multi": 78.5714}
    # 0.3*50 + 0.7*78.5714 = 70; premium 1.2 on the multi leg only
    assert d._premium_adjusted_iv(sc, 1.2) == pytest.approx(0.3 * 50 + 0.7 * 78.5714 * 1.2, rel=1e-4)
    assert d._premium_adjusted_iv(sc, 1.0) == pytest.approx(70.0)
    assert d._premium_adjusted_iv({"intrinsic_value": 9.0}, 1.3) == 9.0   # no multiples leg


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


def test_fundamental_iv_blend_carries_no_composite():
    src = _run_src()
    assert "composite_mult=_composite_mult" not in src
    assert src.count("composite_mult=1.0") >= 2


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


# ── comps quartiles ───────────────────────────────────────────────────────

def test_compute_medians_emits_quartile_siblings():
    members = [{"symbol": f"S{i}", "market_cap": 1e9 * (20 - i)} for i in range(8)]
    metrics = {f"S{i}": {f: v for f in rc.FIELDS for v in [None]} for i in range(8)}
    vals = [6.0, 7.0, 8.0, 9.0, 10.0, 11.0, 12.0, 14.0]
    for i, v in enumerate(vals):
        metrics[f"S{i}"]["ev_ebitda"] = v
    rows = rc.compute_medians({"Apparel": members}, metrics, "industry", 4)
    got = {(r["cohort"], r["field"]): r["value"] for r in rows}
    assert got[("all", "ev_ebitda")] == pytest.approx(9.5)
    assert got[("all", f"ev_ebitda{rc.QUARTILE_SEP}p25")] < 9.5 < got[("all", f"ev_ebitda{rc.QUARTILE_SEP}p75")]


def test_quartiles_attach_to_the_median_from_the_same_rung(monkeypatch):
    stores = {
        ("industry", "large"): {"ev_ebitda": {"value": 10.0, "peer_count": 6, "min_market_cap": 0.0},
                                "ev_ebitda__p25": {"value": 8.0, "peer_count": 6},
                                "ev_ebitda__p75": {"value": 12.0, "peer_count": 6}},
        ("industry", "all"): {"ev_ebitda__p25": {"value": 1.0, "peer_count": 9},
                              "ev_ebitda__p75": {"value": 99.0, "peer_count": 9}},
    }
    monkeypatch.setattr(rc, "load_comps",
                        lambda ex, level, key, cohort, age: stores.get((level, cohort), {}))
    out = rc.get_regional_multiples("US", "Apparel", "Consumer", market_cap=5e9)
    assert set(out) == {"ev_ebitda"}
    assert (out["ev_ebitda"]["p25"], out["ev_ebitda"]["p75"]) == (8.0, 12.0)
