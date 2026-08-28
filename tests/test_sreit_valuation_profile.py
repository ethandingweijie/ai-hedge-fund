"""Singapore REIT valuation profile — regression tests.

S-REITs are priced on a dividend discount model off DPU, not on the US
P/FFO / P/AFFO / NAV-cap-rate stack. An S-REIT must distribute at least
90% of taxable income to keep its tax transparency, so DPU is very nearly
the whole of the equity cash flow, and FFO/AFFO are US GAAP constructs
S-REITs do not report.

Ground truth is the valuation line printed in each broker note:
  * Frasers Centrepoint Trust  DDM, CoE 6.38%, terminal g 1.50%  -> S$2.70
  * Keppel DC REIT             DDM, CoE 6.83%, terminal g 1.75%  -> S$2.46
  * OUE REIT                   DDM, CoE 7.00%, terminal g 1.20%  -> S$0.45
  * CapitaLand India Trust     terminal g 2.75% (OCBC)           -> S$1.32
"""

from __future__ import annotations

import pytest

from src.agents.analysis.dcf_agent import (
    _SREIT_DDM_CALIBRATION,
    _compute_sreit_ddm,
    _sgx_reit_subtype,
    _sreit_ddm_assumptions,
    _sreit_dpu,
)
from src.data.sector_profiles import (
    INDUSTRY_VALUATION_PROFILES,
    get_wacc_profile_for_ticker,
)



@pytest.fixture(autouse=True)
def _no_deposited_report(monkeypatch):
    """Pin these tests to the seed table, not to machine-local DB state.

    `_bank_ggm_assumptions` / `_sreit_ddm_assumptions` consult
    `analyst_basis.get_analyst_basis`, which reads `analyst_reports`. Once
    a drive sync has run locally those rows exist and legitimately
    supersede the static seed table — so these tests passed or failed
    depending on whether someone had synced PDFs on that machine. The
    benchmark-precedence behaviour is covered explicitly, with its own
    stubs, in tests/test_analyst_basis.py.
    """
    import src.memory.analyst_basis as ab
    monkeypatch.setattr(ab, "get_analyst_basis", lambda _t: None)



# ── Routing ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "ticker, name, subtype",
    [
        ("J69U.SI", "Frasers Centrepoint", "retail"),
        ("AJBU.SI", "Keppel DC REIT",      "data_centre"),
        ("CY6U.SI", "CapitaLand India",    "india"),
        ("AU8U.SI", "CapitaLand China",    "china"),
        ("A17U.SI", "CapitaLand Ascendas", "industrial"),
        ("M44U.SI", "Mapletree Logistics", "logistics"),
        ("SK6U.SI", "Parkway Life",        "healthcare"),
        ("CMOU.SI", "CDL Hospitality",     "hospitality"),
        ("OXMU.SI", "Prime US REIT",       "us_office"),
        ("K71U.SI", "Keppel REIT",         "office"),
    ],
)
def test_sgx_reits_route_to_sreit_profile(ticker, name, subtype):
    """All 25 SGX REITs carried a sub-SECTOR hint, not a profile name, so
    every one of them fell through to the runtime LLM classifier."""
    sector, profile = get_wacc_profile_for_ticker(ticker)
    assert (sector, profile) == ("REIT", "S-REIT"), name
    assert _sgx_reit_subtype(ticker) == subtype, name


def test_sreit_profile_is_ddm_anchored():
    spec = INDUSTRY_VALUATION_PROFILES["RealEstate"]["S-REIT"]
    methods = {m["name"]: m for m in spec["methods"]}
    assert methods["DDM (S-REIT)"]["anchor"] is True
    assert methods["DDM (S-REIT)"]["weight"] >= 0.5
    # S-REITs do not report FFO.
    assert "P/FFO" in spec["excluded"]


def test_us_reit_profile_is_unchanged():
    """The US REIT row must keep its NAV-anchored, P/FFO-weighted shape."""
    spec = INDUSTRY_VALUATION_PROFILES["RealEstate"]["REIT"]
    methods = {m["name"]: m["weight"] for m in spec["methods"]}
    assert methods["NAV (Cap Rates)"] == 0.50
    assert methods["P/FFO"] == 0.30
    assert "P/FFO" not in spec["excluded"]


# ── DDM against the published targets ────────────────────────────────────

@pytest.mark.parametrize(
    "ticker, subtype, dpu_cents, dpu_growth, published_tp, name",
    [
        ("J69U.SI", "retail",      12.44, 0.035, 2.70, "Frasers Centrepoint"),
        ("AJBU.SI", "data_centre", 11.54, 0.049, 2.46, "Keppel DC REIT"),
        ("TS0U.SI", "hospitality",  2.30, 0.043, 0.45, "OUE REIT"),
    ],
)
def test_ddm_reproduces_published_target(
    ticker, subtype, dpu_cents, dpu_growth, published_tp, name
):
    """Forward DPU capitalised at (CoE - g) must land on the broker target.

    The broker models are multi-stage, so this reduced form is an
    approximation — 10% is the tolerance, and all three come in well
    inside it.
    """
    row = {"_sreit_dpu_cents_research": dpu_cents}
    value, _dpu_fwd, a = _compute_sreit_ddm(ticker, subtype, row, 1e9,
                                            dpu_growth=dpu_growth)
    assert value == pytest.approx(published_tp, rel=0.10), name
    assert any("broker" in p for p in a["provenance"]), name


