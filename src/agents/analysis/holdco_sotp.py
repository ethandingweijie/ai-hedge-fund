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


#: Bases that produce an EQUITY value (debt already inside it), so they never
#: call for consolidated net debt to be deducted.
EQUITY_BASES = frozenset({"market_stake", "pe_range", "fixed_value", "nil"})


def enabled() -> bool:
    return os.getenv(FLAG, "").strip().lower() in ("1", "true", "yes", "on")


#: Comma-separated canonical tickers the look-through may value live. Unset
#: means every holdco with a template. Set on 2026-09-15 to the four names whose
#: parent-debt NAV sat within 30% of price (Jardine C&C, Kingboard, ThaiBev,
#: Wilmar); PCRD, GenScript, SingPost and Olam stay off until their templates
#: are reviewed, and CITIC / Swire until their division EBITDA completes.
TICKERS_ENV = "FEATURE_RESOURCE_HOLDCO_TICKERS"


def enabled_for(ticker: str) -> bool:
    """The flag is on AND, when an allowlist is set, this ticker is on it.

    A template marked `"stage": "staging"` is never live, allowlist or not:
    an unset allowlist means every template, so the hold has to live on the
    template itself (Baidu, pending the Kunlunxin filing)."""
    if not enabled() or is_staging(ticker):
        return False
    raw = os.getenv(TICKERS_ENV, "").strip()
    if not raw:
        return True
    from src.tools.ticker_canonical import canonical_ticker
    allowed = {canonical_ticker(t.strip()) for t in raw.split(",") if t.strip()}
    return canonical_ticker(ticker or "") in allowed


def is_staging(ticker: str) -> bool:
    """True for a template held in staging / internal research only."""
    return ((template_for(ticker) or {}).get("stage") or "").lower() == "staging"


def _load() -> dict:
    global _CACHE
    if _CACHE is None:
        try:
            _CACHE = json.loads(_PATH.read_text(encoding="utf-8"))
        except Exception:                                  # noqa: BLE001
            _CACHE = {"templates": {}}
    return _CACHE


def template_for(ticker: str) -> Optional[dict]:
    """The look-through template for a holdco, or None.

    A dual-listed company has ONE template under its company key (the ADR):
    09888.HK resolves to BIDU. The template values the company; each listing
    then converts to its own currency and divides by its own share count, so
    the HK line and the ADR evaluate identical SOTP maths (ADS ratio parity
    falls out of the share counts)."""
    from src.tools.ticker_canonical import canonical_ticker
    templates = _load().get("templates") or {}
    hit = templates.get(canonical_ticker(ticker))
    if hit is None:
        try:
            from src.data.dual_listings import company_key
            hit = templates.get(company_key(ticker))
        except Exception:                                  # noqa: BLE001
            hit = None
    return hit


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


def _net_debt_of(ticker: str, end_date: str) -> Optional[tuple[float, str]]:
    """(total debt - cash, currency) for a ticker, or None."""
    try:
        from src.tools.api import search_line_items
        rows = search_line_items(ticker, ["total_debt", "cash_and_equivalents"], end_date, limit=1)
        if not rows:
            return None
        debt = getattr(rows[0], "total_debt", None)
        cash = getattr(rows[0], "cash_and_equivalents", None)
        if not isinstance(debt, (int, float)) or not isinstance(cash, (int, float)):
            return None
        return float(debt) - float(cash), getattr(rows[0], "currency", None) or "USD"
    except Exception:                                      # noqa: BLE001
        return None


