"""The filing segment-note promotion RETIRED (owner, 2026-09-26).

Until then "SOTP (segments)" was inserted at 0.40 as ANCHOR into whatever
profile a pilot name routed to, whenever its filing parsed. Methods are now
declared by the profile: Refining & Marketing lists the segment SOTP, the China
platforms carry the owner-accepted analyst SOTP, and `_SEGMENT_SOTP_TICKERS`
only selects whose filing footnote is fetched for the shadow computation.
"""
import inspect

import pytest

from src.agents.analysis import dcf_agent as d
from src.agents.analysis.dcf_agent import _SEGMENT_SOTP_TICKERS
from src.data.sector_profiles import INDUSTRY_VALUATION_PROFILES as P


class TestThePromotionIsGone:
    def test_no_helper_and_no_call_site(self):
        assert not hasattr(d, "_promote_segment_sotp")
        assert not hasattr(d, "_SEGMENT_SOTP_WEIGHT") and not hasattr(d, "_SEGMENT_SOTP_PROFILES")
        assert "_promote_segment_sotp(" not in inspect.getsource(d.run_dcf_agent)

    def test_refining_declares_the_segment_sotp_at_the_weight_it_was_promoted_at(self):
        m = {x["name"]: x for x in P["Energy"]["Refining & Marketing"]["methods"]}
        assert m["SOTP (segments)"]["weight"] == pytest.approx(0.40)
        assert m["SOTP (segments)"]["anchor"] is False
        assert m["EV/EBITDA (norm)"]["anchor"] is True and m["EV/EBITDA (norm)"]["weight"] == pytest.approx(0.24)
        assert sum(x["weight"] for x in m.values()) == pytest.approx(1.0)

    def test_the_china_platforms_carry_the_analyst_sotp_not_the_segment_one(self):
        m = {x["name"]: x["weight"] for x in P["Tech"]["China Internet Platform"]["methods"]}
        assert m["SOTP (analyst)"] == pytest.approx(0.35) and "SOTP (segments)" not in m


class TestTheFootnoteSelection:
    def test_genscript_is_excluded_on_purpose(self):
        """Legend Biotech is an ASSOCIATE since the Oct-2024 deconsolidation,
        so it contributes no revenue segment: a segment SOTP omits 45.03% of
        its market value by construction. The look-through is its instrument."""
        assert "01548.HK" not in _SEGMENT_SOTP_TICKERS

    def test_the_footnote_is_fetched_for_the_three_names_whose_filings_carry_profit(self):
        assert _SEGMENT_SOTP_TICKERS == {"00700.HK", "01810.HK", "03690.HK"}
