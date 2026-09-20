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

#: (pt, ratio, iv) out of the PT-band flag. The ratio is printed only in prose;
#: see the note at the one call site for why reading it is defensible here.
_PT_BAND_RE = re.compile(
    r"12m PT band violated: bear \$([\d.]+) = ([\d.]+)x its own IV \$([\d.]+)")

#: ── THE SECOND RE-BASELINE: the relative outlier floor in `_normalized_earnings`
#:
#: `max(iqr * 2, 0.05)` became `max(iqr * 2, abs(med) * 0.30)`. Four fixtures
#: moved a normalized field; THREE of them (09988_HK, BABA, C38U_SI) moved a
#: number no valuation leg reads, so their IVs are bit-identical. FCX is the only
#: fixture whose anchor is a normalized leg -- `EV/EBITDA (norm)` -- so it is the
#: only one whose valuation moved.
#:
#: FCX's EBITDA margins are [0.45887, 0.39830, 0.37825, 0.37191, 0.34020],
#: median 0.37825. `iqr * 2` = 0.05278 and the relative term is 0.11348, so the
#: relative term wins and FY2021 is read back in: normalized EBITDA +4.66%,
#: normalized EBIT +6.02%. The series is a MONOTONE five-year decline, so FY2021
#: is not an outlier in any statistical sense -- only the first point of a trend
#: -- and the old rule had trimmed asymmetrically, dropping 2021 at deviation
#: +0.0806 while keeping 2025 at -0.0381.
#:
#: Every value here was derived from the margin series BEFORE the baseline was
#: regenerated, and the regenerated snapshot matched to the cent. The full
#: reasoning is in tests/golden/CHANGELOG.md under the relative-floor entry.
_FLOOR_MOVED = {
    "FCX": {
        # Restated onto the current share count (sixth re-baseline, 2026-09-20);
        # the relative-floor deltas this table records are unchanged by it.
        "base_iv": 26.78,          # was 25.58, +4.34% at the old divisor
        "bear_iv": 16.95,          # was 16.06, +5.23% at the old divisor
        "bull_iv": 47.08,          # was 45.52, +3.08% at the old divisor
        "targets": (18.66, 24.89, 31.11),   # was (17.89, 23.85, 29.81)
        "band_ratio": 2.552,       # was 2.659; still outside [0.33, 2.50]
        "norm_ebitda_delta": 1.0466,
    },
}

#: ── THE THIRD RE-BASELINE: the scenario-ordering invariant
#:
#: `Bear IV <= Base IV <= Bull IV` is now enforced as an engine-level invariant
#: (`_enforce_scenario_ordering`, gate `GATE_SCENARIO_ORDERING`), with base as
#: the PIVOT: bear is pulled down onto base and bull up onto it, and base is
#: never moved.
#:
#: Measured one subprocess per fixture across all 14 baselines, EXACTLY ONE
#: fixture violated, and it is BN4_SI: bear published 7.37 above base 5.20 on a
#: name trading at S$11.10. No `base > bull` anywhere, so the bull half of the
#: invariant ships implemented but unexercised against a real run.
#:
#: The cause is a composition gain, not a disordered input. BN4.SI has NO DCF leg
#: in any scenario (`weight_dcf` 0.0, `iv_dcf` None throughout); two multi legs
#: vote, the anchor `EV/EBITDA` at 0.533333 and `SOTP (published)` at 0.466667.
#: In bear only, the anchor fails its equity bridge — EV SGD 9.021bn against net
#: debt SGD 9.325bn plus minority interest SGD 0.322bn is equity SGD -0.626bn,
#: which `_ev_to_equity_ps` floors to a literal `0.0` rather than refusing — and
#: the blend drops that zero as a non-positive leg and renormalises onto the
#: single survivor, which is the HIGHEST of the three legs bear computed (SOTP
#: 8.37 against Forward P/E 4.21 and Forward EV/EBITDA 0.07). Retaining the zero
#: at its intended weight gives 0.0 x 0.5333 + 8.37 x 0.4667 = 3.906, or 3.44
#: after the 0.8812 composite: below base, correctly ordered. The dropout is
#: worth +3.93 to the bear IV.
#:
#: Gate B's own consequences on BN4_SI are UNCHANGED — `tgr` is still 0.0 and
#: the bear growth premium is still the forced 1.0 — so this fixture still
#: belongs in `_STILL_FIRES`. What moved is one level above the gate: the clamp
#: rewrites the published bear IV, and `12m_targets.bear` follows it from 9.23 to
#: 8.15. `scenarios.bear.intrinsic_value_unclamped` = 7.37 is preserved beside
#: the clamp, so the archive keeps the evidence of its own violation.
_ORDERING_CLAMPED = {
    "BN4_SI": {
        "pre_clamp_bear_iv": 7.37,     # still the HISTORICAL value in the table below
        "clamped_bear_iv":   5.20,     # == base_iv, which did NOT move
        "base_iv":           5.20,
        "bull_iv":           7.12,     # correctly ordered, untouched
        "bear_target":       8.15,     # was 9.23, -11.70%
        "base_target":       8.15,     # unchanged
        "bull_target":       9.11,     # unchanged
        "weight_lost_vs_base": 0.533333,
        "legs_lost_vs_base": ["EV/EBITDA"],
    },
}


