"""The PM under the research rating: the 12-month TSR decides, the target is
never an intrinsic value, and every divergence is disclosed.

Repro: a 09988.HK run published a HK$288 target -- the bull-case intrinsic
value swapped in by the BUY guard -- beside a HK$89.57 12-month target, so the
headline and the target card disagreed with no explanation.
"""
from datetime import date, timedelta

import pytest

import src.utils.llm as llm_mod
from src.agents.portfolio_manager import _quant_block_text, run_advanced_portfolio_manager

PROMPTS: list[str] = []


def _fake_call_llm(prompt=None, pydantic_model=None, **kwargs):
    PROMPTS.append(prompt.to_string() if hasattr(prompt, "to_string") else str(prompt))
    return pydantic_model(
        action="HOLD", position_size_pct=0.01, entry_range=[100.0, 110.0],
        stop_loss=95.0, price_target=120.0, time_horizon="medium",
        rationale="1. Rationale citing TSR -15% vs HSTECH 9.9%.",
    )


@pytest.fixture(autouse=True)
def pm_llm(monkeypatch):
    PROMPTS.clear()
    monkeypatch.setattr(llm_mod, "call_llm", _fake_call_llm)


def _run(ticker="TEST", *, spot=100.0, pt=None, iv=None, bull=None, sector="Tech",
         dcf=None, **extra):
    data = {
        "tickers": [ticker], "sector": sector, "end_date": "2026-09-11",
        "next_earnings": {},
        "analyst_signals": {"advanced_risk_manager": {ticker: {"approved_size_pct": 0.1}}},
        "scenario_analysis": {ticker: {
            "current_price": spot, "expected_value": iv or spot, "upside_pct": 0.0,
            "12m_price_target": pt,
            "bear": {"fair_value": spot * 0.7}, "bull": {"fair_value": bull or spot * 1.5},
            "reconciliation": ({"blended_iv": iv,
                                "upside_to_iv_pct": (iv - spot) / spot * 100}
                               if iv else {}),
        }},
        "power_law_analysis": {ticker: {"total_score": 6}},
        "value_trap_analysis": {ticker: {"overall_verdict": "TRAP RISK LOW"}},
        "dcf_range": {ticker: dcf or {}},
    }
    data.update(extra)
    state = {"messages": [], "data": data}
    return run_advanced_portfolio_manager(state)["decisions"][ticker], state


def test_the_9988_target_is_the_12_month_target_not_the_bull_iv():
    d, _ = _run("09988.HK", spot=105.90, pt=89.57, iv=189.33, bull=288.0,
                dcf={"reported_currency": "HKD", "12m_pt_method": "forward EV/EBITDA"})
    assert d["price_target"] == pytest.approx(89.57)
    assert d["research_rating"] == "UNDERWEIGHT" and d["action"] == "SELL"
    v = d["research_view"]
    assert v["benchmark"]["code"] == "HSTECH"
    assert v["structural_rating"] == "OVERWEIGHT"
    assert v["compliance"]["status"] == "explained_divergence"
    assert "forward EV/EBITDA" in v["compliance"]["notes"][0]
    assert "Underweight vs Hang Seng Tech Index" in PROMPTS[0]
    assert "Disclosure:" in PROMPTS[0]


def test_a_dividend_carries_a_below_spot_target_to_overweight():
    d, _ = _run("D05.SI", sector="Financials", spot=100.0, pt=96.0, iv=120.0,
                dcf={"dividends_per_share": 20.0})
    assert d["action"] == "BUY" and d["research_rating"] == "OVERWEIGHT"
    assert d["price_target"] == pytest.approx(96.0)       # no bull-IV swap
    assert d["research_view"]["tsr_12m"] == pytest.approx(0.16)


