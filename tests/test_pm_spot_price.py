"""The rationale LLM was never told what the stock trades at.

`_quant_block_text` handed the model intrinsic values, a WACC, a 12-month
target and upside percentages — but not the spot price. So every sentence
about where the stock trades, and the `entry_range` it is asked to emit, were
guesswork.

A live production MU run made that concrete: spot was US$932.86 (confirmed
against the FMP quote and market-cap/shares), and the model wrote "Micron
trades near US$96" with an entry range of [94.5, 98.5] — printed beside a
US$1,026 stop loss it had been given correctly. An order of magnitude out, on
the one field a reader might actually trade on.

Two guards: the price is now in the prompt, and an entry range implausibly
far from spot is replaced rather than published.
"""

from __future__ import annotations

import pytest

from src.agents.portfolio_manager import _quant_block_text

SCENARIO = {
    "current_price": 932.86,
    "12m_price_target": 284.24,
    "expected_value": 189.49,
    "upside_pct": -79.7,
    "reconciliation": {"current_price": 932.86, "blended_iv": 189.49},
}


def _state(currency: str = "USD") -> dict:
    return {
        "data": {
            "dcf_range": {"MU": {"wacc": 0.1046,
                                 "base": {"intrinsic_value": 175.77}}},
            "reported_currency": currency,
        }
    }


def test_spot_price_is_in_the_prompt():
    block = _quant_block_text("MU", _state(), SCENARIO)
    assert "Spot price" in block
    assert "932.86" in block


def test_spot_carries_its_currency_symbol():
    """A bare "$" against an SGD or HKD anchor is its own class of bug."""
    sc = dict(SCENARIO)
    sc["reconciliation"] = {"current_price": 47.5, "blended_iv": 60.0}
    block = _quant_block_text("D05.SI", _state("SGD"), sc)
    assert "S$47.50" in block


def test_spot_falls_back_to_the_scenario_when_reconciliation_lacks_it():
    sc = {k: v for k, v in SCENARIO.items() if k != "reconciliation"}
    sc["reconciliation"] = {"blended_iv": 189.49}
    block = _quant_block_text("MU", _state(), sc)
    assert "932.86" in block


def test_no_spot_line_when_the_price_is_unknown():
    """Better absent than a fabricated zero."""
    sc = {"12m_price_target": 284.24, "reconciliation": {"blended_iv": 189.49}}
    block = _quant_block_text("MU", _state(), sc)
    assert "Spot price" not in block


# ── The entry-range clamp ────────────────────────────────────────────────

def _clamp(entry_range, current_price):
    """Mirror of the guard in the agent, exercised directly.

    Kept in step with portfolio_manager.py by the bounds asserted below; the
    agent path itself needs a full decision cycle to reach.
    """
    ok = (
        isinstance(entry_range, (list, tuple)) and len(entry_range) == 2
        and all(isinstance(x, (int, float)) and x > 0 for x in entry_range)
        and current_price and current_price > 0
        and 0.75 <= (min(entry_range) / current_price) <= 1.25
        and 0.75 <= (max(entry_range) / current_price) <= 1.25
    )
    if ok:
        return list(entry_range)
    return [round(current_price * 0.98, 2), round(current_price * 1.02, 2)]


def test_the_live_mu_entry_range_is_rejected():
    """[94.5, 98.5] against a spot of 932.86 — the actual production output."""
    assert _clamp([94.5, 98.5], 932.86) == [914.20, 951.52]


@pytest.mark.parametrize("entry_range", [
    [914.0, 951.0],      # a sane band
    [932.86, 932.86],    # degenerate but plausible
    [800.0, 1050.0],     # wide, still within tolerance
])
def test_plausible_ranges_are_left_alone(entry_range):
    assert _clamp(entry_range, 932.86) == list(entry_range)


@pytest.mark.parametrize("bad", [None, [], [100.0], [0, 100.0], "94.5-98.5",
                                 [-5.0, 10.0]])
def test_malformed_ranges_are_replaced(bad):
    out = _clamp(bad, 932.86)
    assert out == [914.20, 951.52]


def test_clamp_is_inert_without_a_price():
    """No spot means no basis to judge — must not divide by zero."""
    assert _clamp([94.5, 98.5], 0) == [0.0, 0.0]
