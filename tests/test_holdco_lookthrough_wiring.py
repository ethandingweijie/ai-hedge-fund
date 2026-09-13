"""The conglomerate look-through, and the conditions under which it refuses.

`Holding Company` anchors SOTP / NAV at 0.70 with implementable: False, so
70% of every conglomerate's valuation was a P/BV proxy -- and P/BV came back
as a literal 0.0 for Jardine Cycle & Carriage, which then fell through to the
financial ladder and was valued as Mature SaaS on EV/Revenue of 223x.

Network is never touched: the module's three data helpers are patched.
"""
import pytest

from src.agents.analysis import holdco_sotp as H
from src.agents.analysis.dcf_agent import _blend_methods


@pytest.fixture
def stub(monkeypatch):
    """Two-division holdco: one consolidated listed stake, one unlisted."""
    monkeypatch.setattr(H, "_market_value", lambda t, d: 1000.0)
    monkeypatch.setattr(H, "_fx", lambda a, b: 1.0)
    monkeypatch.setattr(H, "_ebitda_of",
                        lambda t, d: (500.0, "USD") if t == "PARENT"
                        else (400.0, "USD"))
    monkeypatch.setattr(H, "template_for", lambda t: {
        "name": "Test Holdco", "currency": "USD",
        "holdco_discount": [0.2, 0.2],
        "divisions": [
            {"name": "Listed Sub", "basis": "market_stake",
             "listed": "SUB", "stake_pct": 0.6},
            {"name": "Unlisted Ops", "basis": "ev_ebitda_range",
             "multiple_range": [5.0, 7.0]},
        ]})
    return H


class TestResidualEbitda:
    def test_subtracts_the_whole_consolidated_subsidiary(self, stub):
        """Consolidation carries 100% of the sub's EBITDA, not the parent's
        share -- the rest shows up as minority interest, not as less EBITDA."""
        assert H.residual_ebitda("PARENT", "2026-08-16", "USD") == 100.0

    def test_completes_and_applies_one_discount(self, stub):
        r = H.look_through_value("PARENT", "2026-08-16")
        assert r["complete"] is True
        # 1000 x 0.6 stake = 600; residual 100 x mid(5,7)=6 -> 600; GAV 1200
        assert r["gross_asset_value"] == pytest.approx(1200.0)
        assert r["net_asset_value"] == pytest.approx(960.0)   # one 20% discount

    def test_refuses_when_two_divisions_await_ebitda(self, stub, monkeypatch):
        """One residual pool cannot be split across divisions whose multiples
        differ without inventing the split; CK Hutchison spans 4.5x to 10x."""
        tpl = H.template_for("PARENT")
        tpl["divisions"].append({"name": "Another", "basis": "ev_ebitda_range",
                                 "multiple_range": [8.0, 9.0]})
        monkeypatch.setattr(H, "template_for", lambda t: tpl)
        assert H.residual_ebitda("PARENT", "2026-08-16", "USD") is None
        assert H.look_through_value("PARENT", "2026-08-16")["complete"] is False

    def test_refuses_when_the_subtraction_cannot_be_verified(self, stub, monkeypatch):
        monkeypatch.setattr(H, "_ebitda_of",
                            lambda t, d: (500.0, "USD") if t == "PARENT" else None)
        assert H.residual_ebitda("PARENT", "2026-08-16", "USD") is None

    def test_refuses_a_non_positive_residual(self, stub, monkeypatch):
        monkeypatch.setattr(H, "_ebitda_of",
                            lambda t, d: (500.0, "USD") if t == "PARENT"
                            else (900.0, "USD"))
        assert H.residual_ebitda("PARENT", "2026-08-16", "USD") is None

    def test_associates_are_not_subtracted(self, stub, monkeypatch):
        """A stake below 50% is equity-accounted, so its EBITDA was never in
        the group figure and removing it would double-count."""
        tpl = H.template_for("PARENT")
        tpl["divisions"][0]["stake_pct"] = 0.25
        monkeypatch.setattr(H, "template_for", lambda t: tpl)
        assert H.residual_ebitda("PARENT", "2026-08-16", "USD") == 500.0


class TestPartialIsRefused:
    def test_incomplete_look_through_yields_no_per_share_value(self, stub, monkeypatch):
        monkeypatch.setattr(H, "residual_ebitda", lambda t, d, c: None)
        assert H.value_per_share("PARENT", "2026-08-16", 100.0) is None

    def test_decline_reason_names_the_missing_division(self, stub, monkeypatch):
        monkeypatch.setattr(H, "residual_ebitda", lambda t, d, c: None)
        why = H.decline_reason("PARENT", "2026-08-16")
        assert "Unlisted Ops" in why

    def test_complete_look_through_yields_per_share(self, stub):
        assert H.value_per_share("PARENT", "2026-08-16", 100.0) == pytest.approx(9.6)

    def test_zero_shares_is_refused(self, stub):
        assert H.value_per_share("PARENT", "2026-08-16", 0.0) is None


class TestBlendPrefersTheRealMethod:
    """A proxy stands in for a method that could not be computed. Once it can
    be, it is the answer -- not a second opinion averaged against it."""

    METHODS = [{"name": "SOTP / NAV", "weight": 0.9, "anchor": True,
                "implementable": False, "proxy": "P/BV"},
               {"name": "DDM", "weight": 0.1, "implementable": True}]

    def test_real_value_wins_over_a_zero_proxy(self):
        iv, _ = _blend_methods(
            profile_methods=self.METHODS,
            method_values={"SOTP / NAV": 10.0, "P/BV": 0.0, "DDM": 2.0},
            c_macro=0.0, dcf_tv_fraction=0.0, forward_flags=[])
        assert iv == pytest.approx(0.9 * 10.0 + 0.1 * 2.0)

    def test_proxy_still_used_when_the_real_method_is_absent(self):
        iv, _ = _blend_methods(
            profile_methods=self.METHODS,
            method_values={"P/BV": 4.0, "DDM": 2.0},
            c_macro=0.0, dcf_tv_fraction=0.0, forward_flags=[])
        assert iv == pytest.approx(0.9 * 4.0 + 0.1 * 2.0)
