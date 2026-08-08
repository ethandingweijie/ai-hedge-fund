"""
src/research_ideas/hundred_q/runner.py
=========================================
Phase 0 orchestrator — quant-only, no LLM, no event triggers yet (those
land in Phase 1/2 per the approved plan's phased rollout).

  run_full_quant_batch(max_workers=8) -> HundredQCohortResult

Two-pass design: (1) fetch every ticker's bundle in parallel, (2) compute
cross-sectional sector medians from the whole batch, then (3) score each
bundle against those medians. This is why sector-relative questions
(operating margin vs sector, EV/EBITDA vs peers, ...) can't be scored
per-ticker in isolation — they need the full batch fetched first.

Sector medians are computed from THIS SCREENING UNIVERSE itself (grouped
by the `sector` field in hundred_q_universe.json), not from
src/data/sector_profiles.py's curated DCF peer baskets — those use a
different profile-name taxonomy sized for peer-multiple DCF inputs, not
general margin/turnover comparison, and mapping between the two taxonomies
is deferred until the universe scales past the Phase-0 pilot (see Phase 4
in the approved plan).
"""
from __future__ import annotations

import logging
import os
import statistics
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone

from src.research_ideas.hundred_q import qualitative, storage
from src.research_ideas.hundred_q.data_fetch import HundredQBundle, fetch_ticker_bundle
from src.research_ideas.hundred_q.questions_registry import REGISTRY, TRIGGER_TO_QUESTIONS
from src.research_ideas.hundred_q.schemas import HundredQCohortResult, HundredQTickerResult, QuestionAnswer
from src.research_ideas.hundred_q.scoring import aggregate_answers, score_bundle, tier_for
from src.research_ideas.hundred_q.universe import get_ticker_metadata, list_tickers

logger = logging.getLogger(__name__)

# Separate state file from the existing pipeline-monitor's trigger_state.json —
# both systems could otherwise use the same trigger name (e.g. "price_shock")
# with different thresholds for the same ticker and stomp each other's cooldown.
_HQ_TRIGGER_STATE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "data", "hundred_q_trigger_state.json",
)

# 10-year UST proxy — refresh manually/periodically; not wired to a live
# macro feed in Phase 0 (see approved plan §1, P5.4).
RISK_FREE_RATE = 0.04

_SECTOR_MEDIAN_FIELDS = [
    "operating_margin", "asset_turnover", "gross_margin",
    "enterprise_value_to_ebitda_ratio", "enterprise_value_to_revenue_ratio",
    "price_to_book_ratio", "return_on_equity",
]


def _compute_sector_medians(bundles: list[HundredQBundle]) -> dict[str, dict[str, float]]:
    by_sector: dict[str, dict[str, list[float]]] = {}
    for b in bundles:
        if not b.sector:
            continue
        bucket = by_sector.setdefault(b.sector, {f: [] for f in _SECTOR_MEDIAN_FIELDS})
        for f in _SECTOR_MEDIAN_FIELDS:
            v = getattr(b, f, None)
            if v is not None:
                bucket[f].append(v)

    result: dict[str, dict[str, float]] = {}
    for sector, fields in by_sector.items():
        result[sector] = {}
        for f, vals in fields.items():
            if vals:
                result[sector][f] = statistics.median(vals)
    return result


def _fetch_all(tickers: list[str], max_workers: int) -> dict[str, HundredQBundle]:
    bundles: dict[str, HundredQBundle] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(fetch_ticker_bundle, t, get_ticker_metadata(t)): t for t in tickers}
        for fut in as_completed(futures):
            ticker = futures[fut]
            try:
                bundle = fut.result()
            except Exception as exc:
                logger.warning("fetch_ticker_bundle(%s) failed: %s", ticker, exc)
                bundle = None
            if bundle is not None:
                bundles[ticker] = bundle
    return bundles


def score_ticker_quant_only(bundle: HundredQBundle, ctx: dict) -> HundredQTickerResult:
    scored = score_bundle(bundle, ctx)
    composite = scored["quant_composite_pct"]   # Phase 0: no qual layer yet, composite == quant
    return HundredQTickerResult(
        ticker=bundle.ticker,
        name=bundle.name,
        sector=bundle.sector,
        industry=bundle.industry,
        price=bundle.price,
        market_cap=bundle.market_cap,
        question_ledger=scored["question_ledger"],
        pillar_scores=scored["pillar_scores"],
        quant_composite_pct=composite,
        qual_composite_pct=None,
        composite_pct=composite,
        tier=tier_for(composite),
        evaluated_at=datetime.now(timezone.utc).isoformat(),
        error=bundle.error,
    )


