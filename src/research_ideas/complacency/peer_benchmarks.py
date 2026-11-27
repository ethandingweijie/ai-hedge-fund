"""
src/research_ideas/complacency/peer_benchmarks.py
==================================================
Cohort-level median benchmarks for computed financial signals.

The qualitative scorer's B1 / B2 / B3 indicators benefit hugely from "how
does the company compare to its 50-ticker peer group on the same metric?"
versus the bare absolute number. E.g.:

    Without peer context:  "gross margin = 65%"            (could be good or bad)
    With peer context:     "gross margin = 65% (peer median 78%, p25 70%)"
                           → immediately reads as below-cohort pressure

Implementation:

  • Computes peer medians lazily across the complacency universe (50 tickers).
  • Caches the result in the run archive (`peer_benchmarks` table; Postgres in
    production via src.data.db since 2026-11-27) with a 14-day TTL
    so per-ticker scoring doesn't re-fan-out the cohort.
  • Falls back to a static SOFTWARE_TECH_REFERENCE dict when the cache is
    cold and a live refresh would be too slow.

Metrics tracked (all sourced from compute_financial_signals):
  goodwill_to_equity, gross_margin_pct, dso_days, deferred_rev_to_revenue,
  revenue_cagr_3y, capex_to_revenue, cfo_to_ni_ratio

For B2 (competitive disintermediation) we don't compute a peer median —
that one stays prose-only via Tavily + 10-K — but we surface the peer
context for B1 (concentration ~= deferred-rev / customer-stickiness proxy)
and B3 (pricing-power = gross margin + DSO trend) where the signal is
directly comparable.
"""
from __future__ import annotations

import json
import logging
import statistics
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Optional

from src.data import db

logger = logging.getLogger(__name__)


# ─── Static reference (used when cache is cold) ───────────────────────────
# Hand-tuned defaults for large-cap US software/tech (Nov 2025).
# Provide rough medians so the LLM has SOMETHING to anchor against even
# before the cohort-refresh job has run.

SOFTWARE_TECH_REFERENCE: dict[str, dict[str, float]] = {
    "goodwill_to_equity":      {"median": 0.40, "p25": 0.10, "p75": 0.90},
    "intangibles_to_equity":   {"median": 0.15, "p25": 0.05, "p75": 0.35},
    "dso_days":                {"median": 75,   "p25": 55,   "p75": 95},
    "gross_margin_pct":        {"median": 0.72, "p25": 0.60, "p75": 0.80},
    "deferred_rev_to_revenue": {"median": 0.30, "p25": 0.15, "p75": 0.55},
    "revenue_cagr_3y":         {"median": 0.18, "p25": 0.08, "p75": 0.32},
    "capex_to_revenue":        {"median": 0.04, "p25": 0.02, "p75": 0.08},
    "cfo_to_ni_ratio":         {"median": 1.30, "p25": 1.05, "p75": 1.80},
}


# ─── Cache (shared run archive) ─────────────────────────────────────────────────────────


_DDL = """
CREATE TABLE IF NOT EXISTS peer_benchmarks (
    refreshed_at  TEXT NOT NULL,
    cohort_name   TEXT NOT NULL,
    metric        TEXT NOT NULL,
    median_value  REAL,
    p25_value     REAL,
    p75_value     REAL,
    sample_size   INTEGER NOT NULL,
    PRIMARY KEY (refreshed_at, cohort_name, metric)
)
"""


_ensured_for: Optional[str] = None
_ensure_lock = threading.Lock()


def _ensure_table() -> None:
    global _ensured_for
    target = "pg" if db.is_postgres() else db.get_db_path()
    if _ensured_for == target:
        return
    with _ensure_lock:
        if _ensured_for == target:
            return
        db.ensure_table(_DDL)
        _ensured_for = target


def _latest_refresh(cohort_name: str) -> Optional[str]:
    _ensure_table()
    row = db.query_one(
        "SELECT MAX(refreshed_at) AS latest FROM peer_benchmarks WHERE cohort_name = ?",
        [cohort_name],
    )
    return row["latest"] if row and row["latest"] else None


def _load_latest(cohort_name: str) -> dict[str, dict[str, float]]:
    """Returns {metric: {median, p25, p75, sample_size}} for latest refresh."""
    latest = _latest_refresh(cohort_name)
    if not latest:
        return {}
    rows = db.query(
        """
        SELECT metric, median_value, p25_value, p75_value, sample_size
        FROM peer_benchmarks
        WHERE cohort_name = ? AND refreshed_at = ?
        """,
        [cohort_name, latest],
    )
    return {
        r["metric"]: {
            "median": r["median_value"],
            "p25": r["p25_value"],
            "p75": r["p75_value"],
            "sample_size": r["sample_size"],
        }
        for r in rows
    }


def _save_batch(cohort_name: str, medians: dict[str, dict[str, float]], sample_size: int) -> None:
    refreshed_at = datetime.now(timezone.utc).isoformat()
    _ensure_table()
    rows = [
        [refreshed_at, cohort_name, metric, vals.get("median"), vals.get("p25"), vals.get("p75"), sample_size]
        for metric, vals in medians.items()
    ]
    if rows:
        db.executemany(
            """
            INSERT INTO peer_benchmarks
              (refreshed_at, cohort_name, metric, median_value, p25_value, p75_value, sample_size)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(refreshed_at, cohort_name, metric) DO UPDATE SET
              median_value = excluded.median_value, p25_value = excluded.p25_value,
              p75_value = excluded.p75_value, sample_size = excluded.sample_size
            """,
            rows,
        )


