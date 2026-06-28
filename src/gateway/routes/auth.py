from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.gateway.auth.jwt import create_jwt
from src.gateway.auth.password import hash_password, verify_password
from src.gateway.auth.oidc import get_authorize_url
from src.infra.db.models import Tenant, User
from src.infra.db.session import get_db

router = APIRouter()


class RegisterRequest(BaseModel):
    email: str
    password: str
    name: str


class LoginRequest(BaseModel):
    email: str
    password: str


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

    user = User(
        tenant_id=tenant.id,
        email=body.email,
        name=body.name,
        role="member",
        auth_provider="builtin",
        hashed_password=hash_password(body.password),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    token = create_jwt({
        "id": user.id,
        "tenant_id": user.tenant_id,
        "email": user.email,
        "role": user.role,
        "workspace_ids": user.workspace_ids,
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
                "workspace_ids": user.workspace_ids,
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

    token = create_jwt({
        "id": user.id,
        "tenant_id": user.tenant_id,
        "email": user.email,
        "role": user.role,
        "workspace_ids": user.workspace_ids,
    })
    return {
        "token": token,
        "user": {
            "id": user.id,
            "email": user.email,
            "name": user.name,
            "role": user.role,
            "workspace_ids": user.workspace_ids,
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
