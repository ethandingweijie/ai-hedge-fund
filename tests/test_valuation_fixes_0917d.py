"""Owner decision 5 (2026-09-17): an FCF yield is a denominator, not a metric.

The defect had two halves and they are fixed in two places, because they fail
in opposite directions.

**The band admitted non-positive yields.** `_BANDS["fcf_yield"]` was
`(-0.50, 0.50)`. An FCF yield is the denominator of a capitalisation —
``value = FCF / yield`` — so a yield at or below zero does not produce a low
value, it produces a NEGATIVE one, or an enormous one as the divisor
approaches zero. Measured on production Postgres (read-only), 1040 stored
`fcf_yield` medians across 7 exchanges: **297 are ≤ 0, 50 more sit in
(0, 0.005], and 4 exceed 0.25**. SCHW's own cohort, ``US / industry /
Financial - Capital Markets``, measured **-0.003152 in BOTH the `all` (18
peers) and `large` (10 peers) cohorts** — the same value in each, so the
cohort split does not rescue it.

**The leg floored the result instead of refusing it.**
`target_yield = max(target_yield, 0.01)` converted that -0.003152 into 0.01
and published **100× free cash flow** as an intrinsic value, with nothing on
the card to say the input had been discarded.

Two consequences worth stating because they are not obvious from either diff:

*The floor was invisible to every test that existed.* Measured across all 12
FCF-leg calls the golden fixtures make — AAPL (peer 0.035), COST (0.020), V
(0.030) — `target_yield` ranges 0.014710 to 0.046667 and the `max()` **never
bound once**. SCHW makes no FCF-leg call at all, its persisted methods being
`['P/BV', 'P/E (norm)']`. So the golden baseline cannot see this defect, which
is why the diff for this change is empty and why that emptiness is asserted
here rather than assumed.

*Narrowing the high side is a second effect of the same numbers.* The band
becomes `(0.005, 0.25)`, so readings in `(0.25, 0.50]` — a 2×-to-4×
capitalisation, i.e. distress or a one-off cash flow — now drop out of the
median too. On production that touches **4 rows of 1040 (0.4%)**: SHH
+0.345632 (11 peers), SHH +0.338160 (9), HKSE +0.338160 (9), US +0.250729
(6). Small, but it is a change to which peers reach the median and not only a
change to whether the median is usable, so it is named rather than left to be
discovered at the next weekly refresh.

The band change is **write-path only** and therefore cannot move a replay:
`_clean` has exactly one call site, inside `compute_medians`, which the weekly
refresh runs. `load_comps` reads stored rows and never re-filters them. It
bites at the next refresh, not at this commit.

A harness trap found while implementing this, recorded because the failure
message points the wrong way: hoisting ``from src.data.regional_comps import
MIN_VALID_FCF_YIELD`` to `dcf_agent`'s module scope broke **14 of 17** golden
tests with *"1 recorded call(s) never used — the fixture is stale"*. It is not
stale. `regional_comps` binds `_fmp_get` by value, the Replayer patches that
name on `src.tools.api`, and `replay_fixture` imports `dcf_agent` OUTSIDE its
`with Replayer(...)` block — so a module-level import loads `regional_comps`
before the patch exists and its `/stable/profile` fetches escape the
recording. Regenerating on that message would have baked in a replay that
reaches for the network. `test_the_lazy_import_is_load_bearing` pins it.
"""
from __future__ import annotations

import inspect
import json
import os
import statistics

import pytest

from src.agents.analysis import dcf_agent
from src.agents.analysis.dcf_agent import _compute_method_value
from src.data import regional_comps as rc
from src.data.regional_comps import (
    MAX_VALID_FCF_YIELD,
    MIN_VALID_FCF_YIELD,
    _BANDS,
    _clean,
    compute_medians,
)

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SNAPSHOTS = os.path.join(_REPO, "tests", "golden", "snapshots.json")

