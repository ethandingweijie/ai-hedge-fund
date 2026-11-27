"""Owner request 2026-11-27: three statement / data-check bugs seen on the V, 09988.HK and 00005.HK production runs.

1. The web statements routed every "Financials" name to the bank layout (Visa: net interest income -US$0.54bn;
   SGX too) while the PDF and workbook routed by profile family. The layout now follows report_families.
2. HSBC's web total income mixed two bases (FMP revenue netted in FY2021/FY2025, passed through in FY2022-24).
   Total income is NII + non-interest income on every year, or the year is not comparable.
3. "book value per share x shares = shareholders' equity" failed on all three runs because the BVPS (period
   equity over the period's basic count) was multiplied by the engine's CURRENT valuation count.

Fixtures are the stored production payloads' figures (web_runs 9c1c4261 V, db0f9dad 09988.HK, f35ea082 00005.HK,
UOB a74ccab4, SGX 11d17749), cut down to the lines each test needs. No network, no database.
"""
from __future__ import annotations

import pytest

from src.agents.analysis.dcf_agent import _bank_total_income
from src.data import input_reconciliation as irc
from src.data.report_families import family_rows
from src.tools.financial_statements import (
    bank_total_income_by_year,
    build_financial_statements,
    relayout_statements,
    resolve_layout,
)

B = 1e9


def _rows(payload, statement):
    return {r["key"]: r for r in payload["statements"][statement]["rows"]}


# ── fixtures (USD / SGD, as stored) ───────────────────────────────────────────

# Visa FY2025-26 (USD): no interest income, a small interest expense -- a fee platform, not a bank.
V_ROWS = {
    "FY2025": {"revenue": 40.0 * B, "cost_of_revenue": 7.855 * B, "gross_profit": 32.145 * B,
               "operating_expense": 8.151 * B, "operating_income": 23.994 * B, "interest_income": 0.0,
               "interest_expense": 0.589 * B, "net_interest_income": -0.589 * B, "pretax_income": 24.194 * B,
               "net_income": 20.058 * B, "shareholders_equity": 37.909 * B, "total_assets": 99.627 * B},
    "FY2026": {"revenue": 45.903 * B, "cost_of_revenue": 9.0 * B, "gross_profit": 36.903 * B,
               "operating_expense": 8.793 * B, "operating_income": 28.11 * B, "interest_income": 0.0,
               "interest_expense": 0.758 * B, "net_interest_income": -0.758 * B, "pretax_income": 28.093 * B,
               "net_income": 23.504 * B, "shareholders_equity": 36.111 * B, "total_assets": 94.36 * B},
}

# HSBC FY2021-25 (USD), FMP: revenue reads as gross (above interest income) in FY2021 and FY2025 only.
HSBC_ROWS = {
    "FY2021": {"revenue": 73.946 * B, "interest_income": 36.188 * B, "interest_expense": 9.699 * B,
               "net_interest_income": 26.489 * B, "operating_expense": 32.595 * B, "net_income": 13.917 * B},
    "FY2022": {"revenue": 46.358 * B, "interest_income": 52.826 * B, "interest_expense": 22.449 * B,
               "net_interest_income": 30.377 * B, "operating_expense": 31.0 * B, "net_income": 15.559 * B},
    "FY2023": {"revenue": 56.353 * B, "interest_income": 100.868 * B, "interest_expense": 65.072 * B,
               "net_interest_income": 35.796 * B, "operating_expense": 34.092 * B, "net_income": 23.533 * B},
    "FY2024": {"revenue": 61.253 * B, "interest_income": 108.631 * B, "interest_expense": 75.898 * B,
               "net_interest_income": 32.733 * B, "operating_expense": 35.087 * B, "net_income": 23.979 * B},
    "FY2025": {"revenue": 150.799 * B, "interest_income": 100.049 * B, "interest_expense": 64.481 * B,
               "net_interest_income": 35.568 * B, "operating_expense": 54.775 * B, "net_income": 22.781 * B},
}

