"""
app/backend/services/analysis_service.py
=========================================
Wraps run_advanced_pipeline() from src/pipeline.py for web use.

Key design:
- New SQLite table `web_runs` in run_archive.db (same DB as src/memory/run_archive.py)
- run_advanced_pipeline() is synchronous — run in threading.Thread, communicate
  progress via asyncio.Queue using loop.call_soon_threadsafe
- Before running pipeline, set API keys from dict as os.environ variables
- After pipeline completes, call _compute_vgpm() from src/utils/pdf_report.py
- Store full result JSON in web_runs table
- Attempt to call save_run() from src/memory/run_archive.py for backwards compat
"""

import asyncio
import json
import math
import os
import sqlite3
import threading
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from src.utils import run_config
from src.data import db


# ── .env.local (process-wide, load once) ──────────────────────────────────────

_env_local_loaded = False
_env_local_lock = threading.Lock()


def _load_env_local_once() -> None:
    """Load .env.local into os.environ the first time only.

    Deployment config (base URLs, fallback keys) is identical for every run, so
    it belongs in os.environ. Per-run, caller-supplied values do NOT — those go
    through run_config's ContextVar overlay instead.
    """
    global _env_local_loaded
    if _env_local_loaded:
        return
    with _env_local_lock:
        if _env_local_loaded:
            return
        env_local = Path(__file__).parent.parent.parent.parent / ".env.local"
        if env_local.exists():
            try:
                from dotenv import load_dotenv

                load_dotenv(env_local, override=True)
            except ImportError:
                # dotenv not installed — parse manually
                for line in env_local.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, _, v = line.partition("=")
                        if k.strip():
                            os.environ[k.strip()] = v.strip().strip('"').strip("'")
        _env_local_loaded = True


# ── Float sanitizer ───────────────────────────────────────────────────────────

def _sanitize_floats(obj: Any) -> Any:
    """
    Recursively replace NaN / Inf / -Inf floats with None so that the result
    is compliant with RFC 7159 JSON (FastAPI's strict serializer rejects them).
    Python's json module writes bare ``NaN`` / ``Infinity`` literals by default,
    which are NOT valid JSON — this cleaner is applied both before DB writes and
    before returning to the HTTP layer.
    """
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: _sanitize_floats(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_floats(v) for v in obj]
    return obj

# ── DB path ───────────────────────────────────────────────────────────────────

def _get_db_path() -> str:
    """Same DB as run_archive.py — run_archive.db in src/data/.
    Configurable via RUN_ARCHIVE_PATH env var for cloud deployment."""
    import os
    env_path = os.environ.get("RUN_ARCHIVE_PATH")
    if env_path:
        return env_path
    this_file = Path(__file__)
    project_root = this_file.parent.parent.parent.parent
    return str(project_root / "src" / "data" / "run_archive.db")


def _connect(path: str | None = None, **kwargs) -> sqlite3.Connection:
    """Open run_archive.db with WAL mode, NORMAL sync, and a 5-second busy timeout.

    SQLite path only. Kept importable for modules that still read their own
    SQLite tables through this connection factory (e.g. routes/dd_alerts.py).
    Production Postgres traffic goes through src.data.db — see _fetch/_exec.
    """
    conn = sqlite3.connect(path or _get_db_path(), **kwargs)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


# ── Dual-mode execution helpers (SQLite local / Postgres production) ─────────

def _fetch(sql: str, params: list | None = None) -> list:
    """SELECT on the active backend; rows support name-based access."""
    if db.is_postgres():
        return db.query(sql, params or [])
    conn = _connect()
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, params or []).fetchall()
    finally:
        conn.close()


def _fetch_one(sql: str, params: list | None = None):
    """SELECT on the active backend; first row or None."""
    rows = _fetch(sql, params)
    return rows[0] if rows else None


def _exec(sql: str, params: list | None = None) -> int:
    """Run a write statement on the active backend; returns rowcount."""
    if db.is_postgres():
        return db.execute(sql, params or [])
    conn = _connect()
    try:
        cur = conn.execute(sql, params or [])
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def _json_field(path: str, col: str = "full_result_json") -> str:
    """Extract a JSON scalar from a TEXT column in the active dialect.

    SQLite: json_extract(col, '$.a.b')
    Postgres: col::jsonb #>> '{a,b}'
    Both return NULL when any segment of the path is absent.
    """
    if db.is_postgres():
        pg_path = "{" + ",".join(path.split(".")) + "}"
        # NULLIF guards against empty-string cells: ''::jsonb raises in PG,
        # while SQLite's json_extract('') just returns NULL.
        return f"NULLIF({col}, '')::jsonb #>> '{pg_path}'"
    return f"json_extract({col}, '$.{path}')"


def _web_runs_upsert_sql(columns: list[str]) -> str:
    """INSERT keyed on run_id that overwrites the row on conflict.

    SQLite: INSERT OR REPLACE. Postgres: ON CONFLICT (run_id) DO UPDATE.
    (DO UPDATE is strictly safer than OR REPLACE here: it only touches the
    listed columns instead of resetting every unlisted column to NULL.)
    """
    cols = ", ".join(columns)
    phs = ", ".join("?" * len(columns))
    if db.is_postgres():
        updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in columns if c != "run_id")
        return (f"INSERT INTO web_runs ({cols}) VALUES ({phs}) "
                f"ON CONFLICT (run_id) DO UPDATE SET {updates}")
    return f"INSERT OR REPLACE INTO web_runs ({cols}) VALUES ({phs})"


# ── web_runs DDL ──────────────────────────────────────────────────────────────

_WEB_RUNS_DDL = """
CREATE TABLE IF NOT EXISTS web_runs (
    run_id           TEXT PRIMARY KEY,
    run_at           TEXT NOT NULL,
    ticker           TEXT NOT NULL,
    model_name       TEXT,
    archive_run_id   TEXT,
    full_result_json TEXT,
    -- summary columns (added in migration; NULL for legacy rows until backfilled)
    final_action     TEXT,
    regime           TEXT,
    sector           TEXT,
    profile_name     TEXT,
    is_checkpoint    INTEGER DEFAULT 0,
    -- Workstream A: per-phase pipeline timing JSON; NULL for legacy rows.
    phase_durations  TEXT
)
"""

_WEB_RUNS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_web_runs_ticker_time ON web_runs(ticker, run_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_web_runs_archive_id ON web_runs(archive_run_id)",
    "CREATE INDEX IF NOT EXISTS idx_web_runs_run_at ON web_runs(run_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_web_runs_action ON web_runs(final_action)",
    "CREATE INDEX IF NOT EXISTS idx_web_runs_sector ON web_runs(sector)",
    # Composite index supports "all Growth SaaS runs" / "all Mature SaaS"
    # queries without a table scan. Ordered (sector, profile_name) so a
    # sector-only filter can also use it (left-prefix match).
    "CREATE INDEX IF NOT EXISTS idx_web_runs_sector_profile ON web_runs(sector, profile_name)",
    "CREATE INDEX IF NOT EXISTS idx_web_runs_user_id ON web_runs(user_id)",
]

# Archive tables DDL — created by CLI pipeline but needed on cloud too
_ARCHIVE_DDL = [
    """CREATE TABLE IF NOT EXISTS runs (
        run_id TEXT PRIMARY KEY, run_at TEXT, model_name TEXT,
        regime_risk_appetite TEXT, sector TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS ticker_signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT, ticker TEXT, final_action TEXT,
        position_size_pct REAL, price_target REAL, stop_loss REAL,
        dcf_base_iv REAL, ev_upside_pct REAL, power_law_score REAL,
        value_trap_verdict TEXT, outcome TEXT, pct_change REAL
    )""",
    """CREATE TABLE IF NOT EXISTS agent_signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT, ticker TEXT, agent_name TEXT, signal TEXT, confidence REAL
    )""",
]

