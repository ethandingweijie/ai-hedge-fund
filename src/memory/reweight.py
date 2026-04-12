"""
src/memory/reweight.py
======================
Agent conviction weight updater powered by the SQLite run archive.

This module replaces the manual weight logic in post_trade_review.py with a
data-driven version that reads actual scored outcomes from the archive.

Reweighting formula (matches Post-Trade Review Agent spec):
    CORRECT   → weight × 1.1  (compounded per net correct; cap 2.0)
    INCORRECT → weight × 0.9  (compounded per net incorrect; floor 0.5)
    NEUTRAL   → unchanged

Usage (called from post_trade_review or standalone):
    from src.memory.reweight import reweight_from_archive
    updates = reweight_from_archive(min_reviews=3, dry_run=False)
    # returns {agent: {"old": float, "new": float, "correct": int, ...}, ...}

Standalone CLI:
    python -m src.memory.reweight
    python -m src.memory.reweight --dry-run
    python -m src.memory.reweight --min-reviews 5
"""

import argparse
import json
import os
from datetime import datetime

from src.memory.run_archive import get_agent_outcomes

# ── Paths ─────────────────────────────────────────────────────────────────────

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
CONVICTION_WEIGHTS_PATH = os.path.join(DATA_DIR, "conviction_weights.json")
REGIME_WEIGHTS_PATH     = os.path.join(DATA_DIR, "regime_weights.json")

ALL_AGENTS = [
    "damodaran", "graham", "ackman", "cathie_wood", "munger",
    "burry", "pabrai", "lynch", "fisher", "jhunjhunwala",
    "druckenmiller", "buffett",
]

WEIGHT_CAP     = 2.0
WEIGHT_FLOOR   = 0.5
CORRECT_MULT   = 1.1
INCORRECT_MULT = 0.9
REGIME_ALPHA   = 0.15   # learning rate for regime-stratified formula


# ── Internal helpers ───────────────────────────────────────────────────────────

def _load_weights() -> dict[str, float]:
    try:
        with open(CONVICTION_WEIGHTS_PATH) as f:
            raw = json.load(f)
        return {k: float(v) for k, v in raw.items() if k in ALL_AGENTS}
    except Exception:
        return {a: 1.0 for a in ALL_AGENTS}


def _save_weights(weights: dict[str, float], total_reviews: int) -> None:
    data: dict = dict(weights)
    data["last_updated"] = datetime.now().strftime("%Y-%m-%d")
    data["total_reviews"] = total_reviews
    with open(CONVICTION_WEIGHTS_PATH, "w") as f:
        json.dump(data, f, indent=2)


# ── Public API ─────────────────────────────────────────────────────────────────

def reweight_from_archive(
    min_reviews: int = 3,
    dry_run: bool = False,
) -> dict[str, dict]:
    """
    Read scored outcomes from the archive, compute updated conviction weights,
    and (unless dry_run) write them to conviction_weights.json.

    Parameters
    ----------
    min_reviews : only update agents with at least this many scored rows
    dry_run     : if True, compute and return updates without writing to disk

    Returns
    -------
    Dict keyed by agent_key:
    {
      "buffett": {
        "old": 1.0,
        "new": 1.21,
        "correct": 12,
        "neutral": 4,
        "incorrect": 2,
        "total": 18,
        "net": 10,
      },
      ...
    }
    Only agents whose weight actually changes are included.
    """
    outcomes = get_agent_outcomes(min_reviews=min_reviews)
    weights = _load_weights()
    updates: dict[str, dict] = {}

    for agent, stats in outcomes.items():
        if agent not in ALL_AGENTS:
            continue

        correct = stats["correct"]
        incorrect = stats["incorrect"]
        net = correct - incorrect
        old_w = weights.get(agent, 1.0)

        if net > 0:
            new_w = min(WEIGHT_CAP, old_w * (CORRECT_MULT ** net))
        elif net < 0:
            new_w = max(WEIGHT_FLOOR, old_w * (INCORRECT_MULT ** abs(net)))
        else:
            new_w = old_w

        new_w = round(new_w, 4)

        if new_w != old_w:
            weights[agent] = new_w
            updates[agent] = {
                "old": old_w,
                "new": new_w,
                "correct": correct,
                "neutral": stats["neutral"],
                "incorrect": incorrect,
                "total": stats["total"],
                "net": net,
            }

    if not dry_run and updates:
        # Preserve total_reviews count
        try:
            with open(CONVICTION_WEIGHTS_PATH) as f:
                existing = json.load(f)
            total_reviews = existing.get("total_reviews", 0) + sum(
                v["total"] for v in updates.values()
            )
        except Exception:
            total_reviews = sum(v["total"] for v in updates.values())
        _save_weights(weights, total_reviews)

    return updates


