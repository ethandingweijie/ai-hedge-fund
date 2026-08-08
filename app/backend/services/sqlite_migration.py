"""
app/backend/services/sqlite_migration.py
==========================================
One-shot data migration: volume SQLite databases -> PostgreSQL.

Sources (same env resolution as routes/admin.py::_get_db_paths):
- hedge_fund.db   (DATABASE_PATH)    ORM tables: users, flows, api_keys, chat…
- run_archive.db  (RUN_ARCHIVE_PATH) archive/screener tables: web_runs,
  master_universe, hq_*, complacency_*, caches…

Target: DATABASE_URL (PostgreSQL). Refuses to run when unset.

Behaviour
- Tables missing from Postgres are created with a deliberately boring schema
  derived from SQLite PRAGMA table_info (single INTEGER pk -> BIGINT identity,
  INT->BIGINT, REAL/FLOAT->DOUBLE PRECISION, BLOB->BYTEA, everything else
  TEXT). Owning modules keep authority over their final schema.
- Existing Postgres tables are reused; source columns missing from the target
  are added via ALTER TABLE ADD COLUMN.
- Rows are copied INSERT ... ON CONFLICT DO NOTHING -> idempotent, re-runnable.
- Values are coerced per target column type (SQLite is dynamically typed;
  e.g. numeric stored in a TEXT column becomes a string, and vice versa).
- After the copy, sequences behind SERIAL/IDENTITY primary keys are bumped
  past MAX(id) so future inserts don't collide with migrated ids.
- Empty source tables are skipped entirely (their owning module will create
  them with the proper schema when it is migrated).
- sqlite_sequence and other sqlite_% internals are never copied.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from typing import Any, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_busy = False

_EXCLUDE_TABLES = {"sqlite_sequence"}
_CHUNK = 2000

# FK-safe copy order for the ORM database. Anything not listed is appended
# afterwards in sqlite_master order.
_HEDGE_FUND_ORDER = [
    "users",
    "api_keys",
    "hedge_fund_flows",
    "hedge_fund_flow_runs",
    "hedge_fund_flow_run_cycles",
    "chat_messages",
    "chat_reactions",
    "research_summary_cache",
]


def is_busy() -> bool:
    return _busy


# ── Connection helpers ────────────────────────────────────────────────────────

def _pg_url() -> str:
    """DATABASE_URL normalised for libpq (no SQLAlchemy driver suffixes)."""
    url = os.environ.get("DATABASE_URL", "")
    for prefix in ("postgresql+psycopg://", "postgres+psycopg://"):
        if url.startswith(prefix):
            return "postgresql://" + url[len(prefix):]
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://"):]
    return url


def _mask_url(url: str) -> str:
    try:
        p = urlparse(url)
        host = p.hostname or "?"
        port = f":{p.port}" if p.port else ""
        user = f"{p.username}:***@" if p.username else ""
        return f"{p.scheme}://{user}{host}{port}{p.path}"
    except Exception:
        return "***"


def _source_paths() -> dict[str, str]:
    return {
        "hedge_fund": os.environ.get("DATABASE_PATH", "hedge_fund.db"),
        "run_archive": os.environ.get("RUN_ARCHIVE_PATH", "run_archive.db"),
    }


def _open_sqlite_ro(path: str) -> sqlite3.Connection:
    """Open read-only when possible; fall back to a normal connection
    (read-only URI opens can fail on WAL databases without a -shm file)."""
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.execute("SELECT 1")
        return conn
    except sqlite3.Error:
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA query_only = ON")
        return conn


def _q(ident: str) -> str:
    return '"' + ident.replace('"', '""') + '"'


# ── SQLite introspection ─────────────────────────────────────────────────────

def _sqlite_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [r[0] for r in rows]


def _ordered_tables(conn: sqlite3.Connection, preferred: list[str]) -> list[str]:
    tables = _sqlite_tables(conn)
    ordered = [t for t in preferred if t in tables]
    ordered += [t for t in tables if t not in ordered]
    return ordered


def _sqlite_columns(conn: sqlite3.Connection, table: str) -> list[dict]:
    rows = conn.execute(f"PRAGMA table_info({_q(table)})").fetchall()
    # (cid, name, type, notnull, dflt_value, pk)
    return [
        {"name": r[1], "type": r[2] or "", "pk": int(r[5] or 0)}
        for r in rows
    ]


# ── Postgres schema helpers ──────────────────────────────────────────────────

def _pg_type(declared: str) -> str:
    d = (declared or "").strip().upper()
    if not d:
        return "TEXT"
    if "INT" in d:
        return "BIGINT"
    if "CHAR" in d or "CLOB" in d or "TEXT" in d:
        return "TEXT"
    if "BLOB" in d:
        return "BYTEA"
    if "REAL" in d or "FLOA" in d or "DOUB" in d or "NUMERIC" in d or "DEC" in d:
        return "DOUBLE PRECISION"
    return "TEXT"  # DATETIME / BOOLEAN / JSON / unknown -> keep as text (safest)


def _table_exists(cur, table: str) -> bool:
    cur.execute("SELECT to_regclass(%s)", (f"public.{_q(table)}",))
    return cur.fetchone()[0] is not None


def _pg_column_types(cur, table: str) -> dict[str, str]:
    cur.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = %s",
        (table,),
    )
    return {r[0]: r[1] for r in cur.fetchall()}


def _create_table_sql(table: str, cols: list[dict]) -> str:
    pk_cols = sorted((c for c in cols if c["pk"] > 0), key=lambda c: c["pk"])
    single_int_pk = len(pk_cols) == 1 and _pg_type(pk_cols[0]["type"]) == "BIGINT"
    parts = []
    for c in cols:
        if single_int_pk and c["pk"] == 1:
            parts.append(f"{_q(c['name'])} BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY")
        else:
            parts.append(f"{_q(c['name'])} {_pg_type(c['type'])}")
    if pk_cols and not single_int_pk:
        parts.append("PRIMARY KEY (" + ", ".join(_q(c["name"]) for c in pk_cols) + ")")
    return f"CREATE TABLE IF NOT EXISTS {_q(table)} ({', '.join(parts)})"


# ── Value coercion ───────────────────────────────────────────────────────────

def _coerce(value: Any, pg_type: Optional[str]) -> Any:
    """Adapt a SQLite value to the declared Postgres column type.

    SQLite columns are dynamically typed, so a TEXT column may hold numbers
    and vice versa; Postgres rejects the mismatch unless we convert.
    """
    if value is None or pg_type is None:
        return value
    if pg_type in ("text", "character varying", "character"):
        if isinstance(value, bytes):
            return value.decode("utf-8", "replace")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
        return value
    if pg_type == "bytea":
        if isinstance(value, str):
            return value.encode("utf-8")
        return value
    if pg_type == "boolean":
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "t", "yes", "y")
        return bool(value)
    if pg_type in ("bigint", "integer", "smallint"):
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, str):
            try:
                return int(value)
            except ValueError:
                try:
                    return int(float(value))
                except (ValueError, OverflowError):
                    return None
        if isinstance(value, float):
            return int(value)
        return value
    if pg_type in ("double precision", "real", "numeric"):
        if isinstance(value, bool):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                return None
        return value
    # timestamp / timestamptz / date / time / json / anything else:
    # pass through and let Postgres parse (ISO strings from SQLite work).
    return value


# ── Per-table copy ───────────────────────────────────────────────────────────

def _migrate_table(pg_cur, sq_conn: sqlite3.Connection, table: str, dry_run: bool) -> dict:
    from psycopg import sql as psql

    info: dict[str, Any] = {
        "table": table,
        "source_rows": 0,
        "rows_read": 0,
        "rows_inserted": 0,
        "created_table": False,
        "added_columns": [],
        "errors": [],
    }

    cols = _sqlite_columns(sq_conn, table)
    if not cols:
        info["errors"].append("PRAGMA table_info returned no columns")
        return info

    count = sq_conn.execute(f"SELECT COUNT(*) FROM {_q(table)}").fetchone()[0]
    info["source_rows"] = count
    if count == 0:
        info["skipped_empty"] = True
        return info

    # Ensure the target table exists and has every source column
    exists = _table_exists(pg_cur, table)
    if not exists:
        info["created_table"] = True
        if not dry_run:
            pg_cur.execute(_create_table_sql(table, cols))
    else:
        target_cols = _pg_column_types(pg_cur, table)
        for c in cols:
            if c["name"] not in target_cols:
                info["added_columns"].append(c["name"])
                if not dry_run:
                    pg_cur.execute(
                        f"ALTER TABLE {_q(table)} ADD COLUMN {_q(c['name'])} {_pg_type(c['type'])}"
                    )

    col_names = [c["name"] for c in cols]

    # Read source rows (ORDER BY rowid for stable chunking; some tables are
    # WITHOUT ROWID — fall back to unordered if rowid is unavailable).
    sel = f"SELECT {', '.join(_q(c) for c in col_names)} FROM {_q(table)}"
    try:
        rows = sq_conn.execute(f"{sel} ORDER BY rowid").fetchall()
    except sqlite3.OperationalError:
        rows = sq_conn.execute(sel).fetchall()
    info["rows_read"] = len(rows)

    if dry_run:
        return info

    target_types = _pg_column_types(pg_cur, table)
    stmt = psql.SQL("INSERT INTO {} ({}) VALUES ({}) ON CONFLICT DO NOTHING").format(
        psql.Identifier(table),
        psql.SQL(", ").join(psql.Identifier(c) for c in col_names),
        psql.SQL(", ").join(psql.Placeholder(name=c) for c in col_names),
    )

    def _record(values: tuple) -> dict:
        return {c: _coerce(v, target_types.get(c)) for c, v in zip(col_names, values)}

    for start in range(0, len(rows), _CHUNK):
        chunk = [_record(r) for r in rows[start:start + _CHUNK]]
        try:
            pg_cur.executemany(stmt, chunk)
            info["rows_inserted"] += max(pg_cur.rowcount, 0)
        except Exception as exc:
            # One bad row must not sink the table — retry row by row.
            logger.warning("migrate %s: batch insert failed (%s) — retrying row-by-row", table, exc)
            for rec in chunk:
                try:
                    pg_cur.executemany(stmt, [rec])
                    info["rows_inserted"] += max(pg_cur.rowcount, 0)
                except Exception as row_exc:
                    info["errors"].append(f"row skipped: {row_exc}")
    if len(info["errors"]) > 5:
        info["errors"] = info["errors"][:5] + [f"… {len(info['errors']) - 5} more"]
    return info


def _fix_sequences(cur) -> list[str]:
    """Bump every sequence-backed PK past MAX(id) so new inserts don't collide."""
    cur.execute(
        """
        SELECT c.relname, a.attname
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
        JOIN pg_attrdef d ON d.adrelid = c.oid AND d.adnum = a.attnum
        WHERE c.relkind = 'r' AND n.nspname = 'public'
          AND pg_get_expr(d.adbin, d.adrelid) ILIKE %s
        """,
        ("nextval%",),
    )
    fixed = []
    for tbl, col in cur.fetchall():
        try:
            cur.execute("SELECT pg_get_serial_sequence(%s, %s)", (f"public.{_q(tbl)}", col))
            seq = cur.fetchone()[0]
            if not seq:
                continue
            cur.execute(
                f"SELECT setval(%s::regclass, COALESCE(MAX({_q(col)}), 1), MAX({_q(col)}) IS NOT NULL) FROM {_q(tbl)}",
                (seq,),
            )
            fixed.append(f"{tbl}.{col} -> {seq}")
        except Exception as exc:
            logger.warning("sequence fix failed for %s.%s: %s", tbl, col, exc)
    return fixed


