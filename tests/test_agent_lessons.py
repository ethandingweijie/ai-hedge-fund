"""
tests/test_agent_lessons.py
===========================
M1 — agent self-improvement (src/memory/agent_lessons.py): gap detection
off the archive's scored outcomes (run_archive.ticker_signals.outcome +
DCF calibration_error), lesson storage (content-hash dedupe, 6-active cap,
deactivation), ingestion ordering, the maybe_generate_lessons orchestrator
(incl. its per-run cost guard), kill switches, and the PG-compatible SQL
shape.

Gap detection reads run_archive via its own SQLite path, so the archive is
pointed at a separate tmp file from the dual-mode DB the lessons table
rides on — mirrors production where both live in the same database.
"""
import inspect
import json

import pytest

from src.data import db as _db


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """Point the dual-mode layer (lessons table) at a fresh tmp SQLite."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("RUN_ARCHIVE_PATH", str(tmp_path / "lessons_dual.db"))
    monkeypatch.delenv("AGENT_LESSONS", raising=False)
    _db.close_all_connections()
    yield
    _db.close_all_connections()


@pytest.fixture()
def lessons(tmp_db):
    from src.memory import agent_lessons
    lessons_mod = agent_lessons
    lessons_mod._tables_ready_key = None  # re-create tables on the tmp DB
    return lessons_mod


@pytest.fixture()
def archive(tmp_path, monkeypatch):
    """Point run_archive's SQLite (runs/ticker_signals) at its own tmp file."""
    import src.memory.run_archive as ra
    monkeypatch.setattr(ra, "DB_PATH", str(tmp_path / "lessons_archive.db"))
    monkeypatch.setattr(ra, "_sqlite_schema_paths", set())
    return ra


def _seed_scored_run(
    archive,
    *,
    run_id: str = "run-gap",
    ticker: str = "CRWD",
    outcome: str = "INCORRECT",
    pct_change: float = -8.4,
    run_at: str = "2026-08-01T00:00:00+00:00",
    calibration_error: bool = False,
    final_action: str = "BUY",
) -> None:
    conn = archive._get_conn()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO runs (run_id, run_at, analysis_date, tickers) "
            "VALUES (?, ?, ?, ?)",
            [run_id, run_at, run_at[:10], json.dumps([ticker])],
        )
        dcf_range = {"calibration_error": True} if calibration_error else {}
        conn.execute(
            "INSERT INTO ticker_signals "
            "(run_id, ticker, final_action, pm_rationale, price_at_run, "
            " price_at_review, pct_change, outcome, dcf_base_iv, dcf_wacc, "
            " dcf_range_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [run_id, ticker.upper(), final_action, " bought the dip", 100.0,
             91.6, pct_change, outcome, 120.0, 0.104, json.dumps(dcf_range)],
        )
        conn.commit()
    finally:
        conn.close()


def _gap(run_id="run-gap", ticker="CRWD") -> dict:
    return {
        "run_id": run_id,
        "run_at": "2026-08-01T00:00:00+00:00",
        "ticker": ticker,
        "final_action": "BUY",
        "outcome": "INCORRECT",
        "pct_change": -8.4,
        "calibration_error": False,
        "gap_reason": "outcome INCORRECT",
    }


# ── Gap detection ─────────────────────────────────────────────────────────────

def test_detect_gap_incorrect_outcome(lessons, archive):
    _seed_scored_run(archive, outcome="INCORRECT")
    gap = lessons.detect_gap("CRWD")
    assert gap is not None
    assert gap["run_id"] == "run-gap"
    assert gap["outcome"] == "INCORRECT"
    assert "INCORRECT" in gap["gap_reason"]


def test_detect_gap_calibration_error_alone(lessons, archive):
    _seed_scored_run(archive, outcome="CORRECT", pct_change=2.0,
                     calibration_error=True)
    gap = lessons.detect_gap("CRWD")
    assert gap is not None
    assert gap["calibration_error"] is True
    assert "calibration" in gap["gap_reason"].lower()


def test_detect_gap_correct_no_gap(lessons, archive):
    _seed_scored_run(archive, outcome="CORRECT", pct_change=2.0)
    assert lessons.detect_gap("CRWD") is None


