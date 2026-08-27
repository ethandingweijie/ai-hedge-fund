"""
src/portfolio/what_if.py
=========================
P5 — what-if crisis simulator: model a crisis that has NOT happened yet.

The user assigns a category (e.g. "AI Capex Meltdown"), describes
industry/macro concerns, and optionally points at the closest historical
crisis as a reference anchor. The engine returns a sector-level scenario:
assumptions to watch across the quarterlies, most-affected vs hedged
sectors, per-holding impact vs the user's portfolio, and recommended
tools (SHORT / BUY / GOLD / CASH).

Division of labor (hard rule — the LLM never does arithmetic):
  • DETERMINISTIC (this module, pure Python, unit-tested):
      - product classification (known inverse-ETF map + holdings-notes
        parsing for the unknowns)
      - leveraged/inverse scenario returns with VOLATILITY TIME DECAY
        via a closed-form log-return approximation
      - reference-crisis anchors (event_library sector_performance as the
        skeleton; realized vol from the reference window when fetchable,
        vol-regime defaults otherwise)
      - the search-necessity heuristic
  • LLM (ONE deepseek-v4-flash structured call): qualitative scenario
    narrative — sector-impact adjustments around the anchors with
    rationale, assumptions to watch, most-affected/hedged sectors,
    holding-level commentary, recommendations, and whether the search
    evidence was actually used.

Cost basis: same as replay — qty × avg_cost weights.

Determinism: identical inputs (holdings, scenario, reference event,
injected dependencies) give byte-identical output; the only wall clock
is in the service layer that persists results.
"""
from __future__ import annotations

import json
import logging
import math
import re
from typing import Callable, Optional

from pydantic import BaseModel, Field, model_validator

from src.portfolio.event_library import EventSpec, get_event

logger = logging.getLogger(__name__)

# ── Scenario categories (user-facing dropdown; free-form concerns carry the
#    detail). Kept in the module so backend validation + frontend meta share
#    one source of truth. ───────────────────────────────────────────────────

CATEGORIES: tuple[str, ...] = (
    "AI Capex Meltdown",
    "US Bond Destabilisation",
    "US-China Geopolitics",
    "Credit / Liquidity Crisis",
    "Inflation Re-acceleration",
    "Pandemic / Exogenous Shock",
    "Custom",
)

WHAT_IF_MODEL_NAME = "deepseek-v4-flash"   # override: WHAT_IF_MODEL env
_TRADING_DAYS_PER_YEAR = 252

# Bumped when prompt/model/anchor logic changes in a way that makes cached
# what-if payloads stale — baked into the service's scenario_hash.
# v2: lenient field-alias parsing + product-only search scoping (2026-08-26).
# v3: synonym-normalizing before-validators (2026-08-26).
# v4: structural coercion — dict-keyed sector_impacts, swapped rec fields
#     (2026-08-26).
# v5: defaults for omitted fields (confidence/rationale/lists) + entry-level
#     drop of rows missing their load-bearing key (2026-08-26).
# v6: assumptions gain linked_sector + if_true_shift_pp for deterministic
#     sensitivity recomputation; shared scenario-memory publish (2026-08-26).
# v7: product map corrected by the user (MUD = −1× Micron/MU, CORD = −2×
#     CoreWeave/CRWV) + deterministic reconciliation so hedged_sectors can
#     never overlap or contradict most_affected_sectors (2026-08-26).
# v8: region-aware anchoring — an HK or SG holding resolves to its OWN
#     market's sector basket, then that market's broad index, and only then
#     to SPY. Previously every holding inherited a US sector ETF return,
#     which is badly wrong when the markets diverge: through the 2021-22
#     China crackdown the Hang Seng fell 52.8% while the S&P fell 1.6%
#     (2026-08-27).
SCENARIO_VERSION = 8

# Annualized vol (%) by volatility_regime label — fallback when realized vol
# cannot be computed from the reference window. Bands match macro_regime.py's
# VIX vocabulary (low < ~14, medium ~14-20, high ~20-30, extreme > 30).
_VOL_REGIME_DEFAULTS_PCT = {"low": 13.0, "medium": 20.0, "high": 32.0, "extreme": 55.0}

DEFAULT_HORIZON_DAYS = 90   # one quarter — the "watch across the quarterlies" unit


# ── Inverse / short product classification ──────────────────────────────────
#
# The user's portfolio can contain short/inverse products (PSQ, MUD, CORD…).
# confidence vocabulary:
#   "confirmed"  — curated, verified (ProShares family etc.)
#   "assumed"    — curated best guess; surfaced as an explicit assumption
#   "notes"      — parsed from the holding's user-written notes field
#   None/unknown — not classified → forces a search recommendation and a
#                  loud warning ("add a note: inverse/bear/short <TICKER> [2x]")

INVERSE_PRODUCT_MAP: dict[str, dict] = {
    "PSQ": {
        "name": "ProShares Short QQQ",
        "underlying": "QQQ",
        "leverage": -1.0,          # daily-rebalanced −1× Nasdaq-100
        "confidence": "confirmed",
    },
    # Single-name inverse ETFs trade under obscure tickers that FMP's
    # stable endpoints do not cover — anchors come from the underlying's
    # GICS sector instead. Identities confirmed by the user (2026-08-26);
    # MUD was previously mis-guessed as an inverse MSFT product.
    "MUD": {
        "name": "Inverse Micron (MU) daily ETF",
        "underlying": "MU",
        "leverage": -1.0,
        "confidence": "confirmed",
    },
    "CORD": {
        "name": "2x inverse CoreWeave (CRWV) daily ETF",
        "underlying": "CRWV",
        "leverage": -2.0,
        "confidence": "confirmed",
    },
}

# Tickers we deliberately do NOT classify: nothing is known. An entry here
# documents the gap; classify_product returns unknown for these. (Empty since
# 2026-08-26 — MUD/CORD were confirmed by the user and moved into the map.)
UNCLASSIFIED_PRODUCTS: frozenset[str] = frozenset()