def _has_unique_constraint(cur, table: str) -> bool:
    """PK, UNIQUE constraint, or UNIQUE index -> ON CONFLICT has a target."""
    cur.execute(
        """
        SELECT 1 FROM pg_constraint con
        JOIN pg_class rel ON rel.oid = con.conrelid
        WHERE rel.relname = %s AND con.contype IN ('p', 'u')
        UNION ALL
        SELECT 1 FROM pg_indexes
        WHERE schemaname = 'public' AND tablename = %s AND indexdef ILIKE %s
        LIMIT 1
        """,
        (table, table, "%UNIQUE%"),
    )
    return cur.fetchone() is not None


def _dedup_table(cur, table: str) -> int:
    """Rebuild a constraint-less table keeping only distinct rows.

    Re-runs of the migration duplicate rows in tables without any PK/unique
    constraint (ON CONFLICT DO NOTHING has nothing to trigger on). Rows are
    identical copies, so SELECT DISTINCT loses nothing.
    """
    tmp = f"{table}_mig_dedup"
    cur.execute(f"DROP TABLE IF EXISTS {_q(tmp)}")
    cur.execute(f"CREATE TABLE {_q(tmp)} AS SELECT DISTINCT * FROM {_q(table)}")
    cur.execute(f"SELECT COUNT(*) FROM {_q(tmp)}")
    kept = cur.fetchone()[0]
    cur.execute(f"DROP TABLE {_q(table)}")
    cur.execute(f"ALTER TABLE {_q(tmp)} RENAME TO {_q(table)}")
    return kept


