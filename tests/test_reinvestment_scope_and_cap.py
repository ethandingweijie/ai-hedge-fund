"""The growth-reinvestment charge: profile scope and the base-margin cap.

Step 4 of the owner's summary checklist, actions two and three. Action one (the
blend's silent leg-dropping, chip lxv) shipped in `967a3c5`. The charge stays
`applied: False`; what changes is what it would do if it were switched on.

THE TWO OWNER-SPECIFIED RULES UNDER TEST, verbatim:

  * "Revenue / Invested Capital is economic nonsense for balance-sheet financial
    intermediaries (`SCHW`), regulated utilities/IPPs (`U96.SI`), and real estate
    asset bases (`C38U.SI`, where capital turnover is ~0.06). Restrict
    `_reinvestment_margin_deduction` strictly to `Apparel / Athletic Wear`,
    `Consumer Growth`, `Hyper-Growth Platform`, and `Capital Goods / Hardware`."
  * "deduction = min(g / ((1 + g) × (S/C)), max(fcf_margin_base − fcf_floor, 0.0)).
    A growth reinvestment deduction must not consume cash beyond the operating
    baseline into negative territory."

WHY THE SCOPE GATE IS A POSITIVE ALLOWLIST AND NOT AN EXCLUSION OF THE
FINANCIALS SET. `BALANCE_SHEET_FINANCIAL_PROFILES` already exists and already
names `Brokerage`, so excluding that set would have handled SCHW. It would not
have handled U96_SI (`Conglomerate / Industrial (SG)`) or C38U_SI (`S-REIT`) —
neither is a financial-intermediary profile, and C38U_SI carries the single worst
ratio in the golden basket at S/C 0.0625, producing a raw charge of +98.04%
against a +55.83% base margin. Excluding the financials set leaves the worst case
in scope, which is why the owner's list is the instrument and not a proxy for it.

ONE OWNER-SPECIFIED NAME DOES NOT EXIST. `Capital Goods / Hardware` is not a
profile name; `INDUSTRY_VALUATION_PROFILES` has 98 distinct names and that string
is not among them. The other three are, verbatim. Two existing names are what it
decomposes into — `Capital Goods` and `Consumer Electronics / Hardware Ecosystem`
— so both are listed, and `test_capital_goods_hardware_is_not_a_profile_name`
pins the discrepancy so it cannot be quietly forgotten. Neither candidate is a
golden fixture profile, so the reading has zero effect on the measured baseline
and striking either line is the whole change if the narrower one was meant.

MEASURED, one subprocess per fixture at `967a3c5` (`scratchpad/probe_sc_ratio.py`,
log `scratchpad/sc_ratio_table.log`): exactly ONE of the 14 golden fixtures is in
scope — MELI, `Hyper-Growth Platform`, S/C 1.9968, raw +6.53% against a +30.28%
base margin. So the scoping makes the charge inert on 13 of 14, and the cap binds
on ZERO of them, because all seven names where it binds are out of scope. Both
facts are asserted below rather than left in prose, because a cap that never
binds on the basket is easy to mistake for a cap that does work.
"""
from __future__ import annotations

import ast
import inspect

import pytest

from src.agents.analysis import dcf_agent
from src.data.sector_profiles import (
    CAPITAL_TURNOVER_PROFILES,
    INDUSTRY_VALUATION_PROFILES,
)

_raw = dcf_agent._reinvestment_margin_deduction_raw
_ded = dcf_agent._reinvestment_margin_deduction

#: The owner's four names, verbatim, plus the two existing names the fourth
#: decomposes into. Pinned as data so a silent edit to the shipped set is a named
#: failure rather than a diff nobody read.
OWNER_LISTED = ["Apparel / Athletic Wear", "Consumer Growth",
                "Hyper-Growth Platform", "Capital Goods / Hardware"]
DECOMPOSED_FOURTH = ["Capital Goods", "Consumer Electronics / Hardware Ecosystem"]

