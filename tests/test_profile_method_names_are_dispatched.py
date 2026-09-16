"""Every implementable profile method name must be a literal the dispatch answers to.

`INDUSTRY_VALUATION_PROFILES` names a valuation method as a STRING, and
`_compute_method_value` matches that string against a series of literal sets::

    if method_name in _EV_MULTIPLE_METHODS: ...
    if method_name in {"EV/EBITDA (norm)", ...}: ...

There is no alias table, no normalisation and no `else: raise`. A name that
matches nothing runs the whole function and falls out of the trailing
`return None`. The caller then treats it as structurally unavailable and
`_blend_methods` renormalises over the survivors — so the method does not
appear broken, it appears ABSENT, and the valuation is quietly rebuilt out of
whatever was left.

Two names were in that state, both of them the ANCHOR of their profile:

    "EV/EBITDA (Norm)"      Steel / Metals    weight 0.50  anchor
    "EV/EBIT (Pre-bonus)"   Ad / Consulting   weight 0.40  anchor

Both carried `"implementable": True` and a prose `"note": "proxied by ..."`.
The note was the author's intent; the machine-readable `"proxy"` key — the one
`methods_to_compute` actually reads, and the one 32 other entries use — was
never set, and `implementable: True` means the proxy arm is not taken either.
So a steel company's anchor was valued on P/BV + FCF Yield + P/E alone, and an
ad agency's on FCF Yield + P/E + Rev DCF alone.

Nothing caught it. No ticker in the golden basket routes to Materials or
ProfessionalServices, and a unit test of `_compute_method_value` exercises the
spellings the tests were written against, not the spellings the profile table
contains. The only way to see it is to cross from the table into the dispatch,
which is what this file does. Seven further entries use the same prose-note
pattern and dispatch fine, because a branch set happens to list their exact
spelling — but four of those spellings resolve to the generic DCF projection
rather than the specialised economics their names promise, which is a smaller
version of the same authoring mistake. `test_known_label_overstatement_inventory`
pins that set so it changes only on purpose.
"""
from __future__ import annotations

import ast
import inspect
import io
import tokenize

import pytest

import src.agents.analysis.dcf_agent as dcf_agent
from src.agents.analysis.dcf_agent import (
    _compute_method_value,
    _EV_EBIT_METHODS,
    _EV_MULTIPLE_METHODS,
)
from src.data.sector_profiles import INDUSTRY_VALUATION_PROFILES


def _string_literals(source: str) -> frozenset[str]:
    """Every string literal in `source`, with comments excluded.

    A plain `name in source` substring scan would pass on a name mentioned
    only in a comment — and this file's subject is precisely a module where a
    method name was written down in prose while no branch answered to it.
    Tokenizing is the difference between "the author mentioned it" and "the
    code matches it".
    """
    out = set()
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type != tokenize.STRING:
            continue
        try:
            value = ast.literal_eval(tok.string)
        except (ValueError, SyntaxError):
            continue            # f-string or odd prefix; never a method name
        if isinstance(value, str):
            out.add(value)
    return frozenset(out)


#: The dispatch module's string literals. Method names are matched against
#: these, so a name absent from the set is a name no branch answers to.
_DISPATCH_LITERALS = _string_literals(inspect.getsource(dcf_agent))


# ── Enumeration ─────────────────────────────────────────────────────────────

def _profiles() -> list[tuple[str, str, dict]]:
    """(sector, profile_name, profile_dict) for every profile in the table."""
    out: list[tuple[str, str, dict]] = []

    def walk(node, path: tuple[str, ...]):
        if not isinstance(node, dict):
            return
        methods = node.get("methods")
        if isinstance(methods, list):
            sector = path[0] if path else "?"
            out.append((sector, path[-1] if path else "?", node))
            return
        for key, value in node.items():
            walk(value, path + (str(key),))

    walk(INDUSTRY_VALUATION_PROFILES, ())
    return out


def _entries() -> list[tuple[str, str, dict]]:
    """(sector, profile_name, method_entry) for every method entry."""
    out = []
    for sector, profile, node in _profiles():
        for m in node["methods"]:
            if isinstance(m, dict) and m.get("name"):
                out.append((sector, profile, m))
    return out


