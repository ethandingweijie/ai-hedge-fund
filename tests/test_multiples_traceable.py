"""A multiple nobody can trace is a multiple nobody can check.

Every relative method multiplies a peer multiple by an earnings or revenue
figure, so the multiple IS most of the answer -- and a run recorded the answer
without recording it. That is how the 2026-09-13 comps outage survived 17
days: HK names were priced on the static US table and the output was
indistinguishable from one priced on HKSE industry medians.
"""
from src.agents.analysis.dcf_agent import _multiples_trace


class TestMultiplesTrace:
    def test_reports_value_basis_and_peer_count_per_field(self):
        peer = {"ev_ebitda": 9.1935, "pe": 20.3944,
                "_comp_basis": {"ev_ebitda": {"basis": "industry", "cohort": "all",
                                              "peer_count": 10},
                                "pe": {"basis": "industry", "cohort": "all",
                                       "peer_count": 9}},
                "_comp_market": "HKSE", "_comp_age_days": 0.0176}
        t = _multiples_trace(peer)
        assert t["comp_market"] == "HKSE"
        assert t["comp_age_days"] == 0.02
        assert t["fields"]["ev_ebitda"] == {
            "value": 9.1935, "basis": "industry", "cohort": "all", "peer_count": 10}
        assert t["all_static"] is False

    def test_all_static_is_stated_not_implied(self):
        """A reader should not have to know MAX_AGE_DAYS to tell whether these
        are measured comps or fallback values."""
        peer = {"ev_ebitda": 14.0, "pe": 20.0,
                "_comp_basis": {"ev_ebitda": {"basis": "static", "cohort": "US",
                                              "peer_count": None},
                                "pe": {"basis": "static", "cohort": "US",
                                       "peer_count": None}},
                "_comp_market": "US", "_comp_age_days": None}
        assert _multiples_trace(peer)["all_static"] is True

    def test_underscore_and_non_numeric_keys_are_not_fields(self):
        t = _multiples_trace({"pe": 20.0, "_comp_market": "US",
                              "cn_adr_haircut_note": "text"})
        assert set(t["fields"]) == {"pe"}

    def test_tolerates_a_peer_dict_with_no_provenance(self):
        """An older caller returning a bare dict still yields the values."""
        t = _multiples_trace({"pe": 20.0, "ev_ebitda": 14.0})
        assert t["fields"]["pe"]["basis"] == "unknown"
        assert t["fields"]["pe"]["value"] == 20.0

    def test_non_dict_is_empty_not_an_error(self):
        assert _multiples_trace(None) == {}
        assert _multiples_trace("nope") == {}


class TestProvenanceIsAlwaysStamped:
    def test_static_fallback_is_labelled_static_not_left_blank(self):
        """Silence about a fallback is what hid the outage: provenance was
        written only when live comps resolved, so a US-static valuation of a
        Hong Kong stock looked like a run with no provenance at all."""
        from src.data.sector_profiles import get_sector_peer_multiples
        peer = get_sector_peer_multiples("Tech")
        assert "_comp_basis" in peer
        assert peer["_comp_basis"]
        assert all(b.get("basis") for b in peer["_comp_basis"].values())
        assert _multiples_trace(peer)["all_static"] is True
