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


class TestMarketAwareRouting:
    """The same industry must route differently per market where the market
    has its OWN calibration. Singapore does; Hong Kong does not."""

    def test_a_singapore_trust_gets_the_s_reit_calibration(self):
        from src.data.industry_profile_map import profile_for_ticker
        assert profile_for_ticker("A17U.SI", "REIT - Industrial") == (
            "RealEstate", "S-REIT")

    def test_a_hong_kong_trust_does_not(self):
        from src.data.industry_profile_map import profile_for_ticker
        assert profile_for_ticker("00823.HK", "REIT - Retail") == (
            "RealEstate", "REIT")

    def test_the_same_bank_industry_splits_by_market(self):
        """The DBS incident in both directions: DBS must not get the US
        money-center row, and ICBC must not get the Singapore one."""
        from src.data.industry_profile_map import profile_for_ticker
        assert profile_for_ticker("D05.SI", "Banks - Regional") == (
            "Financials", "Money Center Bank (SG)")
        assert profile_for_ticker("01398.HK", "Banks - Regional") == (
            "Financials", "EM Bank")

    def test_market_is_read_from_the_suffix(self):
        from src.data.industry_profile_map import market_of
        assert market_of("D05.SI") == "SG"
        assert market_of("00700.HK") == "HK"
        assert market_of("AAPL") == "US"

    def test_every_market_row_resolves(self):
        from src.data.industry_profile_map import market_map
        for market in ("SG",):
            bad = [(i, s, p) for i, (s, p) in market_map(market).items()
                   if not P.get(s, {}).get(p)]
            assert not bad, f"{market}: {bad}"

    def test_sg_rows_may_use_sg_calibrations(self):
        """The (SG) ban applies to the DEFAULT table, not to the SG table --
        that is the whole point of having one."""
        from src.data.industry_profile_map import market_map
        assert any("(SG)" in p for _s, p in market_map("SG").values())


class TestTickerOverrides:
    def test_every_override_resolves(self):
        from src.data.industry_profile_map import ticker_overrides
        bad = [(t, s, p) for t, (s, p) in ticker_overrides().items()
               if not P.get(s, {}).get(p)]
        assert not bad, f"unresolvable overrides: {bad}"

    def test_an_override_beats_the_industry_row(self):
        """FMP's "Real Estate - Services" lumps a prime-retail landlord in with
        a brokerage platform and a property manager."""
        from src.data.industry_profile_map import profile_for_ticker
        assert profile_for_ticker("01997.HK", "Real Estate - Services") == (
            "RealEstate", "REIT")
        assert profile_for_ticker("02423.HK", "Real Estate - Services") == (
            "ProfessionalServices", "Ad / Consulting")

    def test_overrides_are_stored_canonicalised(self):
        from src.tools.ticker_canonical import canonical_ticker
        from src.data.industry_profile_map import ticker_overrides
        for t in ticker_overrides():
            assert t == canonical_ticker(t), t