def test_broker_table_outranks_subsector_default():
    """A published DDM line beats the interpolated sub-sector row."""
    # TS0U is mapped to "commercial" by the SGX hint, but its own broker
    # line states CoE 7.0% / g 1.2%.
    a = _sreit_ddm_assumptions("TS0U.SI", "commercial", {})
    assert (a["coe"], a["g"]) == (0.0700, 0.0120)
    assert "CoE 7.00% (broker)" in a["provenance"]


def test_subsector_default_used_when_no_broker_row():
    a = _sreit_ddm_assumptions("A17U.SI", "industrial", {})
    cfg = _SREIT_DDM_CALIBRATION["industrial"]
    assert (a["coe"], a["g"]) == (cfg["coe"], cfg["g"])
    assert any("sub-sector" in p for p in a["provenance"])


def test_research_coe_rejected_when_far_from_subsector():
    """A cost of equity far off the sub-sector row is a bad extraction."""
    row = {"_sreit_coe_research": 0.13}
    a = _sreit_ddm_assumptions("A17U.SI", "industrial", row)
    assert a["coe"] == _SREIT_DDM_CALIBRATION["industrial"]["coe"]
    assert any("rejected" in p for p in a["provenance"])


def test_research_coe_accepted_when_plausible():
    row = {"_sreit_coe_research": 0.070}
    a = _sreit_ddm_assumptions("A17U.SI", "industrial", row)
    assert a["coe"] == 0.070
    assert "CoE 7.00% (research)" in a["provenance"]


def test_ddm_declines_when_coe_approaches_growth():
    """The capitalisation factor diverges as CoE approaches g.

    Both inputs are inside their own accepted bands — CoE 4.4% is within
    200bps of the 6.3% healthcare row, and g 4.0% is at the ceiling — so
    it is only their proximity to each other that makes the model
    unstable.
    """
    row = {"_sreit_dpu_cents_research": 10.0, "_sreit_coe_research": 0.044,
           "_sreit_terminal_g_research": 0.040}
    a = _sreit_ddm_assumptions("SK6U.SI", "healthcare", row)
    assert (a["coe"], a["g"]) == (0.044, 0.040)      # both accepted
    assert _compute_sreit_ddm("SK6U.SI", "healthcare", row, 1e9) is None


def test_implausible_terminal_growth_is_rejected():
    """A 5% terminal growth rate for an S-REIT is a bad extraction.

    No Singapore REIT in the reviewed set carries a terminal g above
    2.75%, and a perpetual growth rate near the cost of equity makes the
    model explode rather than merely optimistic.
    """
    row = {"_sreit_terminal_g_research": 0.050}
    a = _sreit_ddm_assumptions("A17U.SI", "industrial", row)
    assert a["g"] == _SREIT_DDM_CALIBRATION["industrial"]["g"]
    assert any("sub-sector" in p for p in a["provenance"])


def test_ddm_requires_a_distribution():
    assert _compute_sreit_ddm("A17U.SI", "industrial", {}, 1e9) is None


def test_dpu_growth_is_clamped():
    """A wild scenario growth rate must not leak into the terminal value."""
    row = {"_sreit_dpu_cents_research": 10.0}
    hot, _d, _a = _compute_sreit_ddm("A17U.SI", "industrial", row, 1e9,
                                     dpu_growth=3.0)
    capped, _d2, _a2 = _compute_sreit_ddm("A17U.SI", "industrial", row, 1e9,
                                          dpu_growth=0.15)
    assert hot == capped


# ── DPU resolution ───────────────────────────────────────────────────────

def test_dpu_prefers_research_cents():
    row = {"_sreit_dpu_cents_research": 12.44, "dividends_per_share": 0.09}
    assert _sreit_dpu(row, 1e9) == pytest.approx(0.1244)


def test_dpu_falls_back_through_statement_lines():
    assert _sreit_dpu({"dividends_per_share": 0.1211}, 1e9) == pytest.approx(0.1211)
    assert _sreit_dpu({"distributable_income": 254e6}, 2e9) == pytest.approx(0.127)
    assert _sreit_dpu({"dividends_and_distributions": -254e6}, 2e9) == pytest.approx(0.127)


# ── Calibration sanity ───────────────────────────────────────────────────

def test_calibration_risk_ordering_holds():
    """CoE must rise with cash-flow risk, as the published points imply."""
    c = _SREIT_DDM_CALIBRATION
    assert c["healthcare"]["coe"] < c["retail"]["coe"] < c["data_centre"]["coe"]
    assert c["data_centre"]["coe"] < c["hospitality"]["coe"] < c["china"]["coe"]
    # Every row must produce a finite multiple.
    for name, cfg in c.items():
        assert cfg["coe"] - cfg["g"] > 0.005, name


def test_published_rows_are_marked_as_such():
    """Interpolated calibration must never masquerade as sourced."""
    c = _SREIT_DDM_CALIBRATION
    for key in ("retail", "data_centre", "hospitality"):
        assert c[key]["src"] == "published", key
    assert c["industrial"]["src"] == "interpolated"
