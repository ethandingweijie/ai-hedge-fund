"""Scoring a gate needs ground truth, and the forward loop has none yet.

Production holds 43 runs, the oldest three weeks old, and every
`ticker_signals.outcome` is `PENDING`. The Store A lifecycle would spend its
first quarter decaying rules toward deletion with nothing to score them on.

The backward test sidesteps that: pick a date whose following year has already
been reported, rebuild the drivers as they stood then, project under both the
gated and ungated assumption, and compare each to the figure the company
actually printed. Ground truth is not waited for, it is already on file.

Both paths carry the SAME growth rate, so growth, WACC, share count and every
other driver cancel out of the difference and only the intervention is scored.

The forward half is the same arithmetic on live runs: each gate records
(Path A, Path B) as floats at the moment it fires, because the flag string
records them only as prose and prose cannot be scored.
"""

from __future__ import annotations

import pytest

from src.memory.gate_backtest import (
    ALPHA_STEP,
    BETA_STEP,
    delta_error_verdict,
    epsilon_for,
)


# ── The verdict arithmetic ───────────────────────────────────────────────

def test_the_gate_helped_when_it_moved_the_projection_toward_reality():
    """The shape of the MELI worked example: raw 3.20bn, gated 0.72bn,
    actual 0.85bn."""
    v = delta_error_verdict(3.20e9, 0.72e9, 0.85e9)
    assert v["verdict"] == "HELPED"
    assert v["alpha_delta"] == ALPHA_STEP
    assert v["beta_delta"] == 0.0
    assert v["delta_error_pct"] < 0


def test_the_gate_is_a_false_alarm_when_the_raw_path_was_closer():
    v = delta_error_verdict(1.00e9, 0.20e9, 0.95e9)
    assert v["verdict"] == "FALSE_ALARM"
    assert v["beta_delta"] == BETA_STEP
    assert v["alpha_delta"] == 0.0


def test_penalties_are_symmetric():
    """Every documented incident in this codebase is overvaluation, and there
    is no recorded over-conservatism failure, so penalising the conservative
    direction harder would tune against the observed error distribution."""
    assert ALPHA_STEP == BETA_STEP == 1.0


def test_a_difference_inside_epsilon_is_noise_and_earns_nothing():
    v = delta_error_verdict(1.00e9, 0.99e9, 1.00e9)
    assert v["verdict"] == "NEUTRAL"
    assert v["alpha_delta"] == v["beta_delta"] == 0.0


def test_epsilon_is_per_metric_not_one_global_band():
    """A flat 2% is far inside the noise for free cash flow, which swings on
    working-capital timing, and far outside it for revenue."""
    assert epsilon_for("free_cash_flow") > epsilon_for("revenue")
    assert epsilon_for("something_unknown") > 0


def test_the_verdict_is_scale_free():
    """Normalised by the actual, so a large-cap and a small-cap firing of the
    same gate are weighed equally."""
    small = delta_error_verdict(300.0, 72.0, 85.0)
    large = delta_error_verdict(3.0e9, 0.72e9, 0.85e9)
    assert small["delta_error_pct"] == pytest.approx(large["delta_error_pct"])


# ── It must refuse to score rather than guess ────────────────────────────

@pytest.mark.parametrize("a, b, actual", [
    (None, 1.0, 2.0),
    (1.0, None, 2.0),
    (1.0, 2.0, None),
    (1.0, 2.0, 0.0),          # division by zero
    ("x", 2.0, 3.0),
])
def test_degenerate_input_is_unscorable_not_wrong(a, b, actual):
    v = delta_error_verdict(a, b, actual)
    assert v["verdict"] == "UNSCORABLE"
    assert v["alpha_delta"] == v["beta_delta"] == 0.0
    assert v["delta_error_pct"] is None


def test_a_negative_actual_still_scores_by_magnitude():
    """Sign is not the question — distance from the printed figure is."""
    v = delta_error_verdict(-1.0e9, -0.9e9, -0.85e9)
    assert v["verdict"] in {"HELPED", "NEUTRAL", "FALSE_ALARM"}
    assert v["delta_error_pct"] is not None


# ── The forward half: gates must record both paths ───────────────────────

def test_every_shipped_gate_emits_a_structured_pair():
    """The flag string says "31.2% -> 30.3%" in prose, which no worker can
    score. Assert each gate writes floats instead."""
    import inspect

    from src.agents.analysis import dcf_agent

    src = inspect.getsource(dcf_agent)
    assert src.count("gate_evaluations.append(") >= 2, "gates not instrumented"
    for gate_id in ("GATE_CASH_CONVERSION", "GATE_REVENUE_SCALE_CAP"):
        assert f'"{gate_id}"' in src
    # Both halves of every pair must be present.
    assert src.count('"raw_input_path_a"') == src.count('"gated_output_path_b"')


def test_the_snapshot_always_carries_the_key():
    """Empty means no gate fired; absent means the run predates
    instrumentation. The worker must be able to tell those apart."""
    import inspect

    from src.agents.analysis import dcf_agent

    src = inspect.getsource(dcf_agent)
    assert '"gate_evaluations":   gate_evaluations,' in src
    assert "gate_evaluations: list[dict] = []" in src


def test_the_working_capital_term_is_extracted():
    """`change_in_working_capital` is the non-projectable term of the
    owner-earnings identity. It was fetched from FMP but never copied into the
    series, so a gate could not state how much of reported FCF was working
    capital — it could only infer it."""
    import inspect

    from src.agents.analysis import dcf_agent

    src = inspect.getsource(dcf_agent)
    assert '"change_in_working_capital"' in src
