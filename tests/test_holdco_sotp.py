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
                    assert d.get("listed", "").endswith(".HK"), (tk, d["name"])

    def test_discounts_are_a_sane_range(self):
        for tk, tpl in (h._load()["templates"]).items():
            lo, hi = tpl["holdco_discount"]
            assert 0.0 < lo <= hi < 0.6, tk


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
        multiple choice, so `stake_pct: null` must never be treated as 100%."""
        self._patch_mcap(monkeypatch, 1000.0)
        out = h.look_through_value("00001.HK", "2026-08-16",
                                   ebitda_by_division={
                                       "Ports & Related Services": 100.0,
                                       "Retail (A.S. Watson)": 100.0,
                                       "Telecommunications (3 Group Europe)": 100.0})
        names = [p["division"] for p in out["parts"]]
        assert "Infrastructure (CKI)" not in names
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
