"""
Unified database connection manager for the run_archive database.

This module replaces the duplicated _get_conn() / _connect() patterns across
storage modules with a single, centralized connection layer.

Supports both SQLite (local development) and PostgreSQL (production):
- SQLite: thread-local connection with WAL mode.
- PostgreSQL: psycopg_pool.ConnectionPool (falls back to plain connections).

Row contract: rows support NAME-BASED access (row["col"]). SQLite returns
sqlite3.Row; Postgres returns dict rows. Migrated callers must use name
access, not positional indexing.

Usage:
    from src.data import db

    rows = db.query("SELECT * FROM watchlist WHERE user_id = ?", [user_id])
    db.execute("UPDATE jobs SET status = ? WHERE job_id = ?", ["failed", jid])
"""

import logging
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

logger = logging.getLogger(__name__)

# Thread-local storage for SQLite connections
_thread_local = threading.local()

# Default path for SQLite database
_DATA_DIR = Path(__file__).parent
_DEFAULT_DB_PATH = _DATA_DIR / "run_archive.db"

# Postgres connection pool (initialized on first use)
_pg_pool = None
_pg_pool_lock = threading.Lock()


def get_db_path() -> str:
    """Get the path to the run_archive database.

    Uses RUN_ARCHIVE_PATH env var if set, otherwise falls back to
    the default src/data/run_archive.db path.
    """
    return os.environ.get("RUN_ARCHIVE_PATH", str(_DEFAULT_DB_PATH))


def is_postgres() -> bool:
    """Check if the app is running with PostgreSQL (production) or SQLite (local)."""
    return bool(os.environ.get("DATABASE_URL"))


def _pg_conninfo() -> str:
    """DATABASE_URL normalized for libpq (psycopg parses URLs natively)."""
    url = os.environ["DATABASE_URL"]
    if url.startswith("postgresql+psycopg://"):
        url = url.replace("postgresql+psycopg://", "postgresql://", 1)
    return url


def _get_pg_pool():
    """Get or create the PostgreSQL connection pool."""
    global _pg_pool
    if _pg_pool is None:
        with _pg_pool_lock:
            if _pg_pool is None:
                try:
                    import psycopg_pool
                    _pg_pool = psycopg_pool.ConnectionPool(
                        conninfo=_pg_conninfo(),
                        min_size=2,
                        max_size=20,
                        timeout=30,
                        kwargs={"autocommit": False},
                    )
                    logger.info("run_archive: PostgreSQL connection pool created")
                except ImportError:
                    logger.warning("psycopg_pool unavailable — using plain connections")
                    _pg_pool = _PlainPgFallback()
    return _pg_pool


class _PlainPgFallback:
    """Minimal stand-in when psycopg_pool is not installed."""

    def connection(self):
        import psycopg
        return _PlainPgConn(psycopg.connect(_pg_conninfo()))


class _PlainPgConn:
    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self._conn

    def __exit__(self, *exc):
        self._conn.close()
        return False


def _get_sqlite_connection() -> sqlite3.Connection:
    """Get or create a SQLite connection for the current thread.

    The resolved path is cached alongside the connection and rechecked on
    every call. `get_db_path()` is lazy, but it used to be consulted only
    when the connection was first created — so once anything opened the
    default archive, a later `RUN_ARCHIVE_PATH` change was silently
    ignored for the life of the thread and every read kept hitting the
    old database. That made test isolation depend on import order: a test
    pointing at a fresh tmp store got whatever database an earlier import
    had already opened.
    """
    db_path = get_db_path()
    if (getattr(_thread_local, "conn", None) is not None
            and getattr(_thread_local, "conn_path", None) != db_path):
        try:
            _thread_local.conn.close()
        except Exception:
            pass
        _thread_local.conn = None

    if not hasattr(_thread_local, "conn") or _thread_local.conn is None:
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)

        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")

        _thread_local.conn = conn
        _thread_local.conn_path = db_path
    return _thread_local.conn


