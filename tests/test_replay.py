"""
tests/test_replay.py
=====================
P2 gates — crisis-replay engine on synthetic price fixtures (no network).

Backward-gate semantics baked in:
  • determinism — identical inputs → byte-identical JSON
  • coverage guard — young/unlisted names excluded, weights renormalize
"""
import json
from datetime import date, timedelta

import pytest

from src.portfolio import replay as rp
from src.portfolio.event_library import EVENTS, EventSpec, MacroSnapshot


# ── Synthetic fixtures ───────────────────────────────────────────────────────

def _weekdays(start: str, n: int) -> list[str]:
    y, m, d = (int(x) for x in start.split("-"))
    cur = date(y, m, d)
    out: list[str] = []
    while len(out) < n:
        if cur.weekday() < 5:
            out.append(cur.isoformat())
        cur += timedelta(days=1)
    return out


WINDOW_START, WINDOW_END = "2020-01-01", "2020-01-31"
DATES = _weekdays(WINDOW_START, 22)   # 22 trading days inside the window


class _Pt:
    """Duck-typed Price: the engine only reads .time / .close."""
    def __init__(self, time: str, close: float):
        self.time = time
        self.close = close


def _linear(start_v: float, end_v: float) -> list[float]:
    n = len(DATES) - 1
    return [start_v + (end_v - start_v) * i / n for i in range(len(DATES))]


def _fake_fetcher(table: dict[str, list[_Pt]]):
    def fetch(ticker, start, end):
        return [p for p in table.get(ticker, []) if start <= p.time <= end]
    return fetch


@pytest.fixture()
def base_table():
    """SPY −10% linear, QQQ −15% linear, A −20% linear, B +10% linear,
    YOUNG listed mid-window (excluded by coverage guard)."""
    return {
        "SPY":  [_Pt(d, c) for d, c in zip(DATES, _linear(100, 90))],
        "QQQ":  [_Pt(d, c) for d, c in zip(DATES, _linear(100, 85))],
        "A":    [_Pt(d, c) for d, c in zip(DATES, _linear(100, 80))],
        "B":    [_Pt(d, c) for d, c in zip(DATES, _linear(100, 110))],
        # YOUNG: first price 2020-01-20 > window start + 14-day grace
        "YOUNG": [_Pt(d, 50.0) for d in DATES if d >= "2020-01-20"],
    }


def _test_event(**kw) -> EventSpec:
    base = dict(
        key="synthetic", name="Synthetic stress",
        start=WINDOW_START, end=WINDOW_END,
        spy_return_pct=-10.0, spy_max_dd_pct=-10.0,
        qqq_return_pct=-15.0, qqq_max_dd_pct=-15.0,
        macro=MacroSnapshot("risk-off", "hiking", "rising", "high", "medium"),
    )
    base.update(kw)
    return EventSpec(**base)


HOLDINGS = [
    {"ticker": "A", "quantity": 1.0, "avg_cost": 100.0},
    {"ticker": "B", "quantity": 1.0, "avg_cost": 100.0},
]


# ── Primitives ───────────────────────────────────────────────────────────────

class TestPrimitives:
    def test_window_return(self):
        assert rp._window_return_pct([100, 80]) == pytest.approx(-20.0)
        assert rp._window_return_pct([100]) is None

    def test_max_dd(self):
        # rise then fall: 100 → 200 → 100 = −50% from peak
        assert rp._max_dd_pct([100, 200, 100]) == pytest.approx(-50.0)
        # monotonic rise: no drawdown
        assert rp._max_dd_pct([100, 110, 120]) == pytest.approx(0.0)

    def test_beta_doubled_series(self):
        # Benchmark alternates −1% / flat days (non-zero variance); the asset
        # applies the squared rate each day → its return is ~2× on every
        # down day → beta ≈ 2.
        spy_closes, a_closes = [100.0], [100.0]
        for i in range(len(DATES) - 1):
            r = 0.99 if i % 2 == 0 else 1.0
            spy_closes.append(spy_closes[-1] * r)
            a_closes.append(a_closes[-1] * r * r)
        sd, sr = rp._daily_returns(DATES, spy_closes)
        ad, ar = rp._daily_returns(DATES, a_closes)
        ra, rb = rp._align_returns(ad, ar, sd, sr)
        assert rp._beta_pct_series(ra, rb) == pytest.approx(2.0, abs=0.05)


# ── Event replay ─────────────────────────────────────────────────────────────

