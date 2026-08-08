"""
Trigger state persistence.

Tracks the last-fired date per ticker per trigger type to prevent
duplicate pipeline runs on the same event.

State file: src/data/trigger_state.json

Format:
{
  "NVDA": {
    "price_shock": "2026-03-20",   // date cooldown — fires once per calendar day
    "earnings":    "2026-03-27",   // keyed to the EARNINGS DATE, not check date
    "form4":       "2026-03-19"    // date cooldown — fires once per calendar day
  }
}

Cooldown semantics
------------------
price_shock / form4  : key_date = today's date — daily cooldown.
earnings             : key_date = the actual earnings date string.
                       Won't re-fire for the same earnings event even if the
                       monitor runs multiple days before the event.
"""

import json
import os

from src.utils.json_state import load_json_locked, save_json_locked

_STATE_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "trigger_state.json")


def load_state(path: str | None = None) -> dict:
    """Load trigger state from disk. Returns {} if file missing or corrupt.

    Pass `path` to use a separate state file from a different trigger
    namespace (e.g. hundred_q's own state file) — two systems tracking
    unrelated tickers/cadences under the same trigger name (like
    "price_shock" with different thresholds) would otherwise collide if
    they shared one JSON file.
    """
    target = path or _STATE_PATH
    data = load_json_locked(target, default={})
    return data if isinstance(data, dict) else {}


def save_state(state: dict, path: str | None = None) -> None:
    """Persist trigger state to disk. See load_state() for the `path` param."""
    target = path or _STATE_PATH
    save_json_locked(target, state)


def already_fired(state: dict, ticker: str, trigger: str, key_date: str) -> bool:
    """
    Returns True if this trigger+key_date combo was already fired for ticker.

    Parameters
    ----------
    state    : dict loaded by load_state()
    ticker   : e.g. "NVDA"
    trigger  : "price_shock" | "earnings" | "form4"
    key_date : for price_shock/form4 — today's date; for earnings — earnings date
    """
    return state.get(ticker, {}).get(trigger) == key_date


def mark_fired(state: dict, ticker: str, trigger: str, key_date: str) -> None:
    """Record that this trigger fired for ticker. Mutates state in-place."""
    if ticker not in state:
        state[ticker] = {}
    state[ticker][trigger] = key_date
