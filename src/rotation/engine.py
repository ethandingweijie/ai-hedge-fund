"""
Macro Rotation Engine — Sprint C #3

What it does:
1. Reads the previous macro regime from regime_state.json
2. Runs a fresh macro regime classification (live FMP data + LLM)
3. Compares old vs new regime — scores the shift 0–10 across 5 dimensions
4. For each open position in the SQLite archive:
   - Recalculates recommended position size under the new regime
   - Emits TRIM / ADD / HOLD / ROTATE_OUT recommendation
5. Generates sector-level rotation signal (REDUCE / OVERWEIGHT)
6. Fires push alert (Slack/email) when shift label is SIGNIFICANT
7. Persists the rotation event to run_archive.db (rotation_events table)

Scheduling (SGT — local machine time):
  Runs every second Friday at 21:00 SGT = 09:00 AM ET pre-market.

  Windows Task Scheduler (run once to register):
    schtasks /create /tn "HedgeFundRotation" ^
      /tr "cd /d \"C:\\Users\\ethan\\Documents\\Projects\\AI Hedge Fund\" && poetry run python -m src.rotation.engine" ^
      /sc WEEKLY /mo 2 /d FRI /st 21:00

Usage:
  python -m src.rotation.engine             # live run
  python -m src.rotation.engine --dry-run   # no DB write, no alert
  python -m src.rotation.engine --force     # show recs even if shift < SIGNIFICANT
"""

import argparse
import json
import os
from datetime import datetime

from colorama import Fore, Style, init as colorama_init
from dotenv import load_dotenv

load_dotenv(override=True)
load_dotenv(".env.local", override=True)

colorama_init(autoreset=True)

R  = Style.RESET_ALL
W  = Fore.WHITE
Y  = Fore.YELLOW
G  = Fore.GREEN
Rd = Fore.RED
C  = Fore.CYAN

# ── Constants ──────────────────────────────────────────────────────────────────

DATA_DIR       = os.path.join(os.path.dirname(__file__), "..", "data")
REGIME_STATE   = os.path.join(DATA_DIR, "regime_state.json")
ROTATION_STATE = os.path.join(DATA_DIR, "rotation_state.json")

ALL_AGENTS = [
    "damodaran", "graham", "ackman", "cathie_wood", "munger",
    "burry", "pabrai", "lynch", "fisher", "jhunjhunwala",
    "druckenmiller", "buffett",
]

# Shift scoring per dimension (total possible = 10)
_SHIFT_WEIGHTS = {
    "risk_appetite":     3,   # most consequential — determines entire portfolio posture
    "volatility_regime": 2,   # directly caps position sizes
    "rate_direction":    2,   # reprices growth vs value
    "recession_risk":    2,   # defensive vs cyclical tilt
    "dollar_trend":      1,   # EM/commodity overlay
}

# Recommendation thresholds (percentage-point delta)
_TRIM_THRESHOLD       = -2.0
_ADD_THRESHOLD        = +2.0
_ROTATE_OUT_THRESHOLD = -5.0   # AND new_pct < 1%

# Sector rotation rules: (regime_condition, reduce_list, overweight_list)
_SECTOR_ROTATION: list[tuple[dict, list[str], list[str]]] = [
    (
        {"risk_appetite": "risk-off", "rate_direction": "tightening"},
        ["Tech", "Consumer", "Biopharma", "Crypto"],
        ["Financials", "Energy", "Industrials"],
    ),
    (
        {"risk_appetite": "risk-off"},
        ["Tech", "Consumer", "Crypto"],
        ["Consumer Staples", "Utilities"],
    ),
    (
        {"risk_appetite": "risk-on", "rate_direction": "easing"},
        ["Utilities", "Consumer Staples"],
        ["Tech", "Consumer", "Industrials"],
    ),
    (
        {"volatility_regime": "high"},
        ["Biopharma", "Crypto"],
        [],
    ),
    (
        {"recession_risk": "high"},
        ["Tech", "Consumer", "Biopharma", "Financials", "Industrials"],
        ["Consumer Staples", "Utilities"],
    ),
    (
        {"dollar_trend": "strengthening"},
        ["Crypto"],
        [],
    ),
]


# ── Regime helpers ─────────────────────────────────────────────────────────────