# ── Entry point ──────────────────────────────────────────────────────────────

def run_migration(dry_run: bool = False) -> dict:
    """Copy both volume SQLite databases into Postgres. Returns a report dict.

    Raises RuntimeError when prerequisites are missing (no DATABASE_URL,
    migration already running).
    """
    global _busy

    if not os.environ.get("DATABASE_URL"):
        raise RuntimeError("DATABASE_URL is not set — no Postgres target available")

    if not _lock.acquire(blocking=False):
        raise RuntimeError("Migration already in progress")
    _busy = True
    started = time.time()

    report: dict[str, Any] = {
        "dry_run": dry_run,
        "target": _mask_url(_pg_url()),
        "sources": _source_paths(),
        "tables": [],
        "skipped_empty": [],
        "sequences_fixed": [],
        "errors": [],
    }

    try:
        import psycopg
        try:
            pg_conn = psycopg.connect(_pg_url(), autocommit=True)
        except Exception as exc:
            raise RuntimeError(f"Cannot connect to Postgres: {exc}") from exc

        try:
            with pg_conn.cursor() as cur:
                for db_name, path in _source_paths().items():
                    if not os.path.exists(path):
                        report["errors"].append(f"{db_name}: SQLite file not found at {path}")
                        continue
                    preferred = _HEDGE_FUND_ORDER if db_name == "hedge_fund" else []
                    sq = _open_sqlite_ro(path)
                    try:
                        for table in _ordered_tables(sq, preferred):
                            if table in _EXCLUDE_TABLES:
                                continue
                            try:
                                info = _migrate_table(cur, sq, table, dry_run)
                            except Exception as exc:
                                logger.exception("migrate %s.%s failed", db_name, table)
                                info = {"table": table, "errors": [str(exc)]}
                            info["db"] = db_name
                            if info.get("skipped_empty"):
                                report["skipped_empty"].append(f"{db_name}.{table}")
                            else:
                                report["tables"].append(info)
                    finally:
                        sq.close()
                if not dry_run:
                    # Verify landed row counts; repair constraint-less tables
                    # that accumulated duplicates across repeated runs.
                    for t in report["tables"]:
                        try:
                            cur.execute(f"SELECT COUNT(*) FROM {_q(t['table'])}")
                            t["pg_rows"] = cur.fetchone()[0]
                        except Exception as exc:
                            t["pg_rows"] = f"error: {exc}"
                            continue
                        if (isinstance(t["pg_rows"], int)
                                and t["pg_rows"] > t.get("source_rows", 0)
                                and not _has_unique_constraint(cur, t["table"])):
                            t["deduped_to"] = _dedup_table(cur, t["table"])
                            logger.info("deduped %s: %d -> %d rows",
                                        t["table"], t["pg_rows"], t["deduped_to"])
                            t["pg_rows"] = t["deduped_to"]
                    report["sequences_fixed"] = _fix_sequences(cur)
        finally:
            pg_conn.close()
    finally:
        _busy = False
        _lock.release()

    report["duration_sec"] = round(time.time() - started, 1)
    inserted = sum(t.get("rows_inserted", 0) for t in report["tables"])
    read = sum(t.get("rows_read", 0) for t in report["tables"])
    report["totals"] = {"rows_read": read, "rows_inserted": inserted,
                        "tables_copied": len(report["tables"]),
                        "tables_skipped_empty": len(report["skipped_empty"])}
    logger.info("sqlite->postgres migration done: %d/%d rows across %d tables in %.1fs",
                inserted, read, len(report["tables"]), report["duration_sec"])
    return report
