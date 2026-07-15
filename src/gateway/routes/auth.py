import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.gateway.auth.jwt import create_jwt
from src.gateway.auth.oidc import (
    exchange_code_for_token,
    fetch_userinfo,
    get_authorize_url,
)
from src.gateway.auth.password import hash_password, verify_password
from src.infra.db.models import (
    InviteToken,
    SsoProvider,
    Tenant,
    User,
    UserIdentity,
    Workspace,
    WorkspaceInvitation,
    WorkspaceMember,
)
from src.infra.db.session import get_db
from src.infra.settings import settings

logger = logging.getLogger(__name__)

router = APIRouter()


class RegisterRequest(BaseModel):
    email: str
    password: str
    name: str


class LoginRequest(BaseModel):
    email: str
    password: str


async def _user_workspace_ids(db: AsyncSession, user_id: str) -> list[str]:
    """Read workspace_ids from WorkspaceMember for the given user."""
    rows = await db.execute(
        select(WorkspaceMember.workspace_id).where(WorkspaceMember.user_id == user_id)
    )
    return [r[0] for r in rows.all()]


@router.post("/api/v1/auth/register")
async def register(body: RegisterRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == body.email))
    if result.scalar_one_or_none():
        return JSONResponse(status_code=409, content={"error": {"code": "CONFLICT", "message": "Email already registered"}})

    result = await db.execute(select(Tenant).limit(1))
    tenant = result.scalar_one_or_none()
    if not tenant:
        tenant = Tenant(name="Default", domain="default.local")
        db.add(tenant)
        await db.flush()

    # Auto-assign to the tenant's default workspace
    result = await db.execute(
        select(Workspace).where(Workspace.tenant_id == tenant.id, Workspace.is_default == 1)
    )
    default_ws = result.scalar_one_or_none()

    user = User(
        tenant_id=tenant.id,
        email=body.email,
        name=body.name,
        role="member",
        auth_provider="builtin",
        hashed_password=hash_password(body.password),
    )
    db.add(user)
    await db.flush()
    if default_ws:
        db.add(
            WorkspaceMember(
                workspace_id=default_ws.id,
                user_id=user.id,
                role="member",
            )
        )
    await db.commit()
    await db.refresh(user)

    ws_ids = await _user_workspace_ids(db, user.id)
    token = create_jwt({
        "id": user.id,
        "tenant_id": user.tenant_id,
        "email": user.email,
        "role": user.role,
    })
    return JSONResponse(
        status_code=201,
        content={
            "token": token,
            "user": {
                "id": user.id,
                "email": user.email,
                "name": user.name,
                "role": user.role,
                "workspace_ids": ws_ids,
            },
        },
    )


# ─── Invite flow ────────────────────────────────────────────────────────────


@router.get("/api/v1/auth/invite")
async def get_invite(token: str, db: AsyncSession = Depends(get_db)):
    """Validate an invite token and return user info (email, role)."""
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    result = await db.execute(select(InviteToken).where(InviteToken.token_hash == token_hash))
    invite = result.scalar_one_or_none()
    if not invite:
        return JSONResponse(status_code=404, content={"error": {"code": "NOT_FOUND", "message": "Invalid invite link"}})
    if invite.used_at:
        return JSONResponse(status_code=410, content={"error": {"code": "GONE", "message": "This invite has already been used"}})
    # SQLite doesn't preserve tzinfo — strip from both sides for comparison
    if invite.expires_at.replace(tzinfo=None) < datetime.now(timezone.utc).replace(tzinfo=None):
        return JSONResponse(status_code=410, content={"error": {"code": "EXPIRED", "message": "This invite has expired"}})

    user = await db.get(User, invite.user_id)
    if not user:
        return JSONResponse(status_code=404, content={"error": {"code": "NOT_FOUND", "message": "User not found"}})
    if user.hashed_password:
        return JSONResponse(status_code=400, content={"error": {"code": "BAD_REQUEST", "message": "User already registered"}})

    return {
        "email": user.email,
        "role": user.role,
    }


class AcceptInviteRequest(BaseModel):
    token: str
    password: str
    name: str


