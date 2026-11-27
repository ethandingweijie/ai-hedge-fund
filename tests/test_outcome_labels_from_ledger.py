"""Outcome labels read the prediction from the run_features ledger (2026-11-27).

Production lost its Sep-Oct archive rows (runs / ticker_signals) while run_features kept
them; a sweep that read only the archive could never give those runs a 90/180/365-day
label. These pin: ledger-only runs are labelled, one label per ticker per day, a stopped
name gets a terminal record, and the backfill script is a dry run by default.
"""
import json
from datetime import date

import pytest


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    from src.memory import run_archive as ra
    from src.memory import run_features as rf
    from src.memory import valuation_outcomes as vo
    path = str(tmp_path / "ledger_labels.db")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("RUN_FEATURES_DISABLED", raising=False)
    monkeypatch.setenv("RUN_ARCHIVE_PATH", path)
    monkeypatch.setattr(ra, "DB_PATH", path)
    monkeypatch.setattr(ra, "_sqlite_schema_paths", set())
    vo._tables_ready_key = None
    rf._tables_ready_key = None
    yield
    vo._tables_ready_key = None
    rf._tables_ready_key = None


from src.memory import run_archive  # noqa: E402
from src.memory import run_features as rf  # noqa: E402
from src.memory import valuation_outcomes as vo  # noqa: E402

TODAY = date(2026, 9, 14)


def _dr(iv=100.0, **extra):
    d = {"base": {"intrinsic_value": iv}, "bear": {"intrinsic_value": 80.0},
         "bull": {"intrinsic_value": 130.0}, "profile": "Mature SaaS",
         "routing_trace": {"winner": "ladder"}, "param_version": "constants-x",
         "pt_bridge": {"spot": 90.0},
         "consensus_at_run": {"status": "recorded", "target": 120.0,
                              "fetched_at": "2026-01-01T10:00:00+00:00"}}
    d.update(extra)
    return d


def _ledger(run_id, ticker="ZZCO", run_at="2026-01-01T10:00:00", pt=110.0, pm=None, dr=None,
            **flags):
    rf._ensure_tables()
    row = rf.extract(run_id, ticker, dr if dr is not None else _dr(), {"12m_price_target": pt},
                     run_at=run_at, sector="Tech", price_at_run=90.0,
                     pm_target=pm if pm is not None else pt)
    row.update(flags)
    rf._write([row])


def _archive(run_id, ticker="ZZCO", run_at="2026-01-01T10:00:00", pt=110.0, dr=None):
    run_archive._exec(
        "INSERT INTO runs (run_id, run_at, analysis_date, tickers, sector) VALUES (?, ?, ?, ?, ?)",
        [run_id, run_at, run_at[:10], json.dumps([ticker]), "Tech"])
    run_archive._exec(
        "INSERT INTO ticker_signals (run_id, ticker, price_at_run, price_target, "
        "dcf_range_json, scenario_json) VALUES (?, ?, ?, ?, ?, ?)",
        [run_id, ticker, 90.0, pt, json.dumps(dr if dr is not None else _dr()),
         json.dumps({"12m_price_target": pt})])


def _closes(series):
    calls = []

    def fn(ticker, start, end):
        calls.append((ticker, start, end))
        return [(d, c) for d, c in series.get(ticker, []) if start <= d <= end]
    fn.calls = calls
    return fn


def _no_consensus(ticker):
    raise AssertionError(f"scraped consensus for {ticker}")


def _labels():
    return {(r["run_id"], r["horizon"]): r
            for r in run_archive._fetch("SELECT * FROM valuation_outcomes")}


SERIES = {"ZZCO": [("2026-02-02", 99.0), ("2026-04-01", 108.0), ("2026-07-01", 125.0),
                   ("2026-09-11", 130.0)],
          "AACO": [("2026-02-02", 50.0), ("2026-04-01", 51.0), ("2026-07-01", 52.0),
                   ("2026-09-11", 53.0)]}


