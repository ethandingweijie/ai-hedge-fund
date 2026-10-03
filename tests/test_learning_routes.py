"""Phase G of the self-learning layer: the overview aggregator, its routes and the admin sweep."""
import pytest


OWNER = "owner@example.com"


class _User:
    def __init__(self, email, is_admin=False):
        self.email, self.is_admin, self.id = email, is_admin, 1


USERS = {"owner-token": _User(OWNER), "other-token": _User("other@example.com", is_admin=True)}


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    from src.memory import calibration as cal
    from src.memory import calibration_fit as cf
    from src.memory import calibration_review as review
    from src.memory import estimate_outcomes as eo
    from src.memory import gate_outcomes as go
    from src.memory import override_outcomes as oo
    from src.memory import run_archive as ra
    from src.memory import run_features as rf
    from src.memory import valuation_outcomes as vo
    path = str(tmp_path / "learning.db")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("RUN_ARCHIVE_PATH", path)
    monkeypatch.setattr(ra, "DB_PATH", path)
    monkeypatch.setattr(ra, "_sqlite_schema_paths", set())
    for m in (vo, rf, eo, go, oo, cf, review):
        m._tables_ready_key = None
    cal.clear_cache()
    yield
    for m in (vo, rf, eo, go, oo, cf, review):
        m._tables_ready_key = None
    cal.clear_cache()


class TestOverview:
    def test_empty_tables_give_insufficient_blocks_not_errors(self):
        from src.memory import learning_review as lr
        ov = lr.overview()
        assert ov["ledger"]["rows"] == 0 and ov["ledger"]["enabled"] is True
        assert ov["estimates"]["fy"]["archetype"]["groups"] == {}
        assert ov["guidance"]["n"] == 0
        assert ov["scenarios"]["px_365d"]["status"] == "insufficient"
        assert ov["gates"]["gates"] == {}
        assert ov["overrides"]["horizons"]["px_365d"]["status"] == "insufficient"
        assert ov["prior_misses"]["cells"] == 0 and ov["prior_misses"]["gaps"] == []
        assert ov["families"] == {"iv": None, "pt": None, "est": None}
        assert ov["kill_switches"]["RUN_FEATURES_DISABLED"]["state"] == "on"
        assert not any(isinstance(v, dict) and v.get("status") == "unavailable" for v in ov.values())

    def test_a_broken_block_does_not_take_the_page_down(self, monkeypatch):
        from src.memory import learning_review as lr
        monkeypatch.setattr(lr, "_gates", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        ov = lr.overview()
        assert ov["gates"]["status"] == "unavailable" and "boom" in ov["gates"]["error"]
        assert ov["ledger"]["rows"] == 0

    def test_switch_states_read_both_conventions(self, monkeypatch):
        from src.memory import learning_review as lr
        monkeypatch.setenv("GATE_OUTCOMES_DISABLED", "true")
        monkeypatch.setenv("PRIOR_MISSES", "false")
        sw = lr._switches()
        assert sw["GATE_OUTCOMES_DISABLED"]["state"] == "off" and sw["PRIOR_MISSES"]["state"] == "off"
        assert sw["ESTIMATE_OUTCOMES_DISABLED"]["state"] == "on" and sw["PROFILE_LESSONS"]["state"] == "on"


class TestRoutes:
    @pytest.fixture
    def client(self, monkeypatch):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.backend.database import get_db
        from app.backend.routes import deps
        from app.backend.routes import model_accuracy as ma
        app = FastAPI()
        app.include_router(ma.router)
        app.dependency_overrides[get_db] = lambda: None
        monkeypatch.setenv("MODEL_ACCURACY_EMAILS", f" {OWNER} ")
        monkeypatch.setattr(deps, "get_user_from_token", lambda token, db: USERS.get(token))
        return TestClient(app)

    @staticmethod
    def _bearer(token):
        return {"Authorization": f"Bearer {token}"}

    def test_owner_only(self, client):
        assert client.get("/model-accuracy/learning/overview").status_code == 403
        assert client.get("/model-accuracy/learning/overview", headers=self._bearer("other-token")).status_code == 403
        r = client.get("/model-accuracy/learning/overview", headers=self._bearer("owner-token"))
        assert r.status_code == 200 and r.json()["families"] == {"iv": None, "pt": None, "est": None}

    def test_estimates_and_credibility_routes(self, client):
        h = self._bearer("owner-token")
        r = client.get("/model-accuracy/learning/estimates?group_by=market&period_kind=fy", headers=h)
        assert r.status_code == 200 and r.json()["group_by"] == "market"
        assert client.get("/model-accuracy/learning/estimates?group_by=ticker", headers=h).status_code == 400
        r = client.get("/model-accuracy/learning/guidance-credibility?ticker=ZZCO", headers=h)
        assert r.status_code == 200 and r.json()["n"] == 0


class TestAdminSweep:
    @pytest.fixture
    def client(self, monkeypatch):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.backend.routes import admin as adm
        app = FastAPI()
        app.include_router(adm.router)
        monkeypatch.setattr(adm, "ADMIN_SECRET", "s3cret")     # read at import, so patch the constant
        return TestClient(app)

    def test_write_false_runs_every_loop_and_writes_nothing(self, client):
        from src.data import db
        r = client.post("/admin/learning/sweep?write=false", headers={"X-Admin-Secret": "s3cret"})
        assert r.status_code == 200
        body = r.json()
        assert set(body) >= {"run_features", "estimate_outcomes", "gate_outcomes", "override_outcomes"}
        assert body["run_features"]["written"] == 0 and body["estimate_outcomes"]["written"] == 0
        assert db.query_one("SELECT COUNT(*) AS n FROM run_features")["n"] == 0
        assert client.post("/admin/learning/sweep").status_code == 403
