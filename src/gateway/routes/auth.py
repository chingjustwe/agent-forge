import hashlib
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.gateway.auth.jwt import create_jwt
from src.gateway.auth.password import hash_password, verify_password
from src.gateway.auth.oidc import get_authorize_url
from src.infra.db.models import InviteToken, Tenant, User, Workspace, WorkspaceMember
from src.infra.db.session import get_db

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
async def oidc_login(provider: str = "google", redirect: str = "http://localhost:5173/callback"):
    try:
        uri, state = get_authorize_url(provider, redirect)
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url=uri, status_code=302)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": {"code": "BAD_REQUEST", "message": str(e)}})


@router.get("/api/v1/auth/callback")
async def oidc_callback(code: str, state: str = "", provider: str = "google", redirect: str = "http://localhost:5173/callback"):
    return JSONResponse(
        status_code=200,
        content={"message": "OIDC callback received. Configure provider credentials for full flow."},
    )


@router.post("/api/v1/auth/logout")
async def logout():
    return {"status": "ok"}
