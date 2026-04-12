"""
src/memory/archive_admin.py
===========================
CLI utility to inspect and purge entries from run_archive.db.

All destructive operations are dry-run by default and require --confirm to execute.

Usage:
    python -m src.memory.archive_admin --status
    python -m src.memory.archive_admin --list
    python -m src.memory.archive_admin --purge-all
    python -m src.memory.archive_admin --purge-all --confirm
    python -m src.memory.archive_admin --purge-before 2026-03-26
    python -m src.memory.archive_admin --purge-before 2026-03-26 --confirm
    python -m src.memory.archive_admin --purge-ticker VST
    python -m src.memory.archive_admin --purge-ticker VST --confirm
    python -m src.memory.archive_admin --purge-run-id <uuid>
    python -m src.memory.archive_admin --purge-run-id <uuid> --confirm
    python -m src.memory.archive_admin --reset-weights
    python -m src.memory.archive_admin --reset-weights --confirm
"""

import argparse
import json
import os
import sqlite3
from datetime import datetime

from src.memory.run_archive import DB_PATH

_WEIGHTS_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "conviction_weights.json")

_AGENTS = [
    "damodaran", "graham", "ackman", "cathie_wood", "munger",
    "burry", "pabrai", "lynch", "fisher", "jhunjhunwala",
    "druckenmiller", "buffett",
]

_CHILD_TABLES = ("agent_signals", "ticker_signals")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _connect() -> sqlite3.Connection:
    if not os.path.exists(DB_PATH):
        print(f"[archive_admin] Database not found at: {DB_PATH}")
        raise SystemExit(1)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _run_ids_for(conn: sqlite3.Connection, where_clause: str, params: tuple) -> list[str]:
    rows = conn.execute(f"SELECT run_id FROM runs WHERE {where_clause}", params).fetchall()
    return [r["run_id"] for r in rows]


def _delete_runs(conn: sqlite3.Connection, run_ids: list[str]) -> dict[str, int]:
    """Delete runs and all child rows. Returns counts per table."""
    if not run_ids:
        return {}
    placeholders = ",".join("?" * len(run_ids))
    counts: dict[str, int] = {}
    for table in _CHILD_TABLES:
        cur = conn.execute(f"DELETE FROM {table} WHERE run_id IN ({placeholders})", run_ids)
        counts[table] = cur.rowcount
    cur = conn.execute(f"DELETE FROM runs WHERE run_id IN ({placeholders})", run_ids)
    counts["runs"] = cur.rowcount
    # rotation_events may or may not exist
    try:
        cur = conn.execute(f"DELETE FROM rotation_events WHERE run_id IN ({placeholders})", run_ids)
        counts["rotation_events"] = cur.rowcount
    except sqlite3.OperationalError:
        pass
    return counts


def _print_counts(counts: dict[str, int], dry_run: bool) -> None:
    prefix = "[DRY RUN] Would delete" if dry_run else "Deleted"
    for table, n in counts.items():
        print(f"  {prefix} {n} row(s) from {table}")


# ── Commands ──────────────────────────────────────────────────────────────────

def show_status() -> None:
    """Print row counts per table and current conviction weights."""
    conn = _connect()
    tables = ["runs", "ticker_signals", "agent_signals"]
    try:
        tables.append("rotation_events")
        conn.execute("SELECT 1 FROM rotation_events LIMIT 1")
    except sqlite3.OperationalError:
        tables.pop()

    print("\n-- run_archive.db status ---------------------------------")
    for t in tables:
        n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"  {t:<25} {n:>6} row(s)")

    # Outcome breakdown for ticker_signals
    try:
        rows = conn.execute(
            "SELECT outcome, COUNT(*) AS n FROM ticker_signals GROUP BY outcome"
        ).fetchall()
        if rows:
            print("\n  ticker_signals outcomes:")
            for r in rows:
                print(f"    {r[0] or 'NULL':<12} {r[1]:>5}")
    except sqlite3.OperationalError:
        pass

    conn.close()

    print("\n-- conviction_weights.json -------------------------------")
    if os.path.exists(_WEIGHTS_PATH):
        with open(_WEIGHTS_PATH) as f:
            w = json.load(f)
        for k, v in w.items():
            if k not in ("last_updated", "total_reviews"):
                print(f"  {k:<20} {v}")
        print(f"  last_updated: {w.get('last_updated', '?')}")
        print(f"  total_reviews: {w.get('total_reviews', 0)}")
    else:
        print("  File not found.")
    print()


