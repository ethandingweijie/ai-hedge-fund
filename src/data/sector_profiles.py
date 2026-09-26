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

import logging
import statistics

_log = logging.getLogger(__name__)

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
    # Wave 2. Damodaran Jan 2026 (file read 2026-09-21): Electrical Equipment
    # 8.99%, D/(D+E)=10.7%. NOT Green & Renewable Energy (6.04%, 53% debt):
    # that row is generators, and a manufacturer does not carry their leverage.
    "Clean Tech / Power Equipment OEM": 0.090,
    "EPC Contractor":    0.087,  # project execution risk; Damo Eng/Construction 8.69%
    # Wave 1 oil & gas (owner-approved 2026-09-20). Damodaran Jan 2026:
    #   Oil/Gas Distribution 5.78% D/(D+E)=36.9%; Oilfield Svcs/Equip. 7.04%
    #   D/(D+E)=27.2%. Damodaran publishes no refining row: refiners take the
    #   Resources sector rate (7.0%) the commodity producers use.
    "Midstream / Pipelines":        0.058,
    "Refining & Marketing":         0.070,
    "Oilfield Services & Drilling": 0.070,
}

# Leverage premium caps by Energy sub-type.
# Regulated/PPA-backed utilities carry structural leverage (high D/(D+E)) as part of
# their capital model — cap the incremental premium tightly to avoid double-counting.
_ENERGY_LEVERAGE_CAP: dict[str, float] = {
    "Regulated Utility": 0.015,  # regulatory oversight limits excess risk; tight cap
    "IPP":               0.020,  # PPA visibility compresses the max addendum
    "Merchant Power":    0.035,  # full commodity exposure → wider cap
    "EPC Contractor":    0.030,
    "Midstream / Pipelines":        0.020,  # structural leverage (D/(D+E) 37%), fee-based
    "Refining & Marketing":         0.035,  # full crack-spread exposure
    "Oilfield Services & Drilling": 0.035,  # full activity-cycle exposure
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

# ── Per-profile base WACC, by sector ─────────────────────────────────────────
#
# The one registry both WACC functions consult. A sector listed here prices a
# recognised profile at its own rate; an unrecognised profile, and every sector
# not listed, falls back to the flat SECTOR_WACC rate. The Energy and Financials
# entries ARE the two tables above (same objects, not copies), so the older
# names stay valid and cannot drift. A new sector is one entry here plus its
# rate table -- no new branch in get_wacc or wacc_base_breakdown.
#
# Each sector carries (rates, leverage caps, default cap for a profile with no
# cap of its own).
# Wave 3 (2026-09-22). Damodaran January 2026: Aerospace/Defense 7.24%,
# D/(D+E) 12.6%; Electrical Equipment 8.99% is the nearest row for a pre-profit
# hardware maker (no space row exists). The leverage premium adds TransDigm's
# and Boeing's debt on top; the base is the industry's.
_INDUSTRIALS_PROFILE_WACC: dict[str, float] = {
    "Defense Primes":                 0.072,
    "Commercial Aerospace & Engines": 0.072,
    "Niche Aerospace Components":     0.072,
    "Defense Tech & Space":           0.090,
}

_PROFILE_WACC: dict[str, dict[str, float]] = {
    "Energy":      _ENERGY_PROFILE_WACC,
    "Financials":  _FINANCIALS_PROFILE_WACC,
    "Industrials": _INDUSTRIALS_PROFILE_WACC,
}
_PROFILE_LEVERAGE_CAP: dict[str, tuple[dict[str, float], float]] = {
    "Energy":      (_ENERGY_LEVERAGE_CAP, 0.035),
    "Financials":  (_FINANCIALS_LEVERAGE_CAP, 0.010),
    "Industrials": ({}, 0.040),
}
_DEFAULT_LEVERAGE_CAP = 0.040


def _profile_wacc_rate(sector: str, profile: str) -> "tuple[float, float] | None":
    """(base rate, leverage cap) for a profile with its own rate, else None."""
    rates = _PROFILE_WACC.get(sector)
    if not rates or profile not in rates:
        return None
    caps, default_cap = _PROFILE_LEVERAGE_CAP.get(sector, ({}, _DEFAULT_LEVERAGE_CAP))
    return rates[profile], caps.get(profile, default_cap)


# ── Balance-sheet financials: which profiles may carry an EV or DCF leg ──────
#
# An enterprise value is (market cap + debt − cash). For a business whose
# liabilities ARE its product — a deposit book, a customer float, a margin
# obligation — that subtraction removes the thing being valued and leaves a
# number with no interpretation. The engine nonetheless computed one:
# 02888.HK (Standard Chartered, profile "Money Center Bank") published a
# forward EV/EBITDA of HK$730 per share, and MU's $6,105 came from a peak peer
# multiple applied to a peak consensus EPS. Neither was caught by any test.
#
# Two tiers, because "financial" is not one thing:
#
#   Tier 1 — ZERO EV/DCF, unconditionally. The balance sheet is the business.
#   Tier 1 — ALLOWED unless the Tier 2 ratio fires. Fee-based by profile, but a
#            name routed here may still be deposit- or float-funded in fact.
#
# Tier 2 is the measured test (see _is_balance_sheet_financial in dcf_agent).
# Classification below is by profile NAME, so a ticker routed to the wrong
# profile is not protected — which is the routing problem, not this one.
#
#: Tier 1, zero EV/DCF. Every bank variant, insurance, and the two profiles
#: whose liabilities are customer money held against a trading or custody book.
BALANCE_SHEET_FINANCIAL_PROFILES: frozenset[str] = frozenset({
    # Banks — all variants. Their profiles already carry no EV/DCF leg (GGM,
    # Residual Income, P/TBV, P/E (norm), Excess Capital), so the strip is a
    # no-op for them TODAY; they are listed so that adding an EV leg to a bank
    # profile later cannot silently reintroduce the defect.
    "Money Center Bank", "Money Center Bank (EU)", "Money Center Bank (SG)",
    "Regional Bank", "Super-Regional Bank",
    "EM Bank", "EM Bank (Premium)",
    "Bank / Lending Institution",
    "Investment Bank", "Neo/Challenger", "Mortgage/GSE",
    # Insurance — float is the funding.
    "Insurance", "Insurance (P&C)",
    # Holdco — SOTP/NAV is the instrument; an EV multiple double-counts the
    # subsidiaries' own debt.
    "Holding Company",
    # Brokerage. This is the one that MOVES numbers: SCHW's profile carries
    # DCF at 0.20 and FCF Yield at 0.20, and its customer balances are 1.03x
    # total assets (US$397.8bn of payables + US$107.6bn of receivables on
    # US$491.0bn of assets, FY2025). Reported FCF there is a deposit-flow
    # artefact, not cash available to equity.
    "Brokerage",
    "WealthTech & Specialty Financials (SG)",
})

#: Tier 1, allowed unless the Tier 2 customer-balance ratio fires. Fee-based by
#: profile, so the strip is conditional on measurement rather than on the name.
BALANCE_SHEET_FINANCIAL_CONDITIONAL_PROFILES: frozenset[str] = frozenset({
    "Asset Manager", "Alt Asset Manager",
    "Payment Networks",
    "Market Infrastructure", "Market Infrastructure (SG)",
    "FinTech", "Fintech/Stablecoin",
})

#: Exempt from Tier 2 even though they sit in the conditional set.
#:
#: Clearing houses and exchanges hold margin and guaranty-fund collateral that
#: is NOT their funding — it is passed through, and it appears as matching
#: current assets and current liabilities. Measured live 2026-09-17 against the
#: four names in this class, the exemption is load-bearing for two of them:
#:
#:     ICE     0.613  FIRES   otherCurrentLiabilities US$76.9bn against
#:                            totalCurrentAssets US$85.8bn ≈
#:                            totalCurrentLiabilities US$84.1bn — the textbook
#:                            pass-through match
#:     S68.SI  0.474  FIRES   but for a DIFFERENT reason than ICE: SGX's
#:                            current liabilities are only S$0.64bn. It trips on
#:                            receivables+payables against a small asset base,
#:                            i.e. ratio SHAPE, not deposit funding
#:     CME     0.004  quiet   FMP does not break out CME's collateral at all
#:     0388.HK 0.208  quiet   HK$68.0bn receivables on HK$580.8bn of assets
#:
#: So the exemption is right for ICE exactly as reasoned, and right for S68.SI
#: by a different route. Both are recorded because the distinction decides what
#: to do if a THIRD exchange trips it later: ICE's case is structural and should
#: stay exempt, S68.SI's is an artefact of a small denominator and would need
#: looking at rather than exempting.
TIER2_EXEMPT_PROFILES: frozenset[str] = frozenset({
    "Market Infrastructure", "Market Infrastructure (SG)",
})

#: Financials profiles deliberately in NEITHER tier, with the reason. A new
#: Financials profile that is not classified anywhere fails
#: tests/test_balance_sheet_financial_gate.py — the classification is a
#: decision, not a default.
BALANCE_SHEET_FINANCIAL_UNCLASSIFIED: dict[str, str] = {
    # P/E (norm) + SOTP (published) + DDM. No EV, DCF or FCF-Yield leg exists to
    # strip, so membership would change nothing — and putting it in the zero tier
    # would assert a fact about CapitaLand Investment's balance sheet that this
    # file has not checked.
    "Real Estate Asset Manager (SG)": "no EV/DCF/FCF leg in the profile; strip would be a no-op",
}


# ──────────────────────────────────────────────────────────────────────────────
# Capital-turnover classification (growth-reinvestment charge)
# ──────────────────────────────────────────────────────────────────────────────
#
#: The ONLY profiles on which `_reinvestment_margin_deduction` may be levied.
#: A positive allowlist, not an exclusion of the financials set above, and the
#: difference is the whole point: `BALANCE_SHEET_FINANCIAL_PROFILES` names the
#: profiles whose liabilities are their product, which covers a broker and a bank
#: but NOT a regulated utility, NOT an S-REIT and NOT a conglomerate holding
#: company. Excluding only the financials set would have left the single worst
#: ratio in the golden basket in scope.
#:
#: The test the ratio has to pass is whether `revenue ÷ invested capital` is a
#: SALES-TO-CAPITAL ratio at all — i.e. whether a dollar of incremental revenue
#: really does require a dollar of incremental invested capital underneath it, so
#: that `ΔRev/(S/C)` is the reinvestment that growth consumes. For a balance-sheet
#: intermediary the denominator is a deposit or custody book; for a utility or IPP
#: it is a regulated rate base whose return is set by a regulator and not by
#: turnover; for a property vehicle it is a real-estate asset base whose "turnover"
#: is a capitalisation rate wearing a ratio's clothes. In each case the identity
#: `ΔRev/(S/C)` computes a number with no interpretation.
#:
#: Owner-specified 2026-09-18, verbatim: "Revenue / Invested Capital is economic
#: nonsense for balance-sheet financial intermediaries (SCHW), regulated
#: utilities/IPPs (U96.SI), and real estate asset bases (C38U.SI, where capital
#: turnover is ~0.06). Restrict `_reinvestment_margin_deduction` strictly to
#: `Apparel / Athletic Wear`, `Consumer Growth`, `Hyper-Growth Platform`, and
#: `Capital Goods / Hardware`."
#:
#: ── ONE OWNER-SPECIFIED NAME DOES NOT EXIST, AND HOW IT WAS READ ─────────────
#: `Capital Goods / Hardware` is NOT a profile name. `INDUSTRY_VALUATION_PROFILES`
#: has 98 distinct profile names (checked programmatically, not by eye) and that
#: string is not one of them; the other three ARE, verbatim. Two existing names
#: are what the string decomposes into, so both are listed and the reading is
#: recorded here rather than resolved silently — striking either line is the whole
#: change if the narrower reading was meant:
#:     "Capital Goods"                            exists
#:     "Consumer Electronics / Hardware Ecosystem" exists
#: Note the taxonomy uses "/" INSIDE single profile names ("Apparel / Athletic
#: Wear", "Conglomerate / Industrial (SG)", "Tech Manufacturing / EMS (SG)"), so
#: the slash in the owner's string is not by itself evidence of one name or two.
#: Neither candidate is a golden fixture profile, so the choice has ZERO effect on
#: the measured baseline either way.
#:
#: ── MEASURED, one subprocess per fixture at `967a3c5` ────────────────────────
#: (a shared process leaks ~ten process-lifetime caches and once reported BN4_SI
#: at +26.54% where the true figure is +0.00%). `raw` is the uncapped charge,
#: `headroom` is `fcf_margin_base − fcf_floor`, and `cap` is the owner's rationing
#: bound. Sorted by S/C, which spans 175x — and the span is the argument: the
#: ratio is not comparable across these profiles because it is not measuring the
#: same thing.
#:
#:   fixture   profile                              S/C      raw  headroom  cap
#:   C38U_SI   S-REIT                            0.0625  +98.04%   +50.83%  YES
#:   BN4_SI    Conglomerate / Industrial (SG)    0.2977  +16.00%    +7.79%  YES
#:   U96_SI    Conglomerate / Industrial (SG)    0.4102  +11.61%    +7.16%  YES
#:   02888_HK  Money Center Bank                 0.5009   +4.33%   +26.39%  no
#:   MU        Memory / DRAM-NAND                0.6250  +18.12%    +8.96%  YES
#:   SCHW      Brokerage                         0.8058  +16.19%   +11.53%  YES
#:   09988_HK  Hyperscaler / Tech Conglomerate   0.8952  +10.72%   +14.51%  no
#:   BABA      Hyperscaler / Tech Conglomerate   0.8956  +10.96%   +14.51%  no
#:   V         Payment Networks                  0.9318  +13.70%   +56.67%  no
#:   FCX       Mining (Major)                    0.9517  +12.94%    +5.39%  YES
#:   MELI      Hyper-Growth Platform             1.9968   +6.53%   +35.28%  no   <== IN SCOPE
#:   AAPL      Hyperscaler / Tech Conglomerate   2.7712   +4.62%   +30.25%  no
#:   COST      Membership / Subscription Retail 10.9116   +0.82%    +0.32%  YES
#:   D05_SI    Money Center Bank (SG)               n/a  unmeasurable — invested capital absent
#:
#: Exactly ONE of the 14 golden fixtures is in scope: MELI. So the scoping change
#: makes the charge inert on 13 of 14, and every name the owner cited is outside
#: it — SCHW (broker), U96_SI (utility/IPP holdco), C38U_SI (S-REIT, whose raw
#: +98.04% charge against a +55.83% margin is the reductio). It also removes the
#: two names on which wiring the charge live INVERTED the sign of the answer,
#: 09988_HK (+17.40%) and BABA (+15.60%): both are `Hyperscaler / Tech
#: Conglomerate`, which is not listed, so the perverse outcome where a more
#: conservative cash-flow assumption raised intrinsic value is no longer reachable
#: through this charge on the golden basket.
#:
#: TWO THINGS THIS TABLE SHOWS THAT WERE NOT EXPECTED, recorded rather than
#: smoothed over:
#:   * Once scoping lands, the cap binds on ZERO of the 14 fixtures — all seven
#:     names where it binds are out of scope. The cap is correct and stays, but on
#:     this basket it is inert, and saying otherwise would overstate what shipped.
#:     It binds on out-of-basket names: COST is the shape to keep in mind, a
#:     thin-margin retailer whose +0.82% raw charge exceeds its +0.32% headroom.
#:   * COST has the HIGHEST and most interpretable S/C in the basket (10.91 — it
#:     really does turn capital over) and is EXCLUDED by the allowlist, because
#:     `Membership / Subscription Retail` is not one of the four names given. That
#:     is conservative and costs a small well-behaved charge, but it is a
#:     consequence of the list and not of the economics, so it is written down.
#:
#: Classification is by profile NAME, so a ticker routed to the wrong profile is
#: charged on the wrong basis — the same caveat `BALANCE_SHEET_FINANCIAL_PROFILES`
#: carries, and the same routing problem rather than a classification one.
CAPITAL_TURNOVER_PROFILES: frozenset[str] = frozenset({
    "Apparel / Athletic Wear",
    "Consumer Growth",
    "Hyper-Growth Platform",
    # The two existing names the owner's "Capital Goods / Hardware" decomposes
    # into. See the block above — that exact string is not a profile name.
    "Capital Goods",
    "Consumer Electronics / Hardware Ecosystem",
})


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
    own = _profile_wacc_rate(sector, profile)
    if own is not None:
        base, lev_cap = own
    else:
        base    = SECTOR_WACC.get(sector, 0.090)
        lev_cap = _DEFAULT_LEVERAGE_CAP
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
    "Midstream / Pipelines":        "Industrial",
    "Refining & Marketing":         "Industrial",
    "Oilfield Services & Drilling": "Industrial",
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
        "WealthTech & Specialty Financials (SG)": {
            "methods": [
                {"name": "P/E (norm)",      "weight": 0.4, "anchor": True, "implementable": True},
                {"name": "SOTP (published)",            "weight": 0.35, "anchor": False, "implementable": True},
                {"name": "P/BV",            "weight": 0.25, "anchor": False, "implementable": True},
            ],
            "excluded": ['EV/EBITDA'],
            "rationale": "SG wealthtech / specialty financials (iFAST, Yangzijiang Financial, Credit Bureau Asia). Platform economics scale on assets under administration while any lending or investment arm is a separate book — hence P/AUA alongside a book-value leg. Primary metrics: AUA, net inflows, net revenue margin on AUA, cash and short-term investments, NPLs.",
        },
        "Market Infrastructure (SG)": {
            "methods": [
                {"name": "P/E (norm)",      "weight": 0.45, "anchor": True, "implementable": True},
                {"name": "EV/EBITDA",       "weight": 0.35, "anchor": False, "implementable": True},
                {"name": "DCF",             "weight": 0.2, "anchor": False, "implementable": True},
            ],
            "excluded": ['P/BV'],
            "rationale": "SG market infrastructure / exchange (SGX). Near-monopoly toll-road economics on a fixed cost base, so earnings multiples travel well and operating leverage is the swing factor. Primary metrics: SDAV, DDAV, clearing fee per contract, operating margin.",
        },
        "Real Estate Asset Manager (SG)": {
            "methods": [
                {"name": "P/E (norm)",          "weight": 0.4, "anchor": True, "implementable": True},
                {"name": "SOTP (published)",    "weight": 0.35, "anchor": False, "implementable": True},
                {"name": "DDM",                 "weight": 0.25, "anchor": False, "implementable": True},
            ],
            "excluded": ['P/BV', 'EV/EBITDA'],
            "rationale": "SG real estate asset manager (CapitaLand Investment). SOTP-anchored: recurring fee-related earnings capitalise at a different multiple from the balance-sheet co-investments they sit beside. Primary metrics: FRE, FUM, net gearing, investment-property fair value.",
        },
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
        "Asset Manager": {
            "methods": [
                {"name": "P/E (norm)",      "weight": 0.35, "anchor": True,  "implementable": True},
                {"name": "Forward P/E",     "weight": 0.25, "anchor": False, "implementable": True},
                {"name": "FCF Yield",       "weight": 0.25, "anchor": False, "implementable": True},
                {"name": "EV/EBITDA",       "weight": 0.15, "anchor": False, "implementable": True},
            ],
            "excluded": ["P/BV", "Residual Income"],
            "rationale": (
                "Traditional asset managers (BLK, TROW, AB, AMG) — fee streams driven by "
                "AUM × fee rate, asset-light with high FCF conversion. Valued on normalised "
                "P/E (fee earnings are cyclical with markets) and FCF yield, NOT bank-style "
                "P/BV or Residual Income (no regulatory capital constraint). Distinct from "
                "'Alt Asset Manager' (BX/APO/KKR) which carries fee-related-earnings "
                "complexity and higher multiples."
            ),
        },
        "Fintech/Stablecoin": {
            "methods": [
                {"name": "Forward P/E",     "weight": 0.30, "anchor": True,  "implementable": True},
                {"name": "EV/EBITDA",       "weight": 0.30, "anchor": False, "implementable": True},
                {"name": "EV/Revenue",      "weight": 0.25, "anchor": False, "implementable": True},
                {"name": "P/E (norm)",      "weight": 0.15, "anchor": False, "implementable": True},
            ],
            "excluded": ["P/BV", "DDM"],
            "rationale": (
                "Stablecoin issuers (CRCL) earn reserve income on the backing assets of "
                "their circulating stablecoins — revenue scales with circulation × short "
                "rates, so earnings are rate-sensitive but high-margin and asset-light. "
                "Valued as a growth fintech on forward P/E and EV/EBITDA rather than bank "
                "P/BV (no loan book, no regulatory capital). EV/Revenue captures the "
                "circulation-growth optionality; normalised P/E guards against rate-cycle "
                "peak earnings."
            ),
        },
        "Bank / Lending Institution": {
            "methods": [
                {"name": "GGM (P/B)",       "weight": 0.35, "anchor": True,  "implementable": True},
                {"name": "Residual Income", "weight": 0.30, "anchor": False, "implementable": True},
                {"name": "P/TBV",           "weight": 0.20, "anchor": False, "implementable": True},
                {"name": "P/E (norm)",      "weight": 0.10, "anchor": False, "implementable": True},
                {"name": "Excess Capital",  "weight": 0.05, "anchor": False, "implementable": True},
            ],
            "excluded": ["DCF", "P/BV", "ROE vs CoE"],  # GGM (P/B) supersedes the ROE-vs-CoE stub
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
                {"name": "GGM (P/B)",       "weight": 0.35, "anchor": True,  "implementable": True},
                {"name": "Residual Income", "weight": 0.30, "anchor": False, "implementable": True},
                {"name": "P/TBV",           "weight": 0.20, "anchor": False, "implementable": True},
                {"name": "P/E (norm)",      "weight": 0.10, "anchor": False, "implementable": True},
                {"name": "Excess Capital",  "weight": 0.05, "anchor": False, "implementable": True},
            ],
            "excluded": ["DCF", "P/BV", "ROE vs CoE"],  # GGM (P/B) supersedes the ROE-vs-CoE stub
            "rationale": "US GSIB Money Center bank (JPM/BAC/C/WFC). Target ROE 12%, CoE 9%, P/TBV 1.4x, CET1 target 12%.",
        },
        "Money Center Bank (EU)": {
            "methods": [
                {"name": "GGM (P/B)",       "weight": 0.35, "anchor": True,  "implementable": True},
                {"name": "Residual Income", "weight": 0.30, "anchor": False, "implementable": True},
                {"name": "P/TBV",           "weight": 0.20, "anchor": False, "implementable": True},
                {"name": "P/E (norm)",      "weight": 0.10, "anchor": False, "implementable": True},
                {"name": "Excess Capital",  "weight": 0.05, "anchor": False, "implementable": True},
            ],
            "excluded": ["DCF", "P/BV", "ROE vs CoE"],  # GGM (P/B) supersedes the ROE-vs-CoE stub
            "rationale": "European Money Center (HSBC/Barclays/DB) — higher CoE (11%) and CET1 target (14%) from regulatory drag; lower target ROE (10%) and P/TBV (0.8x).",
        },
        # Singapore money-center banks — DBS (D05.SI), OCBC (O39.SI),
        # UOB (U11.SI). Distinct from the US GSIB row: structurally higher
        # sustainable ROE (13-17% on a low-cost CASA base and a fee-heavy
        # wealth franchise) against a LOWER cost of equity than US peers
        # (AAA sovereign, MAS supervision, SGD funding). GGM carries a
        # heavier weight here because it is the method SG bank desks
        # actually publish.
        "Money Center Bank (SG)": {
            "methods": [
                {"name": "GGM (P/B)",       "weight": 0.45, "anchor": True,  "implementable": True},
                {"name": "Residual Income", "weight": 0.30, "anchor": False, "implementable": True},
                {"name": "P/E (norm)",      "weight": 0.15, "anchor": False, "implementable": True},
                {"name": "Excess Capital",  "weight": 0.10, "anchor": False, "implementable": True},
            ],
            "excluded": ["DCF", "P/BV", "ROE vs CoE", "P/TBV"],
            "rationale": "Singapore money-center bank (DBS/OCBC/UOB). GGM-anchored: target P/B = (ROE-g)/(CoE-g) on Rf 2.5% + ERP 6.3%. CoE 8.6-9.1%, g 3.0-3.3%, CET1 target 14%. P/TBV excluded — GGM P/B supersedes it and SG banks carry minimal goodwill.",
        },
        "Regional Bank": {
            "methods": [
                {"name": "GGM (P/B)",       "weight": 0.35, "anchor": True,  "implementable": True},
                {"name": "Residual Income", "weight": 0.30, "anchor": False, "implementable": True},
                {"name": "P/TBV",           "weight": 0.20, "anchor": False, "implementable": True},
                {"name": "P/E (norm)",      "weight": 0.10, "anchor": False, "implementable": True},
                {"name": "Excess Capital",  "weight": 0.05, "anchor": False, "implementable": True},
            ],
            "excluded": ["DCF", "P/BV", "ROE vs CoE"],  # GGM (P/B) supersedes the ROE-vs-CoE stub
            "rationale": "US Regional (USB/TFC/PNC). Target ROE 11%, CoE 10%, P/TBV 1.2x, CET1 11%. Higher RWA density (0.70x assets) for CRE-heavy books.",
        },
        "Super-Regional Bank": {
            "methods": [
                {"name": "GGM (P/B)",       "weight": 0.35, "anchor": True,  "implementable": True},
                {"name": "Residual Income", "weight": 0.30, "anchor": False, "implementable": True},
                {"name": "P/TBV",           "weight": 0.20, "anchor": False, "implementable": True},
                {"name": "P/E (norm)",      "weight": 0.10, "anchor": False, "implementable": True},
                {"name": "Excess Capital",  "weight": 0.05, "anchor": False, "implementable": True},
            ],
            "excluded": ["DCF", "P/BV", "ROE vs CoE"],  # GGM (P/B) supersedes the ROE-vs-CoE stub
            "rationale": "Super-regional (TD/BMO/RBC). Canadian Big-Six scale + diversification. CoE 9.5%, P/TBV 1.3x.",
        },
        "EM Bank": {
            "methods": [
                {"name": "GGM (P/B)",       "weight": 0.35, "anchor": True,  "implementable": True},
                {"name": "Residual Income", "weight": 0.30, "anchor": False, "implementable": True},
                {"name": "P/TBV",           "weight": 0.20, "anchor": False, "implementable": True},
                {"name": "P/E (norm)",      "weight": 0.10, "anchor": False, "implementable": True},
                {"name": "Excess Capital",  "weight": 0.05, "anchor": False, "implementable": True},
            ],
            "excluded": ["DCF", "P/BV", "ROE vs CoE"],  # GGM (P/B) supersedes the ROE-vs-CoE stub
            "rationale": "EM SOE banks (ICBC/CCB/BOC/ABC). Target ROE 14% (high NIM), CoE 13% (national-service risk premium), P/TBV 1.2x, CET1 10.5%.",
        },
        "EM Bank (Premium)": {
            "methods": [
                {"name": "GGM (P/B)",       "weight": 0.35, "anchor": True,  "implementable": True},
                {"name": "Residual Income", "weight": 0.30, "anchor": False, "implementable": True},
                {"name": "P/TBV",           "weight": 0.20, "anchor": False, "implementable": True},
                {"name": "P/E (norm)",      "weight": 0.10, "anchor": False, "implementable": True},
                {"name": "Excess Capital",  "weight": 0.05, "anchor": False, "implementable": True},
            ],
            "excluded": ["DCF", "P/BV", "ROE vs CoE"],  # GGM (P/B) supersedes the ROE-vs-CoE stub
            "rationale": "EM Premium — India private banks (HDFC/ICICI/Kotak) sustain 16-18% ROE on credit-to-GDP gap. 7-year fade, P/TBV 2.0x.",
        },
        "Investment Bank": {
            "methods": [
                {"name": "GGM (P/B)",       "weight": 0.35, "anchor": True,  "implementable": True},
                {"name": "Residual Income", "weight": 0.30, "anchor": False, "implementable": True},
                {"name": "P/TBV",           "weight": 0.20, "anchor": False, "implementable": True},
                {"name": "P/E (norm)",      "weight": 0.10, "anchor": False, "implementable": True},
                {"name": "Excess Capital",  "weight": 0.05, "anchor": False, "implementable": True},
            ],
            "excluded": ["DCF", "P/BV", "ROE vs CoE"],  # GGM (P/B) supersedes the ROE-vs-CoE stub
            "rationale": "Investment Bank (GS/MS). Target ROE 13% (cyclical), CoE 11% (trading VaR premium), P/TBV 1.2x, RWA proxy 0.40x (market-risk-weighted).",
        },
        "Neo/Challenger": {
            "methods": [
                {"name": "GGM (P/B)",       "weight": 0.35, "anchor": True,  "implementable": True},
                {"name": "Residual Income", "weight": 0.30, "anchor": False, "implementable": True},
                {"name": "P/TBV",           "weight": 0.20, "anchor": False, "implementable": True},
                {"name": "P/E (norm)",      "weight": 0.10, "anchor": False, "implementable": True},
                {"name": "Excess Capital",  "weight": 0.05, "anchor": False, "implementable": True},
            ],
            "excluded": ["DCF", "P/BV", "ROE vs CoE"],  # GGM (P/B) supersedes the ROE-vs-CoE stub
            "rationale": "Neo/Challenger (NU/SOFI) — J-curve ROE. Target 18%, P/TBV 2.8x, 10-year fade (extended because current ROE still ramping).",
        },
        "Insurance (P&C)": {
            "methods": [
                # EMBEDDED VALUE IS A LIFE CONCEPT. It discounts the in-force
                # book of long-duration policies; a general insurer writes
                # one-year contracts and has no in-force value to discount.
                # Routing P&C through the life profile anchored PICC on
                # Embedded Value, which does not exist for it. A general
                # insurer is priced on book value against the return it earns
                # on that book -- underwriting result plus investment yield.
                {"name": "GGM (P/B)",           "weight": 0.40, "anchor": True,  "implementable": True},
                {"name": "Combined Ratio Gate", "weight": 0.25, "anchor": False, "implementable": True},
                {"name": "P/E (ops)",           "weight": 0.20, "anchor": False, "implementable": True},
                {"name": "DDM",                 "weight": 0.15, "anchor": False, "implementable": True},
            ],
            "excluded": ["Embedded Value"],
            "rationale": (
                "P/B against ROE is the general-insurance anchor: the balance "
                "sheet is the asset and the return on it comes from "
                "underwriting margin plus investment yield. The combined ratio "
                "gate carries the underwriting quality that separates a "
                "disciplined book from a bought one -- below 100 the insurer "
                "earns before investing a dollar, above it the float has to "
                "pay for the underwriting. Embedded Value is EXCLUDED rather "
                "than merely unweighted: it has no meaning for one-year "
                "contracts and its presence invited the wrong anchor."
            ),
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
                # Wave 2 (owner-confirmed 2026-09-21). A free-cash-flow DCF cannot
                # anchor a utility that is growing its rate base: capex exceeds
                # operating cash flow every year by design, and the 0.60 DCF leg
                # dropped as non-positive on NEE, DUK and SO alike. The anchor is
                # what the regulator sets. P/Rate Base prices as P/BV until a rate
                # base is accepted (dcf_agent._PER_TICKER_METHODS), so its weight
                # is never lost. "Utility P/E" was EBITDA x EV/EBITDA under a P/E
                # label; it is now a P/E.
                #
                # The ANCHOR FLAG sits on P/E, not on the heaviest leg. Industry
                # routing declines a profile whose anchor resolves to a proxy
                # (dcf_agent: declined_anchor_not_implementable) and that guard is
                # right: with the flag on P/Rate Base, CLP (00002.HK) was refused
                # the profile and fell back to Mature SaaS. P/Rate Base is exact
                # only for a ticker with an accepted rate base; P/E is computable
                # for every utility. The weights are the owner's, unchanged.
                {"name": "P/Rate Base",   "weight": 0.35, "anchor": False, "implementable": False, "proxy": "P/BV"},
                {"name": "P/E",           "weight": 0.30, "anchor": True,  "implementable": True},
                {"name": "DDM",           "weight": 0.25, "anchor": False, "implementable": True},
                {"name": "DCF",           "weight": 0.10, "anchor": False, "implementable": True},
            ],
            "excluded": [],
            "rationale": "Returns are set by the regulator on the equity layer of the rate base; earnings and the dividend follow from it. Free cash flow is structurally negative while the rate base grows.",
        },
        "Merchant Power": {
            "methods": [
                # Wave 2: LBO Floor was uncomputable on every name that carried it
                # (VST, CEG, NRG) and renormalised silently onto the others.
                # Forward P/E carries what trailing EBITDA cannot: the nuclear
                # PTC floor and the contracted data-centre load.
                {"name": "EV/EBITDA",       "weight": 0.45, "anchor": True,  "implementable": True},
                {"name": "FCF Yield",       "weight": 0.25, "anchor": False, "implementable": True},
                {"name": "Forward P/E",     "weight": 0.15, "anchor": False, "implementable": True},
                {"name": "Power Price DCF", "weight": 0.15, "anchor": False, "implementable": True,  "note": "proxied by DCF"},
            ],
            "excluded": [],
            "rationale": "High operational leverage and cyclical commodity prices necessitate EBITDA and FCF focus.",
        },
        "IPP": {
            "methods": [
                # Wave 2: "NAV (Project)" was P/BV at 0.30 under a NAV label. It is
                # named for what it computes and carries less.
                {"name": "PPA-backed DCF", "weight": 0.40, "anchor": True,  "implementable": True,  "note": "proxied by DCF"},
                {"name": "EV/EBITDA",      "weight": 0.35, "anchor": False, "implementable": True},
                {"name": "P/BV",           "weight": 0.15, "anchor": False, "implementable": True},
                {"name": "DDM",            "weight": 0.10, "anchor": False, "implementable": True},
            ],
            "excluded": [],
            "rationale": "Long-term contracts (PPAs) provide visibility for project-level cash flow modeling.",
        },
        # Wave 2 (owner, 2026-09-21). Solar, wind and fuel-cell HARDWARE makers:
        # Enphase, First Solar, Nextracker, Bloom. They had been pinned to IPP
        # and priced on a PPA-backed DCF, a generator's method. The owner's test
        # is the cost structure, not the end market: 20-28% gross margins,
        # manufacturing capex, inventory and warranty reserves -- an OEM, whether
        # the product feeds a utility-scale array or a data centre behind the
        # meter. Not a licensor: grouping Bloom with IP licensors "introduces
        # multiple inflation and distorts return-on-capital benchmarks".
        # Cyclical (dcf_agent._CYCLICAL_PROFILES): the policy cycle moves the
        # whole basket's margins together, so the anchor is through-cycle.
        "Clean Tech / Power Equipment OEM": {
            "methods": [
                {"name": "EV/EBITDA (norm)", "weight": 0.35, "anchor": True,  "implementable": True},
                {"name": "Forward P/E",      "weight": 0.25, "anchor": False, "implementable": True},
                {"name": "DCF",              "weight": 0.25, "anchor": False, "implementable": True},
                {"name": "EV/Revenue",       "weight": 0.15, "anchor": False, "implementable": True},
            ],
            "excluded": ["PPA-backed DCF", "Licensing NPV"],
            "rationale": "Hardware OEM economics: through-cycle margins on current revenue, forward earnings, and a DCF; never a generator's contracted-cash-flow model and never a licensor's multiple.",
        },
        "Midstream / Pipelines": {
            "methods": [
                {"name": "EV/EBITDA",               "weight": 0.45, "anchor": True,  "implementable": True},
                {"name": "Distributable CF Yield",  "weight": 0.25, "anchor": False, "implementable": True,
                 "note": "(OCF - maintenance capex) / required yield; maintenance capex = D&A until reported values are accepted"},
                {"name": "DDM",                     "weight": 0.20, "anchor": False, "implementable": True},
                {"name": "DCF",                     "weight": 0.10, "anchor": False, "implementable": True},
            ],
            "excluded": [],
            "rationale": (
                "Fee-based pipelines and processing: volume-driven, largely contracted "
                "EBITDA, valued on EV/EBITDA and on the cash that can be distributed "
                "after sustaining the asset base."
            ),
            "data_limitation": (
                "Maintenance capex is not reported by FMP; D&A stands in, flagged on "
                "the leg, until a cited reported figure is accepted."
            ),
        },
        "Refining & Marketing": {
            "methods": [
                # Owner, 2026-09-20: the segment SOTP on through-cycle EV/EBITDA
                # bands per business type is the better instrument. Declared
                # here since 2026-09-26 (the data-driven promotion retired) at
                # the 0.40 it was promoted at; the other five keep their
                # ratios. Anchor stays on mid-cycle EV/EBITDA so a filing
                # without a segment note still has its anchor in the blend.
                {"name": "SOTP (segments)",   "weight": 0.40, "anchor": False, "implementable": True},
                {"name": "EV/EBITDA (norm)",  "weight": 0.24, "anchor": True,  "implementable": True, "note": "mid-cycle"},
                {"name": "Forward EV/EBITDA", "weight": 0.12, "anchor": False, "implementable": True,
                 "note": "the current crack-spread cycle, partially"},
                {"name": "FCF Yield",         "weight": 0.12, "anchor": False, "implementable": True},
                {"name": "P/BV",              "weight": 0.06, "anchor": False, "implementable": True, "note": "replacement-cost floor"},
                {"name": "P/E (norm)",        "weight": 0.06, "anchor": False, "implementable": True},
            ],
            "excluded": [],
            "rationale": (
                "Refiners and fuel marketers earn the crack spread, which mean-reverts: "
                "mid-cycle EBITDA anchors, the asset book floors. A forward leg lets the "
                "current cycle count in part (owner, 2026-09-20: Valero sat 44% under "
                "consensus on the pure mid-cycle table while cracks ran above their "
                "5-year average)."
            ),
        },
        "Oilfield Services & Drilling": {
            "methods": [
                {"name": "EV/EBITDA (norm)", "weight": 0.50, "anchor": True,  "implementable": True, "note": "mid-cycle"},
                {"name": "P/BV",             "weight": 0.20, "anchor": False, "implementable": True, "note": "fleet / rig replacement floor"},
                {"name": "FCF Yield",        "weight": 0.20, "anchor": False, "implementable": True},
                {"name": "P/E (norm)",       "weight": 0.10, "anchor": False, "implementable": True},
            ],
            "excluded": [],
            "rationale": (
                "Activity follows upstream capex with a lag: mid-cycle EBITDA anchors; "
                "for drillers the fleet book value is the downside."
            ),
            "data_limitation": (
                "EV/Backlog not yet weighted: backlog arrives review-gated (SEC "
                "remaining performance obligations for US filers, cited Gemini "
                "pre-fill otherwise)."
            ),
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
        # GPU neoclouds and AI infrastructure — CoreWeave, Nebius, IREN,
        # Applied Digital, TeraWulf. Not a SaaS business and not a data-
        # centre REIT: revenue is contracted capacity sold forward, the
        # binding constraint is secured POWER rather than demand, and the
        # balance sheet is consumed by capex years ahead of the earnings.
        #
        # Priced on forward EV/Revenue because EBITDA is thin or negative
        # while the fleet is being built — Goldman values Nebius on "7x
        # 2HCY27+1HCY28E EV/Sales" (down from 9x CY27E), i.e. a blended
        # forward period rather than a trailing multiple. EV/EBITDA is kept
        # as a secondary for the names that have crossed into positive
        # EBITDA, and contributes nothing for the ones that have not.
        "AI Infrastructure / Neocloud": {
            "methods": [
                {"name": "EV/Revenue",   "weight": 0.45, "anchor": True,  "implementable": True},
                {"name": "DCF",          "weight": 0.30, "anchor": False, "implementable": True},
                {"name": "EV/EBITDA",    "weight": 0.25, "anchor": False, "implementable": True},
            ],
            "excluded": ["P/BV", "DDM"],
            "rationale": "GPU neocloud / AI infrastructure (CoreWeave, Nebius, IREN, Applied Digital). Forward EV/Revenue anchored — EBITDA is thin or negative during the build-out, so an earnings multiple prices nothing. Primary metrics: contracted power (GW), contracted revenue backlog, capex-to-revenue, gross margin. Secured power, not demand, is the constraint that caps revenue.",
        },
        # Singapore tech manufacturing / EMS — Venture, UMS, Frencken, AEM.
        # Order-driven and cyclical: priced on forward earnings through the
        # cycle with a DCF cross-check. Book-to-bill is the leading
        # indicator and turns before the P/E does.
        # A branded hardware ecosystem is not a contract manufacturer and not
        # a carmaker. Xiaomi sat in Automotive & EV, which priced a smartphone
        # company on auto comps; before that it was on a static US Consumer
        # table. Neither reads the actual business: hardware at thin margin,
        # an IoT attach, a high-margin internet-services layer, and an EV arm
        # that is the smallest of the four.
        #
        # Earnings anchor it, because hardware volume without margin is what
        # EV/Revenue mistakes for value. No sales multiple is declared at all
        # -- that leg is the one the Automotive & EV gate exists to remove.
        "Consumer Electronics / Hardware Ecosystem": {
            "methods": [
                {"name": "Forward P/E", "weight": 0.40,
                 "anchor": True, "implementable": True},
                {"name": "EV/EBITDA", "weight": 0.35,
                 "anchor": False, "implementable": True},
                {"name": "DCF", "weight": 0.15,
                 "anchor": False, "implementable": True},
                {"name": "FCF Yield", "weight": 0.10,
                 "anchor": False, "implementable": True},
            ],
            "excluded": ["EV/Revenue", "EV/NTM Revenue", "P/BV"],
            "rationale": (
                "Branded consumer hardware with a services attach (Xiaomi, "
                "Lenovo). Blended forward P/E and EV/EBITDA: earnings and "
                "cash operating profit, not top line. EV/Revenue is excluded "
                "outright -- a hardware ecosystem's revenue is deliberately "
                "low-margin to build the installed base the services layer "
                "monetises, so a sales multiple reads the loss-leader as "
                "value. A true SOTP would split hardware P/E, services P/E "
                "and the EV arm on EV/Sales; that needs segment economics "
                "the filings support but the parser does not yet map."),
        },
        # Meituan is not a hyperscaler. Its reportable segments are core
        # local commerce (food delivery, Instashopping, in-store, hotel and
        # travel) and new initiatives (Select, Xiaoxiang, Keeta, B2B supply) --
        # an on-demand commerce operator whose core is EBITDA-positive and
        # whose growth arm is deliberately loss-making.
        #
        # Analysts split it four ways -- delivery and Instashopping on
        # EV/Sales, in-store and travel on P/E or EV/EBITDA, retail/grocery,
        # then overseas and mobility. That split is NOT declared here: the
        # IFRS 8 note discloses two segments, not four, so a per-unit multiple
        # would be applied to numbers the filing does not publish. No
        # EV/Revenue leg either -- on the consolidated entity it would price
        # the loss-making growth arm's revenue as though it earned core
        # margins, which is the whole error the unbundling exists to avoid.
        # Owner, 2026-09-26. China internet platforms are conglomerates: a
        # core commerce or gaming business at 8-16x earnings beside cloud at
        # 4-7x sales and loss-making international, logistics or new-initiative
        # arms. The owner-accepted Gemini SOTP is the one leg that prices the
        # parts separately (0.35: the registry's weight for a SOTP that is one
        # method among several; the 0.25/0.35/0.45 sweep of 2026-09-26 could
        # not separate them). The earnings legs are NORMALISED: Meituan's NTM
        # EPS is 0.19 in the 2026 price war and a raw Forward P/E priced it at
        # HK$3.24. DCF anchors so a name whose SOTP is Degraded or pending
        # still has its anchor in the blend.
        "China Internet Platform": {
            "methods": [
                {"name": "DCF",              "weight": 0.30, "anchor": True,  "implementable": True},
                {"name": "SOTP (analyst)",   "weight": 0.35, "anchor": False, "implementable": True},
                {"name": "EV/EBITDA (norm)", "weight": 0.20, "anchor": False, "implementable": True},
                {"name": "P/E (norm)",       "weight": 0.15, "anchor": False, "implementable": True},
            ],
            "excluded": ["EV/Revenue", "EV/NTM Revenue", "P/BV", "Forward P/E"],
            "rationale": (
                "Sum-of-the-parts on owner-accepted, cited segment inputs, with a "
                "DCF anchor and through-cycle earnings multiples. The parts trade "
                "on different multiples and a single-multiple leg blends them; a "
                "raw forward P/E dies in a price war."),
        },
        "Local Services & Instant Retail": {
            "methods": [
                # NORMALISED, because the raw metric is negative in a price
                # war and the profile would then have no anchor at all.
                # Meituan's FY2025 EBITDA is -24.0bn against +41.5bn in FY2024
                # and +23.4bn in FY2023; raw EV/EBITDA and FCF Yield both
                # return None, which left the blend averaging a 177 DCF
                # against a 3.8 forward P/E and landing on a plausible number
                # by arithmetic accident.
                {"name": "EV/EBITDA (norm)", "weight": 0.40,
                 "anchor": True, "implementable": True},
                {"name": "DCF", "weight": 0.30,
                 "anchor": False, "implementable": True},
                {"name": "Forward P/E", "weight": 0.30,
                 "anchor": False, "implementable": True},
            ],
            "excluded": ["EV/Revenue", "EV/NTM Revenue", "P/BV", "FCF Yield"],
            "rationale": (
                "On-demand local commerce (Meituan). Core local commerce "
                "carries the earnings and new initiatives consumes them, so "
                "the company is priced on consolidated cash operating profit "
                "with an earnings cross-check. A true SOTP would value "
                "delivery, in-store/travel, grocery and overseas separately; "
                "it needs segment economics the two-segment IFRS 8 note does "
                "not disclose."),
        },
        # Kingboard Holdings reports five lines -- laminates, PCBs, chemicals,
        # properties, investments/others -- of which laminates IS a separate
        # listed company (1888.HK, 61.74% held). Specialty Chemicals priced
        # the whole group off one of its five divisions.
        #
        # SOTP anchors because the largest division has a traded price: the
        # look-through marks the Laminates stake at market and values the
        # remainder as a stub. Splitting that stub into PCB, chemicals and
        # property (RNAV / cap rate) is the right structure and is not done
        # here -- the HKEX segment note parses to revenue-only rows with
        # unusable labels, so a three-way split would be allocated rather than
        # read.
        "Electronic Materials & Industrial Diversified": {
            "methods": [
                {"name": "SOTP / NAV", "weight": 0.40, "anchor": True,
                 "implementable": False, "proxy": "P/BV"},
                {"name": "EV/EBITDA", "weight": 0.25,
                 "anchor": False, "implementable": True},
                {"name": "P/E", "weight": 0.20,
                 "anchor": False, "implementable": True},
                {"name": "P/BV", "weight": 0.15,
                 "anchor": False, "implementable": True},
            ],
            "excluded": ["EV/Revenue"],
            "rationale": (
                "Diversified electronic materials with a listed subsidiary "
                "(Kingboard Holdings / 1888.HK). SOTP anchors because the "
                "dominant division trades separately; P/BV carries the "
                "investment properties and landbank the other multiples "
                "cannot see. Marked unimplementable with a P/BV proxy so a "
                "name without a look-through template degrades to book rather "
                "than to nothing -- the per-ticker guard promotes the real "
                "method whenever the template completes."),
        },
        "Tech Manufacturing / EMS (SG)": {
            "methods": [
                {"name": "Forward P/E",  "weight": 0.45, "anchor": True,  "implementable": True},
                {"name": "DCF",          "weight": 0.35, "anchor": False, "implementable": True},
                {"name": "EV/EBITDA",    "weight": 0.20, "anchor": False, "implementable": True},
            ],
            "excluded": ["P/BV"],
            "rationale": "SG tech manufacturing / EMS (Venture, UMS, Frencken, AEM). Forward P/E anchored with a DCF cross-check. Primary metrics: forward P/E, book-to-bill, ROCE, gross margin. PEG is not declared — growth already sits inside the forward multiple and the DCF.",
        },
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
                {"name": "EPV",           "weight": 0.30, "anchor": True,  "implementable": True},
                {"name": "DCF (2-stage)", "weight": 0.25, "anchor": False, "implementable": True},
                {"name": "P/E",           "weight": 0.15, "anchor": False, "implementable": True},
                {"name": "EV/Revenue",    "weight": 0.15, "anchor": False, "implementable": True},
                {"name": "EV/EBITDA",     "weight": 0.10, "anchor": False, "implementable": True},
                {"name": "LBO Floor",     "weight": 0.05, "anchor": False, "implementable": True},
            ],
            "excluded": [],
            "rationale": "Earnings Power Value tests the sustainability of current earnings without growth assumptions.",
        },
        "High-Growth Tech / AI": {
            "methods": [
                {"name": "Reverse DCF",      "weight": 0.40, "anchor": True,  "implementable": True},
                {"name": "TAM Penetration",  "weight": 0.30, "anchor": False, "implementable": False, "proxy": "EV/Revenue"},
                {"name": "EV/NTM Rev",       "weight": 0.20, "anchor": False, "implementable": True},
                {"name": "SOTP (published)",             "weight": 0.10, "anchor": False, "implementable": False, "proxy": "EPV"},
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
                {"name": "DCF (FCF+)", "weight": 0.45, "anchor": True,  "implementable": True},
                {"name": "P/E",        "weight": 0.15, "anchor": False, "implementable": True},
                {"name": "EPV",        "weight": 0.15, "anchor": False, "implementable": True},
                {"name": "EV/EBITDA",  "weight": 0.15, "anchor": False, "implementable": True},
                {"name": "LBO Floor",  "weight": 0.10, "anchor": False, "implementable": True},
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
                {"name": "rNPV",         "weight": 0.45, "anchor": True,  "implementable": True},
                {"name": "EV/R&D",       "weight": 0.25, "anchor": False, "implementable": True},
                {"name": "Pipeline NAV", "weight": 0.20, "anchor": False, "implementable": False, "proxy": "P/BV"},
                {"name": "Cash Runway",  "weight": 0.10, "anchor": False, "implementable": True},
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

    # ── HEALTHCARE SERVICES ─────────────────────────────────────────────────
    # Distinct sector from Biopharma. Without this block, Tier-1 profile
    # verification (strategic_router) silently failed for every HealthcareServices
    # ticker — INDUSTRY_VALUATION_PROFILES had no "HealthcareServices" key, so
    # the lookup returned None and the ticker fell through to the sector default
    # ("Managed Care"), giving animal-health / providers / distributors insurer
    # KPIs. These sub-profile keys mirror SECTOR_KPI_FRAMEWORK exactly.
    "HealthcareServices": {
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
        "Healthcare Providers / Services": {
            "methods": [
                {"name": "EV/EBITDA",  "weight": 0.45, "anchor": True,  "implementable": True},
                {"name": "P/E (Ops)",  "weight": 0.30, "anchor": False, "implementable": True},
                {"name": "DCF",        "weight": 0.25, "anchor": False, "implementable": True},
            ],
            "excluded": ["EV/Revenue"],
            "rationale": (
                "Hospitals, dialysis, labs and care-delivery (HCA, THC, UHS, DVA, LH, DGX). "
                "Capital-intensive, leverage-sensitive — EV/EBITDA anchors; DCF captures "
                "same-facility volume + reimbursement. This is the SAFE GENERIC default for "
                "HealthcareServices names without a more specific sub-profile."
            ),
        },
        "Medical Devices": {
            "methods": [
                {"name": "EV/Revenue",   "weight": 0.40, "anchor": True,  "implementable": True},
                {"name": "P/E",          "weight": 0.30, "anchor": False, "implementable": True},
                {"name": "DCF (5-yr)",   "weight": 0.20, "anchor": False, "implementable": True},
                {"name": "ROIC vs WACC", "weight": 0.10, "anchor": False, "implementable": True},
            ],
            "excluded": [],
            "rationale": (
                "MedTech routed to HealthcareServices. High R&D + recurring consumables "
                "(razor-blade) drive premium revenue multiples and long-cycle growth."
            ),
        },
        "Animal Health": {
            "methods": [
                {"name": "P/E",        "weight": 0.40, "anchor": True,  "implementable": True},
                {"name": "EV/EBITDA",  "weight": 0.30, "anchor": False, "implementable": True},
                {"name": "DCF",        "weight": 0.30, "anchor": False, "implementable": True},
            ],
            "excluded": ["rNPV", "EV/R&D"],
            "rationale": (
                "Animal-health pharma & diagnostics (ZTS, IDXX, ELAN). High-margin, "
                "companion-animal-mix driven, with NO human clinical pipeline — so rNPV / "
                "EV/R&D (Biopharma anchors) are excluded; steady EPS + DCF apply."
            ),
        },
        "Pharma Distribution": {
            "methods": [
                {"name": "P/E (Ops)",  "weight": 0.40, "anchor": True,  "implementable": True},
                {"name": "EV/EBITDA",  "weight": 0.30, "anchor": False, "implementable": True},
                {"name": "FCF Yield",  "weight": 0.30, "anchor": False, "implementable": True},
            ],
            "excluded": ["EV/Revenue", "P/BV"],
            "rationale": (
                "Drug distributors (MCK, COR/Cencora, CAH). Razor-thin operating margins "
                "(~1-2%) on enormous revenue make EV/Revenue meaningless; P/E + FCF yield + "
                "ROIC anchor. Leverage and customer concentration are the key risk levers."
            ),
        },
    },

    # ── CONSUMER ──────────────────────────────────────────────────────────────
    "Consumer": {
        "Packaged Consumer & Lifestyle (SG)": {
            "methods": [
                {"name": "Forward P/E",     "weight": 0.45, "anchor": True, "implementable": True},
                {"name": "EV/EBITDA",       "weight": 0.35, "anchor": False, "implementable": True},
                {"name": "DCF",             "weight": 0.2, "anchor": False, "implementable": True},
            ],
            "excluded": ['P/BV'],
            "rationale": "SG packaged consumer goods and lifestyle brands (Food Empire, The Hour Glass, Cortina, Delfi). Brand and distribution rights are the moat; input costs and FX translation are the margin swing. Primary metrics: same-store sales growth, gross margin, raw material costs, FX translation impact.",
        },
        "Agribusiness & Food (SG)": {
            "methods": [
                {"name": "Forward P/E",     "weight": 0.45, "anchor": True, "implementable": True},
                {"name": "EV/EBITDA",       "weight": 0.35, "anchor": False, "implementable": True},
                {"name": "DCF",             "weight": 0.2, "anchor": False, "implementable": True},
            ],
            "excluded": ['P/BV'],
            "rationale": "SG agribusiness and consumer food (Wilmar, Thai Beverage, Olam, Golden Agri). Margin is set upstream by crush spreads and commodity prices rather than by pricing power. Primary metrics: crushing margin, CPO price and yield, same-store sales growth, gross margin.",
        },
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
                {"name": "EV/EBITDAR",   "weight": 0.40, "anchor": True,  "implementable": True},
                {"name": "P/E",          "weight": 0.25, "anchor": False, "implementable": True},
                {"name": "EV/Revenue",   "weight": 0.15, "anchor": False, "implementable": True, "note": "GMV-driven e-commerce"},
                {"name": "ROIC vs WACC", "weight": 0.10, "anchor": False, "implementable": True},
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
                {"name": "DCF",        "weight": 0.40, "anchor": True,  "implementable": True},
                {"name": "EV/Revenue", "weight": 0.25, "anchor": False, "implementable": True},
                {"name": "P/E",        "weight": 0.20, "anchor": False, "implementable": True},
                {"name": "EV/EBITDA",  "weight": 0.15, "anchor": False, "implementable": True},
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
        "Online Gaming / Sports Betting": {
            "methods": [
                {"name": "EV/EBITDA",    "weight": 0.40, "anchor": True,  "implementable": True},
                {"name": "EV/Revenue",   "weight": 0.30, "anchor": False, "implementable": True},
                {"name": "DCF",          "weight": 0.30, "anchor": False, "implementable": True},
            ],
            "excluded": ["P/BV", "DDM", "EPV"],
            "rationale": (
                "Online sports betting and iGaming (FLUT, DKNG). EV/EBITDA "
                "anchors on the operators that have crossed into positive "
                "EBITDA — Flutter is valued at 11.75x NTM+4 EBITDA for "
                "its US business. EV/Revenue carries real weight because the "
                "category is still scaling and DraftKings is valued on an "
                "equal blend of EV/Sales on NTM+1 and a modified DCF using "
                "an EV/EBITDA exit. Primary drivers: monthly paying players, "
                "net revenue margin (structural hold less promotions), and "
                "customer acquisition spend, which is the swing factor "
                "between growth and profitability in any given quarter. "
                "P/BV excluded — the asset base is licences and brand, "
                "not book."
            ),
        },
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
        "Offshore Marine & Resources (SG)": {
            "methods": [
                {"name": "EV/EBITDA",       "weight": 0.45, "anchor": True, "implementable": True},
                {"name": "P/BV",            "weight": 0.3, "anchor": False, "implementable": True},
                {"name": "DCF",             "weight": 0.25, "anchor": False, "implementable": True},
            ],
            "excluded": [],
            "rationale": "SG offshore marine, vessel fleet and resource support (Marco Polo Marine, Dyna-Mac, Samudera, Rex, Geo Energy). Day rates and utilisation set the cash flow, and the asset book is the downside — so EV/EBITDA over P/B, both against maintenance capex. Primary metrics: charter day rates, fleet utilisation, order intake and yard backlog, freight rates, cash cost per tonne or boe.",
        },
        # Wave 3 (owner, 2026-09-22): "SOTP combined with DDM ... a DDM is
        # frequently overlaid to capture its appeal as a high-yield regional
        # proxy." The SOTP is the analyst form fed by accepted Gemini inputs
        # (segments with cited multiple ranges); without accepted inputs it
        # declines and the DCF, DDM and EV/EBITDA carry the weight. SATS (S58.SI)
        # shares the profile and takes the same table.
        "Aerospace & Engineering (SG)": {
            "methods": [
                # The ANCHOR flag sits on DCF: an SGX anchor must produce a value on
                # an ordinary row (tests/test_sgx_method_availability.py), and the
                # analyst SOTP prices only on owner-accepted inputs. The weights are
                # the owner's: SOTP carries the most.
                {"name": "SOTP (analyst)",      "weight": 0.35, "anchor": False, "implementable": True},
                {"name": "DCF",                 "weight": 0.25, "anchor": True,  "implementable": True},
                {"name": "DDM",                 "weight": 0.25, "anchor": False, "implementable": True},
                {"name": "EV/EBITDA",           "weight": 0.15, "anchor": False, "implementable": True},
            ],
            "excluded": ['P/BV'],
            "rationale": "SG aerospace, defence and engineering (ST Engineering, SATS). DCF-anchored, following the published method for its largest member (ST Engineering: DCF, WACC 6.8%, terminal g 4%). Order-book-driven with multi-year delivery schedules, so backlog and burn rate lead the P&L. Primary metrics: order book backlog, EBIT margin, aviation traffic / cargo tonnage, net debt / EBITDA.",
        },
        "Aviation & Marine (SG)": {
            "methods": [
                {"name": "EV/EBITDA",       "weight": 0.45, "anchor": True, "implementable": True},
                {"name": "DCF",             "weight": 0.3, "anchor": False, "implementable": True},
                {"name": "Forward P/E",     "weight": 0.25, "anchor": False, "implementable": True},
            ],
            "excluded": ['P/BV'],
            "rationale": "SG aviation and marine / offshore (SIA, Seatrium, Yangzijiang). Fleet- and yard-heavy, so EV/EBITDA is fleet-ownership neutral where an earnings multiple is not. Primary metrics: passenger yield and load factor, net order intake and firm yard backlog, fuel hedge ratio, net cash.",
        },
        # Singapore conglomerate / industrial — Keppel, Sembcorp, ST
        # Engineering, Yangzijiang. SOTP is the published method: Keppel's
        # own note values Infrastructure at 15x PE, Real Estate at a 30%
        # discount to book, listed entities mark-to-market and non-core at
        # net book value, in one table. ROIC vs WACC carries the EVA spread.
        "Conglomerate / Industrial (SG)": {
            "methods": [
                {"name": "EV/EBITDA",           "weight": 0.4, "anchor": True, "implementable": True},
                {"name": "SOTP (published)",    "weight": 0.35, "anchor": False, "implementable": True},
                {"name": "DCF",                 "weight": 0.25, "anchor": False, "implementable": True},
            ],
            "excluded": ["P/BV"],
            "rationale": "SG conglomerate / industrial (Keppel, Sembcorp, ST Engineering). EV/EBITDA-anchored with a published-SOTP secondary. ROIC vs WACC was dropped as the EVA proxy: on the BN4.SI forward run it returned S$1.90 against EV/EBITDA S$10.91 and a published SOTP of S$10.70, so at any material weight it dragged the blend toward a number no method supported. EVA remains the right lens for a conglomerate; this engine implementation is not it. EV/EBITDA-anchored with SOTP secondary — the house method is SOTP, but `segment_breakdown` is populated solely from FMP revenue-product-segmentation, which returns nothing for SGX, so an SOTP anchor would silently never fire and the weights would renormalise onto the secondaries. SOTP contributes the moment segment data exists. Per-segment bases (PE, net book value, discount to book, mark-to-market) with per-segment bases (PE, net book value, discount to book, mark-to-market). Primary metrics: ROIC, order book / backlog duration, EV/EBITDA, FCF conversion.",
        },
        # ── Wave 3: aerospace & defence (owner framework, 2026-09-22) ──────────
        # One FMP label, `Aerospace & Defense`, covers three US businesses whose
        # clusters trade at 15x, 27x and unpriceable EV/EBITDA. The parent
        # profile is retired; the label's row defaults to Defense Primes and the
        # rest are pinned. Each profile prices on a curated basket where one
        # exists (regional_comps.PROFILE_PEER_BASKETS).
        #
        # Owner: "DCF based on FCF conversion. Secondary: EV/EBITDA relative to
        # multi-year historical defense budget appropriations ... stable terminal
        # growth (2.5% to 3.0%) coupled with a low WACC." The backlog-coverage
        # DCF is that DCF with the funded book bounding years 1-3; EV/EBITDA
        # (norm) is the through-cycle (dynamic) multiple on normalised earnings,
        # which is "relative to multi-year history" in the engine's own terms.
        "Defense Primes": {
            "methods": [
                {"name": "Backlog-coverage DCF", "weight": 0.40, "anchor": True,  "implementable": True},
                {"name": "EV/EBITDA (norm)",     "weight": 0.25, "anchor": False, "implementable": True},
                {"name": "FCF Yield",            "weight": 0.20, "anchor": False, "implementable": True},
                {"name": "EV/EBITDA",            "weight": 0.15, "anchor": False, "implementable": True},
            ],
            "excluded": ["P/E"],
            "rationale": "Fixed-price and cost-plus DoD programmes skew earnings with contract write-downs; value rests on baseline FCF conversion of a funded backlog, a stable terminal rate and a low WACC, cross-checked against a through-cycle EV/EBITDA.",
        },
        # Owner: Boeing on "SOTP and normalized EV/EBIT rather than P/E or
        # near-term FCF"; GE Aerospace on "P/E and EV/EBITDA on aftermarket
        # service margins". One profile: the normalised EBIT leg is the
        # mid-cycle delivery economics, the SOTP separates commercial from
        # defence, and the earnings legs carry GE's services franchise. Boeing's
        # legs drop where its trailing figures are negative and the run says so.
        "Commercial Aerospace & Engines": {
            "methods": [
                {"name": "EV/EBIT (norm)",       "weight": 0.30, "anchor": True,  "implementable": True},
                {"name": "SOTP (analyst)",       "weight": 0.25, "anchor": False, "implementable": True},
                {"name": "EV/EBITDA",            "weight": 0.25, "anchor": False, "implementable": True},
                {"name": "P/E",                  "weight": 0.20, "anchor": False, "implementable": True},
            ],
            "excluded": ["FCF Yield"],
            "rationale": "Free cash flow has been deeply negative through production crises, so the commercial business is valued on normalised mid-cycle delivery economics and a sum of the parts, while high-margin engine services carry the earnings multiples.",
        },
        # Owner: "PEG ratio and EV/FCF ... ROIC is a critical metric alongside
        # standard multiples." FCF Yield is EV/FCF's equity form.
        "Niche Aerospace Components": {
            "methods": [
                {"name": "PEG",                  "weight": 0.30, "anchor": True,  "implementable": True},
                {"name": "FCF Yield",            "weight": 0.30, "anchor": False, "implementable": True},
                {"name": "ROIC vs WACC",         "weight": 0.20, "anchor": False, "implementable": True},
                {"name": "Forward P/E",          "weight": 0.20, "anchor": False, "implementable": True},
            ],
            "excluded": ["P/BV"],
            "rationale": "Proprietary aftermarket parts with near-monopoly pricing power command structural 30x+ P/Es; growth-adjusted earnings and free cash flow price them, and ROIC checks the debt-funded bolt-on record.",
        },
        # Defence tech and space: pre-profit hardware priced on forward revenue.
        # Owner rule 1 (2026-09-23): the projection leg is the target-margin
        # revenue DCF, the explicit unprofitability fallback that the OE<=0 gate
        # does not disable, so the family's 0.35 is never lost to the multiples.
        # No EV/Backlog: conversion timing differs too much between a prime
        # subcontractor and a launch manufacturer for one constant.
        "Defense Tech & Space": {
            "methods": [
                {"name": "EV/Fwd Rev",           "weight": 0.45, "anchor": True,  "implementable": True},
                {"name": "Rev DCF (Target Margin)", "weight": 0.35, "anchor": False, "implementable": True},
                {"name": "EV/Revenue",           "weight": 0.20, "anchor": False, "implementable": True},
            ],
            "excluded": ["P/E", "EV/EBITDA"],
            "rationale": "Pre-profit or thin-margin defence hardware and launch: revenue and the path to margin are what can be measured; earnings multiples are not yet meaningful.",
        },
        # Owner (HK): AviChina -- "an umbrella vehicle holding stakes in
        # helicopter, trainer aircraft and component subsidiaries ... SOTP to
        # separate its manufacturing segments from its joint-venture holdings",
        # Forward P/E against global aerospace peers as the check. The
        # look-through NAV is exact once a template exists (owner-authored,
        # cited stakes); until then the P/BV proxy stands.
        "Aerospace Holdco (HK)": {
            "methods": [
                {"name": "DCF",                          "weight": 0.35, "anchor": True,  "implementable": True},
                # Owner rule 3 (2026-09-23), declared (2026-09-26): the accepted
                # analyst SOTP takes the structural slot; the look-through is
                # computed and published as an unweighted cross-check.
                {"name": "SOTP (analyst)",               "weight": 0.35, "anchor": False, "implementable": True},
                {"name": "Forward P/E",                  "weight": 0.30, "anchor": False, "implementable": True},
            ],
            "shadow_methods": ["SOTP / NAV (look-through)"],
            "excluded": [],
            "rationale": "A state-backed holding of listed aviation manufacturers: the parts are valued separately and the stakes looked through; forward earnings against global aerospace peers check the whole.",
        },
        # Owner (HK): Cirrus -- "Forward P/E and EV/Sales benchmarked against
        # premium consumer-industrial and general aviation peers ... DCF
        # projections focus heavily on order backlog conversion rates".
        "General Aviation (HK)": {
            "methods": [
                {"name": "Forward P/E",          "weight": 0.35, "anchor": True,  "implementable": True},
                {"name": "EV/Revenue",           "weight": 0.25, "anchor": False, "implementable": True},
                {"name": "Backlog-coverage DCF", "weight": 0.25, "anchor": False, "implementable": True},
                {"name": "EV/EBITDA",            "weight": 0.15, "anchor": False, "implementable": True},
            ],
            "excluded": ["P/BV"],
            "rationale": "A luxury private-aircraft manufacturer behaves like a high-end discretionary maker, not a defence prime: forward earnings, sales and the conversion of its order book.",
        },
        # Owner (HK): Continental Aerospace -- "EV/EBITDA and P/B ... tied to
        # global fleet flight hours and replacement part cycles."
        "GA Engines & Aftermarket (HK)": {
            "methods": [
                {"name": "EV/EBITDA",            "weight": 0.45, "anchor": True,  "implementable": True},
                {"name": "P/BV",                 "weight": 0.35, "anchor": False, "implementable": True},
                {"name": "FCF Yield",            "weight": 0.20, "anchor": False, "implementable": True},
            ],
            "excluded": [],
            "rationale": "General-aviation piston engines and aftermarket parts: an installed-base business whose value follows fleet flight hours and replacement cycles, priced on cash earnings and the asset base.",
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
        # Owner, 2026-09-22. Long-cycle equipment makers whose ORDER BOOK, not their
        # trailing earnings, describes them: heavy power equipment, nuclear, grid
        # infrastructure. GE Vernova on Capital Goods published $318 against a $955
        # quote because every weighted leg was trailing, on a company whose earnings
        # are inflecting, while the two legs that see the inflection carried no
        # weight. Kept apart from short-cycle industrials (Dover, Illinois Tool
        # Works), which Capital Goods still serves.
        #
        # NOT reachable by industry row, pin or classifier. A name arrives only
        # through dcf_agent's eligibility gate (valuation_constants
        # `backlog_gated_long_cycle`): backlog > 3.0x forward sales, book-to-bill
        # > 1.5x, contract liabilities > 50% of receivables plus inventory -- all
        # on owner-ACCEPTED filing figures. Fail one and the name stays where it was.
        "Backlog-Gated Long Cycle": {
            "methods": [
                {"name": "Backlog-coverage DCF", "weight": 0.35, "anchor": True,  "implementable": True},
                {"name": "Forward EV/EBITDA",    "weight": 0.25, "anchor": False, "implementable": True},
                {"name": "Forward P/E",          "weight": 0.20, "anchor": False, "implementable": True},
                {"name": "EV/EBITDA",            "weight": 0.20, "anchor": False, "implementable": True},
            ],
            "excluded": ["FCF Yield", "ROIC vs WACC"],
            "rationale": "A multi-year contracted order book with price escalation is the structural visibility a cash-flow forecast rests on; forward legs carry the earnings inflection trailing ones cannot.",
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
        # Singapore telco / infrastructure — Singtel, StarHub, NetLink NBN,
        # Keppel Infrastructure Trust. Priced SOTP because the value sits in
        # separable assets: regional associates (Airtel, AIS, Telkomsel),
        # the domestic access network, and infrastructure concessions, each
        # on its own multiple. EV/EBITDA cross-checks the operating core.
        "Telco / Infrastructure (SG)": {
            "methods": [
                {"name": "EV/EBITDA",           "weight": 0.4, "anchor": True, "implementable": True},
                {"name": "SOTP (published)",    "weight": 0.35, "anchor": False, "implementable": True},
                {"name": "DCF",                 "weight": 0.25, "anchor": False, "implementable": True},
            ],
            "excluded": ["P/BV"],
            "rationale": "SG telco / infrastructure (Singtel, StarHub, NetLink, Keppel Infra). SOTP-anchored: regional associates and concession assets valued separately from the domestic core. Primary metrics: EV/EBITDA, FCF yield, ARPU, core dividend yield, capex-to-revenue.",
        },
        "Stable Growth": {
            "methods": [
                {"name": "EV/EBITDA",     "weight": 0.35, "anchor": True,  "implementable": True},
                {"name": "DDM",           "weight": 0.25, "anchor": False, "implementable": True},
                {"name": "DCF (2-stage)", "weight": 0.25, "anchor": False, "implementable": True},
                {"name": "EPV",           "weight": 0.15, "anchor": False, "implementable": True},
            ],
            "excluded": [],
            "rationale": (
                "Telcos are valued on EV/EBITDA and dividend yield: the asset base is "
                "capital-intensive and the equity story is cash return, so EBITDA "
                "multiples and the distribution are what the market actually prices. "
                "This profile previously anchored on EPV with NO EV/EBITDA and NO DDM "
                "in the method set at all -- an earnings-power floor as the primary "
                "estimate for a regulated-utility-like cash machine, which is a floor "
                "presented as a valuation. EPV is retained as the no-growth floor it "
                "is, at a weight that reflects that role. DCF still carries the value "
                "of future reinvestment."
            ),
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
        "Digital Asset Mining": {
            "methods": [
                {"name": "P/BV",            "weight": 0.35, "anchor": True,  "implementable": True},
                {"name": "EV/EBITDA (norm)","weight": 0.30, "anchor": False, "implementable": True},
                {"name": "EV/Revenue",      "weight": 0.20, "anchor": False, "implementable": True},
                {"name": "DCF",             "weight": 0.15, "anchor": False, "implementable": True},
            ],
            "excluded": ["P/E (norm)"],
            "rationale": (
                "BTC miners (MARA, RIOT, CLSK, CIFR, BTDR) are asset-backed businesses: "
                "book value captures the BTC treasury + hashrate fleet, so P/BV anchors. "
                "Mining EBITDA is hashprice-cyclical — the 5-yr normalised EV/EBITDA "
                "prevents peak/trough multiple distortion. GAAP P/E excluded: net income "
                "is dominated by BTC mark-to-market and digital-asset impairments, not "
                "operating performance. DCF included with the sector's -10% FCF floor "
                "for sub-breakeven hashprice years."
            ),
        },
        "BTC Treasury / Proxy": {
            "methods": [
                {"name": "NAV Discount",    "weight": 0.45, "anchor": True,  "implementable": True},
                {"name": "EV/EBITDA",       "weight": 0.20, "anchor": False, "implementable": True},
                {"name": "DCF",             "weight": 0.20, "anchor": False, "implementable": True},
                {"name": "EV/Revenue",      "weight": 0.15, "anchor": False, "implementable": True},
            ],
            "excluded": ["P/E (norm)"],
            "rationale": (
                "BTC treasury companies (MSTR) are valued on mNAV — market cap vs the "
                "mark-to-market value of BTC holdings plus the operating business. "
                "NAV Discount is the implementable mNAV proxy: book value/share × the "
                "live mNAV_multiple from the framework extractor (post ASU 2023-08 the "
                "GAAP book carries BTC at fair value, so book × mNAV IS the market's "
                "treasury valuation); with no live mNAV extracted it falls back to "
                "book × peer P/B premium. EV/EBITDA + DCF + EV/Revenue value the residual "
                "operating business. Earnings multiples excluded — net income is "
                "dominated by BTC fair-value swings, not operations."
            ),
        },
        "Crypto Exchange": {
            "methods": [
                {"name": "EV/EBITDA",       "weight": 0.35, "anchor": True,  "implementable": True},
                {"name": "EV/Revenue",      "weight": 0.25, "anchor": False, "implementable": True},
                {"name": "Forward P/E",     "weight": 0.20, "anchor": False, "implementable": True},
                {"name": "P/E (norm)",      "weight": 0.20, "anchor": False, "implementable": True},
            ],
            "excluded": ["P/BV"],
            "rationale": (
                "Crypto exchanges/brokerages (COIN, HOOD) are take-rate businesses on "
                "trading volume — valued like fintech on EV/EBITDA and EV/Revenue, with "
                "earnings multiples for the mature-earnings base. Normalised P/E guards "
                "against crypto-cycle peak earnings (GAAP income includes own-crypto "
                "marks); Forward P/E anchors on consensus through the cycle. P/BV "
                "excluded — custodied client assets are off-balance-sheet, making book "
                "value meaningless relative to franchise value."
            ),
        },
    },

    # ── PROPERTY (SGX sector key) ─────────────────────────────────────────────
    # SGX classifies developers under "Property", separate from REITs.
    # Developers are valued on revalued NAV with a discount, not on
    # earnings: reported profit is lumpy with project completions while the
    # land bank carries the value.
    "Property": {
        "Specialised Accommodation (SG)": {
            "methods": [
                {"name": "NAV",             "weight": 0.45, "anchor": True, "implementable": True},
                {"name": "EV/EBITDA",       "weight": 0.35, "anchor": False, "implementable": True},
                {"name": "SOTP (published)",            "weight": 0.2, "anchor": False, "implementable": True},
            ],
            "excluded": ['P/BV'],
            "rationale": "SG specialised accommodation and co-living (Centurion, Wee Hur, LHN). A hybrid of an owned asset book and an operating business, so RNAV carries the freehold while EV/EBITDA prices the operations. Primary metrics: PBWA and PBSA bed capacity, average occupancy, RevPAB, net gearing.",
        },
        "Real Estate Agency (SG)": {
            "methods": [
                {"name": "Forward P/E",     "weight": 0.5, "anchor": True, "implementable": True},
                {"name": "DDM",             "weight": 0.3, "anchor": False, "implementable": True},
                {"name": "DCF",             "weight": 0.2, "anchor": False, "implementable": True},
            ],
            "excluded": ['EV/EBITDA', 'P/BV'],
            "rationale": "SG real estate agency (PropNex, APAC Realty). Asset-light, large net cash and a high payout, so the multiple is best read ex-cash and the dividend is a genuine floor. Primary metrics: transaction volume, agent headcount, commission split, net cash per share.",
        },
        "Property Developer (SG)": {
            "methods": [
                {"name": "NAV",          "weight": 0.55, "anchor": True,  "implementable": True},
                {"name": "DDM",          "weight": 0.25, "anchor": False, "implementable": True},
                {"name": "P/E (norm)",   "weight": 0.20, "anchor": False, "implementable": True},
            ],
            "excluded": ["EV/EBITDA"],
            "rationale": "SG property developer (City Developments, UOL, Frasers Property). RNAV-anchored — NAV carries the revalued land bank and investment properties, and the market trades at a persistent discount to it. Primary metrics: RNAV discount/premium, net gearing, pre-sales velocity, land bank cost basis.",
        },
    },
    "Healthcare": {
        "Healthcare Provider (SG)": {
            "methods": [
                {"name": "EV/EBITDA",       "weight": 0.45, "anchor": True, "implementable": True},
                {"name": "DCF",             "weight": 0.3, "anchor": False, "implementable": True},
                {"name": "Forward P/E",     "weight": 0.25, "anchor": False, "implementable": True},
            ],
            "excluded": ['P/BV'],
            "rationale": "SG healthcare provider (Thomson Medical, Raffles Medical). Capacity-constrained: bed count and occupancy cap revenue, and new capacity carries a gestation drag. Primary metrics: operational bed capacity, bed occupancy rate, ARPOB, staff-cost ratio, EBITDA margin.",
        },
    },

    "RealEstate": {
        # Singapore-listed REIT. Distinct from the US "REIT" row above:
        # S-REITs are priced on a dividend discount model off DPU, which
        # every Singapore broker note states in its valuation line, while
        # the US row anchors on NAV cap rates with P/FFO and P/AFFO
        # secondary. FFO and AFFO are US GAAP constructs that S-REITs do
        # not report, so pricing a Singapore trust on them applies an
        # American convention to a Singapore disclosure set.
        "S-REIT": {
            "methods": [
                {"name": "DDM (S-REIT)",    "weight": 0.55, "anchor": True,  "implementable": True},
                {"name": "NAV (Cap Rates)", "weight": 0.30, "anchor": False, "implementable": True, "scenario_invariant": True},
                {"name": "P/AFFO",          "weight": 0.15, "anchor": False, "implementable": True},
            ],
            "excluded": ["DCF", "P/BV", "P/FFO"],
            "rationale": "Singapore-listed REIT. DDM-anchored: value/unit = DPU x (1+g) / (CoE - g). CoE 6.4-8.5% and terminal g 0.5-2.75% by sub-sector, calibrated from published broker DDM tables (FCT 6.38%/1.5%, Keppel DC 6.83%/1.75%, OUE REIT 7.0%/1.2%). P/FFO excluded — S-REITs do not report FFO.",
        },
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
                {"name": "EV/EBITDA", "weight": 0.35, "anchor": True,  "implementable": True},
                {"name": "FCF Yield", "weight": 0.25, "anchor": False, "implementable": True},
                {"name": "P/E",       "weight": 0.20, "anchor": False, "implementable": True},
                {"name": "P/BV",      "weight": 0.10, "anchor": False, "implementable": True, "note": "through-cycle asset floor for shipping"},
                {"name": "DCF",       "weight": 0.10, "anchor": False, "implementable": True},
            ],
            "excluded": [],
            "rationale": "Regulated networks with stable volumes support EBITDA multiples; FCF yield reflects high capex.",
        },
    },

    "Materials": {
        "Steel / Metals": {
            "methods": [
                {"name": "EV/EBITDA (Norm)", "weight": 0.50, "anchor": True,  "implementable": True,  "note": "dispatched directly to the normalised-EBITDA branch — the capital-N spelling is a branch literal, not a proxy, and mid-cycle EBITDA is what the rationale below asks for"},
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
            # Wave 1 (owner-approved 2026-09-20). The previous anchor was named
            # "P/CF (mid-cycle)" but dispatched to the FCF-yield branch on
            # trailing FCF: neither price-to-cash-flow nor mid-cycle. EV/OCF is
            # the reportable cash-flow multiple E&Ps are priced on.
            "methods": [
                {"name": "EV/OCF",           "weight": 0.40, "anchor": True,  "implementable": True,
                 "note": "EV / operating cash flow vs same-market peers; EV/DACF's reportable form"},
                {"name": "EV/EBITDA (norm)", "weight": 0.25, "anchor": False, "implementable": True,  "note": "mid-cycle"},
                {"name": "Depleting Asset DCF (Finite Life, No TV)",
                                             "weight": 0.25, "anchor": False, "implementable": True},
                {"name": "P/BV",             "weight": 0.10, "anchor": False, "implementable": True,  "note": "reserve-replacement / equity floor"},
            ],
            "excluded": [],
            "rationale": (
                "Re-anchored on mid-cycle P/CF, which computes from reported cash "
                "flow. Reserve NPV at strip pricing is the industry standard and "
                "remains the right method -- but it needs PV-10 reserve disclosures "
                "this engine does not ingest, and was previously carried at 0.60 "
                "weight while silently resolving to a corporate DCF. With EV/DACF "
                "(0.25) and Real Options (0.05) also proxied, 90% of an upstream "
                "valuation was a label over a different calculation.\n\n"
                "A producing field depletes, so the finite-horizon DCF replaces the "
                "perpetual-growth proxy rather than renaming it."
            ),
            "data_limitation": (
                "Reserve NPV (PV-10) omitted from the blend until accepted: reserve "
                "values arrive review-gated (SEC standardized measure for US filers, "
                "cited Gemini pre-fill for HK/SG)."
            ),
        },
        "Integrated Oil & Gas": {
            "methods": [
                {"name": "EV/EBITDA (norm)", "weight": 0.45, "anchor": True,  "implementable": True, "note": "mid-cycle"},
                {"name": "EV/OCF",           "weight": 0.25, "anchor": False, "implementable": True},
                {"name": "FCF Yield",        "weight": 0.15, "anchor": False, "implementable": True},
                {"name": "DDM",              "weight": 0.15, "anchor": False, "implementable": True},
            ],
            "excluded": [],
            "rationale": (
                "Upstream, refining and chemicals under one balance sheet: priced on "
                "through-cycle EBITDA because any single year sits somewhere on the "
                "oil and crack-spread cycles. Cash-flow and dividend legs because "
                "the majors are valued on distribution capacity."
            ),
        },
        "Coal": {
            "methods": [
                {"name": "EV/EBITDA (norm)", "weight": 0.40, "anchor": True,  "implementable": True, "note": "mid-cycle"},
                {"name": "Depleting Asset DCF (Finite Life, No TV)",
                                             "weight": 0.25, "anchor": False, "implementable": True},
                {"name": "DDM",              "weight": 0.20, "anchor": False, "implementable": True,
                 "note": "state-owned coal producers distribute heavily"},
                {"name": "P/BV",             "weight": 0.15, "anchor": False, "implementable": True, "note": "floor"},
            ],
            "excluded": [],
            "rationale": (
                "Thermal and metallurgical coal: a depleting, price-taking resource. "
                "Mid-cycle EBITDA because realised coal prices swing earnings several-fold; "
                "finite-life DCF because reserves end; dividend leg because the "
                "Chinese majors pay out most of earnings."
            ),
        },
        "Mining (Major)": {
            "methods": [
                {"name": "EV/EBITDA (norm)",   "weight": 0.60, "anchor": True,  "implementable": True,  "note": "through-cycle normalised"},
                {"name": "Depleting Asset DCF (Finite Life, No TV)",
                                               "weight": 0.30, "anchor": False, "implementable": True},
                {"name": "P/BV",               "weight": 0.10, "anchor": False, "implementable": True,  "note": "reserve-replacement / equity floor"},
            ],
            "excluded": [],
            "rationale": (
                "Re-anchored on through-cycle normalised EV/EBITDA, which is how "
                "sell-side and institutional desks price diversified majors when no "
                "asset-level life-of-mine model is maintained, and which computes "
                "cleanly from the income statement and balance sheet.\n\n"
                "This profile previously anchored NAV (LoM) at 0.60 and P/NAV at "
                "0.20 with BOTH marked implementable: False -- so 80% of a miner's "
                "weight was a generic corporate DCF and P/BV wearing mine-life "
                "labels. The proxy erred in a known DIRECTION: a perpetual terminal "
                "growth term on a DEPLETING asset values ore that does not exist.\n\n"
                "The finite-horizon replacement is deliberately NOT called NAV (LoM). "
                "A life-of-mine NAV ingests proven & probable reserves, recovery "
                "rates and a commodity price deck; none of that telemetry is "
                "available, so asset-level LoM NAV is OMITTED rather than simulated."
            ),
            "data_limitation": (
                "Asset-level life-of-mine NAV omitted: no reserve, grade or "
                "commodity price-deck data available."
            ),
        },
    },

    "ProfessionalServices": {
        "Ad / Consulting": {
            "methods": [
                {"name": "EV/EBIT (Pre-bonus)", "weight": 0.40, "anchor": True,  "implementable": True,  "note": "dispatched directly to the EV/EBIT branch — no data source here separates partner bonus from staff cost, so reported EBIT is the proxy and the label overstates what was measured"},
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
                {"name": "EV/EBITDA",  "weight": 0.35, "anchor": True,  "implementable": True},
                {"name": "P/E",        "weight": 0.25, "anchor": False, "implementable": True},
                {"name": "DCF",        "weight": 0.20, "anchor": False, "implementable": True},
                {"name": "EV/Revenue", "weight": 0.10, "anchor": False, "implementable": True, "note": "pre-profit fabless designers"},
                {"name": "FCF Yield",  "weight": 0.10, "anchor": False, "implementable": True},
            ],
            "excluded": ["EPV", "LBO Floor"],
            "rationale": (
                "IDMs and foundries (MU, INTC, TSM, TXN, GFS) have massive fab CapEx "
                "that suppresses FCF. EV/EBITDA anchors because it strips CapEx "
                "distortion. EPV excluded — cyclical earnings make perpetuity nonsensical."
            ),
        },
        "Memory / DRAM-NAND": {
            "methods": [
                {"name": "P/E (norm)",   "weight": 0.45, "anchor": True,  "implementable": True},
                {"name": "EV/EBITDA",    "weight": 0.30, "anchor": False, "implementable": True},
                {"name": "DCF",          "weight": 0.25, "anchor": False, "implementable": True},
            ],
            "excluded": ["EPV", "LBO Floor", "DDM"],
            "rationale": (
                "Memory makers (MU, 000660.KS) are priced on a multiple of "
                "through-cycle earnings, not on trailing EPS: Goldman values "
                "Micron at 18x a normalised EPS of $62 and SK Hynix on an "
                "averaged 2026E/27E P/E. P/E (norm) anchors because the cycle "
                "position, not the spot quarter, is what the multiple is "
                "applied to. Split out from IDM / Foundry, whose utilisation "
                "and leading-edge-mix anchors describe a foundry's economics "
                "rather than DRAM/NAND bit supply, ASP and HBM mix. EPV "
                "excluded — cyclical earnings make a perpetuity nonsensical."
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
    # Crypto sub-profiles (D1 taxonomy gap fix): lookup-assigned profiles for
    # MSTR/MARA/RIOT/CLSK/CIFR/BTDR/COIN/HOOD previously resolved to nothing
    # and silently fell back to DCF-only.
    "Digital Asset Mining": {"ev_ebitda": 10.0, "pe": 18.0, "ev_revenue": 3.5, "pb": 1.3,  "fcf_yield": 0.050, "growth_avg": 0.15},
    "BTC Treasury / Proxy": {"ev_ebitda": 14.0, "pe": 22.0, "ev_revenue": 4.5, "pb": 1.8,  "fcf_yield": 0.040, "growth_avg": 0.20},
    "Crypto Exchange":      {"ev_ebitda": 16.0, "pe": 28.0, "ev_revenue": 8.0, "pb": 5.0,  "fcf_yield": 0.035, "growth_avg": 0.18},
    "Fintech/Stablecoin":   {"ev_ebitda": 18.0, "pe": 26.0, "ev_revenue": 5.5, "pb": 3.5,  "fcf_yield": 0.040, "growth_avg": 0.15},
    "Energy":              {"ev_ebitda": 10.0, "pe": 16.0, "ev_revenue": 2.5,  "pb": 1.8,  "fcf_yield": 0.055, "growth_avg": 0.04},
    # Regulated Utility sub-profile: higher EV/EBITDA (12.5x) and P/E (18x) than
    # generic Energy (10x / 16x) because regulated rate base provides earnings
    # visibility and lower cost of equity.  Benchmarks: NEE 14x, SO 12x, DUK 12x,
    # D 11–13x — mid-range 12.5x base.  FCF yield lower (4.5%) reflecting
    # capital-intensive reinvestment cycle (capex > depreciation for rate base growth).
    # Forward (NTM) statics, owner 2026-09-22: "proceed with NTM for Wave 2".
    # Read by the Forward P/E and Forward EV/EBITDA legs when
    # NTM_FORWARD_MULTIPLES_ENABLED is on AND the live basket resolves no NTM
    # median; a live TRAILING median at industry level outranks them
    # (dcf_agent._basket_rank), so they cannot swap a name's peer set. Derived
    # from the 2026-09-21 local refresh, the first to carry NTM fields; the
    # trailing statics stay on the trailing readings, because the mismatch runs
    # both ways. Re-peg per scripts/check_static_multiples.py.
    #   US Regulated Electric  NTM EV/EBITDA 9.79x (n=15)  NTM P/E 16.86x (n=15)
    "Regulated Utility":   {"ev_ebitda": 12.5, "pe": 18.0, "ev_revenue": 3.0,  "pb": 2.0,  "fcf_yield": 0.045, "growth_avg": 0.04,
                            "ev_ebitda_ntm": 9.8, "pe_ntm": 16.9},
    # IPP / Merchant Power: riskier than regulated; closer to generic Energy
    # Wave 2, derived 2026-09-21 and shown to the owner first. Fallbacks only:
    # live comps win every field they resolve. Basis: US industry medians,
    # trailing (TTM), beside the through-cycle table. The owner's rule is NTM
    # EV/EBITDA and FY1/FY2 P/E with a +/-15% / 30-day re-peg trigger; the comps
    # store carries no NTM median yet, so these are dated TTM readings to be
    # re-derived when it does.
    #   IPP basket        live EV/EBITDA 12.07x (n=7), through-cycle 11.05x, P/B 2.90x
    #   Solar basket      live 22.22x / 20.22x P/E / 2.78x EV/Rev (n=5-8), through-cycle 18.13x / 22.25x
    #   US Independent Power Producers  NTM EV/EBITDA 9.41x (n=6)  NTM P/E 10.59x (n=5) -- one basket
    #   serves IPP and Merchant Power; the same forward readings for both.
    "IPP":                 {"ev_ebitda": 11.0, "pe": 14.0, "ev_revenue": 2.0,  "pb": 2.0,  "fcf_yield": 0.060, "growth_avg": 0.06,
                            "ev_ebitda_ntm": 9.4, "pe_ntm": 10.6},
    "Merchant Power":      {"ev_ebitda": 11.0, "pe": 18.0, "ev_revenue": 2.5,  "pb": 2.9,  "fcf_yield": 0.050, "growth_avg": 0.06,
                            "ev_ebitda_ntm": 9.4, "pe_ntm": 10.6},
    #   US Solar  NTM EV/EBITDA 9.30x (n=8)  NTM P/E 12.01x (n=6). The trailing 18.0x is the
    #   through-cycle reading held deliberately under a 22.35x live median (check_static_multiples
    #   flags it, +24%); a policy-cycle basket's spot multiple is the thing not to peg to.
    "Clean Tech / Power Equipment OEM": {"ev_ebitda": 18.0, "pe": 21.0, "ev_revenue": 2.8, "pb": 2.1, "fcf_yield": 0.050, "growth_avg": 0.10,
                                         "ev_ebitda_ntm": 9.3, "pe_ntm": 12.0},
    "Financials":          {"ev_ebitda": 12.0, "pe": 12.0, "ev_revenue": 2.0,  "pb": 1.4,  "fcf_yield": 0.065, "growth_avg": 0.06},
    # Financials sub-profile overrides — keyed on profile_name for dcf_agent lookup
    # Banks use P/E and P/TBV; EV/EBITDA is not applicable
    "Money Center Bank":   {"ev_ebitda": 11.0, "pe": 11.0, "ev_revenue": 2.5,  "pb": 1.3,  "fcf_yield": 0.060, "growth_avg": 0.05},
    # SG banks: P/E 14x and P/B 2.0x reflect the DBS/OCBC trading band
    # (DBS FY26e PER 14.6x / P/BV 2.4x; OCBC 12.5x / 1.5x). growth_avg 3%
    # — SG bank total income grows at low-single digits while book
    # compounds; the old 5% average pushed the growth premium to its floor.
    "Money Center Bank (SG)": {"ev_ebitda": 12.0, "pe": 14.0, "ev_revenue": 3.0, "pb": 2.0, "fcf_yield": 0.055, "growth_avg": 0.03},
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
    # Wave 3 (2026-09-22), the curated baskets' medians on the 2026-09-21 refresh
    # (regional_comps.PROFILE_PEER_BASKETS): primes n=6, niche n=5; Commercial
    # Aerospace & Engines is the BA/GE pair and takes the industry rung live, so
    # its static is the aftermarket cluster measured beside it (n=10). Defense
    # Tech & Space carries no static: no earnings multiple exists for it and its
    # forward-revenue leg reads the live industry EV/Revenue (4.8x, n=19).
    "Defense Primes":      {"ev_ebitda": 15.3, "pe": 23.1, "ev_revenue": 2.2,  "pb": 4.1,  "fcf_yield": 0.055, "growth_avg": 0.06,
                            "ev_ebitda_ntm": 13.5, "pe_ntm": 19.2, "ev_ebit": 19.0},
    "Commercial Aerospace & Engines": {"ev_ebitda": 27.2, "pe": 40.2, "ev_revenue": 7.0, "pb": 8.5, "fcf_yield": 0.030, "growth_avg": 0.10,
                            "ev_ebitda_ntm": 23.1, "pe_ntm": 34.4},
    "Niche Aerospace Components": {"ev_ebitda": 26.4, "pe": 39.1, "ev_revenue": 8.6, "pb": 8.5, "fcf_yield": 0.025, "growth_avg": 0.15,
                            "ev_ebitda_ntm": 22.7, "pe_ntm": 34.4},
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


# ── Dynamic peer multiples (2026-07-27) ───────────────────────────────────────
# SECTOR_PEER_MULTIPLES above is a single static number per profile, calibrated
# once and never refreshed — it can't tell a FinTech name trading at a market
# discount (competitive share loss, credibility discount) from one trading at
# a premium; every name in the bucket gets the same 22x P/E regardless of
# where its actual peer group trades today. This section replaces that with a
# LIVE median computed from a curated peer basket, when enough fresh data is
# available, falling back field-by-field to the static number above otherwise.
#
# Curation, not automation, is deliberate — this mirrors standard equity-
# research practice (comparable-company analysis always starts from analyst
# judgment about which businesses are genuinely comparable, not just a shared
# industry code; Bloomberg/FactSet's algorithmic peer suggestions are treated
# as a candidate list an analyst prunes, never used as-is). An automated
# same-sector filter would have grouped e.g. PayPal with Visa (structurally
# different economics — see FinTech vs Payment Networks below).
#
# Data source: app/backend/services/knowledge_graph.py's kg_ticker_metrics
# cache (TTM ratios: pe, pb, ev_ebitda, ev_sales, fcf_yield, rev_growth),
# the same per-ticker cache the screener and live pipeline VGPM already share.
# READ-ONLY here — deliberately does NOT live-fetch missing peers. The team
# already tried and removed a screener pre-warm mechanism because it broke
# load times; adding bounded live-fetches for up to N peers on every DCF run
# would reintroduce that same class of risk on a much hotter path. Coverage
# builds up purely organically as the screener/pipeline touch these tickers
# in the course of normal use — large, frequently-analyzed names (most of
# this basket) should accumulate fresh cache entries quickly. Until then,
# every field just falls back to the static table, so this can never make a
# valuation worse than today's baseline, only better once data exists.
SECTOR_PEER_BASKETS: dict[str, list[str]] = {
    "Tech":                 ["MSFT", "GOOGL", "ORCL", "ADBE", "CRM", "IBM", "SAP"],
    "Consumer":             ["PG", "KO", "PEP", "WMT", "TGT", "COST", "CL"],
    "Biopharma":            ["PFE", "MRK", "ABBV", "BMY", "LLY", "JNJ", "GSK"],
    "MedTech / Devices":    ["MDT", "SYK", "BSX", "ISRG", "ZBH", "EW"],
    "CDMO / Life Science Tools": ["TMO", "DHR", "A", "CRL", "ICLR", "AVTR"],
    "Pre-approval Biotech": ["VRTX", "REGN", "ALNY", "BMRN", "RARE", "SRPT"],
    "Telco":                ["VZ", "T", "TMUS", "VOD", "BCE"],
    "Crypto":               ["COIN", "MSTR", "MARA", "RIOT", "HUT", "CLSK"],
    "Energy":               ["XOM", "CVX", "COP", "EOG", "SLB", "OXY"],
    "Regulated Utility":    ["NEE", "DUK", "SO", "D", "AEP", "XEL"],
    "IPP":                  ["NRG", "VST", "CEG", "TLN"],
    "Financials":           ["JPM", "BAC", "WFC", "C", "USB", "PNC"],
    "Money Center Bank":    ["JPM", "BAC", "C", "WFC", "HSBC"],
    "Money Center Bank (SG)": ["D05.SI", "O39.SI", "U11.SI"],
    "Regional Bank":        ["USB", "PNC", "TFC", "RF", "KEY", "CFG"],
    "Insurance":            ["PGR", "TRV", "ALL", "CB", "AIG", "MET"],
    "Investment Bank":      ["GS", "MS", "LAZ", "EVR"],
    "Asset Manager":        ["BLK", "BEN", "TROW", "IVZ", "STT"],
    # FinTech ≠ Payment Networks: PYPL/SQ/AFRM/SOFI carry consumer-credit and
    # checkout-competition risk that V/MA/AXP's closed-loop network economics
    # don't — a real analyst would never treat these as the same comp set,
    # even though FMP/GICS often lump them together.
    "FinTech":              ["SQ", "AFRM", "SOFI", "PYPL", "GPN", "FI"],
    "Mortgage/GSE":         ["FNMA", "FMCC"],
    "Payment Networks":     ["V", "MA", "AXP"],
    "Market Infrastructure": ["ICE", "CME", "NDAQ", "CBOE"],
    "Brokerage":            ["SCHW", "IBKR", "HOOD"],
    "Membership / Subscription Retail": ["COST", "BJ"],
    "Consumer Durables":    ["WHR", "NWL", "SNBR", "NWL"],
    "Automotive & EV":      ["TSLA", "GM", "F", "RIVN", "LCID"],
    "Travel & Dining":      ["MCD", "SBUX", "DIS", "ABNB", "BKNG"],
    "Industrials":          ["HON", "GE", "MMM", "CAT", "DE"],
    "RealEstate":           ["VNO", "BXP", "SLG"],
    "REIT":                 ["O", "SPG", "PLD", "EQIX", "AVB"],
    "Transportation":       ["UNP", "CSX", "DAL", "UAL", "JBHT"],
    "Materials":            ["LIN", "APD", "SHW", "NEM"],
    "Resources":            ["FCX", "NEM", "VALE", "SCCO"],
    "ProfessionalServices": ["ACN", "IBM", "EXLS", "WNS", "GLOB"],
    "Semiconductor":        ["NVDA", "AVGO", "TXN", "QCOM", "AMD"],
}

# Minimum number of peers that must have fresh, usable KG data for a field
# before the dynamic median is trusted over the static fallback. 4 is a
# judgment call — enough to be more than a coin flip, low enough that the
# dynamic path actually engages given organic (non-pre-warmed) KG coverage.
_MIN_PEERS_FOR_DYNAMIC = 4

# KG field name -> SECTOR_PEER_MULTIPLES field name, plus a unit converter
# where they differ (KG's fcf_yield is a percentage like 5.0; the static
# table uses a decimal fraction like 0.05).
_KG_FIELD_MAP: dict[str, tuple[str, float]] = {
    "pe":         ("pe", 1.0),
    "pb":         ("pb", 1.0),
    "ev_ebitda":  ("ev_ebitda", 1.0),
    "ev_sales":   ("ev_revenue", 1.0),
    "fcf_yield":  ("fcf_yield", 0.01),
    "rev_growth": ("growth_avg", 1.0),
}


def get_dynamic_peer_multiples(
    sector: str,
    profile_name: str = "",
) -> dict[str, float]:
    """Median peer multiples computed live from the Knowledge Graph cache for
    a curated basket, field-by-field. Returns {} (never raises, never blocks)
    when the basket is undefined or too few peers have fresh KG data — callers
    should merge this OVER the static SECTOR_PEER_MULTIPLES entry, not use it
    standalone, since individual fields (cn_adr_haircut, ev_rd, growth_avg
    when peers lack growth data) may still need the static value.
    """
    basket = SECTOR_PEER_BASKETS.get(profile_name) or SECTOR_PEER_BASKETS.get(sector)
    if not basket:
        return {}
    try:
        from app.backend.services.knowledge_graph import get_ttm_metrics_cached
        cached = get_ttm_metrics_cached(basket)
    except Exception:
        return {}
    if not cached:
        return {}

    result: dict[str, float] = {}
    for kg_field, (out_field, unit_mult) in _KG_FIELD_MAP.items():
        vals = []
        for t in basket:
            m = cached.get(t)
            if not m:
                continue
            v = m.get(kg_field)
            if v is not None and v > 0:
                vals.append(v * unit_mult)
        if len(vals) >= _MIN_PEERS_FOR_DYNAMIC:
            result[out_field] = round(statistics.median(vals), 4)
    return result


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
    # Wave 2, HKSE industry medians 2026-09-19 (TTM) beside the through-cycle
    # table: Regulated Electric 9.80x / 13.18x / 0.92x (through-cycle 10.47x /
    # 15.60x); Independent Power Producers 8.34x / 7.38x / 0.80x (8.28x / 10.39x).
    #   NTM (2026-09-22): HKSE Regulated Electric NTM EV/EBITDA 8.92x (n=5), NTM P/E 14.60x (n=6) --
    #   ABOVE trailing, because HK consensus sits below trailing earnings. The HKSE IPP basket
    #   forms no NTM median (no member clears two analysts), so IPP carries none: its forward
    #   legs keep the trailing multiple and say so.
    "Regulated Utility": {"ev_ebitda": 10.0, "pe": 14.0, "ev_revenue": 3.0, "pb": 0.95, "fcf_yield": 0.060, "growth_avg": 0.03,
                          "ev_ebitda_ntm": 8.9, "pe_ntm": 14.6},
    "IPP":               {"ev_ebitda":  8.3, "pe":  9.0, "ev_revenue": 2.9, "pb": 0.80, "fcf_yield": 0.060, "growth_avg": 0.04},
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
    ticker: str = "",
    exchange: str = "",
    industry: str = "",
    market_cap: float = 0.0,
) -> dict[str, float]:
    """
    Return sector peer multiples for relative valuation.

    Parameters
    ----------
    sector      : sector string (e.g. "Tech", "Financials")
    is_hk       : True for HKEX-listed stocks → uses HK_SECTOR_PEER_MULTIPLES
    profile_name: optional sub-profile override (e.g. "Money Center Bank")
    ticker      : when given, HK/SG names resolve live exchange comps and the
                  exchange/industry are looked up automatically
    exchange    : FMP short code ("HKSE"/"SES"), skips the ticker lookup
    industry    : FMP industry string, skips the ticker lookup
    market_cap  : the target's own market cap — lets HK/SG comps resolve to a
                  size-matched peer cohort instead of a whole-industry median

    Returns
    -------
    dict with keys: ev_ebitda, pe, ev_revenue, pb, fcf_yield. Individual
    fields may come from a live peer-basket median (see
    get_dynamic_peer_multiples) when enough fresh comp data exists;
    otherwise from the static table below. Never a mix of stale-static
    and stale-dynamic for the same field — each field is independently
    either live or static, never partially blended.
    """
    # Static fallback comes from the market registry, not a two-way branch.
    # The old `HK_SECTOR_PEER_MULTIPLES if is_hk else SECTOR_PEER_MULTIPLES`
    # had no Singapore arm, so every SGX name that missed the live comps was
    # valued on US multiples — which run 20-60% above comparable HK levels
    # across 13 of 15 sectors, and SGX sits far closer to HK than to the US.
    # A market with no authored table now returns {} and relies on live comps,
    # rather than inheriting another market's economics.
    _market = resolve_market(ticker=ticker, exchange=exchange, is_hk=is_hk)
    table = market_peer_multiples(_market)
    static = (
        table.get(profile_name)
        or table.get(sector)
        or {}
    )

    # ── HK / SG: live exchange comps, industry-first ──────────────────────
    # Before FMP global coverage this branch could only return the static
    # 2026-04 HK table. Now HKEX supports genuine industry medians (97 of its
    # 139 industries clear the peer floor) and SGX resolves to sector for all
    # but a handful. Falls through to `static` for any field without a
    # qualifying comp set, so this can only add information.
    regional = _regional_peer_multiples(ticker, exchange, industry, sector,
                                        market_cap)
    # A curated profile basket outranks the industry median for every field it
    # resolves (regional_comps.PROFILE_PEER_BASKETS): the industry label can
    # blend businesses the profile has just told us apart.
    try:
        from src.data.regional_comps import profile_basket_multiples
        _pb = profile_basket_multiples(_market or "US", profile_name)
        if _pb:
            regional = {**(regional or {}), **_pb}
    except Exception:                                      # noqa: BLE001
        pass

    def _stamp(values: dict, basis: dict) -> dict:
        """Attach provenance for EVERY field, including the ones that fell
        back to the static table.

        Silence about a fallback is what let the 2026-09-13 comps outage run
        for 17 days: `_comp_basis` was written only when live comps resolved,
        so a run using US statics for a Hong Kong stock looked exactly like a
        run with no provenance at all. A multiple nobody can trace is a
        multiple nobody can check.
        """
        out = dict(values)
        full = dict(basis)
        for field in out:
            if field.startswith("_") or not isinstance(out[field], (int, float)):
                continue
            if field not in full:
                full[field] = {"basis": "static", "cohort": _market or "US",
                               "peer_count": None}
        out["_comp_basis"] = full
        out["_comp_market"] = _market
        try:
            from src.data.regional_comps import latest_refresh_age_days
            _ex = exchange or ""
            if not _ex and ticker:
                from src.data.regional_comps import get_fmp_classification
                _ex = (get_fmp_classification(ticker) or {}).get("exchange") or ""
            out["_comp_age_days"] = latest_refresh_age_days(_ex) if _ex else None
        except Exception:                                  # noqa: BLE001
            out["_comp_age_days"] = None
        return out

    if regional:
        # Layering, weakest first: static table, then the curated-basket
        # median from the KG cache (US only), then the measured exchange
        # comps. Regional is industry-level and size-matched so it wins any
        # field it resolves; dynamic still fills fields it does not.
        merged = {**static}
        # US only, by market -- not "not HK". The curated basket is US
        # companies, and the old `not is_hk` test let it into Singapore:
        # Seatrium (2026-09-20), whose SGX baskets are under the peer floor,
        # was priced on US Industrials at 18.9x book and 35.8x earnings --
        # base IV S$14.01 against a S$2.12 price. No market borrows another's
        # multiples.
        if _market == "US":
            merged.update(get_dynamic_peer_multiples(sector, profile_name))
        basis: dict[str, dict] = {}
        for field, row in regional.items():
            merged[field] = row["value"]
            basis[field] = {"basis": row["basis"],
                            "cohort": row.get("cohort", "all"),
                            "peer_count": row["peer_count"],
                            # Which basket, so the named peers can be listed.
                            "key": row.get("key"),
                            "exchange": row.get("exchange")}
        # Non-numeric, underscore-prefixed so the numeric consumers that read
        # peer["pe"] / peer.get("ev_ebitda") are unaffected. Lets the report
        # and the LLM write-up state what a multiple was actually derived
        # from instead of implying a precision the peer set does not support.
        return _stamp(merged, basis)
    if _market != "US":
        return _stamp(static, {})

    dynamic = get_dynamic_peer_multiples(sector, profile_name)
    if not dynamic:
        return _stamp(static, {})
    merged = {**static, **dynamic}
    return _stamp(merged, {f: {"basis": "dynamic", "cohort": sector,
                               "peer_count": None} for f in dynamic})


def _regional_peer_multiples(
    ticker: str = "",
    exchange: str = "",
    industry: str = "",
    sector: str = "",
    market_cap: float = 0.0,
) -> dict[str, dict]:
    """Live HK/SG comps for one name, or {} when unavailable.

    Resolves the exchange and FMP industry from the ticker when the caller
    did not supply them. Never raises and never blocks: a missing table, a
    stale refresh or an unclassifiable ticker all return {}.
    """
    try:
        from src.data.regional_comps import (
            get_fmp_classification, get_regional_multiples, market_for_exchange,
        )
    except Exception:
        return {}

    fmp_sector = sector
    if (not exchange or not industry) and ticker:
        info = get_fmp_classification(ticker)
        exchange = exchange or info.get("exchange", "")
        # A ticker pinned AGAINST its FMP label takes the basket of the business
        # it is, or the pin changes the methods and leaves the multiples behind.
        try:
            from src.data.industry_profile_map import comps_industry_for
            industry = industry or comps_industry_for(ticker) or info.get("industry", "")
        except Exception:                                  # noqa: BLE001
            industry = industry or info.get("industry", "")
        # The FMP sector string ("Financial Services") is what keys the
        # comps table, not the repo's internal profile name ("Financials").
        fmp_sector = info.get("sector", "") or sector

    # FMP reports the listing venue ("NASDAQ"); the store is keyed by market
    # ("US"), since where a company listed is not an economic distinction.
    market = market_for_exchange(exchange)
    if not market:
        return {}
    try:
        return get_regional_multiples(market, industry, fmp_sector,
                                      market_cap=market_cap or None)
    except Exception:
        return {}


#: Leverage premium: +1pp of WACC per 1.0x of net debt / equity above 1.5x.
WACC_LEVERAGE_THRESHOLD = 1.5
WACC_LEVERAGE_SLOPE = 0.01


def wacc_base_breakdown(
    sector: str,
    leverage: float = 0.0,
    macro_regime: str = "neutral",
    profile: str = "",
    is_hk: bool = False,
    is_sg: bool = False,
) -> dict:
    """Every component of the sector/profile base WACC, and the result.

    The single source of the base rate: `get_wacc_for_exchange` returns this
    function's `wacc`, so what the valuation export shows is, by
    construction, the rate the engine used. Components:

      market, table, lookup    which table the rate came from and by what key
      table_rate               the table's rate (embeds any country risk
                               premium already, for HK/SG sector tables)
      crp_embedded             the country risk premium inside table_rate
      leverage, leverage_premium, leverage_premium_applies
      leverage_cap             premium + overlay may add at most this much
      macro_regime, macro_overlay
      wacc                     round(min(table + premium + overlay,
                                         table + cap), 4)
    """
    market = "SG" if is_sg else ("HK" if is_hk else "US")
    crp = _SG_CRP if is_sg else (_HK_CHINA_CRP if is_hk else 0.0)
    own = _profile_wacc_rate(sector, profile)
    if own is not None:
        table, lookup = f"{sector} profile WACC (Damodaran)", profile
        rate, lev_cap = own[0] + crp, own[1]
    elif is_sg:
        lookup = sector
        if sector in SG_SECTOR_WACC:
            table, rate = "SG sector WACC", SG_SECTOR_WACC[sector]
        else:
            table, rate = "US sector WACC + SG country risk", SECTOR_WACC.get(sector, 0.090) + _SG_CRP
        lev_cap = 0.040
    elif is_hk:
        lookup = sector
        if sector in HK_SECTOR_WACC:
            table, rate = "HK sector WACC", HK_SECTOR_WACC[sector]
        else:
            table, rate = "US sector WACC + China country risk", SECTOR_WACC.get(sector, 0.090) + _HK_CHINA_CRP
        lev_cap = 0.040
    else:
        table, lookup = "US sector WACC (Damodaran)", sector
        rate = SECTOR_WACC.get(sector, 0.090)
        lev_cap = 0.040
        if sector not in SECTOR_WACC:
            table = "default (sector not in table)"
    # REITs carry leverage by design; the US path exempts them from the
    # premium (the HK/SG paths never did, and still do not).
    applies = not (market == "US" and sector in ("REIT", "RealEstate"))
    premium = (max(0.0, (leverage - WACC_LEVERAGE_THRESHOLD) * WACC_LEVERAGE_SLOPE)
               if applies else 0.0)
    overlay = _MACRO_WACC_OVERLAY.get(macro_regime, 0.0)
    uncapped = rate + premium + overlay
    return {
        "market": market, "table": table, "lookup": lookup,
        "table_rate": rate, "crp_embedded": crp,
        "leverage": leverage, "leverage_threshold": WACC_LEVERAGE_THRESHOLD,
        "leverage_slope": WACC_LEVERAGE_SLOPE,
        "leverage_premium_applies": applies, "leverage_premium": premium,
        "leverage_cap": lev_cap,
        "macro_regime": macro_regime, "macro_overlay": overlay,
        "cap_binding": uncapped > rate + lev_cap,
        "wacc": round(min(uncapped, rate + lev_cap), 4),
    }


def get_wacc_for_exchange(
    sector: str,
    leverage: float = 0.0,
    macro_regime: str = "neutral",
    profile: str = "",
    is_hk: bool = False,
    is_sg: bool = False,
) -> float:
    """
    Return WACC, routing to the listing market's rates.

    For HK-listed stocks the base rate embeds China CRP (+150 bps); for
    SG-listed stocks it embeds the Singapore CRP (+50 bps). All other
    parameters (leverage premium, macro overlay) are applied identically.

    `is_sg` exists because market was previously a boolean (`is_hk`), so every
    SGX name fell through to the US path and was discounted at US rates with no
    country premium. That is how Sembcorp (Energy / Regulated Utility) drew a
    4.18% WACC -- Damodaran's US regulated-utility rate -- against the registry's
    7.0% for SG Energy. With terminal value at ~90% of a utility DCF, that gap
    alone put intrinsic value at 6x spot.

    Both flags False reproduces get_wacc() exactly (pinned by the test below).

    Computed through `wacc_base_breakdown`, the one place the components
    live, so the valuation export shows the build that produced this rate.
    Equivalence with the previous inline formula is pinned over every
    sector x profile x market x leverage x regime combination by
    tests/test_wacc_base_breakdown.py.
    """
    return wacc_base_breakdown(sector, leverage, macro_regime=macro_regime,
                               profile=profile, is_hk=is_hk, is_sg=is_sg)["wacc"]


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
    is_sg: bool = False,
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
        sector, leverage, macro_regime=macro_regime, profile=profile,
        is_hk=is_hk, is_sg=is_sg,
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


# D3: explicit per-sector default profile for classify_valuation_profile().
# Replaces the previous order-dependent `next(iter(profiles), "")` fallback:
# dict-insertion order made the "default" whatever profile happened to be
# defined first (Financials → "Mortgage/GSE", HealthcareServices →
# "Managed Care"), silently mis-routing any ticker whose metrics missed
# every ladder branch. Each entry below is the deliberately chosen
# most-general profile for its sector. Every value must exist in
# INDUSTRY_VALUATION_PROFILES[sector] (enforced by the conformance test).
_SECTOR_PROFILE_DEFAULT = {
    "Financials": "Holding Company",
    "Energy": "Regulated Utility",
    "Tech": "Mature Platform",
    "Biopharma": "Large Cap Pharma",
    "HealthcareServices": "Healthcare Providers / Services",
    "Consumer": "Consumer Growth",
    "Industrials": "Capital Goods",
    "Telco": "Stable Growth",
    "Crypto": "Pre-Revenue Tech",
    "RealEstate": "REIT",
    # SGX classifies developers under "Property", separate from REITs.
    "Property": "Property Developer (SG)",
    # SGX uses "Healthcare"; the US table uses "HealthcareServices".
    "Healthcare": "Healthcare Provider (SG)",
    "Transportation": "Rail / Logistics",
    "Materials": "Specialty Chemicals",
    "Resources": "Mining (Major)",
    "ProfessionalServices": "Ad / Consulting",
    "Semiconductor": "Equipment / EDA",
}


def classify_valuation_profile(
    sector: str,
    revenue_cagr: float,
    fcf_margin: float,
    debt_to_equity: float,
    is_pre_revenue: bool = False,
    revenue_base: float | None = None,
    *,
    gross_margin: float | None = None,
) -> str:
    """
    Auto-classify a company into the most appropriate valuation profile given
    its sector and key financial characteristics.

    Returns the profile key string to look up in INDUSTRY_VALUATION_PROFILES.
    Falls back to the sector's explicit default in _SECTOR_PROFILE_DEFAULT
    when no ladder branch matches (never order-dependent).

    ``gross_margin`` is KEYWORD-ONLY and OPTIONAL, so every positional caller
    keeps working unchanged. It is read by exactly one rung today (the Consumer
    luxury rung) and the convention there is the one the growth-reinvestment
    charge already established: **None means "not measured" and fails closed.**
    An absent gross margin never promotes a name onto a better methodology,
    because a rung that treats an unmeasured margin as a low one and a rung that
    treats it as a high one are both guesses, and only one of them is silent.
    Measured availability on the golden basket: 14 of 14 fixtures carry both
    ``gross_profit`` and ``cost_of_revenue`` on the most recent series row
    (``scratchpad/probe_gross_margin.py``, one subprocess per fixture), so None
    is a real-data-gap path rather than a common one -- but it is a path, and
    ``_gross_margin`` returns None whenever revenue is non-positive or neither
    gross profit nor cost of revenue is present.
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
        # ── MONOTONIC LADDER (2026-09-18) ───────────────────────────────────
        # The invariant, in the owner's words: **"A higher margin must never
        # degrade the valuation methodology to a lower-tier profile."**
        #
        # The pre-change ladder was a BAND-PASS, and a band-pass has an exit on
        # both sides. Its first rung was
        # `0.0 <= revenue_cagr < 0.40 and 0.05 <= fcf_margin < 0.18`, so a name
        # whose FCF margin IMPROVED past 0.18 fell out of the top of the band,
        # missed every later rung whose own bound it no longer satisfied, and
        # landed wherever the fall-through happened to be. Gridded over
        # 14 CAGRs x 17 FCF margins (`scratchpad/probe_consumer_ladder.py`, log
        # `scratchpad/consumer_ladder.log`) that produced THREE places where a
        # better number bought a worse methodology:
        #
        #   (i)   cagr in [0.03, 0.05), fcf >= 0.18
        #           Apparel / Athletic Wear -> Household / Personal
        #         The `< 0.05` Household rung stood ABOVE the `fcf >= 0.15`
        #         Luxury rung, so the highest-margin cell in that CAGR band
        #         resolved to the weakest profile in the sector.
        #   (ii)  cagr >= 0.40, fcf in [0.05, 0.15)
        #           Apparel / Athletic Wear -> Traditional Retail
        #         The documented ONON case: growing faster, at an unchanged
        #         margin, demoted the name two tiers.
        #   (iii) cagr > 0.15, fcf in [0.01, 0.05)
        #           Membership / Subscription Retail -> Traditional Retail
        #
        # (i) and (ii) are fixed below. (iii) is NOT, and the reason is stated
        # rather than hidden: the only rung that would catch a >15% grower is
        # Consumer Growth, whose `fcf >= 0.15` conjunct is load-bearing -- it is
        # the gate that keeps a hyper-growth name from being rewarded for growth
        # it does not convert to cash, which is the defect the growth-
        # reinvestment charge exists to price. Promoting on CAGR alone would
        # reopen it. Widening the Membership rung's own CAGR bound instead would
        # classify a 20% grower as a warehouse club, which is a structural claim
        # about recurring membership-fee economics and not a claim about growth.
        # Neither repair is obviously right, so (iii) is pinned as a measured
        # residual by
        # tests/test_consumer_monotonic_ladder.py::TestTheKnownResiduals
        # and left for an owner decision. It is a CAGR-axis step; the invariant
        # this ladder enforces is on the MARGIN axes.
        #
        # RUNG ORDER IS THE DESIGN. Every rung below is either a lower bound on
        # an improving metric or a structural test, and the rungs are ordered
        # strongest-methodology-first so that satisfying a harder test can never
        # be pre-empted by an easier one. Where a rung keeps an UPPER bound, the
        # bound is on a rung whose fall-through is a BETTER profile, not a worse
        # one -- which is what makes it not a cliff.
        #
        # Financial-metric routing order (when no profile override):

        # Rung 1 — Consumer Growth: fast-growing consumer brand (CAGR >= 15%)
        # WITH a strong FCF margin. Promoted to the top of the ladder from
        # position 4 because a rung that returns the sector's best methodology
        # must be tested before one that returns a merely adequate one; at
        # cagr in [0.15, 0.40) and fcf in [0.15, 0.18) the Apparel band used to
        # win purely by standing first, which is an ordering artifact and not a
        # judgement. Its `fcf >= 0.15` conjunct is DELIBERATELY RETAINED and not
        # replaced by the owner's unconditioned `cagr >= 0.15`: see (iii) above.
        # 3-method profile: DCF 50% + EV/Revenue 30% + EV/EBITDA 20%.
        if revenue_cagr >= 0.15 and fcf_margin >= 0.15:
            return "Consumer Growth"
        # Rung 2 — Luxury Goods on GROSS margin. A >= 65% gross margin is a
        # pricing-power measurement and the only one in this ladder that is not
        # a cash-conversion measurement, so it earns its own rung: Hermès and
        # Estée Lauder are distinguishable from a warehouse club by what they
        # charge, not by what they convert. `is not None` is the fail-closed
        # convention documented on the signature -- an unmeasured margin does
        # not promote.
        #
        # Placed at the TOP of the margin rungs, which is the owner's stated
        # precedence ("if gross_margin >= 0.65: return Luxury Goods" ahead of
        # the FCF band). It can only ever promote: everything it pre-empts
        # below resolves to Apparel (4), Luxury (5, same answer), Household (2),
        # Traditional Retail (1) or a structural profile, and Luxury sits above
        # all of the quality ones in the tier order the ladder tests pin. So no
        # margin improvement can route through this rung into a worse
        # methodology.
        #
        # It does take two populations off the rungs below it, and both are
        # correct rather than incidental. Premium spirits and prestige beauty
        # (Diageo ~60%, Pernod ~65%, Rémy Cointreau ~70%, Estée Lauder ~74%)
        # clear 0.65 and belong on Luxury, not on `Food & Beverage` -- and note
        # that their former destination anchored on plain `P/E` at 0.50 while
        # Luxury anchors on `P/E (Premium)` at 0.50, both of which are in
        # `_PE_NORM_SWAP_LEGS`, so the Failure-2 trough-margin repair reaches
        # either. Genuine FMCG staples do NOT clear it: KO ~59%, PEP ~53%,
        # Nestlé ~48%, UN ~45%, MDLZ ~36%. So the rung does not raid F&B's
        # stated population (KO, PEP, MDLZ at a 20-25% FCF margin).
        #
        # Measured reach on the golden basket: ZERO. The only Consumer fixture
        # is COST at a 0.1284 gross margin, and the three fixtures that do clear
        # 0.65 (C38U.SI 0.6698, V 0.8036, SCHW 0.8644) are a REIT and two
        # Financials, none of which reaches this branch. So this rung is covered
        # by unit tests and by nothing else, and saying so is the point: the
        # basket cannot arbitrate it.
        if gross_margin is not None and gross_margin >= 0.65:
            return "Luxury Goods"
        # Rung 3 — Apparel / Athletic Wear: brand-driven athletic/apparel,
        # mid-to-high growth, mid-FCF margins.
        # CAGR bound raised to <0.40 to capture fast-growing brands (SKX ~25%,
        # CROX ~30%); ONON at ~50% is past it and is pinned by override.
        # FCF threshold capped at <0.18 to separate from luxury (Hermès, LVMH:
        # FCF 20–35%) -- and that cap is NOT a cliff any more. Before the
        # migration the rung it fell through to at `cagr < 0.05` was
        # Household / Personal, the weakest methodology in the sector, which is
        # cliff (i). It now falls through to rung 5, Luxury Goods, a strictly
        # better tier.
        # MUST come before Food & Beverage to prevent misclassifying
        # NKE/LULU/VFC/ONON. NKE: CAGR ~3%, FCF ~10%; LULU: CAGR ~15%,
        # FCF ~16%; ONON: CAGR ~30%, FCF ~12%. That ordering constraint is
        # inherited from the pre-migration ladder verbatim and is PRESERVED:
        # the overlap between this band and F&B is `cagr in [0, 0.03)` at
        # `fcf in [0.15, 0.18)`, and in that overlap Apparel still wins, so no
        # name changes profile because of the migration except through the three
        # rungs this block documents.
        if 0.0 <= revenue_cagr < 0.40 and 0.05 <= fcf_margin < 0.18:
            return "Apparel / Athletic Wear"
        # Rung 4 — Food & Beverage: genuine FMCG staples — very low growth +
        # high FCF margin (KO, PEP, MDLZ: CAGR ~2–3%, FCF margin 20–25%). Its
        # stated population starts at a 20% FCF margin, above the overlap with
        # rung 3, so the `< 0.03` CAGR bound here is doing structural work and
        # not competing with the apparel band.
        # A STRUCTURAL classification (staples), not a quality tier -- which is
        # why the ladder's monotonicity tests exclude it from the tier order
        # rather than placing it in it. At `cagr < 0.03` a margin improvement
        # from 0.179 to 0.180 still moves a name off Apparel and onto this rung;
        # that adjacency predates the migration, cannot be repaired by
        # reordering without making this rung unreachable, and is pinned as a
        # measured residual rather than left implicit.
        if revenue_cagr < 0.03 and fcf_margin >= 0.15:
            return "Food & Beverage"
        # Rung 5 — Luxury Goods on CASH margin.
        if fcf_margin >= 0.15:
            return "Luxury Goods"
        # Rung 6 — Household / Personal. MOVED DOWN from position 3, which is
        # the whole of fix (i). Standing above rung 5 it caught
        # `cagr in [0.03, 0.05)` before the margin was ever read, so the
        # highest-margin names in that band got the sector's weakest profile.
        # Below rung 5 it still catches exactly what it should -- a slow grower
        # that converts less than 15% of revenue to cash -- and a margin
        # improvement can only move a name up.
        if revenue_cagr < 0.05:
            return "Household / Personal"
        # Rung 7 — Membership / Subscription Retail: warehouse clubs and
        # membership-model retailers with intentionally thin margins but strong
        # revenue scale. Signature: revenue > $50B, FCF margin 1-5%, CAGR
        # 5-15%, low leverage. COST, BJ, SAMS — these MUST NOT fall through to
        # Traditional Retail because their premium multiples (45-55x P/E) are
        # structurally justified by recurring membership fee economics, not
        # merchandise margins.
        #
        # COST IS THE FIXTURE THIS RUNG EXISTS FOR and its resolution is pinned
        # twice over: by this ladder on its own measured inputs (cagr 0.0887,
        # fcf 0.0232, revenue $275.2bn, D/E 0.3505 -> Membership) and by the
        # golden archive, which carries the same name. See the coverage warning
        # at the head of the module's ladder tests -- the archive holds the
        # ladder's ANSWER, so the golden suite cannot see this rung change.
        if (revenue_base and revenue_base > 50e9
                and 0.01 <= fcf_margin < 0.05
                and 0.04 <= revenue_cagr <= 0.15
                and debt_to_equity < 1.0):
            return "Membership / Subscription Retail"
        # Rung 8 — Automotive & EV fallback: very high capex + negative FCF
        # typical of EV ramp. A DISTRESS classification rather than a quality
        # tier, which is why it sits low and why a margin improvement out of it
        # is a promotion and not a lateral move.
        if fcf_margin < -0.05 and debt_to_equity > 1.0:
            return "Automotive & EV"
        # Rung 9 — the catch that is the whole of fix (ii). Anything arriving
        # here with a >= 5% FCF margin was excluded from every rung above by a
        # CAGR bound and not by a margin, and the only such population is
        # `cagr >= 0.40, fcf in [0.05, 0.15)`. Those names used to fall through
        # to Traditional Retail, the weakest methodology in the sector, for the
        # crime of growing fast. They now get the Apparel band they would have
        # got at a 39% CAGR.
        if fcf_margin >= 0.05:
            return "Apparel / Athletic Wear"
        # Rung 10 — Traditional Retail.
        return "Traditional Retail"

    # NOTE: "Backlog-Gated Long Cycle" is never returned from here. It is reached
    # only through dcf_agent's eligibility gate, on owner-accepted filing figures.
    if sector == "Industrials":
        # A business model is never inferred from a ratio (owner, 2026-09-21).
        # This ladder used to read: D/E > 1.5 -> Automotive (OEM); revenue CAGR
        # < 8% -> Capital Goods; else Aerospace & Defense. So a levered defence
        # prime classified as a car maker, and any industrial growing at 8% as
        # a defence contractor -- NuScale, a pre-revenue reactor designer, was
        # valued on Aerospace & Defense in the Wave 2 baseline. Car makers and
        # defence names are reached by what they ARE: a pin, the Damodaran
        # `Auto & Truck` mapping, or an industry row. Everything else unmapped
        # is priced on the sector's generic method set.
        return "Capital Goods"

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

    # Default: sector's explicit default profile (D3 — never order-dependent).
    # The previous `next(iter(profiles), "")` returned whichever profile was
    # defined first, which silently mis-routed fall-through tickers.
    default = _SECTOR_PROFILE_DEFAULT.get(sector_lookup, "")
    if default and default in profiles:
        _log.warning(
            "[classify_valuation_profile] No ladder branch matched for "
            "sector=%r (cagr=%.2f, fcf=%.2f, d/e=%.2f) — using explicit "
            "default profile %r",
            sector, revenue_cagr, fcf_margin, debt_to_equity, default,
        )
        return default
    # Table miss — should never happen (conformance test covers it).
    _log.warning(
        "[classify_valuation_profile] _SECTOR_PROFILE_DEFAULT has no valid "
        "entry for sector=%r — falling back to first-defined profile "
        "(order-dependent!)", sector,
    )
    return next(iter(profiles), "")


def get_valuation_profile(
    sector: str,
    revenue_cagr: float,
    fcf_margin: float,
    debt_to_equity: float = 0.0,
    is_pre_revenue: bool = False,
    revenue_base: float | None = None,
    *,
    gross_margin: float | None = None,
) -> tuple[str, dict]:
    """
    Classify and return (profile_name, profile_dict) for the given sector + company data.
    Returns ("", {}) if sector is unrecognised.

    ``gross_margin`` is keyword-only and optional and is forwarded verbatim; see
    ``classify_valuation_profile`` for what None means and why it fails closed.
    """
    profile_key = classify_valuation_profile(
        sector, revenue_cagr, fcf_margin, debt_to_equity, is_pre_revenue,
        revenue_base=revenue_base, gross_margin=gross_margin,
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
    "AAPL":  ("Tech", "Hyperscaler / Tech Conglomerate", "Computers/Peripherals", "Hardware + services + AI capex; Tech WACC applies"),

    # ── Semiconductor (separate sector from Tech) ─────────────────────────
    # Fabless
    "CRWV":    ("Tech", "AI Infrastructure / Neocloud", "AI Infrastructure", "CoreWeave — GPU neocloud"),
    "NBIS":    ("Tech", "AI Infrastructure / Neocloud", "AI Infrastructure", "Nebius Group — GPU neocloud"),
    "IREN":    ("Tech", "AI Infrastructure / Neocloud", "AI Infrastructure", "IREN — AI/HPC hosting on contracted power"),
    "APLD":    ("Tech", "AI Infrastructure / Neocloud", "AI Infrastructure", "Applied Digital — AI/HPC datacenter hosting"),
    "WULF":    ("Tech", "AI Infrastructure / Neocloud", "AI Infrastructure", "TeraWulf — AI/HPC hosting on contracted power"),
    "NVDA":  ("Semiconductor", "Fabless",     "Semiconductor",                   "Fabless — AI GPU"),
    "AVGO":  ("Semiconductor", "Fabless",     "Semiconductor",                   "Fabless — custom ASIC + networking"),
    "QCOM":  ("Semiconductor", "Fabless",     "Semiconductor",                   "Fabless — mobile/edge AI"),
    "AMD":   ("Semiconductor", "Fabless",     "Semiconductor",                   "Fabless — CPU/GPU"),
    "MRVL":  ("Semiconductor", "Fabless",     "Semiconductor",                   "Fabless — networking/storage"),
    "ARM":   ("Semiconductor", "",     "Semiconductor",                   "Fabless — IP licensing/royalties"),
    # IDM / Foundry
    "MU":    ("Semiconductor", "Memory / DRAM-NAND", "Memory (DRAM/NAND)", "Micron Technology — memory IDM; 18x normalised EPS"),
    "000660.KS": ("Semiconductor", "Memory / DRAM-NAND", "Memory (DRAM/NAND)", "SK Hynix — DRAM/HBM leader; averaged-year P/E"),
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
    "META":  ("Tech", "Hyperscaler / Tech Conglomerate", "Software (Entertainment)", "Meta — Ads + AI capex + Reality Labs; hyperscaler-like capex lens"),

    # ── Communication Services → Telco ────────────────────────────────────────
    "T":     ("Telco", "",             "Telecom. Services",               "AT&T — high leverage; Telco WACC 5.5%"),
    "VZ":    ("Telco", "Stable Growth", "Telecom (Wireless)",              "Verizon"),
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
    # v3.21 — additional tickers surfaced from card-pipeline audit (were
    # rendering generic valuation cards because TICKER_SECTOR_LOOKUP didn't
    # cover them, so strategic_router pre-classification skipped them and
    # framework_metrics_dispatch returned {})
    # v3.23 — reclassified after dry-run showed mismatched KPI vocabulary:
    #   • GTM was Levered Subscription — extractor expected arpu_monthly_usd_growth /
    #     subscriber_growth_yoy / fcf_debt_service_coverage which GTM (enterprise
    #     B2B SaaS) does not disclose. Mature SaaS expects nrr_pct / rule_of_40_score /
    #     fcf_margin_pct / gross_margin_pct which GTM DOES disclose.
    #   • TRI was Ad / Consulting — extractor expected agency-billings KPIs which
    #     Thomson Reuters does not disclose. TRI's revenue is mostly recurring
    #     software/data subscription (Westlaw, Refinitiv) which fits Mature SaaS.
    #   • LIF kept as Hyper-Growth Platform — Life360 IS consumer subscription
    #     with disclosed subscriber/ARPU metrics that fit the platform spec.
    "TRI":   ("ProfessionalServices", "IT Services", "Information Services", "Thomson Reuters — Westlaw + Refinitiv + Reuters; recurring B2B information-services subscription"),
    "GTM":   ("Tech", "Mature SaaS", "Software (System & Application)", "ZoomInfo Technologies — enterprise B2B SaaS (sales intelligence); slowing growth + strong FCF + PE-era leverage"),
    "LIF":   ("Tech", "Hyper-Growth Platform", "Software (Internet)",            "Life360 — consumer-subscription family-tracking platform; high subscriber growth"),

    # ── Consumer Discretionary ────────────────────────────────────────────────
    "AMZN":  ("Tech", "Hyperscaler / Tech Conglomerate", "Software (Internet)", "Amazon — AWS + retail + ads + AI capex; AWS > 60% EBIT"),
    "BABA":  ("Tech", "China Internet Platform", "Software (Internet)",   "Alibaba ADR — owner profile 2026-09-26; accepted Gemini SOTP"),
    "JD":    ("Tech", "China Internet Platform", "Retail (General)",      "JD.com — owner profile 2026-09-26 (retail, logistics, new businesses as parts)"),
    # ── Travel & Dining (profile override) ────────────────────────────────
    "MCD":   ("Consumer", "Travel & Dining", "Restaurant/Dining",        "McDonald's — franchise royalty model"),
    "SBUX":  ("Consumer", "Travel & Dining", "Restaurant/Dining",        "Starbucks — global coffeehouse"),
    "DIS":   ("Consumer", "Travel & Dining", "Entertainment",            "Disney: content/parks/cruise — Travel & Dining"),
    "ABNB":  ("Consumer", "Travel & Dining", "Hotel/Gaming",             "Airbnb — asset-light travel platform"),
    "BKNG":  ("Consumer", "Travel & Dining", "Hotel/Gaming",             "Booking Holdings — OTA platform"),
    # ── Apparel & Footwear ────────────────────────────────────────────────
    # NKE, LULU and BIRK are pinned. The pins STAY, but the reason they were
    # originally needed is now history and is recorded as such rather than left
    # standing as if it still applied -- a stale justification on a live pin is
    # how the next reader talks themselves out of the pin, or out of the fix.
    #
    # WHAT THE LADDER USED TO DO. It was a BAND-PASS below 5% CAGR, not a
    # monotonic ladder: `0.05 <= fcf_margin < 0.18` returned Apparel / Athletic
    # Wear, and anything at or above 0.18 fell through to Household / Personal.
    # Measured at NKE's ~3% CAGR, 0.179 -> Apparel (DCF 0.30, and the only
    # Consumer profile carrying a `P/E (norm)` leg at 0.20) while 0.180 ->
    # Household (DCF 0.20, anchor plain trailing `P/E` at 0.40). So a name whose
    # margin IMPROVED past 18% was DEMOTED: DCF weight cut by a third, the sole
    # normalized leg lost, and 40% of the blend moved onto unadjusted trailing
    # earnings. The two profiles share exactly one method (`EV/EBITDA`), so it
    # was never a cosmetic relabelling.
    #
    # WHY THE `< 0.18` BOUND COULD NOT SIMPLY BE DELETED. Its own comment says
    # it exists "to separate from luxury (Hermès, LVMH: FCF 20-35%)", and
    # removing it reclassifies Hermès-, LVMH- and Richemont-like names from
    # Luxury Goods to Apparel / Athletic Wear. Fixing the ladder properly needed
    # a gross-margin test ahead of the FCF test, and `classify_valuation_profile`
    # took no gross margin. Pinning the anchor names was the safe move while that
    # signature change was outstanding.
    #
    # BOTH HALVES HAVE NOW LANDED (2026-09-18). `classify_valuation_profile`
    # takes a keyword-only `gross_margin`, the luxury rung reads it, and the
    # Household rung moved BELOW the `fcf >= 0.15` Luxury rung -- so at a 3%
    # CAGR an 0.180 FCF margin now resolves to Luxury Goods rather than
    # Household / Personal, and the demotion-for-improving is gone without the
    # `< 0.18` bound being deleted. The pins are retained because they are
    # owner-directed anchors and because a pin is a stronger guarantee than a
    # rung: it does not depend on the margin series the extractor managed to
    # fetch. What the pins no longer do is stand between NKE and a wrong
    # profile if they were removed -- verified by
    # tests/test_consumer_monotonic_ladder.py, which resolves NKE's own measured
    # shape through the ladder with the pin bypassed.
    #
    # The NOTE STRING below is left byte-identical and is now historical: it still
    # says the ladder "demotes it past an 18% FCF margin", which was true at
    # 7530577 and is false now. Same treatment as ONON's note. A pin's note is
    # owner-authored documentation of why the pin exists, and rewriting it here
    # would erase the record of the defect the pin was bought against -- so the
    # correction lives in this comment, above the tuple, and not inside it.
    "NKE":   ("Consumer", "Apparel / Athletic Wear", "Apparel", "Nike — pinned; the sub-5%-CAGR ladder is a band-pass that demotes it past an 18% FCF margin"),
    "LULU":  ("Consumer", "Apparel / Athletic Wear", "Athletic Apparel", "Lululemon — 4.50x Q5-Q8 EV/EBITDA"),
    "BIRK":  ("Consumer", "Apparel / Athletic Wear", "Footwear", "Birkenstock — DCF, 9.5pc WACC / 2.5pc terminal growth"),
    "FLUT":  ("Consumer", "Online Gaming / Sports Betting", "Sports Betting", "Flutter Entertainment — 11.75x NTM+4 EBITDA (US)"),
    "DKNG":  ("Consumer", "Online Gaming / Sports Betting", "Sports Betting", "DraftKings — EV/Sales (NTM+1) blended with an EV/EBITDA-exit DCF"),
    # ONON's pin text is corrected here rather than quietly left wrong. It used
    # to say that "at a 50% CAGR the ladder returns Traditional Retail, whose
    # method set has an EMPTY intersection with Luxury Goods". Since the
    # monotonic ladder that is FALSE: a >= 0.40 CAGR at ONON's ~12% FCF margin
    # now hits the rung-9 catch and returns Apparel / Athletic Wear, which is
    # the profile ONON would get at a 39% CAGR. The CAGR cliff that made 50%
    # growth worse than 39% growth was fix (ii) and it is closed. The pin still
    # earns its place on the OTHER half of its original reasoning -- ~30% CAGR
    # is past the 15% fast-grower branch's intent, and Consumer Growth is the
    # profile that prices a hyper-growth platform rather than an apparel band.
    "ONON":  ("Consumer", "Consumer Growth", "Apparel", "On Holding AG ADR — pinned to Consumer Growth: ~30% CAGR is past the 15% fast-grower branch's intent, and at a 50% CAGR the ladder returns Traditional Retail, whose method set has an EMPTY intersection with Luxury Goods. Trade accepted deliberately: Consumer Growth carries no normalized leg, so this swaps Apparel's `P/E (norm)` 0.20 for DCF weight 0.30 -> 0.50"),
    "DECK":  ("Consumer", "",          "Apparel",                         "Deckers Outdoor — UGG/HOKA"),
    "VFC":   ("Consumer", "",          "Apparel",                         "VF Corp — North Face/Vans/Timberland"),
    "GPS":   ("Consumer", "",          "Apparel",                         "Gap Inc"),
    # ── Luxury & Beauty (profile override) ────────────────────────────────
    # EL is pinned to Luxury Goods because it is the clearest case of the
    # two-sided trailing-earnings exposure the Consumer ladder creates, and it
    # was absent from this table entirely. At a TROUGH (~-2% CAGR) it
    # classifies as Household / Personal, anchored 40% on plain trailing `P/E`;
    # at a PEAK (~8% CAGR, 18-20% FCF) as Luxury Goods, anchored 50% on
    # `P/E (Premium)` — which dispatches to the SAME trailing-12m branch. The
    # engine's own comment: "they differ only in documentation intent, not
    # earnings source." So EL sits on unadjusted trailing earnings at both ends
    # of the cycle: depressed earnings at the trough, peak earnings at the top.
    # Pinning does not fix that — it stops the profile itself from flipping with
    # the cycle, which is the precondition for routing the leg to `P/E (norm)`
    # on a margin-deviation test.
    #
    # The intended name was "Prestige Beauty & Personal Care". No such profile
    # exists in INDUSTRY_VALUATION_PROFILES for any sector, and an unresolved
    # override is not an error: the D3 guard logs a warning, sets
    # `_profile_fallback_used`, records `override_unresolved` in the routing
    # trace and KEEPS the classified profile — so a bad pin would silently
    # defeat itself rather than fail loudly in a test. Luxury Goods is the
    # existing profile that matches.
    "EL":    ("Consumer", "Luxury Goods", "Beauty & Personal Care", "Estée Lauder — pinned; first ticker on this profile. Trough classifies as Household / Personal and peak as Luxury Goods, both anchored on trailing TTM earnings"),
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
    "WMT":   ("Consumer", "Traditional Retail", "Retail (General)",       "Walmart"),
    "TGT":   ("Consumer", "",          "Retail (General)",                "Target"),
    "HD":    ("Consumer", "",          "Retail (Building Supply)",        "Home Depot"),
    "TJX":   ("Consumer", "",          "Retail (Special Lines)",          "TJX Companies — off-price retail"),
    "GME":   ("Consumer", "",          "Retail (Special Lines)",          "GameStop — declining retail; Bitcoin treasury pivot"),
    # ── Tech platforms (NOT Consumer) ─────────────────────────────────────
    "UBER":  ("Tech", "",              "Software (Internet)",             "Uber: platform marketplace — Tech WACC (marketplace, not logistics)"),
    "GRAB":  ("Tech", "",              "Software (Internet)",             "Grab Holdings — SEA super-app platform"),
    "PDD":   ("Tech", "China Internet Platform", "Software (Internet)",   "PDD Holdings — owner profile 2026-09-26; 20-F filer (RMB reporting)"),
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
    "PSA":   ("RealEstate", "REIT",              "R.E.I.T.",                         "Public Storage REIT — self storage"),
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
    # Zoetis — animal-health pharma (spun out of Pfizer 2013). $9B revenue,
    # ~35% op margin, sub-10% growth, no clinical pipeline of human drugs.
    # Fits Large Cap Pharma archetype; without this override the LLM
    # classifier was routing ZTS to "Managed Care" because the production
    # sector classifier was returning "HealthcareServices" and the only
    # default profile registered for that sector is Managed Care
    # (strategic_router._SECTOR_PROFILE_DEFAULT). Result was every Managed
    # Care KPI (medical_loss_ratio, members_yoy, medicare_advantage_mix_pct)
    # showing FALLBACK USED on the dashboard.
    "ZTS":   ("Biopharma", "Large Cap Pharma",  "Drugs (Pharmaceutical)",    "Zoetis — animal health pharma; routed to Large Cap Pharma to avoid Managed Care misclassification"),
    "NVO":   ("Biopharma", "",               "Drugs (Pharmaceutical)", "Novo Nordisk ADR — GLP-1/obesity; 20-F filer (DKK reporting currency)"),
    "TXG":   ("Biopharma", "CDMO / Life Science Tools", "Healthcare Products", "10X Genomics — single-cell/spatial genomics instruments; tools co, NOT drug developer"),
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
    "VST":   ("Energy",    "Merchant Power", "Power",                    "Vistra Energy — competitive power gen; NOT regulated utility"),
    # Wave 2: FMP gives one label, `Independent Power Producers`, to contracted
    # generators and merchants alike. The row keeps the contracted default; the
    # US merchants are named.
    "CEG":   ("Energy",    "Merchant Power", "Power",                    "Constellation Energy — merchant nuclear fleet; PTC floor + data-centre contracts"),
    "NRG":   ("Energy",    "Merchant Power", "Power",                    "NRG Energy — integrated merchant generation + retail"),
    "NEE":   ("Energy",    "Regulated Utility", "Utility (General)",     "NextEra Energy"),
    "DUK":   ("Energy",    "Regulated Utility", "Utility (General)",     "Duke Energy"),
    "SO":    ("Energy",    "Regulated Utility", "Utility (General)",     "Southern Company"),
    "XEL":   ("Energy",    "Regulated Utility", "Utility (General)",     "Xcel Energy"),
    "AWK":   ("Energy",    "Regulated Utility", "Utility (Water)",       "American Water Works"),
    "PCG":   ("Energy",    "Regulated Utility", "Utility (General)",     "PG&E"),
    "ENPH":  ("Energy",    "Clean Tech / Power Equipment OEM", "Green & Renewable Energy", "Enphase Energy — solar microinverters"),
    "FSLR":  ("Energy",    "Clean Tech / Power Equipment OEM", "Green & Renewable Energy", "First Solar"),
    # FMP labels Bloom `Electrical Equipment & Parts`, shared with hundreds of
    # unrelated industrials, so it is reached by pin. Owner, 2026-09-21: a
    # hardware OEM and solutions provider, not a licensor.
    "BE":    ("Energy",    "Clean Tech / Power Equipment OEM", "Green & Renewable Energy", "Bloom Energy — SOFC fuel-cell OEM; equipment, installation and long-term service"),

    # ── Industrials ───────────────────────────────────────────────────────────
    # Wave 3 (owner framework, 2026-09-22). One FMP label; the row defaults to
    # Defense Primes and the rest are named here.
    "LMT":   ("Industrials", "Defense Primes",  "Aerospace/Defense",  "Lockheed Martin"),
    "RTX":   ("Industrials", "Defense Primes",  "Aerospace/Defense",  "RTX Corp — Raytheon + Pratt & Whitney + Collins; owner places it with the primes"),
    "NOC":   ("Industrials", "Defense Primes",  "Aerospace/Defense",  "Northrop Grumman"),
    "GD":    ("Industrials", "Defense Primes",  "Aerospace/Defense",  "General Dynamics"),
    "LHX":   ("Industrials", "Defense Primes",  "Aerospace/Defense",  "L3Harris — defence electronics"),
    "BA":    ("Industrials", "Commercial Aerospace & Engines",  "Aerospace/Defense",  "Boeing — SOTP + normalised EV/EBIT; FCF negative through production crises"),
    "HWM":   ("Industrials", "Niche Aerospace Components",  "Aerospace/Defense",  "Howmet — engineered forgings and fasteners"),
    "TDG":   ("Industrials", "Niche Aerospace Components",  "Aerospace/Defense",  "TransDigm — proprietary aftermarket parts, debt-funded bolt-ons"),
    "HEI":   ("Industrials", "Niche Aerospace Components",  "Aerospace/Defense",  "HEICO — aftermarket PMA parts"),
    "KTOS":  ("Industrials", "Defense Tech & Space",  "Aerospace/Defense",  "Kratos — drones, hypersonics, microwave electronics"),
    "AVAV":  ("Industrials", "Defense Tech & Space",  "Aerospace/Defense",  "AeroVironment — tactical UAS and loitering munitions"),
    "RKLB":  ("Industrials", "Defense Tech & Space",  "Aerospace/Defense",  "Rocket Lab — launch and space systems"),
    "CAT":   ("Industrials", "",  "Machinery",          "Caterpillar"),
    "DE":    ("Industrials", "",  "Machinery",          "Deere & Company"),
    "GE":    ("Industrials", "Commercial Aerospace & Engines",  "Aerospace/Defense", "GE Aerospace — engines; P/E and EV/EBITDA on aftermarket service margins"),
    "GEV":   ("Industrials", "Capital Goods",  "Electrical Equipment", "GE Vernova — wind/gas turbine OEM + grid electrification; book-to-bill driven"),
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
    "XOM":   ("Resources",  "Integrated Oil & Gas", "Oil/Gas (Integrated)",  "ExxonMobil — integrated O&G (Wave 1, 2026-09-20)"),
    "CVX":   ("Resources",  "Integrated Oil & Gas", "Oil/Gas (Integrated)",  "Chevron — integrated (Wave 1, 2026-09-20)"),
    "COP":   ("Resources",  "Upstream Oil & Gas",  "Oil/Gas (E&P)",         "ConocoPhillips — pure-play E&P"),
    "EOG":   ("Resources",  "Upstream Oil & Gas",  "Oil/Gas (E&P)",         "EOG Resources"),
    "LEU":   ("Resources",  "",  "Uranium",               "Centrus Energy — uranium enrichment (SWU contracts); NOT power generation"),

    # ── Crypto (multimodal Family G — sub-industry router by ticker) ──────────
    "MSTR":  ("Crypto", "BTC Treasury / Proxy",  "Diversified",  "MicroStrategy — BTC treasury company (Saylor playbook)"),
    "MARA":  ("Crypto", "Digital Asset Mining",  "Diversified",  "Marathon Digital — BTC miner"),
    "RIOT":  ("Crypto", "Digital Asset Mining",  "Diversified",  "Riot Platforms — BTC miner"),
    "CLSK":  ("Crypto", "Digital Asset Mining",  "Diversified",  "CleanSpark — BTC miner"),
    "CIFR":  ("Crypto", "Digital Asset Mining",  "Diversified",  "Cipher Mining — low-cost BTC miner"),
    "BTDR":  ("Crypto", "Digital Asset Mining",  "Diversified",  "Bitdeer — crypto mining hardware (SEALMINER ASICs) + hosting; OEM + miner hybrid"),
    "COIN":  ("Crypto", "Crypto Exchange",        "Diversified",  "Coinbase — institutional crypto exchange + custody"),
    "HOOD":  ("Crypto", "Crypto Exchange",        "Diversified",  "Robinhood — crypto-arm exchange (also Brokerage profile candidate)"),

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
    "SMR":   ("Energy", "Energy Tech Licensor", "Electrical Equipment", "NuScale Power — reactor DESIGN licensor; pre-revenue, so every earnings leg is uncomputable"),

    # PONY: Pony.AI — AV software platform; revenue from robotaxi licences + software
    #       Damodaran would put in Transportation, but business model is software-first
    "PONY":  ("Tech", "",              "Software (System & Application)", "Pony.AI — AV software platform; Tech WACC applies (not Transportation)"),

    # CHAGEE: alternate long-form ticker lookup (canonical ticker is CHA)
    "CHAGEE":("Consumer", "",          "Restaurant/Dining",          "Chagee Holdings — alias; use CHA"),

    # ── Hong Kong (HKEX) — canonical "NNNNN.HK" format ───────────────────────
    # Sectors use internal pipeline names; sub-sector is human-readable label for screener display.
    # Source: user-provided classification table (100 HKEX well-known stocks, April 2026)

    # Technology
    "00700.HK": ("Tech", "China Internet Platform", "Internet Platform", "Tencent Holdings — owner profile 2026-09-26"),
    "09988.HK": ("Tech", "China Internet Platform", "Software (Internet)", "Alibaba Group HK listing — owner profile 2026-09-26"),
    "03690.HK": ("Tech", "China Internet Platform", "On-Demand Commerce", "Meituan — owner profile 2026-09-26 (was Local Services & Instant Retail)"),
    "09618.HK": ("Tech", "China Internet Platform", "E-commerce",       "JD.com HK listing — owner profile 2026-09-26"),
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
    # Wave 2 (owner, 2026-09-21). Baseload, priority dispatch, stable payout: a
    # utility, though 40-55% of volume clears through market-based trading, so
    # its multiples take the owner-set discount in valuation_constants.json.
    # Its EV-based legs went NEGATIVE on the IPP/Merchant tables (base IV -3.24):
    # net debt of HK$391bn funds reactors under construction that earn no
    # EBITDA yet. The Regulated Utility table is equity-based throughout.
    "01816.HK": ("Energy",      "Regulated Utility",  "Nuclear Power",  "CGN Power — NDRC-tariff nuclear operator"),
    # FMP labels Power Assets `Independent Power Producers`; it is a holding
    # company of regulated networks (UK, Australia, HK Electric).
    "00006.HK": ("Energy",      "Regulated Utility",  "Utility (General)", "Power Assets — regulated network holdings"),
    # Wave 3 (owner framework, 2026-09-22): the three HK aerospace names, each
    # on its own method set. All carry the one FMP label.
    "02357.HK": ("Industrials", "Aerospace Holdco (HK)",         "Aerospace/Defense", "AviChina — holding of AVIC listed subsidiaries; SOTP + DCF, Forward P/E check"),
    "02507.HK": ("Industrials", "General Aviation (HK)",         "Aerospace/Defense", "Cirrus Aircraft — SR-series and Vision Jet; Forward P/E + EV/Sales, backlog DCF"),
    "00232.HK": ("Industrials", "GA Engines & Aftermarket (HK)", "Aerospace/Defense", "Continental Aerospace Technologies — GA piston engines and parts; EV/EBITDA + P/B"),

    # Financials
    "00005.HK": ("Financials",  "Money Center Bank (EU)",  "Banking",  "HSBC Holdings"),
    "01299.HK": ("Financials",  "",  "Insurance",                "AIA Group"),
    "02318.HK": ("Financials",  "",  "Insurance",                "Ping An Insurance"),
    "03988.HK": ("Financials",  "EM Bank",          "Banking",        "Bank of China"),
    "01398.HK": ("Financials",  "EM Bank",          "Banking",        "ICBC"),
    "00939.HK": ("Financials",  "EM Bank",          "Banking",        "China Construction Bank"),
    "03968.HK": ("Financials",  "EM Bank",          "Banking",        "China Merchants Bank"),
    "02628.HK": ("Financials",  "",  "Insurance",                "China Life Insurance"),
    "01288.HK": ("Financials",  "EM Bank",          "Banking",        "Agricultural Bank of China"),
    "00998.HK": ("Financials",  "EM Bank",          "Banking",        "CITIC Bank"),
    "03328.HK": ("Financials",  "EM Bank",          "Banking",        "Bank of Communications"),
    "01658.HK": ("Financials",  "EM Bank",          "Banking",        "Postal Savings Bank of China"),
    "00388.HK": ("Financials",  "",  "Exchange",                 "Hong Kong Exchanges (HKEX)"),
    "02388.HK": ("Financials",  "Regional Bank",    "Banking",        "BOC Hong Kong — HK-domiciled"),
    "00011.HK": ("Financials",  "Regional Bank",    "Banking",        "Hang Seng Bank — HK-domiciled"),
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
    # 02020.HK pinned for the same band-pass reason as NKE. Measured at its
    # ~12% CAGR / ~18% FCF margin it classifies as Luxury Goods TODAY, which
    # anchors 50% on `P/E (Premium)` — the trailing-12m branch. Pinning it to
    # Apparel / Athletic Wear moves it to DCF 0.30 plus the Consumer sector's
    # only `P/E (norm)` leg at 0.20, so for Anta the pin ADDS a normalized leg
    # rather than trading one away (the opposite of the ONON trade).
    #
    # 02331.HK (Li Ning) and 01368.HK (Xtep) are the same domestic-sportswear
    # archetype and are deliberately left unpinned here: only the four anchor
    # names in the consumer-discretionary brief were authorised. They remain on
    # the band-pass and are the obvious next rows if this pin holds up.
    "02020.HK": ("Consumer", "Apparel / Athletic Wear", "Sportswear", "Anta Sports — pinned; premium domestic brand, P/E ~25x near US level. Classified as Luxury Goods (50% trailing `P/E (Premium)`) before the pin"),
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
    # Smartphones, IoT and internet services are the business; the EV arm is
    # the smallest of the four segments. Pricing the whole company on auto
    # comps put it 62% from consensus.
    "01810.HK": ("Tech", "Consumer Electronics / Hardware Ecosystem", "Electronics/IoT", "Xiaomi — hardware ecosystem with a services attach and an EV arm"),
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

    # SGX fallback. SGX_TICKER_SECTOR_LOOKUP's field [1] is primarily
    # screener-display metadata and many hints ("Airline", "AssetMgmt",
    # "Conglomerate") are NOT valuation-profile keys — so the hint is
    # returned ONLY when it resolves to a real entry in
    # INDUSTRY_VALUATION_PROFILES for that sector. Anything else falls
    # through to in-situ classification exactly as before.
    #
    # Without this, D05.SI / O39.SI / U11.SI had no deterministic profile
    # and were left to the LLM classifier, which routed DBS to the US
    # "Money Center Bank" row (target ROE 12% / CoE 9.0% / P/TBV 1.4x)
    # instead of the Singapore calibration.
    sgx = SGX_TICKER_SECTOR_LOOKUP.get(ticker.upper())
    if sgx:
        _sector, _hint = sgx[0], sgx[1]
        _key = "RealEstate" if _sector == "REIT" else _sector
        if _hint and INDUSTRY_VALUATION_PROFILES.get(_key, {}).get(_hint):
            return _sector, _hint
        # SGX REITs carry a SUB-SECTOR hint ("Retail", "DataCentre",
        # "India") rather than a profile name, so the check above never
        # matches and all 25 of them used to fall through to the runtime
        # LLM classifier. They are all S-REITs; the hint selects the DDM
        # calibration downstream, not the profile.
        if _sector == "REIT":
            return _sector, "S-REIT"
        return _sector, ""
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


# ═══════════════════════════════════════════════════════════════════════════════
# Market registry — one entry per catchment
# ═══════════════════════════════════════════════════════════════════════════════
#
# Replaces the `is_hk: bool` branch, which could express exactly two markets
# and gave Singapore whichever one it was not. Keys match
# `regional_comps.MARKETS` so a live comp set and a static fallback are always
# looked up under the same name.
#
# Country risk premia are added to the US Damodaran sector base, the same
# derivation `_HK_CHINA_CRP` and `_SG_CRP` already use. They are sovereign
# ratings translated to an equity premium, not free parameters:
#
#   US    AA+   0.00%   the base itself
#   SG    AAA   0.50%   already in use
#   KR    AA    0.50%   comparable sovereign standing to Singapore
#   JP    A+    0.60%   mature market, weaker sovereign rating
#   CN    A+    1.50%   mainland; same China risk the HK constant carries
#   HK    -     1.50%   HK-listed China exposure (existing constant)
#
# `peer_multiples: None` is deliberate and load-bearing. Singapore has no
# static table, and the correct behaviour is to fall through to "no static
# comps" rather than to borrow another market's economics. A caller that gets
# {} keeps its own default and can tell that it did.
_JP_CRP = 0.006
_KR_CRP = 0.005
_CN_CRP = 0.015

MARKET_REGISTRY: dict[str, dict] = {
    "US": {
        "label":          "United States",
        "exchanges":      ("NASDAQ", "NYSE", "AMEX"),
        "crp":            0.0,
        "peer_multiples": "SECTOR_PEER_MULTIPLES",
        "sector_wacc":    None,            # SECTOR_WACC is the base itself
    },
    "HKSE": {
        "label":          "Hong Kong",
        "exchanges":      ("HKSE",),
        "crp":            _HK_CHINA_CRP,
        "peer_multiples": "HK_SECTOR_PEER_MULTIPLES",
        "sector_wacc":    "HK_SECTOR_WACC",
    },
    "SES": {
        "label":          "Singapore",
        "exchanges":      ("SES",),
        "crp":            _SG_CRP,
        "peer_multiples": None,            # none authored — do NOT borrow US
        "sector_wacc":    "SG_SECTOR_WACC",
    },
    "JPX": {
        "label":          "Japan",
        "exchanges":      ("JPX",),
        "crp":            _JP_CRP,
        "peer_multiples": None,
        "sector_wacc":    None,            # derived from base + CRP
    },
    "KSC": {
        "label":          "Korea",
        "exchanges":      ("KSC",),
        "crp":            _KR_CRP,
        "peer_multiples": None,
        "sector_wacc":    None,
    },
    "SHH": {
        "label":          "Shanghai",
        "exchanges":      ("SHH",),
        "crp":            _CN_CRP,
        "peer_multiples": None,
        "sector_wacc":    None,
    },
    "SHZ": {
        "label":          "Shenzhen",
        "exchanges":      ("SHZ",),
        "crp":            _CN_CRP,
        "peer_multiples": None,
        "sector_wacc":    None,
    },
}

DEFAULT_MARKET = "US"

# Exchange code -> market key, built once from the registry.
_EXCHANGE_TO_MARKET: dict[str, str] = {
    code.upper(): market
    for market, cfg in MARKET_REGISTRY.items()
    for code in cfg["exchanges"]
}

# Ticker suffix -> market, for the paths that only have a symbol.
_SUFFIX_TO_MARKET: dict[str, str] = {
    ".HK": "HKSE", ".SI": "SES", ".KS": "KSC",
    ".T": "JPX", ".SS": "SHH", ".SZ": "SHZ",
}


def resolve_market(ticker: str = "", exchange: str = "",
                   is_hk: bool | None = None) -> str:
    """Market key for a ticker or exchange. Falls back to US.

    `is_hk` is accepted so existing callers keep working while they migrate;
    it wins only when nothing more specific is available.
    """
    code = (exchange or "").strip().upper()
    if code in _EXCHANGE_TO_MARKET:
        return _EXCHANGE_TO_MARKET[code]
    if code in MARKET_REGISTRY:
        return code
    sym = (ticker or "").strip().upper()
    for suffix, market in _SUFFIX_TO_MARKET.items():
        if sym.endswith(suffix):
            return market
    if is_hk:
        return "HKSE"
    return DEFAULT_MARKET


def market_crp(market: str) -> float:
    """Country risk premium added to the US sector base."""
    return float((MARKET_REGISTRY.get(market) or {}).get("crp") or 0.0)


def market_peer_multiples(market: str) -> dict:
    """Static peer-multiple table for a market, or {} when none is authored.

    Empty is a real answer: it means this market has no hand-authored
    fallback, and the caller should rely on live comps rather than inherit
    another market's multiples.
    """
    name = (MARKET_REGISTRY.get(market) or {}).get("peer_multiples")
    return globals().get(name, {}) if name else {}


def market_sector_wacc(market: str) -> dict:
    """Pre-computed per-market WACC table, or {} to derive from base + CRP."""
    name = (MARKET_REGISTRY.get(market) or {}).get("sector_wacc")
    return globals().get(name, {}) if name else {}



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
#
# NB: `profile_hint` (field [1], e.g. "AssetMgmt", "Exchange", "Airline") is
# screener-display metadata, NOT a SECTOR_KPI_FRAMEWORK / INDUSTRY_VALUATION_PROFILES
# key — many hints have no registered profile. The card pipeline never uses this
# field as a profile_name (its consumers read only field [3] notes for REIT
# subtype); an unrecognised hint therefore falls through to the LLM profile
# classifier rather than resolving to a bogus card. render_card_payload()
# additionally returns None (and logs) for any profile string it cannot resolve,
# so a stray hint degrades gracefully + visibly instead of mis-rendering.
SGX_TICKER_SECTOR_LOOKUP: dict[str, tuple[str, str, str, str]] = {
    # Banks
    "D05.SI":  ("Financials", "Money Center Bank (SG)", "Banks",      "DBS Group — SG money-center bank"),
    "O39.SI":  ("Financials", "Money Center Bank (SG)", "Banks",      "OCBC Bank — SG money-center bank"),
    "U11.SI":  ("Financials", "Money Center Bank (SG)", "Banks",      "UOB — SG money-center bank"),
    "S68.SI":  ("Financials", "Market Infrastructure (SG)",    "Capital Markets",        "Singapore Exchange"),
    "9CI.SI":  ("Financials", "Real Estate Asset Manager (SG)",   "Asset Management",       "CapitaLand Investment"),
    "U09.SI":  ("Financials", "Insurance",   "Insurance",              "United Overseas Insurance"),
    # Telco
    "Z74.SI":  ("Telco", "Telco / Infrastructure (SG)",            "Telecom Services",       "SingTel"),
    "CC3.SI":  ("Telco", "Telco / Infrastructure (SG)",            "Telecom Services",       "StarHub"),
    # Industrials
    "C6L.SI":  ("Industrials", "Aviation & Marine (SG)",    "Air Transport",          "Singapore Airlines"),
    "BN4.SI":  ("Industrials", "Conglomerate / Industrial (SG)","Conglomerates",         "Keppel Corporation"),
    "BS6.SI":  ("Industrials", "Aviation & Marine (SG)","Shipbuilding",          "Yangzijiang Shipbuilding"),
    "U96.SI":  ("Industrials", "Conglomerate / Industrial (SG)",  "Utilities & Energy",     "Sembcorp Industries"),
    "S63.SI":  ("Industrials", "Aerospace & Engineering (SG)",    "Aerospace & Defence",    "ST Engineering"),
    "S58.SI":  ("Industrials", "Aerospace & Engineering (SG)",    "Airport Services",       "SATS"),
    # A bus and rail operator, not a conglomerate -- the row's own
    # sub-industry field already said Transportation while the profile put it
    # on SOTP (published), which ComfortDelGro does not publish.
    "C52.SI":  ("Transportation", "Rail / Logistics",              "Public Transit",         "ComfortDelGro"),
    "J36.SI":  ("Industrials", "Conglomerate / Industrial (SG)","Conglomerates",         "Jardine Matheson"),
    "J37.SI":  ("Industrials", "Conglomerate / Industrial (SG)","Conglomerates",         "Jardine C&C"),
    "S51.SI":  ("Industrials", "Aviation & Marine (SG)",     "Marine & Offshore",      "Seatrium"),
    "MR7.SI":  ("Industrials", "Aviation & Marine (SG)",     "Marine Services",        "Marco Polo Marine"),
    "S56.SI":  ("Industrials", "Conglomerate / Industrial (SG)",  "Postal & Logistics",     "Singpost"),
    "ACV.SI":  ("Industrials", "Conglomerate / Industrial (SG)",   "Vehicle Inspection",     "Vicom"),
    # Consumer
    "F34.SI":  ("Consumer", "Agribusiness & Food (SG)",       "Food Products",          "Wilmar International"),
    "Y92.SI":  ("Consumer", "Agribusiness & Food (SG)",  "Beverages",              "Thai Beverage"),
    "G13.SI":  ("Consumer", "Packaged Consumer & Lifestyle (SG)",     "Casinos & Gaming",       "Genting Singapore"),
    "E5H.SI":  ("Consumer", "Agribusiness & Food (SG)",       "Agricultural Products",  "Golden Agri-Resources"),
    "AGS.SI":  ("Consumer", "Agribusiness & Food (SG)",     "Grocery Retail",         "Sheng Siong Group"),
    "EB5.SI":  ("Consumer", "Agribusiness & Food (SG)",       "Palm Oil",               "First Resources"),
    "P8Z.SI":  ("Consumer", "Agribusiness & Food (SG)",       "Palm Oil",               "Bumitama Agri"),
    "T14.SI":  ("Consumer", "Agribusiness & Food (SG)",       "Food & Agribusiness",    "Olam Group"),
    # Tech
    "V03.SI":  ("Tech", "Tech Manufacturing / EMS (SG)","Electronics Manufacturing","Venture Corporation"),
    "AWX.SI":  ("Tech", "Tech Manufacturing / EMS (SG)",  "Semiconductor Equipment","AEM Holdings"),
    "5DD.SI":  ("Tech", "Tech Manufacturing / EMS (SG)",  "Semiconductor Equipment","Micro-Mechanics"),
    "BHK.SI":  ("Tech", "Tech Manufacturing / EMS (SG)",  "Semiconductor Equipment","UMS Holdings"),
    "MZH.SI":  ("Tech", "Tech Manufacturing / EMS (SG)",  "Advanced Materials",     "Nanofilm Technologies"),
    "5CP.SI":  ("Tech", "Tech Manufacturing / EMS (SG)",   "Banking Software",       "Silverlake Axis"),
    # Property
    "C09.SI":  ("Property", "Property Developer (SG)",  "Real Estate Development","City Developments"),
    "H78.SI":  ("Property", "Property Developer (SG)",  "Real Estate Development","Hongkong Land"),
    "U14.SI":  ("Property", "Property Developer (SG)",  "Real Estate Development","UOL Group"),
    "OYY.SI":  ("Property", "Real Estate Agency (SG)",   "Real Estate Services",   "PropNex"),
    "W05.SI":  ("Property", "Property Developer (SG)",  "Real Estate Development","Wing Tai Holdings"),
    "40T.SI":  ("Property", "Specialised Accommodation (SG)",  "Workers Dormitory",      "Centurion Corporation"),
    # Energy
    "RE4.SI":  ("Resources",   "Coal",                                   "Coal Mining",            "Geo Energy Resources — thermal coal producer (Wave 1, owner 2026-09-20)"),
    # Healthcare
    "CLN.SI":  ("Healthcare", "Healthcare Provider (SG)", "Medical Gloves",         "Riverstone Holdings"),
    "A50.SI":  ("Healthcare", "Healthcare Provider (SG)",   "Healthcare Services",    "Thomson Medical Group"),
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
    "A7RU.SI":  ("Telco", "Telco / Infrastructure (SG)",      "Infrastructure Trust",   "Keppel Infrastructure Trust"),
    "AU8U.SI": ("REIT",        "China",      "China REIT",             "CapitaLand China Trust"),
    "HMN.SI":  ("REIT",        "Hospitality","Hospitality REIT",       "CapitaLand Ascott Trust"),
    "SK6U.SI": ("REIT",        "Healthcare", "Healthcare REIT",        "Parkway Life REIT"),
    "CWBU.SI":  ("Telco", "Telco / Infrastructure (SG)",      "Infrastructure Trust",   "NetLink NBN Trust"),
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