def reweight_regime_stratified(
    current_regime: str | None = None,
    alpha: float = REGIME_ALPHA,
    min_reviews: int = 20,
    dry_run: bool = False,
) -> dict[str, dict]:
    """
    Compute regime-stratified conviction weights using:

        weight = base_weight × (1 + α × regime_hit_rate)

    where:
        regime_hit_rate = correct_calls_in_this_regime / scored_in_this_regime
        α               = learning_rate (default 0.15)
        base_weight     = current conviction_weights.json value for the agent

    Writes/updates src/data/regime_weights.json keyed by regime name.
    If current_regime is provided, only that regime is (re)computed;
    otherwise all regimes found in the archive are processed.

    Parameters
    ----------
    current_regime : e.g. "risk-on" | "risk-off" | None (all regimes)
    alpha          : learning rate (suggest 0.10–0.20)
    min_reviews    : minimum scored outcomes per agent/regime bucket
    dry_run        : compute and return without writing to disk

    Returns
    -------
    {
      "risk-on": {
        "buffett": {"old": 1.0, "new": 1.08, "hit_rate": 0.53, "scored": 22},
        ...
      },
      "risk-off": { ... },
    }
    Only entries whose weight actually changes are included.
    """
    from src.memory.run_archive import get_agent_outcomes_by_regime

    by_regime   = get_agent_outcomes_by_regime(min_reviews=min_reviews)
    base_weights = _load_weights()

    # Load existing regime_weights.json to detect deltas
    existing_rw: dict = {}
    try:
        with open(REGIME_WEIGHTS_PATH, encoding="utf-8") as f:
            existing_rw = json.load(f)
    except Exception:
        pass

    updates: dict[str, dict] = {}        # what changed  → returned
    new_rw: dict = {                     # full file to write
        k: v for k, v in existing_rw.items() if k != "_meta"
    }

    for regime, agent_stats in by_regime.items():
        if current_regime and regime != current_regime:
            continue

        regime_weights: dict[str, float] = dict(new_rw.get(regime, {}))
        regime_updates: dict[str, dict]  = {}

        for agent, stats in agent_stats.items():
            if agent not in ALL_AGENTS:
                continue
            base_w   = base_weights.get(agent, 1.0)
            hit_rate = stats["hit_rate"]
            new_w    = round(
                min(WEIGHT_CAP, max(WEIGHT_FLOOR,
                    base_w * (1 + alpha * hit_rate))), 4
            )
            old_w = existing_rw.get(regime, {}).get(agent, base_w)
            regime_weights[agent] = new_w
            if abs(new_w - round(old_w, 4)) >= 0.001:
                regime_updates[agent] = {
                    "old":      round(old_w, 4),
                    "new":      new_w,
                    "hit_rate": stats["hit_rate"],
                    "scored":   stats["scored"],
                    "correct":  stats["correct"],
                    "incorrect":stats["incorrect"],
                }

        if regime_weights:
            new_rw[regime] = regime_weights
        if regime_updates:
            updates[regime] = regime_updates

    if not dry_run and updates:
        new_rw["_meta"] = {
            "alpha":        alpha,
            "min_reviews":  min_reviews,
            "formula":      "base_weight × (1 + α × regime_hit_rate)",
            "last_updated": datetime.now().strftime("%Y-%m-%d"),
        }
        with open(REGIME_WEIGHTS_PATH, "w", encoding="utf-8") as f:
            json.dump(new_rw, f, indent=2)

    return updates


