"""Phase 1.1 — convergence fade and the CAGR-divergence gate.

Defect 1: a ONE-YEAR consensus jump was held flat for ten years. BN4.SI
carried +20.3% NTM growth against a −2.5% five-year revenue CAGR and U96.SI
+22% against the same. Neither was caught by the revenue-scale tier, which is
keyed to revenue SCALE (both sit in the ≥$3bn tier whose cap is 22%) rather
than to divergence from history.

These tests pin three properties the plan specifies explicitly:

  * the strict ordering **CAGR gate → seeded g_1 → fade schedule**, so a
    gated name's schedule starts at the gated value and never at raw
    consensus;
  * the gate's two deterministic exceptions, including that a cyclical's peak
    margin must NOT unlock it;
  * that analyst dispersion survives the gate by SCALING the band rather than
    clipping each scenario onto one ceiling.

The profile-name test exists because of defect 3 (peer z-scores sat dormant
for months). A set of profile names that silently stops matching the taxonomy
deactivates the fade with no error, so membership is asserted against the real
taxonomy rather than trusted.

Integration-level pinning of the same behaviour lives in the golden suite:
``tests/golden/snapshots.json`` records BN4.SI's base ``growth_rate`` as 0.05
and U96.SI's as 0.05, which is the ordering property end-to-end.
"""
from __future__ import annotations

import pytest

from src.agents.analysis.dcf_agent import (
    _CAGR_DIVERGENCE_HEADROOM,
    _CAGR_DIVERGENCE_THRESHOLD,
    _CONVERGENCE_ALPHA,
    _CONVERGENCE_ALPHA_PROFILES,
    _CYCLICAL_PROFILES,
    _EBIT_INFLECTION_YEARS,
    _GROWTH_DECAY_DELTA,
    _PROJECTION_YEARS,
    _ebit_margin_inflection,
    _gate_growth_cagr_divergence,
    _growth_convergence_schedule,
)
from src.agents.analysis.dcf_agent import _scale_analyst_bands_to_cap

# ── The numbers from the defect table ────────────────────────────────────────
# BN4.SI (Keppel) and U96.SI (Sembcorp-like SG industrial), as pinned in the
# golden fixtures: NTM consensus base growth and the five-year revenue CAGR the
# engine computes from the same recorded series.
BN4_G_NTM = 0.2026
BN4_CAGR = -0.025
BN4_BANDS = {"bear": 0.0383, "base": 0.2026, "bull": 0.3733}

U96_G_NTM = 0.22
U96_CAGR = -0.025

#: tgr_table["base"] for the Industrials sector, which is what both names
#: resolve to. This is the fade target — the engine's long-run nominal rate,
#: NOT the historical CAGR.
INDUSTRIALS_TGR_BASE = 0.02

#: The gate's ceiling for a −2.5% CAGR: max(-0.025, 0) + 5pp.
CEILING = 0.05


# ── Profile-set integrity ────────────────────────────────────────────────────

def _taxonomy_profile_names() -> set[str]:
    """Every profile name the engine can actually resolve."""
    import src.data.sector_profiles as sp

    names: set[str] = set()

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if isinstance(k, str):
                    names.add(k)
                walk(v)
        elif isinstance(o, (list, tuple)):
            for v in o:
                walk(v)

    walk(sp.SECTOR_PROFILES)
    walk(sp.INDUSTRY_VALUATION_PROFILES)
    return names


@pytest.mark.parametrize("profile", sorted(_CONVERGENCE_ALPHA_PROFILES))
def test_convergence_profile_exists_in_taxonomy(profile):
    """A name that stops matching the taxonomy silently deactivates the fade.

    Defect 3 in this same plan was a dormant feature: peer z-scores were
    computed and discarded for months with the suite green. A frozenset of
    hand-typed profile strings fails the same way — a rename upstream leaves
    the set matching nothing, and every valuation quietly reverts to flat
    growth with no error anywhere.
    """
    assert profile in _taxonomy_profile_names(), (
        f"{profile!r} is in _CONVERGENCE_ALPHA_PROFILES but is not a profile "
        f"name in the taxonomy — the fade will never fire for it. Update the "
        f"set when the taxonomy renames a profile."
    )