def run_full_quant_batch(
    max_workers: int = 8,
    save: bool = True,
    run_id: str | None = None,
    run_type: str = "weekly_quant",
) -> HundredQCohortResult:
    tickers = list_tickers()
    bundles_by_ticker = _fetch_all(tickers, max_workers)
    sector_medians = _compute_sector_medians(list(bundles_by_ticker.values()))
    ctx = {"sector_medians": sector_medians, "risk_free_rate": RISK_FREE_RATE}

    results: list[HundredQTickerResult] = []
    failed: list[dict] = []
    for ticker in tickers:
        bundle = bundles_by_ticker.get(ticker)
        if bundle is None:
            failed.append({"ticker": ticker, "reason": "fetch_failed"})
            continue
        results.append(score_ticker_quant_only(bundle, ctx))

    results.sort(key=lambda r: (-(r.composite_pct or -1.0), r.ticker))
    for i, r in enumerate(results, 1):
        r.rank = i

    tier_counts: dict[str, int] = {}
    for r in results:
        tier_counts[r.tier] = tier_counts.get(r.tier, 0) + 1

    cohort = HundredQCohortResult(
        run_id=run_id or uuid.uuid4().hex[:12],
        created_at=datetime.now(timezone.utc).isoformat(),
        run_type=run_type,
        ticker_count=len(results),
        tier_counts=tier_counts,
        failed_tickers=failed,
        results=results,
    )

    if save:
        try:
            storage.save_run(cohort)
        except Exception as exc:
            logger.warning("hundred_q: persistence skipped — %s", exc)

    return cohort


def assemble_full_ticker_result(ticker: str) -> HundredQTickerResult | None:
    """
    Build the CURRENT full quant+qual view of one ticker from persisted
    state: the latest known answer per quant question (across all past
    runs, not just one run_id) merged with the latest cached qualitative
    answers. This is what makes a partial event-triggered rescore work —
    it recomputes the overall composite_pct/tier from everything known
    about the ticker, not just the handful of questions a trigger touched.

    Returns None if nothing has ever been scored for this ticker.
    """
    ticker = ticker.upper()
    quant_rows = storage.get_latest_quant_answers(ticker)
    qual_rows = storage.get_qual_cache(ticker)
    if not quant_rows and not qual_rows:
        return None

    meta = get_ticker_metadata(ticker)
    ledger: list[QuestionAnswer] = []

    for qid, row in quant_rows.items():
        qdef = REGISTRY.get(qid)
        ledger.append(QuestionAnswer(
            question_id=qid, pillar=row["pillar"], label=qdef.label if qdef else qid,
            q_type="quant", answer=None if row["answer"] is None else bool(row["answer"]),
            raw_value=row["raw_value"], source=row["source"], evaluated_at=row["evaluated_at"],
        ))
    for qid, row in qual_rows.items():
        ledger.append(qualitative._answer_to_question_answer(qid, row))

    agg = aggregate_answers(ledger)
    quant_only_ledger = [qa for qa in ledger if qa.q_type == "quant"]
    qual_only_ledger = [qa for qa in ledger if qa.q_type == "qual"]
    quant_agg = aggregate_answers(quant_only_ledger)
    qual_agg = aggregate_answers(qual_only_ledger)

    return HundredQTickerResult(
        ticker=ticker, name=meta.get("name", ticker), sector=meta.get("sector"), industry=meta.get("industry"),
        question_ledger=ledger, pillar_scores=agg["pillar_scores"],
        quant_composite_pct=quant_agg["composite_pct"], qual_composite_pct=qual_agg["composite_pct"],
        composite_pct=agg["composite_pct"], tier=tier_for(agg["composite_pct"]),
        evaluated_at=datetime.now(timezone.utc).isoformat(),
    )


