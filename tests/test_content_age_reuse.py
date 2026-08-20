"""
tests/test_content_age_reuse.py
===============================
M2 Track A2 — content-age reuse (kills the staleness chain).

Before M2, reuse was keyed on runs.run_at (when the row was written) and
archived/delta tiers could never seed reuse at all — so a pure-cache re-run
wrote a fresh row, which reset the clock, and stale content rolled forward
forever (prod 08-19 BABA: full research pass despite a run the day before).

Now:
  * runs.research_as_of records the CONTENT date (full live -> today,
    delta success -> today, pure cache -> inherited from the source run).
  * get_recent_research qualifies anthropic_web_cached / archive_news_delta
    tiers and ages rows on COALESCE(research_as_of, run_at).
  * deep_research's pure-cache branch inherits the source content date
    instead of refreshing it.
"""
import sqlite3
from datetime import datetime, timedelta

import pytest

import src.agents.industry.deep_research as dr
import src.memory.run_archive as run_archive


# ── tmp-archive fixture (schema-initialised SQLite at a temp path) ───────────

@pytest.fixture
def tmp_archive(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db_path = str(tmp_path / "run_archive.db")
    monkeypatch.setattr(run_archive, "DB_PATH", db_path)
    monkeypatch.setattr(run_archive, "_sqlite_schema_paths", set())
    return db_path


def _state(ticker="CRWD", tier="qwen_web", as_of=None):
    return {
        "metadata": {"model_name": "test-model"},
        "data": {
            "tickers": [ticker],
            "end_date": "2026-08-20",
            "research_tier": tier,
            "research_as_of": as_of,
            "deep_research": "2A. Financial Performance.\nRevenue grew 29%.",
        },
    }


def _seed(ticker="CRWD", tier="qwen_web", as_of=None, run_at=None):
    """Seed a runs row via save_run, then backdate run_at/research_as_of
    directly (save_run always stamps now())."""
    rid = run_archive.save_run(_state(ticker, tier, as_of), {})
    assert rid, "save_run must succeed on the tmp archive"
    conn = sqlite3.connect(run_archive.DB_PATH)
    sets, params = [], []
    if run_at is not None:
        sets.append("run_at = ?")
        params.append(run_at)
    if as_of is not None:
        sets.append("research_as_of = ?")
        params.append(as_of)
    if sets:
        params.append(rid)
        conn.execute(f"UPDATE runs SET {', '.join(sets)} WHERE run_id = ?",
                     params)
    conn.commit()
    conn.close()
    return rid


def _days_ago(n: float) -> str:
    return (datetime.now() - timedelta(days=n)).isoformat()


# ── Schema ────────────────────────────────────────────────────────────────────

def test_runs_table_has_research_as_of_column(tmp_archive):
    run_archive.ensure_schema()
    conn = sqlite3.connect(tmp_archive)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(runs)")}
    conn.close()
    assert "research_as_of" in cols


def test_migration_adds_column_to_legacy_db(tmp_path, monkeypatch):
    """A pre-M2 DB (no research_as_of) gets the column via _MIGRATIONS."""
    db_path = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE runs (run_id TEXT PRIMARY KEY, run_at TEXT)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(run_archive, "DB_PATH", db_path)
    monkeypatch.setattr(run_archive, "_sqlite_schema_paths", set())
    run_archive.ensure_schema()
    conn = sqlite3.connect(db_path)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(runs)")}
    conn.close()
    assert "research_as_of" in cols


# ── save_run → get_recent_research roundtrip ─────────────────────────────────

def test_save_run_persists_research_as_of(tmp_archive):
    rid = _seed(as_of="2026-08-18")
    conn = sqlite3.connect(run_archive.DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT research_as_of FROM runs WHERE run_id = ?", (rid,)).fetchone()
    conn.close()
    assert row["research_as_of"] == "2026-08-18"


def test_cached_and_delta_tiers_now_qualify(tmp_archive):
    """The 08-19 root cause: archived/delta tiers could never seed reuse."""
    for tier in ("anthropic_web_cached", "archive_news_delta"):
        _seed(ticker="TIERQ", tier=tier, as_of=_days_ago(1))
        got = run_archive.get_recent_research("TIERQ", max_age_days=14)
        assert got is not None, f"tier {tier} must qualify for reuse"
        assert got["research_tier"] == tier
        # clean up between tiers so each is tested on its own row
        conn = sqlite3.connect(run_archive.DB_PATH)
        conn.execute("DELETE FROM runs")
        conn.commit()
        conn.close()


def test_age_keyed_on_content_not_write_time(tmp_archive):
    """Row written NOW but whose content is 5 days old: age_days ~5, and
    the returned research_as_of carries the content date."""
    _seed(as_of=_days_ago(5))
    got = run_archive.get_recent_research("CRWD", max_age_days=14)
    assert got is not None
    assert 4.5 <= got["age_days"] <= 5.5
    assert got["research_as_of"] is not None


def test_stale_content_blocks_reuse_despite_fresh_run_at(tmp_archive):
    """The staleness-chain regression: content 20 d old, row written today —
    must NOT qualify under a 14 d window (pre-M2 this reused forever)."""
    _seed(as_of=_days_ago(20), run_at=_days_ago(0.01))
    assert run_archive.get_recent_research("CRWD", max_age_days=14) is None


def test_null_research_as_of_falls_back_to_run_at(tmp_archive):
    """Pre-M2 rows have NULL research_as_of — they stay usable, aged on
    run_at exactly as before."""
    _seed(run_at=_days_ago(2))
    got = run_archive.get_recent_research("CRWD", max_age_days=14)
    assert got is not None
    assert got["research_as_of"] is None
    assert 1.5 <= got["age_days"] <= 2.5


def test_ordering_prefers_freshest_content(tmp_archive):
    """Two rows: A written later but with older content; B written earlier
    but with fresher content. B must win — reuse is content-dated."""
    _seed(run_at=_days_ago(1), as_of=_days_ago(10))                 # A
    _seed(run_at=_days_ago(3), as_of=_days_ago(0.5))                # B
    got = run_archive.get_recent_research("CRWD", max_age_days=14)
    assert got is not None
    assert got["age_days"] < 1.0


# ── deep_research branch: content-age inheritance ────────────────────────────

_CACHED_ROW = {
    "run_id": "cached-run-1",
    "run_at": "2026-08-18T10:00:00",
    "analysis_date": "2026-08-18",
    "age_days": 0.2,
    "research_tier": "qwen_web",
    "deep_research_text": "2A. Financial Performance.\nRevenue grew 29%.",
    "deep_research_sections": {"2a": "Revenue grew 29%."},
}


def _patch_for_cache_hit(monkeypatch, cached_row):
    monkeypatch.setattr(
        run_archive, "get_recent_research",
        lambda ticker, max_age_days=7, qualifying_tiers=None: dict(cached_row),
    )
    monkeypatch.setattr(
        dr, "_extract_citation_registry",
        lambda *a, **k: [{"ref_id": 1, "quote": "q" * 25, "url": "https://x"}],
    )
    persisted = {
        "dcf_calibration": {"wacc": 0.09, "notes": "ok"},
        "saas_metrics": {"nrr_pct": 1.18},
    }
    monkeypatch.setattr(
        run_archive, "get_extractor_outputs",
        lambda ticker, h: {"results": persisted, "failures": []},
    )


def _run_cache_hit(monkeypatch, cached_row):
    _patch_for_cache_hit(monkeypatch, cached_row)
    return dr._research_one_ticker(
        ticker="ZZTEST", sector="Tech", end_date="2026-08-20",
        anthropic_key="k", model_name="qwen3.6-plus",
        raw_financials={}, insider_summary="",
        base_url=None, synthesis_model="qwen3.6-plus", profile_name="SaaS",
    )


def test_pure_cache_inherits_source_content_date(tmp_archive, monkeypatch):
    """Pure cache must NOT refresh content age — the next run still sees
    how old the underlying research really is."""
    row = dict(_CACHED_ROW, research_as_of="2026-08-18")
    out = _run_cache_hit(monkeypatch, row)
    assert out["cache_hit"] is True
    assert out["research_tier"] == "anthropic_web_cached"
    assert out["research_as_of"] == "2026-08-18"


def test_pure_cache_pre_m2_row_inherits_run_at(tmp_archive, monkeypatch):
    """Pre-M2 source rows have no research_as_of — inherit their run_at as
    the best content-date approximation (never NULL, which would let the
    staleness chain back in through the run_at fallback)."""
    out = _run_cache_hit(monkeypatch, dict(_CACHED_ROW))  # no research_as_of
    assert out["research_as_of"] == "2026-08-18T10:00:00"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