@pytest.mark.parametrize("profile", sorted(_CYCLICAL_PROFILES))
def test_cyclical_profile_exists_in_taxonomy(profile):
    """Same dormancy risk for the gate's margin-exception carve-out.

    A cyclical name missing from this set would let a peak margin unlock the
    gate — the exact failure the carve-out exists to prevent, and it would
    fail silently and only at a cycle top.
    """
    assert profile in _taxonomy_profile_names(), (
        f"{profile!r} is in _CYCLICAL_PROFILES but is not a profile name in "
        f"the taxonomy."
    )


def test_decay_and_convergence_profiles_are_disjoint():
    """A profile in both sets would make the branch order load-bearing.

    The scenario loop tests convergence first. That ordering is documented as
    NOT load-bearing, which is only true while the sets do not intersect —
    the two schedules are different models (decay toward zero vs convergence
    toward a named long-run rate) and a profile must not get an arbitrary one.
    """
    both = set(_GROWTH_DECAY_DELTA) & _CONVERGENCE_ALPHA_PROFILES
    assert not both, (
        f"profile(s) in both _GROWTH_DECAY_DELTA and "
        f"_CONVERGENCE_ALPHA_PROFILES: {sorted(both)}"
    )


def test_cyclical_profiles_are_a_subset_of_convergence_profiles():
    """Every cyclical gets the fade.

    The two sets answer different questions — _CYCLICAL_PROFILES gates the
    margin exception, _CONVERGENCE_ALPHA_PROFILES selects the fade — but a
    cyclical whose consensus jump was held flat for a decade is the clearest
    case for a fade, so none may be missing.
    """
    missing = _CYCLICAL_PROFILES - _CONVERGENCE_ALPHA_PROFILES
    assert not missing, f"cyclical profile(s) with no fade: {sorted(missing)}"


def test_convergence_set_is_strictly_wider_than_cyclical():
    """Conglomerates and utilities converge without being cyclical.

    Pinned so the two sets are not collapsed into one during a cleanup: they
    overlap heavily but a Regulated Utility has no peak margin to mistake for
    an inflection, while still needing its consensus year faded.
    """
    assert _CONVERGENCE_ALPHA_PROFILES - _CYCLICAL_PROFILES, (
        "the convergence set equals the cyclical set; the non-cyclical "
        "members (Conglomerate / Industrial (SG), Capital Goods, Regulated "
        "Utility, IPP, ...) are the reason two sets exist"
    )


# ── The fade schedule ────────────────────────────────────────────────────────

def test_schedule_year_one_is_exactly_g_ntm():
    """α^0 = 1, so year 1 is the seed value and the fade does not touch it.

    A one-year consensus figure IS a valid year-1 estimate. The fade exists to
    stop it being repeated ten times, not to discount it in its own year.
    """
    s = _growth_convergence_schedule(0.05, INDUSTRIALS_TGR_BASE)
    assert s[0] == pytest.approx(0.05, abs=1e-12)


def test_schedule_matches_the_specified_arithmetic():
    """g_t = g_ntm·α^(t−1) + g_norm·(1−α^(t−1)), α = 0.5.

    The plan's worked example, which uses a 2.5% long-run rate: from a gated
    5.0% the path is 5.00 / 3.75 / 3.13 / 2.81. (With the Industrials table
    value of 2.0% that BN4.SI and U96.SI actually resolve to, it is
    5.00 / 3.50 / 2.75 / 2.38 — same shape, the table governs.)
    """
    s = _growth_convergence_schedule(0.05, 0.025, alpha=0.5)
    assert s[0] == pytest.approx(0.05)
    assert s[1] == pytest.approx(0.0375)
    assert s[2] == pytest.approx(0.03125)
    assert s[3] == pytest.approx(0.028125)


def test_schedule_length_matches_projection_years():
    """_project_dcf ignores a schedule shorter than `years` and silently falls
    back to constant growth — the exact defect this schedule fixes."""
    s = _growth_convergence_schedule(0.05, 0.02)
    assert len(s) == _PROJECTION_YEARS


