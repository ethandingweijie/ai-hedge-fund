"""Tests for src/agents/dd/sector_clustering.py — Phase 2C clustering logic.

All tests mock TICKER_SECTOR_LOOKUP / FMP fallback to keep them fast and
hermetic. Pure-function tests on lookup_sector live separately."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from src.agents.dd.batch_quote import BatchQuote
from src.agents.dd.sector_clustering import (
    Cluster,
    ClusterResult,
    build_cluster_id,
    cluster_breaches,
    clear_sector_cache,
    lookup_sector,
)


def _q(ticker: str, pct: float, price: float = 100.0) -> BatchQuote:
    return BatchQuote(ticker=ticker, price=price, changes_percentage=pct, raw={})


@pytest.fixture(autouse=True)
def reset_cache():
    """Clear FMP fallback cache between tests so test order doesn't matter."""
    clear_sector_cache()


# ── lookup_sector ──────────────────────────────────────────────────────────


def test_lookup_sector_uses_TICKER_SECTOR_LOOKUP():
    """Hand-curated lookup hits before FMP fallback."""
    fake_lookup = {"NVDA": ("Semiconductor", "Fabless", "...", "...")}
    with patch("src.data.sector_profiles.TICKER_SECTOR_LOOKUP", fake_lookup):
        assert lookup_sector("NVDA") == "Semiconductor"
        assert lookup_sector("nvda") == "Semiconductor"   # case-insensitive


def test_lookup_sector_falls_back_to_fmp_for_unknowns():
    """Tickers absent from TICKER_SECTOR_LOOKUP get an FMP profile lookup."""
    with patch("src.data.sector_profiles.TICKER_SECTOR_LOOKUP", {}), \
         patch("src.tools.api._fmp_get",
               return_value=[{"symbol": "PYPL", "sector": "Financial Services"}]):
        assert lookup_sector("PYPL") == "Financial Services"


def test_lookup_sector_caches_fmp_result():
    """Second call doesn't re-hit FMP."""
    with patch("src.data.sector_profiles.TICKER_SECTOR_LOOKUP", {}), \
         patch("src.tools.api._fmp_get",
               return_value=[{"symbol": "PYPL", "sector": "Financial Services"}]) as mock_fmp:
        lookup_sector("PYPL")
        lookup_sector("PYPL")
        lookup_sector("PYPL")
    assert mock_fmp.call_count == 1


def test_lookup_sector_returns_none_when_fmp_fails():
    with patch("src.data.sector_profiles.TICKER_SECTOR_LOOKUP", {}), \
         patch("src.tools.api._fmp_get", return_value=None):
        assert lookup_sector("UNKNOWN") is None


def test_lookup_sector_returns_none_for_empty_input():
    assert lookup_sector("") is None
    assert lookup_sector(None) is None


# ── cluster_breaches ──────────────────────────────────────────────────────


def test_cluster_breaches_empty_input_returns_empty_result():
    result = cluster_breaches([])
    assert result.clusters == ()
    assert result.singletons == ()


def test_cluster_breaches_groups_by_sector_and_direction():
    """3 same-sector same-direction = 1 cluster."""
    breaches = [
        _q("NVDA", -0.12), _q("AMD", -0.11), _q("AVGO", -0.10),
    ]
    fake_sector = {
        "NVDA": ("Semiconductor", "", "", ""),
        "AMD":  ("Semiconductor", "", "", ""),
        "AVGO": ("Semiconductor", "", "", ""),
    }
    with patch("src.data.sector_profiles.TICKER_SECTOR_LOOKUP", fake_sector):
        result = cluster_breaches(breaches)
    assert len(result.clusters) == 1
    assert result.clusters[0].sector == "Semiconductor"
    assert result.clusters[0].direction == "DROP"
    assert result.clusters[0].n == 3
    assert result.singletons == ()


def test_cluster_breaches_two_members_stays_singleton():
    """Threshold is 3 — 2 members fall back to individual alerts."""
    breaches = [_q("NVDA", -0.12), _q("AMD", -0.11)]
    fake_sector = {
        "NVDA": ("Semiconductor", "", "", ""),
        "AMD":  ("Semiconductor", "", "", ""),
    }
    with patch("src.data.sector_profiles.TICKER_SECTOR_LOOKUP", fake_sector):
        result = cluster_breaches(breaches)
    assert result.clusters == ()
    assert len(result.singletons) == 2


def test_cluster_breaches_separate_directions_two_clusters():
    """Same sector, opposite directions → two clusters (allowed by design)."""
    breaches = [
        _q("BANK1", -0.11), _q("BANK2", -0.12), _q("BANK3", -0.10),
        _q("BANK4", 0.11),  _q("BANK5", 0.12),  _q("BANK6", 0.13),
    ]
    fake_sector = {f"BANK{i}": ("Banks", "", "", "") for i in range(1, 7)}
    with patch("src.data.sector_profiles.TICKER_SECTOR_LOOKUP", fake_sector):
        result = cluster_breaches(breaches)
    assert len(result.clusters) == 2
    directions = {c.direction for c in result.clusters}
    assert directions == {"DROP", "PUMP"}