#: SCHW's measured production cohort median, in BOTH cohorts. Used verbatim so
#: the arithmetic in these tests is the arithmetic that shipped.
SCHW_US_CAPITAL_MARKETS = -0.003152

#: The floor the leg used to apply. Its removal is the fix; it is kept here as a
#: number so the tests can quantify what it cost rather than assert it is gone.
_OLD_SILENT_FLOOR = 0.01


# ── A row and a peer table shaped like the cyclical-peak tests' ─────────────
#
# fcf / shares = 2e9 / 200e6 = 10.0 per share, so the leg's arithmetic is
# readable: value = 10.0 / target_yield.
_ROW = {
    "revenue": 20e9, "ebitda": 4e9, "ebit": 2.5e9, "net_income": 2e9,
    "total_equity": 10e9, "total_assets": 18e9,
    "cash": 1e9, "total_debt": 2e9, "net_debt": 1e9,
    "book_value_per_share": 50.0, "shares_outstanding": 200e6,
    "operating_cash_flow": 3e9, "capital_expenditure": -1e9,
    "free_cash_flow": 2e9,
}
_FCF_PER_SHARE = 10.0
_SCENARIO_MULT = {"bear": 0.75, "base": 1.00, "bull": 1.25}


def _call(peer: dict, *, scenario: str = "base", growth_premium: float = 1.0,
          row: dict | None = None, method: str = "FCF Yield"):
    """One FCF-Yield leg call against a controlled peer table."""
    r = dict(_ROW if row is None else row)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(dcf_agent, "get_sector_peer_multiples",
                   lambda *a, **k: dict(peer))
        return _compute_method_value(
            method_name=method, most_recent=r, revenue_base=r["revenue"],
            shares=200e6, net_debt=r["net_debt"], market_cap=10e9, wacc=0.09,
            growth_base=0.03, fcf_margin_base=0.10, tgr=0.025, fcf_floor=0.0,
            sector="Financial Services", scenario=scenario,
            reported_currency="USD", is_hk=False,
            growth_premium=growth_premium, sbc_pe_discount=1.0,
            profile_name="Brokerage", ticker="SCHW", end_date="2026-09-17")


def _leg_source() -> str:
    """The FCF-Yield leg's source with comment lines removed.

    Comments are stripped before any absence assertion, and that is not
    cosmetic. The leg's own comment quotes the deleted line —
    ``target_yield = max(target_yield, 0.01)`` — to explain what was wrong, so a
    naive substring test over the raw source either fails on the comment or, if
    inverted, passes by matching it. A test that can be satisfied by its own
    explanation is not a test.
    """
    src = inspect.getsource(dcf_agent)
    start = src.index('if method_name in {"FCF Yield", "P/CF", "Price/CF"}:')
    end = src.index("# ── rNPV (Biopharma pipeline)", start)
    body = src[start:end]
    return "\n".join(ln for ln in body.splitlines()
                     if not ln.lstrip().startswith("#"))


# ── The band ────────────────────────────────────────────────────────────────

def test_the_band_is_the_owners_numbers():
    assert _BANDS["fcf_yield"] == (0.005, 0.25)


def test_the_band_is_built_from_the_public_constants():
    """One number, two readers.

    `_clean` filters peer readings before their median is taken; the valuation
    leg refuses to divide by a target yield at or below the same floor. If the
    two could hold different values the band would admit a reading the leg then
    rejects, or the reverse — and the disagreement would be invisible, because
    each half looks locally correct.
    """
    assert _BANDS["fcf_yield"] == (MIN_VALID_FCF_YIELD, MAX_VALID_FCF_YIELD)
    assert MIN_VALID_FCF_YIELD == 0.005
    assert MAX_VALID_FCF_YIELD == 0.25
    # The leg must read the constant, not re-type the number.
    code = _leg_source()
    assert "MIN_VALID_FCF_YIELD" in code
    assert "0.005" not in code