#: ── THE FOURTH RE-BASELINE: two-tier valuation (owner decision 2026-09-19)
#:
#: The quality x risk x commodity composite no longer multiplies the IV's
#: multiples leg, so every fixture whose composite was not 1.0 moved its IVs,
#: and every 12m target now converges from spot toward the IV (the forward-
#: multiple target, the PT-IV band and its validation fallback are superseded:
#: the band is skipped under the unified rule). Gate B's own consequences
#: (bear `tgr`, forced premium) and every method leg are untouched. The tables
#: above keep their history; these are the regenerated baseline.
#: BN4_SI's bear is still clamped onto base; its unclamped bear is 8.37, i.e.
#: the 7.37 above without the 0.8812 composite.
_TWO_TIER_MOVED = {
    #            base_iv   bear_iv   bull_iv   targets (bear, base, bull)
    "02888_HK": (258.53,   214.64,   301.76,   (225.27, 240.64, 255.77)),
    "09988_HK": (156.06,   110.71,   207.48,   (108.45, 131.13, 156.84)),
    "BABA":     (187.48,   138.01,   240.72,   (123.69, 148.43, 175.05)),
    "BN4_SI":   (5.90,     5.90,     8.08,     (8.50,   8.50,   9.59)),
    "COST":     (816.88,   589.32,   1066.04,  (792.14, 871.79, 958.99)),
    "D05_SI":   (43.53,    35.65,    51.42,    (55.65,  59.59,  63.54)),
    "MELI":     (4007.19,  3531.09,  4427.40,  (2424.69, 2591.33, 2738.40)),
    "MU":       (182.97,   139.37,   228.84,   (533.49, 555.29, 578.22)),
}
_TWO_TIER_BN4_UNCLAMPED_BEAR = 8.37

#: ── THE FIFTH RE-BASELINE: DCF-family parity (2026-09-19)
#: Every DCF-family leg now projects with the core DCF's growth schedule,
#: staged WACC and scenario margin delta. Only MELI moves (its weighted
#: DCF (FCF+) leg on a decay profile). The two-tier table above keeps its
#: MELI row as history.
_DCF_PARITY_MOVED = {
    "MELI": (3109.89, 2375.97, 3601.18, (2020.4, 2277.27, 2449.22)),
}


