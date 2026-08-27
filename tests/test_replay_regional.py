"""
tests/test_replay_regional.py
=============================
W4 gates — the crisis engine after it stopped being US-only.

The defect this covers: event_library carried SPY/QQQ benchmarks and the 11
US SPDR sector ETFs and nothing else, so what_if._sector_anchor mapped an
HKEX holding to a US sector return. That is not a rounding error. Through
the 2021-22 China regulatory and property crackdown the Hang Seng fell 52.8%
while the S&P fell 1.6%; through the 1997-98 Asian Financial Crisis the Hang
Seng fell 47.6% and the Straits Times 52.3% while the S&P ROSE 13.9%. A
portfolio with Asian exposure was being scored against the wrong tape.

Offline — all assertions are against the curated library and pure functions.
"""
from __future__ import annotations

import pytest

from src.portfolio import what_if as wi
from src.portfolio.event_library import (
    EVENTS, LIBRARY_VERSION, REGIONAL_BENCHMARKS, REGIONS, EventSpec,
    MacroSnapshot, SectorPerf, get_event, regions_covered, sectors_for_region,
)

ASIA_EVENTS = ("asian_fc_1997", "china_crash_2015", "china_crackdown_2021")


# ── Library shape ───────────────────────────────────────────────────────────

class TestLibraryVersion:
    def test_version_bumped(self):
        """snapshot_hash bakes LIBRARY_VERSION, so cached replays from the
        US-only library must miss and recompute."""
        assert LIBRARY_VERSION >= 3

    def test_version_change_invalidates_the_replay_cache(self, monkeypatch):
        from src.portfolio import replay as rp
        holdings = [{"ticker": "A", "quantity": 1.0, "avg_cost": 10.0}]
        before = rp.snapshot_hash(holdings)
        monkeypatch.setattr(rp, "LIBRARY_VERSION", LIBRARY_VERSION + 1)
        assert rp.snapshot_hash(holdings) != before


class TestAsiaEvents:
    @pytest.mark.parametrize("key", ASIA_EVENTS)
    def test_event_exists(self, key):
        assert get_event(key) is not None, f"{key} missing from the library"

    def test_total_event_count(self):
        assert len(EVENTS) == 10, "7 original + 3 Asia events"

    def test_keys_are_unique(self):
        keys = [e.key for e in EVENTS]
        assert len(keys) == len(set(keys))

    @pytest.mark.parametrize("key", ASIA_EVENTS)
    def test_carries_regional_benchmarks(self, key):
        ev = get_event(key)
        assert ev.regional, f"{key}: no regional benchmarks"
        for label, row in ev.regional.items():
            assert label in REGIONAL_BENCHMARKS
            assert row["return_pct"] is not None
            assert -100.0 < row["return_pct"] < 200.0
            assert row["max_dd_pct"] <= 0.0

    def test_asian_fc_is_orthogonal_to_the_us_tape(self):
        """The single clearest reason these events had to be added: a
        catastrophic Asian crisis during which the US market rose."""
        ev = get_event("asian_fc_1997")
        assert ev.spy_return_pct > 0, "SPY rose through the AFC"
        assert ev.regional["HSI"]["return_pct"] < -35.0
        assert ev.regional["STI"]["return_pct"] < -35.0

    def test_china_crackdown_is_orthogonal_to_the_us_tape(self):
        ev = get_event("china_crackdown_2021")
        assert ev.regional["HSI"]["return_pct"] < -40.0
        # The US drawdown over the same window was an order of magnitude
        # smaller — a US-anchored model cannot see this risk at all.
        assert ev.spy_return_pct > ev.regional["HSI"]["return_pct"] + 30.0

    def test_asian_fc_has_no_hk_sector_baskets(self):
        """Honest coverage: FMP's HK single-name history begins 2000-01-03,
        so there is no way to build an HK constituent basket for 1997-98.
        The rows must be ABSENT, never zero-filled."""
        ev = get_event("asian_fc_1997")
        assert sectors_for_region(ev, "HK") == ()

    def test_windows_are_ordered(self):
        for e in EVENTS:
            assert e.start < e.end, f"{e.key}: window inverted"


