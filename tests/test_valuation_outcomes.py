"""B2 outcome labels: what is scored, what is refused, and that it is idempotent."""
import json
import math
from datetime import date

import pytest


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    """One temp SQLite file for BOTH access paths. In SQLite mode run_archive
    writes to its module-level DB_PATH, not RUN_ARCHIVE_PATH -- setting only
    the env var sends ticker_signals rows into the real local archive."""
    from src.memory import run_archive as ra
    from src.memory import valuation_outcomes as vo
    path = str(tmp_path / "outcomes.db")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("RUN_ARCHIVE_PATH", path)
    monkeypatch.setattr(ra, "DB_PATH", path)
    monkeypatch.setattr(ra, "_sqlite_schema_paths", set())
    vo._tables_ready_key = None
    yield
    vo._tables_ready_key = None


from src.memory import run_archive  # noqa: E402
from src.memory import valuation_outcomes as vo  # noqa: E402

TODAY = date(2026, 9, 14)


def _dcf(iv=100.0, bear=80.0, bull=130.0, **extra):
    d = {"base": {"intrinsic_value": iv}, "bear": {"intrinsic_value": bear},
         "bull": {"intrinsic_value": bull}, "profile": "Mature SaaS",
         "routing_trace": {"winner": "ladder"}, "param_version": "constants-x",
         "is_cache_copy": False,
         "consensus_at_run": {"status": "recorded", "target": 120.0,
                              "fetched_at": "2026-01-01T10:00:00+00:00"}}
    d.update(extra)
    return d


def _add_run(run_id, ticker="ZZCO", run_at="2026-01-01T10:00:00", price=90.0,
             pt=110.0, dcf=None, sector="Tech", pm=None, current_price=None):
    """pt = the engine's scenario 12m target; pm = the PM's (defaults to pt)."""
    scenario = {"12m_price_target": pt}
    if current_price is not None:
        scenario["current_price"] = current_price
    run_archive._exec(
        "INSERT INTO runs (run_id, run_at, analysis_date, tickers, sector) "
        "VALUES (?, ?, ?, ?, ?)",
        [run_id, run_at, run_at[:10], json.dumps([ticker]), sector])
    run_archive._exec(
        "INSERT INTO ticker_signals (run_id, ticker, price_at_run, price_target, "
        "dcf_range_json, scenario_json) VALUES (?, ?, ?, ?, ?, ?)",
        [run_id, ticker, price, pm if pm is not None else pt,
         json.dumps(dcf if dcf is not None else _dcf()), json.dumps(scenario)])


def _closes(series_by_ticker):
    calls = []

    def fn(ticker, start, end):
        calls.append((ticker, start, end))
        return [(d, c) for d, c in series_by_ticker.get(ticker, [])
                if start <= d <= end]
    fn.calls = calls
    return fn


def _no_consensus(ticker):
    raise AssertionError(f"scraped consensus for {ticker}")


def _labels():
    rows = run_archive._fetch("SELECT * FROM valuation_outcomes")
    return {(r["run_id"], r["horizon"]): r for r in rows}


SERIES = {"ZZCO": [("2026-02-02", 99.0),    # first trading day on/after Jan 31
                   ("2026-04-01", 108.0),   # Apr 1 = run + 90d
                   ("2026-07-01", 125.0)]}  # Jun 30 + 1 = run + 180d window


