"""Report families: the production report keyed by the resolved profile family.

Owner request, 2026-09-27 (`docs/report_sector_customisation_proposal.md`, decided "proceed with the
family list"). The JPM production report printed corporate free cash flow (-US$147.8bn) and net debt
(US$599bn) in Key financials because one generic template served every sector. Each family declares the
key-financials rows the PDF and the workbook render, the valuation exposition fields and the thesis
skeleton later build steps will consume, and the signals that belong on the page. Every profile in
`INDUSTRY_VALUATION_PROFILES` maps to exactly one family; anything unmapped is an operating company.

Rows are read from the per-fiscal-year `raw_financials` records the pipeline already carries (FMP line
items overlaid by period: revenue, net_income, ebit, ebitda, free_cash_flow, net_debt, total_equity,
book_value_per_share, dividends_per_share, shares_outstanding, capital_expenditure,
stock_based_compensation, operating_cash_flow). Derived rows divide two of them.
"""
from __future__ import annotations

from typing import Optional

#: Row spec: (label, kind, source). kind is one of
#:   "bn"   a money amount in billions             source = field
#:   "ps"   a per-share amount                     source = field, or (field, numerator, denominator) fallback
#:   "pct"  a ratio shown as a percentage          source = (numerator field, denominator field)
#:   "x"    a ratio shown as a multiple            source = (numerator field, denominator field)
_R = {
    "revenue":  ("Revenue", "bn", "revenue"),
    "ni":       ("Net income", "bn", "net_income"),
    "ebitda":   ("EBITDA", "bn", "ebitda"),
    "fcf":      ("FCF", "bn", "free_cash_flow"),
    "capex":    ("Capex", "bn", "capital_expenditure"),
    "net_debt": ("Net debt / (cash)", "bn", "net_debt"),
    "equity":   ("Total equity", "bn", "total_equity"),
    "bvps":     ("Book value / share", "ps", ("book_value_per_share", "total_equity", "shares_outstanding")),
    "dps":      ("Dividends / share", "ps", ("dividends_per_share", None, None)),
    "roe":      ("Return on equity", "pct", ("net_income", "total_equity")),
    "ebit_m":   ("EBIT margin", "pct", ("ebit", "revenue")),
    "fcf_m":    ("FCF margin", "pct", ("free_cash_flow", "revenue")),
    "sbc_pct":  ("SBC / revenue", "pct", ("stock_based_compensation", "revenue")),
    "capex_pct": ("Capex / revenue", "pct", ("capital_expenditure", "revenue")),
    "nd_ebitda": ("Net debt / EBITDA", "x", ("net_debt", "ebitda")),
    "nd_equity": ("Net debt / equity", "x", ("net_debt", "total_equity")),
}

