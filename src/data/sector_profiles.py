"""
src/data/sector_profiles.py — Structured sector metadata for the advanced pipeline.

Four responsibilities:
  1. WACC per sector (Damodaran-informed base rates + leverage adjustment)
     → consumed by dcf_agent.py (Step 4) for discounting
  2. Terminal growth rates per scenario (bear/base/bull)
     → consumed by dcf_agent.py for Gordon Growth terminal value
  3. Structured signal metadata (stack layers, key metrics, macro linkages)
     → consumed by dcf_agent.py for FCF floor checks
     → available to specialist.py for future industry brief enrichment
  4. INDUSTRY_VALUATION_PROFILES — Master JSON Map (from Ultimate_Valuation_Master_2026.xlsx)
     → consumed by dcf_agent.py Phase 4.5 upgrade for multi-method blended IV
     → maps pipeline sector → company profile → methods + weights + excluded methods

Keys match StrategicRouterOutput.sector Literal exactly (Title Case):
  Consumer | Tech | Biopharma | Telco | Crypto | Energy | Financials | Industrials

WACC sources — Damodaran January 2026 (updated 2026-03-26):
  Source: Aswath Damodaran, NYU Stern — wacc.xls (January 5 2026)
  URL:    https://pages.stern.nyu.edu/~adamodar/New_Home_Page/data.html
  Parameters used in Damodaran's Jan-2026 dataset:
    Risk-free rate (Rf):     3.95%  (10-yr US Treasury as of Jan 2026)
    Equity Risk Premium:     4.46%  (implied ERP, Damodaran Jan 2026)
    Marginal tax rate:       25%
    Leverage:                market-value D/(D+E) aggregated by industry
  Leverage premium (this model): +1bp per 0.1x D/E above 1.5x threshold;
    capped by sector-specific maximum to prevent runaway discounting.

Key corrections vs prior version (2026-03-26 recalibration):
  Financials:  11.0% → 6.0%  — prior rate ignored deposit leverage (D/(D+E)=62%);
                                 Damodaran Money Center Banks = 4.98%
  Telco:        8.0% → 5.5%  — prior ignored high leverage (D/(D+E)=34–60%);
                                 Damodaran Telecom Services = 5.39%
  Biopharma:   10.0% → 8.5%  — stage risk (Ph1/Ph2) belongs in rNPV PoS discount,
                                 NOT in WACC; Damodaran Drugs Biotech = 8.49%
  Consumer:     8.5% → 7.5%  — blended staples/discretionary; Damo Food = 5.79%,
                                 Discretionary avg = 7–9%
  Industrials:  8.5% → 8.0%  — Damo Aerospace 7.60%, Machinery 7.70%
  Energy sub-types: recalibrated — see _ENERGY_PROFILE_WACC
  Financials sub-types: added — see _FINANCIALS_PROFILE_WACC
"""

# ── 1. WACC ───────────────────────────────────────────────────────────────────

# Base WACC rates by sector (pre-leverage-adjustment).
# Source: Damodaran January 2026 wacc.xls; sector mapped to closest industry group(s).
# These are blended midpoints across sub-sectors — see sub-type overrides below
# for Energy and Financials where within-sector dispersion is material (>300bps).
SECTOR_WACC: dict[str, float] = {
    # Damo: Software Sys&App 9.34%, Internet 10.66%, Semiconductor 10.55%, Hardware 9.71%
    "Tech":                0.095,
    # Damo: Food Processing 5.79%, Discretionary (Apparel 7.13%, Auto 9.38%), Retail Gen 7.27%
    # Blended midpoint for staples/discretionary mix; sub-sector spread handled via profile
    "Consumer":            0.075,
    # Damo: Drugs Pharma 7.85%, Drugs Biotech 8.49%, Healthcare Products 7.54%
    # NOTE: Phase 1/2 biotech — do NOT inflate WACC for clinical risk.
    # Stage risk is captured in rNPV PoS discounts (Ph1=63%, Ph2=31%, Ph3=58%).
    # WACC applies only to revenue-generating or Phase-3+ firms.
    "Biopharma":           0.085,
    # Damo: Telecom Services 5.39%, Telecom Wireless 5.48%, Cable TV 5.20%
    # High leverage (D/(D+E) = 34–60%) compresses WACC despite moderate equity risk
    "Telco":               0.055,
    # No Damodaran consensus; crypto miners closest to Coal (8.41%) but with far higher
    # vol and regulatory risk. 15% is a conservative floor; use scenario probabilities
    # to capture tail risk rather than inflating WACC further.
    "Crypto":              0.150,
    # Fallback for unclassified Energy; sub-types handled by _ENERGY_PROFILE_WACC.
    # Damo: Oil/Gas Production 6.25%, Green Renewable 6.04%, Power 5.01%
    "Energy":              0.065,
    # Fallback for unclassified Financials; sub-types in _FINANCIALS_PROFILE_WACC.
    # Damo: Money Center Banks 4.98%, Asset Mgmt 6.13%, Insurance P/C 5.78%
    # NOTE: traditional leverage premium must NOT be applied to banks — deposit funding
    # is their business model, already priced into the 4.98% empirical WACC.
    "Financials":          0.060,
    # Damo: Aerospace/Defense 7.60%, Machinery 7.70%, Engineering/Construction 8.69%
    "Industrials":         0.080,
    # Damo: R.E.I.T. 5.32%, Real Estate Development 5.82%
    "RealEstate":          0.055,
    # SGX REITs and Business Trusts — same as RealEstate (REIT sub-segment).
    # REITs have high leverage (typically 35-45% LTV) and high distribution payout,
    # compressing equity volatility and WACC despite rate sensitivity.
    "REIT":                0.055,
    # Damo: Transportation (railroads) 7.27%, Trucking 7.52%, Air Transport 6.72%
    "Transportation":      0.072,
    # Damo: Metals & Mining 8.20%, Chemical Basic 6.22%, Chemical Specialty 7.25%
    "Materials":           0.075,
    # Damo: Oil/Gas Production 6.25%, Coal 8.41%, Precious Metals 7.47%
    "Resources":           0.070,
    # Damo: Business & Consumer Services 7.23%, Advertising 7.81%
    "ProfessionalServices": 0.075,
    # Damo: Healthcare Support Services ~8.25%; managed care has moderate leverage
    # and government contract risk. Higher than Biopharma due to margin compression risk.
    "HealthcareServices":   0.082,
    # Damo: Semiconductor 8.81%, Semiconductor Equip 8.61%
    # Cyclical earnings with heavy CapEx (fabs) but strong EBITDA margins.
    # Separate from Tech (software/platform) because CapEx intensity, margin volatility,
    # and cyclical demand patterns require different valuation methods.
    "Semiconductor":        0.088,
}


_MACRO_WACC_OVERLAY: dict[str, float] = {
    "risk-off":  +0.015,   # +150 bps — widen equity risk premium in risk-off regimes
    "neutral":    0.000,
    "risk-on":  -0.005,    # -50 bps — compress ERP slightly in risk-on regimes
}

# ── Energy sub-type WACC overrides ────────────────────────────────────────────
# Source: Damodaran January 2026 wacc.xls (recalibrated 2026-03-26).
# Prior values (Regulated Utility 7.5%, IPP 8.0%, Merchant 9.5%) were ~250–350bps
# too high because they did not reflect the high D/(D+E) of regulated/contracted
# utilities (45–53% leverage) which suppresses WACC via cheap regulated debt.
#
# Damodaran anchors (Jan 2026, Rf=3.95%, ERP=4.46%):
#   Utility (General):        4.36%  D/(D+E)=44.9%
#   Green & Renewable Energy: 6.04%  D/(D+E)=53.1%
#   Power (IPPs, broad):      5.01%  D/(D+E)=42.6%
#   Engineering/Construction: 8.69%  D/(D+E)=12.3%
#   Oil/Gas Production & Exp: 6.25%  D/(D+E)=27.3%
_ENERGY_PROFILE_WACC: dict[str, float] = {
    "Regulated Utility": 0.045,  # fully regulated; predictable allowed RoE; Damo 4.36%
    "IPP":               0.060,  # PPA-backed; semi-regulated visible cash flows; Damo 6.04%
    "Merchant Power":    0.065,  # investment-grade IPPs w/ nuclear+PPA: 7.5-8.2% per Gemini; base 6.5% + overlays
    "EPC Contractor":    0.087,  # project execution risk; Damo Eng/Construction 8.69%
}

# Leverage premium caps by Energy sub-type.
# Regulated/PPA-backed utilities carry structural leverage (high D/(D+E)) as part of
# their capital model — cap the incremental premium tightly to avoid double-counting.
_ENERGY_LEVERAGE_CAP: dict[str, float] = {
    "Regulated Utility": 0.015,  # regulatory oversight limits excess risk; tight cap
    "IPP":               0.020,  # PPA visibility compresses the max addendum
    "Merchant Power":    0.035,  # full commodity exposure → wider cap
    "EPC Contractor":    0.030,
}

# Contracted-revenue WACC discount for Merchant Power / IPP companies.
# When deep research or industry brief confirms significant contracted revenue
# (PPAs, behind-the-meter deals, nuclear offtake agreements), the effective risk
# profile shifts closer to IPP/Regulated — discount the WACC accordingly.
# Applied in dcf_agent.py after all other WACC adjustments.
CONTRACTED_REVENUE_WACC_DISCOUNT: float = -0.0125  # -125 bps
CONTRACTED_REVENUE_KEYWORDS: list[str] = [
    "ppa", "power purchase agreement", "behind-the-meter", "contracted revenue",
    "offtake agreement", "long-term contract", "nuclear ppa", "hyperscaler ppa",
    "capacity auction", "capacity payment", "tolling agreement",
]

# ── Financials sub-type WACC overrides ───────────────────────────────────────
# Source: Damodaran January 2026 wacc.xls (added 2026-03-26).
# Within-sector dispersion is >500bps (Banks 4.98% vs FinTech ~10%), so a flat
# sector WACC produces material valuation errors.
#
# CRITICAL — leverage premium for banks:
#   Traditional D/E leverage premiums must NOT be applied to deposit-funded banks.
#   A bank's D/(D+E) of 62% reflects deposit funding (its business model), already
#   embedded in Damodaran's empirical 4.98% WACC. Applying an additional leverage
#   premium on top would double-count this effect and overstate WACC by 4–6%.
#   Use _FINANCIALS_LEVERAGE_CAP = 0.010 for all bank/insurance sub-types.
#
# Damodaran anchors (Jan 2026):
#   Bank (Money Center):              4.98%  D/(D+E)=62.1%
#   Banks (Regional):                 4.98%  D/(D+E)=34.3%
#   Insurance (Prop/Cas.):            5.78%  D/(D+E)=12.9%
#   Insurance (Life):                 5.60%  D/(D+E)=40.4%
#   Investments & Asset Management:   6.13%  D/(D+E)=24.6%
#   Brokerage & Investment Banking:   6.08%  D/(D+E)=57.6%
#   Financial Svcs (Non-bank):        5.00%  D/(D+E)=73.1%
_FINANCIALS_PROFILE_WACC: dict[str, float] = {
    # ── Exact keys returned by classify_valuation_profile() ──────────────────
    # Pipeline auto-routing lands here; these names must match INDUSTRY_VALUATION_PROFILES exactly.
    "Bank / Lending Institution": 0.050,  # Damo Money Center 4.98%, Regional 4.98%; blended 5.0%
    "Insurance":                  0.058,  # Damo P/C 5.78%, Life 5.60%; blended 5.8%
    "Alt Asset Manager":          0.085,  # Beta ~1.5-2.0; Gemini: 12-13% all-in; base 8.5% + overlays
    "Holding Company":            0.065,  # conglomerate/holding; blended above bank base
    # ── Descriptive aliases (for direct profile= override calls) ─────────────
    # These allow callers to pass a descriptive profile without knowing classifier output.
    "Money Center Bank":    0.050,  # Damo 4.98%; deposit leverage already embedded
    "Regional Bank":        0.050,  # Damo 4.98%
    "Asset Manager":        0.062,  # Damo Investments & Asset Mgmt 6.13%
    "Investment Bank":      0.062,  # Damo Brokerage & Inv Banking 6.08%
    "FinTech":              0.090,  # no Damo direct; proxy Brokerage + growth premium
    "Mortgage/GSE":         0.065,  # GSE conservatorship binary risk; above bank base
    "Payment Networks":     0.070,  # toll-road monopoly; low beta, premium to bank base
    "Market Infrastructure": 0.065,  # exchange monopoly; similar to holding company
    "Brokerage":            0.060,  # deposit-funded; between bank and asset manager
}

# Cap for leverage premium in Financials — near-zero for deposit-funded entities
# (deposit leverage is their business model, already priced into Damodaran's 4.98%);
# slightly wider for asset managers and fintech which use traditional leverage.
_FINANCIALS_LEVERAGE_CAP: dict[str, float] = {
    "Bank / Lending Institution": 0.010,
    "Insurance":                  0.015,
    "Alt Asset Manager":          0.025,
    "Holding Company":            0.020,
    "Money Center Bank":          0.010,
    "Regional Bank":              0.010,
    "Asset Manager":              0.025,
    "Investment Bank":            0.020,
    "FinTech":                    0.035,
    "Mortgage/GSE":               0.015,
    "Payment Networks":           0.025,
    "Market Infrastructure":      0.020,
    "Brokerage":                  0.025,
}


def get_wacc(sector: str, leverage: float = 0.0,
             macro_regime: str = "neutral", profile: str = "") -> float:
    """
    Return sector WACC adjusted for company-level leverage and macro regime.

    leverage: net_debt / shareholders_equity from the balance sheet.
    Premium starts at debt/equity > 1.5x, adding 1bp per 0.1x above that threshold,
    capped by a sector/profile-specific maximum to prevent runaway discounting.

    macro_regime: "risk-off" | "neutral" | "risk-on" — sourced from macro_regime agent.
      risk-off adds +150 bps (widen ERP); risk-on subtracts 50 bps.

    profile: optional valuation profile name.
      Energy:     "Regulated Utility" | "IPP" | "Merchant Power" | "EPC Contractor"
      Financials: "Money Center Bank" | "Regional Bank" | "Insurance" |
                  "Asset Manager" | "Investment Bank" | "FinTech" | "Mortgage/GSE" |
                  "Payment Networks" | "Market Infrastructure" | "Brokerage"
      For these sectors, profile-specific base rates and leverage caps replace
      the sector fallback. Unrecognised profiles fall back to the sector base.

    Source: Damodaran January 2026 (Rf=3.95%, ERP=4.46%, tax=25%).
    Returns base WACC when sector is unrecognised (safe default = 9%).
    Backward-compatible: profile="" behaves identically to the prior two-arg signature.
    """
    if sector == "Energy" and profile in _ENERGY_PROFILE_WACC:
        base    = _ENERGY_PROFILE_WACC[profile]
        lev_cap = _ENERGY_LEVERAGE_CAP.get(profile, 0.035)
    elif sector == "Financials" and profile in _FINANCIALS_PROFILE_WACC:
        base    = _FINANCIALS_PROFILE_WACC[profile]
        lev_cap = _FINANCIALS_LEVERAGE_CAP.get(profile, 0.010)
    else:
        base    = SECTOR_WACC.get(sector, 0.090)
        lev_cap = 0.040
    # REITs and Business Trusts have high leverage by design (35-45% LTV is standard).
    # Do NOT apply leverage premium — the 5.5% empirical WACC already reflects this.
    if sector in ("REIT", "RealEstate"):
        leverage_premium = 0.0
    else:
        leverage_premium = max(0.0, (leverage - 1.5) * 0.01)
    overlay = _MACRO_WACC_OVERLAY.get(macro_regime, 0.0)
    return round(min(base + leverage_premium + overlay, base + lev_cap), 4)


# ── 1b. Synthetic credit rating + cost of debt ────────────────────────────────
# Damodaran's synthetic rating: interest coverage ratio → letter rating.
# Two tables because FINANCIAL service firms have structurally lower interest
# coverage (deposits are their business model) and require much looser thresholds
# for the same rating. The distinction is non-financial vs financial, NOT
# large-vs-small cap.
#
# FRED free-tier OAS covers 7 rating-aggregate buckets {AAA, AA, A, BBB, BB, B,
# CCC & Lower}; we collapse Damodaran's +/- modifiers into those 7 buckets and
# map anything below CCC (CC, C, D) to CCC since that's the FRED floor.
#
# Source: Aswath Damodaran, January 2026 synthetic-rating table:
#   pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ratings.htm
# Spreads in the original table are included in _FALLBACK_SPREAD_BPS below;
# live spreads come from FRED (see FRED_RATING_SERIES).

# Non-financial service firms (industrials, tech, consumer, energy, etc.)
# Key: lower-bound of interest coverage ratio; first matching band wins.
_RATING_NON_FINANCIAL: list[tuple[float, str]] = [
    (8.50,  "AAA"),    # Aaa/AAA              (coverage > 8.5)
    (6.50,  "AA"),     # Aa2/AA               (6.5 – 8.5)
    (3.00,  "A"),      # collapses A+ / A / A- (3.0 – 6.5)
    (2.50,  "BBB"),    # Baa2/BBB             (2.5 – 3.0)
    (2.00,  "BB"),     # collapses BB / BB+   (2.0 – 2.5)
    (1.25,  "B"),      # collapses B+ / B / B- (1.25 – 2.0)
    (0.00,  "CCC"),    # collapses CCC / CC / C / D into FRED floor
]

# Financial service firms (banks, insurers, asset managers, REITs if treated
# financially). Coverage thresholds are materially looser — a bank at 3x long-
# term interest coverage is AAA-quality by any honest measure because that
# structure is the business model, not a sign of distress.
_RATING_FINANCIAL: list[tuple[float, str]] = [
    (3.00,  "AAA"),    # Aaa/AAA              (coverage > 3.0)
    (2.50,  "AA"),     # Aa2/AA               (2.5 – 3.0)
    (1.20,  "A"),      # collapses A+ / A / A- (1.2 – 2.5)
    (0.90,  "BBB"),    # Baa2/BBB             (0.9 – 1.2)
    (0.60,  "BB"),     # collapses BB / BB+   (0.6 – 0.9)
    (0.30,  "B"),      # collapses B+ / B / B- (0.3 – 0.6)
    (0.00,  "CCC"),    # collapses CCC / CC / C / D
]

# FRED ICE BofA Option-Adjusted Spread series (rating-aggregate, all sectors).
# Values are in percentage points (e.g. 1.01 means 101 bps).
# Source: https://fred.stlouisfed.org/release?rid=209
FRED_RATING_SERIES: dict[str, str] = {
    "AAA": "BAMLC0A1CAAA",
    "AA":  "BAMLC0A2CAA",
    "A":   "BAMLC0A3CA",
    "BBB": "BAMLC0A4CBBB",
    "BB":  "BAMLH0A1HYBB",
    "B":   "BAMLH0A2HYB",
    "CCC": "BAMLH0A3HYC",
}

# Credit-bucket assignment. FRED free-tier only publishes rating-aggregate OAS
# (no sector × rating cuts), so we apply a static structural multiplier to the
# aggregate to approximate industrial / financial / utility spread premia.
# Multipliers derived from long-run ICE BofA sector-vs-aggregate ratios (2010-
# 2024 average at IG level). They are stable within ±10% outside crisis windows.
SECTOR_CREDIT_MULTIPLIERS: dict[str, float] = {
    "Industrial": 1.00,   # baseline (by construction)
    "Financial":  1.15,   # banks/insurers trade wider at same rating
    "Utility":    0.80,   # regulated cash flows trade tighter
}

# Sector / profile → credit bucket. Energy is profile-aware because regulated
# utilities and merchant power have very different credit profiles even though
# both live under the "Energy" sector.
_CREDIT_BUCKET_MAP: dict[str, str] = {
    # Financial bucket
    "Financials":             "Financial",
    # Utility-like bucket
    "RealEstate":             "Utility",   # REITs: regulated-like, high LTV by design
    "REIT":                   "Utility",
    # Industrial (default) bucket — everything else
    "Consumer":               "Industrial",
    "Tech":                   "Industrial",
    "Biopharma":              "Industrial",
    "Telco":                  "Industrial",
    "Crypto":                 "Industrial",
    "Energy":                 "Industrial",  # overridden below by profile
    "Industrials":            "Industrial",
    "Transportation":         "Industrial",
    "Materials":              "Industrial",
    "Resources":              "Industrial",
    "ProfessionalServices":   "Industrial",
    "HealthcareServices":     "Industrial",
}

# Energy profile overrides: regulated-like profiles map to Utility bucket.
_ENERGY_PROFILE_CREDIT_BUCKET: dict[str, str] = {
    "Regulated Utility":   "Utility",
    "IPP":                 "Utility",    # PPA-backed, semi-regulated
    "Merchant Power":      "Industrial",
    "EPC Contractor":      "Industrial",
    "Energy Tech Licensor":"Industrial",
}

# Hard fallback when FRED is unreachable. Values are in percentage points.
# These are the Damodaran Jan 2026 static spreads; collapsed modifiers (A+/A/A-)
# map to the middle value (A) by convention — see table comments.
# Source: Damodaran Jan 2026 synthetic-rating + default-spread table.
_FALLBACK_SPREAD_BPS: dict[str, float] = {
    "AAA": 0.40,   # Aaa/AAA
    "AA":  0.55,   # Aa2/AA
    "A":   0.78,   # A2/A (middle of A+/A/A-: 0.70/0.78/0.89)
    "BBB": 1.11,   # Baa2/BBB
    "BB":  1.61,   # average of BB (1.84) and BB+ (1.38)
    "B":   3.21,   # B2/B (middle of B+/B/B-: 2.75/3.21/5.09)
    "CCC": 8.85,   # Caa/CCC — FRED BAMLH0A3HYC includes CC & lower (typically wider)
}


def resolve_credit_bucket(sector: str, profile: str = "") -> str:
    """Map (sector, profile) → "Industrial" | "Financial" | "Utility".

    Profile is only consulted for Energy (regulated utilities trade much tighter
    than merchant power at the same rating). All other sectors are determined
    by the sector key alone.
    """
    if sector == "Energy" and profile in _ENERGY_PROFILE_CREDIT_BUCKET:
        return _ENERGY_PROFILE_CREDIT_BUCKET[profile]
    return _CREDIT_BUCKET_MAP.get(sector, "Industrial")


def synthetic_rating(interest_coverage: float | None,
                     is_financial: bool = False) -> str:
    """Map interest coverage (EBIT / interest expense) to a Damodaran synthetic
    letter rating. Returns one of the 7 FRED buckets (AAA..CCC).

    ``is_financial`` selects the financial-firm coverage table (much looser
    thresholds — a bank at 3x is AAA). Non-financial firms use the stricter
    table where 8.5x is needed for AAA.

    A ``None`` coverage — typically a company with no interest expense — is
    treated as unambiguously investment grade (returns "AAA") since there is
    no debt-service risk to price in. A negative coverage maps to CCC.
    """
    if interest_coverage is None:
        return "AAA"
    table = _RATING_FINANCIAL if is_financial else _RATING_NON_FINANCIAL
    # table is sorted high → low by lower-bound; first matching band wins
    for lower_bound, rating in table:
        if interest_coverage >= lower_bound:
            return rating
    return "CCC"


def get_cost_of_debt(
    interest_coverage: float | None,
    sector: str,
    profile: str = "",
    risk_free_rate: float = 0.0395,
    as_of: str | None = None,
) -> dict:
    """Compute live cost of debt using FRED aggregate spread × sector multiplier.

    Returns a dict with:
      - rating            : synthetic letter rating
      - bucket            : "Industrial" | "Financial" | "Utility"
      - aggregate_bps     : FRED aggregate OAS in basis points (None if fallback)
      - multiplier        : sector-bucket multiplier applied
      - spread_bps        : adjusted spread in basis points
      - cost_of_debt      : rf + spread (decimal form, e.g. 0.0512 = 5.12%)
      - source            : "fred" | "fallback-damodaran"
      - series_id         : FRED series used (or "static-table" on fallback)
      - audit             : human-readable one-line audit string

    Never raises. On any FRED failure, falls back to the Damodaran static table.
    ``as_of`` is currently unused (FRED returns latest observation) but reserved
    for historical backtests.
    """
    from src.tools.fred import get_fred_spread  # local import: avoid cycle

    bucket  = resolve_credit_bucket(sector, profile)
    rating  = synthetic_rating(interest_coverage, is_financial=(bucket == "Financial"))
    mult    = SECTOR_CREDIT_MULTIPLIERS.get(bucket, 1.00)
    series  = FRED_RATING_SERIES.get(rating, "BAMLC0A4CBBB")

    # Tier 1: live FRED aggregate for this rating
    agg_pct = get_fred_spread(series)
    source, series_id = "fred", series
    if agg_pct is None:
        # Tier 2: hard fallback to Damodaran static table
        agg_pct = _FALLBACK_SPREAD_BPS.get(rating, 1.60)
        source, series_id = "fallback-damodaran", "static-table"

    aggregate_bps = round(agg_pct * 100, 1)          # pct → bps
    spread_bps    = round(aggregate_bps * mult, 1)
    cost_of_debt  = risk_free_rate + spread_bps / 10000.0

    cov_str = f"{interest_coverage:.1f}x" if interest_coverage is not None else "n/a"
    audit = (
        f"Cost of debt {cost_of_debt:.2%} = rf {risk_free_rate:.2%} + "
        f"{spread_bps:.0f}bps (rating {rating} @ coverage {cov_str}, "
        f"bucket {bucket} ×{mult:.2f}, source {source}:{series_id})"
    )

    return {
        "rating":        rating,
        "bucket":        bucket,
        "aggregate_bps": aggregate_bps,
        "multiplier":    mult,
        "spread_bps":    spread_bps,
        "cost_of_debt":  round(cost_of_debt, 4),
        "source":        source,
        "series_id":     series_id,
        "audit":         audit,
    }


# ── 2. Terminal Growth Rates ──────────────────────────────────────────────────

# Terminal growth rates for Gordon Growth Model in DCF scenarios.
# Bear = below long-run nominal GDP; Base ≈ long-run nominal GDP (~2.5%);
# Bull = above GDP for sectors with above-average structural tailwinds.
# All rates assume USD nominal terms.
TERMINAL_GROWTH_RATES: dict[str, dict[str, float]] = {
    "Tech": {
        "bear": 0.020,   # mature SaaS / commoditised hardware
        "base": 0.030,   # platform compounders
        "bull": 0.040,   # category king / AI-native growth
    },
    "Consumer": {
        "bear": 0.010,
        "base": 0.020,
        "bull": 0.030,
    },
    "Biopharma": {
        "bear": 0.010,   # patent cliff / pipeline failure
        "base": 0.025,
        "bull": 0.035,   # blockbuster pipeline materialises
    },
    "Telco": {
        "bear": 0.005,   # structural decline in legacy lines
        "base": 0.015,
        "bull": 0.025,   # 5G monetisation / tower roll-up
    },
    "Crypto": {
        "bear": 0.010,
        "base": 0.030,
        "bull": 0.050,   # halving cycle tailwind + institutional adoption
    },
    "Energy": {
        "bear": 0.005,   # energy transition headwind
        "base": 0.020,
        "bull": 0.030,   # AI data-centre power demand supercycle
    },
    "Financials": {
        "bear": 0.010,   # credit cycle downturn
        "base": 0.020,
        "bull": 0.030,   # rate normalisation benefit
    },
    "Industrials": {
        "bear": 0.010,
        "base": 0.020,
        "bull": 0.030,   # infrastructure spending / reshoring cycle
    },
    "RealEstate": {
        "bear": 0.010,   # rising cap rates compress NAV
        "base": 0.020,
        "bull": 0.030,   # rent growth + development pipeline
    },
    "REIT": {
        "bear": 0.010,   # same as RealEstate — SGX uses "REIT" as sector
        "base": 0.020,
        "bull": 0.030,
    },
    "Transportation": {
        "bear": 0.005,   # fuel cost and demand cycle headwinds
        "base": 0.015,
        "bull": 0.025,   # freight volume supercycle / reshoring
    },
    "Materials": {
        "bear": 0.005,   # commoditiy downcycle
        "base": 0.015,
        "bull": 0.025,   # infrastructure spending / EV transition demand
    },
    "Resources": {
        "bear": 0.000,   # reserve depletion / commodity price floor
        "base": 0.015,
        "bull": 0.025,   # energy security premium / long-cycle supply deficit
    },
    "ProfessionalServices": {
        "bear": 0.010,   # wallet-share pressure / fee compression
        "base": 0.025,
        "bull": 0.035,   # secular payment volume growth / AI-augmented consulting
    },
    "Semiconductor": {
        "bear": 0.015,   # cyclical trough / overcapacity / demand destruction
        "base": 0.025,   # secular AI/HPC/IoT demand growth
        "bull": 0.040,   # AI supercycle / HBM pricing power / fab bottleneck
    },
}


# ── 3. FCF Margin Floor ───────────────────────────────────────────────────────

# Minimum FCF margin the DCF projection is allowed to reach in the bear case.
# Prevents nonsensical negative-to-infinity FCF projections for companies
# with currently negative FCF (e.g., early-growth SaaS, UBER, Crypto miners).
# The floor is not a guarantee — it is a clamping bound during projection.
FCF_MARGIN_FLOOR: dict[str, float] = {
    "Tech":        -0.05,   # allow modest negative FCF (growth-phase SaaS)
    "Consumer":     0.02,   # consumer staples should always generate some FCF
    "Biopharma":   -0.20,   # pre-revenue biotechs can be deeply FCF-negative
    "Telco":        0.05,   # infrastructure FCF should stay positive
    "Crypto":      -0.10,   # miners can be FCF-negative below hash-price breakeven
    "Energy":       0.00,   # utilities/power should be at least breakeven
    "Financials":          0.00,   # financial FCF proxied via retained earnings
    "Industrials":         0.02,
    "RealEstate":          0.05,   # REITs should maintain positive distributable cash
    "REIT":                0.05,   # SGX REITs (same as RealEstate)
    "Transportation":      0.00,   # airlines can go FCF-negative in downturns
    "Materials":           0.01,   # commodity producers maintain thin but positive FCF at cycle trough
    "Resources":           0.00,   # E&P/mining FCF can be zero at commodity trough
    "ProfessionalServices": 0.05,  # asset-light businesses should maintain positive FCF
    "Semiconductor":       -0.05,  # fab buildouts can push FCF negative during CapEx cycles
}


