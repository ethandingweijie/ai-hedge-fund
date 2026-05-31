"""
src/research_ideas/momentum/runner.py
======================================
Momentum screen orchestrator.

  run_momentum(as_of=None, max_workers=4, save=True) -> MomentumCohortResult

Scores the sector-ETF layer first, then every ticker, applies the
sector-alignment overlay, ranks by signal strength (either direction), and
persists. Per-ticker exceptions go into failed_tickers; one bad ticker never
aborts the run.

`as_of` (ISO yyyy-mm-dd) points the whole screen at a historical date — used
to validate against a known move such as the software sell-down.

Persistence: app/backend/services/momentum_storage.py (SQLite).
"""
from __future__ import annotations

import logging
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Optional

from src.research_ideas.momentum.data_fetch import fetch_price_series
from src.research_ideas.momentum.scoring import apply_sector_alignment, score_series
from src.research_ideas.momentum.schemas import (
    MomentumCohortResult,
    MomentumTickerResult,
)
from src.research_ideas.momentum.sector import run_sectors, sector_lookup
from src.research_ideas.momentum.universe import get_ticker_metadata, list_tickers


logger = logging.getLogger(__name__)


def score_one_ticker(
    ticker: str,
    as_of: Optional[str] = None,
    sector_by_etf: Optional[dict] = None,
) -> MomentumTickerResult | dict:
    """Score ONE ticker. Returns a result or {ticker, reason} on failure."""
    ticker = ticker.strip().upper()
    if not ticker:
        return {"ticker": ticker, "reason": "empty_ticker"}
    meta = get_ticker_metadata(ticker)
    try:
        bundle = fetch_price_series(ticker, as_of=as_of)
        if bundle is None or bundle.df is None or len(bundle.df) < 60:
            return {"ticker": ticker, "reason": "insufficient_history"}
        bundle.name = meta.get("name", ticker)
        bundle.sector = meta.get("sector")
        bundle.sector_etf = meta.get("sector_etf")
        s = score_series(bundle)
        result = MomentumTickerResult(
            ticker=ticker,
            name=meta.get("name", ticker),
            sector=meta.get("sector"),
            sector_etf=meta.get("sector_etf"),
            price=float(bundle.df["close"].iloc[-1]),
            bars=bundle.bars,
            **s,
        )
        if sector_by_etf is not None:
            patched = apply_sector_alignment(result.model_dump(), sector_by_etf)
            result = MomentumTickerResult(**patched)
        return result
    except Exception as exc:
        logger.exception("Momentum scoring %s failed: %s", ticker, exc)
        return {"ticker": ticker, "reason": f"{type(exc).__name__}: {exc}"}


def run_momentum(
    as_of: Optional[str] = None,
    max_workers: int = 4,
    save: bool = True,
    run_id: Optional[str] = None,
) -> MomentumCohortResult:
    # 1) Sector layer first — the ticker overlay aligns against it.
    sectors = run_sectors(as_of=as_of, max_workers=max(max_workers, 6))
    sec_by_etf = sector_lookup(sectors)

    # 2) Ticker layer.
    tickers = list_tickers()
    results: list[MomentumTickerResult] = []
    failed: list[dict] = []
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {
            ex.submit(score_one_ticker, t, as_of, sec_by_etf): t for t in tickers
        }
        for fut in as_completed(futures):
            out = fut.result()
            if isinstance(out, MomentumTickerResult):
                results.append(out)
            elif isinstance(out, dict):
                failed.append({k: v for k, v in out.items() if k != "trace"})

    # 3) Rank by signal strength (either direction); aligned names break ties.
    results.sort(
        key=lambda r: (-(r.signal_strength or 0.0), 0 if r.sector_aligned else 1, r.ticker)
    )
    for i, r in enumerate(results, 1):
        r.rank = i

    long_count = sum(1 for r in results if r.direction == "LONG")
    short_count = sum(1 for r in results if r.direction == "SHORT")

    cohort = MomentumCohortResult(
        run_id=run_id or uuid.uuid4().hex[:12],
        created_at=datetime.now(timezone.utc).isoformat(),
        as_of=as_of,
        ticker_count=len(results),
        long_count=long_count,
        short_count=short_count,
        sectors=sectors,
        failed_tickers=failed,
        results=results,
    )

    if save:
        try:
            from app.backend.services.momentum_storage import save_momentum_run
            save_momentum_run(cohort)
        except Exception as exc:
            logger.warning("Momentum: persistence skipped — %s", exc)

    return cohort


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    as_of_arg = sys.argv[1] if len(sys.argv) > 1 else None
    r = run_momentum(as_of=as_of_arg, max_workers=6, save=False)
    print(f"\nMomentum run_id={r.run_id}  as_of={r.as_of or 'live'}")
    print(f"tickers={r.ticker_count}  long={r.long_count}  short={r.short_count}  failed={len(r.failed_tickers)}")

    print("\n-- Sector momentum map --")
    for s in r.sectors:
        print(f"  {s.etf:<5} {s.label or s.sector:<26} {s.verdict:<18} "
              f"comp={s.composite:+.0f}  S/T/A={s.state_score:+.0f}/{s.turn_score:+.0f}/{s.accel_score:+.0f}")

    print("\n-- Top LONG --")
    for t in [x for x in r.results if x.direction == "LONG"][:8]:
        flag = "[sec]" if t.sector_aligned else "     "
        print(f"  #{t.rank:<3} {t.ticker:<6} {t.verdict:<18} comp={t.composite:+.0f} {flag}  {t.name}")

    print("\n-- Top SHORT --")
    for t in [x for x in r.results if x.direction == "SHORT"][:8]:
        flag = "[sec]" if t.sector_aligned else "     "
        print(f"  #{t.rank:<3} {t.ticker:<6} {t.verdict:<18} comp={t.composite:+.0f} {flag}  {t.name}")

    if r.failed_tickers:
        print(f"\nfailed: {r.failed_tickers}")
