"""Tests for P3b-P3: Scheduler and ScheduledJob classes.

Unit tests for the cron-based scheduler — DB persistence, CRUD operations,
and APScheduler lifecycle. The APScheduler is only started for the
``test_start_and_shutdown`` test; all other tests use ``Scheduler(runtime=None)``
and exercise only the DB operations to avoid event-loop interference.
"""
import pytest
from datetime import datetime, timezone
from sqlalchemy import text

from src.infra.db.engine import engine, async_session
from src.runtime.harness.scheduler import ScheduledJob, Scheduler


@pytest.fixture(autouse=True)
async def setup_db():
    async with engine.begin() as conn:
        await conn.execute(text(
            "CREATE TABLE IF NOT EXISTS scheduled_jobs ("
            "id TEXT NOT NULL PRIMARY KEY,"
            "workspace_id TEXT NOT NULL,"
            "agent_id TEXT NOT NULL,"
            "name TEXT NOT NULL,"
            "cron TEXT NOT NULL,"
            "input_messages TEXT NOT NULL DEFAULT '[]',"
            "enabled INTEGER NOT NULL DEFAULT 1,"
            "created_at TEXT NOT NULL,"
            "last_run_at TEXT,"
            "next_run_at TEXT"
            ")"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_jobs_workspace "
            "ON scheduled_jobs (workspace_id)"
        ))
    yield
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS scheduled_jobs"))


def _make_job(id="j1", ws="ws-1", agent="a-1", name="Daily Report", cron="0 9 * * *"):
    return ScheduledJob(
        id=id, workspace_id=ws, agent_id=agent, name=name, cron=cron,
        input_messages=[{"role": "user", "content": "Generate report"}],
        created_at=datetime.now(timezone.utc),
    )


# ── TestScheduledJob ────────────────────────────────────────────────────
class TestScheduledJob:
    @pytest.mark.asyncio
    async def test_defaults(self):
        job = ScheduledJob(
            id="j1", workspace_id="ws", agent_id="a1",
            name="test", cron="0 9 * * *",
        )
        assert job.input_messages == []
        assert job.enabled is True
        assert job.created_at is None
        assert job.last_run_at is None
        assert job.next_run_at is None

    @pytest.mark.asyncio
    async def test_with_all_fields(self):
        now = datetime.now(timezone.utc)
        job = ScheduledJob(
            id="j2", workspace_id="ws-2", agent_id="a-2",
            name="Full Job", cron="0 9 * * *",
            input_messages=[{"role": "user", "content": "hello"}],
            enabled=False,
            created_at=now,
            last_run_at=now,
            next_run_at=now,
        )
        assert job.id == "j2"
        assert job.workspace_id == "ws-2"
        assert job.agent_id == "a-2"
        assert job.name == "Full Job"
        assert job.cron == "0 9 * * *"
        assert job.input_messages == [{"role": "user", "content": "hello"}]
        assert job.enabled is False
        assert job.created_at == now
        assert job.last_run_at == now
        assert job.next_run_at == now


# ── TestScheduler ───────────────────────────────────────────────────────
class TestScheduler:
    @pytest.mark.asyncio
    async def test_start_and_shutdown(self):
        scheduler = Scheduler(runtime=None)
        await scheduler.start()
        assert scheduler._started is True
        await scheduler.shutdown()
        assert scheduler._started is False

    @pytest.mark.asyncio
    async def test_schedule_persists_to_db(self):
        scheduler = Scheduler(runtime=None)
        job = _make_job(id="persist-1")
        await scheduler.schedule(job)
        fetched = await scheduler.get("persist-1")
        assert fetched is not None
        assert fetched.id == "persist-1"
        assert fetched.workspace_id == "ws-1"
        assert fetched.agent_id == "a-1"
        assert fetched.name == "Daily Report"
        assert fetched.cron == "0 9 * * *"
        assert fetched.input_messages == [{"role": "user", "content": "Generate report"}]
        assert fetched.enabled is True

    @pytest.mark.asyncio
    async def test_schedule_valid_cron(self):
        # APScheduler only populates next_run_time when the scheduler is
        # started, so we start it here and shut it down in finally.
        scheduler = Scheduler(runtime=None)
        await scheduler.start()
        try:
            job = _make_job(id="valid-cron", cron="0 9 * * *")
            result = await scheduler.schedule(job)
            assert result is not None
            # next_run_at is set by APScheduler when the cron is valid
            assert result.next_run_at is not None
        finally:
            await scheduler.shutdown()

    @pytest.mark.asyncio
    async def test_schedule_invalid_cron_does_not_crash(self):
        scheduler = Scheduler(runtime=None)
        job = _make_job(id="bad-cron", cron="invalid cron")
        # _add_aps_job logs a warning but doesn't raise
        result = await scheduler.schedule(job)
        assert result is not None
        assert result.id == "bad-cron"

    @pytest.mark.asyncio
    async def test_list_jobs(self):
        scheduler = Scheduler(runtime=None)
        await scheduler.schedule(_make_job(id="list-1", ws="ws-list"))
        await scheduler.schedule(_make_job(id="list-2", ws="ws-list"))
        jobs = await scheduler.list_jobs("ws-list")
        assert len(jobs) == 2
        ids = {j.id for j in jobs}
        assert ids == {"list-1", "list-2"}

    @pytest.mark.asyncio
    async def test_list_jobs_isolated_per_workspace(self):
        scheduler = Scheduler(runtime=None)
        await scheduler.schedule(_make_job(id="iso-1", ws="ws-a"))
        await scheduler.schedule(_make_job(id="iso-2", ws="ws-b"))
        jobs_a = await scheduler.list_jobs("ws-a")
        jobs_b = await scheduler.list_jobs("ws-b")
        assert len(jobs_a) == 1
        assert jobs_a[0].id == "iso-1"
        assert len(jobs_b) == 1
        assert jobs_b[0].id == "iso-2"

    @pytest.mark.asyncio
    async def test_get_returns_none_for_missing(self):
        scheduler = Scheduler(runtime=None)
        result = await scheduler.get("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_cancel(self):
        scheduler = Scheduler(runtime=None)
        await scheduler.schedule(_make_job(id="cancel-1"))
        result = await scheduler.cancel("cancel-1")
        assert result is True
        assert await scheduler.get("cancel-1") is None

    @pytest.mark.asyncio
    async def test_cancel_missing_returns_false(self):
        scheduler = Scheduler(runtime=None)
        result = await scheduler.cancel("nonexistent")
        assert result is False

    @pytest.mark.asyncio
    async def test_update(self):
        scheduler = Scheduler(runtime=None)
        await scheduler.schedule(_make_job(id="update-1", name="Original"))
        updated = await scheduler.update("update-1", name="Updated Name")
        assert updated is not None
        assert updated.name == "Updated Name"
        fetched = await scheduler.get("update-1")
        assert fetched.name == "Updated Name"

    @pytest.mark.asyncio
    async def test_update_missing_returns_none(self):
        scheduler = Scheduler(runtime=None)
        result = await scheduler.update("nonexistent", name="X")
        assert result is None

    @pytest.mark.asyncio
    async def test_set_runtime(self):
        scheduler = Scheduler(runtime=None)
        assert scheduler._runtime is None
        sentinel = object()
        scheduler.set_runtime(sentinel)
        assert scheduler._runtime is sentinel

    @pytest.mark.asyncio
    async def test_trigger_missing_returns_none(self):
        scheduler = Scheduler(runtime=None)
        result = await scheduler.trigger("nonexistent")
        assert result is None
