"""Three-statement financials: layout, growth and field support.

The Financials tab used to render a flat 25-field mix — revenue, total
assets and free cash flow side by side. Analyst notes present three
statements with a growth column, and a bank's or a REIT's statements are
not shaped like an industrial's.

No network: rows are supplied directly.
"""

from __future__ import annotations

import pytest

from src.tools.api import (
    _BALANCE_MAP, _CASHFLOW_MAP, _INCOME_MAP, _RATIOS_MAP,
)
from src.tools.financial_statements import (
    STATEMENT_LINE_ITEMS,
    build_financial_statements,
    resolve_layout,
)

# Two years of a bank, with DBS's real shape: gross `revenue` far above
# total income, and a cash-flow/income disagreement already resolved.
BANK_ROWS = {
    "FY2024": {
        "revenue": 38_720e6, "interest_income": 30_927e6,
        "interest_expense": 16_503e6, "net_interest_income": 14_424e6,
        "operating_expense": 9_333e6, "pretax_income": 12_884e6,
        "income_tax_expense": 1_594e6, "net_income": 11_289e6,
        "total_assets": 800_000e6, "shareholders_equity": 62_000e6,
        "loans_receivable": 430_000e6, "total_deposits": 560_000e6,
        "operating_cash_flow": 15_000e6, "book_value_per_share": 24.28,
    },
    "FY2025": {
        "revenue": 37_892e6, "interest_income": 28_268e6,
        "interest_expense": 13_768e6, "net_interest_income": 14_500e6,
        "operating_expense": 10_346e6, "pretax_income": 12_999e6,
        "income_tax_expense": 2_065e6, "net_income": 10_933e6,
        "total_assets": 850_000e6, "shareholders_equity": 65_000e6,
        "loans_receivable": 450_000e6, "total_deposits": 590_000e6,
        "operating_cash_flow": 16_000e6, "book_value_per_share": 25.10,
    },
}

STD_ROWS = {
    "FY2024": {"revenue": 1000.0, "cost_of_revenue": 400.0, "gross_profit": 600.0,
               "operating_income": 300.0, "net_income": 200.0,
               "total_assets": 5000.0, "operating_cash_flow": 350.0},
    "FY2025": {"revenue": 1200.0, "cost_of_revenue": 500.0, "gross_profit": 700.0,
               "operating_income": 360.0, "net_income": 250.0,
               "total_assets": 5500.0, "operating_cash_flow": 400.0},
}


def _rows(payload, statement):
    return {r["key"]: r for r in payload["statements"][statement]["rows"]}


# ── Field support ────────────────────────────────────────────────────────

def test_every_statement_field_is_mapped():
    """A layout row whose field no map resolves renders permanently blank.

    This also guards the reverse mistake: editing the maps and dropping an
    existing mapping (provision_for_loan_losses and share_buyback were both
    lost that way once).
    """
    supported = (
        set(_INCOME_MAP.values()) | set(_BALANCE_MAP.values())
        | set(_CASHFLOW_MAP.values()) | set(_RATIOS_MAP.values())
    )
    missing = [f for f in STATEMENT_LINE_ITEMS if f not in supported]
    assert not missing, f"unmapped statement fields: {missing}"


@pytest.mark.parametrize("field", [
    "provision_for_loan_losses", "share_buyback", "accounts_payable",
    "net_interest_income", "inventory", "retained_earnings",
    "investing_cash_flow", "financing_cash_flow",
])
def test_key_mappings_survive(field):
    supported = (
        set(_INCOME_MAP.values()) | set(_BALANCE_MAP.values())
        | set(_CASHFLOW_MAP.values()) | set(_RATIOS_MAP.values())
    )
    assert field in supported


# ── Layout routing ───────────────────────────────────────────────────────

@pytest.mark.parametrize("sector, profile, expected", [
    ("Financials", "Money Center Bank (SG)", "bank"),
    ("Banking",    "",                       "bank"),
    ("REIT",       "S-REIT",                 "reit"),
    ("RealEstate", "S-REIT",                 "reit"),
    ("Tech",       "Fabless",                "standard"),
    ("",           "",                       "standard"),
])
def test_layout_resolution(sector, profile, expected):
    assert resolve_layout(sector, profile) == expected