@router.post("/api/v1/auth/accept-invite")
async def accept_invite(body: AcceptInviteRequest, db: AsyncSession = Depends(get_db)):
    """Accept an invitation: set password, activate account, return JWT."""
    token_hash = hashlib.sha256(body.token.encode()).hexdigest()
    result = await db.execute(select(InviteToken).where(InviteToken.token_hash == token_hash))
    invite = result.scalar_one_or_none()
    if not invite:
        return JSONResponse(status_code=404, content={"error": {"code": "NOT_FOUND", "message": "Invalid invite link"}})
    if invite.used_at:
        return JSONResponse(status_code=410, content={"error": {"code": "GONE", "message": "This invite has already been used"}})
    # SQLite doesn't preserve tzinfo — strip from both sides for comparison
    if invite.expires_at.replace(tzinfo=None) < datetime.now(timezone.utc).replace(tzinfo=None):
        return JSONResponse(status_code=410, content={"error": {"code": "EXPIRED", "message": "This invite has expired"}})

    user = await db.get(User, invite.user_id)
    if not user:
        return JSONResponse(status_code=404, content={"error": {"code": "NOT_FOUND", "message": "User not found"}})
    if user.hashed_password:
        return JSONResponse(status_code=400, content={"error": {"code": "BAD_REQUEST", "message": "User already registered"}})

    user.name = body.name
    user.hashed_password = hash_password(body.password)
    invite.used_at = datetime.now(timezone.utc)

    # Process pending workspace invitations for this email
    ws_invites = await db.execute(
        select(WorkspaceInvitation).where(
            WorkspaceInvitation.email == user.email,
            WorkspaceInvitation.accepted_at.is_(None),
        )
    )
    for ws_invite in ws_invites.scalars().all():
        # Add user to workspace with the invited role
        existing = await db.get(WorkspaceMember, (ws_invite.workspace_id, user.id))
        if not existing:
            db.add(WorkspaceMember(
                workspace_id=ws_invite.workspace_id,
                user_id=user.id,
                role=ws_invite.role,
            ))
        ws_invite.accepted_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(user)

    ws_ids = await _user_workspace_ids(db, user.id)
    token = create_jwt({
        "id": user.id,
        "tenant_id": user.tenant_id,
        "email": user.email,
        "role": user.role,
    })
    return JSONResponse(
        status_code=200,
        content={
            "token": token,
            "user": {
                "id": user.id,
                "email": user.email,
                "name": user.name,
                "role": user.role,
                "workspace_ids": ws_ids,
            },
        },
    )


@router.post("/api/v1/auth/login")
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()
    if not user or not user.hashed_password:
        return JSONResponse(status_code=401, content={"error": {"code": "UNAUTHORIZED", "message": "Invalid credentials"}})

    if not verify_password(body.password, user.hashed_password):
        return JSONResponse(status_code=401, content={"error": {"code": "UNAUTHORIZED", "message": "Invalid credentials"}})

    ws_ids = await _user_workspace_ids(db, user.id)
    token = create_jwt({
        "id": user.id,
        "tenant_id": user.tenant_id,
        "email": user.email,
        "role": user.role,
    })
    return {
        "token": token,
        "user": {
            "id": user.id,
            "email": user.email,
            "name": user.name,
            "role": user.role,
            "workspace_ids": ws_ids,
        },
    }


@router.get("/api/v1/auth/login")
async def oidc_login_legacy(provider: str = "google", redirect: str = "http://localhost:5173/callback"):
    """Legacy OIDC entry — kept for backwards compat, redirects to new SSO flow.

    Deprecated: use ``GET /api/v1/auth/sso/{provider_id}/login`` instead.
    """
    return JSONResponse(
        status_code=410,
        content={"error": {"code": "GONE", "message": "Use GET /api/v1/auth/sso/{provider_id}/login instead"}},
    )


@router.get("/api/v1/auth/callback")
async def oidc_callback_legacy():
    """Legacy OIDC callback — kept for backwards compat."""
    return JSONResponse(
        status_code=410,
        content={"error": {"code": "GONE", "message": "Use GET /api/v1/auth/sso/{provider_id}/callback instead"}},
    )


# ─── SSO (Phase 1 — SSO authentication) ────────────────────────────────────


def _provider_to_dict(p: SsoProvider) -> dict:
    """Convert an SsoProvider ORM row to the dict shape expected by oidc.py."""
    return {
        "provider_type": p.provider_type,
        "client_id": p.client_id,
        "client_secret": p.client_secret,
        "authorize_url": p.authorize_url,
        "token_url": p.token_url,
        "userinfo_url": p.userinfo_url,
        "issuer_url": p.issuer_url,
        "jwks_uri": p.jwks_uri,
        "scopes": p.scopes or ["openid", "email", "profile"],
        "ms_tenant": p.ms_tenant,
    }


@router.get("/api/v1/auth/sso/providers")
async def list_sso_providers(db: AsyncSession = Depends(get_db)):
    """List all enabled SSO providers (public — no auth required).

    Returns only the fields needed to render login buttons: id, name,
    slug, provider_type. Never exposes client_secret.
    """
    result = await db.execute(
        select(SsoProvider).where(SsoProvider.enabled == 1)
    )
    providers = result.scalars().all()
    return {
        "providers": [
            {
                "id": p.id,
                "name": p.name,
                "slug": p.slug,
                "provider_type": p.provider_type,
            }
            for p in providers
        ]
    }


