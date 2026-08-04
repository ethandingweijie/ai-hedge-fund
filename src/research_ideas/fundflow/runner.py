"""
src/research_ideas/fundflow/runner.py
======================================
Geographic fund-flow orchestrator.

  run_fundflow(as_of=None, max_workers=8, save=True) -> FundFlowCohortResult

Sequence:
  1. Fetch every distinct ETF in the universe once, concurrently. Baskets
     overlap across regions, so fetching per-region would re-pull the same
     symbols; the shared cache below fetches each exactly once.
  2. Collapse each region's basket into one set of dollar series and score it.
  3. Layer the price-momentum overlay (reusing the Sectors momentum engine on
     the primary ETF) so flow can be checked against price.
  4. Compute the rotation overlay — each region's share of the universe's
     total flow, now versus a month ago. Share is the number that answers
     "where is money moving FROM and TO", which a per-region reading cannot.
  5. Build the deterministic summary and persist.

`as_of` (ISO yyyy-mm-dd) points the whole screen at a historical date.
Persistence: app/backend/services/fundflow_storage.py (SQLite).
"""
from __future__ import annotations

import logging
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd

from src.research_ideas.fundflow.data_fetch import FlowBundle, fetch_flow_bundle
from src.research_ideas.fundflow.indicators import RegionSeries, aggregate_basket
from src.research_ideas.fundflow.schemas import (
    FlowSparkPoint,
    FundFlowCohortResult,
    FundFlowRegionResult,
)
from src.research_ideas.fundflow.scoring import (
    classify_divergence,
    compose_justification,
    score_flow_series,
)
from src.research_ideas.fundflow.narrator import narrate
from src.research_ideas.fundflow.summary import build_summary
from src.research_ideas.fundflow.universe import list_benchmarks, list_regions
from src.research_ideas.momentum.data_fetch import MomentumBundle
from src.research_ideas.momentum.indicators import trailing_return
from src.research_ideas.momentum.scoring import score_series


logger = logging.getLogger(__name__)

_MONTH_BARS = 21        # trading sessions in ~1 month
_QUARTER_BARS = 63      # trading sessions in ~3 months
_HALF_BARS = 126        # trading sessions in ~6 months
_YEAR_BARS = 252        # trading sessions in ~12 months
_SPARK_BARS = 126       # points on the flow-pressure sparkline (~6 months)


# ─── Fetch layer ────────────────────────────────────────────────────────────


def _fetch_all(symbols: list[str], as_of: Optional[str],
               max_workers: int) -> dict[str, FlowBundle]:
    """Fetch every symbol once. Failures are omitted, never raised."""
    out: dict[str, FlowBundle] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(fetch_flow_bundle, s, as_of): s for s in symbols}
        for fut in as_completed(futures):
            sym = futures[fut]
            try:
                b = fut.result()
            except Exception as exc:
                logger.warning("fundflow: fetch failed for %s: %s", sym, exc)
                continue
            if b is not None:
                out[sym] = b
    return out


# ─── Prior-period re-scoring ────────────────────────────────────────────────


def _truncate(rs: RegionSeries, back_bars: int) -> Optional[RegionSeries]:
    """
    The same region with the last `back_bars` sessions chopped off, so the bar
    `back_bars` ago is treated as 'now'. Returns None when what remains is too
    short for the 63-day reads the scorer needs.
    """
    if len(rs.flow) - back_bars < 90:
        return None
    return RegionSeries(
        region=rs.region,
        flow=rs.flow.iloc[:-back_bars],
        dvol=rs.dvol.iloc[:-back_bars],
        price=rs.price.iloc[:-back_bars],
        aum=rs.aum,
        implied=rs.implied.iloc[:-back_bars] if rs.implied is not None else None,
        implied_quality=rs.implied_quality,
        members=rs.members,
    )


def _prior_scored(rs: RegionSeries, back_bars: int) -> Optional[dict]:
    prior = _truncate(rs, back_bars)
    if prior is None:
        return None
    try:
        return score_flow_series(prior)
    except Exception:
        return None


# ─── Price overlay ──────────────────────────────────────────────────────────


