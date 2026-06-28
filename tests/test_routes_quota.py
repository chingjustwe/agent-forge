import pytest
from httpx import AsyncClient, ASGITransport

from src.gateway.auth.jwt import create_jwt
from src.infra.db.engine import async_session
from src.infra.db.models import Workspace


def _token(role: str = "member"):
    return create_jwt({
        "id": "test-user",
        "tenant_id": "test-tenant",
        "email": "test@test.com",
        "role": role,
        "workspace_ids": [],
    })


@pytest.fixture
def app():
    from src.main import create_app
    return create_app()


@pytest.mark.asyncio
async def test_get_quota_requires_auth(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/workspaces/ws1/quota")
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_get_quota_requires_member(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/workspaces/ws1/quota",
            headers={"Authorization": f"Bearer {_token('viewer')}"},
        )
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_get_quota_returns_data(app):
    async with async_session() as session:
        ws = Workspace(id="quota-test-1", tenant_id="t1", name="QT1")
        session.add(ws)
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/workspaces/quota-test-1/quota",
            headers={"Authorization": f"Bearer {_token()}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "max_tokens_per_day" in data
        assert "usage_today" in data
        assert "tokens_used" in data
        assert "cost_today" in data


@pytest.mark.asyncio
async def test_update_quota_requires_admin(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put(
            "/api/v1/workspaces/ws1/quota",
            json={"max_tokens_per_day": 500},
            headers={"Authorization": f"Bearer {_token()}"},
        )
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_update_quota(app):
    async with async_session() as session:
        ws = Workspace(id="quota-update-1", tenant_id="t1", name="QU1")
        session.add(ws)
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put(
            "/api/v1/workspaces/quota-update-1/quota",
            json={"max_tokens_per_day": 500},
            headers={"Authorization": f"Bearer {_token('workspace_admin')}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["quota"]["max_tokens_per_day"] == 500