class TestEventReplay:
    def test_returns_dd_and_weights(self, base_table):
        out = rp.replay_portfolio(HOLDINGS, events=[_test_event()],
                                  price_fetcher=_fake_fetcher(base_table))
        ev = out["events"][0]
        by_tkr = {r["ticker"]: r for r in ev["holdings"]}
        assert by_tkr["A"]["window_return_pct"] == pytest.approx(-20.0)
        assert by_tkr["A"]["max_dd_pct"] == pytest.approx(-20.0)
        assert by_tkr["B"]["window_return_pct"] == pytest.approx(10.0)
        assert by_tkr["B"]["max_dd_pct"] == pytest.approx(0.0)
        # 50/50 cost-basis weights; portfolio = mean of the two normalized
        # linear paths = 100 − 5t → −5% return, −5% DD
        assert ev["portfolio"]["window_return_pct"] == pytest.approx(-5.0)
        assert ev["portfolio"]["max_dd_pct"] == pytest.approx(-5.0)
        assert ev["portfolio"]["covered_weight_pct"] == pytest.approx(100.0)

    def test_benchmark_cross_check_ok_and_divergent(self, base_table):
        ev_ok = rp.replay_portfolio(HOLDINGS, events=[_test_event()],
                                    price_fetcher=_fake_fetcher(base_table))["events"][0]
        assert ev_ok["benchmarks"]["cross_check"] == "ok"
        assert ev_ok["benchmarks"]["live"]["spy_return_pct"] == pytest.approx(-10.0)

        bad = _test_event(qqq_return_pct=+25.0)   # curated wildly off live −15%
        ev_bad = rp.replay_portfolio(HOLDINGS, events=[bad],
                                     price_fetcher=_fake_fetcher(base_table))["events"][0]
        assert ev_bad["benchmarks"]["cross_check"] == "divergent"
        assert "qqq_return_pct" in ev_bad["benchmarks"]["divergent_keys"]

    def test_coverage_guard_excludes_young_and_renormalizes(self, base_table):
        holdings = HOLDINGS + [{"ticker": "YOUNG", "quantity": 1.0, "avg_cost": 50.0}]
        ev = rp.replay_portfolio(holdings, events=[_test_event()],
                                 price_fetcher=_fake_fetcher(base_table))["events"][0]
        assert "YOUNG" in ev["excluded"]
        young = next(r for r in ev["holdings"] if r["ticker"] == "YOUNG")
        assert young["covered"] is False and young["window_return_pct"] is None
        # Weights renormalize onto A/B → portfolio numbers unchanged
        assert ev["portfolio"]["window_return_pct"] == pytest.approx(-5.0)
        # covered weight = 200/250 of cost basis
        assert ev["portfolio"]["covered_weight_pct"] == pytest.approx(80.0)

    def test_unlisted_ticker_no_crash(self, base_table):
        holdings = [{"ticker": "GHOST", "quantity": 5.0, "avg_cost": 10.0}]
        ev = rp.replay_portfolio(holdings, events=[_test_event()],
                                 price_fetcher=_fake_fetcher(base_table))["events"][0]
        assert ev["excluded"] == ["GHOST"]
        assert ev["portfolio"]["window_return_pct"] is None

    def test_regime_similarity_counts_matches(self, base_table):
        today = {"risk_appetite": "risk-off", "rate_direction": "hiking",
                 "dollar_trend": "rising", "volatility_regime": "low",
                 "recession_risk": "low"}
        out = rp.replay_portfolio(HOLDINGS, events=[_test_event()],
                                  price_fetcher=_fake_fetcher(base_table),
                                  today_regime=today)
        sim = out["events"][0]["regime_similarity"]
        assert sim["matches"] == 3          # appetite, rates, dollar
        assert set(sim["matched_dims"]) == {"risk_appetite", "rate_direction", "dollar_trend"}

    def test_similarity_sort_orders_events(self, base_table):
        close = _test_event(key="close_event")                 # matches all 5 dims
        far = _test_event(key="far_event",
                          macro=MacroSnapshot("risk-on", "cutting", "falling",
                                              "low", "low"))   # matches zero dims
        today = {"risk_appetite": "risk-off", "rate_direction": "hiking",
                 "dollar_trend": "rising", "volatility_regime": "high",
                 "recession_risk": "medium"}                   # close: 5 matches
        out = rp.replay_portfolio(HOLDINGS, events=[far, close],
                                  price_fetcher=_fake_fetcher(base_table),
                                  today_regime=today)
        assert out["events"][0]["key"] == "close_event"
        assert out["events"][0]["regime_similarity"]["matches"] == 5


# ── Determinism (backward gate) ──────────────────────────────────────────────

