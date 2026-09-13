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


#: Listing suffix -> the currency that listing's market cap is quoted in.
#: A look-through sums parts from several exchanges, so every part has to be
#: converted before it is added. Jardine Cycle & Carriage reports in SGD while
#: Astra is quoted in IDR at ~190 TRILLION rupiah -- added unconverted, that
#: one line would be the entire valuation.
_SUFFIX_CCY = {".HK": "HKD", ".SI": "SGD", ".JK": "IDR", ".SS": "CNY",
               ".SZ": "CNY", ".TW": "TWD", ".T": "JPY", ".L": "GBP",
               ".AX": "AUD", ".KS": "KRW", ".NS": "INR", ".BO": "INR"}


def currency_of(listed: str) -> str:
    for suf, ccy in _SUFFIX_CCY.items():
        if (listed or "").upper().endswith(suf):
            return ccy
    return "USD"


def _market_value(listed: str, end_date: str) -> Optional[float]:
    """Market capitalisation of a listed subsidiary, in its own currency."""
    try:
        from src.tools.api import get_market_cap
        from src.tools.fmp_transcripts import to_fmp_symbol
        return get_market_cap(to_fmp_symbol(listed), end_date)
    except Exception:                                      # noqa: BLE001
        return None


def _fx(from_ccy: str, to_ccy: str) -> Optional[float]:
    if not from_ccy or not to_ccy or from_ccy == to_ccy:
        return 1.0
    try:
        from src.tools.api import get_fx_rate
        rate = get_fx_rate(from_ccy, to_ccy)
        return float(rate) if rate else None
    except Exception:                                      # noqa: BLE001
        return None


def _ebitda_of(ticker: str, end_date: str) -> Optional[tuple[float, str]]:
    """(EBITDA, currency) for a ticker, or None."""
    try:
        from src.tools.api import search_line_items
        rows = search_line_items(ticker, ["ebitda"], end_date, limit=1)
        if not rows:
            return None
        v = getattr(rows[0], "ebitda", None)
        c = getattr(rows[0], "currency", None) or "USD"
        return (float(v), c) if isinstance(v, (int, float)) and v else None
    except Exception:                                      # noqa: BLE001
        return None


def residual_ebitda(ticker: str, end_date: str, to_ccy: str) -> Optional[float]:
    """Group EBITDA less the EBITDA of the subsidiaries it CONSOLIDATES.

    Consolidated accounts carry 100% of a majority-owned subsidiary's EBITDA
    (the part that is not the parent's appears as minority interest, not as a
    smaller EBITDA), so the whole of it is removed -- not the parent's share.
    What remains is the operating earnings of everything the template values
    on a multiple rather than at market.

    Deliberately narrow: it is only returned when the template has exactly ONE
    division awaiting EBITDA. With two or more, a single residual pool cannot
    be split between them without inventing the split, and CK Hutchison's
    divisions carry multiples from 4.5x to 10x -- the allocation would drive
    the answer. Those stay declined.
    """
    tpl = template_for(ticker)
    if not tpl:
        return None
    divs = tpl.get("divisions") or []
    pending = [d for d in divs if d.get("basis") != "market_stake"]
    if len(pending) != 1:
        return None
    grp = _ebitda_of(ticker, end_date)
    if not grp:
        return None
    total, gccy = grp
    for d in divs:
        if d.get("basis") != "market_stake":
            continue
        stake = d.get("stake_pct")
        if stake is None or stake < 0.5:
            continue                      # associate: equity-accounted, not in EBITDA
        sub = _ebitda_of(d.get("listed") or "", end_date)
        if not sub:
            return None                   # cannot verify the subtraction -- refuse
        rate = _fx(sub[1], gccy)
        if rate is None:
            return None
        total -= sub[0] * rate
    if total <= 0:
        return None                       # nothing left to value
    # No guard on "nothing was removed": when every listed stake is an
    # associate the group EBITDA IS the unlisted operations, and refusing
    # there would decline a holdco the look-through can value exactly. A
    # subsidiary whose EBITDA could not be fetched already returned above.
    rate = _fx(gccy, to_ccy)
    if rate is None:
        return None
    return total * rate


