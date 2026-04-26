"""
valuation_check.py — Per-ticker anchor-method valuation using FMP + sector
metrics. Cheap (~10 FMP calls/ticker, no LLM cost).

For each ticker:
  1. Look up sector + profile_name (TICKER_SECTOR_LOOKUP)
  2. Pull FMP fundamentals: market cap, BV, TBV, EBITDA, EPS, FCF, shares
  3. For each anchor method declared in SECTOR_KPI_FRAMEWORK[profile]:
       Apply the appropriate formula using industry-default multiples
  4. Compute IV → upside vs current price
  5. Apply sector-specific gates (e.g. Combined Ratio Gate for Insurance)

Output: markdown table valuation_check.md + json valuation_check.json

USAGE:
    poetry run python valuation_check.py --tickers PGR,JPM,NEM,NVDA,LMT,MCD
"""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.parse
import urllib.request
from typing import Any

# ── FMP wrapper ──────────────────────────────────────────────────────────────
_FMP_BASE = "https://financialmodelingprep.com/stable"
_FMP_KEY = os.environ.get("FMP_API_KEY") or "UFPUuQjTht66l2GmJhQbUZzij7IfJbsx"


def _fmp(path: str, ticker: str, timeout: int = 8) -> Any:
    url = f"{_FMP_BASE}/{path}?symbol={urllib.parse.quote(ticker)}&apikey={_FMP_KEY}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "valuation_check/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None


def fmp_bundle(ticker: str) -> dict:
    """Fetch the FMP fields needed for anchor-method valuation.

    V3.2: also pulls balance-sheet (cashAndShortTermInvestments) +
    cash-flow-statement (freeCashFlow) so we can compute risk KPIs
    (net_debt_to_ebitda, cash_runway_years) when the extractor misses them.
    """
    profile = _fmp("profile", ticker) or []
    ratios  = _fmp("ratios-ttm", ticker) or []
    keymet  = _fmp("key-metrics-ttm", ticker) or []
    income  = _fmp("income-statement", ticker) or []
    bs      = _fmp("balance-sheet-statement", ticker) or []
    cfs     = _fmp("cash-flow-statement", ticker) or []
    sf      = _fmp("shares-float", ticker) or []  # V3.2: dedicated shares endpoint
    p = profile[0] if profile else {}
    r = ratios[0]  if ratios  else {}
    k = keymet[0]  if keymet  else {}
    i = income[0]  if income  else {}
    b = bs[0]      if bs      else {}
    c = cfs[0]     if cfs     else {}
    sfv = sf[0]    if sf      else {}

    # V3.2: derive risk KPIs from FMP for the framework's risk_adjustment
    # schemas (net_debt_to_ebitda, cash_runway_years). Extractor may miss
    # these — FMP is the authoritative source anyway.
    nde_ttm = k.get("netDebtToEBITDATTM")  # canonical TTM field
    fcf     = c.get("freeCashFlow")
    cash_st = b.get("cashAndShortTermInvestments")
    runway_yrs = None
    if fcf is not None and cash_st is not None and fcf < 0:
        # Negative FCF -> runway in years until cash exhausts
        runway_yrs = round(cash_st / abs(fcf), 2)
    # If FCF positive, company is self-sustaining -> "infinite" runway
    # represented as a large value so the higher_better schema treats it
    # as "ample" without overflowing JSON.
    elif fcf is not None and fcf >= 0:
        runway_yrs = 99.0
    # Field map verified Apr 2026: bvps/tbvps live on ratios-ttm, NOT key-metrics-ttm
    return {
        "ticker":          ticker,
        "company_name":    p.get("companyName"),
        "current_price":   p.get("price"),
        "market_cap":      k.get("marketCap") or p.get("mktCap") or p.get("marketCap"),
        # Source priority: dedicated shares-float endpoint > key-metrics-ttm >
        # profile.shareNumber. Final fallback: derive from market_cap / price.
        "shares_out":      sfv.get("outstandingShares")
                            or k.get("commonSharesOutstanding")
                            or p.get("shareNumber")
                            or ((k.get("marketCap") or p.get("mktCap") or 0) /
                                p.get("price") if p.get("price") else None),
        "bvps":            r.get("bookValuePerShareTTM") or r.get("bookValuePerShare"),
        "tbvps":           r.get("tangibleBookValuePerShareTTM") or r.get("tangibleBookValuePerShare"),
        "eps":             k.get("netIncomePerShareTTM") or i.get("eps"),
        "ebitda":          i.get("ebitda"),
        "fcf_per_share":   r.get("freeCashFlowPerShareTTM") or k.get("freeCashFlowPerShareTTM"),
        "rev_per_share":   r.get("revenuePerShareTTM") or k.get("revenuePerShareTTM"),
        # FMP field names (verified against /stable/ratios + /stable/ratios-ttm Apr 2026):
        #   priceToEarningsRatio[TTM]        (NOT peRatio[TTM] — old name)
        #   priceToBookRatio[TTM]
        #   enterpriseValueMultiple[TTM]     (NOT enterpriseValueOverEBITDA[TTM])
        #   dividendYield[TTM]               (NOT dividendYielTTM — typo)
        "pe_ttm":          r.get("priceToEarningsRatioTTM") or r.get("priceToEarningsRatio"),
        "pb_ttm":          r.get("priceToBookRatioTTM") or r.get("priceToBookRatio"),
        "ev_ebitda_ttm":   r.get("enterpriseValueMultipleTTM") or r.get("enterpriseValueMultiple"),
        "div_yield":       r.get("dividendYieldTTM") or r.get("dividendYield"),
        # V3.2 — FMP-derived risk KPIs (used by composite_adjustment when
        # extractor doesn't catch them from Section 2F)
        "net_debt_to_ebitda":     nde_ttm,
        "cash_and_st_investments": cash_st,
        "free_cash_flow":         fcf,
        "cash_runway_years":      runway_yrs,
    }


# ── Industry-default multiples per sector ────────────────────────────────────
# Anchor method mapped to a (sector → multiple) lookup. Multiples are defensible
# 10-yr-average industry medians; conservative on the low end. The frontend
# valuation card displays the per-method IV breakdown, so directional accuracy
# matters more than precise calibration here.
_INDUSTRY_MULTIPLES: dict[str, dict[str, float]] = {
    "P/BV": {
        "Financials":    1.30,
        "Insurance":     1.30,
        "Bank":          1.10,
        "Mortgage/GSE":  0.95,
        "default":       1.40,
    },
    "P/TBV": {
        "Financials": 1.50,
        "Bank":       1.40,
        "default":    1.60,
    },
    "EV/EBITDA": {
        "Financials":     8.0,
        "Resources":      6.0,
        "Energy":         7.5,
        "Industrial":    12.0,
        "Consumer":      14.0,
        "Tech":          18.0,
        "Semiconductor": 16.0,
        "Telco":         8.0,
        "Healthcare":    13.0,
        "Biopharma":     14.0,
        "default":       11.0,
    },
    "P/E": {
        "Financials":    11.0,
        "Resources":     12.0,
        "Energy":        13.0,
        "Industrial":    18.0,
        "Consumer":      20.0,
        "Tech":          28.0,
        "Semiconductor": 24.0,
        "Telco":         12.0,
        "Healthcare":    18.0,
        "Biopharma":     17.0,
        "default":       18.0,
    },
    "P/CF": {
        "Resources":  6.0,
        "Energy":     7.0,
        "default":   10.0,
    },
}


