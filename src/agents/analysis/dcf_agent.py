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
import math
import os
import random
import re
import statistics
import time
from datetime import date, datetime, timedelta
from typing import Optional

from src.graph.state import AgentState
from src.tools.api import (
    search_line_items,
    get_analyst_estimates,
    get_prices,
    get_fx_rate,
    get_revenue_product_segmentation,
    get_price_target_consensus,
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
    # ── Balance-sheet-financial classification (Phase 1.2A) ──
    BALANCE_SHEET_FINANCIAL_PROFILES,
    BALANCE_SHEET_FINANCIAL_CONDITIONAL_PROFILES,
    BALANCE_SHEET_FINANCIAL_UNCLASSIFIED,
    TIER2_EXEMPT_PROFILES,
    # ── Capital-turnover scope for the growth-reinvestment charge ──
    CAPITAL_TURNOVER_PROFILES,
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
from src.agents.industry.sector_prompts import (
    is_biopharma_sector, is_tech_sector, is_bank_sector, is_reit_sector,
)
from src.tools.hk.ticker import is_hk_ticker as _is_hk_ticker
from src.tools.sg.ticker import is_sg_ticker as _is_sg_ticker
from src.utils.progress import progress
from src.utils.api_key import get_api_key_from_state

_log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

_PROJECTION_YEARS = 10
_MIN_HISTORY_YEARS = 2
_DEFAULT_TGR = {"bear": 0.015, "base": 0.025, "bull": 0.035}

# RSU/PSU cash tax withholding, as a fraction of gross SBC expense — used
# when FMP doesn't expose a clean withholding line (same estimator and rate
# as src/research_ideas/sw46/tragic_algebra.py's Method E, calibrated
# against Burry/Cassandra-Unchained's published SBC framework).
_RSU_TAX_WITHHOLDING_RATE = 0.37

# Retries for the line-items fetch — a single FMP hiccup shouldn't blank the
# whole Valuation Methodology panel. Short exponential backoff (1.5s, 3s).
_LINE_ITEMS_MAX_RETRIES = 2
_LINE_ITEMS_RETRY_BASE_DELAY = 1.5

# Last-resort base-case revenue growth by sector, used ONLY when guided
# guidance, analyst estimates, AND historical CAGR all fail to produce a
# growth rate (data_source == "sector_default" — lowest confidence tier,
# flagged as such on the frontend). Near-term growth, not terminal/perpetuity
# growth — deliberately higher than TERMINAL_GROWTH_RATES.
_SECTOR_DEFAULT_GROWTH: dict[str, float] = {
    "Tech":                 0.12,
    "Semiconductor":        0.10,
    "Consumer":             0.06,
    "Biopharma":            0.08,
    "Telco":                0.03,
    "Crypto":               0.15,
    "Energy":               0.04,
    "Financials":           0.06,
    "Industrials":          0.05,
    "RealEstate":           0.04,
    "REIT":                 0.04,
    "Transportation":       0.04,
    "Materials":            0.04,
    "Resources":            0.04,
    "ProfessionalServices": 0.06,
    "HealthcareServices":   0.07,
}
_DEFAULT_SECTOR_DEFAULT_GROWTH = 0.05

# Scenario growth multipliers applied to the derived base growth rate
_GROWTH_MULT = {"bear": 0.55, "base": 1.00, "bull": 1.50}

# Per-year FCF margin delta for each scenario (legacy additive form, kept for
# back-compat with non-Tech paths that still inspect this dict).
_MARGIN_DELTA_PER_YEAR = {"bear": -0.002, "base": 0.0, "bull": 0.002}

# Fix B — multiplicative margin variance across scenarios. Bear compresses the
# base margin by 20%, bull expands by 20%. Applied as:
#   md = fcf_margin_base * (_MARGIN_DELTA_MULT[scenario] - 1.0)
# so that margin_t = fcf_margin_base + md is a widened bear/bull band vs the
# previous ±0.2pp/yr drift (which barely differentiated scenarios for mid-margin
# tech names).
_MARGIN_DELTA_MULT = {"bear": 0.80, "base": 1.00, "bull": 1.20}

# Option III Spec 1 — exponential growth-decay δ by Tech sub-profile.
#   growth_t = growth_{t-1} * (1 - δ)
# Calibrated so Y10 growth lands in the 4-7% mature-SaaS range from a
# 20-30%+ Y1 starting point. Profiles not listed here get no decay (constant
# growth — preserves existing behavior for non-Tech paths).
# Scope: growth archetypes ONLY — profiles where the current growth rate is
# materially above the long-run mean and will empirically decay. Mature SaaS
# (CRM, NOW, ADBE, WDAY, VEEV) and Hyperscaler / Tech Conglomerate (MSFT,
# GOOGL, AMZN, META, ORCL) are intentionally EXCLUDED — their growth is
# already near steady-state, so constant-rate projection remains correct.
_GROWTH_DECAY_DELTA: dict[str, float] = {
    "Growth SaaS":                              0.17,  # 28% → ~5% by Y10
    "Hyper-Growth Platform":                    0.15,  # slower decay (network effects)
    "High-Growth Tech / AI":                    0.12,  # longest S-curve
    "Cybersecurity / Mission-Critical SaaS":    0.15,
}

# Option III Spec 2 — terminal-multiple convergence map. Growth-phase Tech
# profiles converge to their mature equivalent at Y10+ so the terminal value
# does not perpetuate a 22× EV/Rev on a 5% grower. Mature-stage profiles
# resolve to themselves.
_TERMINAL_MULTIPLE_CONVERGENCE: dict[str, str] = {
    "Growth SaaS":                              "Mature SaaS",
    "Hyper-Growth Platform":                    "Mature SaaS",
    "High-Growth Tech / AI":                    "Mature SaaS",
    "Cybersecurity / Mission-Critical SaaS":    "Mature SaaS",
    "Mature SaaS":                              "Mature SaaS",
    "Mature Platform":                          "Mature Platform",
    "Hyperscaler / Tech Conglomerate":          "Hyperscaler / Tech Conglomerate",
    "Early Platform":                           "default",
    "Levered Subscription":                     "Levered Subscription",
}

# Option III Spec 3 — two-stage WACC fade. Apply +250 bps to first 3 years for
# FCF-negative / high-dilution / early-stage Tech profiles, then revert to the
# base WACC. Reflects empirically observed risk premium for pre-scale SaaS.
_EARLY_STAGE_WACC_PREMIUM = 0.025
_EARLY_STAGE_WACC_YEARS   = 3
_EARLY_STAGE_PROFILES: set[str] = {
    "Growth SaaS",
    "Hyper-Growth Platform",
    "High-Growth Tech / AI",
    "Cybersecurity / Mission-Critical SaaS",
}

# ── High-SBC scenario tightening (2026-04-25, Gemini review) ─────────────────
# Profiles where SBC/Revenue typically exceeds 15% — these names face genuine
# scenario risk (multiple compression, growth deceleration, dilution drag) that
# the legacy ±0.55/1.50 growth band and ±0.80/1.20 margin band do not capture.
# Mature SaaS (CRM, ADBE) is intentionally EXCLUDED — SBC ~8-10%, multiples
# already compressed, growth steady — legacy bands stay calibrated for them.
# Hyperscaler / Tech Conglomerate also EXCLUDED — mega-cap dispersion is
# narrower (institutional ownership dampens both upside and downside).
_HIGH_SBC_PROFILES: set[str] = {
    "Growth SaaS",
    "Hyper-Growth Platform",
    "High-Growth Tech / AI",
    "Cybersecurity / Mission-Critical SaaS",
}

# Tightened scenario bands for high-SBC profiles. Calibrated to produce a
# "real" bear case (40% growth → 16%, not 22%) and a disciplined bull case
# (no return to ZIRP-era multiples). Symptom on MDB pre-fix: bear IV $423 on
# spot $253 (i.e. bear is +67% upside — definitionally not a bear).
_GROWTH_MULT_HIGH_SBC       = {"bear": 0.40, "base": 1.00, "bull": 1.30}
_MARGIN_DELTA_MULT_HIGH_SBC = {"bear": 0.65, "base": 1.00, "bull": 1.15}


def _scenario_mults_for_profile(profile_name: str) -> tuple[dict, dict]:
    """Return (growth_mult, margin_delta_mult) tuned to the profile.
    High-SBC profiles get tighter dispersion to model genuine scenario risk;
    everything else uses the legacy bands which are well-calibrated for
    cash-generative / mature-multiple businesses (Banks, REITs, Mature SaaS,
    Consumer staples)."""
    if profile_name in _HIGH_SBC_PROFILES:
        return _GROWTH_MULT_HIGH_SBC, _MARGIN_DELTA_MULT_HIGH_SBC
    return _GROWTH_MULT, _MARGIN_DELTA_MULT


def _staged_wacc_for_year(base_wacc: float, profile_name: str, year: int) -> float:
    """Apply early-stage risk premium to first 3 years for high-dilution Tech profiles.
    Year is 1-indexed. Non-early-stage profiles return base_wacc unchanged."""
    if profile_name in _EARLY_STAGE_PROFILES and year <= _EARLY_STAGE_WACC_YEARS:
        return base_wacc + _EARLY_STAGE_WACC_PREMIUM
    return base_wacc


def _decayed_growth_schedule(base_growth: float, profile_name: str, years: int = 10) -> list[float]:
    """Return per-year growth rate list with exponential decay for growth-phase profiles.
    Non-decay profiles (default) return a constant schedule — preserves existing behavior
    for Bank/REIT/Biopharma/non-Tech paths."""
    delta = _GROWTH_DECAY_DELTA.get(profile_name, 0.0)
    if delta == 0.0:
        return [base_growth] * years
    return [base_growth * (1 - delta) ** t for t in range(years)]


# ── Phase 1.1: convergence fade + CAGR-divergence gate (2026-09-16) ──────────
#
# Defect 1 — a ONE-YEAR consensus jump was held flat for ten years. BN4.SI
# carried +20.3% NTM growth against a −2.5% five-year revenue CAGR and U96.SI
# +22% against the same, and both were compounded for a decade. Holding a
# forward-twelve-month figure flat is not a forecast of that year repeated; it
# is an unstated claim that the inflection is permanent. Neither name was
# caught by the revenue-scale tier, because at S$5.8–6.0bn of revenue the tier
# cap is 22% — above both consensus figures.
#
# Two corrections, both owner-specified. No other bound is introduced.
#
#   1. A convergence FADE on the profiles below: growth closes half the
#      distance to the engine's long-run nominal rate each year.
#   2. A divergence GATE on ALL profiles: consensus sitting more than 15pp from
#      the historical CAGR is capped at CAGR + 5pp before it seeds anything.
#
# The fade target is the scenario's TERMINAL GROWTH RATE from tgr_table — the
# engine's own long-run nominal rate — NOT the historical CAGR. Anchoring the
# fade on the historical CAGR would pin MELI at its 42% trailing rate forever,
# which is the opposite failure to the one being fixed.

#: Reserve-backed profiles: an accepted PV-10 / standardized measure floors
#: the bear case and is published as a cross-check. Never blended (owner,
#: 2026-09-20): the standardized measure (ASC 932 / SEC rules) is bound to a
#: 12-month unweighted trailing average price and a mandatory 10% discount
#: rate -- an accounting disclosure, not a fair market valuation -- and on COP,
#: DVN and OXY it sat 75-79% below the share price.
_RESERVE_FLOOR_PROFILES: frozenset[str] = frozenset({
    "Upstream Oil & Gas", "Integrated Oil & Gas",
})

#: Profiles where contracted backlog is revenue visibility: it bounds the bear
#: case's near-term revenue decline (owner, 2026-09-20). No EV/Backlog leg --
#: FMP carries no backlog for peers, so a peer median cannot be taken.
_BACKLOG_VISIBILITY_PROFILES: frozenset[str] = frozenset({
    "Oilfield Services & Drilling",
})


#: Profiles where a peak margin IS the cycle, not an inflection. Excluded from
#: the margin exception to the CAGR gate: at a cycle top the latest EBIT margin
#: sits above its own multi-year mean by construction, so the exception would
#: fire precisely when it is least warranted. Phase 1.2B gives these same
#: profiles mid-cycle legs and routes them to P/B-ROE at peak.
_CYCLICAL_PROFILES: frozenset[str] = frozenset({
    "Memory / DRAM-NAND",
    "Mining (Major)",
    "Upstream Oil & Gas",
    "Integrated Oil & Gas",
    "Refining & Marketing",
    "Oilfield Services & Drilling",
    "Coal",
    "Steel / Metals",
    "Specialty Chemicals",
    "Airlines",
    "Automotive (OEM)",
    "Digital Asset Mining",
    "Clean Tech / Power Equipment OEM",
})

#: Phase 1.2B — peak-consensus trigger. Forward consensus EPS above EITHER
#: bound means the number being capitalised has not happened in any year of the
#: available history and, on a cyclical, will not hold: MU's FY27 consensus of
#: $156.08 measured 20.0x its best year ($7.81) and 26.0x its own mean plus two
#: standard deviations. Both bounds are owner-specified; the two-arm form
#: matters because neither alone is sufficient. A multiple-of-max test misses a
#: name whose history is uniformly depressed (2x a trough is still a trough); a
#: mean-plus-sigma test misses one whose history is a single spike. Measured on
#: the golden basket MU fires both arms and FCX fires neither — FCX's forward
#: $2.95 against a sigma line of $3.19 is within 8%, close enough that a small
#: feed revision flips it, which is recorded rather than tuned away.
_PEAK_EPS_MAX_MULTIPLE: float = 2.0
_PEAK_EPS_SIGMA_MULTIPLE: float = 2.0

#: Owner floors on the mid-cycle P/B-ROE leg, both recording a flag when they
#: bind. ``CoE - g`` at or below zero divides by nothing and publishes an
#: arbitrary multiple; a justified P/B below 0.20x on a going concern is a
#: liquidation statement the Gordon form is not equipped to make. These are the
#: only bounds Phase 1.2B adds — there is deliberately no upper cap on P/B,
#: because the owner specified floors and a cap would be an unauthorised clamp
#: on an estimate.
_PB_ROE_MIN_SPREAD: float = 0.025
_PB_ROE_MIN_PB: float = 0.20

#: Minimum observations before the sigma arm of the trigger is allowed to fire.
#: With two points a standard deviation is a description of the two points, not
#: of a cycle, and mean + 2σ is then below the maximum by construction — the arm
#: would fire on any name with a short history and a single good year. The
#: multiple-of-max arm is unaffected and still applies.
_PEAK_MIN_OBSERVATIONS_FOR_SIGMA: int = 4

#: The trailing legs a cyclical profile should be carrying mid-cycle rather than
#: at whatever point in the cycle the last filing happened to land. Three of
#: the _CYCLICAL_PROFILES already use the normalised spelling — Mining (Major)
#: and Digital Asset Mining on EV/EBITDA, Memory / DRAM-NAND on P/E — which is
#: the evidence that the table's intent is normalisation and the plain legs are
#: oversights rather than a deliberate choice. Keys are matched exactly, so
#: "EV/EBITDAR" (Airlines, whose lease normalisation is separately unimplemented)
#: and "EV/EBITDA (Norm)" (Steel / Metals) are correctly left alone.
_MID_CYCLE_LEG_SWAPS: dict[str, str] = {
    "EV/EBITDA": "EV/EBITDA (norm)",
    "P/E":       "P/E (norm)",
}

#: Profiles whose NTM growth fades toward the long-run rate rather than holding
#: flat: cyclical AND structurally lumpy earners, where a single consensus year
#: can be a disposal, a tariff reset or a contract award that does not repeat.
#: The four tech profiles in _GROWTH_DECAY_DELTA keep their existing
#: multiplicative decay and are deliberately absent — the two sets are
#: DISJOINT, pinned by tests/test_growth_convergence.py so a profile added to
#: both cannot silently pick an arbitrary schedule.
_CONVERGENCE_ALPHA_PROFILES: frozenset[str] = frozenset({
    "Conglomerate / Industrial (SG)",
    "Capital Goods",
    "Electronic Materials & Industrial Diversified",
    "Regulated Utility",
    "IPP",
    "Automotive (OEM)",
    "Automotive & EV",
    "Airlines",
    "Steel / Metals",
    "Specialty Chemicals",
    # Energy services: a one-year consensus jump is an order-book swing, not a
    # decade's growth. Seatrium's T-1 backtest projected 70.5% for ten years
    # and valued it at S$64.56 against a S$2.07 price (owner, 2026-09-20).
    "Offshore Marine & Resources (SG)",
    "Upstream Oil & Gas",
    "Integrated Oil & Gas",
    "Refining & Marketing",
    "Oilfield Services & Drilling",
    "Coal",
    "Mining (Major)",
    "Memory / DRAM-NAND",
    "Digital Asset Mining",
    # Wave 2: a policy-cycle hardware maker. Every cyclical gets the fade
    # (tests/test_growth_convergence.py) -- a one-year consensus jump on an
    # IRA-credit or tariff swing is not a decade's growth.
    "Clean Tech / Power Equipment OEM",
})

#: Fraction of the gap to the long-run rate retained each year. 0.5 halves the
#: remaining distance annually: from a gated 5.0% with a 2.0% long-run rate the
#: path is 5.00 / 3.50 / 2.75 / 2.38 / 2.19 ... converging without a cliff.
_CONVERGENCE_ALPHA = 0.5

#: The gate fires beyond this divergence between NTM consensus and the
#: historical CAGR, and then rewrites NTM growth back toward the CAGR. Both
#: halves of the rewrite are built from these two numbers and no others,
#: deliberately: the ceiling is ``max(CAGR, 0) + HEADROOM`` and the floor is
#: ``min(CAGR, 0) - HEADROOM``, with rewrites ``min(g, ceiling)`` and
#: ``max(g, CAGR - THRESHOLD)``. One slack value and one divergence value,
#: applied symmetrically, so the two halves cannot drift onto different
#: tolerances — the same one-reader discipline the ceiling's ``max(CAGR, 0)``
#: and the floor's ``min(CAGR, 0)`` already share.
_CAGR_DIVERGENCE_THRESHOLD = 0.15
_CAGR_DIVERGENCE_HEADROOM = 0.05

#: Margin-inflection proxy for the gate's second exception: the latest EBIT
#: margin must sit at least this far above its trailing 3-year mean.
_EBIT_INFLECTION_GAP = 0.05
_EBIT_INFLECTION_YEARS = 3


def _growth_convergence_schedule(
    g_ntm: float,
    g_norm: float,
    alpha: float = _CONVERGENCE_ALPHA,
    years: int = _PROJECTION_YEARS,
) -> list[float]:
    """Per-year growth fading geometrically from ``g_ntm`` toward ``g_norm``.

        g_t = g_ntm · α^(t−1) + g_norm · (1 − α^(t−1))      t = 1 .. years

    written 0-indexed below, since ``_project_dcf`` reads year *t* from index
    *t−1* of ``growth_schedule``.

    Year 1 is EXACTLY ``g_ntm`` (α^0 = 1), which is the point of the strict
    gate → seed → fade ordering: a one-year consensus figure is a valid year-1
    estimate and the fade must not disturb it. ``g_ntm`` equals raw consensus
    only when the divergence gate did not fire; when it did, year 1 is the
    gated value. Later years close a fraction α of the remaining gap, so the
    schedule approaches ``g_norm`` monotonically and never crosses it, whether
    ``g_ntm`` starts above or below.
    """
    return [
        g_ntm * (alpha ** t) + g_norm * (1.0 - alpha ** t)
        for t in range(years)
    ]


def _ebit_margin_inflection(series: list[dict]) -> bool:
    """True when the latest EBIT margin sits ≥5pp above its trailing 3-year mean.

    Deterministic stand-in for "margins and order books justify an inflection"
    — the gate's only non-guidance exception, so it must not need an LLM or a
    judgement call to evaluate.

    The mean INCLUDES the latest year. That is the conservative reading: a
    rising margin lifts its own comparison base, so the gap has to be large
    (≥15pp of combined movement across the three years) before consensus growth
    is unlocked. Excluding the latest year would let a single good quarter
    against two flat ones through.

    Needs 3 usable years; with fewer there is no mean to test against and this
    returns False. That matters more than it looks — the feed caps history at 5
    annual rows (defect 8), so the window is usually all the history there is.
    """
    margins: list[float] = []
    for r in series or []:
        ebit = _safe(r.get("ebit"))
        rev = _safe(r.get("revenue"))
        if ebit is not None and rev and rev > 0:
            margins.append(ebit / rev)
    if len(margins) < _EBIT_INFLECTION_YEARS:
        return False
    window = margins[-_EBIT_INFLECTION_YEARS:]
    mean = sum(window) / len(window)
    return (window[-1] - mean) >= _EBIT_INFLECTION_GAP


def _gate_growth_cagr_divergence(
    g_ntm: Optional[float],
    cagr: Optional[float],
    *,
    data_source: str = "",
    profile_name: str = "",
    series: Optional[list[dict]] = None,
) -> tuple[Optional[float], Optional[dict], Optional[str]]:
    """Bound NTM consensus growth that diverges too far from the historical CAGR.

    Returns ``(gated_growth, gate_record, exception_reason)``:

      * ``gated_growth`` — the rewritten figure, or ``g_ntm`` unchanged.
      * ``gate_record`` — the ``gate_evaluations`` entry (path A = raw
        consensus, path B = gated, plus ``direction`` naming which half bound)
        when the gate BINDS, else None. Recorded on binding rather than on
        firing so the forward ledger measures decisions that actually moved a
        number.
      * ``exception_reason`` — why the gate was stood down, else None.

    Fires when ``|g_ntm − CAGR| > 15pp``, then rewrites toward the CAGR in
    whichever direction diverged:

      * **upward** — ``g_ntm := min(g_ntm, max(CAGR, 0) + 5pp)``. The defect is
        a one-year consensus jump held flat for a decade.
      * **downward** (owner decision 3, 2026-09-17) —
        ``g_ntm := max(g_ntm, CAGR − 15pp)``, but ONLY when ``g_ntm`` sits below
        ``min(CAGR, 0) − 5pp``, ``CAGR > 0``, and the profile is not in
        :data:`_CYCLICAL_PROFILES`. The defect here is the mirror image: one
        corrupted estimate in a thin consensus collapsing an explicit ten-year
        cash flow model. ICE measured it — a −12.71% NTM consensus against a
        +8.3% five-year CAGR, 21.0pp apart, which the one-sided gate waved
        through because it only tested for positive blowouts.

    The two halves are mutually exclusive, not merely independent: the ceiling
    binds only above ``max(CAGR, 0) + 5pp`` and the floor only below
    ``min(CAGR, 0) − 5pp``, and those two bounds cannot both contain the same
    number. Whichever binds is named in ``record["direction"]``, which is what
    lets the caller apply it in the right idiom — ``min()`` on ``growth_base``
    and a proportional band scale upward, ``max()`` and an additive band shift
    downward.

    ``max(CAGR, 0)`` is what makes the ceiling sane for a shrinking business.
    BN4.SI's 5-year revenue CAGR is −2.5%; without the floor the ceiling would
    be −2.5% + 5% = 2.5%, but with it the ceiling is 5.0%. The floor says a
    company in structural decline may still be allowed modest growth in the
    forward year — it is the *extrapolation* of the decline that is being
    refused, not growth itself. ``min(CAGR, 0) − 5pp`` is the same argument
    read from the other end: a company with a positive secular history is still
    allowed a bad forward year, down to −5%, and only below that is the figure
    treated as an estimate error rather than a forecast.

    The ``CAGR > 0`` conjunct is what keeps this off a genuine down-cycle that
    has already shown up in the history. A business whose five-year CAGR is
    itself negative gets no downside floor at all — there is nothing secular to
    defend. Combined with the :data:`_CYCLICAL_PROFILES` exemption, the floor
    only ever fires on a name with positive measured history, a non-cyclical
    profile, and a consensus below −5%: DRAM at −35% in a down-cycle is
    exempt on the profile, and shipping at −20% against a −8% CAGR is exempt on
    the sign.

    Two exceptions, both deterministic and both GATE-WIDE — they stand down the
    floor exactly as they stand down the ceiling, because a reason to distrust
    the divergence test is a reason to distrust it in both directions:

      * **company-guided** (``data_source == "guided"``) — R1 structured
        guidance is management's own figure for the forward year. That is
        exactly the information a consensus-vs-history divergence cannot see,
        so it overrides the gate outright.
      * **margin inflection** — latest EBIT margin ≥5pp above its 3-year mean,
        but NOT for :data:`_CYCLICAL_PROFILES`, where a peak margin exceeds its
        mean by construction and must not unlock the gate.

    The gate is applied to the BASE figure before scenario differentiation, and
    the analyst band is then adjusted by the same amount in each path's own
    idiom rather than clipped per scenario — :func:`_scale_analyst_bands_to_cap`
    upward, :func:`_shift_analyst_bands_to_floor` downward — so the dispersion
    14 analysts actually expressed survives instead of collapsing base and bull
    onto one bound.
    """
    if g_ntm is None or cagr is None:
        return g_ntm, None, None

    if abs(g_ntm - cagr) <= _CAGR_DIVERGENCE_THRESHOLD:
        return g_ntm, None, None

    ceiling = max(cagr, 0.0) + _CAGR_DIVERGENCE_HEADROOM
    # Owner decision 3 (2026-09-17). `min(cagr, 0.0)` is inert while the
    # `cagr > 0.0` conjunct below holds — it always evaluates to 0.0, so the
    # floor is always −5pp — but it is written out rather than hardcoded
    # because it is the mirror of the ceiling's `max(cagr, 0.0)`, and a reader
    # comparing the two bounds should see the symmetry instead of having to
    # derive it. If the positive-CAGR conjunct is ever relaxed to let a
    # shrinking history defend its own decline, this expression is already the
    # right one.
    floor = min(cagr, 0.0) - _CAGR_DIVERGENCE_HEADROOM

    if data_source == "guided":
        return g_ntm, None, "company-guided (R1 structured guidance)"
    if (profile_name not in _CYCLICAL_PROFILES
            and _ebit_margin_inflection(series or [])):
        return g_ntm, None, (
            f"EBIT margin inflection (latest ≥{_EBIT_INFLECTION_GAP:.0%} "
            f"above {_EBIT_INFLECTION_YEARS}y mean) on non-cyclical profile"
        )

    gated = min(g_ntm, ceiling)
    if gated < g_ntm:
        direction = "cap"
    else:
        # The upward cap does not bind. Try the downside floor: a consensus
        # this far BELOW a positive secular CAGR is a corrupted estimate
        # collapsing a ten-year model, not a forecast. Cyclicals are exempt
        # because a −20% to −40% down-cycle in DRAM or shipping is genuine;
        # a non-positive CAGR is exempt because there is then no secular
        # history for the consensus to contradict.
        if (g_ntm < floor and cagr > 0.0
                and profile_name not in _CYCLICAL_PROFILES):
            gated = max(g_ntm, cagr - _CAGR_DIVERGENCE_THRESHOLD)
            direction = "floor"
        else:
            direction = None
        if direction is None or gated <= g_ntm:
            # Divergence was real but inside the floor's slack, or the profile
            # is exempt. Neither half binds; nothing moves and nothing is
            # recorded, matching the ceiling path's behaviour on a non-binding
            # cap.
            return g_ntm, None, None

    if direction == "cap":
        basis = (
            f"NTM consensus {g_ntm:+.1%} diverges from historical CAGR "
            f"{cagr:+.1%} by {abs(g_ntm - cagr) * 100:.1f}pp, above the "
            f"{_CAGR_DIVERGENCE_THRESHOLD * 100:.0f}pp threshold; capped at "
            f"max(CAGR, 0) + {_CAGR_DIVERGENCE_HEADROOM * 100:.0f}pp"
        )
    else:
        basis = (
            f"NTM consensus {g_ntm:+.1%} sits {abs(g_ntm - cagr) * 100:.1f}pp "
            f"below historical CAGR {cagr:+.1%} and below the "
            f"{floor:+.1%} downside floor on a non-cyclical profile with "
            f"positive secular history; raised to CAGR − "
            f"{_CAGR_DIVERGENCE_THRESHOLD * 100:.0f}pp"
        )

    record = {
        "gate_id": "GATE_GROWTH_CAGR_DIVERGENCE",
        "metric": "revenue_growth",
        "raw_input_path_a": round(float(g_ntm), 6),
        "gated_output_path_b": round(float(gated), 6),
        "direction": direction,
        "basis": basis,
        "applied": True,
    }
    return gated, record, None


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


def _convergence_bound(scen_iv: float, spot: float, max_capture: float) -> float:
    """Furthest a 12m price target may sit from `spot` toward `scen_iv`.

    The 12m PT engine is forward-multiple-driven and decoupled from intrinsic
    value, so without a bound it can imply a full re-rating to IV inside a
    year. `max_capture` is the fraction of the spot→IV gap a target is allowed
    to close (profile-dependent: 20-35%).

    Deliberately sign-agnostic. Applying this only when `scen_iv > spot`
    exempts exactly the case a bear scenario is meant to produce, which is how
    02888.HK ended up with a bear target of $296.36 above its bull of $252.93
    — the bear IV sat below spot, skipped the cap, and kept its raw multiple
    while base and bull were compressed.

    Callers clamp one-sided (`min(pt, bound)`): a target already inside the
    bound is left alone, so this only ever tightens.

    >>> round(_convergence_bound(258.04, 228.60, 0.35), 2)   # IV above spot
    238.9
    >>> round(_convergence_bound(215.89, 228.60, 0.35), 2)   # IV below spot
    224.15
    >>> _convergence_bound(228.60, 228.60, 0.35)             # IV at spot
    228.6
    """
    return spot + max_capture * (scen_iv - spot)


#: Sectors whose short-term investments ARE the business -- a bank's securities
#: book, an insurer's float -- and are never spare cash to net against debt.
#: Healthcare is excluded too: a managed-care plan's investments back medical
#: claims inside regulated subsidiaries (Molina's were 36% of its market cap in
#: the 2026-09-15 audit), and biotech simply keeps its prior treatment.
_NO_INVESTMENT_NETTING_SECTORS = frozenset({"Financials", "Insurance", "Banks",
                                            "Healthcare", "Health Care"})


#: Balance-sheet lines the EV bridge reads. Everything else on the row is a
#: flow (revenue, EBIT, FCF) and must stay on the annual series.
_BALANCE_SHEET_LINES = ("cash_and_equivalents", "short_term_investments",
                        "total_debt", "net_debt")


#: Step-change threshold for the quarterly balance-sheet overlay, as a ratio of
#: the ANNUAL net cash. Owner-set at 0.25 (2026-09-18), replacing the 0.15 in
#: the original Phase 1.4 plan text: 15% fires on ordinary quarterly
#: working-capital drift, 25% clears that and leaves true structural
#: step-changes. Measured against the cases the overlay exists for --
#: 09988.HK's 31-Mar-2026 year end at RMB98.6bn net cash against the
#: 30-Jun-2026 quarter at RMB161.7bn is 0.64, so it fires; a normal quarter
#: does not. Disclosure only, `applied: False`: crossing this threshold never
#: aborts the overlay and never reverts the row to stale annual data.
#:
#: The `max(abs(annual), 1.0)` denominator floor is the owner's formula, not a
#: tuning knob. It only binds when annual net cash is exactly 0.0 -- including
#: the `_net_debt_net_of_investments` "no net_debt on the row" case, which
#: returns 0.0 rather than None -- and there the ratio correctly explodes,
#: because a move off an unreported base IS a step change and the recorded
#: `annual_net_cash: 0.0` says so plainly.
#:
#: ── The wide-gap threshold ──────────────────────────────────────────────────
#: 0.25 alone fires too often to be read. Measured across the golden fixtures
#: (`scratchpad/probe_stepchange_golden.py`, replayed offline against all 14
#: captured runs): the overlay applied on 8 of them and ALL 8 crossed 0.25 --
#: a 100% firing rate, so the flag carried no information. The reason is
#: temporal, not economic. This overlay does not compare one quarter against
#: the previous one; it compares the ANNUAL year end against the latest
#: reported quarter, and the two are routinely 8-9 months apart (AAPL
#: 2025-09-27 -> 2026-06-27 = 273 days, MU 2025-08-28 -> 2026-05-28 = 273,
#: V 2025-09-30 -> 2026-06-30 = 273, COST 2025-08-31 -> 2026-05-10 = 252).
#: Three quarters of balance-sheet movement is not a step change, it is a
#: baseline drift the annual figure simply predates.
#:
#: Owner ruling (2026-09-19): "Adjust the step-change flag threshold from 0.25
#: to 0.50 (50%) when period_delta_days > 180". The conditional is implemented
#: rather than a global 0.50, which was the ruling's parenthetical alternative:
#: a genuine 91-day year-end-to-Q1 comparison keeps the tighter 0.25 and stays
#: sensitive, while the multi-quarter case is the one that needs the wider band.
#: Measured effect on those same 8 fixtures -- one dropout, MELI at 0.4620 over
#: a 181-day gap; the other seven (0.6202-4.9521) clear 0.50 and still fire.
#:
#: Still disclosure only. Neither threshold aborts the overlay, and nothing
#: here is a materiality floor on the denominator -- that floor stays exactly
#: `max(abs(a_net_cash), 1.0)`.
_BALANCE_SHEET_STEP_CHANGE_THRESHOLD = 0.25

#: Selected when the annual-to-quarterly gap exceeds
#: ``_BALANCE_SHEET_STEP_CHANGE_WIDE_GAP_DAYS``. Owner-set 2026-09-19; see the
#: firing-rate measurement above.
_BALANCE_SHEET_STEP_CHANGE_THRESHOLD_WIDE = 0.50

#: Strictly greater than 180 days selects the wide threshold. 180 itself does
#: not: half a year of drift is still the tight case.
_BALANCE_SHEET_STEP_CHANGE_WIDE_GAP_DAYS = 180


def _period_delta_days(earlier, later) -> Optional[int]:
    """Calendar days between two ISO period strings, or None if either is unusable.

    Both operands at the call site are already ISO ``YYYY-MM-DD`` strings --
    the overlay's own staleness guard compares them lexicographically, which
    only works for that format -- so this parses rather than guesses. It is
    still defensive, because a provider that ever returns ``"FY2026"`` or a
    timestamp would otherwise raise inside a telemetry path whose whole job is
    to be unable to break a valuation.

    Returns None on anything unparseable. The caller then falls back to the
    STRICTER 0.25 threshold rather than the wider one: this is disclosure, so
    the safer failure mode is recording a move that turns out to be ordinary
    drift, not silently dropping one that was structural.
    """
    try:
        a = date.fromisoformat(str(earlier or "")[:10])
        b = date.fromisoformat(str(later or "")[:10])
    except (TypeError, ValueError):
        return None
    # abs() because the sign is a property of the call order, not of the gap.
    # The overlay guarantees `later > earlier` before it gets here, so the
    # absolute value changes nothing in practice -- but a negative delta would
    # otherwise select the STRICT threshold for a reason nobody can see in the
    # record, and abs() makes the recorded number mean "how far apart".
    return abs((b - a).days)



def _refresh_balance_sheet_from_latest_quarter(
    ticker: str,
    row: dict,
    end_date: str,
    api_key=None,
    sector: str = "",
) -> Optional[str]:
    """Overlay ``row``'s cash and debt lines with the latest reported quarter.

    The engine anchors on the last ANNUAL row, so a company whose fiscal year
    ends in March carries a balance sheet up to four quarters stale into every
    valuation. 09988.HK: the 31-Mar-2026 year end showed RMB98.6bn net cash,
    the 30-Jun-2026 quarter RMB161.7bn -- short-term investments alone moved
    RMB58bn in the gap -- so the SOTP, the EV/EBITDA bridge and every other
    EV-based method were pricing a seasonal low nobody reports any more.

    Flows (revenue, EBIT, FCF) stay annual; only the four balance-sheet lines
    move, and only when the quarter is strictly newer and carries both a cash
    and a debt figure. Mutates in place -- the row IS ``series[-1]``, and the
    FX loop downstream converts the series -- and returns a flag, or None when
    nothing was applied (no quarterly data, a stale quarter, a partial row).

    A move larger than the applicable step-change threshold is additionally
    recorded on ``row["_balance_sheet_step_change"]`` for the caller to lift
    into ``gate_evaluations``. Which threshold applies depends on the gap
    between the two readings: ``_BALANCE_SHEET_STEP_CHANGE_THRESHOLD`` (0.25)
    normally, ``_BALANCE_SHEET_STEP_CHANGE_THRESHOLD_WIDE`` (0.50) when the
    year end and the substituted quarter are more than
    ``_BALANCE_SHEET_STEP_CHANGE_WIDE_GAP_DAYS`` apart, because over a
    three-quarter gap ordinary drift alone clears 0.25 and the flag stops being
    readable. The record carries ``threshold_used`` and ``period_delta_days``
    so the choice is visible on the payload rather than inferable. That is
    telemetry and nothing more: it never gates, never aborts this overlay, and
    never reverts the row to the annual figures. The overlay has already been
    applied by the time the ratio is computed, which is the point -- the record
    exists so a post-mortem can see how far the balance sheet moved, not so
    this function can second-guess it.
    """
    try:
        q = search_line_items(ticker, list(_BALANCE_SHEET_LINES), end_date,
                              period="quarterly", limit=1, api_key=api_key)
    except Exception:
        return None
    if not q:
        return None
    qr = q[0]
    q_period = str(getattr(qr, "report_period", "") or "")
    if not q_period or q_period <= str(row.get("period") or ""):
        return None
    cash = getattr(qr, "cash_and_equivalents", None)
    debt = getattr(qr, "total_debt", None)
    if not isinstance(cash, (int, float)) or not isinstance(debt, (int, float)):
        return None
    # An all-zero row is an unreported period, not a debt-free quarter: the
    # provider returns one for D05.SI at 2026-03-31, cash and debt both 0.0
    # against RMB150bn of cash either side of it. Taking it would wipe the
    # balance sheet out of the EV bridge.
    if float(cash) == 0.0 and float(debt) == 0.0:
        return None
    before = _net_debt_net_of_investments(row, sector)
    for field in _BALANCE_SHEET_LINES:
        row[field] = getattr(qr, field, None)
    if not isinstance(row.get("net_debt"), (int, float)):
        row["net_debt"] = float(debt) - float(cash)
    row["_balance_sheet_period"] = q_period
    after = _net_debt_net_of_investments(row, sector)

    # ── Step-change telemetry ────────────────────────────────────────────────
    # NOT a cross-provider parity check, and the Phase 1.4 plan text ("when FMP
    # and the fallback both return a period") cannot be one: `src/tools/api.py`
    # dispatches exclusive-or, so FMP and the HK/SG fallback are never both in
    # hand and nothing on the served row says which one served it. Real
    # cross-provider validation belongs in an offline fixture or an intake smoke
    # test, not in this function.
    #
    # What this compares is one provider's ANNUAL balance sheet against the same
    # run's QUARTERLY overlay -- the substitution this function just performed.
    # Both operands come from `_net_debt_net_of_investments` with the same
    # `sector`, so they share a convention (short-term investments netted, the
    # sector guard applied) and the ratio measures the overlay, not a change of
    # definition between the two readings.
    #
    # Net CASH is the negation of net debt. The negation is cosmetic for
    # `delta_ratio` -- both operands sit inside `abs()` -- but the recorded
    # values are what a post-mortem reads, and "net cash +161.7bn" is the figure
    # the 09988.HK case is actually discussed in.
    a_net_cash = -float(before) if before else 0.0
    q_net_cash = -float(after) if after else 0.0
    delta_ratio = abs(q_net_cash - a_net_cash) / max(abs(a_net_cash), 1.0)
    # WHICH threshold applies is a function of how far apart the two readings
    # are, not of the ratio itself -- see the constant's comment for the
    # measured 100% firing rate that motivated the split. `row["period"]` is
    # still the ANNUAL period here: `_BALANCE_SHEET_LINES` does not include it
    # and the quarter is stashed under `_balance_sheet_period` instead.
    # An unparseable period yields None and falls through to the STRICTER
    # 0.25, because for a disclosure-only record the safer error is a flag
    # that turns out to be ordinary drift, not a silently dropped structural
    # move.
    _gap_days = _period_delta_days(row.get("period"), q_period)
    if (_gap_days is not None
            and _gap_days > _BALANCE_SHEET_STEP_CHANGE_WIDE_GAP_DAYS):
        _threshold = _BALANCE_SHEET_STEP_CHANGE_THRESHOLD_WIDE
    else:
        _threshold = _BALANCE_SHEET_STEP_CHANGE_THRESHOLD
    if delta_ratio > _threshold:
        row["_balance_sheet_step_change"] = {
            "flag": "BALANCE_SHEET_QUARTERLY_STEP_CHANGE",
            "annual_net_cash": a_net_cash,
            "quarterly_net_cash": q_net_cash,
            "delta_ratio": round(delta_ratio, 4),
            "source": "quarterly_overlay_refresh",
            "action": "applied_quarterly_override",
            # Both figures are pre-FX: the series is converted downstream, and
            # this runs before it. A reader comparing them against a
            # reported-currency disclosure would otherwise be off by the FX rate
            # with nothing on the payload to say so.
            "currency_basis": "source_currency_pre_fx",
            # Mine, not the owner's six -- and disclosed as such. Without
            # `threshold_used` a reader sees `delta_ratio: 0.4620` and cannot
            # tell whether it fired against 0.25 or was measured against 0.50
            # and dropped; `period_delta_days` is the input that chose it, so
            # the choice is auditable from the record instead of inferred from
            # source. Both are None/absent-proof: `period_delta_days` is
            # literally None when either period was unparseable, which is the
            # signal that the stricter threshold was used as a fallback.
            "period_delta_days": _gap_days,
            "threshold_used": _threshold,
        }
    return (f"Balance sheet from {q_period}, not the {row.get('period')} year "
            f"end: net debt {before / 1e9:,.1f}bn → {after / 1e9:,.1f}bn "
            f"(cash {cash / 1e9:,.1f}bn, short-term investments "
            f"{(getattr(qr, 'short_term_investments', None) or 0) / 1e9:,.1f}bn, "
            f"debt {debt / 1e9:,.1f}bn)")


def _net_debt_net_of_investments(row: dict, sector: str = "") -> float:
    """Net debt with short-term investments counted as cash.

    FMP's `netDebt` (and the HK line-item path) is total debt minus cash and
    equivalents ONLY. Alibaba keeps most of its liquidity in short-term
    investments, so FY2026 read as RMB86.1bn net DEBT (259.1 - 173.0) when,
    with RMB184.7bn of short-term investments, it holds ~RMB98.6bn net cash --
    about HK$11 a share off the 12m target, and the opposite sign from the
    SOTP in the same run.

    Only applied when the feed's figure is visibly debt minus cash alone, so a
    source that has already netted the investments never has them taken off
    twice; financials are excluded (see _NO_INVESTMENT_NETTING_SECTORS).
    """
    nd = row.get("net_debt")
    if nd is None:
        return 0.0
    sti = row.get("short_term_investments")
    if not sti or sti <= 0 or (sector or "") in _NO_INVESTMENT_NETTING_SECTORS:
        return float(nd)
    td, cash = row.get("total_debt"), row.get("cash_and_equivalents")
    if td is None or cash is None:
        return float(nd)
    if abs(float(nd) - (float(td) - float(cash))) > 0.01 * max(abs(float(td)), 1.0):
        return float(nd)
    return float(nd) - float(sti)


#: Methods that make a valuation SOTP-led. Owner policy (2026-09-15): a SOTP
#: is ALWAYS preferred to a generic multiple across a business group, so any
#: weighted SOTP -- analyst, segment, or holdco look-through / NAV -- puts the
#: 12m target on the convergence path toward the SOTP-led IV.
_SOTP_LED_METHODS = frozenset({
    "SOTP (analyst)", "Analyst SOTP", "SOTP (segments)",
    "SOTP / NAV", "SOTP / NAV (look-through)",
    "SOTP (published)", "Published SOTP",
})
#: Strictly above this share of the blend counts (any positive weight).
_SOTP_LED_PT_MIN_WEIGHT = 0.0


#: 12m-target paths that price NTM consensus with a peer multiple. A target
#: built on one of these is on a different earnings basis from an IV anchored
#: on normalised (through-cycle) earnings.
_FORWARD_CONSENSUS_PT_LABELS = frozenset({
    "EV/EBITDA or EV/Revenue forward multiple",
    "forward P/E x Year-1 EPS",
    "forward multiple (profile-specific)",
})


#: A 12m price target must stay inside this band around the IV it is meant to
#: be converging on. Owner decision 2c (2026-09-17): the divergence guard below
#: is one-sided -- it caps a target that runs to 2x the IV and has no lower
#: bound at all -- so the same corrupted forward inputs that produce MU's
#: $6,105 could equally produce Visa's $12.84, a target at 3.0% of its own base
#: IV, and neither guard would have said anything. A 12m target is a claim that
#: price travels PART of the way toward what this run believes the business is
#: worth; a target two-thirds below that belief is not a bear case, it is a
#: different valuation wearing the first one's label.
_PT_IV_BAND_LO = 0.33
_PT_IV_BAND_HI = 2.50
#: Scenario spread applied to the fallback, matching `_scenario_mult` used
#: everywhere else a single base number is fanned into three.
_PT_BAND_SCENARIO_MULT = {"bear": 0.75, "base": 1.00, "bull": 1.25}
#: A high-SBC name's BEAR 12m target is held at or below this fraction of spot.
#: Named for what it does, not for what the call site calls it: the local there
#: is `_bear_floor` but the comparison is `if _pt > _bear_floor`, i.e. a ceiling.
#: Referenced from `_band_12m_targets` too, so the two sites cannot drift onto
#: different numbers -- a band replacement that exempted itself from this would
#: publish a "bear" target ABOVE spot, which is the exact contradiction the
#: ceiling exists to refuse.
_HIGH_SBC_BEAR_CEILING_MULT = 0.85


def _band_12m_targets(
    targets: dict,
    scenario_ivs: dict,
    base_iv: Optional[float],
    coe: Optional[float],
    spot: Optional[float] = None,
    max_capture: Optional[float] = None,
    high_sbc: bool = False,
) -> tuple[dict, list[tuple[str, float, float, float]], dict]:
    """Enforce the two-sided band on a run's 12-month price targets.

    Returns ``(replacement, breaches, info)``. ``replacement`` is EMPTY unless
    every scenario is to be overwritten, so a caller can test it with a plain
    ``if`` rather than reading a flag; ``breaches`` is a list of
    ``(scenario, pt, scenario_iv, ratio)``.

    Three deliberate deviations from the instruction, each forced by a
    measurement rather than a preference:

    1. The band is tested against each scenario's OWN IV, not Base IV. Across
       the 14 golden fixtures x 3 scenarios, base-referencing manufactures two
       violations out of scenario spread the engine itself produced: FCX's bull
       target of $70.50 is 2.756x its BASE IV but 0.971x its own bull IV of
       $72.62, and U96.SI's bear target of S$1.78 is 0.311x base but 0.764x its
       own bear IV of S$2.33. Both are cyclicals whose bull IV sits far above
       their base IV. Per-scenario referencing still catches the two real cases
       (Visa at 0.030x and MELI's bear at 0.271x) and not those. The FALLBACK
       keeps the instruction exactly: Base IV discounted by the cost of equity.

    2. If ANY scenario breaches, ALL THREE are replaced. Per-scenario
       replacement is what creates an ordering violation -- measured on MELI it
       gives bear $3,754 / base $5,006 / bull $2,273, bull below bear, because
       its bull target did not breach and its bear and base did. Replacing the
       whole triple off one base keeps bear <= base <= bull by construction:
       ``base_iv/(1+coe) * mult``, ``_convergence_bound(iv, spot, cap)`` and the
       band limits ``[0.33, 2.50] * iv`` are each monotonic in the scenario
       order, and min/max of sequences monotonic in the same order is monotonic.

    3. The fallback is itself capped by ``_convergence_bound``. Without that,
       MELI's replacement would publish $5,006 on a $1,461 stock -- a 243% move
       in twelve months, which is the same class of absurdity the band exists to
       refuse, only on the other side, and laundered out of an IV whose own
       fixture flag says the cash-conversion gate "observed, not applied". A
       replacement target does not get to escape a bound every other target
       respects. The band then gets the last word over the cap, because a
       "VALIDATION ERROR: band violated" flag attached to a number that still
       violates the band is worse than either rule alone -- see the clamp at the
       end of the loop.

    The high-SBC bear ceiling (``spot * 0.85``) IS re-applied to the
    replacement, for the bear scenario only, and it is passed in rather than
    re-derived: ``_high_sbc`` lives inside the caller's spot-price guard and a
    second derivation of "is this profile stock-comp heavy" is how two readers
    of one question drift apart -- the same failure this whole decision family
    is about. When the ceiling and the band's low limit cannot both hold, the
    band wins and ``info["conflicts"]`` says so out loud.

    When the band fires but no base IV exists to build a fallback from, the
    targets are left alone and ``info["reason"]`` says why. Refusing to publish
    is not the same as refusing to notice: the caller still emits the
    validation error.
    """
    breaches: list[tuple[str, float, float, float]] = []
    for _sn in ("bear", "base", "bull"):
        _pt = targets.get(_sn)
        _iv = scenario_ivs.get(_sn)
        if not _pt or _pt <= 0 or not _iv or _iv <= 0:
            continue
        _r = float(_pt) / float(_iv)
        if _r < _PT_IV_BAND_LO or _r > _PT_IV_BAND_HI:
            breaches.append((_sn, float(_pt), float(_iv), _r))

    _b_iv = scenario_ivs.get("base")
    _b_pt = targets.get("base")
    _ratio_a = (float(_b_pt) / float(_b_iv)
                if (_b_pt and _b_pt > 0 and _b_iv and _b_iv > 0) else None)
    info: dict = {"base_ratio_path_a": _ratio_a}
    if not breaches:
        return {}, [], info
    if not base_iv or base_iv <= 0:
        info["reason"] = "no positive base IV to fall back on"
        info["base_ratio_path_b"] = _ratio_a
        return {}, breaches, info

    _coe = float(coe) if (isinstance(coe, (int, float)) and coe > 0) else 0.0
    info["coe"] = _coe
    _fb_base = float(base_iv) / (1.0 + _coe)
    info["fallback_base"] = _fb_base
    replacement: dict[str, float] = {}
    for _sn in ("bear", "base", "bull"):
        _v = _fb_base * _PT_BAND_SCENARIO_MULT[_sn]
        _iv = scenario_ivs.get(_sn)
        if (spot and spot > 0 and max_capture is not None
                and _iv and _iv > 0):
            _v = min(_v, _convergence_bound(float(_iv), float(spot),
                                            float(max_capture)))
        # The high-SBC bear ceiling applies to a replacement exactly as it
        # applies to the target it replaces. MELI measured this: spot $1,828.94
        # against a bear IV of $4,588.16 -- 2.5x spot -- so the convergence
        # bound puts the bear replacement at $2,794.67, a "bear" case 53% ABOVE
        # the price. That is the contradiction the ceiling exists to refuse, and
        # a sanity band is no licence to reintroduce it. Ceiling here is
        # $1,554.60, and it is compatible with the band's own low limit of
        # 0.33 x 4,588.16 = $1,514.09, leaving a $40 window.
        if _sn == "bear" and high_sbc and spot and spot > 0:
            _v = min(_v, float(spot) * _HIGH_SBC_BEAR_CEILING_MULT)
        # Last word, and the reason this function cannot emit a target that
        # would re-fire it on the next run. Two ways a fallback can leave the
        # band: the convergence cap can pull it below 0.33x when spot sits far
        # under the IV (spot 100, IV 10,000, capture 20% gives a bound of
        # 2,080 = 0.208x), and a fallback derived from BASE IV can exceed 2.50x
        # of a scenario whose own IV is a fraction of base (base 1,000 against a
        # bear IV of 100 gives 0.75 x 909 = 682 = 6.8x). Both are the band's own
        # subject matter, so the band wins over the cap rather than the two
        # publishing contradictory ideas of what is rational.
        if _iv and _iv > 0:
            _lo = _PT_IV_BAND_LO * float(_iv)
            _hi = _PT_IV_BAND_HI * float(_iv)
            if _v < _lo:
                _v = _lo
            elif _v > _hi:
                _v = _hi
            if (_sn == "bear" and high_sbc and spot and spot > 0
                    and _v > float(spot) * _HIGH_SBC_BEAR_CEILING_MULT):
                # The band's low limit and the ceiling cannot both be
                # satisfied. Recorded, not resolved silently: the band keeps the
                # last word so the invariant it exists to enforce actually
                # holds, and the caller reports that a stated policy had to give
                # way. This needs a bear IV above ~2.6x spot to occur.
                info.setdefault("conflicts", []).append(
                    f"bear band floor {_lo:,.2f} exceeds the high-SBC ceiling "
                    f"{float(spot) * _HIGH_SBC_BEAR_CEILING_MULT:,.2f}"
                )
            # Round toward the INSIDE of the band, not to nearest. `round` is
            # direction-agnostic and 0.33 x IV is not generally a whole number
            # of cents, so round(0.33 x 7809.788, 2) = 2577.23, which reads back
            # as 0.32999999487 -- a target published one cent outside the band
            # whose own validation flag says it satisfies it. Ceil at the low
            # limit and floor at the high one; skip when the two cent-safe
            # limits are not themselves ordered, which needs an IV below ~0.004
            # and therefore no real valuation.
            _lo_c = math.ceil(_lo * 100.0) / 100.0
            _hi_c = math.floor(_hi * 100.0) / 100.0
            replacement[_sn] = (
                min(max(round(_v, 2), _lo_c), _hi_c) if _lo_c <= _hi_c
                else round(_v, 2))
        else:
            replacement[_sn] = round(_v, 2)
    _nb = replacement.get("base")
    info["base_ratio_path_b"] = (
        float(_nb) / float(_b_iv)
        if (_nb and _b_iv and _b_iv > 0) else None)
    return replacement, breaches, info


def _sotp_led_share(scenario: dict) -> float:
    """Share of the blended IV carried by analyst-SOTP methods (0..1)."""
    ew = scenario.get("effective_weights") or []
    total = sum(float(w.get("weight") or 0.0) for w in ew)
    if total <= 0:
        return 0.0
    return sum(float(w.get("weight") or 0.0) for w in ew
               if w.get("method") in _SOTP_LED_METHODS) / total


# ── FX classification for every field _extract_annual_series() puts on a row ──
# These live HERE, next to the row builder, because they previously sat ~200k
# characters downstream inside run_dcf_agent(): fields were added to the row
# builder and silently never registered for conversion. On 02888.HK that left
# `tangible_book_value_per_share` in USD while `total_equity`/`book_value_per_share`
# were converted to HKD, so the bank panel rendered a HK$19.32 "P/TBV fair value"
# against a HK$228.60 share, plus a negative NIM (interest_income unconverted
# against a converted interest_expense) and an 8.27% cost-income ratio.
#
# Every key the row builder emits MUST appear in exactly one of these two sets;
# test_fx_field_coverage pins that, so a newly-added field fails the suite
# rather than silently producing mixed-currency arithmetic.
#
# Per-share fields are converted too — they are denominated in the reporting
# currency just like the totals. Counts (shares) and ratios (debt_to_equity)
# are dimensionless and must NOT be converted.
_FX_MONETARY_FIELDS: frozenset[str] = frozenset({
    # Income statement
    "revenue", "gross_profit", "cost_of_revenue", "operating_income",
    "operating_expense", "ebit", "ebitda", "net_income",
    "interest_income", "interest_expense",
    "research_and_development", "stock_based_compensation",
    "depreciation_and_amortization",
    # Cash flow
    "free_cash_flow", "fcf_owner_earnings", "operating_cash_flow",
    "capital_expenditure", "change_in_working_capital",
    "share_buyback", "common_stock_repurchased",
    # Balance sheet
    "total_assets", "total_equity", "total_liabilities",
    "net_debt", "total_debt", "invested_capital", "cash_and_equivalents",
    "short_term_investments",
    "minority_interest",
    "goodwill", "intangible_assets",
    # The two operating-capital lines the deterministic ROIC denominator floor
    # needs (operating working capital + net PP&E). Both were already fetched
    # by data_router for the three-statement view and mapped in api.py, but
    # never requested here, so they were None on every row this engine builds.
    # Monetary and converted: the floor is compared against a financing-side
    # capital that IS converted, so leaving these in the filing currency would
    # decide the max() by FX rate rather than by balance sheet.
    "inventory", "property_plant_equipment",
    # Bank-specific balance sheet
    "loans_receivable", "loans_held_for_investment", "total_deposits",
    "provision_for_loan_losses",
    # Customer-balance proxy lines for the balance-sheet-financial gate
    # (_tier2_customer_balance_ratio). Monetary, and they MUST be converted:
    # the ratio's denominator is `total_assets`, which is. Leaving these in the
    # filing currency while converting the denominator scales the ratio by the
    # FX rate — on 0388.HK that turns a true 0.208 into a spurious 1.62 and
    # strips an exchange's EV and DCF legs for a reason that does not exist.
    "accounts_payable", "accounts_receivable",
    "other_payables", "other_current_liabilities",
    # Per-share (denominated in the reporting currency)
    "dividends_per_share", "book_value_per_share",
    "tangible_book_value_per_share",
})

# Dimensionless or non-numeric — never FX-converted.
_FX_NON_MONETARY_FIELDS: frozenset[str] = frozenset({
    "period",              # date string
    "shares_outstanding",  # a count
    "debt_to_equity",      # a ratio
})


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
            "total_debt":          _safe(getattr(li, "total_debt", None)),
            "ebitda":              _safe(getattr(li, "ebitda", None)),
            "net_income":          _safe(getattr(li, "net_income", None)),
            "total_assets":        _safe(getattr(li, "total_assets", None)),
            "total_equity":        _safe(getattr(li, "total_equity", None)),
            "minority_interest":   _safe(getattr(li, "minority_interest", None)),
            "dividends_per_share": _safe(getattr(li, "dividends_per_share", None)),
            "book_value_per_share":_safe(getattr(li, "book_value_per_share", None)),
            "capital_expenditure": _safe(getattr(li, "capital_expenditure", None)),
            "ebit":                _safe(getattr(li, "ebit", None)),
            "interest_expense":    _safe(getattr(li, "interest_expense", None)),
            "invested_capital":    _safe(getattr(li, "invested_capital", None)),
            "stock_based_compensation":   _safe(getattr(li, "stock_based_compensation", None)),
            # R&D spend — consumed by the EV/R&D method and the rNPV future-R&D
            # burn deduction. Was requested in search_line_items and listed in
            # _FX_MONETARY but never copied into rows, so EV/R&D silently
            # returned None for every biotech (discovered by D6 fixture tests).
            "research_and_development":   _safe(getattr(li, "research_and_development", None)),
            # REIT-specific: D&A for FFO reconstruction, OCF for AFFO, cash for NAV bridge
            "depreciation_and_amortization": _safe(getattr(li, "depreciation_and_amortization", None)),
            "operating_cash_flow":  _safe(getattr(li, "operating_cash_flow", None)),
            # The non-projectable term of the owner-earnings identity, fetched
            # so a gate can state how much of reported FCF is working capital
            # instead of inferring it.
            "change_in_working_capital": _safe(
                getattr(li, "change_in_working_capital", None)),
            "cash_and_equivalents": _safe(getattr(li, "cash_and_equivalents", None)),
            # Counted as cash in net debt (_net_debt_net_of_investments).
            "short_term_investments": _safe(getattr(li, "short_term_investments", None)),
            # Bank-specific: NII reconstruction, credit cost, TBV
            "interest_income":           _safe(getattr(li, "interest_income", None)),
            "provision_for_loan_losses": _safe(getattr(li, "provision_for_loan_losses", None)),
            "goodwill":                  _safe(getattr(li, "goodwill", None)),
            "intangible_assets":         _safe(getattr(li, "intangible_assets", None)),
            # FMP's pre-computed TBV/sh from /stable/ratios — preferred over our
            # blind goodwill+intang strip because FMP applies the bank's own
            # reporting convention (e.g. JPM treats MSRs as tangible, not strip).
            "tangible_book_value_per_share": _safe(getattr(li, "tangible_book_value_per_share", None)),
            "total_liabilities":         _safe(getattr(li, "total_liabilities", None)),
            "operating_expense":         _safe(getattr(li, "operating_expense", None)),
            "operating_income":          _safe(getattr(li, "operating_income", None)),
            # Bank loan book + deposits (Tier 2 bank UI — loan growth history
            # and LDR). FMP coverage is inconsistent; loans_receivable may be
            # None for major US money-center banks. Downstream falls back to
            # research-extracted loan_growth_yoy when None.
            "loans_receivable":          _safe(getattr(li, "loans_receivable", None)),
            "loans_held_for_investment": _safe(getattr(li, "loans_held_for_investment", None)),
            "total_deposits":            _safe(getattr(li, "total_deposits", None)),
            # Customer-balance proxy for the balance-sheet-financial gate. The
            # feed has no dedicated customer-payables or margin-receivables
            # line, so the gate reads these generic ones instead — see
            # _is_balance_sheet_financial for the mapping and the measured
            # evidence. Requested AND copied: three bank lines above were read
            # here for years without being in run_dcf_agent's request list, so
            # they were None on every FMP row and the loan-to-deposit KPI they
            # fed never computed. tests/test_line_items_requested_are_read.py
            # is what stops that recurring.
            "accounts_payable":          _safe(getattr(li, "accounts_payable", None)),
            "accounts_receivable":       _safe(getattr(li, "accounts_receivable", None)),
            "other_payables":            _safe(getattr(li, "other_payables", None)),
            "other_current_liabilities": _safe(getattr(li, "other_current_liabilities", None)),
            # Buybacks for retention_rate (banks return large % of earnings via
            # repurchases alongside dividends — ignoring this inflates retention)
            "share_buyback":             _safe(getattr(li, "share_buyback", None)),
            "common_stock_repurchased":  _safe(getattr(li, "common_stock_repurchased", None)),
            # Tech/Payment-processor methods: EV/Gross Profit
            "gross_profit":              _safe(getattr(li, "gross_profit", None)),
            "cost_of_revenue":           _safe(getattr(li, "cost_of_revenue", None)),
            # Operating side of the deterministic ROIC denominator floor
            # (src/data/deterministic_kpis.py). Requested AND copied — see the
            # matching entry in _FX_MONETARY_FIELDS and in run_dcf_agent's
            # search_line_items list.
            "inventory":                 _safe(getattr(li, "inventory", None)),
            "property_plant_equipment":  _safe(getattr(li, "property_plant_equipment", None)),
        })

    # SBC-adjusted (owner-earnings) FCF: reported FCF treats SBC as non-cash and
    # adds it back to OCF. Owner-earnings FCF subtracts it back out because SBC
    # is a real dilution cost to shareholders even when it isn't a cash outflow.
    # Falls back to reported FCF when SBC is not disclosed (e.g. some utilities).
    #
    # Buyback-netted (v3.21) — ports the core insight of Method E from
    # src/research_ideas/sw46/tragic_algebra.py (validated against Burry /
    # Cassandra-Unchained's published SBC framework: ADBE dE 88.2% vs their
    # 88.3%). The prior version charged 100% of gross SBC dollar-for-dollar
    # regardless of capital allocation — a company that fully repurchases its
    # own SBC-driven issuance (net share count flat or shrinking) was
    # penalized identically to a company diluting shareholders at the same
    # gross SBC level. Only the UNFUNDED portion is a real cost:
    #   unfunded_comp = max(0, SBC - buybacks)
    # Net buyers (buybacks >= SBC) get unfunded_comp = 0 — the buyback is
    # genuine capital return, not charged against owner earnings. Also
    # charges the cash tax withheld on RSU/PSU vesting (a real financing-
    # activity cash outflow FCF's operating-cash-flow basis doesn't capture),
    # estimated at _RSU_TAX_WITHHOLDING_RATE of gross SBC when FMP doesn't
    # expose a clean withholding line (no such line is currently fetched by
    # search_line_items, so this is always the estimate for now — same
    # fallback and rate tragic_algebra.py uses when its own cleaner FMP line
    # is unavailable).
    for row in rows:
        fcf = row["free_cash_flow"]
        sbc = row["stock_based_compensation"]
        buybacks = abs(row.get("common_stock_repurchased") or row.get("share_buyback") or 0.0)
        if fcf is not None and sbc is not None:
            sbc_abs = abs(sbc)
            unfunded_comp = max(0.0, sbc_abs - buybacks)
            cash_tax_withholding = sbc_abs * _RSU_TAX_WITHHOLDING_RATE
            row["fcf_owner_earnings"] = fcf - unfunded_comp - cash_tax_withholding
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


# How far reported FCF may exceed what earnings plus the D&A-minus-capex gap
# explain before the projection is capped. 25% headroom keeps the gate off
# ordinary timing noise — verified against MU, JPM, BABA and COST, none of
# which bind — while MELI's 4.6x excess does.
_CASH_CONVERSION_TOLERANCE = 1.25

# A deep cut is worth RECORDING but not acting on. Retaining <40% of the
# trailing margin was shipped as a suppression rule on 2026-09-02 and reverted
# the same day when held-out data inverted it:
#
#                       declined firings      Beta of what it dropped
#   original 141 dates   9 false / 0 correct          0.09
#   held-out 188 dates   0 false / 8 correct          0.90
#   combined             9 false / 8 correct          0.47  <- discriminates nothing
#
# The rule conflated two situations that share one symptom. For operating
# companies mid-ramp (UBER 5.4->1.3, DASH 8.4->2.7) a deep cut IS wrong — the
# trailing earnings base is near zero and the OE<=0 cascade owns that regime.
# But for financials a deep cut is the entire point: reported free cash flow is
# meaningless when deposit and lending flows dominate operating cash flow, and
# every firing the floor blocked on held-out data was one — MUFG raw 167%,
# CCB 86% and 77%, Ping An 43%, SoftBank, Ally, all correct firings. The
# original sample's banks happened to retain more than 40%, so it looked
# perfect on the data that chose it.
#
# Kept as an observation so the deep-cut population stays visible for a rule
# that can actually separate those two cases.
_DEEP_CUT_OBSERVATION_FRACTION = 0.40

# How far base IV may sit from the Street's own 12-month consensus before the
# run says so. Deliberately wide — a model that never disagreed with consensus
# would be worthless — but MELI's pre-fix 8.7x was not a disagreement, it was
# an error nothing objected to.
_CONSENSUS_DIVERGENCE_MULT = 3.0


#: A structural turnaround is recognised only when the latest margin clears the
#: window mean by at least this much. Below it the mean and the recent figure
#: tell the same story and there is nothing to correct.
_TURNAROUND_MIN_GAP = 0.03


def _turnaround_margin(series: list[dict], field: str = "free_cash_flow") -> Optional[dict]:
    """{mean_window, recent_two_year, margins} when the window describes a company
    that no longer exists, else None.

    The mirror of `_historical_cagr`'s recency guard, for margins. A five-year
    MEAN is the right base for a business whose margin wanders around a level.
    It is the wrong one for a one-directional turnaround: GE Vernova's FCF margin
    ran -6.8%, -2.1%, +1.3%, +4.9%, +9.75% across a window whose first three
    years are pre-spin carve-out financials, and the mean of that is 1.4% -- a
    DCF of $87 a share, about 2x the free cash flow management guides to
    (owner, 2026-09-21: "anchoring the model to distressed historical carve-out
    margins"). Averaging a trend reports where the company was.

    Deliberately narrow. ALL of:
      * at least four years in the window;
      * the window OPENS cash-burning (first margin < 0) and CLOSES cash-generative;
      * every year improves on the one before -- a single step back and the
        mean stands, because that is a wandering margin, not a turnaround;
      * the latest margin clears the mean by `_TURNAROUND_MIN_GAP`.
    The base then becomes the mean of the last TWO years: audited, recent, and
    still not the single best year. Guidance is never a baseline (owner,
    2026-09-20); a guided 25% is an overlay with its own acceptance, not this.
    """
    margins = [row[field] / row["revenue"] for row in series[-5:]
               if row.get(field) is not None and row.get("revenue")]
    if len(margins) < 4 or not (margins[0] < 0 < margins[-1]):
        return None
    if any(b <= a for a, b in zip(margins, margins[1:])):
        return None
    mean_w = statistics.mean(margins)
    if margins[-1] - mean_w < _TURNAROUND_MIN_GAP:
        return None
    return {"mean_window": mean_w, "recent_two_year": statistics.mean(margins[-2:]),
            "margins": [round(m, 6) for m in margins]}


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


def _median_positive_fcf_margin(
    series: list[dict], field: str = "free_cash_flow"
) -> Optional[float]:
    """Median of the POSITIVE per-year FCF margins over the last 5 years.

    SW50 cascade input (task #18): when the trailing mean margin is ≤ 0,
    the median of the positive years is the most defensible proxy for the
    business's demonstrated owner-earnings power — robust to one-off
    blow-up years, unlike the mean. Returns None when no year was
    positive; the caller then disables the DCF family rather than letting
    the FCF floor manufacture a fake near-zero anchor.
    """
    margins = []
    for row in series[-5:]:
        rev = row.get("revenue")
        fcf = row.get(field)
        if fcf is not None and rev and rev > 0:
            m = fcf / rev
            if m > 0:
                margins.append(m)
    if not margins:
        return None
    return statistics.median(margins)


#: Days of DSI expansion over the prior three years that trips
#: GATE_INVENTORY_STRESS. Owner-specified in the operational post-mortem's
#: Failure 4 ("When `dsi - dsi_3y_median > 25`").
_INVENTORY_STRESS_TRIGGER_DAYS = 25.0

#: Proportional markdown applied to `fcf_margin_base` when the trigger trips.
#: Owner-specified in the same place ("apply an immediate 15% markdown haircut
#: to `fcf_margin_base` before passing it to `_project_dcf`").
_INVENTORY_MARKDOWN_HAIRCUT = 0.15


def _inventory_stress_days(rows_newest_first: list[dict]) -> Optional[float]:
    """DSI expansion over the prior three years, in days, SIGNED.

    ``inventory / cost_of_revenue * 365`` for the most recent year, minus the
    median of the same ratio over the three years before it. Positive means
    inventory is taking longer to sell than it used to — the bullwhip shape the
    brief's Gate 1 exists for, where a retailer has shipped product into a
    channel that has stopped taking it and the reported margin still reflects
    the sales that were booked, not the markdowns that are coming.

    ORDER IS PART OF THE CONTRACT. `rows_newest_first[0]` is the CURRENT year
    and `[1:4]` are the three prior ones. The engine's own `series` is
    ASCENDING — `most_recent = series[-1]` — so the call site passes
    `series[::-1]`, and the parameter is named for the order rather than taking
    `rows` so that a call site reading `_inventory_stress_days(series)` looks
    wrong on its face. Getting this backwards is not a small error: it turns
    "inventory is building" into "inventory is draining" and fires the gate on
    exactly the names it should clear.

    Returns None when the current year's DSI is unmeasurable, or when fewer than
    two prior years measure. Two rather than one is deliberate: a median over a
    single prior year is a point comparison, and one restated inventory line —
    which is what a lease or a consolidation restatement does to a balance
    sheet — would then move the gate by the whole restatement.

    A NEGATIVE inventory is also unmeasurable, not merely small. It is not a
    quantity a warehouse can hold; it is what a contra account, a LIFO reserve
    or a mis-signed consolidation adjustment looks like once it reaches a row
    dict, and dividing it by cost of revenue produces a negative DSI that would
    subtract from the median and manufacture stress in a later year rather than
    this one. Guarding the numerator the same way the denominator is guarded
    costs one comparison and removes a way for the gate to fire on the wrong
    year.

    The result is signed and NOT clamped at zero, for the reason the
    reinvestment deduction gives: a number that can only ever be positive
    cannot distinguish "not stressed" from "stressed in the other direction",
    and the gate record is more useful saying that inventory days COMPRESSED by
    12 than saying nothing. The trigger is `> 25`, so the negative side can
    never fire it.
    """
    def _dsi(row: dict) -> Optional[float]:
        inv = _safe(row.get("inventory"))
        cogs = _safe(row.get("cost_of_revenue"))
        if inv is None or cogs is None or inv < 0 or cogs <= 0:
            return None
        return float(inv) / float(cogs) * 365.0

    if not rows_newest_first:
        return None
    current = _dsi(rows_newest_first[0])
    if current is None:
        return None
    past = [d for d in (_dsi(r) for r in rows_newest_first[1:4]) if d is not None]
    if len(past) < 2:
        return None
    return current - statistics.median(past)


def _scale_analyst_bands_to_cap(
    bands: Optional[dict], cap: float
) -> tuple[Optional[dict], Optional[float]]:
    """Bring an analyst growth band under the revenue-scale cap.

    Returns (bands, scale) with `scale` None when the cap does not bind.

    The tiered cap mutates `growth_base`, which the analyst-band path never
    reads — it takes g straight from the band. So any name with analyst
    coverage escaped the tier entirely and kept only the flat +40% clamp.
    MELI 2026-08-30 carried g = 40% on a $28.9bn revenue base against a 15%
    tier, compounding to $194.9bn of year-10 revenue.

    Scales the whole band rather than clipping each scenario, so the
    bear/base/bull spread the analyst dispersion actually expresses survives.
    This mirrors the CAGR path, where the cap binds the BASE and the scenario
    multipliers (0.55/1.00/1.50) still carry bull above it.
    """
    if not bands or cap <= 0:
        return bands, None
    base = bands.get("base")
    if not base or base <= cap:
        return bands, None
    scale = cap / base
    scaled = {
        k: (v * scale if k in ("bear", "base", "bull") else v)
        for k, v in bands.items()
    }
    return scaled, scale


def _shift_analyst_bands_to_floor(
    bands: Optional[dict], floor: float
) -> tuple[Optional[dict], Optional[float]]:
    """Raise an analyst growth band onto the CAGR gate's downside floor.

    Returns ``(bands, shift)`` with ``shift`` None when the floor does not bind.

    Named *shift*, not *scale*, because the operation is additive and the
    difference is load-bearing rather than cosmetic.
    :func:`_scale_analyst_bands_to_cap` multiplies, and multiplying is safe
    there because a cap only ever binds on a POSITIVE base. A floor only ever
    binds on a NEGATIVE one — it sits at ``min(CAGR, 0) − 5pp`` — and a
    proportional rewrite of a negative band is wrong in two ways an additive one
    is not:

      * the factor is ``floor / base``, and whenever the floor is itself
        positive (reachable for any ``CAGR > 15pp``, since the rewrite is
        ``max(g_ntm, CAGR − 15pp)``) that factor is NEGATIVE and flips the sign
        of every member. A −40% bear would publish as +30% and the ordering
        bear ≤ base ≤ bull would invert outright.
      * when both are negative the factor is positive, but a band that diverged
        this far usually straddles zero, and multiplying then compresses the
        positive bull toward zero while raising the negative bear. Dispersion
        survives only for the half of the band sharing the base's sign.

    Adding ``floor − base`` to every member moves the whole band by exactly the
    amount the base moved. Spread and ordering are preserved in both sign
    regimes, which is the property the ceiling path buys by scaling and cannot
    buy here. This is also why the two functions are separate rather than one
    function with a sign parameter: they are not two directions of one
    operation, they are two operations.
    """
    if not bands:
        return bands, None
    base = bands.get("base")
    if not isinstance(base, (int, float)) or base >= floor:
        return bands, None
    shift = floor - base
    shifted = {
        k: (v + shift
            if k in ("bear", "base", "bull") and isinstance(v, (int, float))
            else v)
        for k, v in bands.items()
    }
    return shifted, shift


def _projectable_fcf_margin_cap(
    series: list[dict],
) -> tuple[Optional[float], Optional[float], str]:
    """(cap, trailing_fcf_margin, basis) — the FCF margin that is repeatable.

    Reported free cash flow is only projectable to the extent it recurs. The
    owner-earnings identity says where the non-recurring part lives:

        FCF = net income + D&A - capex - change in working capital

    The first three terms are structural. The fourth is not — a working
    capital benefit only recurs while the balance sheet keeps growing, and at
    steady state it stops contributing. Projecting it flat for a decade
    capitalises a financing flow as if it were operating profit.

    MELI 2026-08-30 is the case that motivated this. Mercado Pago's float —
    customer deposits and credit-book movements running through operating cash
    flow — produced a reported FCF margin of 37.3% against a 6.9% net margin.
    Held flat over ten years of compounding revenue it projected $59.6bn of
    annual free cash flow in year 10, more than Alphabet earns today, from a
    company that earned $2.0bn. Base IV came out at $18,401 against a $1,966
    spot.

    **Two bases, and the direct measurement wins.** Where the cash-flow
    statement discloses the working-capital line, subtract it: that IS the
    non-recurring term, measured rather than inferred. Where it does not,
    fall back to what earnings plus the D&A-less-capex gap support, which
    approximates the same quantity from the other side.

    Backtested against realised owner earnings over 8 gate firings
    (`src/memory/gate_backtest.py`), mean absolute error:

        ex-working-capital   5.96pp
        raw (no cap)         7.21pp
        earnings cap         8.11pp

    and the split is structural — on businesses that stayed healthy the
    earnings cap was off by 9.2pp against ex-WC's 2.1pp. It is a haircut, not
    an estimator, and it won only where the business subsequently
    deteriorated. Hence ex-WC leads and the earnings cap backstops.

    Returns (None, None, "") when the window cannot support either basis, in
    which case the caller leaves the margin alone — this gate only ever caps
    downward, and only when it can say why.
    """
    rev = fcf = ni = capex = dep = 0.0
    have_ni = have_fcf = False
    wc_margins: list[float] = []
    for row in series[-5:]:
        r = row.get("revenue")
        if not r or r <= 0:
            continue
        rev += r
        if row.get("free_cash_flow") is not None:
            fcf += row["free_cash_flow"]
            have_fcf = True
            wc = row.get("change_in_working_capital")
            if wc is not None:
                wc_margins.append((row["free_cash_flow"] - wc) / r)
        if row.get("net_income") is not None:
            ni += row["net_income"]
            have_ni = True
        capex += abs(row.get("capital_expenditure") or 0.0)
        dep += row.get("depreciation_and_amortization") or 0.0
    if rev <= 0 or not have_fcf:
        return None, None, ""
    trailing = fcf / rev

    # Preferred: the working-capital term as disclosed. Requires most of the
    # window to carry the line — one year of it is a data point, not a basis.
    if len(wc_margins) >= 3:
        return (sum(wc_margins) / len(wc_margins)), trailing, "ex-working-capital"

    # Fallback: only the EXCESS of D&A over capex is a durable add-back. Where
    # capex exceeds D&A the business is reinvesting and net margin already
    # reflects the depreciation of that spend, so subtracting again would
    # double-count.
    if not have_ni:
        return None, None, ""
    return ((ni + max(0.0, dep - capex)) / rev), trailing, "earnings-supported"


def _analyst_revenue_growth(
    estimates: list, revenue_base: float, fx_rate: float = 1.0
) -> Optional[float]:
    if not estimates or not revenue_base:
        return None
    rev_est = _safe(estimates[0].revenue_avg)
    if rev_est is None or rev_est <= 0:
        return None
    # FMP returns estimates in the company's REPORTING currency while
    # revenue_base is already converted to the target currency. Multiply by
    # the same fx_rate applied to the historical series (task #26: raw-CNY
    # estimates ÷ USD base produced +700% phantom growth on China ADRs).
    _fxm = fx_rate if (fx_rate and fx_rate > 0) else 1.0
    return ((rev_est * _fxm) / revenue_base) - 1


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


#: Accounting lines that are not businesses. FMP's product segmentation hands
#: back "Consolidation, Eliminations" as though it were a division: for
#: Phillips 66 that line carried $55.8bn, 61% of the segmentation's total, and
#: the SOTP leg valued it at 3x revenue -- $167.5bn of "value" in an
#: intersegment netting row.
#: How much of a company's segmented revenue must carry a multiple before the
#: SOTP is allowed to speak for the whole company.
_SOTP_MIN_PRICED_REVENUE: float = 0.85

_SEGMENT_NON_BUSINESS: tuple[str, ...] = (
    "consolidation", "elimination", "intersegment", "corporate and other",
    "unallocated", "reconciling", "adjustments and other",
)


def _is_non_business_segment(name: str) -> bool:
    n = (name or "").lower()
    return any(k in n for k in _SEGMENT_NON_BUSINESS)


#: Energy segment types, matched before the generic keyword table so a refinery
#: is never read as something else. Marathon's "Refining And Marketing" matched
#: the generic "marketing" rule and was valued as ADVERTISING at 6.5x revenue --
#: $807.6bn of enterprise value, six times the company's own.
#:
#: Order matters: the most specific phrase wins.
_ENERGY_SEGMENT_KEYWORDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("refining and marketing", "refining & marketing", "refining",
      "refinery", "refined product"),                         "refining"),
    (("midstream", "pipeline", "gathering and processing",
      "logistics and storage", "terminalling"),               "midstream"),
    (("petrochemical", "chemical", "olefins", "polyolefins"), "chemicals"),
    (("renewable diesel", "renewable fuel", "sustainable aviation",
      "biodiesel", "renewable naphtha", "neat saf"),          "renewable_fuels"),
    (("ethanol", "distillers grain"),                         "ethanol"),
    (("marketing and specialties", "marketing & specialties",
      "m&s", "fuel marketing", "retail marketing"),           "fuel_marketing"),
    (("exploration and production", "upstream", "e&p"),       "upstream"),
    (("oilfield", "well services", "drilling", "offshore rig"), "oilfield_services"),
)

#: What each energy segment type is worth, as the owner set it (2026-09-20):
#: a through-cycle EV/EBITDA band per business type, NOT a revenue multiple.
#:
#: Segment EBITDA is not disclosed -- the SEC extractor returns profit=None for
#: all three refiners -- so it is ESTIMATED as segment revenue x the margin its
#: own peer basket implies (ev_revenue / ev_ebitda, US large cohort, comps
#: refreshed 2026-09-19). Both steps are recorded on the leg, because an
#: estimated EBITDA presented as a disclosed one is the kind of number this
#: whole exercise exists to stop.
#:
#: `margin_source` names the basket the margin came from. `None` multiple means
#: the owner has not set a band for that type yet: those segments are priced at
#: nothing and named on the leg, so a partial SOTP cannot masquerade as a whole
#: one.
#: Where in each band the segment is valued. The owner set the bands as ranges
#: and then chose the top of them (2026-09-20), so this is named rather than
#: buried in an expression: "high" is a position, and a later decision to move
#: to the midpoint is a one-word change with every leg's trace recording which
#: position produced it.
_SEGMENT_BAND_POSITION: str = "high"


def _band_multiple(band: tuple[float, float], position: str = _SEGMENT_BAND_POSITION) -> float:
    lo, hi = float(band[0]), float(band[1])
    if position == "low":
        return lo
    if position == "mid":
        return (lo + hi) / 2.0
    return hi


_SEGMENT_EBITDA_MULTIPLES: dict[str, dict] = {
    "refining":        {"band": (5.0, 6.5),  "margin": 0.0886,
                        "margin_source": "Oil & Gas Refining & Marketing (US, large, n=8)"},
    # Re-based by the owner 2026-09-21 on the corrected market (12.18x);
    # was 9-12x, then 14.1x. Kept in step with src/data/dynamic_multiples.py.
    "midstream":       {"band": (9.744, 14.616), "margin": 0.3595,
                        "margin_source": "Oil & Gas Midstream (US, large, n=10)"},
    # Re-based by the owner 2026-09-21 on the corrected market (6.58x);
    # was 7-9x, then 5.2x.
    "chemicals":       {"band": (5.264, 7.896), "margin": 0.0683,
                        "margin_source": "Chemicals (US, large, n=7)"},
    # Owner-set 2026-09-20. Above refining because the cash flows are steadier,
    # below midstream because none of them is fee-based infrastructure. The
    # margins here are ESTIMATES, not peer baskets -- FMP has no clean
    # comparable set for any of the three -- and `margin_source` says so, so
    # nobody reads them later as measured. Fuel marketing is deliberately thin:
    # most of Phillips 66's $85.9bn M&S line is fuel bought to be resold.
    "renewable_fuels": {"band": (6.0, 8.0),  "margin": 0.10,
                        "margin_source": "owner estimate (no peer basket)"},
    "ethanol":         {"band": (4.0, 6.0),  "margin": 0.06,
                        "margin_source": "owner estimate (no peer basket)"},
    "fuel_marketing":  {"band": (6.0, 8.0),  "margin": 0.02,
                        "margin_source": "owner estimate (no peer basket); thin by "
                                         "construction -- largely resold fuel"},
    "upstream":        {"band": None, "margin": None, "margin_source": None},
    "oilfield_services": {"band": None, "margin": None, "margin_source": None},
}


def _reconcile_segment_ebitda(parts: list[dict], company_ebitda: Optional[float]) -> list[dict]:
    """Scale estimated segment EBITDA so the parts sum to the whole.

    A peer basket's margin is a PURE-PLAY margin, and a segment's revenue is
    not a pure-play company's revenue: it carries intersegment transfers and
    low-margin resale. Phillips 66's midstream line took the 35.9% margin of
    standalone midstream operators onto $21.2bn of largely NGL marketing
    revenue, and the segment EBITDA estimates summed to $16.6bn against $9.8bn
    the company actually reported -- 70% too high, in a leg carrying 40% of the
    blend.

    A sum-of-the-parts is an argument about MIX, not about level: the level is
    already known from the income statement. So the mix each margin implies is
    kept and the level is reconciled to the company's own normalised EBITDA.
    Segments held at carrying value are not scaled -- they are not EBITDA.
    """
    if not company_ebitda or company_ebitda <= 0:
        return parts
    est = [p for p in parts if p.get("basis") == "ev_ebitda" and p.get("ebitda_estimated")]
    raw_sum = sum(p["ebitda_estimated"] for p in est)
    if raw_sum <= 0:
        return parts
    scaler = float(company_ebitda) / raw_sum
    for p in est:
        p["ebitda_unreconciled"] = p["ebitda_estimated"]
        p["ebitda_estimated"] = p["ebitda_estimated"] * scaler
        p["ev"] = p["ebitda_estimated"] * p["multiple"]
        p["reconciliation_scaler"] = scaler
    return parts


def _classify_energy_segment(name: str, member: str = "") -> Optional[str]:
    """The energy business type behind a segment label, or None.

    Reads the XBRL member alongside the label: Valero's note breaks its
    refining segment into product rows ("Gasoline and Blendstocks",
    "Distillates") that say nothing about the business, while the member they
    all carry -- vlo_RefiningMember -- says exactly which segment they are.
    """
    hay = f"{member or ''} {name or ''}".lower().replace("_", " ")
    for keywords, seg_type in _ENERGY_SEGMENT_KEYWORDS:
        if any(k in hay for k in keywords):
            return seg_type
    return None


def _dynamic_norm_multiple(peer: dict, live_field: str, ticker: Optional[str],
                           leg: str) -> tuple[Optional[float], Optional[str]]:
    """The industry's through-cycle multiple for a normalised leg, or (None, None).

    Owner decision 2026-09-21: the normalised legs price through-cycle earnings,
    so they take the basket's through-cycle multiple -- updated automatically
    each quarter -- instead of the trailing peer median, which is the wrong
    basis for normalised earnings (the refining audit's finding). The basket is
    the one the live peer multiple itself resolved from, read off its recorded
    provenance, so the dynamic multiple and the peer set can never disagree
    about which industry this is. Falls back to the peer median whenever there
    is no dynamic multiple, and records every use so the Model Accuracy log can
    show the multiple actually reached a valuation.
    """
    try:
        from src.data import dynamic_multiples as _dm
        if not _dm.enabled():
            return None, None
        b = ((peer or {}).get("_comp_basis") or {}).get(live_field) or {}
        key, ex, level = b.get("key"), b.get("exchange"), b.get("basis")
        norm_field = _dm.LIVE_TO_NORM.get(live_field)
        if not (key and ex and norm_field and level in ("industry", "sector")):
            return None, None
        row = _dm.current_multiple(ex, level, key, norm_field)
        if not row or not isinstance(row.get("multiple"), (int, float)) or row["multiple"] <= 0:
            return None, None
        if ticker:
            _dm.record_usage(ticker, leg, ex, level, key, norm_field,
                             float(row["multiple"]), row.get("effective_at"))
        src = (f"dynamic through-cycle {norm_field} ({ex} {level}: {key}; "
               f"{'owner-pinned' if row.get('pinned') else 'auto'} "
               f"{str(row.get('effective_at') or '')[:10]})")
        return float(row["multiple"]), src
    except Exception:                                          # noqa: BLE001
        return None, None


def _sotp_parts(segments: dict[str, float], tier: str = "default",
                members: Optional[dict[str, str]] = None,
                assets: Optional[dict[str, float]] = None,
                company_ebitda: Optional[float] = None) -> list[dict]:
    """The per-segment working behind a SOTP: revenue, type, multiple, EV.

    Exists so the leg can be checked. `SOTP (segments)` published $2,621 a
    share for Marathon -- six times its quote, an implied EV of $832.9bn -- and
    `leg_inputs` carried only the aggregate, so nothing in the record showed
    that the figure came from multiplying refining REVENUE by a multiple meant
    for businesses with margins refiners do not have.
    """
    parts: list[dict] = []
    members = members or {}
    assets = assets or {}
    for seg_name, seg_rev in (segments or {}).items():
        if seg_rev is None or seg_rev <= 0:
            # An equity-accounted segment reports no revenue to consolidate --
            # Phillips 66's Chemicals line is the CPChem 50/50 JV and prints
            # zero -- so a revenue multiple misses it entirely. The owner's
            # decision (2026-09-20) is to carry it at the book value the filing
            # discloses, on a basis the leg names rather than blends silently.
            _ca = assets.get(seg_name)
            if _ca and _ca > 0:
                parts.append({"segment": seg_name, "revenue": 0.0,
                              "type": _classify_energy_segment(
                                  seg_name, members.get(seg_name, "")) or "equity_method",
                              "basis": "carrying_value",
                              "multiple": None, "ev": float(_ca),
                              "carrying_value": float(_ca),
                              "note": "equity-accounted: held at the segment assets "
                                      "the filing discloses, not a multiple"})
            continue
        if _is_non_business_segment(seg_name):
            parts.append({"segment": seg_name, "revenue": float(seg_rev),
                          "type": "non_business", "basis": "excluded",
                          "multiple": None, "ev": 0.0,
                          "note": "an accounting line, not a business"})
            continue
        e_type = _classify_energy_segment(seg_name, members.get(seg_name, ""))
        if e_type:
            cfg = _SEGMENT_EBITDA_MULTIPLES.get(e_type) or {}
            band, margin = cfg.get("band"), cfg.get("margin")
            if not band or not margin:
                # The owner has not set a band for this type. Priced at
                # nothing and named, rather than valued on a guess.
                parts.append({"segment": seg_name, "revenue": float(seg_rev),
                              "type": e_type, "basis": "ev_ebitda",
                              "multiple": None, "ev": 0.0,
                              "note": "no owner-set EV/EBITDA band for this "
                                      "segment type -- unpriced"})
                continue
            # An owner-ACCEPTED dynamic multiple wins (Phase 2): the engine in
            # src/data/dynamic_multiples.py proposes a regime-adjusted multiple
            # with its derivation, and only an accepted one reaches a
            # valuation. Without one, the static band position stands -- and the
            # part says which of the two it is.
            mult, mult_source, dyn_band = _band_multiple(band),                 f"static band ({_SEGMENT_BAND_POSITION} end)", None
            band_rationale = None
            try:
                from src.data import dynamic_multiples as _dm
                band_rationale = (_dm.SEGMENT_BASELINES.get(e_type) or {}).get("band_rationale")
                _acc = _dm.accepted(e_type)
                if _acc and isinstance(_acc.get("multiple"), (int, float)):
                    mult = float(_acc["multiple"])
                    mult_source = f"dynamic, accepted {_acc.get('accepted_at')}"
                    dyn_band = (_acc.get("derivation") or {}).get("band")
            except Exception:                              # noqa: BLE001
                pass
            seg_ebitda = float(seg_rev) * float(margin)
            parts.append({"segment": seg_name, "revenue": float(seg_rev),
                          "type": e_type, "basis": "ev_ebitda",
                          "ebitda_margin": float(margin),
                          "ebitda_margin_source": cfg.get("margin_source"),
                          "ebitda_estimated": seg_ebitda,
                          "band": list(dyn_band or band),
                          "band_position": _SEGMENT_BAND_POSITION,
                          "multiple": float(mult), "multiple_source": mult_source,
                          "band_rationale": band_rationale,
                          "ev": seg_ebitda * mult})
            continue
        seg_type, mult = _classify_segment(seg_name, tier=tier)
        parts.append({"segment": seg_name, "revenue": float(seg_rev),
                      "type": seg_type, "basis": "ev_revenue",
                      "multiple": float(mult),
                      "ev": float(seg_rev) * float(mult)})
    return _reconcile_segment_ebitda(parts, company_ebitda)


def _segment_sotp_block(base_scenario: dict, shares: Optional[float],
                        currency: Optional[str]) -> Optional[dict]:
    """The segment SOTP as the report and the PDF render it, or None.

    Reads the leg's own trace rather than recomputing, so what a reader sees is
    exactly what the valuation used -- including the estimated EBITDA behind
    every multiple, which is the part a segment SOTP most easily hides.
    """
    leg = ((base_scenario or {}).get("leg_inputs") or {}).get("SOTP (segments)")
    if not isinstance(leg, dict):
        return None
    parts = leg.get("segments") or []
    if not parts:
        return None
    total_ev = float(leg.get("metric_value") or 0.0)
    rows = []
    for p in parts:
        ev = float(p.get("ev") or 0.0)
        rows.append({
            "segment": p.get("segment"),
            "type": p.get("type"),
            "basis": p.get("basis"),
            "revenue": p.get("revenue"),
            "ebitda_margin": p.get("ebitda_margin"),
            "ebitda_margin_source": p.get("ebitda_margin_source"),
            "ebitda": p.get("ebitda_estimated"),
            "band": p.get("band"),
            "band_position": p.get("band_position"),
            "multiple_source": p.get("multiple_source"),
            "band_rationale": p.get("band_rationale"),
            "ebitda_unreconciled": p.get("ebitda_unreconciled"),
            "reconciliation_scaler": p.get("reconciliation_scaler"),
            "multiple": p.get("multiple"),
            "ev": ev,
            "share_of_ev": (ev / total_ev) if total_ev > 0 else None,
            "note": p.get("note"),
        })
    # The check the owner asked to be standing (2026-09-20): a sum of the parts
    # has to sum. Two ways it can fail to, and they fail differently:
    #
    #   share_of_ev -- the parts' shares of enterprise value must come to 100%.
    #     This is arithmetic, and a miss means a negative or unpriced EV slipped
    #     into the total rather than being excluded from it.
    #   revenue priced -- the fraction of segmented revenue that carries a
    #     multiple. Arithmetic cannot see this one: Phillips 66's parts summed
    #     to exactly 100% of a total that covered 51% of the company.
    #
    # Both are reported, and the reminder names whichever fell short, because a
    # SOTP that quietly values half a company reads exactly like one that does
    # not.
    _share_sum = sum(r["share_of_ev"] for r in rows if r.get("share_of_ev") is not None)
    _priced_rev = leg.get("priced_share_of_revenue")
    _reminders = []
    if rows and abs(_share_sum - 1.0) > 1e-6:
        _reminders.append(
            f"Segment shares of enterprise value sum to {_share_sum:.1%}, not 100%.")
    if isinstance(_priced_rev, (int, float)) and _priced_rev < 0.999:
        _unpriced = [r["segment"] for r in rows
                     if r.get("multiple") is None and r.get("basis") != "carrying_value"]
        _reminders.append(
            f"Only {_priced_rev:.0%} of segmented revenue carries a multiple"
            + (f"; unpriced: {', '.join(str(u) for u in _unpriced)}." if _unpriced else "."))
    return {
        "currency": currency,
        "segments": rows,
        "total_ev": total_ev,
        "value_per_share": leg.get("value"),
        "shares": shares,
        "priced_share_of_revenue": leg.get("priced_share_of_revenue"),
        "checks": {
            "share_of_ev_sum": _share_sum,
            "share_of_ev_sums_to_100": bool(rows) and abs(_share_sum - 1.0) <= 1e-6,
            "revenue_fully_priced": bool(isinstance(_priced_rev, (int, float))
                                         and _priced_rev >= 0.999),
            "reminders": _reminders,
        },
        "basis_note": ("Segment EBITDA is estimated as segment revenue x the margin "
                       "its peer basket implies; it is not a disclosed figure. "
                       "Multiples are owner-set through-cycle EV/EBITDA bands, "
                       f"applied at the {_SEGMENT_BAND_POSITION} end of each band "
                       "unless the owner has accepted a dynamic multiple for the "
                       "segment type, which the row then says."),
    }


def _sotp_enterprise_value(
    segments: dict[str, float],
    tier: str = "default",
    members: Optional[dict[str, str]] = None,
    assets: Optional[dict[str, float]] = None,
    company_ebitda: Optional[float] = None,
) -> Optional[float]:
    """Sum per-segment EV using tier-adjusted type multiples.

    Returns aggregate EV across all segments, or None when input is empty /
    all-zero. Multiples vary by ``tier`` — see ``_SEGMENT_MULTIPLE_TIERS``.
    """
    if not segments:
        return None
    total_ev = sum(p["ev"] for p in _sotp_parts(segments, tier=tier, members=members,
                                                assets=assets, company_ebitda=company_ebitda))
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


# ── Published SOTP tables ────────────────────────────────────────────────
#
# A third SOTP shape, distinct from the two already here:
#
#   SOTP (segments)  — segment REVENUE x a generic multiple, from FMP
#                      revenue-product-segmentation. Returns nothing for
#                      SGX, so it can never fire on a Singapore name.
#   SOTP (analyst)   — segment revenue_fwd / EBIT x P/E or EV/Rev, the
#                      GS Exhibit-17 shape.
#   SOTP (published) — segment VALUATIONS stated directly, which is what
#                      Singapore conglomerate notes actually print.
#
# Keppel's note gives a complete table: Infrastructure S$12,000mn at 15x PE,
# Non-core S$4,716mn at net book value, Real Estate S$3,686mn at a 30%
# discount to book, Asset Management S$3,000mn at 20x PE, listed entities
# marked to market, private funds marked to valuation — then group net debt
# and corporate costs deducted. There is no revenue or EBIT line to
# reconstruct from, and inventing one would discard the analyst's per-segment
# basis, which is the whole content of the table.
#
# Values are in millions of the reporting currency, matching how the notes
# print them. Seeded by hand; the same rows are what a future extractor
# should populate, which is why each carries its own source and vintage.
_SGX_SOTP_TABLES: dict[str, dict] = {
    # Phillip Securities Research, Keppel Ltd, 12 Aug 2025.
    # Segments sum to S$20,404mn = S$10.70/share.
    "BN4.SI": {
        "currency": "SGD",
        "as_of": "2025-08-12",
        "source": "Phillip Securities Research (Keppel)",
        "segments": [
            {"name": "Infrastructure",    "value_mn": 12000.0, "basis": "15x PE"},
            {"name": "Non-core",          "value_mn":  4716.0, "basis": "net book value"},
            {"name": "Real Estate",       "value_mn":  3686.0, "basis": "30% discount to book"},
            {"name": "Asset Management",  "value_mn":  3000.0, "basis": "20x PE"},
            {"name": "Listed entities",   "value_mn":  2923.0, "basis": "mark to market"},
            {"name": "Private funds",     "value_mn":  1532.0, "basis": "mark to valuation"},
            {"name": "Connectivity",      "value_mn":   600.0, "basis": "15x PE"},
        ],
        "adjustments": [
            {"name": "Group net debt",    "value_mn": -7913.0, "basis": "net debt"},
            {"name": "Corporate cost",    "value_mn":  -140.0, "basis": "corporate activities"},
        ],
    },
}


def _compute_published_sotp(ticker: str, shares: float):
    """Per-share equity value from a published SOTP table.

    Returns (value_per_share, table, provenance) or None when no table
    exists for the ticker or the share count is unusable. Deliberately does
    NOT fall back to a computed SOTP — a missing table means the method is
    unavailable, and the blend renormalises onto the other methods rather
    than substituting a different methodology under the same name.
    """
    tbl = _SGX_SOTP_TABLES.get((ticker or "").upper())
    if not tbl or not shares or shares <= 0:
        return None
    segs = tbl.get("segments") or []
    if not segs:
        return None
    total_mn = sum(_safe(x.get("value_mn")) or 0.0 for x in segs)
    total_mn += sum(_safe(x.get("value_mn")) or 0.0
                    for x in (tbl.get("adjustments") or []))
    if total_mn <= 0:
        return None
    value_ps = (total_mn * 1e6) / shares
    prov = [f"{len(segs)} segments",
            f"{tbl.get('source', 'sell-side')} ({tbl.get('as_of', 'n/a')})"]
    return round(value_ps, 4), tbl, prov


# ── SOTP (analyst) — GS-style segment P/E + EV/Rev SOTP ─────────────────────
# Mirrors sell-side SOTP tables (e.g. GS "Navigating China Internet"
# Exhibit 17, Meituan, 10 Aug 2026): each segment is valued at the higher
# of (a) forward EBIT x (1 - tax) x P/E and (b) forward revenue x EV/Rev;
# segments with no positive EBIT / no P/E fall to EV/Rev, with the keyword-
# classified multiple as last resort. Associates/investments and net cash
# are added as separate lines and a holding-company discount is applied to
# NAV. Pure function of the assumptions dict — deterministic by construction
# (no sampling, no time-dependent inputs).

def _refresh_sotp_net_cash(
    assumptions: dict,
    net_debt: Optional[float],
) -> tuple[dict, Optional[str]]:
    """Restate a SOTP assumptions dict's ``net_cash`` from this run's balance
    sheet. Returns ``(assumptions, flag)``; the input is never mutated.

    ``net_cash`` on an assumptions dict is a captured fact, and both sources
    of it go stale or disagree with the rest of the run:

    * the snapshot artifact freezes it -- 09618.HK carried an August capture
      of cash-minus-debt, RMB75.8bn of short-term investments short of the
      live figure, and re-anchored its segment revenue every run while the
      cash line never moved;
    * the live extractor nets short-term investments for every sector,
      where the engine's own net debt applies the sector guard (an insurer's
      investment book is not spare cash).

    The engine already holds this run's net debt, sector-guarded and FX
    converted into the reporting currency, so the SOTP uses that and its cash
    line agrees with every EV-based method in the same run.

    FX-safe by construction: the engine divides by ``fx_usd_to_reporting``
    here and ``_sotp_analyst_style`` multiplies the result by the same rate,
    so the round trip cancels and net cash contributes exactly
    ``-net_debt / shares`` in the reporting currency at any rate.
    """
    if not isinstance(assumptions, dict) or not isinstance(net_debt, (int, float)):
        return assumptions, None
    fx = _safe(assumptions.get("fx_usd_to_reporting")) or 1.0
    if fx <= 0:
        return assumptions, None
    live = -float(net_debt) / fx
    stated = _safe(assumptions.get("net_cash"))
    out = dict(assumptions)
    out["net_cash"] = live
    out["_net_cash_source"] = "engine_net_debt"
    if stated is not None:
        out["_net_cash_stated"] = stated
    if stated is None or abs(live - stated) <= 0.01 * max(abs(stated), 1.0):
        return out, None
    return out, (
        f"SOTP (analyst): net cash restated to this run's balance sheet, "
        f"${live / 1e9:.1f}bn vs ${stated / 1e9:.1f}bn on the stored "
        f"assumptions ({(live - stated) / 1e9:+.1f}bn)"
    )


def _pin_sotp_holdco_discount(
    ticker: str,
    assumptions: dict,
) -> tuple[dict, Optional[str]]:
    """Pin the SOTP's holdco discount to the company's curated value.

    The discount is the one SOTP input the research LLM re-decides every run,
    and it is a structural view of the parent, not a fresh judgement: the same
    company came out at 25% on its ADR line in August and 15% on its HK line
    in September, worth about HK$21 a share. ``curated_holdco_discount``
    resolves one value per company across listings; a company neither source
    covers keeps whatever the run produced.

    Returns ``(assumptions, flag)``; the input is never mutated.
    """
    if not isinstance(assumptions, dict):
        return assumptions, None
    try:
        from src.agents.analysis.sotp_snapshot import curated_holdco_discount
        curated = curated_holdco_discount(ticker)
    except Exception:
        curated = None
    if curated is None:
        return assumptions, None
    pct, source = curated
    stated = _safe(assumptions.get("holdco_discount_pct"))
    if stated is not None and abs(stated - pct) < 1e-9:
        return assumptions, None
    out = dict(assumptions)
    out["holdco_discount_pct"] = pct
    out["_holdco_source"] = source
    if stated is not None:
        out["_holdco_stated"] = stated
    return out, (
        f"SOTP (analyst): holdco discount pinned to {pct:.0%} from "
        f"{source} (this run's research said "
        f"{stated:.0%})" if stated is not None else
        f"SOTP (analyst): holdco discount {pct:.0%} from {source}"
    )


def _sotp_nonoperating_addback(
    assumptions: Optional[dict],
    net_debt: Optional[float] = None,
) -> tuple[float, float, str]:
    """``(associates, net_cash, basis)`` — the two non-operating items an SOTP
    NAV carries on top of the sum of the segment values.

    ── WHY THIS IS A FUNCTION AND NOT THREE INLINE LINES ──────────────────────
    It used to be three inline lines inside ``_sotp_analyst_style``, sitting
    BELOW that function's ``if not rows: return None`` guard. That placement had
    a consequence nobody chose: when the segment narrative rows failed to
    extract — every segment present but none carrying a usable forward revenue —
    the guard returned ``None`` and the add-back was computed by nobody, so it
    was discarded. On BABA that is $22.3bn of equity-method associates and
    $68bn of net cash, dropped from the run because a prose extractor could not
    find a revenue figure. The owner named it a severe bug on 2026-09-18 and
    said to fix it unconditionally.

    Lifting it out is the fix. Both the happy path and the degraded path now
    call the same function, so there is exactly one place that decides what the
    add-back is, and no ordering inside a table builder can silently delete it
    again.

    ── WHAT THE CALLER MAY AND MAY NOT DO WITH THE RESULT ────────────────────
    ``associates`` is genuinely additive to a DCF equity value: equity-method
    investees are consolidated neither in revenue nor in the operating income
    that FCF is built from, so their value is nowhere in the projection.

    ``net_cash`` is NOT additive to a DCF equity value, and adding it is a
    double count. ``_project_dcf``'s bridge is
    ``equity_value = pv_sum + pv_tv - (net_debt or 0.0)``, so a name with
    negative net debt has already had its net cash added by the time anything
    downstream sees an equity value. Both figures are returned anyway, because
    an SOTP NAV needs both — but the ``basis`` string says which is which, and
    any caller that adds this to an already-netted equity value must take
    ``associates`` alone.

    Returns ``(0.0, 0.0, "...")`` rather than raising on missing or malformed
    input, so a caller cannot turn a research gap into a failed run.
    """
    a = assumptions if isinstance(assumptions, dict) else {}
    associates = _safe(a.get("associates_investments")) or 0.0
    net_cash = _safe(a.get("net_cash"))
    if net_cash is None:
        # Negative net debt IS net cash. Zero (not None) when the name is a net
        # borrower, because an SOTP NAV subtracts debt once, in the bridge.
        net_cash = -net_debt if (net_debt is not None and net_debt < 0) else 0.0
        if net_debt is None:
            _nc_src = "none (net debt not supplied)"
        elif net_cash:
            _nc_src = "derived from net_debt"
        else:
            _nc_src = "none (net borrower)"
    else:
        _nc_src = "assumptions['net_cash']"
    basis = (
        f"associates ${associates / 1e9:+.2f}bn from "
        f"assumptions['associates_investments']; "
        f"net cash ${net_cash / 1e9:+.2f}bn from {_nc_src}"
    )
    return associates, net_cash, basis


def _sotp_analyst_style(
    assumptions: dict,
    shares: float,
    net_debt: Optional[float] = None,
    fx_to_reporting: float = 1.0,
    tier: str = "default",
) -> Optional[dict]:
    """GS-style analyst SOTP; see comment above for methodology.

    ``assumptions`` schema (mirrors SOTPAssumptionsOutput):
        segments: [{name, revenue_fwd, ebit?, unit_economics?{volume_annual,
                    profit_per_unit, fx_to_usd?}, tax_rate?, pe_multiple?,
                    ev_rev_multiple?, ev_ebit_multiple?, rationale?}]
        default_tax_rate?, holdco_discount_pct?, associates_investments?,
        net_cash?, fx_usd_to_reporting?

    Returns an Exhibit-17-shaped dict (rows + nav + holdco + final +
    per_share_reporting), or None when preconditions fail.

    THREE outcomes, not two, since 2026-09-18:
      * a complete table — ``degraded_no_segments`` False, ``per_share_reporting``
        a number, safe to publish and to grade;
      * a DEGRADED table — segments were supplied but none carried a usable
        forward revenue. ``rows`` is empty, ``per_share`` and
        ``per_share_reporting`` are **None**, ``degraded_no_segments`` is True,
        and ``associates`` / ``net_cash`` / ``nav`` still carry the balance-sheet
        add-back that used to be thrown away here. Never publish a per-share
        value from it and never grade it against a sell-side reference;
      * None — no assumptions, no shares, no segments at all, or a zero-row
        extraction with no add-back to preserve.
    """
    if not assumptions or not shares or shares <= 0:
        return None
    segments = assumptions.get("segments") or []
    if not segments:
        return None
    # None check, not truthiness: tax 0.0 is a valid researched value
    # (EV/EBIT-style multiples apply to pre-tax EBIT — GS AMZN replication).
    _dt = assumptions.get("default_tax_rate")
    default_tax = float(_dt) if _dt is not None else 0.15

    rows: list[dict] = []
    total_seg_value = 0.0
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        rev = _safe(seg.get("revenue_fwd"))
        if rev is None or rev <= 0:
            continue
        tax = _safe(seg.get("tax_rate"))
        tax = default_tax if tax is None else tax
        ebit = _safe(seg.get("ebit"))
        if ebit is None:
            ue = seg.get("unit_economics") or {}
            vol = _safe(ue.get("volume_annual"))
            ppu = _safe(ue.get("profit_per_unit"))
            if vol is not None and ppu is not None:
                ebit = vol * ppu * float(ue.get("fx_to_usd") or 1.0)
        if ebit is None:
            margin = _safe(seg.get("ebit_margin"))
            if margin is not None:
                ebit = rev * margin
        pe = _safe(seg.get("pe_multiple"))
        evrev = _safe(seg.get("ev_rev_multiple"))
        evebit = _safe(seg.get("ev_ebit_multiple"))

        # Sell-side convention: anchor on the higher of the P/E-on-NOPAT and
        # EV/Rev paths (GS tables show both columns and effectively adopt the
        # higher anchor per segment).
        anchors: list[tuple[str, float, float]] = []
        if pe and pe > 0 and ebit is not None and ebit > 0:
            anchors.append(("P/E", pe, ebit * (1.0 - tax) * pe))
        if evrev and evrev > 0:
            anchors.append(("EV/Rev", evrev, rev * evrev))
        # EV/EBIT — the basis the research layer states as "8x EV/EBITDA" or
        # "6x EV/EBIT". Applied to pre-tax segment EBIT, unlevered like the
        # EV/Rev path (the docstring's GS AMZN replication uses tax_rate 0 for
        # exactly this). Only ever set when the stated basis was one of those,
        # so it never competes with a P/E or EV/Rev the research layer gave.
        if evebit and evebit > 0 and ebit is not None and ebit > 0:
            anchors.append(("EV/EBIT", evebit, ebit * evebit))
        if not anchors:
            _, mult = _classify_segment(str(seg.get("name", "")), tier=tier)
            anchors.append(("EV/Rev (fallback)", mult, rev * mult))
        method, mult, value = max(anchors, key=lambda a: a[2])

        total_seg_value += value
        rows.append({
            "name":          str(seg.get("name", "segment")),
            "revenue_fwd":   rev,
            "ebit":          ebit,
            "method":        method,
            "multiple":      mult,
            "value":         value,
            "implied_evrev": value / rev if rev else None,
            "rationale":     str(seg.get("rationale", "")),
        })

    # THE ADD-BACK IS COMPUTED ABOVE THE ZERO-ROW GUARD, ON PURPOSE. It used to
    # be computed below it, which meant that when every segment failed the
    # revenue filter the function returned None and the associates and net cash
    # were discarded by nobody's decision. See `_sotp_nonoperating_addback`.
    associates, net_cash, _addback_basis = _sotp_nonoperating_addback(
        assumptions, net_debt)

    if not rows:
        # ── THE DEGRADED PATH ────────────────────────────────────────────────
        # Segments were supplied but none survived the positive-forward-revenue
        # filter, so there is no operating value to sum. What there still is, is
        # the balance-sheet add-back -- and it is returned rather than dropped.
        #
        # `per_share` and `per_share_reporting` are DELIBERATELY None. The
        # tempting thing is to publish the add-back as a per-share value, and it
        # is wrong by an order of magnitude: on BABA it is $90.3bn against a
        # $193 IV, so a 35%-weighted SOTP leg at under half the answer would
        # enter the blend and drag the published IV down for want of a revenue
        # figure. That is the same defect class as the blend silently dropping a
        # non-positive leg, only pointing the other way -- a MISSING input
        # producing a confidently wrong PUBLISHED number instead of a silently
        # inflated one. Both call sites are guarded on `degraded_no_segments`
        # and neither publishes from this table.
        #
        # The holdco discount is not applied here. It is a discount on an
        # operating NAV, and there is no operating NAV; discounting a cash and
        # associates pile by 15% would be a number with no meaning attached.
        if abs(associates) <= 0.0 and abs(net_cash) <= 0.0:
            # Nothing to preserve, so the old contract holds exactly: no rows
            # and no add-back is still None, and a caller cannot tell the
            # difference between this and the pre-fix behaviour.
            return None
        _nav_addback = associates + net_cash
        return {
            "rows":                [],
            "segment_value":       0.0,
            "associates":          associates,
            "net_cash":            net_cash,
            "nav":                 _nav_addback,
            "holdco_discount_pct": 0.0,
            "holdco_discount":     0.0,
            "final":               _nav_addback,
            "per_share":           None,
            "per_share_reporting": None,
            "fx_to_reporting":     float(fx_to_reporting or 1.0),
            "shares":              shares,
            "degraded_no_segments": True,
            "degraded_reason": (
                f"{len(segments)} segment(s) supplied, none carried a usable "
                f"forward revenue, so no operating value could be summed; "
                f"{_addback_basis}"),
        }

    nav = total_seg_value + associates + net_cash
    holdco_pct = float(assumptions.get("holdco_discount_pct", 0.0) or 0.0)
    holdco_value = nav * holdco_pct
    final = nav - holdco_value
    fx = float(fx_to_reporting or 1.0)

    for row in rows:
        row["value_split_pct"] = (row["value"] / nav) if nav else None

    return {
        "rows":                rows,
        "segment_value":       total_seg_value,
        "associates":          associates,
        "net_cash":            net_cash,
        "nav":                 nav,
        "holdco_discount_pct": holdco_pct,
        "holdco_discount":     holdco_value,
        "final":               final,
        "per_share":           final / shares,
        "per_share_reporting": final * fx / shares,
        "fx_to_reporting":     fx,
        "shares":              shares,
        # Present on the happy path too, so a consumer can ask the question
        # without a `.get` default hiding a table that was never degraded from
        # one whose key is missing for an unrelated reason.
        "degraded_no_segments": False,
    }


#: Relative outlier allowance for `_normalized_earnings`, as a fraction of the
#: median margin over the window. Replaces a hardcoded absolute `0.05`, which
#: was 500bp regardless of how thin the business ran. See the comment at the
#: assignment site for the measured effect.
_NORMALIZED_OUTLIER_REL = 0.30


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
        #: Outlier threshold, RELATIVE to the margin level.
        #:
        #: This used to be `max(iqr * 2, 0.05)` — an absolute 500bp floor. That
        #: floor is a fixed fraction of nothing, so its strictness depends
        #: entirely on how thin the business runs: at EL's 14.1% median a 5pp
        #: allowance is a 35% relative move, but at a 5% median it is a 100%
        #: relative move, so a 90% collapse is averaged straight in. Measured on
        #: [0.005, 0.05, 0.05, 0.05, 0.05] the old rule returned 0.0410 — the
        #: outlier survived and dragged the "normalized" figure 18% below the
        #: median it was supposed to be robust to. The rule was loosest exactly
        #: where consumer margins are thinnest, which is what a trough looks
        #: like, i.e. precisely when normalization is load-bearing.
        #:
        #: `abs(med)`, not `med`: the signed product is negative for a
        #: loss-maker, so `max()` would collapse onto `iqr * 2` and the relative
        #: term would be inert for every company with a negative median — the
        #: names whose margins are most distorted. Measured divergence on
        #: [-0.13, -0.10, -0.095, -0.105, -0.10]: abs keeps all five
        #: (-0.1060), signed filters one (-0.1000).
        #:
        #: Deliberately no absolute minimum behind the relative term. Adding one
        #: would reintroduce the defect this removes for thin-margin names.
        #:
        #: An earlier draft of this note justified that omission by pointing at the
        #: `len(filtered) >= 2 else med` fallback below as the safety net for a
        #: degenerate near-breakeven series. It is not a safety net, because that
        #: fallback is UNREACHABLE. `q1` is index `len // 4` and `q3` is
        #: `3 * len // 4`, so at n=5 they are s[1] and s[3], and each sits within
        #: one IQR of the median s[2] by construction: `s[2] - s[1] <=
        #: s[3] - s[1] = iqr`, and `s[3] - s[2] <= iqr` the same way. Since
        #: `threshold >= 2 * iqr >= iqr`, both are always kept and at least three
        #: survivors are guaranteed for every input reaching the branch. A single
        #: extreme value cannot break it: at n=5 an outlier lands on s[4], which is
        #: not a quartile index, so it inflates no threshold. Measured,
        #: `[0.10]*4 + [1e6]` and `[0.10]*4 + [-1e6]` each keep four.
        #:
        #: The decision not to add an absolute minimum therefore rests on the
        #: relative term being the right rule on its own merits, not on dead code
        #: catching the edge case. Stated explicitly because a justification that
        #: leans on an unreachable branch reads as much stronger than it is.
        #:
        #: TWO mechanisms, in opposite directions, and both are intended. An
        #: earlier draft of this comment claimed the change only ever loosens
        #: the filter for thin-margin names; the golden diff falsified that and
        #: the correction is recorded here so the next reader is not misled.
        #:
        #:   (a) REMOVING the 0.05 floor lets `iqr * 2` bind wherever
        #:       `iqr * 2 < 0.05` — a TIGHTENING, and it happens regardless of
        #:       where the relative term lands. Measured on the 09988_HK /
        #:       BABA net-income series [0.0730, 0.0838, 0.0850, 0.1306,
        #:       0.1012]: median 0.0850, `iqr * 2` = 0.0348, relative term
        #:       0.0255 — the relative term LOSES, the threshold falls 0.0500
        #:       -> 0.0348 on the floor's removal alone, and FY2025 (+53.6%
        #:       above the median) is excluded. Normalized NI -9.47%.
        #:   (b) The relative term GOVERNS wherever `abs(med) * 0.30` exceeds
        #:       `iqr * 2` — a LOOSENING, and it bites on rich-margin names,
        #:       not thin ones. Measured on FCX EBITDA [0.4589, 0.3983, 0.3783,
        #:       0.3719, 0.3402]: median 0.3783, `iqr * 2` = 0.0528, relative
        #:       term 0.1135 — the relative term WINS, FY2021 is read back in,
        #:       normalized EBITDA +4.66% and normalized EBIT +6.02%.
        #:
        #: (b) is a valuation-bearing judgement call, so the reasoning is stated
        #: rather than left implicit. FCX's five years are a MONOTONE decline,
        #: not a spike around a central value: there is no outlier here in any
        #: statistical sense, only the first point of a trend. The docstring's
        #: own method is "the mean of (field / revenue) over the window", with
        #: exclusion reserved for one-offs — goodwill write-downs, COVID, a
        #: special dividend — and a commodity peak is not one, it is the cycle
        #: the average exists to span. The old rule also trimmed ASYMMETRICALLY
        #: (dropped 2021 at dev +0.0806 while keeping 2025 at dev -0.0381),
        #: which biases a "mid-cycle" margin low on any trending series. A
        #: genuine peak-strip would have to drop 2022 and 2023 too; dropping
        #: only the single highest point was an artefact of where the fixed
        #: threshold happened to fall.
        #:
        #: The available alternative — capping the relative term at the 0.05 it
        #: replaces, `max(iqr * 2, min(abs(med) * REL, 0.05))` — is provably
        #: monotone-tightening against the old rule, so it would fix (a) and
        #: leave FCX untouched. It is NOT taken here because it is a clamp, and
        #: clamps on estimates are the owner's choice, not the engine's.
        threshold = max(iqr * 2, abs(med) * _NORMALIZED_OUTLIER_REL)
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
    fx_rate: float = 1.0,
) -> Optional[dict]:
    """Derive bear / base / bull revenue growth rates from analyst dispersion.

    Uses the nearest forward-year estimate's low / avg / high revenue figures
    (and the analyst-count quality gate) to produce asymmetric scenario growth
    rates that reflect actual market disagreement, rather than the symmetric
    ±45% multiplier used when dispersion data is unavailable.

    ``fx_rate`` converts the estimate revenue figures (reported in the
    company's REPORTING currency by FMP) into the target currency of
    ``revenue_base`` before the ratio is taken. Task #26: without this,
    raw-CNY estimates ÷ USD-converted base implied +700% growth that
    clamped every band to +100% on China ADRs.

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
    # Positivity / monotonicity are invariant under a positive scale, so the
    # checks above run on raw values; convert before taking the ratio.
    _fxm = fx_rate if (fx_rate and fx_rate > 0) else 1.0
    lo, av, hi = lo * _fxm, av * _fxm, hi * _fxm

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


# ── R1: structured company guidance (Priority 0 over the regex parse) ────────
# The assumption store (src/memory/assumption_store.py) holds guidance
# extracted from PRIMARY sources — the EDGAR press release (FPI 6-K
# EX-99.1 / domestic 8-K Item 2.02) and the full earnings-call transcript —
# by workstream R1. When a stored row exists it takes Priority 0 in the
# growth waterfall and for Year-1 EBITDA; the regex-parsed
# management_guidance stays as fallback and deterministic cross-check.
# Soft-fail throughout: kill switch off, store empty/errored, or unparseable
# amounts → None and the legacy path runs unchanged.

def _norm_metric(s) -> str:
    """'Total Revenue' → 'totalrevenue'; 'Adj. EBITDA' → 'adjebitda'.
    Press-release metric names are free-form text — normalize before
    matching so consumers don't depend on exact wording."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _is_annual_period(period: str) -> bool:
    """True for full-year-ish period labels ('Full Year FY27', 'FY2026',
    'fiscal year ended ...'); False for quarters ('Q2 FY27', '3Q26')."""
    p = (period or "").lower()
    if re.search(r"quarter|qtr|\bq[1-4]\b", p):
        return False
    return any(k in p for k in ("full year", "annual", "fy", "year"))


def _revenue_metric_rank(nm: str) -> int:
    """0 = the canonical P&L revenue line; 2 = another *revenue* figure
    (segment line, currency-annotated 'Revenue (RMB)'); -1 = not revenue.
    ARR ('recurring revenue') is a KPI near revenue in size but NOT the
    P&L line → excluded outright (live CRWD row carries both)."""
    if "recurring" in nm or not nm:
        return -1
    if nm in ("revenue", "revenues", "totalrevenue", "totalrevenues",
              "netrevenue", "netrevenues", "totalnetrevenue",
              "totalnetrevenues", "netsales", "totalnetsales"):
        return 0
    if "revenue" in nm or "revenues" in nm:
        return 2
    return -1


def _r1_structured_guidance(ticker: str,
                            revenue_base: float = 0.0) -> Optional[dict]:
    """Shape the latest stored earnings-assumption guidance row like
    _extract_management_guidance() output:
        {revenue_guidance_mid?, ebitda_guidance_mid?,
         _r1_period, _r1_source, _r1_as_of}
    Metric names are normalized (_norm_metric) — live rows carry
    'Total revenue', 'Adjusted EBITA', etc., not canonical keys.
    Candidates are ranked (canonical revenue line first, annual periods
    before quarterly) and the first whose implied growth survives the
    sanity band wins (−30%..+100% vs revenue_base — rejects quarters
    against an annual base and currency mismatches).
    None when disabled / nothing stored / nothing parseable."""
    if os.environ.get("EARNINGS_ASSUMPTIONS", "true").strip().lower() in (
            "0", "false", "no", "off", ""):
        return None
    try:
        from src.memory import assumption_store
        latest = assumption_store.get_latest_earnings_assumptions(ticker)
    except Exception:
        return None
    if not latest:
        return None
    try:
        from src.memory.assumption_extract import parse_amount
    except Exception:
        return None

    def _amount(g: dict) -> Optional[float]:
        mid = parse_amount(g.get("mid"))
        if mid:
            return mid
        # No mid → average the two range ends (parsed separately; each may
        # carry its own scale suffix, e.g. "$4.4B" / "$4.5B")
        lo = parse_amount(g.get("low"))
        hi = parse_amount(g.get("high"))
        if lo and hi:
            return (lo + hi) / 2.0
        return lo or hi

    out: dict = {}
    # Revenue: rank candidates (canonical line before segment/currency-
    # annotated ones; annual periods before quarterly), then take the
    # first that survives the implied-growth band.  Live rows store one
    # item per fiscal period (CRWD: Total revenue Q2 FY27 AND Full Year
    # FY27) — against the annual base only the full-year figure lands
    # in-band, and the annual-first ordering also picks correctly when
    # no base is supplied.
    cands: list[tuple] = []
    for g in (latest.get("guidance") or []):
        rank = _revenue_metric_rank(_norm_metric(g.get("metric")))
        if rank < 0:
            continue
        amt = _amount(g)
        if not amt:
            continue
        annual = 0 if _is_annual_period(g.get("period")) else 1
        cands.append((rank, annual, g, amt))
    cands.sort(key=lambda c: (c[0], c[1]))
    for _rk, _an, g, amt in cands:
        if revenue_base and revenue_base > 0:
            implied = (amt / revenue_base) - 1.0
            if not (-0.30 <= implied <= 1.0):
                continue
        out["revenue_guidance_mid"] = amt
        out["_r1_period"] = g.get("period") or ""
        break
    # EBITDA/EBITA: same annual-first preference ('Adjusted EBITA' is the
    # reported line at BABA; 'Non-GAAP income from operations' is NOT an
    # EBITDA figure and matches neither substring).
    eb_cands: list[tuple] = []
    for g in (latest.get("guidance") or []):
        nm = _norm_metric(g.get("metric"))
        if "ebitda" not in nm and "ebita" not in nm:
            continue
        amt = _amount(g)
        if amt:
            annual = 0 if _is_annual_period(g.get("period")) else 1
            eb_cands.append((annual, amt))
    if eb_cands:
        eb_cands.sort(key=lambda c: c[0])
        out["ebitda_guidance_mid"] = eb_cands[0][1]
    if out:
        out.setdefault("_r1_period", latest.get("period_label") or "")
        out["_r1_source"] = latest.get("source") or ""
        out["_r1_as_of"] = latest.get("as_of") or ""
        return out
    return None


# ── Core DCF Engine ───────────────────────────────────────────────────────────

# The ceiling `_project_dcf` puts on a projected FCF margin, paired with the
# per-sector `FCF_MARGIN_FLOOR` that puts a floor under it. Named rather than
# left as a literal because there is a THIRD place that has to reproduce the
# same clamp: `_y10_fcf_margin`, which estimates the terminal state Gate B
# judges. When that estimate and the cash-flow engine disagree about the
# margin, Gate B is judging a company the DCF is not modelling — see the
# binding site for what that cost. `src/utils/pdf_report.py` carries its own
# copy of the same expression for its sensitivity grid.
_FCF_MARGIN_CAP = 0.60


def _sales_to_capital(row: Optional[dict]) -> Optional[float]:
    """Revenue ÷ invested capital — the S/C the reinvestment charge divides by.

    Measured off the company's own balance sheet rather than defaulted per
    profile, for a reason the engine already commits to elsewhere: the Y10
    forward-ROIC path scales invested capital by the same multiplier it scales
    revenue by (`_y10_ic = ic_val * _y10_rev_mult`), which is only coherent if
    S/C is CONSTANT across the projection. That is the same assumption the
    charge makes, so reading the ratio from the same two numbers keeps the
    deduction consistent with the ROIC Gate B judges off them. A hardcoded
    per-sector figure — the 1.85 the post-mortem's worked example uses — would
    be consistent with neither and would silently override a measured capital
    base with a guess.

    Returns None when either leg is missing, zero or negative. None means "no
    charge", and that is an honest absence rather than a fabricated ratio: an
    invented S/C would levy a real cash deduction derived from a number nobody
    measured. It is also the failure mode that shows up most often, because
    `invested_capital` is an optional line item — see the gate record, which
    reports which of the two happened.

    No bound is placed on the ratio itself. A genuinely asset-light business
    has a genuinely large S/C and therefore a genuinely near-zero deduction;
    bounding it would turn a correct small charge into an incorrect large one.

    MEASURED, AND THIS IS WHY THE CHARGE IS OBSERVATION-ONLY. Across the 13
    chargeable golden fixtures the ratio spans 0.063 to 10.912 — a factor of
    173 — and the two ends are not "asset-light vs asset-heavy" so much as
    "ratio means capital turnover" vs "ratio means nothing":

        C38U_SI (S-REIT)     S/C 0.063   deduction +98.04pp   base margin 0.5583
        SCHW    (Brokerage)  S/C 0.806   deduction +16.19pp   base margin 0.1153
        BN4_SI  (Conglomerate) S/C 0.298 deduction +16.00pp   base margin 0.0979
        COST    (Retail)     S/C 10.912  deduction  +0.82pp   base margin 0.0232

    A REIT's invested capital is its property base and a broker's is its balance
    sheet; neither is working capital supporting incremental sales, which is the
    quantity the identity `ΔRev/(S/C)` assumes the ratio converts. Revenue over
    that capital is still a true number about the company, it is just not
    sales-to-capital, and dividing a revenue delta by it does not give a capital
    requirement. The post-mortem's worked example used S/C = 1.85 for an apparel
    grower, where the ratio IS capital turnover and 11.8pp is the right answer.

    So the open question is scope, not calibration: which profiles may be
    charged at all. No bound is added here because a bound would be a clamp
    nobody chose, and because no single bound is simultaneously tight on a
    retailer and loose on a REIT — the span is 173x.
    """
    rev = _safe((row or {}).get("revenue"))
    ic = _safe((row or {}).get("invested_capital"))
    if rev is None or ic is None or rev <= 0.0 or ic <= 0.0:
        return None
    return rev / ic


def _reinvestment_margin_deduction_raw(g: float, sales_to_capital: Optional[float],
                                       *, profile: Optional[str] = None) -> float:
    """The UNCAPPED margin deduction one year of growth `g` requires, at ratio S/C.

        ΔRev/(S/C) ÷ Rev_t  =  g / ((1 + g) · (S/C))

    Split out from `_project_dcf`'s loop because three places have to agree on
    it and only one of them is a projection: the loop itself, `_y10_fcf_margin`
    (the terminal-state estimate Gate B judges, which the `_FCF_MARGIN_CAP`
    comment above already names as a parity obligation), and the sensitivity
    grid in `src/utils/pdf_report.py`. Computing it once is what makes "the DCF
    charged reinvestment and Gate B judged a margin that did not" unreachable
    rather than merely tested for — that is the `md_abs * 10` defect, which was
    exactly a parity obligation between these two that nobody had wired.

    ── THE SCOPE GATE, AND WHY IT LIVES IN HERE ───────────────────────────────
    Zero unless `profile` is in `CAPITAL_TURNOVER_PROFILES`. Owner-specified
    2026-09-18: `revenue ÷ invested capital` is economic nonsense for
    balance-sheet financial intermediaries, regulated utilities/IPPs and real
    estate asset bases, and the charge is to be restricted STRICTLY to the
    capital-turnover profiles. The measured case for it is the table at that
    constant: S/C spans 175x across the 14 golden fixtures (0.0625 for an S-REIT
    to 10.9116 for a membership retailer), and the span is not dispersion around
    one quantity — it is fourteen different quantities wearing one name. On
    C38U_SI the raw charge is +98.04% against a +55.83% base margin.

    The gate is in the FUNCTION rather than at the call site so that it cannot be
    bypassed by a new caller, and `profile=None` returns zero rather than charging
    unscoped. That default direction is deliberate: the failure mode it protects
    against is someone threading a ratio into `_project_dcf` later and getting an
    unscoped charge on a bank, which is the defect this exists to close. Forgetting
    the profile now costs nothing; forgetting it the other way would have moved
    money. The same reasoning makes `profile` keyword-only — a positional third
    argument would let a caller pass a growth schedule or a margin where a profile
    name belongs and still type-check.

    Two-sided on purpose: a negative `g` returns a negative deduction, because
    a shrinking business releases working capital it was holding. Zero when S/C
    is unmeasurable or when revenue would not survive the year (`1 + g <= 0`),
    so the caller never has to guard the division. Note that the two-sidedness
    was NOT the source of the sign inversion the live run found — that came from
    the floor and the blend dropping a None leg, and it fired on growth years.
    """
    if profile is None or profile not in CAPITAL_TURNOVER_PROFILES:
        return 0.0
    if sales_to_capital is None or sales_to_capital <= 0.0:
        return 0.0
    if (1.0 + g) <= 0.0:
        return 0.0
    return g / ((1.0 + g) * sales_to_capital)


def _reinvestment_margin_deduction(g: float, sales_to_capital: Optional[float],
                                   *, profile: Optional[str] = None,
                                   margin_headroom: Optional[float] = None
                                   ) -> float:
    """The LEVIABLE charge: the scoped raw deduction, rationed to the margin base.

        deduction = min(g / ((1 + g) · (S/C)), max(fcf_margin_base − fcf_floor, 0))

    Owner-specified 2026-09-18, verbatim: "A growth reinvestment deduction must
    not consume cash beyond the operating baseline into negative territory unless
    the model is explicitly running an un-floored multi-year cash-burn schedule."
    `margin_headroom` is that baseline minus the sector's `FCF_MARGIN_FLOOR`, so
    the bound is "do not push the projected margin through the floor the projector
    is going to apply anyway" — which is stronger than capping at the base margin
    and is the form the owner wrote.

    The cap sits in a SEPARATE function from the formula rather than inside it, so
    that each of the three things that must not drift lives in exactly one place:
    the algebra and the scope gate in `_reinvestment_margin_deduction_raw`, the
    rationing bound here. A caller that wants the uncapped number for disclosure —
    which the gate record does, because "capped from +98.04% to +50.83%" is the
    fact worth publishing and "+50.83%" alone is not — calls the raw function and
    cannot thereby get a different algebra.

    `margin_headroom=None` returns ZERO rather than the uncapped charge, for the
    same reason `profile=None` does: a rationing bound cannot be enforced against
    a baseline nobody supplied, and the safe direction when it cannot be enforced
    is not to charge. Both gates must be passed explicitly for a non-zero result.

    The cap binds only on the positive side. A negative deduction — a shrinking
    year releasing working capital — passes through untouched, because `min()` of a
    negative and a non-negative bound is the negative. That is the owner's formula
    read literally and it is also the right reading: capping a CREDIT at zero would
    silently delete the two-sidedness the raw function documents.

    Today the only live caller is the observation record at
    GATE_GROWTH_REINVESTMENT, which is the reason the helper exists separately
    rather than being inlined in the loop: the counterfactual has to be computed
    by the same code the live charge would use, or the record would describe a
    different mechanism than the one waiting to be switched on. The other two
    parity sites are obligations for whoever switches it on, and both are
    recorded at their own sites — `_y10_fcf_margin` above and the sensitivity
    grid's `_iv` / `_iv_gm`, whose comments already state that the grid's centre
    cell must reproduce the published base IV.
    """
    raw = _reinvestment_margin_deduction_raw(g, sales_to_capital, profile=profile)
    if raw == 0.0:
        return 0.0
    if margin_headroom is None:
        return 0.0
    return min(raw, max(margin_headroom, 0.0))


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
    growth_schedule: Optional[list[float]] = None,
    wacc_schedule: Optional[list[float]] = None,
    margin_delta_absolute: Optional[float] = None,
    include_terminal: bool = True,
    sales_to_capital: Optional[float] = None,
) -> tuple[float, float, float, list[dict]]:
    """
    Core DCF engine.  Returns (intrinsic_value_per_share, pv_fcf_sum_per_share,
    pv_tv_per_share, annual_rows).
    Splitting PV components allows the Forward Gate A (80/20 TV check).
    annual_rows is a list of dicts, one per projection year:
      { year_label, revenue, growth_pct, fcf_margin, fcf, discount_factor, pv_fcf }
    All monetary values are absolute (not per-share).

    Option III extensions (all optional, default preserves legacy behavior):
      growth_schedule: per-year growth list (len == years). Overrides
        growth_rate when provided; enables exponential decay for Tech.
      wacc_schedule: per-year discount rate list (len == years). Overrides
        wacc when provided; enables two-stage WACC fade for early-stage Tech.
      margin_delta_absolute: one-shot absolute delta applied to every year
        (not scaled by t). Used by the multiplicative margin-variance form
        (_MARGIN_DELTA_MULT). When None, falls back to legacy
        margin_delta_per_year drift.
      sales_to_capital: the company's revenue ÷ invested capital, S/C. When
        given, every projected year's margin is reduced by the capital its own
        growth requires — see the reinvestment block below. None (the default)
        reproduces the legacy flat-margin projection exactly, and None is what
        all four call sites pass today.

    ── The reinvestment charge ──────────────────────────────────────────────
    A flat FCF margin over compounding revenue charges nothing for the working
    capital and capacity the growth requires, so a 28%-a-year grower projects
    the same cash margin as a no-growth one. This module said so itself, at the
    cash-conversion cap: "It is also likely a compensating error. `_project_dcf`
    holds the margin flat while revenue compounds, charging nothing for the
    investment that growth requires, so capping the margin makes the OUTPUT look
    sane by breaking an INPUT... The reinvestment charge is the real fix; until
    it lands this records what it would have done and moves nothing."

    This is that fix — BUILT, wired live at all four call sites, and measured
    against the 14 golden fixtures, and it is now OBSERVATION-ONLY: every call
    site passes `sales_to_capital=None`, so this projector is byte-identical to
    the shipped baseline and GATE_GROWTH_REINVESTMENT records the counterfactual
    beside the cash-conversion cap it was written to replace. The measurement
    that decided this is at the gate's site in `run_dcf_agent`; in one line, base
    IV moved on 9 of 14 over −9.45% to +17.40% and the sign INVERTED on the two
    hyper-growth names the charge exists for (09988_HK +17.40%, BABA +15.60%),
    because the deduction exceeded the base margin, the floor absorbed the rest,
    `iv_dcf` went to None and the blend renormalised that leg's weight onto
    higher multiples — `weight_dcf` 0.2778 → 0.0, `weight_multi` → 1.0.

    The mechanism below is kept in full rather than reverted, because the defect
    is in the RATIO's scope and the floor interaction, not in the algebra — and
    because deleting a built-and-tested mechanism is how the next attempt ends up
    reimplementing it slightly differently. What follows is what was measured.

    The charge is expressed as a MARGIN deduction rather than a cash line, which
    is what makes it fit this projector's margin-only shape without the EBIT /
    D&A / capex build-up a cash-line form would need:

        Reinvestment_t   = ΔRev_t / (S/C) = Rev_{t-1} · g_t / (S/C)
        Deduction_t      = Reinvestment_t / Rev_t = g_t / ((1 + g_t) · (S/C))
        Effective margin = max(margin_base − Deduction_t, fcf_floor)

    The middle step is an identity, not an approximation: Rev_t = Rev_{t-1}(1+g_t),
    so dividing the charge by current revenue gives exactly the expression above.
    `revenue × (margin − deduction)` and `revenue × margin − charge` are the same
    number. Expressed per unit of revenue it needs no new cash-flow line, and it
    flows into the terminal value through `fcf_T = rev_T * margin_t` — a cash-line
    form that touched only the projected years would leave the TV built on the
    un-charged margin, and the TV is 86.0% of the total for the ONON-shaped
    inputs this was measured against.

    The deduction is derived from `g_t` INSIDE the loop, from the same per-year
    growth the projector already resolved, rather than handed in as a
    precomputed `margin_schedule`. A list built at the call site would have to
    duplicate this function's `_g_by_year` fallback for the no-schedule case,
    and this module already carries a comment about the last time two growth
    paths were built independently and drifted.

    TWO-SIDED, deliberately. A negative `g_t` gives a negative deduction and
    RAISES the margin, because a shrinking business releases the working capital
    it was holding. That is the correct economics and it is what the formula
    says; restricting the charge to growth years would be an asymmetry nobody
    chose, and it would make bear scenarios more conservative than the model
    they are stressing. It does mean the bear case can carry a margin uplift,
    which is worth knowing before reading a bear IV as strictly worse.
    """
    if shares is None or shares <= 0:
        return 0.0, 0.0, 0.0, []

    # Resolve per-year schedules. growth/wacc schedules are 0-indexed lists of
    # length `years`; for year t (1-indexed), use index t-1.
    if growth_schedule is not None and len(growth_schedule) >= years:
        _g_by_year = growth_schedule
    else:
        _g_by_year = [growth_rate] * years

    if wacc_schedule is not None and len(wacc_schedule) >= years:
        _w_by_year = wacc_schedule
    else:
        _w_by_year = [wacc] * years

    #: Normalised once, outside the loop, so the per-year branch is a None test
    #: and not a coercion. A non-positive ratio is rejected rather than clamped:
    #: S/C <= 0 means invested capital was zero, negative or missing, and there
    #: is no honest charge to compute from it — returning None reproduces the
    #: legacy projection instead of inventing a number. An asset-light name with
    #: a genuinely small capital base gets a genuinely large S/C and therefore a
    #: genuinely near-zero deduction, which is the right answer and needs no
    #: bound of its own.
    _s_to_c: Optional[float] = (
        float(sales_to_capital)
        if (sales_to_capital is not None and sales_to_capital > 0)
        else None
    )

    annual_rows = []
    pv_sum = 0.0
    rev_t = revenue_base
    disc_cum = 1.0
    for t in range(1, years + 1):
        g_t   = _g_by_year[t - 1]
        w_t   = _w_by_year[t - 1]
        rev_t = rev_t * (1 + g_t)
        if margin_delta_absolute is not None:
            margin_t = fcf_margin_base + margin_delta_absolute
        else:
            margin_t = fcf_margin_base + margin_delta_per_year * t
        # ── Reinvestment charge ── see the docstring for the derivation and
        # for why this is a margin deduction and not a cash line. Delegated to
        # `_reinvestment_margin_deduction` so the two other places that have to
        # agree on this quantity cannot drift from it. `reinvest_t` is recorded
        # PRE-floor: when the floor binds, the charge the model wanted to levy
        # and the charge it actually levied differ, and an audit that showed
        # only the post-floor margin would hide the difference. That is not a
        # hypothetical — the live run floored the deduction on five fixtures,
        # and the pre-floor figure is the only thing in the payload that shows
        # it happened. All four call sites pass `sales_to_capital=None` today,
        # so in production `reinvest_t` is 0.0 and the subtraction is inert.
        #
        # DOUBLY inert since the scope-and-rationing change, and that is worth
        # stating plainly because it is a trap for whoever wires this live. This
        # call passes no `profile` and no `margin_headroom`, and both gates fail
        # closed — so threading a ratio in through `sales_to_capital` alone would
        # now charge NOTHING and would look like a silent no-op rather than a
        # missing argument. Wiring the charge live is therefore a three-part
        # change, not a one-part one: add `profile` and `margin_headroom` to
        # `_project_dcf`'s signature (which `test_the_projector_has_none_of_the_
        # inputs_the_briefs_patch_needs` pins as an exact ordered parameter list,
        # so that test moves deliberately and not by accident), pass them here,
        # and move `_y10_fcf_margin` in the same commit because Gate B judges the
        # terminal margin and a charged projection against an uncharged estimate
        # is the `md_abs * 10` parity defect in a new disguise.
        # `test_the_projectors_reinvestment_call_is_scope_inert` pins this call
        # as it stands, so the obligation surfaces as a red test rather than as a
        # valuation that quietly failed to change.
        reinvest_t = _reinvestment_margin_deduction(g_t, _s_to_c)
        margin_t -= reinvest_t
        # Floor then cap, in the legacy order: with `_s_to_c` None this is
        # byte-identical to `min(max(base + delta, floor), cap)`.
        margin_t = max(margin_t, fcf_floor)
        margin_t = min(margin_t, _FCF_MARGIN_CAP)
        fcf_t    = rev_t * margin_t
        # Compound discount factor using per-year WACC (staged fade).
        disc_cum = disc_cum / (1 + w_t)
        pv_fcf_t = fcf_t * disc_cum
        pv_sum  += pv_fcf_t
        annual_rows.append({
            "year_label":      f"Yr {t}",
            "revenue":         rev_t,
            "growth_pct":      g_t,
            "fcf_margin":      margin_t,
            "wacc":            w_t,
            "fcf":             fcf_t,
            "discount_factor": disc_cum,
            "pv_fcf":          pv_fcf_t,
            # Not projected into the golden snapshot — `projection_rows` is in
            # neither `_SCALAR_KEYS` nor `_DICT_KEYS` — so this is a payload
            # audit field, not a baseline field.
            "reinvest_margin_deduction": reinvest_t,
        })

    # Terminal value — use the last year's revenue/margin/WACC so growth-decay
    # and staged-WACC both flow into the TV calculation naturally. WACC fades
    # back to base by Y4+, so the terminal WACC is effectively base_wacc for
    # early-stage profiles (the premium only applies to Y1-3).
    rev_T    = rev_t
    fcf_T    = rev_T * margin_t
    wacc_T   = _w_by_year[years - 1]
    if not include_terminal:
        # A DEPLETING asset has no going concern past its reserve life. Setting
        # tgr=0 is not the same thing: Gordon with g=0 still returns FCF/WACC,
        # a perpetuity roughly 10x the final year's cash flow. For a mine, that
        # perpetuity is the entire overstatement -- it values ore that does not
        # exist. The projection simply stops.
        tv = pv_tv = 0.0
    else:
        # Safety: terminal WACC must exceed TGR by a margin
        if wacc_T <= tgr:
            wacc_T = tgr + 0.005
        fcf_terminal = fcf_T * (1 + tgr)
        tv           = fcf_terminal / (wacc_T - tgr)
        pv_tv        = tv * disc_cum

    equity_value = pv_sum + pv_tv - (net_debt or 0.0)
    iv = equity_value / shares
    return iv, pv_sum / shares, pv_tv / shares, annual_rows


# ── REIT metrics (Tier 2) ─────────────────────────────────────────────────────

# Sub-type-aware maintenance capex as % of revenue (Gemini point 6 fix).
# Protects AFFO from being under-stated when a growth REIT books heavy
# acquisition / development capex. We subtract min(actual_capex,
# sub_type_rate × revenue) so AFFO reflects a normalized maintenance
# reserve rather than the full reported capex.
#
# Industry-standard ranges per sub-type:
#   Data Center / Lab  2-3%  (specialized OpEx, low recurring)
#   Industrial / Self-Storage  3%    (minimal recurring)
#   Residential / Healthcare   4%    (moderate turnover)
#   Retail                     5-6%  (TI + common area)
#   Office                     6%    (TI-heavy, build-out)
#   Hospitality                7-8%  (FF&E reserves)
_REIT_MAINT_CAPEX_PCT: dict[str, float] = {
    "data_center":         0.02,
    # Interconnection-heavy DCs (EQIX): higher recurring maint for network
    # fabric refresh + meet-me-room kit. Offset by higher revenue per sq ft.
    "data_center_premium": 0.025,
    "lab":                 0.025,
    "industrial":          0.03,
    # Net-lease tenants absorb property opex, maintenance, insurance, taxes
    # under triple-net structure — landlord's maintenance capex obligation
    # is minimal (reserve fund for structural items only).
    "net_lease":           0.01,
    "self_storage":        0.03,
    "residential":         0.04,
    "healthcare":          0.04,
    "retail":              0.055,
    "office":              0.060,
    "hospitality":         0.075,
    "infrastructure":      0.085,   # infra concessions — heavy recurring maint reserves
    "default":             0.045,
}

# Default cap rates and REIT multiples by sub-type — used for NAV (Cap Rates),
# P/FFO, and P/AFFO method branches. Source: BofA REIT sector research +
# Green Street quarterly reports, calibrated 2026-04.
#
# cap_rate is the implied yield on NOI used to capitalize property value
# (NAV = NOI / cap_rate). Lower cap rate = premium asset class.
# p_ffo / p_affo are REIT-specific distribution multiples (NOT P/E — these
# apply to cash-adjusted metrics that REITs report in supplemental disclosures).
# ── Singapore REIT (S-REIT) Dividend Discount Model ──────────────────────
#
# S-REITs are priced on a DDM off DPU, not on the US P/FFO / P/AFFO /
# NAV-cap-rate stack. Every Singapore broker note in the reviewed set
# states this outright in its valuation line:
#
#   Frasers Centrepoint Trust  "DDM (Cost of equity 6.38%, Terminal Growth 1.5%)"   TP S$2.70
#   Keppel DC REIT             "DDM (Cost of Equity: 6.83%; Terminal g: 1.75%)"     TP S$2.46
#   OUE REIT                   "DDM (Cost of Equity: 7%; Terminal g: 1.2%)"         TP S$0.45
#
# The reason is structural, not stylistic: an S-REIT must distribute at
# least 90% of taxable income to keep its tax transparency, so DPU is very
# nearly the whole of the equity cash flow and a distribution discount
# model IS the valuation. FFO/AFFO are US GAAP constructs that S-REITs do
# not report; our engine was computing them from statements and pricing a
# Singapore trust on American multiples.
#
# Reconstructing the published targets from forward DPU capitalised at
# (CoE - g) lands within ~1-7%, which is close enough to confirm the
# mechanism. The broker models are multi-stage; this is the reduced form.
#
#   FCT      12.87c x 1.015 / 0.0488 = 2.68  vs 2.70
#   KDCREIT  ~12.1c x 1.0175 / 0.0508 = 2.42 vs 2.46
#   OUE       2.40c x 1.012 / 0.0580 = 0.42  vs 0.45

# Per-sub-sector cost of equity and terminal growth.
#
# PUBLISHED (from the broker tables above): retail, data_centre,
# hospitality. Everything else is INTERPOLATED against those three
# anchors on the risk ordering the reports themselves imply — CoE rises
# with cash-flow volatility (suburban retail 6.38% < data centre 6.83% <
# hospitality 7.00%) and terminal g tracks the growth profile
# (hospitality 1.2% < retail 1.5% < data centre 1.75%). Interpolated rows
# are marked; replace them as broker tables become available rather than
# treating them as sourced.
# Maps the SGX lookup's sub-sector hint ("DataCentre", "Retail", "India")
# onto a _SREIT_DDM_CALIBRATION key. The hints are screener-display
# metadata, so they are normalised rather than trusted verbatim.
_SGX_REIT_SUBTYPE_MAP: dict[str, str] = {
    "retail": "retail", "datacentre": "data_centre", "datacenter": "data_centre",
    "industrial": "industrial", "logistics": "logistics", "office": "office",
    "commercial": "commercial", "hospitality": "hospitality",
    "healthcare": "healthcare", "infra": "infra", "infrastructure": "infra",
    "india": "india", "china": "china", "european": "european",
    "usoffice": "us_office", "accommodation": "accommodation",
}


def _sgx_reit_subtype(ticker: str) -> str:
    """Sub-sector key for an SGX-listed REIT, or 'default'."""
    try:
        from src.data.sector_profiles import SGX_TICKER_SECTOR_LOOKUP as _SGX
    except Exception:
        return "default"
    entry = _SGX.get((ticker or "").upper())
    if not entry or len(entry) < 2:
        return "default"
    key = re.sub(r"[^a-z]", "", (entry[1] or "").lower())
    return _SGX_REIT_SUBTYPE_MAP.get(key, "default")


_SREIT_DDM_CALIBRATION: dict[str, dict] = {
    # ── published ────────────────────────────────────────────────────
    "retail":        {"coe": 0.0638, "g": 0.0150, "src": "published"},
    "data_centre":   {"coe": 0.0683, "g": 0.0175, "src": "published"},
    "hospitality":   {"coe": 0.0700, "g": 0.0120, "src": "published"},
    # ── interpolated ─────────────────────────────────────────────────
    # Healthcare (Parkway Life): longest WALE in the market, CPI-linked
    # downside-protected rents — the most defensive S-REIT cash flow.
    "healthcare":    {"coe": 0.0630, "g": 0.0150, "src": "interpolated"},
    "industrial":    {"coe": 0.0660, "g": 0.0150, "src": "interpolated"},
    "logistics":     {"coe": 0.0660, "g": 0.0150, "src": "interpolated"},
    # Regulated / contracted infrastructure (NetLink, Keppel Infra).
    "infra":         {"coe": 0.0675, "g": 0.0125, "src": "interpolated"},
    "commercial":    {"coe": 0.0690, "g": 0.0125, "src": "interpolated"},
    "accommodation": {"coe": 0.0725, "g": 0.0150, "src": "interpolated"},
    # Singapore office — structurally softer than suburban retail.
    "office":        {"coe": 0.0710, "g": 0.0100, "src": "interpolated"},
    "european":      {"coe": 0.0750, "g": 0.0100, "src": "interpolated"},
    # India: g 2.75% is published (CapitaLand India Trust); the CoE is
    # interpolated upward for EM currency and country risk. INR
    # depreciation is a recurring drag on SGD-reported DPU.
    "india":         {"coe": 0.0800, "g": 0.0275, "src": "g published, CoE interpolated"},
    # China: negative rental reversions across retail, business parks and
    # logistics in the reviewed note; growth assumption deliberately low.
    "china":         {"coe": 0.0850, "g": 0.0100, "src": "interpolated"},
    # US office held by an SGX trust — the weakest cash flow in the
    # universe (Prime US REIT); wide CoE, no real terminal growth.
    "us_office":     {"coe": 0.0850, "g": 0.0050, "src": "interpolated"},
    "default":       {"coe": 0.0700, "g": 0.0125, "src": "interpolated"},
}

# Per-ticker DDM assumptions lifted from published broker valuation lines.
# These outrank the sub-sector defaults. Same precedence principle as the
# bank GGM table: CoE and terminal growth are analyst constructs, never
# issuer disclosures, so a stated broker table beats anything an extractor
# scrapes out of prose.
_SREIT_DDM_OVERRIDES: dict[str, dict] = {
    # Phillip Securities Research, Frasers Centrepoint Trust, Jul 2026.
    "J69U.SI": {"coe": 0.0638, "g": 0.0150, "as_of": "2026-07",
                "source": "Phillip Securities Research (FCT)"},
    # Phillip Securities Research, Keppel DC REIT, 27 Jul 2026 (SG2026_0137).
    "AJBU.SI": {"coe": 0.0683, "g": 0.0175, "as_of": "2026-07-27",
                "source": "Phillip Securities Research SG2026_0137"},
    # Phillip Securities Research, OUE REIT, Jul 2026.
    "TS0U.SI": {"coe": 0.0700, "g": 0.0120, "as_of": "2026-07",
                "source": "Phillip Securities Research (OUE REIT)"},
    # OCBC Global Markets, CapitaLand India Trust, 29 Jul 2026 — terminal
    # growth stated at 2.75%; the note does not publish a cost of equity,
    # so that field is left to the sub-sector row.
    "CY6U.SI": {"g": 0.0275, "as_of": "2026-07-29",
                "source": "OCBC Global Markets (CLINT)"},
}


def _sreit_ddm_assumptions(ticker: str, subtype: str,
                           most_recent: dict) -> dict:
    """Resolve (CoE, g) for an S-REIT DDM, with provenance.

    Precedence: published broker table for this ticker, then a live
    research extraction that survives a plausibility band, then the
    sub-sector calibration. Research ranks BELOW the broker table for the
    same reason it does on the bank GGM — no issuer publishes its own cost
    of equity, so an extracted value is second-hand opinion competing with
    a table that states the number deliberately.
    """
    cfg = _SREIT_DDM_CALIBRATION.get(subtype) or _SREIT_DDM_CALIBRATION["default"]
    ovr = _SREIT_DDM_OVERRIDES.get((ticker or "").upper(), {})
    prov: list[str] = []

    _basis = _analyst_basis(ticker, most_recent)
    _basis_coe = _safe(_basis.get("cost_of_equity"))
    _basis_g = _safe(_basis.get("terminal_growth"))
    _basis_lbl = (f"analyst basis: {_basis.get('house') or 'sell-side'} "
                  f"{_basis.get('as_of') or ''}").strip()

    research_coe = _safe(most_recent.get("_sreit_coe_research"))
    if _basis_coe and 0.03 < _basis_coe < 0.15:
        coe, src = _basis_coe, _basis_lbl
    elif ovr.get("coe"):
        coe, src = ovr["coe"], "broker"
    elif research_coe and abs(research_coe - cfg["coe"]) <= 0.02 and 0.03 < research_coe < 0.15:
        coe, src = research_coe, "research"
    else:
        coe, src = cfg["coe"], f"sub-sector ({subtype})"
        if research_coe:
            prov.append(f"[research CoE {research_coe:.2%} rejected]")
    prov.append(f"CoE {coe:.2%} ({src})")

    research_g = _safe(most_recent.get("_sreit_terminal_g_research"))
    if _basis_g is not None and 0.0 <= _basis_g <= 0.04:
        g, src = _basis_g, _basis_lbl
    elif ovr.get("g") is not None:
        g, src = ovr["g"], "broker"
    elif research_g is not None and 0.0 <= research_g <= 0.04:
        g, src = research_g, "research"
    else:
        g, src = cfg["g"], f"sub-sector ({subtype})"
    prov.append(f"g {g:.2%} ({src})")

    if cfg.get("src") and not ovr:
        prov.append(f"calibration: {cfg['src']}")
    if ovr.get("source"):
        prov.append(f"src: {ovr['source']} ({ovr.get('as_of', 'n/a')})")

    return {"coe": coe, "g": g, "subtype": subtype, "provenance": prov}


def _sreit_dpu(most_recent: dict, shares: float) -> Optional[float]:
    """Distribution per unit, in the reporting currency (not cents).

    S-REITs report DPU in cents; the research extractor captures it that
    way (`dpu_cents`). Fall back through the statement lines when no
    extraction is available.
    """
    dpu_c = _safe(most_recent.get("_sreit_dpu_cents_research"))
    if dpu_c and dpu_c > 0:
        return dpu_c / 100.0
    dps = _safe(most_recent.get("dividends_per_share"))
    if dps and dps > 0:
        return dps
    if shares and shares > 0:
        di = _safe(most_recent.get("distributable_income"))
        if di and di > 0:
            return di / shares
        paid = _safe(most_recent.get("dividends_and_distributions"))
        if paid:
            return abs(paid) / shares
    return None


def _compute_sreit_ddm(ticker: str, subtype: str, most_recent: dict,
                       shares: float, dpu_growth: float = 0.0):
    """Value an S-REIT on its distributions.

        value/unit = DPU_forward x (1 + g) / (CoE - g)

    Returns (value_per_unit, dpu_forward, assumptions) or None when the
    inputs cannot support it — notably when CoE approaches g, where the
    capitalisation factor diverges.
    """
    a = _sreit_ddm_assumptions(ticker, subtype, most_recent)
    coe, g = a["coe"], a["g"]
    if not (coe and g is not None and (coe - g) > 0.005):
        return None
    dpu = _sreit_dpu(most_recent, shares)
    if dpu is None or dpu <= 0:
        return None
    # One year of DPU growth to the forward figure the model capitalises.
    # Clamped: an S-REIT's distribution does not move 30% on trend, and a
    # wild scenario growth rate must not leak into the terminal value.
    _gr = max(min(dpu_growth or 0.0, 0.15), -0.15)
    dpu_fwd = dpu * (1.0 + _gr)
    value = dpu_fwd * (1.0 + g) / (coe - g)
    return round(value, 4), round(dpu_fwd, 6), a


_REIT_SUBTYPE_MULTIPLES: dict[str, dict[str, float]] = {
    # Data center — default DLR-class calibration. Green Street April 2026:
    # 5.0-5.3% implied cap for large DC REITs. Previous 4.5% was too tight;
    # only EQIX (interconnection moat) earns a sub-5% cap, routed through
    # the data_center_premium sub-type below.
    "data_center":          {"cap_rate": 0.050, "p_ffo": 22.0, "p_affo": 25.0},
    # Data center premium — interconnection moat (EQIX). Colocation +
    # interconnection revenue commands tighter cap rate than pure wholesale
    # (DLR). Preserved 4.5% Green Street high-end + 23x P/FFO growth premium.
    "data_center_premium":  {"cap_rate": 0.045, "p_ffo": 23.0, "p_affo": 26.0},
    "lab":                  {"cap_rate": 0.050, "p_ffo": 20.0, "p_affo": 22.0},
    "industrial":           {"cap_rate": 0.055, "p_ffo": 18.0, "p_affo": 20.0},
    # Self storage — Green Street April 2026 shows PSA / EXR ~5.5% (we were
    # 5.2%, slightly tighter than consensus). Move to 5.5% to match sector.
    "self_storage":         {"cap_rate": 0.055, "p_ffo": 19.0, "p_affo": 21.0},
    "residential":          {"cap_rate": 0.055, "p_ffo": 17.0, "p_affo": 19.0},
    # Net-lease / single-tenant / triple-net REITs (O, ADC, NNN, WPC, SRC, GOOD).
    # Traded as fixed-income proxies — long-duration contractual cash flows
    # (10-20yr initial lease terms, escalators, credit-tenant covenants).
    # Cap rate 5.0% reflects credit-tenant net-lease consensus (Green Street
    # April 2026: 5.2% blue-chip, UBS: 5.4% O-specific). Lower than retail
    # (6.2%) because tenant absorbs property opex/taxes/insurance, and
    # lower than industrial (5.5%) because contract duration is longer with
    # investment-grade counterparties.
    "net_lease":            {"cap_rate": 0.050, "p_ffo": 16.0, "p_affo": 18.0},
    "healthcare":           {"cap_rate": 0.060, "p_ffo": 15.0, "p_affo": 17.0},
    # Retail — Class-A mall / grocery-anchored strip average. Green Street
    # April 2026: SPG Class-A malls ~6.0%, strip retail 7.5%, outlets 7.0%.
    # Previous 6.8% penalized Class-A mall operators (SPG, MAC); 6.2%
    # blend better reflects diversified retail REIT quality.
    # Distressed mall REITs (e.g. MAC legacy portfolio) should ideally
    # route through a future 'retail_distressed' sub-type at 7.5%.
    "retail":               {"cap_rate": 0.062, "p_ffo": 14.0, "p_affo": 15.0},
    "office":               {"cap_rate": 0.075, "p_ffo": 12.0, "p_affo": 13.0},
    "hospitality":          {"cap_rate": 0.080, "p_ffo": 11.0, "p_affo": 12.0},
    # Infrastructure trusts (Keppel Infrastructure, Asian Pay Television,
    # Hutchison Port Holdings) — SGX/HK business trust structures that own
    # long-term concession assets rather than fee-simple property. Cap rate
    # is higher than property REITs to reflect terminal-value uncertainty at
    # concession expiry and lack of underlying property asset to liquidate.
    # P/FFO compressed because FFO is less stable (regulatory price caps,
    # concession step-downs). Treated as REITs for framework purposes per
    # SGX/HK market convention — they distribute 90%+ like S-REITs and trade
    # on yield + DPU sustainability.
    "infrastructure": {"cap_rate": 0.085, "p_ffo": 10.0, "p_affo": 11.0},
    "default":        {"cap_rate": 0.065, "p_ffo": 15.0, "p_affo": 17.0},
}


def _classify_reit_subtype(ticker: str, notes: str = "") -> str:
    """
    Classify a REIT into one of the 10 sub-types:
      data_center, lab, industrial, self_storage, residential, healthcare,
      retail, office, hospitality, non_reit
      + "default" (blended multiples) when no match.

    "infrastructure" catches SGX/HK business trusts that own long-term
    concession assets — Keppel Infrastructure Trust, Asian Pay Television,
    Hutchison Port Holdings — and applies a higher cap rate / lower P/FFO
    to reflect terminal-value risk at concession expiry. These still get
    REIT-framework valuation because they distribute 90%+ of cash flow
    like S-REITs and trade on yield + DPU sustainability per SGX/HK
    convention.

    Keyword-based; defaults to "default" on no match. Checked in this order
    (most specific first): infrastructure → data_center/lab → industrial/
    storage → residential → healthcare → retail (incl. SGX China retail
    trusts) → office → hospitality.
    """
    combined = (ticker + " " + (notes or "")).lower()
    keywords = [
        # Infrastructure trust gate — these own concession assets (power,
        # water, transport, telecom) and are valued as REITs with adjusted
        # cap rates (8.5% vs property REITs 4.5-6.5%).
        ("infrastructure", ("infrastructure trust", "business trust",
                            "infra trust", "pay television", "port trust",
                            "shipping trust", "maritime trust")),
        # Data center premium — interconnection / colocation moat. EQIX
        # commands the tightest cap in the sector (Green Street 4.5%) due
        # to network effects in meet-me rooms. Checked BEFORE data_center
        # so pure interconnection plays don't get averaged into wholesale DC.
        ("data_center_premium", ("equinix", "eqix", "interconnection",
                                 "interxion", "meet-me room", "ix fabric")),
        ("data_center",  ("data center", "data centre", "data-center", "digital realty",
                          "dlr", "gds", "keppel dc")),
        ("lab",          ("lab ", "life science", "biotech rent", "alexandria",
                          " are ", "parkway life")),
        ("industrial",   ("industrial", "warehouse", "logistics", "prologis", "pld",
                          "stag", "egp", "mapletree logistics", "mapletree industrial",
                          "frasers logistics", "ascendas reit", "ara logos",
                          "esr-logos")),
        ("self_storage", ("self storage", "self-storage", "storage", "psa",
                          "public storage", "exr", "extra space", "cube")),
        ("residential",  ("residential", "apartment", "multifamily", "single family",
                          "avb", "eqr", "essex", "inv", "camden", "mid-america",
                          "maa", "student accommodation", "centurion accommodation")),
        ("healthcare",   ("healthcare", "health care", "senior housing",
                          "medical office", "vtr", "pea", "omega", "welltower",
                          "well ", "hcp", "doc", "healthpeak", "first reit")),
        # Net-lease / triple-net / single-tenant — checked BEFORE retail because
        # Realty Income, Agree Realty, and NNN often contain "realty" / "retail"
        # tokens that would mis-match the retail bucket. Net-lease economics
        # (5.0% cap, 16x FFO) are substantially different from traditional
        # mall/strip retail (6.8% cap, 14x FFO) due to the triple-net structure.
        ("net_lease",    ("net lease", "net-lease", "triple net", "triple-net",
                          "nnn", "single tenant", "single-tenant",
                          "realty income", "agree realty", "adc ",
                          "spirit realty", "srch", "wpc", "w. p. carey",
                          "w.p. carey", "broadstone", "bnl ", "nnn reit",
                          "national retail properties")),
        ("retail",       ("retail", "mall", "shopping", "outlet", "spg", "simon",
                          "macerich", "mac", "reg", "kim", "kimco", "federal realty",
                          "frt", "china trust", "china reit", "capitaland china",
                          "sasseur", "lippo", "dasin", "starhill", "frasers centrepoint",
                          "cmt ", "mct ", "capitaland integrated",
                          "bhg retail")),
        ("office",       ("office", "tower", "corporate center", "boston properties",
                          "bxp", "vno", "sl green", "slg", "hiw", "kilroy", "krc",
                          "keppel reit", "suntec", "ireit global",
                          "india reit", "india trust", "capitaland india",
                          "it park", "it business park")),
        ("hospitality",  ("hospitality", "hotel", "lodging", "resort", "host", "hst",
                          "ryman", "rhp", "pebblebrook", "peb",
                          "apple hospitality", "aple",
                          "cdl hospitality", "far east hospitality", "ascott",
                          "frasers hospitality")),
    ]
    for subtype, kws in keywords:
        if any(k in combined for k in kws):
            return subtype
    return "default"


def _compute_reit_metrics(
    most_recent: dict,
    subtype: str = "default",
) -> dict:
    """
    Compute REIT-specific metrics (FFO, AFFO, NOI, cap rate, maintenance capex)
    from the latest annual series row.

    NOI proxy: EBITDA (operating income + D&A) is the cleanest readily-available
    approximation of Net Operating Income since most REIT GAAP filings don't
    break out property-level NOI. KNOWN LIMITATION: property-management fees
    are treated as OpEx below EBITDA in internalized PM structures and above
    it in externalized structures (many APAC REITs), leading to small cross-
    structure incomparability. Not material at v1.

    FFO  = net_income + depreciation_and_amortization
           (Nareit definition; adds back non-cash real estate depreciation)
    AFFO = FFO - normalized_maintenance_capex
           where normalized_maintenance_capex = min(|total_capex|,
           _REIT_MAINT_CAPEX_PCT[subtype] × revenue)
           (caps the capex deduction so growth REITs with acquisition capex
            aren't unfairly penalized)
    cap_rate_implied = NOI / (market_cap + total_debt - cash)
           (reverse-engineered from current EV; useful for auditing)

    Returns dict with ffo, affo, noi, normalized_maintenance_capex,
    cap_rate_implied. Missing components return as None; downstream method
    branches skip gracefully.
    """
    ni   = most_recent.get("net_income")
    da   = most_recent.get("depreciation_and_amortization")
    ocf  = most_recent.get("operating_cash_flow")
    capex = most_recent.get("capital_expenditure")
    rev  = most_recent.get("revenue")
    ebitda = most_recent.get("ebitda")

    # FFO = NI + D&A (standard Nareit definition)
    ffo = None
    if ni is not None and da is not None:
        ffo = ni + abs(da)   # D&A often negative on cash flow statement; take absolute
    elif ocf is not None:
        # Fallback: some issuers don't disclose D&A — use OCF as loose FFO proxy
        # (overstates because OCF = FFO + working capital changes)
        ffo = ocf

    # Maintenance capex floor — sub-type-aware as fraction of revenue
    maint_pct = _REIT_MAINT_CAPEX_PCT.get(subtype, _REIT_MAINT_CAPEX_PCT["default"])
    maint_capex = None
    if rev and rev > 0:
        rev_based = rev * maint_pct
        if capex is not None:
            # Capex from cash flow is typically negative; take absolute
            maint_capex = min(abs(capex), rev_based)
        else:
            maint_capex = rev_based

    # AFFO = FFO - normalized maintenance capex
    affo = None
    if ffo is not None and maint_capex is not None:
        affo = ffo - maint_capex
    elif ffo is not None:
        affo = ffo   # no capex info → AFFO = FFO (loose)

    # NOI ≈ EBITDA (limitation noted in docstring). For pure-play REITs
    # reporting Operating Income directly, EBITDA is a close proxy since
    # interest/tax are below the line and D&A adds back non-cash.
    noi = ebitda if ebitda and ebitda > 0 else None

    return {
        "ffo":                         ffo,
        "affo":                        affo,
        "noi":                         noi,
        "normalized_maintenance_capex": maint_capex,
        "maint_capex_pct_used":        maint_pct,
    }


# ── Tech sub-type multiples (Tier 2 item 4) ───────────────────────────────────
#
# Profile-level peer multiples for Tech sub-types. The prior architecture had
# all 8 Tech sub-profiles (Hyperscaler, Growth SaaS, Mature SaaS, etc.) share
# the same sector-level 22x EV/EBITDA, which incorrectly equates AMZN
# (Hyperscaler) with ADBE (Mature SaaS) at the same multiple.
#
# Sources: current trading multiples for representative tickers in each
# sub-type (calibrated 2026-04-22 using FMP /key-metrics-ttm):
#   Hyperscaler      (MSFT, GOOGL, AMZN, META):    blended 20-22x EV/EBITDA
#   Mature SaaS      (ADBE, NOW, CRM, ORCL):       28-32x EV/EBITDA
#   Growth SaaS      (SNOW, DDOG, CRWD, NET, MDB): 40-50x EV/EBITDA
#   Cybersecurity    (PANW, FTNT, ZS, S):          35-45x EV/EBITDA
#   Hyper-Growth     (PLTR, NET, SHOP):            55-65x EV/EBITDA
#   Semiconductor    (NVDA, AVGO, AMD):            separate sector (Semiconductor)
_TECH_SUBTYPE_MULTIPLES: dict[str, dict[str, float]] = {
    # Hyperscaler / Tech Conglomerate
    "Hyperscaler / Tech Conglomerate": {
        "ev_ebitda": 20.0, "ev_revenue": 7.5, "pe": 28.0, "p_s": 6.8, "ev_ebit": 24.0,
    },
    # Mature Platform / SaaS — Adobe, ServiceNow, Salesforce, Oracle
    # Calibration 2026-04-22: ADBE/ORCL trading at 15-22x EV/EBITDA in 2025
    # post-Figma + AI disruption concerns. Prior 30x was mid-2022 peak.
    "Mature Platform": {
        "ev_ebitda": 18.0, "ev_revenue": 6.5, "pe": 24.0, "p_s": 5.8, "ev_ebit": 22.0,
    },
    "Mature SaaS": {
        "ev_ebitda": 22.0, "ev_revenue": 10.0, "pe": 28.0, "p_s": 9.0, "ev_ebit": 26.0,
    },
    # Growth SaaS — Snowflake, Datadog, CrowdStrike, Cloudflare
    "Growth SaaS": {
        "ev_ebitda": 45.0, "ev_revenue": 22.0, "pe": 65.0, "p_s": 19.8, "ev_ebit": 54.0,
    },
    # Cybersecurity — Palo Alto, Fortinet, ZScaler, SentinelOne
    # Calibration 2026-04-22: PANW 65-75x EBITDA, CRWD 80-90x EBITDA in 2025-26
    # on mission-critical demand + AI-driven SOC expansion. Prior 40x was stale.
    "Cybersecurity / Mission-Critical SaaS": {
        "ev_ebitda": 55.0, "ev_revenue": 22.0, "pe": 70.0, "p_s": 19.8, "ev_ebit": 66.0,
    },
    # Hyper-Growth Platform — Palantir, Cloudflare, Shopify, ServiceTitan
    "Hyper-Growth Platform": {
        "ev_ebitda": 55.0, "ev_revenue": 25.0, "pe": 80.0, "p_s": 22.5, "ev_ebit": 66.0,
    },
    # High-Growth Tech / AI (pre-revenue or negative FCF) — reverse DCF preferred
    "High-Growth Tech / AI": {
        "ev_ebitda": 65.0, "ev_revenue": 30.0, "pe": 100.0, "p_s": 27.0, "ev_ebit": 78.0,
    },
    # Early Platform (GMV-model) — Airbnb, Uber, DoorDash
    "Early Platform": {
        "ev_ebitda": 25.0, "ev_revenue": 4.0, "pe": 35.0, "p_s": 3.6, "ev_ebit": 30.0,
    },
    # Levered Subscription — Comcast, Netflix
    "Levered Subscription": {
        "ev_ebitda": 12.0, "ev_revenue": 4.0, "pe": 18.0, "p_s": 3.6, "ev_ebit": 14.0,
    },
    # Default Tech fallback — matches prior sector-level numbers
    "default": {
        "ev_ebitda": 22.0, "ev_revenue": 6.5, "pe": 28.0, "p_s": 5.9, "ev_ebit": 26.0,
    },
}


def _tech_subtype_multiples(profile_name: str) -> dict:
    """Lookup tech sub-type multiples with default fallback."""
    return _TECH_SUBTYPE_MULTIPLES.get(profile_name, _TECH_SUBTYPE_MULTIPLES["default"])


def _terminal_multiple_ev_revenue(profile_name: str, scenario: str = "base") -> float:
    """Terminal EV/Revenue multiple with mature-profile convergence (Option III Spec 2).

    Growth-phase profiles converge to their mature equivalent at Y10+ so the
    terminal multiple reflects the profile the company will have matured into,
    not the hyper-growth multiple it trades on today.

    Scenario band:
      - High-SBC profiles (Growth SaaS, Cybersecurity, Hyper-Growth, AI):
        bear 0.70×, bull 1.30×. These names face genuine multiple compression
        in bear regimes — Mature SaaS in 2022 traded at 5-6x, not 8x.
      - Stable profiles (Banks, REITs, Consumer, Mature SaaS, Hyperscaler):
        legacy bear 0.80× / bull 1.20×. ±20% is the right severity for
        cash-generative profiles where dispersion is structurally narrower.
    """
    convergence = _TERMINAL_MULTIPLE_CONVERGENCE.get(profile_name, profile_name)
    mature_mults = _TECH_SUBTYPE_MULTIPLES.get(convergence, _TECH_SUBTYPE_MULTIPLES["default"])
    base = mature_mults["ev_revenue"]
    # Asymmetric tightening for high-SBC tech profiles: bear penalised
    # harder (-30%) AND bull tightened (-5%, going from legacy 1.20→1.15).
    # Earlier rev had bull at 1.30 which LOOSENED bull case vs legacy and
    # produced an MDB regression (bull IV $645→$691 on the user's screen).
    # Both bands now strictly tighter than legacy on both sides.
    if profile_name in _HIGH_SBC_PROFILES:
        if scenario == "bear":
            return base * 0.70
        elif scenario == "bull":
            return base * 1.15   # was 1.30 — fixed bull regression 2026-04-25
        return base
    # Legacy ±20% band for stable profiles (Banks, REITs, Mature SaaS, Hyperscaler)
    if scenario == "bear":
        return base * 0.80
    elif scenario == "bull":
        return base * 1.20
    return base


#: A software terminal EV/Revenue multiple prices revenue that converts at
#: software gross margins. Below this line the top line is transaction or
#: inventory revenue and the multiple is not the right one.
_SAAS_GROSS_MARGIN_FLOOR = 0.65
#: The gross margin the SaaS terminal multiples are calibrated on; used to
#: scale the terminal multiple when no peer multiple is available.
_SAAS_GROSS_MARGIN_REFERENCE = 0.80


def _gross_margin(row: Optional[dict]) -> Optional[float]:
    """Gross margin from a series row: gross profit, else revenue less cost of
    revenue. None when the row supports neither."""
    row = row or {}
    rev = _safe(row.get("revenue"))
    if not rev or rev <= 0:
        return None
    gp = _safe(row.get("gross_profit"))
    if gp is None:
        cor = _safe(row.get("cost_of_revenue"))
        if cor is None:
            return None
        gp = rev - cor
    return gp / rev


def _qualified_ev_revenue_multiple(
    sector: str,
    profile_name: str,
    scenario: str,
    peer: dict,
    row: Optional[dict],
) -> tuple[float, str]:
    """EV/Revenue multiple for the revenue-multiple methods, and its basis.

    Tech sub-type profiles used a mature SaaS terminal multiple (10x for a
    Hyper-Growth Platform) for any company routed to them. MELI, 2026-09-16:
    a marketplace and payments business whose peers trade at 2.65x sales was
    valued at 10x, producing $10,513 a share against a $1,829 price -- a
    quarter of the blend. A terminal SaaS multiple is only the right multiple
    for revenue that converts like software, so it is now margin-qualified:

    * not a tech sub-type               -> the peer multiple, as before;
    * gross margin >= 65% or unknown    -> the terminal multiple, as before;
    * gross margin below 65%            -> the peer median when there is one,
      else the terminal multiple scaled by (gross margin / 80%)^2.
    """
    if not _is_tech_subtype(sector, profile_name):
        return float(peer.get("ev_revenue", 4.0)), "peer"
    terminal = _terminal_multiple_ev_revenue(profile_name, scenario)
    gm = _gross_margin(row)
    if gm is None or gm >= _SAAS_GROSS_MARGIN_FLOOR:
        return terminal, "SaaS terminal"
    peer_mult = _safe(peer.get("ev_revenue"))
    if peer_mult is not None and peer_mult > 0:
        return peer_mult, (f"peer median — gross margin {gm:.0%} is below the "
                           f"{_SAAS_GROSS_MARGIN_FLOOR:.0%} SaaS floor")
    scaled = terminal * (max(gm, 0.0) / _SAAS_GROSS_MARGIN_REFERENCE) ** 2
    return scaled, (f"SaaS terminal scaled by (gross margin {gm:.0%} / "
                    f"{_SAAS_GROSS_MARGIN_REFERENCE:.0%})^2")


def _is_tech_subtype(sector: str, profile_name: str) -> bool:
    """True when the (sector, profile_name) pair warrants tech-specific
    multiples. Excludes Semiconductor (separate sector table).
    """
    return is_tech_sector(sector) and profile_name in _TECH_SUBTYPE_MULTIPLES


# ── Bank-specific valuation (Tier 2 item 3) ───────────────────────────────────
#
# Institutional-grade bank valuation. Replaces the prior primitive Residual
# Income formula (single-period ROE-CoE spread × 0.5) with a full 2-stage RI
# model with profile-specific ROE fade, CET1 capital-adequacy overlay,
# Tangible Book Value (P/TBV) multiple, and geography-aware sub-profiles.
#
# Design references:
#   * Damodaran "Valuing Financial Service Firms" (2013, updated 2026)
#   * McKinsey "Valuation: Measuring and Managing the Value of Companies"
#     ch. 36 (bank-specific chapter, 7th ed.)
#   * Basel III capital framework for CET1 / RWA mechanics
#
# Why not DCF for banks:
#   Banks are book-value businesses — interest-earning assets and deposits
#   ARE the business. Free cash flow is not a natural unit of output because
#   capital reinvestment (retained earnings becoming regulatory capital) is
#   an accounting flow, not a cash flow. RI sums excess-return-over-cost-of-
#   capital directly on the equity base.

# ── Per-profile bank calibration ─────────────────────────────────────────────

# Target ROE, CoE, P/TBV, P/E, fade years per sub-profile. Used by the
# 2-stage RI model and the P/TBV / P/E method branches. "CoE" overrides the
# engine's hybrid WACC for bank profiles because bank WACC collapses to CoE
# when D/(D+E) ≈ 0 (deposits are not equity).
#
# terminal_spread: ROE premium over CoE sustained in perpetuity (stage 2 of
# the 2-stage RI). Captures durable moat premium for scale-advantaged banks.
#   0.01 (+100 bps)    — GSIBs / Super-Regionals / Indian private — durable moat
#   0.005 (+50 bps)    — Investment Banks / Money Center EU / Brokerage —
#                        cyclical but scaled
#   0.0                — Regional / Mortgage-GSE / Neo — less moat durability
# Rationale: TV = (ROE_terminal - CoE) × BVPS_terminal / CoE, discounted back.
_BANK_PROFILE_CALIBRATION: dict[str, dict] = {
    # US Global Systemically Important Banks (GSIBs)
    # CoE 10.0% and g 3.0% per the Phillip Securities GGM table for JPM
    # (21 Oct 2025), which prices US GSIBs on the same Gordon Growth basis
    # as the SG names. Was 9.0%.
    "Money Center Bank":    {"target_roe": 0.12, "coe": 0.100, "p_tbv": 1.4, "pe": 12.0, "fade_years": 5,
                              "target_cet1": 0.12, "rwa_to_assets": 0.55, "terminal_spread": 0.010, "ggm_g": 0.03},
    # European Money Center — structural regulatory drag, higher CoE
    "Money Center Bank (EU)": {"target_roe": 0.10, "coe": 0.110, "p_tbv": 0.8, "pe": 8.0,  "fade_years": 5,
                              "target_cet1": 0.14, "rwa_to_assets": 0.60, "terminal_spread": 0.005, "ggm_g": 0.02},
    # Regional banks — healthy (USB, TFC, PNC)
    "Regional Bank":        {"target_roe": 0.11, "coe": 0.100, "p_tbv": 1.2, "pe": 11.0, "fade_years": 5,
                              "target_cet1": 0.11, "rwa_to_assets": 0.70, "terminal_spread": 0.0, "ggm_g": 0.025},
    # Super-regionals (TD, BMO, RBC)
    "Super-Regional Bank":  {"target_roe": 0.11, "coe": 0.095, "p_tbv": 1.3, "pe": 11.0, "fade_years": 5,
                              "target_cet1": 0.11, "rwa_to_assets": 0.65, "terminal_spread": 0.010, "ggm_g": 0.025},
    # EM banks — China SOEs (ICBC, CCB, BOC) — national-service risk
    "EM Bank":              {"target_roe": 0.14, "coe": 0.130, "p_tbv": 1.2, "pe": 9.0,  "fade_years": 5,
                              "target_cet1": 0.105, "rwa_to_assets": 0.65, "terminal_spread": 0.0, "ggm_g": 0.04},
    # EM Bank Premium — India private sector (HDFC, ICICI, Kotak) —
    # credit-to-GDP gap supports sustained 16-18% ROE
    "EM Bank (Premium)":    {"target_roe": 0.16, "coe": 0.130, "p_tbv": 2.0, "pe": 14.0, "fade_years": 7,
                              "target_cet1": 0.115, "rwa_to_assets": 0.62, "terminal_spread": 0.010, "ggm_g": 0.05},
    # Investment banks — cyclical (GS, MS)
    "Investment Bank":      {"target_roe": 0.13, "coe": 0.110, "p_tbv": 1.2, "pe": 10.0, "fade_years": 5,
                              "target_cet1": 0.13, "rwa_to_assets": 0.40, "terminal_spread": 0.005, "ggm_g": 0.03},
    # Mortgage/GSE (FNMA, FMCC) — conservatorship overhang
    "Mortgage/GSE":         {"target_roe": 0.09, "coe": 0.110, "p_tbv": 0.8, "pe": 9.0,  "fade_years": 5,
                              "target_cet1": 0.08, "rwa_to_assets": 0.50, "terminal_spread": 0.0, "ggm_g": 0.02},
    # Neo/Challenger banks — J-curve ROEs, extended fade
    "Neo/Challenger":       {"target_roe": 0.18, "coe": 0.120, "p_tbv": 2.8, "pe": 22.0, "fade_years": 10,
                              "target_cet1": 0.11, "rwa_to_assets": 0.45, "terminal_spread": 0.0, "ggm_g": 0.05},
    # Brokerage (SCHW, IBKR) — fee + NII blended
    "Brokerage":            {"target_roe": 0.16, "coe": 0.100, "p_tbv": 2.8, "pe": 18.0, "fade_years": 5,
                              "target_cet1": 0.10, "rwa_to_assets": 0.35, "terminal_spread": 0.005, "ggm_g": 0.035},
    # Singapore money-center banks (DBS / OCBC / UOB). Calibrated from
    # Phillip Securities Research Gordon Growth Model tables — DBS
    # (4 May 2026) and OCBC (11 May 2026): risk-free 2.5%, equity-risk
    # premium 6.3%, beta ~1.0-1.1 -> CoE 8.6-9.1%, terminal g 3.0-3.3%.
    # SG banks sustain structurally higher ROE than US GSIBs (DBS 16.6%,
    # OCBC 12.8% vs JPM ~12%) on a low-cost CASA deposit base and a
    # fee-heavy wealth franchise, while carrying a LOWER CoE than US peers
    # (AAA sovereign, MAS-supervised, SGD funding). Applying the US
    # "Money Center Bank" row (ROE 12% / CoE 9.0% / P/TBV 1.4x) to DBS
    # understated justified P/B by ~40% (1.5x vs the 2.51x the GGM
    # supports). Per-ticker ROE / CoE / g overrides live in
    # _BANK_GGM_OVERRIDES and take precedence over these defaults.
    "Money Center Bank (SG)": {"target_roe": 0.145, "coe": 0.088, "p_tbv": 2.0, "pe": 13.0, "fade_years": 7,
                              "target_cet1": 0.140, "rwa_to_assets": 0.55, "terminal_spread": 0.010,
                              "ggm_g": 0.031},
    # Default fallback
    "default":              {"target_roe": 0.11, "coe": 0.100, "p_tbv": 1.2, "pe": 11.0, "fade_years": 5,
                              "target_cet1": 0.11, "rwa_to_assets": 0.60, "terminal_spread": 0.0, "ggm_g": 0.03},
}


def _bank_profile_calibration(profile_name: str) -> dict:
    """Lookup bank calibration with default fallback."""
    return _BANK_PROFILE_CALIBRATION.get(profile_name, _BANK_PROFILE_CALIBRATION["default"])


#: Profiles whose liabilities ARE the product, so enterprise value has no
#: meaning and the EV-based multiples are dropped from the 12m target. This is
#: a DISPATCH set, not a taxonomy claim: ``FinTech`` and ``Asset Manager`` are
#: here because a name routed to them may be float-funded in fact (that is the
#: same judgement Phase 1.2A's Tier 2 makes by measurement), and
#: ``Bank / Lending Institution`` is here because it is the label FMP's own
#: routing returns.
#:
#: Hoisted to module level from inside :func:`run_dcf_agent`, where it was a
#: local at the 12m-target block. There is no module-level default to fall back
#: on: the local assignment is deleted, not shadowed.
#:
#: Note that a separate and unrelated ``_BANK_PROFILES`` exists in
#: ``src/pipeline.py``. They are not the same set and are not kept in step.
_BANK_PROFILES: frozenset[str] = frozenset({
    "Money Center Bank", "Regional Bank", "Mortgage/GSE",
    "Investment Bank", "Insurance", "FinTech", "Asset Manager",
    "Bank / Lending Institution",   # FMP routing label
})


# ── Per-ticker GGM assumptions extracted from analyst research ───────────
#
# Source-of-truth assumptions lifted from published broker Gordon Growth
# Model tables. These override the profile-level defaults because the
# ROE / CoE / g triplet is bank-specific and is the single largest driver
# of a bank's justified P/B — DBS at 2.51x and OCBC at 1.60x differ almost
# entirely through this triplet, not through anything the profile captures.
#
# Each row records `as_of` and `source` so the audit trail carries the
# vintage. Live deep-research extraction (_bank_*_research) still wins over
# these static rows, which in turn win over the profile defaults.
_BANK_GGM_OVERRIDES: dict[str, dict] = {
    # Phillip Securities Research, "DBS Group Holdings Ltd — Wealth flows
    # and fees drive growth", 4 May 2026 (SG2026_0093). GGM table:
    # Rf 2.5% + ERP 6.3% x beta 1.0 = CoE 8.6%; ROE 16.6%; g 3.3%
    # -> target P/B 2.51x on BVPS S$24.29 -> TP S$61.00.
    "D05.SI": {"roe": 0.166, "coe": 0.086, "ggm_g": 0.033, "rf": 0.025, "erp": 0.063, "beta": 1.0,
               "as_of": "2026-05-04", "source": "Phillip Securities Research SG2026_0093"},
    # Phillip Securities Research, "Oversea-Chinese Banking Corp Ltd —
    # Non-II growth drives strong start to FY26", 11 May 2026 (SG2026_0099).
    # GGM table: Rf 2.5% + ERP 6.3% x beta 1.1 = CoE 9.1%; ROE 12.8%;
    # g 3.0% -> target P/B 1.60x on BVPS S$13.76 -> TP S$22.00.
    "O39.SI": {"roe": 0.128, "coe": 0.091, "ggm_g": 0.030, "rf": 0.025, "erp": 0.063, "beta": 1.1,
               "as_of": "2026-05-11", "source": "Phillip Securities Research SG2026_0099"},
    # Phillip Securities Research, JPMorgan Chase & Co, 21 Oct 2025.
    # GGM table: CoE 10.0%, g 3.0%, ROE (tangible common) 19.5%
    # -> target P/B 2.37x -> TP US$305.
    "JPM":     {"roe": 0.195, "coe": 0.100, "ggm_g": 0.030,
                "as_of": "2025-10-21", "source": "Phillip Securities Research (JPM)"},
    # UOB (U11.SI) — no report in the reviewed set. Deliberately left to the
    # SG profile defaults rather than guessed; add a row when a GGM table
    # for UOB is sourced.
}


# Widest defensible gap between an extracted cost of equity and the
# profile's own. The profile CoE encodes a geography's risk-free rate plus
# equity-risk premium at roughly beta 1.0; 250 bps of slack covers a genuine
# beta spread of about 0.6-1.4 on a 6% ERP, and rejects the sector-average
# or WACC figures an extractor picks up when it cannot find a real one.
_BANK_COE_PLAUSIBLE_BAND = 0.025


def _coe_is_plausible(coe: float, cfg: dict) -> bool:
    """True when an extracted cost of equity sits near the profile's own."""
    if not coe or not (0.04 < coe < 0.25):
        return False
    base = cfg.get("coe")
    if not base:
        return True
    return abs(coe - base) <= _BANK_COE_PLAUSIBLE_BAND


def _analyst_basis(ticker: str, most_recent: dict) -> dict:
    """Parsed sell-side valuation basis for `ticker`, cached on the row.

    This is the INITIAL BENCHMARK layer. It outranks our static seed
    tables and the profile defaults for discount-rate parameters only —
    cost of equity, WACC, terminal growth — because no issuer discloses
    those and a published broker table states them deliberately. It never
    changes which profile a ticker routes to; where the analyst's method
    disagrees with the profile anchor that is raised as a flag instead.

    Matters most for SGX and HKEX names: FMP returns no analyst estimates
    there, so a deposited report is the only sell-side input available.
    """
    cached = most_recent.get("_analyst_basis_cache")
    if cached is not None:
        return cached
    basis: dict = {}
    try:
        from src.memory.analyst_basis import get_analyst_basis
        basis = get_analyst_basis(ticker) or {}
    except Exception:
        basis = {}
    most_recent["_analyst_basis_cache"] = basis
    return basis



# Gap between the RoTE being valued and the RoTE the filings imply, beyond
# which the run carries a flag. 300bps is roughly the width of a genuine
# statutory-vs-underlying difference for a large bank; wider than that and
# the two numbers are usually measuring different things.
_BANK_ROTE_DIVERGENCE_BPS = 300.0


def _return_on_book_basis(rote: Optional[float], bvps: Optional[float],
                          tbvps: Optional[float]) -> Optional[float]:
    """Convert a TANGIBLE-basis return (RoTE) to a BOOK-basis return (ROE).

    Same earnings, larger denominator: ROE = RoTE x TBV/BV.

    A Gordon multiple is only valid against the book it was derived on. The
    engine holds one return per bank but applies it two ways -- P/TBV x TBV
    on the valuation card, P/B x BVPS for the 12m target -- so exactly one
    of those needs the conversion. Skipping it inflated the P/B path: on
    02888.HK a research RoTE of 18% was used as though it were a return on
    total book, giving 2.15x x BVPS 183.46 = HK$395/sh against HK$350 on
    the tangible route for the same bank on the same day.

    Returns `rote` unchanged when the two books are indistinguishable or the
    inputs cannot support the conversion -- the caller then just gets the
    old behaviour rather than a None it has to special-case.
    """
    if rote is None:
        return None
    if not (bvps and tbvps and bvps > 0 and tbvps > 0):
        return rote
    return rote * (tbvps / bvps)


def _bank_ggm_assumptions(ticker: str, profile_name: str,
                          most_recent: dict) -> dict:
    """Resolve the (ROE, CoE, g) triplet driving the Gordon Growth P/B.

    The returned `roe` is on a TANGIBLE basis (RoTE) -- see `roe_basis`.
    Banks guide on RoTE, published broker GGM tables state RoTE, and the
    profile calibration targets are set against the same convention, so the
    three precedence sources below already agree on basis. Consumers that
    apply the multiple to total book must convert first, via
    `_return_on_book_basis`.

    Precedence, highest first:
      1. live deep-research extraction (_bank_target_roe_research)
      2. _BANK_GGM_OVERRIDES — a published broker GGM table for this ticker
      3. the profile calibration row

    Returns the triplet plus a `provenance` list naming which source won
    each field, so the audit flag can show its work.
    """
    cfg = _bank_profile_calibration(profile_name)
    ovr = _BANK_GGM_OVERRIDES.get((ticker or "").upper(), {})
    prov: list[str] = []

    research_roe = _safe(most_recent.get("_bank_target_roe_research"))
    if research_roe and 0.0 < research_roe < 0.60:
        roe, src = research_roe, "research"
    elif ovr.get("roe"):
        roe, src = ovr["roe"], "broker"
    else:
        # No published ROE for this name. The GGM wants a SUSTAINABLE
        # through-cycle ROE, which is a different quantity from the
        # profile's `target_roe` — that one is the conservative endpoint
        # the Residual Income model FADES to. Using the fade target alone
        # systematically under-values every high-return bank (JPM earns
        # ~19.5% against a 12% fade target). Take the midpoint of realised
        # and target ROE: mean-reversion, without pretending today's return
        # persists forever.
        # Realised return must be on the same TANGIBLE basis as the target
        # it is averaged with, or the midpoint silently mixes two books.
        _realised = None
        _ni = _safe(most_recent.get("net_income"))
        _eq = _safe(most_recent.get("total_equity"))
        _gw = _safe(most_recent.get("goodwill")) or 0.0
        _in = _safe(most_recent.get("intangible_assets")) or 0.0
        # Unfloored. This used to read `max(_eq - _gw - _in, _eq * 0.70)`, which
        # synthesized a positive tangible book for any name whose real one was
        # negative or merely small. The `if _teq > 0` guard below already does the
        # right thing with a negative — it declines the midpoint and falls back to
        # the profile's target ROE — so the floor only ever served to defeat that
        # guard, turning "cannot measure realised RoTE" into "measured a RoTE on
        # an invented book".
        _teq = (_eq - _gw - _in) if _eq else None
        if _ni and _teq and _teq > 0:
            _realised = _ni / _teq
        if _realised and 0.0 < _realised < 0.60:
            roe, src = (cfg["target_roe"] + _realised) / 2.0, "realised+target midpoint"
        else:
            roe, src = cfg["target_roe"], "profile"
    prov.append(f"ROE {roe:.1%} ({src})")

    # Cost of equity and terminal growth rank DIFFERENTLY from ROE, and the
    # distinction is what a company can actually tell you. ROE is disclosed
    # and guided by management, so a live extraction is authoritative. CoE
    # and g are not company facts at all — no issuer publishes its own cost
    # of equity — so an "extracted" CoE is always second-hand analyst
    # opinion scraped out of prose, competing against a hand-verified
    # broker valuation table that states the same quantity deliberately.
    # The table wins.
    #
    # This ordering was inverted on the D05.SI run of 2026-08-27: research
    # returned CoE 10.5%, which displaced Phillip's 8.6% and cut the target
    # P/B from 2.51x to 1.62x (S$61 -> S$39). A 10.5% cost of equity implies
    # a beta near 1.27 on Singapore's 2.5% risk-free and 6.3% ERP, which no
    # SG bank desk carries. The run also mixed three sources into one
    # multiple — research ROE, research CoE, broker g — and the GGM triplet
    # is only coherent when its discount-rate half comes from one framework.
    # A deposited analyst report is the benchmark layer above the static
    # seed table: it is the same kind of evidence, but ingested rather than
    # hand-copied, and carries its own vintage.
    _basis = _analyst_basis(ticker, most_recent)
    _basis_coe = _safe(_basis.get("cost_of_equity"))
    _basis_g = _safe(_basis.get("terminal_growth"))
    _basis_lbl = (f"analyst basis: {_basis.get('house') or 'sell-side'} "
                  f"{_basis.get('as_of') or ''}").strip()

    research_coe = _safe(most_recent.get("_bank_coe_research"))
    if _basis_coe and 0.04 < _basis_coe < 0.25:
        coe, src = _basis_coe, _basis_lbl
    elif ovr.get("coe"):
        coe, src = ovr["coe"], "broker"
        if research_coe and abs(research_coe - coe) > 0.005:
            prov.append(f"[research CoE {research_coe:.1%} not used — broker table]")
    elif research_coe and _coe_is_plausible(research_coe, cfg):
        coe, src = research_coe, "research"
    else:
        coe, src = cfg["coe"], "profile"
        if research_coe:
            prov.append(f"[research CoE {research_coe:.1%} rejected — implausible]")
    prov.append(f"CoE {coe:.1%} ({src})")

    research_g = _safe(most_recent.get("_bank_ggm_g_research"))
    if _basis_g is not None and 0.0 <= _basis_g < 0.07:
        g, src = _basis_g, _basis_lbl
    elif ovr.get("ggm_g") is not None:
        g, src = ovr["ggm_g"], "broker"
    elif research_g is not None and 0.0 <= research_g < 0.07:
        g, src = research_g, "research"
    else:
        g, src = cfg.get("ggm_g", 0.030), "profile"
    prov.append(f"g {g:.1%} ({src})")

    if ovr.get("source"):
        prov.append(f"src: {ovr['source']} ({ovr.get('as_of', 'n/a')})")

    return {"roe": roe, "coe": coe, "g": g, "provenance": prov,
            "roe_basis": "tangible", "source_note": ovr.get("source")}


def _compute_ggm_pb(ticker: str, profile_name: str, most_recent: dict,
                    shares: float):
    """Gordon Growth justified P/B valuation for a bank.

        target P/B   = (ROE - g) / (CoE - g)
        value/share  = target P/B x book value per share

    This is the method Asian bank desks actually publish — both the DBS and
    OCBC reports price entirely off it — and it was absent from the engine.
    The bank method set ran Residual Income + P/TBV + P/E + Excess Capital,
    none of which lets a bank's own sustainable ROE set its book multiple:
    P/TBV applied a fixed profile constant, so a 16.6%-ROE bank and a
    12%-ROE bank got the same 1.4x.

    Returns (value_per_share, target_pb, assumptions) or None when the
    inputs can't support it — notably when CoE <= g, where the formula
    diverges to infinity, and when tangible book per share is not positive,
    where there is no book for a P/B multiple to be applied to (see the hard
    stop below; a name that reaches this method with a negative tangible book
    is an asset-light franchise that was routed here by mistake, and the
    honest answer is no value rather than one computed off a synthesized book).
    """
    a = _bank_ggm_assumptions(ticker, profile_name, most_recent)
    roe, coe, g = a["roe"], a["coe"], a["g"]
    if not (coe and g is not None and (coe - g) > 0.005):
        return None          # denominator too small — formula unstable
    if roe is None or roe <= g:
        return None          # no franchise value above the cost of capital
    bvps = _safe(most_recent.get("book_value_per_share"))
    if (bvps is None or bvps <= 0) and shares and shares > 0:
        eq = _safe(most_recent.get("total_equity"))
        bvps = (eq / shares) if eq else None
    if bvps is None or bvps <= 0:
        return None
    # ── Negative / zero tangible book: hard stop ────────────────────────────
    # Computed independently of `_compute_bank_metrics`, whose `tbv_per_share`
    # is floored at 70% of equity. That floor is load-bearing THERE — JPM's
    # blind-strip derivation over-strips by ~$15B against the issuer's own
    # convention, which retains MSRs as tangible, so the floor keeps a real bank
    # from being marked down for a derivation artifact. It is fatal HERE, because
    # it converts "this franchise has no tangible book" into a positive number
    # that the GGM then multiplies.
    #
    # Visa is the case. An asset-light network whose goodwill and intangibles
    # from acquiring its processing franchise exceed total equity, so real
    # tangible book is negative. FMP reports the negative directly;
    # `_compute_bank_metrics` accepts a reported TBVPS only when it is `> 0`, so
    # it discarded the negative and substituted the floor. The method then
    # published a 12m target of $12.84 against a base IV of $428.47 — 3.0% of its
    # own valuation. ICE took the same path for the same reason.
    #
    # Do not synthesize positive equity for an asset-light franchise. Fail the
    # method outright: `_compute_method_value` returns None for it, and
    # `_blend_methods` skips None values and renormalises over the survivors, so
    # the weight is dropped rather than reallocated by hand.
    _reported_tbvps = _safe(most_recent.get("tangible_book_value_per_share"))
    if _reported_tbvps is not None:
        _true_tbvps = _reported_tbvps
    else:
        _eq_t = _safe(most_recent.get("total_equity"))
        _true_tbvps = (
            (_eq_t - (_safe(most_recent.get("goodwill")) or 0.0)
             - (_safe(most_recent.get("intangible_assets")) or 0.0)) / shares
            if (_eq_t and shares and shares > 0) else None
        )
    if _true_tbvps is not None and _true_tbvps <= 0:
        print(
            f"  [ggm-pb] {ticker}: tangible book per share {_true_tbvps:,.2f} "
            f"<= 0 ({'reported' if _reported_tbvps is not None else 'derived'}) "
            f"— GGM (P/B) declined rather than valued on a synthesized book"
        )
        return None
    # The triplet's ROE is a RoTE (a["roe_basis"] == "tangible"); this method
    # applies its multiple to TOTAL book, so it needs the book-basis return.
    # Without the conversion the multiple is derived on tangible equity and
    # then charged against the larger book — free franchise value equal to
    # the goodwill and intangibles the bank has already paid for.
    _bank_m = _compute_bank_metrics(most_recent, profile_name=profile_name)
    _tbvps = _safe(_bank_m.get("tbv_per_share"))
    roe_book = _return_on_book_basis(roe, bvps, _tbvps)
    a = {**a, "roe_book": roe_book, "bvps": bvps, "tbv_per_share": _tbvps}
    if roe_book is None or roe_book <= g:
        return None          # conversion pushed it under g — no franchise value
    target_pb = (roe_book - g) / (coe - g)
    # Guard against an implausible multiple from a bad ROE extraction.
    target_pb = max(0.3, min(target_pb, 4.0))
    return round(bvps * target_pb, 4), round(target_pb, 4), a


# Reporting-currency symbols for audit flags. Anything not listed falls back
# to the ISO code plus a space ("SEK 12.30"), which is unambiguous.
_CCY_SYMBOLS: dict[str, str] = {
    "USD": "$",   "SGD": "S$",  "HKD": "HK$", "CNY": "RMB", "CNH": "RMB",
    "EUR": "€",   "GBP": "£",   "JPY": "¥",   "AUD": "A$",  "CAD": "C$",
    "INR": "₹",   "KRW": "₩",   "TWD": "NT$", "CHF": "CHF ", "MYR": "RM",
}


def _bank_total_income(row: dict) -> Optional[float]:
    """Street "Total Income" for a bank = NII + non-interest income.

    Data providers report a bank's `revenue` as GROSS interest income plus
    non-interest income. The street — and every analyst consensus revenue
    estimate — uses Total Income, which is NET of interest expense. For DBS
    FY2025 that is S$37.9bn (gross) vs S$22.9bn (total income): a 1.65x gap.

    Feeding the gross figure in wherever "revenue" is expected corrupts
    three things at once:
      * analyst growth bands (consensus total income ÷ gross base implied
        ~-40% growth, which pinned bear/base/bull to the -30% clamp floor),
      * the efficiency ratio / cost-income ratio (opex ÷ gross understated
        DBS's CIR as 27.3% against a reported 38.7%),
      * Year-1 revenue and EPS, which inherit the bogus growth rate.

    Validated against Phillip Securities' reported Total Income for DBS:
        FY2023  derived 20,134  vs reported 20,180  (-0.2%)
        FY2024  derived 22,217  vs reported 22,297  (-0.4%)

    Returns None when the row can't support the derivation, and returns
    `revenue` unchanged when revenue is already a net figure (i.e. it does
    not exceed interest income), so US filers that already report net
    revenue are passed through untouched.
    """
    revenue = row.get("revenue")
    if revenue is None or revenue <= 0:
        return None
    int_inc = row.get("interest_income")
    int_exp = row.get("interest_expense")
    if int_exp is None:
        return revenue
    # Gross-revenue convention detected only when revenue exceeds interest
    # income — otherwise `revenue` is already net and subtracting interest
    # expense a second time would double-count it.
    if int_inc is not None and revenue <= int_inc:
        return revenue
    total_income = revenue - abs(int_exp)
    # Sanity: total income must stay a plausible share of gross revenue.
    # Below 25% means the interest-expense line is mis-scaled; fall back.
    if total_income <= 0 or total_income < revenue * 0.25:
        return revenue
    return total_income


def _compute_ppop(row: dict) -> Optional[float]:
    """
    Pre-Provision Operating Profit for a single annual row. Preferred metric
    for bank operating quality — strips out cyclical provisioning noise.

    Computation priority (highest fidelity first):
      1. operating_income + abs(provisions) — cleanest when both available
         (US banks on FMP, before provisions are netted below the operating
         line). Matches DBS Research's PPOP chart methodology.
      2. NII + non_interest_income − operating_expense
         (non_interest_income ≈ revenue − interest_income when revenue is
         TOTAL bank revenue)
      3. revenue − abs(interest_expense) − abs(operating_expense)
         (US-FMP fallback — broken on yfinance-sourced SGX/HK banks where
         revenue may already net interest_expense, producing negatives)

    Returns None when none of the three paths have enough data.
    """
    op_income  = row.get("operating_income")
    provisions = row.get("provision_for_loan_losses")
    # Priority 1: operating_income + provisions add-back
    if op_income is not None and provisions is not None:
        return op_income + abs(provisions)

    revenue    = row.get("revenue")
    int_inc    = row.get("interest_income")
    int_exp    = row.get("interest_expense")
    op_exp     = row.get("operating_expense")

    # Priority 2: NII + non-interest-income − opex (cleanest for banks with
    # full income statement disclosure)
    if int_inc is not None and int_exp is not None and revenue is not None and op_exp is not None:
        nii = int_inc - abs(int_exp)
        non_int_inc = revenue - int_inc   # derive non-interest income
        # Sanity check: non-interest income should be non-negative (else the
        # revenue field doesn't include interest income and we'd double-subtract)
        if non_int_inc >= 0:
            return nii + non_int_inc - abs(op_exp)

    # Priority 3: revenue − interest_expense − opex (legacy US-FMP formula)
    # Only use when op_income is absent AND we have all three inputs. Explicit
    # positivity gate — negative values are almost always a sign that the data
    # source (typically yfinance for SGX/HK) has non-standard revenue semantics.
    if revenue is not None and int_exp is not None and op_exp is not None:
        candidate = revenue - abs(int_exp) - abs(op_exp)
        if candidate > 0:
            return candidate
    return None


def _compute_bank_metrics(most_recent: dict, profile_name: str = "default") -> dict:
    """
    Compute derived bank KPIs from the latest annual line-item row.

    Returns dict with:
        nim                  — net interest margin (NII / interest-earning assets)
        net_interest_income  — interest_income − interest_expense
        efficiency_ratio     — operating_expense / (NII + non-interest income)
                                (proxied when non-interest income breakout unavailable)
        credit_cost_ratio    — provision_for_loan_losses / total_loans
                                (fallback: / total_assets proxy)
        tbv                  — total_equity − goodwill − intangible_assets
        tbv_per_share        — TBV / shares
        roe                  — net_income / total_equity
        retention_rate       — 1 − (dividends_paid / net_income), clamped [0.3, 0.8]
        rwa_estimate         — total_assets × profile-specific RWA proxy ratio
        cet1_implied         — total_equity / rwa_estimate (proxy when deep-research
                                doesn't provide actual cet1_ratio)

    Missing components return as None; downstream method branches skip gracefully.
    """
    ni        = most_recent.get("net_income")
    equity    = most_recent.get("total_equity")
    assets    = most_recent.get("total_assets")
    int_inc   = most_recent.get("interest_income")
    int_exp   = most_recent.get("interest_expense")
    op_exp    = most_recent.get("operating_expense")
    revenue   = most_recent.get("revenue")
    prov      = most_recent.get("provision_for_loan_losses")
    dividends_ps = most_recent.get("dividends_per_share") or 0.0
    shares    = most_recent.get("shares_outstanding")
    goodwill  = most_recent.get("goodwill") or 0.0
    intang    = most_recent.get("intangible_assets") or 0.0

    # NII — bank's core top-line
    nii = None
    if int_inc is not None and int_exp is not None:
        nii = int_inc - abs(int_exp)
    elif revenue is not None and int_exp is not None:
        # Fallback: revenue − interest expense approximates NII for banks
        # that don't cleanly break out interest_income
        nii = revenue - abs(int_exp)

    # Total income (NII + non-interest income) — the street's revenue line
    # for a bank, and the correct denominator for the cost-income ratio.
    total_income = _bank_total_income(most_recent)

    # NIM — NII / interest-EARNING assets. Dividing by TOTAL assets
    # understates the margin, because cash, goodwill, premises and other
    # non-earning assets sit in the denominator without generating any
    # interest. IEA typically runs ~85% of a commercial bank's balance
    # sheet; DBS FY2025 lands at 1.62% on total assets vs the 1.89% the
    # bank actually reported, and the gap is entirely this. Prefer a
    # directly-reported interest-earning-asset figure when present.
    _IEA_TO_ASSETS = 0.85
    nim = None
    iea = _safe(most_recent.get("interest_earning_assets"))
    if not (iea and iea > 0):
        iea = (assets * _IEA_TO_ASSETS) if (assets and assets > 0) else None
    if nii and iea and iea > 0:
        nim = nii / iea

    # Efficiency ratio (cost-income ratio) — operating_expense / total income.
    # Must NOT use `revenue`: for banks that is gross interest income plus
    # non-interest income, so the ratio comes out flattered by roughly the
    # ratio of gross to net income — DBS printed 27.3% against a reported
    # cost-income ratio of 38.7%.
    efficiency_ratio = None
    if op_exp and total_income and total_income > 0:
        efficiency_ratio = abs(op_exp) / total_income

    # Credit cost — provisions / total_assets proxy (total_loans unavailable)
    credit_cost_ratio = None
    if prov is not None and assets and assets > 0:
        credit_cost_ratio = abs(prov) / assets

    # TBV — strip goodwill + intangibles from equity
    # Note: per Gemini critique, do NOT aggressively strip deferred tax assets
    # (DTAs) — these are often recoverable in most jurisdictions. DTAs are a
    # separate balance sheet line not included in our goodwill/intangible map.
    # Prefer FMP's pre-computed tangibleBookValuePerShare from /stable/ratios
    # (via _RATIOS_MAP). FMP's ratio applies the bank's own reporting convention
    # — e.g. JPM excludes goodwill + "soft" intangibles but retains MSRs
    # (Mortgage Servicing Rights) as tangible. Our blind-strip derivation
    # over-strips for JPM by ~$15B and hits the 70% floor, producing $90.81/sh
    # instead of reported $106.85/sh. Derivation remains as fallback for
    # smaller/foreign banks where FMP hasn't pre-computed the ratio.
    tbv = None
    if equity is not None:
        tbv = max(equity - (goodwill or 0) - (intang or 0), equity * 0.70)
    tbv_ps_direct = most_recent.get("tangible_book_value_per_share")
    if tbv_ps_direct and tbv_ps_direct > 0:
        tbv_per_share = tbv_ps_direct                     # Primary — FMP direct
        if shares and shares > 0:
            tbv = tbv_ps_direct * shares                  # Back-solve total TBV
    elif tbv is not None and shares and shares > 0:
        tbv_per_share = tbv / shares                      # Fallback — our derivation
    else:
        tbv_per_share = None

    # ROE + retention rate for 2-stage RI projection. Buybacks are treated
    # as distributions to shareholders (same economic substance as dividends
    # per Gemini critique) — otherwise retention is wildly overstated for
    # banks like JPM that return 60%+ of earnings via buybacks.
    roe = (ni / equity) if (ni is not None and equity and equity > 0) else None
    # RoTE — return on TANGIBLE equity. Distinct from `roe` and NOT
    # interchangeable with it: a justified P/TBV multiple requires a return
    # measured on the same book it will be applied to. Feeding `roe` (total
    # equity, goodwill and intangibles included) into a P/TBV identity and
    # then multiplying by tangible book understates fair value by roughly
    # the BV/TBV ratio — on 02888.HK that was 9.59% against a 10.84% RoTE.
    # Banks also guide on RoTE, so this is the basis a research-sourced
    # target ROE is already stated on.
    rote = (ni / tbv) if (ni is not None and tbv and tbv > 0) else None
    buybacks = most_recent.get("share_buyback") or most_recent.get("common_stock_repurchased") or 0
    # Normalize sign — cash flow statement may report buybacks as negative
    buybacks = abs(buybacks) if buybacks else 0
    retention_rate = None
    if ni and ni > 0:
        total_payout = 0.0
        if dividends_ps and shares:
            total_payout += dividends_ps * shares
        total_payout += buybacks
        payout_ratio = total_payout / ni
        retention_rate = max(0.30, min(0.80, 1.0 - payout_ratio))
    else:
        retention_rate = 0.60   # default: banks retain ~60% on average

    # RWA proxy — from profile calibration table
    cfg = _bank_profile_calibration(profile_name)
    rwa_estimate = (assets * cfg["rwa_to_assets"]) if (assets and assets > 0) else None
    cet1_implied = (equity / rwa_estimate) if (rwa_estimate and rwa_estimate > 0 and equity) else None

    return {
        "nii":                 nii,
        "total_income":        total_income,
        "nim":                 nim,
        "efficiency_ratio":    efficiency_ratio,
        "credit_cost_ratio":   credit_cost_ratio,
        "tbv":                 tbv,
        "tbv_per_share":       tbv_per_share,
        "roe":                 roe,
        "rote":                rote,
        "retention_rate":      retention_rate,
        "rwa_estimate":        rwa_estimate,
        "cet1_implied":        cet1_implied,
    }


def _compute_residual_income_2stage(
    most_recent: dict,
    shares: float,
    profile_name: str,
    research_target_roe: Optional[float] = None,
) -> Optional[float]:
    """
    Full 2-stage Residual Income model for banks (Damodaran/McKinsey standard).

    Stage 1 (5 years, or 7/10 for India/Neo): ROE fades linearly from current
             level to profile target. BVPS grows at retention × ROE per year.
    Stage 2 (terminal): ROE = CoE, so RI = 0 → TV contribution is zero.
             (This is the "fair value" assumption: no excess return in perpetuity.)

    Formula:
        V_per_share = BVPS_0 + Σ_{t=1..N} RI_t / (1 + CoE)^t
        RI_t = (ROE_t - CoE) × BVPS_{t-1}
        BVPS_t = BVPS_{t-1} × (1 + retention_rate × ROE_t)

    research_target_roe overrides the profile's target_roe when deep research
    provides a management-guided target (e.g. "JPM targets 17% ROTCE through
    cycle" from earnings calls).

    Returns None when inputs insufficient.
    """
    bank_m = _compute_bank_metrics(most_recent, profile_name)
    cfg    = _bank_profile_calibration(profile_name)

    roe_current = bank_m.get("roe")
    bvps_0      = most_recent.get("book_value_per_share")
    equity      = most_recent.get("total_equity")

    # Fall back to TBV per share when BVPS missing (happens on some
    # HK/SG data sources where yfinance only exposes total_equity)
    if (bvps_0 is None or bvps_0 <= 0) and bank_m.get("tbv_per_share"):
        bvps_0 = bank_m["tbv_per_share"]

    if (roe_current is None or bvps_0 is None or bvps_0 <= 0
            or shares <= 0 or equity is None or equity <= 0):
        return None

    coe         = cfg["coe"]
    target_roe  = research_target_roe if research_target_roe else cfg["target_roe"]
    fade_years  = cfg["fade_years"]
    retention   = bank_m["retention_rate"]

    # Clamp current ROE to prevent pathological extremes (negative ROE, >50% ROE)
    roe_current = max(-0.05, min(0.50, roe_current))

    v_per_share = bvps_0
    bvps_t = bvps_0
    for t in range(1, fade_years + 1):
        # Linear fade from current to target
        fade_frac = t / fade_years
        roe_t = roe_current + (target_roe - roe_current) * fade_frac

        ri_t = (roe_t - coe) * bvps_t
        pv_ri = ri_t / ((1 + coe) ** t)
        v_per_share += pv_ri

        # Grow book value for next period (retained earnings compound)
        bvps_t = bvps_t * (1 + retention * roe_t)

    # Terminal: ROE fades to (CoE + terminal_spread) in perpetuity. Captures
    # durable moat premium for scale-advantaged banks (GSIBs, Indian privates,
    # Super-Regionals) that sustainably earn above CoE forever. Setting
    # terminal_spread=0 recovers the Damodaran-standard conservative TV=0.
    # Per Gemini critique: even a small 50-100 bps moat premium closes the
    # "missing 30%" gap we see on JPM/GS/DBS where current IV is 55-70% of
    # market price. Perpetuity formula:
    #   TV = (terminal_spread) × BVPS_terminal / CoE
    # Discounted back: PV(TV) = TV / (1 + CoE)^fade_years
    terminal_spread = cfg.get("terminal_spread", 0.0)
    if terminal_spread > 0 and coe > 0:
        tv_per_share = (terminal_spread * bvps_t) / coe
        pv_tv = tv_per_share / ((1 + coe) ** fade_years)
        v_per_share += pv_tv

    # Floor at 50% of TBV to prevent deep pathological discounts when
    # current ROE is transiently negative (e.g. 2020 COVID year for US banks)
    floor = bank_m.get("tbv_per_share") or (bvps_0 * 0.85)
    return max(v_per_share, floor * 0.50)


def _compute_excess_capital(
    most_recent: dict,
    shares: float,
    profile_name: str,
    research_cet1: Optional[float] = None,
) -> Optional[float]:
    """
    Excess-capital-per-share from CET1 overlay.

    If CET1 > target: excess_capital is distributable → adds to IV
                      (haircut 0.7x since not all excess is truly releasable —
                      management buffer, pending stress test, etc.)
    If CET1 < target: capital_deficit must be retained → subtracts from IV
                      (full haircut — regulator can force dilutive raise)

    Asymmetric haircut matches regulatory reality: approval to deploy excess
    is much slower than approval to retain.

    Returns positive, negative, or None (no data).
    """
    cfg = _bank_profile_calibration(profile_name)
    target_cet1  = cfg["target_cet1"]
    rwa_ratio    = cfg["rwa_to_assets"]

    cet1_actual = research_cet1 if research_cet1 else None
    if cet1_actual is None:
        # Use the implied CET1 from book equity / proxy RWA as a rough fallback
        bank_m = _compute_bank_metrics(most_recent, profile_name)
        cet1_actual = bank_m.get("cet1_implied")

    if cet1_actual is None:
        return None

    total_assets = most_recent.get("total_assets")
    if not total_assets or total_assets <= 0 or shares <= 0:
        return None

    rwa = total_assets * rwa_ratio

    if cet1_actual >= target_cet1:
        # Excess — haircut by 30% (not all distributable)
        excess_dollars = (cet1_actual - target_cet1) * rwa
        return (excess_dollars / shares) * 0.70
    else:
        # Deficit — full haircut (must be retained)
        deficit_dollars = (target_cet1 - cet1_actual) * rwa
        return -(deficit_dollars / shares)


# ── Insider-activity WACC overlay (Tier 3) ────────────────────────────────────

def _insider_wacc_modifier(
    insider_data: dict | None,
    market_cap: float | None,
) -> tuple[float, str]:
    """
    Translate the Phase 2.5 insider-activity summary into a WACC modifier.

    Concentrated insider BUYING is a management-conviction signal that the
    market typically under-reacts to for ~6-12 months (Lakonishok & Lee 2001,
    Cohen-Malloy-Pomorski 2012). Cluster buys (multiple insiders <30 days)
    and CEO/CFO conviction sales are the highest-signal sub-cases.

    Mechanism: small ±bp modifier on WACC. Chosen over growth_base adjustment
    because (a) growth_base is already captured by deep-research dcf_calibration
    and analyst estimates, and (b) WACC is the single cleanest lever to surface
    a "management prior" without double-counting other signals.

    Returns (bps_modifier, audit_string). If no usable data, returns (0.0, "").
    bps_modifier is clamped to [-50, +50] (recap spec).

    Scaling:
        signal_pct = net_buying_12m_usd / market_cap
        base_bps   = clamp(-signal_pct * 5000, -25, +25)
          → 0.5% of market cap net-bought maps to -25 bp WACC (tightening)
          → 0.5% net sold maps to +25 bp (loosening, skeptical prior)
        +  cluster_buy with 30d net > 0:    -10 bp (amplify conviction)
        +  conviction_sell_flag:            +15 bp (widen on CEO/CFO >$5M dump)
        final clamp [-50, +50] so no single signal dominates the DCF.
    """
    if not insider_data or not market_cap or market_cap <= 0:
        return 0.0, ""

    net_12m = float(insider_data.get("net_buying_12m_usd") or 0.0)
    net_30d = float(insider_data.get("net_buying_30d_usd") or 0.0)
    cluster = bool(insider_data.get("cluster_buy"))
    conv_sell = bool(insider_data.get("conviction_sell_flag"))
    signal_pct = net_12m / market_cap
    gross_buy  = float(insider_data.get("gross_buy_value_12m") or 0.0)
    gross_sell = float(insider_data.get("gross_sell_value_12m") or 0.0)

    # Skip if the signal is noise: tiny activity relative to company size
    # (< 0.02% of market cap) produces sub-basis-point moves after clamping,
    # not worth emitting an audit flag for.
    if abs(signal_pct) < 0.0002 and not cluster and not conv_sell:
        return 0.0, ""

    base_bps = -max(-25.0, min(25.0, signal_pct * 5000))
    if cluster and net_30d > 0:
        base_bps -= 10.0
    if conv_sell:
        base_bps += 15.0
    final_bps = max(-50.0, min(50.0, base_bps))

    # Build audit line with dollar values scaled for readability
    def _fmt(v: float) -> str:
        absv = abs(v)
        if absv >= 1e9:
            return f"${v/1e9:.2f}B"
        if absv >= 1e6:
            return f"${v/1e6:.1f}M"
        if absv >= 1e3:
            return f"${v/1e3:.0f}K"
        return f"${v:.0f}"

    modifier_sign = "tightening" if final_bps < 0 else ("widening" if final_bps > 0 else "no-op")
    audit = (
        f"Insider activity: net_12m={_fmt(net_12m)} ({signal_pct:+.2%} mkt cap), "
        f"gross {_fmt(gross_buy)} buy / {_fmt(gross_sell)} sell"
        + (f", cluster_buy" if cluster else "")
        + (f", conviction_sell" if conv_sell else "")
        + f" -> WACC {final_bps:+.0f}bp ({modifier_sign})"
    )
    return final_bps, audit


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

def _minority_interest(most_recent: dict) -> float:
    """Book value of the stake in consolidated subsidiaries owned by others.

    Book is a proxy for market, and a rough one -- but the alternative in use
    was zero, which is not a proxy for anything.
    """
    mi = _safe((most_recent or {}).get("minority_interest"))
    # Some filers report it as a negative (accumulated deficit at the sub).
    # Deducting a negative would ADD value on the strength of somebody else's
    # losses, so floor it.
    return max(mi, 0.0) if mi is not None else 0.0


#: Annual line items published in `financials_used` for the Excel export.
_FINANCIALS_USED_FIELDS: tuple[str, ...] = (
    "period", "revenue", "gross_profit", "operating_income", "ebit", "ebitda",
    "net_income", "operating_cash_flow", "capital_expenditure", "free_cash_flow",
    "stock_based_compensation", "fcf_owner_earnings", "depreciation_and_amortization",
    "cash_and_equivalents", "short_term_investments", "total_debt", "net_debt",
    "minority_interest", "total_equity", "total_assets", "shares_outstanding",
    "book_value_per_share", "dividends_per_share",
)


# ── Leg-input trace (feeds the Excel export) ────────────────────────────────
#
# Every valuation leg records the inputs it was computed from, so an analyst
# can rebuild the number: metric x multiple = EV, less net debt and minority
# interest = equity, / shares = per share. Context-scoped rather than threaded
# through `_compute_method_value`'s signature, so the ~40 branches record what
# they have without changing how they are called. `_traced_method_value` opens
# a trace; `_leg_trace` writes into whichever trace is open (none: no-op).
import contextvars as _contextvars

_LEG_TRACE: "_contextvars.ContextVar[Optional[dict]]" = _contextvars.ContextVar(
    "_LEG_TRACE", default=None)


def _leg_trace(**fields) -> None:
    t = _LEG_TRACE.get()
    if t is None:
        return
    # Full precision: the export rebuilds each leg from these inputs, and a
    # rate rounded to 8 d.p. times a 1e11 revenue base leaves a residual.
    t.update(fields)


def _traced_method_value(**kwargs) -> tuple[Optional[float], dict]:
    """`_compute_method_value` plus the inputs the leg recorded."""
    trace: dict = {}
    token = _LEG_TRACE.set(trace)
    try:
        value = _compute_method_value(**kwargs)
    finally:
        _LEG_TRACE.reset(token)
    trace["value"] = value
    return value, trace


def _ev_to_equity_ps(
    ev: float,
    net_debt: Optional[float],
    most_recent: dict,
    shares: float,
) -> Optional[float]:
    """Enterprise value -> equity value per share.

    An EV multiple prices the WHOLE enterprise, including the parts of
    consolidated subsidiaries that belong to somebody else. The bridge is
    EV - net debt - minority interest; this engine stopped at net debt, so
    the minority's share of every consolidated terminal, mall and plantation
    was handed to the parent's shareholders.

    On SGX that is not an edge case: 19% of the large-cap universe carries
    minority interest worth 10% or more of parent equity, and the worst
    affected -- Jardine C&C 120%, Frasers Property 79%, HPH Trust 66%, Thai
    Beverage 53% -- were exactly the names whose valuations would not
    reconcile to their traded prices.

    Preferred equity belongs in this bridge too, but is not carried in the
    line items, so it is not deducted and remains a known overstatement for
    the handful of names that have it.
    """
    if not shares or shares <= 0:
        return None
    _mi = _minority_interest(most_recent)
    equity = ev - (net_debt or 0.0) - _mi
    _leg_trace(ev=float(ev), net_debt=float(net_debt or 0.0),
               minority_interest=float(_mi), equity=float(equity),
               shares=float(shares), floored_at_zero=equity < 0)
    return max(equity / shares, 0.0)


def _multiples_trace(peer: Optional[dict]) -> dict:
    """The peer multiples a valuation actually used, with their provenance.

    A multiple nobody can trace is a multiple nobody can check. Reads the
    underscore-prefixed provenance `get_sector_peer_multiples` attaches, and
    is deliberately tolerant of its absence -- an older caller that returns a
    bare dict still yields the values, just with basis "unknown".
    """
    if not isinstance(peer, dict):
        return {}
    basis = peer.get("_comp_basis") or {}
    fields = {}
    for name, value in peer.items():
        if name.startswith("_") or not isinstance(value, (int, float)):
            continue
        b = basis.get(name) or {}
        fields[name] = {
            "value": round(float(value), 4),
            "basis": b.get("basis", "unknown"),
            "cohort": b.get("cohort"),
            "peer_count": b.get("peer_count"),
        }
        # The basket's identity when it came from live comps (absent for
        # static/dynamic tables), so the export can list the named members.
        if b.get("key"):
            fields[name]["key"] = b.get("key")
            fields[name]["exchange"] = b.get("exchange")
    age = peer.get("_comp_age_days")
    return {
        "fields": fields,
        "comp_market": peer.get("_comp_market"),
        "comp_age_days": round(age, 2) if isinstance(age, (int, float)) else None,
        # Stated rather than implied: a reader should not have to know
        # MAX_AGE_DAYS to tell whether these are measured or fallback values.
        "all_static": bool(fields) and all(
            f["basis"] == "static" for f in fields.values()),
        # No field at all is the case that reads most reassuringly and means
        # the least: `all_static: false` over an empty set says "not fallback
        # values" when in fact nothing was measured and every multiple came
        # from the profile's own defaults. Seatrium priced this way -- SGX
        # lists three energy names above the universe floor, against a
        # five-peer minimum, so no Singapore energy basket exists to build.
        "no_peer_multiples": not fields,
    }


#: Every spelling the EV/EBITDA branch below answers to. A profile-table
#: method name that is not a literal here (or in `_DCF_PROJECTION_FAMILY`, or
#: one of the other branch sets) is dispatched, matches nothing, falls through
#: to the trailing `return None`, and its weight is silently renormalised onto
#: the survivors. Two such names existed and neither was caught, because no
#: ticker in the golden basket routes to their profiles:
#:
#:     "EV/EBITDA (Norm)"      Steel / Metals    anchor, weight 0.50
#:     "EV/EBIT (Pre-bonus)"   Ad / Consulting   anchor, weight 0.40
#:
#: Both carried `"implementable": True` and a prose `"note": "proxied by ..."`.
#: The note was the author's intent; the machine-readable `"proxy"` field — the
#: one the dispatch at `methods_to_compute` actually reads, and the one 32
#: other entries use — was never set, and `implementable: True` means the
#: proxy branch is not taken either. So the anchor of a steel company was
#: valued on P/BV + FCF Yield + P/E alone, and an ad agency on FCF Yield + P/E
#: + Rev DCF. `tests/test_profile_method_names_are_dispatched.py` pins the
#: general invariant over every profile in the table, not just these two.
_EV_MULTIPLE_METHODS: frozenset[str] = frozenset({
    "EV/EBITDA", "EV/EBIT", "EV/EBIT (Pre-bonus)", "EV/EBITDAR",
})

#: The subset of the above whose metric is EBIT rather than EBITDA. Named
#: rather than compared against the single string "EV/EBIT", because the
#: comparison is what would have routed the pre-bonus spelling to EBITDA: the
#: branch discriminates on `method_name != "EV/EBIT"`, so any new EBIT
#: spelling added to the set silently becomes an EBITDA method.
_EV_EBIT_METHODS: frozenset[str] = frozenset({"EV/EBIT", "EV/EBIT (Pre-bonus)"})


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
    ticker: str = "",
    end_date: str = "",
    projection: Optional[dict] = None,
) -> Optional[float]:
    """
    Compute intrinsic value per share for a single valuation method.

    projection: the per-scenario projection context the core DCF uses --
    ``growth_schedule`` (convergence fade / tech decay), ``wacc_schedule``
    (staged WACC) and ``margin_delta_absolute`` (scenario margin move). Every
    DCF-family leg projects with it, so a weighted "DCF (FCF+)" and the core
    "DCF" are one projection, not two.
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
    peer = get_sector_peer_multiples(sector, is_hk=is_hk, profile_name=profile_name,
                                     ticker=ticker, market_cap=market_cap)
    # Owner-set discount on PEER multiples for one ticker (valuation_constants
    # `ticker_multiple_discounts`), 1.0 for everyone else. Applied where the
    # multiple is applied and recorded as its own part, so the peer median a
    # reader sees is still the basket's and the workbook rebuilds the product.
    try:
        from src.data import valuation_constants as _vc_disc
        _own_disc = _vc_disc.ticker_multiple_discount(ticker)
    except Exception:                                      # noqa: BLE001
        _own_disc = 1.0
    ebitda = most_recent.get("ebitda")
    net_income = most_recent.get("net_income")
    ebit = most_recent.get("ebit")
    bvps = most_recent.get("book_value_per_share")
    total_equity = most_recent.get("total_equity")
    total_assets = most_recent.get("total_assets")
    dividends_ps = most_recent.get("dividends_per_share")
    capex = most_recent.get("capital_expenditure")
    invested_capital = most_recent.get("invested_capital")
    # NOT charged for reinvestment, and that is a live decision rather than an
    # omission — see GATE_GROWTH_REINVESTMENT in `run_dcf_agent`, which computes
    # the deduction and records it with `applied: False`. The blast radius
    # measured on the 14 golden fixtures — each replayed in its OWN subprocess,
    # because a shared process leaks ~ten process-lifetime caches and that leak
    # alone moved BN4.SI's base IV 26% with the charge switched off — was a
    # base-IV move on 9 of 14, over a range of −9.45% to +17.40%, and the sign
    # was INVERTED on two: 09988_HK +17.40% and BABA +15.60%. The charge made
    # the hyper-growth names it exists for dramatically MORE expensive. Passing
    # `sales_to_capital=None` here keeps this projection byte-identical to the
    # shipped baseline while the mechanism stays built and tested.
    #
    # `_sales_to_capital` would have been computed here rather than handed in, so
    # every projection this function dispatches charged on the same ratio —
    # including when the caller is `_run_backward_gate`, which passes its T-1 row
    # as `most_recent` and so would get the T-1 capital base without either side
    # having to remember. That is the wiring to restore when the charge is turned
    # on, and the reason it is written down rather than reinvented.

    # Scenario multipliers for relative value methods
    scenario_mult = {"bear": 0.75, "base": 1.00, "bull": 1.25}
    sm = scenario_mult.get(scenario, 1.0)

    # ── Backlog-coverage DCF ───────────────────────────────────────────────
    # Wins by BRANCH ORDER: these names stay in _DCF_PROJECTION_FAMILY, because
    # that set is what the OE<=0 disable gate knocks out, and a bounded
    # projection must be knocked out by it too. It rewrites years 1-3 of the
    # growth path and hands the rest to `_project_dcf` unchanged -- one
    # projection engine, never a fork. With no accepted backlog it runs the
    # UNBOUNDED projection rather than returning None (that would drop a 0.30
    # anchor on every ticker without an accepted figure); `run_dcf_agent` says
    # so on the valuation. Traced as kind="dcf" with its own projection rows,
    # which the workbook links to the core DCF block when the bound did not
    # bind and gives its own block when it did.
    if method_name in _BACKLOG_BOUNDED_METHODS or method_name in _CONTRACTED_BACKLOG_METHODS:
        _pj = projection or {}
        # Left exactly as the core DCF has it (None = flat growth) unless a bound
        # is applied, so the unbounded leg is byte-identical to the family branch.
        _sched = _pj.get("growth_schedule")
        _bl = most_recent.get("backlog_accepted")
        _bl_d = most_recent.get("_backlog_detail") or {}
        _bound = None
        if _bl and _bl > 0 and revenue_base and revenue_base > 0:
            _cov = float(_bl) / float(revenue_base)
            _b2b = None if method_name in _CONTRACTED_BACKLOG_METHODS else _bl_d.get("book_to_bill")
            _sched, _moved = _bound_growth_schedule(
                list(_sched or [growth_base] * _PROJECTION_YEARS), _backlog_growth_bounds(_cov, _b2b))
            _bound = {"backlog": float(_bl), "backlog_kind": _bl_d.get("backlog_kind"),
                      "coverage_years": round(_cov, 4), "book_to_bill": _b2b,
                      "period": _bl_d.get("period"), "source_url": _bl_d.get("source_url"),
                      "years_bounded": _BACKLOG_BOUND_YEARS, "moved": _moved}
        iv, _pv_fcf, _pv_tv, _rows = _project_dcf(
            revenue_base, fcf_margin_base, growth_base, 0.0,
            wacc, tgr, fcf_floor, net_debt, shares,
            growth_schedule=_sched,
            wacc_schedule=_pj.get("wacc_schedule"),
            margin_delta_absolute=_pj.get("margin_delta_absolute"),
        )
        _leg_trace(kind="dcf", revenue_base=float(revenue_base),
                   fcf_margin_base=float(fcf_margin_base), growth_base=float(growth_base),
                   growth_schedule=_sched,
                   margin_delta_absolute=_pj.get("margin_delta_absolute"),
                   wacc=float(wacc), wacc_schedule=_pj.get("wacc_schedule"),
                   tgr=float(tgr), fcf_floor=float(fcf_floor),
                   net_debt=float(net_debt or 0.0), shares=float(shares),
                   pv_fcf_per_share=float(_pv_fcf), pv_tv_per_share=float(_pv_tv),
                   projection_rows=_rows, **({"backlog_bound": _bound} if _bound else {}))
        return iv

    # ── DCF / DCF variants ─────────────────────────────────────────────────
    # _DCF_PROJECTION_FAMILY (module constant, defined below) — every name
    # here projects via _project_dcf and therefore consumes fcf_margin_base.
    if method_name in _DCF_PROJECTION_FAMILY:
        # With the projection context, not without it. Every DCF-family leg
        # used to project `growth_base` FLAT for ten years with no margin move,
        # while the core "DCF" key applied the profile's fade/decay schedule,
        # staged WACC and scenario margin delta -- so the leg that carried the
        # weight was a different projection from the one labelled "DCF". MELI
        # (Hyper-Growth Platform, 15%/yr decay): DCF (FCF+) $6,672 at 45%
        # weight against its own core DCF of $3,807.
        _pj = projection or {}
        iv, _pv_fcf, _pv_tv, _rows = _project_dcf(
            revenue_base, fcf_margin_base, growth_base, 0.0,
            wacc, tgr, fcf_floor, net_debt, shares,
            growth_schedule=_pj.get("growth_schedule"),
            wacc_schedule=_pj.get("wacc_schedule"),
            margin_delta_absolute=_pj.get("margin_delta_absolute"),
        )
        _leg_trace(kind="dcf", revenue_base=float(revenue_base),
                   fcf_margin_base=float(fcf_margin_base), growth_base=float(growth_base),
                   growth_schedule=_pj.get("growth_schedule"),
                   margin_delta_absolute=_pj.get("margin_delta_absolute"),
                   wacc=float(wacc), wacc_schedule=_pj.get("wacc_schedule"),
                   tgr=float(tgr), fcf_floor=float(fcf_floor),
                   net_debt=float(net_debt or 0.0), shares=float(shares),
                   pv_fcf_per_share=float(_pv_fcf), pv_tv_per_share=float(_pv_tv),
                   projection_rows=_rows)
        return iv

    # ── Depleting Asset DCF (finite life, no terminal value) ───────────────
    # For a mine or a producing field the going concern ENDS with the reserve.
    # The label matters as much as the maths: this is deliberately NOT called
    # "NAV (LoM)", because a life-of-mine NAV ingests proven & probable
    # reserves, recovery rates and a commodity price deck, and none of that
    # telemetry exists here. Calling a corporate DCF "NAV (LoM)" told the
    # reader a reserve model had been run when it had not.
    #
    # SCOPE: the reinvestment charge does not model this path, and would not
    # even if it went live on the family above. The charge assumes "revenue
    # growth requires incremental capital at a constant S/C", and a depleting
    # asset's revenue path is a depletion schedule, not growth — the going
    # concern ends with the reserve, which is why this call already passes
    # `include_terminal=False`. Two consequences make applying it here a
    # category error rather than a conservatism:
    #
    #   * a miner's invested capital is the reserve base, not working capital
    #     supporting incremental sales, so its measured S/C is small and the
    #     deduction large. On FCX's shape — revenue and invested capital of the
    #     same order, so S/C near 1 — a 5% "growth" year would deduct ~5pp
    #     against a `fcf_margin_base` of 0.0539 and drive the margin through
    #     the Resources floor of 0.00, zeroing the leg. Measured while this was
    #     wired live: FCX S/C = 0.952, deduction +12.94pp at g = 14.0%, year-1
    #     margin 0.0%, and terminal growth 0.015 → 0.0 — Gate B fired on a name
    #     whose base IV did not move at all, so the payload would have carried a
    #     gate decision with no valuation story behind it;
    #   * a mine's revenue moves with the commodity price at a roughly FIXED
    #     capital base, so the proportionality the identity rests on does not
    #     hold in the direction that matters.
    #
    # Recorded rather than left implicit because it is a scope decision about
    # which projections the charge models, and it survives the charge's removal:
    # whoever turns the charge on for `_DCF_PROJECTION_FAMILY` must not reach
    # for this call at the same time.
    if method_name == _DEPLETING_DCF:
        iv, _, _, _ = _project_dcf(
            revenue_base, fcf_margin_base, growth_base, 0.0,
            wacc, 0.0, fcf_floor, net_debt, shares,
            years=_DEPLETING_HORIZON_YEARS,
            include_terminal=False,
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
            _leg_trace(kind="capitalised_earnings", metric="EBIT (TTM)", metric_value=float(ebit),
                       scenario_band=sm, tax_rate=_EFFECTIVE_TAX_RATE, nopat=float(nopat),
                       capitalisation_rate=float(wacc))
            return _ev_to_equity_ps(ev, net_debt, most_recent, shares)
        return None

    # ── EV/EBITDA (+ EBITDAR proxy: same logic, EBITDAR ≈ EBITDA+rent) ────
    # Tier 2 Tech: tech sub-type multiples (from _TECH_SUBTYPE_MULTIPLES)
    # override the sector-level peer multiple when profile is a known tech
    # sub-type. This stops AMZN/Hyperscaler from using 22x and NOW/Mature
    # SaaS from using 22x — they're 20x and 30x respectively.
    # SBC extension: tech companies with SBC > 10% of revenue get 10%
    # multiple haircut (SBC is real dilution, not non-cash).
    if method_name in _EV_MULTIPLE_METHODS:
        if _is_tech_subtype(sector, profile_name):
            tech_mults = _tech_subtype_multiples(profile_name)
            base_mult = tech_mults["ev_ebit"] if method_name in _EV_EBIT_METHODS else tech_mults["ev_ebitda"]
        else:
            base_mult = peer.get("ev_ebitda", 12.0)
        mult = base_mult * sm * growth_premium
        # Tier 2 Tech SBC discount on EV multiples
        _sbc_v = most_recent.get("stock_based_compensation")
        if _sbc_v and revenue_base and revenue_base > 0 and is_tech_sector(sector):
            _sbc_pct = abs(_sbc_v) / revenue_base
            if _sbc_pct > 0.10:
                mult *= 0.90   # 10% haircut on EV/EBITDA
        # Change 7: apply Chinese ADR multiple haircut for CNY-reporting US-listed companies
        if reported_currency == "CNY":
            mult *= peer.get("cn_adr_haircut", 1.0)
        mult *= _own_disc
        metric = ebit if method_name in _EV_EBIT_METHODS else ebitda
        if metric and metric > 0 and shares > 0:
            ev = metric * mult
            _leg_trace(kind="ev_multiple",
                       metric="EBIT (TTM)" if method_name in _EV_EBIT_METHODS else "EBITDA (TTM)",
                       metric_value=float(metric), multiple=float(mult),
                       multiple_parts={
                           "peer_multiple": float(base_mult),
                           "peer_source": ("tech sub-type table" if _is_tech_subtype(sector, profile_name)
                                           else "peer median ev_ebitda"),
                           "scenario_band": sm, "growth_premium": growth_premium,
                           "sbc_haircut": (0.90 if (_sbc_v and revenue_base and revenue_base > 0
                                                    and is_tech_sector(sector)
                                                    and abs(_sbc_v) / revenue_base > 0.10) else 1.0),
                           "cn_adr_haircut": (peer.get("cn_adr_haircut", 1.0)
                                              if reported_currency == "CNY" else 1.0),
                           **({"owner_multiple_discount": _own_disc} if _own_disc != 1.0 else {})})
            return _ev_to_equity_ps(ev, net_debt, most_recent, shares)
        return None

    # ── EV/EBITDA (norm) — uses 5-yr cycle-normalized EBITDA ──────────────
    # Same peer multiple, but anchored on Damodaran-normalized EBITDA
    # (mean EBITDA margin × current revenue) so peak/trough years don't
    # distort the multiple application. Critical for cyclicals: mining,
    # merchant power, auto, semis, chemicals.
    # "EV/EBITDA (Norm)" (capital N) is Steel / Metals' 0.50-weight ANCHOR.
    # Its profile entry used to say `"note": "proxied by EV/EBITDA"` — but the
    # profile's own rationale is "Normalised mid-cycle EBITDA smooths
    # commodity price volatility", so the normalised branch is the intent and
    # the note was a stopgap written when no branch matched the spelling at
    # all. Routing it to the plain branch instead would satisfy the coverage
    # test and still hand a steel company its peak-year EBITDA, which is the
    # defect Phase 1.2B exists to remove. The note now says what the code does.
    if method_name in {"EV/EBITDA (norm)", "EV/EBITDA (Norm)",
                       "EV/EBITDA norm", "Normalized EV/EBITDA"}:
        norm_ebitda = most_recent.get("normalized_ebitda")
        if norm_ebitda is None or norm_ebitda <= 0 or shares <= 0:
            return None
        _dyn, _dyn_src = _dynamic_norm_multiple(peer, "ev_ebitda", ticker, "EV/EBITDA (norm)")
        _base_mult = _dyn if _dyn else peer.get("ev_ebitda", 12.0)
        mult = _base_mult * sm * growth_premium
        if reported_currency == "CNY":
            mult *= peer.get("cn_adr_haircut", 1.0)
        ev = norm_ebitda * mult
        _leg_trace(kind="ev_multiple", metric="EBITDA (5y normalised)",
                   metric_value=float(norm_ebitda), multiple=float(mult),
                   multiple_parts={"peer_multiple": float(_base_mult),
                                   "peer_source": _dyn_src or "peer median ev_ebitda",
                                   "scenario_band": sm, "growth_premium": growth_premium,
                                   "cn_adr_haircut": (peer.get("cn_adr_haircut", 1.0)
                                                      if reported_currency == "CNY" else 1.0)})
        return _ev_to_equity_ps(ev, net_debt, most_recent, shares)

    # ── SOTP (Sum of Parts) — per-segment EV/Revenue multiples ────────────
    # Uses FMP product-segment revenue breakdown with keyword-matched multiples
    # per segment type (Services ~6x, Hardware ~2.5x, Retail ~1.5x, etc.).
    # Only fires when the ticker disclosed segments and they landed on
    # ``most_recent["segment_breakdown"]`` (set by run_dcf_agent). Deliberately
    # ignores scenario multiplier (sm) — the scenario signal lives in the
    # segment revenue levels when/if analysts update them, not in an artificial
    # ±25% overlay.
    # ── SOTP / NAV, NAV Discount — conglomerate look-through ─────────────
    # The Holding Company profile anchors SOTP / NAV at 0.70 and marked it
    # implementable: False, so 70% of every conglomerate's valuation was a
    # P/BV proxy. The look-through marks each listed stake at MARKET (shares x
    # price x ownership, never carrying value) and applies one holding-company
    # discount at the total.
    #
    # Returns None unless the look-through is COMPLETE. A SOTP missing a
    # division is not conservative, it is wrong, and as the dominant-weight
    # anchor a silently-short NAV would drag the whole valuation while looking
    # deliberate. Incomplete names keep falling through, and
    # holdco_sotp.decline_reason() names the division that is missing.
    if method_name in {"SOTP / NAV", "NAV Discount", "SOTP / NAV (look-through)"}:
        _lt = None
        if ticker and end_date and shares and shares > 0:
            try:
                from src.agents.analysis import holdco_sotp
                if holdco_sotp.enabled_for(ticker):
                    # Parent-level net debt only. A listed stake marked at
                    # market has already netted that subsidiary's borrowings
                    # inside its market cap, so consolidated net debt would
                    # double-count -- and for a holdco that consolidates a
                    # BANK it is a deposit base, not corporate leverage (CITIC
                    # reports HKD 1,989bn against a HKD 367bn look-through).
                    # Passed only when every division is an equity-accounted
                    # stake, which is when the consolidated figure IS the
                    # parent's.
                    _tpl = holdco_sotp.template_for(ticker) or {}
                    _divs = _tpl.get("divisions") or []
                    _all_assoc = bool(_divs) and all(
                        d.get("basis") == "market_stake"
                        and (d.get("stake_pct") or 1.0) < 0.5 for d in _divs)
                    # A market_stake part is an EQUITY value -- the
                    # subsidiary's own borrowings are already inside its
                    # market cap. Every other basis produces an ENTERPRISE
                    # value, so the debt funding those assets has to come out
                    # or the SOTP counts debt-financed assets as if they were
                    # equity. Olam and SingPost are valued entirely on
                    # enterprise bases.
                    # pe_range / fixed_value / nil are EQUITY values like a
                    # market stake: a template built only from those takes
                    # no consolidated net debt (Keppel, Sembcorp).
                    _any_ev = any(d.get("basis") not in holdco_sotp.EQUITY_BASES for d in _divs)
                    # The engine works in the currency the ticker TRADES in,
                    # so the look-through is converted to that, not to the
                    # reporting currency.
                    from src.tools.api import get_listing_currency
                    _listing_ccy = get_listing_currency(ticker)
                    _needs_debt = _all_assoc or _any_ev
                    # Parent-level only: majority-owned listed stakes are
                    # marked at market with their own debt inside, so their
                    # borrowings come out of the consolidated figure
                    # (holdco_sotp.parent_net_debt). If that cannot be
                    # verified the look-through is declined, not guessed.
                    _nd = (holdco_sotp.parent_net_debt(ticker, end_date, net_debt, _listing_ccy)
                           if _needs_debt else None)
                    if not (_needs_debt and _nd is None):
                        _lt = holdco_sotp.value_per_share(
                            ticker, end_date, shares,
                            to_currency=_listing_ccy,
                            ebitda_by_division=_accepted_division_ebitda(ticker),
                            net_debt=_nd)
            except Exception:                              # noqa: BLE001
                _lt = None
        if _lt is not None and _lt > 0:
            return _lt
        # No complete look-through: fall through to the P/BV proxy below,
        # which is what these names already had. Returning None here would
        # drop the anchor entirely and make a partial fix a regression.

    if method_name in {"SOTP (segments)", "Sum of Parts", "SOTP", "SOTP (Segments)"}:
        seg = most_recent.get("segment_breakdown")
        if not seg or shares <= 0:
            return None
        # Tier-adjust segment multiples: ecosystem leaders (AAPL, MSFT, V/MA,
        # luxury) get "premium" multiples on each segment type, materially
        # uplifting SOTP so it tracks market cap for healthy market-multiple
        # names. Tier is driven by the sector profile (see _PROFILE_TIER_MAP).
        tier = _resolve_segment_tier(sector, profile_name)
        _members = most_recent.get("segment_members") or {}
        _assets = most_recent.get("segment_assets") or {}
        # Reconciled to the same five-year normalised EBITDA the anchor leg
        # uses, so the SOTP and the multiple legs stand on one earnings base.
        _co_ebitda = most_recent.get("normalized_ebitda") or most_recent.get("ebitda")
        _parts = _sotp_parts(seg, tier=tier, members=_members, assets=_assets,
                             company_ebitda=_co_ebitda)
        # A SOTP that prices only part of the company is not a valuation of the
        # company. Phillips 66's segmentation is 61% an eliminations line; below
        # this bar the leg declines rather than publish a fraction as a whole.
        _priced = sum(p["revenue"] for p in _parts if p.get("multiple"))
        _all_rev = sum(p["revenue"] for p in _parts) or 1.0
        if _priced / _all_rev < _SOTP_MIN_PRICED_REVENUE:
            most_recent.setdefault("_sotp_refusals", {})[profile_name or "?"] = {
                "priced_share": _priced / _all_rev, "parts": _parts}
            return None
        total_ev = _sotp_enterprise_value(seg, tier=tier, members=_members,
                                          assets=_assets, company_ebitda=_co_ebitda)
        if total_ev is None:
            return None
        # Apply growth premium to the aggregate, same pattern as EV/Revenue.
        # No CNY haircut here — the per-segment multiples are already generic
        # (not peer-table sourced), so the ADR discount would be speculative.
        # Equity = EV − net_debt; when net_debt < 0 (net cash), this adds the
        # cash pile back — matches the standard SOTP accounting for AAPL etc.
        total_ev *= growth_premium
        _leg_trace(kind="sotp", metric="Sum of segment EVs",
                   metric_value=float(total_ev), shares=float(shares),
                   tier=tier, segments=_parts,
                   priced_share_of_revenue=_priced / _all_rev,
                   growth_premium=growth_premium)
        return _ev_to_equity_ps(total_ev, net_debt, most_recent, shares)

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

    # ── SOTP (published) — the analyst's own segment valuation table ─────
    if method_name in {"SOTP (published)", "Published SOTP"}:
        res = _compute_published_sotp(ticker, shares)
        if res is None:
            return None
        return res[0] * sm

    # ── SOTP (analyst) — GS-style segment P/E + EV/Rev SOTP ──────────────
    # Consumes assumptions assembled by the SOTP extractor (or a hand-built
    # fixture) on most_recent["sotp_assumptions"]. Per segment: higher of
    # P/E-on-NOPAT and EV/Rev; + associates + net cash; − holdco discount.
    # Task #25: promoted into the blend at _SOTP_ANALYST_BLEND_WEIGHT via
    # the profile overlay in run_dcf_agent when assumptions exist; tickers
    # without assumptions never see the method. The base table is computed
    # once and cached on most_recent (the scenario loop and the Tier 1
    # report breakdown both reuse it). Bear/bull prefer the Tier 3.8
    # scenario TPs (per-segment multiple overrides from the extractor's
    # _scenarios block) over the flat base value when available, so the
    # scenario IVs carry the same scenario awareness as the other methods.
    if method_name in _SOTP_ANALYST_METHOD_NAMES:
        assumptions = most_recent.get("sotp_assumptions")
        if not assumptions or shares <= 0:
            return None
        table = most_recent.get("sotp_analyst_table")
        if table is None:
            if most_recent.get("sotp_analyst_degraded") is not None:
                # An earlier scenario pass already found the extraction
                # degraded. The verdict is a function of the assumptions alone,
                # so recomputing per scenario cannot change it.
                return None
            table = _sotp_analyst_style(
                assumptions,
                shares=shares,
                net_debt=net_debt,
                fx_to_reporting=float(
                    assumptions.get("fx_usd_to_reporting")
                    or most_recent.get("_sotp_usd_reporting_fx")
                    or 1.0
                ),
                tier=_resolve_segment_tier(sector, profile_name),
            )
            if table is None:
                return None
            if table.get("degraded_no_segments"):
                # ── THE METHOD DOES NOT PUBLISH, BUT NOTHING IS DISCARDED ──
                # Cached under a DIFFERENT key from `sotp_analyst_table`, and
                # that is load-bearing rather than tidy. `build_sotp_breakdown`
                # and the report layer read `sotp_analyst_table` and format
                # `table['per_share_reporting']:,.2f`, which raises TypeError on
                # the None a degraded table carries. Keeping the degraded table
                # out of that key means the existing consumers are untouched,
                # while `sotp_analyst_degraded` still gives the engine the
                # associates and net cash figures to disclose by name and dollar
                # in the ticker's forward flags. Before this change the value
                # was not merely unpublished, it was unrecoverable.
                most_recent["sotp_analyst_degraded"] = table
                return None
            most_recent["sotp_analyst_table"] = table
        if scenario in ("bear", "bull") and assumptions.get("_scenarios"):
            if "_sotp_analyst_scenario_tps" not in most_recent:
                try:
                    from src.agents.analysis.sotp_report_extras import (
                        sotp_scenario_tps,
                    )
                    most_recent["_sotp_analyst_scenario_tps"] = (
                        sotp_scenario_tps(
                            assumptions,
                            assumptions.get("_scenarios") or {},
                            shares=shares,
                            fx=float(table.get("fx_to_reporting") or 1.0),
                            net_debt=net_debt,
                            tier=_resolve_segment_tier(sector, profile_name),
                        ) or {}
                    )
                except Exception:
                    most_recent["_sotp_analyst_scenario_tps"] = {}
            _scen = (most_recent["_sotp_analyst_scenario_tps"]
                     .get(scenario) or {})
            _scen_ps = _scen.get("per_share_reporting")
            if _scen_ps and _scen_ps > 0:
                _leg_trace(kind="sotp", source="analyst scenario TP",
                           table=_sotp_trace_table(table), per_share=float(_scen_ps))
                return float(_scen_ps)
        # No analyst scenario TP: flex each segment by its revenue tree and
        # the standard multiple band (see _sotp_scenario_from_trees).
        if scenario in ("bear", "bull"):
            _flex = _sotp_scenario_from_trees(
                table, most_recent.get("segment_scenarios"), scenario, sm)
            if _flex is not None:
                most_recent.setdefault("_sotp_analyst_flex", {})[scenario] = _flex
                _leg_trace(kind="sotp", source="segment revenue trees x multiple band",
                           table=_sotp_trace_table(table), flex=_flex["segments"])
                return float(_flex["per_share_reporting"])
        _leg_trace(kind="sotp", source="analyst SOTP (base)", table=_sotp_trace_table(table))
        return table["per_share_reporting"]

    # ── EV/Revenue and variants ────────────────────────────────────────────
    # EV/NTM Revenue: forward-looking — uses analyst consensus revenue for the
    # nearest forward fiscal year (bear=low, base=avg, bull=high from FMP's
    # /stable/analyst-estimates). Fallback to TTM × (1 + analyst 1yr growth)
    # when consensus has <3 analysts or is missing. Per Gemini critique:
    # never fall back to 5-year CAGR for SaaS — deceleration curves are
    # steep and historical CAGR systematically overshoots NTM for growth
    # companies. growth_base passed by the engine already applies analyst-
    # preferred waterfall, so the fallback uses growth_base directly.
    #
    # EV/Revenue (no NTM prefix) keeps legacy TTM behavior for non-growth
    # sectors that don't benefit from forward-looking multiples.
    if method_name in {"EV/NTM Revenue", "EV/NTM Rev", "EV/Fwd Rev"}:
        # Prefer consensus revenue when ≥3 analysts
        fwd_rev = None
        if forward_consensus is not None:
            _rev_dict = forward_consensus.get("revenue") or {}
            _count = forward_consensus.get("analyst_count_revenue")
            if _count and _count >= 3:
                fwd_rev = _rev_dict.get(scenario)
        # Fallback: TTM × (1 + NTM growth rate)
        if fwd_rev is None and revenue_base and revenue_base > 0:
            fwd_rev = revenue_base * (1 + growth_base)
        if fwd_rev is None or fwd_rev <= 0 or shares <= 0:
            return None
        # Forward method — sm NOT applied (scenario already mapped to
        # analyst low/avg/high); growth_premium + SBC haircut still apply.
        # Tech sub-type multiples override when applicable (Tier 2 Tech).
        # Option III Spec 2: growth-phase Tech profiles converge to mature
        # equivalent at terminal (Mature SaaS 10x for Growth SaaS, etc.) with
        # bear/bull ±20% band applied inside the helper.
        base_mult, _ev_rev_basis = _qualified_ev_revenue_multiple(
            sector, profile_name, scenario, peer, most_recent)
        mult = base_mult * growth_premium
        # SBC extension (Tier 2 Tech): tech companies with SBC > 10% of
        # revenue get a multiple haircut because SBC is shareholder
        # dilution disguised as non-cash expense. Resolves the "cheap on
        # EBITDA, expensive on FCF" paradox for SNOW/PLTR/DDOG.
        _sbc_v = most_recent.get("stock_based_compensation")
        if _sbc_v and revenue_base and revenue_base > 0 and is_tech_sector(sector):
            _sbc_pct = abs(_sbc_v) / revenue_base
            if _sbc_pct > 0.10:
                mult *= 0.93   # 7% haircut on EV/Revenue
        if reported_currency == "CNY":
            mult *= peer.get("cn_adr_haircut", 1.0)
        ev = fwd_rev * mult
        _leg_trace(kind="ev_multiple", metric=f"Revenue (NTM, {scenario})",
                   metric_value=float(fwd_rev), multiple=float(mult),
                   multiple_parts={"peer_multiple": float(base_mult),
                                   "peer_source": str(_ev_rev_basis),
                                   "growth_premium": growth_premium,
                                   "sbc_haircut": (0.93 if (_sbc_v and revenue_base and revenue_base > 0
                                                            and is_tech_sector(sector)
                                                            and abs(_sbc_v) / revenue_base > 0.10) else 1.0),
                                   "cn_adr_haircut": (peer.get("cn_adr_haircut", 1.0)
                                                      if reported_currency == "CNY" else 1.0)})
        return _ev_to_equity_ps(ev, net_debt, most_recent, shares)

    # EV/Revenue (trailing TTM) — legacy path for non-growth sectors
    if method_name in {"EV/Revenue"}:
        mult = peer.get("ev_revenue", 4.0) * sm * growth_premium
        if reported_currency == "CNY":
            mult *= peer.get("cn_adr_haircut", 1.0)
        if revenue_base > 0 and shares > 0:
            ev = revenue_base * mult
            _leg_trace(kind="ev_multiple", metric="Revenue (TTM)",
                       metric_value=float(revenue_base), multiple=float(mult),
                       multiple_parts={"peer_multiple": float(peer.get("ev_revenue", 4.0)),
                                       "peer_source": "peer median ev_revenue",
                                       "scenario_band": sm, "growth_premium": growth_premium,
                                       "cn_adr_haircut": (peer.get("cn_adr_haircut", 1.0)
                                                          if reported_currency == "CNY" else 1.0)})
            return _ev_to_equity_ps(ev, net_debt, most_recent, shares)
        return None

    # ── Forward P/S — forward Revenue / shares × peer P/S ─────────────────
    # Similar to EV/NTM Revenue but uses direct P/S multiple (no net_debt
    # subtraction). For early-stage SaaS where EV-net_debt produces noise.
    if method_name in {"Forward P/S", "Fwd P/S", "NTM P/S"}:
        if forward_consensus is None:
            return None
        _rev_dict = forward_consensus.get("revenue") or {}
        fwd_rev = _rev_dict.get(scenario)
        if fwd_rev is None or fwd_rev <= 0 or shares <= 0:
            return None
        # Prefer tech sub-type p_s multiple (explicitly calibrated); fall back
        # to EV/Revenue × 0.90 adjust for sectors without a direct P/S multiple.
        if _is_tech_subtype(sector, profile_name):
            ps_mult = _tech_subtype_multiples(profile_name)["p_s"] * growth_premium
        else:
            ps_mult = peer.get("ev_revenue", 4.0) * 0.90 * growth_premium
        if reported_currency == "CNY":
            ps_mult *= peer.get("cn_adr_haircut", 1.0)
        return (fwd_rev / shares) * ps_mult

    # ── Forward EV/EBIT (consensus EBIT × peer EV/EBIT ≈ peer EV/EBITDA × 1.2) ─
    # Uses analyst consensus EBIT (NEW — FMP exposes ebitLow/Avg/High in the
    # same payload as EPS/Revenue/EBITDA; Tier 1 plumbing already fetched
    # these fields but only EPS + EBITDA were wired). EV/EBIT is cleaner than
    # EV/EBITDA for asset-heavy tech (semis) because it captures D&A burden.
    if method_name in {"Forward EV/EBIT", "Fwd EV/EBIT", "NTM EV/EBIT"}:
        if forward_consensus is None:
            return None
        _ebit_dict = forward_consensus.get("ebit") or {}
        ebit_fwd = _ebit_dict.get(scenario)
        if ebit_fwd is None or ebit_fwd <= 0 or shares <= 0:
            return None
        # Tech sub-type has direct ev_ebit multiple; else use EV/EBITDA × 1.20
        if _is_tech_subtype(sector, profile_name):
            base_mult = _tech_subtype_multiples(profile_name)["ev_ebit"]
        else:
            base_mult = peer.get("ev_ebitda", 12.0) * 1.20
        mult = base_mult * growth_premium
        if reported_currency == "CNY":
            mult *= peer.get("cn_adr_haircut", 1.0)
        ev = ebit_fwd * mult
        _leg_trace(kind="ev_multiple", metric="EBIT (NTM consensus)",
                   metric_value=float(ebit_fwd), multiple=float(mult),
                   multiple_parts={"peer_multiple": float(base_mult),
                                   "growth_premium": growth_premium,
                                   "cn_adr_haircut": (peer.get("cn_adr_haircut", 1.0)
                                                      if reported_currency == "CNY" else 1.0)})
        return _ev_to_equity_ps(ev, net_debt, most_recent, shares)

    # ── P/E (TTM / operating) ─────────────────────────────────────────────
    # Uses trailing-12m net income. "P/E (ops)" and "P/E (Premium)" share
    # this branch — they differ only in documentation intent, not earnings
    # source. For the TRUE cycle-normalized path use "P/E (norm)" below.
    if method_name in {"P/E", "P/E (ops)", "P/E (Premium)", "P/E (Ops)"}:
        mult = peer.get("pe", 18.0) * sm * growth_premium * sbc_pe_discount * _own_disc
        eps = (net_income / shares) if (net_income is not None and shares > 0) else None
        if eps and eps > 0:
            _leg_trace(kind="equity_multiple", metric="Net income (TTM)",
                       metric_value=float(net_income), shares=float(shares),
                       per_share_metric=float(eps), multiple=float(mult),
                       multiple_parts={"peer_multiple": float(peer.get("pe", 18.0)),
                                       "peer_source": "peer median pe", "scenario_band": sm,
                                       "growth_premium": growth_premium,
                                       "sbc_pe_discount": sbc_pe_discount,
                                       **({"owner_multiple_discount": _own_disc} if _own_disc != 1.0 else {})})
            return eps * mult
        return None

    # ── P/E (norm) — uses 5-yr cycle-normalized net income ────────────────
    # For cyclicals the trailing net income reflects one point in the cycle;
    # applying a peer P/E at peak earnings produces trough IV (and vice-versa).
    # Banks: trailing NI is distorted by credit-cycle provisions. When engine-
    # computed normalization isn't available, falls back to
    # through-cycle earning power = BVPS × target_ROE × shares, tethering the
    # P/E method to the capital base × sustainable ROE rather than this
    # quarter's provision-swing NI (per Gemini critique). Uses profile-
    # specific P/E from _BANK_PROFILE_CALIBRATION.
    if method_name in {"P/E (norm)", "P/E norm", "Normalized P/E"}:
        norm_ni = most_recent.get("normalized_net_income")
        _is_bank = (sector == "Financials" and profile_name in _BANK_PROFILE_CALIBRATION) \
                    or "Bank" in (profile_name or "")
        if (norm_ni is None or norm_ni <= 0) and _is_bank:
            # Through-cycle normalized earnings = equity × target_ROE.
            # Immune to credit-cycle provision distortion (low provisions →
            # inflated NI at cycle peak → overvalued bank; high provisions →
            # depressed NI at cycle trough → undervalued bank).
            cfg = _bank_profile_calibration(profile_name)
            eq = most_recent.get("total_equity")
            _research_roe = most_recent.get("_bank_target_roe_research")
            _target_roe = _research_roe if _research_roe else cfg["target_roe"]
            if eq and eq > 0:
                norm_ni = eq * _target_roe
        if norm_ni is None or norm_ni <= 0 or shares <= 0:
            return None
        _dyn_pe, _dyn_pe_src = (None, None)
        if _is_bank:
            # The bank leg prices on its owner-calibrated P/E, not a peer
            # multiple, and is left as it is.
            cfg = _bank_profile_calibration(profile_name)
            mult = cfg["pe"] * sm * growth_premium * sbc_pe_discount
        else:
            _dyn_pe, _dyn_pe_src = _dynamic_norm_multiple(peer, "pe", ticker, "P/E (norm)")
            mult = (_dyn_pe if _dyn_pe else peer.get("pe", 18.0)) * sm * growth_premium * sbc_pe_discount
        eps_norm = norm_ni / shares
        _leg_trace(kind="equity_multiple",
                   metric=("Net income (equity x target ROE, bank)"
                           if (_is_bank and most_recent.get("normalized_net_income") in (None, 0))
                           else "Net income (5y normalised)"),
                   metric_value=float(norm_ni), shares=float(shares),
                   per_share_metric=float(eps_norm), multiple=float(mult),
                   multiple_parts={"peer_multiple": float(_bank_profile_calibration(profile_name)["pe"]
                                                          if _is_bank else (_dyn_pe or peer.get("pe", 18.0))),
                                   "peer_source": ("bank calibration P/E" if _is_bank
                                                   else (_dyn_pe_src or "peer median pe")),
                                   "scenario_band": sm, "growth_premium": growth_premium,
                                   "sbc_pe_discount": sbc_pe_discount})
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
        _fwd_pe, _fwd_pe_src = _forward_peer_multiple(peer, "pe", 18.0)
        mult = _fwd_pe * growth_premium * sbc_pe_discount * _own_disc
        if reported_currency == "CNY":
            mult *= peer.get("cn_adr_haircut", 1.0)
        _leg_trace(kind="equity_multiple", metric=f"EPS (NTM consensus, {scenario})",
                   metric_value=float(eps_fwd), per_share_metric=float(eps_fwd),
                   multiple=float(mult),
                   multiple_parts={"peer_multiple": _fwd_pe,
                                   "peer_source": _fwd_pe_src,
                                   "growth_premium": growth_premium,
                                   "sbc_pe_discount": sbc_pe_discount,
                                   "cn_adr_haircut": (peer.get("cn_adr_haircut", 1.0)
                                                      if reported_currency == "CNY" else 1.0),
                                   **({"owner_multiple_discount": _own_disc} if _own_disc != 1.0 else {})})
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
        _fwd_ev, _fwd_ev_src = _forward_peer_multiple(peer, "ev_ebitda", 12.0)
        mult = _fwd_ev * growth_premium
        if reported_currency == "CNY":
            mult *= peer.get("cn_adr_haircut", 1.0)
        ev = ebitda_fwd * mult
        _leg_trace(kind="ev_multiple", metric=f"EBITDA (NTM consensus, {scenario})",
                   metric_value=float(ebitda_fwd), multiple=float(mult),
                   multiple_parts={"peer_multiple": _fwd_ev,
                                   "peer_source": _fwd_ev_src,
                                   "growth_premium": growth_premium,
                                   "cn_adr_haircut": (peer.get("cn_adr_haircut", 1.0)
                                                      if reported_currency == "CNY" else 1.0)})
        return _ev_to_equity_ps(ev, net_debt, most_recent, shares)

    # ── P/BV ──────────────────────────────────────────────────────────────
    # ── Embedded Value (Insurance — Life sub-sub-profile) ────────────────
    # The INDUSTRY_VALUATION_PROFILES["Financials"]["Insurance"] entry lists
    # Embedded Value as the anchor with weight=0.50 but implementable=False
    # (proxies to P/BV in the legacy path). PR #1 makes it implementable
    # using the framework-extracted vnb_margin and embedded_value_per_share
    # KPIs sourced via _extract_insurance_metrics → SECTOR_KPI_FRAMEWORK.
    #
    # Method preference for Life insurers:
    #   1. Disclosed embedded_value_per_share (cleanest — direct IR figure)
    #   2. P/BV × VNB-margin uplift  (uses extracted vnb_margin to size premium)
    #   3. Fall back to None → blend redistributes weight to remaining methods
    if method_name in {"Embedded Value", "EV", "EV per Share"}:
        ev_ps = most_recent.get("embedded_value_per_share")
        if ev_ps and ev_ps > 0:
            # Apply scenario multiplier (bear/base/bull symmetric ±10%)
            return ev_ps * sm
        # VNB-margin uplift on P/BV (proxy when EV/share not disclosed)
        vnb = most_recent.get("vnb_margin")
        if vnb and vnb > 0 and bvps and bvps > 0:
            # VNB margin > 0.20 = high-quality Life writer → P/BV × 1.5
            # VNB margin 0.10-0.20 = average → P/BV × 1.2
            # VNB margin < 0.10 = weak → P/BV × 0.9
            if vnb >= 0.20:
                vnb_mult = 1.5
            elif vnb >= 0.10:
                vnb_mult = 1.2
            else:
                vnb_mult = 0.9
            return bvps * peer.get("pb", 1.5) * vnb_mult * sm
        return None

    # ── Combined Ratio Gate (Insurance — P&C sub-sub-profile) ────────────
    # P&C insurer profitability is governed by combined ratio: <1.00 means
    # underwriting profit, >1.00 means underwriting loss subsidised by
    # investment income. The Combined Ratio Gate is a P/BV multiplier:
    #   CR ≤ 0.92 → 1.30× P/BV (best-in-class underwriting)
    #   CR 0.93-0.97 → 1.10× P/BV (solid underwriter)
    #   CR 0.98-1.02 → 1.00× P/BV (at break-even)
    #   CR 1.03-1.06 → 0.85× P/BV (mild underwriting loss)
    #   CR > 1.06   → 0.70× P/BV (sustained underwriting loss)
    #
    # Adjustment-stack-aware: combines with SCR sanity haircut
    # (SCR < 1.5 → additional 0.95× discount for thin capital).
    if method_name in {"Combined Ratio Gate", "CR Gate"}:
        cr = most_recent.get("combined_ratio")
        if cr is None or not (bvps and bvps > 0):
            return None
        if cr <= 0.92:
            cr_mult = 1.30
        elif cr <= 0.97:
            cr_mult = 1.10
        elif cr <= 1.02:
            cr_mult = 1.00
        elif cr <= 1.06:
            cr_mult = 0.85
        else:
            cr_mult = 0.70
        # SCR adequacy haircut (applies to all insurers, P&C and Life)
        scr = most_recent.get("solvency_ratio_scr")
        if scr is not None and scr < 1.5:
            cr_mult *= 0.95
        return bvps * peer.get("pb", 1.5) * cr_mult * sm * growth_premium

    # ── NAV Discount — live-mNAV anchor (BTC Treasury / Proxy) ─────────────
    # Treasury-company value IS book × mNAV. When the framework extractor
    # supplies a live mNAV_multiple (attached to most_recent via
    # attach_overrides), anchor on it directly: the static peer P/B premium
    # (~3.0) was calibrated for the pre-2025 premium era and — with BTC now
    # carried at fair value on the balance sheet (ASU 2023-08) — manufactures
    # an IV several times the market (MSTR 2026-08: $527 vs ~$93 spot).
    # Scenario spread comes from sm (same pattern as Embedded Value);
    # growth_premium is NOT re-applied — the market-observed mNAV already
    # prices growth expectations, re-multiplying would double-count.
    # Missing or grossly out-of-band mNAV falls through to the generic
    # peer-P/B path below.
    if method_name == "NAV Discount":
        try:
            _mnav = float(most_recent.get("mNAV_multiple"))
        except (TypeError, ValueError):
            _mnav = None
        if _mnav and 0.1 <= _mnav <= 8.0:
            mult = _mnav * sm
            if bvps and bvps > 0:
                return bvps * mult
            # fallback basis: total_equity / shares (mirrors the generic path)
            if total_equity and total_equity > 0 and shares > 0:
                return (total_equity / shares) * mult
        # no live mNAV → fall through to the peer-P/B path below

    # ── P/Rate Base — the regulator's own arithmetic (regulated utilities) ──
    # A regulator authorises a return on the EQUITY layer of the rate base, so
    # the justified multiple on that layer is the Gordon form the bank GGM leg
    # uses: (allowed ROE - g) / (CoE - g). Until Wave 2 this name sat in the
    # P/BV set below and priced book value x peer P/B under a rate-base label.
    #
    #   equity rate base = rate base x authorised equity ratio
    #   equity value     = equity rate base x justified multiple
    #                      + max(book equity - equity rate base, 0) x peer P/B
    #   value per share  = equity value / shares x scenario band
    #
    # The second term is the part of the company no regulator sets a return on.
    # NextEra's accepted rate base is 0.44x of its net plant: the rest is NextEra
    # Energy Resources, and pricing the whole company off Florida Power & Light's
    # rate base alone would value it at half. That remainder keeps the basis the
    # proxy had -- book at peer P/B -- so the leg is exact where the regulator
    # speaks and unchanged where it does not. It is expressed as ONE multiple on
    # book equity, which is the shape the workbook rebuilds.
    #
    # No net-debt bridge: the equity layer is already the equity-funded slice,
    # and subtracting net debt would deduct the debt layer twice. No growth
    # premium: a PEG premium on a regulator-capped return is incoherent (the
    # live-mNAV branch above makes the same argument).
    #
    # Three inputs, none of them guessed. The rate base and the allowed ROE
    # are owner-accepted filing figures; the cost of equity is an owner-set
    # profile constant. Missing any one, the leg returns None and the P/BV
    # proxy the profile declares prices the weight instead -- requested
    # alongside via _PER_TICKER_METHODS, so no weight is ever lost.
    if method_name == "P/Rate Base":
        from src.data import valuation_constants as _vc
        _rb = most_recent.get("rate_base_accepted")
        _rb_d = most_recent.get("_rate_base_detail") or {}
        _roe = _rb_d.get("allowed_roe")
        _coe = _vc.cost_of_equity(profile_name, _vc.market_key(ticker))
        if not (_rb and _rb > 0 and _roe and _coe and shares > 0
                and total_equity and total_equity > 0):
            return None
        _g = min(float(tgr or 0.0), _coe - 0.01)
        if _roe <= _g:
            return None
        _eq_ratio, _eq_src = _rb_d.get("equity_ratio"), "authorised equity ratio (rate order)"
        if not _eq_ratio:
            _debt = most_recent.get("total_debt")
            if not (total_equity and total_equity > 0 and _debt is not None and _debt >= 0):
                return None
            _eq_ratio = total_equity / (total_equity + _debt)
            _eq_src = "book equity / (book equity + debt): no authorised ratio stated"
        _just = (_roe - _g) / (_coe - _g)
        _eq_rb = _rb * _eq_ratio
        _rest = max(float(total_equity) - _eq_rb, 0.0)
        _pb = float(peer.get("pb", 2.0)) * _own_disc
        _on_book = (_eq_rb * _just + _rest * _pb) / float(total_equity)
        mult = _on_book * sm
        # multiple_parts holds ONLY what multiplies: the workbook rebuilds the leg
        # as peer_multiple x band x every other numeric part. The inputs behind
        # the multiple go beside it, not inside it.
        _leg_trace(kind="equity_multiple", rate_base_audit=_rb_d,
                   rate_base_inputs={"rate_base": float(_rb), "equity_ratio": float(_eq_ratio),
                                     "equity_ratio_source": _eq_src, "equity_rate_base": float(_eq_rb),
                                     "allowed_roe": float(_roe), "cost_of_equity": float(_coe),
                                     "g": float(_g), "justified_multiple": float(_just),
                                     "unregulated_book_equity": float(_rest), "peer_pb_on_remainder": _pb},
                   metric="Book equity (regulated layer at the justified multiple, remainder at peer P/B)",
                   metric_value=float(total_equity), shares=float(shares),
                   per_share_metric=float(total_equity / shares), multiple=float(mult),
                   multiple_parts={"peer_multiple": float(_on_book), "scenario_band": sm,
                                   "peer_source": (f"[equity rate base x (allowed ROE - g)/(CoE - g) + "
                                                   f"remaining book x peer P/B] / book equity; "
                                                   f"CoE owner-set ({profile_name})")})
        return (float(total_equity) / shares) * mult

    if method_name in {"P/BV", "NAV Discount", "SOTP / NAV",
                       "NAV (Project)", "Pipeline NAV"}:
        mult = peer.get("pb", 2.0) * sm * growth_premium * _own_disc
        _pb_parts = {"peer_multiple": float(peer.get("pb", 2.0)), "peer_source": "peer median pb",
                     "scenario_band": sm, "growth_premium": growth_premium,
                     **({"owner_multiple_discount": _own_disc} if _own_disc != 1.0 else {})}
        if bvps and bvps > 0:
            _leg_trace(kind="equity_multiple", metric="Book value per share",
                       per_share_metric=float(bvps), multiple=float(mult),
                       multiple_parts=_pb_parts)
            return bvps * mult
        # fallback: total_equity / shares
        if total_equity and total_equity > 0 and shares > 0:
            _leg_trace(kind="equity_multiple", metric="Book value (total equity)",
                       metric_value=float(total_equity), shares=float(shares),
                       per_share_metric=float(total_equity / shares), multiple=float(mult),
                       multiple_parts=_pb_parts)
            return (total_equity / shares) * mult
        return None

    # ── FCF Yield ─────────────────────────────────────────────────────────
    if method_name in {"FCF Yield", "P/CF", "Price/CF"}:
        target_yield = peer.get("fcf_yield", 0.05) / (sm * growth_premium)  # higher growth → lower yield req → higher price
        # Owner decision 5 (2026-09-17). This line used to read
        # `target_yield = max(target_yield, 0.01)`, which turned an invalid
        # benchmark into a 100x capitalisation rather than refusing it. An FCF
        # yield is a DENOMINATOR — value = FCF / yield — so a peer median at or
        # below zero produces a negative value, or an enormous one as the
        # divisor approaches zero, and the `max()` published the enormous one
        # at exactly 100x FCF with nothing on the card to say the input had
        # been thrown away. SCHW's US peer median measured -0.003152 in BOTH
        # cohorts, a 3.2x error manufactured inside a silent clamp.
        #
        # The floor was also invisible to every test, which is worth recording
        # because it explains how it survived: measured across all 12 FCF-leg
        # calls the golden fixtures make (AAPL 0.0254–0.0467, COST
        # 0.0147–0.0267, V 0.0210–0.0400) it never bound once, and SCHW makes
        # no FCF-leg call at all. The defect lived entirely in production
        # cohorts the baseline does not reach.
        #
        # Returning None drops the leg and the blend renormalises over the
        # survivors — the same answer decision 2b gives a non-positive tangible
        # book, because unavailable is not invented. The threshold is shared
        # with the comps band that filters peer readings before their median is
        # taken, so the two readers of one concept cannot drift apart.
        #
        # The 0.05 default in the `.get()` above deliberately survives this
        # check: it is a missing-key fallback, not a reading, and a peer table
        # that does not disclose an FCF yield should still value the leg rather
        # than silently lose it. That is a different situation from a peer
        # table that HAS one and the one it has is not a benchmark.
        #
        # This import is LAZY and the laziness is load-bearing, not stylistic —
        # hoisting it to module scope breaks golden replay. `regional_comps`
        # binds `_fmp_get` by value (`from src.tools.api import _fmp_get`), and
        # the replay `Replayer` patches that name ON `src.tools.api`, so which
        # function `regional_comps` holds depends on when it is first imported:
        # inside the patch window it gets the recorder, outside it gets the real
        # one. `replay_fixture` imports THIS module outside its
        # `with Replayer(...)` block, so a module-level import here loads
        # regional_comps too early and its `/stable/profile` fetches escape the
        # recording. Measured: hoisting it failed 14 of 17 golden tests with
        # "1 recorded call(s) never used — the fixture is stale", a message
        # that misdiagnoses the cause and would have prompted a regeneration
        # baking in a replay that reaches for the network. `sector_profiles`
        # imports regional_comps from inside functions and this follows suit.
        from src.data.regional_comps import MIN_VALID_FCF_YIELD
        if target_yield <= MIN_VALID_FCF_YIELD:
            return None
        # Prefer SBC-adjusted (owner-earnings) FCF; falls back to reported FCF
        # when SBC isn't disclosed (fcf_owner_earnings is seeded to reported
        # FCF in _extract_annual_series when SBC is missing).
        fcf = most_recent.get("fcf_owner_earnings") or most_recent.get("free_cash_flow")
        _fcf_label = ("FCF, owner earnings (TTM)"
                      if most_recent.get("fcf_owner_earnings") else "FCF (TTM)")
        # On a cyclical the same normalisation the EV/EBITDA and P/E legs use --
        # mean margin on revenue over five years, IQR-trimmed -- so the whole
        # blend stands on one basis. Anywhere else the TTM figure is the right
        # one and this is a no-op.
        if profile_name in _CYCLICAL_PROFILES:
            _norm = most_recent.get("normalized_fcf_owner_earnings")
            if _norm and _norm > 0:
                fcf = _norm
                _fcf_label = ("FCF, owner earnings (5y normalised)"
                              if most_recent.get("fcf_owner_earnings") else "FCF (5y normalised)")
        if fcf and fcf > 0 and shares > 0:
            _leg_trace(kind="yield", metric=_fcf_label,
                       metric_value=float(fcf), shares=float(shares),
                       per_share_metric=float(fcf / shares), target_yield=float(target_yield),
                       multiple=float(1.0 / target_yield),
                       multiple_parts={"peer_fcf_yield": float(peer.get("fcf_yield", 0.05)),
                                       "scenario_band": sm, "growth_premium": growth_premium})
            return (fcf / shares) / target_yield
        return None

    # ── EV / operating cash flow (Wave 1 oil & gas, owner-approved 2026-09-20) ──
    # The cash-flow multiple producers are priced on: EV/DACF's reportable
    # form (DACF adds back after-tax interest; OCF is what FMP reports for the
    # target and its peers alike, so both sides of the ratio are the same
    # measure). Returns None -- the leg drops and the blend renormalises --
    # when the peer set carries no EV/OCF reading: a multiple is not invented.
    if method_name in {"EV/OCF", "EV/Operating CF", "EV/DACF"}:
        ocf = most_recent.get("operating_cash_flow")
        peer_ev_ocf = peer.get("ev_ocf")
        if not ocf or ocf <= 0 or not peer_ev_ocf or peer_ev_ocf <= 0 or shares <= 0:
            return None
        mult = peer_ev_ocf * sm * growth_premium
        if reported_currency == "CNY":
            mult *= peer.get("cn_adr_haircut", 1.0)
        ev = ocf * mult
        _leg_trace(kind="ev_multiple", metric="Operating cash flow (latest FY)",
                   metric_value=float(ocf), multiple=float(mult),
                   multiple_parts={"peer_multiple": float(peer_ev_ocf),
                                   "peer_source": "peer median ev_ocf",
                                   "scenario_band": sm, "growth_premium": growth_premium,
                                   "cn_adr_haircut": (peer.get("cn_adr_haircut", 1.0)
                                                      if reported_currency == "CNY" else 1.0)})
        return _ev_to_equity_ps(ev, net_debt, most_recent, shares)

    # ── Distributable cash flow yield (midstream) ────────────────────────────
    # Distributable CF = OCF - maintenance capex. FMP does not report
    # maintenance capex, so D&A stands in (the asset base's own wear) and the
    # trace says so; a reported, accepted figure replaces it when available.
    # Required yield = the peer FCF yield under the scenario band, the same
    # rule as the FCF Yield leg, and the same refusal below a valid benchmark.
    if method_name in {"Distributable CF Yield", "DCF Yield (distributable)"}:
        from src.data.regional_comps import MIN_VALID_FCF_YIELD
        ocf = most_recent.get("operating_cash_flow")
        maint = most_recent.get("maintenance_capex_accepted")
        _maint_d = most_recent.get("_maintenance_capex_detail") or {}
        maint_src = ("accepted maintenance capex"
                     + (f", incl. forward overlay {_maint_d.get('delta_pct', 0):+.1%}"
                        if _maint_d.get("overlay_applied") else ""))
        if maint is None:
            maint = most_recent.get("depreciation_and_amortization")
            maint_src = "D&A (maintenance capex not reported)"
        if not ocf or maint is None or shares <= 0:
            return None
        dcf_amt = ocf - abs(maint)
        # The benchmark must be on the numerator's basis. Distributable cash
        # flow is net of MAINTENANCE capex; the peer FCF yield is net of TOTAL
        # capex, so capitalising one at the other overstates every name whose
        # growth capex is large -- which is the whole midstream sector. The
        # mismatch hid while `maint` fell back to D&A, because OCF - D&A is
        # roughly free cash flow. Energy Transfer's audited $1.32bn maintenance
        # capex against $5.68bn of D&A ended that: the leg went to $49.04 on a
        # $21.14 quote, capitalising a 12.1% market yield at 4.93%.
        #
        # So the yield is an owner-set profile constant, grounded in the
        # Alerian index distribution yield times sector coverage and reviewed
        # on a quarterly clock (src/data/valuation_constants.py). Where no
        # constant is authored the leg declines to price rather than reach for
        # a number from another basis.
        from src.data import valuation_constants as _vc
        _yield_src = f"owner-set target distributable-CF yield ({profile_name})"
        _base_yield = _vc.env_override(profile_name or "") or _vc.target_dcf_yield(profile_name)
        if not _base_yield:
            return None
        target_yield = _base_yield / (sm * growth_premium)
        if dcf_amt <= 0 or target_yield <= MIN_VALID_FCF_YIELD:
            return None
        _vc_detail = _vc.detail(profile_name) or {}
        _leg_trace(kind="yield", maintenance_capex_audit=(_maint_d or None),
                   metric=f"Distributable CF: OCF - {maint_src}",
                   metric_value=float(dcf_amt), shares=float(shares),
                   per_share_metric=float(dcf_amt / shares), target_yield=float(target_yield),
                   multiple=float(1.0 / target_yield),
                   multiple_parts={"target_dcf_yield": float(_base_yield),
                                   "yield_source": _yield_src,
                                   "benchmark": _vc_detail.get("benchmark"),
                                   "last_reviewed": _vc_detail.get("last_reviewed"),
                                   "review_due": _vc_detail.get("review_due"),
                                   "scenario_band": sm, "growth_premium": growth_premium})
        return (dcf_amt / shares) / target_yield

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
            return _ev_to_equity_ps(ev, net_debt, most_recent, shares)
        return None

    # ── DDM (Gordon Growth) ───────────────────────────────────────────────
    # For REIT profiles the dividend is AFFO-gated — REITs sometimes
    # distribute >100% of AFFO by drawing on revolvers during occupancy
    # dips, which accounting DPS captures but is unsustainable. Capping
    # div at AFFO/share catches those "yield traps" and values only the
    # cash-coverable portion of the distribution.
    if method_name == "DDM":
        div = dividends_ps
        if div and div > 0 and wacc > tgr:
            # AFFO-gate for REITs (sector=RealEstate/REIT or profile matches)
            if sector in {"RealEstate", "REIT"} or "REIT" in (profile_name or ""):
                # Prefer research-sourced AFFO/share when available (parsed
                # from the REIT's distribution statement / supplementals by
                # _extract_reit_metrics). Falls back to line-item-derived
                # AFFO when research doesn't disclose per-unit figures.
                affo_ps_research = most_recent.get("affo_per_share_research")
                if affo_ps_research and affo_ps_research > 0:
                    affo_ps = affo_ps_research
                else:
                    reit_subtype = most_recent.get("_reit_subtype") or _classify_reit_subtype(
                        most_recent.get("_ticker", ""),
                        most_recent.get("_lookup_notes", ""),
                    )
                    _reit = _compute_reit_metrics(most_recent, subtype=reit_subtype)
                    affo = _reit.get("affo")
                    affo_ps = (affo / shares) if (affo and affo > 0 and shares > 0) else None
                if affo_ps and affo_ps > 0 and div > affo_ps:
                    div = affo_ps
            d_next = div * (1 + tgr)
            return d_next / (wacc - tgr)
        return None

    # ── NAV (Cap Rates) — REIT asset-backed valuation ─────────────────────
    # NAV = NOI / cap_rate − total_debt + cash
    # Scenario-INVARIANT: NAV is anchored to property value, which doesn't
    # scale bear/base/bull the way growth-driven methods do. Only cap rate
    # and occupancy move across scenarios, and those are embedded in the
    # method's peer cap_rate lookup (sub-type-specific).
    #
    # KNOWN LIMITATION: total_debt from FMP does not include operating
    # lease liabilities under ASC 842 / IFRS 16. For healthcare REITs and
    # some retail REITs with significant ground leases this understates
    # net liability side of the bridge. Most equity REITs own property
    # fee-simple so this isn't material.
    if method_name in {"NAV (Cap Rates)", "NAV"}:
        reit_subtype = most_recent.get("_reit_subtype") or _classify_reit_subtype(
            most_recent.get("_ticker", ""), most_recent.get("_lookup_notes", "")
        )
        mults = _REIT_SUBTYPE_MULTIPLES.get(reit_subtype, _REIT_SUBTYPE_MULTIPLES["default"])
        cap_rate = most_recent.get("cap_rate_market") or mults["cap_rate"]

        _reit = _compute_reit_metrics(most_recent, subtype=reit_subtype)
        noi = _reit.get("noi")
        if noi is None or noi <= 0 or cap_rate <= 0 or shares <= 0:
            return None

        total_debt = most_recent.get("total_debt") or 0.0
        cash = most_recent.get("cash_and_equivalents") or 0.0
        gross_asset_value = noi / cap_rate
        nav = gross_asset_value - total_debt + cash
        return max(nav / shares, 0.0)

    # ── P/FFO — REIT cash-earnings multiple ────────────────────────────────
    # FFO (Funds From Operations) adds back real-estate depreciation, which
    # is non-cash for REITs. REITs trade on P/FFO, not P/E, because D&A
    # dominates GAAP earnings and distorts the P/E multiple.
    if method_name in {"P/FFO"}:
        reit_subtype = most_recent.get("_reit_subtype") or _classify_reit_subtype(
            most_recent.get("_ticker", ""), most_recent.get("_lookup_notes", "")
        )
        mults = _REIT_SUBTYPE_MULTIPLES.get(reit_subtype, _REIT_SUBTYPE_MULTIPLES["default"])
        mult = mults["p_ffo"] * sm * growth_premium

        _reit = _compute_reit_metrics(most_recent, subtype=reit_subtype)
        ffo = _reit.get("ffo")
        if ffo and ffo > 0 and shares > 0:
            return (ffo / shares) * mult
        return None

    # ── P/AFFO — REIT sustainable-cash multiple ────────────────────────────
    # AFFO strips maintenance capex from FFO; it's the closest proxy to
    # distributable cash and typically gets a slight premium multiple over
    # P/FFO (cleaner quality of earnings).
    if method_name in {"P/AFFO"}:
        reit_subtype = most_recent.get("_reit_subtype") or _classify_reit_subtype(
            most_recent.get("_ticker", ""), most_recent.get("_lookup_notes", "")
        )
        mults = _REIT_SUBTYPE_MULTIPLES.get(reit_subtype, _REIT_SUBTYPE_MULTIPLES["default"])
        mult = mults["p_affo"] * sm * growth_premium

        _reit = _compute_reit_metrics(most_recent, subtype=reit_subtype)
        affo = _reit.get("affo")
        if affo and affo > 0 and shares > 0:
            return (affo / shares) * mult
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
                return _ev_to_equity_ps(entry_ev, net_debt, most_recent, shares)
        return None

    # ── Residual Income (2-stage institutional model) ─────────────────────
    # Replaces the prior primitive single-period formula. Full Damodaran
    # template: ROE fades linearly from current level to profile target
    # over 5-10 years, BVPS compounds at retention × ROE, terminal RI = 0
    # (ROE reverts to CoE in perpetuity). research_target_roe overrides
    # profile default when deep research provides management guidance.
    if method_name == "Residual Income":
        # Bank path — profile-aware 2-stage model with CoE override
        if (is_bank_sector(sector) and profile_name in _BANK_PROFILE_CALIBRATION) \
                or "Bank" in (profile_name or "") or profile_name == "Mortgage/GSE":
            research_target_roe = most_recent.get("_bank_target_roe_research")
            iv = _compute_residual_income_2stage(
                most_recent, shares=shares, profile_name=profile_name,
                research_target_roe=research_target_roe,
            )
            return iv
        # Non-bank path — keep legacy simple spread (for utility-company
        # RI proxy usage and any other profile that invokes RI generically)
        roe = (net_income / total_equity) if (net_income and total_equity and total_equity > 0) else None
        if roe is not None and bvps is not None and bvps > 0 and wacc > 0:
            excess_return = (roe - wacc) * bvps
            ri_premium = (excess_return / wacc) * 0.5 * sm
            return max(bvps + ri_premium, bvps * 0.5)
        return None

    # ── P/TBV — Price-to-Tangible-Book-Value (bank-specific) ──────────────
    # Standard bank multiple: TBV = Equity − Goodwill − Intangibles. Strips
    # M&A-related intangibles that aren't regulatory capital. Preferred over
    # P/B for banks with significant acquisition history (BAC, C, HSBC).
    if method_name in {"P/TBV", "Price/TBV", "P/Tangible BV"}:
        cfg = _bank_profile_calibration(profile_name)
        bank_m = _compute_bank_metrics(most_recent, profile_name)
        tbv_ps = bank_m.get("tbv_per_share")
        if tbv_ps is None or tbv_ps <= 0 or shares <= 0:
            return None
        # NOTE: `growth_premium` is deliberately NOT applied here. It is a
        # revenue-growth-derived multiplier, and a bank's book multiple is
        # set by ROE vs CoE, not by top-line growth. Worse, the premium is
        # computed off a revenue base that for banks is gross interest
        # income (see _bank_total_income), so a bank whose consensus total
        # income was measured against that inflated base collapsed to the
        # 0.60 floor — D05.SI priced P/TBV at 1.4 x 0.60 = 0.84x tangible
        # book, i.e. S$18.53 against a S$22.06 TBV/share, valuing DBS below
        # liquidation. Scenario dispersion still flows through `sm`.
        mult = cfg["p_tbv"] * sm
        _leg_trace(kind="equity_multiple", metric="Tangible book value per share",
                   per_share_metric=float(tbv_ps), multiple=float(mult),
                   multiple_parts={"peer_multiple": float(cfg["p_tbv"]),
                                   "peer_source": "bank calibration P/TBV", "scenario_band": sm})
        return tbv_ps * mult

    # ── DDM (S-REIT) — distributions discounted, Singapore convention ─────
    # value/unit = DPU_fwd x (1 + g) / (CoE - g). An S-REIT distributes
    # >=90% of taxable income to hold its tax transparency, so DPU is
    # effectively the whole equity cash flow and every Singapore broker
    # note prices off this. FFO/AFFO are US GAAP constructs S-REITs do not
    # report.
    if method_name in {"DDM (S-REIT)", "S-REIT DDM"}:
        _sub = most_recent.get("_sreit_subtype") or _sgx_reit_subtype(ticker)
        _res = _compute_sreit_ddm(ticker, _sub, most_recent, shares,
                                  dpu_growth=growth_base)
        if _res is None:
            return None
        return _res[0] * sm

    # ── GGM (P/B) — Gordon Growth justified book multiple ─────────────────
    # target P/B = (ROE - g) / (CoE - g), applied to book value per share.
    # The primary published method for Asian bank coverage.
    if method_name in {"GGM (P/B)", "GGM", "Gordon Growth"}:
        ggm = _compute_ggm_pb(ticker, profile_name, most_recent, shares)
        if ggm is None:
            return None
        value_ps, _target_pb, _a = ggm
        _leg_trace(kind="ggm", target_pb=_target_pb, assumptions=_a,
                   value_before_band=float(value_ps), scenario_band=sm)
        return value_ps * sm

    # ── Excess Capital — CET1 overlay (bank-specific) ─────────────────────
    # Quantifies the capital-adequacy delta. Positive when CET1 > target
    # (excess distributable via buybacks/dividends — boosts IV). Negative
    # when CET1 < target (must retain — discount IV). Asymmetric haircut
    # reflects regulator approval asymmetry.
    #
    # Returns the capital DELTA per share, not a full IV — the blend engine
    # adds this to weighted IV via its small (5%) weight. Because it's a
    # delta not a level, this method can return negative values; the blend
    # code already handles `value is None or value <= 0` so we cap
    # downside at 0 (negative capital deficit is surfaced in audit only).
    if method_name in {"Excess Capital", "CET1 Capital"}:
        research_cet1 = most_recent.get("_bank_cet1_research")
        delta_ps = _compute_excess_capital(
            most_recent, shares=shares, profile_name=profile_name,
            research_cet1=research_cet1,
        )
        if delta_ps is None:
            return None
        # Method blend expects a positive IV value. For negative (deficit)
        # cases, we route the full signal via an audit flag instead and
        # return the TBV floor so the 5% weight doesn't go to zero.
        if delta_ps <= 0:
            bank_m = _compute_bank_metrics(most_recent, profile_name)
            return bank_m.get("tbv_per_share") or (bvps or 0.0)
        # Excess capital: surface as a valuation line item = TBV + excess_ps
        bank_m = _compute_bank_metrics(most_recent, profile_name)
        tbv_ps = bank_m.get("tbv_per_share") or (bvps or 0.0)
        return tbv_ps + delta_ps

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

    # ── P/B-ROE (mid-cycle) — Phase 1.2B peak routing ────────────────────
    # The justified P/B for a cyclical at a cycle top is the one its MID-CYCLE
    # ROE supports, not the one its peak ROE does. This leg stands in for the
    # forward legs the peak trigger suppresses: a forward P/E capitalises a
    # consensus EPS that has not occurred in any year of the available history
    # and, on a cyclical, will not hold. Gordon form, P/B = (ROE - g)/(CoE - g).
    #
    # CoE is the run's WACC. `_compute_ggm_pb` is not reusable here: it resolves
    # CoE and g from `_bank_profile_calibration`, `_BANK_GGM_OVERRIDES` and the
    # broker tables, none of which has an entry for a cyclical profile, so it
    # would fall through to a bank default. WACC-as-CoE is the engine's own
    # precedent — "ROE vs CoE" immediately above does exactly this — and it is
    # the conservative direction for a cyclical, whose equity beta sits above
    # the blended WACC it is charged, so the spread is understated and the
    # justified P/B overstated. The gate records the substitution in its basis
    # rather than leaving it implicit.
    if method_name == "P/B-ROE (mid-cycle)":
        norm_ni = most_recent.get("normalized_net_income")
        if norm_ni is None or not total_equity or total_equity <= 0 or shares <= 0:
            return None
        _bv = bvps if bvps and bvps > 0 else total_equity / shares
        if not _bv or _bv <= 0:
            return None
        roe_norm = norm_ni / total_equity
        g = tgr
        spread = wacc - g
        _bound: list[str] = []
        if spread < _PB_ROE_MIN_SPREAD:
            # Reachable in practice: the projection loop already forces
            # tgr = wacc - 0.005 when wacc <= tgr, so a 0.5% spread arrives here
            # and dividing by it publishes a multiple with no meaning.
            _bound.append(f"CoE-g {spread:.4f} clamped to the "
                          f"{_PB_ROE_MIN_SPREAD:.3f} floor")
            spread = _PB_ROE_MIN_SPREAD
        pb = (roe_norm - g) / spread
        if pb < _PB_ROE_MIN_PB:
            _bound.append(f"justified P/B {pb:.3f} clamped to the "
                          f"{_PB_ROE_MIN_PB:.2f} floor")
            pb = _PB_ROE_MIN_PB
        pb *= sm
        if _bound:
            # Same channel the SOTP branches use to hand state back to the
            # caller; `most_recent` is the live dict, not a copy.
            most_recent.setdefault("_pb_roe_floors", []).extend(_bound)
        return _bv * pb

    # ── ROIC vs WACC (also matches bare "ROIC" from Consumer profiles) ───
    if method_name in {"ROIC vs WACC", "ROIC"}:
        # Use invested_capital if available, else approximate as total_assets - cash
        ic = invested_capital
        if ic and ic > 0 and ebit and shares > 0:
            nopat = ebit * (1 - _EFFECTIVE_TAX_RATE)
            roic = nopat / ic
            spread = roic - wacc
            ev = ic * (1.0 + spread / wacc) * sm
            return _ev_to_equity_ps(ev, net_debt, most_recent, shares)
        return None

    # ── Rule of 40 — SaaS quality governor (Tier 2 Tech) ─────────────────
    # Growth% + FCF margin% — the industry-standard SaaS quality metric.
    # <40 = low quality (unprofitable growth or slow decay); 40-60 = healthy;
    # >60 = best-in-class. Applied as a tier multiplier on EV/Revenue-based
    # valuation. Prevents the "50% growth at -40% FCF margin" trap where
    # pure EV/Revenue would overvalue unprofitable growth.
    #
    # Prefers research-sourced Rule of 40 score (captures actual quarter's
    # growth + FCF margin cleanly); falls back to financial-metric-derived
    # score (growth_base + fcf_margin_base in percent).
    if method_name in {"Rule of 40", "Rule-of-40"}:
        _saas = most_recent.get("_saas_metrics") or {}
        ro40_score = _saas.get("rule_of_40_score")
        if ro40_score is None:
            # Derive from engine inputs: growth_base + fcf_margin_base (decimals)
            ro40_score = (growth_base + fcf_margin_base) * 100
        # Tier multiplier on EV/Revenue base IV:
        #   score >= 60 → 1.5x (best-in-class premium)
        #   40 <= score < 60 → 1.0x (fair, no adjustment)
        #   0 <= score < 40 → 0.7x (unprofitable growth penalty)
        #   score < 0 → 0.5x (deteriorating)
        if ro40_score >= 60:
            tier_mult = 1.5
        elif ro40_score >= 40:
            tier_mult = 1.0
        elif ro40_score >= 0:
            tier_mult = 0.7
        else:
            tier_mult = 0.5
        # Base EV/Revenue IV (tech sub-type aware).
        # Option III Spec 2: use convergence-aware terminal EV/Rev so Growth SaaS
        # anchors to Mature SaaS 10x, not 22x perpetuated to terminal.
        base_mult, _ev_rev_basis = _qualified_ev_revenue_multiple(
            sector, profile_name, scenario, peer, most_recent)
        # Use forward revenue when available, else TTM
        fwd_rev = None
        if forward_consensus is not None:
            _rev_dict = forward_consensus.get("revenue") or {}
            fwd_rev = _rev_dict.get(scenario)
        if fwd_rev is None or fwd_rev <= 0:
            fwd_rev = revenue_base * (1 + growth_base) if revenue_base else None
        if fwd_rev is None or fwd_rev <= 0 or shares <= 0:
            return None
        mult = base_mult * tier_mult * growth_premium
        # SBC haircut for high-SBC tech
        _sbc_v = most_recent.get("stock_based_compensation")
        if _sbc_v and revenue_base and revenue_base > 0 and is_tech_sector(sector):
            if abs(_sbc_v) / revenue_base > 0.10:
                mult *= 0.93
        ev = fwd_rev * mult
        return _ev_to_equity_ps(ev, net_debt, most_recent, shares)

    # ── EV/Gross Profit — Payment Processors (Tier 2 Tech) ────────────────
    # For net-vs-gross reporters (PYPL/ADYEN/SQ) EV/Revenue is incomparable
    # because interchange flows through as revenue for gross reporters but
    # not net reporters. EV/GP normalizes on the actual take-rate economics.
    # Peer multiple: 18-22x for payment processors (tighter than EV/EBITDA).
    if method_name in {"EV/Gross Profit", "EV/GP"}:
        gross_profit = most_recent.get("gross_profit")
        # Fallback: revenue − cost_of_revenue when gross_profit not reported
        if gross_profit is None:
            rev_v = most_recent.get("revenue")
            cor_v = most_recent.get("cost_of_revenue")
            if rev_v and cor_v:
                gross_profit = rev_v - cor_v
        if gross_profit is None or gross_profit <= 0 or shares <= 0:
            return None
        # 18x default; can be overridden by peer.get("ev_gp") if set later
        gp_mult = peer.get("ev_gp", 18.0) * sm * growth_premium
        ev = gross_profit * gp_mult
        return _ev_to_equity_ps(ev, net_debt, most_recent, shares)

    # ── EV/Volume — Payment Processors (TPV × take rate × multiple) ────────
    # For payment networks (V/MA) and processors (ADYEN/SQ) where TPV
    # (Total Payment Volume) is the fundamental operational metric. Requires
    # tpv + take_rate from deep research (stored on most_recent by the
    # processor extractor). Falls back to None when data unavailable.
    #
    # Critical for Indian UPI ecosystem (Razorpay, Pine Labs, Paytm) where
    # take rates are 10-20 bps vs US card rails' 200-300 bps — applying
    # EV/Volume directly to bare TPV without take_rate adjustment would
    # inflate IV 10-20x. Always use (tpv × take_rate) for normalized NII.
    if method_name in {"EV/Volume", "EV/TPV"}:
        tpv = most_recent.get("tpv")  # total payment volume (annual $)
        take_rate = most_recent.get("take_rate_bps")  # basis points
        if tpv is None or tpv <= 0 or take_rate is None or take_rate <= 0 or shares <= 0:
            return None
        # Normalized revenue = TPV × take_rate_bps / 10000
        normalized_rev = tpv * take_rate / 10000.0
        # Apply EV/Revenue multiple (payment networks 15x, processors 5-7x)
        volume_mult = peer.get("ev_revenue", 6.0) * sm * growth_premium
        ev = normalized_rev * volume_mult
        return _ev_to_equity_ps(ev, net_debt, most_recent, shares)

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


_DCF_FAMILY_NAMES: frozenset[str] = frozenset({
    "DCF", "DCF (2-stage)", "DCF (FCF+)", "NRR-adj DCF",
    "Rev DCF (ARR)", "Backlog DCF", "PPA-backed DCF",
    "Backlog-coverage DCF", "Contracted-backlog DCF",
    "Unit Econ DCF", "Power Price DCF", "Reverse DCF",
    "DCF (Levered)", "Rev DCF (Mkt Sh)",
    # These four project cash flows through `_project_dcf` exactly like the
    # names above, but were missing here, so `_blend_methods` bucketed them as
    # multiples (which then took the since-retired sentiment composite). On
    # EL (Luxury Goods) every leg was "multi", so the 1.41 composite reached
    # 100% of the IV, cash-flow leg included.
    "DCF (5-yr)", "DCF (LTG)", "Rev DCF (GMV)", "Rev DCF",
})

#: Every method name _compute_method_value routes into _project_dcf. Today it
#: equals _DCF_FAMILY_NAMES (the blend's dcf-bucket membership test); it is
#: kept as its own name because the OE≤0 disable
#: gate (task #18) must knock out exactly the projecting set, so the
#: dispatcher and the gate share this one constant and can never drift.
#: Resources: the reserve runs out, so the projection stops. 15 years is the
#: standard planning horizon for a major producer's mine portfolio — long
#: enough to cover a typical asset life, short enough that it cannot smuggle a
#: perpetuity back in. Terminal salvage/reclamation is ZERO absent real data:
#: for most mines reclamation is a liability, so assuming none is already the
#: generous end of the range.
#: Industry routing has its OWN flag, default off. It used to share
#: FEATURE_RESOURCE_HOLDCO_MAP_V2 with the holdco look-through, so switching the
#: look-through on for ten holdcos (2026-09-15) silently re-routed every ticker:
#: 09988.HK went from Hyperscaler / Tech Conglomerate to Traditional Retail on
#: FMP's "Specialty Retail" label and published Underweight / SELL. The routing
#: commit's own delta sheet moves 88 of 100 HK and 98 of 100 SG profiles -- a
#: change that size is switched on deliberately, never as a side effect.
INDUSTRY_ROUTING_FLAG = "FEATURE_INDUSTRY_ROUTING"


def _industry_routing_in_scope(ticker: str) -> bool:
    """Industry routing for an approved sector wave, independent of the flag."""
    try:
        from src.data.industry_profile_map import in_routing_scope
        return in_routing_scope(ticker, _company_industry(ticker))
    except Exception:                                      # noqa: BLE001
        return False


def _company_industry(ticker: str):
    """FMP industry via the comps classification cache.

    The comps path fetches the same /stable/profile row for every run, so
    reading it here costs no extra request (a second, separately cached
    profile fetch is also what golden replay would see as unrecorded).
    """
    try:
        from src.data.regional_comps import get_fmp_classification
        ind = (get_fmp_classification(ticker) or {}).get("industry")
        if ind:
            return ind
    except Exception:                                      # noqa: BLE001
        pass
    from src.tools.api import get_company_industry
    return get_company_industry(ticker)


def _industry_routing_enabled() -> bool:
    """Industry-based profile routing, behind its own flag (default off)."""
    return os.getenv(INDUSTRY_ROUTING_FLAG, "").strip().lower() in ("1", "true", "yes", "on")


#: Anchors whose computability is per-ticker, not a property of the method.
_LOOKTHROUGH_ANCHORS = frozenset({"SOTP / NAV", "SOTP / NAV (look-through)"})

#: Every method the conglomerate look-through can answer. "NAV Discount"
#: carries 0.20 in the Holding Company profile and is the same net-of-discount
#: NAV; left unrequested it fell to a P/BV proxy of 0.0 and was dropped, so the
#: profile quietly ran on 0.80 of its stated weight. Profiles without a
#: template are unaffected -- the method returns None and the proxy stands.
_LOOKTHROUGH_METHODS = _LOOKTHROUGH_ANCHORS | frozenset({"NAV Discount"})

#: Backlog-coverage DCF (Wave 3 design, built early at the owner's priority,
#: 2026-09-21). The core projection with years 1-3 of its growth path bounded
#: by work that is already under contract. Two spellings of one leg, plus the
#: contracted-book variant for fixed-volume multi-year contracts (uranium, SWU),
#: which has no order-intake ratio and therefore no ceiling.
_BACKLOG_BOUNDED_METHODS: frozenset[str] = frozenset({"Backlog DCF", "Backlog-coverage DCF"})
_CONTRACTED_BACKLOG_METHODS: frozenset[str] = frozenset({"Contracted-backlog DCF"})
_BACKLOG_BOUND_YEARS = 3


def _backlog_growth_bounds(coverage: float, book_to_bill: Optional[float] = None,
                           years: int = _BACKLOG_BOUND_YEARS) -> list[tuple[float, Optional[float]]]:
    """[(floor, ceiling)] for years 1..`years` of revenue growth.

        coverage = backlog / revenue base            -- years of contracted work
        floor_t  = -(1 - min(coverage / t, 1))       -- year t is covered iff coverage >= t
        ceil_t   = book_to_bill - 1  (when cited)    -- orders, not hope, cap the ramp

    The floor generalises the bear-only one in `run_dcf_agent`; at t = 1 they
    are the same expression, so the two cannot double-apply. The ceiling is
    book-to-bill and nothing else: a "revenue <= backlog" ceiling would assume
    zero new awards and drive a 1.5x-coverage prime to zero revenue in year 3.
    Years after `years` are untouched -- the standard fade.
    """
    ceil = (float(book_to_bill) - 1.0) if (book_to_bill and book_to_bill > 0) else None
    return [(-(1.0 - min(max(coverage, 0.0) / t, 1.0)), ceil) for t in range(1, years + 1)]


def _bound_growth_schedule(schedule: list[float],
                           bounds: list[tuple[float, Optional[float]]]) -> tuple[list[float], list[dict]]:
    """(bounded schedule, one record per year a bound actually moved)."""
    out, moved = list(schedule), []
    for i, (lo, hi) in enumerate(bounds):
        if i >= len(out):
            break
        g = out[i]
        # A ceiling below the floor cannot both hold; contracted work wins.
        new = max(min(g, hi) if hi is not None else g, lo)
        if new != g:
            moved.append({"year": i + 1, "from": float(g), "to": float(new),
                          "bound": "floor" if new > g else "ceiling"})
            out[i] = new
    return out, moved


#: Forward legs on forward multiples (owner rule 2026-09-21: "default to NTM
#: EV/EBITDA or FY1/FY2 blended P/E rather than trailing LTM figures"). OFF by
#: default: it re-prices every Forward P/E and Forward EV/EBITDA leg in the book,
#: downward for any growing basket, and ships only once that has been measured.
NTM_FORWARD_FLAG = "NTM_FORWARD_MULTIPLES_ENABLED"


def _ntm_forward_enabled() -> bool:
    return os.getenv(NTM_FORWARD_FLAG, "").strip().lower() in ("1", "true", "yes", "on")


def _forward_peer_multiple(peer: dict, field: str, default: float) -> tuple[float, str]:
    """(multiple, source) for a leg whose METRIC is an NTM consensus figure.

    Consensus NTM EPS x a TRAILING peer P/E pairs next year's earnings with last
    year's multiple. For a basket whose earnings are growing the trailing
    multiple is the higher of the two, so the pairing overstates the leg -- the
    same class of defect as the through-cycle basis fix (75b797f): a multiple and
    the figure it multiplies must be stated on one basis. With the flag on, and
    a measured or authored `<field>_ntm` present, the forward multiple is used
    and the trace names it; otherwise the trailing one, exactly as before.
    """
    ntm = peer.get(f"{field}_ntm")
    if _ntm_forward_enabled() and isinstance(ntm, (int, float)) and ntm > 0:
        return float(ntm), f"peer median {field}_ntm (forward basis)"
    return float(peer.get(field, default)), f"peer median {field}"


#: The same idea, generalised: a method declared `implementable: False` with a
#: proxy, that becomes exact for a ticker once its review-gated input has been
#: accepted. It is requested ALONGSIDE its proxy; the blend takes the real value
#: when there is one and the proxy when there is not. "P/Rate Base" needs an
#: accepted rate base plus an owner-set cost of equity, and prices as P/BV --
#: exactly as before -- for every ticker that has neither.
_PER_TICKER_METHODS = _LOOKTHROUGH_METHODS | frozenset({"P/Rate Base"})


def _industry_routed_profile(ticker: str, sector: str, end_date: str = "",
                             trace: Optional[dict] = None):
    """(sector, profile, profile_data) from the industry map, or None.

    ``trace``, when given, is filled with what the router saw and why it
    answered as it did -- the part of a routing decision the return value
    cannot carry, and the part an error attribution needs.

    Returns None rather than guessing when the industry is unmapped, so an
    unknown industry falls through to the existing classifier VISIBLY instead
    of being assigned a neighbouring row.
    """
    _t = trace if trace is not None else {}
    try:
        from src.data.industry_profile_map import profile_for_ticker
        from src.data.sector_profiles import INDUSTRY_VALUATION_PROFILES
        industry = _company_industry(ticker)
        _t["industry"] = industry
        hit = profile_for_ticker(ticker, industry)
        if not hit:
            _t["outcome"] = "unmapped"
            return None
        r_sector, r_profile = hit
        _t.update(routed_sector=r_sector, routed_profile=r_profile)
        data = INDUSTRY_VALUATION_PROFILES.get(r_sector, {}).get(r_profile)
        if not data:
            _t["outcome"] = "profile_missing"
            return None
        # Do NOT route into a profile whose ANCHOR cannot be computed. The
        # anchor carries the largest weight, so routing there replaces a
        # possibly-wrong number with a proxy standing in for the method that
        # was supposed to be the improvement. Measured against consensus this
        # was the single worst effect of routing: every holding company got
        # worse, because Holding Company anchors `SOTP / NAV` at 0.70 with
        # implementable: False --
        #   CITIC           71.5% ->1310.1%
        #   Swire Pacific   15.3% -> 684.2%
        #   CK Hutchison     4.2% -> 185.3%
        # Falling through to the classifier is not a good answer either, but
        # it is an honest one, and the routing becomes correct for these names
        # the moment the look-through in holdco_sotp.py is wired into the
        # method dispatcher.
        methods = [m for m in (data.get("methods") or []) if isinstance(m, dict)]
        anchor = next((m for m in methods if m.get("anchor")),
                      max(methods, key=lambda m: m.get("weight") or 0)
                      if methods else None)
        if anchor and not anchor.get("implementable"):
            _why = None
            # "implementable" is a property of the METHOD IN GENERAL; whether
            # it can be computed for THIS ticker is a different question. A
            # conglomerate with a complete look-through can compute SOTP / NAV
            # exactly, and declining it on the static flag would send a name
            # we can now value properly back to the financial ladder -- which
            # is how Jardine Cycle & Carriage came to be valued as Mature SaaS.
            if anchor.get("name") in _LOOKTHROUGH_ANCHORS:
                try:
                    from src.agents.analysis import holdco_sotp
                    if holdco_sotp.enabled_for(ticker) and holdco_sotp.can_value(
                            ticker, end_date, ebitda_by_division=_accepted_division_ebitda(ticker)):
                        _log.info("[dcf] %s: anchor %r computable by "
                                  "look-through -> %s/%s",
                                  ticker, anchor.get("name"), r_sector, r_profile)
                        _t["outcome"] = "routed_via_lookthrough"
                        return (r_sector, r_profile, data)
                    _why = holdco_sotp.decline_reason(
                        ticker, end_date, ebitda_by_division=_accepted_division_ebitda(ticker))
                except Exception:                          # noqa: BLE001
                    _why = None
                if _why:
                    _log.info("[dcf] %s: look-through incomplete -- %s",
                              ticker, _why)
            _log.info("[dcf] %s: industry routing DECLINED -> %s/%s "
                      "(anchor %r is not implementable)",
                      ticker, r_sector, r_profile, anchor.get("name"))
            _t.update(outcome="declined_anchor_not_implementable",
                      anchor=anchor.get("name"), lookthrough_reason=_why)
            return None
        # "routed", not "applied": the caller decides whether a route changes
        # anything, and routing into the profile already chosen changes nothing.
        _t["outcome"] = "routed"
        return (r_sector, r_profile, data)
    except Exception as exc:                               # noqa: BLE001
        _log.warning("[dcf] %s: industry routing unavailable (%s)",
                     ticker, type(exc).__name__)
        _t.update(outcome="error", error=type(exc).__name__)
        return None


_DEPLETING_DCF = "Depleting Asset DCF (Finite Life, No TV)"
_DEPLETING_HORIZON_YEARS = 15

_DCF_PROJECTION_FAMILY: frozenset[str] = frozenset(_DCF_FAMILY_NAMES)


# ── SOTP (analyst) blend promotion (task #25) ───────────────────────────────
# Weight the analyst SOTP method carries once promoted into the blend.
# Profile weights sum to 1.0, so a weight of w buys a w/(1+w) share of the
# blended IV — 3.0 → exactly 75%: the analyst SOTP carries three quarters
# and the DCF+multiples blend keeps one quarter, internally renormalized
# (user-chosen for the 3690.HK/BABA/PDD/JD/MSFT/AMZN SOTP coverage; the
# method lands in the multi bucket like every other peer-relative method).
# Override via the env var
# (1.0 → 50/50, the original setting); 0 keeps the method shadow-only
# (pre-promotion behaviour).
try:
    _SOTP_ANALYST_BLEND_WEIGHT = max(
        0.0, float(os.getenv("SOTP_ANALYST_BLEND_WEIGHT", "3.0")))
except (TypeError, ValueError):
    _SOTP_ANALYST_BLEND_WEIGHT = 3.0

_SOTP_ANALYST_METHOD_NAMES: frozenset[str] = frozenset(
    {"SOTP (analyst)", "Analyst SOTP"})


#: Profiles where EV/Revenue is a growth-stage metric applied to a business
#: that has already crossed into profit. Scoped deliberately: the same
#: argument could be made for other sales-multiple profiles, but each has its
#: own cohort and its own evidence, and one gate measured is worth five
#: assumed.
_EV_REVENUE_GATED_PROFILES = frozenset({"Automotive & EV"})

#: Where the freed weight goes when the gate fires.
_EV_REVENUE_REALLOCATION = (("Forward P/E", 0.5), ("EV/EBITDA", 0.5))


def _gate_ev_revenue(profile_data: Optional[dict], profile_name: str,
                     most_recent: dict) -> tuple[Optional[dict], bool]:
    """Drop the sales multiple once a company earns something.

    EV/Revenue prices gross top line. On a Chinese OEM in a domestic price
    war that is a leveraged call on vehicle deliveries, not on equity: at a 6%
    EBITDA margin a static 0.35x EV/Revenue implies ~6x EV/EBITDA, but at 3-4%
    the SAME 0.35x implies 9-12x, so the multiple silently RISES as
    profitability falls. Geely blended to 63.11 against a 28.96 consensus and
    Chery to +172% -- the two worst genuine outliers in a 200-name universe --
    while EV/EBITDA standalone put Geely at 32.93.

    The gate is profitability, not sector: below breakeven a sales multiple is
    the only thing left, and a pre-revenue EV stub keeps it.

    Returns (profile, fired). Copy-on-write -- profile dicts are references
    into INDUSTRY_VALUATION_PROFILES and mutating one leaks into every later
    ticker sharing it.
    """
    if not profile_data or profile_name not in _EV_REVENUE_GATED_PROFILES:
        return profile_data, False
    methods = profile_data.get("methods") or []
    sales_legs = [m for m in methods if m.get("name") in _EV_REVENUE_NAMES]
    if not sales_legs:
        return profile_data, False
    ebitda = _safe((most_recent or {}).get("ebitda"))
    if ebitda is None or ebitda <= 0:
        return profile_data, False          # pre-breakeven: keep the sales leg

    freed = sum(float(m.get("weight") or 0.0) for m in sales_legs)
    kept = [dict(m) for m in methods if m.get("name") not in _EV_REVENUE_NAMES]
    by_name = {m["name"]: m for m in kept}
    for name, share in _EV_REVENUE_REALLOCATION:
        add = freed * share
        if name in by_name:
            by_name[name]["weight"] = float(by_name[name].get("weight") or 0.0) + add
        else:
            kept.append({"name": name, "weight": add, "implementable": True})
            by_name[name] = kept[-1]
    return {**profile_data, "methods": kept}, True


_EV_REVENUE_NAMES = frozenset({"EV/Revenue", "EV/NTM Revenue", "EV/NTM Rev",
                               "EV/Fwd Rev"})


# ── Balance-sheet financials: strip the EV, DCF and FCF legs (Phase 1.2A) ────
#
# 02888.HK — Standard Chartered, profile "Money Center Bank" — published a
# forward EV/EBITDA of HK$730 per share. Not a large number for a bank; a
# meaningless one. Enterprise value is market cap plus debt minus cash, and for
# a deposit-funded business the "debt" term IS the product. The same defect
# produced MU's $6,105 (a peak peer multiple on a peak consensus EPS, handled
# by the cyclical half of this phase) and it survived the whole suite.
#
# The strip removes EV/anything, the DCF family and FCF Yield. FCF Yield goes
# with them rather than staying as an equity-side leg because reported free
# cash flow on a balance-sheet financial is a deposit- and lending-flow
# artefact, not cash available to equity: SCHW's FY2025 customer balances are
# US$397.8bn of payables plus US$107.6bn of receivables against US$491.0bn of
# total assets, and a swing in either dwarfs the operating cash flow the ratio
# is built from.
#
# What is LEFT is the plan's allowed set for these profiles: P/TBV, P/E (norm),
# Residual Income, GGM/DDM, Excess Capital. No price band is invented — the
# engine's answer is the surviving blend, and if that blend is thin the report
# says so through methods_unavailable rather than padding it.

#: Tier 2 trigger: customer-balance funding at or above this share of total
#: assets means the balance sheet, not the fee stream, is what the name is.
_TIER2_CUSTOMER_BALANCE_RATIO = 0.30

#: The feed has NO dedicated customer-payables or margin-receivables line
#: (checked 2026-09-16), so the proxy sums the generic lines customer balances
#: actually land in. Measured live 2026-09-17:
#:
#:   IBKR   accountPayables  US$156.7bn =  77% of assets   (Tier 1 anyway)
#:   SCHW   accountPayables  US$142.0bn =  29%, otherCurrentLiabilities
#:          (bank deposits)  US$255.8bn =  52%             (Tier 1 anyway)
#:   PYPL   otherCurrentLiabilities (customer funds) US$40.2bn = 50%
#:   HOOD   accountsReceivables US$18.4bn = 48%, otherPayables US$12.0bn = 31%
#:
#: Each line is floored at zero before summing. FMP reports
#: `otherCurrentLiabilities` as a NEGATIVE balancing plug for some issuers
#: (CME −US$0.07bn, S68.SI −S$1.15bn, 0388.HK −HK$53.29bn against +HK$53.08bn
#: of real payables). An unclamped sum would subtract genuine payables out of
#: the numerator and understate the funding dependence the gate exists to
#: detect. This is a floor on a RAW FEED LINE, not a bound on an estimate.
_TIER2_PAYABLE_LINES = ("accounts_payable", "other_payables",
                        "other_current_liabilities")

#: Deposits, loan book and margin receivables. Read first-non-null, not summed:
#: `loans_receivable` (netLoans) and `loans_held_for_investment` are two
#: spellings of the same book in FMP's map, so summing them would double-count.
_TIER2_DEPOSIT_LINES = ("total_deposits",)
_TIER2_LOAN_LINES = ("loans_receivable", "loans_held_for_investment")
_TIER2_RECEIVABLE_LINES = ("accounts_receivable",)


def _tier2_customer_balance_ratio(
        most_recent: Optional[dict]) -> tuple[Optional[float], dict]:
    """(deposits + customer payables + loans + margin receivables) / assets.

    Returns ``(ratio, breakdown)``. ``ratio`` is None when total assets are
    missing or non-positive — a ratio with no denominator is not zero, and
    treating it as zero would quietly pass every name whose balance sheet the
    feed failed to deliver. ``breakdown`` carries the components and the keys
    they came from, so a firing can be audited against the filing instead of
    trusted.
    """
    row = most_recent or {}
    assets = _safe(row.get("total_assets"))
    if assets is None or assets <= 0:
        return None, {}

    def _floored_sum(keys: tuple[str, ...]) -> tuple[float, list[str]]:
        total, parts = 0.0, []
        for k in keys:
            v = _safe(row.get(k))
            if v is None:
                continue
            total += max(float(v), 0.0)
            parts.append(f"{k}={v:,.0f}")
        return total, parts

    def _first(keys: tuple[str, ...]) -> tuple[float, list[str]]:
        for k in keys:
            v = _safe(row.get(k))
            if v is not None:
                return max(float(v), 0.0), [f"{k}={v:,.0f}"]
        return 0.0, []

    pay, pay_parts = _floored_sum(_TIER2_PAYABLE_LINES)
    dep, dep_parts = _first(_TIER2_DEPOSIT_LINES)
    loan, loan_parts = _first(_TIER2_LOAN_LINES)
    recv, recv_parts = _first(_TIER2_RECEIVABLE_LINES)

    numerator = dep + pay + loan + recv
    return numerator / assets, {
        "total_assets": float(assets),
        "deposits": dep, "customer_payables": pay,
        "loans_receivable": loan, "margin_receivables": recv,
        "lines": dep_parts + pay_parts + loan_parts + recv_parts,
    }


def _is_balance_sheet_financial(profile_name: Optional[str],
                                most_recent: Optional[dict]) -> bool:
    """True when this name's liabilities are its product, so EV/DCF/FCF are out.

    Two tiers:

      * **Tier 1** — the profile is a balance-sheet business by construction
        (every bank variant, insurance, brokerage, holdco). No measurement
        needed; the classification is the evidence.
      * **Tier 2** — the profile is fee-based (asset manager, payment network,
        exchange, fintech) but a name routed there may still be deposit- or
        float-funded in fact. Measured against
        :data:`_TIER2_CUSTOMER_BALANCE_RATIO`.

    :data:`TIER2_EXEMPT_PROFILES` (Market Infrastructure, +SG) is skipped by
    Tier 2: clearing houses hold pass-through margin and guaranty-fund
    collateral that is not their funding. The exemption is load-bearing — ICE
    measures 0.613 and S68.SI 0.474 live (2026-09-17), so without it both
    would lose 0.65 and 0.55 of their profile weight respectively.
    """
    if not profile_name:
        return False
    if profile_name in BALANCE_SHEET_FINANCIAL_PROFILES:
        return True
    if profile_name not in BALANCE_SHEET_FINANCIAL_CONDITIONAL_PROFILES:
        return False
    if profile_name in TIER2_EXEMPT_PROFILES:
        return False
    ratio, _ = _tier2_customer_balance_ratio(most_recent)
    return ratio is not None and ratio >= _TIER2_CUSTOMER_BALANCE_RATIO


def _is_enterprise_value_leg(name: str) -> bool:
    """Any EV/* multiple. Prefix-matched rather than enumerated on purpose:
    every EV/* name in the taxonomy is an enterprise-value multiple by
    construction, and a new one added to a financial profile must be caught
    without anyone remembering to extend a list."""
    return str(name or "").startswith("EV/")


def _is_dcf_leg(name: str) -> bool:
    """The DCF family. Union of the projection family (which the dispatcher and
    the OE≤0 gate already share, so it cannot drift) and a substring test that
    also catches names the dispatcher does not project — e.g.
    "Depleting Asset DCF (Finite Life, No TV)"."""
    n = str(name or "")
    return n in _DCF_PROJECTION_FAMILY or "DCF" in n


def _is_stripped_balance_sheet_leg(name: str) -> bool:
    return (_is_enterprise_value_leg(name) or _is_dcf_leg(name)
            or str(name or "") == "FCF Yield")


def _gate_balance_sheet_financial(
    profile_data: Optional[dict],
    profile_name: Optional[str],
    most_recent: Optional[dict],
) -> tuple[Optional[dict], Optional[dict], Optional[str], bool]:
    """Strip EV/DCF/FCF legs from a balance-sheet financial's profile.

    Returns ``(profile, gate_record, exception_reason, is_financial)``, the
    first three mirroring :func:`_gate_growth_cagr_divergence`:

      * ``profile`` — a COPY with the legs removed and the blend left to
        renormalise, or the input unchanged.
      * ``gate_record`` — the ``gate_evaluations`` entry, only when legs were
        actually removed. Recording a firing that removed nothing would put
        no-ops in the forward ledger for every bank run, since the bank
        profiles already carry no EV/DCF leg.
      * ``exception_reason`` — set when Tier 2 was measured ABOVE the threshold
        but the profile is exempt. That is the one stand-down worth publishing:
        it is the audit trail for the clearing-house exemption, and without it
        "exempt" and "never looked" are indistinguishable in the run row.
      * ``is_financial`` — the classification alone, returned SEPARATELY from
        ``gate_record`` because the two come apart on exactly the names this
        gate was written for. ``Money Center Bank`` and ``Money Center Bank
        (SG)`` carry no EV/DCF/FCF leg, so there is nothing to strip and no
        record is emitted — yet 02888.HK still computed a ``Forward EV/EBITDA``
        shadow row of HK$730 per share. Tying the shadow skip to "did the strip
        remove something" therefore left the defect in place for every bank.
        Callers that suppress an EV-based computation must key off THIS, not off
        the record.

    Renormalisation is free: :func:`_blend_methods` divides by the sum of the
    weights that survived, so removing legs is all that is required. Nothing is
    reallocated by hand — unlike :func:`_gate_ev_revenue`, which moves the
    freed weight onto Forward P/E and EV/EBITDA and would here re-add the very
    leg being removed.

    **Re-anchoring.** ``_anchor_method`` defaults to ``"DCF"`` and is only
    overwritten by a method carrying ``anchor: True``. FinTech anchors on
    EV/EBITDA at 0.35, so stripping it without re-anchoring would leave the
    published report claiming a DCF anchor on a profile whose DCF leg was just
    deleted — the same class of defect this gate exists to remove. The
    highest-weight survivor is promoted instead.

    One consequence is deliberate and worth knowing: :data:`_norm_led` at the
    12-month-target cap tests ``"(norm)" in _anchor_method``, so a Tier-2-fired
    FinTech re-anchored onto "P/E (norm)" newly takes the normalised-earnings
    convergence path. That is consistent — if the anchor really is normalised
    earnings now, the target should converge on the IV they produce.
    """
    # Classified first and unconditionally: the answer drives the shadow skip
    # even when `profile_data` is missing or carries nothing to strip.
    is_financial = _is_balance_sheet_financial(profile_name, most_recent)

    if not profile_data:
        return profile_data, None, None, is_financial

    in_conditional = profile_name in BALANCE_SHEET_FINANCIAL_CONDITIONAL_PROFILES
    ratio, breakdown = (_tier2_customer_balance_ratio(most_recent)
                        if in_conditional else (None, {}))

    # Tier 2 measured above the threshold but exempt — publish the stand-down.
    if (in_conditional and profile_name in TIER2_EXEMPT_PROFILES
            and ratio is not None and ratio >= _TIER2_CUSTOMER_BALANCE_RATIO):
        return profile_data, None, (
            f"customer-balance ratio {ratio:.3f} ≥ "
            f"{_TIER2_CUSTOMER_BALANCE_RATIO:.2f} but {profile_name} is exempt "
            f"(pass-through margin / guaranty-fund collateral is not funding: "
            f"{', '.join(breakdown.get('lines') or []) or 'no lines reported'})"
        ), False

    if not is_financial:
        return profile_data, None, None, False

    methods = [m for m in (profile_data.get("methods") or [])
               if isinstance(m, dict)]
    removed = [m for m in methods if _is_stripped_balance_sheet_leg(m.get("name"))]
    if not removed:
        # Tier 1 matched but the profile carries nothing to strip — true of
        # every bank, insurance, GSE and holdco profile today. Logged, not
        # flagged: it fires on every bank run and would drown the flags that
        # matter. `is_financial` still returns True, which is what stops
        # 02888.HK's HK$730 forward EV/EBITDA from being computed at all.
        _log.info("[dcf] balance-sheet-financial profile %r carries no "
                  "EV/DCF/FCF leg to strip", profile_name)
        return profile_data, None, None, True

    kept = [dict(m) for m in methods if not _is_stripped_balance_sheet_leg(m.get("name"))]

    total_before = sum(float(m.get("weight") or 0.0) for m in methods)
    removed_weight = sum(float(m.get("weight") or 0.0) for m in removed)
    share = (removed_weight / total_before) if total_before > 0 else 0.0

    # Re-anchor: never leave a profile whose anchor was just deleted, because
    # the fallback is the literal string "DCF".
    reanchored_from = None
    if kept and not any(m.get("anchor") for m in kept):
        top = max(kept, key=lambda m: float(m.get("weight") or 0.0))
        reanchored_from = next(
            (m.get("name") for m in removed if m.get("anchor")), None)
        top["anchor"] = True

    if not kept:
        # Every leg was an EV/DCF/FCF leg. Returning an empty method list would
        # make _blend_methods return None and blank dcf_range for the ticker —
        # the "portfolio manager did not print" symptom. Not reachable for any
        # profile in the taxonomy today (FinTech, the thinnest, keeps
        # P/E (norm) at 0.15); guarded because a future profile could.
        _log.warning("[dcf] balance-sheet-financial strip would remove EVERY "
                     "leg of profile %r — refusing to strip", profile_name)
        return profile_data, None, None, True

    tier = "tier 1 (profile)" if not in_conditional else "tier 2 (measured)"
    legs = ", ".join(f"{m.get('name')} {float(m.get('weight') or 0.0):.2f}"
                     for m in removed)
    basis = (
        f"{profile_name} is a balance-sheet financial by {tier}: enterprise "
        f"value subtracts the liabilities that ARE the product, and reported "
        f"FCF is dominated by customer-balance flows. Stripped {legs} "
        f"({share:.1%} of profile weight); blend renormalised over the "
        f"survivors."
    )
    if in_conditional and ratio is not None:
        basis += (
            f" Customer-balance ratio {ratio:.3f} ≥ "
            f"{_TIER2_CUSTOMER_BALANCE_RATIO:.2f} "
            f"({', '.join(breakdown.get('lines') or []) or 'no lines reported'}"
            f" on total assets {breakdown.get('total_assets', 0):,.0f})."
        )
    if reanchored_from:
        new_anchor = next((m.get("name") for m in kept if m.get("anchor")), None)
        basis += f" Anchor moved {reanchored_from} → {new_anchor}."

    record = {
        "gate_id": "GATE_BALANCE_SHEET_FINANCIAL",
        # Weight share, not a counterfactual IV. The gate's decision variable
        # IS the share; the valuation consequence is in the run's base_iv and
        # in the golden baseline, and scripts/backtest_valuation_fixes.py
        # scores it by replaying the fixture through both engines rather than
        # by reading a stored counterfactual. Computing an IV both ways here
        # would mean running the blend twice per scenario.
        "metric": "ev_dcf_weight_share",
        "raw_input_path_a": round(share, 6),
        "gated_output_path_b": 0.0,
        "basis": basis,
        "applied": True,
    }
    return {**profile_data, "methods": kept}, record, None, True


# ── Phase 1.2B: cyclical peak-consensus routing ─────────────────────────────
#
# Split into three pure functions plus caller-side assembly, unlike Phase 1.2A's
# single gate. The reason is that 1.2B has to report VALUES under both paths —
# the swapped mid-cycle leg against the trailing leg it would replace, and the
# P/B-ROE leg against the forward legs it would suppress — and computing a value
# needs the full `_compute_method_value` argument list, which lives in the
# caller. Passing twenty arguments into a gate to avoid one call site is worse
# than deciding here and measuring there. It also keeps the trigger testable on
# a bare series, which is the part worth testing: the arithmetic that decides
# whether MU is at a cycle top.
#
# Ships OBSERVATION-ONLY (`applied: False`). See `_gate_cyclical_peak_record`.

def _historical_eps_series(series: Optional[list]) -> list[float]:
    """Per-share earnings for every annual row that can produce one.

    Computed as net_income / shares_outstanding rather than read from an `eps`
    field, because `_extract_annual_series` does not carry one — FMP's per-share
    EPS is not among the mapped fields, and the two inputs that produce it are.
    Rows are dropped, not zero-filled: a missing share count makes the quotient
    meaningless, and a zero would drag the mean down and widen sigma in the
    direction that makes the trigger fire.
    """
    out: list[float] = []
    for row in series or []:
        if not isinstance(row, dict):
            continue
        ni = row.get("net_income")
        sh = row.get("shares_outstanding")
        if ni is None or sh is None or sh <= 0:
            continue
        out.append(float(ni) / float(sh))
    return out


def _peak_consensus_trigger(
    series: Optional[list],
    forward_consensus: Optional[dict],
    scenario: str = "base",
) -> Optional[dict]:
    """Measure whether forward consensus EPS sits at a cyclical peak.

    Returns None when the question cannot be answered — no consensus, or no
    usable historical EPS — which is distinct from returning ``fired: False``.
    The distinction matters for the acceptance bar: a name with no history is
    not a quiet firing, it is an unscoreable one, and counting it as quiet would
    inflate the denominator of a hit-rate measured against "at least 10 scoreable
    firings".

    Evaluated on the BASE scenario only, once per ticker rather than per
    scenario. Whether a business is at a cycle top is a fact about the cycle,
    not about the bear/base/bull spread drawn around it; re-evaluating per
    scenario would let the bull case route itself to P/B-ROE and the bear case
    keep its forward legs, on the same history, in the same run.
    """
    if not forward_consensus:
        return None
    eps_fwd = ((forward_consensus.get("eps") or {}).get(scenario))
    if eps_fwd is None or eps_fwd <= 0:
        return None
    hist = _historical_eps_series(series)
    if not hist:
        return None

    eps_max = max(hist)
    n = len(hist)
    mean = sum(hist) / n
    # Sample stdev, and only when there is enough of a cycle for it to describe
    # one. See _PEAK_MIN_OBSERVATIONS_FOR_SIGMA.
    sigma = statistics.stdev(hist) if n >= 2 else 0.0
    sigma_usable = n >= _PEAK_MIN_OBSERVATIONS_FOR_SIGMA
    sigma_line = mean + _PEAK_EPS_SIGMA_MULTIPLE * sigma if sigma_usable else None
    max_line = _PEAK_EPS_MAX_MULTIPLE * eps_max if eps_max > 0 else None

    arms = []
    if max_line is not None and eps_fwd > max_line:
        arms.append("multiple-of-max")
    if sigma_line is not None and eps_fwd > sigma_line:
        arms.append("mean-plus-sigma")

    return {
        "fired": bool(arms),
        "arms": arms,
        "eps_forward": float(eps_fwd),
        "eps_max": eps_max,
        "eps_mean": mean,
        "eps_sigma": sigma if sigma_usable else None,
        "max_line": max_line,
        "sigma_line": sigma_line,
        "n_years": n,
        "multiple_of_max": (eps_fwd / eps_max) if eps_max > 0 else None,
    }


def _mid_cycle_leg_swaps(
    profile_data: Optional[dict],
    profile_name: Optional[str],
) -> list[dict]:
    """Which legs of a cyclical profile would move onto mid-cycle earnings.

    Exact-name matching only, so `EV/EBITDAR` (Airlines — whose lease
    normalisation is separately unimplemented) and `EV/EBITDA (Norm)` (Steel /
    Metals) are left alone rather than double-swapped. A leg whose normalised
    counterpart the profile ALREADY carries is skipped: Digital Asset Mining and
    Mining (Major) both list `EV/EBITDA (norm)`, and adding a second copy would
    double its weight in the blend.
    """
    if not profile_data or profile_name not in _CYCLICAL_PROFILES:
        return []
    methods = [m for m in (profile_data.get("methods") or [])
               if isinstance(m, dict)]
    present = {m.get("name") for m in methods}
    swaps = []
    for m in methods:
        target = _MID_CYCLE_LEG_SWAPS.get(str(m.get("name") or ""))
        if not target or target in present:
            continue
        swaps.append({
            "from": m.get("name"),
            "to": target,
            "weight": float(m.get("weight") or 0.0),
            "anchor": bool(m.get("anchor")),
        })
    return swaps


#: ── P/E normalization: trailing-earnings legs promoted onto cycle-normalized ──
#:
#: Owner-specified in the consumer discretionary post-mortem (Failure 2). The
#: defect it answers is recorded at the `P/E (Premium)` branch of
#: `_compute_method_value`: three spellings of trailing P/E — `P/E`, `P/E (ops)`
#: and `P/E (Premium)` — dispatch to the SAME trailing-12m net income, so the
#: names advertise a distinction the earnings source does not have. `Luxury Goods`
#: anchors 50% of its blend on `P/E (Premium)`, which means a beauty name at a
#: cyclical earnings trough is valued on trough earnings at a premium multiple.
#:
#: Measured across all 99 profiles: 37 carry a trailing P/E leg and NOT ONE of
#: the 37 also carries a normalized P/E leg, so the skip-if-present guard below
#: never engages for this map and all 37 are the population. 13 of the 37 ANCHOR
#: on the trailing leg — Luxury Goods at 0.50, Food & Beverage, Household /
#: Personal and Membership / Subscription Retail at 0.40-0.50 among them — so for
#: those the swap moves the headline rather than a side row.
#:
#: All four spellings are rewritten. `P/E (ops)` / `P/E (Ops)` dispatch to the
#: IDENTICAL trailing branch as `P/E` -- literally the same
#: `if method_name in {...}` set at L5487, reading the same trailing-12m net
#: income -- so excluding them while swapping `P/E` was an artificial
#: distinction, and the owner closed it: *"If a company's consolidated margin is
#: cyclically depressed or inflated, operating earnings are equally distorted."*
#: Both case spellings are listed because the taxonomy is not case-consistent:
#: `P/E (Ops)` x4 (Biopharma/Managed Care, HealthcareServices/Managed Care,
#: Healthcare Providers / Services, Pharma Distribution) and `P/E (ops)` x2
#: (Financials/Insurance, Insurance (P&C)). The map is keyed on an exact string
#: match, so listing only one spelling would silently leave the other on
#: trailing earnings -- which is the failure mode this whole mechanism exists to
#: remove, arriving through a case difference.
#:
#: The census this changes, measured over all 99 profiles
#: (`scratchpad/` census in tests/test_consumer_discretionary_gates.py):
#: eligible profiles 31 -> 37, of which ANCHORED on a swappable trailing leg
#: 10 -> 13. The three new anchors each carry `P/E (Ops)` at 0.40 -- Biopharma
#: and HealthcareServices Managed Care, and Pharma Distribution -- so for a
#: trough-earnings healthcare name the swap now moves the headline rather than a
#: side row, exactly as it already did for Luxury Goods at 0.50.
#:
#: ZERO golden blast radius, stated plainly rather than left to be discovered:
#: none of the 14 fixtures resolves to any of those six profiles (the basket is
#: two banks, two Tech conglomerates, two SG industrials, an S-REIT, a miner, a
#: memory IDM, a broker, an IPP, a payment network and COST), and separately
#: nothing in the basket reaches the 0.40 deviation bar at all -- the largest is
#: FCX at 0.3579 and FCX carries no trailing P/E leg. So this widening is
#: validated by unit tests and by the owner's reasoning, and by no fixture.
_PE_NORM_SWAP_LEGS: dict[str, str] = {
    "P/E":           "P/E (norm)",
    "P/E (Premium)": "P/E (norm)",
    "P/E (ops)":     "P/E (norm)",
    "P/E (Ops)":     "P/E (norm)",
}

#: Deviation of five-year normalized net income from trailing net income above
#: which a trailing P/E leg is no longer measuring the earnings its multiple was
#: calibrated on. Owner-specified at 0.40.
#:
#: The engine already computes this quantity: it is `_delta_pct` at the
#: `Normalized NI` audit flag, which fires at 0.15. This is the same number at a
#: higher bar, and the gap is deliberate — the flag is a disclosure and this is a
#: reweighting of the blend.
#:
#: MEASURED, and worth stating plainly because it bounds what this change can be
#: shown to do. On all 14 golden fixtures the largest |deviation| is FCX at
#: 0.3579, and FCX carries no trailing P/E leg at all. Of the four fixtures that
#: do carry one, 09988_HK and BABA sit at 0.1527, COST at 0.0780 (and COST is the
#: anchored one) and AAPL at 0.0534. NOTHING in the golden set reaches 0.40, so
#: the change has zero blast radius on the baseline. That is a comfort and a gap
#: at once: the intended beneficiary is a trough-earnings beauty name and Estée
#: Lauder is not a fixture. On EL's own published margin series
#: (0.052, 0.138, 0.152, 0.145, 0.141 on $15,600M of revenue, trough year most
#: recent) the normalizer excludes the 5.2% year and returns $2,246.4M against a
#: trailing $811.2M, a deviation of +1.769 — so it fires, and it fires on the
#: 50%-weighted anchor. Pinned by
#: tests/test_consumer_discretionary_gates.py rather than by the golden set.
_PE_NORM_SWAP_DEVIATION: float = 0.40

#: Every spelling the `P/E (norm)` dispatch branch accepts. Used only to decide
#: whether a profile ALREADY reaches that branch, which is the double-weight
#: guard in `_pe_norm_leg_swaps`.
#:
#: Wider than `_PE_NORM_SWAP_LEGS`' single target on purpose. The branch is
#: `if method_name in {"P/E (norm)", "P/E norm", "Normalized P/E"}:`, so a profile
#: carrying `P/E norm` reaches the same normalized earnings under a different
#: label; checking only for the exact string this swap writes would rename its
#: trailing leg anyway and leave the profile with two rows resolving to one
#: branch and one value — the weight doubling the guard exists to prevent, with
#: nothing in the output to show a leg had been counted twice.
#:
#: Vacuous on today's taxonomy: the profile tables use `P/E (norm)` and only
#: `P/E (norm)`, which
#: tests/test_consumer_discretionary_gates.py::test_the_normalized_leg_names_are_not_case_consistent
#: pins as the complete set of normalized spellings present. So this widening
#: buys nothing today and is here because the alternative fails silently.
_PE_NORM_BRANCH_SPELLINGS: frozenset[str] = frozenset({
    "P/E (norm)", "P/E norm", "Normalized P/E",
})


def _pe_normalization_deviation(most_recent: Optional[dict]) -> Optional[float]:
    """(normalized_net_income − trailing net_income) / trailing net_income.

    Mirrors `_delta_pct` at the `Normalized NI` flag with ONE extra guard: a
    non-positive normalized figure returns None. The flag still prints in that
    case, because a collapse to a loss is exactly what it exists to disclose; the
    swap does not fire, because there is no positive normalized EPS to switch the
    leg onto and `P/E (norm)` would return None, silently dropping the anchor's
    weight onto the remaining legs. Losing the guard would turn a trough into a
    valuation with no earnings leg at all.

    Trailing must be positive for the same reason in reverse: `_compute_method_value`
    returns None for the trailing branch when `eps <= 0`, so there is no trailing
    leg to promote.

    Signed, and the sign carries direction. Positive means the trailing year sits
    BELOW its own five-year norm (a trough), which is when a trailing P/E
    understates; negative means above it (a peak), when it overstates. The trigger
    uses `abs()` because both misprice, and the flag prints the sign so a reader
    can tell which one happened.
    """
    if not isinstance(most_recent, dict):
        return None
    norm = most_recent.get("normalized_net_income")
    cur = most_recent.get("net_income")
    if norm is None or norm <= 0 or cur is None or cur <= 0:
        return None
    return (norm - cur) / cur


def _pe_norm_leg_swaps(
    profile_data: Optional[dict],
    deviation: Optional[float],
) -> list[dict]:
    """Which trailing P/E legs this deviation promotes onto normalized earnings.

    Empty unless `abs(deviation)` clears `_PE_NORM_SWAP_DEVIATION`.

    Deliberately NOT gated on `_CYCLICAL_PROFILES` the way `_mid_cycle_leg_swaps`
    is. The defect is a trough-earnings anchor, and a beauty name at the bottom of
    its own cycle is not a cyclical in that table's sense — gating on it would
    have excluded every profile the post-mortem named. The two mechanisms stay
    separate because they are triggered by different evidence: that one by a
    forward-consensus peak signal, this one by the company's own five-year margin
    history. They can both fire on one profile, and `_apply_pe_norm_swaps` is
    idempotent against a leg the other already renamed.

    A leg whose normalized counterpart the profile ALREADY carries is skipped, for
    the reason `_mid_cycle_leg_swaps` gives: a second copy would double its weight
    in the blend. "Already carries" is tested against every spelling the dispatch
    branch accepts, not just the one this swap writes — see
    `_PE_NORM_BRANCH_SPELLINGS`.
    """
    if not profile_data or deviation is None:
        return []
    if abs(deviation) <= _PE_NORM_SWAP_DEVIATION:
        return []
    methods = [m for m in (profile_data.get("methods") or []) if isinstance(m, dict)]
    present = {m.get("name") for m in methods}
    if present & _PE_NORM_BRANCH_SPELLINGS:
        return []
    #: `_PE_NORM_SWAP_LEGS` now has FOUR keys and one value, so a profile naming
    #: any two of them would produce two rows named `P/E (norm)` and double the
    #: weight on that branch. `emitted` makes the promotion first-wins in row
    #: order.
    #:
    #: ── THE EMISSION PRIORITY ORDER, DOCUMENTED RATHER THAN IMPLIED ─────────
    #: First-wins is a choice, not a derivation: promoting both rows would
    #: double a weight, promoting neither would leave the profile reading two
    #: earnings sources at once, and which of those is wrong depends on why a
    #: profile would carry both in the first place. Nothing in the taxonomy
    #: answers that today -- measured, NO profile names two swappable spellings
    #: (0 of 99, and the count is more load-bearing now that the map has four keys
    #: and one value; pinned by
    #: test_the_swap_population_is_thirty_seven_of_ninety_nine) -- so this is a
    #: structural guard. Since it is a choice it needs a stated order, and the
    #: order is the one the rest of the profile pipeline already uses:
    #:
    #:   1. **Profile Overrides** decide WHICH profile's rows are being read.
    #:      `TICKER_SECTOR_LOOKUP`'s pin, then the ladder, then industry
    #:      routing, then the SOTP promotions, applied in the order
    #:      `_promote_lookthrough_sotp` -> `_promote_segment_sotp` ->
    #:      `_promote_sotp_analyst_profile` in `run_dcf_agent`. A promotion here
    #:      can replace the whole row set, so it is necessarily first.
    #:   2. **Analytical Flags** rewrite the selected profile's rows. This swap
    #:      is one of exactly two such mechanisms and it runs FIRST: applied in
    #:      `run_dcf_agent` ABOVE the scenario loop, because the margin deviation
    #:      it tests is scenario-invariant. `_mid_cycle_leg_swaps` is the other,
    #:      and it runs later, INSIDE the loop, base-scenario only, gated on
    #:      `_CYCLICAL_PROFILES`, and observation-only behind
    #:      `GATE_CYCLICAL_PEAK_CONSENSUS` (`applied: False`). `_apply_pe_norm_swaps`
    #:      is idempotent against a row the mid-cycle swap already renamed, so
    #:      the ordering cannot produce a double promotion.
    #:   3. **Default Rungs** are what survive: the profile table's own declared
    #:      rows, in declaration order, when no swap fires.
    #:
    #: WITHIN layer 2 the tie-break is the profile's own `methods` row order,
    #: which is the order its weights are declared in
    #: `INDUSTRY_VALUATION_PROFILES` and the order `_blend_methods` reads them.
    #: So "first" means "the row the profile author listed first", and a
    #: reordering of that list is a change of policy -- which is why
    #: tests/test_consumer_monotonic_ladder.py::
    #: TestTheOpsSpellingsAreInTheSwap::
    #: test_the_first_wins_order_is_the_profiles_own_row_order reverses a
    #: profile's rows and asserts the winner reverses with them, rather than
    #: asserting a fixed spelling wins.
    #:
    #: (Call sites are named rather than numbered on purpose. This file is ~12k
    #: lines and every edit above this point moves the numbers; the three
    #: numbered references this comment used to carry were already stale by 32
    #: lines when the gross-margin plumbing landed.)
    swaps = []
    emitted: set[str] = set()
    for m in methods:
        target = _PE_NORM_SWAP_LEGS.get(str(m.get("name") or ""))
        if not target or target in emitted:
            continue
        emitted.add(target)
        swaps.append({
            "from": m.get("name"),
            "to": target,
            "weight": float(m.get("weight") or 0.0),
            "anchor": bool(m.get("anchor")),
        })
    return swaps


def _apply_pe_norm_swaps(profile_methods: list, swaps: list[dict]) -> list:
    """Rewrite a profile's method rows onto their normalized spelling.

    Returns a NEW list of NEW dicts, and returns the input UNCHANGED when there is
    nothing to do. The rows are the very objects held in
    `INDUSTRY_VALUATION_PROFILES`, so mutating one would rewrite the profile for
    every subsequent ticker in the process — the copy-on-write discipline the SOTP
    overlay already follows.

    Rewrites `proxy` as well as `name`, and that is not incidental. Exactly two
    rows in the whole taxonomy carry a proxy that names a swappable trailing
    spelling, and both are Consumer at weight 0.05:

        Consumer / Food & Beverage  "Brand Valuation"  proxy="P/E"
        Consumer / Luxury Goods     "Brand Val"        proxy="P/E"

    `_blend_methods` resolves a non-implementable row through its proxy, so
    renaming only the anchor would leave that row blending a TRAILING P/E
    alongside a normalized one — the exact labelling inconsistency this change
    exists to remove, at 5% weight.

    The proxy rewrite therefore keys on `_PE_NORM_SWAP_LEGS` and NOT on the leg
    names that actually swapped, and the distinction is load-bearing. Luxury
    Goods has no leg literally called `P/E`; its anchor is `P/E (Premium)`. A
    `{s["from"]: s["to"]}` map built from the swaps would be
    `{"P/E (Premium)": "P/E (norm)"}`, would not match the `"P/E"` proxy, and
    would leave EL — the name this whole change exists for — blending both
    earnings sources at once. Measured before the fix: `Brand Val` came back out
    with `proxy="P/E"` while its anchor had become `P/E (norm)`.

    The proxy path is still gated on `swaps` being non-empty, so a profile whose
    ONLY trailing exposure is a proxy is left alone. That is deliberate: the
    trigger is a distorted trailing leg the blend actually weights, and the
    `excluded` filter at the call site empties `swaps` when `P/E (norm)` is not
    available, which closes the proxy route through the same gate.
    """
    if not swaps:
        return profile_methods
    by_from = {s["from"]: s["to"] for s in swaps}
    out = []
    for m in profile_methods:
        if not isinstance(m, dict):
            out.append(m)
            continue
        row = dict(m)
        if row.get("name") in by_from:
            row["name"] = by_from[row["name"]]
        if row.get("proxy") in _PE_NORM_SWAP_LEGS:
            row["proxy"] = _PE_NORM_SWAP_LEGS[row["proxy"]]
        out.append(row)
    return out


#: Pilot set for filing-derived segment SOTP. Deliberately an explicit list
#: rather than "any ticker whose filing parses": promoting a method changes
#: the blend for every name it touches, and these four are the ones whose
#: segment notes parse to two or more segments WITH profit. The remaining
#: seven SOTP-primary names are blocked on data that does not exist -- SGX
#: publishes no machine-readable segment note (Wilmar, ThaiBev, Olam,
#: SingPost), Geely declares a single reportable segment, WH Group and
#: Kingboard parse to revenue-only rows.
_SEGMENT_SOTP_TICKERS: frozenset[str] = frozenset({
    "00700.HK",   # Tencent  — VAS, Marketing, FinTech & Business Services, Others
    "01810.HK",   # Xiaomi   — Smartphones, IoT, Internet services, EV
    "03690.HK",   # Meituan  — core local commerce, new initiatives
})
#: GenScript was in this set and is deliberately NOT: its segment note covers
#: Life Science, ProBio and Bestzyme, but the value is Legend Biotech, which
#: has been an ASSOCIATE since the October 2024 deconsolidation and therefore
#: contributes no revenue segment at all. A segment SOTP omits 45.03% of
#: Legend's market capitalisation by construction -- it read 10.78 against a
#: 30.66 target and moved the name from 43.2% to 46.0%. The look-through in
#: holdco_sotp_templates.json is the right instrument for GenScript, and it
#: already marks the Legend stake at market.

#: Co-equal anchor weight, not the 3.0 (=75%) the ANALYST SOTP carries. A
#: filing-derived segment map is better evidence than a research note, but
#: the pilot path values each segment on a revenue multiple keyed off its
#: name, so it earns a seat at the table rather than the table.
_SEGMENT_SOTP_WEIGHT = 0.40

#: Profiles whose segment SOTP is priced on owner-set through-cycle EV/EBITDA
#: bands per business type, with the per-segment working recorded (see
#: `_SEGMENT_EBITDA_MULTIPLES`). That is a materially better instrument than
#: the name-keyed revenue multiple the pilot set uses, so it is promoted into
#: the blend rather than shadow-computed (owner, 2026-09-20).
_SEGMENT_SOTP_PROFILES: frozenset[str] = frozenset({
    "Refining & Marketing",
})


#: Tickers whose profile gains SOTP / NAV from a COMPLETE look-through even
#: though the profile itself does not declare the method. Explicit rather than
#: "anyone with a template", for the same reason the segment pilot is:
#: promoting a method moves every name that shares the profile.
#:
#: Olam (VC2.SI) is deliberately absent. Its look-through completes, but the
#: bridge subtracts a reported net debt that includes readily-marketable
#: inventories, and on that figure the SOTP reads SGD 0.355 against a 1.183
#: price -- a discount that belongs to the debt definition, not to a view.
#:
#: Keppel (BN4.SI) and Sembcorp (U96.SI), added 2026-09-15: they route to the
#: SG conglomerate and regulated-utility profiles, neither of which carries
#: SOTP / NAV, yet both are valued by the street as a sum of parts. Their
#: templates are all-equity (listed stakes at market, segment net profit x
#: P/E) and previewed at +10% to price, inside Maybank's ranges.
#:
#: Anta Sports (02020.HK), added 2026-09-18: it routes to Apparel / Athletic
#: Wear, whose anchor is EV/EBITDA at 0.40 and which carries no SOTP method at
#: all, so 100% of the valuation was blind to a listed 42.5% associate worth
#: HKD 51.0bn gross against a HKD 199.5bn market cap. Unlike the two SG names
#: its template is NOT all-equity -- the core is an EV/EBITDA band -- so the
#: bridge takes parent net debt, measured at HKD 24.166bn. Previewed at +12.8%
#: to price at the 9.0x band midpoint against a HKD 72.00 close, with the band
#: ends at -10.2% and +35.8%: inside the +10% convention at the midpoint and
#: straddling the price rather than asserting upside.
_LOOKTHROUGH_PROMOTE: frozenset[str] = frozenset(
    {"S08.SI", "BN4.SI", "U96.SI", "02020.HK"})
_LOOKTHROUGH_PROMOTE_WEIGHT = 0.40


def _in_lookthrough_promote(ticker) -> bool:
    """Membership test on the CANONICAL ticker, not the raw one.

    `enabled_for` and `can_value` both canonicalise -- `holdco_sotp` imports
    `canonical_ticker` locally for exactly that -- but a raw `in` test does
    not, so `2020.HK` and `02020.HK` would reach the same template through one
    gate and miss the promote through the other. The two normalisations move a
    HK ticker in OPPOSITE directions: `to_fmp_symbol('02020.HK')` strips the
    leading zero to `'2020.HK'` and `canonical_ticker('2020.HK')` restores it.
    The `ticker` at the call site is the raw loop variable off
    `state["data"]["tickers"]`, i.e. whatever the caller typed.

    Measured idempotent on S08.SI, BN4.SI, U96.SI, 02020.HK, 00700.HK and AS,
    so this changes nothing for the three names already promoted.
    """
    if not ticker:
        return False
    try:
        from src.tools.ticker_canonical import canonical_ticker
        key = canonical_ticker(ticker)
    except Exception:                                      # noqa: BLE001
        key = ticker                                       # fail closed to raw
    return key in _LOOKTHROUGH_PROMOTE


def _promote_lookthrough_sotp(profile_data, ticker, end_date):
    """Add SOTP / NAV to a ticker whose look-through completes.

    SingPost is a breakup candidate whose SingPost Centre alone (SGD 991m at a
    4.0-4.5% cap rate) exceeds the company's SGD 788m market capitalisation.
    Rail / Logistics prices it on EV/EBITDA and cannot see that at all.
    """
    if (not _in_lookthrough_promote(ticker) or not profile_data
            or not profile_data.get("methods")):
        return profile_data, False
    methods = profile_data["methods"]
    if any(m.get("name") in _LOOKTHROUGH_ANCHORS for m in methods):
        return profile_data, False
    try:
        from src.agents.analysis import holdco_sotp
        if not (holdco_sotp.enabled_for(ticker) and holdco_sotp.can_value(
                ticker, end_date, ebitda_by_division=_accepted_division_ebitda(ticker))):
            return profile_data, False
    except Exception:                                      # noqa: BLE001
        return profile_data, False
    scale = 1.0 - _LOOKTHROUGH_PROMOTE_WEIGHT
    scaled = [{**m, "weight": float(m.get("weight") or 0.0) * scale,
               "anchor": False} for m in methods]
    scaled.append({"name": "SOTP / NAV", "weight": _LOOKTHROUGH_PROMOTE_WEIGHT,
                   "anchor": True, "implementable": True})
    return {**profile_data, "methods": scaled}, True


def _promote_segment_sotp(profile_data: Optional[dict], ticker: str,
                          has_breakdown: bool,
                          profile_name: Optional[str] = None) -> tuple[Optional[dict], bool]:
    """Add "SOTP (segments)" to this ticker's profile at a co-equal weight.

    Copy-on-write: profile dicts are references into
    INDUSTRY_VALUATION_PROFILES and mutating one leaks the method into every
    later ticker sharing the profile. Existing weights are scaled down so the
    total still sums to 1.0, which keeps the relative ordering of the methods
    the profile author chose.

    Returns the input unchanged when there is nothing to promote, so every
    ticker outside the pilot set stays bit-identical.
    """
    _eligible = (ticker in _SEGMENT_SOTP_TICKERS
                 or (profile_name or "") in _SEGMENT_SOTP_PROFILES)
    if (not has_breakdown or not _eligible
            or not profile_data or not profile_data.get("methods")):
        return profile_data, False
    methods = profile_data["methods"]
    if any(m.get("name") in {"SOTP (segments)", "SOTP (Segments)"} for m in methods):
        return profile_data, False
    scale = 1.0 - _SEGMENT_SOTP_WEIGHT
    scaled = [{**m, "weight": float(m.get("weight") or 0.0) * scale,
               "anchor": False} for m in methods]
    scaled.append({"name": "SOTP (segments)", "weight": _SEGMENT_SOTP_WEIGHT,
                   "anchor": True, "implementable": True})
    return {**profile_data, "methods": scaled}, True


def _promote_sotp_analyst_profile(profile_data: Optional[dict],
                                  has_assumptions: bool) -> Optional[dict]:
    """Promote "SOTP (analyst)" into the resolved valuation profile.

    When the ticker carries extractor-built ``sotp_assumptions`` and the
    blend weight is positive, returns a COPY of ``profile_data`` with the
    method appended. Profile dicts are direct references into
    INDUSTRY_VALUATION_PROFILES and must never be mutated in place (that
    would leak the method into every later ticker sharing the profile).

    Returns the input object unchanged when there is nothing to promote
    (no assumptions, zero weight, empty profile, or the method already
    present) — tickers without SOTP evidence keep bit-identical blends.
    """
    if (_SOTP_ANALYST_BLEND_WEIGHT <= 0
            or not has_assumptions
            or not profile_data
            or not profile_data.get("methods")):
        return profile_data
    methods = profile_data["methods"]
    if any(m.get("name") in _SOTP_ANALYST_METHOD_NAMES for m in methods):
        return profile_data
    # The promoted weight belongs to the SOTP FAMILY, not to the machine-built
    # member of it. A profile that already carries a curated SOTP -- a
    # look-through NAV template with sourced stakes, or a broker's own
    # published table -- splits the weight evenly with it, on top of whatever
    # the profile already gave it. BN4.SI, 2026-09-15: the look-through said
    # S$12.25 and the broker table S$11.16, both inside the S$11.70-14.30
    # ground-truth range, and the extractor's S$7.12 outvoted them 75% to 15%
    # and published a SELL. With no curated SOTP present the analyst takes the
    # whole weight and blends are bit-identical to before.
    peers = [m for m in methods if m.get("name") in _SOTP_LED_METHODS]
    share = _SOTP_ANALYST_BLEND_WEIGHT / float(len(peers) + 1)
    kept = [
        {**m, "weight": float(m.get("weight") or 0.0) + share}
        if m.get("name") in _SOTP_LED_METHODS else m
        for m in methods
    ]
    return {
        **profile_data,
        "methods": kept + [{
            "name": "SOTP (analyst)",
            "weight": share,
            "anchor": False,
            "implementable": True,
        }],
    }


# ── Prediction ledger (B1) ────────────────────────────────────────────────────
# A run is only learnable if it records what produced its answer. These stamp
# dcf_range with the parameters in force and the Street reference at the time
# of the run -- two inputs a later outcome job cannot reconstruct, because
# both drift.

#: Constants a calibration would tune. The digest moves when any of them does
#: -- a hand edit included -- so outcomes produced under different parameter
#: sets are never pooled by accident.
_LEARNABLE_PARAM_NAMES: tuple[str, ...] = (
    "_GROWTH_MULT", "_MARGIN_DELTA_MULT",
    "_TV_DOMINANCE_THRESHOLD", "_TV_DOMINANCE_REWEIGHT",
    "_CALIBRATION_TOLERANCE", "_CONSENSUS_DIVERGENCE_MULT",
    "_SEGMENT_SOTP_WEIGHT", "_LOOKTHROUGH_PROMOTE_WEIGHT",
    "_SOTP_ANALYST_BLEND_WEIGHT",
)


def _gate_live_sotp(ticker: str, assumptions: dict, shares: float,
                    net_debt: Optional[float]) -> tuple[dict, Optional[str]]:
    """Live-extracted SOTP inputs, or the validated snapshot when the live
    value falls outside the ground-truth band.

    The 25 Aug production BABA run extracted 8x P/E on EBIT and published
    $61/ADS against a $152-201 consensus band; the validated snapshot for the
    same name sat at $174. Snapshot entries themselves are never gated, and a
    ticker without a ground-truth reference passes through untouched.
    Returns (assumptions to use, forward flag or None)."""
    if str(assumptions.get("_origin") or "").startswith("snapshot:") or shares <= 0:
        return assumptions, None
    try:
        from src.agents.analysis.sotp_ground_truth import check_table
        fx = float(assumptions.get("fx_usd_to_reporting") or 1.0)
        _tbl = _sotp_analyst_style(
            assumptions, shares=shares, net_debt=net_debt, fx_to_reporting=fx)
        if (_tbl or {}).get("degraded_no_segments"):
            # ── A DEGRADED EXTRACTION IS NOT A $0.00 EXTRACTION ─────────────
            # `check_table` reads `float(table.get("per_share") or 0.0)`, so
            # handing it the degraded table would grade the total as $0.00/ADS
            # and the flag below would report "live value $0.00/ADS outside the
            # reference" -- a true sentence about a number the engine never
            # computed, and one that hides the real failure (no usable segment
            # revenue) behind an arithmetic-looking one.
            #
            # The remedy is the same one an implausible grade triggers, because
            # a live extraction that found no usable segment revenue at all is a
            # strictly worse failure than the bad-multiple case this function
            # exists for (25 Aug, BABA, 8x P/E on EBIT, $61/ADS against a
            # $152-201 band). Only the flag text differs: it names what
            # happened rather than what the total graded at.
            from src.agents.analysis.sotp_snapshot import (
                load_sotp_snapshot, lookup_snapshot,
            )
            key, snap = lookup_snapshot(load_sotp_snapshot(), ticker)
            _reason = _tbl.get("degraded_reason") or "no usable segment revenue"
            if not snap:
                return assumptions, (
                    f"SOTP (analyst): live extraction degraded -- {_reason}. "
                    f"No validated snapshot to fall back to, so the method does "
                    f"not publish; the associates and net cash it did find are "
                    f"disclosed rather than valued.")
            return ({**snap, "_origin": f"snapshot:{key}",
                     "fx_usd_to_reporting": fx},
                    f"SOTP (analyst): live extraction degraded -- {_reason}. "
                    f"Replaced with the validated snapshot ({key}).")
        grade = check_table(ticker, _tbl)
        if not grade or grade["plausible"]:
            return assumptions, None
        from src.agents.analysis.sotp_snapshot import load_sotp_snapshot, lookup_snapshot
        key, snap = lookup_snapshot(load_sotp_snapshot(), ticker)
        live = grade["total"]
        band = f"${live['range'][0]}-{live['range'][1]}/ADS"
        if not snap:
            return assumptions, (f"SOTP (analyst): live value ${live['value_per_ads']:.2f}/ADS "
                                 f"outside the {band} reference and no validated snapshot "
                                 f"to fall back to -- treat with caution")
        return ({**snap, "_origin": f"snapshot:{key}", "fx_usd_to_reporting": fx},
                f"SOTP (analyst): live value ${live['value_per_ads']:.2f}/ADS outside the "
                f"{band} reference -- replaced with the validated snapshot ({key})")
    except Exception:
        return assumptions, None


def _accepted_division_ebitda(ticker: str) -> Optional[dict]:
    """Owner-accepted division EBITDA for a holdco look-through, in the template's
    currency, or None. CITIC, CK Hutchison and Swire value unlisted divisions on
    EV/EBITDA and no other source supplies division EBITDA, so without this the
    look-through declines and the Holding Company anchor falls to a P/BV proxy."""
    try:
        from src.agents.analysis import holdco_sotp
        from src.data import segment_memory as sm
        tpl = holdco_sotp.template_for(ticker)
        if not tpl:
            return None
        return sm.division_ebitda_for(ticker, tpl.get("currency") or holdco_sotp.currency_of(ticker))
    except Exception:  # noqa: BLE001
        return None


def _apply_accepted_segment_memory(ticker: str, assumptions: dict, end_date: str,
                                   statement_ccy: str, api_key=None) -> tuple[dict, Optional[str]]:
    """SOTP segment revenue and margin from owner-ACCEPTED segment memory.

    Only an entry the owner accepted on the Model Accuracy page, with exactly
    the figures accepted, is used (src/data/segment_memory.py). Both listings
    of a dual-listed company resolve to one memory entry and one forward
    consensus -- the ADR's (dual_listings.company_key) -- so 09988.HK and BABA
    get the same company value and differ only by share count and currency.
    Multiples stay with the current SOTP rows. Returns (assumptions, flag)."""
    try:
        from src.data import segment_memory as sm
        from src.data.dual_listings import company_key
        key, entry = sm.accepted_entry(ticker)
        if not entry:
            return assumptions, None
        ckey = company_key(ticker)
        estimates = get_analyst_estimates(ckey, end_date, period="annual", limit=10, api_key=api_key) or []
        fwd = next((e for e in estimates if getattr(e, "revenue_avg", None)), None)
        if fwd is None:
            return assumptions, (f"Segment memory ({key}) accepted but {ckey} has no forward consensus "
                                 f"revenue -- current SOTP inputs kept")
        ccy = (statement_ccy or "USD").upper()
        rate = 1.0 if ccy == "USD" else get_fx_rate(ccy, "USD", api_key)
        if not rate or rate <= 0:
            return assumptions, f"Segment memory ({key}) accepted but {ccy}->USD FX unavailable -- current SOTP inputs kept"
        new, info = sm.apply_to_sotp(assumptions, entry, entry_key=key,
                                     fwd_revenue_usd=float(fwd.revenue_avg) * float(rate))
        if not info.get("applied"):
            return assumptions, f"Segment memory ({key}) accepted but not applied: {info.get('reason')}"
        new["_segment_memory"] = {**info, "consensus_ticker": ckey, "consensus_period_end": fwd.period_end,
                                  "consensus_revenue_usd": float(fwd.revenue_avg) * float(rate)}
        return new, (f"SOTP (analyst): segment revenue and margins from accepted segment memory ({key}, "
                     f"FY{info['mix_year']} mix, 3-yr average margins) x {ckey} consensus revenue "
                     f"{fwd.period_end}; multiples unchanged")
    except Exception as exc:  # noqa: BLE001
        _log.warning("[dcf] %s: accepted segment memory not applied: %s", ticker, exc)
        return assumptions, None


def _ledger_num(v) -> Optional[float]:
    try:
        return None if v is None else round(float(v), 6)
    except (TypeError, ValueError):
        return None


def _param_version() -> str:
    """Digest of the valuation parameters in force, e.g. ``constants-3f2a9c…``.

    Covers the named scalars and every profile's method weights -- the
    table a calibration would change most."""
    import hashlib
    import json as _json
    snap: dict = {n: globals().get(n) for n in _LEARNABLE_PARAM_NAMES}
    try:
        from src.data.sector_profiles import INDUSTRY_VALUATION_PROFILES
        snap["profile_weights"] = {
            s: {p: [(m.get("name"), m.get("weight"))
                    for m in (d.get("methods") or []) if isinstance(m, dict)]
                for p, d in profiles.items() if isinstance(d, dict)}
            for s, profiles in INDUSTRY_VALUATION_PROFILES.items()
            if isinstance(profiles, dict)}
    except Exception:                                      # noqa: BLE001
        snap["profile_weights"] = None
    blob = _json.dumps(snap, sort_keys=True, default=str)
    return "constants-" + hashlib.sha1(blob.encode()).hexdigest()[:12]


def _consensus_at_run(ticker: str, consensus_pt: Optional[dict]) -> dict:
    """The Street reference as it stood when this run was made."""
    from datetime import datetime as _dt, timezone as _tz
    t = (ticker or "").upper()
    if t.endswith((".HK", ".SI")):
        # FMP has no consensus for HKEX/SGX. The S&P figure comes from a page
        # scrape (target_price.py, 25 s timeout) that does not belong on the
        # live path; the daily outcome job records it on the run's own day.
        return {"status": "deferred", "source": "stockanalysis.com",
                "fetched_at": None}
    now = _dt.now(_tz.utc).isoformat(timespec="seconds")
    cp = consensus_pt or {}
    target = cp.get("consensus")
    if not target:
        return {"status": "unavailable", "source": "fmp", "fetched_at": now}
    return {
        "status": "recorded", "source": "fmp",
        "target": target, "median": cp.get("median"),
        "low": cp.get("low"), "high": cp.get("high"),
        "currency": "USD" if "." not in t else None,
        "fetched_at": now,
    }


def _blend_methods(
    profile_methods: list[dict],
    method_values: dict[str, Optional[float]],
    c_macro: float,
    forward_flags: list[str],
    dcf_tv_fraction: float,
) -> tuple[Optional[float], dict]:
    """
    Apply the Master Map weights with the C_macro modifier.

    Formula:
      IV_DCF   = weighted-mean(values from DCF-family methods)
      IV_Multi = weighted-mean(values from non-DCF methods, e.g. P/E, EV/EBITDA, P/BV)
      IV_Final = (W_DCF × IV_DCF + W_Multi × IV_Multi) / (W_DCF + W_Multi)

    No quality/sentiment overlay: the quality x risk x commodity composite was
    retired (owner decision 2026-09-19), so every leg is a financial input
    times a peer multiple or a projection, and the IV can be rebuilt from them.

    Forward Gate A: if dcf_tv_fraction > 0.80, reduce DCF family weight by
    _TV_DOMINANCE_REWEIGHT and redistribute to P/BV (asset floor, multi bucket).

    Returns (final_iv, breakdown). breakdown is:
      {
        "iv_dcf":         float | None — weighted-mean DCF IV
        "iv_multi":       float | None — weighted-mean Multi IV
        "weight_dcf":     0..1         — DCF fraction of total weight
        "weight_multi":   0..1         — Multi fraction of total weight
        "legs_dropped":   list[dict]   — every profile leg that contributed no
                                         value: method, proxy, intended weight,
                                         computed value-or-None, and `reason`
                                         ("non_positive" | "uncomputable")
        "weight_surviving": 0..1 | None — share of the profile's intended
                                         weight that actually voted
        "weight_intended": float       — sum of the profile's raw leg weights
        "methods_surviving": int       — len(effective_weights)
        "single_method":  bool         — only one leg voted, so `final_iv` is
                                         that leg's value and not an average
      }

    A leg whose value is None is dropped because there is no opinion to average
    in. A leg whose value computed non-positive is ALSO dropped, but for a
    different reason: it is the most bearish opinion in the set, and dropping it
    hands its weight to the more optimistic survivors. Zero-filling it instead
    was measured and rejected — the resulting move is exactly the leg's profile
    weight, because a zero at weight w pulls a weighted mean down by w, so the
    magnitude comes from the weight table and not from anything the method
    computed. Both cases are therefore disclosed in `legs_dropped` rather than
    acted on. See the comment at the drop site.
    """
    dcf_bucket: list[tuple[float, float]] = []
    multi_bucket: list[tuple[float, float]] = []
    # (method, key whose value was used, weight after Gate A, bucket)
    parts: list[tuple[str, str, float, str]] = []
    #: Legs the profile asked for that contributed no value, with the reason.
    #: Recorded rather than `continue`d in silence — see the classification
    #: comment at the drop site for why the two reasons are not the same event
    #: and why neither is zero-filled.
    dropped: list[dict] = []
    #: Sum of the weights the profile table intended, before any drop. The
    #: surviving weight divided by this is how much of the intended blend
    #: actually voted; `weight_dcf` + `weight_multi` cannot express it because
    #: both are renormalised to sum to 1.0 over the survivors.
    intended_w = 0.0
    #: Same quantity accumulated only over the legs that voted, in the SAME
    #: pre-Gate-A terms as `intended_w` so the ratio between them is exact.
    #: `parts` stores post-Gate-A weights, so it cannot be summed for this.
    surviving_w = 0.0
    asset_floor_reweight = 0.0

    for m in profile_methods:
        raw_name = m["name"]
        # Resolve proxy
        effective_name = m.get("proxy", raw_name) if not m.get("implementable", True) else raw_name
        # The REAL method wins whenever it produced a value. A proxy exists to
        # stand in for a method that could not be computed; once it can be, it
        # is the answer and the proxy is not a second opinion. This mattered
        # for the conglomerate look-through: P/BV came back as a literal 0.0,
        # which is not None, so the proxy was taken and the exact NAV ignored.
        value, value_key = method_values.get(raw_name), raw_name
        if value is None or value <= 0:
            value, value_key = method_values.get(effective_name), effective_name
        if value is None:
            value, value_key = method_values.get(raw_name), raw_name
        w = m["weight"]
        intended_w += float(w or 0.0)
        if value is None or value <= 0:
            # TWO DIFFERENT EVENTS, previously indistinguishable from outside
            # this function because both took the same bare `continue`.
            #
            # `value is None` — nothing computed. There is no opinion to
            # average in, so dropping the leg and renormalising over the
            # survivors is the right arithmetic: you cannot mean in a number
            # you do not have.
            #
            # `value <= 0` — the method DID compute and returned a non-positive
            # equity value. That is an opinion, and the most bearish one in the
            # set. Dropping it hands its weight to the more optimistic
            # survivors, which is how a more conservative input produced a less
            # conservative output on the reinvestment charge (09988_HK 160.84 →
            # 188.82, +17.40%, `weight_dcf` 0.2778 → 0.0).
            #
            # Zero-filling the second case was measured and REJECTED. On the 14
            # golden fixtures it moves 2 — BN4_SI base 5.20 → 3.90 (−25.000%)
            # and U96_SI base 5.73 → 3.52 (−38.569%) — and each move is exactly
            # the dropped leg's profile weight, because a zero at weight w pulls
            # a weighted mean down by w. So the magnitude comes from the weight
            # table and not from anything the method computed: a DCF of −1.318
            # and one of −15.630 would be treated identically, and the
            # information in the value is discarded by saturation just as
            # completely as by omission. On U96_SI it would turn one surviving
            # leg of 5.7269 into "60% EV/EBITDA + 40% fabricated zero". Both
            # names are also in _LOOKTHROUGH_PROMOTE, added because they "are
            # valued by the street as a sum of parts" and their templates
            # "previewed at +10% to price" — a negative CONSOLIDATED DCF on a
            # conglomerate whose value sits in listed stakes is a statement
            # about the model's reach, not about the equity. Limited liability
            # makes 0 a FLOOR on true value, not an estimate of it.
            #
            # So the drop stands and the silence goes: the reason, the intended
            # weight and the computed value are published, and a non-positive
            # drop also raises a forward flag, because that is the case where
            # the published IV means something different from what it appears
            # to mean.
            dropped.append({
                "method": raw_name,
                "proxy": effective_name if effective_name != raw_name else None,
                "weight": round(float(w or 0.0), 6),
                "value": round(float(value), 4) if value is not None else None,
                "reason": "non_positive" if value is not None else "uncomputable",
            })
            if value is not None:
                # Only the non-positive case is flagged. 18 legs drop as
                # `uncomputable` across the 14 golden fixtures against 6 that
                # drop as non-positive, and flagging all 24 would be prose no
                # reader gets to the end of — the opposite failure from silence.
                # The non-positive case is the one worth a line because it is
                # the one where the drop is a decision the blend made about an
                # opinion it had, and the published IV is not what it looks like.
                _flag = (
                    f"Non-positive leg dropped: {raw_name} = {value:,.4f} "
                    f"({float(w or 0.0):.0%} of intended weight renormalised "
                    f"onto the survivors)"
                )
                if _flag not in forward_flags:
                    forward_flags.append(_flag)
            continue

        is_dcf = (raw_name in _DCF_FAMILY_NAMES) or (effective_name == "DCF")

        # Forward Gate A: de-weight DCF family if TV-dominated
        if dcf_tv_fraction > _TV_DOMINANCE_THRESHOLD and is_dcf:
            asset_floor_reweight += w * _TV_DOMINANCE_REWEIGHT
            w = w * (1 - _TV_DOMINANCE_REWEIGHT)
            if "80/20 Rule: DCF weight reduced (TV > 80%)" not in forward_flags:
                forward_flags.append("80/20 Rule: DCF weight reduced (TV > 80%)")

        if is_dcf:
            dcf_bucket.append((value, w))
        else:
            multi_bucket.append((value, w))
        surviving_w += float(w or 0.0)
        parts.append((raw_name, value_key, w, "dcf" if is_dcf else "multi"))

    # Asset floor reweight goes to multi (P/BV is a multi-method anchor)
    if asset_floor_reweight > 0:
        asset_floor_val = method_values.get("P/BV")
        if asset_floor_val and asset_floor_val > 0:
            multi_bucket.append((asset_floor_val, asset_floor_reweight))
            parts.append(("P/BV (asset floor)", "P/BV",
                          asset_floor_reweight, "multi"))
            # Gate A moved this weight out of a DCF leg that already counted
            # into `surviving_w` at its REDUCED post-Gate-A value, so the floor
            # leg's share has to be added back or the ratio understates. With
            # the floor created nothing was dropped — the weight changed
            # buckets — and `weight_surviving` must say 1.0.
            #
            # The `else` is deliberately absent. When P/BV is unavailable the
            # reweighted share goes nowhere at all: `total_w` is smaller than
            # the profile intended and no leg receives it. Leaving it out of
            # `surviving_w` makes the ratio report that loss, which is the one
            # place in this function where weight is destroyed rather than
            # moved, and it has been silent until now.
            surviving_w += float(asset_floor_reweight)

    # ── The disclosure block ────────────────────────────────────────────────
    # Built BEFORE the degenerate returns, because a profile whose every leg
    # dropped is precisely the case a reader needs the record for. Returning
    # `{}` there would publish `legs_dropped: None`, which reads as "nothing was
    # dropped" — the same silence this block exists to remove, one branch later.
    # Every consumer reads these keys with `.get()`, so the numeric keys being
    # absent on the degenerate paths is safe.
    single_method = len(parts) == 1
    disclosure = {
        # `effective_weights` lists survivors only, so a dropped leg is
        # invisible downstream and `iv_dcf: None` cannot be told apart from a
        # DCF bucket that computed and came out non-positive. These keys close
        # that gap without moving a single number.
        "legs_dropped": dropped,
        # Share of the profile's intended weight that actually voted. `intended_w`
        # sums the raw table weights; `surviving_w` sums the weights of the legs
        # that reached a bucket, plus the Gate A asset-floor share when that
        # floor was created. So 1.0 means the profile voted as written, and
        # anything below it means weight left the blend — either a leg was
        # dropped, or Gate A de-weighted a DCF into a P/BV floor that turned out
        # to be unavailable and the share went nowhere. Both were silent before.
        "weight_surviving": (round(surviving_w / intended_w, 6)
                             if intended_w > 0 else None),
        "weight_intended":  round(intended_w, 6),
        # Counts the P/BV asset-floor leg when Gate A added it, because it is a
        # real voting leg carrying real weight.
        "methods_surviving": len(parts),
        "single_method":     single_method,
    }

    if not dcf_bucket and not multi_bucket:
        return None, disclosure

    # c_macro applies uniformly inside each bucket → cancels in the bucket's
    # weighted-mean. Kept for parity with the legacy formula (ratio-invariant).
    macro = 1.0 + c_macro
    dcf_w_total   = sum(w * macro for _, w in dcf_bucket)
    multi_w_total = sum(w * macro for _, w in multi_bucket)
    total_w       = dcf_w_total + multi_w_total
    if total_w <= 0:
        return None, disclosure

    iv_dcf   = (sum(v * w * macro for v, w in dcf_bucket)   / dcf_w_total)   if dcf_w_total   > 0 else 0.0
    iv_multi = (sum(v * w * macro for v, w in multi_bucket) / multi_w_total) if multi_w_total > 0 else 0.0

    # Final blended IV
    final_iv = (dcf_w_total * iv_dcf + multi_w_total * iv_multi) / total_w

    # Single-survivor disclosure. Threshold-free: `len(parts) == 1` needs no
    # constant, so there is nothing here to tune or to get wrong. A "blend" of
    # one method is not a blend, and the payload otherwise publishes
    # `weight_multi: 1.0` as though one had happened. Measured on the shipped
    # baseline this fires on 6 of 57 blend calls — U96_SI on all four of its
    # calls, where base IV 5.7269 is literally its `EV/EBITDA` leg value to
    # four decimals, plus BN4_SI's bear case and MU's backward-gate call.
    if single_method:
        _sm_name, _sm_key, _sm_w, _sm_bucket = parts[0]
        _flag = (
            f"Single-method blend: only {_sm_name} ({_sm_key}, {_sm_bucket}) "
            f"contributed a value — {surviving_w / intended_w:.0%} of the "
            f"profile's intended weight survived, so the published IV is that "
            f"one method and not an average of views"
            if intended_w > 0 else
            f"Single-method blend: only {_sm_name} ({_sm_key}, {_sm_bucket}) "
            f"contributed a value, so the published IV is that one method and "
            f"not an average of views"
        )
        if _flag not in forward_flags:
            forward_flags.append(_flag)

    breakdown = {
        "iv_dcf":           round(iv_dcf, 4)         if iv_dcf   > 0 else None,
        "iv_multi":         round(iv_multi, 4)       if iv_multi > 0 else None,
        "weight_dcf":       round(dcf_w_total   / total_w, 4),
        "weight_multi":     round(multi_w_total / total_w, 4),
        # The weight each method actually carried, after proxies, skipped
        # methods and Gate A. The profile table states intent; this states
        # what happened. IV = sum(weight x method_values[value_key]).
        "effective_weights": [
            {"method": n, "value_key": k, "bucket": b,
             "weight": round(w * macro / total_w, 6)}
            for n, k, w, b in parts
        ],
        **disclosure,
    }

    return final_iv, breakdown


# ── Backward Logic Gate ───────────────────────────────────────────────────────

# ── Scenario-aware analyst SOTP (owner decision A+, 2026-09-19) ─────────────
#
# Without an analyst `_scenarios` block the analyst SOTP returned its base
# value in all three scenarios. On 09618.HK and 09988.HK it carries 77% of the
# blend, so the bear case could barely fall: JD's bear IV was HK$184 against a
# HK$105 spot -- above every published broker target -- and Alibaba's bear
# stayed 39% above spot. Each SEGMENT now flexes by (a) its own 12-month
# revenue scenario tree from deep research (bear = the tree's lowest rate,
# bull = its highest, relative to the tree's probability-weighted rate) and
# (b) the same 0.75x / 1.25x multiple band every other multiple leg and the
# published SOTP already carry. Net cash and associates do not flex: they are
# balance-sheet facts, not scenario outcomes. Revenue trees alone moved the
# bear SOTP only 4-6%; the band is what makes the bear case a bear case.

#: Tokens too generic to identify a segment on their own.
_SEGMENT_GENERIC_TOKENS = frozenset({
    "group", "segment", "segments", "business", "businesses", "division",
    "services", "service", "other", "others", "all", "new", "and", "the",
    "international", "digital", "commerce", "holdings", "inc", "ltd", "co",
})


def _segment_tokens(name: str) -> frozenset:
    n = (name or "").lower().replace("e-commerce", "ecommerce")
    return frozenset(t for t in re.split(r"[^a-z0-9]+", n)
                     if t and t not in _SEGMENT_GENERIC_TOKENS)


def _match_segment_trees(row_names: list, trees: dict) -> dict:
    """{row name: tree} -- exact normalised name first, then shared
    non-generic tokens (overlap coefficient >= 0.5), each tree used once.
    A segment with no confident match gets no tree (band only), rather than
    a neighbour's scenarios."""
    out: dict = {}
    free = dict(trees or {})
    norm = lambda x: re.sub(r"[^a-z0-9]+", "", (x or "").lower())
    for rn in row_names:
        for tn in list(free):
            if norm(tn) == norm(rn):
                out[rn] = free.pop(tn)
                break
    cands = []
    for rn in row_names:
        if rn in out:
            continue
        a = _segment_tokens(rn)
        for tn in free:
            b = _segment_tokens(tn)
            if a and b:
                sc = len(a & b) / min(len(a), len(b))
                if sc >= 0.5:
                    cands.append((sc, rn, tn))
    for sc, rn, tn in sorted(cands, key=lambda x: -x[0]):
        if rn not in out and tn in free:
            out[rn] = free.pop(tn)
    return out


def _tree_factor(tree: Optional[dict], scenario: str) -> float:
    """Revenue level factor for bear/bull relative to the tree's expectation."""
    scens = [x for x in ((tree or {}).get("scenarios") or [])
             if isinstance(x.get("rate"), (int, float))]
    if not scens or scenario not in ("bear", "bull"):
        return 1.0
    tot = sum(float(x.get("prob") or 0.0) for x in scens)
    ref = (sum(float(x.get("prob") or 0.0) * x["rate"] for x in scens) / tot
           if tot > 0 else sum(x["rate"] for x in scens) / len(scens))
    r = min(x["rate"] for x in scens) if scenario == "bear" else max(x["rate"] for x in scens)
    return (1.0 + r) / (1.0 + ref) if 1.0 + ref > 0 else 1.0


def _sotp_trace_table(table: dict) -> dict:
    """The analyst SOTP table, reduced to what rebuilds its per-share value."""
    return {
        "rows": [{k: r.get(k) for k in ("name", "method", "multiple", "revenue_fwd", "ebit", "value")}
                 for r in (table.get("rows") or [])],
        **{k: table.get(k) for k in ("segment_value", "associates", "net_cash", "nav",
                                     "holdco_discount_pct", "holdco_discount", "final",
                                     "per_share", "per_share_reporting", "fx_to_reporting",
                                     "shares")},
    }


def _sotp_scenario_from_trees(table: dict, trees: Optional[dict], scenario: str,
                              sm: float) -> Optional[dict]:
    """Per-share analyst SOTP for `scenario`, flexing segment values only."""
    rows = table.get("rows") or []
    final, ps = table.get("final"), table.get("per_share_reporting")
    nav = table.get("nav")
    if not rows or not final or not ps or not nav or final <= 0 or nav <= 0:
        return None
    matched = _match_segment_trees([r.get("name") for r in rows], trees or {})
    seg = 0.0
    detail = []
    for r in rows:
        v = float(r.get("value") or 0.0)
        f = _tree_factor(matched.get(r.get("name")), scenario)
        seg += v * f * sm
        detail.append({"segment": r.get("name"), "tree": r.get("name") in matched,
                       "revenue_factor": f, "multiple_band": sm})   # full precision: the export rebuilds from it
    fixed = float(nav) - float(table.get("segment_value") or sum(float(r.get("value") or 0) for r in rows))
    nav_s = seg + fixed
    disc = float(table.get("holdco_discount") or 0.0) / float(nav)
    final_s = nav_s * (1.0 - disc)
    if final_s <= 0:
        return None
    return {"per_share_reporting": final_s * float(ps) / float(final),
            "segments": detail, "trees_matched": len(matched)}


# ── Two-tier valuation (owner decision, 2026-09-19) ─────────────────────────
#
# Tier 1, the FUNDAMENTAL IV, is DCF plus peer-median multiples and nothing
# else, so every number in it can be rebuilt from a financial input and a peer
# multiple. Tier 2, the 12-MONTH TARGET, is derived from that IV by one rule
# for every name: it converges a stated share of the way from spot toward the
# IV. Nothing adjusts either tier for "quality": the quality x risk x commodity
# composite was retired outright (owner decision, same day) after it had
# multiplied the IV's multiples leg by up to 1.85x with no peer evidence, and
# then, as a peer-bounded premium on the target, moved targets by ~2% in a
# direction set by hand-cut bands.
#
# Before this, the target came from five different recipes, two of which (the
# bank P/B path) could land on the far side of the IV from spot (SCHW $32.88
# against IV $86.42 and price $104.85). Those recipes are still computed, and
# published as cross-checks in `pt_bridge.cross_checks`.

def _apply_reserve_floor(scenario_results: dict, floor_ps: Optional[float],
                         detail: Optional[dict] = None) -> Optional[dict]:
    """Publish the reserve value as a cross-check; floor the bear case with it.

    Owner decision 2026-09-20. The standardized measure is proved reserves
    only, after tax, at trailing SEC prices -- 75-79% below price on COP, DVN
    and OXY -- so it never carries weight in the blend. It does say what the
    company's own reserves are worth, which is a floor the bear case should
    not sit under. Returns the record written to the bear scenario, or None.
    """
    if not floor_ps or floor_ps <= 0:
        return None
    for scen in scenario_results.values():
        if not isinstance(scen, dict):
            continue
        mit = scen.get("method_iv_table")
        if isinstance(mit, dict):
            mit["Reserve NPV (PV-10)"] = round(floor_ps, 2)
            scen["cross_check_methods"] = _cross_check_methods(mit, scen.get("effective_weights"))
    bear = scenario_results.get("bear")
    if not isinstance(bear, dict) or not isinstance(bear.get("intrinsic_value"), (int, float)):
        return None
    if bear["intrinsic_value"] >= floor_ps:
        return None
    before = bear["intrinsic_value"]
    bear["intrinsic_value"] = round(floor_ps, 2)
    from src.data.industry_inputs import RESERVE_MEASURE_REMARK
    rec = {"floor_per_share": round(floor_ps, 2), "before": before,
           "period": (detail or {}).get("period"),
           "source_url": (detail or {}).get("source_url"),
           "note": ("after-tax discounted value of proved reserves, less net debt, per share"),
           "assumption": RESERVE_MEASURE_REMARK}
    bear["reserve_floor"] = rec
    if isinstance(bear.get("forward_flags"), list):
        bear["forward_flags"].append(
            f"Bear case floored at the reserve value: {before:,.2f} -> {floor_ps:,.2f} per "
            f"share (proved reserves, after tax, less net debt)")
    return rec


def _cross_check_methods(method_iv_table: Optional[dict],
                         effective_weights: Optional[list]) -> list[str]:
    """Legs computed and published but carrying no weight in the blend."""
    used: set = set()
    for w in effective_weights or []:
        used.add(w.get("method")); used.add(w.get("value_key"))
    return sorted(k for k in (method_iv_table or {}) if k not in used)


#: Horizon of the gate's forward score: the T-1 model and the T-1 market price
#: are both scored against the close this many days after the T-1 fiscal
#: period end.
_T1_FORWARD_HORIZON_DAYS = 365
#: How far back from a target date a close may be taken (weekends, holidays,
#: a fiscal year ending on a non-trading day).
_T1_PRICE_LOOKBACK_DAYS = 15


def _close_on_or_before(prices, day: datetime) -> Optional[tuple[float, str]]:
    """Last positive close dated on or before `day` and within the lookback.

    `Price` carries its date on `.time`, not `.date`."""
    lo = (day - timedelta(days=_T1_PRICE_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    hi = day.strftime("%Y-%m-%d")
    best: Optional[tuple[str, float]] = None
    for p in prices or []:
        t = str(getattr(p, "time", None) or (p.get("time") if isinstance(p, dict) else "") or "")[:10]
        c = getattr(p, "close", None) if not isinstance(p, dict) else p.get("close")
        if not t or c is None or not (lo <= t <= hi):
            continue
        try:
            c = float(c)
        except (TypeError, ValueError):
            continue
        if c > 0 and (best is None or t > best[0]):
            best = (t, c)
    return (best[1], best[0]) if best else None


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
    profile_name: str = "",
) -> tuple[bool, str, dict]:
    """
    T-1 Year Test: value the company on its previous fiscal year's financials
    and compare to the stock price at the end of that fiscal year.

    Uses the same blended multi-method approach as the main valuation when
    profile_data is provided, falling back to pure DCF otherwise.

    Returns (calibration_error, calibration_note, calibration_record).
    calibration_error=True means the model is >25% off → flag "Calibration Error".
    The status is decided by the 12-month OUTCOME when one has matured (owner,
    2026-09-20): model closer or level = passed, market closer = fired. The T-1
    gap is reported either way. Without a matured outcome the T-1 tolerance
    decides, as it always did.

    calibration_record is the same result as structured fields, plus a forward
    score: the T-1 model and the T-1 market price, each against the close
    `_T1_FORWARD_HORIZON_DAYS` later. The contemporaneous gap cannot tell a
    miscalibrated model from an early one (NKE: model $40.01, market $60.59,
    a year later $35.51); the forward score can.
    """
    record: dict = {"status": "skipped", "tolerance": _CALIBRATION_TOLERANCE,
                    "forward": {"verdict": "UNSCORABLE"}}

    def _skip(reason: str) -> tuple[bool, str, dict]:
        record["skip_reason"] = reason
        return False, f"Skipped — {reason}", record

    if len(series) < 3:
        return _skip("insufficient history for T-1 test")

    try:
        end_dt = datetime.strptime(end_date[:10], "%Y-%m-%d")
        # T-1 baseline financials: the second-most-recent fiscal year.
        t1_row = series[-2]

        # The benchmark price is dated to the END of that fiscal year, not to
        # the run date minus 365 days. The two differ by up to a year and a
        # half: LULU's FY2025 ended 2025-02-02 at $414.20, while the old window
        # read $169.62 on 2025-09-19 and charged the model a 170% error for the
        # stock's own collapse. Across 61 scored production runs the old date
        # mis-stated 16 verdicts in both directions (2 false alarms, 14 masked
        # failures).
        try:
            t1_dt = datetime.strptime(str(t1_row.get("period") or "")[:10], "%Y-%m-%d")
        except ValueError:
            t1_dt = None
        if t1_dt is not None and t1_dt < end_dt:
            record["benchmark_basis"] = "fiscal_period_end"
        else:
            t1_dt = end_dt - timedelta(days=365)
            record["benchmark_basis"] = "run_date_minus_365"
        record["t1_period"] = t1_dt.strftime("%Y-%m-%d")
        fwd_dt = t1_dt + timedelta(days=_T1_FORWARD_HORIZON_DAYS)

        # One fetch covers both the benchmark and the forward close.
        prices = get_prices(
            ticker,
            (t1_dt - timedelta(days=_T1_PRICE_LOOKBACK_DAYS)).strftime("%Y-%m-%d"),
            min(fwd_dt, end_dt).strftime("%Y-%m-%d"),
            api_key=api_key,
        )
        if not prices:
            return _skip("no historical price data for T-1")
        t1_close = _close_on_or_before(prices, t1_dt)
        if t1_close is None:
            return _skip("invalid T-1 price")
        actual_price, record["t1_price_date"] = t1_close
        record["t1_price"] = actual_price
        revenue_t1 = t1_row.get("revenue", 0)
        shares_t1  = t1_row.get("shares_outstanding") or series[-1].get("shares_outstanding")
        net_debt_t1 = t1_row.get("net_debt") or 0.0

        # The SAME margin basis the live valuation uses, on the rows known at
        # T-1: owner earnings (stock comp deducted) when SBC is disclosed in
        # >=3 of the years, the outlier-filtered mean, and the OE<=0 fallback
        # to the median positive year. The backtest used to average raw
        # reported FCF instead -- for MELI 23.2% on a 9% net margin, customer
        # float included -- so it tested a different method from the one that
        # produces today's number.
        _hist = series[:-1]
        _sbc_t1 = sum(1 for r in _hist[-5:] if r.get("stock_based_compensation") is not None)
        _oe_t1 = _mean_fcf_margin(_hist, field="fcf_owner_earnings")
        if _oe_t1 is not None and _sbc_t1 >= 3:
            fcf_margin_t1, _t1_field = _oe_t1, "fcf_owner_earnings"
        else:
            fcf_margin_t1, _t1_field = (_mean_fcf_margin(_hist) or 0.0), "free_cash_flow"
        # The same structural-turnaround guard the live margin takes, on the
        # window as it stood at T-1 -- or the backtest tests a different method
        # from the one that produces today's number (the MELI lesson above).
        _turn_t1 = _turnaround_margin(_hist, field=_t1_field)
        if _turn_t1:
            fcf_margin_t1 = _turn_t1["recent_two_year"]
            record["fcf_margin_turnaround"] = True
        if fcf_margin_t1 <= 0:
            _pos_t1 = _median_positive_fcf_margin(_hist, field=_t1_field)
            if _pos_t1 is not None:
                fcf_margin_t1 = _pos_t1
        record["fcf_margin_basis"] = _t1_field
        record["fcf_margin_t1"] = round(float(fcf_margin_t1), 6)

        # Historical growth rate from T-2 data, then the live growth schedule:
        # convergence fade toward terminal growth for the alpha profiles, the
        # tech decay for growth-phase tech. Held flat it compounded MELI's 43%
        # for ten years.
        growth_t1 = _historical_cagr(series[:-1]) or 0.05
        _schedule_t1: Optional[list[float]] = None
        if profile_name in _CONVERGENCE_ALPHA_PROFILES:
            _schedule_t1 = _growth_convergence_schedule(
                growth_t1, tgr, alpha=_CONVERGENCE_ALPHA, years=_PROJECTION_YEARS)
        elif profile_name in _GROWTH_DECAY_DELTA:
            _schedule_t1 = _decayed_growth_schedule(
                growth_t1, profile_name, years=_PROJECTION_YEARS)
        _proj_t1 = {"growth_schedule": _schedule_t1}
        record["growth_t1"] = round(float(growth_t1), 6)
        record["growth_schedule"] = ("convergence" if profile_name in _CONVERGENCE_ALPHA_PROFILES
                                     else "decay" if profile_name in _GROWTH_DECAY_DELTA
                                     else "flat")

        if not shares_t1 or shares_t1 <= 0 or not revenue_t1 or revenue_t1 <= 0:
            return False, "Skipped — missing T-1 shares or revenue"

        # ── Core DCF for T-1 (always needed as DCF method input) ──────────
        # NOT charged for reinvestment, because the projection this one is
        # compared against is not either — GATE_GROWTH_REINVESTMENT ships
        # observation-only. That parity is the whole point of the scorer: a T-1
        # blend and a T blend differ in ONE input, so the difference is
        # attributable. Charging one side would measure the charge, not the
        # company, and would do it on the one path that produces a direction
        # call rather than a number.
        #
        # When the charge goes live this call must gain
        # `sales_to_capital=_sales_to_capital(t1_row)` in the same commit, read
        # off `t1_row` rather than from `revenue_t1` because the helper pairs
        # revenue with invested capital from ONE row — the same misalignment the
        # `fcf_margin_pairs` comprehension above guards against. The
        # `_compute_method_value` calls below would get it for free if the ratio
        # were computed inside that function, which is where it belongs.
        iv_dcf_t1, pv_fcf_t1, pv_tv_t1, _ = _project_dcf(
            revenue_t1, fcf_margin_t1, growth_t1, 0.0,
            wacc, tgr, fcf_floor, net_debt_t1, shares_t1,
            growth_schedule=_schedule_t1,
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
                        projection=_proj_t1,
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
                        ticker=ticker,
                    )

            for ex in profile_data.get("excluded", []):
                method_values_t1.pop(ex, None)

            forward_flags_t1: list[str] = []
            # Deliberately blends the RAW profile rows, not the P/E-normalized
            # ones `run_dcf_agent` builds. The live engine promotes a trailing
            # P/E leg onto `P/E (norm)` when normalized net income deviates from
            # trailing by more than `_PE_NORM_SWAP_DEVIATION`, and this path
            # cannot reproduce that decision: `t1_row` is `series[-2]`, a raw
            # annual row, and carries no `normalized_net_income` — that key is set
            # on `most_recent` only. Computing it here would mean normalizing the
            # window ending at T-1, which is a second normalizer invocation on a
            # scorer that is itself blocked on Phase 3 and has no measurement to
            # check the result against.
            #
            # So the divergence is recorded rather than closed: for the 37 profiles
            # carrying a trailing P/E leg, a T-1 comparison is NOT like-for-like
            # with what production would have published. Left alone because
            # stacking an unverifiable change onto an unmeasurable one is how a
            # scorer stops meaning anything.
            iv_t1_blended, _t1_breakdown = _blend_methods(
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

        record["t1_iv"] = iv_t1
        record["method"] = method_label
        if iv_t1 <= 0:
            return _skip("T-1 model returned non-positive IV")

        # Forward score. Since 2026-09-20 this DECIDES the status when it has
        # matured -- see the module note on _outcome_status.
        if fwd_dt <= end_dt:
            fwd_close = _close_on_or_before(prices, fwd_dt)
            if fwd_close is not None:
                from src.memory.gate_backtest import delta_error_verdict
                fwd_price, fwd_date = fwd_close
                # Path A = the market's T-1 price, path B = the T-1 model.
                v = delta_error_verdict(actual_price, iv_t1, fwd_price, metric="price_12m")
                record["forward"] = {
                    "horizon_days": _T1_FORWARD_HORIZON_DAYS,
                    "price": fwd_price, "price_date": fwd_date,
                    "model_error_pct": v.get("error_b_pct"),
                    "market_error_pct": v.get("error_a_pct"),
                    "delta_error_pct": v.get("delta_error_pct"),
                    "verdict": {"HELPED": "MODEL_CLOSER", "FALSE_ALARM": "MARKET_CLOSER",
                                "NEUTRAL": "TIE"}.get(v["verdict"], v["verdict"]),
                }
        else:
            record["forward"] = {"verdict": "NOT_MATURED",
                                 "matures_on": fwd_dt.strftime("%Y-%m-%d")}

        error_pct = abs(iv_t1 - actual_price) / actual_price
        record["error_pct"] = error_pct
        _when = f" on {record['t1_price_date']}"
        _fwd = record.get("forward") or {}
        _verdict = _fwd.get("verdict")

        # ── The outcome decides, where there is one ──────────────────────
        if _verdict in ("MODEL_CLOSER", "MARKET_CLOSER", "TIE"):
            record["status_basis"] = "forward_outcome"
            _m, _k = _fwd.get("model_error_pct"), _fwd.get("market_error_pct")
            _scored = (f"12m later the {'model' if _verdict == 'MODEL_CLOSER' else 'market'} was "
                       f"closer ({_m:.0%} vs {_k:.0%})" if _verdict != "TIE" and
                       isinstance(_m, (int, float)) and isinstance(_k, (int, float))
                       else (f"12m later model and market were level "
                             f"({_m:.0%} vs {_k:.0%})" if isinstance(_m, (int, float))
                             and isinstance(_k, (int, float)) else "12m outcome scored"))
            _gap = (f"T-1 {method_label} IV ${iv_t1:.2f} vs price ${actual_price:.2f}{_when} "
                    f"= {error_pct:.0%} gap")
            if _verdict == "MARKET_CLOSER":
                record["status"] = "fired"
                return True, f"Calibration Error: {_gap}; {_scored}", record
            record["status"] = "passed"
            return False, f"T-1 passed on outcome ({method_label}): {_gap}; {_scored}", record

        # ── No matured outcome: the T-1 tolerance still rules ────────────
        record["status_basis"] = "t1_tolerance"
        if error_pct > _CALIBRATION_TOLERANCE:
            record["status"] = "fired"
            note = (f"Calibration Error: T-1 {method_label} IV ${iv_t1:.2f} vs actual "
                    f"${actual_price:.2f}{_when} = {error_pct:.0%} error (>{_CALIBRATION_TOLERANCE:.0%} tolerance)")
            return True, note, record

        record["status"] = "passed"
        note = (f"T-1 passed ({method_label}): model ${iv_t1:.2f} vs actual "
                f"${actual_price:.2f}{_when} = {error_pct:.0%} error")
        return False, note, record

    except Exception as e:
        return _skip(f"T-1 test error: {e}")


# ── Scenario ordering invariant ───────────────────────────────────────────────
#
# Scenario ordering is an axiomatic property of risk, not an output of the
# blend: a more conservative set of assumptions must never raise intrinsic
# value. The blend cannot guarantee it, because its legs are not scenario-
# invariant in whether they EXIST. A leg that resolves non-positive is dropped
# and the survivors are renormalised, so a scenario that stresses the business
# hardest is also the scenario most likely to lose a leg -- and losing the leg
# that was doing the penalising raises the answer.
#
# Measured on BN4.SI (Keppel), which is the case this was written against. Its
# profile "Conglomerate / Industrial (SG)" votes two multi legs: `EV/EBITDA` at
# 0.5333 and `SOTP (published)` at 0.4667. There is no DCF leg in ANY scenario
# (`weight_dcf` 0.0, `iv_dcf` None throughout).
#
#   bear  EV/EBITDA dropped -> SOTP (published) alone at weight 1.0 -> 8.3672
#   base  EV/EBITDA 1.30 @ .5333 + SOTP 11.16 @ .4667              -> 5.9004
#   bull  EV/EBITDA 2.95 @ .5333 + SOTP 13.95 @ .4667              -> 8.0789
#
# The dropout is an equity-bridge failure, not a cash-flow one. In bear the
# scenario multiplier takes the enterprise value to SGD 9.021bn against
# SGD 9.325bn of net debt plus SGD 0.322bn of minority interest, so
# `_ev_to_equity_ps` computes SGD -0.626bn of ordinary equity and floors it at
# 0.0 -- and a floored zero is then dropped as a non-positive leg. The single
# survivor is the HIGHEST of bear's three computed legs (SOTP 8.37 against
# Forward P/E 4.21 and Forward EV/EBITDA 0.07), so the blend lands at 7.37
# after the 0.8812 composite, above base's 5.20.
#
# Had the zero been RETAINED at its 0.5333 weight, bear would read
# 0.0 x 0.5333 + 8.37 x 0.4667 = 3.906, or 3.44 after the composite -- below
# base, correctly ordered. The dropout is worth +3.93 to the bear IV. That is
# the composition gain this invariant exists to stop publishing.
#
# The remedy is a clamp on the OUTPUT and not a change to the blend, on the
# owner's explicit instruction. Flooring a non-positive leg at zero and keeping
# its weight was tried and rejected -- "base IV for BN4 and u96 drop too much.
# not intuitive" -- and `967a3c5` shipped the dropout as a DISCLOSURE instead
# (`legs_dropped`, `weight_surviving`, `single_method`). So the leg still
# drops; this stops the dropped leg from inverting the scenario set, and the
# gate record names the composition change that caused it rather than leaving
# the clamp to look like an unexplained edit to a published number.
#
# Base is the PIVOT and is never moved. The card leads with base IV, and an
# invariant whose enforcement rewrites the headline number is an invariant
# nobody will trust. Bear is pulled down onto base and bull up onto it, which
# is exactly the owner's `min(Bear IV, Base IV)` generalised by symmetry.
# Raising a bull case to base says "no upside modelled"; it does not invent
# value, and it is the only reading that leaves base untouched on both sides.
_SCENARIO_ORDER: tuple[str, ...] = ("bear", "base", "bull")


def _voted_legs(scen: Optional[dict]) -> dict[str, float]:
    """{method: weight} for the legs that actually carried weight.

    `methods_used` cannot answer this. It is built from raw profile rows, so on
    BN4.SI it names `'DCF'` in base and `'EV/EBITDA'` in bear while neither
    carried any weight at all (`iv_dcf` None in all three scenarios;
    `EV/EBITDA` absent from bear's `method_iv_table`). `effective_weights` is
    the blend's own record of what voted.
    """
    out: dict[str, float] = {}
    for row in ((scen or {}).get("effective_weights") or []):
        if isinstance(row, dict) and row.get("method"):
            try:
                out[str(row["method"])] = float(row.get("weight") or 0.0)
            except (TypeError, ValueError):
                out[str(row["method"])] = 0.0
    return out


def _enforce_scenario_ordering(
    scenario_results: dict[str, dict],
) -> Optional[dict]:
    """Clamp the outer scenarios onto base; return the record, or None if ordered.

    Pure: it reads `scenario_results` and returns what it would change, and the
    caller applies it. Keeping the mutation out of here is what makes the
    invariant testable without an engine run -- a scenario triple is the whole
    input.

    The record answers the owner's step 1 ("check if the inversion is driven by
    method dropouts / composition gain") with data rather than with inspection:
    per scenario it names the legs that voted, the legs base had that this one
    lost, and the share of base's voting weight those lost legs carried. A
    dropout-driven inversion shows `weight_lost_vs_base` near the surviving
    leg's weight; an inversion from genuinely disordered inputs shows zero.
    """
    base = scenario_results.get("base") or {}
    base_iv = base.get("intrinsic_value")
    if not isinstance(base_iv, (int, float)) or isinstance(base_iv, bool):
        return None                     # no pivot to order against: refuse

    base_legs = _voted_legs(base)
    composition: dict[str, dict] = {}
    for name in _SCENARIO_ORDER:
        scen = scenario_results.get(name) or {}
        legs = _voted_legs(scen)
        lost = sorted(set(base_legs) - set(legs))
        composition[name] = {
            "legs_voted": sorted(legs),
            "n_legs": len(legs),
            "single_method": len(legs) == 1,
            "legs_lost_vs_base": lost,
            "weight_lost_vs_base": round(
                sum(base_legs.get(m, 0.0) for m in lost), 6),
        }

    clamped: list[dict] = []
    flags: list[str] = []
    for name, cmp_ in (("bear", lambda a, b: a > b), ("bull", lambda a, b: a < b)):
        iv = (scenario_results.get(name) or {}).get("intrinsic_value")
        if not isinstance(iv, (int, float)) or isinstance(iv, bool):
            continue                    # a missing scenario is not a violation
        if not cmp_(float(iv), float(base_iv)):
            continue
        after = float(base_iv)
        clamped.append({"scenario": name, "before": round(float(iv), 4),
                        "after": round(after, 4)})
        rel = "Bear" if name == "bear" else "Bull"
        op = ">" if name == "bear" else "<"
        flags.append(
            f"⚠ INVARIANT_VIOLATION_SCENARIO_INVERSION: {rel} "
            f"({float(iv):.2f}) {op} Base ({float(base_iv):.2f}); clamped to Base"
        )

    if not clamped:
        return None
    return {"pivot": "base", "base_iv": round(float(base_iv), 4),
            "clamped": clamped, "composition": composition, "flags": flags}


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
    # Per-ticker GS-style SOTP assumptions assembled by the SOTP extractor
    # (src/agents/analysis/sotp_extractor.py) or injected from a fixture.
    sotp_assumptions_all   = state["data"].get("sotp_assumptions", {})
    # Per-ticker Biopharma pipeline assets for the rNPV method.
    # Produced by _extract_pipeline_assets() in deep_research.py.
    pipeline_assets_all    = state["data"].get("pipeline_assets", {})
    # Per-ticker REIT metrics (cap rate override, occupancy, WALE, DPU/AFFO).
    # Produced by _extract_reit_metrics() in deep_research.py.
    reit_metrics_all       = state["data"].get("reit_metrics", {})
    # Per-ticker bank metrics (CET1, target ROE, NIM, efficiency, NPL).
    # Produced by _extract_bank_metrics() in deep_research.py.
    bank_metrics_all       = state["data"].get("bank_metrics", {})
    # Per-ticker SaaS metrics (NRR, Rule of 40, CAC payback, magic number).
    # Produced by _extract_saas_metrics() in deep_research.py.
    saas_metrics_all       = state["data"].get("saas_metrics", {})
    # Per-ticker framework-extracted KPIs for sub-profiles new to PR #6
    # (Regulated Utility, Upstream O&G, Semi Fabless, Semi IDM/Foundry,
    # Telco, Mining (Major), Automotive & EV, Managed Care). Produced by
    # the generic framework_metrics task in deep_research.py — fires only
    # when ticker's profile_name is in SECTOR_KPI_FRAMEWORK and NOT in
    # _LEGACY_COVERED_PROFILES (avoids double-extraction of Insurance/Bank/SaaS/REIT).
    framework_metrics_all  = state["data"].get("framework_metrics", {})
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

        # Retry a transient fetch failure (rate limit / hiccup) before giving
        # up on the ticker entirely — a single flaky FMP call shouldn't blank
        # the whole Valuation Methodology panel for the run.
        line_items = None
        _line_items_exc: Exception | None = None
        for _attempt in range(_LINE_ITEMS_MAX_RETRIES + 1):
            try:
                line_items = search_line_items(
                    ticker,
                    ["revenue", "free_cash_flow", "shares_outstanding",
                     "debt_to_equity", "net_debt", "total_debt", "ebitda", "net_income",
                     "total_equity", "total_assets", "dividends_per_share",
                     # The EV -> equity bridge deducts this. Same trap the R&D
                     # note below records: requested AND copied, or it is None
                     # on every row and the deduction silently does nothing.
                     "minority_interest",
                     "book_value_per_share", "capital_expenditure", "ebit",
                     "interest_expense", "invested_capital",
                     "research_and_development", "stock_based_compensation",
                     # REIT-specific
                     "depreciation_and_amortization", "operating_cash_flow",
                     "cash_and_equivalents",
                     # Counted as cash in net debt (_net_debt_net_of_investments).
                     # Requested AND copied: the 75aa05b fix copied it into rows
                     # but never asked for it, so the 09988.HK production re-run
                     # still carried +HK$100.6bn net debt.
                     "short_term_investments",
                     # The non-recurring term of the owner-earnings identity,
                     # read by the cash-conversion gate. Same trap the SBC
                     # note below records: _extract_annual_series() reads it,
                     # so omitting it here yields None on every row and the
                     # gate silently falls back to its weaker basis.
                     "change_in_working_capital",
                     # Bank-specific (Tier 2)
                     "interest_income", "provision_for_loan_losses",
                     "goodwill", "intangible_assets",
                     "tangible_book_value_per_share",     # FMP-direct TBV (fixes JPM over-strip bug)
                     "total_liabilities",
                     "operating_expense",
                     # Deposit / loan book + the customer-balance proxy lines
                     # for _is_balance_sheet_financial. The first three were
                     # already read by _extract_annual_series but never
                     # requested, so they were None on every FMP row — a KNOWN
                     # GAP recorded in
                     # tests/test_line_items_requested_are_read.py rather than
                     # fixed, pending exactly this decision. Requesting them
                     # changes bank inputs, which is why the test file logged
                     # it instead of silently closing it.
                     "total_deposits", "loans_receivable",
                     "loans_held_for_investment",
                     "accounts_payable", "accounts_receivable",
                     "other_payables", "other_current_liabilities",
                     # Tech/Payment-processor methods
                     "gross_profit", "cost_of_revenue",
                     # The operating side of the deterministic ROIC denominator
                     # floor (src/data/deterministic_kpis.py): operating working
                     # capital = receivables + inventory − payables, plus net
                     # PP&E. The receivables and payables are already requested
                     # above for the balance-sheet-financial gate; these two were
                     # not requested anywhere in this call, so a row built here
                     # had no operating floor to fall back on and every
                     # buyback-heavy name with capital ≤ 0 produced a negative or
                     # astronomical ROIC. Both are copied into rows by
                     # _extract_annual_series and classified in
                     # _FX_MONETARY_FIELDS — all three acts, or the field is
                     # silently None.
                     "inventory", "property_plant_equipment",
                     # Buyback-netted SBC (fcf_owner_earnings) + SBC Dilution
                     # Override — was previously requested nowhere in this
                     # call despite _extract_annual_series() already trying
                     # to read it off every row, so share_buyback silently
                     # came back None/0 for every ticker until this fix.
                     "share_buyback"],
                    end_date,
                    period="annual",
                    limit=7,
                    api_key=api_key,
                )
                _line_items_exc = None
                break
            except Exception as _exc:
                _line_items_exc = _exc
                if _attempt < _LINE_ITEMS_MAX_RETRIES:
                    _delay = _LINE_ITEMS_RETRY_BASE_DELAY * (_attempt + 1)
                    progress.update_status(
                        agent_id, ticker,
                        f"Line items fetch failed (attempt {_attempt + 1}/{_LINE_ITEMS_MAX_RETRIES + 1}) "
                        f"— retrying in {_delay:.1f}s"
                    )
                    time.sleep(_delay)

        if _line_items_exc is not None:
            progress.update_status(agent_id, ticker, "Failed to fetch line items — skipping")
            # Diagnostic — surface the bail reason in state so post-hoc
            # forensics can identify which early-exit fired without
            # needing live progress logs (which aren't persisted).
            state["data"].setdefault("dcf_skip_reasons", {})[ticker] = (
                f"line_items_fetch_failed: {type(_line_items_exc).__name__}: "
                f"{str(_line_items_exc)[:120]} (after {_LINE_ITEMS_MAX_RETRIES + 1} attempts)"
            )
            dcf_range[ticker] = {}
            continue

        series, reported_currency = _extract_annual_series(line_items)
        if len(series) < _MIN_HISTORY_YEARS:
            progress.update_status(agent_id, ticker,
                                   f"Insufficient history ({len(series)} yr) — skipping")
            state["data"].setdefault("dcf_skip_reasons", {})[ticker] = (
                f"insufficient_history: only_{len(series)}_years_min_{_MIN_HISTORY_YEARS}"
            )
            dcf_range[ticker] = {}
            continue

        # ── Deterministic KPIs: computed here, applied once the profile is known ──
        # Computed from the annual row AS FILED, before the quarterly balance-
        # sheet refresh below. That refresh moves cash, short-term investments,
        # total debt and net debt to the latest quarter but leaves equity,
        # receivables, inventory, payables and PP&E at the year end — and ROIC's
        # denominator is assembled from both halves, so a post-refresh capital
        # base would be two filing periods stitched together. Flows are annual
        # either way.
        #
        # Application is deferred to just before `attach_overrides` because
        # `profile_name` is not bound until ~L7330, and which KPIs may be
        # overridden is a property of the profile (its `extractor_only: False`
        # set), not of the arithmetic.
        _det_kpis: dict = {}
        _det_basis: dict = {}
        try:
            from src.data.deterministic_kpis import (
                compute_from_series as _compute_det_kpis,
            )
            _det_kpis, _det_basis = _compute_det_kpis(series)
        except Exception as _det_exc:  # never let a KPI cross-check break a valuation
            _det_basis = {"error": f"{type(_det_exc).__name__}: {_det_exc}"}

        # ── Anchor values from most recent year ──────────────────────────
        most_recent = series[-1]
        revenue_base = most_recent["revenue"]
        shares       = most_recent["shares_outstanding"]
        leverage     = most_recent["debt_to_equity"] or 0.0
        # The EV bridge prices today's balance sheet, not the one that happened
        # to sit at the fiscal year end. Flows stay annual.
        _bs_flag = _refresh_balance_sheet_from_latest_quarter(
            ticker, most_recent, end_date, api_key, sector)
        if _bs_flag:
            print(f"  [balance-sheet] {ticker}: {_bs_flag}")
        net_debt     = _net_debt_net_of_investments(most_recent, sector)

        # ── Spot + 52w + moving averages — FMP /stable/quote (PRIMARY) ────
        # Strict superset of quote-short. One call returns:
        #   {symbol, name, price, dayLow, dayHigh, yearHigh, yearLow,
        #    marketCap, priceAvg50, priceAvg200, exchange, open,
        #    previousClose, timestamp, change, changePercentage, volume}
        # Same API cost as quote-short, fills more fields downstream agents
        # can use (52w high for risk framing, 50/200-day MAs for momentum,
        # marketCap as cross-check for our _close × shares derivation).
        # Calendar-safe — returns last-trade price even on non-trading days.
        _spot_price:    float | None = None
        _year_high:     float | None = None
        _year_low:      float | None = None
        _price_avg_50:  float | None = None
        _price_avg_200: float | None = None
        _quote_mcap:    float | None = None
        if not (_is_hk_ticker(ticker) or _is_sg_ticker(ticker)):
            try:
                from src.tools.api import _fmp_get as _fmp_get_quote, _STABLE as _FMP_STABLE
                _quote = _fmp_get_quote(
                    f"{_FMP_STABLE}/quote", {"symbol": ticker}, api_key,
                )
                if _quote and isinstance(_quote, list) and _quote:
                    _q = _quote[0]
                    def _safe_float(v):
                        try:
                            f = float(v)
                            return f if f > 0 else None
                        except (TypeError, ValueError):
                            return None
                    _spot_price    = _safe_float(_q.get("price"))
                    _year_high     = _safe_float(_q.get("yearHigh"))
                    _year_low      = _safe_float(_q.get("yearLow"))
                    _price_avg_50  = _safe_float(_q.get("priceAvg50"))
                    _price_avg_200 = _safe_float(_q.get("priceAvg200"))
                    _quote_mcap    = _safe_float(_q.get("marketCap"))
                    # Stash on most_recent for downstream (risk panel, narrative)
                    if _year_high  is not None: most_recent["year_high_52w"]  = _year_high
                    if _year_low   is not None: most_recent["year_low_52w"]   = _year_low
                    if _price_avg_50  is not None: most_recent["price_avg_50"]  = _price_avg_50
                    if _price_avg_200 is not None: most_recent["price_avg_200"] = _price_avg_200
            except Exception:
                pass

        # ── Wall Street consensus 12m PT (FMP /stable/price-target-consensus) ─
        # Surfaces analyst consensus alongside our model PT so the frontend can
        # display "model $289 vs consensus $413 (-30%)" as a sanity flag without
        # LLM extraction from research text. None for HK/SG tickers (FMP n/a).
        _consensus_pt: dict | None = None
        try:
            _consensus_pt = get_price_target_consensus(ticker, api_key=api_key)
        except Exception:
            _consensus_pt = None

        # ── Historical EOD (always fires) — _trailing_pe + fallbacks ──────
        # _trailing_pe anchors to end_date (financials snapshot date), so it
        # always uses the historical-price-eod path with a 7-day fallback
        # window for non-trading-day end_date.
        # _market_cap PRIMARY came from quote.marketCap above; this block is
        # a fallback to _close × shares if quote didn't return it.
        # _spot_price PRIMARY came from quote.price above; this is also a
        # fallback for the rare cases where quote was skipped/empty.
        _trailing_pe: float | None = None
        _market_cap:  float | None = _quote_mcap
        try:
            _latest_prices = get_prices(ticker, end_date, end_date, api_key=api_key)
            if not _latest_prices:
                from datetime import datetime as _dt, timedelta as _td
                try:
                    _w_start = (_dt.strptime(end_date, "%Y-%m-%d") - _td(days=7)).strftime("%Y-%m-%d")
                    _latest_prices = get_prices(ticker, _w_start, end_date, api_key=api_key)
                except Exception:
                    pass
            if _latest_prices:
                _p = _latest_prices[-1]
                _close = float(_p.close) if hasattr(_p, "close") else float(_p.get("close", 0))
                _ni = most_recent.get("net_income")
                if _market_cap is None and _close > 0 and shares and shares > 0:
                    _market_cap = _close * shares
                if _close > 0 and _ni and shares and shares > 0 and _ni > 0:
                    _trailing_pe = _close / (_ni / shares)
                    most_recent["price_to_earnings_ratio"] = _trailing_pe
                if _spot_price is None and _close > 0:
                    _spot_price = _close
        except Exception:
            pass

        if not shares or shares <= 0:
            if len(series) >= 2 and series[-2]["shares_outstanding"]:
                shares = series[-2]["shares_outstanding"]
            else:
                progress.update_status(agent_id, ticker, "No shares data — skipping")
                state["data"].setdefault("dcf_skip_reasons", {})[ticker] = (
                    f"no_shares_data: most_recent={most_recent.get('shares_outstanding')}, "
                    f"series_len={len(series)}"
                )
                dcf_range[ticker] = {}
                continue

        # ── Shares cross-check vs live quote (D6 data-robustness fix) ──────
        # FMP line-item shares_outstanding occasionally diverges grossly from
        # reality (discovered on CRWD: line items reported 1.0325e9 shares vs
        # ~2.55e8 implied by quote marketCap/price — every per-share value
        # came out ~4x low). When the /stable/quote marketCap+price imply a
        # share count diverging >25% from the line items, trust the quote:
        # marketCap/price is market-observed and cannot carry unit errors.
        # Skipped automatically for HK/SG tickers (no quote fetched above).
        _shares_source = "line_items"
        if (
            _quote_mcap and _spot_price and _spot_price > 0
            and shares and shares > 0
        ):
            _shares_implied = _quote_mcap / _spot_price
            _shares_div = abs(_shares_implied - shares) / shares
            if _shares_div > 0.25 and _shares_implied > 0:
                _log.warning(
                    "dcf_agent[%s]: shares cross-check — line items %s diverge "
                    "%.0f%% from quote-implied %s (mcap %s / price %s); "
                    "using quote-implied shares",
                    ticker, f"{shares:,.0f}", _shares_div * 100,
                    f"{_shares_implied:,.0f}", f"{_quote_mcap:,.0f}",
                    f"{_spot_price:,.2f}",
                )
                shares = _shares_implied
                _shares_source = "quote_cross_check"
            elif _shares_implied > 0:
                # Recency (owner decision, 2026-09-20). Below the 25% band the
                # line-item count was kept, and the line item is the trailing
                # WEIGHTED AVERAGE diluted count from the last annual filing --
                # an average over a year that has already ended. For a company
                # retiring stock it is stale by construction: Valero divided by
                # 309.0mn against 287.9mn actually outstanding (+7.3%), Marathon
                # by 305.0mn against 291.9mn (+4.5%) while retiring 16.9% a
                # year. Every per-share value came out low by that much, for no
                # reason other than the age of the divisor.
                #
                # Market cap / price is the count the company has today, but it
                # is BASIC, so taking it alone would quietly drop dilution and
                # flatter every heavy issuer of stock comp. The filing's own
                # diluted/basic ratio is carried across instead, so the count is
                # current AND still diluted. The ratio is bounded: above 1.30 it
                # is not dilution, it is a mismatched pair of figures, and the
                # trailing count is the safer answer.
                _basic = most_recent.get("shares_outstanding_basic")
                _dilution = None
                if _basic and _basic > 0 and shares and shares > 0:
                    _r = shares / _basic
                    if 1.0 <= _r <= 1.30:
                        _dilution = _r
                if _dilution is not None:
                    shares = _shares_implied * _dilution
                    _shares_source = "quote_current_diluted"
                else:
                    shares = _shares_implied
                    _shares_source = "quote_current_basic"

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
        _FX_MONETARY = _FX_MONETARY_FIELDS
        # For HK-listed tickers (prices quoted in HKD), convert financials directly
        # into HKD so that all per-share outputs are already in HKD.
        # This eliminates the two-step CNY→USD→HKD chain (and its rounding noise).
        # The USD→HKD tail conversion further below is skipped for HK tickers.
        _is_hk = _is_hk_ticker(ticker)
        # Market is a dimension, not a boolean: without this an SGX name is
        # discounted at US rates with no country premium.
        _is_sg = _is_sg_ticker(ticker)
        _is_sg = _is_sg_ticker(ticker)
        # Value each name in the currency it TRADES in, so intrinsic value
        # and spot are directly comparable. HK already did this; SG did not,
        # and was converting SGD financials to USD while the spot price it
        # was compared against stayed in SGD — understating every SGX
        # intrinsic value by roughly the SGD/USD rate (~22%). The bug was
        # latent while SG had only a single yfinance TTM snapshot; it bites
        # now that SG carries real statement history.
        # The VENUE does not fix the quote currency. SGX lists Jardine
        # Matheson, Hongkong Land, DFI Retail, HPH Trust and the US-asset
        # REITs in USD, so assuming SGD converted their financials up by the
        # SGD/USD rate while the spot price they were measured against stayed
        # in USD -- a silent ~27% overstatement on some of the largest names
        # on the exchange. Ask what the line actually trades in.
        _venue_ccy = "HKD" if _is_hk else ("SGD" if _is_sg else "USD")
        _target_ccy = _venue_ccy
        if _is_hk or _is_sg:
            try:
                from src.tools.api import get_listing_currency
                _target_ccy = get_listing_currency(ticker, api_key) or _venue_ccy
            except Exception:                              # noqa: BLE001
                _target_ccy = _venue_ccy
            if _target_ccy != _venue_ccy:
                print(f"  [fx] {ticker}: quoted in {_target_ccy}, not the "
                      f"{_venue_ccy} venue default — valuing in {_target_ccy}")

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
                net_debt     = _net_debt_net_of_investments(most_recent, sector)
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

        # ── The one size every peer lookup is allowed to use ───────────────
        #
        # `market_cap` is what unlocks the size-matched "large" cohort rungs in
        # `get_regional_multiples`, and five call sites need it: the three
        # `_compute_method_value` legs, the growth-premium benchmark they are
        # scaled by, and the 12m-target peer set whose resolution is published
        # in `multiples_used`. Five copies of one expression is five chances to
        # edit one of them, and that is exactly what happened — the benchmark
        # passed nothing and the 12m call passed `or 0.0`, so both silently
        # resolved whole-grouping medians while the legs resolved a size-matched
        # set. Binding it ONCE makes the divergence structurally impossible
        # rather than merely tested for.
        #
        # Bound here, after the FX block re-derives `revenue_base` at the
        # conversion above and after `_market_cap` is finalised from the quote,
        # so it is downstream of the last assignment to both of its inputs.
        #
        # The `revenue_base * 10` proxy is the legs' own: when the quote carries
        # no market cap they still resolved a size, and a different fallback
        # anywhere would reopen the gap. The guard is the part that was missing
        # — `revenue_base` is `most_recent["revenue"]` and can be None, so the
        # bare expression raised `TypeError: unsupported operand type(s) for *:
        # 'NoneType' and 'int'` on any name with no revenue line and no quote
        # cap. None is the honest answer there: with no size information the
        # caller gets whole-grouping medians and `_comp_basis` says so, instead
        # of a fabricated cap inventing a `large` rung that was never earned.
        resolved_mcap: float | None = (
            _market_cap or (revenue_base * 10.0 if revenue_base else None)
        )

        # Task #25 — HK blends run in HKD but the analyst SOTP assumptions
        # are USD-based. Tell the SOTP dispatcher which currency the blend
        # actually carries so its per-share leg is converted to match:
        # USD→HKD when the conversion above succeeded, else USD→reporting
        # currency (blend stayed unconverted). No-op for non-HK tickers
        # (their blend is USD, same as the assumptions).
        if _is_hk and not most_recent.get("_sotp_usd_reporting_fx"):
            _blend_ccy = (
                _target_ccy if (fx_rate != 1.0 and fx_rate > 0)
                else reported_currency
            )
            if _blend_ccy != "USD":
                try:
                    _usd_blend_fx = get_fx_rate("USD", _blend_ccy, api_key)
                    if _usd_blend_fx and _usd_blend_fx > 0 and _usd_blend_fx != 1.0:
                        most_recent["_sotp_usd_reporting_fx"] = _usd_blend_fx
                except Exception:
                    pass

        # ── Ticker-level forward flags (seed; all subsequent blocks append) ──
        ticker_forward_flags: list[str] = []
        if _bs_flag:
            ticker_forward_flags.append(_bs_flag)
        # Forward-test substrate. A gate holds BOTH values at the moment it
        # fires, but the flag records them only as prose, which cannot be
        # scored. These structured pairs are what the reconciliation worker
        # reads to compute (|B - actual| - |A - actual|) / |actual| once the
        # period reports. Cheap now and impossible retroactively: a snapshot
        # written without them can never be scored.
        gate_evaluations: list[dict] = []

        # ── Quarterly balance-sheet step change → the run's audit payload ────
        # Computed inside `_refresh_balance_sheet_from_latest_quarter`, which is
        # the only place both readings are in hand, and lifted here because that
        # function runs ~340 lines before this list exists and returns prose.
        # `.pop` keeps the stashed key off the row: `most_recent` IS
        # `series[-1]`, it is serialized into the payload downstream, and an
        # internal hand-off key has no business in a published run.
        _bs_step = most_recent.pop("_balance_sheet_step_change", None)
        if isinstance(_bs_step, dict):
            # `.get` with a default rather than `[...]`: the record is built and
            # consumed in one process, so a missing key would mean a code path
            # that never wrote it, and the fallback is the strict threshold
            # rather than a KeyError inside a disclosure-only gate.
            _th_used = _bs_step.get(
                "threshold_used", _BALANCE_SHEET_STEP_CHANGE_THRESHOLD)
            _gap = _bs_step.get("period_delta_days")
            _gap_clause = (
                f"the period gap was unparseable, so the stricter "
                f"{_BALANCE_SHEET_STEP_CHANGE_THRESHOLD:.0%} threshold was used "
                f"as the fallback" if _gap is None else
                f"a {_gap}-day gap between the two readings"
            )
            gate_evaluations.append({
                "gate_id": "GATE_BALANCE_SHEET_QUARTERLY_STEP_CHANGE",
                "metric": "balance_sheet_quarterly_step_change",
                # Path A is the year end the engine would otherwise have priced;
                # path B is the quarter it priced instead. Nothing here chose
                # between them -- the overlay applies unconditionally -- which is
                # exactly why `applied` is False: the pair is recorded so a
                # post-mortem (or the forward scorer, once the next annual
                # reports) can see which reading was closer, not so this run can
                # act on it. Both are net CASH in the statement's source
                # currency, pre-FX.
                "raw_input_path_a": round(float(_bs_step["annual_net_cash"]), 2),
                "gated_output_path_b": round(float(_bs_step["quarterly_net_cash"]), 2),
                "flag": _bs_step["flag"],
                "annual_net_cash": _bs_step["annual_net_cash"],
                "quarterly_net_cash": _bs_step["quarterly_net_cash"],
                "delta_ratio": _bs_step["delta_ratio"],
                "source": _bs_step["source"],
                "action": _bs_step["action"],
                # The seventh key, and the one that was dropped. Shipped in
                # `2f386f0` at the stash only: the `.pop` above then destroyed it
                # and no payload ever carried it, which a production read of
                # 02020.HK's gate record is what found. Both net-cash figures here
                # are pre-FX, so without this a reader comparing them to a
                # reported-currency disclosure is off by the FX rate with nothing
                # on the payload to say so -- worst on the HK names, where the
                # statement currency (CNY) is not the quote currency (HKD).
                # `test_every_stashed_key_is_lifted` now pins the hand-off itself
                # instead of a hand-written list of the keys that survived it.
                "currency_basis": _bs_step["currency_basis"],
                # Carried onto the payload, not left on the popped record:
                # without them the published `delta_ratio` does not say which
                # threshold it was measured against, and a reader cannot tell a
                # 0.46 that fired at 0.25 from a 0.46 that was dropped at 0.50.
                "period_delta_days": _gap,
                "threshold_used": _th_used,
                "basis": (
                    f"The quarterly overlay replaced the "
                    f"{most_recent.get('period')} year-end balance sheet with "
                    f"{most_recent.get('_balance_sheet_period')}, moving net "
                    f"cash by {_bs_step['delta_ratio']:.2%} of the annual "
                    f"figure over {_gap_clause} — above the "
                    f"{_th_used:.0%} structural "
                    f"step-change threshold, so the move is worth explaining "
                    f"rather than burying in a prose flag. The quarterly figure "
                    f"is applied regardless: it is the balance sheet that "
                    f"exists, and reverting to a stale year end would price "
                    f"every EV-based method on a position nobody reports any "
                    f"more. Figures are net cash (not net debt) in the source "
                    f"currency, before the downstream FX conversion."
                ),
                "applied": False,
            })
            ticker_forward_flags.append(
                f"Balance-sheet step change: net cash "
                f"{_bs_step['annual_net_cash'] / 1e9:,.1f}bn at the "
                f"{most_recent.get('period')} year end → "
                f"{_bs_step['quarterly_net_cash'] / 1e9:,.1f}bn at "
                f"{most_recent.get('_balance_sheet_period')} "
                f"({_bs_step['delta_ratio']:+.0%}, source ccy pre-FX) — "
                f"quarterly override APPLIED, disclosure only, no gate fired")

        # ── Normalized (cycle-adjusted) earnings for P/E (norm), EV/EBITDA (norm) ──
        # Damodaran-style: mean(field / revenue) over last 5 yrs × current revenue.
        # For cyclicals this prevents peak-year P/E multiples from producing a
        # trough-earnings IV (and vice versa). For stable businesses the delta
        # is small — safe to apply uniformly. Stored on most_recent so the method
        # branches pick them up automatically; None when insufficient history.
        _norm_ni     = _normalized_earnings(series, "net_income", window=5)
        _norm_ebitda = _normalized_earnings(series, "ebitda",     window=5)
        _norm_ebit   = _normalized_earnings(series, "ebit",       window=5)
        # Owner decision 2026-09-20: a cyclical profile normalises its EARNINGS
        # legs and then capitalised raw TTM cash flow in the FCF leg, so the
        # blend was two-thirds mean-reverted and one-fifth whatever the last
        # twelve months happened to be. At the bottom of a cycle that is a
        # trough number carrying 20% of the weight: Phillips 66's FCF leg
        # priced $64.31 against a $273.13 quote on $2.73bn of TTM owner
        # earnings, while its own five-year median is $4.16bn.
        _norm_fcf = (_normalized_earnings(series, "fcf_owner_earnings", window=5)
                     or _normalized_earnings(series, "free_cash_flow", window=5))
        most_recent["normalized_net_income"] = _norm_ni
        most_recent["normalized_ebitda"]     = _norm_ebitda
        most_recent["normalized_fcf_owner_earnings"] = _norm_fcf
        # Review-gated industry input (Wave 1): an owner-accepted maintenance
        # capex replaces the D&A stand-in in the Distributable CF Yield leg. In
        # the currency the statements are now in -- the listing currency when
        # the FX block above converted them, the statement currency otherwise.
        try:
            from src.data import industry_inputs as _ii
            _mc_ccy = (_target_ccy if (reported_currency != _target_ccy and fx_rate > 0
                                       and fx_rate != 1.0) else reported_currency)
            most_recent["_values_currency"] = _mc_ccy
            _mc_d = _ii.accepted_detail(ticker, "maintenance_capex", _mc_ccy)
            if _mc_d and _mc_d.get("value") is not None:
                most_recent["maintenance_capex_accepted"] = _mc_d["value"]
                most_recent["_maintenance_capex_detail"] = _mc_d
                if _mc_d.get("overlay_applied"):
                    ticker_forward_flags.append(
                        f"Maintenance capex: forward overlay applied — "
                        f"{_mc_d['baseline'] / 1e6:,.0f}m actual ({_mc_d.get('period')}) "
                        f"{_mc_d.get('delta_pct', 0):+.1%} guidance = "
                        f"{_mc_d['value'] / 1e6:,.0f}m")
            # Owner-recorded structural regime deviation (valuation_constants
            # `regime_deviations`): a note, never a number. Appended HERE, before
            # the scenario loop, because `ticker_forward_flags` is snapshotted into
            # each scenario's flags -- a later append reaches no payload.
            try:
                from src.data import valuation_constants as _vc_reg
                _regime = _vc_reg.regime_deviation(ticker)
                if _regime:
                    ticker_forward_flags.append(f"{_regime.get('label')}: {_regime.get('note')}")
            except Exception:                              # noqa: BLE001
                pass
            # Wave 2: an owner-accepted regulated rate base, with the rate
            # order's allowed ROE and equity layer, feeds the P/Rate Base leg.
            _rb_d = _ii.accepted_detail(ticker, "rate_base", _mc_ccy)
            if _rb_d and _rb_d.get("value") is not None:
                most_recent["rate_base_accepted"] = _rb_d["value"]
                most_recent["_rate_base_detail"] = _rb_d
        except Exception:                                  # noqa: BLE001
            pass
        most_recent["normalized_ebit"]       = _norm_ebit
        # Audit flag when normalization materially moves earnings (>15% delta)
        _cur_ni = most_recent.get("net_income")
        if _norm_ni is not None and _cur_ni and _cur_ni > 0:
            _delta_pct = (_norm_ni - _cur_ni) / _cur_ni
            if abs(_delta_pct) > 0.15:
                ticker_forward_flags.append(
                    f"Normalized NI: TTM {_CCY_SYMBOLS.get((reported_currency or 'USD').upper(), (reported_currency or 'USD').upper() + ' ')}{_cur_ni/1e9:.2f}B → 5y-cycle "
                    f"{_CCY_SYMBOLS.get((reported_currency or 'USD').upper(), (reported_currency or 'USD').upper() + ' ')}{_norm_ni/1e9:.2f}B ({_delta_pct:+.0%}) — "
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
        # FMP's product segmentation returns [] for HKEX and SGX by design, so
        # every Asian name arrived here with no segment map at all. The filing
        # note is the authoritative disclosure anyway (IFRS 8 / ASC 280) and
        # the extractor built earlier in this arc already parses it.
        #
        # NOTE what this feeds: _sotp_enterprise_value applies a REVENUE
        # multiple per segment, keyed off the segment's name. The per-segment
        # PROFIT this parser also returns is not consumed by that path, so a
        # loss-making division is still valued on its top line. That is the
        # next piece of work, not this one.
        #
        # Energy names take the filing note in PREFERENCE to FMP, not merely as
        # a fallback (owner, 2026-09-20). FMP returned Phillips 66 a PRODUCT cut
        # -- "Consolidation, Eliminations" $55.8bn, "Natural Gas Liquids",
        # "Crude Oil" -- while the 10-K note carries the business segments the
        # multiples are actually set for: Midstream, Chemicals, Refining, M&S,
        # Renewable Fuels. A product line is not a business, and the SOTP is
        # priced per business.
        # Keyed off the FMP INDUSTRY, not `sector`. The sector in scope here is
        # the pre-routing one the pipeline seeds, and it is a placeholder: it
        # reads "Tech" for Phillips 66, so a sector test silently never fired.
        # The industry comes from the classification cache the comps path
        # already populates, and the family is the same one the peer baskets
        # pool on -- which is exactly the set the segment multiples are written
        # for.
        try:
            from src.data.regional_comps import family_of as _family_of
            _seg_prefers_note = (
                _family_of(_company_industry(ticker)) == "Oil, Gas & Coal (family)")
        except Exception:                                  # noqa: BLE001
            _seg_prefers_note = False
        if _seg_prefers_note:
            try:
                from src.tools.segment_providers import get_segment_footnote
                _fn_e = get_segment_footnote(ticker, end_date)
                # A zero-revenue row is kept when it discloses assets: an
                # equity-accounted segment consolidates no revenue but is still
                # part of the company (Phillips 66's CPChem JV), and dropping it
                # here is what made it invisible to the SOTP.
                _rows_e = [r for r in ((_fn_e or {}).get("segments") or [])
                           if (isinstance(r.get("revenue"), (int, float)) and r["revenue"] > 0)
                           or (isinstance(r.get("assets"), (int, float)) and r["assets"] > 0)]
                if len([r for r in _rows_e if (r.get("revenue") or 0) > 0]) >= 2:
                    product_segments = [{
                        "period_end": (_fn_e.get("period_end") or end_date),
                        "segments": {r["name"]: float(r.get("revenue") or 0.0) for r in _rows_e},
                    }]
                    most_recent["segment_members"] = {
                        r["name"]: str(r.get("member") or "") for r in _rows_e}
                    most_recent["segment_assets"] = {
                        r["name"]: float(r["assets"]) for r in ((_fn_e or {}).get("segments") or [])
                        if isinstance(r.get("assets"), (int, float)) and r["assets"] > 0}
                    _log.info("[dcf] %s: segment map from the filing note, preferred "
                              "over FMP product lines (%d segments)", ticker, len(_rows_e))
            except Exception:                              # noqa: BLE001
                pass
        if not product_segments and ticker in _SEGMENT_SOTP_TICKERS:
            try:
                from src.tools.segment_providers import get_segment_footnote
                _fn = get_segment_footnote(ticker, end_date)
                _rows = [r for r in ((_fn or {}).get("segments") or [])
                         if isinstance(r.get("revenue"), (int, float))
                         and r["revenue"] > 0]
                if len(_rows) >= 2:
                    product_segments = [{
                        "period_end": (_fn.get("period_end") or end_date),
                        "segments": {r["name"]: float(r["revenue"]) for r in _rows},
                    }]
                    _log.info("[dcf] %s: segment map from the filing note "
                              "(%d segments)", ticker, len(_rows))
            except Exception:                              # noqa: BLE001
                pass
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

        # The analyst SOTP flexes by these trees per scenario, and names valued
        # on a published SOTP often have no product segmentation, so the trees
        # are attached here too rather than only inside the segments branch.
        if segment_scenarios_all.get(ticker) and not most_recent.get("segment_scenarios"):
            most_recent["segment_scenarios"] = segment_scenarios_all[ticker]

        # ── Attach GS-style SOTP assumptions (feeds "SOTP (analyst)") ───────
        # Assumptions are USD-denominated; resolve USD→target-currency FX so
        # the per-share IV lands in the listing currency like every other
        # method. Shadow-only today; blend promotion is a later phase.
        _ticker_sotp = sotp_assumptions_all.get(ticker)
        if _ticker_sotp:
            if not _ticker_sotp.get("fx_usd_to_reporting"):
                _ticker_sotp = dict(_ticker_sotp)
                _usd_fx = get_fx_rate("USD", _target_ccy, api_key)
                _ticker_sotp["fx_usd_to_reporting"] = _usd_fx if _usd_fx and _usd_fx > 0 else 1.0
            _ticker_sotp, _net_cash_flag = _refresh_sotp_net_cash(
                _ticker_sotp, net_debt)
            if _net_cash_flag:
                ticker_forward_flags.append(_net_cash_flag)
            _ticker_sotp, _holdco_flag = _pin_sotp_holdco_discount(
                ticker, _ticker_sotp)
            if _holdco_flag:
                ticker_forward_flags.append(_holdco_flag)
            _ticker_sotp, _sotp_gate_flag = _gate_live_sotp(
                ticker, _ticker_sotp, shares, net_debt)
            if _sotp_gate_flag:
                ticker_forward_flags.append(_sotp_gate_flag)
            _ticker_sotp, _seg_mem_flag = _apply_accepted_segment_memory(
                ticker, _ticker_sotp, end_date, reported_currency, api_key)
            if _seg_mem_flag:
                ticker_forward_flags.append(_seg_mem_flag)
            most_recent["sotp_assumptions"] = _ticker_sotp
            _segs = _ticker_sotp.get("segments") or []
            ticker_forward_flags.append(
                f"SOTP (analyst): {len(_segs)} segments, "
                f"holdco {_ticker_sotp.get('holdco_discount_pct', 0):.0%}, "
                f"sources={_ticker_sotp.get('_sources', 'unknown')}"
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

        # ── (Tier 2 REIT/Bank/SaaS research attachment moved to after ──────
        #     profile_name is finalized, approx line 3258. Previously here
        #     but triggered UnboundLocalError on profile_name for non-REIT
        #     tickers because profile_name is only assigned at line ~3228.)

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
            _oe_basis_label = "owner-earnings"
            _oe_basis_field = "fcf_owner_earnings"
            _drag_bps = int(round((fcf_margin_reported - fcf_margin_owner) * 10000))
            if _drag_bps > 0:
                ticker_forward_flags.append(
                    f"SBC drag: FCF margin {fcf_margin_reported:.1%} → "
                    f"{fcf_margin_owner:.1%} (−{_drag_bps} bps, "
                    f"{_sbc_years}/5 yr SBC data)"
                )
        else:
            fcf_margin_base = fcf_margin_reported
            _oe_basis_label = "reported-FCF"
            _oe_basis_field = "free_cash_flow"

        # ── Structural turnaround: the window mean describes a company that no
        #     longer exists (see `_turnaround_margin`). On the basis just chosen.
        _turn = _turnaround_margin(series, field=_oe_basis_field)
        if _turn:
            _turn_pre = fcf_margin_base
            fcf_margin_base = _turn["recent_two_year"]
            ticker_forward_flags.append(
                f"Structural turnaround: FCF margin improved every year of the window from "
                f"{_turn['margins'][0]:+.1%} to {_turn['margins'][-1]:+.1%}, so its {_turn_pre:.1%} mean "
                f"describes the company it was. Base margin is the last two audited years, "
                f"{fcf_margin_base:.1%}; management guidance is an overlay, never the base.")
            gate_evaluations.append({
                "gate_id": "GATE_MARGIN_TURNAROUND",
                "metric": "fcf_margin_base",
                "raw_input_path_a": round(float(_turn_pre), 6),
                "gated_output_path_b": round(float(fcf_margin_base), 6),
                "basis": (f"{_oe_basis_label} margins {_turn['margins']}: window opens cash-burning, "
                          f"improves every year, latest clears the mean by "
                          f">= {_TURNAROUND_MIN_GAP:.0%}; base = mean of the last two years"),
                "applied": True,
            })

        # ── SW50 cascade: owner-earnings basis ≤ 0 (task #18) ─────────────
        # Reference: src/research_ideas/sw46/iv15.py::_resolve_base_oe.
        # A non-positive trailing owner-earnings margin used to flow into
        # _project_dcf, where the per-year FCF floor (FCF_MARGIN_FLOOR,
        # typically −5%) converted it into a small positive IV that was
        # really only discounted net cash — yet it still sat in the blend
        # at full profile weight and dragged the IV toward zero (CRWD
        # 2026-08: baseline IV $13.69 vs multiples ~$26). Instead of
        # letting the floor manufacture a fake anchor:
        #   1. If the chosen basis field has ANY positive-margin years in
        #      the 5-year window, use their MEDIAN as the DCF basis (the
        #      business has demonstrated it can generate owner earnings;
        #      the median is robust to one-off blow-up years) — flagged.
        #   2. If it has none, disable every DCF-projection method (None
        #      → _blend_methods renormalizes onto the multiples bucket;
        #      the structural gate + methods_unavailable name the excluded
        #      methods) — flagged.
        # Profile classification below (get_valuation_profile) deliberately
        # keeps the PRE-cascade trailing margin — the profile choice must
        # reflect demonstrated economics, not the repaired DCF basis.
        _fcf_margin_for_classify = fcf_margin_base
        _dcf_family_disabled = False
        if fcf_margin_base <= 0:
            _pos_median = _median_positive_fcf_margin(series, field=_oe_basis_field)
            if _pos_median is not None:
                ticker_forward_flags.append(
                    f"OE≤0 cascade: trailing {_oe_basis_label} margin "
                    f"{fcf_margin_base:.1%} ≤ 0 → DCF basis = median of "
                    f"positive years {_pos_median:.1%}"
                )
                fcf_margin_base = _pos_median
            else:
                _dcf_family_disabled = True
                ticker_forward_flags.append(
                    f"OE≤0: no positive {_oe_basis_label} margin year in the "
                    f"5y window → DCF-family methods disabled, blend is "
                    f"multiples-only"
                )

        # ── Cash-conversion gate: reported FCF that is not earnings ───────
        # Runs AFTER the OE<=0 cascade so it caps whatever basis that settled
        # on, and is skipped when the DCF family is already disabled (there is
        # nothing left to project). Caps DOWNWARD only.
        #
        # Note the interaction with the SBC drag above: owner-earnings already
        # removes unfunded stock comp, which is a real cost. This gate removes
        # a different thing — a working-capital/float benefit that is not a
        # cost at all, just not repeatable. A name can legitimately trip both.
        if not _dcf_family_disabled:
            _cc_cap, _cc_trailing, _cc_basis = _projectable_fcf_margin_cap(
                series)
            _cc_deep_cut = (
                _cc_cap is not None
                and _cc_cap < _DEEP_CUT_OBSERVATION_FRACTION * fcf_margin_base
            )
            if (_cc_cap is not None and _cc_cap > 0
                    and fcf_margin_base > _cc_cap * _CASH_CONVERSION_TOLERANCE):
                _cc_ratio = (
                    (_cc_trailing / _cc_cap) if _cc_cap else float("inf")
                )
                ticker_forward_flags.append(
                    f"Cash-conversion (observed, not applied): FCF margin "
                    f"{fcf_margin_base:.1%} vs "
                    f"{_cc_cap:.1%} — reported FCF is {_cc_ratio:.1f}x what "
                    f"a repeatable basis supports ({_cc_basis}), so the "
                    f"excess is not projected"
                )
                gate_evaluations.append({
                    "gate_id": "GATE_CASH_CONVERSION",
                    "metric": "fcf_margin",
                    "raw_input_path_a": round(float(fcf_margin_base), 6),
                    "gated_output_path_b": round(float(_cc_cap), 6),
                    "basis": _cc_basis,
                    "deep_cut": bool(_cc_deep_cut),
                    "applied": False,
                })
                # OBSERVED, NOT APPLIED.
                #
                # Backtested over 329 ticker-dates across two independent
                # samples, the cap is validated on financials (10 helped, 1
                # false alarm, Beta 0.85, replicated) and reliably WRONG on
                # marketplaces (7 helped, 19 false alarms, Beta 0.29, also
                # replicated at 0.31 and 0.29).
                #
                # In production those populations are exactly inverted against
                # where it can act. Every financial resolves to a profile whose
                # blend is multiples-only — GGM, Residual Income, P/TBV,
                # P/E(norm), Excess Capital — so `weight_dcf` is 0.0 and the
                # cap changes nothing. Marketplaces carry 0.45 to 0.80. The
                # gate's effect is concentrated precisely where it is wrong and
                # absent precisely where it is right.
                #
                # It is also likely a compensating error. `_project_dcf` holds
                # the margin flat while revenue compounds, charging nothing for
                # the investment that growth requires, so capping the margin
                # makes the OUTPUT look sane by breaking an INPUT. That is why
                # it improves plausibility while degrading forecast accuracy.
                # The reinvestment charge is the real fix; until it lands this
                # records what it would have done and moves nothing.

        # ── Inventory stress: DSI expansion → markdown haircut (LIVE) ──────
        # Failure 4 of the operational post-mortem, and the one gate in this
        # file that APPLIES what it measures. A retailer whose Days Sales of
        # Inventory has expanded well past its own recent median has shipped
        # product into a channel that has stopped taking it. The margin still
        # on the books was earned on the units that sold at full price; the
        # units sitting in the channel get marked down, and that markdown is a
        # cost the trailing margin has not yet absorbed. Projecting the trailing
        # margin forward then projects a price the company will not get.
        #
        # Trigger and size are both owner-specified: expansion > 25 days over
        # the prior three years' median, 15% off the base margin.
        #
        # MULTIPLICATIVE, NOT 15 PERCENTAGE POINTS, and that reading is a
        # judgement worth flagging rather than burying. "A 15% markdown
        # haircut" admits both. On NKE's own archetype inputs — the case this
        # gate exists for — the base margin is ~11.4%, so a proportional
        # haircut takes it to ~9.7% while an absolute 15pp deduction takes it
        # to −3.6%, through the Consumer sector floor of +0.02, and hands the
        # projection a number that describes the floor rather than the company.
        # Measured across the 14 golden fixtures, an absolute 15pp deduction
        # would drive 8 of the 14 base margins negative outright (COST 0.0232,
        # MU 0.0396, FCX 0.0539, U96_SI 0.0716, 09988_HK and BABA 0.0951,
        # BN4_SI 0.0979, SCHW 0.1153). A proportional haircut cannot exceed the
        # base margin, cannot flip its sign and cannot reach the floor from
        # above — which is precisely the failure the reinvestment charge was
        # measured to have, where the deduction exceeded the base margin on
        # seven of fourteen names and the blend's leg-dropping turned the more
        # conservative input into a HIGHER valuation.
        #
        # Only a POSITIVE base margin is marked down. A negative one multiplied
        # by 0.85 moves TOWARD zero, so on MSTR's shape (−21.06) a "haircut"
        # would improve the margin by 3.2pp — the sign error is quiet and the
        # result is exactly backwards. There is also nothing to mark down: a
        # business that does not convert revenue to cash already has its
        # inventory problem dominated by a larger one.
        #
        # MEASURED, NOT ASSUMED, before this was wired. `_inventory_stress_days`
        # is computable on all 14 golden fixtures — 4 of the 5 recorded rows
        # carry a usable inventory/cost-of-revenue pair on every one — and the
        # trigger fires on NONE of them. Split 5 / 6 / 3:
        #
        #   five flat at exactly 0.0, inventory reported as zero in every year
        #        (02888_HK, C38U_SI, D05_SI, SCHW, V) — banks, a REIT and a
        #        payment network, where there is nothing on a shelf to mark down;
        #   six NEGATIVE, inventory days COMPRESSING (BN4_SI −74.3, MU −30.6,
        #        BABA −15.9, 09988_HK −15.8, COST −3.0, AAPL −1.3), which is the
        #        good direction and which a one-sided helper would have been
        #        unable to report;
        #   three positive and under the trigger (FCX +7.1, MELI +3.0,
        #        U96_SI +1.5), the largest at 28% of it.
        #
        # So the live blast radius on the recorded baseline is nil, and this
        # ships live rather than observation-only on the strength of that
        # measurement: the gate exists for an NKE-shaped name and costs nothing
        # on anything currently pinned. Re-measure with
        # `scratchpad/probe_inventory_dsi.py` (one subprocess per fixture) before
        # believing that again after a comps or fixture refresh.
        #
        # One thing the measurement surfaced that is NOT fixed here: 09988_HK
        # and BABA both report inventory of exactly 0.0 in the most recent year
        # against ~15.8 days in each of the three before it. That may be real —
        # a marketplace with a cloud business can genuinely hold almost no
        # stock — but a hard discontinuity to exactly zero is also what a
        # missing line item looks like when the provider fills it with 0 rather
        # than null, and `_safe(0.0)` and `_safe(None)` are indistinguishable
        # downstream. Either way the gate reads it as "inventory drained to
        # nothing" and stays silent, which is the safe direction. It is a data
        # question and not a gate question, so it is recorded rather than
        # patched around with a heuristic nobody chose.
        # The markdown premise is "units in the channel sell at a discount". It
        # does not hold for inventory built against CONTRACTED work: GE Vernova's
        # inventory days rose 30 while it built turbines for a $176bn order book,
        # and the gate took 15% off its margin as if they were unsold trainers.
        # Exempt when an owner-ACCEPTED backlog covers at least a year of revenue
        # -- a filing figure, review-gated like every other; a pending one exempts
        # nothing. Not a profile exemption: Capital Goods also holds dealer-channel
        # names (Caterpillar, Deere) for whom the gate is exactly right.
        _inv_backlog_cov = None
        try:
            from src.data import industry_inputs as _ii_inv
            _inv_bl = _ii_inv.accepted_detail(
                ticker, "backlog", most_recent.get("_values_currency") or reported_currency)
            if _inv_bl and _inv_bl.get("value") and revenue_base and revenue_base > 0:
                _inv_backlog_cov = _inv_bl["value"] / revenue_base
        except Exception:                                  # noqa: BLE001
            _inv_backlog_cov = None
        _inv_contracted = _inv_backlog_cov is not None and _inv_backlog_cov >= 1.0
        if not _dcf_family_disabled and fcf_margin_base > 0:
            _inv_days = _inventory_stress_days(series[::-1])
            # The exemption is a PRECONDITION of the one gate below, not a second
            # gate: still one site that turns inventory into days, still one
            # record, and its `applied` stays the literal its comment insists on.
            if (_inv_contracted and _inv_days is not None
                    and _inv_days > _INVENTORY_STRESS_TRIGGER_DAYS):
                ticker_forward_flags.append(
                    f"Inventory days {_inv_days:+.1f} over the prior 3y median, NOT marked down: "
                    f"accepted backlog covers {_inv_backlog_cov:.2f}x of revenue, so the build is "
                    f"work-in-process against contracted orders, not unsold channel stock.")
                _inv_days = None
            if _inv_days is not None and _inv_days > _INVENTORY_STRESS_TRIGGER_DAYS:
                _inv_haircut = fcf_margin_base * _INVENTORY_MARKDOWN_HAIRCUT
                _inv_pre = fcf_margin_base
                fcf_margin_base = _inv_pre - _inv_haircut
                ticker_forward_flags.append(
                    f"Inventory stress: DSI {_inv_days:+.1f} days over the "
                    f"prior 3y median (> {_INVENTORY_STRESS_TRIGGER_DAYS:.0f}), "
                    f"so a {_INVENTORY_MARKDOWN_HAIRCUT:.0%} markdown haircut "
                    f"takes the projected FCF margin {_inv_pre:.1%} → "
                    f"{fcf_margin_base:.1%}. The trailing margin was earned on "
                    f"units that sold at full price; the units in the channel "
                    f"are the cost it has not absorbed yet."
                )
                gate_evaluations.append({
                    "gate_id": "GATE_INVENTORY_STRESS",
                    "metric": "inventory_stress_days",
                    "raw_input_path_a": round(float(_inv_days), 4),
                    "gated_output_path_b": round(float(fcf_margin_base), 6),
                    "basis": (
                        f"DSI expansion {_inv_days:+.2f}d over the prior 3y "
                        f"median, trigger "
                        f"{_INVENTORY_STRESS_TRIGGER_DAYS:.0f}d; markdown "
                        f"{_INVENTORY_MARKDOWN_HAIRCUT:.0%} of the base margin "
                        f"{_inv_pre:.4f}"
                    ),
                    # A literal, not a derived value. Deriving `applied` from
                    # whether the inputs measured is what would conflate "could
                    # not compute this" with "chose not to act on it" — the
                    # distinction GATE_GROWTH_REINVESTMENT exists to make, and
                    # the one its own record got wrong while the reinvestment
                    # charge was wired live. Counted rather than assumed: five
                    # records in this file say True (CAGR divergence, balance
                    # sheet ×2, deterministic KPI precedence, this one) and
                    # three say False (cash conversion, growth reinvestment,
                    # cyclical peak consensus), and the two groups are the whole
                    # point — a reader can tell from the payload alone whether
                    # path B describes the number that was used or one that was
                    # not.
                    "applied": True,
                })

        # ── Analyst estimates (fetched eagerly — cached) ──────────────────
        # Pulled BEFORE the growth waterfall so dispersion bands are available
        # for scenario construction even when guidance or historical drives the
        # point estimate. Feeds both growth_base (analyst revenue_avg) AND the
        # bear/base/bull band scenarios + Forward P/E / Forward EV/EBITDA methods.
        # FMP returns analyst-estimates sorted DESCENDING by date and `limit`
        # truncates from the top. With limit=3 we got the FURTHEST 3 future
        # years (e.g. MDB: FY2029/30/31) and never saw the nearest forward
        # year (FY2027) which is what NTM growth/consensus actually needs.
        # Bumping to limit=10 captures the full annual horizon; the downstream
        # filter (period_end > end_date) + sort ASC in get_analyst_estimates
        # then makes estimates[0] the nearest forward year. Bug fixed 2026-04-25.
        try:
            estimates = get_analyst_estimates(
                ticker, end_date, period="annual", limit=10, api_key=api_key
            )
        except Exception:
            estimates = []

        # ── Growth rate — priority: guided > analyst > historical ────────
        data_source = "historical"
        guidance = mgmt_guidance_all.get(ticker, {})

        # R1: structured company guidance from primary-source extraction
        # (EDGAR press release + earnings call) takes Priority 0 over the
        # regex-parsed management_guidance; the regex value stays as the
        # fallback whenever the store has nothing usable.
        _r1_guid = _r1_structured_guidance(ticker, revenue_base=revenue_base)
        if _r1_guid:
            _r1_merge = {k: v for k, v in _r1_guid.items()
                         if not k.startswith("_r1")}
            if _r1_merge:
                guidance = {**guidance, **_r1_merge}
                _r1_bits = []
                if "revenue_guidance_mid" in _r1_merge and revenue_base \
                        and revenue_base > 0:
                    _r1_bits.append(
                        f"revenue → "
                        f"{(_r1_merge['revenue_guidance_mid'] / revenue_base) - 1.0:+.1%}")
                if "ebitda_guidance_mid" in _r1_merge:
                    _r1_bits.append(
                        f"Yr1 EBITDA "
                        f"{_r1_merge['ebitda_guidance_mid'] / 1e9:.2f}B")
                ticker_forward_flags.append(
                    "R1 structured guidance (Priority 0): "
                    + (", ".join(_r1_bits) or "guidance on file")
                    + f" [{_r1_guid.get('_r1_source') or 'edgar+transcript'}"
                    + (f", {_r1_guid['_r1_period']}"
                       if _r1_guid.get('_r1_period') else "")
                    + (f", as of {_r1_guid['_r1_as_of']}"
                       if _r1_guid.get('_r1_as_of') else "")
                    + "]")

        # R3: Assumption Steward — OPEN challenges on tracked assumption
        # fields (guidance/margin/one-off) surface as an IV-haircut audit
        # flag. Flag ONLY — the engine stays deterministic; no numbers move.
        try:
            from src.memory.assumption_steward import steward_enabled
            if steward_enabled():
                from src.memory import assumption_store
                _open_ch = [
                    c for c in assumption_store.get_open_challenges(ticker)
                    if str(c.get("field_key") or "").startswith(
                        ("guidance.", "ebitda.", "margin.", "one_off."))]
                if _open_ch:
                    ticker_forward_flags.append(
                        f"R3 Assumption Steward: {len(_open_ch)} open "
                        "challenge(s) on tracked guidance fields ("
                        + ", ".join(sorted(
                            {c["field_key"] for c in _open_ch})[:3])
                        + ") — treat Year-1 guidance inputs with caution "
                        "(IV-haircut flag; engine stays deterministic)")
        except Exception:
            pass

        growth_base = _guided_growth(guidance, revenue_base=revenue_base)
        if growth_base is not None:
            data_source = "guided"
        else:
            growth_base = _analyst_revenue_growth(
                estimates, revenue_base, fx_rate=fx_rate
            )
            if growth_base is not None:
                data_source = "analyst"

        if growth_base is None:
            growth_base = _historical_cagr(series, revenue_base=revenue_base)
            if growth_base is None:
                # Last resort — guidance, analyst estimates, AND historical
                # CAGR all failed (typically bad/missing revenue data despite
                # passing the earlier history-length check). Rather than
                # blank the panel, fall back to a sector-average base-case
                # growth rate; data_source == "sector_default" flags this as
                # the lowest-confidence tier on the frontend.
                growth_base = _SECTOR_DEFAULT_GROWTH.get(sector, _DEFAULT_SECTOR_DEFAULT_GROWTH)
                data_source = "sector_default"
                progress.update_status(
                    agent_id, ticker,
                    f"Cannot derive growth rate — using {sector} sector default ({growth_base:.1%})"
                )
                state["data"].setdefault("dcf_skip_reasons", {})[ticker] = (
                    f"no_growth_rate_used_sector_default_{growth_base:.1%}: "
                    f"guidance_keys={list(guidance.keys())[:5] if guidance else 'none'}, "
                    f"estimates_count={len(estimates) if estimates else 0}, "
                    f"series_count={len(series)}, "
                    f"revenue_first={series[0]['revenue'] if series else None}, "
                    f"revenue_last={series[-1]['revenue'] if series else None}"
                )

        # ── Consensus dispersion bands (Feature 1a) ─────────────────────
        # Derives asymmetric bear / base / bull growth rates from analyst
        # revenue low/avg/high when ≥3 analysts cover the name. Replaces the
        # symmetric ±45% multiplier used when dispersion is unavailable.
        # Per-ticker value — does not vary across the three scenarios.
        # Banks: consensus "revenue" estimates are published on TOTAL INCOME
        # (NII + non-interest income), while `revenue_base` for a bank is
        # gross interest income plus non-II. Comparing the two implies a
        # fictitious collapse — DBS consensus S$23.6bn against a S$37.9bn
        # gross base reads as -38% growth, which pinned bear, base AND bull
        # to the -30% clamp floor (the giveaway signature of this bug is
        # three identical bands sitting exactly on the floor). The growth
        # RATE is scale-free, so deriving it on the total-income base and
        # applying it to the gross base downstream is correct.
        # NOTE: `profile_name` is not bound yet at this point in the
        # function (it is resolved further down), so the bank test is built
        # from `sector` plus the ticker lookup, with a structural fallback
        # for tickers that get classified in-situ. is_bank_sector() alone is
        # too broad — it also matches insurers and asset managers, whose
        # income statements do not carry the gross-interest-income
        # convention this correction targets.
        _bands_base = revenue_base
        _lookup_profile_for_bands = ""
        try:
            from src.data.sector_profiles import get_wacc_profile_for_ticker as _gwp
            _lookup_profile_for_bands = _gwp(ticker)[1] or ""
        except Exception:
            _lookup_profile_for_bands = ""
        # Structural signature of a bank P&L reported gross: interest income
        # is a component of revenue, and interest expense is a material
        # share of it. An insurer or asset manager fails this test.
        _mr_rev = _safe(most_recent.get("revenue")) or 0.0
        _mr_ii = _safe(most_recent.get("interest_income"))
        _mr_ie = _safe(most_recent.get("interest_expense"))
        _looks_like_gross_bank_pl = bool(
            _mr_rev > 0 and _mr_ii and _mr_ie
            and _mr_rev > _mr_ii
            and abs(_mr_ie) > _mr_rev * 0.10
        )
        _is_bank_for_bands = is_bank_sector(sector) and (
            "Bank" in _lookup_profile_for_bands
            or _lookup_profile_for_bands in _BANK_PROFILE_CALIBRATION
            or _looks_like_gross_bank_pl
        )
        if _is_bank_for_bands:
            _ti = _bank_total_income(most_recent)
            if _ti and _ti > 0:
                _bands_base = _ti
        _analyst_bands = _analyst_growth_bands(
            estimates, _bands_base, fx_rate=fx_rate
        )
        if _analyst_bands is not None:
            if _bands_base != revenue_base:
                ticker_forward_flags.append(
                    f"Bank growth base: consensus measured against total income "
                    f"{_bands_base / 1e9:.2f}B (NII + non-II), not gross revenue "
                    f"{revenue_base / 1e9:.2f}B"
                )
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
                # Tier 2 Tech: forward revenue + forward EBIT. FMP already
                # exposes these in the same /stable/analyst-estimates payload
                # (revenueLow/Avg/High, ebitLow/Avg/High) and get_analyst_estimates
                # maps them — we just weren't wiring them into the method dispatch.
                "revenue": {"bear": _fx(_safe(getattr(_fwd, "revenue_low",  None))),
                            "base": _fx(_safe(getattr(_fwd, "revenue_avg",  None))),
                            "bull": _fx(_safe(getattr(_fwd, "revenue_high", None)))},
                "ebit":   {"bear": _fx(_safe(getattr(_fwd, "ebit_low",  None))),
                           "base": _fx(_safe(getattr(_fwd, "ebit_avg",  None))),
                           "bull": _fx(_safe(getattr(_fwd, "ebit_high", None)))},
                "analyst_count_eps":     getattr(_fwd, "analyst_count_eps",     None),
                "analyst_count_revenue": getattr(_fwd, "analyst_count_revenue", None),
                "period_end":            getattr(_fwd, "period_end",            ""),
            }

        # ── R1: licensed analyst-report estimates vs street consensus ─────
        # Deposited sell-side reports (analyst_reports table) are the ONLY
        # estimate source for HK/SG names (FMP returns nothing there); where
        # FMP consensus exists they become a cross-check. Comparison is on
        # RAW reporting-currency figures (both sides pre-FX); a ratio
        # outside 0.25–4.0 is reported as a basis mismatch, not a divergence.
        if os.environ.get("EARNINGS_ASSUMPTIONS", "true").strip().lower() not in (
                "0", "false", "no", "off", ""):
            try:
                from src.memory import assumption_store as _r1_store
                _r1_reports = _r1_store.get_analyst_reports(ticker, limit=1)
            except Exception:
                _r1_reports = []
            if _r1_reports:
                _rep = _r1_reports[0]
                _est_list = _rep.get("estimates") or []
                _est = _est_list[0] if _est_list else {}
                _house_lbl = (f"{_rep.get('house') or 'Sell-side'} "
                              f"{_rep.get('report_date') or ''}").strip()
                try:
                    from src.memory.assumption_extract import (
                        parse_amount as _r1_amt)
                except Exception:
                    _r1_amt = None
                _h_rev = _r1_amt(_est.get("revenue")) if (_r1_amt and _est) else None
                _s_rev = _safe(getattr(_fwd, "revenue_avg", None)) \
                    if estimates else None
                if _h_rev and _s_rev:
                    _ratio = _h_rev / _s_rev
                    if 0.25 <= _ratio <= 4.0:
                        _div = _ratio - 1.0
                        if abs(_div) >= 0.03:
                            ticker_forward_flags.append(
                                f"R1 house vs street FY+1 revenue: "
                                f"{_house_lbl} {_div:+.1%} vs consensus")
                    else:
                        ticker_forward_flags.append(
                            f"R1 house vs street FY+1 revenue: currency "
                            f"basis differs ({_house_lbl} vs FMP) — not "
                            f"compared")
                elif _h_rev and not estimates:
                    ticker_forward_flags.append(
                        f"R1 house estimates on file ({_house_lbl}): FY+1 "
                        f"revenue ≈ {_h_rev/1e9:.1f}B — no FMP consensus "
                        f"for this name")

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
        # v1.5 refactor: prefer pre-classified profile_name from strategic_router
        # (state["data"]["profile_names"][ticker]) to eliminate a class of bugs
        # where downstream code references profile_name before classify_valuation_
        # profile runs. Fall back to in-situ classification for tickers without
        # lookup overrides.
        revenue_cagr = _historical_cagr(series) or growth_base
        is_pre_revenue = (revenue_base < 10_000_000)  # <$10M revenue → treat as pre-revenue

        # Gross margin for the classifier's luxury rung. `_gross_margin` is the
        # SAME function the EV/Revenue multiple qualifier already uses, so the
        # classifier and the multiple qualifier cannot disagree about what a
        # gross margin is. It returns None when revenue is non-positive or when
        # neither `gross_profit` nor `cost_of_revenue` is present, and None is
        # passed through rather than defaulted: an unmeasured margin must not
        # promote a name onto a better methodology.
        #
        # MEASURED on all 14 golden fixtures, one subprocess each
        # (`scratchpad/probe_gross_margin.py` -> `scratchpad/gross_margin_table.json`):
        # gross margin is computable on 14 of 14 and is IDENTICAL across the
        # three scenario passes of each fixture, because it is read off the most
        # recent historical row and scenarios vary the forward path, not the
        # filing. So this adds no scenario-dependent branch to the profile.
        _gross_margin_for_classify = _gross_margin(most_recent)

        _preclassified_profiles = state["data"].get("profile_names") or {}
        _preclassified_name = _preclassified_profiles.get(ticker)
        # B1 ledger: which routing layer produced the profile. Five layers can
        # each overwrite the last, and without this record an error cannot be
        # pinned on routing rather than on the method values.
        _routing_trace: dict = {
            "router_profile": _preclassified_name,
            "router_source": (state["data"].get("profile_sources") or {}).get(ticker),
            "industry_routing": {"enabled": False},
            "steps": [],
        }
        # D3: True when the intended profile did not resolve and a fallback
        # was used. Surfaced in dcf_range[ticker]["profile_fallback_used"]
        # so degradation is loud, not silent.
        _profile_fallback_used = False
        if _preclassified_name:
            # Use the pre-classified profile_name from strategic_router
            from src.data.sector_profiles import INDUSTRY_VALUATION_PROFILES
            _sector_lookup = "RealEstate" if sector == "REIT" else sector
            profile_name = _preclassified_name
            profile_data = INDUSTRY_VALUATION_PROFILES.get(_sector_lookup, {}).get(
                _preclassified_name, {}
            )
            if not profile_data:
                # D3: pre-classified name didn't resolve — LOUD warning, then
                # fall through to in-situ classification.
                _profile_fallback_used = True
                _log.warning(
                    "[dcf] %s: pre-classified profile %r not found in "
                    "INDUSTRY_VALUATION_PROFILES[%r] — falling back to "
                    "in-situ classification",
                    ticker, _preclassified_name, _sector_lookup,
                )
                progress.update_status(
                    agent_id, ticker,
                    f"Profile fallback: {_preclassified_name!r} unresolved — "
                    f"re-classifying from financials",
                )
                profile_name, profile_data = get_valuation_profile(
                    sector, revenue_cagr, _fcf_margin_for_classify, leverage,
                    is_pre_revenue, revenue_base=revenue_base,
                    gross_margin=_gross_margin_for_classify,
                )
        else:
            profile_name, profile_data = get_valuation_profile(
                sector, revenue_cagr, _fcf_margin_for_classify, leverage,
                is_pre_revenue, revenue_base=revenue_base,
                gross_margin=_gross_margin_for_classify,
            )

        if _preclassified_name and not _profile_fallback_used:
            _routing_trace["winner"] = _routing_trace["router_source"] or "router"
        else:
            _routing_trace["winner"] = "ladder"
            _routing_trace["ladder_inputs"] = {
                "revenue_cagr":   _ledger_num(revenue_cagr),
                "fcf_margin":     _ledger_num(_fcf_margin_for_classify),
                "leverage":       _ledger_num(leverage),
                "is_pre_revenue": bool(is_pre_revenue),
                "revenue_base":   _ledger_num(revenue_base),
                # Recorded so a profile can be audited against the rung that
                # produced it. A None here means the gross margin was NOT
                # measurable off the most recent filing, which is a different
                # statement from "the gross margin is low" and must stay
                # distinguishable in the ledger.
                #
                # GOLDEN IMPACT, MEASURED RATHER THAN ASSUMED: none, and not
                # because `routing_trace` is unpinned -- it IS pinned, whole, by
                # `_DICT_KEYS` in `src/memory/golden_replay.py`. It is because
                # `ladder_inputs` is only ever set on this branch, and the
                # branch is not taken by ANY of the 14 fixtures: the probe
                # recorded `routing_trace.winner == "router"` on 13 and
                # `"ticker_override"` on U96_SI, `get_valuation_profile` called
                # ZERO times, and no `routing_trace.ladder_inputs.*` key present
                # in any projection. See the coverage warning in
                # tests/test_consumer_monotonic_ladder.py for why that is a
                # hole and not a comfort.
                "gross_margin":   _ledger_num(_gross_margin_for_classify),
            }
        _routing_trace["steps"].append(
            {"layer": _routing_trace["winner"], "sector": sector,
             "profile": profile_name})

        # ── Guardrail 4: ticker-level profile override ─────────────────────
        # TICKER_SECTOR_LOOKUP can specify a hard profile override (second field).
        # When set, it takes PRIORITY over classify_valuation_profile() — used for
        # companies that can't be differentiated by financials alone (e.g.
        # cybersecurity firms look like SaaS but need different TGR/methods).
        #
        # Guardrail 4a: INDUSTRY routing (FEATURE_RESOURCE_HOLDCO_MAP_V2).
        # `classify_valuation_profile` reads financial characteristics within a
        # sector and never reads the INDUSTRY, so it cannot distinguish a gold
        # miner from a speciality chemical company. Measured across 100 HK and
        # 100 SG large caps it valued BYD as Apparel / Athletic Wear, CATL as
        # Aerospace & Defense, AIA as FinTech and Bukit Sembawang -- a landed
        # residential developer -- as Travel & Dining. It never fails loudly;
        # it returns something plausible.
        #
        # This sits BELOW the ticker override that follows (a company fact
        # still beats an industry rule) and ABOVE the financial ladder.
        _ir_flag = _industry_routing_enabled()
        if _ir_flag or _industry_routing_in_scope(ticker):
            _ir_trace: dict = {}
            _routed = _industry_routed_profile(ticker, sector, end_date,
                                               trace=_ir_trace)
            _routing_trace["industry_routing"] = {
                "enabled": True, "scope": "flag" if _ir_flag else "industry_scope",
                **_ir_trace,
                "changed_profile": bool(_routed and _routed[1] != profile_name)}
            if _routed and _routed[1] != profile_name:
                _r_sector, _r_profile, _r_data = _routed
                _log.info("[dcf] %s: industry routing -> %s/%s (was %s/%r)",
                          ticker, _r_sector, _r_profile, sector, profile_name)
                progress.update_status(
                    agent_id, ticker,
                    f"Profile from industry routing: {_r_sector}/{_r_profile}")
                profile_name, profile_data = _r_profile, _r_data
                # The SECTOR has to move with the profile. Peer-relative
                # methods look their multiples up by sector, so adopting an
                # Insurance profile while leaving sector="Tech" prices an
                # insurer off software comparables. That mismatch was worth
                # multiples of the answer: China Taiping came out at 105.5
                # against a ~25 share price, Longyuan 40.5 against ~8, Cathay
                # 52.9 against ~14. With the sector carried across they land
                # at 21.4, 11.9 and 25.8.
                sector = _r_sector
                _routing_trace["winner"] = "industry_map"
                _routing_trace["steps"].append(
                    {"layer": "industry_map", "sector": sector,
                     "profile": profile_name})

        _lookup_sector, _lookup_profile = get_wacc_profile_for_ticker(ticker)
        if _lookup_profile and _lookup_profile != profile_name:
            from src.data.sector_profiles import INDUSTRY_VALUATION_PROFILES
            # The curated pair travels TOGETHER, the same way the routed pair
            # does. Resolving the override against whatever `sector` happens
            # to hold made this branch fail in two ways at once:
            #
            #   * industry routing had just reassigned `sector`, so the
            #     override was looked up under the routed sector and silently
            #     declined -- inverting the intended precedence, which is that
            #     a company fact beats an industry rule. Sembcorp is not a
            #     regulated utility (Singapore's power market is liberalised)
            #     and ComfortDelGro is not a railway; both had a correct
            #     curated entry that lost to the router.
            #   * sector "REIT" has no entry in INDUSTRY_VALUATION_PROFILES at
            #     all -- S-REIT lives under "RealEstate" -- so all thirteen
            #     S-REITs had their curated override declined. The alias
            #     already existed one branch above and simply was not applied
            #     here.
            _cands = []
            for _s in (_lookup_sector, sector):
                if not _s:
                    continue
                _cands.append(_s)
                if _s == "REIT":
                    _cands.append("RealEstate")
            _override_data, _override_sector = {}, sector
            for _s in _cands:
                _d = INDUSTRY_VALUATION_PROFILES.get(_s, {}).get(_lookup_profile, {})
                if _d:
                    _override_data, _override_sector = _d, _s
                    break
            if _override_data:
                profile_name = _lookup_profile
                profile_data = _override_data
                # Carry the sector too: peer multiples are looked up by
                # sector, so adopting a profile without its sector prices the
                # company off the wrong comparables (35dc0f1).
                sector = _override_sector
                progress.update_status(
                    agent_id, ticker,
                    f"Profile override from TICKER_SECTOR_LOOKUP: {_lookup_profile}"
                )
                _routing_trace["winner"] = "ticker_override"
                _routing_trace["steps"].append(
                    {"layer": "ticker_override", "sector": sector,
                     "profile": profile_name})
            else:
                # D3: the lookup override did not resolve in
                # INDUSTRY_VALUATION_PROFILES — previously this was dropped
                # SILENTLY and the ticker kept whatever profile the ladder
                # picked. Loud warning + flag instead.
                _profile_fallback_used = True
                _routing_trace["override_unresolved"] = _lookup_profile
                _log.warning(
                    "[dcf] %s: TICKER_SECTOR_LOOKUP profile override %r did "
                    "not resolve in INDUSTRY_VALUATION_PROFILES[%r] — keeping "
                    "classified profile %r",
                    ticker, _lookup_profile, sector, profile_name,
                )
                progress.update_status(
                    agent_id, ticker,
                    f"Profile override {_lookup_profile!r} unresolved — "
                    f"keeping {profile_name!r}",
                )

        # ── SOTP (analyst) blend promotion (task #25) ────────────────────
        # With extractor-built assumptions on this ticker, lift the shadow
        # method into the resolved profile at _SOTP_ANALYST_BLEND_WEIGHT
        # (weight 3.0 → exactly 75% of the blended IV; the DCF+multiples
        # blend keeps the other 25%, internally renormalized). Copy-on-
        # write — the shared INDUSTRY_VALUATION_PROFILES dict is never
        # mutated. The T-1 backward gate below receives the overlay too,
        # but its historical t1_row carries no sotp_assumptions → method
        # value None → skipped and renormalized → T-1 calibration and
        # every no-SOTP ticker stay bit-identical.
        # B4: an ACTIVE calibration's fitted method weights for this (sector,
        # profile), and its market IV multiplier. Nothing is active until a
        # proposal is promoted; until then both hooks hand back their input,
        # so the blend is bit-identical. Applied before the gates and SOTP
        # promotions below, which then act on the calibrated weights.
        try:
            from src.memory import calibration as _calibration_mod
            _active_cal = _calibration_mod.active_version()
            profile_data = _calibration_mod.apply_profile_weights(
                profile_data, sector, profile_name, active=_active_cal)
            _iv_calibration_k = _calibration_mod.iv_multiplier(ticker, active=_active_cal)
        except Exception:                                  # noqa: BLE001
            _active_cal, _iv_calibration_k = None, None

        profile_data, _ev_rev_gated = _gate_ev_revenue(
            profile_data, profile_name, most_recent)
        if _ev_rev_gated:
            _log.info("[dcf] %s: EV/Revenue dropped (positive EBITDA) -- "
                      "weight reallocated to Forward P/E and EV/EBITDA", ticker)
            progress.update_status(
                agent_id, ticker,
                "EV/Revenue gated off: profitable, so priced on earnings")

        # ── Balance-sheet-financial strip (Phase 1.2A) ────────────────────
        # Runs before the SOTP promotions: those ADD legs (and reset the
        # anchor), this one REMOVES them, so the reduce-then-promote order
        # keeps the anchor a promotion sets as the final word.
        profile_data, _bsf_gate_rec, _bsf_gate_exc, _bsf_is_financial = (
            _gate_balance_sheet_financial(profile_data, profile_name, most_recent))
        #: The CLASSIFICATION, not "did the strip remove something". The two
        #: come apart on banks: Money Center Bank (+SG) carries no EV/DCF/FCF
        #: leg, so nothing is stripped and no record is emitted — yet 02888.HK
        #: still computed a Forward EV/EBITDA shadow row of HK$730 per share,
        #: which is the defect this gate exists to remove. Keying the shadow
        #: skip off `_bsf_gate_rec` left it in place for every bank; the golden
        #: baseline caught that (02888_HK and D05_SI kept the row).
        _bsf_stripped = _bsf_gate_rec is not None
        if _bsf_stripped:
            gate_evaluations.append(_bsf_gate_rec)
            _log.info("[dcf] %s: %s", ticker, _bsf_gate_rec["basis"])
            progress.update_status(
                agent_id, ticker,
                "Balance-sheet financial: EV/DCF/FCF legs removed "
                f"({_bsf_gate_rec['raw_input_path_a']:.0%} of profile weight)")
            ticker_forward_flags.append(
                "Balance-sheet-financial gate: stripped "
                f"{_bsf_gate_rec['raw_input_path_a']:.1%} of profile weight "
                f"(EV/DCF/FCF legs) — {_bsf_gate_rec['basis']}"
            )
        elif _bsf_gate_exc is not None:
            # Tier 2 measured above the threshold on an exempt profile. The one
            # stand-down worth publishing: it is the audit trail that says the
            # clearing-house exemption was exercised, not that nothing looked.
            ticker_forward_flags.append(
                f"Balance-sheet-financial gate stood down: {_bsf_gate_exc}")

        if (_bsf_is_financial and not _bsf_stripped
                and forward_consensus is not None):
            # The gate still DID something on this run: it suppressed the
            # Forward EV/EBITDA shadow row. Recording only the strip would
            # leave the forward ledger with no firing at all for 02888.HK and
            # DBS — the two names whose published row was the defect — because
            # their profiles carry no EV leg to remove. This is the entry that
            # makes the gate scoreable on banks, and it is emitted once per run
            # here rather than in the scenario loop where the row is built.
            #
            # `forward_consensus is not None` is load-bearing: with no forward
            # consensus the row would not have been computed anyway, and
            # recording a suppression that suppressed nothing would inflate the
            # firing count the acceptance bar is measured on.
            gate_evaluations.append({
                "gate_id": "GATE_BALANCE_SHEET_FINANCIAL",
                "metric": "forward_ev_ebitda_row",
                # 1.0 = the row was published, 0.0 = it is not computed. A
                # presence metric, not a value metric: the point is that no
                # number exists to be wrong. Path A's HK$730 is deliberately
                # not recorded, because recomputing it to store it would
                # re-introduce the very EV bridge the gate exists to refuse.
                "raw_input_path_a": 1.0,
                "gated_output_path_b": 0.0,
                "basis": (
                    f"{profile_name} is a balance-sheet financial (tier 1, "
                    f"profile): enterprise value subtracts the deposits and "
                    f"customer balances that ARE the product, so a forward "
                    f"EV/EBITDA per share has no interpretation. The row is not "
                    f"computed. The profile carries no EV/DCF/FCF leg, so no "
                    f"blend weight moved and the intrinsic value is unchanged."
                ),
                "applied": True,
            })
            ticker_forward_flags.append(
                "Balance-sheet-financial gate: forward EV/EBITDA row suppressed "
                f"(no EV/DCF/FCF leg in {profile_name} to strip; IV unchanged)")

        profile_data, _lt_sotp_on = _promote_lookthrough_sotp(
            profile_data, ticker, end_date)
        if _lt_sotp_on:
            _log.info("[dcf] %s: SOTP / NAV promoted from a complete "
                      "look-through", ticker)

        profile_data, _seg_sotp_on = _promote_segment_sotp(
            profile_data, ticker, bool(most_recent.get("segment_breakdown")),
            profile_name=profile_name)
        if _seg_sotp_on:
            _log.info("[dcf] %s: SOTP (segments) promoted at %.2f from the "
                      "filing segment note", ticker, _SEGMENT_SOTP_WEIGHT)
            progress.update_status(agent_id, ticker,
                                   "SOTP (segments) promoted from the filing")

        profile_data = _promote_sotp_analyst_profile(
            profile_data, bool(most_recent.get("sotp_assumptions")))

        # v3.21 (Fix D) — write the locally-resolved profile_name back to state
        # so late-pipeline consumers (sector_card render, reextract path, audit
        # bridge) see the profile even when strategic_router skipped pre-
        # classification (ticker not in TICKER_SECTOR_LOOKUP and no LLM-based
        # router-side classifier in the v3.21 deploy yet). Belt-and-suspenders
        # — won't help upstream extractors that already ran in deep_research,
        # but ensures aggregation + frontend rendering picks up the resolved
        # profile correctly.
        if profile_name:
            try:
                _pn_dict = state["data"].setdefault("profile_names", {})
                if isinstance(_pn_dict, dict) and not _pn_dict.get(ticker):
                    _pn_dict[ticker] = profile_name
                if not state["data"].get("profile_name"):
                    state["data"]["profile_name"] = profile_name
            except Exception:
                pass  # state-write failure is non-fatal
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

        # ── REIT sub-type classification + audit (Tier 2) ───────────────────
        # For RealEstate/REIT tickers, classify into 9 sub-types (data_center,
        # lab, industrial, self_storage, residential, healthcare, retail,
        # office, hospitality) using ticker + TICKER_SECTOR_LOOKUP notes as
        # keyword source. Sub-type drives cap rate, P/FFO, P/AFFO multiples,
        # and maintenance capex % for AFFO compute. Falls to "default" on no
        # keyword match. Cached on most_recent so NAV/P/FFO/P/AFFO/DDM
        # dispatches don't re-classify.
        if sector in {"RealEstate", "REIT"} or "REIT" in (profile_name or ""):
            from src.data.sector_profiles import TICKER_SECTOR_LOOKUP as _TSL
            from src.data.sector_profiles import SGX_TICKER_SECTOR_LOOKUP as _SGX_TSL
            _lookup_notes = ""
            _lookup_entry = _TSL.get(ticker.upper()) or _SGX_TSL.get(ticker.upper())
            if _lookup_entry and len(_lookup_entry) >= 4:
                _lookup_notes = _lookup_entry[3] or ""
            _reit_subtype = _classify_reit_subtype(ticker, _lookup_notes)
            most_recent["_reit_subtype"]  = _reit_subtype
            # SGX sub-sector drives the DDM calibration and is resolved from
            # the SGX lookup, not from the US sub-type keyword classifier.
            if _SGX_TSL.get(ticker.upper()):
                most_recent["_sreit_subtype"] = _sgx_reit_subtype(ticker)
            most_recent["_ticker"]        = ticker
            most_recent["_lookup_notes"]  = _lookup_notes

            _reit_m = _compute_reit_metrics(most_recent, subtype=_reit_subtype)
            _mults  = _REIT_SUBTYPE_MULTIPLES.get(_reit_subtype, _REIT_SUBTYPE_MULTIPLES["default"])

            def _fmt_b(v):
                if v is None:
                    return "n/a"
                if abs(v) >= 1e9:
                    return f"${v/1e9:.2f}B"
                if abs(v) >= 1e6:
                    return f"${v/1e6:.0f}M"
                return f"${v:.0f}"

            _sreit_sub = most_recent.get("_sreit_subtype")
            if _sreit_sub:
                _ddm_dbg = _compute_sreit_ddm(ticker, _sreit_sub, most_recent,
                                              shares, dpu_growth=growth_base)
                if _ddm_dbg is not None:
                    _dv, _dpu_f, _da = _ddm_dbg
                    _sym = _CCY_SYMBOLS.get((reported_currency or "SGD").upper(),
                                            (reported_currency or "SGD").upper() + " ")
                    ticker_forward_flags.append(
                        f"S-REIT DDM ({_sreit_sub}): DPU fwd {_dpu_f * 100:.2f} cents "
                        f"-> {_sym}{_dv:,.2f}/unit [{', '.join(_da['provenance'])}]"
                    )
                else:
                    ticker_forward_flags.append(
                        f"S-REIT DDM ({_sreit_sub}): unavailable — no DPU or "
                        f"CoE too close to terminal g"
                    )

            ticker_forward_flags.append(
                f"REIT sub-type: {_reit_subtype} | cap_rate "
                f"{_mults['cap_rate']:.2%} | P/FFO {_mults['p_ffo']:.0f}x | "
                f"P/AFFO {_mults['p_affo']:.0f}x | maint_capex "
                f"{_reit_m['maint_capex_pct_used']:.1%} rev | "
                f"FFO={_fmt_b(_reit_m['ffo'])} AFFO={_fmt_b(_reit_m['affo'])} "
                f"NOI={_fmt_b(_reit_m['noi'])}"
            )

            _rm_override = (reit_metrics_all or {}).get(ticker) or {}
            if _rm_override:
                if "cap_rate_market" in _rm_override:
                    most_recent["cap_rate_market"] = _rm_override["cap_rate_market"]
                if "affo_per_unit_cents" in _rm_override:
                    most_recent["affo_per_share_research"] = _rm_override["affo_per_unit_cents"] / 100.0
                # ── S-REIT DDM inputs ────────────────────────────────────
                # Namespaced with an `_sreit_` prefix deliberately. These
                # are read only by the DDM and must never shadow a
                # statement line item — the framework bridge writes
                # extracted KPIs onto the financial row by key, and an
                # unprefixed name silently replaces real data.
                if _rm_override.get("dpu_cents"):
                    most_recent["_sreit_dpu_cents_research"] = _rm_override["dpu_cents"]
                if _rm_override.get("sreit_cost_of_equity"):
                    most_recent["_sreit_coe_research"] = _rm_override["sreit_cost_of_equity"]
                if _rm_override.get("sreit_terminal_growth") is not None:
                    most_recent["_sreit_terminal_g_research"] = _rm_override["sreit_terminal_growth"]
                _rm_parts = []
                if "cap_rate_market" in _rm_override:
                    _rm_parts.append(f"cap_rate {_rm_override['cap_rate_market']:.2%} "
                                     f"(override from default {_mults['cap_rate']:.2%})")
                if "occupancy_rate" in _rm_override:
                    _rm_parts.append(f"occupancy {_rm_override['occupancy_rate']:.0%}")
                if "wale_years" in _rm_override:
                    _rm_parts.append(f"WALE {_rm_override['wale_years']:.1f}y")
                if "dpu_cents" in _rm_override and "affo_per_unit_cents" in _rm_override:
                    _dpu = _rm_override['dpu_cents']
                    _affo_u = _rm_override['affo_per_unit_cents']
                    _cov = _dpu / _affo_u if _affo_u > 0 else 0
                    _rm_parts.append(f"DPU/AFFO coverage {_cov:.1%} "
                                     f"({'sustainable' if _cov <= 1.0 else 'UNSUSTAINABLE'})")
                if "leverage_ratio" in _rm_override:
                    _rm_parts.append(f"leverage {_rm_override['leverage_ratio']:.0%}")
                if _rm_parts:
                    ticker_forward_flags.append("REIT research metrics: " + " | ".join(_rm_parts))

        # ── Bank metrics attachment + audit (Tier 2 item 3) ─────────────────
        if (is_bank_sector(sector) and profile_name in _BANK_PROFILE_CALIBRATION) \
                or "Bank" in (profile_name or "") or profile_name == "Mortgage/GSE":
            _bank_m = _compute_bank_metrics(most_recent, profile_name=profile_name)
            _bank_cfg = _bank_profile_calibration(profile_name)

            _bm_override = (bank_metrics_all or {}).get(ticker) or {}
            if _bm_override.get("cet1_ratio"):
                most_recent["_bank_cet1_research"] = _bm_override["cet1_ratio"]
            if _bm_override.get("management_target_roe"):
                most_recent["_bank_target_roe_research"] = _bm_override["management_target_roe"]
            # GGM assumptions lifted straight from the research note's
            # valuation table, when the extractor found them.
            if _bm_override.get("cost_of_equity"):
                most_recent["_bank_coe_research"] = _bm_override["cost_of_equity"]
            if _bm_override.get("terminal_growth_rate") is not None:
                most_recent["_bank_ggm_g_research"] = _bm_override["terminal_growth_rate"]
            if _bm_override.get("target_price_to_book"):
                most_recent["_bank_target_pb_research"] = _bm_override["target_price_to_book"]

            def _fmt_pct(v):
                return f"{v:.2%}" if v is not None else "n/a"

            # Use the actual reporting currency in the flags. Hard-coding "$"
            # against SGD/HKD figures leaked into the LLM narrative and into
            # the value-trap agent, which raised "currency mislabeling risk"
            # as a genuine red flag on D05.SI and fed it into a SELL.
            _ccy_sym = _CCY_SYMBOLS.get(
                (reported_currency or "USD").upper(),
                f"{(reported_currency or 'USD').upper()} ",
            )

            _bank_parts = [
                f"ROE {_fmt_pct(_bank_m.get('roe'))}",
                f"NIM {_fmt_pct(_bank_m.get('nim'))}",
                f"eff {_fmt_pct(_bank_m.get('efficiency_ratio'))}",
                f"credit_cost {_fmt_pct(_bank_m.get('credit_cost_ratio'))}",
                (f"TBV/sh {_ccy_sym}{_bank_m.get('tbv_per_share'):.2f}"
                 if _bank_m.get('tbv_per_share') else "TBV/sh n/a"),
                f"CET1 implied {_fmt_pct(_bank_m.get('cet1_implied'))}",
            ]
            ticker_forward_flags.append(
                f"Bank metrics ({profile_name}, target ROE {_bank_cfg['target_roe']:.1%} / "
                f"CoE {_bank_cfg['coe']:.1%} / fade {_bank_cfg['fade_years']}y / "
                f"target CET1 {_bank_cfg['target_cet1']:.1%}): " + " | ".join(_bank_parts)
            )

            # GGM audit line — the ROE / CoE / g triplet actually used, with
            # the source of each field, plus the justified P/B it implies.
            _ggm_dbg = _compute_ggm_pb(ticker, profile_name, most_recent, shares)
            if _ggm_dbg is not None:
                _gv, _gpb, _ga = _ggm_dbg
                ticker_forward_flags.append(
                    f"GGM (P/B): target P/B {_gpb:.2f}x → {_ccy_sym}{_gv:,.2f}/sh "
                    f"[{', '.join(_ga['provenance'])}]"
                )

            if _bm_override:
                _or_parts = []
                if _bm_override.get("cet1_ratio"):
                    _or_parts.append(f"CET1 {_bm_override['cet1_ratio']:.2%} (research override)")
                if _bm_override.get("management_target_roe"):
                    _or_parts.append(f"mgmt target ROE {_bm_override['management_target_roe']:.1%}")
                if _bm_override.get("efficiency_ratio"):
                    _or_parts.append(f"efficiency {_bm_override['efficiency_ratio']:.1%}")
                if _bm_override.get("npl_ratio"):
                    _or_parts.append(f"NPL {_bm_override['npl_ratio']:.2%}")
                if _or_parts:
                    ticker_forward_flags.append("Bank research overrides: " + " | ".join(_or_parts))

        # ── SaaS metrics attach (Tier 2 Tech) ──────────────────────────────
        if is_tech_sector(sector):
            _saas_override = (saas_metrics_all or {}).get(ticker) or {}
            if _saas_override:
                most_recent["_saas_metrics"] = _saas_override
                _saas_parts = []
                if "nrr_pct" in _saas_override:
                    _saas_parts.append(f"NRR {_saas_override['nrr_pct']:.0%}")
                if "rule_of_40_score" in _saas_override:
                    _saas_parts.append(f"Rule of 40 {_saas_override['rule_of_40_score']:.0f}")
                if "cac_payback_months" in _saas_override:
                    _saas_parts.append(f"CAC payback {_saas_override['cac_payback_months']:.0f}mo")
                if "magic_number" in _saas_override:
                    _saas_parts.append(f"magic # {_saas_override['magic_number']:.2f}")
                if "gross_retention_pct" in _saas_override:
                    _saas_parts.append(f"gross retention {_saas_override['gross_retention_pct']:.0%}")
                if _saas_parts:
                    ticker_forward_flags.append("SaaS research metrics: " + " | ".join(_saas_parts))

        # ── Deterministic KPIs override the extracted ones (item 4) ────────
        # Alibaba's GAAP operating margin came back +10.0% for 09988.HK and
        # −0.3% for BABA seven minutes apart; Keppel's ROIC came back 1.09% on
        # accounts that do not support it, and BN4.SI is single-listed so the
        # entity cache has no sibling vector to adopt. Both were extracted, not
        # computed. For a KPI that is arithmetic on a filed statement the
        # arithmetic wins — but only for keys the profile itself marks
        # `extractor_only: False`, and only where the filed inputs exist, so a
        # missing input leaves the extractor's value standing rather than being
        # replaced by an invention.
        #
        # Runs BEFORE attach_overrides below, so the row and the card read one
        # number.
        _det_overrides: list[dict] = []
        _det_pre_override: dict = {}
        if _det_kpis and profile_name:
            try:
                from src.data.deterministic_kpis import (
                    apply_overrides as _apply_det_kpis,
                )
                _det_pre_override = dict(
                    (framework_metrics_all or {}).get(ticker) or {})
                _buckets_seen: list[dict] = []
                for _bucket in ([framework_metrics_all]
                                + [state["data"].get(_k) for _k in
                                   ("framework_metrics", "framework_metrics_all",
                                    "insurance_metrics_all", "bank_metrics_all")]):
                    if not isinstance(_bucket, dict):
                        continue
                    if any(_bucket is _seen for _seen in _buckets_seen):
                        continue
                    _existing = _bucket.get(ticker)
                    if not isinstance(_existing, dict):
                        # No extraction to override. Phase 10's create-if-missing
                        # branch still gives this ticker a FMP-only vector for
                        # the card; creating one here would widen this change
                        # from a precedence flip into a new population path.
                        continue
                    _buckets_seen.append(_bucket)
                    _bucket[ticker], _ovr = _apply_det_kpis(
                        _existing, _det_kpis, profile_name, basis=_det_basis)
                    if _ovr and _bucket is framework_metrics_all:
                        _det_overrides = _ovr
                if _det_overrides:
                    _det_summary = ", ".join(
                        f"{o['kpi']} "
                        f"{('gap' if o['llm'] is None else format(o['llm'], '.4g'))}"
                        f"→{o['deterministic']:.4g}"
                        for o in _det_overrides[:4])
                    print(f"  [deterministic-kpis] {ticker} ({profile_name}, "
                          f"FY{str(_det_basis.get('period') or '?')[:4]}): "
                          f"{len(_det_overrides)} KPI(s) computed from filed "
                          f"statements override the extraction — {_det_summary}")
            except Exception as _det_exc:
                ticker_forward_flags.append(
                    f"Deterministic KPI override skipped: "
                    f"{type(_det_exc).__name__}: {str(_det_exc)[:120]}"
                )

        # ── Framework metrics attach (PR #6 — generic for new sub-profiles) ─
        # Handles Regulated Utility, Upstream O&G, Semi Fabless / IDM/Foundry,
        # Telco, Mining (Major), Automotive & EV, Managed Care — any ticker
        # whose profile_name is registered in SECTOR_KPI_FRAMEWORK but NOT
        # covered by a legacy dedicated extractor. Generic loop: each KPI
        # in the framework spec is attached to most_recent under its key,
        # and _<profile>_completeness / _<profile>_missing metadata is set
        # for the downstream UI badge.
        _fm_override = (framework_metrics_all or {}).get(ticker) or {}
        if _fm_override and profile_name:
            try:
                from src.data.sector_kpi_framework import attach_overrides
                _audit_lines = attach_overrides(profile_name, _fm_override, most_recent)
                if _audit_lines:
                    _completeness = _fm_override.get("_completeness_score", "n/a")
                    ticker_forward_flags.append(
                        f"Framework metrics ({profile_name}, "
                        f"completeness={_completeness}): "
                        + " | ".join(_audit_lines)
                    )
            except Exception as _exc:
                # Framework attach is fail-safe — never crash the pipeline.
                # Audit trail captures the failure for debug.
                ticker_forward_flags.append(
                    f"Framework attach skipped ({type(_exc).__name__}: {str(_exc)[:80]})"
                )

        # Live-mNAV audit line — the NAV Discount branch of
        # _compute_method_value consumes most_recent["mNAV_multiple"] when
        # present. Appended pre-loop so it lands in the per-scenario
        # forward_flags snapshot and is visible in the payload/PDF.
        try:
            _mnav_live = float(most_recent.get("mNAV_multiple"))
        except (TypeError, ValueError):
            _mnav_live = None
        if _mnav_live and 0.1 <= _mnav_live <= 8.0:
            ticker_forward_flags.append(
                f"NAV Discount anchor: live mNAV {_mnav_live:.2f}× book "
                f"(framework extractor; static peer P/B premium not used)"
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
                is_sg=_is_sg,
                interest_coverage=_coverage,
                net_debt=net_debt,
                market_cap=_market_cap,
            )
            wacc = _wacc_info["wacc"]
            ticker_forward_flags.append(_wacc_info["audit"])
            # The components, for the Excel export's discount-rate build.
            _wacc_build = {k: v for k, v in _wacc_info.items() if k != "audit"}
        except Exception as _wacc_exc:  # noqa: BLE001 — never block DCF on audit
            _log.warning("[DCF] %s: hybrid WACC failed, using sector base: %s",
                         ticker, _wacc_exc)
            wacc = get_wacc_for_exchange(
                sector, leverage, macro_regime=_risk_appetite,
                profile=profile_name, is_hk=_is_hk, is_sg=_is_sg,
            )
            _wacc_build = {"wacc_base": wacc,
                           "source": "sector/profile table (hybrid cost of debt unavailable)"}
        # How the sector/profile base rate was assumed: table, lookup key,
        # embedded country risk, leverage premium, cap, macro overlay. The
        # same function computes the rate itself, so the two cannot differ.
        try:
            from src.data.sector_profiles import wacc_base_breakdown as _wacc_base_breakdown
            _wacc_build["base_breakdown"] = _wacc_base_breakdown(
                sector, leverage, macro_regime=_risk_appetite,
                profile=profile_name or "", is_hk=_is_hk, is_sg=_is_sg)
        except Exception:  # noqa: BLE001 - disclosure only, never blocks the DCF
            pass

        # ── Insider-activity WACC overlay (Tier 3) ──────────────────────
        # The Phase 2.5 insider_activity_agent populates
        # state["data"]["insider_activity"][ticker] with 12m/90d/30d net
        # buying and conviction flags. That data was previously unused by
        # the DCF. Apply a small ±bp WACC modifier so net-buying signals
        # tighten (lower WACC) and net-selling widens (higher WACC).
        # Capped at ±50bp by the helper so no single signal dominates.
        try:
            _insider_data = (
                state["data"].get("insider_activity", {}) or {}
            ).get(ticker)
            _ins_bps, _ins_audit = _insider_wacc_modifier(_insider_data, _market_cap)
            _wacc_build["insider_bps"] = _ins_bps
            if _ins_bps != 0.0:
                wacc = wacc + _ins_bps / 10000.0
                if _ins_audit:
                    ticker_forward_flags.append(_ins_audit)
        except Exception as _ins_exc:  # noqa: BLE001 — never block DCF on insider overlay
            _log.warning("[DCF] %s: insider WACC overlay failed (ignored): %s",
                         ticker, _ins_exc)

        # Deep-research risk_flag → WACC loading (+50bps HIGH, +25bps MEDIUM)
        _risk_flag = dcf_cal.get("risk_flag", "MEDIUM")
        _wacc_loading = {"HIGH": 0.0050, "MEDIUM": 0.0025, "LOW": 0.0}.get(_risk_flag, 0.0025)
        _wacc_build["research_risk_loading"] = _wacc_loading
        _wacc_build["research_risk_flag"] = _risk_flag
        if _wacc_loading:
            wacc = wacc + _wacc_loading
            progress.update_status(
                agent_id, ticker,
                f"WACC loading from deep research risk_flag={_risk_flag}: "
                # Fix (v3.21): was *100 (percentage points, not basis points)
                # — a 25bps/50bps loading always displayed as "+0bps" since
                # 0.25/0.50 both round to 0 under :.0f. Value applied to wacc
                # was always correct; only this log line was wrong.
                f"+{_wacc_loading*10000:.0f}bps → WACC={wacc:.3f}"
            )

        # REIT NAV: widen the research-extracted cap rate under the SAME
        # risk_flag signal WACC loading just used above (v3.21). Previously
        # a research-extracted cap_rate_market override (see _rm_override
        # further below) was used as-is regardless of how risky deep
        # research flagged the same company elsewhere in this run — e.g. a
        # cap rate override MORE aggressive (lower) than the sector default
        # for a name simultaneously flagged HIGH risk. A higher cap rate
        # means a lower NAV (gross_asset_value = NOI / cap_rate), so this
        # widening is directionally conservative, same as the WACC loading
        # above. Deliberately does NOT touch the sub-type default cap rate
        # (_REIT_SUBTYPE_MULTIPLES) when no research override exists — that
        # table is already a calibrated multi-company baseline, not a
        # single-point extraction, so it doesn't have the same failure mode.
        if most_recent.get("cap_rate_market") is not None and _wacc_loading:
            _cap_rate_before = most_recent["cap_rate_market"]
            most_recent["cap_rate_market"] = _cap_rate_before + _wacc_loading
            ticker_forward_flags.append(
                f"REIT cap rate widened +{_wacc_loading*10000:.0f}bps for risk_flag={_risk_flag}: "
                f"{_cap_rate_before:.2%} → {most_recent['cap_rate_market']:.2%}"
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
        _wacc_build["country_risk_premium"] = _crp
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
                _wacc_build["contracted_revenue_discount"] = _CONTRACTED_DISCOUNT
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

        # ── Analyst valuation basis (benchmark, not an instruction) ──────
        # Records what the sell-side used and what it put in. Where the
        # analyst's method is not this profile's anchor, that is FLAGGED,
        # never acted on: profile routing is ours, and letting a single
        # extracted string reroute a valuation would put a broker's framing
        # in charge of the engine.
        try:
            _ab = _analyst_basis(ticker, most_recent)
            if _ab and _ab.get("method"):
                _parts = [f"method={_ab['method']}"]
                for _k, _lbl in (("wacc", "WACC"), ("cost_of_equity", "CoE"),
                                 ("terminal_growth", "g"),
                                 ("holdco_discount", "holdco disc")):
                    if _ab.get(_k) is not None:
                        _parts.append(f"{_lbl} {_ab[_k]:.2%}")
                if _ab.get("target_multiple"):
                    _parts.append(
                        f"{_ab['target_multiple']:g}x "
                        f"{_ab.get('multiple_basis') or ''}".strip())
                if _ab.get("price_target"):
                    _parts.append(f"TP {_ab['price_target']}")
                if _ab.get("rating"):
                    _parts.append(str(_ab["rating"]))
                _src = (f"{_ab.get('house') or 'sell-side'} "
                        f"{_ab.get('as_of') or ''}").strip()
                ticker_forward_flags.append(
                    f"Analyst basis ({_src}): " + " | ".join(_parts))

                from src.memory.analyst_basis import method_disagrees as _disagrees
                if _anchor_method and _disagrees(_ab, _anchor_method):
                    ticker_forward_flags.append(
                        f"⚠ Method divergence: analyst used "
                        f"{_ab['method']}, profile '{profile_name}' anchors on "
                        f"{_anchor_method} — profile routing retained, "
                        f"flagged for review")
        except Exception as _abe:
            print(f"  [analyst_basis] {ticker}: {type(_abe).__name__}: {_abe!r}")

        # P1.2 — cache most_recent EBITDA for accurate 12m PT computation
        # Using historical EBITDA × (1+g) is far more accurate than FCF / 0.65
        _hist_ebitda = most_recent.get("ebitda")

        progress.update_status(
            agent_id, ticker,
            f"Profile: {profile_name} | Anchor: {_anchor_method} | WACC={wacc:.1%} | g={growth_base:.1%} | C_macro={c_macro:+.2f}"
        )

        # ── Phase 1.1: CAGR-divergence gate (all profiles) ────────────────
        # Applied BEFORE the revenue-scale tier below and BEFORE the scenario
        # multipliers, for the reason that tier's own comment gives: bind the
        # BASE and let the multipliers differentiate afterwards, rather than
        # clipping each scenario to one ceiling and collapsing bear/base/bull
        # onto it.
        #
        # The tier cannot catch defect 1 by itself, because it is keyed to
        # revenue SCALE while this is a divergence-from-history problem.
        # BN4.SI (S$5.98bn) and U96.SI (S$5.80bn) both land in the ≥$3bn tier
        # whose cap is 22% — above BN4's 20.3% consensus and level with U96's
        # 22.0%. Both passed straight through and were then compounded for ten
        # years against a −2.5% five-year CAGR.
        #
        # `_historical_cagr` is the 5-year figure only for as long as the feed
        # caps history at 5 annual rows (defect 8); it is whatever history
        # exists, which is the right input either way.
        _cagr_for_gate = _historical_cagr(series, revenue_base=revenue_base)
        # When analyst bands exist the scenario loop reads them and never looks
        # at growth_base, so the band base is the figure that has to be gated.
        # The two can differ — they come from different estimators.
        _gate_ref = ((_analyst_bands or {}).get("base")
                     if _analyst_bands is not None else growth_base)
        _g_gated, _cagr_gate_rec, _cagr_gate_exc = _gate_growth_cagr_divergence(
            _gate_ref, _cagr_for_gate,
            data_source=data_source, profile_name=profile_name, series=series,
        )
        if _cagr_gate_rec is not None:
            # `_g_gated` IS the bound — read it from the return value rather
            # than back out of the ledger record. (Reaching into the record for
            # it also trips test_gate_backtest's structural check that every
            # gate emits both halves of its path A/B pair, which counts key
            # occurrences in the source and cannot tell a write from a read.)
            _gate_value = float(_g_gated)
            # `direction` IS read out of the record, and that is safe: the key
            # the structural check counts is the path A/B pair, not this one.
            # It has to be read, because the two halves are not two directions
            # of a single operation. A cap is `min()` on the base and a
            # PROPORTIONAL band scale; a floor is `max()` and an ADDITIVE band
            # shift. Applying the ceiling's idiom to a floor would multiply a
            # negative band by `floor / base`, which is NEGATIVE whenever
            # CAGR > 15pp, flipping the sign of every scenario. See
            # `_shift_analyst_bands_to_floor`.
            if _cagr_gate_rec.get("direction") == "floor":
                growth_base = max(growth_base, _gate_value)
                # Shifts the WHOLE band by the amount the base moved, so the
                # spread and the bear ≤ base ≤ bull ordering survive exactly.
                _analyst_bands, _gate_band_adj = _shift_analyst_bands_to_floor(
                    _analyst_bands, _gate_value)
                _gate_band_note = (
                    f"; analyst bands shifted {_gate_band_adj:+.1%}"
                    if _gate_band_adj is not None else "")
            else:
                growth_base = min(growth_base, _gate_value)
                # Scales the WHOLE band, so the spread 14 analysts expressed
                # survives: BN4.SI's 3.8 / 20.3 / 37.3 becomes 0.9 / 5.0 / 9.2,
                # not 3.8 / 5.0 / 5.0.
                _analyst_bands, _gate_band_adj = _scale_analyst_bands_to_cap(
                    _analyst_bands, _gate_value)
                _gate_band_note = (
                    f"; analyst bands scaled {_gate_band_adj:.2f}x"
                    if _gate_band_adj is not None else "")
            gate_evaluations.append(_cagr_gate_rec)
            progress.update_status(
                agent_id, ticker,
                f"CAGR-divergence gate: NTM growth {_gate_ref:.1%} → "
                f"{_gate_value:.1%} (5y CAGR {_cagr_for_gate:.1%})"
            )
            # The arrow format is deliberately NOT made direction-aware. The
            # basis string in the parenthetical already says which half bound
            # ("capped at max(CAGR, 0) + 5pp" vs "raised to CAGR − 15pp"), and
            # rewording the prefix would churn the published flag on every
            # fixture the cap fires on — BN4.SI and U96.SI — for no information.
            ticker_forward_flags.append(
                f"CAGR-divergence gate: NTM growth {_gate_ref:+.1%} → "
                f"{_gate_value:+.1%}"
                + _gate_band_note
                + f" ({_cagr_gate_rec['basis']})"
            )
        elif _cagr_gate_exc is not None and _cagr_for_gate is not None:
            # Recorded, not applied — the divergence was real but an
            # exception stood the gate down. Visible so the forward ledger can
            # tell "gate never fired here" from "gate fired and was overruled".
            ticker_forward_flags.append(
                f"CAGR-divergence gate stood down: NTM growth {_gate_ref:+.1%} "
                f"vs {_cagr_for_gate:+.1%} CAGR "
                f"({abs(_gate_ref - _cagr_for_gate) * 100:.1f}pp apart) — "
                f"{_cagr_gate_exc}"
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

        # The tier above binds growth_base only; the analyst-band path reads
        # its own dict. See _scale_analyst_bands_to_cap for why this scales
        # the band instead of clipping each scenario.
        _analyst_bands, _band_scale = _scale_analyst_bands_to_cap(
            _analyst_bands, _growth_base_cap)
        if _band_scale is not None:
            progress.update_status(
                agent_id, ticker,
                f"Growth cap applied to analyst bands: base → "
                f"{_growth_base_cap:.1%} (revenue "
                f"${revenue_base/1e9:.1f}B exceeds {_growth_base_cap:.0%} "
                f"tier); bear/bull scaled {_band_scale:.2f}x"
            )
            gate_evaluations.append({
                "gate_id": "GATE_REVENUE_SCALE_CAP",
                "metric": "revenue_growth",
                "raw_input_path_a": round(
                    float(_analyst_bands["base"]) / _band_scale, 6),
                "gated_output_path_b": round(float(_analyst_bands["base"]), 6),
                "basis": "revenue-tier cap",
            })
            ticker_forward_flags.append(
                f"Revenue-scale cap on analyst bands: base growth capped to "
                f"{_growth_base_cap:.1%} (${revenue_base/1e9:.1f}B revenue)"
            )
        # The cap above mutates growth_base, which the ANALYST-BAND path never
        # reads — it takes g straight from _analyst_bands below. So a name with
        # analyst coverage escaped the tier entirely and kept only the flat
        # +40% clamp. MELI 2026-08-30 carried g = 40% on a $28.9bn revenue base
        # against a 15% tier, compounding to $194.9bn of year-10 revenue.
        #
        # Scale the whole band rather than clipping each scenario, so the
        # bear/base/bull spread the analyst dispersion actually expresses is
        # preserved. This mirrors the CAGR path exactly: there the cap binds
        # the BASE and the scenario multipliers (0.55/1.00/1.50) still carry
        # bull above it.
        if _analyst_bands is not None:
            _band_base = _analyst_bands.get("base")
            if (_band_base and _band_base > _growth_base_cap > 0):
                _band_scale = _growth_base_cap / _band_base
                progress.update_status(
                    agent_id, ticker,
                    f"Growth cap applied to analyst bands: base "
                    f"{_band_base:.1%} → {_growth_base_cap:.1%} "
                    f"(revenue ${revenue_base/1e9:.1f}B exceeds "
                    f"{_growth_base_cap:.0%} tier); bear/bull scaled "
                    f"{_band_scale:.2f}x"
                )
                ticker_forward_flags.append(
                    f"Revenue-scale cap on analyst bands: base growth "
                    f"{_band_base:.1%} → {_growth_base_cap:.1%} "
                    f"(${revenue_base/1e9:.1f}B revenue)"
                )
                _analyst_bands = {
                    k: (v * _band_scale if k in ("bear", "base", "bull") else v)
                    for k, v in _analyst_bands.items()
                }

        # ── Run three scenarios ───────────────────────────────────────────
        scenario_results: dict[str, dict] = {}
        _base_proj_rows: list[dict] = []
        _base_pv_fcf_per_share: float = 0.0
        _base_pv_tv_per_share: float = 0.0

        # Validate analyst bands — if bear/base/bull dispersion is implausibly
        # tight (<0.5%), analyst data is suspect (observed on NET 2026-04-25:
        # bear=1.00 / base=1.00 / bull=1.00, all identical). Fall back to
        # CAGR-based growth which handles the ticker normally.
        if _analyst_bands is not None:
            # Only the three scenario values participate in the dispersion
            # check — including "analyst_count" in .values() made max−min ≈
            # the analyst count, so this guard could never fire (task #26).
            _band_values = [_analyst_bands[k] for k in ("bear", "base", "bull")]
            _dispersion = max(_band_values) - min(_band_values)
            if _dispersion < 0.005:
                print(
                    f"  [dcf] Analyst bands suspect for {ticker} "
                    f"(dispersion={_dispersion:.4f}, values={_band_values}) "
                    f"— falling back to CAGR-based growth"
                )
                _analyst_bands = None

        # Per-profile scenario dispersion (Tier 2 reality-tightening, Gemini review):
        # High-SBC tech profiles (Growth SaaS, Cybersecurity, Hyper-Growth) get
        # tighter bands to model genuine scenario risk; Banks/REITs/Mature SaaS
        # keep legacy bands that are calibrated for cash-generative profiles.
        _g_mult, _m_mult = _scenario_mults_for_profile(profile_name)

        # ── Structural method availability across scenarios ──────────────
        # Scenario analysis varies INPUTS (growth, margins, multiples), not
        # MODEL STRUCTURE. The base scenario therefore fixes the method set:
        # a profile method that cannot produce a positive value on base-case
        # inputs is unavailable in EVERY scenario. Without this, a method
        # that fires in only one scenario changes the blend composition
        # (_blend_methods skips None values and renormalizes the weights):
        # COIN 2026-08-09 — Forward P/E fired only in bull (consensus EPS
        # crossed zero between eps_avg and eps_high), injecting a $16.85
        # outlier into ~20% of the bull blend → bull IV $64.56 < bear IV
        # $78.25. Bear/bull may still DROP a base-available method when
        # their stressed inputs break it — that is a genuine stress signal,
        # never a composition gain.
        _structurally_unavailable: set = set()

        # ── Failure 2: promote a distorted trailing P/E onto normalized earnings
        #
        # The owner's post-mortem called `P/E (Premium)` a fraud "documented as
        # differing only in label, not earnings source", and prescribed replacing
        # `P/E` and `P/E (Premium)` with `P/E (norm)` whenever the margin
        # deviation exceeds 0.40. Three things had to be built rather than wired
        # up, and each is recorded at its own definition:
        #
        #   * `margin_deviation` is not a symbol in this codebase. The quantity
        #     that is, and the only one the engine already computes, is the
        #     `_delta_pct` the `Normalized NI` audit flag prints.
        #     `_pe_normalization_deviation` gives it a name and guards the four
        #     ways the ratio could return a number with no meaning.
        #   * the post-mortem said `normalized_ebit`. The `P/E (norm)` branch
        #     reads `normalized_net_income`, and a price-to-earnings multiple
        #     divided by EBIT would be wrong by the tax rate and the interest
        #     line. The net-income figure is used; the discrepancy is recorded
        #     rather than followed.
        #   * the swap has to reach EVERY consumer of the profile's rows, not
        #     just the blend. Measured on a forced COST swap: the blend took the
        #     promoted row and valued it correctly, while `methods_used` — built
        #     by filtering the RAW rows against a value map keyed on the
        #     promoted name — silently dropped the 40% anchor from the report.
        #     Right number, wrong attribution.
        #
        # Computed ONCE, above the scenario loop, because nothing in it depends
        # on the scenario. `_pe_norm_methods` is what the value build, the blend,
        # `methods_used`, `methods_unavailable` and the bank block's
        # `primary_anchor` must all read; `_pe_norm_flag` is the disclosure each
        # scenario appends to its own flag list.
        _pe_norm_methods: list = (profile_data or {}).get("methods") or []
        _pe_norm_flag: str = ""
        _pe_norm_dev = _pe_normalization_deviation(most_recent)
        _pe_norm_swaps = _pe_norm_leg_swaps(profile_data, _pe_norm_dev)
        # Same exclusion filter `_mid_cycle_leg_swaps`' call site applies: a
        # profile that excludes the normalized leg must not be handed it by this
        # route either, or the exclusion would be bypassed by a rename.
        _pe_norm_excluded = set((profile_data or {}).get("excluded") or [])
        _pe_norm_swaps = [s for s in _pe_norm_swaps
                          if s["to"] not in _pe_norm_excluded]
        if _pe_norm_swaps:
            _pe_norm_methods = _apply_pe_norm_swaps(
                _pe_norm_methods, _pe_norm_swaps)
            _legs_txt = ", ".join(
                f"{s['from']}→{s['to']} w={s['weight']:.2f}"
                + (" (anchor)" if s["anchor"] else "")
                for s in _pe_norm_swaps)
            # `_apply_pe_norm_swaps` also promotes a non-implementable row's
            # `proxy`, which moves blend weight without moving a leg name. Named
            # here so the flag discloses the whole change: Luxury Goods promotes
            # `Brand Val` (proxy `P/E`, w=0.05) alongside its anchor, and a flag
            # that reported only the anchor would understate what changed by a
            # row that carries real weight. Read off the PROFILE's rows, not
            # `_pe_norm_methods` — that list has already been rewritten, so its
            # proxies no longer match the mapping's keys and the clause would
            # come out empty.
            _proxy_txt = ", ".join(
                f"{m.get('name')} proxy {m.get('proxy')}→"
                f"{_PE_NORM_SWAP_LEGS[m.get('proxy')]} "
                f"w={float(m.get('weight') or 0.0):.2f}"
                for m in ((profile_data or {}).get("methods") or [])
                if isinstance(m, dict)
                and m.get("proxy") in _PE_NORM_SWAP_LEGS
                and not m.get("implementable", True))
            _pe_norm_flag = (
                f"P/E normalization: trailing net income deviates "
                f"{_pe_norm_dev:+.0%} from its 5-year norm "
                f"(|dev| > {_PE_NORM_SWAP_DEVIATION:.0%}) → {_legs_txt}"
                + (f"; {_proxy_txt}" if _proxy_txt else "")
                + ". The trailing leg and `P/E (norm)` read DIFFERENT earnings "
                  f"despite the similar names, so this is a change of earnings "
                  f"source, not a relabel.")

        # ── Growth reinvestment: the charge the flat margin omits, OBSERVED ──
        #
        # The cash-conversion gate above records a cap it does not apply, and
        # says why: "`_project_dcf` holds the margin flat while revenue
        # compounds, charging nothing for the investment that growth requires,
        # so capping the margin makes the OUTPUT look sane by breaking an INPUT.
        # That is why it improves plausibility while degrading forecast
        # accuracy. The reinvestment charge is the real fix; until it lands this
        # records what it would have done and moves nothing."
        #
        # This is that fix. It was built, wired live, and MEASURED — and the
        # measurement says it cannot ship live as specified, so it now records
        # what it would have done and moves nothing, exactly like the gate it
        # replaces. `_project_dcf` still takes the ratio and still applies the
        # deduction when handed one; the four call sites hand it `None`.
        #
        # What the live run measured on the 14 golden fixtures. Each was
        # replayed in its OWN subprocess. That is not pedantry: a shared process
        # leaks ~ten process-lifetime caches, and a first measurement taken that
        # way reported BN4_SI at +26.54% and 09988_HK at +44.49% when the true
        # figures are +0.00% and +17.40%. `test_golden_replay_is_deterministic`
        # exists because this already happened once to BN4.SI's baseline.
        #
        # Base IV moved on 9 of 14, over a range of −9.45% to +17.40%. Unmoved:
        # BN4_SI, C38U_SI, D05_SI, FCX, U96_SI — four of the five because their
        # `iv_dcf` was ALREADY None, so there was no leg for the charge to break.
        #
        #   * the sign INVERTED on two names. 09988_HK +17.40%, BABA +15.60%.
        #     The charge exists to stop a hyper-growth name projecting a free
        #     lunch, and on the two hyper-growth names in the baseline it made
        #     them dramatically MORE expensive. BABA's 12-month targets followed:
        #     base 151.25 → 166.31 (+9.96%), bear +10.37%, bull +8.50%.
        #   * the mechanism is visible in the payload, and it is the blend, not
        #     the formula. On 09988_HK and BABA: `iv_dcf` 88.09 → None and
        #     114.81 → None, `weight_dcf` 0.2778 → 0.0, `weight_multi` 0.7222 →
        #     1.0, `methods_count` 5 → 4, `tv_pct` 0.3714 → 0.0, and
        #     `methods_used` `["DCF","EV/EBITDA","P/E"]` → `["EV/EBITDA","P/E"]`.
        #     A leg that resolves non-positive is DROPPED and its weight
        #     renormalises onto the survivors. Where the DCF is the LOW leg —
        #     which is precisely where it is doing its job — removing it RAISES
        #     the blended IV. Any change that can zero a DCF leg makes a
        #     valuation less conservative, whatever its intent.
        #   * the route there is the floor, not the algebra. On every low-S/C
        #     name the deduction exceeded the base margin: 09988_HK 9.51% −
        #     10.72pp = −1.2%, BABA 9.51% − 10.96pp = −1.5%, SCHW 11.53% −
        #     16.19pp = −4.7%, FCX 5.39% − 12.94pp = −7.6%, C38U_SI 55.83% −
        #     98.04pp = −42.2%.
        #   * `forward_roic` moved on 13 of 14, going NEGATIVE on 09988_HK
        #     (−112.80%), BABA (−115.30%) and to 0.0 on SCHW (−100.0%). That is
        #     the `_y10_fcf_margin` parity edit doing its job — Gate B must judge
        #     the company the DCF models — and it fired Gate B: terminal growth
        #     zeroed on BABA base (0.03 → 0.0), FCX base (0.015 → 0.0), SCHW base
        #     (0.02 → 0.0) and 02888_HK bear (0.01 → 0.0).
        #   * MU shows the same mechanism in miniature and is the clearest
        #     argument that the DCF leg's WEIGHT is what protects a number: its
        #     `iv_dcf` fell −65.43% (34.81 → 12.03) and base IV moved only
        #     −1.94%, because that leg carries almost no weight there.
        #   * `revenue / invested_capital` is not a capital-intensity ratio for
        #     balance-sheet-heavy profiles at all. C38U_SI measures S/C = 0.063 —
        #     an S-REIT's invested capital is ~16x its revenue — and takes a
        #     +98.04pp deduction against a 0.5583 base margin. SCHW measures
        #     0.806 and takes +16.19pp. Measured across the 13 chargeable names
        #     the ratio spans 0.063 to 10.912, a factor of 173, so no single
        #     deduction bound would be tight on a retailer and loose on a REIT.
        #     On the four fixtures where it is most absurd the IV did not move,
        #     because their DCF leg was already gone — the ratio being
        #     meaningless and the ratio being harmless are not the same thing,
        #     and only the isolation of the measurement separates them.
        #   * D05_SI was not charged at all — its invested capital is
        #     unmeasurable — so coverage is 13 of 14 and the gap is silent.
        #
        # The formula is right for the population it was derived from. ONON at
        # g = 28% and S/C = 1.85 deducts 11.8pp, which is what the brief asked
        # for. What is missing is a scope: a ratio that is meaningful for
        # working-capital-funded operating businesses and meaningless for
        # balance-sheet-funded ones, and a rule for what happens when the
        # deduction exceeds the margin it is deducted from. Neither is a
        # calibration constant, so neither is chosen here.
        #
        # The RATIO is scenario-invariant and comes off the balance sheet, so it
        # is resolved once here beside the P/E promotion. The DEDUCTION is not:
        # it is a function of `g_t`, so it is computed per year inside the
        # projector from the growth schedule that projector resolved. Nothing
        # here duplicates a growth path — which is why the observation records
        # the year-1 deduction the projector WOULD have levied rather than a
        # parallel estimate of it.
        #
        # `None` means the company's invested capital was not measurable, and
        # the observation is then absent rather than estimated from a default
        # ratio.
        _s_to_c = _sales_to_capital(most_recent)

        # Base runs first so its method availability gates bear/bull.
        # Review-gated industry inputs, resolved once for every scenario.
        _stmt_ccy = most_recent.get("_values_currency") or reported_currency
        _pv10_d = _backlog_d = None
        _backlog_cov = _pv10_floor_ps = None
        try:
            from src.data import industry_inputs as _ii_g
            if profile_name in _RESERVE_FLOOR_PROFILES:
                _pv10_d = _ii_g.accepted_detail(ticker, "pv10", _stmt_ccy)
                if _pv10_d and shares and shares > 0:
                    _pv10_floor_ps = (_pv10_d["value"] - (net_debt or 0.0)) / shares
            if profile_name in _BACKLOG_VISIBILITY_PROFILES:
                _backlog_d = _ii_g.accepted_detail(ticker, "backlog", _stmt_ccy)
                if _backlog_d and revenue_base and revenue_base > 0:
                    _backlog_cov = _backlog_d["value"] / revenue_base
            # Backlog-coverage DCF: the accepted figure rides on `most_recent` to
            # the leg. Resolved for any profile that DECLARES such a leg, whether
            # or not it also takes the bear-only floor above.
            _bl_legs = [m.get("name") for m in _pe_norm_methods
                        if m.get("name") in _BACKLOG_BOUNDED_METHODS | _CONTRACTED_BACKLOG_METHODS]
            if _bl_legs:
                _bl_leg_d = _backlog_d or _ii_g.accepted_detail(ticker, "backlog", _stmt_ccy)
                _bl_has = bool(_bl_leg_d and _bl_leg_d.get("value") and revenue_base and revenue_base > 0)
                if _bl_has:
                    most_recent["backlog_accepted"] = _bl_leg_d["value"]
                    most_recent["_backlog_detail"] = _bl_leg_d
                    ticker_forward_flags.append(
                        f"{_bl_legs[0]}: years 1-3 of revenue growth bounded by accepted backlog "
                        f"({_bl_leg_d['value'] / revenue_base:.2f}x of revenue, {_bl_leg_d.get('period')}"
                        + (f", book-to-bill {_bl_leg_d['book_to_bill']:.2f}" if _bl_leg_d.get("book_to_bill") else "")
                        + ")")
                else:
                    ticker_forward_flags.append(
                        f"{_bl_legs[0]} ran UNBOUNDED: no accepted backlog figure, so it is the "
                        f"core DCF projection under another name until one is accepted")
                gate_evaluations.append({
                    "gate_id": "GATE_BACKLOG_VISIBILITY",
                    "metric": "backlog_coverage_years",
                    "raw_input_path_a": None,
                    "gated_output_path_b": (round(_bl_leg_d["value"] / revenue_base, 4) if _bl_has else None),
                    "basis": ("accepted backlog / revenue base" if _bl_has
                              else "no accepted backlog: leg ran the unbounded projection"),
                    "leg": _bl_legs[0],
                    "applied": _bl_has,
                })
        except Exception:                                  # noqa: BLE001
            _pv10_d = _backlog_d = None

        for scenario in ("base", "bear", "bull"):
            # Prefer analyst-dispersion-based growth when available (Feature 1a).
            # Falls back to symmetric multiplier when no analyst coverage / FMP
            # doesn't return low/high for this name.
            if _analyst_bands is not None:
                g = _analyst_bands[scenario]
            else:
                g = growth_base * _g_mult[scenario]
            # Clamp growth rate: [-30%, +40%]. Upper cap reduced from 100% to
            # 40% on 2026-04-25 after observing MNDY base IV = $475 on $65
            # spot driven by 60.2% 5yr CAGR being used as forward forecast.
            # 40% is already best-in-class enterprise SaaS (NET 29%, SNOW 30%,
            # DDOG 28%). Caps don't affect typical growth rates.
            g = max(min(g, 0.40), -0.30)
            # Contracted backlog is revenue visibility: work already under
            # contract cannot fall away in the bear year. Coverage of 1.0x or
            # more floors the decline at zero; 0.4x floors it at -60%.
            if scenario == "bear" and _backlog_cov is not None and g < 0:
                _g_floor = -(1.0 - min(_backlog_cov, 1.0))
                if g < _g_floor:
                    ticker_forward_flags.append(
                        f"Bear revenue decline bounded by contracted backlog: "
                        f"{g:+.1%} -> {_g_floor:+.1%} ({_backlog_cov:.2f}x coverage of "
                        f"next-year revenue)")
                    g = _g_floor

            # Fix B — multiplicative margin variance: bear compresses base
            # margin (20% legacy / 35% high-SBC), bull expands (20% / 15%).
            # Replaces the legacy ±0.2pp/yr drift which barely moved the
            # needle for mid-margin Tech names.
            md_abs = fcf_margin_base * (_m_mult[scenario] - 1.0)
            if scenario != "bear":
                md_abs += guidance_margin_adj

            tgr = tgr_table.get(scenario, _DEFAULT_TGR[scenario])

            # ── Growth schedule: convergence fade, else tech decay ──────────
            # Built HERE, before Gate B, for two reasons.
            #
            # ORDER. The sequence is CAGR gate → seeded g_1 → fade. `g` above
            # is already the gated value (the gate runs before the scenario
            # loop), so seeding from it makes year 1 the gated figure: BN4.SI's
            # schedule starts at 5.0%, never at the raw 20.3%. Year 1 equals
            # raw consensus only when the gate did not fire.
            #
            # g_norm. The fade target is the terminal growth rate from
            # tgr_table — the engine's long-run nominal rate — read BEFORE Gate
            # B may zero `tgr`. Those are two separate statements about the
            # business and must not be conflated: the fade says growth
            # converges to the long-run rate, Gate B says this particular name
            # earns no terminal perpetuity growth. Letting Gate B's zero become
            # the fade target would stack both penalties on the same evidence.
            #
            # The two profile sets are disjoint, so the branch order is not
            # load-bearing; convergence is tested first because it is the
            # narrower claim (a named long-run target) rather than a decay rate.
            _growth_schedule: Optional[list[float]] = None
            _wacc_schedule: Optional[list[float]] = None
            if profile_name in _CONVERGENCE_ALPHA_PROFILES:
                _growth_schedule = _growth_convergence_schedule(
                    g, tgr, alpha=_CONVERGENCE_ALPHA, years=_PROJECTION_YEARS)
            elif profile_name in _GROWTH_DECAY_DELTA:
                _growth_schedule = _decayed_growth_schedule(
                    g, profile_name, years=_PROJECTION_YEARS)
            if profile_name in _EARLY_STAGE_PROFILES:
                _wacc_schedule = [
                    _staged_wacc_for_year(wacc, profile_name, y)
                    for y in range(1, _PROJECTION_YEARS + 1)
                ]

            # ── Forward Gate B: ROIC compression (Y10 projection) ──────────
            # Previously 'Forward ROIC' used TRAILING ebit/invested_capital
            # from most_recent. For scaling co's (NET: op_income -$203M,
            # trailing ROIC -1.6%), this triggered Gate B and zeroed
            # terminal value despite the company genuinely being on a path
            # to positive ROIC. Gemini review flagged this as the "Capex
            # vs OpEx trap" — infrastructure-SaaS hybrids (NET with POPs,
            # SNOW with compute) appear to destroy value by GAAP while
            # actually growing into operating leverage.
            #
            # Tier 2a fix (2026-04-25), margin form corrected 2026-09-17:
            #   Compute PROJECTED Y10 ROIC using the terminal FCF margin
            #   (fcf_margin_base + md_abs, a ONE-SHOT absolute delta — the same
            #   value handed to _project_dcf below as `margin_delta_absolute`)
            #   × yr_10_revenue / scaled invested capital — captures the
            #   "forward" aspect instead of trailing. Falls back to trailing if
            #   projection data missing. Scenario-gated thresholds (bear full
            #   gate, base half-WACC, bull never triggers) preserved from Tier 1.
            #
            #   This comment originally described the margin as
            #   `fcf_margin_base × (1 + margin_delta_per_year × 10)`, and the
            #   code below it multiplied the delta by 10 to match. The delta
            #   became a one-shot absolute applied to every year when
            #   _MARGIN_DELTA_MULT landed, but the `× 10` stayed — so Y10
            #   carried it ten times. Since md_abs = fmb·(m − 1), the buggy
            #   margin was fmb·(10m − 9) against a correct fmb·m, so the
            #   projected ROIC — linear in that margin, because _y10_ic is
            #   scaled by the same revenue multiplier as _y10_fcf — came out at
            #   (10m − 9)/m times the right answer:
            #     bear,  m = 0.80 (standard):   −1.25×  → margin −fmb, ROIC negative
            #     bear,  m = 0.65 (high SBC):   −3.85×  → margin −2.5·fmb
            #     bull,  m = 1.20 (standard):   +2.50×  → margin 3·fmb
            #     bull,  m = 1.15 (high SBC):   +2.17×  → margin 2.5·fmb
            #   Every bear case is negative, which (a) fired Gate B in the bear
            #   scenario of 13 of the 14 golden fixtures and (b) fed the
            #   growth-premium quality gate below, which reads this same
            #   `forward_roic` — forcing `_quality` to 0 (gate fully shut) in
            #   bear and pinning it at its 1.0 ceiling in bull, where the
            #   inflated margin cleared 2× WACC everywhere. Base was untouched
            #   only because md_abs is 0 there, so 10 × 0 = 0 — which is why
            #   the defect hid behind an unmoved headline IV for as long as it
            #   did.
            forward_flags: list[str] = list(ticker_forward_flags)

            # Projected Y10 ROIC — scales current invested capital with
            # projected revenue growth × asset turnover ratio, computes
            # NOPAT from terminal margin × terminal revenue.
            _forward_roic_proj = None
            # Audit fields persisted below. Initialised here because the
            # growth-premium block that assigns them is conditional, and an
            # unbound local at the payload assembly would turn "this scenario
            # never computed a sector growth average" into a NameError. `None`
            # is the honest value: it means the path did not run, which is
            # different from 0.08 (the default the block falls back to) and
            # different from a measured average.
            _sector_g_avg: Optional[float] = None
            _sector_g_avg_basis: Optional[dict] = None
            ebit_val = most_recent.get("ebit")
            ic_val = most_recent.get("invested_capital")
            rev_base = most_recent.get("revenue")
            if ic_val and ic_val > 0 and rev_base and rev_base > 0:
                # Y10 revenue from the SAME schedule the projection is handed.
                # This used to inline _GROWTH_DECAY_DELTA and rebuild a second,
                # independent growth path, which could drift from the one
                # _project_dcf actually used — and did not know about the
                # convergence fade at all.
                #
                # Worth recording that this is value-neutral for Gate B's
                # DECISION today: _y10_ic below is scaled by the very same
                # multiplier as _y10_rev, so the multiplier cancels in
                # NOPAT/IC and the "Y10 projected" ROIC does not depend on the
                # growth path it appears to project. Wiring the schedule
                # through anyway keeps the two paths from diverging silently
                # the moment that IC scaling changes.
                if _growth_schedule:
                    _y10_rev_mult = 1.0
                    for _gt in _growth_schedule:
                        _y10_rev_mult *= (1 + _gt)
                else:
                    _y10_rev_mult = (1 + g) ** 10
                _y10_rev = rev_base * _y10_rev_mult
                # Y10 FCF margin. `md_abs` is a ONE-SHOT absolute delta applied
                # to EVERY year — it is handed to _project_dcf below as
                # `margin_delta_absolute`, with `margin_delta_per_year=0.0`
                # explicitly "superseded by md_abs". So year 10 carries it once,
                # not ten times. The `* 10` this replaces was left behind when
                # the per-year drift became a one-shot delta: the Tier 2a
                # comment above still describes the old
                # `fcf_margin_base × (1 + margin_delta_per_year × 10)` form, and
                # the multiplier survived the rename of the thing it multiplied.
                # In bear, md_abs is negative (m < 1), so the `* 10` drove the
                # Y10 margin negative for every profile family — exactly −fmb at
                # m = 0.80, −2.5·fmb at the high-SBC m = 0.65 — which forced a
                # negative projected ROIC and fired Gate B, zeroing terminal
                # growth, in the bear scenario of 13 of the 14 golden fixtures.
                #
                # The floor and the cap are NOT a new clamp. `_project_dcf` —
                # called 20 lines below with this same `md_abs` — already runs
                # `margin_t = min(max(fcf_margin_base + margin_delta_absolute,
                # fcf_floor), _FCF_MARGIN_CAP)` on every projected year, off
                # this same `fcf_floor` binding. This estimate is the terminal
                # state Gate B judges, so the invariant is that it describes
                # the company the DCF actually models. Without the clamp it did
                # not: for a deeply FCF-negative name the unfloored margin
                # fabricated a terminal ROIC the cash-flow engine never
                # produces, and Gate B zeroed terminal growth on the strength of
                # a number nothing downstream ever used. MSTR is the case —
                # `fcf_margin_base = -21.0605`, so `md_abs` is POSITIVE at
                # +4.2121 and the unfloored Y10 margin is -16.85% against the
                # -5.0% Tech floor the engine runs at: a projected ROIC of
                # -11.1% where the DCF itself is running at the floor.
                # `fcf_floor` is reused rather than re-derived so the two cannot
                # drift; `_FCF_MARGIN_CAP` binds the other way, on a bull margin
                # above 0.60, for the same parity reason.
                #
                # NOT charged for reinvestment, because `_project_dcf` is not
                # either — the charge ships observation-only, see
                # GATE_GROWTH_REINVESTMENT. This is the parity obligation the
                # `_FCF_MARGIN_CAP` comment above describes, and it binds the
                # moment the charge goes live: `_project_dcf` would deduct
                # `_reinvestment_margin_deduction(_y10_g, _s_to_c)` from year 10,
                # so an estimate that did not would describe a terminal company
                # more profitable than the one being valued and Gate B would
                # judge THAT one. That is the `md_abs * 10` defect again. Measured
                # while this was wired live, it is not a small correction — it
                # drove `forward_roic` negative on 09988_HK (−112.8%), BABA
                # (−115.3%) and SCHW (−100.0%) and fired Gate B on three
                # fixtures, zeroing terminal growth on BABA, FCX and SCHW.
                # Whoever turns the charge on must add, in this order:
                #     _y10_g = (_growth_schedule[_PROJECTION_YEARS - 1]
                #               if (_growth_schedule and len(_growth_schedule)
                #                   >= _PROJECTION_YEARS) else g)
                #     ... max(fcf_margin_base + md_abs
                #             - _reinvestment_margin_deduction(_y10_g, _s_to_c),
                #         fcf_floor) ...
                # with `_y10_g` read off the same resolved schedule
                # `_y10_rev_mult` uses, under the same length condition
                # `_project_dcf` applies, so the two cannot pick different years.
                _y10_fcf_margin = min(
                    max(fcf_margin_base + md_abs, fcf_floor), _FCF_MARGIN_CAP)
                _y10_fcf = _y10_rev * _y10_fcf_margin
                # Scale invested capital proportionally with revenue
                _y10_ic = ic_val * _y10_rev_mult
                if _y10_ic > 0:
                    _y10_nopat = _y10_fcf * (1 - _EFFECTIVE_TAX_RATE)
                    _forward_roic_proj = _y10_nopat / _y10_ic

            # Use projected ROIC when available, else fall back to trailing
            if _forward_roic_proj is not None:
                forward_roic = _forward_roic_proj
                roic_source = "Y10 projected"
            elif ebit_val and ic_val and ic_val > 0:
                nopat = ebit_val * (1 - _EFFECTIVE_TAX_RATE)
                forward_roic = nopat / ic_val
                roic_source = "trailing (fallback)"
            else:
                forward_roic = None
                roic_source = "n/a"

            if forward_roic is not None:
                _gate_b_threshold = {
                    "bear": wacc,              # full gate — TGR=0 when ROIC<WACC
                    "base": wacc * 0.5,        # relaxed
                    "bull": float("-inf"),     # never triggers in bull
                }[scenario]

                if forward_roic < _gate_b_threshold:
                    tgr = 0.0
                    forward_flags.append(
                        f"Gate B ({scenario}): Forward ROIC ({forward_roic:.1%} [{roic_source}]) "
                        f"< threshold ({_gate_b_threshold:.1%}) → TGR set to 0"
                    )

            # Safety: WACC must exceed TGR
            if wacc <= tgr:
                tgr = wacc - 0.005

            # ── Option III: Tech sub-type schedules ──────────────────────
            # MOVED above Gate B. Both schedules are now built immediately
            # after `tgr` is read from tgr_table, because Gate B's Y10 revenue
            # path consumes `_growth_schedule` and because the convergence
            # fade's target must be the pre-Gate-B terminal growth rate.
            # Non-Tech (Bank / REIT / Biopharma / default) profiles still fall
            # through with None schedules → legacy constant-growth /
            # constant-WACC behavior is preserved exactly.

            # DEBUG-level log for terminal-multiple diagnostics (Option III Spec 2).
            # Only emitted for Tech sub-types so Bank/REIT paths stay quiet.
            if profile_name in _TERMINAL_MULTIPLE_CONVERGENCE:
                _log.debug(
                    "[dcf_terminal] profile=%s → terminal_mult_base=%.2fx scenario=%s mult_used=%.2fx",
                    profile_name,
                    _terminal_multiple_ev_revenue(profile_name, "base"),
                    scenario,
                    _terminal_multiple_ev_revenue(profile_name, scenario),
                )

            # ── Core DCF projection ───────────────────────────────────────
            # Per-leg inputs recorded for the Excel export (see _LEG_TRACE).
            leg_inputs: dict[str, dict] = {}
            # The same context every DCF-family leg projects with below.
            _dcf_projection = {
                "growth_schedule": _growth_schedule,
                "wacc_schedule": _wacc_schedule,
                "margin_delta_absolute": md_abs,
            }
            iv_dcf, pv_fcf, pv_tv, _proj_rows = _project_dcf(
                revenue_base=revenue_base,
                fcf_margin_base=fcf_margin_base,
                growth_rate=g,
                margin_delta_per_year=0.0,              # superseded by md_abs
                wacc=wacc,
                tgr=tgr,
                fcf_floor=fcf_floor,
                net_debt=net_debt,
                shares=shares,
                growth_schedule=_growth_schedule,
                wacc_schedule=_wacc_schedule,
                margin_delta_absolute=md_abs,
            )
            leg_inputs["DCF"] = {
                "kind": "dcf", "value": iv_dcf,
                "revenue_base": revenue_base, "fcf_margin_base": fcf_margin_base,
                "growth_base": g, "growth_schedule": _growth_schedule,
                "margin_delta_absolute": md_abs, "wacc": wacc,
                "wacc_schedule": _wacc_schedule, "tgr": tgr, "fcf_floor": fcf_floor,
                "net_debt": float(net_debt or 0.0), "shares": shares,
                "pv_fcf_per_share": pv_fcf, "pv_tv_per_share": pv_tv,
                "projection_rows": _proj_rows,
            }

            # ── Reinvestment disclosure, OBSERVATION-ONLY ─────────────────
            # Computed from the SAME helper the projector would have used, on
            # the SAME `g` the projector resolved for year 1, so the recorded
            # number is what the live run levied — not a parallel estimate of
            # it. When the charge is wired back on this expression should be
            # replaced by `_proj_rows[0]["reinvest_margin_deduction"]`, the
            # projector's own row, for the reason the `methods_used` defect
            # gives: a disclosure derived independently of the thing it
            # describes is free to describe something else. Today the projector
            # is not the thing being described.
            #
            # This is the PRE-floor figure. The live run showed why that
            # distinction is the whole story: on 09988_HK it is +10.72pp against
            # a 9.51pp base margin, so the post-floor margin was −1.2% → the
            # floor, `iv_dcf` resolved to None, the leg dropped out of the blend
            # (`weight_dcf` 0.2778 → 0.0) and base IV ROSE 17.40%, 160.84 →
            # 188.82. Reporting the pre-floor deduction without saying it
            # exceeded the margin would have made that unreadable.
            #
            # That figure is from the clean measurement, one subprocess per
            # fixture. A first probe that replayed all 14 in a single process
            # reported 44.49% here, and that number was quoted into this comment
            # before anyone noticed it was a cache-leak artifact — see the
            # measurement-error section at the top of this function's gate
            # block, and `test_golden_replay_is_deterministic`.
            # ── SCOPE AND RATIONING, both owner-specified 2026-09-18 ──────────
            # The charge is leviable only on a capital-turnover profile, and only
            # up to the headroom between the base margin and the sector floor:
            #
            #   deduction = min(g/((1+g)·(S/C)), max(fcf_margin_base − fcf_floor, 0))
            #
            # Both gates live in the helpers rather than here, so a second caller
            # cannot get the algebra without them. `_reinvest_raw` is computed
            # only to disclose what the cap removed — "capped from +98.04% to
            # +50.83%" is the fact worth publishing, and "+50.83%" on its own is
            # indistinguishable from a charge that was never larger.
            #
            # Measured at `967a3c5`, one subprocess per fixture: exactly ONE of
            # the 14 golden fixtures is in scope (MELI, `Hyper-Growth Platform`,
            # S/C 1.9968, raw +6.53% against a +30.28% base margin, cap does not
            # bind). So the scoping makes this charge inert on 13 of 14, and the
            # cap binds on ZERO of them — all seven names where it binds are out
            # of scope. Both facts are recorded in the comment at
            # `CAPITAL_TURNOVER_PROFILES` rather than left to be rediscovered.
            _reinvest_in_scope = profile_name in CAPITAL_TURNOVER_PROFILES
            _reinvest_raw = _reinvestment_margin_deduction_raw(
                g, _s_to_c, profile=profile_name)
            _reinvest_headroom = float(fcf_margin_base) - float(fcf_floor)
            _reinvest_ded = _reinvestment_margin_deduction(
                g, _s_to_c, profile=profile_name,
                margin_headroom=_reinvest_headroom)
            _reinvest_cap_binds = abs(_reinvest_raw - _reinvest_ded) > 1e-12

            if _s_to_c is None:
                _reinvest_basis = ("S/C unmeasurable — invested capital absent "
                                   "or non-positive on the row")
            elif not _reinvest_in_scope:
                _reinvest_basis = (
                    f"profile '{profile_name}' is not in "
                    f"CAPITAL_TURNOVER_PROFILES, so revenue ÷ invested capital "
                    f"is not a sales-to-capital ratio here and no deduction is "
                    f"leviable; S/C={_s_to_c:.4f}, g_yr1={g:.4f} measured for "
                    f"reference only")
            else:
                _reinvest_basis = (
                    f"S/C={_s_to_c:.4f}, g_yr1={g:.4f}, in scope"
                    + (f"; raw {_reinvest_raw:+.4f} CAPPED to the base-margin "
                       f"headroom {_reinvest_headroom:+.4f}"
                       if _reinvest_cap_binds else ""))

            if scenario == "base":
                gate_evaluations.append({
                    "gate_id": "GATE_GROWTH_REINVESTMENT",
                    "metric": "reinvestment_margin_deduction",
                    "raw_input_path_a": round(float(fcf_margin_base), 6),
                    "gated_output_path_b": round(
                        float(fcf_margin_base) - _reinvest_ded, 6),
                    "basis": _reinvest_basis,
                    # The four numbers a reader needs to see the two gates work,
                    # published rather than described. `in_scope` is the profile
                    # allowlist's verdict; `margin_headroom` is the cap; the two
                    # deductions are either side of it.
                    "in_scope": _reinvest_in_scope,
                    "deduction_uncapped": round(_reinvest_raw, 6),
                    "deduction_leviable": round(_reinvest_ded, 6),
                    "margin_headroom": round(_reinvest_headroom, 6),
                    "cap_binds": _reinvest_cap_binds,
                    # NEVER True while the charge is observation-only. Not
                    # `_s_to_c is not None`, which is what this field said when
                    # the charge was live and which conflated "measurable" with
                    # "applied" — the distinction the Phase 1.2B gate exists to
                    # make. A reader must be able to tell from this record alone
                    # that path B is a counterfactual. Nor is it `in_scope`: being
                    # leviable in principle and being levied are still different
                    # facts, and conflating them is the same error one level up.
                    "applied": False,
                })
            # Two branches, deliberately different lengths. The out-of-scope line
            # is one sentence because 13 of the 14 golden fixtures land on it and
            # a paragraph on each is prose no reader reaches the end of — the
            # opposite failure from silence, which is what the blend's dropped-leg
            # disclosure was written to avoid. The gate record above carries the
            # full basis and the reference ratio either way, so shortening the
            # flag hides nothing that is not still published.
            if _s_to_c is not None and not _reinvest_in_scope:
                forward_flags.append(
                    f"Growth reinvestment NOT leviable on this profile: "
                    f"'{profile_name}' is not a capital-turnover profile, so "
                    f"revenue ÷ invested capital ({_s_to_c:.2f}) is not a "
                    f"sales-to-capital ratio here and no deduction is computed. "
                    f"Measured for reference only."
                )
            # 5bp is a REPORTING threshold on the prose, not a change detector:
            # below it the sentence would name a charge too small to read. The
            # gate record above carries the unrounded value either way, so
            # nothing is hidden by the flag staying quiet.
            elif _s_to_c is not None and abs(_reinvest_ded) >= 0.0005:
                forward_flags.append(
                    f"Growth reinvestment NOT charged (observation only): at "
                    f"S/C {_s_to_c:.2f} (revenue ÷ invested capital) and "
                    f"g={g:.1%}, a reinvestment charge would take "
                    f"{_reinvest_ded:+.2%} off the Yr-1 FCF margin, "
                    f"{fcf_margin_base:.2%} → "
                    f"{float(fcf_margin_base) - _reinvest_ded:.2%}. The "
                    f"published margin is {fcf_margin_base:.2%} before the "
                    f"scenario delta, with no reinvestment deduction. "
                    f"Two-sided by construction — a shrinking year releases "
                    f"capital and raises the margin."
                    + (f" The raw charge of {_reinvest_raw:+.2%} was CAPPED at "
                       f"the base-margin headroom {_reinvest_headroom:+.2%} "
                       f"(base {fcf_margin_base:.2%} less the sector floor "
                       f"{fcf_floor:.2%}), so the figure above is the capped "
                       f"one and not the algebra's."
                       if _reinvest_cap_binds else "")
                    # Reachable only when the sector floor is NEGATIVE. The cap
                    # bounds the deduction at `base − floor`, which exceeds `base`
                    # exactly when `floor < 0` — so this clause used to be able to
                    # fire on any name and now cannot fire on one whose floor is
                    # zero or positive. Kept rather than deleted as unreachable,
                    # because MELI's floor is −5.00% and its headroom (+35.28%)
                    # does exceed its base margin (+30.28%): the only in-scope
                    # fixture is also the one where this stays live.
                    + (" The deduction EXCEEDS the base margin, so a live "
                       "charge would floor the projection, and a floored DCF "
                       "leg that resolves non-positive is DROPPED from the "
                       "blend — its weight renormalises onto the surviving "
                       "legs, which is how charging more can value the company "
                       "higher. `legs_dropped` in the blend breakdown now names "
                       "the leg when that happens."
                       if abs(_reinvest_ded) > abs(fcf_margin_base) else "")
                )
            if scenario == "base":
                _base_proj_rows = _proj_rows
                _base_pv_fcf_per_share = pv_fcf
                _base_pv_tv_per_share  = pv_tv

            # Terminal value fraction (for Forward Gate A check)
            total_iv = pv_fcf + pv_tv
            tv_fraction = (pv_tv / total_iv) if total_iv > 0 else 0.0

            # ── Build per-method value map ────────────────────────────────
            # OE≤0 cascade (task #18): when no defensible owner-earnings
            # basis exists, the core DCF projection is excluded exactly like
            # any profile method — None here flows through the structural
            # gate and methods_unavailable.
            method_values: dict[str, Optional[float]] = {
                "DCF": None if _dcf_family_disabled else iv_dcf
            }
            # Phase 1.2B observation state, reset per scenario. Declared here
            # rather than inside `if profile_data:` so the path-B blend below
            # can test it unconditionally — a name with no resolved profile
            # never sets it, and reading an unbound local there would take the
            # whole ticker down.
            _cyc_rec: Optional[dict] = None
            _cyc_profile_b: Optional[dict] = None
            _cyc_values_b: Optional[dict] = None
            #: The profile's method rows as THIS scenario actually values them.
            #: A plain alias for `_pe_norm_methods`, bound once above the loop,
            #: and bound unconditionally here — outside `if profile_data:` — for
            #: the reason the `_cyc_*` trio above gives: the blend below reads it
            #: and an unbound local would take the whole ticker down.
            #:
            #: The promotion is scenario-invariant. It depends on `most_recent`
            #: (whose `normalized_net_income` was written at the normalization
            #: block long before this loop) and on `profile_data`, both of which
            #: are fixed by the time the first scenario runs. Computing it here
            #: instead would produce three identical lists and three separate
            #: chances for the flag prose, the value build and the blend to
            #: disagree about which rows are live — and "the blend used one set
            #: of rows while the disclosure named another" is precisely the
            #: defect this alias exists to make unreachable.
            _eff_profile_methods: list = _pe_norm_methods
            if _pe_norm_flag:
                forward_flags.append(_pe_norm_flag)
            if profile_data:
                methods_to_compute = set()
                for m in _eff_profile_methods:
                    if m.get("implementable", True):
                        methods_to_compute.add(m["name"])
                    elif "proxy" in m:
                        methods_to_compute.add(m["proxy"])
                        # A look-through anchor is unimplementable in general
                        # but exact for a conglomerate whose template
                        # completes, so ask for the real method too. When the
                        # look-through does not complete it returns None and
                        # the proxy already requested here stands, unchanged.
                        if m["name"] in _PER_TICKER_METHODS:
                            methods_to_compute.add(m["name"])

                # ── Growth premium: growth-vs-sector, quality-gated by ROIC ──
                # v3.20 — the v3.19 PEG-style "growth vs sector avg" heuristic
                # scaled the multiple off raw growth alone, with no regard for
                # whether that growth actually earns more than its cost of
                # capital — it could (and did) apply a bullish premium in the
                # SAME scenario where Gate B just above concluded "Forward
                # ROIC < threshold → TGR set to 0" (the DCF side of this same
                # function already decided this growth doesn't create value).
                # Two contradictory verdicts on the same growth, from two
                # disconnected mechanisms.
                #
                # First attempt at a fix (still in git history) replaced the
                # driver entirely with the excess-return spread
                # (forward_roic − wacc) / wacc. Tested against FLUT/PYPL/MOH/
                # CY6U.SI/FRSH and rejected: it let a high-ROIC, negative- or
                # low-growth business earn a growth premium on returns alone —
                # MOH tested at growth_premium 1.56x (a premium!) on -2.0%
                # growth, purely because its (normalization-inflated) ROIC
                # cleared WACC. A growth premium for negative growth is not
                # economically coherent regardless of capital efficiency.
                #
                # This version keeps growth-vs-sector as the driver (so sign
                # always tracks growth — negative/below-average growth can
                # never produce a premium) and uses the ROIC−WACC spread only
                # as a QUALITY GATE that scales how much of that growth-based
                # premium/discount to apply:
                #   forward_roic ≤ wacc  → growth isn't clearing its cost of
                #                          capital (same bar Gate B uses) →
                #                          gate fully closed, premium/discount
                #                          suppressed to neutral (1.0). Matches
                #                          Gate B's own verdict instead of
                #                          contradicting it, without ALSO
                #                          double-discounting on top of Gate
                #                          B's terminal-value zeroing.
                #   forward_roic > wacc  → quality = min(1.0, spread/wacc),
                #                          i.e. how comfortably it clears WACC,
                #                          capped at full pass-through. The
                #                          growth-based premium/discount is
                #                          scaled by this quality factor — it
                #                          can only shrink toward neutral, never
                #                          flip sign or amplify beyond the raw
                #                          growth-based figure.
                # Falls back to the ungated legacy growth-vs-sector-avg figure
                # only when forward_roic isn't computable (missing EBIT/
                # invested-capital data).
                _GROWTH_SENSITIVITY = 0.30
                # `market_cap` MUST match what the three `_compute_method_value`
                # legs pass, because this call resolves the benchmark those legs
                # are scaled by. It unlocks the size-matched "large" cohort rungs
                # in `get_regional_multiples`; without it an HK/SG name reads
                # `growth_avg` off the whole-grouping median while every leg
                # multiple is read off a size-matched peer set — the growth
                # premium compares the target against companies that are not its
                # peers, then multiplies the peers' multiples by the result.
                # Measured on production comps: 401 of the 436 groupings that
                # have both cohorts disagree, and HKSE Banks - Regional is
                # +17.11% (all) against +4.88% (large), a -12.24pp spread that
                # moves `_gp_raw_growth` from 0.788 to 1.007 — roughly +28% —
                # for a bank growing at 5%. `_gp_raw_growth` is INVERSELY
                # proportional to this average, so the whole-grouping rung
                # systematically penalises exactly the large names it is being
                # asked about.
                #
                # It now reads `resolved_mcap`, bound once upstream of every
                # peer call in this function — see the comment at its binding.
                #
                # SCOPE, measured in production rather than assumed. The first
                # version of this comment claimed US names were unaffected
                # because `_regional_peer_multiples` returns {} for them. That
                # is true of the GOLDEN FIXTURES and false of production: the
                # fixtures replay against the local comps store, which holds no
                # US rows at all, while the weekly refresh covers seven markets
                # including the US. Live SCHW resolves `industry/large n=10` at
                # +28.22% with a growth premium of 0.962/0.933, where its
                # fixture records `static/US` at 0.05 with a premium of 1.104.
                # So the alignment re-priced a US name in production and the
                # baseline reported "no valuation moved" — which was a true
                # statement about the baseline and a misleading one about the
                # change. Read the cohort off `sector_g_avg_basis` in a live
                # payload, never off a fixture, when the question is US.
                _peer_for_gp = get_sector_peer_multiples(sector, is_hk=_is_hk, profile_name=profile_name,
                                                         ticker=ticker,
                                                         market_cap=resolved_mcap)
                _sector_g_avg = _peer_for_gp.get("growth_avg", 0.08)
                _sector_g_avg_basis = (_peer_for_gp.get("_comp_basis") or {}).get("growth_avg")
                _gp_raw_growth = (
                    1.0 + _GROWTH_SENSITIVITY * (g - _sector_g_avg) / _sector_g_avg
                    if _sector_g_avg > 0.005 else 1.0
                )
                if forward_roic is not None and wacc > 0:
                    if forward_roic <= wacc:
                        _gp_raw = 1.0
                    else:
                        _quality = min(1.0, (forward_roic - wacc) / wacc)
                        _gp_raw = 1.0 + (_gp_raw_growth - 1.0) * _quality
                else:
                    _gp_raw = _gp_raw_growth

                # REIT-specific tighter cap: REIT sub-type peer multiples
                # (_REIT_SUBTYPE_MULTIPLES) already embed growth expectations —
                # data_center at 22x P/FFO already reflects AI-driven premium.
                # Stacking a wide premium band on top of sub-type multiples
                # double-counts growth (DLR at $324 vs $201 market, 61% upside
                # was the original discovery). This is a separate concern from
                # the driver formula above — it doesn't go away just because
                # the driver is now economically grounded, since the double-
                # count is in the peer TABLE, not the driver.
                _is_reit_profile = is_reit_sector(sector) or profile_name == "REIT"
                # High-multiple Tech profiles: same double-count issue as REIT
                # — _TECH_SUBTYPE_MULTIPLES already embed growth expectations.
                _is_high_mult_tech = profile_name in {
                    "Growth SaaS",
                    "Cybersecurity / Mission-Critical SaaS",
                    "Hyper-Growth Platform",
                    "High-Growth Tech / AI",
                }
                if _is_reit_profile:
                    growth_premium = max(0.85, min(1.20, _gp_raw))
                elif _is_high_mult_tech:
                    growth_premium = max(0.85, min(1.30, _gp_raw))
                else:
                    growth_premium = max(0.60, min(1.80, _gp_raw))

                # ── SBC Dilution Override ─────────────────────────────────
                # If UNFUNDED stock comp (SBC not offset by buybacks) exceeds
                # 20% of revenue, the P/E multiple is structurally inflated
                # by GAAP earnings that don't reflect real dilution cost.
                # Discount peer P/E by 15%. Common in EV / tech-heavy
                # consumer (TSLA, RIVN, LCID).
                #
                # Gated on UNFUNDED comp (v3.21), not raw SBC, to stay
                # consistent with fcf_owner_earnings above — a company
                # buying back its own SBC-driven issuance shouldn't be
                # charged twice by two SBC levers that disagree about
                # whether buybacks matter. See that comment for the full
                # Method E rationale (src/research_ideas/sw46/tragic_algebra.py).
                _sbc_discount = 1.0
                _sbc = most_recent.get("stock_based_compensation")
                _sbc_buybacks = abs(
                    most_recent.get("common_stock_repurchased")
                    or most_recent.get("share_buyback") or 0.0
                )
                if _sbc and revenue_base and revenue_base > 0:
                    _unfunded_pct = max(0.0, abs(_sbc) - _sbc_buybacks) / revenue_base
                    if _unfunded_pct > 0.20:
                        _sbc_discount = 0.85
                        forward_flags.append(
                            f"SBC Dilution: unfunded comp/Rev {_unfunded_pct:.0%} > 20% "
                            f"→ P/E discounted 15%"
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
                        if (_dcf_family_disabled
                                and method_name in _DCF_PROJECTION_FAMILY):
                            # OE≤0 cascade (task #18): no positive
                            # owner-earnings year to anchor a projection —
                            # DCF-family methods return None and
                            # _blend_methods renormalizes onto multiples.
                            method_values[method_name] = None
                        else:
                            method_values[method_name], leg_inputs[method_name] = _traced_method_value(
                                method_name=method_name,
                                projection=_dcf_projection,
                                most_recent=most_recent,
                                revenue_base=revenue_base,
                                shares=shares,
                                net_debt=net_debt,
                                # Size-matching the peer cohort is the only
                                # thing this feeds. revenue x 10 is not a
                                # market cap: for a thin-margin, high-revenue
                                # name it overstates by an order of magnitude,
                                # and it decided which comp basket a large cap
                                # was measured against. Use the real one where
                                # the price resolved.
                                market_cap=resolved_mcap,
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
                                ticker=ticker,
                                end_date=end_date,
                            )

                # ── Shadow-compute Forward P/E, Forward EV/EBITDA (Feature 1b)
                # and SOTP segments (Feature 3). Values surface in the per-method
                # table for transparency; they are only included in the blended
                # IV when the profile explicitly references them.
                _shadow_methods: list[str] = []
                if forward_consensus is not None:
                    _shadow_methods.append("Forward P/E")
                    # NOT computed at all on a balance-sheet financial. A
                    # shadow method is excluded from the blend but still
                    # surfaced in the per-method table, so "shadow" is not
                    # harmless: this is the row that published a forward
                    # EV/EBITDA of HK$730 per share for 02888.HK, a Money
                    # Center Bank. Keyed off the CLASSIFICATION rather than off
                    # `_bsf_stripped`, because a bank profile has no EV leg to
                    # strip and would otherwise keep computing it. Forward P/E
                    # stays — it is an equity multiple and needs no EV bridge.
                    if not _bsf_is_financial:
                        _shadow_methods.append("Forward EV/EBITDA")
                if most_recent.get("segment_breakdown"):
                    _shadow_methods.append("SOTP (segments)")
                    # Always shadow-compute probabilistic SOTP too; when the
                    # deep research didn't produce scenarios, the method falls
                    # back to flat growth = growth_base (attached below).
                    most_recent["_sotp_fallback_growth"] = g
                    _shadow_methods.append("SOTP 12m (probabilistic)")
                if most_recent.get("sotp_assumptions"):
                    _shadow_methods.append("SOTP (analyst)")
                for _shadow_name in _shadow_methods:
                    if _shadow_name not in method_values:
                        method_values[_shadow_name], leg_inputs[_shadow_name] = _traced_method_value(
                            method_name=_shadow_name,
                            projection=_dcf_projection,
                            most_recent=most_recent,
                            revenue_base=revenue_base,
                            shares=shares,
                            net_debt=net_debt,
                            market_cap=resolved_mcap,
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
                            ticker=ticker,
                            end_date=end_date,
                        )

                # Check excluded methods are not used
                excluded = profile_data.get("excluded", [])
                for ex in excluded:
                    method_values.pop(ex, None)

                # ── Structural availability gate (see pre-loop note) ─────
                # Base fixes the method set; bear/bull are restricted to it.
                if scenario == "base":
                    for _mn in methods_to_compute:
                        _v = method_values.get(_mn)
                        if _v is None or _v <= 0:
                            _structurally_unavailable.add(_mn)
                elif _structurally_unavailable:
                    for _mn in _structurally_unavailable:
                        method_values.pop(_mn, None)

                # ── Phase 1.2B: cyclical peak-consensus routing ───────────
                # OBSERVATION-ONLY (`applied: False`). Both paths are measured
                # and recorded; the published IV is path A, unchanged.
                #
                # Why not applied. The plan's shipping rule sends an item live
                # only when its backward test clears acceptance — hit-rate
                # ≥ 0.50, MAE no worse than legacy, and at least 10 scoreable
                # firings. Row 1.2B scores peak episodes (MU FY2018 and FY2022,
                # FCX 2021, NUE 2021) and cannot be run until Phase 3 back-fills
                # US history, because historical consensus is not archived and
                # the feed caps at five annual rows. Today it has zero scoreable
                # firings. Applying a ~20% move to MU's IV on an untested change
                # is precisely what the rule exists to prevent, and it would
                # also entangle MU with Phase 2.1's composite squash in a single
                # golden regeneration — the CHANGELOG-names-the-moves rule stops
                # working once two mechanisms move the same number at once.
                #
                # Base scenario only, matching the trigger: whether a business
                # is at a cycle top does not vary with the bear/base/bull spread
                # drawn around it, and emitting per scenario would triple-count
                # one firing in the ledger the acceptance bar is measured on.
                _cyc_rec = None
                _cyc_profile_b = None
                _cyc_values_b = None
                if scenario == "base" and profile_name in _CYCLICAL_PROFILES:
                    _peak = _peak_consensus_trigger(series, forward_consensus)
                    _swaps = [s for s in _mid_cycle_leg_swaps(profile_data, profile_name)
                              if s["to"] not in excluded]
                    _peak_fired = bool(_peak and _peak["fired"])
                    if _peak is not None or _swaps:
                        def _mv(_name: str) -> Optional[float]:
                            return _compute_method_value(
                                method_name=_name,
                                projection=_dcf_projection,
                                most_recent=most_recent,
                                revenue_base=revenue_base,
                                shares=shares,
                                net_debt=net_debt,
                                market_cap=resolved_mcap,
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
                                ticker=ticker,
                                end_date=end_date,
                            )

                        _swapped_weight = 0.0
                        for _s in _swaps:
                            # Path A is free: the trailing leg is already in
                            # method_values because the profile asked for it.
                            _s["path_a_value"] = method_values.get(_s["from"])
                            _s["path_b_value"] = _mv(_s["to"])
                            _swapped_weight += _s["weight"]
                        _total_weight = sum(
                            float(m.get("weight") or 0.0)
                            for m in (profile_data.get("methods") or [])
                            if isinstance(m, dict))
                        _swap_share = (_swapped_weight / _total_weight
                                       if _total_weight > 0 else 0.0)

                        _pb_roe = _mv("P/B-ROE (mid-cycle)") if _peak_fired else None
                        _pb_floors = list(most_recent.pop("_pb_roe_floors", []) or [])
                        if _pb_roe is not None:
                            # Surfaced in the per-method table, inert in the
                            # blend: _blend_methods iterates the PROFILE's
                            # methods, so a key it does not name is never
                            # weighted. That is what makes an observation-only
                            # leg observable at all.
                            method_values.setdefault("P/B-ROE (mid-cycle)", _pb_roe)

                        _basis_parts = []
                        if _swaps:
                            _legs = ", ".join(
                                f"{_s['from']}→{_s['to']} w={_s['weight']:.2f}"
                                + (" (anchor)" if _s["anchor"] else "")
                                for _s in _swaps)
                            _basis_parts.append(
                                f"mid-cycle legs would replace {_legs} "
                                f"({_swap_share:.1%} of profile weight). "
                                f"Normalised earnings are a 5-year IQR-filtered "
                                f"MEAN margin times current revenue, not the "
                                f"median over all available years the plan "
                                f"specifies — reusing _normalized_earnings "
                                f"rather than adding a second normaliser that "
                                f"disagrees with P/E (norm) and EV/EBITDA (norm).")
                        if _peak is not None:
                            _sigma_txt = (
                                f" and mean+{_PEAK_EPS_SIGMA_MULTIPLE:.0f}σ = "
                                f"{_peak['sigma_line']:.2f}"
                                if _peak["sigma_line"] is not None else "")
                            # A loss-making history has no positive max, so
                            # `max_line` is None and there is no multiple-of-max
                            # arm to describe -- Transocean reached this path the
                            # day Oilfield Services & Drilling became cyclical
                            # (2026-09-20) and the old f-string raised on None.
                            _max_txt = (
                                f"{_PEAK_EPS_MAX_MULTIPLE:.1f}x the "
                                f"{_peak['n_years']}-year max "
                                f"{_peak['eps_max']:.2f} = {_peak['max_line']:.2f}"
                                if _peak["max_line"] is not None else
                                f"no positive EPS in {_peak['n_years']} years, so no "
                                f"multiple-of-max line")
                            _lines_txt = (
                                f"forward EPS {_peak['eps_forward']:.2f} against "
                                f"{_max_txt}{_sigma_txt}")
                            if _peak_fired:
                                if _pb_roe is not None:
                                    _action_txt = (
                                        f" Forward legs would be suppressed and "
                                        f"P/B-ROE (mid-cycle) = "
                                        f"{_pb_roe:.2f} substituted.")
                                else:
                                    _action_txt = (
                                        " P/B-ROE (mid-cycle) is unavailable "
                                        "(no normalised net income or no positive "
                                        "equity), so the substitution has no "
                                        "replacement leg and the forward legs "
                                        "would simply be dropped.")
                                _basis_parts.append(
                                    f"peak trigger FIRED on "
                                    f"{'+'.join(_peak['arms'])}: {_lines_txt}."
                                    f"{_action_txt}")
                            else:
                                _basis_parts.append(
                                    f"peak trigger did not fire: {_lines_txt}.")
                        if _pb_floors:
                            _basis_parts.append(
                                "Owner floors bound: " + "; ".join(_pb_floors) + ".")
                        _basis_parts.append(
                            "CoE is proxied by the run's WACC: _compute_ggm_pb "
                            "resolves CoE and g from bank calibration and broker "
                            "tables that no cyclical profile has an entry in, and "
                            "WACC-as-CoE is the engine's own precedent in ROE vs "
                            "CoE. Conservative for a cyclical, whose equity beta "
                            "sits above the blended WACC it is charged.")
                        _basis_parts.append(
                            "OBSERVATION-ONLY: recorded, not applied. Backward "
                            "test row 1.2B needs Phase 3 history and has zero "
                            "scoreable firings today, so the shipping rule holds "
                            "it at applied=False pending the forward test.")

                        _cyc_rec = {
                            "gate_id": "GATE_CYCLICAL_PEAK_CONSENSUS",
                            "metric": ("midcycle_leg_weight_share" if _swaps
                                       else "peak_trigger"),
                            # The swap's decision variable is the weight share it
                            # would move, mirroring 1.2A's ev_dcf_weight_share.
                            # A pure peak firing with no swappable leg reports the
                            # trigger as a presence metric instead.
                            "raw_input_path_a": (round(_swap_share, 6) if _swaps
                                                 else (1.0 if _peak_fired else 0.0)),
                            "gated_output_path_b": 0.0,
                            "peak_trigger": _peak,
                            "legs": _swaps,
                            "path_b_leg": (
                                {"name": "P/B-ROE (mid-cycle)", "value": _pb_roe,
                                 "floors_bound": _pb_floors}
                                if _peak_fired else None),
                            "path_a_forward_legs": (
                                {k: method_values.get(k)
                                 for k in ("Forward P/E", "Forward EV/EBITDA")
                                 if method_values.get(k) is not None}
                                if _peak_fired else None),
                            "basis": " ".join(_basis_parts),
                            "applied": False,
                        }
                        gate_evaluations.append(_cyc_rec)

                        # Path B's profile and value map, for the second blend
                        # below. Built here, evaluated after the real one so
                        # base_iv_path_a is the number actually published.
                        # Starts from `_eff_profile_methods`, NOT
                        # `profile_data["methods"]`: path B is meant to differ
                        # from path A by the mid-cycle legs alone, so it has to
                        # inherit any P/E normalization path A already applied.
                        # Building off the raw profile would make the recorded
                        # A/B comparison measure two changes at once.
                        if _swaps or (_peak_fired and _pb_roe is not None):
                            _methods_b = []
                            for m in _eff_profile_methods:
                                if not isinstance(m, dict):
                                    continue
                                m2 = dict(m)
                                _to = _MID_CYCLE_LEG_SWAPS.get(str(m2.get("name") or ""))
                                if (_to and _to not in excluded
                                        and any(s["from"] == m2.get("name")
                                                for s in _swaps)):
                                    m2["name"] = _to
                                _methods_b.append(m2)
                            if _peak_fired and _pb_roe is not None:
                                # Substituted, not merely added: the forward legs
                                # are shadow rows in path A and carry no weight,
                                # so P/B-ROE takes the weight the trigger freed.
                                # With no forward leg in any cyclical profile
                                # today, that weight is zero and the leg is
                                # recorded for the forward test rather than
                                # blended — stated here because it is the one
                                # place the plan's "replace forward P/E and
                                # EV/EBITDA" reads as a reweighting and is not.
                                _methods_b = [m for m in _methods_b
                                              if m.get("name") not in
                                              ("Forward P/E", "Forward EV/EBITDA")]
                            _cyc_profile_b = {**profile_data, "methods": _methods_b}
                            _cyc_values_b = {
                                **method_values,
                                **{s["to"]: s["path_b_value"] for s in _swaps},
                            }
                            if _peak_fired and _pb_roe is not None:
                                _cyc_values_b["P/B-ROE (mid-cycle)"] = _pb_roe
                                for _fk in ("Forward P/E", "Forward EV/EBITDA"):
                                    _cyc_values_b.pop(_fk, None)

                        _flag_bits = []
                        if _swaps:
                            _flag_bits.append(
                                "mid-cycle legs " + ", ".join(
                                    f"{_s['from']}→{_s['to']}" for _s in _swaps))
                        if _peak is not None and _peak_fired:
                            # `max_line` is None for a cyclical with no profitable
                            # year: the trigger then fired through its other arm and
                            # there is no multiple-of-max line to quote. e07002b fixed
                            # the diagnostic above; this flag formatted it regardless,
                            # and Bloom Energy was the first name to reach it (Wave 2
                            # put a loss-making hardware maker on a cyclical profile).
                            _flag_bits.append(
                                f"peak trigger fired ({'+'.join(_peak['arms'])}, "
                                f"fwd EPS {_peak['eps_forward']:.2f} vs "
                                + (f"{_peak['max_line']:.2f})" if _peak["max_line"] is not None
                                   else "no profitable year to draw a line from)"))
                        if _flag_bits:
                            # `forward_flags`, NOT `ticker_forward_flags`. The
                            # ticker-level list is snapshotted per scenario at
                            # `forward_flags = list(ticker_forward_flags)` ABOVE
                            # this point, so appending there lands the flag on
                            # the NEXT scenario's published output: base — the
                            # one the trigger was actually evaluated on — would
                            # not carry it, and bear and bull would carry an
                            # observation about a scenario they never ran. The
                            # golden diff showed exactly that, which is the only
                            # reason it was caught.
                            forward_flags.append(
                                "Cyclical peak-consensus gate (observation-only, "
                                "not applied): " + "; ".join(_flag_bits)
                                + ". IV unchanged; both paths recorded for the "
                                  "forward test.")

            # ── Blended IV with C_macro, Forward Gate A, and v3.19 Composite ─
            # `_eff_profile_methods`, not `profile_data["methods"]`: identical
            # object whenever no trailing P/E leg was promoted, and the promoted
            # rows otherwise. Blending off the raw profile here would weight a
            # `P/E (norm)` value that the profile never named, and `_blend_methods`
            # resolves by row name — so the two lists have to be the same one the
            # value map was built from.
            blend_breakdown: dict = {}
            if profile_data and profile_data.get("methods"):
                blended_iv, blend_breakdown = _blend_methods(
                    profile_methods=_eff_profile_methods,
                    method_values=method_values,
                    c_macro=c_macro,
                    forward_flags=forward_flags,
                    dcf_tv_fraction=tv_fraction,
                )
                # ── Phase 1.2B path-B blend (observation-only) ────────────
                # The plan asks that an item changing a method or target carry
                # "the method value and the base IV under BOTH paths". Phase
                # 1.2A declined to compute a second IV — "that would mean
                # running the blend twice per scenario" — and is scored instead
                # by replaying fixtures through both engines. 1.2B can afford
                # it: base scenario only, cyclical profiles only, and only when
                # the gate actually fired, so it is a handful of runs out of the
                # basket rather than every scenario of every ticker. Storing
                # both IVs is what makes the row scoreable from the ledger
                # alone, and the ledger is the only record that survives to the
                # forward test — a fixture replay cannot be reconstructed for a
                # run whose inputs were live.
                #
                # A scratch flag list, because _blend_methods appends to the one
                # it is given and passing the real list would publish path B's
                # Gate A / asset-floor flags as though path B had been applied.
                if (scenario == "base" and _cyc_rec is not None
                        and _cyc_profile_b is not None and _cyc_values_b is not None):
                    _scratch_flags: list[str] = []
                    _iv_b, _bd_b = _blend_methods(
                        profile_methods=_cyc_profile_b["methods"],
                        method_values=_cyc_values_b,
                        c_macro=c_macro,
                        forward_flags=_scratch_flags,
                        dcf_tv_fraction=tv_fraction,
                    )
                    _cyc_rec["base_iv_path_a"] = blended_iv
                    _cyc_rec["base_iv_path_b"] = _iv_b
                    _cyc_rec["path_b_breakdown"] = _bd_b
                    _cyc_rec["path_b_flags_suppressed"] = _scratch_flags
                    # Both are the BLEND output, before _iv_calibration_k and
                    # the 12-month target cap. Deliberate: calibration applies
                    # equally to both paths, so recording it would put a
                    # constant factor into a difference that is meant to be
                    # attributable to the leg swap alone.
                # (If the OE≤0 cascade disabled the DCF family AND every
                # multiples method also failed, blended_iv is None and this
                # falls back to the floored projection — the degradation is
                # loud via the OE≤0 forward flags + methods_unavailable.)
                final_iv = blended_iv if blended_iv is not None else iv_dcf
                #: `_eff_profile_methods`, NOT `profile_data["methods"]`, and the
                #: distinction is the whole of the disclosure. This list is built
                #: by filtering rows against `method_values`, and after a
                #: trailing-P/E promotion the value map is keyed `P/E (norm)`
                #: while the raw row is still named `P/E` — so reading the raw
                #: rows drops the promoted leg from `methods_used` entirely.
                #: Measured on a forced COST swap: the blend received
                #: `P/E (norm)` at 0.40 with a live value of 860.99 and used it,
                #: while `methods_used` came back as
                #: ['DCF', 'EV/EBITDAR', 'FCF Yield'] — a report naming three
                #: legs for a valuation computed from four, with the missing one
                #: being the 40%-weighted anchor. The IV was right and the
                #: attribution was not, which is the worse way round: an
                #: unexplained number invites a wrong correction. The same
                #: filter reads `proxy`, so `Luxury Goods`' `Brand Val` row
                #: needs the promoted proxy to be reported at all.
                methods_used = [m["name"] for m in _eff_profile_methods
                                if method_values.get(m.get("proxy", m["name"])) is not None
                                or method_values.get(m["name"]) is not None]
            else:
                # No profile found — fall back to pure DCF with C_macro scaling
                # D3: flag the degradation (once per ticker, not per scenario).
                if not _profile_fallback_used:
                    _log.warning(
                        "[dcf] %s: no valuation profile resolved — using pure "
                        "DCF fallback blend", ticker,
                    )
                _profile_fallback_used = True
                final_iv = iv_dcf * (1.0 + c_macro)
                methods_used = ["DCF (fallback)"]

            # B4: the active calibration's market bias correction.
            if _iv_calibration_k:
                final_iv = final_iv * _iv_calibration_k

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
            # R1: `guidance` already carries the structured Priority-0
            # values merged at the top of the ticker loop; it falls back to
            # the regex-only dict when the store had nothing usable.
            _mgmt = guidance or {}
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
                # `md_abs` is a ONE-SHOT absolute margin delta applied to every
                # projected year (see _project_dcf's `margin_delta_absolute`),
                # not a per-year drift. It was published under the name
                # `margin_delta_per_year` from the day _MARGIN_DELTA_MULT
                # landed, and two consumers read the name rather than the
                # contract and multiplied it by t: Gate B's Y10 margin above,
                # and pdf_report's sensitivity grids. The accurate key is now
                # authoritative. The old key is still written, carrying the
                # identical value, because every archived run in web_runs and
                # every deployed frontend/PDF build reads it — it is a
                # read-compatibility alias, not a second quantity, and a test
                # pins the two as equal so it can never drift into meaning
                # something else.
                "margin_delta_absolute": round(md_abs, 4),
                "margin_delta_per_year": round(md_abs, 4),   # deprecated alias
                "tgr":               round(tgr, 4),
                "tv_pct":            round(tv_fraction, 4),
                "methods_used":      methods_used,
                "forward_flags":     forward_flags,
                # NEW: per-method transparency fields
                "method_iv_table":   method_iv_table,
                "profile_weights":   profile_weights,
                # What each leg was computed from (metric, multiple and its
                # parts, EV bridge, DCF projection) -- the Excel export
                # rebuilds every leg from these with live formulas.
                "leg_inputs":        leg_inputs,
                "yr1_revenue":       round(yr1_revenue, 0) if yr1_revenue else None,
                "yr1_ebitda_est":    round(yr1_ebitda_est, 0) if yr1_ebitda_est else None,
                "yr1_eps_est":       round(yr1_eps_est, 4) if yr1_eps_est else None,
                "methods_count":     len(method_iv_table),
                "growth_premium":    round(growth_premium, 3) if profile_data else 1.0,
                "iv_dcf":            blend_breakdown.get("iv_dcf"),
                "iv_multi":          blend_breakdown.get("iv_multi"),
                "weight_dcf":        blend_breakdown.get("weight_dcf"),
                "weight_multi":      blend_breakdown.get("weight_multi"),
                "effective_weights": blend_breakdown.get("effective_weights"),
                # Legs in `method_iv_table` that carry no weight: published as
                # cross-checks, never as part of the blend.
                "cross_check_methods": _cross_check_methods(
                    method_iv_table, blend_breakdown.get("effective_weights")),
                # NEW: what the blend dropped, and how much of the profile's
                # intended weight actually voted. `effective_weights` lists
                # survivors only, so before these keys a leg the profile asked
                # for and the blend discarded was invisible downstream — and
                # `iv_dcf: None` could not be told apart from a DCF bucket that
                # computed and came out non-positive, which is exactly the
                # ambiguity that made the reinvestment charge's sign inversion
                # unreadable from the payload.
                #
                # `methods_surviving` is NOT `methods_count` above: that one is
                # `len(method_iv_table)`, built by intersecting raw profile row
                # names against `method_values`, so it counts names the profile
                # mentioned rather than legs that carried weight. This one
                # counts legs that voted, including the P/BV asset floor Gate A
                # adds.
                "legs_dropped":      blend_breakdown.get("legs_dropped"),
                "weight_surviving":  blend_breakdown.get("weight_surviving"),
                "weight_intended":   blend_breakdown.get("weight_intended"),
                "methods_surviving": blend_breakdown.get("methods_surviving"),
                "single_method":     blend_breakdown.get("single_method"),
                # ── Audit fields (item 3b) ─────────────────────────────────
                # These three drove two gates and were recoverable only by
                # regex-ing them back out of `forward_flags` prose, which is how
                # the Gate B `md_abs * 10` defect was finally measured: a
                # production census parsing "Forward ROIC (-7.3% [Y10
                # projected])" out of a sentence. Confirmed absent from live
                # production payloads (null on four fresh post-fix rows for
                # SCHW/V/MELI/MSTR). A value that decides whether terminal
                # growth is zeroed has to be a field, not a substring.
                #
                # `sector_g_avg_basis` carries the cohort the average came from,
                # so the divergence documented at the `_peer_for_gp` call is
                # checkable from the payload rather than inferable from source.
                "forward_roic":      round(forward_roic, 4) if forward_roic is not None else None,
                "roic_source":       roic_source,
                "sector_g_avg":      round(_sector_g_avg, 4) if _sector_g_avg is not None else None,
                "sector_g_avg_basis": _sector_g_avg_basis,
            }

        # ── Scenario ordering invariant: bear <= base <= bull ─────────────
        # Runs here, before anything downstream reads a scenario IV, so the
        # clamp reaches every consumer rather than only the published triple:
        # the `_unanimous` same-side-of-spot test and the convergence bound
        # that build `_12m_targets`, the two-sided PT band, the HK per-share
        # note, and `base_iv` itself. Placing it after them would leave the
        # 12m targets ordered against IVs that no longer exist -- which is
        # precisely the state HEAD is in, where the ordering diagnostic a
        # thousand lines below reports a violation it cannot act on.
        #
        # The blend's own arithmetic is left intact beside the clamp:
        # `iv_multi` keeps the unclamped value, and `intrinsic_value_unclamped` is added on the
        # scenario that moved. An invariant that erases the evidence of its own
        # violation cannot be audited, and the next person reading the payload
        # would have no way to tell a clamped 5.20 from a computed one.
        # ── Reserve floor and cross-check (owner, 2026-09-20) ────────────
        _apply_reserve_floor(scenario_results, _pv10_floor_ps, _pv10_d)

        _order_rec = _enforce_scenario_ordering(scenario_results)
        if _order_rec:
            _base_pivot = _order_rec["base_iv"]
            for _cl in _order_rec["clamped"]:
                _sn_cl = _cl["scenario"]
                _scen_cl = scenario_results.get(_sn_cl)
                if not isinstance(_scen_cl, dict):
                    continue
                _scen_cl["intrinsic_value_unclamped"] = _cl["before"]
                _scen_cl["intrinsic_value"] = _cl["after"]
                _scen_cl["ordering_composition"] = (
                    _order_rec["composition"].get(_sn_cl))
            # All three scenarios, NOT `ticker_forward_flags`. That list is
            # snapshotted per scenario inside the loop and never read again
            # after it ends, so an append here reaches no payload at all -- the
            # neighbouring PT-band block documents the same trap and the
            # ordering diagnostic below it still writes to the dead list.
            for _sn_fl in _SCENARIO_ORDER:
                _sf_fl = (scenario_results.get(_sn_fl) or {}).get("forward_flags")
                if isinstance(_sf_fl, list):
                    _sf_fl.extend(_order_rec["flags"])
            _ord_msg = " | ".join(_order_rec["flags"])
            print(f"  [scenario-order] {ticker}: {_ord_msg}")
            progress.update_status(agent_id, ticker, _ord_msg)
            gate_evaluations.append({
                "gate_id": "GATE_SCENARIO_ORDERING",
                "metric": "scenario_iv_ordering",
                # The scenario that moved, before and after. Base is the pivot
                # and never moves, so one pair describes the whole clamp -- and
                # if both outer scenarios ever moved, `clamped` below carries
                # the second one rather than this field having to become a
                # vector (the same reason GATE_PT_IV_BAND records base only).
                "raw_input_path_a": _order_rec["clamped"][0]["before"],
                "gated_output_path_b": _order_rec["clamped"][0]["after"],
                "pivot": _order_rec["pivot"],
                "base_iv": _base_pivot,
                "clamped": _order_rec["clamped"],
                # The owner's step 1, answered with data: which legs voted in
                # each scenario, which of base's legs a scenario lost, and the
                # share of base's voting weight those lost legs carried. A
                # dropout-driven inversion shows a large `weight_lost_vs_base`
                # beside `single_method: true`; an inversion from genuinely
                # disordered inputs shows zero lost weight and is a different
                # bug with a different fix.
                "composition": _order_rec["composition"],
                "basis": (
                    "scenario ordering is an axiomatic property of risk, not an "
                    "output of the blend; base is the pivot and is never moved, "
                    "so the headline number is untouched and the outer scenarios "
                    "are pulled onto it"
                ),
                "applied": True,
            })

        # ── D3: profile methods the base scenario could not produce ──────
        # The profile declares a method set; if data gaps forced the blend
        # to run without some of them, name them explicitly in dcf_range so
        # a silently-degraded valuation is diagnosable from the payload.
        _methods_unavailable: list = []
        if profile_data and profile_data.get("methods"):
            _base_used = set((scenario_results.get("base") or {}).get("methods_used") or [])
            _methods_unavailable = [
                _m["name"] for _m in _pe_norm_methods
                if _m.get("name") not in _base_used
            ]

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
        calibration_error, calibration_note, calibration_record = _run_backward_gate(
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
            profile_name=profile_name,
        )

        # ── 12m Forward-Multiple Price Target ────────────────────────────────
        # Framework §7: separate from intrinsic value — market pricing via sector multiples
        # For Financials sub-types (banks, GSEs, insurance), use the profile-level entry
        # which carries sector-appropriate multiples (P/E, P/TBV) rather than EV/EBITDA.
        # market_cap must match what _compute_method_value passes, or the
        # provenance recorded in "multiples_used" describes a different
        # resolution from the one the methods actually used -- a trace that
        # cannot be trusted is worse than none, because it looks checkable.
        # It did not match: this read `or 0.0` where the legs read
        # `or revenue_base * 10`, so for any HK/SG name whose quote carries no
        # market cap the 12m target was priced off whole-grouping multiples
        # while the trace beside it claimed a size-matched set. Both now read
        # `resolved_mcap`, bound once upstream of every peer call here.
        peer = get_sector_peer_multiples(sector, is_hk=_is_hk, profile_name=profile_name,
                                         ticker=ticker,
                                         market_cap=resolved_mcap)
        if not _multiples_trace(peer).get("fields"):
            _no_peers_flag = (
                f"No peer multiples for {profile_name or sector} in this market: "
                f"every multiple below is the profile's own default, not a "
                f"measured comparable set")
            if _no_peers_flag not in ticker_forward_flags:
                ticker_forward_flags.append(_no_peers_flag)
        _12m_targets: dict[str, Optional[float]] = {}
        _12m_pt_method_label = "forward multiple (profile-specific)"
        _is_reit = sector in {"REIT", "RealEstate"} or profile_name == "REIT"
        # Banks/GSEs: EV-based methods produce nonsense because massive deposit
        # liabilities make (EV − net_debt) negative, so the 12m target takes the
        # GGM target P/B × book value path instead — that is how bank targets are
        # actually published (both the DBS and OCBC reports set TP = target P/B ×
        # FY26e BVPS) and it keeps the PT anchored to the same ROE / CoE / g
        # triplet as the GGM valuation method.
        # REITs: drop through to a dedicated P/FFO + P/AFFO branch (below) —
        # EPS × P/E is conceptually wrong because REIT GAAP earnings are
        # heavily depressed by non-cash real-estate D&A, while institutional
        # REIT PTs price on FFO / AFFO per share × sub-type multiples.
        #
        # Everything else takes the standard forward waterfall, and "everything
        # else" is decided by `_is_balance_sheet_financial` — NOT by the sector.
        # This used to read
        #     profile_name in _BANK_PROFILES or sector == "Financials"
        # which routed every Financials name onto the book-value path, including
        # fee-driven franchises with no deposit base and no meaningful tangible
        # book. Visa published a 12m target of $12.84 against a base IV of
        # $428.47 — 3.0% of its own valuation — because the GGM path needed a
        # tangible book per share, and an asset-light network's tangible book is
        # NEGATIVE (goodwill and intangibles from the acquisition of its
        # processing franchise exceed total equity), so `_compute_bank_metrics`
        # fabricated one. ICE took the same path for the same reason.
        #
        # Reusing `_bsf_is_financial` rather than re-deriving it is the point:
        # Phase 1.2A already classified this name when it stripped the EV/DCF/
        # FCF legs, so the legs the IV was built from and the method the 12m
        # target is built from now come from one answer. A name whose DCF leg was
        # stripped as a balance-sheet business is priced off book; a name that
        # kept its EV legs is priced off EV. Before this they could disagree.
        #
        # Released: Market Infrastructure, Market Infrastructure (SG), Payment
        # Networks, Real Estate Asset Manager (SG), and any Tier-2 profile that
        # measures below the customer-balance ratio (Asset Manager, Alt Asset
        # Manager, FinTech, Fintech/Stablecoin). All 16 Tier-1 profiles are
        # unchanged, including Brokerage — owner decision 4 keeps SCHW on the
        # book path and its DCF weight at 0.
        _use_pe_only = _bsf_is_financial
        # Sector growth average — used per-scenario in the loop below.
        # Pre-fix this block also computed _gp_pt and _gp_pt_reit ONCE from
        # base growth and applied uniformly to bear/base/bull. That caused
        # the bear case to receive a base-case growth premium even while
        # modelling failure — Gemini's "growth premium leak" critique.
        # Now growth_premium is computed PER SCENARIO inside the loop using
        # the scenario's own growth rate, so a bear scenario with growth
        # below sector average correctly receives a DISCOUNT (gp < 1.0).
        _sector_g_avg_pt = peer.get("growth_avg", 0.08)

        # REIT sub-type multiples (pre-computed once, used per scenario below)
        _reit_sub = None
        _reit_m = None
        _reit_mults = None
        if _is_reit:
            _reit_sub = most_recent.get("_reit_subtype") or _classify_reit_subtype(
                ticker, most_recent.get("_lookup_notes", "")
            )
            _reit_mults = _REIT_SUBTYPE_MULTIPLES.get(
                _reit_sub, _REIT_SUBTYPE_MULTIPLES["default"]
            )
            _reit_m = _compute_reit_metrics(most_recent, subtype=_reit_sub)

        for scen_name, _smult in {"bear": 0.75, "base": 1.00, "bull": 1.25}.items():
            _yr1_rev  = scenario_results[scen_name].get("yr1_revenue")
            _yr1_ebit = scenario_results[scen_name].get("yr1_ebitda_est")
            _yr1_eps  = scenario_results[scen_name].get("yr1_eps_est")
            _nd       = net_debt or 0.0
            _pt: Optional[float] = None
            # Change 7: apply ADR haircut for CNY-reporting US-listed companies
            _adr_h = peer.get("cn_adr_haircut", 1.0) if reported_currency == "CNY" else 1.0
            # ── Scenario-specific growth premium (Discrepancy C fix) ──────
            # Use this scenario's growth rate (not base_g) so the premium
            # reflects the scenario's business performance. Bear with
            # growth < sector_avg now produces a DISCOUNT (gp < 1.0).
            _scen_g = scenario_results.get(scen_name, {}).get("growth_rate", growth_base)
            if _sector_g_avg_pt > 0.005:
                _gp_raw_scen = 1.0 + 0.30 * (_scen_g - _sector_g_avg_pt) / _sector_g_avg_pt
                _gp_pt = max(0.60, min(2.50, _gp_raw_scen))
            else:
                _gp_pt = 1.0
            # REIT-specific tighter cap: REIT multiples already embed growth
            # expectations, so we cap the multiplier band more aggressively.
            _gp_pt_reit = max(0.85, min(1.20, _gp_pt))
            # ── REIT 12m PT: P/FFO primary, P/AFFO secondary (60/40 blend) ──
            # Year-1 FFO/sh ≈ current FFO/sh × (1 + growth) since REIT earnings
            # grow with NOI + D&A, tracking revenue growth closely. Same for AFFO.
            # Use sub-type-specific P/FFO and P/AFFO multiples — a data-center
            # REIT (22x/25x) prices differently from an office REIT (12x/13x).
            #
            # IMPORTANT: no growth_premium is applied on top of the multiple.
            # The sub-type multiple already embeds growth expectations
            # (data_center 22x reflects AI-driven demand; retail 14x reflects
            # the secular decline). Stacking a growth_premium would double-count
            # growth since (1+g) is ALREADY applied to FFO/sh below. The scenario
            # multiplier (_smult: 0.75/1.00/1.25) is the sole dispersion mechanism
            # — it flexes the target multiple across bear/base/bull.
            if _is_reit and _reit_m and _reit_mults:
                _ffo   = _reit_m.get("ffo")
                _affo  = _reit_m.get("affo")
                _ffo_ps_ttm  = (_ffo / shares) if (_ffo and shares and shares > 0) else None
                _affo_ps_ttm = (_affo / shares) if (_affo and shares and shares > 0) else None
                # Scenario growth on the per-share metric
                _scen_g = scenario_results[scen_name].get("growth_rate", growth_base) or growth_base
                _ffo_ps_fwd  = _ffo_ps_ttm * (1 + _scen_g)  if _ffo_ps_ttm  else None
                _affo_ps_fwd = _affo_ps_ttm * (1 + _scen_g) if _affo_ps_ttm else None
                # P/FFO × FFO/sh and P/AFFO × AFFO/sh — blended 60/40
                _pt_ffo  = (_ffo_ps_fwd  * _reit_mults["p_ffo"]  * _smult * _adr_h
                            if _ffo_ps_fwd  else None)
                _pt_affo = (_affo_ps_fwd * _reit_mults["p_affo"] * _smult * _adr_h
                            if _affo_ps_fwd else None)
                if _pt_ffo is not None and _pt_affo is not None:
                    _pt = 0.60 * _pt_ffo + 0.40 * _pt_affo
                elif _pt_ffo is not None:
                    _pt = _pt_ffo
                elif _pt_affo is not None:
                    _pt = _pt_affo
                # Last-resort fallback: GAAP EPS × REIT peer PE. Only fires when
                # we have no FFO data at all (e.g. thin filings). Preserves old
                # behavior for tickers where _compute_reit_metrics returns empty.
                elif _yr1_eps and _yr1_eps > 0:
                    _pt = _yr1_eps * peer.get("pe", 35.0) * _smult * _adr_h * _gp_pt
                if _pt is not None:
                    _pt = round(max(_pt, 0.0), 2)
            elif _use_pe_only:
                # ── Bank / financial 12m PT ──────────────────────────────
                # Primary: Gordon Growth target P/B x book value per share.
                # This is how bank price targets are actually published —
                # both the DBS and OCBC reports set TP = target P/B x FY26e
                # BVPS — and it keeps the PT anchored to the same ROE / CoE
                # / g triplet as the GGM valuation method.
                #
                # NOTE: `_gp_pt` is deliberately NOT applied on the bank
                # path. It scales the multiple by revenue growth against a
                # sector average, and a bank's book multiple is a function
                # of ROE vs CoE, not top-line growth. Banks routinely grow
                # total income at low-single digits while compounding book
                # — DBS's -2.3% consensus total income against a 5% sector
                # average drove `_gp_pt` to its 0.60 floor, cutting the
                # target P/E from 11x to 6.6x. Combined with a Year-1 EPS
                # already depressed by the same bogus growth rate, that is
                # what produced a S$18.19 target on a S$75.65 share.
                _ggm_pt = _compute_ggm_pb(ticker, profile_name, most_recent, shares)
                if _ggm_pt is not None:
                    _pt = _ggm_pt[0] * _smult * _adr_h
                elif _yr1_eps and _yr1_eps > 0:
                    _pt = _yr1_eps * peer.get("pe", 12.0) * _smult * _adr_h
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
            if scen_name == "base":
                if _is_reit and _reit_m and _reit_mults:
                    _12m_pt_method_label = "P/FFO + P/AFFO blend (REIT sub-type multiples)"
                elif _use_pe_only:
                    _12m_pt_method_label = (
                        "GGM target P/B x book value per share"
                        if _compute_ggm_pb(ticker, profile_name, most_recent, shares)
                        else "forward P/E x Year-1 EPS"
                    )
                else:
                    _12m_pt_method_label = "EV/EBITDA or EV/Revenue forward multiple"

        # ── Convergence Velocity cap (Tier 2 reality-tightening) ─────────────
        # The 12m PT engine is forward-multiple-driven, decoupled from IV. On
        # high-multiple names the result is that 12m PT runs to ~90% of DCF IV
        # (MDB pre-fix: PT $493 / IV $533 = 92%) which implicitly says the
        # market should re-rate from "efficiency-watch SaaS" to "ZIRP-era
        # growth darling" inside 12 months. Industry research norm is closer
        # to 20-35% of the gap per year.
        #
        # Cap policy:
        #   high-SBC profile, no re-acceleration   → 20% of (IV - spot) per year
        #   high-SBC profile, growth re-accelerating → 30% (looser — execution earns)
        #   stable profiles (Banks, REITs, Mature SaaS, Hyperscaler) → 35%
        #     (cash flow already arriving; multiple already compressed; these
        #      earn into IV faster than aspirational growth names)
        # Re-acceleration trigger: forward growth_base > trailing CAGR × 1.10.
        #
        # Source for spot price: _spot_price captured from FMP get_prices() at
        # the top of this ticker iteration. State["current_prices"] is NOT
        # available here — portfolio_manager populates it after the DCF agent
        # completes, so reading it would always return None and the cap would
        # silently no-op. INTU 2026-04-25: bear/base/bull 12m PT ran to $804/
        # $1076/$1349 on $396 spot because cap never fired. Fix: use
        # _spot_price which IS in scope.
        _spot_for_cap = _spot_price
        # Diagnostic: emit cap state to stdout regardless of outcome so Railway
        # logs show exactly why the cap fires/doesn't (was MDB silent no-op).
        if not _spot_for_cap or float(_spot_for_cap) <= 0:
            print(
                f"  [convergence-cap] {ticker}: SKIPPED — spot price unavailable "
                f"(_spot_price={_spot_price!r})"
            )
        if _spot_for_cap and float(_spot_for_cap) > 0:
            _spot_for_cap = float(_spot_for_cap)
            _trail_cagr = _historical_cagr(series, revenue_base) or growth_base
            _is_reaccel = bool(growth_base and _trail_cagr and growth_base > _trail_cagr * 1.10)
            _high_sbc = profile_name in _HIGH_SBC_PROFILES
            if _is_reaccel and _high_sbc:
                _max_capture = 0.30
            elif _high_sbc:
                _max_capture = 0.20
            else:
                _max_capture = 0.35
            # Unanimous scenarios: when bear, base AND bull all land on the
            # same side of spot, the model is not arguing about direction,
            # only about distance, and the cap is the only thing holding the
            # target back. 09618.HK, 2026-09-15: a bear IV of HK$134 against a
            # HK$106 spot still published a HK$120 target -- 18% below the
            # run's own IV and well under the Street (GS HK$169, JPM HK$148),
            # because a third of the gap is all a 35% cap can close. A split
            # scenario set keeps the base capture: there the cap is carrying
            # genuine disagreement.
            _scen_ivs = [
                v for v in (
                    (scenario_results.get(_s) or {}).get("intrinsic_value")
                    for _s in ("bear", "base", "bull")
                ) if isinstance(v, (int, float)) and v > 0
            ]
            _unanimous = len(_scen_ivs) == 3 and (
                all(v > _spot_for_cap for v in _scen_ivs)
                or all(v < _spot_for_cap for v in _scen_ivs))
            if _unanimous:
                _max_capture = min(0.50, _max_capture + 0.15)
            _capped_any = False
            _cap_diagnostics: list[str] = []
            # SOTP-led valuation: the generic forward multiple (one peer-set
            # EV/EBITDA on consolidated EBITDA) is not what the IV was built
            # from, and on 09988.HK it put the 12m target at HK$91.58 -- 15%
            # BELOW spot -- against a HK$158.53 IV that was 47% above it, so
            # the tactical rating said SELL while the valuation said BUY. For
            # these names the target IS the convergence path toward the
            # scenario IV, in both directions.
            _sotp_led = _sotp_led_share(scenario_results.get("base") or {}) > _SOTP_LED_PT_MIN_WEIGHT
            # Normalised-earnings-led valuation: the same mismatch in a
            # different place. A cyclical anchored on through-cycle earnings
            # (P/E (norm)) gets an IV from mid-cycle EPS, while the forward
            # multiple prices NTM consensus -- peak-cycle EPS at a peak-cycle
            # peer multiple. MU, 2026-09-16: price $927.60, IV $360.24 on
            # normalised earnings, and a target of $578.91 from the forward
            # multiple, 61% of the way to IV in one year -- past any capture
            # the convergence cap allows, because that cap only tightens a
            # target on the side of spot. The target now takes the
            # convergence path toward the IV it is meant to be converging on.
            _norm_led = (
                not _sotp_led
                and "(norm)" in (_anchor_method or "")
                and _12m_pt_method_label in _FORWARD_CONSENSUS_PT_LABELS
            )
            for _sn in ("bear", "base", "bull"):
                _scen_iv = scenario_results.get(_sn, {}).get("intrinsic_value")
                _pt = _12m_targets.get(_sn)
                if _sotp_led and _scen_iv:
                    _conv = round(_convergence_bound(_scen_iv, _spot_for_cap, _max_capture), 2)
                    _cap_diagnostics.append(
                        f"{_sn}: pt {_pt!r}→${_conv:.0f} (SOTP-led: {_max_capture:.0%} of IV-spot gap)")
                    _12m_targets[_sn] = _conv
                    _pt = _conv
                    _12m_pt_method_label = (
                        f"convergence toward SOTP-led intrinsic value "
                        f"({_max_capture:.0%} of the IV-spot gap)")
                elif _norm_led and _scen_iv:
                    _conv = round(_convergence_bound(_scen_iv, _spot_for_cap, _max_capture), 2)
                    _cap_diagnostics.append(
                        f"{_sn}: pt {_pt!r}→${_conv:.0f} (normalised-earnings-led: "
                        f"{_max_capture:.0%} of IV-spot gap)")
                    _12m_targets[_sn] = _conv
                    _pt = _conv
                    _12m_pt_method_label = (
                        f"convergence toward normalised-earnings intrinsic value "
                        f"({_max_capture:.0%} of the IV-spot gap)")
                if not _scen_iv or not _pt:
                    _cap_diagnostics.append(f"{_sn}: no scen_iv/pt")
                    continue
                # ── Convergence Velocity cap (IV-gap based) ──────────────
                # The cap bounds how far a 12m target may travel from spot
                # toward that scenario's OWN intrinsic value. That bound is
                # just as meaningful when the IV sits below spot, so it is
                # applied on both sides.
                #
                # This used to be gated on `_scen_iv > spot and _pt > spot`,
                # which exempted precisely the case a bear scenario produces.
                # On 02888.HK (spot 228.60) bear IV 215.89 fell below spot, so
                # the bear target skipped the cap and kept its raw multiple of
                # 296.36 while base and bull were compressed to 238.90 and
                # 252.93 — a bear target above the bull, and the highest of
                # the three. Any name where bear IV < spot < base IV inverted
                # the same way, which is a very common configuration rather
                # than an edge case.
                #
                # Still a one-sided clamp: a target already below its
                # convergence bound is left alone (the market is free to
                # overshoot downward), so this only ever tightens a target.
                _conv_cap = _convergence_bound(_scen_iv, _spot_for_cap, _max_capture)
                if _pt > _conv_cap:
                    _12m_targets[_sn] = round(_conv_cap, 2)
                    _pt = _12m_targets[_sn]
                    _capped_any = True
                    _cap_diagnostics.append(
                        f"{_sn}: pt→${_conv_cap:.0f} (conv cap {_max_capture:.0%} of "
                        f"IV-spot gap, IV {'above' if _scen_iv >= _spot_for_cap else 'below'} spot)"
                    )
                # ── Bear floor for high-SBC names (Gemini Fix 2) ─────────
                # Even with Convergence Cap, a bear 12m PT above spot is
                # contradictory: a 'bear' should price multiple compression.
                # Force bear PT ≤ spot × 0.85 (=15% drawdown) for high-SBC.
                # Stable profiles are exempt — KO's bear case shouldn't be
                # forced to -15% since its volatility regime is different.
                if _sn == "bear" and _high_sbc:
                    _bear_floor = _spot_for_cap * _HIGH_SBC_BEAR_CEILING_MULT
                    if _pt > _bear_floor:
                        _12m_targets[_sn] = round(_bear_floor, 2)
                        _capped_any = True
                        _cap_diagnostics.append(
                            f"{_sn}: pt→${_bear_floor:.0f} (high-SBC bear floor: "
                            f"spot×0.85)"
                        )
            # Always log the cap state so it's visible in Railway logs
            print(
                f"  [convergence-cap] {ticker}: profile={profile_name!r} "
                f"high_sbc={_high_sbc} reaccel={_is_reaccel} "
                f"max_capture={_max_capture:.0%} spot=${_spot_for_cap:.2f}"
            )
            if _cap_diagnostics:
                for _d in _cap_diagnostics:
                    print(f"    • {_d}")
            if _capped_any:
                ticker_forward_flags.append(
                    f"Convergence cap: 12m PT capped at {_max_capture:.0%} of "
                    f"(IV − spot) gap ({'reaccel' if _is_reaccel else 'high-SBC' if _high_sbc else 'stable'})"
                    + (" + bear floor" if _high_sbc else "")
                )

        # ── 12m target: one rule for every name (two-tier valuation) ─────────
        # Converge from spot toward each scenario's IV. Everything computed
        # above -- forward
        # multiple, SOTP/normalised convergence, bank P/B, REIT P/FFO -- is kept
        # as a cross-check. The capture fraction is the existing one (20-35%,
        # +15pp when all three scenarios sit on the same side of spot).
        _pt_unified = False
        _pt_bridge: Optional[dict] = None
        if _spot_for_cap and float(_spot_for_cap) > 0:
            _pt_cross = {"method": _12m_pt_method_label, "targets": dict(_12m_targets)}
            _pt_rows: dict = {}
            for _sn in ("bear", "base", "bull"):
                _sr = scenario_results.get(_sn) or {}
                _siv = _sr.get("intrinsic_value")
                if not isinstance(_siv, (int, float)) or _siv <= 0:
                    continue
                _tgt = round(_convergence_bound(_siv, float(_spot_for_cap), _max_capture), 2)
                _12m_targets[_sn] = _tgt
                _pt_rows[_sn] = {"intrinsic_value": _siv, "target": _tgt}
            if _pt_rows:
                _pt_unified = True
                _12m_pt_method_label = (
                    f"convergence toward intrinsic value: {_max_capture:.0%} of "
                    f"the spot-to-IV gap")
                _pt_bridge = {
                    "rule": "target = spot + capture x (IV - spot)",
                    "spot": float(_spot_for_cap),
                    "capture": _max_capture,
                    "scenarios": _pt_rows,
                    "cross_checks": _pt_cross,
                }

        # ── 12m PT vs DCF IV divergence guard ────────────────────────────────
        # Skipped when the unified rule set the target: it guarded a target
        # computed independently of the IV, and the unified target is derived
        # from the IV and lies between spot and it by construction.
        # If the base 12m PT diverges > 100% from the base DCF IV, the forward-
        # multiple inputs are likely corrupted (e.g. EBITDA mis-parse).  Cap all
        # scenario PTs to 1.5× their corresponding DCF IVs as a safety net.
        _base_iv = scenario_results.get("base", {}).get("intrinsic_value")
        _base_pt = _12m_targets.get("base")
        if not _pt_unified and _base_iv and _base_iv > 0 and _base_pt and _base_pt > 0:
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

        # ── 12m PT two-sided band vs the IVs it converges on ─────────────────
        # Owner decision 2c (2026-09-17). Runs AFTER the divergence guard above,
        # deliberately: that guard's proportional cap (1.5x each scenario IV) is
        # the less destructive of the two responses to a target that has run
        # away upward, and it should get first refusal. What it cannot do is
        # anything about the low side, which has no bound at all. See
        # `_band_12m_targets` for the three measured deviations from the
        # instruction's literal wording and the reason for each.
        #
        # `wacc` is the cost-of-equity proxy here. A CoE is only computed for
        # balance-sheet financials (it is a GGM input), and this band has to run
        # on every profile -- the name it was written for, Visa, is a payment
        # network. WACC is the discount rate this run already used on the same
        # cash flows, so it is the one that is consistent with the IV being
        # discounted back from.
        _band_ivs = {
            _sn: (scenario_results.get(_sn) or {}).get("intrinsic_value")
            for _sn in ("bear", "base", "bull")
        }
        # `_max_capture` is only assigned inside the spot-price guard above, so
        # it is read lazily: `_spot_for_band` is truthy exactly when that guard
        # ran. Without a spot there is no convergence bound to respect either.
        _spot_for_band = (_spot_for_cap
                          if (_spot_for_cap and float(_spot_for_cap) > 0)
                          else None)
        _capture_for_band = _max_capture if _spot_for_band else None
        _high_sbc_for_band = _high_sbc if _spot_for_band else False
        _band_new, _band_breaches, _band_info = _band_12m_targets(
            _12m_targets, _band_ivs, _base_iv, wacc,
            spot=_spot_for_band, max_capture=_capture_for_band,
            high_sbc=_high_sbc_for_band,
        )
        # The band polices a target computed independently of the IV; skipped
        # under the unified rule for the same reason as the guard above.
        if _band_breaches and not _pt_unified:
            _bmsg = "; ".join(
                f"{_s} ${_p:,.2f} = {_r:.3f}x its own IV ${_iv:,.2f}"
                for _s, _p, _iv, _r in _band_breaches
            )
            _berr = (
                f"12m PT band violated: {_bmsg} — outside "
                f"[{_PT_IV_BAND_LO:.2f}x, {_PT_IV_BAND_HI:.2f}x] of the IV it is "
                f"meant to converge on"
            )
            if _band_new:
                for _sn, _bv in _band_new.items():
                    _12m_targets[_sn] = _bv
                # The label describes the method that produced the NUMBER, and
                # the number is no longer the forward multiple's. Leaving it
                # reading "EV/EBITDA or EV/Revenue forward multiple" next to a
                # value derived from the run's own IV would be a provenance
                # string that contradicts the arithmetic beside it — MELI's
                # label is exactly that string, and `_12m_pt_method` is
                # persisted, so the contradiction would outlive the run.
                _12m_pt_method_label = (
                    f"validation fallback: base IV / (1 + CoE {wacc:.2%}) x "
                    f"0.75/1.00/1.25, bounded to "
                    f"[{_PT_IV_BAND_LO:.2f}x, {_PT_IV_BAND_HI:.2f}x] of each "
                    f"scenario IV")
                _berr += (
                    f"; all three targets replaced with base IV "
                    f"${float(_base_iv):,.2f} / (1 + CoE {wacc:.2%}) x "
                    f"0.75/1.00/1.25, then bounded by the convergence cap"
                    + (f" and the high-SBC bear ceiling "
                       f"(spot ${float(_spot_for_band):,.2f} x "
                       f"{_HIGH_SBC_BEAR_CEILING_MULT:.2f})"
                       if _high_sbc_for_band else "")
                    + " = "
                    + " / ".join(
                        f"{_s} ${_band_new[_s]:,.2f}"
                        for _s in ("bear", "base", "bull"))
                )
            else:
                _berr += f"; targets NOT replaced — {_band_info.get('reason')}"
            for _c in (_band_info.get("conflicts") or []):
                _berr += f"; POLICY CONFLICT: {_c} — band floor kept"
            print(f"  [12m-pt] {ticker}: {_berr}")
            # `forward_flags` on each scenario, NOT `ticker_forward_flags`.
            # The ticker-level list is snapshotted per scenario at
            # `forward_flags = list(ticker_forward_flags)` (L~8870, inside the
            # scenario loop) and is never read again after that loop ends, so an
            # append here — a thousand lines later, with all three scenarios
            # already built — reaches no payload at all. Verified, not assumed:
            # no fixture's persisted flags carry the "Convergence cap" or
            # "12m PT ordering violated" lines that the neighbouring appends
            # write to the same dead list. Writing to all three scenarios is
            # what a ticker-level flag already does by being copied into each,
            # and the band replaced all three targets, so all three carry it.
            for _sn in ("bear", "base", "bull"):
                _sf = (scenario_results.get(_sn) or {}).get("forward_flags")
                if isinstance(_sf, list):
                    _sf.append(f"⚠ VALIDATION ERROR: {_berr}")
            progress.update_status(agent_id, ticker, _berr)
            gate_evaluations.append({
                "gate_id": "GATE_PT_IV_BAND",
                "metric": "pt_over_scenario_iv",
                # The base scenario's ratio, before and after. Base is the one
                # the card leads with, and recording all three would make this
                # the only gate whose path A/B pair is a vector.
                "raw_input_path_a": _band_info.get("base_ratio_path_a"),
                "gated_output_path_b": _band_info.get("base_ratio_path_b"),
                "breaches": [
                    {"scenario": _s, "pt": _p, "scenario_iv": _iv, "ratio": round(_r, 6)}
                    for _s, _p, _iv, _r in _band_breaches
                ],
                "replaced": _band_new or None,
                "band": (_PT_IV_BAND_LO, _PT_IV_BAND_HI),
                "conflicts": _band_info.get("conflicts") or None,
                "basis": (
                    f"two-sided 12m PT band (owner decision 2c); "
                    f"fallback = base IV / (1 + CoE {wacc:.2%}) x "
                    f"0.75/1.00/1.25, capped by the convergence bound"
                ),
                "applied": bool(_band_new),
            })

        # ── Ordering diagnostic: bear ≤ base ≤ bull ──────────────────────────
        # With the convergence cap applied on both sides of spot, the targets
        # are min() of two monotonic sequences (raw multiple × 0.75/1.00/1.25,
        # and the per-scenario convergence bound), so ordering holds by
        # construction. A violation therefore means an input is disordered —
        # scenario IVs out of sequence, or a profile-specific branch above
        # producing a non-monotonic raw target. Surface it rather than
        # silently re-sorting, which would hide the upstream fault.
        _ordered = [_12m_targets.get(s) for s in ("bear", "base", "bull")]
        if all(v is not None for v in _ordered) and not (
            _ordered[0] <= _ordered[1] <= _ordered[2]
        ):
            _msg = (
                f"12m PT ordering violated: bear ${_ordered[0]:.2f} / "
                f"base ${_ordered[1]:.2f} / bull ${_ordered[2]:.2f} — "
                f"scenario IVs bear/base/bull = "
                + " / ".join(
                    f"{(scenario_results.get(s, {}).get('intrinsic_value') or 0):.2f}"
                    for s in ("bear", "base", "bull")
                )
            )
            print(f"  [12m-pt] {ticker}: {_msg}")
            ticker_forward_flags.append(_msg)

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

        # ── REIT breakdown — raw ingredients the UI reconstructs NAV/formulas from ──
        # Every field here must be either a real number or an explicit None so the
        # React panels know what to show vs. hide. Derivation formulas (NAV bridge,
        # AFFO coverage, implied cap rate, leverage) run on the frontend against
        # these ingredients so analysts can see the math, not just the answer.
        reit_breakdown: Optional[dict] = None
        _is_reit = sector in {"RealEstate", "REIT"} or "REIT" in (profile_name or "")
        if _is_reit:
            _rb_subtype = most_recent.get("_reit_subtype") or _classify_reit_subtype(
                ticker, most_recent.get("_lookup_notes", "")
            )
            _rb_mults   = _REIT_SUBTYPE_MULTIPLES.get(_rb_subtype, _REIT_SUBTYPE_MULTIPLES["default"])
            _rb_m       = _compute_reit_metrics(most_recent, subtype=_rb_subtype)
            _rb_research = (reit_metrics_all or {}).get(ticker) or {}

            _rb_total_debt = most_recent.get("total_debt")
            _rb_cash       = most_recent.get("cash_and_equivalents")
            _rb_dps_direct = most_recent.get("dividends_per_share")
            # DPU → per-share (research extractor reports in local cents)
            _rb_dps_research = None
            if "dpu_cents" in _rb_research:
                _rb_dps_research = _rb_research["dpu_cents"] / 100.0
            _rb_affo_ps_research = most_recent.get("affo_per_share_research")
            _rb_ffo_ps = (_rb_m["ffo"] / shares) if (_rb_m.get("ffo") and shares > 0) else None
            _rb_affo_ps = (_rb_m["affo"] / shares) if (_rb_m.get("affo") and shares > 0) else (
                _rb_affo_ps_research
            )
            # Cap rate: research override > peer default
            _rb_cap_rate_used = most_recent.get("cap_rate_market") or _rb_mults["cap_rate"]

            reit_breakdown = {
                "subtype":                _rb_subtype,
                # Absolute figures (for NAV Bridge + audit)
                "ffo":                    _rb_m.get("ffo"),
                "affo":                   _rb_m.get("affo"),
                "noi":                    _rb_m.get("noi"),
                "normalized_maintenance_capex": _rb_m.get("normalized_maintenance_capex"),
                "maint_capex_pct":        _rb_m.get("maint_capex_pct_used"),
                "total_debt":             _rb_total_debt,
                "cash":                   _rb_cash,
                "shares":                 shares,
                # Per-share figures (for KPI header + distribution quality)
                "ffo_per_share":          round(_rb_ffo_ps, 4) if _rb_ffo_ps else None,
                "affo_per_share":         round(_rb_affo_ps, 4) if _rb_affo_ps else None,
                "dps":                    _rb_dps_research if _rb_dps_research else _rb_dps_direct,
                # Multiples used
                "cap_rate_used":          round(_rb_cap_rate_used, 5),
                "cap_rate_peer":          round(_rb_mults["cap_rate"], 5),
                "p_ffo_peer":             _rb_mults["p_ffo"],
                "p_affo_peer":            _rb_mults["p_affo"],
                # Research overrides (may be None)
                "occupancy_rate":         _rb_research.get("occupancy_rate"),
                "wale_years":             _rb_research.get("wale_years"),
                "leverage_ratio_research": _rb_research.get("leverage_ratio"),
                "subtype_mix":            _rb_research.get("subtype_mix"),
                "geographic_mix":         _rb_research.get("geographic_mix"),
                "research_evidence":      _rb_research.get("evidence"),
                # Bridge components (frontend reconstructs but we emit for verification)
                "gross_asset_value":      (
                    round(_rb_m["noi"] / _rb_cap_rate_used, 0)
                    if (_rb_m.get("noi") and _rb_m["noi"] > 0 and _rb_cap_rate_used > 0)
                    else None
                ),
                "nav_total":              None,   # filled below when components present
                "nav_per_share":          None,
            }
            if (reit_breakdown["gross_asset_value"] is not None
                    and _rb_total_debt is not None
                    and _rb_cash is not None
                    and shares and shares > 0):
                _rb_nav = reit_breakdown["gross_asset_value"] - _rb_total_debt + _rb_cash
                reit_breakdown["nav_total"]     = round(_rb_nav, 0)
                reit_breakdown["nav_per_share"] = round(_rb_nav / shares, 2)

            # ── Historical series (7y NPI + DPU) — CLINT-style bar charts ──
            # NPI proxy: EBITDA. We intentionally emit the full series (even
            # rows with None) so the frontend can show gaps as "—" instead
            # of silently dropping years and shifting the x-axis.
            _rb_npi_hist: list[dict] = []
            _rb_dpu_hist: list[dict] = []
            for _row in series:
                _lbl = (_row.get("period") or "")[:4]   # "FY23-12-31" → "FY23" / "2024-12-31" → "2024"
                _rb_npi_hist.append({
                    "period": _lbl,
                    "value":  round(_row["ebitda"], 0) if _row.get("ebitda") else None,
                })
                _rb_dpu_hist.append({
                    "period": _lbl,
                    "value":  round(_row["dividends_per_share"], 4)
                              if _row.get("dividends_per_share") else None,
                })
            reit_breakdown["npi_history"] = _rb_npi_hist
            reit_breakdown["dpu_history"] = _rb_dpu_hist

        # ── Bank breakdown — raw ingredients the Bank UI reconstructs KPIs from ──
        # Emitted for any Financials-sector ticker OR any profile in
        # _BANK_PROFILE_CALIBRATION (covers Money Center Bank, Regional Bank,
        # Investment Bank, Asset Manager, EM Bank, Mortgage/GSE, Insurance,
        # FinTech, Money Center Bank (SG), etc.). Graceful-degradation-first —
        # every field explicitly None when unavailable so the frontend can
        # gate tile-by-tile rather than hide the whole panel.
        bank_breakdown: Optional[dict] = None
        _is_bank_profile = (
            (is_bank_sector(sector) and profile_name in _BANK_PROFILE_CALIBRATION)
            or "Bank" in (profile_name or "")
            or profile_name == "Mortgage/GSE"
        )
        if _is_bank_profile:
            _bb_m   = _compute_bank_metrics(most_recent, profile_name=profile_name)
            _bb_cfg = _bank_profile_calibration(profile_name)
            _bb_research = (bank_metrics_all or {}).get(ticker) or {}

            _bb_ni       = most_recent.get("net_income")
            _bb_equity   = most_recent.get("total_equity")
            _bb_assets   = most_recent.get("total_assets")
            _bb_bvps     = most_recent.get("book_value_per_share")
            _bb_tbv_ps   = _bb_m.get("tbv_per_share")
            _bb_roe      = _bb_m.get("roe")
            _bb_coe      = _bb_cfg["coe"]
            _bb_target_roe  = _bb_research.get("management_target_roe") or _bb_cfg["target_roe"]
            _bb_target_cet1 = _bb_cfg["target_cet1"]
            _bb_dps      = most_recent.get("dividends_per_share")
            _bb_buybacks = most_recent.get("share_buyback") or most_recent.get("common_stock_repurchased")
            _bb_buybacks = abs(_bb_buybacks) if _bb_buybacks else None

            # Fair P/TBV via Gordon-growth identity: 1 + (ROE − CoE) / CoE
            # Floor at 0.3x to prevent negative fair values when ROE << CoE
            # (distressed banks). Institutional convention is to cap display
            # at 3.0x — anything higher implies ROE > 3×CoE which usually
            # reflects short-lived cycle peaks not sustainable long-term.
            _bb_fair_ptbv = None
            _bb_fair_value = None
            _bb_rote_divergence_bps = None
            _bb_ggm_a: dict = {}
            _bb_rote = _bb_m.get("rote")
            # ONE (ROE, CoE, g) triplet for the whole bank panel. This card
            # used to resolve its own: realised ROE on total equity against
            # the profile's CoE, while the 12m PT ran the research RoTE
            # against the research CoE. Two triplets, one page — on 02888.HK
            # 0.93x (HK$151) beside 2.15x (HK$350), a 2.3x spread readers had
            # no way to reconcile. The resolver below is the repo's single
            # documented precedence (research > broker table > profile), and
            # it reports on a TANGIBLE basis, which is the basis this card's
            # TBV denominator requires — so no conversion here.
            _bb_ggm_a = _bank_ggm_assumptions(ticker, profile_name, most_recent)
            _bb_ggm_g = _bb_ggm_a.get("g") or 0.0
            _bb_ggm_roe = _bb_ggm_a.get("roe")
            _bb_coe = _bb_ggm_a.get("coe") or _bb_coe
            if _bb_ggm_roe is not None and _bb_coe and _bb_coe > 0:
                # Full Gordon Growth: P/TBV = (RoTE - g) / (CoE - g). The
                # older form, 1 + (RoTE - CoE) / CoE, is the SAME formula
                # with g pinned to zero, which systematically understates a
                # bank with any terminal growth at all — DBS came out at
                # 1.86x against the 2.51x its own 3.3% g supports, i.e. a
                # 26% haircut to fair value purely from the missing term.
                if (_bb_coe - _bb_ggm_g) > 0.005 and _bb_ggm_roe > _bb_ggm_g:
                    _bb_fair_ptbv = max(0.3, min(4.0,
                                        (_bb_ggm_roe - _bb_ggm_g) / (_bb_coe - _bb_ggm_g)))
                else:
                    # Degenerate inputs — fall back to the zero-growth form
                    # rather than emitting a diverging multiple.
                    _bb_fair_ptbv = max(0.3, min(3.0, 1.0 + (_bb_ggm_roe - _bb_coe) / _bb_coe))
                if _bb_tbv_ps and _bb_tbv_ps > 0:
                    _bb_fair_value = round(_bb_tbv_ps * _bb_fair_ptbv, 2)

            # ── Divergence guard: realised RoTE vs the RoTE being valued ────
            # The multiple is more sensitive to this input than to any other,
            # and it is the one input the engine takes on trust. On 02888.HK
            # the filings imply ~10.8% while the research leg reported 17.9%
            # — an ~8pt gap that is the entire investment case, and nothing
            # flagged it. The existing divergence check watches revenue, and
            # there it fired on a false positive (gross income vs network
            # income). Flag, never silently override: a wide gap can be a
            # genuine statutory-vs-underlying difference, a stale filing, or
            # management guidance the market does not believe.
            if _bb_rote is not None and _bb_ggm_roe is not None:
                _bb_rote_divergence_bps = round((_bb_ggm_roe - _bb_rote) * 10000, 0)
                if abs(_bb_rote_divergence_bps) > _BANK_ROTE_DIVERGENCE_BPS:
                    _msg = (
                        f"RoTE divergence: valuing at {_bb_ggm_roe:.1%} "
                        f"({(_bb_ggm_a.get('provenance') or ['?'])[0]}) vs "
                        f"{_bb_rote:.1%} realised in the filings "
                        f"({_bb_rote_divergence_bps:+.0f} bps). The P/TBV "
                        f"multiple is driven by this input — verify the basis "
                        f"(underlying vs statutory, AT1 and minorities) before "
                        f"relying on the fair value."
                    )
                    print(f"  [bank-rote] {ticker}: {_msg}")
                    ticker_forward_flags.append(_msg)

            # CET1 ratio — prefer research-sourced (directly reported by the
            # bank), fall back to the RWA-based implied estimate
            _bb_cet1 = _bb_research.get("cet1_ratio") or _bb_m.get("cet1_implied")
            _bb_cet1_buffer_bps = None
            _bb_cet1_surplus_usd = None
            if _bb_cet1 is not None:
                _bb_cet1_buffer_bps = round((_bb_cet1 - _bb_target_cet1) * 10000, 0)
                _bb_rwa = _bb_m.get("rwa_estimate")
                if _bb_rwa and _bb_rwa > 0:
                    _bb_cet1_surplus_usd = round(max(0, _bb_cet1 - _bb_target_cet1) * _bb_rwa, 0)

            # ROA — net income / total assets
            _bb_roa = (_bb_ni / _bb_assets) if (_bb_ni and _bb_assets and _bb_assets > 0) else None

            # Capital-return math: div yield + buyback yield vs latest market cap
            _bb_mcap = (_bb_research.get("_live_market_cap")) or (
                _market_cap if _market_cap else None
            )
            _bb_div_yield = None
            _bb_buyback_yield = None
            if _bb_mcap and _bb_mcap > 0:
                if _bb_dps and shares and shares > 0:
                    _bb_div_yield = (_bb_dps * shares) / _bb_mcap
                if _bb_buybacks:
                    _bb_buyback_yield = _bb_buybacks / _bb_mcap
            _bb_payout_ratio = None
            if _bb_ni and _bb_ni > 0:
                _total_payout = 0.0
                if _bb_dps and shares:
                    _total_payout += _bb_dps * shares
                if _bb_buybacks:
                    _total_payout += _bb_buybacks
                _bb_payout_ratio = _total_payout / _bb_ni

            # ── 5y history arrays — each row is either a real number or None ──
            # Frontend renders gaps as placeholder bars (not silently drops them)
            _bb_roe_hist  = []
            _bb_nim_hist  = []
            _bb_bvps_hist = []
            _bb_ppop_hist = []
            _bb_cir_hist  = []
            _bb_loans_hist = []
            for _row in series:
                _lbl = (_row.get("period") or "")[:4]
                _ri_ni    = _row.get("net_income")
                _ri_eq    = _row.get("total_equity")
                _ri_at    = _row.get("total_assets")
                _ri_rev   = _row.get("revenue")
                _ri_bvps  = _row.get("book_value_per_share")
                _ri_ii    = _row.get("interest_income")
                _ri_ie    = _row.get("interest_expense")
                _ri_oe    = _row.get("operating_expense")
                _ri_loans = _row.get("loans_receivable") or _row.get("loans_held_for_investment")
                # ROE
                _bb_roe_hist.append({
                    "period": _lbl,
                    "value":  (_ri_ni / _ri_eq) if (_ri_ni and _ri_eq and _ri_eq > 0) else None,
                })
                # NIM
                _nim_val = None
                if _ri_ii is not None and _ri_ie is not None and _ri_at and _ri_at > 0:
                    _nim_val = (_ri_ii - abs(_ri_ie)) / _ri_at
                _bb_nim_hist.append({"period": _lbl, "value": _nim_val})
                # BVPS (fall back to TBV equivalent when book_value_per_share missing)
                _bvps_val = _ri_bvps
                if _bvps_val is None and _ri_eq and shares and shares > 0:
                    _bvps_val = _ri_eq / shares
                _bb_bvps_hist.append({
                    "period": _lbl,
                    "value":  round(_bvps_val, 4) if _bvps_val else None,
                })
                # PPOP via shared helper (3-tier fallback)
                _bb_ppop_hist.append({
                    "period": _lbl,
                    "value":  round(_compute_ppop(_row), 0) if _compute_ppop(_row) else None,
                })
                # Cost / Income ratio
                _cir_val = (abs(_ri_oe) / _ri_rev) if (_ri_oe and _ri_rev and _ri_rev > 0) else None
                _bb_cir_hist.append({
                    "period": _lbl,
                    "value":  round(_cir_val, 4) if _cir_val else None,
                })
                # Loans (from FMP when present; frontend degrades to single-year
                # research-extracted loan_growth_yoy tile when absent)
                _bb_loans_hist.append({
                    "period": _lbl,
                    "value":  round(_ri_loans, 0) if _ri_loans else None,
                })

            bank_breakdown = {
                "profile":               profile_name,
                # Profile calibration (for UI labels and color-coding thresholds)
                "coe":                   _bb_coe,
                "target_roe":            _bb_target_roe,
                "target_cet1":           _bb_target_cet1,
                "fade_years":            _bb_cfg.get("fade_years"),
                # Core per-share / ratio metrics (latest year)
                "roe":                   _bb_roe,
                # Realised return on TANGIBLE equity — the like-for-like
                # comparison against ggm_roe, which is what the card values.
                "rote":                  _bb_rote,
                "rote_divergence_bps":   _bb_rote_divergence_bps,
                "roa":                   _bb_roa,
                "nim":                   _bb_m.get("nim"),
                "efficiency_ratio":      _bb_m.get("efficiency_ratio"),
                "credit_cost_ratio":     _bb_m.get("credit_cost_ratio"),
                "tbv_per_share":         _bb_tbv_ps,
                "bvps":                  _bb_bvps,
                "total_equity":          _bb_equity,
                "total_assets":          _bb_assets,
                # P/TBV Fair Value anchor (Gordon Growth)
                "fair_p_tbv":            round(_bb_fair_ptbv, 4) if _bb_fair_ptbv else None,
                "fair_value_per_share":  _bb_fair_value,
                # GGM inputs, so the card can state the basis of the multiple
                # and where each input came from (research / broker / profile).
                "ggm_terminal_growth":   _bb_ggm_a.get("g"),
                "ggm_roe":               _bb_ggm_a.get("roe"),
                "ggm_coe":               _bb_ggm_a.get("coe"),
                "ggm_provenance":        _bb_ggm_a.get("provenance"),
                "ggm_source":            _bb_ggm_a.get("source_note"),
                # Whether this profile's method set actually values the bank on
                # P/TBV. SG money-center banks exclude it deliberately ("GGM P/B
                # supersedes it and SG banks carry minimal goodwill") — DBS and
                # OCBC both carry zero goodwill, so P/TBV collapses onto P/B and
                # adds nothing. The UI still headlined a P/TBV fair value there,
                # contradicting the methodology card two panels above it. The
                # flag lets the panel drop the headline while keeping the book
                # and capital stats, which stay informative either way.
                "ptbv_excluded":         "P/TBV" in (profile_data.get("excluded") or []),
                # `_pe_norm_methods`, not the profile's own rows, for the reason
                # `methods_used` gives: this names the leg the blend anchored on,
                # and after a trailing-P/E promotion the raw row names a leg that
                # was not valued. Currently unreachable from here — the bank
                # breakdown is emitted only for `_is_bank_profile`, and none of
                # the 31 swap-eligible profiles is in Financials (measured across
                # all 99; the population is Consumer 8, Tech 5, Semiconductor 4,
                # Biopharma 3, Industrials 3, HealthcareServices 2,
                # Transportation 2, Materials 2, ProfessionalServices 2). Closed
                # anyway because "unreachable given today's profile tables" is a
                # property of the tables and not of this line, and a Financials
                # profile gaining a plain `P/E` leg would make the panel headline
                # an anchor the valuation did not use.
                "primary_anchor":        next(
                    (m["name"] for m in _pe_norm_methods
                     if m.get("anchor")), None
                ),
                # Capital adequacy
                "cet1_ratio":            _bb_cet1,
                "cet1_buffer_bps":       _bb_cet1_buffer_bps,
                "cet1_surplus_usd":      _bb_cet1_surplus_usd,
                # Capital return
                "dividend_yield":        round(_bb_div_yield, 5) if _bb_div_yield else None,
                "buyback_yield":         round(_bb_buyback_yield, 5) if _bb_buyback_yield else None,
                "total_payout_ratio":    round(_bb_payout_ratio, 4) if _bb_payout_ratio else None,
                "dps":                   _bb_dps,
                "buybacks_usd":          _bb_buybacks,
                # Research-sourced (nullable — only present when deep research extractor hit data)
                "npl_ratio":             _bb_research.get("npl_ratio"),
                "npl_coverage_ratio":    _bb_research.get("npl_coverage_ratio"),
                "net_charge_offs_pct":   _bb_research.get("net_charge_offs_pct"),
                "management_overlays_bn": _bb_research.get("management_overlays_bn"),
                "nim_rate_sensitivity_bps": _bb_research.get("nim_rate_sensitivity_bps"),
                "loan_growth_yoy":       _bb_research.get("loan_growth_yoy"),
                "deposit_growth_yoy":    _bb_research.get("deposit_growth_yoy"),
                "loan_to_deposit_ratio": _bb_research.get("loan_to_deposit_ratio"),
                "forward_loan_growth_guidance": _bb_research.get("forward_loan_growth_guidance"),
                "forward_nim_guidance":  _bb_research.get("forward_nim_guidance"),
                "research_evidence":    _bb_research.get("evidence"),
                # 5y history arrays (CLINT-style)
                "roe_history":           _bb_roe_hist,
                "nim_history":           _bb_nim_hist,
                "bvps_history":          _bb_bvps_hist,
                "ppop_history":          _bb_ppop_hist,
                "cir_history":           _bb_cir_hist,
                "loans_history":         _bb_loans_hist,
            }

        # ── Tier 1 report package for the GS-style SOTP (shadow method):
        # valuation sentence, segment table, fwd estimates, elasticities,
        # bear/bull scenario TPs and the assumption snapshot the save step
        # diffs against the previous run (New-vs-Old revisions). Lazy import
        # + try/except: a partial deploy must never take the DCF agent down
        # (precedent: 8b24603 SOTP import guard).
        sotp_breakdown = None
        _sotp_a = most_recent.get("sotp_assumptions")
        if _sotp_a:
            try:
                from src.agents.analysis.sotp_report_extras import (
                    build_sotp_breakdown,
                )
                sotp_breakdown = build_sotp_breakdown(
                    _sotp_a,
                    reporting_ccy=_output_currency,
                    shares=shares,
                    table=most_recent.get("sotp_analyst_table"),
                    net_debt=net_debt,
                    tier=_resolve_segment_tier(sector, profile_name),
                )
            except Exception as _sotp_x_err:
                _log.warning("[dcf] %s: sotp_breakdown build failed: %s",
                             ticker, _sotp_x_err)
            # Segment-level grade against the sell-side reference, where one
            # exists: a total inside the band can hide offsetting errors.
            if sotp_breakdown:
                try:
                    from src.agents.analysis.sotp_ground_truth import check_table
                    sotp_breakdown["ground_truth"] = check_table(ticker, sotp_breakdown)
                except Exception:
                    sotp_breakdown["ground_truth"] = None

            # ── Degraded SOTP disclosure ──────────────────────────────────
            # `sotp_analyst_degraded` is set when the extractor supplied
            # segments but none carried a usable forward revenue, so the SOTP
            # (analyst) method did not publish a per-share value. Before
            # 2026-09-18 `_sotp_analyst_style` returned None on that path and
            # the balance-sheet add-back went with it: on BABA, $22.3bn of
            # equity-method associates and $68bn of net cash, discarded because
            # a prose extractor could not find a revenue figure. The figures
            # survive now, and this is where they become visible in the run.
            #
            # A FLAG AND NOT AN ADD-BACK, and the reason is arithmetic rather
            # than caution. The DCF equity bridge is
            # `equity_value = pv_sum + pv_tv - (net_debt or 0.0)`, so net cash
            # is ALREADY inside the published IV for any name with negative net
            # debt -- adding the $68bn again would double count it. Associates
            # genuinely are missing, but injecting them into the DCF leg needs
            # an FX basis (the table publishes `final * fx / shares`, the DCF
            # leg publishes in the reporting currency already) and a share
            # count on the same basis as the assumptions, and getting that pair
            # wrong on a China name is the documented raw-CNY-into-a-USD-base
            # failure that once put PDD at 11.8x spot. Disclosing the dollar
            # figure is the honest step; valuing it is a separate decision that
            # needs both bases settled first.
            _sotp_deg = most_recent.get("sotp_analyst_degraded")
            if _sotp_deg:
                try:
                    _deg_assoc = float(_sotp_deg.get("associates") or 0.0)
                    _deg_nc = float(_sotp_deg.get("net_cash") or 0.0)
                    ticker_forward_flags.append(
                        f"SOTP (analyst) did not publish: "
                        f"{_sotp_deg.get('degraded_reason') or 'no usable segment revenue'}. "
                        f"Non-operating assets found but NOT valued into the "
                        f"answer -- associates ${_deg_assoc / 1e9:,.2f}bn and "
                        f"net cash ${_deg_nc / 1e9:,.2f}bn (USD, before the "
                        f"{float(_sotp_deg.get('fx_to_reporting') or 1.0):.4f} "
                        f"reporting-currency conversion). Net cash is already "
                        f"inside the DCF equity bridge, so the associates "
                        f"figure is the part genuinely missing from the "
                        f"published IV."
                    )
                except Exception:                  # never fail a run on a flag
                    pass

        # ── Model-vs-consensus sanity gate ────────────────────────────────
        # `consensus_pt` was fetched for frontend display only. It is also the
        # cheapest available check that a valuation has left the realm of the
        # arguable: the Street's own 12-month target is right there, and a
        # model many multiples above it is making a claim no analyst covering
        # the name will recognise.
        #
        # A flag, not a clamp. The model is allowed to disagree with the
        # Street — that is the point of building one — but it should say so
        # out loud rather than publishing an 8.7x divergence silently, which
        # is how MELI reached $18,401 against a $2,106 consensus without
        # anything in the run objecting.
        try:
            _cons = (_consensus_pt or {}).get("consensus")
            _base_iv_chk = (scenario_results.get("base") or {}).get(
                "intrinsic_value")
            if _cons and _base_iv_chk and float(_cons) > 0:
                _cons_mult = float(_base_iv_chk) / float(_cons)
                if _cons_mult >= _CONSENSUS_DIVERGENCE_MULT:
                    ticker_forward_flags.append(
                        f"Model vs consensus: base IV "
                        f"{_output_currency} {_base_iv_chk:,.0f} is "
                        f"{_cons_mult:.1f}x the Street 12m consensus "
                        f"{_cons:,.0f} — review the growth and margin "
                        f"assumptions before relying on this"
                    )
                elif _cons_mult <= (1.0 / _CONSENSUS_DIVERGENCE_MULT):
                    ticker_forward_flags.append(
                        f"Model vs consensus: base IV "
                        f"{_output_currency} {_base_iv_chk:,.0f} is "
                        f"{_cons_mult:.2f}x the Street 12m consensus "
                        f"{_cons:,.0f} — review before relying on this"
                    )
        except Exception:                       # never fail a run on a flag
            pass

        # ── Unrated / Pre-Revenue (owner, 2026-09-21) ─────────────────────────
        # Decided HERE, after the reserve floor, the scenario ordering clamp and
        # the consensus check have all run on real numbers, and applied by
        # REMOVING the headline figures rather than flagging them: a number that
        # is published gets quoted. The computed figures move to `indicative_iv`,
        # named for what they are. Leg-level detail (method_iv_table, leg_inputs,
        # legs_dropped) stays, because it is the evidence for the verdict.
        _rating_state = {"state": "rated"}
        try:
            from src.data import valuation_constants as _vc_unr
            _base_sr = scenario_results.get("base") or {}
            _verdict = _vc_unr.unrated_verdict(
                revenue_base=revenue_base, base_iv=_base_sr.get("intrinsic_value"),
                weight_surviving=_base_sr.get("weight_surviving"))
            if _verdict:
                _rating_state = {
                    "state": "unrated", **_verdict,
                    "weight_surviving": _base_sr.get("weight_surviving"),
                    "methods_surviving": _base_sr.get("methods_surviving"),
                    "indicative_iv": {s: (scenario_results.get(s) or {}).get("intrinsic_value")
                                      for s in ("bear", "base", "bull")},
                    "indicative_12m_targets": dict(_12m_targets or {}),
                    "note": ("Indicative figures are what the surviving legs produced. They are kept for "
                             "audit and are NOT a valuation, a target or a rating."),
                }
                for _s in ("bear", "base", "bull"):
                    if isinstance(scenario_results.get(_s), dict):
                        scenario_results[_s]["intrinsic_value"] = None
                        scenario_results[_s].setdefault("forward_flags", []).insert(
                            0, f"{_verdict['label']}: {_verdict['reason']}. No intrinsic value, "
                               f"12-month target or rating is published.")
                _12m_targets = {"bear": None, "base": None, "bull": None}
                _12m_pt_method_label = "unrated: no target published"
                _pt_bridge = None
        except Exception:                       # a verdict must never fail a run
            _rating_state = {"state": "rated"}

        dcf_range[ticker] = {
            **scenario_results,
            "rating_state":       _rating_state,
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
            "shares_source":      _shares_source,
            "revenue_base":       revenue_base,
            "fcf_margin_base":    round(fcf_margin_base, 4),
            "data_source":        data_source,
            "calibration_error":  calibration_error,
            "calibration_note":   calibration_note,
            "calibration_record": calibration_record,
            "projection_rows":    _base_proj_rows,
            "pv_fcf_base":        _base_pv_fcf_per_share,
            "pv_tv_base":         _base_pv_tv_per_share,
            # §7 of valuation framework: 12m forward-multiple price targets
            "12m_targets":        _12m_targets,
            "12m_pt_method":      _12m_pt_method_label,
            "pt_bridge":          _pt_bridge,
            # Wall Street consensus 12m PT — for "model vs consensus" sanity
            # display on the frontend. None for HK/SG or when FMP returns no
            # data. Shape: {high, low, consensus, median} or None.
            "consensus_pt":       _consensus_pt,
            # Forward test: one (Path A, Path B) pair per gate firing. An
            # empty list means no gate fired; absent means the run predates
            # instrumentation. The reconciliation worker must tell those apart.
            "gate_evaluations":   gate_evaluations,
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
            # REIT-specific breakdown — None for non-REITs so ReportPage can
            # feature-flag the REIT panels off cleanly.
            "reit_breakdown":     reit_breakdown,
            # Bank-specific breakdown — None for non-banks. Same gating pattern
            # as reit_breakdown; frontend checks `dcfRange?.bank_breakdown`.
            "bank_breakdown":     bank_breakdown,
            # GS-style SOTP report package (Tier 1) — None unless the SOTP
            # extractor produced assumptions for this ticker. Frontend checks
            # `dcfRange?.sotp_breakdown`. Rides the existing persistence chain
            # (pipeline allowlist → analysis_service → web_runs) untouched.
            "sotp_breakdown":     sotp_breakdown,
            # D3: loud-degradation metadata — True when the intended profile
            # did not resolve and a fallback path ran; list of declared
            # profile methods that produced no value in the base scenario.
            "profile_fallback_used": _profile_fallback_used,
            "methods_unavailable":   _methods_unavailable,
            # Every relative method multiplies one of these by an earnings or
            # revenue figure, so the multiple IS most of the answer -- and
            # until now a run recorded the answer without recording it. That
            # is how the 2026-09-13 comps outage survived 17 days: Hong Kong
            # names were being priced on the static US table and the output
            # looked identical to a run priced on HKSE medians. Each field
            # carries where it came from (industry / sector comp median,
            # static market table, or the US dynamic basket), the peer count
            # behind it, and how old the comp refresh was.
            "multiples_used":        _multiples_trace(peer),
            # The segment SOTP's working, lifted to the top level so the report
            # and the PDF can show what the parts are and what each was valued
            # on. Distinct from `sotp_breakdown`, which is the ANALYST SOTP
            # (BABA, 09988.HK, 09618.HK) and carries forward estimates and
            # elasticities this one has no equivalent of.
            "segment_sotp":          _segment_sotp_block(
                                         (scenario_results.get("base") or {}), shares,
                                         _output_currency),
            # B1 prediction ledger -- see _param_version / _consensus_at_run.
            "routing_trace":         {**_routing_trace,
                                      "final_sector": sector,
                                      "final_profile": profile_name},
            "consensus_at_run":      _consensus_at_run(ticker, _consensus_pt),
            # Trailing dividend per share, in the listing currency (the FX
            # block above converts per-share fields in place). The research
            # rating's 12-month total shareholder return adds it to the target.
            "dividends_per_share":   _ledger_num(most_recent.get("dividends_per_share")),
            "param_version":         (f"{_active_cal['version_id']}+{_param_version()}"
                                      if _active_cal else _param_version()),
            "calibration":           ({"version_id": _active_cal["version_id"],
                                       "iv_multiplier": _iv_calibration_k}
                                      if _active_cal else None),
            # ── Audit fields (item 3b), scenario-invariant ─────────────────
            # `normalized_net_income` is the earnings figure the normalisation
            # path substituted for a distorted reported one. Several legs read
            # it off `most_recent`; none of them recorded which value they used.
            "normalized_net_income": _ledger_num(most_recent.get("normalized_net_income")),
            # The annual history this valuation was computed from, AFTER the FX
            # conversion (so in `reported_currency`, the listing currency) and
            # with the latest quarter's balance sheet overlaid on the final
            # row where one was newer. The Excel export's Inputs sheet.
            # Discount-rate build: sector/profile base, hybrid cost-of-debt
            # adjustment, insider overlay, and the rate actually used.
            "wacc_build": {**_wacc_build, "leverage": _ledger_num(leverage),
                           "macro_regime": _risk_appetite, "wacc_final": round(wacc, 6)},
            "financials_used": {
                "currency": _output_currency,
                "source_currency": reported_currency,
                "fx_rate": round(fx_rate, 6),
                "source": "FMP annual statements",
                "balance_sheet_period": most_recent.get("_balance_sheet_period"),
                "rows": [
                    {k: (_ledger_num(r.get(k)) if k != "period" else r.get(k))
                     for k in _FINANCIALS_USED_FIELDS}
                    for r in series
                ],
            },
            "is_cache_copy":         False,
            "ledger_schema":         1,
        }

        base_iv = scenario_results["base"]["intrinsic_value"]
        cal_tag = " ⚠ CALIBRATION ERROR" if calibration_error else ""
        progress.update_status(
            agent_id, ticker,
            (f"IV base ${base_iv:.2f}" if base_iv is not None
             else f"{_rating_state.get('label', 'Unrated')}")
            + f" | profile: {profile_name} | C_macro {c_macro:+.2f} "
            f"| source: {data_source}{cal_tag}"
        )

    state["data"]["dcf_range"] = dcf_range
    return state
