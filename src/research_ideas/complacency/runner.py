"""
src/research_ideas/complacency/runner.py
=========================================
Complacency screener orchestrator.

  run_complacency(max_workers=4, save=True) → ComplacencyCohortResult

For each ticker in the complacency universe:
  fetch → 4-pillar score → cohort assembly → persist.

Per-ticker exceptions go into failed_tickers; the cohort run never aborts
on a single bad ticker.

Persistence: app/backend/services/complacency_storage.py (SQLite).
"""
from __future__ import annotations

import logging
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from src.research_ideas.complacency.data_fetch import fetch_ticker_bundle
from src.research_ideas.complacency.scoring import score_bundle
from src.research_ideas.complacency.schemas import (
    ComplacencyCohortResult,
    ComplacencyTickerResult,
)
from src.research_ideas.complacency.universe import (
    get_ticker_metadata,
    list_tickers,
)


logger = logging.getLogger(__name__)


def _compute_aggregate(quant_composite: float, qual_assessment) -> dict:
    """
    Combine quant (0-8) and qualitative (0-N) into a single 0-100 aggregate.
    Quant contributes 0-50, qual contributes 0-50. Returns a dict ready for
    Pydantic kwargs:
      {aggregate_score, aggregate_quant_pts, aggregate_qual_pts}
    Defaults to quant-only (qual_pts = 0) when no qualitative assessment.
    """
    quant_pts = (max(0.0, min(quant_composite, 8.0)) / 8.0) * 50.0
    qual_pts = 0.0
    if qual_assessment is not None and qual_assessment.max_possible > 0:
        qual_pts = (qual_assessment.composite / qual_assessment.max_possible) * 50.0
    return {
        "aggregate_score": round(quant_pts + qual_pts, 1),
        "aggregate_quant_pts": round(quant_pts, 1),
        "aggregate_qual_pts": round(qual_pts, 1),
    }


def score_one_ticker(
    ticker: str,
    name: str | None = None,
    sector: str | None = None,
    industry: str | None = None,
) -> ComplacencyTickerResult | dict:
    """
    Score ONE ticker ad-hoc (not part of the curated cohort).

    If sector / industry aren't supplied, they're auto-looked-up from FMP's
    /profile endpoint so the sector-median benchmark fires correctly.

    Returns a ComplacencyTickerResult on success or a dict {ticker, reason}
    on failure. Does NOT persist to the cohort table — caller decides what
    to do with the result.
    """
    ticker = ticker.strip().upper()
    if not ticker:
        return {"ticker": ticker, "reason": "empty_ticker"}

    # Auto-fill missing meta from /profile
    if not sector or not name:
        try:
            from src.research_ideas.complacency.data_fetch import fetch_profile
            profile = fetch_profile(ticker)
            if profile:
                name = name or profile.get("name", ticker)
                sector = sector or profile.get("sector")
                industry = industry or profile.get("industry")
        except Exception as exc:
            logger.warning("Profile lookup failed for %s: %s", ticker, exc)

    meta = {
        "ticker": ticker,
        "name": name or ticker,
        "sector": sector,
        "industry": industry,
    }
    return _process_ticker_with_meta(ticker, meta)


def _process_ticker_with_meta(ticker: str, meta: dict) -> ComplacencyTickerResult | dict:
    """Score one ticker given pre-resolved meta (sector/name/industry)."""
    try:
        bundle = fetch_ticker_bundle(ticker, meta)
        if bundle is None:
            return {"ticker": ticker, "reason": "fetch_returned_none"}

        s = score_bundle(bundle)

        # Qualitative LLM scoring — only fire for Strong-Short / Watch.
        # Cached 7 days per (ticker, indicator) so re-running the cohort is cheap.
        qual_assessment = None
        if s["verdict"] in ("Strong-Short", "Watch"):
            try:
                from src.research_ideas.complacency.qualitative import assess_qualitative
                qual_assessment = assess_qualitative(
                    ticker=ticker,
                    name=meta.get("name", ticker),
                    sector=meta.get("sector"),
                    quant_passes_gate=s["passes_gate"],
                    quant_composite=s["composite"],
                )
            except Exception as exc:
                logger.warning("Qualitative assessment for %s failed: %s", ticker, exc)

        # Put-option recommendation — only fire for Strong-Short / Watch
        put_rec = None
        put_rec_at = None
        if s["verdict"] in ("Strong-Short", "Watch") and bundle.price:
            try:
                from src.research_ideas.complacency.options import select_put_recommendation
                from datetime import datetime, timezone
                pick = select_put_recommendation(
                    ticker=ticker,
                    composite=s["composite"],
                    verdict=s["verdict"],
                    current_price=bundle.price,
                )
                if pick:
                    from src.research_ideas.complacency.schemas import PutRecommendation
                    put_rec = PutRecommendation(
                        strike=pick.strike,
                        strike_pct_otm=pick.strike_pct_otm,
                        expiry=pick.expiry,
                        days_to_expiry=pick.days_to_expiry,
                        bid=pick.bid,
                        ask=pick.ask,
                        mid=pick.mid,
                        implied_volatility=pick.implied_volatility,
                        open_interest=pick.open_interest,
                        volume=pick.volume,
                        rationale=pick.rationale,
                        contract_symbol=pick.contract_symbol,
                    )
                    put_rec_at = datetime.now(timezone.utc).isoformat()
            except Exception as exc:
                logger.warning("Put-rec fetch for %s failed: %s", ticker, exc)

        return ComplacencyTickerResult(
            ticker=ticker,
            name=meta.get("name", ticker),
            sector=meta.get("sector"),
            industry=meta.get("industry"),
            price=bundle.price,
            market_cap=bundle.market_cap,
            ev_sales=s["ev_sales"],
            ev_sales_sector_median=s["ev_sales_sector_median"],
            ev_sales_relative=s["ev_sales_relative"],
            fcf_yield_ttm=s["fcf_yield_ttm"],
            altman_z=s["altman_z"],
            piotroski=s["piotroski"],
            ad_ratio_4q_avg=s["ad_ratio_4q_avg"],
            eps_revision_yoy=s["eps_revision_yoy"],
            sma200_extension=s["sma200_extension"],
            rsi_weekly=s["rsi_weekly"],
            range_position=s["range_position"],
            val_score=s["val_score"],
            beh_score=s["beh_score"],
            tech_score=s["tech_score"],
            qual_score=s["qual_score"],
            composite=s["composite"],
            passes_gate=s["passes_gate"],
            verdict=s["verdict"],
            flag_notes=s["flag_notes"],
            justification=s["justification"],
            put_recommendation=put_rec,
            options_data_freshness=put_rec_at,
            qualitative=qual_assessment,
            **_compute_aggregate(s["composite"], qual_assessment),
        )
    except Exception as exc:
        logger.exception("Complacency ad-hoc %s failed: %s", ticker, exc)
        return {"ticker": ticker, "reason": f"{type(exc).__name__}: {exc}"}


