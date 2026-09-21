"""Unrated / Pre-Revenue: a valuation that is withheld, not flagged.

Owner, 2026-09-21: "SMR and pre-revenue names urgently require an explicit
'Unrated / Pre-Revenue' state rather than outputting distorted fractional-weight
numbers like $3.90."

NuScale priced at $3.90 on 0.20 of its profile's intended weight, from a
forward-revenue multiple alone. CGN Power once published -3.24: with every leg
dropped the raw DCF was published in the blend's place. A number that is
published gets quoted, so the state REMOVES the headline figures at the
producer and every consumer learns what absence means -- above all the ones
that would quietly put a number back.
"""
import inspect

import pytest

from src.agents import portfolio_manager as pm
from src.agents.analysis import dcf_agent, scenario_agent
from src.data import valuation_constants as vc
from src.decisions import ratings
from src.memory import valuation_outcomes as vo

verdict = vc.unrated_verdict


# ── the verdict ──────────────────────────────────────────────────────────────

def test_the_three_reasons_and_the_order_they_are_tested_in():
    assert verdict(revenue_base=4e6, base_iv=12.0, weight_surviving=1.0)["code"] == "pre_revenue"
    assert verdict(revenue_base=8e10, base_iv=-3.24, weight_surviving=0.0)["code"] == "no_valuation"   # CGN Power
    assert verdict(revenue_base=8e10, base_iv=None, weight_surviving=None)["code"] == "no_valuation"
    smr = verdict(revenue_base=3.7e7, base_iv=3.90, weight_surviving=0.20)                              # NuScale
    assert smr["code"] == "insufficient_methods" and "20%" in smr["reason"] and smr["label"].startswith("Unrated")


def test_a_name_with_most_of_its_method_set_stays_rated_even_with_its_anchor_dropped():
    """Bloom: 0.65 surviving, anchor dropped. Rated, and its flags say so."""
    assert verdict(revenue_base=2e9, base_iv=46.55, weight_surviving=0.65) is None
    assert verdict(revenue_base=2e10, base_iv=58.93, weight_surviving=0.90) is None
    assert verdict(revenue_base=2e10, base_iv=58.93, weight_surviving=0.35) is None       # at the floor: rated


def test_an_unmeasured_weight_is_not_a_reason_to_withhold():
    """Older profiles do not record weight_surviving. Absence of a measurement
    must never turn a rated name into an unrated one."""
    assert verdict(revenue_base=2e10, base_iv=100.0, weight_surviving=None) is None


def test_the_thresholds_are_owner_set_and_read_from_the_constants_file():
    cfg = vc.load()["unrated"]
    assert cfg["pre_revenue_below"] == 10_000_000 and cfg["min_weight_surviving"] == 0.35
    doc = {"unrated": {"pre_revenue_below": 1e6, "min_weight_surviving": 0.10}}
    assert verdict(revenue_base=4e6, base_iv=3.9, weight_surviving=0.20, doc=doc) is None


# ── the producer ─────────────────────────────────────────────────────────────

def test_the_producer_removes_the_headline_figures_and_keeps_the_evidence():
    src = inspect.getsource(dcf_agent.run_dcf_agent)
    at = src.index("_vc_unr.unrated_verdict(")
    block = src[at: at + 2600]
    assert 'scenario_results[_s]["intrinsic_value"] = None' in block
    assert '_12m_targets = {"bear": None, "base": None, "bull": None}' in block
    assert '"indicative_iv"' in block and "NOT a valuation" in block
    # Decided after every internal check that needs real numbers, published with the payload.
    assert at < src.index('"rating_state":       _rating_state')
    assert src.index("_cons_mult") < at


# ── the rating layer ─────────────────────────────────────────────────────────

RS = {"state": "unrated", "code": "insufficient_methods", "label": "Unrated — too little of the method set "
      "could be computed", "reason": "only 20% of the profile's intended method weight could be computed",
      "weight_surviving": 0.2}


