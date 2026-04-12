"""
Phase 10 — Post-Trade Review Agent

What it does:
- Reads trade_log.json (appended to after every advanced pipeline run)
- For each past decision older than review_days (default 30):
    1. Fetches the current price of the ticker
    2. Compares to the price at decision time
    3. Scores: CORRECT (moved >5% in signal direction) | NEUTRAL | INCORRECT
- Updates conviction_weights.json:
    CORRECT  → agent_weight × 1.1  (cap 2.0)
    NEUTRAL  → unchanged
    INCORRECT → agent_weight × 0.9 (floor 0.5)
- Writes updated weights back to disk
- Stores summary in state["data"]["post_trade_review"]

On the first run, trade_log.json is empty → no-op, returns immediately.
After 30+ days of running the pipeline on the same tickers, weights will start
differentiating based on real track record.
"""

import json
import os
from datetime import datetime, timedelta

from src.graph.state import AgentState
from src.tools.api import get_prices
from src.utils.progress import progress
from src.utils.api_key import get_api_key_from_state
from src.memory.run_archive import update_outcomes
from src.memory.reweight import reweight_from_archive, print_reweight_report

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
TRADE_LOG_PATH = os.path.join(DATA_DIR, "trade_log.json")
CONVICTION_WEIGHTS_PATH = os.path.join(DATA_DIR, "conviction_weights.json")

ALL_AGENTS = [
    "damodaran", "graham", "ackman", "cathie_wood", "munger",
    "burry", "pabrai", "lynch", "fisher", "jhunjhunwala",
    "druckenmiller", "buffett",
]


def _load_trade_log() -> list[dict]:
    try:
        with open(TRADE_LOG_PATH) as f:
            return json.load(f)
    except Exception:
        return []


def _load_conviction_weights() -> dict[str, float]:
    try:
        with open(CONVICTION_WEIGHTS_PATH) as f:
            raw = json.load(f)
        return {k: float(v) for k, v in raw.items() if k in ALL_AGENTS}
    except Exception:
        return {a: 1.0 for a in ALL_AGENTS}


def _save_conviction_weights(weights: dict[str, float], total_reviews: int) -> None:
    data = dict(weights)
    data["last_updated"] = datetime.now().strftime("%Y-%m-%d")
    data["total_reviews"] = total_reviews
    with open(CONVICTION_WEIGHTS_PATH, "w") as f:
        json.dump(data, f, indent=2)