def test_the_leg_and_the_band_agree_on_the_floor():
    """The leg's rejection boundary IS the band's lower edge.

    Not a coincidence of two literals: `dcf_agent` imports the constant, so
    this asserts the import resolves to the same object the band was built
    from.
    """
    from src.data.regional_comps import MIN_VALID_FCF_YIELD as _BAND_MIN
    src = inspect.getsource(dcf_agent)
    assert "from src.data.regional_comps import MIN_VALID_FCF_YIELD" in src
    assert _BAND_MIN is MIN_VALID_FCF_YIELD
    assert _BANDS["fcf_yield"][0] == _BAND_MIN


def test_the_high_side_was_tightened_too_not_just_the_low_side():
    """Named because it is a second effect of the owner's numbers.

    The old band was `(-0.50, 0.50)`. Rejecting non-positive yields is the
    decision; excluding `(0.25, 0.50]` is a consequence of writing the upper
    bound as 0.25 rather than leaving it at 0.50. On production that is 4 rows
    of 1040, but it changes WHICH peers reach the median, not only whether the
    median is usable.
    """
    assert MAX_VALID_FCF_YIELD < 0.50
    assert _clean("fcf_yield", [0.2501, 0.30, 0.40, 0.50]) == []
    assert _clean("fcf_yield", [0.25]) == [0.25]


# ── _clean drops, it does not clip ─────────────────────────────────────────

@pytest.mark.parametrize("value", [
    -0.50, -0.067937, SCHW_US_CAPITAL_MARKETS, -1e-9, 0.0,
])
def test_non_positive_readings_are_dropped(value):
    """Zero is dropped too, and the boundary is inclusive at 0.005.

    `0.0` is not a rounding artefact here: three HKSE cohorts store exactly
    +0.000000 as their median (Engineering & Construction, Information
    Technology Services, Entertainment), so zero is a value this pipeline
    really does produce.
    """
    assert _clean("fcf_yield", [value]) == []
    assert _clean("fcf_yield", [value, 0.03]) == [0.03]


def test_the_floor_is_inclusive_and_just_below_it_is_not():
    assert _clean("fcf_yield", [MIN_VALID_FCF_YIELD]) == [MIN_VALID_FCF_YIELD]
    assert _clean("fcf_yield", [0.0049999]) == []


def test_clean_drops_rather_than_clips():
    """A dropped reading leaves the median; a clipped one would poison it.

    Clipping -0.50 up to 0.005 would put a fabricated floor value into the
    median and drag it down — the same failure shape as the leg's `max()`, one
    layer earlier.
    """
    vals = [-0.50, -0.20, SCHW_US_CAPITAL_MARKETS, 0.02, 0.03, 0.04, 0.30]
    assert _clean("fcf_yield", vals) == [0.02, 0.03, 0.04]
    assert statistics.median(_clean("fcf_yield", vals)) == 0.03
    # The old band admitted five more of those seven readings.
    assert len(_clean("fcf_yield", vals)) < len([v for v in vals if -0.50 <= v <= 0.50])


def test_nan_inf_and_none_are_still_dropped():
    """The new bound must not disturb the pre-existing hygiene checks."""
    assert _clean("fcf_yield", [None, float("nan"), float("inf"),
                                float("-inf"), 0.03]) == [0.03]


def test_an_all_invalid_cohort_yields_nothing_not_a_default():
    assert _clean("fcf_yield", [-0.2, -0.1, 0.0]) == []


# ── The write-path invariant ────────────────────────────────────────────────

def _basket(symbols: list[str]) -> dict[str, list[dict]]:
    return {"Financial - Capital Markets": [
        {"symbol": s, "market_cap": 1e10 - i} for i, s in enumerate(symbols)]}


