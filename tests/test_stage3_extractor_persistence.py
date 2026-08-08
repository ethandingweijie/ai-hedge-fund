"""Stage 3 gates — extractor persistence (Workstream C: C2).

FORWARD tests (new behavior works):
  C2 — fan-out outputs persist keyed on (ticker, sections content hash);
       a pure cache hit whose sections are unchanged reuses the persisted
       JSON with ZERO extractor LLM calls (dcf_calibration included).
       Fresh / delta / cache-miss paths all prime the store after running.

BACKWARD tests (old behavior unbroken):
  - Absent row, corrupt blob, unreadable DB → get returns None and the
    pre-C2 live fan-out path runs unchanged (a bad blob can never break
    a run).
  - Upsert: a later run with the same sections replaces an incomplete
    earlier extraction.
"""

import json
import sqlite3

import pytest

import src.agents.industry.deep_research as dr
import src.memory.run_archive as run_archive


# ── tmp-archive fixture (schema-initialised SQLite at a temp path) ───────────

@pytest.fixture
def tmp_archive(tmp_path, monkeypatch):
    db_path = str(tmp_path / "run_archive.db")
    monkeypatch.setattr(run_archive, "DB_PATH", db_path)
    monkeypatch.setattr(run_archive, "_sqlite_schema_paths", set())
    return db_path


# ── C2: archive round-trip ────────────────────────────────────────────────────

def test_c2_save_and_get_round_trip(tmp_archive):
    results = {"dcf_calibration": {"wacc": 0.09}, "saas_metrics": {"nrr_pct": 1.2}}
    failures = [{"extractor": "insurance_metrics", "stage": "empty",
                 "detail": "no usable values after all attempts"}]
    assert run_archive.save_extractor_outputs("crwd", "hash-a", results, failures)
    got = run_archive.get_extractor_outputs("CRWD", "hash-a")   # case-insensitive
    assert got["results"] == results
    assert got["failures"] == failures


def test_c2_get_absent_returns_none(tmp_archive):
    assert run_archive.get_extractor_outputs("CRWD", "never-saved") is None


def test_c2_upsert_replaces_incomplete_extraction(tmp_archive):
    run_archive.save_extractor_outputs(
        "CRWD", "h", {"dcf_calibration": {}}, [])
    run_archive.save_extractor_outputs(
        "CRWD", "h", {"dcf_calibration": {"wacc": 0.08}}, [])
    got = run_archive.get_extractor_outputs("CRWD", "h")
    assert got["results"]["dcf_calibration"] == {"wacc": 0.08}


def test_c2_corrupt_blob_returns_none(tmp_archive, monkeypatch):
    run_archive.save_extractor_outputs(
        "CRWD", "h", {"dcf_calibration": {"wacc": 0.09}}, [])
    # Corrupt the stored JSON in place (simulates a torn write / bad blob)
    conn = sqlite3.connect(tmp_archive)
    conn.execute(
        "UPDATE extractor_outputs SET outputs_json = '{not json' "
        "WHERE ticker = 'CRWD'"
    )
    conn.commit()
    conn.close()
    assert run_archive.get_extractor_outputs("CRWD", "h") is None


def test_c2_schemaless_get_degrades_to_none(monkeypatch):
    """A DB error (e.g. table missing on a legacy replica) must degrade to
    'no persisted outputs', never raise into the pipeline."""
    monkeypatch.setattr(
        run_archive, "_fetch_one",
        lambda sql, params=None: (_ for _ in ()).throw(sqlite3.OperationalError("no such table")),
    )
    assert run_archive.get_extractor_outputs("CRWD", "h") is None


# ── C2: sections hash semantics ───────────────────────────────────────────────

def test_c2_hash_deterministic_and_order_insensitive():
    s1 = {"2a": "Revenue grew 29%.", "2b": "Competition is intense."}
    s2 = {"2b": "Competition is intense.", "2a": "Revenue grew 29%."}
    assert dr._hash_research_sections(s1, "SaaS") == dr._hash_research_sections(s2, "SaaS")


def test_c2_hash_sensitive_to_content_and_profile():
    s = {"2a": "Revenue grew 29%."}
    base = dr._hash_research_sections(s, "SaaS")
    assert base != dr._hash_research_sections({"2a": "Revenue grew 30%."}, "SaaS")
    assert base != dr._hash_research_sections(s, "Money Center Bank")
    # profile match is case/whitespace insensitive
    assert base == dr._hash_research_sections(s, "  saas ")


# ── C2: cache-hit reuse end-to-end through _research_one_ticker ──────────────

_CACHED_ROW = {
    "run_id": "cached-run-1",
    "run_at": "2026-08-08T10:00:00",
    "analysis_date": "2026-08-07",
    "age_days": 0.2,
    "research_tier": "qwen_web",
    "deep_research_text": "2A. Financial Performance.\nRevenue grew 29%.",
    "deep_research_sections": {"2a": "Revenue grew 29%."},
}

_PERSISTED = {
    "dcf_calibration": {"growth_rate_adj": 0.0, "margin_direction": "stable",
                        "risk_flag": "none", "notes": "ok"},
    "saas_metrics":    {"nrr_pct": 1.18},
    "framework_metrics": {"nrr_pct": 1.18, "_completeness_score": 0.9},
    "segment_scenarios": {"Subscription": {"bear": 1, "base": 2, "bull": 3}},
}