# Sector of single-name underlyings for inverse single-stock products.
_SINGLE_NAME_SECTOR: dict[str, str] = {
    "MSFT": "Technology", "AAPL": "Technology", "NVDA": "Technology",
    "AMD": "Technology", "SMCI": "Technology", "AVGO": "Technology",
    "MU": "Technology", "CRWV": "Technology",
    "META": "Communication Services", "GOOGL": "Communication Services",
    "TSLA": "Consumer Discretionary", "AMZN": "Consumer Discretionary",
    "COIN": "Financials", "MSTR": "Financials", "PLTR": "Technology",
}

_LEVERAGE_RE = re.compile(r"(\d(?:\.\d)?)\s*[x×]")
# (?!-) keeps "short-term" style notes from reading as a short declaration
_SHORT_WORDS_RE = re.compile(r"\b(inverse|bear|short)\b(?!-)", re.IGNORECASE)
_LONG_WORDS_RE = re.compile(r"\b(bull|long)\b(?!-)", re.IGNORECASE)
_UNDERLYING_RE = re.compile(r"\b([A-Z]{2,5})\b")
_NOTE_STOPWORDS = frozenset({
    "THE", "THIS", "IS", "AN", "AS", "OF", "FOR", "AND", "NOT", "WITH",
    "MY", "OUR", "BUY", "SELL", "HOLD", "ETF", "ETFS", "NOTE", "NOTES",
    "ADDED", "POSITION", "HEDGE", "HEDGING", "SPECULATIVE", "TRADE",
    # directional/issuer words are never the underlying
    "INVERSE", "BEAR", "SHORT", "LONG", "BULL", "DAILY", "LEVERAGED",
    "LEVER", "PROSHARES", "DIREXION", "GRANITESHARES", "AXS", "TUTTLE",
})


def classify_product(ticker: str, notes: Optional[str] = None) -> dict:
    """Classify a short/inverse product. Returns:
      {classified, ticker, name?, underlying?, leverage?, confidence?,
       needs_classification}
    Curated map first; then holdings-notes parsing ("inverse MSFT 2x",
    "short QQQ") for unknowns."""
    tkr = str(ticker or "").strip().upper()
    if tkr in INVERSE_PRODUCT_MAP:
        meta = INVERSE_PRODUCT_MAP[tkr]
        return {"classified": True, "ticker": tkr, "name": meta["name"],
                "underlying": meta["underlying"], "leverage": meta["leverage"],
                "confidence": meta["confidence"], "needs_classification": False}

    if notes:
        text = str(notes)
        short_hit = _SHORT_WORDS_RE.search(text)
        long_hit = _LONG_WORDS_RE.search(text)
        if short_hit or long_hit:
            lev_match = _LEVERAGE_RE.search(text.lower())
            magnitude = float(lev_match.group(1)) if lev_match else 1.0
            magnitude = min(max(magnitude, 0.5), 3.0)     # sanity band
            sign = -1.0 if short_hit else 1.0
            underlying = None
            for cand in _UNDERLYING_RE.findall(text.upper()):
                if cand != tkr and cand not in _NOTE_STOPWORDS:
                    underlying = cand
                    break
            if underlying:
                return {"classified": True, "ticker": tkr,
                        "name": f"(from notes) {sign:+.0f}×{magnitude:g} {underlying}",
                        "underlying": underlying, "leverage": sign * magnitude,
                        "confidence": "notes", "needs_classification": False}

    return {"classified": False, "ticker": tkr,
            "needs_classification": True,
            "hint": "add a holding note like 'inverse MSFT' or 'short QQQ 2x'"}


# ── Time-decay math (closed form — the LLM never recomputes this) ───────────
#
# A daily-rebalanced leveraged product returns k·r_t per day (simple
# returns). Compounding and expanding ln(1 + k·r) to second order:
#
#   log(product return over N days) ≈ k·L − k(k−1)/2 · N·σ_d²
#
# where L = ln(1 + R) is the underlying's log return over the window,
# k the daily leverage, σ_d the daily vol. Sanity checks:
#   k = +1 → drag term 0 (a plain long ETF tracks exactly)
#   k = −1 → extra −N·σ_d² drag (inverse ETFs decay even when right)
#   k = +2 → classic 2× decay −N·σ_d²

def leveraged_scenario_return_pct(underlying_return_pct: float,
                                  annual_vol_pct: float,
                                  days: int,
                                  leverage: float) -> dict:
    """Scenario return of a daily-rebalanced leveraged/inverse product.

    Returns {est_return_pct, decay_drag_pp, log_return, no_decay_return_pct}.
    decay_drag_pp is the simple-return percentage points lost to volatility
    decay (est − no-decay), ≤ 0 for |k| ≥ 1 in stressed windows."""
    r = float(underlying_return_pct) / 100.0
    r = max(r, -0.99)                       # −100% kills the log expansion
    days = max(int(days), 1)
    k = float(leverage)
    sigma_d = max(float(annual_vol_pct), 0.0) / 100.0 / math.sqrt(_TRADING_DAYS_PER_YEAR)

    log_l = math.log1p(r)
    drag = k * (k - 1.0) / 2.0 * days * sigma_d * sigma_d
    log_ret = k * log_l - drag
    est = max(math.expm1(log_ret), -0.9999)          # cannot lose > 100%
    no_decay = max(math.expm1(k * log_l), -0.9999)
    return {
        "est_return_pct": round(est * 100.0, 2),
        "no_decay_return_pct": round(no_decay * 100.0, 2),
        "decay_drag_pp": round((est - no_decay) * 100.0, 2),
        "log_return": round(log_ret, 6),
    }


# ── Sector mapping (pipeline sector strings → the 11 GICS SPDR sectors) ─────

