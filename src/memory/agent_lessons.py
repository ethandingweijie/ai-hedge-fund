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
import math
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

# ── Self-learning loop 7 (2026-10-04): profile-level gaps and prior-miss retrieval ──
# A same-ticker INCORRECT is rare (agent_lessons stood at zero rows in production); a
# profile that keeps missing in the same direction is the pattern worth a lesson. Both
# read matured PRICE labels only -- never the consensus label -- and the agent's own,
# rated, original runs only (run_features' exclusions).
SCOPES = ("ticker", "profile")
PROFILE_GAP_MIN_RUNS = 3
PROFILE_GAP_PT_LOG_ERR = 0.22314355          # ln(1.25): a median miss above 25%
PROFILE_GAP_BIAS_SHARE = 0.5                 # or shared_method_bias in half the runs
PROFILE_LESSON_COOLDOWN_DAYS = 28
PRIOR_MISSES_K = 3
_PX_PREFERENCE = ("px_365d", "px_180d", "px_90d", "px_30d")

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
    scope         TEXT NOT NULL DEFAULT 'ticker',
    scope_key     TEXT,
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
        # Self-learning loop 7 (2026-10-04): a lesson's scope -- 'ticker' (M1, the default
        # every pre-existing row keeps) or 'profile' (scope_key "<market>|<profile>").
        _db.add_column_if_missing("agent_lessons", "scope", "TEXT NOT NULL DEFAULT 'ticker'")
        _db.add_column_if_missing("agent_lessons", "scope_key", "TEXT")
        _tables_ready_key = key
    except Exception as exc:
        # Concurrent CREATE TABLE IF NOT EXISTS races are harmless; anything
        # persistent surfaces loudly on the first real query.
        logger.warning("agent_lessons _ensure_table: %s", exc)


def lessons_enabled() -> bool:
    return os.environ.get("AGENT_LESSONS", "true").strip().lower() not in (
        "0", "false", "no", "off", "")


def profile_lessons_enabled() -> bool:
    return lessons_enabled() and os.environ.get("PROFILE_LESSONS", "true").strip().lower() not in (
        "0", "false", "no", "off", "")


