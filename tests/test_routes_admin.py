import uuid as _uuid

import pytest
from httpx import AsyncClient, ASGITransport

from src.gateway.auth.jwt import create_jwt


def _admin_token():
    """tenant_admin short-circuits every workspace check — no DB rows needed."""
    return create_jwt({
        "id": "u-1",
        "sub": "u-1",
        "tenant_id": "t-1",
        "email": "admin@test.com",
        "role": "tenant_admin",
    })


def _member_token():
    """Plain tenant member — should be forbidden from admin routes."""
    return create_jwt({
        "id": "u-2",
        "sub": "u-2",
        "tenant_id": "t-1",
        "email": "member@test.com",
        "role": "member",
    })


@pytest.fixture
def app():
    from src.main import create_app
    return create_app()


@pytest.mark.asyncio
async def test_users_me(app):
    from src.infra.db.models import Tenant, User
    from src.infra.db.engine import async_session

    async with async_session() as session:
        # get-or-create tenant (other tests in this file use t-1 too)
        t = await session.get(Tenant, "t-1")
        if not t:
            session.add(Tenant(id="t-1", name="T", domain="t-1.test"))
            await session.flush()
        user = User(
            tenant_id="t-1",
            email="me@test.com",
            name="Me",
            role="member",
        )
        session.add(user)
        await session.commit()
        uid = user.id

    token = create_jwt({
        "id": uid,
        "sub": uid,
        "tenant_id": "t-1",
        "email": "me@test.com",
        "role": "member",
    })
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/users/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "workspace_ids" in data
        assert "workspaces" not in data


@pytest.mark.asyncio
async def test_users_me_unauthorized(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/users/me")
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_admin_list_users(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/admin/users",
            headers={"Authorization": f"Bearer {_admin_token()}"},
        )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_admin_list_users_forbidden(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/admin/users",
            headers={"Authorization": f"Bearer {_member_token()}"},
        )
        assert resp.status_code == 403
