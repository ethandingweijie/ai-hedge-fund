"""The market registry and the shared segment contract.

The point of the registry is that a new market implements ONE parser and
inherits every rule about what makes a valid segment map. These tests pin the
parts that would otherwise drift apart per market.
"""
import pytest

from src.tools import segment_providers as sp


class TestRouting:
    def test_us_ticker_routes_to_sec(self):
        assert [n for n, _ in sp._routes("AAPL")] == ["sec"]

    def test_hk_primary_routes_to_hkex_only(self):
        assert [n for n, _ in sp._routes("00700.HK")] == ["hkex"]

    def test_hk_line_with_an_adr_prefers_sec(self):
        """9988.HK is covered by Alibaba's 20-F as well as its own filing.

        Rendered XBRL carries explicit dimensions; a 3-14MB PDF's columns have
        to be recovered from geometry. Prefer the one that parses reliably,
        and keep the PDF as the fallback.
        """
        names = [n for n, _ in sp._routes("09988.HK")]
        assert names == ["sec", "hkex"]

    def test_sg_ticker_routes_to_sgx(self):
        assert [n for n, _ in sp._routes("D05.SI")] == ["sgx"]

    def test_sgx_is_registered_but_not_implemented(self):
        from src.tools.sgx_segments import (
            NOT_IMPLEMENTED_REASON, get_segment_footnote)
        assert get_segment_footnote("D05.SI", "2026-08-16") is None
        assert "hkex_api" in NOT_IMPLEMENTED_REASON


class TestContract:
    """One shape, whichever market answered."""

    def _sec_shaped(self):
        # the SEC parser's own key names, which predate the second market
        return {"segments": [{"name": "A", "revenue": 10.0, "profit": 2.0,
                              "assets": None},
                             {"name": "B", "revenue": 5.0, "profit": None,
                              "assets": 3.0}],
                "reporting_currency": "USD",
                "profit_metric": "us-gaap_OperatingIncomeLoss",
                "profit_is_gaap_operating_income": True}

    def test_sec_key_names_are_conformed(self):
        out = sp._conform(self._sec_shaped(), "sec")
        assert out["reported_currency"] == "USD"
        assert out["profit_label"] == "us-gaap_OperatingIncomeLoss"
        assert out["profit_is_operating_income"] is True

    def test_aliases_are_copied_not_renamed(self):
        """A provider's existing consumers must keep working."""
        out = sp._conform(self._sec_shaped(), "sec")
        assert out["reporting_currency"] == "USD"
        assert out["profit_metric"] == "us-gaap_OperatingIncomeLoss"

    def test_every_required_key_is_present(self):
        out = sp._conform({"segments": []}, "hkex")
        for key in sp._REQUIRED:
            assert key in out, key
        assert out["warnings"] == []
        assert out["provider"] == "hkex"

    def test_disclosure_flags_are_derived_not_trusted(self):
        out = sp._conform(self._sec_shaped(), "sec")
        assert out["n_segments"] == 2
        assert out["profit_disclosed"] is True     # A has profit
        assert out["assets_disclosed"] is True     # B has assets

    def test_no_segments_means_no_false_disclosure(self):
        out = sp._conform({"segments": [{"name": "A", "revenue": 1.0,
                                         "profit": None, "assets": None}]},
                          "hkex")
        assert out["profit_disclosed"] is False
        assert out["assets_disclosed"] is False


class TestSharedNormalisation:
    def test_both_markets_use_the_same_rules_module(self):
        """The judgement about a valid segment map is written once."""
        import src.tools.sec_segments as sec
        from src.tools import segment_normalize as norm
        assert sec._is_geographic is norm._is_geographic
        assert sec._drop_hierarchy_parents is norm._drop_hierarchy_parents
        assert sec._filter_segments is norm._filter_segments

    def test_provider_failure_is_recorded_not_raised(self, monkeypatch):
        monkeypatch.setattr(sp, "_routes",
                            lambda t: [("boom", lambda *a: 1 / 0)])
        assert sp.get_segment_footnote("X", "2026-08-16") is None
        assert "ZeroDivisionError" in sp.last_reason("X")


def test_known_absent_adr_does_not_trigger_a_sec_lookup():
    """The alias map records known-absent filers as None, not by omission.

    Tencent, Xiaomi and Meituan are each listed with an unsponsored ADR that
    files nothing with the SEC. Testing key presence rather than the value
    sent all three on a wasted SEC round-trip before falling back to HKEX.
    """
    for hk in ("00700.HK", "01810.HK", "03690.HK"):
        assert [n for n, _ in sp._routes(hk)] == ["hkex"], hk
    # while a real ADR filer still gets the preferred SEC route
    assert [n for n, _ in sp._routes("09618.HK")] == ["sec", "hkex"]