# ── 3b. Biopharma rNPV parameters ─────────────────────────────────────────────
#
# Risk-adjusted NPV inputs for the Biopharma rNPV method. These replace the
# prior manual dependency on TICKER_SECTOR_LOOKUP for phase classification —
# any biotech ticker can now produce a pipeline rNPV as long as deep research
# surfaces at least one asset with a phase tag.
#
# Sources:
#   * Clinical phase transition probabilities — BIO / Biomedtracker / Amplion
#     "Clinical Development Success Rates 2011-2020" (industry aggregate across
#     all indications). Values are the per-phase PROBABILITY of advancing to
#     the next clinical stage (not to approval).
#   * FDA regulatory approval probability — filed-to-approval historical rate,
#     ~85% across NDAs/BLAs (industry long-run average, FDA CDER data).
#   * Years-to-launch medians — aggregated from Tufts CSDD pipeline studies
#     and analyst timeline conventions; these are not literal averages but
#     standard analyst defaults used in rNPV modeling.
#
# Cumulative PoS to approval is the product of remaining transition
# probabilities × the 85% regulatory approval rate. Intentionally conservative:
# indication-specific PoS (e.g., oncology Ph2 ≈ 25%, metabolic Ph2 ≈ 40%) is
# not modeled here — that refinement would require indication classification
# per asset, which belongs in a follow-on extractor upgrade.

PHASE_POS_TABLE: dict[str, dict[str, float]] = {
    # phase_key → {transition_prob, cum_pos_to_approval, years_to_launch}
    "preclinical": {"transition": 0.52, "cum_pos": 0.050, "years_to_launch":  9.0},
    "phase_1":     {"transition": 0.63, "cum_pos": 0.096, "years_to_launch":  7.0},
    "phase_2":     {"transition": 0.31, "cum_pos": 0.153, "years_to_launch":  5.0},
    "phase_3":     {"transition": 0.58, "cum_pos": 0.493, "years_to_launch":  3.0},
    "filed":       {"transition": 0.85, "cum_pos": 0.850, "years_to_launch":  1.0},
    "approved":    {"transition": 1.00, "cum_pos": 1.000, "years_to_launch":  0.0},
}

# Phase-label aliases — LLM pipeline extractor will produce varied labels;
# these normalize to PHASE_POS_TABLE keys. Unknown phases fall back to Phase 1
# (most conservative reasonable assumption when a drug is in development).
_PHASE_ALIASES: dict[str, str] = {
    "preclinical":   "preclinical", "pre-clinical": "preclinical", "discovery": "preclinical",
    "ind":           "preclinical", "ind-enabling": "preclinical",
    "phase 1":       "phase_1",     "phase i":      "phase_1", "ph1": "phase_1", "ph 1": "phase_1",
    "phase 1/2":     "phase_1",     "phase i/ii":   "phase_1",
    "phase 2":       "phase_2",     "phase ii":     "phase_2", "ph2": "phase_2", "ph 2": "phase_2",
    "phase 2/3":     "phase_2",     "phase ii/iii": "phase_2",
    "phase 3":       "phase_3",     "phase iii":    "phase_3", "ph3": "phase_3", "ph 3": "phase_3",
    "pivotal":       "phase_3",     "registrational": "phase_3",
    "filed":         "filed",       "nda":          "filed", "bla": "filed", "submitted": "filed",
    "under review":  "filed",       "pdufa":        "filed",
    "approved":      "approved",    "commercial":   "approved", "marketed": "approved",
    "launched":      "approved",    "on-market":    "approved", "on market": "approved",
}


def normalize_phase(phase_label: str | None) -> str:
    """Normalize any phase label to a PHASE_POS_TABLE key.

    Unknown/missing phases fall back to 'phase_1' — this is the most
    conservative assumption a drug tagged with an unclear phase is at least
    in clinical development. Use 'preclinical' explicitly if the label says so.
    """
    if not phase_label:
        return "phase_1"
    key = str(phase_label).strip().lower()
    if key in PHASE_POS_TABLE:
        return key
    return _PHASE_ALIASES.get(key, "phase_1")


def phase_pos(phase_label: str | None) -> float:
    """Cumulative probability-of-success to approval for a phase label."""
    return PHASE_POS_TABLE[normalize_phase(phase_label)]["cum_pos"]


def phase_years_to_launch(phase_label: str | None) -> float:
    """Median years from current phase to commercial launch."""
    return PHASE_POS_TABLE[normalize_phase(phase_label)]["years_to_launch"]


# rNPV commercial-stream defaults by profile. Peak operating margin and
# effective tax rate are profile-aware because:
#   * Large Cap Pharma benefits from Irish/Swiss IP holding structures
#     (effective tax rate 10-15% in practice) and mature portfolio margins
#     (45-55% operating margins on high-moat drugs like Ibrance/Eliquis).
#   * Pre-approval biotechs don't yet enjoy those structures and typically
#     get taxed at the US/domicile statutory rate with narrower margins on
#     novel-drug launches (still-proving manufacturing + commercial learning
#     curve), so a more conservative 40% margin / 21% tax is appropriate.
# The profile lookup is done in dcf_agent.py:_compute_rnpv() with a fallback
# to the "default" entry for any profile not explicitly listed.
RNPV_COMMERCIAL_DEFAULTS: dict[str, dict[str, float]] = {
    "Large Cap Pharma": {
        "peak_op_margin":     0.45,   # mature portfolio margin at peak
        "effective_tax_rate": 0.14,   # Irish/Swiss IP structure blended rate
    },
    "Pre-approval Biotech": {
        "peak_op_margin":     0.40,   # conservative for novel-drug launches
        "effective_tax_rate": 0.21,   # US statutory (no IP structures yet)
    },
    "default": {
        "peak_op_margin":     0.40,
        "effective_tax_rate": 0.21,
    },
}

# Bell-shaped commercial cash-flow profile as a fraction of peak sales, by
# year since launch (year 1 = first full year of commercial sales). Replaces
# the prior level-annuity stylization — flat 10y at peak substantially
# over-counts the ramp years and entirely ignores the post-LOE cliff.
#
# Design:
#   * Years 1–3: ramp (20%, 50%, 80%) — typical specialty/novel drug launch curve
#   * Years 4–10: plateau at 100% of peak (7y at peak — main exclusivity window)
#   * Years 11–13: LOE erosion (40%, 20%, 10%) — approximates branded revenue
#                   decay after generic/biosimilar entry on typical 12y patent life
# Total effective duration ≈ 13 years; total cumulative CF at flat-discount
# ≈ 8.2 × peak (vs 10× for level annuity → ~18% less optimistic).
#
# Used ONLY for rNPV assets flagged as not-yet-launched (phase ≠ approved
# or launch_year > current year). Already-approved drugs still use peak-
# sales revenue directly inside Σ with no phase ramp-up, but LOE erosion
# still applies to them once they pass years-since-launch of 10.
RNPV_RAMP_PROFILE: list[float] = [
    0.20, 0.50, 0.80,              # years 1-3: ramp
    1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00,  # years 4-10: peak
    0.40, 0.20, 0.10,              # years 11-13: post-LOE erosion
]

# Therapeutic-area PoS multipliers — applied on top of aggregate phase PoS
# when the deep research extractor tags an asset's indication. Multiplies
# the cum_pos value from PHASE_POS_TABLE.
#
# Sources: BIO / Biomedtracker / Amplion 2011-2020 data, therapeutic-area
# aggregate Ph1-to-approval success rates, normalized vs industry mean (9.6%):
#   Oncology:      5.3% → 0.55x
#   CNS/Neuro:     5.9% → 0.60x
#   Cardiovascular: 8.7% → 0.85x
#   Allergy/Derm:  9.4% → 1.0x (default)
#   Infectious:   10.6% → 1.1x
#   Metabolic:    11.7% → 1.2x
#   Hematology:   13.2% → 1.4x
#   Rare Disease: 17.0% → 1.7x (includes Orphan PRV designations)
#
# Multipliers are applied to cum_pos, then final PoS is clamped to
# [0.005, 1.0] so a pathological multi-stack (e.g. small-indication
# preclinical onco asset) doesn't zero out and a filed/approved indication
# doesn't exceed 100%.
_THERAPEUTIC_AREA_POS_MULTIPLIERS: dict[str, float] = {
    "oncology":       0.55,  "cancer":         0.55,  "tumor":        0.55,
    "solid tumor":    0.55,  "hematologic malignancy": 0.70,   # hema-onc (slightly better than solid)
    "cns":            0.60,  "neurology":      0.60,  "neurological": 0.60,
    "alzheimer":      0.45,  "parkinson":      0.55,  "psychiatric":  0.60,
    "depression":     0.60,  "schizophrenia":  0.55,
    "cardiovascular": 0.85,  "cardio":         0.85,  "heart":        0.85,
    "allergy":        1.00,  "dermatology":    1.00,  "derm":         1.00,
    "infectious":     1.10,  "antiviral":      1.10,  "antibacterial": 1.10,
    "vaccine":        1.15,  "anti-infective": 1.10,
    "metabolic":      1.20,  "diabetes":       1.20,  "obesity":      1.20,
    "endocrine":      1.20,  "glp-1":          1.30,  # obesity/diabetes — recent high success
    "hematology":     1.40,  "blood":          1.40,
    "rare disease":   1.70,  "orphan":         1.70,  "genetic":      1.70,
    "rare":           1.70,  "ultra-rare":     1.80,
    "gene therapy":   1.30,  "cell therapy":   1.30,  # modality premium, high unmet need
    "respiratory":    1.00,  "ophthalmology":  1.10,  "ophtho":       1.10,
    "urology":        1.10,  "autoimmune":     1.10,  "immunology":   1.10,
    "gastroenterology": 1.05, "gi":            1.05,  "gastro":       1.05,
}


def therapeutic_area_pos_multiplier(indication: str | None) -> float:
    """Lookup therapeutic-area PoS multiplier from a free-text indication label.

    Checks exact match first, then substring match against known keys. Returns
    1.0 (no adjustment) when the indication is missing or doesn't match any
    known area. This intentionally biases toward the aggregate BIO PoS rather
    than guessing a harsh/generous multiplier from thin context.
    """
    if not indication:
        return 1.0
    key = str(indication).strip().lower()
    if key in _THERAPEUTIC_AREA_POS_MULTIPLIERS:
        return _THERAPEUTIC_AREA_POS_MULTIPLIERS[key]
    # Substring match — pick the longest matching key so "hematologic
    # malignancy" beats "hematology" when both apply.
    best_match = None
    best_len   = 0
    for k, v in _THERAPEUTIC_AREA_POS_MULTIPLIERS.items():
        if k in key and len(k) > best_len:
            best_match = v
            best_len   = len(k)
    return best_match if best_match is not None else 1.0


# Biopharma profile-specific rNPV WACC. The Damodaran January 2026 dataset
# distinguishes between Drugs (Pharmaceutical) — 7.85% — and Drugs (Biotech)
# — 8.49%. The sector-level Biopharma WACC (8.5%) is a midpoint; for rNPV
# valuations we use profile-specific rates to better reflect the risk
# profile of each company type:
#
#   * Large Cap Pharma — Damodaran Drugs Pharma: 7.85%. Mature, diversified
#     portfolio, stable FCF funds R&D without equity dilution. Used directly.
#   * Pre-approval Biotech — 11.0%. Damodaran Drugs Biotech (8.49%) + ~250bp
#     clinical-stage premium for (a) lack of asset diversification (binary
#     trial outcomes dominate), (b) liquidity risk (thinner trading, frequent
#     capital raises), and (c) governance risk (first-time commercial teams).
#     Industry analysts typically use 11–15% for pre-revenue biotechs; we
#     apply the low-end 11% as a default so the model stays conservative
#     while not double-penalizing via already-clamped aggregate PoS.
#   * Managed Care / MedTech / CDMO — use base sector WACC from SECTOR_WACC
#     (rNPV doesn't apply to these profiles today).
LARGE_CAP_PHARMA_WACC:    float = 0.0785
PRE_APPROVAL_BIOTECH_WACC: float = 0.110


# ── 4. Tech Stack Layers ──────────────────────────────────────────────────────

# GS AI stack taxonomy — used by specialist.py and dcf_agent.py for growth
# rate calibration. Infrastructure compounds faster near-term; Application
# layer has longer monetisation curves but higher terminal penetration.
TECH_STACK_LAYERS: dict[str, dict] = {
    "infrastructure": {
        "tickers":     ["NVDA", "AMD", "INTC", "MSFT", "GOOGL", "AMZN", "META"],
        "thesis":      "Compute demand compounds with LLM training and inference scale.",
        "bull_trigger": "Rising cloud capex commitments + GPU lead times extending",
        "bear_trigger": "Compute cost deflation faster than expected; China export tightening",
        "watch":       "NVDA H100/H200 ASP trend; hyperscaler capex guidance revisions",
        "growth_premium": 0.05,   # add to base growth rate for category king score ≥8
    },
    "platform": {
        "tickers":     ["SNOW", "MDB", "DDOG", "PLTR"],
        "thesis":      "Data layer democratises LLM access; winner-take-most dynamic.",
        "bull_trigger": "Developer adoption — API calls, Snowpark/MDB Atlas usage acceleration",
        "bear_trigger": "Open-source model proliferation → data layer commoditisation",
        "watch":       "$/token cost deflation trend; open-source vector DB adoption",
        "growth_premium": 0.03,
    },
    "application": {
        "tickers":     ["MSFT", "CRM", "ADBE", "INTU", "GTLB", "NOW", "WDAY"],
        "thesis":      "AI SKU monetisation on top of existing installed base.",
        "bull_trigger": "New AI SKU with disclosed $/user/month pricing at GA",
        "bear_trigger": "Beta fails GA within 18 months; AI-native startup raises >$500M in vertical",
        "watch":       "M365 Copilot seat count; CRM Agentforce ARR; ADBE Firefly attachment rate",
        "growth_premium": 0.02,
    },
}


def classify_stack_layer(ticker: str) -> str:
    """
    Return the AI stack layer ('infrastructure' | 'platform' | 'application')
    for a given ticker, or 'unknown' if not in any layer.
    Note: some tickers (e.g. MSFT) span multiple layers; this returns the
    primary layer based on the GS framework definition.
    """
    for layer_name, layer_data in TECH_STACK_LAYERS.items():
        if ticker.upper() in layer_data["tickers"]:
            return layer_name
    return "unknown"


# ── 5. Sector Profiles ────────────────────────────────────────────────────────

# Structured metadata per sector — consumed by dcf_agent.py for growth rate
# calibration and by specialist.py for enriched brief generation.
# Does NOT duplicate SECTOR_BLOCKS (LLM prompt text) or _SECTOR_KPI_PARSERS
# (KPI extraction) — both remain in specialist.py.

SECTOR_PROFILES: dict[str, dict] = {

    "Tech": {
        "key_metrics": [
            "Rule of 40 (Revenue Growth % + FCF Margin %)",
            "Net Revenue Retention (NRR)",
            "CAC Payback Period (months)",
            "LTV:CAC ratio",
            "ARR growth YoY",
            "R&D intensity (R&D / Revenue)",
            "AI SKU live + pricing disclosed",
        ],
        "moat_types": [
            "network effects",
            "switching costs",
            "1P data moats",
            "distribution + installed base",
            "API ecosystem lock-in",
        ],
        "earnings_signal_tiers": {
            "tier1_actionable": [
                "Disclosed AI SKU pricing ($/user/month)",
                "Disclosed AI ARR or revenue contribution",
                "AI-driven NRR improvement disclosed",
                "GA product launch with customer count",
            ],
            "tier2_watch": [
                "AI cited as key deal driver with % of new wins",
                "Beta product with disclosed user count",
                "Partnership with commercial terms disclosed",
            ],
            "tier3_noise": [
                "We are exploring AI opportunities",
                "AI mentioned without metrics",
                "Demo only, no commercial timeline",
            ],
        },
        "macro_linkages": {
            "rates":      "Rate cuts → multiple expansion for high-duration growth names",
            "fx":         "MSFT/GOOGL/ADBE high intl revenue; intl AI ARPU ~$3 vs $10 US",
            "china_risk": "NVDA direct export restriction; MSFT/GOOGL AI access limited",
            "it_spend":   "Fast time-to-value AI tools more insulated than long-cycle ERP",
        },
        "competitive_watches": [
            "Bing vs Google search share (monthly)",
            "M365 Copilot vs Google Workspace enterprise renewal divergence",
            "GitHub Copilot Business seat count (quarterly)",
            "CRM multi-cloud NRR vs Dynamics 365 seat expansion",
            "ADBE Firefly activation rates",
        ],
        "tam_model": {
            "knowledge_workers_bn": 1.1,
            "apps_per_worker":      5,
            "us_arpu_annual":       120,    # $10/month
            "intl_arpu_annual":     36,     # $3/month
            "base_adoption_rate":   0.30,
            "base_tam_bn":          150,
            "sensitivity": {
                "bull": {"adoption": 0.40, "apps": 6, "tam_bn": 187},
                "bear": {"adoption": 0.20, "apps": 4, "tam_bn":  62},
            },
        },
        "policy_risks": [
            "antitrust (EU DMA, US DOJ)",
            "copyright / IP litigation (ADBE Firefly, generative AI training data)",
            "data privacy / GDPR",
            "China export restrictions (NVDA H-series GPUs)",
        ],
        "stack_layers": TECH_STACK_LAYERS,
    },

    "Consumer": {
        "key_metrics": [
            "Same-store sales growth (SSS)",
            "Contribution margin",
            "Pricing power delta vs. input cost inflation",
            "Revenue per unit vs. cost per unit trend",
            "Inventory turnover",
        ],
        "moat_types": ["brand", "distribution scale", "private label penetration"],
        "macro_linkages": {
            "rates":    "Consumer credit costs rise with rates; discretionary spending sensitive",
            "fx":       "Global brands face translation headwind on USD strengthening",
            "inflation": "Staples can pass through; discretionary faces volume risk",
        },
        "policy_risks": ["minimum wage legislation", "tariffs on imported goods"],
    },

    "Biopharma": {
        "key_metrics": [
            "Pipeline rNPV vs. market cap",
            "Phase-specific PoS: Ph1=63%, Ph2=31%, Ph3=58%, NDA=85%",
            "Patent life remaining (flagship drug)",
            "FDA/EMA decision dates within 90 days",
            "Cash runway (months at current burn)",
        ],
        "moat_types": ["patents", "regulatory exclusivity", "manufacturing scale", "clinical data"],
        "macro_linkages": {
            "rates":    "High burn-rate biotechs penalised by high discount rates",
            "policy":   "IRA drug price negotiation compresses blockbuster margins",
            "fx":       "Global drug pricing partially USD-denominated",
        },
        "policy_risks": [
            "IRA drug price negotiation (US Medicare)",
            "FDA/EMA approval uncertainty (binary events)",
            "Biosimilar entry on loss-of-exclusivity",
        ],
    },

    "Telco": {
        "key_metrics": [
            "Tenancy ratio (co-locations per tower)",
            "FCF yield",
            "Maintenance vs. growth capex split",
            "Asset utilisation rate",
            "ARPU trend",
        ],
        "moat_types": ["spectrum licences", "tower infrastructure", "subscriber lock-in"],
        "macro_linkages": {
            "rates":    "High leverage means interest costs sensitive to rate moves",
            "fx":       "Tower companies have domestic revenue; limited FX risk",
            "regulation": "Spectrum auction costs and price regulation are key overhangs",
        },
        "policy_risks": ["spectrum re-allocation", "roaming price regulation", "5G rollout mandates"],
    },

    "Crypto": {
        "key_metrics": [
            "EV per exahash (EH/s)",
            "Cash production cost per coin",
            "Megawatt pipeline under development",
            "Hash rate growth (6-month CAGR)",
            "Hash price ($/TH/day)",
        ],
        "moat_types": ["low-cost power agreements", "scale hash rate", "balance sheet BTC holdings"],
        "macro_linkages": {
            "rates":      "Risk-off → crypto sell-off; rate cuts supportive",
            "regulation": "ETF approval, exchange regulation, and mining jurisdiction risk",
            "energy":     "Power cost is the largest operating variable",
        },
        "policy_risks": [
            "Mining jurisdiction bans",
            "Exchange regulatory action (SEC, CFTC)",
            "Energy transition / carbon accounting",
        ],
    },

    "Energy": {
        "key_metrics": [
            "SOTP valuation vs. market cap",
            "PPA quality (tenor, counterparty, fixed vs. merchant %)",
            "LCOE vs. current power price spread",
            "Capacity factor by asset type",
            "Regulatory milestone calendar",
        ],
        "moat_types": ["long-term PPAs", "grid interconnection rights", "site permits"],
        "macro_linkages": {
            "rates":      "Capital-intensive; higher rates raise WACC and compress regulated returns",
            "ai_demand":  "Data-centre power demand is a multi-year structural tailwind",
            "policy":     "IRA credits, state RPS mandates, nuclear restart funding",
        },
        "policy_risks": [
            "IRA credit phase-out or modification",
            "Grid interconnection queue delays",
            "Nuclear permitting and liability frameworks",
            "Merchant power price volatility (no PPA)",
        ],
    },

    "Financials": {
        "key_metrics": [
            "Net Interest Margin (NIM) — last 8 quarters",
            "Non-Performing Loan ratio (NPL%)",
            "Common Equity Tier 1 (CET1) vs. regulatory minimum",
            "RoE vs. Cost of Equity spread",
            "Loan-to-deposit ratio",
        ],
        "moat_types": ["deposit franchise", "regulatory moat", "scale/distribution"],
        "macro_linkages": {
            "rates":      "Banks benefit from higher rates (NIM); credit risk rises late-cycle",
            "credit":     "Late-cycle → provision build; watch NPL and charge-off trends",
            "regulation": "Basel IV capital rules tighten RWA; reduces buyback capacity",
        },
        "policy_risks": [
            "Basel IV / stress test capital requirements",
            "Consumer protection regulation (CFPB)",
            "FNMA conservatorship resolution uncertainty",
        ],
    },

    "Industrials": {
        "key_metrics": [
            "Order backlog / annual revenue multiple",
            "Book-to-bill ratio (last 4 quarters)",
            "Fixed-price contract exposure %",
            "Government contract concentration (% revenue)",
            "Operating leverage (revenue growth → margin flow-through)",
        ],
        "moat_types": ["long-duration contracts", "certification barriers", "installed base services"],
        "macro_linkages": {
            "rates":       "Higher rates raise hurdle for government capex programmes",
            "reshoring":   "Domestic manufacturing incentives (CHIPS, IRA, defence) are tailwinds",
            "commodities": "Steel, aluminium, rare-earth input costs affect margin",
        },
        "policy_risks": [
            "Defence budget sequestration risk",
            "Fixed-price contract cost overruns",
            "Export control / ITAR restrictions",
        ],
    },

    "RealEstate": {
        "key_metrics": [
            "Net Asset Value (NAV) vs. share price (premium/discount)",
            "Funds From Operations (FFO) per share",
            "Adjusted FFO (AFFO) per share",
            "Capitalisation rate (cap rate) vs. implied cap rate",
            "Same-store NOI growth",
            "Occupancy rate and lease expiry schedule",
        ],
        "moat_types": ["location / irreplaceable asset base", "long-term leases", "development pipeline", "management track record"],
        "macro_linkages": {
            "rates":      "Rising rates compress cap rate spreads and increase cost of debt; refinancing risk",
            "inflation":  "Rent escalators provide inflation pass-through; construction cost headwind",
            "credit":     "LTV covenants and debt maturity wall are key tail risks",
        },
        "policy_risks": [
            "Rent control legislation",
            "Zoning and planning approvals",
            "Property tax reassessment",
            "REIT qualification and distribution requirements",
        ],
    },

    "Transportation": {
        "key_metrics": [
            "Revenue per available seat mile (RASM) — airlines",
            "Cost per available seat mile (CASM ex-fuel) — airlines",
            "Operating ratio (OR) — rail/trucking (lower = better)",
            "Load factor % — airlines",
            "Revenue ton miles (RTM) — rail/freight",
            "Fuel cost as % of revenue",
        ],
        "moat_types": ["route network density", "fleet scale advantages", "terminal infrastructure", "regulatory slots"],
        "macro_linkages": {
            "rates":      "High debt loads make airlines sensitive to rate moves; rail more insulated",
            "oil":        "Jet fuel is 20–30% of airline COGS; rail fuel surcharges partially offset",
            "trade":      "Freight volumes are a leading indicator of global trade flows",
        },
        "policy_risks": [
            "Fuel hedging and commodity price volatility",
            "Pilot/crew labour contracts and strikes",
            "Route slot allocation / antitrust constraints",
            "Carbon emissions regulation (SAF mandates)",
        ],
    },

    "Materials": {
        "key_metrics": [
            "EBITDA margin at various commodity price points",
            "Cash cost per tonne vs. spot commodity price (spread)",
            "Normalised EBITDA (mid-cycle pricing)",
            "Capital intensity (capex / revenue)",
            "Inventory levels and working capital cycle",
            "ESG: carbon intensity per tonne produced",
        ],
        "moat_types": ["low-cost production position", "scale", "vertical integration", "specialty chemistry IP"],
        "macro_linkages": {
            "rates":       "High capex needs make capital costs material; balance sheet strength critical",
            "china":       "Chinese steel / chemical overcapacity is the primary pricing pressure",
            "ev_demand":   "EV battery supply chain drives structural demand for lithium, cobalt, nickel",
            "construction": "Steel demand tracks global construction and infrastructure spend",
        },
        "policy_risks": [
            "Anti-dumping tariffs and trade protection measures",
            "Carbon border adjustment mechanisms (CBAM)",
            "Environmental permitting for new capacity",
            "ESG-driven financing constraints for high-emission producers",
        ],
    },

    "Resources": {
        "key_metrics": [
            "Reserve life index (RLI) — years of reserves at current production",
            "PV-10 / NAV per share vs. share price",
            "Cash cost per BOE / per oz (breakeven analysis)",
            "All-in sustaining cost (AISC) — mining",
            "Finding and development cost (F&D cost) — E&P",
            "Net debt / EBITDA vs. hedging coverage",
        ],
        "moat_types": ["low-cost reserve position", "resource quality / grade", "infrastructure access", "jurisdiction stability"],
        "macro_linkages": {
            "rates":      "High debt E&P/mining companies highly sensitive to rate moves",
            "usd":        "Commodities priced in USD — strengthening dollar compresses USD revenue",
            "china":      "Largest marginal demand driver for most metals and energy commodities",
            "geopolitics": "Supply disruption risk from OPEC+ actions, sanctions, and resource nationalism",
        },
        "policy_risks": [
            "Resource nationalism and windfall profit taxes",
            "Environmental permitting delays and ESG capital constraints",
            "OPEC+ production quota decisions",
            "Energy transition acceleration reducing long-run fossil fuel demand",
        ],
    },

    "ProfessionalServices": {
        "key_metrics": [
            "Organic revenue growth rate",
            "EBIT margin (pre-staff bonus) — ad agencies",
            "Total payment volume (TPV) growth — payment processors",
            "Net revenue / take rate — payment processors",
            "Revenue per employee — consulting",
            "Rule of 40 (Revenue Growth + FCF Margin) — payment tech",
        ],
        "moat_types": ["client relationships / switching costs", "proprietary data and benchmarks", "network effects (payments)", "regulatory licensing"],
        "macro_linkages": {
            "rates":      "Higher rates increase cost of working capital; payment processors benefit from float income",
            "ad_spend":   "Ad agency revenue highly correlated to global ad market and GDP",
            "ecommerce":  "Payment processor TPV tracks e-commerce penetration and consumer spending",
            "ai":         "Automation threat to lower-value consulting; AI opportunity for payment fraud prevention",
        },
        "policy_risks": [
            "Interchange fee regulation (Durbin Amendment, EU IFR)",
            "Antitrust scrutiny of payment network duopoly (Visa/Mastercard)",
            "Digital advertising privacy regulation (cookie deprecation, ATT)",
            "Cross-border transaction regulation and FX controls",
        ],
    },
}


# ── 6. Industry Valuation Profiles — Master JSON Map ──────────────────────────
#
# Source: Ultimate_Valuation_Master_2026.xlsx "Master Weight Map" sheet.
# Each profile entry contains:
#   "methods"  : list of {"name", "weight", "anchor" (bool), "implementable" (bool)}
#   "excluded" : list of method names that MUST NOT be used for this profile
#   "rationale": one-line justification from the master map
#
# "anchor" = True on the primary driver method (highest weight, first in list).
# "implementable" = True if dcf_agent can compute it from standard FMP line items.
# Non-implementable methods (rNPV, PPA-backed DCF, GMV-based, etc.) receive a
# proxy flag — the engine falls back to the DCF-family equivalent instead.
#
# Pipeline sector → profile mapping is handled by classify_valuation_profile().