def run_event_triggered_rescore(
    ticker: str, trigger_type: str, reason: str = "", save: bool = True,
) -> HundredQTickerResult | None:
    """
    Re-score EXACTLY the question_ids TRIGGER_TO_QUESTIONS maps trigger_type
    to — never the whole ~90-question set — then reassemble the ticker's
    full composite from that partial update plus everything already known.
    This is the concrete implementation of the plan's worked example:
    a Form-4 net-buy re-scores only P4.2/P4.14, not the whole governance
    pillar or any moat/catalyst question.
    """
    ticker = ticker.upper()
    question_ids = TRIGGER_TO_QUESTIONS.get(trigger_type, [])
    if not question_ids:
        logger.warning("run_event_triggered_rescore: no mapping for trigger_type=%s", trigger_type)
        return assemble_full_ticker_result(ticker)

    quant_ids = [q for q in question_ids if q in REGISTRY]
    qual_ids = [q for q in question_ids if q in qualitative._QUAL_INDICATORS]

    meta = get_ticker_metadata(ticker)
    run_id = uuid.uuid4().hex[:12]
    now_iso = datetime.now(timezone.utc).isoformat()
    touched_ledger: list[QuestionAnswer] = []

    if quant_ids:
        bundle = fetch_ticker_bundle(ticker, meta)
        if bundle is not None:
            ctx = {"sector_medians": {}, "risk_free_rate": RISK_FREE_RATE}
            scored = score_bundle(bundle, ctx)
            by_id = {qa.question_id: qa for qa in scored["question_ledger"]}
            for qid in quant_ids:
                qa = by_id.get(qid)
                if qa is not None:
                    touched_ledger.append(qa)

    if qual_ids:
        qual_answers = qualitative.assess_qualitative_pillar(
            ticker, meta.get("name", ticker), meta.get("sector"), qual_ids,
            force_refresh=True, triggered_by=trigger_type,
        )
        touched_ledger.extend(qual_answers.values())

    # Record the run manifest (with trigger_type/trigger_ticker) FIRST — the
    # cohort save below reuses the same run_id and its ON CONFLICT clause
    # only touches finished_at, so this row's trigger fields survive.
    quant_touched = [qa for qa in touched_ledger if qa.q_type == "quant"]
    if save:
        storage.record_event_triggered_run(run_id, ticker, trigger_type, quant_touched)

    result = assemble_full_ticker_result(ticker)
    if result is None:
        return None

    if save:
        prev_rows = storage.get_watchlist()
        prev_tier = next((r["tier"] for r in prev_rows if r["ticker"] == ticker), None)
        cohort = HundredQCohortResult(
            run_id=run_id, created_at=now_iso, run_type="event_triggered",
            ticker_count=1, tier_counts={result.tier: 1}, results=[result],
        )
        # save_run() also upserts hq_watchlist and writes hq_tier_history
        # if the tier changed — the check below is just for a clearer log
        # line, not the only place the transition gets persisted.
        storage.save_run(cohort)
        if prev_tier is not None and prev_tier != result.tier:
            logger.info(
                "hundred_q: %s tier changed %s -> %s (trigger=%s: %s)",
                ticker, prev_tier, result.tier, trigger_type, reason,
            )

    return result


def run_daily_trigger_sweep(tickers: list[str] | None = None) -> list[dict]:
    """
    Check every new detector (new 10-K/10-Q/DEF 14A, Form-4 net-buy,
    earnings-reported, price-shock) against `tickers` (defaults to the
    current hq_watchlist), firing run_event_triggered_rescore on hits.
    Uses a SEPARATE state file (hundred_q_trigger_state.json) from the
    existing pipeline-monitor's trigger_state.json.
    """
    from src.triggers import detectors as trigger_detectors
    from src.triggers import state as trigger_state

    if tickers is None:
        tickers = [r["ticker"] for r in storage.get_watchlist()] or list_tickers()

    state = trigger_state.load_state(path=_HQ_TRIGGER_STATE_PATH)
    fired_log: list[dict] = []

    for ticker in tickers:
        checks = [
            ("new_10k", lambda t=ticker: trigger_detectors.new_edgar_filing(t, "10-K")),
            ("new_10q", lambda t=ticker: trigger_detectors.new_edgar_filing(t, "10-Q")),
            ("new_def14a", lambda t=ticker: trigger_detectors.new_edgar_filing(t, "DEF 14A")),
            ("form4_net_buy", lambda t=ticker: trigger_detectors.form4_net_buy(t)),
            ("earnings_reported", lambda t=ticker: trigger_detectors.earnings_reported(t)),
            ("price_shock_8pct", lambda t=ticker: trigger_detectors.price_shock(t, threshold_pct=8.0)),
        ]
        for trigger_type, check_fn in checks:
            try:
                fired, reason, key = check_fn()
            except Exception as exc:
                logger.warning("trigger check %s(%s) failed: %s", trigger_type, ticker, exc)
                continue
            if not fired or not key:
                continue
            if trigger_state.already_fired(state, ticker, trigger_type, key):
                continue
            trigger_state.mark_fired(state, ticker, trigger_type, key)
            run_event_triggered_rescore(ticker, trigger_type, reason=reason)
            fired_log.append({"ticker": ticker, "trigger_type": trigger_type, "reason": reason})

    trigger_state.save_state(state, path=_HQ_TRIGGER_STATE_PATH)
    storage.record_sweep_run(uuid.uuid4().hex[:12], "daily_trigger_sweep", ticker_count=len(tickers))
    return fired_log