def test_schedule_converges_on_g_norm_from_above():
    s = _growth_convergence_schedule(0.20, 0.02)
    assert s[0] == pytest.approx(0.20)
    assert s[-1] == pytest.approx(0.02, abs=0.001)
    # Monotone, and never crossing below the long-run rate.
    assert all(b < a for a, b in zip(s, s[1:]))
    assert all(x >= 0.02 for x in s)


def test_schedule_converges_on_g_norm_from_below():
    """The fade is symmetric: a consensus year BELOW the long-run rate rises
    toward it. The formula is a weighted blend, not a decay, so it must not
    pin a low year flat either."""
    s = _growth_convergence_schedule(0.005, 0.02)
    assert s[0] == pytest.approx(0.005)
    assert all(b > a for a, b in zip(s, s[1:]))
    assert s[-1] == pytest.approx(0.02, abs=0.001)
    assert all(x <= 0.02 for x in s)


def test_schedule_with_zero_long_run_rate_decays_to_zero():
    """Gate B zeroes the terminal growth rate for value-destructive names, and
    the fade target is read BEFORE that happens — but a genuinely zero
    long-run rate must still produce a clean decay, not a negative path.

    α = 0.5 over ten years retains α^9 ≈ 0.2% of the initial gap, so Y10 is
    ~0.0098% rather than exactly zero. The fade approaches the target without
    reaching it; that is the intended shape, not a tolerance to tighten.
    """
    s = _growth_convergence_schedule(0.05, 0.0)
    assert s[0] == pytest.approx(0.05)
    assert s[-1] == pytest.approx(0.05 * _CONVERGENCE_ALPHA ** 9)
    assert s[-1] < 1e-4
    assert all(x >= 0.0 for x in s)
    assert all(b < a for a, b in zip(s, s[1:]))


def test_alpha_one_holds_growth_flat():
    """α = 1 degenerates to the pre-fix behaviour, confirming the parameter is
    the thing doing the work."""
    s = _growth_convergence_schedule(0.20, 0.02, alpha=1.0)
    assert all(x == pytest.approx(0.20) for x in s)


# ── The gate ─────────────────────────────────────────────────────────────────

def test_gate_fires_on_bn4():
    gated, record, exc = _gate_growth_cagr_divergence(
        BN4_G_NTM, BN4_CAGR,
        profile_name="Conglomerate / Industrial (SG)", series=[])
    assert exc is None
    assert gated == pytest.approx(CEILING)
    assert record is not None
    assert record["gate_id"] == "GATE_GROWTH_CAGR_DIVERGENCE"
    assert record["metric"] == "revenue_growth"
    assert record["raw_input_path_a"] == pytest.approx(BN4_G_NTM)
    assert record["gated_output_path_b"] == pytest.approx(CEILING)
    assert record["applied"] is True


def test_gate_fires_on_u96():
    gated, record, _ = _gate_growth_cagr_divergence(
        U96_G_NTM, U96_CAGR,
        profile_name="Conglomerate / Industrial (SG)", series=[])
    assert gated == pytest.approx(CEILING)
    assert record is not None


def test_gate_ceiling_floors_a_negative_cagr_at_zero():
    """max(CAGR, 0) + 5pp, not CAGR + 5pp.

    For BN4.SI's −2.5% CAGR the floor is what makes the ceiling +5.0% rather
    than +2.5%: a business in structural decline may still be allowed modest
    forward growth. It is the EXTRAPOLATION of the decline that is refused.
    """
    gated, _, _ = _gate_growth_cagr_divergence(
        0.40, -0.20, profile_name="Conglomerate", series=[])
    assert gated == pytest.approx(0.0 + _CAGR_DIVERGENCE_HEADROOM)


def test_gate_quiet_within_threshold():
    """Inside 15pp the gate must not fire, record anything, or move the number.

    The threshold is a divergence test, not a level test: a name growing 20%
    with a 12% history is 8pp apart and is left alone.
    """
    gated, record, exc = _gate_growth_cagr_divergence(
        0.20, 0.12, profile_name="Conglomerate", series=[])
    assert gated == pytest.approx(0.20)
    assert record is None
    assert exc is None