@router.get("/api/v1/auth/sso/{provider_id}/login")
async def sso_login(
    provider_id: str,
    request: Request,
    redirect: str = "/",
    db: AsyncSession = Depends(get_db),
):
    """Initiate SSO login — 302 redirect to the IdP authorize URL.

    Sets an httpOnly cookie with a random ``state`` value for CSRF
    protection; the callback verifies this cookie matches the ``state``
    query parameter returned by the IdP.
    """
    provider = await db.get(SsoProvider, provider_id)
    if not provider or not provider.enabled:
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "NOT_FOUND", "message": "SSO provider not found or disabled"}},
        )

    state = secrets.token_hex(16)
    redirect_uri = f"{settings.app_base_url.rstrip('/')}/api/v1/auth/sso/{provider_id}/callback"

    provider_dict = _provider_to_dict(provider)
    authorize_url = get_authorize_url(provider_dict, redirect_uri, state)

    # Store redirect target in cookie for post-callback navigation.
    resp = RedirectResponse(url=authorize_url, status_code=302)
    resp.set_cookie(
        key=settings.sso_state_cookie_name,
        value=state,
        max_age=settings.sso_state_ttl_seconds,
        httponly=True,
        secure=True,
        samesite="lax",
    )
    # Store the post-login redirect target (frontend path) in a short-lived
    # cookie so the callback knows where to send the user afterwards.
    resp.set_cookie(
        key="sso_redirect",
        value=redirect,
        max_age=settings.sso_state_ttl_seconds,
        httponly=True,
        secure=True,
        samesite="lax",
    )
    return resp