#: (fixture, profile, S/C, g_yr1, raw deduction, base margin, floor, headroom,
#:  capped, cap binds) — measured live, one subprocess per fixture. `None` where
#: the engine recorded S/C as unmeasurable. Reproduced here so the comment table
#: in `sector_profiles.py` is pinned by a test and not only by prose.
MEASURED = [
    ("C38U_SI",   "S-REIT",                          0.0625,   0.0653, 0.9804,  0.5583,  0.05,  0.5083, 0.5083, True),
    ("BN4_SI",    "Conglomerate / Industrial (SG)",   0.2977,   0.0500, 0.1600,  0.0979,  0.02,  0.0779, 0.0779, True),
    ("U96_SI",    "Conglomerate / Industrial (SG)",   0.4102,   0.0500, 0.1161,  0.0716,  0.00,  0.0716, 0.0716, True),
    ("02888_HK",  "Money Center Bank",                0.5009,   0.0221, 0.0433,  0.2639,  0.00,  0.2639, 0.0433, False),
    ("MU",        "Memory / DRAM-NAND",               0.6250,   0.1277, 0.1812,  0.0396, -0.05,  0.0896, 0.0896, True),
    ("SCHW",      "Brokerage",                        0.8058,   0.1500, 0.1619,  0.1153,  0.00,  0.1153, 0.1153, True),
    ("09988_HK",  "Hyperscaler / Tech Conglomerate",  0.8952,   0.1061, 0.1072,  0.0951, -0.05,  0.1451, 0.1072, False),
    ("BABA",      "Hyperscaler / Tech Conglomerate",  0.8956,   0.1088, 0.1096,  0.0951, -0.05,  0.1451, 0.1096, False),
    ("V",         "Payment Networks",                 0.9318,   0.1464, 0.1370,  0.5667,  0.00,  0.5667, 0.1370, False),
    ("FCX",       "Mining (Major)",                   0.9517,   0.1405, 0.1294,  0.0539,  0.00,  0.0539, 0.0539, True),
    ("MELI",      "Hyper-Growth Platform",            1.9968,   0.1500, 0.0653,  0.3028, -0.05,  0.3528, 0.0653, False),
    ("AAPL",      "Hyperscaler / Tech Conglomerate",  2.7712,   0.1468, 0.0462,  0.2525, -0.05,  0.3025, 0.0462, False),
    ("COST",      "Membership / Subscription Retail", 10.9116,   0.0983, 0.0082,  0.0232,  0.02,  0.0032, 0.0032, True),
    ("D05_SI",    "Money Center Bank (SG)",           None,     None,   None,   0.2702,  0.00,  0.2702, None,   False),
]


# ─────────────────────────────────────────────────────────────────────────────
# The allowlist itself
# ─────────────────────────────────────────────────────────────────────────────
class TestTheAllowlistIsTheOwnersList:

    def test_it_is_exactly_the_three_verbatim_names_plus_the_decomposed_fourth(self):
        assert CAPITAL_TURNOVER_PROFILES == frozenset(
            ["Apparel / Athletic Wear", "Consumer Growth", "Hyper-Growth Platform"]
            + DECOMPOSED_FOURTH), sorted(CAPITAL_TURNOVER_PROFILES)

    def test_capital_goods_hardware_is_not_a_profile_name(self):
        """The owner-specified string does not exist, and that is recorded.

        Not a pedantic point: this repo has shipped owner-specified identifiers
        that did not exist before (`_extract_associates_value`,
        `Prestige Beauty & Personal Care`, `margin_deviation`), and in each case
        the failure was silent — the code either never ran or ran against nothing.
        Pinning the absence means the reading chosen here stays visible, and that
        if the taxonomy ever GAINS the name, someone has to decide whether the two
        decomposed entries should collapse back into it.
        """
        every = {p for profiles in INDUSTRY_VALUATION_PROFILES.values()
                 for p in profiles}
        assert "Capital Goods / Hardware" not in every
        assert len(every) == 98, (
            "the taxonomy changed size, so re-check whether the fourth name "
            "exists now and whether the decomposition is still the right reading")
        for name in OWNER_LISTED[:3]:
            assert name in every, name

    def test_both_decomposed_names_are_real_profile_names(self):
        every = {p for profiles in INDUSTRY_VALUATION_PROFILES.values()
                 for p in profiles}
        for name in DECOMPOSED_FOURTH:
            assert name in every, name
        # A set whose members are not profile names scopes nothing: `profile_name
        # in CAPITAL_TURNOVER_PROFILES` is simply always False, and the charge
        # would be inert everywhere while looking scoped. This is the assertion
        # that catches a rename in the Consumer ladder orphaning the set.
        assert CAPITAL_TURNOVER_PROFILES <= every, sorted(
            CAPITAL_TURNOVER_PROFILES - every)

    @pytest.mark.parametrize("profile", [
        "Brokerage",                          # SCHW — owner-named
        "Conglomerate / Industrial (SG)",     # U96_SI — owner-named utility/IPP
        "S-REIT",                             # C38U_SI — owner-named, worst ratio
        "Hyperscaler / Tech Conglomerate",    # 09988_HK + BABA, the two inversions
        "Money Center Bank", "Money Center Bank (SG)", "Payment Networks",
        "Mining (Major)", "Memory / DRAM-NAND",
        "Membership / Subscription Retail",
    ])
    def test_the_profiles_the_measurement_says_must_not_charge(self, profile):
        assert profile not in CAPITAL_TURNOVER_PROFILES

    def test_the_two_sign_inversion_names_are_out_of_scope(self):
        """The perverse outcome is no longer reachable through this charge.

        Wiring the charge live moved 09988_HK base IV +17.40% and BABA +15.60% —
        a MORE conservative cash-flow assumption producing a HIGHER valuation —
        because the deduction exceeded the base margin, the floor absorbed it, the
        DCF leg resolved non-positive, and the blend dropped it and renormalised
        its weight onto the surviving multiples. Both names are `Hyperscaler /
        Tech Conglomerate`. That profile is not in the owner's list, so the
        mechanism cannot be reached through this charge on either name.
        """
        assert "Hyperscaler / Tech Conglomerate" not in CAPITAL_TURNOVER_PROFILES
        rows = [r for r in MEASURED if r[1] == "Hyperscaler / Tech Conglomerate"]
        assert {r[0] for r in rows} == {"09988_HK", "BABA", "AAPL"}


