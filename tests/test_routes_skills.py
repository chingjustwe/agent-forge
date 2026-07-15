"""Route tests for the Skills layers API.

Covers:
- GET list includes ``layer`` / ``editable`` / ``workspace_id`` fields.
- POST creates a workspace skill (201, layer=workspace, editable=true).
- POST duplicate name → 409; invalid name → 400.
- member can write (skills:write granted via permissions.yaml).
- PUT/DELETE on a non-workspace (directory-layer / missing) skill → 403.
- PUT updates a workspace skill; DELETE removes it.
- reload on a workspace skill → 400.
- create / update / delete write AuditLog entries.
"""
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from src.gateway.auth.jwt import create_jwt
from src.infra.db.engine import async_session
from src.infra.db.models import AuditLog, Tenant, Workspace, WorkspaceMember


def _token(user_id: str, tenant_id: str, role: str = "workspace_admin") -> str:
    return create_jwt({
        "id": user_id,
        "sub": user_id,
        "tenant_id": tenant_id,
        "email": f"{user_id}@test.com",
        "role": role,
    })


async def _seed(ws: str, tenant: str, user: str, role: str = "workspace_admin") -> str:
    async with async_session() as db:
        if not await db.get(Tenant, tenant):
            db.add(Tenant(id=tenant, name="T", domain=f"{tenant}.test"))
        if not await db.get(Workspace, ws):
            db.add(Workspace(id=ws, tenant_id=tenant, name="WS"))
        if not await db.get(WorkspaceMember, (ws, user)):
            db.add(WorkspaceMember(workspace_id=ws, user_id=user, role=role))
        await db.commit()
    return _token(user, tenant, role)


@pytest.fixture
def app():
    from src.main import create_app

    return create_app()


def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


