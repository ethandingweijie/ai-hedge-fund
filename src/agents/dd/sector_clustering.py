"""
sector_clustering.py — Phase 2C: group breaches into sector-level clusters.

When ≥3 tickers in the same sector all breach ±10% in the same direction on
the same day, the underlying signal is almost always a sector-wide event
(Fed move, geopolitical shock, regulatory action, sector-rotation flow)
rather than N independent ticker stories. Investigating each ticker
individually wastes LLM calls on what is one event.

Public surface:
  cluster_breaches(quotes, min_members=3) → ClusterResult
    .clusters    list[Cluster]   — qualifying (sector, direction) groups
    .singletons  list[BatchQuote] — breaches not in any qualifying cluster
                                    (either alone in their sector OR sector
                                    not classifiable)

Sector lookup is two-tier:
  1. TICKER_SECTOR_LOOKUP — hand-curated, free, covers ~200 names
  2. FMP /stable/profile fallback for unknowns — cached per-process so
     repeat ticks don't re-hit FMP

Direction-aware: a sector can have a DROP cluster + a PUMP cluster on the
same day (separate cluster_ids, separate downstream alerts). E.g. on a
"value rotation" day, mega-cap tech can pump while small-cap tech drops.
"""

from __future__ import annotations

import logging
import os
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable

from src.agents.dd.batch_quote import BatchQuote


logger = logging.getLogger(__name__)


# Process-lifetime cache for FMP fallback (cron tick is short-lived, but
# manual invocations / tests can hit the same ticker repeatedly).
_FMP_SECTOR_CACHE: dict[str, str | None] = {}


