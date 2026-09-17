"""Gate B multiplied a one-shot margin delta by ten (2026-09-17, owner option (a)).

The defect
----------
`dcf_agent`'s Forward Gate B projects a Year-10 ROIC to decide whether terminal
growth survives the scenario. Its Y10 FCF margin read::

    _y10_fcf_margin = fcf_margin_base + md_abs * 10

But `md_abs` is a ONE-SHOT absolute delta applied to EVERY projected year. Six
lines later the same block hands it to `_project_dcf` as
``margin_delta_absolute=md_abs`` with ``margin_delta_per_year=0.0`` commented
"superseded by md_abs", and `_project_dcf` branches on exactly that::

    if margin_delta_absolute is not None:
        margin_t = max(fcf_margin_base + margin_delta_absolute, fcf_floor)
    else:
        margin_t = max(fcf_margin_base + margin_delta_per_year * t, fcf_floor)

The `× 10` is a fossil of the older per-year form the comment above it still
described. Since ``md_abs = fcf_margin_base × (m − 1)``, the buggy Y10 margin was
``fmb × (10m − 9)`` against a correct ``fmb × m`` — so the projected ROIC, which
is LINEAR in that margin (`_y10_ic` is scaled by the same revenue multiplier as
`_y10_fcf`, and the multiplier cancels), came out at ``(10m − 9)/m`` times the
right answer:

    bear,  m = 0.80 (standard)  →  −1.25×   margin −fmb      ROIC negative
    bear,  m = 0.65 (high SBC)  →  −3.85×   margin −2.5·fmb  ROIC negative
    bull,  m = 1.20 (standard)  →  +2.50×   margin  3·fmb    ROIC inflated
    bull,  m = 1.15 (high SBC)  →  +2.17×   margin  2.5·fmb  ROIC inflated
    base,  m = 1.00 (both)      →  +1.00×   md_abs is 0, so 10 × 0 = 0

Every bear case is negative. That fired Gate B — zeroing terminal growth — in
the bear scenario of **13 of the 14 golden fixtures**, so the entire golden bear
column was computed with no terminal value at all. And base was untouched, which
is why the headline IV never moved and nothing caught it.

The second consumer, and the reason bull moved too
--------------------------------------------------
L9144 overwrites `forward_roic` with that projection, and the growth-premium
quality gate at L9300-9305 reads the same variable::

    if forward_roic <= wacc:
        _gp_raw = 1.0
    else:
        _quality = min(1.0, (forward_roic - wacc) / wacc)
        _gp_raw = 1.0 + (_gp_raw_growth - 1.0) * _quality

`growth_premium` scales roughly twenty relative-value legs. Bull's Gate B
threshold is ``float("-inf")`` so the gate itself never fires there — the bull
damage is entirely this quality gate saturating at `_quality = 1.0` on an
inflated ROIC, and the bear damage includes it forcing the gate fully shut
(`_gp_raw = 1.0` exactly) in all 13 fixtures.

A third consumer was found while renaming the ledger key
--------------------------------------------------------
`pdf_report`'s two sensitivity grids rebuilt the projection as
``margin + margin_delta * t`` — the same per-year misreading, in user-facing
output, under a comment claiming it "match[ed] main DCF".

Its reach is narrower than it first looks, and the narrowing is worth pinning
rather than glossing. Both grids read ``dcf_ticker["base"]``, so the delta they
mis-scaled is the BASE scenario's — which is ``guidance_margin_adj``, and is 0
for all 14 golden fixtures and for any run where guidance does not move the
margin. ``0 × t`` is still ``0``, so for those runs the two formulas agreed and
the output was identical. Latent, not inert: on a synthetic base with
``md = −0.04`` the old grid's centre read **−$0.75 against the engine's +$4.14
(118% divergence)**, and at ``md = +0.04``, **+$13.93 against +$6.26 (123%)** —
and the grid's own `_sens_warn` check would have reported it as a
"revenue_base or shares_outstanding unit mismatch (check FX conversion)",
pointing the reader at two fields that were fine.
`test_the_grid_centre_reproduces_the_engine_iv` pins the corrected identity and
`test_the_old_per_year_reading_would_have_diverged` transcribes the pre-fix
closure verbatim as the control, so the identity test is provably not vacuous.

`_section_2f`'s traceability table had the arithmetic right all along
(``base + delta``, one step) and the LABEL wrong — it printed ``±X.XX%/yr``
under a row headed "Margin delta / year", telling the reader the margin kept
moving every year when the engine moves it once. That one was unconditional and
misdescribed every PDF ever produced.

The rename is a payload contract change, not an identifier change
-----------------------------------------------------------------
`md_abs` was published as ``margin_delta_per_year`` from the day
`_MARGIN_DELTA_MULT` landed. Every archived row in `web_runs` and every deployed
frontend/PDF build reads that name, so it cannot be retired: the ledger now
writes ``margin_delta_absolute`` (authoritative) AND ``margin_delta_per_year``
(deprecated alias, identical value), and both are pinned in the golden
projection so a future divergence fails the baseline instead of shipping it.

Measured re-baseline (every number below is measurement, not intent): ``base_iv``
unchanged in 14/14; bear Gate B deactivates on 5 fixtures and flips the ROIC
sign on 8 while still firing; bull's quality gate unbinds on **6** fixtures — not
the 9 reported to the owner when this was scoped, a miscount made by
string-matching `bull:` inside multi-line `forward_flags` prose; FCX's 12m band
fires for the first time and MELI's bear floor moves.
"""
from __future__ import annotations

import inspect
import json
import os
import re

import pytest
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Table

from src.agents.analysis import dcf_agent
from src.agents.analysis.dcf_agent import (
    _MARGIN_DELTA_MULT,
    _MARGIN_DELTA_MULT_HIGH_SBC,
    _project_dcf,
    _scenario_mults_for_profile,
)
from src.memory import golden_replay
from src.utils import pdf_report
from src.utils.pdf_report import _margin_delta_abs

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SNAPSHOTS = os.path.join(_REPO, "tests", "golden", "snapshots.json")
_SCENARIOS = ("bear", "base", "bull")