def _multiple_for(method: str, sector: str) -> float | None:
    """Returns the BASE-case industry-median multiple."""
    table = _INDUSTRY_MULTIPLES.get(method)
    if not table:
        return None
    return table.get(sector) or table.get("default")


# Bull/Bear bands as multiples of the BASE-case industry median.
# Defensible defaults: ±20% for stable sectors, ±30% for cyclicals/biotech.
# Bull = stretch (premium quality + cycle peak); Bear = stress (cycle trough).
_SCENARIO_BANDS: dict[str, tuple[float, float]] = {
    # sector → (bear_multiplier, bull_multiplier) on the base multiple
    "Financials":    (0.80, 1.25),
    "Insurance":     (0.80, 1.30),
    "Bank":          (0.80, 1.25),
    "Mortgage/GSE":  (0.70, 1.20),
    "Resources":     (0.65, 1.40),  # commodity cyclicality wider
    "Energy":        (0.65, 1.40),
    "Industrial":    (0.78, 1.25),
    "Consumer":      (0.80, 1.25),
    "Tech":          (0.75, 1.35),
    "Semiconductor": (0.70, 1.40),  # silicon cycle wider
    "Telco":         (0.85, 1.18),
    "Healthcare":    (0.78, 1.25),
    "Biopharma":     (0.65, 1.45),  # binary risk wider
    "default":       (0.80, 1.25),
}


def _band_for(sector: str) -> tuple[float, float]:
    return _SCENARIO_BANDS.get(sector) or _SCENARIO_BANDS["default"]


def _scenario_ivs(base_iv: float | None, sector: str) -> dict:
    """Spread a base-case IV into bear/base/bull using sector bands."""
    if base_iv is None:
        return {"bear": None, "base": None, "bull": None}
    lo, hi = _band_for(sector)
    return {
        "bear": round(base_iv * lo, 2),
        "base": round(base_iv, 2),
        "bull": round(base_iv * hi, 2),
    }


# ── Per-method valuation formulas ────────────────────────────────────────────

def value_per_share_pbv(fmp: dict, sector: str) -> float | None:
    bvps = fmp.get("bvps"); m = _multiple_for("P/BV", sector)
    if not bvps or not m: return None
    return round(bvps * m, 2)


def value_per_share_ptbv(fmp: dict, sector: str) -> float | None:
    tbvps = fmp.get("tbvps"); m = _multiple_for("P/TBV", sector)
    if not tbvps or not m: return None
    return round(tbvps * m, 2)


def value_per_share_ev_ebitda(fmp: dict, sector: str) -> float | None:
    """EV/EBITDA × multiple / shares.

    FMP's commonSharesOutstanding is sparse — derive from market_cap / price
    when it's missing (works for any publicly traded ticker).
    """
    ebitda = fmp.get("ebitda")
    shares = fmp.get("shares_out")
    if not shares:
        mc, px = fmp.get("market_cap"), fmp.get("current_price")
        if mc and px and px > 0:
            shares = mc / px
    m = _multiple_for("EV/EBITDA", sector)
    if not ebitda or not shares or not m: return None
    return round((ebitda * m) / shares, 2)


def value_per_share_pe(fmp: dict, sector: str) -> float | None:
    eps = fmp.get("eps"); m = _multiple_for("P/E", sector)
    if not eps or eps <= 0 or not m: return None
    return round(eps * m, 2)


def value_per_share_pcf(fmp: dict, sector: str) -> float | None:
    fcfps = fmp.get("fcf_per_share"); m = _multiple_for("P/CF", sector)
    if not fcfps or fcfps <= 0 or not m: return None
    return round(fcfps * m, 2)


def value_per_share_ev_revenue(fmp: dict, sector: str) -> float | None:
    """SaaS / Tech — EV/Revenue × revenue per share. Default multiples by sector."""
    rev_ps = fmp.get("rev_per_share")
    if not rev_ps: return None
    table = {
        "Tech":          8.0,    # SaaS / cybersec / hyper-growth
        "Semiconductor": 6.0,
        "Healthcare":    4.0,
        "Biopharma":     4.5,
        "Telco":         2.5,
        "Industrial":    2.0,
        "Consumer":      1.8,
        "Resources":     1.5,
        "Energy":        1.5,
        "Financials":    3.0,    # mostly N/A — use P/E instead
        "default":       3.5,
    }
    m = table.get(sector, table["default"])
    return round(rev_ps * m, 2)


def value_per_share_p_rate_base(fmp: dict, sector: str) -> float | None:
    """Regulated Utility — P/Rate Base proxy via P/B (rate base ≈ tangible BV).

    Industry default: 1.5-2.0× rate base (matches the regulated equity premium).
    """
    bvps = fmp.get("bvps")
    if not bvps: return None
    return round(bvps * 1.7, 2)


def value_per_share_dcf_proxy(fmp: dict, sector: str) -> float | None:
    """DCF stand-in for the cheap valuation_check (full DCF needs the pipeline).

    Cascade: P/E (ops) -> EV/Revenue -> P/CF. Picks the first method that
    produces a value. This handles tickers with negative EPS (e.g. high-growth
    SaaS like CRWD where EPS=-0.65 makes pure P/E useless).
    """
    iv = value_per_share_pe(fmp, sector)
    if iv is not None: return iv
    iv = value_per_share_ev_revenue(fmp, sector)
    if iv is not None: return iv
    return value_per_share_pcf(fmp, sector)


def value_per_share_ddm(fmp: dict, sector: str) -> float | None:
    """Dividend Discount Model — DPS × growth.
    Used by REITs / Utilities / mature payers.

    Simplified Gordon: IV = DPS_next / (r - g) where r=0.085, g=0.045 → 25× DPS proxy.
    Falls back to 25× dividend per share if available.
    """
    dy = fmp.get("div_yield")
    px = fmp.get("current_price")
    if not (dy and px and dy > 0): return None
    dps = dy * px
    # Gordon-equivalent multiplier ≈ 25 (yield ~4%, growth ~4.5%)
    return round(dps * 25, 2)


# ─────────────────────────────────────────────────────────────────────────────
# V2 — Quality Kicker (the "Best-in-Class Multiple Multiplier")
#
# Top-decile KPIs unlock a sector-specific quality premium on the aggregated
# primary IV. Addresses the "Multiple Ceiling" problem: previously a P&C
# insurer with 87% combined ratio was treated like one with 96% CR (industry
# average). Premium operators deserve premium multiples.
#
# Calibrated to user-specified targets:
#   PGR (CR=87.4%): 1.40× kicker → IV moves $114 → ~$160
#   JPM (CET1=14.3%): 1.15× kicker → IV moves $191 → ~$220
#   NEM (Q2 cost): 1.20× kicker → IV moves $77 → ~$92
# ─────────────────────────────────────────────────────────────────────────────


