"""FMP industry -> valuation profile routing.

Why the industry and not the ticker
-----------------------------------
`TICKER_SECTOR_LOOKUP` curates one row per company, and at HK large-cap scale
that reached 12 of 100 names carrying a usable profile hint -- the other 88
fell through to `classify_valuation_profile`, a ladder that reads financial
characteristics within a sector and never reads the industry. It does not fail
loudly; it returns something plausible. Measured on this universe it valued BYD
as Apparel / Athletic Wear, CATL as Aerospace & Defense, AIA as FinTech and
Zijin Gold as Specialty Chemicals.

An industry row covers every company in that industry, including ones nobody
has curated yet, so the rows cover 100 of 100 HK large caps. The exact count is
not repeated here: it went stale twice (71, then 72, against 76 today) because
prose cannot be checked. `test_industry_profile_map` counts the live table.

This module only RESOLVES the mapping. Nothing here is wired into the valuation
path yet -- see `profile_for_industry` callers.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

_PATH = Path(__file__).resolve().parent / "industry_profile_map.json"
_CACHE: Optional[dict] = None


def _load() -> dict:
    global _CACHE
    if _CACHE is None:
        try:
            _CACHE = json.loads(_PATH.read_text(encoding="utf-8"))
        except Exception:                              # noqa: BLE001
            _CACHE = {"map": {}, "version": 0}
    return _CACHE


def industry_map() -> dict[str, tuple[str, str]]:
    return {k: (v[0], v[1]) for k, v in (_load().get("map") or {}).items()}


def ticker_overrides() -> dict[str, tuple[str, str]]:
    return {k: (v[0], v[1]) for k, v in (_load().get("ticker_overrides") or {}).items()}


def comps_industry_for(ticker: str | None) -> Optional[str]:
    """The FMP industry whose peer basket this ticker takes, when it is pinned
    against its own FMP label; None for everyone else (the label stands).

    00006.HK and 01816.HK are labelled `Independent Power Producers` and pinned
    to Regulated Utility. Without this they would be priced on a Regulated
    Utility method table at Chinese coal-IPP medians (P/E 7.4x).
    """
    if not ticker:
        return None
    from src.tools.ticker_canonical import canonical_ticker
    return (_load().get("comps_industry_overrides") or {}).get(canonical_ticker(ticker))


def routing_scope() -> frozenset:
    """Industries routed by this map whatever FEATURE_INDUSTRY_ROUTING says.

    The global flag moves ~88/100 HK and ~98/100 SG profiles at once, so it
    stays off; a sector wave that has been measured and approved switches on
    exactly its own industries here.
    """
    return frozenset(_load().get("routing_scope") or [])


def in_routing_scope(ticker: str | None, industry: str | None) -> bool:
    """True when this ticker's industry (or the ticker itself) is in scope."""
    if (industry or "").strip() in routing_scope():
        return True
    if ticker:
        from src.tools.ticker_canonical import canonical_ticker
        return canonical_ticker(ticker) in set(_load().get("routing_scope_tickers") or [])
    return False


def market_of(ticker: str | None) -> str:
    """US / HK / SG from the ticker suffix."""
    t = (ticker or "").strip().upper()
    if t.endswith(".SI"):
        return "SG"
    if t.endswith(".HK"):
        return "HK"
    return "US"


def market_map(market: str) -> dict[str, tuple[str, str]]:
    rows = ((_load().get("markets") or {}).get(market) or {})
    return {k: (v[0], v[1]) for k, v in rows.items()}


def profile_for_ticker(ticker: str | None,
                       industry: str | None) -> Optional[tuple[str, str]]:
    """(sector, profile), preferring a ticker override over the industry row.

    An industry row is right for the MAJORITY of its industry. Where a label
    genuinely lumps different archetypes -- FMP's "Real Estate - Services"
    holds a prime-retail landlord alongside a brokerage platform and a
    property manager -- the override names the exception instead of distorting
    the row for everyone else in it.
    """
    if ticker:
        from src.tools.ticker_canonical import canonical_ticker
        hit = ticker_overrides().get(canonical_ticker(ticker))
        if hit:
            return hit
        # A market with CALIBRATED profiles gets its own table first. Singapore
        # has S-REIT, Money Center Bank (SG), Telco / Infrastructure (SG) and
        # more; routing an S-REIT through the US REIT row prices a Singapore
        # trust off US cap rates, which is the DBS incident in reverse.
        hit = market_map(market_of(ticker)).get((industry or "").strip())
        if hit:
            return hit
    return profile_for_industry(industry)


def profile_for_industry(industry: str | None) -> Optional[tuple[str, str]]:
    """(sector, profile) for an FMP industry label, or None if unmapped.

    Returns None rather than guessing: an unmapped industry should fall through
    to the existing classifier visibly, not be assigned a neighbouring row.
    """
    if not industry:
        return None
    return industry_map().get(industry.strip())