def _patch_cache_env(monkeypatch):
    """Point the archive gate at the canned cached row + cheap citation stub."""
    monkeypatch.setattr(
        run_archive, "get_recent_research",
        lambda ticker, max_age_days=7, qualifying_tiers=None: dict(_CACHED_ROW),
    )
    monkeypatch.setattr(
        dr, "_extract_citation_registry",
        lambda *a, **k: [{"ref_id": 1, "quote": "q" * 25, "url": "https://x"}],
    )


def test_c2_cache_hit_reuses_persisted_with_zero_llm_calls(tmp_archive, monkeypatch):
    _patch_cache_env(monkeypatch)
    run_archive.save_extractor_outputs(
        "ZZTEST",
        dr._hash_research_sections(_CACHED_ROW["deep_research_sections"], "SaaS"),
        _PERSISTED,
        [{"extractor": "insurance_metrics", "stage": "empty",
          "detail": "no usable values after all attempts"}],
    )

    def _must_not_run(*a, **k):
        raise AssertionError("live extraction must not run when persisted outputs match")

    monkeypatch.setattr(dr, "_run_extractor_fanout", _must_not_run)
    monkeypatch.setattr(dr, "_extract_dcf_calibration", _must_not_run)

    out = dr._research_one_ticker(
        ticker="ZZTEST", sector="Tech", end_date="2026-08-08",
        anthropic_key="k", model_name="qwen3.6-plus",
        raw_financials={}, insider_summary="",
        base_url=None, synthesis_model="qwen3.6-plus", profile_name="SaaS",
    )

    assert out["cache_hit"] is True
    assert out["dcf_calibration"] == _PERSISTED["dcf_calibration"]
    assert out["saas_metrics"] == _PERSISTED["saas_metrics"]
    assert out["framework_metrics"] == _PERSISTED["framework_metrics"]
    assert out["segment_scenarios"] == _PERSISTED["segment_scenarios"]
    # Persisted failures ride along (still diagnosable on reused runs)
    assert out["extractor_failures"][0]["extractor"] == "insurance_metrics"


def test_c2_cache_miss_runs_fanout_and_primes_store(tmp_archive, monkeypatch):
    _patch_cache_env(monkeypatch)
    calls = {"fanout": 0, "dcf": 0}

    def fake_dcf(c, m, s, t, retry_directive=""):
        calls["dcf"] += 1
        return {"wacc": 0.1}

    def fake_fanout(c, m, sections, report, ticker, sector, profile_name,
                    raw_financials, precomputed=None):
        calls["fanout"] += 1
        return ({"dcf_calibration": {"wacc": 0.1},
                 "saas_metrics": {"nrr_pct": 1.3}}, [])

    monkeypatch.setattr(dr, "_extract_dcf_calibration", fake_dcf)
    monkeypatch.setattr(dr, "_run_extractor_fanout", fake_fanout)

    out = dr._research_one_ticker(
        ticker="ZZTEST", sector="Tech", end_date="2026-08-08",
        anthropic_key="k", model_name="qwen3.6-plus",
        raw_financials={}, insider_summary="",
        base_url=None, synthesis_model="qwen3.6-plus", profile_name="SaaS",
    )

    assert calls == {"fanout": 1, "dcf": 1}
    assert out["saas_metrics"] == {"nrr_pct": 1.3}
    # Primed: the next cache hit on these sections reuses without LLM calls
    h = dr._hash_research_sections(_CACHED_ROW["deep_research_sections"], "SaaS")
    stored = run_archive.get_extractor_outputs("ZZTEST", h)
    assert stored["results"]["saas_metrics"] == {"nrr_pct": 1.3}


def test_c2_persisted_without_dcf_falls_back_to_live(tmp_archive, monkeypatch):
    """A persisted set whose dcf_calibration is empty (original extraction
    failed) must NOT be reused — the live path gets another chance."""
    _patch_cache_env(monkeypatch)
    run_archive.save_extractor_outputs(
        "ZZTEST",
        dr._hash_research_sections(_CACHED_ROW["deep_research_sections"], "SaaS"),
        {"dcf_calibration": {}, "saas_metrics": {"nrr_pct": 0.5}}, [],
    )
    calls = {"fanout": 0}

    def fake_fanout(c, m, sections, report, ticker, sector, profile_name,
                    raw_financials, precomputed=None):
        calls["fanout"] += 1
        return ({"dcf_calibration": {"wacc": 0.12},
                 "saas_metrics": {"nrr_pct": 1.4}}, [])

    monkeypatch.setattr(
        dr, "_extract_dcf_calibration",
        lambda c, m, s, t, retry_directive="": {"wacc": 0.12},
    )
    monkeypatch.setattr(dr, "_run_extractor_fanout", fake_fanout)

    out = dr._research_one_ticker(
        ticker="ZZTEST", sector="Tech", end_date="2026-08-08",
        anthropic_key="k", model_name="qwen3.6-plus",
        raw_financials={}, insider_summary="",
        base_url=None, synthesis_model="qwen3.6-plus", profile_name="SaaS",
    )

    assert calls["fanout"] == 1                      # live path ran
    assert out["saas_metrics"] == {"nrr_pct": 1.4}   # fresh values, not stale 0.5


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