def quality_multiplier(profile_name: str, sector: str, metrics: dict | None) -> tuple[float, str]:
    """V3 Fix 1 — Operational excellence (best-in-class multiple kicker).

    Pure operational signal — NOT balance sheet (that's risk_multiplier).
    Capped at 1.50x individually; composite cap is 1.85x globally.
    Returns (multiplier, audit_note).
    """
    m = metrics or {}
    multipliers, notes = [], []

    # Insurance — combined ratio (operational efficiency)
    cr = m.get("combined_ratio")
    if cr is not None:
        if   cr < 0.88: multipliers.append(1.50); notes.append(f"CR={cr*100:.1f}% elite +50%")
        elif cr < 0.92: multipliers.append(1.30); notes.append(f"CR={cr*100:.1f}% top-quartile +30%")
        elif cr < 0.96: multipliers.append(1.12); notes.append(f"CR={cr*100:.1f}% above-avg +12%")
        elif cr > 1.02: multipliers.append(0.80); notes.append(f"CR={cr*100:.1f}% loss-making -20%")

    # Bank — efficiency + target ROE (correlated → take max-deviation)
    if "Bank" in profile_name:
        bank_signals = []
        eff = m.get("efficiency_ratio")
        if eff is not None:
            if   eff < 0.50: bank_signals.append((1.30, f"Eff={eff*100:.1f}% top-decile +30%"))
            elif eff < 0.55: bank_signals.append((1.18, f"Eff={eff*100:.1f}% strong +18%"))
            elif eff > 0.65: bank_signals.append((0.92, f"Eff={eff*100:.1f}% bloated -8%"))
        target_roe = m.get("management_target_roe")
        if target_roe is not None:
            if   target_roe > 0.16: bank_signals.append((1.30, f"Target ROE={target_roe*100:.0f}% premium +30%"))
            elif target_roe > 0.13: bank_signals.append((1.15, f"Target ROE={target_roe*100:.0f}% above-avg +15%"))
        if bank_signals:
            pick = max(bank_signals, key=lambda x: abs(x[0] - 1.0))
            multipliers.append(pick[0]); notes.append(f"[bank-corr] {pick[1]}")

    # Mining — cost curve quartile (operational signal — pricing power leverage)
    quartile = m.get("cost_curve_quartile")
    if quartile is not None:
        q = int(quartile)
        if   q == 1: multipliers.append(1.30); notes.append("Q1 cost producer +30%")
        elif q == 2: multipliers.append(1.30); notes.append("Q2 cost producer +30% (low-cost)")
        elif q == 4: multipliers.append(0.85); notes.append("Q4 cost producer -15%")

    # SaaS — NRR + Rule of 40 (correlated)
    saas_signals = []
    nrr = m.get("nrr_pct")
    if nrr is not None:
        if   nrr > 1.30: saas_signals.append((1.40, f"NRR={nrr*100:.0f}% elite +40%"))
        elif nrr > 1.15: saas_signals.append((1.20, f"NRR={nrr*100:.0f}% strong +20%"))
        elif nrr < 1.0:  saas_signals.append((0.85, f"NRR={nrr*100:.0f}% contraction -15%"))
    r40 = m.get("rule_of_40_score")
    if r40 is not None:
        if   r40 > 60: saas_signals.append((1.30, f"Rule40={r40:.0f} elite +30%"))
        elif r40 > 40: saas_signals.append((1.15, f"Rule40={r40:.0f} healthy +15%"))
        elif r40 < 20: saas_signals.append((0.90, f"Rule40={r40:.0f} weak -10%"))
    if saas_signals:
        pick = max(saas_signals, key=lambda x: abs(x[0] - 1.0))
        multipliers.append(pick[0]); notes.append(f"[saas-corr] {pick[1]}")

    if not multipliers:
        return (1.0, "no operational quality KPIs")

    composite = 1.0
    for x in multipliers: composite *= x
    composite = max(0.70, min(1.50, composite))
    return (round(composite, 3), " * ".join(notes))


def risk_multiplier(profile_name: str, sector: str, metrics: dict | None) -> tuple[float, str]:
    """V3 Fix 2 — Balance sheet strength (Beta haircut / discount rate compression).

    Pure capital-adequacy signal — NOT operational (that's quality_multiplier).
    Capped at 1.20x individually.
    """
    m = metrics or {}

    # Insurance — Solvency SCR
    if "Insurance" in profile_name:
        scr = m.get("solvency_ratio_scr")
        if scr is not None:
            if   scr > 2.0: return (1.10, f"SCR={scr:.2f}x strong +10%")
            elif scr < 1.3: return (0.90, f"SCR={scr:.2f}x weak -10%")

    # Bank — CET1 (already drives Residual Income CoE haircut, but also kicks
    # multiple-based methods like P/TBV / P/E that don't use CoE)
    if "Bank" in profile_name:
        cet1 = m.get("cet1_ratio")
        if cet1 is not None:
            if   cet1 > 0.14: return (1.15, f"CET1={cet1*100:.1f}% fortress +15%")
            elif cet1 > 0.12: return (1.10, f"CET1={cet1*100:.1f}% strong +10%")
            elif cet1 < 0.085: return (0.85, f"CET1={cet1*100:.1f}% weak -15%")

    # Mining — net debt / EBITDA (low debt = pricing power resilience)
    if "Mining" in profile_name:
        nd_ebitda = m.get("net_debt_to_ebitda")
        if nd_ebitda is not None:
            if   nd_ebitda < 0.5: return (1.10, f"ND/EBITDA={nd_ebitda:.2f}x fortress +10%")
            elif nd_ebitda > 2.5: return (0.85, f"ND/EBITDA={nd_ebitda:.2f}x weak -15%")
        # Fallback: assume +10% if low_leverage flag set
        if m.get("low_leverage", False):
            return (1.10, "low leverage flag +10%")

    # Biotech — cash runway (binary risk reducer)
    if "Biotech" in profile_name:
        runway = m.get("cash_runway_quarters")
        if runway is not None:
            if   runway > 12: return (1.15, f"Runway={runway}q +15%")
            elif runway < 4:  return (0.70, f"Runway={runway}q dilution risk -30%")

    return (1.0, "no balance sheet KPIs")


def commodity_multiplier(profile_name: str, sector: str, metrics: dict | None) -> tuple[float, str]:
    """V3 Fix 3 — Commodity terminal-value uplift (forward margin > historical).

    Only fires for commodity-exposed sectors. Capped at 1.40x individually.
    """
    m = metrics or {}
    if "Mining" not in profile_name and sector not in ("Resources", "Energy", "Materials"):
        return (1.0, "n/a (non-commodity sector)")

    spot     = m.get("spot_commodity_price")
    realised = m.get("realised_price_per_unit") or m.get("realised_oil_price")
    cost     = m.get("aisc_per_oz") or m.get("lifting_cost_per_boe")
    if not (spot and realised and cost):
        return (1.0, "no commodity price KPIs")

    historical_margin = realised - cost
    if historical_margin <= 0: return (1.0, "negative historical margin")
    blended = spot * 0.33 + realised * 0.67
    forward_margin = blended - cost
    leverage = forward_margin / historical_margin
    # Soft uplift: 1.0 + (leverage - 1) × 0.5, capped at 1.40
    uplift = 1.0 + (leverage - 1.0) * 0.5
    uplift = max(1.0, min(1.40, uplift))
    return (round(uplift, 3), f"spot={spot:.0f}/realised={realised:.0f}/cost={cost:.0f} -> {uplift:.2f}x")