INDUSTRY_VALUATION_PROFILES: dict[str, dict[str, dict]] = {

    # ── FINANCIALS ────────────────────────────────────────────────────────────
    "Financials": {
        "Mortgage/GSE": {
            "methods": [
                {"name": "P/BV",            "weight": 0.55, "anchor": True,  "implementable": True},
                {"name": "P/E (norm)",       "weight": 0.30, "anchor": False, "implementable": True},
                {"name": "DDM",              "weight": 0.15, "anchor": False, "implementable": True},
            ],
            "excluded": ["DCF", "EV/EBITDA", "EV/Revenue"],
            "rationale": (
                "GSEs are valued on P/TBV (conservatorship binary re-rating optionality) "
                "and normalised P/E (earnings power post-privatisation). "
                "EV-based multiples are inapplicable: balance-sheet liabilities (~$4T) make "
                "(EV − net debt) / shares meaningless. "
                "⚠ NET INCOME NOTE: FMP API reports GAAP net income (~$16B for FMCC FY2025). "
                "Management guidance typically refers to net income AFTER the TCCA / Senior "
                "Preferred net worth sweep, which directs substantially all earnings to the "
                "U.S. Treasury (~$10.7B reported). These are not interchangeable: the API "
                "figure represents enterprise earnings; the management figure represents "
                "income attributable to common equity under conservatorship. DCF and "
                "forward-multiple computations use the API (enterprise) figure; scenario "
                "narratives may reference the management (post-sweep) figure. Readers should "
                "treat any net income citation without an explicit basis qualifier with caution."
            ),
        },
        "FinTech": {
            "methods": [
                {"name": "EV/EBITDA",       "weight": 0.35, "anchor": True,  "implementable": True},
                {"name": "FCF Yield",       "weight": 0.30, "anchor": False, "implementable": True},
                {"name": "EV/Revenue",      "weight": 0.20, "anchor": False, "implementable": True},
                {"name": "P/E (norm)",      "weight": 0.15, "anchor": False, "implementable": True},
            ],
            "excluded": [],
            "rationale": (
                "FinTech/payments companies (PYPL, SQ, ADYEN, COIN) are valued on "
                "EV/EBITDA and FCF yield — not P/BV like banks. Their value driver is "
                "take rate × TPV, not book value. EV/Revenue captures growth optionality "
                "for earlier-stage fintechs. P/E included for earnings-mature names."
            ),
        },
        "Bank / Lending Institution": {
            "methods": [
                {"name": "Residual Income", "weight": 0.55, "anchor": True,  "implementable": True},
                {"name": "P/TBV",           "weight": 0.25, "anchor": False, "implementable": True},
                {"name": "P/E (norm)",      "weight": 0.15, "anchor": False, "implementable": True},
                {"name": "Excess Capital",  "weight": 0.05, "anchor": False, "implementable": True},
            ],
            "excluded": ["DCF", "P/BV", "ROE vs CoE"],
            "rationale": (
                "Institutional-grade bank valuation. 2-stage Residual Income anchors "
                "at 55% — ROE fades linearly to profile target over 5-10 years, BVPS "
                "compounds at retention × ROE, terminal RI = 0 (ROE reverts to CoE in "
                "perpetuity). P/TBV replaces P/BV — strips goodwill + intangibles from "
                "the equity base to match Basel regulatory capital definition. Excess "
                "Capital overlay surfaces CET1 vs target (positive = distributable "
                "buffer, negative = mandatory retention). ROE vs CoE removed — it was "
                "effectively a single-period version of Residual Income and caused "
                "60% weight concentration on the same signal."
            ),
        },
        # ── Bank sub-profiles (Tier 2 item 3) ─────────────────────────────
        # Each sub-profile shares the 4-method RI+P/TBV+P/E+ExcessCap structure
        # but gets distinct calibration via dcf_agent._BANK_PROFILE_CALIBRATION
        # (target_roe, CoE, P/TBV multiple, CET1 target, fade years, RWA proxy).
        # classify_valuation_profile + TICKER_SECTOR_LOOKUP resolve tickers to
        # the appropriate sub-profile key below.
        "Money Center Bank": {
            "methods": [
                {"name": "Residual Income", "weight": 0.55, "anchor": True,  "implementable": True},
                {"name": "P/TBV",           "weight": 0.25, "anchor": False, "implementable": True},
                {"name": "P/E (norm)",      "weight": 0.15, "anchor": False, "implementable": True},
                {"name": "Excess Capital",  "weight": 0.05, "anchor": False, "implementable": True},
            ],
            "excluded": ["DCF", "P/BV", "ROE vs CoE"],
            "rationale": "US GSIB Money Center bank (JPM/BAC/C/WFC). Target ROE 12%, CoE 9%, P/TBV 1.4x, CET1 target 12%.",
        },
        "Money Center Bank (EU)": {
            "methods": [
                {"name": "Residual Income", "weight": 0.55, "anchor": True,  "implementable": True},
                {"name": "P/TBV",           "weight": 0.25, "anchor": False, "implementable": True},
                {"name": "P/E (norm)",      "weight": 0.15, "anchor": False, "implementable": True},
                {"name": "Excess Capital",  "weight": 0.05, "anchor": False, "implementable": True},
            ],
            "excluded": ["DCF", "P/BV", "ROE vs CoE"],
            "rationale": "European Money Center (HSBC/Barclays/DB) — higher CoE (11%) and CET1 target (14%) from regulatory drag; lower target ROE (10%) and P/TBV (0.8x).",
        },
        "Regional Bank": {
            "methods": [
                {"name": "Residual Income", "weight": 0.55, "anchor": True,  "implementable": True},
                {"name": "P/TBV",           "weight": 0.25, "anchor": False, "implementable": True},
                {"name": "P/E (norm)",      "weight": 0.15, "anchor": False, "implementable": True},
                {"name": "Excess Capital",  "weight": 0.05, "anchor": False, "implementable": True},
            ],
            "excluded": ["DCF", "P/BV", "ROE vs CoE"],
            "rationale": "US Regional (USB/TFC/PNC). Target ROE 11%, CoE 10%, P/TBV 1.2x, CET1 11%. Higher RWA density (0.70x assets) for CRE-heavy books.",
        },
        "Super-Regional Bank": {
            "methods": [
                {"name": "Residual Income", "weight": 0.55, "anchor": True,  "implementable": True},
                {"name": "P/TBV",           "weight": 0.25, "anchor": False, "implementable": True},
                {"name": "P/E (norm)",      "weight": 0.15, "anchor": False, "implementable": True},
                {"name": "Excess Capital",  "weight": 0.05, "anchor": False, "implementable": True},
            ],
            "excluded": ["DCF", "P/BV", "ROE vs CoE"],
            "rationale": "Super-regional (TD/BMO/RBC). Canadian Big-Six scale + diversification. CoE 9.5%, P/TBV 1.3x.",
        },
        "EM Bank": {
            "methods": [
                {"name": "Residual Income", "weight": 0.55, "anchor": True,  "implementable": True},
                {"name": "P/TBV",           "weight": 0.25, "anchor": False, "implementable": True},
                {"name": "P/E (norm)",      "weight": 0.15, "anchor": False, "implementable": True},
                {"name": "Excess Capital",  "weight": 0.05, "anchor": False, "implementable": True},
            ],
            "excluded": ["DCF", "P/BV", "ROE vs CoE"],
            "rationale": "EM SOE banks (ICBC/CCB/BOC/ABC). Target ROE 14% (high NIM), CoE 13% (national-service risk premium), P/TBV 1.2x, CET1 10.5%.",
        },
        "EM Bank (Premium)": {
            "methods": [
                {"name": "Residual Income", "weight": 0.55, "anchor": True,  "implementable": True},
                {"name": "P/TBV",           "weight": 0.25, "anchor": False, "implementable": True},
                {"name": "P/E (norm)",      "weight": 0.15, "anchor": False, "implementable": True},
                {"name": "Excess Capital",  "weight": 0.05, "anchor": False, "implementable": True},
            ],
            "excluded": ["DCF", "P/BV", "ROE vs CoE"],
            "rationale": "EM Premium — India private banks (HDFC/ICICI/Kotak) sustain 16-18% ROE on credit-to-GDP gap. 7-year fade, P/TBV 2.0x.",
        },
        "Investment Bank": {
            "methods": [
                {"name": "Residual Income", "weight": 0.55, "anchor": True,  "implementable": True},
                {"name": "P/TBV",           "weight": 0.25, "anchor": False, "implementable": True},
                {"name": "P/E (norm)",      "weight": 0.15, "anchor": False, "implementable": True},
                {"name": "Excess Capital",  "weight": 0.05, "anchor": False, "implementable": True},
            ],
            "excluded": ["DCF", "P/BV", "ROE vs CoE"],
            "rationale": "Investment Bank (GS/MS). Target ROE 13% (cyclical), CoE 11% (trading VaR premium), P/TBV 1.2x, RWA proxy 0.40x (market-risk-weighted).",
        },
        "Neo/Challenger": {
            "methods": [
                {"name": "Residual Income", "weight": 0.55, "anchor": True,  "implementable": True},
                {"name": "P/TBV",           "weight": 0.25, "anchor": False, "implementable": True},
                {"name": "P/E (norm)",      "weight": 0.15, "anchor": False, "implementable": True},
                {"name": "Excess Capital",  "weight": 0.05, "anchor": False, "implementable": True},
            ],
            "excluded": ["DCF", "P/BV", "ROE vs CoE"],
            "rationale": "Neo/Challenger (NU/SOFI) — J-curve ROE. Target 18%, P/TBV 2.8x, 10-year fade (extended because current ROE still ramping).",
        },
        "Insurance": {
            "methods": [
                # PR #1 — Embedded Value is now implementable for Life insurers
                # via SECTOR_KPI_FRAMEWORK extracted vnb_margin and
                # embedded_value_per_share. Falls back to P/BV proxy if KPIs
                # missing (handled inside _compute_method_value branch).
                {"name": "Embedded Value",      "weight": 0.35, "anchor": True,  "implementable": True, "proxy": "P/BV"},
                # PR #1 — Combined Ratio Gate uses extracted combined_ratio
                # to apply a P/BV multiplier reflecting underwriting quality.
                # Replaces a piece of the legacy P/BV weight; only contributes
                # to blend when combined_ratio is present (P&C / Reinsurance).
                {"name": "Combined Ratio Gate", "weight": 0.15, "anchor": False, "implementable": True},
                {"name": "P/BV",                "weight": 0.30, "anchor": False, "implementable": True},
                {"name": "P/E (ops)",           "weight": 0.15, "anchor": False, "implementable": True},
                {"name": "DDM",                 "weight": 0.05, "anchor": False, "implementable": True},
            ],
            "excluded": ["DCF"],
            "rationale": (
                "EV (Life) and Combined Ratio Gate (P&C) capture sub-sub-profile-specific "
                "value drivers. P/BV remains the regulatory-capital anchor for blended IV."
            ),
        },
        "Alt Asset Manager": {
            "methods": [
                {"name": "SOTP (FRE+Carry)", "weight": 0.60, "anchor": True,  "implementable": False, "proxy": "EPV"},
                {"name": "P/FRE",            "weight": 0.20, "anchor": False, "implementable": False, "proxy": "P/E (norm)"},
                {"name": "P/DE",             "weight": 0.15, "anchor": False, "implementable": False, "proxy": "P/E (norm)"},
                {"name": "AUM Multiple",     "weight": 0.05, "anchor": False, "implementable": False, "proxy": "EV/Revenue"},
            ],
            "excluded": ["DCF"],
            "rationale": "Distinguishes between stable Fee-Related Earnings (FRE) and volatile Performance Fees (Carry).",
        },
        "Holding Company": {
            "methods": [
                {"name": "SOTP / NAV",     "weight": 0.70, "anchor": True,  "implementable": False, "proxy": "P/BV"},
                {"name": "NAV Discount",   "weight": 0.20, "anchor": False, "implementable": False, "proxy": "P/BV"},
                {"name": "DDM",            "weight": 0.10, "anchor": False, "implementable": True},
            ],
            "excluded": ["DCF"],
            "rationale": "Valuation is a sum of its parts; NAV discount reflects liquidity/management/tax frictions.",
        },
        "Payment Networks": {
            "methods": [
                {"name": "P/E (norm)",  "weight": 0.40, "anchor": True,  "implementable": True},
                {"name": "EV/EBITDA",   "weight": 0.25, "anchor": False, "implementable": True},
                {"name": "DCF",         "weight": 0.20, "anchor": False, "implementable": True},
                {"name": "FCF Yield",   "weight": 0.15, "anchor": False, "implementable": True},
            ],
            "excluded": ["EPV"],
            "rationale": (
                "Monopoly payment networks with 50%+ margins and regulated fee income. "
                "P/E anchors because earnings are highly predictable. "
                "EPV excluded — toll-road economics make EPV understate franchise value."
            ),
        },
        "Market Infrastructure": {
            "methods": [
                {"name": "P/E (norm)",  "weight": 0.35, "anchor": True,  "implementable": True},
                {"name": "EV/EBITDA",   "weight": 0.25, "anchor": False, "implementable": True},
                {"name": "DCF",         "weight": 0.25, "anchor": False, "implementable": True},
                {"name": "FCF Yield",   "weight": 0.15, "anchor": False, "implementable": True},
            ],
            "excluded": [],
            "rationale": (
                "Exchange and clearing monopolies with recurring data/listing fees. "
                "P/E anchors because earnings visibility is among the highest in financials."
            ),
        },
        "Brokerage": {
            "methods": [
                {"name": "P/E (norm)",  "weight": 0.35, "anchor": True,  "implementable": True},
                {"name": "P/BV",        "weight": 0.25, "anchor": False, "implementable": True},
                {"name": "DCF",         "weight": 0.20, "anchor": False, "implementable": True},
                {"name": "FCF Yield",   "weight": 0.20, "anchor": False, "implementable": True},
            ],
            "excluded": ["EPV"],
            "rationale": (
                "Deposit-funded brokerages with different economics from investment banks. "
                "No proprietary trading book; earnings driven by AUM and NII."
            ),
        },
    },

    # ── ENERGY ────────────────────────────────────────────────────────────────
    "Energy": {
        "Regulated Utility": {
            "methods": [
                {"name": "DCF",           "weight": 0.60, "anchor": True,  "implementable": True},
                {"name": "P/Rate Base",   "weight": 0.20, "anchor": False, "implementable": False, "proxy": "P/BV"},
                {"name": "Utility P/E",   "weight": 0.15, "anchor": False, "implementable": True},
                {"name": "DDM",           "weight": 0.05, "anchor": False, "implementable": True},
            ],
            "excluded": [],
            "rationale": "Returns are capped by regulators on the Rate Base, making DCF highly predictable.",
        },
        "Merchant Power": {
            "methods": [
                {"name": "EV/EBITDA",       "weight": 0.40, "anchor": True,  "implementable": True},
                {"name": "FCF Yield",       "weight": 0.30, "anchor": False, "implementable": True},
                {"name": "Power Price DCF", "weight": 0.20, "anchor": False, "implementable": True,  "note": "proxied by DCF"},
                {"name": "LBO Floor",       "weight": 0.10, "anchor": False, "implementable": True},
            ],
            "excluded": [],
            "rationale": "High operational leverage and cyclical commodity prices necessitate EBITDA and FCF focus.",
        },
        "IPP": {
            "methods": [
                {"name": "PPA-backed DCF", "weight": 0.50, "anchor": True,  "implementable": True,  "note": "proxied by DCF"},
                {"name": "NAV (Project)",  "weight": 0.30, "anchor": False, "implementable": False, "proxy": "P/BV"},
                {"name": "EV/EBITDA",      "weight": 0.15, "anchor": False, "implementable": True},
                {"name": "DDM",            "weight": 0.05, "anchor": False, "implementable": True},
            ],
            "excluded": [],
            "rationale": "Long-term contracts (PPAs) provide visibility for project-level cash flow modeling.",
        },
        "EPC Contractor": {
            "methods": [
                {"name": "Backlog DCF",  "weight": 0.50, "anchor": True,  "implementable": True,  "note": "proxied by DCF"},
                {"name": "EV/Backlog",   "weight": 0.30, "anchor": False, "implementable": False, "proxy": "EV/Revenue"},
                {"name": "EV/EBITDA",    "weight": 0.15, "anchor": False, "implementable": True},
                {"name": "Rev DCF",      "weight": 0.05, "anchor": False, "implementable": True},
            ],
            "excluded": [],
            "rationale": "Order backlog is the leading indicator of revenue; burn rate determines near-term value.",
        },
        "Energy Tech Licensor": {
            "methods": [
                {"name": "Licensing NPV",  "weight": 0.50, "anchor": True,  "implementable": False, "proxy": "EPV"},
                {"name": "Real Options",   "weight": 0.30, "anchor": False, "implementable": False, "proxy": "DCF"},
                {"name": "EV/Fwd Rev",     "weight": 0.15, "anchor": False, "implementable": True},
                {"name": "TAM Pen",        "weight": 0.05, "anchor": False, "implementable": False, "proxy": "EV/Revenue"},
            ],
            "excluded": [],
            "rationale": "Value is concentrated in IP; Real Options capture the value of future pivot technologies.",
        },
    },

    # ── TECH ──────────────────────────────────────────────────────────────────
    "Tech": {
        "Growth SaaS": {
            "methods": [
                # Rebalanced 2026-04-25: EV/NTM Revenue previously anchored at
                # 50% which effectively anchored intrinsic valuation to the
                # very market multiple we should be diverging from (reflexivity
                # risk). Observed on MNDY: 60.2% historical CAGR × aggressive
                # NTM multiple × growth_premium produced $475 IV on $65 spot.
                # New weights shift anchor to DCF-based methods (NRR-adj DCF,
                # Rev DCF, traditional FCF DCF together = 80%), relegate
                # EV/NTM Revenue to a 15% sanity check rather than the driver.
                # This trades some upside capture for valuation discipline —
                # appropriate for 'intrinsic' not 'momentum' valuation.
                {"name": "NRR-adj DCF",     "weight": 0.35, "anchor": True,  "implementable": True,  "note": "DCF with NRR-weighted cohort revenue"},
                {"name": "DCF",             "weight": 0.25, "anchor": False, "implementable": True,  "note": "traditional FCF DCF"},
                {"name": "Rev DCF (ARR)",   "weight": 0.20, "anchor": False, "implementable": True},
                {"name": "EV/NTM Revenue",  "weight": 0.15, "anchor": False, "implementable": True,  "note": "sanity check — market-anchor, demoted from 50% due to reflexivity risk"},
                {"name": "TAM Pen",         "weight": 0.05, "anchor": False, "implementable": False, "proxy": "EV/Revenue"},
            ],
            "excluded": [],
            "rationale": "Intrinsic valuation anchored to DCF-family methods (80%) with EV/NTM Revenue as a 15% sanity check. Prioritizes fundamentals over market-multiple reflexivity.",
        },
        # ── Hyperscaler / Tech Conglomerate profile ──────────────────────
        # For mega-cap multi-segment tech companies (AMZN, GOOGL, MSFT, META)
        # where massive CapEx investment ($50B-$130B+/yr) in cloud/AI infra
        # depresses FCF margin to near-zero despite strong EBITDA margins
        # (20-40%) and NI margins (10-20%).  FCF-dependent methods (EPV, DCF)
        # will severely undervalue these businesses because CapEx is growth
        # investment, not maintenance.  EV/EBITDA is the anchor because EBITDA
        # strips out the CapEx distortion.  P/E captures NI-level profitability.
        #
        # Key distinguishing metrics:
        #   - Revenue > $200B (mega-cap)
        #   - FCF margin < 10% despite EBITDA margin > 15%
        #   - CapEx/Revenue > 10% (heavy infra investment)
        #   - Revenue CAGR 8-25% (still growing at massive scale)
        "Hyperscaler / Tech Conglomerate": {
            "methods": [
                {"name": "EV/EBITDA",    "weight": 0.40, "anchor": True,  "implementable": True},
                {"name": "P/E",          "weight": 0.25, "anchor": False, "implementable": True},
                {"name": "DCF",          "weight": 0.25, "anchor": False, "implementable": True},
                {"name": "FCF Yield",    "weight": 0.10, "anchor": False, "implementable": True},
            ],
            "excluded": ["EPV", "LBO Floor"],
            "rationale": (
                "Mega-cap tech conglomerates have structurally depressed FCF margins "
                "due to massive growth CapEx (cloud, AI, logistics).  EV/EBITDA anchors "
                "because it strips CapEx distortion.  EPV excluded — it weights "
                "current FCF which is temporarily suppressed by investment cycles."
            ),
        },
        # ── Cybersecurity / Mission-Critical SaaS ─────────────────────
        # High-growth (CAGR 15-30%) with strong FCF (25-38%) but often GAAP-negative.
        # NRR > 120% = more resilient than standard SaaS during downturns.
        # "Zero Trust" secular tailwind justifies +1.5% TGR bump vs standard SaaS.
        # CRWD, PANW, ZS, FTNT, NET.
        "Cybersecurity / Mission-Critical SaaS": {
            "methods": [
                # Rebalanced 2026-04-25 (Gemini review): EV/Revenue at 35% is
                # still market-anchored. Shifted weight to DCF-family for
                # "hard-math" IV anchoring. NET at $205 spot with stored IV
                # $40 partly reflected EV/Revenue method at $45 with 35%
                # weight contributing while DCF method sat at near-zero due
                # to Gate B + negative trailing FCF margin. Rebalancing
                # doesn't rescue NET alone (Gate B forward-ROIC fix in
                # dcf_agent.py does that), but reduces reflexivity exposure
                # symmetric to Growth SaaS change.
                {"name": "DCF (FCF+)",   "weight": 0.30, "anchor": True,  "implementable": True,  "note": "DCF anchor with forward Y10 ROIC projection"},
                {"name": "NRR-adj DCF",  "weight": 0.25, "anchor": False, "implementable": True,  "note": "cohort-weighted revenue DCF"},
                {"name": "EV/Revenue",   "weight": 0.20, "anchor": False, "implementable": True,  "note": "demoted from 35% — market-anchor, reflexivity risk"},
                {"name": "P/E",          "weight": 0.15, "anchor": False, "implementable": True,  "note": "SBC-discounted for high-dilution profiles"},
                {"name": "EV/EBITDA",    "weight": 0.10, "anchor": False, "implementable": True},
            ],
            "excluded": ["EPV"],  # many are GAAP-negative; EPV produces near-zero
            "rationale": (
                "Cybersecurity companies have mission-critical demand with 120%+ NRR "
                "and 'Zero Trust' secular tailwind. DCF-family anchors (55%) provide "
                "fundamentals-based IV; EV/Revenue at 20% is a sanity check (demoted "
                "from 35% due to reflexivity risk). EPV excluded — GAAP losses make "
                "it meaningless. Higher TGR (+1.5% vs standard SaaS) reflects secular "
                "demand. Gate B uses forward Y10 ROIC projection to avoid the 'Capex vs OpEx trap' on scaling infra-SaaS."
            ),
        },
        "Mature SaaS": {
            "methods": [
                {"name": "EPV",           "weight": 0.40, "anchor": True,  "implementable": True},
                {"name": "DCF (2-stage)", "weight": 0.30, "anchor": False, "implementable": True},
                {"name": "EV/EBITDA",     "weight": 0.20, "anchor": False, "implementable": True},
                {"name": "LBO Floor",     "weight": 0.10, "anchor": False, "implementable": True},
            ],
            "excluded": [],
            "rationale": "Earnings Power Value tests the sustainability of current earnings without growth assumptions.",
        },
        "High-Growth Tech / AI": {
            "methods": [
                {"name": "Reverse DCF",      "weight": 0.40, "anchor": True,  "implementable": True},
                {"name": "TAM Penetration",  "weight": 0.30, "anchor": False, "implementable": False, "proxy": "EV/Revenue"},
                {"name": "EV/NTM Rev",       "weight": 0.20, "anchor": False, "implementable": True},
                {"name": "SOTP",             "weight": 0.10, "anchor": False, "implementable": False, "proxy": "EPV"},
            ],
            "excluded": [],
            "rationale": "High uncertainty in terminal states requires modeling backward from market share assumptions.",
        },
        "Hyper-Growth Platform": {
            "methods": [
                {"name": "DCF (FCF+)",      "weight": 0.45, "anchor": True,  "implementable": True},
                {"name": "EV/NTM Revenue",  "weight": 0.25, "anchor": False, "implementable": True},
                {"name": "EPV",             "weight": 0.20, "anchor": False, "implementable": True},
                {"name": "Power Law Score", "weight": 0.10, "anchor": False, "implementable": False, "proxy": "EV/EBITDA"},
            ],
            "excluded": [],
            "rationale": "High-growth + high-FCF companies require DCF anchored by FCF+ "
                         "with a forward revenue multiple to capture the category-king premium.",
        },
        "Mature Platform": {
            "methods": [
                {"name": "DCF (FCF+)",  "weight": 0.50, "anchor": True,  "implementable": True},
                {"name": "EPV",         "weight": 0.20, "anchor": False, "implementable": True},
                {"name": "EV/EBITDA",   "weight": 0.20, "anchor": False, "implementable": True},
                {"name": "LBO Floor",   "weight": 0.10, "anchor": False, "implementable": True},
            ],
            "excluded": [],
            "rationale": "Predictable cash flows allow for standard 2-stage DCF to be the primary anchor.",
        },
        "Early Platform": {
            "methods": [
                {"name": "GMV-TAM Pen",   "weight": 0.40, "anchor": True,  "implementable": False, "proxy": "EV/NTM Revenue"},
                {"name": "Unit Econ DCF", "weight": 0.30, "anchor": False, "implementable": True,  "note": "proxied by DCF"},
                {"name": "Rev DCF (GMV)", "weight": 0.20, "anchor": False, "implementable": True},
                {"name": "EV/GMV",        "weight": 0.10, "anchor": False, "implementable": False, "proxy": "EV/Revenue"},
            ],
            "excluded": [],
            "rationale": "Unit economics (LTV/CAC) at the transaction level matter more than consolidated P&L.",
        },
        "Levered Subscription": {
            "methods": [
                {"name": "DCF (Levered)",  "weight": 0.40, "anchor": True,  "implementable": True},
                {"name": "EV/EBITDA",      "weight": 0.30, "anchor": False, "implementable": True},
                {"name": "LBO Analysis",   "weight": 0.20, "anchor": False, "implementable": True},
                {"name": "Credit Metrics", "weight": 0.10, "anchor": False, "implementable": False, "proxy": "FCF Yield"},
            ],
            "excluded": [],
            "rationale": "Focus on ability to service debt (DSCR) and equity value post-interest payments.",
        },
    },

    # ── BIOPHARMA ─────────────────────────────────────────────────────────────
    "Biopharma": {
        "Pre-approval Biotech": {
            "methods": [
                {"name": "rNPV",          "weight": 0.45, "anchor": True,  "implementable": True},
                {"name": "EV/R&D",        "weight": 0.25, "anchor": False, "implementable": True},
                {"name": "Pipeline NAV",  "weight": 0.20, "anchor": False, "implementable": False, "proxy": "P/BV"},
                {"name": "Cash Runway",   "weight": 0.10, "anchor": False, "implementable": True},
            ],
            "excluded": ["P/E", "EPV", "EV/EBITDA"],
            "rationale": (
                "Pre-revenue biotech with negative earnings. rNPV anchors pipeline value "
                "using per-asset phase PoS × therapeutic-area multiplier × bell-shaped "
                "cash flow stream (ramp + plateau + LOE decay). EV/R&D values IP as a "
                "multiple of R&D investment (4-8x). P/E and EPV excluded — meaningless "
                "with negative earnings. When rNPV returns None (no pipeline extracted), "
                "weight flows to EV/R&D + Pipeline NAV + Cash Runway via blend fallback."
            ),
        },
        "Large Cap Pharma": {
            "methods": [
                {"name": "P/E",             "weight": 0.40, "anchor": True,  "implementable": True},
                {"name": "rNPV (Pipeline)", "weight": 0.30, "anchor": False, "implementable": True},
                {"name": "DCF",             "weight": 0.20, "anchor": False, "implementable": True},
                {"name": "EV/EBITDA",       "weight": 0.10, "anchor": False, "implementable": True},
            ],
            "excluded": [],
            "rationale": (
                "Blends steady earnings from off-patent drugs (P/E, DCF, EV/EBITDA) with "
                "risk-adjusted pipeline value (rNPV — same engine as Pre-approval Biotech, "
                "but uses base 8.5% WACC since diversified cash flows fund R&D without "
                "dilution). When rNPV returns None, weight flows to P/E/DCF/EV/EBITDA."
            ),
        },
        "Managed Care": {
            "methods": [
                {"name": "P/E (Ops)",  "weight": 0.40, "anchor": True,  "implementable": True},
                {"name": "EV/EBITDA",  "weight": 0.30, "anchor": False, "implementable": True},
                {"name": "DCF",        "weight": 0.20, "anchor": False, "implementable": True},
                {"name": "EPV",        "weight": 0.10, "anchor": False, "implementable": True},
            ],
            "excluded": [],
            "rationale": "Regulated margins (Medical Loss Ratio) make operational EPS a reliable proxy.",
        },
        "MedTech / Devices": {
            "methods": [
                {"name": "EV/Revenue",   "weight": 0.40, "anchor": True,  "implementable": True},
                {"name": "DCF (5-yr)",   "weight": 0.30, "anchor": False, "implementable": True},
                {"name": "P/E",          "weight": 0.20, "anchor": False, "implementable": True},
                {"name": "ROIC vs WACC", "weight": 0.10, "anchor": False, "implementable": True},
            ],
            "excluded": [],
            "rationale": "High R&D and patent protection lead to premium revenue multiples and long-cycle growth.",
        },
        "CDMO / Life Science Tools": {
            "methods": [
                {"name": "P/E",          "weight": 0.35, "anchor": True,  "implementable": True},
                {"name": "EV/EBITDA",    "weight": 0.25, "anchor": False, "implementable": True},
                {"name": "DCF",          "weight": 0.25, "anchor": False, "implementable": True},
                {"name": "FCF Yield",    "weight": 0.15, "anchor": False, "implementable": True},
            ],
            "excluded": [],
            "rationale": (
                "Contract research/manufacturing (TMO, DHR, WuXi) with recurring revenue. "
                "P/E anchors because earnings are stable. GLP-1 fill-finish demand drives "
                "structural tailwind above historical organic growth."
            ),
        },
    },

    # ── CONSUMER ──────────────────────────────────────────────────────────────
    "Consumer": {
        "Food & Beverage": {
            "methods": [
                {"name": "P/E",             "weight": 0.50, "anchor": True,  "implementable": True},
                {"name": "DCF (2-stage)",   "weight": 0.30, "anchor": False, "implementable": True},
                {"name": "EV/EBITDA",       "weight": 0.15, "anchor": False, "implementable": True},
                {"name": "Brand Valuation", "weight": 0.05, "anchor": False, "implementable": False, "proxy": "P/E"},
            ],
            "excluded": [],
            "rationale": "Stable margins and brand moats make P/E and DCF highly reliable.",
        },
        "Apparel / Athletic Wear": {
            "methods": [
                {"name": "EV/EBITDA",   "weight": 0.40, "anchor": True,  "implementable": True},
                {"name": "DCF (FCF+)",  "weight": 0.30, "anchor": False, "implementable": True},
                {"name": "P/E (norm)",  "weight": 0.20, "anchor": False, "implementable": True},
                {"name": "Brand Val",   "weight": 0.10, "anchor": False, "implementable": False, "proxy": "EV/Revenue"},
            ],
            "excluded": [],
            "rationale": "Brand-driven athletic/apparel companies valued on EV/EBITDA; DCF anchors the long-term growth thesis.",
        },
        "Household / Personal": {
            "methods": [
                {"name": "P/E",       "weight": 0.40, "anchor": True,  "implementable": True},
                {"name": "EV/EBITDA", "weight": 0.30, "anchor": False, "implementable": True},
                {"name": "DCF",       "weight": 0.20, "anchor": False, "implementable": True},
                {"name": "ROIC",      "weight": 0.10, "anchor": False, "implementable": True},
            ],
            "excluded": [],
            "rationale": "Brand loyalty and global distribution scale are captured through earnings multiples.",
        },
        "Traditional Retail": {
            "methods": [
                {"name": "EV/EBITDAR",   "weight": 0.50, "anchor": True,  "implementable": True,  "note": "proxied by EV/EBITDA"},
                {"name": "P/E",          "weight": 0.25, "anchor": False, "implementable": True},
                {"name": "ROIC vs WACC", "weight": 0.15, "anchor": False, "implementable": True},
                {"name": "FCF Yield",    "weight": 0.10, "anchor": False, "implementable": True},
            ],
            "excluded": [],
            "rationale": "Normalizes for heavy lease use; ROIC tests expansion and capital efficiency.",
        },
        "Luxury Goods": {
            "methods": [
                {"name": "P/E (Premium)", "weight": 0.50, "anchor": True,  "implementable": True},
                {"name": "EV/EBIT",       "weight": 0.25, "anchor": False, "implementable": True},
                {"name": "DCF (LTG)",     "weight": 0.20, "anchor": False, "implementable": True},
                {"name": "Brand Val",     "weight": 0.05, "anchor": False, "implementable": False, "proxy": "P/E"},
            ],
            "excluded": [],
            "rationale": "Pricing power and brand equity make P/E multiples stable and high.",
        },
        # ── Change 7: Consumer Growth profile ─────────────────────────────────
        # For fast-growing consumer brands (CAGR ≥ 15%) with strong FCF margins
        # (FCF margin ≥ 15%). Examples: CHAGEE (CHA), early-stage SBUX, Shake Shack.
        # Three-method blend: DCF anchors intrinsic value; EV/Revenue provides a
        # market-comp floor when earnings multiples are inflated by rapid growth;
        # EV/EBITDA triangulates on current profitability.
        # For Chinese ADR names, the peer multiples are haircut by cn_adr_haircut
        # factor (applied in dcf_agent._compute_method_value when reported_currency=CNY).
        "Consumer Growth": {
            "methods": [
                {"name": "DCF",         "weight": 0.50, "anchor": True,  "implementable": True},
                {"name": "EV/Revenue",  "weight": 0.30, "anchor": False, "implementable": True},
                {"name": "EV/EBITDA",   "weight": 0.20, "anchor": False, "implementable": True},
            ],
            "excluded": ["P/E"],  # P/E is unreliable at high-growth stage (PEG >3x)
            "rationale": (
                "High-growth consumer brands (CAGR ≥ 15%) with strong FCF margins are "
                "valued on a blend of DCF intrinsic value (50%) and revenue/EBITDA market comps. "
                "EV/Revenue anchors vs. peer brands at similar growth stage; EV/EBITDA provides "
                "a current-profitability floor. P/E excluded — inflated during hypergrowth phase."
            ),
        },
        # ── Membership / Subscription Retail profile ──────────────────────
        # For warehouse club and membership-model retailers (COST, BJ, SAMS)
        # where the profit engine is recurring membership fees, not merchandise
        # margins.  These businesses have intentionally thin operating margins
        # (~3-4%) to drive traffic, but membership economics create SaaS-like
        # recurring revenue with 90%+ retention.  Market consistently values
        # them at 40-55x P/E — far above traditional retail (15-20x) — because
        # fee income is high-margin, predictable, and growing.
        #
        # Key distinguishing metrics vs Traditional Retail:
        #   - FCF margin 2-4% (thin by design, NOT a quality signal)
        #   - Revenue CAGR 5-12% (mid-growth, not hyper-growth)
        #   - P/E 40-55x (premium annuity multiple)
        #   - Membership fee income > 50% of net income
        #
        # Method rationale: P/E anchors because the market prices membership
        # economics through earnings multiples.  DCF captures long-duration
        # compounding.  FCF Yield provides a floor despite thin margins.
        # EV/EBITDAR (low weight) normalizes for lease-heavy operations.
        "Membership / Subscription Retail": {
            "methods": [
                {"name": "P/E",          "weight": 0.40, "anchor": True,  "implementable": True},
                {"name": "DCF",          "weight": 0.30, "anchor": False, "implementable": True},
                {"name": "FCF Yield",    "weight": 0.20, "anchor": False, "implementable": True},
                {"name": "EV/EBITDAR",   "weight": 0.10, "anchor": False, "implementable": True, "note": "proxied by EV/EBITDA"},
            ],
            "excluded": [],
            "rationale": (
                "Membership-model retailers earn the majority of net income from "
                "recurring membership fees with 90%+ renewal rates.  Market values "
                "them at a structural premium (40-55x P/E) vs traditional retail "
                "(15-20x) due to subscription economics, not merchandise margins."
            ),
        },
        # ── Consumer Durables ─────────────────────────────────────────────────
        # Appliances, home furnishings, electronics, outdoor/fitness devices.
        # Cyclical demand tied to housing cycle + consumer confidence.
        # Asset-heavier than apparel; lower multiples (10x EV/EBITDA, 16x P/E).
        # Examples: WHR, GRMN, MHK, TPX; HK: Haier (6690), VTech (0303).
        "Consumer Durables": {
            "methods": [
                {"name": "EV/EBITDA",  "weight": 0.40, "anchor": True,  "implementable": True},
                {"name": "P/E",        "weight": 0.25, "anchor": False, "implementable": True},
                {"name": "DCF",        "weight": 0.20, "anchor": False, "implementable": True},
                {"name": "FCF Yield",  "weight": 0.15, "anchor": False, "implementable": True},
            ],
            "excluded": [],
            "rationale": (
                "Cyclical consumer durables valued on EV/EBITDA (normalizes for "
                "capital intensity and housing-cycle swings). P/E provides market "
                "sanity check; FCF Yield tests cash conversion despite capex."
            ),
        },
        # ── Automotive & EV ───────────────────────────────────────────────────
        # Consumer-facing EV makers and traditional auto OEMs with DTC models.
        # Pre-profit or thin-margin companies (RIVN, LCID, XPeng): EV/Revenue
        # anchors because earnings multiples are meaningless.  Profitable EV
        # leaders (TSLA, BYD, Li Auto): blend shifts toward EV/EBITDA and P/E.
        # P/BV captures manufacturing asset base (gigafactories, battery plants).
        # EPV excluded — cyclical + high capex makes normalized earnings unreliable.
        "Automotive & EV": {
            "methods": [
                {"name": "EV/Revenue", "weight": 0.35, "anchor": True,  "implementable": True},
                {"name": "DCF",        "weight": 0.30, "anchor": False, "implementable": True},
                {"name": "P/BV",       "weight": 0.20, "anchor": False, "implementable": True},
                {"name": "EV/EBITDA",  "weight": 0.15, "anchor": False, "implementable": True},
            ],
            "excluded": ["EPV", "P/E"],
            "rationale": (
                "EV/Auto companies span pre-revenue to profitable.  EV/Revenue "
                "anchors the cohort because many are pre-profit or thin-margin.  "
                "P/BV captures gigafactory and battery asset base.  P/E excluded "
                "for pre-profit names; EPV excluded due to cyclical capex."
            ),
        },
        # ── Travel & Dining ───────────────────────────────────────────────────
        # Hotels, OTAs, restaurants, theme parks, gaming/leisure.
        # Asset-light platforms (ABNB, BKNG, Trip.com) coexist with asset-heavy
        # operators (DIS parks, Galaxy Ent casinos, Haidilao restaurants).
        # EV/EBITDA anchors because it normalizes across CapEx profiles.
        # P/E captures franchise/royalty streams (MCD, SBUX, Sands China).
        "Travel & Dining": {
            "methods": [
                {"name": "EV/EBITDA",  "weight": 0.35, "anchor": True,  "implementable": True},
                {"name": "P/E",        "weight": 0.30, "anchor": False, "implementable": True},
                {"name": "DCF",        "weight": 0.25, "anchor": False, "implementable": True},
                {"name": "FCF Yield",  "weight": 0.10, "anchor": False, "implementable": True},
            ],
            "excluded": [],
            "rationale": (
                "Travel & dining spans asset-light platforms (ABNB, BKNG) and "
                "asset-heavy operators (DIS parks, casinos).  EV/EBITDA normalizes "
                "across CapEx profiles.  P/E captures franchise economics (MCD, SBUX)."
            ),
        },
    },

    # ── INDUSTRIALS ───────────────────────────────────────────────────────────
    "Industrials": {
        "Aerospace & Defense": {
            "methods": [
                {"name": "EV/EBITDA",   "weight": 0.40, "anchor": True,  "implementable": True},
                {"name": "Backlog DCF", "weight": 0.30, "anchor": False, "implementable": True,  "note": "proxied by DCF"},
                {"name": "FCF Yield",   "weight": 0.20, "anchor": False, "implementable": True},
                {"name": "P/E",         "weight": 0.10, "anchor": False, "implementable": True},
            ],
            "excluded": [],
            "rationale": "Long-cycle backlog visibility drives value; FCF yield tests cash conversion from progress payments.",
        },
        "Automotive (OEM)": {
            "methods": [
                {"name": "EV/EBITDA",  "weight": 0.40, "anchor": True,  "implementable": True},
                {"name": "P/E",        "weight": 0.30, "anchor": False, "implementable": True},
                {"name": "P/BV",       "weight": 0.20, "anchor": False, "implementable": True},
                {"name": "FCF Yield",  "weight": 0.10, "anchor": False, "implementable": True},
            ],
            "excluded": [],
            "rationale": "Capital-intensive and cyclical; P/B serves as a floor for manufacturing assets.",
        },
        "Capital Goods": {
            "methods": [
                {"name": "EV/EBITDA",    "weight": 0.40, "anchor": True,  "implementable": True},
                {"name": "FCF Yield",    "weight": 0.30, "anchor": False, "implementable": True},
                {"name": "ROIC vs WACC", "weight": 0.20, "anchor": False, "implementable": True},
                {"name": "P/E",          "weight": 0.10, "anchor": False, "implementable": True},
            ],
            "excluded": [],
            "rationale": "Efficiency focused; ROIC/WACC spread is the ultimate driver of multiple expansion.",
        },
    },

    # ── TELCO ─────────────────────────────────────────────────────────────────
    "Telco": {
        "Stable Growth": {
            "methods": [
                {"name": "EPV",           "weight": 0.40, "anchor": True,  "implementable": True},
                {"name": "DCF (2-stage)", "weight": 0.30, "anchor": False, "implementable": True},
                {"name": "Rev DCF",       "weight": 0.20, "anchor": False, "implementable": True},
                {"name": "LBO Floor",     "weight": 0.10, "anchor": False, "implementable": True},
            ],
            "excluded": [],
            "rationale": "EPV serves as a no-growth floor; DCF captures the value of future reinvestment.",
        },
    },

    # ── CRYPTO ────────────────────────────────────────────────────────────────
    "Crypto": {
        "Pre-Revenue Tech": {
            "methods": [
                {"name": "Scenario IV",     "weight": 0.50, "anchor": True,  "implementable": False, "proxy": "DCF"},
                {"name": "Comp Trans",      "weight": 0.20, "anchor": False, "implementable": False, "proxy": "EV/Revenue"},
                {"name": "Rev DCF (Mkt Sh)","weight": 0.20, "anchor": False, "implementable": True},
                {"name": "TAM Pen",         "weight": 0.10, "anchor": False, "implementable": False, "proxy": "EV/Revenue"},
            ],
            "excluded": [],
            "rationale": "In the absence of cash, value is derived from binary success/failure probability nodes.",
        },
    },

    "RealEstate": {
        "REIT": {
            "methods": [
                {"name": "NAV (Cap Rates)", "weight": 0.50, "anchor": True,  "implementable": True,  "scenario_invariant": True},
                {"name": "P/FFO",           "weight": 0.30, "anchor": False, "implementable": True},
                {"name": "P/AFFO",          "weight": 0.15, "anchor": False, "implementable": True},
                {"name": "DDM",             "weight": 0.05, "anchor": False, "implementable": True},
            ],
            "excluded": ["DCF", "P/BV"],
            "rationale": (
                "REITs are valued on asset quality and distributable cash. NAV (Cap "
                "Rates) anchors to property value via NOI/cap_rate − debt + cash; "
                "scenario-invariant because NAV is asset-backed and doesn't scale "
                "bear/base/bull like growth methods. P/FFO and P/AFFO use REIT-"
                "specific cash multiples (not P/E — GAAP earnings are depressed by "
                "non-cash real-estate depreciation). AFFO-gated DDM prevents yield-"
                "trap valuations of unsustainable distributions. DCF and P/BV "
                "excluded — DCF is irrelevant for high-payout trusts, P/BV is "
                "superseded by NAV."
            ),
        },
    },

    "Transportation": {
        "Airlines": {
            "methods": [
                {"name": "EV/EBITDAR",   "weight": 0.50, "anchor": True,  "implementable": True,  "note": "proxied by EV/EBITDA"},
                {"name": "FCF Yield",    "weight": 0.20, "anchor": False, "implementable": True},
                {"name": "P/BV (Fleet)", "weight": 0.20, "anchor": False, "implementable": False, "proxy": "P/BV"},
                {"name": "P/E",          "weight": 0.10, "anchor": False, "implementable": True},
            ],
            "excluded": [],
            "rationale": "Lease-adjusted EBITDAR normalises for aircraft financing structure; FCF validates cash conversion.",
        },
        "Rail / Logistics": {
            "methods": [
                {"name": "EV/EBITDA", "weight": 0.40, "anchor": True,  "implementable": True},
                {"name": "FCF Yield", "weight": 0.30, "anchor": False, "implementable": True},
                {"name": "P/E",       "weight": 0.20, "anchor": False, "implementable": True},
                {"name": "DCF",       "weight": 0.10, "anchor": False, "implementable": True},
            ],
            "excluded": [],
            "rationale": "Regulated networks with stable volumes support EBITDA multiples; FCF yield reflects high capex.",
        },
    },

    "Materials": {
        "Steel / Metals": {
            "methods": [
                {"name": "EV/EBITDA (Norm)", "weight": 0.50, "anchor": True,  "implementable": True,  "note": "proxied by EV/EBITDA"},
                {"name": "P/BV",             "weight": 0.25, "anchor": False, "implementable": True},
                {"name": "FCF Yield",        "weight": 0.15, "anchor": False, "implementable": True},
                {"name": "P/E",              "weight": 0.10, "anchor": False, "implementable": True},
            ],
            "excluded": [],
            "rationale": "Normalised mid-cycle EBITDA smooths commodity price volatility; P/BV provides asset floor.",
        },
        "Specialty Chemicals": {
            "methods": [
                {"name": "EV/EBITDA", "weight": 0.40, "anchor": True,  "implementable": True},
                {"name": "P/E",       "weight": 0.30, "anchor": False, "implementable": True},
                {"name": "FCF Yield", "weight": 0.20, "anchor": False, "implementable": True},
                {"name": "ROIC",      "weight": 0.10, "anchor": False, "implementable": True},
            ],
            "excluded": [],
            "rationale": "Speciality premium is captured through earnings multiples; ROIC tests pricing power vs. cost of capital.",
        },
    },

    "Resources": {
        "Upstream Oil & Gas": {
            "methods": [
                {"name": "NAV (PV-10)",  "weight": 0.60, "anchor": True,  "implementable": False, "proxy": "DCF"},
                {"name": "EV/DACF",      "weight": 0.25, "anchor": False, "implementable": False, "proxy": "EV/EBITDA"},
                {"name": "P/CF",         "weight": 0.10, "anchor": False, "implementable": True},
                {"name": "Real Options", "weight": 0.05, "anchor": False, "implementable": False, "proxy": "DCF"},
            ],
            "excluded": [],
            "rationale": "Reserve NPV (PV-10) at strip pricing is the industry standard; EV/DACF normalises for D&A distortions.",
        },
        "Mining (Major)": {
            "methods": [
                {"name": "NAV (LoM)",          "weight": 0.60, "anchor": True,  "implementable": False, "proxy": "DCF"},
                {"name": "P/NAV",              "weight": 0.20, "anchor": False, "implementable": False, "proxy": "P/BV"},
                {"name": "EV/EBITDA (norm)",   "weight": 0.15, "anchor": False, "implementable": True,  "note": "proxied by EV/EBITDA"},
                {"name": "Price/CF",           "weight": 0.05, "anchor": False, "implementable": True},
            ],
            "excluded": [],
            "rationale": "Life-of-mine NAV discounts all future ore bodies; P/NAV premium reflects management and jurisdiction quality.",
        },
    },

    "ProfessionalServices": {
        "Ad / Consulting": {
            "methods": [
                {"name": "EV/EBIT (Pre-bonus)", "weight": 0.40, "anchor": True,  "implementable": True,  "note": "proxied by EV/EBIT"},
                {"name": "FCF Yield",           "weight": 0.30, "anchor": False, "implementable": True},
                {"name": "P/E",                 "weight": 0.20, "anchor": False, "implementable": True},
                {"name": "Rev DCF",             "weight": 0.10, "anchor": False, "implementable": True},
            ],
            "excluded": [],
            "rationale": "Pre-bonus EBIT normalises for variable staff compensation; FCF yield tests cash conversion quality.",
        },
        "Payment Processors": {
            "methods": [
                {"name": "EV/Gross Profit", "weight": 0.40, "anchor": True,  "implementable": False, "proxy": "EV/Revenue"},
                {"name": "EV/Volume",       "weight": 0.30, "anchor": False, "implementable": False, "proxy": "EV/Revenue"},
                {"name": "DCF",             "weight": 0.20, "anchor": False, "implementable": True},
                {"name": "Rule of 40",      "weight": 0.10, "anchor": False, "implementable": False, "proxy": "FCF Yield"},
            ],
            "excluded": [],
            "rationale": "Network-effect businesses trade on volume and take-rate expansion; DCF anchors terminal value.",
        },
        # ── IT Services ──────────────────────────────────────────────────
        # Human-capital businesses (marginal cost > 0). Separated from Tech
        # because scalable IP (marginal cost ~ 0) requires different multiples.
        # ACN ($70B), IBM ($68B), CTSH ($21B), INFY ($19B), WIT.
        # P/E anchors because earnings stability is high. FCF Yield as floor.
        "IT Services": {
            "methods": [
                {"name": "P/E",          "weight": 0.35, "anchor": True,  "implementable": True},
                {"name": "EV/EBITDA",    "weight": 0.25, "anchor": False, "implementable": True},
                {"name": "DCF",          "weight": 0.25, "anchor": False, "implementable": True},
                {"name": "FCF Yield",    "weight": 0.15, "anchor": False, "implementable": True},
            ],
            "excluded": [],
            "rationale": (
                "IT services are human-capital businesses with stable earnings and "
                "moderate margins (15-20% NI). P/E anchors because earnings are the "
                "primary value driver. Lower growth (4-6% CAGR) than software."
            ),
        },
    },

    # ── SEMICONDUCTOR ─────────────────────────────────────────────────────────
    # Separate from Tech because semiconductor companies have:
    #   - Heavy CapEx cycles (fab buildouts $10-30B+) that suppress FCF
    #   - Cyclical demand patterns (memory/DRAM/NAND boom-bust)
    #   - Strong EBITDA margins (30-70%) despite low FCF margins during investment
    #   - Earnings volatility that makes EPV (perpetuity assumption) nonsensical
    # EV/EBITDA anchors because it strips CapEx distortion. EPV excluded.
    "Semiconductor": {
        "Fabless": {
            "methods": [
                {"name": "P/E",          "weight": 0.35, "anchor": True,  "implementable": True},
                {"name": "EV/EBITDA",    "weight": 0.25, "anchor": False, "implementable": True},
                {"name": "DCF",          "weight": 0.25, "anchor": False, "implementable": True},
                {"name": "EV/Revenue",   "weight": 0.15, "anchor": False, "implementable": True},
            ],
            "excluded": ["EPV", "LBO Floor"],
            "rationale": (
                "Fabless semis (NVDA, AVGO, QCOM, AMD, MRVL) have high margins and "
                "low CapEx. P/E anchors because earnings are the primary value driver. "
                "EV/Revenue captures growth premium for high-growth names."
            ),
        },
        "IDM / Foundry": {
            "methods": [
                {"name": "EV/EBITDA",    "weight": 0.40, "anchor": True,  "implementable": True},
                {"name": "P/E",          "weight": 0.25, "anchor": False, "implementable": True},
                {"name": "DCF",          "weight": 0.25, "anchor": False, "implementable": True},
                {"name": "FCF Yield",    "weight": 0.10, "anchor": False, "implementable": True},
            ],
            "excluded": ["EPV", "LBO Floor"],
            "rationale": (
                "IDMs and foundries (MU, INTC, TSM, TXN, GFS) have massive fab CapEx "
                "that suppresses FCF. EV/EBITDA anchors because it strips CapEx "
                "distortion. EPV excluded — cyclical earnings make perpetuity nonsensical."
            ),
        },
        "Equipment / EDA": {
            "methods": [
                {"name": "P/E",          "weight": 0.35, "anchor": True,  "implementable": True},
                {"name": "DCF",          "weight": 0.30, "anchor": False, "implementable": True},
                {"name": "EV/EBITDA",    "weight": 0.20, "anchor": False, "implementable": True},
                {"name": "FCF Yield",    "weight": 0.15, "anchor": False, "implementable": True},
            ],
            "excluded": [],
            "rationale": (
                "Semi equipment (ASML, AMAT, LRCX, KLAC) and EDA (SNPS, CDNS) are "
                "asset-lighter with strong FCF. P/E anchors with DCF as primary check. "
                "Equipment demand is cyclical but less volatile than memory."
            ),
        },
        "OSAT / Packaging": {
            "methods": [
                {"name": "EV/EBITDA",    "weight": 0.40, "anchor": True,  "implementable": True},
                {"name": "P/E",          "weight": 0.25, "anchor": False, "implementable": True},
                {"name": "P/BV",         "weight": 0.20, "anchor": False, "implementable": True},
                {"name": "FCF Yield",    "weight": 0.15, "anchor": False, "implementable": True},
            ],
            "excluded": ["EPV"],
            "rationale": (
                "OSAT providers (ASX, AMKR) are asset-heavy with thin margins. "
                "EV/EBITDA anchors; P/BV provides asset floor for capital-intensive operations."
            ),
        },
    },
}


