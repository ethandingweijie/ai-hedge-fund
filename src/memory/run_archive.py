"""
src/memory/run_archive.py
=========================
Episodic run archive for the AI Hedge Fund pipeline.

Dual-mode storage:
  - PostgreSQL (production) when DATABASE_URL is set — all reads/writes go
    through the shared src.data.db layer. ensure_schema() creates/migrates
    the archive tables on first use.
  - SQLite (local dev) otherwise — the .db file lives at DB_PATH
    (RUN_ARCHIVE_PATH or src/data/run_archive.db). _get_conn() is kept for
    CLI backfill scripts that operate on the local file directly.

Tables:
  runs           — one row per pipeline run (regime + metadata + industry brief + deep research)
  ticker_signals — one row per ticker per run (final decision + intel + DCF + debate + PM rationale)
  agent_signals  — one row per agent per ticker per run (thesis, price target, key risks)
  rotation_events, ticker_routing_cache

Outcome columns in ticker_signals / agent_signals are filled later by
run_post_trade_review() → update_outcomes().

Usage:
    from src.memory.run_archive import save_run, load_runs, update_outcomes, get_agent_outcomes

Rows are always accessed by column name (works for sqlite3.Row and Postgres
dict rows alike).

Schema version: 2  (added industry_brief, deep_research, DCF, debate, PM rationale, agent thesis)
"""

import json
import logging
import os
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta
from typing import Any

from src.data import db

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

DB_PATH = os.environ.get("RUN_ARCHIVE_PATH",
          os.path.join(os.path.dirname(__file__), "..", "data", "run_archive.db"))
PIPELINE_VERSION = "2.0"

# M2 A3 — marker for the dated LATEST DEVELOPMENTS addendum appended to
# REUSED (pure-cache / delta) research text. Both section parsers
# (_parse_sections_inline here, deep_research._extract_sections) cut the
# text at this marker BEFORE parsing, so an archived text carrying the
# addendum re-parses into the identical sections — the C2 extractor hash
# stays stable and extractor reuse survives the addendum. Keep the literal
# in lock-step in both parsers (deep_research imports this constant).
LATEST_DEV_ADDENDUM_MARKER = "\n\n---\n\n## LATEST DEVELOPMENTS (as of "

# ── Schema (DDL for fresh databases) ─────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS runs (
    run_id                TEXT PRIMARY KEY,
    run_at                TEXT NOT NULL,          -- ISO-8601 wall-clock timestamp
    analysis_date         TEXT NOT NULL,          -- state["data"]["end_date"]
    sector                TEXT,
    regime_risk_appetite  TEXT,
    regime_rate_direction TEXT,
    regime_volatility     TEXT,
    regime_dollar         TEXT,
    regime_recession_risk TEXT,                   -- "low" | "elevated" | "high"
    tickers               TEXT NOT NULL,          -- JSON array e.g. '["AAPL","NVDA"]'
    model_name            TEXT,
    pipeline_version      TEXT DEFAULT '2.0',

    -- Research quality metadata
    research_tier         TEXT,                   -- "anthropic_web" | "tavily" | "knowledge_only" | "none"
    research_as_of        TEXT,                   -- M2: content date the research reflects (ISO-8601
                                                  -- date or timestamp). NULL = fall back to run_at.
                                                  -- Full live pass -> today; delta success -> today;
                                                  -- pure cache -> inherited from the source run.

    -- Full text outputs (for audit trail and backtesting context)
    industry_brief_text   TEXT,                   -- full industry intelligence brief
    deep_research_text    TEXT                    -- full Section 2 deep research report (2A-2F)
);

CREATE TABLE IF NOT EXISTS ticker_signals (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                TEXT NOT NULL REFERENCES runs(run_id),
    ticker                TEXT NOT NULL,

    -- Portfolio manager final decision
    final_action          TEXT,                   -- BUY/SELL/SHORT/HOLD/COVER
    position_size_pct     REAL,
    price_target          REAL,
    stop_loss             REAL,
    entry_range_low       REAL,
    entry_range_high      REAL,
    time_horizon          TEXT,                   -- short/medium/long
    pm_rationale          TEXT,                   -- PM plain-English rationale

    -- Equity price at run time
    price_at_run          REAL,                   -- last close at run time (for outcome scoring)

    -- DCF engine output
    dcf_base_iv           REAL,                   -- base intrinsic value
    dcf_wacc              REAL,                   -- WACC used
    dcf_iv_vs_price_pct   REAL,                   -- (dcf_base_iv - price_at_run) / price_at_run * 100

    -- Debate round
    debate_triggered      INTEGER DEFAULT 0,       -- 0/1 boolean
    debate_adjudicated_signal TEXT,

    -- Phase 2.5 intelligence signals (deterministic)
    si_signal             TEXT,                   -- short interest signal
    si_short_float_pct    REAL,
    si_squeeze_risk       INTEGER,                -- 0/1 boolean
    si_crowded_trade      INTEGER,                -- 0/1 boolean
    insider_signal        TEXT,
    revision_direction    TEXT,
    news_signal           TEXT,
    eq_quality_verdict    TEXT,
    eq_quality_score      REAL,

    -- Phase 7 analysis
    value_trap_verdict    TEXT,
    ev_upside_pct         REAL,
    power_law_score       REAL,

    -- Post-trade outcome (filled by update_outcomes)
    review_date           TEXT,
    price_at_review       REAL,
    pct_change            REAL,
    outcome               TEXT DEFAULT 'PENDING', -- CORRECT/NEUTRAL/INCORRECT/PENDING

    UNIQUE(run_id, ticker)
);

CREATE TABLE IF NOT EXISTS agent_signals (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                TEXT NOT NULL REFERENCES runs(run_id),
    ticker                TEXT NOT NULL,
    agent_key             TEXT NOT NULL,
    signal                TEXT,                   -- BUY/SELL/SHORT/HOLD
    conviction            INTEGER,
    price_target          REAL,                   -- agent's price target
    time_horizon          TEXT,                   -- agent's time horizon
    thesis_summary        TEXT,                   -- 2-3 sentence thesis
    key_risks             TEXT,                   -- JSON array e.g. '["risk1","risk2"]'

    -- Post-trade outcome (filled by update_outcomes)
    outcome               TEXT DEFAULT 'PENDING', -- CORRECT/NEUTRAL/INCORRECT/PENDING

    UNIQUE(run_id, ticker, agent_key)
);

CREATE TABLE IF NOT EXISTS rotation_events (
    event_id        TEXT PRIMARY KEY,
    event_at        TEXT NOT NULL,               -- ISO-8601 timestamp
    old_regime      TEXT,                        -- JSON regime dict
    new_regime      TEXT,                        -- JSON regime dict
    shift_score     INTEGER,                     -- 0-10
    shift_label     TEXT,                        -- SIGNIFICANT | MINOR | NONE
    recommendations TEXT,                        -- JSON array of per-ticker recommendations
    sector_signal   TEXT,                        -- JSON {reduce: [...], overweight: [...]}
    alert_sent      INTEGER DEFAULT 0            -- 0/1 boolean
);

CREATE TABLE IF NOT EXISTS ticker_routing_cache (
    ticker                TEXT PRIMARY KEY,
    sector                TEXT NOT NULL,
    sector_llm_raw        TEXT,
    sector_confidence     TEXT,
    sector_warning        TEXT,
    company_name          TEXT,
    routing_decision_json TEXT,
    raw_financials_json   TEXT,
    reported_currency     TEXT,                  -- M2 B2: FMP reportedCurrency (e.g. CNY)
    last_updated          TEXT NOT NULL          -- ISO-8601 timestamp
);

-- C2: persisted sector-extractor fan-out outputs, keyed by research content.
-- A cache hit whose sections hash matches reuses these instead of re-running
-- the LLM fan-out. Keyed on (ticker, sections_hash) rather than run_id:
-- extraction is a pure function of sections + profile, so identical content
-- is reusable across runs, and the writer (deep_research) does not know the
-- run_id at extraction time (it is minted later by save_run).
CREATE TABLE IF NOT EXISTS extractor_outputs (
    ticker        TEXT NOT NULL,
    sections_hash TEXT NOT NULL,                 -- sha256 of sections + profile
    outputs_json  TEXT NOT NULL,                 -- {"results": {...}, "failures": [...]}
    extracted_at  TEXT NOT NULL,                 -- ISO-8601 timestamp
    PRIMARY KEY (ticker, sections_hash)
);

-- R2: persisted citation registry, keyed by research FULL-TEXT hash.
-- The registry was rebuilt via LLM on EVERY path (~128s of the cached-run
-- floor). Extraction is a pure function of the report text, so identical
-- text is reusable across runs.
CREATE TABLE IF NOT EXISTS citation_registry (
    ticker        TEXT NOT NULL,
    text_hash     TEXT NOT NULL,                 -- sha256 of deep_research_text
    registry_json TEXT NOT NULL,                 -- list of registry entries
    extracted_at  TEXT NOT NULL,                 -- ISO-8601 timestamp
    PRIMARY KEY (ticker, text_hash)
);
"""

# ── Postgres DDL (full column set — CREATE covers everything _MIGRATIONS adds,
#    so a fresh Postgres database needs no ALTERs). No FK REFERENCES on
#    purpose: SQLite never enforced them (foreign_keys pragma off by default)
#    and the archive is best-effort storage — a constraint failure must not
#    sink a pipeline run's save.
#    NOTE: db.execute_script() splits on ";", so no semicolons in comments.

_PG_DDL = """
CREATE TABLE IF NOT EXISTS runs (
    run_id                TEXT PRIMARY KEY,
    run_at                TEXT NOT NULL,
    analysis_date         TEXT NOT NULL,
    sector                TEXT,
    regime_risk_appetite  TEXT,
    regime_rate_direction TEXT,
    regime_volatility     TEXT,
    regime_dollar         TEXT,
    regime_recession_risk TEXT,
    tickers               TEXT NOT NULL,
    model_name            TEXT,
    pipeline_version      TEXT DEFAULT '2.0',
    research_tier         TEXT,
    research_as_of        TEXT,
    industry_brief_text   TEXT,
    deep_research_text    TEXT
);