def run_quarterly_annual_backstop(max_age_days: int = 365, tiers: tuple[str, ...] = ("active_pass", "on_deck")) -> list[dict]:
    """
    Quarterly job: for every ticker in an active tier, find qualitative
    questions that are missing or older than `max_age_days` and rescore
    them. This is the one place a near-full qual sweep is legitimate
    (per the approved plan §4) — scoped to Active/On-Deck tickers only,
    not the full screening universe, since there's no reason to spend
    LLM budget refreshing names nobody's watching.
    """
    watchlist = storage.get_watchlist()
    target_tickers = [r["ticker"] for r in watchlist if r["tier"] in tiers]

    touched: list[dict] = []
    for ticker in target_tickers:
        stale_ids = qualitative.get_stale_qual_questions(ticker, max_age_days=max_age_days)
        if not stale_ids:
            continue
        meta = get_ticker_metadata(ticker)
        qualitative.assess_qualitative_pillar(
            ticker, meta.get("name", ticker), meta.get("sector"), stale_ids,
            force_refresh=True, triggered_by="annual_backstop",
        )
        result = assemble_full_ticker_result(ticker)
        if result is not None:
            cohort = HundredQCohortResult(
                run_id=uuid.uuid4().hex[:12], created_at=datetime.now(timezone.utc).isoformat(),
                run_type="annual_backstop", ticker_count=1, tier_counts={result.tier: 1}, results=[result],
            )
            storage.save_run(cohort)
        touched.append({"ticker": ticker, "stale_question_count": len(stale_ids)})

    storage.record_sweep_run(uuid.uuid4().hex[:12], "annual_backstop", ticker_count=len(target_tickers))
    return touched


def score_ticker_full(ticker: str, force_qual: bool = False) -> HundredQTickerResult | None:
    """
    Ad-hoc single-ticker rescore for the API/frontend "force rescore"
    action — not part of the pillar-scoped event-trigger cost-control
    path (that's run_event_triggered_rescore). Always recomputes quant.
    force_qual=True additionally re-scores EVERY registered qualitative
    question (not cache-limited) — an explicit, user-initiated exception
    to the "never re-run all ~33 qual questions" rule, mirroring
    complacency's own force_qual UI affordance.
    """
    ticker = ticker.upper()
    meta = get_ticker_metadata(ticker)
    bundle = fetch_ticker_bundle(ticker, meta)
    if bundle is None:
        return None

    ctx = {"sector_medians": {}, "risk_free_rate": RISK_FREE_RATE}
    scored = score_bundle(bundle, ctx)
    run_id = uuid.uuid4().hex[:12]
    storage.record_event_triggered_run(run_id, ticker, "manual_full_rescore", scored["question_ledger"])

    if force_qual:
        qualitative.assess_qualitative_pillar(
            ticker, meta.get("name", ticker), meta.get("sector"), list(qualitative._QUAL_INDICATORS.keys()),
            force_refresh=True, triggered_by="manual_full_rescore",
        )

    result = assemble_full_ticker_result(ticker)
    if result is not None:
        cohort = HundredQCohortResult(
            run_id=run_id, created_at=datetime.now(timezone.utc).isoformat(), run_type="adhoc",
            ticker_count=1, tier_counts={result.tier: 1}, results=[result],
        )
        storage.save_run(cohort)
    return result


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv(override=True)
    load_dotenv(".env.local", override=True)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cohort = run_full_quant_batch(max_workers=6, save=True, run_type="adhoc")
    print(f"\nhundred_q run_id={cohort.run_id}  tickers={cohort.ticker_count}  failed={len(cohort.failed_tickers)}")
    print(f"tier_counts={cohort.tier_counts}\n")
    for r in cohort.results:
        pct = f"{r.composite_pct:.0%}" if r.composite_pct is not None else "N/A"
        answered = sum(p.questions_answered for p in r.pillar_scores)
        print(f"  #{r.rank:<3} {r.ticker:<6} {r.tier:<12} composite={pct:<6} answered={answered:<3} {r.name}")
    if cohort.failed_tickers:
        print("\nFailed:", cohort.failed_tickers)
