"""
src/research_ideas/momentum/sector.py
======================================
Score the sector-ETF layer. Runs the identical momentum scorer on each SPDR
sector ETF (+ thematic IGV/WCLD/SMH) to build the sector momentum map that the
ticker layer aligns against.

  run_sectors(as_of=None, max_workers=8) -> list[MomentumSectorResult]
  sector_lookup(sectors) -> {etf: {composite, direction, verdict}}
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from src.research_ideas.momentum.data_fetch import MomentumBundle, fetch_price_series
from src.research_ideas.momentum.scoring import score_series
from src.research_ideas.momentum.schemas import MomentumSectorResult
from src.research_ideas.momentum.universe import list_sectors


logger = logging.getLogger(__name__)

_MONTH_BARS = 21        # trading sessions in ~1 month (for the prior-rank snapshot)
_QUARTER_BARS = 63      # trading sessions in ~3 months (for the prior-rank snapshot)


def _composite_as_of_prior(bundle: MomentumBundle, back_bars: int = _MONTH_BARS) -> Optional[float]:
    """Re-score the same series with the last `back_bars` sessions chopped off,
    so the bar `back_bars` ago is treated as 'now' — i.e. the composite a month
    ago. Returns None if there isn't enough history to score the earlier point."""
    df = bundle.df
    if df is None or len(df) - back_bars < 60:
        return None
    prior = MomentumBundle(
        symbol=bundle.symbol,
        df=df.iloc[:-back_bars],
        has_true_ohlc=bundle.has_true_ohlc,
        as_of=bundle.as_of,
        bars=len(df) - back_bars,
    )
    try:
        return score_series(prior).get("composite")
    except Exception:
        return None


def _score_one_sector(meta: dict, as_of: Optional[str]) -> Optional[MomentumSectorResult]:
    etf = meta["etf"]
    try:
        bundle = fetch_price_series(etf, as_of=as_of)
        if bundle is None or bundle.df is None or len(bundle.df) < 60:
            logger.warning("Sector %s: insufficient price history", etf)
            return None
        s = score_series(bundle)
        return MomentumSectorResult(
            etf=etf,
            sector=meta["sector"],
            label=meta.get("label"),
            group=meta.get("group"),
            bars=bundle.bars,
            composite_1m=_composite_as_of_prior(bundle, _MONTH_BARS),
            composite_3m=_composite_as_of_prior(bundle, _QUARTER_BARS),
            **s,
        )
    except Exception as exc:
        logger.warning("Sector %s scoring failed: %s", etf, exc)
        return None


def run_sectors(as_of: Optional[str] = None, max_workers: int = 8) -> list[MomentumSectorResult]:
    sectors_meta = list_sectors()
    out: list[MomentumSectorResult] = []
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_score_one_sector, m, as_of): m for m in sectors_meta}
        for fut in as_completed(futures):
            res = fut.result()
            if res is not None:
                out.append(res)
    # Strongest momentum first (either direction).
    out.sort(key=lambda r: (-abs(r.composite), -r.composite, r.etf))
    return out


def sector_lookup(sectors: list[MomentumSectorResult]) -> dict[str, dict]:
    """Map ETF symbol -> minimal scored dict for the alignment overlay."""
    return {
        s.etf: {
            "composite": s.composite,
            "direction": s.direction,
            "verdict": s.verdict,
        }
        for s in sectors
    }