CREATE TABLE IF NOT EXISTS ticker_signals (
    id                    BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    run_id                TEXT NOT NULL,
    ticker                TEXT NOT NULL,
    final_action          TEXT,
    position_size_pct     DOUBLE PRECISION,
    price_target          DOUBLE PRECISION,
    stop_loss             DOUBLE PRECISION,
    entry_range_low       DOUBLE PRECISION,
    entry_range_high      DOUBLE PRECISION,
    time_horizon          TEXT,
    pm_rationale          TEXT,
    price_at_run          DOUBLE PRECISION,
    dcf_base_iv           DOUBLE PRECISION,
    dcf_bear_iv           DOUBLE PRECISION,
    dcf_bull_iv           DOUBLE PRECISION,
    dcf_wacc              DOUBLE PRECISION,
    dcf_iv_vs_price_pct   DOUBLE PRECISION,
    debate_triggered      INTEGER DEFAULT 0,
    debate_adjudicated_signal TEXT,
    si_signal             TEXT,
    si_short_float_pct    DOUBLE PRECISION,
    si_squeeze_risk       INTEGER,
    si_crowded_trade      INTEGER,
    insider_signal        TEXT,
    revision_direction    TEXT,
    news_signal           TEXT,
    eq_quality_verdict    TEXT,
    eq_quality_score      DOUBLE PRECISION,
    value_trap_verdict    TEXT,
    ev_upside_pct         DOUBLE PRECISION,
    power_law_score       DOUBLE PRECISION,
    review_date           TEXT,
    price_at_review       DOUBLE PRECISION,
    pct_change            DOUBLE PRECISION,
    outcome               TEXT DEFAULT 'PENDING',
    power_law_json        TEXT,
    scenario_json         TEXT,
    raw_financials_json   TEXT,
    citation_audit_json   TEXT,
    vgpm_json             TEXT,
    dcf_range_json        TEXT,
    value_trap_json       TEXT,
    sector_card_json      TEXT,
    sector_card_hash      TEXT,
    card_qa_json          TEXT,
    financial_statements_json TEXT,
    UNIQUE(run_id, ticker)
);

CREATE TABLE IF NOT EXISTS agent_signals (
    id                    BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    run_id                TEXT NOT NULL,
    ticker                TEXT NOT NULL,
    agent_key             TEXT NOT NULL,
    signal                TEXT,
    conviction            INTEGER,
    price_target          DOUBLE PRECISION,
    time_horizon          TEXT,
    thesis_summary        TEXT,
    key_risks             TEXT,
    outcome               TEXT DEFAULT 'PENDING',
    UNIQUE(run_id, ticker, agent_key)
);

