"""
tests/test_m2_d2_pm.py
======================
M2 Track D2 — committee-free PM decision architecture.

* Action bands on blended-IV upside (≥+15 BUY, −10…+15 HOLD, −20…−10
  SHORT, ≤−20 SELL) with env overrides.
* Qualitative gates move at most ONE step: material freshness-delta
  direction, TRAP RISK HIGH blocks BUY, degraded research caps at
  HOLD/SELL; gates never stack beyond one step from the band.
* Missing valuation → HOLD, size 0, flagged.
* Sizing = approved × ev_factor × power_factor × qualitative_conviction
  (0.5/0.75/1.0); conviction capped at 0.75 by regime misalignment or an
  unresolved regulatory watch item.
* Research digest: sections present → recent_news + excerpts within
  budget; sections missing → head-truncate the full text.
* decision_inputs payload for the D3 frontend card.
"""
import pytest

import src.agents.portfolio_manager as pm
import src.utils.llm as llm_mod
from src.agents.portfolio_manager import run_advanced_portfolio_manager


# ── harness (same shape as test_m2_b_guards.py) ──────────────────────────────

def _fake_call_llm(prompt=None, pydantic_model=None, agent_name=None,
                   state=None, default_factory=None, **kwargs):
    return pydantic_model(
        action="HOLD", position_size_pct=0.01,
        entry_range=[100.0, 110.0], stop_loss=95.0, price_target=120.0,
        time_horizon="medium",
        rationale="1. Anchor rationale — blended IV $150 vs spot $127.48.",
    )


@pytest.fixture
def pm_llm(monkeypatch):
    monkeypatch.setattr(llm_mod, "call_llm", _fake_call_llm)


def _state(ticker="TEST", scenario=None, extra_data=None):
    state = {
        "messages": [],
        "data": {
            "tickers": [ticker],
            "analyst_signals": {
                "advanced_risk_manager": {ticker: {"approved_size_pct": 0.10}},
            },
            "scenario_analysis": {ticker: scenario or {}},
            "power_law_analysis": {ticker: {"total_score": 6}},
            "value_trap_analysis": {ticker: {"overall_verdict": "TRAP RISK LOW"}},
        },
    }
    if extra_data:
        state["data"].update(extra_data)
    return state


def _scenario(upside_iv, pt_12m=None, current=100.0, ev=105.0,
              bear=80.0, bull=150.0, upside_pct=5.0):
    """Sensible defaults per band are chosen by the caller via pt_12m so
    the directional guards stay out of the way of the band under test."""
    return {
        "current_price": current,
        "expected_value": ev,
        "upside_pct": upside_pct,
        "12m_price_target": pt_12m,
        "bear": {"fair_value": bear},
        "bull": {"fair_value": bull},
        "reconciliation": {"blended_iv": current * (1 + upside_iv / 100),
                           "upside_to_iv_pct": upside_iv},
    }


def _run(state):
    out = run_advanced_portfolio_manager(state)
    return out["decisions"][state["data"]["tickers"][0]], state


# ── action bands ─────────────────────────────────────────────────────────────

def test_band_buy(pm_llm):
    # BUY band; PT clears the BUY directional guard (≥ 0.95 × spot)
    d, _ = _run(_state(scenario=_scenario(20.0, pt_12m=130.0, ev=130.0)))
    assert d["action"] == "BUY"
    assert d["decision_inputs"]["quantitative"]["band_action"] == "BUY"


def test_band_hold(pm_llm):
    d, _ = _run(_state(scenario=_scenario(5.0, pt_12m=105.0)))
    assert d["action"] == "HOLD"


def test_band_short(pm_llm):
    # PT below spot keeps the B1 guard silent
    d, _ = _run(_state(scenario=_scenario(-15.0, pt_12m=90.0)))
    assert d["action"] == "SHORT"


def test_band_sell(pm_llm):
    d, _ = _run(_state(scenario=_scenario(-25.0, pt_12m=80.0, upside_pct=-10.0)))
    assert d["action"] == "SELL"
    assert d["position_size_pct"] > 0   # Fix-1b floor keeps shorts actionable


def test_band_threshold_env_override(monkeypatch, pm_llm):
    monkeypatch.setenv("PM_BUY_BAND_PCT", "5")
    # +10% is HOLD under defaults but BUY with the override
    d, _ = _run(_state(scenario=_scenario(10.0, pt_12m=120.0, ev=120.0)))
    assert d["action"] == "BUY"