def test_after_the_next_refresh_every_stored_median_is_in_band_or_absent():
    """The invariant that makes the two halves complementary rather than
    redundant.

    `compute_medians` builds each row from `statistics.median(_clean(...))` and
    skips the row when fewer than `min_peers` readings survive. Since `_clean`
    admits only `[0.005, 0.25]`, any median it produces is inside that interval
    — so a stored `fcf_yield` after the next weekly refresh is either a valid
    benchmark or missing. The 297 non-positive medians on production today were
    computed under the old band and cannot be recomputed into existence again.
    """
    syms = [f"P{i}" for i in range(8)]
    metrics = {s: {"fcf_yield": v} for s, v in zip(syms, [
        -0.30, -0.10, -0.003152, 0.0, 0.02, 0.03, 0.04, 0.40])}
    rows = compute_medians(_basket(syms), metrics, "industry", min_peers=3)
    fcf = [r for r in rows if r["field"] == "fcf_yield"]
    assert len(fcf) == 1
    assert MIN_VALID_FCF_YIELD <= fcf[0]["value"] <= MAX_VALID_FCF_YIELD
    # median of [0.02, 0.03, 0.04] — the four invalid readings are gone, not
    # clipped, and the surviving median is 0.03 rather than the old -0.0016.
    assert fcf[0]["value"] == pytest.approx(0.03)
    assert fcf[0]["peer_count"] == 3


def test_a_cohort_whose_peers_are_all_cash_burners_stores_no_row():
    """And therefore presents to the leg as a MISSING key, not a bad one."""
    syms = [f"P{i}" for i in range(6)]
    metrics = {s: {"fcf_yield": v} for s, v in zip(syms, [
        -0.30, -0.20, -0.10, -0.05, SCHW_US_CAPITAL_MARKETS, 0.0])}
    rows = compute_medians(_basket(syms), metrics, "industry", min_peers=3)
    assert [r for r in rows if r["field"] == "fcf_yield"] == []


def test_the_band_change_cannot_affect_a_replay():
    """`_clean` has exactly one call site and it is on the write path.

    This is why the golden diff for the band half is structurally empty rather
    than empirically lucky: `load_comps` returns stored rows and never
    re-filters them, so a replay reads medians computed under whichever band
    was live when the fixture's comps were last refreshed.
    """
    src = inspect.getsource(rc)
    code = "\n".join(ln for ln in src.splitlines()
                     if not ln.lstrip().startswith("#"))
    calls = [i for i, ln in enumerate(code.splitlines())
             if "_clean(" in ln and "def _clean" not in ln]
    assert len(calls) == 1, calls
    # ...and that one call is inside compute_medians, not load_comps.
    assert code.index("def compute_medians") < code.index("_clean(field,") \
        < code.index("def save_comps")
    assert "_clean(" not in inspect.getsource(rc.load_comps)


# ── The leg ─────────────────────────────────────────────────────────────────

def test_the_silent_floor_is_gone():
    code = _leg_source()
    assert "max(target_yield" not in code
    # Nor re-typed as a literal: the point is that nothing substitutes a
    # synthetic yield for an invalid one any more.
    assert "0.01" not in code
    assert "return None" in code
    assert "target_yield <= MIN_VALID_FCF_YIELD" in code


def test_a_negative_peer_median_drops_the_leg_instead_of_capitalising_100x():
    """SCHW's cohort, quantified.

    Under the old floor, -0.003152 became 0.01 and the leg published
    10.0 / 0.01 = **$1,000.00** per share against $10.00 of FCF per share — a
    100× capitalisation, and the exact figure the `max()` existed to produce.
    """
    peer = {"fcf_yield": SCHW_US_CAPITAL_MARKETS, "pe": 14.0, "pb": 1.2}
    assert _call(peer) is None
    # what the deleted line would have published
    assert _FCF_PER_SHARE / _OLD_SILENT_FLOOR == pytest.approx(1000.0)
    assert _FCF_PER_SHARE / max(SCHW_US_CAPITAL_MARKETS, _OLD_SILENT_FLOOR) \
        == pytest.approx(1000.0)


def test_a_zero_peer_median_drops_the_leg():
    """Zero divided into a value is not a small number, it is undefined.

    The old `max()` mapped it to the same 100× as a negative did, so the two
    cases were indistinguishable on the card.
    """
    assert _call({"fcf_yield": 0.0}) is None