def _load_previous_regime() -> dict | None:
    """Load last saved regime from regime_state.json. Returns None on first run."""
    try:
        with open(REGIME_STATE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _classify_current_regime() -> tuple[dict, dict, float]:
    """
    Fresh macro regime classification via live FMP data + LLM.
    Builds a minimal AgentState to reuse run_macro_regime_classifier().
    Returns (regime_dict, agent_weight_multipliers, position_size_cap).
    """
    from src.agents.routing.macro_regime import run_macro_regime_classifier

    today = datetime.today().strftime("%Y-%m-%d")
    state: dict = {
        "messages": [],
        "data": {
            "tickers":    [],
            "start_date": today,
            "end_date":   today,
            "analyst_signals": {},
            "portfolio":  {"cash": 100_000, "positions": {}},
            "model":      os.getenv("ANTHROPIC_DEFAULT_MODEL", "claude-sonnet-4-6"),
            "openai_api_key":             os.getenv("OPENAI_API_KEY"),
            "anthropic_api_key":          os.getenv("ANTHROPIC_API_KEY"),
            "financial_datasets_api_key": os.getenv("FINANCIAL_DATASETS_API_KEY"),
        },
        "metadata": {},
    }
    updated = run_macro_regime_classifier(state)
    return (
        updated["data"]["macro_regime"],
        updated["data"]["agent_weight_multipliers"],
        updated["data"]["position_size_cap"],
    )


# ── Shift detection ────────────────────────────────────────────────────────────

def _detect_shift(old_regime: dict, new_regime: dict) -> tuple[int, str, list[dict]]:
    """
    Compare two regime dicts across 5 dimensions.
    Returns (score, label, changed_dims_list).
    label: "SIGNIFICANT" (score >= 3) | "MINOR" (1-2) | "NONE" (0)
    """
    score = 0
    changed: list[dict] = []

    for dim, weight in _SHIFT_WEIGHTS.items():
        old_val = old_regime.get(dim, "")
        new_val = new_regime.get(dim, "")
        if old_val != new_val:
            score += weight
            changed.append({
                "dimension": dim,
                "old":       old_val,
                "new":       new_val,
                "weight":    weight,
            })

    label = "SIGNIFICANT" if score >= 3 else ("MINOR" if score >= 1 else "NONE")
    return score, label, changed


# ── Sector rotation signal ─────────────────────────────────────────────────────

def _sector_rotation_signal(new_regime: dict) -> dict[str, list[str]]:
    """
    Apply sector rotation rules against the new regime.
    Returns {"reduce": [...], "overweight": [...]} sorted by frequency.
    """
    reduce_score:     dict[str, int] = {}
    overweight_score: dict[str, int] = {}

    for condition, reduce_list, ow_list in _SECTOR_ROTATION:
        if all(new_regime.get(k) == v for k, v in condition.items()):
            for s in reduce_list:
                reduce_score[s]     = reduce_score.get(s, 0) + 1
            for s in ow_list:
                overweight_score[s] = overweight_score.get(s, 0) + 1

    return {
        "reduce":     sorted(reduce_score,     key=lambda s: (-reduce_score[s],     s)),
        "overweight": sorted(overweight_score, key=lambda s: (-overweight_score[s], s)),
    }


# ── Per-ticker recommendations ─────────────────────────────────────────────────

def _compute_recommendations(
    signals:     list[dict],
    old_weights: dict[str, float],
    new_weights: dict[str, float],
    old_cap:     float,
    new_cap:     float,
) -> list[dict]:
    """
    For each open BUY/SHORT position, compute new recommended size.

    Formula:
      regime_scale    = new_cap / old_cap
      agent_alignment = avg(new_weight for voting agents)
                      / avg(old_weight for voting agents), capped at 1.0
      new_pct         = current_pct * regime_scale * alignment
                        (hard-capped at new_cap * 15% per position)

    Recommendation:
      delta <= ROTATE_OUT_THRESHOLD AND new_pct < 1%  => ROTATE_OUT
      delta <= TRIM_THRESHOLD                          => TRIM
      delta >= ADD_THRESHOLD                           => ADD
      otherwise                                        => HOLD
    """
    recs = []

    for sig in signals:
        action      = sig.get("final_action", "HOLD")
        current_pct = sig.get("position_size_pct") or 0.0

        if action not in ("BUY", "SHORT") or current_pct <= 0:
            continue

        # Agents who voted the same direction as the final portfolio action
        voting_agents = [
            a["agent_key"] for a in sig.get("agent_votes", [])
            if a.get("signal") == action
        ]

        if voting_agents:
            avg_old   = sum(old_weights.get(a, 1.0) for a in voting_agents) / len(voting_agents)
            avg_new   = sum(new_weights.get(a, 1.0) for a in voting_agents) / len(voting_agents)
            alignment = min(1.0, avg_new / avg_old) if avg_old > 0 else 1.0
        else:
            alignment = 1.0

        regime_scale = new_cap / old_cap if old_cap > 0 else 1.0
        new_pct      = current_pct * regime_scale * alignment
        new_pct      = max(0.0, min(new_cap * 15, new_pct))   # hard per-position cap (15% max × regime cap)
        delta        = new_pct - current_pct

        if delta <= _ROTATE_OUT_THRESHOLD and new_pct < 1.0:
            rec_action = "ROTATE_OUT"
        elif delta <= _TRIM_THRESHOLD:
            rec_action = "TRIM"
        elif delta >= _ADD_THRESHOLD:
            rec_action = "ADD"
        else:
            rec_action = "HOLD"

        reason_parts = []
        if abs(old_cap - new_cap) > 0.001:
            reason_parts.append(f"Cap {old_cap:.1f}\u2192{new_cap:.1f}")
        if alignment < 0.95:
            reason_parts.append(f"Agent alignment {alignment:.2f}\u00d7")

        recs.append({
            "ticker":      sig["ticker"],
            "action":      action,
            "current_pct": round(current_pct, 2),
            "new_pct":     round(new_pct,     2),
            "delta":       round(delta,        2),
            "rec_action":  rec_action,
            "reason":      " \u00b7 ".join(reason_parts) or "Within threshold",
        })

    return recs


# ── Display ────────────────────────────────────────────────────────────────────

def _display_sector(sector_signal: dict, bar: str) -> None:
    reduce_str    = " \u00b7 ".join(sector_signal.get("reduce",     [])) or "None"
    overweight_str = " \u00b7 ".join(sector_signal.get("overweight", [])) or "None"
    print(f"  SECTOR ROTATION SIGNAL")
    print(f"  {bar}")
    print(f"  {Rd}REDUCE:{R}     {reduce_str}")
    print(f"  {G}OVERWEIGHT:{R} {overweight_str}")
    print()


def _display_report(
    run_time:      str,
    shift_score:   int,
    shift_label:   str,
    changed_dims:  list[dict],
    recs:          list[dict],
    sector_signal: dict[str, list[str]],
    dry_run:       bool,
    first_run:     bool = False,
) -> None:
    bar = "\u2500" * 58
    print(f"\n{C}{'=' * 58}{R}")
    print(f"{C}  MACRO ROTATION ENGINE \u2014 {run_time}{R}")
    print(f"{C}{'=' * 58}{R}\n")

    if first_run:
        print(f"  {Y}First run \u2014 no previous regime to compare.{R}")
        print(f"  {Y}Sector rotation signal shown for reference.{R}\n")
        _display_sector(sector_signal, bar)
        if dry_run:
            print(f"  {Y}[DRY RUN] No DB write, no alert.{R}\n")
        return

    label_color = {"SIGNIFICANT": Rd, "MINOR": Y, "NONE": G}.get(shift_label, W)
    print(f"  REGIME SHIFT: {label_color}{shift_label}{R} (score {shift_score}/10)")

    if changed_dims:
        for d in changed_dims:
            print(
                f"    {d['dimension']:<22} {str(d['old']):<14} "
                f"\u2192 {str(d['new']):<14} {Y}[+{d['weight']}]{R}"
            )
    else:
        print(f"    {G}No regime change detected{R}")
    print()

    if recs:
        print(f"  REBALANCE RECOMMENDATIONS")
        print(f"  {bar}")
        print(f"{W}  {'TICKER':<8} {'CURRENT':>8} {'RECOMMENDED':>12} {'ACTION':<12} REASON{R}")
        print(f"  {bar}")
        action_color = {"ROTATE_OUT": Rd, "TRIM": Y, "ADD": G, "HOLD": W}
        for r in recs:
            ac = action_color.get(r["rec_action"], W)
            print(
                f"  {r['ticker']:<8} "
                f"{r['current_pct']:>7.1f}%  "
                f"{r['new_pct']:>11.1f}%  "
                f"{ac}{r['rec_action']:<12}{R} "
                f"{r['reason']}"
            )
        print()
    else:
        print(f"  {G}No open positions to rebalance.{R}\n")

    _display_sector(sector_signal, bar)

    if dry_run:
        print(f"  {Y}[DRY RUN] No DB write, no alert.{R}\n")


# ── Rotation state persistence ─────────────────────────────────────────────────

def _save_rotation_state(run_time: str) -> None:
    try:
        with open(ROTATION_STATE, "w") as f:
            json.dump({"last_rotation_run": run_time}, f, indent=2)
    except Exception:
        pass


# ── Main orchestrator ──────────────────────────────────────────────────────────

def run_rotation_engine(dry_run: bool = False, force: bool = False) -> dict:
    """
    Main entry point.

    Parameters
    ----------
    dry_run : skip DB write and alert dispatch (safe for testing)
    force   : compute recommendations even when shift < SIGNIFICANT

    Returns
    -------
    result dict with keys: shift_score, shift_label, changed_dims,
    recommendations, sector_signal, old_regime, new_regime
    """
    from src.memory.run_archive import (
        get_watchlist,
        get_latest_ticker_signals,
        save_rotation_event,
    )
    from src.utils.alerts import send_rotation_alert

    run_time  = datetime.now().strftime("%Y-%m-%d %H:%M:%S SGT")
    first_run = False

    # ── 1. Load previous regime ────────────────────────────────────────────────
    prev_state = _load_previous_regime()
    if prev_state is None:
        first_run   = True
        old_regime  = {}
        old_weights = {a: 1.0 for a in ALL_AGENTS}
        old_cap     = 1.0
    else:
        old_regime  = prev_state.get("regime", {})
        old_weights = prev_state.get("agent_weights", {a: 1.0 for a in ALL_AGENTS})
        old_cap     = float(prev_state.get("position_size_cap", 1.0))

    # ── 2. Classify current regime (live LLM call ~30s) ───────────────────────
    print(f"\n  [rotation] Classifying current macro regime...")
    new_regime, new_weights, new_cap = _classify_current_regime()

    # ── 3. First run: no comparison possible → treat as no shift ──────────────
    if first_run:
        old_regime  = new_regime
        old_weights = new_weights
        old_cap     = new_cap

    # ── 4. Detect shift ────────────────────────────────────────────────────────
    shift_score, shift_label, changed_dims = _detect_shift(old_regime, new_regime)

    # ── 5. Load open positions from archive ───────────────────────────────────
    watchlist = get_watchlist()
    signals   = get_latest_ticker_signals(watchlist) if watchlist else []

    # ── 6. Per-ticker recommendations (only when shift > 0 or forced) ─────────
    recs = []
    if shift_score > 0 or force:
        recs = _compute_recommendations(signals, old_weights, new_weights, old_cap, new_cap)

    # ── 7. Sector rotation signal ──────────────────────────────────────────────
    sector_signal = _sector_rotation_signal(new_regime)

    # ── 8. Display ────────────────────────────────────────────────────────────
    _display_report(
        run_time, shift_score, shift_label, changed_dims,
        recs, sector_signal, dry_run, first_run,
    )

    result = {
        "shift_score":     shift_score,
        "shift_label":     shift_label,
        "changed_dims":    changed_dims,
        "recommendations": recs,
        "sector_signal":   sector_signal,
        "old_regime":      old_regime,
        "new_regime":      new_regime,
    }

    # ── 9. Persist + alert (SIGNIFICANT only, unless forced) ──────────────────
    if not dry_run and not first_run and (shift_label == "SIGNIFICANT" or force):
        alert_sent = False
        if shift_label == "SIGNIFICANT":
            try:
                send_rotation_alert(result)
                alert_sent = True
                print("  [rotation] Alert sent.")
            except Exception as exc:
                print(f"  [rotation] Alert error: {exc}")
        save_rotation_event(result, alert_sent=alert_sent)
        _save_rotation_state(run_time)

    return result


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="AI Hedge Fund — Macro Rotation Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print report without writing to DB or sending alerts",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Compute recommendations even when shift score < SIGNIFICANT threshold",
    )
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    run_rotation_engine(dry_run=args.dry_run, force=args.force)


if __name__ == "__main__":
    main()