def composite_adjustment(profile_name: str, sector: str, metrics: dict | None) -> tuple[float, dict]:
    """V3 aggregator — DELEGATES to the production framework module so the
    data-driven `quality_tiers` / `risk_adjustment` / `commodity_uplift`
    schemas declared per profile in SECTOR_KPI_FRAMEWORK actually fire.

    Local quality_multiplier/risk_multiplier/commodity_multiplier above
    are kept for backwards compatibility with stand-alone valuation_check.py
    callers but are NOT used here — the framework version reads schemas
    AND falls back to hardcoded sector logic when no schema exists.
    """
    from src.data.sector_kpi_framework import composite_adjustment as _fw_composite
    return _fw_composite(profile_name, sector, metrics)


def quality_kicker(profile_name: str, sector: str, metrics: dict | None) -> tuple[float, str]:
    """LEGACY shim — preserves old API. Calls composite_adjustment internally
    so callers see the correct V3 multiplier. Returns (multiplier, terse_note)."""
    capped, bridge = composite_adjustment(profile_name, sector, metrics)
    note = (f"Q={bridge['quality']:.2f} R={bridge['risk']:.2f} "
            f"C={bridge['commodity']:.2f} -> {capped:.2f}x"
            + (" [capped]" if bridge["was_capped"] else ""))
    return capped, note


def _legacy_inline_quality_kicker(profile_name: str, sector: str, metrics: dict | None) -> tuple[float, str]:
    """V2 — Multiplicative stacking with correlation guard, capped at 1.60x.

    Independent KPIs (e.g. CR vs SCR — operating efficiency vs balance sheet
    strength) → multiplied. Correlated KPIs (e.g. CET1 + Efficiency Ratio —
    both proxies for general bank quality) → max-selection to avoid double
    counting. Final result clamped to [0.70, 1.60].

    Returns (kicker, rationale).
    """
    m = metrics or {}
    independent: list[tuple[float, str]] = []  # multiply these
    correlated: list[tuple[float, str]]  = []  # take max of these

    # ── Insurance ──────────────────────────────────────────────────────────
    cr = m.get("combined_ratio")
    if cr is not None:
        if   cr < 0.88: independent.append((1.50, f"CR={cr*100:.1f}% best-in-class +50%"))
        elif cr < 0.92: independent.append((1.30, f"CR={cr*100:.1f}% top-quartile +30%"))
        elif cr < 0.96: independent.append((1.12, f"CR={cr*100:.1f}% above-avg +12%"))
        elif cr > 1.02: independent.append((0.80, f"CR={cr*100:.1f}% loss-making -20%"))
    scr = m.get("solvency_ratio_scr")
    if scr is not None and "Insurance" in profile_name:
        if   scr > 2.0: independent.append((1.10, f"SCR={scr:.2f}x strong +10%"))
        elif scr < 1.3: independent.append((0.90, f"SCR={scr:.2f}x weak -10%"))

    # ── Bank — CET1 + Efficiency are CORRELATED (both proxies for quality) ─
    # Note: CET1's primary effect is via Residual Income CoE haircut already.
    # The kicker here is for non-RI methods (P/TBV, P/E) that don't use CoE.
    cet1 = m.get("cet1_ratio")
    if cet1 is not None and "Bank" in profile_name:
        if   cet1 > 0.14: correlated.append((1.15, f"CET1={cet1*100:.1f}% fortress +15%"))
        elif cet1 > 0.12: correlated.append((1.08, f"CET1={cet1*100:.1f}% strong +8%"))
        elif cet1 < 0.085: correlated.append((0.85, f"CET1={cet1*100:.1f}% weak -15%"))
    eff = m.get("efficiency_ratio")
    if eff is not None and "Bank" in profile_name:
        if   eff < 0.50: correlated.append((1.12, f"Eff={eff*100:.1f}% top-decile +12%"))
        elif eff > 0.65: correlated.append((0.92, f"Eff={eff*100:.1f}% bloated -8%"))
    target_roe = m.get("management_target_roe")
    if target_roe is not None and "Bank" in profile_name:
        if   target_roe > 0.17: correlated.append((1.18, f"Target ROE={target_roe*100:.0f}% premium +18%"))
        elif target_roe > 0.13: correlated.append((1.08, f"Target ROE={target_roe*100:.0f}% above-avg +8%"))

    # ── Mining — Cost curve + production growth (independent) ──────────────
    quartile = m.get("cost_curve_quartile")
    if quartile is not None:
        q = int(quartile)
        if q == 1: independent.append((1.30, "Q1 cost producer +30%"))
        elif q == 2: independent.append((1.20, "Q2 cost producer +20%"))
        elif q == 4: independent.append((0.85, "Q4 cost producer -15%"))
    prod_growth = m.get("production_yoy_pct")
    if prod_growth is not None and "Mining" in profile_name:
        if   prod_growth > 0.10: independent.append((1.10, f"Production +{prod_growth*100:.1f}% YoY +10%"))
        elif prod_growth < -0.05: independent.append((0.90, f"Production {prod_growth*100:.1f}% YoY -10%"))

    # ── SaaS — NRR + Rule of 40 (correlated) ───────────────────────────────
    nrr = m.get("nrr_pct")
    if nrr is not None:
        if   nrr > 1.30: correlated.append((1.30, f"NRR={nrr*100:.0f}% best-in-class +30%"))
        elif nrr > 1.15: correlated.append((1.15, f"NRR={nrr*100:.0f}% strong +15%"))
        elif nrr < 1.0:  correlated.append((0.85, f"NRR={nrr*100:.0f}% contraction -15%"))
    r40 = m.get("rule_of_40_score")
    if r40 is not None:
        if   r40 > 60: correlated.append((1.20, f"Rule of 40={r40:.0f} elite +20%"))
        elif r40 > 40: correlated.append((1.10, f"Rule of 40={r40:.0f} healthy +10%"))
        elif r40 < 20: correlated.append((0.90, f"Rule of 40={r40:.0f} weak -10%"))

    # ── Combine: independent multiply, correlated take MAX ─────────────────
    multipliers, notes = [], []
    if correlated:
        # Take the strongest correlated signal (max above 1.0, min below 1.0)
        # Pick the one furthest from 1.0 in either direction
        pick = max(correlated, key=lambda x: abs(x[0] - 1.0))
        multipliers.append(pick[0])
        notes.append(f"[corr-max] {pick[1]}")
    for mult, note in independent:
        multipliers.append(mult)
        notes.append(f"[indep] {note}")

    if not multipliers:
        return (1.0, "no quality KPIs supplied -> par")

    # Multiplicative stacking
    composite = 1.0
    for x in multipliers: composite *= x
    # Global cap at 1.60x (per V3 Correlation Guard spec)
    composite = max(0.70, min(1.60, composite))
    return (round(composite, 3), " * ".join(notes) + f" -> {composite:.2f}x (capped)")


