"""
src/research_ideas/sw46/runner.py
==================================
SW46 cohort orchestrator.

  run_sw46(history_years=7, max_workers=6, save=True) -> SW46CohortResult

For each ticker in src/research_ideas/data/sw46_universe.json:
  fetch -> tragic_algebra -> roic -> aict -> iv15 -> composite_score

Parallel via ThreadPoolExecutor. Per-ticker exceptions go into
failed_tickers; the cohort run never aborts on a single bad ticker.

Persistence is via app/backend/services/sw46_storage.py (SQLite).
"""
from __future__ import annotations

import logging
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from src.research_ideas.sw46.aict import classify_aict
from src.research_ideas.sw46.composite_score import compute_composite
from src.research_ideas.sw46.data_fetch import fetch_ticker_bundle
from src.research_ideas.sw46.iv15 import compute_iv, solve_ivb
from src.research_ideas.sw46.roic import compute_roic
from src.research_ideas.sw46.schemas import (
    AICTResult,
    CompositeScore,
    IV15Result,
    ROICResult,
    SW46CohortResult,
    SW46TickerResult,
    TragicAlgebraResult,
)
from src.research_ideas.sw46.tragic_algebra import (
    compute_tragic_algebra,
    pooled_delta_e_across_cohort,
)
from src.research_ideas.sw46.universe import (
    get_ticker_metadata,
    list_tickers,
    load_universe,
)


logger = logging.getLogger(__name__)


def _generate_justification(
    name: str,
    composite_total: float,
    ta_tier: str,
    aict_tier: str,
    roic: float | None,
    ivb_pct: float | None,
    p_iv15: float | None,
    sbc_trend: float | None,
    fwd_growth: float | None,
) -> str:
    """
    Auto-generate a one-line Cassandra-style narrative summarising the verdict.
    Composes from observed metric signals only — no LLM, no hand curation.
    """
    parts: list[str] = []

    # Headline verdict
    if composite_total >= 60:
        parts.append("Fat Pitch territory")
    elif composite_total >= 45:
        parts.append("watch-list quality")
    elif composite_total >= 25:
        parts.append("not close to a buy")
    elif composite_total >= 10:
        parts.append("avoid at current levels")
    else:
        parts.append("deeply unattractive")

    # TA observation
    if ta_tier == "TT*":
        parts.append("Tragic Tier SBC abuser")
    elif ta_tier == "Near-TT":
        parts.append("borderline on shareholder retention")
    elif ta_tier == "Not-TT":
        parts.append("clean SBC discipline")
    else:
        parts.append("inflecting from losses, ΔE math unstable")

    # SBC trend
    if sbc_trend is not None:
        if sbc_trend <= -0.005:
            parts.append("improving SBC trajectory")
        elif sbc_trend >= 0.005:
            parts.append("SBC/revenue ratio worsening")

    # AICT
    aict_phrase = {
        "Fortress": "Fortress — minimal AI threat",
        "Castle":   "Castle — defensible against AI",
        "Chapel":   "Chapel — acute AI threat, real R&D",
        "Stone":    "Stone — moderate threat, shallower moat",
        "Wood":     "Wood — AI strategy fully borrowed",
    }.get(aict_tier, aict_tier)
    parts.append(aict_phrase)

    # ROIC
    if roic is not None:
        if roic >= 0.25:
            parts.append(f"strong ROIC {roic*100:.0f}%")
        elif roic >= 0.10:
            parts.append(f"ROIC {roic*100:.0f}% (adequate)")
        elif roic > 0:
            parts.append(f"thin ROIC {roic*100:.0f}%")
        else:
            parts.append("negative ROIC")

    # Growth
    if fwd_growth is not None:
        if fwd_growth >= 0.25:
            parts.append(f"~{fwd_growth*100:.0f}% fwd growth")
        elif fwd_growth >= 0.10:
            parts.append(f"~{fwd_growth*100:.0f}% growth")
        else:
            parts.append("low growth outlook")

    # Valuation verdict via IVB
    if ivb_pct is not None:
        ivb_p = ivb_pct * 100
        if ivb_p >= 15:
            parts.append(f"current price implies {ivb_p:.1f}% LT return — Fat Pitch Zone")
        elif ivb_p >= 12:
            parts.append(f"{ivb_p:.1f}% implied return — just outside")
        elif ivb_p >= 8:
            parts.append(f"{ivb_p:.1f}% implied return — bougie")
        else:
            parts.append(f"only {ivb_p:.1f}% implied return — expensive")
    elif p_iv15 is not None:
        if p_iv15 <= 1.0:
            parts.append("trades below IV15")
        elif p_iv15 <= 1.5:
            parts.append(f"P/IV15 {p_iv15:.2f}x — near IV15")
        elif p_iv15 <= 3.0:
            parts.append(f"P/IV15 {p_iv15:.2f}x — overpriced")
        else:
            parts.append(f"P/IV15 {p_iv15:.2f}x — far above IV15")
    else:
        parts.append("IV15 undefined (negative OE / no forward NI)")

    return f"{name}: " + "; ".join(parts) + "."


