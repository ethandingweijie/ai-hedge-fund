"""T-1 backward gate: benchmark dated to the financials it values, a forward
score, a structured record, and every projecting leg bucketed as DCF.

Production evidence (2026-09-19): the gate priced LULU's FY2025 financials
(ended 2025-02-02, $414.20) against $169.62 on 2025-09-19 and published a
170% "calibration error"; across 61 scored runs the run-date window mis-stated
16 verdicts in both directions.
"""
from datetime import datetime, timedelta

import pytest

from src.agents.analysis import dcf_agent as d
from src.data.models import Price


def _px(day: str, close: float) -> Price:
    return Price(open=close, close=close, high=close, low=close, volume=1, time=day)


def _daily(start: str, end: str, fn) -> list[Price]:
    out, t = [], datetime.strptime(start, "%Y-%m-%d")
    stop = datetime.strptime(end, "%Y-%m-%d")
    while t <= stop:
        if t.weekday() < 5:
            out.append(_px(t.strftime("%Y-%m-%d"), fn(t)))
        t += timedelta(days=1)
    return out


def _series(periods=("2023-02-01", "2025-02-02", "2026-02-01")) -> list[dict]:
    return [{"period": p, "revenue": 10_000.0, "free_cash_flow": 1_500.0,
             "shares_outstanding": 100.0, "net_debt": 0.0, "net_income": 1_200.0}
            for p in periods]


@pytest.fixture
def fake_prices(monkeypatch):
    calls = []
    # $400 up to FY2025's end, $100 afterwards: the LULU shape.
    series = _daily("2024-01-01", "2026-09-30",
                    lambda t: 400.0 if t <= datetime(2025, 2, 2) else 100.0)

    def _get(ticker, start, end, api_key=None):
        calls.append((start, end))
        return [p for p in series if start <= p.time <= end]

    monkeypatch.setattr(d, "get_prices", _get)
    return calls


def _gate(end_date="2026-09-19", series=None, **kw):
    return d._run_backward_gate(
        ticker="LULU", series=series or _series(), sector="Consumer",
        end_date=end_date, wacc=0.09, tgr=0.025, fcf_floor=-0.05,
        api_key="x", **kw)


def test_benchmark_is_the_t1_fiscal_period_end_not_run_date_minus_a_year(fake_prices):
    _err, note, rec = _gate()
    assert rec["benchmark_basis"] == "fiscal_period_end"
    assert rec["t1_period"] == "2025-02-02"
    # 2025-02-02 is a Sunday: the last close on or before it is Friday's.
    assert rec["t1_price_date"] == "2025-01-31"
    assert rec["t1_price"] == 400.0
    assert "on 2025-01-31" in note
    # One fetch from the lookback start to the forward date.
    assert fake_prices == [("2025-01-18", "2026-02-02")]


def test_forward_score_compares_model_and_market_against_the_later_close(fake_prices):
    _err, _note, rec = _gate()
    fwd = rec["forward"]
    assert fwd["price"] == 100.0 and fwd["price_date"] == "2026-02-02"
    assert fwd["market_error_pct"] == pytest.approx(3.0)            # 400 vs 100
    assert fwd["model_error_pct"] == pytest.approx(abs(rec["t1_iv"] - 100) / 100)
    expected = ("MODEL_CLOSER" if fwd["model_error_pct"] < fwd["market_error_pct"] - 0.05
                else "MARKET_CLOSER" if fwd["model_error_pct"] > fwd["market_error_pct"] + 0.05
                else "TIE")
    assert fwd["verdict"] == expected


def test_forward_score_waits_until_the_horizon_has_passed(fake_prices):
    _err, _note, rec = _gate(end_date="2025-12-01")
    assert rec["forward"] == {"verdict": "NOT_MATURED", "matures_on": "2026-02-02"}
    assert fake_prices == [("2025-01-18", "2025-12-01")]


def test_flag_verdict_is_unchanged_by_the_forward_score(fake_prices):
    err, note, rec = _gate()
    assert rec["status"] == ("fired" if err else "passed")
    assert (rec["error_pct"] > d._CALIBRATION_TOLERANCE) is err
    assert note.startswith("Calibration Error" if err else "T-1 passed")


def test_unparseable_period_falls_back_to_run_date_window(fake_prices):
    s = _series()
    s[-2]["period"] = "FY2025"
    _err, _note, rec = _gate(series=s)
    assert rec["benchmark_basis"] == "run_date_minus_365"
    assert rec["t1_period"] == "2025-09-19"


