"""Stage 2 gates — research -> valuation quality (Workstream C: C1, C3-C6).

FORWARD tests (new behavior works):
  C1 — _run_extractor_fanout is the shared fan-out the cache-hit and delta
       paths now call; precomputed entries are reused without an LLM call.
  C3 — extractor exceptions and still-empty outputs are recorded in
       `failures` (silent {}s become diagnosable); retry-on-empty fires one
       sharper-prompt second attempt for eligible extractors.
  C4 — live-search floor raised 1 -> 4; _research_evidence_state tags
       below-floor live runs as DEGRADED; _coverage_nudge demands more
       searches and re-issues the report.
  C5 — _append_continuation repairs stop_reason=max_tokens truncation by
       appending one tool-free continuation.
  C6 — _check_research_financial_consistency flags research claims that
       contradict FMP line items (growth / margin / magnitude) and stays
       silent when claims are consistent or unparseable.

BACKWARD tests (old behavior unbroken):
  Pre-change archive rows (old `runs` shape — no extractor keys, no
  divergences) still load via get_recent_research with sections parsed.
"""

import sqlite3
from datetime import datetime, timedelta

import pytest

import src.agents.industry.deep_research as dr
import src.memory.run_archive as run_archive


# ── Fakes for the Anthropic SDK surface used by the helpers ──────────────────

class _Citation:
    def __init__(self, url, title="t", cited_text="c",
                 ctype="web_search_result_location"):
        self.type = ctype
        self.url = url
        self.title = title
        self.cited_text = cited_text


class _Block:
    def __init__(self, text=None, btype="text", name=None, citations=None):
        if text is not None:
            self.text = text          # blocks without text must NOT have .text
        self.type = btype
        if name is not None:
            self.name = name
        self.citations = citations or []


class _Resp:
    def __init__(self, content, stop_reason="end_turn"):
        self.content = content
        self.stop_reason = stop_reason


class _Client:
    """Canned-response stand-in for anthropic.Anthropic."""
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []
        outer = self

        class _Messages:
            def create(self, **kw):
                outer.calls.append(kw)
                if not outer._responses:
                    raise RuntimeError("no canned response left")
                return outer._responses.pop(0)

        self.messages = _Messages()


# ── C4: live-data gate ────────────────────────────────────────────────────────

def test_c4_live_search_floor_is_4():
    assert dr._MIN_LIVE_SEARCHES == 4


def test_c4_evidence_state_live():
    is_live, degraded, label = dr._research_evidence_state("anthropic_web", 4)
    assert is_live and not degraded
    assert "LIVE WEB DATA" in label


def test_c4_evidence_state_degraded_below_floor():
    is_live, degraded, label = dr._research_evidence_state("anthropic_web", 2)
    assert degraded and not is_live
    assert "DEGRADED" in label


def test_c4_evidence_state_cached_is_not_degraded():
    # Cache hits ran their searches when originally produced — never degraded.
    is_live, degraded, label = dr._research_evidence_state("anthropic_web_cached", 0)
    assert not is_live and not degraded
    assert "TRAINING DATA" in label


def test_c4_evidence_state_qwen_agent_search_is_live_not_degraded():
    # Qwen's streaming path returns a proxy count of 1 (real count is not
    # observable). It must NOT be degraded on that proxy — agent search is
    # live web data.
    is_live, degraded, label = dr._research_evidence_state("qwen_web", 1)
    assert is_live and not degraded
    assert "LIVE WEB DATA" in label


def test_c4_coverage_nudge_reissues_report_and_counts_searches():
    nudge_resp = _Resp([
        _Block(btype="server_tool_use", name="web_search"),
        _Block(btype="server_tool_use", name="web_search"),
        _Block(btype="server_tool_use", name="web_search"),
        _Block(btype="server_tool_use", name="web_search"),
        _Block(text="RE-ISSUED full report",
               citations=[_Citation("https://new.example/a")]),
    ])
    client = _Client([nudge_resp])
    seen, cits = {"https://old.example"}, []

    text, n = dr._coverage_nudge(
        client, "model", "sys", "human", [object()],
        "thin report", 1, seen, cits, "TEST",
    )

    assert text == "RE-ISSUED full report"          # re-issue supersedes
    assert n == 5                                    # 1 prior + 4 new
    assert cits and cits[0]["url"] == "https://new.example/a"
    # The nudge keeps the search tool available (it must search more)
    assert client.calls[0]["tools"] == [dr._WEB_SEARCH_TOOL]


