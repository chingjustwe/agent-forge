from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from src.gateway.auth.roles import has_permission
from src.infra.db.models import Tenant, User, Workspace, AuditLog as AuditLogModel
from src.infra.db.session import get_db
from src.infra.telemetry.collector import TelemetryCollector

router = APIRouter()
admin_router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


def _require_role(request: Request, min_role: str):
    user = getattr(request.state, "user", None)
    if not user:
        return JSONResponse(status_code=401, content={"error": {"code": "UNAUTHORIZED", "message": "Not authenticated"}})
    if not has_permission(user.get("role", "viewer"), min_role):
        return JSONResponse(status_code=403, content={"error": {"code": "FORBIDDEN", "message": "Insufficient permissions"}})
    return None


# ─── Tenant admin only ───────────────────────────────────────────────────────


@admin_router.get("/tenants")
async def list_tenants(request: Request, db: AsyncSession = Depends(get_db)):
    err = _require_role(request, "tenant_admin")
    if err:
        return err
    result = await db.execute(
        select(
            Tenant.id,
            Tenant.name,
            Tenant.domain,
            Tenant.created_at,
            func.count(func.distinct(User.id)).label("user_count"),
            func.count(func.distinct(Workspace.id)).label("workspace_count"),
        )
        .outerjoin(User, User.tenant_id == Tenant.id)
        .outerjoin(Workspace, Workspace.tenant_id == Tenant.id)
        .group_by(Tenant.id)
    )
    return [
        {
            "id": r.id,
            "name": r.name,
            "domain": r.domain,
            "user_count": r.user_count,
            "workspace_count": r.workspace_count,
            "created_at": r.created_at.isoformat(),
        }
        for r in result.all()
    ]