def list_runs() -> None:
    """Print a summary table of all runs."""
    conn = _connect()
    runs = conn.execute(
        "SELECT run_id, run_at, tickers, sector, research_tier FROM runs ORDER BY run_at DESC"
    ).fetchall()
    if not runs:
        print("\nNo runs in archive.")
        conn.close()
        return

    print(f"\n{'run_id (short)':<14}  {'run_at':<22}  {'tickers':<24}  {'sector':<14}  {'tier'}")
    print("-" * 90)
    for r in runs:
        short_id = r["run_id"][:12]
        tickers_str = ", ".join(json.loads(r["tickers"] or "[]"))[:22]
        print(
            f"{short_id:<14}  {r['run_at'][:22]:<22}  {tickers_str:<24}  "
            f"{(r['sector'] or '?'):<14}  {r['research_tier'] or '?'}"
        )
    print(f"\nTotal: {len(runs)} run(s)")
    conn.close()


def purge_all(confirm: bool) -> None:
    """Delete all rows from all tables."""
    conn = _connect()
    n = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    if n == 0:
        print("Archive is already empty.")
        conn.close()
        return

    run_ids = [r["run_id"] for r in conn.execute("SELECT run_id FROM runs").fetchall()]
    counts = _delete_runs(conn, run_ids)

    if not confirm:
        print(f"\n[DRY RUN] Would delete all {n} run(s) and all child rows:")
        _print_counts(counts, dry_run=True)
        print("\nRe-run with --confirm to execute.")
        conn.close()
        return

    conn.commit()
    print(f"\nPurged all {n} run(s):")
    _print_counts(counts, dry_run=False)
    conn.close()


def purge_before(date_str: str, confirm: bool) -> None:
    """Delete runs archived before date_str (YYYY-MM-DD)."""
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        print(f"Invalid date format: {date_str!r}. Use YYYY-MM-DD.")
        raise SystemExit(1)

    conn = _connect()
    run_ids = _run_ids_for(conn, "run_at < ?", (date_str,))
    if not run_ids:
        print(f"\nNo runs found before {date_str}.")
        conn.close()
        return

    counts = _delete_runs(conn, run_ids)

    if not confirm:
        print(f"\n[DRY RUN] Would delete {len(run_ids)} run(s) before {date_str}:")
        _print_counts(counts, dry_run=True)
        print("\nRe-run with --confirm to execute.")
        conn.close()
        return

    conn.commit()
    print(f"\nDeleted {len(run_ids)} run(s) before {date_str}:")
    _print_counts(counts, dry_run=False)
    conn.close()


def purge_ticker(ticker: str, confirm: bool) -> None:
    """Delete all ticker_signals and agent_signals for a ticker; clean up orphaned runs."""
    ticker = ticker.upper()
    conn = _connect()

    # Find runs that contain this ticker
    all_runs = conn.execute("SELECT run_id, tickers FROM runs").fetchall()
    affected_run_ids = [
        r["run_id"] for r in all_runs
        if ticker in json.loads(r["tickers"] or "[]")
    ]

    if not affected_run_ids:
        print(f"\nNo runs found containing ticker {ticker}.")
        conn.close()
        return

    placeholders = ",".join("?" * len(affected_run_ids))

    # Count what will be deleted from child tables for this ticker
    ts_count = conn.execute(
        f"SELECT COUNT(*) FROM ticker_signals WHERE run_id IN ({placeholders}) AND ticker=?",
        affected_run_ids + [ticker]
    ).fetchone()[0]
    ag_count = conn.execute(
        f"SELECT COUNT(*) FROM agent_signals WHERE run_id IN ({placeholders}) AND ticker=?",
        affected_run_ids + [ticker]
    ).fetchone()[0]

    # Identify runs that will become empty (only had this ticker) → will be orphaned
    orphan_run_ids = []
    for run_id in affected_run_ids:
        tickers_in_run = json.loads(
            conn.execute("SELECT tickers FROM runs WHERE run_id=?", (run_id,)).fetchone()["tickers"] or "[]"
        )
        if set(tickers_in_run) == {ticker}:
            orphan_run_ids.append(run_id)

    if not confirm:
        print(f"\n[DRY RUN] Would delete {ticker} data from {len(affected_run_ids)} run(s):")
        print(f"  ticker_signals rows: {ts_count}")
        print(f"  agent_signals rows:  {ag_count}")
        print(f"  orphaned runs to remove: {len(orphan_run_ids)}")
        print("\nRe-run with --confirm to execute.")
        conn.close()
        return

    conn.execute(
        f"DELETE FROM agent_signals WHERE run_id IN ({placeholders}) AND ticker=?",
        affected_run_ids + [ticker]
    )
    conn.execute(
        f"DELETE FROM ticker_signals WHERE run_id IN ({placeholders}) AND ticker=?",
        affected_run_ids + [ticker]
    )
    if orphan_run_ids:
        oph = ",".join("?" * len(orphan_run_ids))
        conn.execute(f"DELETE FROM runs WHERE run_id IN ({oph})", orphan_run_ids)

    conn.commit()
    print(f"\nDeleted {ticker} data:")
    print(f"  ticker_signals rows: {ts_count}")
    print(f"  agent_signals rows:  {ag_count}")
    print(f"  orphaned runs removed: {len(orphan_run_ids)}")
    conn.close()


