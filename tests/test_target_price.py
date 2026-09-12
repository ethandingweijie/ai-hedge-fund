"""Consensus targets for HK and SG, which FMP does not cover.

No test here touches the network: the parser is exercised on captured page
text, and the cache and routing are pure.
"""
import json

import pytest

from src.tools import target_price as tp

_PAGE = (
    '<p>According to 16 analysts polled by S&amp;P Global, DBS Group Holdings '
    'stock has a consensus rating of "Buy" and an average price target of '
    '$77.11. The average 1-year stock price forecast is 0.14% higher than the '
    'current stock price, while the lowest is $63.00 and the highest is '
    '$85.86.</p>'
)


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(tp, "_CACHE_DIR", tmp_path / "tp")


def _serve(monkeypatch, body, status=200):
    class _R:
        status_code = status
        text = body
    monkeypatch.setattr("requests.get", lambda *a, **k: _R())


class TestRouting:
    def test_hk_uses_the_four_digit_site_code(self):
        assert tp.quote_path("00700.HK") == ("hkg/0700", "HKD")
        assert tp.quote_path("0700.HK") == ("hkg/0700", "HKD")

    def test_sg_passes_through(self):
        assert tp.quote_path("D05.SI") == ("sgx/D05", "SGD")

    def test_other_venues_are_unsupported(self):
        assert tp.quote_path("AAPL") is None
        assert tp.quote_path("") is None


class TestParsing:
    def test_the_consensus_sentence_is_parsed(self, monkeypatch):
        _serve(monkeypatch, _PAGE)
        r = tp.get_consensus_target("D05.SI")
        assert r["target"] == pytest.approx(77.11)
        assert r["low"] == pytest.approx(63.00)
        assert r["high"] == pytest.approx(85.86)
        assert r["analysts"] == 16
        assert r["rating"] == "Buy"

    def test_currency_is_the_listing_currency_not_usd(self, monkeypatch):
        """The page writes every currency as "$"."""
        _serve(monkeypatch, _PAGE)
        assert tp.get_consensus_target("D05.SI")["currency"] == "SGD"
        assert tp.get_consensus_target("00700.HK")["currency"] == "HKD"

    def test_an_unparseable_page_returns_none(self, monkeypatch):
        _serve(monkeypatch, "<p>No coverage for this stock.</p>")
        assert tp.get_consensus_target("D05.SI") is None

    def test_a_non_200_returns_none(self, monkeypatch):
        _serve(monkeypatch, _PAGE, status=404)
        assert tp.get_consensus_target("D05.SI") is None

    def test_a_network_failure_returns_none(self, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("dns")
        monkeypatch.setattr("requests.get", _boom)
        assert tp.get_consensus_target("D05.SI") is None


class TestCaching:
    def test_a_second_call_does_not_refetch(self, monkeypatch):
        calls = {"n": 0}

        class _R:
            status_code = 200
            text = _PAGE

        def _get(*a, **k):
            calls["n"] += 1
            return _R()
        monkeypatch.setattr("requests.get", _get)
        first = tp.get_consensus_target("D05.SI")
        second = tp.get_consensus_target("D05.SI")
        assert first == second
        assert calls["n"] == 1, "a benchmark that moves between runs is not one"