@admin_router.put("/tenants/{tenant_id}")
async def update_tenant(tenant_id: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    err = _require_role(request, "tenant_admin")
    if err:
        return err
    tenant = await db.get(Tenant, tenant_id)
    if not tenant:
        return JSONResponse(status_code=404, content={"error": {"code": "NOT_FOUND", "message": "Tenant not found"}})
    if "name" in body:
        tenant.name = body["name"]
    if "domain" in body:
        tenant.domain = body["domain"]
    if "settings" in body:
        tenant.settings = body["settings"]
    await db.commit()
    await db.refresh(tenant)
    return {
        "id": tenant.id,
        "name": tenant.name,
        "domain": tenant.domain,
        "settings": tenant.settings,
        "created_at": tenant.created_at.isoformat(),
    }


# ─── Users ───────────────────────────────────────────────────────────────────


@admin_router.get("/users")
async def list_users(
    request: Request,
    search: Optional[str] = Query(None),
    role: Optional[str] = Query(None),
    workspace_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    err = _require_role(request, "tenant_admin")
    if err:
        return err
    query = select(User).where(User.archived == 0)
    if search:
        query = query.where(or_(User.email.ilike(f"%{search}%"), User.name.ilike(f"%{search}%")))
    if role:
        query = query.where(User.role == role)
    if workspace_id:
        query = query.where(User.workspace_ids.contains(workspace_id))
    result = await db.execute(query)
    return [
        {
            "id": u.id,
            "email": u.email,
            "name": u.name,
            "role": u.role,
            "workspaces": u.workspace_ids,
            "last_login": u.last_login.isoformat() if u.last_login else None,
            "created_at": u.created_at.isoformat(),
        }
        for u in result.scalars().all()
    ]


@admin_router.put("/users/{user_id}")
async def update_user(user_id: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    err = _require_role(request, "tenant_admin")
    if err:
        return err
    user = await db.get(User, user_id)
    if not user:
        return JSONResponse(status_code=404, content={"error": {"code": "NOT_FOUND", "message": "User not found"}})
    if "role" in body:
        user.role = body["role"]
    if "workspace_ids" in body:
        user.workspace_ids = body["workspace_ids"]
    await db.commit()
    await db.refresh(user)
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "role": user.role,
        "workspaces": user.workspace_ids,
        "created_at": user.created_at.isoformat(),
    }


@admin_router.delete("/users/{user_id}", status_code=204)
async def delete_user(user_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    err = _require_role(request, "tenant_admin")
    if err:
        return err
    user = await db.get(User, user_id)
    if not user:
        return JSONResponse(status_code=404, content={"error": {"code": "NOT_FOUND", "message": "User not found"}})
    user.archived = 1
    await db.commit()


@admin_router.post("/users/invite", status_code=201)
async def invite_user(body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    err = _require_role(request, "tenant_admin")
    if err:
        return err
    email = body.get("email", "")
    existing = await db.execute(select(User).where(User.email == email))
    if existing.scalar_one_or_none():
        return JSONResponse(status_code=409, content={"error": {"code": "CONFLICT", "message": "User already exists"}})
    user = User(
        tenant_id=request.state.user["tenant_id"],
        email=email,
        name=email.split("@")[0],
        role=body.get("role", "member"),
        workspace_ids=[body["workspace_id"]] if body.get("workspace_id") else [],
        auth_provider="builtin",
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return {
        "id": user.id,
        "email": user.email,
        "role": user.role,
        "workspace_ids": user.workspace_ids,
        "created_at": user.created_at.isoformat(),
    }


# ─── Workspaces ──────────────────────────────────────────────────────────────


@admin_router.get("/workspaces")
async def list_workspaces(request: Request, db: AsyncSession = Depends(get_db)):
    err = _require_role(request, "workspace_owner")
    if err:
        return err
    result = await db.execute(select(Workspace).where(Workspace.archived == 0))
    return [
        {
            "id": ws.id,
            "name": ws.name,
            "member_count": 0,
            "agent_count": 0,
            "owner": "",
            "created_at": ws.created_at.isoformat(),
        }
        for ws in result.scalars().all()
    ]


@admin_router.put("/workspaces/{workspace_id}")
async def update_workspace(workspace_id: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    err = _require_role(request, "workspace_owner")
    if err:
        return err
    ws = await db.get(Workspace, workspace_id)
    if not ws:
        return JSONResponse(status_code=404, content={"error": {"code": "NOT_FOUND", "message": "Workspace not found"}})
    if "name" in body:
        ws.name = body["name"]
    if "settings" in body:
        ws.settings = body["settings"]
    if "max_tokens_per_day" in body:
        ws.max_tokens_per_day = body["max_tokens_per_day"]
    if "max_cost_per_month" in body:
        ws.max_cost_per_month = body["max_cost_per_month"]
    await db.commit()
    await db.refresh(ws)
    return {
        "id": ws.id,
        "name": ws.name,
        "settings": ws.settings,
        "max_tokens_per_day": ws.max_tokens_per_day,
        "max_cost_per_month": ws.max_cost_per_month,
        "created_at": ws.created_at.isoformat(),
    }


@admin_router.delete("/workspaces/{workspace_id}", status_code=204)
async def archive_workspace(workspace_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    err = _require_role(request, "workspace_owner")
    if err:
        return err
    ws = await db.get(Workspace, workspace_id)
    if not ws:
        return JSONResponse(status_code=404, content={"error": {"code": "NOT_FOUND", "message": "Workspace not found"}})
    ws.archived = 1
    await db.commit()


# ─── Usage ───────────────────────────────────────────────────────────────────


@admin_router.get("/usage")
async def get_usage(
    request: Request,
    tenant_id: Optional[str] = Query(None),
    since: Optional[str] = Query(None),
    until: Optional[str] = Query(None),
):
    err = _require_role(request, "workspace_owner")
    if err:
        return err
    tid = tenant_id or request.state.user.get("tenant_id")
    since_dt = datetime.fromisoformat(since) if since else None
    until_dt = datetime.fromisoformat(until) if until else None
    collector = TelemetryCollector()
    return await collector.get_tenant_usage(tid, since_dt, until_dt)


# ─── Quota ───────────────────────────────────────────────────────────────────


@admin_router.put("/workspaces/{workspace_id}/quota")
async def update_quota(workspace_id: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    err = _require_role(request, "workspace_admin")
    if err:
        return err
    ws = await db.get(Workspace, workspace_id)
    if not ws:
        return JSONResponse(status_code=404, content={"error": {"code": "NOT_FOUND", "message": "Workspace not found"}})
    if "max_tokens_per_day" in body:
        ws.max_tokens_per_day = body["max_tokens_per_day"]
    if "max_cost_per_month" in body:
        ws.max_cost_per_month = body["max_cost_per_month"]
    await db.commit()
    return {
        "max_tokens_per_day": ws.max_tokens_per_day,
        "max_cost_per_month": ws.max_cost_per_month,
    }


# ─── Audit Log ───────────────────────────────────────────────────────────────


@admin_router.get("/audit")
async def list_audit_logs(
    request: Request,
    tenant_id: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    user_id: Optional[str] = Query(None),
    since: Optional[str] = Query(None),
    until: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    err = _require_role(request, "tenant_admin")
    if err:
        return err
    query = select(AuditLogModel)
    tid = tenant_id or request.state.user.get("tenant_id")
    query = query.where(AuditLogModel.tenant_id == tid)
    if action:
        query = query.where(AuditLogModel.action == action)
    if user_id:
        query = query.where(AuditLogModel.user_id == user_id)
    if since:
        query = query.where(AuditLogModel.created_at >= datetime.fromisoformat(since))
    if until:
        query = query.where(AuditLogModel.created_at <= datetime.fromisoformat(until))
    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar()
    query = query.order_by(AuditLogModel.created_at.desc()).offset(offset).limit(limit)
    items = (await db.execute(query)).scalars().all()
    return {
        "items": [
            {
                "id": a.id,
                "action": a.action,
                "user_id": a.user_id,
                "target_type": a.target_type,
                "target_id": a.target_id,
                "details": a.details,
                "ip_address": a.ip_address,
                "created_at": a.created_at.isoformat(),
            }
            for a in items
        ],
        "total": total,
    }


# ─── Self route (outside admin prefix) ───────────────────────────────────────


@router.get("/api/v1/users/me")
async def get_current_user(request: Request, db: AsyncSession = Depends(get_db)):
    user = getattr(request.state, "user", None)
    if not user:
        return JSONResponse(status_code=401, content={"error": {"code": "UNAUTHORIZED", "message": "Not authenticated"}})
    result = await db.execute(select(User).where(User.id == user.get("sub", "")))
    db_user = result.scalar_one_or_none()
    if not db_user:
        return JSONResponse(status_code=404, content={"error": {"code": "NOT_FOUND", "message": "User not found"}})
    return {
        "id": db_user.id,
        "email": db_user.email,
        "name": db_user.name,
        "role": db_user.role,
        "workspaces": db_user.workspace_ids or [],
    }
