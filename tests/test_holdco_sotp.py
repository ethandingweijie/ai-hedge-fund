"""Look-through SOTP for conglomerate holding companies."""
import json

import pytest

from src.agents.analysis import holdco_sotp as h


@pytest.fixture(autouse=True)
def _no_flag(monkeypatch):
    monkeypatch.delenv(h.FLAG, raising=False)


class TestTemplates:
    def test_the_three_holdcos_resolve_from_any_ticker_form(self):
        for form in ("00019.HK", "19.HK", "0019.HK"):
            t = h.template_for(form)
            assert t and t["name"] == "Swire Pacific A", form

    def test_unknown_ticker_has_no_template(self):
        assert h.template_for("00700.HK") is None

    def test_every_listed_stake_names_a_real_ticker(self):
        for tk, tpl in (h._load()["templates"]).items():
            for d in tpl["divisions"]:
                if d["basis"] == "market_stake":
                    listed = (d.get("listed") or "").strip()
                    assert listed, (tk, d["name"])
                    # Listings span venues: Astra is Jakarta, Yihai Kerry
                    # Shenzhen, AWL Agri Mumbai -- and Legend Biotech is on
                    # Nasdaq, which carries no suffix at all. Requiring a "."
                    # asserted "not US", which is not the property wanted.
                    assert h.currency_of(listed), (tk, d["name"])

    def test_every_listed_stake_resolves_to_a_currency(self):
        """A part added in the wrong currency IS the valuation: Astra is
        quoted in IDR at ~190 trillion against an SGD parent."""
        for tk, tpl in (h._load()["templates"]).items():
            for d in tpl["divisions"]:
                if d["basis"] != "market_stake":
                    continue
                listed = d["listed"]
                ccy = h.currency_of(listed)
                if "." in listed:
                    suffix = "." + listed.rsplit(".", 1)[1]
                    assert suffix in h._SUFFIX_CCY, (tk, listed, "unmapped venue")
                    assert ccy == h._SUFFIX_CCY[suffix]
                else:
                    assert ccy == "USD", (tk, listed)

    def test_every_stake_percentage_is_sourced(self):
        """A wrong ownership percentage moves the answer further than any
        multiple choice, so an unsourced one must be null, not a guess."""
        for tk, tpl in (h._load()["templates"]).items():
            for d in tpl["divisions"]:
                if d["basis"] != "market_stake":
                    continue
                pct = d.get("stake_pct")
                if pct is None:
                    continue
                assert 0.0 < pct <= 1.0, (tk, d["name"], pct)
                assert d.get("source"), (tk, d["name"], "stake without a source")

    def test_discounts_are_a_sane_range(self):
        for tk, tpl in (h._load()["templates"]).items():
            lo, hi = tpl["holdco_discount"]
            assert 0.0 <= lo <= hi < 0.6, tk

    def test_a_zero_group_discount_means_the_discount_is_on_a_division(self):
        """Kingboard's haircut is on the Laminates stake and GenScript's on the
        Legend mark. A group discount of zero there is deliberate -- applying
        both would take the same haircut twice -- but a template with NO
        discount anywhere is an omission, not a policy."""
        for tk, tpl in (h._load()["templates"]).items():
            lo, hi = tpl["holdco_discount"]
            if lo or hi:
                continue
            on_divisions = [d for d in tpl["divisions"] if d.get("discount_pct")]
            assert on_divisions, f"{tk}: no discount at group OR division level"
            assert tpl.get("discount_source"), f"{tk}: undocumented zero discount"

    def test_every_discount_is_documented(self):
        for tk, tpl in (h._load()["templates"]).items():
            if tpl.get("discount_source"):
                continue
            # Pre-existing templates carry their reasoning in the file doc;
            # any template whose discount was SET deliberately must say why.
            assert tk in {"00267.HK", "00001.HK", "00019.HK"}, tk

    def test_division_discounts_are_sane(self):
        for tk, tpl in (h._load()["templates"]).items():
            for d in tpl["divisions"]:
                pct = d.get("discount_pct")
                if pct is None:
                    continue
                assert 0.0 <= pct < 0.6, (tk, d["name"], pct)