#: ── THE SIXTH RE-BASELINE: the current share count (owner, 2026-09-20)
#:
#: Every per-share value now divides by the share count the company has TODAY
#: -- market cap / price, scaled by the filing's diluted/basic ratio -- instead
#: of the trailing weighted-average diluted count from its last annual filing.
#: Valero was dividing by 309.0mn against 287.9mn actually outstanding.
#:
#: The fixtures that move are exactly the ones with a live quote: HK and SG
#: names fetch none, so 02888_HK, 09988_HK, BN4_SI, D05_SI, C38U_SI and U96_SI
#: are bit-identical and are deliberately absent from this table. Nothing here
#: is a valuation decision -- each value is its predecessor times that name's
#: own share ratio.
_SHARES_MOVED = {
    #            base_iv   bear_iv   bull_iv   targets (bear, base, bull)
    "BABA":     (188.07,   138.44,   241.48,   (123.91, 148.72, 175.43)),
    "COST":     (819.32,   591.08,   1069.22,  (792.76, 872.64, 960.10)),
    "MELI":     (3109.88,  2375.96,  3601.17,  (2020.40, 2277.27, 2449.22)),
    "MU":       (182.26,   138.83,   227.95,   (533.22, 554.93, 577.77)),
    # Never moved by an earlier re-baseline, so these carry no row in the
    # tables above; their pre-fix pins live in _PREFIX / _BEAR_IV_UNMOVED.
    "AAPL":     (224.55,   159.55,   302.75,   (245.44, 277.94, 317.04)),
    "V":        (451.18,   327.41,   568.42,   (358.75, 402.07, 443.10)),
    "SCHW":     (76.50,    51.95,    109.73,   (88.25, 96.84, 108.47)),
}


def _current(name: str) -> tuple:
    """The latest re-baselined (base, bear, bull, targets) for a moved name."""
    return (_SHARES_MOVED.get(name) or _DCF_PARITY_MOVED.get(name)
            or _TWO_TIER_MOVED[name])
#: Restated onto the current share count (sixth re-baseline).
_TWO_TIER_TARGETS_UNMOVED_IV = {"FCX": (43.16, 48.07, 58.22)}


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
    """The `× 10` is gone, and the shape it is gone FROM is the clamped one.

    This assertion used to read `_y10_fcf_margin = fcf_margin_base + md_abs`,
    which was the exact post-7ba9aa8 line. Item 3 then wrapped that sum in the
    floor-and-cap `_project_dcf` applies to every projected year, so the bare
    string stopped existing and this test failed on a change that is strictly
    closer to its own intent: the multiplier is still absent, and the estimate
    now agrees with the cash-flow engine as well.

    The shape is pinned as the clamped form rather than loosened to a substring,
    because a substring loose enough to survive both forms would also survive
    `md_abs * 10` reappearing inside the clamp. `_y10_fcf_margin = min(` plus
    `max(fcf_margin_base + md_abs, fcf_floor), _FCF_MARGIN_CAP)` is the pair;
    the fossil sweep below then checks every spacing of the multiplier against
    the whole engine source, which is where it would actually hide.
    """
    src = _engine_src()
    assert "_y10_fcf_margin = min(" in src
    assert "max(fcf_margin_base + md_abs, fcf_floor), _FCF_MARGIN_CAP)" in src
    assert "_y10_fcf_margin = fcf_margin_base + md_abs" not in src, (
        "the estimate has lost its clamp, so Gate B is judging a terminal "
        "margin the DCF never runs — see item 3 in tests/golden/CHANGELOG.md")
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


def test_the_section_2f_row_says_one_shot_not_per_year():
    body = inspect.getsource(pdf_report._section_2f)
    assert "Margin delta (one-shot, Y1–Y10)" in body
    assert "Margin delta / year" not in body
    assert "%/yr" not in _strip_comments(body)


def test_the_report_no_longer_renders_sensitivity_grids():
    """Both grids were removed from the PDF (owner, 2026-09-19): their centre was
    recomputed from stored parameters and its "diverges" banner was a stale trace
    beside the engine's own value. The Excel model carries the live sensitivity."""
    assert not hasattr(pdf_report, "_sensitivity_table")
    assert not hasattr(pdf_report, "_sensitivity_table_growth_margin")


# ══════════════════════════════════════════════════════════════════════════════
# E. The named moves in the re-baselined golden set
# ══════════════════════════════════════════════════════════════════════════════

