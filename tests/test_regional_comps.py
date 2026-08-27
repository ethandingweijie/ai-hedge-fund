"""
tests/test_regional_comps.py
============================
Cover for src/data/regional_comps.py — live HK/SG industry and sector comps.

Two things must hold:

  * The resolution ladder degrades honestly. HKEX supports industry-level
    medians for most of its 139 industries; SGX supports them for about 7 of
    69, so Singapore names must land on sector without ever silently
    presenting a 2-peer reading as a comp set.
  * The universe filters keep cross-listings out. HK's RMB dual-counters
    (80700.HK is the same company as 0700.HK) would double-weight; SGX's HK
    depositary receipts — HBND.SI is Bank of China and the largest "SGX"
    name by market cap — would drag Singapore bank comps toward mainland
    Chinese multiples.

Offline: every FMP call is stubbed and the store is a tmp SQLite file.
"""
from __future__ import annotations

import pytest

from src.data import regional_comps as rc


# ── Name normalisation and de-duplication ───────────────────────────────────

class TestNormalizeName:
    @pytest.mark.parametrize("a,b", [
        ("Tencent Holdings Limited", "Tencent Holdings Ltd"),
        ("Bank of China Limited", "BANK OF CHINA LTD."),
        ("Ping An Insurance (Group) Company", "Ping An Insurance Group Co"),
    ])
    def test_equivalent_names_collapse(self, a, b):
        assert rc.normalize_name(a) == rc.normalize_name(b)

    def test_distinct_companies_stay_distinct(self):
        assert rc.normalize_name("Bank of China") != rc.normalize_name("Bank of East Asia")

    def test_empty(self):
        assert rc.normalize_name(None) == ""
        assert rc.normalize_name("   ") == ""


class TestDedupeUniverse:
    def test_rmb_dual_counter_is_dropped(self):
        """80700.HK and 0700.HK are one company; keeping both would give
        Tencent two votes in every median it appears in."""
        rows = [
            {"symbol": "0700.HK", "name": "Tencent Holdings Limited",
             "sector": "Technology", "industry": "Internet", "market_cap": 4e12},
            {"symbol": "80700.HK", "name": "Tencent Holdings Limited",
             "sector": "Technology", "industry": "Internet", "market_cap": 3.4e12},
        ]
        out = rc.dedupe_universe(rows)
        assert [r["symbol"] for r in out] == ["0700.HK"], "keeps the larger listing"

    def test_cross_listing_exclusion(self):
        """An SGX depositary receipt over an HK-primary company is dropped
        from the Singapore comp set."""
        sg = [
            {"symbol": "HBND.SI", "name": "Bank of China Limited",
             "sector": "Financial Services", "industry": "Banks - Diversified",
             "market_cap": 3.8e11},
            {"symbol": "D05.SI", "name": "DBS Group Holdings Ltd",
             "sector": "Financial Services", "industry": "Banks",
             "market_cap": 2.1e11},
        ]
        hk_names = {rc.normalize_name("Bank of China Limited")}
        out = rc.dedupe_universe(sg, exclude_names=hk_names)
        assert [r["symbol"] for r in out] == ["D05.SI"]

    def test_unnamed_rows_dropped(self):
        out = rc.dedupe_universe([{"symbol": "X.SI", "name": "",
                                   "sector": "", "industry": "", "market_cap": 1e9}])
        assert out == []


# ── Basket construction ─────────────────────────────────────────────────────

