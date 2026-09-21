"""Backlog-Gated Long Cycle: a profile reached only through an eligibility gate,
and an FCF-guidance overlay that fades.

Owner, 2026-09-22. GE Vernova published $318 against a $955 quote on Capital
Goods because every weighted leg was trailing while the two legs that see its
earnings inflection carried no weight. The profile weights the order book and
the forward legs, and is kept apart from short-cycle industrials (Dover,
Illinois Tool Works) by three rules on owner-accepted filing figures.

On the guidance: "A 25% FCF margin cannot and should not be held flat across a
10-year DCF ... Contract liabilities represent cash borrowed from the future."
"""
import inspect

import pytest

from src.agents.analysis import dcf_agent
from src.data import valuation_constants as vc

PROFILE = "Backlog-Gated Long Cycle"
elig = vc.long_cycle_eligibility
fade = vc.fcf_guidance_margin_schedule


# ── eligibility ──────────────────────────────────────────────────────────────

def test_ge_vernova_clears_all_three_rules_on_its_filed_figures():
    """Backlog $176.3bn / $46.3bn consensus forward sales; FY2025 orders $59.3bn /
    $38.07bn revenue; contract liabilities $25.8bn / ($19.1bn + $10.4bn)."""
    v = elig(sector="Industrials", backlog_coverage=176.284 / 46.29, book_to_bill=59.3 / 38.07,
             contract_liability_share=25.77 / 29.53)
    assert v["eligible"] and v["profile"] == PROFILE and [c["ok"] for c in v["checks"]] == [True, True, True]


@pytest.mark.parametrize("kw,why", [
    (dict(backlog_coverage=2.9, book_to_bill=1.6, contract_liability_share=0.9), "under three years of work"),
    (dict(backlog_coverage=4.0, book_to_bill=1.26, contract_liability_share=0.9), "orders are not outrunning sales"),
    (dict(backlog_coverage=4.0, book_to_bill=1.6, contract_liability_share=0.3), "customers are not funding the build"),
    (dict(backlog_coverage=3.0, book_to_bill=1.5, contract_liability_share=0.5), "AT the thresholds is not above them"),
])
def test_failing_any_one_rule_keeps_the_name_where_it_was(kw, why):
    assert not elig(sector="Industrials", **kw)["eligible"], why


def test_a_rule_that_cannot_be_checked_is_a_rule_that_fails():
    """Eligibility is a claim about the company; one that cannot be checked is not made."""
    v = elig(sector="Industrials", backlog_coverage=4.6, book_to_bill=None, contract_liability_share=0.87)
    assert not v["eligible"] and v["checks"][1] == {"rule": "book-to-bill", "value": None, "minimum": 1.5, "ok": False}


def test_a_short_cycle_sector_is_out_of_scope_whatever_its_figures():
    assert not elig(sector="Tech", backlog_coverage=9, book_to_bill=9, contract_liability_share=9)["eligible"]
    assert elig(sector="Energy", backlog_coverage=9, book_to_bill=9, contract_liability_share=9)["eligible"]


def test_the_share_is_measured_against_working_capital_assets_not_net_working_capital():
    """For exactly these companies the advances drive NET working capital to zero
    or below (GE Vernova FY2025: -$0.75bn). A share of a negative number says nothing."""
    assert "receivables plus inventory" in vc.load()["backlog_gated_long_cycle"]["contract_liability_share_definition"]


# ── the profile ──────────────────────────────────────────────────────────────

def test_the_profile_is_the_owners_table_and_its_anchor_is_computable():
    from src.data.sector_profiles import INDUSTRY_VALUATION_PROFILES as P
    got = [(m["name"], m["weight"], bool(m.get("anchor")), m["implementable"]) for m in P["Industrials"][PROFILE]["methods"]]
    assert got == [("Backlog-coverage DCF", 0.35, True, True), ("Forward EV/EBITDA", 0.25, False, True),
                   ("Forward P/E", 0.20, False, True), ("EV/EBITDA", 0.20, False, True)]
    assert {m[0]: m[1] for m in got} == vc.load()["backlog_gated_long_cycle"]["methods"]


def test_no_row_pin_or_classifier_reaches_it_only_the_gate_does():
    from itertools import product
    from src.data import industry_profile_map as ipm
    from src.data.sector_profiles import TICKER_SECTOR_LOOKUP, classify_valuation_profile
    assert PROFILE not in {p for _, p in ipm.industry_map().values()}
    assert PROFILE not in {p for _, p in ipm.ticker_overrides().values()}
    assert PROFILE not in {v[1] for v in TICKER_SECTOR_LOOKUP.values()}
    for cagr, fcf, de in product((-0.1, 0.05, 0.5), (-0.2, 0.1), (0.0, 3.0)):
        assert classify_valuation_profile("Industrials", cagr, fcf, de) != PROFILE
    src = inspect.getsource(dcf_agent._long_cycle_gate)
    assert 'accepted_detail(ticker, "backlog", ccy)' in src          # review-gated: no accepted backlog, no gate
    assert "if not bl or not bl.get" in src


