"""History hides archive runs listed in history_hidden_runs.

Purging a web report keeps its archive row (runs + ticker_signals feed the
90/180-day scorers). Without the hide list, History lists that orphan as a
CLI run, so the purged report comes back on the page.
"""
from __future__ import annotations

import sqlite3

import pytest

from app.backend.services import analysis_service as svc


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "run_archive_test.db"
    monkeypatch.setenv("RUN_ARCHIVE_PATH", str(db_path))
    return db_path


def _seed(db_path, run_ids):
    svc._ensure_web_runs_table()
    conn = sqlite3.connect(db_path)
    for i, rid in enumerate(run_ids):
        conn.execute("INSERT INTO runs (run_id, run_at, model_name, sector) VALUES (?, ?, 'm', 'Tech')",
                     (rid, f"2026-10-0{i + 1}T00:00:00"))
        conn.execute("INSERT INTO ticker_signals (run_id, ticker, final_action) VALUES (?, 'CRWD', 'BUY')",
                     (rid,))
    conn.commit()
    conn.close()


def test_orphan_archive_run_is_listed_by_default(tmp_db):
    _seed(tmp_db, ["a1", "a2"])
    ids = {r["run_id"] for r in svc.get_history()["items"]}
    assert ids == {"a1", "a2"}


def test_hidden_archive_run_is_not_listed(tmp_db):
    _seed(tmp_db, ["a1", "a2"])
    conn = sqlite3.connect(tmp_db)
    conn.execute("INSERT INTO history_hidden_runs (run_id, hidden_at) VALUES ('a1', '2026-11-12')")
    conn.commit()
    conn.close()
    result = svc.get_history()
    assert {r["run_id"] for r in result["items"]} == {"a2"}
    assert result["total"] == 1
    # The archive row itself stays for the learning layer.
    conn = sqlite3.connect(tmp_db)
    assert conn.execute("SELECT COUNT(*) FROM runs WHERE run_id = 'a1'").fetchone()[0] == 1
    conn.close()


# ── delete_run is a soft delete (2026-11-27) ─────────────────────────────────
# Production lost every Sep-Oct archive row to the hard delete before its first
# 90/180-day label. Deleting a report must leave the learning data in place.

def _seed_web(db_path, web_id, archive_id, user_id=None):
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO web_runs (run_id, run_at, ticker, model_name, archive_run_id, "
                 "full_result_json, user_id) VALUES (?, '2026-10-01T00:00:00', 'CRWD', 'm', ?, '{}', ?)",
                 (web_id, archive_id, user_id))
    conn.execute("INSERT INTO agent_signals (run_id, ticker, agent_name, signal, confidence) "
                 "VALUES (?, 'CRWD', 'a', 'bullish', 0.5)", (archive_id,))
    conn.commit()
    conn.close()


def _count(db_path, table, run_id):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table} WHERE run_id = ?", (run_id,)).fetchone()[0]
    finally:
        conn.close()


def test_deleting_a_web_run_keeps_the_archive_and_hides_it(tmp_db):
    _seed(tmp_db, ["a1", "a2"])
    _seed_web(tmp_db, "w1", "a1")
    assert svc.delete_run("w1") is True
    assert _count(tmp_db, "web_runs", "w1") == 0
    for table in ("runs", "ticker_signals", "agent_signals"):
        assert _count(tmp_db, table, "a1") == 1, table
    assert _count(tmp_db, "history_hidden_runs", "a1") == 1
    assert {r["run_id"] for r in svc.get_history()["items"]} == {"a2"}


def test_deleting_a_cli_run_hides_it_and_deletes_nothing(tmp_db):
    _seed(tmp_db, ["a1", "a2"])
    assert svc.delete_run("a1") is True
    assert _count(tmp_db, "runs", "a1") == 1 and _count(tmp_db, "ticker_signals", "a1") == 1
    assert {r["run_id"] for r in svc.get_history()["items"]} == {"a2"}
    assert svc.delete_run("a1") is False                    # already gone from History
    assert svc.delete_run("nope") is False


def test_deleting_never_touches_run_features(tmp_db):
    _seed(tmp_db, ["a1"])
    _seed_web(tmp_db, "w1", "a1")
    conn = sqlite3.connect(tmp_db)
    conn.execute("CREATE TABLE IF NOT EXISTS run_features (feature_key TEXT PRIMARY KEY, run_id TEXT)")
    conn.execute("INSERT INTO run_features VALUES ('a1|CRWD', 'a1')")
    conn.commit()
    conn.close()
    svc.delete_run("w1")
    assert _count(tmp_db, "run_features", "a1") == 1


def test_another_users_run_is_not_deleted(tmp_db):
    _seed(tmp_db, ["a1"])
    _seed_web(tmp_db, "w1", "a1", user_id=7)
    assert svc.delete_run("w1", user_id=8) is False
    assert _count(tmp_db, "web_runs", "w1") == 1
    assert _count(tmp_db, "history_hidden_runs", "a1") == 0


def test_no_hard_delete_of_learning_tables_in_delete_run():
    import inspect
    src = inspect.getsource(svc.delete_run) + inspect.getsource(svc._hide_from_history)
    for table in ("runs", "ticker_signals", "agent_signals", "run_features"):
        assert f"DELETE FROM {table}" not in src