def purge_run_id(run_id: str, confirm: bool) -> None:
    """Delete a single run by its UUID (prefix match supported)."""
    conn = _connect()
    # Support prefix match
    matches = conn.execute(
        "SELECT run_id FROM runs WHERE run_id LIKE ?", (run_id + "%",)
    ).fetchall()
    if not matches:
        print(f"\nNo run found matching ID prefix: {run_id!r}")
        conn.close()
        return
    if len(matches) > 1:
        print(f"\nPrefix {run_id!r} matches {len(matches)} runs — provide a longer prefix.")
        for m in matches:
            print(f"  {m['run_id']}")
        conn.close()
        return

    full_id = matches[0]["run_id"]
    counts = _delete_runs(conn, [full_id])

    if not confirm:
        print(f"\n[DRY RUN] Would delete run {full_id}:")
        _print_counts(counts, dry_run=True)
        print("\nRe-run with --confirm to execute.")
        conn.close()
        return

    conn.commit()
    print(f"\nDeleted run {full_id}:")
    _print_counts(counts, dry_run=False)
    conn.close()


def reset_conviction_weights(confirm: bool) -> None:
    """Reset conviction_weights.json to 1.0 defaults."""
    if not confirm:
        print("\n[DRY RUN] Would reset conviction_weights.json to all 1.0 defaults.")
        print("Re-run with --confirm to execute.")
        return

    weights = {agent: 1.0 for agent in _AGENTS}
    weights["last_updated"] = datetime.now().strftime("%Y-%m-%d")
    weights["total_reviews"] = 0
    with open(_WEIGHTS_PATH, "w") as f:
        json.dump(weights, f, indent=2)
    print("\nconviction_weights.json reset to 1.0 defaults.")


# ── CLI entry point ───────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Archive admin utility for run_archive.db",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--status", action="store_true", help="Row counts + current conviction weights")
    parser.add_argument("--list", action="store_true", help="List all runs (read-only)")
    parser.add_argument("--purge-all", action="store_true", help="Delete all runs")
    parser.add_argument("--purge-before", metavar="DATE", help="Delete runs archived before DATE (YYYY-MM-DD)")
    parser.add_argument("--purge-ticker", metavar="TICKER", help="Delete all data for a specific ticker")
    parser.add_argument("--purge-run-id", metavar="UUID", help="Delete a single run by UUID (prefix ok)")
    parser.add_argument("--reset-weights", action="store_true", help="Reset conviction_weights.json to 1.0")
    parser.add_argument("--confirm", action="store_true", help="Required to execute any destructive operation")
    args = parser.parse_args()

    if args.status:
        show_status()
    elif args.list:
        list_runs()
    elif args.purge_all:
        purge_all(args.confirm)
    elif args.purge_before:
        purge_before(args.purge_before, args.confirm)
    elif args.purge_ticker:
        purge_ticker(args.purge_ticker, args.confirm)
    elif args.purge_run_id:
        purge_run_id(args.purge_run_id, args.confirm)
    elif args.reset_weights:
        reset_conviction_weights(args.confirm)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
