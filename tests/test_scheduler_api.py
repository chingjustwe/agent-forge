"""Tests for P3a §6.7: Scheduler management API.

Mirrors the fixture pattern from ``test_harness_api.py``: module-level ``app``
fixture, ``_token`` / ``_seed`` helpers, ``ASGITransport`` + ``AsyncClient``.

Two autouse fixtures prepare the environment:
- ``setup_db``: creates the ``scheduled_jobs`` table (raw SQL, not ORM).
- ``setup_harness``: resets the HarnessRegistry singleton and re-creates it
  so every test starts with a fresh Scheduler instance.
"""
import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from src.gateway.auth.jwt import create_jwt
from src.infra.db.engine import async_session
from src.infra.db.models import Tenant, User, Workspace, WorkspaceMember


# ── Constants ───────────────────────────────────────────────────────────
WS_ID = "ws-sched-test"
TENANT_ID = "t-sched-test"
ADMIN_USER = "u-sched-admin"
MEMBER_USER = "u-sched-member"


# ── Fixtures & helpers ──────────────────────────────────────────────────
@pytest.fixture
def app():
    from src.main import create_app
    return create_app()


def _token(user_id: str, tenant_id: str, role: str = "member", email: str | None = None):
    return create_jwt({
        "id": user_id,
        "sub": user_id,
        "tenant_id": tenant_id,
        "email": email or f"{user_id}@test.com",
        "role": role,
    })


async def _seed(
    ws_id: str,
    tenant_id: str,
    user_id: str,
    ws_role: str = "workspace_admin",
    tenant_role: str | None = None,
    email: str | None = None,
) -> str:
    """Seed tenant + workspace + user + WorkspaceMember. Returns JWT."""
    if tenant_role is None:
        tenant_role = ws_role
    async with async_session() as session:
        if not await session.get(Tenant, tenant_id):
            session.add(Tenant(id=tenant_id, name="T", domain=f"{tenant_id}.test"))
            await session.flush()
        if not await session.get(Workspace, ws_id):
            session.add(Workspace(id=ws_id, tenant_id=tenant_id, name=f"WS {ws_id}"))
            await session.flush()
        if not await session.get(User, user_id):
            session.add(
                User(
                    id=user_id,
                    tenant_id=tenant_id,
                    email=email or f"{user_id}@test.com",
                    name=user_id,
                    role=tenant_role,
                )
            )
            await session.flush()
        if not await session.get(WorkspaceMember, (ws_id, user_id)):
            session.add(
                WorkspaceMember(workspace_id=ws_id, user_id=user_id, role=ws_role)
            )
        await session.commit()
    return _token(user_id, tenant_id, role=tenant_role, email=email)


@pytest.fixture(autouse=True)
async def setup_db():
    """Create scheduled_jobs table for scheduler API tests."""
    from sqlalchemy import text
    from src.infra.db.engine import engine
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
    # Clean up jobs between tests so list assertions are isolated
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM scheduled_jobs"))


@pytest.fixture(autouse=True)
async def setup_harness():
    """Reset and re-create the HarnessRegistry singleton per test."""
    from src.runtime.harness.registry import reset_registry, HarnessRegistry
    reset_registry()
    HarnessRegistry.create()
    yield
    reset_registry()


