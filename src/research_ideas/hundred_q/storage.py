"""
src/research_ideas/hundred_q/storage.py
==========================================
Persistence for the 100-Question screener in the shared run archive —
Postgres in production, SQLite locally — via the dual-mode `src.data.db`.

2026-11-27: ported off raw sqlite3. In production the private connection
followed RUN_ARCHIVE_PATH to a Windows path that does not exist in the
container, so every sweep and batch failed from 2026-08-08. The Postgres
tables (copied in 2026-04) are read back as they stand.

Phase 0 tables: hq_watchlist (systematic lifecycle tiers — separate from
watchlist_service.py's per-user manual watchlist), hq_runs (run manifest),
hq_question_ledger (per-run per-question audit trail), hq_tier_history
(tier-transition audit). hq_qualitative_cache is added in Phase 1 once
qualitative.py exists to read/write it — no point declaring it unused now.
"""
from __future__ import annotations

import json
import logging
import math
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from src.data import db

from src.research_ideas.hundred_q.schemas import HundredQCohortResult, HundredQTickerResult

logger = logging.getLogger(__name__)


_DDL = """
CREATE TABLE IF NOT EXISTS hq_watchlist (
    ticker              TEXT PRIMARY KEY,
    company_name        TEXT,
    sector              TEXT,
    industry            TEXT,
    tier                TEXT NOT NULL,
    composite_pct       REAL,
    quant_composite_pct REAL,
    qual_composite_pct  REAL,
    entered_tier_at     TEXT,
    cooloff_until       TEXT,
    last_quant_run_at   TEXT,
    last_qual_run_at    TEXT,
    last_full_eval_at   TEXT,
    run_id              TEXT
);

CREATE TABLE IF NOT EXISTS hq_runs (
    run_id          TEXT PRIMARY KEY,
    created_at      TEXT NOT NULL,
    run_type        TEXT NOT NULL,
    trigger_ticker  TEXT,
    trigger_type    TEXT,
    ticker_count    INTEGER,
    finished_at     TEXT
);

CREATE TABLE IF NOT EXISTS hq_tier_history (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker        TEXT NOT NULL,
    from_tier     TEXT,
    to_tier       TEXT NOT NULL,
    composite_pct REAL,
    changed_at    TEXT NOT NULL,
    run_id        TEXT NOT NULL,
    reason        TEXT
);

CREATE TABLE IF NOT EXISTS hq_question_ledger (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        TEXT NOT NULL,
    ticker        TEXT NOT NULL,
    question_id   TEXT NOT NULL,
    pillar        TEXT NOT NULL,
    q_type        TEXT NOT NULL,
    answer        INTEGER,
    raw_value     TEXT,
    evaluated_at  TEXT NOT NULL,
    source        TEXT
);

CREATE TABLE IF NOT EXISTS hq_trigger_state (
    ticker        TEXT NOT NULL,
    trigger_type  TEXT NOT NULL,
    key_date      TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    PRIMARY KEY (ticker, trigger_type)
);

CREATE TABLE IF NOT EXISTS hq_qualitative_cache (
    ticker            TEXT NOT NULL,
    question_id       TEXT NOT NULL,
    pillar            TEXT NOT NULL,
    answer            INTEGER,
    confidence        REAL,
    summary           TEXT,
    evidence_json     TEXT,
    model_used        TEXT,
    cost_usd          REAL,
    last_evaluated_at TEXT NOT NULL,
    triggered_by      TEXT NOT NULL,
    PRIMARY KEY (ticker, question_id)
);
"""

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_hq_ledger_ticker_q ON hq_question_ledger(ticker, question_id, evaluated_at)",
    "CREATE INDEX IF NOT EXISTS idx_hq_ledger_run ON hq_question_ledger(run_id)",
    "CREATE INDEX IF NOT EXISTS idx_hq_runs_created_at ON hq_runs(created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_hq_tier_history_ticker ON hq_tier_history(ticker, changed_at DESC)",
]


_SERIAL_TABLES = ("hq_tier_history", "hq_question_ledger")
_ensured_for: Optional[str] = None
_ensure_lock = threading.Lock()


def _ensure_tables() -> None:
    """Create the tables once per database target; give the Postgres id columns a default."""
    global _ensured_for
    target = "pg" if db.is_postgres() else db.get_db_path()
    if _ensured_for == target:
        return
    with _ensure_lock:
        if _ensured_for == target:
            return
        ddl = _DDL
        if db.is_postgres():
            ddl = ddl.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")
        db.execute_script(ddl)
        for idx in _INDEXES:
            db.execute(idx)
        for t in _SERIAL_TABLES:
            db.ensure_serial_id(t)
        _ensured_for = target