class TestBuildBaskets:
    def _universe(self, n=30):
        return [
            {"symbol": f"{i:04d}.HK", "name": f"Co {i}",
             "sector": "Technology" if i % 2 == 0 else "Financial Services",
             "industry": "Software - Application" if i % 2 == 0 else "Banks",
             "market_cap": 1e12 - i}
            for i in range(n)
        ]

    def test_basket_sizes_are_capped(self):
        ind, sec, syms = rc.build_baskets(self._universe(60))
        assert all(len(v) <= rc.INDUSTRY_BASKET_SIZE for v in ind.values())
        assert all(len(v) <= rc.SECTOR_BASKET_SIZE for v in sec.values())

    def test_baskets_take_the_largest_names(self):
        ind, _, _ = rc.build_baskets(self._universe(60))
        caps = [r["market_cap"] for r in ind["Banks"]]
        assert caps == sorted(caps, reverse=True)

    def test_symbols_are_the_deduped_union(self):
        ind, sec, syms = rc.build_baskets(self._universe(60))
        assert len(syms) == len(set(syms)), "each name fetched once"
        union = ({r["symbol"] for g in ind.values() for r in g}
                 | {r["symbol"] for g in sec.values() for r in g})
        assert set(syms) == union

    def test_blank_classification_skipped(self):
        ind, sec, _ = rc.build_baskets(
            [{"symbol": "X.HK", "name": "X", "sector": "", "industry": "",
              "market_cap": 1e9}])
        assert ind == {} and sec == {}


# ── Outlier handling ────────────────────────────────────────────────────────

class TestClean:
    def test_drops_none_and_nan(self):
        assert rc._clean("pe", [None, float("nan"), float("inf"), 15.0]) == [15.0]

    def test_drops_negative_pe(self):
        """A negative P/E is a loss-maker, not a cheap stock — it must not
        drag a comp median downward."""
        assert rc._clean("pe", [-30.0, 12.0, 18.0]) == [12.0, 18.0]

    def test_drops_absurd_multiples(self):
        assert rc._clean("pe", [10.0, 5000.0]) == [10.0]
        assert rc._clean("ev_ebitda", [8.0, 900.0]) == [8.0]

    def test_negative_fcf_yield_is_kept(self):
        """Unlike P/E, a negative FCF yield is meaningful and in-band."""
        assert rc._clean("fcf_yield", [-0.03, 0.05]) == [-0.03, 0.05]

    def test_every_field_has_a_band(self):
        assert set(rc.FIELDS) == set(rc._BANDS)


class TestComputeMedians:
    def _metrics(self, values):
        return {f"S{i}": {"pe": v} for i, v in enumerate(values)}

    def _basket(self, n):
        return {"Banks": [{"symbol": f"S{i}"} for i in range(n)]}

    def test_median_not_mean(self):
        """One surviving depositary receipt must not move the reading."""
        vals = [12.0, 13.0, 14.0, 15.0, 16.0, 4.0]   # 4.0 = a mainland bank DR
        rows = rc.compute_medians(self._basket(6), self._metrics(vals),
                                  "industry", rc.MIN_INDUSTRY_PEERS)
        pe = [r for r in rows if r["field"] == "pe" and r["cohort"] == "all"][0]
        assert pe["value"] == pytest.approx(13.5)
        assert pe["peer_count"] == 6

    def test_below_peer_floor_is_omitted_not_returned_weak(self):
        rows = rc.compute_medians(self._basket(3), self._metrics([12.0, 13.0, 14.0]),
                                  "industry", rc.MIN_INDUSTRY_PEERS)
        assert rows == []

    def test_sector_floor_is_stricter_than_industry(self):
        assert rc.MIN_SECTOR_PEERS > rc.MIN_INDUSTRY_PEERS

    def test_peer_count_reflects_usable_values_not_basket_size(self):
        """Basket of 8, but two have no P/E — peer_count must say 6."""
        metrics = {f"S{i}": {"pe": 12.0 + i} for i in range(6)}
        metrics["S6"] = {"pe": None}
        metrics["S7"] = {}
        rows = rc.compute_medians(self._basket(8), metrics, "industry",
                                  rc.MIN_INDUSTRY_PEERS)
        pe = [r for r in rows if r["field"] == "pe" and r["cohort"] == "all"]
        assert pe[0]["peer_count"] == 6


# ── Store + ladder ──────────────────────────────────────────────────────────

