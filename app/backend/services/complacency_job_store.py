"""
app/backend/services/complacency_job_store.py
==============================================
SQLite-backed job tracking for long-running Complacency operations
(cohort refresh, force-qual re-scoring). These operations can take 5-10
min — too long for iOS Safari's fetch timeout / app-background handling.

Pattern:
  • POST /refresh  → create_job() → return {job_id, status: pending}
                   → backend asyncio.create_task() executes the work
  • GET  /jobs/X  → returns current status (pending/running/completed/failed)
                   plus result on completion

Frontend polls /jobs/X every 5-10s. iOS Safari can drop the polling fetch
when backgrounded, but the job continues server-side — when the user
returns and re-polls, they get the completed state. A browser push
notification also fires from the polling layer when the status flips
to 'completed'.

Schema (auto-migrated):
  job_id        TEXT PRIMARY KEY
  kind          TEXT NOT NULL          'refresh' | 'score_adhoc'
  ticker        TEXT                   nullable for cohort refresh
  status        TEXT NOT NULL          'pending'|'running'|'completed'|'failed'
  started_at    TEXT NOT NULL
  finished_at   TEXT
  progress_msg  TEXT                   free-form progress label
  result_json   TEXT                   JSON of the operation result
  error_msg     TEXT                   only set when status='failed'
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _get_db_path() -> str:
    env_path = os.environ.get("RUN_ARCHIVE_PATH")
    if env_path:
        return env_path
    here = Path(__file__).resolve()
    project_root = here.parent.parent.parent.parent
    return str(project_root / "src" / "data" / "run_archive.db")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_get_db_path())
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


_DDL = """
CREATE TABLE IF NOT EXISTS complacency_jobs (
    job_id        TEXT PRIMARY KEY,
    kind          TEXT NOT NULL,
    ticker        TEXT,
    status        TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    progress_msg  TEXT,
    result_json   TEXT,
    error_msg     TEXT
)
"""

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_complacency_jobs_status_started "
    "ON complacency_jobs(status, started_at DESC)",
]


def _ensure_table() -> None:
    db_path = _get_db_path()
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = _connect()
    try:
        conn.execute(_DDL)
        for idx in _INDEXES:
            conn.execute(idx)
        conn.commit()
    finally:
        conn.close()


def create_job(kind: str, ticker: Optional[str] = None) -> str:
    """Insert a pending job, return its job_id."""
    _ensure_table()
    job_id = uuid.uuid4().hex[:16]
    now = datetime.now(timezone.utc).isoformat()
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO complacency_jobs "
            "(job_id, kind, ticker, status, started_at, progress_msg) "
            "VALUES (?, ?, ?, 'pending', ?, 'queued')",
            (job_id, kind, ticker, now),
        )
        conn.commit()
    finally:
        conn.close()
    return job_id


def update_progress(job_id: str, status: str, message: str) -> None:
    """Update status + progress_msg for a running job."""
    _ensure_table()
    conn = _connect()
    try:
        conn.execute(
            "UPDATE complacency_jobs SET status = ?, progress_msg = ? WHERE job_id = ?",
            (status, message[:500], job_id),
        )
        conn.commit()
    finally:
        conn.close()


def complete_job(job_id: str, result: dict | None = None) -> None:
    """Mark job as completed with optional result payload."""
    _ensure_table()
    conn = _connect()
    try:
        conn.execute(
            "UPDATE complacency_jobs SET status='completed', finished_at=?, "
            "result_json=?, progress_msg='done' WHERE job_id = ?",
            (
                datetime.now(timezone.utc).isoformat(),
                json.dumps(result) if result is not None else None,
                job_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def fail_job(job_id: str, error: str) -> None:
    """Mark job as failed with error message."""
    _ensure_table()
    conn = _connect()
    try:
        conn.execute(
            "UPDATE complacency_jobs SET status='failed', finished_at=?, "
            "error_msg=?, progress_msg='failed' WHERE job_id = ?",
            (
                datetime.now(timezone.utc).isoformat(),
                str(error)[:1000],
                job_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_job(job_id: str) -> Optional[dict]:
    """Fetch full job state."""
    _ensure_table()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT job_id, kind, ticker, status, started_at, finished_at, "
            "progress_msg, result_json, error_msg "
            "FROM complacency_jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return {
        "job_id":       row[0],
        "kind":         row[1],
        "ticker":       row[2],
        "status":       row[3],
        "started_at":   row[4],
        "finished_at":  row[5],
        "progress_msg": row[6],
        "result":       json.loads(row[7]) if row[7] else None,
        "error":        row[8],
    }


def list_recent_jobs(limit: int = 20) -> list[dict]:
    """List recent jobs across all kinds."""
    _ensure_table()
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT job_id, kind, ticker, status, started_at, finished_at, progress_msg "
            "FROM complacency_jobs ORDER BY started_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "job_id":       r[0],
            "kind":         r[1],
            "ticker":       r[2],
            "status":       r[3],
            "started_at":   r[4],
            "finished_at":  r[5],
            "progress_msg": r[6],
        }
        for r in rows
    ]


def find_in_flight_job(kind: str, ticker: Optional[str] = None) -> Optional[dict]:
    """
    Find a pending/running job of the same kind (+ ticker if specified).
    Used to dedupe: if the user clicks Refresh twice, return the existing
    in-flight job instead of starting a duplicate.
    """
    _ensure_table()
    conn = _connect()
    try:
        if ticker is None:
            row = conn.execute(
                "SELECT job_id, kind, ticker, status, started_at, progress_msg "
                "FROM complacency_jobs "
                "WHERE kind = ? AND ticker IS NULL "
                "AND status IN ('pending','running') "
                "ORDER BY started_at DESC LIMIT 1",
                (kind,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT job_id, kind, ticker, status, started_at, progress_msg "
                "FROM complacency_jobs "
                "WHERE kind = ? AND ticker = ? "
                "AND status IN ('pending','running') "
                "ORDER BY started_at DESC LIMIT 1",
                (kind, ticker),
            ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return {
        "job_id":       row[0],
        "kind":         row[1],
        "ticker":       row[2],
        "status":       row[3],
        "started_at":   row[4],
        "progress_msg": row[5],
    }