def test_c4_coverage_nudge_empty_keeps_original():
    nudge_resp = _Resp([_Block(btype="server_tool_use", name="web_search")])
    client = _Client([nudge_resp])

    text, n = dr._coverage_nudge(
        client, "model", "sys", "human", [object()],
        "original report", 1, set(), [], "TEST",
    )

    assert text == "original report"                 # never lose research
    assert n == 2                                    # searches still counted


def test_c4_coverage_nudge_api_error_keeps_original():
    class _Boom:
        def create(self, **kw):
            raise RuntimeError("api down")

    client = _Client([])
    client.messages = _Boom()

    text, n = dr._coverage_nudge(
        client, "model", "sys", "human", [object()],
        "original report", 1, set(), [], "TEST",
    )

    assert text == "original report"
    assert n == 1


# ── C5: truncation repair ─────────────────────────────────────────────────────

def test_c5_continuation_appended_when_truncated():
    cont_resp = _Resp([_Block(text="2F continued content")])
    client = _Client([cont_resp])
    seen, cits = set(), []

    out = dr._append_continuation(
        client, "model", "sys", "human", [object()],
        "sections 2A through 2E", seen, cits, "TEST",
    )

    assert out == "sections 2A through 2E\n\n2F continued content"
    assert len(client.calls) == 1
    assert "tools" not in client.calls[0]            # continuation only writes
    msgs = client.calls[0]["messages"]
    assert msgs[1]["role"] == "assistant"            # prior turn carried over


def test_c5_keeps_truncated_text_when_continuation_fails():
    class _Boom:
        def create(self, **kw):
            raise RuntimeError("api down")

    client = _Client([])
    client.messages = _Boom()

    out = dr._append_continuation(
        client, "model", "sys", "human", [object()],
        "truncated report", set(), [], "TEST",
    )

    assert out == "truncated report"                 # some report beats none


def test_c5_noop_when_text_empty():
    client = _Client([])
    out = dr._append_continuation(
        client, "model", "sys", "human", [object()],
        "   ", set(), [], "TEST",
    )
    assert out == "   "
    assert client.calls == []                        # no wasted call


# ── C6: deterministic research <-> books consistency ─────────────────────────

_RAW = {
    "FY2024": {"revenue": 100.0, "net_income": 10.0},
    "FY2025": {"revenue": 129.0, "net_income": 12.9},
}


def test_c6_divergent_growth_flagged():
    sections = {"2a": "Revenue grew 55% year-over-year driven by demand."}
    flags = dr._check_research_financial_consistency(sections, _RAW, "T")
    assert "revenue_growth" in flags
    assert flags["revenue_growth"]["books"] == pytest.approx(0.29)
    assert flags["revenue_growth"]["divergence_pp"] > 5


def test_c6_consistent_growth_not_flagged():
    sections = {"2a": "Revenue grew 29% year-over-year."}
    assert dr._check_research_financial_consistency(sections, _RAW, "T") == {}


def test_c6_divergent_margin_flagged():
    sections = {"2a": "Net margin of 35% reflects strong leverage."}
    flags = dr._check_research_financial_consistency(sections, _RAW, "T")
    assert "net_margin" in flags
    assert flags["net_margin"]["divergence_pp"] > 3


def test_c6_consistent_margin_not_flagged():
    sections = {"2a": "Net margin of 10% held steady."}
    assert dr._check_research_financial_consistency(sections, _RAW, "T") == {}


def test_c6_revenue_magnitude_flagged_when_no_scale_fits():
    raw_usd = {
        "FY2024": {"revenue": 100_000_000.0, "net_income": 10_000_000.0},
        "FY2025": {"revenue": 129_000_000.0, "net_income": 12_900_000.0},
    }
    sections = {"2a": "The company reported revenue of $500 million."}
    flags = dr._check_research_financial_consistency(sections, raw_usd, "T")
    assert "revenue_magnitude" in flags