@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("RUN_ARCHIVE_PATH", str(tmp_path / "t.db"))
    from src.data import db as _db
    monkeypatch.setattr(_db, "get_db_path", lambda: str(tmp_path / "t.db"))
    _db.close_all_connections()
    monkeypatch.setattr(rc, "_tables_ready_key", None, raising=False)
    yield
    _db.close_all_connections()


def _seed(exchange, level, key, field, value, peers,
          cohort="all", min_market_cap=0.0):
    from datetime import datetime, timezone
    rc.save_comps(exchange, [{"level": level, "key": key, "cohort": cohort,
                              "field": field, "value": value,
                              "peer_count": peers,
                              "min_market_cap": min_market_cap}],
                  datetime.now(timezone.utc).isoformat())


class TestLadder:
    def test_industry_wins_when_it_clears_the_floor(self, store):
        _seed("HKSE", "industry", "Banks", "pe", 7.5, 9)
        _seed("HKSE", "sector", "Financial Services", "pe", 9.0, 20)
        got = rc.get_regional_multiples("HKSE", "Banks", "Financial Services")
        assert got["pe"]["value"] == 7.5
        assert got["pe"]["basis"] == "industry"
        assert got["pe"]["peer_count"] == 9

    def test_falls_to_sector_when_industry_is_thin(self, store):
        """The SGX case: too few industry peers, so the sector median serves."""
        _seed("SES", "industry", "Banks", "pe", 7.5, 3)
        _seed("SES", "sector", "Financial Services", "pe", 18.0, 20)
        got = rc.get_regional_multiples("SES", "Banks", "Financial Services")
        assert got["pe"]["value"] == 18.0
        assert got["pe"]["basis"] == "sector"

    def test_absent_when_neither_rung_qualifies(self, store):
        """Caller keeps its static value, and can tell that it did."""
        _seed("SES", "industry", "Banks", "pe", 7.5, 2)
        _seed("SES", "sector", "Financial Services", "pe", 18.0, 4)
        assert rc.get_regional_multiples("SES", "Banks", "Financial Services") == {}

    def test_resolution_is_per_field(self, store):
        """A field with enough industry peers uses industry even while a
        neighbouring field falls back to sector."""
        _seed("HKSE", "industry", "Banks", "pe", 7.5, 9)
        _seed("HKSE", "industry", "Banks", "ev_ebitda", 6.0, 2)
        _seed("HKSE", "sector", "Financial Services", "ev_ebitda", 8.5, 15)
        got = rc.get_regional_multiples("HKSE", "Banks", "Financial Services")
        assert got["pe"]["basis"] == "industry"
        assert got["ev_ebitda"]["basis"] == "sector"

    def test_exchanges_are_isolated(self, store):
        _seed("HKSE", "sector", "Financial Services", "pe", 7.7, 20)
        got = rc.get_regional_multiples("SES", None, "Financial Services")
        assert got == {}, "HK comps must never serve an SG lookup"

    def test_stale_rows_are_ignored(self, store):
        rc.save_comps("HKSE", [{"level": "sector", "key": "Financial Services",
                                "field": "pe", "value": 7.7, "peer_count": 20}],
                      "2020-01-01T00:00:00+00:00")
        assert rc.get_regional_multiples("HKSE", None, "Financial Services") == {}

    def test_empty_store_is_empty_not_an_error(self, store):
        assert rc.get_regional_multiples("HKSE", "Banks", "Financial Services") == {}

    def test_upsert_replaces_not_duplicates(self, store):
        _seed("HKSE", "sector", "Financial Services", "pe", 7.7, 20)
        _seed("HKSE", "sector", "Financial Services", "pe", 8.1, 21)
        got = rc.get_regional_multiples("HKSE", None, "Financial Services")
        assert got["pe"]["value"] == 8.1
        assert got["pe"]["peer_count"] == 21


# ── Integration with the valuation table ────────────────────────────────────

