"""Look-through SOTP for conglomerate holding companies.

CITIC, CK Hutchison and Swire Pacific route to `Financials/Holding Company`,
whose anchor `SOTP / NAV` carries 0.70 weight and is `implementable: False` --
so 70% of the valuation was a proxy. Bottom-up segment modelling cannot fix
that: their filings publish segment revenue and profit but no segment BALANCE
SHEET, so there is no basis for a per-division equity bridge.

What practitioners do instead, and what this implements:

  * every LISTED stake is marked at MARKET -- shares x price x ownership,
    never balance-sheet carrying value, which for a long-held stake understates
    by an order of magnitude;
  * unlisted operating divisions are valued on peer EV/EBITDA;
  * the parts are summed and a HOLDING-COMPANY DISCOUNT is applied once, at
    the total, never smeared into the individual multiples where it would be
    invisible.

Missing ownership percentages are refused, not guessed. A wrong stake on a
listed subsidiary moves the answer further than any multiple choice, so a
division whose `stake_pct` is unsourced is skipped and the result is marked
incomplete.

Behind FEATURE_RESOURCE_HOLDCO_MAP_V2, default off.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

_PATH = Path(__file__).resolve().parents[2] / "data" / "holdco_sotp_templates.json"
_CACHE: Optional[dict] = None

FLAG = "FEATURE_RESOURCE_HOLDCO_MAP_V2"


def enabled() -> bool:
    return os.getenv(FLAG, "").strip().lower() in ("1", "true", "yes", "on")


def _load() -> dict:
    global _CACHE
    if _CACHE is None:
        try:
            _CACHE = json.loads(_PATH.read_text(encoding="utf-8"))
        except Exception:                                  # noqa: BLE001
            _CACHE = {"templates": {}}
    return _CACHE


def template_for(ticker: str) -> Optional[dict]:
    """The look-through template for a holdco, or None."""
    from src.tools.ticker_canonical import canonical_ticker
    return (_load().get("templates") or {}).get(canonical_ticker(ticker))


def _market_value(listed: str, end_date: str) -> Optional[float]:
    """Market capitalisation of a listed subsidiary, in its own currency."""
    try:
        from src.tools.api import get_market_cap
        from src.tools.fmp_transcripts import to_fmp_symbol
        return get_market_cap(to_fmp_symbol(listed), end_date)
    except Exception:                                      # noqa: BLE001
        return None


def look_through_value(ticker: str, end_date: str, *,
                       ebitda_by_division: Optional[dict] = None,
                       discount: Optional[float] = None) -> Optional[dict]:
    """Sum the parts and apply one holding-company discount.

    `ebitda_by_division` supplies EBITDA for the unlisted operating divisions;
    divisions without it are skipped and reported, never assumed to be zero --
    a missing division silently valued at nil is the failure mode that makes a
    SOTP look conservative while being wrong.
    """
    tpl = template_for(ticker)
    if not tpl:
        return None
    ebitda_by_division = ebitda_by_division or {}

    parts, skipped = [], []
    for div in tpl.get("divisions") or []:
        name, basis = div.get("name"), div.get("basis")
        if basis == "market_stake":
            stake = div.get("stake_pct")
            if stake is None:
                skipped.append({"division": name,
                                "reason": "ownership percentage not sourced"})
                continue
            mcap = _market_value(div.get("listed") or "", end_date)
            if not mcap:
                skipped.append({"division": name,
                                "reason": f"no market cap for {div.get('listed')}"})
                continue
            parts.append({"division": name, "basis": "market_stake",
                          "listed": div.get("listed"), "stake_pct": stake,
                          "value": mcap * stake})
            continue
        ebitda = ebitda_by_division.get(name)
        if ebitda is None:
            skipped.append({"division": name, "reason": "no EBITDA supplied"})
            continue
        lo, hi = (div.get("multiple_range") or [None, None])
        if lo is None:
            skipped.append({"division": name, "reason": "no multiple range"})
            continue
        parts.append({"division": name, "basis": basis,
                      "multiple_range": [lo, hi], "ebitda": ebitda,
                      "value_low": ebitda * lo, "value_high": ebitda * hi,
                      "value": ebitda * (lo + hi) / 2.0})

    if not parts:
        return None
    gross = sum(p["value"] for p in parts)
    d_lo, d_hi = tpl.get("holdco_discount") or [0.0, 0.0]
    disc = discount if discount is not None else (d_lo + d_hi) / 2.0
    return {
        "ticker": ticker,
        "name": tpl.get("name"),
        "parts": parts,
        "skipped": skipped,
        "gross_asset_value": gross,
        "holdco_discount": disc,
        "holdco_discount_range": [d_lo, d_hi],
        "net_asset_value": gross * (1.0 - disc),
        # A SOTP missing a division is not conservative, it is wrong. The
        # consumer must be able to see that before using the number.
        "complete": not skipped,
    }