# UOB FY2021-25 (SGD): FY2021's revenue (S$9.1bn) is not on the gross basis of the other four years.
UOB_ROWS = {
    "FY2021": {"revenue": 9.132 * B, "interest_income": 8.204 * B, "interest_expense": 1.816 * B,
               "net_interest_income": 6.388 * B, "operating_expense": 4.313 * B},
    "FY2022": {"revenue": 16.096 * B, "interest_income": 12.862 * B, "interest_expense": 4.519 * B,
               "net_interest_income": 8.343 * B, "operating_expense": 5.281 * B},
    "FY2023": {"revenue": 26.495 * B, "interest_income": 22.242 * B, "interest_expense": 12.563 * B,
               "net_interest_income": 9.679 * B, "operating_expense": 6.217 * B},
    "FY2024": {"revenue": 27.868 * B, "interest_income": 23.259 * B, "interest_expense": 13.585 * B,
               "net_interest_income": 9.674 * B, "operating_expense": 6.31 * B},
    "FY2025": {"revenue": 26.103 * B, "interest_income": 20.676 * B, "interest_expense": 11.321 * B,
               "net_interest_income": 9.355 * B, "operating_expense": 7.083 * B},
}

# DBS FY2021-25 (SGD): every year on the gross basis.
DBS_ROWS = {
    "FY2021": {"revenue": 15.916 * B, "interest_income": 10.185 * B, "interest_expense": 1.745 * B},
    "FY2022": {"revenue": 21.48 * B, "interest_income": 15.927 * B, "interest_expense": 4.986 * B},
    "FY2023": {"revenue": 34.354 * B, "interest_income": 27.862 * B, "interest_expense": 14.22 * B},
    "FY2024": {"revenue": 38.72 * B, "interest_income": 30.927 * B, "interest_expense": 16.503 * B},
    "FY2025": {"revenue": 37.892 * B, "interest_income": 28.268 * B, "interest_expense": 13.768 * B},
}


# ── 1. Layout follows the report family ───────────────────────────────────────

@pytest.mark.parametrize("sector, profile, expected", [
    ("Financials", "Payment Networks", "standard"),            # V
    ("Financials", "Market Infrastructure (SG)", "standard"),  # S68.SI
    ("Financials", "Asset Manager", "standard"),
    ("Financials", "Insurance", "standard"),
    ("Financials", "Money Center Bank (EU)", "bank"),          # HSBC
    ("Financials", "Money Center Bank (SG)", "bank"),          # D05 / U11
    ("Financials", "Card Issuer & Consumer Lender", "bank"),   # in the Banks family
    ("Financials", "", "standard"),                            # "Financials" alone is not a bank
    ("Banking", "", "bank"),
    ("RealEstate", "S-REIT", "reit"),
])
def test_layout_follows_the_report_family(sector, profile, expected):
    assert resolve_layout(sector, profile) == expected


def test_every_banks_family_profile_gets_the_bank_layout_and_no_other_family_does():
    from src.data.report_families import REPORT_FAMILIES
    for fam, spec in REPORT_FAMILIES.items():
        for p in spec["profiles"]:
            got = resolve_layout("Financials", p)
            assert (got == "bank") == (fam == "Banks"), (fam, p, got)


def test_visa_renders_revenue_not_net_interest_income():
    out = build_financial_statements(V_ROWS, sector="Financials", profile="Payment Networks", currency="USD")
    inc = _rows(out, "income")
    assert out["layout"] == "standard"
    assert inc["revenue"]["values"]["FY2026"] == pytest.approx(45.903 * B)
    assert "net_interest_income" not in inc and "total_income" not in inc


def test_a_stored_bank_layout_is_rebuilt_on_the_final_profile():
    """Runs stored before the fix carry Visa on the bank layout; the read path rebuilds it from the stored lines
    plus the run's raw_financials, so the web, PDF and workbook agree."""
    stored = build_financial_statements(V_ROWS, sector="Banking", currency="USD")
    assert stored["layout"] == "bank"
    assert _rows(stored, "income")["net_interest_income"]["values"]["FY2026"] == pytest.approx(-0.758 * B)
    fixed = relayout_statements(stored, V_ROWS, sector="Financials", profile="Payment Networks")
    inc = _rows(fixed, "income")
    assert fixed["layout"] == "standard" and fixed["currency"] == "USD"
    assert inc["revenue"]["values"]["FY2026"] == pytest.approx(45.903 * B)
    assert inc["revenue"]["growth"]["FY2026"] == pytest.approx(45.903 / 40.0 - 1)
    assert "net_interest_income" not in inc
    # idempotent, and a payload already on its layout is returned as is
    assert relayout_statements(fixed, V_ROWS, sector="Financials", profile="Payment Networks") is fixed


