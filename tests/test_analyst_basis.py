"""Analyst valuation basis — parsing and precedence.

Every broker note states its method and parameters in one line. Those
lines were already stored verbatim in `analyst_reports.pt_methodology_json`
but never parsed, so the numbers sat in the database unusable while the
engine guessed its own.

Strings below are the real valuation lines from the deposited reports,
spanning US, SGX and HKEX names.
"""

from __future__ import annotations

import pytest

from src.memory.analyst_basis import (
    METHOD_ANCHORS,
    method_disagrees,
    parse_pt_methodology,
)


# ── Parsing the real valuation lines ─────────────────────────────────────

@pytest.mark.parametrize(
    "name, line, expected",
    [
        ("Apple (US)", "Discounted Cash-Flow, WACC 6.3%, g 3.5%",
         {"method": "dcf", "wacc": 0.063, "terminal_growth": 0.035}),
        ("DBS (SGX)", "Gordon Growth Model (COE: 8.6%, g: 3.3%)",
         {"method": "ggm_pb", "cost_of_equity": 0.086, "terminal_growth": 0.033}),
        ("OCBC (SGX)", "Gordon Growth Model (COE: 9.1%, g: 3%)",
         {"method": "ggm_pb", "cost_of_equity": 0.091, "terminal_growth": 0.030}),
        ("JPM (US)", "Gordon Growth Model (COE: 10%, g: 3%)",
         {"method": "ggm_pb", "cost_of_equity": 0.100, "terminal_growth": 0.030}),
        ("Keppel DC (SGX)", "DDM (Cost of Equity: 6.83%; Terminal g: 1.75%)",
         {"method": "ddm", "cost_of_equity": 0.0683, "terminal_growth": 0.0175}),
        ("FCT (SGX)", "DDM (Cost of equity 6.38%, Terminal Growth 1.5%)",
         {"method": "ddm", "cost_of_equity": 0.0638, "terminal_growth": 0.015}),
        ("OUE REIT (SGX)", "DDM (Cost of Equity:7%; Terminal g: 1.2%)",
         {"method": "ddm", "cost_of_equity": 0.070, "terminal_growth": 0.012}),
        ("Keppel (SGX)", "SOTP valuation",
         {"method": "sotp"}),
        ("Sembcorp (SGX)", "EV/Adj. EBITDA@9x FY25e + DPN book value",
         {"method": "ev_ebitda", "target_multiple": 9.0,
          "multiple_basis": "ev_ebitda"}),
        ("Tencent (HKEX)",
         "SOTP valuation methodology, applying a 10% holding discount to the "
         "latest market values",
         {"method": "sotp", "holdco_discount": 0.10}),
    ],
)
def test_parses_published_valuation_lines(name, line, expected):
    got = parse_pt_methodology(line)
    for key, want in expected.items():
        assert got.get(key) == pytest.approx(want) if isinstance(want, float) \
            else got.get(key) == want, f"{name}: {key}"


def test_absent_parameters_are_omitted_not_zeroed():
    """A missing parameter must fall through to the profile default.

    Defaulting it to zero would silently pin a terminal growth rate of 0%
    and look like a deliberate assumption.
    """
    got = parse_pt_methodology("SOTP valuation")
    assert "cost_of_equity" not in got
    assert "terminal_growth" not in got
    assert "wacc" not in got


def test_empty_and_junk_input_is_safe():
    assert parse_pt_methodology("") == {"raw": ""}
    assert parse_pt_methodology(None) == {"raw": ""}
    assert "method" not in parse_pt_methodology("see analyst for details")


def test_implausible_rates_are_rejected():
    """A percentage outside 0-40% is a mis-parse, not a discount rate."""
    got = parse_pt_methodology("DCF, WACC 630%, g 3.5%")
    assert "wacc" not in got
    assert got.get("terminal_growth") == pytest.approx(0.035)


def test_gordon_growth_beats_bare_growth_wording():
    """Ordering matters: 'Gordon Growth' must not resolve to a DCF."""
    assert parse_pt_methodology(
        "Gordon Growth Model (COE: 8.6%, g: 3.3%)")["method"] == "ggm_pb"


# ── Divergence detection ─────────────────────────────────────────────────

def test_method_divergence_detected_but_advisory():
    """A DDM note against a GGM-anchored profile is a disagreement."""
    basis = {"method": "ddm"}
    assert method_disagrees(basis, "GGM (P/B)") is True
    assert method_disagrees(basis, "DDM (S-REIT)") is False