def _process_ticker(ticker: str, history_years: int) -> SW46TickerResult | dict:
    """
    Returns SW46TickerResult on success, or {"ticker", "reason"} dict on failure.
    """
    try:
        md = get_ticker_metadata(ticker)
        bundle = fetch_ticker_bundle(ticker, history_years=history_years)
        if bundle is None:
            return {"ticker": ticker, "reason": "fetch_returned_none"}

        ta = compute_tragic_algebra(bundle)
        roic = compute_roic(bundle, ta)
        aict = classify_aict(ticker)

        # Multi-RR sensitivity: IV12 / IV15 / IV18 + IVB solved for current price
        iv15 = compute_iv(bundle, ta, roic, aict, required_return=0.15)
        iv12 = compute_iv(bundle, ta, roic, aict, required_return=0.12)
        iv18 = compute_iv(bundle, ta, roic, aict, required_return=0.18)
        ivb_pct = solve_ivb(bundle, ta, roic, aict)

        p_iv12 = None
        p_iv18 = None
        if bundle.current_price and iv12.iv15_per_share and iv12.iv15_per_share > 0:
            p_iv12 = bundle.current_price / iv12.iv15_per_share
        if bundle.current_price and iv18.iv15_per_share and iv18.iv15_per_share > 0:
            p_iv18 = bundle.current_price / iv18.iv15_per_share

        composite = compute_composite(bundle, ta, roic, aict, iv15)

        justification = _generate_justification(
            name=md.get("name", ticker),
            composite_total=composite.total,
            ta_tier=ta.ta_tier,
            aict_tier=aict.tier,
            roic=roic.roic,
            ivb_pct=ivb_pct,
            p_iv15=composite.p_iv15,
            sbc_trend=ta.sbc_trend,
            fwd_growth=bundle.forward_revenue_growth,
        )

        return SW46TickerResult(
            ticker=ticker,
            name=md.get("name", ticker),
            aict=aict,
            tragic_algebra=ta,
            roic=roic,
            iv15=iv15,
            iv12=iv12,
            iv18=iv18,
            ivb_pct=ivb_pct,
            p_iv12=p_iv12,
            p_iv18=p_iv18,
            composite=composite,
            price=bundle.current_price,
            market_cap=bundle.market_cap,
            fwd_revenue_growth=bundle.forward_revenue_growth,
            justification=justification,
        )
    except Exception as exc:
        tb = traceback.format_exc()
        logger.exception("SW46 ticker %s failed: %s", ticker, exc)
        return {"ticker": ticker, "reason": f"{type(exc).__name__}: {exc}", "trace": tb}


def run_sw46(
    history_years: int = 7,
    max_workers: int = 6,
    save: bool = True,
    run_id: str | None = None,
) -> SW46CohortResult:
    tickers = list_tickers()
    results: list[SW46TickerResult] = []
    failed: list[dict] = []

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_process_ticker, t, history_years): t for t in tickers}
        for fut in as_completed(futures):
            out = fut.result()
            if isinstance(out, SW46TickerResult):
                results.append(out)
            elif isinstance(out, dict):
                failed.append({k: v for k, v in out.items() if k != "trace"})

    # Stable display order: highest composite first, ties broken by ticker.
    results.sort(key=lambda r: (-(r.composite.total or 0.0), r.ticker))
    # Persist rank on each result so the UI can show "SW46 #X of 46"
    for i, r in enumerate(results, start=1):
        r.rank = i

    pooled = pooled_delta_e_across_cohort([r.tragic_algebra for r in results])

    cohort = SW46CohortResult(
        run_id=run_id or uuid.uuid4().hex[:12],
        created_at=datetime.now(timezone.utc).isoformat(),
        pooled_delta_e=pooled,
        ticker_count=len(results),
        failed_tickers=failed,
        results=results,
    )

    if save:
        # Late import — avoid circular dependency with app/backend
        # when this module is imported standalone (e.g., notebook).
        try:
            from app.backend.services.sw46_storage import save_sw46_run

            save_sw46_run(cohort)
        except Exception as exc:
            logger.warning("SW46: persistence skipped — %s", exc)

    return cohort


if __name__ == "__main__":
    # Manual smoke test:
    #   uv run python -m src.research_ideas.sw46.runner
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    res = run_sw46(history_years=5, max_workers=4, save=False)
    print(f"\nSW46 cohort run_id={res.run_id}")
    print(f"pooled_delta_e = {res.pooled_delta_e}")
    print(f"successes = {len(res.results)}    failures = {len(res.failed_tickers)}")
    for r in res.results[:5]:
        print(
            f"  {r.ticker:6s} AICT={r.aict.tier:9s} dE={r.tragic_algebra.pooled_delta_e}"
            f"  ROIC={r.roic.roic}  P/IV15={r.composite.p_iv15}  score={r.composite.total:.1f}"
        )
    if res.failed_tickers:
        print(f"\nFailures: {res.failed_tickers}")