def test_c6_revenue_magnitude_allows_scale_ambiguity():
    # books "129" may be $129M in millions-units — must not flag
    sections = {"2a": "The company reported revenue of $129 million."}
    assert dr._check_research_financial_consistency(sections, _RAW, "T") == {}


def test_c6_silent_when_inputs_missing():
    s = {"2a": "Revenue grew 55% year-over-year."}
    assert dr._check_research_financial_consistency(s, None, "T") == {}
    assert dr._check_research_financial_consistency({}, _RAW, "T") == {}
    assert dr._check_research_financial_consistency(
        {"2a": "No numbers here."}, _RAW, "T") == {}


# ── C1/C3: shared extractor fan-out ──────────────────────────────────────────

def test_c1_c3_extractor_fanout_precomputed_retry_and_failures(monkeypatch):
    import src.agents.industry.sector_prompts as sp

    calls = []

    def fake_needs(name, sector, profile_name, ticker=None):
        return name in {"dcf_calibration", "bank_metrics",
                        "reit_metrics", "saas_metrics"}

    def fake_dcf(*a, **k):
        raise AssertionError("dcf_calibration must be reused from precomputed")

    def fake_saas(c, m, s, r, t):
        calls.append(("saas", ""))
        return {"net_dollar_retention": 1.2}

    _bank_outputs = [{}, {"cet1_ratio": 0.13}]    # empty 1st, data on retry

    def fake_bank(c, m, s, r, t, retry_directive=""):
        calls.append(("bank", retry_directive))
        return _bank_outputs.pop(0) if _bank_outputs else {"cet1_ratio": 0.13}

    def fake_reit(c, m, s, r, t, retry_directive=""):
        calls.append(("reit", retry_directive))
        raise RuntimeError("boom")

    monkeypatch.setattr(sp, "needs_extractor", fake_needs)
    monkeypatch.setattr(dr, "_extract_dcf_calibration", fake_dcf)
    monkeypatch.setattr(dr, "_extract_saas_metrics", fake_saas)
    monkeypatch.setattr(dr, "_extract_bank_metrics", fake_bank)
    monkeypatch.setattr(dr, "_extract_reit_metrics", fake_reit)

    results, failures = dr._run_extractor_fanout(
        sdk_client=None, synthesis_model="m",
        sections={"2a": "x"}, final_report="r",
        ticker="TEST", sector="Financials",
        profile_name="Money Center Bank", raw_financials={},
        precomputed={"dcf_calibration": {"wacc": 0.10}},
    )

    # C1: precomputed reused, gated extractors populated
    assert results["dcf_calibration"] == {"wacc": 0.10}
    assert results["saas_metrics"] == {"net_dollar_retention": 1.2}
    # C3: retry-on-empty recovered bank_metrics on the sharper second attempt
    assert results["bank_metrics"] == {"cet1_ratio": 0.13}
    bank_calls = [c for c in calls if c[0] == "bank"]
    assert len(bank_calls) == 2
    assert bank_calls[1][1] == dr._EXTRACTOR_RETRY_DIRECTIVE
    # C3: exception recorded as failure; recovered bank NOT listed
    assert results["reit_metrics"] == {}
    pairs = {(f["extractor"], f["stage"]) for f in failures}
    assert ("reit_metrics", "exception") in pairs
    assert not any(f["extractor"] == "bank_metrics" for f in failures)
    assert not any(f["extractor"] == "dcf_calibration" for f in failures)


