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


# ── Scoring the claim the gate actually makes ────────────────────────────
#
# The first harness scored the cash-conversion gate on year-1 reported FCF and
# marked it a false alarm on MELI. But the gate never claimed to predict
# reported FCF — it claims the working-capital portion is not repeatable and
# must not be capitalised for ten years. Two corrections follow.

from src.memory.gate_backtest import (
    _owner_earnings_margin,
    realised_owner_earnings_margin,
)


def test_owner_earnings_strips_the_working_capital_term():
    """MELI FY25: $10.77bn reported FCF on $28.89bn revenue is a 37.3%
    margin, but $5.92bn of it is working-capital inflow."""
    m = _owner_earnings_margin({
        "revenue": 28.893e9, "free_cash_flow": 10.773e9,
        "change_in_working_capital": 5.923e9,
    })
    assert m == pytest.approx(0.1679, abs=0.001)


def test_a_working_capital_outflow_raises_owner_earnings():
    """Sign matters: a build in working capital depresses reported FCF, so
    removing it must move the margin UP, not down."""
    m = _owner_earnings_margin({
        "revenue": 1000.0, "free_cash_flow": 50.0,
        "change_in_working_capital": -30.0,
    })
    assert m == pytest.approx(0.08)


def test_a_missing_working_capital_line_degrades_to_reported_fcf():
    """Absent ΔWC must not be read as a huge adjustment."""
    assert _owner_earnings_margin(
        {"revenue": 100.0, "free_cash_flow": 10.0}) == pytest.approx(0.10)


@pytest.mark.parametrize("row", [
    {"revenue": 0, "free_cash_flow": 10},
    {"revenue": -100, "free_cash_flow": 10},
    {"revenue": 100},
    {},
])
def test_a_degenerate_row_yields_no_margin(row):
    assert _owner_earnings_margin(row) is None


def test_the_metric_is_a_margin_so_growth_cannot_pollute_it():
    """Scoring cash levels required projecting both paths forward, and the
    growth assumption then swamped the intervention — BABA and JPM showed
    300-450% error on BOTH paths. Comparing margin to margin removes the
    growth rate, the revenue base and the compounding entirely."""
    v = delta_error_verdict(0.232, 0.064, 0.168,
                            metric="owner_earnings_margin")
    assert v["verdict"] == "FALSE_ALARM"      # raw 6.4pp off, gated 10.4pp off
    # Same margins, any revenue scale, same verdict.
    assert epsilon_for("owner_earnings_margin") > 0


def test_realised_uses_only_years_after_the_as_of_date(monkeypatch):
    """Ground truth must be a figure that was unknowable at the as-of date."""
    import src.memory.gate_backtest as gb

    rows = [
        {"period": "2023-12-31", "revenue": 100.0, "free_cash_flow": 40.0},
        {"period": "2024-12-31", "revenue": 100.0, "free_cash_flow": 10.0},
        {"period": "2025-12-31", "revenue": 100.0, "free_cash_flow": 20.0},
    ]
    monkeypatch.setattr(gb, "_series", lambda *a, **k: rows)
    mean, n, periods = gb.realised_owner_earnings_margin(
        "X", "2023-12-31", "2026-08-30")
    assert periods == ["2024-12-31", "2025-12-31"]
    assert n == 2
    assert mean == pytest.approx(0.15)        # the 2023 year must not leak in


def test_realised_averages_rather_than_taking_one_year(monkeypatch):
    """"Terminal" means the level that persists; a single year is dominated
    by timing."""
    import src.memory.gate_backtest as gb

    rows = [
        {"period": "2023-12-31", "revenue": 100.0, "free_cash_flow": 0.0},
        {"period": "2024-12-31", "revenue": 100.0, "free_cash_flow": 60.0},
        {"period": "2025-12-31", "revenue": 100.0, "free_cash_flow": 0.0},
    ]
    monkeypatch.setattr(gb, "_series", lambda *a, **k: rows)
    mean, n, _ = gb.realised_owner_earnings_margin("X", "2023-12-31",
                                                   "2026-08-30")
    assert n == 2 and mean == pytest.approx(0.30)


def test_no_reported_year_yet_is_unscorable_not_zero(monkeypatch):
    import src.memory.gate_backtest as gb
    monkeypatch.setattr(gb, "_series", lambda *a, **k: [
        {"period": "2024-12-31", "revenue": 100.0, "free_cash_flow": 10.0}])
    mean, n, periods = gb.realised_owner_earnings_margin(
        "X", "2024-12-31", "2026-08-30")
    assert mean is None and n == 0 and periods == []
