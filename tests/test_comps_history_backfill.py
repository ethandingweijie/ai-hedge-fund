"""The quarterly through-cycle comps history backfill (owner, 2026-09-21).

The weekly refresh records measured medians but cannot form the through-cycle
multiples the dynamic multiples engine fits on; this job does, quarterly.
Offline: FMP is stubbed and the database is a temp file.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.data import comps_history_backfill as bf
from src.data import regional_comps as rc


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


def _row(value, key="Oil & Gas Midstream", field="ev_ebitda_norm"):
    return {"level": "industry", "key": key, "cohort": "large", "field": field,
            "value": value, "peer_count": 10}


class TestReplaceSemantics:
    def test_a_rerun_replaces_its_own_backfill_row(self, store):
        """A late filer changes a past year's median; the latest
        reconstruction is the best one."""
        rc.save_history("US", [_row(13.9)], as_of="2025-12-31", source="backfill", replace=True)
        rc.save_history("US", [_row(14.1)], as_of="2025-12-31", source="backfill", replace=True)
        h = rc.load_history("US", "Oil & Gas Midstream", "ev_ebitda_norm", "large")
        assert len(h) == 1 and h[0]["value"] == 14.1

    def test_a_backfill_never_touches_a_measured_row(self, store):
        rc.save_history("US", [_row(13.5)], as_of="2025-12-31", source="refresh")
        rc.save_history("US", [_row(99.0)], as_of="2025-12-31", source="backfill", replace=True)
        h = rc.load_history("US", "Oil & Gas Midstream", "ev_ebitda_norm", "large")
        assert h[0]["value"] == 13.5 and h[0]["source"] == "refresh"

    def test_without_replace_the_first_row_stands(self, store):
        rc.save_history("US", [_row(1.0)], as_of="2024-12-31", source="backfill")
        rc.save_history("US", [_row(2.0)], as_of="2024-12-31", source="backfill")
        assert rc.load_history("US", "Oil & Gas Midstream", "ev_ebitda_norm", "large")[0]["value"] == 1.0


class TestTheQuarterlyGate:
    def test_no_backfill_yet_means_it_runs(self, store):
        assert bf.already_ran_this_quarter() is False

    def test_a_fresh_backfill_satisfies_the_gate(self, store):
        rc.save_history("US", [_row(10.0)], as_of="2025-12-31", source="backfill")
        assert bf.already_ran_this_quarter() is True

    def test_only_backfill_rows_count(self, store):
        """A weekly refresh row is not a quarterly backfill."""
        rc.save_history("US", [_row(10.0)], as_of="2026-09-19", source="refresh")
        assert bf.already_ran_this_quarter() is False

    def test_an_old_backfill_does_not(self, store, monkeypatch):
        rc.save_history("US", [_row(10.0)], as_of="2025-12-31", source="backfill")
        later = datetime.now(timezone.utc) + timedelta(days=bf.IDEMPOTENCY_DAYS + 1)

        class _DT(datetime):
            @classmethod
            def now(cls, tz=None):
                return later

        monkeypatch.setattr(bf, "datetime", _DT)
        assert bf.already_ran_this_quarter() is False

    def test_the_gate_skips_the_run(self, store, monkeypatch):
        monkeypatch.setattr(bf, "already_ran_this_quarter", lambda: True)
        monkeypatch.setattr(bf, "run_market", lambda m: pytest.fail("ran despite the gate"))
        assert bf.run_quarterly_backfill() is None


class TestMarkets:
    def test_default_markets_are_the_validated_three(self, monkeypatch):
        monkeypatch.delenv("COMPS_HISTORY_MARKETS", raising=False)
        assert bf.markets() == ("US", "HKSE", "SES")

    def test_markets_are_overridable(self, monkeypatch):
        monkeypatch.setenv("COMPS_HISTORY_MARKETS", "US, JPX")
        assert bf.markets() == ("US", "JPX")

    def test_one_market_failing_does_not_stop_the_others(self, monkeypatch):
        monkeypatch.setattr(bf, "already_ran_this_quarter", lambda: False)
        monkeypatch.setenv("COMPS_HISTORY_MARKETS", "US,HKSE")

        def run(m, preset=None):
            if m == "US":
                raise RuntimeError("FMP down")
            return {"exchange": m, "medians": 3, "written": 3}

        monkeypatch.setattr(bf, "run_market", run)
        out = bf.run_quarterly_backfill()
        assert "error" in out["US"] and out["HKSE"]["written"] == 3


class TestTheSchedule:
    def test_it_fires_on_the_second_of_a_quarter_month_at_0500_utc(self):
        from app.backend import scheduler_service as ss
        target = datetime.now(timezone.utc) + timedelta(seconds=ss._seconds_until_comps_history_fire())
        assert target.day == 2 and target.hour == 5 and target.month in (1, 4, 7, 10)

    def test_it_is_registered_with_its_own_long_timeout(self):
        from app.backend.worker import COMPS_HISTORY_BACKFILL_TIMEOUT_S, WorkerSettings
        fn = next(f for f in WorkerSettings.functions
                  if (getattr(f, "__name__", None) or getattr(f, "name", None))
                  == "run_comps_history_backfill_task")
        assert fn.timeout_s == COMPS_HISTORY_BACKFILL_TIMEOUT_S >= 2 * 3600

    def test_it_can_be_switched_off(self, monkeypatch):
        from app.backend import scheduler_service as ss
        spec = next(s for s in ss.build_schedules() if s.name == "comps_history_backfill")
        monkeypatch.setenv("COMPS_HISTORY_BACKFILL_DISABLED", "true")   # the scheduler convention
        assert spec.is_disabled() is True


class TestTheEngineIgnoresTheIncompleteYear:
    def test_a_current_year_bucket_is_never_fitted_or_quoted(self, monkeypatch):
        from datetime import date
        from src.data import dynamic_multiples as dm
        this = date.today().year
        rows = {str(this - 5 + i): 5.0 + i * 0.1 for i in range(5)}
        rows[str(this)] = 99.0                     # a thin early-filer bucket

        def load_history(exchange, key, field, cohort="all", level=None):
            src = {y: 0.08 for y in rows} if field == "roic" else rows
            return [{"as_of": f"{y}-12-31", "value": v, "peer_count": 8,
                     "source": "backfill", "level": "industry"} for y, v in sorted(src.items())]

        monkeypatch.setattr(rc, "load_history", load_history)
        monkeypatch.setattr(dm, "real_rate_series", lambda *a, **k: {
            "source": "stub", "series": {f"{y}-06-15": 0.01 for y in rows}})
        b = dm.fit_betas("Oil & Gas Midstream")
        assert str(this) not in b["years"]
        assert str(this) not in b["series"]