class TestIndustryRoutingWiring:
    """Routing is behind its OWN flag, FEATURE_INDUSTRY_ROUTING, default OFF."""

    def test_default_off(self, monkeypatch):
        from src.agents.analysis.dcf_agent import _industry_routing_enabled
        monkeypatch.delenv("FEATURE_INDUSTRY_ROUTING", raising=False)
        assert _industry_routing_enabled() is False

    def test_turns_on(self, monkeypatch):
        from src.agents.analysis.dcf_agent import _industry_routing_enabled
        monkeypatch.setenv("FEATURE_INDUSTRY_ROUTING", "true")
        assert _industry_routing_enabled() is True

    def test_the_holdco_flag_does_not_switch_routing_on(self, monkeypatch):
        """2026-09-15: the look-through flag also enabled routing, and 09988.HK
        was re-profiled as Traditional Retail in production."""
        from src.agents.analysis.dcf_agent import _industry_routing_enabled
        monkeypatch.delenv("FEATURE_INDUSTRY_ROUTING", raising=False)
        monkeypatch.setenv("FEATURE_RESOURCE_HOLDCO_MAP_V2", "true")
        assert _industry_routing_enabled() is False

    def test_unmapped_industry_returns_none_not_a_guess(self, monkeypatch):
        """An unknown industry must fall through to the existing classifier
        VISIBLY, not be assigned a neighbouring row."""
        from src.agents.analysis import dcf_agent as d
        monkeypatch.setattr("src.tools.api.get_company_industry",
                            lambda t, api_key=None: "Nonexistent Industry")
        assert d._industry_routed_profile("ZZZZ", "Tech") is None

    def test_a_mapped_industry_returns_profile_data(self, monkeypatch):
        from src.agents.analysis import dcf_agent as d
        monkeypatch.setattr("src.tools.api.get_company_industry",
                            lambda t, api_key=None: "Gold")
        got = d._industry_routed_profile("02259.HK", "Tech")
        assert got is not None
        sector, profile, data = got
        assert (sector, profile) == ("Resources", "Mining (Major)")
        assert data and data.get("methods")

    def test_a_lookup_failure_does_not_abort_the_run(self, monkeypatch):
        from src.agents.analysis import dcf_agent as d

        def _boom(t, api_key=None):
            raise RuntimeError("FMP down")
        monkeypatch.setattr("src.tools.api.get_company_industry", _boom)
        assert d._industry_routed_profile("AAPL", "Tech") is None


class TestInsurancePandC:
    """Embedded Value is a LIFE concept.

    It discounts an in-force book of long-duration policies. A general
    insurer writes one-year contracts and has no in-force value to discount,
    so routing P&C through the life profile anchored PICC on a method that
    does not exist for it.
    """

    def _pc(self):
        return P["Financials"]["Insurance (P&C)"]

    def test_anchored_on_book_value_against_return(self):
        ms = {m["name"]: m for m in self._pc()["methods"]}
        assert ms["GGM (P/B)"]["anchor"] is True
        assert ms["GGM (P/B)"]["implementable"] is True

    def test_underwriting_quality_is_weighted(self):
        ms = {m["name"]: m for m in self._pc()["methods"]}
        assert ms["Combined Ratio Gate"]["weight"] >= 0.20

    def test_embedded_value_is_excluded_not_merely_unweighted(self):
        """Its presence in the method set invited the wrong anchor."""
        names = {m["name"] for m in self._pc()["methods"]}
        assert "Embedded Value" not in names
        assert "Embedded Value" in self._pc()["excluded"]

    def test_weights_sum_to_one(self):
        assert round(sum(m["weight"] for m in self._pc()["methods"]), 6) == 1.0

    def test_pc_and_life_route_apart(self):
        from src.data.industry_profile_map import profile_for_ticker
        assert profile_for_ticker("02328.HK", "Insurance - Property & Casualty") == (
            "Financials", "Insurance (P&C)")
        assert profile_for_ticker("01299.HK", "Insurance - Life") == (
            "Financials", "Insurance")

    def test_the_whole_industry_routes_not_just_one_ticker(self):
        """Every P&C insurer needs this, not only the one that surfaced it."""
        from src.data.industry_profile_map import industry_map, market_map
        assert industry_map()["Insurance - Property & Casualty"] == (
            "Financials", "Insurance (P&C)")
        assert market_map("SG")["Insurance - Property & Casualty"] == (
            "Financials", "Insurance (P&C)")


