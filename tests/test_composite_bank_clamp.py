"""Which profiles get the composite quality/risk tilt clamped to [0.90, 1.10].

The clamp exists because the composite scores a bank on ROE, CET1, NPL and
cost-income, and the GGM / 2-stage Residual Income models already consume those
same inputs to set the book multiple — applying the tilt on top double-counts
the evidence (D05.SI earned 1.39x off a 15.9% ROE and 16.9% CET1, lifting
blended IV from S$29.82 to S$41.44 on top of a ROE-driven model).

The predicate used to lead with ``is_bank_sector(sector)``, which tests the
SECTOR and so swept in every Financials name. ``is_bank_sector``'s own docstring
warns against exactly that —

    Note: also returns True for insurance/asset-management — callers that need
    bank-only should additionally check profile_name for 'Bank'.

— but the warning was unusable at that call site, because the sector test was
joined to the profile tests with ``or`` rather than ``and``. A disjunction
cannot be narrowed by adding a conjunct. Measured cost (owner decision,
2026-09-17):

    ICE  raw composite 1.2224 -> clamped 1.10   IV 132.47 -> 147.06   +11.0%
         gap to spot -13.6% -> -4.0%
    V    raw composite 1.1703 -> clamped 1.10   IV 281.05 -> 293.19    +4.3%
         gap to spot -23.8% -> -20.5%

Neither is a bank. ``Market Infrastructure`` and ``Payment Networks`` appear in
neither table, and their quality comes from margin and volume rather than from
ROE-on-book, so there was no book-value engine to double-count against.

The golden baseline cannot verify this change. Of the 14 fixtures exactly one
(V) has a released profile, and its recorded ``composite_applied`` is 1.0 — the
replay's KPI vector never pushes the raw composite above the band, so the clamp
was not binding on the fixture even before the fix. The verification is this
enumeration plus a production basket re-run.
"""

from __future__ import annotations

import inspect
import types

import pytest

from src.agents.analysis.dcf_agent import (
    _BANK_PROFILE_CALIBRATION,
    _BANK_PROFILES,
    _is_bank_for_composite,
    run_dcf_agent,
)
from src.agents.industry.sector_prompts import is_bank_sector
from src.data.sector_kpi_framework import SECTOR_KPI_FRAMEWORK
from src.data.sector_profiles import INDUSTRY_VALUATION_PROFILES


# Every profile the owner's decision releases from the clamp. These are the
# Financials-sector names that are not banks: their IV comes from a multiples
# leg and (for ICE/V) a DCF, not from a book-value engine.
RELEASED = (
    "Market Infrastructure",
    "Market Infrastructure (SG)",
    "Payment Networks",
    "Real Estate Asset Manager (SG)",
)

# The full set of framework profiles that DO get clamped, pinned explicitly so
# that adding a name to either table is a visible act with a failing test
# rather than a silent widening of a 20%-of-IV restriction.
CLAMPED_FRAMEWORK_PROFILES = frozenset({
    "Bank / Lending Institution",
    "Brokerage",
    "EM Bank",
    "EM Bank (Premium)",
    "FinTech",
    "Insurance",
    "Investment Bank",
    "Money Center Bank",
    "Money Center Bank (EU)",
    "Money Center Bank (SG)",
    "Mortgage/GSE",
    "Neo/Challenger",
    "Regional Bank",
    "Super-Regional Bank",
})


def _all_known_profiles() -> set[str]:
    """Every profile name the taxonomy can route to.

    Both registries, because a profile can exist in one and not the other
    (``Asset Manager`` is in ``_BANK_PROFILES`` and in neither framework dict).
    """
    return set(SECTOR_KPI_FRAMEWORK) | set(INDUSTRY_VALUATION_PROFILES)


# ── the released names ──────────────────────────────────────────────────────


@pytest.mark.parametrize("profile", RELEASED)
def test_non_bank_financials_are_released(profile):
    """The four fee-driven Financials profiles no longer get clamped."""
    assert _is_bank_for_composite(profile) is False


@pytest.mark.parametrize("profile", RELEASED)
def test_the_release_is_a_divergence_from_the_sector_test(profile):
    """Each released profile sits in a sector the old predicate matched.

    This is the shape of the defect, pinned: the sector test said "bank" and the
    profile said "not a bank", and the disjunction let the sector win.
    """
    assert is_bank_sector("Financials") is True
    assert _is_bank_for_composite(profile) is False


def test_ice_and_v_profiles_are_the_measured_cases():
    """The two names the owner's decision is quantified on."""
    assert _is_bank_for_composite("Market Infrastructure") is False   # ICE
    assert _is_bank_for_composite("Payment Networks") is False        # V


# ── the kept names ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "profile", sorted(k for k in _BANK_PROFILE_CALIBRATION if k != "default")
)
def test_every_calibration_profile_is_clamped(profile):
    """Arm 1: a GGM / Residual-Income row to double-count against."""
    assert _is_bank_for_composite(profile) is True


@pytest.mark.parametrize("profile", sorted(_BANK_PROFILES))
def test_every_dispatch_profile_is_clamped(profile):
    """Arm 2: the 12m-target dispatch set, which carries FinTech and Asset
    Manager on the same float-funding judgement Phase 1.2A's Tier 2 makes by
    measurement."""
    assert _is_bank_for_composite(profile) is True