_SECTOR_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Technology", ("tech", "software", "saas", "semi", "semiconductor",
                    "hyperscaler", "cyber", "cloud", "chip", "hardware",
                    "internet infra")),
    ("Communication Services", ("telecom", "media", "communication",
                                "advertising", "social", "internet",
                                "streaming", "gaming")),
    ("Consumer Discretionary", ("consumer disc", "consumer growth",
                                "retail", "apparel", "luxury", "autos",
                                "leisure", "hospitality", "restaurant",
                                "e-commerce", "ecommerce", "marketplace",
                                "travel")),
    ("Consumer Staples", ("staple", "food", "beverage", "household",
                          "tobacco")),
    ("Health Care", ("health", "pharma", "biotech", "lifesci", "medical",
                     "managed care", "insurance health", "drug")),
    ("Financials", ("bank", "financial", "insurance", "asset management",
                    "fintech", "payment", "exchange", "crypto", "btc",
                    "broker", "credit", "mortgage", "reinsurance",
                    "digital asset")),
    ("Energy", ("energy", "oil", "gas", "exploration", "refin",
                "pipeline", "midstream")),
    ("Utilities", ("utilit", "power", "electric", "water")),
    ("Real Estate", ("real estate", "reit", "property")),
    ("Industrials", ("industrial", "aerospace", "defense", "transport",
                     "logistics", "rail", "machinery", "construction",
                     "government services")),
    ("Materials", ("material", "chemical", "mining", "metal", "steel",
                   "paper", "packaging")),
)

# GICS sector → SPDR ETF symbol (the event_library sector_performance key)
GICS_SECTOR_SYMBOLS: dict[str, str] = {
    "Technology": "XLK", "Financials": "XLF", "Health Care": "XLV",
    "Consumer Discretionary": "XLY", "Consumer Staples": "XLP",
    "Energy": "XLE", "Industrials": "XLI", "Materials": "XLB",
    "Real Estate": "XLRE", "Utilities": "XLU",
    "Communication Services": "XLC",
}


def sector_to_gics(sector: Optional[str]) -> Optional[str]:
    """Map a free-form pipeline sector string onto one of the 11 GICS
    sectors (keyword match, first hit wins). None when unmatchable."""
    if not sector:
        return None
    text = str(sector).lower()
    for gics, words in _SECTOR_KEYWORDS:
        if any(w in text for w in words):
            return gics
    return None


# ── Realized vol from the reference window ──────────────────────────────────

def _realized_annual_vol_pct(fetcher: Callable, symbol: str,
                             start: str, end: str) -> Optional[float]:
    """Annualized realized vol (%) of `symbol` over the reference window.
    None on fetch failure / too few points → caller uses regime default."""
    try:
        px = fetcher(symbol, start, end)
    except Exception:
        return None
    closes = sorted(
        ((str(getattr(p, "time", "")), float(getattr(p, "close", 0) or 0))
         for p in (px or [])),
        key=lambda t: t[0],
    )
    closes = [c for d, c in closes if start <= d <= end and c > 0]
    if len(closes) < 10:
        return None
    rets = [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes))]
    n = len(rets)
    mean = sum(rets) / n
    var = sum((x - mean) ** 2 for x in rets) / n
    return round(math.sqrt(var) * math.sqrt(_TRADING_DAYS_PER_YEAR) * 100.0, 2)


def ticker_region(ticker: str) -> str:
    """"HK", "SG" or "US" for a holding.

    Anchoring an HKEX holding to a US sector ETF return is simply wrong when
    the two markets diverge — during the 2021-22 China crackdown the Hang
    Seng fell 52.8% while the S&P was down 1.6%. This is what routes a
    holding to its own market's crisis experience.
    """
    try:
        from src.tools.intl_provider import detect_market
        market = detect_market(ticker)
    except Exception:
        return "US"
    return {"hk": "HK", "sg": "SG"}.get(market or "", "US")


def _sector_anchor(ref: Optional[EventSpec], gics: Optional[str],
                   region: str = "US") -> Optional[tuple[float, str]]:
    """(return_pct, region_used) for a GICS sector in the holding's region.

    Falls back to the US row when the region has no calibrated basket for
    this event — which is the honest outcome for e.g. asian_fc_1997, where
    FMP's HK single-name history begins in 2000 and no HK basket exists.
    Returns None when neither region has a row (XLRE/XLC pre-inception).
    """
    if ref is None or not gics:
        return None
    for want in ([region, "US"] if region != "US" else ["US"]):
        for s in ref.sectors:
            if s.sector == gics and s.region == want:
                return s.return_pct, want
    return None


# Which regional benchmark stands in for a region's broad market.
_REGION_BENCHMARK = {"HK": "HSI", "SG": "STI"}


def _regional_anchor(ref: Optional[EventSpec], region: str) -> Optional[float]:
    """The region's broad-index return for this event, or None.

    Used when a holding's sector has no calibrated basket in its own market
    — better to anchor an HK name to the Hang Seng than to the S&P.
    """
    if ref is None or region == "US":
        return None
    key = _REGION_BENCHMARK.get(region)
    if not key:
        return None
    row = (ref.regional or {}).get(key) or {}
    val = row.get("return_pct")
    return None if val is None else float(val)


def _anchor_value(ref, gics, region="US") -> Optional[float]:
    got = _sector_anchor(ref, gics, region)
    return None if got is None else got[0]


# ── Search-necessity heuristic (deterministic; LLM confirms, never decides) ─

def decide_search(category: str, concerns: str,
                  classifications: list[dict],
                  ref: Optional[EventSpec],
                  override: str = "auto") -> dict:
    """override: 'auto' (heuristic) | 'always' | 'never'."""
    reasons: list[str] = []
    if override == "always":
        return {"recommended": True, "reasons": ["user override: always search"]}
    if override == "never":
        return {"recommended": False, "reasons": ["user override: never search"]}

    unknowns = [c["ticker"] for c in classifications if c.get("needs_classification")]
    if unknowns:
        reasons.append(
            "unclassified short product(s): " + ", ".join(sorted(unknowns))
            + " — identification needs live data")
    if ref is None:
        reasons.append("no historical reference crisis chosen — scenario has "
                       "no calibrated anchor")
    if (category or "").strip().lower() == "custom" and len((concerns or "").split()) < 12:
        reasons.append("custom category with thin detail — search can add grounding")
    return {"recommended": bool(reasons), "reasons": reasons}


# ── Deterministic skeleton (everything computed BEFORE the LLM call) ────────

