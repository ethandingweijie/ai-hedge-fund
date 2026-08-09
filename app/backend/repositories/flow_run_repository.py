from typing import List, Optional, Dict, Any
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import desc, func
from app.backend.database.models import HedgeFundFlowRun
from app.backend.models.schemas import FlowRunStatus


class FlowRunRepository:
    """Repository for HedgeFundFlowRun CRUD operations"""

    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def _visible(query, user_id: Optional[int]):
        """Scope a run query to a viewer. Runs created after auth was
        introduced are visible to their owner only; legacy NULL-user_id runs
        remain visible (the parent-flow ownership check in the route is what
        actually gates access to a flow's runs)."""
        if user_id is not None:
            query = query.filter(
                (HedgeFundFlowRun.user_id == user_id)
                | (HedgeFundFlowRun.user_id.is_(None))
            )
        return query

    def create_flow_run(self, flow_id: int, request_data: Dict[str, Any] = None,
                        user_id: Optional[int] = None) -> HedgeFundFlowRun:
        """Create a new flow run"""
        # Get the next run number for this flow
        run_number = self._get_next_run_number(flow_id)

        flow_run = HedgeFundFlowRun(
            flow_id=flow_id,
            request_data=request_data,
            run_number=run_number,
            status=FlowRunStatus.IDLE.value,
            user_id=user_id,
        )
        self.db.add(flow_run)
        self.db.commit()
        self.db.refresh(flow_run)
        return flow_run

    def get_flow_run_by_id(self, run_id: int, user_id: Optional[int] = None) -> Optional[HedgeFundFlowRun]:
        """Get a flow run by its ID (optionally scoped to a viewer)"""
        query = self.db.query(HedgeFundFlowRun).filter(HedgeFundFlowRun.id == run_id)
        return self._visible(query, user_id).first()

    def get_flow_runs_by_flow_id(self, flow_id: int, limit: int = 50, offset: int = 0,
                                 user_id: Optional[int] = None) -> List[HedgeFundFlowRun]:
        """Get all runs for a specific flow, ordered by most recent first"""
        query = (
            self.db.query(HedgeFundFlowRun)
            .filter(HedgeFundFlowRun.flow_id == flow_id)
        )
        return (
            self._visible(query, user_id)
            .order_by(desc(HedgeFundFlowRun.created_at))
            .limit(limit)
            .offset(offset)
            .all()
        )

    def get_active_flow_run(self, flow_id: int, user_id: Optional[int] = None) -> Optional[HedgeFundFlowRun]:
        """Get the current active (IN_PROGRESS) run for a flow"""
        query = (
            self.db.query(HedgeFundFlowRun)
            .filter(
                HedgeFundFlowRun.flow_id == flow_id,
                HedgeFundFlowRun.status == FlowRunStatus.IN_PROGRESS.value
            )
        )
        return self._visible(query, user_id).first()

    def get_latest_flow_run(self, flow_id: int, user_id: Optional[int] = None) -> Optional[HedgeFundFlowRun]:
        """Get the most recent run for a flow"""
        query = (
            self.db.query(HedgeFundFlowRun)
            .filter(HedgeFundFlowRun.flow_id == flow_id)
        )
        return (
            self._visible(query, user_id)
            .order_by(desc(HedgeFundFlowRun.created_at))
            .first()
        )
    
    def update_flow_run(
        self,
        run_id: int,
        status: Optional[FlowRunStatus] = None,
        results: Optional[Dict[str, Any]] = None,
        error_message: Optional[str] = None
    ) -> Optional[HedgeFundFlowRun]:
        """Update an existing flow run"""
        flow_run = self.get_flow_run_by_id(run_id)
        if not flow_run:
            return None
        
        # Update status and timing
        if status is not None:
            flow_run.status = status.value
            
            # Update timing based on status
            if status == FlowRunStatus.IN_PROGRESS and not flow_run.started_at:
                flow_run.started_at = datetime.utcnow()
            elif status in [FlowRunStatus.COMPLETE, FlowRunStatus.ERROR] and not flow_run.completed_at:
                flow_run.completed_at = datetime.utcnow()
        
        # Update results and error message
        if results is not None:
            flow_run.results = results
        if error_message is not None:
            flow_run.error_message = error_message
        
        self.db.commit()
        self.db.refresh(flow_run)
        return flow_run
    
    def delete_flow_run(self, run_id: int) -> bool:
        """Delete a flow run by ID"""
        flow_run = self.get_flow_run_by_id(run_id)
        if not flow_run:
            return False
        
        self.db.delete(flow_run)
        self.db.commit()
        return True
    
    def delete_flow_runs_by_flow_id(self, flow_id: int, user_id: Optional[int] = None) -> int:
        """Delete all runs for a specific flow. Returns count of deleted runs."""
        query = (
            self.db.query(HedgeFundFlowRun)
            .filter(HedgeFundFlowRun.flow_id == flow_id)
        )
        deleted_count = self._visible(query, user_id).delete(
            synchronize_session=False
        )
        self.db.commit()
        return deleted_count

    def get_flow_run_count(self, flow_id: int, user_id: Optional[int] = None) -> int:
        """Get total count of runs for a flow"""
        query = (
            self.db.query(HedgeFundFlowRun)
            .filter(HedgeFundFlowRun.flow_id == flow_id)
        )
        return self._visible(query, user_id).count()
    
    def _get_next_run_number(self, flow_id: int) -> int:
        """Get the next run number for a flow"""
        max_run_number = (
            self.db.query(func.max(HedgeFundFlowRun.run_number))
            .filter(HedgeFundFlowRun.flow_id == flow_id)
            .scalar()
        )
        return (max_run_number or 0) + 1 