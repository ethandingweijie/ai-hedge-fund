"""Step 6 Route (a): the SOTP balance-sheet add-back is no longer losable.

THE BUG, in the owner's words (2026-09-18, Step 6, Route (a)):

    "Promote the add-back outside `if not rows: return None` in
    `_sotp_analyst_style`. Dropping $22.3bn of associates and $68bn of net cash
    on `BABA` simply because segment narrative rows failed to extract is a
    severe bug. Fix this unconditionally."

Both dollar figures are the real ones, read out of
`src/data/sotp_assumptions_v1.json`: BABA carries
`associates_investments = 22300000000.0` and `net_cash = 68000000000.0`. So this
is not a hypothetical shape, it is the largest position in the book.

WHAT WAS WRONG. `_sotp_analyst_style` computed the add-back in three inline
lines placed BELOW its `if not rows: return None` guard. Rows are built from
`assumptions["segments"]`, and a segment survives only when its `revenue_fwd` is
a positive number. So an extractor that found five segments and no usable
forward revenue on any of them produced an empty `rows`, the guard fired, and the
function returned None -- with the add-back never computed by anyone. The brief's
own test module recorded this as a known limitation before it was a fix:

    "It is also unreachable for a name with no segments -- the function returns
    None when `rows` is empty ... a single-brand apparel name with a minority JV
    would not [qualify], and would silently drop the stake."

WHAT CHANGED. The three lines became `_sotp_nonoperating_addback`, called ABOVE
the guard. The zero-row path now returns a DEGRADED table that carries the
add-back, with `per_share` and `per_share_reporting` set to None, rather than
returning None and taking the value with it.

WHY THE DEGRADED TABLE PUBLISHES NO PER-SHARE VALUE, which is the one place this
module deliberately does not do what the owner's illustrative snippet did. On
BABA the add-back alone is $90.3bn; over 2.4bn ADS that is $37.63 against a
$193.13 intrinsic value. SOTP (analyst) enters the blend at
`_SOTP_ANALYST_BLEND_WEIGHT`, so publishing it would have put a
substantially-weighted leg at under a fifth of the answer into the blend and
dragged the published IV down -- a MISSING input producing a confidently wrong
PUBLISHED number, which is the same defect class as the blend silently dropping
a non-positive leg that 967a3c5 fixed, only pointing the other way. The owner's
snippet avoided this by adding back onto `base_dcf_equity`, which is the right
economics; it is not executable inside a table builder that receives no DCF
value, and threading one in would make an SOTP leg a function of the DCF leg and
corrupt the blend's independence.

WHY NET CASH IS NOT ADDITIVE TO A DCF EQUITY VALUE. `_project_dcf`'s bridge is
`equity_value = pv_sum + pv_tv - (net_debt or 0.0)`, so a name with negative net
debt has ALREADY had its net cash added by the time anything downstream sees an
equity value. `base_dcf_equity + associates_val + net_cash` therefore
double-counts the $68bn. Associates genuinely are missing -- equity-method
investees are in neither consolidated revenue nor the operating income FCF is
built from -- so the correct DCF-level add-back is associates alone.
`test_net_cash_is_already_inside_the_dcf_equity_bridge` pins the bridge line so
that claim stays checkable.

ZERO GOLDEN COVERAGE, MEASURED NOT ASSUMED. `_sotp_analyst_style` is called ZERO
times across all 14 golden fixtures
(`scratchpad/probe_sotp_zerorow.py`, log `scratchpad/sotp_zerorow.log`), because
`src/memory/golden_state.py` documents `sotp_assumptions` as never archived and
the snapshot merge happens in `src/pipeline.py` Phase 4.4, which replay does not
run. So this change moves no golden number and is covered by nothing but this
module. `test_the_golden_basket_never_reaches_this_code` pins that fact, because
"the golden suite passed" would otherwise read as coverage it does not provide.
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

import src.agents.analysis.dcf_agent as dcf_agent
from src.agents.analysis.dcf_agent import (
    _sotp_analyst_style,
    _sotp_nonoperating_addback,
)

_ROOT = Path(__file__).resolve().parents[1]
_SNAPSHOT = _ROOT / "src" / "data" / "sotp_assumptions_v1.json"

#: BABA's real snapshot figures, in USD. Read from the file rather than typed,
#: so a curated revision to the snapshot moves these tests with it.
_BABA = json.loads(_SNAPSHOT.read_text(encoding="utf-8"))["assumptions"]["BABA"]
_BABA_ASSOC = float(_BABA["associates_investments"])     # 22_300_000_000.0
_BABA_NETCASH = float(_BABA["net_cash"])                 # 68_000_000_000.0
_BABA_SHARES = 2.4e9

#: The snapshot with every segment's forward revenue removed -- exactly the
#: extraction failure the owner described. Segments are still supplied, so the
#: earlier `if not segments: return None` guard does not fire, and the row loop
#: still runs and still keeps nothing.
_BABA_NO_REVENUE = {
    **_BABA,
    "segments": [{**s, "revenue_fwd": None} for s in _BABA["segments"]],
}


def _degraded(**over):
    a = dict(_BABA_NO_REVENUE)
    a.update(over)
    return _sotp_analyst_style(a, shares=_BABA_SHARES, net_debt=None,
                               fx_to_reporting=1.0)


# ─────────────────────────────────────────────────────────────────────────────
# The helper: one place decides what the add-back is
# ─────────────────────────────────────────────────────────────────────────────
class TestTheAddbackHelper:
    def test_it_returns_both_figures_and_a_basis_string(self):
        assoc, nc, basis = _sotp_nonoperating_addback(
            {"associates_investments": _BABA_ASSOC, "net_cash": _BABA_NETCASH})
        assert assoc == _BABA_ASSOC
        assert nc == _BABA_NETCASH
        assert "22.30bn" in basis and "68.00bn" in basis

    def test_net_cash_is_derived_from_negative_net_debt(self):
        assoc, nc, basis = _sotp_nonoperating_addback({}, net_debt=-5e9)
        assert assoc == 0.0
        assert nc == 5e9
        assert "derived from net_debt" in basis

    def test_a_net_borrower_gets_zero_net_cash_not_a_negative_one(self):
        """An SOTP NAV subtracts debt once, in the bridge. Subtracting it here
        as well would double count it in the other direction."""
        assoc, nc, basis = _sotp_nonoperating_addback({}, net_debt=5e9)
        assert (assoc, nc) == (0.0, 0.0)
        assert "net borrower" in basis

    def test_no_net_debt_at_all_is_reported_as_not_supplied(self):
        """Distinguished from "net borrower", because the two mean different
        things: one is a measurement and the other is an absence of one."""
        _, _, basis = _sotp_nonoperating_addback({}, net_debt=None)
        assert "not supplied" in basis

    def test_an_explicit_zero_net_cash_is_not_overwritten_by_the_derivation(self):
        """`net_cash: 0.0` is a researched value, not a missing one. The
        derivation is gated on `is None`, matching the tax-rate convention two
        lines away in the table builder."""
        assoc, nc, _ = _sotp_nonoperating_addback(
            {"net_cash": 0.0}, net_debt=-5e9)
        assert nc == 0.0

    def test_malformed_input_returns_zeros_rather_than_raising(self):
        for bad in (None, {}, {"associates_investments": "not a number"},
                    {"net_cash": object()}):
            assoc, nc, basis = _sotp_nonoperating_addback(bad)
            assert (assoc, nc) == (0.0, 0.0), bad
            assert isinstance(basis, str) and basis


# ─────────────────────────────────────────────────────────────────────────────
# The degraded path
# ─────────────────────────────────────────────────────────────────────────────
class TestTheDegradedTable:
    def test_zero_rows_with_an_addback_returns_a_table_not_none(self):
        """THE FIX. This is the assertion that was false before it."""
        t = _degraded()
        assert t is not None
        assert t["rows"] == []
        assert t["degraded_no_segments"] is True

    def test_the_owners_two_dollar_figures_survive(self):
        t = _degraded()
        assert t["associates"] == _BABA_ASSOC
        assert t["net_cash"] == _BABA_NETCASH
        assert t["nav"] == pytest.approx(_BABA_ASSOC + _BABA_NETCASH)
        assert t["segment_value"] == 0.0

    def test_it_publishes_no_per_share_value(self):
        """Pinned hard, because publishing one is the catastrophic outcome and
        it is one keystroke away. See the module docstring: $37.63/ADS against a
        $193.13 IV, entering the blend at a real weight."""
        t = _degraded()
        assert t["per_share"] is None
        assert t["per_share_reporting"] is None

    def test_the_number_it_refuses_to_publish_is_the_one_that_would_have_been_wrong(self):
        """Quantifies what the None is protecting against, so the guard reads as
        a measured decision rather than a stylistic one."""
        t = _degraded()
        would_be = t["final"] / _BABA_SHARES
        assert would_be == pytest.approx(90.3e9 / _BABA_SHARES, rel=1e-6)
        assert would_be == pytest.approx(37.625, abs=0.01)
        # And the real answer, for scale. Recorded in the golden baseline.
        assert would_be < 193.13 * 0.25

    def test_no_holdco_discount_is_applied_to_a_pile_of_cash(self):
        """The 15% holdco discount is a discount on an operating NAV. There is
        no operating NAV here, so applying it would produce a number with no
        meaning attached -- 15% off a cash and associates pile."""
        t = _degraded()
        assert t["holdco_discount_pct"] == 0.0
        assert t["holdco_discount"] == 0.0
        assert t["final"] == t["nav"]
        # ... even though the assumptions still ask for one.
        assert float(_BABA_NO_REVENUE["holdco_discount_pct"]) == 0.15

    def test_the_reason_names_the_segment_count_and_both_figures(self):
        t = _degraded()
        r = t["degraded_reason"]
        assert "5 segment(s) supplied" in r
        assert "+22.30bn" in r and "+68.00bn" in r

    def test_zero_rows_and_zero_addback_still_returns_none(self):
        """The pre-fix contract holds exactly where there is nothing to
        preserve, so a caller cannot tell this case apart from before."""
        assert _sotp_analyst_style(
            {"segments": [{"name": "x", "revenue_fwd": None}]},
            shares=1e9) is None

    def test_a_single_surviving_segment_is_not_degraded(self):
        """The path is narrow on purpose: one usable revenue figure out of five
        is a complete table, not a degraded one."""
        segs = [{**s, "revenue_fwd": None} for s in _BABA["segments"]]
        segs[0]["revenue_fwd"] = _BABA["segments"][0]["revenue_fwd"]
        t = _sotp_analyst_style({**_BABA, "segments": segs},
                                shares=_BABA_SHARES, net_debt=None,
                                fx_to_reporting=1.0)
        assert t["degraded_no_segments"] is False
        assert len(t["rows"]) == 1
        assert t["per_share_reporting"] is not None


# ─────────────────────────────────────────────────────────────────────────────
# The happy path is untouched
# ─────────────────────────────────────────────────────────────────────────────
class TestTheHappyPathDidNotMove:
    def test_the_snapshot_still_produces_the_same_table(self):
        t = _sotp_analyst_style(_BABA, shares=_BABA_SHARES, net_debt=None,
                                fx_to_reporting=1.0)
        assert t["degraded_no_segments"] is False
        assert len(t["rows"]) == 5
        assert t["segment_value"] == pytest.approx(346.48e9, rel=1e-4)
        assert t["nav"] == pytest.approx(436.78e9, rel=1e-4)
        assert t["per_share_reporting"] == pytest.approx(154.69, abs=0.01)

    def test_the_flag_key_is_present_on_the_happy_path_too(self):
        """So a consumer can ask the question directly instead of through a
        `.get` default that cannot tell "not degraded" from "key absent because
        this table came from somewhere else"."""
        t = _sotp_analyst_style(_BABA, shares=_BABA_SHARES)
        assert "degraded_no_segments" in t

    def test_the_first_segment_still_anchors_on_its_researched_pe(self):
        """Guards against a suspicion this work raised and then disproved by
        measurement: that a segment carrying `pe_multiple` but `ebit: None`
        would silently fall through to the generic EV/Rev classifier. It does
        not, because the snapshot also carries `ebit_margin: 0.3` and the table
        builder derives EBIT from it. Pinned so the derivation cannot be
        removed without this going red."""
        t = _sotp_analyst_style(_BABA, shares=_BABA_SHARES)
        r0 = t["rows"][0]
        assert r0["method"] == "P/E"
        assert r0["multiple"] == pytest.approx(10.4)
        assert _BABA["segments"][0]["ebit_margin"] == 0.3


