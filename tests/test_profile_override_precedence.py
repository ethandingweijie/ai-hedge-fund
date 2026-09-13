"""A curated per-ticker profile must beat an industry rule, and must resolve
under its own sector.

Two regressions this pins:

  * industry routing reassigns `sector` when it adopts a profile. The ticker
    override that runs afterwards was resolved against that reassigned
    sector, so a correct curated entry was silently declined -- Sembcorp went
    to Regulated Utility (Singapore's power market is liberalised) and
    ComfortDelGro to Rail / Logistics, both over a correct curated
    "Conglomerate / Industrial (SG)".
  * sector "REIT" has no key in INDUSTRY_VALUATION_PROFILES -- S-REIT lives
    under "RealEstate" -- which declined the override for every S-REIT.
"""
from src.data.sector_profiles import (
    INDUSTRY_VALUATION_PROFILES, SGX_TICKER_SECTOR_LOOKUP,
    get_wacc_profile_for_ticker)


class TestCuratedOverrideResolves:
    def test_reit_sector_has_no_profiles_entry(self):
        """The condition that made the alias necessary. If this ever gains an
        entry, the alias is redundant rather than wrong."""
        assert "REIT" not in INDUSTRY_VALUATION_PROFILES
        assert "S-REIT" in INDUSTRY_VALUATION_PROFILES["RealEstate"]

    def test_every_sg_curated_pair_resolves_somewhere(self):
        """Each curated (sector, profile) must resolve under its own sector,
        or under the RealEstate alias when the sector is REIT."""
        unresolved = []
        for ticker in SGX_TICKER_SECTOR_LOOKUP:
            sec, prof = get_wacc_profile_for_ticker(ticker)
            if not prof:
                continue
            cands = [sec] + (["RealEstate"] if sec == "REIT" else [])
            if not any(INDUSTRY_VALUATION_PROFILES.get(c, {}).get(prof)
                       for c in cands if c):
                unresolved.append((ticker, sec, prof))
        assert not unresolved, f"curated pairs that resolve nowhere: {unresolved}"

    def test_names_the_router_overrode(self):
        """Both had their curated entry declined because the router had
        already reassigned `sector`. Restoring precedence exposed that one of
        the two curated entries was itself wrong, so they now differ:

          Sembcorp   -- a conglomerate; the router's "Regulated Utility" is
                        wrong on its face, Singapore's power market being
                        liberalised.
          ComfortDelGro -- a bus and rail operator. Its curated row said
                        conglomerate while the row's OWN sub-industry field
                        said Transportation, and the conglomerate profile
                        weights SOTP (published) at 0.35 against a company
                        that publishes none. Here the router was right.
        """
        expected = {"U96.SI": "Conglomerate / Industrial (SG)",
                    "C52.SI": "Rail / Logistics"}
        for ticker, want in expected.items():
            sec, prof = get_wacc_profile_for_ticker(ticker)
            assert prof == want, f"{ticker}: {prof!r} != {want!r}"
            assert INDUSTRY_VALUATION_PROFILES.get(sec, {}).get(prof), ticker
