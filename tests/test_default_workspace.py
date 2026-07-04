"""P3-4: 默认 workspace 转移。

tenant_admin 可把 is_default 转到另一个 workspace，原子操作。新注册用户
自动加入默认 workspace 的逻辑在 auth.register 中已存在（P0-1 引入）。
"""
import uuid as _uuid

import pytest
from httpx import ASGITransport, AsyncClient

from src.gateway.auth.jwt import create_jwt
from src.infra.db.engine import async_session
from src.infra.db.models import (
    Tenant,
    User,
    Workspace,
    WorkspaceMember,
)


@pytest.fixture
def app():
    from src.main import create_app
    return create_app()


def _token(user_id: str, tenant_id: str, role: str = "tenant_admin") -> str:
    return create_jwt({
        "id": user_id,
        "sub": user_id,
        "tenant_id": tenant_id,
        "email": f"{user_id}@test.com",
        "role": role,
    })


async def _seed_two_workspaces(
    tenant_id: str,
    ws_old_default: str,
    ws_new_default: str,
    user_id: str,
    tenant_role: str = "tenant_admin",
) -> str:
    """Seed tenant + two workspaces (old marked is_default=1) + user. Returns JWT."""
    async with async_session() as session:
        if not await session.get(Tenant, tenant_id):
            session.add(Tenant(id=tenant_id, name="T", domain=f"{tenant_id}.test"))
            await session.flush()
        if not await session.get(Workspace, ws_old_default):
            session.add(
                Workspace(
                    id=ws_old_default,
                    tenant_id=tenant_id,
                    name="Old Default",
                    is_default=1,
                )
            )
            await session.flush()
        if not await session.get(Workspace, ws_new_default):
            session.add(
                Workspace(
                    id=ws_new_default,
                    tenant_id=tenant_id,
                    name="New Default",
                    is_default=0,
                )
            )
            await session.flush()
        if not await session.get(User, user_id):
            session.add(
                User(
                    id=user_id,
                    tenant_id=tenant_id,
                    email=f"{user_id}@test.com",
                    name=user_id,
                    role=tenant_role,
                )
            )
            await session.flush()
        if not await session.get(WorkspaceMember, (ws_new_default, user_id)):
            session.add(
                WorkspaceMember(workspace_id=ws_new_default, user_id=user_id, role="member")
            )
        await session.commit()
    return _token(user_id, tenant_id, role=tenant_role)


class TestSetDefaultWorkspace:
    @pytest.mark.asyncio
    async def test_set_default_transfers_flag(self, app):
        """set-default 后新 ws is_default=1，旧默认 is_default=0。"""
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-sd-{suffix}"
        ws_old = f"ws-old-{suffix}"
        ws_new = f"ws-new-{suffix}"
        tok = await _seed_two_workspaces(tid, ws_old, ws_new, f"admin-{suffix}")

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/admin/workspaces/{ws_new}/set-default",
                headers={"Authorization": f"Bearer {tok}"},
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["id"] == ws_new
        assert body["is_default"] is True

        async with async_session() as session:
            old = await session.get(Workspace, ws_old)
            new = await session.get(Workspace, ws_new)
        assert old.is_default == 0
        assert new.is_default == 1

    @pytest.mark.asyncio
    async def test_non_tenant_admin_forbidden(self, app):
        """非 tenant_admin 403。"""
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-sd-wa-{suffix}"
        ws_old = f"ws-old-{suffix}"
        ws_new = f"ws-new-{suffix}"
        # Seed with tenant_admin, then create a member-role user
        await _seed_two_workspaces(tid, ws_old, ws_new, f"admin-{suffix}")
        async with async_session() as session:
            session.add(
                User(
                    id=f"wa-{suffix}",
                    tenant_id=tid,
                    email=f"wa-{suffix}@test.com",
                    name=f"wa-{suffix}",
                    role="member",
                )
            )
            session.add(
                WorkspaceMember(workspace_id=ws_new, user_id=f"wa-{suffix}", role="member")
            )
            await session.commit()
        tok = _token(f"wa-{suffix}", tid, role="member")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/admin/workspaces/{ws_new}/set-default",
                headers={"Authorization": f"Bearer {tok}"},
            )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_workspace_not_found_404(self, app):
        """workspace 不存在 404。"""
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-sd-nf-{suffix}"
        async with async_session() as session:
            session.add(Tenant(id=tid, name="T", domain=f"{tid}.test"))
            session.add(User(id=f"admin-{suffix}", tenant_id=tid, email=f"a-{suffix}@test.com",
                             name="A", role="tenant_admin"))
            await session.commit()
        tok = _token(f"admin-{suffix}", tid)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/admin/workspaces/ws-nonexistent/set-default",
                headers={"Authorization": f"Bearer {tok}"},
            )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_archived_workspace_cannot_be_default(self, app):
        """archived workspace 不能设为默认 409。"""
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-sd-arc-{suffix}"
        ws_old = f"ws-old-{suffix}"
        ws_archived = f"ws-arc-{suffix}"
        tok = await _seed_two_workspaces(tid, ws_old, ws_archived, f"admin-{suffix}")
        # Mark ws_archived as archived
        async with async_session() as session:
            ws = await session.get(Workspace, ws_archived)
            ws.archived = 1
            await session.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/admin/workspaces/{ws_archived}/set-default",
                headers={"Authorization": f"Bearer {tok}"},
            )
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_register_uses_new_default_workspace(self, app):
        """set-default 后该 tenant 内只有一个 is_default=1 的 workspace，
        且就是 ws_new —— 这是 auth.register 自动加入默认 workspace 所依赖的不变量。

        注：auth.register 用 ``select(Tenant).limit(1)`` 选 tenant，测试间共享
        DB 时无法可靠控制它选到哪个 tenant，所以这里直接验证不变量而非端到端
        调用 register。
        """
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-sd-reg-{suffix}"
        ws_old = f"ws-old-{suffix}"
        ws_new = f"ws-new-{suffix}"
        tok = await _seed_two_workspaces(tid, ws_old, ws_new, f"admin-{suffix}")

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/admin/workspaces/{ws_new}/set-default",
                headers={"Authorization": f"Bearer {tok}"},
            )
            assert resp.status_code == 200

        # Verify the invariant register relies on: exactly one is_default=1
        # workspace in this tenant, and it's ws_new.
        from sqlalchemy import select
        async with async_session() as session:
            defaults = (await session.execute(
                select(Workspace).where(
                    Workspace.tenant_id == tid,
                    Workspace.is_default == 1,
                )
            )).scalars().all()
        assert len(defaults) == 1
        assert defaults[0].id == ws_new
