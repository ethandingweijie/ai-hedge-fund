"""Accept / revoke segment memory: owner-only, like every Model Accuracy route."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

OWNER = "owner@example.com"
USERS = {"owner-token": SimpleNamespace(id=1, email=OWNER),
         "other-token": SimpleNamespace(id=2, email="x@y.z", role="admin")}


@pytest.fixture
def client(monkeypatch):
    # The router on its own, as tests/test_calibration_review.py does: the
    # full app refuses to import without a production-grade JWT secret.
    from fastapi import FastAPI
    from app.backend.database import get_db
    from app.backend.routes import deps
    from app.backend.routes import model_accuracy as ma
    from src.data import segment_memory as sm
    app = FastAPI()
    app.include_router(ma.router)
    app.dependency_overrides[get_db] = lambda: None
    calls = []

    def fake_set_review(ticker, status, reviewer, **kw):
        if ticker == "NOPE":
            raise KeyError(ticker)
        calls.append((ticker, status, reviewer))
        return {"memory_key": "BABA", "status": status}

    monkeypatch.setenv("MODEL_ACCURACY_EMAILS", OWNER)
    monkeypatch.setattr(deps, "get_user_from_token", lambda token, db: USERS.get(token))
    monkeypatch.setattr(sm, "set_review", fake_set_review)
    c = TestClient(app)
    c.calls = calls
    return c


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_the_owner_accepts_and_revokes(client):
    r = client.post("/model-accuracy/segment-memory/09988.HK/accept", headers=_bearer("owner-token"))
    assert r.status_code == 200 and r.json()["status"] == "accepted"
    r = client.post("/model-accuracy/segment-memory/BABA/revoke", headers=_bearer("owner-token"))
    assert r.status_code == 200 and r.json()["status"] == "revoked"
    assert client.calls == [("09988.HK", "accepted", OWNER), ("BABA", "revoked", OWNER)]


@pytest.mark.parametrize("headers", [{}, _bearer("other-token"), _bearer("bad")])
def test_no_one_else_can_accept(client, headers):
    r = client.post("/model-accuracy/segment-memory/BABA/accept", headers=headers)
    assert r.status_code in (401, 403) and client.calls == []


def test_an_unknown_name_is_404(client):
    r = client.post("/model-accuracy/segment-memory/NOPE/accept", headers=_bearer("owner-token"))
    assert r.status_code == 404
