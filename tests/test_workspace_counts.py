"""P1-2: real member_count / owner / agent_count stub fixes.

Follows TDD red-green-refactor — these tests were written BEFORE the
implementation changes and initially fail (RED), then pass after the
batch-query fixes (GREEN).
"""
import uuid as _uuid

import pytest
from httpx import ASGITransport, AsyncClient

from src.gateway.auth.jwt import create_jwt

pytestmark = pytest.mark.asyncio


def _admin_token(tenant_id: str) -> str:
    return create_jwt({
        "id": "admin-counts",
        "sub": "admin-counts",
        "tenant_id": tenant_id,
        "email": "admin@counts.test",
        "role": "tenant_admin",
    })


@pytest.fixture
def app():
    from src.main import create_app
    return create_app()


async def test_list_workspaces_returns_real_member_count(app):
    """`/api/v1/workspaces` must return real member_count, not the 0 stub."""
    from src.infra.db.engine import async_session
    from src.infra.db.models import Tenant, Workspace, User, WorkspaceMember

    suffix = _uuid.uuid4().hex[:8]
    tid = f"t-lw-{suffix}"
    ws_a = f"ws-a-{suffix}"  # will have 2 members
    ws_b = f"ws-b-{suffix}"  # will have 3 members

    async with async_session() as session:
        session.add(Tenant(id=tid, name="T", domain=f"{tid}.test"))
        session.add(Workspace(id=ws_a, tenant_id=tid, name="WS A"))
        session.add(Workspace(id=ws_b, tenant_id=tid, name="WS B"))
        await session.flush()
        for i in range(2):
            uid = f"u-a-{suffix}-{i}"
            session.add(User(id=uid, tenant_id=tid, email=f"{uid}@test.com", name=uid))
            session.add(WorkspaceMember(workspace_id=ws_a, user_id=uid, role="member"))
        for i in range(3):
            uid = f"u-b-{suffix}-{i}"
            session.add(User(id=uid, tenant_id=tid, email=f"{uid}@test.com", name=uid))
            session.add(WorkspaceMember(workspace_id=ws_b, user_id=uid, role="member"))
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/workspaces",
            headers={"Authorization": f"Bearer {_admin_token(tid)}"},
        )
        assert resp.status_code == 200
        by_id = {w["id"]: w for w in resp.json()}
        assert by_id[ws_a]["member_count"] == 2
        assert by_id[ws_b]["member_count"] == 3


async def test_admin_list_workspaces_returns_owner_email(app):
    """`/api/v1/admin/workspaces` must populate owner with the workspace_admin's email."""
    from src.infra.db.engine import async_session
    from src.infra.db.models import Tenant, Workspace, User, WorkspaceMember

    suffix = _uuid.uuid4().hex[:8]
    tid = f"t-ao-{suffix}"
    ws_id = f"ws-ao-{suffix}"
    owner_id = f"owner-{suffix}"
    owner_email = f"owner-{suffix}@test.com"

    async with async_session() as session:
        session.add(Tenant(id=tid, name="T", domain=f"{tid}.test"))
        session.add(Workspace(id=ws_id, tenant_id=tid, name="WS"))
        session.add(User(id=owner_id, tenant_id=tid, email=owner_email, name="Owner"))
        session.add(
            WorkspaceMember(
                workspace_id=ws_id, user_id=owner_id, role="workspace_admin"
            )
        )
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/admin/workspaces",
            headers={"Authorization": f"Bearer {_admin_token(tid)}"},
        )
        assert resp.status_code == 200
        by_id = {w["id"]: w for w in resp.json()}
        assert by_id[ws_id]["owner"] == owner_email


async def test_admin_list_workspaces_no_owner_returns_empty(app):
    """A workspace with no workspace_admin member must return owner == ''."""
    from src.infra.db.engine import async_session
    from src.infra.db.models import Tenant, Workspace, User, WorkspaceMember

    suffix = _uuid.uuid4().hex[:8]
    tid = f"t-no-{suffix}"
    ws_id = f"ws-no-{suffix}"

    async with async_session() as session:
        session.add(Tenant(id=tid, name="T", domain=f"{tid}.test"))
        session.add(Workspace(id=ws_id, tenant_id=tid, name="WS No Owner"))
        # Add only a plain member — no workspace_admin row
        uid = f"u-no-{suffix}"
        session.add(User(id=uid, tenant_id=tid, email=f"{uid}@test.com", name=uid))
        session.add(WorkspaceMember(workspace_id=ws_id, user_id=uid, role="member"))
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/admin/workspaces",
            headers={"Authorization": f"Bearer {_admin_token(tid)}"},
        )
        assert resp.status_code == 200
        by_id = {w["id"]: w for w in resp.json()}
        assert by_id[ws_id]["owner"] == ""


async def test_admin_list_workspaces_agent_count_is_zero_with_todo(app):
    """agent_count stays 0 until P2-2 implements the Agent table."""
    from src.infra.db.engine import async_session
    from src.infra.db.models import Tenant, Workspace

    suffix = _uuid.uuid4().hex[:8]
    tid = f"t-ac-{suffix}"
    ws_id = f"ws-ac-{suffix}"

    async with async_session() as session:
        session.add(Tenant(id=tid, name="T", domain=f"{tid}.test"))
        session.add(Workspace(id=ws_id, tenant_id=tid, name="WS AC"))
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/admin/workspaces",
            headers={"Authorization": f"Bearer {_admin_token(tid)}"},
        )
        assert resp.status_code == 200
        by_id = {w["id"]: w for w in resp.json()}
        assert by_id[ws_id]["agent_count"] == 0
