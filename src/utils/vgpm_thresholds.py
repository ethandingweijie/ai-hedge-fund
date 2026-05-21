"""Sector-aware threshold tables for the pipeline-side VGPM scorer.

Consumed by `src/utils/pdf_report.py::_compute_vgpm`. Each sub-score that
takes sector-sensitive raw inputs (revenue growth, FCF margin, P/FCF,
ROIC-WACC spread) has a per-sector band table here so the score
distribution actually differentiates Tech-grade growth from Utility-grade
growth from Bank-grade growth.

Background — Apr-25 grade compression:
  Pre-fix, _compute_vgpm used cross-sector universal bands (e.g. growth
  >30% = A+, regardless of sector). The result: Banks with 8% growth and
  Utilities with 8% growth and Tech with 8% growth all hit the same sub-
  score (58 → B-band). Combined with _score_to_grade's wide B-band span,
  every ticker collapsed to B-/B/B+. This module restores sector signal.

Sector vocabulary matches `_SECTOR_VGPM_CONFIG` in screener_service.py
(plus pipeline-internal aliases like "Tech" → "Technology",
"Biopharma" → "Healthcare").

Public surface:
  resolve_sector(name)                                → canonical name
  score_growth(gr_pct, sector)                        → 0-100
  score_fcf_margin(fm_pct, sector)                    → 0-100
  score_p_fcf(p_fcf, sector)                          → 0-100
  score_roic_spread(spread, sector)                   → 0-100
  SECTOR_GROWTH_BANDS / SECTOR_FCF_MARGIN_BANDS / ... → introspection
"""
from __future__ import annotations

from typing import Optional


# ── Sector alias normalisation ────────────────────────────────────────────
# Maps inbound names (FMP / pipeline-internal / Yahoo-style) to canonical
# keys used in the per-sector band tables below. Keep in sync with
# `_SECTOR_ALIASES` in app/backend/services/screener_service.py.

_ALIASES: dict[str, str] = {
    # FMP / standard naming variants
    "Financials":             "Financial Services",
    "Finance":                "Financial Services",
    "Financial":              "Financial Services",   # observed in pipeline state
    "Consumer Staples":       "Consumer Defensive",
    "Consumer Discretionary": "Consumer Cyclical",
    "Materials":              "Basic Materials",
    "Information Technology": "Technology",
    "Pharmaceuticals":        "Biopharma",            # NEW canonical
    "Biotechnology":          "Biopharma",
    # Telco family — distinct from Communication Services (ad-tech / streaming
    # platforms). Legacy telcos are mature dividend-paying capex-heavy operators
    # closer to Utilities than to Meta/Alphabet.
    "Telecom":                "Telecom",              # was Communication Services
    "Telecommunications":     "Telecom",
    "Telco":                  "Telecom",              # observed in pipeline state
    # Internal pipeline sector names (from sector_profiles.py TICKER_SECTOR_LOOKUP)
    "Tech":                   "Technology",
    "Semiconductor":          "Technology",
    "Biopharma":              "Biopharma",            # NEW canonical (was → Healthcare)
    "HealthcareServices":     "Healthcare",
    "Healthcare":             "Healthcare",
    "Consumer":               "Consumer Cyclical",    # observed in pipeline state — default to broader cyclical bucket
    "RealEstate":             "Real Estate",
    "Banks":                  "Financial Services",
    "Insurance":              "Financial Services",
    "REIT":                   "Real Estate",
    "REITs":                  "Real Estate",
    "Property Trust":         "Real Estate",
}

# 13 canonical sectors (was 11; added Biopharma + Telecom 2026-05-21).
# ALL band tables below MUST cover every entry. The CANONICAL_SECTORS guard
# tests in tests/test_vgpm_sector_bands.py enforce this.
CANONICAL_SECTORS: tuple[str, ...] = (
    "Technology", "Financial Services", "Healthcare", "Biopharma",
    "Utilities", "Energy", "Real Estate", "Consumer Defensive",
    "Consumer Cyclical", "Industrials", "Communication Services",
    "Telecom", "Basic Materials",
)


