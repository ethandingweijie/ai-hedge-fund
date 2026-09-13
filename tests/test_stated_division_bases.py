"""Divisions valued from a figure recorded in the template.

SGX publishes no machine-readable segment note, so Olam's and SingPost's
segment economics are user-supplied and stored. That makes two things matter
that a parsed figure never has to worry about: the UNITS the table was
published in, and the CURRENCY the parent's net debt arrives in.
"""
import pytest

from src.agents.analysis import holdco_sotp as H


@pytest.fixture(autouse=True)
def no_fx(monkeypatch):
    monkeypatch.setattr(H, "_fx", lambda a, b: 1.0)


class TestBases:
    def test_transaction_anchor_uses_the_deal_mark(self):
        v, d = H._stated_division_value(
            {"basis": "transaction_anchor", "enterprise_value": 3500.0,
             "units_multiplier": 1_000_000}, "USD")
        assert v == 3.5e9
        assert d["enterprise_value"] == 3500.0

    def test_cap_rate_is_income_over_a_yield(self):
        """SingPost Centre: S$42.1m at 4.0-4.5% is ~S$0.94-1.05bn."""
        v, d = H._stated_division_value(
            {"basis": "cap_rate", "ebit": 42.1, "cap_rate_range": [0.040, 0.045],
             "units_multiplier": 1_000_000}, "SGD")
        assert v == pytest.approx(42.1e6 / 0.0425)
        # A LOWER cap rate is a HIGHER value.
        assert d["value_high"] > d["value_low"]
        assert d["value_high"] == pytest.approx(42.1e6 / 0.040)

    def test_ev_ebit_range_takes_the_midpoint(self):
        v, _ = H._stated_division_value(
            {"basis": "ev_ebit_range", "ebit": 785.4,
             "multiple_range": [10.0, 12.0], "units_multiplier": 1_000_000}, "USD")
        assert v == pytest.approx(785.4e6 * 11.0)

    def test_nil_is_an_explicit_zero_not_a_gap(self):
        """A stub under divestment is worth about nothing and saying so is a
        judgement; omitting it is an omission. The two must not look alike."""
        v, d = H._stated_division_value(
            {"basis": "nil", "source": "de-prioritised, gestating"}, "USD")
        assert v == 0.0
        assert "gestating" in d["rationale"]


class TestGuards:
    def test_negative_ebit_is_refused_unless_declared(self):
        """A negative EBIT times a positive multiple is right for a central
        cost line and wrong for an operating stub."""
        assert H._stated_division_value(
            {"basis": "ev_ebit_range", "ebit": -25.1,
             "multiple_range": [7.0, 9.0]}, "SGD") is None
        v, _ = H._stated_division_value(
            {"basis": "ev_ebit_range", "ebit": -25.1, "negative_ok": True,
             "multiple_range": [7.0, 9.0], "units_multiplier": 1_000_000}, "SGD")
        assert v == pytest.approx(-25.1e6 * 8.0)

    def test_a_non_positive_cap_rate_income_is_refused(self):
        assert H._stated_division_value(
            {"basis": "cap_rate", "ebit": -1.0,
             "cap_rate_range": [0.04, 0.045]}, "SGD") is None

    def test_missing_figures_are_refused(self):
        assert H._stated_division_value({"basis": "transaction_anchor"}, "USD") is None
        assert H._stated_division_value(
            {"basis": "ev_ebit_range", "ebit": 10.0}, "USD") is None

    def test_units_default_to_one_when_undeclared(self):
        v, _ = H._stated_division_value(
            {"basis": "transaction_anchor", "enterprise_value": 42.0}, "USD")
        assert v == 42.0


class TestTemplatesAreScaledAndAligned:
    def test_every_stated_figure_declares_its_units(self):
        """The published tables are in millions and net debt is absolute.
        Without the scale the bridge subtracted 13.8bn from 12,139."""
        for tk, tpl in H._load()["templates"].items():
            for div in tpl["divisions"]:
                if div.get("basis") in ("transaction_anchor", "cap_rate",
                                        "ev_ebit_range"):
                    assert div.get("units_multiplier"), (tk, div["name"])

    def test_template_currency_is_the_listing_currency(self):
        """Parent net debt reaches the bridge in the listing currency, so the
        template must be denominated in it; divisions convert individually."""
        suffix_ccy = {".HK": "HKD", ".SI": "SGD"}
        for tk, tpl in H._load()["templates"].items():
            want = suffix_ccy.get("." + tk.rsplit(".", 1)[1])
            if want:
                assert tpl["currency"] == want, (tk, tpl["currency"])

    def test_olam_is_flagged_pending_an_rmi_adjustment(self):
        tpl = H._load()["templates"]["VC2.SI"]
        assert "readily-marketable" in tpl["net_debt_note"]
