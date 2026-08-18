"""
src/memory/agent_lessons.py
============================
M1 — agent self-improvement: distilled lessons from past valuation misses.

The archive already scores every run's outcome (run_archive.update_outcomes:
±5% CORRECT/INCORRECT/NEUTRAL) and the DCF agent flags a T-1 backward
calibration error when it missed last quarter's actuals by >25%. Before M1
those signals were only aggregated for agent reweighting — the concrete
*reason* a valuation missed was never captured, so the same mistake could
repeat indefinitely.

This module closes that loop:

  detect_gap()             — looks up the ticker's most recent SCORED run;
                             a gap exists when its outcome is INCORRECT or
                             its DCF carried calibration_error.
  distill_lessons()        — one fast-tier post-mortem LLM pass (prior recap
                             + what actually happened + miss direction) that
                             emits 1-3 concrete lessons tagged dcf_engine or
                             sotp_extractor. Returns [] on any failure.
  maybe_generate_lessons() — detect → distill → save, never raises. Called
                             from pipeline phase 2.9 (user-triggered, lazy —
                             nothing runs unattended).
  get_active_lessons()     — ingestion point for the dcf_agent /
                             sotp_extractor prompt builders: most recent
                             first, <=6 lessons x <=200 chars.

Lessons are prompt-append only: the existing hard clamps (bank calibration,
T-1 gate, OE<=0 IV drag) stay exactly as they are.

Memory scope: SYSTEM-WIDE and USER-AGNOSTIC — the agent_lessons table keys
on agent_key only, exactly like the archive layer it reads from. Lessons
accumulate fleet-wide so even low-volume agents clear the minimum-review
bar, and one user's miss improves every later run.

Kill switch: AGENT_LESSONS=false disables detection + generation;
get_active_lessons() then returns [] so ingestion is a no-op too.

Storage: dual-mode via src.data.db (S1 pattern — ON CONFLICT DO UPDATE
upserts, ? placeholders, _tables_ready_key memo). Content-hash dedupe via
UNIQUE(agent_key, lesson_hash); capped at _MAX_ACTIVE_PER_AGENT active rows
per agent_key (oldest deactivated on overflow).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from src.data import db as _db

logger = logging.getLogger(__name__)

AGENT_KEYS = ("dcf_engine", "sotp_extractor")
_MAX_ACTIVE_PER_AGENT = 6
_MAX_LESSON_CHARS = 200

_DDL = """
CREATE TABLE IF NOT EXISTS agent_lessons (
    lesson_id     TEXT PRIMARY KEY,
    lesson_hash   TEXT NOT NULL,
    agent_key     TEXT NOT NULL,
    ticker        TEXT,
    run_id        TEXT,
    lesson        TEXT NOT NULL,
    evidence_json TEXT,
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL,
    UNIQUE(agent_key, lesson_hash)
)
"""

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_agent_lessons_key_active "
    "ON agent_lessons(agent_key, active, created_at DESC)",
]

# DDL target memo so CREATE TABLE IF NOT EXISTS runs once per database.
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
        # Concurrent CREATE TABLE IF NOT EXISTS races are harmless; anything
        # persistent surfaces loudly on the first real query.
        logger.warning("agent_lessons _ensure_table: %s", exc)


def lessons_enabled() -> bool:
    return os.environ.get("AGENT_LESSONS", "true").strip().lower() not in (
        "0", "false", "no", "off", "")


def _lesson_hash(agent_key: str, lesson: str) -> str:
    norm = " ".join(str(lesson).lower().split())
    return hashlib.sha256(f"{agent_key}|{norm}".encode("utf-8")).hexdigest()


# ── Gap detection (recency-triggered, reads the archive) ────────────────────

def detect_gap(ticker: str) -> Optional[dict]:
    """
    The ticker's most recent SCORED run, when it represents a gap:
    outcome INCORRECT (price moved against the call by >5%) or the DCF
    carried a calibration_error. Returns the scored-signal dict (enriched
    with a `gap_reason`) or None when there is no gap — no gap means no
    post-mortem LLM call at all.
    """
    try:
        from src.memory.run_archive import get_last_scored_signal
        sig = get_last_scored_signal(ticker)
    except Exception as exc:
        logger.warning("[lessons] detect_gap(%s) lookup failed: %s", ticker, exc)
        return None
    if not sig:
        return None

    reasons = []
    if sig.get("outcome") == "INCORRECT":
        reasons.append(
            f"outcome INCORRECT ({sig.get('pct_change')}% move vs the "
            f"{sig.get('final_action')} call)"
        )
    if sig.get("calibration_error"):
        reasons.append("DCF T-1 calibration error (>25% off last actuals)")
    if not reasons:
        return None
    sig["gap_reason"] = "; ".join(reasons)
    return sig


# ── Post-mortem distillation (fast tier, soft-fail) ─────────────────────────

def distill_lessons(gap: dict, prior_recap: Optional[dict] = None) -> list[dict]:
    """
    One fast-tier LLM post-mortem over a detected gap. Returns a list of
    {agent_key, lesson, general} dicts (1-3 entries) or [] on ANY failure —
    lesson generation must never break a run. Bypasses call_llm because
    there is no AgentState at phase 2.9 (same pattern as
    report_recap._call_recap_llm).
    """
    try:
        from pydantic import BaseModel, Field

        class LessonOut(BaseModel):
            agent_key: str = Field(
                default="dcf_engine",
                description="Which agent should learn this: 'dcf_engine' or 'sotp_extractor'")
            lesson: str = Field(
                default="",
                description="<=200 chars: the concrete mistake to avoid next time")
            general: bool = Field(
                default=True,
                description="True = applies to all tickers; False = ticker-specific")

        class PostMortem(BaseModel):
            lessons: list[LessonOut] = Field(default_factory=list)

        from src.llm.models import ModelProvider, get_model
        from src.memory.report_recap import RECAP_MODEL_NAME
        provider = ModelProvider.ALIBABA
        if RECAP_MODEL_NAME.lower().startswith(("gpt", "o1", "o3", "o4")):
            provider = ModelProvider.OPENAI
        llm = get_model(RECAP_MODEL_NAME, provider, None)
        if llm is None:
            return []

        recap_text = ""
        if prior_recap:
            recap_text = (
                f"Report recap ({str(prior_recap.get('run_at') or '')[:10]}): "
                f"{(prior_recap.get('recap_text') or '')[:500]}\n"
            )

        system = (
            "You run post-mortems on equity research misses and distil "
            "reusable lessons for the valuation agents. Be concrete and "
            "mechanistic — name the wrong assumption or method, not "
            "platitudes like 'be more careful'. Each lesson must be an "
            "instruction an agent can apply on its next run. Respond in "
            "JSON format."
        )
        human = (
            f"Ticker: {gap.get('ticker')}\n"
            f"Run {str(gap.get('run_at') or '')[:10]}: "
            f"{gap.get('final_action')} call "
            f"(price ${gap.get('price_at_run')} → ${gap.get('price_at_review')}, "
            f"{gap.get('pct_change')}% — scored {gap.get('outcome')})\n"
            f"Gap: {gap.get('gap_reason')}\n"
            f"DCF inputs then: base IV {gap.get('dcf_base_iv')}, "
            f"WACC {gap.get('dcf_wacc')}\n"
            f"{recap_text}"
            f"PM rationale then: {(gap.get('pm_rationale') or '')[:600]}\n\n"
            "Distil 1-3 lessons. For each: which agent owns the mistake "
            "(dcf_engine = valuation/assumptions/WACC/terminal value; "
            "sotp_extractor = mis-read or mis-extracted source figures), "
            "the concrete lesson (<=200 chars), and whether it generalises "
            "to all tickers or is ticker-specific. If the miss was pure "
            "market noise with no agent error, return zero lessons.\n"
            "Return a JSON object: {\"lessons\": [{\"agent_key\": "
            "\"dcf_engine\"|\"sotp_extractor\", \"lesson\": \"...\", "
            "\"general\": true|false}]}"
        )
        messages = [("system", system), ("human", human)]
        try:
            from src.research_ideas.complacency import qwen_throttle
            qwen_throttle.acquire(weight=1.0)
        except Exception:
            pass  # throttle is a courtesy; never block lessons on it

        structured_llm = llm.with_structured_output(PostMortem, method="json_mode")
        try:
            out = structured_llm.invoke(messages)
        except Exception:
            raw = llm.invoke(messages)
            text = raw.content if hasattr(raw, "content") else str(raw)
            start, end = text.find("{"), text.rfind("}")
            if start < 0 or end <= start:
                return []
            out = PostMortem(**json.loads(text[start:end + 1]))

        lessons = []
        for lo in (out.lessons or [])[:3]:
            key = (lo.agent_key or "").strip().lower()
            if key not in AGENT_KEYS:
                continue
            text = (lo.lesson or "").strip()[:_MAX_LESSON_CHARS]
            if not text:
                continue
            lessons.append({
                "agent_key": key,
                "lesson":    text,
                "general":   bool(lo.general),
            })
        return lessons
    except Exception as exc:
        logger.warning("[lessons] distill failed for %s: %s",
                       (gap or {}).get("ticker"), exc)
        return []


# ── Storage ──────────────────────────────────────────────────────────────────

_SAVE_SQL = """
INSERT INTO agent_lessons
    (lesson_id, lesson_hash, agent_key, ticker, run_id, lesson,
     evidence_json, active, created_at)
VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
ON CONFLICT(agent_key, lesson_hash) DO UPDATE SET
    active     = 1,
    run_id     = excluded.run_id,
    evidence_json = excluded.evidence_json,
    created_at = excluded.created_at
"""


def _enforce_cap(agent_key: str) -> None:
    """Keep at most _MAX_ACTIVE_PER_AGENT active lessons per agent_key —
    newest win, the oldest overflow rows are deactivated (not deleted)."""
    try:
        rows = _db.query(
            "SELECT lesson_id FROM agent_lessons "
            "WHERE agent_key = ? AND active = 1 "
            "ORDER BY created_at DESC, lesson_id DESC",
            [agent_key],
        )
        overflow = rows[_MAX_ACTIVE_PER_AGENT:]
        for r in overflow:
            _db.execute(
                "UPDATE agent_lessons SET active = 0 WHERE lesson_id = ?",
                [r["lesson_id"]],
            )
    except Exception as exc:
        logger.warning("[lessons] cap enforcement failed for %s: %s",
                       agent_key, exc)


def save_lessons(
    lessons: list[dict],
    gap: dict,
    general_ticker: Optional[str] = None,
) -> int:
    """Upsert distilled lessons with content-hash dedupe. Returns the number
    of rows written. Never raises."""
    if not lessons:
        return 0
    written = 0
    try:
        _ensure_table()
        now = datetime.now(timezone.utc).isoformat()
        evidence = {
            "run_id":          gap.get("run_id"),
            "run_at":          gap.get("run_at"),
            "final_action":    gap.get("final_action"),
            "outcome":         gap.get("outcome"),
            "pct_change":      gap.get("pct_change"),
            "calibration_error": gap.get("calibration_error"),
            "gap_reason":      gap.get("gap_reason"),
        }
        touched_keys = set()
        for ls in lessons:
            agent_key = ls.get("agent_key")
            text = (ls.get("lesson") or "").strip()[:_MAX_LESSON_CHARS]
            if agent_key not in AGENT_KEYS or not text:
                continue
            ticker = None if ls.get("general") else (general_ticker or gap.get("ticker"))
            _db.execute(_SAVE_SQL, [
                uuid.uuid4().hex,
                _lesson_hash(agent_key, text),
                agent_key,
                ticker.upper() if ticker else None,
                gap.get("run_id"),
                text,
                json.dumps(evidence, ensure_ascii=False),
                now,
            ])
            touched_keys.add(agent_key)
            written += 1
        for agent_key in touched_keys:
            _enforce_cap(agent_key)
    except Exception as exc:
        logger.warning("[lessons] save failed: %s", exc)
    return written


def _lessons_for_run(run_id: str) -> bool:
    """True when this gap's source run already produced lessons — repeat
    runs on the same ticker then skip the post-mortem LLM call entirely."""
    if not run_id:
        return False
    try:
        _ensure_table()
        row = _db.query_one(
            "SELECT lesson_id FROM agent_lessons WHERE run_id = ? LIMIT 1",
            [run_id],
        )
        return bool(row)
    except Exception:
        return False


def maybe_generate_lessons(
    ticker: str,
    prior_recap: Optional[dict] = None,
) -> list[dict]:
    """
    Full path for pipeline phase 2.9: detect a gap in the ticker's last
    scored run; if one exists, distil and save lessons. Never raises —
    returns the saved lessons list (possibly empty). Kill-switch aware.
    """
    if not lessons_enabled():
        return []
    try:
        gap = detect_gap(ticker)
        if not gap:
            return []
        if _lessons_for_run(gap.get("run_id") or ""):
            return []
        lessons = distill_lessons(gap, prior_recap)
        if not lessons:
            return []
        n = save_lessons(lessons, gap, general_ticker=ticker.upper())
        if n:
            print(f"  [lessons] {ticker.upper()}: saved {n} lesson(s) "
                  f"from {gap.get('outcome')} run "
                  f"{str(gap.get('run_id') or '')[:8]} ({gap.get('gap_reason')})")
        return lessons
    except Exception as exc:
        logger.warning("[lessons] maybe_generate failed for %s: %s", ticker, exc)
        return []


# ── Ingestion + admin ────────────────────────────────────────────────────────

def get_active_lessons(agent_key: str, limit: int = _MAX_ACTIVE_PER_AGENT) -> list[str]:
    """Active lessons for one agent, most recent first (<=limit x 200 chars).
    Returns [] on any failure or when the kill switch is off — ingestion is
    pure prompt-append and must degrade to nothing."""
    if not lessons_enabled() or agent_key not in AGENT_KEYS:
        return []
    try:
        _ensure_table()
        rows = _db.query(
            "SELECT lesson FROM agent_lessons "
            "WHERE agent_key = ? AND active = 1 "
            "ORDER BY created_at DESC, lesson_id DESC LIMIT ?",
            [agent_key, limit],
        )
        return [(r["lesson"] or "")[:_MAX_LESSON_CHARS] for r in rows if r["lesson"]]
    except Exception as exc:
        logger.warning("[lessons] get_active_lessons(%s) failed: %s",
                       agent_key, exc)
        return []


def list_lessons(agent_key: Optional[str] = None,
                 include_inactive: bool = False) -> list[dict]:
    """Admin view of the lesson store."""
    try:
        _ensure_table()
        sql = "SELECT lesson_id, agent_key, ticker, run_id, lesson, active, created_at FROM agent_lessons"
        clauses, params = [], []
        if agent_key:
            clauses.append("agent_key = ?")
            params.append(agent_key)
        if not include_inactive:
            clauses.append("active = 1")
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC, lesson_id DESC LIMIT 100"
        rows = _db.query(sql, params)
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.warning("[lessons] list_lessons failed: %s", exc)
        return []


def deactivate_lesson(lesson_id: str) -> bool:
    """Admin: soft-delete one lesson (kept for audit)."""
    try:
        _ensure_table()
        _db.execute(
            "UPDATE agent_lessons SET active = 0 WHERE lesson_id = ?",
            [lesson_id],
        )
        return True
    except Exception as exc:
        logger.warning("[lessons] deactivate failed for %s: %s", lesson_id, exc)
        return False
