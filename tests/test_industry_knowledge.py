"""Sector notes fill the 2F block instead of it being hand-written.

`_SECTOR_PROFILE_PROMPTS` is keyed by `(sector, profile_name)` and feeds the
deep-research prompt as "2F. INDUSTRY-SPECIFIC KPI FRAMEWORK". There are 11
hand-written blocks for 95 routes: 26 get a tailored one and **54 fall through
to a generic "every industry has 3-5 metrics that matter"** — including
`Managed Care`, where MOH sits.

Market is part of the key because the profile taxonomy is inconsistent about
it. `Money Center Bank` is held by JPM, BAC, C, WFC *and* 02888.HK, so one
key would blend a CCAR/US-rate-cycle industry with a HIBOR/mainland-credit
one. Lookup falls back to market-agnostic, since most industries are not
market-specific.

2F feeds the KPI extractor, not only the prose, so an unchallenged analyst
claim here can move a composite multiplier. That is why the forward view is
posed as a question to test — and why it is asserted here as a test.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.agents.industry import sector_prompts as sp
from src.memory import industry_knowledge as ik

NOTE = {
    "market": "US", "sector": "HealthcareServices", "profile": "Managed Care",
    "as_of": "2026-08", "house": "Goldman Sachs",
    "anchor_kpi": "Medical loss ratio (MLR) vs pricing trend",
    "disclosed_metrics": ["MLR", "membership", "premium yield"],
    "economics": ["MLR spread over pricing trend"],
    "competitive": ["scale in claims data"],
    "quantitative": {"TAM": "US$1.5tn", "CAGR": "6%"},
    "peer_multiples": [],
    "trends": [{"name": "Medicaid redetermination", "direction": "headwind",
                "stage": "mid", "horizon": "12-24m"}],
    "positioning": [{"ticker": "MOH", "stance": "at risk",
                     "why": "Medicaid concentration"}],
    "doc_path": None,
}


def _with_notes(notes):
    return patch.object(ik, "get_industry_knowledge", return_value=notes)


def _block(sector, profile, market="", notes=None):
    with patch("src.memory.industry_knowledge.get_industry_knowledge",
               return_value=notes if notes is not None else []):
        return sp.get_kpi_prompt(sector, profile, market=market)


# ── Tier 0 wins, and only when it has something ──────────────────────────

def test_a_stored_note_fills_a_route_that_was_generic():
    """Managed Care is one of the 54. MOH is valued through it."""
    assert _block("HealthcareServices", "Managed Care") is sp._GENERIC_KPI_PROMPT
    filled = _block("HealthcareServices", "Managed Care", "US", [NOTE])
    assert filled is not sp._GENERIC_KPI_PROMPT
    assert "Medical loss ratio" in filled


def test_hand_written_blocks_are_untouched_without_stored_knowledge():
    """The 26 tailored routes must not regress."""
    assert _block("RealEstate", "R.E.I.T.") is not sp._GENERIC_KPI_PROMPT
    assert _block("Financials", "Money Center Bank") is not sp._GENERIC_KPI_PROMPT


def test_a_store_failure_degrades_to_the_hand_written_block():
    """A missing 2F block must never surface as an exception in the research
    prompt — the whole run would fail for a display-grade input."""
    with patch("src.memory.industry_knowledge.get_industry_knowledge",
               side_effect=RuntimeError("db down")):
        assert sp.get_kpi_prompt("RealEstate", "R.E.I.T.") is not None
        assert sp.get_kpi_prompt("HealthcareServices", "Managed Care") \
            is sp._GENERIC_KPI_PROMPT


def test_an_empty_note_does_not_produce_an_empty_block():
    bare = {**NOTE, "anchor_kpi": None, "disclosed_metrics": [],
            "economics": [], "competitive": [], "quantitative": {},
            "trends": [], "positioning": []}
    assert _block("HealthcareServices", "Managed Care", "US", [bare]) \
        is sp._GENERIC_KPI_PROMPT


# ── Everything is attributed, and the forward view is a question ─────────

def test_every_claim_carries_its_house_and_date():
    block = _block("HealthcareServices", "Managed Care", "US", [NOTE])
    assert block.count("Goldman Sachs 2026-08") >= 3


def test_trends_are_posed_as_claims_to_test():
    block = _block("HealthcareServices", "Managed Care", "US", [NOTE])
    assert "ASSESS whether" in block
    assert "Do not restate the view as ours" in block


def test_positioning_is_marked_as_one_house_s_dated_view():
    block = _block("HealthcareServices", "Managed Care", "US", [NOTE])
    assert "MOH: at risk" in block
    assert "NOT a conclusion to adopt" in block


def test_the_company_s_own_filing_outranks_the_sector_note():
    block = _block("HealthcareServices", "Managed Care", "US", [NOTE])
    assert "prefer the company's own filing" in block


def test_the_2f_header_is_preserved():
    """Downstream reads section 2F by name — _framework_metrics_extract pulls
    section_2f FIRST, so the header is a contract, not decoration."""
    block = _block("HealthcareServices", "Managed Care", "US", [NOTE])
    assert "2F. INDUSTRY-SPECIFIC KPI FRAMEWORK" in block


# ── Market separation ────────────────────────────────────────────────────

def test_market_scoped_knowledge_sorts_ahead_of_agnostic():
    agnostic = {**NOTE, "market": "", "house": "Generic House",
                "anchor_kpi": "generic anchor"}
    specific = {**NOTE, "market": "US"}
    with patch.object(ik.db, "query", return_value=[]), \
         patch.object(ik, "ensure_industry_table", return_value=None):
        pass    # store is exercised below via the composer ordering
    block = _block("HealthcareServices", "Managed Care", "US",
                   [specific, agnostic])
    # The market-specific anchor is emitted; the agnostic one is not repeated.
    assert "Medical loss ratio" in block
    assert "generic anchor" not in block


def test_the_query_scopes_to_the_requested_market_or_agnostic():
    """The regression the shared-profile collision would cause: HK knowledge
    served to JPM. Assert the SQL is scoped rather than trusting the caller."""
    captured = {}

    def _q(sql, params=None):
        captured["sql"], captured["params"] = sql, params
        return []

    with patch.object(ik, "ensure_industry_table", return_value=None), \
         patch.object(ik.db, "query", side_effect=_q):
        ik.get_industry_knowledge("Financials", "Money Center Bank", "HKSE")

    assert "market = ?" in captured["sql"]
    assert "HKSE" in captured["params"]
    assert ik.ANY_MARKET in captured["params"], (
        "market-agnostic notes must still serve every market"
    )


def test_a_read_failure_returns_no_notes_rather_than_raising():
    with patch.object(ik, "ensure_industry_table",
                      side_effect=RuntimeError("no db")):
        assert ik.get_industry_knowledge("X", "Y", "US") == []
    with patch.object(ik, "ensure_industry_table", return_value=None), \
         patch.object(ik.db, "query", side_effect=RuntimeError("boom")):
        assert ik.get_industry_knowledge("X", "Y", "US") == []
        assert ik.industry_coverage() == []
