"""
Guards for the unified bank valuation triplet.

Three defects shipped together on 02888.HK and are pinned here:

1. TWO TRIPLETS ON ONE PAGE. The P/TBV card resolved its own (realised ROE on
   total equity, profile CoE) while the 12m PT ran the research RoTE against
   the research CoE. On 02888.HK that printed 0.93x (HK$151) beside 2.15x
   (HK$350) — a 2.3x spread with no way for a reader to reconcile it.

2. BASIS MISMATCH. A justified multiple is only valid against the book it was
   derived on. `fair_p_tbv` divided a total-equity return and multiplied by
   TANGIBLE book; `_compute_ggm_pb` took the tangible-basis research RoTE and
   multiplied by TOTAL book. Both directions were wrong, and they were wrong
   in opposite directions, which is what made the spread so wide.

3. NO GUARD ON THE MOST SENSITIVE INPUT. The multiple is driven by RoTE above
   all else, and RoTE is the one input taken on trust from research. On
   02888.HK the filings imply ~10.8% against a reported 17.9% and nothing
   flagged it.

Convergence: after the fix the two paths differ by EXACTLY

    P/TBV x TBV  -  P/B x BV  =  g (BV - TBV) / (CoE - g)

which is the analytic difference between the two Gordon identities, not model
error. test_convergence_residual_is_the_analytic_term pins that, so a future
change that reintroduces a basis error shows up as a residual that no longer
matches theory.
"""
from __future__ import annotations

import pytest

from src.agents.analysis.dcf_agent import (
    _BANK_ROTE_DIVERGENCE_BPS,
    _compute_bank_metrics,
    _return_on_book_basis,
)

clamp = lambda x: max(0.3, min(4.0, x))


# ── fix 2: basis conversion ────────────────────────────────────────────────

def test_rote_exceeds_roe_when_intangibles_exist():
    """Same earnings over a smaller book must give a higher return."""
    row = {"net_income": 5_191_685_000, "total_equity": 54_586_000_000,
           "goodwill": 2_423_000_000, "intangible_assets": 3_808_000_000,
           "shares_outstanding": 2_404_000_000, "total_assets": 919_955_000_000}
    m = _compute_bank_metrics(row, profile_name="Money Center Bank")
    assert m["roe"] == pytest.approx(0.0951, abs=1e-3)
    assert m["rote"] == pytest.approx(0.1074, abs=1e-3)
    assert m["rote"] > m["roe"]


def test_rote_equals_roe_when_no_intangibles():
    row = {"net_income": 100.0, "total_equity": 1000.0, "goodwill": 0,
           "intangible_assets": 0, "shares_outstanding": 10.0, "total_assets": 5000.0}
    m = _compute_bank_metrics(row, profile_name="Money Center Bank")
    assert m["rote"] == pytest.approx(m["roe"])


def test_return_on_book_basis_scales_down():
    """RoTE -> ROE shrinks by TBV/BV (18% tangible = 15.95% on total book)."""
    assert _return_on_book_basis(0.18, 183.46, 162.52) == pytest.approx(0.1595, abs=1e-4)


@pytest.mark.parametrize("bvps,tbvps", [(None, 10.0), (10.0, None), (0, 10.0), (10.0, 0)])
def test_return_on_book_basis_degrades_to_input(bvps, tbvps):
    """Unusable books return the input unchanged rather than None."""
    assert _return_on_book_basis(0.18, bvps, tbvps) == 0.18


def test_return_on_book_basis_passes_none_through():
    assert _return_on_book_basis(None, 10.0, 9.0) is None


# ── fix 1 + convergence ────────────────────────────────────────────────────

# (ticker, g, CoE, RoTE valued, BVPS, TBV/sh) — from the archived runs.
CASES = [
    ("02888.HK", 0.04,  0.105, 0.180, 183.46,  162.52),
    ("D05.SI",   0.033, 0.086, 0.165,  24.18,   22.0585),
    ("O39.SI",   0.03,  0.091, 0.128,  13.8126, 12.8551),
]