REPORT_FAMILIES: dict[str, dict] = {
    "Banks": {
        "profiles": ["Money Center Bank", "Money Center Bank (EU)", "Money Center Bank (SG)", "Regional Bank",
                     "Super-Regional Bank", "EM Bank", "EM Bank (Premium)", "Bank / Lending Institution",
                     "Investment Bank", "Neo/Challenger", "Mortgage/GSE", "Card Issuer & Consumer Lender"],
        "rows": ["revenue", "ni", "equity", "bvps", "dps", "roe"],
        "exposition": ["GGM inputs (RoTE, CoE, g, target P/B) against spot P/TBV", "residual-income fade",
                       "excess capital", "implied-CoE flag when it fires"],
        "skeleton": ["rating and 12-month TSR", "book multiple against the GGM", "NII and credit cycle",
                     "capital return", "primary risk"],
        "signals": ["revisions", "dividend safety"],
        "excluded_metrics": ["free cash flow", "net debt"],
    },
    "Insurance": {
        "profiles": ["Insurance", "Insurance (P&C)"],
        "rows": ["revenue", "ni", "equity", "bvps", "dps", "roe"],
        "exposition": ["P&C: GGM on book and the combined-ratio gate", "life: P/EV on the accepted embedded value with the quarantine state named"],
        "skeleton": ["rating", "P/EV or P/B against the return on it", "underwriting or new-business trend", "capital", "risk"],
        "signals": ["reserve releases", "rate cycle"],
        "excluded_metrics": ["free cash flow", "net debt"],
    },
    "Fee financials": {
        "profiles": ["Asset Manager", "Alt Asset Manager", "Payment Networks", "Market Infrastructure",
                     "Market Infrastructure (SG)", "Brokerage", "FinTech", "Fintech/Stablecoin", "Insurance Broker",
                     "Financial Data & Ratings", "Payment Processors", "WealthTech & Specialty Financials (SG)",
                     "Real Estate Asset Manager (SG)", "Crypto Exchange"],
        "rows": ["revenue", "ebitda", "ni", "fcf", "net_debt"],
        "exposition": ["P/DE on the accepted input (alts)", "Forward P/E and EV/EBITDA against the basket (fee platforms)"],
        "skeleton": ["rating", "the multiple on the earnings the market prices", "flows or volumes", "operating leverage", "risk"],
        "signals": ["flows", "volumes"],
    },
    "Property, REITs and holdcos": {
        "profiles": ["REIT", "S-REIT", "Property Developer (SG)", "Real Estate Agency (SG)", "Specialised Accommodation (SG)",
                     "Holding Company", "Aerospace Holdco (HK)", "Conglomerate / Industrial (SG)",
                     "Property Developer (HK / China)", "Landlord / Investment Property (HK)", "Homebuilder / Land Developer", "Real Estate Services"],   # Wave 8
        "rows": ["revenue", "ni", "equity", "bvps", "dps", "nd_equity"],
        "exposition": ["NAV discount", "SOTP look-through with the accepted inputs", "gearing"],
        "skeleton": ["rating", "price against NAV", "portfolio or subsidiary drivers", "distributions and gearing", "risk"],
        "signals": ["cap rates", "occupancy"],
    },
    "Energy and resources": {
        "profiles": ["Regulated Utility", "Merchant Power", "IPP", "Clean Tech / Power Equipment OEM", "Midstream / Pipelines",
                     "Refining & Marketing", "Oilfield Services & Drilling", "EPC Contractor", "Energy Tech Licensor",
                     "Upstream Oil & Gas", "Integrated Oil & Gas", "Coal", "Mining (Major)", "Offshore Marine & Resources (SG)",
                     "Digital Asset Mining"],
        "rows": ["revenue", "ebitda", "fcf", "capex", "net_debt", "nd_ebitda"],
        "exposition": ["EV/EBITDA (norm) with the normalisation window stated", "PV-10 coverage", "SOTP (segments) for refiners", "rate base for utilities"],
        "skeleton": ["rating", "mid-cycle multiple and where the cycle sits", "volume and price", "capital return", "risk"],
        "signals": ["commodity strip", "regime flag"],
    },
    "Consumer": {
        "profiles": ["Packaged Consumer & Lifestyle (SG)", "Agribusiness & Food (SG)", "Food & Beverage", "Agribusiness & Food Processing",
                     "Grocery & Discount Retail", "Tobacco", "Apparel / Athletic Wear", "Household / Personal", "Traditional Retail",
                     "Luxury Goods", "Consumer Growth", "Membership / Subscription Retail", "Consumer Durables", "Automotive & EV",
                     "Online Gaming / Sports Betting", "Travel & Dining", "Local Services & Instant Retail"],
        "rows": ["revenue", "ebit_m", "fcf", "net_debt", "dps"],
        "exposition": ["Forward P/E against the NTM basket", "DDM at cost of equity where declared", "EV/EBITDA (norm) for cyclicals"],
        "skeleton": ["rating", "forward multiple against the basket", "volume/price and margin", "payout", "risk"],
        "signals": ["pricing", "inventory"],
    },
    "Health care": {
        "profiles": ["Pre-approval Biotech", "Commercial Biotech", "Large Cap Pharma", "Managed Care", "MedTech / Devices",
                     "Surgical Robotics / Capital Systems", "CDMO / Life Science Tools", "Healthcare Providers / Services",
                     "Medical Devices", "Animal Health", "Pharma Distribution", "Healthcare Provider (SG)"],
        "rows": ["revenue", "ebit_m", "ni", "fcf", "net_debt"],
        "exposition": ["rNPV state (accepted or quarantined, and what it covers)", "the Forward P/E sanity gate when it fires",
                       "the structural flag on managed care"],
        "skeleton": ["rating", "the anchor and its basis", "pipeline or utilisation", "pricing and reimbursement", "risk"],
        "signals": ["trial readouts", "MLR trend"],
    },
    "Industrials, materials and transport": {
        "profiles": ["Aerospace & Engineering (SG)", "Aviation & Marine (SG)", "Defense Primes", "Commercial Aerospace & Engines",
                     "Niche Aerospace Components", "Defense Tech & Space", "General Aviation (HK)", "GA Engines & Aftermarket (HK)",
                     "Automotive (OEM)", "Backlog-Gated Long Cycle", "Capital Goods", "Airlines", "Rail / Logistics",
                     "Steel / Metals", "Specialty Chemicals", "Electronic Materials & Industrial Diversified"],
        "rows": ["revenue", "ebit_m", "fcf", "net_debt", "nd_ebitda"],
        "exposition": ["backlog-coverage DCF with the accepted backlog named", "EV/EBIT (norm)"],
        "skeleton": ["rating", "backlog visibility", "margin", "capital", "risk"],
        "signals": ["orders", "book-to-bill"],
    },
    "Technology, telecom and media": {
        "profiles": ["AI Infrastructure / Neocloud", "Consumer Electronics / Hardware Ecosystem", "China Internet Platform",
                     "Tech Manufacturing / EMS (SG)", "Growth SaaS", "Hyperscaler / Tech Conglomerate",
                     "Cybersecurity / Mission-Critical SaaS", "Mature SaaS", "High-Growth Tech / AI", "Hyper-Growth Platform",
                     "Mature Platform", "Early Platform", "Levered Subscription", "Fabless", "IDM / Foundry", "Memory / DRAM-NAND",
                     "Equipment / EDA", "OSAT / Packaging", "Telco / Infrastructure (SG)", "Stable Growth", "Ad / Consulting",
                     "IT Services", "Pre-Revenue Tech", "BTC Treasury / Proxy",
                     "Analog / Mixed-signal IDM", "Media & Streaming", "Networking & Communication Equipment", "Telecom Carrier"],   # Wave 7
        "rows": ["revenue", "ebit_m", "fcf_m", "sbc_pct", "capex_pct", "net_debt"],
        "exposition": ["forward anchor against the live basket", "the terminal convergence stated", "SBC treatment stated"],
        "skeleton": ["rating", "forward multiple against the basket", "growth durability", "SBC and capital return", "risk"],
        "signals": ["revisions", "Growth_Inflection_Speculative flag"],
    },
    "Operating company": {
        "profiles": [],
        "rows": ["revenue", "ni", "fcf", "net_debt"],
        "exposition": [], "skeleton": [], "signals": [],
    },
}

