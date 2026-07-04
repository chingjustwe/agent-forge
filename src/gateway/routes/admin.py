import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import sqlalchemy
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from src.gateway.auth.rbac import (
    get_workspace_member_role,
    require_tenant_role,
    require_workspace_role,
)
from src.gateway.email.sender import send_invite_email
from src.infra.db.models import (
    AgentConfig,
    InviteToken,
    Tenant,
    User,
    Workspace,
    WorkspaceMember,
    AuditLog as AuditLogModel,
)
from src.infra.db.session import get_db
from src.infra.settings import settings
from src.infra.telemetry.collector import TelemetryCollector
from src.utils.slugify import slugify, unique_slug

router = APIRouter()
admin_router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


# ─── Tenant admin only ───────────────────────────────────────────────────────


@admin_router.get("/tenants")
async def list_tenants(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_tenant_role("tenant_admin")),
):
    result = await db.execute(
        select(
            Tenant.id,
            Tenant.name,
            Tenant.domain,
            Tenant.created_at,
            Tenant.max_total_tokens_per_day,
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
            "max_total_tokens_per_day": r.max_total_tokens_per_day,
            "created_at": r.created_at.isoformat(),
        }
        for r in result.all()
    ]


@admin_router.put("/tenants/{tenant_id}")
async def update_tenant(
    tenant_id: str,
    body: dict,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_tenant_role("tenant_admin")),
):
    tenant = await db.get(Tenant, tenant_id)
    if not tenant:
        return JSONResponse(status_code=404, content={"error": {"code": "NOT_FOUND", "message": "Tenant not found"}})
    if "name" in body:
        tenant.name = body["name"]
    if "domain" in body:
        tenant.domain = body["domain"]
    if "settings" in body:
        tenant.settings = body["settings"]
    # P2-4: tenant-level token quota. 0 means unlimited.
    if "max_total_tokens_per_day" in body:
        tenant.max_total_tokens_per_day = body["max_total_tokens_per_day"]
    await db.commit()
    await db.refresh(tenant)
    return {
        "id": tenant.id,
        "name": tenant.name,
        "domain": tenant.domain,
        "settings": tenant.settings,
        "max_total_tokens_per_day": tenant.max_total_tokens_per_day,
        "created_at": tenant.created_at.isoformat(),
    }


# ─── Tenant quota (P2-4) ────────────────────────────────────────────────────


@admin_router.get("/tenants/{tenant_id}/quota")
async def get_tenant_quota(
    tenant_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_tenant_role("tenant_admin")),
):
    """View tenant-level daily token quota and aggregated usage for today."""
    from datetime import date as date_type
    from sqlalchemy import text as sa_text

    tenant = await db.get(Tenant, tenant_id)
    if not tenant:
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "NOT_FOUND", "message": "Tenant not found"}},
        )
    today = date_type.today().isoformat()
    tenant_tokens_used = (await db.execute(
        sa_text(
            "SELECT COALESCE(SUM(qu.tokens_used), 0) "
            "FROM quota_usage qu "
            "JOIN workspaces w ON qu.workspace_id = w.id "
            "WHERE w.tenant_id = :tenant_id AND qu.date = :today"
        ),
        {"tenant_id": tenant_id, "today": today},
    )).scalar() or 0
    return {
        "tenant_id": tenant.id,
        "max_total_tokens_per_day": tenant.max_total_tokens_per_day,
        "tenant_tokens_used": tenant_tokens_used,
    }


@admin_router.patch("/tenants/{tenant_id}/quota")
async def update_tenant_quota(
    tenant_id: str,
    body: dict,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_tenant_role("tenant_admin")),
):
    """Update tenant-level daily token quota (max_total_tokens_per_day).

    0 means unlimited. Only ``tenant_admin`` may invoke this.
    """
    tenant = await db.get(Tenant, tenant_id)
    if not tenant:
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "NOT_FOUND", "message": "Tenant not found"}},
        )
    if "max_total_tokens_per_day" in body:
        value = body["max_total_tokens_per_day"]
        if not isinstance(value, int) or value < 0:
            return JSONResponse(
                status_code=422,
                content={
                    "error": {
                        "code": "VALIDATION_ERROR",
                        "message": "max_total_tokens_per_day must be a non-negative integer",
                    }
                },
            )
        tenant.max_total_tokens_per_day = value
    await db.commit()
    await db.refresh(tenant)
    return {
        "tenant_id": tenant.id,
        "max_total_tokens_per_day": tenant.max_total_tokens_per_day,
    }


# ─── Users ───────────────────────────────────────────────────────────────────