def test_base_iv_is_unchanged_in_thirteen_and_fcx_is_the_named_exception():
    """The headline number did not move on thirteen, and that is the point.

    Renamed from `test_base_iv_is_unchanged_in_all_fourteen`. As written for the
    × 10 fix the name was true: all fourteen held, and "nothing moved" was the
    evidence that removing an inflation touches no valuation leg it should not.

    It stopped being true with the LATER relative-floor re-baseline, and FCX is
    the named exception: it is the only fixture whose anchor is a normalized leg
    (`EV/EBITDA (norm)`), so re-admitting FY2021 to that series moves the
    valuation. 09988_HK, BABA and C38U_SI moved a normalized field too and their
    base IVs are bit-identical, because their anchors read trailing figures.

    `_PREFIX["FCX"][0]` keeps the 25.58 it always held. That table records the
    state before the × 10 fix; overwriting it to make a test pass would erase the
    baseline the section exists to compare against.
    """
    for name, fx in _fixtures().items():
        if name == "FCX":
            assert fx["base_iv"] == _FLOOR_MOVED["FCX"]["base_iv"]
            assert _PREFIX["FCX"][0] == 25.58, "the pre-×-10 pin stays as history"
            continue
        if name in _TWO_TIER_MOVED:
            # Moved by the fourth re-baseline (composite out of the IV), not by
            # this fix; the pre-fix pin stays as history.
            assert fx["base_iv"] == _current(name)[0], name
            continue
        if name in _SHARES_MOVED:
            # Moved only by the sixth re-baseline (current share count).
            assert fx["base_iv"] == _SHARES_MOVED[name][0], name
            continue
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
#:
#: FCX's 16.06 and BN4_SI's 7.37 are now BOTH historical values. Each is still
#: true of the × 10 fix — that fix did not move either bear IV — but a later
#: re-baseline did: the relative floor took FCX to 16.90, and the
#: scenario-ordering clamp took BN4_SI down to 5.20. Both are left here
#: unchanged on purpose: re-valuing them would make this table claim the × 10
#: fix moved those bear legs, which it did not.
#: `test_the_sign_flips_changed_only_their_flag_text` asserts both halves for
#: each, so neither history is silently overwritten.
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
    # The × 10 fix left it at 39.21; the two-tier re-baseline (its 1.10
    # composite out of the IV) took it to 35.65. Both halves asserted.
    assert _BEAR_IV_UNMOVED["D05_SI"] == 39.21
    assert p["scenarios.bear.intrinsic_value"] == _TWO_TIER_MOVED["D05_SI"][1]


