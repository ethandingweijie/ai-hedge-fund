"""
universe.py — Build the universe of tickers the cron dispatcher monitors.

Phase 2B starts intentionally narrow:

  Tier 1 (default, always on):
    Tickers on the user's WATCHLIST tab (DB-driven). The watchlist is the
    user's curated set of "stuff I want to keep an eye on" — same source
    that powers the Watchlist page in the UI. No env var needed; users
    add/remove via the UI and the dispatcher picks it up automatically.

  Tier 2 (opt-in via DD_INCLUDE_ANALYZED=true):
    Tier 1 + tickers analyzed in last 90 days (from web_runs table).
    Captures names you've researched but haven't added to the watchlist.

  Tier 3 (opt-in via DD_INCLUDE_SP500=true):
    Tier 2 + full S&P 500 (from FMP /stable/sp500-constituent).
    Production-realistic surface. Significant cost increase — only
    enable once Tier 1/2 has been observed firing safely.

Design notes:
- Pure functions. No state, no mutation. Easy to test by overriding env
  and mocking watchlist_service.
- Safe failure: any source that errors returns an empty contribution to
  the union. Never raises to the caller. The dispatcher uses the union;
  if Tier 3 is requested but FMP is down, you still get Tier 1+2.
- Symbols normalized to uppercase + stripped. Duplicates collapsed via set.
- Phase 2C will add news_trigger tier (event-driven). Not in 2B.

Backward compat note:
- PORTFOLIO_TICKERS env var (used in initial Phase 2B) is now a fallback /
  override only. If set, its contents UNION with the watchlist DB result
  so existing deployments don't break, but the watchlist is the canonical
  source going forward.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Final


logger = logging.getLogger(__name__)


# Env-var names — kept here as the single source of truth so tests +
# documentation reference the same string constants.
ENV_PORTFOLIO_TICKERS: Final[str] = "PORTFOLIO_TICKERS"
ENV_INCLUDE_ANALYZED:  Final[str] = "DD_INCLUDE_ANALYZED"
ENV_INCLUDE_SP500:     Final[str] = "DD_INCLUDE_SP500"
ENV_ANALYZED_LOOKBACK: Final[str] = "DD_ANALYZED_LOOKBACK_DAYS"

# Defaults
_DEFAULT_ANALYZED_LOOKBACK_DAYS: Final[int] = 90


# ── Tier 1: Watchlist tickers (DB-driven, with env override for tests/migration) ──


def get_watchlist_tickers() -> set[str]:
    """Return the union of all watchlist tickers, plus any env override.

    Primary source: the `watchlist` table in the SQLite DB (same DB used
    by the rest of the app). The watchlist is the canonical curated set
    the user maintains in the Watchlist UI tab.

    Secondary source (UNION): PORTFOLIO_TICKERS env var, if set. Lets
    operators force-add tickers without a UI session — useful during
    deployment / testing / on-call escalations.

    Returns set() if both sources are empty AND the watchlist table is
    missing/unavailable. Dispatcher logs + skips the tick in that case.

    Symbols normalized to uppercase + stripped. Duplicates collapsed.

    Multi-user note: the cron dispatcher has no user context, so this
    function returns the GLOBAL union across all users. For the current
    single-user deployment this is identical to the logged-in user's
    watchlist. Multi-tenant isolation is a separate Phase 3 concern.
    """
    out: set[str] = set()

    # Source 1 — env var override (kept for backward compat + manual escalation)
    raw = os.environ.get(ENV_PORTFOLIO_TICKERS, "")
    if raw.strip():
        parts = raw.replace(",", " ").split()
        out |= {p.strip().upper() for p in parts if p.strip()}

    # Source 2 — watchlist DB (canonical Tier 1 source)
    try:
        # Local import keeps the module light + avoids pulling watchlist_service's
        # FastAPI/SQLAlchemy stack into pure-Python contexts (e.g. unit tests of
        # other dd modules).
        from app.backend.services.analysis_service import _connect

        with _connect() as conn:
            # Cheap: just SELECT DISTINCT ticker. No JSON parse, no FMP call.
            try:
                rows = conn.execute(
                    "SELECT DISTINCT UPPER(ticker) FROM watchlist "
                    "WHERE ticker IS NOT NULL AND ticker != ''"
                ).fetchall()
            except Exception:
                # Table may not exist on a fresh DB — graceful return
                rows = []
        out |= {r[0].strip() for r in rows if r and r[0]}
    except Exception as exc:
        logger.warning("universe: watchlist query failed: %s", exc)

    return out


# Phase 2B initial implementation called this `get_held_positions`. Renamed
# to reflect the actual data source (watchlist, not held positions). The old
# name is kept as an alias so any downstream callers don't break.
get_held_positions = get_watchlist_tickers


# ── Tier 2 contributor: Recently-analyzed tickers (from web_runs) ──────────


def get_analyzed_universe(lookback_days: int | None = None) -> set[str]:
    """Tickers that appeared in web_runs within the last `lookback_days`.

    Uses the same SQLite DB the rest of the app uses (run_archive.db).
    Returns set() on any DB error so the dispatcher degrades gracefully.

    Args:
      lookback_days: defaults to DD_ANALYZED_LOOKBACK_DAYS env or 90.
    """
    if lookback_days is None:
        try:
            lookback_days = int(os.environ.get(ENV_ANALYZED_LOOKBACK, _DEFAULT_ANALYZED_LOOKBACK_DAYS))
        except ValueError:
            lookback_days = _DEFAULT_ANALYZED_LOOKBACK_DAYS

    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()

    try:
        from src.data import db as _db
        if not _db.table_exists("web_runs"):
            return set()
        rows = _db.query(
            "SELECT DISTINCT UPPER(ticker) AS ticker FROM web_runs "
            "WHERE run_at >= ? AND ticker IS NOT NULL AND ticker != ''",
            [cutoff],
        )
        return {r["ticker"].strip() for r in rows if r["ticker"]}
    except Exception as exc:
        logger.warning("universe: analyzed-universe query failed: %s", exc)
        return set()


# ── Tier 3 contributor: S&P 500 (from FMP) ──────────────────────────────────


def get_sp500_universe() -> set[str]:
    """Fetch the S&P 500 constituent list.

    Two-tier fetch:
      1. PRIMARY: FMP `/stable/sp500-constituent` — fresh, authoritative
         per the user's FMP subscription. Returns set() if FMP returns
         402 (plan restriction) / 401 / 404 / network error.
      2. FALLBACK: datahub.io's S&P 500 constituents CSV mirror — public,
         no auth, refreshed periodically by the datahub team. Returns set()
         on any network failure.

    The fallback only fires when FMP returns None (auth/plan/quota error)
    OR an empty list. It produces ~503 tickers — same scale as FMP.
    Both endpoints are normalised to upper-case ticker symbols with dots
    converted to dashes (Yahoo/FMP convention, e.g. "BRK.B" → "BRK-B").

    History (2026-05-20):
      User's FMP plan was returning 402 on /stable/sp500-constituent
      ("Payment Required" — premium endpoint not on the plan). After
      shipping the fallback the dispatcher's Tier 3 universe populates
      reliably; after the user upgraded their FMP plan the primary path
      succeeds and the fallback never fires (defense in depth).

    Returns set() only when BOTH paths fail. Never raises.
    """
    # ── Primary: FMP ─────────────────────────────────────────────────────
    try:
        from src.tools.api import _fmp_get, _STABLE    # type: ignore
        data = _fmp_get(f"{_STABLE}/sp500-constituent", params={}, api_key=None, uncap=True)
        if isinstance(data, list) and data:
            out: set[str] = set()
            for entry in data:
                sym = (entry.get("symbol") or "").strip().upper()
                if sym:
                    out.add(sym)
            if out:
                return out
        logger.info(
            "universe: FMP /stable/sp500-constituent returned no data — "
            "falling back to datahub.io CSV mirror"
        )
    except Exception as exc:
        logger.warning(
            "universe: FMP /stable/sp500-constituent raised %s — falling back", exc
        )

    # ── Fallback: datahub.io public CSV mirror ──────────────────────────
    try:
        return _fetch_sp500_from_datahub()
    except Exception as exc:
        logger.warning("universe: datahub fallback also failed: %s", exc)
        return set()


def _fetch_sp500_from_datahub() -> set[str]:
    """Pull S&P 500 constituents from datahub.io's public CSV mirror.

    URL: https://raw.githubusercontent.com/datasets/s-and-p-500-companies/master/data/constituents.csv

    The repo is maintained by the datahub team and updated periodically
    (typically once per quarter to reflect index reconstitution). Returns
    ~503 tickers as a set of upper-case strings.

    Symbols use Yahoo/FMP convention (dots → dashes): "BRK.B" → "BRK-B"
    so the result is directly consumable by FMP batch-quote calls without
    extra normalisation.

    Raises on network failure — the caller catches and degrades gracefully.
    """
    import csv
    import io
    import urllib.request

    url = (
        "https://raw.githubusercontent.com/datasets/"
        "s-and-p-500-companies/master/data/constituents.csv"
    )
    # Use a browser-like UA — Wikipedia + many CDNs reject default Python
    # urllib UA. GitHub raw is more permissive but we set this defensively.
    req = urllib.request.Request(url, headers={"User-Agent": "ai-hedge-fund-dd/1.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = resp.read().decode("utf-8")

    out: set[str] = set()
    for row in csv.DictReader(io.StringIO(body)):
        sym = (row.get("Symbol") or "").strip().upper()
        if sym:
            # FMP convention: dots become dashes (BRK.B → BRK-B). datahub
            # uses dots, so normalise here for direct downstream use.
            out.add(sym.replace(".", "-"))
    logger.info("universe: datahub fallback returned %d S&P 500 tickers", len(out))
    return out


# ── Composite tier builder ──────────────────────────────────────────────────


def build_dispatcher_universe(
    *,
    include_analyzed: bool | None = None,
    include_sp500:    bool | None = None,
) -> set[str]:
    """Compose the dispatcher's monitoring universe per env-var settings.

    Args (all default to env var lookups):
      include_analyzed: if True, union in tickers analyzed in last N days.
      include_sp500:    if True, union in the S&P 500 constituent list.

    Returns the union, deduplicated, all uppercased. Empty set is a valid
    return — the dispatcher logs and skips the tick rather than crashing.

    Env-var precedence: explicit kwarg > env var > default (False).
    """
    if include_analyzed is None:
        include_analyzed = _truthy(os.environ.get(ENV_INCLUDE_ANALYZED, ""))
    if include_sp500 is None:
        include_sp500 = _truthy(os.environ.get(ENV_INCLUDE_SP500, ""))

    universe = get_watchlist_tickers()
    watchlist_count = len(universe)

    if include_analyzed:
        analyzed = get_analyzed_universe()
        logger.info("universe: +analyzed contributed %d tickers", len(analyzed))
        universe |= analyzed

    if include_sp500:
        sp500 = get_sp500_universe()
        logger.info("universe: +S&P 500 contributed %d tickers", len(sp500))
        universe |= sp500

    logger.info(
        "universe: built (watchlist=%d, +analyzed=%s, +sp500=%s, total=%d)",
        watchlist_count, include_analyzed, include_sp500, len(universe),
    )
    return universe


def _truthy(s: str) -> bool:
    """Permissive truthy parser for env vars: 'true','1','yes','on' all True."""
    return s.strip().lower() in {"true", "1", "yes", "on", "y", "t"}