_PROFILE_TO_FAMILY: dict[str, str] = {p: f for f, spec in REPORT_FAMILIES.items() for p in spec["profiles"]}


def report_family_for(profile_name: Optional[str]) -> str:
    """The family a profile renders under; "Operating company" for anything unmapped."""
    return _PROFILE_TO_FAMILY.get((profile_name or "").strip(), "Operating company")


def family_row_specs(family: str) -> list[tuple[str, str, object]]:
    spec = REPORT_FAMILIES.get(family) or REPORT_FAMILIES["Operating company"]
    return [_R[k] for k in spec["rows"]]


def _num(v) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def family_rows(raw_financials: Optional[dict], family: str, years: int = 3) -> tuple[list[str], list[tuple[str, str, list]]]:
    """(fiscal-year keys, [(label, kind, [value per year])]) for the family's rows, read from the
    per-year `raw_financials` records. Missing inputs give None; a derived ratio with a
    non-positive denominator gives None. Never raises."""
    if not isinstance(raw_financials, dict):
        return [], []
    fy_keys = sorted(k for k, v in raw_financials.items() if isinstance(v, dict))[-years:]
    if not fy_keys:
        return [], []

    def get(fy, field):
        return _num((raw_financials.get(fy) or {}).get(field)) if field else None

    out = []
    for label, kind, source in family_row_specs(family):
        vals = []
        for fy in fy_keys:
            if kind == "bn":
                v = get(fy, source)
            elif kind == "ps":
                field, num_f, den_f = source
                v = get(fy, field)
                if v is None and num_f and den_f:
                    num, den = get(fy, num_f), get(fy, den_f)
                    v = (num / den) if (num is not None and den) else None
            else:                                           # pct, x
                num, den = get(fy, source[0]), get(fy, source[1])
                if kind == "pct" and num is not None and label.startswith(("SBC", "Capex")):
                    num = abs(num)
                v = (num / den) if (num is not None and den and den > 0) else None
            vals.append(v)
        out.append((label, kind, vals))
    return fy_keys, out


def format_value(v, kind: str) -> str:
    if v is None:
        return "n/a"
    if kind == "bn":
        return f"{v / 1e9:,.1f}B" if abs(v) >= 1e8 else f"{v / 1e6:,.0f}M"
    if kind == "ps":
        return f"{v:,.2f}"
    if kind == "pct":
        return f"{v * 100:.1f}%"
    return f"{v:.2f}x"
