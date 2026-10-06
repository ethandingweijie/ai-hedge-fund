"""
tests/test_portfolio.py
========================
P1 gates — user_holdings CRUD + dashboard math.

Backward-gate semantics: all service paths degrade cleanly without
signals/prices (holdings-only dashboard), and user scoping never leaks
rows across users (the watchlist cross-user write class of bug).
"""
import pytest
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.backend.database.connection import Base
from app.backend.database.models import UserHolding
from app.backend.services import portfolio_service as ps


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


# ── CRUD + scoping ───────────────────────────────────────────────────────────

class TestCrudAndScoping:
    def test_upsert_adds_then_replaces(self, db):
        row = ps.upsert_holding(db, None, "baba", 100, 150.0)
        assert row.ticker == "BABA"  # uppercased
        assert row.quantity == 100 and row.avg_cost == 150.0
        # same (user, ticker) → replace, not duplicate
        row2 = ps.upsert_holding(db, None, "BABA", 200, 160.0,
                                 notes="added on weakness")
        assert row2.id == row.id
        assert row2.quantity == 200 and row2.avg_cost == 160.0
        assert row2.notes == "added on weakness"
        assert len(ps.list_holdings(db, None)) == 1

    def test_user_isolation(self, db):
        ps.upsert_holding(db, 1, "CRWD", 10, 300.0)
        ps.upsert_holding(db, 2, "JPM", 5, 200.0)
        anon = ps.upsert_holding(db, None, "O", 50, 55.0)

        assert [h.ticker for h in ps.list_holdings(db, 1)] == ["CRWD"]
        assert [h.ticker for h in ps.list_holdings(db, 2)] == ["JPM"]
        assert [h.ticker for h in ps.list_holdings(db, None)] == ["O"]

        # user 2 can never see / mutate user 1's row (or anon rows)
        assert ps.update_holding(db, 2, anon.id, quantity=1) is None
        assert not ps.delete_holding(db, 2, anon.id)
        assert ps.delete_holding(db, None, anon.id)

    def test_update_partial_fields(self, db):
        row = ps.upsert_holding(db, 7, "MSTR", 3, 400.0)
        updated = ps.update_holding(db, 7, row.id, avg_cost=410.0)
        assert updated is not None
        assert updated.quantity == 3  # untouched
        assert updated.avg_cost == 410.0

    def test_delete_missing_returns_false(self, db):
        assert not ps.delete_holding(db, None, 99999)


# ── Dashboard math (pure) ────────────────────────────────────────────────────

HOLDINGS = [
    {"id": 1, "ticker": "BABA", "quantity": 100, "avg_cost": 120.0,
     "opened_at": None, "notes": None, "added_at": None},
    {"id": 2, "ticker": "CRWD", "quantity": 10, "avg_cost": 400.0,
     "opened_at": None, "notes": None, "added_at": None},
]

PRICES = {"BABA": 132.0, "CRWD": 440.0}

SIGNALS = {
    "BABA": {
        "run_at": "2026-08-25T09:21:58", "decision": "HOLD",
        "dcf_base_iv": 111.38, "sector": "China Internet",
        "iv_bear": 65.69, "iv_base": 111.38, "iv_bull": 167.0,
        "vgpm_composite": 62,
    },
    "CRWD": {
        "run_at": "2026-08-20T12:00:00", "decision": "BUY",
        "dcf_base_iv": 49.93, "sector": "Cybersecurity SaaS",
        "iv_base": 49.93, "vgpm_composite": 55,
    },
}