def test_non_positive_iv_skip_keeps_the_t1_iv(fake_prices):
    s = _series()
    for r in s:
        r["free_cash_flow"] = -2_000.0
        r["net_debt"] = 50_000.0
    err, note, rec = _gate(series=s)
    assert err is False and note == "Skipped — T-1 model returned non-positive IV"
    assert rec["status"] == "skipped" and rec["t1_iv"] <= 0


def test_short_history_returns_a_record(fake_prices):
    err, note, rec = _gate(series=_series()[:2])
    assert (err, rec["status"]) == (False, "skipped")
    assert rec["skip_reason"] == "insufficient history for T-1 test"
    assert fake_prices == []


def test_close_on_or_before_respects_the_lookback():
    prices = [_px("2025-01-01", 5.0), _px("2025-01-20", 7.0), _px("2025-02-05", 9.0)]
    assert d._close_on_or_before(prices, datetime(2025, 2, 2)) == (7.0, "2025-01-20")
    assert d._close_on_or_before(prices, datetime(2025, 1, 10)) == (5.0, "2025-01-01")
    assert d._close_on_or_before(prices, datetime(2024, 12, 1)) is None


# ── Every projecting leg is sentiment-free ─────────────────────────────────

@pytest.mark.parametrize("leg", ["DCF (LTG)", "DCF (5-yr)", "Rev DCF (GMV)", "Rev DCF"])
def test_projecting_legs_sit_in_the_dcf_bucket(leg):
    iv, bd = d._blend_methods(
        profile_methods=[{"name": "P/E", "weight": 0.8, "anchor": True, "implementable": True},
                         {"name": leg, "weight": 0.2, "anchor": False, "implementable": True}],
        method_values={"P/E": 100.0, leg: 50.0},
        c_macro=0.0, forward_flags=[], dcf_tv_fraction=0.5)
    buckets = {w["method"]: w["bucket"] for w in bd["effective_weights"]}
    assert buckets == {"P/E": "multi", leg: "dcf"}
    assert bd["iv_dcf"] == pytest.approx(50.0)
    assert iv == pytest.approx(0.8 * 100.0 + 0.2 * 50.0)


def test_projection_family_and_blend_family_cannot_drift():
    assert d._DCF_PROJECTION_FAMILY == d._DCF_FAMILY_NAMES


# ── DCF-family legs project with the live context ──────────────────────────

def _cmv(name, projection=None, growth=0.30):
    return d._compute_method_value(
        method_name=name, most_recent={}, revenue_base=1_000.0, shares=10.0,
        net_debt=0.0, market_cap=10_000.0, wacc=0.10, growth_base=growth,
        fcf_margin_base=0.20, tgr=0.03, fcf_floor=-0.05, sector="Tech",
        scenario="base", projection=projection)


def test_every_dcf_family_leg_equals_the_core_projection_with_the_same_context():
    sched = d._decayed_growth_schedule(0.30, "Hyper-Growth Platform")
    ctx = {"growth_schedule": sched, "wacc_schedule": None, "margin_delta_absolute": -0.02}
    core = d._project_dcf(1_000.0, 0.20, 0.30, 0.0, 0.10, 0.03, -0.05, 0.0, 10.0,
                          growth_schedule=sched, margin_delta_absolute=-0.02)[0]
    for leg in ("DCF (FCF+)", "NRR-adj DCF", "DCF (LTG)", "DCF (5-yr)"):
        assert _cmv(leg, ctx) == pytest.approx(core), leg
    # Without the context a leg projects growth flat -- the old defect.
    assert _cmv("DCF (FCF+)") > _cmv("DCF (FCF+)", ctx)


def test_the_backtest_uses_the_live_margin_basis_and_growth_schedule(fake_prices):
    # Owner earnings need SBC in >=3 of the years known at T-1.
    s = _series(("2021-02-01", "2022-02-01", "2023-02-01", "2025-02-02", "2026-02-01"))
    for r in s:
        r["stock_based_compensation"] = 300.0
        r["fcf_owner_earnings"] = r["free_cash_flow"] - 300.0
    _e, _n, rec = _gate(series=s, profile_name="Hyper-Growth Platform")
    assert rec["fcf_margin_basis"] == "fcf_owner_earnings"
    assert rec["fcf_margin_t1"] == pytest.approx(0.12)
    assert rec["growth_schedule"] == "decay"