def test_unrated_is_the_absence_of_an_opinion_not_a_fourth_one():
    v = ratings.build_unrated_view(RS, price=38.2)
    assert v["research_rating"] == "UNRATED" and v["trade_action"] == "HOLD"
    assert v["target_12m"] is None and v["intrinsic_value"] is None and v["tsr_12m"] is None
    assert "not a Neutral view" in v["rating_definition"]
    # No action maps TO it: a HOLD is Neutral, never Unrated.
    assert ratings.ResearchRating.UNRATED not in ratings.ACTION_TO_RATING_MAP.values()
    assert ratings.to_rating("HOLD") == ratings.ResearchRating.NEUTRAL


def test_an_unrated_view_has_every_key_a_rated_one_has_so_no_reader_needs_a_second_shape():
    rated = ratings.build_research_view(ticker="AAPL", sector="Tech", price=100.0, price_as_of=None,
                                        target_12m=120.0, intrinsic_value=130.0, dps=1.0)
    assert set(rated) <= set(ratings.build_unrated_view(RS, price=38.2))


def test_a_stored_unrated_decision_is_never_relabelled_neutral_on_read():
    row = {"action": "HOLD", "research_rating": "UNRATED", "rating_label": "Unrated"}
    assert ratings.normalize_legacy_rating(row)["research_rating"] == "UNRATED"


# ── the consumers that would put a number back ───────────────────────────────

def test_the_scenario_agent_never_rebuilds_a_target_from_llm_fair_values():
    """Its fallback branch does exactly that whenever the engine's targets are
    missing -- which is what an unrated name's are."""
    src = inspect.getsource(scenario_agent)
    unrated_at = src.index('scenario_dict["12m_pt_method"] = "unrated: no target published"')
    fallback_at = src.index('"scenario fair values (forward multiple unavailable)"')
    assert unrated_at < fallback_at
    assert "elif _12m_bull and _12m_base and _12m_bear:" in src
    assert "if _unrated:\n            _blended_iv = None" in src


def test_the_portfolio_manager_gives_the_unrated_view_and_has_the_last_word():
    state = {"data": {"dcf_range": {"SMR": {"rating_state": RS}}, "sectors": {"SMR": "Energy"}}}
    view = pm._research_view_for("SMR", state, {"12m_price_target": None, "current_price": 38.2}, {})
    assert view["research_rating"] == "UNRATED"
    # ...and even when a stale scenario still carries a target, unrated wins.
    view = pm._research_view_for("SMR", state, {"12m_price_target": 55.0, "current_price": 38.2}, {})
    assert view["research_rating"] == "UNRATED" and view["target_12m"] is None
    src = inspect.getsource(pm.run_advanced_portfolio_manager)
    last = src[src.index("the LAST word on this decision"):]
    for line in ('d["action"] = "HOLD"', 'd["position_size_pct"] = 0.0', 'd["price_target"] = None',
                 'd["stop_loss"] = None'):
        assert line in last
    assert '"rating_basis": ("unrated" if _is_unrated' in src
    assert "Do NOT state or imply a" in pm._rating_block_text(view)


def test_the_outcome_ledger_does_not_score_a_forecast_nobody_made():
    dr = {"rating_state": RS, "base": {"intrinsic_value": None}}
    # An older PM row can still carry a price_target beside an unrated valuation.
    row = {"run_at": "2026-09-21T10:00:00", "scenario_json": '{"12m_price_target": 55.0}',
           "price_target": 55.0, "run_id": "r1"}
    assert vo.is_unrated(dr) and vo._prediction(row, "SMR", dr) is None
    rated = {"base": {"intrinsic_value": 100.0}}
    assert not vo.is_unrated(rated) and not vo.is_unrated(None)
    assert "skipped_unrated" in inspect.getsource(vo.score_matured)


def test_nothing_that_formats_an_intrinsic_value_assumes_it_is_a_number():
    from src.agents import valuation
    assert "isinstance((dcf_engine.get(_s) or {}).get(\"intrinsic_value\"), (int, float))" in inspect.getsource(valuation)
    import src.pipeline as pipeline
    assert 'if isinstance(base_iv, (int, float))' in inspect.getsource(pipeline)
    assert "if base_iv is not None" in inspect.getsource(dcf_agent.run_dcf_agent)
