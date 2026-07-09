"""Route tests for the Skills layers API.

Covers:
- GET list includes ``layer`` / ``editable`` / ``workspace_id`` fields.
- POST creates a workspace skill (201, layer=workspace, editable=true).
- POST duplicate name → 409; invalid name → 400.
- Unauthorized (member) POST/PUT/DELETE → 403 (skills:write).
- PUT/DELETE on a non-workspace (directory-layer / missing) skill → 403.
- PUT updates a workspace skill; DELETE removes it.
- reload on a workspace skill → 400.
"""
import pytest
from httpx import ASGITransport, AsyncClient

from src.gateway.auth.jwt import create_jwt
from src.infra.db.engine import async_session
from src.infra.db.models import Tenant, Workspace, WorkspaceMember


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
    async def test_member_cannot_write(self, app):
        ws, tenant, user = "ws-sk4", "t-sk4", "u-sk4"
        tok = await _seed(ws, tenant, user, role="member")
        h = {"Authorization": f"Bearer {tok}"}
        async with _client(app) as ac:
            resp = await ac.post(
                f"/api/v1/workspaces/{ws}/skills",
                headers=h,
                json={"name": "nope", "instructions": "x"},
            )
            assert resp.status_code == 403, resp.text

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