def _price_overlay(bundles: dict[str, FlowBundle], meta: dict) -> dict:
    """
    Score the primary ETF's price series on the Sectors momentum engine, and
    decompose FX where a hedged twin exists. Every field degrades to None
    rather than failing the region.
    """
    out: dict = {
        "price_composite": None, "price_verdict": None,
        "r_21d": None, "r_63d": None, "r_126d": None, "r_252d": None,
        "fx_drag_21d": None,
    }
    primary = bundles.get(meta["etf"])
    if primary is None or primary.df is None or len(primary.df) < 60:
        return out

    close = primary.df["close"]
    out["r_21d"] = trailing_return(close, 21)
    out["r_63d"] = trailing_return(close, 63)
    out["r_126d"] = trailing_return(close, 126)
    out["r_252d"] = trailing_return(close, 252)

    try:
        mb = MomentumBundle(
            symbol=primary.symbol,
            df=primary.df,
            has_true_ohlc=primary.has_true_ohlc,
            as_of=primary.as_of,
            bars=primary.bars,
        )
        scored = score_series(mb)
        out["price_composite"] = scored.get("composite")
        out["price_verdict"] = scored.get("verdict")
    except Exception as exc:
        logger.warning("fundflow: price overlay failed for %s: %s", meta["etf"], exc)

    # FX drag: the unhedged USD return minus the currency-hedged twin's. What
    # remains is the currency's contribution, which for Japan and Europe is
    # regularly the larger half of a USD investor's outcome.
    hedged_sym = meta.get("hedged")
    if hedged_sym and hedged_sym != meta["etf"]:
        h = bundles.get(hedged_sym)
        if h is not None and h.df is not None and len(h.df) > 21 and out["r_21d"] is not None:
            hr = trailing_return(h.df["close"], 21)
            if hr is not None:
                out["fx_drag_21d"] = out["r_21d"] - hr
    return out


# ─── Per-region assembly ────────────────────────────────────────────────────


def _build_region(meta: dict, bundles: dict[str, FlowBundle],
                  is_benchmark: bool) -> Optional[FundFlowRegionResult]:
    basket = meta.get("basket") or [meta["etf"]]
    present = [bundles[s] for s in basket if s in bundles]
    if not present:
        return None

    rs = aggregate_basket(present, meta["region"])
    if rs is None or len(rs.flow) < 90:
        return None

    scored = score_flow_series(rs)
    prior_1m = _prior_scored(rs, _MONTH_BARS)
    prior_3m = _prior_scored(rs, _QUARTER_BARS)
    prior_6m = _prior_scored(rs, _HALF_BARS)
    prior_12m = _prior_scored(rs, _YEAR_BARS)
    price = _price_overlay(bundles, meta)

    notes = list(scored.get("flag_notes") or [])
    divergence = classify_divergence(scored["composite"], price["price_composite"])
    if divergence == "flow-leads":
        notes.append(
            f"Flow is ahead of price (flow {scored['composite']:+.0f} vs price "
            f"{price['price_composite']:+.0f}) — money moving before the move"
        )
    elif divergence == "price-leads":
        notes.append(
            f"Price is ahead of flow (price {price['price_composite']:+.0f} vs flow "
            f"{scored['composite']:+.0f}) — the move lacks flow backing"
        )
    if price.get("fx_drag_21d") is not None and abs(price["fx_drag_21d"]) > 0.01:
        d = price["fx_drag_21d"]
        notes.append(
            f"Currency {'added' if d > 0 else 'cost'} {abs(d) * 100:.1f}pp of the "
            f"1m USD return (unhedged vs hedged)"
        )
    if rs.implied_quality == "stale":
        notes.append(
            "Issuer share-count feed is stale for this basket — implied "
            "creation/redemption shown as n/a; the tape-derived flow above stands"
        )

    spark = _build_spark(rs)

    if scored.get("implied_flow_21d") is not None and scored.get("implied_flow_21d_pct_aum") is not None:
        notes.append(
            f"Issuer creation/redemption over 1m: "
            f"{scored['implied_flow_21d'] / 1e9:+.2f}bn USD "
            f"({scored['implied_flow_21d_pct_aum'] * 100:+.2f}% of basket assets) — "
            "measured from shares outstanding, independent of the tape signal above"
        )

    return FundFlowRegionResult(
        region=meta["region"],
        label=meta["label"],
        emoji=meta.get("emoji"),
        bloc=meta.get("bloc"),
        etf=meta["etf"],
        basket=[b.symbol for b in present],
        is_benchmark=is_benchmark,
        bars=len(rs.flow),
        aum=rs.aum,
        composite_1m=prior_1m.get("composite") if prior_1m else None,
        composite_3m=prior_3m.get("composite") if prior_3m else None,
        composite_6m=prior_6m.get("composite") if prior_6m else None,
        composite_12m=prior_12m.get("composite") if prior_12m else None,
        divergence=divergence,
        spark=spark,
        data_notes=sorted(set(rs.notes)),
        justification=compose_justification(
            meta["label"], scored["verdict"], scored["composite"],
            scored.get("cmf_z_21"), notes,
        ),
        **{**scored, "flag_notes": notes},
        **price,
    )