def run_post_trade_review(state: AgentState, review_days: int = 30) -> AgentState:
    """Phase 10: score past calls and update conviction weights."""
    agent_id = "post_trade_review"
    api_key = get_api_key_from_state(state, "FINANCIAL_DATASETS_API_KEY")
    current_end_date = state["data"]["end_date"]

    progress.update_status(agent_id, None, "Loading trade log")

    trade_log = _load_trade_log()
    if not trade_log:
        progress.update_status(agent_id, None, "No prior trades to review — skipping")
        state["data"]["post_trade_review"] = {
            "reviewed": 0,
            "message": "No prior trades in trade_log.json",
        }
        return state

    cutoff_date = datetime.strptime(current_end_date, "%Y-%m-%d") - timedelta(days=review_days)
    reviewable = [
        entry for entry in trade_log
        if entry.get("run_date") and
        datetime.strptime(entry["run_date"], "%Y-%m-%d") <= cutoff_date
    ]

    if not reviewable:
        progress.update_status(agent_id, None, f"No trades older than {review_days} days — skipping")
        state["data"]["post_trade_review"] = {
            "reviewed": 0,
            "message": f"No trades older than {review_days} days yet.",
        }
        return state

    weights = _load_conviction_weights()
    agent_scores: dict[str, list[str]] = {a: [] for a in ALL_AGENTS}
    reviewed_count = 0

    for entry in reviewable:
        tickers = entry.get("tickers", [])
        decisions = entry.get("decisions", {})
        agent_signals = entry.get("analyst_signals", {})
        decision_date = entry.get("date", entry.get("run_date"))

        for ticker in tickers:
            ticker_decision = decisions.get(ticker, {})
            original_action = ticker_decision.get("action", "HOLD")

            if original_action == "HOLD":
                continue  # No directional bet to score

            # Fetch price at decision date and current price
            try:
                old_prices = get_prices(ticker, decision_date, decision_date, api_key=api_key)
                new_prices = get_prices(ticker, current_end_date, current_end_date, api_key=api_key)

                if not old_prices or not new_prices:
                    continue

                price_then = old_prices[-1].close
                price_now = new_prices[-1].close
                pct_change = (price_now - price_then) / price_then * 100

            except Exception:
                continue

            # Score this ticker in the SQLite archive using the same current price
            # (update_outcomes handles its own days_back filter; days_back=0 here
            #  because post_trade_review already filtered reviewable entries above)
            try:
                update_outcomes(
                    ticker=ticker,
                    price_at_review=float(price_now),
                    review_date=current_end_date,
                    days_back=0,
                )
            except Exception:
                pass  # Archive scoring is best-effort — never block the pipeline

            # Score: CORRECT if moved >5% in signal direction
            if original_action in ("BUY", "COVER"):
                if pct_change > 5:
                    call_score = "CORRECT"
                elif pct_change < -5:
                    call_score = "INCORRECT"
                else:
                    call_score = "NEUTRAL"
            elif original_action in ("SELL", "SHORT"):
                if pct_change < -5:
                    call_score = "CORRECT"
                elif pct_change > 5:
                    call_score = "INCORRECT"
                else:
                    call_score = "NEUTRAL"
            else:
                continue

            # Update each agent's score for this ticker
            for agent_key, sigs in agent_signals.items():
                if not isinstance(sigs, dict) or ticker not in sigs:
                    continue
                # Map agent key to short key
                short_key = None
                for candidate in ALL_AGENTS:
                    if candidate in agent_key:
                        short_key = candidate
                        break
                if not short_key:
                    continue

                agent_signal = sigs[ticker].get("signal", "HOLD")
                if agent_signal == "HOLD":
                    continue

                # Did this agent agree with the final decision?
                if (original_action in ("BUY", "COVER") and agent_signal == "BUY") or \
                   (original_action in ("SELL", "SHORT") and agent_signal in ("SELL", "SHORT")):
                    agent_scores[short_key].append(call_score)

            reviewed_count += 1

    # Apply weight updates
    updates_applied = []
    for agent, scores in agent_scores.items():
        if not scores:
            continue
        correct = scores.count("CORRECT")
        incorrect = scores.count("INCORRECT")
        net = correct - incorrect
        old_w = weights.get(agent, 1.0)
        if net > 0:
            new_w = min(2.0, old_w * (1.1 ** net))
        elif net < 0:
            new_w = max(0.5, old_w * (0.9 ** abs(net)))
        else:
            new_w = old_w
        weights[agent] = round(new_w, 3)
        if new_w != old_w:
            updates_applied.append(
                f"{agent}: {old_w:.2f} → {new_w:.2f} "
                f"({correct}C/{incorrect}I/{len(scores)-correct-incorrect}N)"
            )

    # Load existing total_reviews count
    try:
        with open(CONVICTION_WEIGHTS_PATH) as f:
            existing = json.load(f)
        total_reviews = existing.get("total_reviews", 0) + reviewed_count
    except Exception:
        total_reviews = reviewed_count

    _save_conviction_weights(weights, total_reviews)

    # ── Archive-based reweighting (runs automatically after trade log scoring) ──
    # Reads all scored outcomes from the SQLite archive (not just this batch)
    # and recomputes conviction weights from the full historical track record.
    # min_reviews=3 prevents noise from single-call flukes.
    archive_updates: dict = {}
    try:
        archive_updates = reweight_from_archive(min_reviews=3, dry_run=False)
        if archive_updates:
            print_reweight_report(archive_updates, dry_run=False)
    except Exception as e:
        progress.update_status(agent_id, None, f"Archive reweight skipped: {e}")

    summary = {
        "reviewed": reviewed_count,
        "weight_updates": updates_applied,
        "updated_weights": weights,
        "archive_reweight": {
            agent: {"old": v["old"], "new": v["new"], "net": v["net"]}
            for agent, v in archive_updates.items()
        },
    }

    progress.update_status(
        agent_id, None,
        f"Reviewed {reviewed_count} trade(s). {len(updates_applied)} weight(s) updated. "
        f"Archive reweight: {len(archive_updates)} agent(s) adjusted."
    )

    state["data"]["post_trade_review"] = summary
    return state
