"""Step 7 — the Consumer monotonic ladder, its blast radius, and its blind spot.

This module is the ONLY automated coverage the classifier migration has. That is
not a rhetorical point and it is measured, not assumed:

    of the 14 golden fixtures, `get_valuation_profile` is called ZERO times.

Two independent mechanisms produce that zero, and `TestTheGoldenBlindSpot` pins
both. Five fixtures (`AAPL`, `C38U_SI`, `FCX`, `SCHW`, `V`) carry
`state_source == "lookup_tables"` and resolve their profile from
`TICKER_SECTOR_LOOKUP` or the SGX fallback without ever consulting the ladder.
The other nine carry an archived `profile_names` entry, which `run_dcf_agent`
reads BEFORE it would classify — and the archived entry is itself a write-back of
whatever the ladder returned at capture time (`dcf_agent.py`, "v3.21 Fix D"). So
the archive freezes the ladder's ANSWER and hands it back as an INPUT.

The consequence is the reason this file exists. A classifier change moves
production money on every unpinned Consumer name — `02331.HK` (Li Ning) and
`01368.HK` (Xtep) both carry an EMPTY `TICKER_SECTOR_LOOKUP` override and so are
decided by this ladder and nothing else — and it moves ZERO golden numbers. The
golden suite cannot arbitrate this change in either direction: it will not catch
a regression, and it will not confirm a fix. Every claim below is therefore
discharged against the real function on a real grid, against the real fixture
archives, and against the real profile table.

What the migration did, in one line: HEAD's Consumer branch was a BAND-PASS
whose first rung had an upper bound on FCF margin, so a margin improvement past
0.18 fell out of the band and could land on a WEAKER profile. The ladder is now
ordered strongest-methodology-first with the upper bounds retained only where
the fall-through is better. `TestTheMigrationChangesExactlyWhatItSays` diffs the
two ladders over 3000 cells and asserts the complete move set.
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from src.data.sector_profiles import (
    INDUSTRY_VALUATION_PROFILES,
    TICKER_SECTOR_LOOKUP,
    classify_valuation_profile,
    get_valuation_profile,
    get_wacc_profile_for_ticker,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "golden"

#: The Consumer ladder at HEAD (7530577), transcribed verbatim from
#: `git show HEAD:src/data/sector_profiles.py`. It is the baseline the migration
#: is diffed against, so a transcription error would silently redefine what
#: "unchanged" means. `test_the_head_transcription_is_validated` discharges that
#: risk by reproducing the 20 cells HEAD's own test module asserted on, taken
#: from `tests/test_consumer_discretionary_gates.py` before it was rewritten.
def _head_ladder(revenue_cagr, fcf_margin, debt_to_equity,
                 is_pre_revenue=False, revenue_base=None, gross_margin=None):
    if 0.0 <= revenue_cagr < 0.40 and 0.05 <= fcf_margin < 0.18:
        return "Apparel / Athletic Wear"
    if revenue_cagr < 0.03 and fcf_margin >= 0.15:
        return "Food & Beverage"
    if revenue_cagr < 0.05:
        return "Household / Personal"
    if revenue_cagr >= 0.15 and fcf_margin >= 0.15:
        return "Consumer Growth"
    if fcf_margin >= 0.15:
        return "Luxury Goods"
    if (revenue_base and revenue_base > 50e9
            and 0.01 <= fcf_margin < 0.05
            and 0.04 <= revenue_cagr <= 0.15
            and debt_to_equity < 1.0):
        return "Membership / Subscription Retail"
    if fcf_margin < -0.05 and debt_to_equity > 1.0:
        return "Automotive & EV"
    return "Traditional Retail"


#: Quality tiers, owner-derived rather than invented here.
#:
#: The owner's own four-rung ladder orders Consumer Growth above Luxury Goods
#: above Apparel / Athletic Wear above Household / Personal, and that order is
#: adopted verbatim. Traditional Retail is placed at the floor on a measured
#: property rather than a judgement: it is the ONLY Consumer profile with no DCF
#: leg of any kind (EV/EBITDAR .40 anchor, P/E .25, EV/Revenue .15,
#: ROIC vs WACC .10, FCF Yield .10), so it cannot see a business that is
#: mispriced against its own cash generation. `test_traditional_retail_is_the_
#: only_consumer_profile_with_no_dcf_leg` pins that.
#:
#: The gap between 2 and 4 is deliberate: nothing in the sector currently sits
#: at 3, and leaving the number free means adding a rung does not require
#: renumbering every assertion below it.
QUALITY_TIER = {
    "Traditional Retail": 1,
    "Household / Personal": 2,
    "Apparel / Athletic Wear": 4,
    "Luxury Goods": 5,
    "Consumer Growth": 6,
}

#: Structural profiles: a claim about BUSINESS MODEL, not about method quality.
#: They are excluded from the tier order rather than placed inside it, because
#: there is no defensible answer to "is a warehouse club a better methodology
#: than a sportswear brand". Excluding them is what makes the monotonicity
#: property stateable at all; `TestTheKnownResiduals` pins every transition into
#: or out of this set so the exclusion cannot hide a real demotion.
STRUCTURAL = {
    "Food & Beverage",                   # FMCG staples
    "Membership / Subscription Retail",  # recurring membership-fee economics
    "Automotive & EV",                   # distress / EV-ramp capex
}

#: The grid. 5 gross margins x 25 CAGRs x 24 FCF margins = 3000 cells, dense
#: enough that every rung boundary in the ladder is straddled by at least one
#: adjacent pair: 0.029/0.03, 0.049/0.05, 0.149/0.15, 0.179/0.18, 0.399/0.40,
#: 0.009/0.01, 0.04/0.049, -0.06/-0.05 and 0.649/0.65.
CAGRS = [-0.10, -0.05, -0.01, 0.0, 0.01, 0.02, 0.029, 0.03, 0.04, 0.049,
         0.05, 0.06, 0.0887, 0.10, 0.14, 0.149, 0.15, 0.16, 0.20, 0.30,
         0.39, 0.399, 0.40, 0.50, 0.80]
MARGINS = [-0.20, -0.10, -0.06, -0.05, -0.01, 0.0, 0.009, 0.01, 0.03, 0.04,
           0.049, 0.05, 0.08, 0.10, 0.14, 0.149, 0.15, 0.16, 0.179, 0.18,
           0.19, 0.25, 0.40, 0.60]
GROSS_MARGINS = [None, 0.30, 0.649, 0.65, 0.80]

REV_BASE = 275e9     # COST's measured revenue, the one Consumer fixture
D_TO_E = 0.35        # COST's measured leverage


def _new(cagr, fcf, de=D_TO_E, rev=REV_BASE, gm=None):
    return classify_valuation_profile("Consumer", cagr, fcf, de, False,
                                      revenue_base=rev, gross_margin=gm)


def _grid():
    for gm in GROSS_MARGINS:
        for cagr in CAGRS:
            for fcf in MARGINS:
                yield gm, cagr, fcf


# ══════════════════════════════════════════════════════════════════════════════
# The transcription, validated before it is trusted
# ══════════════════════════════════════════════════════════════════════════════


def test_the_head_transcription_is_validated():
    """`_head_ladder` reproduces the 20 cells HEAD's own tests asserted on.

    Every one of these was an assertion in
    `tests/test_consumer_discretionary_gates.py` at 7530577, written against the
    live function. If the transcription drifts, this fails and the blast-radius
    diff below becomes meaningless rather than quietly wrong.

    The three cells marked "cliff" are the defects the migration exists to
    remove; they are asserted HERE, against HEAD, so that the record of what was
    broken survives the fix instead of being deleted along with the test that
    witnessed it.
    """
    cases = [
        # NKE's band-pass, at both edges.
        ((0.03, 0.02, 0.6), "Household / Personal"),
        ((0.03, 0.049, 0.6), "Household / Personal"),
        ((0.03, 0.05, 0.6), "Apparel / Athletic Wear"),
        ((0.03, 0.10, 0.6), "Apparel / Athletic Wear"),
        ((0.03, 0.179, 0.6), "Apparel / Athletic Wear"),
        ((0.03, 0.18, 0.6), "Household / Personal"),          # cliff (i)
        ((0.03, 0.25, 0.6), "Household / Personal"),          # cliff (i)
        ((0.049, 0.30, 0.6), "Household / Personal"),
        ((0.05, 0.30, 0.6), "Luxury Goods"),
        # Anta across the 15% FCF rung, ONON across the 40% CAGR rung.
        ((0.12, 0.18, 0.4), "Luxury Goods"),
        ((0.12, 0.12, 0.4), "Apparel / Athletic Wear"),
        ((0.30, 0.12, 0.3), "Apparel / Athletic Wear"),
        ((0.50, 0.12, 0.3), "Traditional Retail"),            # cliff (ii)
        # Membership's revenue gate, and cliff (iii) on the CAGR axis.
        ((0.10, 0.02, 0.3, False, 275e9), "Membership / Subscription Retail"),
        ((0.10, 0.02, 0.3, False, None), "Traditional Retail"),
        ((0.15, 0.02, 0.3, False, 275e9), "Membership / Subscription Retail"),
        ((0.16, 0.02, 0.3, False, 275e9), "Traditional Retail"),   # cliff (iii)
        # EL's three cycle positions, and the Apparel-above-F&B constraint.
        ((-0.02, 0.10, 0.5), "Household / Personal"),
        ((0.06, 0.16, 0.5), "Apparel / Athletic Wear"),
        ((0.08, 0.18, 0.5), "Luxury Goods"),
        ((0.02, 0.16, 0.6), "Apparel / Athletic Wear"),
        ((0.02, 0.20, 0.6), "Food & Beverage"),
    ]
    for args, want in cases:
        assert _head_ladder(*args) == want, (args, want)
    assert len(cases) == 22


def test_traditional_retail_is_the_only_consumer_profile_with_no_dcf_leg():
    """The measured property that puts Traditional Retail at the bottom of the
    tier order, so the floor is a fact about the table and not a preference.

    Read off the real profile table. Every other Consumer profile carries a DCF
    leg; Traditional Retail carries five relative-multiple and spread legs and no
    cash-flow leg at all, so a name routed there is valued entirely by what its
    peers cost. That is the weakest available methodology in the sector, which is
    why cliff (ii) — a 50% grower landing there — was worth fixing, and why the
    fix is a promotion from tier 1 to tier 4 rather than a relabel.
    """
    dcf_names = {n for n in INDUSTRY_VALUATION_PROFILES["Consumer"]
                 if any("dcf" in (m.get("name") or "").lower()
                        for m in INDUSTRY_VALUATION_PROFILES["Consumer"][n].get("methods", [])
                        if isinstance(m, dict))}
    without = sorted(set(INDUSTRY_VALUATION_PROFILES["Consumer"]) - dcf_names)
    assert without == ["Traditional Retail"], without
    tr = {m["name"]: m["weight"]
          for m in INDUSTRY_VALUATION_PROFILES["Consumer"]["Traditional Retail"]["methods"]}
    assert tr == {"EV/EBITDAR": 0.4, "P/E": 0.25, "EV/Revenue": 0.15,
                  "ROIC vs WACC": 0.1, "FCF Yield": 0.1}, tr


# ══════════════════════════════════════════════════════════════════════════════
# The blast radius, stated completely
# ══════════════════════════════════════════════════════════════════════════════


class TestTheMigrationChangesExactlyWhatItSays:
    """The complete set of cells the migration moves, asserted as a set.

    A refactor of a first-match ladder is dangerous precisely because it is easy
    to move a cell you did not mean to. This class enumerates every move over the
    3000-cell grid and asserts the enumeration is exhaustive, so "I checked the
    cases I cared about" is replaced by "these are all of them".
    """

    #: (gross_margin, HEAD profile, new profile) -> the number of grid cells.
    #: Measured by `scratchpad/probe_ladder_diff.py` -> `scratchpad/ladder_diff.log`.
    MOVE_SET = {
        # ── gross_margin=None: the migration WITHOUT the new rung ─────────
        # Cliff (i). `cagr in [0.03, 0.05)` at `fcf >= 0.18` used to hit the
        # `cagr < 0.05` Household rung, which stood ABOVE the `fcf >= 0.15`
        # Luxury rung, so the strongest cash converter in that band got the
        # second-weakest profile in the sector. Household moved below Luxury.
        (None, "Household / Personal", "Luxury Goods"): 15,
        # Cliff (ii), the documented ONON case. `cagr >= 0.40` at
        # `fcf in [0.05, 0.15)` was excluded from the Apparel band by its CAGR
        # bound and fell all the way through to Traditional Retail. The rung-9
        # catch now gives it the Apparel band it would have got at 39%.
        (None, "Traditional Retail", "Apparel / Athletic Wear"): 15,
        # The rung-1 promotion. At `cagr in [0.15, 0.40)` and `fcf in [0.15,
        # 0.18)` the Apparel band used to win by standing first, even though the
        # name satisfied Consumer Growth's own condition. An ordering artifact,
        # not a judgement; Consumer Growth now stands at the top.
        (None, "Apparel / Athletic Wear", "Consumer Growth"): 18,
        # ── gross_margin >= 0.65: the new rung, at 0.65 and at 0.80 ───────
        # Identical move sets at both, which is the point: the rung is a
        # threshold and not a slope, so nothing above 0.65 behaves differently
        # from anything else above it.
        (0.65, "Apparel / Athletic Wear", "Luxury Goods"): 134,
        (0.65, "Apparel / Athletic Wear", "Consumer Growth"): 18,
        (0.65, "Household / Personal", "Luxury Goods"): 140,
        (0.65, "Traditional Retail", "Luxury Goods"): 152,
        (0.65, "Food & Beverage", "Luxury Goods"): 44,
        (0.65, "Membership / Subscription Retail", "Luxury Goods"): 28,
        (0.80, "Apparel / Athletic Wear", "Luxury Goods"): 134,
        (0.80, "Apparel / Athletic Wear", "Consumer Growth"): 18,
        (0.80, "Household / Personal", "Luxury Goods"): 140,
        (0.80, "Traditional Retail", "Luxury Goods"): 152,
        (0.80, "Food & Beverage", "Luxury Goods"): 44,
        (0.80, "Membership / Subscription Retail", "Luxury Goods"): 28,
        # ── gross_margin below the rung: the migration is inert ──────────
        # The SAME three fix cells, at 0.30 and at 0.649, and nothing else. The
        # new rung does not fire below 0.65, so a sub-threshold gross margin
        # reproduces HEAD plus the three documented fixes exactly — which is what
        # makes the plumbing safe to ship ahead of any caller measuring a margin.
        # The 0.649 row is the one that matters: it is one thousandth below the
        # threshold, so if the rung's comparison were `>` instead of `>=` or the
        # threshold were off by a hair, this row would change shape.
        (0.30, "Household / Personal", "Luxury Goods"): 15,
        (0.30, "Traditional Retail", "Apparel / Athletic Wear"): 15,
        (0.30, "Apparel / Athletic Wear", "Consumer Growth"): 18,
        (0.649, "Household / Personal", "Luxury Goods"): 15,
        (0.649, "Traditional Retail", "Apparel / Athletic Wear"): 15,
        (0.649, "Apparel / Athletic Wear", "Consumer Growth"): 18,
        # Note what is NOT here: at 0.65 and 0.80 the three fix classes appear
        # only as `Apparel -> Consumer Growth` (18), because the rung-1 promotion
        # is tested before the gross-margin rung and so survives it. The other two
        # fix classes are subsumed into `Household -> Luxury` (140) and
        # `Traditional Retail -> Luxury` (152), since at a qualifying margin both
        # HEAD destinations now resolve to Luxury regardless.
    }

    def test_the_move_set_is_exhaustive(self):
        seen: dict[tuple, int] = {}
        for gm, cagr, fcf in _grid():
            a = _head_ladder(cagr, fcf, D_TO_E, False, REV_BASE)
            b = _new(cagr, fcf, gm=gm)
            if a != b:
                seen[(gm, a, b)] = seen.get((gm, a, b), 0) + 1
        assert seen == self.MOVE_SET, (
            f"unexpected moves {sorted(set(seen) - set(self.MOVE_SET))}; "
            f"missing moves {sorted(set(self.MOVE_SET) - set(seen))}")
        assert sum(seen.values()) == 1176

    def test_a_sub_threshold_gross_margin_reproduces_head_except_for_the_three_fixes(self):
        """The fail-closed half of the new rung, measured rather than claimed.

        At 0.30 and at 0.649 — either side of nothing, and below the 0.65
        threshold — the ladder differs from HEAD on exactly the 48 cells that are
        cliffs (i) and (ii) and the rung-1 promotion. The gross-margin plumbing
        adds no behaviour of its own until the margin clears 0.65, so a wrong or
        stale gross margin cannot silently re-route a name; it can only fail to
        promote one.
        """
        for gm in (0.30, 0.649):
            moved = sorted({(_head_ladder(c, f, D_TO_E, False, REV_BASE),
                             _new(c, f, gm=gm))
                            for _g, c, f in _grid() if _g == gm
                            and _head_ladder(c, f, D_TO_E, False, REV_BASE)
                            != _new(c, f, gm=gm)})
            assert moved == sorted({(k[1], k[2]) for k in self.MOVE_SET
                                    if k[0] is None}), (gm, moved)

    def test_the_unmeasured_margin_is_indistinguishable_from_a_low_one(self):
        """`gross_margin=None` and `gross_margin=0.30` agree on every cell.

        This is what "fails closed" means concretely: an absent gross margin
        behaves exactly like a margin that does not qualify, and never like one
        that does. The alternative — treating None as a high margin — would
        promote every name whose filing lacks `gross_profit` AND
        `cost_of_revenue` onto Luxury Goods, which is the silent failure mode the
        convention exists to prevent.
        """
        for _gm, cagr, fcf in _grid():
            assert _new(cagr, fcf, gm=None) == _new(cagr, fcf, gm=0.30), (cagr, fcf)

    def test_no_move_is_a_demotion_between_two_quality_profiles(self):
        """Every one of the 1176 moves either promotes or leaves the tier order.

        Checked off `MOVE_SET` rather than off the grid, so the assertion is about
        the enumerated blast radius and cannot pass by never running. The four
        structural moves are excluded here and pinned individually in
        `TestTheKnownResiduals`, where each one is named with its reason.
        """
        for (gm, a, b), n in self.MOVE_SET.items():
            if a in STRUCTURAL or b in STRUCTURAL:
                continue
            assert QUALITY_TIER[b] > QUALITY_TIER[a], (gm, a, b, n)


# ══════════════════════════════════════════════════════════════════════════════
# The invariant the owner asked for
# ══════════════════════════════════════════════════════════════════════════════


class TestMarginAxisMonotonicity:
    """"A higher margin must never degrade the valuation methodology to a
    lower-tier profile."

    Enforced on BOTH margin axes, over the whole grid, at every CAGR and every
    leverage and revenue setting the grid carries. Transitions into or out of a
    structural profile are outside the property by construction and are pinned
    separately; see `STRUCTURAL` for why.
    """

    def test_a_rising_fcf_margin_never_lowers_the_quality_tier(self):
        bad = []
        for gm in GROSS_MARGINS:
            for cagr in CAGRS:
                prev = None
                for fcf in MARGINS:
                    p = _new(cagr, fcf, gm=gm)
                    if p not in QUALITY_TIER:
                        continue
                    if prev is not None and QUALITY_TIER[p] < QUALITY_TIER[prev[1]]:
                        bad.append((gm, cagr, prev, (fcf, p)))
                    prev = (fcf, p)
        assert not bad, bad

    def test_a_rising_gross_margin_never_lowers_the_quality_tier(self):
        """Sweeping the gross margin from unmeasured to 0.80 at every fixed
        (cagr, fcf) cell.

        This is the property that makes the new rung safe to place at the top of
        the margin rungs: everything it pre-empts is below Luxury Goods in the
        tier order or structural, so firing it can only promote. Consumer Growth
        is the single quality profile above it, and Consumer Growth stands ABOVE
        it in the ladder, which is why a 20%-grower with a 70% gross margin still
        resolves to Consumer Growth rather than to Luxury.
        """
        bad = []
        ordered = [None, 0.30, 0.649, 0.65, 0.80]
        for cagr in CAGRS:
            for fcf in MARGINS:
                prev = None
                for gm in ordered:
                    p = _new(cagr, fcf, gm=gm)
                    if p not in QUALITY_TIER:
                        continue
                    if prev is not None and QUALITY_TIER[p] < QUALITY_TIER[prev[1]]:
                        bad.append((cagr, fcf, prev, (gm, p)))
                    prev = (gm, p)
        assert not bad, bad

    def test_consumer_growth_outranks_the_gross_margin_rung(self):
        """The one precedence that had to be chosen, and it is the owner's.

        The owner's ladder tests `cagr >= 0.15` BEFORE `gross_margin >= 0.65`, so
        a fast grower keeps Consumer Growth even at a 90% gross margin. Preserved
        here. The alternative — margin first — would move every high-margin
        hyper-grower onto a profile anchored 50% on a trailing premium P/E, which
        is the trough-margin exposure the Failure-2 repair exists to remove.
        """
        assert _new(0.20, 0.20, gm=0.90) == "Consumer Growth"
        assert _new(0.50, 0.30, gm=0.90) == "Consumer Growth"
        # And the same name at a sub-threshold margin is unchanged, so the
        # precedence is not doing work the margin could have done anyway.
        assert _new(0.20, 0.20, gm=0.30) == "Consumer Growth"

    def test_the_ladder_still_reaches_every_profile_it_could_at_head(self):
        """No profile was orphaned by the reordering.

        Reordering a first-match ladder can make a rung unreachable without
        removing it, which reads as a no-op in review and deletes a profile in
        production. All eight of HEAD's Consumer outcomes are still reachable,
        and the reachability witness for each is a single cell.
        """
        witnesses = {
            "Consumer Growth":              (0.20, 0.20),
            "Luxury Goods":                 (0.08, 0.20),
            "Apparel / Athletic Wear":      (0.03, 0.10),
            "Food & Beverage":              (0.02, 0.20),
            "Household / Personal":         (0.02, 0.02),
            "Membership / Subscription Retail": (0.10, 0.02),
            # CAGR must clear 0.05, or the `cagr < 0.05` rung takes the cell
            # first and Automotive & EV is unreachable — at HEAD as well as now.
            "Automotive & EV":              (0.10, -0.10),
            "Traditional Retail":           (0.20, -0.10),
        }
        assert set(witnesses) == set(INDUSTRY_VALUATION_PROFILES["Consumer"]) - {
            # Routed by TICKER_SECTOR_LOOKUP override only; the ladder has never
            # been able to return these and HEAD's branch could not either.
            "Agribusiness & Food (SG)", "Consumer Durables",
            "Packaged Consumer & Lifestyle (SG)", "Travel & Dining",
            "Online Gaming / Sports Betting",
        }, sorted(set(INDUSTRY_VALUATION_PROFILES["Consumer"]) - set(witnesses))
        for name, (cagr, fcf) in witnesses.items():
            de = 2.0 if name == "Automotive & EV" else D_TO_E
            got = classify_valuation_profile("Consumer", cagr, fcf, de, False,
                                             revenue_base=REV_BASE)
            assert got == name, (name, cagr, fcf, got)
            assert _head_ladder(cagr, fcf, de, False, REV_BASE) == name, (
                f"{name} was not reachable at HEAD either, so this is not a "
                f"property the migration preserved")


# ══════════════════════════════════════════════════════════════════════════════
# What is still broken, on purpose
# ══════════════════════════════════════════════════════════════════════════════


class TestTheKnownResiduals:
    """The three things this migration does NOT fix, pinned as measured facts.

    Each is recorded here rather than left in a comment because a comment can be
    read as an oversight and a failing-then-xfail-free assertion cannot: these
    pass today, and the day one of them stops passing is the day somebody made a
    decision about it, which is what should happen.
    """

    def test_cliff_three_is_a_cagr_axis_step_and_is_not_fixed(self):
        """`cagr > 0.15` at `fcf in [0.01, 0.05)`: Membership -> Traditional
        Retail. Still live, unchanged from HEAD.

        Not fixed, and the reason is a conflict between two owner-authored
        mechanisms rather than an oversight. The only rung that would catch a
        >15% grower is Consumer Growth, whose `fcf >= 0.15` conjunct is
        load-bearing: it is the gate that stops a hyper-growth name being
        rewarded for growth it does not convert to cash — the exact defect the
        growth-reinvestment charge was built to price, and the one the owner's
        unconditioned `if cagr >= 0.15: return "Consumer Growth"` would reopen.
        Widening the Membership rung's own CAGR bound instead would classify a
        20% grower as a warehouse club, which is a structural claim about
        recurring membership-fee economics and not a claim about growth.

        It is also outside the invariant as stated. The invariant is on MARGIN:
        "a higher margin must never degrade the valuation methodology". This is a
        step on the growth axis at an unchanged margin, and the ladder's margin
        axes are monotone at every CAGR (pinned above).
        """
        assert _new(0.15, 0.02) == "Membership / Subscription Retail"
        assert _new(0.16, 0.02) == "Traditional Retail"
        assert _new(0.50, 0.03) == "Traditional Retail"
        # And it is byte-identical to HEAD, i.e. the migration neither caused it
        # nor repaired it.
        for cagr in (0.15, 0.16, 0.50):
            for fcf in (0.01, 0.02, 0.03, 0.049):
                assert _new(cagr, fcf) == _head_ladder(cagr, fcf, D_TO_E,
                                                       False, REV_BASE)
        # The invariant still holds through the step: at a fixed CAGR above
        # 0.15, raising the margin out of the Membership band promotes.
        assert QUALITY_TIER[_new(0.16, 0.05)] > QUALITY_TIER[_new(0.16, 0.02)]
        assert QUALITY_TIER[_new(0.16, 0.15)] > QUALITY_TIER[_new(0.16, 0.05)]

    def test_the_apparel_to_food_and_beverage_step_is_structural_and_predates_this(self):
        """`cagr < 0.03`, margin 0.179 -> 0.180: Apparel -> Food & Beverage.

        The one margin-axis transition that leaves the quality ordering, and it
        cannot be repaired by reordering. Moving Food & Beverage below the
        Apparel band makes it unreachable: the Apparel band covers
        `fcf in [0.05, 0.18)` and the `fcf >= 0.15` Luxury rung covers everything
        above it, so nothing would be left for F&B to catch. That is a deletion
        wearing the clothes of a reordering, and HEAD's own comment on the band
        says Apparel "MUST come before Food & Beverage".

        Excluded from the tier order as structural. The exclusion is not free and
        is not hiding a demotion: F&B anchors on plain `P/E` at 0.50 with a DCF
        leg at 0.30, against Apparel's EV/EBITDA anchor at 0.40 with
        `DCF (FCF+)` at 0.30 — a lateral move between two profiles that both
        carry a cash-flow leg, and both of whose trailing P/E exposure is in
        `_PE_NORM_SWAP_LEGS`, so the Failure-2 trough repair reaches either.
        """
        assert _new(0.02, 0.179) == "Apparel / Athletic Wear"
        assert _new(0.02, 0.18) == "Food & Beverage"
        assert _head_ladder(0.02, 0.179, D_TO_E, False, REV_BASE) == \
            "Apparel / Athletic Wear"
        assert _head_ladder(0.02, 0.18, D_TO_E, False, REV_BASE) == \
            "Food & Beverage"
        # Both carry a DCF leg at the same weight, so the step is not a move
        # off cash-flow valuation.
        con = INDUSTRY_VALUATION_PROFILES["Consumer"]
        fb = {m["name"]: m["weight"] for m in con["Food & Beverage"]["methods"]}
        ap = {m["name"]: m["weight"]
              for m in con["Apparel / Athletic Wear"]["methods"]}
        assert fb["DCF (2-stage)"] == ap["DCF (FCF+)"] == 0.30
        assert fb["P/E"] == 0.5 and ap["P/E (norm)"] == 0.20

    def test_the_gross_margin_rung_pre_empts_the_membership_rung(self):
        """A membership-shaped name with a >= 65% gross margin resolves to Luxury
        Goods, not to Membership / Subscription Retail. 28 grid cells at each of
        0.65 and 0.80.

        Recorded because it is the one consequence of the new rung's placement
        that has a defensible argument on both sides, and the placement follows
        the owner's stated precedence rather than my preference. The owner's
        ladder tests the gross margin second, immediately after `cagr >= 0.15`,
        which puts it above every structural rung below.

        BOTH counterexamples, stated rather than one of them:
          * FOR the shipped order — LVMH or Pernod Ricard in a heavy-capex year
            (gross margin ~68%, conversion down to 3%) satisfies Membership's
            five-way conjunction and would be valued by a warehouse-club profile
            with no premium-earnings anchor. That is plainly wrong.
          * AGAINST it — a genuine high-margin membership retailer loses the
            `FCF Yield` leg at 0.20, which is the leg that prices thin cash
            conversion, and Luxury Goods has no equivalent.

        No fixture or live name reaches this today: COST, the only membership-
        shaped name in the basket, has a 0.1284 gross margin, and BJ's and Sam's
        Club are similar. If a real name ever lands here, the fix is to move the
        Membership rung above the gross-margin rung and re-run this module —
        not to add a second copy of the five-way conjunction as an exclusion.
        """
        assert _new(0.10, 0.02, gm=0.30) == "Membership / Subscription Retail"
        assert _new(0.10, 0.02, gm=0.65) == "Luxury Goods"
        assert _new(0.10, 0.02, gm=0.80) == "Luxury Goods"
        # The membership shape is otherwise untouched: below the threshold it is
        # identical to HEAD across the whole grid.
        for _gm, cagr, fcf in _grid():
            if _gm is not None and _gm >= 0.65:
                continue
            if _head_ladder(cagr, fcf, D_TO_E, False, REV_BASE) == \
                    "Membership / Subscription Retail":
                assert _new(cagr, fcf, gm=_gm) == \
                    "Membership / Subscription Retail", (_gm, cagr, fcf)
        # And Luxury Goods does carry a premium earnings anchor, which is the
        # half of the argument that favours the shipped order.
        lux = {m["name"]: (m["weight"], bool(m.get("anchor")))
               for m in INDUSTRY_VALUATION_PROFILES["Consumer"]["Luxury Goods"]["methods"]}
        assert lux["P/E (Premium)"] == (0.5, True)
        mem = {m["name"]: m["weight"] for m in
               INDUSTRY_VALUATION_PROFILES["Consumer"]["Membership / Subscription Retail"]["methods"]}
        assert mem["FCF Yield"] == 0.2 and "P/E (Premium)" not in mem

    def test_the_unpinned_peers_the_owner_named_are_reached_by_this_change(self):
        """`02331.HK` (Li Ning) and `01368.HK` (Xtep) carry an EMPTY profile
        override, so this ladder decides them and nothing else does.

        The mechanism is pinned first, because it is the part that is certainly
        true: an empty override is not an override, so production runs the ladder
        for both names and the pin offers them no protection.

        The SIZE of their exposure is narrower than "the `< 0.18` band-pass cliff"
        suggests, and this is measured rather than argued. Cliff (i) is not a
        property of the 0.18 bound alone; it needs the `cagr < 0.05` Household
        rung to be standing above the `fcf >= 0.15` Luxury rung AND to be
        reachable, which only happens for `cagr in [0.03, 0.05)`. At any CAGR of
        0.05 or more HEAD's own fall-through already returned Luxury Goods above
        the bound, so a margin improvement was a promotion and not a demotion.
        The demotion window is therefore three percent wide on the growth axis,
        and a peer growing faster than 5% was never in it.

        Stated plainly because it corrects the framing of the instruction rather
        than quietly implementing it: the migration removes the cliff outright for
        every CAGR, so the fix is not conditional on where these two names sit —
        but the exposure the instruction described only ever bit inside
        `[0.03, 0.05)`, and the second exposure worth knowing about is cliff (ii),
        which needs a 40% CAGR and neither sportswear peer is near it.
        """
        for t in ("02331.HK", "01368.HK"):
            assert t in TICKER_SECTOR_LOOKUP, t
            sector, profile = TICKER_SECTOR_LOOKUP[t][0], TICKER_SECTOR_LOOKUP[t][1]
            assert sector == "Consumer", (t, sector)
            assert profile == "", (
                f"{t} now carries a profile override, so it no longer reaches the "
                f"ladder and this test's premise is stale")
            assert get_wacc_profile_for_ticker(t) == ("Consumer", ""), t

        # Inside the demotion window: HEAD demoted, the migration promotes.
        for cagr in (0.03, 0.04, 0.049):
            assert _head_ladder(cagr, 0.179, 0.4, False, None) == "Apparel / Athletic Wear"
            assert _head_ladder(cagr, 0.180, 0.4, False, None) == "Household / Personal"
            assert _new(cagr, 0.180, rev=None, de=0.4) == "Luxury Goods"

        # Outside it: HEAD already promoted, and the migration promotes further.
        # The tier never falls across the bound at any CAGR.
        for cagr in (0.05, 0.08, 0.10, 0.14):
            assert _head_ladder(cagr, 0.180, 0.4, False, None) == "Luxury Goods"
            assert _new(cagr, 0.179, rev=None, de=0.4) == "Apparel / Athletic Wear"
            assert _new(cagr, 0.180, rev=None, de=0.4) == "Luxury Goods"
            assert QUALITY_TIER[_new(cagr, 0.180, rev=None, de=0.4)] > \
                QUALITY_TIER[_new(cagr, 0.179, rev=None, de=0.4)]
        # And a peer growing at 20% lands on Consumer Growth above the bound,
        # which is tier 6 — the strongest profile in the sector.
        assert _new(0.20, 0.180, rev=None, de=0.4) == "Consumer Growth"
        assert _head_ladder(0.20, 0.180, 0.4, False, None) == "Consumer Growth"


# ══════════════════════════════════════════════════════════════════════════════
# The named shapes
# ══════════════════════════════════════════════════════════════════════════════


class TestTheNamedShapes:
    """The four anchors and the one fixture, resolved through the ladder with
    every pin bypassed.

    None of these four names is a golden fixture, and NKE / ONON / EL / 02020.HK
    are all pinned in `TICKER_SECTOR_LOOKUP`, so calling the ladder on them is the
    only way to observe what the migration does to the shapes the owner cares
    about. The inputs are the measured ones recorded in the pin comments and in
    `ARCHETYPES` in `tests/test_consumer_discretionary_gates.py`.

    That is also the correction to the instruction this step came from: the four
    anchor pins do NOT "protect the golden basket while you perform this refactor",
    because none of the four is in the basket. `TestTheGoldenBlindSpot` pins what
    the basket actually does instead.
    """

    def test_cost_still_resolves_to_its_archived_profile(self):
        """The only Consumer fixture, on its own measured inputs.

        Inputs from `scratchpad/gross_margin_table.json`, produced by
        `scratchpad/probe_gross_margin.py` one subprocess per fixture:
        cagr 0.08868248129197176, fcf margin 0.023249614723076645,
        revenue $275,235,000,000, D/E 0.35046632835002056, gross margin
        0.12839... (35,349 / 275,235).

        This assertion is load-bearing in a way the golden suite cannot be: the
        archive holds the ladder's own prior answer for COST, so replay never
        re-runs the ladder and a regression here is invisible to
        `tests/test_golden_valuations.py`. It is the only automated check that
        rung 7 still recognises the profile it was written for.
        """
        got = classify_valuation_profile(
            "Consumer", 0.08868248129197176, 0.023249614723076645,
            0.35046632835002056, False, revenue_base=275235000000.0,
            gross_margin=35349000000.0 / 275235000000.0)
        assert got == "Membership / Subscription Retail", got
        archived = json.loads(
            (FIXTURES / "COST" / "web_run.json").read_text(encoding="utf-8")
        )["data"]["profile_names"]["COST"]
        assert got == archived, (
            "the ladder no longer reproduces COST's archived profile, so the "
            "archive and the engine disagree and replay will keep using the "
            "stale one")

    def test_nke_across_the_edge_that_used_to_be_a_cliff(self):
        """NKE at a 3% CAGR, one thousandth either side of an 0.18 FCF margin.

        HEAD: 0.179 -> Apparel / Athletic Wear, 0.180 -> Household / Personal.
        The upper edge ran the wrong way; raising NKE's FCF margin halved its DCF
        weight (0.30 -> 0.20), dropped its only normalized leg (`P/E (norm)` at
        0.20) and handed 40% of the blend to unadjusted trailing earnings.

        Now: 0.180 -> Luxury Goods, tier 5 against Apparel's tier 4. The cliff is
        gone and the step that replaces it is a promotion. The `TICKER_SECTOR_LOOKUP`
        pin is retained regardless — it is owner-directed, and a pin is a stronger
        guarantee than a rung — but it is no longer load-bearing against a
        demotion, and the comment above it in `sector_profiles.py` says so.
        """
        assert _new(0.03, 0.179, de=0.6, rev=None) == "Apparel / Athletic Wear"
        assert _new(0.03, 0.180, de=0.6, rev=None) == "Luxury Goods"
        assert _head_ladder(0.03, 0.180, 0.6, False, None) == "Household / Personal"
        assert QUALITY_TIER[_new(0.03, 0.180, de=0.6, rev=None)] > \
            QUALITY_TIER[_new(0.03, 0.179, de=0.6, rev=None)]
        # The pin agrees with the ladder below the edge and disagrees above it,
        # which is now a disagreement about a PROMOTION.
        assert TICKER_SECTOR_LOOKUP["NKE"][1] == "Apparel / Athletic Wear"

    def test_onon_no_longer_lands_on_traditional_retail(self):
        """Cliff (ii), the documented ONON case, at ONON's own pinned shape.

        At a 50% CAGR and a 12% FCF margin HEAD returned Traditional Retail —
        the sector's only profile with no DCF leg — for the crime of growing fast.
        The rung-9 catch now returns Apparel / Athletic Wear, which is what the
        same name got at a 39% CAGR. ONON is pinned in `TICKER_SECTOR_LOOKUP` to
        `Consumer Growth`, so the pin still overrides; what changed is that the
        fall-through the pin is protecting against is no longer the weakest
        methodology in the sector.
        """
        assert _new(0.50, 0.12, de=0.3, rev=None) == "Apparel / Athletic Wear"
        assert _head_ladder(0.50, 0.12, 0.3, False, None) == "Traditional Retail"
        assert _new(0.39, 0.12, de=0.3, rev=None) == "Apparel / Athletic Wear"
        assert TICKER_SECTOR_LOOKUP["ONON"][1] == "Consumer Growth"

    def test_el_at_its_real_gross_margin_resolves_to_its_pin_at_every_cycle_position(self):
        """EL's pin says `Luxury Goods`. With EL's real gross margin plumbed in,
        the ladder now agrees at the trough, in the middle and at the peak.

        This is the Failure-2 classifier trap closing at the classifier. HEAD
        returned three different profiles across EL's cycle — Household / Personal
        at the trough, Apparel / Athletic Wear in the healthy middle, Luxury Goods
        at the peak — which is why no fixed remedy could be attached to the name
        and why the repair had to key on a margin deviation instead of a profile
        name. EL's gross margin is ~0.74 and is a filing fact, not a cycle
        position, so the rung is cycle-invariant by construction.

        The last two assertions are the fail-closed half: with the gross margin
        unmeasured EL is back to HEAD's cycle-dependent answer, which is why the
        margin-deviation swap is still needed and is not made redundant by this.
        """
        for cagr, fcf in ((-0.02, 0.10), (0.06, 0.16), (0.08, 0.18), (0.08, 0.20)):
            assert _new(cagr, fcf, de=0.5, rev=None, gm=0.74) == "Luxury Goods", \
                (cagr, fcf)
        assert TICKER_SECTOR_LOOKUP["EL"][1] == "Luxury Goods"
        assert _new(-0.02, 0.10, de=0.5, rev=None, gm=None) == "Household / Personal"
        assert _new(0.06, 0.16, de=0.5, rev=None, gm=None) == "Apparel / Athletic Wear"

    def test_anta_at_its_archetype_shape(self):
        """`02020.HK` at the (0.12, 0.18, 0.4) archetype is unchanged by the
        migration.

        Pinned because the pin comment in `sector_profiles.py` and the override
        test in `test_consumer_discretionary_gates.py` both assert this cell, and
        because Anta's gross margin is ~0.62 — below the new rung — so the rung
        does not reach it either. Note the key: the lookup carries the five-digit
        HK form `02020.HK`, and `2020.HK` is NOT in it and resolves to
        `('Tech', '')`, so an assertion written against the short form would raise
        KeyError rather than test anything. Anta's real valuation gap is the Amer
        Sports associate, which is Route (c) of Step 6 and a look-through
        template, not a classifier question; nothing here should be read as
        addressing it.
        """
        assert _new(0.12, 0.18, de=0.4, rev=None) == "Luxury Goods"
        assert _new(0.12, 0.18, de=0.4, rev=None, gm=0.62) == "Luxury Goods"
        assert _head_ladder(0.12, 0.18, 0.4, False, None) == "Luxury Goods"
        assert TICKER_SECTOR_LOOKUP["02020.HK"][1] == "Apparel / Athletic Wear"
        assert "2020.HK" not in TICKER_SECTOR_LOOKUP
        # Anta's gross margin at ~0.62 sits just under the 0.65 rung, so pin the
        # threshold on a cell where the two sides actually differ: an apparel
        # band shape, which the rung promotes and nothing else does.
        assert _new(0.03, 0.10, de=0.4, rev=None, gm=0.62) == "Apparel / Athletic Wear"
        assert _new(0.03, 0.10, de=0.4, rev=None, gm=0.65) == "Luxury Goods"


# ══════════════════════════════════════════════════════════════════════════════
# The signature
# ══════════════════════════════════════════════════════════════════════════════


class TestTheSignatureIsBackwardCompatible:
    """`gross_margin` is keyword-only and optional on both entry points.

    `classify_valuation_profile` has positional callers across the codebase —
    `tests/test_profile_conformance.py` calls it with five positional arguments —
    and a keyword-only parameter is the only addition that cannot break one. This
    class pins that property instead of trusting it.
    """

    def test_gross_margin_cannot_be_passed_positionally(self):
        sig = inspect.signature(classify_valuation_profile)
        p = sig.parameters["gross_margin"]
        assert p.kind is inspect.Parameter.KEYWORD_ONLY, p.kind
        assert p.default is None
        with pytest.raises(TypeError):
            classify_valuation_profile("Consumer", 0.03, 0.10, 0.6, False,
                                       275e9, 0.74)

    def test_get_valuation_profile_forwards_it_verbatim(self):
        sig = inspect.signature(get_valuation_profile)
        p = sig.parameters["gross_margin"]
        assert p.kind is inspect.Parameter.KEYWORD_ONLY, p.kind
        assert p.default is None
        # A high gross margin changes the answer through the wrapper too, so the
        # forwarding is real and not a swallowed kwarg.
        _name, prof = get_valuation_profile("Consumer", 0.10, 0.10, 0.4,
                                            revenue_base=1e9, gross_margin=0.80)
        assert _name == "Luxury Goods"
        assert prof is INDUSTRY_VALUATION_PROFILES["Consumer"]["Luxury Goods"]
        low, _ = get_valuation_profile("Consumer", 0.10, 0.10, 0.4,
                                       revenue_base=1e9, gross_margin=0.30)
        assert low == "Apparel / Athletic Wear"

    def test_every_positional_caller_still_gets_heads_answer(self):
        """Omitting the parameter is not a new code path.

        With `gross_margin` absent the ladder must reproduce HEAD exactly except
        on the three documented fix cells, and this asserts the "except" half
        cell by cell on shapes that appear in the repo's other test modules, so a
        positional caller elsewhere in the suite cannot be silently re-routed.

        The list deliberately excludes `(0.15, 0.16)`, which DOES move — that is
        the rung-1 promotion, one of the three fix classes, and it is pinned in
        `TestTheMigrationChangesExactlyWhatItSays` instead. An omission there
        would be a test hiding a move, not a test proving there is none.
        """
        for cagr, fcf, de in ((0.03, 0.10, 0.6), (0.30, 0.12, 0.3),
                              (-0.02, 0.10, 0.5), (0.12, 0.18, 0.4),
                              (0.08, 0.20, 0.5), (0.06, 0.16, 0.5),
                              (0.02, 0.20, 0.6), (0.10, 0.02, 0.3)):
            assert classify_valuation_profile("Consumer", cagr, fcf, de) == \
                _head_ladder(cagr, fcf, de, False, None), (cagr, fcf, de)
        # The excluded cell, stated rather than merely absent.
        assert classify_valuation_profile("Consumer", 0.15, 0.16, 0.4) == "Consumer Growth"
        assert _head_ladder(0.15, 0.16, 0.4, False, None) == "Apparel / Athletic Wear"

    def test_the_lookup_override_path_is_untouched(self):
        """`get_wacc_profile_for_ticker` never sees a gross margin, and does not
        need one.

        The lookup path returns `(sector, profile)` and short-circuits the ladder
        entirely when the profile is non-empty. Plumbing the margin into the
        classifier does not change routing for any pinned name, which is why the
        five `lookup_tables` fixtures are unaffected. Measured on the four anchors
        plus the two unpinned peers.

        Anta is pinned under the five-digit HK key `02020.HK`; the short form
        `2020.HK` is absent from the lookup and falls back to `('Tech', '')`. Both
        facts are asserted, because the owner's Step 6 and Step 7 notes write the
        name as `2020.HK` and a test copied from them would KeyError.
        """
        assert get_wacc_profile_for_ticker("NKE") == ("Consumer", "Apparel / Athletic Wear")
        assert get_wacc_profile_for_ticker("ONON") == ("Consumer", "Consumer Growth")
        assert get_wacc_profile_for_ticker("EL") == ("Consumer", "Luxury Goods")
        assert get_wacc_profile_for_ticker("02020.HK") == ("Consumer", "Apparel / Athletic Wear")
        assert get_wacc_profile_for_ticker("2020.HK") == ("Tech", "")
        # The two unpinned peers carry an empty override, so production DOES run
        # the ladder for them. That is the vulnerability the owner named.
        assert get_wacc_profile_for_ticker("02331.HK") == ("Consumer", "")
        assert get_wacc_profile_for_ticker("01368.HK") == ("Consumer", "")
        # And COST is not in the lookup at all, so it falls back to `("Tech","")`
        # and is routed to Consumer by the sector classifier upstream.
        assert "COST" not in TICKER_SECTOR_LOOKUP
        assert get_wacc_profile_for_ticker("COST") == ("Tech", "")


# ══════════════════════════════════════════════════════════════════════════════
# The engine side of the plumbing
# ══════════════════════════════════════════════════════════════════════════════


class TestTheEnginePassesTheEnginesOwnGrossMargin:
    """`run_dcf_agent` computes the margin with `_gross_margin`, the same function
    the EV/Revenue multiple qualifier already uses.

    Two readers of the same row shape that define it differently would let the
    classifier and the multiple qualifier disagree about what a gross margin is,
    and nothing in the output would show it. Pinning the shared call site is
    cheaper than pinning the agreement.
    """

    @pytest.fixture(scope="class")
    def src(self):
        from src.agents.analysis import dcf_agent
        return inspect.getsource(dcf_agent)

    def test_the_classify_site_binds_the_shared_helper(self, src):
        assert "_gross_margin_for_classify = _gross_margin(most_recent)" in src

    def test_both_get_valuation_profile_calls_forward_it(self, src):
        assert src.count("gross_margin=_gross_margin_for_classify") == 2, (
            "there are two `get_valuation_profile` call sites in the classify "
            "block and both must forward the margin; a third appearing means a "
            "new call site that this test has not seen")

    def test_the_routing_trace_publishes_it(self, src):
        assert '"gross_margin":   _ledger_num(_gross_margin_for_classify)' in src

    def test_gross_margin_is_read_off_the_most_recent_row_only(self, src):
        """Scenario-invariance, pinned from the source shape.

        `most_recent = series[-1]` is a filing fact, so the margin cannot differ
        between the bear, base and bull passes. Measured rather than argued:
        `scratchpad/probe_gross_margin.py` recorded three classify hooks per
        fixture and reported `inconsistent gross margin across scenarios: []` for
        all 14. If this ever becomes scenario-dependent the profile itself becomes
        scenario-dependent, which is a different and much worse bug.

        The assignment appears twice in the module (the historical-series bind and
        a re-bind further down) plus once inside a comment, so this counts lines
        rather than substrings.
        """
        binds = [ln for ln in src.splitlines()
                 if ln.strip() == "most_recent = series[-1]"]
        assert len(binds) == 2, len(binds)
        assert "_gross_margin_for_classify = _gross_margin(most_recent)" in src


# ══════════════════════════════════════════════════════════════════════════════
# The blind spot
# ══════════════════════════════════════════════════════════════════════════════


class TestTheGoldenBlindSpot:
    """Why no golden re-baseline accompanies this change, and why that is not
    reassuring.

    The claim usually made when a change ships without a golden diff is "zero
    blast radius". For a classifier change that claim is not available, and the
    reason is structural: golden replay never runs the ladder. This class pins the
    two mechanisms that produce that, so the absence of a golden diff is a
    recorded fact about the harness and not an inference about the change.
    """

    def test_profile_names_is_archived_and_profile_sources_is_not(self):
        from src.memory.golden_state import _DIRECT_KEYS
        assert "profile_names" in _DIRECT_KEYS
        assert "sector" in _DIRECT_KEYS and "sectors" in _DIRECT_KEYS
        assert "profile_sources" not in _DIRECT_KEYS, (
            "profile provenance is now archived, so `routing_trace.router_source` "
            "is recoverable and part of this class's premise is stale")

    def test_every_fixture_with_an_archived_run_carries_a_profile_name(self):
        """Nine of fourteen fixtures, read off the archives themselves.

        Each carries `profile_names[ticker]` non-null and `profile_sources` null.
        `run_dcf_agent` calls `get_valuation_profile` only when that entry is
        absent or fails to resolve, so on these nine the ladder is unreachable —
        and because `profile_sources` is not archived, there is no record of WHERE
        the name came from, so the projection reports
        `routing_trace.winner == "router"` for a value that was actually produced
        by the ladder at capture time.
        """
        seen = []
        for d in sorted(FIXTURES.iterdir()):
            f = d / "web_run.json"
            if not d.is_dir() or not f.exists():
                continue
            data = json.loads(f.read_text(encoding="utf-8"))["data"]
            ticker = (data.get("tickers") or [None])[0]
            seen.append((d.name, ticker,
                         (data.get("profile_names") or {}).get(ticker),
                         data.get("profile_sources")))
        assert len(seen) == 9, sorted(x[0] for x in seen)
        for name, ticker, profile, sources in seen:
            assert ticker, name
            assert profile, (name, "no archived profile name")
            assert sources is None, (name, sources)

    def test_the_other_five_resolve_from_the_lookup_tables(self):
        """The remaining five fixtures never had an archived run to bypass.

        `AAPL`, `C38U_SI`, `FCX`, `SCHW` and `V` carry
        `state_source == "lookup_tables"` and their profile comes from
        `TICKER_SECTOR_LOOKUP` or the SGX fallback. So the split is 9 archived +
        5 lookup = 14 of 14 bypassing the ladder, by two unrelated mechanisms.
        """
        lookup = []
        for d in sorted(FIXTURES.iterdir()):
            if not d.is_dir():
                continue
            meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
            if meta.get("state_source") == "lookup_tables":
                lookup.append(d.name)
        assert lookup == ["AAPL", "C38U_SI", "FCX", "SCHW", "V"], lookup

    def test_the_archive_holds_the_ladders_own_answer_for_cost(self):
        """The write-back, and what it costs.

        `dcf_agent.py`'s "v3.21 (Fix D)" block writes the locally-resolved profile
        back into `state["data"]["profile_names"]` so late-pipeline consumers can
        see it. That is correct for its purpose and is not a bug. The consequence
        for replay is that `profile_names` is archived, so the ladder's ANSWER at
        capture time becomes an INPUT at replay time.

        COST is the witness because it is the only fixture whose production
        profile the Consumer ladder decides: it is absent from
        `TICKER_SECTOR_LOOKUP`, so nothing upstream pins it. Its archived name is
        reproduced exactly by `test_cost_still_resolves_to_its_archived_profile`,
        which is the proof that the archive is holding the ladder's output and not
        an independent opinion of it. A ladder change therefore moves production
        and moves no golden number, in either direction.
        """
        from src.agents.analysis import dcf_agent
        src = inspect.getsource(dcf_agent)
        assert '_pn_dict = state["data"].setdefault("profile_names", {})' in src
        assert "_pn_dict[ticker] = profile_name" in src
        assert "COST" not in TICKER_SECTOR_LOOKUP
        # The write-back is guarded so it cannot overwrite a name that was already
        # supplied, which is what makes it a pure pre-classification feed and not a
        # two-way sync.
        assert "if isinstance(_pn_dict, dict) and not _pn_dict.get(ticker):" in src

    def test_meli_is_the_other_ladder_decided_fixture_and_is_also_bypassed(self):
        """The blind spot is not Consumer-specific.

        MELI is absent from `TICKER_SECTOR_LOOKUP`, so its production profile is
        decided by the Tech ladder, and the archived `Hyper-Growth Platform` is
        that ladder's own output fed back. Asserted by reproducing it from MELI's
        measured inputs (cagr 0.4219, fcf margin 0.3028, D/E 1.6882, revenue
        $28.89bn) rather than by asserting the archive agrees with itself.

        Recorded here because it generalises the warning: EVERY ladder-decided
        fixture in the basket is insulated from the golden suite, so any future
        classifier change to any sector branch ships with the same zero coverage.
        """
        assert "MELI" not in TICKER_SECTOR_LOOKUP
        got = classify_valuation_profile("Tech", 0.4219, 0.3028, 1.6882, False,
                                         revenue_base=28.89e9)
        archived = json.loads(
            (FIXTURES / "MELI" / "web_run.json").read_text(encoding="utf-8")
        )["data"]["profile_names"]["MELI"]
        assert got == archived == "Hyper-Growth Platform", (got, archived)

    def test_no_fixture_projection_carries_ladder_inputs(self):
        """`routing_trace.ladder_inputs` appears in zero of fourteen projections.

        So adding the `gross_margin` key to it — which `_DICT_KEYS` captures
        whole, since `routing_trace` is one of the dict keys the projection
        flattens — cannot move a golden number either. Verified against the
        committed snapshot rather than against a replay, because the snapshot is
        the thing the golden suite diffs.
        """
        snap = json.loads(
            (Path(__file__).resolve().parent / "golden" / "snapshots.json")
            .read_text(encoding="utf-8"))
        offenders = []
        for name, entry in snap.items():
            if name.startswith("_"):
                continue
            for k in (entry.get("projection") or {}):
                if "ladder_inputs" in k:
                    offenders.append((name, k))
        assert not offenders, offenders
        assert sum(1 for k in snap if not k.startswith("_")) == 14


# ══════════════════════════════════════════════════════════════════════════════
# The P/E (ops) widening
# ══════════════════════════════════════════════════════════════════════════════


class TestTheOpsSpellingsAreInTheSwap:
    """`_PE_NORM_SWAP_LEGS` widened from two keys to four, on the owner's
    authorisation: "If a company's consolidated margin is cyclically depressed or
    inflated, operating earnings are equally distorted."

    The three collisions that would have made this unsafe were measured before it
    was made, and each is pinned here so the widening stays safe rather than
    having been safe once.
    """

    def test_the_map_has_four_keys_and_one_value(self):
        from src.agents.analysis import dcf_agent
        assert dcf_agent._PE_NORM_SWAP_LEGS == {
            "P/E": "P/E (norm)",
            "P/E (Premium)": "P/E (norm)",
            "P/E (ops)": "P/E (norm)",
            "P/E (Ops)": "P/E (norm)",
        }
        assert set(dcf_agent._PE_NORM_SWAP_LEGS.values()) == {"P/E (norm)"}
        # Both case spellings, because the taxonomy is not case-consistent and
        # the map keys on exact string match.
        assert len({k.lower() for k in dcf_agent._PE_NORM_SWAP_LEGS}) == 3

    def test_the_census_moved_from_thirty_one_to_thirty_seven(self):
        """The measured size of the widening, off the real table.

        99 profiles; 37 carry a trailing P/E leg of some spelling; 31 carried one
        of the TWO spellings the swap used to name and 37 carry one of the FOUR it
        names now; 10 of those anchored on it and 13 do now. The three new anchors
        are all at 0.40, and no Consumer profile carries an ops leg, so
        `consumer_anchors` is unchanged by the widening.
        """
        from src.agents.analysis import dcf_agent
        tot = trail = elig = anchored = 0
        consumer_anchors, anchors = [], []
        for sec, profs in INDUSTRY_VALUATION_PROFILES.items():
            for pn, cfg in profs.items():
                tot += 1
                ms = [m for m in cfg.get("methods", []) if isinstance(m, dict)]
                names = {m.get("name") for m in ms}
                if names & {"P/E", "P/E (ops)", "P/E (Ops)", "P/E (Premium)"}:
                    trail += 1
                if names & dcf_agent._PE_NORM_SWAP_LEGS.keys():
                    elig += 1
                for m in ms:
                    if m.get("name") in dcf_agent._PE_NORM_SWAP_LEGS and m.get("anchor"):
                        anchored += 1
                        anchors.append((sec, pn, m["name"], m["weight"]))
                        if sec == "Consumer":
                            consumer_anchors.append((pn, m["name"], m["weight"]))
        # Wave 2 (2026-09-21): +1 profile, and Regulated Utility's new trailing P/E anchor
        # (see the same census in test_consumer_discretionary_gates.py).
        # Backlog-Gated Long Cycle (2026-09-22): +1 profile, no normalised leg and no trailing P/E.
        # Wave 3 (owner framework 2026-09-22): Aerospace & Defense split into seven profiles.
        assert (tot, trail, elig, anchored) == (113, 38, 38, 14)   # +China Internet Platform (2026-09-26)
        assert sorted(consumer_anchors) == [
            ("Food & Beverage", "P/E", 0.5),
            ("Household / Personal", "P/E", 0.4),
            ("Luxury Goods", "P/E (Premium)", 0.5),
            ("Membership / Subscription Retail", "P/E", 0.4),
        ], consumer_anchors
        # The three the widening added, all at 0.40, none Consumer.
        added = sorted(a for a in anchors if a[2] in ("P/E (ops)", "P/E (Ops)"))
        assert added == [
            ("Biopharma", "Managed Care", "P/E (Ops)", 0.4),
            ("HealthcareServices", "Managed Care", "P/E (Ops)", 0.4),
            ("HealthcareServices", "Pharma Distribution", "P/E (Ops)", 0.4),
        ], added

    def test_the_six_ops_carriers_are_the_whole_of_the_former_gap(self):
        """37 - 31 = 6, and the six are exactly these.

        Kept from the pre-widening test, whose premise ("these are deliberately
        NOT in the swap") is now authorised away but whose census was correct and
        is still the complete list of profiles that read trailing earnings through
        an ops spelling. Three anchor at 0.40, so a trough-earnings healthcare
        name now gets the same repair a trough-earnings beauty name already got.
        """
        ops = {"P/E (ops)", "P/E (Ops)"}
        carriers = []
        for sec, profs in INDUSTRY_VALUATION_PROFILES.items():
            for pn, cfg in profs.items():
                for m in cfg.get("methods", []):
                    if isinstance(m, dict) and m.get("name") in ops:
                        carriers.append((sec, pn, m["name"], m["weight"],
                                         bool(m.get("anchor"))))
        assert sorted(carriers) == [
            ("Biopharma", "Managed Care", "P/E (Ops)", 0.4, True),
            ("Financials", "Insurance", "P/E (ops)", 0.15, False),
            ("Financials", "Insurance (P&C)", "P/E (ops)", 0.2, False),
            ("HealthcareServices", "Healthcare Providers / Services", "P/E (Ops)", 0.3, False),
            ("HealthcareServices", "Managed Care", "P/E (Ops)", 0.4, True),
            ("HealthcareServices", "Pharma Distribution", "P/E (Ops)", 0.4, True),
        ], carriers
        assert len(carriers) == 37 - 31
        # All six are now swappable, which is the widening in one assertion.
        from src.agents.analysis import dcf_agent
        assert ops <= set(dcf_agent._PE_NORM_SWAP_LEGS)

    def test_no_profile_names_two_swappable_spellings(self):
        """The collision that would have made a four-key map unsafe: two keys, one
        value, and a profile carrying both would emit two rows named
        `P/E (norm)`, which `_blend_methods` resolves by name, doubling the
        branch's weight with nothing in the output to show it.

        Measured across all 99 profiles: zero. Also zero for any duplicate method
        name at all, which is the stronger property the first-wins dedup relies
        on.
        """
        from src.agents.analysis import dcf_agent
        keys = set(dcf_agent._PE_NORM_SWAP_LEGS)
        both = dups = 0
        for _sec, profs in INDUSTRY_VALUATION_PROFILES.items():
            for _pn, cfg in profs.items():
                names = [m.get("name") for m in cfg.get("methods", [])
                         if isinstance(m, dict)]
                both += int(len(keys & set(names)) > 1)
                dups += int(len(names) != len(set(names)))
        assert (both, dups) == (0, 0)

    def test_no_ops_carrier_also_carries_a_normalized_leg(self):
        """If one did, the swap would rewrite a profile that already reads
        normalized earnings, and the `emitted` first-wins guard would be the only
        thing standing between that and a double-weighted branch.

        Zero of the six. Also zero of the 37: no profile in the taxonomy names
        both a swappable trailing spelling and `P/E (norm)`, which is what makes
        the double-weight guard vacuous today and worth keeping anyway.
        """
        from src.agents.analysis import dcf_agent
        norm = set(dcf_agent._PE_NORM_BRANCH_SPELLINGS)
        offenders = []
        for sec, profs in INDUSTRY_VALUATION_PROFILES.items():
            for pn, cfg in profs.items():
                names = {m.get("name") for m in cfg.get("methods", [])
                         if isinstance(m, dict)}
                if names & set(dcf_agent._PE_NORM_SWAP_LEGS) and names & norm:
                    offenders.append((sec, pn, sorted(names & norm)))
        assert not offenders, offenders

    def test_no_cyclical_profile_carries_an_ops_leg(self):
        """`_MID_CYCLE_LEG_SWAPS` needed no widening, and this is why.

        The two swaps both target `P/E (norm)`, and `_mid_cycle_leg_swaps` is
        gated on `_CYCLICAL_PROFILES`. If a cyclical profile carried an ops leg,
        the two swaps would race on the same row. None does: the four cyclical
        profiles with a P/E-family leg (Automotive (OEM), Airlines, Steel /
        Metals, Specialty Chemicals) all carry plain `P/E`, which both maps
        already handled.
        """
        from src.agents.analysis import dcf_agent
        ops = {"P/E (ops)", "P/E (Ops)"}
        for sec, profs in INDUSTRY_VALUATION_PROFILES.items():
            for pn, cfg in profs.items():
                if pn not in dcf_agent._CYCLICAL_PROFILES:
                    continue
                names = {m.get("name") for m in cfg.get("methods", [])
                         if isinstance(m, dict)}
                assert not (names & ops), (sec, pn, sorted(names & ops))

    def test_the_swap_target_dispatches_for_every_profile(self):
        """`P/E (norm)` is dispatched by name, not by profile, so a widening that
        reached a profile the branch could not handle would silently value the leg
        at zero.

        The dispatcher set is `{"P/E (norm)", "P/E norm", "Normalized P/E"}` and
        it sits in `_compute_method_value`, which every profile goes through. This
        answers, for this swap, the general question nothing else in the suite
        pins: that every name a swap can PRODUCE is a name the engine can value.
        """
        from src.agents.analysis import dcf_agent
        src = inspect.getsource(dcf_agent)
        assert 'if method_name in {"P/E (norm)", "P/E norm", "Normalized P/E"}:' in src
        produced = set(dcf_agent._PE_NORM_SWAP_LEGS.values())
        assert produced == {"P/E (norm)"}
        assert produced <= set(dcf_agent._PE_NORM_BRANCH_SPELLINGS)

    def test_the_first_wins_order_is_the_profiles_own_row_order(self):
        """Named by the emission-priority comment in `_pe_norm_leg_swaps`.

        The policy is first-wins and the tie-break is the profile's own `methods`
        row order — the same order `_blend_methods` reads. Asserted by REVERSING a
        synthetic profile's rows and checking the winner reverses with them, rather
        than by asserting a fixed spelling wins, because the point is that the
        order is the profile's and not the map's. No profile in the taxonomy
        reaches this (pinned above), so the test is about the policy being
        deterministic and not about a live collision.
        """
        from src.agents.analysis import dcf_agent
        rows = [
            {"name": "P/E", "weight": 0.30, "anchor": True},
            {"name": "EV/EBITDA", "weight": 0.20},
            {"name": "P/E (Ops)", "weight": 0.20},
        ]
        fwd = dcf_agent._pe_norm_leg_swaps({"methods": rows}, 0.90)
        assert [s["from"] for s in fwd] == ["P/E"], fwd
        rev = dcf_agent._pe_norm_leg_swaps(
            {"methods": list(reversed(rows))}, 0.90)
        assert [s["from"] for s in rev] == ["P/E (Ops)"], rev
        # Exactly one record either way: first-wins, not both.
        assert len(fwd) == len(rev) == 1
        eff = dcf_agent._apply_pe_norm_swaps(rows, fwd)
        assert [m["name"] for m in eff] == ["P/E (norm)", "EV/EBITDA", "P/E (Ops)"]

    def test_the_widening_has_zero_golden_blast_radius_for_two_reasons(self):
        """Stated because "no golden diff" is otherwise read as verification.

        Reason one: none of the 14 fixtures resolves to any of the six ops-carrying
        profiles. The nine archived names and five lookup names are Money Center
        Bank, Hyperscaler / Tech Conglomerate (x2), Conglomerate / Industrial (SG),
        Membership / Subscription Retail, Money Center Bank (SG), Hyper-Growth
        Platform, Memory / DRAM-NAND, IPP, S-REIT... and the six carriers are two
        Managed Care profiles, Pharma Distribution, Healthcare Providers /
        Services, Insurance and Insurance (P&C). Disjoint.

        Reason two, and it is independent: nothing in the basket reaches the 0.40
        deviation bar at all, so `_pe_norm_leg_swaps` emits nothing for any
        fixture under either the two-key or the four-key map. The largest measured
        deviation is FCX at 0.3579 and FCX carries no trailing P/E leg.

        So the widening is validated by these unit tests and by the owner's
        reasoning, and by no fixture. That is a coverage statement, not a comfort.
        """
        from src.agents.analysis import dcf_agent
        assert dcf_agent._PE_NORM_SWAP_DEVIATION == 0.40
        archived = {}
        for d in sorted(FIXTURES.iterdir()):
            if not d.is_dir():
                continue
            f = d / "web_run.json"
            if f.exists():
                data = json.loads(f.read_text(encoding="utf-8"))["data"]
                ticker = (data.get("tickers") or [None])[0]
                archived[d.name] = (data.get("profile_names") or {}).get(ticker)
            else:
                meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
                assert meta.get("state_source") == "lookup_tables", d.name
                archived[d.name] = TICKER_SECTOR_LOOKUP.get(
                    meta["ticker"], ("", ""))[1] or "(lookup/SGX fallback)"
        ops_profiles = {"Managed Care", "Pharma Distribution",
                        "Healthcare Providers / Services", "Insurance",
                        "Insurance (P&C)"}
        assert not (set(archived.values()) & ops_profiles), archived
        assert len(archived) == 14
