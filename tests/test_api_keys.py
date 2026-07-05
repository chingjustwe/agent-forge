"""Tests for P2-3: workspace-scoped API keys.

Covers:
- ``ApiKey`` model definition (columns, indexes, defaults).
- Create route: admin/owner allowed, member 403, plaintext returned once,
  default scopes, expiry, audit log.
- List route: prefixes only (no plaintext), cross-workspace isolation.
- Revoke route: soft-delete (revoked=1), 404 cross-workspace, audit log.
- Middleware auth flow: ``X-API-Key`` header accepted on a protected
  endpoint, invalid / revoked / expired / missing-all → 401, Bearer JWT
  still works (no regression), ``last_used_at`` updated after use.
- Chat scope check: API key without ``chat:write`` → 403.
"""
import hashlib
import uuid as _uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from src.gateway.auth.jwt import create_jwt
from src.infra.db.engine import async_session
from src.infra.db.models import (
    ApiKey,
    AuditLog,
    Tenant,
    User,
    Workspace,
    WorkspaceMember,
)


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------
@pytest.fixture
def app():
    from src.main import create_app
    return create_app()


def _token(user_id: str, tenant_id: str, role: str = "member", email: str | None = None):
    return create_jwt({
        "id": user_id,
        "sub": user_id,
        "tenant_id": tenant_id,
        "email": email or f"{user_id}@test.com",
        "role": role,
    })


async def _seed(
    ws_id: str,
    tenant_id: str,
    user_id: str,
    ws_role: str = "workspace_admin",
    tenant_role: str | None = None,
    email: str | None = None,
) -> str:
    """Seed tenant + workspace + user + WorkspaceMember. Returns JWT."""
    if tenant_role is None:
        tenant_role = ws_role
    async with async_session() as session:
        if not await session.get(Tenant, tenant_id):
            session.add(Tenant(id=tenant_id, name="T", domain=f"{tenant_id}.test"))
            await session.flush()
        if not await session.get(Workspace, ws_id):
            session.add(Workspace(id=ws_id, tenant_id=tenant_id, name=f"WS {ws_id}"))
            await session.flush()
        if not await session.get(User, user_id):
            session.add(
                User(
                    id=user_id,
                    tenant_id=tenant_id,
                    email=email or f"{user_id}@test.com",
                    name=user_id,
                    role=tenant_role,
                )
            )
            await session.flush()
        if not await session.get(WorkspaceMember, (ws_id, user_id)):
            session.add(
                WorkspaceMember(workspace_id=ws_id, user_id=user_id, role=ws_role)
            )
        await session.commit()
    return _token(user_id, tenant_id, role=tenant_role, email=email)


async def _create_key_via_api(
    app,
    token: str,
    ws_id: str,
    name: str = "My Key",
    scopes: list[str] | None = None,
    expires_in_days: int | None = None,
):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        body: dict = {"name": name}
        if scopes is not None:
            body["scopes"] = scopes
        if expires_in_days is not None:
            body["expires_in_days"] = expires_in_days
        return await ac.post(
            f"/api/v1/workspaces/{ws_id}/api-keys",
            headers={"Authorization": f"Bearer {token}"},
            json=body,
        )


def _hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


# ---------------------------------------------------------------------------
# 1. Model definition
# ---------------------------------------------------------------------------
class TestApiKeyModel:
    def test_tablename(self):
        assert ApiKey.__tablename__ == "api_keys"

    def test_fields_exist(self):
        cols = {c.name for c in ApiKey.__table__.columns}
        assert {
            "id",
            "workspace_id",
            "name",
            "key_prefix",
            "key_hash",
            "scopes",
            "created_by",
            "expires_at",
            "last_used_at",
            "revoked",
            "created_at",
        } <= cols

    def test_workspace_id_is_indexed(self):
        col = ApiKey.__table__.columns["workspace_id"]
        assert col.index is True

    def test_key_hash_is_unique_and_indexed(self):
        col = ApiKey.__table__.columns["key_hash"]
        assert col.unique is True
        assert col.index is True

    def test_scopes_defaults_to_empty_list(self):
        col_default = ApiKey.__table__.columns["scopes"].default
        assert col_default is not None
        assert getattr(col_default.arg, "__name__", None) == "list"

    def test_revoked_defaults_to_zero(self):
        col_default = ApiKey.__table__.columns["revoked"].default
        assert col_default is not None
        assert col_default.arg == 0


