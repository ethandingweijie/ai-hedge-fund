"""Loop 5 of the self-learning layer: user overrides scored beside the agent at maturity."""
import json
from datetime import date

import pytest


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    from src.data import estimate_override_store as store
    from src.memory import estimate_outcomes as eo
    from src.memory import override_outcomes as oo
    from src.memory import run_archive as ra
    from src.memory import run_features as rf
    from src.memory import valuation_outcomes as vo
    path = str(tmp_path / "ovr.db")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("OVERRIDE_OUTCOMES_DISABLED", raising=False)
    monkeypatch.setenv("RUN_ARCHIVE_PATH", path)
    monkeypatch.setattr(ra, "DB_PATH", path)
    monkeypatch.setattr(ra, "_sqlite_schema_paths", set())
    for m in (vo, rf, eo, oo):
        m._tables_ready_key = None
    store._ready_key = None
    yield
    for m in (vo, rf, eo, oo):
        m._tables_ready_key = None
    store._ready_key = None


from src.data import estimate_override_store as store  # noqa: E402
from src.memory import override_outcomes as oo  # noqa: E402
from src.memory import run_archive  # noqa: E402
from src.memory import valuation_outcomes as vo  # noqa: E402

TODAY = date(2026, 9, 14)


def _archive_run(run_id, ticker="ZZCO", run_at="2026-01-01T10:00:00", price=90.0):
    run_archive._exec("INSERT INTO runs (run_id, run_at, analysis_date, tickers, sector) VALUES (?, ?, ?, ?, ?)",
                      [run_id, run_at, run_at[:10], json.dumps([ticker]), "Tech"])
    dcf = {"base": {"intrinsic_value": 100.0}, "bear": {"intrinsic_value": 80.0}, "bull": {"intrinsic_value": 130.0},
           "profile": "Mature SaaS", "is_cache_copy": False,
           "consensus_at_run": {"status": "recorded", "target": 120.0, "fetched_at": run_at}}
    run_archive._exec("INSERT INTO ticker_signals (run_id, ticker, price_at_run, price_target, dcf_range_json, "
                      "scenario_json) VALUES (?, ?, ?, ?, ?, ?)",
                      [run_id, ticker, price, 110.0, json.dumps(dcf), json.dumps({"12m_price_target": 110.0})])


def _web_run(web_id, archive_id, ticker="ZZCO", run_at="2026-01-01T10:00:00"):
    from src.data import db
    db.execute("CREATE TABLE IF NOT EXISTS web_runs (run_id TEXT PRIMARY KEY, run_at TEXT, ticker TEXT, "
               "archive_run_id TEXT, is_checkpoint INTEGER DEFAULT 0)")
    db.execute("INSERT INTO web_runs (run_id, run_at, ticker, archive_run_id) VALUES (?, ?, ?, ?)",
               [web_id, run_at, ticker, archive_id])


def _override(web_id, ticker="ZZCO", before_pt=110.0, after_pt=96.0, before_iv=100.0, after_iv=92.0,
              fields=("revenue_growth_fy1", "fade_years"), user_rg=0.05):
    return store.save(web_id, ticker, 7,
                      {"scenarios": {"base": {"revenue_growth_fy1": user_rg}}, "shared": {"fade_years": 6}},
                      "slower", {"fields": list(fields),
                                 "before": {"intrinsic_value": before_iv, "12m_price_target": before_pt},
                                 "after": {"intrinsic_value": after_iv, "12m_price_target": after_pt}})


def _closes(series):
    def fn(ticker, start, end):
        return [(d, c) for d, c in series if start <= d <= end]
    return fn


SERIES = [("2026-02-02", 99.0), ("2026-04-01", 97.0), ("2026-07-01", 95.0)]


def _rows():
    from src.data import db
    return {r["horizon"]: dict(r) for r in db.query("SELECT * FROM override_outcomes")}


