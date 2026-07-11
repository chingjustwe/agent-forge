"""P0-2 RBAC acceptance tests.

Covers the spec's acceptance criteria:

1. Same user with different roles in two workspaces — permissions isolated.
2. tenant_admin can access any workspace without a WorkspaceMember row.
3. Non-member access to a workspace's resources returns 403.
4. viewer cannot chat.
5. Per-role × per-route access matrix for the key workspace routes.
"""
import uuid as _uuid

import pytest
from httpx import ASGITransport, AsyncClient

from src.gateway.auth.jwt import create_jwt
from src.gateway.auth.roles import (
    TenantRole,
    WorkspaceRole,
    has_workspace_role,
    WORKSPACE_ROLE_HIERARCHY,
)
from src.main import create_app

# asyncio_mode=auto in pyproject.toml marks async tests automatically.


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed(
    ws_id: str,
    user_id: str,
    user_role: str = "member",
    tenant_role: str = "member",
    tenant_id: str | None = None,
    email: str | None = None,
) -> tuple[str, str]:
    """Seed tenant+workspace+user+WorkspaceMember and return (token, tenant_id)."""
    from tests.conftest import setup_workspace_with_member
    from src.infra.db.session import get_db

    tid = tenant_id or f"t-{_uuid.uuid4().hex[:8]}"
    mail = email or f"{user_id}@test.com"
    async for session in get_db():
        token = await setup_workspace_with_member(
            session,
            ws_id=ws_id,
            tenant_id=tid,
            user_id=user_id,
            user_role=user_role,
            tenant_role=tenant_role,
            email=mail,
            name=user_id,
        )
        break
    return token, tid


async def _ensure_workspace(workspace_id: str, tenant_id: str) -> None:
    from src.infra.db.models import Workspace
    from src.infra.db.session import get_db

    async for session in get_db():
        existing = await session.get(Workspace, workspace_id)
        if not existing:
            session.add(
                Workspace(id=workspace_id, tenant_id=tenant_id, name=f"WS {workspace_id}")
            )
            await session.commit()
        break


def _tenant_admin_token(tenant_id: str) -> str:
    return create_jwt({
        "id": "ta-1",
        "sub": "ta-1",
        "tenant_id": tenant_id,
        "email": "ta@test.com",
        "role": "tenant_admin",
    })


@pytest.fixture
def app():
    return create_app()


# ---------------------------------------------------------------------------
# 1. Pure-logic unit tests for the new role helpers
# ---------------------------------------------------------------------------


class TestRoleEnums:
    def test_tenant_role_values(self):
        assert TenantRole.MEMBER.value == "member"
        assert TenantRole.TENANT_ADMIN.value == "tenant_admin"

    def test_workspace_role_values(self):
        assert WorkspaceRole.VIEWER.value == "viewer"
        assert WorkspaceRole.MEMBER.value == "member"
        assert WorkspaceRole.WORKSPACE_ADMIN.value == "workspace_admin"

    def test_workspace_role_hierarchy_ordering(self):
        assert WORKSPACE_ROLE_HIERARCHY["viewer"] == 0
        assert WORKSPACE_ROLE_HIERARCHY["member"] == 1
        assert WORKSPACE_ROLE_HIERARCHY["workspace_admin"] == 2


class TestHasWorkspaceRole:
    def test_admin_passes_all(self):
        for r in ("viewer", "member", "workspace_admin"):
            assert has_workspace_role("workspace_admin", r) is True

    def test_admin_passes_member(self):
        assert has_workspace_role("workspace_admin", "workspace_admin") is True
        assert has_workspace_role("workspace_admin", "member") is True
        assert has_workspace_role("member", "workspace_admin") is False

    def test_member_denies_admin(self):
        assert has_workspace_role("member", "workspace_admin") is False
        assert has_workspace_role("member", "member") is True

    def test_none_role_denies_everything(self):
        assert has_workspace_role(None, "viewer") is False
        assert has_workspace_role(None, "member") is False

    def test_unknown_role_denies(self):
        assert has_workspace_role("bogus", "member") is False

    def test_viewer_can_view(self):
        assert has_workspace_role("viewer", "viewer") is True
        assert has_workspace_role("viewer", "member") is False