def _process_ticker(ticker: str) -> ComplacencyTickerResult | dict:
    """Cohort run path — looks up meta from the curated universe JSON."""
    meta = get_ticker_metadata(ticker)
    return _process_ticker_with_meta(ticker, meta)


def _auto_refresh_sector_medians_if_stale(max_age_days: int = 7) -> None:
    """
    Trigger a sector-median refresh if the latest cached row is older than
    `max_age_days` (or the table is empty). Quiet — failures logged, not raised.
    The refresh itself takes ~5 min on FMP Premium, so this only fires once per
    week per cohort run.
    """
    try:
        from app.backend.services.sector_medians_storage import get_latest_refresh_timestamp
        from src.research_ideas.complacency.sector_medians import refresh_sector_medians

        latest = get_latest_refresh_timestamp()
        if latest:
            from datetime import datetime, timezone
            try:
                dt = datetime.fromisoformat(latest.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                age_days = (datetime.now(timezone.utc) - dt).total_seconds() / 86400
                if age_days <= max_age_days:
                    logger.info("Sector medians fresh (age=%.1fd) — skipping auto-refresh.", age_days)
                    return
                logger.info("Sector medians stale (age=%.1fd > %dd) — refreshing.", age_days, max_age_days)
            except Exception:
                logger.info("Sector medians timestamp unparseable — refreshing.")
        else:
            logger.info("Sector medians cache empty — running initial seed.")

        summary = refresh_sector_medians(max_workers=8, persist=True)
        logger.info("Sector-median refresh complete: %s", summary)
    except Exception as exc:
        logger.warning("Auto-refresh sector medians failed: %s", exc)


def run_complacency(
    max_workers: int = 4,
    save: bool = True,
    run_id: str | None = None,
    auto_refresh_sectors: bool = True,
    sector_max_age_days: int = 7,
) -> ComplacencyCohortResult:
    # If the sector-median cache is stale, refresh BEFORE scoring so we
    # use live medians for this run. Costs ~5 min once a week.
    if auto_refresh_sectors:
        _auto_refresh_sector_medians_if_stale(max_age_days=sector_max_age_days)

    tickers = list_tickers()
    results: list[ComplacencyTickerResult] = []
    failed: list[dict] = []

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_process_ticker, t): t for t in tickers}
        for fut in as_completed(futures):
            out = fut.result()
            if isinstance(out, ComplacencyTickerResult):
                results.append(out)
            elif isinstance(out, dict):
                failed.append({k: v for k, v in out.items() if k != "trace"})

    # Sort: highest composite first, ties broken by ticker.
    results.sort(key=lambda r: (-(r.composite or 0.0), r.ticker))
    for i, r in enumerate(results, 1):
        r.rank = i

    gate_passers = sum(1 for r in results if r.passes_gate)

    cohort = ComplacencyCohortResult(
        run_id=run_id or uuid.uuid4().hex[:12],
        created_at=datetime.now(timezone.utc).isoformat(),
        ticker_count=len(results),
        gate_passers=gate_passers,
        failed_tickers=failed,
        results=results,
    )

    if save:
        try:
            from app.backend.services.complacency_storage import save_complacency_run

            save_complacency_run(cohort)
        except Exception as exc:
            logger.warning("Complacency: persistence skipped — %s", exc)

    return cohort


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    r = run_complacency(max_workers=4, save=False)
    print(f"\nComplacency run_id={r.run_id}")
    print(f"tickers={r.ticker_count}  gate_passers={r.gate_passers}  failed={len(r.failed_tickers)}")
    for t in r.results[:10]:
        print(
            f"  #{t.rank:<3} {t.ticker:<6} {t.verdict:<14} composite={t.composite:.1f}/8  "
            f"V={t.val_score} B={t.beh_score} T={t.tech_score} Q={t.qual_score}"
        )