# Archive table indexes (runs + ticker_signals live in run_archive.db too)
_ARCHIVE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_runs_run_at ON runs(run_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_ts_run_id ON ticker_signals(run_id)",
    "CREATE INDEX IF NOT EXISTS idx_ts_ticker ON ticker_signals(ticker)",
]


def _migrate_web_runs_columns(conn: sqlite3.Connection) -> None:
    """Add summary columns to web_runs if they don't exist yet (idempotent)."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(web_runs)").fetchall()}
    migrations = [
        ("final_action",  "ALTER TABLE web_runs ADD COLUMN final_action  TEXT"),
        ("regime",        "ALTER TABLE web_runs ADD COLUMN regime         TEXT"),
        ("sector",        "ALTER TABLE web_runs ADD COLUMN sector         TEXT"),
        # v2.0.2 — profile_name as first-class column enables admin queries
        # like "all Growth SaaS runs" without a table scan over
        # full_result_json and gives the admin UI a visible sub-sector column.
        ("profile_name",  "ALTER TABLE web_runs ADD COLUMN profile_name  TEXT"),
        ("is_checkpoint", "ALTER TABLE web_runs ADD COLUMN is_checkpoint  INTEGER DEFAULT 0"),
        ("user_id",       "ALTER TABLE web_runs ADD COLUMN user_id        INTEGER"),
        # Workstream A — per-phase pipeline timing JSON. Dedicated column so
        # timing queries (avg deep-research duration, slowest phase) don't
        # have to parse the full_result_json blob. Additive-only migration.
        ("phase_durations", "ALTER TABLE web_runs ADD COLUMN phase_durations TEXT"),
    ]
    for col, sql in migrations:
        if col not in existing:
            conn.execute(sql)


def _ensure_web_runs_table():
    if db.is_postgres():
        # web_runs was created by the SQLite->Postgres data migration with the
        # full column set; backfill anything that may still be missing, then
        # make sure the indexes exist too.
        for col, definition in [
            ("final_action",    "TEXT"),
            ("regime",          "TEXT"),
            ("sector",          "TEXT"),
            ("profile_name",    "TEXT"),
            ("is_checkpoint",   "INTEGER DEFAULT 0"),
            ("user_id",         "INTEGER"),
            ("phase_durations", "TEXT"),
        ]:
            db.add_column_if_missing("web_runs", col, definition)
        for idx_sql in _WEB_RUNS_INDEXES:
            db.execute(idx_sql)
        # The CLI archive tables (runs / ticker_signals / agent_signals /
        # rotation_events / ticker_routing_cache) are owned by
        # src/memory/run_archive.py — delegate to its full-schema ensure.
        from src.memory import run_archive
        run_archive.ensure_schema()
        for idx_sql in _ARCHIVE_INDEXES:
            try:
                db.execute(idx_sql)
            except Exception:
                pass
        return

    db_path = _get_db_path()
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = _connect(db_path)
    try:
        conn.execute(_WEB_RUNS_DDL)
        _migrate_web_runs_columns(conn)
        for idx_sql in _WEB_RUNS_INDEXES:
            conn.execute(idx_sql)
        # Ensure archive tables exist (created by CLI pipeline locally,
        # but need to be bootstrapped on fresh cloud databases)
        for ddl in _ARCHIVE_DDL:
            conn.execute(ddl)
        for idx_sql in _ARCHIVE_INDEXES:
            try:
                conn.execute(idx_sql)
            except Exception:
                pass
        conn.commit()
    finally:
        conn.close()


def _extract_web_run_summary(result: dict, ticker: str) -> tuple[str, str, str, str]:
    """Extract (final_action, regime, sector, profile_name) from a pipeline result.

    profile_name resolution tree (first non-empty wins):
      1. state.data.profile_name                        — primary ticker
      2. state.data.profile_names[<primary_ticker>]     — multi-ticker map
      3. state.data.profile_names[<ticker>]             — argument ticker
      4. TICKER_SECTOR_LOOKUP[ticker] → second element  — deterministic fallback
    Returns "" when nothing resolves (ticker not in lookup AND state has no
    profile — rare, only for unknown foreign tickers).
    """
    try:
        data = result.get("data", {})
        tickers_list = data.get("tickers", [ticker])
        t = tickers_list[0] if tickers_list else ticker

        macro = data.get("macro_regime", {})
        regime = (
            macro.get("regime", {}).get("risk_appetite", "")
            if isinstance(macro.get("regime"), dict)
            else macro.get("risk_appetite", "")
        )

        sector = data.get("sector", "") or ""

        decisions = result.get("decisions", {})
        final_action = ""
        if isinstance(decisions, dict):
            td = decisions.get(t, {})
            final_action = td.get("action", "") or ""

        # profile_name resolution with TICKER_SECTOR_LOOKUP fallback
        profile_name = (data.get("profile_name") or "").strip()
        if not profile_name:
            profile_names_map = data.get("profile_names") or {}
            if isinstance(profile_names_map, dict):
                profile_name = (profile_names_map.get(t) or profile_names_map.get(ticker) or "").strip()
        if not profile_name:
            # Final fallback — canonical ticker lookup. Covers historic runs
            # archived before the strategic_router pre-classification feature
            # (v2.0) landed. Matches the same lookup logic needs_extractor()
            # uses for extractor gating.
            try:
                from src.data.sector_profiles import TICKER_SECTOR_LOOKUP
                entry = TICKER_SECTOR_LOOKUP.get((ticker or t).upper())
                if entry and len(entry) >= 2:
                    profile_name = (entry[1] or "").strip()
            except Exception:
                pass

        return final_action, regime, sector, profile_name
    except Exception:
        return "", "", "", ""


def _save_partial_web_run(
    run_id: str,
    ticker: str,
    model_name: str,
    checkpoint_name: str,
    state: dict,
    user_id: Optional[int] = None,
):
    """
    Upsert a partial pipeline result into web_runs for a named checkpoint.
    The JSON carries a top-level ``"checkpoint"`` key so get_cached_run() can
    exclude these rows from the cache-hit logic.
    Uses INSERT OR REPLACE so subsequent checkpoints (and the final save) all
    land in the same row, keyed on run_id.
    """
    _ensure_web_runs_table()
    data = state.get("data", {})
    partial_result = {
        "run_id":       run_id,
        "ticker":       ticker,
        "model_name":   model_name,
        "run_at":       datetime.now(timezone.utc).isoformat(),
        "checkpoint":   checkpoint_name,   # sentinel — marks this as a partial save
        "data": {
            # ── always present ───────────────────────────────────────────
            "tickers":                     data.get("tickers", [ticker]),
            # In-progress phase timings (Workstream A) — a crashed/stuck run's
            # checkpoint row shows exactly which phases finished before failure.
            "phase_durations":             data.get("phase_durations"),
            "macro_regime":                data.get("macro_regime"),
            "raw_financials":              data.get("raw_financials"),
            "routing_decision":            data.get("routing_decision"),
            "research_tier":               data.get("research_tier"),
            # Sector classification fields — set by strategic_router BEFORE any
            # checkpoint fires. Missing them from the subset caused the frontend
            # to render blank sector panels for runs that were still mid-pipeline
            # (checkpoint rows live in the same table as final saves and get
            # returned when users click a run before _save_web_run() replaces
            # the row with full JSON). CRM / SNOW observed without these.
            "sector":                      data.get("sector"),
            "sectors":                     data.get("sectors"),
            "profile_name":                data.get("profile_name"),
            "profile_names":               data.get("profile_names"),
            # ── checkpoint: deep_research ────────────────────────────────
            "deep_research":               data.get("deep_research"),
            "deep_research_annotated":     data.get("deep_research_annotated"),
            "deep_research_sections":      data.get("deep_research_sections"),
            "citation_registry":           data.get("citation_registry", []),
            # Sector-specific extractor outputs — populated by deep_research.py's
            # parallel extractor fan-out. Omitting these from the subset caused
            # saas_metrics / pipeline_assets / bank_metrics / reit_metrics panels
            # to render empty on any run observed pre-final-save.
            "saas_metrics":                data.get("saas_metrics"),
            "pipeline_assets":             data.get("pipeline_assets"),
            "bank_metrics":                data.get("bank_metrics"),
            "reit_metrics":                data.get("reit_metrics"),
            "dcf_calibration":             data.get("dcf_calibration"),
            "segment_scenarios":           data.get("segment_scenarios"),
            # ── checkpoint: industry_brief ───────────────────────────────
            "industry_brief":              data.get("industry_brief"),
            # ── checkpoint: investor_signals ─────────────────────────────
            "analyst_signals":             data.get("analyst_signals"),
            "dcf_range":                   data.get("dcf_range"),
            "peer_comparison":             data.get("peer_comparison"),
            "price_history":               data.get("price_history"),
            # ── checkpoint: final_calculation ────────────────────────────
            "scenario_analysis":           data.get("scenario_analysis"),
            "power_law_analysis":          data.get("power_law_analysis"),
            "value_trap_analysis":         data.get("value_trap_analysis"),
            "vgpm":                        data.get("vgpm"),
            "decisions":                   data.get("decisions"),
            # ── sector_card (Option B render) ───────────────────────────
            # Included in partial save so the SSE progressive UI shows the
            # card mid-run as soon as DCF + framework metrics are ready.
            # See src/data/sector_kpi_framework.render_card_payloads_for_run.
            "sector_card":                 data.get("sector_card"),
        },
    }
    try:
        _exec(
            _web_runs_upsert_sql(
                ["run_id", "run_at", "ticker", "model_name", "archive_run_id",
                 "full_result_json", "is_checkpoint", "user_id"]
            ),
            [
                run_id,
                datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f"),
                ticker,
                model_name,
                None,
                json.dumps(_sanitize_floats(partial_result), default=str),
                1,
                user_id,
            ],
        )
        print(f"  [checkpoint] '{checkpoint_name}' saved to web_runs ({run_id[:8]})")
    except Exception as e:
        print(f"  [checkpoint] DB write failed ({checkpoint_name}): {e}")


def _save_web_run(
    run_id: str,
    ticker: str,
    model_name: str,
    result: dict,
    archive_run_id: Optional[str] = None,
    user_id: Optional[int] = None,
):
    _ensure_web_runs_table()
    final_action, regime, sector, profile_name = _extract_web_run_summary(result, ticker)
    # Workstream A — persist per-phase timing JSON in a dedicated column (it
    # also lives inside full_result_json). NULL when absent (legacy/CLI runs).
    _pd = (result.get("data") or {}).get("phase_durations") or None
    _pd_json = json.dumps(_sanitize_floats(_pd), default=str) if _pd else None
    _exec(
        _web_runs_upsert_sql(
            ["run_id", "run_at", "ticker", "model_name", "archive_run_id",
             "full_result_json", "final_action", "regime", "sector",
             "profile_name", "phase_durations", "is_checkpoint", "user_id"]
        ),
        [
            run_id,
            # Store as plain ISO without tz suffix so string sort is consistent
            # with CLI archive timestamps (both naive local-time strings).
            datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f"),
            ticker,
            model_name,
            archive_run_id,
            json.dumps(_sanitize_floats(result), default=str),
            final_action or None,
            regime or None,
            sector or None,
            profile_name or None,
            _pd_json,
            0,
            user_id,
        ],
    )

    # Invalidate screener caches so the new pipeline VGPM is reflected immediately
    try:
        from app.backend.services.screener_service import invalidate_for_ticker
        invalidate_for_ticker(ticker)
    except Exception:
        pass

    # If this ticker is on the watchlist, push the authoritative pipeline VGPM
    # straight into the watchlist table so it's visible on next page load.
    try:
        from app.backend.services.watchlist_service import is_in_watchlist, refresh_ticker_vgpm
        if is_in_watchlist(ticker):
            refresh_ticker_vgpm(ticker)
    except Exception:
        pass


# ── Cache helper ──────────────────────────────────────────────────────────────

# ── Agent-name canonicalization for cache key matching ──────────────────────
# The frontend sends short names ('graham', 'buffett', 'burry', etc.) but the
# backend stores agent_id keys in analyst_signals using FULL names like
# 'ben_graham_agent', 'warren_buffett_agent', 'michael_burry_agent'. Naive
# normalization `f"{name}_agent"` produces 'graham_agent' which never matches
# 'ben_graham_agent' → cache miss on every Deep-Value / Quality-Growth /
# partial-profile run. Hardcode the short→full mapping here.
#
# Source of truth: src/utils/analysts.py ANALYST_CONFIG keys, which get
# suffixed with '_agent' to form the agent_id by get_analyst_nodes().
_FRONTEND_SHORT_TO_AGENT_ID = {
    "damodaran":      "aswath_damodaran_agent",
    "graham":         "ben_graham_agent",
    "ackman":         "bill_ackman_agent",
    "cathie_wood":    "cathie_wood_agent",
    "munger":         "charlie_munger_agent",
    "burry":          "michael_burry_agent",
    "pabrai":         "mohnish_pabrai_agent",
    "lynch":          "peter_lynch_agent",
    "fisher":         "phil_fisher_agent",
    "jhunjhunwala":   "rakesh_jhunjhunwala_agent",
    "druckenmiller":  "stanley_druckenmiller_agent",
    "buffett":        "warren_buffett_agent",
}

# Agents that ALWAYS run and appear in analyst_signals but are NOT
# user-selectable (and therefore excluded from the cache-comparison set).
# Includes:
#   - System: risk_management, advanced_risk_manager, portfolio_manager
#     (note: portfolio_manager has NO _agent suffix per portfolio_manager.py)
#   - Always-on data analysts: sentiment, technical, valuation, growth,
#     fundamentals, news_sentiment
_CACHE_SYSTEM_AGENTS = {
    "risk_management_agent",
    "advanced_risk_manager",
    "portfolio_manager",
    "advanced_portfolio_manager",
    "sentiment_analyst_agent",
    "technical_analyst_agent",
    "valuation_analyst_agent",
    "growth_analyst_agent",
    "fundamentals_analyst_agent",
    "news_sentiment_agent",
}


def _canonicalize_requested_agents(agents: list[str]) -> list[str]:
    """Translate frontend short names to backend agent_id format.
    'graham' → 'ben_graham_agent'. Already-suffixed names pass through.
    Unknown names get the legacy `f"{name}_agent"` fallback so older
    profile configs still work."""
    out: list[str] = []
    for a in agents:
        if a.endswith("_agent"):
            out.append(a)
        elif a in _FRONTEND_SHORT_TO_AGENT_ID:
            out.append(_FRONTEND_SHORT_TO_AGENT_ID[a])
        else:
            out.append(f"{a}_agent")
    return out


def get_cached_run(
    ticker: str,
    within_minutes: int = 30,
    agents: list[str] | None = None,
) -> Optional[dict]:
    """
    Return the most recent completed web run for *ticker* if it was created
    within *within_minutes* minutes ago AND was run with the same investor
    agents as requested, otherwise None.

    *agents* is the raw list sent by the frontend (e.g. ['graham', 'burry']).
    None means "all agents" — any cached full-committee run is acceptable.
    If *agents* is a non-empty list, the cached run must contain exactly the
    same set of investor agents (cache miss when agent selection differs).
    """
    _ensure_web_runs_table()
    cutoff = (datetime.now() - timedelta(minutes=within_minutes)).strftime(
        "%Y-%m-%dT%H:%M:%S.%f"
    )
    row = _fetch_one(
        f"""
        SELECT run_id, run_at, ticker, full_result_json
        FROM   web_runs
        WHERE  ticker  = ?
          AND  run_at >= ?
          AND  full_result_json IS NOT NULL
          AND  {_json_field("checkpoint")} IS NULL
        ORDER  BY run_at DESC
        LIMIT  1
        """,
        [ticker.upper(), cutoff],
    )
    if row is None:
        return None

    result = {
        "run_id":           row["run_id"],
        "run_at":           row["run_at"],
        "ticker":           row["ticker"],
        "full_result_json": json.loads(row["full_result_json"]),
    }

    # ── Agent-set validation ───────────────────────────────────────────
    # Verify the cached run used the same investor-agent set as requested.
    # Two layers of normalisation needed (bug fixed 2026-04-25):
    #   1. Frontend short names → backend agent_id via _FRONTEND_SHORT_TO_AGENT_ID
    #      ('graham' → 'ben_graham_agent', not 'graham_agent')
    #   2. Strip always-on system + data analysts from the cached set
    #      (sentiment, technical, valuation, growth, fundamentals,
    #       news_sentiment, plus risk + portfolio managers)
    # Pre-fix: every Deep-Value / partial-profile run cache-missed because
    # short-name normalisation produced 'graham_agent' which never matched
    # the cached 'ben_graham_agent'.
    if agents:
        cached_data    = result["full_result_json"].get("data", {})
        cached_signals = cached_data.get("analyst_signals", {})
        cached_investor_agents = sorted(
            k for k in cached_signals if k not in _CACHE_SYSTEM_AGENTS
        )
        requested_normalised = sorted(_canonicalize_requested_agents(agents))
        if cached_investor_agents != requested_normalised:
            return None   # different agent selection — force fresh run

    return result


# ── Delete helper ─────────────────────────────────────────────────────────────

def delete_run(run_id: str, user_id: int = None) -> bool:
    """
    Permanently delete a run from the archive.
    Only the owner can delete a run. Legacy runs (user_id IS NULL) can be
    deleted by any authenticated user for backward compatibility.
    Removes from web_runs first; if the row carries an archive_run_id,
    cascades to the CLI archive tables (runs, ticker_signals, agent_signals).
    Falls back to deleting directly from the CLI tables for CLI-only runs.
    Returns True if anything was deleted, False if run_id not found or not owned.
    """
    _ensure_web_runs_table()

    # ── 1. Check web_runs ────────────────────────────────────────────────
    row = _fetch_one(
        "SELECT archive_run_id, user_id FROM web_runs WHERE run_id = ?", [run_id]
    )

    if row is not None:
        # Ownership check: only owner can delete (legacy runs have NULL user_id)
        run_user_id = row["user_id"]
        if run_user_id is not None and user_id is not None and run_user_id != user_id:
            return False  # Not the owner — deny deletion

        archive_id = row["archive_run_id"]
        _exec("DELETE FROM web_runs WHERE run_id = ?", [run_id])
        # Cascade to CLI archive if this web run was also saved there
        if archive_id:
            _exec("DELETE FROM agent_signals  WHERE run_id = ?", [archive_id])
            _exec("DELETE FROM ticker_signals WHERE run_id = ?", [archive_id])
            _exec("DELETE FROM runs           WHERE run_id = ?", [archive_id])
        return True

    # ── 2. Fallback: CLI-only run (no web_runs entry) ────────────────────
    affected = _exec("DELETE FROM runs WHERE run_id = ?", [run_id])
    if affected:
        _exec("DELETE FROM agent_signals  WHERE run_id = ?", [run_id])
        _exec("DELETE FROM ticker_signals WHERE run_id = ?", [run_id])
        return True

    return False


# ── Public read helpers ───────────────────────────────────────────────────────

def get_run_result(run_id: str, user_id: int = None) -> Optional[dict]:
    """
    Return full result dict for a run.
    Checks web_runs first (full JSON stored); falls back to reconstructing
    from the CLI archive tables (runs + ticker_signals + agent_signals).
    Ownership: user can only access their own runs (legacy runs visible to all).
    """
    _ensure_web_runs_table()

    # ── 1. Try web_runs (full JSON) ───────────────────────────────────────
    row = _fetch_one(
        "SELECT full_result_json, user_id FROM web_runs WHERE run_id = ?",
        [run_id],
    )
    if row and row["full_result_json"]:
        # Ownership check: only owner can access (legacy runs have NULL user_id)
        run_user_id = row["user_id"]
        if run_user_id is not None and user_id is not None and run_user_id != user_id:
            return None  # Not the owner — deny access
        return _sanitize_floats(json.loads(row["full_result_json"]))

    # ── 2. Try reconstructing from CLI archive tables ─────────────────────
    run_row = _fetch_one(
        "SELECT * FROM runs WHERE run_id = ?", [run_id]
    )
    if not run_row:
        return None

    tickers: list[str] = json.loads(run_row["tickers"] or "[]")
    ticker = tickers[0] if tickers else ""

    # Ticker signals
    ts = _fetch_one(
        "SELECT * FROM ticker_signals WHERE run_id = ? AND ticker = ?",
        [run_id, ticker],
    )

    # Agent signals
    agent_rows = _fetch(
        "SELECT * FROM agent_signals WHERE run_id = ? AND ticker = ?",
        [run_id, ticker],
    )

    # Reconstruct analyst_signals dict (investor agents only)
    analyst_signals: dict = {}
    for ag in agent_rows:
        analyst_signals[ag["agent_key"]] = {
            ticker: {
                "signal":         ag["signal"],
                "conviction":     ag["conviction"],
                "price_target":   ag["price_target"],
                "time_horizon":   ag["time_horizon"],
                "thesis_summary": ag["thesis_summary"],
                "key_risks":      json.loads(ag["key_risks"] or "[]"),
            }
        }

    # Reconstruct decisions dict
    decisions: dict = {}
    if ts:
        decisions[ticker] = {
            "action":            ts["final_action"],
            "position_size_pct": ts["position_size_pct"],
            "price_target":      ts["price_target"],
            "stop_loss":         ts["stop_loss"],
            "entry_range":       [ts["entry_range_low"], ts["entry_range_high"]],
            "time_horizon":      ts["time_horizon"],
            "rationale":         ts["pm_rationale"],
        }

    # ── v3 JSON blobs (use rich data if stored, fall back to scalar stubs) ─
    def _load_json_col(col_name: str) -> Optional[dict]:
        val = ts[col_name] if ts else None
        if val:
            try:
                return json.loads(val)
            except Exception:
                pass
        return None

    # Scenario analysis
    scenario_full = _load_json_col("scenario_json")
    if scenario_full:
        scenario_analysis: dict = {ticker: scenario_full}
    elif ts and ts["ev_upside_pct"] is not None:
        scenario_analysis = {
            ticker: {
                "upside_pct":    ts["ev_upside_pct"],
                "expected_value": None,
                "current_price": ts["price_at_run"],
                "bull": {"fair_value": None, "probability": 0.25, "assumptions": ""},
                "base": {"fair_value": None, "probability": 0.50, "assumptions": ""},
                "bear": {"fair_value": None, "probability": 0.25, "assumptions": ""},
            }
        }
    else:
        scenario_analysis = {}

    # Power law analysis
    pl_full = _load_json_col("power_law_json")
    if pl_full:
        power_law_analysis: dict = {ticker: pl_full}
    elif ts and ts["power_law_score"] is not None:
        power_law_analysis = {
            ticker: {"total_score": ts["power_law_score"], "score": ts["power_law_score"]}
        }
    else:
        power_law_analysis = {}

    # Raw financials
    raw_financials: dict = _load_json_col("raw_financials_json") or {}

    # Citation audit
    ca_full = _load_json_col("citation_audit_json")
    citation_audit: dict = {ticker: ca_full} if ca_full else {}

    # VGPM
    vgpm_full = _load_json_col("vgpm_json")
    vgpm: dict = {ticker: vgpm_full} if vgpm_full else {}

    # DCF range — all three scenario intrinsic values (bear/bull added in v3)
    dcf_range: dict = {}
    if ts and ts["dcf_base_iv"]:
        dcf_range[ticker] = {
            "wacc": ts["dcf_wacc"],
            "base": {"intrinsic_value": ts["dcf_base_iv"]},
            "bear": {"intrinsic_value": ts["dcf_bear_iv"] if "dcf_bear_iv" in ts.keys() else None},
            "bull": {"intrinsic_value": ts["dcf_bull_iv"] if "dcf_bull_iv" in ts.keys() else None},
        }

    # Intelligence stubs from scalar columns
    intel: dict = {}
    if ts:
        intel["insider_activity_agent"] = {
            ticker: {"signal": ts["insider_signal"], "summary": ts["insider_signal"] or ""}
        }
        intel["analyst_revision_agent"] = {
            ticker: {"revision_direction": ts["revision_direction"]}
        }
        intel["news_sentiment_agent"] = {
            ticker: {"signal": ts["news_signal"]}
        }
        intel["earnings_quality_agent"] = {
            ticker: {
                "quality_verdict":       ts["eq_quality_verdict"],
                "overall_quality_score": ts["eq_quality_score"],
            }
        }
        intel["short_interest_agent"] = {
            ticker: {
                "signal":          ts["si_signal"],
                "short_float_pct": ts["si_short_float_pct"],
                "squeeze_risk":    bool(ts["si_squeeze_risk"]),
                "crowded_trade":   bool(ts["si_crowded_trade"]),
            }
        }

    # Value trap
    value_trap_analysis: dict = {}
    if ts and ts["value_trap_verdict"]:
        value_trap_analysis[ticker] = {"overall_verdict": ts["value_trap_verdict"]}

    # Sector card (Option B render — v3.3). When the column is missing
    # (very old runs predating the v3.3 migration) sqlite3.Row raises
    # IndexError on string lookup, hence the .keys() guard.
    sector_card: dict = {}
    if ts and "sector_card_json" in ts.keys():
        sc_full = _load_json_col("sector_card_json")
        if sc_full:
            sector_card[ticker] = sc_full

    # Macro regime
    macro_regime: dict = {
        "risk_appetite":     run_row["regime_risk_appetite"],
        "rate_direction":    run_row["regime_rate_direction"],
        "volatility_regime": run_row["regime_volatility"],
        "dollar_trend":      run_row["regime_dollar"],
        "recession_risk":    run_row["regime_recession_risk"],
    }

    result = {
        "run_id":     run_id,
        "ticker":     ticker,
        "model_name": run_row["model_name"],
        "run_at":     run_row["run_at"],
        "source":     "cli_archive",
        "data": {
            "tickers":            tickers,
            "sector":             run_row["sector"],
            "macro_regime":       macro_regime,
            "analyst_signals":    {**analyst_signals, **intel},
            "scenario_analysis":  scenario_analysis,
            "dcf_range":          dcf_range,
            "value_trap_analysis":value_trap_analysis,
            "power_law_analysis": power_law_analysis,
            "raw_financials":     raw_financials,
            "citation_audit":     citation_audit,
            "industry_brief":          run_row["industry_brief_text"] or "",
            "deep_research":           run_row["deep_research_text"] or "",
            "deep_research_annotated": run_row["deep_research_text"] or "",
            "deep_research_sections":  {},
            # v3.3 — sector valuation card (Option B render).
            "sector_card":             sector_card,
        },
        "decisions": decisions,
        "vgpm":      vgpm,
    }
    return result


def get_history(
    ticker: Optional[str] = None,
    sector: Optional[str] = None,
    regime: Optional[str] = None,
    action: Optional[str] = None,
    outcome: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    user_id: Optional[int] = None,
) -> dict:
    """
    Return a unified paginated history from both sources:
      - web_runs   : runs triggered via the web UI (full_result_json available)
      - runs / ticker_signals : CLI pipeline runs already in the archive
    Web runs that were also saved to the archive (archive_run_id set) are only
    shown once (from web_runs) to avoid duplicates.

    Fast path: summary columns (final_action, regime, sector, is_checkpoint) on
    web_runs allow SQL-level filtering and COUNT(*) without loading full_result_json.
    Only the page slice fetches the JSON blob for VGPM grade extraction.
    Legacy rows (NULL summary cols) fall back to json_extract so nothing is lost.
    """
    _ensure_web_runs_table()
    offset = (page - 1) * page_size

    # ── 1. Web runs — filter without loading full_result_json ─────────────
    web_where: list[str] = [
        # Exclude checkpoint rows: prefer the fast column; fall back to a JSON
        # field extraction for legacy rows where is_checkpoint is NULL.
        f"(w.is_checkpoint = 0 OR (w.is_checkpoint IS NULL AND "
        f"{_json_field('checkpoint', col='w.full_result_json')} IS NULL))",
        # Phase 2B: belt-and-suspenders DD exclusion. New DD reports go
        # to the dedicated dd_reports table (NOT web_runs), so this
        # filter is technically a no-op for fresh installs. It still
        # catches any stragglers from the pre-Phase-2B architecture
        # that haven't been purged yet via /admin/dd-purge-legacy-web-runs.
        "(w.model_name IS NULL OR "
        "(w.model_name NOT LIKE ? AND w.model_name != 'synthetic-dd-trigger'))",
    ]
    # NB: 'dd_%' is passed as a PARAM — a literal % in the SQL text would be
    # parsed as a placeholder prefix by psycopg and raise ProgrammingError.
    web_params: list[Any] = ["dd_%"]

    if ticker:
        web_where.append("UPPER(w.ticker) = UPPER(?)")
        web_params.append(ticker)
    if date_from:
        web_where.append("w.run_at >= ?")
        web_params.append(date_from)
    if date_to:
        web_where.append("w.run_at <= ?")
        web_params.append(date_to + "T23:59:59")
    if action:
        # Use summary column when populated; fall back to JSON field for legacy rows
        web_where.append(
            "(UPPER(w.final_action) = UPPER(?) OR "
            "(w.final_action IS NULL AND "
            f"UPPER({_json_field('decisions', col='w.full_result_json')}) LIKE UPPER(?)))"
        )
        web_params.extend([action, f"%{action}%"])
    if regime:
        web_where.append(
            "(UPPER(w.regime) = UPPER(?) OR "
            "(w.regime IS NULL AND "
            f"UPPER({_json_field('data.macro_regime.risk_appetite', col='w.full_result_json')}) = UPPER(?)))"
        )
        web_params.extend([regime, regime])
    if user_id is not None:
        # Show runs belonging to this user OR legacy runs with no owner (backward compat)
        web_where.append("(w.user_id = ? OR w.user_id IS NULL)")
        web_params.append(user_id)

    if sector:
        web_where.append(
            "(UPPER(w.sector) = UPPER(?) OR "
            "(w.sector IS NULL AND "
            f"UPPER({_json_field('data.sector', col='w.full_result_json')}) = UPPER(?)))"
        )
        web_params.extend([sector, sector])

    web_where_sql = "WHERE " + " AND ".join(web_where)

    # COUNT without JSON load — instant with idx_web_runs_run_at
    _cnt = _fetch_one(
        f"SELECT COUNT(*) AS n FROM web_runs w {web_where_sql}", web_params
    )
    web_total: int = _cnt["n"] if _cnt else 0

    # Fetch metadata only (no full_result_json) for all matching rows → used for total
    web_meta_rows = _fetch(
        f"SELECT w.run_id, w.run_at, w.ticker, w.model_name, "
        f"w.final_action, w.regime, w.sector "
        f"FROM web_runs w {web_where_sql} ORDER BY w.run_at DESC",
        web_params,
    )

    # ── 2. CLI archive runs (exclude any already imported via web) ────────
    imported_archive_ids = set(
        r["archive_run_id"]
        for r in _fetch(
            "SELECT archive_run_id FROM web_runs WHERE archive_run_id IS NOT NULL"
        )
    )

    cli_where: list[str] = ["r.run_id NOT IN ({})".format(
        ",".join("?" * len(imported_archive_ids)) if imported_archive_ids else "'__none__'"
    )]
    cli_params: list[Any] = list(imported_archive_ids)

    if ticker:
        cli_where.append("UPPER(ts.ticker) = UPPER(?)")
        cli_params.append(ticker)
    if date_from:
        cli_where.append("r.run_at >= ?")
        cli_params.append(date_from)
    if date_to:
        cli_where.append("r.run_at <= ?")
        cli_params.append(date_to + "T23:59:59")
    if regime:
        cli_where.append("UPPER(r.regime_risk_appetite) = UPPER(?)")
        cli_params.append(regime)
    if action:
        cli_where.append("UPPER(ts.final_action) = UPPER(?)")
        cli_params.append(action)
    if outcome:
        cli_where.append("UPPER(ts.outcome) = UPPER(?)")
        cli_params.append(outcome)
    if sector:
        cli_where.append("UPPER(r.sector) = UPPER(?)")
        cli_params.append(sector)

    cli_where_sql = "WHERE " + " AND ".join(cli_where)

    cli_rows = _fetch(
        f"""
        SELECT
            r.run_id, r.run_at, ts.ticker, r.model_name,
            r.regime_risk_appetite  AS regime,
            r.sector                AS sector,
            ts.final_action, ts.position_size_pct, ts.price_target, ts.stop_loss,
            ts.dcf_base_iv, ts.ev_upside_pct, ts.power_law_score,
            ts.value_trap_verdict,  ts.outcome, ts.pct_change,
            'cli' AS source
        FROM ticker_signals ts
        JOIN runs r ON r.run_id = ts.run_id
        {cli_where_sql}
        ORDER BY r.run_at DESC
        """,
        cli_params,
    )

    # ── 3. Merge metadata (no JSON) + sort → determine page slice ─────────
    web_light: list[dict] = [
        {
            "run_id":     r["run_id"],
            "run_at":     r["run_at"],
            "ticker":     r["ticker"],
            "model_name": r["model_name"],
            "source":     "web",
            # summary cols present on new rows; None on legacy rows
            "_final_action": r["final_action"],
            "_regime":       r["regime"],
            "_sector":       r["sector"],
        }
        for r in web_meta_rows
    ]

    cli_light: list[dict] = [
        {
            "run_id":           row["run_id"],
            "run_at":           row["run_at"],
            "ticker":           row["ticker"],
            "model_name":       row["model_name"],
            "source":           "cli",
            "regime":           row["regime"] or "",
            "sector":           row["sector"] or "",
            "final_action":     row["final_action"] or "",
            "position_size_pct": row["position_size_pct"],
            "price_target":     row["price_target"],
            "stop_loss":        row["stop_loss"],
            "dcf_base_iv":      row["dcf_base_iv"],
            "ev_upside_pct":    row["ev_upside_pct"],
            "power_law_score":  row["power_law_score"],
            "value_trap_verdict": row["value_trap_verdict"],
            "outcome":          row["outcome"] or "PENDING",
            "pct_change":       row["pct_change"],
            "vgpm_grades":      {},
        }
        for row in cli_rows
    ]

    all_light = web_light + cli_light

    # ISO timestamp sort — strip tz suffix so naive and aware strings compare equal
    all_light.sort(key=lambda x: x["run_at"][:26], reverse=True)

    total = len(all_light)
    page_slice = all_light[offset: offset + page_size]

    # ── 4. Enrich only the page slice — fetch JSON just for those run_ids ──
    web_page_ids = [
        item["run_id"] for item in page_slice if item["source"] == "web"
    ]
    json_by_id: dict[str, str] = {}
    if web_page_ids:
        placeholders = ",".join("?" * len(web_page_ids))
        for row in _fetch(
            f"SELECT run_id, full_result_json FROM web_runs WHERE run_id IN ({placeholders})",
            web_page_ids,
        ):
            json_by_id[row["run_id"]] = row["full_result_json"]

    page_items: list[dict] = []
    for item in page_slice:
        if item["source"] != "web":
            page_items.append(item)
            continue

        enriched: dict = {
            "run_id":     item["run_id"],
            "run_at":     item["run_at"],
            "ticker":     item["ticker"],
            "model_name": item["model_name"],
            "source":     "web",
            "final_action": item["_final_action"] or "",
            "regime":       item["_regime"] or "",
            "sector":       item["_sector"] or "",
        }

        raw_json = json_by_id.get(item["run_id"])
        if raw_json:
            try:
                result = _sanitize_floats(json.loads(raw_json))
                data = result.get("data", {})
                tickers_list = data.get("tickers", [item["ticker"]])
                t = tickers_list[0] if tickers_list else item["ticker"]

                # Fill summary fields from JSON if summary cols were NULL (legacy row)
                if not enriched["regime"]:
                    macro = data.get("macro_regime", {})
                    enriched["regime"] = (
                        macro.get("regime", {}).get("risk_appetite", "")
                        if isinstance(macro.get("regime"), dict)
                        else macro.get("risk_appetite", "")
                    )
                if not enriched["sector"]:
                    enriched["sector"] = data.get("sector", "") or ""

                decisions = result.get("decisions", {})
                if isinstance(decisions, dict):
                    td = decisions.get(t, {})
                    if not enriched["final_action"]:
                        enriched["final_action"] = td.get("action", "") or ""
                    enriched["position_size_pct"] = td.get("position_size_pct")
                    enriched["price_target"] = td.get("price_target")
                    enriched["stop_loss"] = td.get("stop_loss")

                dcf_range = data.get("dcf_range", {})
                dcf_ticker_data = dcf_range.get(t, {})
                if dcf_ticker_data and dcf_ticker_data.get("base"):
                    enriched["dcf_base_iv"] = dcf_ticker_data["base"].get("intrinsic_value")

                scenario = data.get("scenario_analysis", {}).get(t, {})
                enriched["ev_upside_pct"] = scenario.get("upside_pct")

                power_law = data.get("power_law_analysis", {}).get(t, {})
                enriched["power_law_score"] = (
                    power_law.get("total_score") or power_law.get("score")
                )

                value_trap = data.get("value_trap_analysis", {}).get(t, {})
                enriched["value_trap_verdict"] = (
                    value_trap.get("overall_verdict") or value_trap.get("verdict")
                )

                vgpm = result.get("vgpm", {}).get(t, {})
                enriched["vgpm_grades"] = (
                    {k: v.get("grade") for k, v in vgpm.items()} if vgpm else {}
                )
            except Exception:
                pass

        page_items.append(enriched)

    return {
        "items": page_items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def get_archive_summary() -> dict:
    """Counts from both web_runs and the CLI archive."""
    _ensure_web_runs_table()

    # Total: web runs + CLI runs not imported via web
    imported_ids = set(
        r["archive_run_id"]
        for r in _fetch(
            "SELECT archive_run_id FROM web_runs WHERE archive_run_id IS NOT NULL"
        )
    )
    _web_cnt = _fetch_one("SELECT COUNT(*) AS n FROM web_runs")
    web_count = _web_cnt["n"] if _web_cnt else 0
    if imported_ids:
        _cli_cnt = _fetch_one(
            f"SELECT COUNT(DISTINCT r.run_id) AS n FROM runs r "
            f"WHERE r.run_id NOT IN ({','.join('?' * len(imported_ids))})",
            list(imported_ids),
        )
    else:
        _cli_cnt = _fetch_one("SELECT COUNT(DISTINCT run_id) AS n FROM runs")
    cli_count = _cli_cnt["n"] if _cli_cnt else 0

    total = web_count + cli_count

    # Sector breakdown from CLI archive
    sector_breakdown: dict[str, int] = {}
    action_breakdown: dict[str, int] = {}
    outcome_breakdown: dict[str, int] = {}

    cli_rows = _fetch(
        "SELECT r.sector, ts.final_action, ts.outcome FROM ticker_signals ts "
        "JOIN runs r ON r.run_id = ts.run_id"
    )
    for row in cli_rows:
        sec = row["sector"] or "Unknown"
        act = row["final_action"] or "UNKNOWN"
        out = row["outcome"] or "PENDING"
        sector_breakdown[sec] = sector_breakdown.get(sec, 0) + 1
        action_breakdown[act] = action_breakdown.get(act, 0) + 1
        outcome_breakdown[out] = outcome_breakdown.get(out, 0) + 1

    # Augment with web-only runs (those without archive_run_id)
    web_only_rows = _fetch(
        "SELECT full_result_json FROM web_runs "
        "WHERE archive_run_id IS NULL AND full_result_json IS NOT NULL "
        f"AND {_json_field('checkpoint')} IS NULL"
    )
    for row in web_only_rows:
        # Name-based access on purpose: PG dict rows iterate KEYS, not values,
        # so the old `for (full_json,) in rows:` tuple-unpack would break there.
        full_json = row["full_result_json"]
        try:
            result = json.loads(full_json)
            data = result.get("data", {})
            tickers_list = data.get("tickers", [])
            t = tickers_list[0] if tickers_list else None
            if not t:
                continue
            routing = data.get("routing_decision", {})
            sec = routing.get(t, {}).get("sector", "Unknown") if isinstance(routing, dict) else "Unknown"
            decisions = result.get("decisions", {})
            act = decisions.get(t, {}).get("action", "UNKNOWN") if isinstance(decisions, dict) else "UNKNOWN"
            sector_breakdown[sec] = sector_breakdown.get(sec, 0) + 1
            action_breakdown[act] = action_breakdown.get(act, 0) + 1
            outcome_breakdown["PENDING"] = outcome_breakdown.get("PENDING", 0) + 1
        except Exception:
            pass

    return {
        "total_runs": total,
        "sector_breakdown": sector_breakdown,
        "action_breakdown": action_breakdown,
        "outcome_breakdown": outcome_breakdown,
    }


def _resolve_provider(model_name: str) -> str:
    """Infer provider from model name so the pipeline calls the correct API."""
    m = model_name.lower()
    if "qwen" in m:
        return "Alibaba"
    if "gpt" in m or "o1" in m or "o3" in m or "o4" in m:
        return "OpenAI"
    if "gemini" in m:
        return "Google"
    if "grok" in m:
        return "xAI"
    if "deepseek" in m:
        return "DeepSeek"
    if "groq" in m or "llama" in m or "mixtral" in m:
        return "Groq"
    # Default to Anthropic (Claude models)
    return "Anthropic"


# ── Pipeline runner ───────────────────────────────────────────────────────────

async def run_analysis_pipeline(
    ticker: str,
    model_name: str,
    api_keys: dict,
    on_phase: Callable[..., None],  # (phase, status, summary, reasoning, ticker, timestamp, partial_data)
    selected_agents: list[str] | None = None,
    user_id: Optional[int] = None,
    run_id: Optional[str] = None,
) -> tuple[str, dict]:
    """
    Run the 10-phase advanced pipeline for a ticker.
    Streams progress via on_phase callback.
    Returns (run_id, result_dict).

    run_id may be supplied by the caller (queue/enqueue path) so SSE clients
    can subscribe to progress:{run_id} before the job starts executing.
    """
    run_id = run_id or str(uuid.uuid4())
    loop = asyncio.get_event_loop()
    progress_queue: asyncio.Queue = asyncio.Queue()
    result_container: dict = {}
    error_container: dict = {}

    # ── Load .env.local once, process-wide ───────────────────────────────────
    # This is deployment-level config (same for every run), so os.environ is the
    # right home for it — and _load_env_local_once() makes it idempotent instead
    # of re-parsing the file on every request.
    _load_env_local_once()

    # ── Per-run settings overlay ─────────────────────────────────────────────
    # These are caller-supplied and therefore differ between concurrent runs.
    # They MUST NOT go into os.environ: that is process-global, so with two
    # users running at once the second run's keys clobbered the first's
    # mid-flight and User A's pipeline could execute against User B's key.
    # run_config keeps them in a ContextVar scoped to this run; every key read
    # downstream (src/llm/models.py, src/tools/api.py, deep_research.py) goes
    # through run_config.getenv() and falls back to os.environ when no run
    # context is active (CLI, schedulers).
    provider_to_env = {
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
        "financial_datasets": "FINANCIAL_DATASETS_API_KEY",
        "fmp": "FMP_API_KEY",
        "ANTHROPIC_API_KEY": "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY": "OPENAI_API_KEY",
        "FINANCIAL_DATASETS_API_KEY": "FINANCIAL_DATASETS_API_KEY",
        "FMP_API_KEY": "FMP_API_KEY",
    }
    _run_settings: dict[str, str] = {}
    for provider, key_value in api_keys.items():
        if key_value:
            _run_settings[provider_to_env.get(provider, provider)] = key_value

    # ── Register progress handler ─────────────────────────────────────────────
    from src.utils.progress import progress

    def _progress_handler(agent_name, ticker_sym, status, analysis, timestamp, partial_data=None, event_run_id=None):
        # Drop events that belong to a different concurrent run.
        # event_run_id is None for CLI / legacy callers — always pass those through.
        if event_run_id is not None and event_run_id != run_id:
            return
        # Normalise all terminal status strings to "Done" so the frontend
        # progress bar counts every completed phase uniformly.
        # The original descriptive string is preserved in `summary` for display.
        _sl = status.lower()
        _terminal = (
            _sl == "done"
            or status.startswith("✓")           # pipeline "✓ <msg>" completions
            or status.startswith("[cache]")      # power_law / value_trap cache hits
            or status.startswith("Cache HIT")    # deep_research pure cache hit
            or status.startswith("EDGAR")        # edgar_hkex_resolver: "EDGAR OK:", "EDGAR CIK...", "HKEX OK:"
            or _sl == "data routing complete"    # data_router final status
            or status.startswith("Score:")       # power_law live: "Score: 8/10 — ..."
            or _sl.startswith("trap risk")       # value_trap live: "TRAP RISK LOW/MEDIUM/HIGH"
        )
        normalized_status = "Done" if _terminal else status
        def _enqueue():
            progress_queue.put_nowait(
                {
                    "phase": agent_name,
                    "status": normalized_status,
                    "summary": status,
                    "reasoning": (analysis or ""),
                    "ticker": ticker_sym,
                    "timestamp": timestamp,
                    "partial_data": partial_data,
                }
            )

        loop.call_soon_threadsafe(_enqueue)

    # register_handler returns the handler itself as the ID
    progress.register_handler(_progress_handler)

    # ── Checkpoint callback (runs inside the pipeline thread) ────────────────
    def _on_checkpoint(state: dict, checkpoint_name: str) -> None:
        """Save partial pipeline state to web_runs so the run is visible early."""
        try:
            _save_partial_web_run(run_id, ticker.upper(), model_name, checkpoint_name, state,
                                  user_id=user_id)
        except Exception as _ck_e:
            print(f"  [checkpoint] _on_checkpoint failed ({checkpoint_name}): {_ck_e}")

    # ── Pipeline thread ───────────────────────────────────────────────────────
    def _run_pipeline():
        # Install this run's settings + run_id inside the thread's own context.
        # run_config.spawn() copies the caller's context, so these two calls
        # apply to this run only and are inherited by every ThreadPoolExecutor
        # worker the pipeline fans out to (submits go through run_config.submit).
        from src.utils.progress import progress as _prog
        run_config.set_run_settings(_run_settings)
        _prog.set_run_id(run_id)
        try:
            from src.pipeline import run_advanced_pipeline

            end_date = date.today().strftime("%Y-%m-%d")

            # Default minimal portfolio for web runs
            portfolio = {
                "cash": 100_000.0,
                "margin_requirement": 0.0,
                "positions": {},
                "realized_gains": {},
            }

            # selected_agents=[] (empty list) is falsy in Python — treat it as
            # "no preference" (all agents) only when truly None, not empty list.
            _agents = selected_agents if selected_agents else None

            # Qwen drives deep research (web search + free-text synthesis)
            # but structured pipeline phases (macro regime, investors, sector
            # classification, etc.) must use Claude because Qwen's thinking
            # mode blocks function_calling / Pydantic schema compliance.
            _is_qwen = model_name.startswith("qwen")
            if _is_qwen:
                # Per-run, not process-wide: a concurrent Claude run must not
                # inherit this user's Qwen deep-research model.
                run_config.update_run_settings({"DEEP_RESEARCH_MODEL": model_name})
                _pipeline_model = "claude-sonnet-4-6"
                _provider = "Anthropic"
            else:
                _pipeline_model = model_name
                _provider = "Anthropic"  # default for Claude/other models

            state = run_advanced_pipeline(
                tickers=[ticker.upper()],
                start_date="2020-01-01",
                end_date=end_date,
                portfolio=portfolio,
                selected_agents=_agents,
                model_name=_pipeline_model,
                model_provider=_provider,
                show_reasoning=True,
                enable_post_trade_review=False,
                on_checkpoint=_on_checkpoint,
            )
            result_container["state"] = state
        except Exception as e:
            error_container["error"] = str(e)
            import traceback

            error_container["traceback"] = traceback.format_exc()
        finally:
            loop.call_soon_threadsafe(
                lambda: progress_queue.put_nowait(
                    {"phase": "__done__", "status": "done", "summary": ""}
                )
            )

    # run_config.spawn (not threading.Thread) so the pipeline thread starts from
    # a copy of this context — without it the ContextVars set inside
    # _run_pipeline would not propagate to the pipeline's own worker pools.
    thread = run_config.spawn(_run_pipeline, daemon=True)
    thread.start()

    # ── Drain progress queue until __done__ ───────────────────────────────────
    # Wrapped in try/finally so the handler is ALWAYS unregistered even when the
    # client disconnects mid-run (asyncio.CancelledError escapes the await).
    # Without this, orphaned handlers from earlier runs accumulate in the progress
    # singleton and fire on subsequent runs, leaking partial_data (analyst_signals,
    # etc.) across unrelated runs.
    try:
        while True:
            try:
                event = await asyncio.wait_for(progress_queue.get(), timeout=2.0)
            except asyncio.TimeoutError:
                continue
            if event.get("phase") == "__done__":
                break
            on_phase(
                event.get("phase", ""),
                event.get("status", ""),
                event.get("summary", ""),
                event.get("reasoning", ""),
                event.get("ticker"),
                event.get("timestamp"),
                event.get("partial_data"),
            )
    finally:
        progress.unregister_handler(_progress_handler)
        thread.join(timeout=5)

    if error_container:
        raise RuntimeError(
            f"Pipeline error: {error_container.get('error')}\n"
            f"{error_container.get('traceback', '')}"
        )

    # run_advanced_pipeline() returns a flat dict — all pipeline outputs sit
    # at the top level (deep_research, scenario_analysis, dcf_range, …).
    # There is no nested "data" key.  Use the flat dict directly as data.
    pipeline_result = result_container.get("state", {})
    decisions = pipeline_result.get("decisions", {})
    data = pipeline_result          # flat dict IS the data payload

    # ── Build result dict ─────────────────────────────────────────────────────
    result: dict = {
        "run_id": run_id,
        "ticker": ticker.upper(),
        "model_name": model_name,
        "run_at": datetime.now(timezone.utc).isoformat(),
        "data": data,
        "decisions": decisions,
    }

    # ── VGPM — computed inside pipeline after Phase 7; reuse directly ───────────
    # pipeline.py computes _compute_vgpm right after scenario_analysis is set and
    # emits it as partial_data. We just carry the result through here.
    t = ticker.upper()
    pipeline_vgpm = pipeline_result.get("vgpm", {})
    result["vgpm"] = pipeline_vgpm if pipeline_vgpm else {}

    # ── Persist to web_runs ───────────────────────────────────────────────────
    # The pipeline already called save_run() internally and returned its archive_run_id.
    # We link to that existing row rather than calling save_run() again (which would
    # create a duplicate CLI-archive row that shows up as a second history entry).
    archive_run_id = pipeline_result.get("_archive_run_id")
    _save_web_run(run_id, t, model_name, result, archive_run_id=archive_run_id, user_id=user_id)

    return run_id, result
