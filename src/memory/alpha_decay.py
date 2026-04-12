"""
src/memory/alpha_decay.py
=========================
Alpha Decay Monitor

Weekly comparison: last N live signals vs all-time baseline hit rate.
Fires an alert if the recent hit rate drops > DECAY_THRESHOLD below baseline.

The "last N signals" window uses the most recent N *scored* agent-level signals
from the archive (ordered by run_at DESC).  Hit rate = CORRECT / (CORRECT + INCORRECT).
NEUTRAL outcomes are excluded from the denominator so they don't dilute the rate.

Alert is only dispatched when recent_scored >= 5 (avoids noise from tiny samples).

CLI:
    python -m src.memory.alpha_decay
    python -m src.memory.alpha_decay --window 20 --threshold 0.15
    python -m src.memory.alpha_decay --dry-run          # print only, no alert
"""

import argparse
import os
import sqlite3
from datetime import datetime

# Dispatch reuses the existing alert infrastructure
try:
    from src.utils.alerts import _send_slack, _send_email
    _ALERTS_AVAILABLE = True
except ImportError:
    _ALERTS_AVAILABLE = False

# ── Constants ─────────────────────────────────────────────────────────────────

DB_PATH         = os.path.join(os.path.dirname(__file__), "..", "data", "run_archive.db")
DECAY_THRESHOLD = float(os.getenv("ALPHA_DECAY_THRESHOLD", "0.15"))
DEFAULT_WINDOW  = int(os.getenv("ALPHA_DECAY_WINDOW", "20"))
MIN_RECENT      = 5   # require at least this many recent scored signals before alerting


# ── Internal helpers ──────────────────────────────────────────────────────────

def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ── Public API ────────────────────────────────────────────────────────────────

def compute_alpha_decay(window: int = DEFAULT_WINDOW) -> dict:
    """
    Query the archive and compute hit-rate decay.

    Returns
    -------
    {
      "baseline_hit_rate": float,   # all-time hit rate (excl. NEUTRAL)
      "recent_hit_rate":   float,   # hit rate over last `window` scored signals
      "decay":             float,   # baseline - recent  (positive = decay)
      "window":            int,
      "baseline_scored":   int,
      "recent_scored":     int,
      "alert":             bool,    # True if decay > threshold AND recent_scored >= MIN_RECENT
      "threshold":         float,
      "checked_at":        str,
    }
    """
    try:
        conn = _get_conn()

        # ── Baseline: all non-pending, non-neutral agent signals ──────────────
        base = conn.execute(
            """
            SELECT
                SUM(CASE WHEN outcome='CORRECT'   THEN 1 ELSE 0 END) AS correct,
                SUM(CASE WHEN outcome='INCORRECT' THEN 1 ELSE 0 END) AS incorrect
            FROM agent_signals
            WHERE outcome NOT IN ('PENDING', 'NEUTRAL')
            """
        ).fetchone()
        base_correct   = base["correct"]   or 0
        base_incorrect = base["incorrect"] or 0
        base_scored    = base_correct + base_incorrect
        baseline_hr    = base_correct / base_scored if base_scored else 0.0

        # ── Recent: last `window` scored signals by run date ──────────────────
        recent_rows = conn.execute(
            """
            SELECT ag.outcome
            FROM agent_signals ag
            JOIN runs r ON r.run_id = ag.run_id
            WHERE ag.outcome NOT IN ('PENDING', 'NEUTRAL')
            ORDER BY r.run_at DESC
            LIMIT ?
            """,
            (window,),
        ).fetchall()
        conn.close()

        recent_correct   = sum(1 for r in recent_rows if r["outcome"] == "CORRECT")
        recent_incorrect = sum(1 for r in recent_rows if r["outcome"] == "INCORRECT")
        recent_scored    = recent_correct + recent_incorrect
        recent_hr        = recent_correct / recent_scored if recent_scored else 0.0

        decay = baseline_hr - recent_hr
        alert = (recent_scored >= MIN_RECENT) and (decay > DECAY_THRESHOLD)

        return {
            "baseline_hit_rate": round(baseline_hr, 4),
            "recent_hit_rate":   round(recent_hr, 4),
            "decay":             round(decay, 4),
            "window":            window,
            "baseline_scored":   base_scored,
            "recent_scored":     recent_scored,
            "alert":             alert,
            "threshold":         DECAY_THRESHOLD,
            "checked_at":        datetime.now().strftime("%Y-%m-%d %H:%M"),
        }

    except Exception as exc:
        return {
            "baseline_hit_rate": 0.0,
            "recent_hit_rate":   0.0,
            "decay":             0.0,
            "window":            window,
            "baseline_scored":   0,
            "recent_scored":     0,
            "alert":             False,
            "threshold":         DECAY_THRESHOLD,
            "checked_at":        datetime.now().strftime("%Y-%m-%d %H:%M"),
            "error":             str(exc),
        }


