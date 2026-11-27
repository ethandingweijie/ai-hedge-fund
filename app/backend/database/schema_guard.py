"""
app/backend/database/schema_guard.py
=====================================
Runtime schema guards for columns the ORM's create_all cannot add to
tables that already exist.

Railway's predeploy hook runs scripts/sync_schema.py (create_all +
Alembic), but when that hook doesn't fire or its Alembic step no-ops,
the app would otherwise serve with a stale schema — and any ORM query
against a model column the database lacks is a hard 500 (this happened
in production on the Phase 3b deploy: the users table missed the four
new columns and every authenticated route broke).

Each guard inspects first and ALTERs only missing columns, so it is
idempotent and safe to run on every boot on both PostgreSQL and SQLite.
"""
from __future__ import annotations

import logging

import sqlalchemy as sa

logger = logging.getLogger(__name__)


def ensure_user_extensions(engine: sa.engine.Engine) -> None:
    """Phase 3b user columns: role / is_active / daily_pipeline_limit /
    concurrent_pipeline_limit. NOT NULL with server defaults so existing
    rows are backfilled in place (member / active / 20 / 3)."""
    inspector = sa.inspect(engine)
    if "users" not in inspector.get_table_names():
        return  # nothing to patch — create_all hasn't produced the table yet
    cols = {c["name"] for c in inspector.get_columns("users")}

    bool_default = "TRUE" if engine.dialect.name == "postgresql" else "1"
    pending: list[str] = []
    if "role" not in cols:
        pending.append(
            "ALTER TABLE users ADD COLUMN role VARCHAR(20) "
            "NOT NULL DEFAULT 'member'")
    if "is_active" not in cols:
        pending.append(
            "ALTER TABLE users ADD COLUMN is_active BOOLEAN "
            f"NOT NULL DEFAULT {bool_default}")
    if "daily_pipeline_limit" not in cols:
        pending.append(
            "ALTER TABLE users ADD COLUMN daily_pipeline_limit INTEGER "
            "NOT NULL DEFAULT 20")
    if "concurrent_pipeline_limit" not in cols:
        pending.append(
            "ALTER TABLE users ADD COLUMN concurrent_pipeline_limit INTEGER "
            "NOT NULL DEFAULT 3")

    if not pending:
        return
    with engine.begin() as conn:
        for stmt in pending:
            conn.execute(sa.text(stmt))
    logger.info(
        "Schema guard: added %d missing users column(s): %s",
        len(pending),
        [s.split("ADD COLUMN ")[1].split(" ")[0] for s in pending],
    )


def ensure_api_key_user_scope(engine: sa.engine.Engine) -> None:
    """Phase 3e per-user API keys: api_keys.user_id owner column (NULL =
    global key). On PostgreSQL also swaps the provider-only UNIQUE
    constraint for the composite (user_id, provider) so per-user
    overrides can coexist with the global key for the same provider.
    SQLite can't drop constraints without a table rebuild — dev-only, so
    the column backfill is all it gets (fresh SQLite DBs get the
    composite constraint from create_all)."""
    inspector = sa.inspect(engine)
    if "api_keys" not in inspector.get_table_names():
        return  # nothing to patch — create_all hasn't produced the table yet
    cols = {c["name"] for c in inspector.get_columns("api_keys")}

    if "user_id" not in cols:
        with engine.begin() as conn:
            conn.execute(sa.text(
                "ALTER TABLE api_keys ADD COLUMN user_id INTEGER "
                "REFERENCES users(id)"))
        logger.info("Schema guard: added api_keys.user_id column")

    if engine.dialect.name != "postgresql":
        return

    # Constraint swap (inspect-then-act, idempotent).
    inspector = sa.inspect(engine)
    drop_names = [uc["name"] for uc in inspector.get_unique_constraints("api_keys")
                  if uc["column_names"] == ["provider"]]
    have_composite = any(uc["name"] == "uq_api_keys_user_provider"
                         for uc in inspector.get_unique_constraints("api_keys"))
    if not drop_names and have_composite:
        return
    with engine.begin() as conn:
        for name in drop_names:
            conn.execute(sa.text(
                f'ALTER TABLE api_keys DROP CONSTRAINT "{name}"'))
        if not have_composite:
            conn.execute(sa.text(
                "ALTER TABLE api_keys ADD CONSTRAINT "
                "uq_api_keys_user_provider UNIQUE (user_id, provider)"))
    logger.info("Schema guard: api_keys uniqueness now (user_id, provider)")


def ensure_calibration_family(engine: sa.engine.Engine) -> None:
    """Self-learning layer (2026-10-03): calibration_versions.family, one active
    version per family (iv / pt / est). Every pre-existing row is an iv version.
    The dual-mode module adds the same column lazily; this guard covers a boot
    where the ORM path reaches the table first."""
    inspector = sa.inspect(engine)
    if "calibration_versions" not in inspector.get_table_names():
        return
    cols = {c["name"] for c in inspector.get_columns("calibration_versions")}
    if "family" in cols:
        return
    with engine.begin() as conn:
        conn.execute(sa.text("ALTER TABLE calibration_versions ADD COLUMN family "
                             "VARCHAR(16) NOT NULL DEFAULT 'iv'"))
    logger.info("Schema guard: added calibration_versions.family")