with open(_SNAPSHOTS, encoding="utf-8") as _fh:
    _SNAP = json.load(_fh)


def _fixtures() -> dict:
    """The 14 fixture entries, without the `_meta` sibling that has no projection."""
    return {k: v for k, v in _SNAP.items() if k != "_meta"}


def _proj(name: str) -> dict:
    return _fixtures()[name]["projection"]


def _strip_comments(src: str) -> str:
    """Drop comment lines so a comment can never satisfy its own absence test.

    The fix's own comment quotes the deleted ``* 10`` form three times over to
    explain what was wrong. A naive ``assert "md_abs * 10" not in src`` fails on
    the explanation; inverted, it passes by matching it.
    """
    return "\n".join(ln for ln in src.splitlines()
                     if not ln.lstrip().startswith("#"))


def _engine_src() -> str:
    return _strip_comments(inspect.getsource(dcf_agent.run_dcf_agent))


# The 14 fixtures' pre-fix baseline, transcribed from tests/golden/snapshots.json
# as it stood at commit a4bce28 (the working copy is at
# scratchpad/snapshots_prefix.json, which is gitignored — hence the literal
# table here). `bear_roic` is the ROIC Gate B printed into the bear flag; None
# means the gate did not fire.
_PREFIX = {
    #            base_iv   bear_tgr  bear_gp  bull_gp  bear_roic
    "02888_HK": (284.38,   0.0,      1.0,     0.719,   -10.4),
    "09988_HK": (160.84,   0.0,      1.0,     1.167,    -6.7),
    "AAPL":     (219.80,   0.0,      1.0,     1.101,   -55.3),
    "BABA":     (193.13,   0.0,      1.0,     1.019,    -6.7),
    "BN4_SI":   (5.20,     0.0,      1.0,     1.0,      -2.3),
    "C38U_SI":  (1.81,     0.0,      1.0,     1.2,      -2.8),
    "COST":     (1324.57,  0.0,      1.0,     1.088,   -20.0),
    "D05_SI":   (47.89,    0.01,     0.65,    0.731,    None),
    "FCX":      (25.58,    0.0,      1.0,     1.8,      -4.1),
    "MELI":     (5578.42,  0.0,      1.0,     1.086,  -119.4),
    "MU":       (293.14,   0.0,      1.0,     1.0,      -2.0),
    "SCHW":     (75.41,    0.0,      1.0,     1.654,    -7.3),
    "U96_SI":   (5.73,     0.0,      1.0,     1.0,      -2.3),
    "V":        (428.47,   0.0,      1.0,     1.144,   -41.7),
}

#: Gate B deactivated on exactly these five: their corrected bear ROIC clears
#: WACC, so terminal growth survives the bear scenario for the first time. Value
#: is the bear `tgr` each one now carries.
_DEACTIVATED = {"02888_HK": 0.01, "AAPL": 0.02, "COST": 0.01, "MELI": 0.02, "V": 0.01}

#: Gate B still fires on these eight — correctly, on a now-POSITIVE ROIC below
#: the bear threshold — and D05_SI never fired at all.
_STILL_FIRES = ("09988_HK", "BABA", "BN4_SI", "C38U_SI", "FCX", "MU", "SCHW", "U96_SI")

#: Bull's quality gate unbinds on exactly six: (old_gp, new_gp). The scoping note
#: said nine; the three extra were `forward_flags` prose containing "bull:".
_BULL_MOVED = {
    "02888_HK": (0.719, 0.815),
    "09988_HK": (1.167, 1.0),
    "BABA":     (1.019, 1.0),
    "C38U_SI":  (1.2,   1.0),
    "FCX":      (1.8,   1.0),
    "SCHW":     (1.654, 1.267),
}

_GATE_B_RE = re.compile(r"^Gate B \(bear\): Forward ROIC \((-?[\d.]+)%")


def _bear_gate_b_roic(projection: dict) -> float | None:
    """The ROIC Gate B printed into the bear flag, or None if it did not fire."""
    for flag in projection.get("scenarios.bear.forward_flags") or []:
        m = _GATE_B_RE.match(flag)
        if m:
            return float(m.group(1))
    return None


# ══════════════════════════════════════════════════════════════════════════════
# A. The arithmetic of the defect
# ══════════════════════════════════════════════════════════════════════════════

def test_the_multiplier_is_gone_from_the_assignment():
    src = _engine_src()
    assert "_y10_fcf_margin = fcf_margin_base + md_abs" in src
    # The fossil, in every spacing anyone would write it in.
    for form in ("md_abs * 10", "md_abs*10", "md_abs * 10.0",
                 "10 * md_abs", "10*md_abs", "md_abs * _PROJECTION_YEARS"):
        assert form not in src, f"the × 10 survived as {form!r}"


def test_the_engine_still_hands_the_delta_to_the_projector_as_absolute():
    """The fix is only coherent because of the call a few lines below it."""
    src = _engine_src()
    assert "margin_delta_absolute=md_abs" in src
    assert "margin_delta_per_year=0.0" in src


@pytest.mark.parametrize("profile,mults", [
    ("Payment Networks", _MARGIN_DELTA_MULT),
    ("Hyper-Growth Platform", _MARGIN_DELTA_MULT_HIGH_SBC),
])
@pytest.mark.parametrize("scenario", ["bear", "base", "bull"])
def test_the_buggy_margin_was_an_exact_multiple_of_the_right_one(
        profile, mults, scenario):
    """``fmb·(10m − 9)`` vs ``fmb·m`` — the ratio the ROIC inherits.

    Linear because `_y10_ic = ic_val × _y10_rev_mult` and
    `_y10_rev = rev_base × _y10_rev_mult` cancel in NOPAT/IC.
    """
    assert _scenario_mults_for_profile(profile)[1] is mults
    fmb = 0.30
    m = mults[scenario]
    md = fmb * (m - 1.0)
    buggy = fmb + md * 10
    correct = fmb + md
    assert buggy == pytest.approx(fmb * (10 * m - 9))
    assert correct == pytest.approx(fmb * m)
    if scenario == "base":
        # md is 0, so 10 × 0 = 0. THIS is why no headline IV ever moved and the
        # defect stayed invisible for as long as it did.
        assert md == 0.0 and buggy == correct == fmb
    elif scenario == "bear":
        assert buggy < 0 < correct, "every bear family produced a negative margin"
    else:
        assert buggy > correct > 0, "bull inflated the margin, never negated it"