class TestScoring:
    def test_user_vs_agent_at_each_matured_horizon_via_the_web_run_bridge(self):
        _archive_run("a1"); _web_run("w1", "a1"); rec = _override("w1")
        rep = oo.score_matured(today=TODAY, closes_fn=_closes(SERIES))
        assert rep["overrides"] == 1 and rep["written"] == 3 and rep["not_matured"] == 1
        rows = _rows()
        assert set(rows) == {"px_30d", "px_90d", "px_180d"}
        r = rows["px_180d"]
        assert r["archive_run_id"] == "a1" and r["override_id"] == rec["id"] and r["label_value"] == 95.0
        assert r["after_pt"] == 96.0 and r["before_pt"] == 110.0 and r["user_closer"] == 1
        assert json.loads(r["fields_json"]) == ["revenue_growth_fy1", "fade_years"]
        assert r["label_source"] == "close"
        # idempotent
        assert oo.score_matured(today=TODAY, closes_fn=_closes(SERIES))["written"] == 0

    def test_labels_come_from_the_agents_ledger_when_present_and_never_from_consensus(self):
        _archive_run("a1"); _web_run("w1", "a1"); _override("w1")
        vo.score_matured(today=TODAY, closes_fn=_closes(SERIES), consensus_fn=lambda t: None)
        assert ("a1", "consensus_0d") in {(r["run_id"], r["horizon"]) for r in run_archive._fetch(
            "SELECT run_id, horizon FROM valuation_outcomes")}

        def never(ticker, start, end):
            raise AssertionError("prices fetched although the ledger has the labels")
        rep = oo.score_matured(today=TODAY, closes_fn=never)
        rows = _rows()
        assert rep["written"] == 3 and "consensus_0d" not in rows
        assert rows["px_90d"]["label_value"] == 97.0 and rows["px_90d"]["profile"] == "Mature SaaS"

    def test_unmapped_and_unpriced_overrides_are_counted(self):
        _override("ghost")                                  # no web run, no archive run
        _archive_run("a2"); _web_run("w2", "a2"); _override("w2")
        rep = oo.score_matured(today=TODAY, closes_fn=_closes([]))
        assert rep["unmapped"] == 1 and rep["no_price"] == 3 and rep["written"] == 0

    def test_write_false_and_disabled(self, monkeypatch):
        _archive_run("a1"); _web_run("w1", "a1"); _override("w1")
        rep = oo.score_matured(today=TODAY, closes_fn=_closes(SERIES), write=False)
        assert rep["rows"] == 3 and rep["written"] == 0 and _rows() == {}
        monkeypatch.setenv("OVERRIDE_OUTCOMES_DISABLED", "1")
        assert oo.score_matured(today=TODAY, closes_fn=_closes(SERIES)).get("disabled")


class TestReports:
    def test_report_shares_and_field_frequency(self):
        for i in range(6):
            _archive_run(f"a{i}", ticker=f"T{i}"); _web_run(f"w{i}", f"a{i}", ticker=f"T{i}")
            _override(f"w{i}", ticker=f"T{i}", after_pt=96.0 if i < 4 else 130.0)
        oo.score_matured(today=TODAY, closes_fn=_closes(SERIES))
        rep = oo.report()
        h = rep["horizons"]["px_180d"]
        assert h["n"] == 6 and h["status"] == "ok" and h["user_closer_share"] == pytest.approx(4 / 6, abs=1e-3)
        assert h["user_median_miss_pct"] < h["agent_median_miss_pct"]
        assert rep["horizons"]["px_365d"]["n"] == 0 and rep["horizons"]["px_365d"]["status"] == "insufficient"
        assert rep["fields_changed"] == {"fade_years": 6, "revenue_growth_fy1": 6}
        assert rep["n_overrides"] == 6

    def test_estimate_field_outcomes_compare_the_users_figure_with_the_agents_print(self):
        from src.data import db
        from src.memory import estimate_outcomes as eo
        _archive_run("a1"); _web_run("w1", "a1"); _override("w1", user_rg=0.05)
        eo._ensure_tables()
        row = {c: None for c in eo._COLUMNS}
        row.update(outcome_key="a1|ZZCO|revenue_growth|fy", run_id="a1", ticker="ZZCO", run_date="2026-01-01",
                   fiscal_year_1=2026, field="revenue_growth", period_kind="fy", agent_base=0.08, actual=0.04,
                   scored_at="2027-03-01T00:00:00")
        db.execute(vo._upsert_sql("estimate_outcomes", "outcome_key", eo._COLUMNS), [row[c] for c in eo._COLUMNS])
        out = oo.estimate_field_outcomes()
        assert out["n"] == 1 and out["user_closer"] == 1 and out["rows"][0]["actual"] == 0.04
