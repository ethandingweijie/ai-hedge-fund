"""run_features provenance (2026-11-27): pipeline model, research model and engine build,
so the accuracy scorecard can split Qwen from Anthropic runs and one build from the next.
New columns on an existing table: the runtime schema guard must add them too."""
import json
import sqlite3

import pytest
import sqlalchemy as sa


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    from src.memory import run_archive as ra
    from src.memory import run_features as rf
    from src.memory import valuation_outcomes as vo
    path = str(tmp_path / "prov.db")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("RUN_FEATURES_DISABLED", raising=False)
    monkeypatch.delenv("RAILWAY_GIT_COMMIT_SHA", raising=False)
    monkeypatch.setenv("RUN_ARCHIVE_PATH", path)
    monkeypatch.setattr(ra, "DB_PATH", path)
    monkeypatch.setattr(ra, "_sqlite_schema_paths", set())
    vo._tables_ready_key = None
    rf._tables_ready_key = None
    yield path
    vo._tables_ready_key = None
    rf._tables_ready_key = None


from src.memory import run_archive  # noqa: E402
from src.memory import run_features as rf  # noqa: E402

DR = {"base": {"intrinsic_value": 100.0}, "bear": {"intrinsic_value": 80.0},
      "bull": {"intrinsic_value": 130.0}, "_engine_cache_version": 3}


def _all():
    return {r["feature_key"]: r for r in rf.rows(exclude_user=False)}


def test_extract_records_the_models_and_the_engine_build():
    row = rf.extract("r1", "ZZCO", DR, {}, run_at="2026-11-27T10:00:00",
                     pipeline_model="qwen3.8-flash", research_model="qwen3.8-flash",
                     pipeline_version="2.0", commit="abcdef123456")
    assert row["pipeline_model"] == "qwen3.8-flash" and row["research_model"] == "qwen3.8-flash"
    assert row["engine_version"] == "pipeline 2.0|dcf 3|abcdef1"
    assert set(row) == set(rf.COLUMNS)


def test_record_stamps_the_deployed_commit(monkeypatch):
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "0a877d39ffff")
    assert rf.record("r1", "ZZCO", DR, {}, run_at="2026-11-27T10:00:00",
                     pipeline_model="claude-sonnet", pipeline_version="2.0")
    got = _all()["r1|ZZCO"]
    assert got["engine_version"] == "pipeline 2.0|dcf 3|0a877d3"
    assert got["pipeline_model"] == "claude-sonnet"