@pytest.mark.parametrize("m,expected", [
    (0.80, -1.25),         # bear, standard
    (0.65, -3.846153846),  # bear, high SBC
    (1.20, 2.50),          # bull, standard
    (1.15, 2.173913043),   # bull, high SBC
])
def test_the_roic_error_factor(m, expected):
    """``(10m − 9) / m`` — the factor the projected ROIC was wrong by."""
    assert (10 * m - 9) / m == pytest.approx(expected, rel=1e-6)


def test_the_sign_flips_in_the_baseline_obey_that_factor_exactly():
    """Eight fixtures flipped ROIC sign; each ratio is ``m/(10m − 9)`` = −0.80.

    This is the strongest evidence that the × 10 was the ONLY thing wrong with
    the bear projection: a different defect would not leave the ratio pinned to
    the profile multiplier. Both sides are one-decimal printouts, so the
    tolerance is the two rounding half-widths composed — 0.8 × 0.05 on the old
    value plus 0.05 on the new — and nothing more.
    """
    tol = 0.80 * 0.05 + 0.05
    for name in _STILL_FIRES:
        old = _PREFIX[name][4]
        new = _bear_gate_b_roic(_proj(name))
        assert new is not None and new > 0, f"{name} should still fire, positively"
        assert old < 0
        # m = 0.80 for all eight; none is a high-SBC profile.
        assert _scenario_mults_for_profile(_proj(name)["profile"])[1] is _MARGIN_DELTA_MULT
        assert new == pytest.approx(-0.80 * old, abs=tol), name


def test_meli_is_the_one_high_sbc_bear_so_its_factor_is_the_other_one():
    """MELI's bear ROIC went from −119.4% to clearing an 11.4% WACC.

    Its factor is ``0.65/(10×0.65 − 9)`` = −0.26, not −0.80: −119.4 × −0.26 ≈
    +31%, far above the threshold. So the LARGEST printed error in the set is the
    one that deactivates the gate rather than merely re-signing it.
    """
    assert _scenario_mults_for_profile("Hyper-Growth Platform")[1] is _MARGIN_DELTA_MULT_HIGH_SBC
    corrected = -119.4 * (0.65 / (10 * 0.65 - 9))
    assert corrected == pytest.approx(31.04, abs=0.05)
    assert corrected > 11.4
    assert _bear_gate_b_roic(_proj("MELI")) is None


def test_project_dcf_holds_a_one_shot_delta_flat_for_ten_years():
    """The contract Gate B was violating, stated as behaviour."""
    _, _, _, rows = _project_dcf(
        revenue_base=10e9, fcf_margin_base=0.20, growth_rate=0.10,
        margin_delta_per_year=0.0, wacc=0.09, tgr=0.02, fcf_floor=-0.05,
        net_debt=1e9, shares=1e9, margin_delta_absolute=-0.04)
    margins = [r["fcf_margin"] for r in rows]
    assert len(margins) == 10
    assert all(m == pytest.approx(0.16) for m in margins), margins


def test_project_dcf_still_drifts_on_the_legacy_per_year_branch():
    """The fix must not have quietly rewritten the branch it does not use."""
    _, _, _, rows = _project_dcf(
        revenue_base=10e9, fcf_margin_base=0.20, growth_rate=0.10,
        margin_delta_per_year=-0.01, wacc=0.09, tgr=0.02, fcf_floor=-0.05,
        net_debt=1e9, shares=1e9, margin_delta_absolute=None)
    margins = [r["fcf_margin"] for r in rows]
    assert margins[0] == pytest.approx(0.19)
    assert margins[-1] == pytest.approx(0.10)
    assert len(set(margins)) == 10, "the legacy branch must still drift by t"


# ══════════════════════════════════════════════════════════════════════════════
# B. The coupling into growth_premium
# ══════════════════════════════════════════════════════════════════════════════

def test_forward_roic_is_overwritten_before_the_quality_gate_reads_it():
    """Source ORDER is the coupling; if this ever inverts, bull stops moving."""
    src = _engine_src()
    assert src.index("forward_roic = _forward_roic_proj") < \
           src.index("_quality = min(1.0, (forward_roic - wacc) / wacc)")


def test_the_bull_gate_b_threshold_is_minus_infinity():
    """So the bull damage cannot be Gate B itself — only the quality gate."""
    src = _engine_src()
    block = src[src.index("_gate_b_threshold = {"):src.index("}[scenario]")]
    assert '"bull": float("-inf")' in block
    assert '"bear": wacc' in block
    assert '"base": wacc * 0.5' in block


def _gp_raw(forward_roic: float, wacc: float, gp_growth: float) -> float:
    """Transcription of the quality gate at dcf_agent L9300-9305."""
    if forward_roic <= wacc:
        return 1.0
    quality = min(1.0, (forward_roic - wacc) / wacc)
    return 1.0 + (gp_growth - 1.0) * quality


@pytest.mark.parametrize("gp_growth", [1.5, 1.0, 0.7])
def test_a_roic_at_or_below_wacc_forces_the_premium_to_exactly_one(gp_growth):
    """The bear signature — and it is indistinguishable from "no growth signal".

    That ambiguity is why 13 forced-shut baselines read as plausible values
    rather than as a defect.
    """
    assert _gp_raw(0.09, 0.09, gp_growth) == 1.0
    assert _gp_raw(-0.104, 0.09, gp_growth) == 1.0   # 02888_HK's old bear ROIC
    assert _gp_raw(-1.194, 0.09, gp_growth) == 1.0   # MELI's old bear ROIC