def _sanitize_float(v: Any) -> Any:
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


def save_run(cohort: HundredQCohortResult) -> None:
    """
    Persist a cohort run: the run manifest, every ticker's question ledger,
    and the current hq_watchlist state (tier + composite), recording a
    hq_tier_history row whenever a ticker's tier actually changed.
    """
    _ensure_tables()
    now_iso = datetime.now(timezone.utc).isoformat()
    db.execute(
        """
        INSERT INTO hq_runs (run_id, created_at, run_type, trigger_ticker, trigger_type, ticker_count, finished_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(run_id) DO UPDATE SET finished_at = excluded.finished_at
        """,
        [cohort.run_id, cohort.created_at, cohort.run_type, None, None, cohort.ticker_count, now_iso],
    )
    for result in cohort.results:
        _save_ticker_result(cohort.run_id, result)


_LEDGER_INSERT = """
    INSERT INTO hq_question_ledger
        (run_id, ticker, question_id, pillar, q_type, answer, raw_value, evaluated_at, source)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def _save_ticker_result(run_id: str, result: HundredQTickerResult) -> None:
    ledger = [
        [
            run_id, result.ticker, qa.question_id, qa.pillar, qa.q_type,
            None if qa.answer is None else int(qa.answer),
            qa.raw_value, qa.evaluated_at or datetime.now(timezone.utc).isoformat(), qa.source,
        ]
        for qa in result.question_ledger
    ]
    if ledger:
        db.executemany(_LEDGER_INSERT, ledger)

    row = db.query_one("SELECT tier FROM hq_watchlist WHERE ticker = ?", [result.ticker])
    prev_tier = row["tier"] if row else None
    now_iso = datetime.now(timezone.utc).isoformat()

    db.execute(
        """
        INSERT INTO hq_watchlist
            (ticker, company_name, sector, industry, tier, composite_pct, quant_composite_pct,
             qual_composite_pct, entered_tier_at, last_quant_run_at, last_full_eval_at, run_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(ticker) DO UPDATE SET
            company_name        = excluded.company_name,
            sector              = excluded.sector,
            industry            = excluded.industry,
            tier                = excluded.tier,
            composite_pct       = excluded.composite_pct,
            quant_composite_pct = excluded.quant_composite_pct,
            qual_composite_pct  = COALESCE(excluded.qual_composite_pct, hq_watchlist.qual_composite_pct),
            entered_tier_at     = CASE WHEN hq_watchlist.tier != excluded.tier THEN excluded.entered_tier_at ELSE hq_watchlist.entered_tier_at END,
            last_quant_run_at   = excluded.last_quant_run_at,
            last_full_eval_at   = excluded.last_full_eval_at,
            run_id              = excluded.run_id
        """,
        [
            result.ticker, result.name, result.sector, result.industry, result.tier,
            _sanitize_float(result.composite_pct), _sanitize_float(result.quant_composite_pct),
            _sanitize_float(result.qual_composite_pct), now_iso, now_iso, now_iso, run_id,
        ],
    )

    if prev_tier is not None and prev_tier != result.tier:
        db.execute(
            """
            INSERT INTO hq_tier_history (ticker, from_tier, to_tier, composite_pct, changed_at, run_id, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                result.ticker, prev_tier, result.tier, _sanitize_float(result.composite_pct),
                now_iso, run_id, f"composite_pct={result.composite_pct}",
            ],
        )


def record_event_triggered_run(
    run_id: str, ticker: str, trigger_type: str, quant_ledger: list,
) -> None:
    """Persist the manifest + ledger rows for a single-ticker event-triggered
    partial rescore (run_event_triggered_rescore). Only the QUANT question
    answers go in hq_question_ledger here — qualitative answers are already
    persisted by qualitative.assess_qualitative_pillar's own cache writes."""
    _ensure_tables()
    now_iso = datetime.now(timezone.utc).isoformat()
    db.execute(
        """
        INSERT INTO hq_runs (run_id, created_at, run_type, trigger_ticker, trigger_type, ticker_count, finished_at)
        VALUES (?, ?, 'event_triggered', ?, ?, 1, ?)
        """,
        [run_id, now_iso, ticker.upper(), trigger_type, now_iso],
    )
    ledger = [
        [
            run_id, ticker.upper(), qa.question_id, qa.pillar, qa.q_type,
            None if qa.answer is None else int(qa.answer),
            qa.raw_value, qa.evaluated_at or now_iso, "fmp_edgar_derived",
        ]
        for qa in quant_ledger
    ]
    if ledger:
        db.executemany(_LEDGER_INSERT, ledger)