def test_relayout_without_raw_rows_keeps_the_stored_payload():
    stored = build_financial_statements(V_ROWS, sector="Banking")
    assert relayout_statements(stored, None, sector="Financials", profile="Payment Networks") is stored


# ── 2. Bank total income on one derivation ────────────────────────────────────

def test_hsbc_total_income_is_withheld_rather_than_mixing_bases():
    """Before: 64.25 / 46.36 / 56.35 / 61.25 / 86.32bn -- FY2021 and FY2025 netted from a gross revenue, FY2022-24
    FMP revenue passed through. Reported revenue was ~50.0 / 66.1 / 65.9bn FY2022-24: neither basis is right, and
    the provider's definition changes inside the series, so no year is printed."""
    vals, why = bank_total_income_by_year(HSBC_ROWS)
    assert all(v is None for v in vals.values())
    assert set(why) == set(HSBC_ROWS)
    assert "below interest income" in why["FY2023"]
    assert "only 2 of 5 years" in why["FY2025"]

    out = build_financial_statements(HSBC_ROWS, sector="Financials", profile="Money Center Bank (EU)", currency="USD")
    inc = _rows(out, "income")
    assert out["layout"] == "bank"
    assert "total_income" not in inc and "non_interest_income" not in inc and "pre_provision_profit" not in inc
    assert inc["net_interest_income"]["values"]["FY2023"] == pytest.approx(35.796 * B)
    assert inc["operating_expense"]["values"]["FY2025"] == pytest.approx(54.775 * B)   # reported lines stay
    assert set(out["not_comparable"]) == set(HSBC_ROWS)
    assert all(w.startswith("total income: ") for w in out["not_comparable"].values())


def test_uob_keeps_its_fixed_years_and_withholds_the_broken_one():
    vals, why = bank_total_income_by_year(UOB_ROWS)
    assert vals["FY2023"] == pytest.approx(13.932 * B, rel=1e-3)
    assert vals["FY2024"] == pytest.approx(14.283 * B, rel=1e-3)
    assert vals["FY2025"] == pytest.approx(14.782 * B, rel=1e-3)
    assert vals["FY2022"] == pytest.approx(11.577 * B, rel=1e-3)
    assert vals["FY2021"] is None and "13% of total income" in why["FY2021"]   # was 7.32bn (reported ~9.8bn)
    inc = _rows(build_financial_statements(UOB_ROWS, sector="Financials", profile="Money Center Bank (SG)"), "income")
    ti, nii, nonii = (inc[k]["values"] for k in ("total_income", "net_interest_income", "non_interest_income"))
    for fy in ("FY2022", "FY2023", "FY2024", "FY2025"):
        assert ti[fy] == pytest.approx(nii[fy] + nonii[fy])
    assert inc["total_income"]["growth"]["FY2022"] is None        # no growth out of a withheld year


def test_dbs_is_unchanged():
    vals, why = bank_total_income_by_year(DBS_ROWS)
    assert not why
    assert [round(vals[k] / B, 3) for k in sorted(vals)] == [14.171, 16.494, 20.134, 22.217, 24.124]


def test_pdf_key_financials_use_the_same_series():
    """The PDF / workbook Banks row reads the series derivation, so it withholds what the web withholds."""
    _fy, rows = family_rows(HSBC_ROWS, "Banks", years=3)
    ti = next(v for (label, _k, v) in rows if label.startswith("Total income"))
    assert ti == [None, None, None]
    _fy, rows = family_rows(UOB_ROWS, "Banks", years=3)
    ti = next(v for (label, _k, v) in rows if label.startswith("Total income"))
    assert [round(x / B, 2) for x in ti] == [13.93, 14.28, 14.78]