@pytest.mark.parametrize("peer_yield,expected", [
    (0.005, None),                                  # boundary: <= rejects
    (0.0050001, _FCF_PER_SHARE / 0.0050001),        # just above: valued
    (0.03, _FCF_PER_SHARE / 0.03),
    (0.25, _FCF_PER_SHARE / 0.25),
])
def test_the_rejection_boundary_is_exclusive_at_the_floor(peer_yield, expected):
    got = _call({"fcf_yield": peer_yield})
    if expected is None:
        assert got is None
    else:
        assert got == pytest.approx(expected, rel=1e-9)


def test_the_check_is_on_the_divided_target_not_the_raw_median():
    """The owner's line replaced the `max()`, which sat AFTER the division.

    That placement is load-bearing: the divisor `sm * growth_premium` reaches
    1.25 × 1.80 = 2.25, so a median that is itself valid can still produce an
    absurd required yield. A peer table saying 0.6% and a bull scenario
    demanding a further 2.25× less is a 450× capitalisation, and rejecting it
    is the same decision as rejecting the negative one.
    """
    # 0.006 is above the band floor, so the READING is valid...
    assert MIN_VALID_FCF_YIELD < 0.006
    # ...but 0.006 / (1.25 * 1.80) = 0.002667 is not a valid target yield.
    assert 0.006 / (1.25 * 1.80) < MIN_VALID_FCF_YIELD
    assert _call({"fcf_yield": 0.006}, scenario="bull", growth_premium=1.80) is None
    # The same reading with no scenario or premium adjustment is fine.
    assert _call({"fcf_yield": 0.006}) == pytest.approx(
        _FCF_PER_SHARE / 0.006, rel=1e-9)


def test_a_band_valid_median_can_survive_in_bear_and_drop_in_bull():
    """The interaction, stated as a behaviour rather than left to surprise.

    `sm` is 0.75 in bear and 1.25 in bull, and it is in the DENOMINATOR, so a
    higher-conviction scenario demands a LOWER yield and therefore tests the
    floor harder. At exactly the band's lower edge the leg exists for bear and
    vanishes for base and bull. That is deliberate — a bull case capitalising
    $10.00 of FCF at 0.4% is a 250× multiple — but it does mean method
    availability is scenario-dependent, so the three scenarios can carry
    different `methods_used` lists for a reason that has nothing to do with the
    company.
    """
    peer = {"fcf_yield": MIN_VALID_FCF_YIELD}
    assert _call(peer, scenario="bear") == pytest.approx(
        _FCF_PER_SHARE / (MIN_VALID_FCF_YIELD / 0.75), rel=1e-9)
    assert _call(peer, scenario="base") is None
    assert _call(peer, scenario="bull") is None


def test_the_missing_key_default_survives_the_new_check():
    """`peer.get("fcf_yield", 0.05)` is a missing-key fallback, not a reading.

    A peer table that never collected an FCF yield should still value the leg;
    a peer table that collected one and got a non-benchmark should not. The two
    cases must stay distinguishable, and the check placed after the `.get()`
    keeps them so, because 0.05 > 0.005.

    This is also the graceful path out of
    `test_a_cohort_whose_peers_are_all_cash_burners_stores_no_row`: when the
    band empties a cohort, no row is stored, the key is absent, and the leg
    falls back to 5% rather than disappearing. Whether that is the right answer
    for a cohort of pure cash burners is a judgement the owner has not made —
    the alternative, dropping the leg whenever the key is absent, would also
    remove it from every cohort that simply never collected the field.
    """
    assert _call({}) == pytest.approx(_FCF_PER_SHARE / 0.05, rel=1e-9)
    assert _call({"pe": 14.0, "pb": 1.2}) == pytest.approx(200.0, rel=1e-9)


