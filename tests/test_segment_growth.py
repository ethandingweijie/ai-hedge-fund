"""Tests for per-segment growth resolution.

The precedence itself is the contract: a growth rate management stated on the
call must beat one derived from the filing's own history, and a rate that came
from the group fallback must be labelled as such so a report can never present
a uniform assumption as a differentiated one.

No test here touches the network. The transcript tier is exercised by patching
the fetch and the LLM call.
"""
import json

import pytest

from src.agents.analysis import segment_growth as sg


SEGS = [{"name": "Family of Apps"}, {"name": "Reality Labs"}]


class _Seg:
    def __init__(self, name, growth, basis="reported", evidence=""):
        self.name, self.growth = name, growth
        self.basis, self.evidence = basis, evidence


class _Out:
    def __init__(self, segments):
        self.segments = segments


@pytest.fixture(autouse=True)
def _isolate_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(sg, "_CACHE_DIR", tmp_path / "sgcache")


def _patch_transcript(monkeypatch, segments, content="prepared remarks",
                      date="2026-07-30"):
    calls = {"n": 0}

    def _fetch(ticker):
        return {"content": content, "date": date}

    def _llm(*a, **k):
        calls["n"] += 1
        return _Out(segments)

    import src.tools.fmp_transcripts as ft
    monkeypatch.setattr(ft, "fetch_earnings_transcript", _fetch,
                        raising=False)
    monkeypatch.setattr(ft, "split_sections", lambda c: (c, ""),
                        raising=False)
    monkeypatch.setattr("src.utils.llm.call_llm_vision", _llm)
    return calls


# ── precedence ─────────────────────────────────────────────────────────────

def test_transcript_beats_filing_history(monkeypatch):
    _patch_transcript(monkeypatch, [_Seg("Family of Apps", 0.28)])
    out = sg.resolve_segment_growth(
        "META", SEGS, filing_growth={"Family of Apps": 0.11,
                                     "Reality Labs": 0.05},
        group_growth=0.09, state={})
    assert out["Family of Apps"]["growth"] == pytest.approx(0.28)
    assert out["Family of Apps"]["source"] == "earnings_transcript"
    # the segment management did not discuss falls to the filing, not the group
    assert out["Reality Labs"]["source"] == "filing_history"
    assert out["Reality Labs"]["growth"] == pytest.approx(0.05)


def test_group_consensus_is_last_and_labelled(monkeypatch):
    _patch_transcript(monkeypatch, [])
    out = sg.resolve_segment_growth("X", SEGS, filing_growth={},
                                    group_growth=0.09, state={})
    assert {v["source"] for v in out.values()} == {"group_consensus"}
    assert all(v["growth"] == pytest.approx(0.09) for v in out.values())


def test_segment_with_no_source_at_all_is_absent(monkeypatch):
    _patch_transcript(monkeypatch, [])
    out = sg.resolve_segment_growth("X", SEGS, filing_growth={},
                                    group_growth=None, state={})
    assert out == {}


def test_transcript_disabled_skips_the_llm(monkeypatch):
    calls = _patch_transcript(monkeypatch, [_Seg("Family of Apps", 0.28)])
    out = sg.resolve_segment_growth(
        "META", SEGS, filing_growth={"Family of Apps": 0.11}, state={},
        use_transcript=False)
    assert calls["n"] == 0
    assert out["Family of Apps"]["source"] == "filing_history"


# ── mapping discipline ─────────────────────────────────────────────────────

def test_unmatched_segment_name_is_dropped(monkeypatch):
    # "Hyperscale" is management vernacular that maps to no reportable segment.
    _patch_transcript(monkeypatch, [_Seg("Hyperscale", 0.60)])
    out = sg.resolve_segment_growth("NVDA", SEGS, filing_growth={}, state={})
    assert out == {}


def test_ambiguous_containment_match_is_dropped(monkeypatch):
    segs = [{"name": "Cloud"}, {"name": "Cloud Intelligence"}]
    _patch_transcript(monkeypatch, [_Seg("Cloud", 0.30)])
    out = sg.resolve_segment_growth("BABA", segs, filing_growth={}, state={})
    # "cloud" is contained in both -> two candidates -> refuse rather than guess
    assert "Cloud Intelligence" not in out


def test_growth_is_clamped_to_the_band(monkeypatch):
    _patch_transcript(monkeypatch, [_Seg("Family of Apps", 1.57),
                                    _Seg("Reality Labs", -0.90)])
    out = sg.resolve_segment_growth("X", SEGS, filing_growth={}, state={})
    assert out["Family of Apps"]["growth"] == pytest.approx(sg._GROWTH_CEIL)
    assert out["Reality Labs"]["growth"] == pytest.approx(sg._GROWTH_FLOOR)


# ── determinism ────────────────────────────────────────────────────────────

def test_extraction_is_cached_by_transcript_date(monkeypatch):
    calls = _patch_transcript(monkeypatch, [_Seg("Family of Apps", 0.28)])
    first = sg.transcript_segment_growth("META", ["Family of Apps"], state={})
    second = sg.transcript_segment_growth("META", ["Family of Apps"], state={})
    assert first == second
    assert calls["n"] == 1, "second call must be served from disk"


def test_empty_extraction_is_never_cached(monkeypatch):
    # A failed LLM call collapses to an empty default. Caching it would poison
    # every later run for the quarter with a result the model never produced.
    calls = _patch_transcript(monkeypatch, [])
    assert sg.transcript_segment_growth("X", ["A"], state={}) == {}
    assert sg.transcript_segment_growth("X", ["A"], state={}) == {}
    assert calls["n"] == 2


def test_fetch_failure_is_survivable(monkeypatch):
    import src.tools.fmp_transcripts as ft
    monkeypatch.setattr(ft, "fetch_earnings_transcript",
                        lambda t: (_ for _ in ()).throw(RuntimeError("503")),
                        raising=False)
    assert sg.transcript_segment_growth("X", ["A"], state={}) == {}


# ── prompt contract ────────────────────────────────────────────────────────

def test_prompt_carries_the_json_literal_dashscope_requires():
    # DashScope rejects json_mode unless the messages contain the word "json".
    assert "json" in sg._SYSTEM.lower()
    schema = sg._SYSTEM[sg._SYSTEM.index("{"):]
    assert json.loads(schema.replace('"guidance" | "reported"', '"reported"'))
