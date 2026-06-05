"""
src/research_ideas/sw46/tragic_algebra.py
==========================================
Cassandra Unchained "Tragic Algebra" — the true cost of SBC (Method E).

  Omega    = SBC + C + max(0, SBC - B)
  OE       = N + SBC - Omega   =   N - C - max(0, SBC - B)
  dE       = OE / N
  pooled   = sum(OE) / sum(N)

  N   = GAAP net income
  SBC = GAAP stock-based-compensation expense (added back, since OE rebases
        onto a cash-comp basis)
  C   = Cash paid to IRS for RSU/ISO/PSU tax withholding (financing CF)
  B   = Buyback dollars (financing CF, common-stock-repurchased)

Owner earnings = GAAP net income, minus the cash tax on vesting, minus the
stock comp that buybacks did NOT fund ("unfunded_comp" = max(0, SBC - B)):
  * Net buyers (B >= SBC): unfunded_comp = 0 -> OE = N - C. Buybacks at least
    offset comp, so there is no dilution charge; the excess buyback is genuine
    capital return and is NOT charged against owner earnings.
  * Net diluters (B < SBC): charge the comp buybacks left unfunded (SBC - B).

This reproduces Burry / Cassandra-Unchained's published pooled-dE (validated
live at his 10yr window: ADBE Omega = SBC + C = $16.0B -> dE 88.2% vs his 88.3%;
NOW net-diluter -> negative). It deliberately does NOT use gross buybacks alone
or the year-over-year diluted-share delta (dS*P) — that delta is noisy and is
contaminated by non-comp issuance (convertibles, secondaries, M&A), which
over-charges serial acquirers. Only clean dollar inputs are used.

`share_change` and `avg_share_price` are still recorded per year for display,
but no longer enter Omega.

If FMP doesn't surface a clean RSU-tax line we fall back to
  C_estimate = SBC * 0.37
Flagged as `cash_tax_withholding_estimated=True` for UI display.
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

        # Method E — Omega = SBC + C + max(0, SBC - B). Charge the cash tax on
        # vesting plus the stock comp buybacks left unfunded. Gross buybacks and
        # the noisy/M&A-contaminated dS*P term are NOT used.
        omega: Optional[float] = None
        unfunded_comp: Optional[float] = None
        genuine_return: Optional[float] = None
        is_diluter: Optional[bool] = None
        if y.sbc_expense is not None:
            sbc = y.sbc_expense
            unfunded_comp = max(0.0, sbc - b_val)
            genuine_return = max(0.0, b_val - sbc)
            is_diluter = b_val < sbc
            omega = sbc
            omega += c_val if c_val is not None else 0.0
            omega += unfunded_comp

        # OE = N + SBC - Omega = N - C - max(0, SBC - B)
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
                unfunded_comp=unfunded_comp,
                genuine_buyback_return=genuine_return,
                is_net_diluter=is_diluter,
                omega=omega,
                owner_earnings=oe,
                delta_e=de,
            )
        )

    pooled = (sum_oe / sum_n) if (has_any_year and sum_n > 0) else None
    # Stability guard: pooled ΔE = ΣOE/ΣN is only meaningful for solidly
    # profitable windows. It sign-flips when ΣN < 0 and explodes when ΣN is a
    # tiny positive (e.g. DOCU −1464%, CRWD +555%). Burry reports ΔE only for
    # profitable names and marks the rest N/A — mirror that by nulling ΣN ≤ 0
    # (above) and any out-of-band ratio. NOW (−1.15) and ADSK/ADBE stay in band.
    if pooled is not None and abs(pooled) > 2.0:
        pooled = None
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
