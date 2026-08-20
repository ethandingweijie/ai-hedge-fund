"""
tests/test_m2_b_guards.py
=========================
M2 Track B — decision-consistency guards.

B1: SHORT-side directional guard (mirror of the BUY guard). A SELL/SHORT
    whose price target is at/above the current price is either clamped to
    the bear-case fair value (bear below current) or downgraded to HOLD
    (neither the 12m PT nor the bear case below current). Reproduces the
    prod BABA 08-19 defect: SHORT with PT $134.76 vs spot $127.48.
B2: Currency-labeled research inputs — _fmt_fmp/_currency_symbol label
    figures with the reporting currency; the C6 consistency check gains an
    FX-aware currency-mislabel flag and the magnitude check accepts
    correctly USD-converted claims for non-USD reporters.
B3: Flip-justification backstop — reversing the prior report's direction
    without a material freshness delta appends a visible flag to the
    rationale and consistency_flags; a material delta clears it.
"""
import pytest

import src.agents.industry.deep_research as dr
import src.utils.llm as llm_mod
from src.agents.portfolio_manager import run_advanced_portfolio_manager


# ── harness ───────────────────────────────────────────────────────────────────

def _fake_call_llm(prompt=None, pydantic_model=None, agent_name=None,
                   state=None, default_factory=None, **kwargs):
    """The PM pins action/size/stop/PT from Python — the LLM only writes the
    rationale, so a canned decision object is enough. The rationale carries
    numbers so the anchor-citation check stays quiet."""
    return pydantic_model(
        action="HOLD", position_size_pct=0.01,
        entry_range=[100.0, 110.0], stop_loss=95.0, price_target=120.0,
        time_horizon="medium",
        rationale="1. Test rationale — blended IV $125 vs spot $127.48 (5% gap).",
    )


@pytest.fixture
def pm_llm(monkeypatch):
    monkeypatch.setattr(llm_mod, "call_llm", _fake_call_llm)


def _state(ticker="BABA", scenario=None, prior=None, delta=None):
    """Minimal PM state. M2 D2: the action comes from the quantitative band
    on reconciliation.upside_to_iv_pct (set on the scenario fixture), not
    from any investor panel signal."""
    state = {
        "messages": [],
        "data": {
            "tickers": [ticker],
            "analyst_signals": {
                "advanced_risk_manager": {ticker: {"approved_size_pct": 0.08}},
            },
            "scenario_analysis": {ticker: scenario or {}},
            "power_law_analysis": {ticker: {"total_score": 6}},
            "value_trap_analysis": {ticker: {"overall_verdict": "TRAP RISK LOW"}},
        },
    }
    if prior is not None:
        state["data"]["prior_recap"] = {ticker: prior}
    if delta is not None:
        state["data"]["freshness_delta"] = {ticker: delta}
    return state


def _scenario(current=127.48, pt_12m=134.76, bear=100.0, bull=160.0,
              ev=120.0, upside_iv=-25.0):
    """upside_iv defaults to the SELL band (≤ −20%) so the SHORT-side
    guards are exercised; override per band as needed."""
    return {
        "current_price": current,
        "expected_value": ev,
        "upside_pct": -5.0,
        "12m_price_target": pt_12m,
        "bear": {"fair_value": bear},
        "bull": {"fair_value": bull},
        "reconciliation": {"blended_iv": 125.0,
                           "upside_to_iv_pct": upside_iv},
    }


def _run(state):
    out = run_advanced_portfolio_manager(state)
    ticker = state["data"]["tickers"][0]
    return out["decisions"][ticker], state


# ── B1: SHORT-side directional guard ─────────────────────────────────────────

def test_b1_short_pt_above_spot_clamps_to_bear(pm_llm):
    """The BABA repro: PT $134.76 > spot $127.48, bear $100 < spot →
    keep the bearish action, clamp PT to the bear-case IV."""
    decision, state = _run(_state(scenario=_scenario()))
    assert decision["action"] == "SELL"
    assert decision["price_target"] == 100.0
    flag = state["data"]["consistency_flags"]["BABA"]
    assert "clamped to downside anchor" in flag
    assert decision["stop_loss"] == pytest.approx(127.48 * 1.10)


def test_b1_short_pt_above_spot_bear_also_above_downgrades(pm_llm):
    """Neither the 12m PT nor the bear case below current → HOLD, and the
    stop flips to the long-side 0.90× (HOLD semantics)."""
    decision, state = _run(
        _state(scenario=_scenario(pt_12m=134.76, bear=150.0, ev=120.0)))
    assert decision["action"] == "HOLD"
    assert decision["stop_loss"] == pytest.approx(127.48 * 0.90)
    # neutral reference target (mirrors the BUY-downgrade path)
    assert decision["price_target"] == 120.0
    flag = state["data"]["consistency_flags"]["BABA"]
    assert "downgraded to HOLD" in flag


def test_b1_short_with_pt_below_spot_untouched(pm_llm):
    """A consistent SHORT (PT already below spot) passes through unflagged."""
    decision, state = _run(_state(scenario=_scenario(pt_12m=110.0)))
    assert decision["action"] == "SELL"
    assert decision["price_target"] == 110.0
    assert "consistency_flags" not in state["data"]


def test_b1_short_bear_missing_falls_back_to_default_anchor(pm_llm):
    """No usable bear fair value (None) → the 0.80×current anchor applies;
    it is below current, so the clamp path targets it instead of downgrading."""
    scen = _scenario(bear=None)   # {"bear": {"fair_value": None}}
    decision, _ = _run(_state(scenario=scen))
    assert decision["action"] == "SELL"
    assert decision["price_target"] == pytest.approx(127.48 * 0.80, rel=1e-3)


# ── B3: flip-justification backstop ──────────────────────────────────────────