def _build_skeleton(holdings: list[dict], ref: Optional[EventSpec],
                    sectors_map: dict[str, str],
                    fetcher: Optional[Callable],
                    horizon_days: int,
                    vol_cache: dict[str, Optional[float]]) -> dict:
    """Per-holding deterministic scenario estimates.

    Regular holdings  → anchored to their GICS sector's reference return.
    Classified short products → anchored underlying return + closed-form
    leverage/time-decay math.
    Unknown products  → no estimate (needs_classification surfaces instead).
    """
    spy_anchor = ref.spy_return_pct if ref else None
    qqq_anchor = ref.qqq_return_pct if ref else None
    regional_anchors = dict(ref.regional) if ref else {}
    regime = (ref.macro.volatility_regime if ref else "medium")
    default_vol = _VOL_REGIME_DEFAULTS_PCT.get(regime, 20.0)

    def _vol_for(symbol: str) -> tuple[float, str]:
        if symbol in vol_cache:
            v = vol_cache[symbol]
        else:
            v = None
            if fetcher is not None and ref is not None:
                v = _realized_annual_vol_pct(fetcher, symbol, ref.start, ref.end)
            vol_cache[symbol] = v
        if v is not None:
            return v, "realized"
        return default_vol, f"regime_default_{regime}"

    rows: list[dict] = []
    for h in sorted(holdings, key=lambda x: str(x["ticker"]).upper()):
        tkr = str(h["ticker"]).strip().upper()
        row: dict = {
            "ticker": tkr,
            "kind": "equity",
            "sector": None, "gics": None,
            "est_impact_pct": None, "anchor_pct": None,
            "weight_basis": round(float(h.get("quantity") or 0)
                                  * float(h.get("avg_cost") or 0), 2),
        }

        cls = classify_product(tkr, h.get("notes"))
        # A holding takes the product path only when it is a KNOWN product
        # ticker (curated map or documented-unknown set) or its notes
        # explicitly declare a leveraged/inverse exposure. Everything else
        # is a plain equity — classify_product returns needs_classification
        # for any unmapped ticker, which must NOT turn regular holdings
        # into "unknown products".
        is_product = (
            tkr in INVERSE_PRODUCT_MAP
            or tkr in UNCLASSIFIED_PRODUCTS
            or (cls.get("classified") and cls.get("confidence") == "notes")
        )
        if is_product and cls.get("classified"):
            row.update({
                "kind": "product",
                "product": {
                    "name": cls.get("name"),
                    "underlying": cls.get("underlying"),
                    "leverage": cls.get("leverage"),
                    "confidence": cls.get("confidence"),
                },
            })
            underlying = cls["underlying"]
            if underlying == "SPY":
                anchor = spy_anchor
            elif underlying == "QQQ":
                anchor = qqq_anchor
            else:
                # Leveraged/inverse products track their US underlying, so
                # they stay on the US anchor regardless of where they list.
                anchor = _anchor_value(ref, _SINGLE_NAME_SECTOR.get(underlying))
            row["anchor_pct"] = anchor
            if anchor is not None:
                vol, vol_src = _vol_for(underlying)
                decay = leveraged_scenario_return_pct(
                    anchor, vol, horizon_days, cls["leverage"])
                row.update({
                    "est_impact_pct": decay["est_return_pct"],
                    "no_decay_return_pct": decay["no_decay_return_pct"],
                    "decay_drag_pp": decay["decay_drag_pp"],
                    "vol_pct": vol, "vol_source": vol_src,
                    "horizon_days": horizon_days,
                })
        else:
            if is_product:
                # Product ticker with no classification and no parseable
                # note (only reachable via UNCLASSIFIED_PRODUCTS entries)
                # → surface loudly, no estimate.
                row.update({"kind": "unknown_product",
                            "product": {"hint": cls.get("hint")}})
            sec = sectors_map.get(tkr)
            gics = sector_to_gics(sec)
            region = ticker_region(tkr)
            row["sector"], row["gics"], row["region"] = sec, gics, region
            if row["kind"] == "equity":
                anchor, anchor_region = None, None
                if ref and gics:
                    got = _sector_anchor(ref, gics, region)
                    if got is not None:
                        anchor, anchor_region = got
                if anchor is None:
                    # No sector row anywhere — fall back to the region's own
                    # broad benchmark before reaching for SPY.
                    anchor = _regional_anchor(ref, region)
                    anchor_region = region if anchor is not None else None
                if anchor is None:
                    anchor, anchor_region = spy_anchor, "US"
                row["anchor_pct"] = anchor
                row["anchor_region"] = anchor_region
                row["anchor_region_fallback"] = bool(
                    anchor_region and region != "US" and anchor_region != region)
                if anchor is not None:
                    row["est_impact_pct"] = round(anchor, 2)
        rows.append(row)

    # Cost-basis weighted portfolio estimate over holdings with numbers
    total_w = sum(r["weight_basis"] for r in rows) or 0.0
    covered_w = sum(r["weight_basis"] for r in rows if r["est_impact_pct"] is not None)
    port_est = None
    if covered_w > 0:
        port_est = round(sum(r["weight_basis"] * r["est_impact_pct"] for r in rows
                             if r["est_impact_pct"] is not None) / covered_w, 2)
    return {
        "holdings": rows,
        "portfolio_est_impact_pct": port_est,
        "covered_weight_pct": round(covered_w / total_w * 100.0, 2) if total_w else None,
        "anchors": {"spy": spy_anchor, "qqq": qqq_anchor,
                    "regional": regional_anchors,
                    "sectors": ([s.as_dict() for s in ref.sectors] if ref else [])},
        "vol_default_pct": default_vol,
        "horizon_days": horizon_days,
    }


# ── Deterministic sensitivity recompute (assumption holds → portfolio delta) ─
#
# The LLM tags each assumption with the sector it governs + the pp shift if
# it proves true; this function re-runs the skeleton arithmetic with that
# shift applied. The LLM never does this math.
#
# Affected rows:
#   • equities whose gics matches the linked sector      → est += shift
#   • products on a SINGLE-NAME underlying in that sector → decay closed form
#     re-run on the shifted anchor (an inverse product gains when its sector
#     falls, minus the shifted decay drag)
# Unaffected (by design — index dilution): products on SPY/QQQ underlyings,
# rows without estimates, sectors that resolve to nothing.

_SYMBOL_TO_GICS = {v: k for k, v in GICS_SECTOR_SYMBOLS.items()}


