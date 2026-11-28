"""Currency invariant on a published valuation (owner, 2026-11-28, D3).

A valuation is WITHHELD (unrated: no intrinsic value, target or rating; the run's credit is returned) when:

  * the per-share figures are labelled in a currency other than the one the stock is QUOTED in; or
  * the statements had to be converted (reporting currency != quote currency) and the base value lands outside
    [1/BAND, BAND] x the share price. A cross-currency value that far from the market is, on the record, an FX
    artifact far more often than an insight (PDD 11.8x spot, 2026-09; BABA's RMB-labelled $133) -- every one of them
    was found by a human reviewer, after publication.

The verdict has the same shape as valuation_constants.unrated_verdict, so dcf_agent's unrated path withholds it.
"""
from __future__ import annotations

import os
from typing import Optional


def band() -> float:
    try:
        v = float(os.environ.get("CURRENCY_CHECK_BAND", "5"))
        return v if v > 1 else 5.0
    except (TypeError, ValueError):
        return 5.0


def currency_verdict(output_ccy: Optional[str], listing_ccy: Optional[str], reported_ccy: Optional[str],
                     base_iv, spot) -> Optional[dict]:
    out = str(output_ccy or "").upper()
    lst = str(listing_ccy or "").upper()
    rep = str(reported_ccy or "").upper()
    if out and lst and out != lst:
        return {"code": "currency_check", "label": "Valuation withheld",
                "reason": (f"the valuation is in {out} but the stock is quoted in {lst}; it is withheld until the "
                           f"currency conversion is corrected")}
    try:
        iv, px = float(base_iv), float(spot)
    except (TypeError, ValueError):
        return None
    if not (rep and lst and rep != lst and iv > 0 and px > 0):
        return None
    ratio, b = iv / px, band()
    if ratio > b or ratio < 1.0 / b:
        return {"code": "currency_check", "label": "Valuation withheld",
                "reason": (f"the value converted from {rep} statements is {ratio:.1f}x the {lst} share price, "
                           f"outside the {1.0 / b:.1f}x-{b:.0f}x range a currency conversion error produces; it is "
                           f"withheld until it is checked")}
    return None