def look_through_value(ticker: str, end_date: str, *,
                       ebitda_by_division: Optional[dict] = None,
                       discount: Optional[float] = None,
                       net_debt: Optional[float] = None) -> Optional[dict]:
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
    ccy = tpl.get("currency") or currency_of(ticker)

    parts, skipped = [], []
    for div in tpl.get("divisions") or []:
        name, basis = div.get("name"), div.get("basis")
        if basis == "market_stake":
            stake = div.get("stake_pct")
            if stake is None:
                skipped.append({"division": name,
                                "reason": "ownership percentage not sourced"})
                continue
            listed = div.get("listed") or ""
            mcap = _market_value(listed, end_date)
            if not mcap:
                skipped.append({"division": name,
                                "reason": f"no market cap for {listed}"})
                continue
            src_ccy = currency_of(listed)
            rate = _fx(src_ccy, ccy)
            if rate is None:
                skipped.append({"division": name,
                                "reason": f"no {src_ccy}->{ccy} rate"})
                continue
            parts.append({"division": name, "basis": "market_stake",
                          "listed": listed, "stake_pct": stake,
                          "currency": src_ccy, "fx_to_reporting": rate,
                          "value": mcap * stake * rate})
            continue
        ebitda = ebitda_by_division.get(name)
        if ebitda is None:
            ebitda = residual_ebitda(ticker, end_date, ccy)
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
    # Only PARENT-level net debt belongs here. A listed stake marked at market
    # has already netted that subsidiary's own borrowings inside its market
    # capitalisation, so subtracting consolidated net debt would count the
    # subsidiaries' debt twice. For a holdco that equity-accounts its stakes
    # the consolidated figure IS parent-level; where it is not, the caller
    # passes None and the bridge is stated without it.
    nd = float(net_debt) if isinstance(net_debt, (int, float)) else 0.0
    nav_pre_discount = gross - nd
    return {
        "ticker": ticker,
        "name": tpl.get("name"),
        "parts": parts,
        "skipped": skipped,
        "reporting_currency": ccy,
        "gross_asset_value": gross,
        "parent_net_debt": nd,
        "holdco_discount": disc,
        "holdco_discount_range": [d_lo, d_hi],
        "net_asset_value": nav_pre_discount * (1.0 - disc),
        # A SOTP missing a division is not conservative, it is wrong. The
        # consumer must be able to see that before using the number.
        "complete": not skipped,
    }


def value_per_share(ticker: str, end_date: str, shares: float, *,
                    to_currency: Optional[str] = None,
                    ebitda_by_division: Optional[dict] = None,
                    net_debt: Optional[float] = None) -> Optional[float]:
    """Look-through NAV per share, or None when the SOTP does not complete.

    A partial look-through is NOT returned. Skipping a division does not make
    the answer conservative, it makes it wrong -- and this value is the 0.70
    anchor of the Holding Company profile, so a silently-short NAV would drag
    the whole valuation down while looking like a considered number. When a
    division cannot be valued the method returns None, the blender drops it
    and renormalises, and `decline_reason` says exactly what is missing.
    """
    if not shares or shares <= 0:
        return None
    res = look_through_value(ticker, end_date,
                             ebitda_by_division=ebitda_by_division,
                             net_debt=net_debt)
    if not res or not res.get("complete"):
        return None
    nav = res.get("net_asset_value")
    if not isinstance(nav, (int, float)) or nav <= 0:
        return None
    if to_currency:
        rate = _fx(res.get("reporting_currency") or "", to_currency)
        if rate is None:
            return None
        nav = nav * rate
    return nav / shares


def decline_reason(ticker: str, end_date: str, *,
                   ebitda_by_division: Optional[dict] = None) -> Optional[str]:
    """Why the look-through cannot be used for this ticker, or None if it can.

    Stated per division so the gap is actionable -- a missing ownership
    percentage is a question for a filing, a missing division EBITDA is a
    question for the segment note, and they are not the same problem.
    """
    if not template_for(ticker):
        return "no look-through template"
    res = look_through_value(ticker, end_date,
                             ebitda_by_division=ebitda_by_division)
    if not res:
        return "no division could be valued"
    if res.get("complete"):
        return None
    return "; ".join(f"{s['division']}: {s['reason']}"
                     for s in (res.get("skipped") or []))


def can_value(ticker: str, end_date: str, *,
              ebitda_by_division: Optional[dict] = None) -> bool:
    """True when a COMPLETE look-through exists for this ticker."""
    return decline_reason(
        ticker, end_date, ebitda_by_division=ebitda_by_division) is None