def _resolve_gics(label: Optional[str]) -> Optional[str]:
    """Lenient resolution of an LLM-supplied sector label to one of the 11
    GICS names: exact name (any case), SPDR symbol (XLK…), keyword match."""
    if not label:
        return None
    text = str(label).strip()
    if not text:
        return None
    upper = text.upper()
    if upper in _SYMBOL_TO_GICS:
        return _SYMBOL_TO_GICS[upper]
    for name in GICS_SECTOR_SYMBOLS:
        if text.lower() == name.lower():
            return name
    return sector_to_gics(text)


def _portfolio_weighted_est(rows: list[dict]) -> Optional[float]:
    covered = [r for r in rows if r.get("est_impact_pct") is not None]
    w = sum(float(r.get("weight_basis") or 0.0) for r in covered)
    if w <= 0:
        return None
    return round(sum(float(r["weight_basis"]) * float(r["est_impact_pct"])
                     for r in covered) / w, 2)


def apply_assumption_shift(holdings_rows: list[dict],
                           linked_sector: Optional[str],
                           shift_pp: Optional[float]) -> dict:
    """Portfolio delta if one assumption holds. Pure-Python re-run of the
    skeleton math — {linked_gics, shift_pp, base_portfolio_est_pct,
    adjusted_portfolio_est_pct, delta_pp, affected_tickers}. Degenerates
    to delta 0 when the sector is unresolvable or the shift is absent."""
    base = _portfolio_weighted_est(list(holdings_rows or []))
    gics = _resolve_gics(linked_sector)
    try:
        shift = round(float(shift_pp), 2) if shift_pp is not None else None
    except (TypeError, ValueError):
        shift = None
    if gics is None or shift is None or shift == 0.0:
        return {
            "linked_gics": gics,
            "shift_pp": shift,
            "base_portfolio_est_pct": base,
            "adjusted_portfolio_est_pct": base,
            "delta_pp": 0.0,
            "affected_tickers": [],
        }

    adjusted_rows: list[dict] = []
    affected: list[str] = []
    for row in holdings_rows or []:
        r = dict(row)
        est = r.get("est_impact_pct")
        if est is None:
            adjusted_rows.append(r)
            continue
        if r.get("kind") == "equity" and r.get("gics") == gics:
            r["est_impact_pct"] = round(float(est) + shift, 2)
            affected.append(r.get("ticker"))
        elif r.get("kind") == "product":
            underlying = str((r.get("product") or {}).get("underlying") or "")
            if _SINGLE_NAME_SECTOR.get(underlying) == gics \
                    and r.get("anchor_pct") is not None \
                    and r.get("vol_pct") is not None \
                    and r.get("product", {}).get("leverage") is not None:
                decay = leveraged_scenario_return_pct(
                    float(r["anchor_pct"]) + shift,
                    float(r["vol_pct"]),
                    int(r.get("horizon_days") or DEFAULT_HORIZON_DAYS),
                    float(r["product"]["leverage"]),
                )
                r["est_impact_pct"] = decay["est_return_pct"]
                affected.append(r.get("ticker"))
        adjusted_rows.append(r)

    adjusted = _portfolio_weighted_est(adjusted_rows)
    delta = round(adjusted - base, 2) if (adjusted is not None
                                           and base is not None) else 0.0
    return {
        "linked_gics": gics,
        "shift_pp": shift,
        "base_portfolio_est_pct": base,
        "adjusted_portfolio_est_pct": adjusted,
        "delta_pp": delta,
        "affected_tickers": affected,
    }


# ── LLM structured output ────────────────────────────────────────────────────

# Lenient parsing (Q1 lesson): the structured call instructs the exact field
# names below, but cheap models drift to natural synonyms across runs
# (observed live: return_pct/note, item/quarter, tool, comment). Rather than
# chasing each variant with aliases — which also pollute the JSON schema the
# model is steered by — a before-validator normalizes a synonym table into
# the canonical keys on every nested model. The injected schema stays clean.
_SYNONYMS = {
    "est_return_pct": ("return_pct", "estimated_return_pct",
                       "expected_return_pct", "est_pct", "impact_pct",
                       "scenario_return_pct"),
    "est_impact_pct": ("estimated_impact_pct", "impact_pct", "est_return_pct"),
    "rationale": ("note", "reason", "comment", "explanation", "details"),
    "metric": ("item", "indicator", "datapoint", "assumption", "name"),
    "watch_for": ("trigger", "signal", "confirmation", "threshold"),
    "timing": ("quarter", "when", "timeframe", "visibility"),
    "instrument": ("tool", "ticker", "symbol", "vehicle"),
    "confidence": ("conf", "certainty", "probability", "conviction"),
    "sector": ("linked_sector", "sector_name", "gics_sector", "gics",
               "affected_sector"),
    "linked_sector": ("sector", "affected_sector", "gics_sector",
                      "sector_name", "gics"),
    "if_true_shift_pp": ("shift_pp", "sensitivity_pp", "impact_pp",
                         "sector_shift_pp", "shift_if_true_pp"),
}

_ACTIONS = {"SHORT", "BUY", "GOLD", "CASH", "HOLD"}


def _synonymize(data: object) -> object:
    if not isinstance(data, dict):
        return data
    out = dict(data)
    for canon, aliases in _SYNONYMS.items():
        if out.get(canon) in (None, ""):
            for a in aliases:
                if out.get(a) not in (None, ""):
                    out[canon] = out[a]
                    break
    return out


