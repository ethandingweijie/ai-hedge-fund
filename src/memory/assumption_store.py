"""
src/memory/assumption_store.py
==============================
Workstream R1 (persist) + R3 (versioning) — dual-mode storage for
earnings assumptions, analyst-report extractions, and the Assumption
Steward's recursive-learning ledger.

Tables (plain DDL, CREATE IF NOT EXISTS — new tables, so no schema_guard
needed; dual-mode via src.data.db):

  earnings_assumptions   CURRENT company guidance/outlook per quarter
                         (PK ticker, fiscal_year, fiscal_quarter)
  analyst_reports        CURRENT sell-side view per document
                         (PK ticker, content_hash)
  assumption_versions    APPEND-ONLY trajectory of every extracted value
                         incl. stated priors — R3's recursion data
  assumption_challenges  anomalies raised by R3 (open/resolved/dismissed)
  assumption_scorecard   hit-rate ledger: predictions vs reported actuals

The local backend runs in PG mode, so production runs consume these rows
even though the raw PDFs/filings never leave this machine.
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from typing import Optional

from src.data import db

_DDL = """
CREATE TABLE IF NOT EXISTS earnings_assumptions (
    ticker TEXT NOT NULL,
    fiscal_year INTEGER NOT NULL,
    fiscal_quarter INTEGER NOT NULL,
    as_of TEXT,
    source TEXT,
    source_ref TEXT,
    period_label TEXT,
    guidance_json TEXT,
    segments_json TEXT,
    margins_json TEXT,
    kpis_json TEXT,
    capital_allocation_json TEXT,
    one_offs_json TEXT,
    quotes_json TEXT,
    extracted_at TEXT,
    model_used TEXT,
    PRIMARY KEY (ticker, fiscal_year, fiscal_quarter)
);

CREATE TABLE IF NOT EXISTS analyst_reports (
    ticker TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    house TEXT,
    analyst TEXT,
    report_date TEXT,
    rating TEXT,
    price_target REAL,
    price_target_currency TEXT,
    pt_methodology_json TEXT,
    estimates_json TEXT,
    house_vs_consensus_json TEXT,
    scenarios_json TEXT,
    thesis_json TEXT,
    revisions_json TEXT,
    doc_path TEXT,
    drive_file_id TEXT,
    source_url TEXT,
    ai_input_allowed INTEGER DEFAULT 0,
    extracted_at TEXT,
    model_used TEXT,
    PRIMARY KEY (ticker, content_hash)
);