def _quoted(name: str) -> bool:
    """Is `name` a string literal somewhere in the dispatch module?

    A text scan of the whole module rather than an AST walk of
    `_compute_method_value`'s branch sets, because the DCF-family names are
    matched via module constants (`_DCF_PROJECTION_FAMILY`, `_DEPLETING_DCF`)
    assembled from literals defined elsewhere in the file. Scanning the module
    over-reports nothing that matters — a literal that exists but is never
    compared is still a spelling the author had in mind somewhere — whereas an
    AST walk of one function under-reports and would fail names that work.
    """
    return name in _DISPATCH_LITERALS


# ── The invariant ───────────────────────────────────────────────────────────

def test_every_implementable_method_name_is_a_dispatch_literal():
    missing = []
    for sector, profile, m in _entries():
        if not m.get("implementable", True):
            continue          # resolved to its proxy by the caller, never dispatched
        if not _quoted(m["name"]):
            missing.append(f"{sector} / {profile}: {m['name']!r} "
                           f"(weight {m.get('weight')}, anchor={bool(m.get('anchor'))})")
    assert not missing, (
        "these profile method names match no branch in _compute_method_value, "
        "so they return None and their weight is renormalised onto the "
        "survivors:\n  " + "\n  ".join(missing))


def test_every_proxy_target_is_a_dispatch_literal():
    """The `proxy` arm is the escape hatch for an unimplementable method, so a
    proxy that itself matches nothing leaves the profile with one fewer leg and
    no diagnostic at all."""
    missing = []
    for sector, profile, m in _entries():
        proxy = m.get("proxy")
        if proxy and not _quoted(proxy):
            missing.append(f"{sector} / {profile}: {m['name']!r} -> proxy {proxy!r}")
    assert not missing, "\n  ".join(missing)


def test_a_prose_proxy_note_is_not_a_mechanism():
    """The authoring mistake, named directly.

    `"note": "proxied by X"` is documentation; `"proxy": "X"` is code. An entry
    that says the first while claiming `implementable: True` is asserting that
    something resolves it, and nothing does. Either the name is itself a branch
    literal (the nine entries below now are) or the entry must set `proxy` and
    `implementable: False`. This asserts the former, so a tenth entry written
    the old way fails here with the reason attached rather than silently
    dropping an anchor.
    """
    offenders = []
    for sector, profile, m in _entries():
        note = str(m.get("note") or "")
        if "proxied by" in note and m.get("implementable", True) and "proxy" not in m:
            if not _quoted(m["name"]):
                offenders.append(f"{sector} / {profile}: {m['name']!r} — {note}")
    assert not offenders, (
        "these entries claim to be proxied in prose while being dispatched by "
        "name, and no branch answers to the name:\n  " + "\n  ".join(offenders))


# ── Guard the guard ─────────────────────────────────────────────────────────

def test_the_enumeration_actually_found_the_table():
    """A walk that silently finds nothing passes every test above."""
    profiles = _profiles()
    entries = _entries()
    implementable = [e for e in entries if e[2].get("implementable", True)]
    assert len(profiles) >= 90, len(profiles)
    assert len(entries) >= 250, len(entries)
    assert len(implementable) >= 55, len(implementable)
    # The two profiles this file exists because of must be in the enumeration.
    found = {(s, p) for s, p, _ in profiles}
    assert ("Materials", "Steel / Metals") in found
    assert ("ProfessionalServices", "Ad / Consulting") in found


def test_the_literal_scan_counts_code_and_not_comments():
    """The predicate's teeth.

    Both fixed names are discussed in comments in dcf_agent.py as well as
    listed in branch sets. A substring scan cannot tell those apart, so
    deleting a name from a branch set while leaving the comment behind would
    pass it — the exact failure mode this file exists to prevent, wearing a
    different hat.
    """
    src = (
        'def f(method_name):\n'
        '    # "Mentioned Only In A Comment" is documented here\n'
        '    if method_name in {"A Real Branch Literal"}:\n'
        '        return 1\n'
    )
    lits = _string_literals(src)
    assert "A Real Branch Literal" in lits
    assert "Mentioned Only In A Comment" not in lits

    # Positive and negative controls against the real module.
    assert "EV/EBITDA (Norm)" in _DISPATCH_LITERALS
    assert "EV/EBIT (Pre-bonus)" in _DISPATCH_LITERALS
    assert "EV/EBITDA (Normm)" not in _DISPATCH_LITERALS
    assert len(_DISPATCH_LITERALS) > 500, len(_DISPATCH_LITERALS)


# ── Behaviour: the two fixed spellings ──────────────────────────────────────