@pytest.mark.parametrize("roic,quality", [
    (0.10, 1 / 9),   # just above WACC
    (0.135, 0.5),    # halfway to saturation
    (0.18, 1.0),     # exactly 2× WACC — saturation begins
    (0.25, 1.0),     # what a 2.5×-inflated 10% ROIC looks like
    (0.90, 1.0),     # and a 10× one; the gate cannot tell them apart
])
def test_the_quality_gate_saturates_at_twice_wacc(roic, quality):
    """`_quality = min(1.0, (roic − wacc)/wacc)` cannot distinguish 2× from 10×.

    A 2.5× ROIC inflation therefore does not merely overstate quality, it ERASES
    the distinction: any true ROIC above 0.8 × wacc was already scored as
    perfect. That is how FCX's bull premium came to sit on its 1.80 clamp
    ceiling, and why removing the inflation drops it to neutral rather than to
    some intermediate value.
    """
    wacc = 0.09
    assert min(1.0, (roic - wacc) / wacc) == pytest.approx(quality)
    # Consequence: a saturated quality gate passes the growth premium through
    # untouched, so the premium is the ungated growth-vs-sector figure.
    if quality >= 1.0:
        assert _gp_raw(roic, wacc, 1.7) == pytest.approx(1.7)


def test_the_forced_shut_premiums_are_where_the_baseline_says():
    """The eight that still fire keep 1.0; the five that cleared move off it."""
    for name in _STILL_FIRES:
        assert _proj(name)["scenarios.bear.growth_premium"] == 1.0, name
        assert _PREFIX[name][2] == 1.0
    for name in _DEACTIVATED:
        gp = _proj(name)["scenarios.bear.growth_premium"]
        assert gp != 1.0, f"{name} should have moved off the forced shut"
        assert _PREFIX[name][2] == 1.0, "and it was shut before"
    assert _proj("D05_SI")["scenarios.bear.growth_premium"] == 0.65
    assert _PREFIX["D05_SI"][2] == 0.65


# ══════════════════════════════════════════════════════════════════════════════
# C. The ledger writes both spellings
# ══════════════════════════════════════════════════════════════════════════════

def test_the_ledger_writes_the_accurate_key_and_the_alias():
    src = _engine_src()
    assert '"margin_delta_absolute": round(md_abs, 4)' in src
    assert '"margin_delta_per_year": round(md_abs, 4)' in src


def test_replay_pins_both_spellings():
    """A divergence between them must fail the baseline, not ship."""
    assert "margin_delta_absolute" in golden_replay._SCENARIO_KEYS
    assert "margin_delta_per_year" in golden_replay._SCENARIO_KEYS


def test_the_alias_is_equal_to_the_authoritative_key_everywhere():
    for name, fx in _fixtures().items():
        for scen in _SCENARIOS:
            a = fx["projection"][f"scenarios.{scen}.margin_delta_absolute"]
            b = fx["projection"][f"scenarios.{scen}.margin_delta_per_year"]
            assert a == b, f"{name}/{scen}: {a} != {b}"


def test_base_delta_is_zero_in_all_fourteen():
    """`md_abs = fmb × (1.00 − 1) + guidance_adj`, and guidance_adj is 0 here.

    This single fact is the whole reason the defect survived: base_iv is the
    number everybody reads, and 10 × 0 is 0.
    """
    for name, fx in _fixtures().items():
        assert fx["projection"]["scenarios.base.margin_delta_absolute"] == 0.0, name


def test_the_published_delta_is_the_multiplier_identity():
    """``md_abs == round(fcf_margin_base × (m − 1), 4)`` for all 14 × bear/bull.

    Recomputed from the profile the engine actually routed each fixture to, so
    MELI — the one high-SBC name in the set, m = 0.65/1.15, bear −0.1060 against
    bull +0.0454 — is checked against a different multiplier pair than the other
    thirteen (m = 0.80/1.20) rather than being waved through by symmetry.
    """
    for name, fx in _fixtures().items():
        p = fx["projection"]
        fmb = p["fcf_margin_base"]
        _, m_mult = _scenario_mults_for_profile(p["profile"])
        assert m_mult["bear"] < 1.0 < m_mult["bull"]
        for scen in ("bear", "bull"):
            want = round(fmb * (m_mult[scen] - 1.0), 4)
            got = p[f"scenarios.{scen}.margin_delta_absolute"]
            assert got == pytest.approx(want, abs=5e-5), f"{name}/{scen}"
            assert (got < 0) == (scen == "bear"), f"{name}/{scen} sign"


# ══════════════════════════════════════════════════════════════════════════════
# D. pdf_report read the same key and made the same mistake
# ══════════════════════════════════════════════════════════════════════════════

def test_the_helper_prefers_the_accurate_key():
    assert _margin_delta_abs({"margin_delta_absolute": -0.05,
                              "margin_delta_per_year": -0.03}) == -0.05


def test_the_helper_falls_back_to_the_archived_key():
    """Every row already in web_runs carries only the misnomer."""
    assert _margin_delta_abs({"margin_delta_per_year": -0.0528}) == -0.0528


@pytest.mark.parametrize("payload", [
    {}, {"margin_delta_absolute": None}, {"margin_delta_per_year": None},
    {"margin_delta_absolute": "n/a"}, {"margin_delta_absolute": 0},
])
def test_the_helper_never_raises_and_never_scales(payload):
    got = _margin_delta_abs(payload)
    assert isinstance(got, float)
    assert got == 0.0


@pytest.mark.parametrize("fn,step", [
    (pdf_report._sensitivity_table, "margin + margin_delta"),
    (pdf_report._sensitivity_table_growth_margin, "margin_val + margin_delta"),
])
def test_neither_grid_scales_the_delta_by_the_year(fn, step):
    body = _strip_comments(inspect.getsource(fn))
    for form in ("margin_delta * t", "margin_delta*t",
                 "margin_delta * YEARS", "margin_delta*YEARS"):
        assert form not in body, f"{fn.__name__} still drifts the margin: {form!r}"
    # And the one step it does take is a single statement, hoisted above the
    # t loop — which is the point: the projected margin is one value, not a drift.
    assert body.count(step) == 1, f"{fn.__name__}: expected exactly one {step!r}"
    assert body.index(step) < body.index("for t in range(1, YEARS + 1):"), \
        f"{fn.__name__}: the step must be hoisted out of the year loop"


def test_the_section_2f_row_says_one_shot_not_per_year():
    body = inspect.getsource(pdf_report._section_2f)
    assert "Margin delta (one-shot, Y1–Y10)" in body
    assert "Margin delta / year" not in body
    assert "%/yr" not in _strip_comments(body)


