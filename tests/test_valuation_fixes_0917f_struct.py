"""Item 3b/3a: the audit fields are persisted, and the peer call sites agree.

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

Persisting `_sector_g_avg_basis` is also what made item 3a measurable. The cohort
a name resolves is not otherwise in the payload, so the `market_cap` divergence
between `_peer_for_gp` and the three legs could only be re-derived from source;
once the basis was a field, the 3b baseline showed every HK/SG fixture resolving
`all` and never `large` — the divergence, read off data instead of read off code.
Section A2 pins the alignment itself as a source-shape guard, for the reason
given there: the golden baseline resolves comps from a local store that is a
fifth the size of production's, so a value guard would not see a regression.

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


# ── A2. Item 3a: every peer call site resolves the same size cohort ──────────


def test_every_peer_call_site_resolves_market_cap_the_same_way():
    """Item 3a aligned the five peer call sites; item 2 removed the copies.

    They must all pass the SAME `market_cap`. They did not: `_peer_for_gp`
    omitted the argument entirely (defaulting to 0.0 → None) and the 12m call
    read `or 0.0` where the three legs read `or revenue_base * 10`, so for any
    HK/SG name whose quote carries no market cap the growth premium was computed
    against the whole-grouping cohort while the peer multiples it scaled came off
    the size-matched one — and the 12m target was priced off whole-grouping
    multiples while the provenance recorded beside it claimed a size-matched set.

    3a fixed that by writing the legs' expression at all five sites. Five
    identical expressions is still five chances to edit one of them, and nothing
    but this test would notice, so it is now bound ONCE as `resolved_mcap` and
    passed by name. That makes the divergence structurally impossible rather than
    merely asserted-against — which is the whole reason the guard exists in this
    shape: the golden baseline resolves comps from the LOCAL store, which holds
    190 `growth_avg` rows against production's 1057, and three of the five HK/SG
    groupings in the fixture set store no `large` rung at all. A divergence
    reintroduced here would move production and leave the baseline almost
    entirely unchanged. Production SCHW is the proof that "almost" is not
    "entirely": it resolves `industry/large n=10` at +28.22% live while its
    fixture says `static/US`, because the local store has no US comps rows and
    production's weekly refresh does.
    """
    src = _run_src()
    # One binding, and the guard that goes with it — `revenue_base` is
    # `most_recent["revenue"]` and can be None, so the bare `or revenue_base * 10`
    # raised TypeError on a name with neither a quote cap nor a revenue line.
    assert src.count("resolved_mcap: float | None = (") == 1, (
        "resolved_mcap must be bound exactly once")
    assert "revenue_base * 10.0 if revenue_base else None" in src, (
        "the falsy-revenue_base guard is gone, so a name with no revenue and no "
        "quote cap raises TypeError instead of resolving no size")
    # Five uses of that one binding, and no surviving copy of either expression.
    assert src.count("market_cap=resolved_mcap") == 5, (
        f"expected the five aligned call sites to pass resolved_mcap, found "
        f"{src.count('market_cap=resolved_mcap')}")
    assert "_market_cap or revenue_base" not in src, (
        "a call site has gone back to inlining the fallback expression")
    assert "_market_cap or 0.0" not in src
    assert "market_cap=0.0" not in src
    # `_compute_method_value` — which lives outside `run_dcf_agent` — receives
    # the size as a parameter and forwards it verbatim. A call site that passed
    # a literal instead would silently un-align the leg.
    assert ("ticker=ticker, market_cap=market_cap)"
            in inspect.getsource(dcf_agent._compute_method_value))


def test_resolved_mcap_is_bound_after_both_of_its_inputs_are_final():
    """The one hazard hoisting introduces, guarded directly.

    `revenue_base` is assigned TWICE — once from the most recent annual row and
    again inside the FX block, which multiplies the whole series by the rate and
    re-derives the anchored scalars. `_market_cap` is assigned once, from the
    quote, and only when the quote carried no cap of its own. A binding placed
    above either would capture a stale value and produce a size in the wrong
    currency or no size at all — silently, because the result is still a float.

    So the ordering is the assertion: one binding, below all three assignments,
    above all five uses. Line indices rather than values, because the values are
    what the ordering exists to get right.
    """
    lines = _run_src().splitlines()

    def _idx(pred):
        return [i for i, ln in enumerate(lines) if pred(ln)]

    rev = _idx(lambda ln: ln.strip() == 'revenue_base = most_recent["revenue"]')
    cap = _idx(lambda ln: "_market_cap = _close * shares" in ln)
    bind = _idx(lambda ln: ln.strip().startswith("resolved_mcap: float | None"))
    uses = _idx(lambda ln: "market_cap=resolved_mcap" in ln)
    assert len(rev) == 2, rev        # the anchor, and the post-FX re-derivation
    assert len(cap) == 1, cap
    assert len(bind) == 1, bind
    assert len(uses) == 5, uses
    assert max(rev + cap) < bind[0], (
        "resolved_mcap is bound above an assignment to one of its inputs")
    assert bind[0] < min(uses), "resolved_mcap is used before it is bound"


def _comment_above(src: str, needle: str) -> str:
    """The contiguous `#` block immediately above the first line containing
    `needle`.

    Every "the comment still says X" test in this module used to slice a fixed
    character budget backwards from the call site. That budget is a proxy for
    "the block above", and it degrades silently: lengthen the comment and the
    window starts partway down it, so the test fails on text that is present and
    merely out of reach. Reading the block by its own shape means the test asks
    the question it is written to ask, at any length.
    """
    i = src.index(needle)
    # `index` lands mid-line, on the needle itself; the remainder of that line
    # would be a whitespace-only "blank" and end the walk before it started.
    # Cut at the line boundary instead.
    i = src.rfind("\n", 0, i) + 1
    block: list[str] = []
    for ln in reversed(src[:i].splitlines()):
        stripped = ln.strip()
        if stripped.startswith("#"):
            block.append(ln)
        elif not stripped:
            # A blank line inside a comment block is still part of it; a blank
            # line directly above code is the boundary, which the `if block`
            # distinguishes from one above nothing.
            if block:
                block.append(ln)
            else:
                break
        else:
            break
    return "\n".join(reversed(block))


def test_the_alignment_comment_still_carries_the_measured_size():
    """The comment at `_peer_for_gp` is the record of why the argument is there
    and what the divergence cost. Keeping the measurement in the source is what
    stops the next reader from deleting it as a redundant fallback — the
    fallback only fires when the quote carries no cap, which no fixture does."""
    src = _run_src()
    comment = _comment_above(src, "_peer_for_gp = get_sector_peer_multiples(")
    assert comment, "no comment block above the _peer_for_gp call"
    assert "market_cap" in comment
    assert "401 of the 436" in comment
    assert "resolved_mcap" in comment
    # The scope correction: the claim that US names were unaffected was measured
    # in production and is false there. A comment that carries the measurement
    # and not the correction is worse than either alone, because the number is
    # checkable and the false scope reads as checked.
    assert "US names are unaffected" not in comment
    assert "industry/large n=10" in comment
    assert "+45.92%" not in comment, (
        "02888_HK's average is NOT an item-3a result; HKSE Banks - Diversified "
        "stores no `large` rung, so the alignment cannot reach it")


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


# ── D. The 12m-target peer call is the one whose provenance is published ─────


def test_the_12m_peer_call_sits_outside_the_scenario_loop():
    """The growth-premium peer call is inside `for scenario in (...)`, so it runs
    three times and resolves the same rung each time — which is why `sector_g_avg`
    is scenario-invariant across the baseline. The 12m-target call is not: it runs
    once and its resolution is what `multiples_used` records. Aligning the two
    matters for different reasons, and a reader who moves one across the loop
    boundary changes how many live comp lookups a single valuation performs."""
    lines = _run_src().splitlines()
    loop = [i for i, ln in enumerate(lines)
            if ln.strip() == 'for scenario in ("base", "bear", "bull"):']
    gp = [i for i, ln in enumerate(lines)
          if "_peer_for_gp = get_sector_peer_multiples(" in ln]
    pt = [i for i, ln in enumerate(lines)
          if ln.strip().startswith("peer = get_sector_peer_multiples(")]
    assert len(loop) == 1 and len(gp) == 1 and len(pt) == 1
    assert loop[0] < gp[0], "_peer_for_gp must stay inside the scenario loop"
    assert len(lines[loop[0]]) - len(lines[loop[0]].lstrip()) < \
        len(lines[gp[0]]) - len(lines[gp[0]].lstrip())
    # The 12m call is at the loop's own indent level, i.e. outside it.
    assert (len(lines[pt[0]]) - len(lines[pt[0]].lstrip())) <= \
        (len(lines[loop[0]]) - len(lines[loop[0]].lstrip()))


def test_the_12m_call_site_says_why_its_trace_has_to_match():
    """`multiples_used` publishes the resolution the 12m target was priced off.
    When this site read `or 0.0` while the legs read `or revenue_base * 10`, an
    HK/SG name whose quote carried no cap published a size-matched cohort beside
    whole-grouping multiples — a trace that reads as checkable and is not is
    worse than no trace, because nobody looks again. The comment is the reason
    the argument is there; deleting it deletes the reason."""
    src = _run_src()
    comment = _comment_above(src, "        peer = get_sector_peer_multiples(")
    assert "multiples_used" in comment
    assert "or revenue_base * 10" in comment
    assert "resolved_mcap" in comment
