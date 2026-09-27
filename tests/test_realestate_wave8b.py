"""Wave 8b, the real-estate remediation in the owner's order B -> C -> A -> D (decisions of 2026-09-27).

Each step is pinned as it lands. Step 1: clean cash NOI (revenue less cost of revenue) on every cap-rate
NAV, statutory EBITDA only as the fallback, the basis on the trace.
"""
import inspect

import pytest

from src.agents.analysis import dcf_agent as d


# ── step 1 (B): clean cash NOI ────────────────────────────────────────────────

def test_clean_noi_is_revenue_less_cost_of_revenue_with_ebitda_as_the_fallback():
    # Swire-shaped: a revaluation loss drives EBITDA and net income negative; the property NOI is positive
    m = d._compute_reit_metrics({"revenue": 16.0e9, "cost_of_revenue": 6.0e9, "ebitda": 1.6e9, "net_income": -1.5e9,
                                 "depreciation_and_amortization": 0.6e9}, subtype="default")
    assert m["noi"] == pytest.approx(10.0e9) and m["noi_basis"].startswith("clean NOI")
    # a negative cost of revenue sign convention is taken at absolute value
    assert d._compute_reit_metrics({"revenue": 100.0, "cost_of_revenue": -30.0, "ebitda": 5.0}, subtype="retail")["noi"] == pytest.approx(70.0)
    # no cost of revenue: EBITDA, and the basis says so
    m2 = d._compute_reit_metrics({"revenue": 100.0, "cost_of_revenue": None, "ebitda": 60.0}, subtype="retail")
    assert m2["noi"] == 60.0 and "fallback: cost of revenue missing" in m2["noi_basis"]
    # cost of revenue above revenue and no positive EBITDA: nothing, stated
    m3 = d._compute_reit_metrics({"revenue": 100.0, "cost_of_revenue": 120.0, "ebitda": None}, subtype="retail")
    assert m3["noi"] is None and m3["noi_basis"].startswith("none")
    # FFO is untouched by the step (decision B covered NOI): net income plus D&A
    assert m["ffo"] == pytest.approx(-0.9e9)


def test_the_nav_leg_trace_names_the_noi_basis_and_the_bridge():
    src = inspect.getsource(d._compute_method_value)
    for lit in ('noi_basis=_reit_pre.get("noi_basis")', "gross_asset_value=", 'total_debt=most_recent.get("total_debt")'):
        assert lit in src, lit
    assert "[{_reit_m.get('noi_basis')}]" in inspect.getsource(d.run_dcf_agent)
