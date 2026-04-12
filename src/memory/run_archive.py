"""
src/memory/run_archive.py
=========================
SQLite-backed episodic run archive for the AI Hedge Fund pipeline.

Three tables:
  runs           — one row per pipeline run (regime + metadata + industry brief + deep research)
  ticker_signals — one row per ticker per run (final decision + intel + DCF + debate + PM rationale)
  agent_signals  — one row per agent per ticker per run (thesis, price target, key risks)

Outcome columns in ticker_signals / agent_signals are filled later by
run_post_trade_review() → update_outcomes().

Usage:
    from src.memory.run_archive import save_run, load_runs, update_outcomes, get_agent_outcomes

The .db file lives at:
    src/data/run_archive.db

It can be opened directly with DB Browser for SQLite, DBeaver, TablePlus, or
any SQL GUI — no code required.

Schema version: 2  (added industry_brief, deep_research, DCF, debate, PM rationale, agent thesis)
"""

import json
import os
import sqlite3
import uuid
from datetime import datetime, timedelta
from typing import Any

# ── Constants ────────────────────────────────────────────────────────────────

DB_PATH = os.environ.get("RUN_ARCHIVE_PATH",
          os.path.join(os.path.dirname(__file__), "..", "data", "run_archive.db"))
PIPELINE_VERSION = "2.0"

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
    last_updated          TEXT NOT NULL          -- ISO-8601 timestamp
);
"""

# ── Migrations (for existing databases missing new columns) ───────────────────
# Each entry is (table, column, definition). Applied with ALTER TABLE ADD COLUMN
# inside a try/except so they are silently skipped if the column already exists.

_MIGRATIONS = [
    # v2 additions to runs
    ("runs", "research_tier",        "TEXT"),
    ("runs", "industry_brief_text",  "TEXT"),
    ("runs", "deep_research_text",   "TEXT"),
    ("runs", "regime_recession_risk","TEXT"),   # v2.1 — 5th regime dimension

    # v2 additions to ticker_signals
    ("ticker_signals", "entry_range_low",           "REAL"),
    ("ticker_signals", "entry_range_high",          "REAL"),
    ("ticker_signals", "time_horizon",              "TEXT"),
    ("ticker_signals", "pm_rationale",              "TEXT"),
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
]


# ── Internal helpers ──────────────────────────────────────────────────────────

def _get_conn() -> sqlite3.Connection:
    """Open (and initialise / migrate if needed) the archive database."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

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

    return conn


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
        conn = _get_conn()
        with conn:
            # ── runs row ──────────────────────────────────────────────────────
            conn.execute(
                """
                INSERT INTO runs (
                    run_id, run_at, analysis_date, sector,
                    regime_risk_appetite, regime_rate_direction,
                    regime_volatility, regime_dollar, regime_recession_risk,
                    tickers, model_name, pipeline_version,
                    research_tier, industry_brief_text, deep_research_text
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                    data.get("industry_brief") or data.get("deep_research") or None,
                    data.get("deep_research") or None,
                ),
            )

            # ── per-ticker rows ───────────────────────────────────────────────
            analyst_signals: dict = data.get("analyst_signals", {})
            skip_agents = {"risk_management_agent", "advanced_risk_manager"}
            debate_results: dict = data.get("debate_result", {})
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

                # Debate
                debate = debate_results.get(ticker, {})
                debate_triggered = int(bool(debate))
                debate_adj_signal = debate.get("adjudicated_signal") if debate else None

                # Entry range
                entry_range = decision.get("entry_range") or []
                entry_low  = _safe(entry_range[0], float) if len(entry_range) > 0 else None
                entry_high = _safe(entry_range[1], float) if len(entry_range) > 1 else None

                # ── v3 JSON blobs ─────────────────────────────────────────────
                pl_json      = _safe_json(pl)   if pl   is not None else None
                scen_json    = _safe_json(scen) if scen is not None else None
                dcf_rng_json = _safe_json(dcf)  if dcf  is not None else None  # v3.1
                vt_json      = _safe_json(trap) if trap is not None else None  # v3.2

                # raw_financials lives at state["data"]["raw_financials"] (LLM-formatted,
                # keyed by FY year) — store as-is so FinancialsTable can render it
                raw_fin = data.get("raw_financials") or {}
                raw_fin_json = _safe_json(raw_fin) if raw_fin else None

                ca_json = _safe_json(data.get("citation_audit", {}).get(ticker))

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
                    vgpm_result = _compute_vgpm(
                        dcf_ticker=dcf,
                        scen_ticker=scen,
                        raw_financials=raw_fin,
                        dcf_cal=dcf_cal_data,
                        insider_summary=insider_sum,
                    )
                    vgpm_json = _safe_json(vgpm_result)
                except Exception:
                    pass

                conn.execute(
                    """
                    INSERT OR IGNORE INTO ticker_signals (
                        run_id, ticker,
                        final_action, position_size_pct, price_target, stop_loss,
                        entry_range_low, entry_range_high, time_horizon, pm_rationale,
                        price_at_run,
                        dcf_base_iv, dcf_bear_iv, dcf_bull_iv, dcf_wacc, dcf_iv_vs_price_pct,
                        debate_triggered, debate_adjudicated_signal,
                        si_signal, si_short_float_pct, si_squeeze_risk, si_crowded_trade,
                        insider_signal, revision_direction, news_signal,
                        eq_quality_verdict, eq_quality_score,
                        value_trap_verdict, ev_upside_pct, power_law_score,
                        power_law_json, scenario_json, raw_financials_json,
                        citation_audit_json, vgpm_json, dcf_range_json, value_trap_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
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
                        """
                        INSERT OR IGNORE INTO agent_signals
                            (run_id, ticker, agent_key, signal, conviction,
                             price_target, time_horizon, thesis_summary, key_risks)
                        VALUES (?,?,?,?,?,?,?,?,?)
                        """,
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
        conn = _get_conn()
        updated = 0

        if run_id:
            rows = conn.execute(
                """
                SELECT ts.run_id, ts.ticker, ts.final_action, ts.price_at_run
                FROM ticker_signals ts
                WHERE ts.ticker = ? AND ts.run_id = ? AND ts.outcome = 'PENDING'
                """,
                (ticker, run_id),
            ).fetchall()
        else:
            cutoff = datetime.now().strftime("%Y-%m-%d")
            rows = conn.execute(
                """
                SELECT ts.run_id, ts.ticker, ts.final_action, ts.price_at_run
                FROM ticker_signals ts
                JOIN runs r ON r.run_id = ts.run_id
                WHERE ts.ticker = ?
                  AND ts.outcome = 'PENDING'
                  AND date(r.run_at) <= date(?, ?)
                """,
                (ticker, cutoff, f"-{days_back} days"),
            ).fetchall()

        with conn:
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

                conn.execute(
                    """
                    UPDATE ticker_signals
                    SET outcome = ?, review_date = ?, price_at_review = ?, pct_change = ?
                    WHERE run_id = ? AND ticker = ?
                    """,
                    (outcome, review_date, price_at_review, round(pct_change, 4),
                     row["run_id"], row["ticker"]),
                )

                # Propagate outcome to agent_signals that agreed with final action
                agent_rows = conn.execute(
                    "SELECT id, signal FROM agent_signals WHERE run_id=? AND ticker=? AND outcome='PENDING'",
                    (row["run_id"], row["ticker"]),
                ).fetchall()

                for ar in agent_rows:
                    agent_sig = ar["signal"] or "HOLD"
                    agreed = (
                        (action in ("BUY", "COVER") and agent_sig == "BUY") or
                        (action in ("SELL", "SHORT") and agent_sig in ("SELL", "SHORT"))
                    )
                    agent_outcome = outcome if agreed else "NEUTRAL"
                    conn.execute(
                        "UPDATE agent_signals SET outcome=? WHERE id=?",
                        (agent_outcome, ar["id"]),
                    )

                updated += 1

        conn.close()
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
        conn = _get_conn()
        rows = conn.execute(
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
            (min_reviews,),
        ).fetchall()
        conn.close()

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
        conn = _get_conn()
        rows = conn.execute(
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
            (min_reviews,),
        ).fetchall()
        conn.close()
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

    Returns None if no qualifying run is found.
    """
    try:
        conn  = _get_conn()
        cutoff = (datetime.now() - timedelta(days=max_age_days)).isoformat()

        row = conn.execute(
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
                ts.value_trap_json
            FROM runs r
            JOIN ticker_signals ts ON r.run_id = ts.run_id AND ts.ticker = ?
            WHERE r.run_at >= ?
            ORDER BY r.run_at DESC
            LIMIT 1
            """,
            (ticker, cutoff),
        ).fetchone()
        conn.close()

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
        }

    except Exception as exc:
        print(f"  [cache] Warning: get_phase_cache({ticker}) failed: {exc}")
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
        last_updated (str ISO-8601), age_days (float)
    or None if no valid cache entry exists.
    """
    try:
        conn   = _get_conn()
        cutoff = (datetime.now() - timedelta(days=max_age_days)).isoformat()

        row = conn.execute(
            """
            SELECT ticker, sector, sector_llm_raw, sector_confidence, sector_warning,
                   company_name, routing_decision_json, raw_financials_json, last_updated
            FROM ticker_routing_cache
            WHERE ticker = ? AND last_updated >= ?
            """,
            (ticker.upper(), cutoff),
        ).fetchone()
        conn.close()

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
) -> None:
    """
    Upsert (INSERT OR REPLACE) a routing-cache entry for *ticker*.
    Called at the end of run_strategic_router() after a successful LLM call.
    """
    try:
        conn = _get_conn()
        conn.execute(
            """
            INSERT OR REPLACE INTO ticker_routing_cache
                (ticker, sector, sector_llm_raw, sector_confidence, sector_warning,
                 company_name, routing_decision_json, raw_financials_json, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ticker.upper(),
                sector,
                sector_llm_raw,
                sector_confidence,
                sector_warning,
                company_name,
                _safe_json(routing_decision),
                _safe_json(raw_financials),
                datetime.now().isoformat(),
            ),
        )
        conn.commit()
        conn.close()
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
        conn = _get_conn()
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

        run_rows = conn.execute(
            f"SELECT * FROM runs {where} ORDER BY run_at DESC LIMIT ?", params
        ).fetchall()

        result = []
        for rr in run_rows:
            ts_rows = conn.execute(
                "SELECT * FROM ticker_signals WHERE run_id=?", (rr["run_id"],)
            ).fetchall()
            ag_rows = conn.execute(
                "SELECT * FROM agent_signals WHERE run_id=?", (rr["run_id"],)
            ).fetchall()

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

        conn.close()
        return result

    except Exception as exc:
        print(f"  [archive] Warning: load_runs failed: {exc}")
        return []