@pytest.mark.parametrize("alias", ["P/CF", "Price/CF"])
def test_the_two_aliases_take_the_same_path(alias):
    """The dispatch set has three names; a fix applied to one is a fix to none."""
    assert _call({"fcf_yield": SCHW_US_CAPITAL_MARKETS}, method=alias) is None
    assert _call({"fcf_yield": 0.03}, method=alias) == pytest.approx(
        _FCF_PER_SHARE / 0.03, rel=1e-9)


def test_the_leg_still_returns_none_when_fcf_is_absent_or_non_positive():
    """Unchanged by this decision, and asserted so the new early return is not
    mistaken for the reason those cases stopped producing a value."""
    for row in ({**_ROW, "free_cash_flow": 0.0},
                {**_ROW, "free_cash_flow": -5e8},
                {k: v for k, v in _ROW.items() if k != "free_cash_flow"}):
        assert _call({"fcf_yield": 0.03}, row=row) is None


def test_owner_earnings_still_take_precedence_over_reported_fcf():
    row = {**_ROW, "fcf_owner_earnings": 1e9, "free_cash_flow": 2e9}
    assert _call({"fcf_yield": 0.05}, row=row) == pytest.approx(
        (1e9 / 200e6) / 0.05, rel=1e-9)


# ── The lazy import is load-bearing ─────────────────────────────────────────

def _module_level_imports(path: str) -> list[str]:
    """First-party module names imported at column 0 of one file."""
    out = []
    for ln in open(path, encoding="utf-8").read().splitlines():
        if not ln.startswith(("from src.", "import src.")):
            continue
        mod = ln[len("from "):].split(" import ")[0].strip() \
            if ln.startswith("from ") else ln[len("import "):].strip()
        out.append(mod)
    return out


def _fmp_get_by_value_binders() -> list[str]:
    """First-party modules that bind `_fmp_get` by value at module scope.

    These are the modules whose interception depends on WHEN they are first
    imported, because `from src.tools.api import _fmp_get` copies whatever the
    name points at that instant and the Replayer's attribute patch on
    `src.tools.api` cannot reach a copy already made.
    """
    out = []
    for root, dirs, files in os.walk(os.path.join(_REPO, "src")):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in files:
            if not f.endswith(".py"):
                continue
            p = os.path.join(root, f)
            rel = os.path.relpath(p, _REPO).replace(os.sep, ".")[:-3]
            for ln in open(p, encoding="utf-8").read().splitlines():
                if ln.startswith("from src.tools.api import") and "_fmp_get" in ln:
                    out.append(rel)
                    break
    return sorted(out)


def test_the_lazy_import_is_load_bearing():
    """`dcf_agent` must not import `regional_comps` at module scope.

    Not a style preference and not a cycle: `replay_fixture` imports
    `dcf_agent` at line 400, OUTSIDE the `with gc.Replayer(calls)` block that
    opens at line 409. Anything loaded by that import binds the real `_fmp_get`
    and its calls escape the recording, which surfaces as `unused` recorded
    calls and a message telling you to re-record a fixture that is fine.

    Measured, not theorised: hoisting this one import failed 14 of 17 golden
    tests. The import lives inside the leg instead, which is also where
    `sector_profiles` puts its three `regional_comps` imports.
    """
    dcf_path = os.path.join(_REPO, "src", "agents", "analysis", "dcf_agent.py")
    top = _module_level_imports(dcf_path)
    assert "src.data.regional_comps" not in top
    assert not any(m.endswith("regional_comps") for m in top)
    # And it IS imported, lazily, where it is used.
    leg_src = inspect.getsource(dcf_agent)
    i = leg_src.index('if method_name in {"FCF Yield", "P/CF", "Price/CF"}:')
    j = leg_src.index("# ── rNPV (Biopharma pipeline)", i)
    assert "from src.data.regional_comps import MIN_VALID_FCF_YIELD" in leg_src[i:j]