def _build_spark(rs: RegionSeries) -> list[FlowSparkPoint]:
    """
    Flow-pressure trace over the last quarter, in sigma off the region's own
    baseline. Every region therefore shares one vertical scale, and zero means
    the same thing on all nine sparklines: flow running at this geography's
    normal. Raw cumulative dollars would put the US three orders of magnitude
    above Indonesia and make the small multiples unreadable.
    """
    from src.research_ideas.fundflow.indicators import cmf_z_series

    series = cmf_z_series(rs.flow, rs.dvol, 21).iloc[-_SPARK_BARS:]
    return [
        FlowSparkPoint(d=idx.strftime("%Y-%m-%d"), v=round(float(v), 3))
        for idx, v in series.items()
        if pd.notna(v) and np.isfinite(v)
    ]


# ─── Rotation overlay ───────────────────────────────────────────────────────


def _apply_rotation(regions: list[FundFlowRegionResult],
                    series_by_region: dict[str, RegionSeries],
                    world: Optional[FundFlowRegionResult],
                    world_series: Optional[RegionSeries]) -> None:
    """
    Each region's flow pressure NET OF the global benchmark's, now and a month
    ago. Subtracting ACWI is what turns nine correlated readings into a
    rotation map: in a broad risk-on month every geography prints inflow, and
    only the excess over the world separates "investors chose this" from
    "everything went up".

    The month-ago comparison is recomputed from truncated series rather than
    read off a stored earlier run, so the number is available on the very
    first run and cannot drift out of sync with the current one.
    """
    from src.research_ideas.fundflow.indicators import cmf_z

    world_now = world.cmf_z_21 if world else None
    world_prior = None
    if world_series is not None:
        wp = _truncate(world_series, _MONTH_BARS)
        if wp is not None:
            world_prior = cmf_z(wp.flow, wp.dvol, 21)

    for r in regions:
        if world_now is not None and r.cmf_z_21 is not None:
            r.rel_flow_z = r.cmf_z_21 - world_now

        rs = series_by_region.get(r.region)
        p = _truncate(rs, _MONTH_BARS) if rs is not None else None
        if p is not None and world_prior is not None:
            own_prior = cmf_z(p.flow, p.dvol, 21)
            if own_prior is not None:
                r.rel_flow_z_1m = own_prior - world_prior

        if r.rel_flow_z is not None and r.rel_flow_z_1m is not None:
            r.rel_flow_z_delta = r.rel_flow_z - r.rel_flow_z_1m


# ─── Entry point ────────────────────────────────────────────────────────────


def run_fundflow(
    as_of: Optional[str] = None,
    max_workers: int = 8,
    save: bool = True,
    run_id: Optional[str] = None,
    narrate_summary: bool = True,
) -> FundFlowCohortResult:
    region_metas = list_regions()
    bench_metas = list_benchmarks()

    symbols: list[str] = []
    for m in region_metas + bench_metas:
        for s in m.get("basket") or [m["etf"]]:
            if s not in symbols:
                symbols.append(s)
        if m.get("hedged") and m["hedged"] not in symbols:
            symbols.append(m["hedged"])

    bundles = _fetch_all(symbols, as_of, max_workers)

    regions: list[FundFlowRegionResult] = []
    benchmarks: list[FundFlowRegionResult] = []
    failed: list[dict] = []
    series_by_region: dict[str, RegionSeries] = {}

    for meta, is_bench in [(m, False) for m in region_metas] + [(m, True) for m in bench_metas]:
        try:
            basket = meta.get("basket") or [meta["etf"]]
            present = [bundles[s] for s in basket if s in bundles]
            rs = aggregate_basket(present, meta["region"]) if present else None
            if rs is not None:
                series_by_region[meta["region"]] = rs
            row = _build_region(meta, bundles, is_bench)
            if row is None:
                failed.append({"region": meta["region"], "reason": "no_usable_history"})
                continue
            (benchmarks if is_bench else regions).append(row)
        except Exception as exc:
            logger.exception("fundflow: region %s failed: %s", meta.get("region"), exc)
            failed.append({"region": meta.get("region"), "reason": f"{type(exc).__name__}: {exc}"})

    world = next((b for b in benchmarks if b.region == "WORLD"), None)
    _apply_rotation(regions, series_by_region, world, series_by_region.get("WORLD"))

    # Strongest flow signal first, either direction; stronger relative flow
    # breaks ties, so two regions on the same composite are separated by which
    # one is actually winning share from the rest of the world.
    regions.sort(key=lambda r: (-abs(r.composite), -(r.rel_flow_z or 0.0), r.region))

    inflow = sum(1 for r in regions if r.direction == "INFLOW")
    outflow = sum(1 for r in regions if r.direction == "OUTFLOW")

    # Compute the facts first, then let DeepSeek write them up. The narrator
    # falls back to this draft on any failure, so the summary panel is never
    # empty and never depends on the model being reachable.
    summary = build_summary(regions, benchmarks)
    if narrate_summary and regions:
        summary = narrate(summary, regions, benchmarks)

    cohort = FundFlowCohortResult(
        run_id=run_id or uuid.uuid4().hex[:12],
        created_at=datetime.now(timezone.utc).isoformat(),
        as_of=as_of,
        region_count=len(regions),
        inflow_count=inflow,
        outflow_count=outflow,
        summary=summary,
        regions=regions,
        benchmarks=benchmarks,
        failed_regions=failed,
    )

    if save:
        try:
            from app.backend.services.fundflow_storage import save_fundflow_run
            save_fundflow_run(cohort)
        except Exception as exc:
            logger.warning("fundflow: persistence skipped — %s", exc)

    return cohort