class TestAnchorImplementabilityGuard:
    """Routing must not send a name into a profile it cannot compute.

    The anchor carries the largest weight, so routing to a profile whose
    anchor is `implementable: False` swaps a possibly-wrong number for a proxy
    standing in for the very method that was meant to be the improvement.
    Measured against consensus this was the worst effect of routing: every
    holding company regressed, because Holding Company anchors `SOTP / NAV` at
    0.70 with implementable False -- CITIC 71.5% -> 1310.1%.
    """

    def test_declines_a_profile_whose_anchor_is_not_implementable(self, monkeypatch):
        from src.agents.analysis import dcf_agent as d
        monkeypatch.setattr("src.tools.api.get_company_industry",
                            lambda t, api_key=None: "Conglomerates")
        # Conglomerates -> Financials/Holding Company, anchor SOTP / NAV
        assert P["Financials"]["Holding Company"]["methods"][0]["name"] == "SOTP / NAV"
        assert not P["Financials"]["Holding Company"]["methods"][0]["implementable"]
        assert d._industry_routed_profile("00267.HK", "Financials") is None

    def test_still_routes_where_the_anchor_computes(self, monkeypatch):
        from src.agents.analysis import dcf_agent as d
        monkeypatch.setattr("src.tools.api.get_company_industry",
                            lambda t, api_key=None: "Gold")
        got = d._industry_routed_profile("02259.HK", "Materials")
        assert got and got[1] == "Mining (Major)"


class TestPlatformsMislabelledAsRetail:
    """EV/EBITDAR is a LEASE-ADJUSTED metric for a shop estate.

    FMP's "Specialty Retail" holds Meituan, SHEIN and a semiconductor test
    company. None of them pays rent on a retail estate, which is all that
    Traditional Retail's anchor measures -- it valued Meituan at 290.4 against
    a 110.5 consensus target.
    """

    def test_the_industry_row_still_serves_real_retailers(self):
        from src.data.industry_profile_map import industry_map
        assert industry_map()["Specialty Retail"] == ("Consumer", "Traditional Retail")

    def test_the_mislabelled_platforms_are_overridden(self):
        from src.data.industry_profile_map import profile_for_ticker
        # Meituan later normalised again, from the generic hyperscaler row to
        # the taxonomy that matches its own reporting lines. What this test
        # guards is that it is not a RETAILER -- the anchor Traditional Retail
        # applies is rent on a retail estate, which Meituan does not pay.
        assert profile_for_ticker("03690.HK", "Specialty Retail") == (
            "Tech", "Local Services & Instant Retail")
        assert profile_for_ticker("00625.HK", "Specialty Retail") == (
            "Consumer", "Consumer Growth")
        assert profile_for_ticker("AWI.SI", "Specialty Retail") == (
            "Tech", "Tech Manufacturing / EMS (SG)")