def test_no_divergence_when_basis_absent():
    assert method_disagrees(None, "GGM (P/B)") is False
    assert method_disagrees({}, "GGM (P/B)") is False
    assert method_disagrees({"method": None}, "GGM (P/B)") is False


def test_every_canonical_method_has_an_anchor_mapping():
    """A method with no anchor mapping would never raise a divergence."""
    for _name, pattern_key in (("dcf", "dcf"), ("ggm_pb", "ggm_pb"),
                               ("ddm", "ddm"), ("sotp", "sotp"),
                               ("ev_ebitda", "ev_ebitda"), ("pe", "pe"),
                               ("nav", "nav")):
        assert METHOD_ANCHORS.get(pattern_key), pattern_key


# ── Precedence into the valuation engines ────────────────────────────────

def test_basis_outranks_seed_table_for_discount_rates(monkeypatch):
    """A deposited report beats the hand-copied seed row."""
    import src.memory.analyst_basis as ab
    from src.agents.analysis import dcf_agent

    monkeypatch.setattr(ab, "get_analyst_basis", lambda t: {
        "method": "ggm_pb", "cost_of_equity": 0.084,
        "terminal_growth": 0.028, "house": "Phillip", "as_of": "2026-08"})

    a = dcf_agent._bank_ggm_assumptions(
        "U11.SI", "Money Center Bank (SG)",
        {"net_income": 6e9, "total_equity": 45e9})
    assert a["coe"] == 0.084
    assert a["g"] == 0.028
    assert any("analyst basis" in p for p in a["provenance"])


def test_basis_outranks_subsector_for_sreit(monkeypatch):
    import src.memory.analyst_basis as ab
    from src.agents.analysis import dcf_agent

    monkeypatch.setattr(ab, "get_analyst_basis", lambda t: {
        "method": "ddm", "cost_of_equity": 0.0705, "terminal_growth": 0.016,
        "house": "DBS Research", "as_of": "2026-08"})

    a = dcf_agent._sreit_ddm_assumptions("A17U.SI", "industrial", {})
    assert (a["coe"], a["g"]) == (0.0705, 0.016)
    assert any("analyst basis" in p for p in a["provenance"])


def test_engine_falls_back_cleanly_when_no_report(monkeypatch):
    """No deposited report must leave existing behaviour untouched."""
    import src.memory.analyst_basis as ab
    from src.agents.analysis import dcf_agent

    monkeypatch.setattr(ab, "get_analyst_basis", lambda t: None)
    a = dcf_agent._bank_ggm_assumptions(
        "D05.SI", "Money Center Bank (SG)",
        {"net_income": 10933e6, "total_equity": 68916e6})
    assert a["coe"] == 0.086          # the static seed row still wins
    assert any("broker" in p for p in a["provenance"])


def test_store_failure_never_blocks_valuation(monkeypatch):
    """A broken store must degrade to the defaults, not raise."""
    import src.memory.analyst_basis as ab
    from src.agents.analysis import dcf_agent

    def _boom(_t):
        raise RuntimeError("store down")

    monkeypatch.setattr(ab, "get_analyst_basis", _boom)
    a = dcf_agent._sreit_ddm_assumptions("A17U.SI", "industrial", {})
    assert a["coe"] > 0


def test_terminal_growth_stated_before_the_words():
    """OCBC on CapitaLand India Trust prints the rate first.

    Matching only "terminal growth of X%" dropped a stated assumption and
    silently fell back to the profile default.
    """
    got = parse_pt_methodology("DCF with 2.75% terminal growth rate")
    assert got["method"] == "dcf"
    assert got["terminal_growth"] == pytest.approx(0.0275)


def test_both_terminal_growth_orders_agree():
    a = parse_pt_methodology("DDM, terminal growth of 1.5%")
    b = parse_pt_methodology("DDM with 1.5% terminal growth")
    assert a["terminal_growth"] == b["terminal_growth"] == pytest.approx(0.015)


def test_pe_without_a_slash_resolves_the_method():
    """Asian notes write "28x PE", not "P/E".

    The basis detector already treated the slash as optional, so Sheng
    Siong's "28x PE valuations rolled over to FY27e" produced a
    multiple_basis of "pe" with no method at all — the two patterns
    disagreed about the same notation.
    """
    got = parse_pt_methodology("28x PE valuations rolled over to FY27e")
    assert got["method"] == "pe"
    assert got["target_multiple"] == pytest.approx(28.0)
    assert got["multiple_basis"] == "pe"


def test_method_and_basis_never_disagree_about_pe():
    for line in ("28x PE", "28x P/E", "PER of 28x", "28x price-earnings"):
        got = parse_pt_methodology(line)
        assert got.get("method") == "pe", line