# ── Sector peer multiples for relative valuation ──────────────────────────────
# EV/EBITDA and P/E peer medians used by multi-method engine in dcf_agent.py.
# Source: Damodaran sector multiples, January 2026.
    # growth_avg: median sector revenue CAGR (3-5yr).  Source: Damodaran sector
    # data Jan 2026 + FMP universe screening.  Used by dcf_agent.py to compute a
    # PEG-inspired growth premium/discount on relative-value multiples.
    # A company growing 2× its sector avg receives ~1.30× the base multiple;
    # a company growing 0.5× receives ~0.85×.  See _GROWTH_SENSITIVITY in dcf_agent.py.
SECTOR_PEER_MULTIPLES: dict[str, dict[str, float]] = {
    "Tech":                {"ev_ebitda": 22.0, "pe": 28.0, "ev_revenue": 6.5,  "pb": 6.0,  "fcf_yield": 0.035, "growth_avg": 0.12},
    # cn_adr_haircut: Chinese ADR names trade at ~40% of Western peer multiples (2025 discount).
    # Applied in dcf_agent._compute_method_value when reported_currency == "CNY".
    "Consumer":            {"ev_ebitda": 14.0, "pe": 20.0, "ev_revenue": 2.5,  "pb": 3.5,  "fcf_yield": 0.045, "cn_adr_haircut": 0.40, "growth_avg": 0.05},
    "Biopharma":           {"ev_ebitda": 16.0, "pe": 22.0, "ev_revenue": 5.0,  "pb": 4.0,  "fcf_yield": 0.040, "growth_avg": 0.08},
    "MedTech / Devices":   {"ev_ebitda": 20.0, "pe": 30.0, "ev_revenue": 6.0,  "pb": 5.0,  "fcf_yield": 0.030, "growth_avg": 0.10},
    "CDMO / Life Science Tools": {"ev_ebitda": 17.0, "pe": 26.0, "ev_revenue": 5.0,  "pb": 5.0,  "fcf_yield": 0.035, "growth_avg": 0.07, "ev_rd": 6.0},
    "Pre-approval Biotech": {"ev_ebitda": 16.0, "pe": 22.0, "ev_revenue": 5.0,  "pb": 4.0,  "fcf_yield": 0.040, "growth_avg": 0.08, "ev_rd": 6.0},
    "Telco":               {"ev_ebitda": 8.5,  "pe": 14.0, "ev_revenue": 2.0,  "pb": 2.0,  "fcf_yield": 0.060, "growth_avg": 0.03},
    "Crypto":              {"ev_ebitda": 20.0, "pe": 35.0, "ev_revenue": 8.0,  "pb": 3.0,  "fcf_yield": 0.030, "growth_avg": 0.25},
    "Energy":              {"ev_ebitda": 10.0, "pe": 16.0, "ev_revenue": 2.5,  "pb": 1.8,  "fcf_yield": 0.055, "growth_avg": 0.04},
    # Regulated Utility sub-profile: higher EV/EBITDA (12.5x) and P/E (18x) than
    # generic Energy (10x / 16x) because regulated rate base provides earnings
    # visibility and lower cost of equity.  Benchmarks: NEE 14x, SO 12x, DUK 12x,
    # D 11–13x — mid-range 12.5x base.  FCF yield lower (4.5%) reflecting
    # capital-intensive reinvestment cycle (capex > depreciation for rate base growth).
    "Regulated Utility":   {"ev_ebitda": 12.5, "pe": 18.0, "ev_revenue": 3.0,  "pb": 2.0,  "fcf_yield": 0.045, "growth_avg": 0.04},
    # IPP / Merchant Power: riskier than regulated; closer to generic Energy
    "IPP":                 {"ev_ebitda": 9.0,  "pe": 14.0, "ev_revenue": 2.0,  "pb": 1.5,  "fcf_yield": 0.060, "growth_avg": 0.06},
    "Financials":          {"ev_ebitda": 12.0, "pe": 12.0, "ev_revenue": 2.0,  "pb": 1.4,  "fcf_yield": 0.065, "growth_avg": 0.06},
    # Financials sub-profile overrides — keyed on profile_name for dcf_agent lookup
    # Banks use P/E and P/TBV; EV/EBITDA is not applicable
    "Money Center Bank":   {"ev_ebitda": 11.0, "pe": 11.0, "ev_revenue": 2.5,  "pb": 1.3,  "fcf_yield": 0.060, "growth_avg": 0.05},
    "Regional Bank":       {"ev_ebitda": 10.0, "pe": 10.0, "ev_revenue": 2.0,  "pb": 1.1,  "fcf_yield": 0.065, "growth_avg": 0.04},
    "Insurance":           {"ev_ebitda": 10.0, "pe": 11.0, "ev_revenue": 1.5,  "pb": 1.3,  "fcf_yield": 0.060, "growth_avg": 0.05},
    "Investment Bank":     {"ev_ebitda": 12.0, "pe": 13.0, "ev_revenue": 2.5,  "pb": 1.5,  "fcf_yield": 0.055, "growth_avg": 0.06},
    "Asset Manager":       {"ev_ebitda": 13.0, "pe": 14.0, "ev_revenue": 3.0,  "pb": 2.5,  "fcf_yield": 0.055, "growth_avg": 0.08},
    "FinTech":             {"ev_ebitda": 18.0, "pe": 22.0, "ev_revenue": 5.0,  "pb": 4.0,  "fcf_yield": 0.040, "growth_avg": 0.15},
    # GSEs: valued on P/E with conservatorship discount; EV/EBITDA does not apply
    # P/E 9x reflects political binary risk premium vs. 11x for regular banks
    "Mortgage/GSE":        {"ev_ebitda": 10.0, "pe": 9.0,  "ev_revenue": 3.0,  "pb": 0.4,  "fcf_yield": 0.075, "growth_avg": 0.03},
    "Payment Networks":    {"ev_ebitda": 25.0, "pe": 32.0, "ev_revenue": 15.0, "pb": 12.0, "fcf_yield": 0.030, "growth_avg": 0.10},
    "Market Infrastructure": {"ev_ebitda": 22.0, "pe": 28.0, "ev_revenue": 10.0, "pb": 8.0,  "fcf_yield": 0.035, "growth_avg": 0.08},
    "Brokerage":           {"ev_ebitda": 12.0, "pe": 15.0, "ev_revenue": 3.0,  "pb": 2.0,  "fcf_yield": 0.055, "growth_avg": 0.05},
    # Membership / Subscription Retail: COST, BJ, SAMS.
    # P/E 48x reflects 5-year average for COST (range 40-55x); NOT traditional retail.
    # EV/EBITDA 30x: membership fee income creates a structural premium over 14x retail.
    # FCF yield 2.0%: thin margins by design (membership-model), not a quality deficiency.
    # Benchmarks: COST 48-52x P/E, BJ 28-35x P/E (discount for scale gap).
    "Membership / Subscription Retail": {"ev_ebitda": 30.0, "pe": 48.0, "ev_revenue": 1.8,  "pb": 15.0, "fcf_yield": 0.020, "growth_avg": 0.08},
    # Consumer Durables: appliances, home furnishings — cyclical, asset-heavy.
    # Damodaran Household Products 16x PE, Furn/Home 12x → blended 16x; EV/EBITDA 10x.
    "Consumer Durables":   {"ev_ebitda": 10.0, "pe": 16.0, "ev_revenue": 1.5,  "pb": 2.5,  "fcf_yield": 0.055, "growth_avg": 0.04},
    # Automotive & EV: blended traditional + EV. TSLA 65x, BYD 25x, F 6x, GM 5x → 40x median
    # for growth-weighted cohort.  EV/EBITDA 25x (EV premium); EV/Revenue 3.5x.
    "Automotive & EV":     {"ev_ebitda": 25.0, "pe": 40.0, "ev_revenue": 3.5,  "pb": 6.0,  "fcf_yield": 0.025, "growth_avg": 0.18},
    # Travel & Dining: MCD 25x, SBUX 22x, DIS 20x, ABNB 25x, BKNG 22x → 22x median.
    # EV/EBITDA 14x (franchise/lease normalize). Growth avg 8% (travel recovery plateau).
    "Travel & Dining":     {"ev_ebitda": 14.0, "pe": 22.0, "ev_revenue": 3.0,  "pb": 6.0,  "fcf_yield": 0.040, "growth_avg": 0.08},
    "Industrials":         {"ev_ebitda": 13.0, "pe": 18.0, "ev_revenue": 2.0,  "pb": 3.0,  "fcf_yield": 0.050, "growth_avg": 0.06},
    "RealEstate":          {"ev_ebitda": 20.0, "pe": 35.0, "ev_revenue": 8.0,  "pb": 1.5,  "fcf_yield": 0.045, "growth_avg": 0.05},
    # REIT: SGX/APAC REITs trade at tighter multiples than US REITs (lower growth).
    # P/B around 0.9-1.0 (trading near NAV), P/E 12-15x (distributable income focus),
    # FCF yield ~6-7% (higher payout ratio norm).
    "REIT":                {"ev_ebitda": 15.0, "pe": 14.0, "ev_revenue": 6.0,  "pb": 1.0,  "fcf_yield": 0.065, "growth_avg": 0.03},
    "Transportation":      {"ev_ebitda": 8.0,  "pe": 12.0, "ev_revenue": 1.5,  "pb": 2.0,  "fcf_yield": 0.065, "growth_avg": 0.05},
    "Materials":           {"ev_ebitda": 8.0,  "pe": 12.0, "ev_revenue": 1.2,  "pb": 1.5,  "fcf_yield": 0.065, "growth_avg": 0.04},
    "Resources":           {"ev_ebitda": 6.0,  "pe": 12.0, "ev_revenue": 2.0,  "pb": 1.5,  "fcf_yield": 0.070, "growth_avg": 0.04},
    "ProfessionalServices":{"ev_ebitda": 15.0, "pe": 22.0, "ev_revenue": 3.0,  "pb": 5.0,  "fcf_yield": 0.045, "growth_avg": 0.07},
    # Semiconductor: Damo Semiconductor PE=28.4, EV/EBITDA=22.7 (Jan 2026)
    # Fabless (NVDA, AVGO) trade at premium; IDM/foundry at discount.
    # Sector median used here; sub-profile routing handles differentiation via growth_premium.
    "Semiconductor":       {"ev_ebitda": 20.0, "pe": 25.0, "ev_revenue": 6.0,  "pb": 5.0,  "fcf_yield": 0.035, "growth_avg": 0.12},
}

