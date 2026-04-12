"""
Admin endpoint — browse cloud database tables.
Protected by DB_UPLOAD_SECRET env var.
"""
import os
import sqlite3
import json
import logging
from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)
router = APIRouter()

ADMIN_SECRET = os.environ.get("DB_UPLOAD_SECRET", "")


def _get_db_paths() -> dict[str, str]:
    """Return both database paths."""
    return {
        "hedge_fund": os.environ.get("DATABASE_PATH", "hedge_fund.db"),
        "run_archive": os.environ.get("RUN_ARCHIVE_PATH", "run_archive.db"),
    }


@router.get("/admin/db")
async def list_tables(secret: str = "", db: str = "run_archive"):
    """List all tables and row counts in the specified database."""
    if not ADMIN_SECRET or secret != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")

    paths = _get_db_paths()
    db_path = paths.get(db)
    if not db_path or not os.path.exists(db_path):
        raise HTTPException(status_code=404, detail=f"Database '{db}' not found at {db_path}")

    conn = sqlite3.connect(db_path)
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()

    result = []
    for (name,) in tables:
        count = conn.execute(f"SELECT COUNT(*) FROM [{name}]").fetchone()[0]
        # Get column names
        cols = [row[1] for row in conn.execute(f"PRAGMA table_info([{name}])").fetchall()]
        result.append({"table": name, "rows": count, "columns": cols})
    conn.close()

    return {"database": db, "path": db_path, "tables": result}


@router.get("/admin/db/query")
async def query_table(
    secret: str = "",
    db: str = "run_archive",
    table: str = "web_runs",
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    order_by: str = "rowid DESC",
    columns: str = "",  # comma-separated, empty = all
):
    """Query rows from a table with pagination."""
    if not ADMIN_SECRET or secret != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")

    paths = _get_db_paths()
    db_path = paths.get(db)
    if not db_path or not os.path.exists(db_path):
        raise HTTPException(status_code=404, detail=f"Database '{db}' not found")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Validate table exists
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]
    if table not in tables:
        conn.close()
        raise HTTPException(status_code=404, detail=f"Table '{table}' not found")

    # Select columns
    if columns:
        col_list = ", ".join(c.strip() for c in columns.split(","))
    else:
        col_list = "*"

    # Exclude full_result_json by default (too large) unless explicitly requested
    if col_list == "*" and table == "web_runs":
        all_cols = [row[1] for row in conn.execute(f"PRAGMA table_info([{table}])").fetchall()]
        col_list = ", ".join(c for c in all_cols if c != "full_result_json")

    total = conn.execute(f"SELECT COUNT(*) FROM [{table}]").fetchone()[0]

    # Sanitize order_by to prevent SQL injection
    allowed_orders = ["rowid DESC", "rowid ASC", "run_at DESC", "run_at ASC",
                      "ticker ASC", "ticker DESC", "added_at DESC", "added_at ASC",
                      "created_at DESC", "cached_at DESC"]
    if order_by not in allowed_orders:
        order_by = "rowid DESC"

    rows = conn.execute(
        f"SELECT {col_list} FROM [{table}] ORDER BY {order_by} LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()

    result = [dict(r) for r in rows]
    conn.close()

    return {
        "database": db,
        "table": table,
        "total": total,
        "offset": offset,
        "limit": limit,
        "rows": result,
    }