def parent_net_debt(ticker: str, end_date: str, consolidated_net_debt: Optional[float],
                    to_ccy: str) -> Optional[float]:
    """Consolidated net debt less the net debt of majority-owned LISTED stakes.

    Consolidated accounts carry 100% of a majority-owned subsidiary's
    borrowings, but the look-through marks that stake at MARKET, where its own
    debt is already netted inside the market cap. Subtracting consolidated net
    debt therefore counted it twice: Jardine C&C's NAV came out at 49% below
    price with Astra's borrowings taken off an Astra stake valued at market.
    The whole subsidiary figure is removed, not the parent's share, because the
    consolidated number holds all of it (the same rule as residual_ebitda).

    Minority stakes are equity-accounted -- their debt was never consolidated
    -- so nothing is removed for them. Returns None when any majority stake's
    net debt cannot be fetched or converted: an unverifiable subtraction is
    refused, and the caller declines the look-through rather than guess.
    `consolidated_net_debt` is already in `to_ccy` (the engine's FX block).
    """
    tpl = template_for(ticker)
    if not tpl:
        return None
    # A template may state its own dated parent-level net debt when the feed's
    # balance sheet lags a transaction that changed it: Olam's FMP figure
    # (S$13.83bn) predates the US$1.88bn Olam Agri Tranche 1 proceeds (net
    # debt S$8,880.6m at 30 Jun 2026); SingPost's predates the Australia sale.
    override = tpl.get("net_debt_override")
    if isinstance(override, dict) and isinstance(override.get("value"), (int, float)):
        scale = override.get("units_multiplier")
        scale = float(scale) if isinstance(scale, (int, float)) and scale > 0 else 1.0
        rate = _fx((override.get("currency") or tpl.get("currency") or to_ccy).upper(), to_ccy)
        return None if rate is None else float(override["value"]) * scale * rate
    if not isinstance(consolidated_net_debt, (int, float)):
        return None
    consolidated = float(consolidated_net_debt)
    removed = 0.0
    for d in tpl.get("divisions") or []:
        stake = d.get("stake_pct")
        if d.get("basis") != "market_stake" or stake is None or stake < 0.5:
            continue
        sub = _net_debt_of(d.get("listed") or "", end_date)
        if sub is None:
            return None
        rate = _fx(sub[1], to_ccy)
        if rate is None:
            return None
        removed += sub[0] * rate
    # Removing more than the consolidated figure is not a parent with net
    # cash, it is a subsidiary whose "debt" is not corporate leverage: CITIC
    # Bank's borrowings turned CITIC's HKD 1,989bn consolidated net debt into
    # HKD -1,142bn. A bank's funding is its business, so the subtraction is
    # refused rather than published.
    if consolidated > 0 and removed > consolidated:
        return None
    return consolidated - removed


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
    _SELF_VALUING = {"market_stake", "transaction_anchor", "cap_rate",
                     "ev_ebit_range", "nil", "pe_range", "fixed_value", "revenue_multiple"}
    pending = [d for d in divs if d.get("basis") not in _SELF_VALUING]
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