class TestSectorProfilesWiring:
    def test_live_comps_override_static_field_by_field(self, store, monkeypatch):
        from src.data import sector_profiles as sp
        _seed("HKSE", "industry", "Internet Content & Information", "pe", 21.0, 11)
        monkeypatch.setattr(
            "src.data.regional_comps.get_fmp_classification",
            lambda t: {"exchange": "HKSE", "sector": "Communication Services",
                       "industry": "Internet Content & Information"},
        )
        got = sp.get_sector_peer_multiples("Tech", is_hk=True, ticker="00700.HK")
        assert got["pe"] == 21.0, "live industry median must win"
        assert got["ev_ebitda"] == sp.HK_SECTOR_PEER_MULTIPLES["Tech"]["ev_ebitda"], \
            "untouched fields keep their static value"
        assert got["_comp_basis"]["pe"]["basis"] == "industry"
        assert "ev_ebitda" not in got["_comp_basis"]

    def test_static_table_when_nothing_stored(self, store, monkeypatch):
        from src.data import sector_profiles as sp
        monkeypatch.setattr(
            "src.data.regional_comps.get_fmp_classification",
            lambda t: {"exchange": "HKSE", "sector": "Technology",
                       "industry": "Software - Application"},
        )
        got = sp.get_sector_peer_multiples("Tech", is_hk=True, ticker="00700.HK")
        assert got == sp.HK_SECTOR_PEER_MULTIPLES["Tech"]
        assert "_comp_basis" not in got

    def test_us_path_is_untouched(self, store):
        """No ticker, not HK — the US dynamic/static merge must be unchanged."""
        from src.data import sector_profiles as sp
        got = sp.get_sector_peer_multiples("Tech")
        assert "_comp_basis" not in got
        assert got.get("pe") is not None

    def test_basis_key_does_not_disturb_numeric_consumers(self, store, monkeypatch):
        """_comp_basis is underscore-prefixed and non-numeric; anything that
        iterates the dict must be able to skip it."""
        from src.data import sector_profiles as sp
        _seed("HKSE", "sector", "Communication Services", "pe", 21.0, 15)
        monkeypatch.setattr(
            "src.data.regional_comps.get_fmp_classification",
            lambda t: {"exchange": "HKSE", "sector": "Communication Services",
                       "industry": "X"},
        )
        got = sp.get_sector_peer_multiples("Tech", is_hk=True, ticker="00700.HK")
        numeric = {k: v for k, v in got.items() if not k.startswith("_")}
        assert all(isinstance(v, (int, float)) for v in numeric.values())


# ── Size cohorts ────────────────────────────────────────────────────────────

class TestSizeCohorts:
    """The HKEX failure this exists to prevent: the "Internet Content &
    Information" basket spans Tencent (HK$4.1tn) to Inkeverse (HK$1.7bn), so
    its whole-industry median lands on de-rated micro-caps at ~8x earnings.
    Valuing Tencent off that would roughly halve its intrinsic value."""

    def _members(self, n, top_cap=4.0e12):
        return [{"symbol": f"S{i}", "market_cap": top_cap / (10 ** i)}
                for i in range(n)]

    def test_split_produces_upper_half(self):
        cohorts = rc.split_cohorts(self._members(20))
        assert set(cohorts) == {"all", "large"}
        assert len(cohorts["all"]) == 20
        assert len(cohorts["large"]) == 10
        assert cohorts["large"][0]["symbol"] == "S0", "largest names"

    def test_small_basket_has_no_large_cohort(self):
        """Halving below the peer floor would produce a cohort that could
        never qualify — omit it rather than store an unusable row."""
        assert set(rc.split_cohorts(self._members(6))) == {"all"}

    def test_large_cohort_median_differs_from_all(self):
        members = self._members(20, top_cap=1.0e12)
        # Big names rate highly, small ones are de-rated.
        metrics = {m["symbol"]: {"pe": 25.0 if i < 10 else 8.0}
                   for i, m in enumerate(members)}
        rows = rc.compute_medians({"Internet": members}, metrics,
                                  "industry", rc.MIN_INDUSTRY_PEERS)
        by = {r["cohort"]: r["value"] for r in rows if r["field"] == "pe"}
        assert by["large"] == pytest.approx(25.0)
        assert by["all"] < by["large"], "whole-industry median is dragged down"

    def test_large_cohort_records_its_floor(self):
        members = self._members(20, top_cap=1.0e12)
        metrics = {m["symbol"]: {"pe": 20.0} for m in members}
        rows = rc.compute_medians({"X": members}, metrics, "industry",
                                  rc.MIN_INDUSTRY_PEERS)
        large = [r for r in rows if r["cohort"] == "large"][0]
        assert large["min_market_cap"] == pytest.approx(1.0e12 / (10 ** 9))