def record_sweep_run(run_id: str, run_type: str, ticker_count: int = 0) -> None:
    """Manifest-only hq_runs row for a scheduler cycle that touches many
    tickers (daily trigger sweep, quarterly annual backstop) rather than
    scoring one — used purely for the scheduler's own idempotency check
    (get_latest_run_by_type), not tied to any single ticker."""
    _ensure_tables()
    now_iso = datetime.now(timezone.utc).isoformat()
    db.execute(
        """
        INSERT INTO hq_runs (run_id, created_at, run_type, trigger_ticker, trigger_type, ticker_count, finished_at)
        VALUES (?, ?, ?, NULL, NULL, ?, ?)
        ON CONFLICT(run_id) DO UPDATE SET finished_at = excluded.finished_at
        """,
        [run_id, now_iso, run_type, ticker_count, now_iso],
    )


def get_latest_run_by_type(run_type: str) -> Optional[dict]:
    """Most recent hq_runs row of this run_type — used by scheduler.py's
    idempotency checks (e.g. "has the weekly quant batch already run in
    the last 6 days?")."""
    _ensure_tables()
    row = db.query_one(
        "SELECT * FROM hq_runs WHERE run_type = ? ORDER BY created_at DESC LIMIT 1",
        [run_type],
    )
    return dict(row) if row else None


def get_latest_run_id() -> Optional[str]:
    _ensure_tables()
    row = db.query_one("SELECT run_id FROM hq_runs ORDER BY created_at DESC LIMIT 1")
    return row["run_id"] if row else None


def get_cohort_summary() -> Optional[dict]:
    """Snapshot for the /research/ideas catalogue card and the main GET
    endpoint: tier counts + the most recent run's metadata. Cheap — reads
    hq_watchlist rather than replaying any run."""
    _ensure_tables()
    watchlist = get_watchlist()
    if not watchlist:
        return None
    tier_counts: dict[str, int] = {}
    for row in watchlist:
        tier_counts[row["tier"]] = tier_counts.get(row["tier"], 0) + 1
    row = db.query_one("SELECT * FROM hq_runs ORDER BY created_at DESC LIMIT 1")
    latest_run = dict(row) if row else None
    return {
        "ticker_count": len(watchlist),
        "tier_counts": tier_counts,
        "latest_run": latest_run,
    }


def get_watchlist(tier: Optional[str] = None) -> list[dict]:
    _ensure_tables()
    # NULLs last on both engines (SQLite already sorts NULL last under DESC; Postgres would put it first).
    order = "ORDER BY composite_pct IS NULL, composite_pct DESC"
    if tier:
        rows = db.query(f"SELECT * FROM hq_watchlist WHERE tier = ? {order}", [tier])
    else:
        rows = db.query(f"SELECT * FROM hq_watchlist {order}")
    return [dict(r) for r in rows]


def get_question_ledger(run_id: str, ticker: str) -> list[dict]:
    _ensure_tables()
    rows = db.query(
        "SELECT * FROM hq_question_ledger WHERE run_id = ? AND ticker = ? ORDER BY question_id",
        [run_id, ticker.upper()],
    )
    return [dict(r) for r in rows]


def get_qual_cache(ticker: str, question_ids: Optional[list[str]] = None) -> dict[str, dict]:
    """Cached qualitative answers for a ticker, keyed by question_id. Pass
    question_ids to restrict to a subset (e.g. one pillar's questions)."""
    _ensure_tables()
    if question_ids:
        placeholders = ",".join("?" * len(question_ids))
        rows = db.query(
            f"SELECT * FROM hq_qualitative_cache WHERE ticker = ? AND question_id IN ({placeholders})",
            [ticker.upper(), *question_ids],
        )
    else:
        rows = db.query("SELECT * FROM hq_qualitative_cache WHERE ticker = ?", [ticker.upper()])
    return {r["question_id"]: dict(r) for r in rows}