def _prior(action="BUY"):
    return {
        "run_id": "run-prev", "run_at": "2026-03-01T00:00:00",
        "age_days": 170.0, "final_action": action,
        "recap_text": "prior thesis",
        "recap_json": {"price_target": 247.0, "assumptions": [],
                       "catalysts": []},
    }


def test_b3_flip_without_material_news_flags(pm_llm):
    """Prior BUY → SELL with a non-material delta → visible flip flag on the
    rationale AND in consistency_flags."""
    decision, state = _run(_state(
        scenario=_scenario(pt_12m=110.0),
        prior=_prior("BUY"),
        delta={"material": False, "events": [], "verdict": "still current"},
    ))
    assert decision["action"] == "SELL"
    assert "flipped from BUY without material fresh news" in decision["rationale"]
    assert "flipped from BUY" in state["data"]["consistency_flags"]["BABA"]


def test_b3_flip_with_material_news_not_flagged(pm_llm):
    decision, state = _run(_state(
        scenario=_scenario(pt_12m=110.0),
        prior=_prior("BUY"),
        delta={"material": True,
               "events": [{"headline": "Guidance cut", "date": "2026-08-14",
                           "relevance": "breaks thesis"}],
               "verdict": "thesis broken"},
    ))
    assert decision["action"] == "SELL"
    assert "flipped from" not in decision["rationale"]
    assert "consistency_flags" not in state["data"]


def test_b3_flip_from_short_to_buy_flagged_when_delta_unavailable(pm_llm):
    """material None (check unavailable) is NOT evidence → flag still fires."""
    decision, state = _run(_state(
        # BUY band (≥ +15%) with a PT that clears the BUY directional guard
        scenario=_scenario(pt_12m=160.0, ev=150.0, upside_iv=20.0),
        prior=_prior("SHORT"),
        delta=None,
    ))
    assert decision["action"] == "BUY"
    assert "flipped from SHORT without material fresh news" in decision["rationale"]


def test_b3_neutral_prior_is_not_a_flip(pm_llm):
    """HOLD → SELL is a new bearish view, not a reversal of a long call."""
    decision, _ = _run(_state(
        scenario=_scenario(pt_12m=110.0),
        prior=_prior("HOLD"),
        delta={"material": False, "events": [], "verdict": "still current"},
    ))
    assert decision["action"] == "SELL"
    assert "flipped from" not in decision["rationale"]


def test_b3_no_prior_no_flag(pm_llm):
    decision, _ = _run(_state(scenario=_scenario(pt_12m=110.0)))
    assert decision["action"] == "SELL"
    assert "flipped from" not in decision["rationale"]


# ── B2: currency labels + FX-aware consistency check ─────────────────────────

def test_fmt_fmp_labels_currency():
    assert dr._fmt_fmp(1.0237e12, "¥") == "¥1023.7B"
    assert dr._fmt_fmp(1.0237e12) == "$1023.7B"        # default stays $
    assert dr._fmt_fmp(-7.8959e10, "¥") == "-¥79.0B"


def test_currency_symbol_lookup():
    assert dr._currency_symbol("CNY") == "¥"
    assert dr._currency_symbol("USD") == "$"
    assert dr._currency_symbol(None) == "$"
    assert dr._currency_symbol("CHF") == "CHF "        # unknown → ISO prefix


_BABA_RAW = {
    "FY2024": {"revenue": 9.41e11, "net_income": 7.1e10},
    "FY2025": {"revenue": 1.0237e12, "net_income": 1.30e11},
}


@pytest.fixture
def fx_cny(monkeypatch):
    import src.tools.api as api_mod
    monkeypatch.setattr(api_mod, "get_fx_rate",
                        lambda f, t="USD", api_key=None: 0.139)


def test_c6_flags_raw_cny_claimed_as_usd(fx_cny):
    """The 08-19 defect: research quotes the raw ¥ figure with a $ label."""
    sections = {"2a": "Revenue of $1023.7B for the fiscal year."}
    flags = dr._check_research_financial_consistency(
        sections, _BABA_RAW, "BABA", reported_currency="CNY")
    assert "currency_mislabel" in flags
    f = flags["currency_mislabel"]
    assert f["reported_currency"] == "CNY"
    assert f["fx_rate"] == 0.139
    assert "revenue_magnitude" not in flags   # raw match → magnitude passes


def test_c6_accepts_correctly_converted_usd_claim(fx_cny):
    """A claim at the USD-converted value must raise NEITHER flag."""
    sections = {"2a": "Revenue of $142.3B (¥1,023.7B) for the fiscal year."}
    flags = dr._check_research_financial_consistency(
        sections, _BABA_RAW, "BABA", reported_currency="CNY")
    assert "currency_mislabel" not in flags
    assert "revenue_magnitude" not in flags


def test_c6_fx_check_off_for_usd_reporters(fx_cny):
    """USD books: the FX branch never runs (get_fx_rate would be a bug)."""
    sections = {"2a": "Revenue of $1023.7B for the fiscal year."}
    flags = dr._check_research_financial_consistency(
        sections, _BABA_RAW, "BABA")            # default reported_currency
    assert "currency_mislabel" not in flags


def test_c6_fx_unavailable_softfails(fx_cny, monkeypatch):
    """FX rate unknown (identity) → mislabel check stays off, no crash."""
    import src.tools.api as api_mod
    monkeypatch.setattr(api_mod, "get_fx_rate",
                        lambda f, t="USD", api_key=None: 1.0)
    sections = {"2a": "Revenue of $1023.7B for the fiscal year."}
    flags = dr._check_research_financial_consistency(
        sections, _BABA_RAW, "BABA", reported_currency="CNY")
    assert "currency_mislabel" not in flags


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
