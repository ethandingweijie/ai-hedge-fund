"""Firm-level CAPM cost of capital (owner, 2026-11-25: CFA audit -- "Firm CAPM, overlays out").

WACC = E/V x Ke + D/V x Kd x (1 - t), with
  Ke = rf + beta_L x ERP + CRP                       (CAPM; country risk premium for HK / SG / non-US reporters)
  beta_L = beta_U x (1 + (1 - t) x D/E)              (Hamada, at the firm's market D/E: net debt / market cap)
  beta_U = Damodaran's US industry unlevered beta corrected for cash (January 2026), by profile
  Kd = rf + the credit spread of the firm's rating (agency rating when accepted, else the rating implied by cover)
  rf = the live 10-year US Treasury (FRED DGS10), else 4.94% (Damodaran's rate on 2026-11-01)
  ERP = 4.41% (Damodaran's implied ERP, 2026-11-01; trailing 12-month cash yield with adjusted payout)

Replaces the sector WACC table and its overlays (risk-off regime +150bp, research-risk loading, insider activity)
for operating companies. Balance-sheet financials (banks, insurers, brokers, holding companies) keep their own
cost-of-equity models: a WACC on deposits or float is not a cost of capital.
"""
from __future__ import annotations

import os
from typing import Optional

SOURCE = "Damodaran, Betas by Sector (US), data as of January 2026; implied ERP and T-bond rate 2026-11-01"
RF_FALLBACK = 0.0494
ERP = 0.0441
WACC_FLOOR_OVER_RF = 0.01

#: Damodaran US industry: unlevered beta corrected for cash (January 2026).
INDUSTRY_BETA_U: dict[str, float] = {
    "Advertising": 1.01, "Aerospace/Defense": 0.87, "Air Transport": 0.76, "Apparel": 0.79, "Auto & Truck": 1.31,
    "Auto Parts": 1.13, "Beverage (Alcoholic)": 0.63, "Beverage (Soft)": 0.58, "Broadcasting": 0.32,
    "Building Materials": 0.96, "Business & Consumer Services": 0.81, "Cable TV": 0.36, "Chemical (Basic)": 0.64,
    "Chemical (Diversified)": 0.41, "Chemical (Specialty)": 0.82, "Coal & Related Energy": 1.18,
    "Computer Services": 0.96, "Computers/Peripherals": 1.32, "Construction Supplies": 1.05, "Diversified": 0.84,
    "Drugs (Biotechnology)": 1.08, "Drugs (Pharmaceutical)": 0.92, "Education": 0.72, "Electrical Equipment": 1.19,
    "Electronics (Consumer & Office)": 0.93, "Electronics (General)": 0.94, "Engineering/Construction": 1.14,
    "Entertainment": 0.76, "Environmental & Waste Services": 0.82, "Farming/Agriculture": 0.85,
    "Food Processing": 0.47, "Food Wholesalers": 0.65, "Furn/Home Furnishings": 0.65,
    "Green & Renewable Energy": 0.47, "Healthcare Products": 0.86, "Healthcare Support Services": 0.74,
    "Heathcare Information and Technology": 1.02, "Homebuilding": 0.85, "Hospitals/Healthcare Facilities": 0.56,
    "Hotel/Gaming": 0.88, "Household Products": 0.74, "Information Services": 0.76, "Insurance (General)": 0.58,
    "Investments & Asset Management": 0.59, "Machinery": 0.89, "Metals & Mining": 1.01,
    "Office Equipment & Services": 1.04, "Oil/Gas (Integrated)": 0.28, "Oil/Gas (Production and Exploration)": 0.58,
    "Oil/Gas Distribution": 0.47, "Oilfield Svcs/Equip.": 0.79, "Packaging & Container": 0.75,
    "Paper/Forest Products": 0.77, "Power": 0.31, "Precious Metals": 0.83, "Publishing & Newspapers": 0.51,
    "R.E.I.T.": 0.40, "Real Estate (Development)": 0.56, "Real Estate (General/Diversified)": 0.63,
    "Real Estate (Operations & Services)": 0.86, "Recreation": 0.74, "Restaurant/Dining": 0.78,
    "Retail (Automotive)": 0.71, "Retail (Building Supply)": 1.32, "Retail (Distributors)": 0.80,
    "Retail (General)": 0.78, "Retail (Grocery and Food)": 0.85, "Retail (REITs)": 0.44,
    "Retail (Special Lines)": 1.00, "Semiconductor": 1.50, "Semiconductor Equip": 1.39,
    "Shipbuilding & Marine": 0.66, "Shoe": 1.00, "Software (Entertainment)": 1.02, "Software (Internet)": 1.59,
    "Software (System & Application)": 1.25, "Steel": 0.94, "Telecom (Wireless)": 0.39, "Telecom. Equipment": 0.89,
    "Telecom. Services": 0.38, "Tobacco": 0.69, "Transportation": 0.71, "Transportation (Railroads)": 0.81,
    "Trucking": 0.87, "Utility (General)": 0.15, "Utility (Water)": 0.28,
    "Total Market (without financials)": 0.90,
}