@router.get("/api/v1/auth/sso/{provider_id}/callback")
async def sso_callback(
    provider_id: str,
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    db: AsyncSession = Depends(get_db),
):
    """IdP callback — complete SSO login.

    1. Verify ``state`` cookie matches the URL parameter (CSRF protection).
    2. Exchange ``code`` for an access token at the IdP token endpoint.
    3. Fetch userinfo from the IdP.
    4. Look up or create a local user + ``UserIdentity`` record.
    5. Issue a JWT and 302 redirect to the frontend ``/callback`` page with
       the token in the URL fragment (``#token=xxx``).
    """
    frontend_callback = f"{settings.app_base_url.rstrip('/')}/callback"

    # IdP-reported error (e.g. user denied consent).
    if error:
        return RedirectResponse(
            url=f"{frontend_callback}?error={error}", status_code=302
        )

    # Verify state cookie (CSRF protection).
    cookie_state = request.cookies.get(settings.sso_state_cookie_name, "")
    if not state or state != cookie_state:
        return RedirectResponse(
            url=f"{frontend_callback}?error=state_mismatch", status_code=302
        )

    provider = await db.get(SsoProvider, provider_id)
    if not provider or not provider.enabled:
        return RedirectResponse(
            url=f"{frontend_callback}?error=provider_not_found", status_code=302
        )

    provider_dict = _provider_to_dict(provider)
    redirect_uri = f"{settings.app_base_url.rstrip('/')}/api/v1/auth/sso/{provider_id}/callback"

    # Exchange code → access_token.
    try:
        token_resp = await exchange_code_for_token(provider_dict, code, redirect_uri)
    except Exception as exc:
        logger.warning("SSO token exchange failed for provider %s: %s", provider_id, exc)
        return RedirectResponse(
            url=f"{frontend_callback}?error=token_exchange_failed", status_code=302
        )

    access_token = token_resp.get("access_token", "")
    if not access_token:
        return RedirectResponse(
            url=f"{frontend_callback}?error=no_access_token", status_code=302
        )

    # Fetch userinfo from IdP.
    try:
        userinfo = await fetch_userinfo(provider_dict, access_token)
    except Exception as exc:
        logger.warning("SSO userinfo fetch failed for provider %s: %s", provider_id, exc)
        return RedirectResponse(
            url=f"{frontend_callback}?error=userinfo_failed", status_code=302
        )

    # Phase 2: If an id_token was returned and we have a jwks_uri, verify it
    # and prefer verified claims from the ID Token over userinfo.
    id_token_raw = token_resp.get("id_token")
    if id_token_raw and provider.jwks_uri:
        try:
            from src.gateway.auth.oidc import verify_id_token
            id_claims = await verify_id_token(id_token_raw, provider_dict, provider.client_id)
            # ID Token claims take precedence (they are signed and verified).
            for key in ("sub", "email", "name", "email_verified"):
                if id_claims.get(key) is not None:
                    userinfo[key] = id_claims[key]
        except Exception as exc:
            logger.warning("SSO ID Token verification failed for provider %s: %s", provider_id, exc)
            return RedirectResponse(
                url=f"{frontend_callback}?error=id_token_invalid", status_code=302
            )

    sub = userinfo.get("sub", "")
    email = userinfo.get("email", "")
    name = userinfo.get("name") or email or "SSO User"

    if not sub:
        return RedirectResponse(
            url=f"{frontend_callback}?error=no_subject", status_code=302
        )

    # ── User matching ──────────────────────────────────────────────
    # SSO is an independent auth route from password/invitation login.
    # We match via UserIdentity (provider_id + provider_subject) first.
    # For first-time SSO login, we handle the email collision case:
    #   - If a User with the same email exists but was created by an
    #     invitation (no password, auth_provider=builtin), SSO "takes
    #     over" the inactive account — resetting role to the provider's
    #     default (member) instead of inheriting the invitation role.
    #   - If a User with the same email exists and is already activated
    #     (has a password or is already an SSO user), we refuse to
    #     auto-link to prevent account hijacking.
    result = await db.execute(
        select(UserIdentity).where(
            UserIdentity.provider_id == provider_id,
            UserIdentity.provider_subject == sub,
        )
    )
    identity = result.scalar_one_or_none()

    if identity:
        # Existing SSO user — load the linked User.
        user = await db.get(User, identity.user_id)
        if not user or user.archived:
            return RedirectResponse(
                url=f"{frontend_callback}?error=user_not_found", status_code=302
            )
    else:
        # No existing identity — check auto_provision.
        if not provider.auto_provision:
            return RedirectResponse(
                url=f"{frontend_callback}?error=auto_provision_disabled", status_code=302
            )

        tenant_id = provider.tenant_id
        if not tenant_id:
            # Global provider — try email domain → tenant mapping.
            if email and "@" in email:
                email_domain = email.rsplit("@", 1)[-1].lower()
                result = await db.execute(
                    select(Tenant).where(Tenant.domain == email_domain)
                )
                matched_tenant = result.scalar_one_or_none()
                if matched_tenant:
                    tenant_id = matched_tenant.id
            # Fallback: use system's first tenant.
            if not tenant_id:
                result = await db.execute(select(Tenant).limit(1))
                tenant = result.scalar_one_or_none()
                if not tenant:
                    tenant = Tenant(name="Default", domain="default.local")
                    db.add(tenant)
                    await db.flush()
                tenant_id = tenant.id

        # Check if a User with this email already exists.
        existing_user = None
        if email:
            result = await db.execute(select(User).where(User.email == email))
            existing_user = result.scalar_one_or_none()

        if existing_user:
            if existing_user.hashed_password or existing_user.auth_provider == "sso":
                # Already activated — refuse to auto-link for security.
                return RedirectResponse(
                    url=f"{frontend_callback}?error=email_already_registered", status_code=302
                )
            # Inactive invitation user — SSO takes over the account.
            # Reset role to provider default; do NOT inherit invitation role.
            user = existing_user
            user.role = provider.default_role or "member"
            user.auth_provider = "sso"
            user.name = name
            await db.flush()
        else:
            # Create a brand new SSO user.
            user = User(
                tenant_id=tenant_id,
                email=email or f"sso_{sub[:16]}@noemail.local",
                name=name,
                role=provider.default_role or "member",
                auth_provider="sso",
                hashed_password=None,
            )
            db.add(user)
            await db.flush()

            # Auto-assign to tenant's default workspace.
            result = await db.execute(
                select(Workspace).where(
                    Workspace.tenant_id == tenant_id,
                    Workspace.is_default == 1,
                )
            )
            default_ws = result.scalar_one_or_none()
            if default_ws:
                db.add(
                    WorkspaceMember(
                        workspace_id=default_ws.id,
                        user_id=user.id,
                        role="member",
                    )
                )

        # Create the UserIdentity link.
        identity = UserIdentity(
            user_id=user.id,
            provider_id=provider_id,
            provider_subject=sub,
            email_at_provider=email or None,
        )
        db.add(identity)
        await db.commit()
        await db.refresh(user)

    # Update last_login.
    user.last_login = datetime.now(timezone.utc)
    await db.commit()

    # Issue JWT.
    jwt_token = create_jwt({
        "id": user.id,
        "tenant_id": user.tenant_id,
        "email": user.email,
        "role": user.role,
    })

    # Determine post-login redirect (frontend path stored in cookie).
    post_redirect = request.cookies.get("sso_redirect", "/")

    # Clear SSO cookies.
    resp = RedirectResponse(
        url=f"{frontend_callback}#token={jwt_token}&redirect={post_redirect}",
        status_code=302,
    )
    resp.delete_cookie(settings.sso_state_cookie_name)
    resp.delete_cookie("sso_redirect")
    return resp


@router.post("/api/v1/auth/logout")
async def logout():
    return {"status": "ok"}
