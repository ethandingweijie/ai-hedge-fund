"""
src/memory/regime_calibrate.py
===============================
Quarterly Regime-Stratified Weight Recalibration

Guards before writing weights:
  - MIN_BUCKET_REVIEWS (default 20) scored outcomes per agent/regime bucket
  - At least 2 distinct regimes represented in the archive
  - At least 2 distinct tickers per regime bucket

Intended schedule: run once per quarter.  Safe to run more frequently —
the guard conditions ensure it is a no-op until sufficient data exists.

CLI:
    python -m src.memory.regime_calibrate
    python -m src.memory.regime_calibrate --dry-run
    python -m src.memory.regime_calibrate --min-reviews 10   # lower for testing
    python -m src.memory.regime_calibrate --alpha 0.20
"""

import argparse
import json
import os
import sqlite3
from datetime import datetime

from src.memory.reweight import (
    reweight_regime_stratified,
    print_regime_reweight_report,
    REGIME_ALPHA,
    REGIME_WEIGHTS_PATH,
)

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "run_archive.db")

MIN_BUCKET_REVIEWS  = 20   # scored outcomes per agent/regime bucket
MIN_DISTINCT_REGIMES = 2   # archive must span at least 2 regimes
MIN_TICKERS_PER_REGIME = 2  # at least 2 distinct tickers per regime


# ── Guard checks ─────────────────────────────────────────────────────────────

def _check_readiness(min_reviews: int = MIN_BUCKET_REVIEWS) -> dict:
    """
    Query the archive and assess whether calibration is statistically safe.

    Returns
    -------
    {
      "ready":          bool,
      "reasons":        list[str],   # reasons NOT ready (empty if ready)
      "regime_counts":  {regime: {agent: scored_count}},
      "distinct_regimes": int,
    }
    """
    reasons: list[str] = []
    regime_counts: dict[str, dict[str, int]] = {}
    regime_tickers: dict[str, set] = {}

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row

        rows = conn.execute(
            """
            SELECT
                r.regime_risk_appetite  AS regime,
                ag.agent_key,
                ts.ticker,
                ag.outcome
            FROM agent_signals ag
            JOIN runs r  ON r.run_id  = ag.run_id
            JOIN ticker_signals ts
                       ON ts.run_id = ag.run_id AND ts.ticker = ag.ticker
            WHERE ag.outcome NOT IN ('PENDING', 'NEUTRAL')
              AND r.regime_risk_appetite IS NOT NULL
            """
        ).fetchall()
        conn.close()

        for row in rows:
            regime = row["regime"]
            agent  = row["agent_key"]
            ticker = row["ticker"]
            regime_counts.setdefault(regime, {})
            regime_counts[regime][agent] = regime_counts[regime].get(agent, 0) + 1
            regime_tickers.setdefault(regime, set()).add(ticker)

    except Exception as exc:
        reasons.append(f"DB query failed: {exc}")
        return {"ready": False, "reasons": reasons,
                "regime_counts": {}, "distinct_regimes": 0}

    distinct_regimes = len(regime_counts)

    if distinct_regimes < MIN_DISTINCT_REGIMES:
        reasons.append(
            f"Only {distinct_regimes} regime(s) in archive "
            f"(need {MIN_DISTINCT_REGIMES}+)"
        )

    for regime, agent_counts in regime_counts.items():
        tickers = regime_tickers.get(regime, set())
        if len(tickers) < MIN_TICKERS_PER_REGIME:
            reasons.append(
                f"Regime '{regime}': only {len(tickers)} ticker(s) "
                f"(need {MIN_TICKERS_PER_REGIME}+)"
            )
        for agent, count in agent_counts.items():
            if count < min_reviews:
                reasons.append(
                    f"Regime '{regime}' / agent '{agent}': "
                    f"{count} scored outcomes (need {min_reviews})"
                )

    return {
        "ready":           len(reasons) == 0,
        "reasons":         reasons,
        "regime_counts":   {r: dict(ac) for r, ac in regime_counts.items()},
        "distinct_regimes": distinct_regimes,
    }


# ── Report ────────────────────────────────────────────────────────────────────

