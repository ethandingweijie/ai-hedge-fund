"""
tests/test_screener_dual_mode.py
================================
Screener cache layer on the dual-mode DB (src/data/db.py).

Regression tests for the 2026-08-16 production incident: screener_service
used raw sqlite3 against RUN_ARCHIVE_PATH=/data/run_archive.db; when the
/data volume was detached for multi-replica web, every screener endpoint
500'd with "unable to open database file". The service now goes through the
shared SQLite-local / Postgres-production layer like the other migrated
storage modules.
"""
import json

import pytest

from src.data import db as _db


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """Point the dual-mode layer at a fresh tmp SQLite file."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("RUN_ARCHIVE_PATH", str(tmp_path / "screener_test.db"))
    _db.close_all_connections()
    yield
    _db.close_all_connections()


@pytest.fixture()
def ss(tmp_db):
    from app.backend.services import screener_service
    return screener_service


# ── Schema ────────────────────────────────────────────────────────────────────

def test_ensure_tables_creates_all_screener_tables(ss):
    ss._ensure_tables()
    names = {r["name"] for r in _db.query(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {
        "screener_cache", "fast_vgpm_cache", "raw_metrics_cache",
        "screener_lookup_cache", "company_name_cache", "master_universe",
    } <= names


# ── screener_cache ────────────────────────────────────────────────────────────

def test_screener_cache_roundtrip_and_replace(ss):
    ss._ensure_tables()
    ss._set_cached("k1", [{"symbol": "AAPL"}])
    assert ss._get_cached("k1") == [{"symbol": "AAPL"}]

    # upsert must replace, not duplicate
    ss._set_cached("k1", [{"symbol": "MSFT"}])
    assert ss._get_cached("k1") == [{"symbol": "MSFT"}]
    rows = _db.query("SELECT COUNT(*) AS n FROM screener_cache")
    assert rows[0]["n"] == 1

    assert ss._get_cached("missing") is None


def test_screener_cache_expiry(ss):
    ss._ensure_tables()
    ss._set_cached("stale", [{"symbol": "X"}], ttl_hours=-1)
    assert ss._get_cached("stale") is None


# ── fast_vgpm_cache ───────────────────────────────────────────────────────────

def test_fast_vgpm_cache_roundtrip(ss):
    ss._ensure_tables()
    data = {"AAPL": {"V": {"score": 78, "grade": "A"}},
            "MSFT": {"V": {"score": 55, "grade": "B"}}}
    ss._set_fast_vgpm_cached(data)
    assert ss._get_fast_vgpm_cached(["AAPL", "MSFT", "NOPE"]) == data
    assert ss._get_fast_vgpm_cached([]) == {}


def test_fast_vgpm_cache_expiry(ss):
    ss._ensure_tables()
    ss._set_fast_vgpm_cached({"AAPL": {"V": {"score": 1}}}, ttl_hours=-1)
    assert ss._get_fast_vgpm_cached(["AAPL"]) == {}


# ── master_universe ───────────────────────────────────────────────────────────

def test_master_universe_roundtrip(ss):
    ss._ensure_tables()
    assert ss._get_master_universe() is None
    assert ss._get_master_universe_cached_at() is None

    stocks = [{"symbol": "AAPL", "marketCap": 3_000_000_000_000},
              {"symbol": "MSFT", "marketCap": 2_500_000_000_000}]
    ss._set_master_universe(stocks)

    got = ss._get_master_universe()
    assert got is not None and len(got) == 2
    assert {s["symbol"] for s in got} == {"AAPL", "MSFT"}
    assert ss._get_master_universe_cached_at()  # ISO timestamp

    # replacing drops old rows
    ss._set_master_universe([{"symbol": "NVDA", "marketCap": 1}])
    got = ss._get_master_universe()
    assert [s["symbol"] for s in got] == ["NVDA"]


def test_master_universe_expiry(ss):
    ss._ensure_tables()
    ss._set_master_universe([{"symbol": "AAPL"}], ttl_hours=-1)
    assert ss._get_master_universe() is None


# ── invalidate_for_ticker ─────────────────────────────────────────────────────

def test_invalidate_for_ticker_clears_caches(ss):
    ss._ensure_tables()
    ss._set_cached("k1", [{"symbol": "AAPL"}])
    ss._set_fast_vgpm_cached({"AAPL": {"V": {"score": 50}}})
    ss._upsert("screener_lookup_cache", "symbol",
               ["symbol", "fetched_at", "expires_at", "item_json"],
               ["AAPL", "2026-08-16T00:00:00+00:00",
                "2999-01-01T00:00:00+00:00", json.dumps({"symbol": "AAPL"})])

    ss.invalidate_for_ticker("AAPL")

    assert ss._get_cached("k1") is None
    assert ss._get_fast_vgpm_cached(["AAPL"]) == {}
    row = _db.query_one(
        "SELECT item_json FROM screener_lookup_cache WHERE symbol = ?", ["AAPL"])
    assert row is None


# ── company_name_cache via get_company_names ─────────────────────────────────

def test_get_company_names_serves_cache_hits_without_network(ss):
    ss._ensure_tables()
    ss._upsert("company_name_cache", "ticker",
               ["ticker", "name", "sector", "industry", "expires_at"],
               ["AAPL", "Apple Inc.", "Technology", "Consumer Electronics",
                "2999-01-01T00:00:00+00:00"])

    got = ss.get_company_names(["AAPL"])
    assert got == {"AAPL": {"name": "Apple Inc.", "sector": "Technology",
                            "industry": "Consumer Electronics"}}


# ── upsert SQL shape per DB mode ──────────────────────────────────────────────

def test_upsert_sql_pg_uses_on_conflict(ss, monkeypatch):
    monkeypatch.setattr(ss._db, "is_postgres", lambda: True)
    sql = ss._upsert_sql("screener_cache", "cache_key",
                         ["cache_key", "fetched_at", "expires_at", "results_json"])
    assert "INSERT OR REPLACE" not in sql
    assert "ON CONFLICT (cache_key) DO UPDATE SET" in sql
    assert "fetched_at = EXCLUDED.fetched_at" in sql
    assert "cache_key = EXCLUDED.cache_key" not in sql  # conflict col not self-updated
    assert sql.count("?") == 4


def test_upsert_sql_sqlite_uses_insert_or_replace(ss, monkeypatch):
    monkeypatch.setattr(ss._db, "is_postgres", lambda: False)
    sql = ss._upsert_sql("screener_cache", "cache_key",
                         ["cache_key", "fetched_at", "expires_at", "results_json"])
    assert sql.startswith("INSERT OR REPLACE INTO screener_cache")
    assert "ON CONFLICT" not in sql
