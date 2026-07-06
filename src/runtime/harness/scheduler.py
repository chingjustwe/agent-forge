"""P3b-P3: Scheduler — cron-based agent invocation via APScheduler.

Wraps ``AsyncIOScheduler`` to schedule agent runs on cron triggers.
Jobs persist to the ``scheduled_jobs`` table (M16 migration). On
trigger, the scheduler calls ``HarnessRuntime.run()`` with the job's
seed messages and publishes the result to the session's SSE stream.

The scheduler is started in ``main.py`` lifespan and shut down on app
exit. Manual one-off triggers (``POST /scheduler/jobs/{id}/trigger``)
run the job immediately without waiting for the cron schedule.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from pydantic import BaseModel, Field
from sqlalchemy import text

from src.infra.db.engine import async_session

if TYPE_CHECKING:
    from src.runtime.harness.runtime import HarnessRuntime

logger = logging.getLogger(__name__)


class ScheduledJob(BaseModel):
    """A scheduled cron job that invokes an agent run."""

    id: str
    workspace_id: str
    agent_id: str
    name: str
    cron: str  # 5-field cron expression
    input_messages: list[dict] = Field(default_factory=list)
    enabled: bool = True
    created_at: datetime | None = None
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None


class Scheduler:
    """Wraps APScheduler for cron-based agent invocation.

    Holds a reference to ``HarnessRuntime`` so scheduled jobs can call
    ``runtime.run()`` directly. The APScheduler ``AsyncIOScheduler``
    runs in the same event loop as FastAPI.
    """

    def __init__(self, runtime: "HarnessRuntime | None" = None) -> None:
        self._runtime = runtime
        self._scheduler = AsyncIOScheduler()
        self._started = False

    def set_runtime(self, runtime: "HarnessRuntime") -> None:
        """Inject the HarnessRuntime (called after runtime is created)."""
        self._runtime = runtime

    async def start(self) -> None:
        """Start the APScheduler event loop."""
        if not self._started:
            self._scheduler.start()
            self._started = True
            logger.info("Scheduler started")

    async def shutdown(self) -> None:
        """Shut down the scheduler. Called by HarnessRegistry.shutdown()."""
        if self._started:
            self._scheduler.shutdown(wait=False)
            self._started = False
            logger.info("Scheduler shutdown")

    async def schedule(self, job: ScheduledJob) -> ScheduledJob:
        """Add or update a cron job. Persists to DB and registers with APScheduler.

        Returns the job with ``next_run_at`` populated.
        """
        if job.created_at is None:
            job.created_at = datetime.now(timezone.utc)

        # Persist to DB
        async with async_session() as db:
            await db.execute(
                text(
                    "INSERT OR REPLACE INTO scheduled_jobs "
                    "(id, workspace_id, agent_id, name, cron, input_messages, "
                    "enabled, created_at, last_run_at, next_run_at) "
                    "VALUES (:id, :ws, :aid, :name, :cron, :msgs, :en, :cat, :lra, :nra)"
                ),
                {
                    "id": job.id,
                    "ws": job.workspace_id,
                    "aid": job.agent_id,
                    "name": job.name,
                    "cron": job.cron,
                    "msgs": json.dumps(job.input_messages),
                    "en": 1 if job.enabled else 0,
                    "cat": job.created_at.isoformat(),
                    "lra": job.last_run_at.isoformat() if job.last_run_at else None,
                    "nra": job.next_run_at.isoformat() if job.next_run_at else None,
                },
            )
            await db.commit()

        if job.enabled:
            self._add_aps_job(job)
        return job

    async def cancel(self, job_id: str) -> bool:
        """Cancel and delete a scheduled job. Returns True if found."""
        # Remove from APScheduler
        try:
            self._scheduler.remove_job(job_id)
        except Exception:
            pass  # job not in APScheduler (disabled or never started)

        # Delete from DB
        async with async_session() as db:
            result = await db.execute(
                text("DELETE FROM scheduled_jobs WHERE id = :id"),
                {"id": job_id},
            )
            await db.commit()
            return result.rowcount > 0

    async def trigger(self, job_id: str) -> dict | None:
        """Manually trigger a one-off run of a job immediately.

        Returns ``{"trace_id": ...}`` on success, ``None`` if job not found.
        """
        job = await self.get(job_id)
        if job is None:
            return None
        if self._runtime is None:
            logger.error("Cannot trigger job: HarnessRuntime not set")
            return None

        trace_id = uuid.uuid4().hex
        # Run the job in the background — don't block the API call.
        import asyncio

        asyncio.ensure_future(
            self._execute_job(job, trace_id)
        )
        return {"trace_id": trace_id}

    async def list_jobs(self, workspace_id: str) -> list[ScheduledJob]:
        """List all scheduled jobs for a workspace."""
        async with async_session() as db:
            result = await db.execute(
                text(
                    "SELECT * FROM scheduled_jobs WHERE workspace_id = :ws "
                    "ORDER BY created_at ASC"
                ),
                {"ws": workspace_id},
            )
            return [self._row_to_job(r) for r in result.fetchall()]

    async def get(self, job_id: str) -> ScheduledJob | None:
        """Get a single job by id."""
        async with async_session() as db:
            result = await db.execute(
                text("SELECT * FROM scheduled_jobs WHERE id = :id"),
                {"id": job_id},
            )
            row = result.fetchone()
            return self._row_to_job(row) if row else None

    async def update(
        self, job_id: str, **fields
    ) -> ScheduledJob | None:
        """Update fields of a scheduled job. Re-registers with APScheduler."""
        job = await self.get(job_id)
        if job is None:
            return None

        for key, value in fields.items():
            if hasattr(job, key) and value is not None:
                setattr(job, key, value)

        # Re-persist and re-register
        return await self.schedule(job)

    async def _execute_job(self, job: ScheduledJob, trace_id: str) -> None:
        """Execute a scheduled job by calling HarnessRuntime.run()."""
        if self._runtime is None:
            logger.error("Cannot execute job: HarnessRuntime not set")
            return

        logger.info("Executing scheduled job %s (%s) trace=%s", job.id, job.name, trace_id)
        try:
            from src.runtime.models import RuntimeConfig

            config = RuntimeConfig(
                workspace_id=job.workspace_id,
                agent=job.agent_id,
                model="",
                temperature=0.7,
                max_tokens=4096,
            )

            # Drain the async generator to completion
            async for _event in self._runtime.run(
                session_id=f"sched-{job.id}-{trace_id[:8]}",
                messages=job.input_messages,
                config=config,
                user_id="scheduler",
                trace_id=trace_id,
            ):
                pass  # events are discarded for scheduled runs

            # Update last_run_at
            async with async_session() as db:
                await db.execute(
                    text(
                        "UPDATE scheduled_jobs SET last_run_at = :lra "
                        "WHERE id = :id"
                    ),
                    {
                        "lra": datetime.now(timezone.utc).isoformat(),
                        "id": job.id,
                    },
                )
                await db.commit()

        except Exception as exc:
            logger.exception("Scheduled job %s failed: %s", job.id, exc)

    def _add_aps_job(self, job: ScheduledJob) -> None:
        """Register a job with APScheduler."""
        try:
            # Remove existing job if present (for updates)
            try:
                self._scheduler.remove_job(job.id)
            except Exception:
                pass

            trigger = CronTrigger.from_crontab(job.cron)
            self._scheduler.add_job(
                self._aps_callback,
                trigger=trigger,
                args=[job.id],
                id=job.id,
                name=job.name,
                replace_existing=True,
            )
            # Update next_run_at
            aps_job = self._scheduler.get_job(job.id)
            next_run = getattr(aps_job, "next_run_time", None) if aps_job else None
            if next_run is not None:
                job.next_run_at = next_run.astimezone(timezone.utc)
        except Exception as exc:
            logger.warning("Failed to register APScheduler job %s: %s", job.id, exc)

    async def _aps_callback(self, job_id: str) -> None:
        """APScheduler callback — executes the job."""
        job = await self.get(job_id)
        if job is None or not job.enabled:
            return
        trace_id = uuid.uuid4().hex
        await self._execute_job(job, trace_id)

    def _row_to_job(self, row) -> ScheduledJob:
        messages = json.loads(row.input_messages) if row.input_messages else []
        created_at = self._parse_dt(row.created_at)
        last_run_at = self._parse_dt(row.last_run_at)
        next_run_at = self._parse_dt(row.next_run_at)
        return ScheduledJob(
            id=row.id,
            workspace_id=row.workspace_id,
            agent_id=row.agent_id,
            name=row.name,
            cron=row.cron,
            input_messages=messages,
            enabled=bool(row.enabled),
            created_at=created_at,
            last_run_at=last_run_at,
            next_run_at=next_run_at,
        )

    def _parse_dt(self, val) -> datetime | None:
        if not val:
            return None
        if isinstance(val, datetime):
            return val
        try:
            return datetime.fromisoformat(str(val))
        except (ValueError, TypeError):
            return None