# ── HK / HKEX sector peer multiples ──────────────────────────────────────────
# Source: Hang Seng Index sector benchmarks, calibrated 2026-04.
#
# P/E benchmarks and proxies:
#   Tech        33.6x  — Hang Seng TECH Index (30 largest: Tencent, Alibaba, Meituan, etc.)
#   Consumer    22.0x  — HSI Commerce & Industry (mid-range of 18.5x–25.0x Discretionary/Staples)
#   Biopharma   42.0x  — Hang Seng Healthcare Index (mid-range of 38x–45x;
#                         Chemical Meds ~15–20x pull the index lower vs pure Biotech)
#   Telco       13.0x  — HSI Communication Services (mid-range of 11.5x–14.0x;
#                         China Mobile/Telecom/Unicom SOE-compressed)
#   Energy      18.0x  — HSI Energy sub-index (Integrated Oil & Gas 18x; New Energy 40–57x blended)
#   Financials   7.7x  — Hang Seng Finance Index (mid-range of 6.5x–8.8x;
#                         SOE/dividend discount vs US; banks dominate at ~7x)
#   Industrials 14.0x  — HSI Commerce & Industry (mid-range of 12.0x–16.5x;
#                         Machinery and Electrical components — significantly below US 18x)
#   RealEstate   7.5x  — Hang Seng Properties Index (mid-range of 5.8x–9.5x;
#                         Developer discount; Services trade higher ~15x but are a minority)
#
# EV/EBITDA and EV/Revenue: calibrated from HK/China company filings and broker consensus.
# P/B: sourced from HSI sub-index book value ratios.
# FCF yield: approximate inverse of P/FCF for each sector, adjusted for HK payout norms.
HK_SECTOR_PEER_MULTIPLES: dict[str, dict[str, float]] = {
    #                           ev_ebitda   pe      ev_revenue  pb      fcf_yield   growth_avg
    "Tech":         {"ev_ebitda": 15.0, "pe": 33.6, "ev_revenue": 3.5, "pb": 4.0, "fcf_yield": 0.035, "growth_avg": 0.10},
    "Consumer":     {"ev_ebitda":  9.0, "pe": 22.0, "ev_revenue": 1.5, "pb": 2.0, "fcf_yield": 0.045, "growth_avg": 0.06},
    # HK Biopharma: pe=42x for profitable pharma (Hansoh, CSPC). Pre-revenue biotech
    # should use EV/R&D 5-8x instead of P/E (earnings negative). ev_rd=6.5 (midpoint).
    "Biopharma":    {"ev_ebitda": 18.0, "pe": 42.0, "ev_revenue": 5.0, "pb": 3.5, "fcf_yield": 0.025, "growth_avg": 0.10, "ev_rd": 6.5},
    "Telco":        {"ev_ebitda":  6.5, "pe": 13.0, "ev_revenue": 1.4, "pb": 1.2, "fcf_yield": 0.070, "growth_avg": 0.03},
    "Crypto":       {"ev_ebitda": 20.0, "pe": 35.0, "ev_revenue": 8.0, "pb": 3.0, "fcf_yield": 0.030, "growth_avg": 0.20},
    "Energy":       {"ev_ebitda":  7.5, "pe": 18.0, "ev_revenue": 1.3, "pb": 1.1, "fcf_yield": 0.055, "growth_avg": 0.04},
    "Financials":   {"ev_ebitda":  8.5, "pe":  7.7, "ev_revenue": 1.4, "pb": 0.7, "fcf_yield": 0.090, "growth_avg": 0.05},
    "Industrials":  {"ev_ebitda":  8.0, "pe": 14.0, "ev_revenue": 1.2, "pb": 1.5, "fcf_yield": 0.060, "growth_avg": 0.05},
    "RealEstate":   {"ev_ebitda":  8.0, "pe":  7.5, "ev_revenue": 2.0, "pb": 0.6, "fcf_yield": 0.080, "growth_avg": 0.04},
    "Transportation":{"ev_ebitda": 6.5, "pe": 14.0, "ev_revenue": 1.1, "pb": 1.3, "fcf_yield": 0.060, "growth_avg": 0.05},
    "Materials":    {"ev_ebitda":  7.0, "pe": 14.0, "ev_revenue": 1.0, "pb": 1.1, "fcf_yield": 0.055, "growth_avg": 0.04},
    # Consumer sub-profile HK overrides:
    # Consumer Durables HK: Haier 8x EV/EBITDA, VTech 10x, Hisense 7x → 8x median.
    "Consumer Durables": {"ev_ebitda": 8.0, "pe": 12.0, "ev_revenue": 0.8, "pb": 1.5, "fcf_yield": 0.060, "growth_avg": 0.06},
    # Automotive & EV HK: BYD 25x PE, Li Auto 30x, XPeng 40x (pre-profit premium) → 30x median.
    # Higher growth_avg (25%) — China EV penetration >50%, still accelerating.
    "Automotive & EV": {"ev_ebitda": 18.0, "pe": 30.0, "ev_revenue": 2.0, "pb": 3.5, "fcf_yield": 0.030, "growth_avg": 0.25},
    # Travel & Dining HK: Haidilao 25x, Galaxy 15x, Trip.com 20x, H World 18x → 18x median.
    "Travel & Dining": {"ev_ebitda": 10.0, "pe": 18.0, "ev_revenue": 2.0, "pb": 3.0, "fcf_yield": 0.045, "growth_avg": 0.12},
    # Semiconductor HK: SMIC/Hua Hong trade at deep discount to US (NVDA/AMD).
    # Legacy node foundries with lower utilization rates and geopolitical discount.
    "Semiconductor":{"ev_ebitda": 11.5, "pe": 14.2, "ev_revenue": 2.5, "pb": 1.5, "fcf_yield": 0.050, "growth_avg": 0.08},
}

# ── HK / HKEX sector WACC ─────────────────────────────────────────────────────
# US Damodaran WACC + China Country Risk Premium (CRP).
#
# Damodaran China ERP (Jan 2026): ~5.8% vs US 4.46% → China CRP ≈ +1.35%
# Rounded to +1.5% (150 bps) to account for additional HK-listed stock liquidity
# premium and regulatory/geopolitical risk embedded in Chinese equity.
#
# Real Estate gets an extra +50 bps for China property sector risk post-2021
# (Evergrande contagion, developer liquidity crises, policy headwinds).
_HK_CHINA_CRP = 0.015   # China Country Risk Premium added to US base rates

HK_SECTOR_WACC: dict[str, float] = {
    "Tech":                SECTOR_WACC["Tech"]        + _HK_CHINA_CRP,   # 9.5% + 1.5% = 11.0%
    "Consumer":            SECTOR_WACC["Consumer"]    + _HK_CHINA_CRP,   # 7.5% + 1.5% =  9.0%
    "Biopharma":           SECTOR_WACC["Biopharma"]   + _HK_CHINA_CRP,   # 8.5% + 1.5% = 10.0%
    "Telco":               SECTOR_WACC["Telco"]       + _HK_CHINA_CRP,   # 5.5% + 1.5% =  7.0%
    "Crypto":              SECTOR_WACC["Crypto"],                         # 15.0% — unchanged
    "Energy":              SECTOR_WACC["Energy"]      + _HK_CHINA_CRP,   # 6.5% + 1.5% =  8.0%
    "Financials":          SECTOR_WACC["Financials"]  + _HK_CHINA_CRP,   # 6.0% + 1.5% =  7.5%
    "Industrials":         SECTOR_WACC["Industrials"] + _HK_CHINA_CRP,   # 8.0% + 1.5% =  9.5%
    "RealEstate":          SECTOR_WACC["RealEstate"]  + _HK_CHINA_CRP + 0.005,  # 5.5% + 2.0% = 7.5%
    "Transportation":      SECTOR_WACC["Transportation"] + _HK_CHINA_CRP, # 7.2% + 1.5% = 8.7%
    "Materials":           SECTOR_WACC["Materials"]   + _HK_CHINA_CRP,   # 7.5% + 1.5% =  9.0%
    "Resources":           SECTOR_WACC["Resources"]   + _HK_CHINA_CRP,   # 7.0% + 1.5% =  8.5%
    "Semiconductor":       SECTOR_WACC["Semiconductor"] + _HK_CHINA_CRP, # 8.8% + 1.5% = 10.3%
}


def get_sector_peer_multiples(
    sector: str,
    is_hk: bool = False,
    profile_name: str = "",
) -> dict[str, float]:
    """
    Return sector peer multiples for relative valuation.

    Parameters
    ----------
    sector      : sector string (e.g. "Tech", "Financials")
    is_hk       : True for HKEX-listed stocks → uses HK_SECTOR_PEER_MULTIPLES
    profile_name: optional sub-profile override (e.g. "Money Center Bank")

    Returns
    -------
    dict with keys: ev_ebitda, pe, ev_revenue, pb, fcf_yield
    """
    if is_hk:
        return (
            HK_SECTOR_PEER_MULTIPLES.get(profile_name)
            or HK_SECTOR_PEER_MULTIPLES.get(sector)
            or {}
        )
    return (
        SECTOR_PEER_MULTIPLES.get(profile_name)
        or SECTOR_PEER_MULTIPLES.get(sector)
        or {}
    )


def get_wacc_for_exchange(
    sector: str,
    leverage: float = 0.0,
    macro_regime: str = "neutral",
    profile: str = "",
    is_hk: bool = False,
) -> float:
    """
    Return WACC, routing to HK rates when is_hk=True.

    For HK-listed stocks the base rate already embeds China CRP (+150 bps).
    All other parameters (leverage premium, macro overlay) are applied identically.

    Backward-compatible: is_hk=False delegates entirely to get_wacc().
    """
    if not is_hk:
        return get_wacc(sector, leverage, macro_regime=macro_regime, profile=profile)

    overlay = _MACRO_WACC_OVERLAY.get(macro_regime, 0.0)
    # Energy and Financials sub-profiles: add CRP on top of the US sub-profile base
    if sector == "Energy" and profile in _ENERGY_PROFILE_WACC:
        base    = _ENERGY_PROFILE_WACC[profile] + _HK_CHINA_CRP
        lev_cap = _ENERGY_LEVERAGE_CAP.get(profile, 0.035)
    elif sector == "Financials" and profile in _FINANCIALS_PROFILE_WACC:
        base    = _FINANCIALS_PROFILE_WACC[profile] + _HK_CHINA_CRP
        lev_cap = _FINANCIALS_LEVERAGE_CAP.get(profile, 0.010)
    else:
        base    = HK_SECTOR_WACC.get(sector, SECTOR_WACC.get(sector, 0.090) + _HK_CHINA_CRP)
        lev_cap = 0.040
    leverage_premium = max(0.0, (leverage - 1.5) * 0.01)
    return round(min(base + leverage_premium + overlay, base + lev_cap), 4)


# ── 1c. Hybrid WACC with live credit-spread overlay ───────────────────────────
# Applies a cyclical credit-spread overlay on top of the Damodaran sector WACC.
#
# Math (equivalent to a full Re/Rd decomposition with Re held constant):
#
#     WACC_hybrid = WACC_base + (rd_live - rd_baseline) × (D/V) × (1 - tax)
#
# Where:
#   WACC_base   — existing sector WACC from get_wacc_for_exchange() (unchanged
#                  — preserves all the Damodaran sector calibration, macro
#                  overlay, profile sub-type logic, HK CRP, leverage premium).
#   rd_live     — live cost of debt from FRED (see get_cost_of_debt()).
#   rd_baseline — long-run baseline cost of debt for the SAME rating, taken
#                  from Damodaran's Jan 2026 static spread table. The delta
#                  therefore captures *only* cyclical credit-cycle deviation
#                  from Damodaran's implicit baseline — benign cycles shrink
#                  WACC slightly, stress cycles expand it.
#   D/V         — company-specific market-value debt weight.
#   tax         — marginal tax rate (25% per Damodaran Jan 2026 dataset).
#
# When FRED is unreachable the overlay is ~zero (live spread equals baseline
# by construction of the fallback table), so WACC_hybrid collapses to
# WACC_base — a safe no-op.

_DEFAULT_TAX_RATE = 0.25        # Damodaran Jan 2026 marginal tax assumption
_DEFAULT_RISK_FREE = 0.0395     # Damodaran Jan 2026 Rf (10-yr UST)


def compute_wacc_hybrid(
    sector: str,
    leverage: float = 0.0,
    macro_regime: str = "neutral",
    profile: str = "",
    is_hk: bool = False,
    # ── Live cost-of-debt inputs ─────────────────────────────────────────
    interest_coverage: float | None = None,
    net_debt: float | None = None,
    market_cap: float | None = None,
    tax_rate: float = _DEFAULT_TAX_RATE,
    risk_free_rate: float = _DEFAULT_RISK_FREE,
) -> dict:
    """Compute WACC with a live credit-spread overlay.

    Returns a dict with the final WACC plus full diagnostic breakdown so the
    calling agent can surface an audit line. Never raises — on any missing
    input or FRED failure, returns the sector-level WACC unchanged with
    ``source="no-overlay"`` or ``"fallback-damodaran"`` accordingly.

    All monetary inputs (net_debt, market_cap) must already be in the same
    currency (caller's responsibility after FX conversion).
    """
    wacc_base = get_wacc_for_exchange(
        sector, leverage, macro_regime=macro_regime, profile=profile, is_hk=is_hk
    )

    # Short-circuit when we can't compute D/V cleanly
    if market_cap is None or market_cap <= 0 or net_debt is None:
        return {
            "wacc":          wacc_base,
            "wacc_base":     wacc_base,
            "rd_live":       None,
            "rd_baseline":   None,
            "dv_ratio":      0.0,
            "credit_delta":  0.0,
            "rating":        None,
            "bucket":        resolve_credit_bucket(sector, profile),
            "source":        "no-overlay-missing-inputs",
            "series_id":     None,
            "audit":         (
                f"WACC {wacc_base:.2%} (sector base; no credit overlay — "
                f"market_cap or net_debt unavailable)"
            ),
        }

    D = max(net_debt, 0.0)                   # net cash → zero debt weight
    E = market_cap
    V = D + E
    dv_ratio = D / V if V > 0 else 0.0

    # Zero-debt / net-cash companies: no credit overlay makes sense
    if dv_ratio <= 0.0:
        return {
            "wacc":          wacc_base,
            "wacc_base":     wacc_base,
            "rd_live":       None,
            "rd_baseline":   None,
            "dv_ratio":      0.0,
            "credit_delta":  0.0,
            "rating":        "AAA",
            "bucket":        resolve_credit_bucket(sector, profile),
            "source":        "no-overlay-net-cash",
            "series_id":     None,
            "audit":         (
                f"WACC {wacc_base:.2%} (sector base; net-cash position, "
                f"no debt weight to overlay)"
            ),
        }

    cod = get_cost_of_debt(
        interest_coverage=interest_coverage,
        sector=sector,
        profile=profile,
        risk_free_rate=risk_free_rate,
    )
    rd_live     = cod["cost_of_debt"]
    rating      = cod["rating"]
    # Baseline uses the same sector-bucket multiplier as live so that the delta
    # captures ONLY the cyclical deviation from Damodaran's long-run table. When
    # FRED is unreachable and live falls back to the same static table, delta
    # collapses exactly to zero (no spurious overlay on top of wacc_base).
    rd_baseline_bps = _FALLBACK_SPREAD_BPS.get(rating, 1.60) * cod["multiplier"]
    rd_baseline  = risk_free_rate + rd_baseline_bps / 100.0
    credit_delta = (rd_live - rd_baseline) * dv_ratio * (1.0 - tax_rate)
    wacc_hybrid  = wacc_base + credit_delta

    return {
        "wacc":          round(wacc_hybrid, 4),
        "wacc_base":     wacc_base,
        "rd_live":       round(rd_live, 4),
        "rd_baseline":   round(rd_baseline, 4),
        "dv_ratio":      round(dv_ratio, 4),
        "credit_delta":  round(credit_delta, 6),
        "rating":        rating,
        "bucket":        cod["bucket"],
        "source":        cod["source"],
        "series_id":     cod["series_id"],
        "audit":         (
            f"WACC {wacc_hybrid:.2%} = base {wacc_base:.2%} + credit overlay "
            f"{credit_delta*10000:+.0f}bps "
            f"(rd_live {rd_live:.2%} vs baseline {rd_baseline:.2%} for {rating} "
            f"× D/V {dv_ratio:.1%} × {(1-tax_rate):.2f}) "
            f"[{cod['source']}:{cod['series_id']}]"
        ),
    }


# Macro confidence modifier table — applied as C_macro in the blended IV formula.
# Formula: IV = Σ(V_i × W_i × (1 + C_macro)) / Σ(W_i × (1 + C_macro))
# C_macro is the SUM of all applicable dimension modifiers from Phase 1 regime.
MACRO_CONFIDENCE_MODIFIERS: dict[str, float] = {
    "risk-on":     +0.10,
    "risk-off":    -0.20,
    "easing":      +0.10,   # rate_direction = "easing"
    "tightening":  -0.15,   # rate_direction = "tightening"
    "neutral":      0.00,   # rate_direction = "neutral"
    "low":         +0.05,   # volatility_regime = "low"
    "high":        -0.10,   # volatility_regime = "high"
    "medium":       0.00,   # volatility_regime = "medium"
}


def compute_c_macro(macro_regime: dict) -> float:
    """
    Compute the aggregate Macro Confidence Modifier from the Phase 1 regime dict.

    Sums modifiers across three independent regime dimensions:
      - risk_appetite : "risk-on" (+0.10) | "risk-off" (-0.20)
      - rate_direction: "easing" (+0.10)  | "tightening" (-0.15) | "neutral" (0)
      - volatility_regime: "low" (+0.05)  | "high" (-0.10)       | "medium" (0)

    C_macro is clamped to [-0.35, +0.25] so the blended multiplier (1 + C_macro)
    never falls below 0.65 or above 1.25.
    """
    c = 0.0
    c += MACRO_CONFIDENCE_MODIFIERS.get(macro_regime.get("risk_appetite", ""), 0.0)
    c += MACRO_CONFIDENCE_MODIFIERS.get(macro_regime.get("rate_direction", "neutral"), 0.0)
    c += MACRO_CONFIDENCE_MODIFIERS.get(macro_regime.get("volatility_regime", "medium"), 0.0)
    return max(min(c, 0.25), -0.35)


def classify_valuation_profile(
    sector: str,
    revenue_cagr: float,
    fcf_margin: float,
    debt_to_equity: float,
    is_pre_revenue: bool = False,
    revenue_base: float | None = None,
) -> str:
    """
    Auto-classify a company into the most appropriate valuation profile given
    its sector and key financial characteristics.

    Returns the profile key string to look up in INDUSTRY_VALUATION_PROFILES.
    Falls back to the first (anchor) profile for that sector if no rule matches.
    """
    # Loose-match helpers (local import to avoid any load-time cycles).
    # These accept LLM classifier variants like "Technology", "Biotechnology",
    # "Banking", "Real Estate" — preventing silent mis-routing to the wrong
    # sector branch when the strict Title-Case sector string doesn't match.
    from src.agents.industry.sector_prompts import (
        is_biopharma_sector, is_tech_sector, is_bank_sector, is_reit_sector,
    )
    _is_tech = is_tech_sector(sector)
    _is_biopharma = is_biopharma_sector(sector)
    _is_bank = is_bank_sector(sector)
    _is_reit = is_reit_sector(sector)

    # Normalize sector key: "REIT" (from SGX universe) maps to "RealEstate"
    sector_lookup = "RealEstate" if sector == "REIT" else sector
    profiles = INDUSTRY_VALUATION_PROFILES.get(sector_lookup, {})
    if not profiles:
        return ""

    if _is_tech:
        if is_pre_revenue or (fcf_margin < -0.15 and revenue_cagr > 0.40):
            return "High-Growth Tech / AI"
        if revenue_cagr > 0.20 and fcf_margin < 0.05:
            return "Growth SaaS"
        # Hyper-Growth Platform: high revenue growth AND high FCF margin.
        # Must come before "Mature Platform" to avoid misclassifying a category
        # king as a mature/steady-state business.
        if revenue_cagr > 0.35 and fcf_margin >= 0.15:
            return "Hyper-Growth Platform"
        # Early Platform: GMV/marketplace businesses where unit economics dominate
        # (Uber, Airbnb, DoorDash, Palantir). FCF margin 5–15%, still building cash flows.
        # Uses >= 0.20 (inclusive) so companies at exactly 20% CAGR are captured correctly.
        if revenue_cagr >= 0.20 and 0.05 <= fcf_margin < 0.15:
            return "Early Platform"
        if debt_to_equity > 2.0:
            return "Levered Subscription"
        # Hyperscaler / Tech Conglomerate: mega-cap tech with massive CapEx.
        # Gate: revenue > $100B (catches MSFT $282B, AMZN $717B, GOOGL $403B,
        # META $201B). ORCL ($57B) misses but routes to Levered Sub (D/E 5.1).
        # No FCF margin gate — hyperscalers can have high (MSFT 25%) or low
        # (AMZN 1%) FCF depending on CapEx cycle. The EV/EBITDA anchor works
        # for both because EBITDA strips CapEx distortion.
        if revenue_base and revenue_base > 100e9:
            return "Hyperscaler / Tech Conglomerate"
        # Cybersecurity: financially similar to SaaS but with "Zero Trust"
        # secular tailwind. Cannot be differentiated purely by financials —
        # use TICKER_SECTOR_LOOKUP notes field or the Damodaran industry tag
        # to flag. The classify function doesn't have access to ticker, so
        # cybersecurity routing is handled by explicit profile override in
        # TICKER_SECTOR_LOOKUP (second field = profile name override).
        # This block is a fallback for tickers NOT in the lookup.
        if fcf_margin >= 0.10:
            return "Mature Platform"
        return "Mature SaaS"

    if _is_biopharma:
        if is_pre_revenue or fcf_margin < -0.15:
            return "Pre-approval Biotech"
        if revenue_base and revenue_base > 30e9:
            return "Large Cap Pharma"
        if fcf_margin >= 0.10 and revenue_cagr < 0.08 and (not revenue_base or revenue_base < 30e9):
            return "CDMO / Life Science Tools"
        if revenue_cagr > 0.05 and fcf_margin > 0.10:
            return "MedTech / Devices"
        return "Large Cap Pharma"

    if _is_bank:
        # Money Center Bank gate: revenue > $50B = diversified G-SIB bank.
        if revenue_base and revenue_base > 50e9:
            if debt_to_equity > 5.0:
                return "Bank / Lending Institution"
            return "Money Center Bank"
        # Payment Networks: monopoly toll-road networks (V, MA, FI)
        # Very high FCF margins (>25%) + low D/E (<3) + moderate revenue ($20-40B)
        # Separates from FinTech (PYPL, SQ) which have lower margins
        if fcf_margin > 0.25 and debt_to_equity < 3.0 and revenue_base and revenue_base > 15e9:
            return "Payment Networks"
        # Market Infrastructure: exchanges with ultra-high margins
        # FCF margin >20% + very low D/E + moderate revenue
        if fcf_margin > 0.20 and debt_to_equity < 1.5 and revenue_base and revenue_base < 15e9:
            return "Market Infrastructure"
        # Alt Asset Manager: BX, KKR, APO
        if debt_to_equity >= 1.0 and debt_to_equity < 5.0 and fcf_margin > 0.25:
            return "Alt Asset Manager"
        # FinTech: payment processors, digital wallets, neobanks
        if debt_to_equity < 1.0 and fcf_margin > 0.12 and revenue_cagr > 0.05:
            return "FinTech"
        # Brokerage: deposit-funded, moderate leverage
        if 0.3 <= debt_to_equity <= 2.0 and fcf_margin > 0.15:
            return "Brokerage"
        # Insurance: identified by profile override in TICKER_SECTOR_LOOKUP
        # (Insurance companies are hard to detect by financials — GAAP ≠ economics)
        if debt_to_equity > 5.0:
            return "Bank / Lending Institution"
        # Mid-leverage: insurance, holding companies, regional banks
        return "Bank / Lending Institution"

    if sector == "Energy":
        # Regulated Utility: high FCF margin OR high-capex regulated utilities
        # (NEE, D, SO, DUK) have depressed FCF margin due to growth capex
        # but are fundamentally regulated. Detect via D/E > 1.0 + low FCF
        # (heavy capex = negative/low FCF but regulated earnings base).
        if fcf_margin >= 0.10 and debt_to_equity < 2.0:
            return "Regulated Utility"
        # High-capex regulated utilities: D/E 1.0-3.0, FCF < 10%
        # (capex-heavy infrastructure build suppresses FCF margin)
        if debt_to_equity >= 1.0 and debt_to_equity < 3.0 and fcf_margin < 0.10:
            return "Regulated Utility"
        if fcf_margin >= 0.05 and debt_to_equity < 1.5:
            return "IPP"
        return "Merchant Power"

    if sector == "Consumer":
        # ── Profile-override sub-profiles ─────────────────────────────────────
        # Automotive & EV, Travel & Dining, and Consumer Durables are routed
        # primarily via TICKER_SECTOR_LOOKUP profile override because financial
        # metrics alone cannot distinguish them reliably.  The classify function
        # only provides a fallback if the ticker is NOT in the lookup.
        #
        # Financial-metric routing order (when no profile override):

        # Apparel / Athletic Wear: brand-driven athletic/apparel, mid-to-high growth, mid-FCF margins.
        # CAGR bound raised to <0.40 to capture fast-growing brands (ONON ~50%, SKX ~25%, CROX ~30%).
        # FCF threshold capped at <0.18 to separate from luxury (Hermès, LVMH: FCF 20–35%).
        # MUST come before Food & Beverage to prevent misclassifying NKE/LULU/VFC/ONON.
        # NKE: CAGR ~3%, FCF ~10%; LULU: CAGR ~15%, FCF ~16%; ONON: CAGR ~30%, FCF ~12%.
        if 0.0 <= revenue_cagr < 0.40 and 0.05 <= fcf_margin < 0.18:
            return "Apparel / Athletic Wear"
        # Food & Beverage: genuine FMCG staples — very low growth + high FCF margin
        # (KO, PEP, MDLZ: CAGR ~2–3%, FCF margin 20–25%)
        if revenue_cagr < 0.03 and fcf_margin >= 0.15:
            return "Food & Beverage"
        if revenue_cagr < 0.05:
            return "Household / Personal"
        # Change 7: fast-growing consumer brand (CAGR ≥ 15%) with strong FCF margin
        # gets Consumer Growth profile (3-method: DCF 50% + EV/Revenue 30% + EV/EBITDA 20%)
        # before the Luxury Goods fallthrough (which would over-weight P/E Premium).
        if revenue_cagr >= 0.15 and fcf_margin >= 0.15:
            return "Consumer Growth"
        if fcf_margin >= 0.15:
            return "Luxury Goods"
        # Membership / Subscription Retail: warehouse clubs and membership-model
        # retailers with intentionally thin margins but strong revenue scale.
        # Signature: revenue > $50B, FCF margin 1-5%, CAGR 5-15%, low leverage.
        # COST, BJ, SAMS — these MUST NOT fall through to Traditional Retail
        # because their premium multiples (45-55x P/E) are structurally justified
        # by recurring membership fee economics, not merchandise margins.
        if (revenue_base and revenue_base > 50e9
                and 0.01 <= fcf_margin < 0.05
                and 0.04 <= revenue_cagr <= 0.15
                and debt_to_equity < 1.0):
            return "Membership / Subscription Retail"
        # Automotive & EV fallback: very high capex + negative FCF typical of EV ramp
        if fcf_margin < -0.05 and debt_to_equity > 1.0:
            return "Automotive & EV"
        return "Traditional Retail"

    if sector == "Industrials":
        if debt_to_equity > 1.5:
            return "Automotive (OEM)"
        if revenue_cagr < 0.08:
            return "Capital Goods"
        return "Aerospace & Defense"

    if sector == "Telco":
        return "Stable Growth"

    if sector == "Crypto":
        return "Pre-Revenue Tech"

    if _is_reit:
        return "REIT"

    if sector == "Transportation":
        # Airlines have very high leverage (leased fleet ≈ high D/E) and volatile FCF
        if debt_to_equity > 2.0 or fcf_margin < 0.02:
            return "Airlines"
        return "Rail / Logistics"

    if sector == "Materials":
        # Cyclical metals/steel have thin margins and trade on normalised EBITDA
        if fcf_margin < 0.08:
            return "Steel / Metals"
        return "Specialty Chemicals"

    if sector == "Resources":
        # Mining if strong operating margins (ore grade); O&G otherwise
        if fcf_margin >= 0.15:
            return "Mining (Major)"
        return "Upstream Oil & Gas"

    if sector == "ProfessionalServices":
        # Payment processors grow faster and trade on volume multiples
        if revenue_cagr > 0.12:
            return "Payment Processors"
        # IT Services: human-capital businesses with moderate margins
        # ACN ($70B), IBM ($68B), CTSH ($21B), INFY ($19B), WIT ($901B TWD)
        # Differentiate from Ad/Consulting by: higher revenue base, lower margins
        if revenue_base and revenue_base > 15e9:
            return "IT Services"
        return "Ad / Consulting"

    if sector == "Semiconductor":
        # OSAT: low FCF + low margins + asset-heavy packaging
        # ASX (FCM -3%, D/E 1.0), AMKR (FCM 3%, D/E 0.8)
        # Must exclude IDMs (INTC) which also have negative FCF but high revenue
        if fcf_margin < 0.05 and debt_to_equity > 0.5 and (not revenue_base or revenue_base < 30e9):
            return "OSAT / Packaging"
        # IDM / Foundry: CapEx-heavy with suppressed FCF (<15%)
        # MU (FCM 5%), INTC (FCM -9%), TXN (FCM 15%), GFS (FCM 15%)
        # ARM (FCM 4%) also lands here — IP-light but low FCM due to R&D spend
        if fcf_margin < 0.15:
            return "IDM / Foundry"
        # Fabless: high-growth (CAGR >= 15%) with healthy FCF
        # NVDA (88%, 45%), AVGO (34%, 42%), AMD (24%, 19%), TSM (33%, 29%)
        if revenue_cagr >= 0.15:
            return "Fabless"
        # Equipment / EDA: moderate growth (<15%) with strong FCF
        # ASML (9%, 33%), AMAT (3%, 20%), LRCX (3%, 29%), KLAC (8%, 31%)
        # SNPS (15%, 19%), CDNS (14%, 30%), TER (9%, 14%)
        # Also catches mature analog: ADI (-5%, 39%), ON (-15%, 24%), NXPI (-4%, 20%)
        return "Equipment / EDA"

    # Default: return first profile key for sector
    return next(iter(profiles), "")