def set_qual_answer(
    ticker: str,
    question_id: str,
    pillar: str,
    answer: Optional[bool],
    confidence: Optional[float],
    summary: Optional[str],
    evidence_json: str,
    model_used: str,
    cost_usd: float,
    triggered_by: str,
) -> None:
    """Upsert one qualitative answer — the unit of a pillar-scoped rescore
    only ever touches the rows it was asked to score, never the whole cache."""
    _ensure_tables()
    db.execute(
            """
            INSERT INTO hq_qualitative_cache
                (ticker, question_id, pillar, answer, confidence, summary, evidence_json,
                 model_used, cost_usd, last_evaluated_at, triggered_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker, question_id) DO UPDATE SET
                pillar            = excluded.pillar,
                answer            = excluded.answer,
                confidence        = excluded.confidence,
                summary           = excluded.summary,
                evidence_json     = excluded.evidence_json,
                model_used        = excluded.model_used,
                cost_usd          = excluded.cost_usd,
                last_evaluated_at = excluded.last_evaluated_at,
                triggered_by      = excluded.triggered_by
            """,
            [
                ticker.upper(), question_id, pillar,
                None if answer is None else int(answer), confidence, summary, evidence_json,
                model_used, cost_usd, datetime.now(timezone.utc).isoformat(), triggered_by,
            ],
        )


def get_latest_quant_answers(ticker: str) -> dict[str, dict]:
    """Latest known answer per question_id for this ticker, across ALL
    past runs (not just one run_id) — the "current state" of the
    ticker's quant ledger. Used to assemble a full quant+qual composite
    for a single ticker after an event-triggered PARTIAL rescore, where
    only a handful of question_ids were freshly recomputed and the rest
    should keep their last-known value rather than disappearing.

    One query with a window function (latest row per question_id),
    mirroring complacency's _rehydrate_qual_from_cache pattern.
    """
    _ensure_tables()
    rows = db.query(
            """
            SELECT l.question_id, l.pillar, l.q_type, l.answer, l.raw_value, l.evaluated_at, l.source
            FROM hq_question_ledger l
            INNER JOIN (
                SELECT question_id, MAX(evaluated_at) AS latest
                FROM hq_question_ledger
                WHERE ticker = ?
                GROUP BY question_id
            ) m ON l.question_id = m.question_id AND l.evaluated_at = m.latest
            WHERE l.ticker = ?
            """,
            [ticker.upper(), ticker.upper()],
        )
    return {r["question_id"]: dict(r) for r in rows}


def get_stale_qual_question_ids(ticker: str, all_question_ids: list[str], max_age_days: int = 365) -> list[str]:
    """Which of `all_question_ids` are missing from hq_qualitative_cache or
    older than max_age_days — used by the annual-backstop job (Phase 2/3),
    since staleness is inherently dynamic per ticker/time rather than a
    static trigger->question list."""
    _ensure_tables()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
    if not all_question_ids:
        return []
    placeholders = ",".join("?" * len(all_question_ids))
    rows = db.query(
        f"SELECT question_id, last_evaluated_at FROM hq_qualitative_cache "
        f"WHERE ticker = ? AND question_id IN ({placeholders})",
        [ticker.upper(), *all_question_ids],
    )
    fresh_ids = {r["question_id"] for r in rows if r["last_evaluated_at"] and r["last_evaluated_at"] >= cutoff}
    return [qid for qid in all_question_ids if qid not in fresh_ids]


def get_tier_history(ticker: str) -> list[dict]:
    _ensure_tables()
    rows = db.query(
        "SELECT * FROM hq_tier_history WHERE ticker = ? ORDER BY changed_at DESC",
        [ticker.upper()],
    )
    return [dict(r) for r in rows]


# ─── Daily-sweep trigger state ─────────────────────────────────────────────
# 2026-11-27: the "already fired" memory lived in a JSON file inside the
# container, wiped on every deploy, so each deploy re-fired every ticker's
# latest filing / earnings as a new event (an LLM rescore apiece). It lives
# here now, in the same shape src.triggers.state uses: {ticker: {trigger: key}}.


def load_trigger_state() -> dict:
    _ensure_tables()
    out: dict = {}
    for r in db.query("SELECT ticker, trigger_type, key_date FROM hq_trigger_state"):
        out.setdefault(r["ticker"], {})[r["trigger_type"]] = r["key_date"]
    return out


def save_trigger_state(state: dict) -> None:
    _ensure_tables()
    now_iso = datetime.now(timezone.utc).isoformat()
    rows = [[t, trig, str(key), now_iso]
            for t, by in (state or {}).items() if isinstance(by, dict)
            for trig, key in by.items() if key]
    if rows:
        db.executemany(
            """
            INSERT INTO hq_trigger_state (ticker, trigger_type, key_date, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(ticker, trigger_type) DO UPDATE SET
                key_date = excluded.key_date, updated_at = excluded.updated_at
            """,
            rows,
        )