class TestLabels:
    def test_consensus_and_matured_prices_are_labelled(self):
        _add_run("r1")
        report = vo.score_matured(today=TODAY, closes_fn=_closes(SERIES),
                                  consensus_fn=_no_consensus)
        got = _labels()
        assert set(h for _, h in got) == {"consensus_0d", "px_30d", "px_90d", "px_180d"}
        assert report["not_matured"] == 1                    # px_365d
        c = got[("r1", "consensus_0d")]
        assert c["pt_log_err"] == pytest.approx(math.log(110 / 120), abs=1e-6)
        assert c["iv_log_err"] == pytest.approx(math.log(100 / 120), abs=1e-6)
        assert c["in_band"] == 1 and c["direction_hit"] == 1
        p30 = got[("r1", "px_30d")]
        assert (p30["label_date"], p30["label_value"]) == ("2026-02-02", 99.0)
        p180 = got[("r1", "px_180d")]
        assert p180["label_value"] == 125.0 and p180["in_band"] == 1

    def test_engine_and_pm_targets_are_scored_separately(self):
        _add_run("r1", pt=110.0, pm=150.0)
        vo.score_matured(today=TODAY, closes_fn=_closes({}), consensus_fn=_no_consensus)
        c = _labels()[("r1", "consensus_0d")]
        assert c["pt_12m"] == 110.0 and c["pm_target"] == 150.0
        assert c["pt_log_err"] == pytest.approx(math.log(110 / 120), abs=1e-6)
        assert c["pm_log_err"] == pytest.approx(math.log(150 / 120), abs=1e-6)

    def test_a_run_without_an_engine_target_does_not_borrow_the_pm_one(self):
        _add_run("r1", pt=None, pm=150.0)
        vo.score_matured(today=TODAY, closes_fn=_closes({}), consensus_fn=_no_consensus)
        c = _labels()[("r1", "consensus_0d")]
        assert c["pt_12m"] is None and c["pt_log_err"] is None
        assert c["pm_target"] == 150.0

    def test_a_missing_price_at_run_falls_back_to_the_scenario_price(self):
        # without the fallback the direction check has no price to work from
        _add_run("r1", price=None, current_price=90.0)
        vo.score_matured(today=TODAY, closes_fn=_closes({}), consensus_fn=_no_consensus)
        c = _labels()[("r1", "consensus_0d")]
        assert c["price_at_run"] == 90.0 and c["direction_hit"] == 1

    def test_a_miss_is_recorded_as_a_miss(self):
        # model below the price, Street above it and outside the bear-bull band
        _add_run("r1", dcf=_dcf(iv=85.0, bear=80.0, bull=100.0))
        vo.score_matured(today=TODAY, closes_fn=_closes({}), consensus_fn=_no_consensus)
        c = _labels()[("r1", "consensus_0d")]
        assert c["in_band"] == 0 and c["direction_hit"] == 0

    def test_a_second_sweep_writes_nothing_new(self):
        _add_run("r1")
        closes = _closes(SERIES)
        vo.score_matured(today=TODAY, closes_fn=closes, consensus_fn=_no_consensus)
        again = vo.score_matured(today=TODAY, closes_fn=closes, consensus_fn=_no_consensus)
        assert again["labels"] == 0 and again["written"] == 0

    def test_write_false_reports_without_writing(self):
        _add_run("r1")
        report = vo.score_matured(today=TODAY, closes_fn=_closes(SERIES),
                                  consensus_fn=_no_consensus, write=False)
        assert report["labels"] == 4 and report["written"] == 0
        assert _labels() == {}

    def test_no_close_inside_the_window_is_left_for_later(self):
        _add_run("r1")
        gap = {"ZZCO": [("2026-02-20", 99.0)]}               # >7d after Jan 31
        report = vo.score_matured(today=TODAY, closes_fn=_closes(gap),
                                  consensus_fn=_no_consensus)
        assert ("r1", "px_30d") not in _labels()
        assert report["no_price"] >= 1

    def test_one_price_fetch_per_ticker(self):
        _add_run("r1")
        _add_run("r2", run_at="2026-02-01T10:00:00", dcf=_dcf(iv=101.0))
        closes = _closes(SERIES)
        vo.score_matured(today=TODAY, closes_fn=closes, consensus_fn=_no_consensus)
        assert len(closes.calls) == 1


class TestRefusals:
    def test_a_flagged_cache_copy_is_not_scored(self):
        _add_run("r1", dcf=_dcf(is_cache_copy=True))
        report = vo.score_matured(today=TODAY, closes_fn=_closes(SERIES),
                                  consensus_fn=_no_consensus)
        assert _labels() == {} and report["skipped_cache_copy"] == 1

    def test_a_pre_flag_copy_is_found_by_content(self):
        original = _dcf()
        original.pop("is_cache_copy")
        _add_run("r1", run_at="2026-01-01T10:00:00", dcf=original)
        _add_run("r2", run_at="2026-01-03T10:00:00",
                 dcf={**original, "_engine_cache_version": 2})
        report = vo.score_matured(today=TODAY, closes_fn=_closes(SERIES),
                                  consensus_fn=_no_consensus)
        assert {rid for rid, _ in _labels()} == {"r1"}       # the original survives
        assert report["skipped_cache_copy"] == 1

    def test_the_mis_scale_guard_still_works_without_price_at_run(self):
        _add_run("r1", price=None, current_price=90.0,
                 dcf=_dcf(consensus_at_run={"status": "recorded", "target": 900.0}))
        report = vo.score_matured(today=TODAY, closes_fn=_closes({}),
                                  consensus_fn=_no_consensus)
        assert ("r1", "consensus_0d") not in _labels()
        assert report["skipped_mis_scaled"] == 1

    def test_a_mis_scaled_label_is_refused(self):
        _add_run("r1", dcf=_dcf(consensus_at_run={"status": "recorded", "target": 900.0}))
        report = vo.score_matured(today=TODAY, closes_fn=_closes({}),
                                  consensus_fn=_no_consensus)
        assert ("r1", "consensus_0d") not in _labels()
        assert report["skipped_mis_scaled"] == 1

    def test_unavailable_consensus_is_not_invented(self):
        _add_run("r1", dcf=_dcf(consensus_at_run={"status": "unavailable"},
                                consensus_pt={"consensus": 120.0}))
        vo.score_matured(today=TODAY, closes_fn=_closes({}), consensus_fn=_no_consensus)
        assert ("r1", "consensus_0d") not in _labels()

    def test_pre_ledger_rows_use_the_consensus_fetched_during_the_run(self):
        legacy = _dcf(consensus_pt={"consensus": 115.0})
        legacy.pop("consensus_at_run")
        _add_run("r1", dcf=legacy)
        vo.score_matured(today=TODAY, closes_fn=_closes({}), consensus_fn=_no_consensus)
        assert _labels()[("r1", "consensus_0d")]["label_value"] == 115.0