class TestRegionalSectorRows:
    def test_modern_events_have_hk_baskets(self):
        """Events inside FMP's HK coverage must actually carry HK rows —
        otherwise the region-aware anchor silently degrades to US."""
        for key in ("gfc_2008", "covid_2020", "rate_shock_2022",
                    "china_crackdown_2021"):
            ev = get_event(key)
            assert sectors_for_region(ev, "HK"), f"{key}: no HK sector rows"

    def test_regions_covered_reports_honestly(self):
        """regions_covered must list exactly the regions that actually have
        rows — including the empty case for asian_fc_1997, where no sector
        data source reaches back that far."""
        for e in EVENTS:
            covered = regions_covered(e)
            for r in covered:
                assert r in REGIONS
                assert sectors_for_region(e, r)
            for r in REGIONS:
                if r not in covered:
                    assert sectors_for_region(e, r) == ()
            if e.key == "asian_fc_1997":
                assert covered == ()
            else:
                assert "US" in covered

    def test_regional_rows_declare_their_basis(self):
        """HK/SG rows are constituent baskets carrying survivorship bias, and
        must not be presentable as index returns."""
        for e in EVENTS:
            for region in ("HK", "SG"):
                for s in sectors_for_region(e, region):
                    assert s.basis == "constituent_basket"
                    assert s.constituents >= 5

    def test_events_with_baskets_carry_the_caveat(self):
        for e in EVENTS:
            if sectors_for_region(e, "HK") or sectors_for_region(e, "SG"):
                assert e.caveats, f"{e.key}: basket rows without a caveat"
                assert any("survivorship" in c.lower() for c in e.caveats)


# ── Region-aware anchoring ──────────────────────────────────────────────────

class TestTickerRegion:
    @pytest.mark.parametrize("ticker,expected", [
        ("00700.HK", "HK"), ("0700.HK", "HK"), ("9988", "HK"),
        ("D05.SI", "SG"), ("D05", "SG"),
        ("AAPL", "US"), ("MSFT", "US"),
    ])
    def test_region(self, ticker, expected):
        assert wi.ticker_region(ticker) == expected


def _event(**kw) -> EventSpec:
    base = dict(
        key="synthetic", name="Synthetic", start="2020-01-01", end="2020-01-31",
        spy_return_pct=-10.0, spy_max_dd_pct=-10.0,
        qqq_return_pct=-15.0, qqq_max_dd_pct=-15.0,
        macro=MacroSnapshot("risk-off", "hiking", "rising", "high", "medium"),
    )
    base.update(kw)
    return EventSpec(**base)


class TestSectorAnchor:
    SECTORS = (
        SectorPerf("Technology", "XLK", -20.0, region="US"),
        SectorPerf("Technology", "HK:Technology", -55.0, region="HK",
                   basis="constituent_basket", constituents=12),
    )

    def test_hk_holding_uses_the_hk_row(self):
        ev = _event(sectors=self.SECTORS)
        assert wi._sector_anchor(ev, "Technology", "HK") == (-55.0, "HK")

    def test_us_holding_uses_the_us_row(self):
        ev = _event(sectors=self.SECTORS)
        assert wi._sector_anchor(ev, "Technology", "US") == (-20.0, "US")

    def test_falls_back_to_us_when_the_region_has_no_row(self):
        """asian_fc_1997 for an HK holding: no HK basket exists, so the US
        row serves — and the caller is told it did."""
        ev = _event(sectors=(self.SECTORS[0],))
        assert wi._sector_anchor(ev, "Technology", "HK") == (-20.0, "US")

    def test_none_when_no_row_anywhere(self):
        ev = _event(sectors=self.SECTORS)
        assert wi._sector_anchor(ev, "Utilities", "HK") is None

    def test_none_without_event_or_sector(self):
        assert wi._sector_anchor(None, "Technology", "HK") is None
        assert wi._sector_anchor(_event(), None, "HK") is None


class TestRegionalBroadAnchor:
    def test_uses_the_home_index(self):
        ev = _event(regional={"HSI": {"return_pct": -52.8, "max_dd_pct": -55.0}})
        assert wi._regional_anchor(ev, "HK") == pytest.approx(-52.8)

    def test_us_has_no_regional_anchor(self):
        ev = _event(regional={"HSI": {"return_pct": -52.8}})
        assert wi._regional_anchor(ev, "US") is None

    def test_missing_index_is_none(self):
        assert wi._regional_anchor(_event(), "HK") is None