def _normalize_llm_output(data: object) -> object:
    """Structural coercion on the whole payload (observed live drift):

    • sector_impacts emitted as an OBJECT keyed by sector name → list;
    • most_affected_sectors / hedged_sectors entries as objects → names;
    • recommendations with action/tool swapped (enum value in 'tool');
    • entries missing their load-bearing keys are dropped, never fatal —
      one malformed entry must not kill the whole scenario block.
    """
    if not isinstance(data, dict):
        return data
    out = dict(data)

    value_keys = ("est_return_pct",) + tuple(_SYNONYMS["est_return_pct"])
    metric_keys = ("metric",) + tuple(_SYNONYMS["metric"])

    def _keep(entries, keys):
        if not isinstance(entries, list):
            return entries
        # Non-dict entries (already-built model instances on direct
        # construction) pass through untouched — pydantic validates them.
        return [e for e in entries
                if not isinstance(e, dict)
                or any(e.get(k) not in (None, "") for k in keys)]

    si = out.get("sector_impacts")
    if isinstance(si, dict):
        rows = []
        for k, v in si.items():
            if isinstance(v, dict):
                row = dict(v)
                row.setdefault("sector", str(k))
                rows.append(row)
            elif isinstance(v, (int, float)):
                rows.append({"sector": str(k), "est_return_pct": v})
        out["sector_impacts"] = rows
    out["sector_impacts"] = _keep(out.get("sector_impacts"), value_keys) or []

    out["assumptions_to_watch"] = (_keep(out.get("assumptions_to_watch"),
                                         metric_keys) or [])
    out["holding_impacts"] = _keep(out.get("holding_impacts"), ("ticker",)) or []

    for key in ("most_affected_sectors", "hedged_sectors"):
        lst = out.get(key)
        if isinstance(lst, list):
            normed = []
            for it in lst:
                if isinstance(it, dict):
                    name = it.get("sector") or it.get("name") or ""
                    if not name and it:
                        name = next(iter(it.values()))
                    normed.append(str(name))
                else:
                    normed.append(str(it))
            out[key] = normed

    recs = out.get("recommendations")
    if isinstance(recs, list):
        fixed = []
        for r in recs:
            if isinstance(r, dict):
                r = dict(r)
                action = str(r.get("action") or "").strip().upper()
                tool = str(r.get("tool") or "").strip().upper()
                if action not in _ACTIONS and tool in _ACTIONS:
                    r["action"], r["tool"] = tool, r.get("action")
                fixed.append(r)
            else:
                fixed.append(r)
        out["recommendations"] = fixed
    out["recommendations"] = (_keep(out.get("recommendations"),
                                    ("action", "tool")) or [])
    return out


class _SectorImpact(BaseModel):
    _norm = model_validator(mode="before")(_synonymize)
    sector: str = Field(description="GICS sector name (e.g. 'Technology')")
    symbol: Optional[str] = Field(default=None, description="SPDR ETF symbol if known (XLK, XLF...)")
    est_return_pct: float = Field(
        description="Expected % move over the horizon under this scenario")
    # rationale defaults to "": observed live drift emits sector rows WITHOUT
    # it (the numbers are the load-bearing part); a missing sentence must not
    # kill the whole scenario block.
    rationale: str = Field(default="", description="One sentence, <= 200 chars")


class _AssumptionWatch(BaseModel):
    _norm = model_validator(mode="before")(_synonymize)
    metric: str = Field(
        description="The specific quarterly datapoint to watch, e.g. 'Hyperscaler capex guidance'")
    watch_for: str = Field(
        default="",
        description="What reading would confirm vs disconfirm the scenario, <= 200 chars")
    timing: str = Field(
        default="",
        description="When it becomes visible, e.g. 'Q3 earnings, Oct 2026'")
    linked_sector: Optional[str] = Field(
        default=None,
        description="The single GICS sector whose scenario return this assumption governs")
    if_true_shift_pp: Optional[float] = Field(
        default=None,
        description="Percentage points that sector's scenario return moves if this assumption proves true")


class _HoldingImpact(BaseModel):
    _norm = model_validator(mode="before")(_synonymize)
    ticker: str
    est_impact_pct: Optional[float] = Field(
        default=None,
        description="Use the precomputed figure when given; adjust only with explicit rationale")
    # Observed live drift: the model echoes the precomputed skeleton rows back
    # verbatim (all numbers, no prose). The numbers are what matter; a missing
    # rationale must not discard the row.
    rationale: str = Field(default="", description="One sentence, <= 200 chars")


class _Recommendation(BaseModel):
    _norm = model_validator(mode="before")(_synonymize)
    action: str = Field(description="One of: SHORT, BUY, GOLD, CASH, HOLD")
    instrument: str = Field(
        description="Ticker or instrument (e.g. 'PSQ', 'XLU', 'GLD', 'gold')")
    rationale: str = Field(default="", description="<= 200 chars")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class _WhatIfLLMOutput(BaseModel):
    _norm = model_validator(mode="before")(_normalize_llm_output)
    scenario_summary: str = Field(description="3-5 sentence narrative of how this crisis unfolds, sector by sector")
    sector_impacts: list[_SectorImpact] = Field(default_factory=list)
    assumptions_to_watch: list[_AssumptionWatch] = Field(
        default_factory=list,
        description="4-8 items across earnings, inventory cycle, interest rates, stock levels")
    most_affected_sectors: list[str] = Field(default_factory=list)
    hedged_sectors: list[str] = Field(
        default_factory=list,
        description="Sectors that hold up or benefit in this scenario")
    holding_impacts: list[_HoldingImpact] = Field(default_factory=list)
    recommendations: list[_Recommendation] = Field(
        default_factory=list,
        description="Max 6, spanning SHORT/BUY/GOLD/CASH/HOLD tools; consider the user's existing short products")
    search_evidence_used: bool = Field(default=False)

    @model_validator(mode="after")
    def _reconcile_sector_lists(self):
        """Deterministic guard (user directive 2026-08-26): a sector cannot
        be BOTH most-affected and hedged/held-up. Names are normalized
        through the GICS resolver (aliases like 'Information Technology' →
        'Technology'), each list is deduped, hedged entries overlapping
        most-affected ones are dropped, and membership is cross-checked
        against sector_impacts numbers when present: a sector projected to
        fall hard is not 'hedged', a sector projected to rise is not 'most
        affected' (±2pp tolerance so float noise never flips membership)."""
        def norm(s):
            return _resolve_gics(s) or str(s or "").strip()

        affected, seen = [], set()
        for s in self.most_affected_sectors or []:
            n = norm(s)
            if n and n.lower() not in seen:
                seen.add(n.lower())
                affected.append(n)

        hedged, hedged_seen = [], set()
        for s in self.hedged_sectors or []:
            n = norm(s)
            if n and n.lower() not in hedged_seen:
                hedged_seen.add(n.lower())
                hedged.append(n)

        imp = {}
        for si in self.sector_impacts or []:
            g = norm(si.sector)
            if g:
                imp[g.lower()] = si.est_return_pct

        # Numbers win: filter membership against the projected returns
        # FIRST (a sector projected to fall hard is not 'hedged'; one
        # projected to rise is not 'most affected'), then resolve any
        # remaining overlap in favour of most_affected — the two lists
        # must be disjoint.
        affected = [s for s in affected if imp.get(s.lower(), -1.0) <= 2.0]
        hedged = [s for s in hedged if imp.get(s.lower(), 1.0) >= -2.0]
        seen = {s.lower() for s in affected}
        hedged = [s for s in hedged if s.lower() not in seen]

        self.most_affected_sectors = affected
        self.hedged_sectors = hedged
        return self


