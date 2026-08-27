"""
tests/test_screener_intl.py
===========================
Smoke cover for the HK and SGX screeners after they moved onto FMP.

Why this exists: the SGX rewrite dropped a `raw_metrics = {}` initialisation
and shipped. Every existing test passed, because none of them execute the
body of get_sg_screener_stocks — it needs network — so a plain NameError
reached production and 500'd the endpoint for 88 seconds per request.

These tests stub the two network boundaries (_intl_universe and
_fetch_ticker_metrics) and run the real function bodies end to end, so any
unbound name, bad key or shape error surfaces offline.
"""
from __future__ import annotations

import pytest

from app.backend.services import screener_service as ss


# ── Fixtures ────────────────────────────────────────────────────────────────

def _universe(prefix: str, n: int = 12):
    """(rows, sector_map, industry_map, name_map) in _intl_universe's shape."""
    rows, sector_map, industry_map, name_map = [], {}, {}, {}
    for i in range(n):
        t = f"{prefix}{i:04d}"
        # Two industries so the industry tier has a chance to engage.
        industry = "Banks" if i % 2 == 0 else "Software - Application"
        sector = "Financial Services" if i % 2 == 0 else "Technology"
        rows.append({"canonical": t, "name": f"Co {i}", "sector": sector,
                     "industry": industry, "market_cap": 1e11 - i * 1e9})
        sector_map[t], industry_map[t], name_map[t] = sector, industry, f"Co {i}"
    return rows, sector_map, industry_map, name_map


def _metrics(ticker: str, api_key=None, **kw):
    """Plausible _fetch_ticker_metrics output."""
    seed = abs(hash(ticker)) % 17
    return {
        "ticker": ticker,
        "pe": 10.0 + seed, "pb": 1.0 + seed / 10, "ev_ebitda": 8.0 + seed,
        "ev_sales": 2.0 + seed / 5, "fcf_yield": 0.03 + seed / 1000,
        "roe": 0.10 + seed / 200, "net_margin": 0.15,
        "rev_growth": 0.05, "eps_growth": 0.07,
        "price": 100.0 + seed, "beta": 1.0,
        "market_cap": 5e10,
    }


@pytest.fixture
def stubbed(monkeypatch, tmp_path):
    monkeypatch.setattr(ss, "_get_fmp_key", lambda: "test-key")
    monkeypatch.setattr(ss, "_fetch_ticker_metrics", _metrics)
    monkeypatch.setattr(ss, "_get_cached", lambda key: None)      # always cold
    monkeypatch.setattr(ss, "_set_cached", lambda *a, **k: None)
    monkeypatch.setattr(ss, "_ensure_tables", lambda: None)
    monkeypatch.setattr(ss, "get_live_quotes", lambda *a, **k: {})
    monkeypatch.setattr(ss, "_set_fast_vgpm_cached", lambda *a, **k: None)
    import app.backend.services.knowledge_graph as kg
    monkeypatch.setattr(kg, "set_ttm_metrics", lambda *a, **k: None)
    yield


# ── The regression ──────────────────────────────────────────────────────────

class TestSgScreenerRuns:
    def test_returns_items(self, stubbed, monkeypatch):
        """The exact failure that reached prod: NameError on raw_metrics."""
        monkeypatch.setattr(ss, "_intl_universe", lambda m, **k: _universe("D0"))
        out = ss.get_sg_screener_stocks()
        assert out["total"] == 12
        assert len(out["items"]) == 12
        assert out["cached"] is False

    def test_items_carry_required_fields(self, stubbed, monkeypatch):
        monkeypatch.setattr(ss, "_intl_universe", lambda m, **k: _universe("D0"))
        item = ss.get_sg_screener_stocks()["items"][0]
        for field in ("symbol", "companyName", "sector", "industry"):
            assert field in item, f"{field} missing from SGX screener item"

    def test_empty_universe_degrades(self, stubbed, monkeypatch):
        monkeypatch.setattr(ss, "_intl_universe", lambda m, **k: ([], {}, {}, {}))
        out = ss.get_sg_screener_stocks()
        assert out == {"items": [], "total": 0, "cached": False}

    def test_universe_failure_degrades(self, stubbed, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("screener down")
        monkeypatch.setattr(ss, "_intl_universe", boom)
        out = ss.get_sg_screener_stocks()
        assert out == {"items": [], "total": 0, "cached": False}


class TestHkScreenerRuns:
    def test_returns_items(self, stubbed, monkeypatch):
        monkeypatch.setattr(ss, "_intl_universe", lambda m, **k: _universe("0"))
        out = ss.get_hk_screener_stocks()
        assert out["total"] == 12
        assert len(out["items"]) == 12

    def test_industry_is_real_not_the_hkex_placeholder(self, stubbed, monkeypatch):
        """Every HK stock used to be labelled the literal "HKEX", which put
        them all in one bucket and made the industry percentile tier
        meaningless."""
        monkeypatch.setattr(ss, "_intl_universe", lambda m, **k: _universe("0"))
        industries = {i["industry"] for i in ss.get_hk_screener_stocks()["items"]}
        assert "HKEX" not in industries
        assert industries == {"Banks", "Software - Application"}

    def test_exchange_and_country_tagged(self, stubbed, monkeypatch):
        monkeypatch.setattr(ss, "_intl_universe", lambda m, **k: _universe("0"))
        item = ss.get_hk_screener_stocks()["items"][0]
        assert item["exchange"] == "HKEX"
        assert item["country"] == "HK"

    def test_empty_universe_degrades(self, stubbed, monkeypatch):
        monkeypatch.setattr(ss, "_intl_universe", lambda m, **k: ([], {}, {}, {}))
        assert ss.get_hk_screener_stocks()["total"] == 0


class TestVgpmScoring:
    def test_scores_are_computed(self, stubbed, monkeypatch):
        """A screener row without vgpm is the visible symptom of the metric
        pipeline failing, so assert the scores actually land."""
        monkeypatch.setattr(ss, "_intl_universe", lambda m, **k: _universe("0", n=20))
        items = ss.get_hk_screener_stocks()["items"]
        scored = [i for i in items if i.get("vgpm")]
        assert scored, "no HK row received a VGPM score"
        assert any(i.get("composite_score") is not None for i in scored)


class TestSharedMetricPipeline:
    def test_hk_and_sg_use_fetch_ticker_metrics(self, stubbed, monkeypatch):
        """Both markets must go through the same fetcher as US, or their
        scores are not comparable to anything."""
        seen: list[str] = []

        def spy(ticker, api_key=None, **kw):
            seen.append(ticker)
            return _metrics(ticker)

        monkeypatch.setattr(ss, "_fetch_ticker_metrics", spy)
        monkeypatch.setattr(ss, "_intl_universe", lambda m, **k: _universe("0", n=6))
        ss.get_hk_screener_stocks()
        assert len(seen) == 6
        seen.clear()
        monkeypatch.setattr(ss, "_intl_universe", lambda m, **k: _universe("D0", n=6))
        ss.get_sg_screener_stocks()
        assert len(seen) == 6


class TestIntlUniverseContract:
    """_intl_universe is the network boundary the tests above stub, so its
    own shape is asserted separately."""

    def test_per_sector_cap_matches_the_us_screener(self):
        import inspect
        sig = inspect.signature(ss._intl_universe)
        assert sig.parameters["per_sector_limit"].default == ss._PER_SECTOR_LIMIT