# ─────────────────────────────────────────────────────────────────────────────
# The scope gate fails closed
# ─────────────────────────────────────────────────────────────────────────────
class TestTheScopeGateFailsClosed:

    def test_no_profile_means_no_charge_even_with_a_measurable_ratio(self):
        """The default direction is the whole point of putting the gate inside.

        A caller that forgets `profile` charges nothing. The opposite default —
        charge unless told not to — is the one that would have put a +98.04%
        deduction on an S-REIT the moment anyone threaded a ratio into the
        projector, which is the failure this exists to prevent.
        """
        assert _raw(0.15, 1.9968) == 0.0
        assert _ded(0.15, 1.9968, margin_headroom=0.3528) == 0.0

    def test_empty_and_unknown_profile_strings_mean_no_charge(self):
        for bad in ("", "hyper-growth platform", "Hyper Growth Platform",
                    "Consumer", "Apparel"):
            assert _raw(0.15, 1.9968, profile=bad) == 0.0, bad
            assert _ded(0.15, 1.9968, profile=bad, margin_headroom=0.3528) == 0.0, bad

    def test_profile_is_keyword_only_so_a_positional_cannot_sneak_past_it(self):
        """`_raw(g, s_to_c, "Consumer Growth")` must be a TypeError.

        Positional acceptance would let a caller pass a growth schedule, a margin
        or a scenario name where a profile belongs and still type-check — and
        since every non-member string returns 0.0, the bug would present as "the
        charge silently stopped firing", not as an error.
        """
        with pytest.raises(TypeError):
            _raw(0.15, 1.9968, "Consumer Growth")
        with pytest.raises(TypeError):
            _ded(0.15, 1.9968, "Consumer Growth", 0.3528)

    @pytest.mark.parametrize("profile", sorted(CAPITAL_TURNOVER_PROFILES))
    def test_every_allowlist_member_actually_charges(self, profile):
        assert _raw(0.15, 2.0, profile=profile) > 0.0, profile
        assert _ded(0.15, 2.0, profile=profile, margin_headroom=0.30) > 0.0, profile


