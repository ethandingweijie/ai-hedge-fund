"""Item 3b: the audit fields that drove two gates are now persisted.

`forward_roic`, `roic_source`, `_sector_g_avg` and its cohort, the composite
bridge (including `bank_clamp`) and `normalized_net_income` all existed as
locals and none of them reached the payload. The consequence was that the Gate B
`md_abs * 10` defect — a defect in the arithmetic that produces `forward_roic` —
had to be measured by regex-ing the value back out of a sentence:

    "Gate B (bear): Forward ROIC (-7.3% [Y10 projected]) < threshold (5.9%)"

That is what `scratchpad/census_gateB_prod.py` does across 49 production rows,
and it is what `scratchpad/verify_gateB_prod.py` had to do to confirm the fix
live. A value that decides whether terminal growth is zeroed should be a field,
not a substring. Confirmed absent from production before this change: null on
four fresh post-fix rows for SCHW, V, MELI and MSTR.

These tests are the baseline-independent half. The half that pins actual values
lives in `test_valuation_fixes_0917f.py`, which reads the regenerated snapshot.
"""
from __future__ import annotations

import inspect
from pathlib import Path

from src.agents.analysis import dcf_agent
from src.memory import golden_replay as gr

_ROOT = Path(__file__).resolve().parents[1]

#: The new per-scenario leaves.
_SCENARIO_AUDIT = ("forward_roic", "roic_source", "sector_g_avg",
                   "sector_g_avg_basis")

#: The new top-level leaves.
_TOP_AUDIT = ("composite_bridge", "normalized_net_income")


# ── A. The payload writes them ───────────────────────────────────────────────


def _run_src() -> str:
    return inspect.getsource(dcf_agent.run_dcf_agent)


def test_every_new_scenario_leaf_is_written_into_the_payload():
    src = _run_src()
    for key in _SCENARIO_AUDIT:
        assert f'"{key}":' in src, f"{key} is not persisted per scenario"


def test_every_new_top_level_leaf_is_written_into_the_payload():
    src = _run_src()
    for key in _TOP_AUDIT:
        assert f'"{key}":' in src, f"{key} is not persisted at top level"


def test_the_audit_locals_are_initialised_before_the_conditional_that_sets_them():
    """`_sector_g_avg` is assigned inside the growth-premium block, which is
    conditional. An unbound local at the payload assembly would turn "this
    scenario never computed a sector growth average" into a NameError — a crash
    in place of a missing field. The init must sit at the scenario-loop body
    level, above the block."""
    lines = _run_src().splitlines()
    init = [i for i, ln in enumerate(lines)
            if "_sector_g_avg: Optional[float] = None" in ln]
    assign = [i for i, ln in enumerate(lines)
              if "_sector_g_avg = _peer_for_gp.get(" in ln]
    payload = [i for i, ln in enumerate(lines) if '"sector_g_avg":' in ln]
    assert len(init) == 1 and len(assign) == 1 and len(payload) == 1
    assert init[0] < assign[0] < payload[0]


def test_the_basis_leaf_is_read_off_the_peer_provenance_not_guessed():
    """`_comp_basis` is stamped for EVERY field by `get_sector_peer_multiples`,
    including the ones that fell back to the static table — that stamping was
    added after the 2026-09-13 comps outage ran undetected for 17 days. Reading
    the cohort from anywhere else would re-introduce the silence."""
    src = _run_src()
    assert '_peer_for_gp.get("_comp_basis")' in src
    assert '.get("growth_avg")' in src


def test_the_divergence_is_documented_at_the_call_site():
    """`_peer_for_gp` passes no `market_cap` while the three legs pass a real
    one. That is a live divergence with a measured size, deliberately left in
    place for its own commit. A comment that says so is what stops the next
    reader from "fixing" it as drive-by cleanup and shipping an unattributable
    400-grouping golden move."""
    src = _run_src()
    i = src.index("_peer_for_gp = get_sector_peer_multiples(")
    comment = src[max(0, i - 2000):i]
    assert "market_cap" in comment
    assert "401 of the 436" in comment


# ── B. golden_replay pins them, so removing one fails the baseline ───────────


def test_the_new_scenario_leaves_are_in_the_projection():
    for key in _SCENARIO_AUDIT:
        assert key in gr._SCENARIO_KEYS, (
            f"{key} is persisted but not projected, so the golden baseline "
            f"cannot see it and a silent removal would ship")


def test_the_new_top_level_leaves_are_in_the_projection():
    assert "composite_bridge" in gr._DICT_KEYS
    assert "normalized_net_income" in gr._SCALAR_KEYS


def test_composite_bridge_is_carried_as_a_dict_not_flattened_away():
    """It is a nested block, so it belongs in `_DICT_KEYS`; `_flatten` expands
    it into dotted leaves, which is what makes a single changed sub-score
    visible in a diff."""
    assert isinstance(gr._DICT_KEYS, tuple)
    assert "composite_bridge" in gr._DICT_KEYS


# ── C. The composite bridge actually carries what an audit needs ─────────────


def test_the_bridge_holds_the_three_sub_scores_and_the_clamp():
    """Decision 1 narrowed `bank_clamp` to a real bank test. The golden
    baseline could not see that change at all, because the bridge was never
    persisted — only the resulting multiple was, and a narrowing that leaves the
    multiple unchanged is invisible. Pinning the bridge's KEY SET here means the
    next change to it has to be deliberate."""
    src = inspect.getsource(dcf_agent)
    assert '_composite_bridge["bank_clamp"]' in src
    # The three sub-scores the log line prints.
    for part in ("quality", "risk", "commodity", "composite_score"):
        assert f"'{part}'" in src or f'"{part}"' in src, part


def test_bank_clamp_is_written_only_under_a_bank_test():
    """The clamp assignment must sit inside a condition, not apply to every
    profile — that was Decision 1."""
    src = inspect.getsource(dcf_agent)
    i = src.index('_composite_bridge["bank_clamp"]')
    preceding = src[max(0, i - 1200):i]
    assert "if " in preceding, "bank_clamp appears to be assigned unconditionally"
