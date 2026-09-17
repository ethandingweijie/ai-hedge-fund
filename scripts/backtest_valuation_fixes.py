"""Backward tests for the valuation-engine hardening items (plan: Backward tests).

The golden suite answers **"did the numbers move?"** This answers **"were the
moves right?"** Both are needed: the reinvestment charge passed the suite and
failed its backward test at 19.1pp of error against 4.4pp for legacy.

Each item in Phases 1-2 gets a row here. Running an item scores the SHIPPED
gate against ground truth that is already on file, and the acceptance result
decides whether the gate goes live (`applied: True`) or ships observation-only
(`applied: False`) and waits for the forward test.

    python scripts/backtest_valuation_fixes.py                 # every item
    python scripts/backtest_valuation_fixes.py --item 1.2A
    python scripts/backtest_valuation_fixes.py --item 1.2A --show-rows
    python scripts/backtest_valuation_fixes.py --as-of 2026-09-17

Results are written to `scratchpad/backtest_<item>.json` (gitignored: they
embed live-feed figures). The summary table and the accept/reject line are what
go into the commit message and the golden CHANGELOG entry.

**On `--as-of`.** Only items whose ground truth is a REPORTED figure can be
replayed at a past date, and only once the history exists: the live feed caps at
5 annual rows, so an as-of date more than ~4 years back has nothing to score
against. That is what Phase 3's EDGAR back-fill buys. Items that need it are
marked below rather than quietly scoring on a two-date sample and reporting a
pass.
"""
from __future__ import annotations

import argparse
import json
import sys
import textwrap
from pathlib import Path
from typing import Any, Callable, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env.local", override=True)
load_dotenv(ROOT / ".env")

OUT_DIR = ROOT / "scratchpad"


# ── Item registry ───────────────────────────────────────────────────────────

def _run_1_2a(as_of: str, **_kw) -> dict[str, Any]:
    from src.memory.gate_backtest import backtest_balance_sheet_financial
    return backtest_balance_sheet_financial(as_of=as_of)


def _run_1_1(as_of: str, **_kw) -> dict[str, Any]:
    """Growth CAGR divergence + convergence fade.

    PENDING. The plan's row scores the faded schedule against realised revenue
    CAGR over the FOLLOWING years, which needs 8+ as-of dates per ticker to mean
    anything; the live feed supplies 1-2. Phase 3's EDGAR back-fill is the
    prerequisite, and until it lands this reports pending rather than passing on
    a two-date sample.

    Recorded here, and not merely omitted, because Phase 1.1 shipped
    `applied: True` on the owner's explicit specification of the bound — the
    gate is live and its backward test is not done. A registry that silently
    lacked the row would let that stay invisible.
    """
    return {
        "gate_id": "GATE_GROWTH_CAGR_DIVERGENCE",
        "as_of": as_of, "metric": "realised_revenue_cagr",
        "status": "pending",
        "accepted": None,
        "shipped_applied": True,
        "pending_reason": (
            "needs Phase 3 (scripts/ingest_edgar_history.py): the live feed "
            "caps history at 5 annual rows, giving 1-2 as-of dates per ticker "
            "against the 8+ the plan's sample column asks for. The gate is "
            "LIVE with applied=True on the owner's specification of the bound; "
            "this row is the outstanding obligation."),
    }


def _run_1_2b(as_of: str, **_kw) -> dict[str, Any]:
    """Cyclical peak-consensus routing to mid-cycle legs and P/B-ROE.

    PENDING, and shipped observation-only (`applied: False`) *because* it is
    pending — unlike 1.1, which shipped live on the owner's specification of the
    bound and carries its backward test as an outstanding obligation.

    Two things block scoring. The plan's row is MU FY2018 and FY2022, FCX 2021,
    NUE 2021, scored against realised price twelve months on; the live feed caps
    at five annual rows so none of those dates exist yet, and Phase 3's EDGAR
    back-fill is the prerequisite. Separately, historical CONSENSUS is not
    archived anywhere, so even with the history the trigger's input has to be
    proxied by realised next-year EPS — a perfect-foresight stand-in that
    *favours* path A, which is what makes the eventual test conservative rather
    than merely approximate.

    What could be measured now is recorded, because "the trigger discriminates"
    is a different claim from "the trigger is right" and only the first is
    available: on the golden fixtures MU fires both arms and FCX fires neither,
    separated by a factor of twenty on the same line rather than by a threshold
    tuned to land between them. MU's forward EPS of $156.08 is 10.1x its
    2x-max line ($15.48, from a five-year diluted max of $7.74) and 11.0x its
    mean-plus-two-sigma line ($14.25); FCX's $2.95 is 0.51x and 0.88x of the
    equivalent lines ($5.80 and $3.33). One name at a cycle top and one that is
    not.

    The figures are DILUTED EPS, because that is what the engine computes:
    `shares_outstanding` on a LineItem maps to FMP's `weightedAverageShsOutDil`.
    That is the consistent side to compare against - analyst consensus EPS is
    diluted too - but it means the basic-share numbers (MU's FY22 $7.81, FY25
    $7.65) give a 2x-max line of $15.62, not the $15.48 the run publishes.
    `tests/test_cyclical_peak_consensus.py` pins the published lines so the two
    cannot drift apart unnoticed.

    None of this says whether the substitution improves the estimate, which is
    what scoring is for.
    """
    return {
        "gate_id": "GATE_CYCLICAL_PEAK_CONSENSUS",
        "as_of": as_of, "metric": "realised_price_12m",
        "status": "pending",
        "accepted": None,
        "shipped_applied": False,
        "pending_reason": (
            "needs Phase 3 (scripts/ingest_edgar_history.py) for the as-of dates "
            "the plan names - MU FY2018/FY2022, FCX 2021, NUE 2021 - and needs a "
            "proxy for historical consensus, which is not archived at all. "
            "Shipped OBSERVATION-ONLY (applied=False) rather than live: the "
            "shipping rule requires >=10 scoreable firings and this row has zero. "
            "Both paths are recorded per run so the forward test can score it "
            "without a replay."),
        "discrimination_observed": {
            "MU": {"fired": True, "arms": ["multiple-of-max", "mean-plus-sigma"],
                   "eps_forward": 156.08, "max_line": 15.48, "sigma_line": 14.25,
                   "eps_max_5y": 7.74},
            "FCX": {"fired": False, "arms": [],
                    "eps_forward": 2.95, "max_line": 5.80, "sigma_line": 3.33,
                    "eps_max_5y": 2.90},
            "note": ("golden-fixture inputs, not live feed; DILUTED EPS, matching "
                     "what the engine computes and what consensus EPS is quoted "
                     "on. Shows the two arms separate a cycle top from an "
                     "ordinary year by a wide margin; says nothing about whether "
                     "the substitution improves the estimate, which is what "
                     "scoring is for."),
        },
    }