@pytest.mark.parametrize("tk,g,coe,rote,bv,tbv", CASES)
def test_convergence_residual_is_the_analytic_term(tk, g, coe, rote, bv, tbv):
    """Both paths, one triplet -> they differ only by g(BV-TBV)/(CoE-g)."""
    card = tbv * clamp((rote - g) / (coe - g))
    roe_book = _return_on_book_basis(rote, bv, tbv)
    ggm = bv * clamp((roe_book - g) / (coe - g))
    assert card - ggm == pytest.approx(g * (bv - tbv) / (coe - g), abs=0.02)


@pytest.mark.parametrize("tk,g,coe,rote,bv,tbv", CASES)
def test_paths_agree_within_five_percent(tk, g, coe, rote, bv, tbv):
    """Before the fix these were 10-89% apart."""
    card = tbv * clamp((rote - g) / (coe - g))
    roe_book = _return_on_book_basis(rote, bv, tbv)
    ggm = bv * clamp((roe_book - g) / (coe - g))
    spread = abs(card - ggm) / ((card + ggm) / 2)
    assert spread < 0.05, f"{tk}: paths {spread:.1%} apart"


def test_unconverted_roe_would_break_convergence():
    """Negative control: skipping the conversion reopens the spread."""
    g, coe, rote, bv, tbv = 0.04, 0.105, 0.18, 183.46, 162.52
    card = tbv * clamp((rote - g) / (coe - g))
    ggm_wrong = bv * clamp((rote - g) / (coe - g))     # the old behaviour
    assert abs(card - ggm_wrong) / ((card + ggm_wrong) / 2) > 0.10


# ── fix 4: divergence guard ────────────────────────────────────────────────

@pytest.mark.parametrize("realised,valued,should_fire", [
    (0.1074, 0.180, True),    # 02888.HK — ~7pt gap, the case that motivated this
    (0.1739, 0.165, False),   # D05.SI   — -89bps, ordinary statutory/underlying noise
    (0.1268, 0.128, False),   # O39.SI   — +12bps
    (0.10,   0.14,  True),    # +400bps
    (0.14,   0.10,  True),    # -400bps — fires in BOTH directions
])
def test_divergence_guard_selectivity(realised, valued, should_fire):
    gap_bps = (valued - realised) * 10000
    assert (abs(gap_bps) > _BANK_ROTE_DIVERGENCE_BPS) is should_fire


def test_divergence_threshold_is_sane():
    assert 100.0 <= _BANK_ROTE_DIVERGENCE_BPS <= 1000.0


# ── P/TBV card suppression where the profile excludes the method ───────────

def _bank_profiles():
    from src.data.sector_profiles import INDUSTRY_VALUATION_PROFILES
    fin = INDUSTRY_VALUATION_PROFILES.get("Financials", {})
    return {n: p for n, p in fin.items() if "Bank" in n or "GSE" in n}


def test_sg_money_center_excludes_ptbv():
    """The SG profile states the exclusion; the flag must reflect it.

    DBS and OCBC both report zero goodwill and zero intangibles, so P/TBV
    collapses onto P/B and the card adds nothing — while contradicting the
    methodology panel two cards above it.
    """
    p = _bank_profiles()["Money Center Bank (SG)"]
    assert "P/TBV" in p["excluded"]


def test_other_bank_profiles_keep_ptbv():
    """Suppression must be narrow — 02888.HK carries real goodwill (TBV/BV 0.89)."""
    profiles = _bank_profiles()
    excluded = {n for n, p in profiles.items() if "P/TBV" in (p.get("excluded") or [])}
    assert excluded == {"Money Center Bank (SG)"}, sorted(excluded)


def test_every_bank_profile_has_an_anchor():
    """The card names the anchor when it drops the P/TBV headline."""
    for name, p in _bank_profiles().items():
        anchors = [m["name"] for m in p.get("methods", []) if m.get("anchor")]
        assert len(anchors) == 1, f"{name}: {anchors}"
