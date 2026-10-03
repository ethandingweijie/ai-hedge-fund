"""The user's estimate overrides, persisted (owner, 2026-10-03, interactive valuation agent).

One table on the run archive (SQLite locally, Postgres in production), one active row per run and
ticker. Two readers:

  * the web service applies a run's override on read (estimate_override_service.apply_saved), so
    the page, the PDF and the workbook agree without rewriting the stored run;
  * the valuation pipeline carries the ticker's LATEST active override into the next run
    (`latest_for_ticker`): the user's estimates replace the research's in the guidance block, the
    engine overrides (fade, tax, capex, working capital, terminal ROIC) reach the forecast, and WACC
    or terminal growth replace the engine's. The run's flags say so and the Model Accuracy page lists
    what is carried, with a revoke.

ESTIMATE_OVERRIDE_CARRY=off stops the carry-forward without touching the rows.
"""
from __future__ import annotations

import json
import os
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Optional

from src.data import db

_DDL = """CREATE TABLE IF NOT EXISTS estimate_overrides (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    ticker TEXT NOT NULL,
    user_id INTEGER,
    overrides_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL,
    active INTEGER DEFAULT 1
)"""
_IDX = "CREATE INDEX IF NOT EXISTS idx_estimate_overrides_run ON estimate_overrides(run_id, ticker, active)"
_IDX_T = "CREATE INDEX IF NOT EXISTS idx_estimate_overrides_ticker ON estimate_overrides(ticker, active, created_at)"
_ready_key: Any = None

SCENARIOS = ("bear", "base", "bull")
ENGINE_FIELDS = ("fade_years", "tax_rate", "capex_alpha", "nwc_intensity", "terminal_roic")


def carry_enabled() -> bool:
    return os.environ.get("ESTIMATE_OVERRIDE_CARRY", "on").strip().lower() not in ("off", "0", "false", "no")


def _key(t: str) -> str:
    return (t or "").strip().upper()


def ensure_table() -> None:
    global _ready_key
    k = ("pg",) if db.is_postgres() else ("sqlite", db.get_db_path())
    if k != _ready_key:
        db.ensure_table(_DDL)
        db.execute(_IDX)
        db.execute(_IDX_T)
        _ready_key = k


def _row_to_record(row) -> dict:
    g = row.get if isinstance(row, dict) else (lambda k: row[k])
    return {"id": g("id"), "run_id": g("run_id"), "ticker": g("ticker"), "user_id": g("user_id"), "note": g("note"),
            "created_at": g("created_at"), "overrides": json.loads(g("overrides_json") or "{}"), "result": json.loads(g("result_json") or "{}")}


def get_active(run_id: str, ticker: str) -> Optional[dict]:
    ensure_table()
    row = db.query_one("SELECT * FROM estimate_overrides WHERE run_id = ? AND ticker = ? AND active = 1 ORDER BY created_at DESC LIMIT 1",
                       [run_id, _key(ticker)])
    return _row_to_record(row) if row else None


def list_active(run_id: str) -> list[dict]:
    ensure_table()
    rows = db.query("SELECT * FROM estimate_overrides WHERE run_id = ? AND active = 1 ORDER BY created_at DESC", [run_id])
    seen, out = set(), []
    for r in rows:
        rec = _row_to_record(r)
        if rec["ticker"] not in seen:
            seen.add(rec["ticker"])
            out.append(rec)
    return out


def list_carried() -> list[dict]:
    """The latest active override per ticker: what the next run of each name will carry."""
    ensure_table()
    rows = db.query("SELECT * FROM estimate_overrides WHERE active = 1 ORDER BY created_at DESC", [])
    seen, out = set(), []
    for r in rows:
        rec = _row_to_record(r)
        if rec["ticker"] not in seen:
            seen.add(rec["ticker"])
            out.append({k: rec[k] for k in ("id", "run_id", "ticker", "user_id", "note", "created_at", "overrides")}
                       | {"fields": (rec["result"] or {}).get("fields") or [], "before": (rec["result"] or {}).get("before"),
                          "after": (rec["result"] or {}).get("after")})
    return out