def test_gate_is_one_sided():
    """Consensus far BELOW a high CAGR is not raised.

    The divergence test is symmetric but the rewrite is min(), so a
    conservative forecast on a fast-growing history passes through untouched.
    The defect being fixed is a jump held flat for a decade, not conservatism.
    """
    gated, record, exc = _gate_growth_cagr_divergence(
        0.05, 0.42, profile_name="Hyper-Growth Platform", series=[])
    assert gated == pytest.approx(0.05)
    assert record is None
    assert exc is None


def test_gate_threshold_and_headroom_are_the_specified_values():
    assert _CAGR_DIVERGENCE_THRESHOLD == pytest.approx(0.15)
    assert _CAGR_DIVERGENCE_HEADROOM == pytest.approx(0.05)
    assert _CONVERGENCE_ALPHA == pytest.approx(0.5)


@pytest.mark.parametrize("g_ntm,cagr", [(None, -0.025), (0.2026, None)])
def test_gate_needs_both_inputs(g_ntm, cagr):
    """No CAGR (thin history) means no divergence test — the gate stands down
    rather than inventing a comparison."""
    gated, record, exc = _gate_growth_cagr_divergence(
        g_ntm, cagr, profile_name="Conglomerate", series=[])
    assert gated == g_ntm
    assert record is None
    assert exc is None


# ── The two exceptions ───────────────────────────────────────────────────────

def test_guided_growth_overrides_the_gate():
    """R1 structured guidance is management's own forward-year figure.

    That is exactly the information a consensus-vs-history divergence cannot
    see, so it stands the gate down outright — and says so, rather than
    failing quietly.
    """
    gated, record, exc = _gate_growth_cagr_divergence(
        BN4_G_NTM, BN4_CAGR, data_source="guided",
        profile_name="Conglomerate / Industrial (SG)", series=[])
    assert gated == pytest.approx(BN4_G_NTM)
    assert record is None
    assert exc is not None and "guided" in exc


def test_analyst_source_does_not_override_the_gate():
    """Only ``guided`` is an exception. BN4.SI and U96.SI are both
    ``data_source == "analyst"`` — if analyst consensus counted as
    self-justifying, the gate could never fire on the names it was built for.
    """
    gated, _, exc = _gate_growth_cagr_divergence(
        BN4_G_NTM, BN4_CAGR, data_source="analyst",
        profile_name="Conglomerate / Industrial (SG)", series=[])
    assert gated == pytest.approx(CEILING)
    assert exc is None


def _series_with_margins(*margins: float) -> list[dict]:
    return [{"revenue": 100.0, "ebit": m * 100.0} for m in margins]


def test_margin_inflection_detected():
    assert _ebit_margin_inflection(_series_with_margins(0.05, 0.05, 0.25))


def test_margin_inflection_needs_a_real_gap():
    """A 5pp lift over a 3-year mean that INCLUDES the latest year needs a
    large underlying move — two flat years and one merely good year is not an
    inflection."""
    assert not _ebit_margin_inflection(_series_with_margins(0.10, 0.11, 0.13))


def test_margin_inflection_needs_three_years():
    """With fewer years there is no mean to test against. This matters more
    than it looks: the feed caps history at 5 annual rows (defect 8), so the
    window is usually all the history there is."""
    assert not _ebit_margin_inflection(_series_with_margins(0.05, 0.30))
    assert not _ebit_margin_inflection([])


def test_margin_inflection_ignores_unusable_rows():
    """Zero or missing revenue must not produce an inf margin or a crash."""
    series = [
        {"revenue": 100.0, "ebit": 5.0},
        {"revenue": 0.0, "ebit": 90.0},      # unusable — skipped
        {"revenue": None, "ebit": 90.0},      # unusable — skipped
        {"revenue": 100.0, "ebit": 5.0},
        {"revenue": 100.0, "ebit": 30.0},
    ]
    assert _EBIT_INFLECTION_YEARS == 3
    assert _ebit_margin_inflection(series)