def test_band_falls_back_to_ev_upside(pm_llm):
    """No reconciliation block → EV-based upside feeds the band."""
    scen = _scenario(0.0)
    del scen["reconciliation"]
    scen["expected_value"] = 130.0          # +30% vs spot 100 → BUY band
    scen["12m_price_target"] = 125.0
    d, _ = _run(_state(scenario=scen))
    assert d["action"] == "BUY"


def test_missing_valuation_hold_size_zero(pm_llm):
    d, state = _run(_state(scenario={}))
    assert d["action"] == "HOLD"
    assert d["position_size_pct"] == 0.0
    assert "valuation missing" in state["data"]["consistency_flags"]["TEST"]


# ── qualitative gates ────────────────────────────────────────────────────────

def test_trap_high_blocks_buy(pm_llm):
    scen = _scenario(20.0, pt_12m=130.0, ev=130.0)
    state = _state(scenario=scen, extra_data={
        "value_trap_analysis": {"TEST": {"overall_verdict": "TRAP RISK HIGH"}}})
    d, st = _run(state)
    assert d["action"] == "HOLD"
    assert any("blocked BUY" in g for g in d["decision_inputs"]["gates"])
    # trap halving also applies to sizing
    assert "TRAP RISK HIGH" in st["data"]["value_trap_analysis"]["TEST"]["overall_verdict"]


def test_material_adverse_delta_shifts_one_step_bearish(pm_llm):
    delta = {"material": True,
             "events": [{"headline": "Guidance cut after probe",
                         "date": "2026-08-18", "relevance": "thesis risk"}],
             "verdict": "material adverse change"}
    state = _state(scenario=_scenario(0.0, pt_12m=95.0),
                   extra_data={"freshness_delta": {"TEST": delta}})
    d, _ = _run(state)
    assert d["action"] == "SHORT"     # HOLD band shifted one step bearish
    assert any("one step" in g for g in d["decision_inputs"]["gates"])


def test_material_positive_delta_shifts_one_step_bullish(pm_llm):
    delta = {"material": True,
             "events": [{"headline": "FDA approval, beats estimates",
                         "date": "2026-08-18", "relevance": "thesis boost"}],
             "verdict": "material positive change"}
    # PT clears the BUY guard after the shift lands on BUY
    state = _state(scenario=_scenario(0.0, pt_12m=120.0, ev=120.0),
                   extra_data={"freshness_delta": {"TEST": delta}})
    d, _ = _run(state)
    assert d["action"] == "BUY"


def test_gates_never_stack_beyond_one_step(pm_llm):
    """BUY band + adverse delta (−1) + trap gate: the delta already moved
    off BUY, so the trap block finds nothing to block; final = HOLD =
    exactly one step from the band."""
    delta = {"material": True,
             "events": [{"headline": "Profit warning issued",
                         "date": "2026-08-18", "relevance": "downside"}],
             "verdict": "material adverse"}
    scen = _scenario(20.0, pt_12m=130.0, ev=130.0)
    state = _state(scenario=scen, extra_data={
        "freshness_delta": {"TEST": delta},
        "value_trap_analysis": {"TEST": {"overall_verdict": "TRAP RISK HIGH"}}})
    d, _ = _run(state)
    assert d["action"] == "HOLD"      # BUY → SHORT via delta; trap N/A on SHORT


def test_delta_without_direction_does_not_shift(pm_llm):
    """Material but keyword-ambiguous → no band movement."""
    delta = {"material": True,
             "events": [{"headline": "CEO commentary on strategy",
                         "date": "2026-08-18", "relevance": "mixed"}],
             "verdict": "material but unclear direction"}
    state = _state(scenario=_scenario(0.0, pt_12m=100.0),
                   extra_data={"freshness_delta": {"TEST": delta}})
    d, _ = _run(state)
    assert d["action"] == "HOLD"


def test_degraded_research_caps_buy_at_hold(pm_llm):
    state = _state(scenario=_scenario(20.0, pt_12m=130.0, ev=130.0),
                   extra_data={"research_tier": "knowledge_only"})
    d, _ = _run(state)
    assert d["action"] == "HOLD"
    assert any("stale research" in g for g in d["decision_inputs"]["gates"])