@pytest.mark.parametrize(
    "profile",
    ["Commercial Bank", "Savings Bank", "Bank of Nowhere", "Thai Bank (Retail)"],
)
def test_bank_substring_arm(profile):
    """Arm 3: the substring test, now scoped to the profile not the sector."""
    assert _is_bank_for_composite(profile) is True


def test_brokerage_stays_clamped():
    """Owner decision 4: do NOT release Brokerage.

    SCHW and IBKR are fee + NII blended and are kept out of the FCFF DCF
    because their operating cash flow oscillates with bank-depository sweep
    balances and brokerage receivables ($1.2bn -> 18.9bn -> 2.0bn -> 8.8bn).
    The clamp is retained for the same structural reason as the banks — a
    book-value engine already prices the quality — and in any case is not
    binding: SCHW's raw composite measures 1.0918, inside [0.90, 1.10].
    """
    assert "Brokerage" in _BANK_PROFILE_CALIBRATION
    assert _is_bank_for_composite("Brokerage") is True


def test_the_two_d05_and_02888_fixture_profiles_stay_clamped():
    """The golden fixtures whose profiles were clamped before stay clamped, so
    this change moves neither of them."""
    assert _is_bank_for_composite("Money Center Bank (SG)") is True   # D05_SI
    assert _is_bank_for_composite("Money Center Bank") is True        # 02888_HK


# ── falsy and sentinel inputs ───────────────────────────────────────────────


@pytest.mark.parametrize("profile", [None, "", "default"])
def test_falsy_and_sentinel_inputs_are_not_banks(profile):
    """An unknown profile is not a bank, and the clamp restricts a valuation
    rather than defaulting one. ``"default"`` is a fallback ROW in the
    calibration dict, not a profile — no framework profile is named that."""
    assert _is_bank_for_composite(profile) is False


def test_default_is_not_a_real_profile():
    """Pins the assumption the sentinel exclusion rests on."""
    assert "default" not in _all_known_profiles()


# ── the whole taxonomy, enumerated ──────────────────────────────────────────


def test_clamped_set_over_every_known_profile_is_pinned():
    """Adding a profile to either table widens a 20%-of-IV restriction.

    This test fails when that happens, so the widening has to be a deliberate
    edit here rather than a side effect of a calibration row nobody re-read.
    """
    actual = {p for p in _all_known_profiles() if _is_bank_for_composite(p)}
    assert actual == set(CLAMPED_FRAMEWORK_PROFILES)


def test_no_known_profile_is_clamped_only_by_the_substring_arm():
    """Documents what arm 3 currently contributes: nothing on its own.

    Every framework profile containing "Bank" is already a calibration key. The
    arm is kept because the taxonomy grows by extraction and an unmapped
    bank-like profile should still be clamped, but a reader is entitled to know
    it is load-bearing for no profile that exists today.
    """
    for p in _all_known_profiles():
        if "Bank" in p:
            assert (p in _BANK_PROFILE_CALIBRATION) or (p in _BANK_PROFILES), p


def test_fintech_and_insurance_are_clamped_by_the_dispatch_arm():
    """The two names arm 2 adds over arms 1 and 3 — neither contains "Bank" and
    neither has a calibration row, but both are float-funded in fact."""
    for p in ("FinTech", "Insurance"):
        assert "Bank" not in p
        assert p not in _BANK_PROFILE_CALIBRATION
        assert p in _BANK_PROFILES
        assert _is_bank_for_composite(p) is True


# ── the signature, and the scope hazard that made this a two-part change ────


def test_sector_is_not_a_parameter():
    """The predicate must not be able to see the sector.

    A signature that accepted ``sector`` and ignored it would invite the test
    back in at the next edit. Keeping it out makes the narrowing structural.
    """
    params = list(inspect.signature(_is_bank_for_composite).parameters)
    assert params == ["profile_name"]


def test_bank_profiles_is_module_level_and_not_a_run_dcf_agent_local():
    """``_BANK_PROFILES`` was assigned inside ``run_dcf_agent``, ~1000 lines
    below the composite clamp.

    A name assigned anywhere in a function body is local to the WHOLE body, so
    reading it from the earlier site raises ``UnboundLocalError`` rather than
    falling back to a module-level default — the hoist had to delete the local,
    not merely shadow it. Pinned so a re-introduced local fails here instead of
    at valuation time inside a ``try/except`` that silently defaults the
    composite to 1.0.
    """
    assert isinstance(_BANK_PROFILES, frozenset)
    code = run_dcf_agent.__code__
    assert "_BANK_PROFILES" not in code.co_varnames
    assert "_is_bank_for_composite" not in code.co_varnames
    # ... and not rebound as a local in any nested scope either.
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            assert "_BANK_PROFILES" not in const.co_varnames


def test_the_module_level_set_matches_the_dispatch_reader():
    """The 12m-target block reads the same object, so the hoist cannot have
    changed dispatch. Membership is the pre-hoist literal, verbatim."""
    assert set(_BANK_PROFILES) == {
        "Money Center Bank", "Regional Bank", "Mortgage/GSE",
        "Investment Bank", "Insurance", "FinTech", "Asset Manager",
        "Bank / Lending Institution",
    }