def test_no_by_value_fmp_binder_is_reachable_from_dcf_agents_module_scope():
    """The general rule, so the next hoist is caught by name rather than by a
    confusing `unused` assertion.

    `regional_comps` is one instance of a class: any module doing
    `from src.tools.api import _fmp_get` at module scope has an interception
    window, and importing it from `dcf_agent`'s module scope puts that window
    before the Replayer installs.
    """
    binders = _fmp_get_by_value_binders()
    assert "src.data.regional_comps" in binders, binders
    dcf_path = os.path.join(_REPO, "src", "agents", "analysis", "dcf_agent.py")
    top = set(_module_level_imports(dcf_path))
    overlap = top & set(binders)
    assert not overlap, (
        f"dcf_agent imports {sorted(overlap)} at module scope; these bind "
        f"_fmp_get by value and would load before golden replay's Replayer "
        f"installs, escaping the recording")


def test_the_replay_import_really_is_outside_the_replayer_block():
    """The premise of the two tests above, pinned in the harness itself.

    If `replay_fixture` is ever reordered to import inside the `with`, the
    constraint these tests enforce disappears and they should be deleted rather
    than kept as folklore.
    """
    src = inspect.getsource(
        __import__("src.memory.golden_replay", fromlist=["replay_fixture"]))
    imp = src.index("from src.agents.analysis.dcf_agent import run_dcf_agent")
    with_blk = src.index("with gc.pinned_env(), gc.Replayer(calls) as rp")
    assert imp < with_blk


# ── The golden baseline cannot see this defect ──────────────────────────────

def _snapshot() -> dict:
    with open(_SNAPSHOTS, encoding="utf-8") as fh:
        return json.load(fh)


def test_exactly_three_fixtures_dispatch_the_fcf_yield_leg():
    """And SCHW — the cohort the decision is about — is not one of them.

    This is why the golden diff for this change is empty and why that emptiness
    proves nothing about the fix. AAPL, COST and V carry the leg; SCHW's
    persisted methods are `['P/BV', 'P/E (norm)']`, so its -0.003152 peer
    median never reaches the leg in the baseline. The defect was production-only
    and stays production-only as far as the snapshot is concerned.
    """
    snap = _snapshot()
    with_leg, without = [], []
    for name, entry in snap.items():
        if name == "_meta" or not isinstance(entry, dict):
            continue
        proj = entry["projection"]
        keys = [k for k in proj if "method_iv_table.FCF Yield" in k
                or "method_iv_table.P/CF" in k
                or "method_iv_table.Price/CF" in k]
        (with_leg if keys else without).append(name)
    assert sorted(with_leg) == ["AAPL", "COST", "V"]
    assert "SCHW" in without
    assert len(with_leg) + len(without) == 14


def test_the_three_fixtures_target_yields_all_clear_the_new_floor():
    """The measured reason the diff is empty, pinned as a claim.

    Every one of the 12 leg calls the fixtures make lands in
    [0.014710, 0.046667] — the closest to the floor is COST's bull at 0.0147,
    three times above it. The old `max(·, 0.01)` never bound on any of them
    either, so this change removes a line that the baseline had already proven
    inert. Regenerating a snapshot here would produce a no-op diff and hide the
    fact that the fix is untested by the baseline.
    """
    snap = _snapshot()
    measured = {
        # fixture -> (peer fcf_yield, min target_yield, max target_yield)
        "AAPL": (0.035, 0.025437, 0.046667),
        "COST": (0.020, 0.014710, 0.026667),
        "V":    (0.030, 0.020973, 0.040000),
    }
    for fx, (peer_y, lo, hi) in measured.items():
        proj = snap[fx]["projection"]
        assert any("method_iv_table.FCF Yield" in k for k in proj), fx
        # every target yield the fixture can produce is above the floor
        assert lo > MIN_VALID_FCF_YIELD, fx
        assert hi > MIN_VALID_FCF_YIELD, fx
        # and each is reproducible from the peer median and the scenario grid
        for scen, sm in _SCENARIO_MULT.items():
            for gp in (1.0, 1.0670, 1.0685, 1.0877, 1.1007, 1.1391, 1.1443):
                assert peer_y / (sm * gp) > MIN_VALID_FCF_YIELD, (fx, scen, gp)