def test_cluster_breaches_unclassifiable_stay_singleton():
    """Tickers with no sector (FMP also fails) become singletons."""
    breaches = [_q("ZZZZ", -0.15), _q("YYYY", -0.12)]
    with patch("src.data.sector_profiles.TICKER_SECTOR_LOOKUP", {}), \
         patch("src.tools.api._fmp_get", return_value=None):
        result = cluster_breaches(breaches)
    assert result.clusters == ()
    assert len(result.singletons) == 2


def test_cluster_breaches_mixed_clustered_and_singleton():
    """3 in tech cluster + 1 unclassifiable + 1 lone-name in another sector."""
    breaches = [
        _q("CRM", -0.11), _q("NOW", -0.12), _q("NET", -0.13),  # Tech cluster
        _q("XOM", -0.10),                                       # Energy singleton
        _q("UNKN", -0.14),                                      # No sector → singleton
    ]
    fake_sector = {
        "CRM": ("Tech", "", "", ""),
        "NOW": ("Tech", "", "", ""),
        "NET": ("Tech", "", "", ""),
        "XOM": ("Energy", "", "", ""),
    }
    with patch("src.data.sector_profiles.TICKER_SECTOR_LOOKUP", fake_sector), \
         patch("src.tools.api._fmp_get", return_value=None):
        result = cluster_breaches(breaches)
    assert len(result.clusters) == 1
    assert result.clusters[0].sector == "Tech"
    assert {m.ticker for m in result.clusters[0].members} == {"CRM", "NOW", "NET"}
    # Singletons: XOM (lone in Energy) + UNKN (unclassifiable)
    singleton_tickers = {s.ticker for s in result.singletons}
    assert singleton_tickers == {"XOM", "UNKN"}


def test_cluster_members_sorted_by_abs_magnitude():
    """Most extreme breach in each cluster comes first."""
    breaches = [
        _q("NVDA", -0.10), _q("AMD", -0.20), _q("AVGO", -0.15),
    ]
    fake_sector = {
        "NVDA": ("Semiconductor", "", "", ""),
        "AMD":  ("Semiconductor", "", "", ""),
        "AVGO": ("Semiconductor", "", "", ""),
    }
    with patch("src.data.sector_profiles.TICKER_SECTOR_LOOKUP", fake_sector):
        result = cluster_breaches(breaches)
    assert [m.ticker for m in result.clusters[0].members] == ["AMD", "AVGO", "NVDA"]


def test_cluster_min_members_configurable_via_env(monkeypatch):
    """DD_CLUSTER_MIN_MEMBERS=2 lets pairs cluster."""
    monkeypatch.setenv("DD_CLUSTER_MIN_MEMBERS", "2")
    breaches = [_q("NVDA", -0.12), _q("AMD", -0.11)]
    fake_sector = {
        "NVDA": ("Semiconductor", "", "", ""),
        "AMD":  ("Semiconductor", "", "", ""),
    }
    with patch("src.data.sector_profiles.TICKER_SECTOR_LOOKUP", fake_sector):
        result = cluster_breaches(breaches)
    assert len(result.clusters) == 1
    assert result.clusters[0].n == 2


def test_cluster_median_pct_correct():
    breaches = [
        _q("A", -0.10), _q("B", -0.15), _q("C", -0.20),
    ]
    fake_sector = {t: ("Tech", "", "", "") for t in "ABC"}
    with patch("src.data.sector_profiles.TICKER_SECTOR_LOOKUP", fake_sector):
        result = cluster_breaches(breaches)
    assert result.clusters[0].median_pct == pytest.approx(-0.15)


# ── build_cluster_id ───────────────────────────────────────────────────────


def test_build_cluster_id_format():
    cid = build_cluster_id("Tech", "DROP")
    # tech_drop_YYYY-MM-DD
    assert cid.startswith("tech_drop_")
    parts = cid.split("_")
    assert len(parts) == 3
    assert parts[0] == "tech"
    assert parts[1] == "drop"
    # parts[2] is the ISO date


def test_build_cluster_id_normalizes_sector_name():
    """Slashes and spaces become underscores so the id is filename/url safe."""
    cid = build_cluster_id("Consumer Discretionary", "PUMP")
    assert "consumer_discretionary" in cid


def test_build_cluster_id_distinguishes_directions():
    drop_id = build_cluster_id("Tech", "DROP")
    pump_id = build_cluster_id("Tech", "PUMP")
    assert drop_id != pump_id
    assert "drop" in drop_id and "pump" in pump_id
