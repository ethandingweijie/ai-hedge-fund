"""Owner 2026-11-28 (option A): Singtel's EV/EBITDA leg prices on a regional telco basket, not the static 12x.

SGX has Singtel, StarHub and an infrastructure trust, so the label set fell under the peer floor. The basket
is StarHub plus Telstra, Spark, HKT, Maxis, CelcomDigi and AIS (8.9x TTM EV/EBITDA at derivation).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.data import regional_comps as rc

P = "Telco / Infrastructure (SG)"
LIVE = {"CC3.SI": 8.749, "TLS.AX": 8.889, "SPK.NZ": 5.84, "6823.HK": 9.773, "6012.KL": 8.893,
        "6947.KL": 6.523, "ADVANC.BK": 9.577}           # FMP TTM EV/EBITDA, 2026-11-28


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("RUN_ARCHIVE_PATH", str(tmp_path / "t.db"))
    from src.data import db as _db
    monkeypatch.setattr(_db, "get_db_path", lambda: str(tmp_path / "t.db"))
    _db.close_all_connections()
    monkeypatch.setattr(rc, "_tables_ready_key", None, raising=False)
    rows = [{"level": "profile", "key": P, "cohort": "all", "symbol": s, "name": s, "market_cap": 1e10,
             "metrics": {"ev_ebitda": {"value": v, "in_band": True}}} for s, v in LIVE.items()]
    rc.save_members(rc.CROSS_MARKET_EXCHANGE, rows, datetime.now(timezone.utc).isoformat())
    yield
    _db.close_all_connections()


def test_basket_is_the_owner_set():
    spec = rc.CROSS_MARKET_BASKETS[P]
    assert spec["markets"] == ("SES",)
    assert set(spec["symbols"]) == set(LIVE)
    assert "Z74.SI" not in spec["symbols"] and "TLKM.JK" not in spec["symbols"]


def test_singtel_prices_on_the_regional_median(store):
    got = rc.profile_basket_multiples("SES", P, exclude="Z74.SI")
    assert got["ev_ebitda"]["value"] == pytest.approx(8.889, abs=0.01)


def test_starhub_excludes_itself(store):
    got = rc.profile_basket_multiples("SES", P, exclude="CC3.SI")
    assert "CC3.SI" not in got["ev_ebitda"]["members"]
    assert got["ev_ebitda"]["value"] == pytest.approx(8.891, abs=0.01)