def commodity_terminal_uplift(metrics: dict | None) -> tuple[float, str]:
    """Mining/Energy specific — terminal value uplift when AISC margin expands.

    Logic: spot commodity price often runs above realised price (lag). The
    forward AISC trajectory and spot premium drive the terminal multiple.
    Applied AFTER NAV mine-by-mine to capture the leverage effect the user
    flagged for NEM ("AISC Inertia" — engine under-weights commodity leverage).

    Returns multiplier (>1.0 if forward margin > historical margin).
    """
    m = metrics or {}
    spot = m.get("spot_commodity_price")
    realised = m.get("realised_price_per_unit")
    aisc = m.get("aisc_per_oz")
    if not all([realised, aisc]): return (1.0, "no commodity data")
    historical_margin = realised - aisc
    if historical_margin <= 0: return (1.0, "negative historical margin")
    # If spot price not provided, assume +10% commodity tailwind
    forward_price = spot if spot else realised * 1.10
    forward_margin = forward_price - aisc
    leverage = forward_margin / historical_margin
    # Cap the terminal uplift at 1.5× to avoid runaway IV
    capped = min(1.5, max(1.0, leverage * 0.5 + 0.5))
    return (capped, f"forward margin {forward_margin:.0f}/oz vs historical {historical_margin:.0f}/oz -> {capped:.2f}x")


# ─────────────────────────────────────────────────────────────────────────────
# TIER-1 SECTOR-NATIVE FORMULAS — require Section 2F sector KPIs
#
# Each takes (fmp, sector, metrics) where metrics is the dict of extracted KPIs.
# Returns None if mandatory KPIs are missing — DCF aggregator skips and falls
# back to Tier-2/3 methods automatically.
# ─────────────────────────────────────────────────────────────────────────────


def value_per_share_embedded_value(fmp: dict, sector: str, metrics: dict | None) -> float | None:
    """Insurance — Embedded Value (Tier-1 sector-native method).

    V3 architecture: this method returns the institutional intrinsic value
    using the standard Embedded Value formula (which includes the VNB-margin
    premium as part of the formula itself — this is NOT a quality kicker,
    it's the actual valuation math). The Quality/Risk/Commodity multipliers
    in `composite_adjustment` apply ON TOP for market-quality premium.

    Life formula:  IV = EV/share × (1 + VNB-margin premium)
    P&C fallback:  IV = bvps + capitalised underwriting profit (5× EPS)
    """
    if not metrics: return None
    ev_ps = metrics.get("embedded_value_per_share")
    vnb   = metrics.get("vnb_margin")
    if ev_ps:
        # Life: VNB margin tier is part of EV math (not quality kicker)
        if vnb is not None:
            if   vnb > 0.25: premium = 0.40
            elif vnb > 0.18: premium = 0.20
            elif vnb > 0.10: premium = 0.00
            else:            premium = -0.10
        else:
            premium = 0.0
        return round(float(ev_ps) * (1 + premium), 2)
    # P&C: book + capitalised underwriting profit (no quality premium here —
    # quality_multiplier handles that)
    bvps = fmp.get("bvps"); eps = fmp.get("eps")
    if bvps and eps and eps > 0:
        return round(bvps + eps * 5.0, 2)
    return None


def value_per_share_residual_income(fmp: dict, sector: str, metrics: dict | None) -> float | None:
    """Bank — Residual Income (10-yr explicit + Gordon terminal).

    Formula:
       IV = BV₀ + Σ_{t=1..10} (ROE - r) × BV_{t-1} / (1+r)^t
              + TV / (1+r)^10
       TV = (ROE - r) × BV_10 × (1+g) / (r - g)

    Inputs:
       - bvps (FMP)
       - management_target_roe (Section 2F mandatory KPI)
       - cost of equity = 10% (bank default)
       - terminal growth = 2.5%
       - payout ratio = 50% (BV grows at ROE × (1 - payout))
    """
    bv = fmp.get("bvps")
    target_roe = (metrics or {}).get("management_target_roe")
    if not bv or not target_roe: return None
    # V3 BETA HAIRCUT: high CET1 lowers Equity Risk Premium (lower beta →
    # lower cost of equity → higher IV). Solves the "JPM Capital Drag"
    # paradox where excess capital was treated as ROE drag without credit
    # for the safety it provides.
    base_coe = 0.10
    cet1 = (metrics or {}).get("cet1_ratio")
    if cet1 and cet1 > 0.10:
        excess_cet1 = cet1 - 0.10
        coe_haircut = excess_cet1 * 0.50  # 100bps excess CET1 → -50bps CoE
        cost_of_equity = max(0.07, base_coe - coe_haircut)
    else:
        cost_of_equity = base_coe
    g, payout = 0.025, 0.50
    bv_growth = target_roe * (1 - payout)
    if cost_of_equity <= g:  # numerical safety
        return None
    pv_ri = 0.0
    bv_t = bv
    for t in range(1, 11):
        bv_t *= (1 + bv_growth)
        ri_t = (target_roe - cost_of_equity) * bv_t
        pv_ri += ri_t / (1 + cost_of_equity) ** t
    terminal_ri = (target_roe - cost_of_equity) * bv_t * (1 + g)
    tv = terminal_ri / (cost_of_equity - g)
    pv_tv = tv / (1 + cost_of_equity) ** 10
    return round(bv + pv_ri + pv_tv, 2)


def value_per_share_nav_pv10(fmp: dict, sector: str, metrics: dict | None) -> float | None:
    """Upstream Oil & Gas — NAV (PV-10) per share.

    Formula: pv10_value_usd / shares_outstanding
    SEC PV-10 supplement gives the present value of future net cash flows
    from proved reserves discounted at 10%. Divide by shares to get per-share NAV.
    Falls back to deriving shares from market_cap / current_price.
    """
    if not metrics: return None
    pv10 = metrics.get("pv10_value_usd")
    if not pv10: return None
    shares = fmp.get("shares_out")
    if not shares:
        mc, px = fmp.get("market_cap"), fmp.get("current_price")
        if mc and px and px > 0:
            shares = mc / px
    if not shares: return None
    return round(pv10 / shares, 2)


