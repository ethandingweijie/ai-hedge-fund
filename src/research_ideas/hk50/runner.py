"""
src/research_ideas/hk50/runner.py
==================================
"Long China / HK" cohort orchestrator.

  run_hk50(max_workers=6, save=True) -> HK50CohortResult

For each of the 50 names:
  route -> resolve (multi-source) -> backfill -> score_growth + score_div -> IV15

The deterministic compute kernel is reused verbatim from scripts/hk50_*.py so
there is ONE source of truth for the resolver / scorers / IV15 math. This module
only assembles results into schema objects, ranks each screen independently, and
persists.

Parallel via ThreadPoolExecutor; a single bad ticker never aborts the cohort —
it lands in failed_tickers instead.
"""
from __future__ import annotations

import logging
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from scripts.hk50_adr_report import backfill_from_hk_listing
from scripts.hk50_gap_analysis import (
    resolve, score_growth, score_div, ALL_METRICS,
)
from scripts.hk50_iv15 import (
    AICT_TIER, TIER_PARAMS, NEUTRAL_PARAMS,
    build_inputs, stage1_growth, iv15 as _iv15_calc, piv_score,
)

from src.research_ideas.hk50.schemas import (
    HK50CohortResult, HK50TickerResult, IV15Detail, MetricResult,
)
from src.research_ideas.hk50.universe import get_ticker_metadata, list_tickers


logger = logging.getLogger(__name__)


def _compute_iv15(hk_ticker: str, rep: str) -> IV15Detail:
    """Full IV15 build-up. AICT-modulated for SW/Internet names (keyed by the HK
    ticker), neutral Gordon for everyone else. Mirrors scripts.hk50_combined."""
    if hk_ticker in AICT_TIER:
        tier = AICT_TIER[hk_ticker]
        haircut, mult, wA = TIER_PARAMS[tier]
        label = tier
    else:
        haircut, mult, wA = NEUTRAL_PARAMS
        label = "—"

    detail = IV15Detail(
        aict=label, haircut=haircut, terminal_multiple=mult, blend_weight_a=wA,
    )
    try:
        inp = build_inputs(rep)
    except Exception:
        return detail

    detail.currency = inp.get("currency", "?")
    detail.price = inp.get("price")
    E0 = inp.get("E0")
    if E0 is None or E0 <= 0:
        return detail

    g1 = stage1_growth(inp)
    ivA, ivB, iv = _iv15_calc(E0, g1, haircut, mult, wA)
    price = inp.get("price")
    piv = (price / iv) if (price and iv and iv > 0) else None

    detail.base_eps = E0
    detail.stage1_growth = g1
    detail.iv_gordon = ivA
    detail.iv_buffett = ivB
    detail.iv15 = iv
    detail.price = price
    detail.p_iv15 = piv
    detail.valuation_points = piv_score(piv) if piv is not None else None
    return detail


def _process_ticker(hk_ticker: str) -> HK50TickerResult | dict:
    """Returns HK50TickerResult on success, or {ticker, reason} on failure."""
    md = get_ticker_metadata(hk_ticker)
    rep = md["report_ticker"]
    try:
        r = resolve(rep)
    except Exception as exc:
        logger.exception("HK50 resolve %s (%s) failed: %s", rep, hk_ticker, exc)
        return {"ticker": rep, "reason": f"{type(exc).__name__}: {exc}"}

    try:
        backfill_from_hk_listing(hk_ticker, rep, r)
        g, _gc = score_growth(r)
        d, _dc = score_div(r)
        detail = _compute_iv15(hk_ticker, rep)

        metrics = {
            m: MetricResult(value=r[m].value, source=r[m].source)
            for m in ALL_METRICS
        }
        lead = "Growth" if g >= d else "Dividend"

        return HK50TickerResult(
            ticker=rep,
            hk_ticker=hk_ticker,
            name=md["name"],
            route_label=md["route_label"],
            currency=detail.currency,
            growth_score=round(float(g), 1),
            dividend_score=round(float(d), 1),
            lead=lead,
            aict_tier=detail.aict,
            price=detail.price,
            iv15=detail.iv15,
            p_iv15=detail.p_iv15,
            metrics=metrics,
            iv15_detail=detail,
        )
    except Exception as exc:
        tb = traceback.format_exc()
        logger.exception("HK50 ticker %s (%s) failed: %s", rep, hk_ticker, exc)
        return {"ticker": rep, "reason": f"{type(exc).__name__}: {exc}", "trace": tb}


def run_hk50(
    max_workers: int = 6,
    save: bool = True,
    run_id: str | None = None,
) -> HK50CohortResult:
    tickers = list_tickers()
    results: list[HK50TickerResult] = []
    failed: list[dict] = []

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_process_ticker, t): t for t in tickers}
        for fut in as_completed(futures):
            out = fut.result()
            if isinstance(out, HK50TickerResult):
                results.append(out)
            elif isinstance(out, dict):
                failed.append({k: v for k, v in out.items() if k != "trace"})

    # Independent per-screen rankings (the UI toggles between these two orders).
    for i, r in enumerate(
        sorted(results, key=lambda x: x.growth_score, reverse=True), 1
    ):
        r.growth_rank = i
    for i, r in enumerate(
        sorted(results, key=lambda x: x.dividend_score, reverse=True), 1
    ):
        r.dividend_rank = i

    # Default stored order: leading (max) score desc, ties by ticker.
    results.sort(
        key=lambda x: (-(max(x.growth_score, x.dividend_score)), x.ticker)
    )

    n = len(results)
    avg_g = sum(x.growth_score for x in results) / n if n else None
    avg_d = sum(x.dividend_score for x in results) / n if n else None
    pivs = sorted(x.p_iv15 for x in results if x.p_iv15)
    med_piv = pivs[len(pivs) // 2] if pivs else None
    lead_growth = sum(1 for x in results if x.lead == "Growth")

    cohort = HK50CohortResult(
        run_id=run_id or uuid.uuid4().hex[:12],
        created_at=datetime.now(timezone.utc).isoformat(),
        ticker_count=n,
        avg_growth=round(avg_g, 1) if avg_g is not None else None,
        avg_dividend=round(avg_d, 1) if avg_d is not None else None,
        median_p_iv15=med_piv,
        lead_growth_count=lead_growth,
        failed_tickers=failed,
        results=results,
    )

    if save:
        try:
            from app.backend.services.hk50_storage import save_hk50_run
            save_hk50_run(cohort)
        except Exception as exc:
            logger.warning("HK50: persistence skipped — %s", exc)

    return cohort


if __name__ == "__main__":
    # Manual smoke test:
    #   .venv\Scripts\python.exe -m src.research_ideas.hk50.runner
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    res = run_hk50(max_workers=6, save=False)
    print(f"\nHK50 cohort run_id={res.run_id}  n={res.ticker_count}  failed={len(res.failed_tickers)}")
    print(f"avg Growth={res.avg_growth}  avg Dividend={res.avg_dividend}  "
          f"median P/IV15={res.median_p_iv15}  lead split {res.lead_growth_count}G")
    top_g = sorted(res.results, key=lambda x: x.growth_score, reverse=True)[:5]
    top_d = sorted(res.results, key=lambda x: x.dividend_score, reverse=True)[:5]
    print("Top 5 Growth :", [(x.name, x.growth_score) for x in top_g])
    print("Top 5 Dividend:", [(x.name, x.dividend_score) for x in top_d])
    if res.failed_tickers:
        print("Failures:", res.failed_tickers)