def _default_llm_caller(system_prompt: str,
                        user_prompt: str) -> tuple[Optional[_WhatIfLLMOutput], float]:
    """One deepseek-v4-flash structured call (hundred_q json_mode pattern)."""
    import os
    from src.llm.models import ModelProvider, get_model

    model_name = os.environ.get("WHAT_IF_MODEL", WHAT_IF_MODEL_NAME)
    try:
        llm = get_model(model_name, ModelProvider.DEEPSEEK, None)
    except Exception as exc:
        logger.warning("what_if: DeepSeek unavailable (%s)", exc)
        return None, 0.0
    if llm is None:
        return None, 0.0

    structured = llm.with_structured_output(_WhatIfLLMOutput, method="json_mode")
    messages = [("system", system_prompt), ("human", user_prompt)]
    try:
        result = structured.invoke(messages)
    except Exception as exc:
        logger.warning("what_if: structured output failed (%s); retrying raw.", exc)
        try:
            raw = llm.invoke(messages)
            text = raw.content if hasattr(raw, "content") else str(raw)
            start, end = text.find("{"), text.rfind("}")
            if start >= 0 and end > start:
                result = _WhatIfLLMOutput(**json.loads(text[start:end + 1]))
            else:
                return None, 0.0
        except Exception:
            logger.exception("what_if: JSON extraction failed")
            return None, 0.0

    approx_in = len(system_prompt + user_prompt) // 3
    approx_out = 900
    cost = approx_in * 0.27e-6 + approx_out * 1.1e-6
    return result, cost


# ── Prompts ──────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are a macro strategy desk modelling a HYPOTHETICAL future crisis for a \
private portfolio. You receive: the scenario category, the user's concerns, \
a historical reference crisis with calibrated sector returns, a PRECOMPUTED \
deterministic skeleton (per-holding estimates incl. leveraged-product time \
decay), and optional search evidence.

Respond in JSON format.

