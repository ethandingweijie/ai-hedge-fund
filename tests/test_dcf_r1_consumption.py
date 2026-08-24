"""Workstream R1 — dcf_agent consumption of structured assumptions.

Offline: store pointed at a temp sqlite (RUN_ARCHIVE_PATH), no network.
Covers the Priority-0 helper (_r1_structured_guidance): kill switch,
empty store, revenue sanity-band acceptance, currency-mismatch rejection,
EBITDA pass-through — plus the parse_amount primitives the helper relies
on and the backward gate that the regex path still works standalone.
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="module")
def r1_env(tmp_path_factory):
    """Temp sqlite for the assumption store + import the dcf helper."""
    db_file = tmp_path_factory.mktemp("dcf_r1") / "store.db"
    saved = {k: os.environ.get(k) for k in
             ("RUN_ARCHIVE_PATH", "DATABASE_URL", "EARNINGS_ASSUMPTIONS")}
    os.environ["RUN_ARCHIVE_PATH"] = str(db_file)
    os.environ.pop("DATABASE_URL", None)
    os.environ.pop("EARNINGS_ASSUMPTIONS", None)  # default-on path
    import src.memory.assumption_store as store
    store._ensured = False
    from src.agents.analysis.dcf_agent import (
        _r1_structured_guidance, _guided_growth)
    yield store, _r1_structured_guidance, _guided_growth
    store._ensured = False
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def test_kill_switch_returns_none(r1_env, monkeypatch):
    store, r1_guid, _ = r1_env
    monkeypatch.setenv("EARNINGS_ASSUMPTIONS", "false")
    assert r1_guid("BABA", revenue_base=1e11) is None
    monkeypatch.setenv("EARNINGS_ASSUMPTIONS", "off")
    assert r1_guid("BABA") is None


def test_empty_store_returns_none(r1_env):
    _, r1_guid, _ = r1_env
    assert r1_guid("NO-FILINGS-HERE", revenue_base=1e9) is None


def test_structured_guidance_revenue_in_band(r1_env):
    store, r1_guid, guided_growth = r1_env
    store.upsert_earnings_assumptions(
        "CRWD", 2026, 4, as_of="2026-03-05",
        source="edgar_8k_ex99+fmp_transcript",
        period_label="FY2026",
        guidance=[
            {"metric": "revenue", "period": "FY2026",
             "low": "$4.4B", "high": "$4.5B"},
            {"metric": "ebitda", "period": "FY2026", "mid": "$1.0B"},
        ],
    )
    got = r1_guid("CRWD", revenue_base=4.2e9)
    assert got is not None
    # midpoint of $4.4–4.5B
    assert abs(got["revenue_guidance_mid"] - 4.45e9) < 1e6
    assert abs(got["ebitda_guidance_mid"] - 1.0e9) < 1e6
    assert got["_r1_source"] == "edgar_8k_ex99+fmp_transcript"
    assert got["_r1_as_of"] == "2026-03-05"
    # And it drives the existing _guided_growth machinery
    g = guided_growth(got, revenue_base=4.2e9)
    assert g is not None and 0.04 < g < 0.08


def test_currency_mismatch_rejected_by_band(r1_env):
    store, r1_guid, _ = r1_env
    # Guidance in RMB (~7x a USD base) must fail the −30%..+100% band
    store.upsert_earnings_assumptions(
        "BABA", 2027, 1, as_of="2026-08-20",
        guidance=[{"metric": "revenue", "period": "FY2027",
                   "mid": "Rmb1000bn"}],
    )
    got = r1_guid("BABA", revenue_base=1.4e11)  # USD-ish base
    assert got is None


def test_quarterly_guidance_rejected_against_annual_base(r1_env):
    store, r1_guid, _ = r1_env
    store.upsert_earnings_assumptions(
        "MSFT", 2026, 4, as_of="2026-07-22",
        guidance=[{"metric": "revenue", "period": "Q1 FY2027",
                   "mid": "$70bn"}],
    )
    # ~$280bn annual base → a $70bn quarter implies −75% → rejected
    assert r1_guid("MSFT", revenue_base=2.8e11) is None


# ── Live-shape tests (2026-08-24 CRWD row from the 8-K EX-99.1) ─────────────

def test_live_crwd_metric_forms(r1_env):
    """Press-release metric names are free-form ('Total revenue', 'Annual
    recurring revenue', 'Non-GAAP income from operations'), one item per
    fiscal period.  Must pick the FULL-YEAR TOTAL revenue, never ARR
    (near-revenue KPI, excluded outright), and not misread op income as
    EBITDA."""
    store, r1_guid, _ = r1_env
    store.upsert_earnings_assumptions(
        "CRWD", 2027, 1, as_of="2026-06-03", source="edgar_8k_ex99",
        guidance=[
            {"metric": "Annual recurring revenue", "period": "Q2 FY27",
             "low": "$5,792.6 million", "mid": "$5,793.6 million",
             "high": "$5,794.6 million", "unit": "USD"},
            {"metric": "Annual recurring revenue", "period": "Full Year FY27",
             "low": "$6,531.7 million", "mid": "$6,543.6 million",
             "high": "$6,555.5 million", "unit": "USD"},
            {"metric": "Total revenue", "period": "Q2 FY27",
             "low": "$1,436.0 million", "mid": "$1,439.0 million",
             "high": "$1,442.0 million", "unit": "USD"},
            {"metric": "Total revenue", "period": "Full Year FY27",
             "low": "$5,914.7 million", "mid": "$5,936.7 million",
             "high": "$5,958.7 million", "unit": "USD"},
            {"metric": "Non-GAAP income from operations",
             "period": "Full Year FY27", "mid": "$1,466.3 million"},
        ],
    )
    got = r1_guid("CRWD", revenue_base=4.6e9)
    assert got is not None
    assert abs(got["revenue_guidance_mid"] - 5.9367e9) < 1e6
    assert got["_r1_period"] == "Full Year FY27"
    assert "ebitda_guidance_mid" not in got      # op income ≠ EBITDA
    # No base → annual-first ordering still selects the FY item, not Q2
    got_nb = r1_guid("CRWD", revenue_base=0)
    assert got_nb is not None
    assert got_nb["_r1_period"] == "Full Year FY27"


def test_ebita_alias_and_annual_first(r1_env):
    """'Adjusted EBITA' (BABA's reported line) must match, and the annual
    item must win over the quarterly one."""
    store, r1_guid, _ = r1_env
    store.upsert_earnings_assumptions(
        "BABA", 2027, 2, as_of="2026-11-15", source="edgar_6k_ex99",
        guidance=[
            {"metric": "Adjusted EBITA", "period": "Q2 FY2027",
             "mid": "Rmb28.0bn"},
            {"metric": "Adjusted EBITA", "period": "Full Year FY2027",
             "mid": "Rmb120.0bn"},
        ],
    )
    got = r1_guid("BABA", revenue_base=1.4e11)
    assert got is not None
    assert abs(got["ebitda_guidance_mid"] - 120.0e9) < 1e6


def test_metric_helpers():
    from src.agents.analysis.dcf_agent import (
        _norm_metric, _is_annual_period, _revenue_metric_rank)
    assert _norm_metric("Total Revenue") == "totalrevenue"
    assert _norm_metric("Adj. EBITDA") == "adjebitda"
    assert _is_annual_period("Full Year FY27")
    assert _is_annual_period("FY2026")
    assert _is_annual_period("fiscal year ended March 31, 2027")
    assert not _is_annual_period("Q2 FY27")
    assert not _is_annual_period("Three months ended June 30, 2026")
    assert _revenue_metric_rank("totalrevenue") == 0
    assert _revenue_metric_rank("revenue") == 0
    assert _revenue_metric_rank("revenuermb") == 2       # currency note
    assert _revenue_metric_rank("subscriptionrevenue") == 2
    assert _revenue_metric_rank("annualrecurringrevenue") == -1  # ARR
    assert _revenue_metric_rank("ebitda") == -1


# ── parse_amount primitives (shared with extraction) ─────────────────────────

def test_parse_amount_forms():
    from src.memory.assumption_extract import parse_amount
    assert parse_amount("US$186") == 186.0
    assert parse_amount("Rmb210bn") == 210e9
    assert abs(parse_amount("$130-145bn") - 137.5e9) < 1.0
    assert parse_amount("$4.4B") == 4.4e9
    assert parse_amount(None) is None
    assert parse_amount("no numbers here") is None
    # Multi-amount non-range strings → first value (protects US$/HK$ pairs)
    assert parse_amount("US$186 / HK$180") == 186.0


# ── Backward gate: regex path unchanged ──────────────────────────────────────

def test_regex_guided_growth_standalone(r1_env):
    _, _, guided_growth = r1_env
    # Priority 1: explicit percentage
    assert guided_growth({"revenue_growth_pct": 15}) == 0.15
    # Priority 2: dollar mid vs base
    g = guided_growth({"revenue_guidance_mid": 1.1e9}, revenue_base=1e9)
    assert abs(g - 0.10) < 1e-9
    # Empty dict → None (falls through to analyst/historical)
    assert guided_growth({}, revenue_base=1e9) is None