# ── The bank layout ──────────────────────────────────────────────────────

def test_bank_income_has_no_gross_profit_row():
    """A bank has no cost of revenue. Rendering one is the same category
    error as pricing an S-REIT on P/FFO."""
    rows = _rows(build_financial_statements(BANK_ROWS, sector="Financials"), "income")
    assert "gross_profit" not in rows
    assert "cost_of_revenue" not in rows


def test_bank_income_leads_with_total_income():
    rows = _rows(build_financial_statements(BANK_ROWS, sector="Financials"), "income")
    assert "net_interest_income" in rows
    assert "total_income" in rows
    # NII + non-II must reconcile to total income
    ti = rows["total_income"]["values"]["FY2025"]
    nii = rows["net_interest_income"]["values"]["FY2025"]
    nonii = rows["non_interest_income"]["values"]["FY2025"]
    assert ti == pytest.approx(nii + nonii)


def test_bank_total_income_is_net_not_gross():
    """DBS FY2025: total income S$24,124mn against gross revenue
    S$37,892mn — the 1.57x gap _bank_total_income exists to unwind."""
    rows = _rows(build_financial_statements(BANK_ROWS, sector="Financials"), "income")
    assert rows["total_income"]["values"]["FY2025"] == pytest.approx(24_124e6)
    assert "revenue" not in rows


def test_bank_balance_sheet_is_a_loan_book():
    rows = _rows(build_financial_statements(BANK_ROWS, sector="Financials"), "balance")
    assert {"loans_receivable", "total_deposits"} <= set(rows)
    assert "inventory" not in rows


# ── Growth ───────────────────────────────────────────────────────────────

def test_growth_is_derived_not_fetched():
    """HK is served no growth data by FMP, so it must be computed."""
    rows = _rows(build_financial_statements(STD_ROWS, sector="Tech"), "income")
    g = rows["revenue"]["growth"]
    assert g["FY2024"] is None          # no prior year
    assert g["FY2025"] == pytest.approx(0.2)


def test_growth_is_none_across_a_sign_flip():
    """Loss to profit has no meaningful percentage."""
    rows = build_financial_statements(
        {"FY2024": {"revenue": 100.0, "net_income": -50.0},
         "FY2025": {"revenue": 120.0, "net_income": 25.0}},
        sector="Tech",
    )
    assert _rows(rows, "income")["net_income"]["growth"]["FY2025"] is None


def test_growth_is_none_on_a_zero_base():
    rows = build_financial_statements(
        {"FY2024": {"revenue": 0.0}, "FY2025": {"revenue": 120.0}}, sector="Tech")
    assert _rows(rows, "income")["revenue"]["growth"]["FY2025"] is None


def test_per_share_rows_carry_no_growth():
    rows = _rows(build_financial_statements(BANK_ROWS, sector="Financials"), "balance")
    assert rows["book_value_per_share"]["growth"] == {}


# ── Shape ────────────────────────────────────────────────────────────────

def test_empty_rows_are_dropped_not_rendered_blank():
    """A row the data cannot fill must not occupy space in the statement."""
    rows = _rows(build_financial_statements(STD_ROWS, sector="Tech"), "income")
    assert "provision_for_loan_losses" not in rows
    assert "research_and_development" not in rows


def test_input_is_not_mutated():
    """raw_financials keeps its flat keys — dcf_agent and every extractor
    read them, so this view must stay strictly additive."""
    before = {fy: dict(r) for fy, r in STD_ROWS.items()}
    build_financial_statements(STD_ROWS, sector="Tech")
    assert STD_ROWS == before


def test_three_statements_are_present():
    payload = build_financial_statements(BANK_ROWS, sector="Financials")
    assert set(payload["statements"]) == {"income", "balance", "cashflow"}
    assert payload["periods"] == ["FY2024", "FY2025"]


def test_no_periods_yields_empty_payload():
    payload = build_financial_statements({}, sector="Tech")
    assert payload["periods"] == []
    assert payload["statements"] == {}