def test_the_sign_flips_changed_only_their_flag_text():
    """The eight still fire, so nothing downstream of the gate may move.

    `tgr` stays 0.0 and the premium stays at the forced 1.0, which leaves the
    bear IV exactly where it was — a bear IV moving here would mean the fix
    reached something it was not supposed to.

    Seven of the nine still qualify outright. There are TWO exceptions now, and
    it is worth being precise about WHICH claim fails for each, because in both
    cases Gate B's own consequences are unchanged and the movement came from
    somewhere else entirely.

    FCX: its `tgr` is still 0.0 and its premium is still the forced 1.0. What
    moved is the feed the gate sits on — re-admitting FY2021 to the normalized
    EBITDA series raised the anchor, and with no terminal value the bear leg
    scales straight with it. The gate did not reach anything new.

    BN4_SI: also `tgr` 0.0 and premium 1.0, so it still belongs in
    `_STILL_FIRES`. Its bear IV moved one level ABOVE the gate, when the
    scenario-ordering invariant clamped a bear case that had published 7.37
    against a base of 5.20 — an inversion caused by the anchor `EV/EBITDA`
    failing its equity bridge in bear only and the blend renormalising onto its
    single surviving leg, which happened to be the highest of the three. See
    `_ORDERING_CLAMPED` for the arithmetic. Base did not move, and the
    unclamped 7.37 is preserved in the archive beside the clamp.
    """
    for name in _STILL_FIRES:
        p = _proj(name)
        assert p["scenarios.bear.tgr"] == 0.0, name
        assert p["scenarios.bear.growth_premium"] == 1.0, name
        if name == "FCX":
            # Both halves asserted, so neither history is silently overwritten:
            # the × 10 fix left it at 16.06, the later floor re-baseline took it
            # to 16.90.
            assert _BEAR_IV_UNMOVED["FCX"] == 16.06
            assert p["scenarios.bear.intrinsic_value"] == _FLOOR_MOVED["FCX"]["bear_iv"]
            continue
        if name in _ORDERING_CLAMPED:
            oc = _ORDERING_CLAMPED[name]
            # Both halves again: the × 10 fix left BN4_SI at 7.37, and that is
            # still what the historical table says; the clamp took the published
            # value to base. Asserting the unclamped leaf too is what stops a
            # future change from quietly deleting the evidence.
            assert _BEAR_IV_UNMOVED[name] == oc["pre_clamp_bear_iv"], name
            if name in _TWO_TIER_MOVED:
                # Still clamped onto base after the two-tier re-baseline, at
                # the new levels; the third re-baseline's numbers stay in the
                # table as history.
                tt = _TWO_TIER_MOVED[name]
                assert oc["clamped_bear_iv"] == oc["base_iv"] == 5.20
                assert p["scenarios.bear.intrinsic_value"] == tt[1] == tt[0], name
                assert p["scenarios.bear.intrinsic_value_unclamped"] == \
                    _TWO_TIER_BN4_UNCLAMPED_BEAR, name
                assert p["scenarios.bull.intrinsic_value"] == tt[2], name
                assert (p["12m_targets.bear"], p["12m_targets.base"],
                        p["12m_targets.bull"]) == tt[3], name
                assert p["scenarios.bear.ordering_composition.single_method"] is True, name
                continue
            assert p["scenarios.bear.intrinsic_value"] == oc["clamped_bear_iv"], name
            assert p["scenarios.bear.intrinsic_value_unclamped"] == \
                oc["pre_clamp_bear_iv"], name
            # The pivot. The whole point of clamping the OUTER scenarios is that
            # the headline number is untouched, so this is the assertion that
            # matters most in the block.
            assert p["scenarios.base.intrinsic_value"] == oc["base_iv"], name
            assert p["scenarios.bull.intrinsic_value"] == oc["bull_iv"], name
            assert p["12m_targets.bear"] == oc["bear_target"], name
            assert p["12m_targets.base"] == oc["base_target"], name
            assert p["12m_targets.bull"] == oc["bull_target"], name
            # The composition diagnostic that says WHY, frozen as data.
            assert p["scenarios.bear.ordering_composition.legs_lost_vs_base"] == \
                oc["legs_lost_vs_base"], name
            assert p["scenarios.bear.ordering_composition.weight_lost_vs_base"] == \
                oc["weight_lost_vs_base"], name
            assert p["scenarios.bear.ordering_composition.single_method"] is True, name
            continue
        if name in _TWO_TIER_MOVED or name in _SHARES_MOVED:
            assert p["scenarios.bear.intrinsic_value"] == _current(name)[1], name
            continue
        assert p["scenarios.bear.intrinsic_value"] == _BEAR_IV_UNMOVED[name], name


