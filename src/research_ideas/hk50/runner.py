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
from src.research_ideas.hk50.selection import (
    ENTER_THRESHOLD, STAY_THRESHOLD, select_cohort,
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
    # Method-E owner-earnings adjustment transparency (ADR/FMP names only; None otherwise)
    detail.e0_raw = inp.get("e0_raw")
    detail.oe_retention = inp.get("oe_retention")
    detail.unfunded_comp = inp.get("unfunded_comp")
    detail.sbc_pct_ni = inp.get("sbc_pct_ni")
    detail.is_net_diluter = inp.get("is_net_diluter")
    detail.oe_cash_tax_estimated = inp.get("oe_cash_tax_estimated")
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

        # Qualitative overlay (Policy + Moat) — curated seed path: free,
        # deterministic, no LLM. Surfaced as a PARALLEL column + conviction
        # gate; never summed into the two pure quant screens. The LLM deep-
        # research run (assess_hk50_qualitative(use_llm=True)) can override
        # the seed tiers on demand.
        qualitative = None
        try:
            from src.research_ideas.hk50.hk50_qualitative import (
                assess_hk50_qualitative,
            )
            qualitative = assess_hk50_qualitative(
                hk_ticker, report_ticker=rep, name=md["name"],
                quant_lead_score=float(max(g, d)), use_llm=False,
            )
        except Exception as exc:
            logger.warning("HK50 qual overlay %s skipped: %s", hk_ticker, exc)

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
            qualitative=qualitative,
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

    # Per-name leading score = max of the two ABSOLUTE screens. This is the
    # single ranking metric for the dynamic universe (quant-only, by design).
    for r in results:
        r.lead_score = round(max(r.growth_score, r.dividend_score), 1)

    # Independent per-screen rankings (the UI toggles between these two orders).
    for i, r in enumerate(
        sorted(results, key=lambda x: x.growth_score, reverse=True), 1
    ):
        r.growth_rank = i
    for i, r in enumerate(
        sorted(results, key=lambda x: x.dividend_score, reverse=True), 1
    ):
        r.dividend_rank = i

    # Default stored order: leading score desc, ties by ticker — so the UI can
    # render the full ranked POOL with the displayed cohort highlighted.
    results.sort(key=lambda x: (-(x.lead_score or 0.0), x.ticker))

    # ── Dynamic-universe selection ────────────────────────────────────────────
    # Read the prior run's membership for the hysteresis bands, then tag each
    # result's in_cohort / cohort_rank / membership and compute promotion deltas.
    prev_in: set[str] = set()
    try:
        from app.backend.services.hk50_storage import get_latest_membership
        prev_in = get_latest_membership()
    except Exception as exc:
        logger.warning("HK50: prior membership unavailable (cold start?) — %s", exc)

    displayed_count, promoted, relegated = select_cohort(results, prev_in)

    # Aggregates describe the DISPLAYED cohort (the actual recommendation), not
    # the whole eligible pool.
    shown = [x for x in results if x.in_cohort]
    nshown = len(shown)
    avg_g = sum(x.growth_score for x in shown) / nshown if nshown else None
    avg_d = sum(x.dividend_score for x in shown) / nshown if nshown else None
    pivs = sorted(x.p_iv15 for x in shown if x.p_iv15)
    med_piv = pivs[len(pivs) // 2] if pivs else None
    lead_growth = sum(1 for x in shown if x.lead == "Growth")

    cohort = HK50CohortResult(
        run_id=run_id or uuid.uuid4().hex[:12],
        created_at=datetime.now(timezone.utc).isoformat(),
        ticker_count=displayed_count,           # = displayed (back-compat list views)
        avg_growth=round(avg_g, 1) if avg_g is not None else None,
        avg_dividend=round(avg_d, 1) if avg_d is not None else None,
        median_p_iv15=med_piv,
        lead_growth_count=lead_growth,
        eligible_count=len(results),
        displayed_count=displayed_count,
        enter_threshold=ENTER_THRESHOLD,
        stay_threshold=STAY_THRESHOLD,
        promoted=promoted,
        relegated=relegated,
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
    print(f"\nHK50 cohort run_id={res.run_id}  failed={len(res.failed_tickers)}")
    print(f"eligible={res.eligible_count}  displayed={res.displayed_count}  "
          f"ENTER={res.enter_threshold} STAY={res.stay_threshold}")
    print(f"displayed avg Growth={res.avg_growth}  avg Dividend={res.avg_dividend}  "
          f"median P/IV15={res.median_p_iv15}  lead split {res.lead_growth_count}G")
    print(f"promoted={len(res.promoted)}  relegated={len(res.relegated)}")
    shown = [x for x in res.results if x.in_cohort]
    print("Cohort top 10 by lead_score:",
          [(x.cohort_rank, x.name, x.lead_score) for x in shown[:10]])
    min_lead = min((x.lead_score for x in shown), default=None)
    print(f"lowest displayed lead_score={min_lead}  (must be >= STAY {res.stay_threshold})")
    bench = [x for x in res.results if x.membership == "bench"]
    print("First 5 on bench:", [(x.name, x.lead_score) for x in bench[:5]])
    if res.failed_tickers:
        print("Failures:", [f.get("ticker") for f in res.failed_tickers])