class TestAsiaConsensus:
    def _hk_dcf(self):
        return _dcf(consensus_at_run={"status": "deferred"})

    def test_a_recent_hk_run_takes_todays_consensus(self):
        _add_run("r1", ticker="0700.HK", run_at="2026-09-13T10:00:00",
                 price=500.0, pt=600.0, dcf=self._hk_dcf())
        seen = []
        vo.score_matured(today=TODAY, closes_fn=_closes({}),
                         consensus_fn=lambda t: seen.append(t) or {"target": 650.0})
        assert seen == ["0700.HK"]
        row = _labels()[("r1", "consensus_0d")]
        assert row["label_value"] == 650.0 and row["market"] == "HK"

    def test_an_old_hk_run_is_not_given_todays_consensus(self):
        _add_run("r1", ticker="0700.HK", run_at="2026-08-01T10:00:00",
                 price=500.0, pt=600.0, dcf=self._hk_dcf())
        report = vo.score_matured(today=TODAY, closes_fn=_closes({}),
                                  consensus_fn=_no_consensus)
        assert ("r1", "consensus_0d") not in _labels()
        assert report["no_consensus"] == 1

    def test_a_suspect_consensus_is_refused(self):
        _add_run("r1", ticker="D05.SI", run_at="2026-09-14T01:00:00",
                 price=40.0, pt=45.0, dcf=self._hk_dcf())
        vo.score_matured(today=TODAY, closes_fn=_closes({}),
                         consensus_fn=lambda t: {"target": 44.0, "suspect": True})
        assert _labels() == {}


class TestScorecardAndGate:
    def test_blended_score_leans_on_mature_labels(self):
        _add_run("r1")
        vo.score_matured(today=TODAY, closes_fn=_closes(SERIES), consensus_fn=_no_consensus)
        card = vo.scorecard(("market",))
        us = card["groups"]["US"]
        assert us["horizons"]["consensus_0d"]["median_signed_pct"] == pytest.approx(
            (110 / 120 - 1) * 100, abs=0.1)
        errs = {"consensus_0d": math.log(110 / 120), "px_30d": math.log(110 / 99),
                "px_90d": math.log(110 / 108), "px_180d": math.log(110 / 125)}
        w = vo.BLEND_WEIGHTS
        expected = sum(w[h] * e for h, e in errs.items()) / sum(w[h] for h in errs)
        assert us["blended"]["median_signed_pct"] == pytest.approx(
            (math.exp(expected) - 1) * 100, abs=0.1)

    def test_scorecard_refuses_unknown_group_columns(self):
        with pytest.raises(ValueError):
            vo.scorecard(("ticker; DROP TABLE runs",))

    def test_sweep_gate_is_per_day(self):
        assert vo.swept_today(TODAY) is False
        vo.mark_swept({"labels": 0}, TODAY)
        assert vo.swept_today(TODAY) is True
        assert vo.swept_today(date(2026, 9, 15)) is False


class TestActionScoring:
    def test_pending_actions_older_than_30_days_are_scored(self, monkeypatch):
        monkeypatch.delenv("ACTION_OUTCOME_SCORING", raising=False)
        _add_run("r1", run_at="2026-07-01T10:00:00")
        run_archive._exec("UPDATE ticker_signals SET final_action = 'BUY' WHERE run_id = 'r1'")
        out = vo.score_actions(today=TODAY,
                               closes_fn=_closes({"ZZCO": [("2026-09-11", 120.0)]}))
        assert out["rows_scored"] == 1
        row = run_archive._fetch("SELECT outcome FROM ticker_signals WHERE run_id = 'r1'")[0]
        assert row["outcome"] == "CORRECT"                  # 90 -> 120, BUY

    def test_kill_switch(self, monkeypatch):
        monkeypatch.setenv("ACTION_OUTCOME_SCORING", "false")
        assert vo.score_actions(today=TODAY) == {"enabled": False}