def print_regime_reweight_report(updates: dict[str, dict], dry_run: bool = False) -> None:
    """Pretty-print the regime-stratified reweighting outcome."""
    tag = " [DRY RUN]" if dry_run else ""
    print(f"\n{'='*62}")
    print(f"Regime-Stratified Agent Reweight{tag}")
    print(f"  Formula: weight = base × (1 + {REGIME_ALPHA} × regime_hit_rate)")
    print("=" * 62)

    if not updates:
        print("  No weight changes (insufficient regime-scored outcomes).")
        print("=" * 62)
        return

    for regime in sorted(updates):
        print(f"\n  [{regime.upper()}]")
        for agent, info in sorted(
            updates[regime].items(), key=lambda x: abs(x[1]["new"] - x[1]["old"]), reverse=True
        ):
            direction = "↑" if info["new"] > info["old"] else "↓"
            print(
                f"    {agent:<18} {info['old']:.3f} -> {info['new']:.3f} {direction}  "
                f"hit={info['hit_rate']:.1%}  "
                f"({info['correct']}C/{info['incorrect']}I, n={info['scored']})"
            )

    if not dry_run:
        print(f"\n  regime_weights.json updated.")
    print("=" * 62)


def print_reweight_report(updates: dict[str, dict], dry_run: bool = False) -> None:
    """Pretty-print the reweighting outcome."""
    tag = " [DRY RUN]" if dry_run else ""
    print(f"\n{'='*55}")
    print(f"Agent Conviction Reweight{tag}")
    print("="*55)

    if not updates:
        print("  No weight changes (insufficient scored outcomes or all neutral).")
        return

    for agent, info in sorted(updates.items(), key=lambda x: abs(x[1]["net"]), reverse=True):
        direction = "+" if info["net"] > 0 else "-"
        print(
            f"  {agent:<18} {info['old']:.3f} -> {info['new']:.3f}  "
            f"({direction}{abs(info['net'])} net | "
            f"{info['correct']}C / {info['neutral']}N / {info['incorrect']}I)"
        )

    if not dry_run:
        print(f"\n  conviction_weights.json updated.")
    print("="*55)


# ── CLI entrypoint ─────────────────────────────────────────────────────────────

def _cli():
    parser = argparse.ArgumentParser(
        description="Reweight agent convictions from the run archive.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m src.memory.reweight --dry-run
  python -m src.memory.reweight --min-reviews 5
  python -m src.memory.reweight --regime-stratified --dry-run
  python -m src.memory.reweight --regime-stratified --regime risk-on --alpha 0.15
        """,
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Compute weights but do not write to disk.",
    )
    parser.add_argument(
        "--min-reviews", type=int, default=3,
        help="Minimum scored outcomes required per agent (default: 3).",
    )
    parser.add_argument(
        "--regime-stratified", action="store_true",
        help="Run regime-stratified reweighting (writes regime_weights.json).",
    )
    parser.add_argument(
        "--regime", default=None,
        help="Limit regime-stratified run to one regime, e.g. 'risk-on'.",
    )
    parser.add_argument(
        "--alpha", type=float, default=REGIME_ALPHA,
        help=f"Learning rate for regime formula (default: {REGIME_ALPHA}).",
    )
    args = parser.parse_args()

    if args.regime_stratified:
        updates = reweight_regime_stratified(
            current_regime=args.regime,
            alpha=args.alpha,
            min_reviews=args.min_reviews,
            dry_run=args.dry_run,
        )
        print_regime_reweight_report(updates, dry_run=args.dry_run)
    else:
        updates = reweight_from_archive(min_reviews=args.min_reviews, dry_run=args.dry_run)
        print_reweight_report(updates, dry_run=args.dry_run)


if __name__ == "__main__":
    _cli()
