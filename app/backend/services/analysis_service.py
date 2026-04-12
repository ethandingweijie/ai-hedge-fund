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
    """Open run_archive.db with WAL mode, NORMAL sync, and a 5-second busy timeout."""
    conn = sqlite3.connect(path or _get_db_path(), **kwargs)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


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
    is_checkpoint    INTEGER DEFAULT 0
)
"""

_WEB_RUNS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_web_runs_ticker_time ON web_runs(ticker, run_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_web_runs_archive_id ON web_runs(archive_run_id)",
    "CREATE INDEX IF NOT EXISTS idx_web_runs_run_at ON web_runs(run_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_web_runs_action ON web_runs(final_action)",
    "CREATE INDEX IF NOT EXISTS idx_web_runs_sector ON web_runs(sector)",
    "CREATE INDEX IF NOT EXISTS idx_web_runs_user_id ON web_runs(user_id)",
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
        ("is_checkpoint", "ALTER TABLE web_runs ADD COLUMN is_checkpoint  INTEGER DEFAULT 0"),
        ("user_id",       "ALTER TABLE web_runs ADD COLUMN user_id        INTEGER"),
    ]
    for col, sql in migrations:
        if col not in existing:
            conn.execute(sql)


def _ensure_web_runs_table():
    db_path = _get_db_path()
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = _connect(db_path)
    try:
        conn.execute(_WEB_RUNS_DDL)
        _migrate_web_runs_columns(conn)
        for idx_sql in _WEB_RUNS_INDEXES:
            conn.execute(idx_sql)
        for idx_sql in _ARCHIVE_INDEXES:
            try:
                conn.execute(idx_sql)
            except Exception:
                pass  # archive tables may not exist in this DB
        conn.commit()
    finally:
        conn.close()


def _extract_web_run_summary(result: dict, ticker: str) -> tuple[str, str, str]:
    """Extract (final_action, regime, sector) from a completed pipeline result dict."""
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

        return final_action, regime, sector
    except Exception:
        return "", "", ""


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
    db_path = _get_db_path()
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
            "macro_regime":                data.get("macro_regime"),
            "raw_financials":              data.get("raw_financials"),
            "routing_decision":            data.get("routing_decision"),
            "research_tier":               data.get("research_tier"),
            # ── checkpoint: deep_research ────────────────────────────────
            "deep_research":               data.get("deep_research"),
            "deep_research_annotated":     data.get("deep_research_annotated"),
            "citation_registry":           data.get("citation_registry", []),
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
        },
    }
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO web_runs "
            "(run_id, run_at, ticker, model_name, archive_run_id, full_result_json, is_checkpoint, user_id) "
            "VALUES (?,?,?,?,?,?,1,?)",
            (
                run_id,
                datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f"),
                ticker,
                model_name,
                None,
                json.dumps(_sanitize_floats(partial_result), default=str),
                user_id,
            ),
        )
        conn.commit()
        print(f"  [checkpoint] '{checkpoint_name}' saved to web_runs ({run_id[:8]})")
    except Exception as e:
        print(f"  [checkpoint] DB write failed ({checkpoint_name}): {e}")
    finally:
        conn.close()


def _save_web_run(
    run_id: str,
    ticker: str,
    model_name: str,
    result: dict,
    archive_run_id: Optional[str] = None,
    user_id: Optional[int] = None,
):
    _ensure_web_runs_table()
    db_path = _get_db_path()
    final_action, regime, sector = _extract_web_run_summary(result, ticker)
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO web_runs "
            "(run_id, run_at, ticker, model_name, archive_run_id, full_result_json, "
            " final_action, regime, sector, is_checkpoint, user_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,0,?)",
            (
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
                user_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()

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
    db_path = _get_db_path()
    conn = _connect(db_path, check_same_thread=False)
    try:
        conn.row_factory = sqlite3.Row
        cutoff = (datetime.now() - timedelta(minutes=within_minutes)).strftime(
            "%Y-%m-%dT%H:%M:%S.%f"
        )
        row = conn.execute(
            """
            SELECT run_id, run_at, ticker, full_result_json
            FROM   web_runs
            WHERE  ticker  = ?
              AND  run_at >= ?
              AND  full_result_json IS NOT NULL
              AND  json_extract(full_result_json, '$.checkpoint') IS NULL
            ORDER  BY run_at DESC
            LIMIT  1
            """,
            (ticker.upper(), cutoff),
        ).fetchone()
        if row is None:
            return None

        result = {
            "run_id":           row["run_id"],
            "run_at":           row["run_at"],
            "ticker":           row["ticker"],
            "full_result_json": json.loads(row["full_result_json"]),
        }

        # ── Agent-set validation ───────────────────────────────────────────
        # If specific agents were requested, verify the cached run used the
        # same set. System agents (risk, portfolio) are always present and
        # excluded from the comparison.
        if agents:
            _SYSTEM = {"risk_management_agent", "advanced_risk_manager", "portfolio_manager_agent"}
            cached_data    = result["full_result_json"].get("data", {})
            cached_signals = cached_data.get("analyst_signals", {})
            # Keys in analyst_signals are like "graham_agent", "burry_agent"
            cached_investor_agents = sorted(
                k for k in cached_signals if k not in _SYSTEM
            )
            # Normalise requested list: "graham" → "graham_agent"
            requested_normalised = sorted(
                a if a.endswith("_agent") else f"{a}_agent"
                for a in agents
            )
            if cached_investor_agents != requested_normalised:
                return None   # different agent selection — force fresh run

        return result
    finally:
        conn.close()


# ── Delete helper ─────────────────────────────────────────────────────────────

def delete_run(run_id: str) -> bool:
    """
    Permanently delete a run from the archive.
    Removes from web_runs first; if the row carries an archive_run_id,
    cascades to the CLI archive tables (runs, ticker_signals, agent_signals).
    Falls back to deleting directly from the CLI tables for CLI-only runs.
    Returns True if anything was deleted, False if run_id not found.
    """
    db_path = _get_db_path()
    conn = _connect(db_path, check_same_thread=False)
    try:
        conn.row_factory = sqlite3.Row

        # ── 1. Check web_runs ────────────────────────────────────────────────
        row = conn.execute(
            "SELECT archive_run_id FROM web_runs WHERE run_id = ?", (run_id,)
        ).fetchone()

        if row is not None:
            archive_id = row["archive_run_id"]
            conn.execute("DELETE FROM web_runs WHERE run_id = ?", (run_id,))
            # Cascade to CLI archive if this web run was also saved there
            if archive_id:
                conn.execute("DELETE FROM agent_signals  WHERE run_id = ?", (archive_id,))
                conn.execute("DELETE FROM ticker_signals WHERE run_id = ?", (archive_id,))
                conn.execute("DELETE FROM runs           WHERE run_id = ?", (archive_id,))
            conn.commit()
            return True

        # ── 2. Fallback: CLI-only run (no web_runs entry) ────────────────────
        affected = conn.execute(
            "DELETE FROM runs WHERE run_id = ?", (run_id,)
        ).rowcount
        if affected:
            conn.execute("DELETE FROM agent_signals  WHERE run_id = ?", (run_id,))
            conn.execute("DELETE FROM ticker_signals WHERE run_id = ?", (run_id,))
            conn.commit()
            return True

        return False
    finally:
        conn.close()


# ── Public read helpers ───────────────────────────────────────────────────────

def get_run_result(run_id: str) -> Optional[dict]:
    """
    Return full result dict for a run.
    Checks web_runs first (full JSON stored); falls back to reconstructing
    from the CLI archive tables (runs + ticker_signals + agent_signals).
    """
    _ensure_web_runs_table()
    db_path = _get_db_path()
    conn = _connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        # ── 1. Try web_runs (full JSON) ───────────────────────────────────────
        row = conn.execute(
            "SELECT full_result_json FROM web_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row and row[0]:
            return _sanitize_floats(json.loads(row[0]))

        # ── 2. Try reconstructing from CLI archive tables ─────────────────────
        run_row = conn.execute(
            "SELECT * FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if not run_row:
            return None

        tickers: list[str] = json.loads(run_row["tickers"] or "[]")
        ticker = tickers[0] if tickers else ""

        # Ticker signals
        ts = conn.execute(
            "SELECT * FROM ticker_signals WHERE run_id = ? AND ticker = ?",
            (run_id, ticker),
        ).fetchone()

        # Agent signals
        agent_rows = conn.execute(
            "SELECT * FROM agent_signals WHERE run_id = ? AND ticker = ?",
            (run_id, ticker),
        ).fetchall()

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
            },
            "decisions": decisions,
            "vgpm":      vgpm,
        }
        return result

    finally:
        conn.close()


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
    db_path = _get_db_path()
    conn = _connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        offset = (page - 1) * page_size

        # ── 1. Web runs — filter without loading full_result_json ─────────────
        web_where: list[str] = [
            # Exclude checkpoint rows: prefer the fast column; fall back to json_extract
            # for legacy rows where is_checkpoint is NULL.
            "(w.is_checkpoint = 0 OR (w.is_checkpoint IS NULL AND "
            "json_extract(w.full_result_json, '$.checkpoint') IS NULL))"
        ]
        web_params: list[Any] = []

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
            # Use summary column when populated; fall back to json_extract for legacy rows
            web_where.append(
                "(UPPER(w.final_action) = UPPER(?) OR "
                "(w.final_action IS NULL AND "
                "UPPER(json_extract(w.full_result_json, '$.decisions')) LIKE UPPER(?)))"
            )
            web_params.extend([action, f"%{action}%"])
        if regime:
            web_where.append(
                "(UPPER(w.regime) = UPPER(?) OR "
                "(w.regime IS NULL AND "
                "UPPER(json_extract(w.full_result_json, '$.data.macro_regime.risk_appetite')) = UPPER(?)))"
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
                "UPPER(json_extract(w.full_result_json, '$.data.sector')) = UPPER(?)))"
            )
            web_params.extend([sector, sector])

        web_where_sql = "WHERE " + " AND ".join(web_where)

        # COUNT without JSON load — instant with idx_web_runs_run_at
        web_total: int = conn.execute(
            f"SELECT COUNT(*) FROM web_runs w {web_where_sql}", web_params
        ).fetchone()[0]

        # Fetch metadata only (no full_result_json) for all matching rows → used for total
        web_meta_rows = conn.execute(
            f"SELECT w.run_id, w.run_at, w.ticker, w.model_name, "
            f"w.final_action, w.regime, w.sector "
            f"FROM web_runs w {web_where_sql} ORDER BY w.run_at DESC",
            web_params,
        ).fetchall()

        # ── 2. CLI archive runs (exclude any already imported via web) ────────
        imported_archive_ids = set(
            r[0]
            for r in conn.execute(
                "SELECT archive_run_id FROM web_runs WHERE archive_run_id IS NOT NULL"
            ).fetchall()
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

        cli_rows = conn.execute(
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
        ).fetchall()

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
            for row in conn.execute(
                f"SELECT run_id, full_result_json FROM web_runs WHERE run_id IN ({placeholders})",
                web_page_ids,
            ).fetchall():
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
    finally:
        conn.close()


def get_archive_summary() -> dict:
    """Counts from both web_runs and the CLI archive."""
    _ensure_web_runs_table()
    db_path = _get_db_path()
    conn = _connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        # Total: web runs + CLI runs not imported via web
        imported_ids = set(
            r[0]
            for r in conn.execute(
                "SELECT archive_run_id FROM web_runs WHERE archive_run_id IS NOT NULL"
            ).fetchall()
        )
        web_count = conn.execute("SELECT COUNT(*) FROM web_runs").fetchone()[0]
        if imported_ids:
            cli_count = conn.execute(
                f"SELECT COUNT(DISTINCT r.run_id) FROM runs r "
                f"WHERE r.run_id NOT IN ({','.join('?' * len(imported_ids))})",
                list(imported_ids),
            ).fetchone()[0]
        else:
            cli_count = conn.execute("SELECT COUNT(DISTINCT run_id) FROM runs").fetchone()[0]

        total = web_count + cli_count

        # Sector breakdown from CLI archive
        sector_breakdown: dict[str, int] = {}
        action_breakdown: dict[str, int] = {}
        outcome_breakdown: dict[str, int] = {}

        cli_rows = conn.execute(
            "SELECT r.sector, ts.final_action, ts.outcome FROM ticker_signals ts "
            "JOIN runs r ON r.run_id = ts.run_id"
        ).fetchall()
        for row in cli_rows:
            sec = row["sector"] or "Unknown"
            act = row["final_action"] or "UNKNOWN"
            out = row["outcome"] or "PENDING"
            sector_breakdown[sec] = sector_breakdown.get(sec, 0) + 1
            action_breakdown[act] = action_breakdown.get(act, 0) + 1
            outcome_breakdown[out] = outcome_breakdown.get(out, 0) + 1

        # Augment with web-only runs (those without archive_run_id)
        web_only_rows = conn.execute(
            "SELECT full_result_json FROM web_runs "
            "WHERE archive_run_id IS NULL AND full_result_json IS NOT NULL "
            "AND json_extract(full_result_json, '$.checkpoint') IS NULL"
        ).fetchall()
        for (full_json,) in web_only_rows:
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
    finally:
        conn.close()


# ── Pipeline runner ───────────────────────────────────────────────────────────

async def run_analysis_pipeline(
    ticker: str,
    model_name: str,
    api_keys: dict,
    on_phase: Callable[..., None],  # (phase, status, summary, reasoning, ticker, timestamp, partial_data)
    selected_agents: list[str] | None = None,
    user_id: Optional[int] = None,
) -> tuple[str, dict]:
    """
    Run the 10-phase advanced pipeline for a ticker.
    Streams progress via on_phase callback.
    Returns (run_id, result_dict).
    """
    run_id = str(uuid.uuid4())
    loop = asyncio.get_event_loop()
    progress_queue: asyncio.Queue = asyncio.Queue()
    result_container: dict = {}
    error_container: dict = {}

    # ── Load .env.local so FMP_API_KEY and other keys are available ──────────
    from pathlib import Path
    _project_root = Path(__file__).parent.parent.parent.parent
    _env_local = _project_root / ".env.local"
    if _env_local.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(_env_local, override=True)   # override=True: .env.local wins over process env
        except ImportError:
            # dotenv not installed — parse manually
            for _line in _env_local.read_text(encoding="utf-8").splitlines():
                _line = _line.strip()
                if _line and not _line.startswith("#") and "=" in _line:
                    _k, _, _v = _line.partition("=")
                    if _k.strip():
                        os.environ[_k.strip()] = _v.strip().strip('"').strip("'")

    # ── Also apply any keys passed from the web UI DB (override=True) ────────
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
    for provider, key_value in api_keys.items():
        env_name = provider_to_env.get(provider, provider)
        if key_value:
            os.environ[env_name] = key_value

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
        # Tag this thread so every progress.update_status() call stamps the
        # correct run_id.  Handlers for other concurrent runs will filter it out.
        from src.utils.progress import progress as _prog
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

            # All LangChain agents (macro regime, industry specialist, investors, PM, etc.)
            # use Anthropic/Claude for structured output — Qwen's thinking mode blocks
            # function_calling which is required for Pydantic schema compliance.
            # deep_research.py handles its own HK→Qwen routing internally.
            state = run_advanced_pipeline(
                tickers=[ticker.upper()],
                start_date="2020-01-01",
                end_date=end_date,
                portfolio=portfolio,
                selected_agents=_agents,
                model_name=model_name,
                model_provider="Anthropic",
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

    thread = threading.Thread(target=_run_pipeline, daemon=True)
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