@admin_router.get("/users")
async def list_users(
    request: Request,
    search: Optional[str] = Query(None),
    role: Optional[str] = Query(None),
    workspace_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_tenant_role("tenant_admin")),
):
    query = select(User).where(User.archived == 0)
    if search:
        query = query.where(or_(User.email.ilike(f"%{search}%"), User.name.ilike(f"%{search}%")))
    if role:
        query = query.where(User.role == role)
    if workspace_id:
        # Filter by membership in WorkspaceMember table
        query = query.join(
            WorkspaceMember, WorkspaceMember.user_id == User.id
        ).where(WorkspaceMember.workspace_id == workspace_id)
    result = await db.execute(query)
    users = result.scalars().all()

    # Build workspace_ids per user from WorkspaceMember rows
    out = []
    for u in users:
        wm_rows = await db.execute(
            select(WorkspaceMember.workspace_id).where(WorkspaceMember.user_id == u.id)
        )
        ws_ids = [r[0] for r in wm_rows.all()]
        out.append({
            "id": u.id,
            "email": u.email,
            "name": u.name,
            "role": u.role,
            "workspaces": ws_ids,
            "last_login": u.last_login.isoformat() if u.last_login else None,
            "created_at": u.created_at.isoformat(),
        })
    return out


@admin_router.put("/users/{user_id}")
async def update_user(
    user_id: str,
    body: dict,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_tenant_role("tenant_admin")),
):
    user = await db.get(User, user_id)
    if not user:
        return JSONResponse(status_code=404, content={"error": {"code": "NOT_FOUND", "message": "User not found"}})
    if "role" in body:
        user.role = body["role"]
    if "workspace_ids" in body:
        # Sync WorkspaceMember rows: add new, remove missing
        desired = set(body["workspace_ids"])
        existing_rows = await db.execute(
            select(WorkspaceMember).where(WorkspaceMember.user_id == user_id)
        )
        existing = {wm.workspace_id: wm for wm in existing_rows.scalars().all()}
        for ws_id in desired - set(existing.keys()):
            db.add(WorkspaceMember(workspace_id=ws_id, user_id=user_id, role="member"))
        for ws_id, wm in existing.items():
            if ws_id not in desired:
                await db.delete(wm)
    await db.commit()
    await db.refresh(user)

    wm_rows = await db.execute(
        select(WorkspaceMember.workspace_id).where(WorkspaceMember.user_id == user.id)
    )
    ws_ids = [r[0] for r in wm_rows.all()]
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "role": user.role,
        "workspaces": ws_ids,
        "created_at": user.created_at.isoformat(),
    }


@admin_router.delete("/users/{user_id}", status_code=204)
async def delete_user(
    user_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_tenant_role("tenant_admin")),
):
    user = await db.get(User, user_id)
    if not user:
        return JSONResponse(status_code=404, content={"error": {"code": "NOT_FOUND", "message": "User not found"}})
    user.archived = 1
    await db.commit()


