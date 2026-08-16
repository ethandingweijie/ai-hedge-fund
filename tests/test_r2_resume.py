"""
tests/test_r2_resume.py
=======================
R2 batch — checkpoint resume: bundle builder + stale-checkpoint cleanup
(app/backend/services/analysis_service.py), phase-cache seeding
(src/pipeline.py::_merge_resume_into_phase_cache), the deep-research
resume cache path (src/agents/industry/deep_research.py::
_resolve_research_cache), and the R2 failure-surfacing warn paths
(save_run / _save_web_run archive warnings, warn-once throttles in
complacency scoring, rate limiter fail-open, progress_bus livestatus).

A web_runs row keeps is_checkpoint=1 only while its run is incomplete —
the successful final save upserts the SAME row to is_checkpoint=0 — so an
is_checkpoint=1 row inside the resume window is by definition an
abandoned/crashed run whose expensive Phase 3/4 output the next run of the
same ticker can reuse.
"""
import asyncio
import json
import uuid
from datetime import datetime, timedelta

import pytest

from src.data import db as _db


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """Point the dual-mode layer at a fresh tmp SQLite file."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("RUN_ARCHIVE_PATH", str(tmp_path / "r2_resume.db"))
    monkeypatch.delenv("RESUME_FROM_CHECKPOINT", raising=False)
    monkeypatch.delenv("RESUME_WINDOW_H", raising=False)
    monkeypatch.delenv("CHECKPOINT_RETENTION_DAYS", raising=False)
    _db.close_all_connections()
    yield
    _db.close_all_connections()


def _ckpt_data(ticker: str = "TEST") -> dict:
    """Checkpoint `data` subset as _save_partial_web_run writes it."""
    return {
        "tickers": [ticker],
        "research_tier": "anthropic_web",
        "deep_research": f"SECTION 2 research text for {ticker}",
        "deep_research_sections": {"2": f"research text for {ticker}"},
        "industry_brief": "industry brief text",
        "dcf_range": {ticker: {"fair_value": 100.0}},
        "power_law_analysis": {ticker: {"total_score": 7}},
        "value_trap_analysis": {ticker: {"risk": "LOW"}},
    }


def _seed_checkpoint(
    asvc,
    *,
    ticker: str = "TEST",
    model: str = "claude-sonnet-4-6",
    age_minutes: float = 90.0,
    is_checkpoint: int = 1,
    data: dict | None = None,
    checkpoint: str = "final_calculation",
    run_id: str | None = None,
) -> str:
    """Insert one web_runs row shaped exactly like _save_partial_web_run's
    output, but with a backdated run_at for age-window tests."""
    run_id = run_id or str(uuid.uuid4())
    payload = {
        "run_id": run_id,
        "ticker": ticker,
        "model_name": model,
        "run_at": datetime.utcnow().isoformat(),
        "checkpoint": checkpoint,
        "data": _ckpt_data(ticker) if data is None else data,
    }
    run_at = (datetime.now() - timedelta(minutes=age_minutes)).strftime(
        "%Y-%m-%dT%H:%M:%S.%f")
    asvc._exec(
        asvc._web_runs_upsert_sql(
            ["run_id", "run_at", "ticker", "model_name", "archive_run_id",
             "full_result_json", "is_checkpoint", "user_id"]),
        [run_id, run_at, ticker.upper(), model, None, json.dumps(payload),
         is_checkpoint, None],
    )
    return run_id


# ── _build_resume_bundle ─────────────────────────────────────────────────────

def test_bundle_fresh_checkpoint(tmp_db):
    from app.backend.services import analysis_service as asvc
    asvc._ensure_web_runs_table()
    rid = _seed_checkpoint(asvc, age_minutes=90)

    b = asvc._build_resume_bundle("TEST", "claude-sonnet-4-6", str(uuid.uuid4()))
    assert b is not None
    assert b["run_id"] == rid
    assert b["checkpoint"] == "final_calculation"
    assert b["data"]["deep_research"].startswith("SECTION 2")
    assert b["data"]["deep_research_sections"]["2"]
    assert b["data"]["research_tier"] == "anthropic_web"
    # 90 min old ≈ 0.0625 days
    assert 0.05 < b["age_days"] < 0.08


def test_bundle_too_old(tmp_db):
    from app.backend.services import analysis_service as asvc
    asvc._ensure_web_runs_table()
    _seed_checkpoint(asvc, age_minutes=7 * 60)  # beyond the 6 h window
    assert asvc._build_resume_bundle("TEST", "claude-sonnet-4-6", "cur") is None


def test_bundle_too_new(tmp_db):
    from app.backend.services import analysis_service as asvc
    asvc._ensure_web_runs_table()
    _seed_checkpoint(asvc, age_minutes=5)  # inside the 20-min min-age guard
    assert asvc._build_resume_bundle("TEST", "claude-sonnet-4-6", "cur") is None


def test_bundle_model_mismatch(tmp_db):
    from app.backend.services import analysis_service as asvc
    asvc._ensure_web_runs_table()
    _seed_checkpoint(asvc, model="claude-sonnet-4-6", age_minutes=90)
    assert asvc._build_resume_bundle("TEST", "qwen3.6-plus", "cur") is None


def test_bundle_final_row_not_resumable(tmp_db):
    from app.backend.services import analysis_service as asvc
    asvc._ensure_web_runs_table()
    # is_checkpoint=0 → successful run; resume must never touch final rows
    _seed_checkpoint(asvc, age_minutes=90, is_checkpoint=0)
    assert asvc._build_resume_bundle("TEST", "claude-sonnet-4-6", "cur") is None


def test_bundle_multi_ticker_rejected(tmp_db):
    from app.backend.services import analysis_service as asvc
    asvc._ensure_web_runs_table()
    data = _ckpt_data("TEST")
    data["tickers"] = ["TEST", "OTHER"]
    _seed_checkpoint(asvc, age_minutes=90, data=data)
    assert asvc._build_resume_bundle("TEST", "claude-sonnet-4-6", "cur") is None


def test_bundle_pre_phase3_rejected(tmp_db):
    from app.backend.services import analysis_service as asvc
    asvc._ensure_web_runs_table()
    data = _ckpt_data("TEST")
    data["deep_research"] = None
    data["industry_brief"] = None
    _seed_checkpoint(asvc, age_minutes=90, data=data)
    assert asvc._build_resume_bundle("TEST", "claude-sonnet-4-6", "cur") is None


def test_bundle_own_run_id_rejected(tmp_db):
    from app.backend.services import analysis_service as asvc
    asvc._ensure_web_runs_table()
    rid = _seed_checkpoint(asvc, age_minutes=90)
    assert asvc._build_resume_bundle("TEST", "claude-sonnet-4-6", rid) is None


def test_bundle_kill_switch(tmp_db, monkeypatch):
    from app.backend.services import analysis_service as asvc
    asvc._ensure_web_runs_table()
    _seed_checkpoint(asvc, age_minutes=90)
    monkeypatch.setenv("RESUME_FROM_CHECKPOINT", "false")
    assert asvc._build_resume_bundle("TEST", "claude-sonnet-4-6", "cur") is None


def test_bundle_window_override(tmp_db, monkeypatch):
    from app.backend.services import analysis_service as asvc
    asvc._ensure_web_runs_table()
    _seed_checkpoint(asvc, age_minutes=90)
    monkeypatch.setenv("RESUME_WINDOW_H", "1")  # 90 min > 1 h window
    assert asvc._build_resume_bundle("TEST", "claude-sonnet-4-6", "cur") is None


def test_bundle_latest_checkpoint_wins(tmp_db):
    from app.backend.services import analysis_service as asvc
    asvc._ensure_web_runs_table()
    _seed_checkpoint(asvc, age_minutes=150, checkpoint="deep_research")
    rid2 = _seed_checkpoint(asvc, age_minutes=45, checkpoint="investor_signals")
    b = asvc._build_resume_bundle("TEST", "claude-sonnet-4-6", "cur")
    assert b is not None and b["run_id"] == rid2
    assert b["checkpoint"] == "investor_signals"


# ── cleanup_stale_checkpoints ────────────────────────────────────────────────

def test_cleanup_deletes_only_stale_checkpoints(tmp_db):
    from app.backend.services import analysis_service as asvc
    asvc._ensure_web_runs_table()
    _seed_checkpoint(asvc, ticker="OLD", age_minutes=20 * 24 * 60)      # stale ckpt
    _seed_checkpoint(asvc, ticker="NEW", age_minutes=60)                # fresh ckpt
    _seed_checkpoint(asvc, ticker="FINAL", age_minutes=30 * 24 * 60,
                     is_checkpoint=0)                                   # stale FINAL

    deleted = asvc.cleanup_stale_checkpoints(retention_days=14)
    assert deleted == 1

    rows = asvc._fetch("SELECT ticker, is_checkpoint FROM web_runs ORDER BY ticker")
    kept = {r["ticker"] for r in rows}
    assert kept == {"FINAL", "NEW"}   # stale checkpoint gone; FINAL + fresh kept


def test_cleanup_retention_override(tmp_db):
    from app.backend.services import analysis_service as asvc
    asvc._ensure_web_runs_table()
    _seed_checkpoint(asvc, ticker="A1", age_minutes=3 * 24 * 60)
    assert asvc.cleanup_stale_checkpoints(retention_days=14) == 0
    assert asvc.cleanup_stale_checkpoints(retention_days=2) == 1


def test_cleanup_env_default(tmp_db, monkeypatch):
    from app.backend.services import analysis_service as asvc
    asvc._ensure_web_runs_table()
    _seed_checkpoint(asvc, ticker="E1", age_minutes=10 * 24 * 60)
    monkeypatch.setenv("CHECKPOINT_RETENTION_DAYS", "5")
    assert asvc.cleanup_stale_checkpoints() == 1


# ── E2E: seed → resume → cleanup (the verification-recipe flow) ──────────────

def test_resume_then_cleanup_e2e(tmp_db):
    from app.backend.services import analysis_service as asvc
    asvc._ensure_web_runs_table()
    rid = _seed_checkpoint(asvc, ticker="E2E", age_minutes=120)

    bundle = asvc._build_resume_bundle("E2E", "claude-sonnet-4-6", "current-run")
    assert bundle is not None and bundle["run_id"] == rid

    assert asvc.cleanup_stale_checkpoints(retention_days=0.05) == 1  # ~72 min
    assert asvc._build_resume_bundle("E2E", "claude-sonnet-4-6", "current-run") is None


# ── _merge_resume_into_phase_cache (pure) ────────────────────────────────────

def _bundle_for_merge(ticker="TEST", age_days=0.05):
    return {
        "checkpoint": "final_calculation",
        "run_id": "ckpt-run",
        "run_at": "2026-08-16T01:00:00.000000",
        "age_days": age_days,
        "data": _ckpt_data(ticker),
    }


def test_merge_creates_entry_from_scratch():
    from src.pipeline import _merge_resume_into_phase_cache
    cache: dict = {}
    assert _merge_resume_into_phase_cache(cache, _bundle_for_merge(), ["TEST"]) is True
    e = cache["TEST"]
    assert e["industry_brief"] == "industry brief text"
    assert e["dcf_range"] == {"fair_value": 100.0}
    assert e["power_law"] == {"total_score": 7}
    assert e["value_trap"] == {"risk": "LOW"}
    assert e["deep_research"].startswith("SECTION 2")
    assert e["age_days"] == 0.05
    assert e["run_id"] == "ckpt-run"


def test_merge_never_clobbers_archive_values():
    from src.pipeline import _merge_resume_into_phase_cache
    archive_entry = {
        "run_id": "archive-run", "run_at": "2026-08-15T00:00:00",
        "age_days": 1.0,
        "industry_brief": "archive brief",           # archive wins
        "deep_research": None,
        "power_law": None, "dcf_range": None, "citation_audit": None,
        "scenario": None, "value_trap": None,
        "sector_card_hash": None, "card_qa_audit": None,
    }
    cache = {"TEST": archive_entry}
    assert _merge_resume_into_phase_cache(cache, _bundle_for_merge(), ["TEST"]) is True
    assert cache["TEST"]["industry_brief"] == "archive brief"      # untouched
    assert cache["TEST"]["dcf_range"] == {"fair_value": 100.0}     # gap filled
    assert cache["TEST"]["power_law"] == {"total_score": 7}        # gap filled


def test_merge_noop_without_bundle():
    from src.pipeline import _merge_resume_into_phase_cache
    cache: dict = {}
    assert _merge_resume_into_phase_cache(cache, None, ["TEST"]) is False
    assert cache == {}


# ── _resolve_research_cache ──────────────────────────────────────────────────

def _resume_research(tier="anthropic_web", text="SECTION 2 text", sections=None):
    return {
        "run_id": "ckpt-run",
        "run_at": "2026-08-16T01:00:00.000000",
        "analysis_date": "2026-08-16",
        "age_days": 0.04,
        "research_tier": tier,
        "deep_research_text": text,
        "deep_research_sections": sections if sections is not None else {"2": "text"},
    }


def test_resolve_prefers_live_tier_resume(monkeypatch):
    import src.memory.run_archive as ra
    from src.agents.industry.deep_research import _resolve_research_cache

    def _boom(*a, **k):
        raise AssertionError("archive must not be consulted when resume is valid")
    monkeypatch.setattr(ra, "get_recent_research", _boom)

    out = _resolve_research_cache("TEST", resume_research=_resume_research())
    assert out is not None
    assert out["run_id"] == "ckpt-run"
    assert out["deep_research_text"] == "SECTION 2 text"


def test_resolve_reparses_missing_sections(monkeypatch):
    import src.memory.run_archive as ra
    from src.agents.industry.deep_research import _resolve_research_cache

    monkeypatch.setattr(ra, "_parse_sections_inline", lambda text: {"2": "parsed"})
    rr = _resume_research(sections={})
    out = _resolve_research_cache("TEST", resume_research=rr)
    assert out["deep_research_sections"] == {"2": "parsed"}


def test_resolve_discards_knowledge_only_resume(monkeypatch):
    import src.memory.run_archive as ra
    from src.agents.industry.deep_research import _resolve_research_cache

    archive_hit = _resume_research(tier="tavily")
    archive_hit["run_id"] = "archive-run"
    monkeypatch.setattr(ra, "get_recent_research", lambda *a, **k: archive_hit)

    out = _resolve_research_cache("TEST",
                                  resume_research=_resume_research(tier="knowledge_only"))
    assert out is not None and out["run_id"] == "archive-run"


def test_resolve_empty_resume_falls_back(monkeypatch):
    import src.memory.run_archive as ra
    from src.agents.industry.deep_research import _resolve_research_cache

    archive_hit = _resume_research()
    archive_hit["run_id"] = "archive-run"
    monkeypatch.setattr(ra, "get_recent_research", lambda *a, **k: archive_hit)

    out = _resolve_research_cache("TEST", resume_research=_resume_research(text=""))
    assert out["run_id"] == "archive-run"


def test_resolve_no_resume_uses_archive_and_discards_knowledge_only(monkeypatch):
    import src.memory.run_archive as ra
    from src.agents.industry.deep_research import _resolve_research_cache

    monkeypatch.setattr(ra, "get_recent_research", lambda *a, **k: None)
    assert _resolve_research_cache("TEST") is None

    ko = _resume_research(tier="knowledge_only")
    monkeypatch.setattr(ra, "get_recent_research", lambda *a, **k: ko)
    assert _resolve_research_cache("TEST") is None


# ── R2-6 surfacing touched by this file's modules ────────────────────────────

def test_save_web_run_warns_without_archive_link(tmp_db, caplog):
    import logging
    from app.backend.services import analysis_service as asvc
    asvc._ensure_web_runs_table()
    with caplog.at_level(logging.WARNING, logger="app.backend.services.analysis_service"):
        asvc._save_web_run("run-x", "TEST", "claude-sonnet-4-6",
                           {"run_id": "run-x", "data": {}}, archive_run_id=None)
    assert any("WITHOUT archive link" in r.getMessage() for r in caplog.records)


def test_save_run_failure_is_loud(monkeypatch, caplog):
    import logging
    import src.memory.run_archive as ra

    def _boom(*a, **k):
        raise RuntimeError("pg down")
    monkeypatch.setattr(ra, "_Txn", _boom)

    with caplog.at_level(logging.WARNING, logger="src.memory.run_archive"):
        out = ra.save_run({"data": {"tickers": ["TEST"]}}, {})
    assert out == ""
    assert any("save_run FAILED" in r.getMessage() for r in caplog.records)


# ── R2-6 warn-once / throttle behaviours ─────────────────────────────────────

def test_scoring_fallback_warns_once_per_sector_metric(monkeypatch, caplog):
    import logging as _logging
    import app.backend.services.sector_medians_storage as sms
    from src.research_ideas.complacency import scoring

    monkeypatch.setattr(sms, "get_latest_sector_median", lambda *a, **k: None)
    scoring._FALLBACK_WARNED.discard(("Technology", "ev_sales"))

    with caplog.at_level(_logging.WARNING,
                         logger="src.research_ideas.complacency.scoring"):
        v1 = scoring.resolve_sector_median("Technology", "ev_sales")
        v2 = scoring.resolve_sector_median("Technology", "ev_sales")
    assert v1 == scoring.SECTOR_EV_SALES_MEDIAN_FALLBACK["Technology"] == 7.0
    assert v2 == v1
    warns = [r for r in caplog.records if "cache miss" in r.getMessage()]
    assert len(warns) == 1  # a 50-ticker sweep must not spam the log


def test_rate_limiter_fail_open_warns_throttled(monkeypatch, caplog):
    import logging as _logging
    from types import SimpleNamespace

    from app.backend.services import rate_limiter, redis_client

    class _FakeTime:
        def __init__(self):
            self._mono = 1000.0
        def monotonic(self):
            return self._mono
        def time(self):
            return self._mono

    fake = _FakeTime()
    monkeypatch.setattr(rate_limiter, "time", fake)

    async def _not_ready():
        return False
    monkeypatch.setattr(redis_client, "redis_ready", _not_ready)

    user = SimpleNamespace(id=1, role="member")
    kwargs = dict(user=user, scope="analysis", daily_limit=5,
                  concurrent_limit=None, slot_ttl_seconds=60)

    with caplog.at_level(_logging.WARNING,
                         logger="app.backend.services.rate_limiter"):
        await_ok = asyncio.run(rate_limiter.check_limits(**kwargs))   # warn #1
        assert await_ok is None                                        # fail OPEN
        asyncio.run(rate_limiter.check_limits(**kwargs))               # throttled
        assert len([r for r in caplog.records if "fail open" in r.getMessage()]) == 1
        fake._mono += rate_limiter._FAIL_OPEN_WARN_INTERVAL_S + 1
        asyncio.run(rate_limiter.check_limits(**kwargs))               # warn #2
        assert len([r for r in caplog.records if "fail open" in r.getMessage()]) == 2


def test_progress_bus_live_warn_throttled(monkeypatch, caplog):
    import logging as _logging
    from app.backend.services import progress_bus as pb

    class _FakeTime:
        def __init__(self):
            self._mono = 1000.0
        def monotonic(self):
            return self._mono

    monkeypatch.setattr(pb, "_LIVE_WARN_AT", 0.0)
    monkeypatch.setattr(pb, "time", _FakeTime())

    with caplog.at_level(_logging.WARNING,
                         logger="app.backend.services.progress_bus"):
        pb._warn_live_redis_failure("get_phase_map", ConnectionError("down"))
        pb._warn_live_redis_failure("clear_ticker", ConnectionError("down"))
        assert len([r for r in caplog.records if "livestatus degraded"
                    in r.getMessage()]) == 1  # inside the throttle window