class TestCohortLadder:
    def test_large_target_gets_the_large_cohort(self, store):
        _seed("HKSE", "industry", "Internet", "pe", 8.0, 12, cohort="all")
        _seed("HKSE", "industry", "Internet", "pe", 25.0, 6,
              cohort="large", min_market_cap=5.0e10)
        got = rc.get_regional_multiples("HKSE", "Internet", "Communication Services",
                                        market_cap=4.0e12)
        assert got["pe"]["value"] == 25.0
        assert got["pe"]["cohort"] == "large"

    def test_small_target_gets_the_whole_industry(self, store):
        _seed("HKSE", "industry", "Internet", "pe", 8.0, 12, cohort="all")
        _seed("HKSE", "industry", "Internet", "pe", 25.0, 6,
              cohort="large", min_market_cap=5.0e10)
        got = rc.get_regional_multiples("HKSE", "Internet", "Communication Services",
                                        market_cap=2.0e9)
        assert got["pe"]["value"] == 8.0
        assert got["pe"]["cohort"] == "all"

    def test_without_market_cap_the_large_rung_is_skipped(self, store):
        """A caller that cannot supply a market cap must not be handed a
        large-cap comp set it has no basis for."""
        _seed("HKSE", "industry", "Internet", "pe", 25.0, 6,
              cohort="large", min_market_cap=5.0e10)
        _seed("HKSE", "industry", "Internet", "pe", 8.0, 12, cohort="all")
        got = rc.get_regional_multiples("HKSE", "Internet", "Communication Services")
        assert got["pe"]["cohort"] == "all"

    def test_falls_through_cohort_then_level(self, store):
        """Large industry cohort too thin -> whole industry too thin ->
        sector."""
        _seed("HKSE", "industry", "Internet", "pe", 25.0, 2, cohort="large",
              min_market_cap=5.0e10)
        _seed("HKSE", "industry", "Internet", "pe", 8.0, 3, cohort="all")
        _seed("HKSE", "sector", "Communication Services", "pe", 10.3, 17,
              cohort="all")
        got = rc.get_regional_multiples("HKSE", "Internet", "Communication Services",
                                        market_cap=4.0e12)
        assert got["pe"]["value"] == 10.3
        assert got["pe"]["basis"] == "sector"


# ── US market ───────────────────────────────────────────────────────────────

class TestUsMarket:
    """US pools NASDAQ/NYSE/AMEX into one universe. Where a company chose to
    list is not an economic distinction, and pooling gives industry baskets
    deep enough to clear the peer floor."""

    def test_exchange_to_market_mapping(self):
        for code in ("NASDAQ", "NYSE", "AMEX", "nasdaq"):
            assert rc.market_for_exchange(code) == "US"
        assert rc.market_for_exchange("HKSE") == "HKSE"
        assert rc.market_for_exchange("SES") == "SES"

    def test_unknown_exchange_is_none(self):
        assert rc.market_for_exchange("LSE") is None
        assert rc.market_for_exchange(None) is None
        assert rc.market_for_exchange("") is None

    def test_us_is_a_supported_market(self):
        assert "US" in rc.EXCHANGES
        assert rc.MARKETS["US"] == ("NASDAQ", "NYSE", "AMEX")

    def test_hk_and_sg_stay_separate(self):
        """Pooling HK into SG would leak mainland China risk pricing into
        Singapore comps — the thing the DR filter exists to prevent."""
        assert rc.MARKETS["HKSE"] == ("HKSE",)
        assert rc.MARKETS["SES"] == ("SES",)

    def test_us_ladder_resolves(self, store):
        _seed("US", "industry", "Consumer Electronics", "pe", 31.5, 9)
        got = rc.get_regional_multiples("US", "Consumer Electronics", "Technology")
        assert got["pe"]["value"] == 31.5
        assert got["pe"]["basis"] == "industry"

    def test_us_store_is_isolated_from_hk(self, store):
        _seed("HKSE", "industry", "Consumer Electronics", "pe", 9.0, 9)
        assert rc.get_regional_multiples("US", "Consumer Electronics", "Technology") == {}