class TestBuildDashboard:
    def test_market_value_weights_pnl(self):
        d = ps.build_dashboard(HOLDINGS, PRICES, SIGNALS)
        baba, crwd = d["holdings"]
        assert baba["market_value"] == pytest.approx(13200.0)
        assert crwd["market_value"] == pytest.approx(4400.0)
        total = 13200.0 + 4400.0
        assert baba["weight_pct"] == pytest.approx(13200.0 / total * 100)
        assert crwd["weight_pct"] == pytest.approx(4400.0 / total * 100)
        assert baba["unrealized_pnl"] == pytest.approx(1200.0)
        assert baba["pnl_pct"] == pytest.approx(10.0)
        assert crwd["unrealized_pnl"] == pytest.approx(400.0)

    def test_summary_totals(self):
        d = ps.build_dashboard(HOLDINGS, PRICES, SIGNALS)
        s = d["summary"]
        assert s["total_market_value"] == pytest.approx(17600.0)
        assert s["total_cost_basis"] == pytest.approx(16000.0)
        assert s["total_unrealized_pnl"] == pytest.approx(1600.0)
        assert s["total_pnl_pct"] == pytest.approx(10.0)
        assert s["position_count"] == 2
        assert s["top_weight_pct"] == pytest.approx(75.0)

    def test_sector_exposure_weight_attributed(self):
        d = ps.build_dashboard(HOLDINGS, PRICES, SIGNALS)
        assert d["sector_exposure"]["China Internet"] == pytest.approx(75.0)
        assert d["sector_exposure"]["Cybersecurity SaaS"] == pytest.approx(25.0)

    def test_iv_upside(self):
        d = ps.build_dashboard(HOLDINGS, PRICES, SIGNALS)
        baba = d["holdings"][0]
        assert baba["iv_upside_pct"] == pytest.approx((111.38 / 132.0 - 1) * 100)

    def test_missing_price_degrades(self):
        d = ps.build_dashboard(HOLDINGS, {"BABA": 132.0}, SIGNALS)
        crwd = next(h for h in d["holdings"] if h["ticker"] == "CRWD")
        assert crwd["market_value"] is None
        assert crwd["weight_pct"] is None
        # CRWD excluded from exposure; BABA renormalizes to the only priced row
        assert d["sector_exposure"] == {"China Internet": 100.0}

    def test_empty_portfolio(self):
        d = ps.build_dashboard([], {}, {})
        assert d["holdings"] == []
        assert d["summary"]["position_count"] == 0
        assert d["summary"]["total_market_value"] is None
        assert d["sector_exposure"] == {}

    def test_no_signals_degrades(self):
        d = ps.build_dashboard(HOLDINGS, PRICES, {})
        baba = d["holdings"][0]
        assert baba["signals"] is None
        assert baba["iv_upside_pct"] is None
        # sector falls back to Unclassified — exposure still attributed
        assert d["sector_exposure"].get("Unclassified") == pytest.approx(100.0)


# ── Signals reader resilience ────────────────────────────────────────────────

class TestSignalsReader:
    def test_empty_input(self):
        assert ps._latest_signals([]) == {}

    def test_missing_table_degrades_to_empty(self, monkeypatch, tmp_path):
        # conftest strips DATABASE_URL → sqlite mode; point it at a fresh
        # tmp store with NO ticker_signals table so the reader exercises
        # its degrade-to-{} path (never propagates archive errors)
        monkeypatch.setenv("RUN_ARCHIVE_PATH", str(tmp_path / "empty.db"))
        assert ps._latest_signals(["BABA"]) == {}


class TestVgpmComposite:
    def test_composite(self):
        vgpm = {
            "valuation": {"score": 60, "grade": "B"},
            "growth": {"score": 80, "grade": "A"},
            "profitability": {"score": 70, "grade": "B+"},
            "momentum": {"score": 50, "grade": "C"},
        }
        assert ps._vgpm_composite(vgpm) == 65

    def test_empty(self):
        assert ps._vgpm_composite({}) is None


# ── HK / SG holdings: canonical tickers + USD roll-up ───────────────────────

