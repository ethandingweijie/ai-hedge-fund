"""
Temporary endpoint to migrate run data to the cloud.
DELETE THIS ROUTE after migration is complete.
"""
import os
import sqlite3
import json
import logging
from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger(__name__)
router = APIRouter()

UPLOAD_SECRET = os.environ.get("DB_UPLOAD_SECRET", "")


def _get_db_path():
    return os.environ.get("RUN_ARCHIVE_PATH", "/data/run_archive.db")


@router.post("/admin/migrate-runs")
async def migrate_runs(request: Request, secret: str = ""):
    """Import web_runs from JSON payload."""
    if not UPLOAD_SECRET or secret != UPLOAD_SECRET:
        raise HTTPException(status_code=403, detail="Invalid or missing secret")

    body = await request.json()
    runs = body if isinstance(body, list) else body.get("runs", [])

    db_path = _get_db_path()
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")

    # Ensure table exists
    conn.execute("""CREATE TABLE IF NOT EXISTS web_runs (
        run_id TEXT PRIMARY KEY, run_at TEXT NOT NULL, ticker TEXT NOT NULL,
        model_name TEXT, archive_run_id TEXT, full_result_json TEXT,
        final_action TEXT, regime TEXT, sector TEXT,
        is_checkpoint INTEGER DEFAULT 0, user_id INTEGER
    )""")

    inserted = 0
    for r in runs:
        try:
            conn.execute(
                "INSERT OR REPLACE INTO web_runs "
                "(run_id, run_at, ticker, model_name, full_result_json, final_action, regime, sector) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (r["run_id"], r["run_at"], r["ticker"], r.get("model_name"),
                 r.get("full_result_json"), r.get("final_action"),
                 r.get("regime"), r.get("sector")),
            )
            inserted += 1
        except Exception as e:
            logger.warning(f"Skip run {r.get('run_id', '?')}: {e}")

    conn.commit()
    conn.close()
    return {"status": "ok", "inserted": inserted, "total": len(runs)}
