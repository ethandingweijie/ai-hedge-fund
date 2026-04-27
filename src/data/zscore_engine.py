"""V4-β Z-Score Engine — peer-cohort normalisation for sector KPI tiers.

Replaces hardcoded tier thresholds (e.g. NRR > 1.30 → elite +40%) with
dynamic tiers derived from where the ticker sits in its peer cohort.

Statistics: Median + Median Absolute Deviation (MAD), not mean + stdev.
MAD is robust against outliers — important because peer cohorts in the
archive can be small (3–10 tickers per profile) and a single outlier
would otherwise distort the z-score for the rest.

z = (value - median) / (1.4826 * MAD)

The 1.4826 constant scales MAD so that for a normal distribution it
matches stdev — keeps tier thresholds (z > +1.5, etc.) directly
interpretable as "1.5 standard deviations from the median."

Pipeline integration: `augment_metrics_with_z_scores(profile, ticker, metrics)`
runs after FMP augmentation, before composite_adjustment. Writes
`_z_scores` into the metrics dict; quality/risk multipliers consume it
when present, fall back to band-based tiers when absent.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Cohort fetch parameters — kept conservative to balance signal vs sample size
DEFAULT_LOOKBACK_DAYS = 60      # how far back to scan web_runs
DEFAULT_MIN_COHORT    = 3       # below this, skip z-scoring (band fallback)
MAD_NORMALIZATION     = 1.4826  # scales MAD to stdev-equivalent (normal dist)

# v3.20 — Cohort eligibility cutoff. Excludes the 47 historical web_runs
# accumulated during v3.0–v3.18 debugging (CRWD's wrong 76x ltv_cac_ratio,
# stale extractor outputs from pre-v3.13 field-name drift, runs done before
# the LTV/CAC 4-step protocol landed in v3.18, etc.). Only post-v3.19 runs
# (the first commit where IV is actually biased by composite) feed cohort
# statistics — guarantees z-tier kickers fire on a clean, internally-
# consistent peer set.
#
# Override via env var COHORT_MIN_RUN_AT_ISO (e.g. for staging environments
# that need a different cutoff). Default = v3.19 commit timestamp (2026-04-27).
COHORT_MIN_RUN_AT_ISO_DEFAULT = "2026-04-27T00:00:00+00:00"


# ── DB path resolution (matches analysis_service._get_db_path) ────────────────

def _resolve_db_path() -> str:
    env_path = os.environ.get("RUN_ARCHIVE_PATH")
    if env_path:
        return env_path
    return str(Path(__file__).resolve().parent / "run_archive.db")


def _connect_ro() -> sqlite3.Connection | None:
    """Open the web_runs DB in read-only mode. Returns None if the DB
    doesn't exist yet (fresh dev environment) — callers must handle."""
    db_path = _resolve_db_path()
    if not Path(db_path).exists():
        return None
    try:
        # URI mode for read-only — protects against accidental writes from
        # the cohort fetch path. busy_timeout still useful in case the
        # write-side is checkpointing WAL.
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.execute("PRAGMA busy_timeout=2000")
        return conn
    except Exception:
        return None


# ── Cohort fetch ──────────────────────────────────────────────────────────────

