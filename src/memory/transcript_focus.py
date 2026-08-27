"""
src/memory/transcript_focus.py
==============================
What to pull out of an earnings call, per industry and sub-sector.

The base extraction in assumption_extract.py asks every company the same
questions — guidance, segments, margins, capital allocation, one-offs. That
is the right floor, but it wastes the most valuable property of a transcript:
management answering the questions that matter for THEIR business. A property
developer's call turns on contracted sales and gearing; an S-REIT's turns on
rental reversion and aggregate leverage against the MAS cap; a foundry's on
utilisation and ASP direction. Asking all three about "segment growth" gets a
generic answer from each.

This module supplies the industry overlay. It resolves off the FMP `industry`
string (the same one that keys src/data/regional_comps), falling back to the
FMP `sector`, so it works identically for US, HK and SG names now that all
three route through FMP.

Two output groups:

  * `targets` — industry KPIs that feed the NUMBERS. These land in the
    existing `kpis` array of the earnings_assumptions row, so R3's detectors
    and the DCF's _r1_structured_guidance path pick them up with no new
    plumbing.
  * `qualitative` — what feeds the WRITE-UP: tone shift, the topics analysts
    pushed on, and risk language that is new this quarter. Stored separately
    (see transcript_signals in assumption_store) because none of it should
    ever move an intrinsic value on its own.

Matching is longest-key-first on a normalised industry string, so
"REIT - Retail" beats a generic "REIT" entry.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


# ── Industry overlays ───────────────────────────────────────────────────────
#
# Keys are matched as case-insensitive substrings of the FMP industry string.
# Coverage is deliberately weighted toward the industries that dominate the
# HKEX and SGX universes (HK: property development 84 names, biotech 83,
# engineering & construction 75; SG: REITs across four sub-types) plus the
# US-heavy ones already in the book.

INDUSTRY_TARGETS: dict[str, tuple[str, ...]] = {
    # ── Financials ──────────────────────────────────────────────────────────
    "banks": (
        "net interest margin — guided direction and rate sensitivity",
        "credit cost / provisions and non-performing loan formation",
        "CET1 ratio and stated buyback or dividend capacity",
        "loan growth by segment and deposit cost / CASA mix",
        "fee and non-interest income mix",
    ),
    "insurance": (
        "new business value and value-of-new-business margin",
        "combined ratio and reserve development",
        "investment yield and asset-liability duration",
        "solvency ratio and capital return capacity",
    ),
    "asset management": (
        "net flows and assets under management by channel",
        "fee rate compression",
        "performance fee contribution",
    ),
    "capital markets": (
        "average daily turnover and cash-market volumes",
        "listing pipeline and IPO fee income",
        "margin financing balance and net investment income",
    ),
    # ── Real estate ─────────────────────────────────────────────────────────
    "reit": (
        "rental reversion percentage on renewals",
        "portfolio occupancy and weighted average lease expiry",
        "aggregate leverage versus the regulatory cap, and interest coverage",
        "cost of debt, fixed-rate hedge share and refinancing schedule",
        "distribution per unit guidance and payout policy",
        "acquisitions, divestments and any equity fund-raising plans",
    ),
    "real estate - development": (
        "contracted sales value and volume versus the full-year target",
        "land bank additions, replenishment cost and land-cost-to-ASP ratio",
        "presale cash collection rate",
        "net gearing and debt maturity profile",
        "project delivery pace, completions and any impairment on inventory",
    ),
    "real estate - services": (
        "gross floor area under management and new contract wins",
        "value-added services revenue mix",
        "receivables from related developers and collection risk",
    ),
    # ── Technology ──────────────────────────────────────────────────────────
    "internet content": (
        "daily and monthly active users, engagement and time spent",
        "revenue mix across advertising, commerce, gaming and cloud",
        "take rate and monetisation per user",
        "AI capital expenditure and its stated margin drag",
        "content or game pipeline and deferred revenue",
    ),
    "software": (
        "annual recurring revenue, net revenue retention and churn",
        "remaining performance obligation and billings",
        "seat expansion versus price increase as growth drivers",
        "gross margin trajectory including AI serving cost",
    ),
    "semiconductor": (
        "fab utilisation rate",
        "product and node mix, and average selling price direction",
        "capital expenditure intensity and capacity additions",
        "inventory correction stage and channel inventory weeks",
        "export-control or geopolitical exposure to specific end markets",
    ),
    "hardware": (
        "unit shipments and average selling price",
        "component cost trend and any pass-through",
        "order backlog and book-to-bill",
    ),
    # ── Healthcare ──────────────────────────────────────────────────────────
    "biotechnology": (
        "clinical trial readout timing by asset",
        "regulatory submissions and approval milestones",
        "cash runway in quarters at current burn",
        "partnership, licensing and milestone economics",
        "reimbursement status — for China names, NRDL inclusion and the "
        "price cut accepted",
    ),
    "drug manufacturers": (
        "volume-based procurement exposure and price erosion",
        "R&D pipeline progression and launch schedule",
        "generic versus innovative revenue mix",
    ),
    "medical - care facilities": (
        "bed occupancy, patient volumes and case mix",
        "revenue per patient day and payor mix",
        "expansion capex and new hospital ramp",
    ),
    # ── Industrials ─────────────────────────────────────────────────────────
    "engineering & construction": (
        "order book, new order intake and book-to-bill",
        "gross margin on the backlog versus work completed",
        "receivables, retention balances and impairment",
        "overseas versus domestic revenue mix",
    ),
    "industrial - machinery": (
        "order intake and backlog coverage",
        "input cost trend and pass-through to price",
        "export mix and tariff exposure",
        "aftermarket and services revenue share",
    ),
    "aerospace & defense": (
        "order book and multi-year programme funding",
        "programme execution, cost overruns and milestone slippage",
    ),
    "airlines": (
        "load factor, yield and revenue per available seat kilometre",
        "fuel cost, hedging position and unit cost ex-fuel",
        "capacity plans and fleet delivery schedule",
    ),
    "conglomerates": (
        "performance and outlook by operating segment",
        "portfolio actions — divestments, listings, capital recycling",
        "holding-company discount commentary and net asset value",
    ),
    # ── Consumer ────────────────────────────────────────────────────────────
    "packaged foods": (
        "volume versus price contribution to growth",
        "raw material cost trend and gross margin outlook",
        "channel mix and distributor inventory",
    ),
    "beverages": (
        "volume versus price/mix contribution",
        "premiumisation and product mix shift",
        "input cost and hedging",
    ),
    "restaurants": (
        "same-store sales growth and average ticket",
        "store openings, closures and unit economics",
        "delivery mix and its margin impact",
    ),
    "specialty retail": (
        "same-store sales and footfall",
        "inventory position and markdown risk",
        "store network changes",
    ),
    "grocery stores": (
        "same-store sales and basket size",
        "gross margin and shrinkage",
        "format and geography mix",
    ),
    "auto manufacturers": (
        "unit deliveries and order backlog",
        "vehicle gross margin excluding regulatory credits",
        "average selling price and discounting",
        "new model launch cadence and capacity utilisation",
    ),
    # ── Energy / Materials / Utilities ──────────────────────────────────────
    "oil & gas": (
        "production volumes and realised price",
        "lifting cost and capital expenditure plan",
        "reserve replacement",
    ),
    "utilities": (
        "tariff decisions and regulated return",
        "capacity additions and capital plan",
        "fuel cost pass-through mechanism",
    ),
    "renewable": (
        "installed capacity, additions and utilisation hours",
        "tariff and subsidy receivable position",
        "curtailment rate",
    ),
    "steel": (
        "output volumes, spreads and raw-material cost",
        "capacity utilisation and inventory",
    ),
    "gold": (
        "production ounces, all-in sustaining cost and grade",
        "reserve position and mine life",
    ),
    "shipping": (
        "freight rates, fleet utilisation and contract coverage",
        "newbuild deliveries and scrapping",
    ),
    "agricultural": (
        "crush volumes and margins",
        "commodity price exposure and hedging",
        "biological asset revaluation",
    ),
    "telecommunications": (
        "subscriber net additions and ARPU by segment",
        "capital expenditure cycle and 5G spend",
        "enterprise and cloud revenue contribution",
    ),
}

# Sector-level fallback when the industry string matches nothing above. FMP
# sector names, including the three that differ from the GICS labels
# ("Financial Services", "Consumer Cyclical", "Consumer Defensive").
SECTOR_TARGETS: dict[str, tuple[str, ...]] = {
    "financial services": (
        "net interest margin or fee-rate direction",
        "credit quality and provisioning",
        "regulatory capital and capital-return capacity",
    ),
    "real estate": (
        "occupancy, rental reversion or contracted sales",
        "gearing, cost of debt and refinancing schedule",
        "asset valuation movement",
    ),
    "technology": (
        "revenue mix by product line and recurring share",
        "gross margin trajectory",
        "capital expenditure and capacity plans",
    ),
    "healthcare": (
        "pipeline milestones and regulatory timing",
        "pricing and reimbursement changes",
        "cash runway where pre-profit",
    ),
    "industrials": (
        "order book, backlog and book-to-bill",
        "input cost and pass-through",
        "capacity utilisation",
    ),
    "consumer cyclical": (
        "volume versus price contribution",
        "channel inventory and discounting",
        "store or capacity footprint changes",
    ),
    "consumer defensive": (
        "volume versus price contribution",
        "input cost and gross margin outlook",
    ),
    "communication services": (
        "subscriber or user metrics and ARPU",
        "advertising demand commentary",
        "content or network capital expenditure",
    ),
    "energy": (
        "production volumes and realised pricing",
        "capital expenditure and cost per unit",
    ),
    "basic materials": (
        "output volumes, pricing spread and input cost",
        "capacity utilisation",
    ),
    "utilities": (
        "tariff and regulated return",
        "capacity additions and capital plan",
    ),
}

# Asked of every company, regardless of industry. These feed the write-up,
# never the numbers.
QUALITATIVE_TARGETS: tuple[str, ...] = (
    "tone_shift — has management's confidence changed versus the prior "
    "call: are previously firm commitments now hedged, or vice versa",
    "qa_pressure — which topics drew repeated or multi-analyst questioning, "
    "and whether management answered directly or deflected",
    "new_risks — risks named this quarter that were not raised previously",
    "strategic_pivots — changes in stated strategy, capital priorities or "
    "competitive positioning",
    "regulatory — policy, regulatory or geopolitical commentary "
    "(materially more load-bearing for HK/China names than for US)",
)


def _norm(text: Optional[str]) -> str:
    return (text or "").strip().lower()


def targets_for(industry: Optional[str], sector: Optional[str] = None
                ) -> tuple[str, tuple[str, ...]]:
    """(label, targets) for an industry/sector pair.

    Longest matching industry key wins, so "real estate - development" is
    preferred over a bare "real estate". Falls back to the sector map, then
    to ("", ()) — which leaves the base extraction untouched rather than
    inventing generic prompts.
    """
    ind = _norm(industry)
    if ind:
        matches = [k for k in INDUSTRY_TARGETS if k in ind]
        if matches:
            best = max(matches, key=len)
            return industry or best, INDUSTRY_TARGETS[best]

    sec = _norm(sector)
    if sec in SECTOR_TARGETS:
        return sector or sec, SECTOR_TARGETS[sec]
    return "", ()


def targets_for_ticker(ticker: str) -> tuple[str, tuple[str, ...]]:
    """As targets_for, resolving the classification from the ticker.

    Soft-fails to ("", ()) so a classification miss never blocks extraction.
    """
    try:
        from src.data.regional_comps import get_fmp_classification
        info = get_fmp_classification(ticker)
    except Exception:
        return "", ()
    if not info:
        return "", ()
    return targets_for(info.get("industry"), info.get("sector"))


def industry_prompt_block(ticker: str) -> str:
    """The industry overlay as a prompt fragment, or "" when unresolvable."""
    label, targets = targets_for_ticker(ticker)
    if not targets:
        return ""
    return (
        f"\n\nINDUSTRY FOCUS ({label}) — when management addresses any of "
        "these, capture it in `kpis` with the figure and a supporting quote. "
        "Omit silently what was not discussed; never infer a value:\n- "
        + "\n- ".join(targets)
    )