def ensure_agent_lessons_scope(engine: sa.engine.Engine) -> None:
    """Self-learning loop 7 (2026-10-04): agent_lessons.scope / scope_key. Every
    pre-existing row is a ticker-scope lesson."""
    inspector = sa.inspect(engine)
    if "agent_lessons" not in inspector.get_table_names():
        return
    cols = {c["name"] for c in inspector.get_columns("agent_lessons")}
    pending = []
    if "scope" not in cols:
        pending.append("ALTER TABLE agent_lessons ADD COLUMN scope VARCHAR(16) NOT NULL DEFAULT 'ticker'")
    if "scope_key" not in cols:
        pending.append("ALTER TABLE agent_lessons ADD COLUMN scope_key VARCHAR(200)")
    if not pending:
        return
    with engine.begin() as conn:
        for stmt in pending:
            conn.execute(sa.text(stmt))
    logger.info("Schema guard: agent_lessons scope columns added (%d)", len(pending))


#: Columns added to the self-learning ledgers after their tables shipped. The dual-mode
#: modules (src/memory/run_features.py, estimate_outcomes.py) add the same columns lazily
#: on first use; this guard covers a boot where something else reaches the table first.
#: Keep in step with run_features.ADDED_COLUMNS.
_LEARNING_COLUMNS: dict[str, tuple[tuple[str, str], ...]] = {
    "run_features": (("pt_calibration_version", "TEXT"), ("fy0_end", "TEXT"),
                     # 2026-11-27 provenance: split the scorecard by provider and build
                     ("pipeline_model", "TEXT"), ("research_model", "TEXT"),
                     ("engine_version", "TEXT")),
    # 2026-11-27 point-in-time estimate scoring
    "estimate_outcomes": (("lead_days", "INTEGER"), ("anchor_rule", "TEXT")),
}


def ensure_learning_ledger_columns(engine: sa.engine.Engine) -> None:
    """run_features provenance columns and estimate_outcomes.lead_days / anchor_rule.
    All nullable: existing rows read NULL (unknown provenance; an estimate row with no
    anchor_rule is rescored by the next sweep)."""
    inspector = sa.inspect(engine)
    tables = set(inspector.get_table_names())
    added: list[str] = []
    for table, cols in _LEARNING_COLUMNS.items():
        if table not in tables:
            continue
        have = {c["name"] for c in inspector.get_columns(table)}
        pending = [(c, t) for c, t in cols if c not in have]
        if not pending:
            continue
        with engine.begin() as conn:
            for col, typ in pending:
                conn.execute(sa.text(f"ALTER TABLE {table} ADD COLUMN {col} {typ}"))
        added += [f"{table}.{c}" for c, _ in pending]
    if added:
        logger.info("Schema guard: learning ledger columns added: %s", added)


def ensure_id_sequences(engine: sa.engine.Engine) -> None:
    """Postgres: every identity / serial key generates ahead of the rows already there.

    2026-11-27: the 2026-04 SQLite->Postgres copy left identity sequences where
    they started. watchlist.id sat at 3 beside a max id of 8, and its insert is
    ON CONFLICT DO NOTHING, so adding a name silently did nothing;
    hq_question_ledger.id sat at 1 beside 39,226 rows. Forward-only (setval to
    MAX+1 when behind), idempotent, a no-op on SQLite.
    """
    if engine.dialect.name != "postgresql":
        return
    from src.data import db
    if not db.is_postgres():
        return
    rows = db.query(
        "SELECT DISTINCT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND (is_identity = 'YES' "
        "OR position('nextval' in coalesce(column_default, '')) > 0)"
    )
    moved = []
    for r in rows:
        try:
            if db.ensure_serial_id(r["table_name"], r["column_name"]):
                moved.append(f"{r['table_name']}.{r['column_name']}")
        except Exception:                                  # noqa: BLE001
            logger.exception("Schema guard: sequence check failed for %s.%s", r["table_name"], r["column_name"])
    if moved:
        logger.info("Schema guard: id sequences moved past MAX(id): %s", moved)


def ensure_all(engine: sa.engine.Engine) -> None:
    """Run every runtime schema guard. Called at boot (main.py) and by
    scripts/sync_schema.py. Failures are logged, not raised — a startup
    crash loop is worse than serving with the diag endpoint reporting
    the missing columns."""
    for guard in (ensure_user_extensions, ensure_api_key_user_scope,
                  ensure_calibration_family, ensure_agent_lessons_scope,
                  ensure_learning_ledger_columns, ensure_id_sequences):
        try:
            guard(engine)
        except Exception:
            logger.exception("Schema guard failed — database may be stale")