# ── TestSchedulerAPI ────────────────────────────────────────────────────
class TestSchedulerAPI:
    @pytest.mark.asyncio
    async def test_list_jobs_empty(self, app):
        tok = await _seed(WS_ID, TENANT_ID, ADMIN_USER, ws_role="workspace_admin")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(
                f"/api/v1/workspaces/{WS_ID}/scheduler/jobs",
                headers={"Authorization": f"Bearer {tok}"},
            )
        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.asyncio
    async def test_list_jobs_requires_auth(self, app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(f"/api/v1/workspaces/{WS_ID}/scheduler/jobs")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_create_job(self, app):
        tok = await _seed(WS_ID, TENANT_ID, ADMIN_USER, ws_role="workspace_admin")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/workspaces/{WS_ID}/scheduler/jobs",
                headers={"Authorization": f"Bearer {tok}"},
                json={
                    "name": "Daily Report",
                    "agent_id": "test-agent-1",
                    "cron": "0 9 * * *",
                    "input_messages": [{"role": "user", "content": "Generate report"}],
                },
            )
        assert resp.status_code == 201
        body = resp.json()
        assert "id" in body
        assert body["name"] == "Daily Report"
        assert body["agent_id"] == "test-agent-1"
        assert body["cron"] == "0 9 * * *"

    @pytest.mark.asyncio
    async def test_create_job_invalid_cron(self, app):
        tok = await _seed(WS_ID, TENANT_ID, ADMIN_USER, ws_role="workspace_admin")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/workspaces/{WS_ID}/scheduler/jobs",
                headers={"Authorization": f"Bearer {tok}"},
                json={
                    "name": "Bad Cron",
                    "agent_id": "test-agent-1",
                    "cron": "not-a-cron",
                },
            )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_job_member_forbidden(self, app):
        tok = await _seed(WS_ID, TENANT_ID, MEMBER_USER, ws_role="member")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/workspaces/{WS_ID}/scheduler/jobs",
                headers={"Authorization": f"Bearer {tok}"},
                json={
                    "name": "Should Fail",
                    "agent_id": "test-agent-1",
                    "cron": "0 9 * * *",
                },
            )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_list_jobs_after_create(self, app):
        tok = await _seed(WS_ID, TENANT_ID, ADMIN_USER, ws_role="workspace_admin")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            create_resp = await ac.post(
                f"/api/v1/workspaces/{WS_ID}/scheduler/jobs",
                headers={"Authorization": f"Bearer {tok}"},
                json={
                    "name": "Listed Job",
                    "agent_id": "test-agent-1",
                    "cron": "0 9 * * *",
                },
            )
            assert create_resp.status_code == 201
            list_resp = await ac.get(
                f"/api/v1/workspaces/{WS_ID}/scheduler/jobs",
                headers={"Authorization": f"Bearer {tok}"},
            )
        assert list_resp.status_code == 200
        jobs = list_resp.json()
        assert len(jobs) == 1
        assert jobs[0]["name"] == "Listed Job"

    @pytest.mark.asyncio
    async def test_update_job(self, app):
        tok = await _seed(WS_ID, TENANT_ID, ADMIN_USER, ws_role="workspace_admin")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            create_resp = await ac.post(
                f"/api/v1/workspaces/{WS_ID}/scheduler/jobs",
                headers={"Authorization": f"Bearer {tok}"},
                json={
                    "name": "Original",
                    "agent_id": "test-agent-1",
                    "cron": "0 9 * * *",
                },
            )
            assert create_resp.status_code == 201
            job_id = create_resp.json()["id"]
            resp = await ac.put(
                f"/api/v1/workspaces/{WS_ID}/scheduler/jobs/{job_id}",
                headers={"Authorization": f"Bearer {tok}"},
                json={"name": "Updated Name"},
            )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Updated Name"

    @pytest.mark.asyncio
    async def test_update_job_invalid_cron(self, app):
        tok = await _seed(WS_ID, TENANT_ID, ADMIN_USER, ws_role="workspace_admin")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            create_resp = await ac.post(
                f"/api/v1/workspaces/{WS_ID}/scheduler/jobs",
                headers={"Authorization": f"Bearer {tok}"},
                json={
                    "name": "Cron Update",
                    "agent_id": "test-agent-1",
                    "cron": "0 9 * * *",
                },
            )
            assert create_resp.status_code == 201
            job_id = create_resp.json()["id"]
            resp = await ac.put(
                f"/api/v1/workspaces/{WS_ID}/scheduler/jobs/{job_id}",
                headers={"Authorization": f"Bearer {tok}"},
                json={"cron": "bad-cron-expr"},
            )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_update_job_missing_404(self, app):
        tok = await _seed(WS_ID, TENANT_ID, ADMIN_USER, ws_role="workspace_admin")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.put(
                f"/api/v1/workspaces/{WS_ID}/scheduler/jobs/nonexistent",
                headers={"Authorization": f"Bearer {tok}"},
                json={"name": "X"},
            )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_job(self, app):
        tok = await _seed(WS_ID, TENANT_ID, ADMIN_USER, ws_role="workspace_admin")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            create_resp = await ac.post(
                f"/api/v1/workspaces/{WS_ID}/scheduler/jobs",
                headers={"Authorization": f"Bearer {tok}"},
                json={
                    "name": "To Delete",
                    "agent_id": "test-agent-1",
                    "cron": "0 9 * * *",
                },
            )
            assert create_resp.status_code == 201
            job_id = create_resp.json()["id"]
            resp = await ac.delete(
                f"/api/v1/workspaces/{WS_ID}/scheduler/jobs/{job_id}",
                headers={"Authorization": f"Bearer {tok}"},
            )
        assert resp.status_code == 204

    @pytest.mark.asyncio
    async def test_delete_job_missing_404(self, app):
        tok = await _seed(WS_ID, TENANT_ID, ADMIN_USER, ws_role="workspace_admin")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.delete(
                f"/api/v1/workspaces/{WS_ID}/scheduler/jobs/nonexistent",
                headers={"Authorization": f"Bearer {tok}"},
            )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_trigger_job(self, app):
        tok = await _seed(WS_ID, TENANT_ID, ADMIN_USER, ws_role="workspace_admin")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            create_resp = await ac.post(
                f"/api/v1/workspaces/{WS_ID}/scheduler/jobs",
                headers={"Authorization": f"Bearer {tok}"},
                json={
                    "name": "Trigger Me",
                    "agent_id": "test-agent-1",
                    "cron": "0 9 * * *",
                    "input_messages": [{"role": "user", "content": "hi"}],
                },
            )
            assert create_resp.status_code == 201
            job_id = create_resp.json()["id"]

            # Set a fake runtime so trigger() returns a trace_id instead of None
            from src.runtime.harness.registry import get_registry
            scheduler = get_registry().scheduler
            scheduler.set_runtime(object())

            resp = await ac.post(
                f"/api/v1/workspaces/{WS_ID}/scheduler/jobs/{job_id}/trigger",
                headers={"Authorization": f"Bearer {tok}"},
            )
            assert resp.status_code == 202
            assert "trace_id" in resp.json()

            # Let the fire-and-forget background task settle to avoid
            # pending-task warnings when the event loop closes.
            await asyncio.sleep(0.1)

    @pytest.mark.asyncio
    async def test_trigger_job_missing_404(self, app):
        tok = await _seed(WS_ID, TENANT_ID, ADMIN_USER, ws_role="workspace_admin")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/workspaces/{WS_ID}/scheduler/jobs/nonexistent/trigger",
                headers={"Authorization": f"Bearer {tok}"},
            )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_member_can_read(self, app):
        tok = await _seed(WS_ID, TENANT_ID, MEMBER_USER, ws_role="member")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(
                f"/api/v1/workspaces/{WS_ID}/scheduler/jobs",
                headers={"Authorization": f"Bearer {tok}"},
            )
        assert resp.status_code == 200