def fetch_peer_cohort(
    profile_name: str,
    *,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    exclude_ticker: str | None = None,
    max_runs: int = 200,
    min_run_at_iso: str | None = None,
) -> dict[str, list[float]]:
    """Returns {kpi_name: [peer values]} for the given profile.

    Source: web_runs table. Walks full_result_json[data][framework_metrics_all]
    for each peer run within the lookback window. One value per peer ticker
    (latest run wins if a ticker has multiple runs in the window).

    `exclude_ticker` is the current ticker being scored — always excluded
    from its own cohort to prevent self-reference bias.

    `min_run_at_iso` is an explicit ISO-timestamp floor for cohort eligibility
    (v3.20). When set, runs older than this date are excluded REGARDLESS of
    `lookback_days`. Default resolution order:
      1. explicit `min_run_at_iso` argument
      2. env var COHORT_MIN_RUN_AT_ISO
      3. module constant COHORT_MIN_RUN_AT_ISO_DEFAULT
    The effective cutoff is `max(now − lookback_days, min_run_at_iso)` —
    whichever is later wins. Lets us mask the 47 v3.0–v3.18 debugging-era
    rows from polluting the cohort median.

    Returns {} if cohort fetch fails or yields no data.
    """
    conn = _connect_ro()
    if conn is None:
        return {}
    try:
        rolling_cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
        explicit_floor = (
            min_run_at_iso
            or os.environ.get("COHORT_MIN_RUN_AT_ISO")
            or COHORT_MIN_RUN_AT_ISO_DEFAULT
        )
        # Whichever is LATER wins (most restrictive cutoff)
        cutoff_iso = max(rolling_cutoff, explicit_floor)
        rows = conn.execute(
            """
            SELECT ticker, run_at, full_result_json
            FROM web_runs
            WHERE profile_name = ?
              AND run_at >= ?
              AND full_result_json IS NOT NULL
            ORDER BY run_at DESC
            LIMIT ?
            """,
            (profile_name, cutoff_iso, max_runs),
        ).fetchall()
    except Exception:
        return {}
    finally:
        try:
            conn.close()
        except Exception:
            pass

    if not rows:
        return {}

    # Latest-run-wins per ticker — first row encountered (DESC by run_at)
    # is the most recent.
    seen_tickers: set[str] = set()
    if exclude_ticker:
        seen_tickers.add(exclude_ticker.upper())

    cohort: dict[str, list[float]] = {}
    for ticker, _run_at, full_json in rows:
        t_upper = (ticker or "").upper()
        if not t_upper or t_upper in seen_tickers:
            continue
        seen_tickers.add(t_upper)
        try:
            payload = json.loads(full_json)
        except Exception:
            continue
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            continue
        # Walk all metric state buckets for this ticker
        for state_key in ("framework_metrics_all", "insurance_metrics_all", "bank_metrics_all"):
            bucket = data.get(state_key)
            if not isinstance(bucket, dict):
                continue
            metrics = bucket.get(t_upper) or bucket.get(ticker)
            if not isinstance(metrics, dict):
                continue
            for kpi_name, value in metrics.items():
                if not isinstance(kpi_name, str) or kpi_name.startswith("_"):
                    continue
                # Coerce to float — skip non-numeric (string KPIs, dicts, etc.)
                try:
                    fval = float(value)
                except (TypeError, ValueError):
                    continue
                if fval != fval or fval in (float("inf"), float("-inf")):  # NaN / inf guard
                    continue
                cohort.setdefault(kpi_name, []).append(fval)
    return cohort


# ── MAD-based z-score ─────────────────────────────────────────────────────────

def _median(values: list[float]) -> float:
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return (s[mid - 1] + s[mid]) / 2.0 if n % 2 == 0 else s[mid]


def _mad(values: list[float], median_value: float) -> float:
    """Median Absolute Deviation — robust spread estimator."""
    deviations = [abs(v - median_value) for v in values]
    return _median(deviations) if deviations else 0.0