@admin_router.post("/users/invite", status_code=201)
async def invite_user(
    body: dict,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_tenant_role("tenant_admin")),
):
    email = body.get("email", "")

    # Check for non-archived user with this email
    existing = await db.execute(select(User).where(User.email == email, User.archived == 0))
    if existing.scalar_one_or_none():
        return JSONResponse(status_code=409, content={"error": {"code": "CONFLICT", "message": "User already exists"}})

    workspace_id = body.get("workspace_id")
    # If there's an archived user, re-activate it instead of creating a new one
    archived = await db.execute(select(User).where(User.email == email, User.archived == 1))
    archived_user = archived.scalar_one_or_none()
    if archived_user:
        user = archived_user
        user.archived = 0
        user.role = body.get("role", "member")
        user.hashed_password = None
        user.name = email.split("@")[0]
        if workspace_id:
            # Re-activate membership in this workspace
            existing_membership = await db.get(WorkspaceMember, (workspace_id, user.id))
            if not existing_membership:
                db.add(
                    WorkspaceMember(
                        workspace_id=workspace_id,
                        user_id=user.id,
                        role="member",
                    )
                )
        # Remove any previous unused invite tokens
        await db.execute(
            sqlalchemy.text("DELETE FROM invite_tokens WHERE user_id = :uid AND used_at IS NULL"),
            {"uid": user.id},
        )
    else:
        # Create new user (no password yet — will be set on invite acceptance)
        user = User(
            tenant_id=request.state.user["tenant_id"],
            email=email,
            name=email.split("@")[0],
            role=body.get("role", "member"),
            auth_provider="builtin",
        )
        db.add(user)
        await db.flush()
        if workspace_id:
            db.add(
                WorkspaceMember(
                    workspace_id=workspace_id,
                    user_id=user.id,
                    role="member",
                )
            )

    await db.flush()

    # Generate invite token (valid for 7 days)
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    invite = InviteToken(
        user_id=user.id,
        token_hash=token_hash,
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    db.add(invite)
    await db.commit()
    await db.refresh(user)

    # Send invitation email
    base_url = settings.app_base_url.rstrip("/")
    invite_url = f"{base_url}/invite?token={raw_token}"
    send_invite_email(email=email, invite_url=invite_url)

    # Build workspace_ids from WorkspaceMember for the response
    wm_rows = await db.execute(
        select(WorkspaceMember.workspace_id).where(WorkspaceMember.user_id == user.id)
    )
    ws_ids = [r[0] for r in wm_rows.all()]

    return {
        "id": user.id,
        "email": user.email,
        "role": user.role,
        "workspace_ids": ws_ids,
        "created_at": user.created_at.isoformat(),
    }


# ─── Workspaces ──────────────────────────────────────────────────────────────


@admin_router.get("/workspaces")
async def list_workspaces(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_tenant_role("tenant_admin")),
):
    result = await db.execute(select(Workspace).where(Workspace.archived == 0))
    workspaces = result.scalars().all()

    ws_ids = [w.id for w in workspaces]

    # Count members per workspace in batch via WorkspaceMember
    member_counts: dict[str, int] = {}
    agent_counts: dict[str, int] = {}
    if ws_ids:
        cnt_result = await db.execute(
            select(WorkspaceMember.workspace_id, func.count())
            .where(WorkspaceMember.workspace_id.in_(ws_ids))
            .group_by(WorkspaceMember.workspace_id)
        )
        for ws_id, cnt in cnt_result.all():
            member_counts[ws_id] = cnt

        # P2-2: batch-count agent configs per workspace (real values,
        # replacing the previous `0  # TODO: P2-2` stub).
        agent_cnt_result = await db.execute(
            select(AgentConfig.workspace_id, func.count())
            .where(AgentConfig.workspace_id.in_(ws_ids))
            .group_by(AgentConfig.workspace_id)
        )
        for ws_id, cnt in agent_cnt_result.all():
            agent_counts[ws_id] = cnt

        # Batch-lookup each workspace's workspace_owner email
        owner_result = await db.execute(
            select(WorkspaceMember.workspace_id, User.email)
            .join(User, User.id == WorkspaceMember.user_id)
            .where(
                WorkspaceMember.role == "workspace_owner",
                WorkspaceMember.workspace_id.in_(ws_ids),
            )
        )
        owners: dict[str, str] = {
            ws_id: email for ws_id, email in owner_result.all()
        }
    else:
        owners = {}

    return [
        {
            "id": ws.id,
            "name": ws.name,
            "slug": ws.slug,
            "description": ws.description,
            "icon": ws.icon,
            "owner_id": ws.owner_id,
            "member_count": member_counts.get(ws.id, 0),
            "agent_count": agent_counts.get(ws.id, 0),
            "owner": owners.get(ws.id, ""),
            "is_default": bool(ws.is_default),
            "created_at": ws.created_at.isoformat() if ws.created_at else None,
            "updated_at": ws.updated_at.isoformat() if ws.updated_at else None,
        }
        for ws in workspaces
    ]


class CreateWorkspaceBody(BaseModel):
    name: str
    slug: str | None = None
    description: str | None = None
    icon: str | None = None


@admin_router.post("/workspaces", status_code=201)
async def admin_create_workspace(
    body: CreateWorkspaceBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_tenant_role("tenant_admin")),
):
    tenant_id = request.state.user.get("tenant_id", "")
    user_id = request.state.user.get("sub") or request.state.user.get("id")
    base_slug = slugify(body.slug) if body.slug else slugify(body.name)
    final_slug = await unique_slug(db, tenant_id, base_slug)
    ws = Workspace(
        tenant_id=tenant_id,
        name=body.name,
        slug=final_slug,
        description=body.description,
        icon=body.icon,
        owner_id=user_id,
    )
    db.add(ws)
    await db.commit()
    await db.refresh(ws)
    return {
        "id": ws.id,
        "name": ws.name,
        "slug": ws.slug,
        "description": ws.description,
        "icon": ws.icon,
        "owner_id": ws.owner_id,
        "member_count": 0,
        "agent_count": 0,  # new workspace has no agents yet
        "owner": "",
        "created_at": ws.created_at.isoformat() if ws.created_at else None,
        "updated_at": ws.updated_at.isoformat() if ws.updated_at else None,
    }