class TestAsiaHoldings:
    def test_hk_and_sg_input_is_canonicalised(self, db):
        assert ps.upsert_holding(db, None, "9988", 100, 80.0).ticker == "09988.HK"
        assert ps.upsert_holding(db, None, "d05", 10, 40.0).ticker == "D05.SI"
        # same position typed another way replaces, never duplicates
        ps.upsert_holding(db, None, "9988.HK", 200, 90.0)
        tickers = sorted(h.ticker for h in ps.list_holdings(db, None))
        assert tickers == ["09988.HK", "D05.SI"]

    def test_legacy_raw_rows_renamed_on_read(self, db):
        db.add(UserHolding(user_id=None, ticker="9988", quantity=1, avg_cost=1))
        db.commit()
        assert [h.ticker for h in ps.list_holdings(db, None)] == ["09988.HK"]

    def test_legacy_raw_row_with_canonical_twin_left_alone(self, db):
        db.add(UserHolding(user_id=None, ticker="9988", quantity=1, avg_cost=1))
        db.add(UserHolding(user_id=None, ticker="09988.HK", quantity=2, avg_cost=1))
        db.commit()
        assert sorted(h.ticker for h in ps.list_holdings(db, None)) == ["09988.HK", "9988"]

    def test_totals_and_weights_in_usd(self):
        holdings = [
            {"id": 1, "ticker": "AAPL", "quantity": 10, "avg_cost": 100.0},
            {"id": 2, "ticker": "09988.HK", "quantity": 100, "avg_cost": 100.0},
        ]
        prices = {"AAPL": 100.0, "09988.HK": 110.0}
        fx = {"AAPL": {"currency": "USD", "fx_to_usd": 1.0},
              "09988.HK": {"currency": "HKD", "fx_to_usd": 0.128}}
        d = ps.build_dashboard(holdings, prices, {}, fx)
        aapl, baba = d["holdings"]
        # per-row figures stay in the listing currency
        assert baba["currency"] == "HKD"
        assert baba["market_value"] == pytest.approx(11000.0)
        assert baba["unrealized_pnl"] == pytest.approx(1000.0)
        assert baba["market_value_usd"] == pytest.approx(1408.0)
        # HK$11,000 is US$1,408 — it must not outweigh US$1,000 of AAPL 11:1
        total = 1000.0 + 1408.0
        assert baba["weight_pct"] == pytest.approx(1408.0 / total * 100)
        s = d["summary"]
        assert s["total_market_value"] == pytest.approx(total)
        assert s["total_cost_basis"] == pytest.approx(1000.0 + 1280.0)
        assert s["total_unrealized_pnl"] == pytest.approx(128.0)
        assert s["currencies"] == ["HKD", "USD"]

    def test_unpriced_line_cost_is_not_a_loss(self):
        holdings = [
            {"id": 1, "ticker": "AAPL", "quantity": 10, "avg_cost": 100.0},
            {"id": 2, "ticker": "D05.SI", "quantity": 10, "avg_cost": 40.0},
        ]
        d = ps.build_dashboard(holdings, {"AAPL": 110.0}, {})
        assert d["summary"]["total_unrealized_pnl"] == pytest.approx(100.0)
        assert d["summary"]["total_pnl_pct"] == pytest.approx(10.0)

    def test_replay_weights_use_fx(self, monkeypatch):
        from src.portfolio import replay as rp
        seen = {}

        def _capture(ev, holdings, weights, fetcher, regime):
            seen.update(weights)
            return {"key": ev.key, "regime_similarity": {"matches": 0}}
        monkeypatch.setattr(rp, "_replay_event", _capture)
        holdings = [
            {"ticker": "AAPL", "quantity": 10, "avg_cost": 100.0, "fx_to_usd": 1.0},
            {"ticker": "09988.HK", "quantity": 100, "avg_cost": 100.0, "fx_to_usd": 0.125},
        ]
        out = rp.replay_portfolio(holdings, price_fetcher=lambda *a: [])
        # US$1,000 vs HK$10,000 = US$1,250 → 1000/2250, not 1000/11000
        assert seen["AAPL"] == pytest.approx(1000.0 / 2250.0)
        assert seen["09988.HK"] == pytest.approx(1250.0 / 2250.0)
        assert out["holdings_snapshot"]["weight_basis"] == "cost_basis_qty_x_avg_cost_usd"
