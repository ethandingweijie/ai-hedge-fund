"""PM write-up, owner 2026-10-03 (institutional critique): no engine residue in the prose, one
price anchor, clean rounding, a stressed bear case with an explicit skew, the bridge to
normalised earnings, and management's forward guidance (earnings release) cited against
consensus and the house estimate.
"""
import inspect

from src.agents import portfolio_manager as pm
from src.agents.pm.industry_pm import number_guard

P = pm._PM_RATIONALE_SYSTEM_PROMPT


def test_the_prompt_carries_the_six_institutional_rules_and_keeps_the_old_pins():
    for rule in ("Voice rule (no engine residue)", "One-anchor rule", "Rounding rule", "Arithmetic rule",
                 "Bear-case rule", "Bridge rule", "Guidance rule"):
        assert rule in P, rule
    assert "TRAP RISK" in P and "sovereign ownership floor" in P                 # the translation example
    assert "whole currency units" in P and "never basis points" in P
    assert "upside to downside" in P and "No bridge, no" in P
    assert "earnings release or call" in P
    # pins the older tests rely on survive
    assert "TWO specific figures with units" in P and "Analyst-thesis rule" in P and "Output JSON only." in P


_GE = {"as_of": "2026-09-25", "fiscal_year_1": "FY2026", "fiscal_year_2": "FY2027", "confidence": "HIGH", "applied": True,
       "guidance": {"revenue_growth": {"low": 0.054, "mid": 0.063, "high": 0.072}, "revenue": {"low": 290, "mid": 292.5, "high": 295, "currency": "USD", "scale": "bn"},
                    "ebitda_margin": None, "eps": {"low": 18.5, "mid": 18.85, "high": 19.2, "currency": "USD"},
                    "basis": "reported", "status": "raised", "quote": "We now expect revenue of $290 to $295 billion", "source": "Q4 FY2025 earnings release"},
       "consensus": {"revenue_growth_fy1": 0.058, "eps_fy1": 18.4, "as_of": "2026-09-30"},
       "guidance_vs_consensus_pct": 0.005, "track_record": "beat the midpoint 7 of the last 8 quarters",
       "estimates": {"bear": {"revenue_growth_fy1": 0.054, "revenue_growth_fy2": 0.05, "eps_fy1": 18.4},
                     "base": {"revenue_growth_fy1": 0.065, "revenue_growth_fy2": 0.06, "eps_fy1": 18.9},
                     "bull": {"revenue_growth_fy1": 0.072, "revenue_growth_fy2": 0.065, "eps_fy1": 19.4}},
       "rationale": "Base above the midpoint for the beat record."}


def _state(dcf: dict, extra: dict | None = None) -> dict:
    return {"data": {"dcf_range": {"COST": dcf}, "reported_currency": "USD", **(extra or {})}}


def test_the_forward_block_cites_guidance_with_its_source_consensus_and_our_estimates():
    blk = pm._forward_estimates_block("COST", _state({"guidance_estimates": _GE, "reported_currency": "USD"}))
    assert "Management guidance for FY2026 (raised) — Q4 FY2025 earnings release, 2026-09-25" in blk
    assert "revenue growth +5.4% to +7.2% (midpoint +6.3%)" in blk and "revenue 290.0 to 295.0 (midpoint 292.5) USD bn" in blk
    assert "EPS USD 18.50 to USD 19.20" in blk
    assert 'Management, verbatim: "We now expect revenue of $290 to $295 billion"' in blk
    assert "Consensus FY2026 (as of 2026-09-30): revenue growth +5.8%; EPS USD 18.40" in blk
    assert "Our estimate, revenue growth FY2026: bear +5.4% / base +6.5% / bull +7.2%" in blk
    assert "Our estimate, EPS FY2026: bear USD 18.40 / base USD 18.90 / bull USD 19.40" in blk
    assert "Guidance track record: beat the midpoint" in blk
    assert "the DCF's first two years run on these estimates" in blk


def test_the_forward_block_falls_back_to_parsed_guidance_or_says_there_is_none():
    blk = pm._forward_estimates_block("COST", _state({}, {"management_guidance": {"COST": {"revenue_guidance_mid": 292.5e9, "margin_direction": "expanding"}}}))
    assert "parsed from the research" in blk and "revenue $292.50bn (midpoint)" in blk and "margin expanding" in blk
    none = pm._forward_estimates_block("COST", _state({}))
    assert "no quantitative forward guidance" in none and "consensus" in none


_SCEN = {"current_price": 250.0, "12m_price_target": 262.28, "12m_pt_method": "forward P/E on FY27 EPS",
         "12m_targets_by_scenario": {"bear": 215.0, "base": 262.0, "bull": 300.0},
         "reconciliation": {"current_price": 250.0, "blended_iv": 333.77, "upside_to_iv_pct": 33.5}}


def test_the_quant_block_names_one_anchor_a_cross_check_and_the_skew_in_rounded_units():
    blk = pm._quant_block_text("COST", _state({"reported_currency": "USD"}), _SCEN)
    assert "PRIMARY ANCHOR: 12m price target $262 on forward P/E on FY27 EPS" in blk
    assert "CROSS-CHECK ONLY: intrinsic value about $334" in blk
    assert "upside to target +5%" in blk and "bear case $215 (-14% from spot)" in blk and "bull case $300 (+20%)" in blk
    assert "skew about 0.4:1 upside to downside" in blk
    assert "Rounded for prose: spot $250, 12m target $262, intrinsic value $334, bear $215, bull $300" in blk
    assert "UNDER-STRESSED" not in blk
    # the older lines are still there for the tests that pin them
    assert "Spot price $250.00" in blk and "12m PT $262.28" in blk


def test_an_untested_bear_case_is_called_out_and_low_prices_keep_one_decimal():
    sc = {**_SCEN, "12m_targets_by_scenario": {"bear": 2.37, "base": 2.50, "bull": 3.46}, "12m_price_target": 2.44,
          "current_price": 2.15, "reconciliation": {"current_price": 2.15, "blended_iv": 2.76}}
    blk = pm._quant_block_text("S51.SI", {"data": {"dcf_range": {"S51.SI": {"reported_currency": "SGD"}}, "reported_currency": "SGD"}}, sc)
    assert "BEAR CASE UNDER-STRESSED: the bear case sits only 5% below the base" in blk
    assert "12m price target S$2.4 on" in blk and "bear S$2.4" in blk and "bull S$3.5" in blk
    # a bear at or above spot: the downside is not modelled
    sc2 = {**_SCEN, "12m_targets_by_scenario": {"bear": 255.0, "base": 262.0, "bull": 300.0}}
    assert "bear case at or above spot" in pm._quant_block_text("COST", _state({"reported_currency": "USD"}), sc2)


def test_rounded_figures_pass_the_number_guard_against_the_precise_inputs():
    inputs = pm._quant_block_text("COST", _state({"reported_currency": "USD"}), _SCEN)
    draft = "1. Overweight: our 12-month target of $262 against a $250 spot, cross-check intrinsic value about $334. 2. Bear case $215, about 0.4:1 skew."
    assert number_guard(draft, inputs)["ok"]


def test_the_human_prompt_carries_the_forward_block():
    src = inspect.getsource(pm.run_advanced_portfolio_manager)
    assert "Forward estimates (management guidance, earnings release → house estimates):" in src
    assert '"forward_block": _forward_estimates_block(ticker, state),' in src
