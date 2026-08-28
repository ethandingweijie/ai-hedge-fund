"""Semiconductor / Memory — Micron and SK Hynix.

Both Goldman notes price the name on a multiple of THROUGH-CYCLE earnings:
Micron at "18X ... applied to our normalized EPS estimate of $62", SK Hynix
on a "2026E/27E avg. P/E-based 12m TP". That is why the pair cannot sit on
`IDM / Foundry`, whose anchors are fab utilisation and leading-edge revenue
mix — a foundry's economics, not a memory maker's.

Samsung is deliberately NOT here. Its own methodology section reads
"EV/EBITDA-based SOTP" across five divisions, so pricing it on DRAM bit
growth would anchor the whole company on roughly a third of its earnings.
It stays unrouted until a conglomerate profile exists, so it fails loudly
rather than valuing on the wrong basis.
"""

from __future__ import annotations

import pytest

from app.backend.services.drive_sync import build_gazetteer, match_tickers
from src.data.sector_kpi_framework import (
    SECTOR_KPI_FRAMEWORK,
    build_extractor_schema,
    render_card_payload,
)
from src.data.sector_profiles import (
    INDUSTRY_VALUATION_PROFILES,
    get_wacc_profile_for_ticker,
)

PROFILE = "Memory / DRAM-NAND"
MEMORY_TICKERS = ["MU", "000660.KS"]


# ── Link 1: routing ──────────────────────────────────────────────────────

@pytest.mark.parametrize("ticker", MEMORY_TICKERS)
def test_memory_names_route_to_the_memory_profile(ticker):
    assert get_wacc_profile_for_ticker(ticker) == ("Semiconductor", PROFILE)


@pytest.mark.parametrize("ticker", ["INTC", "TSM"])
def test_foundries_stay_on_idm_foundry(ticker):
    """Splitting memory out must not drag the foundries with it."""
    assert get_wacc_profile_for_ticker(ticker) == ("Semiconductor", "IDM / Foundry")


def test_samsung_is_not_routed_to_memory():
    """Samsung's stated method is EV/EBITDA-based SOTP across five divisions.

    Memory is one of them. Routing it here would price the group on DRAM
    bit growth; leaving it unrouted makes the omission visible instead.
    """
    sector, profile = get_wacc_profile_for_ticker("005930.KS")
    assert profile != PROFILE


# ── Link 2: the valuation method ─────────────────────────────────────────

def test_anchor_is_normalised_pe_not_ev_ebitda():
    """The cycle, not the spot quarter, is what the multiple applies to."""
    methods = INDUSTRY_VALUATION_PROFILES["Semiconductor"][PROFILE]["methods"]
    anchors = [m["name"] for m in methods if m.get("anchor")]
    assert anchors == ["P/E (norm)"]


def test_method_weights_sum_to_one():
    methods = INDUSTRY_VALUATION_PROFILES["Semiconductor"][PROFILE]["methods"]
    assert sum(m["weight"] for m in methods) == pytest.approx(1.0)


def test_epv_is_excluded():
    """A perpetuity on trough or peak memory earnings is meaningless."""
    excluded = INDUSTRY_VALUATION_PROFILES["Semiconductor"][PROFILE]["excluded"]
    assert "EPV" in excluded


def test_every_method_is_implementable():
    """An anchor the engine cannot compute leaves the card empty."""
    methods = INDUSTRY_VALUATION_PROFILES["Semiconductor"][PROFILE]["methods"]
    assert all(m.get("implementable") for m in methods)


# ── Link 3: extraction ───────────────────────────────────────────────────

def test_extractor_schema_covers_every_kpi():
    schema = build_extractor_schema(PROFILE)
    assert schema, "no extractor schema registered"
    keys = set(schema["kpi_keys"])
    assert keys == {k["key"] for k in SECTOR_KPI_FRAMEWORK[PROFILE]["kpis"]}


def test_the_memory_cycle_drivers_are_mandatory():
    """These four are what both notes actually model the name on."""
    mandatory = set(build_extractor_schema(PROFILE)["mandatory"])
    assert mandatory == {
        "memory_gross_margin", "hbm_revenue_share",
        "dram_bit_growth", "memory_inventory_days",
    }


def test_foundry_metrics_are_absent():
    """Utilisation and leading-edge mix describe a foundry, not a memory maker."""
    keys = {k["key"] for k in SECTOR_KPI_FRAMEWORK[PROFILE]["kpis"]}
    assert not keys & {
        "wafer_capacity_kwspm", "utilisation_rate_pct", "leading_edge_revenue_pct",
    }


def test_no_kpi_key_collides_with_a_statement_line_item():
    """The bridge writes KPIs onto the financial row BY KEY, so a collision
    silently replaces real data — that is how an extracted 32.5 displaced
    DBS's real book value per share of 24.28. Note `memory_gross_margin`
    is namespaced precisely because `gross_margin` is a real line item."""
    LINE_ITEMS = {
        "revenue", "net_income", "total_equity", "total_assets", "total_debt",
        "total_liabilities", "book_value_per_share", "earnings_per_share",
        "free_cash_flow", "operating_cash_flow", "operating_income",
        "interest_income", "interest_expense", "capital_expenditure",
        "shares_outstanding", "net_debt", "gross_margin", "long_term_debt",
        "dividends_per_share", "dividends_and_distributions",
        "accounts_receivable", "depreciation_and_amortization", "inventory",
    }
    for kpi in SECTOR_KPI_FRAMEWORK[PROFILE]["kpis"]:
        assert kpi["key"] not in LINE_ITEMS, (
            f"{kpi['key']} shadows a statement line item — namespace it"
        )


# ── Link 4: the card ─────────────────────────────────────────────────────

def test_card_renders_with_groups():
    payload = render_card_payload(PROFILE, {"data": {}}, "MU")
    assert payload, "no card payload"
    groups = payload.get("groups") or []
    assert groups, "card rendered with no groups"
    rendered = {k["key"] for g in groups for k in g["kpis"]}
    assert rendered == {k["key"] for k in SECTOR_KPI_FRAMEWORK[PROFILE]["kpis"]}


# ── The archive feeds it ─────────────────────────────────────────────────

@pytest.fixture(scope="module")
def gazetteer():
    return build_gazetteer()


@pytest.mark.parametrize(
    "filename, expected",
    [
        ("SK Hynix_Aug2026.pdf", {"000660.KS"}),
        ("Micron_June2026.pdf",  {"MU"}),
    ],
)
def test_memory_reports_match_their_ticker(filename, expected, gazetteer):
    assert set(match_tickers(filename, gazetteer)) == expected


def test_samsung_report_matches_nothing_while_unrouted(gazetteer):
    """It is in the archive. Until a conglomerate profile exists it must
    surface in the sync's `unmatched` count rather than attach itself to a
    memory profile that would misprice it."""
    assert match_tickers("Samsung_Aug2026.pdf", gazetteer) == []
