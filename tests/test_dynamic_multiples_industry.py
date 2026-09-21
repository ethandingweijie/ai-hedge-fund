"""Dynamic multiples for every industry, updated automatically each quarter.

Owner decisions 2026-09-21: auto-apply within guardrails, applied in the
normalised legs only, switched on straight away, and logged on Model Accuracy
so the owner can confirm the update ran, reached every industry, and reached
valuations. Offline: a temp database, stubbed rates.
"""
from __future__ import annotations

from datetime import date

import pytest

from src.data import dynamic_multiples as dm
from src.data import regional_comps as rc

Y = date.today().year


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("RUN_ARCHIVE_PATH", str(tmp_path / "t.db"))
    monkeypatch.delenv("DYNAMIC_MULTIPLES_ENABLED", raising=False)
    monkeypatch.delenv("DYNAMIC_MULTIPLES_AUTO_DISABLED", raising=False)
    from src.data import db as _db
    monkeypatch.setattr(_db, "get_db_path", lambda: str(tmp_path / "t.db"))
    _db.close_all_connections()
    monkeypatch.setattr(rc, "_tables_ready_key", None, raising=False)
    monkeypatch.setattr(dm, "_dm_ready", None, raising=False)
    monkeypatch.setattr(dm, "real_rate_series", lambda *a, **k: {
        "source": "stub", "series": {f"{Y - 5 + i}-06-15": 0.01 for i in range(6)}})
    yield
    _db.close_all_connections()


def _seed(key, values, field="ev_ebitda_norm", roic=0.08, exchange="US", level="industry"):
    for i, v in enumerate(values):
        as_of = f"{Y - len(values) + i}-12-31"
        rc.save_history(exchange, [
            {"level": level, "key": key, "cohort": "large", "field": field, "value": v,
             "peer_count": 8},
            {"level": level, "key": key, "cohort": "large", "field": "roic", "value": roic,
             "peer_count": 8}], as_of=as_of, source="backfill")


class TestWhichMultiples:
    def test_most_industries_carry_both_normalised_multiples(self):
        assert dm.industry_fields("Software - Application") == ["ev_ebitda_norm", "pe_norm"]

    def test_financials_carry_pe_only(self):
        """A bank's EBITDA is not a meaningful figure."""
        for k in ("Banks - Regional", "Insurance - Property & Casualty", "Asset Management"):
            assert dm.industry_fields(k) == ["pe_norm"], k

    def test_reits_are_left_to_their_own_path(self):
        assert dm.industry_fields("REIT - Industrial") == []


class TestProposal:
    def test_baseline_is_the_basket_s_own_through_cycle_average(self, store):
        _seed("Semiconductors", [10.0, 14.0, 20.0, 24.0, 32.0])
        p = dm.propose_industry("US", "industry", "Semiconductors", "ev_ebitda_norm")
        assert p["baseline"] == pytest.approx(20.0)
        assert p["band"] == pytest.approx([16.0, 24.0])
        assert p["market_now"] == pytest.approx(32.0)

    def test_a_market_outside_the_band_is_flagged(self, store):
        _seed("Semiconductors", [10.0, 14.0, 20.0, 24.0, 32.0])
        p = dm.propose_industry("US", "industry", "Semiconductors", "ev_ebitda_norm")
        assert any("above the band" in f for f in p["flags"])

    def test_the_proposal_never_leaves_twenty_percent(self, store, monkeypatch):
        _seed("Semiconductors", [10.0, 14.0, 20.0, 24.0, 32.0])
        monkeypatch.setitem(dm.PRIORS, "default", {"b1": 0.0, "b2": 500.0})
        _seed("Semiconductors", [10.0, 14.0, 20.0, 24.0, 32.0], roic=0.08)
        rc.save_history("US", [{"level": "industry", "key": "Semiconductors", "cohort": "large",
                                "field": "roic", "value": 0.50, "peer_count": 8}],
                        as_of=f"{Y - 1}-12-31", source="refresh")
        p = dm.propose_industry("US", "industry", "Semiconductors", "ev_ebitda_norm")
        assert p["band"][0] - 1e-9 <= p["proposed"] <= p["band"][1] + 1e-9

    def test_too_little_history_is_no_proposal(self, store):
        _seed("Tiny", [10.0, 11.0])
        assert dm.propose_industry("US", "industry", "Tiny", "ev_ebitda_norm") is None

    def test_hk_and_sg_say_the_rate_factor_is_a_us_proxy(self, store):
        _seed("Semiconductors", [10.0, 11.0, 12.0, 13.0, 14.0], exchange="HKSE")
        p = dm.propose_industry("HKSE", "industry", "Semiconductors", "ev_ebitda_norm")
        assert any("US 10y real yield" in f for f in p["flags"])