#: Balance-sheet financials keep their own cost-of-equity models (not a WACC).
FINANCIAL_PROFILES = frozenset({
    "Mortgage/GSE", "Bank / Lending Institution", "Money Center Bank", "Money Center Bank (EU)", "Money Center Bank (SG)",
    "Regional Bank", "Super-Regional Bank", "EM Bank", "Investment Bank", "Insurance (P&C)", "Insurance",
    "Holding Company", "Asian Holding Company (Look-Through)", "Card Issuer & Consumer Lender", "Brokerage",
})

#: Valuation profile -> Damodaran industry. Integrated oil and refining read the E&P beta: Damodaran's integrated
#: group has four firms (0.28), too few to stand for a refiner's cycle.
PROFILE_INDUSTRY: dict[str, str] = {
    # Financials that are operating businesses
    "WealthTech & Specialty Financials (SG)": "Investments & Asset Management",
    "Market Infrastructure (SG)": "Information Services", "Real Estate Asset Manager (SG)": "Investments & Asset Management",
    "FinTech": "Software (System & Application)", "Asset Manager": "Investments & Asset Management",
    "Fintech/Stablecoin": "Software (Internet)", "Alt Asset Manager": "Investments & Asset Management",
    "Insurance Broker": "Business & Consumer Services", "Financial Data & Ratings": "Information Services",
    "Payment Networks": "Information Services", "Market Infrastructure": "Information Services",
    # Energy
    "Regulated Utility": "Utility (General)", "Merchant Power": "Power", "IPP": "Power",
    "City Gas Distribution (HK / China)": "Oil/Gas Distribution", "Clean Tech / Power Equipment OEM": "Electrical Equipment",
    "Midstream / Pipelines": "Oil/Gas Distribution", "Refining & Marketing": "Oil/Gas (Production and Exploration)",
    "Oilfield Services & Drilling": "Oilfield Svcs/Equip.", "Energy Tech Licensor": "Electrical Equipment",
    # Tech
    "AI Infrastructure / Neocloud": "Computer Services", "Consumer Electronics / Hardware Ecosystem": "Computers/Peripherals",
    "China Internet Platform": "Software (Internet)", "Local Services & Instant Retail": "Software (Internet)",
    "Electronic Materials & Industrial Diversified": "Electronics (General)",
    "Tech Manufacturing / EMS (SG)": "Electronics (General)", "Growth SaaS": "Software (System & Application)",
    "Hyperscaler / Tech Conglomerate": "Software (System & Application)",
    "Consumer Technology Ecosystem": "Computers/Peripherals",
    "Cybersecurity / Mission-Critical SaaS": "Software (System & Application)", "Media & Streaming": "Entertainment",
    "Networking & Communication Equipment": "Telecom. Equipment", "Mature SaaS": "Software (System & Application)",
    "Hyper-Growth Platform": "Software (Internet)", "Mature Platform": "Software (Internet)",
    # Biopharma and healthcare
    "Pre-approval Biotech": "Drugs (Biotechnology)", "Commercial Biotech": "Drugs (Biotechnology)",
    "Big Pharma (Consolidated DCF)": "Drugs (Pharmaceutical)", "Biotech Platform (SOTP)": "Drugs (Biotechnology)",
    "Large Cap Pharma": "Drugs (Pharmaceutical)", "Specialty & Generic Pharma": "Drugs (Pharmaceutical)",
    "MedTech / Devices": "Healthcare Products", "Surgical Robotics / Capital Systems": "Healthcare Products",
    "CDMO / Life Science Tools": "Healthcare Products", "Managed Care": "Healthcare Support Services",
    "Healthcare Providers / Services": "Hospitals/Healthcare Facilities", "Pharma Distribution": "Healthcare Support Services",
    "Healthcare Provider (SG)": "Hospitals/Healthcare Facilities",
    # Consumer
    "Packaged Consumer & Lifestyle (SG)": "Household Products", "Agribusiness & Food (SG)": "Farming/Agriculture",
    "Food & Beverage": "Food Processing", "Agribusiness & Food Processing": "Farming/Agriculture",
    "Grocery & Discount Retail": "Retail (Grocery and Food)", "Tobacco": "Tobacco", "Apparel / Athletic Wear": "Apparel",
    "Household / Personal": "Household Products", "Traditional Retail": "Retail (General)", "Luxury Goods": "Apparel",
    "Consumer Growth": "Retail (Special Lines)", "Membership / Subscription Retail": "Retail (General)",
    "Consumer Durables": "Furn/Home Furnishings", "Automotive & EV": "Auto & Truck",
    "Online Gaming / Sports Betting": "Hotel/Gaming", "Travel & Dining": "Restaurant/Dining",
    "Restaurants": "Restaurant/Dining", "Casinos & Integrated Resorts": "Hotel/Gaming",
    "Lodging (Asset-Light)": "Hotel/Gaming", "Hotel Owner-Operator (HK)": "Hotel/Gaming",
    "Online Travel": "Business & Consumer Services", "Cruise Lines": "Recreation", "Auto Parts & Suppliers": "Auto Parts",
    "Leisure Products & Brands": "Recreation",
    # Industrials
    "Offshore Marine & Resources (SG)": "Shipbuilding & Marine", "Aerospace & Engineering (SG)": "Aerospace/Defense",
    "Aviation & Marine (SG)": "Aerospace/Defense", "Conglomerate / Industrial (SG)": "Diversified",
    "Defense Primes": "Aerospace/Defense", "Commercial Aerospace & Engines": "Aerospace/Defense",
    "Niche Aerospace Components": "Aerospace/Defense", "Defense Tech & Space": "Aerospace/Defense",
    "Aerospace Holdco (HK)": "Aerospace/Defense", "General Aviation (HK)": "Aerospace/Defense",
    "GA Engines & Aftermarket (HK)": "Aerospace/Defense", "Automotive (OEM)": "Auto & Truck",
    "Capital Goods": "Machinery", "Long-Cycle E&C": "Engineering/Construction",
    "Industrial Distribution": "Retail (Distributors)", "Equipment Rental": "Machinery",
    "Waste & Environmental Services": "Environmental & Waste Services",
    "Toll Road / Infrastructure (HK)": "Transportation",
    "Industrial Route & Uniform Services": "Business & Consumer Services", "Information Services": "Information Services",
    "Marketplace / Salvage Platform": "Business & Consumer Services", "Blended Industrial OpCo": "Diversified",
    "Battery & Energy Storage": "Electrical Equipment",
    # Telco, crypto, property
    "Telco / Infrastructure (SG)": "Telecom. Services", "Telecom Carrier": "Telecom. Services",
    "Digital Asset Mining": "Software (Internet)", "BTC Treasury / Proxy": "Software (Internet)",
    "Crypto Exchange": "Software (Internet)",
    "Specialised Accommodation (SG)": "Real Estate (Operations & Services)",
    "Real Estate Agency (SG)": "Real Estate (Operations & Services)",
    "Property Developer (SG)": "Real Estate (Development)", "Property Developer (HK / China)": "Real Estate (Development)",
    "Landlord / Investment Property (HK)": "Real Estate (General/Diversified)", "Homebuilder / Land Developer": "Homebuilding",
    "Real Estate Services": "Real Estate (Operations & Services)", "S-REIT": "R.E.I.T.", "REIT": "R.E.I.T.",
    "REIT (Specialty / OpCo)": "R.E.I.T.",
    # Transport, materials, resources
    "Airlines": "Air Transport", "Rail / Logistics": "Transportation (Railroads)",
    "Trucking & Parcel Logistics": "Trucking", "Container & Bulk Shipping": "Transportation",
    "Steel / Metals": "Steel", "Specialty Chemicals": "Chemical (Specialty)",
    "Commodity Chemicals & Ag Inputs": "Chemical (Basic)", "Aggregates & Cement": "Building Materials",
    "Packaging & Paper": "Packaging & Container", "Upstream Oil & Gas": "Oil/Gas (Production and Exploration)",
    "Integrated Oil & Gas": "Oil/Gas (Production and Exploration)", "Coal": "Coal & Related Energy",
    "Base Metals": "Metals & Mining", "Precious Metals": "Precious Metals", "Diversified Miners": "Metals & Mining",
    "Mining (Major)": "Metals & Mining",
    # Services and semiconductors
    "Ad / Consulting": "Advertising", "IT Services": "Computer Services",
    "Analog / Mixed-signal IDM": "Semiconductor", "Fabless": "Semiconductor", "IDM / Foundry": "Semiconductor",
    "Memory / DRAM-NAND": "Semiconductor", "Equipment / EDA": "Semiconductor Equip",
}