HARD RULES:
1. NEVER recompute leveraged/inverse product returns or decay figures — the \
skeleton's est_impact_pct/decay_drag_pp are exact closed-form math. Quote \
them, never alter them.
2. Sector estimates must stay within ±15 percentage points of the reference \
anchor for that sector unless the user's concerns justify a deviation; when \
deviating, say why in the rationale.
3. assumptions_to_watch: 4-8 items spanning earnings, inventory cycle, \
interest rates and stock levels — each with the quarter it becomes visible.
4. Recommendations span SHORT / BUY / GOLD / CASH / HOLD tools, max 6. \
Prefer instruments the user already owns (including their short products) \
when they fit the scenario. Gold (GLD/physical) is the classic \
rates/geopolitics hedge. Keep confidence honest (< 0.5 when evidence is thin).
5. If search evidence is provided and materially informs the scenario, set \
search_evidence_used=true and cite it in the relevant rationale.
6. Be concrete and sectoral, not vague: name the transmission channels.
7. For EVERY assumption also provide linked_sector (the single GICS sector \
whose scenario return the assumption governs, e.g. 'Technology') and \
if_true_shift_pp (how many percentage points that sector's scenario return \
moves if the assumption proves TRUE — negative when it deepens the crisis; \
keep within ±15 pp). These feed a deterministic sensitivity recompute — do \
not apply the shifts yourself anywhere else.
8. most_affected_sectors and hedged_sectors must be DISJOINT — a sector \
cannot be both most-affected and held-up. Derive them consistently from \
your sector_impacts signs: most affected = the sectors taking the largest \
NEGATIVE returns; hedged = sectors that hold up or benefit (near-flat to \
positive). Use canonical GICS sector names in both lists.
"""


def _build_user_prompt(category: str, concerns: str, horizon_days: int,
                       ref: Optional[EventSpec], skeleton: dict,
                       search_packs: list[dict]) -> str:
    parts = [
        f"SCENARIO CATEGORY: {category}",
        f"HORIZON: {horizon_days} days (~{horizon_days // 30} months)",
        "",
        "USER'S CONCERNS:",
        (concerns or "").strip()[:4000],
        "",
    ]
    if ref is not None:
        parts += [
            "REFERENCE CRISIS (historical anchor — the skeleton below is built on it):",
            json.dumps({
                "key": ref.key, "name": ref.name,
                "window": {"start": ref.start, "end": ref.end},
                "spy_return_pct": ref.spy_return_pct,
                "qqq_return_pct": ref.qqq_return_pct,
                "macro_notes": ref.macro.notes,
                "sector_performance": [s.as_dict() for s in ref.sectors],
            }, indent=1),
            "",
        ]
    else:
        parts += ["REFERENCE CRISIS: none chosen — anchor to the user's "
                  "concerns only and flag lower confidence.", ""]

    parts += [
        "PRECOMPUTED DETERMINISTIC SKELETON (do not recompute the math):",
        json.dumps({
            "portfolio_est_impact_pct": skeleton["portfolio_est_impact_pct"],
            "covered_weight_pct": skeleton["covered_weight_pct"],
            "holdings": skeleton["holdings"],
        }, indent=1),
        "",
    ]
    if search_packs:
        parts.append("SEARCH EVIDENCE:")
        for i, pack in enumerate(search_packs[:6], 1):
            parts.append(f"--- {i}. {pack.get('title', '?')[:90]} ---")
            parts.append((pack.get("content") or "")[:1200])
        parts.append("")
    parts.append(
        "Now emit the JSON: scenario_summary, sector_impacts (all 11 GICS "
        "sectors where the reference provides them), assumptions_to_watch "
        "(each with metric, watch_for, timing, linked_sector, "
        "if_true_shift_pp), most_affected_sectors, hedged_sectors, "
        "holding_impacts (every skeleton holding), recommendations, "
        "search_evidence_used.")
    return "\n".join(parts)


# ── Entry point ──────────────────────────────────────────────────────────────

def run_what_if(holdings: list[dict],
                category: str,
                concerns: str,
                reference_key: Optional[str] = None,
                search_override: str = "auto",
                horizon_days: int = DEFAULT_HORIZON_DAYS,
                sectors_map: Optional[dict[str, str]] = None,
                price_fetcher: Optional[Callable] = None,
                search_fn: Optional[Callable] = None,
                llm_caller: Optional[Callable] = None) -> dict:
    """Run one what-if simulation. Returns a JSON-safe dict.

    holdings:      [{ticker, quantity, avg_cost, notes?}]
    sectors_map:   {ticker: pipeline sector string} for anchor mapping
    price_fetcher: (ticker, start, end) -> prices (realized vol only)
    search_fn:     (query, days, max_results) -> [{title, content, ...}]
                   (defaults to complacency's tavily_search; failures
                   degrade to search_unavailable, never fatal)
    llm_caller:    (system, user) -> (_WhatIfLLMOutput|None, cost) — inject
                   in tests; defaults to one deepseek-v4-flash call.
    """
    if price_fetcher is None:
        from src.tools.api import get_prices
        price_fetcher = get_prices
    if llm_caller is None:
        llm_caller = _default_llm_caller

    ref = get_event(reference_key) if reference_key else None
    horizon_days = int(horizon_days or DEFAULT_HORIZON_DAYS)

    # Classification/search scope is PRODUCT CANDIDATES ONLY — curated product
    # tickers, documented-unknown ones, or holdings whose notes declare a
    # leveraged/inverse exposure. Regular equities must never be flagged as
    # "unclassified short products" (mirrors the skeleton's is_product guard).
    classifications = []
    for h in holdings:
        tkr = str(h["ticker"]).strip().upper()
        cls = classify_product(tkr, h.get("notes"))
        if (tkr in INVERSE_PRODUCT_MAP
                or tkr in UNCLASSIFIED_PRODUCTS
                or (cls.get("classified") and cls.get("confidence") == "notes")):
            classifications.append(cls)
    search = decide_search(category, concerns, classifications, ref,
                           search_override or "auto")

    search_packs: list[dict] = []
    search_used = False
    search_unavailable = False
    if search["recommended"]:
        if search_fn is None:
            from src.research_ideas.complacency.evidence_sources import tavily_search
            search_fn = tavily_search
        queries = [f"{category} crisis risk sectors {concerns[:120]}"]
        unknowns = [c["ticker"] for c in classifications
                    if c.get("needs_classification")]
        for u in unknowns[:2]:
            queries.append(f"{u} ETF inverse short what is")
        for q in queries[:3]:
            try:
                results = search_fn(q, 60, 4) or []
            except Exception:
                results = []
            search_packs.extend(results)
        if not search_packs:
            search_unavailable = True   # quota/network exhausted — degrade
        else:
            search_used = True

    vol_cache: dict[str, Optional[float]] = {}
    skeleton = _build_skeleton(holdings, ref, dict(sectors_map or {}),
                               price_fetcher, horizon_days, vol_cache)

    output, cost = llm_caller(_SYSTEM_PROMPT,
                              _build_user_prompt(category, concerns, horizon_days,
                                                 ref, skeleton, search_packs))

    warnings: list[str] = []
    for row in skeleton["holdings"]:
        if row["kind"] == "unknown_product":
            warnings.append(
                f"{row['ticker']} could not be classified as a short/inverse "
                f"product — {row['product'].get('hint', 'add a holding note')}")
        elif row["kind"] == "product" and row["product"]["confidence"] == "assumed":
            warnings.append(
                f"{row['ticker']} is treated as {row['product']['name']} "
                f"(ASSUMPTION — verify before acting)")
        elif row["kind"] in ("equity", "unknown_product") and row["est_impact_pct"] is None:
            warnings.append(f"{row['ticker']}: no scenario estimate "
                            f"(sector unmatchable or reference window lacks it)")

    result = {
        "category": category,
        "concerns": (concerns or "").strip(),
        "horizon_days": horizon_days,
        "reference_event": ref.as_dict() if ref else None,
        "search": {
            "recommended": search["recommended"],
            "reasons": search["reasons"],
            "used": search_used,
            "unavailable": search_unavailable,
        },
        "skeleton": {
            "holdings": skeleton["holdings"],
            "portfolio_est_impact_pct": skeleton["portfolio_est_impact_pct"],
            "covered_weight_pct": skeleton["covered_weight_pct"],
        },
        "llm": None,
        "warnings": warnings,
        "model": {"name": "deepseek-v4-flash", "cost_usd_est": round(cost, 6)},
    }
    if output is not None:
        result["llm"] = {
            "scenario_summary": output.scenario_summary,
            "sector_impacts": [s.model_dump() for s in output.sector_impacts],
            "assumptions_to_watch": [a.model_dump() for a in output.assumptions_to_watch],
            "most_affected_sectors": list(output.most_affected_sectors),
            "hedged_sectors": list(output.hedged_sectors),
            "holding_impacts": [h.model_dump() for h in output.holding_impacts],
            "recommendations": [r.model_dump() for r in output.recommendations],
            "search_evidence_used": bool(output.search_evidence_used),
        }
    else:
        result["warnings"].append("LLM unavailable — deterministic skeleton "
                                  "returned without narrative")
    return result


__all__ = [
    "CATEGORIES", "DEFAULT_HORIZON_DAYS", "INVERSE_PRODUCT_MAP",
    "UNCLASSIFIED_PRODUCTS", "SCENARIO_VERSION",
    "classify_product", "leveraged_scenario_return_pct", "sector_to_gics",
    "decide_search", "apply_assumption_shift", "run_what_if",
]
