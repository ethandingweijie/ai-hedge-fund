"""Biopharma valuation-method selection (owner, 2026-10-06).

Two heuristics decide which valuation methods a drug company gets:

  * DETERMINISTIC -- popular tickers take the owner's table (POPULAR), each with its components and
    the reason the method fits.
  * LOGIC -- every other drug company walks the owner's four gates (select_by_gates):

      Gate 1  Commercial concentration = product sales / total revenue > 0.80, and no unapproved
              late-stage asset expected to double the enterprise   -> consolidated DCF (LOE model)
      Gate 2  N clinical programmes <= 3 and product sales ~ 0     -> standalone pipeline rNPV
      Gate 3  recurring software / service revenue                 -> platform two-pillar SOTP
      Gate 4  otherwise                                            -> hybrid SOTP
              (with no commercial base: an asset-by-asset pipeline SOTP)

The owner's diagram tests commercial sales against enterprise value at the first fork; the written
algorithm (section 3) tests product sales against total revenue. The algorithm is implemented; the
EV form is reported in the trace as a cross-check.

Each archetype maps to an engine profile (ARCHETYPES[...]["profile"]). The result carries a trace of
every gate evaluated, so the routing is auditable from the payload.
"""
from __future__ import annotations

from typing import Optional

#: Owner-set thresholds (PROPOSED 2026-10-06, the owner's gate spec).
COMMERCIAL_CONCENTRATION = 0.80
N_CLINICAL_STANDALONE_MAX = 3
#: "Commercial product sales ~ 0": below this share of revenue or this absolute amount (USD).
COMMERCIAL_ZERO_SHARE = 0.05
COMMERCIAL_ZERO_USD = 10e6
#: "Expected to double enterprise size": an unapproved Phase 3 / filed asset's risk-adjusted peak
#: sales x this value-per-peak multiple reaching the enterprise value (rule of thumb: an approved
#: drug is worth ~3x peak sales).
PEAK_SALES_VALUE_MULTIPLE = 3.0

ARCHETYPES: dict[str, dict] = {
    "consolidated_dcf": {
        "label": "Consolidated multi-period DCF and forward multiples",
        "profile": "Big Pharma (Consolidated DCF)",
        "premise": "Pipeline assets are an organic replacement pool offsetting loss of exclusivity; the "
                   "company is valued on corporate cash flow, margins and WACC.",
        "blindspot": "Obscures high-potential breakthrough assets and the binary failure of a single Phase 3.",
    },
    "pipeline_rnpv": {
        "label": "Asset-by-asset pipeline rNPV",
        "profile": "Pre-approval Biotech",
        "premise": "Enterprise value derives from asset-level clinical de-risking and indication TAM, "
                   "discounted at biotech hurdle rates; unallocated G&A deducted, net cash added.",
        "blindspot": "Dilution risk and cash runway before the next readout.",
    },
    "platform_sotp": {
        "label": "Two-pillar SOTP (platform multiple + milestone / royalty rNPV)",
        "profile": "Biotech Platform (SOTP)",
        "premise": "Recurring software / service revenue and binary clinical development need different "
                   "multiples and discount rates.",
        "blindspot": "Conglomerate discount; double-counting shared G&A across the pillars.",
    },
    "hybrid_sotp": {
        "label": "Hybrid SOTP (commercial base + pipeline rNPV)",
        "profile": "Commercial Biotech",
        "premise": "A cash-generating commercial base priced on operating legs, the unapproved pipeline "
                   "added as a risk-adjusted sum-of-the-parts line.",
        "blindspot": "Conglomerate discount; shared G&A is borne by the operating legs, not deducted twice.",
    },
    "inlicensing_sotp": {
        "label": "In-licensing portfolio SOTP",
        "profile": "Commercial Biotech",
        "premise": "Each in-licensed asset carries its own transfer price and royalties payable to the "
                   "licensor; assets are valued net of those economics, net cash added.",
        "blindspot": "Licensor economics differ by asset; a single company margin misprices them.",
    },
    "two_tier_sotp": {
        "label": "Two-tier SOTP (base business P/E or DCF + global pipeline rNPV)",
        "profile": "Large Cap Pharma",
        "premise": "A mature domestic base priced on earnings, with probability-weighted upside from "
                   "out-licensing and new-modality pipelines added on top.",
        "blindspot": "Out-licence milestones are bio-bucks until earned; royalties are partner revenue.",
    },
}