_TAX = {"US": 0.21, "HK": 0.165, "SG": 0.17, "CN": 0.25}   # CN: PRC EIT statutory (src/markets/china.py)


def live_rf_enabled() -> bool:
    """CAPM_LIVE_RF=off uses the fallback (golden replay pins it off: its fixtures hold no DGS10 call)."""
    return os.environ.get("CAPM_LIVE_RF", "on").strip().lower() not in ("0", "false", "no", "off")


def risk_free(market: str = "US") -> tuple[float, str]:
    """The risk-free rate in the currency of the cash flows: an owner-set government 10y for a market listed in
    valuation_constants.risk_free_by_market (SG: SGS 10y, owner 2026-11-25), else live FRED DGS10."""
    try:
        from src.data import valuation_constants as _vc
        _m = (((_vc.load().get("risk_free_by_market") or {}).get("markets") or {}).get(market or "US") or {})
        _v = _m.get("value")
        if isinstance(_v, (int, float)) and 0.0 < _v < 0.15:
            return float(_v), f"{_m.get('basis')} (owner-set; {_m.get('source')})"
    except Exception:                                      # noqa: BLE001
        pass
    if live_rf_enabled():
        try:
            from src.tools.fred import get_fred_spread
            v = get_fred_spread("DGS10")
            if isinstance(v, (int, float)) and 0.5 < v < 15:
                return float(v) / 100.0, "FRED DGS10 (latest)"
        except Exception:                                  # noqa: BLE001
            pass
    return RF_FALLBACK, "Damodaran US T-bond rate 2026-11-01 (fallback)"


