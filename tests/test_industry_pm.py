"""Industry-specific PM write-up (owner, 2026-09-27): family desk rules, the checklist from the
record, and the number guard that keeps the thesis to the inputs."""
import inspect

import pytest

from src.agents import portfolio_manager as pm
from src.agents.pm import industry_pm as ip
from src.data import report_families as rf


def test_the_five_focus_families_have_desk_rules_in_their_own_language():
    words = {
        "Energy and resources": ("MID-CYCLE", "strip", "PV-10", "capital-returns"),
        "Industrials, materials and transport": ("BACKLOG VISIBILITY", "book-to-bill", "aftermarket", "programme"),
        "Consumer": ("VOLUME, PRICE and MIX", "gross margin", "payout", "staples basket"),
        "Health care": ("risk-adjusted NPV", "PTRS", "medical loss ratio", "reimbursement"),
        "Technology, telecom and media": ("FORWARD multiple", "net revenue retention", "stock-based compensation", "Growth_Inflection_Speculative"),
    }
    for fam, ws in words.items():
        add = ip.family_addendum(fam)
        for w in ws:
            assert w in add, (fam, w)
        assert "FIDELITY RULE" in add
    assert "deposits are not debt" in ip.family_addendum("Banks")
    assert ip.family_addendum("Operating company").startswith("FIDELITY RULE")
    for fam in rf.REPORT_FAMILIES:
        if fam != "Operating company":
            assert fam in ip.FAMILY_ADDENDA, fam


def _dr(**over):
    base = {"profile": "Integrated Oil & Gas", "anchor_method": "EV/EBITDA (norm)", "reported_currency": "USD",
            "pt_bridge": {"spot": 160.56}, "consensus_spread": 0.047, "regime_flag": None,
            "gate_evaluations": [],
            "base": {"intrinsic_value": 63.0, "methods_used": ["EV/EBITDA (norm)", "EV/OCF", "FCF Yield", "DDM"],
                     "method_iv_table": {"EV/EBITDA (norm)": 58.2, "FCF Yield": 71.4, "DDM": 66.0},
                     "legs_dropped": [],
                     "forward_flags": ["Normalized NI: TTM $33.7B -> 5y-cycle $41.2B (+22%) -- P/E (norm) will use normalized figure",
                                       "rNPV (Pipeline): quarantined -- no owner-accepted pipeline input"],
                     "leg_inputs": {"EV/EBITDA (norm)": {"value": 58.2, "multiple": 5.4, "multiple_parts": {"peer_source": "peer median ev_ebitda"}},
                                    "FCF Yield": {"value": 71.4, "multiple": 14.3, "target_yield": 0.07},
                                    "DCF": {"value": 60.0, "fcf_margin_base": 0.11, "growth_base": 0.02, "wacc": 0.085}}}}
    base.update(over)
    return base


def test_the_energy_checklist_states_the_anchor_cycle_and_legs_with_numbers():
    lines = ip.family_checklist("Energy and resources", _dr(), {"current_price": 160.56}, {}, "$")
    joined = "\n".join(lines)
    assert "Anchor EV/EBITDA (norm) = $58.20 per share (in the blend); blended IV $63.00" in joined
    assert "EV/EBITDA (norm) leg $58.20 on 5.40x (peer median ev_ebitda)" in joined
    assert "target yield +7.0%" in joined and "Cycle: Normalized NI" in joined
    assert "DCF base: FCF margin +11.0%, growth +2.0%, WACC +8.5%" in joined
    assert "Consensus target sits +5% from spot" in joined
    assert any(l.startswith("Engine flag: rNPV") for l in lines)


def test_the_aerospace_checklist_leads_with_backlog_visibility():
    dr = _dr(profile="Defense Primes", anchor_method="Backlog-coverage DCF",
             gate_evaluations=[{"gate_id": "GATE_BACKLOG_VISIBILITY", "gated_output_path_b": 2.31,
                                "basis": "accepted backlog / revenue base"}])
    dr["base"]["method_iv_table"]["Backlog-coverage DCF"] = 554.4
    dr["base"]["methods_used"] = ["Backlog-coverage DCF"]
    dr["base"]["leg_inputs"]["Backlog-coverage DCF"] = {"value": 554.4}
    lines = ip.family_checklist("Industrials, materials and transport", dr, None, {}, "$")
    assert any(l.startswith("Backlog visibility: accepted backlog = 2.31 years of revenue") for l in lines)
    assert any("Backlog-coverage DCF leg $554.40" in l for l in lines)


