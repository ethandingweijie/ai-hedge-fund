"""
src/research_ideas/hundred_q/storage.py
==========================================
SQLite persistence for the 100-Question screener — raw sqlite3 in
src/data/run_archive.db, matching every sibling screener
(complacency_storage.py, sw46_storage.py) and the Knowledge Graph cache,
per the approved plan's persistence decision.

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
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from src.research_ideas.hundred_q.schemas import HundredQCohortResult, HundredQTickerResult

logger = logging.getLogger(__name__)


def _get_db_path() -> str:
    import os

    env_path = os.environ.get("RUN_ARCHIVE_PATH")
    if env_path:
        return env_path
    here = Path(__file__).resolve()
    project_root = here.parent.parent.parent.parent
    return str(project_root / "src" / "data" / "run_archive.db")


def _connect(path: Optional[str] = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or _get_db_path())
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


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


def _ensure_tables() -> None:
    import os

    db_path = _get_db_path()
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = _connect(db_path)
    try:
        conn.executescript(_DDL)
        for idx in _INDEXES:
            conn.execute(idx)
        conn.commit()
    finally:
        conn.close()


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
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO hq_runs (run_id, created_at, run_type, trigger_ticker, trigger_type, ticker_count, finished_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET finished_at = excluded.finished_at
            """,
            (cohort.run_id, cohort.created_at, cohort.run_type, None, None, cohort.ticker_count, now_iso),
        )

        for result in cohort.results:
            _save_ticker_result(conn, cohort.run_id, result)

        conn.commit()
    finally:
        conn.close()


def _save_ticker_result(conn: sqlite3.Connection, run_id: str, result: HundredQTickerResult) -> None:
    for qa in result.question_ledger:
        conn.execute(
            """
            INSERT INTO hq_question_ledger
                (run_id, ticker, question_id, pillar, q_type, answer, raw_value, evaluated_at, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id, result.ticker, qa.question_id, qa.pillar, qa.q_type,
                None if qa.answer is None else int(qa.answer),
                qa.raw_value, qa.evaluated_at or datetime.now(timezone.utc).isoformat(), qa.source,
            ),
        )

    row = conn.execute("SELECT tier FROM hq_watchlist WHERE ticker = ?", (result.ticker,)).fetchone()
    prev_tier = row[0] if row else None
    now_iso = datetime.now(timezone.utc).isoformat()

    conn.execute(
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
        (
            result.ticker, result.name, result.sector, result.industry, result.tier,
            _sanitize_float(result.composite_pct), _sanitize_float(result.quant_composite_pct),
            _sanitize_float(result.qual_composite_pct), now_iso, now_iso, now_iso, run_id,
        ),
    )

    if prev_tier is not None and prev_tier != result.tier:
        conn.execute(
            """
            INSERT INTO hq_tier_history (ticker, from_tier, to_tier, composite_pct, changed_at, run_id, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.ticker, prev_tier, result.tier, _sanitize_float(result.composite_pct),
                now_iso, run_id, f"composite_pct={result.composite_pct}",
            ),
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
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO hq_runs (run_id, created_at, run_type, trigger_ticker, trigger_type, ticker_count, finished_at)
            VALUES (?, ?, 'event_triggered', ?, ?, 1, ?)
            """,
            (run_id, now_iso, ticker.upper(), trigger_type, now_iso),
        )
        for qa in quant_ledger:
            conn.execute(
                """
                INSERT INTO hq_question_ledger
                    (run_id, ticker, question_id, pillar, q_type, answer, raw_value, evaluated_at, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id, ticker.upper(), qa.question_id, qa.pillar, qa.q_type,
                    None if qa.answer is None else int(qa.answer),
                    qa.raw_value, qa.evaluated_at or now_iso, "fmp_edgar_derived",
                ),
            )
        conn.commit()
    finally:
        conn.close()