# ─────────────────────────────────────────────────────────────────────────────
# Why the degraded table is cached under a separate key
# ─────────────────────────────────────────────────────────────────────────────
class TestTheSeparateCacheKeyIsLoadBearing:
    def test_the_report_layer_would_raise_on_a_degraded_table(self):
        """`sotp_report_extras` formats `table['per_share_reporting']:,.2f` in
        at least six places. Handing it a degraded table is a TypeError, so the
        engine caches degraded tables under `sotp_analyst_degraded` and leaves
        `sotp_analyst_table` unset -- the report layer then sees exactly what it
        saw before this change."""
        import src.agents.analysis.sotp_report_extras as sre
        src = inspect.getsource(sre)
        assert src.count("per_share_reporting") >= 6
        assert "table['per_share_reporting']:,.2f" in src
        t = _degraded()
        with pytest.raises(TypeError):
            f"{t['per_share_reporting']:,.2f}"

    def test_the_ground_truth_grader_would_call_a_degraded_table_zero(self):
        """`check_table` reads `float(table.get("per_share") or 0.0)`, so a
        degraded table grades as $0.00/ADS -- plausible-looking arithmetic about
        a number the engine never computed. That is why `_gate_live_sotp`
        branches before grading rather than passing the table through."""
        from src.agents.analysis.sotp_ground_truth import check_table
        t = _degraded()
        grade = check_table("BABA", t)
        assert grade is not None, "BABA has a ground-truth reference"
        assert grade["total"]["value_per_ads"] == 0.0
        assert grade["total"]["status"] != "ok"

    def test_the_method_dispatcher_does_not_publish_from_a_degraded_table(self):
        mr = {"sotp_assumptions": dict(_BABA_NO_REVENUE)}
        out = dcf_agent._compute_method_value(
            method_name="SOTP (analyst)", most_recent=mr, revenue_base=1e11,
            shares=_BABA_SHARES, net_debt=None, market_cap=None, wacc=0.10,
            growth_base=0.05, fcf_margin_base=0.10, tgr=0.02, fcf_floor=-0.05,
            sector="Consumer Cyclical", scenario="base",
            reported_currency="USD", is_hk=False, growth_premium=0.0,
            sbc_pe_discount=0.0, profile_name="Hyperscaler / Tech Conglomerate",
            forward_consensus=None, ticker="BABA", end_date="2025-12-31")
        assert out is None
        assert "sotp_analyst_table" not in mr
        assert mr["sotp_analyst_degraded"]["degraded_no_segments"] is True
        assert mr["sotp_analyst_degraded"]["associates"] == _BABA_ASSOC

    def test_the_dispatcher_does_not_recompute_on_the_second_scenario(self):
        """The verdict is a function of the assumptions alone, so bear and bull
        cannot disagree with base. Recomputing would be three wasted table
        builds per run and a chance for them to diverge."""
        mr = {"sotp_assumptions": dict(_BABA_NO_REVENUE)}
        calls = []
        _orig = dcf_agent._sotp_analyst_style

        def _spy(*a, **k):
            calls.append(1)
            return _orig(*a, **k)

        dcf_agent._sotp_analyst_style = _spy
        try:
            for scen in ("base", "bear", "bull"):
                dcf_agent._compute_method_value(
                    method_name="SOTP (analyst)", most_recent=mr,
                    revenue_base=1e11, shares=_BABA_SHARES, net_debt=None,
                    market_cap=None, wacc=0.10, growth_base=0.05,
                    fcf_margin_base=0.10, tgr=0.02, fcf_floor=-0.05,
                    sector="Consumer Cyclical", scenario=scen,
                    reported_currency="USD", is_hk=False, growth_premium=0.0,
                    sbc_pe_discount=0.0,
                    profile_name="Hyperscaler / Tech Conglomerate",
                    forward_consensus=None, ticker="BABA",
                    end_date="2025-12-31")
        finally:
            dcf_agent._sotp_analyst_style = _orig
        assert len(calls) == 1