#: Owner's table, 2026-10-06. Ticker -> (archetype, components, why it fits).
POPULAR: dict[str, tuple[str, str, str]] = {
    "ALNY": ("hybrid_sotp",
             "Commercial DCF / EV-Sales on marketed siRNA (Onpattro, Amvuttra, Givlaari, Oxlumo); asset-by-asset "
             "pipeline rNPV; partner royalties (Leqvio from Novartis)",
             "Predictable commercial cash flows, but the platform keeps spinning off clinical-risk assets."),
    "SDGR": ("platform_sotp",
             "Software: EV/ARR or EV/Sales (6-10x specialised engineering SaaS); drug discovery: milestone and "
             "equity NPV plus rNPV of wholly owned candidates",
             "Recurring software and binary clinical development need different discount rates and multiples."),
    "RXRX": ("platform_sotp",
             "Partnership pipeline milestone/royalty rNPV (Roche-Genentech, Bayer); proprietary pipeline rNPV; "
             "platform residual on capitalised platform R&D or target-licence benchmarks",
             "Little product revenue; value rests on partnered milestones and platform validation."),
    "ABCL": ("platform_sotp",
             "Discovery services on EV/Sales; portfolio rNPV of partnered antibody royalties (1-5%); net cash "
             "added back",
             "An outsourced discovery partner: value is volume and downstream royalty options."),
    "NTLA": ("pipeline_rnpv",
             "In vivo and ex vivo programmes, indication-specific rNPV; platform option value",
             "Pre-commercial gene editing: each indication carries its own delivery risk, IP and safety."),
    "BEAM": ("pipeline_rnpv",
             "In vivo and ex vivo programmes, indication-specific rNPV; platform option value",
             "Pre-commercial gene editing: each indication carries its own delivery risk, IP and safety."),
    "MRNA": ("hybrid_sotp",
             "Commercial vaccines (COVID-19, RSV, flu) on a multi-scenario DCF; oncology / latent pipeline "
             "indication-by-indication rNPV (intismeran with Merck); net cash",
             "Commercial respiratory products and unproven oncology / rare-disease assets diverge in maturity."),
    "PFE": ("consolidated_dcf",
            "Consolidated unlevered DCF with patent cliffs and post-COVID base earnings; forward P/E (10-13x), "
            "EV/EBITDA and dividend yield against MRK, BMY, LLY, ABBV",
            "A fully integrated big pharma: total corporate cash flow, patent drag and capital allocation "
            "dominate any single asset."),
    "01801.HK": ("hybrid_sotp",
                 "Marketed portfolio (Tyvyt, biosimilars, mazdutide) on DCF / EV-Sales; innovative Phase 2/3 "
                 "rNPV; ex-China out-licensing milestones and royalties (Lilly, Sanofi, Takeda)",
                 "A cash-generating domestic commercial base with a clinical pipeline carrying trial and "
                 "international-expansion risk."),
    "09688.HK": ("inlicensing_sotp",
                 "Approved Greater China in-licensed products (Zejula, Optune, Vyvgart) on DCF / revenue "
                 "multiples; clinical in-licensed rNPV net of licensor royalties (10-20%+); net cash",
                 "Bridge-and-in-license model: each asset has its own transfer price, royalties and pathway."),
    "01276.HK": ("two_tier_sotp",
                 "Mature domestic base on P/E (A-share peers) or DCF incl. NRDL-listed innovative drugs; "
                 "innovative pipeline and out-licence milestone/royalty rNPV",
                 "Priced partly as Chinese big pharma, increasingly on out-licensing and new-modality upside."),
    "600276.SS": ("two_tier_sotp",
                  "Mature domestic base on P/E (A-share peers) or DCF incl. NRDL-listed innovative drugs; "
                  "innovative pipeline and out-licence milestone/royalty rNPV",
                  "Priced partly as Chinese big pharma, increasingly on out-licensing and new-modality upside."),
}

#: Profiles the logic heuristic may route between (innovator drug companies). Specialty / generic,
#: MedTech, managed care and the rest keep their own routing.
DRUG_PROFILES = frozenset({"Pre-approval Biotech", "Commercial Biotech", "Large Cap Pharma",
                           "Big Pharma (Consolidated DCF)", "Biotech Platform (SOTP)"})

_NON_PRODUCT_WORDS = ("collaborat", "licens", "royalt", "grant", "milestone", "contract revenue", "other revenue")
_PLATFORM_WORDS = ("software", "services", "service revenue", "platform", "drug discovery", "subscription",
                   "contract research", "research services")


def classify_segments(segments: Optional[dict]) -> dict:
    """Split a revenue segmentation ({segment: amount}) into product, platform and other revenue."""
    out = {"product": 0.0, "platform": 0.0, "other": 0.0, "named": {}}
    for name, v in (segments or {}).items():
        if not isinstance(v, (int, float)) or v <= 0:
            continue
        n = str(name).lower()
        if any(w in n for w in _PLATFORM_WORDS):
            k = "platform"
        elif any(w in n for w in _NON_PRODUCT_WORDS):
            k = "other"
        else:
            k = "product"
        out[k] += float(v)
        out["named"][str(name)] = k
    return out