class TestQuarterlyUpdate:
    def test_first_run_writes_every_basket_and_logs_the_run(self, store):
        _seed("Semiconductors", [18.0, 19.0, 20.0, 21.0, 22.0])
        _seed("Semiconductors", [25.0, 26.0, 27.0, 28.0, 29.0], field="pe_norm")
        _seed("Banks - Regional", [10.0, 11.0, 12.0, 13.0, 14.0], field="pe_norm")
        s = dm.update_all(("US",), trigger="test")
        assert s["totals"]["initial"] == 3
        assert dm.current_multiple("US", "industry", "Semiconductors", "ev_ebitda_norm")
        assert dm.current_multiple("US", "industry", "Banks - Regional", "pe_norm")
        assert dm.current_multiple("US", "industry", "Banks - Regional", "ev_ebitda_norm") is None
        assert dm.log_report()["runs"][0]["run_id"] == s["run_id"]

    def test_a_small_move_is_held_as_noise(self, store):
        _seed("Semiconductors", [18.0, 19.0, 20.0, 21.0, 22.0])
        dm.update_all(("US",), trigger="test")
        before = dm.current_multiple("US", "industry", "Semiconductors", "ev_ebitda_norm")["multiple"]
        s = dm.update_all(("US",), trigger="test")
        assert s["totals"]["held_inertia"] >= 1
        assert dm.current_multiple("US", "industry", "Semiconductors",
                                   "ev_ebitda_norm")["multiple"] == before

    def test_a_pinned_basket_is_never_touched(self, store):
        _seed("Semiconductors", [18.0, 19.0, 20.0, 21.0, 22.0])
        dm.pin("US", "industry", "Semiconductors", "ev_ebitda_norm", 30.0, "owner")
        s = dm.update_all(("US",), trigger="test")
        assert s["totals"]["pinned"] == 1
        row = dm.current_multiple("US", "industry", "Semiconductors", "ev_ebitda_norm")
        assert row["multiple"] == 30.0 and row["pinned"]

    def test_unpin_hands_it_back(self, store):
        _seed("Semiconductors", [18.0, 19.0, 20.0, 21.0, 22.0])
        dm.pin("US", "industry", "Semiconductors", "ev_ebitda_norm", 30.0, "owner")
        dm.unpin("US", "industry", "Semiconductors", "ev_ebitda_norm")
        s = dm.update_all(("US",), trigger="test")
        assert s["totals"]["updated"] == 1

    def test_the_quarterly_backfill_runs_the_update_after_it(self, store, monkeypatch):
        from src.data import comps_history_backfill as bf
        monkeypatch.setattr(bf, "already_ran_this_quarter", lambda: False)
        monkeypatch.setattr(bf, "run_market", lambda m, preset=None: {"exchange": m})
        monkeypatch.setenv("COMPS_HISTORY_MARKETS", "US")
        called = {}
        monkeypatch.setattr(dm, "update_all", lambda markets, trigger: called.setdefault(
            "args", (markets, trigger)) and {"totals": {}})
        bf.run_quarterly_backfill()
        assert called["args"] == (("US",), "quarterly")

    def test_the_kill_switch_stops_the_update_only(self, store, monkeypatch):
        from src.data import comps_history_backfill as bf
        monkeypatch.setattr(bf, "already_ran_this_quarter", lambda: False)
        monkeypatch.setattr(bf, "run_market", lambda m, preset=None: {"exchange": m})
        monkeypatch.setenv("COMPS_HISTORY_MARKETS", "US")
        monkeypatch.setenv("DYNAMIC_MULTIPLES_AUTO_DISABLED", "true")
        monkeypatch.setattr(dm, "update_all", lambda *a, **k: pytest.fail("ran despite the switch"))
        out = bf.run_quarterly_backfill()
        assert "US" in out and "_multiples" not in out