class TestSkeletonRouting:
    """End-to-end through _build_skeleton: the behaviour that was wrong."""

    def _run(self, tickers_sectors, ev):
        holdings = [{"ticker": t, "quantity": 1.0, "avg_cost": 100.0}
                    for t, _ in tickers_sectors]
        sectors_map = {t: s for t, s in tickers_sectors}
        return wi._build_skeleton(holdings, ev, sectors_map, None, 30, {})

    def test_hk_and_us_holdings_diverge(self):
        ev = _event(sectors=(
            SectorPerf("Technology", "XLK", -20.0, region="US"),
            SectorPerf("Technology", "HK:Technology", -55.0, region="HK",
                       basis="constituent_basket", constituents=12),
        ))
        out = self._run([("AAPL", "Technology"), ("00700.HK", "Technology")], ev)
        by = {r["ticker"]: r for r in out["holdings"]}
        assert by["AAPL"]["est_impact_pct"] == pytest.approx(-20.0)
        assert by["00700.HK"]["est_impact_pct"] == pytest.approx(-55.0)
        assert by["00700.HK"]["region"] == "HK"
        assert by["00700.HK"]["anchor_region"] == "HK"
        assert by["00700.HK"]["anchor_region_fallback"] is False

    def test_fallback_to_home_index_before_spy(self):
        """No HK sector basket for this sector, but the Hang Seng return is
        known — anchoring to the S&P would be the wrong tape."""
        ev = _event(
            sectors=(SectorPerf("Utilities", "XLU", -5.0, region="US"),),
            regional={"HSI": {"return_pct": -52.8, "max_dd_pct": -55.0}},
        )
        out = self._run([("00001.HK", "Real Estate")], ev)
        row = out["holdings"][0]
        assert row["est_impact_pct"] == pytest.approx(-52.8)
        assert row["anchor_region"] == "HK"

    def test_us_fallback_is_flagged(self):
        ev = _event(sectors=(SectorPerf("Technology", "XLK", -20.0, region="US"),))
        out = self._run([("00700.HK", "Technology")], ev)
        row = out["holdings"][0]
        assert row["anchor_region"] == "US"
        assert row["anchor_region_fallback"] is True, \
            "a US-anchored HK holding must be visibly flagged"

    def test_inverse_products_stay_on_their_us_underlying(self):
        """MUD is -1x Micron and CORD is -2x CoreWeave — both US exposures
        regardless of where the wrapper lists."""
        ev = _event(sectors=(
            SectorPerf("Technology", "XLK", -20.0, region="US"),
            SectorPerf("Technology", "HK:Technology", -55.0, region="HK",
                       basis="constituent_basket", constituents=12),
        ))
        out = self._run([("MUD", "Technology")], ev)
        row = out["holdings"][0]
        assert row["kind"] == "product"
        assert row["anchor_pct"] == pytest.approx(-20.0), "US underlying"

    def test_anchors_payload_exposes_regional(self):
        ev = _event(regional={"HSI": {"return_pct": -52.8, "max_dd_pct": -55.0}})
        out = self._run([("AAPL", "Technology")], ev)
        assert out["anchors"]["regional"]["HSI"]["return_pct"] == -52.8


class TestScenarioVersion:
    def test_bumped_for_region_awareness(self):
        assert wi.SCENARIO_VERSION >= 8

    def test_version_is_in_the_scenario_hash(self, monkeypatch):
        from app.backend.services import portfolio_service as ps
        holdings = [{"ticker": "AAPL", "quantity": 1.0, "avg_cost": 100.0}]
        args = dict(category="Custom", concerns="x", reference_key=None,
                    horizon_days=30, search_override="never")
        before = ps.compute_scenario_hash(holdings=holdings, **args)
        monkeypatch.setattr(wi, "SCENARIO_VERSION", wi.SCENARIO_VERSION + 1)
        assert ps.compute_scenario_hash(holdings=holdings, **args) != before


# ── Replay engine ───────────────────────────────────────────────────────────

class TestReplayHomeBenchmark:
    @pytest.mark.parametrize("ticker,expected", [
        ("00700.HK", "HSI"), ("D05.SI", "STI"), ("AAPL", None),
    ])
    def test_home_benchmark_selection(self, ticker, expected):
        from src.portfolio import replay as rp
        assert rp._home_benchmark(ticker) == expected