def test_the_health_and_bank_checklists_read_their_gates_and_breakdowns():
    dr = _dr(profile="Commercial Biotech", anchor_method="Forward P/E",
             gate_evaluations=[{"gate_id": "GATE_FORWARD_PE_SANITY", "raw_input_path_a": 53.6, "gated_output_path_b": {"EV/Fwd Rev": 0.219}}])
    dr["base"]["leg_inputs"]["EV/Fwd Rev"] = {"value": 300.1, "multiple": 5.12, "multiple_parts": {"peer_source": "peer"}}
    lines = ip.family_checklist("Health care", dr, None, {}, "$")
    assert any("Forward P/E sanity gate fired: forward P/E at spot 53.6x" in l for l in lines)
    assert any("EV/Fwd Rev leg $300.10 on 5.12x" in l for l in lines)
    bank = _dr(profile="Money Center Bank", anchor_method="GGM (P/B)", bank_breakdown={"tbv_per_share": 106.85})
    bank["base"]["leg_inputs"]["GGM (P/B)"] = {"value": 241.96, "target_pb": 1.8617, "assumptions": {"roe": 0.195, "coe": 0.10, "g": 0.03}}
    bank["pt_bridge"]["spot"] = 343.06
    lines = ip.family_checklist("Banks", bank, None, {}, "$")
    assert any("GGM: RoTE +19.5%, CoE +10.0%, g +3.0% -> target P/B 1.86x; TBV/share $106.85" in l for l in lines)
    assert any("Spot P/TBV 3.21x" in l for l in lines)


INPUTS = ("Spot price $343.06 (current market price)\nBlended IV $209.10\n12m PT $276.08\nWACC 9.3%\n"
          "GGM: RoTE +19.5%, CoE +10.0%, g +3.0% -> target P/B 1.86x; TBV/share $106.85\nSpot P/TBV 3.21x\n"
          "Research digest: 2026-07-15 Q2 markets revenue +45% YoY; CET1 14.1%")


def test_the_number_guard_passes_supplied_figures_and_catches_invented_ones():
    ok = ("1. Underweight: at US$343.06 the 12-month target of US$276.08 is -19.5% below spot.\n"
          "2. Our GGM assumes a 19.5% RoTE, a 10.0% cost of equity and 3.0% growth for a 1.86x target P/B against 3.21x P/TBV.\n"
          "3. Markets revenue rose 45% in Q2 2026; CET1 of 14.1% is genuine but priced in.")
    v = ip.number_guard(ok, INPUTS)
    assert v["ok"], v
    bad = ok + "\n4. The 41.7 percentage-point divergence against a consensus claim of +45% and a 17% ROTCE undermine the case; buybacks at a 52% premium destroy value."
    v2 = ip.number_guard(bad, INPUTS)
    assert not v2["ok"] and 41.7 in v2["offending_numbers"] and 17.0 in v2["offending_numbers"] and 52.0 in v2["offending_numbers"]
    assert 45.0 not in v2["offending_numbers"]                        # supplied by the digest
    stripped = ip.strip_offending(bad, v2)
    assert "41.7" not in stripped and "19.5% RoTE" in stripped and stripped.count("\n") == 2
    assert "41.7" in ip.retry_instruction(v2)
    # scale tolerance: 15.5B against 15,500,000,000; theme numbers and years are always allowed
    assert ip.number_guard("1. Net cash of $15.5B in 2026.", "net cash 15500000000").get("ok")
    assert ip.number_guard("2. Trades at 24x.", "24.1x fwd P/E").get("ok")
    assert not ip.number_guard("2. Trades at 31x.", "24.1x fwd P/E").get("ok")


def test_the_pm_agent_is_wired_to_the_family_language_and_the_guard():
    src = inspect.getsource(pm.run_advanced_portfolio_manager)
    assert "family_addendum as _fam_add" in src and "_system_prompt = _system_prompt + _fam_add(_family)" in src
    assert "family_checklist as _fam_check" in src and "_quant_block = _quant_block + " in src
    assert "number_guard as _ng" in src and "_retry_txt(_verdict)" in src and 'd["rationale_fidelity"]' in src
    assert "taken from the anchors" in pm._PM_RATIONALE_SYSTEM_PROMPT
    assert "TWO specific figures with units" in pm._PM_RATIONALE_SYSTEM_PROMPT      # the density test's pin survives
    assert "deposits are not debt" in pm._PM_BANK_RATIONALE_ADDENDUM                # kept for the fallback path