def resolve_sector(name: str | None) -> str:
    """Normalize an inbound sector name to canonical form.

    Returns "Technology" for unknown sectors (the safe default — same
    behaviour as falling through `_SECTOR_VGPM_CONFIG` to Technology).
    """
    if not name:
        return "Technology"
    s = name.strip()
    canonical = _ALIASES.get(s, s)
    if canonical in CANONICAL_SECTORS:
        return canonical
    return "Technology"


# ── Band tables ──────────────────────────────────────────────────────────
#
# Each table maps a sector to a list of (threshold, score) pairs ORDERED
# BEST FIRST. Score 95 = A+ band, 18 = D band, etc. Match the
# _score_to_grade band scheme so sub-scores combine cleanly.
#
# For HIGHER-IS-BETTER metrics (growth, margin, ROIC), the FIRST threshold
# the value EXCEEDS wins. For LOWER-IS-BETTER (P/FCF), the FIRST threshold
# the value FALLS BELOW wins.


# ── Revenue growth bands (% values, e.g. 30.0 = 30%) — higher better ─────
# Calibrated per sector typical growth distributions.

SECTOR_GROWTH_BANDS: dict[str, list[tuple[float, int]]] = {
    "Technology": [
        (35, 95), (25, 85), (15, 70), (8, 58), (3, 44), (0, 30), (-10, 18), (-25, 8),
    ],
    "Financial Services": [
        # Banks grow slowly — what counts as good is lower than Tech.
        (15, 95), (10, 85), (7, 70), (4, 58), (1, 44), (-2, 30), (-8, 18), (-15, 8),
    ],
    "Healthcare": [
        # Mid-tier — covers HealthcareServices (Managed Care, etc.).
        # Biotech now has its own bucket (Biopharma).
        (20, 95), (14, 85), (10, 70), (6, 58), (2, 44), (0, 30), (-8, 18), (-20, 8),
    ],
    "Biopharma": [
        # Wide tolerance — pre-approval biotech can have lumpy / zero revenue
        # then explosive growth at approval. Mature pharma is steady 5-10%.
        # Bands accept high-growth small-cap AND mature large-cap.
        (30, 95), (20, 85), (12, 70), (5, 58), (0, 44), (-15, 30), (-40, 18), (-80, 8),
    ],
    "Utilities": [
        # Regulated, capex-heavy — even 5% is exceptional.
        (8, 95), (5, 85), (3, 70), (2, 58), (1, 44), (0, 30), (-2, 18), (-6, 8),
    ],
    "Energy": [
        # Commodity-cyclical, wide range. Negative years are common at lows.
        (25, 95), (12, 85), (5, 70), (0, 58), (-8, 44), (-15, 30), (-30, 18), (-50, 8),
    ],
    "Real Estate": [
        # REIT revenue grows with lease escalations + acquisitions; mid-bar.
        (10, 95), (6, 85), (4, 70), (2, 58), (0, 44), (-2, 30), (-8, 18), (-15, 8),
    ],
    "Consumer Defensive": [
        # Mature, low single-digit growth norm. 10%+ is exceptional.
        (12, 95), (8, 85), (5, 70), (3, 58), (1, 44), (0, 30), (-3, 18), (-8, 8),
    ],
    "Consumer Cyclical": [
        # Cycle-sensitive; ranges wider than Defensive.
        (18, 95), (12, 85), (7, 70), (3, 58), (0, 44), (-5, 30), (-15, 18), (-25, 8),
    ],
    "Industrials": [
        # GDP+ growth is normal; cycle adds variance.
        (15, 95), (10, 85), (6, 70), (3, 58), (1, 44), (-2, 30), (-10, 18), (-20, 8),
    ],
    "Communication Services": [
        # Ad-tech / streaming / platforms (legacy telcos now in "Telecom").
        # Higher growth ceiling than Telecom — META/GOOGL-style platforms.
        (18, 95), (12, 85), (6, 70), (3, 58), (0, 44), (-3, 30), (-10, 18), (-20, 8),
    ],
    "Telecom": [
        # Legacy telcos (VZ, T, BT, etc.): mature, capex-heavy, low single-digit
        # growth norm. Closer to Utility than to Communication Services.
        (8, 95), (5, 85), (3, 70), (2, 58), (1, 44), (0, 30), (-3, 18), (-8, 8),
    ],
    "Basic Materials": [
        # Commodity-cyclical similar to Energy.
        (18, 95), (10, 85), (5, 70), (0, 58), (-8, 44), (-15, 30), (-25, 18), (-40, 8),
    ],
}