# ── 1.3: deterministic KPIs override extracted ones ─────────────────────────
#
# The plan's backward-test row scores the override against a filed reference.
# This item is different from 1.1/1.2 in a way the row has to say out loud: the
# defect is not that the extracted KPI is *wrong*, it is that it is *not
# reproducible*. Alibaba's GAAP operating margin came back +10.0% for 09988.HK
# and −0.3% for BABA seven minutes apart on the same filing. A single number
# cannot be scored against ground truth when two runs of the same input produce
# two different numbers — so this splits into two legs and only one of them is
# an accuracy score.
#
#   LEG A (stability, pre-fix). Read-only against production `web_runs`: for
#     pairs of runs of the same ticker less than 24h apart, how often did an
#     eligible KPI change value? No annual filing can land inside a day, so
#     every such change is the extractor, not the company. This measures the
#     size of what the fix removes. It cannot be re-measured post-fix — the fix
#     is what makes the measurement come out zero — so the post-fix assertion is
#     a FORWARD-test obligation, recorded as such rather than claimed here.
#
#   LEG B (accuracy, post-fix). For each (ticker, KPI) where the extracted
#     value, the deterministic value and an FMP reference all exist, score
#     `delta_error_verdict(path_a=extracted, path_b=deterministic,
#     actual=reference)`. Path A is the ungated input, path B the intervention,
#     exactly as the other rows use it.
#
# Leg B's reference is TTM and the deterministic value is LATEST ANNUAL, because
# the series is what the DCF projects from (see `compute_from_series`). The two
# bases differ by however far the fiscal year end is from today, and that
# difference is charged to BOTH paths equally — the extracted value is quoted
# against the same reference — so the comparison stays fair while the absolute
# errors stay inflated. A name that reported last week scores near zero on
# both; a name mid-year scores high on both. The verdict is about which is
# closer, not about whether either is exact.
#
# Acceptance, mapped from the plan's and stated rather than assumed:
#
#   plan: hit-rate ≥ 0.50        → (HELPED + NEUTRAL) / scored ≥ 0.50
#   plan: MAE no worse than      → mean relative error of the deterministic
#                                  value ≤ the extracted one's
#   plan: ≥10 scoreable firings  → ≥10 (ticker, KPI) pairs with all three
#                                  values present
#
# Leg A carries no bar. It is the defect's own evidence and it is reported so
# the size of the removed nondeterminism is a number in the commit message.

#: Relative-error tolerance for a KPI ratio. `delta_error_verdict` divides by
#: the reference, so 0.05 is 5% OF the ratio — on a 15% ROIC that is 0.75pp.
_KPI_METRIC = "kpi_ratio"

#: Leg B costs ~6 FMP calls per ticker (the reference set plus the series) and
#: runs through the shared free-tier token bucket, so the sample is capped. It
#: is chosen by how many eligible KPIs the run persisted, not alphabetically:
#: a ticker with one persisted KPI cannot contribute a score.
_LEG_B_MAX_TICKERS = 10

#: Leg A pairs wider than this cannot be attributed to the extractor, because a
#: filing could have landed in between. The documented incident is 7 minutes.
_LEG_A_MAX_GAP_HOURS = 24


def _prod_dsn() -> str:
    """Read-only production DSN. Same host/port swap as prod_verify_gate.py."""
    import os
    import re
    path = os.path.expanduser("~/.railway_pg_url")
    with open(path, encoding="utf-8") as fh:
        raw = fh.read().strip()
    return re.sub(r"@[^/]+/", "@tokaido.proxy.rlwy.net:25751/", raw)