def test_detect_gap_pending_ignored(lessons, archive):
    """PENDING rows are unscored — they must never trigger a post-mortem."""
    _seed_scored_run(archive, outcome="PENDING", pct_change=0.0)
    assert lessons.detect_gap("CRWD") is None


def test_detect_gap_no_runs(lessons, archive):
    assert lessons.detect_gap("ZZZZ") is None


# ── Storage: dedupe, cap, deactivation ────────────────────────────────────────

def test_save_and_ingest_roundtrip(lessons):
    n = lessons.save_lessons(
        [{"agent_key": "dcf_engine", "lesson": "Clamp IV drag when OE<=0.",
          "general": True}],
        _gap())
    assert n == 1
    got = lessons.get_active_lessons("dcf_engine")
    assert got == ["Clamp IV drag when OE<=0."]
    assert lessons.get_active_lessons("sotp_extractor") == []


def test_content_hash_dedupe(lessons):
    ls = [{"agent_key": "dcf_engine", "lesson": "  Banks: no DLR-class capex. ",
           "general": True}]
    lessons.save_lessons(ls, _gap(run_id="r1"))
    # Same text again (whitespace-normalised) from a different run: upserts,
    # it does not duplicate.
    lessons.save_lessons(
        [{"agent_key": "dcf_engine",
          "lesson": "Banks:  no DLR-class capex.", "general": True}],
        _gap(run_id="r2"))
    rows = lessons.list_lessons("dcf_engine", include_inactive=True)
    assert len(rows) == 1
    assert rows[0]["run_id"] == "r2"  # evidence refreshed to the latest gap


def test_six_active_cap(lessons):
    batch = [{"agent_key": "dcf_engine", "lesson": f"lesson number {i}",
              "general": True} for i in range(8)]
    lessons.save_lessons(batch, _gap())
    active = lessons.get_active_lessons("dcf_engine")
    assert len(active) == 6
    total = lessons.list_lessons("dcf_engine", include_inactive=True)
    assert len(total) == 8  # overflow deactivated, never deleted


def test_invalid_entries_filtered(lessons):
    n = lessons.save_lessons([
        {"agent_key": "not_an_agent", "lesson": "x", "general": True},
        {"agent_key": "dcf_engine", "lesson": "   ", "general": True},
        {"agent_key": "sotp_extractor", "lesson": "keep me", "general": True},
    ], _gap())
    assert n == 1
    assert lessons.get_active_lessons("sotp_extractor") == ["keep me"]


def test_ticker_specific_vs_general(lessons):
    lessons.save_lessons([
        {"agent_key": "dcf_engine", "lesson": "general rule", "general": True},
        {"agent_key": "dcf_engine", "lesson": "crwd only", "general": False},
    ], _gap(), general_ticker="CRWD")
    rows = {r["lesson"]: r["ticker"] for r in
            lessons.list_lessons("dcf_engine")}
    assert rows["general rule"] is None
    assert rows["crwd only"] == "CRWD"


def test_ingestion_newest_first(lessons):
    """Seed created_at explicitly — ordering must not depend on wall-clock
    granularity (which is coarse on Windows)."""
    import uuid
    lessons._ensure_table()
    for lesson, created, lid in [
        ("older lesson", "2026-08-01T00:00:00+00:00", uuid.uuid4().hex),
        ("newer lesson", "2026-08-10T00:00:00+00:00", uuid.uuid4().hex),
    ]:
        _db.execute(lessons._SAVE_SQL, [
            lid, lessons._lesson_hash("dcf_engine", lesson), "dcf_engine",
            None, "run-x", lesson, "{}", created,
        ])
    got = lessons.get_active_lessons("dcf_engine")
    assert got[0] == "newer lesson"


def test_deactivate_hides_lesson(lessons):
    lessons.save_lessons(
        [{"agent_key": "dcf_engine", "lesson": "to retire", "general": True}],
        _gap())
    lid = lessons.list_lessons("dcf_engine")[0]["lesson_id"]
    assert lessons.deactivate_lesson(lid) is True
    assert lessons.get_active_lessons("dcf_engine") == []
    assert len(lessons.list_lessons("dcf_engine", include_inactive=True)) == 1