def _stated_division_value(div: dict, to_ccy: str
                          ) -> Optional[tuple[float, dict]]:
    """Value a division from a figure recorded in the template.

    Four bases, each answering a question a multiple cannot:

      transaction_anchor  a real transaction priced the division -- SALIC paid
                          US$1.24bn for 35.4% of Olam Agri, marking it at
                          ~US$3.5bn. A completed deal outranks a comp set.
      cap_rate            an income-producing property is worth its net income
                          over a yield, not a multiple of it. SingPost Centre
                          at S$42.1m and 4.0-4.5% is S$0.94-1.05bn.
      ev_ebit_range       a stated segment EBIT against a peer EV/EBIT band.
      nil                 an explicit, reasoned zero -- NOT the same as a
                          missing division. A stub being wound down is worth
                          about nothing and saying so is a judgement; failing
                          to value it is an omission. The distinction matters
                          because one is a number and the other is a gap.

    Returns (value in `to_ccy`, detail) or None when the inputs are unusable.
    """
    basis = div.get("basis")
    src_ccy = (div.get("currency") or to_ccy or "USD").upper()
    rate = _fx(src_ccy, to_ccy)
    if rate is None:
        return None
    # Segment tables are published in MILLIONS and market caps and net debt
    # are absolute, so a template that records "785.4" against a net debt of
    # 13,831,993,000 subtracts thirteen billion from twelve thousand. The
    # scale is declared per division rather than inferred, because inferring
    # it from magnitude is exactly how that error survives.
    scale = div.get("units_multiplier")
    scale = float(scale) if isinstance(scale, (int, float)) and scale > 0 else 1.0
    rate = rate * scale

    if basis == "nil":
        return 0.0, {"rationale": div.get("source") or "explicit zero"}

    if basis == "fixed_value":
        # A dated, sourced amount: a monetisation pathway after a haircut
        # (Keppel's legacy rigs), or a claim ahead of ordinary shareholders
        # (perpetual securities) recorded as a negative with negative_ok.
        amount_ = div.get("amount")
        if not isinstance(amount_, (int, float)):
            return None
        if amount_ < 0 and not div.get("negative_ok"):
            return None
        return float(amount_) * rate, {"amount": float(amount_), "currency": src_ccy}

    if basis == "revenue_multiple":
        # ENTERPRISE value on revenue x EV/Sales band -- for growth units
        # valued on sales (Baidu AI Cloud at 4.6-5x). Not an equity basis:
        # a template using it takes a parent-level net debt figure.
        revenue = div.get("revenue")
        lo, hi = (div.get("multiple_range") or [None, None])
        if not isinstance(revenue, (int, float)) or revenue <= 0 or lo is None or hi is None:
            return None
        return revenue * (lo + hi) / 2.0 * rate, {
            "revenue": revenue, "multiple_range": [lo, hi], "currency": src_ccy,
            "value_low": revenue * lo * rate, "value_high": revenue * hi * rate}

    if basis == "pe_range":
        # EQUITY value: segment net profit x P/E. The segment's own project
        # and operating-company debt is already inside that profit (its
        # interest is deducted), which is how Keppel's and Sembcorp's brokers
        # value them -- so group net debt must not be taken off again.
        profit = div.get("net_profit")
        lo, hi = (div.get("multiple_range") or [None, None])
        if not isinstance(profit, (int, float)) or lo is None or hi is None:
            return None
        if profit <= 0 and not div.get("negative_ok"):
            return None
        return profit * (lo + hi) / 2.0 * rate, {
            "net_profit": profit, "multiple_range": [lo, hi], "currency": src_ccy,
            "equity_value": True,
            "value_low": profit * lo * rate, "value_high": profit * hi * rate}

    if basis == "transaction_anchor":
        ev = div.get("enterprise_value")
        if not isinstance(ev, (int, float)) or ev <= 0:
            return None
        return float(ev) * rate, {"enterprise_value": float(ev),
                                  "currency": src_ccy}

    earnings = div.get("ebit")
    if not isinstance(earnings, (int, float)):
        return None

    if basis == "cap_rate":
        lo, hi = (div.get("cap_rate_range") or [None, None])
        if not lo or not hi or lo <= 0 or hi <= 0:
            return None
        # A LOWER cap rate is a HIGHER value, so the midpoint is taken on the
        # rate and not on the two values it implies.
        mid = (lo + hi) / 2.0
        if earnings <= 0:
            return None
        return (earnings / mid) * rate, {
            "ebit": earnings, "cap_rate_range": [lo, hi], "currency": src_ccy,
            "value_low": (earnings / hi) * rate,
            "value_high": (earnings / lo) * rate}

    # ev_ebit_range
    lo, hi = (div.get("multiple_range") or [None, None])
    if lo is None or hi is None:
        return None
    if earnings <= 0:
        # A negative EBIT times a positive multiple is a negative value, which
        # is right for a central-cost line and wrong for an operating stub.
        # Only `negative_ok` divisions are allowed to subtract.
        if not div.get("negative_ok"):
            return None
    mid = earnings * (lo + hi) / 2.0
    return mid * rate, {"ebit": earnings, "multiple_range": [lo, hi],
                        "currency": src_ccy,
                        "value_low": earnings * lo * rate,
                        "value_high": earnings * hi * rate}


def _apply_toggles(tpl: dict, overrides: Optional[dict]) -> tuple[list[dict], dict]:
    """Divisions with any switched-on scenario toggle applied, and the toggles.

    A toggle is declared on the template (`scenario_toggles`, all defaulting to
    False) and a division opts in with `toggle` + `toggle_override`. Only an
    explicit True from the caller or the template switches it on -- so a
    speculative case (Kunlunxin at a US$50bn unfiled IPO target, +US$79/ADS
    on Baidu) can be stress-tested but never becomes the baseline by default."""
    toggles = {k: bool(v) for k, v in (tpl.get("scenario_toggles") or {}).items()}
    for k, v in (overrides or {}).items():
        if k in toggles:
            toggles[k] = bool(v)
    divisions = []
    for div in tpl.get("divisions") or []:
        name = div.get("toggle")
        if name and toggles.get(name) and isinstance(div.get("toggle_override"), dict):
            div = {**div, **div["toggle_override"], "scenario": name}
        divisions.append(div)
    return divisions, toggles


