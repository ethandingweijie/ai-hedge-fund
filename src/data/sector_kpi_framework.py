"""
Sector KPI Framework — single source of truth for sub-profile-specific
research prompts, extractor schemas, and downstream method consumption.

Architecture (PR #1):

  ┌─────────────────────────────┐
  │  SECTOR_KPI_FRAMEWORK dict  │  ← one entry per sub-profile (e.g. "Insurance",
  │  (this file)                │    "Growth SaaS", "REIT", "Pre-approval Biotech")
  │  - kpis: list with          │
  │      key, mandatory,        │
  │      applies_to, clamp,     │
  │      search_phrases,        │
  │      extractor_only,        │
  │      fmp_field, fallback    │
  └──────────────┬──────────────┘
                 │
       ┌─────────┴──────────┬──────────────┬──────────────────┐
       ▼                    ▼              ▼                  ▼
  render_search    build_extractor  validate_       attach_
  _overlay         _schema          extractor       overrides
  (L4 prompt       (L5 LLM         _output         (L6 dcf_agent
   text injected   schema +         (soft-           attachment loop —
   into 2F.5b)     clamps dict)     mandatory        per-key overrides
                                    flagging)        on most_recent)

Resolution order: profile_name → sector → "" (no overlay, legacy generic 2F)

Migration policy (PR #1 = Option B — low risk):
  - Insurance is the only sub-profile populated. Other sub-profiles use the
    legacy hand-written extractors (_extract_saas_metrics, _extract_bank_metrics,
    _extract_reit_metrics, _extract_pipeline_assets) which keep their behavior
    byte-identical. Migration to the framework is per-sub-profile follow-up PRs.
"""
from __future__ import annotations

import json
import re
from typing import Any

# V4-β Z-Score Engine — peer-cohort normalisation. Imported lazily in
# multiplier functions; module-level import is just to register the symbol so
# the multiplier code can do `_z_tier_kicker(z, direction=...)` without a
# per-call import overhead.
try:
    from src.data.zscore_engine import (
        z_tier_kicker as _z_tier_kicker,
        augment_metrics_with_z_scores as _augment_metrics_with_z_scores,
    )
except Exception:
    _z_tier_kicker = None
    _augment_metrics_with_z_scores = None


# ── Schema definition ────────────────────────────────────────────────────────
# Each entry under SECTOR_KPI_FRAMEWORK:
#   sector:           str                — broad sector (used for fallback lookup)
#   anchor_methods:   list[str]          — IV methods this sub-profile drives
#   kpis:             list[dict]         — per-KPI specs (see fields below)
#   source_priority:  list[str]          — citation source ranking
#
# Each KPI dict:
#   key:              str                — snake_case field name in extractor output
#   mandatory:        bool               — drives _completeness_score + UI badge
#   applies_to:       list[str]          — sub-sub-profile gate (omit = all)
#   search_phrases:   list[str]          — what the LLM should look for in research
#   compute_hint:     str                — short formula/definition for prompt
#   clamp:            (float, float)     — safe range; LLM hallucinations dropped
#   extractor_only:   bool               — True = WEB-only (LLM); False = FMP-derivable
#   decimal_format:   bool               — instruct LLM to convert % → decimal
#   fmp_field:        str (optional)     — when extractor_only=False, FMP key to read
#   fallback:         str                — human description of fallback behavior

SECTOR_KPI_FRAMEWORK: dict[str, dict] = {

    "Insurance": {
        "sector":         "Financials",
        "anchor_methods": ["Embedded Value", "P/BV", "Combined Ratio Gate"],
        # V3 quality tiers: combined ratio (operational efficiency)
        "quality_tiers": {
            "kpi_bands": [{
                "kpi": "combined_ratio", "direction": "lower_better",
                "bands": [
                    {"max": 0.88, "mult": 1.50, "label": "elite"},
                    {"max": 0.92, "mult": 1.30, "label": "top-quartile"},
                    {"max": 0.96, "mult": 1.12, "label": "above-avg"},
                    {"max": 1.02, "mult": 1.00, "label": "in-band"},
                    {"max": 99.0, "mult": 0.80, "label": "loss-making"},
                ],
            }],
            "cap": [0.70, 1.50],
        },
        # V3 risk adjustment: solvency ratio (Beta haircut)
        "risk_adjustment": {
            "kpi": "solvency_ratio_scr", "direction": "higher_better",
            "bands": [
                {"min": 2.0,  "mult": 1.10, "label": "strong"},
                {"min": 1.3,  "mult": 1.00, "label": "in-band"},
                {"min": 0.0,  "mult": 0.90, "label": "weak"},
            ],
        },
        "kpis": [
            {
                "key":             "combined_ratio",
                "mandatory":       True,
                "applies_to":      ["P&C", "Reinsurance"],
                "search_phrases":  ["combined ratio of X%", "100.X CR", "95.4 CR"],
                "compute_hint":    "P&C: losses+LAE+expenses / NEP",
                "clamp":           (0.70, 1.20),
                "extractor_only":  True,
                "decimal_format":  True,
                "fallback":        "use 0.96 P&C industry average + flag _completeness",
            },
            {
                "key":             "loss_ratio",
                "mandatory":       False,
                "applies_to":      ["P&C", "Reinsurance"],
                "search_phrases":  ["loss ratio"],
                "compute_hint":    "losses+LAE / NEP, before reserves",
                "clamp":           (0.40, 0.85),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "expense_ratio",
                "mandatory":       False,
                "applies_to":      ["P&C", "Reinsurance"],
                "search_phrases":  ["expense ratio"],
                "compute_hint":    "acquisition+admin / NEP",
                "clamp":           (0.15, 0.40),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "vnb_margin",
                "mandatory":       True,
                "applies_to":      ["Life"],
                "search_phrases":  ["VNB margin", "VNB / APE"],
                "compute_hint":    "Life: VNB / APE",
                "clamp":           (0.05, 0.40),
                "extractor_only":  True,
                "decimal_format":  True,
                "fallback":        "use 0.20 Life industry average + flag _completeness",
            },
            {
                "key":             "embedded_value_per_share",
                "mandatory":       True,
                "applies_to":      ["Life"],
                "search_phrases":  ["embedded value", "EV per share"],
                "compute_hint":    "Life: EV per share, USD or local ccy",
                "clamp":           (0.0, 1_000_000.0),
                "extractor_only":  True,
                "fallback":        "fall back to P/BV proxy (legacy behavior)",
            },
            {
                "key":             "solvency_ratio_scr",
                "mandatory":       True,
                "applies_to":      ["P&C", "Life", "Reinsurance"],
                "search_phrases":  ["SCR ratio", "RBC ratio", "Solvency II coverage"],
                "compute_hint":    "Solvency II SCR / RBC coverage (1.0 = at requirement)",
                "clamp":           (1.0, 3.0),
                "extractor_only":  True,
                "fallback":        "use 1.80 (regulatory baseline) + flag _completeness",
            },
            {
                "key":             "reserve_release_pct",
                "mandatory":       False,
                "applies_to":      ["P&C", "Reinsurance"],
                "search_phrases":  ["PYD", "prior-year development", "reserve release"],
                "compute_hint":    "PYD / earned premium (negative if adverse)",
                "clamp":           (-0.05, 0.15),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "catastrophe_losses_pct",
                "mandatory":       False,
                "applies_to":      ["P&C", "Reinsurance"],
                "search_phrases":  ["cat losses", "catastrophe loss", "pts of CR"],
                "compute_hint":    "cat losses / NEP, latest qtr",
                "clamp":           (0.0, 0.20),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "new_money_yield",
                "mandatory":       False,
                "applies_to":      ["P&C", "Life", "Reinsurance"],
                "search_phrases":  ["new money yield", "reinvestment yield"],
                "compute_hint":    "forward investment yield on newly invested money",
                "clamp":           (0.02, 0.10),
                "extractor_only":  True,
                "decimal_format":  True,
            },
        ],
        "source_priority": [
            "Q4/FY earnings call",
            "IR investor day deck",
            "10-K MD&A",
        ],
    },

    # ════════════════════════════════════════════════════════════════════
    # STAGE A entries (PR #2-#5) — additive specs for sub-profiles whose
    # extractors already work today via the legacy hand-written code path.
    #
    # These entries provide:
    #   - L4 Section 2F overlay text (richer than legacy implicit prompts)
    #   - L5 extractor schema (auto-generated from KPI list)
    #   - Backward-compatibility test: framework clamps == legacy hand-written clamps
    #
    # The legacy _extract_X_metrics functions remain the production code path
    # until Stage B (deferred) ships per-sub-profile IV equivalence tests.
    # ════════════════════════════════════════════════════════════════════

    # ── PR #2: Bank (Money Center, Regional, EM, Investment, etc.) ────────
    "Money Center Bank": {
        "sector":         "Financials",
        "anchor_methods": ["Residual Income", "P/TBV", "Excess Capital", "P/E (ops)"],
        # V3 quality tiers: efficiency_ratio + management_target_roe (correlated —
        # both proxies for general bank quality, take max-deviation)
        "quality_tiers": {
            "kpi_bands": [
                {"kpi": "efficiency_ratio", "direction": "lower_better",
                 "correlation_group": "bank_op_quality",
                 "bands": [
                     # v3.3 US-Stringent bands (per user calibration)
                     {"max": 0.50, "mult": 1.30, "label": "elite"},
                     {"max": 0.58, "mult": 1.15, "label": "strong"},
                     {"max": 0.68, "mult": 1.00, "label": "in-band"},
                     {"max": 99.0, "mult": 0.85, "label": "bloated"},
                 ]},
                {"kpi": "management_target_roe", "direction": "higher_better",
                 "correlation_group": "bank_op_quality",
                 "bands": [
                     {"min": 0.16, "mult": 1.30, "label": "premium"},
                     {"min": 0.13, "mult": 1.15, "label": "above-avg"},
                     {"min": 0.0,  "mult": 1.00, "label": "in-band"},
                 ]},
            ],
            "cap": [0.70, 1.50],
        },
        # v3.3 — recalibrated to "scale-adjusted CET1" thresholds. G-SIBs face
        # higher regulatory surcharges than Regionals but the market also
        # demands a buffer above the minimum; these tighter bands reflect what
        # actually distinguishes "fortress" from "in-band" for Money Centers.
        # JPM at ~15.0% CET1 → fortress (1.10×). Was 1.15× under prior bands.
        "risk_adjustment": {
            "kpi": "cet1_ratio", "direction": "higher_better",
            "bands": [
                {"min": 0.145, "mult": 1.10, "label": "fortress"},
                {"min": 0.130, "mult": 1.05, "label": "strong"},
                {"min": 0.115, "mult": 1.00, "label": "in-band"},
                {"min": 0.0,   "mult": 0.85, "label": "weak"},
            ],
        },
        "kpis": [
            {
                "key":             "cet1_ratio",
                "mandatory":       True,
                "search_phrases":  ["CET1", "Common Equity Tier 1"],
                "compute_hint":    "Common Equity Tier 1 ratio (regulatory capital)",
                "clamp":           (0.05, 0.25),
                "extractor_only":  True,
                "decimal_format":  True,
                "fallback":        "use cfg['target_cet1'] from _BANK_PROFILE_CALIBRATION",
            },
            {
                "key":             "nim_pct",
                "mandatory":       True,
                "search_phrases":  ["NIM", "net interest margin"],
                "compute_hint":    "Net interest margin (last quarter)",
                "clamp":           (0.005, 0.08),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "efficiency_ratio",
                "mandatory":       True,
                "search_phrases":  ["efficiency ratio", "cost-to-income"],
                "compute_hint":    "op_exp / total income (lower is better)",
                "clamp":           (0.30, 0.80),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "management_target_roe",
                "mandatory":       True,
                "search_phrases":  ["target ROE", "through-cycle ROE", "ROTCE target", "aspires to Y% ROTCE"],
                "compute_hint":    "Through-cycle ROE/ROTCE target from earnings call",
                "clamp":           (0.05, 0.25),
                "extractor_only":  True,
                "decimal_format":  True,
                "fallback":        "use cfg['target_roe'] from _BANK_PROFILE_CALIBRATION",
            },
            {
                "key":             "npl_ratio",
                "mandatory":       False,
                "search_phrases":  ["NPL", "non-performing loan"],
                "compute_hint":    "Non-performing loans as % of total loans",
                "clamp":           (0.0, 0.15),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "net_charge_offs_pct",
                "mandatory":       False,
                "search_phrases":  ["net charge-offs", "NCO"],
                "compute_hint":    "Annualized NCO / avg loans",
                "clamp":           (0.0, 0.05),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "loan_to_deposit_ratio",
                "mandatory":       False,
                "search_phrases":  ["loan-to-deposit", "LDR"],
                "compute_hint":    "Loans / deposits",
                "clamp":           (0.40, 1.20),
                "extractor_only":  True,
            },
            {
                "key":             "dividend_payout_ratio",
                "mandatory":       False,
                "search_phrases":  ["payout ratio", "dividend payout"],
                "compute_hint":    "Dividends / net income (also FMP-derivable)",
                "clamp":           (0.0, 1.0),
                "extractor_only":  True,
            },
            {
                "key":             "loan_growth_yoy",
                "mandatory":       False,
                "search_phrases":  ["loan growth", "loan book growth"],
                "compute_hint":    "YoY loan growth (decimal)",
                "clamp":           (-0.30, 0.40),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "deposit_growth_yoy",
                "mandatory":       False,
                "search_phrases":  ["deposit growth"],
                "compute_hint":    "YoY deposit growth (decimal)",
                "clamp":           (-0.30, 0.40),
                "extractor_only":  True,
                "decimal_format":  True,
            },
        ],
        "source_priority": [
            "Q4/FY earnings call",
            "10-Q regulatory capital disclosure",
            "Federal Reserve / regulator filings (FFIEC, EBA)",
        ],
    },

    # ── PR #3: REIT (single profile, sub-types via separate _REIT_SUBTYPE_MULTIPLES) ──
    "REIT": {
        "sector":         "REIT",
        "anchor_methods": ["NAV (Cap Rates)", "P/FFO", "P/AFFO", "DDM"],
        # V3 quality tiers — same-store NOI growth is THE operating signal
        # for REITs (works across self-storage, residential, retail, industrial,
        # data center). Cap rate compression is a secondary lift but mainly
        # market-driven, not operator-driven, so we lead on SS-NOI.
        "quality_tiers": {
            "kpi_bands": [
                {"kpi": "same_store_noi_growth_pct", "direction": "higher_better",
                 "bands": [
                     {"min":  0.05, "mult": 1.30, "label": "elite"},
                     {"min":  0.03, "mult": 1.15, "label": "strong"},
                     {"min":  0.01, "mult": 1.00, "label": "in-band"},
                     {"min":  0.0,  "mult": 0.95, "label": "flat"},
                     {"min": -0.99, "mult": 0.85, "label": "declining"},
                 ]},
                {"kpi": "occupancy_rate", "direction": "higher_better", "correlation_group": "reit_q",
                 "bands": [
                     {"min": 0.95, "mult": 1.15, "label": "elite"},
                     {"min": 0.90, "mult": 1.00, "label": "in-band"},
                     {"min": 0.80, "mult": 0.95, "label": "soft"},
                     {"min": 0.0,  "mult": 0.85, "label": "weak"},
                 ]},
            ],
            "cap": [0.75, 1.45],
        },
        # V3 risk adjustment — leverage_ratio is the universal REIT risk gate
        # (works for both S-REIT aggregate-leverage and US-REIT debt-to-assets
        # framing once normalised by extractor).
        "risk_adjustment": {
            "kpi": "leverage_ratio", "direction": "lower_better",
            "bands": [
                {"max": 0.30, "mult": 1.10, "label": "fortress"},
                {"max": 0.40, "mult": 1.00, "label": "in-band"},
                {"max": 0.50, "mult": 0.92, "label": "stretched"},
                {"max": 0.99, "mult": 0.80, "label": "over-levered"},
            ],
        },
        "kpis": [
            {
                "key":             "cap_rate_market",
                "mandatory":       True,
                "search_phrases":  ["cap rate", "implied cap rate", "CBRE/JLL appraisal",
                                    "stabilised cap rate", "implied stabilized yield"],
                "compute_hint":    "Portfolio weighted-avg cap rate (US-REIT: implied cap rate from EV/NOI also acceptable)",
                "clamp":           (0.02, 0.20),
                "extractor_only":  True,
                "decimal_format":  True,
                "fallback":        "use _REIT_SUBTYPE_MULTIPLES sub-type default + flag",
            },
            {
                "key":             "occupancy_rate",
                "mandatory":       True,
                "search_phrases":  ["occupancy", "same-store occupancy", "portfolio occupancy"],
                "compute_hint":    "Portfolio-weighted occupancy (US-REIT: same-store occupancy if portfolio-wide not disclosed)",
                "clamp":           (0.3, 1.0),
                "extractor_only":  True,
                "decimal_format":  True,
                "fallback":        "use 0.93 industry average + flag",
            },
            {
                "key":             "wale_years",
                "mandatory":       False,
                "search_phrases":  ["WALE", "weighted average lease expiry"],
                "compute_hint":    "Weighted-avg lease expiry in years",
                "clamp":           (0.5, 30),
                "extractor_only":  True,
            },
            {
                "key":             "leverage_ratio",
                "mandatory":       True,
                "search_phrases":  ["aggregate leverage", "debt to NAV", "debt-to-NAV"],
                "compute_hint":    "Debt / NAV or aggregate leverage",
                "clamp":           (0, 0.80),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "dpu_cents",
                "mandatory":       True,
                "search_phrases":  ["DPU", "distribution per unit"],
                "compute_hint":    "Distribution per unit (LOCAL cents/pennies)",
                "clamp":           (0, 500),
                "extractor_only":  True,
            },
            {
                "key":             "affo_per_unit_cents",
                "mandatory":       False,
                "search_phrases":  ["AFFO per unit", "AFFO per share"],
                "compute_hint":    "AFFO per unit (same unit as dpu_cents)",
                "clamp":           (0, 500),
                "extractor_only":  True,
            },
            # Note: subtype_mix and geographic_mix are dict-shaped, not numeric.
            # The legacy extractor validates them with bespoke logic; framework
            # extraction returns {} for non-numeric clamps (subtype_mix /
            # geographic_mix continue to require legacy extraction until Stage B).

            # ── US-REIT vocabulary additions (Fix B v3.2) ─────────────────
            # These rows were added so PSA / EXR / SPG / AMT / O / DLR can
            # populate the framework_metrics bucket with the metrics US-REITs
            # actually disclose (versus S-REIT-shaped DPU-in-cents schema).
            # Same-store NOI growth is the V3 quality_tiers anchor; dps_usd /
            # core_ffo_per_share are the cash-earnings cross-checks.
            {
                "key":             "same_store_noi_growth_pct",
                "mandatory":       True,
                "search_phrases":  ["same-store NOI growth", "same-store NOI", "SS NOI",
                                    "same-store revenue growth"],
                "compute_hint":    "TTM same-store NOI growth (decimal — 0.04 = 4%). Negative = declining.",
                "clamp":           (-0.20, 0.40),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "dps_usd",
                "mandatory":       False,
                "search_phrases":  ["annualized dividend", "annual DPS", "quarterly dividend",
                                    "dividend per share"],
                "compute_hint":    "Annualised dividend per share (USD). Quarterly DPS × 4 if needed.",
                "clamp":           (0.01, 100.0),
                "extractor_only":  True,
            },
            {
                "key":             "core_ffo_per_share",
                "mandatory":       False,
                "search_phrases":  ["Core FFO per share", "AFFO per share", "FFO per share"],
                "compute_hint":    "Annualised Core FFO / AFFO / FFO per share (whichever the REIT cites primarily)",
                "clamp":           (0.10, 50.0),
                "extractor_only":  True,
            },
        ],
        "source_priority": [
            "Annual report valuation table",
            "Q4 supplemental disclosure",
            "IR investor day deck",
        ],
    },

    # ── PR #4: SaaS family (Growth, Mature, Cybersecurity) ────────────────
    # All three share the SaaS KPI schema (NRR, Rule of 40, CAC payback, etc.)
    # but differ on which subset is MANDATORY for the valuation card to render
    # the sub-profile-specific anchor method.
    "Growth SaaS": {
        "sector":         "Tech",
        "anchor_methods": ["NRR-adj DCF", "EV/NTM Revenue", "Rule of 40"],
        # V3 quality: NRR primary (the platform-stickiness moat) +
        # Rule of 40 kicker (path-to-profitability). Both at full magnitude
        # in separate correlation groups so they multiply (capped 0.70-1.50).
        "quality_tiers": {
            "kpi_bands": [
                {"kpi": "nrr_pct", "direction": "higher_better",
                 "correlation_group": "growth_saas_q_primary",
                 "bands": [
                     {"min": 1.30, "mult": 1.30, "label": "elite-DDOG"},
                     {"min": 1.15, "mult": 1.15, "label": "strong"},
                     {"min": 1.00, "mult": 1.00, "label": "in-band"},
                     {"min": 0.0,  "mult": 0.85, "label": "contraction"},
                 ]},
                {"kpi": "rule_of_40_score", "direction": "higher_better",
                 "correlation_group": "growth_saas_q_kicker",
                 "bands": [
                     {"min": 60, "mult": 1.30, "label": "elite-R40"},
                     {"min": 40, "mult": 1.15, "label": "strong-R40"},
                     {"min": 25, "mult": 1.00, "label": "in-band"},
                     {"min": -99, "mult": 0.85, "label": "weak"},
                 ]},
            ],
            "cap": [0.70, 1.50],
            # quality cap_when: magic_number < 0.4 caps composite quality at
            # 1.00x (the "burn-and-pray" override). Even elite NRR + Rule of 40
            # can't compensate for sales-efficiency breakdown.
            "cap_when": {
                "kpi":      "magic_number",
                "lt":       0.40,
                "max_mult": 1.00,
                "note":     "magic_number cap: <0.4 = burn-and-pray, sales efficiency broken",
            },
        },
        # V3 risk: magic_number (sales efficiency = the leverage proxy for SaaS;
        # most SaaS firms have zero debt + large cash so net_debt_to_ebitda is
        # not meaningful). Has fallback: burn_rate_monthly_usd if magic_number
        # missing.
        "risk_adjustment": {
            "kpi": "magic_number", "direction": "higher_better",
            "bands": [
                {"min": 1.0, "mult": 1.10, "label": "fortress-DDOG"},
                {"min": 0.7, "mult": 1.05, "label": "strong"},
                {"min": 0.4, "mult": 1.00, "label": "in-band"},
                {"min": 0.0, "mult": 0.85, "label": "weak"},
            ],
        },
        "kpis": [
            {
                "key":             "nrr_pct",
                "mandatory":       True,
                "search_phrases":  ["NRR", "net retention", "net dollar retention", "net expansion"],
                "compute_hint":    "Net revenue retention decimal",
                "clamp":           (0.80, 1.50),
                "extractor_only":  True,
                "decimal_format":  True,
                "fallback":        "use 1.10 sector avg + flag _completeness",
            },
            {
                "key":             "rule_of_40_score",
                "mandatory":       True,
                "search_phrases":  ["Rule of 40", "growth + FCF margin"],
                "compute_hint":    "Revenue growth % + FCF margin %",
                "clamp":           (-30, 120),
                "extractor_only":  True,
                "fallback":        "compute from FMP growth + FCF margin (acceptable proxy)",
            },
            {
                "key":             "gross_retention_pct",
                "mandatory":       False,
                "search_phrases":  ["gross retention", "GRR"],
                "compute_hint":    "Gross retention decimal — floor for NRR quality",
                "clamp":           (0.80, 1.00),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "cac_payback_months",
                "mandatory":       False,
                "search_phrases":  ["CAC payback"],
                "compute_hint":    "CAC payback in months",
                "clamp":           (3, 60),
                "extractor_only":  True,
            },
            {
                "key":             "ltv_cac_ratio",
                "mandatory":       False,
                "search_phrases":  ["LTV:CAC", "LTV/CAC"],
                "compute_hint":    "LTV / CAC ratio (target >3x)",
                "clamp":           (1, 15),
                "extractor_only":  True,
            },
            {
                "key":             "magic_number",
                "mandatory":       True,
                "search_phrases":  ["magic number", "new ARR / S&M", "sales efficiency"],
                "compute_hint":    "New ARR / prior-quarter S&M (Growth SaaS V3 risk lever; <0.4 caps quality at 1.00x)",
                "clamp":           (0.0, 3.0),
                "extractor_only":  True,
            },
            {
                "key":             "rpo_growth_yoy",
                "mandatory":       False,
                "search_phrases":  ["RPO", "remaining performance obligations"],
                "compute_hint":    "Remaining performance obligation growth (consumption-revenue leading indicator)",
                "clamp":           (-0.20, 0.80),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "billings_growth_yoy",
                "mandatory":       False,
                "search_phrases":  ["billings growth"],
                "compute_hint":    "Leading indicator vs reported GAAP revenue",
                "clamp":           (-0.20, 0.80),
                "extractor_only":  True,
                "decimal_format":  True,
            },
        ],
        "source_priority": [
            "Q4 earnings call supplement",
            "Latest 10-K",
            "Company shareholder letter / investor day",
        ],
    },

    "Mature SaaS": {
        "sector":         "Tech",
        "anchor_methods": ["DCF (FCF)", "EV/EBITDA", "P/E (ops)"],
        # V3 quality: NRR (primary — even Mature SaaS lives or dies on
        # retention) + fcf_margin_pct (kicker — the "we are now a real
        # business" signal). Separate groups MULTIPLY.
        "quality_tiers": {
            "kpi_bands": [
                {"kpi": "nrr_pct", "direction": "higher_better",
                 "correlation_group": "mature_saas_q_primary",
                 "bands": [
                     {"min": 1.15, "mult": 1.25, "label": "elite"},
                     {"min": 1.08, "mult": 1.10, "label": "strong"},
                     {"min": 1.00, "mult": 1.00, "label": "in-band"},
                     {"min": 0.0,  "mult": 0.85, "label": "contraction"},
                 ]},
                {"kpi": "fcf_margin_pct", "direction": "higher_better",
                 "correlation_group": "mature_saas_q_kicker",
                 "bands": [
                     {"min": 0.35, "mult": 1.10, "label": "elite-FCF"},
                     {"min": 0.25, "mult": 1.05, "label": "strong-FCF"},
                     {"min": 0.15, "mult": 1.00, "label": "in-band"},
                     {"min": 0.0,  "mult": 0.92, "label": "soft"},
                     {"min": -99,  "mult": 0.85, "label": "weak"},
                 ]},
            ],
            "cap": [0.70, 1.40],
        },
        # V3 risk: net_debt_to_ebitda — Mature SaaS often M&A-heavy (CRM
        # Slack acquisition, NOW M&A). Penalises debt-funded growth that
        # erodes balance-sheet quality.
        "risk_adjustment": {
            "kpi": "net_debt_to_ebitda", "direction": "lower_better",
            "bands": [
                {"max": 0.0,  "mult": 1.10, "label": "fortress-net-cash"},
                {"max": 1.5,  "mult": 1.05, "label": "strong"},
                {"max": 3.0,  "mult": 1.00, "label": "in-band"},
                {"max": 99,   "mult": 0.85, "label": "weak-MA-heavy"},
            ],
        },
        "kpis": [
            # Same KPI schema as Growth SaaS, but for Mature SaaS only NRR is
            # mandatory (Rule of 40 less critical at scale; FCF margin already
            # FMP-derivable). Keeps clamp-equivalence test simple by sharing
            # the legacy SaaS extractor schema.
            {"key": "nrr_pct",              "mandatory": True,  "search_phrases": ["NRR", "net retention"], "clamp": (0.80, 1.50), "extractor_only": True, "decimal_format": True},
            {"key": "fcf_margin_pct",       "mandatory": True,  "search_phrases": ["FCF margin", "free cash flow margin"], "compute_hint": "TTM FCF / TTM revenue (decimal — FMP-augmented)", "clamp": (-0.20, 0.55), "source": "F", "extractor_only": False, "decimal_format": True},
            {"key": "net_debt_to_ebitda",   "mandatory": True,  "search_phrases": ["net debt to EBITDA", "leverage ratio"], "compute_hint": "(total_debt - cash) / TTM EBITDA — FMP-augmented", "clamp": (-3.0, 8.0), "source": "F", "extractor_only": False, "fmp_field": "netDebtToEBITDATTM"},
            {"key": "rule_of_40_score",     "mandatory": False, "search_phrases": ["Rule of 40"],          "clamp": (-30, 120),    "extractor_only": True},
            {"key": "gross_retention_pct",  "mandatory": False, "search_phrases": ["gross retention"],     "clamp": (0.80, 1.00), "extractor_only": True, "decimal_format": True},
            {"key": "cac_payback_months",   "mandatory": False, "search_phrases": ["CAC payback"],          "clamp": (3, 60),       "extractor_only": True},
            {"key": "ltv_cac_ratio",        "mandatory": False, "search_phrases": ["LTV:CAC"],              "clamp": (1, 15),       "extractor_only": True},
            {"key": "magic_number",         "mandatory": False, "search_phrases": ["magic number"],         "clamp": (0.1, 3.0),    "extractor_only": True},
            {"key": "rpo_growth_yoy",       "mandatory": False, "search_phrases": ["RPO"],                  "clamp": (-0.20, 0.80), "extractor_only": True, "decimal_format": True},
            {"key": "billings_growth_yoy", "mandatory": False, "search_phrases": ["billings growth"],      "clamp": (-0.20, 0.80), "extractor_only": True, "decimal_format": True},
        ],
        "source_priority": ["Q4 earnings call supplement", "10-K", "Investor day"],
    },

    "Cybersecurity / Mission-Critical SaaS": {
        "sector":         "Tech",
        "anchor_methods": ["DCF (FCF+ anchor)", "NRR-adj DCF", "EV/Revenue"],
        "quality_tiers": {
            "kpi_bands": [
                {"kpi": "nrr_pct", "direction": "higher_better", "correlation_group": "saas_q",
                 "bands": [{"min": 1.30, "mult": 1.40, "label": "elite"},
                           {"min": 1.15, "mult": 1.20, "label": "strong"},
                           {"min": 1.00, "mult": 1.00, "label": "in-band"},
                           {"min": 0.0,  "mult": 0.85, "label": "contraction"}]},
                {"kpi": "rule_of_40_score", "direction": "higher_better", "correlation_group": "saas_q",
                 "bands": [{"min": 60, "mult": 1.30, "label": "elite"},
                           {"min": 40, "mult": 1.15, "label": "healthy"},
                           {"min": 0,  "mult": 0.95, "label": "weak"}]},
            ],
            "cap": [0.70, 1.50],
        },
        "risk_adjustment": {
            "kpi": "cash_runway_years", "direction": "higher_better",
            "bands": [{"min": 4, "mult": 1.05, "label": "ample"},
                      {"min": 1.5, "mult": 1.00, "label": "in-band"},
                      {"min": 0, "mult": 0.80, "label": "tight"}],
        },
        "kpis": [
            # Cybersecurity: NRR + ARR growth proxies (rpo_growth_yoy / billings_growth_yoy) mandatory
            # because category-king status drives multiple expansion. Rule of 40 nice but FCF margin is
            # often negative for fast-growth cyber names so it's not a reliable mandatory.
            {"key": "nrr_pct",              "mandatory": True,  "search_phrases": ["NRR", "net retention"], "clamp": (0.80, 1.50), "extractor_only": True, "decimal_format": True},
            {"key": "rpo_growth_yoy",       "mandatory": True,  "search_phrases": ["RPO growth", "remaining performance obligations"], "clamp": (-0.20, 0.80), "extractor_only": True, "decimal_format": True},
            {"key": "billings_growth_yoy", "mandatory": False, "search_phrases": ["billings growth"],      "clamp": (-0.20, 0.80), "extractor_only": True, "decimal_format": True},
            {"key": "rule_of_40_score",     "mandatory": False, "search_phrases": ["Rule of 40"],          "clamp": (-30, 120),    "extractor_only": True},
            {"key": "gross_retention_pct",  "mandatory": False, "search_phrases": ["gross retention"],     "clamp": (0.80, 1.00), "extractor_only": True, "decimal_format": True},
            {"key": "cac_payback_months",   "mandatory": False, "search_phrases": ["CAC payback"],          "clamp": (3, 60),       "extractor_only": True},
            {"key": "ltv_cac_ratio",        "mandatory": False, "search_phrases": ["LTV:CAC"],              "clamp": (1, 15),       "extractor_only": True},
            {"key": "magic_number",         "mandatory": False, "search_phrases": ["magic number"],         "clamp": (0.1, 3.0),    "extractor_only": True},
        ],
        "source_priority": ["Q4 earnings call supplement", "10-K", "Investor day"],
    },

    # ── PR #5: Biopharma (Pre-approval + Large Cap Pharma) ────────────────
    # NOTE: Pipeline assets (per-asset list) are extracted by the legacy
    # _extract_pipeline_assets and stay there in Stage A. This entry covers
    # the SUPPLEMENTARY KPIs (cash runway, R&D intensity, LOE for top drugs).
    # Stage B (deferred) integrates per-asset extraction into the framework
    # via a new "list" KPI type.
    "Pre-approval Biotech": {
        "sector":         "Biopharma",
        "anchor_methods": ["rNPV (Pipeline)"],
        "kpis": [
            {
                "key":             "cash_runway_qtrs",
                "mandatory":       True,
                "search_phrases":  ["cash runway", "quarters of runway", "cash burn"],
                "compute_hint":    "Quarters until cash zero (cash / quarterly burn)",
                "clamp":           (0.5, 40.0),
                "extractor_only":  True,
                "fallback":        "compute as cash / (|OCF|/4) from FMP — acceptable proxy",
            },
            {
                "key":             "next_catalyst_date",
                "mandatory":       True,
                "search_phrases":  ["topline data", "Phase 3 readout", "BLA filing", "FDA decision date", "PDUFA"],
                "compute_hint":    "Next material clinical / regulatory catalyst (free-form date string)",
                "extractor_only":  True,
                "fallback":        "'unknown' + flag",
            },
            {
                "key":             "rd_intensity_pct",
                "mandatory":       False,
                "search_phrases":  ["R&D as % of revenue", "R&D intensity"],
                "compute_hint":    "R&D / revenue (FMP-derivable; included as cross-check)",
                "clamp":           (0.05, 5.0),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "max_dilution_pct",
                "mandatory":       False,
                "search_phrases":  ["fully diluted shares", "potential dilution", "warrants outstanding"],
                "compute_hint":    "Max dilution if all options/warrants/notes exercise",
                "clamp":           (0.0, 0.50),
                "extractor_only":  True,
                "decimal_format":  True,
            },
        ],
        "source_priority": [
            "Latest 10-Q (most recent cash position)",
            "S-1 (if recent IPO)",
            "Sell-side initiation note",
            "Company corporate deck",
        ],
    },

    "Large Cap Pharma": {
        "sector":         "Biopharma",
        "anchor_methods": ["rNPV (Pipeline)", "DCF", "P/E (ops)"],
        # FIX (audit Apr 2026): tightened search_phrases below add common
        # alternate terms ("blockbuster", "patent cliff", "expiry year").
        # Schema KPIs gross_margin_pct + net_debt_to_ebitda promoted to first-
        # class kpis so the extractor LOOKS for them (was: V3 schema declared
        # but kpis list didn't have them, so extractor never tried).
        "quality_tiers": {
            "kpi_bands": [{
                "kpi": "gross_margin_pct", "direction": "higher_better",
                "bands": [{"min": 0.80, "mult": 1.30, "label": "best-in-class"},
                          {"min": 0.70, "mult": 1.15, "label": "strong"},
                          {"min": 0.55, "mult": 1.00, "label": "in-band"},
                          {"min": 0.0,  "mult": 0.90, "label": "weak"}],
            }],
            "cap": [0.70, 1.50],
        },
        "risk_adjustment": {
            "kpi": "net_debt_to_ebitda", "direction": "lower_better",
            "bands": [{"max": 1.0, "mult": 1.10, "label": "fortress"},
                      {"max": 2.5, "mult": 1.00, "label": "in-band"},
                      {"max": 99.0, "mult": 0.85, "label": "leveraged"}],
        },
        "kpis": [
            {
                "key":             "loe_year_top_drug",
                "mandatory":       True,
                "search_phrases":  ["patent expiry", "loss of exclusivity", "LOE", "patent cliff", "patent expires", "exclusivity expiry", "blockbuster expiry year"],
                "compute_hint":    "Year of patent expiry for #1 revenue drug (e.g. Keytruda LOE 2028; Trulicity 2027)",
                "clamp":           (2026, 2050),
                "extractor_only":  True,
                "fallback":        "use generic 12-yr LOE assumption from RNPV_RAMP_PROFILE",
            },
            {
                "key":             "top_drug_revenue_pct",
                "mandatory":       False,
                "search_phrases":  ["lead drug", "top drug", "% of revenue", "blockbuster revenue", "concentration", "top product"],
                "compute_hint":    "% of total revenue from #1 drug (concentration risk indicator)",
                "clamp":           (0.0, 0.80),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "rd_intensity_pct",
                "mandatory":       False,
                "search_phrases":  ["R&D as % of revenue", "R&D intensity", "research and development spending", "R&D spend"],
                "compute_hint":    "R&D / revenue (typically 18-25% for Big Pharma)",
                "clamp":           (0.05, 0.40),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "effective_tax_rate",
                "mandatory":       False,
                "search_phrases":  ["effective tax rate", "ETR", "tax rate", "non-GAAP tax rate"],
                "compute_hint":    "Effective tax rate (Irish/Swiss IP structures pull this down)",
                "clamp":           (0.05, 0.30),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            # ── V3 schema KPIs (added so extractor LOOKS for them) ──────────
            {
                "key":             "gross_margin_pct",
                "mandatory":       False,
                "search_phrases":  ["gross margin", "gross profit margin", "GM %"],
                "compute_hint":    "Gross margin % (Big Pharma typically 75-85%)",
                "clamp":           (0.5, 0.95),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "net_debt_to_ebitda",
                "mandatory":       False,
                "search_phrases":  ["net debt to EBITDA", "leverage ratio", "net debt EBITDA", "debt/EBITDA"],
                "compute_hint":    "Net debt / TTM EBITDA (Big Pharma typically 0.5-2.0x)",
                "clamp":           (-2.0, 5.0),
                "extractor_only":  True,
            },
        ],
        "source_priority": [
            "Latest 10-K + Q4 earnings call",
            "IR pipeline page",
            "Sell-side LOE / pipeline coverage notes",
        ],
    },

    # ════════════════════════════════════════════════════════════════════
    # PR #6 — NEW sub-profiles (no legacy extractor exists today)
    # End-to-end ship: framework spec drives the L4 overlay, the generic
    # framework_metrics task in deep_research.py runs the LLM extractor,
    # dcf_agent reads via attach_overrides for any anchor methods.
    # No regression possible — these tickers had no sector-specific
    # extraction path before.
    # ════════════════════════════════════════════════════════════════════

    # ── Energy: Regulated Utility (NEE, DUK, SO, AEP, ED, XEL) ────────────
    "Regulated Utility": {
        "sector":         "Energy",
        "anchor_methods": ["P/Rate Base", "DDM", "P/E (ops)"],
        "quality_tiers": {
            "kpi_bands": [{
                "kpi": "allowed_roe", "direction": "higher_better",
                "bands": [{"min": 0.105, "mult": 1.20, "label": "premium"},
                          {"min": 0.095, "mult": 1.10, "label": "above-avg"},
                          {"min": 0.085, "mult": 1.00, "label": "in-band"},
                          {"min": 0.0,   "mult": 0.90, "label": "below-allowed"}],
            }],
            "cap": [0.80, 1.30],
        },
        "risk_adjustment": {
            "kpi": "debt_to_ebitda", "direction": "lower_better",
            "bands": [{"max": 4.5, "mult": 1.05, "label": "strong"},
                      {"max": 6.0, "mult": 1.00, "label": "in-band"},
                      {"max": 99.0, "mult": 0.90, "label": "weak"}],
        },
        "kpis": [
            {
                "key":             "allowed_roe",
                "mandatory":       True,
                "search_phrases":  ["allowed ROE", "authorized return", "regulator-approved ROE"],
                "compute_hint":    "Regulator-approved return on equity (per state PUC docket)",
                "clamp":           (0.07, 0.12),
                "extractor_only":  True,
                "decimal_format":  True,
                "fallback":        "use 0.095 sector default + flag",
            },
            {
                "key":             "rate_base_growth_yoy",
                "mandatory":       True,
                "search_phrases":  ["rate base growth", "rate base of $XB growing"],
                "compute_hint":    "YoY rate-base growth (primary driver, replaces revenue CAGR)",
                "clamp":           (0.0, 0.15),
                "extractor_only":  True,
                "decimal_format":  True,
                "fallback":        "use 0.05 sector default + flag",
            },
            {
                "key":             "rate_case_outcome_pct",
                "mandatory":       False,
                "search_phrases":  ["rate case", "filed vs granted", "rate case outcome"],
                "compute_hint":    "Last filing approval ratio (granted / requested)",
                "clamp":           (0.5, 1.1),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "capex_to_rate_base_pct",
                "mandatory":       False,
                "search_phrases":  ["capex / rate base", "capital plan"],
                "compute_hint":    "Annual capex as % of rate base (capex intensity indicator)",
                "clamp":           (0.05, 0.25),
                "extractor_only":  True,
                "decimal_format":  True,
            },
        ],
        "source_priority": [
            "Latest 10-K + Q4 earnings call",
            "State PUC rate case dockets",
            "FERC Form 1",
        ],
    },

    # ── Resources: Upstream Oil & Gas (XOM, CVX, OXY, EOG, PXD) ───────────
    "Upstream Oil & Gas": {
        "sector":         "Resources",
        "anchor_methods": ["NAV (PV-10)", "EV/EBITDAX", "P/CF"],
        # V4-α aggregator weights — solves the "Integrated Trap":
        # For supermajors (XOM/CVX), PV-10 only captures upstream reserves and
        # ignores downstream + chemicals worth $50-70/share. Without weights,
        # the median is unfairly tethered to the NAV outlier. Weighted mean
        # tilts toward EV/EBITDAX (integrated cash flow) which is the true
        # going-concern value for these conglomerates.
        "method_weights": {
            "NAV (PV-10)":  0.20,   # downweight — upstream-only proxy
            "EV/EBITDAX":   0.50,   # primary — integrated cash flow
            "P/CF":         0.30,   # secondary — operating cash
        },
        "quality_tiers": {
            "kpi_bands": [{
                # FIX: KPI name aligned with framework's `reserve_replacement_ratio` (was reserves_replacement_pct)
                "kpi": "reserve_replacement_ratio", "direction": "higher_better",
                "bands": [{"min": 1.30, "mult": 1.30, "label": "best-in-class"},
                          {"min": 1.00, "mult": 1.10, "label": "replacing"},
                          {"min": 0.70, "mult": 1.00, "label": "in-band"},
                          {"min": 0.0,  "mult": 0.85, "label": "depleting"}],
            }],
            "cap": [0.70, 1.40],
        },
        "risk_adjustment": {
            # FIX: switched from net_debt_to_capital (not in FMP) to net_debt_to_ebitda
            # (FMP-augmented at pipeline level)
            "kpi": "net_debt_to_ebitda", "direction": "lower_better",
            "bands": [{"max": 0.5,  "mult": 1.10, "label": "fortress"},
                      {"max": 1.5,  "mult": 1.00, "label": "in-band"},
                      {"max": 99.0, "mult": 0.85, "label": "leveraged"}],
        },
        "commodity_uplift": {
            "spot_kpi": "spot_brent_price", "realised_kpi": "realised_oil_price",
            "cost_kpi": "lifting_cost_per_boe",
            "spot_weight": 0.33, "max_uplift": 1.30,
        },
        "kpis": [
            {
                "key":             "pv10_value_usd",
                "mandatory":       True,
                "search_phrases":  ["PV-10", "discounted future net cash flows"],
                "compute_hint":    "SEC PV-10 supplement (USD billions)",
                "clamp":           (1.0e9, 1.0e12),
                "extractor_only":  True,
                "fallback":        "use book value as NAV proxy + flag",
            },
            {
                "key":             "breakeven_oil_price_usd",
                "mandatory":       True,
                "search_phrases":  ["breakeven oil price", "free cash flow breakeven"],
                "compute_hint":    "Oil price ($/bbl) at which FCF = 0",
                "clamp":           (20.0, 80.0),
                "extractor_only":  True,
                "fallback":        "use $50/bbl industry mid + flag",
            },
            {
                "key":             "reserve_replacement_ratio",
                "mandatory":       False,
                "search_phrases":  ["reserve replacement ratio", "reserves added"],
                "compute_hint":    "New reserves added / production (>1.0 = sustainability)",
                "clamp":           (0.5, 2.5),
                "extractor_only":  True,
            },
            {
                "key":             "f_d_cost_per_boe",
                "mandatory":       False,
                "search_phrases":  ["F&D cost", "finding and development cost"],
                "compute_hint":    "Finding & development cost per boe",
                "clamp":           (5.0, 60.0),
                "extractor_only":  True,
            },
            {
                "key":             "decline_rate_yoy",
                "mandatory":       False,
                "search_phrases":  ["decline rate", "production decline"],
                "compute_hint":    "Annual production decline rate (decimal)",
                "clamp":           (0.05, 0.40),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "production_growth_yoy",
                "mandatory":       False,
                "search_phrases":  ["production growth", "boe/d growth"],
                "compute_hint":    "YoY production growth (boe/d)",
                "clamp":           (-0.20, 0.30),
                "extractor_only":  True,
                "decimal_format":  True,
            },
        ],
        "source_priority": [
            "Latest 10-K (SEC PV-10 supplement)",
            "Q4 earnings call",
            "Investor day deck",
        ],
    },

    # ── Resources: Mining (Major) (FCX, NEM, GOLD, BHP, RIO) ──────────────
    "Mining (Major)": {
        "sector":         "Resources",
        "anchor_methods": ["NAV (Mine-by-Mine)", "EV/EBITDA", "P/CF"],
        # V4-α aggregator weights — Major miners (FCX, NEM) often have
        # smelting + by-products. NAV captures mine reserves; EV/EBITDA
        # captures the consolidated franchise. Same Integrated Trap as O&G
        # but less severe — NAV tracks reasonably well for pure miners.
        "method_weights": {
            "NAV (Mine-by-Mine)": 0.40,   # primary — mine economics
            "EV/EBITDA":          0.40,   # consolidated franchise
            "P/CF":               0.20,   # secondary
        },
        # V3 quality tiers: cost_curve_quartile (operational pricing-power leverage)
        "quality_tiers": {
            "kpi_bands": [{
                "kpi": "cost_curve_quartile", "direction": "lower_better",
                "bands": [
                    {"max": 1, "mult": 1.30, "label": "Q1-cost (lowest)"},
                    {"max": 2, "mult": 1.30, "label": "Q2-cost (low)"},
                    {"max": 3, "mult": 1.00, "label": "Q3-cost (median)"},
                    {"max": 4, "mult": 0.85, "label": "Q4-cost (highest)"},
                ],
            }],
            "cap": [0.70, 1.50],
        },
        "risk_adjustment": {
            "kpi": "net_debt_to_ebitda", "direction": "lower_better",
            "bands": [
                {"max": 0.5, "mult": 1.10, "label": "fortress"},
                {"max": 1.5, "mult": 1.05, "label": "strong"},
                {"max": 2.5, "mult": 1.00, "label": "in-band"},
                {"max": 99.0, "mult": 0.85, "label": "weak"},
            ],
        },
        "commodity_uplift": {
            "spot_kpi":     "spot_commodity_price",
            "realised_kpi": "realised_price_per_unit",
            "cost_kpi":     "aisc_per_oz",
            "spot_weight":  0.33,
            "max_uplift":   1.40,
        },
        "kpis": [
            {
                "key":             "aisc_per_oz",
                "mandatory":       True,
                "search_phrases":  ["AISC", "all-in sustaining cost"],
                "compute_hint":    "All-in sustaining cost per oz/lb (gold/copper)",
                "clamp":           (300.0, 3000.0),
                "extractor_only":  True,
                "fallback":        "use sector-tier estimate + flag",
            },
            {
                "key":             "cost_curve_quartile",
                "mandatory":       True,
                "search_phrases":  ["Q1 cost producer", "cost curve", "cost quartile"],
                "compute_hint":    "Cost-curve quartile (1 = lowest-cost, 4 = highest)",
                "clamp":           (1, 4),
                "extractor_only":  True,
                "fallback":        "use Q3 (median) + flag",
            },
            {
                "key":             "reserve_life_years",
                "mandatory":       False,
                "search_phrases":  ["reserve life", "mine life"],
                "compute_hint":    "Proved reserves / annual production (years)",
                "clamp":           (5.0, 80.0),
                "extractor_only":  True,
            },
            {
                "key":             "production_yoy_pct",
                "mandatory":       False,
                "search_phrases":  ["production growth", "tonnage growth"],
                "compute_hint":    "YoY production growth (decimal)",
                "clamp":           (-0.20, 0.30),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "realised_price_per_unit",
                "mandatory":       False,
                "search_phrases":  ["realised price", "realized price"],
                "compute_hint":    "Average realised price per oz/lb (net of by-products)",
                "extractor_only":  True,
            },
        ],
        "source_priority": [
            "Latest 10-K + Q4 earnings call",
            "Operator's NI 43-101 / JORC technical reports",
            "Wood Mackenzie / CRU cost-curve analysis",
        ],
    },

    # ── Semiconductor: Fabless (NVDA, AMD, AVGO, QCOM, MRVL) ──────────────
    "Fabless": {
        "sector":         "Semiconductor",
        "anchor_methods": ["DCF", "EV/EBITDA", "EV/Revenue", "P/E (ops)"],
        # V3.1: bumped cap to 1.65 + added "AI hyperscale" top tier when both
        # gross_margin AND data_center mix are best-in-class (NVDA-grade).
        "quality_tiers": {
            "kpi_bands": [
                {"kpi": "gross_margin_pct", "direction": "higher_better", "correlation_group": "fabless_q",
                 "bands": [{"min": 0.75, "mult": 1.50, "label": "AI hyperscale"},
                           {"min": 0.65, "mult": 1.30, "label": "elite"},
                           {"min": 0.55, "mult": 1.15, "label": "strong"},
                           {"min": 0.45, "mult": 1.00, "label": "in-band"},
                           {"min": 0.0,  "mult": 0.90, "label": "weak"}]},
                {"kpi": "data_center_revenue_pct", "direction": "higher_better", "correlation_group": "fabless_q",
                 "bands": [{"min": 0.80, "mult": 1.50, "label": "AI dominant"},
                           {"min": 0.40, "mult": 1.30, "label": "elite"},
                           {"min": 0.20, "mult": 1.15, "label": "strong"},
                           {"min": 0.10, "mult": 1.00, "label": "in-band"},
                           {"min": 0.0,  "mult": 0.90, "label": "weak"}]}
            ],
            "cap": [0.70, 1.65],
        },
        "risk_adjustment": {
            "kpi": "net_debt_to_ebitda", "direction": "lower_better",
            "bands": [{"max": 0.5, "mult": 1.1, "label": "fortress"},
                      {"max": 1.5, "mult": 1.0, "label": "in-band"},
                      {"max": 99.0, "mult": 0.85, "label": "weak"}],
        },
        "kpis": [
            {
                "key":             "gross_margin_pct",
                "mandatory":       True,
                "search_phrases":  ["gross margin", "gross profit margin"],
                "compute_hint":    "Gross margin (cycle-amplitude indicator; FMP-derivable cross-check)",
                "clamp":           (0.15, 0.75),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "data_center_revenue_pct",
                "mandatory":       True,
                "search_phrases":  ["data center revenue", "AI accelerator revenue", "DC segment"],
                "compute_hint":    "Data center / AI accelerator revenue as % of total",
                "clamp":           (0.0, 0.95),
                "extractor_only":  True,
                "decimal_format":  True,
                "fallback":        "use 0.30 default + flag (likely zero for non-AI fabless)",
            },
            {
                "key":             "lead_time_weeks",
                "mandatory":       False,
                "search_phrases":  ["lead time", "lead times of X weeks"],
                "compute_hint":    "Customer lead times (demand-supply gap signal)",
                "clamp":           (0.0, 60.0),
                "extractor_only":  True,
            },
            {
                "key":             "china_revenue_pct",
                "mandatory":       False,
                "search_phrases":  ["China revenue", "PRC revenue", "China exposure"],
                "compute_hint":    "China revenue as % of total (geopolitical haircut driver)",
                "clamp":           (0.0, 0.50),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "design_win_pipeline_qty",
                "mandatory":       False,
                "search_phrases":  ["design wins", "next-gen silicon commitments"],
                "compute_hint":    "Disclosed design wins for next-gen products (count or qualitative)",
                "extractor_only":  True,
            },
        ],
        "source_priority": [
            "Q4 earnings call segment disclosure",
            "Latest 10-K",
            "Investor day product roadmap",
        ],
    },

    # ── Semiconductor: IDM / Foundry (TSM, INTC, GFS) ─────────────────────
    "IDM / Foundry": {
        "sector":         "Semiconductor",
        "anchor_methods": ["DCF", "EV/EBITDA", "P/B"],
        "quality_tiers": {
            "kpi_bands": [
                {"kpi": "utilisation_rate_pct", "direction": "higher_better",
                 "bands": [{"min": 0.85, "mult": 1.3, "label": "elite"}, {"min": 0.75, "mult": 1.15, "label": "strong"}, {"min": 0.65, "mult": 1.0, "label": "in-band"}, {"min": 0.0, "mult": 0.9, "label": "weak"}]}
            ],
            "cap": [0.7, 1.5],
        },
        "risk_adjustment": {
            "kpi": "net_debt_to_ebitda", "direction": "lower_better",
            "bands": [{"max": 1.0, "mult": 1.1, "label": "fortress"},
                      {"max": 2.5, "mult": 1.0, "label": "in-band"},
                      {"max": 99.0, "mult": 0.85, "label": "weak"}],
        },
        "kpis": [
            {
                "key":             "wafer_capacity_kwspm",
                "mandatory":       True,
                "search_phrases":  ["wafer capacity", "kwspm", "thousand wafers per month"],
                "compute_hint":    "Wafer capacity in KWSpm (thousand wafers per month)",
                "clamp":           (10.0, 1500.0),
                "extractor_only":  True,
                "fallback":        "skip if not disclosed",
            },
            {
                "key":             "utilisation_rate_pct",
                "mandatory":       True,
                "search_phrases":  ["fab utilisation", "fab utilization", "utilization rate"],
                "compute_hint":    "Fab utilisation rate (cycle position indicator)",
                "clamp":           (0.50, 1.0),
                "extractor_only":  True,
                "decimal_format":  True,
                "fallback":        "use 0.85 mid-cycle + flag",
            },
            {
                "key":             "leading_edge_revenue_pct",
                "mandatory":       False,
                "search_phrases":  ["leading edge", "advanced node revenue", "<7nm"],
                "compute_hint":    "Revenue from leading-edge nodes (3nm/5nm/7nm) as % of total",
                "clamp":           (0.0, 0.95),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "capex_to_sales_pct",
                "mandatory":       False,
                "search_phrases":  ["capex / sales", "capital intensity"],
                "compute_hint":    "Capex as % of sales (FMP-derivable cross-check; high for foundry)",
                "clamp":           (0.10, 0.60),
                "extractor_only":  True,
                "decimal_format":  True,
            },
        ],
        "source_priority": [
            "Q4 earnings call + capex guidance",
            "Latest 10-K",
            "TSMC technology symposium / Intel investor day",
        ],
    },

    # ── Tech: Hyperscaler / Tech Conglomerate (MSFT, AMZN, GOOGL, AAPL, META) ──
    # Mega-cap tech umbrella per catalog — covers the 5 biggest names regardless
    # of whether their anchor is cloud (MSFT/AMZN/GOOGL), ads (META/GOOGL), or
    # devices+services (AAPL). Schema uses GENERALIST KPIs that all 5 disclose
    # consolidated — revenue growth, operating margin, capex intensity. Cloud-
    # and AI-specific KPIs are optional; they fire as a kicker for tickers that
    # disclose them but don't gate the schema for those that don't.
    "Hyperscaler / Tech Conglomerate": {
        "sector":         "Tech",
        "anchor_methods": ["EV/EBITDA", "P/E (ops)", "DCF (FCF)", "FCF Yield"],
        # V3 quality tiers — universal: revenue growth + operating margin both
        # disclosed by all 5 tickers. Correlated as megacap_q (max-deviation
        # pick) so a single elite signal lifts; multiple elites don't double-
        # count. Mature mega-cap thresholds: 20% growth = AI-accelerating
        # (META FY24, MSFT Cloud), 12% = strong, 7% = in-band, <3% = decel.
        "quality_tiers": {
            "kpi_bands": [
                {"kpi": "revenue_growth_pct", "direction": "higher_better", "correlation_group": "megacap_q",
                 "bands": [
                     {"min": 0.20, "mult": 1.30, "label": "AI-accelerating"},
                     {"min": 0.12, "mult": 1.15, "label": "strong"},
                     {"min": 0.07, "mult": 1.00, "label": "in-band"},
                     {"min": 0.03, "mult": 0.95, "label": "mature"},
                     {"min": -0.99, "mult": 0.85, "label": "decel"},
                 ]},
                {"kpi": "operating_margin_pct", "direction": "higher_better", "correlation_group": "megacap_q",
                 "bands": [
                     {"min": 0.35, "mult": 1.20, "label": "elite"},
                     {"min": 0.25, "mult": 1.10, "label": "strong"},
                     {"min": 0.18, "mult": 1.00, "label": "in-band"},
                     {"min": 0.10, "mult": 0.95, "label": "compressed"},
                     {"min": -0.99, "mult": 0.85, "label": "weak"},
                 ]},
                # Cloud-specific KPI — only kicks in when extracted (MSFT/AMZN/
                # GOOGL disclose; AAPL/META don't). Treated as separate group
                # so it ADDS to the base megacap_q signal when present.
                {"kpi": "cloud_revenue_growth_pct", "direction": "higher_better", "correlation_group": "cloud_q",
                 "bands": [
                     {"min": 0.30, "mult": 1.20, "label": "AI-hyperscale"},
                     {"min": 0.20, "mult": 1.10, "label": "elite"},
                     {"min": 0.10, "mult": 1.00, "label": "in-band"},
                     {"min": -0.99, "mult": 0.92, "label": "decel"},
                 ]},
            ],
            "cap": [0.75, 1.50],
        },
        # V3 risk adjustment — capex intensity flags the AI capex digestion
        # risk. Above 30% capex/rev signals overbuild risk (META 2022 lesson:
        # 35%+ capex without commensurate ROIC = -65% drawdown). Applies
        # universally to all 5 tickers.
        "risk_adjustment": {
            "kpi": "capex_intensity_pct", "direction": "lower_better",
            "bands": [
                {"max": 0.10, "mult": 1.10, "label": "conservative"},
                {"max": 0.20, "mult": 1.00, "label": "in-band"},
                {"max": 0.30, "mult": 0.92, "label": "aggressive"},
                {"max": 0.99, "mult": 0.80, "label": "over-extended"},
            ],
        },
        "kpis": [
            # ── Universal mandatory KPIs (all 5 tickers disclose) ──────────
            {
                "key":             "revenue_growth_pct",
                "mandatory":       True,
                "search_phrases":  ["consolidated revenue growth", "revenue grew",
                                    "total revenue YoY", "TTM revenue growth"],
                "compute_hint":    "Consolidated revenue growth YoY (decimal — 0.13 = 13%)",
                "clamp":           (-0.30, 0.80),
                "source":          "W",
                "extractor_only":  False,
                "decimal_format":  True,
                "fallback":        "compute from FMP TTM revenue / prior TTM revenue",
            },
            {
                "key":             "operating_margin_pct",
                "mandatory":       True,
                "search_phrases":  ["operating margin", "GAAP operating income",
                                    "consolidated operating margin"],
                "compute_hint":    "GAAP operating income / total revenue (decimal)",
                "clamp":           (-0.20, 0.55),
                "source":          "F",
                "extractor_only":  False,
                "fmp_field":       "operatingProfitMarginTTM",
                "decimal_format":  True,
            },
            {
                "key":             "capex_intensity_pct",
                "mandatory":       True,
                "search_phrases":  ["capex / revenue", "capital intensity",
                                    "infrastructure capex", "AI capex commitments"],
                "compute_hint":    "Capex / TTM revenue (FMP-derivable + earnings-call cross-check)",
                "clamp":           (0.01, 0.50),
                "source":          "F",
                "extractor_only":  False,
                "fmp_field":       "capexToRevenueTTM",
                "decimal_format":  True,
            },
            # ── Cloud-specific (MSFT/AMZN/GOOGL only — optional kicker) ────
            {
                "key":             "cloud_revenue_growth_pct",
                "mandatory":       False,
                "search_phrases":  ["Azure revenue growth", "AWS revenue growth", "GCP revenue growth",
                                    "Intelligent Cloud growth", "cloud segment revenue YoY"],
                "compute_hint":    "Cloud / hyperscale segment revenue growth YoY (decimal)",
                "clamp":           (-0.20, 1.00),
                "source":          "W",
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "cloud_operating_margin_pct",
                "mandatory":       False,
                "search_phrases":  ["cloud segment operating margin", "Intelligent Cloud margin",
                                    "AWS operating income margin"],
                "compute_hint":    "Cloud segment operating income / segment revenue (decimal)",
                "clamp":           (-0.20, 0.60),
                "source":          "W",
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "ai_revenue_run_rate_usd_b",
                "mandatory":       False,
                "search_phrases":  ["AI revenue run-rate", "AI annualized revenue",
                                    "Copilot revenue", "Bedrock revenue"],
                "compute_hint":    "Latest disclosed AI-attributable run-rate revenue ($B)",
                "clamp":           (0.0, 200.0),
                "source":          "W",
                "extractor_only":  True,
            },
            # ── Mature-tech specific (AAPL/META — optional context) ────────
            {
                "key":             "services_revenue_pct",
                "mandatory":       False,
                "search_phrases":  ["Services revenue", "Services segment", "Family of Apps revenue"],
                "compute_hint":    "Services / Family-of-Apps revenue as % of total (decimal)",
                "clamp":           (0.0, 0.60),
                "source":          "W",
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "fcf_margin_pct",
                "mandatory":       False,
                "search_phrases":  ["FCF margin", "free cash flow margin"],
                "compute_hint":    "TTM FCF / TTM revenue (decimal — FMP-derivable)",
                "clamp":           (-0.20, 0.55),
                "source":          "F",
                "extractor_only":  False,
                "fmp_field":       "freeCashFlowMarginTTM",
                "decimal_format":  True,
            },
            # ── Risk fallback ──────────────────────────────────────────────
            {
                "key":             "net_debt_to_ebitda",
                "mandatory":       False,
                "search_phrases":  ["net debt to EBITDA", "leverage ratio"],
                "compute_hint":    "(total_debt - cash) / TTM EBITDA — augmented from FMP",
                "clamp":           (-3.0, 6.0),
                "source":          "F",
                "extractor_only":  False,
                "fmp_field":       "netDebtToEBITDATTM",
            },
        ],
        "source_priority": [
            "10-K / 10-Q consolidated income statement",
            "Earnings call transcripts (capex + segment commentary)",
            "Segment notes (cloud / Services / Family of Apps)",
            "IDC / Gartner / Canalys hyperscaler share reports (when applicable)",
        ],
    },

    # ── Telco (T, VZ, TMUS, BCE, CHL) ─────────────────────────────────────
    "Stable Growth": {
        "sector":         "Telco",
        "anchor_methods": ["DCF", "DDM", "EV/EBITDA"],
        "quality_tiers": {
            "kpi_bands": [
                {"kpi": "arpu_usd", "direction": "higher_better", "correlation_group": "telco_q",
                 "bands": [{"min": 60, "mult": 1.3, "label": "elite"}, {"min": 45, "mult": 1.15, "label": "strong"}, {"min": 30, "mult": 1.0, "label": "in-band"}, {"min": 0.0, "mult": 0.9, "label": "weak"}]},
                {"kpi": "churn_pct_monthly", "direction": "lower_better", "correlation_group": "telco_q",
                 "bands": [{"max": 0.012, "mult": 1.3, "label": "elite"}, {"max": 0.018, "mult": 1.15, "label": "strong"}, {"max": 0.024, "mult": 1.0, "label": "in-band"}, {"max": 99.0, "mult": 0.9, "label": "weak"}]}
            ],
            "cap": [0.7, 1.5],
        },
        "risk_adjustment": {
            "kpi": "debt_to_ebitda", "direction": "lower_better",
            "bands": [{"max": 2.5, "mult": 1.1, "label": "fortress"},
                      {"max": 4.0, "mult": 1.0, "label": "in-band"},
                      {"max": 99.0, "mult": 0.85, "label": "weak"}],
        },
        "kpis": [
            {
                "key":             "arpu_usd",
                "mandatory":       True,
                "search_phrases":  ["ARPU", "average revenue per user"],
                "compute_hint":    "Blended monthly ARPU (USD or local currency)",
                "clamp":           (5.0, 200.0),
                "extractor_only":  True,
                "fallback":        "use TTM revenue / subscribers from FMP + flag",
            },
            {
                "key":             "postpaid_net_adds_qtr",
                "mandatory":       True,
                "search_phrases":  ["postpaid net adds", "net additions"],
                "compute_hint":    "Postpaid net adds latest quarter (thousands)",
                "clamp":           (-2000.0, 2000.0),
                "extractor_only":  True,
            },
            {
                "key":             "churn_pct_monthly",
                "mandatory":       True,
                "search_phrases":  ["postpaid churn", "monthly churn"],
                "compute_hint":    "Postpaid monthly churn (decimal — 0.009 = 0.9%)",
                "clamp":           (0.005, 0.05),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "fivg_coverage_pct",
                "mandatory":       False,
                "search_phrases":  ["5G coverage", "5G population"],
                "compute_hint":    "% of population covered by 5G network",
                "clamp":           (0.0, 1.0),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "capex_intensity_pct",
                "mandatory":       False,
                "search_phrases":  ["capex intensity", "capex / revenue"],
                "compute_hint":    "Capex / revenue (FMP-derivable cross-check)",
                "clamp":           (0.10, 0.30),
                "extractor_only":  True,
                "decimal_format":  True,
            },
        ],
        "source_priority": [
            "Q4 earnings call subscriber metrics",
            "Latest 10-K",
            "Industry trackers (Strand Consult, Gartner)",
        ],
    },

    # ── Consumer: Automotive & EV (TSLA, F, GM, RIVN, LCID) ───────────────
    "Automotive & EV": {
        "sector":         "Consumer",
        "anchor_methods": ["DCF", "EV/EBITDA"],
        "quality_tiers": {
            "kpi_bands": [
                {"kpi": "vehicle_deliveries_yoy", "direction": "higher_better",
                 "bands": [{"min": 0.3, "mult": 1.3, "label": "elite"}, {"min": 0.1, "mult": 1.15, "label": "strong"}, {"min": 0.0, "mult": 1.0, "label": "in-band"}, {"min": 0.0, "mult": 0.9, "label": "weak"}]}
            ],
            "cap": [0.7, 1.5],
        },
        "risk_adjustment": {
            "kpi": "net_debt_to_ebitda", "direction": "lower_better",
            "bands": [{"max": 1.0, "mult": 1.1, "label": "fortress"},
                      {"max": 3.0, "mult": 1.0, "label": "in-band"},
                      {"max": 99.0, "mult": 0.85, "label": "weak"}],
        },
        "kpis": [
            {
                "key":             "vehicle_deliveries_yoy",
                "mandatory":       True,
                "search_phrases":  ["vehicle deliveries", "deliveries grew"],
                "compute_hint":    "YoY vehicle deliveries growth (decimal)",
                "clamp":           (-0.50, 1.00),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "asp_per_unit_usd",
                "mandatory":       True,
                "search_phrases":  ["ASP", "average selling price", "average transaction price"],
                "compute_hint":    "Average selling price per vehicle (USD)",
                "clamp":           (15000.0, 200000.0),
                "extractor_only":  True,
            },
            {
                "key":             "auto_gross_margin_ex_credits",
                "mandatory":       True,
                "search_phrases":  ["auto gross margin ex credits", "ex regulatory credits"],
                "compute_hint":    "Auto gross margin EXCLUDING ZEV credits (TSLA-specific; -ve for cash-burning EVs)",
                "clamp":           (-0.10, 0.40),
                "extractor_only":  True,
                "decimal_format":  True,
                "fallback":        "fall back to total auto gross margin from FMP + flag",
            },
            {
                "key":             "ev_mix_pct",
                "mandatory":       False,
                "search_phrases":  ["EV mix", "BEV mix", "electrification rate"],
                "compute_hint":    "EV/BEV deliveries as % of total (legacy OEMs only — N/A for pure-EV)",
                "clamp":           (0.0, 1.0),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "free_cash_flow_per_vehicle_usd",
                "mandatory":       False,
                "search_phrases":  ["FCF per vehicle", "free cash flow per car"],
                "compute_hint":    "FCF per delivered vehicle (USD)",
                "extractor_only":  True,
            },
        ],
        "source_priority": [
            "Q4 earnings call delivery + mix breakdown",
            "Latest 10-K",
            "Production reports (TSLA, RIVN, LCID monthly disclosures)",
        ],
    },

    # ── Biopharma: Managed Care (UNH, ELV, HUM, CI, CVS) ──────────────────
    # NOTE: Currently routed to Biopharma sector in TICKER_SECTOR_LOOKUP but
    # may need a HealthcareServices sector creation in a follow-up PR.
    "Managed Care": {
        "sector":         "Biopharma",
        "anchor_methods": ["DCF", "P/E (ops)", "EV/EBITDA"],
        "quality_tiers": {
            "kpi_bands": [
                {"kpi": "medical_loss_ratio", "direction": "lower_better",
                 "bands": [{"max": 0.84, "mult": 1.3, "label": "elite"}, {"max": 0.88, "mult": 1.15, "label": "strong"}, {"max": 0.92, "mult": 1.0, "label": "in-band"}, {"max": 99.0, "mult": 0.9, "label": "weak"}]}
            ],
            "cap": [0.7, 1.5],
        },
        "risk_adjustment": {
            "kpi": "debt_to_ebitda", "direction": "lower_better",
            "bands": [{"max": 2.0, "mult": 1.1, "label": "fortress"},
                      {"max": 4.0, "mult": 1.0, "label": "in-band"},
                      {"max": 99.0, "mult": 0.85, "label": "weak"}],
        },
        "kpis": [
            {
                "key":             "medical_loss_ratio",
                "mandatory":       True,
                "search_phrases":  ["medical loss ratio", "MLR", "medical cost ratio"],
                "compute_hint":    "MLR — claims paid / premium revenue (target <0.85)",
                "clamp":           (0.75, 0.95),
                "extractor_only":  True,
                "decimal_format":  True,
                "fallback":        "use 0.83 industry mid + flag",
            },
            {
                "key":             "members_yoy_pct",
                "mandatory":       True,
                "search_phrases":  ["membership growth", "members grew", "lives added"],
                "compute_hint":    "Membership / enrollment growth YoY (decimal)",
                "clamp":           (-0.10, 0.20),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "medicare_advantage_mix_pct",
                "mandatory":       True,
                "search_phrases":  ["Medicare Advantage", "MA membership", "MA mix"],
                "compute_hint":    "Medicare Advantage members as % of total (higher-margin segment)",
                "clamp":           (0.0, 0.80),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "premium_revenue_pmpm_usd",
                "mandatory":       False,
                "search_phrases":  ["PMPM", "per member per month"],
                "compute_hint":    "Premium revenue per member per month (USD)",
                "clamp":           (200.0, 2000.0),
                "extractor_only":  True,
            },
            {
                "key":             "reimbursement_rate_change_pct",
                "mandatory":       False,
                "search_phrases":  ["CMS rate notice", "reimbursement rate", "rate update"],
                "compute_hint":    "CMS reimbursement rate change (decimal — regulatory tailwind/headwind)",
                "clamp":           (-0.10, 0.10),
                "extractor_only":  True,
                "decimal_format":  True,
            },
        ],
        "source_priority": [
            "Q4 earnings call + CMS Final Notice analysis",
            "Latest 10-K",
            "CMS rate-update letters (annual)",
        ],
    },

# ════════════════════════════════════════════════════════════════════
# AUTO-GENERATED FROM PROFILE_CATALOG.md
# 48 sub-profiles built from Gemini-authored catalog specs
# (skips: 16 already-shipped framework + Hyperscaler / Tech Conglomerate)
# ════════════════════════════════════════════════════════════════════

# ── Biopharma ──────────────────────────────────────────────────
    'CDMO / Life Science Tools': {
        "sector":         'Biopharma',
        "anchor_methods": ['P/E (ops)', 'EV/EBITDA', 'DCF (FCF)', 'FCF Yield'],
        # V3 quality: book_to_bill_ratio primary + utilization_rate_pct kicker
        # (CDMO-heavy) + consumables_rev_pct kicker (toolmakers TMO/DHR/A).
        # Both kickers in separate groups → multiply when both present.
        "quality_tiers": {
            "kpi_bands": [
                {"kpi": "book_to_bill_ratio", "direction": "higher_better",
                 "correlation_group": "cdmo_q_primary",
                 "bands": [
                     {"min": 1.10,  "mult": 1.30, "label": "elite"},
                     {"min": 1.01,  "mult": 1.15, "label": "strong"},
                     {"min": 0.95,  "mult": 1.00, "label": "in-band"},
                     {"min": 0.0,   "mult": 0.85, "label": "weak"},
                 ]},
                {"kpi": "utilization_rate_pct", "direction": "higher_better",
                 "correlation_group": "cdmo_q_util_kicker",
                 "bands": [
                     {"min": 0.85, "mult": 1.30, "label": "elite-util"},
                     {"min": 0.75, "mult": 1.15, "label": "strong-util"},
                     {"min": 0.65, "mult": 1.00, "label": "in-band"},
                     {"min": 0.0,  "mult": 0.85, "label": "weak-util"},
                 ]},
                # Toolmaker alternative kicker — recurring consumables/service rev
                {"kpi": "consumables_rev_pct", "direction": "higher_better",
                 "correlation_group": "cdmo_q_consumables_kicker",
                 "bands": [
                     {"min": 0.60, "mult": 1.10, "label": "razor-blade-moat"},
                     {"min": 0.40, "mult": 1.05, "label": "strong-recurring"},
                     {"min": 0.20, "mult": 1.00, "label": "in-band"},
                     {"min": 0.0,  "mult": 0.95, "label": "commodity-tools"},
                 ]},
            ],
            "cap": [0.70, 1.50],
        },
        # V3 risk: net_debt_to_ebitda + Innovation Trap multi-gate drag.
        # ILMN/Grail lesson: high R&D burn with no revenue traction = capital
        # destruction. Multi-gate AND: rd_intensity >25% AND revenue_growth <5%
        # → 0.90× drag.
        "risk_adjustment": {
            "kpi": "net_debt_to_ebitda", "direction": "lower_better",
            "bands": [
                {"max": 2.0,  "mult": 1.10, "label": "fortress"},
                {"max": 3.5,  "mult": 1.00, "label": "in-band"},
                {"max": 5.0,  "mult": 0.92, "label": "stretched-post-MA"},
                {"max": 99,   "mult": 0.85, "label": "weak"},
            ],
            "drag_when": {
                "gates": [
                    {"kpi": "rd_intensity_pct",   "gt": 0.25},
                    {"kpi": "revenue_growth_pct", "lt": 0.05},
                ],
                "factor": 0.90,
                "note":   "Innovation Trap: high R&D + low growth (ILMN/Grail lesson)",
            },
        },
        "kpis": [
            {
                "key":             'book_to_bill_ratio',
                "mandatory":       True,
                "search_phrases":  ['book-to-bill ratio', 'net orders divided by revenue', 'order-to-shipment ratio'],
                "compute_hint":    'New orders / shipped revenue (>1.10 elite)',
                "clamp":           (0.5, 2.0),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  False,
            },
            {
                "key":             'backlog_usd',
                "mandatory":       True,
                "search_phrases":  ['total order backlog', 'contracted revenue backlog', 'closing backlog balance'],
                "clamp":           (1e8, 1e11),
                "source":          'W',
                "extractor_only":  True,
            },
            {
                "key":             'utilization_rate_pct',
                "mandatory":       True,
                "search_phrases":  ['capacity utilization', 'manufacturing utilization', 'plant utilization rate'],
                "compute_hint":    'Capacity utilization (decimal — CDMO-heavy: WAT/ILMN style)',
                "clamp":           (0.30, 1.0),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'consumables_rev_pct',
                "mandatory":       True,
                "search_phrases":  ['consumables revenue', 'recurring service revenue', 'razor-blade revenue mix',
                                    'consumables and service mix'],
                "compute_hint":    'Recurring consumables + service revenue / total revenue (decimal — toolmaker moat)',
                "clamp":           (0.0, 1.0),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'rd_intensity_pct',
                "mandatory":       True,
                "search_phrases":  ['R&D intensity', 'research and development % of sales', 'R&D / revenue'],
                "compute_hint":    'TTM R&D / TTM revenue (decimal — Innovation Trap gate at >25%)',
                "clamp":           (0.0, 0.50),
                "source":          'F',
                "extractor_only":  False,
                "decimal_format":  True,
            },
            {
                "key":             'revenue_growth_pct',
                "mandatory":       True,
                "search_phrases":  ['revenue growth YoY', 'consolidated revenue growth'],
                "compute_hint":    'TTM revenue growth (decimal — FMP-augmented)',
                "clamp":           (-0.30, 0.80),
                "source":          'F',
                "extractor_only":  False,
                "decimal_format":  True,
            },
            {
                "key":             'net_debt_to_ebitda',
                "mandatory":       True,
                "search_phrases":  ['net debt to EBITDA', 'leverage ratio'],
                "clamp":           (-3.0, 8.0),
                "source":          'F',
                "extractor_only":  False,
                "fmp_field":       'netDebtToEBITDATTM',
            },
        ],
        "source_priority": ['10-K segment reporting', 'Book-to-bill announcements', 'Capacity utilization disclosures', 'Consumables revenue mix'],
    },

    'MedTech / Devices': {
        "sector":         'Biopharma',
        "anchor_methods": ['EV/Revenue', 'P/E (ops)', 'EV/EBITDA', 'DCF (FCF)'],
        # V3 quality: procedure_volume_growth_yoy primary (top-of-funnel
        # demand) + new_product_sales_pct kicker (innovation engine — ISRG
        # da Vinci, EW TAVR, BSX Watchman moat).
        "quality_tiers": {
            "kpi_bands": [
                {"kpi": "procedure_volume_growth_yoy", "direction": "higher_better",
                 "correlation_group": "medtech_q_primary",
                 "bands": [
                     {"min":  0.10, "mult": 1.30, "label": "elite-ISRG"},
                     {"min":  0.05, "mult": 1.15, "label": "strong"},
                     {"min":  0.0,  "mult": 1.00, "label": "in-band"},
                     {"min": -99,   "mult": 0.85, "label": "decel"},
                 ]},
                {"kpi": "new_product_sales_pct", "direction": "higher_better",
                 "correlation_group": "medtech_q_innovation_kicker",
                 "bands": [
                     {"min": 0.30, "mult": 1.10, "label": "innovation-led"},
                     {"min": 0.15, "mult": 1.05, "label": "strong-pipeline"},
                     {"min": 0.0,  "mult": 1.00, "label": "legacy-heavy"},
                 ]},
            ],
            "cap": [0.70, 1.45],
        },
        # V3 risk: net_debt_to_ebitda
        "risk_adjustment": {
            "kpi": "net_debt_to_ebitda", "direction": "lower_better",
            "bands": [
                {"max": 1.5,  "mult": 1.10, "label": "fortress"},
                {"max": 3.0,  "mult": 1.00, "label": "in-band"},
                {"max": 4.5,  "mult": 0.92, "label": "stretched"},
                {"max": 99,   "mult": 0.85, "label": "weak"},
            ],
        },
        "kpis": [
            {
                "key":             'procedure_volume_growth_yoy',
                "mandatory":       True,
                "search_phrases":  ['procedure volume growth', 'surgical case volume', 'underlying utilization growth'],
                "compute_hint":    'YoY procedure/surgical case volume growth (decimal — ISRG da Vinci style >10% elite)',
                "clamp":           (-0.20, 0.40),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'new_product_sales_pct',
                "mandatory":       True,
                "search_phrases":  ['vitality index', 'revenue from products launched in last 3 years', 'new product contribution'],
                "compute_hint":    'new_product_revenue / total_revenue (decimal — innovation engine, >30% elite)',
                "clamp":           (0.0, 0.70),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'net_debt_to_ebitda',
                "mandatory":       True,
                "search_phrases":  ['net debt to EBITDA', 'leverage ratio'],
                "clamp":           (-3.0, 8.0),
                "source":          'F',
                "extractor_only":  False,
                "fmp_field":       'netDebtToEBITDATTM',
            },
            {
                "key":             'market_share_pct',
                "mandatory":       False,
                "search_phrases":  ['segment share', 'market penetration'],
                "source":          'W',
                "extractor_only":  True,
            },
        ],
        "source_priority": ['NPI Vitality Index reports', 'Hospital capex budgets', 'Procedure volume disclosures'],
    },

# ── Consumer ──────────────────────────────────────────────────
    'Apparel / Athletic Wear': {
        "sector":         'Consumer',
        "anchor_methods": ['EV/EBITDA', 'DCF (FCF)', 'P/E (ops)', 'Brand Valuation'],
        # V3 quality: sssg_pct primary + dtc_revenue_pct kicker (DTC mix =
        # brand power / margin lever).
        "quality_tiers": {
            "kpi_bands": [
                {"kpi": "sssg_pct", "direction": "higher_better",
                 "correlation_group": "apparel_q_primary",
                 "bands": [
                     {"min":  0.08, "mult": 1.30, "label": "elite"},
                     {"min":  0.04, "mult": 1.15, "label": "strong"},
                     {"min":  0.0,  "mult": 1.00, "label": "in-band"},
                     {"min": -99,   "mult": 0.85, "label": "decline"},
                 ]},
                {"kpi": "dtc_revenue_pct", "direction": "higher_better",
                 "correlation_group": "apparel_q_kicker",
                 "bands": [
                     {"min": 0.50, "mult": 1.10, "label": "premium-direct"},
                     {"min": 0.30, "mult": 1.05, "label": "balanced"},
                     {"min": 0.0,  "mult": 1.00, "label": "wholesale-heavy"},
                 ]},
            ],
            "cap": [0.70, 1.45],
        },
        # V3 risk: inventory_turnover (lower turn = retail death spiral signal —
        # TGT 2022 lesson). Per-user calibration: 2.5-3.1 in-band for premium
        # athletic; >4.0 elite.
        "risk_adjustment": {
            "kpi": "inventory_turnover", "direction": "higher_better",
            "bands": [
                {"min": 4.0,  "mult": 1.10, "label": "elite"},
                {"min": 3.2,  "mult": 1.05, "label": "strong"},
                {"min": 2.5,  "mult": 1.00, "label": "in-band"},
                {"min": 0.0,  "mult": 0.85, "label": "weak"},
            ],
        },
        "kpis": [
            {
                "key":             'sssg_pct',
                "mandatory":       True,
                "search_phrases":  ['same-store sales growth', 'comparable store sales', 'comp sales growth'],
                "compute_hint":    '(current_period_comp_sales / prior_period_comp_sales) - 1 (decimal)',
                "clamp":           (-0.2, 0.4),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'inventory_turnover',
                "mandatory":       True,
                "search_phrases":  ['inventory turnover ratio', 'inventory turns', 'COGS / average inventory'],
                "compute_hint":    'annual_COGS / average_inventory (FMP-augmentable)',
                "clamp":           (1.0, 15.0),
                "source":          'F',
                "extractor_only":  False,
                "fmp_field":       'inventoryTurnoverTTM',
            },
            {
                "key":             'dtc_revenue_pct',
                "mandatory":       True,
                "search_phrases":  ['Direct-to-Consumer sales mix', 'DTC revenue share', 'D2C revenue %'],
                "compute_hint":    'DTC revenue / total revenue (decimal — premium athletic >50%)',
                "clamp":           (0.0, 1.0),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
        ],
        "source_priority": ['Quarterly SSSG disclosures', 'Inventory turnover schedules', 'DTC revenue mix (segment notes)'],
    },

    'Consumer Durables': {
        "sector":         'Consumer',
        "anchor_methods": ['EV/EBITDA', 'P/E (ops)', 'DCF (FCF)', 'FCF Yield'],
        # V3 quality: new_orders_growth_yoy primary + warranty_expense_pct
        # quality drag (lower = fewer product issues = higher Q signal).
        "quality_tiers": {
            "kpi_bands": [
                {"kpi": "new_orders_growth_yoy", "direction": "higher_better",
                 "correlation_group": "durables_q_primary",
                 "bands": [
                     {"min":  0.10, "mult": 1.30, "label": "elite"},
                     {"min":  0.0,  "mult": 1.00, "label": "in-band"},
                     {"min": -99,   "mult": 0.85, "label": "decline"},
                 ]},
                {"kpi": "warranty_expense_pct", "direction": "lower_better",
                 "correlation_group": "durables_q_kicker",
                 "bands": [
                     {"max": 0.02, "mult": 1.10, "label": "low-defect"},
                     {"max": 0.04, "mult": 1.00, "label": "in-band"},
                     {"max": 99,   "mult": 0.85, "label": "quality-issues"},
                 ]},
            ],
            "cap": [0.70, 1.40],
        },
        "risk_adjustment": {
            "kpi": "net_debt_to_ebitda", "direction": "lower_better",
            "bands": [
                {"max": 1.0,  "mult": 1.10, "label": "fortress"},
                {"max": 2.5,  "mult": 1.00, "label": "in-band"},
                {"max": 4.0,  "mult": 0.92, "label": "stretched"},
                {"max": 99,   "mult": 0.80, "label": "weak"},
            ],
        },
        "kpis": [
            {
                "key":             'new_orders_growth_yoy',
                "mandatory":       True,
                "search_phrases":  ['new order intake growth', 'order volume change', 'incoming orders YOY'],
                "clamp":           (-0.30, 0.60),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'warranty_expense_pct',
                "mandatory":       True,
                "search_phrases":  ['warranty costs as % of sales', 'product warranty expense ratio', 'warranty accruals / revenue'],
                "compute_hint":    'total_warranty_accrual / total_revenue',
                "clamp":           (0.0, 0.10),
                "source":          'H',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'net_debt_to_ebitda',
                "mandatory":       True,
                "search_phrases":  ['net debt to EBITDA', 'leverage ratio'],
                "compute_hint":    'FMP-augmented',
                "clamp":           (-3.0, 8.0),
                "source":          'F',
                "extractor_only":  False,
                "fmp_field":       'netDebtToEBITDATTM',
            },
            {
                "key":             'raw_material_cost_delta',
                "mandatory":       False,
                "search_phrases":  ['input cost inflation', 'commodity price impact'],
                "source":          'W',
                "extractor_only":  True,
            },
        ],
        "source_priority": ['New order intake reports', 'Warranty accrual tables', 'Leverage disclosures'],
    },

    'Consumer Growth': {
        "sector":         'Consumer',
        "anchor_methods": ['DCF (FCF)', 'EV/Revenue', 'EV/EBITDA'],
        # V3 quality: GMV growth + payback period (separate groups, multiply).
        "quality_tiers": {
            "kpi_bands": [
                {"kpi": "gmv_growth_yoy", "direction": "higher_better",
                 "correlation_group": "cgrowth_q_primary",
                 "bands": [
                     {"min":  0.30, "mult": 1.30, "label": "elite"},
                     {"min":  0.15, "mult": 1.15, "label": "strong"},
                     {"min":  0.05, "mult": 1.00, "label": "in-band"},
                     {"min": -99,   "mult": 0.85, "label": "decel"},
                 ]},
                {"kpi": "payback_period_months", "direction": "lower_better",
                 "correlation_group": "cgrowth_q_kicker",
                 "bands": [
                     {"max": 12,  "mult": 1.30, "label": "elite-payback"},
                     {"max": 18,  "mult": 1.15, "label": "strong-payback"},
                     {"max": 24,  "mult": 1.00, "label": "in-band"},
                     {"max": 999, "mult": 0.85, "label": "weak-payback"},
                 ]},
            ],
            "cap": [0.70, 1.50],
        },
        # V3 risk: gross_margin_pct (path-to-profitability gate, FMP-augmented)
        "risk_adjustment": {
            "kpi": "gross_margin_pct", "direction": "higher_better",
            "bands": [
                {"min": 0.60, "mult": 1.10, "label": "fortress"},
                {"min": 0.45, "mult": 1.05, "label": "strong"},
                {"min": 0.30, "mult": 1.00, "label": "in-band"},
                {"min": 0.25, "mult": 0.92, "label": "soft"},
                {"min": 0.0,  "mult": 0.80, "label": "weak"},
            ],
        },
        "kpis": [
            {
                "key":             'cac_usd',
                "mandatory":       True,
                "search_phrases":  ['customer acquisition cost', 'blended CAC', 'cost to acquire a new customer'],
                "clamp":           (5, 500),
                "source":          'W',
                "extractor_only":  True,
            },
            {
                "key":             'gmv_growth_yoy',
                "mandatory":       True,
                "search_phrases":  ['Gross Merchandise Value growth', 'total platform volume growth', 'GMV YOY', 'revenue growth'],
                "clamp":           (-0.20, 2.0),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'payback_period_months',
                "mandatory":       True,
                "search_phrases":  ['time to recover CAC', 'customer break-even', 'CAC payback months'],
                "compute_hint":    'Months to recover blended CAC (Elite <12mo, Weak >24mo)',
                "clamp":           (3, 60),
                "source":          'W',
                "extractor_only":  True,
            },
            {
                "key":             'gross_margin_pct',
                "mandatory":       True,
                "search_phrases":  ['gross margin', 'gross profit margin'],
                "compute_hint":    'TTM gross margin (decimal — FMP-augmented)',
                "clamp":           (0.0, 1.0),
                "source":          'F',
                "extractor_only":  False,
                "fmp_field":       'grossProfitMarginTTM',
                "decimal_format":  True,
            },
        ],
        "source_priority": ['Unit economics disclosures (CAC/LTV/payback)', 'Platform GMV growth logs', 'Gross margin trend'],
    },

    'Food & Beverage': {
        "sector":         'Consumer',
        "anchor_methods": ['P/E (ops)', 'DCF (FCF)', 'EV/EBITDA', 'Brand Valuation'],
        # V3 quality: weighted-geometric Vol/Price + Moat Integrity kicker +
        # ad_promotion_pct kicker. Per user A4 spec — Vol 0.6w (the "Truth"
        # metric) + Price 0.4w (the "Inflation" metric).
        # Moat Integrity: when volume_growth > price_mix_growth → +1.05x
        # ("Volume-Led Growth" — share-gain via real demand, not just price).
        "derived_kpis": [
            {"key":         "vol_price_diff",
             "numerator":   "volume_growth_yoy",
             "denominator": "price_mix_growth_yoy",
             "op":          "subtract"},
        ],
        "quality_tiers": {
            "kpi_bands": [
                {"kpi": "volume_growth_yoy", "direction": "higher_better",
                 "correlation_group": "fnb_q_volprice",
                 "kpi_weight": 0.6,
                 "bands": [
                     {"min":  0.020, "mult": 1.30, "label": "elite-vol"},
                     {"min":  0.0,   "mult": 1.15, "label": "strong-vol"},
                     {"min": -99,    "mult": 0.80, "label": "weak-vol"},
                 ]},
                {"kpi": "price_mix_growth_yoy", "direction": "higher_better",
                 "correlation_group": "fnb_q_volprice",
                 "kpi_weight": 0.4,
                 "bands": [
                     {"min":  0.030, "mult": 1.30, "label": "elite-price"},
                     {"min":  0.010, "mult": 1.00, "label": "in-band"},
                     {"min": -99,    "mult": 0.85, "label": "weak-price"},
                 ]},
                # Moat Integrity kicker (separate group, multiplies):
                # vol_price_diff > 0 → "Volume-Led Growth" → 1.05x
                {"kpi": "vol_price_diff", "direction": "higher_better",
                 "correlation_group": "fnb_q_moat",
                 "bands": [
                     {"min":  0.0001, "mult": 1.05, "label": "volume-led-moat"},
                     {"min": -99,     "mult": 1.00, "label": "price-led"},
                 ]},
                # Ad/promo discipline kicker (separate group):
                {"kpi": "ad_promotion_pct", "direction": "lower_better",
                 "correlation_group": "fnb_q_brand",
                 "bands": [
                     {"max": 0.08, "mult": 1.10, "label": "brand-gravity"},
                     {"max": 0.12, "mult": 1.00, "label": "in-band"},
                     {"max": 0.14, "mult": 0.95, "label": "elevated"},
                     {"max": 99,   "mult": 0.90, "label": "buying-share"},
                 ]},
            ],
            "cap": [0.70, 1.50],
        },
        # V3 risk: net_debt_to_ebitda — F&B carry meaningful leverage.
        "risk_adjustment": {
            "kpi": "net_debt_to_ebitda", "direction": "lower_better",
            "bands": [
                {"max": 2.0,  "mult": 1.10, "label": "fortress-KO"},
                {"max": 3.0,  "mult": 1.05, "label": "strong-PEP"},
                {"max": 4.0,  "mult": 1.00, "label": "in-band-MDLZ"},
                {"max": 5.0,  "mult": 0.92, "label": "stretched"},
                {"max": 99,   "mult": 0.85, "label": "weak"},
            ],
        },
        "kpis": [
            # NEW v3.7 — split vol vs price per A4-impl
            {
                "key":             'volume_growth_yoy',
                "mandatory":       True,
                "search_phrases":  ['organic volume growth', 'volume contribution to revenue', 'unit volume YoY'],
                "compute_hint":    'Organic volume growth YoY (decimal — the "Truth" metric per A4 spec)',
                "clamp":           (-0.15, 0.25),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'price_mix_growth_yoy',
                "mandatory":       True,
                "search_phrases":  ['pricing contribution to revenue', 'price/mix impact', 'realised pricing'],
                "compute_hint":    'Pricing/mix contribution YoY (decimal — the "Inflation" metric per A4 spec)',
                "clamp":           (-0.10, 0.30),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            # Legacy combined KPI — kept for backward-compat with v3.6 schemas
            {
                "key":             'volume_vs_price_mix',
                "mandatory":       False,
                "search_phrases":  ['organic volume growth', 'pricing contribution to revenue', 'price/mix impact'],
                "clamp":           (-0.15, 0.25),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'ad_promotion_pct',
                "mandatory":       True,
                "search_phrases":  ['advertising and promotion as % of sales', 'A&P intensity', 'marketing spend ratio'],
                "compute_hint":    'total_marketing_spend / total_revenue (decimal)',
                "clamp":           (0.0, 0.30),
                "source":          'H',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'net_debt_to_ebitda',
                "mandatory":       True,
                "search_phrases":  ['net debt to EBITDA', 'leverage ratio'],
                "compute_hint":    'FMP-augmented',
                "clamp":           (-3.0, 8.0),
                "source":          'F',
                "extractor_only":  False,
                "fmp_field":       'netDebtToEBITDATTM',
            },
            {
                "key":             'input_cost_coverage',
                "mandatory":       False,
                "search_phrases":  ['gross margin bridge', 'cost of goods sold analysis'],
                "source":          'H',
                "extractor_only":  True,
            },
        ],
        "source_priority": ['Organic volume vs price mix reports', 'A&P spend disclosures', 'Leverage'],
    },

    'Household / Personal': {
        "sector":         'Consumer',
        "anchor_methods": ['P/E (ops)', 'EV/EBITDA', 'DCF (FCF)'],
        # V3 quality: organic_sales_growth + market_share_delta (correlated max-pick).
        "quality_tiers": {
            "kpi_bands": [
                {"kpi": "organic_sales_growth", "direction": "higher_better",
                 "correlation_group": "hp_q",
                 "bands": [
                     {"min":  0.05, "mult": 1.25, "label": "elite"},
                     {"min":  0.03, "mult": 1.15, "label": "strong"},
                     {"min":  0.01, "mult": 1.00, "label": "in-band"},
                     {"min": -99,   "mult": 0.85, "label": "weak"},
                 ]},
                {"kpi": "market_share_delta", "direction": "higher_better",
                 "correlation_group": "hp_q",
                 "bands": [
                     {"min":  0.005, "mult": 1.25, "label": "elite-share-gain"},
                     {"min":  0.001, "mult": 1.15, "label": "strong-share-gain"},
                     {"min":  0.0,   "mult": 1.00, "label": "in-band"},
                     {"min": -99,    "mult": 0.85, "label": "share-loss"},
                 ]},
            ],
            "cap": [0.70, 1.40],
        },
        # V3 risk: net_debt_to_ebitda — staples are bond proxies, more
        # leverage tolerance than tech but less than utilities. Per A5 spec.
        "risk_adjustment": {
            "kpi": "net_debt_to_ebitda", "direction": "lower_better",
            "bands": [
                {"max": 1.5,  "mult": 1.10, "label": "fortress-CL"},
                {"max": 2.5,  "mult": 1.05, "label": "strong-PG"},
                {"max": 3.5,  "mult": 1.00, "label": "in-band-KMB"},
                {"max": 4.0,  "mult": 0.92, "label": "stretched"},
                {"max": 99,   "mult": 0.85, "label": "weak-EL"},
            ],
        },
        "kpis": [
            {
                "key":             'organic_sales_growth',
                "mandatory":       True,
                "search_phrases":  ['organic revenue growth', 'underlying sales', 'sales growth ex-FX/M&A'],
                "compute_hint":    '(revenue_ex_mna_fx / prior_revenue) - 1 (decimal)',
                "clamp":           (-0.10, 0.25),
                "source":          'H',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'market_share_delta',
                "mandatory":       True,
                "search_phrases":  ['market share gain/loss', 'share points change', 'category penetration delta'],
                "compute_hint":    'current_share - prior_share (decimal — +50bps elite, -bps share-loss)',
                "clamp":           (-0.05, 0.05),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'net_debt_to_ebitda',
                "mandatory":       True,
                "search_phrases":  ['net debt to EBITDA', 'leverage ratio'],
                "compute_hint":    'FMP-augmented',
                "clamp":           (-3.0, 8.0),
                "source":          'F',
                "extractor_only":  False,
                "fmp_field":       'netDebtToEBITDATTM',
            },
            {
                "key":             'premium_segment_mix',
                "mandatory":       False,
                "search_phrases":  ['prestige brand mix', 'premium product revenue share', 'high-end contribution'],
                "compute_hint":    'premium_revenue / total_revenue',
                "clamp":           (0.1, 0.65),
                "source":          'H',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'gross_margin_bridge',
                "mandatory":       False,
                "search_phrases":  ['gross margin price/mix impact', 'commodity cost headwind', 'COGS inflation delta'],
                "compute_hint":    'change in gross margin basis points',
                "clamp":           (-0.1, 0.1),
                "source":          'H',
                "extractor_only":  True,
                "decimal_format":  False,
            },
        ],
        "source_priority": ['10-K Segmented Disclosures', 'Nielsen / IRI Market Share Reports', 'Management Commentary'],
    },

    'Luxury Goods': {
        "sector":         'Consumer',
        "anchor_methods": ['P/E Premium', 'EV/EBITDA', 'DCF (FCF)', 'Brand Valuation'],
        # V3 quality: ASP growth (the Hermès Pricing Standard) +
        # brand_search_momentum_china kicker (Inventory Stuffing red flag).
        "quality_tiers": {
            "kpi_bands": [
                {"kpi": "asp_growth_pct", "direction": "higher_better",
                 "correlation_group": "luxury_q_primary",
                 "bands": [
                     {"min":  0.08, "mult": 1.30, "label": "elite-RMS"},
                     {"min":  0.04, "mult": 1.15, "label": "strong-LVMH"},
                     {"min":  0.0,  "mult": 1.00, "label": "in-band"},
                     {"min": -99,   "mult": 0.80, "label": "weak-KER-discounting"},
                 ]},
                {"kpi": "brand_search_momentum_china", "direction": "higher_better",
                 "correlation_group": "luxury_q_china_kicker",
                 "bands": [
                     {"min":  0.10, "mult": 1.10, "label": "demand-pull"},
                     {"min":  0.0,  "mult": 1.05, "label": "healthy-interest"},
                     {"min": -0.10, "mult": 1.00, "label": "stable"},
                     {"min": -99,   "mult": 0.85, "label": "INVENTORY-STUFFING"},
                 ]},
            ],
            "cap": [0.70, 1.45],
        },
        # V3 risk: china_revenue_mix Goldilocks. Bands non-monotonic — ordered
        # most-restrictive first so the iteration picks the right tier.
        "risk_adjustment": {
            "kpi": "china_revenue_mix", "direction": "higher_better",
            "bands": [
                {"min": 0.60, "mult": 0.85, "label": "concentration-risk"},
                {"min": 0.41, "mult": 1.00, "label": "in-band"},
                {"min": 0.25, "mult": 1.10, "label": "goldilocks"},
                {"min": 0.15, "mult": 1.00, "label": "moderate"},
                {"min": 0.0,  "mult": 0.95, "label": "neglected-china"},
            ],
        },
        "kpis": [
            {
                "key":             'asp_growth_pct',
                "mandatory":       True,
                "search_phrases":  ['average selling price growth', 'pricing power impact', 'ASP increase'],
                "compute_hint":    'Year-over-year ASP growth (decimal — Hermès >8% elite)',
                "clamp":           (-0.10, 0.30),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'china_revenue_mix',
                "mandatory":       True,
                "search_phrases":  ['Greater China revenue share', 'exposure to Chinese consumer', 'China region sales %'],
                "compute_hint":    'Greater China revenue / total revenue (decimal — Goldilocks 25-40%)',
                "clamp":           (0.0, 0.80),
                "source":          'H',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'brand_search_momentum_china',
                "mandatory":       True,
                "search_phrases":  ['Baidu search trend', 'Tmall search rank', 'JD search volume',
                                    'Chinese consumer brand interest', 'China brand momentum YoY'],
                "compute_hint":    'YoY change in Baidu/Tmall/JD search index for the brand (decimal). '
                                   'Critical: if revenue is up but search is collapsing, that\'s INVENTORY '
                                   'STUFFING — the moat is dying.',
                "clamp":           (-0.50, 1.0),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'store_network_growth',
                "mandatory":       False,
                "search_phrases":  ['net new boutiques', 'square footage expansion'],
                "source":          'W',
                "extractor_only":  True,
            },
        ],
        "source_priority": ['ASP growth disclosures (LVMH/RMS reports)', 'China search momentum (Baidu/Tmall trends)', 'Regional revenue mix'],
    },

    'Membership / Subscription Retail': {
        "sector":         'Consumer',
        "anchor_methods": ['P/E (ops)', 'DCF (FCF)', 'FCF Yield', 'Subscription DCF'],
        # V3 quality: renewal_rate_pct + fee_revenue_pct_ebitda — joint
        # qualification per A7 spec ("Subscription Service with Warehouse
        # Attached"). Separate correlation groups multiply (full magnitudes).
        "quality_tiers": {
            "kpi_bands": [
                {"kpi": "renewal_rate_pct", "direction": "higher_better",
                 "correlation_group": "membership_q_renewal",
                 "bands": [
                     {"min": 0.92,  "mult": 1.35, "label": "elite-COST"},
                     {"min": 0.88,  "mult": 1.15, "label": "strong"},
                     {"min": 0.85,  "mult": 1.00, "label": "in-band"},
                     {"min": 0.0,   "mult": 0.80, "label": "weak"},
                 ]},
                {"kpi": "fee_revenue_pct_ebitda", "direction": "higher_better",
                 "correlation_group": "membership_q_fee_share",
                 "bands": [
                     {"min": 0.50,  "mult": 1.35, "label": "elite-fee-engine"},
                     {"min": 0.40,  "mult": 1.15, "label": "strong"},
                     {"min": 0.30,  "mult": 1.00, "label": "in-band"},
                     {"min": 0.0,   "mult": 0.80, "label": "weak-transactional"},
                 ]},
            ],
            "cap": [0.70, 1.50],
        },
        # V3 risk: net_debt_to_ebitda + drag_when on membership_fee_growth_yoy
        # ("Saturation Risk" — if fee growth <5%, the Inertia Moat is stalling).
        "risk_adjustment": {
            "kpi": "net_debt_to_ebitda", "direction": "lower_better",
            "bands": [
                {"max":  0.0, "mult": 1.10, "label": "fortress-net-cash-COST"},
                {"max":  1.5, "mult": 1.05, "label": "strong"},
                {"max":  3.0, "mult": 1.00, "label": "in-band"},
                {"max": 99,   "mult": 0.85, "label": "weak"},
            ],
            "drag_when": {
                "kpi":    "membership_fee_growth_yoy",
                "lt":     0.05,
                "factor": 0.95,
                "note":   "Saturation Risk: fee growth <5% — Inertia Moat stalling",
            },
        },
        "kpis": [
            {
                "key":             'renewal_rate_pct',
                "mandatory":       True,
                "search_phrases":  ['membership renewal rate', 'member retention percentage', 'renewal rate'],
                "compute_hint":    'renewed_members / total_base',
                "clamp":           (0.75, 0.99),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'fee_revenue_pct_ebitda',
                "mandatory":       True,
                "search_phrases":  ['membership fees as % of EBITDA', 'fee income contribution to profit'],
                "compute_hint":    'total_membership_fees / adjusted_EBITDA',
                "clamp":           (0.3, 0.95),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'new_club_growth_yoy',
                "mandatory":       False,
                "search_phrases":  ['net new warehouse openings', 'club count growth', 'unit expansion count'],
                "compute_hint":    '(current_clubs / prior_clubs) - 1',
                "clamp":           (0.01, 0.1),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'membership_fee_growth_yoy',
                "mandatory":       True,
                "search_phrases":  ['membership fee growth', 'fee revenue growth', 'membership income YoY'],
                "compute_hint":    'YoY growth in membership fee revenue (decimal — Saturation Risk drag if <5%)',
                "clamp":           (-0.20, 0.50),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'net_debt_to_ebitda',
                "mandatory":       True,
                "search_phrases":  ['net debt to EBITDA', 'leverage ratio'],
                "compute_hint":    'FMP-augmented (negative = net cash, COST standard)',
                "clamp":           (-3.0, 8.0),
                "source":          'F',
                "extractor_only":  False,
                "fmp_field":       'netDebtToEBITDATTM',
            },
            {
                "key":             'mkt_penetration_per_region',
                "mandatory":       False,
                "search_phrases":  ['households per club location', 'market saturation', 'club density'],
                "compute_hint":    'households_in_radius / club_count',
                "clamp":           (1, 1000),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  False,
            },
        ],
        "source_priority": ['Quarterly Membership Supplements', '10-K Deferred Revenue Footnotes'],
    },

    'Traditional Retail': {
        "sector":         'Consumer',
        "anchor_methods": ['EV/EBITDAR', 'P/E (ops)', 'DCF (FCF)'],
        # V3 quality tiers — same-store sales growth is THE retail anchor
        "quality_tiers": {
            "kpi_bands": [
                {"kpi": "sssg_pct", "direction": "higher_better",
                 "bands": [
                     {"min":  0.06, "mult": 1.30, "label": "accelerating"},
                     {"min":  0.04, "mult": 1.18, "label": "healthy"},
                     {"min":  0.02, "mult": 1.08, "label": "in-band"},
                     {"min":  0.0,  "mult": 1.00, "label": "flat"},
                     {"min": -0.99, "mult": 0.85, "label": "declining"},
                 ]},
            ],
            "cap": [0.70, 1.40],
        },
        # V3 risk adjustment — leverage matters for retail (capex + lease debt)
        "risk_adjustment": {
            "kpi": "net_debt_to_ebitda", "direction": "lower_better",
            "bands": [
                {"max": 1.5,  "mult": 1.10, "label": "fortress"},
                {"max": 3.0,  "mult": 1.00, "label": "in-band"},
                {"max": 4.0,  "mult": 0.92, "label": "stretched"},
                {"max": 99.0, "mult": 0.80, "label": "over-levered"},
            ],
        },
        "kpis": [
            {
                "key":             'sssg_pct',
                "mandatory":       True,
                "search_phrases":  ['same-store sales growth', 'comparable store sales', 'comp sales growth'],
                "compute_hint":    '(current_period_comp_sales / prior_period_comp_sales) - 1',
                "clamp":           (-0.2, 0.4),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'inventory_turnover',
                "mandatory":       True,
                "search_phrases":  ['inventory turnover ratio', 'inventory turns', 'COGS / average inventory'],
                "compute_hint":    'annual_COGS / average_inventory',
                "clamp":           (2.0, 15.0),
                "source":          'F',
                "extractor_only":  False,
            },
            {
                "key":             'sales_per_sq_ft',
                "mandatory":       False,
                "search_phrases":  ['store productivity', 'revenue per square foot'],
                "source":          'W',
                "extractor_only":  True,
            },
            # Risk KPI — net_debt_to_ebitda is FMP-augmented in pipeline
            {
                "key":             'net_debt_to_ebitda',
                "mandatory":       False,
                "search_phrases":  ['net debt to EBITDA', 'leverage ratio', 'debt / EBITDA'],
                "compute_hint":    '(total_debt - cash) / TTM EBITDA — augmented from FMP /stable/key-metrics-ttm',
                "clamp":           (-1.0, 10.0),
                "source":          'F',
                "extractor_only":  False,
                "fmp_field":       "netDebtToEBITDATTM",
            },
        ],
        "source_priority": [
            'Quarterly SSSG (Same-Store Sales Growth) disclosures',
            'Lease liability footnotes',
            'EBITDAR margin trend (capitalised lease component)',
        ],
    },

    'Travel & Dining': {
        "sector":         'Consumer',
        # Multi-method anchor list — single EV/EBITDA was too fragile when
        # shares_out missing (audit Apr 2026: MCD test failed with only 1 method).
        "anchor_methods": ['EV/EBITDA', 'P/E (ops)', 'DCF (FCF)'],
        # V3.1: system-wide sales growth = brand pricing power for franchise/royalty model
        "quality_tiers": {
            "kpi_bands": [{
                "kpi": "system_wide_sales_growth", "direction": "higher_better",
                # Calibrated for mature brand-royalty model: 5%+ sustained growth
                # is genuinely premium for QSR (MCD/SBUX class).
                "bands": [{"min": 0.10, "mult": 1.30, "label": "best-in-class"},
                          {"min": 0.05, "mult": 1.20, "label": "premium brand"},
                          {"min": 0.02, "mult": 1.10, "label": "above-avg"},
                          {"min": 0.0,  "mult": 1.00, "label": "in-band"},
                          {"min": -1.0, "mult": 0.85, "label": "negative comp"}],
            }],
            "cap": [0.80, 1.40],
        },
        "risk_adjustment": {
            "kpi": "net_debt_to_ebitda", "direction": "lower_better",
            "bands": [{"max": 2.5,  "mult": 1.10, "label": "fortress"},
                      {"max": 4.0,  "mult": 1.00, "label": "in-band"},
                      {"max": 99.0, "mult": 0.90, "label": "leveraged"}],
        },
        "kpis": [
            {
                "key":             'revpar_growth_yoy',
                "mandatory":       True,
                "search_phrases":  ['RevPAR growth', 'Revenue Per Available Room YOY', 'hotel yield growth'],
                "clamp":           (-0.1, 0.5),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'system_wide_sales_growth',
                "mandatory":       True,
                "search_phrases":  ['global system-wide sales growth', 'franchisee sales growth', 'total network sales'],
                "clamp":           (-0.05, 0.3),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'take_rate_pct',
                "mandatory":       False,
                "search_phrases":  ['platform commission', 'marketplace take rate'],
                "source":          'W',
                "extractor_only":  True,
            },
        ],
        "source_priority": ['System-wide sales reports', 'STR (Smith Travel Research) global data'],
    },

# ── Crypto ──────────────────────────────────────────────────
    # ── G4: Pre-Revenue / Network Tech (Protocol / L1 / L2 plays —
    # Solana-style, DePIN, Filecoin, Helium etc.) ────────────────────────
    'Pre-Revenue Tech': {
        "sector":         'Crypto',
        "anchor_methods": ['Scenario Intrinsic Value', 'Comparable Transactions', 'Revenue DCF', 'TAM Penetration'],
        # V3 quality: active_developer_growth_yoy primary + tam_penetration_pct kicker.
        "quality_tiers": {
            "kpi_bands": [
                {"kpi": "active_developer_growth_yoy", "direction": "higher_better",
                 "correlation_group": "prerev_q_primary",
                 "bands": [
                     {"min":  0.30, "mult": 1.30, "label": "elite-Solana-Base-momentum"},
                     {"min":  0.10, "mult": 1.15, "label": "strong"},
                     {"min":  0.0,  "mult": 1.00, "label": "maturing"},
                     {"min": -99,   "mult": 0.80, "label": "ecosystem-decay"},
                 ]},
                {"kpi": "tam_penetration_pct", "direction": "higher_better",
                 "correlation_group": "prerev_q_kicker",
                 "bands": [
                     {"min": 0.05, "mult": 1.10, "label": "breakout"},
                     {"min": 0.01, "mult": 1.05, "label": "growth-phase"},
                     {"min": 0.0,  "mult": 1.00, "label": "early-pilot"},
                 ]},
            ],
            "cap": [0.70, 1.45],
        },
        # V3 risk: cash_runway_years — Survival is the only Moat for pre-rev.
        "risk_adjustment": {
            "kpi": "cash_runway_years", "direction": "higher_better",
            "bands": [
                {"min": 3.0,  "mult": 1.10, "label": "fortress"},
                {"min": 1.5,  "mult": 1.00, "label": "in-band"},
                {"min": 0.75, "mult": 0.90, "label": "warning"},
                {"min": 0.0,  "mult": 0.70, "label": "distressed"},
            ],
        },
        "kpis": [
            {"key": 'active_developer_growth_yoy', "mandatory": True, "search_phrases": ['active ecosystem developers growth','GitHub contributor growth','developer commits YOY'], "compute_hint": '(current_devs/prior_devs)-1 (decimal)', "clamp": (-0.50, 5.0), "source": 'W', "extractor_only": True, "decimal_format": True},
            {"key": 'tam_penetration_pct',         "mandatory": True, "search_phrases": ['market share of total addressable volume','protocol penetration rate','adoption share of target market'], "compute_hint": 'protocol_volume/TAM (decimal)', "clamp": (0.0, 0.50), "source": 'W', "extractor_only": True, "decimal_format": True},
            {"key": 'cash_runway_years',           "mandatory": True, "search_phrases": ['cash runway months','months of cash','liquidity runway'], "compute_hint": 'FMP-augmented from cash + burn rate', "clamp": (0.0, 99.0), "source": 'F', "extractor_only": False},
            {"key": 'token_velocity',              "mandatory": False, "search_phrases": ['on-chain transaction volume vs market cap'], "source": 'W', "extractor_only": True},
        ],
        "source_priority": ['Ecosystem developer activity', 'Protocol volume logs', 'Cash runway disclosures'],
    },

    # ── G1: Crypto Exchange (COIN, Kraken-public, Robinhood-crypto-arm) ──
    # 2026 driver: Institutional AUM Flow (not retail hype).
    'Crypto Exchange': {
        "sector":         'Crypto',
        "anchor_methods": ['EV/Revenue', 'P/E (ops)', 'DCF (FCF)', 'EV/EBITDA'],
        # Q: trading_volume_growth_yoy (primary 0.7w) + assets_on_platform_growth (kicker 0.3w).
        # Joint qualification per user's table — separate groups multiply.
        "quality_tiers": {
            "kpi_bands": [
                {"kpi": "trading_volume_growth_yoy", "direction": "higher_better",
                 "correlation_group": "cexch_q_primary",
                 "bands": [
                     {"min":  0.15, "mult": 1.30, "label": "elite"},
                     {"min":  0.05, "mult": 1.15, "label": "strong"},
                     {"min":  0.0,  "mult": 1.00, "label": "in-band"},
                     {"min": -99,   "mult": 0.80, "label": "weak"},
                 ]},
                {"kpi": "assets_on_platform_growth", "direction": "higher_better",
                 "correlation_group": "cexch_q_kicker",
                 "bands": [
                     {"min":  0.25, "mult": 1.30, "label": "elite-AUM-flow"},
                     {"min":  0.10, "mult": 1.15, "label": "strong-AUM"},
                     {"min":  0.0,  "mult": 1.00, "label": "in-band"},
                     {"min": -99,   "mult": 0.80, "label": "AUM-outflow"},
                 ]},
            ],
            "cap": [0.70, 1.50],
        },
        # R: non_interest_expense_pct_rev (efficiency gate — operating leverage).
        "risk_adjustment": {
            "kpi": "non_interest_expense_pct_rev", "direction": "lower_better",
            "bands": [
                {"max": 0.55, "mult": 1.10, "label": "fortress-bull-cycle-COIN"},
                {"max": 0.75, "mult": 1.00, "label": "in-band"},
                {"max": 0.95, "mult": 0.92, "label": "stretched"},
                {"max": 99,   "mult": 0.80, "label": "loss-making-bear-cycle"},
            ],
        },
        "kpis": [
            {"key": 'trading_volume_growth_yoy', "mandatory": True, "search_phrases": ['trading volume growth','transaction volume YoY','platform trading volume change'], "compute_hint": 'YoY % growth in trading volume (decimal)', "clamp": (-0.80, 5.0), "source": 'W', "extractor_only": True, "decimal_format": True},
            {"key": 'assets_on_platform_growth', "mandatory": True, "search_phrases": ['assets on platform growth','custody assets growth','AUM on exchange YoY','client assets growth'], "compute_hint": 'YoY % growth in assets held on platform (decimal)', "clamp": (-0.50, 3.0), "source": 'W', "extractor_only": True, "decimal_format": True},
            {"key": 'non_interest_expense_pct_rev', "mandatory": True, "search_phrases": ['non-interest expense as % of revenue','operating expense ratio','total expenses / revenue'], "compute_hint": 'Total non-interest expense / total revenue (decimal — fortress <55%)', "clamp": (0.0, 2.0), "source": 'F', "extractor_only": False, "decimal_format": True},
        ],
        "source_priority": ['Quarterly trading volume disclosures', 'Custody / assets on platform reports', 'Operating expense ratio'],
    },

    # ── G2: BTC Treasury / Proxy (MSTR — Saylor playbook) ──────────────
    # 2026: BTC Yield model — outperform raw BTC through accretive capital raises.
    'BTC Treasury / Proxy': {
        "sector":         'Crypto',
        "anchor_methods": ['mNAV', 'BTC NAV-Anchored DCF', 'EV/BTC Holdings'],
        # Q: btc_yield_pct (BTC-per-share growth) + mNAV_multiple (premium/discount to NAV).
        "quality_tiers": {
            "kpi_bands": [
                {"kpi": "btc_yield_pct", "direction": "higher_better",
                 "correlation_group": "btct_q_primary",
                 "bands": [
                     {"min":  0.20, "mult": 1.30, "label": "elite-accretive"},
                     {"min":  0.10, "mult": 1.15, "label": "strong"},
                     {"min":  0.05, "mult": 1.00, "label": "in-band"},
                     {"min": -99,   "mult": 0.80, "label": "weak-dilutive"},
                 ]},
                {"kpi": "mNAV_multiple", "direction": "higher_better",
                 "correlation_group": "btct_q_premium_kicker",
                 "bands": [
                     {"min": 2.0,  "mult": 1.30, "label": "premium-capital-raise-window"},
                     {"min": 1.2,  "mult": 1.15, "label": "modest-premium"},
                     {"min": 0.9,  "mult": 1.00, "label": "in-band"},
                     {"min": 0.0,  "mult": 0.80, "label": "discount-no-accretion"},
                 ]},
            ],
            "cap": [0.70, 1.50],
        },
        # R: btc_ltv_ratio (Liquidation Floor — debt/BTC value).
        "risk_adjustment": {
            "kpi": "btc_ltv_ratio", "direction": "lower_better",
            "bands": [
                {"max": 0.15, "mult": 1.10, "label": "fortress-MSTR-2020-conservative"},
                {"max": 0.30, "mult": 1.00, "label": "in-band-steady-state"},
                {"max": 0.45, "mult": 0.90, "label": "stretched-MSTR-2022-bear"},
                {"max": 99,   "mult": 0.70, "label": "distress-margin-call"},
            ],
        },
        "kpis": [
            {"key": 'btc_yield_pct', "mandatory": True, "search_phrases": ['BTC yield','BTC per share growth','accretive BTC accumulation','BTC holdings growth relative to share count'], "compute_hint": 'YoY growth in (btc_holdings/diluted_shares) (decimal)', "clamp": (-0.30, 1.0), "source": 'W', "extractor_only": True, "decimal_format": True},
            {"key": 'mNAV_multiple', "mandatory": True, "search_phrases": ['mNAV multiple','premium to NAV','price to BTC NAV','mark-to-NAV multiple'], "compute_hint": 'Market cap / (BTC holdings * BTC price)', "clamp": (0.3, 5.0), "source": 'W', "extractor_only": True},
            {"key": 'btc_ltv_ratio', "mandatory": True, "search_phrases": ['debt to BTC value ratio','BTC LTV','convertible notes vs BTC holdings'], "compute_hint": 'Total debt / (BTC holdings * BTC price) (decimal)', "clamp": (0.0, 1.0), "source": 'W', "extractor_only": True, "decimal_format": True},
            {"key": 'btc_holdings_value', "mandatory": True, "search_phrases": ['total BTC holdings value','BTC treasury value USD'], "clamp": (1e7, 1e12), "source": 'W', "extractor_only": True},
            {"key": 'btc_holdings_per_share', "mandatory": False, "search_phrases": ['BTC per share','satoshis per share'], "clamp": (0.000001, 0.1), "source": 'W', "extractor_only": True},
        ],
        "source_priority": ['MSTR investor presentations (BTC Yield disclosures)', 'mNAV multiple from research providers', 'Debt schedule + BTC holdings'],
    },

    # ── G3: Digital Asset Mining (MARA, RIOT, CIFR) ──────────────────────
    # Post-halving 2026: Cost-of-Production game.
    'Digital Asset Mining': {
        "sector":         'Crypto',
        "anchor_methods": ['EV/Hash', 'EV/EBITDA', 'NAV (BTC + Cash)', 'P/E (ops)'],
        # Q: hash_rate_growth_yoy + all_in_sustainable_cost_per_btc (AISC) — joint
        # qualification (separate groups multiply, full magnitudes capped).
        "quality_tiers": {
            "kpi_bands": [
                {"kpi": "hash_rate_growth_yoy", "direction": "higher_better",
                 "correlation_group": "miner_q_primary",
                 "bands": [
                     {"min":  0.40, "mult": 1.30, "label": "elite-aggressive-buildout"},
                     {"min":  0.20, "mult": 1.15, "label": "strong"},
                     {"min":  0.10, "mult": 1.00, "label": "in-band"},
                     {"min": -99,   "mult": 0.80, "label": "weak-shrinking-fleet"},
                 ]},
                {"kpi": "cost_per_btc_mined", "direction": "lower_better",
                 "correlation_group": "miner_q_aisc_kicker",
                 "bands": [
                     {"max": 45000, "mult": 1.30, "label": "elite-low-cost-CIFR-style"},
                     {"max": 65000, "mult": 1.15, "label": "strong"},
                     {"max": 85000, "mult": 1.00, "label": "in-band"},
                     {"max": 999999, "mult": 0.70, "label": "weak-uneconomic-MARA-stress"},
                 ]},
            ],
            "cap": [0.70, 1.50],
        },
        # R: cash_and_btc_runway_months — survive bear markets.
        "risk_adjustment": {
            "kpi": "cash_and_btc_runway_months", "direction": "higher_better",
            "bands": [
                {"min": 24, "mult": 1.10, "label": "fortress-CIFR-style"},
                {"min": 12, "mult": 1.00, "label": "in-band"},
                {"min":  6, "mult": 0.90, "label": "warning-post-halving-stress"},
                {"min":  0, "mult": 0.70, "label": "distress-forced-sell"},
            ],
        },
        "kpis": [
            {"key": 'hash_rate_growth_yoy',     "mandatory": True, "search_phrases": ['hash rate growth','EH/s growth YoY','mining capacity expansion'], "compute_hint": 'YoY growth in installed hash rate (decimal — >40% elite post-halving)', "clamp": (-0.50, 5.0), "source": 'W', "extractor_only": True, "decimal_format": True},
            {"key": 'cost_per_btc_mined',       "mandatory": True, "search_phrases": ['all-in sustainable cost per BTC','AISC per BTC','cost to mine BTC','direct cost per BTC mined'], "compute_hint": 'Total mining cost / BTC mined (USD per BTC — Elite <$45k, Weak >$85k)', "clamp": (10000, 250000), "source": 'W', "extractor_only": True},
            {"key": 'cash_and_btc_runway_months', "mandatory": True, "search_phrases": ['cash and BTC runway','liquidity runway months','months of operating cash plus BTC'], "compute_hint": '(cash + BTC value) / monthly opex (months — Fortress >24mo)', "clamp": (0, 99), "source": 'W', "extractor_only": True},
            {"key": 'all_in_sustainable_cost_per_btc', "mandatory": False, "search_phrases": ['AISC','all-in sustainable cost'], "clamp": (10000, 250000), "source": 'W', "extractor_only": True},
        ],
        "source_priority": ['Hash rate and AISC disclosures (monthly mining updates)', 'BTC holdings + cash position', 'Operating cost schedules'],
    },

# ── Energy ──────────────────────────────────────────────────
    'EPC Contractor': {
        "sector":         'Energy',
        "anchor_methods": ['Backlog DCF', 'EV/EBITDA', 'P/E (ops)'],
        # V3 quality: backlog_burn_rate primary (slow burn = long visibility) +
        # backlog_growth_yoy kicker.
        "quality_tiers": {
            "kpi_bands": [
                {"kpi": "backlog_burn_rate_pct", "direction": "lower_better",
                 "correlation_group": "epc_q_primary",
                 "bands": [
                     {"max": 0.25, "mult": 1.30, "label": "slow-burn-elite"},
                     {"max": 0.35, "mult": 1.15, "label": "strong"},
                     {"max": 0.50, "mult": 1.00, "label": "in-band"},
                     {"max": 99,   "mult": 0.85, "label": "rapid-burn"},
                 ]},
                {"kpi": "backlog_growth_yoy", "direction": "higher_better",
                 "correlation_group": "epc_q_kicker",
                 "bands": [
                     {"min":  0.20, "mult": 1.10, "label": "elite-growth"},
                     {"min":  0.0,  "mult": 1.00, "label": "in-band"},
                     {"min": -99,   "mult": 0.95, "label": "shrinking"},
                 ]},
            ],
            "cap": [0.70, 1.40],
        },
        # V3 risk: project_gross_margin (negative = death spiral — Bechtel/
        # Skanska 2018 lessons).
        "risk_adjustment": {
            "kpi": "project_gross_margin", "direction": "higher_better",
            "bands": [
                {"min": 0.10, "mult": 1.10, "label": "fortress"},
                {"min": 0.06, "mult": 1.05, "label": "strong"},
                {"min": 0.03, "mult": 1.00, "label": "in-band"},
                {"min": 0.0,  "mult": 0.85, "label": "compression"},
                {"min": -99,  "mult": 0.70, "label": "negative-death-spiral"},
            ],
        },
        "kpis": [
            {
                "key":             'backlog_burn_rate_pct',
                "mandatory":       True,
                "search_phrases":  ['backlog execution rate', 'revenue as % of opening backlog', 'project burn rate'],
                "compute_hint":    'annual_revenue / opening_backlog_balance (decimal — slow burn = long visibility)',
                "clamp":           (0.0, 1.0),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'order_backlog_usd',
                "mandatory":       True,
                "search_phrases":  ['total contracted backlog', 'remaining performance obligations', 'order book value'],
                "clamp":           (1e8, 1e11),
                "source":          'W',
                "extractor_only":  True,
            },
            {
                "key":             'backlog_growth_yoy',
                "mandatory":       True,
                "search_phrases":  ['backlog growth YoY', 'order book expansion', 'contracted backlog change'],
                "compute_hint":    'YoY change in order_backlog_usd (decimal)',
                "clamp":           (-0.50, 1.0),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'project_gross_margin',
                "mandatory":       True,
                "search_phrases":  ['weighted average project margin', 'project gross margin', 'EPC margin'],
                "compute_hint":    'Weighted average project gross margin (decimal — negative = death spiral)',
                "clamp":           (-0.20, 0.30),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
        ],
        "source_priority": ['Contract award announcements', 'Project burn rate disclosures', 'Project gross margin schedule'],
    },

    'Energy Tech Licensor': {
        "sector":         'Energy',
        "anchor_methods": ['Licensing NPV', 'Real Options', 'EV/Forward Revenue', 'TAM Penetration'],
        # V3 quality: royalty_revenue_pct primary + licensed_capacity_growth_yoy
        # kicker (Platform Velocity per user — raw GW lumpy, growth signals
        # adoption velocity).
        "quality_tiers": {
            "kpi_bands": [
                {"kpi": "royalty_revenue_pct", "direction": "higher_better",
                 "correlation_group": "etl_q_primary",
                 "bands": [
                     {"min": 0.40, "mult": 1.30, "label": "royalty-rich-elite"},
                     {"min": 0.25, "mult": 1.15, "label": "strong"},
                     {"min": 0.10, "mult": 1.00, "label": "in-band"},
                     {"min": 0.0,  "mult": 0.85, "label": "transactional-weak"},
                 ]},
                {"kpi": "licensed_capacity_growth_yoy", "direction": "higher_better",
                 "correlation_group": "etl_q_velocity_kicker",
                 "bands": [
                     {"min": 0.30, "mult": 1.30, "label": "massive-adoption"},
                     {"min": 0.15, "mult": 1.15, "label": "strong-velocity"},
                     {"min": 0.05, "mult": 1.00, "label": "pilot-phase"},
                     {"min": -99,  "mult": 0.80, "label": "obsolescence"},
                 ]},
            ],
            "cap": [0.70, 1.50],
        },
        # V3 risk: cash_runway_years — for licensors like Plug Power, Survival
        # is the only Moat. Royalty growth is lagging; runway is leading.
        "risk_adjustment": {
            "kpi": "cash_runway_years", "direction": "higher_better",
            "bands": [
                {"min": 3.0,  "mult": 1.10, "label": "fortress"},
                {"min": 1.5,  "mult": 1.00, "label": "in-band"},
                {"min": 0.75, "mult": 0.90, "label": "warning"},
                {"min": 0.0,  "mult": 0.70, "label": "distressed"},
            ],
        },
        "kpis": [
            {
                "key":             'royalty_revenue_pct',
                "mandatory":       True,
                "search_phrases":  ['royalty and licensing revenue share', 'recurring royalty contribution'],
                "compute_hint":    'total_royalty_revenue / total_revenue (decimal — >40% royalty-rich elite)',
                "clamp":           (0.0, 1.0),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'licensed_capacity_growth_yoy',
                "mandatory":       True,
                "search_phrases":  ['licensed capacity growth', 'GW adoption growth', 'platform deployment YoY'],
                "compute_hint":    'YoY growth in licensed_capacity_gw (decimal — >30% massive adoption)',
                "clamp":           (-0.50, 2.0),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'patent_portfolio_count',
                "mandatory":       False,
                "search_phrases":  ['active patents', 'technology disclosures'],
                "source":          'W',
                "extractor_only":  True,
            },
            {
                "key":             'licensed_capacity_gw',
                "mandatory":       False,
                "search_phrases":  ['total licensed capacity', 'installed technology base'],
                "source":          'W',
                "extractor_only":  True,
            },
            {
                "key":             'cash_runway_years',
                "mandatory":       True,
                "search_phrases":  ['cash runway months', 'months of cash', 'liquidity runway'],
                "compute_hint":    'cash + ST investments / (annualized burn rate) — FMP-augmented',
                "clamp":           (0.0, 99.0),
                "source":          'F',
                "extractor_only":  False,
            },
        ],
        "source_priority": ['Royalty revenue segment logs', 'Licensed capacity growth (GW)', 'Cash runway disclosures'],
    },

    'IPP': {
        "sector":         'Energy',
        "anchor_methods": ['PPA-backed DCF', 'EV/EBITDA', 'P/AFFO'],
        # V3 quality: ppa_coverage_pct primary + WALE kicker.
        "quality_tiers": {
            "kpi_bands": [
                {"kpi": "ppa_coverage_pct", "direction": "higher_better",
                 "correlation_group": "ipp_q_primary",
                 "bands": [
                     {"min": 0.90, "mult": 1.30, "label": "fortress-contracted"},
                     {"min": 0.75, "mult": 1.15, "label": "strong"},
                     {"min": 0.60, "mult": 1.00, "label": "in-band"},
                     {"min": 0.0,  "mult": 0.85, "label": "merchant-exposed"},
                 ]},
                {"kpi": "weighted_avg_contract_life", "direction": "higher_better",
                 "correlation_group": "ipp_q_wale_kicker",
                 "bands": [
                     {"min": 10, "mult": 1.10, "label": "elite-WALE"},
                     {"min": 5,  "mult": 1.05, "label": "strong-WALE"},
                     {"min": 0,  "mult": 0.95, "label": "exposure-risk"},
                 ]},
            ],
            "cap": [0.70, 1.40],
        },
        # V3 risk: net_debt_to_ebitda — IPPs are LEVERAGED infrastructure (project finance norm).
        "risk_adjustment": {
            "kpi": "net_debt_to_ebitda", "direction": "lower_better",
            "bands": [
                {"max": 4.0,  "mult": 1.10, "label": "fortress-project-finance"},
                {"max": 6.0,  "mult": 1.00, "label": "in-band"},
                {"max": 8.0,  "mult": 0.92, "label": "stretched"},
                {"max": 99,   "mult": 0.85, "label": "weak"},
            ],
        },
        "kpis": [
            {
                "key":             'ppa_coverage_pct',
                "mandatory":       True,
                "search_phrases":  ['capacity under long-term PPA', 'contracted revenue mix', 'PPA-backed capacity %'],
                "clamp":           (0.0, 1.0),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'weighted_avg_contract_life',
                "mandatory":       True,
                "search_phrases":  ['average remaining PPA term', 'WALE for power contracts', 'contract duration years'],
                "compute_hint":    'Weighted-avg years remaining on power contracts',
                "clamp":           (1, 30),
                "source":          'W',
                "extractor_only":  True,
            },
            {
                "key":             'net_debt_to_ebitda',
                "mandatory":       True,
                "search_phrases":  ['net debt to EBITDA', 'leverage ratio', 'project finance leverage'],
                "clamp":           (-3.0, 12.0),
                "source":          'F',
                "extractor_only":  False,
                "fmp_field":       'netDebtToEBITDATTM',
            },
            {
                "key":             'installed_capacity_gw',
                "mandatory":       False,
                "search_phrases":  ['total operating capacity', 'megawatts in operation'],
                "source":          'W',
                "extractor_only":  True,
            },
        ],
        "source_priority": ['Long-term PPA disclosures', 'WALE schedules', 'Net leverage disclosures'],
    },

    'Merchant Power': {
        "sector":         'Energy',
        "anchor_methods": ['EV/EBITDA', 'FCF Yield', 'Power Price DCF', 'LBO Floor'],
        # V3 quality: realized_spark_spread primary ($/MWh — Nuclear AI-uptime
        # premium per CEG/VST 2026) + hedged_revenue_pct kicker (risk dampener).
        "quality_tiers": {
            "kpi_bands": [
                {"kpi": "realized_spark_spread", "direction": "higher_better",
                 "correlation_group": "merchant_q_primary",
                 "bands": [
                     {"min": 35,  "mult": 1.30, "label": "nuclear-AI-elite-CEG"},
                     {"min": 25,  "mult": 1.15, "label": "high-eff-CCGT"},
                     {"min": 15,  "mult": 1.00, "label": "in-band"},
                     {"min":  0,  "mult": 0.80, "label": "commodity-trap"},
                 ]},
                {"kpi": "hedged_revenue_pct", "direction": "higher_better",
                 "correlation_group": "merchant_q_hedge_kicker",
                 "bands": [
                     {"min": 0.70, "mult": 1.10, "label": "well-hedged"},
                     {"min": 0.40, "mult": 1.00, "label": "in-band"},
                     {"min": 0.0,  "mult": 0.90, "label": "open-exposure"},
                 ]},
            ],
            "cap": [0.70, 1.45],
        },
        "risk_adjustment": {
            "kpi": "net_debt_to_ebitda", "direction": "lower_better",
            "bands": [
                {"max": 3.5,  "mult": 1.10, "label": "fortress"},
                {"max": 5.0,  "mult": 1.00, "label": "in-band"},
                {"max": 7.0,  "mult": 0.92, "label": "stretched"},
                {"max": 99,   "mult": 0.85, "label": "weak"},
            ],
        },
        "kpis": [
            {
                "key":             'realized_spark_spread',
                "mandatory":       True,
                "search_phrases":  ['realized spark spread', 'dark spread per MWh', 'generation margin per unit', '$/MWh spread'],
                "compute_hint":    'average_realized_power_price - (fuel_cost_per_unit * heat_rate) — USD per MWh',
                "clamp":           (0.0, 200.0),
                "source":          'W',
                "extractor_only":  True,
            },
            {
                "key":             'hedged_revenue_pct',
                "mandatory":       True,
                "search_phrases":  ['forward hedging percentage', 'locked-in revenue for next 12 months', 'hedged revenue %'],
                "compute_hint":    'Forward-hedged revenue / total revenue (decimal)',
                "clamp":           (0.0, 1.0),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'net_debt_to_ebitda',
                "mandatory":       True,
                "search_phrases":  ['net debt to EBITDA', 'leverage ratio'],
                "clamp":           (-3.0, 12.0),
                "source":          'F',
                "extractor_only":  False,
                "fmp_field":       'netDebtToEBITDATTM',
            },
            {
                "key":             'generation_output_mwh',
                "mandatory":       False,
                "search_phrases":  ['total gigawatt hours generated', 'GWh output'],
                "source":          'W',
                "extractor_only":  True,
            },
        ],
        "source_priority": ['Realized spark spread indices', 'Forward hedging logs', 'Generation availability'],
    },

# ── Financials ──────────────────────────────────────────────────
    'Alt Asset Manager': {
        "sector":         'Financials',
        "anchor_methods": ['SOTP', 'P/FRE', 'P/E'],
        # V3 quality: FRE margin = primary anchor (the "Blackstone Standard"
        # — fee-related earnings margin is the moat); FPAUM growth =
        # tie-breaker. They share a correlation group so max-pick — the
        # better of the two drives the lift.
        "quality_tiers": {
            "kpi_bands": [
                {"kpi": "fre_margin_pct", "direction": "higher_better",
                 "correlation_group": "alt_am_q",
                 "bands": [
                     {"min": 0.60, "mult": 1.30, "label": "elite"},
                     {"min": 0.52, "mult": 1.15, "label": "strong"},
                     {"min": 0.45, "mult": 1.00, "label": "in-band"},
                     {"min": 0.0,  "mult": 0.80, "label": "weak"},
                 ]},
                {"kpi": "fpaum_growth_pct", "direction": "higher_better",
                 "correlation_group": "alt_am_q",
                 "bands": [
                     {"min": 0.20, "mult": 1.20, "label": "elite-flows"},
                     {"min": 0.10, "mult": 1.10, "label": "strong-flows"},
                     {"min": 0.0,  "mult": 1.00, "label": "in-band"},
                     {"min": -99,  "mult": 0.90, "label": "outflows"},
                 ]},
            ],
            "cap": [0.70, 1.50],
        },
        # V3 risk: Net Debt / FRE EBITDA — leverage relative to recurring
        # cash flow. Alt-AMs run lighter than banks but must be measured
        # against their FEE income (not total income, which is volatile
        # carry).
        "risk_adjustment": {
            "kpi": "net_debt_to_fre_ebitda", "direction": "lower_better",
            "bands": [
                {"max": 1.5, "mult": 1.10, "label": "fortress"},
                {"max": 3.0, "mult": 1.00, "label": "in-band"},
                {"max": 4.5, "mult": 0.92, "label": "stretched"},
                {"max": 99,  "mult": 0.80, "label": "over-levered"},
            ],
        },
        "kpis": [
            {
                "key":             'fre_margin_pct',
                "mandatory":       True,
                "search_phrases":  ['fee-related earnings margin', 'FRE margin'],
                "clamp":           (0.2, 0.7),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'fpaum_growth_pct',
                "mandatory":       True,
                "search_phrases":  ['fee-paying AUM growth', 'FPAUM growth', 'fee-paying assets'],
                "compute_hint":    'YoY growth in fee-paying AUM (decimal — distinct from total AUM)',
                "clamp":           (-0.20, 0.50),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'aum_growth_yoy_pct',
                "mandatory":       True,
                "search_phrases":  ['AUM growth', 'assets under management growth'],
                "clamp":           (-0.1, 0.4),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'net_debt_to_fre_ebitda',
                "mandatory":       True,
                "search_phrases":  ['net debt to FRE EBITDA', 'leverage to fee-related earnings'],
                "compute_hint":    'Net debt / TTM FRE EBITDA — leverage measured vs RECURRING fee earnings (not carry)',
                "clamp":           (-2.0, 10.0),
                "source":          'W',
                "extractor_only":  True,
            },
            {
                "key":             'dry_powder_usd',
                "mandatory":       False,
                "search_phrases":  ['uncalled capital', 'dry powder'],
                "source":          'W',
                "extractor_only":  True,
            },
        ],
        "source_priority": ['Quarterly Non-GAAP Supplements (FRE margin + FPAUM)', 'Net debt / FRE EBITDA disclosures', 'Investor presentations'],
    },

    'Bank / Lending Institution': {
        "sector":         'Financials',
        "anchor_methods": ['Residual Income', 'P/TBV', 'P/E', 'Excess Capital'],
        # V3 quality: NIM (developed bands) + efficiency_ratio (US-Stringent
        # bands), correlated.
        "quality_tiers": {
            "kpi_bands": [
                {"kpi": "nim_pct", "direction": "higher_better",
                 "correlation_group": "lending_q",
                 "bands": [
                     {"min": 0.032, "mult": 1.30, "label": "elite"},
                     {"min": 0.026, "mult": 1.15, "label": "strong"},
                     {"min": 0.020, "mult": 1.00, "label": "in-band"},
                     {"min": 0.0,   "mult": 0.85, "label": "weak"},
                 ]},
                {"kpi": "efficiency_ratio", "direction": "lower_better",
                 "correlation_group": "lending_q",
                 "bands": [
                     {"max": 0.50, "mult": 1.30, "label": "elite"},
                     {"max": 0.58, "mult": 1.15, "label": "strong"},
                     {"max": 0.68, "mult": 1.00, "label": "in-band"},
                     {"max": 99.0, "mult": 0.85, "label": "bloated"},
                 ]},
            ],
            "cap": [0.70, 1.50],
        },
        # V3 risk: CET1, Money Center bands.
        "risk_adjustment": {
            "kpi": "cet1_ratio", "direction": "higher_better",
            "bands": [
                {"min": 0.145, "mult": 1.10, "label": "fortress"},
                {"min": 0.130, "mult": 1.05, "label": "strong"},
                {"min": 0.115, "mult": 1.00, "label": "in-band"},
                {"min": 0.0,   "mult": 0.85, "label": "weak"},
            ],
        },
        "kpis": [
            {
                "key":             'nim_pct',
                "mandatory":       True,
                "search_phrases":  ['Net Interest Margin', 'NIM', 'net interest spread'],
                "clamp":           (0.01, 0.08),
                "source":          'H',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'npl_ratio_pct',
                "mandatory":       True,
                "search_phrases":  ['Non-Performing Loans ratio', 'Gross NPL ratio', 'impaired loans %'],
                "clamp":           (0.0, 0.15),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'efficiency_ratio',
                "mandatory":       True,
                "search_phrases":  ['cost-to-income ratio', 'Efficiency Ratio'],
                "compute_hint":    'Operating expenses / total revenue (decimal)',
                "clamp":           (0.30, 1.00),
                "source":          'H',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'cet1_ratio',
                "mandatory":       True,
                "search_phrases":  ['CET1 ratio', 'Common Equity Tier 1', 'CET-1'],
                "clamp":           (0.05, 0.25),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
        ],
        "source_priority": ['Statutory filings (NSE/BSE, SGX, 10-K)', 'NIM/NPL ratio disclosures', 'CASA (Current and Savings Account) mix reports'],
    },

    'Brokerage': {
        "sector":         'Financials',
        "anchor_methods": ['P/E', 'P/AUM', 'DCF (FCF)'],
        # V3 quality: NNA Capture (NNA / AUM) is the platform-gravity moat;
        # cash_as_pct_of_client_assets is the monetisation tie-breaker (the
        # "Schwab Model" — high cash% = hidden NIM in high-rate regime).
        # NNA Capture computed via derived_kpis below from the existing
        # net_new_assets_usd + interest_earning_assets_usd dollar values.
        "derived_kpis": [
            {"key":         "nna_capture_pct",
             "numerator":   "net_new_assets_usd",
             "denominator": "interest_earning_assets_usd"},
        ],
        "quality_tiers": {
            "kpi_bands": [
                {"kpi": "nna_capture_pct", "direction": "higher_better",
                 "correlation_group": "brokerage_q_primary",
                 "bands": [
                     {"min": 0.10, "mult": 1.30, "label": "elite"},
                     {"min": 0.07, "mult": 1.15, "label": "strong"},
                     {"min": 0.04, "mult": 1.00, "label": "in-band"},
                     {"min": 0.0,  "mult": 0.85, "label": "weak"},
                 ]},
                # Cash% as monetisation tie-breaker — separate group so it
                # MULTIPLIES with the NNA Capture pick.
                {"kpi": "cash_as_pct_of_client_assets", "direction": "higher_better",
                 "correlation_group": "brokerage_q_kicker",
                 "bands": [
                     {"min": 0.15, "mult": 1.10, "label": "high-cash-monetisation"},
                     {"min": 0.08, "mult": 1.05, "label": "above-avg-cash"},
                     {"min": 0.0,  "mult": 1.00, "label": "in-band"},
                 ]},
            ],
            "cap": [0.70, 1.50],
        },
        # V3 risk: equity_to_assets_pct as the universal capital-cushion proxy
        # (Net Capital Rule excess is rarely disclosed cleanly in earnings).
        "risk_adjustment": {
            "kpi": "equity_to_assets_pct", "direction": "higher_better",
            "bands": [
                {"min": 0.20, "mult": 1.15, "label": "fortress"},
                {"min": 0.12, "mult": 1.05, "label": "strong"},
                {"min": 0.08, "mult": 1.00, "label": "in-band"},
                {"min": 0.0,  "mult": 0.85, "label": "weak"},
            ],
        },
        "kpis": [
            {
                "key":             'net_new_assets_usd',
                "mandatory":       True,
                "search_phrases":  ['net new assets', 'NNA'],
                "clamp":           (1000000000, 500000000000),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  False,
            },
            {
                "key":             'interest_earning_assets_usd',
                "mandatory":       True,
                "search_phrases":  ['total interest-earning assets', 'IEA', 'AUM', 'total client assets'],
                "compute_hint":    'Total client AUM in USD (denominator of NNA Capture derived KPI)',
                "clamp":           (1e10, 1e13),
                "source":          'W',
                "extractor_only":  True,
            },
            {
                "key":             'cash_as_pct_of_client_assets',
                "mandatory":       True,
                "search_phrases":  ['cash as % of total client assets', 'sweep balances'],
                "compute_hint":    'Client cash + money market / total client assets (decimal)',
                "clamp":           (0.0, 0.50),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'equity_to_assets_pct',
                "mandatory":       True,
                "search_phrases":  ['shareholders equity / total assets', 'capital ratio',
                                    'tangible common equity ratio'],
                "compute_hint":    'Shareholders equity / total assets (decimal — universal capital cushion proxy)',
                "clamp":           (0.0, 0.50),
                "source":          'F',
                "extractor_only":  False,
                "decimal_format":  True,
            },
            {
                "key":             'recurring_data_rev_pct',
                "mandatory":       False,
                "search_phrases":  ['recurring data revenue', 'information services mix'],
                "clamp":           (0.1, 0.7),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
        ],
        "source_priority": ['SEC 10-K/Q', 'Supplemental Earnings Data (NNA / cash sweep balances)', 'Management Commentary'],
    },

    'EM Bank': {
        "sector":         'Financials',
        "anchor_methods": ['Residual Income', 'P/TBV'],
        # V3 quality: NIM (EM bands — structurally higher than developed) +
        # CASA ratio (correlated as the income engine). Plus an INDEPENDENT
        # CASA "funding moat" kicker — when CASA >45%, add 1.05x as a
        # separate group so it MULTIPLIES with the main quality pick.
        # Two band entries for casa_ratio_pct: one in correlation_group
        # `em_bank_q` (max-pick alongside NIM), one in `em_bank_funding_moat`
        # (independent kicker, multiplies). Same KPI evaluated twice — by
        # design.
        "quality_tiers": {
            "kpi_bands": [
                {"kpi": "nim_pct", "direction": "higher_better",
                 "correlation_group": "em_bank_q",
                 "bands": [
                     {"min": 0.055, "mult": 1.30, "label": "elite"},
                     {"min": 0.045, "mult": 1.15, "label": "strong"},
                     {"min": 0.035, "mult": 1.00, "label": "in-band"},
                     {"min": 0.0,   "mult": 0.85, "label": "weak"},
                 ]},
                {"kpi": "casa_ratio_pct", "direction": "higher_better",
                 "correlation_group": "em_bank_q",
                 "bands": [
                     {"min": 0.40, "mult": 1.15, "label": "strong"},
                     {"min": 0.25, "mult": 1.00, "label": "in-band"},
                     {"min": 0.0,  "mult": 0.90, "label": "weak"},
                 ]},
                # Independent funding-moat kicker — multiplies on top of the
                # max-pick from em_bank_q. Only fires when CASA >45%.
                {"kpi": "casa_ratio_pct", "direction": "higher_better",
                 "correlation_group": "em_bank_funding_moat",
                 "bands": [
                     {"min": 0.45, "mult": 1.05, "label": "funding-moat"},
                     {"min": 0.0,  "mult": 1.00, "label": "no-moat"},
                 ]},
            ],
            "cap": [0.70, 1.50],
        },
        # V3 risk: CET1 with EM-bumped bands (12% in EM ≠ 12% in developed
        # — currency volatility erodes capital faster).
        "risk_adjustment": {
            "kpi": "cet1_ratio", "direction": "higher_better",
            "bands": [
                {"min": 0.150, "mult": 1.10, "label": "fortress"},
                {"min": 0.135, "mult": 1.05, "label": "strong"},
                {"min": 0.120, "mult": 1.00, "label": "in-band"},
                {"min": 0.0,   "mult": 0.85, "label": "weak"},
            ],
        },
        "kpis": [
            {
                "key":             'casa_ratio_pct',
                "mandatory":       True,
                "search_phrases":  ['CASA ratio', 'current and savings account mix'],
                "clamp":           (0.2, 0.6),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'npl_ratio_pct',
                "mandatory":       True,
                "search_phrases":  ['non-performing loan ratio', 'gross NPL'],
                "clamp":           (0.0, 0.15),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'nim_pct',
                "mandatory":       True,
                "search_phrases":  ['net interest margin'],
                "compute_hint":    'NIM as decimal (0.045 = 4.5%, EM banks structurally higher than developed)',
                "clamp":           (0.01, 0.10),
                "source":          'H',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'cet1_ratio',
                "mandatory":       True,
                "search_phrases":  ['CET1 ratio', 'Common Equity Tier 1', 'CET-1'],
                "compute_hint":    'CET1 / RWA decimal — EM regulator usually requires 9-10% min',
                "clamp":           (0.05, 0.25),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
        ],
        "source_priority": ['Statutory Filings (NSE/BSE/SGX)', 'CET1 ratio + CASA disclosures'],
    },

    'EM Bank (Premium)': {
        "sector":         'Financials',
        "anchor_methods": ['Residual Income', 'P/TBV'],
        # V3 quality: ROA (the "alpha" metric — premium EM banks generate
        # >2% ROA in growth markets) + CASA ratio (correlated). Plus
        # independent CASA funding-moat kicker — same pattern as EM Bank.
        "quality_tiers": {
            "kpi_bands": [
                {"kpi": "roa_pct", "direction": "higher_better",
                 "correlation_group": "em_premium_q",
                 "bands": [
                     {"min": 0.020, "mult": 1.30, "label": "elite"},
                     {"min": 0.015, "mult": 1.15, "label": "strong"},
                     {"min": 0.010, "mult": 1.00, "label": "in-band"},
                     {"min": 0.0,   "mult": 0.85, "label": "weak"},
                 ]},
                {"kpi": "casa_ratio_pct", "direction": "higher_better",
                 "correlation_group": "em_premium_q",
                 "bands": [
                     {"min": 0.40, "mult": 1.15, "label": "strong"},
                     {"min": 0.25, "mult": 1.00, "label": "in-band"},
                     {"min": 0.0,  "mult": 0.90, "label": "weak"},
                 ]},
                # Independent CASA funding-moat kicker (multiplies)
                {"kpi": "casa_ratio_pct", "direction": "higher_better",
                 "correlation_group": "em_premium_funding_moat",
                 "bands": [
                     {"min": 0.45, "mult": 1.05, "label": "funding-moat"},
                     {"min": 0.0,  "mult": 1.00, "label": "no-moat"},
                 ]},
            ],
            "cap": [0.70, 1.50],
        },
        # V3 risk: CET1, EM bands.
        "risk_adjustment": {
            "kpi": "cet1_ratio", "direction": "higher_better",
            "bands": [
                {"min": 0.150, "mult": 1.10, "label": "fortress"},
                {"min": 0.135, "mult": 1.05, "label": "strong"},
                {"min": 0.120, "mult": 1.00, "label": "in-band"},
                {"min": 0.0,   "mult": 0.85, "label": "weak"},
            ],
        },
        "kpis": [
            {
                "key":             'casa_ratio_pct',
                "mandatory":       True,
                "search_phrases":  ['CASA ratio', 'current and savings account mix'],
                "clamp":           (0.2, 0.6),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'roa_pct',
                "mandatory":       True,
                "search_phrases":  ['Return on Assets', 'ROA'],
                "compute_hint":    'Net income / total assets (decimal — 0.02 = 2.0%; premium EM banks generate >2%)',
                "clamp":           (0.0, 0.05),
                "source":          'H',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'cet1_ratio',
                "mandatory":       True,
                "search_phrases":  ['CET1 ratio', 'Common Equity Tier 1'],
                "clamp":           (0.05, 0.25),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'provision_coverage_ratio',
                "mandatory":       False,
                "search_phrases":  ['PCR', 'NPL coverage'],
                "source":          'W',
                "extractor_only":  True,
            },
            {
                "key":             'npl_ratio_pct',
                "mandatory":       False,
                "search_phrases":  ['non-performing loan ratio', 'gross NPL'],
                "clamp":           (0.0, 0.15),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
        ],
        "source_priority": ['Statutory Filings (NSE/BSE/SGX)', 'CET1 + ROA disclosures'],
    },

    'FinTech': {
        "sector":         'Financials',
        "anchor_methods": ['EV/NTM Revenue', 'P/E (ops)', 'DCF (FCF)'],
        # V3 quality: TPV growth (volume) + take_rate_stability (pricing
        # power, lower delta = better). Correlated as the "platform vitality"
        # signal.
        "quality_tiers": {
            "kpi_bands": [
                {"kpi": "tpv_growth_yoy", "direction": "higher_better",
                 "correlation_group": "fintech_q",
                 "bands": [
                     {"min": 0.30, "mult": 1.30, "label": "elite"},
                     {"min": 0.18, "mult": 1.15, "label": "strong"},
                     {"min": 0.10, "mult": 1.00, "label": "in-band"},
                     {"min": 0.0,  "mult": 0.90, "label": "decel"},
                     {"min": -99,  "mult": 0.80, "label": "shrinking"},
                 ]},
                {"kpi": "take_rate_stability_bps", "direction": "lower_better",
                 "correlation_group": "fintech_q",
                 "bands": [
                     {"max":  3, "mult": 1.10, "label": "stable-pricing"},
                     {"max":  8, "mult": 1.00, "label": "in-band"},
                     {"max": 99, "mult": 0.90, "label": "compression"},
                 ]},
            ],
            "cap": [0.70, 1.50],
        },
        # V3 risk: incentive_ratio_pct (client incentives / gross revenue).
        # Heavy incentives signal pricing power loss — banks demanding
        # discounts to keep volume on the network.
        "risk_adjustment": {
            "kpi": "incentive_ratio_pct", "direction": "lower_better",
            "bands": [
                {"max": 0.22, "mult": 1.10, "label": "fortress"},
                {"max": 0.28, "mult": 1.00, "label": "in-band"},
                {"max": 0.32, "mult": 0.92, "label": "stretched"},
                {"max": 99,   "mult": 0.85, "label": "weak"},
            ],
        },
        "kpis": [
            {
                "key":             'tpv_growth_yoy',
                "mandatory":       True,
                "search_phrases":  ['Total Payment Volume growth', 'processed volume'],
                "clamp":           (-0.20, 1.50),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'tpv_growth_pct',
                "mandatory":       True,
                "search_phrases":  ['Total Payment Volume growth', 'TPV YOY', 'processed volume expansion'],
                "clamp":           (-0.20, 1.50),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'take_rate_bps',
                "mandatory":       True,
                "search_phrases":  ['net take rate in bps', 'revenue as bps of volume'],
                "clamp":           (5, 300),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  False,
            },
            {
                "key":             'take_rate_stability_bps',
                "mandatory":       True,
                "search_phrases":  ['take rate change YoY in bps', 'pricing stability', 'take rate compression'],
                "compute_hint":    'Absolute change in take_rate_bps YoY (lower = stable pricing power)',
                "clamp":           (0, 50),
                "source":          'W',
                "extractor_only":  True,
            },
            {
                "key":             'incentive_ratio_pct',
                "mandatory":       True,
                "search_phrases":  ['client incentives', 'rebates and incentives % of revenue', 'incentive ratio'],
                "compute_hint":    'Client incentives / gross revenue (decimal — V/MA fortress < 22%)',
                "clamp":           (0.0, 0.50),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'ltv_cac_ratio',
                "mandatory":       False,
                "search_phrases":  ['lifetime value to acquisition cost'],
                "source":          'W',
                "extractor_only":  True,
            },
        ],
        "source_priority": ['Take-rate (bps) trends', 'TPV growth logs', 'Incentive ratio disclosures (V/MA 10-K)'],
    },

    'Holding Company': {
        "sector":         'Financials',
        "anchor_methods": ['SOTP / Net Asset Value', 'P/Book', 'DDM'],
        # V3 quality: Look-Through Earnings Growth (primary moat) +
        # cash-as-%-of-NAV (war-chest tie-breaker). Cash% is computed via
        # derived_kpis from existing dollar fields if not directly extracted.
        "derived_kpis": [
            {"key":         "cash_to_nav_pct",
             "numerator":   "cash_and_equivalents_usd",
             "denominator": "sotp_nav_per_share"},
        ],
        "quality_tiers": {
            "kpi_bands": [
                {"kpi": "look_through_earnings_growth_pct", "direction": "higher_better",
                 "correlation_group": "holdco_q",
                 "bands": [
                     {"min": 0.15, "mult": 1.30, "label": "elite"},
                     {"min": 0.08, "mult": 1.15, "label": "strong"},
                     {"min": 0.03, "mult": 1.00, "label": "in-band"},
                     {"min": -99,  "mult": 0.85, "label": "weak"},
                 ]},
                # War-chest kicker — high cash % of NAV signals optionality
                # to deploy during market dislocations (Buffett standard).
                {"kpi": "cash_to_nav_pct", "direction": "higher_better",
                 "correlation_group": "holdco_q_kicker",
                 "bands": [
                     {"min": 0.15, "mult": 1.10, "label": "war-chest"},
                     {"min": 0.07, "mult": 1.05, "label": "ample-cash"},
                     {"min": 0.0,  "mult": 1.00, "label": "in-band"},
                 ]},
            ],
            "cap": [0.70, 1.50],
        },
        # V3 risk: Holdco-level Debt / Total NAV. >15% is the red-flag
        # threshold for a diversified holdco (per user spec).
        "risk_adjustment": {
            "kpi": "debt_to_nav_pct", "direction": "lower_better",
            "bands": [
                {"max": 0.05, "mult": 1.10, "label": "fortress"},
                {"max": 0.10, "mult": 1.00, "label": "in-band"},
                {"max": 0.15, "mult": 0.95, "label": "stretched"},
                {"max": 99,   "mult": 0.85, "label": "red-flag"},
            ],
        },
        "kpis": [
            {
                "key":             'sotp_nav_per_share',
                "mandatory":       True,
                "search_phrases":  ['intrinsic value per share', 'Sum-of-the-parts NAV', 'book value plus look-through'],
                "clamp":           (100, 1000000),
                "source":          'W',
                "extractor_only":  True,
            },
            {
                "key":             'look_through_earnings_usd',
                "mandatory":       True,
                "search_phrases":  ['proportionate share of investee earnings', 'look-through net income', 'total economic earnings'],
                "clamp":           (1000000000, 200000000000),
                "source":          'W',
                "extractor_only":  True,
            },
            {
                "key":             'look_through_earnings_growth_pct',
                "mandatory":       True,
                "search_phrases":  ['look-through earnings growth', 'pro-rata investee earnings growth',
                                    'subsidiary earnings + investee share growth'],
                "compute_hint":    'YoY growth of (subsidiary earnings + pro-rata share of investee earnings), decimal',
                "clamp":           (-0.30, 0.50),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'cash_and_equivalents_usd',
                "mandatory":       True,
                "search_phrases":  ['total cash and short-term investments', 'cash position'],
                "compute_hint":    'Total cash + short-term investments USD (numerator of cash_to_nav_pct derived KPI)',
                "clamp":           (1e8, 5e11),
                "source":          'F',
                "extractor_only":  False,
            },
            {
                "key":             'debt_to_nav_pct',
                "mandatory":       True,
                "search_phrases":  ['holding company debt to NAV', 'parent-level debt / total NAV',
                                    'debt to net asset value'],
                "compute_hint":    'Holdco-level debt / total NAV (decimal — >15% red flag for diversified holdco)',
                "clamp":           (0.0, 0.50),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
        ],
        "source_priority": ['Intrinsic value / NAV per share disclosures', 'Look-through earnings tables', 'Holdco-level debt schedule'],
    },

    'Investment Bank': {
        "sector":         'Financials',
        "anchor_methods": ['Residual Income', 'P/TBV', 'P/E', 'Excess Capital'],
        # V3 quality: comp_ratio = primary anchor (the controllable cost
        # discipline lever); advisory_backlog_growth = momentum kicker. They
        # live in SEPARATE correlation groups so they MULTIPLY (primary +
        # kicker semantics rather than max-pick), giving comp_ratio the
        # heavier swing per user's 0.7/0.3 weighting intent.
        "quality_tiers": {
            "kpi_bands": [
                {"kpi": "compensation_ratio", "direction": "lower_better",
                 "correlation_group": "ib_q_primary",
                 "bands": [
                     {"max": 0.35, "mult": 1.30, "label": "elite"},
                     {"max": 0.42, "mult": 1.15, "label": "strong"},
                     {"max": 0.50, "mult": 1.00, "label": "in-band"},
                     {"max": 99.0, "mult": 0.85, "label": "weak"},
                 ]},
                {"kpi": "advisory_backlog_growth", "direction": "higher_better",
                 "correlation_group": "ib_q_kicker",
                 "bands": [
                     {"min":  0.20, "mult": 1.10, "label": "elite-pipeline"},
                     {"min":  0.10, "mult": 1.05, "label": "strong-pipeline"},
                     {"min":  0.0,  "mult": 1.00, "label": "in-band"},
                     {"min": -99.0, "mult": 0.95, "label": "shrinking"},
                 ]},
            ],
            "cap": [0.70, 1.50],
        },
        # V3 risk: Liquidity Coverage Ratio (LCR) — IBs face Basel III LCR
        # requirements (≥100% min). Above 130% = fortress, below 100% =
        # severe regulatory failure (rare but catastrophic).
        "risk_adjustment": {
            "kpi": "liquidity_coverage_ratio", "direction": "higher_better",
            "bands": [
                {"min": 1.30, "mult": 1.10, "label": "fortress"},
                {"min": 1.10, "mult": 1.05, "label": "strong"},
                {"min": 1.00, "mult": 1.00, "label": "in-band"},
                {"min": 0.0,  "mult": 0.85, "label": "weak"},
            ],
        },
        "kpis": [
            {
                "key":             'advisory_backlog_growth',
                "mandatory":       True,
                "search_phrases":  ['M&A deal pipeline growth', 'investment banking backlog', 'advisory mandates'],
                "clamp":           (-0.3, 1.0),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'compensation_ratio',
                "mandatory":       True,
                "search_phrases":  ['compensation and benefits as % of revenue', 'bonus pool ratio', 'staff cost ratio'],
                "compute_hint":    'total_comp_expense / total_net_revenue (decimal)',
                "clamp":           (0.30, 0.65),
                "source":          'H',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'liquidity_coverage_ratio',
                "mandatory":       True,
                "search_phrases":  ['LCR', 'Liquidity Coverage Ratio', 'HQLA over net cash outflows'],
                "compute_hint":    'High-quality liquid assets / 30-day net cash outflows (decimal — 1.30 = 130%)',
                "clamp":           (0.50, 3.00),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'assets_under_custody_usd',
                "mandatory":       False,
                "search_phrases":  ['AUC', 'total client assets'],
                "source":          'W',
                "extractor_only":  True,
            },
        ],
        "source_priority": ['Basel III LCR disclosures', 'Advisory / M&A backlog pipeline growth', 'Compensation-to-revenue ratio'],
    },

    'Market Infrastructure': {
        "sector":         'Financials',
        "anchor_methods": ['P/E (ops)', 'EV/EBITDA', 'DCF (FCF)'],
        # V3 quality: Recurring Data Revenue % (the LSEG/Nasdaq moat —
        # subscription data is utility-style stable revenue vs transaction
        # revenue which is cyclical).
        "quality_tiers": {
            "kpi_bands": [
                {"kpi": "recurring_data_rev_pct", "direction": "higher_better",
                 "bands": [
                     {"min": 0.50, "mult": 1.25, "label": "elite"},
                     {"min": 0.35, "mult": 1.10, "label": "strong"},
                     {"min": 0.25, "mult": 1.00, "label": "in-band"},
                     {"min": 0.0,  "mult": 0.90, "label": "transaction-heavy"},
                 ]},
            ],
            "cap": [0.70, 1.40],
        },
        # V3 risk: Net Debt / EBITDA — these firms are M&A machines (ICE,
        # LSEG) carrying tech-style debt on utility-style cash flows.
        "risk_adjustment": {
            "kpi": "net_debt_to_ebitda", "direction": "lower_better",
            "bands": [
                {"max": 1.5, "mult": 1.10, "label": "fortress"},
                {"max": 3.0, "mult": 1.00, "label": "in-band"},
                {"max": 4.5, "mult": 0.92, "label": "stretched"},
                {"max": 99,  "mult": 0.80, "label": "over-levered"},
            ],
        },
        "kpis": [
            {
                "key":             'recurring_data_rev_pct',
                "mandatory":       True,
                "search_phrases":  ['recurring data revenue', 'information services mix',
                                    'data and analytics revenue share', 'subscription data revenue'],
                "compute_hint":    'Recurring (subscription) data revenue / total revenue (decimal)',
                "clamp":           (0.0, 0.80),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'avg_daily_volume_adv',
                "mandatory":       True,
                "search_phrases":  ['average daily volume', 'ADV by product', 'total contracts traded per day'],
                "clamp":           (100000, 50000000),
                "source":          'W',
                "extractor_only":  True,
            },
            {
                "key":             'net_debt_to_ebitda',
                "mandatory":       True,
                "search_phrases":  ['net debt to EBITDA', 'leverage ratio'],
                "compute_hint":    '(Total debt - cash) / TTM EBITDA — FMP-augmented',
                "clamp":           (-2.0, 8.0),
                "source":          'F',
                "extractor_only":  False,
                "fmp_field":       'netDebtToEBITDATTM',
            },
            {
                "key":             'clearing_fee_per_contract',
                "mandatory":       False,
                "search_phrases":  ['RPC', 'rate per contract', 'capture rate'],
                "source":          'W',
                "extractor_only":  True,
            },
        ],
        "source_priority": ['SEC 10-K/Q (recurring vs transactional revenue split)', 'Exchange ADV reports', 'Supplemental segment data'],
    },

    'Money Center Bank (EU)': {
        "sector":         'Financials',
        "anchor_methods": ['Residual Income', 'P/TBV', 'P/E', 'Excess Capital'],
        # V3 quality: cost_of_risk + efficiency_ratio (correlated). EU banks
        # often have low credit risk but bloated cost bases — both must hold
        # for "elite". Efficiency bands are RELAXED vs US to reflect EU
        # structural cost levels (labour rules, branch density).
        "quality_tiers": {
            "kpi_bands": [
                {"kpi": "cost_of_risk_bps", "direction": "lower_better",
                 "correlation_group": "eu_bank_q",
                 "bands": [
                     {"max": 30,  "mult": 1.30, "label": "elite"},
                     {"max": 50,  "mult": 1.15, "label": "strong"},
                     {"max": 80,  "mult": 1.00, "label": "in-band"},
                     {"max": 999, "mult": 0.85, "label": "weak"},
                 ]},
                {"kpi": "efficiency_ratio", "direction": "lower_better",
                 "correlation_group": "eu_bank_q",
                 "bands": [
                     # v3.3 EU-Relaxed bands (vs US-Stringent in Money Center).
                     # EU banks structurally carry higher cost-to-income due to
                     # labour rules and branch density.
                     {"max": 0.55, "mult": 1.30, "label": "elite"},
                     {"max": 0.62, "mult": 1.15, "label": "strong"},
                     {"max": 0.72, "mult": 1.00, "label": "in-band"},
                     {"max": 99.0, "mult": 0.85, "label": "bloated"},
                 ]},
            ],
            "cap": [0.70, 1.50],
        },
        # V3 risk: CET1, Money Center bands (G-SIB scale). EU G-SIBs have
        # similar regulatory surcharges to US.
        "risk_adjustment": {
            "kpi": "cet1_ratio", "direction": "higher_better",
            "bands": [
                {"min": 0.145, "mult": 1.10, "label": "fortress"},
                {"min": 0.130, "mult": 1.05, "label": "strong"},
                {"min": 0.115, "mult": 1.00, "label": "in-band"},
                {"min": 0.0,   "mult": 0.85, "label": "weak"},
            ],
        },
        "kpis": [
            {
                "key":             'cet1_ratio',
                "mandatory":       True,
                "search_phrases":  ['Common Equity Tier 1 ratio', 'CET1 solvency', 'fully loaded CET1'],
                "clamp":           (0.1, 0.2),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'cost_of_risk_bps',
                "mandatory":       True,
                "search_phrases":  ['impairment charge basis points', 'cost of risk', 'CoR bps'],
                "compute_hint":    'Loan loss provisions / avg loans (basis points — EU bank typical 25-100 bps)',
                "clamp":           (0, 500),
                "source":          'W',
                "extractor_only":  True,
            },
            {
                "key":             'efficiency_ratio',
                "mandatory":       True,
                "search_phrases":  ['cost-to-income ratio', 'efficiency ratio', 'cost income ratio'],
                "compute_hint":    'Operating expenses / total revenue (decimal — 0.55 = 55%)',
                "clamp":           (0.30, 1.00),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'leverage_ratio_delegated',
                "mandatory":       False,
                "search_phrases":  ['EU leverage ratio', 'Tier 1 leverage'],
                "source":          'W',
                "extractor_only":  True,
            },
        ],
        "source_priority": ['Basel III / CET1 ratio filings', 'EU leverage ratio disclosures', 'Cost-of-risk (bps) logs'],
    },

    'Mortgage/GSE': {
        "sector":         'Financials',
        "anchor_methods": ['Residual Income', 'P/TBV', 'P/E (ops)', 'Excess Capital'],
        # V3 quality: G-Fee rate (the pricing-power moat — government-set
        # but Enterprise pricing power emerges in conservatorship exit).
        # delinquency_rate_90plus as a quality drag (correlated max-pick —
        # if delinquency spikes, the G-fee elite tier gets overridden).
        "quality_tiers": {
            "kpi_bands": [
                {"kpi": "g_fee_rate_bps", "direction": "higher_better",
                 "correlation_group": "gse_q",
                 "bands": [
                     {"min": 62, "mult": 1.25, "label": "elite"},
                     {"min": 58, "mult": 1.10, "label": "strong"},
                     {"min": 50, "mult": 1.00, "label": "in-band"},
                     {"min":  0, "mult": 0.85, "label": "weak"},
                 ]},
                {"kpi": "delinquency_rate_90plus", "direction": "lower_better",
                 "correlation_group": "gse_q",
                 "bands": [
                     {"max": 0.005, "mult": 1.10, "label": "low-default"},
                     {"max": 0.015, "mult": 1.00, "label": "in-band"},
                     {"max": 0.030, "mult": 0.92, "label": "stressed"},
                     {"max": 99,    "mult": 0.80, "label": "high-default"},
                 ]},
            ],
            "cap": [0.70, 1.40],
        },
        # V3 risk: CET1, GSE-specific bands. For FNMA/FMCC modelling
        # exit-from-conservatorship: use REGULATORY CET1 excluding the
        # liquidation preference (per user spec).
        "risk_adjustment": {
            "kpi": "cet1_ratio", "direction": "higher_better",
            "bands": [
                {"min": 0.150, "mult": 1.10, "label": "fortress"},
                {"min": 0.120, "mult": 1.05, "label": "strong"},
                {"min": 0.080, "mult": 1.00, "label": "in-band"},
                {"min": 0.0,   "mult": 0.70, "label": "weak"},
            ],
        },
        "kpis": [
            {
                "key":             'net_charge_off_pct',
                "mandatory":       True,
                "search_phrases":  ['NCO ratio', 'annualized charge-offs'],
                "clamp":           (0.0, 0.05),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'cet1_ratio',
                "mandatory":       True,
                "search_phrases":  ['CET1 ratio', 'common equity tier 1'],
                "clamp":           (0.08, 0.18),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'delinquency_rate_90plus',
                "mandatory":       True,
                "search_phrases":  ['90-day delinquency rate', 'mortgage non-accrual ratio', 'serious delinquency rate'],
                "clamp":           (0.0, 0.15),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'g_fee_rate_bps',
                "mandatory":       True,
                "search_phrases":  ['guarantee fee rate', 'G-fee yields', 'guaranty fee'],
                "compute_hint":    'Guarantee fee rate (basis points — GSE typical 50-65 bps)',
                "clamp":           (10, 200),
                "source":          'W',
                "extractor_only":  True,
            },
            {
                "key":             'servicing_portfolio_val_usd',
                "mandatory":       False,
                "search_phrases":  ['MSR fair value', 'Mortgage Servicing Rights'],
                "source":          'W',
                "extractor_only":  True,
            },
        ],
        "source_priority": ['SEC 10-K/Q', 'FHFA Monthly Summary Reports', 'Quarterly Credit Supplements'],
    },

    'Neo/Challenger': {
        "sector":         'Financials',
        "anchor_methods": ['Residual Income', 'P/TBV', 'P/E', 'Excess Capital'],
        # V3 quality: unit_econ_ratio = ARPU / cost-to-serve. Explicit KPI so
        # the LLM extracts the ratio directly (more robust than computing —
        # gives the LLM a chance to reason about which ARPU/CTS pair to use
        # if multiple are disclosed).
        "quality_tiers": {
            "kpi_bands": [
                {"kpi": "unit_econ_ratio", "direction": "higher_better",
                 "bands": [
                     {"min": 10.0, "mult": 1.30, "label": "elite"},
                     {"min":  6.0, "mult": 1.15, "label": "strong"},
                     {"min":  3.0, "mult": 1.00, "label": "in-band"},
                     {"min":  0.0, "mult": 0.85, "label": "weak"},
                 ]},
            ],
            "cap": [0.70, 1.50],
        },
        # V3 risk: equity_to_assets_pct (CET1 proxy — many neobanks lack
        # full banking licenses and don't disclose CET1). With cash-burn
        # cap: if net_income_pct < 0, risk multiplier capped at 1.00× (you
        # can't be a "fortress" with a hole in the bucket).
        "risk_adjustment": {
            "kpi": "equity_to_assets_pct", "direction": "higher_better",
            "bands": [
                {"min": 0.20, "mult": 1.15, "label": "fortress"},
                {"min": 0.12, "mult": 1.05, "label": "strong"},
                {"min": 0.08, "mult": 1.00, "label": "in-band"},
                {"min": 0.0,  "mult": 0.80, "label": "weak"},
            ],
            "cap_when": {
                "kpi":      "net_income_pct",
                "lt":       0.0,
                "max_mult": 1.00,
                "note":     "cash-burn cap (negative NI -> can't be fortress)",
            },
        },
        "kpis": [
            {
                "key":             'cost_to_serve_per_user',
                "mandatory":       True,
                "search_phrases":  ['operating cost per active user', 'service cost per head', 'opex per customer'],
                "compute_hint":    'total_operating_expenses / total_active_users (USD)',
                "clamp":           (1.0, 100.0),
                "source":          'W',
                "extractor_only":  True,
            },
            {
                "key":             'arpu_monthly_usd',
                "mandatory":       True,
                "search_phrases":  ['average revenue per active user', 'monthly ARPU'],
                "compute_hint":    'Monthly ARPU in USD (annualised /12)',
                "clamp":           (1.0, 500.0),
                "source":          'W',
                "extractor_only":  True,
            },
            {
                "key":             'unit_econ_ratio',
                "mandatory":       True,
                "search_phrases":  ['ARPU to cost-to-serve ratio', 'unit economics multiple',
                                    'revenue per user vs cost per user'],
                "compute_hint":    'arpu_monthly_usd / cost_to_serve_per_user — Elite (>10x): NuBank standard',
                "clamp":           (0.1, 50.0),
                "source":          'W',
                "extractor_only":  True,
            },
            {
                "key":             'equity_to_assets_pct',
                "mandatory":       True,
                "search_phrases":  ['shareholders equity / total assets', 'tangible common equity ratio',
                                    'capital ratio'],
                "compute_hint":    'CET1 proxy when neobank lacks full banking license (decimal)',
                "clamp":           (0.0, 0.50),
                "source":          'F',
                "extractor_only":  False,
                "decimal_format":  True,
            },
            {
                "key":             'net_income_pct',
                "mandatory":       True,
                "search_phrases":  ['net income margin', 'GAAP net margin', 'cash burn rate',
                                    'profitable / unprofitable status'],
                "compute_hint":    'Net income / total revenue (decimal — negative if burning cash)',
                "clamp":           (-1.00, 0.50),
                "source":          'F',
                "extractor_only":  False,
                "decimal_format":  True,
            },
            {
                "key":             'deposit_beta_pct',
                "mandatory":       False,
                "search_phrases":  ['rate pass-through to depositors', 'deposit beta'],
                "source":          'W',
                "extractor_only":  True,
            },
        ],
        "source_priority": ['Cost-to-serve per user', 'Monthly ARPU', 'Equity / total assets (capital cushion)', 'Net income margin (cash-burn signal)'],
    },

    'Payment Networks': {
        "sector":         'Financials',
        "anchor_methods": ['EV/NTM Revenue', 'P/E (ops)', 'DCF (FCF)'],
        # V3 quality: TPV growth + take_rate_stability — same pattern as
        # FinTech (V/MA/PYPL/SQ all share platform-vitality KPIs).
        "quality_tiers": {
            "kpi_bands": [
                {"kpi": "tpv_growth_yoy", "direction": "higher_better",
                 "correlation_group": "paynet_q",
                 "bands": [
                     {"min": 0.18, "mult": 1.30, "label": "elite"},
                     {"min": 0.12, "mult": 1.15, "label": "strong"},
                     {"min": 0.07, "mult": 1.00, "label": "in-band"},
                     {"min": 0.0,  "mult": 0.90, "label": "decel"},
                     {"min": -99,  "mult": 0.80, "label": "shrinking"},
                 ]},
                {"kpi": "take_rate_stability_bps", "direction": "lower_better",
                 "correlation_group": "paynet_q",
                 "bands": [
                     {"max":  3, "mult": 1.10, "label": "stable-pricing"},
                     {"max":  8, "mult": 1.00, "label": "in-band"},
                     {"max": 99, "mult": 0.90, "label": "compression"},
                 ]},
            ],
            "cap": [0.70, 1.50],
        },
        # V3 risk: rebates_and_incentives_pct_rev IS the incentive ratio for
        # Payment Networks (same metric as FinTech's incentive_ratio_pct, just
        # under the existing schema's name).
        "risk_adjustment": {
            "kpi": "rebates_and_incentives_pct_rev", "direction": "lower_better",
            "bands": [
                {"max": 0.22, "mult": 1.10, "label": "fortress"},
                {"max": 0.28, "mult": 1.00, "label": "in-band"},
                {"max": 0.32, "mult": 0.92, "label": "stretched"},
                {"max": 99,   "mult": 0.85, "label": "weak"},
            ],
        },
        "kpis": [
            {
                "key":             'take_rate_bps',
                "mandatory":       True,
                "search_phrases":  ['net take rate in bps', 'revenue as bps of volume'],
                "clamp":           (5, 300),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  False,
            },
            {
                "key":             'take_rate_stability_bps',
                "mandatory":       True,
                "search_phrases":  ['take rate change YoY in bps', 'pricing stability'],
                "compute_hint":    'Absolute change in take_rate_bps YoY (lower = stable pricing power)',
                "clamp":           (0, 50),
                "source":          'W',
                "extractor_only":  True,
            },
            {
                "key":             'tpv_growth_yoy',
                "mandatory":       True,
                "search_phrases":  ['Total Payment Volume growth', 'processed volume'],
                "clamp":           (-0.20, 1.50),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'cross_border_vol_growth_pct',
                "mandatory":       True,
                "search_phrases":  ['cross-border volume growth', 'international transaction growth'],
                "source":          'W',
                "extractor_only":  True,
            },
            {
                "key":             'rebates_and_incentives_pct_rev',
                "mandatory":       True,
                "search_phrases":  ['client incentives as % of gross revenue', 'rebates and incentives'],
                "compute_hint":    'Client incentives / gross revenue (decimal — V/MA fortress < 22%)',
                "clamp":           (0.0, 0.50),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'processed_transactions_yoy',
                "mandatory":       False,
                "search_phrases":  ['total processed transactions', 'transaction count growth'],
                "source":          'W',
                "extractor_only":  True,
            },
        ],
        "source_priority": ['SEC 10-K/Q', 'Quarterly Operating Statistics', 'Incentive ratio disclosures'],
    },

    'Regional Bank': {
        "sector":         'Financials',
        "anchor_methods": ['Residual Income', 'P/TBV', 'P/E', 'Excess Capital'],
        # V3 quality: NIM (developed bands) + LDR Goldilocks (correlated). The
        # LDR bands use higher_better with descending mins so the iteration
        # picks the right tier — >100% triggers the liquidity-risk penalty
        # FIRST (most-restrictive band wins), 80-100% the sweet-spot lift,
        # <80% the lazy-balance-sheet drag.
        "quality_tiers": {
            "kpi_bands": [
                {"kpi": "nim_pct", "direction": "higher_better",
                 "correlation_group": "regional_q",
                 "bands": [
                     {"min": 0.032, "mult": 1.30, "label": "elite"},
                     {"min": 0.026, "mult": 1.15, "label": "strong"},
                     {"min": 0.020, "mult": 1.00, "label": "in-band"},
                     {"min": 0.0,   "mult": 0.85, "label": "weak"},
                 ]},
                {"kpi": "loan_to_deposit_ratio", "direction": "higher_better",
                 "correlation_group": "regional_q",
                 "bands": [
                     {"min": 1.00, "mult": 0.85, "label": "liquidity-risk"},
                     {"min": 0.80, "mult": 1.15, "label": "sweet-spot"},
                     {"min": 0.0,  "mult": 0.95, "label": "lazy"},
                 ]},
            ],
            "cap": [0.70, 1.50],
        },
        # V3 risk: CET1, Regional bands (lower buffer than Money Centers
        # because they lack TBTF implicit backing — but market accepts thinner).
        "risk_adjustment": {
            "kpi": "cet1_ratio", "direction": "higher_better",
            "bands": [
                {"min": 0.130, "mult": 1.10, "label": "fortress"},
                {"min": 0.115, "mult": 1.05, "label": "strong"},
                {"min": 0.095, "mult": 1.00, "label": "in-band"},
                {"min": 0.0,   "mult": 0.85, "label": "weak"},
            ],
        },
        "kpis": [
            {
                "key":             'nim_pct',
                "mandatory":       True,
                "search_phrases":  ['Net Interest Margin', 'NIM', 'net interest spread'],
                "clamp":           (0.01, 0.08),
                "source":          'H',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'npl_ratio_pct',
                "mandatory":       True,
                "search_phrases":  ['Non-Performing Loans ratio', 'Gross NPL ratio', 'impaired loans %'],
                "clamp":           (0.0, 0.15),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'loan_to_deposit_ratio',
                "mandatory":       True,
                "search_phrases":  ['LDR', 'Loan-to-Deposit ratio', 'loans / deposits'],
                "compute_hint":    'Total loans / total deposits (decimal — Goldilocks: 80-100%)',
                "clamp":           (0.30, 1.50),
                "source":          'H',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'tier_1_capital_ratio',
                "mandatory":       True,
                "search_phrases":  ['Tier 1 capital'],
                "source":          'W',
                "extractor_only":  True,
            },
            # v3.3 — added explicit cet1_ratio (vs the broader tier_1) for
            # consistency with the V3 risk schema.
            {
                "key":             'cet1_ratio',
                "mandatory":       True,
                "search_phrases":  ['CET1 ratio', 'Common Equity Tier 1', 'CET-1'],
                "compute_hint":    'CET1 / risk-weighted assets (decimal — 0.12 = 12%)',
                "clamp":           (0.05, 0.25),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'yield_on_advances_pct',
                "mandatory":       False,
                "search_phrases":  ['loan yield', 'average interest on advances'],
                "source":          'H',
                "extractor_only":  True,
            },
        ],
        "source_priority": ['Statutory filings (NSE/BSE, SGX, 10-K)', 'NIM/NPL ratio disclosures', 'CASA mix reports'],
    },

    'Super-Regional Bank': {
        "sector":         'Financials',
        "anchor_methods": ['Residual Income', 'P/TBV', 'P/E (ops)', 'Excess Capital'],
        # V3 quality: efficiency_ratio (single, US-Stringent bands).
        "quality_tiers": {
            "kpi_bands": [
                {"kpi": "efficiency_ratio", "direction": "lower_better",
                 "bands": [
                     {"max": 0.50, "mult": 1.30, "label": "elite"},
                     {"max": 0.58, "mult": 1.15, "label": "strong"},
                     {"max": 0.68, "mult": 1.00, "label": "in-band"},
                     {"max": 99.0, "mult": 0.85, "label": "bloated"},
                 ]},
            ],
            "cap": [0.70, 1.50],
        },
        # V3 risk: CET1 with Money Center bands. Super-Regionals (USB, PNC,
        # TFC) sit at scale just below G-SIBs; market expects similar buffer.
        "risk_adjustment": {
            "kpi": "cet1_ratio", "direction": "higher_better",
            "bands": [
                {"min": 0.145, "mult": 1.10, "label": "fortress"},
                {"min": 0.130, "mult": 1.05, "label": "strong"},
                {"min": 0.115, "mult": 1.00, "label": "in-band"},
                {"min": 0.0,   "mult": 0.85, "label": "weak"},
            ],
        },
        "kpis": [
            {
                "key":             'net_charge_off_pct',
                "mandatory":       True,
                "search_phrases":  ['NCO ratio', 'annualized charge-offs'],
                "clamp":           (0.0, 0.05),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'cet1_ratio',
                "mandatory":       True,
                "search_phrases":  ['CET1 ratio', 'common equity tier 1'],
                "clamp":           (0.08, 0.18),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'efficiency_ratio',
                "mandatory":       True,
                "search_phrases":  ['cost-to-income', 'non-interest expense % of revenue', 'efficiency ratio'],
                "compute_hint":    'Operating expenses / total revenue (decimal)',
                "clamp":           (0.30, 1.00),
                "source":          'H',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'net_charge_offs_pct',
                "mandatory":       True,
                "search_phrases":  ['NCO ratio', 'annualized loan losses'],
                "source":          'W',
                "extractor_only":  True,
            },
            {
                "key":             'tangible_book_value_per_share',
                "mandatory":       False,
                "search_phrases":  ['TBVPS', 'NAV per share ex-intangibles'],
                "source":          'F',
                "extractor_only":  False,
            },
        ],
        "source_priority": ['SEC 10-K/Q', 'FFIEC Call Reports', 'Supplemental Earnings Presentations'],
    },

# ── Industrials ──────────────────────────────────────────────────
    'Aerospace & Defense': {
        "sector":         'Industrials',
        # Multi-method anchor list — Aerospace/Defense often has lumpy EBITDA
        # so blend with P/E and DCF for robustness (audit Apr 2026: LMT failed
        # with only EV/EBITDA when shares_out was None).
        "anchor_methods": ['EV/EBITDA', 'P/E (ops)', 'DCF (FCF)'],
        # V3.1: book_to_bill_ratio = backlog visibility quality (LMT $150B+ backlog deserves premium)
        "quality_tiers": {
            "kpi_bands": [{
                "kpi": "book_to_bill_ratio", "direction": "higher_better",
                "bands": [{"min": 1.20, "mult": 1.20, "label": "best-in-class"},
                          {"min": 1.05, "mult": 1.10, "label": "growing backlog"},
                          {"min": 0.95, "mult": 1.00, "label": "in-band"},
                          {"min": 0.0,  "mult": 0.90, "label": "shrinking"}],
            }],
            "cap": [0.80, 1.30],
        },
        "risk_adjustment": {
            "kpi": "net_debt_to_ebitda", "direction": "lower_better",
            "bands": [{"max": 1.5,  "mult": 1.10, "label": "fortress"},
                      {"max": 3.0,  "mult": 1.00, "label": "in-band"},
                      {"max": 99.0, "mult": 0.85, "label": "leveraged"}],
        },
        "kpis": [
            {
                "key":             'total_backlog_usd',
                "mandatory":       True,
                "search_phrases":  ['total funded and unfunded backlog', 'multi-year order book', 'RPO balance'],
                "clamp":           (1000000000, 500000000000),
                "source":          'W',
                "extractor_only":  True,
            },
            {
                "key":             'book_to_bill_ratio',
                "mandatory":       True,
                "search_phrases":  ['orders divided by revenue', 'book-to-bill', 'net new orders / shipments'],
                "clamp":           (0.7, 1.5),
                "source":          'W',
                "extractor_only":  True,
            },
            {
                "key":             'aftermarket_revenue_pct',
                "mandatory":       False,
                "search_phrases":  ['spare parts and service revenue mix'],
                "source":          'H',
                "extractor_only":  True,
            },
        ],
        "source_priority": ['Federal defense budget appropriations (DOD)', 'Book-to-bill ratios'],
    },

    'Automotive (OEM)': {
        "sector":         'Industrials',
        "anchor_methods": ['EV/EBITDA', 'P/E (ops)', 'P/BV', 'FCF Yield'],
        # V3 quality: unit_deliveries_yoy primary + ev_delivery_mix_pct (BEV)
        # kicker (Electrification Alpha — 2026 BEV >25% Elite, recalibrated
        # per E1 spec since global BEV share now ~19%).
        "quality_tiers": {
            "kpi_bands": [
                {"kpi": "unit_deliveries_yoy", "direction": "higher_better",
                 "correlation_group": "auto_q_primary",
                 "bands": [
                     {"min":  0.10, "mult": 1.30, "label": "elite"},
                     {"min":  0.05, "mult": 1.15, "label": "strong"},
                     {"min":  0.0,  "mult": 1.00, "label": "in-band"},
                     {"min": -99,   "mult": 0.85, "label": "decel"},
                 ]},
                {"kpi": "ev_delivery_mix_pct", "direction": "higher_better",
                 "correlation_group": "auto_q_ev_kicker",
                 "bands": [
                     {"min": 0.25, "mult": 1.30, "label": "elite-BEV-leader"},
                     {"min": 0.15, "mult": 1.15, "label": "strong-electrification"},
                     {"min": 0.05, "mult": 1.00, "label": "in-band"},
                     {"min": 0.0,  "mult": 0.80, "label": "legacy-ICE-only"},
                 ]},
            ],
            "cap": [0.70, 1.50],
        },
        # V3 risk: inventory_days_sales primary (death-spiral signal, F 2024
        # lesson — Toyota gold standard ~33d) + drag_when on net_debt_to_ebitda
        # >4.5x (the "Debt Trap" — high leverage restricts R&D pivot to Gen-3
        # EV platforms).
        "risk_adjustment": {
            "kpi": "inventory_days_sales", "direction": "lower_better",
            "bands": [
                {"max":  60, "mult": 1.10, "label": "fortress-Toyota"},
                {"max":  90, "mult": 1.00, "label": "in-band"},
                {"max": 120, "mult": 0.92, "label": "stretched"},
                {"max": 999, "mult": 0.80, "label": "weak-Stellantis-VW-bloat"},
            ],
            "drag_when": {
                "kpi":    "net_debt_to_ebitda",
                "gt":     4.5,
                "factor": 0.90,
                "note":   "Debt Trap: high leverage restricts R&D pivot to Gen-3 EV platforms",
            },
        },
        "kpis": [
            {
                "key":             'inventory_days_sales',
                "mandatory":       True,
                "search_phrases":  ['days of inventory on hand', 'dealer stock levels', 'DOH', 'days of supply'],
                "compute_hint":    'Days of inventory on dealer lots (Toyota ~33d gold standard, >120d bloat)',
                "clamp":           (15, 200),
                "source":          'F',
                "extractor_only":  False,
                "fmp_field":       'daysOfInventoryOnHandTTM',
            },
            {
                "key":             'unit_deliveries_yoy',
                "mandatory":       True,
                "search_phrases":  ['wholesale vehicle deliveries', 'retail sales volume growth', 'unit sales YOY'],
                "compute_hint":    'YoY unit delivery growth (decimal)',
                "clamp":           (-0.30, 0.50),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'ev_delivery_mix_pct',
                "mandatory":       True,
                "search_phrases":  ['BEV mix', 'battery EV penetration', 'electrification share of units', 'BEV % of deliveries'],
                "compute_hint":    'BEV deliveries / total deliveries (decimal — 2026 BEV share ~19%; >25% Elite)',
                "clamp":           (0.0, 1.0),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'net_debt_to_ebitda',
                "mandatory":       True,
                "search_phrases":  ['net debt to EBITDA', 'leverage ratio', 'industrial net debt'],
                "compute_hint":    'Excluding Finance Arm debt — FMP-augmented',
                "clamp":           (-3.0, 12.0),
                "source":          'F',
                "extractor_only":  False,
                "fmp_field":       'netDebtToEBITDATTM',
            },
        ],
        "source_priority": ['Dealer inventory days of supply', 'Wholesale unit delivery logs', 'BEV delivery mix', 'Net leverage ex-finance arm'],
    },

    'Capital Goods': {
        "sector":         'Industrials',
        "anchor_methods": ['EV/EBITDA', 'FCF Yield', 'ROIC vs WACC', 'P/E (ops)'],
        # V3 quality: organic_revenue_growth + book_to_bill_ratio (joint
        # qualification per E2 spec — separate groups, multiply, full magnitudes).
        "quality_tiers": {
            "kpi_bands": [
                {"kpi": "organic_revenue_growth", "direction": "higher_better",
                 "correlation_group": "capgoods_q_primary",
                 "bands": [
                     {"min":  0.08, "mult": 1.30, "label": "elite"},
                     {"min":  0.05, "mult": 1.15, "label": "strong"},
                     {"min":  0.01, "mult": 1.00, "label": "in-band"},
                     {"min": -99,   "mult": 0.85, "label": "decline"},
                 ]},
                {"kpi": "book_to_bill_ratio", "direction": "higher_better",
                 "correlation_group": "capgoods_q_b2b_kicker",
                 "bands": [
                     {"min": 1.15, "mult": 1.30, "label": "elite-pipeline"},
                     {"min": 1.05, "mult": 1.15, "label": "strong-pipeline"},
                     {"min": 0.95, "mult": 1.00, "label": "in-band"},
                     {"min": 0.0,  "mult": 0.85, "label": "weak-pipeline"},
                 ]},
            ],
            "cap": [0.70, 1.50],
        },
        "risk_adjustment": {
            "kpi": "net_debt_to_ebitda", "direction": "lower_better",
            "bands": [
                {"max": 1.5,  "mult": 1.10, "label": "fortress"},
                {"max": 3.0,  "mult": 1.00, "label": "in-band"},
                {"max": 4.5,  "mult": 0.92, "label": "stretched"},
                {"max": 99,   "mult": 0.80, "label": "weak"},
            ],
        },
        "kpis": [
            {
                "key":             'organic_revenue_growth',
                "mandatory":       True,
                "search_phrases":  ['organic revenue growth', 'like-for-like sales growth', 'revenue growth ex-FX/M&A'],
                "compute_hint":    'YoY revenue growth ex-FX ex-M&A (decimal — global >8% elite, India L&T >16%)',
                "clamp":           (-0.30, 0.50),
                "source":          'H',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'book_to_bill_ratio',
                "mandatory":       True,
                "search_phrases":  ['order-to-delivery ratio', 'book-to-bill', 'orders to revenue'],
                "compute_hint":    'New orders / shipped revenue',
                "clamp":           (0.5, 2.0),
                "source":          'W',
                "extractor_only":  True,
            },
            {
                "key":             'net_debt_to_ebitda',
                "mandatory":       True,
                "search_phrases":  ['net debt to EBITDA', 'leverage ratio'],
                "clamp":           (-3.0, 8.0),
                "source":          'F',
                "extractor_only":  False,
                "fmp_field":       'netDebtToEBITDATTM',
            },
            {
                "key":             'service_revenue_mix_pct',
                "mandatory":       False,
                "search_phrases":  ['service and maintenance revenue share'],
                "source":          'H',
                "extractor_only":  True,
            },
        ],
        "source_priority": ['Organic revenue growth ex-FX/M&A', 'Book-to-bill ratios', 'Leverage disclosures'],
    },

# ── Materials ──────────────────────────────────────────────────
    'Specialty Chemicals': {
        "sector":         'Materials',
        "anchor_methods": ['EV/EBITDA', 'P/E (ops)', 'FCF Yield', 'ROIC vs WACC'],
        # V3 quality: volume_growth_yoy primary (cyclical demand signal) +
        # pricing_power_pct kicker (price realization).
        "quality_tiers": {
            "kpi_bands": [
                {"kpi": "volume_growth_yoy", "direction": "higher_better",
                 "correlation_group": "specchem_q_primary",
                 "bands": [
                     {"min":  0.05, "mult": 1.20, "label": "strong-cycle"},
                     {"min":  0.0,  "mult": 1.00, "label": "in-band"},
                     {"min": -99,   "mult": 0.85, "label": "destocking-weak"},
                 ]},
                {"kpi": "pricing_power_pct", "direction": "higher_better",
                 "correlation_group": "specchem_q_kicker",
                 "bands": [
                     {"min":  0.04, "mult": 1.10, "label": "premium-pricing"},
                     {"min":  0.0,  "mult": 1.00, "label": "in-band"},
                     {"min": -99,   "mult": 0.90, "label": "capitulation"},
                 ]},
            ],
            "cap": [0.70, 1.35],
        },
        "risk_adjustment": {
            "kpi": "net_debt_to_ebitda", "direction": "lower_better",
            "bands": [
                {"max": 2.0,  "mult": 1.10, "label": "fortress"},
                {"max": 3.5,  "mult": 1.00, "label": "in-band"},
                {"max": 5.0,  "mult": 0.92, "label": "stretched"},
                {"max": 99,   "mult": 0.80, "label": "weak"},
            ],
        },
        "kpis": [
            {"key": 'volume_growth_yoy',     "mandatory": True, "search_phrases": ['volume growth YoY','organic volume growth','shipment volume change'], "compute_hint": 'YoY volume growth (decimal)', "clamp": (-0.30, 0.30), "source": 'W', "extractor_only": True, "decimal_format": True},
            {"key": 'pricing_power_pct',     "mandatory": True, "search_phrases": ['pricing realization','price/mix contribution','price growth YoY'], "compute_hint": 'YoY pricing contribution to revenue (decimal)', "clamp": (-0.15, 0.30), "source": 'W', "extractor_only": True, "decimal_format": True},
            {"key": 'specialty_mix_pct',     "mandatory": True, "search_phrases": ['revenue mix from specialty vs commodity','high-value product contribution'], "compute_hint": 'specialty/total revenue (decimal)', "clamp": (0.0, 1.0), "source": 'H', "extractor_only": True, "decimal_format": True},
            {"key": 'rd_intensity_pct',      "mandatory": True, "search_phrases": ['R&D % of sales','innovation spend'], "clamp": (0.0, 0.15), "source": 'F', "extractor_only": False, "decimal_format": True},
            {"key": 'net_debt_to_ebitda',    "mandatory": True, "search_phrases": ['net debt to EBITDA','leverage ratio'], "clamp": (-3.0, 8.0), "source": 'F', "extractor_only": False, "fmp_field": 'netDebtToEBITDATTM'},
            {"key": 'raw_material_pass_through_pct', "mandatory": False, "search_phrases": ['pricing surcharge effectiveness','input cost recovery rate'], "source": 'W', "extractor_only": True},
        ],
        "source_priority": ['Volume / pricing contribution', 'Specialty mix', 'Leverage'],
    },

    'Steel / Metals': {
        "sector":         'Materials',
        "anchor_methods": ['EV/EBITDA', 'P/BV', 'FCF Yield', 'P/E (ops)'],
        # V3 quality: price_yoy_growth primary (relative measure — works
        # across steel/aluminum/iron-ore without sub-type bands) +
        # capacity_utilization_pct kicker.
        "quality_tiers": {
            "kpi_bands": [
                {"kpi": "price_yoy_growth", "direction": "higher_better",
                 "correlation_group": "steel_q_primary",
                 "bands": [
                     {"min":  0.10, "mult": 1.30, "label": "strong-pricing-cycle"},
                     {"min":  0.0,  "mult": 1.00, "label": "in-band"},
                     {"min": -0.10, "mult": 0.90, "label": "soft-cycle"},
                     {"min": -99,   "mult": 0.80, "label": "deep-trough"},
                 ]},
                {"kpi": "capacity_utilization_pct", "direction": "higher_better",
                 "correlation_group": "steel_q_kicker",
                 "bands": [
                     {"min": 0.85, "mult": 1.10, "label": "strong-cycle"},
                     {"min": 0.75, "mult": 1.00, "label": "in-band"},
                     {"min": 0.0,  "mult": 0.90, "label": "weak-cycle"},
                 ]},
            ],
            "cap": [0.70, 1.40],
        },
        # V3 risk: net_debt_to_ebitda — steel cycles can wipe out 2-3x leverage in trough.
        "risk_adjustment": {
            "kpi": "net_debt_to_ebitda", "direction": "lower_better",
            "bands": [
                {"max": 1.5,  "mult": 1.10, "label": "fortress-trough-survivor"},
                {"max": 2.5,  "mult": 1.00, "label": "in-band"},
                {"max": 3.5,  "mult": 0.92, "label": "stretched"},
                {"max": 99,   "mult": 0.75, "label": "weak-trough-bankruptcy-risk"},
            ],
        },
        "kpis": [
            {"key": 'price_yoy_growth',         "mandatory": True, "search_phrases": ['realized price YoY','price per ton growth','spot price change YoY','metal price growth'], "compute_hint": 'YoY change in realized price per unit (decimal — works across steel/Al/iron-ore)', "clamp": (-0.50, 1.0), "source": 'W', "extractor_only": True, "decimal_format": True},
            {"key": 'capacity_utilization_pct', "mandatory": True, "search_phrases": ['steel mill utilization rate','capacity utilization','plant operating rate'], "compute_hint": 'actual_production/nameplate_capacity (decimal)', "clamp": (0.40, 1.05), "source": 'W', "extractor_only": True, "decimal_format": True},
            {"key": 'cost_per_tonne_usd',       "mandatory": True, "search_phrases": ['cash cost of production per tonne','AISC steel','cost per ton'], "compute_hint": '(COGS+sustaining_capex)/total_tonnes', "clamp": (200, 2500), "source": 'W', "extractor_only": True},
            {"key": 'net_debt_to_ebitda',       "mandatory": True, "search_phrases": ['net debt to EBITDA','leverage ratio'], "clamp": (-3.0, 12.0), "source": 'F', "extractor_only": False, "fmp_field": 'netDebtToEBITDATTM'},
            {"key": 'green_steel_mix_pct',      "mandatory": False, "search_phrases": ['low-carbon steel production','EAF vs Blast Furnace mix'], "source": 'W', "extractor_only": True},
        ],
        "source_priority": ['Realized price disclosures', 'Mill utilization rates', 'AISC per tonne', 'Leverage'],
    },

# ── ProfessionalServices ──────────────────────────────────────────────────
    'Ad / Consulting': {
        "sector":         'ProfessionalServices',
        "anchor_methods": ['EV/EBIT', 'FCF Yield', 'P/E (ops)', 'Revenue DCF'],
        "quality_tiers": {
            "kpi_bands": [
                {"kpi": "organic_revenue_growth", "direction": "higher_better",
                 "correlation_group": "adcons_q_primary",
                 "bands": [
                     {"min":  0.08, "mult": 1.30, "label": "elite"},
                     {"min":  0.04, "mult": 1.15, "label": "strong"},
                     {"min":  0.0,  "mult": 1.00, "label": "in-band"},
                     {"min": -99,   "mult": 0.85, "label": "ad-recession"},
                 ]},
                {"kpi": "personnel_cost_to_revenue", "direction": "lower_better",
                 "correlation_group": "adcons_q_kicker",
                 "bands": [
                     {"max": 0.55, "mult": 1.10, "label": "efficient"},
                     {"max": 0.65, "mult": 1.00, "label": "in-band"},
                     {"max": 99,   "mult": 0.90, "label": "bloated"},
                 ]},
            ],
            "cap": [0.70, 1.40],
        },
        "risk_adjustment": {
            "kpi": "net_debt_to_ebitda", "direction": "lower_better",
            "bands": [
                {"max": 1.5,  "mult": 1.10, "label": "fortress"},
                {"max": 3.0,  "mult": 1.00, "label": "in-band"},
                {"max": 4.5,  "mult": 0.92, "label": "stretched"},
                {"max": 99,   "mult": 0.85, "label": "weak"},
            ],
        },
        "kpis": [
            {"key": 'organic_revenue_growth',  "mandatory": True,  "search_phrases": ['organic revenue growth ex-FX','like-for-like sales growth'], "compute_hint": '(revenue_ex_mna_fx/prior_revenue)-1', "clamp": (-0.30, 0.50), "source": 'H', "extractor_only": True, "decimal_format": True},
            {"key": 'personnel_cost_to_revenue', "mandatory": True, "search_phrases": ['staff costs as percentage of net revenue','personnel expense ratio','compensation/revenue'], "compute_hint": 'total_employee_compensation/net_revenue (decimal)', "clamp": (0.30, 0.95), "source": 'H', "extractor_only": True, "decimal_format": True},
            {"key": 'net_debt_to_ebitda',      "mandatory": True,  "search_phrases": ['net debt to EBITDA','leverage ratio'], "clamp": (-3.0, 8.0), "source": 'F', "extractor_only": False, "fmp_field": 'netDebtToEBITDATTM'},
            {"key": 'net_new_billings',        "mandatory": False, "search_phrases": ['new business wins','net account movement'], "source": 'W', "extractor_only": True},
        ],
        "source_priority": ['Personnel cost-to-revenue ratio', 'Organic revenue growth ex-FX', 'Leverage'],
    },

    'IT Services': {
        "sector":         'ProfessionalServices',
        "anchor_methods": ['P/E (ops)', 'EV/EBITDA', 'DCF (FCF)'],
        "quality_tiers": {
            "kpi_bands": [
                {"kpi": "utilization_rate_pct", "direction": "higher_better",
                 "correlation_group": "itsv_q_primary",
                 "bands": [
                     {"min": 0.83, "mult": 1.30, "label": "elite"},
                     {"min": 0.78, "mult": 1.15, "label": "strong"},
                     {"min": 0.73, "mult": 1.00, "label": "in-band"},
                     {"min": 0.0,  "mult": 0.85, "label": "weak-idle-bench"},
                 ]},
                {"kpi": "attrition_rate_pct", "direction": "lower_better",
                 "correlation_group": "itsv_q_kicker",
                 "bands": [
                     {"max": 0.13, "mult": 1.10, "label": "retention-strong"},
                     {"max": 0.18, "mult": 1.00, "label": "in-band"},
                     {"max": 0.22, "mult": 0.95, "label": "concerning"},
                     {"max": 99,   "mult": 0.85, "label": "bleeding"},
                 ]},
            ],
            "cap": [0.70, 1.45],
        },
        "risk_adjustment": {
            "kpi": "net_debt_to_ebitda", "direction": "lower_better",
            "bands": [
                {"max": 0.0,  "mult": 1.10, "label": "fortress-net-cash-TCS"},
                {"max": 1.0,  "mult": 1.05, "label": "strong"},
                {"max": 2.0,  "mult": 1.00, "label": "in-band"},
                {"max": 99,   "mult": 0.85, "label": "weak"},
            ],
        },
        "kpis": [
            {"key": 'attrition_rate_pct',  "mandatory": True, "search_phrases": ['voluntary attrition','LTM attrition','employee attrition rate'], "compute_hint": 'TTM voluntary attrition (decimal)', "clamp": (0.0, 0.50), "source": 'W', "extractor_only": True, "decimal_format": True},
            {"key": 'utilization_rate_pct',"mandatory": True, "search_phrases": ['billable utilization','bench utilization','consultant utilization'], "compute_hint": 'Billable hours/total hours (decimal)', "clamp": (0.50, 1.0), "source": 'W', "extractor_only": True, "decimal_format": True},
            {"key": 'net_debt_to_ebitda',  "mandatory": True, "search_phrases": ['net debt to EBITDA','leverage ratio'], "clamp": (-3.0, 8.0), "source": 'F', "extractor_only": False, "fmp_field": 'netDebtToEBITDATTM'},
            {"key": 'offshore_delivery_mix_pct', "mandatory": False, "search_phrases": ['offshore mix'], "source": 'W', "extractor_only": True},
            {"key": 'digital_revenue_pct', "mandatory": False, "search_phrases": ['digital services mix','cloud and data revenue'], "source": 'W', "extractor_only": True},
        ],
        "source_priority": ['Earnings Presentations', 'Statutory Filings', 'Leverage'],
    },

    'Payment Processors': {
        "sector":         'ProfessionalServices',
        "anchor_methods": ['EV/Gross Profit', 'EV/Volume', 'DCF (FCF)', 'Rule of 40'],
        "quality_tiers": {
            "kpi_bands": [
                {"kpi": "tpv_growth_yoy", "direction": "higher_better",
                 "correlation_group": "ppro_q_primary",
                 "bands": [
                     {"min":  0.15, "mult": 1.30, "label": "elite"},
                     {"min":  0.08, "mult": 1.15, "label": "strong"},
                     {"min":  0.03, "mult": 1.00, "label": "in-band"},
                     {"min": -99,   "mult": 0.85, "label": "decel"},
                 ]},
                {"kpi": "blended_take_rate_bps", "direction": "higher_better",
                 "correlation_group": "ppro_q_kicker",
                 "bands": [
                     {"min": 100, "mult": 1.15, "label": "premium-Adyen-PYPL"},
                     {"min":  40, "mult": 1.00, "label": "in-band-merchant-acquirer"},
                     {"min":   0, "mult": 0.85, "label": "commodity-FIS-FISV-legacy"},
                 ]},
            ],
            "cap": [0.70, 1.40],
        },
        "risk_adjustment": {
            "kpi": "net_debt_to_ebitda", "direction": "lower_better",
            "bands": [
                {"max": 2.0,  "mult": 1.10, "label": "fortress"},
                {"max": 3.5,  "mult": 1.00, "label": "in-band"},
                {"max": 5.0,  "mult": 0.92, "label": "stretched"},
                {"max": 99,   "mult": 0.85, "label": "weak"},
            ],
        },
        "kpis": [
            {"key": 'tpv_growth_yoy',       "mandatory": True, "search_phrases": ['Total Processing Volume growth','processed volume YOY'], "compute_hint": '(current_tpv/prior_tpv)-1', "clamp": (-0.20, 1.5), "source": 'W', "extractor_only": True, "decimal_format": True},
            {"key": 'blended_take_rate_bps',"mandatory": True, "search_phrases": ['net take rate in basis points','blended fee margin'], "compute_hint": '(total_revenue/TPV)*10000 — bps', "clamp": (5, 500), "source": 'W', "extractor_only": True},
            {"key": 'net_debt_to_ebitda',   "mandatory": True, "search_phrases": ['net debt to EBITDA','leverage ratio'], "clamp": (-3.0, 8.0), "source": 'F', "extractor_only": False, "fmp_field": 'netDebtToEBITDATTM'},
            {"key": 'e_commerce_volume_mix',"mandatory": False, "search_phrases": ['online vs card-present volume'], "source": 'W', "extractor_only": True},
        ],
        "source_priority": ['TPV growth', 'Take rate (bps)', 'Leverage'],
    },

# ── Semiconductor ──────────────────────────────────────────────────
    'Equipment / EDA': {
        "sector":         'Semiconductor',
        "anchor_methods": ['P/E (ops)', 'DCF (FCF)', 'EV/EBITDA'],
        # V3 quality: book_to_bill_ratio primary + service_revenue_pct kicker
        # (the "razor-blade" recurring moat for ASML/AMAT/LRCX).
        "quality_tiers": {
            "kpi_bands": [
                {"kpi": "book_to_bill_ratio", "direction": "higher_better",
                 "correlation_group": "semi_eq_q_primary",
                 "bands": [
                     {"min": 1.20, "mult": 1.30, "label": "hyperscaler-cycle-elite"},
                     {"min": 1.0,  "mult": 1.15, "label": "strong"},
                     {"min": 0.85, "mult": 1.00, "label": "in-band"},
                     {"min": 0.0,  "mult": 0.85, "label": "cycle-trough"},
                 ]},
                {"kpi": "service_revenue_pct", "direction": "higher_better",
                 "correlation_group": "semi_eq_q_service_kicker",
                 "bands": [
                     {"min": 0.35, "mult": 1.10, "label": "premium-installed-base"},
                     {"min": 0.25, "mult": 1.05, "label": "in-band"},
                     {"min": 0.0,  "mult": 1.00, "label": "transactional"},
                 ]},
            ],
            "cap": [0.70, 1.45],
        },
        # V3 risk: R&D intensity (higher_better — staying alive in semi cycle).
        "risk_adjustment": {
            "kpi": "rd_intensity_pct", "direction": "higher_better",
            "bands": [
                {"min": 0.18, "mult": 1.10, "label": "fortress-reinvest"},
                {"min": 0.12, "mult": 1.05, "label": "strong"},
                {"min": 0.08, "mult": 1.00, "label": "in-band"},
                {"min": 0.07, "mult": 0.95, "label": "stretched"},
                {"min": 0.0,  "mult": 0.80, "label": "weak-stagnation"},
            ],
        },
        "kpis": [
            {
                "key":             'book_to_bill_ratio',
                "mandatory":       True,
                "search_phrases":  ['book-to-bill ratio', 'net orders over shipments', 'order-to-bill'],
                "compute_hint":    'total_new_orders / total_shipments',
                "clamp":           (0.5, 2.0),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  False,
            },
            {
                "key":             'service_revenue_pct',
                "mandatory":       True,
                "search_phrases":  ['installed base services revenue', 'recurring service and parts mix', 'service revenue %'],
                "compute_hint":    'total_service_revenue / total_revenue (decimal — premium >35%)',
                "clamp":           (0.0, 0.70),
                "source":          'H',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'rd_intensity_pct',
                "mandatory":       True,
                "search_phrases":  ['R&D as % of sales', 'research and development intensity'],
                "compute_hint":    'total_RD_expense / total_revenue (decimal — fortress >18%)',
                "clamp":           (0.0, 0.40),
                "source":          'F',
                "extractor_only":  False,
                "decimal_format":  True,
            },
            {
                "key":             'backlog_coverage_ratio',
                "mandatory":       False,
                "search_phrases":  ['backlog divided by quarterly revenue', 'months of backlog visibility'],
                "compute_hint":    'total_backlog / avg_quarterly_revenue',
                "clamp":           (2.0, 18.0),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  False,
            },
        ],
        "source_priority": ['Book-to-bill press releases', 'Service revenue mix disclosures', 'R&D intensity (10-K)'],
    },

    'OSAT / Packaging': {
        "sector":         'Semiconductor',
        "anchor_methods": ['EV/EBITDA', 'P/E (ops)', 'P/BV', 'FCF Yield'],
        # V3 quality: advanced_packaging_revenue_pct primary (the AI-leverage
        # differentiator vs commodity packaging) + wafer_test_utilization_pct
        # kicker (cycle leverage).
        "quality_tiers": {
            "kpi_bands": [
                {"kpi": "advanced_packaging_revenue_pct", "direction": "higher_better",
                 "correlation_group": "osat_q_primary",
                 "bands": [
                     {"min": 0.50, "mult": 1.30, "label": "AI-leverage-elite"},
                     {"min": 0.30, "mult": 1.15, "label": "strong"},
                     {"min": 0.15, "mult": 1.00, "label": "in-band"},
                     {"min": 0.0,  "mult": 0.85, "label": "commodity"},
                 ]},
                {"kpi": "wafer_test_utilization_pct", "direction": "higher_better",
                 "correlation_group": "osat_q_util_kicker",
                 "bands": [
                     {"min": 0.80, "mult": 1.10, "label": "elite-util"},
                     {"min": 0.65, "mult": 1.00, "label": "in-band"},
                     {"min": 0.0,  "mult": 0.90, "label": "weak-util"},
                 ]},
            ],
            "cap": [0.70, 1.45],
        },
        # V3 risk: capital_intensity_pct (lower_better — OSAT is capex-heavy,
        # lower = better cash conversion).
        "risk_adjustment": {
            "kpi": "capital_intensity_pct", "direction": "lower_better",
            "bands": [
                {"max": 0.15, "mult": 1.10, "label": "strong-cash-conversion"},
                {"max": 0.25, "mult": 1.00, "label": "in-band"},
                {"max": 0.99, "mult": 0.85, "label": "over-built"},
            ],
        },
        "kpis": [
            {
                "key":             'advanced_packaging_revenue_pct',
                "mandatory":       True,
                "search_phrases":  ['2.5D/3D packaging revenue share', 'CoWoS and advanced packaging mix', 'high-end packaging contribution', 'AI packaging revenue %'],
                "compute_hint":    'advanced_packaging_revenue / total_revenue (decimal — >50% AI-leverage elite)',
                "clamp":           (0.0, 0.95),
                "source":          'H',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'wafer_test_utilization_pct',
                "mandatory":       True,
                "search_phrases":  ['test and assembly utilization rate', 'backend utilization', 'factory operating level'],
                "compute_hint":    'actual_wafer_starts / total_wafer_capacity (decimal)',
                "clamp":           (0.30, 1.0),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'capital_intensity_pct',
                "mandatory":       True,
                "search_phrases":  ['capex as % of revenue', 'capital intensity', 'capex/sales ratio'],
                "compute_hint":    'capex / TTM revenue (decimal — FMP-augmented; OSAT typically 15-30%)',
                "clamp":           (0.0, 0.50),
                "source":          'F',
                "extractor_only":  False,
                "decimal_format":  True,
            },
        ],
        "source_priority": ['Advanced packaging revenue share (CoWoS / 2.5D / 3D)', 'Backend utilization disclosures', 'Capex-to-sales intensity'],
    },

# ── Tech ──────────────────────────────────────────────────
    'Early Platform': {
        "sector":         'Tech',
        "anchor_methods": ['GMV-TAM Penetration', 'DCF', 'EV/NTM Revenue'],
        # V3 quality: gmv_growth_yoy = primary anchor (the velocity metric);
        # unit_economics_ratio (LTV/CAC) = independent kicker. Separate
        # correlation groups so they MULTIPLY (primary + kicker semantics
        # honoring 0.7 / 0.3 weighting per user spec).
        "quality_tiers": {
            "kpi_bands": [
                {"kpi": "gmv_growth_yoy", "direction": "higher_better",
                 "correlation_group": "early_q_primary",
                 "bands": [
                     {"min": 0.30, "mult": 1.30, "label": "elite"},
                     {"min": 0.20, "mult": 1.15, "label": "strong"},
                     {"min": 0.12, "mult": 1.00, "label": "in-band"},
                     {"min": 0.10, "mult": 0.90, "label": "weak"},
                     {"min": -99,  "mult": 0.80, "label": "decel"},
                 ]},
                {"kpi": "unit_economics_ratio", "direction": "higher_better",
                 "correlation_group": "early_q_kicker",
                 "bands": [
                     {"min": 5.0, "mult": 1.10, "label": "elite-LTV"},
                     {"min": 3.5, "mult": 1.05, "label": "healthy"},
                     {"min": 2.0, "mult": 1.00, "label": "in-band"},
                     {"min": 0.0, "mult": 0.85, "label": "burn-and-pray"},
                 ]},
            ],
            "cap": [0.70, 1.50],
        },
        # V3 risk: gross_margin_pct (the path-to-profitability gate).
        # ABNB asset-light → fortress; DASH/MELI logistics-hybrid → in-band.
        "risk_adjustment": {
            "kpi": "gross_margin_pct", "direction": "higher_better",
            "bands": [
                {"min": 0.75, "mult": 1.10, "label": "fortress"},
                {"min": 0.60, "mult": 1.05, "label": "strong"},
                {"min": 0.45, "mult": 1.00, "label": "in-band"},
                {"min": 0.40, "mult": 0.92, "label": "soft"},
                {"min": 0.0,  "mult": 0.80, "label": "weak"},
            ],
        },
        "kpis": [
            {
                "key":             'gmv_growth_yoy',
                "mandatory":       True,
                "search_phrases":  ['Gross Merchandise Value growth', 'total platform volume growth', 'GMV YOY'],
                "clamp":           (-0.20, 10.0),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'unit_economics_ratio',
                "mandatory":       True,
                "search_phrases":  ['LTV to CAC ratio', 'lifetime value over acquisition cost',
                                    'unit economics multiple'],
                "compute_hint":    'Customer LTV / CAC — Elite >5x, In-band 2-3.4x, Burn-and-pray <2x',
                "clamp":           (0.1, 30.0),
                "source":          'W',
                "extractor_only":  True,
            },
            {
                "key":             'gross_margin_pct',
                "mandatory":       True,
                "search_phrases":  ['gross margin', 'gross profit margin', 'GAAP gross margin'],
                "compute_hint":    '(Revenue - COGS) / Revenue (decimal — ABNB ~83%, MELI ~50%, DASH ~50%)',
                "clamp":           (0.0, 1.00),
                "source":          'F',
                "extractor_only":  False,
                "fmp_field":       'grossProfitMarginTTM',
                "decimal_format":  True,
            },
            {
                "key":             'customer_acquisition_cost_usd',
                "mandatory":       False,
                "search_phrases":  ['CAC', 'blended acquisition cost'],
                "source":          'W',
                "extractor_only":  True,
            },
            {
                "key":             'burn_rate_monthly_usd',
                "mandatory":       False,
                "search_phrases":  ['monthly cash burn', 'net cash consumption'],
                "source":          'H',
                "extractor_only":  True,
            },
        ],
        "source_priority": ['GMV growth disclosures', 'LTV / CAC ratio (investor day decks)', 'Gross margin trend'],
    },

    'High-Growth Tech / AI': {
        "sector":         'Tech',
        "anchor_methods": ['Reverse DCF', 'TAM Penetration', 'EV/NTM Revenue'],
        # V3 quality: rpo_growth_yoy = primary anchor (forward AI bookings
        # visibility — generational spend signal); net_retention_pct =
        # independent kicker. Separate correlation groups so they MULTIPLY
        # (primary + kicker per user's 0.7 / 0.3 weight intent).
        "quality_tiers": {
            "kpi_bands": [
                {"kpi": "rpo_growth_yoy", "direction": "higher_better",
                 "correlation_group": "ai_q_primary",
                 "bands": [
                     {"min": 0.60, "mult": 1.35, "label": "elite"},
                     {"min": 0.40, "mult": 1.15, "label": "strong"},
                     {"min": 0.20, "mult": 1.00, "label": "in-band"},
                     {"min": -99,  "mult": 0.80, "label": "decel"},
                 ]},
                {"kpi": "net_retention_pct", "direction": "higher_better",
                 "correlation_group": "ai_q_kicker",
                 "bands": [
                     {"min": 1.30, "mult": 1.10, "label": "elite-NRR"},
                     {"min": 1.15, "mult": 1.05, "label": "strong-NRR"},
                     {"min": 1.00, "mult": 1.00, "label": "in-band"},
                     {"min": 0.0,  "mult": 0.92, "label": "contraction"},
                 ]},
            ],
            "cap": [0.75, 1.55],
        },
        # V3 risk: gross_margin_pct (Universal AI bands per user's Refined
        # Option A — auto-discriminates SW moat (PLTR 88%, NVDA 75% both
        # fortress) from commodity HW (SMCI 15% weak) without needing
        # sub-profile classification). Plus customer_concentration_pct as
        # a cap_when gate — heavy concentration overrides any fortress
        # GM signal (the "AI Commodity Filter" per user spec).
        "risk_adjustment": {
            "kpi": "gross_margin_pct", "direction": "higher_better",
            "bands": [
                {"min": 0.70, "mult": 1.10, "label": "fortress"},
                {"min": 0.45, "mult": 1.05, "label": "strong"},
                {"min": 0.20, "mult": 1.00, "label": "in-band"},
                {"min": 0.15, "mult": 0.90, "label": "soft"},
                {"min": 0.0,  "mult": 0.80, "label": "weak"},
            ],
            "cap_when": {
                "kpi":      "customer_concentration_pct",
                "gt":       0.40,
                "max_mult": 1.00,
                "note":     "AI Commodity Filter: top-3 customer concentration >40% caps fortress signal",
            },
        },
        "kpis": [
            {
                "key":             'rpo_growth_yoy',
                "mandatory":       True,
                "search_phrases":  ['Remaining Performance Obligations growth', 'RPO YOY', 'backlog expansion'],
                "clamp":           (-0.20, 2.0),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'net_retention_pct',
                "mandatory":       True,
                "search_phrases":  ['Net Revenue Retention', 'NRR', 'net dollar retention'],
                "clamp":           (0.7, 1.8),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'gross_margin_pct',
                "mandatory":       True,
                "search_phrases":  ['gross margin', 'gross profit margin'],
                "compute_hint":    'GAAP gross margin (decimal — NVDA 75% / PLTR 88% fortress; SMCI 15% commodity weak)',
                "clamp":           (0.0, 1.0),
                "source":          'F',
                "extractor_only":  False,
                "fmp_field":       'grossProfitMarginTTM',
                "decimal_format":  True,
            },
            {
                "key":             'customer_concentration_pct',
                "mandatory":       True,
                "search_phrases":  ['top-3 customer concentration', 'top-10 customer revenue %',
                                    'largest customer revenue percentage'],
                "compute_hint":    'Top-3 customer revenue / total revenue (decimal — >40% triggers AI Commodity cap)',
                "clamp":           (0.0, 1.0),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'customer_acquisition_cost_usd',
                "mandatory":       False,
                "search_phrases":  ['CAC', 'blended acquisition cost'],
                "source":          'W',
                "extractor_only":  True,
            },
        ],
        "source_priority": ['RPO disclosures (10-Q)', 'Gross margin trend (segment if disclosed)', 'Customer concentration footnotes'],
    },

    'Hyper-Growth Platform': {
        "sector":         'Tech',
        "anchor_methods": ['GMV-TAM Penetration', 'DCF', 'EV/NTM Revenue'],
        # V3 quality: take_rate_expansion_bps = primary anchor (pricing power
        # on existing GMV — the platform-monetisation moat); rule_of_40_score
        # = independent kicker (path-to-profitability). Both at FULL magnitude
        # in separate correlation groups so they MULTIPLY (capped at 1.50).
        "quality_tiers": {
            "kpi_bands": [
                {"kpi": "take_rate_expansion_bps", "direction": "higher_better",
                 "correlation_group": "hgp_q_primary",
                 "bands": [
                     {"min":  50, "mult": 1.30, "label": "elite"},
                     {"min":  20, "mult": 1.15, "label": "strong"},
                     {"min":   0, "mult": 1.00, "label": "in-band"},
                     {"min": -999, "mult": 0.80, "label": "compression"},
                 ]},
                {"kpi": "rule_of_40_score", "direction": "higher_better",
                 "correlation_group": "hgp_q_kicker",
                 "bands": [
                     {"min": 50, "mult": 1.30, "label": "elite"},
                     {"min": 40, "mult": 1.15, "label": "strong"},
                     {"min": 25, "mult": 1.00, "label": "in-band"},
                     {"min": -99, "mult": 0.80, "label": "weak"},
                 ]},
            ],
            "cap": [0.70, 1.50],
        },
        # V3 risk: contribution_margin_pct (the platform unit-economics gate).
        # ABNB pure software → fortress; UBER logistics-hybrid → in-band.
        "risk_adjustment": {
            "kpi": "contribution_margin_pct", "direction": "higher_better",
            "bands": [
                {"min": 0.70, "mult": 1.10, "label": "fortress"},
                {"min": 0.50, "mult": 1.05, "label": "strong"},
                {"min": 0.30, "mult": 1.00, "label": "in-band"},
                {"min": 0.25, "mult": 0.92, "label": "soft"},
                {"min": 0.0,  "mult": 0.80, "label": "weak"},
            ],
        },
        "kpis": [
            {
                "key":             'take_rate_expansion_bps',
                "mandatory":       True,
                "search_phrases":  ['take rate expansion basis points', 'platform fee increase', 'monetization rate delta'],
                "compute_hint":    'current_take_rate_bps - prior_take_rate_bps (positive = expansion, negative = compression)',
                "clamp":           (-100, 500),
                "source":          'W',
                "extractor_only":  True,
            },
            {
                "key":             'rule_of_40_score',
                "mandatory":       True,
                "search_phrases":  ['Rule of 40 score', 'revenue growth plus FCF margin'],
                "compute_hint":    'revenue_growth_pct (in %) + fcf_margin_pct (in %) — e.g. 30% growth + 15% FCF margin = 45',
                "clamp":           (-20, 120),
                "source":          'H',
                "extractor_only":  True,
            },
            {
                "key":             'contribution_margin_pct',
                "mandatory":       True,
                "search_phrases":  ['unit contribution margin', 'variable margin per order',
                                    'contribution profit %', 'segment contribution margin'],
                "compute_hint":    '(Revenue per unit - variable cost per unit) / Revenue per unit (decimal)',
                "clamp":           (-0.50, 1.0),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
        ],
        "source_priority": ['Take-rate expansion (bps) disclosures', 'Rule of 40 score (investor day)', 'Contribution margin per segment'],
    },

    'Levered Subscription': {
        "sector":         'Tech',
        "anchor_methods": ['DCF (Levered)', 'EV/EBITDA', 'LBO Analysis', 'Credit Metrics'],
        # V3 quality: arpu_monthly_usd_growth = primary anchor (pricing
        # power on the existing subscriber base — the "Growth-to-Value"
        # pivot moat); subscriber_growth_yoy = independent kicker. Separate
        # correlation groups so they MULTIPLY (per user's 0.6 / 0.4 weights).
        "quality_tiers": {
            "kpi_bands": [
                {"kpi": "arpu_monthly_usd_growth", "direction": "higher_better",
                 "correlation_group": "levered_q_primary",
                 "bands": [
                     {"min":  0.08, "mult": 1.20, "label": "elite-pricing"},
                     {"min":  0.04, "mult": 1.10, "label": "strong"},
                     {"min":  0.01, "mult": 1.00, "label": "in-band"},
                     {"min": -99,   "mult": 0.85, "label": "price-cuts"},
                 ]},
                {"kpi": "subscriber_growth_yoy", "direction": "higher_better",
                 "correlation_group": "levered_q_kicker",
                 "bands": [
                     {"min": 0.10, "mult": 1.10, "label": "elite-NFLX-style"},
                     {"min": 0.06, "mult": 1.05, "label": "strong-DIS-style"},
                     {"min": 0.02, "mult": 1.00, "label": "in-band-saturated"},
                     {"min": -99,  "mult": 0.92, "label": "shrinking-DISH-style"},
                 ]},
            ],
            "cap": [0.70, 1.40],
        },
        # V3 risk: net_debt_to_ebitda — TIGHTER bands per user's "higher-
        # for-longer 2026" thesis. 5.5× leverage on subscription business
        # is a ticking clock. NFLX (<2×) is the new gold standard.
        "risk_adjustment": {
            "kpi": "net_debt_to_ebitda", "direction": "lower_better",
            "bands": [
                {"max": 2.0,  "mult": 1.15, "label": "fortress-NFLX"},
                {"max": 3.5,  "mult": 1.05, "label": "strong"},
                {"max": 4.5,  "mult": 1.00, "label": "in-band"},
                {"max": 5.5,  "mult": 0.90, "label": "stretched-SIRI"},
                {"max": 99,   "mult": 0.70, "label": "weak-DISH"},
            ],
        },
        "kpis": [
            {
                "key":             'arpu_monthly_usd_growth',
                "mandatory":       True,
                "search_phrases":  ['ARPU growth YoY', 'monthly ARPU growth',
                                    'average revenue per user growth', 'ARPU trend'],
                "compute_hint":    'Annualised growth in monthly ARPU (decimal — NFLX 2024 ~10% from price hikes + ad tier)',
                "clamp":           (-0.30, 0.50),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'subscriber_growth_yoy',
                "mandatory":       True,
                "search_phrases":  ['paid subscriber growth', 'net subscriber additions YoY',
                                    'global subscriber base growth'],
                "compute_hint":    'YoY growth in paid subscribers (decimal — NFLX 2024 ~14%, DISH/SIRI negative)',
                "clamp":           (-0.30, 0.50),
                "source":          'W',
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             'net_debt_to_ebitda',
                "mandatory":       True,
                "search_phrases":  ['net leverage ratio', 'Net Debt / Adjusted EBITDA', 'leverage covenant'],
                "compute_hint":    '(total_debt - cash) / LTM_EBITDA — NFLX <2x fortress, DISH >5.5x distressed',
                "clamp":           (-1.0, 12.0),
                "source":          'F',
                "extractor_only":  False,
                "fmp_field":       'netDebtToEBITDATTM',
            },
            {
                "key":             'fcf_debt_service_coverage',
                "mandatory":       True,
                "search_phrases":  ['FCF / interest expense', 'debt service coverage ratio'],
                "source":          'F',
                "extractor_only":  False,
            },
            {
                "key":             'cost_of_debt_pct',
                "mandatory":       False,
                "search_phrases":  ['weighted-average cost of debt', 'interest rate on borrowings'],
                "source":          'F',
                "extractor_only":  False,
            },
        ],
        "source_priority": ['ARPU + subscriber count disclosures (10-Q)', 'Net debt-to-EBITDA (leverage covenants)', 'Debt service coverage ratios'],
    },

    'Mature Platform': {
        "sector":         'Tech',
        "anchor_methods": ['DCF (FCF)', 'EV/EBITDA', 'P/E (ops)', 'LBO Analysis'],
        # V3 quality: fcf_yield_pct = primary anchor (the cash-machine moat
        # for mature tech); buyback_yield_pct = independent kicker (capital
        # return discipline). Separate correlation groups so they MULTIPLY
        # per user's 0.8 / 0.2 weighting intent.
        "quality_tiers": {
            "kpi_bands": [
                {"kpi": "fcf_yield_pct", "direction": "higher_better",
                 "correlation_group": "mature_q_primary",
                 "bands": [
                     {"min": 0.07,  "mult": 1.25, "label": "elite-cash-machine"},
                     {"min": 0.05,  "mult": 1.10, "label": "strong"},
                     {"min": 0.03,  "mult": 1.00, "label": "in-band"},
                     {"min": 0.0,   "mult": 0.80, "label": "yield-trap"},
                 ]},
                {"kpi": "buyback_yield_pct", "direction": "higher_better",
                 "correlation_group": "mature_q_kicker",
                 "bands": [
                     {"min": 0.05,  "mult": 1.10, "label": "elite-buyback"},
                     {"min": 0.03,  "mult": 1.05, "label": "strong-return"},
                     {"min": 0.015, "mult": 1.00, "label": "in-band"},
                     {"min": 0.0,   "mult": 0.95, "label": "no-return"},
                 ]},
            ],
            "cap": [0.70, 1.45],
        },
        # V3 risk: r_and_d_intensity_pct as the "Stagnation Gauge" — for
        # Mature Platforms the risk isn't leverage, it's BECOMING THE NEXT
        # SUN MICROSYSTEMS. Higher R&D = staying alive. Lower R&D = harvest
        # mode that kills terminal-value moat.
        "risk_adjustment": {
            "kpi": "r_and_d_intensity_pct", "direction": "higher_better",
            "bands": [
                {"min": 0.18, "mult": 1.10, "label": "fortress-reinvest"},
                {"min": 0.13, "mult": 1.00, "label": "in-band"},
                {"min": 0.12, "mult": 0.92, "label": "thin"},
                {"min": 0.0,  "mult": 0.85, "label": "stagnation-warning"},
            ],
        },
        "kpis": [
            {
                "key":             'fcf_yield_pct',
                "mandatory":       True,
                "search_phrases":  ['Free Cash Flow yield', 'FCF as percentage of market cap'],
                "compute_hint":    'LTM_FCF / market_cap (decimal — Mature: ORCL ~6%, CSCO ~5%)',
                "clamp":           (0.0, 0.20),
                "source":          'F',
                "extractor_only":  False,
                "decimal_format":  True,
            },
            {
                "key":             'buyback_yield_pct',
                "mandatory":       True,
                "search_phrases":  ['share repurchase yield', 'buyback as % of market cap',
                                    'net buyback yield'],
                "compute_hint":    'TTM net buybacks / market cap (decimal — META FY24 ~5%, AAPL ~3%)',
                "clamp":           (0.0, 0.15),
                "source":          'F',
                "extractor_only":  False,
                "decimal_format":  True,
            },
            {
                "key":             'r_and_d_intensity_pct',
                "mandatory":       True,
                "search_phrases":  ['R&D intensity', 'research and development as % of revenue',
                                    'R&D / revenue', 'R&D spend ratio'],
                "compute_hint":    'TTM R&D expense / TTM revenue (decimal — Mature elite >18%, stagnation <12%)',
                "clamp":           (0.0, 0.50),
                "source":          'F',
                "extractor_only":  False,
                "decimal_format":  True,
            },
            {
                "key":             'dividend_payout_ratio',
                "mandatory":       False,
                "search_phrases":  ['dividend payout ratio', 'dividends / net income'],
                "source":          'F',
                "extractor_only":  False,
            },
        ],
        "source_priority": ['FCF + buyback yield disclosures', 'R&D intensity trend (10-K opex breakdown)', 'Capital allocation policy'],
    },

# ── Transportation ──────────────────────────────────────────────────
    'Airlines': {
        "sector":         'Transportation',
        "anchor_methods": ['EV/EBITDAR', 'FCF Yield', 'P/BV'],
        # V3 quality: casm_ex_fuel primary (cost discipline) + load_factor_pct kicker.
        "quality_tiers": {
            "kpi_bands": [
                {"kpi": "casm_ex_fuel", "direction": "lower_better",
                 "correlation_group": "airline_q_primary",
                 "bands": [
                     {"max": 0.10, "mult": 1.30, "label": "elite-Spirit-Frontier-low-cost"},
                     {"max": 0.12, "mult": 1.15, "label": "strong"},
                     {"max": 0.13, "mult": 1.00, "label": "in-band"},
                     {"max": 99,   "mult": 0.85, "label": "bloated-legacy-hubs"},
                 ]},
                {"kpi": "load_factor_pct", "direction": "higher_better",
                 "correlation_group": "airline_q_kicker",
                 "bands": [
                     {"min": 0.85, "mult": 1.10, "label": "strong-yield-mgmt"},
                     {"min": 0.80, "mult": 1.00, "label": "in-band"},
                     {"min": 0.0,  "mult": 0.90, "label": "weak"},
                 ]},
            ],
            "cap": [0.70, 1.40],
        },
        # V3 risk: net_debt_to_ebitda — airlines have BANKRUPTCY HISTORY,
        # leverage gates are tight (LUV historical fortress at <2x).
        "risk_adjustment": {
            "kpi": "net_debt_to_ebitda", "direction": "lower_better",
            "bands": [
                {"max": 2.0,  "mult": 1.10, "label": "fortress-LUV-historical"},
                {"max": 3.5,  "mult": 1.00, "label": "in-band"},
                {"max": 5.0,  "mult": 0.90, "label": "stretched"},
                {"max": 99,   "mult": 0.70, "label": "weak-bankruptcy-risk"},
            ],
        },
        "kpis": [
            {"key": 'casm_ex_fuel',     "mandatory": True, "search_phrases": ['cost per available seat mile ex-fuel','unit cost ex-fuel','CASM-ex'], "compute_hint": 'operating_exp_ex_fuel/ASM (cents per ASM)', "clamp": (0.05, 0.30), "source": 'W', "extractor_only": True, "decimal_format": False},
            {"key": 'load_factor_pct',  "mandatory": True, "search_phrases": ['passenger load factor','occupancy rate','percentage of seats filled'], "compute_hint": 'RPM/ASM (decimal)', "clamp": (0.50, 1.0), "source": 'W', "extractor_only": True, "decimal_format": True},
            {"key": 'net_debt_to_ebitda', "mandatory": True, "search_phrases": ['net debt to EBITDA','leverage ratio'], "clamp": (-3.0, 12.0), "source": 'F', "extractor_only": False, "fmp_field": 'netDebtToEBITDATTM'},
            {"key": 'yield_per_pax_mile', "mandatory": False, "search_phrases": ['passenger yield','average fare per mile','yield per RPM'], "clamp": (0.05, 0.50), "source": 'W', "extractor_only": True},
            {"key": 'prasm_yoy',          "mandatory": False, "search_phrases": ['PRASM growth','Passenger Revenue per ASM YoY'], "clamp": (-0.30, 0.40), "source": 'W', "extractor_only": True, "decimal_format": True},
        ],
        "source_priority": ['Monthly operating statistics', 'CASM-ex schedules', '10-K fleet schedules', 'Leverage'],
    },

    'Rail / Logistics': {
        "sector":         'Transportation',
        "anchor_methods": ['EV/EBITDA', 'FCF Yield', 'P/E (ops)'],
        # V3 quality: operating_ratio_pct primary (THE rail efficiency anchor —
        # CSX/NSC precision-railroading <60% elite) + revenue_ton_miles_growth kicker.
        "quality_tiers": {
            "kpi_bands": [
                {"kpi": "operating_ratio_pct", "direction": "lower_better",
                 "correlation_group": "rail_q_primary",
                 "bands": [
                     {"max": 0.60, "mult": 1.30, "label": "elite-precision-railroading-CSX-NSC"},
                     {"max": 0.65, "mult": 1.15, "label": "strong"},
                     {"max": 0.70, "mult": 1.00, "label": "in-band"},
                     {"max": 99,   "mult": 0.85, "label": "weak-bloated"},
                 ]},
                {"kpi": "revenue_ton_miles_growth", "direction": "higher_better",
                 "correlation_group": "rail_q_kicker",
                 "bands": [
                     {"min":  0.05, "mult": 1.10, "label": "strong-cycle"},
                     {"min":  0.0,  "mult": 1.00, "label": "in-band"},
                     {"min": -99,   "mult": 0.85, "label": "recession-signal"},
                 ]},
            ],
            "cap": [0.70, 1.45],
        },
        "risk_adjustment": {
            "kpi": "net_debt_to_ebitda", "direction": "lower_better",
            "bands": [
                {"max": 2.5,  "mult": 1.10, "label": "fortress"},
                {"max": 4.0,  "mult": 1.00, "label": "in-band"},
                {"max": 4.5,  "mult": 0.92, "label": "stretched"},
                {"max": 99,   "mult": 0.85, "label": "weak"},
            ],
        },
        "kpis": [
            {"key": 'operating_ratio_pct',  "mandatory": True, "search_phrases": ['railroad operating ratio','operating expenses divided by revenue','efficiency ratio'], "compute_hint": 'total_opex/total_revenue (decimal — <60% elite precision-railroading)', "clamp": (0.40, 1.0), "source": 'F', "extractor_only": False, "decimal_format": True},
            {"key": 'revenue_ton_miles_growth', "mandatory": True, "search_phrases": ['RTM growth','revenue ton miles YOY','freight volume growth'], "compute_hint": '(current_rtm/prior_rtm)-1 (decimal)', "clamp": (-0.30, 0.30), "source": 'W', "extractor_only": True, "decimal_format": True},
            {"key": 'net_debt_to_ebitda',   "mandatory": True, "search_phrases": ['net debt to EBITDA','leverage ratio'], "clamp": (-3.0, 8.0), "source": 'F', "extractor_only": False, "fmp_field": 'netDebtToEBITDATTM'},
            {"key": 'fuel_efficiency_delta', "mandatory": False, "search_phrases": ['fuel consumption per ton-mile'], "source": 'W', "extractor_only": True},
        ],
        "source_priority": ['Operating ratio disclosures', 'RTM growth', 'Leverage'],
    },

}


# ════════════════════════════════════════════════════════════════════════════
# V3.2 — 3-Layer Search Phrase Enrichment
#
# Auto-augments per-KPI search_phrases at extraction time so the framework
# data stays terse but the LLM gets rich phrase guidance. Same ~30 KPIs that
# previously had 1-3 phrases each now get 5-10 phrases via pattern matching
# and sector vocabulary, without touching 60 profile dicts.
# ════════════════════════════════════════════════════════════════════════════

# Layer 1 — KPI key-suffix patterns (cross-sector, KPI-shape-keyed)
_PHRASE_LIBRARY: dict[str, list[str]] = {
    "_ratio":      ["ratio", "expressed as %", "as decimal", "in basis points"],
    "_pct":        ["%", "percent", "basis points", "bps", "as decimal"],
    "_yoy":        ["YoY", "year-over-year", "vs prior year", "annual growth"],
    "_growth":     ["YoY growth", "growth rate", "CAGR"],
    "_margin":     ["margin", "as % of revenue", "as % of sales", "operating margin"],
    "_per_share":  ["per share", "per diluted share", "DPS", "EPS"],
    "_per_oz":     ["per oz", "per ounce", "per troy ounce"],
    "_per_boe":    ["per BOE", "per barrel of oil equivalent", "per Mcfe"],
    "_runway":     ["months of runway", "cash runway", "burn coverage"],
    "_quartile":   ["quartile", "Q1/Q2/Q3/Q4", "decile rank"],
    "_year":       ["year", "expiry year", "in 20XX"],
    "_intensity":  ["intensity", "as % of revenue", "spending"],
    "_coverage":   ["coverage ratio", "times covered", "x"],
    "_yield":      ["yield", "% per annum", "yield-to-maturity"],
}

# Layer 2 — Sector-specific vocabulary (broad sector → standard industry terms)
_SECTOR_LEXICON: dict[str, list[str]] = {
    "Biopharma":   ["blockbuster", "patent cliff", "GLP-1", "PDUFA", "FDA approval",
                    "Phase 3 readout", "label expansion", "exclusivity"],
    "Healthcare":  ["MLR", "underwriting", "membership growth", "PMPM", "premium yield"],
    "Financials":  ["Basel III", "RWA", "Tier 1 capital", "regulatory stress test",
                    "leverage ratio", "loan-to-deposit"],
    "Resources":   ["AISC", "C1 cost", "by-product credit", "ore grade",
                    "reserve replacement", "PV-10", "cost curve"],
    "Energy":      ["lifting cost", "F&D cost", "spot vs realised", "rate base",
                    "regulatory lag", "PPA pricing", "spark spread"],
    "Materials":   ["realized price", "throughput", "utilization rate", "spread"],
    "Tech":        ["NRR", "ARR", "Rule of 40", "billings", "RPO", "magic number",
                    "CAC payback", "logo retention", "GAAP-to-non-GAAP"],
    "Semiconductor": ["wafer", "design wins", "lead times", "GM%", "fab utilisation",
                      "node generation", "AI accelerator"],
    "Industrials": ["book-to-bill", "backlog conversion", "order momentum",
                    "service revenue mix", "aftermarket"],
    "Industrial":  ["book-to-bill", "backlog conversion", "order momentum"],
    "Consumer":    ["SSSG", "comp sales", "store productivity", "unit growth",
                    "mix headwind", "pricing power"],
    "Telco":       ["ARPU", "churn", "5G coverage", "fiber penetration", "subscriber adds",
                    "FTTH"],
    "Transportation": ["load factor", "yield per mile", "operating ratio", "RTM"],
    "Crypto":      ["DAU", "TPS", "TVL", "hash rate", "block reward"],
    "ProfessionalServices": ["billable utilisation", "attrition", "offshore mix",
                             "attach rate"],
}

# Layer 3 — Section-aware extraction hints (where in the report to look)
_SECTION_HINTS = (
    "Look in BOTH (a) the 2F.5b sub-profile-specific metrics table AND "
    "(b) the narrative prose of 2F.1-2F.4 AND (c) the 2F.6 Management "
    "Guidance section. KPI values may be quoted as midpoints, ranges, "
    "or with citation markers like [12]."
)


def enrich_search_phrases(kpi: dict, sector: str) -> list[str]:
    """V3.2 — auto-enrich KPI search_phrases without editing 60 profile dicts.

    Returns deduped list of phrases =
        kpi["search_phrases"] (curated minimum from framework data)
      + Layer 1: pattern-matched variants from _PHRASE_LIBRARY (key suffix)
      + Layer 2: sector-specific vocabulary (capped to top 4 to avoid bloat)
      + Layer 3: narrative form of the KPI key itself ("net debt to ebitda")
    """
    out = list(kpi.get("search_phrases", []))
    key = kpi.get("key", "").lower()
    # Layer 1: pattern matching on key suffix
    for suffix, lib in _PHRASE_LIBRARY.items():
        if suffix in key:
            out.extend(lib)
    # Layer 2: sector lexicon (cap to keep prompt size bounded)
    sector_terms = _SECTOR_LEXICON.get(sector, [])
    out.extend(sector_terms[:4])
    # Layer 3: narrative form of key itself
    if "_" in key:
        out.append(key.replace("_", " "))
    # Dedupe preserving order
    return list(dict.fromkeys(out))


# ── Renderer 1: Section 2F overlay text ──────────────────────────────────────

def render_search_overlay(
    profile_name: str,
    sector: str = "",
    sub_sub: str = "",
) -> str:
    """L4 — produce the text to inject into Section 2F (between 2F.5 and 2F.6)
    of the deep research system prompt.

    Resolution order:
      1. profile_name (e.g. "Insurance")
      2. sector       (e.g. "Financials")
      3. ""           (no append; generic 2F is unchanged)

    sub_sub gate: when set (e.g. "P&C" or "Life"), filters to KPIs whose
    `applies_to` includes the sub-sub-profile. When unset, all KPIs are listed.
    """
    spec = SECTOR_KPI_FRAMEWORK.get(profile_name) or SECTOR_KPI_FRAMEWORK.get(sector)
    if not spec:
        return ""
    # v3.4: include ALL KPIs (not just extractor_only). The original filter
    # excluded FMP-derivable KPIs from the 2F overlay on the rationale that
    # FMP fetches them anyway — but in the V3 tier architecture, FMP-derived
    # KPIs (e.g. fcf_yield_pct, r_and_d_intensity_pct) ARE quality/risk
    # tier drivers, so they MUST appear in 2F so the LLM cites them in the
    # narrative report. The narrative citation also serves as a cross-check
    # against the FMP-derived value (catches FMP staleness / wrong taxonomy).
    web_kpis = list(spec["kpis"])
    if sub_sub:
        web_kpis = [
            k for k in web_kpis
            if not k.get("applies_to") or sub_sub in k["applies_to"]
        ]
    if not web_kpis:
        return ""

    mandatory = [k for k in web_kpis if k.get("mandatory")]
    optional  = [k for k in web_kpis if not k.get("mandatory")]

    lines: list[str] = [
        f"\n2F.5b {profile_name.upper()}-SPECIFIC METRICS "
        f"(in addition to generic 2F.1\u20132F.5 above):"
    ]
    sector_for_enrich = spec.get("sector", "")
    if mandatory:
        lines.append("\nMANDATORY (must appear in your Section 2F report):")
        for k in mandatory:
            # V3.2 enrichment: combine framework phrases + Layer 1 patterns + Layer 2 sector lexicon
            enriched = enrich_search_phrases(k, sector_for_enrich)
            phrases = " | ".join(f"'{p}'" for p in enriched[:8])
            applies = (
                f"  ({', '.join(k['applies_to'])} only)"
                if k.get("applies_to") and not sub_sub else ""
            )
            hint = f" \u2014 {k['compute_hint']}" if k.get("compute_hint") else ""
            lines.append(f"  - {k['key']}: search for {phrases}{applies}{hint}")
    if optional:
        lines.append("\nNICE-TO-HAVE (include when found):")
        for k in optional:
            enriched = enrich_search_phrases(k, sector_for_enrich)
            phrases = " | ".join(f"'{p}'" for p in enriched[:6])
            applies = (
                f"  ({', '.join(k['applies_to'])} only)"
                if k.get("applies_to") and not sub_sub else ""
            )
            lines.append(f"  - {k['key']}: {phrases}{applies}")
    if spec.get("source_priority"):
        lines.append(
            f"\nSource priority: {' > '.join(spec['source_priority'])}"
        )
    lines.append(
        "Cite each figure with date and source name "
        "(e.g. \"Q1 2026 release 2026-04-15\")."
    )
    return "\n".join(lines) + "\n"


# ── Renderer 2: extractor schema (system prompt + clamps dict) ───────────────

def build_extractor_schema(profile_name: str) -> dict:
    """L5 — auto-generate the extractor LLM system prompt + clamps dict from
    the framework spec. Replaces hand-written schemas in _extract_X_metrics.

    Returns:
        {
            "system_prompt": str,            # send to sdk_client.messages.create
            "clamps":        dict[str, tuple],  # per-field (lo, hi) for validation
            "kpi_keys":      list[str],      # all WEB-only field names
            "mandatory":     list[str],      # subset that are required for completeness
        }
    """
    spec = SECTOR_KPI_FRAMEWORK.get(profile_name)
    if not spec:
        return {"system_prompt": "", "clamps": {}, "kpi_keys": [], "mandatory": []}

    # v3.4: include ALL KPIs (see render_search_overlay docstring for rationale).
    # FMP-derivable KPIs that drive V3 quality/risk tiers MUST be in the
    # extractor schema so the LLM extracts them as a fallback when FMP misses
    # the value AND so the LLM cross-checks FMP against the narrative.
    web_kpis = list(spec["kpis"])
    clamps = {k["key"]: tuple(k["clamp"]) for k in web_kpis if "clamp" in k}
    mandatory = [k["key"] for k in web_kpis if k.get("mandatory")]
    sector = spec.get("sector", "")

    # V3.2 — embed enriched search phrases per KPI so the extractor LLM
    # has concrete terms to look for in the text (vs guessing what synonyms
    # the report used).
    schema_lines = []
    for k in web_kpis:
        enriched_phrases = enrich_search_phrases(k, sector)
        # Cap phrase string length to keep prompt size bounded
        phrase_str = ", ".join(enriched_phrases[:8])
        if "clamp" in k:
            schema_lines.append(
                f"  {k['key']}: float ({k['clamp'][0]}-{k['clamp'][1]}, "
                f"{k.get('compute_hint', '')}) "
                f"[search: {phrase_str}]"
            )
        else:
            schema_lines.append(
                f"  {k['key']}: {k.get('compute_hint', 'free-form')} "
                f"[search: {phrase_str}]"
            )

    rule_lines = []
    for k in web_kpis:
        if k.get("decimal_format"):
            rule_lines.append(
                f"  * {k['key']}: convert percentages to decimals "
                f"(e.g. 95.3% \u2192 0.953)"
            )

    system_prompt = (
        f"You are a {profile_name}-sector analyst. Extract structured KPIs from "
        f"the research and return ONLY valid JSON (no markdown fences, no commentary).\n\n"
        f"Schema (all fields OPTIONAL \u2014 omit if not substantiated by research):\n"
        + "\n".join(schema_lines) + "\n"
        + "  evidence: string \u2264300 chars citing research source\n\n"
        + f"Where to look in the text: {_SECTION_HINTS}\n\n"
        + f"Rules:\n"
        + f"  * Return {{}} if the company isn't a {profile_name.lower()} business.\n"
        + ("\n".join(rule_lines) + "\n" if rule_lines else "")
    )

    return {
        "system_prompt": system_prompt,
        "clamps":        clamps,
        "kpi_keys":      [k["key"] for k in web_kpis],
        "mandatory":     mandatory,
    }


# ── Renderer 3: validator (soft-mandatory completeness scoring) ──────────────

def validate_extractor_output(profile_name: str, output: dict) -> dict:
    """Annotate extractor output with _completeness_score + _mandatory_missing.

    Soft-mandatory: NEVER raises. Missing mandatory KPIs are flagged for the
    UI badge but the extractor still returns whatever it found. Downstream
    method branches in dcf_agent apply per-KPI fallbacks.
    """
    spec = SECTOR_KPI_FRAMEWORK.get(profile_name)
    if not spec:
        output["_completeness_score"] = 1.0
        return output

    mandatory_keys = [
        k["key"] for k in spec["kpis"] if k.get("mandatory")
    ]
    if not mandatory_keys:
        output["_completeness_score"] = 1.0
        return output

    missing = [k for k in mandatory_keys if k not in output]
    output["_mandatory_missing"]  = missing
    output["_completeness_score"] = round(
        (len(mandatory_keys) - len(missing)) / len(mandatory_keys), 2
    )
    return output


# ── Renderer 4: generic LLM extractor (replaces hand-written _extract_X) ─────

def extract_via_framework(
    sdk_client,
    model_name: str,
    sections: dict[str, str],
    deep_research: str,
    ticker: str,
    profile_name: str,
) -> dict:
    """L5 — generic sector extractor. Calls the LLM with the framework-rendered
    system prompt, validates the output against framework clamps, and annotates
    with completeness score.

    Mirrors the input gate + try/except + parse + clamp + validate pipeline
    used by the legacy _extract_X_metrics functions, but driven entirely by
    the framework spec instead of hand-typed schemas.

    Returns {} when not applicable or research too thin.
    """
    spec_built = build_extractor_schema(profile_name)
    if not spec_built["system_prompt"]:
        return {}     # profile not in framework — caller should use legacy extractor

    if not deep_research and not sections:
        return {}

    section_2a = sections.get("2a") or sections.get("2A") or ""
    section_2d = sections.get("2d") or sections.get("2D") or ""
    section_2f = sections.get("2f") or sections.get("2F") or ""
    # FIX (audit Apr 2026): Section 2F goes FIRST (the 2F.5b table contains
    # the framework KPIs we're extracting). Old order was 2A+2D+2F which got
    # truncated at 8000 chars when 2A was verbose, dropping 2F.5b entirely.
    # New order: 2F-first + bumped truncation to 16000 chars (covers full
    # Pharma/Bank reports without losing the KPI table).
    combined = (section_2f + "\n\n" + section_2a + "\n\n" + section_2d).strip()
    if not combined or len(combined) < 500:
        combined = (deep_research or "")[:16000]
    if not combined:
        return {}

    # Tightened mandatory-extraction directive: forces the LLM to actually
    # search for each declared KPI rather than returning {} when uncertain.
    mandatory_keys = spec_built.get("mandatory", [])
    mandatory_directive = ""
    if mandatory_keys:
        mandatory_directive = (
            f"\n\nMANDATORY: For each of these KPIs you MUST search the text "
            f"for the value (look in section 2F.5b table format, narrative "
            f"prose, and the Management Guidance section): "
            f"{', '.join(mandatory_keys)}.\n"
            f"If a value is in the text expressed as a range (e.g. '13-15%'), "
            f"return the midpoint. If expressed as 'approximately X', return X. "
            f"Only return null/omit if the value is genuinely absent.\n"
        )

    try:
        # temperature=0.1 — extractors want deterministic JSON output. Default
        # ~0.7 is fine for prose synthesis but causes Qwen to skip mandatory
        # KPIs randomly (~25% recall observed). Mirror the fix from c0ce2e9
        # which applied this to the re-extract adapter path.
        resp = sdk_client.messages.create(
            model=model_name,
            max_tokens=900,    # bumped from 600 to allow more KPIs + evidence
            temperature=0.1,
            system=spec_built["system_prompt"] + mandatory_directive,
            messages=[{
                "role": "user",
                "content": (
                    f"Ticker: {ticker}\n\n"
                    f"Research excerpts (Section 2F prioritised — KPI table "
                    f"is typically in 2F.5b):\n{combined[:16000]}"
                ),
            }],
        )
        raw = "".join(b.text for b in resp.content if hasattr(b, "text")).strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            return {}

        out: dict = {}
        for k, (lo, hi) in spec_built["clamps"].items():
            v = parsed.get(k)
            if isinstance(v, (int, float)) and lo <= v <= hi:
                out[k] = float(v)
        if "evidence" in parsed:
            out["evidence"] = str(parsed["evidence"])[:300]

        return validate_extractor_output(profile_name, out)
    except Exception:
        return {}


# ── Renderer 5: dcf_agent attachment (override most_recent in one loop) ──────

def attach_overrides(
    profile_name: str,
    extractor_output: dict,
    most_recent: dict,
) -> list[str]:
    """L6a — generic loop that attaches extractor output to `most_recent` so
    `_compute_method_value` branches can read overrides via `.get()`.

    Replaces hand-written per-sector if-blocks like:
        if "cap_rate_market" in _rm_override:
            most_recent["cap_rate_market"] = _rm_override["cap_rate_market"]

    Returns a list of human-readable audit lines (e.g. for ticker_forward_flags).
    """
    spec = SECTOR_KPI_FRAMEWORK.get(profile_name)
    if not spec or not extractor_output:
        return []

    audit: list[str] = []
    for kpi in spec["kpis"]:
        key = kpi["key"]
        if key in extractor_output:
            most_recent[key] = extractor_output[key]
            if "compute_hint" in kpi:
                audit.append(f"{key}={extractor_output[key]} ({kpi['compute_hint']})")
            else:
                audit.append(f"{key}={extractor_output[key]}")

    # Surface metadata
    if "_completeness_score" in extractor_output:
        most_recent[f"_{profile_name}_completeness"] = extractor_output["_completeness_score"]
    if "_mandatory_missing" in extractor_output:
        most_recent[f"_{profile_name}_missing"] = extractor_output["_mandatory_missing"]

    return audit


# ── Renderer 6: specialist prompt addendum (industry brief KPI table) ────────

def render_specialist_addendum(
    profile_name: str,
    sector: str = "",
    sub_sub: str = "",
) -> str:
    """Produce a markdown prompt addendum that instructs the specialist agent's
    LLM to output a `## Key Sector Metrics` markdown table containing this
    sub-profile's mandatory + nice-to-have KPIs.

    The specialist agent appends this addendum to its sector_block prompt at
    LLM-call time. The LLM then writes the filled-in KPI table into the
    industry_brief markdown, which the existing IndustryBriefPanel.tsx
    renders natively to the frontend (auto-built ToC picks up the h2 heading).

    Resolution order:
      1. SECTOR_KPI_FRAMEWORK[profile_name]
      2. SECTOR_KPI_FRAMEWORK[sector]
      3. ""  → empty addendum → specialist prompt unchanged

    sub_sub gate: when set (e.g. "P&C" or "Life"), filters KPIs whose
    `applies_to` includes the sub-sub-profile. Unset → all KPIs included.
    """
    spec = SECTOR_KPI_FRAMEWORK.get(profile_name) or SECTOR_KPI_FRAMEWORK.get(sector)
    if not spec:
        return ""
    web_kpis = [k for k in spec["kpis"] if k.get("extractor_only")]
    if sub_sub:
        web_kpis = [
            k for k in web_kpis
            if not k.get("applies_to") or sub_sub in k["applies_to"]
        ]
    # Include FMP-derivable KPIs too — they're informative for the brief even
    # if not LLM-extracted (the LLM can read them from the FMP-loaded data block).
    fmp_kpis = [k for k in spec["kpis"] if not k.get("extractor_only")]
    all_kpis = web_kpis + fmp_kpis
    if not all_kpis:
        return ""

    label_for = lambda k: (
        k.get("compute_hint") or k["key"].replace("_", " ").title()
    )

    lines: list[str] = []
    lines.append("\n")
    lines.append("=" * 60)
    lines.append(f"SECTOR KPI ADDENDUM — {profile_name}")
    lines.append("=" * 60)
    lines.append("")
    lines.append(
        f"After your sector analysis, you MUST output a `## Key Sector Metrics` "
        f"section containing the markdown table below. Fill values from the "
        f"research; mark missing ones as `n/d` (not disclosed). Use the units "
        f"implied by the metric name (% for ratios, $ for monetary, count "
        f"for ratios like Rule of 40)."
    )
    lines.append("")
    lines.append("## Key Sector Metrics")
    lines.append("")
    lines.append("| Metric | Value | Source |")
    lines.append("|---|---|---|")
    for kpi in all_kpis:
        label = label_for(kpi)
        applies = (
            f" ({', '.join(kpi['applies_to'])} only)"
            if kpi.get("applies_to") and not sub_sub else ""
        )
        mandatory_marker = " **(M)**" if kpi.get("mandatory") else ""
        lines.append(f"| {label}{applies}{mandatory_marker} | <fill> | <[n]> |")
    lines.append("")
    lines.append(
        f"**Mandatory metrics** are flagged with the M-marker in bold parens — "
        f"these MUST be populated (use `n/d` only if the research truly didn't "
        f"surface them). Nice-to-have metrics are unmarked."
    )
    if spec.get("source_priority"):
        lines.append("")
        lines.append(
            f"**Source priority** (cite [n] for each value): "
            f"{' > '.join(spec['source_priority'])}."
        )
    lines.append("")
    return "\n".join(lines)


# ════════════════════════════════════════════════════════════════════════════
# render_card_payload — produces the JSON payload consumed by the frontend
# `SectorValuationCard` component (Option B styling). The shape mirrors the
# TypeScript `SectorValuationCardDataB` interface exactly, so adding new
# fields here requires updating reportTypes.ts in lockstep.
#
# CRITICAL persistence rules (per prior incident — see commits 1ac5490,
# 10ed937, d748ad4):
#   1. The pipeline MUST include the rendered `sector_card` dict in its
#      return-dict (`run_advanced_pipeline()`); state-only writes get lost.
#   2. The web_runs partial-save (_save_checkpoint) MUST include sector_card
#      so SSE progressive UI works.
#   3. The archive MUST add a `sector_card_json` column (ticker_signals)
#      via the migrations list, and save_run() MUST write it.
#   4. get_run_result() MUST read it back from BOTH paths (web_runs JSON
#      AND archive ticker_signals reconstruction).
# ════════════════════════════════════════════════════════════════════════════

# ════════════════════════════════════════════════════════════════════════════
# V3 COMPOSITE ADJUSTMENT — Quality × Risk × Commodity multipliers
#
# Three independent multipliers that lift/discount the aggregated IV:
#   - Quality (Fix 1): operational excellence (best-in-class margins/growth)
#   - Risk    (Fix 2): balance sheet strength (lower discount rate)
#   - Commodity (Fix 3): forward commodity-price leverage (terminal margin)
#
# Stacking: multiplicative with correlation guard (correlated KPIs in same
# bucket take max-deviation, independent KPIs multiply).
#
# Sector caps:
#   - Resources/Energy/Materials: composite ∈ [0.50, 1.70]  (commodity ceiling)
#   - All other sectors:           composite ∈ [0.50, 1.85]
# ════════════════════════════════════════════════════════════════════════════

_COMMODITY_SECTORS: frozenset[str] = frozenset({"Resources", "Energy", "Materials"})


# ── V3 Data-driven schema: per-profile bands ────────────────────────────────
#
# Each profile in SECTOR_KPI_FRAMEWORK can declare any of three optional
# top-level keys:
#
#   "quality_tiers": {
#       "kpi_bands": [
#           {
#               "kpi": "<KPI name from kpis list>",
#               "direction": "lower_better" | "higher_better",
#               "correlation_group": "<group_id>",  # KPIs in same group take max-dev
#               "bands": [
#                   {"max": 0.88, "mult": 1.50, "label": "elite"},  # for lower_better
#                   {"min": 0.16, "mult": 1.30, "label": "premium"},  # for higher_better
#                   ...
#               ],
#           },
#           ...
#       ],
#       "cap": [0.70, 1.50],  # final quality_multiplier clamp
#   }
#
#   "risk_adjustment": {
#       "kpi": "<KPI name>", "direction": "lower_better" | "higher_better",
#       "bands": [...],
#   }
#
#   "commodity_uplift": {
#       "spot_kpi": "spot_commodity_price",
#       "realised_kpi": "realised_price_per_unit",
#       "cost_kpi": "aisc_per_oz",
#       "spot_weight": 0.33,
#       "max_uplift": 1.40,
#   }
#
# Profiles WITHOUT these slots fall back to (1.0, "no schema declared").


def _evaluate_band(kpi_value: float | None, cfg: dict) -> tuple[float, str] | None:
    """Walk a kpi-bands config and return the matching band's (multiplier, note),
    or None if no band matches or value is missing."""
    if kpi_value is None:
        return None
    direction = cfg.get("direction", "lower_better")
    bands = cfg.get("bands", [])
    is_higher_better = direction == "higher_better"
    # Sort so we evaluate strongest-first (lowest threshold for lower_better,
    # highest for higher_better — first match wins)
    if is_higher_better:
        sorted_bands = sorted(bands, key=lambda b: -b.get("min", float("-inf")))
    else:
        sorted_bands = sorted(bands, key=lambda b: b.get("max", float("inf")))
    for band in sorted_bands:
        if is_higher_better and "min" in band and kpi_value >= band["min"]:
            return (float(band["mult"]),
                    f"{cfg['kpi']}={kpi_value} {band.get('label','')} ({band['mult']:.2f}x)")
        if not is_higher_better and "max" in band and kpi_value <= band["max"]:
            return (float(band["mult"]),
                    f"{cfg['kpi']}={kpi_value} {band.get('label','')} ({band['mult']:.2f}x)")
    return None


def _compute_derived_kpis(profile_name: str, metrics: dict | None) -> dict:
    """Compute schema-defined derived KPIs.

    Walks SECTOR_KPI_FRAMEWORK[profile_name].derived_kpis and writes each
    computed value into the metrics dict. Returns the (in-place mutated)
    metrics dict for chaining. No-op when no derived_kpis defined or when
    operands missing/non-numeric.

    Schema format:
        "derived_kpis": [
            {"key": "<output_kpi_name>",
             "numerator": "<existing_kpi_name>",
             "denominator": "<existing_kpi_name>",
             "op": "divide" | "subtract" | "add"},   # default "divide"
        ],
    """
    if metrics is None:
        return {}
    spec = SECTOR_KPI_FRAMEWORK.get(profile_name) or {}
    derived = spec.get("derived_kpis") or []
    for cfg in derived:
        out_key = cfg.get("key")
        num_key = cfg.get("numerator")
        den_key = cfg.get("denominator")
        op      = cfg.get("op", "divide")
        if not out_key or not num_key or not den_key:
            continue
        if out_key in metrics and metrics[out_key] is not None:
            continue  # respect explicit value if already populated
        num = metrics.get(num_key)
        den = metrics.get(den_key)
        try:
            if num is None or den is None:
                continue
            num_f = float(num)
            den_f = float(den)
            if op == "divide":
                if den_f == 0:
                    continue
                metrics[out_key] = round(num_f / den_f, 6)
            elif op == "subtract":
                metrics[out_key] = round(num_f - den_f, 6)
            elif op == "add":
                metrics[out_key] = round(num_f + den_f, 6)
        except (TypeError, ValueError):
            continue
    return metrics


def _apply_risk_cap_when(ra: dict, metrics: dict, mult: float, note: str) -> tuple[float, str]:
    """Apply optional `cap_when` clause on a risk_adjustment block.

    Schema format:
        "cap_when": {
            "kpi":      "<gate_kpi>",
            "lt":       <threshold>,    # also supports "gt", "le", "ge"
            "max_mult": <ceiling>,
            "note":     "<reason>",
        },

    Returns (possibly capped multiplier, possibly augmented note). Used by
    Neo/Challenger to enforce "if net_income < 0 → risk capped at 1.00x"
    (the "you can't be a fortress with a hole in the bucket" rule).
    """
    cap = ra.get("cap_when")
    if not isinstance(cap, dict):
        return mult, note
    gate_kpi = cap.get("kpi")
    if not gate_kpi:
        return mult, note
    gate_value = metrics.get(gate_kpi) if isinstance(metrics, dict) else None
    if gate_value is None:
        return mult, note
    try:
        gv = float(gate_value)
    except (TypeError, ValueError):
        return mult, note
    triggered = False
    for op, label in (("lt", "<"), ("le", "<="), ("gt", ">"), ("ge", ">=")):
        if op in cap:
            try:
                threshold = float(cap[op])
            except (TypeError, ValueError):
                continue
            if op == "lt" and gv <  threshold: triggered = True
            if op == "le" and gv <= threshold: triggered = True
            if op == "gt" and gv >  threshold: triggered = True
            if op == "ge" and gv >= threshold: triggered = True
            if triggered:
                op_label = label
                break
    if not triggered:
        return mult, note
    max_mult = float(cap.get("max_mult", 1.0))
    if mult <= max_mult:
        return mult, note
    cap_reason = cap.get("note", "cap_when triggered")
    return (
        max_mult,
        f"{note} | CAPPED to {max_mult:.2f}x ({gate_kpi}={gv} {op_label} {threshold}: {cap_reason})",
    )


def _evaluate_gate(gate: dict, metrics: dict) -> tuple[bool, str]:
    """Evaluate a single gate (kpi + comparator + threshold). Returns
    (triggered, evidence_string). Used by both single-gate and multi-gate
    drag_when / cap_when forms."""
    kpi = gate.get("kpi")
    if not kpi:
        return False, ""
    val = metrics.get(kpi) if isinstance(metrics, dict) else None
    if val is None:
        return False, ""
    try:
        gv = float(val)
    except (TypeError, ValueError):
        return False, ""
    for op, label in (("lt", "<"), ("le", "<="), ("gt", ">"), ("ge", ">=")):
        if op in gate:
            try:
                threshold = float(gate[op])
            except (TypeError, ValueError):
                continue
            triggered = (
                (op == "lt" and gv <  threshold) or
                (op == "le" and gv <= threshold) or
                (op == "gt" and gv >  threshold) or
                (op == "ge" and gv >= threshold)
            )
            if triggered:
                return True, f"{kpi}={gv} {label} {threshold}"
            return False, ""
    return False, ""


def _apply_drag_when(spec_block: dict, metrics: dict, mult: float, note: str) -> tuple[float, str]:
    """Apply optional `drag_when` clause — multiplies mult by `factor` when
    the gate condition(s) are true. Sibling to cap_when but does a
    MULTIPLICATIVE drag rather than an upper cap.

    Schema formats (both supported):
      Single-gate (v3.7):
        "drag_when": {"kpi": "X", "lt": 0.05, "factor": 0.95, "note": "..."}

      Multi-gate AND (v3.8 — Innovation Trap pattern):
        "drag_when": {
            "gates": [
                {"kpi": "rd_intensity_pct",  "gt": 0.25},
                {"kpi": "revenue_growth_pct","lt": 0.05},
            ],
            "factor": 0.90,
            "note":   "Innovation Trap (CDMO ILMN/Grail lesson)",
        }
    """
    drag = spec_block.get("drag_when")
    if not isinstance(drag, dict):
        return mult, note

    # Multi-gate AND form
    if "gates" in drag and isinstance(drag["gates"], list):
        evidences: list[str] = []
        for gate in drag["gates"]:
            triggered, ev = _evaluate_gate(gate, metrics)
            if not triggered:
                return mult, note  # AND semantics — any miss aborts
            evidences.append(ev)
        factor = float(drag.get("factor", 1.0))
        if factor == 1.0:
            return mult, note
        new_mult = mult * factor
        drag_reason = drag.get("note", "multi-gate drag_when triggered")
        return (
            new_mult,
            f"{note} | DRAG x{factor:.2f} ({' AND '.join(evidences)}: {drag_reason})",
        )

    # Single-gate form (legacy v3.7)
    triggered, ev = _evaluate_gate(drag, metrics)
    if not triggered:
        return mult, note
    factor = float(drag.get("factor", 1.0))
    if factor == 1.0:
        return mult, note
    new_mult = mult * factor
    drag_reason = drag.get("note", "drag_when triggered")
    return (
        new_mult,
        f"{note} | DRAG x{factor:.2f} ({ev}: {drag_reason})",
    )


def _quality_multiplier(profile_name: str, sector: str, metrics: dict | None) -> tuple[float, str]:
    """Operational excellence — best-in-class margins/growth/retention.

    V3.1: Data-driven via SECTOR_KPI_FRAMEWORK[profile_name].quality_tiers.
    Falls back to legacy hardcoded sector branches if no schema present
    (preserves existing behavior during rollout transition).

    Returns (multiplier ∈ [0.70, 1.50], note).
    """
    m = metrics or {}
    spec = SECTOR_KPI_FRAMEWORK.get(profile_name) or {}
    qt = spec.get("quality_tiers")
    if qt:
        # ── Data-driven path ───────────────────────────────────────────────
        # V4-β: when peer cohort z-score is available for a band's KPI, use
        # the dynamic z-tier kicker INSTEAD of the static band lookup. The
        # band-based fallback covers KPIs whose cohort is too small (<3 peers)
        # or whose values are non-numeric.
        z_scores = m.get("_z_scores") if isinstance(m.get("_z_scores"), dict) else {}
        z_tier_lookup = _z_tier_kicker  # late-bound; see import at module top

        from collections import defaultdict
        # picks: list of (mult, note); weights: list of kpi_weight (or None)
        grouped: dict[str, list[tuple[float, str, float | None]]] = defaultdict(list)
        for cfg in qt.get("kpi_bands", []):
            kpi_name = cfg["kpi"]
            kpi_value = m.get(kpi_name)
            kpi_w = cfg.get("kpi_weight")  # v3.7 — weighted-geometric blend
            z_entry = z_scores.get(kpi_name) if z_tier_lookup else None
            if z_entry and isinstance(z_entry, dict) and "z" in z_entry:
                mult, label = z_tier_lookup(
                    z_entry["z"], direction=cfg.get("direction", "higher_better")
                )
                z_val = z_entry["z"]
                cohort_n = z_entry.get("cohort_size", 0)
                note = (
                    f"{kpi_name}={kpi_value} z={z_val:+.2f} "
                    f"(n={cohort_n}, {label}) {mult:.2f}x"
                )
                grouped[cfg.get("correlation_group", "_indep")].append((mult, note, kpi_w))
                continue
            result = _evaluate_band(kpi_value, cfg)
            if result is not None:
                grouped[cfg.get("correlation_group", "_indep")].append(
                    (result[0], result[1], kpi_w)
                )
        if not grouped:
            return (1.0, "no quality KPIs supplied")
        # v3.7: each group contributes either:
        #  - WEIGHTED-GEOMETRIC blend when ALL members have kpi_weight (e.g. F&B
        #    Vol 0.6w + Price 0.4w → composite = Vol^0.6 × Price^0.4), OR
        #  - MAX-DEVIATION pick (existing behavior — backward compatible)
        composite = 1.0
        notes: list[str] = []
        for group_id, picks in grouped.items():
            weights = [p[2] for p in picks]
            if picks and all(w is not None for w in weights):
                # Weighted-geometric — multiply each pick's mult^weight
                grp_mult = 1.0
                grp_notes: list[str] = []
                for mult_v, n, w in picks:
                    grp_mult *= mult_v ** float(w)
                    grp_notes.append(f"{n} ^{w:.2f}w")
                composite *= grp_mult
                tag = f"[{group_id}] " if group_id != "_indep" else ""
                notes.append(f"{tag}weighted: {' * '.join(grp_notes)} = {grp_mult:.3f}")
            else:
                # Max-deviation pick (existing behavior)
                best = max(picks, key=lambda x: abs(x[0] - 1.0))
                composite *= best[0]
                tag = f"[{group_id}] " if group_id != "_indep" else ""
                notes.append(f"{tag}{best[1]}")
        cap = qt.get("cap", [0.70, 1.50])
        composite = max(cap[0], min(cap[1], composite))
        # v3.4: cap_when on quality_tiers — symmetric to risk_adjustment.cap_when.
        # Used by Growth SaaS (magic_number < 0.4 caps quality at 1.00x —
        # the "burn-and-pray" override even when NRR / Rule of 40 are elite).
        composite, q_note = _apply_risk_cap_when(qt, m, composite, " * ".join(notes))
        # v3.7: drag_when on quality_tiers (multiplicative drag, opt-in)
        composite, q_note = _apply_drag_when(qt, m, composite, q_note)
        return (round(composite, 3), q_note)

    # ── Legacy hardcoded fallback (preserves existing tests) ────────────────
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
            if   target_roe > 0.16: bank_signals.append((1.30, f"ROE_tgt={target_roe*100:.0f}% premium +30%"))
            elif target_roe > 0.13: bank_signals.append((1.15, f"ROE_tgt={target_roe*100:.0f}% above-avg +15%"))
        if bank_signals:
            pick = max(bank_signals, key=lambda x: abs(x[0] - 1.0))
            multipliers.append(pick[0]); notes.append(f"[corr] {pick[1]}")

    # Mining — cost curve quartile (operational signal)
    quartile = m.get("cost_curve_quartile")
    if quartile is not None:
        q = int(quartile)
        if   q == 1: multipliers.append(1.30); notes.append("Q1 cost producer +30%")
        elif q == 2: multipliers.append(1.30); notes.append("Q2 cost producer +30%")
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
        multipliers.append(pick[0]); notes.append(f"[corr] {pick[1]}")

    if not multipliers:
        return (1.0, "no operational quality KPIs")
    composite = 1.0
    for x in multipliers: composite *= x
    composite = max(0.70, min(1.50, composite))
    return (round(composite, 3), " * ".join(notes))


def _risk_multiplier(profile_name: str, sector: str, metrics: dict | None) -> tuple[float, str]:
    """Balance sheet strength — Beta haircut / discount rate compression.

    The 'JPM Capital Drag' fix: high CET1 isn't a drag, it's a stabilizer.
    V3.1: Data-driven via SECTOR_KPI_FRAMEWORK[profile_name].risk_adjustment.
    Falls back to hardcoded sector branches if no schema present.

    Returns (multiplier ∈ [0.70, 1.20], note).
    """
    m = metrics or {}
    spec = SECTOR_KPI_FRAMEWORK.get(profile_name) or {}
    ra = spec.get("risk_adjustment")
    if ra:
        kpi_name = ra["kpi"]
        kpi_value = m.get(kpi_name)
        # V4-β: peer-cohort z-tier kicker takes precedence over static band
        z_scores = m.get("_z_scores") if isinstance(m.get("_z_scores"), dict) else {}
        z_entry = z_scores.get(kpi_name) if _z_tier_kicker else None
        if z_entry and isinstance(z_entry, dict) and "z" in z_entry:
            mult, label = _z_tier_kicker(
                z_entry["z"], direction=ra.get("direction", "higher_better")
            )
            mult = max(0.70, min(1.20, mult))  # risk cap is tighter than quality
            note = (
                f"{kpi_name}={kpi_value} z={z_entry['z']:+.2f} "
                f"(n={z_entry.get('cohort_size', 0)}, {label}) {mult:.2f}x"
            )
            mult, note = _apply_risk_cap_when(ra, m, mult, note)
            mult, note = _apply_drag_when(ra, m, mult, note)
            return (round(mult, 3), note)
        result = _evaluate_band(kpi_value, ra)
        if result is not None:
            mult = max(0.70, min(1.20, result[0]))
            note = result[1]
            mult, note = _apply_risk_cap_when(ra, m, mult, note)
            mult, note = _apply_drag_when(ra, m, mult, note)
            return (round(mult, 3), note)
        return (1.0, f"{ra['kpi']} not extracted")
    # ── Legacy hardcoded fallback ────────────────────────────────────────────
    if "Insurance" in profile_name:
        scr = m.get("solvency_ratio_scr")
        if scr is not None:
            if   scr > 2.0: return (1.10, f"SCR={scr:.2f}x strong +10%")
            elif scr < 1.3: return (0.90, f"SCR={scr:.2f}x weak -10%")
    if "Bank" in profile_name:
        cet1 = m.get("cet1_ratio")
        if cet1 is not None:
            if   cet1 > 0.14: return (1.15, f"CET1={cet1*100:.1f}% fortress +15%")
            elif cet1 > 0.12: return (1.10, f"CET1={cet1*100:.1f}% strong +10%")
            elif cet1 < 0.085: return (0.85, f"CET1={cet1*100:.1f}% weak -15%")
    if "Mining" in profile_name:
        nd = m.get("net_debt_to_ebitda")
        if nd is not None:
            if   nd < 0.5: return (1.10, f"ND/EBITDA={nd:.2f}x fortress +10%")
            elif nd > 2.5: return (0.85, f"ND/EBITDA={nd:.2f}x weak -15%")
    if "Biotech" in profile_name or "Pre-approval" in profile_name:
        runway = m.get("cash_runway_quarters")
        if runway is not None:
            if   runway > 12: return (1.15, f"Runway={runway}q strong +15%")
            elif runway < 4:  return (0.70, f"Runway={runway}q dilution risk -30%")
    return (1.0, "no balance sheet KPIs")


def _commodity_multiplier(profile_name: str, sector: str, metrics: dict | None) -> tuple[float, str]:
    """Commodity terminal-value uplift — only fires for commodity sectors.
    Returns (multiplier ∈ [1.00, 1.40], note).

    V4-α: Schema-aware KPI lookup. Reads commodity_uplift slot from
    SECTOR_KPI_FRAMEWORK[profile] to find the right spot/realised/cost KPI
    names per profile (e.g. Upstream O&G uses spot_brent_price + lifting_cost,
    Mining uses spot_commodity_price + aisc_per_oz).

    Also: when realised or cost KPIs are missing but spot is available,
    derive sensible proxies (realised = spot × 0.90, cost = breakeven × 0.50)
    so commodity_uplift doesn't silently fail when the extractor only catches
    spot price.
    """
    m = metrics or {}
    if sector not in _COMMODITY_SECTORS:
        return (1.0, "n/a (non-commodity sector)")

    # Read profile-specific KPI names from schema
    spec = SECTOR_KPI_FRAMEWORK.get(profile_name, {})
    cu_cfg = spec.get("commodity_uplift", {})
    spot_key     = cu_cfg.get("spot_kpi", "spot_commodity_price")
    realised_key = cu_cfg.get("realised_kpi", "realised_price_per_unit")
    cost_key     = cu_cfg.get("cost_kpi", "aisc_per_oz")
    max_uplift   = cu_cfg.get("max_uplift", 1.40)

    spot     = m.get(spot_key)
    realised = m.get(realised_key)
    cost     = m.get(cost_key)

    # Fall-back proxies when extractor missed realised or cost KPIs
    if spot and not realised:
        realised = spot * 0.90  # typical oil/gold realization vs spot
    if spot and not cost:
        # Use breakeven_oil_price as cost proxy for E&P (50% conservative)
        bep = m.get("breakeven_oil_price_usd")
        if bep:
            cost = bep * 0.50

    if not (spot and realised and cost):
        return (1.0, f"no commodity price KPIs ({spot_key}={spot}, {realised_key}={realised}, {cost_key}={cost})")
    hist_margin = realised - cost
    if hist_margin <= 0:
        return (1.0, f"negative historical margin (realised={realised:.0f}, cost={cost:.0f})")
    blended = spot * 0.33 + realised * 0.67
    fwd_margin = blended - cost
    leverage = fwd_margin / hist_margin
    uplift = max(1.0, min(max_uplift, 1.0 + (leverage - 1.0) * 0.5))
    return (round(uplift, 3),
            f"spot={spot:.0f}/realised={realised:.0f}/cost={cost:.0f} -> {uplift:.2f}x")


# V3.2 FMP fallback for balance-sheet risk KPIs.
#
# Most extractor outputs lack `net_debt_to_ebitda` / `cash_runway_years` because
# these are balance-sheet derived and rarely quoted verbatim in deep research
# narrative. FMP is the authoritative source. We fetch lazily at composite_
# adjustment time and cache per-ticker per-process to avoid repeated calls
# (one fmp call set per ticker, not per render).

_FMP_RISK_CACHE: dict[str, dict] = {}


def _fmp_risk_kpis(ticker: str) -> dict:
    """Returns dict of FMP-derived KPIs for the ticker (cached).

    v3.2: broadened beyond risk-only fields to cover the universal mandatory
    KPIs used by the Hyperscaler / Tech Conglomerate, Traditional Retail, and
    REIT schemas. These fields are FMP-derivable so the schema can mark them
    `mandatory=True` without forcing the LLM extractor to re-derive what FMP
    already computes deterministically.

    Risk fields (consumed by _risk_multiplier):
      - net_debt_to_ebitda  ← key-metrics-ttm.netDebtToEBITDATTM
      - debt_to_ebitda      ← alias of net_debt_to_ebitda (Utilities schema)
      - cash_runway_years   ← cash_and_st_inv / |FCF| if FCF<0, else 99.0
      - leverage_ratio      ← key-metrics-ttm.debtToAssetsTTM (REIT V3 risk)

    Quality fields (consumed by _quality_multiplier):
      - operating_margin_pct ← ratios-ttm.operatingProfitMarginTTM
      - revenue_growth_pct   ← financial-growth.revenueGrowth (1 fy)
      - capex_intensity_pct  ← key-metrics-ttm.capexToRevenueTTM
      - fcf_margin_pct       ← key-metrics-ttm.freeCashFlowMarginTTM (when present)

    Returns {} on FMP failure (caller treats as "no FMP fallback available").
    """
    if ticker in _FMP_RISK_CACHE:
        return _FMP_RISK_CACHE[ticker]
    out: dict = {}
    try:
        import os
        import urllib.request
        import urllib.parse
        key = os.environ.get("FMP_API_KEY") or "UFPUuQjTht66l2GmJhQbUZzij7IfJbsx"
        base = "https://financialmodelingprep.com/stable"

        def _get(path: str) -> Any:
            url = f"{base}/{path}?symbol={urllib.parse.quote(ticker)}&apikey={key}"
            req = urllib.request.Request(url, headers={"User-Agent": "framework-fmp/1.0"})
            with urllib.request.urlopen(req, timeout=8) as r:
                return json.loads(r.read())

        keymet  = (_get("key-metrics-ttm") or [{}])[0]
        ratios  = (_get("ratios-ttm") or [{}])[0]
        bs      = (_get("balance-sheet-statement") or [{}])[0]
        cfs     = (_get("cash-flow-statement") or [{}])[0]
        finGrow = (_get("financial-growth") or [{}])[0]

        # ── Risk fields ────────────────────────────────────────────────────
        nde = keymet.get("netDebtToEBITDATTM")
        if nde is not None:
            out["net_debt_to_ebitda"] = nde
            out["debt_to_ebitda"]     = nde

        # leverage_ratio for REIT (debt-to-assets is the universal proxy).
        # FMP ratios-ttm field is `debtToAssetsRatioTTM`; key-metrics-ttm
        # uses `debtToAssetsTTM` on some accounts. Try both.
        d2a = (
            ratios.get("debtToAssetsRatioTTM")
            or ratios.get("debtToAssetsTTM")
            or keymet.get("debtToAssetsTTM")
        )
        if d2a is not None and 0 <= float(d2a) < 1.0:
            out["leverage_ratio"] = round(float(d2a), 4)

        cash_st = bs.get("cashAndShortTermInvestments")
        fcf     = cfs.get("freeCashFlow")
        if cash_st is not None and fcf is not None:
            if fcf < 0:
                out["cash_runway_years"] = round(cash_st / abs(fcf), 2)
            else:
                out["cash_runway_years"] = 99.0

        # ── Quality fields (Hyperscaler/Tech Conglomerate, etc.) ───────────
        # Source: /stable/ratios-ttm (per user-confirmed schema). Field names
        # have the `RatioTTM` suffix on this endpoint vs the `TTM` suffix on
        # key-metrics-ttm — try both.
        op_m = (
            ratios.get("operatingProfitMarginTTM")
            or keymet.get("operatingProfitMarginTTM")
        )
        if op_m is not None:
            out["operating_margin_pct"] = round(float(op_m), 4)

        # Revenue growth — financial-growth endpoint, latest annual entry.
        rev_g = finGrow.get("revenueGrowth")
        if rev_g is not None:
            out["revenue_growth_pct"] = round(float(rev_g), 4)

        # Capex intensity — derive from capexPerShareTTM / revenuePerShareTTM
        # (ratios-ttm doesn't expose a direct capexToRevenue field).
        cps = ratios.get("capexPerShareTTM")
        rps = ratios.get("revenuePerShareTTM")
        if cps is not None and rps is not None and float(rps) > 0:
            out["capex_intensity_pct"] = round(abs(float(cps) / float(rps)), 4)

        # v3.4: gross_margin_pct (used by Early Platform + High-Growth Tech / AI)
        gm = ratios.get("grossProfitMarginTTM")
        if gm is not None:
            out["gross_margin_pct"] = round(float(gm), 4)

        # FCF margin — derive from FCF-per-share / revenue-per-share if needed
        fcf_ps = ratios.get("freeCashFlowPerShareTTM")
        if fcf_ps is not None and rps is not None and float(rps) > 0:
            out["fcf_margin_pct"] = round(float(fcf_ps) / float(rps), 4)

    except Exception:
        pass  # Fail silently — composite_adjustment will return 1.0x for missing fields

    _FMP_RISK_CACHE[ticker] = out
    return out


def _augment_metrics_with_fmp_risk(ticker: str, metrics: dict | None) -> dict:
    """Merge FMP-derived risk KPIs into the extractor metrics dict.

    Extractor wins where it has an explicit value — FMP only fills gaps.
    Returns a NEW dict (doesn't mutate the input).
    """
    out = dict(metrics or {})
    fmp_risk = _fmp_risk_kpis(ticker)
    for k, v in fmp_risk.items():
        if k not in out or out[k] is None:
            out[k] = v
    return out


# ── V3.1: FMP commodity-price augmentation ──────────────────────────────────
# Resources/Energy/Materials profiles need spot commodity prices for the
# commodity_uplift multiplier. The extractor often misses these (they're
# market data, not company-disclosed) — FMP /stable/quote provides them.
_FMP_COMMODITY_CACHE: dict[str, dict] = {}

# Map per-profile commodity → FMP symbol + KPI name
_PROFILE_COMMODITY_MAP: dict[str, list[tuple[str, str]]] = {
    "Upstream Oil & Gas": [("BZUSD", "spot_brent_price")],
    "Mining (Major)":     [("GCUSD", "spot_commodity_price")],
    "Mining (Junior)":    [("GCUSD", "spot_commodity_price")],
    "Refining":           [("BZUSD", "spot_brent_price")],
    # Steel/Materials (no FMP commodity for hot-rolled coil — skip)
}


def _fmp_commodity_price(symbol: str) -> float | None:
    """Fetch latest commodity spot price from FMP. Cached per-process."""
    if symbol in _FMP_COMMODITY_CACHE:
        return _FMP_COMMODITY_CACHE[symbol].get("price")
    try:
        import os, urllib.request, urllib.parse
        key = os.environ.get("FMP_API_KEY") or "UFPUuQjTht66l2GmJhQbUZzij7IfJbsx"
        url = f"https://financialmodelingprep.com/stable/quote?symbol={urllib.parse.quote(symbol)}&apikey={key}"
        req = urllib.request.Request(url, headers={"User-Agent": "framework-fmp/1.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
        if data:
            _FMP_COMMODITY_CACHE[symbol] = data[0]
            return data[0].get("price")
    except Exception:
        pass
    return None


def _augment_metrics_with_fmp_commodity(profile_name: str, metrics: dict | None) -> dict:
    """Merge FMP-derived commodity prices into metrics for commodity sectors.
    Same gap-fill semantics: extractor wins, FMP only fills gaps."""
    out = dict(metrics or {})
    pricing = _PROFILE_COMMODITY_MAP.get(profile_name, [])
    for fmp_symbol, kpi_key in pricing:
        if kpi_key in out and out[kpi_key] is not None:
            continue  # extractor caught it
        spot = _fmp_commodity_price(fmp_symbol)
        if spot is not None:
            out[kpi_key] = spot
    return out


# ── V4-α: Cross-profile multiplier weights ──────────────────────────────────
# Different sectors prioritize different valuation levers:
#   Tech / Biopharma → Quality dominant (growth >> safety)
#   Banks / Utilities → Risk dominant (capital safety = valuation floor)
#   Energy / Materials / Mining → Commodity dominant (spot price >> ops excellence)
#
# Weights expressed as (quality, risk, commodity) tuples summing to 1.0.
# Geometric weighted mean: composite = q^(3*wq) * r^(3*wr) * c^(3*wc)
# (× 3 normalizes so equal weights (1/3, 1/3, 1/3) give the original q×r×c).

_PROFILE_WEIGHTS: dict[str, tuple[float, float, float]] = {
    # ── Default (when profile not listed) ──────────────────────────────
    "default":              (0.40, 0.40, 0.20),  # Q+R balanced, Commodity rare

    # ── Risk-dominant: Banks / Utilities / regulated ───────────────────
    "Money Center Bank":      (0.20, 0.60, 0.20),
    "Money Center Bank (EU)": (0.20, 0.60, 0.20),
    "Regional Bank":          (0.20, 0.60, 0.20),
    "Super-Regional Bank":    (0.20, 0.60, 0.20),
    "Investment Bank":        (0.30, 0.50, 0.20),
    "Bank / Lending Institution": (0.20, 0.60, 0.20),
    "EM Bank":                (0.20, 0.60, 0.20),
    "EM Bank (Premium)":      (0.20, 0.60, 0.20),
    "Insurance":              (0.30, 0.50, 0.20),
    "Mortgage/GSE":           (0.10, 0.70, 0.20),
    "Regulated Utility":      (0.20, 0.60, 0.20),
    "IPP":                    (0.30, 0.50, 0.20),

    # ── Quality-dominant: Tech / Biopharma / Pharma ────────────────────
    "Large Cap Pharma":               (0.70, 0.30, 0.00),
    "Pre-approval Biotech":           (0.60, 0.40, 0.00),
    "MedTech / Devices":              (0.60, 0.40, 0.00),
    "CDMO / Life Science Tools":      (0.60, 0.40, 0.00),
    "High-Growth Tech / AI":          (0.70, 0.30, 0.00),
    "Cybersecurity / Mission-Critical SaaS": (0.70, 0.30, 0.00),
    "Hyper-Growth Platform":          (0.70, 0.30, 0.00),
    "Mature Platform":                (0.55, 0.40, 0.05),
    "Early Platform":                 (0.55, 0.45, 0.00),
    # v3.4: Growth SaaS + Mature SaaS migrated from legacy. SaaS-pure economics
    # → quality-dominant; risk lever is sales efficiency / leverage, not commodity.
    "Growth SaaS":                    (0.65, 0.35, 0.00),  # quality-heavy (NRR + R40 drive)
    "Mature SaaS":                    (0.55, 0.45, 0.00),  # more balanced (FCF + leverage matter)
    # AAPL/MSFT/AMZN/GOOGL/META — quality-dominant (cloud growth + margin) but
    # risk weight elevated vs pure SaaS because AI capex digestion is real.
    "Hyperscaler / Tech Conglomerate": (0.60, 0.35, 0.05),

    # ── Commodity-dominant: Mining / Energy / Materials ────────────────
    "Mining (Major)":         (0.10, 0.10, 0.80),
    "Mining (Junior)":        (0.10, 0.10, 0.80),
    "Upstream Oil & Gas":     (0.10, 0.10, 0.80),
    "Refining":               (0.10, 0.10, 0.80),
    "Steel / Metals":         (0.20, 0.20, 0.60),
    "Specialty Chemicals":    (0.40, 0.30, 0.30),
    "Merchant Power":         (0.20, 0.20, 0.60),

    # ── Quality + Risk balanced (specialty) ────────────────────────────
    "Fabless":                (0.60, 0.30, 0.10),  # quality dominant; cycle matters
    "IDM / Foundry":          (0.50, 0.40, 0.10),
    "Equipment / EDA":        (0.60, 0.40, 0.00),
    "OSAT / Packaging":       (0.50, 0.50, 0.00),
    "Aerospace & Defense":    (0.40, 0.50, 0.10),  # backlog visibility + balance sheet
    "Capital Goods":          (0.40, 0.50, 0.10),
    "Automotive (OEM)":       (0.50, 0.40, 0.10),
    "Automotive & EV":        (0.55, 0.40, 0.05),

    # ── Quality + brand pricing (Consumer) ─────────────────────────────
    "Travel & Dining":        (0.50, 0.40, 0.10),
    "Luxury Goods":           (0.55, 0.40, 0.05),
    "Consumer Growth":        (0.60, 0.40, 0.00),
    "Food & Beverage":        (0.45, 0.50, 0.05),
    "Household / Personal":   (0.45, 0.50, 0.05),
    "Membership / Subscription Retail": (0.55, 0.40, 0.05),
    "Traditional Retail":     (0.45, 0.50, 0.05),
    "Apparel / Athletic Wear": (0.55, 0.40, 0.05),
    "Consumer Durables":      (0.40, 0.50, 0.10),

    # ── REIT (legacy bespoke panel — but weight present for completeness
    #    if a future migration moves REIT off the legacy path) ──────────
    "REIT":                   (0.40, 0.50, 0.10),  # FFO quality + leverage risk

    # ── Telco / Healthcare / Other ─────────────────────────────────────
    "Stable Growth":          (0.30, 0.60, 0.10),  # Telco — risk-tilted
    "Managed Care":           (0.40, 0.50, 0.10),
    "Asset Manager":          (0.40, 0.50, 0.10),
    "Alt Asset Manager":      (0.50, 0.40, 0.10),
    "Brokerage":              (0.30, 0.60, 0.10),
    "Holding Company":        (0.40, 0.50, 0.10),
    "Market Infrastructure":  (0.30, 0.60, 0.10),
    "Payment Networks":       (0.55, 0.40, 0.05),
    "FinTech":                (0.60, 0.40, 0.00),
    "Neo/Challenger":         (0.50, 0.50, 0.00),
    "Pre-Revenue Tech":       (0.60, 0.40, 0.00),  # Network/L1/L2 — quality-dominant
    # v3.10 — Crypto sub-profiles (multimodal Family G):
    "Crypto Exchange":        (0.70, 0.30, 0.00),  # COIN — quality dominant (volume + AUM are everything)
    "BTC Treasury / Proxy":   (0.55, 0.45, 0.00),  # MSTR — balanced; LTV gate matters
    "Digital Asset Mining":   (0.50, 0.50, 0.00),  # MARA/RIOT/CIFR — survival = quality, runway = risk

    # ── Energy infrastructure (NOT commodity-exposed) ───────────────────
    "EPC Contractor":         (0.40, 0.50, 0.10),  # service biz on fixed-price contracts
    "Energy Tech Licensor":   (0.55, 0.40, 0.05),  # asset-light royalty/IP model

    # ── ProfessionalServices ────────────────────────────────────────────
    "Ad / Consulting":        (0.55, 0.40, 0.05),  # talent + brand franchise
    "IT Services":            (0.60, 0.35, 0.05),  # quality-dominant: utilization/attrition
    "Payment Processors":     (0.55, 0.40, 0.05),  # TPV + take rate × scale

    # ── Tech (debt-burdened) ────────────────────────────────────────────
    "Levered Subscription":   (0.50, 0.45, 0.05),  # NFLX-like — debt service is existential

    # ── Transportation (capital-intensive cyclicals) ────────────────────
    "Airlines":               (0.30, 0.60, 0.10),  # MOST risk-dominant — bankruptcy history
    "Rail / Logistics":       (0.40, 0.50, 0.10),  # oligopoly stability vs Airlines
}


def _profile_weights(profile_name: str) -> tuple[float, float, float]:
    """Returns (q_weight, r_weight, c_weight) for the profile. Defaults to balanced."""
    return _PROFILE_WEIGHTS.get(profile_name, _PROFILE_WEIGHTS["default"])


def composite_adjustment(profile_name: str, sector: str, metrics: dict | None) -> tuple[float, dict]:
    """V4-α aggregator — multiplicative stacking with PER-PROFILE WEIGHTS,
    sector-aware cap, and full audit bridge.

    Math: composite = q^(3*wq) × r^(3*wr) × c^(3*wc)
    (× 3 normalizes so equal weights (1/3, 1/3, 1/3) reproduce the V3 q*r*c)

    A bank with weights (0.2, 0.6, 0.2) sees Risk's effect AMPLIFIED 1.8×
    (3 × 0.6) and Quality's effect DAMPED to 0.6× (3 × 0.2). So a bank with
    Q=1.30, R=1.15, C=1.00 produces:
      composite = 1.30^0.6 × 1.15^1.8 × 1.00^0.6 = 1.173 × 1.290 × 1.0 = 1.513
    vs equal weights: 1.30 × 1.15 × 1.00 = 1.495 (negligible change for this case)

    But for Tech (0.7, 0.3, 0.0) with Q=1.50, R=1.10, C=1.00:
      composite = 1.50^2.1 × 1.10^0.9 × 1.00^0.0 = 2.347 × 1.090 × 1.0 = 2.558
    vs equal weights: 1.50 × 1.10 × 1.00 = 1.650 — Quality REALLY drives Tech.

    Returns (final_multiplier, bridge_dict).
    """
    # v3.3: pre-compute derived KPIs (e.g. NNA Capture = NNA/AUM for Brokerage)
    # so band evaluation sees them. Idempotent — won't overwrite if already set.
    if metrics is not None:
        _compute_derived_kpis(profile_name, metrics)
    q, q_note = _quality_multiplier(profile_name, sector, metrics)
    r, r_note = _risk_multiplier(profile_name, sector, metrics)
    c, c_note = _commodity_multiplier(profile_name, sector, metrics)

    wq, wr, wc = _profile_weights(profile_name)
    # Geometric weighted mean (× 3 normalization keeps backward compat at equal weights)
    raw = (q ** (3 * wq)) * (r ** (3 * wr)) * (c ** (3 * wc))
    cap_high = 1.70 if sector in _COMMODITY_SECTORS else 1.85
    capped = max(0.50, min(cap_high, raw))

    # V4-β audit: surface z-score evidence for the dominant Quality + Risk KPIs.
    # Frontend renders z + cohort_size as a small chip under each lever so the
    # user can see whether the multiplier was z-driven (peer-relative) or
    # band-driven (static thresholds).
    spec = SECTOR_KPI_FRAMEWORK.get(profile_name) or {}
    m = metrics or {}
    z_scores = m.get("_z_scores") if isinstance(m.get("_z_scores"), dict) else {}
    quality_kpi = (spec.get("quality_tiers", {}).get("kpi_bands") or [{}])[0].get("kpi") if spec.get("quality_tiers") else None
    risk_kpi    = (spec.get("risk_adjustment") or {}).get("kpi")
    quality_z_entry = z_scores.get(quality_kpi) if quality_kpi else None
    risk_z_entry    = z_scores.get(risk_kpi)    if risk_kpi    else None

    # ── v3.6 P2: extraction-coverage counters per lever ──────────────────
    # Counts how many tier KPIs (referenced by the schema's quality_tiers
    # / risk_adjustment) have non-None values in the metrics dict. Lets
    # the frontend surface "low-confidence multiplier" badges when only
    # some of the levers actually fired.
    qt_for_count = spec.get("quality_tiers") or {}
    quality_total = len(qt_for_count.get("kpi_bands", []))
    quality_extracted = sum(
        1 for cfg in qt_for_count.get("kpi_bands", [])
        if m.get(cfg.get("kpi")) is not None
    )
    risk_total     = 1 if risk_kpi else 0
    risk_extracted = 1 if (risk_kpi and m.get(risk_kpi) is not None) else 0
    # cap_when gate KPI counts as risk-extracted dependency
    cap_when_ra = (spec.get("risk_adjustment") or {}).get("cap_when") or {}
    cap_gate_kpi = cap_when_ra.get("kpi") if isinstance(cap_when_ra, dict) else None

    # ── v3.6 P1: completeness signal from extract_via_framework ───────────
    # _completeness_score = ratio of mandatory KPIs the extractor populated
    # _mandatory_missing  = list of mandatory KPI names that came back null
    completeness_score = m.get("_completeness_score")
    mandatory_missing  = m.get("_mandatory_missing") or []

    return capped, {
        "quality":            round(q, 3),
        "quality_note":       q_note,
        "quality_weight":     round(wq, 2),
        "quality_z":          quality_z_entry.get("z")           if isinstance(quality_z_entry, dict) else None,
        "quality_cohort":     quality_z_entry.get("cohort_size") if isinstance(quality_z_entry, dict) else None,
        "quality_extracted":  quality_extracted,
        "quality_total":      quality_total,
        "risk":               round(r, 3),
        "risk_note":          r_note,
        "risk_weight":        round(wr, 2),
        "risk_z":             risk_z_entry.get("z")              if isinstance(risk_z_entry, dict) else None,
        "risk_cohort":        risk_z_entry.get("cohort_size")    if isinstance(risk_z_entry, dict) else None,
        "risk_extracted":     risk_extracted,
        "risk_total":         risk_total,
        "risk_cap_gate_kpi":  cap_gate_kpi,
        "commodity":          round(c, 3),
        "commodity_note":     c_note,
        "commodity_weight":   round(wc, 2),
        "raw_composite":      round(raw, 3),
        "final_multiplier":   round(capped, 3),
        "cap_high":           cap_high,
        "was_capped":         raw != capped,
        # P1: completeness from the framework extractor — surfaces "fallback used"
        # (mandatory KPI missing) so frontend can show low-confidence badge.
        "completeness_score": completeness_score,
        "mandatory_missing":  mandatory_missing,
    }


# Legacy sub-profiles already render bespoke cards (per separate KPI panels
# in the existing frontend). Do NOT generate a generic sector_card for these
# — the existing UI is purpose-built and the user has explicitly held them.
_LEGACY_PROFILES: frozenset[str] = frozenset({
    # v3.4 — Growth SaaS + Mature SaaS migrated off legacy:
    # they now have V3 quality_tiers + risk_adjustment + V4 weights and use
    # the generic SectorValuationCard. The bespoke TechValuationPanel can
    # coexist as a richer alternate UI for SaaS — no removal, just no longer
    # the only path.
    "Hyperscaler",
    "REIT",
    "Pipeline (Pre-revenue Biotech)",
    "Pre-approval Biotech",
    "Pre-Revenue Biotech",
})


def is_legacy_profile(profile_name: str | None) -> bool:
    """True when the sub-profile already has a bespoke frontend card and
    should NOT receive the generic sector_card render."""
    return bool(profile_name) and profile_name in _LEGACY_PROFILES


# Heuristic format inference for KPI values. Keyed on substrings in `key` —
# the framework spec doesn't store an explicit format so we derive it.
def _infer_kpi_format(kpi: dict) -> str:
    if kpi.get("decimal_format"):
        return "pct"
    key = kpi.get("key", "").lower()
    label = (kpi.get("compute_hint") or "").lower()
    blob = key + " " + label
    if any(t in key for t in ("_pct", "ratio", "_rate", "yield", "margin", "_yoy")):
        return "pct"
    if any(t in key for t in ("per_share", "per_oz", "per_unit", "price", "_aisc", "value")):
        return "usd"
    if any(t in key for t in ("_x", "coverage", "leverage", "multiple", "turnover")):
        return "x"
    if any(t in key for t in ("count", "quartile", "weeks", "years", "_qty")):
        return "int"
    if "$" in label:
        return "usd"
    if "%" in label:
        return "pct"
    return "string"


# Heuristic auto-grouping into themed sections. Each KPI is assigned to one
# of four buckets based on its key/label semantics. Frontend renders each
# group with the Option B accent color.
def _classify_kpi_group(kpi: dict) -> tuple[str, str]:
    """Return (group_title, accent) for the given KPI."""
    key = kpi.get("key", "").lower()
    label = (kpi.get("compute_hint") or "").lower()
    blob = key + " " + label
    # Capital / balance-sheet strength
    if any(t in blob for t in (
        "tier", "cet1", "scr", "rbc", "solvency", "capital", "leverage",
        "book", "tangible", "tbv", "embedded_value",
    )):
        return ("Capital", "green")
    # Risk / loss / quality
    if any(t in blob for t in (
        "loss", "reserve", "cat ", "catastrophe", "default", "npl",
        "churn", "dilution", "credit", "delinquen",
    )):
        return ("Risk & Reserves", "rose")
    # Profitability / margins / returns / yield
    if any(t in blob for t in (
        "margin", "roe", "roa", "rotce", "yield", "ratio",
        "nim", "efficiency", "spread", "profit",
    )):
        return ("Profitability", "blue")
    # Growth / pipeline / forward
    if any(t in blob for t in (
        "growth", "yoy", "_qoq", "pipeline", "design_win", "backlog",
        "lead_time", "production",
    )):
        return ("Growth & Pipeline", "violet")
    # Catch-all
    return ("Operations", "amber")


# Map ticker-keyed state metric dicts to their canonical name. The framework
# dispatch writes per-profile metrics under different state keys; render_card
# reads from all of them and merges so any present extractor wins.
_METRIC_STATE_KEYS: tuple[str, ...] = (
    "framework_metrics_all",
    "insurance_metrics_all",
    "bank_metrics_all",
    # legacy keys — read for completeness when render_card_payload is called
    # for a legacy profile during a transition window (caller normally gates
    # on is_legacy_profile() and skips):
    "saas_metrics_all",
    "reit_metrics_all",
    "pipeline_assets_all",
)


def _collect_kpi_values(state: dict, ticker: str) -> dict[str, Any]:
    """Walk all metric state-dicts and collect the per-ticker KPI values
    into a single flat dict {kpi_key: value}. Later writers win, but the
    framework dispatch writes uniquely so collisions are rare."""
    if not isinstance(state, dict):
        return {}
    data = state.get("data") if "data" in state else state
    if not isinstance(data, dict):
        return {}
    merged: dict[str, Any] = {}
    for state_key in _METRIC_STATE_KEYS:
        bucket = data.get(state_key)
        if not isinstance(bucket, dict):
            continue
        ticker_bucket = bucket.get(ticker)
        if not isinstance(ticker_bucket, dict):
            continue
        for k, v in ticker_bucket.items():
            # Skip framework metadata (_completeness_score, _mandatory_missing)
            if isinstance(k, str) and k.startswith("_"):
                continue
            merged[k] = v
    return merged


def _kpi_label(kpi: dict) -> str:
    """Human-readable label for the KPI card. Prefers a short noun-phrase
    derived from the key over the long compute_hint."""
    key = kpi.get("key", "")
    # Snake-case → Title Case, with a few common abbreviation fixes
    label = key.replace("_", " ").title()
    label = (label
        .replace(" Pct", " %")
        .replace(" Tbv", " TBV")
        .replace("Cet1", "CET1")
        .replace(" Roe", " ROE")
        .replace(" Roa", " ROA")
        .replace("Nim", "NIM")
        .replace("Aisc", "AISC")
        .replace("Scr", "SCR")
        .replace("Rbc", "RBC")
        .replace("Pyd", "PYD")
        .replace("Npl", "NPL")
    )
    return label


def render_card_payload(
    profile_name: str,
    state: dict,
    ticker: str,
    sub_sub: str = "",
) -> dict | None:
    """Build the JSON payload for the frontend sector valuation card.

    Returns ``None`` when:
      - profile_name is empty / not in the framework
      - profile_name is a legacy sub-profile (frontend uses its bespoke card)

    Shape (mirrors `SectorValuationCardDataB` in TS):

        {
          "ticker": str,
          "sector": str,
          "profile_name": str,
          "sub_profile": str | None,
          "anchor_methods": list[str],
          "groups": [
            {
              "title": str,
              "accent": "blue" | "green" | "amber" | "rose" | "violet",
              "kpis": [
                {
                  "key": str,
                  "label": str,
                  "value": float | str | None,
                  "format": "pct" | "usd" | "x" | "int" | "string",
                  "decimals": int | None,
                  "unit": str | None,
                  "mandatory": bool,
                  "clamp_low": float | None,
                  "clamp_high": float | None,
                },
                ...
              ]
            },
            ...
          ],
          "source_priority": list[str],
        }
    """
    if not profile_name or is_legacy_profile(profile_name):
        return None

    spec = SECTOR_KPI_FRAMEWORK.get(profile_name)
    if not spec:
        return None

    # Read all extracted KPI values for this ticker
    values = _collect_kpi_values(state, ticker)

    # Filter KPIs to those applicable to the sub_sub_profile (if specified)
    kpis = list(spec.get("kpis", []))
    if sub_sub:
        kpis = [
            k for k in kpis
            if not k.get("applies_to") or sub_sub in k["applies_to"]
        ]

    # Bucket KPIs into themed groups (preserve original order within each group)
    buckets: dict[str, dict] = {}
    for kpi in kpis:
        title, accent = _classify_kpi_group(kpi)
        buckets.setdefault(title, {"title": title, "accent": accent, "kpis": []})
        clamp = kpi.get("clamp")
        if isinstance(clamp, (list, tuple)) and len(clamp) == 2:
            clamp_low, clamp_high = float(clamp[0]), float(clamp[1])
        else:
            clamp_low, clamp_high = None, None
        value = values.get(kpi["key"])
        # Coerce non-finite floats to None — they break frontend tabular-nums
        if isinstance(value, float):
            try:
                if not (value == value) or value in (float("inf"), float("-inf")):
                    value = None
            except Exception:
                value = None
        buckets[title]["kpis"].append({
            "key":       kpi["key"],
            "label":     _kpi_label(kpi),
            "value":     value,
            "format":    _infer_kpi_format(kpi),
            "mandatory": bool(kpi.get("mandatory")),
            "clamp_low":  clamp_low,
            "clamp_high": clamp_high,
        })

    # Render groups in a stable, semantically meaningful order
    _GROUP_ORDER = (
        "Profitability", "Capital", "Risk & Reserves",
        "Growth & Pipeline", "Operations",
    )
    groups = [buckets[t] for t in _GROUP_ORDER if t in buckets]

    # ── V3: Composite adjustment audit bridge ───────────────────────────────
    # Computes the Quality x Risk x Commodity multipliers from extracted KPIs
    # and includes the full breakdown in the payload so the frontend can
    # render the "Pre-IV -> Q x R x C -> Final" bridge on every card.
    _composite_mult, _bridge = composite_adjustment(profile_name, spec.get("sector", ""), values)

    return {
        "ticker":         ticker,
        "sector":         spec.get("sector", ""),
        "profile_name":   profile_name,
        "sub_profile":    sub_sub or None,
        "anchor_methods": list(spec.get("anchor_methods", [])),
        "groups":         groups,
        "source_priority": list(spec.get("source_priority", [])),
        # V3 Composite Adjustment Audit Bridge — frontend renders this as
        # "Pre-IV  ->  Q × R × C  ->  Final" for full transparency.
        "audit_bridge":   _bridge,
    }


def render_card_payloads_for_run(state: dict) -> dict[str, dict]:
    """Convenience: build sector_card dict for every ticker in the run.

    Returns ``{ticker: payload}`` where payload is the dict from
    `render_card_payload`. Tickers whose profile is legacy or unknown are
    omitted (frontend should fall back to its existing bespoke card or
    render nothing — both are valid).

    Call site: add this AFTER dcf_agent (so all metric extractors have
    finished writing to state) and BEFORE the pipeline return so it
    propagates to web_runs JSON. See pipeline.py call site.
    """
    if not isinstance(state, dict):
        return {}
    data = state.get("data") if "data" in state else state
    if not isinstance(data, dict):
        return {}
    profile_names = data.get("profile_names") or {}
    tickers = data.get("tickers") or list(profile_names.keys())
    out: dict[str, dict] = {}
    for ticker in tickers:
        profile = profile_names.get(ticker) or data.get("profile_name") or ""
        payload = render_card_payload(profile, state, ticker)
        if payload is not None:
            out[ticker] = payload
    return out


# ── Public API surface ───────────────────────────────────────────────────────

__all__ = [
    "SECTOR_KPI_FRAMEWORK",
    "render_search_overlay",
    "render_specialist_addendum",
    "build_extractor_schema",
    "validate_extractor_output",
    "extract_via_framework",
    "attach_overrides",
    # Sector card payload (Option B card render)
    "render_card_payload",
    "render_card_payloads_for_run",
    "is_legacy_profile",
    # V3 Composite Adjustment audit bridge
    "composite_adjustment",
]