def test_c3_empty_precomputed_falls_through_to_live_extraction(monkeypatch):
    """An upstream extraction that returned nothing (e.g. transient API
    failure) must NOT be reused as-is — the fan-out gets one live attempt
    so the value can still be recovered (and failures stay recorded)."""
    import src.agents.industry.sector_prompts as sp

    calls = []

    monkeypatch.setattr(
        sp, "needs_extractor",
        lambda name, sector, profile_name, ticker=None: name == "dcf_calibration",
    )

    def fake_dcf(c, m, s, t, retry_directive=""):
        calls.append(retry_directive)
        return {"wacc": 0.09}

    monkeypatch.setattr(dr, "_extract_dcf_calibration", fake_dcf)

    results, failures = dr._run_extractor_fanout(
        sdk_client=None, synthesis_model="m",
        sections={"2a": "x"}, final_report="r",
        ticker="TEST", sector="Tech", profile_name="SaaS",
        raw_financials={},
        precomputed={"dcf_calibration": {}},   # upstream failed -> empty
    )

    assert calls == [""]                        # one live attempt happened
    assert results["dcf_calibration"] == {"wacc": 0.09}
    assert failures == []


def test_c3_still_empty_after_retry_recorded_as_empty_failure(monkeypatch):
    import src.agents.industry.sector_prompts as sp

    monkeypatch.setattr(
        sp, "needs_extractor",
        lambda name, sector, profile_name, ticker=None: name == "bank_metrics",
    )
    monkeypatch.setattr(
        dr, "_extract_bank_metrics",
        lambda c, m, s, r, t, retry_directive="": {},   # empty both attempts
    )

    results, failures = dr._run_extractor_fanout(
        sdk_client=None, synthesis_model="m",
        sections={}, final_report="", ticker="TEST",
        sector="Financials", profile_name="Money Center Bank",
        raw_financials={},
    )

    assert results["bank_metrics"] == {}
    assert failures == [{
        "extractor": "bank_metrics",
        "stage":     "empty",
        "detail":    "no usable values after all attempts",
    }]


# ── C3 helper semantics ───────────────────────────────────────────────────────

def test_c3_is_empty_extraction():
    assert dr._is_empty_extraction(None)
    assert dr._is_empty_extraction([])
    assert dr._is_empty_extraction({})
    assert dr._is_empty_extraction({"a": None, "b": "", "c": [], "d": {}})
    # evidence-only payloads carry no usable KPI values
    assert dr._is_empty_extraction({"evidence": "some text", "_model": "qwen"})
    assert not dr._is_empty_extraction({"evidence": "x", "cet1_ratio": 0.13})
    assert not dr._is_empty_extraction([{"asset": "X"}])


# ── BACKWARD: pre-change archive rows still load ─────────────────────────────

def test_backward_old_archive_row_loads_without_new_keys(tmp_path, monkeypatch):
    """An archive row written BEFORE Stage 2 (no extractor outputs, no
    divergences stored) must still load through get_recent_research —
    the cache-hit path builds everything else from deep_research_text."""
    db_path = tmp_path / "run_archive.db"
    monkeypatch.setattr(run_archive, "DB_PATH", str(db_path))

    conn = sqlite3.connect(str(db_path))
    conn.executescript(run_archive._DDL)
    conn.execute(
        """INSERT INTO runs
           (run_id, run_at, analysis_date, sector, tickers, model_name,
            pipeline_version, research_tier, deep_research_text)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "old-run-1",
            (datetime.now() - timedelta(days=1)).isoformat(),
            "2026-08-07",
            "Tech",
            '["ZZTEST"]',
            "qwen3.6-plus",
            "2.0",
            "anthropic_web",
            "2A. Financial Performance.\nRevenue grew 29%.\n\n"
            "2F. KPI Block.\nNRR 120%.",
        ),
    )
    conn.commit()
    conn.close()

    row = run_archive.get_recent_research("ZZTEST", max_age_days=14)
    assert row is not None
    assert row["run_id"] == "old-run-1"
    assert row["research_tier"] == "anthropic_web"
    assert "Revenue grew 29%" in row["deep_research_text"]
    # Sections parsed from the stored text (no sections column existed)
    assert row["deep_research_sections"]
    assert any("2a" in k.lower() for k in row["deep_research_sections"])
    # New Stage 2 keys are NOT part of the archive contract — consumers
    # rebuild them at read time (C1 fan-out) instead of requiring them.
    assert "saas_metrics" not in row
    assert "extractor_failures" not in row


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