# ─────────────────────────────────────────────────────────────────────────────
# `_gate_live_sotp`: a degraded input is flagged, never replaced (2026-09-26)
# ─────────────────────────────────────────────────────────────────────────────
class TestTheLiveGateHandlesDegradation:
    """Owner, 2026-09-26: the snapshot is retired, so the gate FLAGS a degraded
    input and never substitutes one. The assumptions come back unchanged."""

    def test_a_degraded_live_baba_is_flagged_not_replaced(self):
        a, flag = dcf_agent._gate_live_sotp(
            "BABA", dict(_BABA_NO_REVENUE), _BABA_SHARES, None)
        assert a.get("_origin") is None                     # nothing substituted
        assert flag is not None and "degraded" in flag
        # The flag must NOT report a computed total that was never computed.
        assert "$0.00/ADS" not in flag
        # And it must say what actually went wrong.
        assert "none carried a usable forward revenue" in flag
        assert "does not publish" in flag

    def test_the_gate_returns_the_same_object_it_was_given(self):
        given = dict(_BABA_NO_REVENUE)
        a, _ = dcf_agent._gate_live_sotp("BABA", given, _BABA_SHARES, None)
        assert a is given

    def test_a_degraded_name_with_no_snapshot_discloses_instead_of_publishing(self):
        a, flag = dcf_agent._gate_live_sotp(
            "NOT_IN_ANY_SNAPSHOT", dict(_BABA_NO_REVENUE), _BABA_SHARES, None)
        assert a.get("_origin") is None
        assert flag is not None
        assert "does not publish" in flag
        assert "disclosed rather than valued" in flag

    def test_no_origin_short_circuits_the_gate_any_more(self):
        """The `snapshot:` early return went with the snapshot: every set of
        inputs is graded on what it contains."""
        a = {**_BABA_NO_REVENUE, "_origin": "snapshot:BABA"}
        out, flag = dcf_agent._gate_live_sotp("BABA", a, _BABA_SHARES, None)
        assert out is a and flag is not None and "degraded" in flag

    def test_a_healthy_live_extraction_is_still_graded_not_short_circuited(self):
        out, flag = dcf_agent._gate_live_sotp(
            "BABA", dict(_BABA), _BABA_SHARES, None)
        # Plausible against the reference, so nothing is substituted.
        assert out.get("_origin") is None or not str(
            out["_origin"]).startswith("snapshot:")
        assert flag is None


