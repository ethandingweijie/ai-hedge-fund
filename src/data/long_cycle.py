"""Balance-sheet reading for the Backlog-Gated Long Cycle eligibility gate.

One figure: how much of a company's operating working-capital assets its
customers fund in advance.

    share = contract liabilities / (receivables + inventory)

Contract liabilities are customer advances, progress payments and reservation
fees (FMP: deferred revenue). Measured against working-capital ASSETS and not
NET working capital, on purpose: for exactly the companies this gate is about,
the advances are large enough to drive net working capital to zero or below
(GE Vernova FY2025: -$0.75bn), and a share of a negative number says nothing.

Kept out of dcf_agent because the engine's main statement request has a pinned
field list, and because the engine reads inventory at exactly one site (the
inventory-stress gate). This runs only for a ticker that already has an
owner-accepted backlog, so it costs one small request for a handful of names.
"""
from __future__ import annotations

from typing import Optional

_FIELDS = ("deferred_revenue", "accounts_receivable", "inventory")


def contract_liability_share(ticker: str, end_date: str) -> Optional[dict]:
    """{share, contract_liabilities, receivables, inventory, period} or None.

    None when contract liabilities are not reported, or there is no receivable
    or inventory base to measure them against. Never raises.
    """
    try:
        from src.tools.api import search_line_items
        rows = search_line_items(ticker, list(_FIELDS), end_date, period="annual", limit=1)
    except Exception:                                      # noqa: BLE001
        return None
    if not rows:
        return None
    row = rows[0]
    cl, ar, inv = (getattr(row, f, None) for f in _FIELDS)
    base = (ar or 0.0) + (inv or 0.0)
    if not cl or cl <= 0 or base <= 0:
        return None
    return {"share": float(cl) / float(base), "contract_liabilities": float(cl),
            "receivables": float(ar or 0.0), "inventory": float(inv or 0.0),
            "period": getattr(row, "report_period", None)}
