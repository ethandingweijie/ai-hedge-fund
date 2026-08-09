"""
app/backend/services/complacency_job_store.py
==============================================
Job tracking for long-running Complacency operations (cohort refresh,
force-qual re-scoring). These operations can take 5-10 min — too long for
iOS Safari's fetch timeout / app-background handling.

Storage is dual-mode via src.data.db: SQLite locally, PostgreSQL on Railway
(shared with the web + worker processes — safe for multi-instance deploys).

Pattern:
  • POST /refresh  → create_job() → return {job_id, status: pending}
                   → backend executes the work in-process or in the worker
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
  user_id       INTEGER                id of the user who triggered the job;
                                       NULL for scheduled/service-triggered
                                       jobs and rows created before auth.
                                       Attribution + per-user rate limiting
                                       only — job reads stay globally visible
                                       because these jobs compute shared
                                       research data (see routes/research.py).
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
from datetime import datetime, timezone
from typing import Optional

from src.data import db

logger = logging.getLogger(__name__)


_DDL = """
CREATE TABLE IF NOT EXISTS complacency_jobs (
    job_id        TEXT PRIMARY KEY,
    kind          TEXT NOT NULL,
    ticker        TEXT,
    user_id       INTEGER,
    status        TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    progress_msg  TEXT,
    result_json   TEXT,
    error_msg     TEXT
);
CREATE INDEX IF NOT EXISTS idx_complacency_jobs_status_started
    ON complacency_jobs(status, started_at DESC);
"""


def _ensure_table() -> None:
    db.ensure_table(_DDL)
    # Schema evolution for DBs created before the column existed
    db.add_column_if_missing("complacency_jobs", "user_id", "INTEGER")


def _to_dt(value) -> Optional[datetime]:
    """Normalise started_at/finished_at (ISO str on SQLite, datetime on PG)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def create_job(kind: str, ticker: Optional[str] = None,
               user_id: Optional[int] = None) -> str:
    """Insert a pending job, return its job_id.

    user_id stamps who triggered the job (attribution + the basis for
    per-user rate limits). NULL = scheduled/service-triggered or pre-auth.
    """
    _ensure_table()
    import uuid
    job_id = uuid.uuid4().hex[:16]
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        "INSERT INTO complacency_jobs "
        "(job_id, kind, ticker, user_id, status, started_at, progress_msg) "
        "VALUES (?, ?, ?, ?, 'pending', ?, 'queued')",
        [job_id, kind, ticker, user_id, now],
    )
    return job_id


def update_progress(job_id: str, status: str, message: str) -> None:
    """Update status + progress_msg for a running job."""
    _ensure_table()
    db.execute(
        "UPDATE complacency_jobs SET status = ?, progress_msg = ? WHERE job_id = ?",
        [status, message[:500], job_id],
    )


def update_running_result(job_id: str, partial_result: dict) -> None:
    """
    Patch result_json on a running job mid-flight. Used by the two-phase
    score worker to publish quant-only result after Phase 1, then update
    again as qualitative indicators stream in.

    Frontend polling reads result_json on every successful GET /jobs/{id},
    so live updates surface immediately. The job status STAYS 'running'
    until complete_job() or fail_job() is called.
    """
    _ensure_table()
    db.execute(
        "UPDATE complacency_jobs SET result_json = ? WHERE job_id = ?",
        [json.dumps(partial_result) if partial_result is not None else None, job_id],
    )


def complete_job(job_id: str, result: dict | None = None) -> None:
    """Mark job as completed with optional result payload."""
    _ensure_table()
    db.execute(
        "UPDATE complacency_jobs SET status='completed', finished_at=?, "
        "result_json=?, progress_msg='done' WHERE job_id = ?",
        [
            datetime.now(timezone.utc).isoformat(),
            json.dumps(result) if result is not None else None,
            job_id,
        ],
    )


def fail_job(job_id: str, error: str) -> None:
    """Mark job as failed with error message."""
    _ensure_table()
    db.execute(
        "UPDATE complacency_jobs SET status='failed', finished_at=?, "
        "error_msg=?, progress_msg='failed' WHERE job_id = ?",
        [
            datetime.now(timezone.utc).isoformat(),
            str(error)[:1000],
            job_id,
        ],
    )


# Watchdog ceiling — jobs pending/running longer than this are auto-failed
# at read time. Covers cases where the background task died silently
# (uvicorn worker restart, OOM kill, asyncio task GC'd before the
# strong-reference fix landed, etc.) so the user gets a definitive
# "failed" instead of an indefinite "pending" that the poll layer waits
# on until its own 25-min timeout fires.
_STUCK_JOB_CEILING_MINUTES = 30


