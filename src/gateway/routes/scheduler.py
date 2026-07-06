"""P3a §6.7: Scheduler management API.

Workspace-scoped cron job management for scheduled agent runs.

Access:
- Reads: ``workspace_admin`` (``scheduler:read``).
- Mutations: ``workspace_admin`` (``scheduler:write``).
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from src.gateway.auth.rbac import require_permission
from src.runtime.harness.registry import get_registry

router = APIRouter()


class ScheduledJobOut(BaseModel):
    id: str
    agent_id: str
    name: str
    cron: str
    enabled: bool
    created_at: str | None = None
    last_run_at: str | None = None
    next_run_at: str | None = None


class CreateJobRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    agent_id: str = Field(..., min_length=1)
    cron: str = Field(..., min_length=1)
    input_messages: list[dict] = Field(default_factory=list)
    enabled: bool = True


class UpdateJobRequest(BaseModel):
    name: str | None = None
    agent_id: str | None = None
    cron: str | None = None
    input_messages: list[dict] | None = None
    enabled: bool | None = None


@router.get("/api/v1/workspaces/{workspace_id}/scheduler/jobs")
async def list_jobs(
    workspace_id: str,
    _ctx=Depends(require_permission("scheduler:read", workspace_id_param="workspace_id")),
):
    """List all scheduled jobs for this workspace."""
    scheduler = get_registry().scheduler
    if scheduler is None:
        return []
    jobs = await scheduler.list_jobs(workspace_id)
    return [
        ScheduledJobOut(
            id=j.id,
            agent_id=j.agent_id,
            name=j.name,
            cron=j.cron,
            enabled=j.enabled,
            created_at=j.created_at.isoformat() if j.created_at else None,
            last_run_at=j.last_run_at.isoformat() if j.last_run_at else None,
            next_run_at=j.next_run_at.isoformat() if j.next_run_at else None,
        ).model_dump()
        for j in jobs
    ]


@router.post("/api/v1/workspaces/{workspace_id}/scheduler/jobs")
async def create_job(
    workspace_id: str,
    body: CreateJobRequest,
    _ctx=Depends(require_permission("scheduler:write", workspace_id_param="workspace_id")),
):
    """Create a new scheduled job."""
    from src.runtime.harness.scheduler import ScheduledJob

    scheduler = get_registry().scheduler
    if scheduler is None:
        return JSONResponse(
            status_code=503,
            content={"error": {"code": "SCHEDULER_UNAVAILABLE", "message": "Scheduler not initialized"}},
        )

    # Validate cron expression
    try:
        from apscheduler.triggers.cron import CronTrigger
        CronTrigger.from_crontab(body.cron)
    except Exception:
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "VALIDATION_ERROR", "message": f"Invalid cron expression: {body.cron!r}"}},
        )

    job = ScheduledJob(
        id=uuid.uuid4().hex[:32],
        workspace_id=workspace_id,
        agent_id=body.agent_id,
        name=body.name,
        cron=body.cron,
        input_messages=body.input_messages,
        enabled=body.enabled,
        created_at=datetime.now(timezone.utc),
    )
    await scheduler.schedule(job)
    return JSONResponse(
        status_code=201,
        content=ScheduledJobOut(
            id=job.id,
            agent_id=job.agent_id,
            name=job.name,
            cron=job.cron,
            enabled=job.enabled,
            created_at=job.created_at.isoformat() if job.created_at else None,
            next_run_at=job.next_run_at.isoformat() if job.next_run_at else None,
        ).model_dump(),
    )


@router.put("/api/v1/workspaces/{workspace_id}/scheduler/jobs/{job_id}")
async def update_job(
    workspace_id: str,
    job_id: str,
    body: UpdateJobRequest,
    _ctx=Depends(require_permission("scheduler:write", workspace_id_param="workspace_id")),
):
    """Update a scheduled job."""
    scheduler = get_registry().scheduler
    if scheduler is None:
        return JSONResponse(
            status_code=503,
            content={"error": {"code": "SCHEDULER_UNAVAILABLE", "message": "Scheduler not initialized"}},
        )

    existing = await scheduler.get(job_id)
    if existing is None or existing.workspace_id != workspace_id:
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "NOT_FOUND", "message": "Scheduled job not found"}},
        )

    # Validate cron if provided
    if body.cron is not None:
        try:
            from apscheduler.triggers.cron import CronTrigger
            CronTrigger.from_crontab(body.cron)
        except Exception:
            return JSONResponse(
                status_code=422,
                content={"error": {"code": "VALIDATION_ERROR", "message": f"Invalid cron expression: {body.cron!r}"}},
            )

    fields = body.model_dump(exclude_none=True)
    updated = await scheduler.update(job_id, **fields)
    if updated is None:
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "NOT_FOUND", "message": "Scheduled job not found"}},
        )
    return ScheduledJobOut(
        id=updated.id,
        agent_id=updated.agent_id,
        name=updated.name,
        cron=updated.cron,
        enabled=updated.enabled,
        created_at=updated.created_at.isoformat() if updated.created_at else None,
        last_run_at=updated.last_run_at.isoformat() if updated.last_run_at else None,
        next_run_at=updated.next_run_at.isoformat() if updated.next_run_at else None,
    ).model_dump()


@router.delete("/api/v1/workspaces/{workspace_id}/scheduler/jobs/{job_id}")
async def delete_job(
    workspace_id: str,
    job_id: str,
    _ctx=Depends(require_permission("scheduler:write", workspace_id_param="workspace_id")),
):
    """Delete a scheduled job."""
    scheduler = get_registry().scheduler
    if scheduler is None:
        return JSONResponse(
            status_code=503,
            content={"error": {"code": "SCHEDULER_UNAVAILABLE", "message": "Scheduler not initialized"}},
        )

    existing = await scheduler.get(job_id)
    if existing is None or existing.workspace_id != workspace_id:
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "NOT_FOUND", "message": "Scheduled job not found"}},
        )

    await scheduler.cancel(job_id)
    return Response(status_code=204)


@router.post("/api/v1/workspaces/{workspace_id}/scheduler/jobs/{job_id}/trigger")
async def trigger_job(
    workspace_id: str,
    job_id: str,
    _ctx=Depends(require_permission("scheduler:write", workspace_id_param="workspace_id")),
):
    """Manually trigger a one-off run of a scheduled job."""
    scheduler = get_registry().scheduler
    if scheduler is None:
        return JSONResponse(
            status_code=503,
            content={"error": {"code": "SCHEDULER_UNAVAILABLE", "message": "Scheduler not initialized"}},
        )

    existing = await scheduler.get(job_id)
    if existing is None or existing.workspace_id != workspace_id:
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "NOT_FOUND", "message": "Scheduled job not found"}},
        )

    result = await scheduler.trigger(job_id)
    if result is None:
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "TRIGGER_FAILED", "message": "Failed to trigger job"}},
        )
    return JSONResponse(status_code=202, content=result)
