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
has curated yet, so 71 rows cover 100 of 100 HK large caps.

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


def profile_for_industry(industry: str | None) -> Optional[tuple[str, str]]:
    """(sector, profile) for an FMP industry label, or None if unmapped.

    Returns None rather than guessing: an unmapped industry should fall through
    to the existing classifier visibly, not be assigned a neighbouring row.
    """
    if not industry:
        return None
    return industry_map().get(industry.strip())