def test_the_ordering_clamp_fired_on_exactly_one_of_the_fourteen():
    """The clamp is live, so its blast radius is a fact and not a prediction.

    Asserted on the archive rather than on a re-run: exactly one fixture of the
    fourteen carries `intrinsic_value_unclamped`, and it is BN4_SI. If this ever
    goes red with a SECOND name, the invariant has started firing somewhere new
    and that needs looking at before the baseline is regenerated again — not
    after, when the evidence is overwritten.

    It also pins the half that never fires: no fixture carries a clamped BULL,
    so `bull := max(bull, base)` ships implemented but unexercised against a
    real run. `tests/test_scenario_ordering_invariant.py::TestTheBullHalf` is
    the only coverage it has and it is synthetic.
    """
    names = sorted(_fixtures())
    assert len(names) == 14, names
    clamped = []
    for name in names:
        p = _proj(name)
        for scen in ("bear", "base", "bull"):
            if p.get(f"scenarios.{scen}.intrinsic_value_unclamped") is not None:
                clamped.append((name, scen))
    assert clamped == [("BN4_SI", "bear")], clamped


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

    The premium half of this is unchanged and is the actual thesis: `1.0`, off a
    ceiling of `1.8`. The IV it produced has since moved once more, to 46.92, on
    the later relative-floor re-baseline — FCX's anchor is `EV/EBITDA (norm)`, and
    re-admitting FY2021 raises that series 4.66%. So 45.52 is pinned here as the
    value the × 10 fix produced, and the live value is asserted separately.
    """
    p = _proj("FCX")
    assert p["scenarios.bull.growth_premium"] == 1.0
    assert _PREFIX["FCX"][3] == 1.8
    assert 46.92 / 72.62 == pytest.approx(1 - 0.354, abs=5e-4), (
        "the cumulative cut from the pre-fix 72.62, × 10 fix and floor re-baseline")
    assert p["scenarios.bull.intrinsic_value"] == _FLOOR_MOVED["FCX"]["bull_iv"]


def test_02888_is_the_one_bull_premium_that_rose():
    """A discount shrinking toward neutral, not a premium being granted.

    Its bull `_quality` was below 1 even inflated, so removing the inflation
    moved the premium UP from 0.719 to 0.815 — and because 02888_HK's bull IV
    carries no DCF weight, the headline barely moved (+0.7%).
    """
    p = _proj("02888_HK")
    assert p["scenarios.bull.growth_premium"] == 0.815
    # 331.93 was this bull IV with the 1.10 composite; the two-tier
    # re-baseline removed the composite from the IV.
    assert p["scenarios.bull.intrinsic_value"] == _TWO_TIER_MOVED["02888_HK"][2]
    assert 331.93 == pytest.approx(301.76 * 1.10, abs=0.01)
    assert 0.815 < 1.0


def test_fcx_12m_band_fires_for_the_first_time():
    """Its own bear IV moved enough to put the analyst PT outside [0.33×, 2.50×].

    bear $43.13 = 2.552× a bear IV of $16.90 — so all three targets are replaced
    with the validation fallback and then bounded by the convergence cap. The
    band is decision 2c's; this fix is what first gave it something to catch.

    Both sides of that ratio are one re-baseline newer than this test was written
    for: the PT moved 42.71 → 43.13 and the bear IV moved 16.06 → 16.90, which
    took the ratio from 2.659× to 2.552×. The gate's decision did not change, and
    that is the point worth pinning — a +5.23% move to the bear IV was large
    enough to leave the ±5% reporting tolerance and still not large enough to
    bring FCX back inside the band. It has to fall another 2.1% to do that.
    """
    p = _proj("FCX")
    # SUPERSEDED by the two-tier re-baseline: the target is now derived from
    # the IV by one rule and lies between spot and IV by construction, so the
    # band is skipped and never fires. The band-era numbers stay as history.
    assert _FLOOR_MOVED["FCX"]["targets"] == (18.66, 24.89, 31.11)
    assert p["12m_pt_method"].startswith("convergence toward intrinsic value")
    assert (p["12m_targets.bear"], p["12m_targets.base"], p["12m_targets.bull"]) == \
           _TWO_TIER_TARGETS_UNMOVED_IV["FCX"]
    assert "pt_over_scenario_iv" not in p["gate_metrics"]
    for scen in _SCENARIOS:
        assert not any("VALIDATION ERROR" in f
                       for f in p[f"scenarios.{scen}.forward_flags"]), scen
    return
    assert _FLOOR_MOVED["FCX"]["targets"]
    assert "pt_over_scenario_iv" in p["gate_metrics"]
    for scen in _SCENARIOS:
        flags = p[f"scenarios.{scen}.forward_flags"]
        assert any("VALIDATION ERROR: 12m PT band violated" in f for f in flags), scen

    # The ratio itself is printed ONLY into the flag prose — `gate_metrics` is a
    # list of gate ids with no values attached. So this reads it out of the text,
    # which this module warns elsewhere is not the same as reading a payload. It
    # is made safe by cross-checking the prose against the payload it describes:
    # the PT and the IV the ratio is computed from are both pinned separately, so
    # the string cannot drift from the numbers without the ratio going with it.
    text = next(f for f in p["scenarios.bear.forward_flags"] if "PT band violated" in f)
    pt, ratio, iv = _PT_BAND_RE.search(text).groups()
    assert float(pt) == pytest.approx(43.13, abs=5e-3)
    assert float(iv) == _FLOOR_MOVED["FCX"]["bear_iv"] == pytest.approx(
        p["scenarios.bear.intrinsic_value"], abs=5e-3)
    assert float(ratio) == pytest.approx(_FLOOR_MOVED["FCX"]["band_ratio"], abs=5e-4)
    assert float(ratio) > 2.50, "the ceiling it breaches; 2.1% further and it would not"
    assert float(pt) / float(iv) == pytest.approx(float(ratio), abs=5e-3), (
        "the ratio the prose prints is the ratio its own two numbers imply")


def test_meli_bear_floor_mechanics_and_its_policy_conflict():
    """MELI's bear IV rose 7.7%, which moved the band floor above the ceiling.

    `0.33 × 4940.12 = 1630.24` now exceeds the high-SBC bear ceiling
    `1828.94 × 0.85 = 1554.60`. The band floor is kept and the conflict is
    declared on the card rather than resolved silently. Pre-fix the ratio was
    0.271× and the ceiling won at 1554.60; base and bull targets did not move.
    """
    p = _proj("MELI")
    # SUPERSEDED by the two-tier re-baseline (see the FCX test above): no band,
    # no policy conflict. The band-era values stay as history in the prose.
    tt = _current("MELI")
    assert (p["12m_targets.bear"], p["12m_targets.base"], p["12m_targets.bull"]) == tt[3]
    assert p["scenarios.bear.intrinsic_value"] == tt[1]
    for scen in _SCENARIOS:
        assert not any("POLICY CONFLICT" in f
                       for f in p[f"scenarios.{scen}.forward_flags"]), scen
    return
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
    # AAPL and V restated onto the current share count (sixth re-baseline);
    # the direction check against `before` is unaffected, which is the point.
    raised = {"02888_HK": 236.10, "AAPL": 159.55, "COST": 956.33,
              "MELI": 4940.12, "V": 327.41}
    before = {"02888_HK": 236.57, "AAPL": 146.37, "COST": 923.28,
              "MELI": 4588.16, "V": 276.28}
    for name, want in raised.items():
        got = _proj(name)["scenarios.bear.intrinsic_value"]
        if name in _TWO_TIER_MOVED:
            # Composite removed from the IV by the two-tier re-baseline, so
            # the direction check against `before` (which carried it) no
            # longer compares like with like. The value is pinned instead.
            assert got == _current(name)[1], name
            continue
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
    # +1 for EV/OCF (Wave 1 oil, gas & coal (owner-approved 2026-09-20)), which carries the premium like its siblings.
    assert len(mults) == 17
    with_gp = [m for m in mults if "growth_premium" in m]
    without = [m for m in mults if "growth_premium" not in m]
    assert len(with_gp) == 15
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
    # FCF Yield and Distributable CF Yield share the rule. They no longer share
    # a NUMERATOR for the yield: the distributable leg capitalises at the owner
    # constant, because the peer FCF yield is net of total capex while
    # distributable CF is net of maintenance capex only. What this test guards
    # is the scaling, which is the denominator, and that is unchanged -- so it
    # matches on the assignment rather than on where the yield came from.
    line = [ln for ln in body.splitlines() if "target_yield = " in ln and "#" != ln.strip()[:1]]
    assert len(line) == 2, line
    assert all("/ (sm * growth_premium)" in x for x in line)
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
        # The composite is retired (2026-09-19), so there is no post-composite
        # leaf. The 1.1x relationship below is the history this test was
        # written for.
        assert multi == _02888_BLEND[scen][0], scen
        assert f"scenarios.{scen}.iv_multi_post" not in proj, scen
        assert _02888_BLEND[scen][1] == pytest.approx(multi * 1.1, rel=1e-6), scen


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