if __name__ == "__main__":
    import sys

    from dotenv import load_dotenv

    # Standalone CLI use — the FastAPI app loads these at startup, but running
    # this module directly would otherwise hit FMP with an empty key.
    load_dotenv(override=True)
    load_dotenv(".env.local", override=True)

    # Region labels carry flag emoji; a Windows console defaults to cp1252 and
    # would raise UnicodeEncodeError on the first summary line.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    as_of_arg = sys.argv[1] if len(sys.argv) > 1 else None
    r = run_fundflow(as_of=as_of_arg, max_workers=8, save=False)

    print(f"\nFund flow run_id={r.run_id}  as_of={r.as_of or 'live'}")
    print(f"regions={r.region_count}  inflow={r.inflow_count}  outflow={r.outflow_count}  failed={len(r.failed_regions)}")

    if r.summary:
        print(f"\n[summary via {r.summary.summary_source}"
              + (f" / {r.summary.model_used}" if r.summary.model_used else "") + "]")
        print(f"\n{r.summary.headline}")
        print(f"Regime: {r.summary.regime}")
        for title, items in (("Key flows", r.summary.key_flows),
                             ("Key changes", r.summary.key_changes),
                             ("Implications", r.summary.implications),
                             ("Watch", r.summary.watch_items)):
            if items:
                print(f"\n-- {title} --")
                for it in items:
                    print(f"  • {it}")

    print("\n-- Region flow map --")
    hdr = (f"{'REG':<5}{'LABEL':<18}{'VERDICT':<22}{'COMP':>6}{'P/T/A':>10}"
           f"{'PRESS':>8}{'REL':>7}{'ROT':>7}{'IMPL 1M':>10}{'%AUM':>8}{'DIVERG':>13}")
    print(hdr)
    for x in r.regions:
        imp = f"${x.implied_flow_21d / 1e9:+.2f}B" if x.implied_flow_21d is not None else "n/a"
        pct = f"{x.implied_flow_21d_pct_aum * 100:+.2f}%" if x.implied_flow_21d_pct_aum is not None else "n/a"
        pta = f"{x.pressure_score:+.0f}/{x.turn_score:+.0f}/{x.accel_score:+.0f}"
        prs = f"{x.cmf_z_21:+.2f}" if x.cmf_z_21 is not None else "n/a"
        rel = f"{x.rel_flow_z:+.2f}" if x.rel_flow_z is not None else "n/a"
        rot = f"{x.rel_flow_z_delta:+.2f}" if x.rel_flow_z_delta is not None else "n/a"
        print(f"{x.region:<5}{x.label:<18}{x.verdict:<22}{x.composite:>+6.0f}{pta:>10}"
              f"{prs:>8}{rel:>7}{rot:>7}{imp:>10}{pct:>8}{(x.divergence or '—'):>13}")

    print("\n-- Benchmarks --")
    for x in r.benchmarks:
        imp = f"${x.implied_flow_21d / 1e9:+.2f}B" if x.implied_flow_21d is not None else "n/a"
        print(f"  {x.region:<8}{x.label:<22}{x.verdict:<22}comp={x.composite:+.0f}  "
              f"press={(f'{x.cmf_z_21:+.2f}' if x.cmf_z_21 is not None else 'n/a'):>6}  implied 1m={imp}")

    if r.failed_regions:
        print(f"\nfailed: {r.failed_regions}")