# ── FCF Margin bands (% values) — higher better ──────────────────────────
# Banks/Insurance have ill-defined FCF margin — handled separately in
# _compute_vgpm before this is called. For the sectors here, calibration
# reflects typical mature-business FCF margins.

SECTOR_FCF_MARGIN_BANDS: dict[str, list[tuple[float, int]]] = {
    "Technology": [
        # SaaS/hyperscalers regularly exceed 25%.
        (25, 95), (18, 82), (12, 68), (6, 54), (0, 34), (-10, 18), (-25, 6),
    ],
    "Financial Services": [
        # FCF margin not meaningful — set conservative bands as a guard
        # in case the pipeline does emit it for non-bank Financials.
        (10, 95), (6, 82), (3, 68), (1, 54), (0, 34), (-5, 18), (-15, 6),
    ],
    "Healthcare": [
        # HealthcareServices (Managed Care): thin margins typical.
        # Biotech-specific tolerance now in "Biopharma".
        (18, 95), (12, 82), (8, 68), (5, 54), (2, 34), (-3, 18), (-10, 6),
    ],
    "Biopharma": [
        # Pre-approval biotech: negative FCF is NORMAL during clinical
        # spend. Mature pharma: 25-35% common. Bands accept both.
        (25, 95), (15, 82), (8, 68), (0, 54), (-15, 34), (-40, 18), (-100, 6),
    ],
    "Utilities": [
        # Capex-heavy — FCF margins typically low single-digit. 8%+ = A.
        (10, 95), (7, 82), (4, 68), (2, 54), (0, 34), (-3, 18), (-8, 6),
    ],
    "Energy": [
        # Wide swings with commodity prices.
        (20, 95), (12, 82), (7, 68), (2, 54), (-3, 34), (-10, 18), (-25, 6),
    ],
    "Real Estate": [
        # REITs have high "FCF" (FFO/AFFO) margins vs revenue.
        (30, 95), (22, 82), (15, 68), (8, 54), (0, 34), (-8, 18), (-20, 6),
    ],
    "Consumer Defensive": [
        # 6-12% typical for staples.
        (12, 95), (8, 82), (6, 68), (4, 54), (0, 34), (-3, 18), (-10, 6),
    ],
    "Consumer Cyclical": [
        # Tighter margins than Defensive.
        (10, 95), (6, 82), (4, 68), (2, 54), (0, 34), (-5, 18), (-15, 6),
    ],
    "Industrials": [
        # 6-12% typical for industrials.
        (12, 95), (8, 82), (5, 68), (3, 54), (0, 34), (-5, 18), (-15, 6),
    ],
    "Communication Services": [
        # Ad-tech / streaming wide range (platforms can sustain 20%+).
        (20, 95), (14, 82), (8, 68), (4, 54), (0, 34), (-8, 18), (-20, 6),
    ],
    "Telecom": [
        # Legacy telcos: 12-18% FCF margins typical, capex pressure caps upside.
        (18, 95), (13, 82), (9, 68), (5, 54), (1, 34), (-3, 18), (-10, 6),
    ],
    "Basic Materials": [
        # Commodity-tied; volatile.
        (15, 95), (10, 82), (6, 68), (2, 54), (-3, 34), (-12, 18), (-25, 6),
    ],
}


