"""
src/research_ideas/fundflow/universe.py
========================================
Load the geographic fund-flow universe.

JSON source:
  src/research_ideas/data/fundflow_universe.json
    {regions: [...], benchmarks: [...]}

A "region" is a BASKET of US-listed ETFs, not a single ticker — dollar flows
are summed across the basket so the row measures money into the geography.
Benchmarks (ACWI / EEM / VEA) are scored on the identical engine but excluded
from the ranked region table; they exist to anchor the relative-flow overlay.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional


_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@lru_cache(maxsize=1)
def _load_raw() -> dict[str, Any]:
    path = _DATA_DIR / "fundflow_universe.json"
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def list_regions() -> list[dict[str, Any]]:
    """The nine tracked geographies, in display order."""
    return [dict(r) for r in _load_raw().get("regions", [])]


def list_benchmarks() -> list[dict[str, Any]]:
    """Global / EM / DM-ex-US anchors for the relative-flow overlay."""
    return [dict(r) for r in _load_raw().get("benchmarks", [])]


def list_all() -> list[dict[str, Any]]:
    """Regions + benchmarks — everything the runner scores."""
    return list_regions() + list_benchmarks()


def get_region(region: str) -> Optional[dict[str, Any]]:
    key = region.strip().upper()
    for row in list_all():
        if row["region"] == key:
            return row
    return None


def all_symbols() -> list[str]:
    """Every distinct ETF symbol referenced anywhere in the universe."""
    seen: list[str] = []
    for row in list_all():
        for sym in row.get("basket") or [row["etf"]]:
            if sym not in seen:
                seen.append(sym)
    return seen
