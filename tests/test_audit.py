import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select

from src.gateway.auth.jwt import create_jwt
from src.infra.db.engine import engine, async_session
from src.infra.db.models import Tenant, AuditLog, Base


def _admin_token():
    return create_jwt({
        "id": "admin-1",
        "tenant_id": "tenant-1",
        "email": "admin@test.com",
        "role": "tenant_admin",
        "workspace_ids": [],
    })


@pytest.fixture
def app():
    from src.main import create_app
    return create_app()


@pytest.fixture(autouse=True)
async def _create_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


@pytest.mark.asyncio
async def test_health_does_not_create_audit_log(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/health")
        assert resp.status_code == 200

    async with async_session() as session:
        result = await session.execute(
            select(AuditLog).where(AuditLog.action == "get./api/v1/health")
        )
        rows = result.scalars().all()
    assert len(rows) == 0


@pytest.mark.asyncio
async def test_create_workspace_creates_audit_log(app):
    async with async_session() as session:
        session.add(Tenant(name="Audit Test", domain="audit-test.com"))
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/workspaces",
            json={"name": "Audit Workspace"},
            headers={"Authorization": f"Bearer {_admin_token()}"},
        )
        assert resp.status_code == 201

    async with async_session() as session:
        result = await session.execute(
            select(AuditLog).where(AuditLog.action.contains("workspaces"))
        )
        rows = result.scalars().all()
    assert len(rows) > 0
    assert "workspaces" in rows[0].action