def test_margin_inflection_unlocks_the_gate_for_a_non_cyclical():
    gated, record, exc = _gate_growth_cagr_divergence(
        0.40, 0.02, profile_name="Conglomerate / Industrial (SG)",
        series=_series_with_margins(0.05, 0.05, 0.25))
    assert gated == pytest.approx(0.40)
    assert record is None
    assert exc is not None and "inflection" in exc


@pytest.mark.parametrize("profile", sorted(_CYCLICAL_PROFILES))
def test_peak_margin_does_not_unlock_the_gate_for_a_cyclical(profile):
    """The exception that must NOT apply at a cycle top.

    For a cyclical the latest EBIT margin sits above its own multi-year mean
    BY CONSTRUCTION — that is what a peak is. Allowing the margin inflection
    to unlock the gate would disable it exactly when it is most needed, and
    only in the upcycle where the resulting valuation looks most plausible.
    MU is the live case: a +100% one-year consensus jump against a +7.8%
    five-year CAGR.
    """
    gated, record, exc = _gate_growth_cagr_divergence(
        0.40, 0.02, profile_name=profile,
        series=_series_with_margins(0.05, 0.05, 0.25))
    assert exc is None, f"{profile}: margin inflection must not stand the gate down"
    assert record is not None
    assert gated == pytest.approx(0.02 + _CAGR_DIVERGENCE_HEADROOM)


def test_memory_profile_is_treated_as_cyclical():
    """MU's profile by name — the defect-2 exemplar, and the one cyclical in
    the golden basket whose gate actually fires."""
    assert "Memory / DRAM-NAND" in _CYCLICAL_PROFILES
    assert "Memory / DRAM-NAND" in _CONVERGENCE_ALPHA_PROFILES


# ── Ordering: gate → seed → fade ─────────────────────────────────────────────

def test_bn4_schedule_starts_at_the_gated_value_never_at_raw_consensus():
    """THE ordering pin the plan mandates.

    BN4.SI's schedule must start at 5.0%, never at 20.3%. The gate rewrites
    g_ntm BEFORE it seeds the schedule, so year 1 is the gated value. If a
    future refactor builds the schedule from raw consensus and gates
    afterwards, year 1 reverts to 20.3% and this fails — which is the whole
    point, since year 1 is the only year the two orderings disagree about.
    """
    gated, record, exc = _gate_growth_cagr_divergence(
        BN4_G_NTM, BN4_CAGR,
        profile_name="Conglomerate / Industrial (SG)", series=[])
    assert record is not None and exc is None, "precondition: the gate fires"

    schedule = _growth_convergence_schedule(gated, INDUSTRIALS_TGR_BASE)

    assert schedule[0] == pytest.approx(0.05), (
        f"year 1 must be the GATED 5.0%, got {schedule[0]:.4f}"
    )
    assert schedule[0] != pytest.approx(BN4_G_NTM), (
        "year 1 is raw consensus — the gate ran after the schedule was seeded"
    )
    # The rest of the path the plan specifies, on the Industrials table rate.
    assert schedule[1] == pytest.approx(0.035)
    assert schedule[2] == pytest.approx(0.0275)
    assert schedule[-1] == pytest.approx(INDUSTRIALS_TGR_BASE, abs=0.001)


def test_year_one_equals_raw_consensus_only_when_the_gate_does_not_fire():
    """The converse: an ungated name keeps its consensus year 1 exactly."""
    gated, record, _ = _gate_growth_cagr_divergence(
        0.20, 0.12, profile_name="Conglomerate / Industrial (SG)", series=[])
    assert record is None
    schedule = _growth_convergence_schedule(gated, INDUSTRIALS_TGR_BASE)
    assert schedule[0] == pytest.approx(0.20)


def test_gate_runs_before_the_fade_in_the_engine():
    """Structural pin on the call order inside ``run_dcf_agent``.

    The value tests above prove the two functions compose correctly, but not
    that the engine composes them in that order — it could seed the schedule
    from raw consensus and gate a different variable. Asserting on source text
    is brittle, so this checks the weaker, load-bearing fact: the gate is
    applied before the scenario loop, which is where the schedule is seeded.
    """
    import inspect

    from src.agents.analysis import dcf_agent

    src = inspect.getsource(dcf_agent.run_dcf_agent)
    gate_at = src.index("_gate_growth_cagr_divergence")
    loop_at = src.index('for scenario in ("base", "bear", "bull")')
    sched_at = src.index("_growth_convergence_schedule(")
    assert gate_at < loop_at < sched_at, (
        "the CAGR gate must be applied before the scenario loop, and the "
        "convergence schedule seeded inside it"
    )