def print_decay_report(result: dict) -> None:
    W = 58
    print(f"\n{'='*W}")
    print(f"  Alpha Decay Monitor")
    print(f"  Checked : {result.get('checked_at', 'now')}")
    print(f"{'='*W}")
    print(f"  Baseline hit rate : {result['baseline_hit_rate']:.1%}  "
          f"({result['baseline_scored']} scored, all-time)")
    print(f"  Recent hit rate   : {result['recent_hit_rate']:.1%}  "
          f"(last {result['window']}, n={result['recent_scored']} scored)")
    print(f"  Decay             : {result['decay']:+.1%}  "
          f"(threshold {result.get('threshold', DECAY_THRESHOLD):.0%})")

    if result.get("error"):
        print(f"  STATUS  : ERROR — {result['error']}")
    elif result["baseline_scored"] == 0:
        print(f"  STATUS  : NO DATA — no scored outcomes in archive yet")
    elif result["recent_scored"] < MIN_RECENT:
        print(f"  STATUS  : INSUFFICIENT RECENT DATA "
              f"(need {MIN_RECENT}+ recent scored, have {result['recent_scored']})")
    elif result["alert"]:
        print(f"  STATUS  : *** ALPHA DECAY ALERT ***")
        print(f"            Hit rate dropped {result['decay']:.1%} below baseline.")
        print(f"            Run: python -m src.memory.reweight --dry-run")
    else:
        print(f"  STATUS  : OK — within threshold")
    print(f"{'='*W}\n")


def _build_alert_message(result: dict) -> str:
    sep = "=" * 52
    return "\n".join([
        "AI Hedge Fund -- ALPHA DECAY ALERT",
        sep,
        f"Recent hit rate has fallen {result['decay']:.1%} below baseline.",
        "",
        f"Baseline (all-time) : {result['baseline_hit_rate']:.1%}  "
        f"({result['baseline_scored']} signals)",
        f"Recent (last {result['window']:>2})    : {result['recent_hit_rate']:.1%}  "
        f"({result['recent_scored']} signals)",
        f"Threshold           : {result['threshold']:.0%}",
        "",
        "Action: a new market regime may have emerged that current weights",
        "do not reflect.  Recommended steps:",
        "  1. python -m src.memory.reweight --dry-run",
        "  2. python -m src.memory.backtest --min-scored 3",
        "  3. Review recent signals for systematic misalignment.",
        "",
        f"Checked: {result.get('checked_at', datetime.now().strftime('%Y-%m-%d %H:%M'))}",
    ])


def run_alpha_decay_check(
    window: int = DEFAULT_WINDOW,
    dry_run: bool = False,
) -> dict:
    """
    Compute alpha decay, print the report, and dispatch an alert if warranted.

    Parameters
    ----------
    window  : number of recent scored signals to compare against baseline
    dry_run : if True, print report but never dispatch alerts

    Returns
    -------
    Result dict from compute_alpha_decay().
    """
    result = compute_alpha_decay(window=window)
    print_decay_report(result)

    if result["alert"] and not dry_run and _ALERTS_AVAILABLE:
        msg = _build_alert_message(result)
        subject = "[AI Hedge Fund] Alpha Decay Alert"
        _send_slack(msg)
        _send_email(subject, msg)

    return result


# ── CLI ───────────────────────────────────────────────────────────────────────

def _cli() -> None:
    global DECAY_THRESHOLD  # noqa: PLW0603
    parser = argparse.ArgumentParser(
        description="Alpha Decay Monitor — compare recent vs baseline hit rate.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m src.memory.alpha_decay
  python -m src.memory.alpha_decay --window 30
  python -m src.memory.alpha_decay --threshold 0.10 --dry-run
        """,
    )
    parser.add_argument(
        "--window", type=int, default=DEFAULT_WINDOW,
        help=f"Number of recent scored signals to compare (default: {DEFAULT_WINDOW})",
    )
    parser.add_argument(
        "--threshold", type=float, default=DECAY_THRESHOLD,
        help=f"Decay fraction that triggers alert (default: {DECAY_THRESHOLD})",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print report without dispatching alerts",
    )
    args = parser.parse_args()

    DECAY_THRESHOLD = args.threshold
    run_alpha_decay_check(window=args.window, dry_run=args.dry_run)


if __name__ == "__main__":
    _cli()