# ---------------------------------------------------------------------------
# 2. Create API key
# ---------------------------------------------------------------------------
class TestCreateApiKey:
    @pytest.mark.asyncio
    async def test_admin_can_create(self, app):
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-ca-{suffix}"
        ws = f"ws-ca-{suffix}"
        uid = f"admin-{suffix}"
        tok = await _seed(ws, tid, uid, ws_role="workspace_admin")

        resp = await _create_key_via_api(app, tok, ws, name="CI Key")
        assert resp.status_code == 201
        body = resp.json()
        assert body["id"]
        assert body["name"] == "CI Key"
        # Plaintext key returned exactly once, with the ap_ prefix.
        assert body["key"].startswith("ap_")
        assert len(body["key"]) == 35  # ap_ + 32 hex chars
        # key_prefix is the first 8 chars of the plaintext key.
        assert body["key_prefix"] == body["key"][:8]
        # Default scopes when omitted.
        assert body["scopes"] == ["chat:write"]
        assert body["expires_at"] is None
        assert body["created_at"]

    @pytest.mark.asyncio
    async def test_owner_can_create(self, app):
        suffix = _uuid.uuid4().hex[:8]
        tok = await _seed(f"ws-{suffix}", f"t-{suffix}", f"owner-{suffix}", ws_role="workspace_admin")
        resp = await _create_key_via_api(app, tok, f"ws-{suffix}")
        assert resp.status_code == 201

    @pytest.mark.asyncio
    async def test_member_forbidden(self, app):
        suffix = _uuid.uuid4().hex[:8]
        tok = await _seed(f"ws-{suffix}", f"t-{suffix}", f"mem-{suffix}", ws_role="member")
        resp = await _create_key_via_api(app, tok, f"ws-{suffix}")
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_custom_scopes_and_expiry(self, app):
        suffix = _uuid.uuid4().hex[:8]
        tok = await _seed(f"ws-{suffix}", f"t-{suffix}", f"admin-{suffix}", ws_role="workspace_admin")
        resp = await _create_key_via_api(
            app, tok, f"ws-{suffix}",
            name="Scoped",
            scopes=["chat:write", "quota:read"],
            expires_in_days=30,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["scopes"] == ["chat:write", "quota:read"]
        assert body["expires_at"] is not None

    @pytest.mark.asyncio
    async def test_invalid_scope_rejected(self, app):
        suffix = _uuid.uuid4().hex[:8]
        tok = await _seed(f"ws-{suffix}", f"t-{suffix}", f"admin-{suffix}", ws_role="workspace_admin")
        resp = await _create_key_via_api(
            app, tok, f"ws-{suffix}",
            scopes=["chat:write", "bogus:scope"],
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_invalid_expiry_rejected(self, app):
        suffix = _uuid.uuid4().hex[:8]
        tok = await _seed(f"ws-{suffix}", f"t-{suffix}", f"admin-{suffix}", ws_role="workspace_admin")
        resp = await _create_key_via_api(
            app, tok, f"ws-{suffix}", expires_in_days=0,
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_create_persists_hash_not_plaintext(self, app):
        suffix = _uuid.uuid4().hex[:8]
        ws = f"ws-{suffix}"
        tok = await _seed(ws, f"t-{suffix}", f"admin-{suffix}", ws_role="workspace_admin")
        body = (await _create_key_via_api(app, tok, ws)).json()
        plaintext = body["key"]

        async with async_session() as session:
            result = await session.execute(
                select(ApiKey).where(ApiKey.id == body["id"])
            )
            stored = result.scalar_one()
        assert stored.key_hash == _hash_key(plaintext)
        # Plaintext must NEVER be stored.
        assert stored.key_hash != plaintext
        assert plaintext not in (stored.key_hash or "")
        assert plaintext not in (stored.key_prefix or "")

    @pytest.mark.asyncio
    async def test_create_writes_audit_log(self, app):
        suffix = _uuid.uuid4().hex[:8]
        ws = f"ws-{suffix}"
        tok = await _seed(ws, f"t-{suffix}", f"admin-{suffix}", ws_role="workspace_admin")
        body = (await _create_key_via_api(app, tok, ws, name="Audited")).json()
        async with async_session() as session:
            rows = (await session.execute(
                select(AuditLog).where(
                    AuditLog.action == "api_key.create",
                    AuditLog.target_id == body["id"],
                )
            )).scalars().all()
        assert len(rows) == 1
        assert rows[0].target_type == "api_key"
        assert rows[0].workspace_id == ws


# ---------------------------------------------------------------------------
# 3. List API keys
# ---------------------------------------------------------------------------
class TestListApiKeys:
    @pytest.mark.asyncio
    async def test_list_returns_prefixes_only(self, app):
        suffix = _uuid.uuid4().hex[:8]
        ws = f"ws-{suffix}"
        tok = await _seed(ws, f"t-{suffix}", f"admin-{suffix}", ws_role="workspace_admin")
        created = (await _create_key_via_api(app, tok, ws, name="K1")).json()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(
                f"/api/v1/workspaces/{ws}/api-keys",
                headers={"Authorization": f"Bearer {tok}"},
            )
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 1
        item = items[0]
        assert item["id"] == created["id"]
        assert item["key_prefix"] == created["key_prefix"]
        # Plaintext key must NOT appear in list responses.
        assert "key" not in item
        assert created["key"] not in str(item)

    @pytest.mark.asyncio
    async def test_list_isolated_per_workspace(self, app):
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-{suffix}"
        ws_a = f"ws-a-{suffix}"
        ws_b = f"ws-b-{suffix}"
        tok_a = await _seed(ws_a, tid, f"admin-a-{suffix}", ws_role="workspace_admin")
        tok_b = await _seed(ws_b, tid, f"admin-b-{suffix}", ws_role="workspace_admin",
                            email=f"admin-b-{suffix}@test.com")
        await _create_key_via_api(app, tok_a, ws_a, name="Key in A")
        await _create_key_via_api(app, tok_b, ws_b, name="Key in B")

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp_a = await ac.get(
                f"/api/v1/workspaces/{ws_a}/api-keys",
                headers={"Authorization": f"Bearer {tok_a}"},
            )
            resp_b = await ac.get(
                f"/api/v1/workspaces/{ws_b}/api-keys",
                headers={"Authorization": f"Bearer {tok_b}"},
            )
        a_names = {k["name"] for k in resp_a.json()}
        b_names = {k["name"] for k in resp_b.json()}
        assert a_names == {"Key in A"}
        assert b_names == {"Key in B"}

    @pytest.mark.asyncio
    async def test_list_empty(self, app):
        suffix = _uuid.uuid4().hex[:8]
        tok = await _seed(f"ws-{suffix}", f"t-{suffix}", f"u-{suffix}", ws_role="workspace_admin")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(
                f"/api/v1/workspaces/ws-{suffix}/api-keys",
                headers={"Authorization": f"Bearer {tok}"},
            )
        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.asyncio
    async def test_member_cannot_list(self, app):
        suffix = _uuid.uuid4().hex[:8]
        ws = f"ws-{suffix}"
        admin_tok = await _seed(ws, f"t-{suffix}", f"admin-{suffix}", ws_role="workspace_admin")
        await _create_key_via_api(app, admin_tok, ws, name="K1")
        member_tok = await _seed(
            ws, f"t-{suffix}", f"mem-{suffix}", ws_role="member",
            email=f"mem-{suffix}@test.com",
        )
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(
                f"/api/v1/workspaces/{ws}/api-keys",
                headers={"Authorization": f"Bearer {member_tok}"},
            )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 4. Revoke API key
# ---------------------------------------------------------------------------
class TestRevokeApiKey:
    @pytest.mark.asyncio
    async def test_admin_can_revoke(self, app):
        suffix = _uuid.uuid4().hex[:8]
        ws = f"ws-{suffix}"
        tok = await _seed(ws, f"t-{suffix}", f"admin-{suffix}", ws_role="workspace_admin")
        created = (await _create_key_via_api(app, tok, ws)).json()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.delete(
                f"/api/v1/workspaces/{ws}/api-keys/{created['id']}",
                headers={"Authorization": f"Bearer {tok}"},
            )
        assert resp.status_code == 204

        # Row still exists (soft delete) but is marked revoked.
        async with async_session() as session:
            stored = await session.get(ApiKey, created["id"])
        assert stored is not None
        assert stored.revoked == 1

    @pytest.mark.asyncio
    async def test_member_cannot_revoke(self, app):
        suffix = _uuid.uuid4().hex[:8]
        ws = f"ws-{suffix}"
        admin_tok = await _seed(ws, f"t-{suffix}", f"admin-{suffix}", ws_role="workspace_admin")
        created = (await _create_key_via_api(app, admin_tok, ws)).json()
        member_tok = await _seed(
            ws, f"t-{suffix}", f"mem-{suffix}", ws_role="member",
            email=f"mem-{suffix}@test.com",
        )
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.delete(
                f"/api/v1/workspaces/{ws}/api-keys/{created['id']}",
                headers={"Authorization": f"Bearer {member_tok}"},
            )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_revoke_cross_workspace_404(self, app):
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-{suffix}"
        ws_a = f"ws-a-{suffix}"
        ws_b = f"ws-b-{suffix}"
        tok_a = await _seed(ws_a, tid, f"admin-a-{suffix}", ws_role="workspace_admin")
        tok_b = await _seed(ws_b, tid, f"admin-b-{suffix}", ws_role="workspace_admin",
                            email=f"admin-b-{suffix}@test.com")
        created = (await _create_key_via_api(app, tok_a, ws_a)).json()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.delete(
                f"/api/v1/workspaces/{ws_b}/api-keys/{created['id']}",
                headers={"Authorization": f"Bearer {tok_b}"},
            )
        assert resp.status_code == 404
        # Key in ws_a must still be active.
        async with async_session() as session:
            stored = await session.get(ApiKey, created["id"])
        assert stored.revoked == 0

    @pytest.mark.asyncio
    async def test_revoke_writes_audit_log(self, app):
        suffix = _uuid.uuid4().hex[:8]
        ws = f"ws-{suffix}"
        tok = await _seed(ws, f"t-{suffix}", f"admin-{suffix}", ws_role="workspace_admin")
        created = (await _create_key_via_api(app, tok, ws, name="To Revoke")).json()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            await ac.delete(
                f"/api/v1/workspaces/{ws}/api-keys/{created['id']}",
                headers={"Authorization": f"Bearer {tok}"},
            )
        async with async_session() as session:
            rows = (await session.execute(
                select(AuditLog).where(
                    AuditLog.action == "api_key.revoke",
                    AuditLog.target_id == created["id"],
                )
            )).scalars().all()
        assert len(rows) == 1


# ---------------------------------------------------------------------------
# 5. Middleware: X-API-Key authentication flow
# ---------------------------------------------------------------------------
class TestApiKeyAuthentication:
    @pytest.mark.asyncio
    async def test_api_key_authenticates_protected_endpoint(self, app):
        """A valid X-API-Key reaches /api/v1/me/workspaces as the key creator."""
        suffix = _uuid.uuid4().hex[:8]
        ws = f"ws-{suffix}"
        tok = await _seed(ws, f"t-{suffix}", f"admin-{suffix}", ws_role="workspace_admin")
        plaintext = (await _create_key_via_api(app, tok, ws)).json()["key"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(
                "/api/v1/me/workspaces",
                headers={"X-API-Key": plaintext},
            )
        assert resp.status_code == 200
        # The creator is a member of ws, so it should appear in the list.
        ids = {w["id"] for w in resp.json()}
        assert ws in ids

    @pytest.mark.asyncio
    async def test_api_key_can_list_agents(self, app):
        """A valid X-API-Key reaches a workspace-scoped endpoint."""
        suffix = _uuid.uuid4().hex[:8]
        ws = f"ws-{suffix}"
        tok = await _seed(ws, f"t-{suffix}", f"admin-{suffix}", ws_role="workspace_admin")
        plaintext = (await _create_key_via_api(app, tok, ws)).json()["key"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(
                f"/api/v1/workspaces/{ws}/agents",
                headers={"X-API-Key": plaintext},
            )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_invalid_key_returns_401(self, app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(
                "/api/v1/me/workspaces",
                headers={"X-API-Key": "ap_totallyfakeinvalidkey12345678901234"},
            )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_revoked_key_returns_401(self, app):
        suffix = _uuid.uuid4().hex[:8]
        ws = f"ws-{suffix}"
        tok = await _seed(ws, f"t-{suffix}", f"admin-{suffix}", ws_role="workspace_admin")
        created = (await _create_key_via_api(app, tok, ws)).json()
        plaintext = created["key"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            await ac.delete(
                f"/api/v1/workspaces/{ws}/api-keys/{created['id']}",
                headers={"Authorization": f"Bearer {tok}"},
            )
            # Now the revoked key must no longer authenticate.
            resp = await ac.get(
                "/api/v1/me/workspaces",
                headers={"X-API-Key": plaintext},
            )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_expired_key_returns_401(self, app):
        suffix = _uuid.uuid4().hex[:8]
        ws = f"ws-{suffix}"
        tid = f"t-{suffix}"
        uid = f"admin-{suffix}"
        tok = await _seed(ws, tid, uid, ws_role="workspace_admin")
        # Create a key that is already expired by seeding directly.
        from src.gateway.routes.api_keys import _generate_key
        raw, prefix, digest = _generate_key()
        async with async_session() as session:
            session.add(ApiKey(
                workspace_id=ws,
                name="Expired",
                key_prefix=prefix,
                key_hash=digest,
                scopes=["chat:write"],
                created_by=uid,
                expires_at=datetime.now(timezone.utc) - timedelta(days=1),
            ))
            await session.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(
                "/api/v1/me/workspaces",
                headers={"X-API-Key": raw},
            )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_missing_both_headers_returns_401(self, app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/v1/me/workspaces")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_bearer_jwt_still_works(self, app):
        """No regression: Bearer JWT still authenticates after the middleware change."""
        suffix = _uuid.uuid4().hex[:8]
        tok = await _seed(f"ws-{suffix}", f"t-{suffix}", f"u-{suffix}", ws_role="workspace_admin")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(
                "/api/v1/me/workspaces",
                headers={"Authorization": f"Bearer {tok}"},
            )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_last_used_at_updated_after_use(self, app):
        suffix = _uuid.uuid4().hex[:8]
        ws = f"ws-{suffix}"
        tok = await _seed(ws, f"t-{suffix}", f"admin-{suffix}", ws_role="workspace_admin")
        created = (await _create_key_via_api(app, tok, ws)).json()
        plaintext = created["key"]

        async with async_session() as session:
            before = await session.get(ApiKey, created["id"])
        assert before.last_used_at is None

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            await ac.get(
                "/api/v1/me/workspaces",
                headers={"X-API-Key": plaintext},
            )

        async with async_session() as session:
            after = await session.get(ApiKey, created["id"])
        assert after.last_used_at is not None


# ---------------------------------------------------------------------------
# 6. Chat scope check
# ---------------------------------------------------------------------------
class TestChatScopeCheck:
    @pytest.mark.asyncio
    async def test_api_key_without_chat_write_scope_rejected(self, app):
        """An API key missing the chat:write scope cannot call /api/v1/chat."""
        suffix = _uuid.uuid4().hex[:8]
        ws = f"ws-{suffix}"
        tok = await _seed(ws, f"t-{suffix}", f"admin-{suffix}", ws_role="workspace_admin")
        plaintext = (await _create_key_via_api(
            app, tok, ws, scopes=["quota:read"],  # no chat:write
        )).json()["key"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/chat",
                headers={"X-API-Key": plaintext},
                json={
                    "messages": [{"role": "user", "content": "hi"}],
                    "config": {"workspace_id": ws},
                },
            )
        assert resp.status_code == 403
        assert "chat:write" in resp.json()["error"]["message"]