class TestLookThrough:
    """Marked at market, summed, discounted ONCE at the total."""

    def _patch_mcap(self, monkeypatch, value=1000.0):
        monkeypatch.setattr(h, "_market_value", lambda listed, end: value)

    def test_listed_stake_is_marked_at_market_times_ownership(self, monkeypatch):
        self._patch_mcap(monkeypatch, 1000.0)
        out = h.look_through_value("00019.HK", "2026-08-16",
                                   ebitda_by_division={"Beverages (Swire Coca-Cola)": 100.0,
                                                       "Aviation Services (HAECO)": 50.0})
        props = next(p for p in out["parts"] if p["division"] == "Swire Properties")
        assert props["value"] == pytest.approx(820.0)   # 1000 x 0.82

    def test_the_discount_is_applied_once_at_the_total(self, monkeypatch):
        self._patch_mcap(monkeypatch, 1000.0)
        out = h.look_through_value("00019.HK", "2026-08-16",
                                   ebitda_by_division={"Beverages (Swire Coca-Cola)": 100.0,
                                                       "Aviation Services (HAECO)": 50.0},
                                   discount=0.20)
        assert out["net_asset_value"] == pytest.approx(out["gross_asset_value"] * 0.8)

    def test_an_unsourced_stake_is_skipped_not_guessed(self, monkeypatch):
        """A wrong ownership percentage moves the answer further than any
        multiple choice, so `stake_pct: null` must never be treated as 100%.

        Every stake in the shipped templates is now sourced, so this builds an
        unsourced one rather than relying on a gap that should not persist.
        """
        self._patch_mcap(monkeypatch, 1000.0)
        monkeypatch.setattr(h, "_load", lambda: {"templates": {"ZZ.HK": {
            "name": "Test", "currency": "HKD", "holdco_discount": [0.2, 0.2],
            "divisions": [
                {"name": "Sourced", "basis": "market_stake",
                 "listed": "00001.HK", "stake_pct": 0.5},
                {"name": "Unsourced", "basis": "market_stake",
                 "listed": "00002.HK", "stake_pct": None}]}}})
        out = h.look_through_value("ZZ.HK", "2026-08-16")
        assert [p["division"] for p in out["parts"]] == ["Sourced"]
        assert any("ownership percentage" in s["reason"] for s in out["skipped"])

    def test_a_missing_division_marks_the_result_incomplete(self, monkeypatch):
        """A SOTP missing a division is not conservative, it is wrong."""
        self._patch_mcap(monkeypatch, 1000.0)
        out = h.look_through_value("00019.HK", "2026-08-16",
                                   ebitda_by_division={"Beverages (Swire Coca-Cola)": 100.0})
        assert out["complete"] is False
        assert any(s["division"] == "Aviation Services (HAECO)" for s in out["skipped"])

    def test_a_fully_sourced_holdco_reports_complete(self, monkeypatch):
        self._patch_mcap(monkeypatch, 1000.0)
        out = h.look_through_value("00019.HK", "2026-08-16",
                                   ebitda_by_division={"Beverages (Swire Coca-Cola)": 100.0,
                                                       "Aviation Services (HAECO)": 50.0})
        assert out["complete"] is True
        assert len(out["parts"]) == 4

    def test_ev_ebitda_divisions_carry_their_range(self, monkeypatch):
        self._patch_mcap(monkeypatch, 1000.0)
        out = h.look_through_value("00019.HK", "2026-08-16",
                                   ebitda_by_division={"Beverages (Swire Coca-Cola)": 100.0,
                                                       "Aviation Services (HAECO)": 50.0})
        bev = next(p for p in out["parts"] if p["division"].startswith("Beverages"))
        assert bev["value_low"] == pytest.approx(900.0)    # 100 x 9.0
        assert bev["value_high"] == pytest.approx(1100.0)  # 100 x 11.0
        assert bev["value"] == pytest.approx(1000.0)       # midpoint

    def test_no_parts_means_no_answer(self, monkeypatch):
        monkeypatch.setattr(h, "_market_value", lambda listed, end: None)
        assert h.look_through_value("00019.HK", "2026-08-16") is None


class TestFeatureFlag:
    def test_default_off(self):
        assert h.enabled() is False

    def test_turns_on(self, monkeypatch):
        monkeypatch.setenv(h.FLAG, "true")
        assert h.enabled() is True


class TestForeignSuffixes:
    """A dot is a share class OR an exchange code. They cannot be told apart
    by shape -- both are one or two letters -- so the exchange codes are
    listed explicitly."""

    def test_exchange_suffixes_survive(self):
        from src.tools.fmp_transcripts import to_fmp_symbol
        for t in ("ASII.JK", "600030.SS", "RIO.L", "D05.SI", "7203.T"):
            assert to_fmp_symbol(t) == t, t

    def test_us_class_shares_still_take_the_hyphen(self):
        from src.tools.fmp_transcripts import to_fmp_symbol
        assert to_fmp_symbol("BRK.B") == "BRK-B"
        assert to_fmp_symbol("BF.B") == "BF-B"

    def test_hk_still_drops_to_four_digits(self):
        from src.tools.fmp_transcripts import to_fmp_symbol
        assert to_fmp_symbol("00700.HK") == "0700.HK"


class TestCurrencyConversion:
    """A look-through sums parts from several exchanges."""

    def test_currency_inferred_from_suffix(self):
        assert h.currency_of("ASII.JK") == "IDR"
        assert h.currency_of("00008.HK") == "HKD"
        assert h.currency_of("C07.SI") == "SGD"
        assert h.currency_of("AAPL") == "USD"

    def test_parts_are_converted_before_being_summed(self, monkeypatch):
        """Astra is quoted at ~190 TRILLION rupiah against a SGD parent.
        Unconverted, that one line would be the entire valuation."""
        monkeypatch.setattr(h, "_market_value", lambda listed, end: 100.0)
        monkeypatch.setattr(h, "_fx", lambda a, b: 0.0001 if a == "IDR" else 1.0)
        out = h.look_through_value("C07.SI", "2026-08-16",
                                   ebitda_by_division={"Direct Motor Interests": 10.0})
        astra = next(p for p in out["parts"] if p["listed"] == "ASII.JK")
        assert astra["currency"] == "IDR"
        assert astra["fx_to_reporting"] == 0.0001
        assert astra["value"] == pytest.approx(100.0 * 0.5011 * 0.0001)
        assert out["reporting_currency"] == "SGD"

    def test_a_missing_rate_skips_rather_than_sums_raw(self, monkeypatch):
        monkeypatch.setattr(h, "_market_value", lambda listed, end: 100.0)
        monkeypatch.setattr(h, "_fx", lambda a, b: None if a == "IDR" else 1.0)
        out = h.look_through_value("C07.SI", "2026-08-16",
                                   ebitda_by_division={"Direct Motor Interests": 10.0})
        assert any("rate" in s["reason"] for s in out["skipped"])
        assert all(p["listed"] != "ASII.JK" for p in out["parts"]
                   if p.get("listed"))


def test_every_sourced_stake_records_its_provenance():
    """A percentage without a source cannot be checked later."""
    for tk, tpl in (h._load()["templates"]).items():
        for d in tpl["divisions"]:
            if d.get("stake_pct") is not None and tk not in ("00019.HK",):
                assert d.get("source"), f"{tk}/{d['name']} has no source"