def test_degraded_research_caps_short_at_sell(pm_llm):
    state = _state(scenario=_scenario(-15.0, pt_12m=90.0, upside_pct=-8.0),
                   extra_data={"research_degraded": True,
                               "research_tier": "qwen_web"})
    d, _ = _run(state)
    assert d["action"] == "SELL"


# ── research digest ──────────────────────────────────────────────────────────

def test_digest_uses_sections_when_present():
    state = _state(extra_data={
        "deep_research_sections": {
            "recent_news": "LATEST DEVELOPMENTS (as of 2026-08-20): news body",
            "2f": "Research narrative thesis body",
            "2a": "Profit pool body",
        },
        "industry_brief": "Industry brief body",
    })
    digest = pm._build_research_digest(state, "TEST")
    assert "LATEST DEVELOPMENTS" in digest
    assert "news body" in digest
    assert "Research narrative thesis body" in digest
    assert "INDUSTRY BRIEF" in digest
    assert len(digest) <= 8000


def test_digest_falls_back_to_full_text_head_truncation():
    state = _state(extra_data={"deep_research": "F" * 9000})
    digest = pm._build_research_digest(state, "TEST")
    assert digest.startswith("F" * 100)
    assert "head-truncated" in digest
    assert len(digest) < 9000


def test_digest_empty_state():
    assert "no research" in pm._build_research_digest(_state(), "TEST")


# ── conviction + caps ────────────────────────────────────────────────────────

def _rich_state(**extra):
    """Fresh tier + known delta + all-A VGPM → raw conviction 1.0."""
    extra_data = {
        "research_tier": "qwen_web",
        "freshness_delta": {"TEST": {"material": False, "events": [],
                                     "verdict": "current"}},
        "vgpm": {"TEST": {d: {"grade": "A", "score": 90}
                          for d in ("valuation", "growth",
                                    "profitability", "momentum")}},
    }
    extra_data.update(extra)
    return _state(scenario=_scenario(20.0, pt_12m=130.0, ev=130.0),
                  extra_data=extra_data)


def test_conviction_full_when_fresh_and_broad(pm_llm):
    d, _ = _run(_rich_state())
    assert d["decision_inputs"]["conviction"]["value"] == 1.0


def test_regime_risk_off_caps_buy_conviction(pm_llm):
    state = _rich_state(macro_regime={
        "risk_appetite": "risk-off", "volatility_regime": "high",
        "recession_risk": "low", "regime_notes": "stressed"})
    d, _ = _run(state)
    ci = d["decision_inputs"]["conviction"]
    assert ci["value"] == 0.75
    assert any("regime" in n for n in ci["notes"])


def test_regime_risk_on_caps_short_conviction(pm_llm):
    state = _state(scenario=_scenario(-15.0, pt_12m=90.0, upside_pct=-8.0),
                   extra_data={
                       "research_tier": "qwen_web",
                       "freshness_delta": {"TEST": {"material": False,
                                                    "events": [],
                                                    "verdict": "current"}},
                       "vgpm": {"TEST": {d: {"grade": "A"} for d in
                                         ("valuation", "growth",
                                          "profitability", "momentum")}},
                       "macro_regime": {"risk_appetite": "risk-on"},
                   })
    d, _ = _run(state)
    assert d["action"] == "SHORT"
    assert d["decision_inputs"]["conviction"]["value"] == 0.75


def test_regulatory_watch_caps_conviction_and_flags(pm_llm):
    state = _rich_state(deep_research_sections={
        "recent_news": "Regulators open a CSRC probe into disclosures."})
    d, st = _run(state)
    ci = d["decision_inputs"]["conviction"]
    assert ci["value"] == 0.75
    assert d["decision_inputs"]["qualitative"]["regulatory_watch"]
    assert "regulatory watch" in st["data"]["consistency_flags"]["TEST"]


def test_sizing_uses_conviction_not_zero(pm_llm):
    d, _ = _run(_rich_state())
    assert d["action"] == "BUY"
    assert d["position_size_pct"] > 0


# ── decision_inputs payload ──────────────────────────────────────────────────

def test_decision_inputs_payload_shape(pm_llm):
    d, _ = _run(_state(scenario=_scenario(-15.0, pt_12m=90.0)))
    di = d["decision_inputs"]
    assert di["quantitative"]["band_action"] == "SHORT"
    assert di["quantitative"]["upside_to_iv_pct"] == -15.0
    assert "trap_verdict" in di["quantitative"]
    assert "research_tier" in di["qualitative"]
    assert isinstance(di["gates"], list)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