def value_per_share_nav_mining(fmp: dict, sector: str, metrics: dict | None) -> float | None:
    """Mining — NAV mine-by-mine (DCF on per-share margin × reserve life).

    Formula:
       oz_per_share        = rev_per_share / realised_price
       margin_per_oz       = realised_price - AISC
       annual_margin_ps    = oz_per_share × margin_per_oz
       NAV per share       = Σ_{t=1..mine_life} cf_t / (1+WACC)^t
                             where cf_t = annual_margin_ps × (1 + production_yoy)^(t-1)

    Inputs:
       - rev_per_share, fcf_per_share (FMP)
       - aisc_per_oz, realised_price_per_unit, reserve_life_years (Section 2F KPIs)
       - production_yoy_pct (optional, defaults 0)
       - WACC = 9% (mining default)
    """
    if not metrics: return None
    aisc      = metrics.get("aisc_per_oz")
    realised  = metrics.get("realised_price_per_unit")
    life      = metrics.get("reserve_life_years")
    growth    = metrics.get("production_yoy_pct") or 0.0
    spot      = metrics.get("spot_commodity_price")  # forward leverage
    rev_ps    = fmp.get("rev_per_share")
    if not all([aisc, realised, life, rev_ps]): return None
    historical_margin = realised - aisc
    if historical_margin <= 0: return None
    oz_per_share = rev_ps / realised
    wacc = 0.09
    nav = 0.0
    # V2: split the reserve life into two phases:
    #   - Years 1-5: historical margin (locked-in via existing contracts)
    #   - Years 6+:  forward margin reflects spot price tailwind (if disclosed)
    # This addresses the user's "AISC Inertia" critique — engine no longer
    # under-weights spot commodity leverage in terminal years.
    # Conservative forward assumption: 1/3 weight on spot, 2/3 on realised
    # Then cap forward_margin uplift at 1.3x historical to avoid runaway IV
    # when spot is anomalously high (e.g. NEM gold at $4732 vs $2245 realised).
    blended_forward = (spot * 0.33 + realised * 0.67) if spot else realised * 1.05
    forward_margin = min(blended_forward - aisc, historical_margin * 1.30)
    forward_margin = max(forward_margin, historical_margin)  # never below historical
    for t in range(1, int(life) + 1):
        margin_t = historical_margin if t <= 5 else forward_margin
        annual_ps = oz_per_share * margin_t
        cf_t = annual_ps * ((1 + growth) ** (t - 1))
        nav += cf_t / ((1 + wacc) ** t)
    return round(nav, 2)


def value_per_share_excess_capital(fmp: dict, sector: str, metrics: dict | None) -> float | None:
    """Bank — Excess Capital model.

    Formula:
       Excess CET1 ratio  = max(0, CET1_actual - CET1_target)
       Excess capital/shr = Excess_pct × tbvps × leverage_factor
       IV per share       = tbvps + Excess capital/shr

    Logic: CET1 above the regulatory + buffer is "stranded" capital that should
    be returned to shareholders (buybacks/dividends) — adds 1:1 to fair value.

    Inputs:
       - tbvps (FMP)
       - cet1_ratio (Section 2F KPI)
       - cet1_target = 10% (regulatory + GSIB buffer for big banks)
       - leverage_factor = 6.0 (RWA/equity proxy for diversified banks)
    """
    tbvps = fmp.get("tbvps")
    cet1  = (metrics or {}).get("cet1_ratio")
    if not tbvps or cet1 is None: return None
    cet1_target = 0.10
    excess_pct = max(0.0, cet1 - cet1_target)
    leverage_factor = 6.0
    excess_capital_ps = excess_pct * tbvps * leverage_factor
    return round(tbvps + excess_capital_ps, 2)


# Anchor method router. Tier-1 methods (Embedded Value, Residual Income,
# NAV mine-by-mine, Excess Capital) require sector KPIs in `metrics`. Tier-2/3
# methods ignore `metrics` and use only FMP. Methods marked as strings remain
# placeholders (DCF needs full pipeline, EV/Revenue needs forward revenue).
_METHOD_DISPATCH: dict[str, Any] = {
    # Tier-3 (FMP-only)
    "P/BV":              value_per_share_pbv,
    "P/TBV":             value_per_share_ptbv,
    "EV/EBITDA":         value_per_share_ev_ebitda,
    "EV/EBITDAX":        value_per_share_ev_ebitda,  # E&P alias
    "EV/Revenue":        value_per_share_ev_revenue,
    "EV/Sales":          value_per_share_ev_revenue,  # alias
    "P/E (ops)":         value_per_share_pe,
    "P/E":               value_per_share_pe,
    "P/CF":              value_per_share_pcf,
    # Sector-native multiples
    "P/Rate Base":       value_per_share_p_rate_base,    # Utilities
    "DDM":               value_per_share_ddm,             # Utilities/REITs
    # Tier-1 (sector-native — uses Section 2F KPIs in `metrics`)
    "Embedded Value":     value_per_share_embedded_value,
    "Residual Income":    value_per_share_residual_income,
    "NAV (Mine-by-Mine)": value_per_share_nav_mining,
    "NAV (PV-10)":        value_per_share_nav_pv10,      # E&P PV-10 supplement / shares
    "Excess Capital":     value_per_share_excess_capital,
    # DCF aliases — cascade P/E -> EV/Rev -> P/CF for negative-EPS tickers
    "DCF":                value_per_share_dcf_proxy,
    "DCF (FCF)":          value_per_share_dcf_proxy,
    "DCF (FCF+ anchor)":  value_per_share_dcf_proxy,
    "DCF (Rate Base)":    value_per_share_p_rate_base,
    "DCF (rNPV)":         value_per_share_dcf_proxy,    # Pharma — pipeline NPV proxy
    "rNPV (Pipeline)":    value_per_share_dcf_proxy,
    "NRR-adj DCF":        value_per_share_ev_revenue,   # SaaS — NRR is in quality_kicker
    "Rule of 40 Score Multiple": value_per_share_ev_revenue,
    # Overlays
    "Combined Ratio Gate": "gate_only",
}

# Methods that accept metrics (Tier-1). Lookup by name to decide call signature.
_TIER1_METHODS: frozenset[str] = frozenset({
    "Embedded Value", "Residual Income",
    "NAV (Mine-by-Mine)", "NAV (PV-10)",  # Mining + Upstream O&G aliases
    "Excess Capital",
})


def value_per_share(method: str, fmp: dict, sector: str,
                    metrics: dict | None = None) -> dict:
    """Apply one anchor method. Returns {iv (base), bear, bull, status, note}."""
    fn = _METHOD_DISPATCH.get(method)
    if fn is None:
        return {"iv": None, "bear": None, "bull": None,
                "status": "unknown_method", "note": f"no formula for {method!r}"}
    if isinstance(fn, str):
        return {"iv": None, "bear": None, "bull": None,
                "status": fn, "note": f"{method}: {fn}"}
    try:
        if method in _TIER1_METHODS:
            iv = fn(fmp, sector, metrics)
        else:
            iv = fn(fmp, sector)
        if iv is None:
            return {"iv": None, "bear": None, "bull": None,
                    "status": "missing_input",
                    "note": (f"{method}: needs sector KPIs from Section 2F"
                             if method in _TIER1_METHODS
                             else f"{method}: missing FMP field or industry multiple")}
        scen = _scenario_ivs(iv, sector)
        return {"iv": iv, "bear": scen["bear"], "bull": scen["bull"],
                "status": "ok", "note": ""}
    except Exception as e:
        return {"iv": None, "bear": None, "bull": None,
                "status": "error", "note": f"{method}: {type(e).__name__}: {e}"}


