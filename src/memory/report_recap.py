"""
src/memory/report_recap.py
==========================
M1 — report recap layer: per-ticker summaries of completed reports.

The pipeline archives every run (runs/ticker_signals + web_runs), but all
recency reuse was phase-level (get_phase_cache / get_recent_research). The
final report itself — decision, thesis, price targets, PM rationale — was
never summarised and never fed forward, so a second run on the same ticker
started from amnesia. This module closes that loop:

  build_and_save_recap()  — called after the final web_runs save; extracts
                            structured fields from the just-completed run and
                            compresses thesis/assumptions/catalysts via one
                            fast-tier LLM pass (falls back to structured-only
                            on any LLM failure — a recap must never fail a run).
  get_recent_recap()      — mirrors run_archive.get_recent_research: most
                            recent recap for a ticker within RECAP_MAX_AGE_DAYS.
                            Consumed by pipeline phases 2.8/2.9 (prior-report
                            context + freshness delta) and the report UI.

Memory scope: SYSTEM-WIDE and USER-AGNOSTIC, exactly like the archive layer
it rides on (src/memory/run_archive.py has no user_id dimension). Recaps key
on (ticker, run_id) — the freshest recap wins regardless of which user
triggered the run. User scoping exists only in the presentation layer
(web_runs.user_id ownership checks).

Kill switches: REPORT_RECAPS=false disables generation,
RECAP_MAX_AGE_DAYS (default 30) bounds get_recent_recap.

Storage: dual-mode via src.data.db (S1 pattern — ON CONFLICT DO UPDATE
upserts, ? placeholders, _tables_ready_key memo).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from src.data import db as _db

logger = logging.getLogger(__name__)

# Fast-tier model for the compression pass (same default as the complacency
# qual layer — cheap, and DEEP_RESEARCH_API_KEY resolves from env).
RECAP_MODEL_NAME = os.environ.get("RECAP_MODEL", "qwen3.6-plus")

_DDL = """
CREATE TABLE IF NOT EXISTS report_recaps (
    ticker        TEXT NOT NULL,
    run_id        TEXT NOT NULL,
    run_at        TEXT NOT NULL,
    price_at_run  REAL,
    final_action  TEXT,
    signal_score  REAL,
    recap_json    TEXT,
    recap_text    TEXT,
    created_at    TEXT NOT NULL,
    PRIMARY KEY (ticker, run_id)
)
"""

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_report_recaps_ticker_run_at "
    "ON report_recaps(ticker, run_at DESC)",
]

# DDL target memo so CREATE TABLE IF NOT EXISTS runs once per database
# (per-process in PG mode, per-file in SQLite mode).
_tables_ready_key: Optional[tuple] = None


def _ensure_table() -> None:
    global _tables_ready_key
    key = ("pg",) if _db.is_postgres() else ("sqlite", _db.get_db_path())
    if key == _tables_ready_key:
        return
    try:
        _db.execute_script(";".join([_DDL] + _INDEXES))
        _tables_ready_key = key
    except Exception as exc:
        # A concurrent CREATE TABLE IF NOT EXISTS race at boot is harmless;
        # anything persistent surfaces loudly on the first real query.
        logger.warning("report_recap _ensure_table: %s", exc)


def recaps_enabled() -> bool:
    return os.environ.get("REPORT_RECAPS", "true").strip().lower() not in (
        "0", "false", "no", "off", "")


def _max_age_days() -> int:
    try:
        return max(1, int(os.environ.get("RECAP_MAX_AGE_DAYS", "30")))
    except ValueError:
        return 30


# ON CONFLICT form works on BOTH SQLite and Postgres (no INSERT OR REPLACE).
_SAVE_SQL = """
INSERT INTO report_recaps
    (ticker, run_id, run_at, price_at_run, final_action, signal_score,
     recap_json, recap_text, created_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(ticker, run_id) DO UPDATE SET
    run_at       = excluded.run_at,
    price_at_run = excluded.price_at_run,
    final_action = excluded.final_action,
    signal_score = excluded.signal_score,
    recap_json   = excluded.recap_json,
    recap_text   = excluded.recap_text,
    created_at   = excluded.created_at
"""


def save_recap(recap: dict) -> bool:
    """Upsert one recap row. Returns True on success."""
    try:
        _ensure_table()
        _db.execute(_SAVE_SQL, [
            recap["ticker"],
            recap["run_id"],
            recap["run_at"],
            recap.get("price_at_run"),
            recap.get("final_action"),
            recap.get("signal_score"),
            json.dumps(recap.get("recap_json") or {}, ensure_ascii=False),
            recap.get("recap_text") or "",
            datetime.now(timezone.utc).isoformat(),
        ])
        return True
    except Exception as exc:
        logger.warning("[recap] save failed for %s/%s: %s",
                       recap.get("ticker"), recap.get("run_id"), exc)
        return False


def has_recap(ticker: str, run_id: str) -> bool:
    """True when a recap row exists for (ticker, run_id) — used by the
    admin backfill to skip runs that are already covered. Soft-fail: any
    error reads as 'no recap' so the backfill just (re)builds it."""
    if not ticker or not run_id:
        return False
    try:
        _ensure_table()
        row = _db.query_one(
            "SELECT 1 AS one FROM report_recaps WHERE ticker = ? AND run_id = ?",
            [ticker.upper(), run_id],
        )
        return bool(row)
    except Exception:
        return False


def get_recent_recap(
    ticker: str,
    max_age_days: Optional[int] = None,
) -> Optional[dict]:
    """
    Most recent recap for `ticker` within max_age_days (env default 30), or
    None. Shaped like run_archive.get_recent_research's return:

        {run_id, run_at, age_days, price_at_run, final_action, signal_score,
         recap_text, recap_json}
    """
    if not ticker:
        return None
    age_limit = max_age_days if max_age_days is not None else _max_age_days()
    try:
        _ensure_table()
        row = _db.query_one(
            "SELECT run_id, run_at, price_at_run, final_action, signal_score, "
            "recap_json, recap_text "
            "FROM report_recaps WHERE ticker = ? "
            "ORDER BY run_at DESC LIMIT 1",
            [ticker.upper()],
        )
    except Exception as exc:
        logger.warning("[recap] get_recent_recap(%s) failed: %s", ticker, exc)
        return None
    if not row:
        return None

    try:
        run_dt = datetime.fromisoformat(str(row["run_at"]).replace("Z", "+00:00"))
        if run_dt.tzinfo is None:
            run_dt = run_dt.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - run_dt).total_seconds() / 86400.0
    except Exception:
        age_days = 999.0
    if age_days > age_limit:
        return None

    try:
        recap_json = json.loads(row["recap_json"]) if row["recap_json"] else {}
    except Exception:
        recap_json = {}

    return {
        "run_id":       row["run_id"],
        "run_at":       row["run_at"],
        "age_days":     round(age_days, 2),
        "price_at_run": row["price_at_run"],
        "final_action": row["final_action"],
        "signal_score": row["signal_score"],
        "recap_text":   row["recap_text"] or "",
        "recap_json":   recap_json,
    }


# ── Structured extraction (pure — no LLM) ────────────────────────────────────

def _fnum(v) -> Optional[float]:
    try:
        if v is None:
            return None
        f = float(v)
        if f != f or f in (float("inf"), float("-inf")):
            return None
        return f
    except (TypeError, ValueError):
        return None


def extract_structured(run_payload: dict, ticker: str) -> dict:
    """
    Pull recap fields out of a saved run payload (the web_runs
    full_result_json shape: flat data.* keyed by ticker + top-level
    decisions). Pure/deterministic — the LLM compression pass layers on top.
    """
    data = run_payload.get("data") or {}
    decisions = run_payload.get("decisions") or {}
    t = ticker.upper()

    decision = decisions.get(t) or {}
    dcf = (data.get("dcf_range") or {}).get(t) or {}
    scenario = (data.get("scenario_analysis") or {}).get(t) or {}
    power_law = (data.get("power_law_analysis") or {}).get(t) or {}
    trap = (data.get("value_trap_analysis") or {}).get(t) or {}

    base = dcf.get("base") or {}
    bear = dcf.get("bear") or {}
    bull = dcf.get("bull") or {}

    entry_range = decision.get("entry_range") or []

    return {
        "final_action":     decision.get("action"),
        "price_target":     _fnum(decision.get("price_target")),
        "stop_loss":        _fnum(decision.get("stop_loss")),
        "entry_range":      [_fnum(x) for x in entry_range][:2] if entry_range else [],
        "position_size_pct": _fnum(decision.get("position_size_pct")),
        "time_horizon":     decision.get("time_horizon"),
        "rationale":        (decision.get("rationale") or decision.get("reasoning") or "")[:1200],
        "price_at_run":     _fnum(scenario.get("current_price")),
        "dcf_base_iv":      _fnum(base.get("intrinsic_value")),
        "dcf_bear_iv":      _fnum(bear.get("intrinsic_value")),
        "dcf_bull_iv":      _fnum(bull.get("intrinsic_value")),
        "dcf_wacc":         _fnum(dcf.get("wacc")),
        "power_law_score":  _fnum(power_law.get("total_score")),
        "value_trap_verdict": trap.get("overall_verdict"),
        "ev_upside_pct":    _fnum(scenario.get("upside_pct")),
    }


# ── LLM compression pass (fast tier, soft-fail) ─────────────────────────────

def _call_recap_llm(structured: dict, ticker: str) -> Optional[dict]:
    """
    One fast-tier LLM call compressing the run into recap_text + structured
    assumptions/catalysts/risks. Returns None on ANY failure (missing key,
    timeout, malformed output) — callers fall back to structured-only.
    Bypasses call_llm because recap generation has no AgentState (same
    pattern as complacency/qualitative._call_qwen_indicator).
    """
    try:
        from pydantic import BaseModel, Field

        class RecapLLMOutput(BaseModel):
            recap_text: str = Field(
                default="",
                description="<=150 words: what this report concluded and why")
            assumptions: list[str] = Field(default_factory=list)
            catalysts: list[str] = Field(default_factory=list)
            risks: list[str] = Field(default_factory=list)

        from src.llm.models import ModelProvider, get_model
        provider = ModelProvider.ALIBABA
        if RECAP_MODEL_NAME.lower().startswith(("gpt", "o1", "o3", "o4")):
            provider = ModelProvider.OPENAI
        llm = get_model(RECAP_MODEL_NAME, provider, None)
        if llm is None:
            return None

        system = (
            "You summarise a completed equity research report into a compact "
            "recap for the NEXT run on the same ticker. Be concrete: numbers, "
            "dates, named assumptions. No filler, no disclaimers. Respond in "
            "JSON format."
        )
        human = (
            f"Ticker: {ticker}\n"
            f"Decision: {structured.get('final_action')} | "
            f"price target {structured.get('price_target')} | "
            f"horizon {structured.get('time_horizon')}\n"
            f"DCF intrinsic value: base {structured.get('dcf_base_iv')} / "
            f"bear {structured.get('dcf_bear_iv')} / bull {structured.get('dcf_bull_iv')} "
            f"(WACC {structured.get('dcf_wacc')})\n"
            f"Power-law score: {structured.get('power_law_score')}/10 | "
            f"Value trap: {structured.get('value_trap_verdict')} | "
            f"EV upside: {structured.get('ev_upside_pct')}%\n"
            f"PM rationale:\n{structured.get('rationale') or '(none)'}\n\n"
            "Produce:\n"
            "1. recap_text: <=150-word recap of what this report concluded and "
            "the strongest reasons.\n"
            "2. assumptions: 2-5 key quantitative assumptions the next run must "
            "check for change.\n"
            "3. catalysts: events this report was watching for.\n"
            "4. risks: the main risks flagged.\n"
            "Return a JSON object with keys recap_text, assumptions, catalysts, "
            "risks."
        )

        messages = [("system", system), ("human", human)]
        try:
            from src.research_ideas.complacency import qwen_throttle
            qwen_throttle.acquire(weight=1.0)
        except Exception:
            pass  # throttle is a courtesy; never block recaps on it

        structured_llm = llm.with_structured_output(RecapLLMOutput, method="json_mode")
        try:
            out = structured_llm.invoke(messages)
        except Exception:
            raw = llm.invoke(messages)
            text = raw.content if hasattr(raw, "content") else str(raw)
            start, end = text.find("{"), text.rfind("}")
            if start < 0 or end <= start:
                return None
            out = RecapLLMOutput(**json.loads(text[start:end + 1]))

        return {
            "recap_text": (out.recap_text or "")[:2000],
            "assumptions": [str(a)[:300] for a in (out.assumptions or [])][:8],
            "catalysts":   [str(c)[:300] for c in (out.catalysts or [])][:8],
            "risks":       [str(r)[:300] for r in (out.risks or [])][:8],
        }
    except Exception as exc:
        logger.warning("[recap] LLM compression failed for %s: %s", ticker, exc)
        return None


# ── Top-level build path ─────────────────────────────────────────────────────

def build_recap(run_payload: dict, ticker: str) -> dict:
    """
    Build the recap dict for one ticker from a saved run payload. Always
    returns a usable recap (structured-only fallback when the LLM pass fails).
    """
    structured = extract_structured(run_payload, ticker)
    llm_part = _call_recap_llm(structured, ticker)
    llm_used = llm_part is not None

    recap_json = dict(structured)
    recap_json["assumptions"] = (llm_part or {}).get("assumptions", [])
    recap_json["catalysts"] = (llm_part or {}).get("catalysts", [])
    recap_json["risks"] = (llm_part or {}).get("risks", [])
    recap_json["llm_used"] = llm_used

    if llm_used and (llm_part or {}).get("recap_text"):
        recap_text = llm_part["recap_text"]
    else:
        # Deterministic fallback: headline the structured fields.
        bits = [f"{structured.get('final_action') or 'N/A'}"]
        if structured.get("price_target"):
            bits.append(f"PT {structured['price_target']}")
        if structured.get("dcf_base_iv"):
            bits.append(f"DCF base {structured['dcf_base_iv']}")
        rationale = (structured.get("rationale") or "").strip()
        if rationale:
            bits.append(rationale[:280])
        recap_text = " | ".join(bits)

    data = run_payload.get("data") or {}
    run_at = (
        run_payload.get("run_at")
        or data.get("end_date")
        or datetime.now(timezone.utc).isoformat()
    )

    return {
        "ticker":       ticker.upper(),
        "run_id":       run_payload.get("run_id") or "",
        "run_at":       run_at,
        "price_at_run": structured.get("price_at_run"),
        "final_action": structured.get("final_action"),
        "signal_score": _fnum((run_payload.get("decisions") or {})
                              .get(ticker.upper(), {}).get("signal_score")),
        "recap_json":   recap_json,
        "recap_text":   recap_text,
    }


def build_and_save_recap(
    run_payload: dict,
    ticker: str,
    run_id: Optional[str] = None,
    run_at: Optional[str] = None,
) -> Optional[dict]:
    """
    Full path: build the recap and upsert it. Never raises — returns the
    saved recap dict or None. run_id/run_at override the payload values
    (backfill passes the web_runs row's own values).
    """
    if not recaps_enabled():
        return None
    try:
        recap = build_recap(run_payload, ticker)
        if run_id:
            recap["run_id"] = run_id
        if run_at:
            recap["run_at"] = run_at
        if not recap["run_id"]:
            logger.warning("[recap] no run_id for %s — skipping save", ticker)
            return None
        ok = save_recap(recap)
        if ok:
            print(f"  [recap] {ticker.upper()}: saved recap "
                  f"({recap.get('final_action')}, "
                  f"llm={'yes' if recap['recap_json'].get('llm_used') else 'fallback'})")
        return recap if ok else None
    except Exception as exc:
        logger.warning("[recap] build_and_save failed for %s: %s", ticker, exc)
        return None
