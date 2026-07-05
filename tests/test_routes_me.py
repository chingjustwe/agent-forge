import uuid

import pytest
from httpx import AsyncClient, ASGITransport

from src.gateway.auth.jwt import create_jwt


@pytest.fixture
def app():
    from src.main import create_app
    return create_app()


@pytest.mark.asyncio
async def test_list_my_workspaces_requires_auth(app):
    """No token → 401."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/me/workspaces")
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_list_my_workspaces_as_tenant_admin(app):
    """tenant_admin sees all (non-archived) workspaces in their tenant,
    each with role=workspace_admin.
    """
    suffix = uuid.uuid4().hex[:8]
    tid = f"t-ta-{suffix}"
    ws1 = f"ws-ta-1-{suffix}"
    ws2 = f"ws-ta-2-{suffix}"
    ws_archived = f"ws-ta-arc-{suffix}"

    from src.infra.db.models import Tenant, Workspace
    from src.infra.db.engine import async_session

    async with async_session() as session:
        session.add(Tenant(id=tid, name="T", domain=f"{tid}.test"))
        session.add(Workspace(id=ws1, tenant_id=tid, name="WS One"))
        session.add(Workspace(id=ws2, tenant_id=tid, name="WS Two"))
        session.add(Workspace(id=ws_archived, tenant_id=tid, name="WS Arc", archived=1))
        await session.commit()

    token = create_jwt({
        "id": f"admin-{suffix}",
        "sub": f"admin-{suffix}",
        "tenant_id": tid,
        "email": f"admin-{suffix}@test.com",
        "role": "tenant_admin",
    })

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/me/workspaces",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)
        ids = {w["id"] for w in body}
        assert ws1 in ids
        assert ws2 in ids
        # archived workspace must NOT appear
        assert ws_archived not in ids
        # tenant_admin sees every workspace as workspace_admin
        for w in body:
            assert w["role"] == "workspace_admin"
        # names are populated
        names = {w["name"] for w in body}
        assert {"WS One", "WS Two"}.issubset(names)


@pytest.mark.asyncio
async def test_list_my_workspaces_as_member(app):
    """A plain member sees only the workspaces they joined, with their
    WorkspaceMember.role.
    """
    from tests.conftest import setup_workspace_with_member
    from src.infra.db.session import get_db

    async for session in get_db():
        token = await setup_workspace_with_member(
            session,
            ws_id="ws-me-1",
            tenant_id="t-me-1",
            user_id="mem-me-1",
            user_role="workspace_admin",
            email="mem-me-1@test.com",
        )
        break

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/me/workspaces",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)
        assert len(body) == 1
        ws = body[0]
        assert ws["id"] == "ws-me-1"
        assert ws["role"] == "workspace_admin"
        assert ws["name"] == "WS ws-me-1"


@pytest.mark.asyncio
async def test_list_my_workspaces_member_no_memberships(app):
    """A member with no WorkspaceMember rows gets an empty array."""
    suffix = uuid.uuid4().hex[:8]
    token = create_jwt({
        "id": f"lonely-{suffix}",
        "sub": f"lonely-{suffix}",
        "tenant_id": f"t-lonely-{suffix}",
        "email": f"lonely-{suffix}@test.com",
        "role": "member",
    })

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/me/workspaces",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json() == []


@pytest.mark.asyncio
async def test_list_my_workspaces_excludes_archived(app):
    """Archived workspaces a member belongs to must not appear in the list."""
    from src.infra.db.models import Workspace
    from tests.conftest import setup_workspace_with_member
    from src.infra.db.session import get_db

    async for session in get_db():
        token = await setup_workspace_with_member(
            session,
            ws_id="ws-arc-1",
            tenant_id="t-arc-1",
            user_id="mem-arc-1",
            user_role="member",
            email="mem-arc-1@test.com",
        )
        # Archive the workspace the user belongs to.
        ws = await session.get(Workspace, "ws-arc-1")
        assert ws is not None
        ws.archived = 1
        await session.commit()
        break

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/me/workspaces",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        ids = [w["id"] for w in body]
        assert "ws-arc-1" not in ids


@pytest.mark.asyncio
async def test_list_my_workspaces_member_multiple(app):
    """A member of two workspaces sees both, each with the right role."""
    from src.infra.db.models import Workspace, WorkspaceMember
    from tests.conftest import setup_workspace_with_member
    from src.infra.db.session import get_db

    async for session in get_db():
        token = await setup_workspace_with_member(
            session,
            ws_id="ws-multi-1",
            tenant_id="t-multi-1",
            user_id="mem-multi-1",
            user_role="member",
            email="mem-multi-1@test.com",
        )
        # Add a second workspace + membership for the same user.
        session.add(Workspace(id="ws-multi-2", tenant_id="t-multi-1", name="WS Multi 2"))
        session.add(
            WorkspaceMember(
                workspace_id="ws-multi-2",
                user_id="mem-multi-1",
                role="workspace_admin",
            )
        )
        await session.commit()
        break

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/me/workspaces",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        by_id = {w["id"]: w["role"] for w in body}
        assert by_id.get("ws-multi-1") == "member"
        assert by_id.get("ws-multi-2") == "workspace_admin"


@pytest.mark.asyncio
async def test_me_workspaces_cache_ttl(app):
    """P0-4: /me/workspaces caches results for 60s.

    Within TTL, a second request must return the cached (stale) list even
    if DB state changed. After TTL expiry, the next request re-queries DB.
    """
    from src.gateway.routes.me import _workspace_cache, _CACHE_TTL_SECONDS
    from src.infra.db.models import Workspace, WorkspaceMember
    from tests.conftest import setup_workspace_with_member
    from src.infra.db.session import get_db

    # Reset cache to isolate this test
    _workspace_cache.clear()

    async for session in get_db():
        token = await setup_workspace_with_member(
            session,
            ws_id="ws-cache-1",
            tenant_id="t-cache-ttl",
            user_id="mem-cache-ttl",
            user_role="member",
            email="mem-cache-ttl@test.com",
        )
        break

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1) First request — cache miss, populates cache
        resp = await client.get(
            "/api/v1/me/workspaces",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        first_ids = {w["id"] for w in resp.json()}
        assert "ws-cache-1" in first_ids
        assert "mem-cache-ttl" in _workspace_cache

        # 2) Mutate DB: add a second workspace + membership for the same user
        async for session in get_db():
            session.add(Workspace(id="ws-cache-2", tenant_id="t-cache-ttl", name="WS2"))
            session.add(
                WorkspaceMember(
                    workspace_id="ws-cache-2",
                    user_id="mem-cache-ttl",
                    role="member",
                )
            )
            await session.commit()
            break

        # 3) Second request — cache HIT, must return stale list (ws-cache-2 absent)
        resp = await client.get(
            "/api/v1/me/workspaces",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        second_ids = {w["id"] for w in resp.json()}
        assert "ws-cache-2" not in second_ids, "cache should hide ws-cache-2 within TTL"

        # 4) Expire the cache entry by rewinding its timestamp into the past
        ts, data = _workspace_cache["mem-cache-ttl"]
        _workspace_cache["mem-cache-ttl"] = (ts - _CACHE_TTL_SECONDS - 1, data)

        # 5) Third request — cache miss, re-queries DB and picks up ws-cache-2
        resp = await client.get(
            "/api/v1/me/workspaces",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        third_ids = {w["id"] for w in resp.json()}
        assert "ws-cache-2" in third_ids, "expired cache must re-query DB"


@pytest.mark.asyncio
async def test_me_workspaces_cache_invalidation(app):
    """P0-4: add_member / remove_member must invalidate the cache for that user."""
    from src.gateway.routes.me import _workspace_cache
    from src.gateway.auth.jwt import create_jwt
    from src.infra.db.models import Tenant, User, Workspace, WorkspaceMember
    from src.infra.db.session import get_db

    # Reset cache to isolate this test
    _workspace_cache.clear()

    async for session in get_db():
        session.add(Tenant(id="t-inv", name="T", domain="t-inv.test"))
        session.add(Workspace(id="ws-inv-1", tenant_id="t-inv", name="WS1"))
        session.add(Workspace(id="ws-inv-2", tenant_id="t-inv", name="WS2"))
        session.add(
            User(
                id="mem-inv",
                tenant_id="t-inv",
                email="mem-inv@test.com",
                name="Mem",
                role="member",
            )
        )
        session.add(
            WorkspaceMember(workspace_id="ws-inv-1", user_id="mem-inv", role="member")
        )
        session.add(
            User(
                id="admin-inv",
                tenant_id="t-inv",
                email="admin-inv@test.com",
                name="Admin",
                role="tenant_admin",
            )
        )
        await session.commit()
        break

    member_token = create_jwt({
        "id": "mem-inv",
        "sub": "mem-inv",
        "tenant_id": "t-inv",
        "email": "mem-inv@test.com",
        "role": "member",
    })
    admin_token = create_jwt({
        "id": "admin-inv",
        "sub": "admin-inv",
        "tenant_id": "t-inv",
        "email": "admin-inv@test.com",
        "role": "tenant_admin",
    })

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1) First request — cache populated with [ws-inv-1]
        resp = await client.get(
            "/api/v1/me/workspaces",
            headers={"Authorization": f"Bearer {member_token}"},
        )
        assert resp.status_code == 200
        ids = {w["id"] for w in resp.json()}
        assert ids == {"ws-inv-1"}
        assert "mem-inv" in _workspace_cache

        # 2) Add mem-inv to ws-inv-2 via API → must invalidate cache
        resp = await client.post(
            "/api/v1/workspaces/ws-inv-2/members",
            json={"user_id": "mem-inv", "role": "member"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 201

        # Cache was invalidated for mem-inv
        assert "mem-inv" not in _workspace_cache

        # 3) Next request — DB re-query, picks up ws-inv-2
        resp = await client.get(
            "/api/v1/me/workspaces",
            headers={"Authorization": f"Bearer {member_token}"},
        )
        assert resp.status_code == 200
        ids = {w["id"] for w in resp.json()}
        assert ids == {"ws-inv-1", "ws-inv-2"}

        # 4) Remove mem-inv from ws-inv-2 → must invalidate cache again
        resp = await client.delete(
            "/api/v1/workspaces/ws-inv-2/members/mem-inv",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 204
        assert "mem-inv" not in _workspace_cache

        # 5) Next request — DB re-query, ws-inv-2 gone
        resp = await client.get(
            "/api/v1/me/workspaces",
            headers={"Authorization": f"Bearer {member_token}"},
        )
        assert resp.status_code == 200
        ids = {w["id"] for w in resp.json()}
        assert ids == {"ws-inv-1"}