def _translate(sql: str) -> str:
    """Convert SQLite-style ? placeholders to Postgres %s."""
    return sql.replace("?", "%s")


@contextmanager
def _pg_cursor():
    """Yield (conn, cursor) from the pool, dict-row factory, commit on success."""
    from psycopg.rows import dict_row
    pool = _get_pg_pool()
    with pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            try:
                yield conn, cur
                conn.commit()
            except Exception:
                conn.rollback()
                raise


def query(sql: str, params: Optional[list] = None) -> list:
    """Execute a SELECT and return all rows (name-accessible)."""
    params = params or []
    if is_postgres():
        with _pg_cursor() as (conn, cur):
            cur.execute(_translate(sql), params)
            return cur.fetchall()
    conn = _get_sqlite_connection()
    return conn.execute(sql, params).fetchall()


def query_one(sql: str, params: Optional[list] = None):
    """Execute a SELECT and return the first row or None."""
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: Optional[list] = None) -> int:
    """Execute an INSERT/UPDATE/DELETE and return rowcount."""
    params = params or []
    if is_postgres():
        with _pg_cursor() as (conn, cur):
            cur.execute(_translate(sql), params)
            return cur.rowcount
    conn = _get_sqlite_connection()
    cur = conn.execute(sql, params)
    conn.commit()
    return cur.rowcount


def executemany(sql: str, params_list: list) -> int:
    """Execute a statement with multiple parameter sets; returns rowcount."""
    if is_postgres():
        with _pg_cursor() as (conn, cur):
            cur.executemany(_translate(sql), params_list)
            return cur.rowcount
    conn = _get_sqlite_connection()
    cur = conn.executemany(sql, params_list)
    conn.commit()
    return cur.rowcount


def execute_script(ddl_script: str) -> None:
    """Execute a multi-statement DDL script (SQLite executescript equivalent)."""
    if is_postgres():
        # Split on semicolons; run each non-empty statement individually.
        statements = [s.strip() for s in ddl_script.split(";") if s.strip()]
        with _pg_cursor() as (conn, cur):
            for stmt in statements:
                cur.execute(stmt)
    else:
        conn = _get_sqlite_connection()
        conn.executescript(ddl_script)
        conn.commit()


def table_exists(table_name: str) -> bool:
    """Check if a table exists in the database."""
    if is_postgres():
        row = query_one(
            "SELECT 1 AS one FROM information_schema.tables WHERE table_name = ?",
            [table_name],
        )
        return row is not None
    row = query_one(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        [table_name],
    )
    return row is not None


def column_exists(table_name: str, column_name: str) -> bool:
    """Check if a column exists on a table."""
    if is_postgres():
        row = query_one(
            "SELECT 1 AS one FROM information_schema.columns "
            "WHERE table_name = ? AND column_name = ?",
            [table_name, column_name],
        )
        return row is not None
    try:
        conn = _get_sqlite_connection()
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}
        return column_name in cols
    except Exception:
        return False


def add_column_if_missing(table_name: str, column_name: str, definition: str) -> None:
    """ALTER TABLE ADD COLUMN, no-op if the column already exists."""
    if column_exists(table_name, column_name):
        return
    try:
        execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")
    except Exception as exc:
        # Concurrent add from another process — re-check
        if not column_exists(table_name, column_name):
            raise
        logger.debug("add_column_if_missing raced (ok): %s", exc)


def ensure_table(ddl: str) -> None:
    """Ensure a table exists, creating it if necessary (CREATE TABLE IF NOT EXISTS)."""
    execute_script(ddl)


def close_all_connections() -> None:
    """Close thread-local SQLite connections and the PG pool. Called on shutdown."""
    global _pg_pool
    if hasattr(_thread_local, "conn") and _thread_local.conn:
        try:
            _thread_local.conn.close()
        except Exception:
            pass
        _thread_local.conn = None
    if _pg_pool is not None and hasattr(_pg_pool, "close"):
        try:
            _pg_pool.close()
        except Exception:
            pass
        _pg_pool = None
