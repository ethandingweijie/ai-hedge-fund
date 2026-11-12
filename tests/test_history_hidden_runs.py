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