CREATE TABLE IF NOT EXISTS rotation_events (
    event_id        TEXT PRIMARY KEY,
    event_at        TEXT NOT NULL,
    old_regime      TEXT,
    new_regime      TEXT,
    shift_score     INTEGER,
    shift_label     TEXT,
    recommendations TEXT,
    sector_signal   TEXT,
    alert_sent      INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS ticker_routing_cache (
    ticker                TEXT PRIMARY KEY,
    sector                TEXT NOT NULL,
    sector_llm_raw        TEXT,
    sector_confidence     TEXT,
    sector_warning        TEXT,
    company_name          TEXT,
    routing_decision_json TEXT,
    raw_financials_json   TEXT,
    reported_currency     TEXT,
    last_updated          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS extractor_outputs (
    ticker        TEXT NOT NULL,
    sections_hash TEXT NOT NULL,
    outputs_json  TEXT NOT NULL,
    extracted_at  TEXT NOT NULL,
    PRIMARY KEY (ticker, sections_hash)
);

CREATE TABLE IF NOT EXISTS citation_registry (
    ticker        TEXT NOT NULL,
    text_hash     TEXT NOT NULL,
    registry_json TEXT NOT NULL,
    extracted_at  TEXT NOT NULL,
    PRIMARY KEY (ticker, text_hash)
);
"""

# ── Migrations (for existing databases missing new columns) ───────────────────
# Each entry is (table, column, definition). Applied with ALTER TABLE ADD COLUMN
# inside a try/except so they are silently skipped if the column already exists.

_MIGRATIONS = [
    # v1 additions to runs (production DB pre-dates the CREATE TABLE
    # having these columns — older Railway volumes still need the
    # ALTER TABLE path. Without this, save_run hits:
    #   "table runs has no column named analysis_date"
    # and the archive write silently fails while web_runs persists.
    # Symptom: run completes + frontend sees it, but /analysis/runs
    # endpoint (which reads ticker_signals) shows stale totals.)
    ("runs", "analysis_date",        "TEXT"),
    ("runs", "sector",               "TEXT"),
    ("runs", "regime_risk_appetite", "TEXT"),
    ("runs", "regime_rate_direction","TEXT"),
    ("runs", "regime_volatility",    "TEXT"),
    ("runs", "regime_dollar",        "TEXT"),
    ("runs", "tickers",              "TEXT"),
    ("runs", "model_name",           "TEXT"),
    ("runs", "pipeline_version",     "TEXT DEFAULT '2.0'"),

    # v2 additions to runs
    ("runs", "research_tier",        "TEXT"),
    ("runs", "research_as_of",       "TEXT"),   # M2 A2 — content-age reuse trigger
    ("runs", "industry_brief_text",  "TEXT"),
    ("runs", "deep_research_text",   "TEXT"),
    ("runs", "regime_recession_risk","TEXT"),   # v2.1 — 5th regime dimension

    # v2 additions to ticker_signals
    ("ticker_signals", "entry_range_low",           "REAL"),
    ("ticker_signals", "entry_range_high",          "REAL"),
    ("ticker_signals", "time_horizon",              "TEXT"),
    ("ticker_signals", "pm_rationale",              "TEXT"),
    ("ticker_signals", "price_at_run",              "REAL"),   # last close at run time — DDL has it; migration missing on Railway volume
    ("ticker_signals", "dcf_base_iv",               "REAL"),
    ("ticker_signals", "dcf_bear_iv",               "REAL"),
    ("ticker_signals", "dcf_bull_iv",               "REAL"),
    ("ticker_signals", "dcf_wacc",                  "REAL"),
    ("ticker_signals", "dcf_iv_vs_price_pct",       "REAL"),
    ("ticker_signals", "debate_triggered",          "INTEGER DEFAULT 0"),
    ("ticker_signals", "debate_adjudicated_signal", "TEXT"),

    # v2 additions to agent_signals
    ("agent_signals", "price_target",    "REAL"),
    ("agent_signals", "time_horizon",    "TEXT"),
    ("agent_signals", "thesis_summary",  "TEXT"),
    ("agent_signals", "key_risks",       "TEXT"),

    # v3 — full JSON blobs for rich frontend display
    ("ticker_signals", "power_law_json",      "TEXT"),  # full power_law_analysis[ticker] dict
    ("ticker_signals", "scenario_json",       "TEXT"),  # full scenario_analysis[ticker] dict
    ("ticker_signals", "raw_financials_json", "TEXT"),  # raw financials dict (FY-keyed)
    ("ticker_signals", "citation_audit_json", "TEXT"),  # citation_audit[ticker] dict
    ("ticker_signals", "vgpm_json",           "TEXT"),  # VGPM scorecard result

    # v3.1 — full dcf_range[ticker] dict for cache reuse
    ("ticker_signals", "dcf_range_json",      "TEXT"),  # full dcf_range[ticker] dict

    # v3.2 — full value_trap_analysis[ticker] dict for cache reuse
    ("ticker_signals", "value_trap_json",     "TEXT"),  # full value_trap_analysis[ticker] dict

    # v4 — Phase 2.5 intelligence signals + Phase 7 analysis columns
    # These were added to the DDL but never propagated to _MIGRATIONS, so
    # production DBs (Railway volume) created before the DDL update never
    # received them. Symptom on save_run:
    #   "table ticker_signals has no column named si_signal"
    # → archive write silently fails, watchlist + backtest scoring miss
    # the run, [archive] 0 run(s) stored | 0 scored | 0 pending.
    ("ticker_signals", "si_signal",            "TEXT"),
    ("ticker_signals", "si_short_float_pct",   "REAL"),
    ("ticker_signals", "si_squeeze_risk",      "INTEGER"),
    ("ticker_signals", "si_crowded_trade",     "INTEGER"),
    ("ticker_signals", "insider_signal",       "TEXT"),
    ("ticker_signals", "revision_direction",   "TEXT"),
    ("ticker_signals", "news_signal",          "TEXT"),
    ("ticker_signals", "eq_quality_verdict",   "TEXT"),
    ("ticker_signals", "eq_quality_score",     "REAL"),
    ("ticker_signals", "value_trap_verdict",   "TEXT"),
    ("ticker_signals", "ev_upside_pct",        "REAL"),
    ("ticker_signals", "power_law_score",      "REAL"),

    # v4 — outcome-scoring columns (filled by update_outcomes job)
    ("ticker_signals", "review_date",          "TEXT"),
    ("ticker_signals", "price_at_review",      "REAL"),
    ("ticker_signals", "pct_change",           "REAL"),
    ("ticker_signals", "outcome",              "TEXT DEFAULT 'PENDING'"),

    # v4 — agent_signals outcome (parity with ticker_signals.outcome)
    ("agent_signals",  "outcome",              "TEXT DEFAULT 'PENDING'"),

    # v3.3 — sector-specific valuation card payload (Option B render).
    # Built by src.data.sector_kpi_framework.render_card_payload. Persisting
    # the rendered JSON (not just the raw metric values) means historical
    # runs continue to render correctly even if the framework spec changes.
    ("ticker_signals", "sector_card_json",    "TEXT"),  # full sector_card[ticker] dict

    # v3.4 — R5 card-QA delta check (Workstream E speed round 2).
    # sector_card_hash: sha256 over rendered sector_card[ticker] + deep research
    # text, computed in pipeline phase 10_5. card_qa_json: the full
    # card_qa_audit[ticker] dict. Together they let an unchanged re-run skip
    # the QA LLM pass and reuse the prior clean audit.
    ("ticker_signals", "sector_card_hash",    "TEXT"),
    ("ticker_signals", "card_qa_json",        "TEXT"),

    # v5 — three-statement financials (income / balance / cash flow with
    # derived YoY growth), built from FMP line items rather than the LLM
    # echo in raw_financials_json. Additive: raw_financials_json is
    # unchanged and still what dcf_agent and the extractors read.
    ("ticker_signals", "financial_statements_json", "TEXT"),

    # M2 B2 — reporting currency for the cached FMP financials (currency-
    # labeling of research prompts + FX-aware consistency check)
    ("ticker_routing_cache", "reported_currency", "TEXT"),
]


# ── Internal helpers ──────────────────────────────────────────────────────────

_schema_lock = threading.Lock()
_pg_schema_ready = False
_sqlite_schema_paths: set[str] = set()


def _sqlite_conn_for(path: str) -> sqlite3.Connection:
    """Open a SQLite connection, applying schema/migrations once per path."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    if path not in _sqlite_schema_paths:
        with _schema_lock:
            if path not in _sqlite_schema_paths:
                # Create tables (idempotent for fresh DBs)
                conn.executescript(_DDL)
                conn.commit()
                # Apply column-level migrations for existing DBs
                for table, column, definition in _MIGRATIONS:
                    try:
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
                        conn.commit()
                    except sqlite3.OperationalError:
                        pass  # Column already exists — ignore
                _sqlite_schema_paths.add(path)
    return conn


def _get_conn() -> sqlite3.Connection:
    """Open (and initialise / migrate if needed) the SQLite archive database.

    SQLite/local-dev path only. Production (DATABASE_URL set) goes through
    src.data.db — see ensure_schema(). Kept public-ish for CLI backfill
    scripts that operate on the local run_archive.db file directly.
    """
    return _sqlite_conn_for(DB_PATH)


def _pg_definition(definition: str) -> str:
    """SQLite column definition -> Postgres equivalent (REAL is 8-byte in
    SQLite but only 4-byte in Postgres; map it to DOUBLE PRECISION)."""
    return re.sub(r"\bREAL\b", "DOUBLE PRECISION", definition, flags=re.IGNORECASE)


def ensure_schema() -> None:
    """Create/migrate the archive tables in the active backend (idempotent).

    Called lazily before Postgres reads/writes. In SQLite mode this just
    touches the local DB so its schema is initialised too.
    """
    global _pg_schema_ready
    if not db.is_postgres():
        _get_conn().close()
        return
    if _pg_schema_ready:
        return
    with _schema_lock:
        if _pg_schema_ready:
            return
        db.execute_script(_PG_DDL)
        for table, column, definition in _MIGRATIONS:
            db.add_column_if_missing(table, column, _pg_definition(definition))
        _pg_schema_ready = True


def _fetch(sql: str, params: list | None = None) -> list:
    """SELECT returning name-accessible rows (sqlite3.Row / PG dict rows)."""
    if db.is_postgres():
        ensure_schema()
        return db.query(sql, params or [])
    conn = _get_conn()
    try:
        return conn.execute(sql, params or []).fetchall()
    finally:
        conn.close()


def _fetch_one(sql: str, params: list | None = None):
    """SELECT returning the first row or None."""
    rows = _fetch(sql, params)
    return rows[0] if rows else None


def _exec(sql: str, params: list | None = None) -> int:
    """Run a write statement on the active backend; returns rowcount."""
    if db.is_postgres():
        ensure_schema()
        return db.execute(sql, params or [])
    conn = _get_conn()
    try:
        cur = conn.execute(sql, params or [])
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def _insert_ignore_sql(table: str, columns: list[str], conflict_target: str) -> str:
    """INSERT that skips conflicts, in the active dialect.

    SQLite: INSERT OR IGNORE. Postgres: ON CONFLICT (<target>) DO NOTHING.
    """
    cols = ", ".join(columns)
    phs = ", ".join("?" * len(columns))
    if db.is_postgres():
        return (f"INSERT INTO {table} ({cols}) VALUES ({phs}) "
                f"ON CONFLICT ({conflict_target}) DO NOTHING")
    return f"INSERT OR IGNORE INTO {table} ({cols}) VALUES ({phs})"


class _Txn:
    """Write context standing in for a sqlite3 connection in save_run().

    SQLite: wraps a real connection; the `with` block commits/rolls back the
    whole save as one transaction (original behaviour).
    Postgres: each execute() goes through src.data.db with its own commit —
    run_id is a fresh UUID and child inserts use ON CONFLICT DO NOTHING, so
    a partial save on failure can't cause duplicates on retry.
    """

    def __init__(self):
        self._pg = db.is_postgres()
        if self._pg:
            ensure_schema()
            self._conn = None
        else:
            self._conn = _get_conn()

    def execute(self, sql: str, params=()) -> None:
        if self._pg:
            db.execute(sql, list(params))
        else:
            self._conn.execute(sql, params)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._conn is not None:
            if exc_type is None:
                self._conn.commit()
            else:
                self._conn.rollback()
        return False

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None


def _safe(value: Any, cast=None):
    """Return value cast to cast(), or None if value is None."""
    if value is None:
        return None
    try:
        return cast(value) if cast else value
    except (TypeError, ValueError):
        return None


def _safe_json(value: Any) -> str | None:
    """Serialise value to JSON string, or None on failure."""
    if value is None:
        return None
    try:
        return json.dumps(value)
    except (TypeError, ValueError):
        return None


# ── Public API ────────────────────────────────────────────────────────────────

def save_run(state: dict, decisions: dict) -> str:
    """
    Persist a completed pipeline run to the archive.

    Parameters
    ----------
    state     : pipeline state dict (state["data"] contains all intel + regime)
    decisions : portfolio manager decisions dict  {ticker: {action, position_size_pct, ...}}

    Returns
    -------
    run_id : str — UUID of the new row in `runs`
    """
    run_id = str(uuid.uuid4())
    run_at = datetime.now().isoformat()

    data   = state.get("data", {})
    meta   = state.get("metadata", {})
    tickers: list[str] = data.get("tickers", [])
    regime: dict       = data.get("macro_regime", {})

    try:
        conn = _Txn()
        with conn:
            # ── runs row ──────────────────────────────────────────────────────
            conn.execute(
                """
                INSERT INTO runs (
                    run_id, run_at, analysis_date, sector,
                    regime_risk_appetite, regime_rate_direction,
                    regime_volatility, regime_dollar, regime_recession_risk,
                    tickers, model_name, pipeline_version,
                    research_tier, research_as_of,
                    industry_brief_text, deep_research_text
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    run_id,
                    run_at,
                    data.get("end_date", ""),
                    data.get("sector"),
                    regime.get("risk_appetite"),
                    regime.get("rate_direction"),
                    regime.get("volatility_regime"),
                    regime.get("dollar_trend"),
                    regime.get("recession_risk"),
                    json.dumps(tickers),
                    meta.get("model_name"),
                    PIPELINE_VERSION,
                    data.get("research_tier"),          # set by deep_research.py
                    data.get("research_as_of"),         # M2 A2: content date (deep_research.py)
                    data.get("industry_brief") or data.get("deep_research") or None,
                    data.get("deep_research") or None,
                ),
            )

            # ── per-ticker rows ───────────────────────────────────────────────
            analyst_signals: dict = data.get("analyst_signals", {})
            skip_agents = {"risk_management_agent", "advanced_risk_manager"}
            # Debate round decommissioned (M2 Track E): the columns stay for
            # historical rows but new runs always write 0/NULL.
            dcf_range: dict = data.get("dcf_range", {})

            for ticker in tickers:
                decision = decisions.get(ticker, {})

                # Phase 2.5 signals
                si = data.get("short_interest", {}).get(ticker, {})
                ia = data.get("insider_activity", {}).get(ticker, {})
                ar = data.get("analyst_revisions", {}).get(ticker, {})
                ns = data.get("news_sentiment", {}).get(ticker, {})
                eq = data.get("earnings_quality", {}).get(ticker, {})

                # Phase 7
                trap = data.get("value_trap_analysis", {}).get(ticker, {})
                scen = data.get("scenario_analysis", {}).get(ticker, {})
                pl   = data.get("power_law_analysis", {}).get(ticker, {})

                # DCF — persist all three scenario intrinsic values
                dcf      = dcf_range.get(ticker, {})
                dcf_iv   = _safe(dcf.get("base", {}).get("intrinsic_value") if dcf else None, float)
                dcf_iv_b = _safe(dcf.get("bear", {}).get("intrinsic_value") if dcf else None, float)
                dcf_iv_u = _safe(dcf.get("bull", {}).get("intrinsic_value") if dcf else None, float)
                dcf_w    = _safe(dcf.get("wacc") if dcf else None, float)

                # Best-effort last close price
                price_at_run: float | None = None
                try:
                    prices = data.get("routed_data", {}).get(ticker, {}).get("prices", [])
                    if prices:
                        price_at_run = float(prices[-1].close)
                except Exception:
                    pass

                # DCF margin-of-safety
                dcf_iv_vs_price: float | None = None
                if dcf_iv is not None and price_at_run and price_at_run > 0:
                    dcf_iv_vs_price = round((dcf_iv - price_at_run) / price_at_run * 100, 2)

                # Debate — decommissioned (M2 Track E); columns kept for
                # historical rows, new runs always write 0/NULL.
                debate_triggered = 0
                debate_adj_signal = None

                # Entry range
                entry_range = decision.get("entry_range") or []
                entry_low  = _safe(entry_range[0], float) if len(entry_range) > 0 else None
                entry_high = _safe(entry_range[1], float) if len(entry_range) > 1 else None

                # ── v3 JSON blobs ─────────────────────────────────────────────
                pl_json      = _safe_json(pl)   if pl   is not None else None
                scen_json    = _safe_json(scen) if scen is not None else None
                dcf_rng_json = _safe_json(dcf)  if dcf  is not None else None  # v3.1
                vt_json      = _safe_json(trap) if trap is not None else None  # v3.2

                # v3.3 — sector_card payload (Option B render). Persisted as
                # JSON so historical runs continue to render even if the
                # framework spec changes structure later.
                sector_card_t = (data.get("sector_card") or {}).get(ticker)
                sc_json = _safe_json(sector_card_t) if sector_card_t is not None else None

                # v3.4 — R5 card-QA delta check: persist the card content hash
                # and the QA audit so an unchanged future re-run can reuse the
                # audit instead of re-running the QA LLM pass.
                sc_hash = (data.get("sector_card_hash") or {}).get(ticker)
                card_qa_t = (data.get("card_qa_audit") or {}).get(ticker)
                cq_json = _safe_json(card_qa_t) if card_qa_t is not None else None

                # raw_financials lives at state["data"]["raw_financials"] (LLM-formatted,
                # keyed by FY year) — store as-is so FinancialsTable can render it
                raw_fin = data.get("raw_financials") or {}
                raw_fin_json = _safe_json(raw_fin) if raw_fin else None

                ca_json = _safe_json(data.get("citation_audit", {}).get(ticker))

                # Three-statement view (data_router builds it from FMP rows).
                _fin_stmts = data.get("financial_statements") or {}
                fin_stmts_json = _safe_json(_fin_stmts) if _fin_stmts else None

                # VGPM — compute inline so CLI runs get the same scorecard as web runs
                vgpm_json = None
                try:
                    from src.utils.pdf_report import _compute_vgpm
                    dcf_cal_data: dict = {}
                    if dcf and dcf.get("base"):
                        dcf_cal_data = {
                            "margin_direction": dcf.get("base", {}).get("margin_direction", "stable"),
                            "risk_flag":        dcf.get("base", {}).get("risk_flag", ""),
                        }
                    insider_d   = analyst_signals.get("insider_activity_agent", {}).get(ticker, {})
                    insider_sum = insider_d.get("summary", "") if isinstance(insider_d, dict) else ""
                    # ROIC-proxy input from the shared knowledge graph (same fix
                    # as src/pipeline.py's live VGPM call) — falls back to
                    # raw_fin (LLM-formatted, used as-is for raw_fin_json above)
                    # if the KG fetch fails for any reason.
                    vgpm_raw_fin = raw_fin
                    try:
                        from app.backend.services.knowledge_graph import get_kg_annual_line_items
                        _kg_fin = get_kg_annual_line_items(
                            ticker, data.get("end_date", ""), sector=data.get("sector"),
                        )
                        if _kg_fin:
                            vgpm_raw_fin = _kg_fin
                    except Exception:
                        pass
                    vgpm_result = _compute_vgpm(
                        dcf_ticker=dcf,
                        scen_ticker=scen,
                        raw_financials=vgpm_raw_fin,
                        dcf_cal=dcf_cal_data,
                        insider_summary=insider_sum,
                    )
                    vgpm_json = _safe_json(vgpm_result)
                except Exception:
                    pass

                conn.execute(
                    _insert_ignore_sql(
                        "ticker_signals",
                        [
                            "run_id", "ticker",
                            "final_action", "position_size_pct", "price_target", "stop_loss",
                            "entry_range_low", "entry_range_high", "time_horizon", "pm_rationale",
                            "price_at_run",
                            "dcf_base_iv", "dcf_bear_iv", "dcf_bull_iv", "dcf_wacc", "dcf_iv_vs_price_pct",
                            "debate_triggered", "debate_adjudicated_signal",
                            "si_signal", "si_short_float_pct", "si_squeeze_risk", "si_crowded_trade",
                            "insider_signal", "revision_direction", "news_signal",
                            "eq_quality_verdict", "eq_quality_score",
                            "value_trap_verdict", "ev_upside_pct", "power_law_score",
                            "power_law_json", "scenario_json", "raw_financials_json",
                            "citation_audit_json", "vgpm_json", "dcf_range_json", "value_trap_json",
                            "sector_card_json", "sector_card_hash", "card_qa_json",
                            "financial_statements_json",
                        ],
                        "run_id, ticker",
                    ),
                    (
                        run_id, ticker,
                        decision.get("action"),
                        _safe(decision.get("position_size_pct"), float),
                        _safe(decision.get("price_target"), float),
                        _safe(decision.get("stop_loss"), float),
                        entry_low,
                        entry_high,
                        decision.get("time_horizon"),
                        decision.get("rationale"),
                        price_at_run,
                        dcf_iv,
                        dcf_iv_b,
                        dcf_iv_u,
                        dcf_w,
                        dcf_iv_vs_price,
                        debate_triggered,
                        debate_adj_signal,
                        si.get("signal"),
                        _safe(si.get("short_float_pct"), float),
                        int(bool(si.get("squeeze_risk"))),
                        int(bool(si.get("crowded_trade"))),
                        ia.get("signal"),
                        ar.get("revision_direction"),
                        ns.get("signal"),
                        eq.get("quality_verdict"),
                        _safe(eq.get("overall_quality_score"), float),
                        trap.get("overall_verdict"),
                        _safe(scen.get("upside_pct"), float),
                        _safe(pl.get("total_score"), float),
                        pl_json,
                        scen_json,
                        raw_fin_json,
                        ca_json,
                        vgpm_json,
                        dcf_rng_json,
                        vt_json,
                        sc_json,
                        sc_hash,
                        cq_json,
                        fin_stmts_json,
                    ),
                )

                # ── per-agent rows ────────────────────────────────────────────
                for agent_key, agent_data in analyst_signals.items():
                    if agent_key in skip_agents or not isinstance(agent_data, dict):
                        continue
                    ticker_sig = agent_data.get(ticker)
                    if not isinstance(ticker_sig, dict):
                        continue
                    conn.execute(
                        _insert_ignore_sql(
                            "agent_signals",
                            ["run_id", "ticker", "agent_key", "signal", "conviction",
                             "price_target", "time_horizon", "thesis_summary", "key_risks"],
                            "run_id, ticker, agent_key",
                        ),
                        (
                            run_id, ticker, agent_key,
                            ticker_sig.get("signal"),
                            _safe(ticker_sig.get("conviction"), int),
                            _safe(ticker_sig.get("price_target"), float),
                            ticker_sig.get("time_horizon"),
                            ticker_sig.get("thesis_summary"),
                            _safe_json(ticker_sig.get("key_risks")),
                        ),
                    )

        conn.close()
        print(f"  [archive] Run saved: {run_id}")
        return run_id

    except Exception as exc:
        # R2 failure surfacing: this used to be print-only, so a save_run
        # failure was invisible in Railway logs and the run's archive link
        # silently went missing. The print stays for CLI runs.
        logger.warning("[archive] save_run FAILED for %s: %s",
                       (state.get("data") or {}).get("tickers"), exc)
        print(f"  [archive] Warning: could not save run: {exc}")
        return ""


def update_outcomes(
    ticker: str,
    price_at_review: float,
    review_date: str,
    run_id: str | None = None,
    days_back: int = 30,
) -> int:
    """
    Fill outcome columns for ticker_signals and agent_signals rows whose
    outcome is still PENDING and whose run was >= days_back days ago.

    outcome logic (matches post_trade_review):
        final_action BUY/COVER  → CORRECT if pct_change > +5%, INCORRECT if < -5%
        final_action SELL/SHORT → CORRECT if pct_change < -5%, INCORRECT if > +5%
        else                    → NEUTRAL

    Parameters
    ----------
    ticker           : ticker symbol
    price_at_review  : current price used for scoring
    review_date      : ISO date string of the review (today)
    run_id           : if set, score only this specific run; else score all pending
    days_back        : minimum age in days before a run is eligible for review

    Returns
    -------
    Number of ticker_signals rows updated.
    """
    try:
        updated = 0

        if run_id:
            rows = _fetch(
                """
                SELECT ts.run_id, ts.ticker, ts.final_action, ts.price_at_run
                FROM ticker_signals ts
                WHERE ts.ticker = ? AND ts.run_id = ? AND ts.outcome = 'PENDING'
                """,
                [ticker, run_id],
            )
        else:
            # Runs at least days_back old are eligible. substr() date prefix
            # comparison works identically on SQLite and Postgres (run_at is
            # ISO-8601 text, so the first 10 chars are the calendar date).
            cutoff = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
            rows = _fetch(
                """
                SELECT ts.run_id, ts.ticker, ts.final_action, ts.price_at_run
                FROM ticker_signals ts
                JOIN runs r ON r.run_id = ts.run_id
                WHERE ts.ticker = ?
                  AND ts.outcome = 'PENDING'
                  AND substr(r.run_at, 1, 10) <= ?
                """,
                [ticker, cutoff],
            )

        for row in rows:
            price_then = row["price_at_run"]
            action     = row["final_action"] or "HOLD"

            if price_then and price_then > 0:
                pct_change = (price_at_review - price_then) / price_then * 100
            else:
                pct_change = 0.0

            if action in ("BUY", "COVER"):
                outcome = "CORRECT" if pct_change > 5 else ("INCORRECT" if pct_change < -5 else "NEUTRAL")
            elif action in ("SELL", "SHORT"):
                outcome = "CORRECT" if pct_change < -5 else ("INCORRECT" if pct_change > 5 else "NEUTRAL")
            else:
                outcome = "NEUTRAL"

            _exec(
                """
                UPDATE ticker_signals
                SET outcome = ?, review_date = ?, price_at_review = ?, pct_change = ?
                WHERE run_id = ? AND ticker = ?
                """,
                [outcome, review_date, price_at_review, round(pct_change, 4),
                 row["run_id"], row["ticker"]],
            )

            # Propagate outcome to agent_signals that agreed with final action
            agent_rows = _fetch(
                "SELECT id, signal FROM agent_signals WHERE run_id=? AND ticker=? AND outcome='PENDING'",
                [row["run_id"], row["ticker"]],
            )

            for ar in agent_rows:
                agent_sig = ar["signal"] or "HOLD"
                agreed = (
                    (action in ("BUY", "COVER") and agent_sig == "BUY") or
                    (action in ("SELL", "SHORT") and agent_sig in ("SELL", "SHORT"))
                )
                agent_outcome = outcome if agreed else "NEUTRAL"
                _exec(
                    "UPDATE agent_signals SET outcome=? WHERE id=?",
                    [agent_outcome, ar["id"]],
                )

            updated += 1

        return updated

    except Exception as exc:
        print(f"  [archive] Warning: update_outcomes failed: {exc}")
        return 0


def get_agent_outcomes_by_regime(
    min_reviews: int = 20,
) -> dict[str, dict[str, dict]]:
    """
    Return per-agent outcome stats stratified by macro regime (risk_appetite).

    Only agent/regime buckets with >= min_reviews scored rows are included.

    Returns
    -------
    {
      "risk-on":  {"buffett": {"correct": 8, "neutral": 2, "incorrect": 2,
                               "total": 12, "scored": 10, "hit_rate": 0.8}, ...},
      "risk-off": {...},
    }
    """
    try:
        rows = _fetch(
            """
            SELECT
                r.regime_risk_appetite   AS regime,
                ag.agent_key,
                SUM(CASE WHEN ag.outcome='CORRECT'   THEN 1 ELSE 0 END) AS correct,
                SUM(CASE WHEN ag.outcome='NEUTRAL'   THEN 1 ELSE 0 END) AS neutral,
                SUM(CASE WHEN ag.outcome='INCORRECT' THEN 1 ELSE 0 END) AS incorrect,
                COUNT(*) AS total
            FROM agent_signals ag
            JOIN runs r ON r.run_id = ag.run_id
            WHERE ag.outcome != 'PENDING'
              AND r.regime_risk_appetite IS NOT NULL
            GROUP BY r.regime_risk_appetite, ag.agent_key
            HAVING COUNT(*) >= ?
            """,
            [min_reviews],
        )

        result: dict[str, dict[str, dict]] = {}
        for row in rows:
            regime = row["regime"]
            agent  = row["agent_key"]
            scored = row["correct"] + row["incorrect"]
            hit_rate = row["correct"] / scored if scored else 0.0
            result.setdefault(regime, {})[agent] = {
                "correct":   row["correct"],
                "neutral":   row["neutral"],
                "incorrect": row["incorrect"],
                "total":     row["total"],
                "scored":    scored,
                "hit_rate":  round(hit_rate, 3),
            }
        return result
    except Exception as exc:
        print(f"  [archive] Warning: get_agent_outcomes_by_regime failed: {exc}")
        return {}


def get_agent_outcomes(min_reviews: int = 3) -> dict[str, dict]:
    """
    Return per-agent outcome stats for all agents with at least min_reviews scored rows.

    Returns
    -------
    {
      "buffett": {"correct": 12, "neutral": 4, "incorrect": 2, "total": 18, "hit_rate": 0.67},
      ...
    }
    """
    try:
        rows = _fetch(
            """
            SELECT agent_key,
                   SUM(CASE WHEN outcome='CORRECT'   THEN 1 ELSE 0 END) as correct,
                   SUM(CASE WHEN outcome='NEUTRAL'   THEN 1 ELSE 0 END) as neutral,
                   SUM(CASE WHEN outcome='INCORRECT' THEN 1 ELSE 0 END) as incorrect,
                   COUNT(*) as total
            FROM agent_signals
            WHERE outcome != 'PENDING'
            GROUP BY agent_key
            HAVING COUNT(*) >= ?
            """,
            [min_reviews],
        )
        result = {}
        for r in rows:
            total = r["total"] or 1
            hit = r["correct"] / total
            result[r["agent_key"]] = {
                "correct":   r["correct"],
                "neutral":   r["neutral"],
                "incorrect": r["incorrect"],
                "total":     r["total"],
                "hit_rate":  round(hit, 3),
            }
        return result
    except Exception as exc:
        print(f"  [archive] Warning: get_agent_outcomes failed: {exc}")
        return {}


def get_phase_cache(
    ticker: str,
    max_age_days: int = 7,
) -> dict | None:
    """
    Return cached phase data for a ticker from the most recent run within
    ``max_age_days`` that also contains data for that ticker.

    Designed to let the pipeline skip expensive LLM / web-search phases when
    fresh-enough results already exist in the archive.

    Parameters
    ----------
    ticker       : ticker symbol (e.g. "BABA")
    max_age_days : maximum age of the cached run in calendar days (default 7)

    Returns
    -------
    A dict with the following keys (any may be None if not present in the run):

        run_id         (str)   — archive run_id of the cached row
        run_at         (str)   — ISO-8601 timestamp of that run
        age_days       (float) — age in fractional days
        industry_brief (str | None) — full industry_brief_text
        deep_research  (str | None) — full deep_research_text
        dcf_range      (dict | None) — full dcf_range[ticker] dict (v3.1+)
        power_law      (dict | None) — full power_law_analysis[ticker] dict
        citation_audit (dict | None) — full citation_audit[ticker] dict
        scenario       (dict | None) — full scenario_analysis[ticker] dict
        sector_card_hash (str | None) — R5 card-QA content hash (v3.4+)
        card_qa_audit  (dict | None) — full card_qa_audit[ticker] dict (v3.4+)

    Returns None if no qualifying run is found.
    """
    try:
        cutoff = (datetime.now() - timedelta(days=max_age_days)).isoformat()

        row = _fetch_one(
            """
            SELECT
                r.run_id,
                r.run_at,
                r.industry_brief_text,
                r.deep_research_text,
                ts.power_law_json,
                ts.dcf_range_json,
                ts.citation_audit_json,
                ts.scenario_json,
                ts.value_trap_json,
                ts.sector_card_hash,
                ts.card_qa_json,
                ts.financial_statements_json
            FROM runs r
            JOIN ticker_signals ts ON r.run_id = ts.run_id AND ts.ticker = ?
            WHERE r.run_at >= ?
            ORDER BY r.run_at DESC
            LIMIT 1
            """,
            [ticker, cutoff],
        )

        if not row:
            return None

        run_at   = row["run_at"]
        age_days = (datetime.now() - datetime.fromisoformat(run_at)).total_seconds() / 86400

        def _load_json(col: str) -> dict | None:
            raw = row[col]
            if not raw:
                return None
            try:
                return json.loads(raw)
            except (TypeError, ValueError):
                return None

        return {
            "run_id":         row["run_id"],
            "run_at":         run_at,
            "age_days":       round(age_days, 2),
            "industry_brief": row["industry_brief_text"] or None,
            "deep_research":  row["deep_research_text"]  or None,
            "power_law":      _load_json("power_law_json"),
            "dcf_range":      _load_json("dcf_range_json"),
            "citation_audit": _load_json("citation_audit_json"),
            "scenario":       _load_json("scenario_json"),
            "value_trap":     _load_json("value_trap_json"),
            "sector_card_hash": row["sector_card_hash"] or None,
            "card_qa_audit":  _load_json("card_qa_json"),
            "financial_statements": _load_json("financial_statements_json"),
        }

    except Exception as exc:
        print(f"  [cache] Warning: get_phase_cache({ticker}) failed: {exc}")
        return None


def get_last_scored_signal(ticker: str) -> dict | None:
    """
    M1 lessons — most recent ticker_signals row for `ticker` whose outcome
    has been scored (CORRECT / INCORRECT / NEUTRAL, i.e. NOT PENDING), plus
    the DCF calibration flag parsed from its dcf_range_json. This is the
    gap-detection source for agent_lessons: an INCORRECT outcome or a
    calibration_error means the valuation/extractor stack missed.

    Returns None when the ticker has no scored run yet (or on any failure —
    lesson detection must never break a run).
    """
    try:
        row = _fetch_one(
            """
            SELECT ts.run_id, r.run_at, ts.ticker, ts.final_action,
                   ts.pm_rationale, ts.price_at_run, ts.price_at_review,
                   ts.pct_change, ts.outcome, ts.dcf_base_iv, ts.dcf_wacc,
                   ts.dcf_range_json
            FROM ticker_signals ts
            JOIN runs r ON r.run_id = ts.run_id
            WHERE ts.ticker = ? AND ts.outcome != 'PENDING'
            ORDER BY r.run_at DESC
            LIMIT 1
            """,
            [ticker.upper()],
        )
        if not row:
            return None
        dcf_range = None
        try:
            dcf_range = json.loads(row["dcf_range_json"]) if row["dcf_range_json"] else None
        except (TypeError, ValueError):
            dcf_range = None
        return {
            "run_id":            row["run_id"],
            "run_at":            row["run_at"],
            "ticker":            row["ticker"],
            "final_action":      row["final_action"],
            "pm_rationale":      row["pm_rationale"] or "",
            "price_at_run":      row["price_at_run"],
            "price_at_review":   row["price_at_review"],
            "pct_change":        row["pct_change"],
            "outcome":           row["outcome"],
            "dcf_base_iv":       row["dcf_base_iv"],
            "dcf_wacc":          row["dcf_wacc"],
            "calibration_error": bool((dcf_range or {}).get("calibration_error")),
        }
    except Exception as exc:
        print(f"  [archive] Warning: get_last_scored_signal({ticker}) failed: {exc}")
        return None


# ── Routing cache ─────────────────────────────────────────────────────────────

def get_routing_cache(
    ticker: str,
    max_age_days: int = 30,
) -> dict | None:
    """
    Return cached strategic-router output for *ticker* if it was saved within
    ``max_age_days`` calendar days.

    Returns a dict with keys:
        sector, sector_llm_raw, sector_confidence, sector_warning,
        company_name, routing_decision (dict), raw_financials (dict),
        reported_currency (str | None), last_updated (str ISO-8601),
        age_days (float)
    or None if no valid cache entry exists.
    """
    try:
        cutoff = (datetime.now() - timedelta(days=max_age_days)).isoformat()

        row = _fetch_one(
            """
            SELECT ticker, sector, sector_llm_raw, sector_confidence, sector_warning,
                   company_name, routing_decision_json, raw_financials_json,
                   reported_currency, last_updated
            FROM ticker_routing_cache
            WHERE ticker = ? AND last_updated >= ?
            """,
            [ticker.upper(), cutoff],
        )

        if not row:
            return None

        def _j(col: str):
            raw = row[col]
            if not raw:
                return None
            try:
                return json.loads(raw)
            except (TypeError, ValueError):
                return None

        last_updated = row["last_updated"]
        age_days = (datetime.now() - datetime.fromisoformat(last_updated)).total_seconds() / 86400

        return {
            "sector":            row["sector"],
            "sector_llm_raw":    row["sector_llm_raw"],
            "sector_confidence": row["sector_confidence"],
            "sector_warning":    row["sector_warning"],
            "company_name":      row["company_name"],
            "routing_decision":  _j("routing_decision_json"),
            "raw_financials":    _j("raw_financials_json"),
            "reported_currency": row["reported_currency"],
            "last_updated":      last_updated,
            "age_days":          round(age_days, 2),
        }

    except Exception as exc:
        print(f"  [routing-cache] Warning: get_routing_cache({ticker}) failed: {exc}")
        return None


def save_routing_cache(
    ticker: str,
    sector: str,
    sector_llm_raw: str | None,
    sector_confidence: str | None,
    sector_warning: str | None,
    company_name: str | None,
    routing_decision: dict | None,
    raw_financials: dict | None,
    reported_currency: str | None = None,
) -> None:
    """
    Upsert (INSERT OR REPLACE) a routing-cache entry for *ticker*.
    Called at the end of run_strategic_router() after a successful LLM call.
    """
    try:
        cols = ["ticker", "sector", "sector_llm_raw", "sector_confidence",
                "sector_warning", "company_name", "routing_decision_json",
                "raw_financials_json", "reported_currency", "last_updated"]
        phs = ", ".join("?" * len(cols))
        if db.is_postgres():
            updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c != "ticker")
            sql = (f"INSERT INTO ticker_routing_cache ({', '.join(cols)}) VALUES ({phs}) "
                   f"ON CONFLICT (ticker) DO UPDATE SET {updates}")
        else:
            sql = f"INSERT OR REPLACE INTO ticker_routing_cache ({', '.join(cols)}) VALUES ({phs})"
        _exec(
            sql,
            [
                ticker.upper(),
                sector,
                sector_llm_raw,
                sector_confidence,
                sector_warning,
                company_name,
                _safe_json(routing_decision),
                _safe_json(raw_financials),
                reported_currency,
                datetime.now().isoformat(),
            ],
        )
    except Exception as exc:
        print(f"  [routing-cache] Warning: save_routing_cache({ticker}) failed: {exc}")


def load_runs(
    regime: str | None = None,
    sector: str | None = None,
    ticker: str | None = None,
    research_tier: str | None = None,
    limit: int = 100,
    include_text: bool = False,
) -> list[dict]:
    """
    Query the run archive with optional filters.

    Parameters
    ----------
    regime         : filter by regime_risk_appetite (e.g. "risk-off")
    sector         : filter by sector (e.g. "Tech")
    ticker         : filter to runs that include this ticker
    research_tier  : filter by research tier used (e.g. "anthropic_web")
    limit          : max rows returned from `runs` table
    include_text   : if False (default), omit industry_brief_text and deep_research_text
                     (keeps results lean for programmatic use)

    Returns
    -------
    List of run dicts including nested ticker_signals and agent_signals.
    """
    try:
        clauses: list[str] = []
        params: list = []

        if regime:
            clauses.append("regime_risk_appetite = ?")
            params.append(regime)
        if sector:
            clauses.append("sector = ?")
            params.append(sector)
        if ticker:
            clauses.append("tickers LIKE ?")
            params.append(f'%"{ticker}"%')
        if research_tier:
            clauses.append("research_tier = ?")
            params.append(research_tier)

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)

        run_rows = _fetch(
            f"SELECT * FROM runs {where} ORDER BY run_at DESC LIMIT ?", params
        )

        result = []
        for rr in run_rows:
            ts_rows = _fetch(
                "SELECT * FROM ticker_signals WHERE run_id=?", [rr["run_id"]]
            )
            ag_rows = _fetch(
                "SELECT * FROM agent_signals WHERE run_id=?", [rr["run_id"]]
            )

            run_dict = dict(rr)
            run_dict["tickers"] = json.loads(run_dict.get("tickers") or "[]")

            # Optionally omit large text blobs to keep results lean
            if not include_text:
                run_dict.pop("industry_brief_text", None)
                run_dict.pop("deep_research_text",  None)

            # Parse agent key_risks from JSON string back to list
            ag_list = []
            for ag in ag_rows:
                ag_dict = dict(ag)
                try:
                    ag_dict["key_risks"] = json.loads(ag_dict.get("key_risks") or "[]")
                except (json.JSONDecodeError, TypeError):
                    ag_dict["key_risks"] = []
                ag_list.append(ag_dict)

            run_dict["ticker_signals"] = [dict(r) for r in ts_rows]
            run_dict["agent_signals"]  = ag_list
            result.append(run_dict)

        return result

    except Exception as exc:
        print(f"  [archive] Warning: load_runs failed: {exc}")
        return []


def _parse_sections_inline(text: str) -> dict[str, str]:
    """
    Self-contained Section 2 parser — mirrors deep_research._extract_sections().

    Kept here to avoid a circular import (run_archive → deep_research).
    Returns {"2a": ..., "2b": ..., ..., "2f": ..., "brief": ...} or
    {"full": text} if no section headers are found.

    MUST stay in lock-step with deep_research._extract_sections(): the C2
    extractor cache keys on a hash of these sections, and a fresh run hashes
    _extract_sections()' output while a cache-hit run hashes THIS re-parse of
    the archived text. Any drift (boundary regex, SECTION 7 handling) makes
    the hashes disagree and silently disables extractor reuse. Sync points:
      * boundary regex (2026-04 broadening: non-word prefixes, Section/Part prose)
      * SECTION 7 brief extraction + spanning-section trim (R1, 2026-08)
    """
    import re
    # M2 A3: strip the freshness addendum (appended to reused research text)
    # so re-parsed sections match the original extraction — C2 hash stability.
    text = text.split(LATEST_DEV_ADDENDUM_MARKER)[0]
    # Widened to tolerate LLM formatting variants — kept in lock-step with
    # deep_research._extract_sections(). See that function's docstring.
    boundary = re.compile(
        r"(?:^|\n)[^\w\n]*\*{0,2}(?:section\s+|part\s+)?\b(2[A-F])\b[\.\:—\-\)\*\s]",
        re.IGNORECASE | re.MULTILINE,
    )
    positions: list[tuple[str, int]] = []
    for m in boundary.finditer(text):
        key = m.group(1).lower()
        if key not in [k for k, _ in positions]:
            positions.append((key, m.start()))
    positions.sort(key=lambda x: x[1])
    if not positions:
        return {"full": text}
    sections: dict[str, str] = {}
    for i, (key, start) in enumerate(positions):
        end = positions[i + 1][1] if i + 1 < len(positions) else len(text)
        sections[key] = text[start:end].strip()

    # R1: SECTION 7 — Industry Intelligence Brief (same regex/trim as
    # _extract_sections): surface under "brief" and trim the spanning section.
    _brief_m = re.search(
        r"(?:^|\n)[^\w\n]*(?:SECTION\s*7[^\n]*?)?INDUSTRY\s+INTELLIGENCE\s+BRIEF",
        text,
        re.IGNORECASE,
    )
    if _brief_m:
        _bstart = _brief_m.start()
        sections["brief"] = text[_bstart:].strip()
        _spanning = [k for k, s in positions if s <= _bstart]
        if _spanning:
            _last_key = _spanning[-1]
            _last_start = dict(positions)[_last_key]
            sections[_last_key] = text[_last_start:_bstart].strip()
    return sections


def get_recent_research(
    ticker: str,
    max_age_days: "int | None" = 7,
    qualifying_tiers: tuple[str, ...] = (
        "anthropic_web", "tavily", "qwen_web",
        # M2 A2: archived/delta-derived research is first-class reuse seed.
        # Before this, runs saved as cache or delta tiers could never seed
        # reuse — staleness chained forward forever (08-19 BABA incident:
        # full research pass despite a run from the day before).
        "anthropic_web_cached", "archive_news_delta",
    ),
) -> dict | None:
    """
    Return the most recent qualifying deep-research run for `ticker`, or None.

    A run qualifies when ALL of:
      - `ticker` appears in runs.tickers JSON array (as the PRIMARY ticker)
      - runs.research_tier is in qualifying_tiers  (excludes knowledge_only / none)
      - runs.deep_research_text is non-empty
      - the CONTENT date is within the last max_age_days days — keyed on
        research_as_of (M2 A2), falling back to run_at when NULL. Reuse is
        decided by how old the research content is, not when the row was
        written: a delta-refreshed run stays reusable for its full window.
        max_age_days=None lifts the age filter entirely (fast-path reuse:
        memory + one freshness search patches recency instead).

    Returns a dict with keys:
        run_id               str
        run_at               str   ISO-8601 timestamp
        analysis_date        str   end_date used in that run
        age_days             float decimal days of CONTENT age at call time
        research_as_of       str|None content date (None → age keyed on run_at)
        research_tier        str
        deep_research_text   str   full Section 2 report
        deep_research_sections dict[str, str]  pre-parsed via _parse_sections_inline()

    Returns None if no qualifying run is found or if the DB is unavailable.
    """
    placeholders = ",".join("?" * len(qualifying_tiers))
    # Match runs where this ticker was the PRIMARY ticker (deep_research_text
    # stores the primary ticker's research only).  Using tickers LIKE would
    # match multi-ticker runs where this ticker was secondary — returning the
    # primary ticker's research text for the wrong company (NEE→ZIM bleed bug).
    # The tickers JSON array's FIRST element is always the primary ticker.
    # COALESCE(research_as_of, run_at): rows written before M2 have NULL
    # research_as_of and fall back to their write time.
    #
    # Tie-break on run_at DESC: same-day re-runs legitimately share an
    # as_of date ('2026-08-24' is date-precision), and a bare ORDER BY on
    # the COALESCE key alone makes the pick arbitrary among ties — Postgres
    # then returned a STALE week-old research row for BABA (2026-08-25
    # incident: replayed the pre-earnings Aug-19 text instead of the
    # Aug-24 post-earnings text → IV and decision flipped).  Among equal
    # content dates, the most recently WRITTEN row carries the most
    # delta/freshness-patched content, so it wins.
    _age_clause = ""
    _age_params: list = []
    if max_age_days is not None:
        cutoff = (datetime.now() - timedelta(days=max_age_days)).isoformat()
        _age_clause = "AND    COALESCE(research_as_of, run_at) >= ?"
        _age_params = [cutoff]
    sql = f"""
        SELECT run_id, run_at, analysis_date, research_tier,
               research_as_of, deep_research_text
        FROM   runs
        WHERE  tickers LIKE ?
        AND    research_tier IN ({placeholders})
        AND    deep_research_text IS NOT NULL
        AND    deep_research_text != ''
        {_age_clause}
        ORDER  BY COALESCE(research_as_of, run_at) DESC, run_at DESC
        LIMIT  1
    """
    # Match only when ticker is the FIRST element in the JSON array:
    # '["ZIM"' at position 0 ensures ZIM was the primary ticker.
    params: list = [f'["{ticker.upper()}"%', *qualifying_tiers, *_age_params]
    try:
        row = _fetch_one(sql, params)
    except Exception:
        return None

    if not row:
        return None

    # Content age: how old is the research itself (M2 A2), not the row.
    as_of_raw = row["research_as_of"] if "research_as_of" in row.keys() else None
    content_dt = None
    if as_of_raw:
        try:
            content_dt = datetime.fromisoformat(str(as_of_raw))
        except (ValueError, TypeError):
            content_dt = None
    if content_dt is None:
        as_of_raw = None
        try:
            content_dt = datetime.fromisoformat(row["run_at"])
        except (ValueError, TypeError):
            return {
                "run_id":                  row["run_id"],
                "run_at":                  row["run_at"],
                "analysis_date":           row["analysis_date"],
                "age_days":                0.0,
                "research_as_of":          None,
                "research_tier":           row["research_tier"],
                "deep_research_text":      row["deep_research_text"],
                "deep_research_sections":  _parse_sections_inline(row["deep_research_text"]),
            }
    age_days = (datetime.now() - content_dt).total_seconds() / 86_400

    text = row["deep_research_text"]
    return {
        "run_id":                  row["run_id"],
        "run_at":                  row["run_at"],
        "analysis_date":           row["analysis_date"],
        "age_days":                round(age_days, 2),
        "research_as_of":          str(as_of_raw) if as_of_raw else None,
        "research_tier":           row["research_tier"],
        "deep_research_text":      text,
        "deep_research_sections":  _parse_sections_inline(text),
    }


# ── C2: persisted extractor fan-out outputs ──────────────────────────────────

def save_extractor_outputs(
    ticker: str,
    sections_hash: str,
    results: dict,
    failures: list | None = None,
) -> bool:
    """Persist sector-extractor fan-out outputs keyed by research content.

    Best-effort by contract: a persistence failure returns False and must
    never sink the run (the caller already has the outputs in memory).
    Upserts — a later run with identical sections may hold a more complete
    extraction (e.g. an earlier dcf_calibration attempt timed out).
    """
    payload = _safe_json({"results": results or {}, "failures": failures or []})
    if not ticker or not sections_hash or payload is None:
        return False
    try:
        if db.is_postgres():
            sql = (
                "INSERT INTO extractor_outputs "
                "(ticker, sections_hash, outputs_json, extracted_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT (ticker, sections_hash) DO UPDATE SET "
                "outputs_json = EXCLUDED.outputs_json, "
                "extracted_at = EXCLUDED.extracted_at"
            )
        else:
            sql = (
                "INSERT OR REPLACE INTO extractor_outputs "
                "(ticker, sections_hash, outputs_json, extracted_at) "
                "VALUES (?, ?, ?, ?)"
            )
        _exec(sql, [ticker.upper(), sections_hash, payload,
                    datetime.now().isoformat()])
        return True
    except Exception:
        return False


def get_extractor_outputs(ticker: str, sections_hash: str) -> dict | None:
    """Return persisted fan-out outputs for this ticker + sections content.

    Returns {"results": dict, "failures": list} or None when absent,
    unreadable, or corrupt — the caller falls back to live extraction (the
    pre-C2 path) in all of those cases, so a bad blob can never break a run.
    """
    if not ticker or not sections_hash:
        return None
    try:
        row = _fetch_one(
            "SELECT outputs_json FROM extractor_outputs "
            "WHERE ticker = ? AND sections_hash = ?",
            [ticker.upper(), sections_hash],
        )
    except Exception:
        return None
    if not row:
        return None
    try:
        parsed = json.loads(row["outputs_json"])
    except (TypeError, ValueError):
        return None
    if not isinstance(parsed, dict) or not isinstance(parsed.get("results"), dict):
        return None
    failures = parsed.get("failures")
    parsed["failures"] = failures if isinstance(failures, list) else []
    return parsed


# ── R2: persisted citation registry (keyed by research full-text hash) ───────

def save_citation_registry(
    ticker: str,
    text_hash: str,
    registry: list,
) -> bool:
    """Persist the LLM-extracted citation registry keyed by report text.

    Best-effort by contract (same as save_extractor_outputs): a persistence
    failure returns False and must never sink the run. Upserts — a later
    extraction over identical text may be more complete.
    """
    payload = _safe_json(registry if isinstance(registry, list) else [])
    if not ticker or not text_hash or payload is None:
        return False
    try:
        if db.is_postgres():
            sql = (
                "INSERT INTO citation_registry "
                "(ticker, text_hash, registry_json, extracted_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT (ticker, text_hash) DO UPDATE SET "
                "registry_json = EXCLUDED.registry_json, "
                "extracted_at = EXCLUDED.extracted_at"
            )
        else:
            sql = (
                "INSERT OR REPLACE INTO citation_registry "
                "(ticker, text_hash, registry_json, extracted_at) "
                "VALUES (?, ?, ?, ?)"
            )
        _exec(sql, [ticker.upper(), text_hash, payload,
                    datetime.now().isoformat()])
        return True
    except Exception:
        return False


def get_citation_registry(ticker: str, text_hash: str) -> list | None:
    """Return the persisted citation registry for this ticker + text hash.

    Returns the list of registry entries, or None when absent/unreadable —
    the caller rebuilds via LLM in that case (the pre-R2 path), so a bad
    blob can never break a run.
    """
    if not ticker or not text_hash:
        return None
    try:
        row = _fetch_one(
            "SELECT registry_json FROM citation_registry "
            "WHERE ticker = ? AND text_hash = ?",
            [ticker.upper(), text_hash],
        )
    except Exception:
        return None
    if not row:
        return None
    try:
        parsed = json.loads(row["registry_json"])
    except (TypeError, ValueError):
        return None
    if not isinstance(parsed, list):
        return None
    return parsed


def archive_summary() -> dict:
    """Return high-level stats about the archive (for display)."""
    try:
        def _count(sql: str) -> int:
            row = _fetch_one(sql)
            return row["n"] if row else 0

        total_runs    = _count("SELECT COUNT(*) AS n FROM runs")
        total_tickers = _count("SELECT COUNT(*) AS n FROM ticker_signals")
        pending       = _count("SELECT COUNT(*) AS n FROM ticker_signals WHERE outcome='PENDING'")
        scored = total_tickers - pending

        # Tier breakdown
        tier_rows = _fetch(
            "SELECT research_tier, COUNT(*) as cnt FROM runs GROUP BY research_tier"
        )
        tiers = {r["research_tier"] or "unknown": r["cnt"] for r in tier_rows}

        return {
            "total_runs":            total_runs,
            "total_ticker_signals":  total_tickers,
            "scored":                scored,
            "pending":               pending,
            "research_tiers":        tiers,
            "db_path":               "postgres" if db.is_postgres() else DB_PATH,
        }
    except Exception:
        return {
            "total_runs": 0,
            "total_ticker_signals": 0,
            "scored": 0,
            "pending": 0,
            "research_tiers": {},
            "db_path": "postgres" if db.is_postgres() else DB_PATH,
        }


def backtest_query(
    ticker: str | None = None,
    regime: str | None = None,
    sector: str | None = None,
    agent: str | None = None,
    min_conviction: int | None = None,
) -> list[dict]:
    """
    Purpose-built backtesting query.  Returns a flat list of scored decisions
    (outcome != PENDING) with all columns needed for regime-tagged analysis.

    Example use:
        rows = backtest_query(ticker="NVDA", regime="risk-on")
        for r in rows:
            print(r["run_at"], r["final_action"], r["pct_change"], r["outcome"])
            print("  DCF IV vs price:", r["dcf_iv_vs_price_pct"])
            print("  Research tier:  ", r["research_tier"])
    """
    try:
        clauses: list[str] = ["ts.outcome != 'PENDING'"]
        params: list = []

        if ticker:
            clauses.append("ts.ticker = ?")
            params.append(ticker)
        if regime:
            clauses.append("r.regime_risk_appetite = ?")
            params.append(regime)
        if sector:
            clauses.append("r.sector = ?")
            params.append(sector)

        where = "WHERE " + " AND ".join(clauses)

        rows = _fetch(
            f"""
            SELECT
                r.run_id, r.run_at, r.analysis_date, r.sector,
                r.regime_risk_appetite, r.regime_volatility, r.regime_rate_direction,
                r.regime_dollar, r.regime_recession_risk,
                r.research_tier,
                ts.ticker, ts.final_action, ts.position_size_pct,
                ts.price_at_run, ts.price_target, ts.stop_loss,
                ts.entry_range_low, ts.entry_range_high, ts.time_horizon, ts.pm_rationale,
                ts.dcf_base_iv, ts.dcf_wacc, ts.dcf_iv_vs_price_pct,
                ts.debate_triggered, ts.debate_adjudicated_signal,
                ts.si_signal, ts.si_short_float_pct, ts.si_squeeze_risk,
                ts.insider_signal, ts.revision_direction, ts.news_signal,
                ts.eq_quality_verdict, ts.eq_quality_score,
                ts.value_trap_verdict, ts.ev_upside_pct, ts.power_law_score,
                ts.review_date, ts.price_at_review, ts.pct_change, ts.outcome
            FROM ticker_signals ts
            JOIN runs r ON r.run_id = ts.run_id
            {where}
            ORDER BY r.run_at DESC
            """,
            params,
        )

        result = [dict(r) for r in rows]

        # ── Attach per-agent signals to each row ──────────────────────────────────
        # Build a lightweight index: (run_id, ticker) → [agent_signal dicts]
        all_run_ticker_pairs = [(r["run_id"], r["ticker"]) for r in result]
        if all_run_ticker_pairs:
            placeholders = ",".join("(?,?)" for _ in all_run_ticker_pairs)
            flat = [val for pair in all_run_ticker_pairs for val in pair]
            ag_all = _fetch(
                f"""
                SELECT run_id, ticker, agent_key, signal, conviction,
                       price_target, time_horizon, thesis_summary, outcome
                FROM agent_signals
                WHERE (run_id, ticker) IN ({placeholders})
                """,
                flat,
            )
            ag_by_key: dict = {}
            for ag in ag_all:
                k = (ag["run_id"], ag["ticker"])
                ag_by_key.setdefault(k, []).append(dict(ag))
            for row in result:
                row["agent_signals"] = ag_by_key.get((row["run_id"], row["ticker"]), [])

        # If agent filter requested, further filter by agent agreement
        if agent or min_conviction:
            agent_clauses = ["ag.outcome != 'PENDING'"]
            agent_params: list = []
            if agent:
                agent_clauses.append("ag.agent_key = ?")
                agent_params.append(agent)
            if min_conviction:
                agent_clauses.append("ag.conviction >= ?")
                agent_params.append(min_conviction)

            ag_rows = _fetch(
                f"""
                SELECT ag.run_id, ag.ticker, ag.agent_key, ag.signal,
                       ag.conviction, ag.price_target, ag.time_horizon,
                       ag.thesis_summary, ag.key_risks, ag.outcome
                FROM agent_signals ag
                WHERE {' AND '.join(agent_clauses)}
                """,
                agent_params,
            )

            # Index by (run_id, ticker)
            ag_index: dict = {}
            for ar in ag_rows:
                key = (ar["run_id"], ar["ticker"])
                ag_index.setdefault(key, []).append(dict(ar))

            # Filter result to only rows that have a matching agent signal
            # (This is intentionally a loose join — keep all ticker rows that
            # have at least one matching agent signal)
            run_rows_lookup = {(r.get("run_id"), r["ticker"]) for r in result if "run_id" in r}
            result = [r for r in result if ag_index.get((r.get("run_id"), r["ticker"]))]

        return result

    except Exception as exc:
        print(f"  [archive] Warning: backtest_query failed: {exc}")
        return []


def get_watchlist() -> list[str]:
    """
    Return all distinct tickers from the run archive (ticker_signals table).
    Used by the Macro Rotation Engine to build the portfolio watchlist.
    """
    try:
        rows = _fetch(
            "SELECT DISTINCT ticker FROM ticker_signals ORDER BY ticker"
        )
        return [r["ticker"] for r in rows]
    except Exception as exc:
        print(f"  [archive] Warning: get_watchlist failed: {exc}")
        return []


def get_latest_ticker_signals(tickers: list[str] | None = None) -> list[dict]:
    """
    Return the most-recent ticker_signal + agent votes for each ticker.

    Parameters
    ----------
    tickers : list of ticker symbols to look up. If None, uses all archive tickers.

    Returns
    -------
    List of dicts with keys:
      ticker, final_action, position_size_pct, run_id, run_at,
      agent_votes: [{"agent_key": str, "signal": str}, ...]
    """
    try:
        if tickers is None:
            rows_t  = _fetch("SELECT DISTINCT ticker FROM ticker_signals")
            tickers = [r["ticker"] for r in rows_t]

        if not tickers:
            return []

        result = []
        for ticker in tickers:
            sig = _fetch_one(
                """
                SELECT ts.ticker, ts.final_action, ts.position_size_pct,
                       ts.run_id, r.run_at
                FROM ticker_signals ts
                JOIN runs r ON ts.run_id = r.run_id
                WHERE ts.ticker = ?
                ORDER BY r.run_at DESC
                LIMIT 1
                """,
                [ticker],
            )

            if not sig:
                continue

            sig_dict = dict(sig)

            ag_rows = _fetch(
                "SELECT agent_key, signal FROM agent_signals WHERE run_id=? AND ticker=?",
                [sig["run_id"], ticker],
            )
            sig_dict["agent_votes"] = [
                {"agent_key": r["agent_key"], "signal": r["signal"]} for r in ag_rows
            ]
            result.append(sig_dict)

        return result

    except Exception as exc:
        print(f"  [archive] Warning: get_latest_ticker_signals failed: {exc}")
        return []


def save_rotation_event(rotation_result: dict, alert_sent: bool = False) -> str:
    """
    Persist a macro rotation event to the rotation_events table.

    Parameters
    ----------
    rotation_result : dict returned by run_rotation_engine()
    alert_sent      : whether a push alert was dispatched

    Returns
    -------
    event_id : str — UUID of the new row, or "" on failure
    """
    import uuid as _uuid
    event_id = str(_uuid.uuid4())
    event_at = datetime.now().isoformat()
    try:
        _exec(
            """
            INSERT INTO rotation_events
                (event_id, event_at, old_regime, new_regime,
                 shift_score, shift_label, recommendations, sector_signal, alert_sent)
            VALUES (?,?,?,?,?,?,?,?,?)
            """,
            [
                event_id,
                event_at,
                json.dumps(rotation_result.get("old_regime", {})),
                json.dumps(rotation_result.get("new_regime", {})),
                rotation_result.get("shift_score", 0),
                rotation_result.get("shift_label", "NONE"),
                json.dumps(rotation_result.get("recommendations", [])),
                json.dumps(rotation_result.get("sector_signal", {})),
                int(alert_sent),
            ],
        )
        print(f"  [archive] Rotation event saved: {event_id}")
        return event_id
    except Exception as exc:
        print(f"  [archive] Warning: save_rotation_event failed: {exc}")
        return ""


def agent_backtest_query(
    ticker: str | None = None,
    regime: str | None = None,
    sector: str | None = None,
    agent: str | None = None,
    min_conviction: int | None = None,
) -> list[dict]:
    """
    Per-agent backtest query. Returns one row per agent-signal with regime tags.

    Joins agent_signals → runs + ticker_signals so every row carries:
      - the agent's signal, conviction, outcome
      - the macro regime at run time
      - the portfolio-level outcome and pct_change for cross-reference

    Only rows where agent_signals.outcome != 'PENDING' are returned.

    Example use:
        rows = agent_backtest_query(agent="buffett", regime="risk-off")
        hit_rate = sum(1 for r in rows if r["outcome"]=="CORRECT") / len(rows)
    """
    try:
        clauses: list[str] = ["ag.outcome != 'PENDING'"]
        params: list = []

        if ticker:
            clauses.append("ag.ticker = ?")
            params.append(ticker)
        if regime:
            clauses.append("r.regime_risk_appetite = ?")
            params.append(regime)
        if sector:
            clauses.append("r.sector = ?")
            params.append(sector)
        if agent:
            clauses.append("ag.agent_key = ?")
            params.append(agent)
        if min_conviction is not None:
            clauses.append("ag.conviction >= ?")
            params.append(min_conviction)

        where = "WHERE " + " AND ".join(clauses)

        rows = _fetch(
            f"""
            SELECT
                ag.run_id, ag.ticker, ag.agent_key,
                ag.signal       AS agent_signal,
                ag.conviction,
                ag.price_target AS agent_price_target,
                ag.time_horizon AS agent_time_horizon,
                ag.thesis_summary,
                ag.outcome,
                r.run_at, r.analysis_date, r.sector,
                r.regime_risk_appetite, r.regime_volatility,
                r.regime_rate_direction, r.regime_dollar,
                r.regime_recession_risk, r.research_tier,
                ts.price_at_run, ts.pct_change,
                ts.final_action AS pm_action,
                ts.power_law_score, ts.value_trap_verdict,
                ts.dcf_iv_vs_price_pct, ts.ev_upside_pct
            FROM agent_signals ag
            JOIN runs r ON r.run_id = ag.run_id
            LEFT JOIN ticker_signals ts
                   ON ts.run_id = ag.run_id AND ts.ticker = ag.ticker
            {where}
            ORDER BY r.run_at DESC
            """,
            params,
        )

        return [dict(r) for r in rows]

    except Exception as exc:
        print(f"  [archive] Warning: agent_backtest_query failed: {exc}")
        return []
