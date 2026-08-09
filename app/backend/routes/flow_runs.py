from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy.orm import Session
from typing import List, Optional

from app.backend.database import get_db
from app.backend.database.models import User
from app.backend.routes.deps import require_user
from app.backend.repositories.flow_run_repository import FlowRunRepository
from app.backend.repositories.flow_repository import FlowRepository
from app.backend.models.schemas import (
    FlowRunCreateRequest,
    FlowRunUpdateRequest,
    FlowRunResponse,
    FlowRunSummaryResponse,
    FlowRunStatus,
    ErrorResponse
)

router = APIRouter(prefix="/flows/{flow_id}/runs", tags=["flow-runs"])


def _get_owned_flow(flow_id: int, user: User, db: Session):
    """Fetch the flow scoped to the viewer: their own flows plus templates.
    Raises 404 for anything else (does not leak existence of other users'
    flows). Every flow-run endpoint gates on this first."""
    flow_repo = FlowRepository(db)
    flow = flow_repo.get_flow_by_id(flow_id, user_id=user.id)
    if not flow:
        raise HTTPException(status_code=404, detail="Flow not found")
    return flow


@router.post(
    "/",
    response_model=FlowRunResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Flow not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def create_flow_run(
    flow_id: int,
    request: FlowRunCreateRequest,
    user: User = Depends(require_user),
    db: Session = Depends(get_db)
):
    """Create a new flow run for the specified flow"""
    try:
        _get_owned_flow(flow_id, user, db)

        # Create the flow run (stamped with the owner so other users'
        # list/get/delete calls can't see it on shared/template flows)
        run_repo = FlowRunRepository(db)
        flow_run = run_repo.create_flow_run(
            flow_id=flow_id,
            request_data=request.request_data,
            user_id=user.id,
        )
        return FlowRunResponse.from_orm(flow_run)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create flow run: {str(e)}")


@router.get(
    "/",
    response_model=List[FlowRunSummaryResponse],
    responses={
        404: {"model": ErrorResponse, "description": "Flow not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_flow_runs(
    flow_id: int,
    limit: int = Query(50, ge=1, le=100, description="Maximum number of runs to return"),
    offset: int = Query(0, ge=0, description="Number of runs to skip"),
    user: User = Depends(require_user),
    db: Session = Depends(get_db)
):
    """Get all runs for the specified flow"""
    try:
        _get_owned_flow(flow_id, user, db)

        # Get flow runs
        run_repo = FlowRunRepository(db)
        flow_runs = run_repo.get_flow_runs_by_flow_id(
            flow_id, limit=limit, offset=offset, user_id=user.id
        )
        return [FlowRunSummaryResponse.from_orm(run) for run in flow_runs]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to retrieve flow runs: {str(e)}")


@router.get(
    "/active",
    response_model=Optional[FlowRunResponse],
    responses={
        404: {"model": ErrorResponse, "description": "Flow not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_active_flow_run(
    flow_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Get the current active (IN_PROGRESS) run for the specified flow"""
    try:
        _get_owned_flow(flow_id, user, db)

        # Get active flow run
        run_repo = FlowRunRepository(db)
        active_run = run_repo.get_active_flow_run(flow_id, user_id=user.id)
        return FlowRunResponse.from_orm(active_run) if active_run else None
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to retrieve active flow run: {str(e)}")


@router.get(
    "/latest",
    response_model=Optional[FlowRunResponse],
    responses={
        404: {"model": ErrorResponse, "description": "Flow not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_latest_flow_run(
    flow_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Get the most recent run for the specified flow"""
    try:
        _get_owned_flow(flow_id, user, db)

        # Get latest flow run
        run_repo = FlowRunRepository(db)
        latest_run = run_repo.get_latest_flow_run(flow_id, user_id=user.id)
        return FlowRunResponse.from_orm(latest_run) if latest_run else None
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to retrieve latest flow run: {str(e)}")


@router.get(
    "/count",
    responses={
        200: {"description": "Flow run count"},
        404: {"model": ErrorResponse, "description": "Flow not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_flow_run_count(
    flow_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Get the total count of runs for a flow"""
    try:
        _get_owned_flow(flow_id, user, db)

        # Get run count
        run_repo = FlowRunRepository(db)
        count = run_repo.get_flow_run_count(flow_id, user_id=user.id)

        return {"flow_id": flow_id, "total_runs": count}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get flow run count: {str(e)}")


@router.get(
    "/{run_id}",
    response_model=FlowRunResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Flow or run not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_flow_run(
    flow_id: int,
    run_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Get a specific flow run by ID"""
    try:
        _get_owned_flow(flow_id, user, db)

        # Get flow run (scoped to the viewer; NULL-user legacy runs visible)
        run_repo = FlowRunRepository(db)
        flow_run = run_repo.get_flow_run_by_id(run_id, user_id=user.id)
        if not flow_run or flow_run.flow_id != flow_id:
            raise HTTPException(status_code=404, detail="Flow run not found")

        return FlowRunResponse.from_orm(flow_run)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to retrieve flow run: {str(e)}")


@router.put(
    "/{run_id}",
    response_model=FlowRunResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Flow or run not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def update_flow_run(
    flow_id: int,
    run_id: int,
    request: FlowRunUpdateRequest,
    user: User = Depends(require_user),
    db: Session = Depends(get_db)
):
    """Update an existing flow run"""
    try:
        _get_owned_flow(flow_id, user, db)

        # Update flow run
        run_repo = FlowRunRepository(db)
        # First verify the run exists, belongs to this flow, and is visible
        # to this viewer
        existing_run = run_repo.get_flow_run_by_id(run_id, user_id=user.id)
        if not existing_run or existing_run.flow_id != flow_id:
            raise HTTPException(status_code=404, detail="Flow run not found")

        flow_run = run_repo.update_flow_run(
            run_id=run_id,
            status=request.status,
            results=request.results,
            error_message=request.error_message
        )

        if not flow_run:
            raise HTTPException(status_code=404, detail="Flow run not found")

        return FlowRunResponse.from_orm(flow_run)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update flow run: {str(e)}")


@router.delete(
    "/{run_id}",
    responses={
        204: {"description": "Flow run deleted successfully"},
        404: {"model": ErrorResponse, "description": "Flow or run not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def delete_flow_run(
    flow_id: int,
    run_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Delete a flow run"""
    try:
        _get_owned_flow(flow_id, user, db)

        # Verify run exists, belongs to this flow, and is visible to this viewer
        run_repo = FlowRunRepository(db)
        existing_run = run_repo.get_flow_run_by_id(run_id, user_id=user.id)
        if not existing_run or existing_run.flow_id != flow_id:
            raise HTTPException(status_code=404, detail="Flow run not found")

        success = run_repo.delete_flow_run(run_id)
        if not success:
            raise HTTPException(status_code=404, detail="Flow run not found")

        return {"message": "Flow run deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete flow run: {str(e)}")


@router.delete(
    "/",
    responses={
        204: {"description": "All flow runs deleted successfully"},
        404: {"model": ErrorResponse, "description": "Flow not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def delete_all_flow_runs(
    flow_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Delete all runs for the specified flow"""
    try:
        _get_owned_flow(flow_id, user, db)

        # Delete all runs visible to this viewer
        run_repo = FlowRunRepository(db)
        deleted_count = run_repo.delete_flow_runs_by_flow_id(flow_id, user_id=user.id)

        return {"message": f"Deleted {deleted_count} flow runs successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete flow runs: {str(e)}")
