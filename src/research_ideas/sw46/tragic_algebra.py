"""
src/research_ideas/sw46/tragic_algebra.py
==========================================
Cassandra Unchained "Tragic Algebra" — the true cost of SBC.

  Omega    = G + C + B + dS * P
  OE       = N + G - Omega
  dE       = OE / N
  pooled   = sum(OE) / sum(N)

  N  = GAAP net income
  G  = GAAP stock-based-compensation expense (added back, since OE rebases)
  C  = Cash paid to IRS for RSU/ISO/PSU tax withholding (financing CF)
  B  = Buyback dollars (financing CF, common-stock-repurchased)
  dS = End-period diluted shares minus start-period diluted shares
       (sign convention: positive dS = dilution, negative dS = retirement)
  P  = Annual average share price

If FMP doesn't surface a clean RSU-tax line we fall back to
  C_estimate = (issued_shares_for_RSU_year * P * 0.37)
which we approximate from the cash-flow statement's common_stock_issued
field. Flagged as `cash_tax_withholding_estimated=True` for UI display.
"""
from __future__ import annotations

import statistics
from typing import Optional

from src.research_ideas.sw46.data_fetch import TickerBundle, YearlyStatement
from src.research_ideas.sw46.schemas import (
    TATier,
    TragicAlgebraResult,
    TragicAlgebraYear,
)


# Default withholding rate used in the C estimator when FMP doesn't expose a
# clean RSU-tax line. Article framing: tax withholding ~37% of vesting value.
_DEFAULT_WITHHOLDING_RATE = 0.37


def _estimate_C(year: YearlyStatement) -> Optional[float]:
    """
    Estimator for cash tax withholding when FMP's cash-flow row doesn't
    expose a clean line. Approximation:
        C_estimate ~= SBC_expense * 0.37
    (assumes the bulk of SBC is RSU vesting and net-share settlement applies).
    """
    if year.sbc_expense is None:
        return None
    return abs(year.sbc_expense) * _DEFAULT_WITHHOLDING_RATE


def _ta_tier_from_delta(
    pooled_delta_e: Optional[float],
    sum_net_income: Optional[float] = None,
) -> TATier:
    if pooled_delta_e is None:
        return "N/A"
    # Guard: when window ΣN is non-positive, the negative-over-negative ΔE math
    # produces values that LOOK like good retention (3.26 for FRSH) but are
    # economically meaningless. Per article's "Growth Companies Inflecting to
    # Profitability" section, these cases need separate treatment.
    if sum_net_income is not None and sum_net_income <= 0:
        return "N/A"
    if pooled_delta_e >= 0.85:
        return "Not-TT"
    if pooled_delta_e >= 0.60:
        return "Near-TT"
    return "TT*"


def _sbc_trend_slope(years: list[YearlyStatement]) -> Optional[float]:
    """
    Signed slope of (SBC / Revenue) over the window. Positive = SBC growing
    faster than revenue (worse for shareholders), negative = improving.
    Returns the unitless slope (per-year change in the ratio).
    """
    ratios = []
    for y in years:
        if y.sbc_expense is not None and y.revenue and y.revenue > 0:
            ratios.append((y.fiscal_year, y.sbc_expense / y.revenue))
    if len(ratios) < 3:
        return None
    n = len(ratios)
    mean_x = sum(r[0] for r in ratios) / n
    mean_y = sum(r[1] for r in ratios) / n
    num = sum((r[0] - mean_x) * (r[1] - mean_y) for r in ratios)
    den = sum((r[0] - mean_x) ** 2 for r in ratios)
    if den == 0:
        return None
    return num / den


def compute_tragic_algebra(bundle: TickerBundle) -> TragicAlgebraResult:
    if not bundle.years:
        return TragicAlgebraResult()

    out_years: list[TragicAlgebraYear] = []
    sum_oe = 0.0
    sum_n = 0.0
    has_any_year = False
    estimated_c_years = 0

    prev_shares: Optional[float] = None
    for y in bundle.years:
        # Share change uses period-over-period delta on diluted shares.
        share_change: Optional[float] = None
        if prev_shares is not None and y.diluted_shares is not None:
            share_change = y.diluted_shares - prev_shares
        prev_shares = y.diluted_shares if y.diluted_shares is not None else prev_shares

        # C — try FMP line; fall back to SBC * withholding-rate estimator.
        c_val = y.rsu_tax_withholding
        c_estimated = False
        if c_val is None:
            c_val = _estimate_C(y)
            c_estimated = c_val is not None
        if c_estimated:
            estimated_c_years += 1

        # B — buybacks are reported as negative cash flow; convert to positive.
        b_val = (
            abs(y.common_stock_repurchased)
            if y.common_stock_repurchased is not None
            else 0.0
        )

        # Omega = G + C + B + dS * P
        omega: Optional[float] = None
        if y.sbc_expense is not None:
            omega = y.sbc_expense
            omega += c_val if c_val is not None else 0.0
            omega += b_val
            if share_change is not None and y.avg_share_price is not None:
                omega += share_change * y.avg_share_price

        # OE = N + G - Omega
        oe: Optional[float] = None
        if (
            y.net_income is not None
            and y.sbc_expense is not None
            and omega is not None
        ):
            oe = y.net_income + y.sbc_expense - omega

        # dE = OE / N (skip if N is zero — division undefined)
        de: Optional[float] = None
        if oe is not None and y.net_income and y.net_income != 0:
            de = oe / y.net_income

        if oe is not None and y.net_income is not None:
            sum_oe += oe
            sum_n += y.net_income
            has_any_year = True

        out_years.append(
            TragicAlgebraYear(
                fiscal_year=y.fiscal_year,
                net_income=y.net_income,
                sbc_expense=y.sbc_expense,
                cash_tax_withholding=c_val,
                cash_tax_withholding_estimated=c_estimated,
                buybacks=b_val,
                share_change=share_change,
                avg_share_price=y.avg_share_price,
                omega=omega,
                owner_earnings=oe,
                delta_e=de,
            )
        )

    pooled = (sum_oe / sum_n) if (has_any_year and sum_n != 0) else None
    avg_oe = (sum_oe / max(1, sum(1 for y in out_years if y.owner_earnings is not None))) if has_any_year else None
    latest_oe = next(
        (y.owner_earnings for y in reversed(out_years) if y.owner_earnings is not None),
        None,
    )
    positive_oes = [y.owner_earnings for y in out_years
                    if y.owner_earnings is not None and y.owner_earnings > 0]
    median_oe = statistics.median(positive_oes) if positive_oes else None
    sum_n_value = sum_n if has_any_year else None

    return TragicAlgebraResult(
        years=out_years,
        pooled_delta_e=pooled,
        avg_owner_earnings=avg_oe,
        latest_owner_earnings=latest_oe,
        median_owner_earnings=median_oe,
        positive_oe_years=len(positive_oes),
        sum_net_income=sum_n_value,
        sbc_trend=_sbc_trend_slope(bundle.years),
        ta_tier=_ta_tier_from_delta(pooled, sum_n_value),
        estimated_c_years=estimated_c_years,
    )


def pooled_delta_e_across_cohort(per_ticker: list[TragicAlgebraResult]) -> Optional[float]:
    """sum(OE_y_t) / sum(N_y_t) across every ticker and every year."""
    num = 0.0
    den = 0.0
    any_row = False
    for r in per_ticker:
        for y in r.years:
            if y.owner_earnings is None or y.net_income is None:
                continue
            num += y.owner_earnings
            den += y.net_income
            any_row = True
    if not any_row or den == 0:
        return None
    return num / den
