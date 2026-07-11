import pytest
from httpx import AsyncClient, ASGITransport

from src.gateway.auth.jwt import create_jwt


def _admin_token():
    return create_jwt({
        "id": "admin-1",
        "sub": "admin-1",
        "tenant_id": "tenant-1",
        "email": "admin@test.com",
        "role": "tenant_admin",
    })


def _member_token():
    return create_jwt({
        "id": "member-1",
        "sub": "member-1",
        "tenant_id": "tenant-1",
        "email": "member@test.com",
        "role": "member",
    })


@pytest.fixture
def app():
    from src.main import create_app
    return create_app()


@pytest.mark.asyncio
async def test_list_workspaces_as_admin(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/workspaces",
            headers={"Authorization": f"Bearer {_admin_token()}"},
        )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_list_workspaces_as_member_forbidden(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/workspaces",
            headers={"Authorization": f"Bearer {_member_token()}"},
        )
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_create_workspace(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        from src.infra.db.models import Tenant
        from src.infra.db.engine import async_session
        async with async_session() as session:
            session.add(Tenant(name="Test", domain="test.com"))
            await session.commit()

        resp = await client.post(
            "/api/v1/workspaces",
            json={"name": "Test Workspace"},
            headers={"Authorization": f"Bearer {_admin_token()}"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["name"] == "Test Workspace"


@pytest.mark.asyncio
async def test_create_workspace_seeds_default_agent(app):
    """Creating a workspace should auto-seed a default 'Assistant' agent
    so the chat page always has at least one agent to pick."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        from src.infra.db.models import Tenant
        from src.infra.db.engine import async_session
        async with async_session() as session:
            session.add(Tenant(name="Test2", domain="test2.com"))
            await session.commit()

        resp = await client.post(
            "/api/v1/workspaces",
            json={"name": "Seeded Workspace"},
            headers={"Authorization": f"Bearer {_admin_token()}"},
        )
        assert resp.status_code == 201
        ws_id = resp.json()["id"]

        # The workspace should have exactly one agent named "Assistant".
        agents_resp = await client.get(
            f"/api/v1/workspaces/{ws_id}/agents",
            headers={"Authorization": f"Bearer {_admin_token()}"},
        )
        assert agents_resp.status_code == 200
        agents = agents_resp.json()
        assert len(agents) == 1
        assert agents[0]["name"] == "Assistant"
        assert agents[0]["framework"] == "deepagents"


@pytest.mark.asyncio
async def test_create_workspace_member_forbidden(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/workspaces",
            json={"name": "Should Fail"},
            headers={"Authorization": f"Bearer {_member_token()}"},
        )
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_workspaces_require_auth(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/workspaces")
        assert resp.status_code == 401

        resp = await client.post("/api/v1/workspaces", json={"name": "x"})
        assert resp.status_code == 401


# ─── P0-2: workspace-level RBAC for member endpoints ──────────────────────────


@pytest.mark.asyncio
async def test_list_members_requires_workspace_admin(app):
    """A plain `member` of the workspace cannot list members."""
    from tests.conftest import setup_workspace_with_member
    from src.infra.db.session import get_db

    async for session in get_db():
        token = await setup_workspace_with_member(
            session,
            ws_id="ws-list-1",
            tenant_id="t-list-1",
            user_id="mem-1",
            user_role="member",
            email="mem1@test.com",
        )
        break

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/workspaces/ws-list-1/members",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_list_members_as_workspace_admin(app):
    from tests.conftest import setup_workspace_with_member
    from src.infra.db.session import get_db

    async for session in get_db():
        token = await setup_workspace_with_member(
            session,
            ws_id="ws-list-2",
            tenant_id="t-list-2",
            user_id="admin-1",
            user_role="workspace_admin",
            tenant_role="workspace_admin",
            email="admin1@test.com",
        )
        break

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/workspaces/ws-list-2/members",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)
        # The admin themselves must show up with the WorkspaceMember role.
        roles = [m["role"] for m in body]
        assert "workspace_admin" in roles


@pytest.mark.asyncio
async def test_list_members_as_tenant_admin(app):
    """tenant_admin short-circuits — no WorkspaceMember row needed."""
    import uuid as _uuid
    from src.infra.db.models import Tenant, Workspace
    from src.infra.db.engine import async_session

    tid = f"t-ta-{_uuid.uuid4().hex[:8]}"
    ws_id = f"ws-ta-{_uuid.uuid4().hex[:8]}"
    async with async_session() as session:
        session.add(Tenant(id=tid, name="T", domain=f"{tid}.test"))
        session.add(Workspace(id=ws_id, tenant_id=tid, name="WS"))
        await session.commit()

    token = create_jwt({
        "id": "admin-1",
        "sub": "admin-1",
        "tenant_id": tid,
        "email": "admin@test.com",
        "role": "tenant_admin",
    })
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/workspaces/{ws_id}/members",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_list_members_non_member_forbidden(app):
    """A user with no WorkspaceMember row in this workspace gets 403."""
    import uuid as _uuid
    from src.infra.db.models import Workspace
    from tests.conftest import setup_workspace_with_member
    from src.infra.db.session import get_db

    suffix = _uuid.uuid4().hex[:8]
    tid = f"t-iso-{suffix}"
    ws_a = f"ws-A-{suffix}"
    ws_b = f"ws-B-{suffix}"

    async for session in get_db():
        # User is a member of ws-A but not ws-B
        token = await setup_workspace_with_member(
            session,
            ws_id=ws_a,
            tenant_id=tid,
            user_id=f"iso-{suffix}",
            user_role="workspace_admin",
            tenant_role="workspace_admin",
            email=f"iso-{suffix}@test.com",
        )
        # Create ws-B without this user
        session.add(Workspace(id=ws_b, tenant_id=tid, name="WS B"))
        await session.commit()
        break

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/workspaces/{ws_b}/members",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403