def record_sweep_run(run_id: str, run_type: str, ticker_count: int = 0) -> None:
    """Manifest-only hq_runs row for a scheduler cycle that touches many
    tickers (daily trigger sweep, quarterly annual backstop) rather than
    scoring one — used purely for the scheduler's own idempotency check
    (get_latest_run_by_type), not tied to any single ticker."""
    _ensure_tables()
    now_iso = datetime.now(timezone.utc).isoformat()
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO hq_runs (run_id, created_at, run_type, trigger_ticker, trigger_type, ticker_count, finished_at)
            VALUES (?, ?, ?, NULL, NULL, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET finished_at = excluded.finished_at
            """,
            (run_id, now_iso, run_type, ticker_count, now_iso),
        )
        conn.commit()
    finally:
        conn.close()


def get_latest_run_by_type(run_type: str) -> Optional[dict]:
    """Most recent hq_runs row of this run_type — used by scheduler.py's
    idempotency checks (e.g. "has the weekly quant batch already run in
    the last 6 days?")."""
    _ensure_tables()
    conn = _connect()
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM hq_runs WHERE run_type = ? ORDER BY created_at DESC LIMIT 1",
            (run_type,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_latest_run_id() -> Optional[str]:
    _ensure_tables()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT run_id FROM hq_runs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


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
    latest_run = None
    conn = _connect()
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM hq_runs ORDER BY created_at DESC LIMIT 1").fetchone()
        latest_run = dict(row) if row else None
    finally:
        conn.close()
    return {
        "ticker_count": len(watchlist),
        "tier_counts": tier_counts,
        "latest_run": latest_run,
    }


def get_watchlist(tier: Optional[str] = None) -> list[dict]:
    _ensure_tables()
    conn = _connect()
    conn.row_factory = sqlite3.Row
    try:
        if tier:
            rows = conn.execute(
                "SELECT * FROM hq_watchlist WHERE tier = ? ORDER BY composite_pct DESC", (tier,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM hq_watchlist ORDER BY composite_pct DESC"
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_question_ledger(run_id: str, ticker: str) -> list[dict]:
    _ensure_tables()
    conn = _connect()
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM hq_question_ledger WHERE run_id = ? AND ticker = ? ORDER BY question_id",
            (run_id, ticker.upper()),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_qual_cache(ticker: str, question_ids: Optional[list[str]] = None) -> dict[str, dict]:
    """Cached qualitative answers for a ticker, keyed by question_id. Pass
    question_ids to restrict to a subset (e.g. one pillar's questions)."""
    _ensure_tables()
    conn = _connect()
    conn.row_factory = sqlite3.Row
    try:
        if question_ids:
            placeholders = ",".join("?" * len(question_ids))
            rows = conn.execute(
                f"SELECT * FROM hq_qualitative_cache WHERE ticker = ? AND question_id IN ({placeholders})",
                (ticker.upper(), *question_ids),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM hq_qualitative_cache WHERE ticker = ?", (ticker.upper(),)
            ).fetchall()
        return {r["question_id"]: dict(r) for r in rows}
    finally:
        conn.close()


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
    conn = _connect()
    try:
        conn.execute(
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
            (
                ticker.upper(), question_id, pillar,
                None if answer is None else int(answer), confidence, summary, evidence_json,
                model_used, cost_usd, datetime.now(timezone.utc).isoformat(), triggered_by,
            ),
        )
        conn.commit()
    finally:
        conn.close()


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
    conn = _connect()
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
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
            (ticker.upper(), ticker.upper()),
        ).fetchall()
        return {r["question_id"]: dict(r) for r in rows}
    finally:
        conn.close()


def get_stale_qual_question_ids(ticker: str, all_question_ids: list[str], max_age_days: int = 365) -> list[str]:
    """Which of `all_question_ids` are missing from hq_qualitative_cache or
    older than max_age_days — used by the annual-backstop job (Phase 2/3),
    since staleness is inherently dynamic per ticker/time rather than a
    static trigger->question list."""
    _ensure_tables()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
    conn = _connect()
    conn.row_factory = sqlite3.Row
    try:
        placeholders = ",".join("?" * len(all_question_ids))
        rows = conn.execute(
            f"SELECT question_id, last_evaluated_at FROM hq_qualitative_cache "
            f"WHERE ticker = ? AND question_id IN ({placeholders})",
            (ticker.upper(), *all_question_ids),
        ).fetchall()
        fresh_ids = {r["question_id"] for r in rows if r["last_evaluated_at"] and r["last_evaluated_at"] >= cutoff}
        return [qid for qid in all_question_ids if qid not in fresh_ids]
    finally:
        conn.close()


def get_tier_history(ticker: str) -> list[dict]:
    _ensure_tables()
    conn = _connect()
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM hq_tier_history WHERE ticker = ? ORDER BY changed_at DESC",
            (ticker.upper(),),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