def test_the_published_fcf_yield_values_match_the_current_baseline():
    """Leg values as persisted, and the one reason the bear column moved.

    Written for decision 5 as ``..._are_unchanged``, pinning nine values that
    the FCF-Yield fix demonstrably did not move — the point being that the
    deleted ``max(target_yield, 0.01)`` never bound on this baseline, so the
    fix was untestable by it. That claim was true then and is not what this
    test asserts now.

    THE THREE BEAR VALUES HAVE SINCE MOVED, and the mover is not decision 5.
    The leg is ``(fcf / shares) / target_yield`` with
    ``target_yield = peer_fcf_yield / (sm * growth_premium)``, so the published
    value is LINEAR IN ``growth_premium``. The Gate B ``md_abs * 10`` fix
    (2026-09-17, ``tests/test_valuation_fixes_0917e.py``) deactivated Gate B on
    AAPL, COST and V in the bear scenario, which moved their bear premium off
    the forced 1.0 — and every multiple leg in those three scenarios moved
    with it, this one included. Base and bull are untouched here because AAPL,
    COST and V are not among the six fixtures whose BULL premium changed.

    So the pin is re-struck rather than deleted, and re-struck as a
    RELATIONSHIP instead of nine bare literals: bear must equal its pre-Gate-B
    value scaled by the premium the same fixture now publishes, and base/bull
    must be bit-identical to what decision 5 recorded. A future move that is
    not a premium move still fails. The ``rel=1e-3`` on the ratio is the
    persisted premium's own 3dp rounding — the leg uses the unrounded value, so
    1.03762 is stored as 1.038 — and is an order of magnitude tighter than the
    5e-3 the literal check uses.
    """
    snap = _snapshot()
    # decision 5's recorded values. `bear` entries are PRE-Gate-B, when the
    # premium was forced to exactly 1.0 and so scaled the leg by nothing.
    #
    # RE-STRUCK AGAIN 2026-09-20, and again the mover is not this leg. Every
    # per-share value in the engine now divides by the CURRENT share count
    # (market cap / price, scaled by the filing's diluted/basic ratio) instead
    # of the trailing weighted-average diluted count from the last annual
    # filing. The leg is linear in 1/shares, so each fixture moved by exactly
    # its own share ratio and by nothing else:
    #
    #     AAPL  x1.0216      COST  x1.0030      V  x1.0530
    #
    # The relationship this test exists to guard is untouched -- bear is still
    # its pre-Gate-B value times the published premium, to 3dp -- so only the
    # recorded literals are restated onto the new divisor.
    expected = {
        "AAPL": {"bear": 137.10, "base": 195.14, "bull": 251.62},
        "COST": {"bear": 635.95, "base": 905.75, "bull": 1152.55},
        "V":    {"bear": 284.55, "base": 432.04, "bull": 542.55},
    }
    for fx, per in expected.items():
        proj = snap[fx]["projection"]
        for scen, val in per.items():
            key = f"scenarios.{scen}.method_iv_table.FCF Yield"
            if scen == "bear":
                gp = proj["scenarios.bear.growth_premium"]
                assert gp > 1.0, (fx, "Gate B should have deactivated", gp)
                assert proj[key] == pytest.approx(val * gp, rel=1e-3), (
                    fx, scen, proj[key], val * gp)
            else:
                assert proj[key] == pytest.approx(val, rel=5e-3), (fx, scen, proj[key])


def test_schw_never_dispatches_the_leg_in_the_baseline():
    """Pinned separately from the count above because it is the load-bearing
    half of the empty-diff story: the cohort with the corrupt median is not in
    the basket's FCF-Yield path at all."""
    proj = _snapshot()["SCHW"]["projection"]
    for scen in ("bear", "base", "bull"):
        methods = proj[f"scenarios.{scen}.methods_used"]
        assert "FCF Yield" not in methods
        assert "P/CF" not in methods and "Price/CF" not in methods
        assert methods == ["P/BV", "P/E (norm)"]