def equity_risk_premium(market: str = "US") -> tuple[float, str]:
    """(ERP, source): an owner-set market ERP from valuation_constants.equity_risk_premium_by_market (CN: A-share
    implied, net of the CRP, owner 2026-11-28), else the global ERP."""
    try:
        from src.data import valuation_constants as _vc
        _m = (((_vc.load().get("equity_risk_premium_by_market") or {}).get("markets") or {}).get(market or "") or {})
        _v = _m.get("value")
        if isinstance(_v, (int, float)) and 0.0 < _v < 0.15:
            return float(_v), f"owner-set {market} ERP ({_m.get('accepted')}; net of CRP)"
    except Exception:                                      # noqa: BLE001
        pass
    return ERP, "Damodaran implied ERP 2026-11-01"


def local_min_spread(market: str) -> Optional[float]:
    """The owner's minimum Ke / WACC spread over a local government curve, or None (valuation_constants)."""
    try:
        from src.data import valuation_constants as _vc
        v = ((((_vc.load().get("risk_free_by_market") or {}).get("markets") or {}).get(market or "") or {})
             .get("min_spread_over_rf"))
        return float(v) if isinstance(v, (int, float)) and 0.0 < v < 0.10 else None
    except Exception:                                      # noqa: BLE001
        return None