# ── P/FCF bands (multiples) — LOWER better ───────────────────────────────

SECTOR_P_FCF_BANDS: dict[str, list[tuple[float, int]]] = {
    "Technology": [
        # Tech accepts higher multiples; 15-25x is normal for quality.
        (12, 95), (20, 80), (30, 62), (45, 40), (70, 18),
    ],
    "Financial Services": [
        # Rarely used for banks; bands set as a guard.
        (8, 95), (12, 80), (18, 62), (28, 40), (45, 18),
    ],
    "Healthcare": [
        # HealthcareServices (Managed Care): mid-multiples.
        (10, 95), (15, 80), (22, 62), (32, 40), (50, 18),
    ],
    "Biopharma": [
        # Wide range — clinical-stage often has no FCF (→ None → 30 score).
        # Mature pharma 10-18x typical; growth pharma at higher multiples.
        (10, 95), (16, 80), (25, 62), (38, 40), (60, 18),
    ],
    "Utilities": [
        # Compressed multiples; 8-15x typical.
        (7, 95), (10, 80), (15, 62), (22, 40), (35, 18),
    ],
    "Energy": [
        # Cyclical valuations; 6-12x at mid-cycle.
        (5, 95), (8, 80), (12, 62), (20, 40), (35, 18),
    ],
    "Real Estate": [
        # REITs trade on FFO/AFFO multiples, but P/FCF still informative.
        (8, 95), (13, 80), (20, 62), (30, 40), (50, 18),
    ],
    "Consumer Defensive": [
        # Quality premium; 15-22x normal.
        (12, 95), (18, 80), (26, 62), (38, 40), (60, 18),
    ],
    "Consumer Cyclical": [
        # Discount to Defensive given cyclicality.
        (8, 95), (13, 80), (20, 62), (30, 40), (50, 18),
    ],
    "Industrials": [
        # Mid-multiple range.
        (9, 95), (14, 80), (22, 62), (32, 40), (50, 18),
    ],
    "Communication Services": [
        # Platforms (META/GOOGL/NFLX): higher multiples accepted.
        (10, 95), (16, 80), (25, 62), (38, 40), (60, 18),
    ],
    "Telecom": [
        # Compressed multiples — mature, capex-heavy, dividend-yielding.
        # Closer to Utility valuation profile.
        (7, 95), (11, 80), (16, 62), (24, 40), (40, 18),
    ],
    "Basic Materials": [
        # Cyclical, compressed.
        (6, 95), (10, 80), (15, 62), (25, 40), (45, 18),
    ],
}


# ── ROIC - WACC spread bands (decimals; 0.10 = +10pp spread) — higher better ─
# Sector-aware because intrinsic ROIC differs:
#   - Tech routinely generates 20%+ spreads (low capital intensity)
#   - Banks operate on thin spreads (regulated capital)
#   - Utilities target spreads near WACC by design

