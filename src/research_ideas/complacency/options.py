"""
src/research_ideas/complacency/options.py
==========================================
yfinance-backed put-option recommender for Complacency-flagged tickers.

For a Strong-Short or Watch verdict, picks a put strike + expiry from the
live chain matching a deterministic rule:

  Strong-Short (composite ≥ 7.5)  →  8-12% OTM, 90-180 days to expiry
  Watch         (composite 6-7.4) →  15-25% OTM, 180-365 days to expiry

Liquidity floor: open interest ≥ 50, bid > 0 (avoids ghost contracts).

v1 limitations (per spec §10):
  - No IV-percentile filter (needs historical IV cache — v2)
  - No earnings-window avoidance (v2 via FMP earnings calendar)
  - No Greeks (only impliedVolatility from yfinance)
  - yfinance is unofficial — may break on Yahoo schema drift; wrapped in
    try/except, returns None on any failure (caller treats as "no rec")
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, date
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class PutSelection:
    strike: float
    strike_pct_otm: float       # negative; -0.12 == 12% OTM
    expiry: str                 # ISO yyyy-mm-dd
    days_to_expiry: int
    bid: Optional[float] = None
    ask: Optional[float] = None
    mid: Optional[float] = None
    implied_volatility: Optional[float] = None
    open_interest: Optional[int] = None
    volume: Optional[int] = None
    rationale: str = ""
    contract_symbol: Optional[str] = None


# ─── Verdict → selection profile ──────────────────────────────────────────


@dataclass
class _SelectionProfile:
    target_otm_pct: float       # e.g. 0.10 = 10% OTM (positive number)
    otm_band: tuple[float, float]   # min, max OTM acceptable
    tenor_band_days: tuple[int, int]  # min, max DTE acceptable
    rationale_template: str


_PROFILES: dict[str, _SelectionProfile] = {
    "Strong-Short": _SelectionProfile(
        target_otm_pct=0.10,
        otm_band=(0.05, 0.20),
        tenor_band_days=(60, 200),
        rationale_template=(
            "Strong-Short ({comp:.1f}/8) — {otm:.0%} OTM at {dte}-day tenor. "
            "High conviction window for the catalyst to fire; leveraged convex payoff. "
            "Liquid chain (OI {oi})."
        ),
    ),
    "Watch": _SelectionProfile(
        target_otm_pct=0.20,
        otm_band=(0.10, 0.35),
        tenor_band_days=(150, 400),
        rationale_template=(
            "Watch ({comp:.1f}/8) — {otm:.0%} OTM at {dte}-day tenor. Moderate conviction; "
            "longer expiry for theta cushion while thesis develops. Chain liquid (OI {oi})."
        ),
    ),
}


# ─── Public selector ──────────────────────────────────────────────────────


def select_put_recommendation(
    ticker: str,
    composite: float,
    verdict: str,
    current_price: float,
    min_open_interest: int = 50,
) -> Optional[PutSelection]:
    """
    Returns a PutSelection or None.

    None reasons:
      - verdict not in {Strong-Short, Watch}
      - current_price missing / non-positive
      - yfinance failure (network, schema change, no chain)
      - no expiry inside the verdict's tenor band
      - no put in band passing liquidity floor
    """
    if verdict not in _PROFILES:
        return None
    if not current_price or current_price <= 0:
        return None

    profile = _PROFILES[verdict]
    today = date.today()

    try:
        import yfinance as yf
    except ImportError:
        logger.warning("yfinance not installed — put recommendations disabled.")
        return None

    try:
        tkr = yf.Ticker(ticker)
        expiries = list(getattr(tkr, "options", []) or [])
    except Exception as exc:
        logger.warning("yfinance Ticker(%s).options failed: %s", ticker, exc)
        return None

    if not expiries:
        return None

    # Filter expiries to those inside the verdict's tenor band.
    candidate_expiries: list[tuple[str, int]] = []
    for e in expiries:
        try:
            edate = date.fromisoformat(e)
            dte = (edate - today).days
            if profile.tenor_band_days[0] <= dte <= profile.tenor_band_days[1]:
                candidate_expiries.append((e, dte))
        except ValueError:
            continue

    if not candidate_expiries:
        return None

    # Pick the expiry whose DTE is closest to the midpoint of the tenor band.
    midpoint_dte = sum(profile.tenor_band_days) / 2
    candidate_expiries.sort(key=lambda x: abs(x[1] - midpoint_dte))

    target_strike = current_price * (1 - profile.target_otm_pct)
    strike_min = current_price * (1 - profile.otm_band[1])  # deepest OTM allowed
    strike_max = current_price * (1 - profile.otm_band[0])  # shallowest OTM allowed

    # Walk expiries until we find a liquid put inside the OTM band.
    for expiry, dte in candidate_expiries:
        try:
            chain = tkr.option_chain(expiry)
        except Exception as exc:
            logger.debug("option_chain(%s, %s) failed: %s", ticker, expiry, exc)
            continue

        puts = chain.puts
        if puts is None or len(puts) == 0:
            continue

        # Filter to in-band + liquid contracts.
        in_band = puts[
            (puts["strike"] >= strike_min)
            & (puts["strike"] <= strike_max)
            & (puts["openInterest"].fillna(0) >= min_open_interest)
            & (puts["bid"].fillna(0) > 0)
        ]
        if len(in_band) == 0:
            continue

        # Pick the put with strike closest to the target.
        in_band = in_band.copy()
        in_band["strike_diff"] = (in_band["strike"] - target_strike).abs()
        in_band = in_band.sort_values("strike_diff")
        best = in_band.iloc[0]

        strike = float(best["strike"])
        bid = _to_float(best.get("bid"))
        ask = _to_float(best.get("ask"))
        mid = (bid + ask) / 2 if (bid is not None and ask is not None) else None
        iv = _to_float(best.get("impliedVolatility"))
        oi = _to_int(best.get("openInterest"))
        vol = _to_int(best.get("volume"))
        contract_symbol = best.get("contractSymbol") or None
        strike_pct_otm = (strike / current_price) - 1.0  # negative for OTM puts

        rationale = profile.rationale_template.format(
            comp=composite,
            otm=abs(strike_pct_otm),
            dte=dte,
            oi=oi or 0,
        )

        return PutSelection(
            strike=strike,
            strike_pct_otm=strike_pct_otm,
            expiry=expiry,
            days_to_expiry=dte,
            bid=bid,
            ask=ask,
            mid=mid,
            implied_volatility=iv,
            open_interest=oi,
            volume=vol,
            rationale=rationale,
            contract_symbol=contract_symbol,
        )

    # No liquid contract in any expiry within the tenor band.
    return None


# ─── helpers ──────────────────────────────────────────────────────────────


def _to_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
        if f != f:  # NaN
            return None
        return f
    except (TypeError, ValueError):
        return None


def _to_int(v) -> Optional[int]:
    f = _to_float(v)
    return int(f) if f is not None else None


if __name__ == "__main__":
    # Manual smoke:
    #   $env:FMP_API_KEY = "..."
    #   .\.venv\Scripts\python.exe -m src.research_ideas.complacency.options
    logging.basicConfig(level=logging.INFO)
    import sys
    ticker = sys.argv[1] if len(sys.argv) > 1 else "NVDA"
    verdict = sys.argv[2] if len(sys.argv) > 2 else "Watch"
    # NB: price would normally come from FMP; for this smoke test use yfinance's quote
    import yfinance as yf
    t = yf.Ticker(ticker)
    px = float(t.info.get("regularMarketPrice") or t.info.get("currentPrice") or 0)
    print(f"{ticker}  price=${px:.2f}  verdict={verdict}")
    rec = select_put_recommendation(ticker, composite=7.0, verdict=verdict, current_price=px)
    if rec:
        print(f"  Strike    : ${rec.strike}")
        print(f"  OTM       : {rec.strike_pct_otm * 100:.1f}%")
        print(f"  Expiry    : {rec.expiry}  ({rec.days_to_expiry} days)")
        print(f"  Bid/Ask   : ${rec.bid} / ${rec.ask}    Mid ${rec.mid}")
        print(f"  IV        : {rec.implied_volatility:.2%}" if rec.implied_volatility else "  IV: N/A")
        print(f"  OI/Vol    : {rec.open_interest}  /  {rec.volume}")
        print(f"  Contract  : {rec.contract_symbol}")
        print(f"  Rationale : {rec.rationale}")
    else:
        print("  No put recommendation (chain illiquid / no qualifying expiry).")