def _is_stuck(status: str, started_at) -> bool:
    if status not in ("pending", "running"):
        return False
    dt = _to_dt(started_at)
    if dt is None:
        return False
    age_minutes = (datetime.now(timezone.utc) - dt).total_seconds() / 60
    return age_minutes > _STUCK_JOB_CEILING_MINUTES


def _row_to_job(row) -> dict:
    result_raw = row["result_json"]
    if isinstance(result_raw, (dict, list)):
        # Postgres JSONB columns can come back already-deserialised
        result = result_raw
    else:
        result = json.loads(result_raw) if result_raw else None
    return {
        "job_id":       row["job_id"],
        "kind":         row["kind"],
        "ticker":       row["ticker"],
        "status":       row["status"],
        "started_at":   row["started_at"],
        "finished_at":  row["finished_at"],
        "progress_msg": row["progress_msg"],
        "result":       result,
        "error":        row["error_msg"],
    }


def get_job(job_id: str) -> Optional[dict]:
    """Fetch full job state. Auto-fails jobs that have been stuck >30 min."""
    _ensure_table()
    row = db.query_one(
        "SELECT job_id, kind, ticker, status, started_at, finished_at, "
        "progress_msg, result_json, error_msg "
        "FROM complacency_jobs WHERE job_id = ?",
        [job_id],
    )
    if not row:
        return None
    job = _row_to_job(row)
    if _is_stuck(job["status"], job["started_at"]):
        logger.warning(
            "Job %s stuck in %s state since %s — auto-failing",
            job_id, job["status"], job["started_at"],
        )
        fail_job(
            job_id,
            f"Watchdog: job stuck in '{job['status']}' state for >{_STUCK_JOB_CEILING_MINUTES} min. "
            "Background task likely died (worker recycled / OOM / GC'd before strong-ref fix landed)."
        )
        # Re-read to surface the now-failed state
        row = db.query_one(
            "SELECT job_id, kind, ticker, status, started_at, finished_at, "
            "progress_msg, result_json, error_msg "
            "FROM complacency_jobs WHERE job_id = ?",
            [job_id],
        )
        if row:
            job = _row_to_job(row)
    return job


def list_recent_jobs(limit: int = 20) -> list[dict]:
    """List recent jobs across all kinds."""
    _ensure_table()
    rows = db.query(
        "SELECT job_id, kind, ticker, status, started_at, finished_at, progress_msg "
        "FROM complacency_jobs ORDER BY started_at DESC LIMIT ?",
        [limit],
    )
    return [
        {
            "job_id":       r["job_id"],
            "kind":         r["kind"],
            "ticker":       r["ticker"],
            "status":       r["status"],
            "started_at":   r["started_at"],
            "finished_at":  r["finished_at"],
            "progress_msg": r["progress_msg"],
        }
        for r in rows
    ]


def find_in_flight_job(kind: str, ticker: Optional[str] = None) -> Optional[dict]:
    """
    Find a pending/running job of the same kind (+ ticker if specified).
    Used to dedupe: if the user clicks Refresh twice, return the existing
    in-flight job instead of starting a duplicate.

    Stuck jobs (pending/running > 30 min) are auto-failed and treated as
    NOT in-flight, so the user's next click starts a fresh task instead of
    being permanently stuck on a dead job_id.
    """
    _ensure_table()
    if ticker is None:
        row = db.query_one(
            "SELECT job_id, kind, ticker, status, started_at, progress_msg "
            "FROM complacency_jobs "
            "WHERE kind = ? AND ticker IS NULL "
            "AND status IN ('pending','running') "
            "ORDER BY started_at DESC LIMIT 1",
            [kind],
        )
    else:
        row = db.query_one(
            "SELECT job_id, kind, ticker, status, started_at, progress_msg "
            "FROM complacency_jobs "
            "WHERE kind = ? AND ticker = ? "
            "AND status IN ('pending','running') "
            "ORDER BY started_at DESC LIMIT 1",
            [kind, ticker],
        )
    if not row:
        return None
    if _is_stuck(row["status"], row["started_at"]):
        logger.warning(
            "find_in_flight: %s job %s stuck since %s — auto-failing",
            kind, row["job_id"], row["started_at"],
        )
        fail_job(
            row["job_id"],
            f"Watchdog: stuck in '{row['status']}' for >{_STUCK_JOB_CEILING_MINUTES} min "
            "(background task likely died). Treating as not-in-flight so caller can start fresh."
        )
        return None
    return {
        "job_id":       row["job_id"],
        "kind":         row["kind"],
        "ticker":       row["ticker"],
        "status":       row["status"],
        "started_at":   row["started_at"],
        "progress_msg": row["progress_msg"],
    }