def applies(profile: Optional[str]) -> bool:
    """FIRM_CAPM=off restores the sector table and its overlays (rollback lever)."""
    if os.environ.get("FIRM_CAPM", "on").strip().lower() in ("0", "false", "no", "off"):
        return False
    return bool(profile) and profile not in FINANCIAL_PROFILES


#: Owner, 2026-11-25: the beta blends the relevered industry beta with the company's own Blume-adjusted regression
#: beta (0.67 x raw + 0.33); an own beta outside this band is noise and the industry beta stands alone.
OWN_BETA_BAND = (0.4, 2.5)
#: Owner, 2026-11-25 (moat): a terminal RONIC above WACC is earned only on the record -- ROIC at least 5 points above
#: WACC in 8 of the last 10 years; RONIC = WACC + half the median spread, capped at 10 points.
MOAT_MIN_SPREAD, MOAT_MIN_YEARS, MOAT_SHARE, MOAT_CAP = 0.05, 8, 0.5, 0.10


def moat_history_enabled() -> bool:
    """MOAT_HISTORY=off skips the ROIC history fetch (golden replay pins it off: no such call in the fixtures)."""
    return os.environ.get("MOAT_HISTORY", "on").strip().lower() not in ("0", "false", "no", "off")


def roic_ex_goodwill(income: list, balance: list) -> list[float]:
    """Annual after-tax ROIC on invested capital EXCLUDING goodwill and acquired intangibles, newest first.

    Owner, 2026-11-28 (D4 b): the terminal RONIC concerns organic reinvestment; goodwill is the price of past
    acquisitions, not capital the business needs to grow. FMP's returnOnInvestedCapital keeps it in the denominator,
    so acquirers with high operating returns failed the moat test (KO 10% FMP vs 32% ex-goodwill; PEP 13% vs 70%;
    PG 15% vs 165%; GOOGL 17% vs 44%). NOPAT = operating income x (1 - effective tax, clamped 0-35%); invested
    capital = total debt + equity - cash & short-term investments - goodwill - intangibles. Years with non-positive
    invested capital are skipped (ROIC undefined)."""
    bs = {str(r.get("date"))[:10]: r for r in balance or [] if isinstance(r, dict)}
    out = []
    for i in sorted((r for r in income or [] if isinstance(r, dict)), key=lambda r: str(r.get("date")), reverse=True):
        b = bs.get(str(i.get("date"))[:10])
        if not b:
            continue
        ebit = i.get("operatingIncome")
        pre = i.get("incomeBeforeTax") or 0
        tax = (i.get("incomeTaxExpense") or 0) / pre if pre else 0.21
        tax = min(max(tax, 0.0), 0.35)
        ic = ((b.get("totalDebt") or 0) + (b.get("totalStockholdersEquity") or 0)
              - (b.get("cashAndShortTermInvestments") or 0) - (b.get("goodwill") or 0) - (b.get("intangibleAssets") or 0))
        if not isinstance(ebit, (int, float)) or ic <= 0:
            continue
        v = ebit * (1.0 - tax) / ic
        if -1.0 < v < 3.0:
            out.append(float(v))
    return out