CREATE TABLE IF NOT EXISTS assumption_versions (
    id TEXT PRIMARY KEY,
    ticker TEXT NOT NULL,
    source TEXT NOT NULL,
    fiscal_year INTEGER,
    fiscal_quarter INTEGER,
    field_key TEXT NOT NULL,
    new_value TEXT,
    prior_value_stated TEXT,
    direction TEXT,
    doc_ref TEXT,
    at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS assumption_challenges (
    id TEXT PRIMARY KEY,
    ticker TEXT NOT NULL,
    raised_at TEXT NOT NULL,
    field_key TEXT,
    anomaly_type TEXT,
    evidence TEXT,
    status TEXT NOT NULL,
    resolution TEXT,
    outcome_note TEXT,
    resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS assumption_scorecard (
    ticker TEXT NOT NULL,
    source TEXT NOT NULL,
    field_key TEXT NOT NULL,
    fiscal_year INTEGER,
    fiscal_quarter INTEGER,
    predicted TEXT,
    actual TEXT,
    in_range INTEGER,
    magnitude REAL,
    scored_at TEXT,
    PRIMARY KEY (ticker, source, field_key, fiscal_year, fiscal_quarter)
);

-- Qualitative readings of an earnings call: management tone, what analysts
-- pushed on, risk language that is new this quarter. Deliberately a SEPARATE
-- table rather than columns on earnings_assumptions — partly so no ALTER is
-- needed on an existing table (schema_guard territory), and partly because
-- none of this may ever move an intrinsic value. It feeds the LLM write-up
-- and R3's challenge loop only.
CREATE TABLE IF NOT EXISTS transcript_signals (
    ticker TEXT NOT NULL,
    fiscal_year INTEGER NOT NULL,
    fiscal_quarter INTEGER NOT NULL,
    as_of TEXT,
    tone_shift TEXT,
    qa_pressure_json TEXT,
    new_risks_json TEXT,
    strategic_pivots_json TEXT,
    regulatory_json TEXT,
    speakers_json TEXT,
    industry_label TEXT,
    extracted_at TEXT,
    model_used TEXT,
    PRIMARY KEY (ticker, fiscal_year, fiscal_quarter)
);
"""

_ensure_lock = threading.Lock()
_ensured = False


def ensure_assumption_tables() -> None:
    """Idempotent table creation (first use per process)."""
    global _ensured
    if _ensured:
        return
    with _ensure_lock:
        if _ensured:
            return
        db.execute_script(_DDL)
        _ensured = True


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dumps(obj) -> Optional[str]:
    if obj is None:
        return None
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return None


def _loads(raw) :
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


# ── earnings_assumptions ─────────────────────────────────────────────────────

def upsert_earnings_assumptions(
    ticker: str,
    fiscal_year: int,
    fiscal_quarter: int,
    *,
    as_of: str | None = None,
    source: str | None = None,
    source_ref: str | None = None,
    period_label: str | None = None,
    guidance: list | None = None,
    segments: list | None = None,
    margins: list | None = None,
    kpis: list | None = None,
    capital_allocation: list | None = None,
    one_offs: list | None = None,
    quotes: list | None = None,
    model_used: str | None = None,
) -> None:
    ensure_assumption_tables()
    db.execute(
        """
        INSERT INTO earnings_assumptions
            (ticker, fiscal_year, fiscal_quarter, as_of, source, source_ref,
             period_label, guidance_json, segments_json, margins_json,
             kpis_json, capital_allocation_json, one_offs_json, quotes_json,
             extracted_at, model_used)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(ticker, fiscal_year, fiscal_quarter) DO UPDATE SET
            as_of=excluded.as_of,
            source=excluded.source,
            source_ref=excluded.source_ref,
            period_label=excluded.period_label,
            guidance_json=excluded.guidance_json,
            segments_json=excluded.segments_json,
            margins_json=excluded.margins_json,
            kpis_json=excluded.kpis_json,
            capital_allocation_json=excluded.capital_allocation_json,
            one_offs_json=excluded.one_offs_json,
            quotes_json=excluded.quotes_json,
            extracted_at=excluded.extracted_at,
            model_used=excluded.model_used
        """,
        [ticker.upper(), int(fiscal_year), int(fiscal_quarter), as_of,
         source, source_ref, period_label,
         _dumps(guidance), _dumps(segments), _dumps(margins),
         _dumps(kpis), _dumps(capital_allocation), _dumps(one_offs),
         _dumps(quotes), _now(), model_used],
    )


def get_earnings_assumptions(ticker: str, limit: int = 8) -> list[dict]:
    """Stored quarters for a ticker, newest (fy, fq) first."""
    ensure_assumption_tables()
    rows = db.query(
        """
        SELECT ticker, fiscal_year, fiscal_quarter, as_of, source,
               source_ref, period_label, guidance_json, segments_json,
               margins_json, kpis_json, capital_allocation_json,
               one_offs_json, quotes_json, extracted_at, model_used
        FROM earnings_assumptions WHERE ticker = ?
        ORDER BY fiscal_year DESC, fiscal_quarter DESC LIMIT ?
        """,
        [ticker.upper(), limit],
    )
    out = []
    for r in rows:
        out.append({
            "ticker": r["ticker"],
            "fiscal_year": r["fiscal_year"],
            "fiscal_quarter": r["fiscal_quarter"],
            "as_of": r["as_of"],
            "source": r["source"],
            "source_ref": r["source_ref"],
            "period_label": r["period_label"],
            "guidance": _loads(r["guidance_json"]) or [],
            "segments": _loads(r["segments_json"]) or [],
            "margins": _loads(r["margins_json"]) or [],
            "kpis": _loads(r["kpis_json"]) or [],
            "capital_allocation": _loads(r["capital_allocation_json"]) or [],
            "one_offs": _loads(r["one_offs_json"]) or [],
            "quotes": _loads(r["quotes_json"]) or [],
            "extracted_at": r["extracted_at"],
            "model_used": r["model_used"],
        })
    return out


def get_latest_earnings_assumptions(ticker: str) -> Optional[dict]:
    rows = get_earnings_assumptions(ticker, limit=1)
    return rows[0] if rows else None


def get_stored_quarter(ticker: str) -> Optional[tuple]:
    """(fiscal_year, fiscal_quarter) of the newest stored row, else None —
    the refresh trigger compares against the latest REPORTED quarter."""
    row = get_latest_earnings_assumptions(ticker)
    if not row:
        return None
    return (row["fiscal_year"], row["fiscal_quarter"])


# ── analyst_reports ──────────────────────────────────────────────────────────

def upsert_analyst_report(
    ticker: str,
    content_hash: str,
    *,
    house: str | None = None,
    analyst: str | None = None,
    report_date: str | None = None,
    rating: str | None = None,
    price_target: float | None = None,
    price_target_currency: str | None = None,
    pt_methodology=None,
    estimates=None,
    house_vs_consensus=None,
    scenarios=None,
    thesis=None,
    revisions=None,
    doc_path: str | None = None,
    drive_file_id: str | None = None,
    source_url: str | None = None,
    ai_input_allowed: bool = False,
    model_used: str | None = None,
) -> None:
    ensure_assumption_tables()
    db.execute(
        """
        INSERT INTO analyst_reports
            (ticker, content_hash, house, analyst, report_date, rating,
             price_target, price_target_currency, pt_methodology_json,
             estimates_json, house_vs_consensus_json, scenarios_json,
             thesis_json, revisions_json, doc_path, drive_file_id,
             source_url, ai_input_allowed, extracted_at, model_used)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(ticker, content_hash) DO UPDATE SET
            house=excluded.house,
            analyst=excluded.analyst,
            report_date=excluded.report_date,
            rating=excluded.rating,
            price_target=excluded.price_target,
            price_target_currency=excluded.price_target_currency,
            pt_methodology_json=excluded.pt_methodology_json,
            estimates_json=excluded.estimates_json,
            house_vs_consensus_json=excluded.house_vs_consensus_json,
            scenarios_json=excluded.scenarios_json,
            thesis_json=excluded.thesis_json,
            revisions_json=excluded.revisions_json,
            doc_path=excluded.doc_path,
            drive_file_id=excluded.drive_file_id,
            source_url=excluded.source_url,
            ai_input_allowed=excluded.ai_input_allowed,
            extracted_at=excluded.extracted_at,
            model_used=excluded.model_used
        """,
        [ticker.upper(), content_hash, house, analyst, report_date, rating,
         price_target, price_target_currency, _dumps(pt_methodology),
         _dumps(estimates), _dumps(house_vs_consensus), _dumps(scenarios),
         _dumps(thesis), _dumps(revisions), doc_path, drive_file_id,
         source_url, 1 if ai_input_allowed else 0, _now(), model_used],
    )


def get_analyst_report_by_hash(ticker: str, content_hash: str) -> Optional[dict]:
    ensure_assumption_tables()
    r = db.query_one(
        "SELECT * FROM analyst_reports WHERE ticker = ? AND content_hash = ?",
        [ticker.upper(), content_hash],
    )
    return _analyst_row(r) if r else None


def get_analyst_reports(ticker: str, limit: int = 10) -> list[dict]:
    ensure_assumption_tables()
    rows = db.query(
        """
        SELECT * FROM analyst_reports WHERE ticker = ?
        ORDER BY COALESCE(report_date, '') DESC, extracted_at DESC LIMIT ?
        """,
        [ticker.upper(), limit],
    )
    return [_analyst_row(r) for r in rows]


def _analyst_row(r) -> dict:
    return {
        "ticker": r["ticker"],
        "content_hash": r["content_hash"],
        "house": r["house"],
        "analyst": r["analyst"],
        "report_date": r["report_date"],
        "rating": r["rating"],
        "price_target": r["price_target"],
        "price_target_currency": r["price_target_currency"],
        "pt_methodology": _loads(r["pt_methodology_json"]),
        "estimates": _loads(r["estimates_json"]) or [],
        "house_vs_consensus": _loads(r["house_vs_consensus_json"]) or [],
        "scenarios": _loads(r["scenarios_json"]) or [],
        "thesis": _loads(r["thesis_json"]) or {},
        "revisions": _loads(r["revisions_json"]) or [],
        "doc_path": r["doc_path"],
        "drive_file_id": r["drive_file_id"],
        "source_url": r["source_url"],
        "ai_input_allowed": bool(r["ai_input_allowed"]),
        "extracted_at": r["extracted_at"],
        "model_used": r["model_used"],
    }


def set_analyst_report_allowance(ticker: str, content_hash: str,
                                 allowed: bool) -> bool:
    """Flip the per-document compliance gate. Returns True if a row changed."""
    ensure_assumption_tables()
    n = db.execute(
        "UPDATE analyst_reports SET ai_input_allowed = ? "
        "WHERE ticker = ? AND content_hash = ?",
        [1 if allowed else 0, ticker.upper(), content_hash],
    )
    return n > 0


# ── assumption_versions (R3 recursion data) ─────────────────────────────────

def append_assumption_versions(rows: list[dict]) -> int:
    """Append version rows (never update — the trajectory is the point).

    Each row: {ticker, source, fiscal_year?, fiscal_quarter?, field_key,
    new_value, prior_value_stated?, direction?, doc_ref?}.
    Duplicate (ticker, source, field_key, new_value, prior_value_stated,
    at-day) is skipped so repeated ingests stay idempotent.
    """
    if not rows:
        return 0
    ensure_assumption_tables()
    added = 0
    for row in rows:
        if not row.get("field_key"):
            continue
        at = _now()
        dupe = db.query_one(
            """
            SELECT id FROM assumption_versions
            WHERE ticker = ? AND source = ? AND field_key = ?
              AND COALESCE(new_value, '') = COALESCE(?, '')
              AND COALESCE(prior_value_stated, '') = COALESCE(?, '')
              AND SUBSTR(at, 1, 10) = SUBSTR(?, 1, 10)
            """,
            [row["ticker"].upper(), row.get("source") or "",
             row["field_key"],
             row.get("new_value"), row.get("prior_value_stated"), at],
        )
        if dupe:
            continue
        db.execute(
            """
            INSERT INTO assumption_versions
                (id, ticker, source, fiscal_year, fiscal_quarter, field_key,
                 new_value, prior_value_stated, direction, doc_ref, at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            [uuid.uuid4().hex, row["ticker"].upper(),
             row.get("source") or "", row.get("fiscal_year"),
             row.get("fiscal_quarter"), row["field_key"],
             row.get("new_value"), row.get("prior_value_stated"),
             row.get("direction"), row.get("doc_ref"), at],
        )
        added += 1
    return added


def get_assumption_versions(ticker: str, field_key: str | None = None,
                            limit: int = 50) -> list[dict]:
    ensure_assumption_tables()
    sql = "SELECT * FROM assumption_versions WHERE ticker = ?"
    params: list = [ticker.upper()]
    if field_key:
        sql += " AND field_key = ?"
        params.append(field_key)
    # rowid tie-break exists only in sqlite (same-second writes: _now()
    # has 1 s resolution); PG orders by at alone — same-second ties are
    # display-order-only and harmless there.
    sql += " ORDER BY at DESC, rowid DESC LIMIT ?" if not db.is_postgres() \
        else " ORDER BY at DESC LIMIT ?"
    params.append(limit)
    return [dict(r) if not isinstance(r, dict) else r for r in db.query(sql, params)]


# ── assumption_challenges (R3) ───────────────────────────────────────────────

def raise_challenge(ticker: str, field_key: str, anomaly_type: str,
                    evidence: str) -> str:
    """Open a challenge unless an identical OPEN one already exists."""
    ensure_assumption_tables()
    dupe = db.query_one(
        "SELECT id FROM assumption_challenges WHERE ticker = ? AND field_key = ? "
        "AND anomaly_type = ? AND status = 'open'",
        [ticker.upper(), field_key, anomaly_type],
    )
    if dupe:
        return dupe["id"]
    cid = uuid.uuid4().hex
    db.execute(
        "INSERT INTO assumption_challenges "
        "(id, ticker, raised_at, field_key, anomaly_type, evidence, status) "
        "VALUES (?,?,?,?,?,?, 'open')",
        [cid, ticker.upper(), _now(), field_key, anomaly_type, evidence],
    )
    return cid


def get_open_challenges(ticker: str | None = None) -> list[dict]:
    ensure_assumption_tables()
    if ticker:
        rows = db.query(
            "SELECT * FROM assumption_challenges WHERE ticker = ? "
            "AND status = 'open' ORDER BY raised_at DESC",
            [ticker.upper()],
        )
    else:
        rows = db.query(
            "SELECT * FROM assumption_challenges WHERE status = 'open' "
            "ORDER BY raised_at DESC LIMIT 200", [])
    return [dict(r) if not isinstance(r, dict) else r for r in rows]


def resolve_challenge(challenge_id: str, status: str,
                      resolution: str | None = None,
                      outcome_note: str | None = None) -> None:
    ensure_assumption_tables()
    db.execute(
        "UPDATE assumption_challenges SET status = ?, resolution = ?, "
        "outcome_note = ?, resolved_at = ? WHERE id = ?",
        [status, resolution, outcome_note, _now(), challenge_id],
    )


def annotate_challenge(challenge_id: str, note: str) -> None:
    """Append an analyst note (e.g. the steward's LLM reading) WITHOUT
    changing status — the challenge stays open until facts resolve it."""
    ensure_assumption_tables()
    r = db.query_one(
        "SELECT outcome_note FROM assumption_challenges WHERE id = ?",
        [challenge_id],
    )
    if r is None:
        return
    prior = r["outcome_note"] or ""
    merged = ((prior + "\n") if prior else "") + note
    db.execute(
        "UPDATE assumption_challenges SET outcome_note = ? WHERE id = ?",
        [merged[:4000], challenge_id],
    )


# ── assumption_scorecard (R3) ────────────────────────────────────────────────

def record_scorecard(ticker: str, source: str, field_key: str,
                     fiscal_year: int, fiscal_quarter: int,
                     predicted: str, actual: str,
                     in_range: bool, magnitude: float | None = None) -> None:
    ensure_assumption_tables()
    db.execute(
        """
        INSERT INTO assumption_scorecard
            (ticker, source, field_key, fiscal_year, fiscal_quarter,
             predicted, actual, in_range, magnitude, scored_at)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(ticker, source, field_key, fiscal_year, fiscal_quarter)
        DO UPDATE SET predicted=excluded.predicted, actual=excluded.actual,
            in_range=excluded.in_range, magnitude=excluded.magnitude,
            scored_at=excluded.scored_at
        """,
        [ticker.upper(), source, field_key, int(fiscal_year),
         int(fiscal_quarter), predicted, actual,
         1 if in_range else 0, magnitude, _now()],
    )


def get_scorecard_summary(ticker: str, source: str | None = None) -> dict:
    """Hit-rate per source: {source: {hits, misses, hit_rate}}."""
    ensure_assumption_tables()
    sql = "SELECT source, in_range FROM assumption_scorecard WHERE ticker = ?"
    params: list = [ticker.upper()]
    if source:
        sql += " AND source = ?"
        params.append(source)
    tally: dict[str, dict] = {}
    for r in db.query(sql, params):
        s = r["source"]
        t = tally.setdefault(s, {"hits": 0, "misses": 0})
        if r["in_range"]:
            t["hits"] += 1
        else:
            t["misses"] += 1
    for t in tally.values():
        n = t["hits"] + t["misses"]
        t["hit_rate"] = round(t["hits"] / n, 3) if n else None
    return tally


# ── Transcript call signals (qualitative) ───────────────────────────────────
#
# Kept apart from earnings_assumptions on purpose: these readings feed the
# narrative and R3's challenge loop, and must never move an intrinsic value
# on their own. Tone and Q&A pressure exist only in a transcript — no press
# release or filing carries them — so this is the one store that would be
# empty without the FMP transcript channel.

def upsert_transcript_signals(
    ticker: str,
    fiscal_year: int,
    fiscal_quarter: int,
    *,
    as_of: str | None = None,
    tone_shift: str | None = None,
    qa_pressure: list | None = None,
    new_risks: list | None = None,
    strategic_pivots: list | None = None,
    regulatory: list | None = None,
    speakers: list | None = None,
    industry_label: str | None = None,
    model_used: str | None = None,
) -> None:
    ensure_assumption_tables()
    db.execute(
        """
        INSERT INTO transcript_signals
            (ticker, fiscal_year, fiscal_quarter, as_of, tone_shift,
             qa_pressure_json, new_risks_json, strategic_pivots_json,
             regulatory_json, speakers_json, industry_label, extracted_at,
             model_used)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(ticker, fiscal_year, fiscal_quarter) DO UPDATE SET
            as_of=excluded.as_of,
            tone_shift=excluded.tone_shift,
            qa_pressure_json=excluded.qa_pressure_json,
            new_risks_json=excluded.new_risks_json,
            strategic_pivots_json=excluded.strategic_pivots_json,
            regulatory_json=excluded.regulatory_json,
            speakers_json=excluded.speakers_json,
            industry_label=excluded.industry_label,
            extracted_at=excluded.extracted_at,
            model_used=excluded.model_used
        """,
        [ticker.upper(), int(fiscal_year), int(fiscal_quarter), as_of,
         (tone_shift or "")[:600], _dumps(qa_pressure), _dumps(new_risks),
         _dumps(strategic_pivots), _dumps(regulatory), _dumps(speakers),
         (industry_label or "")[:120], _now(), model_used],
    )


def get_transcript_signals(ticker: str, limit: int = 4) -> list[dict]:
    """Newest-first call signals for a ticker."""
    ensure_assumption_tables()
    rows = db.query(
        "SELECT * FROM transcript_signals WHERE ticker = ? "
        "ORDER BY fiscal_year DESC, fiscal_quarter DESC LIMIT ?",
        [ticker.upper(), int(limit)],
    )
    out = []
    for r in rows or []:
        out.append({
            "ticker": r["ticker"],
            "fiscal_year": r["fiscal_year"],
            "fiscal_quarter": r["fiscal_quarter"],
            "as_of": r["as_of"],
            "tone_shift": r["tone_shift"] or "",
            "qa_pressure": _loads(r["qa_pressure_json"]) or [],
            "new_risks": _loads(r["new_risks_json"]) or [],
            "strategic_pivots": _loads(r["strategic_pivots_json"]) or [],
            "regulatory": _loads(r["regulatory_json"]) or [],
            "speakers": _loads(r["speakers_json"]) or [],
            "industry_label": r["industry_label"] or "",
            "model_used": r["model_used"],
        })
    return out


def get_latest_transcript_signals(ticker: str) -> "Optional[dict]":
    rows = get_transcript_signals(ticker, limit=1)
    return rows[0] if rows else None