class TestDeterminism:
    def test_byte_identical_repeat(self, base_table):
        kwargs = dict(events=[_test_event()], price_fetcher=_fake_fetcher(base_table),
                      today_regime={"risk_appetite": "risk-on"})
        a = json.dumps(rp.replay_portfolio(HOLDINGS, **kwargs), sort_keys=True)
        b = json.dumps(rp.replay_portfolio(HOLDINGS, **kwargs), sort_keys=True)
        assert a == b

    def test_snapshot_hash_stable_and_sensitive(self):
        h1 = [{"ticker": "A", "quantity": 1, "avg_cost": 100},
              {"ticker": "B", "quantity": 2, "avg_cost": 50}]
        h2 = list(reversed(h1))                       # order-insensitive
        h3 = [{"ticker": "A", "quantity": 1.0000001, "avg_cost": 100},
              {"ticker": "B", "quantity": 2, "avg_cost": 50}]
        assert rp.snapshot_hash(h1) == rp.snapshot_hash(h2)
        assert rp.snapshot_hash(h1) == rp.snapshot_hash(h3)  # rounds at 6dp
        h4 = [{"ticker": "A", "quantity": 2, "avg_cost": 100},
              {"ticker": "B", "quantity": 2, "avg_cost": 50}]
        assert rp.snapshot_hash(h1) != rp.snapshot_hash(h4)


# ── Event library sanity ─────────────────────────────────────────────────────

class TestEventLibrary:
    def test_six_events_windows_ordered(self):
        assert len(EVENTS) == 6
        keys = [e.key for e in EVENTS]
        assert len(set(keys)) == 6
        for e in EVENTS:
            assert e.start < e.end
            assert -100 < e.spy_return_pct < 100
            assert e.spy_max_dd_pct <= 0
            assert e.macro.risk_appetite in ("risk-on", "mixed", "risk-off")

    def test_expected_events_present(self):
        keys = {e.key for e in EVENTS}
        assert {"gfc_2008", "covid_2020", "rate_shock_2022",
                "svb_2023", "q4_2018", "euro_2011"} <= keys


# ── Replay store (dual-mode cache table) ────────────────────────────────────

class TestReplayStore:
    def test_roundtrip_and_user_scope(self, monkeypatch, tmp_path):
        # conftest strips DATABASE_URL → sqlite mode; point at a tmp store
        monkeypatch.setenv("RUN_ARCHIVE_PATH", str(tmp_path / "replay.db"))
        from app.backend.services import portfolio_service as ps

        result = {"event_count": 6, "events": []}
        ps.save_replay(None, "hashA", result)
        assert ps.get_cached_replay(None, "hashA") == result
        # user 7 sees no anon rows (scope isolation)
        assert ps.get_cached_replay(7, "hashA") is None

        ps.save_replay(7, "hashA", {"event_count": 2})
        assert ps.get_cached_replay(7, "hashA") == {"event_count": 2}
        assert ps.get_cached_replay(None, "hashA") == result  # anon intact

        assert ps.get_cached_replay(None, "other-hash") is None

    def test_env_knob(self, monkeypatch):
        from app.backend.services import portfolio_service as ps
        monkeypatch.setenv("PORTFOLIO_REPLAY", "false")
        assert ps.replay_enabled() is False
        monkeypatch.setenv("PORTFOLIO_REPLAY", "true")
        assert ps.replay_enabled() is True
        monkeypatch.delenv("PORTFOLIO_REPLAY", raising=False)
        assert ps.replay_enabled() is True   # default on

    def test_replay_job_user_scope(self, monkeypatch, tmp_path):
        # Regression: replay jobs are PERSONAL — authenticated polls must
        # scope on user_id. The shared get_job() dict omits user_id by
        # contract (shared research jobs stay globally visible), so
        # get_replay_job reads the column directly; without that, prod
        # polls 404'd (None != real-user-id).
        monkeypatch.setenv("RUN_ARCHIVE_PATH", str(tmp_path / "jobs.db"))
        from app.backend.services import complacency_job_store as job_store
        from app.backend.services import portfolio_service as ps

        job_id = job_store.create_job("portfolio_replay", ticker=None, user_id=7)
        assert ps.get_replay_job(job_id, 7) is not None
        assert ps.get_replay_job(job_id, None) is None   # anon cannot see it
        assert ps.get_replay_job(job_id, 8) is None      # other user cannot
        # A different job kind stays invisible even with matching user_id
        other = job_store.create_job("refresh", ticker=None, user_id=7)
        assert ps.get_replay_job(other, 7) is None