def print_readiness_report(check: dict) -> None:
    W = 62
    print(f"\n{'='*W}")
    print(f"  Quarterly Regime Calibration — Readiness Check")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * W)
    print(f"  Distinct regimes in archive : {check['distinct_regimes']}")
    for regime, agents in check["regime_counts"].items():
        total = sum(agents.values())
        print(f"  [{regime}]  {len(agents)} agents  {total} scored outcomes")
        for agent, cnt in sorted(agents.items()):
            flag = "" if cnt >= MIN_BUCKET_REVIEWS else "  ← insufficient"
            print(f"    {agent:<18} {cnt:>4} scored{flag}")

    if check["ready"]:
        print(f"\n  STATUS  : READY — all guard conditions met")
    else:
        print(f"\n  STATUS  : NOT READY — {len(check['reasons'])} condition(s) unmet:")
        for r in check["reasons"]:
            print(f"    · {r}")
    print("=" * W)


# ── Public API ────────────────────────────────────────────────────────────────

def run_quarterly_calibration(
    alpha: float = REGIME_ALPHA,
    min_reviews: int = MIN_BUCKET_REVIEWS,
    dry_run: bool = False,
    force: bool = False,
) -> dict:
    """
    Run the quarterly regime-stratified weight recalibration with guards.

    Parameters
    ----------
    alpha       : learning rate for regime formula
    min_reviews : scored outcomes per agent/regime bucket required
    dry_run     : compute without writing files
    force       : bypass guard conditions (for testing; not for production)

    Returns
    -------
    {"ready": bool, "updates": dict, "check": dict}
    """
    check = _check_readiness(min_reviews=min_reviews)
    print_readiness_report(check)

    if not check["ready"] and not force:
        print(
            "\n  Calibration skipped — guard conditions not met.\n"
            "  Rerun quarterly once archive accumulates more scored outcomes.\n"
            "  Use --force to bypass guards (testing only).\n"
        )
        return {"ready": False, "updates": {}, "check": check}

    if force and not check["ready"]:
        print("\n  WARNING: --force bypasses guard conditions. "
              "Results may not be statistically meaningful.\n")

    print("\n  Running regime-stratified reweighting...")
    updates = reweight_regime_stratified(
        current_regime=None,
        alpha=alpha,
        min_reviews=min_reviews,
        dry_run=dry_run,
    )
    print_regime_reweight_report(updates, dry_run=dry_run)

    if not dry_run and updates:
        # Stamp the last calibration date into regime_weights.json _meta
        try:
            with open(REGIME_WEIGHTS_PATH, encoding="utf-8") as f:
                rw = json.load(f)
            rw.setdefault("_meta", {})["last_quarterly_calibration"] = (
                datetime.now().strftime("%Y-%m-%d")
            )
            with open(REGIME_WEIGHTS_PATH, "w", encoding="utf-8") as f:
                json.dump(rw, f, indent=2)
        except Exception:
            pass

    return {"ready": check["ready"] or force, "updates": updates, "check": check}


# ── CLI ───────────────────────────────────────────────────────────────────────

def _cli() -> None:
    parser = argparse.ArgumentParser(
        description="Quarterly regime-stratified weight recalibration.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m src.memory.regime_calibrate --dry-run
  python -m src.memory.regime_calibrate --min-reviews 5 --force --dry-run
  python -m src.memory.regime_calibrate --alpha 0.20
        """,
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Compute without writing files.",
    )
    parser.add_argument(
        "--min-reviews", type=int, default=MIN_BUCKET_REVIEWS,
        help=f"Scored outcomes required per agent/regime bucket (default: {MIN_BUCKET_REVIEWS}).",
    )
    parser.add_argument(
        "--alpha", type=float, default=REGIME_ALPHA,
        help=f"Learning rate for regime formula (default: {REGIME_ALPHA}).",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Bypass guard conditions (testing only — not for production).",
    )
    args = parser.parse_args()

    run_quarterly_calibration(
        alpha=args.alpha,
        min_reviews=args.min_reviews,
        dry_run=args.dry_run,
        force=args.force,
    )


if __name__ == "__main__":
    _cli()