def get_valuation_profile(
    sector: str,
    revenue_cagr: float,
    fcf_margin: float,
    debt_to_equity: float = 0.0,
    is_pre_revenue: bool = False,
    revenue_base: float | None = None,
) -> tuple[str, dict]:
    """
    Classify and return (profile_name, profile_dict) for the given sector + company data.
    Returns ("", {}) if sector is unrecognised.
    """
    profile_key = classify_valuation_profile(
        sector, revenue_cagr, fcf_margin, debt_to_equity, is_pre_revenue,
        revenue_base=revenue_base,
    )
    # Normalize sector key: "REIT" (from SGX universe) → "RealEstate" (profiles key)
    sector_lookup = "RealEstate" if sector == "REIT" else sector
    profiles = INDUSTRY_VALUATION_PROFILES.get(sector_lookup, {})
    return profile_key, profiles.get(profile_key, {})


# ── Damodaran indname.xls → Internal Sector/Profile Mapping ──────────────────
#
# Maps (Primary Sector, Industry Group) from Damodaran's indname.xls dataset
# (48,156 companies, Jan 2026) to the pipeline's internal (sector, wacc_profile).
#
# Column alignment:
#   indname.xls "Primary Sector"  →  tuple[0]: internal sector key (SECTOR_WACC keys)
#   indname.xls "Industry Group"  →  tuple[1]: wacc_profile hint
#     - For Energy/Financials: profile is passed directly to get_wacc(profile=)
#     - For all other sectors:  profile "" — sector WACC applies; profile drives
#       classify_valuation_profile() for multiples selection, not WACC
#
# Source: Damodaran January 2026 — https://pages.stern.nyu.edu/~adamodar/
# 94 unique Industry Groups across 11 Primary Sectors covered.

DAMODARAN_SECTOR_MAP: dict[tuple[str, str], tuple[str, str]] = {

    # ── Information Technology ────────────────────────────────────────────────
    ("Information Technology", "Software (System & Application)"): ("Tech", ""),
    ("Information Technology", "Software (Internet)"):             ("Tech", ""),
    ("Information Technology", "Semiconductor"):                   ("Tech", ""),
    ("Information Technology", "Semiconductor Equip"):             ("Tech", ""),
    ("Information Technology", "Computers/Peripherals"):           ("Tech", ""),
    ("Information Technology", "Computer Services"):               ("Tech", ""),
    ("Information Technology", "Electronics (Consumer & Office)"): ("Tech", ""),
    ("Information Technology", "Electronics (General)"):           ("Tech", ""),
    ("Information Technology", "Telecom. Equipment"):              ("Tech", ""),
    ("Information Technology", "Office Equipment & Services"):     ("Tech", ""),
    ("Information Technology", "Heathcare Information and Technology"): ("Tech", ""),
    ("Information Technology", "Information Services"):            ("Tech", ""),

    # ── Communication Services ────────────────────────────────────────────────
    ("Communication Services", "Telecom. Services"):               ("Telco", ""),
    ("Communication Services", "Telecom (Wireless)"):              ("Telco", ""),
    ("Communication Services", "Cable TV"):                        ("Telco", ""),
    ("Communication Services", "Broadcasting"):                    ("Telco", ""),
    ("Communication Services", "Advertising"):                     ("ProfessionalServices", "Ad / Consulting"),
    ("Communication Services", "Publishing & Newspapers"):         ("ProfessionalServices", "Ad / Consulting"),
    ("Communication Services", "Entertainment"):                   ("Consumer", ""),
    ("Communication Services", "Software (Entertainment)"):        ("Tech", ""),
    ("Communication Services", "Information Services"):            ("Tech", ""),

    # ── Consumer Discretionary ────────────────────────────────────────────────
    ("Consumer Discretionary", "Apparel"):                         ("Consumer", ""),
    ("Consumer Discretionary", "Shoe"):                            ("Consumer", ""),
    ("Consumer Discretionary", "Auto & Truck"):                    ("Industrials", "Automotive (OEM)"),
    ("Consumer Discretionary", "Auto Parts"):                      ("Industrials", "Capital Goods"),
    ("Consumer Discretionary", "Furn/Home Furnishings"):           ("Consumer", ""),
    ("Consumer Discretionary", "Hotel/Gaming"):                    ("Consumer", ""),
    ("Consumer Discretionary", "Homebuilding"):                    ("Consumer", ""),
    ("Consumer Discretionary", "Recreation"):                      ("Consumer", ""),
    ("Consumer Discretionary", "Restaurant/Dining"):               ("Consumer", ""),
    ("Consumer Discretionary", "Retail (Automotive)"):             ("Consumer", ""),
    ("Consumer Discretionary", "Retail (Building Supply)"):        ("Consumer", ""),
    ("Consumer Discretionary", "Retail (Distributors)"):           ("Consumer", ""),
    ("Consumer Discretionary", "Retail (General)"):                ("Consumer", ""),
    ("Consumer Discretionary", "Retail (Grocery and Food)"):       ("Consumer", ""),
    ("Consumer Discretionary", "Retail (Special Lines)"):          ("Consumer", ""),
    ("Consumer Discretionary", "Rubber & Tires"):                  ("Industrials", "Capital Goods"),
    ("Consumer Discretionary", "Education"):                       ("ProfessionalServices", "Ad / Consulting"),

    # ── Consumer Staples ──────────────────────────────────────────────────────
    ("Consumer Staples", "Beverage (Alcoholic)"):                  ("Consumer", ""),
    ("Consumer Staples", "Beverage (Soft)"):                       ("Consumer", ""),
    ("Consumer Staples", "Food Processing"):                       ("Consumer", ""),
    ("Consumer Staples", "Food Wholesalers"):                      ("Consumer", ""),
    ("Consumer Staples", "Household Products"):                    ("Consumer", ""),
    ("Consumer Staples", "Tobacco"):                               ("Consumer", ""),
    ("Consumer Staples", "Farming/Agriculture"):                   ("Consumer", ""),

    # ── Financials ────────────────────────────────────────────────────────────
    # Note: R.E.I.T. and Real Estate groups appear under Financials in Damodaran's
    # classification; they route to RealEstate internally.
    ("Financials", "Bank (Money Center)"):                         ("Financials", "Money Center Bank"),
    ("Financials", "Banks (Regional)"):                            ("Financials", "Regional Bank"),
    ("Financials", "Brokerage & Investment Banking"):              ("Financials", "Investment Bank"),
    ("Financials", "Financial Svcs. (Non-bank & Insurance)"):      ("Financials", "FinTech"),
    ("Financials", "Insurance (General)"):                         ("Financials", "Insurance"),
    ("Financials", "Insurance (Life)"):                            ("Financials", "Insurance"),
    ("Financials", "Insurance (Prop/Cas.)"):                       ("Financials", "Insurance"),
    ("Financials", "Investments & Asset Management"):              ("Financials", "Asset Manager"),
    ("Financials", "Reinsurance"):                                 ("Financials", "Insurance"),
    ("Financials", "R.E.I.T."):                                    ("RealEstate", ""),
    ("Financials", "Real Estate (Development)"):                   ("RealEstate", ""),
    ("Financials", "Real Estate (General/Diversified)"):           ("RealEstate", ""),
    ("Financials", "Real Estate (Operations & Services)"):         ("RealEstate", ""),
    ("Financials", "Retail (REITs)"):                              ("RealEstate", ""),
    ("Financials", "Diversified"):                                 ("Financials", "Holding Company"),

    # ── Health Care ───────────────────────────────────────────────────────────
    ("Health Care", "Drugs (Biotechnology)"):                      ("Biopharma", ""),
    ("Health Care", "Drugs (Pharmaceutical)"):                     ("Biopharma", ""),
    ("Health Care", "Healthcare Products"):                        ("Biopharma", ""),
    ("Health Care", "Healthcare Support Services"):                ("Biopharma", ""),
    ("Health Care", "Heathcare Information and Technology"):       ("Tech", ""),
    ("Health Care", "Hospitals/Healthcare Facilities"):            ("Biopharma", ""),

    # ── Industrials ───────────────────────────────────────────────────────────
    ("Industrials", "Aerospace/Defense"):                          ("Industrials", ""),
    ("Industrials", "Business & Consumer Services"):               ("ProfessionalServices", ""),
    ("Industrials", "Building Materials"):                         ("Materials", ""),
    ("Industrials", "Construction Supplies"):                      ("Materials", ""),
    ("Industrials", "Electrical Equipment"):                       ("Industrials", ""),
    ("Industrials", "Engineering/Construction"):                   ("Industrials", ""),
    ("Industrials", "Environmental & Waste Services"):             ("ProfessionalServices", ""),
    ("Industrials", "Machinery"):                                  ("Industrials", ""),
    ("Industrials", "Office Equipment & Services"):                ("Industrials", ""),
    ("Industrials", "Packaging & Container"):                      ("Materials", ""),
    ("Industrials", "Paper/Forest Products"):                      ("Materials", ""),
    ("Industrials", "Shipbuilding & Marine"):                      ("Industrials", ""),
    ("Industrials", "Transportation"):                             ("Transportation", ""),
    ("Industrials", "Transportation (Railroads)"):                 ("Transportation", ""),
    ("Industrials", "Trucking"):                                   ("Transportation", ""),
    ("Industrials", "Air Transport"):                              ("Transportation", ""),

    # ── Energy ────────────────────────────────────────────────────────────────
    # Damodaran's "Energy" primary sector splits: pure E&P/integrated → Resources;
    # power/distribution/renewable → Energy with profile routing.
    ("Energy", "Green & Renewable Energy"):                        ("Energy", "IPP"),
    ("Energy", "Oil/Gas (Integrated)"):                            ("Resources", ""),
    ("Energy", "Oil/Gas (Production and Exploration)"):            ("Resources", ""),
    ("Energy", "Oil/Gas Distribution"):                            ("Energy", "Merchant Power"),
    ("Energy", "Oilfield Svcs/Equip."):                            ("Industrials", ""),
    ("Energy", "Power"):                                           ("Energy", "Merchant Power"),
    ("Energy", "Coal & Related Energy"):                           ("Resources", ""),

    # ── Utilities ─────────────────────────────────────────────────────────────
    ("Utilities", "Utility (General)"):                            ("Energy", "Regulated Utility"),
    ("Utilities", "Utility (Water)"):                              ("Energy", "Regulated Utility"),
    ("Utilities", "Power"):                                        ("Energy", "IPP"),
    ("Utilities", "Green & Renewable Energy"):                     ("Energy", "IPP"),

    # ── Materials ─────────────────────────────────────────────────────────────
    ("Materials", "Chemical (Basic)"):                             ("Materials", ""),
    ("Materials", "Chemical (Diversified)"):                       ("Materials", ""),
    ("Materials", "Chemical (Specialty)"):                         ("Materials", ""),
    ("Materials", "Metals & Mining"):                              ("Resources", ""),
    ("Materials", "Precious Metals"):                              ("Resources", ""),
    ("Materials", "Steel"):                                        ("Materials", ""),
    ("Materials", "Paper/Forest Products"):                        ("Materials", ""),
    ("Materials", "Rubber & Tires"):                               ("Materials", ""),
    ("Materials", "Building Materials"):                           ("Materials", ""),
    ("Materials", "Packaging & Container"):                        ("Materials", ""),
    ("Materials", "Coal & Related Energy"):                        ("Resources", ""),

    # ── Real Estate ───────────────────────────────────────────────────────────
    ("Real Estate", "R.E.I.T."):                                   ("RealEstate", ""),
    ("Real Estate", "Real Estate (Development)"):                  ("RealEstate", ""),
    ("Real Estate", "Real Estate (General/Diversified)"):          ("RealEstate", ""),
    ("Real Estate", "Real Estate (Operations & Services)"):        ("RealEstate", ""),
    ("Real Estate", "Retail (REITs)"):                             ("RealEstate", ""),
    ("Real Estate", "Diversified"):                                ("RealEstate", ""),
}


def map_damodaran(primary_sector: str, industry_group: str) -> tuple[str, str]:
    """
    Translate Damodaran indname.xls classification into the pipeline's internal
    (sector, wacc_profile) pair.

    Args:
        primary_sector: Value from indname.xls "Primary Sector" column.
                        One of 11 GICS-style sectors (e.g. "Information Technology").
        industry_group: Value from indname.xls "Industry Group" column.
                        One of 94 groups (e.g. "Software (System & Application)").

    Returns:
        (sector, wacc_profile) where:
          sector       — matches a key in SECTOR_WACC (e.g. "Tech", "Financials")
          wacc_profile — passed to get_wacc(profile=); "" for non-Energy/Financials sectors

    Falls back to a best-effort primary-sector-only mapping if the exact
    (primary_sector, industry_group) pair is not in DAMODARAN_SECTOR_MAP.

    Usage:
        sector, profile = map_damodaran("Utilities", "Utility (General)")
        wacc = get_wacc(sector, leverage=0.8, macro_regime="neutral", profile=profile)
        # → get_wacc("Energy", 0.8, "neutral", "Regulated Utility") → 4.58%
    """
    key = (primary_sector, industry_group)
    if key in DAMODARAN_SECTOR_MAP:
        return DAMODARAN_SECTOR_MAP[key]

    # ── Fallback: primary-sector-only heuristic ───────────────────────────────
    _PRIMARY_FALLBACK: dict[str, tuple[str, str]] = {
        "Information Technology": ("Tech",                ""),
        "Communication Services": ("Telco",               ""),
        "Consumer Discretionary": ("Consumer",            ""),
        "Consumer Staples":       ("Consumer",            ""),
        "Financials":             ("Financials",          ""),
        "Health Care":            ("Biopharma",           ""),
        "Industrials":            ("Industrials",         ""),
        "Energy":                 ("Energy",              ""),
        "Utilities":              ("Energy",   "Regulated Utility"),
        "Materials":              ("Materials",           ""),
        "Real Estate":            ("RealEstate",          ""),
    }
    return _PRIMARY_FALLBACK.get(primary_sector, ("Tech", ""))


# ── Guardrail 1: Ticker-Level Hard Lookup ─────────────────────────────────────
#
# Static ground-truth classification for ~90 commonly analysed tickers.
# Used by validate_sector() to cross-check (and optionally override) the LLM's
# Phase 2 classification before it propagates into WACC, TGR, and valuation methods.
#
# Format: TICKER → (internal_sector, wacc_profile, damodaran_industry_group, notes)
#   internal_sector      — must be a key in SECTOR_WACC
#   wacc_profile         — passed to get_wacc(profile=); "" for non-Energy/Financials
#   damodaran_ig         — Damodaran indname.xls "Industry Group" for audit trail
#   notes                — brief rationale for any non-obvious routing decision
#
# Maintenance: add new tickers here when misclassification is observed in production.
# DO NOT remove entries — comment them out if a company changes its business model.

_TL = tuple[str, str, str, str]   # type alias for readability