def look_through_value(ticker: str, end_date: str, *,
                       ebitda_by_division: Optional[dict] = None,
                       discount: Optional[float] = None,
                       net_debt: Optional[float] = None,
                       scenario_toggles: Optional[dict] = None) -> Optional[dict]:
    """Sum the parts and apply the holding-company discount.

    Discounts compose in one direction only: a division's own `discount_pct`
    is applied to that part, and the template's `holdco_discount` is applied
    once to the total afterwards. A name discounted at the stake therefore
    carries a group discount of zero -- otherwise the same haircut would be
    taken twice and nobody reading the output could tell.

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
    divisions, toggles = _apply_toggles(tpl, scenario_toggles)

    parts, skipped = [], []
    for div in divisions:
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
            # A discount can belong to a SINGLE STAKE rather than to the
            # group. Kingboard's haircut is on the Laminates holding, because
            # it monetises tranches through placements at 9-12% below last
            # close, which caps how far the holdco gap can narrow; GenScript's
            # is on the Legend mark, for Nasdaq biotech beta and the Carvykti
            # execution overhang. Neither is a statement about the parent's
            # other businesses, and applying it at group level would say it
            # was.
            _d = div.get("discount_pct")
            _d = float(_d) if isinstance(_d, (int, float)) else 0.0
            parts.append({"division": name, "basis": "market_stake",
                          "listed": listed, "stake_pct": stake,
                          "currency": src_ccy, "fx_to_reporting": rate,
                          "discount_pct": _d,
                          "gross_value": mcap * stake * rate,
                          "value": mcap * stake * rate * (1.0 - _d)})
            continue
        # ── bases that carry their own figure ────────────────────────────
        # SGX publishes no machine-readable segment note, so for Olam and
        # SingPost the segment economics are recorded in the template with the
        # fiscal year and currency they were reported in. That makes them
        # STALE-ABLE in a way a parsed figure is not, which is why every such
        # division states its own `fiscal_year` and the result reports it.
        if basis in ("transaction_anchor", "cap_rate", "ev_ebit_range", "nil", "pe_range", "fixed_value",
                     "revenue_multiple"):
            _r = _stated_division_value(div, ccy)
            if _r is None:
                skipped.append({"division": name,
                                "reason": f"{basis}: figure missing or unusable"})
                continue
            _val, _detail = _r
            _d = div.get("discount_pct")
            _d = float(_d) if isinstance(_d, (int, float)) else 0.0
            parts.append({"division": name, "basis": basis,
                          "discount_pct": _d,
                          "gross_value": _val,
                          "value": _val * (1.0 - _d),
                          "fiscal_year": div.get("fiscal_year"),
                          **({"scenario": div["scenario"]} if div.get("scenario") else {}),
                          **_detail})
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
        _d = div.get("discount_pct")
        _d = float(_d) if isinstance(_d, (int, float)) else 0.0
        _mid = ebitda * (lo + hi) / 2.0
        parts.append({"division": name, "basis": basis,
                      "multiple_range": [lo, hi], "ebitda": ebitda,
                      "discount_pct": _d,
                      "value_low": ebitda * lo * (1.0 - _d),
                      "value_high": ebitda * hi * (1.0 - _d),
                      "gross_value": _mid,
                      "value": _mid * (1.0 - _d)})

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
        "scenario_toggles": toggles,
        # A SOTP missing a division is not conservative, it is wrong. The
        # consumer must be able to see that before using the number.
        "complete": not skipped,
    }


def value_per_share(ticker: str, end_date: str, shares: float, *,
                    to_currency: Optional[str] = None,
                    ebitda_by_division: Optional[dict] = None,
                    net_debt: Optional[float] = None,
                    scenario_toggles: Optional[dict] = None) -> Optional[float]:
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
    # `net_debt` comes in `to_currency` (the listing's), but the parts sum in
    # the TEMPLATE currency. They only differ for a dual-listed company on one
    # template -- 09888.HK trades in HKD against Baidu's USD template -- where
    # subtracting HKD from USD overstated the HK line's NAV 3.6x.
    tpl = template_for(ticker) or {}
    rep_ccy = (tpl.get("currency") or currency_of(ticker)).upper()
    if isinstance(net_debt, (int, float)) and to_currency and to_currency.upper() != rep_ccy:
        _nd_rate = _fx(to_currency.upper(), rep_ccy)
        if _nd_rate is None:
            return None
        net_debt = net_debt * _nd_rate
    res = look_through_value(ticker, end_date,
                             ebitda_by_division=ebitda_by_division,
                             net_debt=net_debt,
                             scenario_toggles=scenario_toggles)
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