_ROW = {
    "revenue": 20e9, "ebitda": 4e9, "ebit": 2.5e9, "net_income": 2e9,
    "total_equity": 10e9, "total_assets": 18e9,
    "normalized_ebitda": 3e9, "normalized_net_income": 1.5e9,
    "cash": 1e9, "total_debt": 2e9, "net_debt": 1e9,
    "book_value_per_share": 50.0, "shares_outstanding": 200e6,
    # The DCF-family branches project from cash flow, so a row without these
    # returns None and the proxy-equivalence tests below would compare None
    # to None — which passes, and proves nothing.
    "operating_cash_flow": 3e9, "capital_expenditure": -1e9,
    "free_cash_flow": 2e9,
}

_PEER = {"ev_ebitda": 8.0, "ev_revenue": 1.5, "pe": 14.0, "pb": 1.2,
         "fcf_yield": 0.06}


def _call(method_name: str, *, sector: str, profile: str, scenario: str = "base"):
    return _compute_method_value(
        method_name=method_name,
        most_recent=dict(_ROW),
        revenue_base=_ROW["revenue"],
        shares=200e6,
        net_debt=_ROW["net_debt"],
        market_cap=10e9,
        wacc=0.09,
        growth_base=0.03,
        fcf_margin_base=0.10,
        tgr=0.025,
        fcf_floor=0.0,
        sector=sector,
        scenario=scenario,
        reported_currency="USD",
        is_hk=False,
        growth_premium=1.0,
        sbc_pe_discount=1.0,
        profile_name=profile,
        ticker="",
        end_date="2026-09-17",
    )


@pytest.fixture(autouse=True)
def _fixed_peer(monkeypatch):
    """Pin the peer multiples so these tests assert dispatch, not feed state."""
    monkeypatch.setattr(dcf_agent, "get_sector_peer_multiples",
                        lambda *a, **k: dict(_PEER))


def test_steels_capital_n_anchor_computes_a_value():
    got = _call("EV/EBITDA (Norm)", sector="Materials", profile="Steel / Metals")
    assert got is not None and got > 0, (
        "Steel / Metals' 0.50-weight anchor returned None; the blend would "
        "renormalise onto P/BV + FCF Yield + P/E")


def test_steels_anchor_is_the_normalised_leg_not_the_peak_leg():
    """The profile's rationale is 'Normalised mid-cycle EBITDA smooths
    commodity price volatility'. Routing the capital-N spelling to the plain
    EV/EBITDA branch would satisfy the coverage test above and still hand a
    steel company its peak-year EBITDA — the defect Phase 1.2B removes."""
    norm = _call("EV/EBITDA (Norm)", sector="Materials", profile="Steel / Metals")
    assert norm == pytest.approx(
        _call("EV/EBITDA (norm)", sector="Materials", profile="Steel / Metals"))
    peak = _call("EV/EBITDA", sector="Materials", profile="Steel / Metals")
    # normalized_ebitda (3e9) < ebitda (4e9) in the fixture row, so the
    # mid-cycle leg must come out below the trailing one.
    assert norm < peak


def test_the_pre_bonus_spelling_is_an_ebit_method_not_an_ebitda_one():
    """The branch discriminates EBIT from EBITDA on the method name. Adding a
    new spelling to the set without adding it to `_EV_EBIT_METHODS` routes it
    to EBITDA — a 1.6x error on the fixture row, in the direction that
    overvalues."""
    got = _call("EV/EBIT (Pre-bonus)", sector="ProfessionalServices",
                profile="Ad / Consulting")
    assert got is not None and got > 0
    assert got == pytest.approx(
        _call("EV/EBIT", sector="ProfessionalServices", profile="Ad / Consulting"))
    assert got != pytest.approx(
        _call("EV/EBITDA", sector="ProfessionalServices", profile="Ad / Consulting"))


def test_the_ebit_discrimination_is_set_membership_not_a_string_compare():
    """Pinned because `metric = ebitda if method_name != "EV/EBIT" else ebit`
    is what made the second spelling an EBITDA method the moment it joined the
    branch set. Any EBIT spelling must be in both sets."""
    assert _EV_EBIT_METHODS <= _EV_MULTIPLE_METHODS
    assert "EV/EBIT" in _EV_EBIT_METHODS
    assert "EV/EBIT (Pre-bonus)" in _EV_EBIT_METHODS
    src = inspect.getsource(_compute_method_value)
    assert 'method_name != "EV/EBIT"' not in src, (
        "the EBIT/EBITDA discrimination went back to a single-string compare; "
        "the next EBIT spelling added to the branch set becomes an EBITDA method")