TICKER_SECTOR_LOOKUP: dict[str, _TL] = {

    # ── Information Technology (Software / Platform / Hardware) ──────────────
    # Profile overrides (2nd field) route to sector-specific KPI prompts in
    # sector_prompts.py and sub-type valuation panels on the frontend:
    #   "Hyperscaler / Tech Conglomerate" → cloud + AI capex lens
    #   "Mature SaaS"                     → NRR + Rule of 40 + Post-SBC FCF lens
    #   "Growth SaaS"                     → unit economics + collapse-risk lens
    #   "Cybersecurity / Mission-Critical SaaS" → growth_saas variant with
    #     platform-attach + renewals emphasis (already in place below)
    "MSFT":  ("Tech", "Hyperscaler / Tech Conglomerate", "Software (System & Application)", "Azure + M365 + AI capex; hyperscaler profile"),
    "AAPL":  ("Tech", "",              "Computers/Peripherals",           "Hardware + services mix; Tech WACC applies"),

    # ── Semiconductor (separate sector from Tech) ─────────────────────────
    # Fabless
    "NVDA":  ("Semiconductor", "Fabless",     "Semiconductor",                   "Fabless — AI GPU"),
    "AVGO":  ("Semiconductor", "Fabless",     "Semiconductor",                   "Fabless — custom ASIC + networking"),
    "QCOM":  ("Semiconductor", "Fabless",     "Semiconductor",                   "Fabless — mobile/edge AI"),
    "AMD":   ("Semiconductor", "Fabless",     "Semiconductor",                   "Fabless — CPU/GPU"),
    "MRVL":  ("Semiconductor", "Fabless",     "Semiconductor",                   "Fabless — networking/storage"),
    "ARM":   ("Semiconductor", "",     "Semiconductor",                   "Fabless — IP licensing/royalties"),
    # IDM / Foundry
    "MU":    ("Semiconductor", "",     "Semiconductor",                   "IDM — DRAM/NAND/HBM fabs"),
    "INTC":  ("Semiconductor", "IDM / Foundry",     "Semiconductor",                   "IDM + Foundry — x86/fabs"),
    "TSM":   ("Semiconductor", "IDM / Foundry",     "Semiconductor",                   "Foundry — TSMC ADR (reports TWD)"),
    "TXN":   ("Semiconductor", "",     "Semiconductor",                   "IDM — analog fabs"),
    "GFS":   ("Semiconductor", "",     "Semiconductor",                   "Foundry — specialty nodes"),
    "UMC":   ("Semiconductor", "",     "Semiconductor",                   "Foundry — UMC ADR (reports TWD)"),
    "ADI":   ("Semiconductor", "",     "Semiconductor",                   "IDM — analog/mixed-signal"),
    "MCHP":  ("Semiconductor", "",     "Semiconductor",                   "IDM — microcontrollers"),
    "ON":    ("Semiconductor", "",     "Semiconductor",                   "IDM — power semiconductors"),
    "NXPI":  ("Semiconductor", "",     "Semiconductor",                   "IDM — automotive semi"),
    # Equipment / EDA
    "ASML":  ("Semiconductor", "Equipment / EDA",     "Semiconductor Equip",             "Equipment — EUV lithography monopoly"),
    "AMAT":  ("Semiconductor", "Equipment / EDA",     "Semiconductor Equip",             "Equipment — deposition/etch"),
    "LRCX":  ("Semiconductor", "",     "Semiconductor Equip",             "Equipment — etch/deposition"),
    "KLAC":  ("Semiconductor", "",     "Semiconductor Equip",             "Equipment — process control"),
    "TER":   ("Semiconductor", "",     "Semiconductor Equip",             "Equipment — automated test"),
    "SNPS":  ("Semiconductor", "",     "Semiconductor Equip",             "EDA — design tools"),
    "CDNS":  ("Semiconductor", "",     "Semiconductor Equip",             "EDA — design tools"),
    # OSAT
    "ASX":   ("Semiconductor", "",     "Semiconductor",                   "OSAT — ASE ADR (reports TWD)"),
    "AMKR":  ("Semiconductor", "",     "Semiconductor",                   "OSAT — packaging"),
    "CRM":   ("Tech", "Mature SaaS",   "Software (System & Application)", "Salesforce — durable enterprise SaaS; NRR + Rule-of-40 lens"),
    "NOW":   ("Tech", "Mature SaaS",   "Software (System & Application)", "ServiceNow — workflow platform; durable enterprise SaaS"),
    "SNOW":  ("Tech", "Growth SaaS",   "Software (System & Application)", "Snowflake — consumption model; growth SaaS profile"),
    "PLTR":  ("Tech", "Growth SaaS",   "Software (System & Application)", "Palantir — AIP inflection; growth SaaS profile"),
    "ORCL":  ("Tech", "Hyperscaler / Tech Conglomerate", "Software (System & Application)", "Oracle — OCI + Fusion ERP/CRM migration; hyperscaler profile"),
    "SAP":   ("Tech", "Mature SaaS",   "Software (System & Application)", "SAP SE ADR — durable enterprise ERP cloud migration"),
    "DELL":  ("Tech", "",              "Computers/Peripherals",           ""),
    "HPQ":   ("Tech", "",              "Computers/Peripherals",           ""),
    # Mature SaaS (durable profitable enterprise — NRR + R40 + Post-SBC FCF lens)
    "ADBE":  ("Tech", "Mature SaaS",   "Software (System & Application)", "Adobe — Creative Cloud + Experience Cloud"),
    "WDAY":  ("Tech", "Mature SaaS",   "Software (System & Application)", "Workday — HCM + Financials"),
    "INTU":  ("Tech", "Mature SaaS",   "Software (System & Application)", "Intuit — TurboTax + QuickBooks"),
    "VEEV":  ("Tech", "Mature SaaS",   "Software (System & Application)", "Veeva — life sciences vertical SaaS"),
    # Growth SaaS (scaling with positive NRR, unit economics + collapse-risk lens)
    "HUBS":  ("Tech", "Growth SaaS",   "Software (System & Application)", "HubSpot — mid-market CRM/marketing"),
    "FRSH":  ("Tech", "Growth SaaS",   "Software (System & Application)", "Freshworks — ITSM + customer engagement SMB"),
    "DDOG":  ("Tech", "Growth SaaS",   "Software (System & Application)", "Datadog — observability"),
    "MDB":   ("Tech", "Growth SaaS",   "Software (System & Application)", "MongoDB — database as a service"),
    "TEAM":  ("Tech", "Growth SaaS",   "Software (System & Application)", "Atlassian — Jira/Confluence"),
    "ZM":    ("Tech", "Growth SaaS",   "Software (System & Application)", "Zoom — video communications"),
    "OKTA":  ("Tech", "Growth SaaS",   "Software (System & Application)", "Okta — identity / access"),
    "TWLO":  ("Tech", "Growth SaaS",   "Software (System & Application)", "Twilio — CPaaS"),
    "MNDY":  ("Tech", "Growth SaaS",   "Software (System & Application)", "Monday.com — work OS"),
    "BILL":  ("Tech", "Growth SaaS",   "Software (System & Application)", "BILL Holdings — SMB finance SaaS"),
    "GTLB":  ("Tech", "Growth SaaS",   "Software (System & Application)", "GitLab — DevSecOps platform"),
    "S":     ("Tech", "Growth SaaS",   "Software (System & Application)", "SentinelOne — cybersecurity SaaS"),
    # Cybersecurity — profile override forces "Cybersecurity / Mission-Critical SaaS"
    "CRWD":  ("Tech", "Cybersecurity / Mission-Critical SaaS", "Software (System & Application)", "CrowdStrike — Cybersecurity"),
    "PANW":  ("Tech", "Cybersecurity / Mission-Critical SaaS", "Software (System & Application)", "Palo Alto Networks — Cybersecurity"),
    "ZS":    ("Tech", "Cybersecurity / Mission-Critical SaaS", "Software (System & Application)", "Zscaler — Cybersecurity"),
    "FTNT":  ("Tech", "Cybersecurity / Mission-Critical SaaS", "Software (System & Application)", "Fortinet — Cybersecurity"),
    "NET":   ("Tech", "Cybersecurity / Mission-Critical SaaS", "Software (System & Application)", "Cloudflare — Cybersecurity/CDN"),
    # Digital Platforms
    "PINS":  ("Tech", "",              "Software (Entertainment)",        "Pinterest — digital platform"),
    "SNAP":  ("Tech", "",              "Software (Entertainment)",        "Snap Inc — digital platform"),
    "MTCH":  ("Tech", "",              "Software (Entertainment)",        "Match Group — digital platform"),
    # E-commerce / Marketplace
    "EBAY":  ("Tech", "",              "Software (Internet)",             "eBay — e-commerce marketplace"),
    "DASH":  ("Tech", "",              "Software (Internet)",             "DoorDash — delivery marketplace"),
    "ETSY":  ("Tech", "",              "Software (Internet)",             "Etsy — e-commerce marketplace"),
    # (Semi tickers moved to Semiconductor section above)

    # ── IT Services → ProfessionalServices (human-capital, marginal cost > 0) ─
    "IBM":   ("ProfessionalServices", "", "Computer Services",            "IBM — IT services/consulting; moved from Tech"),
    "ACN":   ("ProfessionalServices", "", "Business & Consumer Services", "Accenture — IT consulting/outsourcing"),
    "CTSH":  ("ProfessionalServices", "", "Business & Consumer Services", "Cognizant — IT services"),
    "INFY":  ("ProfessionalServices", "", "Business & Consumer Services", "Infosys ADR — IT services"),
    "WIT":   ("ProfessionalServices", "", "Business & Consumer Services", "Wipro ADR — IT services"),

    # ── Communication Services → Tech (digital advertising / search platforms) ─
    "GOOGL": ("Tech", "Hyperscaler / Tech Conglomerate", "Information Services", "Alphabet — Search + YouTube + GCP + AI capex"),
    "GOOG":  ("Tech", "Hyperscaler / Tech Conglomerate", "Information Services", "Alphabet class C — same business as GOOGL"),
    "GOOG":  ("Tech", "",              "Information Services",            "Alphabet Class C"),
    "META":  ("Tech", "Hyperscaler / Tech Conglomerate", "Software (Entertainment)", "Meta — Ads + AI capex + Reality Labs; hyperscaler-like capex lens"),

    # ── Communication Services → Telco ────────────────────────────────────────
    "T":     ("Telco", "",             "Telecom. Services",               "AT&T — high leverage; Telco WACC 5.5%"),
    "VZ":    ("Telco", "",             "Telecom (Wireless)",              "Verizon"),
    "CMCSA": ("Telco", "",             "Cable TV",                        "Comcast — cable/broadband"),
    "CHTR":  ("Telco", "",             "Cable TV",                        "Charter Communications"),
    "TMUS":  ("Telco", "",             "Telecom (Wireless)",              "T-Mobile US"),
    # DIS moved to Consumer Discretionary section with "Travel & Dining" profile override
    "NFLX":  ("Tech", "",              "Software (Entertainment)",        "Netflix: streaming tech platform — Tech"),
    "SPOT":  ("Tech", "",              "Software (Entertainment)",        "Spotify ADR"),
    "TTWO":  ("Tech", "",              "Software (Entertainment)",        "Take-Two Interactive"),
    "EA":    ("Tech", "",              "Software (Entertainment)",        "Electronic Arts"),
    "WPP":   ("ProfessionalServices", "Ad / Consulting", "Advertising",  "WPP plc ADR"),
    "IPG":   ("ProfessionalServices", "Ad / Consulting", "Advertising",  "Interpublic"),
    "OMC":   ("ProfessionalServices", "Ad / Consulting", "Advertising",  "Omnicom"),

    # ── Consumer Discretionary ────────────────────────────────────────────────
    "AMZN":  ("Tech", "Hyperscaler / Tech Conglomerate", "Software (Internet)", "Amazon — AWS + retail + ads + AI capex; AWS > 60% EBIT"),
    "BABA":  ("Tech", "",              "Software (Internet)",             "Alibaba ADR — cloud/e-commerce; misclassified as Consumer frequently"),
    "JD":    ("Consumer", "",          "Retail (General)",                "JD.com — pure-play retailer; Consumer"),
    # ── Travel & Dining (profile override) ────────────────────────────────
    "MCD":   ("Consumer", "Travel & Dining", "Restaurant/Dining",        "McDonald's — franchise royalty model"),
    "SBUX":  ("Consumer", "Travel & Dining", "Restaurant/Dining",        "Starbucks — global coffeehouse"),
    "DIS":   ("Consumer", "Travel & Dining", "Entertainment",            "Disney: content/parks/cruise — Travel & Dining"),
    "ABNB":  ("Consumer", "Travel & Dining", "Hotel/Gaming",             "Airbnb — asset-light travel platform"),
    "BKNG":  ("Consumer", "Travel & Dining", "Hotel/Gaming",             "Booking Holdings — OTA platform"),
    # ── Apparel & Footwear ────────────────────────────────────────────────
    "NKE":   ("Consumer", "",          "Apparel",                         "Nike"),
    "LULU":  ("Consumer", "",          "Apparel",                         "Lululemon"),
    "ONON":  ("Consumer", "",          "Apparel",                         "On Holding AG ADR"),
    "DECK":  ("Consumer", "",          "Apparel",                         "Deckers Outdoor — UGG/HOKA"),
    "VFC":   ("Consumer", "",          "Apparel",                         "VF Corp — North Face/Vans/Timberland"),
    "GPS":   ("Consumer", "",          "Apparel",                         "Gap Inc"),
    # ── Consumer Durables (profile override) ──────────────────────────────
    "WHR":   ("Consumer", "Consumer Durables", "Furn/Home Furnishings",  "Whirlpool — major appliances"),
    "GRMN":  ("Consumer", "Consumer Durables", "Electronics (Consumer & Office)", "Garmin — GPS/fitness wearables"),
    "MHK":   ("Consumer", "Consumer Durables", "Furn/Home Furnishings",  "Mohawk Industries — flooring"),
    "LEG":   ("Consumer", "Consumer Durables", "Furn/Home Furnishings",  "Leggett & Platt — furniture components"),
    "TPX":   ("Consumer", "Consumer Durables", "Furn/Home Furnishings",  "Tempur Sealy — mattresses"),
    "SONO":  ("Consumer", "Consumer Durables", "Electronics (Consumer & Office)", "Sonos — premium consumer audio"),
    # ── Automotive & EV (profile override) ────────────────────────────────
    # TSLA/RIVN/LCID are consumer EV brands; F/GM stay Industrials Automotive (OEM)
    "TSLA":  ("Consumer", "Automotive & EV", "Auto & Truck",             "Tesla — consumer EV; growth premium via EV/Revenue anchor"),
    "RIVN":  ("Consumer", "Automotive & EV", "Auto & Truck",             "Rivian — pre-profit EV; EV/Revenue primary"),
    "LCID":  ("Consumer", "Automotive & EV", "Auto & Truck",             "Lucid — pre-profit EV; EV/Revenue primary"),
    # F/GM: traditional OEMs stay in Industrials — capex profile, union labor, legacy ICE
    "TM":    ("Industrials", "Automotive (OEM)", "Auto & Truck",         "Toyota — Industrial/Auto, NOT Consumer"),
    "GM":    ("Industrials", "Automotive (OEM)", "Auto & Truck",         "General Motors — traditional OEM"),
    "F":     ("Industrials", "Automotive (OEM)", "Auto & Truck",         "Ford — traditional OEM"),
    # ── Retail (General) ──────────────────────────────────────────────────
    "WMT":   ("Consumer", "",          "Retail (General)",                "Walmart"),
    "TGT":   ("Consumer", "",          "Retail (General)",                "Target"),
    "HD":    ("Consumer", "",          "Retail (Building Supply)",        "Home Depot"),
    "TJX":   ("Consumer", "",          "Retail (Special Lines)",          "TJX Companies — off-price retail"),
    "GME":   ("Consumer", "",          "Retail (Special Lines)",          "GameStop — declining retail; Bitcoin treasury pivot"),
    # ── Tech platforms (NOT Consumer) ─────────────────────────────────────
    "UBER":  ("Tech", "",              "Software (Internet)",             "Uber: platform marketplace — Tech WACC (marketplace, not logistics)"),
    "GRAB":  ("Tech", "",              "Software (Internet)",             "Grab Holdings — SEA super-app platform"),
    "PDD":   ("Tech", "",              "Software (Internet)",             "PDD Holdings — Pinduoduo/Temu marketplace; 20-F filer (RMB reporting)"),
    # CRWD moved to Cybersecurity section above with profile override
    "KO":    ("Consumer", "",          "Beverage (Soft)",                 ""),
    "PEP":   ("Consumer", "",          "Beverage (Soft)",                 ""),
    "PG":    ("Consumer", "",          "Household Products",              ""),
    "UL":    ("Consumer", "",          "Household Products",              "Unilever ADR"),

    # ── Financials ────────────────────────────────────────────────────────────
    "JPM":   ("Financials", "Money Center Bank",  "Bank (Money Center)",              ""),
    "BAC":   ("Financials", "Money Center Bank",  "Bank (Money Center)",              ""),
    "C":     ("Financials", "Money Center Bank",  "Bank (Money Center)",              "Citigroup"),
    "WFC":   ("Financials", "Money Center Bank",  "Bank (Money Center)",              ""),
    "GS":    ("Financials", "Investment Bank",    "Brokerage & Investment Banking",   ""),
    "MS":    ("Financials", "Investment Bank",    "Brokerage & Investment Banking",   "Morgan Stanley"),
    "BLK":   ("Financials", "Asset Manager",      "Investments & Asset Management",   "BlackRock"),
    "AB":    ("Financials", "Asset Manager",      "Investments & Asset Management",   "Alliance Bernstein — publicly traded asset manager"),
    "CRCL":  ("Financials", "Fintech/Stablecoin", "Financial Svcs. (Non-bank & Insurance)", "Circle Internet Corp — USDC stablecoin issuer; reserve income model"),
    "BX":    ("Financials", "Alt Asset Manager",  "Investments & Asset Management",   "Blackstone"),
    "APO":   ("Financials", "Alt Asset Manager",  "Investments & Asset Management",   "Apollo Global"),
    "KKR":   ("Financials", "Alt Asset Manager",  "Investments & Asset Management",   ""),
    "CB":    ("Financials", "Insurance",          "Insurance (Prop/Cas.)",            "Chubb"),
    "AIG":   ("Financials", "Insurance",          "Insurance (General)",              ""),
    "MET":   ("Financials", "Insurance",          "Insurance (Life)",                 "MetLife"),
    "BRK.B": ("Financials", "Holding Company",    "Diversified",                      "Berkshire Hathaway"),
    "BRK.A": ("Financials", "Holding Company",    "Diversified",                      "Berkshire Hathaway Class A"),
    "FNMA":  ("Financials", "Mortgage/GSE",       "Financial Svcs. (Non-bank & Insurance)", "Fannie Mae — GSE conservatorship binary risk"),
    "FMCC":  ("Financials", "Mortgage/GSE",       "Financial Svcs. (Non-bank & Insurance)", "Freddie Mac — GSE conservatorship binary risk"),
    # Payments & Networks — profile override (can't distinguish from FinTech by financials alone)
    "V":     ("Financials", "Payment Networks",    "Financial - Credit Services",  "Visa — monopoly payment network"),
    "MA":    ("Financials", "Payment Networks",    "Financial - Credit Services",  "Mastercard — monopoly payment network"),
    "FI":    ("Financials", "Payment Networks",    "Information Technology",        "Fiserv — payment infrastructure"),
    # Asset Management
    "TROW":  ("Financials", "Asset Manager",       "Asset Management",             "T. Rowe Price"),
    # Insurance
    "PRU":   ("Financials", "Insurance",           "Insurance - Life",             "Prudential Financial"),
    "PGR":   ("Financials", "Insurance",           "Insurance - P&C",              "Progressive — auto insurance"),
    # Brokerage
    "SCHW":  ("Financials", "Brokerage",           "Financial - Capital Markets",  "Charles Schwab — deposit-funded brokerage"),
    "JEF":   ("Financials", "Investment Bank",     "Financial - Capital Markets",  "Jefferies — mid-cap IB"),
    # Market Infrastructure — profile override (looks like Tech by financials)
    "CME":   ("Financials", "Market Infrastructure", "Financial Data & Stock Exch", "CME Group — derivatives exchange"),
    "ICE":   ("Financials", "Market Infrastructure", "Financial Data & Stock Exch", "Intercontinental Exchange"),
    "NDAQ":  ("Financials", "Market Infrastructure", "Financial Data & Stock Exch", "Nasdaq Inc — exchange + data"),
    "CBOE":  ("Financials", "Market Infrastructure", "Financial Data & Stock Exch", "CBOE Global Markets"),
    "PLD":   ("RealEstate", "",                   "R.E.I.T.",                         "Prologis REIT — industrial / logistics"),
    "SPG":   ("RealEstate", "",                   "Retail (REITs)",                   "Simon Property Group REIT — retail mall operator"),
    "O":     ("RealEstate", "",                   "R.E.I.T.",                         "Realty Income REIT — single-tenant net lease · triple net lease · net-lease blue chip"),
    "ADC":   ("RealEstate", "",                   "R.E.I.T.",                         "Agree Realty REIT — triple net lease retail properties"),
    "NNN":   ("RealEstate", "",                   "R.E.I.T.",                         "NNN REIT (National Retail Properties) — single-tenant net lease"),
    "WPC":   ("RealEstate", "",                   "R.E.I.T.",                         "W. P. Carey REIT — diversified net-lease"),
    "SRC":   ("RealEstate", "",                   "R.E.I.T.",                         "Spirit Realty (legacy ticker — now acquired by O) — net-lease"),
    "BNL":   ("RealEstate", "",                   "R.E.I.T.",                         "Broadstone Net Lease REIT — single-tenant net lease"),
    "AMT":   ("RealEstate", "",                   "R.E.I.T.",                         "American Tower REIT — telecoms towers"),
    "DLR":   ("RealEstate", "",                   "R.E.I.T.",                         "Digital Realty REIT — wholesale data center · hyperscale cloud colocation"),
    "EQIX":  ("RealEstate", "",                   "R.E.I.T.",                         "Equinix REIT — data center · interconnection moat · meet-me room network"),
    "PSA":   ("RealEstate", "",                   "R.E.I.T.",                         "Public Storage REIT — self storage"),
    "EXR":   ("RealEstate", "",                   "R.E.I.T.",                         "Extra Space Storage REIT — self storage"),
    "ARE":   ("RealEstate", "",                   "R.E.I.T.",                         "Alexandria Real Estate REIT — life-science labs"),
    "WELL":  ("RealEstate", "",                   "R.E.I.T.",                         "Welltower REIT — senior housing / medical office (healthcare)"),
    "VTR":   ("RealEstate", "",                   "R.E.I.T.",                         "Ventas REIT — healthcare"),
    "AVB":   ("RealEstate", "",                   "R.E.I.T.",                         "AvalonBay REIT — residential"),
    "EQR":   ("RealEstate", "",                   "R.E.I.T.",                         "Equity Residential REIT — residential"),
    "MAA":   ("RealEstate", "",                   "R.E.I.T.",                         "Mid-America Apartment REIT — residential"),
    "ESS":   ("RealEstate", "",                   "R.E.I.T.",                         "Essex Property Trust REIT — residential"),
    "VICI":  ("RealEstate", "",                   "R.E.I.T.",                         "VICI Properties REIT — gaming / experiential"),
    "BXP":   ("RealEstate", "",                   "R.E.I.T.",                         "Boston Properties REIT — office"),
    "VNO":   ("RealEstate", "",                   "R.E.I.T.",                         "Vornado Realty REIT — NYC office"),
    "STAG":  ("RealEstate", "",                   "R.E.I.T.",                         "STAG Industrial REIT — single-tenant industrial"),
    "HST":   ("RealEstate", "",                   "R.E.I.T.",                         "Host Hotels REIT — hospitality"),
    "RHP":   ("RealEstate", "",                   "R.E.I.T.",                         "Ryman Hospitality REIT — hotels / entertainment"),
    "APLE":  ("RealEstate", "",                   "R.E.I.T.",                         "Apple Hospitality REIT — hotels"),
    "KIM":   ("RealEstate", "",                   "Retail (REITs)",                   "Kimco Realty REIT — open-air shopping centers"),
    "FRT":   ("RealEstate", "",                   "Retail (REITs)",                   "Federal Realty REIT — retail"),
    "REG":   ("RealEstate", "",                   "Retail (REITs)",                   "Regency Centers REIT — grocery-anchored retail"),
    "MAC":   ("RealEstate", "",                   "Retail (REITs)",                   "Macerich REIT — class-A malls"),
    "DOC":   ("RealEstate", "",                   "R.E.I.T.",                         "Healthpeak Properties REIT — healthcare"),
    "OHI":   ("RealEstate", "",                   "R.E.I.T.",                         "Omega Healthcare REIT — skilled nursing"),

    # ── Health Care / Biopharma ────────────────────────────────────────────────
    "PFE":   ("Biopharma", "",  "Drugs (Pharmaceutical)",    ""),
    "MRNA":  ("Biopharma", "",  "Drugs (Biotechnology)",     ""),
    "AMGN":  ("Biopharma", "",  "Drugs (Biotechnology)",     "Amgen"),
    "GILD":  ("Biopharma", "",  "Drugs (Biotechnology)",     "Gilead Sciences"),
    "ABBV":  ("Biopharma", "",  "Drugs (Pharmaceutical)",    "AbbVie"),
    "LLY":   ("Biopharma", "Large Cap Pharma",  "Drugs (Pharmaceutical)",    "Eli Lilly"),
    "JNJ":   ("Biopharma", "",  "Drugs (Pharmaceutical)",    "Johnson & Johnson (post-Kenvue spin-off)"),
    "MDT":   ("Biopharma", "",               "Healthcare Products",    "Medtronic — MedTech devices"),
    "ISRG":  ("Biopharma", "",               "Healthcare Products",    "Intuitive Surgical"),
    "NVO":   ("Biopharma", "",               "Drugs (Pharmaceutical)", "Novo Nordisk ADR — GLP-1/obesity; 20-F filer (DKK reporting currency)"),
    "TXG":   ("Biopharma", "LifeSciTools",   "Healthcare Products",    "10X Genomics — single-cell/spatial genomics instruments; tools co, NOT drug developer"),
    "MRK":   ("Biopharma", "",               "Drugs (Pharmaceutical)",    "Merck"),
    "VRTX":  ("Biopharma", "",               "Drugs (Biotech)",           "Vertex Pharmaceuticals"),
    "REGN":  ("Biopharma", "",               "Drugs (Biotech)",           "Regeneron"),
    "BIIB":  ("Biopharma", "",               "Drugs (Biotech)",           "Biogen — MS + Alzheimer's (Leqembi) + ophthalmology; patent cliff on Tecfidera + Tysabri biosimilar risk"),
    "BMY":   ("Biopharma", "",               "Drugs (Pharmaceutical)",    "Bristol-Myers Squibb — oncology + cardiovascular; Eliquis/Opdivo LOE exposure"),
    "CRSP":  ("Biopharma", "",               "Drugs (Biotech)",           "CRISPR Therapeutics — gene editing; Casgevy launch"),
    "BEAM":  ("Biopharma", "",               "Drugs (Biotech)",           "Beam Therapeutics — base editing platform; pre-commercial"),
    "SAGE":  ("Biopharma", "",               "Drugs (Biotech)",           "Sage Therapeutics — CNS; zuranolone with Biogen"),
    "SRPT":  ("Biopharma", "",               "Drugs (Biotech)",           "Sarepta — DMD gene therapy (Elevidys)"),
    "ARWR":  ("Biopharma", "",               "Drugs (Biotech)",           "Arrowhead — RNAi platform (plozasiran, olpasiran w/ Amgen)"),
    "IONS":  ("Biopharma", "",               "Drugs (Biotech)",           "Ionis — antisense oligonucleotides (Spinraza, Waylivra)"),
    "ALNY":  ("Biopharma", "",               "Drugs (Biotech)",           "Alnylam — RNAi platform (Onpattro, Amvuttra)"),
    "RHHBY": ("Biopharma", "",               "Drugs (Pharmaceutical)",    "Roche ADR — oncology + diagnostics"),
    "NVS":   ("Biopharma", "",               "Drugs (Pharmaceutical)",    "Novartis ADR — Entresto, Cosentyx"),
    "AZN":   ("Biopharma", "",               "Drugs (Pharmaceutical)",    "AstraZeneca ADR — oncology (Tagrisso, Enhertu)"),
    "GSK":   ("Biopharma", "",               "Drugs (Pharmaceutical)",    "GSK ADR — vaccines, HIV, respiratory"),
    "SNY":   ("Biopharma", "",               "Drugs (Pharmaceutical)",    "Sanofi ADR — Dupixent, vaccines"),
    "TAK":   ("Biopharma", "",               "Drugs (Pharmaceutical)",    "Takeda ADR — rare disease, oncology"),
    "SYK":   ("Biopharma", "",               "Healthcare Products",       "Stryker — MedTech"),
    "BSX":   ("Biopharma", "",               "Healthcare Products",       "Boston Scientific"),
    "ABT":   ("Biopharma", "",               "Healthcare Products",       "Abbott Laboratories"),
    "TMO":   ("Biopharma", "CDMO / Life Science Tools", "Healthcare Products", "Thermo Fisher"),
    "DHR":   ("Biopharma", "CDMO / Life Science Tools", "Healthcare Products", "Danaher"),
    "A":     ("Biopharma", "CDMO / Life Science Tools", "Healthcare Products", "Agilent Technologies"),
    "WAT":   ("Biopharma", "CDMO / Life Science Tools", "Healthcare Products", "Waters Corporation"),
    "MTD":   ("Biopharma", "CDMO / Life Science Tools", "Healthcare Products", "Mettler-Toledo"),
    "WBA":   ("HealthcareServices", "Managed Care", "Retail (Pharmacy)", "Walgreens Boots Alliance"),
    "TDOC":  ("Biopharma", "",               "Healthcare Products",       "Teladoc Health — digital health"),
    "GDRX":  ("Biopharma", "",               "Healthcare Products",       "GoodRx — digital pharmacy"),
    "UNH":   ("HealthcareServices", "Managed Care", "Healthcare Support Services", "UnitedHealth Group"),
    "CI":    ("HealthcareServices", "Managed Care", "Healthcare Support Services", "Cigna"),
    "HUM":   ("HealthcareServices", "Managed Care", "Healthcare Support Services", "Humana"),
    "CVS":   ("HealthcareServices", "Managed Care", "Healthcare Support Services", "CVS Health — PBM + Aetna"),
    "ELV":   ("HealthcareServices", "Managed Care", "Healthcare Support Services", "Elevance Health (fmr Anthem)"),
    "MOH":   ("HealthcareServices", "Managed Care", "Healthcare Support Services", "Molina Healthcare — Medicaid"),
    "CNC":   ("HealthcareServices", "Managed Care", "Healthcare Support Services", "Centene — Medicaid/ACA"),

    # ── Energy ────────────────────────────────────────────────────────────────
    "XOM":   ("Resources", "",              "Oil/Gas (Integrated)",      "ExxonMobil — integrated O&G → Resources"),
    "CVX":   ("Resources", "",              "Oil/Gas (Integrated)",      "Chevron"),
    "COP":   ("Resources", "",              "Oil/Gas (Production and Exploration)", "ConocoPhillips"),
    "VST":   ("Energy",    "Merchant Power", "Power",                    "Vistra Energy — competitive power gen; NOT regulated utility"),
    "NEE":   ("Energy",    "Regulated Utility", "Utility (General)",     "NextEra Energy"),
    "DUK":   ("Energy",    "Regulated Utility", "Utility (General)",     "Duke Energy"),
    "SO":    ("Energy",    "Regulated Utility", "Utility (General)",     "Southern Company"),
    "XEL":   ("Energy",    "Regulated Utility", "Utility (General)",     "Xcel Energy"),
    "AWK":   ("Energy",    "Regulated Utility", "Utility (Water)",       "American Water Works"),
    "PCG":   ("Energy",    "Regulated Utility", "Utility (General)",     "PG&E"),
    "ENPH":  ("Energy",    "IPP",            "Green & Renewable Energy", "Enphase Energy — solar microinverters"),
    "FSLR":  ("Energy",    "IPP",            "Green & Renewable Energy", "First Solar"),
    "BE":    ("Energy",    "IPP",            "Green & Renewable Energy", "Bloom Energy — fuel cell power generation (hydrogen/natural gas)"),

    # ── Industrials ───────────────────────────────────────────────────────────
    "LMT":   ("Industrials", "Aerospace & Defense",  "Aerospace/Defense",  "Lockheed Martin"),
    "RTX":   ("Industrials", "Aerospace & Defense",  "Aerospace/Defense",  "RTX Corp (Raytheon)"),
    "BA":    ("Industrials", "Aerospace & Defense",  "Aerospace/Defense",  "Boeing"),
    "CAT":   ("Industrials", "",  "Machinery",          "Caterpillar"),
    "DE":    ("Industrials", "",  "Machinery",          "Deere & Company"),
    "GE":    ("Industrials", "Aerospace & Defense",  "Electrical Equipment", "GE Aerospace (post-Vernova spin-off)"),
    "GEV":   ("Industrials", "",  "Electrical Equipment", "GE Vernova — wind/gas turbine OEM + grid electrification; book-to-bill driven"),
    "HON":   ("Industrials", "",  "Electrical Equipment", "Honeywell"),
    "UPS":   ("Transportation", "", "Transportation",   "United Parcel Service"),
    "FDX":   ("Transportation", "", "Transportation",   "FedEx"),
    "DAL":   ("Transportation", "Airlines", "Air Transport", "Delta Air Lines"),
    "UAL":   ("Transportation", "Airlines", "Air Transport", "United Airlines"),

    # ── Materials / Resources ─────────────────────────────────────────────────
    "LIN":   ("Materials",  "",  "Chemical (Specialty)",  "Linde plc"),
    "NUE":   ("Materials",  "",  "Steel",                 "Nucor — steel mini-mills"),
    "FCX":   ("Resources",  "Mining (Major)",      "Metals & Mining",       "Freeport-McMoRan — copper/gold"),
    "NEM":   ("Resources",  "Mining (Major)",      "Precious Metals",       "Newmont Mining"),
    "XOM":   ("Resources",  "Upstream Oil & Gas",  "Oil/Gas (Integrated)",  "ExxonMobil — integrated O&G → Resources"),
    "CVX":   ("Resources",  "Upstream Oil & Gas",  "Oil/Gas (Integrated)",  "Chevron — integrated"),
    "COP":   ("Resources",  "Upstream Oil & Gas",  "Oil/Gas (E&P)",         "ConocoPhillips — pure-play E&P"),
    "EOG":   ("Resources",  "Upstream Oil & Gas",  "Oil/Gas (E&P)",         "EOG Resources"),
    "LEU":   ("Resources",  "",  "Uranium",               "Centrus Energy — uranium enrichment (SWU contracts); NOT power generation"),

    # ── Crypto ────────────────────────────────────────────────────────────────
    "MSTR":  ("Crypto", "",  "Diversified",  "MicroStrategy — BTC treasury company"),
    "MARA":  ("Crypto", "",  "Diversified",  "Marathon Digital — BTC miner"),
    "RIOT":  ("Crypto", "",  "Diversified",  "Riot Platforms — BTC miner"),
    "CLSK":  ("Crypto", "",  "Diversified",  "CleanSpark — BTC miner"),
    "BTDR":  ("Crypto", "",  "Diversified",  "Bitdeer — crypto mining hardware (SEALMINER ASICs) + hosting; OEM + miner hybrid"),

    # ── China / ADR tickers that are commonly misclassified ───────────────────
    "CHA":   ("Consumer", "",  "Restaurant/Dining",       "Chagee Holdings — premium Chinese tea brand (NASDAQ: CHA)"),
    # China Telecom trades as 0728.HK (HKEX) — no US ADR ticker mapping needed
    "CHT":   ("Telco",    "",  "Telecom. Services",      "Chunghwa Telecom ADR (Taiwan)"),
    "XIAOMI":("Consumer", "",  "Electronics (Consumer & Office)", "Xiaomi — consumer electronics"),
    "9988.HK": ("Tech", "",   "Software (Internet)",    "Alibaba HK listing"),

    # ── India ─────────────────────────────────────────────────────────────────
    # INFY and WIT moved to ProfessionalServices / IT Services section above
    "HDB":   ("Financials", "Regional Bank", "Banks (Regional)",    "HDFC Bank ADR"),

    # ── User-identified misclassification risk tickers ────────────────────────
    # SIRI: LLM frequently picks Consumer (subscription service feel);
    #       correct = Telco (Broadcasting) — satellite infrastructure + spectrum assets
    "SIRI":  ("Telco", "",             "Broadcasting",               "Sirius XM — satellite radio; Telco WACC 5.5%, not Consumer 7.5%"),

    # SMR: NuScale Power — pre-revenue nuclear SMR designer; sells reactor modules
    #      NOT a power generator → Industrials (Capital Goods), not Energy/Regulated Utility
    "SMR":   ("Industrials", "",       "Electrical Equipment",       "NuScale Power — SMR technology vendor; pre-revenue; Industrials not Energy"),

    # PONY: Pony.AI — AV software platform; revenue from robotaxi licences + software
    #       Damodaran would put in Transportation, but business model is software-first
    "PONY":  ("Tech", "",              "Software (System & Application)", "Pony.AI — AV software platform; Tech WACC applies (not Transportation)"),

    # CHAGEE: alternate long-form ticker lookup (canonical ticker is CHA)
    "CHAGEE":("Consumer", "",          "Restaurant/Dining",          "Chagee Holdings — alias; use CHA"),

    # ── Hong Kong (HKEX) — canonical "NNNNN.HK" format ───────────────────────
    # Sectors use internal pipeline names; sub-sector is human-readable label for screener display.
    # Source: user-provided classification table (100 HKEX well-known stocks, April 2026)

    # Technology
    "00700.HK": ("Tech",        "",  "Internet Platform",        "Tencent Holdings"),
    "09988.HK": ("Tech",        "",  "Software (Internet)",      "Alibaba Group HK listing"),
    "03690.HK": ("Tech",        "",  "Internet Platform",        "Meituan"),
    "09618.HK": ("Tech",        "",  "E-commerce",               "JD.com HK listing"),
    "09999.HK": ("Tech",        "",  "Gaming",                   "NetEase"),
    "09626.HK": ("Tech",        "",  "Internet Media",           "Bilibili"),
    "02018.HK": ("Tech",        "",  "Components",               "AAC Technologies"),
    "00992.HK": ("Tech",        "",  "PC & Hardware",            "Lenovo Group"),
    "02382.HK": ("Tech",        "",  "Optics",                   "Sunny Optical Technology"),
    "03888.HK": ("Tech",        "",  "Software",                 "Kingsoft Corporation"),
    "00268.HK": ("Tech",        "",  "Enterprise SaaS",          "Kingdee International"),
    "00285.HK": ("Tech",        "",  "Components",               "BYD Electronic"),
    "08083.HK": ("Tech",        "",  "SaaS/E-commerce",          "Youzan Technology"),
    "00909.HK": ("Tech",        "",  "PropTech SaaS",            "Mingyuan Cloud"),
    "02013.HK": ("Tech",        "",  "Enterprise SaaS",          "Weimob — marketing SaaS"),
    "00354.HK": ("Tech",        "",  "IT Services",              "Chinasoft Intl — IT outsourcing"),
    "01357.HK": ("Tech",        "",  "Apps & SaaS",              "Meitu"),
    "00763.HK": ("Tech",        "",  "Telecom Equipment",        "ZTE Corporation"),
    "09888.HK": ("Tech",        "",  "AI & Internet",            "Baidu Group"),
    "00772.HK": ("Tech",        "",  "Digital Content",          "China Literature"),
    "00020.HK": ("Tech",        "",  "AI / Vision",              "SenseTime"),
    "01810.HK": ("Tech",        "",  "Electronics",              "Xiaomi Group"),
    "01024.HK": ("Tech",        "",  "Software (Internet)",      "Kuaishou Technology"),
    "00981.HK": ("Semiconductor", "", "Semiconductors",           "SMIC — HK foundry"),
    "01347.HK": ("Semiconductor", "", "Semiconductors",           "Hua Hong Semi — specialty foundry"),
    "09660.HK": ("Semiconductor", "", "Semiconductors",           "Horizon Robotics — auto AI chips"),
    "00100.HK": ("Tech",        "",  "Generative AI",            "MiniMax"),
    "03896.HK": ("Tech",        "",  "Cloud Computing",          "Kingsoft Cloud"),

    # Telecom
    "00941.HK": ("Telco",       "",  "Telco",                    "China Mobile"),
    "00762.HK": ("Telco",       "",  "Telco",                    "China Unicom"),
    "00728.HK": ("Telco",       "",  "Telco",                    "China Telecom"),
    "00788.HK": ("Telco",       "",  "Tower Infrastructure",     "China Tower"),

    # Energy
    "00883.HK": ("Energy",      "",  "Oil & Gas",                "CNOOC"),
    "00857.HK": ("Energy",      "",  "Oil & Gas",                "PetroChina"),
    "00386.HK": ("Energy",      "",  "Oil & Gas",                "Sinopec"),
    "00991.HK": ("Energy",      "",  "Power Generation",         "Datang International Power"),
    "01816.HK": ("Energy",      "",  "Nuclear Power",            "CGN Power"),

    # Financials
    "00005.HK": ("Financials",  "Money Center Bank (EU)",  "Banking",  "HSBC Holdings"),
    "01299.HK": ("Financials",  "",  "Insurance",                "AIA Group"),
    "02318.HK": ("Financials",  "",  "Insurance",                "Ping An Insurance"),
    "03988.HK": ("Financials",  "EM Bank",          "Banking",        "Bank of China"),
    "01398.HK": ("Financials",  "EM Bank",          "Banking",        "ICBC"),
    "00939.HK": ("Financials",  "EM Bank",          "Banking",        "China Construction Bank"),
    "03968.HK": ("Financials",  "EM Bank",          "Banking",        "China Merchants Bank"),
    "02628.HK": ("Financials",  "",  "Insurance",                "China Life Insurance"),
    "01288.HK": ("Financials",  "",  "Banking",                  "Agricultural Bank of China"),
    "00998.HK": ("Financials",  "",  "Banking",                  "CITIC Bank"),
    "03328.HK": ("Financials",  "",  "Banking",                  "Bank of Communications"),
    "01658.HK": ("Financials",  "",  "Banking",                  "Postal Savings Bank of China"),
    "00388.HK": ("Financials",  "",  "Exchange",                 "Hong Kong Exchanges (HKEX)"),
    "02388.HK": ("Financials",  "",  "Banking",                  "BOC Hong Kong"),
    "00011.HK": ("Financials",  "",  "Banking",                  "Hang Seng Bank"),
    "02888.HK": ("Financials", "Money Center Bank",    "Banks (Diversified)",    "Standard Chartered"),
    "09959.HK": ("Financials", "FinTech",              "Software - Infrastructure", "Linklogis — supply chain fintech"),
    "09923.HK": ("Financials", "FinTech",              "Software - Infrastructure", "Yeahka — payment tech"),
    "00806.HK": ("Financials", "Asset Manager",        "Asset Management",         "Value Partners"),
    "01359.HK": ("Financials", "Asset Manager",        "Asset Management",         "China Cinda Asset Mgmt"),
    "02378.HK": ("Financials", "Insurance",            "Insurance - Life",          "Prudential plc"),
    "01336.HK": ("Financials", "Insurance",            "Insurance - Life",          "New China Life"),
    "06030.HK": ("Financials", "Investment Bank",      "Capital Markets",           "CITIC Securities"),
    "03908.HK": ("Financials", "Investment Bank",      "Capital Markets",           "CICC"),
    "06886.HK": ("Financials", "Investment Bank",      "Capital Markets",           "Huatai Securities"),
    "06837.HK": ("Financials", "Investment Bank",      "Capital Markets",           "Haitong Securities"),

    # Real Estate
    "00016.HK": ("RealEstate",  "",  "Property Development",     "Sun Hung Kai Properties"),
    "00012.HK": ("RealEstate",  "",  "Property Development",     "Henderson Land Development"),
    "00688.HK": ("RealEstate",  "",  "Property Development",     "China Overseas Land & Investment"),
    "01113.HK": ("RealEstate",  "",  "Property Development",     "CK Asset Holdings"),
    "06098.HK": ("RealEstate",  "",  "Property Management",      "Country Garden Services"),
    "00873.HK": ("RealEstate",  "",  "Property Management",      "Shimao Services"),
    "01516.HK": ("RealEstate",  "",  "Property Management",      "Sunac Services"),
    "06049.HK": ("RealEstate",  "",  "Property Management",      "Poly Property Services"),
    "01918.HK": ("RealEstate",  "",  "Property Development",     "Sunac China"),
    "03900.HK": ("RealEstate",  "",  "Property Development",     "Greenland Hong Kong"),
    "02423.HK": ("RealEstate",  "",  "Prop Marketplace",         "KE Holdings (Beike)"),

    # Healthcare / Biopharma
    "01177.HK": ("Biopharma",   "",  "Pharmaceutical",           "Sino Biopharmaceutical"),
    "02269.HK": ("Biopharma",   "",  "Biotech CDMO",             "Wuxi Biologics"),
    "02268.HK": ("Biopharma",   "",  "Biotechnology",            "Wuxi XDC Cayman"),
    "00241.HK": ("Biopharma",   "",  "Health Platform",          "Alibaba Health"),
    "02359.HK": ("Biopharma",   "",  "CRO/CDMO",                 "Wuxi AppTec"),
    "02196.HK": ("Biopharma",   "",  "Pharmaceutical",           "Fosun Pharma"),
    "06185.HK": ("Biopharma",   "",  "Biotech/Vaccine",          "CanSino Biologics"),
    "06618.HK": ("Biopharma",   "",  "Health Platform",          "JD Health"),
    "01093.HK": ("Biopharma",   "",  "Pharmaceutical",           "CSPC Pharmaceutical"),
    "03692.HK": ("Biopharma", "",  "Drugs (Pharmaceutical)", "Hansoh Pharma"),
    "03320.HK": ("Biopharma", "",  "Drugs (Pharmaceutical)", "CR Pharma"),
    "01801.HK": ("Biopharma", "",  "Drugs (Biotech)",        "Innovent Biologics"),
    "09926.HK": ("Biopharma", "",  "Drugs (Biotech)",        "Akeso Inc"),
    "06160.HK": ("Biopharma", "",  "Drugs (Biotech)",        "BeiGene"),
    "09995.HK": ("Biopharma", "",  "Drugs (Biotech)",        "RemeGen"),
    "00853.HK": ("Biopharma", "",  "Healthcare Products",    "MicroPort Scientific"),
    "02252.HK": ("Biopharma", "",  "Healthcare Products",    "MicroPort Robot"),
    "01302.HK": ("Biopharma", "",  "Healthcare Products",    "LifeTech Scientific"),
    "09996.HK": ("Biopharma", "",  "Healthcare Products",    "Peijia Medical"),
    "01548.HK": ("Biopharma", "CDMO / Life Science Tools", "Healthcare Products", "Genscript Biotech"),
    "03759.HK": ("Biopharma", "CDMO / Life Science Tools", "Healthcare Products", "Pharmaron Beijing"),
    "01833.HK": ("Biopharma", "",  "Healthcare Products",    "Ping An Healthcare"),
    "01099.HK": ("Biopharma", "",  "Drugs (Pharmaceutical)", "Sinopharm Group"),
    "02601.HK": ("Financials", "Insurance", "Insurance - Life", "CPIC"),

    # Consumer — Apparel & Footwear
    "02020.HK": ("Consumer",    "",  "Sportswear",               "Anta Sports — premium domestic brand; P/E ~25x near US level"),
    "02331.HK": ("Consumer",    "",  "Sportswear",               "Li Ning"),
    "02313.HK": ("Consumer",    "",  "Apparel/Mfg",              "Shenzhou International — OEM apparel manufacturing"),
    "01368.HK": ("Consumer",    "",  "Sportswear",               "Xtep International"),
    "03998.HK": ("Consumer",    "",  "Apparel",                  "Bosideng — down jacket brand"),
    "01910.HK": ("Consumer",    "",  "Luggage",                  "Samsonite International"),
    # Consumer — Consumer Durables (profile override)
    "06690.HK": ("Consumer",    "Consumer Durables", "Home Appliance", "Haier Smart Home — major appliances"),
    "01691.HK": ("Consumer",    "Consumer Durables", "Home Appliance", "JS Global Lifestyle — SharkNinja; small appliances"),
    "00303.HK": ("Consumer",    "Consumer Durables", "Electronics",    "VTech Holdings — electronic learning toys"),
    "00751.HK": ("Consumer",    "Consumer Durables", "Electronics",    "Skyworth Group — TV/display"),
    "00921.HK": ("Consumer",    "Consumer Durables", "Electronics",    "Hisense Home Appliances"),
    "00669.HK": ("Consumer",    "Consumer Durables", "Power Tools",    "Techtronic Industries — Milwaukee/Ryobi"),
    # Consumer — Retail (General)
    "01929.HK": ("Consumer",    "",  "Jewelry & Retail",         "Chow Tai Fook — jewelry retail"),
    "06808.HK": ("Consumer",    "",  "Retail (General)",         "Sun Art Retail — hypermarket"),
    "00178.HK": ("Consumer",    "",  "Retail (Special Lines)",   "Sa Sa International — beauty retail"),
    "00984.HK": ("Consumer",    "",  "Retail (General)",         "Aeon Stores — supermarket"),
    "00709.HK": ("Consumer",    "",  "Retail (General)",         "Giordano International — casual wear retail"),
    # Consumer — Automotive & EV (profile override)
    "01211.HK": ("Consumer",    "Automotive & EV", "EV & Battery",    "BYD — global EV leader; P/E ~25x near US level"),
    "01810.HK": ("Consumer",    "Automotive & EV", "Electronics/EV",  "Xiaomi — smartphone + EV pivot; SU7 production ramp"),
    "02015.HK": ("Consumer",    "Automotive & EV", "EV / Auto",       "Li Auto — profitable EV; EREV powertrain"),
    "09868.HK": ("Consumer",    "Automotive & EV", "EV / Auto",       "XPeng — EV + autonomous driving"),
    "00175.HK": ("Consumer",    "Automotive & EV", "Auto & Truck",    "Geely Automobile — traditional + EV transition"),
    "09866.HK": ("Consumer",    "Automotive & EV", "EV / Auto",       "NIO — premium EV; battery swap model"),
    # Consumer — Travel & Dining (profile override)
    "09961.HK": ("Consumer",    "Travel & Dining", "OTA/Travel",      "Trip.com Group — China OTA platform"),
    "00027.HK": ("Consumer",    "Travel & Dining", "Gaming & Leisure","Galaxy Entertainment — Macau casino"),
    "01928.HK": ("Consumer",    "Travel & Dining", "Gaming & Leisure","Sands China — Macau casino"),
    "06862.HK": ("Consumer",    "Travel & Dining", "Restaurant",      "Haidilao — hotpot chain; P/E ~30x premium"),
    "01179.HK": ("Consumer",    "Travel & Dining", "Hotels",          "H World Group — hotel chain"),
    "09922.HK": ("Consumer",    "Travel & Dining", "Restaurant",      "Jiumaojiu Group — multi-brand restaurants"),
    "09987.HK": ("Consumer",    "Travel & Dining", "Restaurant",      "Yum China — KFC/Pizza Hut China"),
    "02150.HK": ("Consumer",    "Travel & Dining", "F&B / Cafe",      "Nayuki Holdings — tea chain"),
    "00780.HK": ("Consumer",    "Travel & Dining", "Travel & Tourism","Tongcheng Travel — OTA"),
    # Consumer — Food & Beverage / Other
    "09992.HK": ("Consumer",    "",  "Toys & IP",                "Pop Mart International"),
    "00322.HK": ("Consumer",    "",  "Food & Beverage",          "Tingyi"),
    "00151.HK": ("Consumer",    "",  "Food & Beverage",          "Want Want China"),
    "02319.HK": ("Consumer",    "",  "Food & Beverage",          "China Mengniu Dairy"),
    "01458.HK": ("Consumer",    "",  "Food & Beverage",          "Zhou Hei Ya"),
    "01579.HK": ("Consumer",    "",  "Food & Beverage",          "Yihai International"),
    "06186.HK": ("Consumer",    "",  "Infant Formula",           "China Feihe"),
    "00168.HK": ("Consumer",    "",  "Beer / Beverage",          "Tsingtao Brewery"),
    "00291.HK": ("Consumer",    "",  "Beer / Beverage",          "China Resources Beer"),
    "01876.HK": ("Consumer",    "",  "Beer / Beverage",          "Budweiser APAC"),
    "09633.HK": ("Consumer",    "",  "Beverages",                "Nongfu Spring"),
    "01896.HK": ("Consumer",    "",  "Entertainment",            "Maoyan Entertainment"),
    "01060.HK": ("Consumer",    "",  "Entertainment",            "Damai Entertainment"),
    "01797.HK": ("Consumer",    "",  "E-commerce/Edu",           "East Buy (New Oriental Online)"),
    "09901.HK": ("Consumer",    "",  "Edu & Training",           "New Oriental"),
    "06969.HK": ("Consumer",    "",  "Vaping / FMCG",            "Smoore International"),
    "02333.HK": ("Industrials", "",  "Auto & Truck",             "Great Wall Motor"),
    "02618.HK": ("Industrials", "",  "Logistics",                "JD Logistics"),
    "02057.HK": ("Industrials", "",  "Express Delivery",         "ZTO Express"),
    "01919.HK": ("Industrials", "",  "Shipping",                 "COSCO Shipping Holdings"),
    "01138.HK": ("Industrials", "",  "Shipping / Tankers",       "COSCO Shipping Energy"),
    "00656.HK": ("Industrials", "",  "Conglomerate",             "Fosun International"),
    "00001.HK": ("Industrials", "",  "Diversified",              "CK Hutchison Holdings"),
    "03750.HK": ("Industrials", "",  "EV Battery",               "CATL HK listing"),

    # Materials
    "00914.HK": ("Industrials", "",  "Cement",                   "Anhui Conch Cement"),
    "02600.HK": ("Industrials", "",  "Metals & Mining",          "Aluminum Corp of China"),
    "01772.HK": ("Industrials", "",  "Battery/Lithium",          "Ganfeng Lithium"),
    "09696.HK": ("Industrials", "",  "Battery/Lithium",          "Tianqi Lithium"),
    "06865.HK": ("Industrials", "",  "Specialty Glass",          "Flat Glass Group"),
    "00868.HK": ("Industrials", "",  "Specialty Glass",          "Xinyi Glass"),
    "03323.HK": ("Industrials", "",  "Cement",                   "China National Building Material"),
    "02513.HK": ("Tech",        "",  "Software (Internet)",      "Knowledge Atlas — edtech"),
}