SECTOR_ROIC_SPREAD_BANDS: dict[str, list[tuple[float, int]]] = {
    "Technology": [
        # +13pp+ spread is A+ band (tech routinely generates 15-30pp)
        (0.13, 92), (0.08, 78), (0.02, 62), (-0.03, 42), (-0.15, 20),
    ],
    "Financial Services": [
        # Banks: small spreads are fine (regulated capital structure)
        (0.04, 92), (0.02, 78), (0.0, 62), (-0.02, 42), (-0.08, 20),
    ],
    "Healthcare": [
        # HealthcareServices: thin spreads typical for Managed Care.
        (0.08, 92), (0.04, 78), (0.01, 62), (-0.03, 42), (-0.10, 20),
    ],
    "Biopharma": [
        # Mature pharma: positive spreads expected. Pre-approval / clinical
        # stage: routinely negative — bands tolerate that without flooring.
        (0.10, 92), (0.04, 78), (-0.05, 62), (-0.20, 42), (-0.45, 20),
    ],
    "Utilities": [
        # Spreads near 0 by design (regulated returns at cost of capital).
        # 0pp = passing grade (B+) because that's what regulation targets.
        (0.03, 92), (0.015, 78), (0.0, 62), (-0.02, 42), (-0.06, 20),
    ],
    "Energy": [
        # Mid-cycle ROIC > WACC; cyclical wide range
        (0.12, 92), (0.05, 78), (0.0, 62), (-0.05, 42), (-0.15, 20),
    ],
    "Real Estate": [
        (0.04, 92), (0.02, 78), (0.0, 62), (-0.02, 42), (-0.08, 20),
    ],
    "Consumer Defensive": [
        (0.08, 92), (0.04, 78), (0.0, 62), (-0.04, 42), (-0.12, 20),
    ],
    "Consumer Cyclical": [
        (0.10, 92), (0.05, 78), (0.0, 62), (-0.05, 42), (-0.15, 20),
    ],
    "Industrials": [
        (0.08, 92), (0.04, 78), (0.0, 62), (-0.04, 42), (-0.12, 20),
    ],
    "Communication Services": [
        # Ad-tech platforms generate wide spreads — Meta/Google ROIC > 30%.
        (0.10, 92), (0.05, 78), (0.0, 62), (-0.05, 42), (-0.15, 20),
    ],
    "Telecom": [
        # Capex-heavy regulated — tight positive spread is the realistic ceiling.
        (0.04, 92), (0.02, 78), (0.0, 62), (-0.03, 42), (-0.10, 20),
    ],
    "Basic Materials": [
        (0.08, 92), (0.03, 78), (0.0, 62), (-0.04, 42), (-0.15, 20),
    ],
}


# ── Helpers ─────────────────────────────────────────────────────────────


def _score_higher_better(value: float, bands: list[tuple[float, int]]) -> int:
    """First band whose threshold the value MEETS OR EXCEEDS wins.

    Uses `>=` so exact-match thresholds (e.g. ROIC == WACC = 0.0) hit the
    intended band rather than falling through. If value falls below all
    bands, return the floor score (8 typically).
    """
    for threshold, score in bands:
        if value >= threshold:
            return score
    return 8


def _score_lower_better(value: float, bands: list[tuple[float, int]]) -> int:
    """First band whose threshold the value MEETS OR FALLS BELOW wins."""
    for threshold, score in bands:
        if value <= threshold:
            return score
    return 8


# ── Public score functions ──────────────────────────────────────────────


def score_growth(gr_pct: float, sector: str | None) -> int:
    """Sector-aware Growth sub-score (g1) from revenue growth %."""
    canonical = resolve_sector(sector)
    return _score_higher_better(gr_pct, SECTOR_GROWTH_BANDS[canonical])


def score_fcf_margin(fm_pct: float, sector: str | None) -> int:
    """Sector-aware Profitability sub-score (p1) from FCF margin %."""
    canonical = resolve_sector(sector)
    return _score_higher_better(fm_pct, SECTOR_FCF_MARGIN_BANDS[canonical])


def score_p_fcf(p_fcf: Optional[float], sector: str | None) -> int:
    """Sector-aware Valuation sub-score (v3) from P/FCF multiple.
    None / unavailable → 30 (below-average, matches pre-fix behaviour)."""
    if p_fcf is None or p_fcf <= 0:
        return 30
    canonical = resolve_sector(sector)
    return _score_lower_better(p_fcf, SECTOR_P_FCF_BANDS[canonical])


def score_roic_spread(spread: float, sector: str | None) -> int:
    """Sector-aware Profitability sub-score (p2) from ROIC-WACC spread."""
    canonical = resolve_sector(sector)
    return _score_higher_better(spread, SECTOR_ROIC_SPREAD_BANDS[canonical])