# ─────────────────────────────────────────────────────────────────────────────
# The cap
# ─────────────────────────────────────────────────────────────────────────────
class TestTheCapIsTheOwnersFormula:

    def test_the_algebra_is_unchanged_for_an_in_scope_name(self):
        # MELI's measured inputs. g/((1+g)·(S/C)) = 0.15/(1.15 × 1.9968).
        assert _raw(0.15, 1.9968, profile="Hyper-Growth Platform") == pytest.approx(
            0.15 / (1.15 * 1.9968), rel=1e-12)
        assert _ded(0.15, 1.9968, profile="Hyper-Growth Platform",
                    margin_headroom=0.3528) == pytest.approx(0.0653219, abs=1e-6)

    def test_the_cap_returns_the_headroom_exactly_when_it_binds(self):
        got = _ded(0.20, 0.5, profile="Consumer Growth", margin_headroom=0.01)
        assert _raw(0.20, 0.5, profile="Consumer Growth") == pytest.approx(1 / 3)
        assert got == 0.01

    def test_the_cap_is_base_margin_less_floor_not_base_margin(self):
        """The owner wrote `max(fcf_margin_base − fcf_floor, 0.0)`, and the
        distinction is reachable: MELI's floor is −5.00%, so its headroom
        (+35.28%) EXCEEDS its base margin (+30.28%). Capping at the base margin
        instead would be a tighter bound than the one specified, and would be the
        kind of unauthorised clamp this repo keeps a list of."""
        headroom = 0.3028 - (-0.05)
        assert headroom == pytest.approx(0.3528)
        assert headroom > 0.3028

    def test_no_headroom_means_no_charge_even_in_scope(self):
        """Fails closed, like the scope gate. A rationing bound cannot be
        enforced against a baseline nobody supplied, so nothing is charged rather
        than something uncapped."""
        assert _ded(0.15, 1.9968, profile="Hyper-Growth Platform") == 0.0
        assert _ded(0.15, 1.9968, profile="Hyper-Growth Platform",
                    margin_headroom=None) == 0.0

    def test_a_negative_headroom_caps_at_zero_not_below(self):
        # The max(..., 0.0) arm. A base margin already under the floor leaves no
        # room, and the charge must not push further into negative territory.
        assert _ded(0.10, 1.0, profile="Consumer Growth",
                    margin_headroom=-0.05) == 0.0
        assert _ded(0.10, 1.0, profile="Consumer Growth",
                    margin_headroom=0.0) == 0.0

    def test_a_shrinking_year_credit_survives_the_cap(self):
        """Two-sidedness is preserved through the cap, not truncated by it.

        `min(negative, non_negative)` is the negative, so a year that releases
        working capital still raises the margin. Capping a CREDIT at zero would
        silently delete the two-sidedness the raw function documents, and would be
        an asymmetric bound nobody chose.
        """
        raw = _raw(-0.10, 1.0, profile="Consumer Growth")
        assert raw == pytest.approx(-0.10 / 0.90)
        for headroom in (0.0, 0.05, -0.05, 0.3528):
            assert _ded(-0.10, 1.0, profile="Consumer Growth",
                        margin_headroom=headroom) == pytest.approx(raw), headroom

    @pytest.mark.parametrize("sc,expected", [(None, 0.0), (0.0, 0.0), (-1.0, 0.0)])
    def test_the_pre_existing_guards_still_fire_for_an_in_scope_profile(
            self, sc, expected):
        assert _raw(0.15, sc, profile="Consumer Growth") == expected
        assert _ded(0.15, sc, profile="Consumer Growth",
                    margin_headroom=0.30) == expected

    def test_a_year_that_does_not_survive_still_returns_zero(self):
        assert _raw(-1.0, 1.0, profile="Consumer Growth") == 0.0
        assert _raw(-1.5, 1.0, profile="Consumer Growth") == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# The measured table