@dataclass(frozen=True)
class Cluster:
    """One (sector, direction) group with ≥min_members qualifying breaches."""
    sector:    str                   # e.g. "Tech", "Semiconductor", "Banks"
    direction: str                   # 'DROP' or 'PUMP'
    members:   tuple[BatchQuote, ...]   # ordered by abs(pct) DESC
    cluster_id: str = ""              # populated by build_cluster_id

    @property
    def n(self) -> int:
        return len(self.members)

    @property
    def median_pct(self) -> float:
        """Median pct change across members. Used for the cluster's
        headline magnitude in Slack + dashboard."""
        if not self.members:
            return 0.0
        pcts = sorted(m.changes_percentage for m in self.members)
        return pcts[len(pcts) // 2]

    @property
    def tickers(self) -> list[str]:
        return [m.ticker for m in self.members]


@dataclass(frozen=True)
class ClusterResult:
    """Output of cluster_breaches: clusters + singletons (each list ordered
    largest-magnitude first to match the dispatcher's existing convention)."""
    clusters:   tuple[Cluster, ...]      = field(default_factory=tuple)
    singletons: tuple[BatchQuote, ...]   = field(default_factory=tuple)


# ── Sector lookup ───────────────────────────────────────────────────────────


def lookup_sector(ticker: str) -> str | None:
    """Return the sector for `ticker`, or None if unclassifiable.

    Order:
      1. TICKER_SECTOR_LOOKUP (free, hand-curated)
      2. FMP /stable/profile fallback (cached per process)
    """
    t = (ticker or "").strip().upper()
    if not t:
        return None

    # Layer 1: hand-curated lookup
    try:
        from src.data.sector_profiles import TICKER_SECTOR_LOOKUP
        entry = TICKER_SECTOR_LOOKUP.get(t)
        if entry:
            sector = entry[0] if isinstance(entry, tuple) and entry else None
            if sector:
                return sector
    except Exception as exc:
        logger.debug("sector_clustering: TICKER_SECTOR_LOOKUP unavailable: %s", exc)

    # Layer 2: FMP profile fallback (cached)
    if t in _FMP_SECTOR_CACHE:
        return _FMP_SECTOR_CACHE[t]

    sector = _fetch_sector_via_fmp(t)
    _FMP_SECTOR_CACHE[t] = sector
    return sector


def _fetch_sector_via_fmp(ticker: str) -> str | None:
    """One-shot FMP /stable/profile lookup. Returns the sector string or
    None on any failure. Never raises."""
    try:
        from src.tools.api import _fmp_get, _STABLE
        data = _fmp_get(
            f"{_STABLE}/profile",
            params={"symbol": ticker},
            api_key=None,
            uncap=True,
        )
        if isinstance(data, list) and data:
            row = data[0]
            sector = (row.get("sector") or "").strip()
            return sector if sector else None
        if isinstance(data, dict):
            sector = (data.get("sector") or "").strip()
            return sector if sector else None
    except Exception as exc:
        logger.warning("sector_clustering: FMP profile failed for %s: %s", ticker, exc)
    return None


# ── Cluster builder ─────────────────────────────────────────────────────────


_DEFAULT_MIN_MEMBERS = 3


def cluster_breaches(
    quotes: Iterable[BatchQuote],
    min_members: int | None = None,
) -> ClusterResult:
    """Partition breaches into qualifying (sector, direction) clusters +
    singletons.

    Args:
      quotes:      iterable of BatchQuote (typically the output of
                   batch_quote.detect_breaches)
      min_members: minimum number of same-direction same-sector breaches
                   required to form a cluster. Defaults to env
                   DD_CLUSTER_MIN_MEMBERS or 3 if unset.

    Returns:
      ClusterResult. The dispatcher fires:
        - One sector-level alert per Cluster (member tickers marked
          sent_status='cluster_member' in dd_alerts)
        - One individual alert per singleton (existing path)

    Empty input → empty result. Never raises.
    """
    if min_members is None:
        try:
            min_members = int(os.environ.get("DD_CLUSTER_MIN_MEMBERS", _DEFAULT_MIN_MEMBERS))
        except (TypeError, ValueError):
            min_members = _DEFAULT_MIN_MEMBERS
    min_members = max(2, min_members)   # cluster of 1 doesn't make sense

    breaches = list(quotes)
    if not breaches:
        return ClusterResult()

    # Group by (sector, direction); track unclassifiable separately
    groups: dict[tuple[str, str], list[BatchQuote]] = defaultdict(list)
    unclassifiable: list[BatchQuote] = []

    for b in breaches:
        sector = lookup_sector(b.ticker)
        if not sector:
            unclassifiable.append(b)
            continue
        direction = "DROP" if b.changes_percentage < 0 else "PUMP"
        groups[(sector, direction)].append(b)

    clusters: list[Cluster] = []
    singletons: list[BatchQuote] = list(unclassifiable)

    for (sector, direction), members in groups.items():
        if len(members) >= min_members:
            # Sort members by abs(pct) DESC so the worst/best is first
            members_sorted = sorted(members, key=lambda m: abs(m.changes_percentage), reverse=True)
            clusters.append(Cluster(
                sector=sector,
                direction=direction,
                members=tuple(members_sorted),
                cluster_id=build_cluster_id(sector, direction),
            ))
        else:
            singletons.extend(members)

    # Order clusters by aggregate magnitude (most extreme first for Slack thread order)
    clusters.sort(key=lambda c: abs(c.median_pct), reverse=True)
    # Singletons preserve magnitude order (matches existing dispatcher convention)
    singletons.sort(key=lambda b: abs(b.changes_percentage), reverse=True)

    logger.info(
        "sector_clustering: %d breaches → %d clusters + %d singletons",
        len(breaches), len(clusters), len(singletons),
    )
    return ClusterResult(clusters=tuple(clusters), singletons=tuple(singletons))


def build_cluster_id(sector: str, direction: str) -> str:
    """Stable, human-readable cluster id used as dd_alerts.cluster_id +
    Slack thread context.

    Format: `<sector_slug>_<direction>_<utc_date>` — same sector firing on
    different days produces different cluster_ids; same sector firing in
    both directions on the same day produces TWO ids (DROP + PUMP).
    """
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).date().isoformat()
    slug = sector.lower().replace(" ", "_").replace("/", "_")
    return f"{slug}_{direction.lower()}_{today}"


def clear_sector_cache() -> None:
    """Test helper — drop the FMP fallback cache so each test starts clean."""
    _FMP_SECTOR_CACHE.clear()