@admin_router.put("/workspaces/{workspace_id}")
async def update_workspace(
    workspace_id: str,
    body: dict,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _ctx=Depends(require_workspace_role("workspace_id", "workspace_owner")),
):
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
    if "description" in body:
        ws.description = body["description"]
    if "icon" in body:
        ws.icon = body["icon"]
    if "slug" in body:
        new_slug = slugify(body["slug"]) if body["slug"] else None
        if new_slug and new_slug != ws.slug:
            # Enforce tenant-local uniqueness; reject with 409 on conflict.
            conflict = await db.execute(
                select(Workspace.id).where(
                    Workspace.tenant_id == ws.tenant_id,
                    Workspace.slug == new_slug,
                    Workspace.id != ws.id,
                )
            )
            if conflict.scalar_one_or_none() is not None:
                return JSONResponse(
                    status_code=409,
                    content={"error": {"code": "SLUG_CONFLICT", "message": "Slug already in use within this tenant"}},
                )
            ws.slug = new_slug
    await db.commit()
    await db.refresh(ws)
    return {
        "id": ws.id,
        "name": ws.name,
        "slug": ws.slug,
        "description": ws.description,
        "icon": ws.icon,
        "owner_id": ws.owner_id,
        "settings": ws.settings,
        "max_tokens_per_day": ws.max_tokens_per_day,
        "max_cost_per_month": ws.max_cost_per_month,
        "created_at": ws.created_at.isoformat() if ws.created_at else None,
        "updated_at": ws.updated_at.isoformat() if ws.updated_at else None,
    }


@admin_router.delete("/workspaces/{workspace_id}", status_code=204)
async def archive_workspace(
    workspace_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _ctx=Depends(require_workspace_role("workspace_id", "workspace_owner")),
):
    ws = await db.get(Workspace, workspace_id)
    if not ws:
        return JSONResponse(status_code=404, content={"error": {"code": "NOT_FOUND", "message": "Workspace not found"}})

    # Prevent archiving the default workspace
    if ws.is_default:
        return JSONResponse(
            status_code=409,
            content={"error": {"code": "CANNOT_ARCHIVE_DEFAULT", "message": "Cannot archive the default workspace"}},
        )

    # Prevent archiving the last active workspace for this tenant
    remaining = await db.execute(
        select(func.count(Workspace.id)).where(
            Workspace.tenant_id == ws.tenant_id,
            Workspace.archived == 0,
            Workspace.id != workspace_id,
        )
    )
    if remaining.scalar() == 0:
        return JSONResponse(
            status_code=409,
            content={"error": {"code": "LAST_WORKSPACE", "message": "Cannot archive the last active workspace"}},
        )

    # Prevent archiving a workspace that still has members
    member_count_result = await db.execute(
        select(func.count()).select_from(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id
        )
    )
    if member_count_result.scalar() > 0:
        return JSONResponse(
            status_code=409,
            content={"error": {"code": "WORKSPACE_IN_USE", "message": "Cannot archive a workspace that still has members. Remove all members first."}},
        )

    ws.archived = 1
    await db.commit()


# ─── Usage ───────────────────────────────────────────────────────────────────


@admin_router.get("/usage")
async def get_usage(
    request: Request,
    tenant_id: Optional[str] = Query(None),
    since: Optional[str] = Query(None),
    until: Optional[str] = Query(None),
    _user=Depends(require_tenant_role("tenant_admin")),
):
    tid = tenant_id or request.state.user.get("tenant_id")
    since_dt = datetime.fromisoformat(since) if since else None
    until_dt = datetime.fromisoformat(until) if until else None
    collector = TelemetryCollector()
    return await collector.get_tenant_usage(tid, since_dt, until_dt)


# ─── Quota ───────────────────────────────────────────────────────────────────


@admin_router.put("/workspaces/{workspace_id}/quota")
async def update_quota(
    workspace_id: str,
    body: dict,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _ctx=Depends(
        require_workspace_role("workspace_id", "workspace_admin", "workspace_owner")
    ),
):
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
    _user=Depends(require_tenant_role("tenant_admin")),
):
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
    user_id = user.get("sub") or user.get("id", "")
    result = await db.execute(select(User).where(User.id == user_id))
    db_user = result.scalar_one_or_none()
    if not db_user:
        return JSONResponse(status_code=404, content={"error": {"code": "NOT_FOUND", "message": "User not found"}})
    # Build workspace_ids from WorkspaceMember rows
    wm_rows = await db.execute(
        select(WorkspaceMember.workspace_id).where(WorkspaceMember.user_id == db_user.id)
    )
    ws_ids = [r[0] for r in wm_rows.all()]
    return {
        "id": db_user.id,
        "email": db_user.email,
        "name": db_user.name,
        "role": db_user.role,
        "workspace_ids": ws_ids,
    }
