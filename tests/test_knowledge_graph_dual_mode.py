"""
tests/test_knowledge_graph_dual_mode.py
=======================================
Knowledge-graph cache on the dual-mode DB (src/data/db.py) — R1 reliability
batch. Same regression pattern as tests/test_screener_dual_mode.py: the
module used raw sqlite3 against RUN_ARCHIVE_PATH, the exact incident class
that 500'd the screener on 2026-08-16 when the /data volume was detached
for multi-replica web. All reads/writes now go through the shared
SQLite-local / Postgres-production layer.
"""
import pytest

from src.data import db as _db


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """Point the dual-mode layer at a fresh tmp SQLite file."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("RUN_ARCHIVE_PATH", str(tmp_path / "kg_test.db"))
    _db.close_all_connections()
    yield
    _db.close_all_connections()


@pytest.fixture()
def kg(tmp_db):
    from app.backend.services import knowledge_graph
    knowledge_graph._tables_ready_key = None  # fresh DB per test
    return knowledge_graph


# ── Schema ────────────────────────────────────────────────────────────────────

def test_ensure_table_creates_kg_table(kg):
    kg._ensure_table()
    names = {r["name"] for r in _db.query(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "kg_ticker_metrics" in names


# ── TTM metrics ───────────────────────────────────────────────────────────────

def test_ttm_roundtrip_and_expiry(kg):
    data = {"AAPL": {"metricA": 1.5, "last_earnings_date": "2026-07-31"}}
    kg.set_ttm_metrics(data, sector_map={"AAPL": "Technology"},
                       industry_map={"AAPL": "Consumer Electronics"})

    assert kg.get_ttm_metrics_cached(["AAPL", "NOPE"]) == data
    assert kg.get_ttm_metrics_cached([]) == {}

    kg.set_ttm_metrics({"MSFT": {"m": 1}}, ttl_hours=-1)
    assert kg.get_ttm_metrics_cached(["MSFT"]) == {}


def test_ttm_upsert_retains_last_earnings_date(kg):
    """COALESCE(excluded.last_earnings_date, existing) — a refresh without
    an earnings date must not wipe the previously observed one."""
    kg.set_ttm_metrics({"AAPL": {"v": 1, "last_earnings_date": "2026-07-31"}})
    kg.set_ttm_metrics({"AAPL": {"v": 2}})  # no earnings date this time

    row = _db.query_one(
        "SELECT ttm_metrics_json, last_earnings_date "
        "FROM kg_ticker_metrics WHERE ticker = ?", ["AAPL"])
    assert row["last_earnings_date"] == "2026-07-31"
    assert '"v": 2' in row["ttm_metrics_json"]  # payload DID update


def test_ttm_upsert_replaces_not_duplicates(kg):
    kg.set_ttm_metrics({"AAPL": {"v": 1}})
    kg.set_ttm_metrics({"AAPL": {"v": 2}})
    rows = _db.query("SELECT COUNT(*) AS n FROM kg_ticker_metrics")
    assert rows[0]["n"] == 1


# ── Annual line items ─────────────────────────────────────────────────────────

def test_annual_roundtrip_and_expiry(kg):
    annual = {"2025-12-31": {"revenue": 100, "net_income": 20}}
    kg.set_annual_line_items("AAPL", annual, sector="Technology")
    assert kg.get_annual_line_items_cached("AAPL") == annual

    kg.set_annual_line_items("OLD", annual, ttl_hours=-1)
    assert kg.get_annual_line_items_cached("OLD") is None

    assert kg.get_annual_line_items_cached("NEVER_SEEN") is None


def test_annual_stale_when_newer_earnings_seen(kg):
    """Row cached as-of earnings date X must go stale once last_earnings_date
    advances past X — even inside the TTL window."""
    annual = {"2025-12-31": {"revenue": 100}}

    # Seed an earnings date first so the annual write captures it as as-of.
    kg.set_ttm_metrics({"AAPL": {"last_earnings_date": "2026-06-30"}})
    kg.set_annual_line_items("AAPL", annual)
    assert kg.get_annual_line_items_cached("AAPL") == annual

    # Newer earnings arrives via a TTM refresh → annual row is now stale.
    kg.set_ttm_metrics({"AAPL": {"last_earnings_date": "2026-09-30"}})
    assert kg.get_annual_line_items_cached("AAPL") is None


def test_annual_upsert_keeps_existing_sector(kg):
    """COALESCE(existing.sector, excluded.sector) — annual writes without a
    sector must not wipe the sector set by the screener's TTM write."""
    kg.set_ttm_metrics({"AAPL": {"v": 1}}, sector_map={"AAPL": "Technology"})
    kg.set_annual_line_items("AAPL", {"2025-12-31": {"revenue": 1}}, sector=None)

    row = _db.query_one(
        "SELECT sector FROM kg_ticker_metrics WHERE ticker = ?", ["AAPL"])
    assert row["sector"] == "Technology"


# ── get_kg_annual_line_items (cache-first entry point) ────────────────────────

def test_get_kg_annual_line_items_cache_hit_skips_network(kg, monkeypatch):
    annual = {"2025-12-31": {"revenue": 100}}
    kg.set_annual_line_items("AAPL", annual)

    def _boom(**kwargs):
        raise AssertionError("network must not be touched on cache hit")

    monkeypatch.setattr("src.tools.api.search_line_items", _boom)
    assert kg.get_kg_annual_line_items("AAPL", end_date="2026-08-16") == annual


def test_get_kg_annual_line_items_live_fetch_failure_returns_empty(kg, monkeypatch):
    def _boom(**kwargs):
        raise RuntimeError("FMP down")

    monkeypatch.setattr("src.tools.api.search_line_items", _boom)
    assert kg.get_kg_annual_line_items("AAPL", end_date="2026-08-16") == {}


# ── delete ────────────────────────────────────────────────────────────────────

def test_delete_ticker_clears_row(kg):
    kg.set_ttm_metrics({"AAPL": {"v": 1}})
    kg.delete_ticker("AAPL")
    assert kg.get_ttm_metrics_cached(["AAPL"]) == {}


# ── upsert SQL shape per DB mode ──────────────────────────────────────────────

def test_upsert_sql_is_pg_compatible(kg):
    """The ON CONFLICT(ticker) DO UPDATE SET form works on BOTH SQLite and
    Postgres; no SQLite-only INSERT OR REPLACE anywhere."""
    for sql in (kg._TTM_UPSERT_SQL, kg._ANNUAL_UPSERT_SQL):
        assert "INSERT OR REPLACE" not in sql
        assert "ON CONFLICT(ticker) DO UPDATE SET" in sql
        assert sql.count("?") >= 7  # placeholders survive to the translator