class TestItReachesTheValuation:
    def _peer(self, key="Semiconductors"):
        return {"ev_ebitda": 30.0, "pe": 40.0,
                "_comp_basis": {"ev_ebitda": {"basis": "industry", "key": key, "exchange": "US"},
                                "pe": {"basis": "industry", "key": key, "exchange": "US"}}}

    def _leg(self, method, peer, monkeypatch, ticker="NVDA"):
        from src.agents.analysis import dcf_agent as d
        monkeypatch.setattr(d, "get_sector_peer_multiples", lambda *a, **k: dict(peer))
        row = {"revenue": 1e10, "normalized_ebitda": 1e9, "normalized_net_income": 5e8,
               "net_debt": 0.0, "shares_outstanding": 1e8}
        value, trace = d._traced_method_value(
            method_name=method, projection=None, most_recent=row, revenue_base=1e10,
            shares=1e8, net_debt=0.0, market_cap=1e10, wacc=0.08, growth_base=0.05,
            fcf_margin_base=0.2, tgr=0.02, fcf_floor=0.0, sector="Tech", scenario="base",
            reported_currency="USD", is_hk=False, growth_premium=1.0, sbc_pe_discount=1.0,
            profile_name="Growth Semis", ticker=ticker, end_date="2026-09-21")
        return value, trace

    def test_the_normalised_ev_ebitda_leg_prices_on_the_dynamic_multiple(self, store, monkeypatch):
        _seed("Semiconductors", [18.0, 19.0, 20.0, 21.0, 22.0])
        dm.update_all(("US",), trigger="test")
        live = dm.current_multiple("US", "industry", "Semiconductors", "ev_ebitda_norm")["multiple"]
        _, trace = self._leg("EV/EBITDA (norm)", self._peer(), monkeypatch)
        assert trace["multiple_parts"]["peer_multiple"] == pytest.approx(live)
        assert "dynamic through-cycle" in trace["multiple_parts"]["peer_source"]

    def test_and_the_valuation_records_that_it_did(self, store, monkeypatch):
        _seed("Semiconductors", [18.0, 19.0, 20.0, 21.0, 22.0])
        dm.update_all(("US",), trigger="test")
        self._leg("EV/EBITDA (norm)", self._peer(), monkeypatch, ticker="NVDA")
        rv = dm.log_report()["reached_valuations"]
        assert rv["tickers"] == 1 and rv["baskets_used"] == 1
        assert rv["baskets_used_since_last_update"] == 1

    def test_a_leg_whose_basket_has_no_row_of_its_own_falls_back(self, store, monkeypatch):
        _seed("Semiconductors", [18.0, 19.0, 20.0, 21.0, 22.0])
        dm.update_all(("US",), trigger="test")
        _, trace = self._leg("P/E (norm)", self._peer(), monkeypatch)
        # P/E (norm) has no pe_norm row for this basket in this test: falls back
        assert trace["multiple_parts"]["peer_source"] == "peer median pe"

    def test_no_dynamic_row_falls_back_to_the_peer_median(self, store, monkeypatch):
        _, trace = self._leg("EV/EBITDA (norm)", self._peer("Unknown Industry"), monkeypatch)
        assert trace["multiple_parts"]["peer_multiple"] == pytest.approx(30.0)
        assert trace["multiple_parts"]["peer_source"] == "peer median ev_ebitda"

    def test_switching_valuations_off_restores_the_peer_median(self, store, monkeypatch):
        _seed("Semiconductors", [18.0, 19.0, 20.0, 21.0, 22.0])
        dm.update_all(("US",), trigger="test")
        monkeypatch.setenv("DYNAMIC_MULTIPLES_ENABLED", "false")
        _, trace = self._leg("EV/EBITDA (norm)", self._peer(), monkeypatch)
        assert trace["multiple_parts"]["peer_multiple"] == pytest.approx(30.0)


class TestTheLog:
    def test_coverage_counts_every_industry_per_market(self, store):
        _seed("Semiconductors", [18.0, 19.0, 20.0, 21.0, 22.0])
        _seed("Banks - Regional", [10.0, 11.0, 12.0, 13.0, 14.0], field="pe_norm")
        dm.update_all(("US",), trigger="test")
        c = dm.log_report()["coverage"]["US"]
        assert c["baskets_with_history"] == 2
        assert c["multiples_live"] == 2

    def test_golden_replay_pins_the_feature_off(self):
        from src.memory.golden_capture import PINNED_ENV
        assert PINNED_ENV["DYNAMIC_MULTIPLES_ENABLED"] == "false"
