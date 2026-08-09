"""Phase 3a (research jobs) — user attribution for background jobs.

Research jobs compute SHARED research data (cohorts every user sees), so
their reads stay globally visible — but every job row is now stamped with
the triggering user's id:

* attribution (who kicked it off), and
* the basis for Phase 3c per-user rate limits.

Service/scheduler-triggered jobs (X-Admin-Secret, no user identity) and
pre-auth rows keep user_id NULL.

Covered here:
1. Job store — user_id column auto-migration + create_job stamping.
2. deps.require_user_or_service — dual auth (JWT → User, admin secret →
   None, neither → 401), and the require_admin non-ASCII hmac regression.
3. Route level — the six research trigger endpoints stamp the actor.

Store tests run against a throwaway SQLite via RUN_ARCHIVE_PATH; async
route functions are driven with asyncio.run (no pytest-asyncio in this
repo).
"""

import asyncio

import pytest
from fastapi import HTTPException

from src.data import db
from app.backend.services import complacency_job_store as job_store
from app.backend.routes import deps as D
from app.backend.routes import research as R


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture()
def temp_db(monkeypatch, tmp_path):
    """Point the dual-mode db layer at a throwaway SQLite file."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("RUN_ARCHIVE_PATH", str(tmp_path / "run_archive_test.db"))
    db.close_all_connections()
    yield
    db.close_all_connections()


@pytest.fixture()
def no_queue(monkeypatch):
    """Keep queue mode off so routes take the in-process path."""
    monkeypatch.delenv("REDIS_URL", raising=False)


@pytest.fixture()
def captured_spawns(monkeypatch):
    """Replace _spawn_background so no real task/thread is started."""
    spawned = []

    def fake_spawn(coro):
        spawned.append(coro)
        coro.close()  # never awaited — close to avoid RuntimeWarning

    monkeypatch.setattr(R, "_spawn_background", fake_spawn)
    return spawned


class _FakeUser:
    def __init__(self, user_id: int):
        self.id = user_id


def _stamped_user_id(job_id: str):
    row = db.query_one(
        "SELECT user_id FROM complacency_jobs WHERE job_id = ?", [job_id]
    )
    return row["user_id"] if row else "NO ROW"


# ---------------------------------------------------------------------------
# 1. Job store: migration + stamping
# ---------------------------------------------------------------------------

def test_create_job_stamps_user_id(temp_db):
    job_id = job_store.create_job("refresh", user_id=7)
    assert _stamped_user_id(job_id) == 7


def test_create_job_without_user_is_null(temp_db):
    job_id = job_store.create_job("refresh")
    assert _stamped_user_id(job_id) is None


def test_create_job_with_ticker_and_user(temp_db):
    job_id = job_store.create_job("score_adhoc", ticker="CRWD", user_id=3)
    row = db.query_one(
        "SELECT ticker, user_id FROM complacency_jobs WHERE job_id = ?", [job_id]
    )
    assert row["ticker"] == "CRWD"
    assert row["user_id"] == 3


def test_user_id_column_auto_migrated_onto_old_schema(temp_db):
    """DBs created before the column existed get it added on first use."""
    # Old schema — no user_id column
    db.execute_script("""
        CREATE TABLE IF NOT EXISTS complacency_jobs (
            job_id TEXT PRIMARY KEY, kind TEXT NOT NULL, ticker TEXT,
            status TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
            progress_msg TEXT, result_json TEXT, error_msg TEXT
        );
    """)
    assert not db.column_exists("complacency_jobs", "user_id")

    job_store._ensure_table()
    assert db.column_exists("complacency_jobs", "user_id")

    job_id = job_store.create_job("hk50_qual", user_id=11)
    assert _stamped_user_id(job_id) == 11


def test_get_job_unaffected_by_new_column(temp_db):
    """Regression: _row_to_job still returns the usual dict shape."""
    job_id = job_store.create_job("refresh", user_id=5)
    job = job_store.get_job(job_id)
    assert job["status"] == "pending"
    assert job["kind"] == "refresh"
    assert set(job.keys()) == {
        "job_id", "kind", "ticker", "status", "started_at", "finished_at",
        "progress_msg", "result", "error",
    }


# ---------------------------------------------------------------------------
# 2. deps.require_user_or_service + require_admin hmac fix
# ---------------------------------------------------------------------------

def test_service_call_returns_none(monkeypatch):
    monkeypatch.setenv("DB_UPLOAD_SECRET", "sekret")
    assert D.require_user_or_service(
        authorization=None, x_admin_secret="sekret", db=None) is None


def test_jwt_call_returns_user(monkeypatch):
    monkeypatch.delenv("DB_UPLOAD_SECRET", raising=False)
    sentinel = _FakeUser(42)
    monkeypatch.setattr(D, "get_user_from_token", lambda token, db: sentinel)
    out = D.require_user_or_service(
        authorization="Bearer abc", x_admin_secret=None, db=None)
    assert out is sentinel


def test_secret_beats_jwt_check_ordering(monkeypatch):
    monkeypatch.setenv("DB_UPLOAD_SECRET", "sekret")

    def _boom(token, db):
        raise AssertionError("JWT path should not run when secret matches")

    monkeypatch.setattr(D, "get_user_from_token", _boom)
    assert D.require_user_or_service(
        authorization="Bearer abc", x_admin_secret="sekret", db=None) is None


def test_neither_auth_nor_secret_raises_401(monkeypatch):
    monkeypatch.setenv("DB_UPLOAD_SECRET", "sekret")
    with pytest.raises(HTTPException) as exc:
        D.require_user_or_service(
            authorization=None, x_admin_secret="wrong", db=None)
    assert exc.value.status_code == 401


def test_unset_secret_never_authenticates(monkeypatch):
    monkeypatch.delenv("DB_UPLOAD_SECRET", raising=False)
    with pytest.raises(HTTPException) as exc:
        D.require_user_or_service(
            authorization=None, x_admin_secret="anything", db=None)
    assert exc.value.status_code == 401


def test_require_admin_non_ascii_secret_does_not_crash(monkeypatch):
    """hmac.compare_digest raises TypeError on non-ASCII str operands;
    require_admin must UTF-8 encode both sides (bug already fixed in
    admin.py — this guards the deps.py copy)."""
    secret = "sékrét-π-密码"
    monkeypatch.setenv("DB_UPLOAD_SECRET", secret)
    assert D.require_admin(
        authorization=None, x_admin_secret=secret, db=None) is None


def test_require_admin_non_ascii_mismatch_denies(monkeypatch):
    monkeypatch.setenv("DB_UPLOAD_SECRET", "sékrét-π")
    with pytest.raises(HTTPException) as exc:
        D.require_admin(authorization=None, x_admin_secret="sékrét-Ω", db=None)
    assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# 3. Route level: trigger endpoints stamp the actor
# ---------------------------------------------------------------------------

def test_refresh_complacency_stamps_user(temp_db, no_queue, captured_spawns):
    resp = asyncio.run(R.refresh_complacency(max_workers=3, actor=_FakeUser(42)))
    assert resp["deduped"] is False
    assert _stamped_user_id(resp["job_id"]) == 42
    assert len(captured_spawns) == 1  # in-process path spawned


def test_refresh_complacency_service_call_stamps_null(temp_db, no_queue, captured_spawns):
    resp = asyncio.run(R.refresh_complacency(max_workers=3, actor=None))
    assert _stamped_user_id(resp["job_id"]) is None


def test_score_adhoc_stamps_user(temp_db, no_queue, captured_spawns):
    resp = asyncio.run(
        R.score_complacency_adhoc("CRWD", False, actor=_FakeUser(7)))
    assert _stamped_user_id(resp["job_id"]) == 7
    row = db.query_one(
        "SELECT ticker, kind FROM complacency_jobs WHERE job_id = ?",
        [resp["job_id"]])
    assert row["ticker"] == "CRWD"
    assert row["kind"] == "score_adhoc"


def test_idea_gen_stamps_user(temp_db, no_queue, captured_spawns):
    resp = asyncio.run(R.trigger_idea_generation(mode=None, actor=_FakeUser(3)))
    assert _stamped_user_id(resp["job_id"]) == 3


def test_hk50_qual_stamps_user(temp_db, no_queue, captured_spawns):
    resp = asyncio.run(
        R.hk50_qual_deep_research(top_n=20, force_refresh=False, actor=_FakeUser(8)))
    assert _stamped_user_id(resp["job_id"]) == 8


def test_hk50_qual_ticker_stamps_user(temp_db, no_queue, captured_spawns):
    resp = asyncio.run(
        R.hk50_qual_one_ticker("0700", force_refresh=False, actor=_FakeUser(9)))
    assert _stamped_user_id(resp["job_id"]) == 9


def test_hundred_q_refresh_stamps_user(temp_db, no_queue, captured_spawns):
    resp = asyncio.run(R.refresh_hundred_q(actor=_FakeUser(10)))
    assert _stamped_user_id(resp["job_id"]) == 10


def test_dedup_returns_in_flight_job_without_new_row(temp_db, no_queue, captured_spawns):
    """Global dedup contract: second trigger returns the existing job —
    which may belong to another user — so polling it must stay possible.
    This is why job READS are not owner-scoped."""
    first = asyncio.run(R.refresh_complacency(max_workers=3, actor=_FakeUser(1)))
    second = asyncio.run(R.refresh_complacency(max_workers=3, actor=_FakeUser(2)))
    assert second["deduped"] is True
    assert second["job_id"] == first["job_id"]
    # The job is still readable through the store (no owner filter on reads)
    assert job_store.get_job(first["job_id"])["status"] == "pending"
    # Only one job row was created
    assert db.query_one("SELECT COUNT(*) AS n FROM complacency_jobs")["n"] == 1