def test_value_trap_caps_overweight_and_the_view_agrees_with_the_trade():
    d, _ = _run(spot=100.0, pt=130.0, iv=130.0,
                value_trap_analysis={"TEST": {"overall_verdict": "TRAP RISK HIGH"}})
    assert d["action"] == "HOLD"
    assert d["research_rating"] == "NEUTRAL" and d["rating_label"] == "Neutral"
    assert d["research_view"]["tactical_rating"] == "OVERWEIGHT"   # arithmetic kept
    assert any("value-trap gate" in n for n in d["research_view"]["compliance"]["notes"])


def test_stale_research_caps_overweight():
    d, _ = _run(spot=100.0, pt=130.0, iv=130.0, research_tier="knowledge_only")
    assert d["action"] == "HOLD" and d["research_rating"] == "NEUTRAL"


def test_earnings_inside_the_window_puts_a_divergent_rating_under_review():
    soon = (date.today() + timedelta(days=7)).isoformat()
    d, _ = _run(spot=100.0, pt=90.0, iv=150.0, next_earnings={"TEST": soon},
                dcf={"12m_pt_method": "fwd P/E"})
    assert d["rating_label"] == "Under Review"
    assert d["action"] == "HOLD" and d["price_target"] == pytest.approx(90.0)


def test_material_news_is_the_catalyst_not_a_rating_shift():
    delta = {"material": True, "verdict": "material adverse",
             "events": [{"headline": "Guidance cut after probe"}]}
    d, _ = _run(spot=100.0, pt=110.0, iv=150.0, freshness_delta={"TEST": delta})
    assert d["action"] == "HOLD"                          # TSR +10% → Neutral, no shift
    assert "Guidance cut" in d["research_view"]["compliance"]["notes"][0]


def test_a_far_sotp_value_is_disclosed():
    d, _ = _run(spot=100.0, pt=130.0, iv=150.0,
                dcf={"sotp_breakdown": {"per_share_reporting": 60.0}})
    assert d["research_view"]["compliance"]["status"] == "holdco_disclosure"


def test_no_target_keeps_the_legacy_path():
    d, _ = _run(spot=100.0, pt=None, iv=130.0)
    assert d["research_view"] is None and d["research_rating"] is None
    assert d["decision_inputs"]["quantitative"]["rating_basis"] == "intrinsic_value_band"
    assert "Research rating: not available" in PROMPTS[0]


def test_prose_currency_is_the_listing_currency_not_the_statement_currency():
    state = {"data": {"reported_currency": "CNY",
                      "dcf_range": {"09988.HK": {"reported_currency": "HKD"}}}}
    block = _quant_block_text("09988.HK", state, {"current_price": 105.9,
                                                  "12m_price_target": 89.57})
    assert "HK$89.57" in block and "RMB" not in block


# ── earnings calendar ─────────────────────────────────────────────────────

class _Resp:
    status_code = 200

    def __init__(self, rows):
        self._rows = rows

    def json(self):
        return self._rows


def test_no_key_means_no_call_and_no_date(monkeypatch):
    import requests
    from src.tools import earnings_calendar as ec
    monkeypatch.delenv("FMP_API_KEY", raising=False)
    monkeypatch.setattr(requests, "get", lambda *a, **k: pytest.fail("called"))
    assert ec.next_earnings_date("AAPL") is None


def test_calendar_returns_the_earliest_date_and_maps_hk_symbols(monkeypatch):
    import requests
    from src.tools import earnings_calendar as ec
    calls = []
    monkeypatch.setenv("FMP_API_KEY", "k")
    monkeypatch.setattr(requests, "get", lambda *a, **k: calls.append(1) or _Resp([
        {"symbol": "9988.HK", "date": "2026-09-30"},
        {"symbol": "9988.HK", "date": "2026-09-20"},
        {"symbol": "AAPL", "date": "2026-10-01"},
    ]))
    today = date(2026, 9, 14)
    assert ec.next_earnings_date("09988.HK", today=today) == "2026-09-20"
    assert ec.next_earnings_date("AAPL", today=today) == "2026-10-01"
    assert ec.next_earnings_date("MSFT", today=today) is None
    assert len(calls) == 1                                # one fetch per window