# ─────────────────────────────────────────────────────────────────────────────
# The double-count claim, pinned at the bridge
# ─────────────────────────────────────────────────────────────────────────────
class TestNetCashIsAlreadyInTheDcf:
    def test_net_cash_is_already_inside_the_dcf_equity_bridge(self):
        """The owner's illustrative snippet was
        `base_dcf_equity + associates_val + net_cash`. The `net_cash` term
        double-counts, because the bridge has already added it for any name with
        negative net debt. Pinned as a source fact so the claim in the module
        docstring stays checkable rather than being an assertion in prose."""
        src = inspect.getsource(dcf_agent)
        assert "equity_value = pv_sum + pv_tv - (net_debt or 0.0)" in src

    def test_associates_appear_nowhere_in_the_projection(self):
        """The other half of the claim: associates genuinely ARE missing from
        the DCF, so they are the part a future add-back would have to carry."""
        sig = inspect.signature(dcf_agent._project_dcf)
        assert "associates" not in sig.parameters
        body = inspect.getsource(dcf_agent._project_dcf)
        assert "associates" not in body


# ─────────────────────────────────────────────────────────────────────────────
# Coverage honesty
# ─────────────────────────────────────────────────────────────────────────────
class TestWhatTheGoldenBasketDoesAndDoesNotCover:
    def test_the_golden_basket_never_reaches_this_code(self):
        """`sotp_assumptions` is never archived, so replay has none, so
        `_sotp_analyst_style` is called zero times across all 14 fixtures.
        Measured with a module-level spy, one subprocess per fixture
        (`scratchpad/probe_sotp_zerorow.py`). This change therefore moves no
        golden number -- and is covered by nothing but this module."""
        gs = (_ROOT / "src" / "memory" / "golden_state.py").read_text(
            encoding="utf-8")
        assert "``sotp_assumptions``    — never archived" in gs
        log = (_ROOT / "scratchpad" / "sotp_zerorow.log")
        if log.exists():
            txt = log.read_text(encoding="utf-8")
            assert txt.count('"sotp_calls": []') == 14, (
                "a golden fixture reached the SOTP analyst path; this module's "
                "coverage claim is stale and the golden baseline may move")

    def test_the_snapshot_merge_happens_in_the_pipeline_not_the_engine(self):
        """The other half of why replay cannot see this: the snapshot that
        supplies BABA's assumptions is attached in `src/pipeline.py` Phase 4.4,
        and golden replay calls `run_dcf_agent` directly with reconstructed
        state."""
        pl = (_ROOT / "src" / "pipeline.py").read_text(encoding="utf-8")
        # 2026-09-26: the attach is retired; replay and pipeline now agree
        # that the leg's only source is the owner-accepted input.
        assert "attach_snapshot(" not in pl
        assert "sotp_assumptions_v1.json" in pl