class TestSkillRoutes:
    @pytest.mark.asyncio
    async def test_create_and_list(self, app):
        ws, tenant, user = "ws-sk1", "t-sk1", "u-sk1"
        tok = await _seed(ws, tenant, user)
        h = {"Authorization": f"Bearer {tok}"}
        async with _client(app) as ac:
            resp = await ac.post(
                f"/api/v1/workspaces/{ws}/skills",
                headers=h,
                json={
                    "name": "my-ws-skill",
                    "description": "desc",
                    "instructions": "do it",
                    "tools": ["t1"],
                    "version": "1.0",
                },
            )
            assert resp.status_code == 201, resp.text
            body = resp.json()
            assert body["layer"] == "workspace"
            assert body["editable"] is True
            assert body["workspace_id"] == ws
            assert body["instructions"] == "do it"

            resp = await ac.get(f"/api/v1/workspaces/{ws}/skills", headers=h)
            assert resp.status_code == 200
            items = resp.json()
            found = [s for s in items if s["name"] == "my-ws-skill"]
            assert found and found[0]["layer"] == "workspace"
            assert found[0]["editable"] is True
            # every entry carries layer/editable/workspace_id keys
            for s in items:
                assert "layer" in s and "editable" in s and "workspace_id" in s

    @pytest.mark.asyncio
    async def test_create_duplicate_conflict(self, app):
        ws, tenant, user = "ws-sk2", "t-sk2", "u-sk2"
        tok = await _seed(ws, tenant, user)
        h = {"Authorization": f"Bearer {tok}"}
        async with _client(app) as ac:
            r1 = await ac.post(
                f"/api/v1/workspaces/{ws}/skills",
                headers=h,
                json={"name": "dup", "instructions": "a"},
            )
            assert r1.status_code == 201
            r2 = await ac.post(
                f"/api/v1/workspaces/{ws}/skills",
                headers=h,
                json={"name": "dup", "instructions": "b"},
            )
            assert r2.status_code == 409, r2.text

    @pytest.mark.asyncio
    async def test_create_invalid_name(self, app):
        ws, tenant, user = "ws-sk3", "t-sk3", "u-sk3"
        tok = await _seed(ws, tenant, user)
        h = {"Authorization": f"Bearer {tok}"}
        async with _client(app) as ac:
            resp = await ac.post(
                f"/api/v1/workspaces/{ws}/skills",
                headers=h,
                json={"name": "Bad Name!", "instructions": "x"},
            )
            assert resp.status_code == 400, resp.text

    @pytest.mark.asyncio
    async def test_member_can_write(self, app):
        """member has skills:write (Wave 1 sidebar reorganization)."""
        ws, tenant, user = "ws-sk4", "t-sk4", "u-sk4"
        tok = await _seed(ws, tenant, user, role="member")
        h = {"Authorization": f"Bearer {tok}"}
        async with _client(app) as ac:
            resp = await ac.post(
                f"/api/v1/workspaces/{ws}/skills",
                headers=h,
                json={"name": "member-skill", "instructions": "x"},
            )
            assert resp.status_code == 201, resp.text

    @pytest.mark.asyncio
    async def test_update_non_workspace_forbidden(self, app):
        ws, tenant, user = "ws-sk5", "t-sk5", "u-sk5"
        tok = await _seed(ws, tenant, user)
        h = {"Authorization": f"Bearer {tok}"}
        async with _client(app) as ac:
            resp = await ac.put(
                f"/api/v1/workspaces/{ws}/skills/ghost-skill",
                headers=h,
                json={"description": "x"},
            )
            assert resp.status_code == 403, resp.text

    @pytest.mark.asyncio
    async def test_update_and_delete_workspace_skill(self, app):
        ws, tenant, user = "ws-sk6", "t-sk6", "u-sk6"
        tok = await _seed(ws, tenant, user)
        h = {"Authorization": f"Bearer {tok}"}
        async with _client(app) as ac:
            await ac.post(
                f"/api/v1/workspaces/{ws}/skills",
                headers=h,
                json={"name": "editme", "instructions": "v1"},
            )
            resp = await ac.put(
                f"/api/v1/workspaces/{ws}/skills/editme",
                headers=h,
                json={"instructions": "v2", "version": "2.0"},
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["instructions"] == "v2"
            assert resp.json()["version"] == "2.0"

            resp = await ac.delete(
                f"/api/v1/workspaces/{ws}/skills/editme", headers=h
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["ok"] is True

            # Now missing → GET 404.
            resp = await ac.get(
                f"/api/v1/workspaces/{ws}/skills/editme", headers=h
            )
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_non_workspace_forbidden(self, app):
        ws, tenant, user = "ws-sk7", "t-sk7", "u-sk7"
        tok = await _seed(ws, tenant, user)
        h = {"Authorization": f"Bearer {tok}"}
        async with _client(app) as ac:
            resp = await ac.delete(
                f"/api/v1/workspaces/{ws}/skills/ghost", headers=h
            )
            assert resp.status_code == 403, resp.text

    @pytest.mark.asyncio
    async def test_reload_workspace_skill_rejected(self, app):
        ws, tenant, user = "ws-sk8", "t-sk8", "u-sk8"
        tok = await _seed(ws, tenant, user)
        h = {"Authorization": f"Bearer {tok}"}
        async with _client(app) as ac:
            await ac.post(
                f"/api/v1/workspaces/{ws}/skills",
                headers=h,
                json={"name": "wsreload", "instructions": "x"},
            )
            resp = await ac.post(
                f"/api/v1/workspaces/{ws}/skills/wsreload/reload", headers=h
            )
            assert resp.status_code == 400, resp.text


# ── Audit log tests ─────────────────────────────────────────────────────


class TestSkillAudit:
    @pytest.mark.asyncio
    async def test_create_writes_audit_log(self, app):
        ws, tenant, user = "ws-ska1", "t-ska1", "u-ska1"
        tok = await _seed(ws, tenant, user)
        h = {"Authorization": f"Bearer {tok}"}
        async with _client(app) as ac:
            resp = await ac.post(
                f"/api/v1/workspaces/{ws}/skills",
                headers=h,
                json={"name": "audited-skill", "instructions": "x"},
            )
            assert resp.status_code == 201
        async with async_session() as db:
            rows = (
                await db.execute(
                    select(AuditLog).where(AuditLog.action == "skill.create")
                )
            ).scalars().all()
            assert any(r.target_id == "audited-skill" for r in rows)

    @pytest.mark.asyncio
    async def test_update_writes_audit_log(self, app):
        ws, tenant, user = "ws-ska2", "t-ska2", "u-ska2"
        tok = await _seed(ws, tenant, user)
        h = {"Authorization": f"Bearer {tok}"}
        async with _client(app) as ac:
            await ac.post(
                f"/api/v1/workspaces/{ws}/skills",
                headers=h,
                json={"name": "upd-audit", "instructions": "v1"},
            )
            resp = await ac.put(
                f"/api/v1/workspaces/{ws}/skills/upd-audit",
                headers=h,
                json={"instructions": "v2"},
            )
            assert resp.status_code == 200
        async with async_session() as db:
            rows = (
                await db.execute(
                    select(AuditLog).where(AuditLog.action == "skill.update")
                )
            ).scalars().all()
            assert any(r.target_id == "upd-audit" for r in rows)

    @pytest.mark.asyncio
    async def test_delete_writes_audit_log(self, app):
        ws, tenant, user = "ws-ska3", "t-ska3", "u-ska3"
        tok = await _seed(ws, tenant, user)
        h = {"Authorization": f"Bearer {tok}"}
        async with _client(app) as ac:
            await ac.post(
                f"/api/v1/workspaces/{ws}/skills",
                headers=h,
                json={"name": "del-audit", "instructions": "x"},
            )
            resp = await ac.delete(
                f"/api/v1/workspaces/{ws}/skills/del-audit", headers=h
            )
            assert resp.status_code == 200
        async with async_session() as db:
            rows = (
                await db.execute(
                    select(AuditLog).where(AuditLog.action == "skill.delete")
                )
            ).scalars().all()
            assert any(r.target_id == "del-audit" for r in rows)


# ── Ownership tests ────────────────────────────────────────────────────


class TestSkillOwnership:
    """User-level ownership: all members can create, only owner/admin can
    edit or delete."""

    @pytest.mark.asyncio
    async def test_owner_can_update_own_skill(self, app):
        ws, tenant = "ws-sk-own1", "t-sk-own1"
        mem_a = "u-mem-a1"
        tok_a = await _seed(ws, tenant, mem_a, role="member")
        h = {"Authorization": f"Bearer {tok_a}"}
        async with _client(app) as ac:
            await ac.post(
                f"/api/v1/workspaces/{ws}/skills",
                headers=h,
                json={"name": "own-skill", "instructions": "v1"},
            )
            resp = await ac.put(
                f"/api/v1/workspaces/{ws}/skills/own-skill",
                headers=h,
                json={"instructions": "v2"},
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["instructions"] == "v2"

    @pytest.mark.asyncio
    async def test_owner_can_delete_own_skill(self, app):
        ws, tenant = "ws-sk-own2", "t-sk-own2"
        mem_a = "u-mem-a2"
        tok_a = await _seed(ws, tenant, mem_a, role="member")
        h = {"Authorization": f"Bearer {tok_a}"}
        async with _client(app) as ac:
            await ac.post(
                f"/api/v1/workspaces/{ws}/skills",
                headers=h,
                json={"name": "del-own", "instructions": "x"},
            )
            resp = await ac.delete(
                f"/api/v1/workspaces/{ws}/skills/del-own", headers=h
            )
        assert resp.status_code == 200, resp.text

    @pytest.mark.asyncio
    async def test_non_owner_cannot_update_skill(self, app):
        ws, tenant = "ws-sk-own3", "t-sk-own3"
        mem_a = "u-mem-a3"
        mem_b = "u-mem-b3"
        tok_a = await _seed(ws, tenant, mem_a, role="member")
        tok_b = await _seed(ws, tenant, mem_b, role="member")
        h_a = {"Authorization": f"Bearer {tok_a}"}
        h_b = {"Authorization": f"Bearer {tok_b}"}
        async with _client(app) as ac:
            await ac.post(
                f"/api/v1/workspaces/{ws}/skills",
                headers=h_a,
                json={"name": "a-skill", "instructions": "v1"},
            )
            resp = await ac.put(
                f"/api/v1/workspaces/{ws}/skills/a-skill",
                headers=h_b,
                json={"instructions": "hacked"},
            )
        assert resp.status_code == 403, resp.text

    @pytest.mark.asyncio
    async def test_non_owner_cannot_delete_skill(self, app):
        ws, tenant = "ws-sk-own4", "t-sk-own4"
        mem_a = "u-mem-a4"
        mem_b = "u-mem-b4"
        tok_a = await _seed(ws, tenant, mem_a, role="member")
        tok_b = await _seed(ws, tenant, mem_b, role="member")
        h_a = {"Authorization": f"Bearer {tok_a}"}
        h_b = {"Authorization": f"Bearer {tok_b}"}
        async with _client(app) as ac:
            await ac.post(
                f"/api/v1/workspaces/{ws}/skills",
                headers=h_a,
                json={"name": "no-delete", "instructions": "x"},
            )
            resp = await ac.delete(
                f"/api/v1/workspaces/{ws}/skills/no-delete", headers=h_b
            )
        assert resp.status_code == 403, resp.text

    @pytest.mark.asyncio
    async def test_admin_can_update_others_skill(self, app):
        ws, tenant = "ws-sk-own5", "t-sk-own5"
        mem_a = "u-mem-a5"
        admin = "u-admin-a5"
        tok_a = await _seed(ws, tenant, mem_a, role="member")
        tok_admin = await _seed(ws, tenant, admin, role="workspace_admin")
        h_a = {"Authorization": f"Bearer {tok_a}"}
        h_admin = {"Authorization": f"Bearer {tok_admin}"}
        async with _client(app) as ac:
            await ac.post(
                f"/api/v1/workspaces/{ws}/skills",
                headers=h_a,
                json={"name": "admin-edit", "instructions": "v1"},
            )
            resp = await ac.put(
                f"/api/v1/workspaces/{ws}/skills/admin-edit",
                headers=h_admin,
                json={"instructions": "admin-overwrite"},
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["instructions"] == "admin-overwrite"
