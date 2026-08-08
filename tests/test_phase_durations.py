"""Tests for Workstream A — pipeline phase-duration instrumentation.

Forward gates (new behaviour works end-to-end):
  * _migrate_web_runs_columns adds phase_durations to a legacy DB (additive)
  * _save_web_run persists the JSON into the dedicated column AND into
    full_result_json.data.phase_durations
  * get_run_result replay returns data.phase_durations
  * checkpoint partial saves carry in-progress phase_durations

Backward gates (old behaviour unbroken):
  * legacy rows (no phase_durations anywhere) still replay cleanly
  * results without phase_durations save with NULL column (CLI/legacy path)
  * migration never touches existing rows' data
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from app.backend.services import analysis_service as svc


SAMPLE_DURATIONS = [
    {"phase": "1_macro_regime", "started_at": "2026-08-08T09:00:00",
     "finished_at": "2026-08-08T09:00:12", "duration_s": 12.34},
    {"phase": "3_deep_research_router", "started_at": "2026-08-08T09:00:12",
     "finished_at": "2026-08-08T09:04:02", "duration_s": 230.1},
]


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Point RUN_ARCHIVE_PATH at a fresh temp DB for each test."""
    db_path = tmp_path / "run_archive_test.db"
    monkeypatch.setenv("RUN_ARCHIVE_PATH", str(db_path))
    return db_path


def _make_result(with_durations: bool = True) -> dict:
    """Minimal pipeline result shaped like run_analysis_pipeline's output."""
    data = {
        "tickers": ["CRWD"],
        "sector": "Technology",
        "macro_regime": {"risk_appetite": "risk_on"},
        "profile_name": "Growth SaaS",
        "decisions": {},
    }
    if with_durations:
        data["phase_durations"] = SAMPLE_DURATIONS
    return {
        "run_id": "test-run-1",
        "ticker": "CRWD",
        "model_name": "claude-sonnet-4-6",
        "run_at": "2026-08-08T09:00:00+00:00",
        "data": data,
        "decisions": {"CRWD": {"action": "BUY"}},
    }


# ── Forward: migration is additive ──────────────────────────────────────────


def test_migration_adds_phase_durations_column_to_legacy_db(tmp_db):
    # Build the OLD schema (pre-Workstream-A): no phase_durations column
    with sqlite3.connect(tmp_db) as conn:
        conn.execute("""
            CREATE TABLE web_runs (
                run_id           TEXT PRIMARY KEY,
                run_at           TEXT NOT NULL,
                ticker           TEXT NOT NULL,
                model_name       TEXT,
                archive_run_id   TEXT,
                full_result_json TEXT,
                final_action     TEXT,
                regime           TEXT,
                sector           TEXT,
                profile_name     TEXT,
                is_checkpoint    INTEGER DEFAULT 0
            )
        """)
        conn.execute(
            "INSERT INTO web_runs (run_id, run_at, ticker, full_result_json) "
            "VALUES ('legacy-1', '2026-04-01T00:00:00', 'NET', '{\"data\": {}}')"
        )
        conn.commit()

    svc._ensure_web_runs_table()

    with sqlite3.connect(tmp_db) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(web_runs)")}
        assert "phase_durations" in cols
        # Legacy row untouched
        row = conn.execute(
            "SELECT full_result_json, phase_durations FROM web_runs WHERE run_id='legacy-1'"
        ).fetchone()
        assert row[0] == '{"data": {}}'
        assert row[1] is None  # NULL for legacy rows


def test_migration_is_idempotent(tmp_db):
    svc._ensure_web_runs_table()
    svc._ensure_web_runs_table()  # second call must not raise
    with sqlite3.connect(tmp_db) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(web_runs)")}
        assert "phase_durations" in cols


# ── Forward: save + replay round-trip ───────────────────────────────────────


def test_save_web_run_persists_phase_durations(tmp_db):
    svc._save_web_run("run-pd-1", "CRWD", "claude-sonnet-4-6", _make_result(with_durations=True))

    with sqlite3.connect(tmp_db) as conn:
        col_json, full_json = conn.execute(
            "SELECT phase_durations, full_result_json FROM web_runs WHERE run_id='run-pd-1'"
        ).fetchone()

    # Dedicated column
    assert json.loads(col_json) == SAMPLE_DURATIONS
    # Also inside full_result_json (serialization contract)
    full = json.loads(full_json)
    assert full["data"]["phase_durations"] == SAMPLE_DURATIONS


def test_get_run_result_replays_phase_durations(tmp_db):
    svc._save_web_run("run-pd-2", "CRWD", "claude-sonnet-4-6", _make_result(with_durations=True))
    replayed = svc.get_run_result("run-pd-2")
    assert replayed is not None
    assert replayed["data"]["phase_durations"] == SAMPLE_DURATIONS


def test_checkpoint_partial_save_includes_phase_durations(tmp_db):
    state = {
        "data": {
            "tickers": ["CRWD"],
            "phase_durations": SAMPLE_DURATIONS[:1],  # mid-run: only phase 1 done
            "deep_research": "partial research text",
        }
    }
    svc._save_partial_web_run("run-pd-3", "CRWD", "claude-sonnet-4-6",
                              "deep_research", state)

    with sqlite3.connect(tmp_db) as conn:
        (full_json, is_ckpt) = conn.execute(
            "SELECT full_result_json, is_checkpoint FROM web_runs WHERE run_id='run-pd-3'"
        ).fetchone()
    assert is_ckpt == 1
    payload = json.loads(full_json)
    assert payload["checkpoint"] == "deep_research"
    assert payload["data"]["phase_durations"] == SAMPLE_DURATIONS[:1]


# ── Backward: old payloads still work ───────────────────────────────────────


def test_save_without_phase_durations_sets_null_column(tmp_db):
    svc._save_web_run("run-old-1", "CRWD", "claude-sonnet-4-6", _make_result(with_durations=False))

    with sqlite3.connect(tmp_db) as conn:
        col_json = conn.execute(
            "SELECT phase_durations FROM web_runs WHERE run_id='run-old-1'"
        ).fetchone()[0]
    assert col_json is None

    # Replay still fine, key simply absent
    replayed = svc.get_run_result("run-old-1")
    assert replayed is not None
    assert "phase_durations" not in replayed["data"]


def test_legacy_row_replays_without_phase_durations(tmp_db):
    # Simulate a pre-change archived run written by old code
    svc._ensure_web_runs_table()
    legacy_payload = {
        "run_id": "legacy-2",
        "ticker": "JPM",
        "model_name": "claude-sonnet-4-6",
        "run_at": "2026-04-01T00:00:00+00:00",
        "data": {"tickers": ["JPM"], "sector": "Financials"},
        "decisions": {"JPM": {"action": "HOLD"}},
    }
    with sqlite3.connect(tmp_db) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO web_runs "
            "(run_id, run_at, ticker, model_name, full_result_json, is_checkpoint) "
            "VALUES (?,?,?,?,?,0)",
            ("legacy-2", "2026-04-01T00:00:00", "JPM", "claude-sonnet-4-6",
             json.dumps(legacy_payload)),
        )
        conn.commit()

    replayed = svc.get_run_result("legacy-2")
    assert replayed is not None
    assert replayed["decisions"]["JPM"]["action"] == "HOLD"
    assert replayed["data"].get("phase_durations") is None  # additive key absent
