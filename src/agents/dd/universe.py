"""
universe.py — Build the universe of tickers the cron dispatcher monitors.

Phase 2B starts intentionally narrow:

  Tier 1 (default, always on):
    Held positions from PORTFOLIO_TICKERS env var. This is the user's
    actual portfolio — the highest-priority surface for monitoring.
    Per Phase 1 Q5: "Start with Env, transition to DB."

  Tier 2 (opt-in via DD_INCLUDE_ANALYZED=true):
    Tier 1 + tickers analyzed in last 90 days (from web_runs table).
    Useful for "watch list" — names you've researched but don't hold.

  Tier 3 (opt-in via DD_INCLUDE_SP500=true):
    Tier 2 + full S&P 500 (from FMP /stable/sp500-constituent).
    Production-realistic surface. Significant cost increase — only
    enable once Tier 1/2 has been observed firing safely.

Design notes:
- Pure functions. No state, no mutation. Easy to test by overriding env.
- Safe failure: any source that errors returns an empty contribution to
  the union. Never raises to the caller. The dispatcher uses the union;
  if Tier 3 is requested but FMP is down, you still get Tier 1+2.
- Symbols normalized to uppercase + stripped. Duplicates collapsed via set.
- Phase 2C will add news_trigger tier (event-driven). Not in 2B.
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


# ── Tier 1: Held positions (env-var portfolio) ──────────────────────────────


def get_held_positions() -> set[str]:
    """Read PORTFOLIO_TICKERS env var → set of normalized tickers.

    Format: comma- or whitespace-separated, e.g. "AAPL, MSFT NVDA, GOOGL".
    Empty / unset → empty set (dispatcher will log + skip the tick).

    Symbols are uppercased and stripped. Empty strings filtered out.
    """
    raw = os.environ.get(ENV_PORTFOLIO_TICKERS, "")
    if not raw.strip():
        return set()
    # Split on both commas and whitespace; tolerates "AAPL,MSFT NVDA,,GOOGL"
    parts = raw.replace(",", " ").split()
    return {p.strip().upper() for p in parts if p.strip()}


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
        from app.backend.services.analysis_service import _connect, _ensure_web_runs_table
        _ensure_web_runs_table()
        with _connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT UPPER(ticker) FROM web_runs "
                "WHERE run_at >= ? AND ticker IS NOT NULL AND ticker != ''",
                (cutoff,),
            ).fetchall()
        return {r[0].strip() for r in rows if r[0]}
    except Exception as exc:
        logger.warning("universe: analyzed-universe query failed: %s", exc)
        return set()


# ── Tier 3 contributor: S&P 500 (from FMP) ──────────────────────────────────


def get_sp500_universe() -> set[str]:
    """Fetch the S&P 500 constituent list from FMP.

    Endpoint: /stable/sp500-constituent — returns ~503 entries (constituents
    can fluctuate slightly during reconstitution).

    Returns set() if FMP isn't configured or the call fails. Never raises.
    """
    try:
        # Local import — keeps module import light + isolates FMP dependency
        # to the path that actually needs it.
        from src.tools.api import _fmp_get, _STABLE    # type: ignore

        # /stable/sp500-constituent has shape: [{"symbol":"AAPL","name":"Apple",...}, ...]
        # Pass uncap=True since the response naturally returns ~503 entries.
        data = _fmp_get(f"{_STABLE}/sp500-constituent", params={}, api_key=None, uncap=True)
        if not isinstance(data, list):
            return set()
        out: set[str] = set()
        for entry in data:
            sym = (entry.get("symbol") or "").strip().upper()
            if sym:
                out.add(sym)
        return out
    except Exception as exc:
        logger.warning("universe: S&P 500 fetch failed: %s", exc)
        return set()


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

    universe = get_held_positions()

    if include_analyzed:
        analyzed = get_analyzed_universe()
        logger.info("universe: +analyzed contributed %d tickers", len(analyzed))
        universe |= analyzed

    if include_sp500:
        sp500 = get_sp500_universe()
        logger.info("universe: +S&P 500 contributed %d tickers", len(sp500))
        universe |= sp500

    logger.info(
        "universe: built (held=%d, +analyzed=%s, +sp500=%s, total=%d)",
        len(get_held_positions()) if universe else 0,
        include_analyzed, include_sp500, len(universe),
    )
    return universe


def _truthy(s: str) -> bool:
    """Permissive truthy parser for env vars: 'true','1','yes','on' all True."""
    return s.strip().lower() in {"true", "1", "yes", "on", "y", "t"}