class TestFootwearLabelWasUnmapped:
    """FMP's "Apparel - Footwear & Accessories" was absent from the table.

    Measured live on 2026-09-18 across the 16-name Consumer Discretionary
    basket: NKE, ONON, DECK, CPRI and 02313.HK all carry that label, all five
    returned route=None, and `_industry_routed_profile` recorded
    outcome='unmapped' and declined rather than guessing. Declining is the
    right behaviour for an industry the table has never seen and the wrong
    outcome for one it should have covered: all five fell through to the LLM
    router, so the same filing could land on different profiles on different
    runs, and a profile that cannot deliver its own recommended method arrives
    with nothing in the log saying so.

    The ruling that added the row named the target as "Consumer/Apparel &
    Footwear". No such profile exists -- Consumer has 13 and none is called
    that -- so the row uses "Apparel / Athletic Wear", which is the pair
    "Apparel - Manufacturers" already maps to and the profile NKE, LULU and
    02020.HK are pinned to. Pointing it at Consumer Growth instead would have
    Guardrail 4a route those three one way and the ticker override pull them
    straight back, the internal contradiction this file's v3 note warns
    against.
    """

    LABEL = "Apparel - Footwear & Accessories"
    NAMES = ("NKE", "ONON", "DECK", "CPRI", "02313.HK")

    def test_the_label_routes_to_the_apparel_profile(self):
        assert profile_for_industry(self.LABEL) == (
            "Consumer", "Apparel / Athletic Wear")

    def test_the_names_measured_on_the_label_all_take_the_row(self):
        """None of the five has a ticker_overrides entry, so for four of them
        this row is the only deterministic route there is. ONON is the fifth
        and is decided elsewhere -- see the override test below."""
        from src.data.industry_profile_map import profile_for_ticker, ticker_overrides
        ovr = ticker_overrides()
        for t in self.NAMES:
            assert t not in ovr, f"{t} gained an override; this row no longer decides it"
            assert profile_for_ticker(t, self.LABEL) == (
                "Consumer", "Apparel / Athletic Wear"), t

    def test_the_routed_profile_can_deliver_a_dcf_leg(self):
        """The reason to route these five deterministically is that the profile
        can compute the method the practitioner spec asks for. Traditional
        Retail -- where the sibling "Apparel - Retail" label sends LULU and
        01368.HK -- has no DCF-family leg at all, which is the 01368.HK
        finding this row is the cheap half of."""
        prof = P["Consumer"]["Apparel / Athletic Wear"]
        names = {m["name"] for m in prof["methods"]}
        assert "DCF (FCF+)" in names, names
        # _industry_routed_profile declines any profile whose ANCHOR is not
        # implementable, so a row pointing at one would silently not route.
        anchor = next(m for m in prof["methods"] if m.get("anchor"))
        assert anchor.get("implementable"), anchor

    def test_the_row_does_not_fight_the_pins_it_agrees_with(self):
        """Three of the pinned Consumer names share this label. The row must
        land where their pins already do, or Guardrail 4a and the override
        disagree on every run and the trace reads as a flip-flop."""
        from src.data.sector_profiles import get_wacc_profile_for_ticker
        for t in ("NKE", "LULU", "02020.HK"):
            assert get_wacc_profile_for_ticker(t) == profile_for_industry(self.LABEL), t

    def test_onon_is_the_one_disagreement_and_the_override_wins(self):
        """ONON is pinned to Consumer Growth at ~30% CAGR; this row says
        Apparel / Athletic Wear. The ticker override sits BELOW industry
        routing in run_dcf_agent and wins, so the pin holds and the row is
        inert for ONON -- which is why adding the key is safe for it."""
        from src.data.sector_profiles import get_wacc_profile_for_ticker
        assert get_wacc_profile_for_ticker("ONON") == ("Consumer", "Consumer Growth")
        assert get_wacc_profile_for_ticker("ONON") != profile_for_industry(self.LABEL)

        import inspect
        from src.agents.analysis import dcf_agent as d
        src = inspect.getsource(d.run_dcf_agent)
        routed = '_routing_trace["winner"] = "industry_map"'
        override = '_routing_trace["winner"] = "ticker_override"'
        # Uniqueness measured before indexing: str.index() returns the FIRST
        # match, so a second occurrence anywhere would make the ordering
        # assertion silently measure the wrong one.
        assert src.count(routed) == 1, src.count(routed)
        assert src.count(override) == 1, src.count(override)
        assert src.index(routed) < src.index(override), (
            "the ticker override no longer runs after industry routing, so a "
            "routed profile would overwrite the pin")