class TestLayering:
    """static <- curated-basket dynamic (US only) <- measured exchange comps."""

    def test_regional_beats_dynamic_and_static(self, store, monkeypatch):
        from src.data import sector_profiles as sp
        _seed("US", "industry", "Consumer Electronics", "pe", 31.5, 9)
        monkeypatch.setattr(
            "src.data.regional_comps.get_fmp_classification",
            lambda t: {"exchange": "NASDAQ", "sector": "Technology",
                       "industry": "Consumer Electronics"})
        monkeypatch.setattr(sp, "get_dynamic_peer_multiples",
                            lambda sector, profile: {"pe": 44.0, "pb": 7.0})
        got = sp.get_sector_peer_multiples("Tech", ticker="AAPL", market_cap=4e12)
        assert got["pe"] == 31.5, "measured industry comp must win"
        assert got["pb"] == 7.0, "dynamic still fills a field regional missed"
        assert got["_comp_basis"]["pe"]["basis"] == "industry"

    def test_dynamic_is_not_consulted_for_hk(self, store, monkeypatch):
        """HK has no curated KG baskets; consulting them would splice US
        multiples into an HK valuation."""
        from src.data import sector_profiles as sp
        _seed("HKSE", "industry", "Internet Content & Information", "pe", 9.2, 6)
        monkeypatch.setattr(
            "src.data.regional_comps.get_fmp_classification",
            lambda t: {"exchange": "HKSE", "sector": "Communication Services",
                       "industry": "Internet Content & Information"})
        called = []
        monkeypatch.setattr(sp, "get_dynamic_peer_multiples",
                            lambda sector, profile: called.append(1) or {"pb": 99.0})
        got = sp.get_sector_peer_multiples("Tech", is_hk=True, ticker="00700.HK",
                                           market_cap=4e12)
        assert called == [], "US curated baskets must not feed an HK name"
        assert got["pb"] == sp.HK_SECTOR_PEER_MULTIPLES["Tech"]["pb"]


class TestDepositaryReceipts:
    """SGX lists receipts over HK-primary companies. HPAD.SI (Ping An) was
    the #2 SGX name by market cap and survived the cross-exchange name
    filter, because its name carries a receipt suffix the HK listing does
    not. Left in, it drags Singapore financial comps toward mainland
    Chinese multiples."""

    @pytest.mark.parametrize("name", [
        "Ping An Insurance (Group) Company of China Ltd. Shs UnSp Singapore "
        "Depositary Receipt Repr 1/2 Sh",
        "Some Co Ltd Depository Receipt",
        "Alibaba Group Holding Ltd Sponsored ADR",
        "Some Co Unsponsored ADR",
        "Foo Plc GDR",
    ])
    def test_detected(self, name):
        assert rc.is_depositary_receipt(name)

    @pytest.mark.parametrize("name", [
        "DBS Group Holdings Ltd",
        "Bank of China Limited",
        "Adroit Holdings",          # contains "adr" as a substring
        "Cadre Holdings Inc",       # contains "adr" as a substring
        "Andrea Electronics",
        "",
    ])
    def test_not_detected(self, name):
        assert not rc.is_depositary_receipt(name)

    def test_none_is_safe(self):
        assert not rc.is_depositary_receipt(None)