def _parse_sections_inline(text: str) -> dict[str, str]:
    """
    Self-contained Section 2 parser — mirrors deep_research._extract_sections().

    Kept here to avoid a circular import (run_archive → deep_research).
    Returns {"2a": ..., "2b": ..., ..., "2f": ...} or {"full": text} if no
    section headers are found.
    """
    import re
    boundary = re.compile(r"(?:^|\n)[ \t#─]*\b(2[A-F])[\.\s]", re.IGNORECASE)
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
    return sections


def get_recent_research(
    ticker: str,
    max_age_days: int = 7,
    qualifying_tiers: tuple[str, ...] = ("anthropic_web", "tavily", "qwen_web"),
) -> dict | None:
    """
    Return the most recent qualifying deep-research run for `ticker`, or None.

    A run qualifies when ALL of:
      - `ticker` appears in runs.tickers JSON array
      - runs.research_tier is in qualifying_tiers  (excludes knowledge_only / none)
      - runs.deep_research_text is non-empty
      - runs.run_at is within the last max_age_days days

    Returns a dict with keys:
        run_id               str
        run_at               str   ISO-8601 timestamp
        analysis_date        str   end_date used in that run
        age_days             float decimal days at call time
        research_tier        str
        deep_research_text   str   full Section 2 report
        deep_research_sections dict[str, str]  pre-parsed via _parse_sections_inline()

    Returns None if no qualifying run is found or if the DB is unavailable.
    """
    cutoff = (datetime.now() - timedelta(days=max_age_days)).isoformat()
    placeholders = ",".join("?" * len(qualifying_tiers))
    # Match runs where this ticker was the PRIMARY ticker (deep_research_text
    # stores the primary ticker's research only).  Using tickers LIKE would
    # match multi-ticker runs where this ticker was secondary — returning the
    # primary ticker's research text for the wrong company (NEE→ZIM bleed bug).
    # The tickers JSON array's FIRST element is always the primary ticker.
    sql = f"""
        SELECT run_id, run_at, analysis_date, research_tier, deep_research_text
        FROM   runs
        WHERE  tickers LIKE ?
        AND    research_tier IN ({placeholders})
        AND    deep_research_text IS NOT NULL
        AND    deep_research_text != ''
        AND    run_at >= ?
        ORDER  BY run_at DESC
        LIMIT  1
    """
    # Match only when ticker is the FIRST element in the JSON array:
    # '["ZIM"' at position 0 ensures ZIM was the primary ticker.
    params: list = [f'["{ticker.upper()}"%', *qualifying_tiers, cutoff]
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(sql, params).fetchone()
    except Exception:
        return None

    if not row:
        return None

    age_days = (
        datetime.now() - datetime.fromisoformat(row["run_at"])
    ).total_seconds() / 86_400

    text = row["deep_research_text"]
    return {
        "run_id":                  row["run_id"],
        "run_at":                  row["run_at"],
        "analysis_date":           row["analysis_date"],
        "age_days":                round(age_days, 2),
        "research_tier":           row["research_tier"],
        "deep_research_text":      text,
        "deep_research_sections":  _parse_sections_inline(text),
    }