class TestLedgerIsTheSource:
    def test_a_run_whose_archive_rows_are_gone_is_still_labelled(self):
        _ledger("r1")                                       # no runs / ticker_signals rows
        report = vo.score_matured(today=TODAY, closes_fn=_closes(SERIES),
                                  consensus_fn=_no_consensus)
        got = _labels()
        assert {h for _, h in got} == {"px_30d", "px_90d", "px_180d"}
        assert report["ledger_only"] == 1
        p90 = got[("r1", "px_90d")]
        assert p90["label_value"] == 108.0 and p90["pt_12m"] == 110.0
        assert p90["base_iv"] == 100.0 and p90["price_at_run"] == 90.0
        assert p90["in_band"] == 1 and p90["profile"] == "Mature SaaS"
        # consensus at run lives only in the archive blob: not invented from the ledger
        assert ("r1", "consensus_0d") not in got and report["no_consensus"] == 1

    def test_a_run_in_both_takes_the_ledger_figures_and_the_archive_consensus(self):
        _archive("r1", pt=110.0)
        _ledger("r1", pt=110.0)
        vo.score_matured(today=TODAY, closes_fn=_closes(SERIES), consensus_fn=_no_consensus)
        got = _labels()
        assert got[("r1", "consensus_0d")]["label_value"] == 120.0
        assert got[("r1", "px_30d")]["label_value"] == 99.0

    def test_ledger_exclusions_are_honoured(self):
        _ledger("r1", ticker="ZZCO", override_carried=1)
        _ledger("r2", ticker="AACO", unrated=1)
        _ledger("r3", ticker="ZZCO", run_at="2026-01-05T10:00:00", cache_copy=1)
        report = vo.score_matured(today=TODAY, closes_fn=_closes(SERIES),
                                  consensus_fn=_no_consensus)
        assert _labels() == {}
        assert (report["skipped_override_carried"], report["skipped_unrated"],
                report["skipped_cache_copy"]) == (1, 1, 1)

    def test_an_archive_content_copy_is_not_rescued_by_the_ledger(self):
        # the save-time ledger row cannot know it is a replay; the archive hash does
        _archive("r1", run_at="2026-01-01T10:00:00")
        _archive("r2", run_at="2026-01-03T10:00:00")       # identical dcf_range -> copy
        _ledger("r1", run_at="2026-01-01T10:00:00")
        _ledger("r2", run_at="2026-01-03T10:00:00")
        vo.score_matured(today=TODAY, closes_fn=_closes(SERIES), consensus_fn=_no_consensus)
        assert {rid for rid, _ in _labels()} == {"r1"}

    def test_idempotent(self):
        _ledger("r1")
        closes = _closes(SERIES)
        vo.score_matured(today=TODAY, closes_fn=closes, consensus_fn=_no_consensus)
        again = vo.score_matured(today=TODAY, closes_fn=closes, consensus_fn=_no_consensus)
        assert again["labels"] == 0 and again["written"] == 0 and again["terminal"] == 0


class TestOneLabelPerTickerPerDay:
    def test_the_days_latest_run_is_the_prediction(self):
        _ledger("early", run_at="2026-01-01T09:00:00", dr=_dr(iv=95.0))
        _ledger("late", run_at="2026-01-01T15:00:00", dr=_dr(iv=105.0))
        _ledger("next_day", run_at="2026-01-02T09:00:00", dr=_dr(iv=101.0))
        report = vo.score_matured(today=TODAY, closes_fn=_closes(SERIES),
                                  consensus_fn=_no_consensus)
        assert {rid for rid, _ in _labels()} == {"late", "next_day"}
        assert report["skipped_same_day_rerun"] == 1

    def test_a_label_already_written_for_a_rerun_is_removed(self):
        _archive("early", run_at="2026-01-01T09:00:00", dr=_dr(iv=95.0))
        vo.score_matured(today=TODAY, closes_fn=_closes(SERIES), consensus_fn=_no_consensus)
        assert ("early", "consensus_0d") in _labels()
        _archive("late", run_at="2026-01-01T15:00:00", dr=_dr(iv=105.0))
        report = vo.score_matured(today=TODAY, closes_fn=_closes(SERIES),
                                  consensus_fn=_no_consensus)
        assert {rid for rid, _ in _labels()} == {"late"}
        assert report["superseded_removed"] == 1

    def test_a_dry_run_removes_nothing(self):
        _archive("early", run_at="2026-01-01T09:00:00", dr=_dr(iv=95.0))
        vo.score_matured(today=TODAY, closes_fn=_closes(SERIES), consensus_fn=_no_consensus)
        _archive("late", run_at="2026-01-01T15:00:00", dr=_dr(iv=105.0))
        vo.score_matured(today=TODAY, closes_fn=_closes(SERIES), consensus_fn=_no_consensus,
                         write=False)
        assert ("early", "consensus_0d") in _labels()