# ── Kill switch ───────────────────────────────────────────────────────────────

def test_kill_switch(lessons, monkeypatch, archive):
    _seed_scored_run(archive, outcome="INCORRECT")
    lessons.save_lessons(
        [{"agent_key": "dcf_engine", "lesson": "existing", "general": True}],
        _gap())
    monkeypatch.setenv("AGENT_LESSONS", "false")
    assert lessons.lessons_enabled() is False
    # ingestion degrades to nothing…
    assert lessons.get_active_lessons("dcf_engine") == []
    # …and generation is skipped entirely
    assert lessons.maybe_generate_lessons("CRWD") == []


# ── Orchestrator: maybe_generate_lessons ─────────────────────────────────────

def test_maybe_generate_full_path(lessons, monkeypatch, archive):
    _seed_scored_run(archive, outcome="INCORRECT")
    calls = []

    def fake_distill(gap, prior_recap=None):
        calls.append(gap["run_id"])
        return [{"agent_key": "dcf_engine",
                 "lesson": "Post-mortem lesson.", "general": True}]

    monkeypatch.setattr(lessons, "distill_lessons", fake_distill)
    out = lessons.maybe_generate_lessons("CRWD", prior_recap={"recap_text": "x"})
    assert len(out) == 1
    assert calls == ["run-gap"]
    assert lessons.get_active_lessons("dcf_engine") == ["Post-mortem lesson."]

    # Cost guard: the SAME gap run must not pay a second post-mortem.
    out2 = lessons.maybe_generate_lessons("CRWD")
    assert out2 == []
    assert calls == ["run-gap"]


def test_maybe_generate_no_gap_no_llm(lessons, monkeypatch, archive):
    _seed_scored_run(archive, outcome="CORRECT", pct_change=1.0)

    def fake_distill(gap, prior_recap=None):
        pytest.fail("no gap → no post-mortem LLM call")

    monkeypatch.setattr(lessons, "distill_lessons", fake_distill)
    assert lessons.maybe_generate_lessons("CRWD") == []


def test_maybe_generate_distill_empty(lessons, monkeypatch, archive):
    """A 'pure market noise' post-mortem (zero lessons) saves nothing."""
    _seed_scored_run(archive, outcome="INCORRECT")
    monkeypatch.setattr(lessons, "distill_lessons", lambda gap, prior_recap=None: [])
    assert lessons.maybe_generate_lessons("CRWD") == []
    assert lessons.list_lessons(include_inactive=True) == []


# ── PG-compatible SQL shape ──────────────────────────────────────────────────

def test_save_sql_pg_compatible(lessons):
    sql = lessons._SAVE_SQL
    assert "ON CONFLICT(agent_key, lesson_hash) DO UPDATE" in " ".join(sql.split())
    assert "INSERT OR REPLACE" not in sql.upper()
    assert "?" in sql


# ── Ingestion wiring guards ───────────────────────────────────────────────────
# The ingestion sites live deep inside the extractor/DCF prompt builders
# (behind full AgentState + LLM calls), so these guard the wiring at source
# level: each consumer must load its agent_key's lessons and append them to
# the system prompt. E2E covers the runtime path.

def test_sotp_extractor_ingests_lessons():
    from src.agents.analysis import sotp_extractor
    src = inspect.getsource(sotp_extractor)
    assert 'get_active_lessons("sotp_extractor")' in src
    assert "Past misses to avoid" in src
    # both SOTP passes get the lessons block
    assert src.count("_ECONOMICS_SYSTEM + _lessons_txt") >= 1
    assert src.count("_MULTIPLES_SYSTEM + _lessons_txt") >= 1


def test_dcf_calibration_extractor_ingests_lessons():
    """dcf_agent.py is deterministic (no LLM) — the dcf_engine lessons attach
    to _extract_dcf_calibration in deep_research, the one LLM surface that
    feeds the DCF engine."""
    from src.agents.industry import deep_research
    src = inspect.getsource(deep_research._extract_dcf_calibration)
    assert 'get_active_lessons("dcf_engine")' in src
    assert "Past misses to avoid" in src
    assert "_lessons_block" in src
