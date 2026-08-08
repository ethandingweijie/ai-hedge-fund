"""
Unified database connection manager for the run_archive.db.

This module replaces the 20+ duplicated _get_conn() / _connect() patterns
across storage modules with a single, centralized connection manager.

Supports both SQLite (local development) and PostgreSQL (production).

Usage:
    from src.data.db import get_db_path, get_connection, execute_query

    # Simple query
    rows = execute_query("SELECT * FROM watchlist WHERE user_id = ?", [user_id])

    # With context manager (auto-close)
    with connection() as conn:
        cursor = conn.execute("SELECT ...")
"""

import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

# Thread-local storage for connections
_thread_local = threading.local()

# Default path for SQLite database
_DATA_DIR = Path(__file__).parent
_DEFAULT_DB_PATH = _DATA_DIR / "run_archive.db"

# Lock for initialization
_init_lock = threading.Lock()
_initialized = False

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


def _get_pg_pool():
    """Get or create the PostgreSQL connection pool."""
    global _pg_pool
    if _pg_pool is None:
        with _pg_pool_lock:
            if _pg_pool is None:
                try:
                    import psycopg_pool
                    _pg_pool = psycopg_pool.ConnectionPool(
                        conninfo=os.environ["DATABASE_URL"],
                        min_size=5,
                        max_size=20,
                        timeout=30,
                    )
                except ImportError:
                    # Fall back to simple psycopg connections
                    import psycopg
                    _pg_pool = psycopg.connect(os.environ["DATABASE_URL"])
    return _pg_pool


def _get_sqlite_connection() -> sqlite3.Connection:
    """Get or create a SQLite connection for the current thread."""
    if not hasattr(_thread_local, 'conn') or _thread_local.conn is None:
        db_path = get_db_path()
        # Ensure directory exists
        os.makedirs(os.path.dirname(db_path), exist_ok=True)

        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row

        # Set SQLite pragmas for WAL mode and concurrency
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")

        _thread_local.conn = conn
    return _thread_local.conn


def get_connection():
    """Get a database connection.

    For SQLite, returns a thread-local connection.
    For PostgreSQL, returns a connection from the pool.
    """
    if is_postgres():
        pool = _get_pg_pool()
        if hasattr(pool, 'connection'):
            return pool.connection()
        return pool
    return _get_sqlite_connection()


@contextmanager
def connection():
    """Context manager that yields a connection and closes it on exit.

    Usage:
        with connection() as conn:
            cursor = conn.execute("SELECT ...")
    """
    conn = get_connection()
    try:
        yield conn
    finally:
        if is_postgres():
            conn.close()
        else:
            conn.close()
            _thread_local.conn = None


def execute_query(query: str, params: list = None) -> list:
    """Execute a query and return all rows.

    Args:
        query: SQL query with ? placeholders (SQLite) or %s (Postgres)
        params: List of parameters to bind

    Returns:
        List of Row objects
    """
    conn = get_connection()
    if is_postgres():
        # Convert ? to %s for Postgres
        query = query.replace('?', '%s')
        with conn.cursor() as cursor:
            cursor.execute(query, params or [])
            return cursor.fetchall()
    else:
        cursor = conn.execute(query, params or [])
        return cursor.fetchall()


def execute_insert(query: str, params: list = None) -> int:
    """Execute an INSERT/UPDATE/DELETE and return rowcount.

    Args:
        query: SQL query with ? placeholders (SQLite) or %s (Postgres)
        params: List of parameters to bind

    Returns:
        Number of rows affected
    """
    conn = get_connection()
    if is_postgres():
        query = query.replace('?', '%s')
        with conn.cursor() as cursor:
            cursor.execute(query, params or [])
            conn.commit()
            return cursor.rowcount
    else:
        cursor = conn.execute(query, params or [])
        conn.commit()
        return cursor.rowcount


def execute_many(query: str, params_list: list) -> int:
    """Execute a query with multiple parameter sets.

    Args:
        query: SQL query with ? placeholders (SQLite) or %s (Postgres)
        params_list: List of parameter lists

    Returns:
        Number of rows affected
    """
    conn = get_connection()
    if is_postgres():
        query = query.replace('?', '%s')
        with conn.cursor() as cursor:
            cursor.executemany(query, params_list)
            conn.commit()
            return cursor.rowcount
    else:
        cursor = conn.executemany(query, params_list)
        conn.commit()
        return cursor.rowcount


def table_exists(table_name: str) -> bool:
    """Check if a table exists in the database."""
    conn = get_connection()
    if is_postgres():
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_name = %s",
                [table_name]
            )
            return cursor.fetchone() is not None
    else:
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            [table_name]
        )
        return cursor.fetchone() is not None


def ensure_table(ddl: str) -> None:
    """Ensure a table exists, creating it if necessary.

    Args:
        ddl: CREATE TABLE IF NOT EXISTS statement
    """
    conn = get_connection()
    if is_postgres():
        with conn.cursor() as cursor:
            cursor.execute(ddl)
            conn.commit()
    else:
        conn.execute(ddl)
        conn.commit()


def close_all_connections() -> None:
    """Close all thread-local connections. Called on shutdown."""
    if hasattr(_thread_local, 'conn') and _thread_local.conn:
        _thread_local.conn.close()
        _thread_local.conn = None