def test_both_grids_read_the_base_scenario_so_the_delta_is_usually_zero():
    """The reach of the per-year misreading, stated as a fact about the code.

    Neither grid takes a scenario argument: both pull `dcf_ticker["base"]`, whose
    delta is `guidance_margin_adj`. That is 0 for all 14 golden fixtures, so the
    old `* t` produced identical output for them — the defect was latent for
    unguided names and live for guided ones. A future edit that gives these grids
    a bear or bull block inherits the corrected one-step arithmetic for free,
    which is the point of centralising the read in `_margin_delta_abs`.
    """
    for fn in (pdf_report._sensitivity_table,
               pdf_report._sensitivity_table_growth_margin):
        body = _strip_comments(inspect.getsource(fn))
        assert 'dcf_ticker.get("base")' in body, fn.__name__
        assert "_margin_delta_abs(base)" in body, fn.__name__
        assert '"bear"' not in body and '"bull"' not in body, \
            f"{fn.__name__} now reads a non-base scenario — re-check the delta"
    for name, fx in _fixtures().items():
        assert fx["projection"]["scenarios.base.margin_delta_absolute"] == 0.0, name


# A synthetic name whose DCF is small enough that the grid renders two decimals
# (`_iv_prec = 2 if base_iv < 10`), so the centre cell can be compared to the
# engine at cent precision. Its base delta of −0.04 is the GUIDED case — the one
# where the pre-fix `* t` actually changed the output.
_SYNTH = dict(
    wacc=0.09, revenue_base=10e9, revenue_base_usd=10e9,
    shares_outstanding=10e9, net_debt=1e9, fcf_floor=-0.05,
    reported_currency="USD", fcf_margin_base=0.20, growth_rate=0.10, tgr=0.02,
)


def _engine_iv(md: float) -> float:
    """What the engine itself computes for this name, per share."""
    iv, _, _, _ = _project_dcf(
        revenue_base=_SYNTH["revenue_base"], fcf_margin_base=_SYNTH["fcf_margin_base"],
        growth_rate=_SYNTH["growth_rate"], margin_delta_per_year=0.0,
        wacc=_SYNTH["wacc"], tgr=_SYNTH["tgr"], fcf_floor=_SYNTH["fcf_floor"],
        net_debt=_SYNTH["net_debt"], shares=_SYNTH["shares_outstanding"],
        margin_delta_absolute=md)
    return iv


def _render(md: float, stored_iv: float) -> tuple[list, float]:
    """Render the WACC × TGR grid; return (flowables, centre-cell value)."""
    styles = {k: ParagraphStyle(name=k, fontName="Helvetica", fontSize=8, leading=10)
              for k in ("RptBody", "RptLabel", "RptValue")}
    dcf_ticker = dict(_SYNTH)
    dcf_ticker["base"] = {
        "tgr": _SYNTH["tgr"], "growth_rate": _SYNTH["growth_rate"],
        "fcf_margin_start": _SYNTH["fcf_margin_base"],
        "intrinsic_value": stored_iv, "margin_delta_absolute": md,
    }
    out = pdf_report._sensitivity_table(dcf_ticker, styles, page_w=400.0)
    assert any(isinstance(f, Table) for f in out), "the grid did not render"
    footer = next(f for f in out
                  if hasattr(f, "text") and "DCF IV $" in f.text).text
    centre = float(re.search(r"DCF IV \$(-?[\d.,]+)", footer)
                   .group(1).replace(",", ""))
    return out, centre


def test_the_grid_centre_reproduces_the_engine_iv():
    """The identity the grid's own divergence warning claims to police.

    With the delta stepped once and held flat, the grid's centre cell IS the
    engine's IV: the two formulas agree term for term whenever WACC is flat and
    ``tgr <= wacc − 0.005``, which holds here and for every fixture. The −0.04
    delta is the guided case — a non-zero BASE delta, the only kind these grids
    ever see, and the only kind the pre-fix ``* t`` could get wrong.
    """
    md = -0.04
    engine = _engine_iv(md)
    out, centre = _render(md, stored_iv=engine)
    assert centre == pytest.approx(engine, abs=0.005)
    joined = " ".join(getattr(f, "text", "") for f in out)
    assert "diverges" not in joined, "the centre matched the engine, so no warning"


def test_the_grid_warning_fires_when_the_stored_iv_really_is_wrong():
    """Control for the assertion above: the warning is reachable at all."""
    engine = _engine_iv(-0.04)
    out, centre = _render(-0.04, stored_iv=engine * 2)
    joined = " ".join(getattr(f, "text", "") for f in out)
    assert "diverges" in joined
    assert centre == pytest.approx(engine, abs=0.005), "the centre ignores stored_iv"


@pytest.mark.parametrize("md,expected_div,sign", [
    (-0.04, 1.181, -1),   # measured: engine +4.1374, old grid −0.7493
    (+0.04, 1.226, +1),   # measured: engine +6.2561, old grid +13.9285
])
def test_the_old_per_year_reading_would_have_diverged(md, expected_div, sign):
    """Discriminating control: the identity test above is not vacuous.

    Both directions, at the magnitudes quoted in the docstrings. The negative
    delta floors out (the drifted margin passes `fcf_floor` around Y5) so the old
    grid read LOW; the positive one compounds, so it read HIGH by more than the
    engine's entire value. Either would have surfaced as an FX/share-count
    warning blaming two innocent fields.
    """
    margin, fcf_floor = 0.20, -0.05
    rev, gr, YEARS = _SYNTH["revenue_base"], 0.10, 10
    wacc, tgr = _SYNTH["wacc"], 0.02

    # Verbatim transcription of the pre-fix `_iv` closure, from
    # `git show a4bce28:src/utils/pdf_report.py`, which carried the comment
    # "CHECK 3 FIX (b): apply margin delta per year, matching main DCF".
    def _iv_old(wacc_, tgr_):
        _tgr = min(tgr_, wacc_ - 0.005)
        pv = 0.0
        for t in range(1, YEARS + 1):
            margin_t = max(margin + md * t, fcf_floor)
            margin_t = min(margin_t, 0.60)
            pv += (rev * (1 + gr) ** t * margin_t) / (1 + wacc_) ** t
        margin_T = min(max(margin + md * YEARS, fcf_floor), 0.60)
        fcf_T = rev * (1 + gr) ** YEARS * margin_T
        tv = fcf_T * (1 + _tgr) / (wacc_ - _tgr)
        return (pv + tv / (1 + wacc_) ** YEARS
                - _SYNTH["net_debt"]) / _SYNTH["shares_outstanding"]

    engine = _engine_iv(md)
    old = _iv_old(wacc, tgr)
    divergence = abs(old - engine) / max(abs(engine), 1.0)
    assert divergence == pytest.approx(expected_div, abs=0.005), divergence
    assert divergence > 0.05, "below the warning threshold, so nothing would fire"
    assert (old - engine) * sign > 0, "direction of the old grid's error"