def test_gate_b_consumes_the_same_schedule():
    """Gate B's Y10 revenue path must read the schedule, not rebuild one.

    It used to inline _GROWTH_DECAY_DELTA, which knew nothing about the
    convergence fade — two growth paths in one scenario. NOTE this is
    value-neutral for Gate B's decision today: _y10_ic is scaled by the same
    multiplier as _y10_rev, so it cancels in NOPAT/IC and the "Y10 projected"
    ROIC does not depend on the growth path at all. Wiring it through anyway
    keeps the paths from diverging when that IC scaling changes.
    """
    import inspect

    from src.agents.analysis import dcf_agent

    src = inspect.getsource(dcf_agent.run_dcf_agent)
    gate_b = src[src.index("Forward Gate B"):]
    gate_b = gate_b[:gate_b.index("_project_dcf(")]
    # Comments are stripped: the explanatory comment in that block names
    # _GROWTH_DECAY_DELTA to say why it is no longer used there, and matching
    # on raw source would flag the comment rather than the code.
    code = "\n".join(
        line for line in gate_b.splitlines() if not line.lstrip().startswith("#")
    )
    assert "_growth_schedule" in code, (
        "Gate B's Y10 revenue path no longer reads _growth_schedule"
    )
    assert "_GROWTH_DECAY_DELTA" not in code, (
        "Gate B is rebuilding its own decayed growth path instead of using "
        "the scenario's schedule"
    )


# ── Dispersion survives the gate ─────────────────────────────────────────────

def test_band_is_scaled_not_clipped():
    """Clipping each scenario at the ceiling would collapse base and bull onto
    5.0% and throw away what 14 analysts actually said. Scaling keeps the
    spread: BN4.SI's 3.8 / 20.3 / 37.3 becomes 0.9 / 5.0 / 9.2.

    This mirrors _scale_analyst_bands_to_cap, whose docstring gives the same
    reason for the revenue-scale tier.
    """
    gated, record, _ = _gate_growth_cagr_divergence(
        BN4_BANDS["base"], BN4_CAGR,
        profile_name="Conglomerate / Industrial (SG)", series=[])
    ceiling = record["gated_output_path_b"]
    scaled, scale = _scale_analyst_bands_to_cap(BN4_BANDS, ceiling)

    assert scale is not None
    assert scaled["base"] == pytest.approx(0.05)
    assert scaled["bear"] == pytest.approx(0.0383 * scale)
    assert scaled["bull"] == pytest.approx(0.3733 * scale)
    # Ordering and spread both survive.
    assert scaled["bear"] < scaled["base"] < scaled["bull"]
    assert scaled["bull"] > scaled["base"], (
        "bull collapsed onto the ceiling — the band was clipped, not scaled"
    )


def test_band_untouched_when_the_gate_does_not_fire():
    bands = {"bear": 0.05, "base": 0.10, "bull": 0.16}
    gated, record, _ = _gate_growth_cagr_divergence(
        bands["base"], 0.08, profile_name="Conglomerate", series=[])
    assert record is None
    scaled, scale = _scale_analyst_bands_to_cap(bands, gated)
    assert scale is None
    assert scaled == bands


def test_non_scenario_band_keys_are_preserved():
    """``analyst_count`` rides along in the band dict and must not be scaled —
    scaling it would corrupt the dispersion plausibility guard downstream."""
    bands = {**BN4_BANDS, "analyst_count": 14}
    scaled, scale = _scale_analyst_bands_to_cap(bands, CEILING)
    assert scale is not None
    assert scaled["analyst_count"] == 14
    for k in ("bear", "base", "bull"):
        assert scaled[k] == pytest.approx(bands[k] * scale)