def roic_history(ticker: str, api_key: Optional[str] = None, years: int = 10) -> list[float]:
    """The company's own annual after-tax ROIC, newest first; [] when disabled or unavailable.
    D4 (b): excluding goodwill and acquired intangibles (roic_ex_goodwill) when at least MOAT_MIN_YEARS valid years
    exist; otherwise FMP's key-metrics ROIC as before."""
    if not moat_history_enabled():
        return []
    try:
        from src.tools.api import _STABLE, _fmp_get
        inc = _fmp_get(f"{_STABLE}/income-statement", {"symbol": ticker, "period": "annual", "limit": years}, api_key, uncap=True) or []
        bal = _fmp_get(f"{_STABLE}/balance-sheet-statement", {"symbol": ticker, "period": "annual", "limit": years}, api_key, uncap=True) or []
        ex = roic_ex_goodwill(inc if isinstance(inc, list) else [], bal if isinstance(bal, list) else [])
        if len(ex) >= MOAT_MIN_YEARS:
            return ex
    except Exception:                                      # noqa: BLE001
        pass
    try:
        from src.tools.api import _STABLE, _fmp_get
        rows = _fmp_get(f"{_STABLE}/key-metrics", {"symbol": ticker, "period": "annual", "limit": years}, api_key, uncap=True) or []
    except Exception:                                      # noqa: BLE001
        return []
    out = []
    for r in rows if isinstance(rows, list) else []:
        v = r.get("returnOnInvestedCapital") if isinstance(r, dict) else None
        if isinstance(v, (int, float)) and -1.0 < v < 3.0:
            out.append(float(v))
    return out