# ─────────────────────────────────────────────────────────────────────────────
class TestTheMeasuredTable:

    def test_the_owner_formula_reproduces_the_published_capped_column(self):
        """The evidence in the `sector_profiles.py` comment is internally
        consistent. Checked as arithmetic on the measurement rather than by
        re-running the engine, so a transcription error in the table is a named
        failure."""
        for (name, _prof, _sc, _g, raw, base, floor, head, capped,
             binds) in MEASURED:
            if raw is None:
                continue
            assert head == pytest.approx(max(base - floor, 0.0), abs=5e-4), name
            assert capped == pytest.approx(min(raw, head), abs=5e-4), name
            assert (raw > head + 5e-4) is binds, name

    @pytest.mark.parametrize("row", MEASURED, ids=[r[0] for r in MEASURED])
    def test_out_of_scope_rows_charge_nothing_whatever_the_ratio(self, row):
        name, profile, sc, g, *_rest = row
        if profile in CAPITAL_TURNOVER_PROFILES:
            pytest.skip(f"{name} is the in-scope fixture")
        assert _raw(g or 0.15, sc or 1.0, profile=profile) == 0.0, name
        assert _ded(g or 0.15, sc or 1.0, profile=profile,
                    margin_headroom=0.30) == 0.0, name

    def test_exactly_one_of_the_fourteen_fixtures_is_in_scope(self):
        inside = [r[0] for r in MEASURED if r[1] in CAPITAL_TURNOVER_PROFILES]
        assert inside == ["MELI"], inside

    def test_the_cap_binds_on_zero_of_the_fourteen_once_scoping_lands(self):
        """Stated as a test because it is the opposite of what the raw numbers
        suggest, and it is the kind of fact that gets lost: seven of thirteen
        measurable names have a raw charge above their headroom, and all seven
        are out of scope, so after scoping the cap never binds on this basket.

        The cap is still correct and still shipped — it binds on out-of-basket
        names, and COST is the shape (a thin-margin retailer whose +0.82% raw
        charge exceeds its +0.32% headroom). But a reader who assumed the cap was
        doing work on the golden fixtures would be wrong, and this is where that
        is written down as something checkable.
        """
        binding = [r[0] for r in MEASURED
                   if r[9] and r[1] in CAPITAL_TURNOVER_PROFILES]
        assert binding == [], binding
        assert [r[0] for r in MEASURED if r[9]] == [
            "C38U_SI", "BN4_SI", "U96_SI", "MU", "SCHW", "FCX", "COST"]

    def test_the_s_c_span_is_the_argument_for_scoping(self):
        meas = [r[2] for r in MEASURED if r[2] is not None]
        assert len(meas) == 13
        assert min(meas) == pytest.approx(0.0625)
        assert max(meas) == pytest.approx(10.9116)
        # 175x. Not dispersion around one quantity — fourteen different
        # quantities wearing one name, which is why no numeric band on the ratio
        # would have been a substitute for the profile scope (and why none was
        # added: a band is a clamp, and clamps are the owner's to choose).
        assert max(meas) / min(meas) == pytest.approx(174.6, abs=0.5)