def _parse_run_at(value: Any) -> Optional[Any]:
    """`web_runs.run_at` is TEXT, and not even uniformly formatted.

    Some rows carry microseconds (`2026-09-16T22:40:32.002272`) and some do not
    (`2026-08-24T17:27:53`); the column is also written naive-local by
    run_archive and aware-UTC by analysis_service. All of that makes SQL date
    arithmetic on it impossible — `MAX(run_at) - INTERVAL '14 days'` raises
    `operator does not exist: text - interval` — and makes a wall-clock filter
    unreliable even if it parsed. So the window and the pair gap are both
    computed here, on the string the row actually holds.
    """
    from datetime import datetime
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _prod_run_index(days: int = 14) -> list[dict]:
    """Run metadata for the last `days`, oldest first within a ticker.

    Metadata only — no payload. 46 archived runs is small, but their
    `full_result_json` is not, and leg B needs the newest row of a handful of
    tickers while leg A needs adjacent pairs, so the payloads are fetched
    separately for exactly the rows the two legs select.
    """
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(_prod_dsn(), row_factory=dict_row,
                         connect_timeout=25) as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT ticker, run_at, profile_name, sector
            FROM web_runs
            WHERE COALESCE(is_checkpoint, 0) = 0
              AND full_result_json IS NOT NULL
            ORDER BY ticker, run_at
        """)
        raw = cur.fetchall()
    stamped = [(r, _parse_run_at(r["run_at"])) for r in raw]
    newest = max((ts for _, ts in stamped if ts is not None), default=None)
    if newest is None:
        return []
    from datetime import timedelta
    cutoff = newest - timedelta(days=days)
    return [r for r, ts in stamped if ts is not None and ts >= cutoff]


def _prod_payloads(picks: list[tuple[str, str]]) -> dict[tuple[str, str], dict]:
    """The archived `framework_metrics_all[ticker]` for exactly these runs.

    Parses and discards each payload inside the loop rather than returning the
    rows: a run's `full_result_json` is hundreds of kilobytes and this fetches
    dozens of them, while both legs want one small dict out of each.
    """
    if not picks:
        return {}
    import psycopg
    from psycopg.rows import dict_row

    out: dict[tuple[str, str], dict] = {}
    with psycopg.connect(_prod_dsn(), row_factory=dict_row,
                         connect_timeout=25) as conn:
        cur = conn.cursor()
        for ticker, run_at in picks:
            cur.execute("""
                SELECT full_result_json FROM web_runs
                WHERE ticker = %s AND run_at = %s
                  AND COALESCE(is_checkpoint, 0) = 0
                LIMIT 1
            """, (ticker, run_at))
            row = cur.fetchone()
            if not row:
                continue
            try:
                data = (json.loads(row["full_result_json"] or "{}")).get("data") or {}
            except (ValueError, TypeError):
                continue
            metrics = (data.get("framework_metrics_all") or {}).get(ticker)
            out[(ticker, run_at)] = metrics if isinstance(metrics, dict) else {}
    return out


def _reference_symbol(ticker: str) -> str:
    """The symbol form FMP's TTM endpoints actually answer to.

    Measured 2026-09-17 against /stable: `09988.HK` returns **zero** records on
    both `key-metrics-ttm` and `ratios-ttm`, while `9988.HK` returns one, with
    `operatingProfitMarginTTM = 0.042081`. `00700.HK` returns zero on both and
    so does `700.HK` — Tencent has no TTM ratio rows on this plan at all, which
    is a coverage gap rather than a symbol gap.

    Production does NOT do this. `_fmp_risk_kpis` passes the ticker through as
    archived, so for every HK name stored zero-padded the FMP gap-fill silently
    returns {} and the risk and quality multipliers run on extractor-only
    values. That is a separate defect from the one this row scores; it is noted
    here because leg B has to work around it to get a reference at all, and
    working around it means leg B's reference is better-sourced than the live
    gap-fill. The workaround is confined to this script.
    """
    if ticker.endswith(".HK"):
        stem, _, suffix = ticker.partition(".")
        return f"{stem.lstrip('0') or '0'}.{suffix}"
    return ticker


def _reference_kpis(ticker: str) -> dict:
    """FMP's own arithmetic on the same filed statements — the leg-B reference.

    Reuses `_fmp_risk_kpis`, which already implements the mapping the framework
    documents (operating margin, gross margin, FCF margin, capex intensity,
    revenue growth) rather than restating it here where the two could drift.
    `roic_pct` is added because that function does not supply it — and roic is
    the KPI 1.3 flipped to `extractor_only: False` for, since nothing else was
    computing it and the extractor returned 1.09% for Keppel on accounts that do
    not support it.
    """
    from src.data.sector_kpi_framework import _fmp_risk_kpis
    from src.tools.api import _STABLE, _fmp_get

    symbol = _reference_symbol(ticker)
    ref: dict = {k: v for k, v in (_fmp_risk_kpis(symbol) or {}).items()}
    try:
        km = _fmp_get(f"{_STABLE}/key-metrics-ttm", {"symbol": symbol},
                      api_key=None)
        row = (km or [{}])[0] if isinstance(km, list) else (km or {})
        roic = row.get("roicTTM") if isinstance(row, dict) else None
        if roic is not None:
            ref["roic_pct"] = float(roic)
    except Exception:
        pass  # an absent reference just drops roic from leg B
    return ref


def _prod_annual_line_items() -> list[str]:
    """The line items production actually requests, read out of its own source.

    Deliberately not a copy. `gate_backtest._LINE_ITEMS` is a nine-item subset
    built for the 1.1 and 1.2B rows, and scoring 1.3 against it under-measured
    the fix: no `ebit` or `operating_income`, so no `operating_margin_pct`; no
    `gross_profit`, so no `gross_margin_pct`; no `inventory` or
    `property_plant_equipment`, so the ROIC operating floor had nothing to stand
    on. The drop table that produced read as a coverage limit of
    `compute_from_series` when it was a hole in the harness — and the KPI it
    cost is `operating_margin_pct`, the exact key leg A caught drifting on
    09988.HK.

    `tests/test_line_items_requested_are_read.py:52-54` extracts the same list
    with the same regex and asserts it stays a literal inside the call, so this
    cannot drift silently in either direction: hoist the literal and both break.
    """
    import inspect
    import re

    from src.agents.analysis import dcf_agent as _d

    src = inspect.getsource(_d.run_dcf_agent)
    m = re.search(r"search_line_items\(\s*ticker,\s*\[(.*?)\],\s*end_date",
                  src, re.S)
    if not m:
        raise RuntimeError(
            "production search_line_items request list not found in "
            "run_dcf_agent; if it was hoisted to a module constant, point this "
            "helper at the constant instead of copying a list here")
    # The literal carries explanatory comments between items; strip them before
    # splitting. No requested key contains '#' or ','.
    body = "\n".join(line.split("#", 1)[0] for line in m.group(1).splitlines())
    return [t.strip().strip("'\"") for t in body.split(",") if t.strip().strip("'\"")]


def _series_prod(ticker: str, as_of: str) -> list[dict]:
    """The same annual series `run_dcf_agent` builds, through the same two calls.

    Same request list, same `period="annual"`, same `limit=7`, same
    `_extract_annual_series` row builder — so a value this row scores is a value
    production could have written, not one a thinner request happened to allow.
    """
    from src.agents.analysis.dcf_agent import _extract_annual_series
    from src.tools.api import search_line_items

    li = search_line_items(ticker, _prod_annual_line_items(), as_of,
                           period="annual", limit=7)
    rows, _ccy = _extract_annual_series(li or [])
    return rows


def _run_1_3(as_of: str, **_kw) -> dict[str, Any]:
    from src.data.deterministic_kpis import (
        compute_from_series, eligible_kpi_keys,
    )
    from src.memory.gate_backtest import delta_error_verdict

    reject_reasons: list[str] = []
    rows: list[dict] = []
    leg_a = {"pairs": 0, "drifted_pairs": 0, "compared": 0, "drifted": 0,
             "examples": [],
             # Carried in the JSON as well as the log, because the forward test
             # is written off this row and the unqualified claim ("post-fix this
             # reads zero on eligible keys") is false for the one key that
             # drifted. `operating_margin_pct` is declared eligible by the
             # profile but is not written by `compute_from_series`, so the
             # extractor still supplies it and the drift persists.
             "post_fix_scope": (
                 "zero-drift is asserted only for keys in "
                 "`_deterministic_kpis.computed`; NOT for `operating_margin_pct`, "
                 "which this pass declines (FMP's `ebit` is not operating income "
                 "and `operating_income` is unrequested) — that drift stays live "
                 "until the KNOWN GAP is closed")}

    try:
        index = _prod_run_index()
    except Exception as exc:
        index = []
        reject_reasons.append(
            f"production web_runs unreadable ({type(exc).__name__}: "
            f"{str(exc)[:80]}); neither leg measured")
    by_ticker: dict[str, list[dict]] = {}
    for r in index:
        by_ticker.setdefault(r["ticker"], []).append(r)

    # Select every row both legs need BEFORE fetching a single payload: leg A
    # wants adjacent runs of one ticker inside the gap, leg B wants the newest
    # run per ticker. The selection is metadata-only, so the payload fetch is
    # one bounded round trip rather than a read of the whole archive.
    from datetime import timedelta
    pairs: list[tuple[str, dict, dict, Any]] = []
    for ticker, runs in sorted(by_ticker.items()):
        profile = next((r.get("profile_name") for r in reversed(runs)
                        if r.get("profile_name")), None)
        if not profile or not eligible_kpi_keys(profile):
            continue
        for older, newer in zip(runs[-3:], runs[-2:]):
            o_ts = _parse_run_at(older["run_at"])
            n_ts = _parse_run_at(newer["run_at"])
            if o_ts is None or n_ts is None:
                continue
            gap = n_ts - o_ts
            if gap <= timedelta(0) or gap > timedelta(hours=_LEG_A_MAX_GAP_HOURS):
                continue
            pairs.append((ticker, older, newer, gap))

    picks = {(r["ticker"], r["run_at"]) for r in
             (t[-1] for t in by_ticker.values())}
    picks |= {(r["ticker"], r["run_at"])
              for _t, older, newer, _g in pairs for r in (older, newer)}
    try:
        payloads = _prod_payloads(sorted(picks))
    except Exception as exc:
        payloads = {}
        reject_reasons.append(
            f"archived payloads unreadable ({type(exc).__name__}: "
            f"{str(exc)[:80]})")

    # ── Leg A: pre-fix drift on runs of the same ticker inside one day ──────
    for ticker, older, newer, gap in pairs:
        profile = newer.get("profile_name") or older.get("profile_name")
        eligible = eligible_kpi_keys(profile)
        a_metrics = payloads.get((ticker, older["run_at"])) or {}
        b_metrics = payloads.get((ticker, newer["run_at"])) or {}
        changed: list[str] = []
        for key in sorted(eligible):
            av, bv = a_metrics.get(key), b_metrics.get(key)
            if av is None or bv is None:
                continue
            leg_a["compared"] += 1
            try:
                same = abs(float(av) - float(bv)) <= max(
                    1e-9, abs(float(av)) * 0.01)
            except (TypeError, ValueError):
                continue
            if not same:
                changed.append(key)
                leg_a["drifted"] += 1
        leg_a["pairs"] += 1
        if changed:
            leg_a["drifted_pairs"] += 1
            if len(leg_a["examples"]) < 6:
                leg_a["examples"].append({
                    "ticker": ticker, "profile": profile,
                    "gap_minutes": round(gap.total_seconds() / 60, 1),
                    "changed": {
                        k: {"older": a_metrics.get(k),
                            "newer": b_metrics.get(k)} for k in changed},
                })

    # ── Leg B: deterministic vs extracted, both against an FMP reference ────
    scored = helped = neutral = false_alarm = 0
    fills = 0
    fill_rows: list[dict] = []
    err_a_sum = err_b_sum = 0.0
    # Per-ticker accounting of what dropped and why. The row reports an
    # under-sampled leg, so the claim "the intersection is structurally small"
    # needs to be checkable rather than asserted — three candidate tickers were
    # fetched and produced no rows, and the reason is different in each case.
    drops: list[dict] = []
    candidates = []
    for ticker, runs in sorted(by_ticker.items()):
        newest = runs[-1]
        profile = newest.get("profile_name")
        if not profile:
            continue
        eligible = eligible_kpi_keys(profile)
        metrics = payloads.get((ticker, newest["run_at"])) or {}
        persisted = sorted(k for k in eligible if metrics.get(k) is not None)
        if persisted:
            candidates.append((len(persisted), ticker, profile, persisted))
    # Most-informative first: a ticker that persisted one eligible KPI cannot
    # reach the ≥10-pair bar on its own, and the cap is an API-budget cap.
    candidates.sort(key=lambda c: (-c[0], c[1]))

    for _n, ticker, profile, keys in candidates[:_LEG_B_MAX_TICKERS]:
        try:
            det, basis = compute_from_series(_series_prod(ticker, as_of))
        except Exception as exc:
            reject_reasons.append(f"{ticker}: series unavailable "
                                  f"({type(exc).__name__})")
            continue
        ref = _reference_kpis(ticker)
        metrics = payloads.get((ticker, by_ticker[ticker][-1]["run_at"])) or {}
        d_row = {"ticker": ticker, "profile": profile, "eligible": len(keys),
                 "not_computed": [], "no_reference": [], "scored": 0,
                 "fills": 0, "basis": (basis or {}).get("basis"),
                 "period": (basis or {}).get("period")}
        drops.append(d_row)
        for key in keys:
            actual = ref.get(key)
            path_a = metrics.get(key)          # extracted (ungated)
            path_b = det.get(key)              # deterministic (intervention)
            # Attributed in both directions: a key can fail either test, and
            # "the arithmetic could not derive it" is a different finding from
            # "FMP publishes no TTM reference for it".
            if path_b is None:
                d_row["not_computed"].append(key)
                if actual is None:
                    d_row["no_reference"].append(key)
                continue
            if actual is None:
                d_row["no_reference"].append(key)
                continue
            if path_a is None:
                # A FILL, not a reversal: the extractor left a gap and the
                # arithmetic supplied a value. `delta_error_verdict` scores an
                # intervention against the ungated path, so it cannot score a
                # fill — but fills are most of what this fix does, and dropping
                # them silently would make the row look like the override
                # barely fires. Counted and reported separately.
                fills += 1
                d_row["fills"] += 1
                fill_rows.append({
                    "ticker": ticker, "profile": profile, "kpi": key,
                    "period": (basis or {}).get("period"),
                    "deterministic": path_b, "reference": actual,
                    "reference_gap_pct": (
                        round((path_b - actual) / abs(actual), 4)
                        if actual else None),
                })
                continue
            v = delta_error_verdict(path_a, path_b, actual, metric=_KPI_METRIC)
            if v["verdict"] == "UNSCORABLE":
                continue
            scored += 1
            d_row["scored"] += 1
            err_a_sum += v.get("error_a_pct") or 0.0
            err_b_sum += v.get("error_b_pct") or 0.0
            if v["verdict"] == "HELPED":
                helped += 1
            elif v["verdict"] == "NEUTRAL":
                neutral += 1
            else:
                false_alarm += 1
            rows.append({"ticker": ticker, "profile": profile, "kpi": key,
                         "period": (basis or {}).get("period"),
                         "extracted": path_a, "deterministic": path_b,
                         "reference": actual, "verdict": v["verdict"],
                         "delta_error_pct": v["delta_error_pct"]})

    hit_rate = ((helped + neutral) / scored) if scored else None
    mae_a = (err_a_sum / scored) if scored else None
    mae_b = (err_b_sum / scored) if scored else None

    if scored < 10:
        # NO DATA, not a rejection. The sample is small for a structural reason
        # the drop table below makes checkable rather than asserted: leg B can
        # only score a KPI that (a) the profile marks `extractor_only: False`,
        # (b) `compute_from_series` can derive from a filed statement, (c) the
        # extractor actually returned a value for, and (d) FMP publishes a TTM
        # reference for. Condition (b) is the binding one — four of the keys
        # these seven tickers declare (`net_debt_to_ebitda`, `debt_to_ebitda`,
        # `equity_to_assets_pct`, `operating_margin_pct`) are outside the seven
        # families the arithmetic derives, so they can never be written however
        # wide the window is made. A REJECT verdict would read as "the override
        # was scored and lost"; it was not scored.
        accepted = None
        reject_reasons.append(
            f"only {scored} scoreable (ticker, KPI) pairs across "
            f"{len({r['ticker'] for r in rows})} ticker(s) against the ≥10 bar "
            f"(plus {fills} fills, which no delta-error test can score); the "
            f"binding constraint is that most keys these profiles declare "
            f"`extractor_only: False` are outside the seven families "
            f"`compute_from_series` derives — see the drop table")
    else:
        accepted = bool(hit_rate is not None and hit_rate >= 0.50
                        and mae_a is not None and mae_b is not None
                        and mae_b <= mae_a)

    return {
        "gate_id": "DETERMINISTIC_KPI_OVERRIDE",
        "as_of": as_of, "metric": "kpi_vs_fmp_reference",
        "status": "pending" if accepted is None else "scored",
        "accepted": accepted,
        # Shipped LIVE with the accuracy leg unscored, the same posture as 1.1
        # and for a different reason than 1.2B. The reason has to be stated with
        # its scope attached, because part of the evidence in this row does NOT
        # support it.
        #
        # What does: for every key this pass writes, the "do nothing" arm is a
        # number an LLM quoted out of a research report, and the alternative is
        # arithmetic on the filed statement. Leg B measures the quoted values
        # 65.4% off the reference on average and the computed ones 23.9% off,
        # with 2 of 9 pairs helped and only 1 false alarm — and that false alarm
        # is a basis mismatch (latest annual vs TTM), verified below rather than
        # waved off. There is no case for preferring the quote.
        #
        # What does not: leg A caught `operating_margin_pct` moving from +10.0%
        # to −0.3% on one filing inside 109 minutes, and this pass now DECLINES
        # to write that key (see the KNOWN GAP note in
        # deterministic_kpis.compute_from_series — FMP's `ebit` is not operating
        # income, and `operating_income` is unrequested). So leg A is not
        # evidence that shipping removes a drift. It is evidence that a drift it
        # found is still live. Recorded here as a caveat rather than cited as
        # justification, because the first draft of this row did the second.
        "shipped_applied": True,
        "ship_state_reason": (
            "LIVE (applied=True) with leg B under-sampled on the pair count "
            "only: 9 scoreable pairs against a ≥10 bar, with hit-rate 88.9% "
            "against 50% and mean relative error 65.4% → 23.9% against a "
            "'no worse' bar. Observation-only is not available for the keys "
            "this pass writes — the arm it would observe against is a value "
            "quoted out of a research report, and once the arithmetic is "
            "measurably closer to the reference there is no reason to prefer "
            "the quote. CAVEAT, not support: the one key leg A caught drifting "
            "(operating_margin_pct) is one this pass now declines to write, so "
            "that drift is still live in production and is not fixed by this "
            "change. The ≥10-pair obligation stays open, and what the change "
            "does to the fourteen golden fixtures is measured separately and "
            "named in tests/golden/CHANGELOG.md."),
        "reject_reasons": reject_reasons,
        "scored": scored, "helped": helped, "neutral": neutral,
        "false_alarm": false_alarm, "fills": fills,
        "hit_rate": hit_rate, "mae_extracted": mae_a, "mae_deterministic": mae_b,
        "bar": {"hit_rate": 0.50, "min_scored": 10,
                "mae": "deterministic <= extracted"},
        "leg_a_stability": leg_a,
        "rows": rows, "fill_rows": fill_rows, "drops": drops,
        "tickers_scored": len({r["ticker"] for r in rows}),
        # The pair count overstates the sample and saying so is the point:
        # 09988.HK and BABA are one issuer, and FMP serves both from the same
        # underlying financials, so their deterministic values are identical to
        # the last digit and their references differ only in rounding. Nine
        # pairs are six independent observations across two issuers.
        "sample_note": (
            "09988.HK and BABA are the same issuer served from the same FMP "
            "financials — identical deterministic values, references differing "
            "only in rounding — so the pair count is roughly double the "
            "independent evidence: nine pairs are about six observations across "
            "two issuers, not nine. Widening the window does not help; the "
            "archive holds 17 tickers in total. The per-ticker drop table above "
            "says why the other four contributed nothing, and it is worth "
            "reading the table rather than this sentence: every one of the seven "
            "candidates DOES declare at least one eligible KPI, so the shortfall "
            "is not profiles declaring nothing. It is the permission-vs-"
            "capability gap described next."),
        # A gap in what this row can measure, stated because the drop table
        # reads like a verdict on `compute_from_series` and is partly one on the
        # eligibility declaration instead. Nothing is broken — `apply_overrides`
        # intersects the two sets correctly — but a row that reports "9 of 12
        # eligible pairs scored" invites the wrong conclusion about which of the
        # two sides is short.
        "coverage_note": (
            "`eligible_kpi_keys` grants PERMISSION, not capability: it returns "
            "every key a profile marks `extractor_only: False`, while "
            "`compute_from_series` derives seven families (operating margin, "
            "gross margin, FCF margin, capex intensity, R&D intensity, revenue "
            "growth, ROIC). The first set is wider than the second, so a key can "
            "be declared overridable and never be written. Across these seven "
            "tickers that is four keys: `net_debt_to_ebitda` (09988.HK, BABA, "
            "ICE), `debt_to_ebitda` (U96.SI) and `equity_to_assets_pct` (SCHW) "
            "are outside the seven families altogether, and "
            "`operating_margin_pct` (all three HK names) is inside them but "
            "declined by design. Closing that last one means adding "
            "`operating_income` to the production request list — the KNOWN GAP "
            "`tests/test_line_items_requested_are_read.py` records — which also "
            "activates the dormant Priority-1 branch of the bank EBIT path "
            "(`operating_income + abs(provisions)`, dcf_agent.py:3272) and so "
            "moves every bank valuation. That is an owner decision with its own "
            "golden diff, not a field to slip into this change. Until then the "
            "operating-margin drift leg A found stays live."),
        "false_alarm_note": (
            "The one FALSE_ALARM is the documented basis mismatch, verified "
            "rather than assumed: JD's FY2025 free cash flow is ¥4.807bn on "
            "¥1,275.2bn of revenue = 0.377%, against 3.82% in FY2024, so the "
            "latest annual year genuinely is a trough. FMP's TTM reference "
            "spans Q4-2025..Q3-2026 and reads 2.43%. The deterministic value "
            "is arithmetically right for its own basis — and that basis is the "
            "one the DCF projects from, so the composite and the projection "
            "read the same year instead of a TTM margin scored against an "
            "FY-based model. The trough-year exposure belongs to the "
            "projection, which is where it was already visible."),
    }


#: item key -> (description, runner, plan's backward-test row)
ITEMS: dict[str, tuple[str, Callable[..., dict], str]] = {
    "1.1": (
        "Growth: flat g_ntm -> gated + faded schedule",
        _run_1_1,
        "realised revenue CAGR over following years; alpha profiles plus a "
        "non-alpha control; 1-2 dates/ticker now, 8+ after Phase 3",
    ),
    "1.2A": (
        "Balance-sheet financials lose EV/DCF/FCF legs",
        _run_1_2a,
        "classifier precision/recall on a hand-labelled set (~20 "
        "deposit/float-funded vs ~20 not)",
    ),
    "1.2B": (
        "Cyclicals: peak consensus -> mid-cycle legs / P/B-ROE",
        _run_1_2b,
        "realised price 12m later; MU FY2018 and FY2022, FCX 2021, NUE 2021; "
        "needs Phase 3, and realised next-year EPS stands in for consensus "
        "(a perfect-foresight proxy that favours path A)",
    ),
    "1.3": (
        "Deterministic KPIs override extracted ones",
        _run_1_3,
        "extracted vs computed against a filed reference, plus run-to-run "
        "stability on the same filing period; reference is TTM and the "
        "deterministic value is latest annual, a basis mismatch charged to "
        "both paths",
    ),
}


# ── Reporting ───────────────────────────────────────────────────────────────

def _fmt(v: Optional[float], pct: bool = True) -> str:
    if v is None:
        return "-"
    return f"{v:.1%}" if pct else f"{v:.4f}"


def _para(text: str, indent: str = "  ") -> None:
    """Print a wrapped, indented note.

    These notes are the part of the row that survives into the commit message,
    so they are written out in full rather than truncated to one line.
    """
    for line in textwrap.wrap(text.strip(), width=96 - len(indent),
                              initial_indent=indent, subsequent_indent=indent):
        print(line)


def print_summary(results: dict[str, dict], show_rows: bool) -> int:
    print()
    print("=" * 100)
    print(f"{'item':<6} {'gate':<34} {'metric':<28} {'result':<10} verdict")
    print("-" * 100)
    exit_code = 0
    for item, res in results.items():
        gid = res.get("gate_id", "?")
        metric = res.get("metric", "?")
        if res.get("status") == "pending":
            verdict = "PENDING"
            detail = {True: "LIVE - backward test owed",
                      False: "observation-only"}.get(
                          res.get("shipped_applied"), "ship state not recorded")
            exit_code = 1
        elif res.get("accepted"):
            verdict = "ACCEPT"
            detail = ""
        elif res.get("accepted") is None:
            verdict = "NO DATA"
            detail = "; ".join(res.get("reject_reasons") or [])
            exit_code = 1
        else:
            verdict = "REJECT"
            detail = "; ".join(res.get("reject_reasons") or [])
            exit_code = 1
        print(f"{item:<6} {gid:<34} {metric:<28} {verdict:<10} {detail}")
    print("=" * 100)

    for item, res in results.items():
        if res.get("status") == "pending":
            # `shipped_applied` distinguishes the two kinds of pending, and the
            # distinction is the whole point of the row: an item that shipped
            # LIVE with its test owed is an open risk in production, while an
            # item that shipped observation-only is inert until someone promotes
            # it. Reading both as "not done yet" hides which one needs chasing.
            shipped = res.get("shipped_applied")
            state = {True: "shipped LIVE (applied=True)",
                     False: "shipped OBSERVATION-ONLY (applied=False)"}.get(
                         shipped, "ship state not recorded")
            print(f"\n[{item}] PENDING — {state}")
            reason = res.get("pending_reason") or "; ".join(
                res.get("reject_reasons") or []) or "(no reason recorded)"
            print(f"    {reason}")
            if res.get("ship_state_reason"):
                print(f"    why it shipped in that state: {res['ship_state_reason']}")
            if res.get("discrimination_observed"):
                print("    measured while pending (not a score):")
                for t, d in res["discrimination_observed"].items():
                    if not isinstance(d, dict):
                        print(f"      note: {d}")
                        continue
                    print(f"      {t:<6} fired={str(d['fired']):<5} "
                          f"arms={'+'.join(d['arms']) or '-':<32} "
                          f"fwd EPS {d['eps_forward']:.2f} vs 2x-max "
                          f"{d['max_line']:.2f} / mean+2sig {d['sigma_line']:.2f}")
            if res.get("metric") != "kpi_vs_fmp_reference":
                continue
            # 1.3 falls through: its legs ARE the measurement, and hiding them
            # behind the pending header would leave the commit message with a
            # verdict and no numbers.
        if res.get("metric") == "kpi_vs_fmp_reference":
            a = res.get("leg_a_stability") or {}
            print(f"\n[{item}] LEG A — pre-fix drift, runs of one ticker "
                  f"inside {_LEG_A_MAX_GAP_HOURS}h (no filing can intervene):")
            if a.get("pairs"):
                print(f"  {a['drifted_pairs']}/{a['pairs']} run pairs changed at "
                      f"least one eligible KPI; {a['drifted']}/{a['compared']} "
                      f"individual KPI readings moved")
                for ex in a.get("examples") or []:
                    print(f"    {ex['ticker']:<10} {ex['profile'][:30]:<30} "
                          f"{ex['gap_minutes']:>7.1f} min apart")
                    for k, v in (ex.get("changed") or {}).items():
                        print(f"        {k:<26} {v['older']} -> {v['newer']}")
                # Scoped, because the unqualified version of this sentence was
                # wrong. The zero holds only for keys the deterministic pass
                # WRITES, and the single key leg A caught drifting is a key it
                # now declines to write.
                _para("Post-fix this reads zero for the keys the deterministic "
                      "pass writes. That is a forward-test assertion, not "
                      "something re-measurable here — removing the drift is "
                      "what the fix does. It does NOT read zero for "
                      "`operating_margin_pct`, which is the key that actually "
                      "drifted: this pass now declines it, because FMP's `ebit` "
                      "is a bottom-up plug rather than an operating income and "
                      "`operating_income` is unrequested (KNOWN GAP), so the "
                      "extractor keeps supplying the value and the drift stays "
                      "live. The forward test must therefore scope its "
                      "zero-drift assertion to `_deterministic_kpis.computed` "
                      "and not to the profile's eligible keys, or it will fail "
                      "on a drift this change never claimed to fix.")
            else:
                print("  no qualifying run pairs in production; leg A "
                      "unmeasured")
            print(f"\n[{item}] LEG B — deterministic vs extracted, both scored "
                  f"against the FMP reference:")
            if res.get("fills"):
                print(f"  {res['fills']} further (ticker, KPI) pair(s) were "
                      f"FILLS — the extractor had no value at all, so there is "
                      f"no ungated path to score against:")
                for r in (res.get("fill_rows") or [])[:8]:
                    gap = r.get("reference_gap_pct")
                    print(f"    {r['ticker']:<10} {r['kpi']:<24} "
                          f"computed {r['deterministic']:>10.4f}  reference "
                          f"{r['reference']:>10.4f}"
                          + (f"  ({gap:+.1%})" if gap is not None else ""))
            if res.get("scored"):
                print(f"  {res['scored']} (ticker, KPI) pairs scored across "
                      f"{res.get('tickers_scored', '?')} ticker(s): "
                      f"HELPED={res['helped']} NEUTRAL={res['neutral']} "
                      f"FALSE_ALARM={res['false_alarm']}")
                print(f"  hit-rate={_fmt(res['hit_rate'])} (bar "
                      f"{_fmt(res['bar']['hit_rate'])})   mean relative error "
                      f"extracted={_fmt(res['mae_extracted'])} -> "
                      f"deterministic={_fmt(res['mae_deterministic'])} "
                      f"(bar: no worse)")
                if show_rows:
                    print(f"\n  {'ticker':<10} {'kpi':<24} {'extracted':>11} "
                          f"{'determ':>11} {'reference':>11} {'delta':>9}  "
                          f"verdict")
                    for r in res["rows"]:
                        ea = "-" if r["extracted"] is None else f"{r['extracted']:.4f}"
                        print(f"  {r['ticker']:<10} {r['kpi']:<24} {ea:>11} "
                              f"{r['deterministic']:>11.4f} "
                              f"{r['reference']:>11.4f} "
                              f"{r['delta_error_pct']:>+9.1%}  {r['verdict']}")
                if res.get("drops"):
                    print(f"\n  every candidate that was fetched, and where its "
                          f"eligible KPIs went:")
                    print(f"  {'ticker':<10} {'profile':<32} {'eligible':>8} "
                          f"{'scored':>7} {'fills':>6} {'not_computed':>13} "
                          f"{'no_reference':>13}")
                    for d in res["drops"]:
                        print(f"  {d['ticker']:<10} {str(d['profile'])[:32]:<32} "
                              f"{d['eligible']:>8} {d['scored']:>7} "
                              f"{d['fills']:>6} {len(d['not_computed']):>13} "
                              f"{len(d['no_reference']):>13}")
                        # The names, not just the counts: "eligible" is what the
                        # profile declares, "not_computed" is what the arithmetic
                        # could not derive from the filed statements, and the
                        # gap between the two is a coverage limit of
                        # compute_from_series, not of the profile.
                        if d["not_computed"]:
                            print(f"      not derived from the statements: "
                                  f"{', '.join(d['not_computed'])}")
                        if d["no_reference"]:
                            print(f"      no FMP TTM reference: "
                                  f"{', '.join(d['no_reference'])}")
                for r in res["rows"]:
                    if r["verdict"] == "FALSE_ALARM":
                        print(f"\n  FALSE_ALARM {r['ticker']} {r['kpi']} "
                              f"(FY{str(r.get('period'))[:4]}): extracted "
                              f"{r['extracted']} was closer to the reference "
                              f"{r['reference']:.4f} than the computed "
                              f"{r['deterministic']:.4f}")
                if res.get("false_alarm") and res.get("false_alarm_note"):
                    _para(res["false_alarm_note"])
                if res.get("sample_note"):
                    _para(res["sample_note"])
                if res.get("coverage_note"):
                    _para(res["coverage_note"])
            else:
                print("  nothing scoreable")
            continue
        if res.get("metric") != "classifier_precision_recall":
            continue
        print(f"\n[{item}] confusion matrix over {res['scored']} scored labels "
              f"({res['no_data']} could not be measured):")
        print(f"  TP={res['tp']}  FP={res['fp']}  TN={res['tn']}  FN={res['fn']}"
              f"   firings={res['firings']}")
        print(f"  precision={_fmt(res['precision'])} (bar "
              f"{_fmt(res['bar']['precision'])})   "
              f"recall={_fmt(res['recall'])} (bar {_fmt(res['bar']['recall'])})")

        if show_rows:
            print(f"\n  {'ticker':<10} {'profile':<32} {'label':<6} {'fired':<6} "
                  f"{'ratio':>7}  outcome")
            for r in res["rows"]:
                ratio = r.get("customer_balance_ratio")
                ratio_s = "-" if ratio is None else f"{ratio:.4f}"
                print(f"  {r['ticker']:<10} {r['profile']:<32} "
                      f"{str(r['label']):<6} {str(r['fired']):<6} "
                      f"{ratio_s:>7}  {r['outcome']}")

        for r in res["rows"]:
            if r["outcome"] in ("FP", "FN"):
                print(f"\n  {r['outcome']} {r['ticker']} ({r['profile']}): "
                      f"label={r['label']} fired={r['fired']} "
                      f"ratio={r.get('customer_balance_ratio')}")
                if r.get("note"):
                    print(f"      note: {r['note']}")
            if r.get("routing_drift"):
                print(f"\n  ROUTING DRIFT {r['ticker']}: {r['routing_drift']}")
    return exit_code


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--item", action="append", choices=sorted(ITEMS),
                    help="run only these items (default: all)")
    ap.add_argument("--as-of", default="2026-09-17",
                    help="as-of date for the balance-sheet readings")
    ap.add_argument("--show-rows", action="store_true",
                    help="print every labelled row, not just the errors")
    ap.add_argument("--no-write", action="store_true",
                    help="do not write scratchpad/backtest_<item>.json")
    args = ap.parse_args(argv)

    wanted = args.item or sorted(ITEMS)
    results: dict[str, dict] = {}
    for item in wanted:
        desc, runner, _row = ITEMS[item]
        print(f"\n── {item}: {desc} " + "─" * max(0, 60 - len(desc)))
        res = runner(as_of=args.as_of)
        results[item] = res
        if not args.no_write:
            OUT_DIR.mkdir(parents=True, exist_ok=True)
            path = OUT_DIR / f"backtest_{item.replace('.', '_')}.json"
            path.write_text(json.dumps(res, indent=2, default=str),
                            encoding="utf-8")
            print(f"   wrote {path.relative_to(ROOT)}")

    return print_summary(results, args.show_rows)


if __name__ == "__main__":
    raise SystemExit(main())
