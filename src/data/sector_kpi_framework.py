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
import logging
import re
from typing import Any

# D5: FMP key resolution through the run-config overlay (per-run API keys in
# web runs) with process-env fallback — replaces a hardcoded key.
from src.utils.run_config import getenv as _run_getenv


def _fmp_api_key() -> str | None:
    """Resolve the FMP API key, or None when unset.

    D5: the previous fallback was a hardcoded key committed in source. When
    no key is configured, callers degrade gracefully (risk-KPI fetch returns
    {}, commodity fetch returns None — both are gap-fill only).
    """
    return _run_getenv("FMP_API_KEY")

_LOG = logging.getLogger("sector_kpi_framework")



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
            # ── Gordon Growth Model assumptions ──────────────────
            # The triplet below IS the bank price target on the sell side:
            # target P/B = (ROE - g) / (CoE - g). Broker notes publish these
            # explicitly in a valuation table (risk-free rate, equity-risk
            # premium, beta -> CoE; plus a terminal growth rate), so they are
            # extractable rather than assumed. dcf_agent._bank_ggm_assumptions
            # consumes them, falling back to _BANK_GGM_OVERRIDES and then to
            # the profile calibration.
            {
                "key":             "cost_of_equity",
                "mandatory":       False,
                "search_phrases":  ["cost of equity", "COE", "required return on equity"],
                "compute_hint":    "Cost of equity used in the GGM/DDM valuation (risk-free + beta x ERP)",
                "clamp":           (0.05, 0.20),
                "extractor_only":  True,
                "decimal_format":  True,
                "fallback":        "use cfg['coe'] from _BANK_PROFILE_CALIBRATION",
            },
            {
                "key":             "terminal_growth_rate",
                "mandatory":       False,
                "search_phrases":  ["terminal growth", "long-term growth rate", "g =", "perpetual growth"],
                "compute_hint":    "Terminal growth rate g in the Gordon Growth Model",
                "clamp":           (0.0, 0.06),
                "extractor_only":  True,
                "decimal_format":  True,
                "fallback":        "use cfg['ggm_g'] from _BANK_PROFILE_CALIBRATION",
            },
            {
                "key":             "target_price_to_book",
                "mandatory":       False,
                "search_phrases":  ["target P/B", "target price to book", "P/BV multiple", "we assume a x P/BV"],
                "compute_hint":    "Justified P/B multiple the analyst applies to book value per share",
                "clamp":           (0.2, 5.0),
                "extractor_only":  True,
            },
            {
                "key":             "equity_risk_premium",
                "mandatory":       False,
                "search_phrases":  ["equity risk premium", "ERP", "market risk premium"],
                "compute_hint":    "Equity risk premium used to build the cost of equity",
                "clamp":           (0.02, 0.12),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "credit_cost_bps",
                "mandatory":       False,
                "search_phrases":  ["credit cost", "specific provisions", "SP of bps", "provisions guidance"],
                "compute_hint":    "Guided credit cost / specific provisions in basis points of loans",
                "clamp":           (0.0, 300.0),
                "extractor_only":  True,
            },
            # NOTE: book value per share is deliberately NOT extracted. The
            # key collides with the balance-sheet line item of the same
            # name, and the framework-metrics bridge writes extracted KPIs
            # onto the financial row by key — so an LLM-extracted value
            # silently displaces the real one. On the D05.SI run of
            # 2026-08-27 that replaced DBS's actual BVPS of S$24.28 with an
            # extracted 32.5, inflating the GGM from S$60.48 to S$80.94 and
            # flipping the call from SELL to HOLD. BVPS is total equity over
            # shares — a hard number that is always available — so there is
            # nothing to gain from extracting it.
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
            # ── Singapore REIT disclosure set ─────────────────────
            # Every S-REIT results note leads with these: gearing,
            # all-in cost of debt, the hedged proportion, occupancy,
            # rental reversion, and DPU. The last two entries are the
            # DDM itself — Singapore brokers publish the cost of
            # equity and terminal growth in the valuation line, which
            # makes them extractable rather than assumed.
            {
                "key":             "cost_of_debt_pct",
                "mandatory":       True,
                "search_phrases":  ['all-in cost of debt', 'average cost of debt', 'cost of borrowings'],
                "compute_hint":    "Average all-in cost of debt (decimal, e.g. 2.7% -> 0.027)",
                "clamp":           (0.005, 0.12),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "fixed_rate_debt_pct",
                "mandatory":       False,
                "search_phrases":  ['hedged to fixed', 'fixed rate borrowings', 'fixed-rate debt proportion'],
                "compute_hint":    "Share of borrowings hedged to fixed rates (decimal)",
                "clamp":           (0.0, 1.0),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "rental_reversion_pct",
                "mandatory":       True,
                "search_phrases":  ['rental reversion', 'reversions', 'positive reversion'],
                "compute_hint":    "Portfolio rental reversion for the period (decimal, +10% -> 0.10)",
                "clamp":           (-0.4, 0.6),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "interest_coverage_ratio",
                "mandatory":       False,
                "search_phrases":  ['interest coverage', 'ICR'],
                "compute_hint":    "Interest coverage ratio (x)",
                "clamp":           (0.5, 20.0),
                "extractor_only":  True,
            },
            {
                "key":             "nav_per_unit",
                "mandatory":       False,
                "search_phrases":  ['NAV per unit', 'net asset value per unit', 'adjusted NAV'],
                "compute_hint":    "Net asset value per unit in reporting currency",
                "clamp":           (0.05, 100.0),
                "extractor_only":  True,
            },
            {
                "key":             "distribution_yield_pct",
                "mandatory":       False,
                "search_phrases":  ['distribution yield', 'DPU yield', 'dividend yield'],
                "compute_hint":    "Forward distribution yield (decimal, 5.1% -> 0.051)",
                "clamp":           (0.005, 0.25),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "distribution_payout_ratio",
                "mandatory":       False,
                "search_phrases":  ['payout ratio', 'distribution payout'],
                "compute_hint":    "Distributable income paid out as DPU (decimal)",
                "clamp":           (0.3, 1.2),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "sreit_cost_of_equity",
                "mandatory":       False,
                "search_phrases":  ['cost of equity', 'COE', 'required return'],
                "compute_hint":    "Cost of equity used in the analyst's DDM (decimal, 6.83% -> 0.0683)",
                "clamp":           (0.03, 0.15),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "sreit_terminal_growth",
                "mandatory":       False,
                "search_phrases":  ['terminal growth', 'terminal g', 'long-term growth rate'],
                "compute_hint":    "Terminal growth rate g in the analyst's DDM (decimal)",
                "clamp":           (0.0, 0.05),
                "extractor_only":  True,
                "decimal_format":  True,
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
            # Same-store NOI growth is the headline operating KPI; dps_usd /
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
                "mandatory":       True,
                "search_phrases":  ["LTV:CAC", "LTV/CAC"],
                "compute_hint":    "LTV/CAC. Fully-loaded CAC = S&M / Net New Logos (or S&M / Net New ARR if logos n/d). LTV = (Blended ACV × Subscription GM) / Annual Revenue Churn. If LTV/CAC > 10x OR Payback < 6mo → re-calc using enterprise cohort only. Target 3-10x; outliers (>10x) usually indicate blended-cohort error.",
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
            {"key": "ltv_cac_ratio",        "mandatory": True,  "search_phrases": ["LTV:CAC"],              "clamp": (1, 15),       "extractor_only": True},
            {"key": "magic_number",         "mandatory": False, "search_phrases": ["magic number"],         "clamp": (0.1, 3.0),    "extractor_only": True},
            {"key": "rpo_growth_yoy",       "mandatory": False, "search_phrases": ["RPO"],                  "clamp": (-0.20, 0.80), "extractor_only": True, "decimal_format": True},
            {"key": "billings_growth_yoy", "mandatory": False, "search_phrases": ["billings growth"],      "clamp": (-0.20, 0.80), "extractor_only": True, "decimal_format": True},
        ],
        "source_priority": ["Q4 earnings call supplement", "10-K", "Investor day"],
    },

    "Cybersecurity / Mission-Critical SaaS": {
        "sector":         "Tech",
        "anchor_methods": ["DCF (FCF+ anchor)", "NRR-adj DCF", "EV/Revenue"],
        "kpis": [
            # Cybersecurity: NRR + ARR growth proxies (rpo_growth_yoy / billings_growth_yoy) mandatory
            # because category-king status drives multiple expansion. Rule of 40 nice but FCF margin is
            # often negative for fast-growth cyber names so it's not a reliable mandatory.
            {"key": "nrr_pct",              "mandatory": True,  "search_phrases": ["NRR", "net retention"], "clamp": (0.80, 1.50), "extractor_only": True, "decimal_format": True},
            {"key": "rpo_growth_yoy",       "mandatory": True,  "search_phrases": ["RPO growth", "remaining performance obligations"], "clamp": (-0.20, 0.80), "extractor_only": True, "decimal_format": True},
            {"key": "billings_growth_yoy", "mandatory": False, "search_phrases": ["billings growth"],      "clamp": (-0.20, 0.80), "extractor_only": True, "decimal_format": True},
            {"key": "rule_of_40_score",     "mandatory": True,  "search_phrases": ["Rule of 40"],          "clamp": (-30, 120),    "extractor_only": True},
            {"key": "gross_retention_pct",  "mandatory": False, "search_phrases": ["gross retention"],     "clamp": (0.80, 1.00), "extractor_only": True, "decimal_format": True},
            {"key": "cac_payback_months",   "mandatory": False, "search_phrases": ["CAC payback"],          "clamp": (3, 60),       "extractor_only": True},
            {"key": "ltv_cac_ratio",        "mandatory": True,  "search_phrases": ["LTV:CAC"],              "clamp": (1, 15),       "extractor_only": True},
            {"key": "magic_number",         "mandatory": False, "search_phrases": ["magic number"],         "clamp": (0.1, 3.0),    "extractor_only": True},
            {"key": "cash_runway_years",    "mandatory": True,  "search_phrases": ["cash runway", "months of runway", "burn rate"], "compute_hint": "Cash & equivalents / annualised cash burn — FMP-augmented", "clamp": (0.0, 99.0), "source": "F", "extractor_only": False},
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
                "mandatory":       True,
                "search_phrases":  ["gross margin", "gross profit margin", "GM %"],
                "compute_hint":    "Gross margin % (Big Pharma typically 75-85%)",
                "clamp":           (0.5, 0.95),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "net_debt_to_ebitda",
                "mandatory":       True,
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
            {
                "key":             "debt_to_ebitda",
                "mandatory":       True,
                "search_phrases":  ["debt to EBITDA", "leverage ratio", "debt/EBITDA"],
                "compute_hint":    "Total debt / TTM EBITDA — FMP-augmented (utilities typically 4.0-6.0x)",
                "clamp":           (0.0, 12.0),
                "source":          "F",
                "extractor_only":  False,
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
                "mandatory":       True,
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
            {
                "key":             "net_debt_to_ebitda",
                "mandatory":       True,
                "search_phrases":  ["net debt to EBITDA", "leverage ratio", "debt / EBITDA"],
                "compute_hint":    "(total_debt - cash) / TTM EBITDA — FMP-augmented (E&P typically 0.5-2.0x mid-cycle)",
                "clamp":           (-1.0, 10.0),
                "source":          "F",
                "extractor_only":  False,
                "fmp_field":       "netDebtToEBITDATTM",
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
            {
                "key":             "net_debt_to_ebitda",
                "mandatory":       True,
                "search_phrases":  ["net debt to EBITDA", "leverage ratio", "debt / EBITDA"],
                "compute_hint":    "(total_debt - cash) / TTM EBITDA — FMP-augmented (cycle-sensitive)",
                "clamp":           (-1.0, 10.0),
                "source":          "F",
                "extractor_only":  False,
                "fmp_field":       "netDebtToEBITDATTM",
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
            {
                "key":             "net_debt_to_ebitda",
                "mandatory":       True,
                "search_phrases":  ["net debt to EBITDA", "leverage ratio", "debt / EBITDA"],
                "compute_hint":    "(total_debt - cash) / TTM EBITDA — FMP-augmented (fabless typically <0.5x)",
                "clamp":           (-1.0, 10.0),
                "source":          "F",
                "extractor_only":  False,
                "fmp_field":       "netDebtToEBITDATTM",
            },
        ],
        "source_priority": [
            "Q4 earnings call segment disclosure",
            "Latest 10-K",
            "Investor day product roadmap",
        ],
    },

    # ── Semiconductor: IDM / Foundry (TSM, INTC, GFS) ─────────────────────
    "Memory / DRAM-NAND": {
        "sector":         "Semiconductor",
        "anchor_methods": ["P/E (norm)", "EV/EBITDA", "DCF"],
        "kpis": [
            {
                # Canonical key, not a bespoke one: the FMP augmentation stage
                # fills `gross_margin_pct` (0.7257 on the live MU run) and the
                # LLM extractor is only ever asked for the profile's own keys,
                # so a renamed duplicate can never be populated.
                "key":             "gross_margin_pct",
                "mandatory":       True,
                "search_phrases":  ["gross margin", "GM%", "blended gross margin"],
                "compute_hint":    "Blended memory gross margin (decimal) — the cycle's clearest single read",
                "clamp":           (-0.60, 0.80),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "hbm_revenue_share",
                "mandatory":       True,
                "search_phrases":  ["HBM revenue", "HBM mix", "HBM share of DRAM", "high bandwidth memory"],
                "compute_hint":    "HBM as a share of DRAM revenue (decimal) — the AI-cycle growth driver",
                "clamp":           (0.0, 0.90),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "dram_bit_growth",
                "mandatory":       True,
                # The research states this as "DRAM Bit Demand Growth vs. Bit
                # Supply Growth" — the demand-side wording was absent from the
                # phrase list, and this was the one mandatory KPI still
                # unfilled on the live MU run.
                "search_phrases":  ["DRAM bit growth", "DRAM bit demand growth",
                                    "bit demand growth", "DRAM bit shipment",
                                    "bit supply growth"],
                "compute_hint":    "YoY DRAM bit demand/shipment growth (decimal)",
                "clamp":           (-0.50, 1.50),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "nand_bit_growth",
                "mandatory":       False,
                "search_phrases":  ["NAND bit growth", "NAND bit shipment"],
                "compute_hint":    "YoY NAND bit shipment growth (decimal)",
                "clamp":           (-0.50, 1.50),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "memory_asp_change",
                "mandatory":       False,
                "search_phrases":  ["ASP", "average selling price", "blended ASP", "pricing"],
                "compute_hint":    "YoY change in blended average selling price (decimal)",
                "clamp":           (-0.70, 2.00),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "bit_supply_demand_gap",
                "mandatory":       True,
                "search_phrases":  ["supply/demand", "supply demand balance", "sufficiency ratio", "bit supply growth vs demand"],
                "compute_hint":    "Industry bit supply growth less demand growth (decimal; positive = oversupply)",
                "clamp":           (-0.50, 0.50),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "memory_capex",
                "mandatory":       False,
                "search_phrases":  ["capex", "capital expenditure", "capex guidance"],
                "compute_hint":    "Capex guidance for the year, reporting currency",
                "clamp":           (0.0, 200000000000.0),
                "extractor_only":  True,
            },
            {
                "key":             "node_transition_pct",
                "mandatory":       False,
                "search_phrases":  ["node transition", "1-alpha", "1a nm", "1b nm", "1c nm", "technology migration"],
                "compute_hint":    "Share of bits on the leading node (decimal)",
                "clamp":           (0.0, 1.0),
                "extractor_only":  True,
                "decimal_format":  True,
            },
        ],
        "source_priority": [
            "Quarterly results + capex/bit-growth guidance",
            "Latest 10-K / annual report",
            "Sell-side memory-cycle notes (supply/demand balance)",
        ],
    },

    "IDM / Foundry": {
        "sector":         "Semiconductor",
        "anchor_methods": ["DCF", "EV/EBITDA", "P/B"],
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
            {
                "key":             "net_debt_to_ebitda",
                "mandatory":       True,
                "search_phrases":  ["net debt to EBITDA", "leverage ratio", "debt / EBITDA"],
                "compute_hint":    "(total_debt - cash) / TTM EBITDA — FMP-augmented (foundry typically 1.0-2.5x)",
                "clamp":           (-1.0, 10.0),
                "source":          "F",
                "extractor_only":  False,
                "fmp_field":       "netDebtToEBITDATTM",
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
                "mandatory":       True,
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
            {
                "key":             "debt_to_ebitda",
                "mandatory":       True,
                "search_phrases":  ["debt to EBITDA", "leverage ratio", "debt/EBITDA"],
                "compute_hint":    "Total debt / TTM EBITDA — FMP-augmented (telco typically 2.5-4.0x)",
                "clamp":           (0.0, 12.0),
                "source":          "F",
                "extractor_only":  False,
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
            {
                "key":             "net_debt_to_ebitda",
                "mandatory":       True,
                "search_phrases":  ["net debt to EBITDA", "leverage ratio", "debt / EBITDA"],
                "compute_hint":    "(total_debt - cash) / TTM EBITDA — FMP-augmented (auto OEM 1.0-3.0x)",
                "clamp":           (-1.0, 10.0),
                "source":          "F",
                "extractor_only":  False,
                "fmp_field":       "netDebtToEBITDATTM",
            },
        ],
        "source_priority": [
            "Q4 earnings call delivery + mix breakdown",
            "Latest 10-K",
            "Production reports (TSLA, RIVN, LCID monthly disclosures)",
        ],
    },

    # ── HealthcareServices: Managed Care (UNH, ELV, HUM, CI, CVS) ──────────
    # Sector is HealthcareServices (matches TICKER_SECTOR_LOOKUP). Previously
    # registered under "Biopharma", which caused the card header to render
    # "BIOPHARMA · MANAGED CARE" and made Managed Care the ONLY candidate
    # profile for the HealthcareServices sector — so any health-services name
    # without a hardcoded override (e.g. animal-health ZTS) inherited insurer
    # KPIs. See sibling HealthcareServices profiles below.
    "Managed Care": {
        "sector":         "HealthcareServices",
        "anchor_methods": ["DCF", "P/E (ops)", "EV/EBITDA"],
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
            {
                "key":             "debt_to_ebitda",
                "mandatory":       True,
                "search_phrases":  ["debt to EBITDA", "leverage ratio", "debt/EBITDA"],
                "compute_hint":    "Total debt / TTM EBITDA — FMP-augmented (managed care typically 2.0-4.0x)",
                "clamp":           (0.0, 12.0),
                "source":          "F",
                "extractor_only":  False,
            },
        ],
        "source_priority": [
            "Q4 earnings call + CMS Final Notice analysis",
            "Latest 10-K",
            "CMS rate-update letters (annual)",
        ],
    },

    # ── HealthcareServices: Healthcare Providers / Services ───────────────
    # Hospitals, dialysis, labs, services (HCA, THC, UHS, DVA, LH, DGX).
    # This is the SAFE GENERIC default for the HealthcareServices sector —
    # margin + leverage driven, no insurance-specific KPIs.
    "Healthcare Providers / Services": {
        "sector":         "HealthcareServices",
        "anchor_methods": ["EV/EBITDA", "P/E (ops)", "DCF"],
        "kpis": [
            {
                "key":             "same_facility_revenue_growth_pct",
                "mandatory":       True,
                "search_phrases":  ["same-facility revenue growth", "same-store revenue",
                                    "organic revenue growth", "same-facility net revenue"],
                "compute_hint":    "Same-facility (organic) revenue growth YoY (decimal)",
                "clamp":           (-0.10, 0.25),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "admissions_growth_yoy_pct",
                "mandatory":       False,
                "search_phrases":  ["admissions growth", "adjusted admissions",
                                    "patient volume growth", "same-facility admissions"],
                "compute_hint":    "Adjusted admissions / patient volume growth YoY (decimal)",
                "clamp":           (-0.15, 0.20),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "ebitda_margin_pct",
                "mandatory":       True,
                "search_phrases":  ["EBITDA margin", "adjusted EBITDA margin"],
                "compute_hint":    "Adjusted EBITDA / revenue (decimal)",
                "clamp":           (0.0, 0.40),
                "source":          "F",
                "extractor_only":  False,
                "fmp_field":       "ebitdaMarginTTM",
                "decimal_format":  True,
            },
            {
                "key":             "bad_debt_pct",
                "mandatory":       False,
                "search_phrases":  ["bad debt", "uncompensated care",
                                    "provision for doubtful accounts"],
                "compute_hint":    "Bad debt / uncompensated care as % of revenue (decimal)",
                "clamp":           (0.0, 0.25),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "occupancy_rate_pct",
                "mandatory":       False,
                "search_phrases":  ["occupancy rate", "bed occupancy", "utilization rate"],
                "compute_hint":    "Facility occupancy / utilization rate (decimal)",
                "clamp":           (0.0, 1.0),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "net_debt_to_ebitda",
                "mandatory":       True,
                "search_phrases":  ["net debt to EBITDA", "leverage ratio", "net debt / EBITDA"],
                "compute_hint":    "(total_debt - cash) / TTM EBITDA — FMP-augmented",
                "clamp":           (-1.0, 12.0),
                "source":          "F",
                "extractor_only":  False,
                "fmp_field":       "netDebtToEBITDATTM",
            },
        ],
        "source_priority": [
            "Latest 10-K + Q earnings (same-facility metrics)",
            "Investor day volume + margin guidance",
        ],
    },

    # ── HealthcareServices: Medical Devices ───────────────────────────────
    # MedTech in the HealthcareServices sector (distinct from Biopharma's
    # "MedTech / Devices"). Premium revenue multiples; gross-margin + leverage
    # driven (ISRG, EW, ZBH-style names that route as HealthcareServices).
    "Medical Devices": {
        "sector":         "HealthcareServices",
        "anchor_methods": ["EV/Revenue", "P/E (ops)", "DCF"],
        "kpis": [
            {
                "key":             "organic_revenue_growth_pct",
                "mandatory":       True,
                "search_phrases":  ["organic revenue growth", "constant-currency revenue growth",
                                    "underlying revenue growth"],
                "compute_hint":    "Organic (constant-currency) revenue growth YoY (decimal)",
                "clamp":           (-0.15, 0.40),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "gross_margin_pct",
                "mandatory":       True,
                "search_phrases":  ["gross margin", "adjusted gross margin"],
                "compute_hint":    "Gross profit / revenue (decimal)",
                "clamp":           (0.0, 0.90),
                "source":          "F",
                "extractor_only":  False,
                "fmp_field":       "grossProfitMarginTTM",
                "decimal_format":  True,
            },
            {
                "key":             "operating_margin_pct",
                "mandatory":       False,
                "search_phrases":  ["operating margin", "adjusted operating margin"],
                "compute_hint":    "Operating income / revenue (decimal)",
                "clamp":           (-0.20, 0.45),
                "source":          "F",
                "extractor_only":  False,
                "fmp_field":       "operatingProfitMarginTTM",
                "decimal_format":  True,
            },
            {
                "key":             "rd_intensity_pct",
                "mandatory":       False,
                "search_phrases":  ["R&D as % of revenue", "research and development intensity"],
                "compute_hint":    "R&D expense / revenue (decimal)",
                "clamp":           (0.0, 0.30),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "recurring_revenue_pct",
                "mandatory":       False,
                "search_phrases":  ["recurring revenue", "consumables revenue mix",
                                    "razor-blade revenue"],
                "compute_hint":    "Recurring / consumables revenue as % of total (decimal)",
                "clamp":           (0.0, 1.0),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "net_debt_to_ebitda",
                "mandatory":       True,
                "search_phrases":  ["net debt to EBITDA", "leverage ratio", "net debt / EBITDA"],
                "compute_hint":    "(total_debt - cash) / TTM EBITDA — FMP-augmented",
                "clamp":           (-1.0, 10.0),
                "source":          "F",
                "extractor_only":  False,
                "fmp_field":       "netDebtToEBITDATTM",
            },
        ],
        "source_priority": [
            "Latest 10-K + Q earnings (organic growth + margin)",
            "Investor day pipeline + recurring-revenue mix",
        ],
    },

    # ── HealthcareServices: Animal Health ─────────────────────────────────
    # Animal-health pharma & diagnostics (ZTS, IDXX, ELAN). High-margin,
    # companion-animal-mix driven; NO human clinical pipeline. This profile
    # exists so animal-health names no longer inherit Managed Care insurer
    # KPIs when routed to HealthcareServices.
    "Animal Health": {
        "sector":         "HealthcareServices",
        "anchor_methods": ["P/E (ops)", "EV/EBITDA", "DCF"],
        "kpis": [
            {
                "key":             "organic_revenue_growth_pct",
                "mandatory":       True,
                "search_phrases":  ["organic revenue growth", "constant-currency revenue growth",
                                    "operational revenue growth"],
                "compute_hint":    "Organic (constant-currency) revenue growth YoY (decimal)",
                "clamp":           (-0.10, 0.30),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "companion_animal_mix_pct",
                "mandatory":       False,
                "search_phrases":  ["companion animal mix", "companion animal revenue",
                                    "pet vs livestock mix"],
                "compute_hint":    "Companion-animal (higher-margin) revenue as % of total (decimal)",
                "clamp":           (0.0, 1.0),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "operating_margin_pct",
                "mandatory":       True,
                "search_phrases":  ["operating margin", "adjusted operating margin"],
                "compute_hint":    "Operating income / revenue (decimal)",
                "clamp":           (0.0, 0.50),
                "source":          "F",
                "extractor_only":  False,
                "fmp_field":       "operatingProfitMarginTTM",
                "decimal_format":  True,
            },
            {
                "key":             "gross_margin_pct",
                "mandatory":       False,
                "search_phrases":  ["gross margin", "adjusted gross margin"],
                "compute_hint":    "Gross profit / revenue (decimal)",
                "clamp":           (0.0, 0.90),
                "source":          "F",
                "extractor_only":  False,
                "fmp_field":       "grossProfitMarginTTM",
                "decimal_format":  True,
            },
            {
                "key":             "rd_intensity_pct",
                "mandatory":       False,
                "search_phrases":  ["R&D as % of revenue", "research and development intensity"],
                "compute_hint":    "R&D expense / revenue (decimal)",
                "clamp":           (0.0, 0.20),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "net_debt_to_ebitda",
                "mandatory":       True,
                "search_phrases":  ["net debt to EBITDA", "leverage ratio", "net debt / EBITDA"],
                "compute_hint":    "(total_debt - cash) / TTM EBITDA — FMP-augmented",
                "clamp":           (-1.0, 8.0),
                "source":          "F",
                "extractor_only":  False,
                "fmp_field":       "netDebtToEBITDATTM",
            },
        ],
        "source_priority": [
            "Latest 10-K + Q earnings (organic growth + margin)",
            "Investor day companion-animal mix + pipeline",
        ],
    },

    # ── HealthcareServices: Pharma Distribution ───────────────────────────
    # Drug distributors / PBMs-distribution (MCK, COR/Cencora, CAH). Razor-thin
    # operating margins (~1-2%) on enormous revenue; ROIC + leverage matter
    # more than margin level.
    "Pharma Distribution": {
        "sector":         "HealthcareServices",
        "anchor_methods": ["P/E (ops)", "EV/EBITDA", "FCF Yield"],
        "kpis": [
            {
                "key":             "revenue_growth_pct",
                "mandatory":       True,
                "search_phrases":  ["revenue growth", "total revenue YoY", "TTM revenue growth"],
                "compute_hint":    "Consolidated revenue growth YoY (decimal)",
                "clamp":           (-0.20, 0.40),
                "source":          "F",
                "extractor_only":  False,
                "decimal_format":  True,
            },
            {
                "key":             "operating_margin_pct",
                "mandatory":       True,
                "search_phrases":  ["operating margin", "adjusted operating margin"],
                "compute_hint":    "Operating income / revenue (decimal — distributors run ~1-2%)",
                "clamp":           (0.0, 0.10),
                "source":          "F",
                "extractor_only":  False,
                "fmp_field":       "operatingProfitMarginTTM",
                "decimal_format":  True,
            },
            {
                "key":             "gross_margin_pct",
                "mandatory":       False,
                "search_phrases":  ["gross margin", "gross profit margin"],
                "compute_hint":    "Gross profit / revenue (decimal)",
                "clamp":           (0.0, 0.20),
                "source":          "F",
                "extractor_only":  False,
                "fmp_field":       "grossProfitMarginTTM",
                "decimal_format":  True,
            },
            {
                "key":             "distribution_segment_growth_pct",
                "mandatory":       False,
                "search_phrases":  ["distribution segment growth", "pharmaceutical distribution growth",
                                    "drug distribution revenue growth"],
                "compute_hint":    "Core pharmaceutical-distribution segment revenue growth YoY (decimal)",
                "clamp":           (-0.15, 0.30),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "roic_pct",
                "mandatory":       False,
                "search_phrases":  ["return on invested capital", "ROIC"],
                "compute_hint":    "Return on invested capital (decimal)",
                "clamp":           (0.0, 0.60),
                # Computed, not extracted: NOPAT / invested capital with an
                # operating denominator floor, from the same annual series the
                # DCF engine projects from (src/data/deterministic_kpis.py).
                # The extractor still gets asked and still serves as the
                # fallback when the filed inputs are missing — a KPI with no
                # EBIT or no equity is reported unavailable rather than
                # invented. `search_phrases` stays so the model keeps supplying
                # the cross-check.
                "extractor_only":  False,
                "decimal_format":  True,
            },
            {
                "key":             "net_debt_to_ebitda",
                "mandatory":       True,
                "search_phrases":  ["net debt to EBITDA", "leverage ratio", "net debt / EBITDA"],
                "compute_hint":    "(total_debt - cash) / TTM EBITDA — FMP-augmented",
                "clamp":           (-1.0, 8.0),
                "source":          "F",
                "extractor_only":  False,
                "fmp_field":       "netDebtToEBITDATTM",
            },
        ],
        "source_priority": [
            "Latest 10-K + Q earnings (segment revenue + margin)",
            "Distribution contract renewal cadence (large-customer concentration)",
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
    "Online Gaming / Sports Betting": {
        "sector":         "Consumer",
        "anchor_methods": ["EV/EBITDA", "EV/Revenue", "DCF"],
        "kpis": [
            {
                "key":             "monthly_paying_players",
                "mandatory":       True,
                "search_phrases":  ["monthly unique payers", "MUP", "average monthly players", "AMP", "monthly actives"],
                "compute_hint":    "Monthly unique paying players (000s) — DraftKings MUPs / Flutter AMPs",
                "clamp":           (0.0, 100000.0),
                "extractor_only":  True,
            },
            {
                "key":             "net_revenue_margin_pct",
                "mandatory":       True,
                "search_phrases":  ["net revenue margin", "structural hold", "hold rate", "win margin"],
                "compute_hint":    "Net revenue margin (decimal) — structural hold net of promotions",
                "clamp":           (0.0, 0.90),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "gaming_ebitda_margin",
                "mandatory":       True,
                "search_phrases":  ["adjusted EBITDA margin", "EBITDA margin"],
                "compute_hint":    "Adjusted EBITDA margin (decimal); negative while the operator is still scaling",
                "clamp":           (-1.50, 0.60),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "handle_amount",
                "mandatory":       False,
                "search_phrases":  ["handle", "amount wagered", "stakes", "gross gaming revenue", "GGR"],
                "compute_hint":    "Total amount wagered (handle), reporting currency",
                "clamp":           (0.0, 1000000000000.0),
                "extractor_only":  True,
            },
            {
                "key":             "promotional_spend_pct",
                "mandatory":       False,
                "search_phrases":  ["promotional spend", "promotions as a share of revenue", "customer acquisition spend", "external marketing"],
                "compute_hint":    "Promotional plus acquisition spend as a share of revenue (decimal)",
                "clamp":           (0.0, 1.00),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "live_market_count",
                "mandatory":       False,
                "search_phrases":  ["states live", "live markets", "regulated markets", "jurisdictions"],
                "compute_hint":    "Count of live regulated markets / states",
                "clamp":           (0.0, 250.0),
                "extractor_only":  True,
            },
            {
                "key":             "igaming_revenue_share",
                "mandatory":       False,
                "search_phrases":  ["iGaming", "casino revenue", "iGaming mix"],
                "compute_hint":    "iGaming as a share of net revenue (decimal); structurally higher margin than sportsbook",
                "clamp":           (0.0, 1.0),
                "extractor_only":  True,
                "decimal_format":  True,
            },
        ],
        "source_priority": [
            "Quarterly results plus MUP/AMP and net revenue margin disclosure",
            "Regulatory / state gaming commission handle data",
            "Latest 10-K / annual report",
        ],
    },

    'Apparel / Athletic Wear': {
        "sector":         'Consumer',
        "anchor_methods": ['EV/EBITDA', 'DCF (FCF)', 'P/E (ops)', 'Brand Valuation'],
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
                "key":             'inventory_turns_research',
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
                "key":             'inventory_turns_research',
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
                "mandatory":       True,
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
            {
                "key":             "net_debt_to_ebitda",
                "mandatory":       True,
                "search_phrases":  ["net debt to EBITDA", "leverage ratio", "debt / EBITDA"],
                "compute_hint":    "(total_debt - cash) / TTM EBITDA — FMP-augmented (franchise/royalty typically 2.5-4.0x)",
                "clamp":           (-1.0, 10.0),
                "source":          "F",
                "extractor_only":  False,
                "fmp_field":       "netDebtToEBITDATTM",
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
                "key":             'nna_capture_pct',
                "mandatory":       False,
                "search_phrases":  ['net new asset growth', 'NNA as a % of client assets', 'organic growth rate'],
                "compute_hint":    'Annualised net new assets / beginning client assets (decimal)',
                "clamp":           (-0.30, 0.50),
                "extractor_only":  True,
                "decimal_format":  True,
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
                "key":             'cash_to_nav_pct',
                "mandatory":       False,
                "search_phrases":  ['cash as a % of NAV', 'net cash to NAV', 'dry powder'],
                "compute_hint":    'Cash and equivalents / SOTP NAV (decimal)',
                "clamp":           (0.0, 0.80),
                "extractor_only":  True,
                "decimal_format":  True,
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
                "key":             'tbvps_research',
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
            {
                "key":             "net_debt_to_ebitda",
                "mandatory":       True,
                "search_phrases":  ["net debt to EBITDA", "leverage ratio", "debt / EBITDA"],
                "compute_hint":    "(total_debt - cash) / TTM EBITDA — FMP-augmented (A&D primes typically 1.5-3.0x)",
                "clamp":           (-1.0, 10.0),
                "source":          "F",
                "extractor_only":  False,
                "fmp_field":       "netDebtToEBITDATTM",
            },
        ],
        "source_priority": ['Federal defense budget appropriations (DOD)', 'Book-to-bill ratios'],
    },

    'Automotive (OEM)': {
        "sector":         'Industrials',
        "anchor_methods": ['EV/EBITDA', 'P/E (ops)', 'P/BV', 'FCF Yield'],
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

    # Value-aware, not presence-aware. A key sitting in the dict as None is
    # missing in every sense that matters to the card, and the old `not in`
    # test counted it as present.
    missing = [
        k for k in mandatory_keys
        if output.get(k) is None
    ]
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
    retry_directive: str = "",
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
            system=spec_built["system_prompt"] + mandatory_directive + retry_directive,
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
        # C3: robust parse (preamble/postamble/mixed-fence tolerant). The old
        # fence-strip + json.loads silently returned {} when the model wrapped
        # the JSON in prose — observed failure mode on Qwen synthesis models.
        # Function-level import to avoid a module-load cycle (deep_research
        # imports this module at call time too).
        from src.agents.industry.deep_research import _parse_llm_json
        parsed = _parse_llm_json(
            raw, extractor_name=f"framework_metrics[{profile_name}]"
        )
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
    except Exception as _exc:
        print(f"  [framework_metrics[{profile_name}] {ticker}] extractor FAILED: {type(_exc).__name__}: {_exc}")
        return {}


# ── Renderer 5: dcf_agent attachment (override most_recent in one loop) ──────

def _protected_line_item_keys() -> frozenset[str]:
    """Names that carry values sourced from filings, not from an LLM.

    Anything declared on the financial models is data the pipeline reads off
    a statement or a provider ratio endpoint. A KPI key that matches one of
    these names lands on the same slot in `most_recent`, and the consumer
    reading it back has no way to tell which source won.
    """
    names: set[str] = set()
    try:
        from src.data.models import FinancialMetrics, LineItem
        names |= set(FinancialMetrics.model_fields)
        names |= set(LineItem.model_fields)
    except Exception:      # pragma: no cover — models must not break the bridge
        pass
    # Statement line items the DCF requests explicitly. LineItem allows extra
    # fields, so its declared set does not cover these.
    names |= {
        "revenue", "gross_profit", "cost_of_revenue", "operating_income",
        "operating_expense", "ebit", "ebitda", "net_income", "interest_income",
        "interest_expense", "research_and_development", "stock_based_compensation",
        "depreciation_and_amortization", "free_cash_flow", "operating_cash_flow",
        "capital_expenditure", "change_in_working_capital", "share_buyback",
        "common_stock_repurchased", "total_assets", "total_equity",
        "total_liabilities", "net_debt", "total_debt", "invested_capital",
        "cash_and_equivalents", "goodwill", "intangible_assets",
        "loans_receivable", "loans_held_for_investment", "total_deposits",
        "provision_for_loan_losses", "dividends_per_share",
        "book_value_per_share", "tangible_book_value_per_share",
        "shares_outstanding", "earnings_per_share", "minority_interest",
    }
    return frozenset(names)


PROTECTED_LINE_ITEM_KEYS: frozenset[str] = _protected_line_item_keys()


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
            value = extractor_output[key]
            # An LLM-extracted KPI must never silently replace a value that
            # came off the filings. 36 KPI keys across the framework are also
            # real fields on the financial models — `net_debt_to_ebitda` on 34
            # profiles, `nav_per_unit` on the two REIT profiles, and that one
            # feeds the NAV Discount method directly. Overwriting is invisible
            # once it happens: the downstream branch reads `.get(key)` and
            # cannot tell a filing apart from a sentence in a research note.
            #
            # Filling a GAP is legitimate and stays supported — REIT NAV per
            # unit is disclosed in filings the data provider often omits — so
            # the guard only diverts when a filed value is already present.
            # The research figure is kept under `_research_<key>` so a
            # cross-check can still read it.
            if key in PROTECTED_LINE_ITEM_KEYS and most_recent.get(key) is not None:
                most_recent[f"_research_{key}"] = value
                audit.append(
                    f"{key}={value} NOT applied — collides with the filed value "
                    f"({most_recent[key]}); kept as _research_{key}"
                )
                continue
            filled_gap = key in PROTECTED_LINE_ITEM_KEYS
            most_recent[key] = value
            if "compute_hint" in kpi:
                audit.append(f"{key}={value} ({kpi['compute_hint']})")
            else:
                audit.append(f"{key}={value}")
            if filled_gap:
                audit[-1] += " [research-sourced — no filed value]"

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

# V3.2 FMP fallback for balance-sheet risk KPIs.
#
# Most extractor outputs lack `net_debt_to_ebitda` / `cash_runway_years` because
# these are balance-sheet derived and rarely quoted verbatim in deep research
# narrative. FMP is the authoritative source. We fetch lazily and cache
# per-ticker per-process to avoid repeated calls
# (one fmp call set per ticker, not per render).

_FMP_RISK_CACHE: dict[str, dict] = {}


def _fmp_risk_kpis(ticker: str) -> dict:
    """Returns dict of FMP-derived KPIs for the ticker (cached).

    v3.2: broadened beyond risk-only fields to cover the universal mandatory
    KPIs used by the Hyperscaler / Tech Conglomerate, Traditional Retail, and
    REIT schemas. These fields are FMP-derivable so the schema can mark them
    `mandatory=True` without forcing the LLM extractor to re-derive what FMP
    already computes deterministically.

    Risk fields:
      - net_debt_to_ebitda  ← key-metrics-ttm.netDebtToEBITDATTM
      - debt_to_ebitda      ← alias of net_debt_to_ebitda (Utilities schema)
      - cash_runway_years   ← cash_and_st_inv / |FCF| if FCF<0, else 99.0
      - leverage_ratio      ← key-metrics-ttm.debtToAssetsTTM (REIT V3 risk)

    Quality fields:
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
        import urllib.request
        import urllib.parse
        key = _fmp_api_key()
        if not key:
            return {}  # D5: no key configured — gap-fill degrades to nothing
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

        # FCF conversion = FCF / net profit. NOT the same metric as
        # fcf_margin_pct (FCF / revenue) — the Conglomerate (SG) schema marks
        # it mandatory and BN4.SI's deep research mentions "FCF conversion"
        # zero times, so the LLM extractor could never supply it. Both inputs
        # are FMP fields, so derive it here rather than asking the model to
        # re-derive what is already computable.
        eps_ttm = ratios.get("netIncomePerShareTTM")
        if fcf_ps is not None and eps_ttm is not None and float(eps_ttm) > 0:
            out["fcf_conversion_pct"] = round(float(fcf_ps) / float(eps_ttm), 4)

    except Exception:
        pass  # Fail silently — a missing field is left missing on the card

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
# Resources/Energy/Materials profiles show spot commodity prices on the
# sector card. The extractor often misses these (they're market data, not
# company-disclosed) — FMP /stable/quote provides them.
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
        import urllib.request, urllib.parse
        key = _fmp_api_key()
        if not key:
            return None  # D5: no key configured — commodity gap-fill skipped
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


# Legacy sub-profiles already render bespoke cards (per separate KPI panels
# in the existing frontend). Do NOT generate a generic sector_card for these
# — the existing UI is purpose-built and the user has explicitly held them.
_LEGACY_PROFILES: frozenset[str] = frozenset({
    # v3.4 — Growth SaaS + Mature SaaS migrated off legacy:
    # they now have a full KPI spec and use the generic SectorValuationCard. The bespoke TechValuationPanel can
    # coexist as a richer alternate UI for SaaS — no removal, just no longer
    # the only path.
    #
    # D4 (2026-08) — "Hyperscaler" migrated off legacy for the same reason:
    # the framework key "Hyperscaler / Tech Conglomerate" has a full KPI spec
    # and the classifier emits the FULL key,
    # so the bare "Hyperscaler" legacy entry never matched live runs — it
    # only gated old cached profile strings, whose cards then rendered
    # nothing and whose FMP augmentation was skipped. Bare strings now
    # resolve via _PROFILE_ALIASES below.
    "REIT",
    "Pipeline (Pre-revenue Biotech)",
    "Pre-approval Biotech",
    "Pre-Revenue Biotech",
})


def is_legacy_profile(profile_name: str | None) -> bool:
    """True when the sub-profile already has a bespoke frontend card and
    should NOT receive the generic sector_card render."""
    return bool(profile_name) and profile_name in _LEGACY_PROFILES


# ── KPI display-format contract ──────────────────────────────────────────────
# Display formats understood by the frontend `fmt()` (SectorValuationCard.tsx):
#   pct     — value is a 0–1 ratio, rendered "× 100 = N%"   (e.g. 0.12 → "12.0%")
#   pct100  — value is ALREADY 0–100, rendered "N%"          (no ×100)
#   bps     — basis points, rendered "N bps"                 (e.g. 166 → "166 bps")
#   usd     — absolute dollars, rendered "$N"                (toLocaleString)
#   usd_b   — value already in $billions, rendered "$N.NB"
#   x       — a multiple / ratio / score, rendered "N×"
#   int     — a count / duration, rendered as a plain integer
#   string  — opaque text (dates, free-form) rendered verbatim
#
# Format resolution order (see _infer_kpi_format): an explicit per-KPI "fmt"
# wins; then the curated key→format map below (covers EVERY instance of a key,
# e.g. all 30 net_debt_to_ebitda defs at once); then suffix-anchored heuristics.
_KPI_FORMAT_OVERRIDES: dict[str, str] = {
    # ── multiples / leverage / coverage / unitless scores → "×" ──
    # (these carry NO decimal_format flag and are NOT percentages; the old
    #  substring heuristic mis-rendered them as % → e.g. ltv_cac 8 → "800%")
    "net_debt_to_ebitda":      "x",   # was raw float "-0.4326732673" (30 profiles)
    "net_debt_to_fre_ebitda":  "x",
    "debt_to_ebitda":          "x",
    "magic_number":            "x",
    "ltv_cac_ratio":           "x",
    "unit_econ_ratio":         "x",
    "unit_economics_ratio":    "x",
    "backlog_coverage_ratio":  "x",
    "book_to_bill_ratio":      "x",
    "rule_of_40_score":        "x",   # was "%" → 4500%; a unitless score
    "token_velocity":          "x",
    # ── SGX sub-profile formats ──────────────────────────────────────
    # Coverage/leverage multiples quoted as "2.4x" in Singapore notes.
    "ebitda_interest_cover":        "x",
    "holdco_net_debt_to_ebitda":    "x",
    "net_debt_to_ebitda_transport": "x",
    # Currency amounts (per-unit or absolute), reporting currency.
    "arpu":                         "usd",
    "yield_per_rpk":                "usd",
    "unit_cost_ex_fuel":            "usd",
    "digital_order_intake":         "usd",
    "divestment_proceeds":          "usd",
    "infra_fum":                    "usd",
    "special_distribution":         "usd",
    "inventory_provision":          "usd",
    "capital_recycling_proceeds":   "usd",
    # Plain counts / physical quantities — never a percentage.
    "divisional_ebitda_disclosed":  "int",
    "dc_capacity_mw":               "int",
    "land_bank_gfa":                "int",
    # ── memory / DRAM-NAND formats ────────────────────────
    "memory_capex":                 "usd",
    "inventory_turns_research":     "x",
    "tbvps_research":               "usd",
    # online gaming formats
    "monthly_paying_players":       "int",
    "live_market_count":            "int",
    "handle_amount":                "usd",
    # ── AI infrastructure / neocloud formats ─────────────────────────
    "contracted_power_gw":          "int",
    "powered_land_options_gw":      "int",
    "gpu_fleet_size":               "int",
    "behind_meter_power_mw":        "int",
    "contract_duration_years":      "x",
    "contracted_revenue_backlog":   "usd",
    "customer_prepayments":         "usd",
    # ── Step-4 (ranks 51-100) SGX formats ────────────────────────────
    "reserve_replacement":            "x",
    "consignment_inventory":          "usd",
    "assets_under_administration":    "usd",
    "aua_net_inflows":                "usd",
    "platform_cash_investments":      "usd",
    "digital_bank_deposits":          "usd",
    "portfolio_nav":                  "usd",
    "revpab":                         "usd",
    "yard_order_intake":              "usd",
    "drydocking_capex":               "usd",
    "raw_material_cost_index":        "usd",
    "test_handler_shipments":         "int",
    "pbwa_bed_capacity":              "int",
    "pbsa_bed_capacity":              "int",
    "agency_transaction_volume":      "int",
    "agent_headcount":                "int",
    "launch_pipeline_units":          "int",
    "hdb_mop_supply":                 "int",
    "yard_capacity_sqm":              "int",
    "inventory_turnover_days":        "int",
    # ── Step-3 SGX sub-profile formats ───────────────────────────────
    "aero_net_debt_to_ebitda":        "x",
    "market_data_revenue":            "usd",
    "technology_capex":               "usd",
    "fee_related_earnings":           "usd",
    "funds_under_management":         "usd",
    "investment_property_revaluation": "usd",
    "capital_recycling_target":       "usd",
    "aero_order_backlog":             "usd",
    "unbilled_revenue":               "usd",
    "net_order_intake":               "usd",
    "advance_payments_net_cash":      "usd",
    "steel_plate_cost_sensitivity":   "usd",
    "revenue_per_sqft":               "usd",
    "biological_asset_fv_change":     "usd",
    "arpob":                          "usd",
    "preopening_capex":               "usd",
    "derivatives_daily_avg_volume":   "int",
    "fx_futures_volume":              "int",
    "iron_ore_derivatives_volume":    "int",
    "aviation_traffic_volume":        "int",
    "mro_turnaround_days":            "int",
    "p2f_conversion_demand":          "int",
    "available_seat_km":              "int",
    "operational_bed_capacity":       "int",
    "medical_tourism_volume":         "int",
    "specialist_headcount":           "int",
    "day_surgery_volume":             "int",
    # S-REIT coverage multiple — quoted as "2.4x" in every Singapore
    # REIT note, never as a percentage.
    "interest_coverage_ratio": "x",
    # ── ratios conventionally quoted as % — pinned so they don't depend on a
    #    per-instance decimal_format flag (some defs of the same key omit it,
    #    which silently regressed them to a raw "string" before this map) ──
    "reserve_replacement_ratio": "pct",
    "solvency_ratio_scr":        "pct",
    "tier_1_capital_ratio":      "pct",
    "provision_coverage_ratio":  "pct",
    "leverage_ratio_delegated":  "pct",
    "leverage_ratio":            "pct",
    "dividend_payout_ratio":     "pct",
    "loan_to_deposit_ratio":     "pct",
    "cet1_ratio":                "pct",
    "efficiency_ratio":          "pct",
    "combined_ratio":            "pct",
    "compensation_ratio":        "pct",
    "expense_ratio":             "pct",
    "loss_ratio":                "pct",
    "medical_loss_ratio":        "pct",
    "npl_ratio":                 "pct",
    "liquidity_coverage_ratio":  "pct",
    "btc_ltv_ratio":             "pct",
    "personnel_cost_to_revenue": "pct",
    # ── value already expressed in $billions ──
    "ai_revenue_run_rate_usd_b": "usd_b",   # was "%" → 15000%
    # ── absolute USD the suffix rules don't catch ──
    "casm_ex_fuel":        "usd",
    "realized_spark_spread": "usd",
    "dpu_cents":           "usd",
    "yield_per_pax_mile":  "usd",
    "net_new_billings":    "usd",
    "sales_per_sq_ft":     "usd",
    # ── counts / durations / capacities ──
    "inventory_days_sales":       "int",
    "payback_period_months":      "int",
    "cac_payback_months":         "int",
    "cash_and_btc_runway_months": "int",
    "cash_runway_qtrs":           "int",
    "postpaid_net_adds_qtr":      "int",
    "avg_daily_volume_adv":       "int",
    "weighted_avg_contract_life": "int",
    "generation_output_mwh":      "int",
    "mkt_penetration_per_region": "int",
    # ── intentionally textual (year / date) — exclude from numeric-string lint ──
    "loe_year_top_drug":  "string",
    "next_catalyst_date": "string",
}

# format → (default decimals, display unit). Emitted in render_card_payload so
# the frontend can format without re-deriving. Frontend may override decimals.
_FORMAT_META: dict[str, tuple[int | None, str | None]] = {
    "pct":    (1, "%"),
    "pct100": (1, "%"),
    "bps":    (0, "bps"),
    "usd":    (2, "$"),
    "usd_b":  (1, "$B"),
    "x":      (2, "×"),
    "int":    (0, None),
    "string": (None, None),
}


def _infer_kpi_format(kpi: dict) -> str:
    """Resolve a KPI's display format. Explicit `fmt` > curated override map >
    suffix-anchored heuristics. Suffix matching (not naive substring) avoids the
    `gene*ratio*n` / `penet*ratio*n` / `_rate`-in-`burn_rate` collisions."""
    # 1) explicit per-KPI override
    explicit = kpi.get("fmt")
    if isinstance(explicit, str) and explicit:
        return explicit
    key = (kpi.get("key") or "").lower()
    # 2) curated key→format map (one entry covers all instances of a key)
    if key in _KPI_FORMAT_OVERRIDES:
        return _KPI_FORMAT_OVERRIDES[key]
    label = (kpi.get("compute_hint") or "").lower()
    # 3) basis points — BEFORE any _rate/ratio/pct logic
    if key.endswith("_bps"):
        return "bps"
    # 4) explicit decimal (0–1) values are percentages by contract
    if kpi.get("decimal_format"):
        return "pct"
    # 5) billions, then absolute USD / per-unit prices
    if key.endswith("_usd_b") or key.endswith("_usd_bn") or key.endswith("_bn"):
        return "usd_b"
    if (key.endswith("_usd") or key.endswith("_cents")
            or key.endswith("_per_boe") or key.endswith("_per_tonne")
            or key.endswith("_per_user") or key.endswith("_per_vehicle")
            or key.endswith("_per_contract") or key.endswith("_aisc")
            or any(t in key for t in ("per_share", "per_oz", "per_unit",
                                      "per_btc", "price", "value"))):
        return "usd"
    # 6) multiples / coverage / turnover (NON-decimal) → "×"
    if key.endswith(("_coverage", "_turnover", "_multiple", "_x")):
        return "x"
    # 7) percentage families — suffix / word-anchored
    if (key.endswith(("_pct", "_yoy", "_qoq", "_growth", "_margin", "_yield",
                      "_rate", "_mix", "_delta"))
            or "_pct_" in key or "_margin" in key or "margin" in key):
        return "pct"
    # 8) counts / durations / capacities
    if key.endswith(("_count", "_quartile", "_weeks", "_years", "_qty",
                     "_months", "_qtrs", "_mwh", "_gw", "_kwspm")):
        return "int"
    # 9) label-based last resort (NB: no "% in label → pct"; that mis-scaled
    #    unitless scores like rule_of_40 into "4500%")
    if "$" in label or " usd" in label:
        return "usd"
    if "basis point" in label or " bps" in label:
        return "bps"
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


# ── Singapore money-center bank spec ────────────────────────────────────
# Derived from the US "Money Center Bank" spec so the KPI set, extractor
# phrases and card layout stay in lockstep. Registered here rather than as a
# literal so the two specs cannot drift apart.
if "Money Center Bank" in SECTOR_KPI_FRAMEWORK:
    import copy as _copy
    _sg_bank = _copy.deepcopy(SECTOR_KPI_FRAMEWORK["Money Center Bank"])
    SECTOR_KPI_FRAMEWORK["Money Center Bank (SG)"] = _sg_bank
    del _sg_bank

# ── SGX sub-profile KPI specs ───────────────────────────────────────────
# Primary metrics per SGX industry sub-profile, split critical vs
# good-to-have via the existing `mandatory` flag:
#   mandatory=True  -> valuation blocker. Without it the model cannot run,
#                      and _completeness_score registers the gap.
#   mandatory=False -> catalyst / moat. Moves the multiple, not the base
#                      formula.
#
# Registered as real framework entries so build_extractor_schema(profile)
# returns a field set — a profile with no entry yields ZERO fields and the
# deep-research extractor is asked for nothing at all (which is exactly
# what happened to "S-REIT" before it was registered).
#
# Every key below was checked against the statement line-item names. The
# framework bridge writes extracted KPIs onto the financial row BY KEY, so
# a collision silently replaces real data.
SECTOR_KPI_FRAMEWORK.update({
    "Telco / Infrastructure (SG)": {
        "sector": "Telco",
        "anchor_methods": ['EV/EBITDA', 'SOTP (published)', 'DCF'],
        "source_priority": ['Quarterly results presentation', 'Annual report segment note', 'Associate share-of-profit disclosure'],
        "kpis": [
            {
                "key":             "arpu",
                "mandatory":       True,
                "group":           "Operations",
                "search_phrases":  ['ARPU', 'average revenue per user', 'postpaid ARPU', 'prepaid ARPU'],
                "compute_hint":    "Blended ARPU per month, reporting currency",
                "clamp":           (1.0, 500.0),
                "extractor_only":  True,
            },
            {
                "key":             "associate_pretax_contribution",
                "mandatory":       True,
                "group":           "Profitability",
                "search_phrases":  ["associates' pre-tax contribution", 'share of associates', 'Airtel', 'Telkomsel'],
                "compute_hint":    "Share of pre-tax profit from regional associates (decimal)",
                "clamp":           (0.0, 1.0),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "capex_to_revenue_pct",
                "mandatory":       True,
                "group":           "Capital",
                "search_phrases":  ['capex-to-revenue', 'capex intensity', 'capex to revenue'],
                "compute_hint":    "Capex / revenue (decimal)",
                "clamp":           (0.0, 0.6),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "ebitda_interest_cover",
                "mandatory":       True,
                "group":           "Capital",
                "search_phrases":  ['EBITDA interest cover', 'interest cover'],
                "compute_hint":    "EBITDA / net interest expense (x)",
                "clamp":           (0.5, 40.0),
                "extractor_only":  True,
            },
            {
                "key":             "fcf_yield_pct",
                "mandatory":       False,
                "group":           "Profitability",
                "search_phrases":  ['free cash flow yield', 'FCF yield'],
                "compute_hint":    "Free cash flow / market cap (decimal)",
                "clamp":           (0.0, 0.3),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "core_dividend_yield_pct",
                "mandatory":       False,
                "group":           "Profitability",
                "search_phrases":  ['core dividend yield', 'dividend yield'],
                "compute_hint":    "Core dividend yield (decimal)",
                "clamp":           (0.0, 0.2),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "dc_capacity_mw",
                "mandatory":       False,
                "group":           "Growth & Pipeline",
                "search_phrases":  ['MW IT load', 'data centre capacity', 'enterprise DC capacity'],
                "compute_hint":    "Contracted data-centre IT load in MW",
                "clamp":           (0.0, 10000.0),
                "extractor_only":  True,
            },
            {
                "key":             "churn_rate_pct",
                "mandatory":       False,
                "group":           "Risk & Reserves",
                "search_phrases":  ['churn rate', 'postpaid churn'],
                "compute_hint":    "Monthly subscriber churn (decimal)",
                "clamp":           (0.0, 0.1),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "digital_order_intake",
                "mandatory":       False,
                "group":           "Growth & Pipeline",
                "search_phrases":  ['NCS order intake', 'digital services order intake'],
                "compute_hint":    "Digital-services order intake, reporting currency",
                "clamp":           (0.0, 100000000000.0),
                "extractor_only":  True,
            },
        ],
    },
    "Conglomerate / Industrial (SG)": {
        "sector": "Industrials",
        "anchor_methods": ['EV/EBITDA', 'SOTP (published)', 'DCF'],
        "source_priority": ['Segment results presentation', 'Annual report divisional note', 'Capital-recycling / divestment announcements'],
        "kpis": [
            {
                "key":             "roic_pct",
                "mandatory":       True,
                "group":           "Profitability",
                "search_phrases":  ['ROIC', 'return on invested capital', 'ROIC by division'],
                "compute_hint":    "Return on invested capital (decimal)",
                "clamp":           (-0.2, 0.6),
                # This is the defect that made ROIC deterministic. Keppel
                # (BN4.SI) is single-listed, so `resolve_company_metrics` has no
                # sibling vector to adopt and every run re-extracted from
                # scratch: one run read 1.09% off a research sentence. That is
                # not a rounding difference: a mandatory input was being
                # sampled from a distribution over sentences. It is now computed
                # from the series; the extractor still supplies the fallback
                # when the filed inputs are missing.
                "extractor_only":  False,
                "decimal_format":  True,
            },
            {
                # Good-to-have, not mandatory: this asks whether the group
                # breaks EBITDA out by division, which BN4.SI's deep research
                # mentions zero times and which no FMP field can supply. A
                # mandatory flag that can never clear only depresses the
                # completeness badge.
                "key":             "divisional_ebitda_disclosed",
                "mandatory":       False,
                "group":           "Operations",
                "search_phrases":  ['divisional EBITDA', 'segment EBITDA', 'EBIT breakdown'],
                "compute_hint":    "Count of divisions with a disclosed EBITDA/EBIT split",
                "clamp":           (0.0, 20.0),
                "extractor_only":  True,
            },
            {
                "key":             "holdco_net_debt_to_ebitda",
                "mandatory":       True,
                "group":           "Capital",
                "search_phrases":  ['holding company debt', 'holdco net debt', 'net debt to EBITDA'],
                "compute_hint":    "Holding-company net debt / EBITDA (x)",
                "clamp":           (-5.0, 15.0),
                "extractor_only":  True,
            },
            {
                "key":             "fcf_conversion_pct",
                "mandatory":       True,
                "group":           "Profitability",
                "search_phrases":  ['FCF conversion', 'cash conversion', 'free cash flow conversion'],
                "compute_hint":    "Free cash flow / net profit (decimal)",
                "clamp":           (-1.0, 3.0),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "divestment_proceeds",
                "mandatory":       False,
                "group":           "Growth & Pipeline",
                "search_phrases":  ['divestment proceeds', 'capital recycling', 'monetisation'],
                "compute_hint":    "Announced divestment / recycling proceeds, reporting currency",
                "clamp":           (0.0, 1000000000000.0),
                "extractor_only":  True,
            },
            {
                "key":             "contracted_power_capacity_gw",
                "mandatory":       False,
                "group":           "Operations",
                "search_phrases":  ['contracted power capacity', 'renewables gross installed capacity', 'GW'],
                "compute_hint":    "Contracted or installed power capacity in GW",
                "clamp":           (0.0, 100.0),
                "extractor_only":  True,
            },
            {
                "key":             "infra_fum",
                "mandatory":       False,
                "group":           "Growth & Pipeline",
                "search_phrases":  ['infrastructure asset management FUM', 'funds under management'],
                "compute_hint":    "Infrastructure FUM, reporting currency",
                "clamp":           (0.0, 1000000000000.0),
                "extractor_only":  True,
            },
            {
                "key":             "special_distribution",
                "mandatory":       False,
                "group":           "Capital",
                "search_phrases":  ['special dividend', 'buyback', 'distribution in specie'],
                "compute_hint":    "Special distribution or buyback value, reporting currency",
                "clamp":           (0.0, 1000000000000.0),
                "extractor_only":  True,
            },
        ],
    },
    "Tech Manufacturing / EMS (SG)": {
        "sector": "Tech",
        "anchor_methods": ['Forward P/E', 'DCF', 'EV/EBITDA'],
        "source_priority": ['Quarterly results', 'Order-book commentary', 'Customer concentration note'],
        "kpis": [
            {
                "key":             "book_to_bill_ratio",
                "mandatory":       True,
                "group":           "Growth & Pipeline",
                "search_phrases":  ['book-to-bill', 'book to bill', 'orders visibility'],
                "compute_hint":    "Book-to-bill ratio (x)",
                "clamp":           (0.2, 3.0),
                "extractor_only":  True,
            },
            {
                "key":             "roce_pct",
                "mandatory":       True,
                "group":           "Profitability",
                "search_phrases":  ['ROCE', 'return on capital employed'],
                "compute_hint":    "Return on capital employed (decimal)",
                "clamp":           (-0.2, 0.6),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "gross_profit_margin",
                "mandatory":       True,
                "group":           "Profitability",
                "search_phrases":  ['gross margin', 'gross profit margin'],
                "compute_hint":    "Gross profit margin (decimal)",
                "clamp":           (0.0, 0.8),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "customer_concentration_pct",
                "mandatory":       True,
                "group":           "Risk & Reserves",
                "search_phrases":  ['key customer revenue concentration', 'customer concentration', 'top customer'],
                "compute_hint":    "Revenue share of the largest customer (decimal)",
                "clamp":           (0.0, 1.0),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "consignment_inventory",
                "mandatory":       False,
                "group":           "Operations",
                "search_phrases":  ['consignment inventory level', 'consignment stock'],
                "compute_hint":    "Consignment inventory held, reporting currency",
                "clamp":           (0.0, 1000000000000.0),
                "extractor_only":  True,
            },
            {
                "key":             "test_handler_shipments",
                "mandatory":       False,
                "group":           "Growth & Pipeline",
                "search_phrases":  ['test handler shipment volume', 'handler shipments'],
                "compute_hint":    "Test handler shipment volume, units",
                "clamp":           (0.0, 1000000.0),
                "extractor_only":  True,
            },
            {
                "key":             "front_end_revenue_mix",
                "mandatory":       False,
                "group":           "Operations",
                "search_phrases":  ['front-end vs back-end exposure', 'front-end mix'],
                "compute_hint":    "Front-end share of semiconductor revenue (decimal)",
                "clamp":           (0.0, 1.0),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "capacity_utilisation_pct",
                "mandatory":       False,
                "group":           "Operations",
                "search_phrases":  ['capacity utilization rate', 'utilisation rate'],
                "compute_hint":    "Capacity utilisation (decimal)",
                "clamp":           (0.0, 1.2),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "inventory_provision",
                "mandatory":       False,
                "group":           "Risk & Reserves",
                "search_phrases":  ['inventory write-down', 'inventory provision'],
                "compute_hint":    "Inventory write-down / provision, reporting currency",
                "clamp":           (0.0, 100000000000.0),
                "extractor_only":  True,
            },
            {
                "key":             "npi_pipeline_count",
                "mandatory":       False,
                "group":           "Growth & Pipeline",
                "search_phrases":  ['new product introduction', 'NPI pipeline'],
                "compute_hint":    "Count of NPI programmes in the pipeline",
                "clamp":           (0.0, 500.0),
                "extractor_only":  True,
            },
        ],
    },
    "Property Developer (SG)": {
        "sector": "Property",
        "anchor_methods": ['NAV', 'DDM', 'P/E (norm)'],
        "source_priority": ['Results presentation', 'Independent valuation report', 'Quarterly pre-sales / take-up disclosure'],
        "kpis": [
            {
                "key":             "rnav_per_share",
                "mandatory":       True,
                "group":           "Capital",
                "search_phrases":  ['RNAV', 'revalued NAV', 'RNAV per share', 'gross development value', 'GDV'],
                "compute_hint":    "Revalued net asset value per share",
                "clamp":           (0.05, 1000.0),
                "extractor_only":  True,
            },
            {
                "key":             "rnav_discount_pct",
                "mandatory":       True,
                "group":           "Capital",
                "search_phrases":  ['discount to RNAV', 'RNAV discount', 'premium to RNAV'],
                "compute_hint":    "Discount (positive) or premium (negative) to RNAV (decimal)",
                "clamp":           (-0.8, 0.8),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "net_gearing_ratio",
                "mandatory":       True,
                "group":           "Risk & Reserves",
                "search_phrases":  ['net gearing', 'net debt to equity', 'gearing ratio'],
                "compute_hint":    "Net debt / equity (decimal)",
                "clamp":           (0.0, 3.0),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "presales_take_up_pct",
                "mandatory":       True,
                "group":           "Growth & Pipeline",
                "search_phrases":  ['pre-sales rate', 'take-up rate', 'pre-sales', 'locked-in sales'],
                "compute_hint":    "Pre-sales take-up rate on launched inventory (decimal)",
                "clamp":           (0.0, 1.2),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "investment_property_fair_value",
                "mandatory":       False,
                "group":           "Capital",
                "search_phrases":  ['fair value of investment properties', 'revaluation gain', 'revaluation loss'],
                "compute_hint":    "Fair value of investment properties, reporting currency",
                "clamp":           (0.0, 10000000000000.0),
                "extractor_only":  True,
            },
            {
                "key":             "land_bank_gfa",
                "mandatory":       False,
                "group":           "Growth & Pipeline",
                "search_phrases":  ['land bank', 'landbank', 'GFA', 'land bank inventory'],
                "compute_hint":    "Land bank gross floor area",
                "clamp":           (0.0, 1000000000.0),
                "extractor_only":  True,
            },
            {
                "key":             "capital_recycling_proceeds",
                "mandatory":       False,
                "group":           "Operations",
                "search_phrases":  ['capital recycling', 'divestment proceeds', 'asset monetisation'],
                "compute_hint":    "Capital-recycling proceeds, reporting currency",
                "clamp":           (0.0, 1000000000000.0),
                "extractor_only":  True,
            },
        ],
    },
    "Market Infrastructure (SG)": {
        "sector": "Financials",
        "anchor_methods": ['P/E (norm)', 'EV/EBITDA', 'DCF'],
        "source_priority": ['Monthly market statistics', 'Quarterly results', 'Fee schedule'],
        "kpis": [
            {
                "key":             "securities_daily_avg_value",
                "mandatory":       True,
                "group":           "Operations",
                "search_phrases":  ['SDAV', 'securities daily average value', 'securities daily traded value'],
                "compute_hint":    "Securities daily average traded value, reporting currency",
                "clamp":           (0.0, 100000000000.0),
                "extractor_only":  True,
            },
            {
                "key":             "derivatives_daily_avg_volume",
                "mandatory":       True,
                "group":           "Operations",
                "search_phrases":  ['DDAV', 'derivatives daily average volume', 'derivatives daily traded volume'],
                "compute_hint":    "Derivatives daily average volume, contracts",
                "clamp":           (0.0, 1000000000.0),
                "extractor_only":  True,
            },
            {
                "key":             "clearing_fee_per_contract",
                "mandatory":       True,
                "group":           "Profitability",
                "search_phrases":  ['clearing fees', 'fee per contract', 'average fee per contract'],
                "compute_hint":    "Average clearing fee per derivatives contract",
                "clamp":           (0.0, 100.0),
                "extractor_only":  True,
            },
            {
                "key":             "exchange_operating_margin",
                "mandatory":       True,
                "group":           "Profitability",
                "search_phrases":  ['operating margin', 'operating leverage'],
                "compute_hint":    "Operating margin (decimal)",
                "clamp":           (0.0, 0.9),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "fx_futures_volume",
                "mandatory":       False,
                "group":           "Growth & Pipeline",
                "search_phrases":  ['FX futures volume', 'currency futures'],
                "compute_hint":    "FX futures volume, contracts",
                "clamp":           (0.0, 1000000000.0),
                "extractor_only":  True,
            },
            {
                "key":             "iron_ore_derivatives_volume",
                "mandatory":       False,
                "group":           "Growth & Pipeline",
                "search_phrases":  ['iron ore derivatives volume', 'commodities derivatives'],
                "compute_hint":    "Iron ore derivatives volume, contracts",
                "clamp":           (0.0, 1000000000.0),
                "extractor_only":  True,
            },
            {
                "key":             "market_data_revenue",
                "mandatory":       False,
                "group":           "Growth & Pipeline",
                "search_phrases":  ['index licensing', 'market data revenue', 'data and connectivity'],
                "compute_hint":    "Index licensing and market data revenue, reporting currency",
                "clamp":           (0.0, 100000000000.0),
                "extractor_only":  True,
            },
            {
                "key":             "technology_capex",
                "mandatory":       False,
                "group":           "Capital",
                "search_phrases":  ['technology capex', 'platform investment'],
                "compute_hint":    "Technology capex, reporting currency",
                "clamp":           (0.0, 100000000000.0),
                "extractor_only":  True,
            },
        ],
    },
    "Real Estate Asset Manager (SG)": {
        "sector": "Financials",
        "anchor_methods": ['P/E (norm)', 'SOTP (published)', 'DDM'],
        "source_priority": ['Results presentation', 'FUM disclosure', 'Independent valuation report'],
        "kpis": [
            {
                "key":             "fee_related_earnings",
                "mandatory":       True,
                "group":           "Profitability",
                "search_phrases":  ['Fee-Related Earnings', 'FRE', 'fee income'],
                "compute_hint":    "Fee-related earnings, reporting currency",
                "clamp":           (0.0, 100000000000.0),
                "extractor_only":  True,
            },
            {
                "key":             "funds_under_management",
                "mandatory":       True,
                "group":           "Growth & Pipeline",
                "search_phrases":  ['FUM', 'funds under management', 'assets under management'],
                "compute_hint":    "Funds under management, reporting currency",
                "clamp":           (0.0, 10000000000000.0),
                "extractor_only":  True,
            },
            {
                "key":             "ram_net_gearing",
                "mandatory":       True,
                "group":           "Risk & Reserves",
                "search_phrases":  ['net gearing ratio', 'net debt to equity', 'cash balance'],
                "compute_hint":    "Net debt / equity (decimal)",
                "clamp":           (0.0, 3.0),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "investment_property_revaluation",
                "mandatory":       True,
                "group":           "Capital",
                "search_phrases":  ['revaluation gain', 'revaluation loss', 'fair value of investment properties'],
                "compute_hint":    "Revaluation gain (positive) or loss, reporting currency",
                "clamp":           (-1000000000000.0, 1000000000000.0),
                "extractor_only":  True,
            },
            {
                "key":             "capital_recycling_target",
                "mandatory":       False,
                "group":           "Operations",
                "search_phrases":  ['capital recycling target', 'recycling proceeds'],
                "compute_hint":    "Capital-recycling target or proceeds, reporting currency",
                "clamp":           (0.0, 1000000000000.0),
                "extractor_only":  True,
            },
            {
                "key":             "fund_coinvestment_value",
                "mandatory":       False,
                "group":           "Capital",
                "search_phrases":  ['co-investment', 'private equity fund co-investments', 'sponsor stake'],
                "compute_hint":    "Balance-sheet co-investment value, reporting currency",
                "clamp":           (0.0, 1000000000000.0),
                "extractor_only":  True,
            },
            {
                "key":             "effective_stake_pct",
                "mandatory":       False,
                "group":           "Operations",
                "search_phrases":  ['effective stake', 'effective interest'],
                "compute_hint":    "Effective economic stake in managed vehicles (decimal)",
                "clamp":           (0.0, 1.0),
                "extractor_only":  True,
                "decimal_format":  True,
            },
        ],
    },
    "Aerospace & Engineering (SG)": {
        "sector": "Industrials",
        "anchor_methods": ['DCF', 'EV/EBITDA', 'Forward P/E'],
        "source_priority": ['Order-book disclosure', 'Quarterly results', 'Aviation traffic statistics'],
        "kpis": [
            {
                "key":             "aero_order_backlog",
                "mandatory":       True,
                "group":           "Growth & Pipeline",
                "search_phrases":  ['order book backlog', 'order book', 'backlog'],
                "compute_hint":    "Order book backlog, reporting currency",
                "clamp":           (0.0, 1000000000000.0),
                "extractor_only":  True,
            },
            {
                "key":             "aero_ebit_margin",
                "mandatory":       True,
                "group":           "Profitability",
                "search_phrases":  ['EBIT margin', 'operating margin'],
                "compute_hint":    "EBIT / revenue (decimal)",
                "clamp":           (-0.2, 0.4),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "aviation_traffic_volume",
                "mandatory":       True,
                "group":           "Operations",
                "search_phrases":  ['aviation traffic volume', 'meals served', 'cargo tonnage', 'flights handled'],
                "compute_hint":    "Aviation traffic throughput — meals, flights or cargo tonnes",
                "clamp":           (0.0, 10000000000.0),
                "extractor_only":  True,
            },
            {
                "key":             "aero_net_debt_to_ebitda",
                "mandatory":       True,
                "group":           "Capital",
                "search_phrases":  ['net debt to EBITDA', 'debt-to-EBITDA', 'net debt'],
                "compute_hint":    "Net debt / EBITDA (x)",
                "clamp":           (-5.0, 20.0),
                "extractor_only":  True,
            },
            {
                "key":             "backlog_burn_months",
                "mandatory":       False,
                "group":           "Growth & Pipeline",
                "search_phrases":  ['burn rate', 'delivery schedule', 'backlog conversion'],
                "compute_hint":    "Months of backlog burn at the current delivery rate",
                "clamp":           (0.0, 180.0),
                "extractor_only":  True,
            },
            {
                "key":             "mro_turnaround_days",
                "mandatory":       False,
                "group":           "Operations",
                "search_phrases":  ['MRO turnaround time', 'turnaround time', 'maintenance turnaround'],
                "compute_hint":    "Commercial aerospace MRO turnaround time in days",
                "clamp":           (0.0, 365.0),
                "extractor_only":  True,
            },
            {
                "key":             "p2f_conversion_demand",
                "mandatory":       False,
                "group":           "Growth & Pipeline",
                "search_phrases":  ['passenger-to-freighter', 'P2F conversion'],
                "compute_hint":    "P2F conversion slots or units in demand",
                "clamp":           (0.0, 1000.0),
                "extractor_only":  True,
            },
            {
                "key":             "unbilled_revenue",
                "mandatory":       False,
                "group":           "Risk & Reserves",
                "search_phrases":  ['unbilled revenue', 'working capital cycle', 'contract assets'],
                "compute_hint":    "Unbilled revenue / contract assets, reporting currency",
                "clamp":           (0.0, 1000000000000.0),
                "extractor_only":  True,
            },
        ],
    },
    "Aviation & Marine (SG)": {
        "sector": "Industrials",
        "anchor_methods": ['EV/EBITDA', 'DCF', 'Forward P/E'],
        "source_priority": ['Monthly operating statistics', 'Order-intake announcements', 'Fuel hedging disclosure'],
        "kpis": [
            {
                "key":             "passenger_yield",
                "mandatory":       True,
                "group":           "Profitability",
                "search_phrases":  ['passenger yield', 'yield per RPK', 'revenue per RPK'],
                "compute_hint":    "Revenue per revenue-passenger-kilometre",
                "clamp":           (0.001, 2.0),
                "extractor_only":  True,
            },
            {
                "key":             "passenger_load_factor",
                "mandatory":       True,
                "group":           "Operations",
                "search_phrases":  ['passenger load factor', 'PLF', 'load factor'],
                "compute_hint":    "Passenger load factor (decimal)",
                "clamp":           (0.3, 1.0),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "net_order_intake",
                "mandatory":       True,
                "group":           "Growth & Pipeline",
                "search_phrases":  ['net order intake', 'firm order backlog', 'yard backlog', 'order wins'],
                "compute_hint":    "Net order intake or firm backlog, reporting currency",
                "clamp":           (0.0, 1000000000000.0),
                "extractor_only":  True,
            },
            {
                "key":             "fuel_hedge_ratio",
                "mandatory":       True,
                "group":           "Risk & Reserves",
                "search_phrases":  ['jet fuel hedging ratio', 'fuel hedge', 'hedged at'],
                "compute_hint":    "Share of fuel requirement hedged (decimal)",
                "clamp":           (0.0, 1.0),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "advance_payments_net_cash",
                "mandatory":       True,
                "group":           "Capital",
                "search_phrases":  ['advance payments', 'net cash position', 'customer deposits'],
                "compute_hint":    "Advance payments / net cash, reporting currency",
                "clamp":           (-1000000000000.0, 1000000000000.0),
                "extractor_only":  True,
            },
            {
                "key":             "available_seat_km",
                "mandatory":       False,
                "group":           "Operations",
                "search_phrases":  ['available seat kilometers', 'ASK', 'revenue passenger km', 'RPK'],
                "compute_hint":    "Available seat kilometres",
                "clamp":           (0.0, 1000000000000.0),
                "extractor_only":  True,
            },
            {
                "key":             "cargo_load_factor",
                "mandatory":       False,
                "group":           "Operations",
                "search_phrases":  ['cargo load factor', 'freight yields', 'freight load factor'],
                "compute_hint":    "Cargo load factor (decimal)",
                "clamp":           (0.0, 1.0),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "offshore_wind_revenue_mix",
                "mandatory":       False,
                "group":           "Growth & Pipeline",
                "search_phrases":  ['offshore wind', 'renewables revenue mix', 'oil & gas revenue mix'],
                "compute_hint":    "Offshore wind share of revenue (decimal)",
                "clamp":           (0.0, 1.0),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "steel_plate_cost_sensitivity",
                "mandatory":       False,
                "group":           "Risk & Reserves",
                "search_phrases":  ['steel plate price', 'raw material costs'],
                "compute_hint":    "Steel plate cost or sensitivity, reporting currency",
                "clamp":           (0.0, 1000000000.0),
                "extractor_only":  True,
            },
        ],
    },
    "Agribusiness & Food (SG)": {
        "sector": "Consumer",
        "anchor_methods": ['Forward P/E', 'EV/EBITDA', 'DCF'],
        "source_priority": ['Quarterly results', 'Commodity price disclosure', 'Segment note'],
        "kpis": [
            {
                "key":             "crushing_margin",
                "mandatory":       True,
                "group":           "Profitability",
                "search_phrases":  ['crushing margin', 'crush margin', 'oilseeds margin', 'soybean crush'],
                "compute_hint":    "Crushing margin per tonne, reporting currency",
                "clamp":           (-500.0, 1000.0),
                "extractor_only":  True,
            },
            {
                "key":             "cpo_selling_price",
                "mandatory":       True,
                "group":           "Operations",
                "search_phrases":  ['CPO selling price', 'crude palm oil price', 'average selling price'],
                "compute_hint":    "CPO average selling price per tonne",
                "clamp":           (0.0, 100000.0),
                "extractor_only":  True,
            },
            {
                "key":             "same_store_sales_growth",
                "mandatory":       True,
                "group":           "Growth & Pipeline",
                "search_phrases":  ['Same-Store Sales Growth', 'SSSG', 'same store sales'],
                "compute_hint":    "Same-store sales growth (decimal)",
                "clamp":           (-0.4, 0.6),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "agri_gross_margin",
                "mandatory":       True,
                "group":           "Profitability",
                "search_phrases":  ['gross profit margin', 'gross margin', 'raw material hedging'],
                "compute_hint":    "Gross profit margin (decimal)",
                "clamp":           (0.0, 0.8),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "beverage_volume_mix",
                "mandatory":       False,
                "group":           "Operations",
                "search_phrases":  ['spirits volume', 'beer volume', 'volume breakdown'],
                "compute_hint":    "Spirits share of beverage volume (decimal)",
                "clamp":           (0.0, 1.0),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "revenue_per_sqft",
                "mandatory":       False,
                "group":           "Operations",
                "search_phrases":  ['revenue per sq ft', 'store count', 'new stores'],
                "compute_hint":    "Retail revenue per square foot",
                "clamp":           (0.0, 1000000.0),
                "extractor_only":  True,
            },
            {
                "key":             "consumer_pack_mix",
                "mandatory":       False,
                "group":           "Growth & Pipeline",
                "search_phrases":  ['consumer pack', 'bulk commodity sales mix', 'branded mix'],
                "compute_hint":    "Consumer-pack share of sales (decimal)",
                "clamp":           (0.0, 1.0),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "biological_asset_fv_change",
                "mandatory":       False,
                "group":           "Risk & Reserves",
                "search_phrases":  ['biological asset fair value', 'biological assets'],
                "compute_hint":    "Biological asset fair-value adjustment, reporting currency",
                "clamp":           (-100000000000.0, 100000000000.0),
                "extractor_only":  True,
            },
        ],
    },
    "Healthcare Provider (SG)": {
        "sector": "Healthcare",
        "anchor_methods": ['EV/EBITDA', 'DCF', 'Forward P/E'],
        "source_priority": ['Quarterly results', 'Hospital operating statistics', 'Capacity disclosure'],
        "kpis": [
            {
                "key":             "operational_bed_capacity",
                "mandatory":       True,
                "group":           "Operations",
                "search_phrases":  ['operational bed capacity', 'licensed beds', 'bed count'],
                "compute_hint":    "Operational bed capacity",
                "clamp":           (0.0, 20000.0),
                "extractor_only":  True,
            },
            {
                "key":             "bed_occupancy_rate",
                "mandatory":       True,
                "group":           "Operations",
                "search_phrases":  ['Bed Occupancy Rate', 'BOR', 'occupancy rate'],
                "compute_hint":    "Bed occupancy rate (decimal)",
                "clamp":           (0.0, 1.0),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "arpob",
                "mandatory":       True,
                "group":           "Profitability",
                "search_phrases":  ['ARPOB', 'average revenue per occupied bed', 'revenue per bed'],
                "compute_hint":    "Average revenue per occupied bed",
                "clamp":           (0.0, 1000000.0),
                "extractor_only":  True,
            },
            {
                "key":             "staff_cost_to_revenue",
                "mandatory":       True,
                "group":           "Profitability",
                "search_phrases":  ['staff costs-to-revenue', 'staff cost ratio', 'manpower cost'],
                "compute_hint":    "Staff costs / revenue (decimal)",
                "clamp":           (0.0, 1.0),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "hospital_ebitda_margin",
                "mandatory":       True,
                "group":           "Profitability",
                "search_phrases":  ['EBITDA margin per hospital', 'cluster EBITDA margin', 'EBITDA margin'],
                "compute_hint":    "EBITDA margin per hospital or cluster (decimal)",
                "clamp":           (-0.3, 0.6),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "medical_tourism_volume",
                "mandatory":       False,
                "group":           "Growth & Pipeline",
                "search_phrases":  ['medical tourism', 'foreign patient volume', 'overseas patients'],
                "compute_hint":    "Foreign medical-tourism patient volume",
                "clamp":           (0.0, 10000000.0),
                "extractor_only":  True,
            },
            {
                "key":             "specialist_headcount",
                "mandatory":       False,
                "group":           "Growth & Pipeline",
                "search_phrases":  ['doctor headcount', 'specialist clinic', 'specialists'],
                "compute_hint":    "Specialist / doctor headcount",
                "clamp":           (0.0, 10000.0),
                "extractor_only":  True,
            },
            {
                "key":             "preopening_capex",
                "mandatory":       False,
                "group":           "Capital",
                "search_phrases":  ['pre-opening capex', 'gestation drag', 'ramp-up costs'],
                "compute_hint":    "Pre-opening capex, reporting currency",
                "clamp":           (0.0, 100000000000.0),
                "extractor_only":  True,
            },
            {
                "key":             "day_surgery_volume",
                "mandatory":       False,
                "group":           "Operations",
                "search_phrases":  ['day surgery', 'outpatient consultation volume', 'outpatient visits'],
                "compute_hint":    "Day surgery / outpatient volume",
                "clamp":           (0.0, 10000000.0),
                "extractor_only":  True,
            },
        ],
    },
    "WealthTech & Specialty Financials (SG)": {
        "sector": "Financials",
        "anchor_methods": ['P/E (norm)', 'SOTP (published)', 'P/BV'],
        "source_priority": ['Quarterly results', 'AUA disclosure', 'Segment note'],
        "kpis": [
            {
                "key":             "assets_under_administration",
                "mandatory":       True,
                "group":           "Growth & Pipeline",
                "search_phrases":  ['Assets Under Administration', 'AUA', 'assets under administration'],
                "compute_hint":    "Assets under administration, reporting currency",
                "clamp":           (0.0, 10000000000000.0),
                "extractor_only":  True,
            },
            {
                "key":             "aua_net_inflows",
                "mandatory":       True,
                "group":           "Growth & Pipeline",
                "search_phrases":  ['net inflows', 'gross inflows', 'net new money'],
                "compute_hint":    "Net inflows to AUA, reporting currency",
                "clamp":           (-1000000000000.0, 1000000000000.0),
                "extractor_only":  True,
            },
            {
                "key":             "net_revenue_margin_aua",
                "mandatory":       True,
                "group":           "Profitability",
                "search_phrases":  ['net revenue margin', 'take-rate', 'revenue margin on AUA'],
                "compute_hint":    "Net revenue margin on AUA (decimal)",
                "clamp":           (0.0, 0.05),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "platform_cash_investments",
                "mandatory":       True,
                "group":           "Capital",
                "search_phrases":  ['cash & short-term investments', 'cash and short term investments'],
                "compute_hint":    "Cash and short-term investments, reporting currency",
                "clamp":           (0.0, 1000000000000.0),
                "extractor_only":  True,
            },
            {
                "key":             "platform_npl_ratio",
                "mandatory":       True,
                "group":           "Risk & Reserves",
                "search_phrases":  ['non-performing loans', 'NPL', 'default provisions'],
                "compute_hint":    "Non-performing loan ratio on the lending book (decimal)",
                "clamp":           (0.0, 0.3),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "digital_bank_deposits",
                "mandatory":       False,
                "group":           "Growth & Pipeline",
                "search_phrases":  ['digital bank deposit base', 'gross loans', 'deposit base'],
                "compute_hint":    "Digital-bank deposit base, reporting currency",
                "clamp":           (0.0, 1000000000000.0),
                "extractor_only":  True,
            },
            {
                "key":             "b2b_aua_share",
                "mandatory":       False,
                "group":           "Operations",
                "search_phrases":  ['B2B vs B2C', 'B2B AUA', 'channel split'],
                "compute_hint":    "B2B share of AUA (decimal)",
                "clamp":           (0.0, 1.0),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "portfolio_nav",
                "mandatory":       False,
                "group":           "Capital",
                "search_phrases":  ['private equity portfolio NAV', 'debt portfolio NAV', 'investment portfolio'],
                "compute_hint":    "Investment portfolio NAV, reporting currency",
                "clamp":           (0.0, 1000000000000.0),
                "extractor_only":  True,
            },
        ],
    },
    "Specialised Accommodation (SG)": {
        "sector": "Property",
        "anchor_methods": ['NAV', 'EV/EBITDA', 'SOTP (published)'],
        "source_priority": ['Results presentation', 'Bed capacity disclosure', 'Valuation report'],
        "kpis": [
            {
                "key":             "pbwa_bed_capacity",
                "mandatory":       True,
                "group":           "Operations",
                "search_phrases":  ['Purpose-Built Workers Accommodation', 'PBWA', 'bed capacity', 'workers accommodation'],
                "compute_hint":    "PBWA operational bed capacity",
                "clamp":           (0.0, 500000.0),
                "extractor_only":  True,
            },
            {
                "key":             "pbsa_bed_capacity",
                "mandatory":       True,
                "group":           "Operations",
                "search_phrases":  ['PBSA', 'student accommodation', 'operational beds'],
                "compute_hint":    "Student accommodation operational beds",
                "clamp":           (0.0, 500000.0),
                "extractor_only":  True,
            },
            {
                "key":             "accommodation_occupancy",
                "mandatory":       True,
                "group":           "Operations",
                "search_phrases":  ['average occupancy rate', 'occupancy'],
                "compute_hint":    "Average occupancy (decimal)",
                "clamp":           (0.0, 1.0),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "revpab",
                "mandatory":       True,
                "group":           "Profitability",
                "search_phrases":  ['RevPAH', 'RevPAB', 'revenue per available bed', 'revenue per available head'],
                "compute_hint":    "Revenue per available bed",
                "clamp":           (0.0, 100000.0),
                "extractor_only":  True,
            },
            {
                "key":             "accommodation_net_gearing",
                "mandatory":       True,
                "group":           "Risk & Reserves",
                "search_phrases":  ['net gearing', 'fair value gains', 'net debt to equity'],
                "compute_hint":    "Net debt / equity (decimal)",
                "clamp":           (0.0, 3.0),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "master_lease_mix",
                "mandatory":       False,
                "group":           "Operations",
                "search_phrases":  ['master lease', 'owned asset mix', 'leasehold vs freehold'],
                "compute_hint":    "Master-leased share of the portfolio (decimal)",
                "clamp":           (0.0, 1.0),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "land_lease_expiry_years",
                "mandatory":       False,
                "group":           "Risk & Reserves",
                "search_phrases":  ['lease expiry', 'land plot lease', 'tenure remaining'],
                "compute_hint":    "Weighted remaining land lease in years",
                "clamp":           (0.0, 99.0),
                "extractor_only":  True,
            },
            {
                "key":             "coliving_rental_reversion",
                "mandatory":       False,
                "group":           "Growth & Pipeline",
                "search_phrases":  ['rental reversion', 'co-living reversion'],
                "compute_hint":    "Residential / co-living rental reversion (decimal)",
                "clamp":           (-0.4, 0.6),
                "extractor_only":  True,
                "decimal_format":  True,
            },
        ],
    },
    "Real Estate Agency (SG)": {
        "sector": "Property",
        "anchor_methods": ['Forward P/E', 'DDM', 'DCF'],
        "source_priority": ['Quarterly results', 'URA / HDB transaction statistics', 'Agent count disclosure'],
        "kpis": [
            {
                "key":             "agency_transaction_volume",
                "mandatory":       True,
                "group":           "Operations",
                "search_phrases":  ['transaction volume', 'new launch', 'resale', 'HDB transactions'],
                "compute_hint":    "Property transaction volume handled",
                "clamp":           (0.0, 10000000.0),
                "extractor_only":  True,
            },
            {
                "key":             "agent_headcount",
                "mandatory":       True,
                "group":           "Operations",
                "search_phrases":  ['salesforce', 'agent headcount', 'agent count'],
                "compute_hint":    "Salesforce / agent headcount",
                "clamp":           (0.0, 100000.0),
                "extractor_only":  True,
            },
            {
                "key":             "commission_take_rate",
                "mandatory":       True,
                "group":           "Profitability",
                "search_phrases":  ['commission split', 'take-rate', 'commission margin'],
                "compute_hint":    "Firm's share of gross commission (decimal)",
                "clamp":           (0.0, 1.0),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "net_cash_per_share",
                "mandatory":       True,
                "group":           "Capital",
                "search_phrases":  ['net cash balance per share', 'net cash per share', 'cash per share'],
                "compute_hint":    "Net cash per share, reporting currency",
                "clamp":           (0.0, 100.0),
                "extractor_only":  True,
            },
            {
                "key":             "launch_pipeline_units",
                "mandatory":       False,
                "group":           "Growth & Pipeline",
                "search_phrases":  ['pipeline of upcoming condo launches', 'upcoming launches', 'units launching'],
                "compute_hint":    "Units in the upcoming launch pipeline",
                "clamp":           (0.0, 1000000.0),
                "extractor_only":  True,
            },
            {
                "key":             "hdb_mop_supply",
                "mandatory":       False,
                "group":           "Growth & Pipeline",
                "search_phrases":  ['HDB MOP', 'Minimum Occupation Period', 'MOP supply'],
                "compute_hint":    "HDB flats reaching MOP",
                "clamp":           (0.0, 1000000.0),
                "extractor_only":  True,
            },
            {
                "key":             "project_marketing_share",
                "mandatory":       False,
                "group":           "Operations",
                "search_phrases":  ['market share in project marketing', 'project marketing share'],
                "compute_hint":    "Share of project marketing mandates (decimal)",
                "clamp":           (0.0, 1.0),
                "extractor_only":  True,
                "decimal_format":  True,
            },
        ],
    },
    "Offshore Marine & Resources (SG)": {
        "sector": "Industrials",
        "anchor_methods": ['EV/EBITDA', 'P/BV', 'DCF'],
        "source_priority": ['Quarterly results', 'Charter / order announcements', 'Production reports'],
        "kpis": [
            {
                "key":             "charter_day_rate",
                "mandatory":       True,
                "group":           "Profitability",
                "search_phrases":  ['charter day rates', 'day rate', 'charter rate'],
                "compute_hint":    "Average charter day rate, reporting currency",
                "clamp":           (0.0, 1000000.0),
                "extractor_only":  True,
            },
            {
                "key":             "fleet_utilisation",
                "mandatory":       True,
                "group":           "Operations",
                "search_phrases":  ['fleet utilisation rate', 'utilization rate', 'vessel utilisation'],
                "compute_hint":    "Fleet utilisation (decimal)",
                "clamp":           (0.0, 1.0),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "yard_order_intake",
                "mandatory":       True,
                "group":           "Growth & Pipeline",
                "search_phrases":  ['order intake', 'firm yard backlog', 'orderbook', 'fabrication backlog'],
                "compute_hint":    "Order intake / firm yard backlog, reporting currency",
                "clamp":           (0.0, 1000000000000.0),
                "extractor_only":  True,
            },
            {
                "key":             "cash_cost_per_unit",
                "mandatory":       True,
                "group":           "Profitability",
                "search_phrases":  ['cash cost of production per tonne', 'cash cost per boe', 'production cost'],
                "compute_hint":    "Cash cost of production per tonne or boe",
                "clamp":           (0.0, 10000.0),
                "extractor_only":  True,
            },
            {
                "key":             "average_freight_rate",
                "mandatory":       False,
                "group":           "Profitability",
                "search_phrases":  ['average freight rate', 'freight rate'],
                "compute_hint":    "Average freight rate",
                "clamp":           (0.0, 1000000.0),
                "extractor_only":  True,
            },
            {
                "key":             "drydocking_capex",
                "mandatory":       False,
                "group":           "Capital",
                "search_phrases":  ['dry-docking schedule', 'maintenance capex', 'drydocking'],
                "compute_hint":    "Dry-docking / maintenance capex, reporting currency",
                "clamp":           (0.0, 100000000000.0),
                "extractor_only":  True,
            },
            {
                "key":             "reserve_replacement",
                "mandatory":       False,
                "group":           "Risk & Reserves",
                "search_phrases":  ['reserve replacement ratio', '2P reserves', 'coal reserves'],
                "compute_hint":    "Reserve replacement ratio (x)",
                "clamp":           (0.0, 5.0),
                "extractor_only":  True,
            },
            {
                "key":             "yard_capacity_sqm",
                "mandatory":       False,
                "group":           "Operations",
                "search_phrases":  ['shipyard fabrication capacity', 'yard capacity'],
                "compute_hint":    "Shipyard fabrication capacity in square metres",
                "clamp":           (0.0, 100000000.0),
                "extractor_only":  True,
            },
        ],
    },
    "Packaged Consumer & Lifestyle (SG)": {
        "sector": "Consumer",
        "anchor_methods": ['Forward P/E', 'EV/EBITDA', 'DCF'],
        "source_priority": ['Quarterly results', 'Segment note', 'Commodity cost disclosure'],
        "kpis": [
            {
                "key":             "lifestyle_sssg",
                "mandatory":       True,
                "group":           "Growth & Pipeline",
                "search_phrases":  ['Same-Store Sales Growth', 'SSSG', 'same store sales'],
                "compute_hint":    "Same-store sales growth (decimal)",
                "clamp":           (-0.4, 0.6),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "lifestyle_gross_margin",
                "mandatory":       True,
                "group":           "Profitability",
                "search_phrases":  ['gross profit margin', 'gross margin'],
                "compute_hint":    "Gross profit margin (decimal)",
                "clamp":           (0.0, 0.8),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "raw_material_cost_index",
                "mandatory":       True,
                "group":           "Risk & Reserves",
                "search_phrases":  ['raw material costs', 'coffee beans', 'cocoa', 'sugar'],
                "compute_hint":    "Key raw material cost per unit",
                "clamp":           (0.0, 1000000.0),
                "extractor_only":  True,
            },
            {
                "key":             "fx_translation_impact",
                "mandatory":       True,
                "group":           "Risk & Reserves",
                "search_phrases":  ['FX translation impact', 'currency translation', 'RUB', 'IDR'],
                "compute_hint":    "FX translation impact on revenue (decimal)",
                "clamp":           (-1.0, 1.0),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "boutique_count",
                "mandatory":       False,
                "group":           "Operations",
                "search_phrases":  ['boutique', 'point of sale network', 'store count'],
                "compute_hint":    "Boutique / point-of-sale count",
                "clamp":           (0.0, 100000.0),
                "extractor_only":  True,
            },
            {
                "key":             "inventory_turnover_days",
                "mandatory":       False,
                "group":           "Operations",
                "search_phrases":  ['inventory aging', 'inventory turnover days', 'stock turn'],
                "compute_hint":    "Inventory turnover in days",
                "clamp":           (0.0, 1000.0),
                "extractor_only":  True,
            },
            {
                "key":             "brand_allocation_status",
                "mandatory":       False,
                "group":           "Growth & Pipeline",
                "search_phrases":  ['brand allocation', 'authorized retailer', 'authorised dealer'],
                "compute_hint":    "Share of revenue from allocated premium brands (decimal)",
                "clamp":           (0.0, 1.0),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "ap_spend_ratio",
                "mandatory":       False,
                "group":           "Profitability",
                "search_phrases":  ['advertising & promotional', 'A&P spend', 'marketing spend ratio'],
                "compute_hint":    "A&P spend / revenue (decimal)",
                "clamp":           (0.0, 0.4),
                "extractor_only":  True,
                "decimal_format":  True,
            },
        ],
    },
})


# ── AI Infrastructure / Neocloud KPI spec ───────────────────────────────
# Drawn from the Goldman notes on CoreWeave, Nebius and Nvidia (Aug 2026).
# The critical four are the ones without which no forward revenue line can
# be built: secured power caps capacity, backlog is the revenue already
# sold, capex intensity decides whether it can be financed, and gross
# margin decides whether it is worth building.
SECTOR_KPI_FRAMEWORK.update({
    "AI Infrastructure / Neocloud": {
        "sector": "Tech",
        "anchor_methods": ["EV/Revenue", "DCF", "EV/EBITDA"],
        "source_priority": ["Quarterly results and capacity disclosure",
                            "Power procurement announcements",
                            "Contracted backlog / RPO disclosure"],
        "kpis": [
            {
                "key":             "contracted_power_gw",
                "mandatory":       True,
                "group":           "Operations",
                "search_phrases":  ['contracted power', 'GW of contracted power', 'secured power', 'power procurement'],
                "compute_hint":    "Contracted / secured power capacity in GW",
                "clamp":           (0.0, 100.0),
                "extractor_only":  True,
            },
            {
                "key":             "contracted_revenue_backlog",
                "mandatory":       True,
                "group":           "Growth & Pipeline",
                "search_phrases":  ['contracted backlog', 'revenue backlog', 'RPO', 'remaining performance obligation'],
                "compute_hint":    "Contracted revenue backlog / RPO, reporting currency",
                "clamp":           (0.0, 1000000000000.0),
                "extractor_only":  True,
            },
            {
                "key":             "neocloud_capex_to_revenue",
                "mandatory":       True,
                "group":           "Capital",
                "search_phrases":  ['capex', 'capital expenditure', 'CapEx including finance leases'],
                "compute_hint":    "Capex (incl. finance leases) / revenue (decimal)",
                "clamp":           (0.0, 20.0),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "neocloud_gross_margin",
                "mandatory":       True,
                "group":           "Profitability",
                "search_phrases":  ['gross margin', 'gross profit margin'],
                "compute_hint":    "Gross margin (decimal)",
                "clamp":           (-0.50, 0.95),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "powered_land_options_gw",
                "mandatory":       False,
                "group":           "Growth & Pipeline",
                "search_phrases":  ['powered land options', 'powered land', 'land bank power'],
                "compute_hint":    "Optioned but not yet contracted power, GW",
                "clamp":           (0.0, 100.0),
                "extractor_only":  True,
            },
            {
                "key":             "customer_prepayments",
                "mandatory":       False,
                "group":           "Capital",
                "search_phrases":  ['customer prepayments', 'prepayments', 'upfront payments'],
                "compute_hint":    "Customer prepayments received, reporting currency",
                "clamp":           (0.0, 1000000000000.0),
                "extractor_only":  True,
            },
            {
                "key":             "gpu_fleet_size",
                "mandatory":       False,
                "group":           "Operations",
                "search_phrases":  ['GPU fleet', 'GPUs deployed', 'accelerator count'],
                "compute_hint":    "Deployed GPU / accelerator count",
                "clamp":           (0.0, 100000000.0),
                "extractor_only":  True,
            },
            {
                "key":             "behind_meter_power_mw",
                "mandatory":       False,
                "group":           "Operations",
                "search_phrases":  ['behind-the-meter', 'behind the meter power', 'on-site generation'],
                "compute_hint":    "Behind-the-meter generation capacity, MW",
                "clamp":           (0.0, 100000.0),
                "extractor_only":  True,
            },
            {
                "key":             "neocloud_customer_concentration",
                "mandatory":       False,
                "group":           "Risk & Reserves",
                "search_phrases":  ['customer concentration', 'largest customer', 'top customer revenue'],
                "compute_hint":    "Revenue share of the largest customer (decimal)",
                "clamp":           (0.0, 1.0),
                "extractor_only":  True,
                "decimal_format":  True,
            },
            {
                "key":             "contract_duration_years",
                "mandatory":       False,
                "group":           "Risk & Reserves",
                "search_phrases":  ['contract duration', 'weighted average contract length', 'take-or-pay term'],
                "compute_hint":    "Weighted average contract duration in years",
                "clamp":           (0.0, 30.0),
                "extractor_only":  True,
            },
        ],
    },
})

# ── Singapore REIT spec ─────────────────────────────────────────────────
# Derived from the "REIT" spec so the KPI set, extractor phrases and card
# layout stay in lockstep. Registered here because the valuation profile
# is named "S-REIT": without this entry build_extractor_schema("S-REIT")
# returns ZERO fields, so every SGX REIT would route to a profile whose
# extractor asks for nothing — the Singapore disclosure set (gearing,
# cost of debt, hedged proportion, rental reversion, DPU, and the DDM's
# own cost of equity and terminal growth) would never be captured.
if "REIT" in SECTOR_KPI_FRAMEWORK:
    import copy as _copy_sreit
    SECTOR_KPI_FRAMEWORK["S-REIT"] = _copy_sreit.deepcopy(
        SECTOR_KPI_FRAMEWORK["REIT"])

# Normalised (strip + casefold) index over SECTOR_KPI_FRAMEWORK keys so the
# card lookup tolerates whitespace / case drift between the router's
# profile_name string and the framework key (de-fragilizes exact-string match).
# Built once at import; collisions are vanishingly unlikely given the curated
# key set, and on collision the first registered key wins.
_FRAMEWORK_KEY_NORM: dict[str, str] = {}
for _pn in SECTOR_KPI_FRAMEWORK:
    _norm = " ".join(_pn.split()).casefold()
    _FRAMEWORK_KEY_NORM.setdefault(_norm, _pn)
del _pn, _norm  # keep module namespace clean

# D4: known short/legacy aliases → canonical framework keys, tried after
# exact + normalised match. "Hyperscaler" survives in cached pre-D4 profile
# strings; the framework key is "Hyperscaler / Tech Conglomerate".
_PROFILE_ALIASES: dict[str, str] = {
    "hyperscaler": "Hyperscaler / Tech Conglomerate",
}


def _resolve_profile_spec(profile_name: str) -> tuple[str | None, dict | None]:
    """Resolve a (possibly noisy) profile_name to its canonical framework key
    and spec. Returns (canonical_key, spec) or (None, None) if unresolvable.

    Tries exact match first, then a whitespace/case-normalised match, then
    the explicit alias table (D4). Logs a structured warning when a
    normalised/alias match was needed (signals upstream profile-string
    drift) or when the profile is unknown entirely."""
    if not profile_name:
        return None, None
    spec = SECTOR_KPI_FRAMEWORK.get(profile_name)
    if spec is not None:
        return profile_name, spec
    norm = " ".join(profile_name.split()).casefold()
    canon = _FRAMEWORK_KEY_NORM.get(norm)
    if canon is not None:
        _LOG.warning(
            "render_card_payload: profile_name %r matched %r only after "
            "normalisation — upstream profile string drift", profile_name, canon,
        )
        return canon, SECTOR_KPI_FRAMEWORK[canon]
    alias_canon = _PROFILE_ALIASES.get(norm)
    if alias_canon is not None and alias_canon in SECTOR_KPI_FRAMEWORK:
        _LOG.warning(
            "render_card_payload: legacy alias %r resolved to %r",
            profile_name, alias_canon,
        )
        return alias_canon, SECTOR_KPI_FRAMEWORK[alias_canon]
    return None, None


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
    if not profile_name:
        _LOG.info("render_card_payload(%s): empty profile_name → None", ticker)
        return None
    if is_legacy_profile(profile_name):
        # Expected path — legacy sub-profiles use their bespoke frontend card.
        return None

    canonical, spec = _resolve_profile_spec(profile_name)
    if not spec:
        _LOG.warning(
            "render_card_payload(%s): profile_name %r not in SECTOR_KPI_FRAMEWORK "
            "→ None (frontend will fall back / render nothing)", ticker, profile_name,
        )
        return None
    # Use the canonical key downstream so the payload reports the registered name.
    profile_name = canonical or profile_name

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
        fmt = _infer_kpi_format(kpi)
        decimals, unit = _FORMAT_META.get(fmt, (None, None))
        buckets[title]["kpis"].append({
            "key":       kpi["key"],
            "label":     _kpi_label(kpi),
            "value":     value,
            "format":    fmt,
            "decimals":  decimals,
            "unit":      unit,
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

    return {
        "ticker":         ticker,
        "sector":         spec.get("sector", ""),
        "profile_name":   profile_name,
        "sub_profile":    sub_sub or None,
        "anchor_methods": list(spec.get("anchor_methods", [])),
        "groups":         groups,
        "source_priority": list(spec.get("source_priority", [])),
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
]