def test_book_to_bill_is_cited_or_derived_from_accepted_orders_and_the_record_says_which():
    src = inspect.getsource(dcf_agent._long_cycle_gate)
    assert 'bl["orders"] / revenue_base' in src and '"book_to_bill_basis"' in src
    assert '"gate_id": "GATE_LONG_CYCLE_ELIGIBILITY"' in src and '"applied": v["eligible"]' in src


# ── the fade ─────────────────────────────────────────────────────────────────

def test_the_schedule_is_the_owners_three_phases():
    s = fade(12.0 / 46.0)
    assert s["schedule"] == [0.25, 0.25, 0.25, 0.2275, 0.205, 0.1825, 0.16, 0.15, 0.15, 0.15]
    assert s["ceiling_applied"] and s["explicit_margin"] == 0.25 and s["floor"] == 0.15
    cfg = vc.load()["fcf_guidance_fade"]
    assert cfg["explicit_margin_band"] == [0.22, 0.25] and cfg["fade_bps_band"] == [200, 250]
    assert cfg["floor_margin_band"] == [0.145, 0.155]


def test_the_fade_never_goes_through_the_floor_and_never_lifts_a_low_guidance_onto_it():
    assert fade(0.18)["schedule"] == [0.18, 0.18, 0.18, 0.1575, 0.15, 0.15, 0.15, 0.15, 0.15, 0.15]
    assert fade(0.15) is None and fade(0.09) is None and fade(None) is None


def test_the_terminal_takes_the_floor_because_it_takes_the_last_years_margin():
    base = dict(revenue_base=4e10, fcf_margin_base=0.07, growth_rate=0.05, margin_delta_per_year=0.0,
                wacc=0.0825, tgr=0.02, fcf_floor=0.0, net_debt=0.0, shares=2.7e8)
    flat25, *_ = dcf_agent._project_dcf(**base, margin_schedule=[0.25] * 10)
    faded, _, _, rows = dcf_agent._project_dcf(**base, margin_schedule=fade(0.26)["schedule"])
    audited, *_ = dcf_agent._project_dcf(**base)
    assert audited < faded < flat25
    assert [round(r["fcf_margin"], 4) for r in rows][-3:] == [0.15, 0.15, 0.15]
    # Held flat, 25% would be worth far more: most of a DCF is its terminal.
    assert flat25 / faded > 1.4


def test_no_margin_schedule_leaves_the_projector_exactly_as_it_was():
    base = dict(revenue_base=4e10, fcf_margin_base=0.07, growth_rate=0.05, margin_delta_per_year=0.0,
                wacc=0.0825, tgr=0.02, fcf_floor=0.0, net_debt=0.0, shares=2.7e8, margin_delta_absolute=0.01)
    assert dcf_agent._project_dcf(**base) == dcf_agent._project_dcf(**base, margin_schedule=None)


def test_the_overlay_needs_an_acceptance_and_the_forward_overlay_switch():
    """Guidance is never a baseline (owner, 2026-09-20)."""
    src = inspect.getsource(dcf_agent.run_dcf_agent)
    at = src.index('accepted_detail(ticker, "fcf_guidance", _mc_ccy)')
    assert "if _ii.overlay_enabled():" in src[at - 200: at]


def test_the_scenarios_stay_ordered_under_the_faded_overlay(monkeypatch):
    monkeypatch.setattr(dcf_agent, "get_sector_peer_multiples", lambda *a, **k: {})
    row = {"_fcf_guidance_detail": {"guided_margin": 0.26}}
    vals = []
    for scen, md in (("bear", -0.0146), ("base", 0.0), ("bull", 0.0146)):
        vals.append(dcf_agent._compute_method_value(
            method_name="Backlog-coverage DCF", most_recent=row, revenue_base=4e10, shares=2.7e8, net_debt=-9e9,
            market_cap=2.5e11, wacc=0.0825, growth_base=0.05, fcf_margin_base=0.0731, tgr=0.02, fcf_floor=0.0,
            sector="Industrials", scenario=scen, profile_name=PROFILE,
            projection={"growth_schedule": [0.05] * 10, "margin_delta_absolute": md}))
    assert vals[0] < vals[1] < vals[2]


# ── the review gate for guidance ─────────────────────────────────────────────

def test_guidance_must_be_for_a_year_that_has_not_ended_and_everything_else_for_one_that_has():
    from src.data import industry_inputs as ii
    ctx = {"period": "2025-12-31", "revenue": 3.8e10}
    period = lambda kind, p: [c["ok"] for c in ii.reconcile(kind, 1.2e10 if kind == "fcf_guidance" else 1e9, ctx, period=p)
                              if "period" in c["check"]]
    assert period("fcf_guidance", "FY2026E") == [True]
    assert period("fcf_guidance", "FY2025") == [False]          # a year already reported is an actual
    assert period("fcf_guidance", "FY2029E") == [False]         # too far out to be next-year guidance
    assert any(c["ok"] is False for c in ii.reconcile("fcf_guidance", 1.2e13, ctx, period="FY2026E"))   # bn read as tn