class TestTerminal:
    def _terminal(self):
        return {(r["run_id"], r["horizon"]): r
                for r in run_archive._fetch("SELECT * FROM valuation_outcome_terminal")}

    def test_a_name_that_stopped_trading_gets_a_terminal_record(self):
        _ledger("r1", ticker="GONE")
        _ledger("r2", ticker="AACO")                        # the feed is answering
        series = {**SERIES, "GONE": [("2026-02-02", 40.0), ("2026-03-02", 38.0)]}
        report = vo.score_matured(today=TODAY, closes_fn=_closes(series),
                                  consensus_fn=_no_consensus)
        term = self._terminal()
        assert set(term) == {("r1", "px_90d"), ("r1", "px_180d")}
        rec = term[("r1", "px_90d")]
        assert rec["reason"] == "no_close_since_target"
        assert (rec["last_close"], rec["last_close_date"]) == (38.0, "2026-03-02")
        assert report["terminal_no_price"] == 2
        assert ("r1", "px_30d") in _labels()                # the window that did trade
        # never refetched or relabelled
        again = vo.score_matured(today=TODAY, closes_fn=_closes(series),
                                 consensus_fn=_no_consensus)
        assert again["terminal"] == 0 and vo.terminal_counts() == {"px_90d": 1, "px_180d": 1}

    def test_a_recent_miss_is_retried_not_terminal(self):
        _ledger("r1", ticker="GONE", run_at="2026-06-01T10:00:00")
        _ledger("r2", ticker="AACO")
        vo.score_matured(today=date(2026, 7, 10), closes_fn=_closes(SERIES),
                         consensus_fn=_no_consensus)
        assert self._terminal() == {}                       # px_30d window ended < 14d ago

    def test_a_feed_outage_is_never_terminal(self):
        _ledger("r1", ticker="GONE")
        vo.score_matured(today=TODAY, closes_fn=_closes({}), consensus_fn=_no_consensus)
        assert self._terminal() == {}

    def test_a_gap_with_later_closes_is_not_terminal(self):
        _ledger("r1", ticker="GAPPY")
        _ledger("r2", ticker="AACO")
        series = {**SERIES, "GAPPY": [("2026-02-20", 10.0), ("2026-09-01", 11.0)]}
        report = vo.score_matured(today=TODAY, closes_fn=_closes(series),
                                  consensus_fn=_no_consensus)
        assert ("r1", "px_30d") not in self._terminal()
        assert report["no_price"] >= 1


class TestBackfillScript:
    def test_dry_run_by_default_writes_nothing(self, capsys):
        from scripts import backfill_outcome_labels as bol
        _ledger("r1")
        rc = bol.main(["--today", TODAY.isoformat()],
                      closes_fn=_closes(SERIES))
        assert rc == 0 and _labels() == {}
        out = capsys.readouterr().out
        assert "DRY RUN" in out and '"labels": 3' in out

    def test_write_labels_every_matured_ledger_row(self):
        from scripts import backfill_outcome_labels as bol
        _ledger("r1")
        _ledger("r2", ticker="AACO", run_at="2026-03-01T10:00:00")
        rc = bol.main(["--today", TODAY.isoformat(), "--write"],
                      closes_fn=_closes(SERIES))
        assert rc == 0
        assert {rid for rid, _ in _labels()} == {"r1", "r2"}

    def test_plan_counts_matured_rows_without_fetching(self):
        _ledger("r1")
        _ledger("r1b", run_at="2026-01-01T12:00:00")        # same-day rerun
        _ledger("r2", ticker="AACO", run_at="2026-09-01T10:00:00")   # nothing matured
        plan = vo.label_plan(today=TODAY)
        assert plan["predictions"] == 2 and plan["same_day_reruns"] == 1
        assert plan["with_matured_horizon"] == 1
        assert plan["matured_by_horizon"]["px_180d"] == 1
