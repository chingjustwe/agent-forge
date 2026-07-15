"""Tests for SSO authentication (Phase 1).

Covers:
- Model definition (SsoProvider, UserIdentity)
- OIDC helper logic (resolve_endpoints, Microsoft URL template filling)
- Admin CRUD: create/list/get/update/delete SSO providers, RBAC, audit log
- Public endpoints: list providers, login redirect
- Full SSO flow (mocked IdP): callback → token exchange → userinfo → JIT
  provisioning → JWT issuance
- Security: state CSRF protection, disabled provider 404, client_secret
  never exposed, auto_provision=false rejection
"""
import uuid as _uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from src.gateway.auth.jwt import create_jwt, decode_jwt
from src.gateway.auth.oidc import (
    PROVIDER_PRESETS,
    discover_endpoints,
    resolve_endpoints,
    verify_id_token,
)
from src.infra.db.engine import async_session
from src.infra.db.models import (
    AuditLog,
    SsoProvider,
    Tenant,
    User,
    UserIdentity,
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
    tenant_id: str,
    user_id: str,
    role: str = "tenant_admin",
    email: str | None = None,
) -> str:
    """Seed tenant + user. Returns JWT."""
    async with async_session() as session:
        if not await session.get(Tenant, tenant_id):
            session.add(Tenant(id=tenant_id, name="T", domain=f"{tenant_id}.test"))
            await session.flush()
        if not await session.get(User, user_id):
            session.add(
                User(
                    id=user_id,
                    tenant_id=tenant_id,
                    email=email or f"{user_id}@test.com",
                    name=user_id,
                    role=role,
                )
            )
            await session.flush()
        await session.commit()
    return _token(user_id, tenant_id, role=role, email=email)


async def _seed_provider(
    tenant_id: str | None = None,
    name: str = "Google",
    slug: str = "google",
    provider_type: str = "google",
    client_id: str = "test-client-id",
    client_secret: str = "test-secret",
    auto_provision: bool = True,
    enabled: bool = True,
    ms_tenant: str | None = None,
    authorize_url: str | None = None,
    token_url: str | None = None,
    userinfo_url: str | None = None,
) -> SsoProvider:
    """Insert an SsoProvider directly into the DB."""
    async with async_session() as session:
        provider = SsoProvider(
            tenant_id=tenant_id,
            name=name,
            slug=slug,
            provider_type=provider_type,
            client_id=client_id,
            client_secret=client_secret,
            authorize_url=authorize_url,
            token_url=token_url,
            userinfo_url=userinfo_url,
            ms_tenant=ms_tenant,
            auto_provision=1 if auto_provision else 0,
            enabled=1 if enabled else 0,
        )
        # Auto-fill URLs for built-in types.
        if provider_type in PROVIDER_PRESETS:
            endpoints = resolve_endpoints({
                "provider_type": provider_type,
                "ms_tenant": ms_tenant,
                "scopes": ["openid", "email", "profile"],
            })
            provider.authorize_url = endpoints["authorize_url"]
            provider.token_url = endpoints["token_url"]
            provider.userinfo_url = endpoints["userinfo_url"]
        session.add(provider)
        await session.commit()
        await session.refresh(provider)
        return provider


# ---------------------------------------------------------------------------
# 1. Model definition
# ---------------------------------------------------------------------------
class TestSsoModels:
    def test_sso_provider_tablename(self):
        assert SsoProvider.__tablename__ == "sso_providers"

    def test_sso_provider_fields(self):
        cols = {c.name for c in SsoProvider.__table__.columns}
        assert {
            "id", "tenant_id", "name", "slug", "provider_type",
            "client_id", "client_secret", "authorize_url", "token_url",
            "userinfo_url", "issuer_url", "scopes", "ms_tenant",
            "auto_provision", "default_role", "enabled",
            "created_at", "updated_at",
        } <= cols

    def test_user_identity_tablename(self):
        assert UserIdentity.__tablename__ == "user_identities"

    def test_user_identity_fields(self):
        cols = {c.name for c in UserIdentity.__table__.columns}
        assert {
            "id", "user_id", "provider_id", "provider_subject",
            "email_at_provider", "created_at",
        } <= cols

    def test_sso_provider_defaults(self):
        col = SsoProvider.__table__.columns["auto_provision"]
        assert col.default.arg == 1
        col = SsoProvider.__table__.columns["enabled"]
        assert col.default.arg == 1
        col = SsoProvider.__table__.columns["default_role"]
        assert col.default.arg == "member"


# ---------------------------------------------------------------------------
# 2. OIDC helper logic
# ---------------------------------------------------------------------------
class TestOidcHelpers:
    def test_resolve_endpoints_google(self):
        endpoints = resolve_endpoints({
            "provider_type": "google",
            "ms_tenant": None,
            "scopes": ["openid", "email", "profile"],
        })
        assert endpoints["authorize_url"] == "https://accounts.google.com/o/oauth2/v2/auth"
        assert endpoints["token_url"] == "https://oauth2.googleapis.com/token"
        assert endpoints["userinfo_url"] == "https://openidconnect.googleapis.com/v1/userinfo"
        assert endpoints["issuer_url"] == "https://accounts.google.com"

    def test_resolve_endpoints_microsoft_common(self):
        endpoints = resolve_endpoints({
            "provider_type": "microsoft",
            "ms_tenant": "common",
            "scopes": ["openid", "email", "profile"],
        })
        assert "login.microsoftonline.com/common/oauth2/v2.0/authorize" in endpoints["authorize_url"]
        assert "login.microsoftonline.com/common/oauth2/v2.0/token" in endpoints["token_url"]

    def test_resolve_endpoints_microsoft_specific_tenant(self):
        endpoints = resolve_endpoints({
            "provider_type": "microsoft",
            "ms_tenant": "aaa-bbb-ccc",
            "scopes": ["openid", "email", "profile"],
        })
        assert "login.microsoftonline.com/aaa-bbb-ccc/oauth2/v2.0/authorize" in endpoints["authorize_url"]

    def test_resolve_endpoints_custom_oidc(self):
        endpoints = resolve_endpoints({
            "provider_type": "custom_oidc",
            "authorize_url": "https://idp.example.com/auth",
            "token_url": "https://idp.example.com/token",
            "userinfo_url": "https://idp.example.com/userinfo",
            "issuer_url": "https://idp.example.com",
            "scopes": ["openid", "email"],
        })
        assert endpoints["authorize_url"] == "https://idp.example.com/auth"
        assert endpoints["token_url"] == "https://idp.example.com/token"

    def test_resolve_endpoints_custom_oidc_defaults(self):
        """custom_oidc with no URLs returns empty strings."""
        endpoints = resolve_endpoints({
            "provider_type": "custom_oidc",
            "authorize_url": None,
            "token_url": None,
            "userinfo_url": None,
        })
        assert endpoints["authorize_url"] == ""
        assert endpoints["token_url"] == ""