def moat_spread(roics: list[float], wacc: float) -> dict:
    """{'spread', 'qualifying_years', 'years', 'median_excess', 'tagged'}: the terminal RONIC premium over WACC."""
    n = len(roics or [])
    if n < MOAT_MIN_YEARS:
        return {"spread": 0.0, "qualifying_years": 0, "years": n, "median_excess": None, "tagged": False,
                "basis": f"{n} years of ROIC on file (needs {MOAT_MIN_YEARS})"}
    ex = sorted(r - float(wacc) for r in roics)
    q = sum(1 for e in ex if e >= MOAT_MIN_SPREAD)
    med = ex[len(ex) // 2] if len(ex) % 2 else 0.5 * (ex[len(ex) // 2 - 1] + ex[len(ex) // 2])
    tagged = q >= MOAT_MIN_YEARS and med > 0
    spread = min(MOAT_SHARE * med, MOAT_CAP) if tagged else 0.0
    return {"spread": round(spread, 5), "qualifying_years": q, "years": n, "median_excess": round(med, 5), "tagged": tagged,
            "basis": (f"ROIC beat WACC by {MOAT_MIN_SPREAD:.0%}+ in {q} of {n} years (median excess {med:.1%}): terminal "
                      f"RONIC = WACC + {spread:.1%}" if tagged else
                      f"ROIC beat WACC by {MOAT_MIN_SPREAD:.0%}+ in {q} of {n} years (needs {MOAT_MIN_YEARS}): RONIC = WACC")}


def firm_wacc(*, profile: Optional[str], net_debt: Optional[float], market_cap: Optional[float],
              cost_of_debt: Optional[float], crp: float = 0.0, market: str = "US",
              contracted_discount: float = 0.0, own_beta: Optional[float] = None) -> Optional[dict]:
    """The firm's WACC and its build, or None when it cannot be computed (no market cap; financial profile)."""
    if not applies(profile) or not market_cap or market_cap <= 0:
        return None
    industry = PROFILE_INDUSTRY.get(profile or "", "Total Market (without financials)")
    beta_u = INDUSTRY_BETA_U.get(industry, INDUSTRY_BETA_U["Total Market (without financials)"])
    rf, rf_source = risk_free(market)
    t = _TAX.get(market, 0.21)
    D = max(float(net_debt or 0.0), 0.0)
    E = float(market_cap)
    de = D / E
    beta_l_industry = beta_u * (1.0 + (1.0 - t) * de)
    beta_own_adj = None
    if isinstance(own_beta, (int, float)) and OWN_BETA_BAND[0] <= own_beta <= OWN_BETA_BAND[1]:
        beta_own_adj = 0.67 * float(own_beta) + 0.33
    beta_l = 0.5 * (beta_l_industry + beta_own_adj) if beta_own_adj is not None else beta_l_industry
    erp, erp_source = equity_risk_premium(market)
    ke = rf + beta_l * erp + float(crp or 0.0)
    # Owner, 2026-11-25: on a local government curve far below the US one (SGS 2.29%), a low-beta name's Ke and
    # WACC fall to levels no local desk would use (Singtel Ke 4.16%, WACC 3.9%: 0.42 x a US ERP on the SGS rate).
    # Floor both at the market's minimum spread over its own rf (valuation_constants.risk_free_by_market).
    _min_spread = local_min_spread(market) if "owner-set" in str(rf_source) else None
    ke_floored = bool(_min_spread) and ke < rf + _min_spread
    if ke_floored:
        ke = rf + _min_spread
    kd = float(cost_of_debt) if isinstance(cost_of_debt, (int, float)) and cost_of_debt > 0 else rf + 0.016
    # HK/SG workbook audit (2026-11-25): the live Kd is a US yield (Treasury + the rating's spread). When the market
    # discounts on its own government curve, the debt moves onto that curve too, spread kept -- Singtel's Kd stayed
    # at 5.15% on US rates while its Ke fell to 4.16% on SGS, a cost of equity below its cost of debt.
    kd_rebased, us_rf = None, None
    if isinstance(cost_of_debt, (int, float)) and cost_of_debt > 0 and "owner-set" in str(rf_source):
        us_rf = risk_free("US")[0]
        kd_rebased = max(kd - us_rf + rf, rf)
        kd = kd_rebased
    dv = D / (D + E)
    wacc = (1.0 - dv) * ke + dv * kd * (1.0 - t) + float(contracted_discount or 0.0)
    floor = rf + max(WACC_FLOOR_OVER_RF, _min_spread or 0.0)
    floored = wacc < floor
    wacc = max(wacc, floor)
    return {"wacc": round(wacc, 5), "industry": industry, "beta_unlevered": beta_u, "beta_levered": round(beta_l, 4),
            "beta_industry_relevered": round(beta_l_industry, 4), "beta_own_raw": own_beta,
            "beta_own_adjusted": (round(beta_own_adj, 4) if beta_own_adj is not None else None),
            "de_ratio": round(de, 4), "dv_ratio": round(dv, 4), "rf": round(rf, 5), "rf_source": rf_source, "erp": erp,
            "erp_source": erp_source,
            "crp": round(float(crp or 0.0), 5), "cost_of_equity": round(ke, 5), "cost_of_debt": round(kd, 5),
            "tax_rate": t, "contracted_discount": float(contracted_discount or 0.0), "floored": floored,
            "cost_of_debt_rebased": kd_rebased is not None,
            "min_spread_over_rf": _min_spread, "ke_floored": ke_floored,
            **({"us_rf": round(us_rf, 5), "cost_of_debt_us": round(float(cost_of_debt), 5)} if kd_rebased is not None else {}),
            "source": SOURCE}


def describe(w: dict) -> str:
    _b = (f"{w['beta_levered']:.2f} = average of industry {w['beta_industry_relevered']:.2f} ({w['industry']}, unlevered "
          f"{w['beta_unlevered']:.2f} relevered at D/E {w['de_ratio']:.0%}) and own adjusted {w['beta_own_adjusted']:.2f}"
          if w.get("beta_own_adjusted") is not None else
          f"{w['beta_levered']:.2f} (industry: {w['industry']}, unlevered {w['beta_unlevered']:.2f} relevered at D/E "
          f"{w['de_ratio']:.0%}; own beta unavailable or outside {OWN_BETA_BAND[0]}-{OWN_BETA_BAND[1]})")
    return (f"WACC {w['wacc']:.2%} (firm CAPM): Ke {w['cost_of_equity']:.2%} = rf {w['rf']:.2%} + beta "
            + _b + f" x ERP {w['erp']:.2%}" + (f" + CRP {w['crp']:.2%}" if w['crp'] else "")
            + f"; Kd {w['cost_of_debt']:.2%} pre-tax at D/V {w['dv_ratio']:.0%}"
            + (f"; contracted-revenue {w['contracted_discount']:+.2%}" if w['contracted_discount'] else "")
            + (f"; Ke floored at rf + {w['min_spread_over_rf']:.1%}" if w.get('ke_floored') else "")
            + (f"; floored at rf + {max(WACC_FLOOR_OVER_RF, w.get('min_spread_over_rf') or 0.0):.1%}" if w['floored'] else "")
            + " -- replaces the sector table and its regime / research / insider overlays")
