"""The industry -> profile routing table.

Every row must resolve, and no Singapore calibration may reach another market.
"""
import pytest

from src.data.industry_profile_map import industry_map, profile_for_industry
from src.data.sector_profiles import INDUSTRY_VALUATION_PROFILES as P


def test_every_row_resolves_to_a_real_profile():
    """A typo falls through SILENTLY to the financial ladder -- which is the
    exact failure this table exists to remove."""
    bad = [(i, s, p) for i, (s, p) in industry_map().items()
           if not P.get(s, {}).get(p)]
    assert not bad, f"unresolvable rows: {bad}"


def test_no_singapore_calibration_leaks_into_the_table():
    """(SG) profiles are Singapore calibrations.

    The DBS incident was this in reverse: with no deterministic profile, DBS
    routed to the US Money Center Bank row (ROE 12% / CoE 9.0% / P/TBV 1.4x)
    instead of the Singapore one. Routing a HK listing to an (SG) profile is
    the same error with the markets swapped.
    """
    leaks = [(i, p) for i, (_s, p) in industry_map().items() if "(SG)" in p]
    assert not leaks, f"SG calibrations in a cross-market table: {leaks}"


def test_unmapped_industry_returns_none_rather_than_guessing():
    assert profile_for_industry("Nonexistent Industry") is None
    assert profile_for_industry(None) is None
    assert profile_for_industry("") is None


def test_known_rows_route_where_a_practitioner_would():
    """Spot-checks on the names the ladder got wrong."""
    assert profile_for_industry("Auto - Manufacturers") == ("Consumer", "Automotive & EV")
    assert profile_for_industry("Gold") == ("Resources", "Mining (Major)")
    assert profile_for_industry("Insurance - Life") == ("Financials", "Insurance")
    assert profile_for_industry("Financial - Data & Stock Exchanges") == (
        "Financials", "Market Infrastructure")
    assert profile_for_industry("Banks - Regional") == ("Financials", "EM Bank")


def test_industry_labels_are_stored_trimmed():
    for ind in industry_map():
        assert ind == ind.strip() and ind, repr(ind)


class TestTelcoProfile:
    """Telcos are priced on EV/EBITDA and the distribution.

    `Telco / Stable Growth` previously anchored on EPV with NO EV/EBITDA and
    NO DDM anywhere in its method set -- an earnings-power FLOOR presented as
    the primary estimate for a capital-intensive cash machine. Every telco in
    every market routed through it.
    """

    def _methods(self):
        return {m["name"]: m for m in P["Telco"]["Stable Growth"]["methods"]}

    def test_anchored_on_ev_ebitda(self):
        m = self._methods()
        assert m["EV/EBITDA"]["anchor"] is True
        assert m["EV/EBITDA"]["implementable"] is True

    def test_the_distribution_is_valued(self):
        m = self._methods()
        assert "DDM" in m and m["DDM"]["implementable"] is True

    def test_epv_is_retained_as_a_floor_not_the_anchor(self):
        m = self._methods()
        assert m["EPV"]["anchor"] is False
        assert m["EPV"]["weight"] < m["EV/EBITDA"]["weight"]

    def test_weights_sum_to_one(self):
        assert round(sum(m["weight"] for m in
                         P["Telco"]["Stable Growth"]["methods"]), 6) == 1.0


def test_resources_profiles_are_no_longer_proxied():
    """This test previously asserted the OPPOSITE, deliberately.

    It pinned `Mining (Major)` at 80% proxied and `Upstream Oil & Gas` at 90%
    -- NAV (LoM) and NAV (PV-10) resolving to a generic corporate DCF, P/NAV
    to P/BV -- so that implementing them would have to be a deliberate act
    rather than a silent one. That act has now happened: both are re-anchored
    on computable normalised multiples with a finite-horizon depleting-asset
    DCF, and the reserve-model omission is stated in `data_limitation` instead
    of being papered over with a label.

    See tests/test_resources_valuation_policy.py for the policy itself.
    """
    for prof in ("Mining (Major)", "Upstream Oil & Gas"):
        ms = P["Resources"][prof]["methods"]
        proxied = sum(m["weight"] for m in ms if not m.get("implementable"))
        assert proxied == 0.0, f"{prof} is {proxied:.0%} proxied again"
        assert P["Resources"][prof].get("data_limitation"), prof