def prior_misses_enabled() -> bool:
    return os.environ.get("PRIOR_MISSES", "true").strip().lower() not in (
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
    recap_text = ""
    if prior_recap:
        recap_text = (
            f"Report recap ({str(prior_recap.get('run_at') or '')[:10]}): "
            f"{(prior_recap.get('recap_text') or '')[:500]}\n"
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
    return _post_mortem(human, str((gap or {}).get("ticker")))


def distill_profile_lessons(gap: dict) -> list[dict]:
    """The profile-level post-mortem (loop 7): the same distiller over a cell's matured
    misses. Every lesson it returns is general to the profile, never to one ticker."""
    worst = "\n".join(
        f"  - {w['ticker']} {w['run_date']}: target {w['pt']:.2f} vs {w['label']:.2f} at {w['horizon']} "
        f"({w['miss_pct']:+.0f}%); cause {w.get('cause') or 'n/a'}"
        for w in (gap.get("worst") or [])[:5])
    shares = ", ".join(f"{k} {v:.0%}" for k, v in sorted((gap.get("cause_shares") or {}).items()))
    human = (
        f"Profile: {gap.get('profile')} ({gap.get('market')} market)\n"
        f"{gap.get('n')} matured agent runs; median target miss {gap.get('median_miss_pct'):+.0f}% "
        f"(signed: positive = the target was above what the price did)\n"
        f"Attribution causes across the runs: {shares or 'n/a'}\n"
        f"Worst misses:\n{worst}\n\n"
        "Distil 1-3 lessons for the valuation agents about THIS PROFILE as a class -- the "
        "method, weight, growth or margin assumption that keeps missing -- not about any one "
        "ticker. Each <=200 chars and applicable on the next run of a name in this profile. "
        "If the misses look like market noise with no systematic error, return zero lessons.\n"
        "Return a JSON object: {\"lessons\": [{\"agent_key\": \"dcf_engine\"|\"sotp_extractor\", "
        "\"lesson\": \"...\", \"general\": true}]}"
    )
    out = _post_mortem(human, f"{gap.get('market')}|{gap.get('profile')}")
    for ls in out:
        ls["general"] = True
    return out


def _post_mortem(human: str, who: str) -> list[dict]:
    """The one fast-tier LLM call both distillers share. [] on any failure."""
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

        system = (
            "You run post-mortems on equity research misses and distil "
            "reusable lessons for the valuation agents. Be concrete and "
            "mechanistic — name the wrong assumption or method, not "
            "platitudes like 'be more careful'. Each lesson must be an "
            "instruction an agent can apply on its next run. Respond in "
            "JSON format."
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
        logger.warning("[lessons] distill failed for %s: %s", who, exc)
        return []



# ── Self-learning loop 7: profile-level gaps and prior-miss retrieval ────────

def _profile_rows(market: str, profile: str) -> list[dict]:
    """One row per matured agent run in the (market, profile) cell: the longest PRICE
    horizon labelled, its signed target miss (the IV miss when no target), never consensus
    and never a carried-override, unrated or cache-copy run."""
    try:
        from src.memory import run_features as rf
        from src.memory import valuation_outcomes as vo
        rf._ensure_tables()
        vo._ensure_tables()
        rows = _db.query(
            "SELECT f.run_id, f.ticker, f.run_date, f.pt_12m, f.iv_base, o.horizon, o.label_value, "
            "o.pt_log_err, o.iv_log_err FROM run_features f "
            "JOIN valuation_outcomes o ON o.run_id = f.run_id AND o.ticker = f.ticker "
            "WHERE f.market = ? AND f.profile = ? AND o.horizon != ? AND " + rf.AGENT_ONLY_WHERE,
            [market, profile, vo.CONSENSUS])
    except Exception as exc:
        logger.warning("[lessons] profile rows for %s|%s failed: %s", market, profile, exc)
        return []
    rank = {h: i for i, h in enumerate(_PX_PREFERENCE)}
    best: dict[tuple, dict] = {}
    for r in rows:
        r = dict(r)
        if r["horizon"] not in rank:
            continue
        err = r["pt_log_err"] if r["pt_log_err"] is not None else r["iv_log_err"]
        if err is None:
            continue
        key = (r["run_id"], r["ticker"])
        if key not in best or rank[r["horizon"]] < rank[best[key]["horizon"]]:
            best[key] = {"run_id": r["run_id"], "ticker": r["ticker"], "run_date": r["run_date"],
                         "pt": r["pt_12m"] if r["pt_12m"] is not None else r["iv_base"],
                         "label": r["label_value"], "horizon": r["horizon"], "err": float(err),
                         "miss_pct": round((math.exp(float(err)) - 1) * 100, 1)}
    return list(best.values())


def _attach_causes(rows: list[dict]) -> None:
    """B3's attribution cause per run, when the archived dcf_range can be read."""
    if not rows:
        return
    try:
        from src.memory import valuation_attribution as va
        blobs = va._load_dcf({(r["run_id"], r["ticker"]) for r in rows})
        for r in rows:
            dr = blobs.get((r["run_id"], r["ticker"]))
            att = va.attribute_run(dr, r["label"]) if dr else None
            r["cause"] = (att or {}).get("cause")
    except Exception as exc:
        logger.debug("[lessons] attribution unavailable: %s", exc)


def detect_profile_gap(market: str, profile: str) -> Optional[dict]:
    """A (market, profile) cell that keeps missing: at least PROFILE_GAP_MIN_RUNS matured
    agent runs and either a median |target miss| above 25% or shared_method_bias as the
    attribution cause in at least half of them. None otherwise."""
    if not market or not profile:
        return None
    rows = _profile_rows(market, profile)
    if len(rows) < PROFILE_GAP_MIN_RUNS:
        return None
    _attach_causes(rows)
    errs = sorted(r["err"] for r in rows)
    med_abs = sorted(abs(e) for e in errs)[len(errs) // 2]
    med_signed = errs[len(errs) // 2]
    causes = [r.get("cause") for r in rows if r.get("cause")]
    shares = {c: round(causes.count(c) / len(causes), 3) for c in set(causes)} if causes else {}
    reasons = []
    if med_abs > PROFILE_GAP_PT_LOG_ERR:
        reasons.append(f"median target miss {(math.exp(med_abs) - 1) * 100:.0f}% over {len(rows)} matured runs")
    if shares.get("shared_method_bias", 0.0) >= PROFILE_GAP_BIAS_SHARE:
        reasons.append(f"every weighted method missed the same way in {shares['shared_method_bias']:.0%} of runs")
    if not reasons:
        return None
    worst = sorted(rows, key=lambda r: -abs(r["err"]))
    return {"market": market, "profile": profile, "n": len(rows),
            "median_miss_pct": round((math.exp(med_signed) - 1) * 100, 1),
            "median_abs_miss_pct": round((math.exp(med_abs) - 1) * 100, 1),
            "cause_shares": shares, "worst": worst[:5], "gap_reason": "; ".join(reasons),
            "run_id": None, "ticker": None}


def _recent_profile_lesson(scope_key: str, days: int = PROFILE_LESSON_COOLDOWN_DAYS) -> bool:
    try:
        _ensure_table()
        row = _db.query_one(
            "SELECT created_at FROM agent_lessons WHERE scope = 'profile' AND scope_key = ? "
            "ORDER BY created_at DESC LIMIT 1", [scope_key])
        if not row:
            return False
        made = datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00"))
        if made.tzinfo is None:
            made = made.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - made).days < days
    except Exception:
        return False


def maybe_generate_profile_lessons(market: str, profile: str) -> list[dict]:
    """Phase 2.8b, once per (market, profile) in a run: detect the cell's gap, distil and
    save profile-scoped lessons, at most once per cooldown. Never raises."""
    if not profile_lessons_enabled():
        return []
    try:
        scope_key = f"{market}|{profile}"
        if _recent_profile_lesson(scope_key):
            return []
        gap = detect_profile_gap(market, profile)
        if not gap:
            return []
        lessons = distill_profile_lessons(gap)
        if not lessons:
            return []
        evidence_gap = {"run_id": None, "run_at": None, "final_action": None, "outcome": None,
                        "pct_change": gap["median_miss_pct"], "calibration_error": None,
                        "gap_reason": gap["gap_reason"], "ticker": None}
        n = save_lessons(lessons, evidence_gap, scope="profile", scope_key=scope_key)
        if n:
            print(f"  [lessons] {scope_key}: saved {n} profile lesson(s) ({gap['gap_reason']})")
        return lessons
    except Exception as exc:
        logger.warning("[lessons] profile lessons failed for %s|%s: %s", market, profile, exc)
        return []


def retrieve_prior_misses(profile: Optional[str], market: Optional[str], k: int = PRIOR_MISSES_K) -> list[str]:
    """The k largest matured target misses in the (market, profile) cell, one line each
    with B3's cause, for the prompts that reason about a new name in the profile.
    Prompt-append only; [] when disabled or nothing has matured."""
    if not prior_misses_enabled() or not profile or not market or k <= 0:
        return []
    try:
        rows = _profile_rows(market, profile)
        if not rows:
            return []
        rows = sorted(rows, key=lambda r: -abs(r["err"]))[:k]
        _attach_causes(rows)
        return [f"{r['ticker']} {r['run_date']}: target {r['pt']:.2f} vs {r['label']:.2f} at {r['horizon']} "
                f"({r['miss_pct']:+.0f}%); cause {r.get('cause') or 'n/a'}" for r in rows]
    except Exception as exc:
        logger.warning("[lessons] prior misses for %s|%s failed: %s", market, profile, exc)
        return []


# ── Storage ──────────────────────────────────────────────────────────────────

_SAVE_SQL = """
INSERT INTO agent_lessons
    (lesson_id, lesson_hash, agent_key, ticker, run_id, lesson,
     evidence_json, active, created_at, scope, scope_key)
VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
ON CONFLICT(agent_key, lesson_hash) DO UPDATE SET
    active     = 1,
    run_id     = excluded.run_id,
    evidence_json = excluded.evidence_json,
    created_at = excluded.created_at,
    scope      = excluded.scope,
    scope_key  = excluded.scope_key
"""


def _enforce_cap(agent_key: str, scope: str = "ticker") -> None:
    """Keep at most _MAX_ACTIVE_PER_AGENT active lessons per (agent_key, scope) —
    newest win, the oldest overflow rows are deactivated (not deleted). Profile
    lessons never evict ticker lessons, and the reverse."""
    try:
        rows = _db.query(
            "SELECT lesson_id FROM agent_lessons "
            "WHERE agent_key = ? AND active = 1 AND scope = ? "
            "ORDER BY created_at DESC, lesson_id DESC",
            [agent_key, scope],
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
    *,
    scope: str = "ticker",
    scope_key: Optional[str] = None,
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
                scope if scope in SCOPES else "ticker",
                scope_key,
            ])
            touched_keys.add(agent_key)
            written += 1
        for agent_key in touched_keys:
            _enforce_cap(agent_key, scope if scope in SCOPES else "ticker")
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

def get_active_lessons(agent_key: str, limit: int = _MAX_ACTIVE_PER_AGENT, *,
                       profile: Optional[str] = None, market: Optional[str] = None) -> list[str]:
    """Active lessons for one agent, most recent first (<=limit x 200 chars): the
    ticker-scope lessons (M1) and, when a profile and market are given, that cell's
    profile-scope lessons (loop 7), still at most `limit` in total.
    Returns [] on any failure or when the kill switch is off — ingestion is
    pure prompt-append and must degrade to nothing."""
    if not lessons_enabled() or agent_key not in AGENT_KEYS:
        return []
    try:
        _ensure_table()
        if profile and market and profile_lessons_enabled():
            rows = _db.query(
                "SELECT lesson FROM agent_lessons "
                "WHERE agent_key = ? AND active = 1 "
                "AND (scope = 'ticker' OR (scope = 'profile' AND scope_key = ?)) "
                "ORDER BY created_at DESC, lesson_id DESC LIMIT ?",
                [agent_key, f"{market}|{profile}", limit],
            )
        else:
            rows = _db.query(
                "SELECT lesson FROM agent_lessons "
                "WHERE agent_key = ? AND active = 1 AND scope = 'ticker' "
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
        sql = ("SELECT lesson_id, agent_key, ticker, run_id, lesson, active, created_at, "
               "scope, scope_key FROM agent_lessons")
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
