"""Sell-side thesis reaches the portfolio manager, as a view to weigh."""
from __future__ import annotations

import pytest

from src.agents.portfolio_manager import (
    _PM_RATIONALE_SYSTEM_PROMPT,
    _analyst_thesis_block,
)
from src.memory.analyst_basis import get_analyst_thesis

SAMPLE = {
    "points": ["Orderbook up 14% YoY to S$35.7bn.", "PATMI +27% YoY."],
    "catalysts": ["Doubling of 155mm ammunition capacity."],
    "risks": ["Lumpy contract timing."],
    "house": "Phillip Securities Research",
    "analyst": "Paul Chew",
    "as_of": "17 August 2026",
    "rating": "BUY",
    "price_target": "13.0",
}


def test_block_renders_thesis_catalysts_and_risks(monkeypatch):
    import src.memory.analyst_basis as ab
    monkeypatch.setattr(ab, "get_analyst_thesis", lambda _t: SAMPLE)
    block = _analyst_thesis_block("S63.SI")
    assert "Phillip Securities Research" in block
    assert "BUY" in block and "TP 13.0" in block
    assert "[Thesis]" in block and "[Catalysts]" in block and "[Risks]" in block
    assert "Orderbook up 14% YoY" in block


def test_block_is_capped(monkeypatch):
    """The whole note is already available to deep research; the decision
    needs the argument, not the document."""
    import src.memory.analyst_basis as ab
    big = dict(SAMPLE, points=[f"point {i}" for i in range(20)],
               catalysts=[f"cat {i}" for i in range(20)],
               risks=[f"risk {i}" for i in range(20)])
    monkeypatch.setattr(ab, "get_analyst_thesis", lambda _t: big)
    block = _analyst_thesis_block("S63.SI")
    assert block.count("[Thesis]") == 4
    assert block.count("[Catalysts]") == 3
    assert block.count("[Risks]") == 3


def test_absent_report_is_explicit_not_empty(monkeypatch):
    """An empty string would silently look like a thesis with no content."""
    import src.memory.analyst_basis as ab
    monkeypatch.setattr(ab, "get_analyst_thesis", lambda _t: None)
    assert "no deposited analyst report" in _analyst_thesis_block("ZZZZ")


def test_store_failure_never_blocks_a_decision(monkeypatch):
    import src.memory.analyst_basis as ab

    def _boom(_t):
        raise RuntimeError("store down")

    monkeypatch.setattr(ab, "get_analyst_thesis", _boom)
    assert "no deposited analyst report" in _analyst_thesis_block("S63.SI")


def test_prompt_requires_engagement_not_adoption():
    """A sell-side thesis is another analyst's argument on a stated date.

    Without this rule the model restates it, and the decision silently
    becomes the broker's rather than ours.
    """
    p = _PM_RATIONALE_SYSTEM_PROMPT
    assert "Analyst-thesis rule" in p
    assert "not a conclusion to adopt" in p
    assert "never restate it as our own" in p


def test_accessor_is_market_agnostic():
    """One shape for US, HKEX and SGX — no per-market branch."""
    for ticker in ("S63.SI", "BABA", "00700.HK"):
        t = get_analyst_thesis(ticker)
        if t is None:
            continue                       # nothing deposited on this machine
        assert isinstance(t["points"], list)
        assert {"points", "catalysts", "risks", "house", "as_of"} <= set(t)


def test_accessor_returns_none_when_nothing_deposited():
    assert get_analyst_thesis("NOSUCHTICKER") is None
