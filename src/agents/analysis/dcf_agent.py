"""
Phase 4.5 — Upgraded DCF Engine (deterministic, no LLM)

UPGRADE (2026-03-17): Industry-Profile-Aware Multi-Method Intrinsic Value
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

What's new vs. prior version:
  1. Macro Handshake    — reads Phase 1 regime, computes C_macro confidence modifier
  2. Profile Classifier — maps sector + company characteristics → Master JSON Map profile
  3. Multi-Method IV    — blends all implementable methods using profile weights + C_macro
  4. Backward Gate      — T-1 Year Test: checks if model explains 12-month-ago price ±25%
  5. Forward Gate A     — 80/20 Rule: if TV >80% of total IV, de-weight DCF ↓20% → Asset Floor
  6. Forward Gate B     — Value Creation: if Forward ROIC < WACC, set TGR = 0

Multi-Method Blended IV Formula:
    IV = Σ (V_i × W_i × (1 + C_macro)) / Σ (W_i × (1 + C_macro))

Where:
    V_i     = intrinsic value from method i
    W_i     = profile weight for method i (from Master JSON Map)
    C_macro = aggregate macro confidence modifier from Phase 1 regime

Placement in pipeline:
    Phase 4 (Data Router) → [THIS] → Phase 5 (Investor Agents)

State writes:
    state["data"]["dcf_range"][ticker] = {
        "bear":  {intrinsic_value, growth_rate, fcf_margin_start, tgr, tv_pct, methods_used},
        "base":  {intrinsic_value, growth_rate, fcf_margin_start, tgr, tv_pct, methods_used},
        "bull":  {intrinsic_value, growth_rate, fcf_margin_start, tgr, tv_pct, methods_used},
        "wacc":              float,
        "c_macro":           float,   # NEW: macro confidence modifier
        "profile":           str,     # NEW: valuation profile name
        "leverage":          float,
        "shares_outstanding": float,
        "revenue_base":      float,
        "fcf_margin_base":   float,
        "data_source":       str,     # "analyst" | "guided" | "historical"
        "calibration_error": bool,    # NEW: T-1 backward gate flag
        "calibration_note":  str,     # NEW: detail on T-1 test
        "forward_flags":     list,    # NEW: forward gate warnings
    }

Fallback behaviour:
  - No analyst estimates (FMP free tier / 402) → historical revenue CAGR
  - Fewer than 2 years history → skip ticker, leave dcf_range[ticker] = {}
  - WACC ≤ terminal growth rate → clamp TGR to WACC - 0.5%
  - Method value = None → method is excluded from blend silently
"""

from __future__ import annotations

import logging
import statistics
from datetime import datetime, timedelta
from typing import Optional

from src.graph.state import AgentState
from src.tools.api import (
    search_line_items,
    get_analyst_estimates,
    get_prices,
    get_fx_rate,
    get_revenue_product_segmentation,
)
from src.data.sector_profiles import (
    get_wacc,
    get_wacc_for_exchange,
    get_sector_peer_multiples,
    TERMINAL_GROWTH_RATES,
    FCF_MARGIN_FLOOR,
    SECTOR_PEER_MULTIPLES,
    SECTOR_WACC,
    compute_c_macro,
    get_valuation_profile,
    get_wacc_profile_for_ticker,
    # ── Biopharma rNPV helpers (Tier 2) ──
    phase_pos,
    phase_years_to_launch,
    normalize_phase,
    therapeutic_area_pos_multiplier,
    RNPV_COMMERCIAL_DEFAULTS,
    RNPV_RAMP_PROFILE,
    PRE_APPROVAL_BIOTECH_WACC,
    LARGE_CAP_PHARMA_WACC,
)
from src.tools.hk.ticker import is_hk_ticker as _is_hk_ticker
from src.utils.progress import progress
from src.utils.api_key import get_api_key_from_state

_log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

_PROJECTION_YEARS = 10
_MIN_HISTORY_YEARS = 2
_DEFAULT_TGR = {"bear": 0.015, "base": 0.025, "bull": 0.035}

# Scenario growth multipliers applied to the derived base growth rate
_GROWTH_MULT = {"bear": 0.55, "base": 1.00, "bull": 1.50}

# Per-year FCF margin delta for each scenario
_MARGIN_DELTA_PER_YEAR = {"bear": -0.002, "base": 0.0, "bull": 0.002}

# Guidance-based margin adjustment
_GUIDANCE_MARGIN_DELTA = {
    "expanding":   0.003,
    "compressing": -0.003,
    "stable":      0.0,
}

# Forward Gate A: if terminal value > this fraction of total DCF value → trigger
_TV_DOMINANCE_THRESHOLD = 0.80

# Backward Gate: maximum allowed error between T-1 model price and actual price
_CALIBRATION_TOLERANCE = 0.25   # 25%

# Effective tax rate proxy for ROIC / NOPAT calculations
_EFFECTIVE_TAX_RATE = 0.21

# Asset floor weight shift when TV >80% (de-weight DCF by this, re-allocate to Asset Floor)
_TV_DOMINANCE_REWEIGHT = 0.20


# ── Private helpers ───────────────────────────────────────────────────────────

def _safe(val) -> Optional[float]:
    """Return float or None; swallow conversion errors."""
    if val is None:
        return None
    try:
        f = float(val)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def _extract_annual_series(line_items: list) -> tuple[list[dict], str]:
    """
    Extract annual records from LineItem objects (sorted newest-first).
    Returns (rows_sorted_oldest_first, reported_currency).
    reported_currency is taken from the first record with a non-empty value;
    defaults to "USD" if not present (safe for tickers that don't tag currency).
    """
    rows = []
    reported_currency = "USD"
    for li in line_items:
        rev = _safe(getattr(li, "revenue", None))
        if rev is None or rev <= 0:
            continue
        ccy = getattr(li, "currency", None) or "USD"
        if reported_currency == "USD" and ccy and ccy.upper() != "USD":
            reported_currency = ccy.upper()
        rows.append({
            "period":              li.report_period,
            "revenue":             rev,
            "free_cash_flow":      _safe(getattr(li, "free_cash_flow", None)),
            "shares_outstanding":  _safe(getattr(li, "shares_outstanding", None)),
            "debt_to_equity":      _safe(getattr(li, "debt_to_equity", None)),
            "net_debt":            _safe(getattr(li, "net_debt", None)),
            "ebitda":              _safe(getattr(li, "ebitda", None)),
            "net_income":          _safe(getattr(li, "net_income", None)),
            "total_assets":        _safe(getattr(li, "total_assets", None)),
            "total_equity":        _safe(getattr(li, "total_equity", None)),
            "dividends_per_share": _safe(getattr(li, "dividends_per_share", None)),
            "book_value_per_share":_safe(getattr(li, "book_value_per_share", None)),
            "capital_expenditure": _safe(getattr(li, "capital_expenditure", None)),
            "ebit":                _safe(getattr(li, "ebit", None)),
            "interest_expense":    _safe(getattr(li, "interest_expense", None)),
            "invested_capital":    _safe(getattr(li, "invested_capital", None)),
            "stock_based_compensation": _safe(getattr(li, "stock_based_compensation", None)),
        })

    # SBC-adjusted (owner-earnings) FCF: reported FCF treats SBC as non-cash and
    # adds it back to OCF. Owner-earnings FCF subtracts it back out because SBC
    # is a real dilution cost to shareholders even when it isn't a cash outflow.
    # Falls back to reported FCF when SBC is not disclosed (e.g. some utilities).
    for row in rows:
        fcf = row["free_cash_flow"]
        sbc = row["stock_based_compensation"]
        if fcf is not None and sbc is not None:
            row["fcf_owner_earnings"] = fcf - abs(sbc)
        else:
            row["fcf_owner_earnings"] = fcf

    rows.sort(key=lambda r: r["period"])
    return rows, reported_currency


def _historical_cagr(series: list[dict], revenue_base: Optional[float] = None) -> Optional[float]:
    """
    Compute historical revenue CAGR from the data series.

    Fix 3 — Recency Bias Guard:
    High-growth companies often have an inflated long-run CAGR because early years
    captured startup-phase expansion (e.g., SNOW $97M → $3.6B).  If the full-history
    CAGR exceeds the most-recent 2-year CAGR by more than 15 percentage points, we
    use the 2-year recency-weighted figure instead.  This prevents startup-era data
    from dominating a forward projection for a large, maturing company.

    Revenue-base cap (Fix 2a) is applied downstream in the scenario loop so that the
    scenario multipliers (0.55/1.00/1.50) still create differentiated bear/base/bull
    values before the final cap is imposed.
    """
    revenues = [r["revenue"] for r in series if r["revenue"] > 0]
    if len(revenues) < 2:
        return None
    n = len(revenues) - 1
    try:
        full_cagr = (revenues[-1] / revenues[0]) ** (1 / n) - 1
        full_cagr = max(min(full_cagr, 1.0), -0.30)

        # Recency check: compute most-recent 2-year CAGR when ≥3 data points exist
        if len(revenues) >= 3:
            recent_cagr = (revenues[-1] / revenues[-3]) ** (1 / 2) - 1
            recent_cagr = max(min(recent_cagr, 1.0), -0.30)
            # If full CAGR is materially higher than recent trend, use recent
            if full_cagr - recent_cagr > 0.15:
                return recent_cagr

        return full_cagr
    except (ZeroDivisionError, ValueError):
        return None


