"""Phase 3a — user scoping for flow runs.

Covers two layers:

1. Repository level — ``FlowRunRepository._visible`` semantics: owner-stamped
   runs are only visible to their owner, legacy NULL-user_id runs stay
   visible to everyone (parent-flow ownership is the real gate), and
   unscoped calls (user_id=None) see everything.

2. Route level — every endpoint in ``routes/flow_runs.py`` gates on the
   parent-flow ownership check (``get_flow_by_id(flow_id, user_id=...)``)
   and passes the viewer's user_id through to the run repository, so
   cross-user access returns 404 even on shared/template flows.

All tests run against an in-memory SQLite database; async route functions
are driven with ``asyncio.run`` (no pytest-asyncio in this repo).
"""

import asyncio
from datetime import datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.backend.database.connection import Base
from app.backend.database.models import HedgeFundFlow, HedgeFundFlowRun, User
from app.backend.models.schemas import (
    FlowRunCreateRequest,
    FlowRunStatus,
)
from app.backend.repositories.flow_repository import FlowRepository
from app.backend.repositories.flow_run_repository import FlowRunRepository
from app.backend.routes import flow_runs as R


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def _make_user(db, n: int) -> User:
    user = User(
        email=f"user{n}@example.com",
        name=f"User {n}",
        provider="google",
        provider_sub=f"sub-{n}",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _make_flow(db, user_id=None, is_template=False) -> HedgeFundFlow:
    flow = HedgeFundFlow(
        user_id=user_id,
        name="Test flow",
        nodes=[],
        edges=[],
        is_template=is_template,
    )
    db.add(flow)
    db.commit()
    db.refresh(flow)
    return flow


def _make_run(db, flow_id, user_id=None, status=FlowRunStatus.IDLE.value,
              created_at=None) -> HedgeFundFlowRun:
    run = HedgeFundFlowRun(
        flow_id=flow_id,
        user_id=user_id,
        status=status,
        run_number=1,
        created_at=created_at or datetime(2026, 1, 1, 12, 0, 0),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


# ---------------------------------------------------------------------------
# Repository: _visible semantics
# ---------------------------------------------------------------------------

def test_create_flow_run_stamps_user_id(session):
    flow = _make_flow(session)
    repo = FlowRunRepository(session)

    owned = repo.create_flow_run(flow_id=flow.id, request_data={"a": 1}, user_id=7)
    assert owned.user_id == 7

    legacy = repo.create_flow_run(flow_id=flow.id)
    assert legacy.user_id is None


def test_get_flow_run_by_id_scoped(session):
    flow = _make_flow(session)
    a_run = _make_run(session, flow.id, user_id=1)
    b_run = _make_run(session, flow.id, user_id=2)
    legacy_run = _make_run(session, flow.id, user_id=None)
    repo = FlowRunRepository(session)

    # Owner sees their run
    assert repo.get_flow_run_by_id(a_run.id, user_id=1).id == a_run.id
    # Other user does not
    assert repo.get_flow_run_by_id(a_run.id, user_id=2) is None
    # Legacy NULL-user run visible to any viewer
    assert repo.get_flow_run_by_id(legacy_run.id, user_id=1).id == legacy_run.id
    assert repo.get_flow_run_by_id(legacy_run.id, user_id=2).id == legacy_run.id
    # Unscoped sees everything
    assert repo.get_flow_run_by_id(b_run.id).id == b_run.id


def test_get_flow_runs_by_flow_id_scoped(session):
    flow = _make_flow(session)
    _make_run(session, flow.id, user_id=1)
    _make_run(session, flow.id, user_id=2)
    _make_run(session, flow.id, user_id=None)
    # A run on another flow must never appear
    other_flow = _make_flow(session)
    _make_run(session, other_flow.id, user_id=1)

    repo = FlowRunRepository(session)

    a_runs = repo.get_flow_runs_by_flow_id(flow.id, user_id=1)
    assert {r.user_id for r in a_runs} == {None, 1}

    b_runs = repo.get_flow_runs_by_flow_id(flow.id, user_id=2)
    assert {r.user_id for r in b_runs} == {None, 2}

    unscoped = repo.get_flow_runs_by_flow_id(flow.id)
    assert len(unscoped) == 3


def test_get_active_flow_run_scoped(session):
    flow = _make_flow(session)
    a_active = _make_run(session, flow.id, user_id=1,
                         status=FlowRunStatus.IN_PROGRESS.value)
    _make_run(session, flow.id, user_id=2,
              status=FlowRunStatus.IN_PROGRESS.value)

    repo = FlowRunRepository(session)
    assert repo.get_active_flow_run(flow.id, user_id=1).id == a_active.id
    assert repo.get_active_flow_run(flow.id, user_id=1).user_id == 1


def test_get_latest_flow_run_scoped(session):
    flow = _make_flow(session)
    # B's run is newer, but A's latest must still be A's own run
    a_run = _make_run(session, flow.id, user_id=1,
                      created_at=datetime(2026, 1, 1, 12, 0, 0))
    _make_run(session, flow.id, user_id=2,
              created_at=datetime(2026, 2, 1, 12, 0, 0))

    repo = FlowRunRepository(session)
    assert repo.get_latest_flow_run(flow.id, user_id=1).id == a_run.id
    assert repo.get_latest_flow_run(flow.id, user_id=2).user_id == 2


def test_delete_flow_runs_by_flow_id_only_deletes_visible(session):
    flow = _make_flow(session)
    _make_run(session, flow.id, user_id=1)
    b_run = _make_run(session, flow.id, user_id=2)
    _make_run(session, flow.id, user_id=None)

    repo = FlowRunRepository(session)
    deleted = repo.delete_flow_runs_by_flow_id(flow.id, user_id=1)
    # A's own run + the legacy run are visible to A; B's run is not
    assert deleted == 2
    remaining = session.query(HedgeFundFlowRun).all()
    assert [r.id for r in remaining] == [b_run.id]


def test_get_flow_run_count_scoped(session):
    flow = _make_flow(session)
    _make_run(session, flow.id, user_id=1)
    _make_run(session, flow.id, user_id=1)
    _make_run(session, flow.id, user_id=2)
    _make_run(session, flow.id, user_id=None)

    repo = FlowRunRepository(session)
    assert repo.get_flow_run_count(flow.id, user_id=1) == 3  # 2 own + legacy
    assert repo.get_flow_run_count(flow.id, user_id=2) == 2  # 1 own + legacy
    assert repo.get_flow_run_count(flow.id) == 4


def test_visible_none_user_id_leaves_query_unfiltered(session):
    flow = _make_flow(session)
    _make_run(session, flow.id, user_id=1)
    _make_run(session, flow.id, user_id=2)

    query = session.query(HedgeFundFlowRun)
    assert FlowRunRepository._visible(query, None).count() == 2


# ---------------------------------------------------------------------------
# Routes: ownership gate + viewer scoping
# ---------------------------------------------------------------------------

def test_route_other_users_flow_is_404(session):
    a = _make_user(session, 1)
    b = _make_user(session, 2)
    flow = _make_flow(session, user_id=a.id)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(R.get_flow_runs(flow.id, limit=50, offset=0, user=b, db=session))
    assert exc.value.status_code == 404

    with pytest.raises(HTTPException) as exc:
        asyncio.run(R.create_flow_run(
            flow.id, FlowRunCreateRequest(request_data=None), user=b, db=session))
    assert exc.value.status_code == 404


def test_route_create_flow_run_stamps_owner(session):
    a = _make_user(session, 1)
    flow = _make_flow(session, user_id=a.id)

    resp = asyncio.run(R.create_flow_run(
        flow.id, FlowRunCreateRequest(request_data={"t": ["AAPL"]}),
        user=a, db=session))
    row = session.get(HedgeFundFlowRun, resp.id)
    assert row.user_id == a.id
    assert row.request_data == {"t": ["AAPL"]}


def test_route_template_flow_runs_are_owner_scoped(session):
    a = _make_user(session, 1)
    b = _make_user(session, 2)
    template = _make_flow(session, is_template=True)

    # Both users create runs on the shared template
    asyncio.run(R.create_flow_run(
        template.id, FlowRunCreateRequest(request_data=None), user=a, db=session))
    b_resp = asyncio.run(R.create_flow_run(
        template.id, FlowRunCreateRequest(request_data=None), user=b, db=session))

    # Each user lists only their own runs (limit/offset passed explicitly:
    # FastAPI resolves Query() defaults at request time, direct calls don't)
    a_runs = asyncio.run(R.get_flow_runs(
        template.id, limit=50, offset=0, user=a, db=session))
    assert len(a_runs) == 1
    assert all(r.flow_id == template.id for r in a_runs)

    b_runs = asyncio.run(R.get_flow_runs(
        template.id, limit=50, offset=0, user=b, db=session))
    assert [r.id for r in b_runs] == [b_resp.id]

    # Count endpoint is scoped too
    a_count = asyncio.run(R.get_flow_run_count(template.id, user=a, db=session))
    assert a_count == {"flow_id": template.id, "total_runs": 1}


def test_route_get_run_cross_user_is_404(session):
    a = _make_user(session, 1)
    b = _make_user(session, 2)
    template = _make_flow(session, is_template=True)

    a_resp = asyncio.run(R.create_flow_run(
        template.id, FlowRunCreateRequest(request_data=None), user=a, db=session))

    # Owner can read it
    own = asyncio.run(R.get_flow_run(template.id, a_resp.id, user=a, db=session))
    assert own.id == a_resp.id

    # Other user cannot
    with pytest.raises(HTTPException) as exc:
        asyncio.run(R.get_flow_run(template.id, a_resp.id, user=b, db=session))
    assert exc.value.status_code == 404


def test_route_active_and_latest_scoped(session):
    a = _make_user(session, 1)
    b = _make_user(session, 2)
    template = _make_flow(session, is_template=True)

    a_run = _make_run(session, template.id, user_id=a.id,
                      status=FlowRunStatus.IN_PROGRESS.value)
    b_run = _make_run(session, template.id, user_id=b.id,
                      status=FlowRunStatus.IN_PROGRESS.value,
                      created_at=datetime(2026, 3, 1, 12, 0, 0))

    a_active = asyncio.run(R.get_active_flow_run(template.id, user=a, db=session))
    assert a_active.id == a_run.id

    b_latest = asyncio.run(R.get_latest_flow_run(template.id, user=b, db=session))
    assert b_latest.id == b_run.id


def test_route_delete_run_cross_user_is_404(session):
    a = _make_user(session, 1)
    b = _make_user(session, 2)
    template = _make_flow(session, is_template=True)

    a_resp = asyncio.run(R.create_flow_run(
        template.id, FlowRunCreateRequest(request_data=None), user=a, db=session))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(R.delete_flow_run(template.id, a_resp.id, user=b, db=session))
    assert exc.value.status_code == 404
    # Run still exists
    assert session.get(HedgeFundFlowRun, a_resp.id) is not None

    # Owner can delete it
    asyncio.run(R.delete_flow_run(template.id, a_resp.id, user=a, db=session))
    assert session.get(HedgeFundFlowRun, a_resp.id) is None


def test_route_delete_all_only_deletes_viewers_runs(session):
    a = _make_user(session, 1)
    b = _make_user(session, 2)
    template = _make_flow(session, is_template=True)

    asyncio.run(R.create_flow_run(
        template.id, FlowRunCreateRequest(request_data=None), user=a, db=session))
    b_resp = asyncio.run(R.create_flow_run(
        template.id, FlowRunCreateRequest(request_data=None), user=b, db=session))

    msg = asyncio.run(R.delete_all_flow_runs(template.id, user=a, db=session))
    assert "Deleted 1" in msg["message"]
    # B's run survived A's bulk delete
    assert session.get(HedgeFundFlowRun, b_resp.id) is not None


def test_count_route_registered_before_run_id_route():
    """/count used to be registered after /{run_id}, so GET .../runs/count
    was swallowed by the /{run_id} route and 422'd on int validation.
    Guard the ordering fix."""
    paths = [getattr(r, "path", "") for r in R.router.routes]
    assert paths.index("/flows/{flow_id}/runs/count") < \
        paths.index("/flows/{flow_id}/runs/{run_id}")


def test_flow_repository_get_flow_by_id_scoping(session):
    """The route gate relies on FlowRepository.get_flow_by_id scoping:
    own flows and templates pass, other users' flows don't."""
    a = _make_user(session, 1)
    b = _make_user(session, 2)
    own = _make_flow(session, user_id=a.id)
    template = _make_flow(session, is_template=True)

    repo = FlowRepository(session)
    assert repo.get_flow_by_id(own.id, user_id=a.id).id == own.id
    assert repo.get_flow_by_id(own.id, user_id=b.id) is None
    assert repo.get_flow_by_id(template.id, user_id=b.id).id == template.id