def test_backfill_reads_the_run_model_and_keeps_save_time_provenance(monkeypatch):
    run_archive._exec(
        "INSERT INTO runs (run_id, run_at, analysis_date, tickers, sector, model_name, "
        "pipeline_version) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ["r1", "2026-11-27T10:00:00", "2026-11-27", json.dumps(["ZZCO"]), "Tech", "qwen3.8-flash", "2.0"])
    run_archive._exec(
        "INSERT INTO ticker_signals (run_id, ticker, price_at_run, price_target, dcf_range_json, "
        "scenario_json) VALUES (?, ?, ?, ?, ?, ?)",
        ["r1", "ZZCO", 90.0, 110.0, json.dumps(DR), json.dumps({"12m_price_target": 110.0})])
    rf.backfill()
    got = _all()["r1|ZZCO"]
    assert got["pipeline_model"] == "qwen3.8-flash"
    assert got["engine_version"] == "pipeline 2.0|dcf 3"
    # a save-time row knew the research model and the commit; a forced rewrite keeps them
    from src.data import db
    db.execute("UPDATE run_features SET research_model = 'qwen3.8-flash', "
               "engine_version = 'pipeline 2.0|dcf 3|0a877d3' WHERE feature_key = 'r1|ZZCO'")
    rf.backfill(force=True)
    got = _all()["r1|ZZCO"]
    assert got["research_model"] == "qwen3.8-flash"
    assert got["engine_version"] == "pipeline 2.0|dcf 3|0a877d3"


def test_an_old_run_features_table_gains_the_columns(isolated_db):
    # the table as first shipped: today's DDL without any later-added column
    added = {c for c, _ in rf.ADDED_COLUMNS}
    lines = [ln for ln in rf._DDL.splitlines() if ln.strip().split(" ")[0] not in added]
    ddl = "\n".join(lines).replace("recorded_at          TEXT NOT NULL,", "recorded_at          TEXT NOT NULL")
    conn = sqlite3.connect(isolated_db)
    conn.execute(ddl)
    conn.commit()
    have = {r[1] for r in conn.execute("PRAGMA table_info(run_features)")}
    assert not (have & added)
    conn.close()
    from src.data import db
    rf._ensure_tables()
    for col, _ in rf.ADDED_COLUMNS:
        assert db.column_exists("run_features", col), col


def test_the_price_scorecard_splits_by_provider():
    from datetime import date
    from src.memory import valuation_outcomes as vo
    for rid, tk, model in (("q1", "AAA", "qwen3.8-flash"), ("c1", "BBB", "claude-sonnet")):
        rf._ensure_tables()
        rf._write([rf.extract(rid, tk, {**DR, "pt_bridge": {"spot": 90.0}}, {"12m_price_target": 110.0},
                              run_at="2026-01-01T10:00:00", pipeline_model=model)])
    series = [("2026-02-02", 99.0), ("2026-04-01", 108.0), ("2026-07-01", 125.0), ("2026-09-11", 1.0)]
    vo.score_matured(today=date(2026, 9, 14), closes_fn=lambda t, a, b: [x for x in series if a <= x[0] <= b],
                     consensus_fn=lambda t: None)
    card = vo.scorecard(("pipeline_model",))
    assert set(card["groups"]) == {"qwen3.8-flash", "claude-sonnet"}
    assert card["groups"]["qwen3.8-flash"]["horizons"]["px_90d"]["n"] == 1
    with pytest.raises(ValueError):
        vo.scorecard(("f.user_id",))


class TestSchemaGuard:
    def test_guard_adds_the_learning_columns_to_existing_tables(self, tmp_path):
        from app.backend.database import schema_guard
        eng = sa.create_engine(f"sqlite:///{tmp_path / 'orm.db'}")
        with eng.begin() as c:
            c.execute(sa.text("CREATE TABLE run_features (feature_key TEXT PRIMARY KEY)"))
            c.execute(sa.text("CREATE TABLE estimate_outcomes (outcome_key TEXT PRIMARY KEY)"))
        schema_guard.ensure_learning_ledger_columns(eng)
        schema_guard.ensure_learning_ledger_columns(eng)          # idempotent
        insp = sa.inspect(eng)
        rf_cols = {c["name"] for c in insp.get_columns("run_features")}
        eo_cols = {c["name"] for c in insp.get_columns("estimate_outcomes")}
        assert {"pipeline_model", "research_model", "engine_version", "fy0_end",
                "pt_calibration_version"} <= rf_cols
        assert {"lead_days", "anchor_rule"} <= eo_cols

    def test_guard_skips_missing_tables_and_runs_at_boot(self, tmp_path):
        import inspect
        from app.backend.database import schema_guard
        eng = sa.create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
        schema_guard.ensure_learning_ledger_columns(eng)          # no tables: no-op
        assert "ensure_learning_ledger_columns" in inspect.getsource(schema_guard.ensure_all)

    def test_guard_matches_the_modules(self):
        from app.backend.database import schema_guard
        from src.memory import estimate_outcomes as eo
        assert schema_guard._LEARNING_COLUMNS["run_features"] == rf.ADDED_COLUMNS
        eo_cols = dict(schema_guard._LEARNING_COLUMNS["estimate_outcomes"])
        assert set(eo_cols) <= set(eo._COLUMNS)