def latest_for_ticker(ticker: str) -> Optional[dict]:
    ensure_table()
    row = db.query_one("SELECT * FROM estimate_overrides WHERE ticker = ? AND active = 1 ORDER BY created_at DESC LIMIT 1", [_key(ticker)])
    return _row_to_record(row) if row else None


def save(run_id: str, ticker: str, user_id: Optional[int], overrides: dict, note: Optional[str], result: dict) -> dict:
    ensure_table()
    t = _key(ticker)
    db.execute("UPDATE estimate_overrides SET active = 0 WHERE run_id = ? AND ticker = ? AND active = 1", [run_id, t])
    rec = {"id": uuid.uuid4().hex, "run_id": run_id, "ticker": t, "user_id": user_id, "note": (note or "").strip()[:500] or None,
           "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "overrides": overrides, "result": result}
    db.execute("INSERT INTO estimate_overrides (id, run_id, ticker, user_id, overrides_json, result_json, note, created_at, active) "
               "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)",
               [rec["id"], run_id, t, user_id, json.dumps(overrides), json.dumps(result, default=str), rec["note"], rec["created_at"]])
    return rec


def clear(run_id: str, ticker: str) -> int:
    ensure_table()
    return db.execute("UPDATE estimate_overrides SET active = 0 WHERE run_id = ? AND ticker = ? AND active = 1", [run_id, _key(ticker)])


def revoke_ticker(ticker: str) -> int:
    """Stop carrying the ticker's estimates forward: every active row for the name is deactivated."""
    ensure_table()
    return db.execute("UPDATE estimate_overrides SET active = 0 WHERE ticker = ? AND active = 1", [_key(ticker)])


def update_result(rec_id: str, result: dict) -> None:
    ensure_table()
    db.execute("UPDATE estimate_overrides SET result_json = ? WHERE id = ?", [json.dumps(result, default=str), rec_id])


# ── carry-forward helpers for the pipeline ───────────────────────────────────

def merge_block(block: Optional[dict], rec: dict) -> dict:
    """The research's guidance block with the user's estimates and medium-term target in place of
    the research's. Where the research gave no block, the user's estimates ARE the block."""
    ov = (rec or {}).get("overrides") or {}
    out = deepcopy(block) if block else {}
    out.setdefault("estimates", {})
    for sc in SCENARIOS:
        out["estimates"].setdefault(sc, {})
        out["estimates"][sc].update((ov.get("scenarios") or {}).get(sc) or {})
    base = out["estimates"]["base"]
    for sc in ("bear", "bull"):
        if out["estimates"][sc].get("revenue_growth_fy1") is None and base.get("revenue_growth_fy1") is not None:
            out["estimates"][sc] = {**base, **out["estimates"][sc]}
    if ov.get("medium_term_target") is not None:
        out["medium_term_target"] = ov["medium_term_target"] or None
    if not out.get("confidence"):
        out["confidence"] = "HIGH"                    # the user's own number clears the channel's gate
    out["user_override"] = {"run_id": rec.get("run_id"), "created_at": rec.get("created_at"), "note": rec.get("note"),
                            "fields": ((rec.get("result") or {}).get("fields")) or [], "id": rec.get("id")}
    return out


def engine_overrides(rec: dict) -> dict:
    sh = ((rec or {}).get("overrides") or {}).get("shared") or {}
    return {k: sh[k] for k in ENGINE_FIELDS if sh.get(k) is not None}


def rate_overrides(rec: dict) -> dict:
    sh = ((rec or {}).get("overrides") or {}).get("shared") or {}
    return {k: sh[k] for k in ("wacc", "tgr") if sh.get(k) is not None}
