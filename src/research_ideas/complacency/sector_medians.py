"""
src/research_ideas/complacency/sector_medians.py
==================================================
Live sector-median fetcher for the Complacency scorer.

Pulls EV/Sales TTM + FCF Yield TTM for every S&P 500 constituent, groups
by GICS sector, computes median / p25 / p75 per sector, and writes the
result to the `sector_medians` SQLite cache.

  refresh_sector_medians(force=False) → dict[sector, dict[metric, ...]]

Refresh cadence: weekly (the scorer auto-triggers if cache is >7 days old).
Cost: 1 S&P-500 list call + ~503 /key-metrics-ttm calls @ 8-worker parallel
≈ 5 min per refresh.

The cache lives in run_archive.db alongside the cohort runs.
"""
from __future__ import annotations

import logging
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Optional

from src.tools.api import _fmp_get, _safe_float, _STABLE


logger = logging.getLogger(__name__)


# ─── S&P 500 constituent fetch ─────────────────────────────────────────────


def fetch_sp500_constituents() -> list[dict]:
    """
    Returns [{symbol, sector, ...}, ...] for all S&P 500 names from
    /stable/sp500-constituent. The 'sector' field is GICS-standard.
    """
    data = _fmp_get(
        f"{_STABLE}/sp500-constituent",
        {},
        api_key=None,
        uncap=True,
    )
    if not isinstance(data, list):
        return []
    return [
        {"symbol": r.get("symbol"), "sector": r.get("sector") or "Unknown"}
        for r in data
        if r.get("symbol")
    ]


# ─── Per-ticker key-metrics fetch ──────────────────────────────────────────


def _fetch_ticker_metrics(ticker: str) -> Optional[dict]:
    """
    Pull EV/Sales TTM + FCF Yield TTM for one ticker. Uses both TTM-
    suffixed and unsuffixed field names so it survives FMP schema drift.
    Returns None on hard fetch failure.
    """
    data = _fmp_get(
        f"{_STABLE}/key-metrics-ttm",
        {"symbol": ticker, "limit": 1},
        api_key=None,
        uncap=True,
    )
    if not isinstance(data, list) or not data:
        return None
    row = data[0]
    return {
        "symbol": ticker,
        "ev_sales": _safe_float(
            row.get("evToSalesTTM")
            or row.get("evToSales")
            or row.get("enterpriseValueOverSalesTTM")
            or row.get("enterpriseValueOverSales")
        ),
        "fcf_yield": _safe_float(
            row.get("freeCashFlowYieldTTM")
            or row.get("freeCashFlowYield")
        ),
    }


def _fetch_all_ticker_metrics(
    constituents: list[dict],
    max_workers: int = 8,
) -> list[dict]:
    """
    Parallel-fetch key metrics for every constituent. Joins back the GICS
    sector from the input list. Returns rows with non-None ev_sales OR
    fcf_yield only (drops total failures).
    """
    sector_map = {c["symbol"]: c["sector"] for c in constituents}
    out: list[dict] = []
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_fetch_ticker_metrics, c["symbol"]): c["symbol"] for c in constituents}
        for fut in as_completed(futures):
            try:
                r = fut.result()
            except Exception as e:
                logger.warning("sector-medians fetch failed: %s", e)
                continue
            if not r:
                continue
            sym = r["symbol"]
            sec = sector_map.get(sym, "Unknown")
            if r["ev_sales"] is not None or r["fcf_yield"] is not None:
                out.append({
                    "symbol": sym,
                    "sector": sec,
                    "ev_sales": r["ev_sales"],
                    "fcf_yield": r["fcf_yield"],
                })
    return out


# ─── Median computation ────────────────────────────────────────────────────


def _compute_sector_medians(
    rows: list[dict],
    min_sample: int = 5,
) -> list[dict]:
    """
    Group rows by sector, compute median / p25 / p75 for each metric.
    Drops sectors with <min_sample observations for that metric.

    Returns batch-insert-ready rows for sector_medians_storage.
    """
    by_sector_ev: dict[str, list[float]] = {}
    by_sector_fcf: dict[str, list[float]] = {}
    for r in rows:
        sec = r["sector"]
        if r["ev_sales"] is not None and r["ev_sales"] > 0:
            by_sector_ev.setdefault(sec, []).append(r["ev_sales"])
        if r["fcf_yield"] is not None:
            by_sector_fcf.setdefault(sec, []).append(r["fcf_yield"])

    out: list[dict] = []

    for sec, vals in by_sector_ev.items():
        if len(vals) < min_sample:
            continue
        s = sorted(vals)
        n = len(s)
        out.append({
            "sector": sec,
            "metric": "ev_sales",
            "median": statistics.median(s),
            "p25": s[max(0, int(n * 0.25) - 1)] if n >= 4 else None,
            "p75": s[min(n - 1, int(n * 0.75))] if n >= 4 else None,
            "sample_size": n,
        })

    for sec, vals in by_sector_fcf.items():
        if len(vals) < min_sample:
            continue
        s = sorted(vals)
        n = len(s)
        out.append({
            "sector": sec,
            "metric": "fcf_yield",
            "median": statistics.median(s),
            "p25": s[max(0, int(n * 0.25) - 1)] if n >= 4 else None,
            "p75": s[min(n - 1, int(n * 0.75))] if n >= 4 else None,
            "sample_size": n,
        })

    return out


# ─── Public refresh ────────────────────────────────────────────────────────


def refresh_sector_medians(
    max_workers: int = 8,
    persist: bool = True,
) -> dict:
    """
    Full refresh:
      1. Pull S&P 500 constituent list (1 API call)
      2. Pull /key-metrics-ttm for each (≈ 503 API calls, parallel)
      3. Group by sector, compute median / p25 / p75
      4. Persist to sector_medians table

    Returns a summary dict.
    """
    t_start = time.time()
    constituents = fetch_sp500_constituents()
    if not constituents:
        return {"error": "sp500_constituent_fetch_failed", "sectors": 0}

    rows = _fetch_all_ticker_metrics(constituents, max_workers=max_workers)
    if not rows:
        return {
            "error": "no_ticker_metrics_returned",
            "constituents_count": len(constituents),
            "elapsed_sec": round(time.time() - t_start, 1),
        }

    medians = _compute_sector_medians(rows)

    refreshed_at = datetime.now(timezone.utc).isoformat()
    written = 0
    if persist:
        try:
            from app.backend.services.sector_medians_storage import save_sector_medians_batch
            written = save_sector_medians_batch(refreshed_at, medians, universe="sp500")
        except Exception as e:
            logger.exception("sector-medians persist failed: %s", e)

    return {
        "refreshed_at": refreshed_at,
        "constituents_fetched": len(constituents),
        "ticker_metrics_returned": len(rows),
        "sector_count": len({m["sector"] for m in medians}),
        "row_count": len(medians),
        "persisted": written,
        "elapsed_sec": round(time.time() - t_start, 1),
        "sample": [{"sector": m["sector"], "metric": m["metric"], "median": m["median"],
                    "sample_size": m["sample_size"]} for m in medians[:6]],
    }


if __name__ == "__main__":
    # Manual seed:
    #   $env:FMP_API_KEY = "..."
    #   .\.venv\Scripts\python.exe -m src.research_ideas.complacency.sector_medians
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    res = refresh_sector_medians(max_workers=8, persist=True)
    print()
    for k, v in res.items():
        if k != "sample":
            print(f"  {k:25s} {v}")
    print()
    print("Sample medians:")
    for m in res.get("sample", []):
        print(f"  {m['sector']:25s} {m['metric']:12s} median={m['median']:.2f}  n={m['sample_size']}")