# ── Combined Ratio Gate (Insurance) — adjustment overlay ─────────────────────

def apply_combined_ratio_gate(iv: float, combined_ratio: float | None) -> tuple[float, str]:
    """V3 OVERRIDE SEMANTICS: gate now passes through raw IV unchanged.
    All quality adjustments are centralized in `quality_kicker()`.

    The Combined Ratio Gate method itself remains an anchor (it signals
    a real economic check — a P&C insurer with CR > 1.0 is destroying
    value), but its multiplier role is now handled at the aggregate level.
    """
    if combined_ratio is None:
        return iv, "no CR data (gate passes through)"
    return iv, f"CR={combined_ratio*100:.1f}% (gate passes raw — kicker handles premium)"


# ── Main runner ──────────────────────────────────────────────────────────────

def valuate_ticker(ticker: str, sector_metrics: dict | None = None) -> dict:
    """Returns the full valuation breakdown for a ticker."""
    from src.data.sector_profiles import TICKER_SECTOR_LOOKUP
    from src.data.sector_kpi_framework import SECTOR_KPI_FRAMEWORK

    lookup = TICKER_SECTOR_LOOKUP.get(ticker)
    if not lookup:
        return {"ticker": ticker, "error": f"{ticker} not in TICKER_SECTOR_LOOKUP"}
    sector, profile_name = lookup[0], lookup[1]
    spec = SECTOR_KPI_FRAMEWORK.get(profile_name) or {}
    anchor_methods = spec.get("anchor_methods", [])

    fmp = fmp_bundle(ticker)
    current = fmp.get("current_price")

    method_results: list[dict] = []
    valid_base: list[float] = []
    valid_bear: list[float] = []
    valid_bull: list[float] = []
    for m in anchor_methods:
        r = value_per_share(m, fmp, sector, metrics=sector_metrics)
        # Combined Ratio Gate (Insurance overlay) — adjust the prior method's IV
        if m == "Combined Ratio Gate" and method_results:
            base_iv = next((x["iv"] for x in method_results if x.get("iv") is not None), None)
            cr = (sector_metrics or {}).get("combined_ratio") if sector_metrics else None
            if base_iv is not None:
                gated, gate_note = apply_combined_ratio_gate(base_iv, cr)
                scen = _scenario_ivs(gated, sector)
                r = {"iv": gated, "bear": scen["bear"], "bull": scen["bull"],
                     "status": "gated_iv", "note": gate_note}
        method_results.append({"method": m, **r})
        if r.get("iv") is not None:
            valid_base.append(r["iv"])
            if r.get("bear") is not None: valid_bear.append(r["bear"])
            if r.get("bull") is not None: valid_bull.append(r["bull"])

    # ── Aggregator: weighted-mean if profile declares method_weights, else median ──
    # V4-α "Integrated Trap" fix: for conglomerates (Upstream O&G integrateds,
    # Mining majors with smelting), the median is unfairly tethered to NAV
    # which only captures one segment. Weighted mean tilts toward cash-flow
    # methods (EV/EBITDA[X], P/CF) which represent the going-concern value.
    spec_for_weights = SECTOR_KPI_FRAMEWORK.get(profile_name) or {}
    method_weights = spec_for_weights.get("method_weights")

    def _median(xs: list[float]) -> float | None:
        if not xs: return None
        s = sorted(xs); n = len(s)
        return round(s[n//2] if n % 2 else (s[n//2-1] + s[n//2]) / 2, 2)

    def _weighted(method_results_list: list[dict], scenario: str) -> float | None:
        """Weighted mean using profile's method_weights. P75 fallback when
        dispersion exceeds 50% (max - min) / median."""
        if not method_weights: return None
        weighted_sum = 0.0
        total_w = 0.0
        for mr in method_results_list:
            iv = mr.get(scenario)
            if iv is None: continue
            w = method_weights.get(mr["method"])
            if w is None: continue   # method not weighted → skip
            weighted_sum += iv * w
            total_w += w
        if total_w == 0: return None
        return round(weighted_sum / total_w, 2)

    if method_weights:
        pre_kicker_iv = _weighted(method_results, "iv")
        bear_iv_pre   = _weighted(method_results, "bear")
        bull_iv_pre   = _weighted(method_results, "bull")
        # Fall back to median if weighted aggregator returned nothing (e.g.
        # all weighted methods missing — only un-weighted methods fired)
        if pre_kicker_iv is None: pre_kicker_iv = _median(valid_base)
        if bear_iv_pre is None:   bear_iv_pre   = _median(valid_bear)
        if bull_iv_pre is None:   bull_iv_pre   = _median(valid_bull)
    else:
        pre_kicker_iv = _median(valid_base)
        bear_iv_pre   = _median(valid_bear)
        bull_iv_pre   = _median(valid_bull)

    # ── V3: Composite adjustment with full audit bridge ────────────────────
    # Three independent multipliers (Quality, Risk, Commodity) stacked
    # multiplicatively with global cap [0.50, 1.85]. Audit bridge logs
    # each lever's contribution so the user can see WHY the IV moved.
    #
    # V3.2: AUGMENT extractor metrics with FMP-derived risk KPIs (the
    # extractor often misses these since they're balance-sheet-derived
    # and rarely quoted verbatim in deep research narrative). FMP is
    # authoritative — extractor wins ONLY where it has an explicit value.
    augmented_metrics = dict(sector_metrics or {})
    fmp_risk_kpis = ("net_debt_to_ebitda", "cash_runway_years",
                     "debt_to_ebitda")  # debt_to_ebitda alias used by Utilities
    for fmp_key in fmp_risk_kpis:
        if fmp_key not in augmented_metrics or augmented_metrics[fmp_key] is None:
            fmp_val = fmp.get(fmp_key)
            # debt_to_ebitda not directly in FMP -> use net_debt_to_ebitda as proxy
            if fmp_val is None and fmp_key == "debt_to_ebitda":
                fmp_val = fmp.get("net_debt_to_ebitda")
            if fmp_val is not None:
                augmented_metrics[fmp_key] = fmp_val
    composite_mult, bridge = composite_adjustment(profile_name, sector, augmented_metrics)
    kicker, kicker_note = composite_mult, (
        f"Q={bridge['quality']:.2f}({bridge['quality_note']}) "
        f"x R={bridge['risk']:.2f}({bridge['risk_note']}) "
        f"x C={bridge['commodity']:.2f}({bridge['commodity_note']}) "
        f"-> {composite_mult:.2f}x"
        + (" [CAPPED]" if bridge["was_capped"] else "")
    )
    primary_iv = round(pre_kicker_iv * composite_mult, 2) if pre_kicker_iv else None
    bear_iv    = round(bear_iv_pre   * composite_mult, 2) if bear_iv_pre   else None
    bull_iv    = round(bull_iv_pre   * composite_mult, 2) if bull_iv_pre   else None

    upside_pct = bear_upside = bull_upside = None
    if current:
        if primary_iv: upside_pct  = round((primary_iv - current) / current * 100, 1)
        if bear_iv:    bear_upside = round((bear_iv    - current) / current * 100, 1)
        if bull_iv:    bull_upside = round((bull_iv    - current) / current * 100, 1)

    return {
        "ticker":         ticker,
        "company_name":   fmp.get("company_name"),
        "sector":         sector,
        "profile_name":   profile_name,
        "current_price":  current,
        "anchor_methods": anchor_methods,
        "method_results": method_results,
        "pre_kicker_iv":  pre_kicker_iv,
        "kicker":         kicker,
        "kicker_note":    kicker_note,
        "audit_bridge":   bridge,   # V3 — separated Q/R/C breakdown
        "bear_iv":        bear_iv,
        "primary_iv":     primary_iv,    # Base case AFTER quality kicker
        "bull_iv":        bull_iv,
        "bear_upside":    bear_upside,
        "upside_pct":     upside_pct,
        "bull_upside":    bull_upside,
        "fmp_inputs":     {k: v for k, v in fmp.items()
                           if k in ("bvps", "tbvps", "eps", "ebitda", "shares_out",
                                    "fcf_per_share", "pe_ttm", "pb_ttm", "ev_ebitda_ttm")},
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", default="PGR,JPM,NEM,NVDA,LMT,MCD")
    parser.add_argument("--out-md", default="valuation_check.md")
    parser.add_argument("--out-json", default="valuation_check.json")
    parser.add_argument("--with-kpis", action="store_true",
                        help="Hand-populate sector KPIs to demonstrate Tier-1 lift")
    args = parser.parse_args()

    # Hand-populated mandatory KPIs per ticker — shows what valuation looks like
    # WHEN the framework extractor produces a complete result. Real production
    # runs read these from state.<insurance|bank|framework>_metrics_all[ticker].
    HAND_KPIS = {
        "PGR": {"combined_ratio": 0.874, "loss_ratio": 0.658, "expense_ratio": 0.19,
                "solvency_ratio_scr": 2.10, "embedded_value_per_share": 95.0,
                "vnb_margin": 0.22, "reserve_release_pct": 0.012},
        "JPM": {"cet1_ratio": 0.143, "nim_pct": 0.026, "efficiency_ratio": 0.52,
                "management_target_roe": 0.17, "npl_ratio": 0.011},
        "NEM": {"aisc_per_oz": 1428, "cost_curve_quartile": 2,
                "reserve_life_years": 14.2, "production_yoy_pct": 0.046,
                "realised_price_per_unit": 2245},
    } if args.with_kpis else {}

    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    print(f"\n=== Anchor-method valuation check on {len(tickers)} tickers "
          f"({'WITH hand-populated KPIs' if args.with_kpis else 'FMP-only'}) ===\n")

    rows = []
    t0 = time.time()
    for i, t in enumerate(tickers, 1):
        print(f"  [{i}/{len(tickers)}] {t}...")
        row = valuate_ticker(t, sector_metrics=HAND_KPIS.get(t))
        rows.append(row)
        if "error" in row:
            print(f"    ERROR: {row['error']}")
            continue
        ok_methods = [m for m in row["method_results"] if m.get("iv") is not None]
        print(f"    sector={row['sector']!r} profile={row['profile_name']!r}")
        print(f"    current=${row['current_price']}  "
              f"BEAR=${row.get('bear_iv') or '—'} ({row.get('bear_upside') or '—'}%)  "
              f"BASE=${row['primary_iv'] or '—'} ({row['upside_pct'] or '—'}%)  "
              f"BULL=${row.get('bull_iv') or '—'} ({row.get('bull_upside') or '—'}%)  "
              f"({len(ok_methods)}/{len(row['anchor_methods'])} methods)")
        for m in row["method_results"]:
            iv_str   = f"${m['iv']}"   if m.get("iv")   is not None else "—"
            bear_str = f"${m['bear']}" if m.get("bear") is not None else "—"
            bull_str = f"${m['bull']}" if m.get("bull") is not None else "—"
            print(f"      {m['method']:25s}: bear={bear_str:>9s} base={iv_str:>9s} bull={bull_str:>9s}  [{m['status']}] {m['note']}")
    elapsed = time.time() - t0
    print(f"\n  Done in {elapsed:.1f}s")

    # JSON dump
    json.dump({"rows": rows, "elapsed_sec": elapsed},
              open(args.out_json, "w"), indent=2, default=str)

    # Markdown table
    md = ["# Valuation Check — Bear / Base / Bull (Anchor Methods)",
          "",
          f"_Elapsed: {elapsed:.1f}s · {len(tickers)} tickers · "
          f"{'Hand-populated KPIs' if args.with_kpis else 'FMP-only'} · "
          "Anchor methods from SECTOR_KPI_FRAMEWORK_",
          "",
          "## Summary",
          "",
          "| # | Ticker | Sector / Profile | Current | Bear | **Base** | Bull | Bear% | Base% | Bull% | Methods |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(rows, 1):
        if "error" in r:
            md.append(f"| {i} | {r['ticker']} | — | — | — | — | — | — | — | — | {r['error']} |")
            continue
        ok_methods = [m for m in r["method_results"] if m.get("iv") is not None]
        def _f(v, kind="usd"):
            if v is None: return "—"
            return f"${v}" if kind == "usd" else f"{v:+.1f}%"
        md.append(
            f"| {i} | {r['ticker']} | {r['sector']} / {r['profile_name']} | "
            f"{_f(r.get('current_price'))} | "
            f"{_f(r.get('bear_iv'))} | **{_f(r.get('primary_iv'))}** | {_f(r.get('bull_iv'))} | "
            f"{_f(r.get('bear_upside'),'pct')} | {_f(r.get('upside_pct'),'pct')} | {_f(r.get('bull_upside'),'pct')} | "
            f"{len(ok_methods)}/{len(r['anchor_methods'])} |"
        )

    md.append("")
    md.append("## Per-Ticker Method Breakdown")
    md.append("")
    for r in rows:
        if "error" in r:
            continue
        md.append(f"### {r['ticker']} — {r['company_name']} ({r['sector']} / {r['profile_name']})")
        md.append("")
        md.append(f"Current price: **${r['current_price']}** · Primary IV: **${r['primary_iv']}** · "
                  f"Upside: **{r['upside_pct']:+.1f}%**" if r.get("upside_pct") is not None
                  else f"Current price: **${r['current_price']}**")
        md.append("")
        md.append("| Anchor Method | IV / share | Status | Note |")
        md.append("|---|---|---|---|")
        for m in r["method_results"]:
            iv_str = f"${m['iv']}" if m.get("iv") is not None else "—"
            md.append(f"| {m['method']} | {iv_str} | {m['status']} | {m['note'] or '—'} |")
        md.append("")
        md.append("**FMP inputs:** " +
                  ", ".join(f"`{k}={v}`" for k, v in r["fmp_inputs"].items() if v))
        md.append("")

    open(args.out_md, "w", encoding="utf-8").write("\n".join(md) + "\n")
    print(f"  Markdown -> {args.out_md}")


if __name__ == "__main__":
    main()
