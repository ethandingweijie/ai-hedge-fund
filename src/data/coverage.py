"""Tuned vs limited coverage (owner, 2026-11-28, D8).

A company is TUNED when the owner has pinned its sector profile: TICKER_SECTOR_LOOKUP (US + HK), SGX_TICKER_SECTOR_LOOKUP
(SG) or industry_profile_map.json ticker_overrides. Every other name is LIMITED: it is valued on the general defaults its
data-provider industry label routes to -- which, for unpinned growth names (APP, SHOP, RDDT, TTD, SMCI, SOFI...), are
mature-company archetypes (review 2026-11-28, accuracy F3). Limited names are still analysed, labelled as such on the
report, and a first analysis costs 1 credit instead of 2 (billing.run_type).
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Optional

_MAP = Path(__file__).resolve().parent / "industry_profile_map.json"
_LOCK = threading.Lock()
_OVERRIDES: dict = {"v": None}

LIMITED_NOTE = ("Limited coverage: we have not yet tuned a sector model for this company, so it is valued on general "
                "defaults for its industry. Treat the figures as indicative.")


def _overrides() -> set:
    with _LOCK:
        if _OVERRIDES["v"] is None:
            try:
                _OVERRIDES["v"] = set((json.load(open(_MAP, encoding="utf-8")).get("ticker_overrides") or {}).keys())
            except (OSError, ValueError):
                _OVERRIDES["v"] = set()
        return _OVERRIDES["v"]


def is_tuned(ticker: Optional[str]) -> bool:
    t = str(ticker or "").strip().upper()
    if not t:
        return False
    try:
        from src.tools.ticker_canonical import canonical_ticker
        t = canonical_ticker(t) or t
    except Exception:                                       # noqa: BLE001
        pass
    from src.data.sector_profiles import SGX_TICKER_SECTOR_LOOKUP, TICKER_SECTOR_LOOKUP
    return t in TICKER_SECTOR_LOOKUP or t in SGX_TICKER_SECTOR_LOOKUP or t in _overrides()


def coverage(ticker: Optional[str]) -> dict:
    tuned = is_tuned(ticker)
    return {"level": "tuned" if tuned else "limited", "note": None if tuned else LIMITED_NOTE}