def test_both_fixed_names_reach_the_branch_set_they_claim():
    assert "EV/EBIT (Pre-bonus)" in _EV_MULTIPLE_METHODS
    assert "EV/EBITDA (Norm)" in {
        "EV/EBITDA (norm)", "EV/EBITDA (Norm)",
        "EV/EBITDA norm", "Normalized EV/EBITDA"}


# ── The remaining prose-note entries: honest, and pinned as honest ──────────

#: Profile names whose method label promises a specialised model but whose
#: entry says in plain terms what it actually computes. Unlike the two fixed
#: above, these notes are not contradicted by their own profile's rationale,
#: and the numbers agree with the proxy named. Pinned so that implementing a
#: real PPA-backed or backlog DCF — which would change the 0.50-weight ANCHOR
#: of Energy / IPP and Energy / EPC Contractor — fails here and forces the
#: note to be updated in the same commit, rather than leaving a stale claim
#: that the profile documents a proxy.
_PROXY_EQUIVALENCES = {
    "Power Price DCF":  ("DCF",       "Energy",       "Merchant Power"),
    "PPA-backed DCF":   ("DCF",       "Energy",       "IPP"),
    "Backlog DCF":      ("DCF",       "Energy",       "EPC Contractor"),
    "Unit Econ DCF":    ("DCF",       "Tech",         "Early Platform"),
    "EV/EBITDAR":       ("EV/EBITDA", "Transportation", "Airlines"),
}


@pytest.mark.parametrize("name", sorted(_PROXY_EQUIVALENCES))
def test_a_documented_proxy_still_computes_what_its_note_says(name):
    proxy, sector, profile = _PROXY_EQUIVALENCES[name]
    got = _call(name, sector=sector, profile=profile)
    want = _call(proxy, sector=sector, profile=profile)
    assert got is not None, f"{name!r} returned None; its weight would be renormalised"
    assert got == pytest.approx(want), (
        f"{name!r} no longer equals {proxy!r}. If a genuine {name} was just "
        f"implemented, update the profile note in sector_profiles.py — it "
        f"still claims the value is proxied by {proxy}.")


def test_the_proxy_inventory_matches_the_table():
    """If someone writes a new `"note": "proxied by X"` entry, it belongs in
    `_PROXY_EQUIVALENCES` above — otherwise its claim is untested."""
    live = {}
    for sector, profile, m in _entries():
        note = str(m.get("note") or "")
        if ("proxied by" in note and m.get("implementable", True)
                and "proxy" not in m):
            live.setdefault(m["name"], []).append(f"{sector} / {profile}")
    assert set(live) == set(_PROXY_EQUIVALENCES), (
        f"prose-proxy names in the table but not pinned: "
        f"{sorted(set(live) - set(_PROXY_EQUIVALENCES))}; "
        f"pinned but no longer in the table: "
        f"{sorted(set(_PROXY_EQUIVALENCES) - set(live))}")


def test_which_profiles_put_their_anchor_on_a_proxied_name():
    """Sizes the exposure; not a defect the notes hide.

    Two classes, and they are not equally serious. The DCF ones are
    substantive: Energy / IPP and Energy / EPC Contractor each put half their
    weight on an anchor that is a generic DCF wearing a specialised name, so a
    contracted-cash-flow IPP and an order-book-driven contractor are both
    discounted and terminated as if they were an ordinary industrial. The
    EBITDAR ones are labelling only: `EV/EBITDAR` applies an EV/EBITDA
    multiple to EBITDA, which is internally consistent — the rent adjustment
    the name promises is simply absent, and the number is a legitimate
    EV/EBITDA. `Consumer / Traditional Retail` carries that name as its anchor
    without a prose note at all, which is why this test counts names rather
    than notes.
    """
    dcf_anchored, ebitdar_anchored = [], []
    for sector, profile, m in _entries():
        if not m.get("anchor") or m["name"] not in _PROXY_EQUIVALENCES:
            continue
        where = f"{sector} / {profile}: {m['name']} w={m.get('weight')}"
        (dcf_anchored if _PROXY_EQUIVALENCES[m["name"]][0] == "DCF"
         else ebitdar_anchored).append(where)
    assert len(dcf_anchored) == 2, dcf_anchored
    assert len(ebitdar_anchored) == 2, ebitdar_anchored
