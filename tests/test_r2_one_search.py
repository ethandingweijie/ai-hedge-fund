"""
tests/test_r2_one_search.py
===========================
Workstream R2 — one-search delta routing + persistent citation registry.

R2 kills the two repeat-run costs left in the 3–14d research window:

  1. ROUTING — phase 2_9 (src/memory/freshness.py) already ran THE one web
     search and classified materiality. `_delta_route` consumes that verdict
     instead of re-searching: NOT material → replay archived sections with
     0 additional searches; MATERIAL → one_search delta synthesizes
     amendments from the carried snippets; anything else (no verdict, empty
     snippets, DEEP_RESEARCH_DELTA_MODE=legacy) → the pre-R2 delta search
     pass unchanged (backward gate).
  2. CITATION REGISTRY PERSISTENCE — the registry was rebuilt via LLM on
     EVERY cached path (~128 s, the cached-run floor). It is now persisted
     keyed by sha256(research full text) in run_archive (extractor_outputs
     pattern); cache-miss rebuilds as before (backward-safe), and empty
     registries are never persisted so a one-off extraction failure cannot
     poison future hits.

The freshness verdict carries its raw snippets through to phase 3
(`base_delta` gained "snippets") so the material route needs no new search.
"""
from __future__ import annotations

import sqlite3

import pytest

import src.agents.industry.deep_research as dr
import src.memory.freshness as freshness
import src.memory.run_archive as run_archive


# ── tmp-archive fixture (mirrors test_m2_addendum_delta.py) ──────────────────

