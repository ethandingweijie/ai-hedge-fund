"""The EV -> equity bridge must deduct minority interest.

An EV multiple prices the whole enterprise. Where a parent consolidates a
subsidiary it only part-owns, some of that enterprise belongs to somebody
else, and handing it to the parent's shareholders overstates the equity --
badly, for the Asian trust and conglomerate structures where the minority
can exceed the parent's own equity.
"""
from src.agents.analysis.dcf_agent import (
    _ev_to_equity_ps, _minority_interest, _FX_MONETARY_FIELDS)


class TestMinorityInterest:
    def test_deducted_from_enterprise_value(self):
        mr = {"minority_interest": 300.0}
        # EV 1000, net debt 200, MI 300 -> equity 500 over 100 shares
        assert _ev_to_equity_ps(1000.0, 200.0, mr, 100.0) == 5.0

    def test_absent_minority_is_a_no_op(self):
        assert _ev_to_equity_ps(1000.0, 200.0, {}, 100.0) == 8.0
        assert _ev_to_equity_ps(1000.0, 200.0, {"minority_interest": None},
                                100.0) == 8.0

    def test_negative_minority_never_adds_value(self):
        """A sub with an accumulated deficit must not credit the parent."""
        mr = {"minority_interest": -400.0}
        assert _ev_to_equity_ps(1000.0, 200.0, mr, 100.0) == 8.0
        assert _minority_interest(mr) == 0.0

    def test_floors_at_zero_not_negative_per_share(self):
        mr = {"minority_interest": 5000.0}
        assert _ev_to_equity_ps(1000.0, 200.0, mr, 100.0) == 0.0

    def test_guards_zero_shares(self):
        assert _ev_to_equity_ps(1000.0, 0.0, {}, 0.0) is None

    def test_minority_interest_is_fx_converted(self):
        """It is deducted against an FX-converted EV, so it must convert too.

        HPH Trust reports in HKD and is valued in USD: an unconverted HKD
        16.5bn minority against a USD enterprise value would wipe the equity
        out entirely.
        """
        assert "minority_interest" in _FX_MONETARY_FIELDS