# ══════════════════════════════════════════════════════════════════════════════
# E. The named moves in the re-baselined golden set
# ══════════════════════════════════════════════════════════════════════════════

def test_base_iv_is_unchanged_in_all_fourteen():
    """The headline number did not move, and that is the point, not a miss."""
    for name, fx in _fixtures().items():
        assert fx["base_iv"] == _PREFIX[name][0], name


def test_bear_gate_b_deactivated_on_exactly_the_five_named_fixtures():
    for name, new_tgr in _DEACTIVATED.items():
        p = _proj(name)
        assert _bear_gate_b_roic(p) is None, f"{name}: Gate B still fires"
        assert p["scenarios.bear.tgr"] == new_tgr, name
        assert _PREFIX[name][1] == 0.0, "it fired before"
    for name in _STILL_FIRES:                      # and nobody else deactivated
        assert _bear_gate_b_roic(_proj(name)) is not None, name
    assert _proj("D05_SI")["scenarios.bear.tgr"] == 0.01


def test_no_bear_terminal_value_was_the_pre_fix_state():
    """13 of 14 pre-fix bear rows had tgr == 0.0 — no terminal value at all.

    Recorded because it is the scale of the defect: the golden bear column was
    not a conservative estimate, it was a ten-year annuity with no perpetuity,
    for every fixture but the one bank that never tripped the gate. After the
    fix exactly the eight that legitimately fail the gate remain at 0.0.
    """
    assert sum(1 for v in _PREFIX.values() if v[1] == 0.0) == 13
    now = [name for name, fx in _fixtures().items()
           if fx["projection"]["scenarios.bear.tgr"] == 0.0]
    assert sorted(now) == sorted(_STILL_FIRES)


#: Bear intrinsic values for the eight fixtures whose gate still fires, and for
#: D05_SI. Every one of these is IDENTICAL before and after the fix — measured
#: against the pre-fix snapshot, the whole diff for these nine fixtures is two
#: leaves long (the flag text, which prints the ROIC, and the new alias key).
#: Pinned as literals so that "did not move" is an assertion rather than an
#: absence.
_BEAR_IV_UNMOVED = {
    "09988_HK": 114.32,
    "BABA":     142.27,
    "BN4_SI":   7.37,
    "C38U_SI":  1.41,
    "D05_SI":   39.21,
    "FCX":      16.06,
    "MU":       222.82,
    "SCHW":     51.21,
    "U96_SI":   2.33,
}


def test_d05_is_the_one_fixture_whose_bear_column_did_not_change():
    """It was already clear of the gate, so the × 10 never reached its ROIC.

    D05_SI is the only fixture with a bear terminal value in the pre-fix
    baseline (tgr 0.01, premium 0.65 — the bank calibration's own path), and the
    only one whose bear leaves are untouched apart from the new alias key.
    """
    p = _proj("D05_SI")
    assert _PREFIX["D05_SI"][4] is None
    assert _bear_gate_b_roic(p) is None
    assert p["scenarios.bear.tgr"] == 0.01
    assert p["scenarios.bear.growth_premium"] == 0.65
    assert p["scenarios.bear.intrinsic_value"] == _BEAR_IV_UNMOVED["D05_SI"]


def test_the_sign_flips_changed_only_their_flag_text():
    """The eight still fire, so nothing downstream of the gate may move.

    `tgr` stays 0.0 and the premium stays at the forced 1.0, which leaves the
    bear IV exactly where it was — a bear IV moving here would mean the fix
    reached something it was not supposed to.
    """
    for name in _STILL_FIRES:
        p = _proj(name)
        assert p["scenarios.bear.tgr"] == 0.0, name
        assert p["scenarios.bear.growth_premium"] == 1.0, name
        assert p["scenarios.bear.intrinsic_value"] == _BEAR_IV_UNMOVED[name], name


def test_bull_quality_gate_unbinds_on_exactly_six_not_nine():
    """Corrects the count given to the owner when this was scoped.

    The three extras (AAPL, COST, MELI) have a `bull:` substring inside
    multi-line `forward_flags` prose — MELI's bull flag quotes its own bear IV,
    which did move — and their bull premiums never changed. String-matching a log
    is not counting a payload, so this asserts on the premium itself: the six
    named fixtures are at their new values and the other eight are at their
    pre-fix ones.
    """
    assert len(_BULL_MOVED) == 6, "the scoping note said nine; it is six"
    for name, (old_gp, new_gp) in _BULL_MOVED.items():
        p = _proj(name)
        assert _PREFIX[name][3] == old_gp, name
        assert p["scenarios.bull.growth_premium"] == new_gp, name
    unbound = set()
    for name in _fixtures():
        if name in _BULL_MOVED:
            continue
        # Unmoved: the bull premium still equals the pre-fix value, and the only
        # bull leaf that changed is the new alias key.
        assert _proj(name)["scenarios.bull.growth_premium"] == _PREFIX[name][3], name
        unbound.add(name)
    assert unbound == {"AAPL", "BN4_SI", "COST", "D05_SI", "MELI", "MU", "U96_SI", "V"}


