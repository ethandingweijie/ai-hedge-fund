"""
Idempotent schema bootstrap for Railway deployments.

What it does, in order:
1. Base.metadata.create_all() — creates any ORM tables that don't exist yet
   (users, hedge_fund_flows, api_keys, chat tables, ...). Safe to re-run;
   existing tables are left untouched.
2. If Alembic has never run on this database, stamps it at the revision whose
   changes create_all() already covers (a1b2c3d4e5f6), so the older CREATE
   TABLE migrations are not replayed against tables that already exist.
3. alembic upgrade head — applies only the migrations create_all() cannot
   cover (run_archive tables from Phase 1, plus any future migrations).

Wired as the Railway pre-deploy command (railway.toml) so every deploy lands
with the schema up to date. Can also be run manually:

    railway run python scripts/sync_schema.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from alembic.runtime.migration import MigrationContext  # noqa: E402

# Every migration up to and including this revision creates or alters ORM
# tables that Base.metadata.create_all() produces with their final schema,
# so on a fresh database we stamp past them instead of replaying them.
CREATE_ALL_COVERED_REVISION = "a1b2c3d4e5f6"


def main() -> None:
    import os

    from app.backend.database.connection import Base, engine
    import app.backend.database.models  # noqa: F401 — registers models on Base

    # Announce the target so it's obvious from deploy logs which DB got migrated
    if os.environ.get("DATABASE_URL"):
        print(f"Schema sync target: PostgreSQL ({os.environ['DATABASE_URL'].split('@')[-1]})")
    else:
        print("Schema sync target: SQLite (DATABASE_URL not set — local dev mode)")

    # 1. ORM tables (no-op for tables that already exist)
    Base.metadata.create_all(bind=engine)
    print("create_all complete")

    # 1b. Runtime guards: create_all cannot ALTER existing tables, and this
    # is the authoritative place to backfill columns added by later model
    # changes even if the Alembic step below no-ops.
    from app.backend.database.schema_guard import ensure_all  # noqa: E402
    ensure_all(engine)
    print("schema guards complete")

    alembic_cfg = Config(str(ROOT / "app" / "backend" / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(ROOT / "app" / "backend" / "alembic"))

    # 2. Fresh DB -> stamp past the create_all-covered migrations
    with engine.connect() as conn:
        current_rev = MigrationContext.configure(conn).get_current_revision()
    if current_rev is None:
        print(f"No alembic revision found — stamping {CREATE_ALL_COVERED_REVISION}")
        command.stamp(alembic_cfg, CREATE_ALL_COVERED_REVISION)
    else:
        print(f"Database already at revision {current_rev}")

    # 3. Apply remaining migrations
    command.upgrade(alembic_cfg, "head")
    print("Schema sync complete")


if __name__ == "__main__":
    main()