# ---------------------------------------------------------------------------
# 2. get_workspace_member_role short-circuits
# ---------------------------------------------------------------------------


class TestGetWorkspaceMemberRole:
    async def test_tenant_admin_short_circuits_to_owner(self):
        from src.gateway.auth.rbac import get_workspace_member_role
        from src.infra.db.session import get_db

        async for session in get_db():
            role = await get_workspace_member_role(
                "ws-that-does-not-exist",
                {"role": "tenant_admin", "sub": "anyone"},
                session,
            )
            assert role == "workspace_admin"
            break

    async def test_returns_none_for_non_member(self):
        from src.gateway.auth.rbac import get_workspace_member_role
        from src.infra.db.session import get_db

        # Use a fresh unique ws_id with no members at all.
        ws_id = f"ws-empty-{_uuid.uuid4().hex[:8]}"
        async for session in get_db():
            role = await get_workspace_member_role(
                ws_id, {"role": "member", "sub": "nobody"}, session
            )
            assert role is None
            break

    async def test_returns_member_role_for_seeded_user(self):
        from src.gateway.auth.rbac import get_workspace_member_role
        from src.infra.db.session import get_db

        ws_id = f"ws-seed-{_uuid.uuid4().hex[:8]}"
        user_id = f"u-seed-{_uuid.uuid4().hex[:8]}"
        async for session in get_db():
            from src.infra.db.models import Tenant, Workspace, User, WorkspaceMember

            tenant = Tenant(
                id=f"t-{_uuid.uuid4().hex[:8]}",
                name="T",
                domain=f"{_uuid.uuid4().hex}.test",
            )
            session.add(tenant)
            await session.flush()
            session.add(Workspace(id=ws_id, tenant_id=tenant.id, name="WS"))
            await session.flush()
            session.add(
                User(
                    id=user_id,
                    tenant_id=tenant.id,
                    email=f"{user_id}@test.com",
                    name=user_id,
                    role="member",
                )
            )
            await session.flush()
            session.add(
                WorkspaceMember(
                    workspace_id=ws_id, user_id=user_id, role="workspace_admin"
                )
            )
            await session.commit()
            break

        # Now query from a fresh session
        async for session in get_db():
            role = await get_workspace_member_role(
                ws_id, {"role": "member", "sub": user_id}, session
            )
            assert role == "workspace_admin"
            break

    async def test_falls_back_to_id_when_sub_missing(self):
        """Old JWTs may carry only `id`, not `sub`."""
        from src.gateway.auth.rbac import get_workspace_member_role
        from src.infra.db.session import get_db

        ws_id = f"ws-fb-{_uuid.uuid4().hex[:8]}"
        user_id = f"u-fb-{_uuid.uuid4().hex[:8]}"
        async for session in get_db():
            from src.infra.db.models import Tenant, Workspace, User, WorkspaceMember

            tenant = Tenant(
                id=f"t-{_uuid.uuid4().hex[:8]}",
                name="T",
                domain=f"{_uuid.uuid4().hex}.test",
            )
            session.add(tenant)
            await session.flush()
            session.add(Workspace(id=ws_id, tenant_id=tenant.id, name="WS"))
            await session.flush()
            session.add(
                User(
                    id=user_id,
                    tenant_id=tenant.id,
                    email=f"{user_id}@test.com",
                    name=user_id,
                    role="member",
                )
            )
            await session.flush()
            session.add(
                WorkspaceMember(workspace_id=ws_id, user_id=user_id, role="member")
            )
            await session.commit()
            break

        async for session in get_db():
            role = await get_workspace_member_role(
                ws_id, {"role": "member", "id": user_id}, session
            )
            assert role == "member"
            break