def test_single_row_never_returns_the_provider_revenue():
    assert _bank_total_income(HSBC_ROWS["FY2023"]) is None
    assert _bank_total_income(DBS_ROWS["FY2025"]) == pytest.approx(24.124 * B)


# ── 3. BVPS x shares = shareholders' equity, on one date and one basis ────────

# most_recent as the engine held it (listing currency for HK), with the period's FMP share counts.
BVPS_CASES = {
    # ticker: (row, valuation share count, old gap)
    "V": ({"book_value_per_share": 21.380107, "total_equity": 36.111 * B, "shares_outstanding": 1.907 * B,
           "shares_outstanding_basic": 1.689 * B, "minority_interest": 0.0}, 1.880 * B, 0.113),
    "09988.HK": ({"book_value_per_share": 66.001476, "total_equity": 1243.008 * B, "shares_outstanding": 19.235 * B,
                  "shares_outstanding_basic": 18.83304 * B, "minority_interest": 74.898 * B}, 20.3267 * B, 0.079),
    "00005.HK": ({"book_value_per_share": 76.211743, "total_equity": 1392.864 * B, "shares_outstanding": 18.402085 * B,
                  "shares_outstanding_basic": 18.276237 * B, "minority_interest": 3.129 * B}, 17.2362 * B, 0.057),
}


@pytest.mark.parametrize("ticker", sorted(BVPS_CASES))
def test_bvps_identity_passes_on_the_period_share_count(ticker):
    row, current, old_gap = BVPS_CASES[ticker]
    # the old comparison: BVPS x the CURRENT valuation count
    old = irc.check_bvps({"book_value_per_share": row["book_value_per_share"], "total_equity": row["total_equity"]}, current)
    assert old["status"] == "fail" and old["gap"] == pytest.approx(old_gap, abs=0.002)
    c = irc.check_bvps(row, current)
    assert c["status"] == "pass", c
    assert c["gap"] < 0.001 and c["share_basis"] == "period basic shares"
    assert "the valuation counts" in c["detail"]          # the count difference is disclosed, not failed


def test_bvps_identity_still_fails_on_book_value_including_minorities():
    """Alibaba's FMP BVPS before the parent-equity restatement: total equity (incl. CNY 65bn minorities) per share."""
    row = dict(BVPS_CASES["09988.HK"][0])
    row["book_value_per_share"] = 1122.505 * 1.1756 / 18.83304          # HKD, total equity over basic shares
    c = irc.check_bvps(row, BVPS_CASES["09988.HK"][1])
    assert c["status"] == "fail" and "including minority interests" in c["detail"]


def test_bvps_identity_still_fails_on_a_currency_mismatch():
    """HSBC's BVPS left in USD while equity was converted to HKD (the 2026-11-25 cap-currency class of error)."""
    row = dict(BVPS_CASES["00005.HK"][0])
    row["book_value_per_share"] = 9.728082
    assert irc.check_bvps(row, BVPS_CASES["00005.HK"][1])["status"] == "fail"


def test_bvps_identity_reconcile_no_longer_flags_the_three_runs():
    for ticker, (row, current, _g) in BVPS_CASES.items():
        out = irc.reconcile(row, shares=current)
        assert not any("book value per share" in f for f in out["flags"]), ticker


def test_financials_endpoint_uses_the_series_for_a_bank():
    from app.backend.routes.analysis import _sg_financial_items
    recs = [{"report_period": f"{fy[2:]}-12-31", **row} for fy, row in sorted(HSBC_ROWS.items())]
    items = _sg_financial_items(recs, "annual", "Banks - Diversified")
    assert [i["revenue"] for i in items] == [None] * 5
    assert all(i.get("not_comparable") for i in items)
    recs = [{"report_period": f"{fy[2:]}-12-31", **row} for fy, row in sorted(UOB_ROWS.items())]
    items = _sg_financial_items(recs, "annual", "Banks - Regional")
    assert items[0]["revenue"] is None and items[0]["not_comparable"]
    assert items[-1]["revenue"] == pytest.approx(14.782 * B, rel=1e-3) and "not_comparable" not in items[-1]