def late_stage_value(assets: list[dict]) -> float:
    """Unapproved Phase 3 / filed assets: risk-adjusted peak (USD) x PEAK_SALES_VALUE_MULTIPLE."""
    total = 0.0
    for a in assets or []:
        if a.get("phase") not in ("phase_3", "filed"):
            continue
        peak = a.get("peak_sales_usd") or 0.0
        p = a.get("ptrs_override") if isinstance(a.get("ptrs_override"), (int, float)) else 0.5
        total += float(peak) * float(p) * PEAK_SALES_VALUE_MULTIPLE
    return total


def select_by_gates(f: dict) -> dict:
    """The owner's four gates. `f`: total_revenue, product_sales, platform_revenue, ev, n_clinical,
    late_stage_value (USD; any may be None), has_approved, operating_profitable. Returns {archetype, trace}."""
    trace = []
    rev = f.get("total_revenue") or 0.0
    prod = f.get("product_sales")
    if prod is None:
        # No segmentation: revenue is product sales only for a company with approved products or at scale;
        # a small biotech's revenue is usually collaboration income.
        if f.get("has_approved") or rev >= 1e9:
            prod = rev
            trace.append("no revenue segmentation: revenue treated as product sales (approved products / scale)")
        else:
            prod = 0.0
            trace.append("no revenue segmentation and no approved product: revenue treated as collaboration income")
    platform = f.get("platform_revenue") or 0.0
    ev = f.get("ev")
    n = f.get("n_clinical")
    lsv = f.get("late_stage_value") or 0.0
    conc = (prod / rev) if rev > 0 else 0.0
    breakthrough = bool(ev and ev > 0 and lsv >= ev)
    commercial_zero = prod < COMMERCIAL_ZERO_USD or (rev > 0 and prod / rev < COMMERCIAL_ZERO_SHARE)
    trace.append(f"Gate 1: commercial concentration {conc:.0%} (> {COMMERCIAL_CONCENTRATION:.0%}?); late-stage "
                 f"value ${lsv / 1e9:,.1f}bn vs EV " + (f"${ev / 1e9:,.1f}bn" if ev else "n/a")
                 + (" -- a breakthrough that could double the enterprise" if breakthrough else "")
                 + (f"; diagram cross-check: product sales / EV {prod / ev:.0%}" if ev else ""))
    # Gate 1 is the maturity filter: material product sales AND an operating profit (a loss-making company is
    # not valued like mature big pharma, whatever its revenue mix).
    mature = f.get("operating_profitable") is not False
    if conc > COMMERCIAL_CONCENTRATION and not breakthrough and not commercial_zero and mature:
        return {"archetype": "consolidated_dcf", "trace": trace + ["-> consolidated DCF (LOE model)"]}
    trace.append(f"Gate 2: {n if n is not None else 'unknown'} clinical programme(s) (<= {N_CLINICAL_STANDALONE_MAX}?), "
                 f"product sales ${prod / 1e6:,.0f}m ({'~0' if commercial_zero else 'material'})")
    if commercial_zero and n is not None and n <= N_CLINICAL_STANDALONE_MAX:
        return {"archetype": "pipeline_rnpv", "trace": trace + ["-> standalone pipeline rNPV"]}
    trace.append(f"Gate 3: platform / service revenue ${platform / 1e6:,.0f}m")
    if platform > 0 and (rev <= 0 or platform / rev >= COMMERCIAL_ZERO_SHARE):
        return {"archetype": "platform_sotp", "trace": trace + ["-> platform two-pillar SOTP"]}
    if commercial_zero:
        return {"archetype": "pipeline_rnpv",
                "trace": trace + ["Gate 4: no commercial base -> asset-by-asset pipeline SOTP (rNPV)"]}
    return {"archetype": "hybrid_sotp", "trace": trace + ["Gate 4: commercial base + pipeline -> hybrid SOTP"]}


def select(ticker: str, features: Optional[dict] = None) -> dict:
    """{archetype, profile, source, trace, components?, reason?}: the owner's table for a popular
    ticker, else the gates."""
    t = str(ticker or "").upper()
    if t in POPULAR:
        arch, comp, why = POPULAR[t]
        return {"archetype": arch, "profile": ARCHETYPES[arch]["profile"], "source": "owner table (2026-10-06)",
                "label": ARCHETYPES[arch]["label"], "components": comp, "reason": why, "trace": []}
    r = select_by_gates(features or {})
    a = r["archetype"]
    return {"archetype": a, "profile": ARCHETYPES[a]["profile"], "source": "gates (2026-10-06)",
            "label": ARCHETYPES[a]["label"], "trace": r["trace"]}