# ─── Cohort refresh ───────────────────────────────────────────────────────


METRICS = [
    "goodwill_to_equity",
    "intangibles_to_equity",
    "dso_days",
    "gross_margin_pct",
    "deferred_rev_to_revenue",
    "revenue_cagr_3y",
    "capex_to_revenue",
    "cfo_to_ni_ratio",
]


def refresh_complacency_peer_medians(max_workers: int = 6) -> dict:
    """
    Fan-out compute_financial_signals across the full complacency cohort
    and persist median / p25 / p75 per metric.

    Returns a summary dict {sample_size, refreshed_at, medians}.
    """
    from src.research_ideas.complacency.evidence_sources import compute_financial_signals

    universe_path = (
        Path(__file__).resolve().parent.parent
        / "data" / "complacency_universe.json"
    )
    with universe_path.open() as f:
        universe = json.load(f)
    tickers = [u["ticker"] for u in universe]

    per_ticker_signals: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(compute_financial_signals, t): t for t in tickers}
        for fut in as_completed(futures):
            t = futures[fut]
            try:
                per_ticker_signals[t] = fut.result()
            except Exception as exc:
                logger.warning("peer median compute failed for %s: %s", t, exc)
                per_ticker_signals[t] = {}

    # Aggregate per-metric across tickers
    medians: dict[str, dict[str, float]] = {}
    for metric in METRICS:
        vals = [
            s[metric] for s in per_ticker_signals.values()
            if s.get(metric) is not None
        ]
        if not vals:
            continue
        vals_sorted = sorted(vals)
        medians[metric] = {
            "median": statistics.median(vals_sorted),
            "p25": vals_sorted[len(vals_sorted) // 4] if len(vals_sorted) >= 4 else vals_sorted[0],
            "p75": vals_sorted[(3 * len(vals_sorted)) // 4] if len(vals_sorted) >= 4 else vals_sorted[-1],
        }

    _save_batch("complacency", medians, sample_size=len(per_ticker_signals))
    return {
        "refreshed_at": _latest_refresh("complacency"),
        "sample_size": len(per_ticker_signals),
        "medians": medians,
    }


# ─── Per-ticker peer context ──────────────────────────────────────────────


def get_peer_context(
    ticker: str,
    ticker_signals: dict,
    use_cache: bool = True,
    fallback_to_static: bool = True,
) -> dict:
    """
    Returns {metric: {value, median, p25, p75, percentile_bucket}} for the
    ticker. Uses cached cohort medians when available; falls back to the
    SOFTWARE_TECH_REFERENCE table when not.

    percentile_bucket ∈ {"BELOW p25", "p25-p50", "p50-p75", "ABOVE p75"}
    """
    cohort_medians = _load_latest("complacency") if use_cache else {}
    out: dict[str, dict] = {}

    for metric in METRICS:
        val = ticker_signals.get(metric)
        if val is None:
            continue
        ref = cohort_medians.get(metric)
        source = "cohort"
        if not ref and fallback_to_static:
            ref = SOFTWARE_TECH_REFERENCE.get(metric)
            source = "static_reference"
        if not ref:
            continue
        median = ref.get("median")
        p25 = ref.get("p25")
        p75 = ref.get("p75")
        bucket = "n/a"
        if p25 is not None and p75 is not None:
            if val < p25:
                bucket = "BELOW p25"
            elif val < median:
                bucket = "p25-p50"
            elif val < p75:
                bucket = "p50-p75"
            else:
                bucket = "ABOVE p75"
        out[metric] = {
            "value": val,
            "median": median,
            "p25": p25,
            "p75": p75,
            "bucket": bucket,
            "source": source,
        }
    return out


def format_peer_context_for_prompt(ctx: dict) -> str:
    if not ctx:
        return ""
    lines = ["PEER COMPARISON (vs. complacency cohort medians, or static software reference):"]
    fmt = {
        "goodwill_to_equity":      ("{:.1%}", "Goodwill / Equity"),
        "intangibles_to_equity":   ("{:.1%}", "Intangibles / Equity"),
        "dso_days":                ("{:.0f}d", "DSO (days)"),
        "gross_margin_pct":        ("{:.1%}", "Gross margin"),
        "deferred_rev_to_revenue": ("{:.1%}", "Deferred-rev / Revenue"),
        "revenue_cagr_3y":         ("{:.1%}", "Revenue CAGR 3y"),
        "capex_to_revenue":        ("{:.1%}", "Capex / Revenue"),
        "cfo_to_ni_ratio":         ("{:.2f}x", "CFO / Net income"),
    }
    for metric, vals in ctx.items():
        f, label = fmt.get(metric, ("{}", metric))
        v_str = f.format(vals['value']) if vals['value'] is not None else "n/a"
        m_str = f.format(vals['median']) if vals['median'] is not None else "n/a"
        p25_str = f.format(vals['p25']) if vals['p25'] is not None else "n/a"
        p75_str = f.format(vals['p75']) if vals['p75'] is not None else "n/a"
        lines.append(
            f"  {label:<26s} value={v_str:>8s}  median={m_str:>8s}  "
            f"[p25 {p25_str} – p75 {p75_str}]  bucket={vals['bucket']}  "
            f"({vals['source']})"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print("Refreshing peer medians across full complacency cohort...")
    s = refresh_complacency_peer_medians()
    print(f"Sample size : {s['sample_size']}")
    print(f"Refreshed   : {s['refreshed_at']}")
    print(f"Medians     :")
    for m, v in s["medians"].items():
        print(f"  {m:<30s} median={v['median']}  p25={v['p25']}  p75={v['p75']}")