def test_fcx_bull_premium_came_off_its_clamp_ceiling():
    """1.80 was the `_gp_raw` clamp ceiling, not a measured premium.

    With the ROIC no longer inflated 2.5×, `_quality` stops saturating and the
    premium collapses to neutral — a 37.3% cut to FCX's bull IV (72.62 → 45.52),
    the largest single move in the re-baseline.
    """
    p = _proj("FCX")
    assert p["scenarios.bull.growth_premium"] == 1.0
    assert p["scenarios.bull.intrinsic_value"] == 45.52
    assert _PREFIX["FCX"][3] == 1.8


def test_02888_is_the_one_bull_premium_that_rose():
    """A discount shrinking toward neutral, not a premium being granted.

    Its bull `_quality` was below 1 even inflated, so removing the inflation
    moved the premium UP from 0.719 to 0.815 — and because 02888_HK's bull IV
    carries no DCF weight, the headline barely moved (+0.7%).
    """
    p = _proj("02888_HK")
    assert p["scenarios.bull.growth_premium"] == 0.815
    assert p["scenarios.bull.intrinsic_value"] == 331.93
    assert 0.815 < 1.0


def test_fcx_12m_band_fires_for_the_first_time():
    """Its own bear IV moved enough to put the analyst PT outside [0.33×, 2.50×].

    bear $42.71 = 2.659× a bear IV of $16.06 — so all three targets are replaced
    with the validation fallback and then bounded by the convergence cap. The
    band is decision 2c's; this fix is what first gave it something to catch.
    """
    p = _proj("FCX")
    assert p["12m_pt_method"].startswith("validation fallback")
    assert (p["12m_targets.bear"], p["12m_targets.base"], p["12m_targets.bull"]) == \
           (17.89, 23.85, 29.81)
    assert "pt_over_scenario_iv" in p["gate_metrics"]
    for scen in _SCENARIOS:
        flags = p[f"scenarios.{scen}.forward_flags"]
        assert any("VALIDATION ERROR: 12m PT band violated" in f for f in flags), scen


def test_meli_bear_floor_mechanics_and_its_policy_conflict():
    """MELI's bear IV rose 7.7%, which moved the band floor above the ceiling.

    `0.33 × 4940.12 = 1630.24` now exceeds the high-SBC bear ceiling
    `1828.94 × 0.85 = 1554.60`. The band floor is kept and the conflict is
    declared on the card rather than resolved silently. Pre-fix the ratio was
    0.271× and the ceiling won at 1554.60; base and bull targets did not move.
    """
    p = _proj("MELI")
    assert p["12m_targets.bear"] == 1630.24
    assert (p["12m_targets.base"], p["12m_targets.bull"]) == (3141.26, 3334.71)
    assert p["scenarios.bear.intrinsic_value"] == 4940.12
    flags = " ".join(p["scenarios.bear.forward_flags"])
    assert ("POLICY CONFLICT: bear band floor 1,630.24 exceeds the high-SBC "
            "ceiling 1,554.60 — band floor kept") in flags
    assert "0.251x its own IV $4,940.12" in flags
    # The same 12m band flag is written into all three scenarios' lists, so
    # MELI's BULL forward_flags moved too — because the text quotes the bear IV,
    # not because anything in MELI's bull valuation changed. That is exactly the
    # shape of leaf that got counted as a "bull move" in the scoping note.
    for scen in _SCENARIOS:
        assert any("POLICY CONFLICT" in f
                   for f in p[f"scenarios.{scen}.forward_flags"]), scen


def test_the_bear_deactivations_raise_bear_iv_and_nothing_else_does():
    """Direction check on the five: a surviving terminal value can only add."""
    raised = {"02888_HK": 236.10, "AAPL": 156.17, "COST": 956.33,
              "MELI": 4940.12, "V": 310.93}
    before = {"02888_HK": 236.57, "AAPL": 146.37, "COST": 923.28,
              "MELI": 4588.16, "V": 276.28}
    for name, want in raised.items():
        got = _proj(name)["scenarios.bear.intrinsic_value"]
        assert got == want, name
        if name == "02888_HK":
            # The exception, and an instructive one: its bear IV carries no DCF
            # weight, so the surviving tgr adds nothing while the premium moving
            # off the forced 1.0 (→ 0.969) shaves the multiple legs. Net −0.2%.
            assert got < before[name]
            assert _proj(name)["scenarios.bear.growth_premium"] == 0.969
        else:
            assert got > before[name], name


# ── F. The premium is a leg-wide multiplier, and the two legs that skip it
#       are the documented exceptions — which is what makes 02888_HK's bear
#       IV the one that fell ──────────────────────────────────────────────


def _method_value_src() -> str:
    return _strip_comments(inspect.getsource(dcf_agent._compute_method_value))


def test_fourteen_of_sixteen_leg_multiples_carry_the_premium():
    """The census behind "a premium fix re-prices the blend".

    Not sixteen of sixteen, and asserting that would be asserting against two
    deliberate design decisions. NAV Discount omits the premium because a
    market-observed mNAV already prices growth, and P/TBV omits it because a
    bank's book multiple is set by ROE vs CoE while the premium's revenue base
    for a bank is gross interest income — D05.SI once priced P/TBV at 0.84x
    tangible book, below liquidation, because of exactly that. Both say so at
    their own branch. What this pins is that the exceptions are still exactly
    those two: a seventeenth assignment that quietly drops the premium would
    decouple a leg from the blend with nothing to catch it.
    """
    body = _method_value_src()
    mults = [ln.strip() for ln in body.splitlines() if ln.strip().startswith("mult = ")]
    assert len(mults) == 16
    with_gp = [m for m in mults if "growth_premium" in m]
    without = [m for m in mults if "growth_premium" not in m]
    assert len(with_gp) == 14
    assert without == ['mult = _mnav * sm', 'mult = cfg["p_tbv"] * sm']


def test_the_fcf_yield_leg_reaches_the_same_scaling_from_the_other_side():
    """Its premium sits in a denominator, so the VALUE still scales with it.

    This is why decision 5's pin on the published FCF-Yield values broke when
    the bear premium moved: `target_yield = peer / (sm * growth_premium)` and
    the leg returns `(fcf/shares) / target_yield`, so the premium cancels into
    the numerator. A test that only looked for `* growth_premium` would have
    reported this leg as premium-independent and the breakage as a mystery.
    """
    body = _method_value_src()
    line = [ln for ln in body.splitlines() if "target_yield = peer" in ln]
    assert len(line) == 1
    assert "/ (sm * growth_premium)" in line[0]
    assert "(fcf / shares) / target_yield" in body