# ── Guardrail 2: sector_profiles.py validation function ──────────────────────

# Valid internal sectors — must stay in sync with SECTOR_WACC keys
_VALID_SECTORS: frozenset[str] = frozenset(SECTOR_WACC.keys())

# Sectors where the LLM regularly misclassifies; extra scrutiny applied
_HIGH_RISK_MISCLASSIFICATION: dict[str, str] = {
    "Crypto":   "Crypto companies are often misclassified as Tech or Financials",
    "RealEstate": "REITs are often classified as Financials by the LLM",
    "Resources": "Oil/Gas (Integrated) often classified as Energy; use Resources for E&P",
    "Transportation": "Airlines/Rail often classified as Industrials",
    "ProfessionalServices": "Ad agencies often classified as Tech or Consumer",
}

# Sectors where an incorrect classification causes the largest WACC error (bps)
_WACC_ERROR_SENSITIVITY: dict[str, int] = {
    "Energy":      350,  # Regulated Utility 4.5% vs Merchant Power 7.5% = 300bps within sector
    "Financials":  400,  # Bank 5.0% vs FinTech 9.0% = 400bps within sector
    "Telco":       400,  # Telco 5.5% vs Tech 9.5% = 400bps cross-sector
    "RealEstate":  400,  # REIT 5.5% vs Financials 6.0% = 50bps (but valuation methods differ)
    "Crypto":      550,  # Crypto 15.0% vs Tech 9.5% = 550bps — largest cross-sector error
    "Biopharma":   100,  # Biopharma 8.5% vs Tech 9.5% = 100bps
    "Consumer":    200,  # Consumer 7.5% vs Tech 9.5% = 200bps
    "Resources":   250,  # Resources 7.0% vs Energy 6.5% = 50bps but valuation methods differ
    "Industrials": 100,  # Industrials 8.0% vs Tech 9.5% = 150bps
}


def validate_sector(
    ticker: str,
    llm_sector: str,
    allow_override: bool = True,
) -> tuple[str, str, str | None]:
    """
    Cross-validate the LLM's sector classification against the hard-coded
    ticker lookup table and return an audit result.

    Args:
        ticker:         Exchange ticker (e.g. "NVDA", "VST").
        llm_sector:     Sector string returned by the Phase 2 LLM.
        allow_override: If True (default), the lookup table wins over the LLM when
                        they disagree. Set False to use LLM output with a warning only.

    Returns:
        (final_sector, confidence, warning)
        final_sector — the sector to use downstream ("Tech", "Financials", …)
        confidence   — "HIGH" | "MEDIUM" | "LOW"
        warning      — human-readable warning string, or None if no issue
    """
    ticker_upper = ticker.upper()

    # ── Not in lookup — trust the LLM but flag known-risky sectors ────────────
    if ticker_upper not in TICKER_SECTOR_LOOKUP:
        if llm_sector not in _VALID_SECTORS:
            # LLM returned a sector string that isn't in SECTOR_WACC at all
            return (
                "Tech",   # safe fallback
                "LOW",
                f"[SECTOR] '{llm_sector}' is not a recognised internal sector for {ticker}. "
                f"Falling back to 'Tech'. Add {ticker} to TICKER_SECTOR_LOOKUP to resolve.",
            )
        note = _HIGH_RISK_MISCLASSIFICATION.get(llm_sector)
        if note:
            return (
                llm_sector,
                "MEDIUM",
                f"[SECTOR] {ticker} classified as '{llm_sector}' — {note}. "
                f"Add {ticker} to TICKER_SECTOR_LOOKUP to lock classification.",
            )
        return (llm_sector, "HIGH", None)

    # ── In lookup — compare against LLM output ────────────────────────────────
    expected_sector, expected_profile, damo_ig, notes = TICKER_SECTOR_LOOKUP[ticker_upper]

    if llm_sector == expected_sector:
        # Agreement — both sources match
        return (expected_sector, "HIGH", None)

    # Disagreement — compute WACC error magnitude
    wacc_expected = get_wacc(expected_sector, 0.0, "neutral", expected_profile)
    wacc_llm      = get_wacc(llm_sector,      0.0, "neutral")
    wacc_delta_bps = abs(wacc_expected - wacc_llm) * 10_000

    warning = (
        f"[SECTOR MISMATCH] {ticker}: LLM classified as '{llm_sector}', "
        f"lookup expects '{expected_sector}' (Damodaran: {damo_ig}). "
        f"WACC delta = {wacc_delta_bps:.0f} bps. "
        f"{'Override applied.' if allow_override else 'LLM value retained (allow_override=False).'}"
        + (f" Note: {notes}" if notes else "")
    )

    final_sector = expected_sector if allow_override else llm_sector
    confidence   = "HIGH" if allow_override else "LOW"
    return (final_sector, confidence, warning)


def get_wacc_profile_for_ticker(ticker: str) -> tuple[str, str]:
    """
    Convenience function: return (sector, wacc_profile) for a known ticker.
    Falls back to ("Tech", "") if ticker is not in the lookup.

    Used by DCF agent to get the profile hint when the strategic router
    did not store one (current pipeline only stores sector, not profile).
    """
    entry = TICKER_SECTOR_LOOKUP.get(ticker.upper())
    if entry:
        return entry[0], entry[1]
    return ("Tech", "")


# ═══════════════════════════════════════════════════════════════════════════════
# Singapore (SGX) Sector Configuration
# ═══════════════════════════════════════════════════════════════════════════════

# Singapore is AAA-rated, country risk premium ~0.5% (50bps)
_SG_CRP = 0.005

SG_SECTOR_WACC: dict[str, float] = {
    "Financials":    SECTOR_WACC.get("Financials", 0.06)    + _SG_CRP,  # 6.5%
    "REIT":          0.055,                                              # 5.5% — regulated, high distribution
    "Tech":          SECTOR_WACC.get("Tech", 0.095)         + _SG_CRP,  # 10.0%
    "Industrials":   SECTOR_WACC.get("Industrials", 0.08)   + _SG_CRP,  # 8.5%
    "Property":      0.070,                                              # 7.0%
    "Telco":         SECTOR_WACC.get("Telco", 0.055)        + _SG_CRP,  # 6.0%
    "Consumer":      SECTOR_WACC.get("Consumer", 0.075)     + _SG_CRP,  # 8.0%
    "Energy":        SECTOR_WACC.get("Energy", 0.065)       + _SG_CRP,  # 7.0%
    "Healthcare":    SECTOR_WACC.get("Biopharma", 0.085)    + _SG_CRP,  # 9.0%
}


# ── REIT-specific VGPM scoring weights ───────────────────────────────────────
# REITs need yield-based and cash-flow-based valuation rather than P/E.
# These weights are used when the ticker's sector is "REIT" in the SGX universe.
REIT_VGPM_WEIGHTS = {
    "valuation": {
        # P/FFO replaces P/E; distribution yield and P/NAV are primary
        "div_yield":   0.30,    # distribution yield — the #1 REIT metric
        "pb":          0.25,    # P/NAV proxy (P/Book ≈ P/NAV for REITs)
        "fcf_yield":   0.25,    # AFFO yield proxy
        "ev_ebitda":   0.20,    # EV/EBITDA as cap rate proxy
    },
    "growth": {
        # DPU growth, revenue growth, NAV growth
        "rev_growth":    0.30,  # same-store NOI proxy (revenue growth)
        "rev_cagr_3y":   0.25,  # long-term organic growth
        "eps_growth":    0.25,  # DPU growth proxy (EPS ≈ DPU for REITs)
        "net_inc_growth": 0.20, # NPI growth
    },
    "profitability": {
        # Operating margins, FCF conversion (AFFO/FFO), interest coverage
        "net_margin":      0.25,  # NPI margin
        "roe":             0.20,  # return on equity
        "fcf_conversion":  0.25,  # AFFO/FFO quality
        "piotroski":       0.15,  # financial health
        "asset_turnover":  0.15,  # capital efficiency
    },
    "momentum": {
        "price_1y":          0.30,
        "rec_score":         0.25,
        "earnings_revision": 0.25,
        "price_3m":          0.20,
    },
}


# ── SGX TICKER_SECTOR_LOOKUP ─────────────────────────────────────────────────
# Format: "CODE.SI": (sector, profile_hint, industry, company_name)
# Imported by screener_service and analysis routes for sector classification.
SGX_TICKER_SECTOR_LOOKUP: dict[str, tuple[str, str, str, str]] = {
    # Banks
    "D05.SI":  ("Financials", "Money Center Bank", "Banks",           "DBS Group — SG money-center bank"),
    "O39.SI":  ("Financials", "Money Center Bank", "Banks",           "OCBC Bank — SG money-center bank"),
    "U11.SI":  ("Financials", "Money Center Bank", "Banks",           "UOB — SG money-center bank"),
    "S68.SI":  ("Financials", "Exchange",    "Capital Markets",        "Singapore Exchange"),
    "9CI.SI":  ("Financials", "AssetMgmt",   "Asset Management",       "CapitaLand Investment"),
    "U09.SI":  ("Financials", "Insurance",   "Insurance",              "United Overseas Insurance"),
    # Telco
    "Z74.SI":  ("Telco",      "",            "Telecom Services",       "SingTel"),
    "CC3.SI":  ("Telco",      "",            "Telecom Services",       "StarHub"),
    # Industrials
    "C6L.SI":  ("Industrials", "Airline",    "Air Transport",          "Singapore Airlines"),
    "BN4.SI":  ("Industrials", "Conglomerate","Conglomerates",         "Keppel Corporation"),
    "BS6.SI":  ("Industrials", "Shipbuilding","Shipbuilding",          "Yangzijiang Shipbuilding"),
    "U96.SI":  ("Industrials", "Utilities",  "Utilities & Energy",     "Sembcorp Industries"),
    "S63.SI":  ("Industrials", "Defence",    "Aerospace & Defence",    "ST Engineering"),
    "S58.SI":  ("Industrials", "Airport",    "Airport Services",       "SATS"),
    "C52.SI":  ("Industrials", "Transport",  "Transportation",         "ComfortDelGro"),
    "J36.SI":  ("Industrials", "Conglomerate","Conglomerates",         "Jardine Matheson"),
    "J37.SI":  ("Industrials", "Conglomerate","Conglomerates",         "Jardine C&C"),
    "S51.SI":  ("Industrials", "Marine",     "Marine & Offshore",      "Seatrium"),
    "MR7.SI":  ("Industrials", "Marine",     "Marine Services",        "Marco Polo Marine"),
    "S56.SI":  ("Industrials", "Logistics",  "Postal & Logistics",     "Singpost"),
    "ACV.SI":  ("Industrials", "Services",   "Vehicle Inspection",     "Vicom"),
    # Consumer
    "F34.SI":  ("Consumer",    "Food",       "Food Products",          "Wilmar International"),
    "Y92.SI":  ("Consumer",    "Beverages",  "Beverages",              "Thai Beverage"),
    "G13.SI":  ("Consumer",    "Gaming",     "Casinos & Gaming",       "Genting Singapore"),
    "E5H.SI":  ("Consumer",    "Agri",       "Agricultural Products",  "Golden Agri-Resources"),
    "AGS.SI":  ("Consumer",    "Retail",     "Grocery Retail",         "Sheng Siong Group"),
    "EB5.SI":  ("Consumer",    "Agri",       "Palm Oil",               "First Resources"),
    "P8Z.SI":  ("Consumer",    "Agri",       "Palm Oil",               "Bumitama Agri"),
    "T14.SI":  ("Consumer",    "Food",       "Food & Agribusiness",    "Olam Group"),
    # Tech
    "V03.SI":  ("Tech",        "Electronics","Electronics Manufacturing","Venture Corporation"),
    "AWX.SI":  ("Tech",        "SemiEquip",  "Semiconductor Equipment","AEM Holdings"),
    "5DD.SI":  ("Tech",        "SemiEquip",  "Semiconductor Equipment","Micro-Mechanics"),
    "BHK.SI":  ("Tech",        "SemiEquip",  "Semiconductor Equipment","UMS Holdings"),
    "MZH.SI":  ("Tech",        "Materials",  "Advanced Materials",     "Nanofilm Technologies"),
    "5CP.SI":  ("Tech",        "Software",   "Banking Software",       "Silverlake Axis"),
    # Property
    "C09.SI":  ("Property",    "Developer",  "Real Estate Development","City Developments"),
    "H78.SI":  ("Property",    "Developer",  "Real Estate Development","Hongkong Land"),
    "U14.SI":  ("Property",    "Developer",  "Real Estate Development","UOL Group"),
    "OYY.SI":  ("Property",    "Services",   "Real Estate Services",   "PropNex"),
    "W05.SI":  ("Property",    "Developer",  "Real Estate Development","Wing Tai Holdings"),
    "40T.SI":  ("Property",    "Dormitory",  "Workers Dormitory",      "Centurion Corporation"),
    # Energy
    "RE4.SI":  ("Energy",      "Coal",       "Coal Mining",            "Geo Energy Resources"),
    # Healthcare
    "CLN.SI":  ("Healthcare",  "MedDevices", "Medical Gloves",         "Riverstone Holdings"),
    "A50.SI":  ("Healthcare",  "Services",   "Healthcare Services",    "Thomson Medical Group"),
    # REITs
    "A17U.SI": ("REIT",        "Industrial", "Industrial REIT",        "CapitaLand Ascendas REIT"),
    "C38U.SI": ("REIT",        "Retail",     "Retail REIT",            "CapitaLand Integrated Commercial Trust"),
    "N2IU.SI": ("REIT",        "Commercial", "Commercial REIT",        "Mapletree Pan Asia Commercial Trust"),
    "ME8U.SI": ("REIT",        "Industrial", "Industrial REIT",        "Mapletree Industrial Trust"),
    "M44U.SI": ("REIT",        "Logistics",  "Logistics REIT",         "Mapletree Logistics Trust"),
    "BUOU.SI": ("REIT",        "Logistics",  "Logistics REIT",         "Frasers Logistics & Commercial Trust"),
    "J69U.SI": ("REIT",        "Retail",     "Retail REIT",            "Frasers Centrepoint Trust"),
    "T82U.SI": ("REIT",        "Commercial", "Commercial REIT",        "Suntec REIT"),
    "K71U.SI": ("REIT",        "Office",     "Office REIT",            "Keppel REIT"),
    "AJBU.SI": ("REIT",        "DataCentre", "Data Centre REIT",       "Keppel DC REIT"),
    "A7RU.SI": ("REIT",        "Infra",      "Infrastructure Trust",   "Keppel Infrastructure Trust"),
    "AU8U.SI": ("REIT",        "China",      "China REIT",             "CapitaLand China Trust"),
    "HMN.SI":  ("REIT",        "Hospitality","Hospitality REIT",       "CapitaLand Ascott Trust"),
    "SK6U.SI": ("REIT",        "Healthcare", "Healthcare REIT",        "Parkway Life REIT"),
    "CWBU.SI": ("REIT",        "Infra",      "Infrastructure Trust",   "NetLink NBN Trust"),
    "J91U.SI": ("REIT",        "Industrial", "Industrial REIT",        "ESR-LOGOS REIT"),
    "CY6U.SI": ("REIT",        "India",      "India REIT",             "CapitaLand India Trust"),
    "OXMU.SI": ("REIT",        "US Office",  "US Office REIT",         "Prime US REIT"),
    "8C8U.SI": ("REIT",        "Accommodation", "Accommodation REIT",  "Centurion Accommodation REIT"),
    "CMOU.SI": ("REIT",        "Hospitality","Hospitality REIT",       "CDL Hospitality Trusts"),
    "P40U.SI": ("REIT",        "Retail",     "Retail REIT",            "Starhill Global REIT"),
    "Q5T.SI":  ("REIT",        "Hospitality","Hospitality REIT",       "Far East Hospitality Trust"),
    "TS0U.SI": ("REIT",        "Commercial", "Commercial REIT",        "OUE Commercial REIT"),
    "D8DU.SI": ("REIT",        "DataCentre", "Data Centre REIT",       "Digital Core REIT"),
    "RW0U.SI": ("REIT",        "European",   "European REIT",          "Cromwell European REIT"),
    "CRPU.SI": ("REIT",        "Outlet",     "Outlet Mall REIT",       "Sasseur REIT"),
    "JYEU.SI": ("REIT",        "Commercial", "Commercial REIT",        "Lendlease Global Commercial REIT"),
    "BTOU.SI": ("REIT",        "USOffice",   "US Office REIT",         "Manulife US REIT"),
}