def archive_summary() -> dict:
    """Return high-level stats about the archive (for display)."""
    try:
        conn = _get_conn()
        total_runs    = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        total_tickers = conn.execute("SELECT COUNT(*) FROM ticker_signals").fetchone()[0]
        pending       = conn.execute(
            "SELECT COUNT(*) FROM ticker_signals WHERE outcome='PENDING'"
        ).fetchone()[0]
        scored = total_tickers - pending

        # Tier breakdown
        tier_rows = conn.execute(
            "SELECT research_tier, COUNT(*) as cnt FROM runs GROUP BY research_tier"
        ).fetchall()
        tiers = {r["research_tier"] or "unknown": r["cnt"] for r in tier_rows}

        conn.close()
        return {
            "total_runs":            total_runs,
            "total_ticker_signals":  total_tickers,
            "scored":                scored,
            "pending":               pending,
            "research_tiers":        tiers,
            "db_path":               DB_PATH,
        }
    except Exception:
        return {
            "total_runs": 0,
            "total_ticker_signals": 0,
            "scored": 0,
            "pending": 0,
            "research_tiers": {},
            "db_path": DB_PATH,
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
        conn = _get_conn()
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

        rows = conn.execute(
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
        ).fetchall()

        result = [dict(r) for r in rows]

        # ── Attach per-agent signals to each row ──────────────────────────────────
        # Build a lightweight index: (run_id, ticker) → [agent_signal dicts]
        all_run_ticker_pairs = [(r["run_id"], r["ticker"]) for r in result]
        if all_run_ticker_pairs:
            placeholders = ",".join("(?,?)" for _ in all_run_ticker_pairs)
            flat = [val for pair in all_run_ticker_pairs for val in pair]
            ag_all = conn.execute(
                f"""
                SELECT run_id, ticker, agent_key, signal, conviction,
                       price_target, time_horizon, thesis_summary, outcome
                FROM agent_signals
                WHERE (run_id, ticker) IN ({placeholders})
                """,
                flat,
            ).fetchall()
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

            ag_rows = conn.execute(
                f"""
                SELECT ag.run_id, ag.ticker, ag.agent_key, ag.signal,
                       ag.conviction, ag.price_target, ag.time_horizon,
                       ag.thesis_summary, ag.key_risks, ag.outcome
                FROM agent_signals ag
                WHERE {' AND '.join(agent_clauses)}
                """,
                agent_params,
            ).fetchall()

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

        conn.close()
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
        conn = _get_conn()
        rows = conn.execute(
            "SELECT DISTINCT ticker FROM ticker_signals ORDER BY ticker"
        ).fetchall()
        conn.close()
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
        conn = _get_conn()

        if tickers is None:
            rows_t  = conn.execute("SELECT DISTINCT ticker FROM ticker_signals").fetchall()
            tickers = [r["ticker"] for r in rows_t]

        if not tickers:
            conn.close()
            return []

        result = []
        for ticker in tickers:
            sig = conn.execute(
                """
                SELECT ts.ticker, ts.final_action, ts.position_size_pct,
                       ts.run_id, r.run_at
                FROM ticker_signals ts
                JOIN runs r ON ts.run_id = r.run_id
                WHERE ts.ticker = ?
                ORDER BY r.run_at DESC
                LIMIT 1
                """,
                (ticker,),
            ).fetchone()

            if not sig:
                continue

            sig_dict = dict(sig)

            ag_rows = conn.execute(
                "SELECT agent_key, signal FROM agent_signals WHERE run_id=? AND ticker=?",
                (sig["run_id"], ticker),
            ).fetchall()
            sig_dict["agent_votes"] = [
                {"agent_key": r["agent_key"], "signal": r["signal"]} for r in ag_rows
            ]
            result.append(sig_dict)

        conn.close()
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
        conn = _get_conn()
        with conn:
            conn.execute(
                """
                INSERT INTO rotation_events
                    (event_id, event_at, old_regime, new_regime,
                     shift_score, shift_label, recommendations, sector_signal, alert_sent)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    event_id,
                    event_at,
                    json.dumps(rotation_result.get("old_regime", {})),
                    json.dumps(rotation_result.get("new_regime", {})),
                    rotation_result.get("shift_score", 0),
                    rotation_result.get("shift_label", "NONE"),
                    json.dumps(rotation_result.get("recommendations", [])),
                    json.dumps(rotation_result.get("sector_signal", {})),
                    int(alert_sent),
                ),
            )
        conn.close()
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
        conn = _get_conn()
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

        rows = conn.execute(
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
        ).fetchall()

        conn.close()
        return [dict(r) for r in rows]

    except Exception as exc:
        print(f"  [archive] Warning: agent_backtest_query failed: {exc}")
        return []