def _mean_fcf_margin(series: list[dict], field: str = "free_cash_flow") -> Optional[float]:
    """Compute 5-year average FCF margin with outlier exclusion.

    One-time acquisition capex (e.g. Cogentrix for VST) or restructuring years
    can drag the 5-year mean to unrealistic levels. We exclude years where the
    FCF margin deviates from the median by more than 2× IQR, then return the
    mean of remaining years. If fewer than 2 years remain after filtering,
    fall back to the median.

    ``field`` selects which cash-flow series to average. Default is reported
    ``free_cash_flow``; pass ``fcf_owner_earnings`` to get SBC-adjusted margin.
    """
    margins = []
    for row in series[-5:]:
        rev = row["revenue"]
        fcf = row.get(field)
        if fcf is not None and rev and rev != 0:
            margins.append(fcf / rev)
    if not margins:
        return None
    if len(margins) <= 2:
        return statistics.mean(margins)

    # IQR-based outlier exclusion
    sorted_m = sorted(margins)
    q1 = sorted_m[len(sorted_m) // 4]
    q3 = sorted_m[3 * len(sorted_m) // 4]
    iqr = q3 - q1
    med = statistics.median(margins)
    threshold = max(iqr * 2, 0.05)  # minimum 5pp threshold to avoid over-filtering
    filtered = [m for m in margins if abs(m - med) <= threshold]

    if len(filtered) >= 2:
        return statistics.mean(filtered)
    # Fallback: use median if too many outliers
    return med


def _analyst_revenue_growth(estimates: list, revenue_base: float) -> Optional[float]:
    if not estimates or not revenue_base:
        return None
    rev_est = _safe(estimates[0].revenue_avg)
    if rev_est is None or rev_est <= 0:
        return None
    return (rev_est / revenue_base) - 1


# ── Segment-type EV/Revenue multiples (for SOTP method) ──────────────────────
# Segment names are keyword-matched to a type label (hardware / services / ...),
# and the type label resolves to an EV/Revenue multiple VIA the tier table.
#
# Tiers reflect the quality of the business backing the segment:
#   "default" — generic industry averages. A commodity smartphone maker's
#               hardware segment, a small-cap IT services firm, a regional
#               retail chain. Multiples track long-run sector averages.
#   "premium" — ecosystem leaders where each segment is worth materially more
#               than the industry average because of moat / recurring revenue /
#               pricing power. AAPL, MSFT, GOOGL, AMZN, V, MA, LVMH. Multiples
#               are ~2× the default tier, calibrated so that SOTP approximates
#               market cap for names with healthy market-multiple valuations.
#
# Tier selection is driven by ``profile`` name (see _PROFILE_TIER_MAP below),
# not by ticker. A company sitting in the "Hyperscaler / Tech Conglomerate"
# profile automatically gets premium multiples on its segments.
#
# Note on EV/Revenue vs EV/EBITDA: we use EV/Revenue because FMP segment data
# is revenue-only (no segment-level EBITDA disclosure). Each multiple already
# bakes in a typical segment margin — e.g. premium services at 14× EV/Rev
# corresponds to ~28× EV/EBITDA at 50% EBITDA margin, which matches the
# analyst benchmark range for AAPL Services et al.

_SEGMENT_TYPE_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    # (matching keywords — case-insensitive substring — first match wins,  type label)
    # ORDER MATTERS: specific-before-generic. The top rules catch composite
    # bucket names (GOOGL "subscriptions, platforms, and devices", META
    # "Family of Apps", AMZN "Online stores") before they fall through to the
    # generic services / retail / software buckets.
    #
    # Keywords include both singular and plural so FMP labels like "Service"
    # (AAPL) and "Services" (MSFT) both match.
    #
    # Mixed bucket — GOOGL-style blended (subscription + hardware + platform).
    # Placed first so it beats the generic "subscription" match on services.
    (("subscriptions, platforms", "platforms and devices",
      "subscriptions and devices", "subscriptions, platforms, and devices"),
                                                                          "mixed_platform"),
    # Marketplace — commission businesses (AMZN 3P, Etsy)
    (("third-party", "seller", "marketplace", "commission"),              "marketplace"),
    # Advertising (includes META-specific "Family of Apps", social, newsfeed)
    (("advertising", "ads", "marketing", "search",
      "family of apps", "newsfeed", "social network", "social media"),    "advertising"),
    # 1P retail — razor-thin margin commodity e-commerce (AMZN "Online stores")
    # MUST come before generic retail / services so "online store" doesn't
    # match "store" alone (which would mix into the higher retail multiple).
    (("online store", "1p retail", "first-party retail",
      "e-commerce"),                                                      "retail_1p"),
    # Services (generic recurring / cloud / subscription)
    (("service", "cloud", "aws", "azure", "gcp", "saas", "subscription"), "services"),
    # Software — includes productivity / office / linkedin / workplace
    (("software", "apps", "application", "platform",
      "productivity", "business process", "office", "linkedin",
      "workplace"),                                                       "software"),
    (("data center", "data-center", "networking", "infrastructure"),      "infrastructure"),
    # Hardware — includes personal computing / windows
    (("iphone", "mac", "ipad", "watch", "hardware", "product", "device",
      "consumer electronics", "phone", "handset", "smartphone",
      "wearable", "personal computing", "windows"),                       "hardware"),
    (("retail", "store", "brick-and-mortar", "physical"),                 "retail"),
    (("wholesale", "distribution"),                                       "wholesale"),
    (("gaming", "games", "entertainment", "media"),                       "media"),
    (("automotive", "auto", "vehicle", "ev ", "battery"),                 "auto"),
    (("energy", "oil", "gas", "power", "utility"),                        "energy"),
    (("bank", "loan", "lending", "deposit", "insurance",
      "asset management"),                                                "financial"),
    (("health", "medical", "pharmacy", "drug", "clinical"),               "healthcare"),
]

_SEGMENT_MULTIPLE_TIERS: dict[str, dict[str, float]] = {
    # EV/Revenue multiples — bakes in typical operating margin for the segment type.
    # Three tiers by moat quality: default (no moat), mid (moderate / transitioning),
    # premium (ecosystem leaders). Tier assignment is driven by sector profile
    # (see _PROFILE_TIER_MAP below).
    "default": {
        "services":       6.0,
        "software":       5.5,
        "advertising":    6.5,
        "infrastructure": 5.0,
        "marketplace":    3.5,   # commission business (Mercari, Etsy)
        "mixed_platform": 4.5,   # blended subscription / hardware / platform
        "retail_1p":      0.7,   # commodity 1P e-commerce — razor-thin margins
        "hardware":       2.5,
        "retail":         1.5,
        "wholesale":      1.2,
        "media":          4.0,
        "auto":           2.0,
        "energy":         1.8,
        "financial":      3.0,
        "healthcare":     4.5,
        "default":        3.0,
    },
    "mid": {
        # Transitioning franchises — ORCL/SAP-scale legacy businesses with real
        # cloud momentum but not AAPL-level moats. Services ~9x EV/Rev ≈ 18x
        # EV/EBITDA at 50% margin — between generic-IT (12x) and hyperscaler
        # (30x+). Calibrated so a cloud-pivot name sits roughly 60% between
        # default and premium on most segment types.
        "services":       9.0,
        "software":       8.0,
        "advertising":    7.0,
        "infrastructure": 6.0,
        "marketplace":    4.5,
        "mixed_platform": 5.5,
        "retail_1p":      0.85,
        "hardware":       3.5,
        "retail":         1.8,
        "wholesale":      1.4,
        "media":          5.0,
        "auto":           2.5,
        "energy":         2.0,
        "financial":      3.5,
        "healthcare":     5.5,
        "default":        3.5,
    },
    "premium": {
        # Ecosystem leaders — recurring revenue, pricing power, annuity-like hardware.
        # Services 15.5x ≈ 31x EV/EBITDA at 50% margin (AAPL Services top of range).
        # Hardware 5.5x ≈ 17-18x EV/EBITDA at 30% margin (AAPL iPhone top of range).
        # Advertising 8.0x ≈ 22-24x EV/EBITDA at 35% margin (antitrust-discounted GOOGL).
        # Marketplace 6.0x ≈ AMZN 3P analyst SOTP range (0.8-1.0T EV on ~$156B rev).
        # Mixed platform 7.0x ≈ weighted avg of subscription (15x) + hardware (5x).
        # Retail_1P 1.0x ≈ Amazon 1P scale with Prime moat.
        "services":       15.5,
        "software":       12.0,
        "advertising":     8.0,
        "infrastructure":  8.0,
        "marketplace":     6.0,
        "mixed_platform":  7.0,
        "retail_1p":       1.0,
        "hardware":        5.5,
        "retail":          2.5,
        "wholesale":       1.8,
        "media":           7.0,
        "auto":            3.5,
        "energy":          2.5,
        "financial":       4.5,
        "healthcare":      7.0,
        "default":         4.5,
    },
}

# Profile → tier mapping. Profiles not listed here default to "default" tier.
# Premium tier is reserved for profiles whose archetypal member has AAPL-level
# moats (pricing power, recurring revenue, ecosystem lock-in). Mid tier is for
# transitioning franchises — legacy names with real cloud/digital momentum but
# without peak-franchise multiples (ORCL, SAP, TXN).
_PROFILE_TIER_MAP: dict[str, str] = {
    # Premium — full ecosystem leader multiples
    "Hyperscaler / Tech Conglomerate":           "premium",
    "Growth SaaS":                               "premium",
    "Cybersecurity / Mission-Critical SaaS":     "premium",
    "Payment Networks":                          "premium",
    "Market Infrastructure":                     "premium",
    "Luxury Goods":                              "premium",
    "Membership / Subscription Retail":          "premium",
    "Large Cap Pharma":                          "premium",
    "Managed Care":                              "premium",
    # Mid — transitioning franchises with moderate moats
    "Mature SaaS":                               "mid",
}


def _resolve_segment_tier(sector: str, profile: str) -> str:
    """Return "premium" or "default" based on the company's valuation profile."""
    return _PROFILE_TIER_MAP.get(profile or "", "default")


def _classify_segment(name: str, tier: str = "default") -> tuple[str, float]:
    """Classify a segment name → (type_label, EV/Revenue multiple for that tier).

    Case-insensitive substring match; first matching keyword wins. Falls back
    to "default" type (tier's default multiple) when nothing matches.
    """
    mults = _SEGMENT_MULTIPLE_TIERS.get(tier, _SEGMENT_MULTIPLE_TIERS["default"])
    n = (name or "").lower()
    for keywords, type_label in _SEGMENT_TYPE_KEYWORDS:
        if any(k in n for k in keywords):
            return type_label, mults.get(type_label, mults["default"])
    return "default", mults["default"]


def _sotp_enterprise_value(
    segments: dict[str, float],
    tier: str = "default",
) -> Optional[float]:
    """Sum per-segment EV using tier-adjusted type multiples.

    Returns aggregate EV across all segments, or None when input is empty /
    all-zero. Multiples vary by ``tier`` — see ``_SEGMENT_MULTIPLE_TIERS``.
    """
    if not segments:
        return None
    total_ev = 0.0
    for seg_name, seg_rev in segments.items():
        if seg_rev is None or seg_rev <= 0:
            continue
        _, mult = _classify_segment(seg_name, tier=tier)
        total_ev += seg_rev * mult
    return total_ev if total_ev > 0 else None


# ── Segment-name normalization for scenario → segment lookup ─────────────────
# FMP labels (e.g. "Service", "iPhone") and LLM-output labels (e.g. "Services",
# "iPhone segment") can differ slightly. Normalize both sides before matching
# so minor differences don't drop the scenario lookup.

def _normalize_segment_name(name: str) -> str:
    """Lowercase + strip whitespace + common punctuation for fuzzy matching."""
    return "".join(c for c in (name or "").lower().strip() if c.isalnum())


def _find_scenario_for_segment(
    segment_name: str,
    scenarios_by_segment: dict[str, dict],
) -> Optional[dict]:
    """Return the scenario block whose key best matches ``segment_name``.

    Tries exact match first, then normalized (punctuation/whitespace/case-
    insensitive) match, then bidirectional substring on the normalized keys.
    Returns None if no reasonable match exists.
    """
    if not scenarios_by_segment:
        return None
    # Exact
    if segment_name in scenarios_by_segment:
        return scenarios_by_segment[segment_name]
    # Normalized
    norm_target = _normalize_segment_name(segment_name)
    normalized_map = {_normalize_segment_name(k): (k, v)
                      for k, v in scenarios_by_segment.items()}
    if norm_target in normalized_map:
        return normalized_map[norm_target][1]
    # Bidirectional substring on normalized keys
    for n_key, (_, v) in normalized_map.items():
        if n_key and (n_key in norm_target or norm_target in n_key):
            return v
    return None


# ── Probabilistic SOTP 12m (Monte Carlo) ─────────────────────────────────────
# Consumes segment-scenario trees from the deep research extractor. Each
# segment has a list of (prob, rate) scenarios summing to 1.0. For each
# Monte Carlo iteration we draw one scenario per segment (independent) and
# compute total EV. The resulting distribution captures right-tail hypergrowth
# (e.g. 5% chance of NVDA data center 3x-ing) and left-tail contraction
# without any hardcoded numeric clamp.

_SOTP_MC_ITERATIONS = 10_000


def _draw_scenario_rate(scenarios: list[dict], rng: "random.Random") -> float:
    """Sample one scenario from the list by its probability. Returns the rate."""
    r = rng.random()
    cumulative = 0.0
    for s in scenarios:
        cumulative += s.get("prob", 0.0)
        if r <= cumulative:
            return float(s.get("rate", 0.0))
    # Numerical edge: cumulative just under 1.0; return last scenario's rate
    return float(scenarios[-1].get("rate", 0.0))


def _sotp_12m_probabilistic(
    segments: dict[str, float],
    scenarios_by_segment: dict[str, dict],
    tier: str,
    net_debt: float,
    shares: float,
    fallback_growth: float = 0.0,
    n_iter: int = _SOTP_MC_ITERATIONS,
    seed: int = 20260421,
) -> Optional[dict]:
    """Monte Carlo probabilistic SOTP 12m.

    For each of ``n_iter`` iterations, draws one scenario per segment
    (segments without scenarios use ``fallback_growth`` as a single-point rate)
    and sums segment EV = revenue × (1 + rate) × tier_multiple. Subtracts
    net_debt to get equity, divides by shares for per-share IV.

    Returns a dict with:
        mean, p10, p50, p90, p99, stdev  — distribution stats per share
        segments_with_scenarios          — how many segments had scenario data
        segments_fallback                — how many fell back to flat growth
        sample_iv_p50                    — median IV (primary output)
    Or None if preconditions fail (no segments, non-positive shares, etc.).

    Deterministic via ``seed`` so identical inputs yield identical stats
    across runs — critical for reproducibility in a valuation pipeline.
    """
    import random as _random
    if not segments or shares is None or shares <= 0:
        return None

    # Pre-resolve scenario lookup for each segment (skip segments with zero rev)
    segment_data = []
    n_with_scenarios = 0
    n_fallback = 0
    for name, rev in segments.items():
        if rev is None or rev <= 0:
            continue
        _, mult = _classify_segment(name, tier=tier)
        scen_block = _find_scenario_for_segment(name, scenarios_by_segment)
        scenarios = scen_block.get("scenarios") if scen_block else None
        if scenarios:
            n_with_scenarios += 1
        else:
            n_fallback += 1
        segment_data.append((name, float(rev), mult, scenarios))

    if not segment_data:
        return None

    rng = _random.Random(seed)
    per_share_ivs: list[float] = []
    for _ in range(n_iter):
        total_ev = 0.0
        for _name, rev, mult, scen in segment_data:
            rate = _draw_scenario_rate(scen, rng) if scen else fallback_growth
            total_ev += rev * (1.0 + rate) * mult
        equity = total_ev - (net_debt or 0.0)
        per_share_ivs.append(max(equity / shares, 0.0))

    per_share_ivs.sort()
    def pct(p: float) -> float:
        idx = min(n_iter - 1, max(0, int(round(p * (n_iter - 1)))))
        return per_share_ivs[idx]

    mean_iv = sum(per_share_ivs) / n_iter
    var = sum((v - mean_iv) ** 2 for v in per_share_ivs) / n_iter
    return {
        "mean":    round(mean_iv, 2),
        "p10":     round(pct(0.10), 2),
        "p50":     round(pct(0.50), 2),
        "p90":     round(pct(0.90), 2),
        "p99":     round(pct(0.99), 2),
        "stdev":   round(var ** 0.5, 2),
        "segments_with_scenarios": n_with_scenarios,
        "segments_fallback":       n_fallback,
    }


def _normalized_earnings(
    series: list[dict],
    field: str,
    window: int = 5,
) -> Optional[float]:
    """Cycle-normalized earnings figure for ``field`` (e.g. net_income, ebitda).

    Method (Damodaran): compute the mean of (field / revenue) over the window,
    then multiply by current revenue. This captures *what would earnings be if
    current revenue ran at average-cycle profitability?* — the correct
    normalization for cyclicals where revenue trends upward but margins cycle.
    For stable businesses the adjustment is nearly a no-op, so applying it
    uniformly across profiles is safe.

    Uses IQR-based outlier exclusion (same pattern as ``_mean_fcf_margin``)
    to reject one-off years — massive goodwill write-downs, COVID anomalies,
    special dividends, etc.

    Returns None when fewer than 2 usable observations are available.
    """
    tail = series[-window:]
    if not tail:
        return None
    margins: list[float] = []
    for row in tail:
        rev = row.get("revenue")
        val = row.get(field)
        if rev and rev > 0 and val is not None:
            margins.append(val / rev)
    if len(margins) < 2:
        return None

    if len(margins) <= 2:
        avg_margin = statistics.mean(margins)
    else:
        sorted_m = sorted(margins)
        q1 = sorted_m[len(sorted_m) // 4]
        q3 = sorted_m[3 * len(sorted_m) // 4]
        iqr = q3 - q1
        med = statistics.median(margins)
        threshold = max(iqr * 2, 0.05)
        filtered = [m for m in margins if abs(m - med) <= threshold]
        avg_margin = statistics.mean(filtered) if len(filtered) >= 2 else med

    current_revenue = tail[-1].get("revenue")
    if current_revenue is None or current_revenue <= 0:
        return None
    return avg_margin * current_revenue


def _analyst_growth_bands(
    estimates: list,
    revenue_base: float,
    min_analysts: int = 3,
) -> Optional[dict]:
    """Derive bear / base / bull revenue growth rates from analyst dispersion.

    Uses the nearest forward-year estimate's low / avg / high revenue figures
    (and the analyst-count quality gate) to produce asymmetric scenario growth
    rates that reflect actual market disagreement, rather than the symmetric
    ±45% multiplier used when dispersion data is unavailable.

    Returns a dict ``{"bear","base","bull","analyst_count"}`` or None if:
      - estimates list empty / no revenue_base
      - any of low / avg / high is missing
      - fewer than ``min_analysts`` analysts cover revenue (noisy single-analyst
        dispersions would otherwise distort scenarios)
      - values are not monotonic (low ≤ avg ≤ high) — malformed data

    Growth rates are clamped to [-30%, +100%] to match the DCF engine's
    existing safety bounds.
    """
    if not estimates or not revenue_base or revenue_base <= 0:
        return None
    est = estimates[0]
    lo  = _safe(getattr(est, "revenue_low",  None))
    av  = _safe(getattr(est, "revenue_avg",  None))
    hi  = _safe(getattr(est, "revenue_high", None))
    cov = getattr(est, "analyst_count_revenue", None)
    if lo is None or av is None or hi is None:
        return None
    if cov is None or cov < min_analysts:
        return None
    if lo <= 0 or av <= 0 or hi <= 0 or not (lo <= av <= hi):
        return None

    def _implied(rev_est: float) -> float:
        return max(min((rev_est / revenue_base) - 1.0, 1.0), -0.30)

    return {
        "bear":           _implied(lo),
        "base":           _implied(av),
        "bull":           _implied(hi),
        "analyst_count":  int(cov),
    }


def _guided_growth(guidance: dict, revenue_base: float = 0.0) -> Optional[float]:
    """Extract forward growth rate from management guidance.

    Priority 1: explicit revenue_growth_pct (percentage, e.g. 15 → 0.15)
    Priority 2: revenue_guidance_mid (dollar amount) converted to implied
                growth rate using current revenue_base.  This bridges the gap
                where _extract_management_guidance() captures "$44B–$45B"
                but stores it as a dollar figure, not a percentage.
    """
    raw = guidance.get("revenue_growth_pct")
    if raw is not None:
        val = _safe(raw)
        return val / 100.0 if val is not None else None

    # Fallback: convert dollar revenue guidance to implied growth rate
    rev_mid = guidance.get("revenue_guidance_mid")
    if rev_mid and revenue_base and revenue_base > 0:
        rev_mid_f = _safe(rev_mid)
        if rev_mid_f and rev_mid_f > 0:
            implied = (rev_mid_f / revenue_base) - 1.0
            # Sanity: guidance should be within -30% to +100% of current revenue
            if -0.30 <= implied <= 1.0:
                return implied
    return None


# ── Core DCF Engine ───────────────────────────────────────────────────────────

def _project_dcf(
    revenue_base: float,
    fcf_margin_base: float,
    growth_rate: float,
    margin_delta_per_year: float,
    wacc: float,
    tgr: float,
    fcf_floor: float,
    net_debt: float,
    shares: float,
    years: int = _PROJECTION_YEARS,
) -> tuple[float, float, float, list[dict]]:
    """
    Core DCF engine.  Returns (intrinsic_value_per_share, pv_fcf_sum_per_share,
    pv_tv_per_share, annual_rows).
    Splitting PV components allows the Forward Gate A (80/20 TV check).
    annual_rows is a list of dicts, one per projection year:
      { year_label, revenue, growth_pct, fcf_margin, fcf, discount_factor, pv_fcf }
    All monetary values are absolute (not per-share).
    """
    if shares is None or shares <= 0:
        return 0.0, 0.0, 0.0, []

    annual_rows = []
    pv_sum = 0.0
    for t in range(1, years + 1):
        rev_t    = revenue_base * (1 + growth_rate) ** t
        margin_t = max(fcf_margin_base + margin_delta_per_year * t, fcf_floor)
        margin_t = min(margin_t, 0.60)
        fcf_t    = rev_t * margin_t
        disc_t   = 1 / (1 + wacc) ** t
        pv_fcf_t = fcf_t * disc_t
        pv_sum  += pv_fcf_t
        annual_rows.append({
            "year_label":      f"Yr {t}",
            "revenue":         rev_t,
            "growth_pct":      growth_rate,
            "fcf_margin":      margin_t,
            "fcf":             fcf_t,
            "discount_factor": disc_t,
            "pv_fcf":          pv_fcf_t,
        })

    rev_T = revenue_base * (1 + growth_rate) ** years
    margin_T = max(fcf_margin_base + margin_delta_per_year * years, fcf_floor)
    margin_T = min(margin_T, 0.60)
    fcf_T = rev_T * margin_T
    fcf_terminal = fcf_T * (1 + tgr)
    tv = fcf_terminal / (wacc - tgr)
    pv_tv = tv / (1 + wacc) ** years

    equity_value = pv_sum + pv_tv - (net_debt or 0.0)
    iv = equity_value / shares
    return iv, pv_sum / shares, pv_tv / shares, annual_rows


# ── rNPV (Biopharma pipeline) helpers ─────────────────────────────────────────

def _compute_rnpv(
    pipeline_assets: list[dict],
    most_recent: dict,
    shares: float,
    net_debt: float,
    wacc: float,
    profile_name: str,
    scenario: str = "base",
) -> tuple[Optional[float], dict]:
    """
    Risk-adjusted NPV of a biopharma pipeline. Each asset is valued as a
    bell-shaped cash flow stream (ramp + plateau + LOE decay) starting at
    its expected launch year, weighted by cumulative phase PoS × therapeutic-
    area multiplier, and discounted to today.

    Returns (iv_per_share, audit_dict). iv_per_share is None when the pipeline
    is empty or no assets survive validation. audit_dict surfaces per-asset
    PV contributions, applied PoS, and bridge to equity value.

    Scenario multipliers: scenarios adjust peak-sales expectation symmetrically
    around base — bear applies 0.75× peak, bull 1.25× peak. This is narrower
    than the scenario multipliers used for relative-valuation methods because
    clinical/commercial uncertainty is already loaded into PoS and WACC; adding
    another wide scenario band would double-count the risk.

    Parameters
    ----------
    pipeline_assets : list of asset dicts from _extract_pipeline_assets().
    most_recent     : latest annual record — read for cash (-net_debt), R&D,
                      and current year for launch_year discounting.
    shares          : reported shares outstanding (will be diluted +10% for
                      Pre-approval Biotech profile to approximate future raises
                      when FMP doesn't expose diluted share count).
    net_debt        : net debt from most_recent (used in equity bridge).
    wacc            : base WACC from the engine — OVERRIDDEN to 12% for
                      Pre-approval Biotech profile (clinical-stage premium).
    profile_name    : "Pre-approval Biotech" | "Large Cap Pharma" | other.
    scenario        : "bear" | "base" | "bull" — peak-sales multiplier.
    """
    if not pipeline_assets or shares <= 0:
        return None, {}

    # Profile-specific WACC — rNPV uses tighter rates than the sector default:
    #   Large Cap Pharma:      7.85% (Damodaran Drugs Pharma, Jan 2026)
    #   Pre-approval Biotech: 11.00% (Damodaran Biotech 8.49% + clinical-stage
    #                                  premium for liquidity/diversification risk)
    # Other Biopharma sub-profiles (Managed Care, MedTech, CDMO) use the
    # engine's sector WACC input unchanged — rNPV doesn't currently route
    # to those profiles, but the fallback keeps the contract stable.
    if profile_name == "Large Cap Pharma":
        effective_wacc = LARGE_CAP_PHARMA_WACC
    elif profile_name == "Pre-approval Biotech":
        effective_wacc = max(wacc, PRE_APPROVAL_BIOTECH_WACC)
    else:
        effective_wacc = wacc

    # Dilution reserve — the `shares` input is already FMP's diluted count
    # (weightedAverageShsOutDil — includes options, warrants, convertibles).
    # The 10% buffer here projects ADDITIONAL dilution from expected future
    # secondary offerings between today and commercialization. Only applied
    # to pre-revenue biotech; Big Pharma funds R&D from approved-drug cash
    # flows and does not routinely raise equity.
    dilution_factor = 1.10 if profile_name == "Pre-approval Biotech" else 1.00
    effective_shares = shares * dilution_factor

    # Scenario → peak-sales multiplier (narrow band, see docstring)
    peak_scen_mult = {"bear": 0.75, "base": 1.0, "bull": 1.25}.get(scenario, 1.0)

    # Profile-specific margin + tax — Large Cap Pharma benefits from Irish/Swiss
    # IP structures (eff. tax ~14%) and mature 45% op margins; Pre-approval
    # biotechs taxed at US statutory 21% with narrower novel-drug margins 40%.
    # Unknown profiles fall through to default (40% / 21%).
    _margin_cfg = RNPV_COMMERCIAL_DEFAULTS.get(profile_name, RNPV_COMMERCIAL_DEFAULTS["default"])
    op_margin = _margin_cfg["peak_op_margin"]
    tax       = _margin_cfg["effective_tax_rate"]

    current_year = datetime.now().year

    total_pipeline_pv = 0.0
    asset_breakdown: list[dict] = []
    weighted_years_to_launch = 0.0
    total_raw_peak = 0.0

    for asset in pipeline_assets:
        phase_key = normalize_phase(asset.get("phase"))
        base_pos  = phase_pos(phase_key)
        # TA multiplier applies to PRE-APPROVAL assets only — once a drug is
        # approved, the clinical/scientific risk is realized. Continuing to
        # discount for therapeutic-area risk would double-penalize (e.g. an
        # approved oncology drug would lose 45% of its value despite being
        # on-market and generating revenue).
        if phase_key == "approved":
            ta_mult = 1.0
        else:
            ta_mult = therapeutic_area_pos_multiplier(asset.get("indication"))
        pos = max(0.005, min(1.0, base_pos * ta_mult))

        # Years-to-launch: prefer asset-supplied launch_year if sane, else
        # fall back to phase median
        explicit_launch = asset.get("launch_year") or 0
        if explicit_launch and current_year <= explicit_launch <= current_year + 15:
            years_to_launch = float(explicit_launch - current_year)
        else:
            years_to_launch = phase_years_to_launch(phase_key)
        # Already-launched assets (approved + launch in past) contribute
        # immediately (years_to_launch = 0)
        if phase_key == "approved" and years_to_launch < 0:
            years_to_launch = 0.0

        peak_sales = float(asset.get("peak_sales_usd", 0)) * peak_scen_mult
        if peak_sales <= 0:
            continue

        # Cash-flow stream: pre-approval assets follow the full ramp + plateau
        # + LOE profile. Already-approved assets skip the ramp years (they
        # are at plateau) and use only plateau + LOE decay. Without this gate
        # a marketed blockbuster is valued as if just-launched (year-1 at 20%
        # of peak), which under-counts Big Pharma's approved-drug value by
        # ~30-40%.
        #
        # Assumption: when the extractor doesn't supply a launch_year, we
        # assume marketed drugs have already consumed ~2 years of their
        # commercial window (i.e. start at year 3 of the ramp profile, which
        # is near-peak). This is a rough mid-point; for pinpoint accuracy the
        # extractor should supply launch_year and years_since_launch gets
        # computed directly. The current-year launch_year path also lands
        # here via years_to_launch == 0 and approved phase.
        if phase_key == "approved":
            cf_profile = RNPV_RAMP_PROFILE[2:]   # skip 20% + 50% ramp years
        else:
            cf_profile = RNPV_RAMP_PROFILE

        asset_pv = 0.0
        for ramp_idx, ramp_frac in enumerate(cf_profile):
            t_from_today = years_to_launch + ramp_idx + 1  # year 1 of sales = launch_year+1
            after_tax_cf = peak_sales * ramp_frac * op_margin * (1 - tax)
            pv = after_tax_cf / ((1 + effective_wacc) ** t_from_today)
            asset_pv += pv

        asset_rnpv = asset_pv * pos
        total_pipeline_pv += asset_rnpv
        weighted_years_to_launch += years_to_launch * peak_sales
        total_raw_peak += peak_sales

        asset_breakdown.append({
            "name":              asset.get("name"),
            "phase":             phase_key,
            "indication":        asset.get("indication", ""),
            "base_phase_pos":    base_pos,
            "ta_multiplier":     ta_mult,
            "effective_pos":     pos,
            "peak_sales_usd":    peak_sales,
            "years_to_launch":   years_to_launch,
            "undiscounted_cf":   asset_pv / pos if pos > 0 else 0.0,
            "risk_adjusted_pv":  asset_rnpv,
        })

    if not asset_breakdown:
        return None, {}

    # Equity bridge: + cash − debt − PV of future R&D burn (pre-revenue only)
    cash = max(-(net_debt or 0.0), 0.0)   # net_debt < 0 implies net cash
    debt = max((net_debt or 0.0), 0.0)

    future_rd_pv = 0.0
    if profile_name == "Pre-approval Biotech":
        current_rd = most_recent.get("research_and_development") or 0.0
        avg_years_to_launch = (
            weighted_years_to_launch / total_raw_peak if total_raw_peak > 0 else 5.0
        )
        # PV of R&D annuity for `avg_years_to_launch` years at effective WACC.
        # pv_annuity_factor = (1 - (1+r)^-n) / r
        if effective_wacc > 0 and avg_years_to_launch > 0 and current_rd > 0:
            annuity_factor = (1 - (1 + effective_wacc) ** (-avg_years_to_launch)) / effective_wacc
            future_rd_pv = current_rd * annuity_factor

    equity_value = total_pipeline_pv + cash - debt - future_rd_pv
    iv_per_share = max(equity_value / effective_shares, 0.0)

    audit = {
        "pipeline_pv":              total_pipeline_pv,
        "cash":                     cash,
        "debt":                     debt,
        "future_rd_pv":             future_rd_pv,
        "equity_value":             equity_value,
        "shares_reported":          shares,
        "shares_diluted":           effective_shares,
        "effective_wacc":           effective_wacc,
        "assets":                   asset_breakdown,
        "n_assets":                 len(asset_breakdown),
        "scenario":                 scenario,
        "peak_scenario_multiplier": peak_scen_mult,
    }
    return iv_per_share, audit


# ── Multi-Method Valuation Engine ─────────────────────────────────────────────

def _compute_method_value(
    method_name: str,
    most_recent: dict,
    revenue_base: float,
    shares: float,
    net_debt: float,
    market_cap: float,
    wacc: float,
    growth_base: float,
    fcf_margin_base: float,
    tgr: float,
    fcf_floor: float,
    sector: str,
    scenario: str,
    reported_currency: str = "USD",
    is_hk: bool = False,
    growth_premium: float = 1.0,
    sbc_pe_discount: float = 1.0,
    profile_name: str = "",
    forward_consensus: Optional[dict] = None,
) -> Optional[float]:
    """
    Compute intrinsic value per share for a single valuation method.
    Returns None if required data is unavailable.

    growth_premium: PEG-inspired multiplier applied to relative-value methods
    (P/E, EV/EBITDA, EV/Revenue, P/BV, FCF Yield) to adjust peer multiples
    for the company's growth rate relative to its sector average.  1.0 = no
    adjustment.  DCF/EPV methods are NOT adjusted (they already use growth_base).

    profile_name: sub-profile override (e.g. "REIT", "Money Center Bank") so
    peer multiples are looked up at the profile level when one exists. Critical
    for REITs — without it, SGX REITs resolve to the US RealEstate peer table
    (pe=35, pb=1.5) instead of the REIT table (pe=14, pb=1.0), producing
    intrinsic values 2-3x too high.

    Non-implementable methods (marked in INDUSTRY_VALUATION_PROFILES with
    'implementable': False + 'proxy': ...) are resolved to their proxy method
    before reaching here by the caller — so this function only sees implementable
    method names or proxy names.
    """
    peer = get_sector_peer_multiples(sector, is_hk=is_hk, profile_name=profile_name)
    ebitda = most_recent.get("ebitda")
    net_income = most_recent.get("net_income")
    ebit = most_recent.get("ebit")
    bvps = most_recent.get("book_value_per_share")
    total_equity = most_recent.get("total_equity")
    total_assets = most_recent.get("total_assets")
    dividends_ps = most_recent.get("dividends_per_share")
    capex = most_recent.get("capital_expenditure")
    invested_capital = most_recent.get("invested_capital")

    # Scenario multipliers for relative value methods
    scenario_mult = {"bear": 0.75, "base": 1.00, "bull": 1.25}
    sm = scenario_mult.get(scenario, 1.0)

    # ── DCF / DCF variants ─────────────────────────────────────────────────
    dcf_family = {"DCF", "DCF (2-stage)", "DCF (FCF+)", "DCF (Levered)", "DCF (5-yr)",
                  "DCF (LTG)", "NRR-adj DCF", "Rev DCF (ARR)", "Backlog DCF",
                  "PPA-backed DCF", "Unit Econ DCF", "Rev DCF (GMV)", "Rev DCF",
                  "Power Price DCF", "Reverse DCF", "Rev DCF (Mkt Sh)"}
    if method_name in dcf_family:
        iv, _, _, _ = _project_dcf(
            revenue_base, fcf_margin_base, growth_base, 0.0,
            wacc, tgr, fcf_floor, net_debt, shares,
        )
        return iv

    # ── EPV (Earnings Power Value) ─────────────────────────────────────────
    # EPV = steady-state earnings power with NO growth assumed.
    # Scenario multiplier (sm) scales normalized EBIT to reflect:
    #   Bear (0.75): earnings under a normalised downturn / margin pressure
    #   Base (1.00): current reported EBIT, no change assumed
    #   Bull (1.25): earnings at peak / expanded operating leverage
    # Without sm, EPV is identical across all three scenarios — a CHECK #1 error.
    if method_name in {"EPV"}:
        if ebit is not None and ebit > 0 and wacc > 0:
            nopat = ebit * sm * (1 - _EFFECTIVE_TAX_RATE)   # sm ∈ {0.75, 1.00, 1.25}
            ev = nopat / wacc
            return max((ev - (net_debt or 0.0)) / shares, 0.0)
        return None

    # ── EV/EBITDA (+ EBITDAR proxy: same logic, EBITDAR ≈ EBITDA+rent) ────
    if method_name in {"EV/EBITDA", "EV/EBIT", "Utility P/E", "EV/EBITDAR"}:
        mult = peer.get("ev_ebitda", 12.0) * sm * growth_premium
        # Change 7: apply Chinese ADR multiple haircut for CNY-reporting US-listed companies
        if reported_currency == "CNY":
            mult *= peer.get("cn_adr_haircut", 1.0)
        metric = ebitda if method_name != "EV/EBIT" else ebit
        if metric and metric > 0 and shares > 0:
            ev = metric * mult
            return max((ev - (net_debt or 0.0)) / shares, 0.0)
        return None

    # ── EV/EBITDA (norm) — uses 5-yr cycle-normalized EBITDA ──────────────
    # Same peer multiple, but anchored on Damodaran-normalized EBITDA
    # (mean EBITDA margin × current revenue) so peak/trough years don't
    # distort the multiple application. Critical for cyclicals: mining,
    # merchant power, auto, semis, chemicals.
    if method_name in {"EV/EBITDA (norm)", "EV/EBITDA norm", "Normalized EV/EBITDA"}:
        norm_ebitda = most_recent.get("normalized_ebitda")
        if norm_ebitda is None or norm_ebitda <= 0 or shares <= 0:
            return None
        mult = peer.get("ev_ebitda", 12.0) * sm * growth_premium
        if reported_currency == "CNY":
            mult *= peer.get("cn_adr_haircut", 1.0)
        ev = norm_ebitda * mult
        return max((ev - (net_debt or 0.0)) / shares, 0.0)

    # ── SOTP (Sum of Parts) — per-segment EV/Revenue multiples ────────────
    # Uses FMP product-segment revenue breakdown with keyword-matched multiples
    # per segment type (Services ~6x, Hardware ~2.5x, Retail ~1.5x, etc.).
    # Only fires when the ticker disclosed segments and they landed on
    # ``most_recent["segment_breakdown"]`` (set by run_dcf_agent). Deliberately
    # ignores scenario multiplier (sm) — the scenario signal lives in the
    # segment revenue levels when/if analysts update them, not in an artificial
    # ±25% overlay.
    if method_name in {"SOTP (segments)", "Sum of Parts", "SOTP", "SOTP (Segments)"}:
        seg = most_recent.get("segment_breakdown")
        if not seg or shares <= 0:
            return None
        # Tier-adjust segment multiples: ecosystem leaders (AAPL, MSFT, V/MA,
        # luxury) get "premium" multiples on each segment type, materially
        # uplifting SOTP so it tracks market cap for healthy market-multiple
        # names. Tier is driven by the sector profile (see _PROFILE_TIER_MAP).
        tier = _resolve_segment_tier(sector, profile_name)
        total_ev = _sotp_enterprise_value(seg, tier=tier)
        if total_ev is None:
            return None
        # Apply growth premium to the aggregate, same pattern as EV/Revenue.
        # No CNY haircut here — the per-segment multiples are already generic
        # (not peer-table sourced), so the ADR discount would be speculative.
        # Equity = EV − net_debt; when net_debt < 0 (net cash), this adds the
        # cash pile back — matches the standard SOTP accounting for AAPL etc.
        total_ev *= growth_premium
        return max((total_ev - (net_debt or 0.0)) / shares, 0.0)

    # ── SOTP 12m (probabilistic) — Monte Carlo with scenario trees ────────
    # Same tier-based multiples, but each segment revenue is grown by a rate
    # sampled from a probabilistic scenario tree produced by the deep research
    # agent. Scenarios have no clamp — hypergrowth and contraction tails flow
    # through honestly. Output is by scenario: bear → p10, base → p50, bull → p90.
    # If segment scenarios are unavailable, segments fall back to a flat
    # ``fallback_growth`` (from most_recent) so the method still produces a
    # deterministic number equivalent to the current SOTP × (1 + growth_base).
    if method_name in {"SOTP 12m (probabilistic)", "SOTP 12m", "Probabilistic SOTP"}:
        seg = most_recent.get("segment_breakdown")
        if not seg or shares <= 0:
            return None
        scenarios = most_recent.get("segment_scenarios") or {}
        tier = _resolve_segment_tier(sector, profile_name)
        fallback_g = most_recent.get("_sotp_fallback_growth", 0.0)
        dist = _sotp_12m_probabilistic(
            segments=seg,
            scenarios_by_segment=scenarios,
            tier=tier,
            net_debt=(net_debt or 0.0),
            shares=shares,
            fallback_growth=float(fallback_g),
        )
        if dist is None:
            return None
        # Stash the full distribution on most_recent so the DCF engine's
        # reporting layer can surface percentiles beyond the single returned IV.
        most_recent.setdefault("sotp_12m_distribution", {})[scenario] = dist
        # Map bear/base/bull → P10/P50/P90 so the existing scenario plumbing
        # picks up asymmetric tail exposure automatically.
        pct_key = {"bear": "p10", "base": "p50", "bull": "p90"}.get(scenario, "p50")
        return dist.get(pct_key)

    # ── EV/Revenue and variants ────────────────────────────────────────────
    if method_name in {"EV/NTM Revenue", "EV/NTM Rev", "EV/Revenue", "EV/Fwd Rev"}:
        mult = peer.get("ev_revenue", 4.0) * sm * growth_premium
        # Change 7: apply Chinese ADR multiple haircut — CNY-reporting US-listed
        # companies trade at a persistent discount to Western peers due to VIE risk,
        # regulatory uncertainty and capital controls. Source: Damodaran 2025 ADR study.
        if reported_currency == "CNY":
            mult *= peer.get("cn_adr_haircut", 1.0)
        if revenue_base > 0 and shares > 0:
            ev = revenue_base * mult
            return max((ev - (net_debt or 0.0)) / shares, 0.0)
        return None

    # ── P/E (TTM / operating) ─────────────────────────────────────────────
    # Uses trailing-12m net income. "P/E (ops)" and "P/E (Premium)" share
    # this branch — they differ only in documentation intent, not earnings
    # source. For the TRUE cycle-normalized path use "P/E (norm)" below.
    if method_name in {"P/E", "P/E (ops)", "P/E (Premium)", "P/E (Ops)"}:
        mult = peer.get("pe", 18.0) * sm * growth_premium * sbc_pe_discount
        eps = (net_income / shares) if (net_income is not None and shares > 0) else None
        if eps and eps > 0:
            return eps * mult
        return None

    # ── P/E (norm) — uses 5-yr cycle-normalized net income ────────────────
    # For cyclicals the trailing net income reflects one point in the cycle;
    # applying a peer P/E at peak earnings produces trough IV (and vice-versa).
    # The normalized anchor is Damodaran-style: mean(NI margin) × current
    # revenue. Falls back to None (method skipped) when insufficient history.
    if method_name in {"P/E (norm)", "P/E norm", "Normalized P/E"}:
        norm_ni = most_recent.get("normalized_net_income")
        if norm_ni is None or norm_ni <= 0 or shares <= 0:
            return None
        mult = peer.get("pe", 18.0) * sm * growth_premium * sbc_pe_discount
        eps_norm = norm_ni / shares
        return eps_norm * mult

    # ── Forward P/E (consensus EPS × peer P/E) ─────────────────────────────
    # Uses analyst consensus EPS for the nearest forward fiscal year instead
    # of trailing net income. Scenarios map directly to the analyst dispersion
    # (eps_low / eps_avg / eps_high) so there is no scenario multiplier (sm)
    # — dispersion IS the scenario signal. Growth premium and SBC discount
    # still apply to the multiple.
    if method_name in {"Forward P/E", "Fwd P/E", "NTM P/E"}:
        if forward_consensus is None:
            return None
        eps_fwd = forward_consensus.get("eps", {}).get(scenario)
        if eps_fwd is None or eps_fwd <= 0:
            return None
        mult = peer.get("pe", 18.0) * growth_premium * sbc_pe_discount
        if reported_currency == "CNY":
            mult *= peer.get("cn_adr_haircut", 1.0)
        return eps_fwd * mult

    # ── Forward EV/EBITDA (consensus EBITDA × peer EV/EBITDA) ──────────────
    # Uses analyst consensus EBITDA; scenarios map to low/avg/high dispersion.
    # Same no-sm logic as Forward P/E.
    if method_name in {"Forward EV/EBITDA", "Fwd EV/EBITDA", "NTM EV/EBITDA"}:
        if forward_consensus is None:
            return None
        ebitda_fwd = forward_consensus.get("ebitda", {}).get(scenario)
        if ebitda_fwd is None or ebitda_fwd <= 0 or shares <= 0:
            return None
        mult = peer.get("ev_ebitda", 12.0) * growth_premium
        if reported_currency == "CNY":
            mult *= peer.get("cn_adr_haircut", 1.0)
        ev = ebitda_fwd * mult
        return max((ev - (net_debt or 0.0)) / shares, 0.0)

    # ── P/BV ──────────────────────────────────────────────────────────────
    if method_name in {"P/BV", "P/Rate Base", "NAV Discount", "SOTP / NAV",
                       "NAV (Project)", "Pipeline NAV"}:
        mult = peer.get("pb", 2.0) * sm * growth_premium
        if bvps and bvps > 0:
            return bvps * mult
        # fallback: total_equity / shares
        if total_equity and total_equity > 0 and shares > 0:
            return (total_equity / shares) * mult
        return None

    # ── FCF Yield ─────────────────────────────────────────────────────────
    if method_name in {"FCF Yield", "P/CF", "Price/CF"}:
        target_yield = peer.get("fcf_yield", 0.05) / (sm * growth_premium)  # higher growth → lower yield req → higher price
        target_yield = max(target_yield, 0.01)
        # Prefer SBC-adjusted (owner-earnings) FCF; falls back to reported FCF
        # when SBC isn't disclosed (fcf_owner_earnings is seeded to reported
        # FCF in _extract_annual_series when SBC is missing).
        fcf = most_recent.get("fcf_owner_earnings") or most_recent.get("free_cash_flow")
        if fcf and fcf > 0 and shares > 0:
            return (fcf / shares) / target_yield
        return None

    # ── rNPV (Biopharma pipeline) ─────────────────────────────────────────
    # Risk-adjusted NPV of the drug pipeline. Pipeline assets are extracted
    # from deep research by _extract_pipeline_assets(); each asset is valued
    # as a bell-shaped cash flow stream (ramp + plateau + LOE) weighted by
    # cumulative phase PoS × therapeutic-area multiplier. When no pipeline
    # assets are available the method returns None (falls through to the
    # profile's DCF proxy via the blend engine).
    if method_name in {"rNPV", "rNPV (Pipeline)"}:
        assets = most_recent.get("pipeline_assets") or []
        if not assets:
            return None
        iv, audit = _compute_rnpv(
            pipeline_assets=assets,
            most_recent=most_recent,
            shares=shares,
            net_debt=(net_debt or 0.0),
            wacc=wacc,
            profile_name=profile_name,
            scenario=scenario,
        )
        if iv is not None:
            # Stash the audit on most_recent for the engine to surface in
            # ticker_forward_flags. Keyed by scenario so bear/base/bull all
            # retain their own audit trail.
            most_recent.setdefault("_rnpv_audit", {})[scenario] = audit
        return iv

    # ── EV/R&D (for pre-revenue biotech) ─────────────────────────────────
    if method_name in {"EV/R&D", "EV/R&D Spend"}:
        rd = most_recent.get("research_and_development")
        if rd and rd > 0 and shares and shares > 0:
            rd_multiple = peer.get("ev_rd", 6.0) * sm * growth_premium
            ev = rd * rd_multiple
            return max((ev - (net_debt or 0.0)) / shares, 0.0)
        return None

    # ── DDM (Gordon Growth) ───────────────────────────────────────────────
    if method_name == "DDM":
        div = dividends_ps
        if div and div > 0 and wacc > tgr:
            d_next = div * (1 + tgr)
            return d_next / (wacc - tgr)
        return None

    # ── LBO Floor ─────────────────────────────────────────────────────────
    if method_name in {"LBO Floor", "LBO Analysis"}:
        # Simplified LBO: EBITDA × 7x entry multiple, 40% equity, 5-yr exit at 8x
        if ebitda and ebitda > 0 and shares > 0:
            entry_ev = ebitda * 7.0
            equity_entry = entry_ev * 0.40
            exit_ev = ebitda * 8.0 * sm
            exit_equity = max(exit_ev - entry_ev * 0.60, 0.0)
            irr_gross = (exit_equity / equity_entry) ** (1 / 5) - 1 if equity_entry > 0 else 0
            # If LBO IRR > 20%, floor ≈ current equity entry
            if irr_gross >= 0.20:
                return max((entry_ev - (net_debt or 0.0)) / shares, 0.0)
        return None

    # ── Residual Income ───────────────────────────────────────────────────
    if method_name == "Residual Income":
        # RI model: BV + PV(excess returns)
        roe = (net_income / total_equity) if (net_income and total_equity and total_equity > 0) else None
        if roe is not None and bvps is not None and bvps > 0 and wacc > 0:
            excess_return = (roe - wacc) * bvps
            # Capitalise excess return: assume mean-reversion over 10 years → multiply by 5x
            ri_premium = (excess_return / wacc) * 0.5 * sm
            return max(bvps + ri_premium, bvps * 0.5)
        return None

    # ── ROE vs CoE (Gordon-Growth RoE spread) ─────────────────────────────
    if method_name == "ROE vs CoE":
        if total_equity and total_equity > 0 and net_income and shares > 0:
            roe = net_income / total_equity
            spread = roe - wacc
            pb_implied = 1.0 + spread / wacc
            pb_implied = max(pb_implied, 0.5) * sm
            bv = (total_equity / shares)
            return bv * pb_implied
        return None

    # ── ROIC vs WACC (also matches bare "ROIC" from Consumer profiles) ───
    if method_name in {"ROIC vs WACC", "ROIC"}:
        # Use invested_capital if available, else approximate as total_assets - cash
        ic = invested_capital
        if ic and ic > 0 and ebit and shares > 0:
            nopat = ebit * (1 - _EFFECTIVE_TAX_RATE)
            roic = nopat / ic
            spread = roic - wacc
            ev = ic * (1.0 + spread / wacc) * sm
            return max((ev - (net_debt or 0.0)) / shares, 0.0)
        return None

    # ── Cash Runway (biotech-specific) ────────────────────────────────────
    if method_name == "Cash Runway":
        # Floor = cash / shares (net cash position)
        net_cash = -(net_debt or 0.0)
        if net_cash > 0 and shares > 0:
            return net_cash / shares
        return None

    # ── Generic proxy fallback ────────────────────────────────────────────
    # Any method not matched above is unimplementable without specialty data.
    return None


def _blend_methods(
    profile_methods: list[dict],
    method_values: dict[str, Optional[float]],
    c_macro: float,
    forward_flags: list[str],
    dcf_tv_fraction: float,
) -> Optional[float]:
    """
    Apply the Master Map weights with C_macro modifier.

    Formula: IV = Σ(V_i × W_i × (1+C_macro)) / Σ(W_i × (1+C_macro))

    Forward Gate A: if dcf_tv_fraction > 0.80, reduce DCF family weight by
    _TV_DOMINANCE_REWEIGHT and redistribute to P/BV (asset floor).
    """
    adjusted_methods = []
    asset_floor_reweight = 0.0

    for m in profile_methods:
        raw_name = m["name"]
        # Resolve proxy
        effective_name = m.get("proxy", raw_name) if not m.get("implementable", True) else raw_name
        value = method_values.get(effective_name)
        if value is None:
            value = method_values.get(raw_name)
        if value is None or value <= 0:
            continue

        w = m["weight"]

        # Forward Gate A: de-weight DCF family if TV-dominated
        dcf_family_names = {"DCF", "DCF (2-stage)", "DCF (FCF+)", "NRR-adj DCF",
                            "Rev DCF (ARR)", "Backlog DCF", "PPA-backed DCF",
                            "Unit Econ DCF", "Power Price DCF", "Reverse DCF",
                            "DCF (Levered)", "Rev DCF (Mkt Sh)"}
        if dcf_tv_fraction > _TV_DOMINANCE_THRESHOLD:
            if raw_name in dcf_family_names or effective_name in {"DCF"}:
                asset_floor_reweight += w * _TV_DOMINANCE_REWEIGHT
                w = w * (1 - _TV_DOMINANCE_REWEIGHT)
                if "80/20 Rule: DCF weight reduced (TV > 80%)" not in forward_flags:
                    forward_flags.append("80/20 Rule: DCF weight reduced (TV > 80%)")

        adjusted_methods.append((value, w))

    # Add Asset Floor (P/BV proxy) if weight was shifted from DCF
    if asset_floor_reweight > 0:
        asset_floor_val = method_values.get("P/BV")
        if asset_floor_val and asset_floor_val > 0:
            adjusted_methods.append((asset_floor_val, asset_floor_reweight))

    if not adjusted_methods:
        return None

    multiplier = 1.0 + c_macro
    numerator   = sum(v * w * multiplier for v, w in adjusted_methods)
    denominator = sum(w * multiplier     for _, w in adjusted_methods)

    return numerator / denominator if denominator > 0 else None


# ── Backward Logic Gate ───────────────────────────────────────────────────────

def _run_backward_gate(
    ticker: str,
    series: list[dict],
    sector: str,
    end_date: str,
    wacc: float,
    tgr: float,
    fcf_floor: float,
    api_key: str,
    profile_data: Optional[dict] = None,
    reported_currency: str = "USD",
) -> tuple[bool, str]:
    """
    T-1 Year Test: run the valuation model with data from ~12 months ago and
    compare to the actual stock price at that time.

    Uses the same blended multi-method approach as the main valuation when
    profile_data is provided, falling back to pure DCF otherwise.

    Returns (calibration_error: bool, calibration_note: str).
    calibration_error=True means the model is >25% off → flag "Calibration Error".
    """
    if len(series) < 3:
        return False, "Skipped — insufficient history for T-1 test"

    try:
        # Approximate T-1 date as 1 year before end_date
        end_dt = datetime.strptime(end_date[:10], "%Y-%m-%d")
        t1_date = (end_dt - timedelta(days=365)).strftime("%Y-%m-%d")
        t1_start = (end_dt - timedelta(days=380)).strftime("%Y-%m-%d")

        prices = get_prices(ticker, t1_start, t1_date, api_key=api_key)
        if not prices:
            return False, "Skipped — no historical price data for T-1"

        actual_price = float(prices[-1].close) if hasattr(prices[-1], "close") else float(prices[-1].get("close", 0))
        if actual_price <= 0:
            return False, "Skipped — invalid T-1 price"

        # Use second-most-recent year as T-1 baseline financials
        t1_row = series[-2]
        revenue_t1 = t1_row.get("revenue", 0)
        shares_t1  = t1_row.get("shares_outstanding") or series[-1].get("shares_outstanding")
        net_debt_t1 = t1_row.get("net_debt") or 0.0

        # Build FCF margin from paired FCF/revenue rows (must be from same row to avoid misalignment)
        fcf_margin_pairs = [
            r.get("free_cash_flow") / r.get("revenue")
            for r in series[:-1]
            if r.get("free_cash_flow") is not None and r.get("revenue") and r["revenue"] > 0
        ]
        fcf_margin_t1 = statistics.mean(fcf_margin_pairs) if fcf_margin_pairs else 0.0

        # Historical growth rate from T-2 data
        growth_t1 = _historical_cagr(series[:-1]) or 0.05

        if not shares_t1 or shares_t1 <= 0 or not revenue_t1 or revenue_t1 <= 0:
            return False, "Skipped — missing T-1 shares or revenue"

        # ── Core DCF for T-1 (always needed as DCF method input) ──────────
        iv_dcf_t1, pv_fcf_t1, pv_tv_t1, _ = _project_dcf(
            revenue_t1, fcf_margin_t1, growth_t1, 0.0,
            wacc, tgr, fcf_floor, net_debt_t1, shares_t1,
        )
        tv_fraction_t1 = (pv_tv_t1 / (pv_fcf_t1 + pv_tv_t1)
                          if (pv_fcf_t1 + pv_tv_t1) > 0 else 0.0)

        # ── Blended IV using same profile as main run (when available) ─────
        if profile_data and profile_data.get("methods"):
            method_values_t1: dict[str, Optional[float]] = {"DCF": iv_dcf_t1}

            methods_to_compute: set[str] = set()
            for m in profile_data.get("methods", []):
                if m.get("implementable", True):
                    methods_to_compute.add(m["name"])
                elif "proxy" in m:
                    methods_to_compute.add(m["proxy"])

            for method_name in methods_to_compute:
                if method_name not in method_values_t1:
                    method_values_t1[method_name] = _compute_method_value(
                        method_name=method_name,
                        most_recent=t1_row,
                        revenue_base=revenue_t1,
                        shares=shares_t1,
                        net_debt=net_debt_t1,
                        market_cap=revenue_t1 * 10,
                        wacc=wacc,
                        growth_base=growth_t1,
                        fcf_margin_base=fcf_margin_t1,
                        tgr=tgr,
                        fcf_floor=fcf_floor,
                        sector=sector,
                        scenario="base",
                        reported_currency=reported_currency,
                        is_hk=_is_hk_ticker(ticker),
                        profile_name=profile_name,
                    )

            for ex in profile_data.get("excluded", []):
                method_values_t1.pop(ex, None)

            forward_flags_t1: list[str] = []
            iv_t1_blended = _blend_methods(
                profile_methods=profile_data["methods"],
                method_values=method_values_t1,
                c_macro=0.0,  # no macro adjustment for historical T-1 test
                forward_flags=forward_flags_t1,
                dcf_tv_fraction=tv_fraction_t1,
            )
            iv_t1 = iv_t1_blended if (iv_t1_blended is not None and iv_t1_blended > 0) else iv_dcf_t1
            method_label = "blended"
        else:
            iv_t1 = iv_dcf_t1
            method_label = "DCF"

        if iv_t1 <= 0:
            return False, "Skipped — T-1 model returned non-positive IV"

        error_pct = abs(iv_t1 - actual_price) / actual_price
        if error_pct > _CALIBRATION_TOLERANCE:
            note = (f"Calibration Error: T-1 {method_label} IV ${iv_t1:.2f} vs actual "
                    f"${actual_price:.2f} = {error_pct:.0%} error (>{_CALIBRATION_TOLERANCE:.0%} tolerance)")
            return True, note

        note = (f"T-1 passed ({method_label}): model ${iv_t1:.2f} vs actual "
                f"${actual_price:.2f} = {error_pct:.0%} error")
        return False, note

    except Exception as e:
        return False, f"Skipped — T-1 test error: {e}"


# ── Public Entry Point ────────────────────────────────────────────────────────

def run_dcf_agent(state: AgentState) -> AgentState:
    """
    Phase 4.5 — run multi-method blended DCF for each ticker.

    Reads:
        state["data"]["macro_regime"]       — Phase 1 output (for C_macro)
        state["data"]["tickers"]
        state["data"]["sector"]
        state["data"]["management_guidance"]

    Writes:
        state["data"]["dcf_range"][ticker]  — extended schema with c_macro, profile,
                                              calibration_error, forward_flags
    """
    agent_id = "dcf_engine"
    tickers = state["data"]["tickers"]
    end_date = state["data"]["end_date"]
    # Per-ticker sector map built by strategic_router (multi-ticker runs).
    # Fall back to shared sector for single-ticker runs.
    sectors_map = state["data"].get("sectors", {})
    _primary_sector = state["data"].get("sector", "Tech")
    mgmt_guidance_all      = state["data"].get("management_guidance", {})
    segment_scenarios_all  = state["data"].get("segment_scenarios", {})
    # Per-ticker Biopharma pipeline assets for the rNPV method.
    # Produced by _extract_pipeline_assets() in deep_research.py.
    pipeline_assets_all    = state["data"].get("pipeline_assets", {})
    # Per-ticker signals from deep research sections 2D (cycle) + 2F (KPI framework).
    # Produced by _extract_dcf_calibration() in deep_research.py.
    dcf_calibration_all  = state["data"].get("dcf_calibration_signals", {})
    api_key = get_api_key_from_state(state, "FINANCIAL_DATASETS_API_KEY")

    # ── Macro Handshake (Phase 1 input) ─────────────────────────────────────
    macro_regime = state["data"].get("macro_regime", {})
    c_macro = compute_c_macro(macro_regime)
    regime_str = (f"{macro_regime.get('risk_appetite', '?')} | "
                  f"{macro_regime.get('rate_direction', '?')} rates | "
                  f"{macro_regime.get('volatility_regime', '?')} vol")
    progress.update_status(agent_id, "global",
                           f"Macro Handshake: C_macro={c_macro:+.2f} ({regime_str})")

    # ── Guardrail 3: sector validity check before first lookup ───────────────
    # All downstream .get() calls use silent fallbacks; we surface any sector
    # issue here so it appears in logs and progress output before the first ticker.
    _sector_confidence = state["data"].get("sector_confidence", "HIGH")
    _sector_warning    = state["data"].get("sector_warning")
    dcf_range: dict[str, dict] = {}

    for ticker in tickers:
        # Resolve per-ticker sector so WACC, TGR, and FCF floor reflect the correct industry
        sector = sectors_map.get(ticker, _primary_sector)

        if sector not in SECTOR_WACC:
            _log.error(
                "[DCF] Unrecognised sector '%s' for %s — WACC/TGR/profile will use fallback defaults. "
                "Check TICKER_SECTOR_LOOKUP in sector_profiles.py.", sector, ticker
            )
            progress.update_status(agent_id, ticker,
                                    f"⚠ SECTOR '{sector}' not in SECTOR_WACC — using Tech fallbacks")
        elif _sector_confidence != "HIGH":
            _log.warning("[DCF] Sector confidence = %s for '%s'. %s",
                         _sector_confidence, sector, _sector_warning or "")

        tgr_table = TERMINAL_GROWTH_RATES.get(sector, _DEFAULT_TGR)
        fcf_floor = FCF_MARGIN_FLOOR.get(sector, -0.05)

        if sector not in TERMINAL_GROWTH_RATES:
            _log.warning("[DCF] Sector '%s' not in TERMINAL_GROWTH_RATES — using default TGR.", sector)
        if sector not in FCF_MARGIN_FLOOR:
            _log.warning("[DCF] Sector '%s' not in FCF_MARGIN_FLOOR — using -5%% default.", sector)
        progress.update_status(agent_id, ticker, "Fetching historical financials")

        try:
            line_items = search_line_items(
                ticker,
                ["revenue", "free_cash_flow", "shares_outstanding",
                 "debt_to_equity", "net_debt", "ebitda", "net_income",
                 "total_equity", "total_assets", "dividends_per_share",
                 "book_value_per_share", "capital_expenditure", "ebit",
                 "interest_expense", "invested_capital",
                 "research_and_development", "stock_based_compensation"],
                end_date,
                period="annual",
                limit=7,
                api_key=api_key,
            )
        except Exception:
            progress.update_status(agent_id, ticker, "Failed to fetch line items — skipping")
            dcf_range[ticker] = {}
            continue

        series, reported_currency = _extract_annual_series(line_items)
        if len(series) < _MIN_HISTORY_YEARS:
            progress.update_status(agent_id, ticker,
                                   f"Insufficient history ({len(series)} yr) — skipping")
            dcf_range[ticker] = {}
            continue

        # ── Anchor values from most recent year ──────────────────────────
        most_recent = series[-1]
        revenue_base = most_recent["revenue"]
        shares       = most_recent["shares_outstanding"]
        leverage     = most_recent["debt_to_equity"] or 0.0
        net_debt     = most_recent["net_debt"] or 0.0

        # ── Fetch latest price for trailing P/E (Deep Value Recovery) ────
        # Also captures market_cap for the WACC credit-spread overlay.
        _trailing_pe: float | None = None
        _market_cap: float | None = None
        try:
            _latest_prices = get_prices(ticker, end_date, end_date, api_key=api_key)
            if _latest_prices:
                _p = _latest_prices[-1]
                _close = float(_p.close) if hasattr(_p, "close") else float(_p.get("close", 0))
                _ni = most_recent.get("net_income")
                if _close > 0 and shares and shares > 0:
                    _market_cap = _close * shares
                if _close > 0 and _ni and shares and shares > 0 and _ni > 0:
                    _trailing_pe = _close / (_ni / shares)
                    most_recent["price_to_earnings_ratio"] = _trailing_pe
        except Exception:
            pass

        if not shares or shares <= 0:
            if len(series) >= 2 and series[-2]["shares_outstanding"]:
                shares = series[-2]["shares_outstanding"]
            else:
                progress.update_status(agent_id, ticker, "No shares data — skipping")
                dcf_range[ticker] = {}
                continue

        # ── FX Conversion (ADR / cross-listed tickers) ───────────────────
        # Some tickers trade on US exchanges (ADRs or direct listings) but
        # report financials in their home currency (e.g. BABA/BIDU in CNY,
        # SHOP in CAD, ASML in EUR).  The DCF engine assumes all monetary
        # inputs are in USD.  Convert the full series in-place before any
        # further computation so that intrinsic values are output in USD.
        #
        # Shares outstanding is a count — NOT converted.
        # Ratio-based fields (debt_to_equity) are dimensionless — NOT converted.
        # Per-share fields (dividends_per_share, book_value_per_share) are
        # converted because they're denominated in the home currency.
        fx_rate    = 1.0
        fx_note    = ""
        revenue_base_raw_ccy = None  # Set only for non-USD tickers (Change 9)
        _FX_MONETARY = {
            "revenue", "free_cash_flow", "fcf_owner_earnings",
            "net_debt", "ebitda", "net_income",
            "total_assets", "total_equity", "ebit", "interest_expense",
            "invested_capital", "capital_expenditure",
            "research_and_development", "stock_based_compensation",
            "dividends_per_share", "book_value_per_share",
        }
        # For HK-listed tickers (prices quoted in HKD), convert financials directly
        # into HKD so that all per-share outputs are already in HKD.
        # This eliminates the two-step CNY→USD→HKD chain (and its rounding noise).
        # The USD→HKD tail conversion further below is skipped for HK tickers.
        _is_hk = _is_hk_ticker(ticker)
        _target_ccy = "HKD" if _is_hk else "USD"

        if reported_currency != _target_ccy:
            fx_rate = get_fx_rate(reported_currency, _target_ccy, api_key)
            if fx_rate != 1.0 and fx_rate > 0:
                for row in series:
                    for field in _FX_MONETARY:
                        if row.get(field) is not None:
                            row[field] = row[field] * fx_rate
                # Re-derive anchored scalars after conversion
                most_recent = series[-1]
                revenue_base = most_recent["revenue"]
                net_debt     = most_recent["net_debt"] or 0.0
                # Change 9: store the pre-FX (raw currency) revenue for debugging
                revenue_base_raw_ccy = revenue_base / fx_rate if fx_rate else revenue_base
                _ccy_label = f"{reported_currency}→{_target_ccy}"
                fx_note = (
                    f"Financials reported in {reported_currency}; "
                    f"converted to {_target_ccy} at {fx_rate:.6f} {_ccy_label}"
                )
                progress.update_status(
                    agent_id, ticker,
                    f"FX: {_ccy_label} @ {fx_rate:.4f} | "
                    f"rev_{_target_ccy.lower()} ${revenue_base/1e9:.2f}B"
                )
            else:
                fx_note = (
                    f"WARNING: FX rate for {reported_currency}→{_target_ccy} unavailable "
                    f"(returned {fx_rate}); values may be in {reported_currency}"
                )
                progress.update_status(
                    agent_id, ticker,
                    f"FX rate unavailable for {reported_currency}→{_target_ccy} — values unscaled"
                )

        # ── Ticker-level forward flags (seed; all subsequent blocks append) ──
        ticker_forward_flags: list[str] = []

        # ── Normalized (cycle-adjusted) earnings for P/E (norm), EV/EBITDA (norm) ──
        # Damodaran-style: mean(field / revenue) over last 5 yrs × current revenue.
        # For cyclicals this prevents peak-year P/E multiples from producing a
        # trough-earnings IV (and vice versa). For stable businesses the delta
        # is small — safe to apply uniformly. Stored on most_recent so the method
        # branches pick them up automatically; None when insufficient history.
        _norm_ni     = _normalized_earnings(series, "net_income", window=5)
        _norm_ebitda = _normalized_earnings(series, "ebitda",     window=5)
        _norm_ebit   = _normalized_earnings(series, "ebit",       window=5)
        most_recent["normalized_net_income"] = _norm_ni
        most_recent["normalized_ebitda"]     = _norm_ebitda
        most_recent["normalized_ebit"]       = _norm_ebit
        # Audit flag when normalization materially moves earnings (>15% delta)
        _cur_ni = most_recent.get("net_income")
        if _norm_ni is not None and _cur_ni and _cur_ni > 0:
            _delta_pct = (_norm_ni - _cur_ni) / _cur_ni
            if abs(_delta_pct) > 0.15:
                ticker_forward_flags.append(
                    f"Normalized NI: TTM ${_cur_ni/1e9:.2f}B → 5y-cycle "
                    f"${_norm_ni/1e9:.2f}B ({_delta_pct:+.0%}) — "
                    f"P/E (norm) will use normalized figure"
                )

        # ── Product-segment revenue breakdown (Feature 3) ───────────────
        # FMP /stable/revenue-product-segmentation. Paid-tier endpoint — a
        # free-tier key returns [] and the downstream SOTP method just skips.
        # Segments arrive in reported currency; apply the same FX multiplier
        # used on the historical series so multiples are applied in the target
        # currency. Only the MOST-RECENT year's segments feed SOTP.
        try:
            product_segments = get_revenue_product_segmentation(
                ticker, end_date, period="annual", api_key=api_key,
            )
        except Exception:
            product_segments = []
        if product_segments:
            _latest_seg = product_segments[-1]
            _fxm = fx_rate if (fx_rate and fx_rate > 0) else 1.0
            _converted = {k: v * _fxm for k, v in _latest_seg["segments"].items()}
            most_recent["segment_breakdown"] = _converted
            # Build top-5 mix string for the audit flag
            _total = sum(_converted.values()) or 1.0
            _mix = sorted(_converted.items(), key=lambda x: -x[1])[:5]
            _mix_str = ", ".join(f"{n} {v/_total:.0%}" for n, v in _mix)
            ticker_forward_flags.append(
                f"Product segments ({_latest_seg['period_end']}): {_mix_str}"
            )

            # Attach segment scenarios from deep research (feeds probabilistic
            # SOTP 12m method). Missing ticker → empty dict → method falls back
            # to flat growth = growth_base (attached further below).
            _ticker_scenarios = segment_scenarios_all.get(ticker, {})
            if _ticker_scenarios:
                most_recent["segment_scenarios"] = _ticker_scenarios
                # One-line audit: first scenario per segment with its evidence
                _scen_mix = []
                for _seg_name, _block in list(_ticker_scenarios.items())[:4]:
                    _scens = _block.get("scenarios", [])
                    if _scens:
                        _rates = [s.get("rate", 0.0) for s in _scens]
                        _rate_lo = min(_rates)
                        _rate_hi = max(_rates)
                        _scen_mix.append(
                            f"{_seg_name} [{_rate_lo:+.0%}→{_rate_hi:+.0%}]"
                        )
                if _scen_mix:
                    ticker_forward_flags.append(
                        f"Segment scenarios ({len(_ticker_scenarios)} segments, "
                        f"conf={_block.get('confidence','?')}): " + ", ".join(_scen_mix)
                    )

        # ── Attach Biopharma pipeline assets for rNPV method ────────────────
        # Deep research extractor produces a list of {name, phase, peak_sales_usd,
        # launch_year, indication} per ticker. _compute_method_value reads this
        # from most_recent["pipeline_assets"] when dispatching the rNPV method.
        # Absent assets → rNPV returns None → blended IV falls to DCF proxy.
        _ticker_pipeline = pipeline_assets_all.get(ticker) or []
        if _ticker_pipeline:
            most_recent["pipeline_assets"] = _ticker_pipeline
            # Pipeline-composition audit: phase mix + top assets by peak_sales.
            # Full per-asset rNPV table surfaces later from _compute_rnpv audit.
            from src.data.sector_profiles import normalize_phase as _norm_phase
            _phase_mix: dict[str, int] = {}
            for _a in _ticker_pipeline:
                _p = _norm_phase(_a.get("phase"))
                _phase_mix[_p] = _phase_mix.get(_p, 0) + 1
            _phase_str = ", ".join(
                f"{k.replace('phase_', 'Ph')}={v}" for k, v in sorted(_phase_mix.items())
            )
            _top_assets = sorted(
                _ticker_pipeline, key=lambda x: x.get("peak_sales_usd", 0), reverse=True
            )[:3]
            _top_str = "; ".join(
                f"{a.get('name', '?')} ({_norm_phase(a.get('phase'))}, "
                f"${a.get('peak_sales_usd', 0)/1e9:.1f}B peak)"
                for a in _top_assets
            )
            ticker_forward_flags.append(
                f"Pipeline assets ({len(_ticker_pipeline)}): {_phase_str} | "
                f"Top: {_top_str}"
            )

        # ── FCF margin (SBC-adjusted / owner-earnings) ──────────────────
        # Reported FCF treats stock-based comp as non-cash (adds it back to
        # OCF). For valuation we prefer owner-earnings FCF = reported FCF −
        # |SBC|, because SBC is a real dilution cost to shareholders. The
        # adjustment flows into every DCF-family method and the FCF-Yield
        # method via fcf_margin_base / most_recent["fcf_owner_earnings"].
        # Requires SBC disclosed in ≥3 of the last 5 years to be trusted;
        # otherwise we fall back to the reported FCF margin unchanged.
        fcf_margin_reported = _mean_fcf_margin(series) or 0.0
        fcf_margin_owner = _mean_fcf_margin(series, field="fcf_owner_earnings")
        _sbc_years = sum(
            1 for row in series[-5:]
            if row.get("stock_based_compensation") is not None
        )
        if fcf_margin_owner is not None and _sbc_years >= 3:
            fcf_margin_base = fcf_margin_owner
            _drag_bps = int(round((fcf_margin_reported - fcf_margin_owner) * 10000))
            if _drag_bps > 0:
                ticker_forward_flags.append(
                    f"SBC drag: FCF margin {fcf_margin_reported:.1%} → "
                    f"{fcf_margin_owner:.1%} (−{_drag_bps} bps, "
                    f"{_sbc_years}/5 yr SBC data)"
                )
        else:
            fcf_margin_base = fcf_margin_reported

        # ── Analyst estimates (fetched eagerly — cached) ──────────────────
        # Pulled BEFORE the growth waterfall so dispersion bands are available
        # for scenario construction even when guidance or historical drives the
        # point estimate. Feeds both growth_base (analyst revenue_avg) AND the
        # bear/base/bull band scenarios + Forward P/E / Forward EV/EBITDA methods.
        try:
            estimates = get_analyst_estimates(
                ticker, end_date, period="annual", limit=3, api_key=api_key
            )
        except Exception:
            estimates = []

        # ── Growth rate — priority: guided > analyst > historical ────────
        data_source = "historical"
        guidance = mgmt_guidance_all.get(ticker, {})

        growth_base = _guided_growth(guidance, revenue_base=revenue_base)
        if growth_base is not None:
            data_source = "guided"
        else:
            growth_base = _analyst_revenue_growth(estimates, revenue_base)
            if growth_base is not None:
                data_source = "analyst"

        if growth_base is None:
            growth_base = _historical_cagr(series, revenue_base=revenue_base)
            if growth_base is None:
                progress.update_status(agent_id, ticker,
                                       "Cannot derive growth rate — skipping")
                dcf_range[ticker] = {}
                continue

        # ── Consensus dispersion bands (Feature 1a) ─────────────────────
        # Derives asymmetric bear / base / bull growth rates from analyst
        # revenue low/avg/high when ≥3 analysts cover the name. Replaces the
        # symmetric ±45% multiplier used when dispersion is unavailable.
        # Per-ticker value — does not vary across the three scenarios.
        _analyst_bands = _analyst_growth_bands(estimates, revenue_base)
        if _analyst_bands is not None:
            ticker_forward_flags.append(
                f"Analyst dispersion ({_analyst_bands['analyst_count']} analysts): "
                f"bear {_analyst_bands['bear']:+.1%} / "
                f"base {_analyst_bands['base']:+.1%} / "
                f"bull {_analyst_bands['bull']:+.1%}"
            )

        # ── Forward consensus point estimates (Feature 1b inputs) ──────
        # Absolute EPS / EBITDA consensus by scenario, used by Forward P/E
        # and Forward EV/EBITDA methods downstream. None-safe: methods skip
        # when the particular scenario's value is missing. FMP returns these
        # in reported currency, so we apply the same FX multiplier used on
        # the historical series to keep everything in the target currency.
        forward_consensus = None
        if estimates:
            _fwd = estimates[0]
            _fxm = fx_rate if (fx_rate and fx_rate > 0) else 1.0
            def _fx(v):
                return (v * _fxm) if v is not None else None
            forward_consensus = {
                "eps":    {"bear": _fx(_safe(getattr(_fwd, "eps_low",  None))),
                           "base": _fx(_safe(getattr(_fwd, "eps_avg",  None))),
                           "bull": _fx(_safe(getattr(_fwd, "eps_high", None)))},
                "ebitda": {"bear": _fx(_safe(getattr(_fwd, "ebitda_low",  None))),
                           "base": _fx(_safe(getattr(_fwd, "ebitda_avg",  None))),
                           "bull": _fx(_safe(getattr(_fwd, "ebitda_high", None)))},
                "analyst_count_eps":     getattr(_fwd, "analyst_count_eps",     None),
                "analyst_count_revenue": getattr(_fwd, "analyst_count_revenue", None),
                "period_end":            getattr(_fwd, "period_end",            ""),
            }

        # ── Deep-research DCF calibration (from sections 2D + 2F) ────────
        # Applied AFTER the guided/analyst/historical waterfall so it acts as a
        # directional nudge, not an override.  Blended at 30% weight to avoid
        # over-indexing on a single LLM parse of qualitative text.
        dcf_cal = dcf_calibration_all.get(ticker, {})
        _cal_adj = dcf_cal.get("growth_rate_adj")
        if _cal_adj is not None and data_source == "historical":
            # Apply when no hard guidance or analyst estimate overrides.
            # Weight: 50% of the LLM signal (e.g. +0.08 → +0.04 applied).
            # Increased from 30% to 50% to better capture secular growth
            # inflections (AI supercycle, grid upgrade, GLP-1 ramp) that the
            # deep research identifies but the historical CAGR misses.
            _CAL_WEIGHT = 0.50
            growth_base = growth_base + float(_cal_adj) * _CAL_WEIGHT
            progress.update_status(
                agent_id, ticker,
                f"Growth nudge from deep research: {_cal_adj:+.3f} × {_CAL_WEIGHT:.0%} = "
                f"{float(_cal_adj)*_CAL_WEIGHT:+.3f} → adjusted base={growth_base:.3f}"
            )

        # ── Margin guidance ───────────────────────────────────────────────
        # Deep-research margin direction is used as a fallback when mgmt guidance
        # does not specify a direction.
        _cal_margin = dcf_cal.get("margin_direction")
        guided_margin_direction = (
            guidance.get("margin_direction")
            or (_cal_margin if _cal_margin else "stable")
        )
        guidance_margin_adj = _GUIDANCE_MARGIN_DELTA.get(
            guided_margin_direction or "stable", 0.0
        )

        _risk_appetite = macro_regime.get("risk_appetite", "neutral")

        # ── Industry profile auto-classification (must precede WACC) ─────
        # Profile is needed to select the correct Energy sub-type WACC base.
        revenue_cagr = _historical_cagr(series) or growth_base
        is_pre_revenue = (revenue_base < 10_000_000)  # <$10M revenue → treat as pre-revenue
        profile_name, profile_data = get_valuation_profile(
            sector, revenue_cagr, fcf_margin_base, leverage, is_pre_revenue,
            revenue_base=revenue_base,
        )

        # ── Guardrail 4: ticker-level profile override ─────────────────────
        # TICKER_SECTOR_LOOKUP can specify a hard profile override (second field).
        # When set, it takes PRIORITY over classify_valuation_profile() — used for
        # companies that can't be differentiated by financials alone (e.g.
        # cybersecurity firms look like SaaS but need different TGR/methods).
        _lookup_sector, _lookup_profile = get_wacc_profile_for_ticker(ticker)
        if _lookup_profile and _lookup_profile != profile_name:
            from src.data.sector_profiles import INDUSTRY_VALUATION_PROFILES
            _override_data = INDUSTRY_VALUATION_PROFILES.get(sector, {}).get(_lookup_profile, {})
            if _override_data:
                profile_name = _lookup_profile
                profile_data = _override_data
                progress.update_status(
                    agent_id, ticker,
                    f"Profile override from TICKER_SECTOR_LOOKUP: {_lookup_profile}"
                )
        if not profile_name:
            _log.warning(
                "[DCF] %s: No valuation profile found for sector='%s'. "
                "All methods will fall back to DCF. "
                "Consider adding '%s' to TICKER_SECTOR_LOOKUP.", ticker, sector, ticker
            )
            progress.update_status(
                agent_id, ticker,
                f"No valuation profile for sector='{sector}' — DCF only"
            )

        # ── WACC (hybrid: Damodaran sector base + live credit overlay) ───
        # The sector base WACC preserves all existing calibration (Damodaran
        # Jan 2026, profile sub-types, HK CRP, macro regime, leverage premium).
        # On top, a cyclical overlay uses FRED's live ICE BofA OAS to flex
        # cost of debt by current credit conditions — tight credit shrinks WACC
        # modestly, stressed credit widens it. The overlay falls to zero when
        # FRED is unreachable or when market_cap / net_debt are unavailable,
        # so WACC collapses to the legacy sector value as a safe no-op.
        _ebit_v = most_recent.get("ebit")
        _int_v  = most_recent.get("interest_expense")
        if _int_v and _int_v > 0 and _ebit_v is not None:
            _coverage = _ebit_v / _int_v
        else:
            _coverage = None  # no interest expense → rated AAA
        try:
            from src.data.sector_profiles import compute_wacc_hybrid as _compute_wacc_hybrid
            _wacc_info = _compute_wacc_hybrid(
                sector=sector,
                leverage=leverage,
                macro_regime=_risk_appetite,
                profile=profile_name or "",
                is_hk=_is_hk,
                interest_coverage=_coverage,
                net_debt=net_debt,
                market_cap=_market_cap,
            )
            wacc = _wacc_info["wacc"]
            ticker_forward_flags.append(_wacc_info["audit"])
        except Exception as _wacc_exc:  # noqa: BLE001 — never block DCF on audit
            _log.warning("[DCF] %s: hybrid WACC failed, using sector base: %s",
                         ticker, _wacc_exc)
            wacc = get_wacc_for_exchange(
                sector, leverage, macro_regime=_risk_appetite,
                profile=profile_name, is_hk=_is_hk,
            )

        # Deep-research risk_flag → WACC loading (+50bps HIGH, +25bps MEDIUM)
        _risk_flag = dcf_cal.get("risk_flag", "MEDIUM")
        _wacc_loading = {"HIGH": 0.0050, "MEDIUM": 0.0025, "LOW": 0.0}.get(_risk_flag, 0.0025)
        if _wacc_loading:
            wacc = wacc + _wacc_loading
            progress.update_status(
                agent_id, ticker,
                f"WACC loading from deep research risk_flag={_risk_flag}: "
                f"+{_wacc_loading*100:.0f}bps → WACC={wacc:.3f}"
            )

        # ── Change 6: Country Risk Premium (CRP) for non-USD-reporting US-listed tickers ──
        # Source: Damodaran Jan 2026 country risk premiums.
        # Applied when the company reports in a non-USD currency (ADR or cross-listing)
        # reflecting political/regulatory/FX tail risk not captured by the sector WACC.
        _CRP_BY_CURRENCY = {
            "CNY":  0.018,  # China (mainland) — VIE risk, regulatory, capital controls
            "HKD":  0.010,  # Hong Kong — lower than CNY; separate legal system
            "BRL":  0.022,  # Brazil — fiscal policy risk, FX volatility
            "INR":  0.014,  # India — governance improving; lower than EM median
            "MXN":  0.018,  # Mexico — AMLO/Sheinbaum policy uncertainty
            "ZAR":  0.025,  # South Africa — load-shedding, governance risk
            "KRW":  0.007,  # South Korea — high-quality governance; small premium
            "IDR":  0.020,  # Indonesia — commodity, EM
            "TRY":  0.040,  # Turkey — currency and political risk
            "RUB":  0.080,  # Russia — sanctions; use only in non-sanction context
        }
        _crp = _CRP_BY_CURRENCY.get(reported_currency.upper(), 0.0)
        if _crp > 0:
            wacc = round(wacc + _crp, 4)
            fx_note = (fx_note or "") + (
                f" | CRP +{_crp:.1%} added for {reported_currency} jurisdiction risk "
                f"(Damodaran 2026 country risk premium)."
            )
            progress.update_status(
                agent_id, ticker,
                f"CRP +{_crp:.1%} for {reported_currency} → WACC={wacc:.3f}"
            )

        # ── Contracted-revenue WACC discount ────────────────────────────────
        # For Merchant Power / IPP companies with significant PPA or contracted
        # revenue (detected via deep research / industry brief keywords), apply a
        # -125 bps discount. This shifts WACC closer to IPP/Regulated profile,
        # reflecting lower effective cash-flow risk from long-term contracts.
        _CONTRACTED_DISCOUNT = -0.0125  # -125 bps
        _CONTRACTED_KWS = ["ppa", "power purchase agreement", "behind-the-meter", "contracted revenue",
                           "offtake agreement", "long-term contract", "nuclear ppa", "hyperscaler ppa",
                           "capacity auction", "capacity payment", "tolling agreement"]
        if sector == "Energy" and profile_name in ("Merchant Power", "IPP"):
            _research_text = (
                (state["data"].get("deep_research", "") or "") + " " +
                (state["data"].get("industry_brief", "") or "")
            ).lower()
            _has_contracted = any(kw in _research_text for kw in _CONTRACTED_KWS)
            if _has_contracted:
                wacc = round(wacc + _CONTRACTED_DISCOUNT, 4)
                progress.update_status(
                    agent_id, ticker,
                    f"Contracted-revenue discount: {_CONTRACTED_DISCOUNT*100:+.0f}bps "
                    f"(PPA/contracted keywords found) → WACC={wacc:.3f}"
                )

        # P1.1 — extract anchor method and rationale for PDF display (§6 Step 4)
        _anchor_method = "DCF"  # fallback
        _profile_rationale = ""
        if profile_data:
            for _m in profile_data.get("methods", []):
                if _m.get("anchor"):
                    _anchor_method = _m["name"]
                    break
            _profile_rationale = profile_data.get("rationale", "")

        # P1.2 — cache most_recent EBITDA for accurate 12m PT computation
        # Using historical EBITDA × (1+g) is far more accurate than FCF / 0.65
        _hist_ebitda = most_recent.get("ebitda")

        progress.update_status(
            agent_id, ticker,
            f"Profile: {profile_name} | Anchor: {_anchor_method} | WACC={wacc:.1%} | g={growth_base:.1%} | C_macro={c_macro:+.2f}"
        )

        # ── Revenue-scaled growth cap (Fix 2) ────────────────────────────
        # Historical CAGRs from a company's high-growth startup phase routinely
        # overstate the sustainable forward growth rate once revenue scale is large.
        # A $10B revenue company cannot sustain 50%+ annual growth; applying it for
        # 10 years produces terminal revenues larger than global GDP — mechanically
        # possible but economically nonsensical.
        #
        # Caps are calibrated to Damodaran's sector growth databases:
        #   > $10B : max base 15% (mega-cap platform  e.g. MSFT, GOOGL late-stage)
        #   > $3B  : max base 22% (large growth-stage e.g. SNOW, DDOG at $3–10B)
        #   > $1B  : max base 30% (mid-stage growers)
        #   ≤ $1B  : no additional cap — small-cap hyper-growth is legitimate
        #
        # Caps are applied to growth_base BEFORE scenario multipliers so that
        # bear/base/bull still produce differentiated values (0.55/1.00/1.50 × capped base).
        if revenue_base >= 10_000_000_000:
            _growth_base_cap = 0.15
        elif revenue_base >= 3_000_000_000:
            _growth_base_cap = 0.22
        elif revenue_base >= 1_000_000_000:
            _growth_base_cap = 0.30
        else:
            _growth_base_cap = 1.0   # no additional cap for sub-$1B companies

        growth_base_capped = min(growth_base, _growth_base_cap)
        if growth_base_capped < growth_base:
            progress.update_status(
                agent_id, ticker,
                f"Growth cap applied: {growth_base:.1%} → {growth_base_capped:.1%} "
                f"(revenue ${revenue_base/1e9:.1f}B exceeds ${_growth_base_cap:.0%} tier)"
            )
            growth_base = growth_base_capped

        # ── Run three scenarios ───────────────────────────────────────────
        scenario_results: dict[str, dict] = {}
        _base_proj_rows: list[dict] = []
        _base_pv_fcf_per_share: float = 0.0
        _base_pv_tv_per_share: float = 0.0

        for scenario in ("bear", "base", "bull"):
            # Prefer analyst-dispersion-based growth when available (Feature 1a).
            # Falls back to symmetric multiplier when no analyst coverage / FMP
            # doesn't return low/high for this name.
            if _analyst_bands is not None:
                g = _analyst_bands[scenario]
            else:
                g = growth_base * _GROWTH_MULT[scenario]
            g = max(min(g, 1.0), -0.30)

            md = _MARGIN_DELTA_PER_YEAR[scenario]
            if scenario != "bear":
                md += guidance_margin_adj

            tgr = tgr_table.get(scenario, _DEFAULT_TGR[scenario])

            # ── Forward Gate B: ROIC < WACC → TGR = 0 ────────────────────
            forward_flags: list[str] = list(ticker_forward_flags)
            ebit_val = most_recent.get("ebit")
            ic_val = most_recent.get("invested_capital")
            if ebit_val and ic_val and ic_val > 0:
                nopat = ebit_val * (1 - _EFFECTIVE_TAX_RATE)
                forward_roic = nopat / ic_val
                if forward_roic < wacc:
                    tgr = 0.0
                    forward_flags.append(
                        f"Gate B: Forward ROIC ({forward_roic:.1%}) < WACC ({wacc:.1%}) → TGR set to 0"
                    )

            # Safety: WACC must exceed TGR
            if wacc <= tgr:
                tgr = wacc - 0.005

            # ── Core DCF projection ───────────────────────────────────────
            iv_dcf, pv_fcf, pv_tv, _proj_rows = _project_dcf(
                revenue_base=revenue_base,
                fcf_margin_base=fcf_margin_base,
                growth_rate=g,
                margin_delta_per_year=md,
                wacc=wacc,
                tgr=tgr,
                fcf_floor=fcf_floor,
                net_debt=net_debt,
                shares=shares,
            )
            if scenario == "base":
                _base_proj_rows = _proj_rows
                _base_pv_fcf_per_share = pv_fcf
                _base_pv_tv_per_share  = pv_tv

            # Terminal value fraction (for Forward Gate A check)
            total_iv = pv_fcf + pv_tv
            tv_fraction = (pv_tv / total_iv) if total_iv > 0 else 0.0

            # ── Build per-method value map ────────────────────────────────
            method_values: dict[str, Optional[float]] = {"DCF": iv_dcf}
            if profile_data:
                methods_to_compute = set()
                for m in profile_data.get("methods", []):
                    if m.get("implementable", True):
                        methods_to_compute.add(m["name"])
                    elif "proxy" in m:
                        methods_to_compute.add(m["proxy"])

                # ── Growth premium: PEG-inspired multiple adjustment ─────
                # Scale relative-value multiples based on company growth vs
                # sector average.  Sensitivity=0.30 means a company growing
                # 2x sector avg gets ~1.30x the base multiple.
                _GROWTH_SENSITIVITY = 0.30
                _peer_for_gp = get_sector_peer_multiples(sector, is_hk=_is_hk, profile_name=profile_name)
                _sector_g_avg = _peer_for_gp.get("growth_avg", 0.08)
                if _sector_g_avg > 0.005:
                    _gp_raw = 1.0 + _GROWTH_SENSITIVITY * (g - _sector_g_avg) / _sector_g_avg
                    growth_premium = max(0.60, min(2.50, _gp_raw))
                else:
                    growth_premium = 1.0

                # ── SBC Dilution Override ─────────────────────────────────
                # If stock-based compensation > 20% of revenue, the P/E
                # multiple is structurally inflated by GAAP earnings that
                # don't reflect real dilution cost.  Discount peer P/E by 15%.
                # Common in EV / tech-heavy consumer (TSLA, RIVN, LCID).
                _sbc_discount = 1.0
                _sbc = most_recent.get("stock_based_compensation")
                if _sbc and revenue_base and revenue_base > 0:
                    _sbc_pct = abs(_sbc) / revenue_base
                    if _sbc_pct > 0.20:
                        _sbc_discount = 0.85
                        forward_flags.append(
                            f"SBC Dilution: SBC/Rev {_sbc_pct:.0%} > 20% → P/E discounted 15%"
                        )

                # ── Deep Value Recovery Alert ─────────────────────────────
                # Safety floor: if current trailing P/E < peer P/E anchor
                # AND company growth > sector growth average, flag as
                # potential deep value recovery (market is mispricing growth).
                _peer_pe = _peer_for_gp.get("pe", 20.0)
                _current_pe = most_recent.get("price_to_earnings_ratio")
                if (_current_pe and _current_pe > 0
                        and _current_pe < _peer_pe
                        and g > _sector_g_avg):
                    forward_flags.append(
                        f"Deep Value Recovery: Current P/E {_current_pe:.1f}x < "
                        f"Peer {_peer_pe:.0f}x while growth {g:.1%} > "
                        f"sector avg {_sector_g_avg:.1%}"
                    )

                for method_name in methods_to_compute:
                    if method_name not in method_values:
                        method_values[method_name] = _compute_method_value(
                            method_name=method_name,
                            most_recent=most_recent,
                            revenue_base=revenue_base,
                            shares=shares,
                            net_debt=net_debt,
                            market_cap=revenue_base * 10,   # rough proxy if not available
                            wacc=wacc,
                            growth_base=g,
                            fcf_margin_base=fcf_margin_base,
                            tgr=tgr,
                            fcf_floor=fcf_floor,
                            sector=sector,
                            scenario=scenario,
                            reported_currency=reported_currency,
                            is_hk=_is_hk,
                            growth_premium=growth_premium,
                            sbc_pe_discount=_sbc_discount,
                            profile_name=profile_name,
                            forward_consensus=forward_consensus,
                        )

                # ── Shadow-compute Forward P/E, Forward EV/EBITDA (Feature 1b)
                # and SOTP segments (Feature 3). Values surface in the per-method
                # table for transparency; they are only included in the blended
                # IV when the profile explicitly references them.
                _shadow_methods: list[str] = []
                if forward_consensus is not None:
                    _shadow_methods.extend(["Forward P/E", "Forward EV/EBITDA"])
                if most_recent.get("segment_breakdown"):
                    _shadow_methods.append("SOTP (segments)")
                    # Always shadow-compute probabilistic SOTP too; when the
                    # deep research didn't produce scenarios, the method falls
                    # back to flat growth = growth_base (attached below).
                    most_recent["_sotp_fallback_growth"] = g
                    _shadow_methods.append("SOTP 12m (probabilistic)")
                for _shadow_name in _shadow_methods:
                    if _shadow_name not in method_values:
                        method_values[_shadow_name] = _compute_method_value(
                            method_name=_shadow_name,
                            most_recent=most_recent,
                            revenue_base=revenue_base,
                            shares=shares,
                            net_debt=net_debt,
                            market_cap=revenue_base * 10,
                            wacc=wacc,
                            growth_base=g,
                            fcf_margin_base=fcf_margin_base,
                            tgr=tgr,
                            fcf_floor=fcf_floor,
                            sector=sector,
                            scenario=scenario,
                            reported_currency=reported_currency,
                            is_hk=_is_hk,
                            growth_premium=growth_premium,
                            sbc_pe_discount=_sbc_discount,
                            profile_name=profile_name,
                            forward_consensus=forward_consensus,
                        )

                # Check excluded methods are not used
                excluded = profile_data.get("excluded", [])
                for ex in excluded:
                    method_values.pop(ex, None)

            # ── Blended IV with C_macro and Forward Gate A ─────────────────
            if profile_data and profile_data.get("methods"):
                blended_iv = _blend_methods(
                    profile_methods=profile_data["methods"],
                    method_values=method_values,
                    c_macro=c_macro,
                    forward_flags=forward_flags,
                    dcf_tv_fraction=tv_fraction,
                )
                final_iv = blended_iv if blended_iv is not None else iv_dcf
                methods_used = [m["name"] for m in profile_data["methods"]
                                if method_values.get(m.get("proxy", m["name"])) is not None
                                or method_values.get(m["name"]) is not None]
            else:
                # No profile found — fall back to pure DCF with C_macro scaling
                final_iv = iv_dcf * (1.0 + c_macro)
                methods_used = ["DCF (fallback)"]

            # Store per-method individual IVs for transparent PDF display
            # Each method gets its own bull/base/bear value — "Blended IV" is the weighted sum
            method_iv_table: dict[str, float] = {
                k: round(v, 2) for k, v in method_values.items()
                if v is not None and v > 0
            }
            # Profile weights list for method-weight column in PDF
            profile_weights: list[dict] = (
                [{"name": m["name"], "weight": m["weight"]}
                 for m in profile_data.get("methods", [])]
                if profile_data else []
            )

            # Year-1 projected metrics (for 12m forward-multiple price target)
            if _proj_rows:
                _yr1 = _proj_rows[0]
                yr1_revenue = _yr1.get("revenue", 0.0)
                yr1_fcf     = _yr1.get("fcf", 0.0)
            else:
                yr1_revenue = revenue_base * (1 + g)
                yr1_fcf     = yr1_revenue * fcf_margin_base

            # P1.2 FIX — Year-1 EBITDA: management guidance > historical growth > heuristic.
            # Priority 0: Management guidance EBITDA (from deep research extraction).
            # Priority 1: Historical EBITDA × (1+g) — scenario-specific growth.
            # Priority 2: FCF / 0.65 heuristic — only if no EBITDA data at all.
            _mgmt = mgmt_guidance_all.get(ticker, {}) if mgmt_guidance_all else {}
            _mgmt_ebitda = _mgmt.get("ebitda_guidance_mid")
            _mgmt_revenue = _mgmt.get("revenue_guidance_mid")

            if _mgmt_ebitda and _mgmt_ebitda > 0:
                # Priority 0: management guidance — apply scenario multiplier
                _scenario_mult = {"bear": 0.90, "base": 1.00, "bull": 1.10}[scenario]
                yr1_ebitda_est = _mgmt_ebitda * _scenario_mult
                if scenario == "base":
                    progress.update_status(
                        agent_id, ticker,
                        f"Yr1 EBITDA from mgmt guidance: ${yr1_ebitda_est/1e9:.2f}B"
                    )
            elif _hist_ebitda and _hist_ebitda > 0:
                yr1_ebitda_est = _hist_ebitda * (1 + g)   # scenario growth applied
            elif yr1_fcf and yr1_fcf > 0:
                yr1_ebitda_est = yr1_fcf / 0.65           # fallback heuristic only if no EBITDA
            else:
                yr1_ebitda_est = None

            # ── EBITDA sanity gate ──────────────────────────────────────────
            # EBITDA cannot exceed revenue.  If management guidance regex
            # mis-parsed a revenue figure as EBITDA (e.g. "$100B revenue
            # target" captured as ebitda_guidance_mid), fall back to the
            # historical EBITDA path.  Also cap at 60% of revenue (no
            # sector has sustainable EBITDA margins above ~55%).
            if yr1_ebitda_est and yr1_revenue and yr1_revenue > 0:
                _ebitda_margin_implied = yr1_ebitda_est / yr1_revenue
                if _ebitda_margin_implied > 0.60:
                    _fallback_ebitda = None
                    if _hist_ebitda and _hist_ebitda > 0:
                        _fallback_ebitda = _hist_ebitda * (1 + g)
                    elif yr1_fcf and yr1_fcf > 0:
                        _fallback_ebitda = yr1_fcf / 0.65
                    if scenario == "base":
                        progress.update_status(
                            agent_id, ticker,
                            f"EBITDA sanity gate: ${yr1_ebitda_est/1e9:.1f}B "
                            f"implies {_ebitda_margin_implied:.0%} margin on "
                            f"${yr1_revenue/1e9:.1f}B rev — capped to "
                            f"${(_fallback_ebitda or 0)/1e9:.1f}B"
                        )
                    yr1_ebitda_est = _fallback_ebitda

            # Also override yr1_revenue with guidance if available
            if _mgmt_revenue and _mgmt_revenue > 0:
                _rev_mult = {"bear": 0.95, "base": 1.00, "bull": 1.05}[scenario]
                yr1_revenue = _mgmt_revenue * _rev_mult

            # Year-1 EPS estimate: prefer actual NI margin over FCF margin proxy.
            # FCF margin can significantly understate NI margin (e.g. JNJ: FCF 22%
            # vs NI 28%) because FCF deducts capex while NI does not.
            _ni = most_recent.get("net_income")
            _ni_margin = (_ni / revenue_base) if (_ni and revenue_base and revenue_base > 0) else None
            _eps_margin = _ni_margin if (_ni_margin and 0 < _ni_margin < 0.80) else fcf_margin_base
            yr1_eps_est = (yr1_revenue * _eps_margin / shares) if shares and shares > 0 else None

            scenario_results[scenario] = {
                "intrinsic_value":   round(final_iv, 2),
                "growth_rate":       round(g, 4),
                "fcf_margin_start":  round(fcf_margin_base, 4),
                "margin_delta_per_year": round(md, 4),
                "tgr":               round(tgr, 4),
                "tv_pct":            round(tv_fraction, 4),
                "methods_used":      methods_used,
                "forward_flags":     forward_flags,
                # NEW: per-method transparency fields
                "method_iv_table":   method_iv_table,
                "profile_weights":   profile_weights,
                "yr1_revenue":       round(yr1_revenue, 0) if yr1_revenue else None,
                "yr1_ebitda_est":    round(yr1_ebitda_est, 0) if yr1_ebitda_est else None,
                "yr1_eps_est":       round(yr1_eps_est, 4) if yr1_eps_est else None,
                "methods_count":     len(method_iv_table),
                "growth_premium":    round(growth_premium, 3) if profile_data else 1.0,
            }

        # ── rNPV per-asset audit (Biopharma only) ────────────────────────
        # The base-scenario rNPV audit was stashed on most_recent during the
        # scenario loop. Emit a single multi-line audit flag with per-asset
        # PoS × peak-sales × PV contribution so analysts can trace the
        # blended rNPV back to individual pipeline drugs.
        _rnpv_audit_base = (most_recent.get("_rnpv_audit") or {}).get("base")
        if _rnpv_audit_base and _rnpv_audit_base.get("n_assets"):
            _rnpv_lines = [
                f"rNPV valuation ({_rnpv_audit_base['n_assets']} assets, "
                f"wacc={_rnpv_audit_base['effective_wacc']:.1%}, "
                f"diluted shares {_rnpv_audit_base['shares_diluted']/1e6:.1f}M):"
            ]
            _rnpv_lines.append(
                f"  pipeline_PV=${_rnpv_audit_base['pipeline_pv']/1e9:.2f}B + "
                f"cash=${_rnpv_audit_base['cash']/1e9:.2f}B − "
                f"debt=${_rnpv_audit_base['debt']/1e9:.2f}B − "
                f"fut_R&D_PV=${_rnpv_audit_base['future_rd_pv']/1e9:.2f}B = "
                f"equity=${_rnpv_audit_base['equity_value']/1e9:.2f}B"
            )
            # Per-asset breakdown — top 5 by risk-adjusted PV
            _assets_sorted = sorted(
                _rnpv_audit_base["assets"],
                key=lambda a: a.get("risk_adjusted_pv", 0),
                reverse=True,
            )[:5]
            for _a in _assets_sorted:
                _rnpv_lines.append(
                    f"  • {_a['name']} ({_a['phase']}"
                    + (f", {_a['indication']}" if _a.get("indication") else "")
                    + f"): peak ${_a['peak_sales_usd']/1e9:.1f}B, "
                    f"PoS {_a['effective_pos']:.1%} "
                    f"(ta_mult {_a['ta_multiplier']:.2f}x), "
                    f"launch +{_a['years_to_launch']:.0f}y, "
                    f"rPV ${_a['risk_adjusted_pv']/1e9:.2f}B"
                )
            ticker_forward_flags.append("\n".join(_rnpv_lines))

        # ── Backward Logic Gate (T-1 Year Test) ──────────────────────────
        base_tgr = tgr_table.get("base", _DEFAULT_TGR["base"])
        if wacc <= base_tgr:
            base_tgr = wacc - 0.005
        calibration_error, calibration_note = _run_backward_gate(
            ticker=ticker,
            series=series,
            sector=sector,
            end_date=end_date,
            wacc=wacc,
            tgr=base_tgr,
            fcf_floor=fcf_floor,
            api_key=api_key,
            profile_data=profile_data,
            reported_currency=reported_currency,
        )

        # ── 12m Forward-Multiple Price Target ────────────────────────────────
        # Framework §7: separate from intrinsic value — market pricing via sector multiples
        # For Financials sub-types (banks, GSEs, insurance), use the profile-level entry
        # which carries sector-appropriate multiples (P/E, P/TBV) rather than EV/EBITDA.
        peer = get_sector_peer_multiples(sector, is_hk=_is_hk, profile_name=profile_name)
        _12m_targets: dict[str, Optional[float]] = {}
        # Bank/GSE/financial profiles: EV-based multiples are meaningless because
        # massive balance-sheet liabilities make (EV - net_debt) negative.
        # Use P/E directly for any bank, GSE, or insurance sub-profile.
        _BANK_PROFILES = {
            "Money Center Bank", "Regional Bank", "Mortgage/GSE",
            "Investment Bank", "Insurance", "FinTech", "Asset Manager",
            "Bank / Lending Institution",   # FMP routing label
        }
        _use_pe_only = (profile_name in _BANK_PROFILES) or (sector == "Financials")
        # Growth premium for 12m PT: use base scenario growth rate
        _base_g = scenario_results.get("base", {}).get("growth_rate", growth_base)
        _sector_g_avg_pt = peer.get("growth_avg", 0.08)
        if _sector_g_avg_pt > 0.005:
            _gp_raw_pt = 1.0 + 0.30 * (_base_g - _sector_g_avg_pt) / _sector_g_avg_pt
            _gp_pt = max(0.60, min(2.50, _gp_raw_pt))
        else:
            _gp_pt = 1.0
        for scen_name, _smult in {"bear": 0.75, "base": 1.00, "bull": 1.25}.items():
            _yr1_rev  = scenario_results[scen_name].get("yr1_revenue")
            _yr1_ebit = scenario_results[scen_name].get("yr1_ebitda_est")
            _yr1_eps  = scenario_results[scen_name].get("yr1_eps_est")
            _nd       = net_debt or 0.0
            _pt: Optional[float] = None
            # Change 7: apply ADR haircut for CNY-reporting US-listed companies
            _adr_h = peer.get("cn_adr_haircut", 1.0) if reported_currency == "CNY" else 1.0
            if _use_pe_only:
                # Bank/GSE forward multiple: P/E only — EV approaches don't apply
                if _yr1_eps and _yr1_eps > 0:
                    _pt = _yr1_eps * peer.get("pe", 12.0) * _smult * _adr_h * _gp_pt
            else:
                # Standard non-financial waterfall: EV/EBITDA → EV/Revenue → P/E
                # EBITDA margin gate: only use EV/EBITDA when margin > 10%.
                # Near-zero EBITDA (CRWD $120M on $4.8B rev = 2.5%) produces
                # absurd PTs ($32 instead of $148). Skip to EV/Revenue instead.
                _ebitda_margin_ok = (
                    _yr1_ebit and _yr1_rev and _yr1_rev > 0
                    and _yr1_ebit / _yr1_rev > 0.10
                )
                if _ebitda_margin_ok and _yr1_ebit > 0 and shares and shares > 0:
                    _ev = _yr1_ebit * peer.get("ev_ebitda", 15.0) * _smult * _adr_h * _gp_pt
                    _pt = max((_ev - _nd) / shares, 0.0)
                elif _yr1_rev and _yr1_rev > 0 and shares and shares > 0:
                    _ev = _yr1_rev * peer.get("ev_revenue", 4.0) * _smult * _adr_h * _gp_pt
                    _pt = max((_ev - _nd) / shares, 0.0)
                elif _yr1_eps and _yr1_eps > 0:
                    _pt = _yr1_eps * peer.get("pe", 20.0) * _smult * _adr_h * _gp_pt
            _12m_targets[scen_name] = round(_pt, 2) if _pt else None

        # ── 12m PT vs DCF IV divergence guard ────────────────────────────────
        # If the base 12m PT diverges > 100% from the base DCF IV, the forward-
        # multiple inputs are likely corrupted (e.g. EBITDA mis-parse).  Cap all
        # scenario PTs to 1.5× their corresponding DCF IVs as a safety net.
        _base_iv = scenario_results.get("base", {}).get("intrinsic_value")
        _base_pt = _12m_targets.get("base")
        if _base_iv and _base_iv > 0 and _base_pt and _base_pt > 0:
            _pt_iv_ratio = _base_pt / _base_iv
            if _pt_iv_ratio > 2.0:  # 12m PT more than 2× DCF IV
                progress.update_status(
                    agent_id, ticker,
                    f"12m PT divergence guard: base PT ${_base_pt:.0f} is "
                    f"{_pt_iv_ratio:.1f}x base IV ${_base_iv:.0f} — "
                    f"capping all PTs to 1.5x IV"
                )
                for _sn in ("bear", "base", "bull"):
                    _scen_iv = scenario_results.get(_sn, {}).get("intrinsic_value")
                    if _scen_iv and _scen_iv > 0 and _12m_targets.get(_sn):
                        _12m_targets[_sn] = round(min(
                            _12m_targets[_sn], _scen_iv * 1.5
                        ), 2)

        # ── HK tickers: ensure per-share outputs are in HKD ─────────────────
        # When reported_currency != "HKD" (e.g. CNY), the FX conversion above
        # has already converted all monetary inputs directly to HKD, so the DCF
        # outputs are already in HKD per share.  No second conversion needed.
        #
        # When reported_currency == "HKD" (HK-incorporated companies like HSBC,
        # Sun Hung Kai), inputs were never converted (fx_rate=1.0), so outputs
        # are already in HKD.  Again, no tail conversion needed.
        #
        # Legacy path (reported_currency == "USD", is_hk=True, e.g. CNOOC/AIA):
        # inputs were converted USD → HKD in the block above, so outputs are HKD.
        _output_currency = reported_currency   # stays source ccy unless we convert
        if _is_hk:
            _output_currency = "HKD"
            fx_note = (fx_note or "") + " | Per-share IV & PT in HKD (HKEX prices quoted in HKD)"
            progress.update_status(
                agent_id, ticker,
                f"HK output in HKD | base IV HK${scenario_results['base']['intrinsic_value']:.2f}"
            )

        dcf_range[ticker] = {
            **scenario_results,
            "wacc":               round(wacc, 4),
            "c_macro":            round(c_macro, 4),
            "profile":            profile_name,
            # P1.1: anchor method + rationale for PDF display (§6 Step 4 justification)
            "anchor_method":      _anchor_method,
            "profile_rationale":  _profile_rationale,
            "leverage":           round(leverage, 2),
            "net_debt":           round(net_debt, 0),   # CHECK 3 fix: actual net debt ($), not D/E ratio
            "fcf_floor":          round(fcf_floor, 4),  # CHECK 3 fix: needed by sensitivity recompute
            "shares_outstanding": shares,
            "revenue_base":       revenue_base,
            "fcf_margin_base":    round(fcf_margin_base, 4),
            "data_source":        data_source,
            "calibration_error":  calibration_error,
            "calibration_note":   calibration_note,
            "projection_rows":    _base_proj_rows,
            "pv_fcf_base":        _base_pv_fcf_per_share,
            "pv_tv_base":         _base_pv_tv_per_share,
            # §7 of valuation framework: 12m forward-multiple price targets
            "12m_targets":        _12m_targets,
            # FX metadata — populated when financials are not in USD
            # _output_currency = "HKD" for HK tickers (IV/PT converted USD→HKD);
            # reported_currency remains the original financial statement currency.
            "reported_currency":  _output_currency,
            "source_currency":    reported_currency,   # original statement currency
            "fx_rate":            round(fx_rate, 6),
            "fx_note":            fx_note,
            # Country Risk Premium (Change 6) — 0.0 if USD-reporting
            "crp":                _crp,
            # Change 9: explicit USD revenue base for sensitivity table consistency
            # revenue_base is ALWAYS post-FX USD at this point.
            # revenue_base_raw is the original-currency value (only set for non-USD).
            "revenue_base_usd":   revenue_base,       # Always USD after FX conversion
            "revenue_base_raw":   revenue_base_raw_ccy,  # Original currency; None for USD tickers
        }

        base_iv = scenario_results["base"]["intrinsic_value"]
        cal_tag = " ⚠ CALIBRATION ERROR" if calibration_error else ""
        progress.update_status(
            agent_id, ticker,
            f"IV base ${base_iv:.2f} | profile: {profile_name} | C_macro {c_macro:+.2f} "
            f"| source: {data_source}{cal_tag}"
        )

    state["data"]["dcf_range"] = dcf_range
    return state
