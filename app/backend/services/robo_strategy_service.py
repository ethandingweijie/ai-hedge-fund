"""
app/backend/services/robo_strategy_service.py
=================================================
Deterministic asset-allocation engine — Python port of the "Robo Strategy"
Next.js prototype's src/lib/allocation.ts, wired to this app's live data
sources instead of the prototype's static arrays + mock price table.

No LLM, no randomness (the prototype's `seed` param was dead code and is not
ported) — same questionnaire answers always produce the same portfolio.

Deliberate fixes vs. the ported prototype (all called out inline below):
  1. ETF category partition is now a mutually-exclusive `bucket` tag instead
     of the prototype's exclusion filter, which accidentally let REIT ETFs
     be double-picked via both the "stock" and "REIT" category rankings.
  2. Breakdown charts are computed separately per mode (etf vs stock) instead
     of the prototype always deriving them from the stock portfolio alone,
     even while viewing the ETF tab.
  3. Both select_etfs and select_stocks allocate geography FIRST (proportional
     slot counts across US/Europe/Asia-Pacific/Emerging Markets, with a
     guaranteed floor for any region with nonzero user weight), THEN rank
     within each region pool — instead of one blended cross-region score.
     A single blended ranking let a broad, ultra-cheap US fund (VTI's 0.03%
     expense ratio vs. a China ETF's 0.6-0.9%) structurally out-score a
     concentrated regional fund even after a user meaningfully reduced (but
     didn't zero out) US geography weight, since the expense-ratio penalty
     dwarfed any realistic region-fit swing from a moderate reallocation —
     the reported "reduced US, but still no China/HK" bug. See
     `_proportional_counts` for the guarantee mechanism.

Everything else — the risk/horizon glide-path formula, the sector/region
scoring shape, the pick counts, the equal-weight-within-category logic, the
10%-max-position cap — is ported faithfully, including its exact rounding
behaviour (Math.round() rounds .5 up; Python's round() rounds .5 to even, so
_round_half_up() is used everywhere the source used Math.round()).
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Optional

from app.backend.services import etf_metadata_service, screener_service

# Geography taxonomy: US / Europe / Asia-Pacific / Emerging Markets (matches
# ROBO_REGIONS on the frontend and etf_metadata_service's _COUNTRY_TO_REGION).
# Stock-mode candidates get a REAL region tag straight from their listing
# market — HK stocks are tagged "Emerging Markets" (grouped with China, same
# Greater-China-proxy rationale as etf_metadata_service's country map: HK is
# how a user actually dials up China exposure here) and SG stocks are tagged
# "Asia-Pacific". Europe has no stock-mode candidates (this app's screener
# only covers US/HK/SG) — a nonzero Europe weight simply gets 0 stock-mode
# slots; ETF mode has full Europe coverage via VEA/EFA/VXUS.
_HK_STOCK_REGION = "Emerging Markets"
_SG_STOCK_REGION = "Asia-Pacific"

# HK/SG screener sector names -> canonical 11-sector taxonomy
# (app/backend/services/screener_service.py's _SCREENER_SECTORS), so a
# sector weight the user set applies consistently across all three source
# universes. Sectors with no HK/SG equivalent (Communication Services aside
# from SG's Telco, Consumer Defensive, Basic Materials, Utilities) simply
# have no HK/SG candidates — fewer picks for that sector/region combo, not
# an error.
_HK_SECTOR_TO_CANONICAL = {
    "Technology": "Technology",
    "Financials": "Financial Services",
    "Property": "Real Estate",
    "Consumer": "Consumer Cyclical",
    "Industrials": "Industrials",
    "Healthcare": "Healthcare",
    "Energy": "Energy",
}
_SG_SECTOR_TO_CANONICAL = {
    "Financials": "Financial Services",
    "REIT": "Real Estate",
    "Tech": "Technology",
    "Industrials": "Industrials",
    "Consumer": "Consumer Cyclical",
    "Property": "Real Estate",
    "Telco": "Communication Services",
    "Energy": "Energy",
}


def _round_half_up(x: float) -> int:
    return math.floor(x + 0.5) if x >= 0 else math.ceil(x - 0.5)


# ── Step 1: risk + horizon -> base asset-class split ──────────────────────────

def get_base_allocation(risk: str, horizon: str) -> dict:
    risk_multiplier = {"aggressive": 1.0, "moderate": 0.7}.get(risk, 0.4)
    horizon_bonus = {
        "15+ years": 10,
        "7-15 years": 5,
        "3-7 years": 0,
    }.get(horizon, -10)  # "<3 years" and anything unrecognised

    stocks = _round_half_up(min(95, max(20, risk_multiplier * 80 + horizon_bonus)))
    bonds = _round_half_up(min(60, max(0, (1 - risk_multiplier) * 45 - horizon_bonus / 2)))
    commodities = 5 if risk in ("aggressive", "moderate") else 0
    reits = max(0, 100 - stocks - bonds - commodities)

    total = stocks + bonds + commodities + reits
    if total == 0:
        return {"stocks": 0, "bonds": 0, "commodities": 0, "reits": 0}
    return {
        "stocks": _round_half_up((stocks / total) * 100),
        "bonds": _round_half_up((bonds / total) * 100),
        "commodities": _round_half_up((commodities / total) * 100),
        "reits": _round_half_up((reits / total) * 100),
    }


# ── Scoring primitives ─────────────────────────────────────────────────────────

def score_sector(sector: str, sector_prefs: dict) -> float:
    return sector_prefs.get(sector, 50)


def normalize_weights(weights: dict) -> dict:
    total = sum(weights.values())
    if total == 0:
        return dict(weights)
    return {k: (v / total) * 100 for k, v in weights.items()}


def _dominant(weights: Optional[dict]) -> Optional[str]:
    """Pick the single highest-weight key, for a one-line display label on a
    fund whose real composition is a weighted mix, not one category."""
    if not weights:
        return None
    return max(weights.items(), key=lambda kv: kv[1])[0]


def _proportional_counts(weights: dict[str, float], total_count: int) -> dict[str, int]:
    """Split `total_count` slots across `weights`' nonzero-weight keys
    proportionally, largest-remainder-style, but with a floor of 1 slot for
    every key with weight > 0 (as long as slots remain) — this is the actual
    mechanism that guarantees "I set a nonzero China/HK/Emerging-Markets
    weight" translates into at least one pick from that region, instead of
    a single blended score letting it get crowded out entirely."""
    active = {k: w for k, w in weights.items() if w > 0}
    if not active or total_count <= 0:
        return {}
    norm = normalize_weights(active)
    ordered = sorted(norm.items(), key=lambda kv: -kv[1])
    counts: dict[str, int] = {}
    remaining = total_count
    for i, (key, w) in enumerate(ordered):
        if remaining <= 0:
            break
        is_last = i == len(ordered) - 1
        share = remaining if is_last else min(remaining, max(1, _round_half_up((w / 100) * total_count)))
        counts[key] = share
        remaining -= share
    return counts


# ── Step 4a: ETF selection ─────────────────────────────────────────────────────

def select_etfs(
    base_alloc: dict,
    sector_weights: dict,
    geo_weights: dict,
    total_amount: float,
    etf_universe: list[dict],
) -> list[dict]:
    selected: list[dict] = []
    max_etfs = 8

    # Deliberate fix vs. the prototype: mutually-exclusive bucket membership
    # instead of an exclusion filter, so a REIT ETF can't be picked twice.
    stock_etfs = [e for e in etf_universe if e.get("bucket") == "stock"]
    bond_etfs = [e for e in etf_universe if e.get("bucket") == "bond"]
    commodity_etfs = [e for e in etf_universe if e.get("bucket") == "commodity"]
    reit_etfs = [e for e in etf_universe if e.get("bucket") == "reit"]

    def score_etf(etf: dict) -> float:
        sector_w = etf.get("sectorWeights") or {}
        region_w = etf.get("regionWeights") or {}
        # Weighted dot-product against the fund's REAL composition (live
        # FMP sector/country-weightings) rather than a single hand-typed
        # tag — a blended fund like VXUS scores correctly as a mix instead
        # of being forced into one bucket. Falls back to the prototype's
        # neutral defaults (50 / 30) when FMP had no data for this ticker.
        if sector_w:
            sector_fit = sum(pct * sector_weights.get(sec, 50) for sec, pct in sector_w.items()) / 100
        else:
            sector_fit = 50
        if region_w:
            region_fit = sum(pct * geo_weights.get(reg, 30) for reg, pct in region_w.items()) / 100
        else:
            region_fit = 30
        expense_ratio = etf.get("expenseRatio") or 0
        # Expense ratio is a minor tiebreaker, not a primary driver: real
        # China/India funds run 0.6-0.9% vs. ~0.03% for VTI/VOO. The
        # prototype's *10 multiplier (tuned against its own mock/narrow
        # ratio range) turned that gap into a 6-9 point penalty — bigger
        # than any realistic region_fit swing from a moderate geography
        # reallocation — so cheap broad-US fields always won regardless of
        # region preference. *2 keeps cost a real factor without letting it
        # override what the user actually asked for.
        return sector_fit * 0.6 + region_fit * 0.4 - expense_ratio * 2

    def build_item(etf: dict, alloc_percent: float) -> dict:
        amount = (alloc_percent / 100) * total_amount
        price = etf["price"]
        return {
            "ticker": etf["ticker"],
            "name": etf.get("name") or etf["ticker"],
            "category": etf.get("bucket"),
            # Single dominant tag — for the holdings-table row / CSV export,
            # where one label per line is what's readable. NOT used for the
            # breakdown charts (see sectorWeights/regionWeights below and
            # calculate_breakdowns) — a broad fund's dominant sector is a
            # poor summary of a fund that's actually spread across 11
            # sectors (e.g. VTI is 36% Technology, not 100%).
            "sector": _dominant(etf.get("sectorWeights")),
            "region": _dominant(etf.get("regionWeights")),
            # Full real composition (live FMP sector/country-weightings) —
            # what calculate_breakdowns uses to aggregate this holding's
            # dollar allocation PROPORTIONALLY across every sector/region it
            # actually holds, instead of dumping 100% of it into one tag.
            "sectorWeights": etf.get("sectorWeights") or {},
            "regionWeights": etf.get("regionWeights") or {},
            "allocationPercent": round(alloc_percent, 2),
            "amount": round(amount, 2),
            "shares": math.floor(amount / price),
            "price": price,
            "expenseRatio": etf.get("expenseRatio"),
            "riskLevel": None,  # no single risk tier for a fund — omitted, not faked
            "topHoldings": etf.get("topHoldings") or [],
            # Constituents for the equity look-through. Carried on the item
            # (not re-fetched) so the aggregation stays a pure function of
            # the plan, and coverage travels with the weights it describes.
            "holdings": etf.get("holdings") or [],
            "holdingsCount": etf.get("holdingsCount") or 0,
            "holdingsCoveredPct": etf.get("holdingsCoveredPct") or 0.0,
        }

    def allocate_to_category(etfs: list[dict], target_percent: float, count: int) -> None:
        if target_percent <= 0 or not etfs or count <= 0:
            return
        # Can't compute shares without a live price — exclude rather than
        # fabricate one (the prototype used a mock-price fallback; we don't).
        priced = [e for e in etfs if e.get("price")]
        picks = sorted(priced, key=score_etf, reverse=True)[:count]
        if not picks:
            return
        per_pick = target_percent / len(picks)
        for etf in picks:
            selected.append(build_item(etf, per_pick))

    def allocate_equity_etfs(etfs: list[dict], target_percent: float, count: int) -> None:
        """Equity sleeve only: split `count` slots across US/Europe/
        Asia-Pacific/Emerging Markets PROPORTIONALLY to geo_weights (with a
        guaranteed floor — see _proportional_counts), THEN rank within each
        region pool by score_etf — instead of one blended ranking across all
        ~35 equity ETFs. See the module docstring for why the blended
        version structurally favored cheap broad-US funds."""
        if target_percent <= 0 or not etfs or count <= 0:
            return
        priced = [e for e in etfs if e.get("price")]
        by_region: dict[str, list[dict]] = {"US": [], "Europe": [], "Asia-Pacific": [], "Emerging Markets": []}
        for e in priced:
            region = _dominant(e.get("regionWeights"))
            if region in by_region:
                by_region[region].append(e)

        region_counts = _proportional_counts(
            {r: geo_weights.get(r, 0) for r in by_region if by_region[r]}, count,
        )
        picks: list[dict] = []
        for region, region_count in region_counts.items():
            pool = sorted(by_region[region], key=score_etf, reverse=True)
            picks.extend(pool[:region_count])

        # Safety fallback: fill any shortfall (a region requested more slots
        # than its pool had) from the best remaining candidates overall,
        # rather than silently under-filling the sleeve.
        if len(picks) < count:
            picked_tickers = {p["ticker"] for p in picks}
            leftover = sorted(
                (e for pool in by_region.values() for e in pool if e["ticker"] not in picked_tickers),
                key=score_etf, reverse=True,
            )
            picks.extend(leftover[: count - len(picks)])

        if not picks:
            return
        per_pick = target_percent / len(picks)
        for etf in picks:
            selected.append(build_item(etf, per_pick))

    stock_count = min(5, max_etfs - 3)
    bond_count = 2 if base_alloc["bonds"] > 0 else 0
    comm_count = 1 if base_alloc["commodities"] > 0 else 0
    reit_count = 1 if base_alloc["reits"] > 0 else 0

    allocate_equity_etfs(stock_etfs, base_alloc["stocks"], stock_count)
    allocate_to_category(bond_etfs, base_alloc["bonds"], bond_count)
    allocate_to_category(commodity_etfs, base_alloc["commodities"], comm_count)
    allocate_to_category(reit_etfs, base_alloc["reits"], reit_count)

    return selected


# ── Step 4b: individual stock selection ────────────────────────────────────────

def select_stocks(
    sector_weights: dict,
    geo_weights: dict,
    risk: str,
    total_amount: float,
    candidates: list[dict],
) -> list[dict]:
    """Region-first, sector-second: split target_count across regions by
    geo_weights (guaranteed floor per _proportional_counts — this is what
    makes "reduce US, raise Emerging Markets" actually surface HK stocks
    instead of them being crowded out sector-by-sector), then within each
    region's own candidate pool, distribute across whichever sectors that
    pool actually covers. The old sector-first ordering meant any sector
    with zero HK/SG candidates (Consumer Defensive, Basic Materials,
    Utilities — this app's screener has no other non-US market) silently
    filled with US stocks every time, regardless of geography preference;
    doing region first means that gap only affects sector distribution
    *within* the non-US sleeve, not how big the non-US sleeve is."""
    selected: list[dict] = []
    target_count = {"conservative": 12, "aggressive": 18}.get(risk, 15)

    region_pools: dict[str, list[dict]] = {}
    for c in candidates:
        region_pools.setdefault(c.get("region", ""), []).append(c)

    norm_sectors_all = normalize_weights(sector_weights)
    active_geo = {r: geo_weights.get(r, 0) for r in region_pools if region_pools[r]}
    if not any(w > 0 for w in active_geo.values()):
        # The user's entire geography preference sits in regions this mode
        # has no live candidates for (Europe has no stock-mode data source;
        # Asia-Pacific's only source, Singapore, currently has none either
        # — see _SG_STOCK_REGION). Falling through here would return an
        # empty portfolio. Rather than that, fall back to an even split
        # across whichever regions DO have candidates (today: US and
        # Emerging Markets) — a coverage limitation should degrade to "best
        # available", never to nothing.
        active_geo = {r: 1 for r in region_pools if region_pools[r]}
    norm_geo = normalize_weights(active_geo)
    region_counts = _proportional_counts(dict(norm_geo), target_count)

    for region, region_count in region_counts.items():
        region_percent = norm_geo.get(region, 0)
        pool = region_pools[region]

        # Re-normalize sector preference to only the sectors THIS region's
        # pool actually covers, so a coverage gap (e.g. HK/SG have no
        # Utilities candidates) redistributes across the sectors that pool
        # does have instead of implicitly falling back to US.
        available_sectors = {c.get("sector") for c in pool if c.get("sector")}
        region_sector_weights = {
            s: w for s, w in norm_sectors_all.items() if s in available_sectors and w > 3
        }
        if not region_sector_weights and available_sectors:
            region_sector_weights = {s: 100 / len(available_sectors) for s in available_sectors}
        norm_region_sectors = normalize_weights(region_sector_weights)
        sector_counts = _proportional_counts(dict(region_sector_weights), region_count)

        for sector, sector_count in sector_counts.items():
            sector_stocks = [c for c in pool if c.get("sector") == sector]

            scored = []
            for stock in sector_stocks:
                score = 0.0
                if risk == "conservative" and stock.get("riskLevel") == "low":
                    score += 20
                if risk == "aggressive" and stock.get("riskLevel") == "high":
                    score += 15
                if stock.get("isMegaCap"):
                    score += 10
                scored.append((stock, score))
            scored.sort(key=lambda x: -x[1])
            picks = [s for s, _ in scored[:sector_count]]
            if not picks:
                continue

            # region_percent * sector's share within the region = this
            # sector-within-region's share of the whole portfolio.
            sector_percent = region_percent * norm_region_sectors.get(sector, 0) / 100
            per_pick_weight = sector_percent / len(picks)
            for stock in picks:
                amount = (per_pick_weight / 100) * total_amount
                price = stock.get("price") or 0
                selected.append({
                    "ticker": stock["ticker"],
                    "name": stock.get("name") or stock["ticker"],
                    "sector": stock.get("sector"),
                    "region": stock.get("region"),
                    "allocationPercent": round(per_pick_weight, 2),
                    "amount": round(amount, 2),
                    "shares": math.floor(amount / price) if price else 0,
                    "price": price,
                    "riskLevel": stock.get("riskLevel"),
                })

    # Enforce max 10% per position.
    max_per_position = total_amount * 0.1
    for item in selected:
        if item["amount"] > max_per_position:
            item["amount"] = round(max_per_position, 2)
            item["allocationPercent"] = round((item["amount"] / total_amount) * 100, 2) if total_amount else 0
            price = item.get("price") or 0
            item["shares"] = math.floor(item["amount"] / price) if price else 0

    return selected


# ── Step 5: breakdowns ──────────────────────────────────────────────────────────

def calculate_breakdowns(items: list[dict]) -> dict:
    """ETF items carry full look-through composition (sectorWeights /
    regionWeights, each summing to ~100 across the fund's real holdings) —
    aggregate each fund's dollar allocation PROPORTIONALLY across every
    sector/region it actually holds, not into a single dominant tag. A
    broad fund like VTI is ~36% Technology, ~12% Financial Services, ...
    across all 11 sectors; crediting its whole allocation to "Technology"
    alone would badly overstate one sector and erase the rest of the fund's
    real composition. Individual-stock items (Individual Stocks mode) have
    no weights dict — a single stock genuinely IS 100% one sector/region,
    so they fall back to the flat sector/region tag, which is exact for
    them, not an approximation."""
    sector_breakdown: dict[str, float] = {}
    geography_breakdown: dict[str, float] = {}
    risk_breakdown: dict[str, float] = {}
    for item in items:
        pct = item.get("allocationPercent", 0) or 0
        risk_level = item.get("riskLevel")

        sector_weights = item.get("sectorWeights")
        if sector_weights:
            for sector, weight in sector_weights.items():
                sector_breakdown[sector] = sector_breakdown.get(sector, 0) + pct * weight / 100
        elif item.get("sector"):
            sector = item["sector"]
            sector_breakdown[sector] = sector_breakdown.get(sector, 0) + pct

        region_weights = item.get("regionWeights")
        if region_weights:
            for region, weight in region_weights.items():
                geography_breakdown[region] = geography_breakdown.get(region, 0) + pct * weight / 100
        elif item.get("region"):
            region = item["region"]
            geography_breakdown[region] = geography_breakdown.get(region, 0) + pct

        if risk_level:  # ETF items carry no riskLevel — deliberately excluded, not faked
            risk_breakdown[risk_level] = risk_breakdown.get(risk_level, 0) + pct
    return {"sector": sector_breakdown, "geography": geography_breakdown, "risk": risk_breakdown}


# ── Step 5b: equity look-through ───────────────────────────────────────────────

# Buckets whose constituents are shares in companies. Bond and commodity
# funds are deliberately excluded: BNDX's "holdings" are instruments like
# "Dexia SA 01/21/2028" and GLD's is bullion, and neither is an equity
# position however the fund reports it.
_EQUITY_BUCKETS = {"stock", "reit"}


def calculate_equity_lookthrough(items: list[dict], total_investment: float = 0.0,
                                 top_n: int = 25) -> dict:
    """Which individual COMPANIES a portfolio of funds actually owns.

    The ETF plan tells a user they hold VTI and KWEB; it does not tell them
    their largest single company exposure is NVDA, or that Tencent arrives
    through two different funds. This resolves equity funds to their
    constituents and aggregates by company, so fund choice and company
    exposure are both visible.

    Allocation is distributed strictly in proportion to the weights actually
    available: a fund contributing `pct` of the plan with a holding at `w`
    percent of the fund contributes `pct * w / 100`. Anything the stored
    constituents do not cover is reported as `uncovered_pct` rather than
    spread over the rows that are present.

    That is the whole design decision here, and it is not cosmetic. The
    obvious alternative — renormalising over the visible weights so they sum
    to the fund's full allocation — assumes the unseen tail resembles the
    visible head. That holds at 99% coverage and fails badly below it: BNDX
    reports 3 usable rows totalling 0.01% of the fund, and renormalising
    spread its entire 9.5% plan weight across those three, showing a single
    bond at 4.6% of the portfolio. Proportional allocation can only ever
    understate a position, never invent one, and the shortfall is named.

    Individual stocks (Individual Stocks mode) pass straight through — a
    single stock genuinely IS 100% itself, which is exact.

    Returns:
        {positions, position_count, resolved_pct, uncovered_pct,
         non_equity_pct, top_concentration_pct, coverage}
      positions       — [{symbol, name, allocationPercent, amount, viaFunds}]
      resolved_pct    — plan share attributed to named companies
      uncovered_pct   — equity-fund weight whose constituents are not stored
      non_equity_pct  — bond/commodity funds, excluded by design
      coverage        — per-fund {ticker: coveredPct}, so a thin fund is visible
    """
    by_symbol: dict[str, dict] = {}
    uncovered_pct = 0.0
    non_equity_pct = 0.0
    coverage: dict[str, float] = {}

    for item in items:
        pct = float(item.get("allocationPercent") or 0)
        if pct <= 0:
            continue
        # ETF items are keyed by `ticker`, individual-stock items by `symbol`.
        symbol = item.get("ticker") or item.get("symbol")
        bucket = item.get("category")
        holdings = item.get("holdings") or []
        is_fund = bool(item.get("sectorWeights") or item.get("regionWeights")
                       or holdings or bucket)

        if not is_fund:
            # An individual stock resolves to itself.
            if not symbol:
                uncovered_pct += pct
                continue
            entry = by_symbol.setdefault(symbol, {
                "symbol": symbol, "name": item.get("name") or symbol,
                "allocationPercent": 0.0, "viaFunds": [],
            })
            entry["allocationPercent"] += pct
            entry["viaFunds"].append({"ticker": symbol,
                                      "contributionPct": round(pct, 4),
                                      "direct": True})
            continue

        if bucket is not None and bucket not in _EQUITY_BUCKETS:
            non_equity_pct += pct
            continue

        if not holdings:
            uncovered_pct += pct
            continue

        covered = 0.0
        for h in holdings:
            w = float(h.get("weightPercentage") or 0)
            asset = h.get("asset")
            if w <= 0 or not asset:
                continue
            covered += w
            contribution = pct * w / 100.0
            entry = by_symbol.setdefault(asset, {
                "symbol": asset, "name": h.get("name") or asset,
                "allocationPercent": 0.0, "viaFunds": [],
            })
            entry["allocationPercent"] += contribution
            entry["viaFunds"].append({
                "ticker": symbol, "contributionPct": round(contribution, 4),
                "direct": False,
            })
        coverage[symbol or "?"] = round(covered, 2)
        uncovered_pct += pct * max(0.0, 100.0 - covered) / 100.0

    positions = sorted(by_symbol.values(), key=lambda e: -e["allocationPercent"])
    for e in positions:
        e["allocationPercent"] = round(e["allocationPercent"], 4)
        e["amount"] = round(total_investment * e["allocationPercent"] / 100, 2)
        # Collapse repeat contributions from one fund, heaviest fund first,
        # so "mostly via VTI" reads at a glance.
        merged: dict[str, dict] = {}
        for v in e["viaFunds"]:
            m = merged.setdefault(v["ticker"], {"ticker": v["ticker"],
                                                "contributionPct": 0.0,
                                                "direct": v["direct"]})
            m["contributionPct"] += v["contributionPct"]
        e["viaFunds"] = sorted(
            ({**m, "contributionPct": round(m["contributionPct"], 4)}
             for m in merged.values()),
            key=lambda v: -v["contributionPct"],
        )

    resolved = sum(e["allocationPercent"] for e in positions)
    return {
        "positions": positions[:top_n],
        "position_count": len(positions),
        "resolved_pct": round(resolved, 2),
        "uncovered_pct": round(uncovered_pct, 2),
        "non_equity_pct": round(non_equity_pct, 2),
        "top_concentration_pct": round(
            sum(e["allocationPercent"] for e in positions[:10]), 2),
        "coverage": coverage,
    }


# ── Stock candidate sourcing (Individual Stocks mode) ──────────────────────────

def _to_candidate(item: dict, region: str, sector_map: Optional[dict] = None) -> Optional[dict]:
    symbol = item.get("symbol")
    price = item.get("price")
    sector = item.get("sector")
    if not symbol or not price or not sector:
        return None
    if sector_map is not None:
        sector = sector_map.get(sector)
        if not sector:
            return None  # sector has no canonical equivalent — drop, don't mis-bucket
    beta = item.get("beta")
    if isinstance(beta, (int, float)):
        risk_level = "low" if beta < 0.9 else "high" if beta > 1.3 else "medium"
    else:
        risk_level = "medium"
    market_cap = item.get("marketCap") or 0
    return {
        "ticker": symbol,
        "name": item.get("companyName") or symbol,
        "sector": sector,
        "region": region,
        "price": price,
        "riskLevel": risk_level,
        "isMegaCap": market_cap >= 200_000_000_000,
    }


def _dedupe_by_company(candidates: list[dict]) -> list[dict]:
    """FMP's screener universe includes secondary-exchange listings of the
    same company as separate rows (confirmed live: NVDA + NVDA.NE, GS +
    GS.NE, MU + MU.TO all appeared as distinct candidates in the same
    sector/region) — without this, select_stocks could recommend the same
    company twice under two tickers. Keep one representative per company
    name: prefer a bare ticker (no exchange-suffix dot) over a suffixed one."""
    best: dict[str, dict] = {}
    for c in candidates:
        key = (c.get("name") or c["ticker"]).strip().upper()
        existing = best.get(key)
        if existing is None:
            best[key] = c
        elif "." in existing["ticker"] and "." not in c["ticker"]:
            best[key] = c
    return list(best.values())


def _get_stock_candidates() -> list[dict]:
    """Live Screener universe, tagged for geography scoring: US stocks keep
    region='US'; HK stocks are tagged 'Emerging Markets' (Greater-China
    proxy — see _HK_STOCK_REGION); SG stocks are tagged 'Asia-Pacific'."""
    candidates: list[dict] = []

    us = screener_service.get_screener_stocks(market_cap_more_than=2_000_000_000)
    for item in us.get("items", []):
        c = _to_candidate(item, region="US")
        if c:
            candidates.append(c)

    hk = screener_service.get_hk_screener_stocks()
    for item in hk.get("items", []):
        c = _to_candidate(item, region=_HK_STOCK_REGION, sector_map=_HK_SECTOR_TO_CANONICAL)
        if c:
            candidates.append(c)

    sg = screener_service.get_sg_screener_stocks()
    for item in sg.get("items", []):
        c = _to_candidate(item, region=_SG_STOCK_REGION, sector_map=_SG_SECTOR_TO_CANONICAL)
        if c:
            candidates.append(c)

    return _dedupe_by_company(candidates)


# ── Entry point ─────────────────────────────────────────────────────────────────

def generate_portfolio(answers: dict) -> dict:
    """answers: {risk_tolerance, time_horizon, sector_preferences,
    geography_preferences, investment_amount} — snake_case, already
    translated from the wire-format camelCase by the route layer."""
    risk_tolerance = answers["risk_tolerance"]
    time_horizon = answers["time_horizon"]
    sector_prefs = dict(answers.get("sector_preferences") or {})
    geo_prefs = dict(answers.get("geography_preferences") or {})
    investment_amount = float(answers["investment_amount"])

    base_alloc = get_base_allocation(risk_tolerance, time_horizon)

    etf_universe = etf_metadata_service.get_etf_universe()
    etf_portfolio = select_etfs(base_alloc, sector_prefs, geo_prefs, investment_amount, etf_universe)

    # Stock-mode regions now line up 1:1 with geo_prefs' own keys (US /
    # Europe / Asia-Pacific / Emerging Markets) — no combining needed. Europe
    # simply has no stock-mode candidates (screener only covers US/HK/SG);
    # select_stocks' region_pools filter drops empty regions automatically.
    stock_candidates = _get_stock_candidates()
    stock_portfolio = select_stocks(sector_prefs, geo_prefs, risk_tolerance, investment_amount, stock_candidates)

    return {
        "etf_portfolio": etf_portfolio,
        "stock_portfolio": stock_portfolio,
        "etf_breakdowns": calculate_breakdowns(etf_portfolio),
        "stock_breakdowns": calculate_breakdowns(stock_portfolio),
        # What companies the FUND plan actually owns, resolved through each
        # ETF's constituents — so the user sees ETF choice and equity
        # exposure side by side rather than only the fund tickers.
        "etf_equity_lookthrough": calculate_equity_lookthrough(
            etf_portfolio, investment_amount),
        "total_investment": investment_amount,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