# ---------------------------------------------------------------------------
# 3. Admin CRUD
# ---------------------------------------------------------------------------
class TestSsoAdminCrud:
    @pytest.mark.asyncio
    async def test_create_google_provider(self, app):
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-{suffix}"
        tok = await _seed(tid, f"admin-{suffix}")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/admin/sso-providers",
                headers={"Authorization": f"Bearer {tok}"},
                json={
                    "name": "Google",
                    "slug": "google",
                    "provider_type": "google",
                    "client_id": "xxx.apps.googleusercontent.com",
                    "client_secret": "GOCSPX-xxx",
                },
            )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["id"]
        assert body["name"] == "Google"
        assert body["provider_type"] == "google"
        assert body["client_id"] == "xxx.apps.googleusercontent.com"
        # URLs auto-filled from preset.
        assert body["authorize_url"] == "https://accounts.google.com/o/oauth2/v2/auth"
        # client_secret must NEVER be in the response.
        assert "client_secret" not in body

    @pytest.mark.asyncio
    async def test_create_microsoft_provider(self, app):
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-{suffix}"
        tok = await _seed(tid, f"admin-{suffix}")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/admin/sso-providers",
                headers={"Authorization": f"Bearer {tok}"},
                json={
                    "name": "Microsoft",
                    "slug": "microsoft",
                    "provider_type": "microsoft",
                    "client_id": "ms-client-id",
                    "client_secret": "ms-secret",
                    "ms_tenant": "common",
                },
            )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert "login.microsoftonline.com/common" in body["authorize_url"]

    @pytest.mark.asyncio
    async def test_create_custom_oidc_requires_urls(self, app):
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-{suffix}"
        tok = await _seed(tid, f"admin-{suffix}")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/admin/sso-providers",
                headers={"Authorization": f"Bearer {tok}"},
                json={
                    "name": "Custom",
                    "slug": "custom",
                    "provider_type": "custom_oidc",
                    "client_id": "c-id",
                    "client_secret": "c-secret",
                },
            )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_create_custom_oidc_with_urls(self, app):
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-{suffix}"
        tok = await _seed(tid, f"admin-{suffix}")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/admin/sso-providers",
                headers={"Authorization": f"Bearer {tok}"},
                json={
                    "name": "Company IdP",
                    "slug": "company-idp",
                    "provider_type": "custom_oidc",
                    "client_id": "c-id",
                    "client_secret": "c-secret",
                    "authorize_url": "https://idp.example.com/auth",
                    "token_url": "https://idp.example.com/token",
                    "userinfo_url": "https://idp.example.com/userinfo",
                    "issuer_url": "https://idp.example.com",
                },
            )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["authorize_url"] == "https://idp.example.com/auth"

    @pytest.mark.asyncio
    async def test_duplicate_slug_rejected(self, app):
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-{suffix}"
        tok = await _seed(tid, f"admin-{suffix}")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            payload = {
                "name": "Google",
                "slug": "google",
                "provider_type": "google",
                "client_id": "x",
                "client_secret": "y",
            }
            resp1 = await ac.post(
                "/api/v1/admin/sso-providers",
                headers={"Authorization": f"Bearer {tok}"},
                json=payload,
            )
            assert resp1.status_code == 201
            resp2 = await ac.post(
                "/api/v1/admin/sso-providers",
                headers={"Authorization": f"Bearer {tok}"},
                json=payload,
            )
        assert resp2.status_code == 409

    @pytest.mark.asyncio
    async def test_list_providers(self, app):
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-{suffix}"
        tok = await _seed(tid, f"admin-{suffix}")
        await _seed_provider(tenant_id=tid, slug="google", name="Google")
        await _seed_provider(tenant_id=tid, slug="ms", name="MS", provider_type="microsoft")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(
                "/api/v1/admin/sso-providers",
                headers={"Authorization": f"Bearer {tok}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 2
        slugs = {p["slug"] for p in body}
        assert slugs == {"google", "ms"}

    @pytest.mark.asyncio
    async def test_update_provider(self, app):
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-{suffix}"
        tok = await _seed(tid, f"admin-{suffix}")
        provider = await _seed_provider(tenant_id=tid, slug="google")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.put(
                f"/api/v1/admin/sso-providers/{provider.id}",
                headers={"Authorization": f"Bearer {tok}"},
                json={"auto_provision": False, "name": "Google Updated"},
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["auto_provision"] is False
        assert body["name"] == "Google Updated"

    @pytest.mark.asyncio
    async def test_delete_provider(self, app):
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-{suffix}"
        tok = await _seed(tid, f"admin-{suffix}")
        provider = await _seed_provider(tenant_id=tid, slug="google")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.delete(
                f"/api/v1/admin/sso-providers/{provider.id}",
                headers={"Authorization": f"Bearer {tok}"},
            )
        assert resp.status_code == 200
        # Verify it's gone.
        async with async_session() as session:
            assert not await session.get(SsoProvider, provider.id)

    @pytest.mark.asyncio
    async def test_member_cannot_create_provider(self, app):
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-{suffix}"
        tok = await _seed(tid, f"mem-{suffix}", role="member")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/admin/sso-providers",
                headers={"Authorization": f"Bearer {tok}"},
                json={
                    "name": "Google",
                    "slug": "google",
                    "provider_type": "google",
                    "client_id": "x",
                    "client_secret": "y",
                },
            )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_create_writes_audit_log(self, app):
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-{suffix}"
        uid = f"admin-{suffix}"
        tok = await _seed(tid, uid)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/admin/sso-providers",
                headers={"Authorization": f"Bearer {tok}"},
                json={
                    "name": "Google",
                    "slug": "google",
                    "provider_type": "google",
                    "client_id": "x",
                    "client_secret": "y",
                },
            )
            provider_id = resp.json()["id"]
        async with async_session() as session:
            rows = (await session.execute(
                select(AuditLog).where(
                    AuditLog.action == "sso_provider.create",
                    AuditLog.target_id == provider_id,
                )
            )).scalars().all()
        assert len(rows) == 1
        assert rows[0].target_type == "sso_provider"


# ---------------------------------------------------------------------------
# 4. Public endpoints
# ---------------------------------------------------------------------------
class TestSsoPublicEndpoints:
    @pytest.mark.asyncio
    async def test_list_public_providers(self, app):
        # Use unique slug to identify our provider among any others
        # that may exist from other tests (shared DB).
        slug = f"google-{_uuid.uuid4().hex[:8]}"
        await _seed_provider(tenant_id=None, slug=slug, name="Google")
        await _seed_provider(tenant_id=None, slug=f"disabled-{slug}", name="Disabled", enabled=False)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/v1/auth/sso/providers")
        assert resp.status_code == 200
        body = resp.json()
        # Our enabled provider must be in the list.
        slugs = [p["slug"] for p in body["providers"]]
        assert slug in slugs
        # Disabled provider must NOT be in the list.
        assert f"disabled-{slug}" not in slugs
        # No secrets in public response.
        for p in body["providers"]:
            assert "client_secret" not in p
            assert "client_id" not in p

    @pytest.mark.asyncio
    async def test_login_redirects_to_idp(self, app):
        provider = await _seed_provider(tenant_id=None, slug="google", name="Google")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test", follow_redirects=False) as ac:
            resp = await ac.get(f"/api/v1/auth/sso/{provider.id}/login")
        assert resp.status_code == 302
        # Should redirect to Google's authorize URL.
        assert "accounts.google.com" in resp.headers["location"]
        # State cookie should be set.
        cookies = resp.headers.get_list("set-cookie")
        assert any("sso_state=" in c for c in cookies)

    @pytest.mark.asyncio
    async def test_login_disabled_provider_404(self, app):
        provider = await _seed_provider(tenant_id=None, slug="disabled", name="Disabled", enabled=False)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(f"/api/v1/auth/sso/{provider.id}/login")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_login_nonexistent_provider_404(self, app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/v1/auth/sso/nonexistent-id/login")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 5. Full SSO flow (mocked IdP)
# ---------------------------------------------------------------------------
class TestSsoFlow:
    @pytest.mark.asyncio
    async def test_first_login_creates_user_and_identity(self, app):
        """JIT provisioning: first SSO login creates a user + UserIdentity."""
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-{suffix}"
        # Seed tenant + a default workspace for auto-assignment.
        async with async_session() as session:
            session.add(Tenant(id=tid, name="T", domain=f"{tid}.test"))
            await session.flush()
            session.add(Workspace(id=f"ws-{suffix}", tenant_id=tid, name="Default", is_default=1))
            await session.commit()

        provider = await _seed_provider(tenant_id=tid, slug="google", name="Google")

        # Mock the IdP token exchange + userinfo.
        mock_token = {"access_token": "mock-at", "token_type": "Bearer"}
        mock_userinfo = {
            "sub": "google-sub-123",
            "email": f"newuser-{suffix}@gmail.com",
            "name": "New User",
            "email_verified": True,
        }

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test", follow_redirects=False) as ac:
            # Step 1: initiate login to get the state cookie.
            login_resp = await ac.get(f"/api/v1/auth/sso/{provider.id}/login")
            state_cookie = login_resp.cookies.get("sso_state", "")

            # Step 2: simulate IdP callback with the correct state.
            with patch(
                "src.gateway.routes.auth.exchange_code_for_token",
                new_callable=AsyncMock,
                return_value=mock_token,
            ), patch(
                "src.gateway.routes.auth.fetch_userinfo",
                new_callable=AsyncMock,
                return_value=mock_userinfo,
            ):
                callback_resp = await ac.get(
                    f"/api/v1/auth/sso/{provider.id}/callback?code=mock-code&state={state_cookie}",
                    cookies={"sso_state": state_cookie},
                )

        assert callback_resp.status_code == 302
        location = callback_resp.headers["location"]
        assert "/callback#token=" in location

        # Extract the JWT from the redirect URL fragment.
        fragment = location.split("#", 1)[1]
        token_param = next(p for p in fragment.split("&") if p.startswith("token="))
        jwt_token = token_param.removeprefix("token=")

        # Verify JWT contents.
        claims = decode_jwt(jwt_token)
        assert claims is not None
        assert claims["email"] == f"newuser-{suffix}@gmail.com"

        # Verify user + identity were created in DB.
        async with async_session() as session:
            result = await session.execute(
                select(User).where(User.email == f"newuser-{suffix}@gmail.com")
            )
            user = result.scalar_one()
            assert user.auth_provider == "sso"
            assert user.hashed_password is None

            result = await session.execute(
                select(UserIdentity).where(
                    UserIdentity.provider_id == provider.id,
                    UserIdentity.provider_subject == "google-sub-123",
                )
            )
            identity = result.scalar_one()
            assert identity.user_id == user.id

    @pytest.mark.asyncio
    async def test_second_login_reuses_existing_user(self, app):
        """Second SSO login finds the existing UserIdentity and reuses the user."""
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-{suffix}"
        async with async_session() as session:
            session.add(Tenant(id=tid, name="T", domain=f"{tid}.test"))
            await session.flush()
            user = User(
                tenant_id=tid,
                email=f"existing-{suffix}@gmail.com",
                name="Existing",
                role="member",
                auth_provider="sso",
            )
            session.add(user)
            await session.flush()
            provider = SsoProvider(
                tenant_id=tid, name="Google", slug="google", provider_type="google",
                client_id="x", client_secret="y",
                authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
                token_url="https://oauth2.googleapis.com/token",
                userinfo_url="https://openidconnect.googleapis.com/v1/userinfo",
            )
            session.add(provider)
            await session.flush()
            session.add(UserIdentity(
                user_id=user.id, provider_id=provider.id,
                provider_subject="google-sub-456",
                email_at_provider=user.email,
            ))
            await session.commit()
            await session.refresh(provider)
            user_id = user.id

        mock_token = {"access_token": "mock-at"}
        mock_userinfo = {
            "sub": "google-sub-456",
            "email": f"existing-{suffix}@gmail.com",
            "name": "Existing",
        }

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test", follow_redirects=False) as ac:
            login_resp = await ac.get(f"/api/v1/auth/sso/{provider.id}/login")
            state_cookie = login_resp.cookies.get("sso_state", "")
            with patch(
                "src.gateway.routes.auth.exchange_code_for_token",
                new_callable=AsyncMock, return_value=mock_token,
            ), patch(
                "src.gateway.routes.auth.fetch_userinfo",
                new_callable=AsyncMock, return_value=mock_userinfo,
            ):
                callback_resp = await ac.get(
                    f"/api/v1/auth/sso/{provider.id}/callback?code=mock-code&state={state_cookie}",
                    cookies={"sso_state": state_cookie},
                )
        assert callback_resp.status_code == 302
        # No new user should have been created.
        async with async_session() as session:
            result = await session.execute(select(User).where(User.email == f"existing-{suffix}@gmail.com"))
            assert result.scalar_one().id == user_id

    @pytest.mark.asyncio
    async def test_sso_does_not_link_existing_local_account_by_email(self, app):
        """SSO refuses to auto-link to an already-activated local account.

        If a user already has a password, SSO will not auto-link to prevent
        account hijacking. The user must log in with password and link SSO
        manually (Phase 2).
        """
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-{suffix}"
        async with async_session() as session:
            session.add(Tenant(id=tid, name="T", domain=f"{tid}.test"))
            await session.flush()
            # Existing activated (password) user.
            local_user = User(
                tenant_id=tid,
                email=f"local-{suffix}@gmail.com",
                name="Local",
                role="member",
                auth_provider="builtin",
                hashed_password="$2b$12$somehash",
            )
            session.add(local_user)
            await session.commit()
            local_user_id = local_user.id

        provider = await _seed_provider(tenant_id=tid, slug="google", name="Google")
        mock_token = {"access_token": "at"}
        mock_userinfo = {
            "sub": "google-sub-789",
            "email": f"local-{suffix}@gmail.com",
            "name": "Local",
        }
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test", follow_redirects=False) as ac:
            login_resp = await ac.get(f"/api/v1/auth/sso/{provider.id}/login")
            state_cookie = login_resp.cookies.get("sso_state", "")
            with patch(
                "src.gateway.routes.auth.exchange_code_for_token",
                new_callable=AsyncMock, return_value=mock_token,
            ), patch(
                "src.gateway.routes.auth.fetch_userinfo",
                new_callable=AsyncMock, return_value=mock_userinfo,
            ):
                callback_resp = await ac.get(
                    f"/api/v1/auth/sso/{provider.id}/callback?code=mock-code&state={state_cookie}",
                    cookies={"sso_state": state_cookie},
                )
        assert callback_resp.status_code == 302
        # Should redirect with email_already_registered error.
        assert "email_already_registered" in callback_resp.headers["location"]
        # No UserIdentity should have been created.
        async with async_session() as session:
            result = await session.execute(
                select(UserIdentity).where(UserIdentity.provider_subject == "google-sub-789")
            )
            assert result.scalar_one_or_none() is None

    @pytest.mark.asyncio
    async def test_sso_takes_over_inactive_invitation_user(self, app):
        """SSO takes over an inactive invitation user (no password).

        When a User was created by an invitation (no password, builtin auth)
        but the invitation expired before acceptance, SSO can "take over"
        the account. The role is reset to the provider's default (member),
        NOT the invitation role — preventing privilege escalation.
        """
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-{suffix}"
        async with async_session() as session:
            session.add(Tenant(id=tid, name="T", domain=f"{tid}.test"))
            await session.flush()
            # Inactive invitation user with workspace_admin role (from invitation).
            inv_user = User(
                tenant_id=tid,
                email=f"invited-{suffix}@gmail.com",
                name="Invited",
                role="workspace_admin",
                auth_provider="builtin",
                hashed_password=None,  # never activated
            )
            session.add(inv_user)
            await session.commit()
            inv_user_id = inv_user.id

        provider = await _seed_provider(tenant_id=tid, slug="google", name="Google")
        mock_token = {"access_token": "at"}
        mock_userinfo = {
            "sub": "google-sub-takeover",
            "email": f"invited-{suffix}@gmail.com",
            "name": "Invited",
        }
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test", follow_redirects=False) as ac:
            login_resp = await ac.get(f"/api/v1/auth/sso/{provider.id}/login")
            state_cookie = login_resp.cookies.get("sso_state", "")
            with patch(
                "src.gateway.routes.auth.exchange_code_for_token",
                new_callable=AsyncMock, return_value=mock_token,
            ), patch(
                "src.gateway.routes.auth.fetch_userinfo",
                new_callable=AsyncMock, return_value=mock_userinfo,
            ):
                callback_resp = await ac.get(
                    f"/api/v1/auth/sso/{provider.id}/callback?code=mock-code&state={state_cookie}",
                    cookies={"sso_state": state_cookie},
                )
        assert callback_resp.status_code == 302
        assert "token=" in callback_resp.headers["location"]
        # The existing user should have been taken over, not duplicated.
        async with async_session() as session:
            result = await session.execute(select(User).where(User.email == f"invited-{suffix}@gmail.com"))
            users = result.scalars().all()
            assert len(users) == 1
            assert users[0].id == inv_user_id
            # Role should be reset to member (provider default), NOT workspace_admin.
            assert users[0].role == "member"
            assert users[0].auth_provider == "sso"
            # Identity should point to the taken-over user.
            result = await session.execute(
                select(UserIdentity).where(UserIdentity.provider_subject == "google-sub-takeover")
            )
            identity = result.scalar_one()
            assert identity.user_id == inv_user_id

    @pytest.mark.asyncio
    async def test_auto_provision_disabled_rejects_unknown_user(self, app):
        """When auto_provision=false, unknown users are redirected with error."""
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-{suffix}"
        async with async_session() as session:
            session.add(Tenant(id=tid, name="T", domain=f"{tid}.test"))
            await session.commit()

        provider = await _seed_provider(
            tenant_id=tid, slug="google", name="Google",
            auto_provision=False,
        )
        mock_token = {"access_token": "at"}
        mock_userinfo = {
            "sub": "unknown-sub",
            "email": f"unknown-{suffix}@gmail.com",
            "name": "Unknown",
        }
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test", follow_redirects=False) as ac:
            login_resp = await ac.get(f"/api/v1/auth/sso/{provider.id}/login")
            state_cookie = login_resp.cookies.get("sso_state", "")
            with patch(
                "src.gateway.routes.auth.exchange_code_for_token",
                new_callable=AsyncMock, return_value=mock_token,
            ), patch(
                "src.gateway.routes.auth.fetch_userinfo",
                new_callable=AsyncMock, return_value=mock_userinfo,
            ):
                callback_resp = await ac.get(
                    f"/api/v1/auth/sso/{provider.id}/callback?code=mock-code&state={state_cookie}",
                    cookies={"sso_state": state_cookie},
                )
        assert callback_resp.status_code == 302
        assert "error=auto_provision_disabled" in callback_resp.headers["location"]

    @pytest.mark.asyncio
    async def test_idp_error_redirects_with_error(self, app):
        """IdP-reported error (e.g. user denied consent) redirects with error."""
        suffix = _uuid.uuid4().hex[:8]
        provider = await _seed_provider(tenant_id=None, slug="google", name="Google")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test", follow_redirects=False) as ac:
            resp = await ac.get(
                f"/api/v1/auth/sso/{provider.id}/callback?error=access_denied",
            )
        assert resp.status_code == 302
        assert "error=access_denied" in resp.headers["location"]


# ---------------------------------------------------------------------------
# 6. Security
# ---------------------------------------------------------------------------
class TestSsoSecurity:
    @pytest.mark.asyncio
    async def test_state_mismatch_rejected(self, app):
        """CSRF protection: mismatched state redirects with error."""
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-{suffix}"
        async with async_session() as session:
            session.add(Tenant(id=tid, name="T", domain=f"{tid}.test"))
            await session.commit()
        provider = await _seed_provider(tenant_id=tid, slug="google", name="Google")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test", follow_redirects=False) as ac:
            # State in URL doesn't match cookie.
            resp = await ac.get(
                f"/api/v1/auth/sso/{provider.id}/callback?code=x&state=wrong-state",
                cookies={"sso_state": "correct-state"},
            )
        assert resp.status_code == 302
        assert "error=state_mismatch" in resp.headers["location"]

    @pytest.mark.asyncio
    async def test_missing_state_rejected(self, app):
        """No state at all → rejected."""
        suffix = _uuid.uuid4().hex[:8]
        provider = await _seed_provider(tenant_id=None, slug="google", name="Google")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test", follow_redirects=False) as ac:
            resp = await ac.get(
                f"/api/v1/auth/sso/{provider.id}/callback?code=x",
            )
        assert resp.status_code == 302
        assert "error=state_mismatch" in resp.headers["location"]

    @pytest.mark.asyncio
    async def test_secret_never_in_admin_response(self, app):
        """client_secret must never appear in any API response."""
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-{suffix}"
        tok = await _seed(tid, f"admin-{suffix}")
        provider = await _seed_provider(tenant_id=tid, slug="google", name="Google")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # GET list
            resp = await ac.get(
                "/api/v1/admin/sso-providers",
                headers={"Authorization": f"Bearer {tok}"},
            )
            assert "client_secret" not in resp.text
            # GET single
            resp = await ac.get(
                f"/api/v1/admin/sso-providers/{provider.id}",
                headers={"Authorization": f"Bearer {tok}"},
            )
            assert "client_secret" not in resp.text

    @pytest.mark.asyncio
    async def test_callback_disabled_provider(self, app):
        """Callback for a disabled provider redirects with error."""
        suffix = _uuid.uuid4().hex[:8]
        provider = await _seed_provider(tenant_id=None, slug="google", name="Google", enabled=False)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test", follow_redirects=False) as ac:
            resp = await ac.get(
                f"/api/v1/auth/sso/{provider.id}/callback?code=x&state=anything",
                cookies={"sso_state": "anything"},
            )
        assert resp.status_code == 302
        assert "error=provider_not_found" in resp.headers["location"]


# ---------------------------------------------------------------------------
# 7. Me identities endpoint
# ---------------------------------------------------------------------------
class TestMeIdentities:
    @pytest.mark.asyncio
    async def test_list_my_identities(self, app):
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-{suffix}"
        uid = f"u-{suffix}"
        tok = await _seed(tid, uid, role="member")
        async with async_session() as session:
            provider = SsoProvider(
                tenant_id=tid, name="Google", slug="google", provider_type="google",
                client_id="x", client_secret="y",
                authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
                token_url="https://oauth2.googleapis.com/token",
                userinfo_url="https://openidconnect.googleapis.com/v1/userinfo",
            )
            session.add(provider)
            await session.flush()
            session.add(UserIdentity(
                user_id=uid, provider_id=provider.id,
                provider_subject="sub-123",
                email_at_provider=f"{uid}@gmail.com",
            ))
            await session.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(
                "/api/v1/me/identities",
                headers={"Authorization": f"Bearer {tok}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["identities"]) == 1
        assert body["identities"][0]["provider_name"] == "Google"
        assert body["identities"][0]["provider_type"] == "google"

    @pytest.mark.asyncio
    async def test_list_my_identities_empty(self, app):
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-{suffix}"
        uid = f"u-{suffix}"
        tok = await _seed(tid, uid, role="member")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(
                "/api/v1/me/identities",
                headers={"Authorization": f"Bearer {tok}"},
            )
        assert resp.status_code == 200
        assert resp.json() == {"identities": []}


# ---------------------------------------------------------------------------
# 8. OIDC Discovery (Phase 2)
# ---------------------------------------------------------------------------
class TestOidcDiscovery:
    @pytest.mark.asyncio
    async def test_discover_endpoints_extracts_all_fields(self):
        """discover_endpoints extracts all endpoints from the discovery document."""
        fake_metadata = {
            "authorization_endpoint": "https://idp.example.com/auth",
            "token_endpoint": "https://idp.example.com/token",
            "userinfo_endpoint": "https://idp.example.com/userinfo",
            "issuer": "https://idp.example.com",
            "jwks_uri": "https://idp.example.com/jwks",
        }
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value=fake_metadata)

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.get.return_value = mock_resp

        with patch("src.gateway.auth.oidc.httpx.AsyncClient", return_value=mock_client):
            result = await discover_endpoints("https://idp.example.com")

        assert result["authorize_url"] == "https://idp.example.com/auth"
        assert result["token_url"] == "https://idp.example.com/token"
        assert result["userinfo_url"] == "https://idp.example.com/userinfo"
        assert result["issuer_url"] == "https://idp.example.com"
        assert result["jwks_uri"] == "https://idp.example.com/jwks"

    @pytest.mark.asyncio
    async def test_discover_endpoints_missing_required_field(self):
        """Missing required field raises ValueError."""
        fake_metadata = {
            "authorization_endpoint": "https://idp.example.com/auth",
            "issuer": "https://idp.example.com",
            # token_endpoint missing
        }
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value=fake_metadata)

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.get.return_value = mock_resp

        with patch("src.gateway.auth.oidc.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(ValueError, match="missing required field"):
                await discover_endpoints("https://idp.example.com")

    @pytest.mark.asyncio
    async def test_discover_endpoints_http_error(self):
        """HTTP error propagates as exception."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock(side_effect=Exception("HTTP 500"))

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.get.return_value = mock_resp

        with patch("src.gateway.auth.oidc.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(Exception):
                await discover_endpoints("https://idp.example.com")


# ---------------------------------------------------------------------------
# 9. ID Token verification (Phase 2)
# ---------------------------------------------------------------------------
class TestIdTokenVerification:
    @pytest.mark.asyncio
    async def test_verify_id_token_missing_jwks_uri(self):
        """Missing jwks_uri raises ValueError."""
        provider = {
            "provider_type": "custom_oidc",
            "client_id": "test-client",
            "client_secret": "test-secret",
            "authorize_url": "https://idp.example.com/auth",
            "token_url": "https://idp.example.com/token",
            "userinfo_url": "https://idp.example.com/userinfo",
            "issuer_url": "https://idp.example.com",
        }
        with pytest.raises(ValueError, match="No jwks_uri"):
            await verify_id_token("fake-token", provider)

    @pytest.mark.asyncio
    async def test_verify_id_token_invalid_token(self):
        """Invalid ID Token raises ValueError after JWKS fetch."""
        provider = {
            "provider_type": "custom_oidc",
            "client_id": "test-client",
            "client_secret": "test-secret",
            "authorize_url": "https://idp.example.com/auth",
            "token_url": "https://idp.example.com/token",
            "userinfo_url": "https://idp.example.com/userinfo",
            "issuer_url": "https://idp.example.com",
            "jwks_uri": "https://idp.example.com/jwks",
        }
        fake_jwks = {
            "keys": [
                {
                    "kty": "RSA",
                    "kid": "test-key-1",
                    "use": "sig",
                    "alg": "RS256",
                    "n": "0vx7agoebGcQSuuPiLJXZptN9nndrQmbXEps2aiAFbWhM78LhWx4cbbfAAtVT86zwu1RK7aPFFxuhDR1L6tSoc_BJECPebWKRXjBZCiFV4n3oknjhMstn64tZ_2W-5JsGY4Hc5n9yBXArwl93lqt7_RN5w6Cf0h4QyQ5v-65YGjQR0_FDW2QvzqY368QQMicAtaSqzs8KJZgnYb9c7d0zgdAZHzu6qMQvRL5hajrn1n91CbOpbISD08qNLyrdkt-bFTWhAI4vMQFh6WeZu0fM4lFd2NcRwr3XPksINHaQ-G_xBniIqbw0Ls1jF44-csFCur-kEgU8awapJzKnqDKgw",
                    "e": "AQAB",
                }
            ]
        }
        with patch("src.gateway.auth.oidc.fetch_jwks", new_callable=AsyncMock, return_value=fake_jwks):
            with pytest.raises(ValueError, match="ID Token verification failed"):
                await verify_id_token("invalid.token.here", provider)


# ---------------------------------------------------------------------------
# 10. Admin SSO provider with OIDC Discovery (Phase 2)
# ---------------------------------------------------------------------------
class TestSsoAdminDiscovery:
    @pytest.mark.asyncio
    async def test_create_with_oidc_discovery(self, app):
        """Admin creates custom_oidc provider with issuer_url — discovery auto-fills URLs."""
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-{suffix}"
        tok = await _seed(tid, f"admin-{suffix}")
        fake_discovery = {
            "authorize_url": "https://idp.example.com/auth",
            "token_url": "https://idp.example.com/token",
            "userinfo_url": "https://idp.example.com/userinfo",
            "issuer_url": "https://idp.example.com",
            "jwks_uri": "https://idp.example.com/jwks",
        }
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            with patch("src.gateway.auth.oidc.discover_endpoints", new_callable=AsyncMock, return_value=fake_discovery):
                resp = await ac.post(
                    "/api/v1/admin/sso-providers",
                    headers={"Authorization": f"Bearer {tok}"},
                    json={
                        "name": "Company IdP",
                        "slug": f"company-{suffix}",
                        "provider_type": "custom_oidc",
                        "client_id": "c-id",
                        "client_secret": "c-secret",
                        "issuer_url": "https://idp.example.com",
                    },
                )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["authorize_url"] == "https://idp.example.com/auth"
        assert body["token_url"] == "https://idp.example.com/token"
        assert body["userinfo_url"] == "https://idp.example.com/userinfo"
        assert body["issuer_url"] == "https://idp.example.com"
        assert body["jwks_uri"] == "https://idp.example.com/jwks"

    @pytest.mark.asyncio
    async def test_discovery_failure_returns_400(self, app):
        """OIDC Discovery failure returns 400."""
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-{suffix}"
        tok = await _seed(tid, f"admin-{suffix}")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            with patch("src.gateway.auth.oidc.discover_endpoints", new_callable=AsyncMock, side_effect=Exception("Connection refused")):
                resp = await ac.post(
                    "/api/v1/admin/sso-providers",
                    headers={"Authorization": f"Bearer {tok}"},
                    json={
                        "name": "Company IdP",
                        "slug": f"company-{suffix}",
                        "provider_type": "custom_oidc",
                        "client_id": "c-id",
                        "client_secret": "c-secret",
                        "issuer_url": "https://idp.example.com",
                    },
                )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# 11. Unlink identity (Phase 2)
# ---------------------------------------------------------------------------
class TestUnlinkIdentity:
    @pytest.mark.asyncio
    async def test_unlink_with_password_succeeds(self, app):
        """User with password + one SSO identity → unlink succeeds."""
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-{suffix}"
        uid = f"u-{suffix}"
        tok = await _seed(tid, uid, role="member")
        provider = await _seed_provider(tenant_id=tid, slug=f"google-{suffix}", name="Google")
        async with async_session() as session:
            user = await session.get(User, uid)
            user.hashed_password = "$2b$12$somehash"
            identity = UserIdentity(
                user_id=uid, provider_id=provider.id,
                provider_subject="sub-1",
                email_at_provider=f"{uid}@gmail.com",
            )
            session.add(identity)
            await session.commit()
            await session.refresh(identity)
            identity_id = identity.id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.delete(
                f"/api/v1/me/identities/{identity_id}",
                headers={"Authorization": f"Bearer {tok}"},
            )
        assert resp.status_code == 200, resp.text

    @pytest.mark.asyncio
    async def test_unlink_last_identity_sso_only_blocked(self, app):
        """SSO-only user (no password) with one identity → unlink returns 400."""
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-{suffix}"
        uid = f"u-{suffix}"
        tok = await _seed(tid, uid, role="member")
        provider = await _seed_provider(tenant_id=tid, slug=f"google-{suffix}", name="Google")
        async with async_session() as session:
            identity = UserIdentity(
                user_id=uid, provider_id=provider.id,
                provider_subject="sub-1",
                email_at_provider=f"{uid}@gmail.com",
            )
            session.add(identity)
            await session.commit()
            await session.refresh(identity)
            identity_id = identity.id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.delete(
                f"/api/v1/me/identities/{identity_id}",
                headers={"Authorization": f"Bearer {tok}"},
            )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_unlink_one_of_two_identities_sso_only(self, app):
        """SSO-only user with two identities → unlink one succeeds."""
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-{suffix}"
        uid = f"u-{suffix}"
        tok = await _seed(tid, uid, role="member")
        provider1 = await _seed_provider(tenant_id=tid, slug=f"google-{suffix}", name="Google")
        provider2 = await _seed_provider(tenant_id=tid, slug=f"google2-{suffix}", name="Google2")
        async with async_session() as session:
            identity1 = UserIdentity(
                user_id=uid, provider_id=provider1.id,
                provider_subject="sub-1",
                email_at_provider=f"{uid}@gmail.com",
            )
            identity2 = UserIdentity(
                user_id=uid, provider_id=provider2.id,
                provider_subject="sub-2",
                email_at_provider=f"{uid}@github.com",
            )
            session.add_all([identity1, identity2])
            await session.commit()
            await session.refresh(identity1)
            identity_id = identity1.id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.delete(
                f"/api/v1/me/identities/{identity_id}",
                headers={"Authorization": f"Bearer {tok}"},
            )
        assert resp.status_code == 200, resp.text

    @pytest.mark.asyncio
    async def test_unlink_other_user_identity_404(self, app):
        """Unlink another user's identity → 404."""
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-{suffix}"
        uid_a = f"u-a-{suffix}"
        uid_b = f"u-b-{suffix}"
        tok_a = await _seed(tid, uid_a, role="member")
        tok_b = await _seed(tid, uid_b, role="member")
        provider = await _seed_provider(tenant_id=tid, slug=f"google-{suffix}", name="Google")
        async with async_session() as session:
            identity = UserIdentity(
                user_id=uid_a, provider_id=provider.id,
                provider_subject="sub-a",
                email_at_provider=f"{uid_a}@gmail.com",
            )
            session.add(identity)
            await session.commit()
            await session.refresh(identity)
            identity_id = identity.id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.delete(
                f"/api/v1/me/identities/{identity_id}",
                headers={"Authorization": f"Bearer {tok_b}"},
            )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 12. Email domain → tenant auto-mapping (Phase 2)
# ---------------------------------------------------------------------------
class TestEmailDomainTenantMapping:
    @pytest.mark.asyncio
    async def test_email_domain_maps_to_tenant(self, app):
        """Global SSO provider + email domain matching tenant → user assigned to that tenant."""
        suffix = _uuid.uuid4().hex[:8]
        domain = f"company-{suffix}.com"
        company_tid = f"company-{suffix}"
        async with async_session() as session:
            session.add(Tenant(id=company_tid, name="Company", domain=domain))
            await session.flush()
            session.add(Workspace(id=f"ws-{suffix}", tenant_id=company_tid, name="Default", is_default=1))
            await session.commit()

        provider = await _seed_provider(tenant_id=None, slug=f"google-{suffix}", name="Google")
        mock_token = {"access_token": "at"}
        mock_userinfo = {
            "sub": f"sub-{suffix}",
            "email": f"user-{suffix}@{domain}",
            "name": "Company User",
        }
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test", follow_redirects=False) as ac:
            login_resp = await ac.get(f"/api/v1/auth/sso/{provider.id}/login")
            state_cookie = login_resp.cookies.get("sso_state", "")
            with patch(
                "src.gateway.routes.auth.exchange_code_for_token",
                new_callable=AsyncMock, return_value=mock_token,
            ), patch(
                "src.gateway.routes.auth.fetch_userinfo",
                new_callable=AsyncMock, return_value=mock_userinfo,
            ):
                callback_resp = await ac.get(
                    f"/api/v1/auth/sso/{provider.id}/callback?code=mock-code&state={state_cookie}",
                    cookies={"sso_state": state_cookie},
                )
        assert callback_resp.status_code == 302
        assert "token=" in callback_resp.headers["location"]

        async with async_session() as session:
            result = await session.execute(
                select(User).where(User.email == f"user-{suffix}@{domain}")
            )
            user = result.scalar_one()
            assert user.tenant_id == company_tid

    @pytest.mark.asyncio
    async def test_email_domain_no_match_falls_back(self, app):
        """No tenant domain match → user assigned to first tenant (fallback)."""
        suffix = _uuid.uuid4().hex[:8]
        domain = f"company-{suffix}.com"
        company_tid = f"company-{suffix}"
        async with async_session() as session:
            session.add(Tenant(id=company_tid, name="Company", domain=domain))
            await session.flush()
            session.add(Workspace(id=f"ws-{suffix}", tenant_id=company_tid, name="Default", is_default=1))
            await session.commit()

        # Query the first tenant (what the fallback will use).
        async with async_session() as session:
            first_tenant = (await session.execute(select(Tenant).limit(1))).scalar_one_or_none()
            first_tenant_id = first_tenant.id if first_tenant else None

        provider = await _seed_provider(tenant_id=None, slug=f"google-{suffix}", name="Google")
        mock_token = {"access_token": "at"}
        mock_userinfo = {
            "sub": f"sub-{suffix}",
            "email": f"nouser-{suffix}@gmail.com",
            "name": "Gmail User",
        }
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test", follow_redirects=False) as ac:
            login_resp = await ac.get(f"/api/v1/auth/sso/{provider.id}/login")
            state_cookie = login_resp.cookies.get("sso_state", "")
            with patch(
                "src.gateway.routes.auth.exchange_code_for_token",
                new_callable=AsyncMock, return_value=mock_token,
            ), patch(
                "src.gateway.routes.auth.fetch_userinfo",
                new_callable=AsyncMock, return_value=mock_userinfo,
            ):
                callback_resp = await ac.get(
                    f"/api/v1/auth/sso/{provider.id}/callback?code=mock-code&state={state_cookie}",
                    cookies={"sso_state": state_cookie},
                )
        assert callback_resp.status_code == 302
        assert "token=" in callback_resp.headers["location"]

        async with async_session() as session:
            result = await session.execute(
                select(User).where(User.email == f"nouser-{suffix}@gmail.com")
            )
            user = result.scalar_one()
            assert user.tenant_id == first_tenant_id