class TestXtepIsNotAShopEstateRetailer:
    """FMP's "Apparel - Retail" sends 01368.HK to Traditional Retail.

    Traditional Retail anchors EV/EBITDAR, a LEASE-ADJUSTED metric for a shop
    estate, and its five legs contain no DCF-family method at all. Xtep is a
    branded sportswear manufacturer and retailer whose value is its cash flow,
    not its rents, and its own peer set agrees: 02020.HK and 02331.HK sit on
    Apparel / Athletic Wear and Consumer Growth.

    The override names the exception rather than re-pointing the row, which is
    the v3/v9 pattern. The row does serve genuine shop-estate retailers, the
    population it covers has not been measured, and moving all of it onto a DCF
    leg would be a regression risk taken without evidence.
    """

    LABEL = "Apparel - Retail"

    def test_the_row_still_serves_real_retailers(self):
        assert industry_map()[self.LABEL] == ("Consumer", "Traditional Retail")
        from src.data.industry_profile_map import profile_for_ticker
        # LULU carries this label and has no override, so the row still decides
        # it -- its pin lives in TICKER_SECTOR_LOOKUP, a different layer.
        assert profile_for_ticker("LULU", self.LABEL) == (
            "Consumer", "Traditional Retail")

    def test_xtep_overrides_to_the_apparel_profile(self):
        from src.data.industry_profile_map import profile_for_ticker
        assert profile_for_ticker("01368.HK", self.LABEL) == (
            "Consumer", "Apparel / Athletic Wear")

    def test_the_premise_the_override_rests_on(self):
        """Traditional Retail has no DCF-family leg. If that ever changes the
        override stops being necessary and this test says so, rather than
        leaving a stale exception behind."""
        names = [m["name"] for m in P["Consumer"]["Traditional Retail"]["methods"]]
        assert not any("DCF" in n for n in names), names
        # ...and the profile the override points at does have one.
        assert any("DCF" in m["name"]
                   for m in P["Consumer"]["Apparel / Athletic Wear"]["methods"])


class TestTencentIsTheExceptionInsideACorrectRow:
    """"Internet Content & Information" -> Mature Platform is RIGHT for
    Kuaishou (deviation 109.5% -> 24.7%) and for TME, and WRONG for Tencent
    (2.3% -> 32.8%). Gaming, fintech, cloud and a large investment portfolio
    are a conglomerate, not a single platform -- which is what Tencent's SOTP
    primary reflects. The row stays; the exception is named.
    """

    def test_the_row_is_unchanged(self):
        from src.data.industry_profile_map import industry_map
        assert industry_map()["Internet Content & Information"] == (
            "Tech", "Mature Platform")

    def test_peers_still_take_the_row(self):
        from src.data.industry_profile_map import profile_for_ticker
        for peer in ("01024.HK", "01698.HK"):
            assert profile_for_ticker(peer, "Internet Content & Information") == (
                "Tech", "Mature Platform"), peer

    def test_tencent_overrides_to_the_conglomerate_profile(self):
        from src.data.industry_profile_map import profile_for_ticker
        assert profile_for_ticker("00700.HK", "Internet Content & Information") == (
            "Tech", "Hyperscaler / Tech Conglomerate")


class TestRoutedSectorPropagates:
    """The sector must move with the profile.

    Peer-relative methods look their multiples up BY SECTOR. Adopting an
    Insurance profile while leaving sector="Tech" prices an insurer off
    software comparables, and that mismatch was worth multiples of the answer:

        China Taiping   112.13 -> 21.45   (price ~25)
        Cathay Pacific   64.46 -> 25.79   (price ~14)
        CR Power         49.80 -> 21.51   (target 21.01)

    It bites whenever the incoming sector differs from the routed one, which
    is the normal case for any ticker absent from TICKER_SECTOR_LOOKUP -- that
    table has 162 HK rows curated ad-hoc and covers only half of the HK large
    caps.
    """

    def test_the_routing_block_adopts_the_routed_sector(self):
        import inspect
        from src.agents.analysis import dcf_agent as d
        src = inspect.getsource(d.run_dcf_agent)
        i = src.index("industry routing")
        window = src[i:i + 1800]
        assert "sector = _r_sector" in window, (
            "routed sector is not adopted; peer multiples would be looked up "
            "under the incoming sector")

    def test_routing_returns_the_sector_alongside_the_profile(self, monkeypatch):
        from src.agents.analysis import dcf_agent as d
        monkeypatch.setattr("src.tools.api.get_company_industry",
                            lambda t, api_key=None: "Insurance - Life")
        got = d._industry_routed_profile("00966.HK", "Tech")
        assert got is not None
        r_sector, r_profile, _data = got
        assert r_sector == "Financials"
        assert r_profile == "Insurance"
