"""
tests/test_m2_addendum_delta.py
===============================
M2 Track A3 + A4 — LATEST DEVELOPMENTS addendum on reused research and the
Qwen-routed delta provider fix.

A3: pure-cache and delta runs append a dated "LATEST DEVELOPMENTS" addendum
    to the archived FULL TEXT — never to deep_research_sections, so the C2
    extractor hash stays stable. Both section parsers must strip the marker
    identically (lock-step), so the NEXT run re-parses the archived text
    into the same sections.
A4: Qwen-routed tickers (base_url set) run their delta pass via bounded
    native qwen_web_search calls instead of the Anthropic server web_search
    tool through the DashScope base_url (provider mismatch — the bug that
    silently killed every Qwen delta and forced full research). Anthropic-
    routed tickers keep the server-tool path.
"""
from types import SimpleNamespace

import pytest

import src.agents.industry.deep_research as dr
import src.memory.run_archive as run_archive
from src.memory.run_archive import LATEST_DEV_ADDENDUM_MARKER


# ── tmp-archive fixture ───────────────────────────────────────────────────────

@pytest.fixture
def tmp_archive(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db_path = str(tmp_path / "run_archive.db")
    monkeypatch.setattr(run_archive, "DB_PATH", db_path)
    monkeypatch.setattr(run_archive, "_sqlite_schema_paths", set())
    return db_path


# ── A3: addendum builder ──────────────────────────────────────────────────────

def test_builder_material_events():
    delta = {
        "material": True,
        "events": [
            {"headline": "Guidance raised", "date": "2026-08-14",
             "relevance": "beats prior assumption"},
            {"headline": "", "date": "2026-08-15", "relevance": "skipped"},
        ],
        "verdict": "Prior thesis strengthened",
    }
    out = dr._build_latest_developments_addendum(delta, "2026-08-20")
    assert out.startswith("## LATEST DEVELOPMENTS (as of 2026-08-20)")
    assert "- 2026-08-14: Guidance raised — beats prior assumption" in out
    assert "skipped" not in out                    # empty-headline event dropped
    assert "Verdict: Prior thesis strengthened" in out


def test_builder_not_material_says_so_explicitly():
    out = dr._build_latest_developments_addendum(
        {"material": False, "events": [], "verdict": "still current"},
        "2026-08-20",
    )
    assert "No material developments" in out
    assert "remains current" in out


def test_builder_unclassified_states_outcome():
    out = dr._build_latest_developments_addendum(
        {"material": None, "events": [], "verdict": "no fresh results"},
        "2026-08-20",
    )
    assert "no classification" in out
    assert "no fresh results" in out


def test_builder_no_delta_returns_empty():
    assert dr._build_latest_developments_addendum(None, "2026-08-20") == ""
    assert dr._build_latest_developments_addendum({}, "2026-08-20") == ""


# ── A3: parser strip (lock-step, C2 hash stability) ──────────────────────────

_BASE_TEXT = (
    "2A. Financial Performance.\nRevenue grew 29%.\n\n"
    "2B. Competitive Landscape.\nCompetition is intense.\n"
)
_ADDENDUM = (
    "\n\n---\n\n## LATEST DEVELOPMENTS (as of 2026-08-20)\n\n"
    "- 2026-08-14: Guidance raised — beats prior assumption\n"
)


def test_both_parsers_strip_addendum_identically():
    plain_sections = dr._extract_sections(_BASE_TEXT)
    with_addendum = dr._extract_sections(_BASE_TEXT + _ADDENDUM)
    assert with_addendum == plain_sections

    re_parsed = run_archive._parse_sections_inline(_BASE_TEXT + _ADDENDUM)
    assert re_parsed == run_archive._parse_sections_inline(_BASE_TEXT)
    # and the two parsers agree with each other on the stripped result
    assert dict(re_parsed) == dict(plain_sections)


def test_hash_stable_across_addendum():
    s_plain = dr._extract_sections(_BASE_TEXT)
    s_appended = dr._extract_sections(_BASE_TEXT + _ADDENDUM)
    assert (dr._hash_research_sections(s_plain, "SaaS")
            == dr._hash_research_sections(s_appended, "SaaS"))


# ── A3: pure-cache run appends addendum, sections untouched ──────────────────

_CACHED_ROW = {
    "run_id": "cached-run-1",
    "run_at": "2026-08-19T10:00:00",
    "analysis_date": "2026-08-19",
    "age_days": 0.2,
    "research_tier": "qwen_web",
    "research_as_of": "2026-08-19",
    "deep_research_text": _BASE_TEXT,
    "deep_research_sections": dr._extract_sections(_BASE_TEXT),
}


def _patch_cache_env(monkeypatch, cached_row):
    monkeypatch.setattr(
        run_archive, "get_recent_research",
        lambda ticker, max_age_days=7, qualifying_tiers=None: dict(cached_row),
    )
    monkeypatch.setattr(
        dr, "_extract_citation_registry",
        lambda *a, **k: [{"ref_id": 1, "quote": "q" * 25, "url": "https://x"}],
    )
    persisted = {"dcf_calibration": {"wacc": 0.09, "notes": "ok"},
                 "saas_metrics": {"nrr_pct": 1.18}}
    monkeypatch.setattr(
        run_archive, "get_extractor_outputs",
        lambda ticker, h: {"results": persisted, "failures": []},
    )


def test_pure_cache_appends_addendum_and_keeps_sections_hash(
        tmp_archive, monkeypatch):
    _patch_cache_env(monkeypatch, _CACHED_ROW)
    delta = {"material": True,
             "events": [{"headline": "Guidance raised", "date": "2026-08-14",
                         "relevance": "beats prior assumption"}],
             "verdict": "Prior thesis strengthened"}

    out = dr._research_one_ticker(
        ticker="ZZTEST", sector="Tech", end_date="2026-08-20",
        anthropic_key="k", model_name="qwen3.6-plus",
        raw_financials={}, insider_summary="",
        base_url=None, synthesis_model="qwen3.6-plus", profile_name="SaaS",
        freshness_delta=delta,
    )

    assert out["cache_hit"] is True
    # Addendum rides on the archived full text...
    assert LATEST_DEV_ADDENDUM_MARKER in out["deep_research"]
    assert "Guidance raised" in out["deep_research"]
    # ...but sections are byte-identical → C2 hash unchanged
    assert out["deep_research_sections"] == _CACHED_ROW["deep_research_sections"]
    assert (dr._hash_research_sections(out["deep_research_sections"], "SaaS")
            == dr._hash_research_sections(
                _CACHED_ROW["deep_research_sections"], "SaaS"))
    # The NEXT run's re-parse of this archived text recovers the same sections
    assert (run_archive._parse_sections_inline(out["deep_research"])
            == _CACHED_ROW["deep_research_sections"])


def test_pure_cache_without_freshness_delta_has_no_addendum(
        tmp_archive, monkeypatch):
    _patch_cache_env(monkeypatch, _CACHED_ROW)
    out = dr._research_one_ticker(
        ticker="ZZTEST", sector="Tech", end_date="2026-08-20",
        anthropic_key="k", model_name="qwen3.6-plus",
        raw_financials={}, insider_summary="",
        base_url=None, synthesis_model="qwen3.6-plus", profile_name="SaaS",
    )
    assert LATEST_DEV_ADDENDUM_MARKER not in out["deep_research"]


# ── A4: delta provider selection ──────────────────────────────────────────────

class _FakeAnthropic:
    """Records messages.create kwargs; optionally fails on the Nth
    construction (to detect which branch built a client)."""
    created: list = []
    fail_on_construction: int | None = None   # 1-based
    response_text: str = ""
    create_exc: Exception | None = None

    def __init__(self, *a, **k):
        _FakeAnthropic.created.append(k)
        if (_FakeAnthropic.fail_on_construction is not None
                and len(_FakeAnthropic.created) == _FakeAnthropic.fail_on_construction):
            raise _FullPathSentinel("full research path reached")
        self.messages = self

    def create(self, **kwargs):
        _FakeAnthropic.created[-1]["_create_kwargs"] = kwargs
        if _FakeAnthropic.create_exc is not None:
            raise _FakeAnthropic.create_exc
        return SimpleNamespace(
            content=[SimpleNamespace(text=_FakeAnthropic.response_text)])


class _FullPathSentinel(RuntimeError):
    pass


_DELTA_ROW = dict(_CACHED_ROW, age_days=5.0)   # 3–14d → delta branch

_DELTA_RESPONSE = (
    "[2A] Company raised FY guidance at the Aug 14 print (Reuters, August 2026).\n"
    "[2B] NO CHANGE\n[2C] NO CHANGE\n[2D] NO CHANGE\n[2E] NO CHANGE\n[2F] NO CHANGE\n"
)


def _patch_delta_env(monkeypatch):
    monkeypatch.setattr(
        run_archive, "get_recent_research",
        lambda ticker, max_age_days=7, qualifying_tiers=None: dict(_DELTA_ROW),
    )
    monkeypatch.setattr(dr, "_extract_citation_registry", lambda *a, **k: [])
    monkeypatch.setattr(
        dr, "_extract_dcf_calibration",
        lambda c, m, s, t, retry_directive="": {"wacc": 0.09},
    )
    monkeypatch.setattr(
        dr, "_run_extractor_fanout",
        lambda c, m, sections, report, ticker, sector, profile_name,
               raw_financials, precomputed=None: (
            {"dcf_calibration": precomputed.get("dcf_calibration", {}),
             "saas_metrics": {"nrr_pct": 1.2}}, []),
    )
    monkeypatch.setattr(dr, "anthropic", SimpleNamespace(Anthropic=_FakeAnthropic))
    _FakeAnthropic.created = []
    _FakeAnthropic.fail_on_construction = None
    _FakeAnthropic.create_exc = None
    _FakeAnthropic.response_text = _DELTA_RESPONSE


def test_qwen_routed_delta_uses_native_search_no_tools(tmp_archive, monkeypatch):
    """base_url set → bounded qwen_web_search loop + tools-free synthesis."""
    _patch_delta_env(monkeypatch)
    queries = []

    def fake_qwen(prompt, **kwargs):
        queries.append(prompt)
        return "2026-08-14: ZZTEST raised FY guidance (Reuters)."

    import src.research_ideas.complacency.web_research as wr
    monkeypatch.setattr(wr, "qwen_web_search", fake_qwen)

    out = dr._research_one_ticker(
        ticker="ZZTEST", sector="Tech", end_date="2026-08-20",
        anthropic_key="k", model_name="qwen3.6-plus",
        raw_financials={}, insider_summary="",
        base_url="https://dashscope.example/v1",
        synthesis_model="qwen3-max", profile_name="SaaS",
    )

    assert len(queries) == dr._DELTA_MAX_SEARCHES          # bounded loop
    assert all("since 2026-08-19" in q for q in queries)   # date-anchored
    # first client that actually got a create() call = the delta client
    # (a later client is constructed for the empty news supplement)
    create_kwargs = next(
        k["_create_kwargs"] for k in _FakeAnthropic.created
        if "_create_kwargs" in k
    )
    assert "tools" not in create_kwargs                    # tools-free synthesis
    assert out["research_tier"] == "archive_news_delta"
    assert out["research_as_of"] == "2026-08-20"           # delta refreshes content
    assert "DELTA UPDATE" in out["deep_research_sections"]["2a"]
    assert "raised FY guidance" in out["deep_research"]


def test_qwen_delta_empty_searches_fall_through_to_full(tmp_archive, monkeypatch):
    """Nothing usable from Qwen → no unverified delta; the run falls through
    to full research (here detected by the second Anthropic client
    construction raising the sentinel)."""
    _patch_delta_env(monkeypatch)
    import src.research_ideas.complacency.web_research as wr
    monkeypatch.setattr(wr, "qwen_web_search", lambda *a, **k: None)
    company_calls = []
    monkeypatch.setattr(
        dr, "_fetch_company_name",
        lambda t: company_calls.append(t) or t,
    )
    _FakeAnthropic.fail_on_construction = 2   # 1st = delta client, 2nd = full path

    with pytest.raises(_FullPathSentinel):
        dr._research_one_ticker(
            ticker="ZZTEST", sector="Tech", end_date="2026-08-20",
            anthropic_key="k", model_name="qwen3.6-plus",
            raw_financials={}, insider_summary="",
            base_url="https://dashscope.example/v1",
            synthesis_model="qwen3-max", profile_name="SaaS",
        )
    # The sentinel fires when the FULL path builds its client — reaching it
    # is the proof of fall-through. Two client constructions (delta + full
    # path), zero create() calls: the empty-snippets raise precedes any LLM
    # synthesis in the delta branch, and the full path dies in its
    # constructor here.
    assert company_calls == ["ZZTEST"]
    assert len(_FakeAnthropic.created) == 2
    assert all("_create_kwargs" not in k for k in _FakeAnthropic.created)


def test_anthropic_routed_delta_keeps_server_tool(tmp_archive, monkeypatch):
    """base_url None → unchanged server-side web_search tool path."""
    _patch_delta_env(monkeypatch)
    # Qwen must NOT be consulted on the Anthropic route
    import src.research_ideas.complacency.web_research as wr
    monkeypatch.setattr(
        wr, "qwen_web_search",
        lambda *a, **k: pytest.fail("qwen_web_search must not run on the "
                                    "Anthropic-routed delta"),
    )

    out = dr._research_one_ticker(
        ticker="ZZTEST", sector="Tech", end_date="2026-08-20",
        anthropic_key="k", model_name="claude-sonnet-4-6",
        raw_financials={}, insider_summary="",
        base_url=None, synthesis_model="claude-sonnet-4-6", profile_name="SaaS",
    )

    create_kwargs = next(
        k["_create_kwargs"] for k in _FakeAnthropic.created
        if "_create_kwargs" in k
    )
    assert "tools" in create_kwargs
    assert create_kwargs["tools"][0]["name"] == "web_search"
    assert out["research_tier"] == "archive_news_delta"
    assert out["research_as_of"] == "2026-08-20"
    assert "DELTA UPDATE" in out["deep_research_sections"]["2a"]


def test_run_qwen_delta_searches_empty_on_provider_failure(monkeypatch):
    import src.research_ideas.complacency.web_research as wr

    def boom(prompt, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(wr, "qwen_web_search", boom)
    assert dr._run_qwen_delta_searches("ZZTEST", "2026-08-19") == ""


def test_build_delta_system_modes():
    server = dr._build_delta_system("2026", "2026-08-19")
    assert "Run exactly" in server and "SEARCH TARGETS" in server
    synth = dr._build_delta_system("2026", "2026-08-19",
                                   searches_already_run=True)
    assert "ALREADY been run" in synth
    assert "SEARCH TARGETS" not in synth
    # both share the merge contract
    for text in (server, synth):
        assert "[2A]" in text and "NO CHANGE" in text


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