# ─────────────────────────────────────────────────────────────────────────────
# Source shape: the invariant lives in one place, and the projector stays inert
# ─────────────────────────────────────────────────────────────────────────────
class TestTheInvariantsAreNotDuplicated:

    def test_the_formula_exists_in_exactly_one_function(self):
        src = inspect.getsource(dcf_agent)
        assert src.count("g / ((1.0 + g) * sales_to_capital)") == 1
        assert "g / ((1.0 + g) * sales_to_capital)" in inspect.getsource(_raw)
        assert "g / ((1.0 + g) * sales_to_capital)" not in inspect.getsource(_ded)

    def test_the_scope_gate_is_in_the_raw_function_only(self):
        """So that a caller wanting the UNCAPPED number for disclosure cannot
        thereby get an unscoped one. The gate record publishes both, and if the
        gate lived only in the capped wrapper the raw figure it publishes would
        have been computed without the profile check."""
        assert "CAPITAL_TURNOVER_PROFILES" in inspect.getsource(_raw)
        assert "CAPITAL_TURNOVER_PROFILES" not in inspect.getsource(_ded)

    def test_the_capped_function_delegates_rather_than_recomputing(self):
        assert "_reinvestment_margin_deduction_raw(" in inspect.getsource(_ded)

    def test_the_projectors_reinvestment_call_is_scope_inert(self):
        """`_project_dcf` passes no profile and no headroom, so it charges
        nothing for ANY ratio — including a live one.

        That is a trap worth pinning rather than a property worth celebrating:
        threading `sales_to_capital` into the projector now looks like it should
        work and does nothing. Wiring the charge live is a three-part change
        (signature, this call, and `_y10_fcf_margin` in the same commit), recorded
        in the comment above the call. This test is the marker that makes the
        omission a red test instead of a valuation that quietly failed to move.
        """
        eng = inspect.getsource(dcf_agent._project_dcf)
        assert "reinvest_t = _reinvestment_margin_deduction(g_t, _s_to_c)" in eng
        calls = [n for n in ast.walk(ast.parse(eng))
                 if isinstance(n, ast.Call)
                 and getattr(n.func, "id", None) == "_reinvestment_margin_deduction"]
        assert len(calls) == 1
        assert [kw.arg for kw in calls[0].keywords] == [], "the projector now scopes"
        assert len(calls[0].args) == 2
        # And the consequence, asserted on behaviour rather than on shape.
        for sc in (0.0625, 1.0, 1.9968, 10.9116):
            assert dcf_agent._reinvestment_margin_deduction(0.15, sc) == 0.0, sc

    def test_the_gate_site_passes_the_resolved_profile_name(self):
        src = inspect.getsource(dcf_agent.run_dcf_agent)
        assert "profile=profile_name," in src
        assert "margin_headroom=_reinvest_headroom)" in src
        assert "_reinvest_headroom = float(fcf_margin_base) - float(fcf_floor)" in src

    def test_the_gate_record_still_says_not_applied_and_publishes_both_gates(self):
        src = inspect.getsource(dcf_agent)
        rec = src[src.index('"gate_id": "GATE_GROWTH_REINVESTMENT"'):]
        # The existing pin in `test_consumer_discretionary_gates.py` slices this
        # record with `rec.index("})", 2)`, so `"applied": False,` has to stay
        # inside the first `)}` — i.e. last, and with no `)}` sequence introduced
        # above it by the new keys. Asserted directly rather than trusted.
        rec = rec[:rec.index("})", 2)]
        assert '"applied": False,' in rec, rec
        for key in ('"in_scope"', '"deduction_uncapped"', '"deduction_leviable"',
                    '"margin_headroom"', '"cap_binds"'):
            assert key in rec, key
        assert '"applied": _reinvest_in_scope' not in src
        # FOUR, not three. The fourth is
        # `GATE_BALANCE_SHEET_QUARTERLY_STEP_CHANGE`, Phase 1.4 telemetry over
        # the quarterly balance-sheet overlay: the overlay substitutes the
        # latest reported quarter for the year-end balance sheet
        # unconditionally, and the record sizes that substitution for the audit
        # payload (`applied` a literal False, with no branch that could make it
        # True). It has nothing to do with reinvestment scope and everything to
        # do with this count being a fact about the whole file rather than about
        # this gate — which is why an unrelated change reddens a test whose name
        # says "not applied and publishes both gates".
        assert src.count('"applied": False,') == 4
        # SIX, not five, since GATE_SCENARIO_ORDERING landed with a literal
        # `"applied": True,`. This is the SECOND copy of that count — the first
        # is in `test_consumer_discretionary_gates.py::
        # test_gate_vocabulary_is_closed_and_has_eleven_members`, and neither
        # mentions the other, so adding a live gate turns two tests red in two
        # modules that look unrelated. Both were widened together when the
        # tenth gate arrived, and again when the eleventh did; if you are reading
        # this because one of them failed and the other did not, a gate was
        # added without a record.
        # FIVE since 2026-09-19: GATE_DETERMINISTIC_KPI_PRECEDENCE was retired
        # with the composite it measured.
        assert src.count('"applied": True,') == 5

    def test_the_two_flag_branches_are_mutually_exclusive(self):
        """Out-of-scope names get one sentence; in-scope names get the paragraph.

        13 of 14 fixtures land on the short branch. A paragraph on each would be
        prose no reader reaches the end of, which is the opposite failure from the
        silence the blend's dropped-leg disclosure was written to remove — and the
        gate record carries the full basis and the reference ratio either way, so
        the short flag hides nothing that is not still published.
        """
        src = inspect.getsource(dcf_agent.run_dcf_agent)
        assert "if _s_to_c is not None and not _reinvest_in_scope:" in src
        assert "elif _s_to_c is not None and abs(_reinvest_ded) >= 0.0005:" in src
        short = src.index("Growth reinvestment NOT leviable on this profile")
        long_ = src.index("Growth reinvestment NOT charged (observation only)")
        assert short < long_
        assert "is not a capital-turnover profile" in src

    def test_the_exceed_clause_survives_because_melis_floor_is_negative(self):
        """The cap made this clause nearly unreachable, but not quite.

        The deduction is bounded at `base − floor`, which exceeds `base` exactly
        when `floor < 0`. MELI's floor is −5.00% and its headroom (+35.28%) does
        exceed its base margin (+30.28%), so the only in-scope golden fixture is
        also the one where the clause stays live. Deleting it as dead code would
        have been wrong, and this is the assertion that says why.
        """
        src = inspect.getsource(dcf_agent.run_dcf_agent)
        assert "if abs(_reinvest_ded) > abs(fcf_margin_base) else " in src
        row = next(r for r in MEASURED if r[0] == "MELI")
        _n, _p, _sc, _g, _raw_v, base, floor, head, _c, _b = row
        assert floor < 0 and head > base