def compute_z_scores(
    profile_name: str,
    ticker_metrics: dict[str, Any],
    *,
    cohort: dict[str, list[float]] | None = None,
    exclude_ticker: str | None = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    min_cohort: int = DEFAULT_MIN_COHORT,
) -> dict[str, dict[str, float]]:
    """Compute z-score per KPI against peer cohort. Returns:

      {kpi_name: {"z": float, "cohort_size": int, "median": float, "mad": float}}

    KPIs are skipped (omitted from result) when:
      - cohort size < min_cohort
      - MAD == 0 (all peers identical → z is undefined)
      - value is non-numeric or NaN

    Caller (compositor) treats absent z-score as "use band-based fallback."
    """
    if cohort is None:
        cohort = fetch_peer_cohort(
            profile_name,
            lookback_days=lookback_days,
            exclude_ticker=exclude_ticker,
        )
    out: dict[str, dict[str, float]] = {}
    for kpi_name, value in (ticker_metrics or {}).items():
        if not isinstance(kpi_name, str) or kpi_name.startswith("_"):
            continue
        try:
            fval = float(value)
        except (TypeError, ValueError):
            continue
        if fval != fval:  # NaN
            continue
        peer_values = cohort.get(kpi_name) or []
        if len(peer_values) < min_cohort:
            continue
        med = _median(peer_values)
        mad = _mad(peer_values, med)
        if mad == 0.0:
            continue
        z = (fval - med) / (MAD_NORMALIZATION * mad)
        out[kpi_name] = {
            "z":            round(z, 3),
            "cohort_size":  len(peer_values),
            "median":       round(med, 6),
            "mad":          round(mad, 6),
        }
    return out


# ── Z-tier kicker (replaces band-based tier when z available) ─────────────────

# Kicker schedule — symmetric around 0, anchored at z=±0.5 / ±1.0 / ±1.5.
# These match the existing band-based tiers in spirit:
#   top decile (z ≥ +1.5)        → 1.30× (was "elite")
#   top quartile (z ≥ +1.0)      → 1.15× (was "top-quartile")
#   above median (z ≥ +0.5)      → 1.05× (was "above-avg")
#   below median (z ≤ -0.5)      → 0.95× (was "below-avg")
#   bottom quartile (z ≤ -1.0)   → 0.85× (was "weak")
#   bottom decile (z ≤ -1.5)     → 0.70× (was "value-trap")
_Z_TIERS_HIGHER_BETTER: list[tuple[float, float, str]] = [
    ( 1.5, 1.30, "top-decile"),
    ( 1.0, 1.15, "top-quartile"),
    ( 0.5, 1.05, "above-median"),
    (-0.5, 1.00, "near-median"),
    (-1.0, 0.95, "below-median"),
    (-1.5, 0.85, "bottom-quartile"),
]
_Z_TIER_FLOOR = (0.70, "bottom-decile")


def z_tier_kicker(
    z: float,
    *,
    direction: str = "higher_better",
) -> tuple[float, str]:
    """Map a z-score to a quality/risk tier multiplier.

    `direction`:
      - "higher_better" (NRR, ROE, FCF margin) → positive z is good
      - "lower_better"  (combined ratio, leverage) → negative z is good

    For lower_better KPIs, flip the sign before tier lookup so the same
    tier table works.
    """
    z_eff = z if direction == "higher_better" else -z
    for threshold, mult, label in _Z_TIERS_HIGHER_BETTER:
        if z_eff >= threshold:
            return (mult, label)
    return _Z_TIER_FLOOR


# ── Pipeline integration helper ───────────────────────────────────────────────

def augment_metrics_with_z_scores(
    profile_name: str,
    ticker: str,
    metrics: dict[str, Any] | None,
    *,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    min_cohort: int = DEFAULT_MIN_COHORT,
) -> dict[str, Any]:
    """Add `_z_scores` key to the metrics dict in place. Idempotent.

    Wrapped in try/except so cohort-fetch failures degrade gracefully
    (no z-scores → composite uses band-based tiers, identical to v3.0).
    """
    if metrics is None:
        metrics = {}
    if not profile_name or not ticker:
        return metrics
    try:
        z_scores = compute_z_scores(
            profile_name,
            metrics,
            exclude_ticker=ticker,
            lookback_days=lookback_days,
            min_cohort=min_cohort,
        )
        if z_scores:
            metrics["_z_scores"] = z_scores
    except Exception:
        # Fail silent — band-based tier is the existing behaviour
        pass
    return metrics


__all__ = [
    "fetch_peer_cohort",
    "compute_z_scores",
    "z_tier_kicker",
    "augment_metrics_with_z_scores",
    "DEFAULT_LOOKBACK_DAYS",
    "DEFAULT_MIN_COHORT",
]