_BANK_LEGS = ["Excess Capital", "GGM (P/B)", "P/TBV", "Residual Income"]
_02888_LEGS = {
    "bear": {"Excess Capital": 188.02, "GGM (P/B)": 252.98, "P/TBV": 170.72,
             "Residual Income": 230.90, "Forward P/E": 138.28, "P/E (norm)": 132.80},
    "bull": {"Excess Capital": 188.02, "GGM (P/B)": 421.63, "P/TBV": 284.54,
             "Residual Income": 230.90, "Forward P/E": 153.21, "P/E (norm)": 186.07},
}


_02888_BLEND = {"bear": (214.6404, 236.1044), "bull": (301.759, 331.9349)}


def test_02888_six_legs_split_exactly_along_the_premium_line():
    """The census above, measured on the one fixture whose blend straddles it.

    Two premium-scaled P/E legs move; four bank legs are bit-identical. In bear
    the two move DOWN (premium 1.0 -> 0.969) and in bull they move UP (0.719 ->
    0.815, a discount shrinking toward neutral) — the four bank legs do not
    move in either direction. Only P/E (norm) of the two is in this fixture's
    weighted `methods_used`; Forward P/E is recorded but not blended.

    Nothing downstream of the blend contributed either: `iv_multi_post` is
    exactly 1.1 x `iv_multi` for this fixture both before and after the fix
    (bear 215.0645 -> 214.6404 and 236.571 -> 236.1044, both -0.197%; bull
    299.5749 -> 301.759 and 329.5323 -> 331.9349, both +0.729%), so the
    post-adjustment is a constant scale and the -0.2% / +0.7% is entirely the
    legs. That is what makes this the only fixture in the baseline whose bear
    IV fell despite gaining a terminal value.
    """
    for scen, legs in _02888_LEGS.items():
        proj = _proj("02888_HK")
        table = {k: v for k, v in proj.items()
                 if k.startswith(f"scenarios.{scen}.method_iv_table.")}
        assert sorted(table) == sorted(
            f"scenarios.{scen}.method_iv_table.{m}" for m in legs), scen
        for method, want in legs.items():
            got = table[f"scenarios.{scen}.method_iv_table.{method}"]
            assert got == want, (scen, method, got)
        # The post-adjustment is a constant 1.1x scale on this fixture, so it
        # transmitted the leg move without adding anything of its own.
        multi = proj[f"scenarios.{scen}.iv_multi"]
        post = proj[f"scenarios.{scen}.iv_multi_post"]
        assert (multi, post) == _02888_BLEND[scen], scen
        assert post == pytest.approx(multi * 1.1, rel=1e-6), (scen, multi, post)


def test_02888_carries_no_dcf_leg_so_a_surviving_tgr_adds_nothing():
    """The other half of why its bear IV can fall.

    `weight_dcf = 0.0` in all three scenarios and the `iv_dcf` leaf is None:
    this is the bank path, valued on GGM, residual income, excess capital and
    earnings multiples. Gate B deactivating hands it back a terminal growth
    rate that the published number never reads.

    Note the leaf EXISTS and is None rather than being absent — `.get()` hides
    the difference, and an earlier draft of this test asserted absence and
    passed for the wrong reason on a fixture that has the key.
    """
    proj = _proj("02888_HK")
    for scen in ("bear", "base", "bull"):
        assert proj[f"scenarios.{scen}.weight_dcf"] == 0.0, scen
        assert f"scenarios.{scen}.iv_dcf" in proj, scen
        assert proj[f"scenarios.{scen}.iv_dcf"] is None, scen
        # The WEIGHTED set. Five legs, not the six the table records — see
        # test_the_leg_table_is_a_superset_of_the_weighted_set.
        assert sorted(proj[f"scenarios.{scen}.methods_used"]) == sorted(
            _BANK_LEGS + ["P/E (norm)"]), scen
    # ... and it did gain the terminal growth rate, so the two facts coexist.
    assert proj["scenarios.bear.tgr"] == 0.01
    assert proj["scenarios.bear.growth_premium"] == 0.969


def test_the_leg_table_is_a_superset_of_the_weighted_set():
    """Pinned as an observation, because it changes how the census above reads.

    `method_iv_table` records every leg that was COMPUTED; `methods_used` is
    the subset that carries weight in the blend. The table has at least one leg
    the weighted set lacks in ALL 42 (fixture, scenario) pairs — AAPL's table
    carries four such legs (Forward EV/EBITDA, Forward P/E, SOTP (segments),
    SOTP 12m (probabilistic)) — and the weighted set names a leg with no table
    entry in exactly six pairs, enumerated below.

    The consequence for this fix is specific and worth stating rather than
    leaving to inference: of 02888_HK's two moved legs, only P/E (norm) is in
    its weighted set, so Forward P/E's 142.70 -> 138.28 is recorded but not
    blended. Whether the gap is intentional disclosure or a reporting defect is
    NOT settled here; this test only stops it changing silently.
    """
    reverse: dict[tuple[str, str], list[str]] = {}
    forward = 0
    for name in _fixtures():
        proj = _proj(name)
        for scen in ("bear", "base", "bull"):
            pre = f"scenarios.{scen}."
            used = set(proj[pre + "methods_used"])
            table = {k[len(pre) + len("method_iv_table."):]
                     for k in proj if k.startswith(pre + "method_iv_table.")}
            if used - table:
                reverse[(name, scen)] = sorted(used - table)
            forward += bool(table - used)
    assert forward == 42, forward
    assert reverse == {
        ("BN4_SI", "bear"): ["EV/EBITDA"],
        ("BN4_SI", "base"): ["DCF"],
        ("MELI", "bear"): ["Power Law Score"],
        ("MELI", "base"): ["Power Law Score"],
        ("MELI", "bull"): ["Power Law Score"],
        ("U96_SI", "base"): ["DCF"],
    }, reverse
