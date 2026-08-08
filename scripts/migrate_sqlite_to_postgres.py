"""
One-shot SQLite -> PostgreSQL data migration.

Copies the two volume SQLite databases (hedge_fund.db + run_archive.db) into
the DATABASE_URL Postgres instance. Idempotent — rows are inserted with
ON CONFLICT DO NOTHING and sequences are bumped afterwards, so re-running is
safe.

Run inside the Railway container (where both the volume files and Postgres
are reachable):

    python scripts/migrate_sqlite_to_postgres.py             # migrate
    python scripts/migrate_sqlite_to_postgres.py --dry-run   # inventory only

Or hit the admin endpoint instead (same code):
    GET /admin/migrate-to-postgres?secret=***&dry_run=true
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.backend.services.sqlite_migration import run_migration  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate volume SQLite data to Postgres")
    parser.add_argument("--dry-run", action="store_true",
                        help="inventory source tables without writing to Postgres")
    args = parser.parse_args()

    try:
        report = run_migration(dry_run=args.dry_run)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