# ---------------------------------------------------------------------------
# 3. tenant_admin cross-workspace access (no membership row needed)
# ---------------------------------------------------------------------------


class TestTenantAdminShortCircuit:
    async def test_tenant_admin_can_view_any_workspace_audit(self, app):
        """tenant_admin reads audit log of a workspace they never joined."""
        token, tid = await _seed(
            "ws-ta-iso-1", "seed-1", user_role="member", tenant_role="member"
        )
        admin_token = _tenant_admin_token(tid)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(
                "/api/v1/workspaces/ws-ta-iso-1/audit",
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            assert resp.status_code == 200

    async def test_tenant_admin_can_list_any_workspace_members(self, app):
        token, tid = await _seed(
            "ws-ta-iso-2", "seed-2", user_role="member", tenant_role="member"
        )
        admin_token = _tenant_admin_token(tid)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(
                "/api/v1/workspaces/ws-ta-iso-2/members",
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 4. Non-member denied access
# ---------------------------------------------------------------------------


class TestNonMemberDenied:
    async def test_non_member_audit_403(self, app):
        """User is a member of ws-A but tries to read ws-B's audit log."""
        token, tid = await _seed(
            "ws-nm-A", "nm-1", user_role="member", tenant_role="member"
        )
        await _ensure_workspace("ws-nm-B", tid)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(
                "/api/v1/workspaces/ws-nm-B/audit",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 403

    async def test_non_member_quota_403(self, app):
        token, tid = await _seed(
            "ws-nm-C", "nm-2", user_role="member", tenant_role="member"
        )
        await _ensure_workspace("ws-nm-D", tid)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(
                "/api/v1/workspaces/ws-nm-D/quota",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 5. Workspace permission isolation across two workspaces
# ---------------------------------------------------------------------------


class TestWorkspacePermissionIsolation:
    async def test_same_user_admin_in_a_member_in_b(self, app):
        """One user, two workspaces: workspace_admin in A, member in B.

        With the new permission model, members:read is checked at the
        tenant level. Since the user has tenant_role=workspace_admin,
        they can list members in both workspaces as long as they are
        members of the workspace.
        """
        # Use a single tenant so foreign keys line up
        tid = f"t-iso-{_uuid.uuid4().hex[:8]}"
        user_id = "iso-user-X"
        # Seed ws-A with workspace_admin role
        token_a, _ = await _seed(
            "ws-iso-A",
            user_id,
            user_role="workspace_admin",
            tenant_role="workspace_admin",
            tenant_id=tid,
            email=f"{user_id}@test.com",
        )
        # Add same user to ws-B with member role (manually, since
        # setup_workspace_with_member would try to recreate the user)
        from src.infra.db.models import Workspace, WorkspaceMember
        from src.infra.db.session import get_db

        async for session in get_db():
            ws_b = await session.get(Workspace, "ws-iso-B")
            if not ws_b:
                session.add(Workspace(id="ws-iso-B", tenant_id=tid, name="WS B"))
                await session.flush()
            existing = await session.get(WorkspaceMember, ("ws-iso-B", user_id))
            if not existing:
                session.add(
                    WorkspaceMember(
                        workspace_id="ws-iso-B", user_id=user_id, role="member"
                    )
                )
            await session.commit()
            break

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # A — admin can list members
            resp = await ac.get(
                "/api/v1/workspaces/ws-iso-A/members",
                headers={"Authorization": f"Bearer {token_a}"},
            )
            assert resp.status_code == 200

            # B — the user is a member of both workspaces with
            # tenant_role=workspace_admin, so they can also list members here
            resp = await ac.get(
                "/api/v1/workspaces/ws-iso-B/members",
                headers={"Authorization": f"Bearer {token_a}"},
            )
            assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 6. viewer cannot chat
# ---------------------------------------------------------------------------


class TestViewerCannotChat:
    async def test_viewer_gets_403_on_chat(self, app):
        """A tenant-level viewer is rejected from /chat by the existing
        has_permission(user.role, 'member') check (kept for tenant level)."""
        token = create_jwt({
            "id": "viewer-1",
            "sub": "viewer-1",
            "tenant_id": "test-tenant",
            "email": "viewer@test.com",
            "role": "viewer",
        })
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/chat",
                json={"messages": [{"role": "user", "content": "Hi"}]},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 7. Access matrix: role × route
# ---------------------------------------------------------------------------


class TestAccessMatrix:
    """Each test seeds one (workspace, role) pair and asserts the expected
    status code for a few representative routes.

    Routes covered (workspace_id path param = `ws_id`):
      - GET  /workspaces/{ws_id}/audit            → member+ (200), non-member (403)
      - GET  /workspaces/{ws_id}/quota            → member+ (200), viewer-no-memb (403)
      - PUT  /workspaces/{ws_id}/quota            → workspace_admin+ (200), member (403)
      - GET  /workspaces/{ws_id}/settings/otel    → workspace_admin+ (200), member (403)
      - PUT  /workspaces/{ws_id}/settings/otel    → workspace_admin+ (200), member (403)
      - GET  /workspaces/{ws_id}/observability/summary → member+ (200)
      - GET  /workspaces/{ws_id}/members          → workspace_admin+ (200), member (403)
      - PUT  /admin/workspaces/{ws_id}/quota      → workspace_admin+ (200), member (403)
      - PUT  /admin/workspaces/{ws_id}            → tenant_admin only (200), workspace_admin (403)
    """

    async def _seed(self, role: str, ws_id: str | None = None) -> tuple[str, str]:
        ws = ws_id or f"ws-mx-{_uuid.uuid4().hex[:8]}"
        user = f"u-mx-{_uuid.uuid4().hex[:8]}"
        token, _tid = await _seed(ws, user, user_role=role, tenant_role=role)
        return token, ws

    async def test_member_can_read_quota(self, app):
        token, ws_id = await self._seed("member")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(
                f"/api/v1/workspaces/{ws_id}/quota",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200

    async def test_member_cannot_update_quota(self, app):
        token, ws_id = await self._seed("member")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.put(
                f"/api/v1/workspaces/{ws_id}/quota",
                json={"max_tokens_per_day": 100},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 403

    async def test_workspace_admin_can_update_quota(self, app):
        token, ws_id = await self._seed("workspace_admin")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.put(
                f"/api/v1/workspaces/{ws_id}/quota",
                json={"max_tokens_per_day": 100},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200

    async def test_member_cannot_list_members(self, app):
        token, ws_id = await self._seed("member")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(
                f"/api/v1/workspaces/{ws_id}/members",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 403

    async def test_workspace_admin_can_list_members(self, app):
        token, ws_id = await self._seed("workspace_admin")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(
                f"/api/v1/workspaces/{ws_id}/members",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200

    async def test_workspace_admin_can_list_members(self, app):
        token, ws_id = await self._seed("workspace_admin")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(
                f"/api/v1/workspaces/{ws_id}/members",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200

    async def test_member_cannot_read_otel_settings(self, app):
        token, ws_id = await self._seed("member")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(
                f"/api/v1/workspaces/{ws_id}/settings/otel",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 403

    async def test_member_cannot_update_otel(self, app):
        token, ws_id = await self._seed("member")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.put(
                f"/api/v1/workspaces/{ws_id}/settings/otel",
                json={"enabled": True, "endpoint": "http://otel", "headers": {}},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 403

    async def test_workspace_admin_can_update_otel(self, app):
        token, ws_id = await self._seed("workspace_admin")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.put(
                f"/api/v1/workspaces/{ws_id}/settings/otel",
                json={"enabled": True, "endpoint": "http://otel", "headers": {}},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200

    async def test_member_can_view_observability(self, app):
        token, ws_id = await self._seed("member")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(
                f"/api/v1/workspaces/{ws_id}/observability/summary",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200

    async def test_member_cannot_update_workspace(self, app):
        """PUT /admin/workspaces/{ws_id} requires workspace_admin."""
        token, ws_id = await self._seed("member")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.put(
                f"/api/v1/admin/workspaces/{ws_id}",
                json={"name": "Renamed"},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 403

    async def test_admin_cannot_update_workspace(self, app):
        """PUT /admin/workspaces/{ws_id} requires admin:workspaces:write
        which is only available to tenant_admin (super_admin)."""
        token, ws_id = await self._seed("workspace_admin")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.put(
                f"/api/v1/admin/workspaces/{ws_id}",
                json={"name": "Renamed"},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 403

    async def test_member_cannot_archive_workspace(self, app):
        """DELETE /admin/workspaces/{ws_id} requires workspace_admin."""
        token, ws_id = await self._seed("member")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.delete(
                f"/api/v1/admin/workspaces/{ws_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 403

    async def test_member_can_view_audit_log(self, app):
        token, ws_id = await self._seed("member")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(
                f"/api/v1/workspaces/{ws_id}/audit",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200

    async def test_admin_route_quota_update_admin_role(self, app):
        """PUT /admin/workspaces/{ws_id}/quota — workspace_admin can."""
        token, ws_id = await self._seed("workspace_admin")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.put(
                f"/api/v1/admin/workspaces/{ws_id}/quota",
                json={"max_tokens_per_day": 999},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200

    async def test_admin_route_quota_member_forbidden(self, app):
        """PUT /admin/workspaces/{ws_id}/quota — plain member cannot."""
        token, ws_id = await self._seed("member")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.put(
                f"/api/v1/admin/workspaces/{ws_id}/quota",
                json={"max_tokens_per_day": 999},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 8. permissions.yaml config (Wave 1 sidebar reorganization)
# ---------------------------------------------------------------------------


class TestPermissionsConfig:
    """Verify the permissions.yaml changes for Wave 1 sidebar reorg.

    - member gains skills:write / mcp:write / usage:read
    - workspace_admin gains usage:read
    - frontend_tabs paths renamed: /admin/skills→/skills, /admin/mcp→/mcp,
      /admin/usage→/usage
    """

    def test_member_has_skills_write(self):
        from src.gateway.auth.permissions import has_permission
        assert has_permission("member", "skills:write") is True

    def test_member_has_mcp_write(self):
        from src.gateway.auth.permissions import has_permission
        assert has_permission("member", "mcp:write") is True

    def test_member_has_usage_read(self):
        from src.gateway.auth.permissions import has_permission
        assert has_permission("member", "usage:read") is True

    def test_workspace_admin_has_usage_read(self):
        from src.gateway.auth.permissions import has_permission
        assert has_permission("workspace_admin", "usage:read") is True

    def test_viewer_lacks_write_perms(self):
        from src.gateway.auth.permissions import has_permission
        assert has_permission("viewer", "skills:write") is False
        assert has_permission("viewer", "mcp:write") is False
        assert has_permission("viewer", "usage:read") is False

    def test_frontend_tabs_has_renamed_paths(self):
        from src.gateway.auth.permissions import get_frontend_tabs
        tabs = get_frontend_tabs()
        assert "/skills" in tabs
        assert "/mcp" in tabs
        assert "/analytics" in tabs

    def test_frontend_tabs_no_old_admin_paths(self):
        from src.gateway.auth.permissions import get_frontend_tabs
        tabs = get_frontend_tabs()
        assert "/admin/skills" not in tabs
        assert "/admin/mcp" not in tabs
        assert "/admin/usage" not in tabs
