"""
src/research_ideas/sw46/roic.py
================================
Fully-adjusted ROIC, Cassandra Unchained / Scion definition.

  numerator   = OE - interest_income - capital_lease_payments - other_expense
  denominator = total_capital - LT_operating_leases - net_cash + other_capital

  total_capital = total_debt + shareholders_equity
  net_cash      = cash + ST_investments + LT_marketable_securities
  other_capital = purchase_obligations (productive, non-cancellable)
                  + customer_float + restricted_cash + crypto + loans-held-for-settlement
                  (v1 hard-coded to 0 unless overridden via JSON)
"""
from __future__ import annotations

from typing import Optional

from src.research_ideas.sw46.data_fetch import TickerBundle, YearlyStatement
from src.research_ideas.sw46.schemas import ROICResult, TragicAlgebraResult
from src.research_ideas.sw46.universe import load_purchase_obligations


def _latest_with_value(years: list[YearlyStatement], attr: str) -> Optional[float]:
    for y in reversed(years):
        v = getattr(y, attr, None)
        if v is not None:
            return v
    return None


def compute_roic(
    bundle: TickerBundle,
    ta: TragicAlgebraResult,
) -> ROICResult:
    if not bundle.years:
        return ROICResult()

    latest = bundle.years[-1]

    interest_income = latest.interest_income
    capital_lease_payments = latest.capital_lease_payments
    other_expense_adjustments = 0.0  # placeholder for per-company overrides

    owner_earnings = ta.latest_owner_earnings or ta.avg_owner_earnings

    numerator: Optional[float] = None
    if owner_earnings is not None:
        numerator = owner_earnings
        if interest_income is not None:
            numerator -= interest_income
        if capital_lease_payments is not None:
            numerator -= capital_lease_payments
        numerator -= other_expense_adjustments

    # Total capital = debt + equity (use most recent year balance sheet)
    total_debt = latest.total_debt or 0.0
    equity = latest.shareholders_equity or 0.0
    total_capital = total_debt + equity

    lt_op_leases = latest.long_term_operating_leases or 0.0

    net_cash = (
        (latest.cash_and_equivalents or 0.0)
        + (latest.short_term_investments or 0.0)
        + (latest.long_term_investments or 0.0)
    )

    purchase_obligations = load_purchase_obligations().get(bundle.ticker, 0.0)
    other_capital = purchase_obligations  # v1: only purchase obligations

    denominator: Optional[float] = total_capital - lt_op_leases - net_cash + other_capital

    roic: Optional[float] = None
    if numerator is not None and denominator and denominator > 0:
        roic = numerator / denominator

    return ROICResult(
        owner_earnings=owner_earnings,
        interest_income=interest_income,
        capital_lease_payments=capital_lease_payments,
        other_expense_adjustments=other_expense_adjustments,
        numerator=numerator,
        total_capital=total_capital,
        lt_operating_leases=lt_op_leases,
        net_cash=net_cash,
        purchase_obligations=purchase_obligations,
        other_capital=other_capital,
        denominator=denominator,
        roic=roic,
    )