@pytest.fixture
def tmp_archive(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db_path = str(tmp_path / "run_archive.db")
    monkeypatch.setattr(run_archive, "DB_PATH", db_path)
    monkeypatch.setattr(run_archive, "_sqlite_schema_paths", set())
    return db_path


# ── 1. Route decision matrix ─────────────────────────────────────────────────

class TestDeltaRoute:
    """`_delta_route` decides replay / one_search / legacy for a cache hit."""

    def test_default_mode_not_material_replays(self, monkeypatch):
        monkeypatch.delenv("DEEP_RESEARCH_DELTA_MODE", raising=False)
        fd = {"material": False, "events": [], "verdict": "quiet",
              "snippets": "some snippets"}
        assert dr._delta_route(5.0, fd) == "replay"

    def test_default_mode_material_with_snippets_one_search(self, monkeypatch):
        monkeypatch.delenv("DEEP_RESEARCH_DELTA_MODE", raising=False)
        fd = {"material": True, "events": [], "verdict": "big news",
              "snippets": "dated developments..."}
        assert dr._delta_route(5.0, fd) == "one_search"

    def test_material_without_snippets_falls_to_legacy(self, monkeypatch):
        """material=True but nothing carried (classification without usable
        text) cannot synthesize — must fall back to the search pass."""
        monkeypatch.delenv("DEEP_RESEARCH_DELTA_MODE", raising=False)
        for fd in (
            {"material": True, "snippets": ""},
            {"material": True},                       # snippets key missing
            {"material": True, "snippets": "   \n "},  # whitespace only
        ):
            assert dr._delta_route(5.0, fd) == "legacy"

    def test_no_verdict_falls_to_legacy(self, monkeypatch):
        monkeypatch.delenv("DEEP_RESEARCH_DELTA_MODE", raising=False)
        assert dr._delta_route(5.0, {"material": None}) == "legacy"
        assert dr._delta_route(5.0, {}) == "legacy"
        assert dr._delta_route(5.0, None) == "legacy"

    def test_legacy_mode_never_short_circuits(self, monkeypatch):
        """DEEP_RESEARCH_DELTA_MODE=legacy reproduces pre-R2 behavior: even a
        NOT-material verdict still runs the old delta search pass."""
        monkeypatch.setenv("DEEP_RESEARCH_DELTA_MODE", "legacy")
        assert dr._delta_route(5.0, {"material": False}) == "legacy"
        assert dr._delta_route(
            5.0, {"material": True, "snippets": "x"}) == "legacy"
        assert dr._delta_route(5.0, None) == "legacy"

    def test_mode_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("DEEP_RESEARCH_DELTA_MODE", "ONE_SEARCH")
        assert dr._delta_route(5.0, {"material": False}) == "replay"

    def test_age_does_not_alter_route(self, monkeypatch):
        """The reuse-window age guard lives in cache resolution; the route
        itself depends only on mode + verdict."""
        monkeypatch.delenv("DEEP_RESEARCH_DELTA_MODE", raising=False)
        fd = {"material": False, "snippets": ""}
        assert dr._delta_route(3.1, fd) == "replay"
        assert dr._delta_route(13.9, fd) == "replay"


# ── 2. Freshness snippets carry-through ──────────────────────────────────────

class TestFreshnessSnippets:
    """The classification's raw snippets travel with the verdict so the
    one_search delta can synthesize from THE one search (0 new searches)."""

    def test_base_delta_shape_includes_snippets(self):
        base = freshness.base_delta(None)
        assert base["snippets"] == ""
        assert base["material"] is None
        assert base["events"] == []
        assert "verdict" in base

    def test_snippets_carried_on_classified_delta(self, monkeypatch):
        monkeypatch.delenv("FRESHNESS_DELTA_SEARCH", raising=False)
        monkeypatch.setattr(
            freshness, "_search_fresh_snippets",
            lambda ticker, since, tavily_key, timeout: "FRESH SNIPPETS")
        monkeypatch.setattr(
            freshness, "classify_delta",
            lambda ticker, prior, snippets, since="": {
                "material": True,
                "events": [{"headline": "Guidance raised", "date": "",
                            "relevance": ""}],
                "verdict": "material change",
            })
        out = freshness.run_freshness_search("CRWD", {"run_at": "2026-08-10"})
        assert out["snippets"] == "FRESH SNIPPETS"
        assert out["material"] is True
        assert out["verdict"] == "material change"

    def test_snippets_truncated_to_keep_chars(self, monkeypatch):
        monkeypatch.delenv("FRESHNESS_DELTA_SEARCH", raising=False)
        big = "x" * (freshness._SNIPPETS_KEEP_CHARS + 2000)
        monkeypatch.setattr(
            freshness, "_search_fresh_snippets",
            lambda ticker, since, tavily_key, timeout: big)
        monkeypatch.setattr(freshness, "classify_delta",
                            lambda *a, **k: None)
        out = freshness.run_freshness_search("CRWD", None)
        assert len(out["snippets"]) == freshness._SNIPPETS_KEEP_CHARS

    def test_no_results_means_empty_snippets(self, monkeypatch):
        monkeypatch.delenv("FRESHNESS_DELTA_SEARCH", raising=False)
        monkeypatch.setattr(
            freshness, "_search_fresh_snippets",
            lambda ticker, since, tavily_key, timeout: "")
        out = freshness.run_freshness_search("CRWD", None)
        assert out["snippets"] == ""
        assert out["verdict"] == "no fresh results"
        assert out["material"] is None

    def test_disabled_check_keeps_empty_snippets(self, monkeypatch):
        monkeypatch.setenv("FRESHNESS_DELTA_SEARCH", "false")
        out = freshness.run_freshness_search("CRWD", None)
        assert out["snippets"] == ""
        assert out["verdict"] == "check disabled"

    def test_classification_failure_keeps_snippets(self, monkeypatch):
        """Soft-fail: classify_delta returning None leaves snippets intact —
        material stays None so the route falls to legacy, but a later
        consumer can still see what the search found."""
        monkeypatch.delenv("FRESHNESS_DELTA_SEARCH", raising=False)
        monkeypatch.setattr(
            freshness, "_search_fresh_snippets",
            lambda ticker, since, tavily_key, timeout: "RAW")
        monkeypatch.setattr(freshness, "classify_delta",
                            lambda *a, **k: None)
        out = freshness.run_freshness_search("CRWD", None)
        assert out["snippets"] == "RAW"
        assert out["material"] is None


# ── 3. Citation registry persistence (round-trip) ────────────────────────────

_REGISTRY = [
    {"ref_id": 1, "claim": "AWS grew 37%", "source_name": "Q2 release",
     "source_type": "web_search", "date": "2026-07-31", "speaker": "",
     "quote": "AWS revenue grew 37% year over year", "url": "https://x/1",
     "section": "2B", "verified": True},
    {"ref_id": 2, "claim": "capex raised", "source_name": "Transcript",
     "source_type": "web_search", "date": "", "speaker": "CFO",
     "quote": "we are raising FY26 capex to $220bn", "url": "https://x/2",
     "section": "2E", "verified": False},
]


class TestCitationRegistryPersistence:

    def test_save_get_round_trip(self, tmp_archive):
        assert run_archive.save_citation_registry(
            "AMZN", "hash_a", _REGISTRY) is True
        got = run_archive.get_citation_registry("AMZN", "hash_a")
        assert got == _REGISTRY

    def test_get_absent_returns_none(self, tmp_archive):
        assert run_archive.get_citation_registry("AMZN", "nope") is None

    def test_distinct_hashes_isolated(self, tmp_archive):
        run_archive.save_citation_registry("AMZN", "h1", _REGISTRY)
        run_archive.save_citation_registry("AMZN", "h2", [_REGISTRY[0]])
        assert run_archive.get_citation_registry("AMZN", "h1") == _REGISTRY
        assert run_archive.get_citation_registry("AMZN", "h2") == [_REGISTRY[0]]

    def test_upsert_latest_wins(self, tmp_archive):
        run_archive.save_citation_registry("AMZN", "h", _REGISTRY)
        run_archive.save_citation_registry("AMZN", "h", [_REGISTRY[0]])
        got = run_archive.get_citation_registry("AMZN", "h")
        assert got == [_REGISTRY[0]]

    def test_ticker_case_insensitive(self, tmp_archive):
        run_archive.save_citation_registry("amzn", "h", _REGISTRY)
        assert run_archive.get_citation_registry("AMZN", "h") == _REGISTRY
        assert run_archive.get_citation_registry("AmZn", "h") == _REGISTRY

    def test_corrupt_json_returns_none(self, tmp_archive):
        """A bad blob must make the caller rebuild (pre-R2 path), never
        crash the run."""
        run_archive.ensure_schema()
        conn = sqlite3.connect(tmp_archive)
        conn.execute(
            "INSERT INTO citation_registry "
            "(ticker, text_hash, registry_json, extracted_at) "
            "VALUES (?, ?, ?, ?)",
            ["CRWD", "bad", "{not json", "2026-08-24T00:00:00"])
        conn.commit()
        conn.close()
        assert run_archive.get_citation_registry("CRWD", "bad") is None

    def test_non_list_registry_coerced(self, tmp_archive):
        assert run_archive.save_citation_registry(
            "AMZN", "h", "not a list") is True
        assert run_archive.get_citation_registry("AMZN", "h") == []

    def test_empty_key_rejected(self, tmp_archive):
        assert run_archive.save_citation_registry("", "h", _REGISTRY) is False
        assert run_archive.save_citation_registry("AMZN", "", _REGISTRY) is False
        assert run_archive.get_citation_registry("", "h") is None
        assert run_archive.get_citation_registry("AMZN", "") is None

    def test_persisted_blob_matches_rebuild_contract(self, tmp_archive):
        """The persisted value is what the pure-cache path serves AS its
        citation_registry — it must be the raw LLM-extracted list (what a
        rebuild would return), not seed-decorated entries."""
        run_archive.save_citation_registry("MSFT", "h", _REGISTRY)
        served = run_archive.get_citation_registry("MSFT", "h")
        assert isinstance(served, list)
        assert all(isinstance(e, dict) and "ref_id" in e for e in served)


# ── 4. R2 helpers ────────────────────────────────────────────────────────────

class TestR2Helpers:

    def test_hash_research_text_deterministic_and_distinct(self):
        h1 = dr._hash_research_text("REPORT TEXT")
        assert h1 == dr._hash_research_text("REPORT TEXT")
        assert h1 != dr._hash_research_text("REPORT TEXT.")
        assert len(h1) == 64                      # sha256 hex digest
        assert dr._hash_research_text("") == dr._hash_research_text(None)

    def test_citation_persist_default_on(self, monkeypatch):
        monkeypatch.delenv("CITATION_REGISTRY_PERSIST", raising=False)
        assert dr._citation_persist_enabled() is True

    def test_citation_persist_kill_switch(self, monkeypatch):
        for val in ("0", "false", "FALSE", "no", "off", ""):
            monkeypatch.setenv("CITATION_REGISTRY_PERSIST", val)
            assert dr._citation_persist_enabled() is False, val
        for val in ("true", "1", "yes"):
            monkeypatch.setenv("CITATION_REGISTRY_PERSIST", val)
            assert dr._citation_persist_enabled() is True, val

    def test_hash_keys_align_with_archive_contract(self, tmp_archive):
        """End-to-end key shape: save under _hash_research_text(text) and
        read back with the hash of the IDENTICAL text — the exact flow the
        pure-cache path runs (archived text hashes to the persisted key)."""
        text = "## 1. EXECUTIVE SUMMARY\nCloud revenue accelerated..."
        key = dr._hash_research_text(text)
        run_archive.save_citation_registry("BABA", key, _REGISTRY)
        assert run_archive.get_citation_registry(
            "BABA", dr._hash_research_text(text)) == _REGISTRY
        assert run_archive.get_citation_registry(
            "BABA", dr._hash_research_text(text + "\n")) is None

    def test_key_canonicalization_strips_addendum_stack(self):
        """Archived text accumulates a LATEST DEVELOPMENTS addendum on every
        consecutive re-run; the persistence key must canonicalize to the
        base text so run N+1 hits what run N persisted."""
        base = "## 1. EXECUTIVE SUMMARY\nBase research body..."
        addendum = (run_archive.LATEST_DEV_ADDENDUM_MARKER
                    + "2026-08-24)\n- nothing material; report current")
        k0 = dr._hash_research_text(
            base.split(run_archive.LATEST_DEV_ADDENDUM_MARKER)[0])
        k1 = dr._hash_research_text(
            (base + addendum).split(run_archive.LATEST_DEV_ADDENDUM_MARKER)[0])
        k2 = dr._hash_research_text(
            (base + addendum + addendum).split(
                run_archive.LATEST_DEV_ADDENDUM_MARKER)[0])
        assert k0 == k1 == k2
        assert k0 != dr._hash_research_text(base + " amended")


# ── 5. News-supplement shape coercion ────────────────────────────────────────

class TestNewsSupplementShapes:
    """`_build_news_supplement` must tolerate BOTH shapes the news agent
    emits. Live bug 2026-08-24 (first one_search delta gate, BABA): the
    agent's `top_headlines` is list[str] ("[LABEL] title") while dated
    entries live in `scored_articles`; the old code called `.get()` on the
    strings and the AttributeError killed the WHOLE delta pass (fall-through
    to a full ~9-min research). Any delta run with article_count > 0 hit it.
    client=None drives the LLM-call fallback branch (no network)."""

    _STR_HEADLINES = ["[BEARISH] Law firm urges investors to act",
                      "[NEUTRAL] Co raises $10B for AI expansion"]

    def test_scored_articles_dicts_are_preferred_and_date_filtered(self):
        ns = {"article_count": 3, "signal": "BEARISH", "composite_score": -0.4,
              "volume_spike": False, "top_headlines": self._STR_HEADLINES,
              "scored_articles": [
                  {"title": "New item", "date": "2026-08-24", "score": -0.2,
                   "label": "BEARISH"},
                  {"title": "Pre-cache item", "date": "2026-08-01", "score": 0.1,
                   "label": "NEUTRAL"}],
              "analysis_note": "note"}
        out = dr._build_news_supplement(
            client=None, model_name="x", news_sentiment=ns, ticker="BABA",
            as_of="2026-08-24", since_date="2026-08-20")
        assert "RECENT NEWS SUPPLEMENT" in out
        assert "New item" in out
        assert "Pre-cache item" not in out          # dated before cache date
        assert "New articles: 1" in out

    def test_string_only_headlines_no_crash(self):
        """The exact live shape that crashed: strings only, no dicts."""
        ns = {"article_count": 2, "signal": "NEUTRAL", "composite_score": 0.0,
              "volume_spike": False, "top_headlines": self._STR_HEADLINES,
              "scored_articles": [], "analysis_note": ""}
        out = dr._build_news_supplement(
            client=None, model_name="x", news_sentiment=ns, ticker="BABA",
            as_of="2026-08-24", since_date="2026-08-20")
        assert "Law firm urges investors to act" in out   # title parsed out
        assert "undated" in out                            # kept, not dropped

    def test_malformed_inputs_return_empty(self):
        assert dr._build_news_supplement(
            client=None, model_name="x", news_sentiment="stray string",
            ticker="T", as_of="d") == ""
        assert dr._build_news_supplement(
            client=None, model_name="x", news_sentiment={"article_count": 0},
            ticker="T", as_of="d") == ""
        assert dr._build_news_supplement(
            client=None, model_name="x", news_sentiment=None,
            ticker="T", as_of="d") == ""
