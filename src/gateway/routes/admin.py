import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import sqlalchemy
from sqlalchemy import select, func, or_, delete, text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from src.gateway.auth.rbac import get_admin_workspace_ids, require_permission
from src.gateway.email.sender import send_invite_email
from src.infra.db.models import (
    AgentConfig,
    ApiKey,
    ChatMessage,
    ChatSession,
    InviteToken,
    OTelSettings,
    QuotaUsage,
    RequestLog,
    SsoProvider,
    Tenant,
    User,
    Workspace,
    WorkspaceInvitation,
    WorkspaceMember,
    AuditLog as AuditLogModel,
)
from src.infra.db.session import get_db
from src.infra.settings import settings
from src.infra.telemetry.collector import TelemetryCollector
from src.utils.slugify import slugify, unique_slug

logger = logging.getLogger(__name__)

router = APIRouter()
admin_router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


# ─── Tenant admin only ───────────────────────────────────────────────────────


@admin_router.get("/tenants")
async def list_tenants(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_permission("admin:tenant:write")),
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
    _user=Depends(require_permission("admin:tenant:write")),
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
    _user=Depends(require_permission("admin:tenant:write")),
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
    _user=Depends(require_permission("admin:tenant:write")),
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
    _user=Depends(require_permission("admin:users:read")),
):
    user = request.state.user
    admin_ws_ids = await get_admin_workspace_ids(user, db)
    query = select(User).where(User.archived == 0, User.hashed_password.isnot(None))
    if admin_ws_ids is not None:
        # workspace_admin: only see users in their workspaces
        member_subq = select(WorkspaceMember.user_id).where(
            WorkspaceMember.workspace_id.in_(admin_ws_ids)
        ).subquery()
        query = query.where(User.id.in_(select(member_subq.c.user_id)))
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

    # Build workspace names per user from WorkspaceMember rows
    user_ids = [u.id for u in users]
    wm_name_map: dict[str, list[str]] = {uid: [] for uid in user_ids}
    if user_ids:
        wm_rows = await db.execute(
            select(WorkspaceMember.user_id, Workspace.name)
            .select_from(WorkspaceMember)
            .join(Workspace, Workspace.id == WorkspaceMember.workspace_id)
            .where(WorkspaceMember.user_id.in_(user_ids))
        )
        for user_id, ws_name in wm_rows.all():
            wm_name_map[user_id].append(ws_name)

    out = []
    for u in users:
        out.append({
            "id": u.id,
            "email": u.email,
            "name": u.name,
            "role": u.role,
            "workspaces": wm_name_map.get(u.id, []),
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
    _user=Depends(require_permission("admin:users:write")),
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
        select(Workspace.name)
        .select_from(WorkspaceMember)
        .join(Workspace, Workspace.id == WorkspaceMember.workspace_id)
        .where(WorkspaceMember.user_id == user.id)
    )
    ws_names = [r[0] for r in wm_rows.all()]
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "role": user.role,
        "workspaces": ws_names,
        "created_at": user.created_at.isoformat(),
    }


@admin_router.delete("/users/{user_id}", status_code=204)
async def delete_user(
    user_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_permission("admin:users:write")),
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
    _user=Depends(require_permission("admin:users:write")),
):
    email = body.get("email", "")
    invited_role = body.get("role", "member")
    workspace_id = body.get("workspace_id")
    expires_in_days = body.get("expires_in_days", 7)
    if not isinstance(expires_in_days, int) or expires_in_days < 1 or expires_in_days > 365:
        expires_in_days = 7

    # Check for non-archived user with this email
    existing = await db.execute(select(User).where(User.email == email, User.archived == 0))
    if existing.scalar_one_or_none():
        return JSONResponse(status_code=409, content={"error": {"code": "CONFLICT", "message": "User already exists"}})

    inviter_id = _user.get("sub") or _user.get("id", "")
    tenant_id = request.state.user["tenant_id"]

    # If there's an archived user, re-activate it instead of creating a new one
    archived = await db.execute(select(User).where(User.email == email, User.archived == 1))
    archived_user = archived.scalar_one_or_none()
    if archived_user:
        user = archived_user
        user.archived = 0
        user.role = invited_role
        user.hashed_password = None
        user.name = email.split("@")[0]
        # Remove any previous unused invite tokens
        await db.execute(
            sa_text("DELETE FROM invite_tokens WHERE user_id = :uid AND used_at IS NULL"),
            {"uid": user.id},
        )
    else:
        # Create new user (no password yet — will be set on invite acceptance)
        user = User(
            tenant_id=tenant_id,
            email=email,
            name=email.split("@")[0],
            role=invited_role,
            auth_provider="builtin",
        )
        db.add(user)
        await db.flush()

    # ── Do NOT add WorkspaceMember immediately ──
    # The user is added to the workspace only when they accept the invitation.
    # Instead, create a WorkspaceInvitation (if workspace_id is provided).
    ws_name = None
    if workspace_id:
        ws = await db.get(Workspace, workspace_id)
        if ws and ws.tenant_id == tenant_id:
            ws_name = ws.name
            # Remove previous unaccepted invitations for this email+workspace
            await db.execute(
                delete(WorkspaceInvitation).where(
                    WorkspaceInvitation.workspace_id == workspace_id,
                    WorkspaceInvitation.email == email,
                    WorkspaceInvitation.accepted_at.is_(None),
                )
            )
            db.add(WorkspaceInvitation(
                workspace_id=workspace_id,
                email=email,
                role=invited_role,
                token=secrets.token_urlsafe(32),
                invited_by=inviter_id,
                expires_at=datetime.now(timezone.utc) + timedelta(days=expires_in_days),
            ))

    await db.flush()

    # Generate invite token
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    invite = InviteToken(
        user_id=user.id,
        token_hash=token_hash,
        expires_at=datetime.now(timezone.utc) + timedelta(days=expires_in_days),
    )
    db.add(invite)
    await db.commit()
    await db.refresh(user)

    # Send invitation email
    base_url = settings.app_base_url.rstrip("/")
    invite_url = f"{base_url}/invite?token={raw_token}"
    email_error = None
    try:
        send_invite_email(email=email, invite_url=invite_url, expires_in_days=expires_in_days)
    except Exception as exc:
        logger.error("Failed to send invite email to %s: %s", email, exc)
        email_error = str(exc)

    return {
        "id": user.id,
        "email": user.email,
        "role": user.role,
        "workspaces": [ws_name] if ws_name else [],
        "created_at": user.created_at.isoformat(),
        "invited_workspace_id": workspace_id,
        "invited_workspace_name": ws_name,
        "expires_at": invite.expires_at.isoformat(),
        "email_error": email_error,
    }


@admin_router.get("/pending-invitations")
async def list_pending_invitations(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_permission("admin:users:read")),
):
    """List pending invitations: users who haven't accepted yet."""
    rows = await db.execute(
        select(
            User.id,
            User.email,
            User.name,
            User.role,
            User.created_at,
            InviteToken.expires_at,
            WorkspaceInvitation.workspace_id,
            Workspace.name,
            WorkspaceInvitation.role,
        )
        .join(InviteToken, InviteToken.user_id == User.id)
        .outerjoin(WorkspaceInvitation, WorkspaceInvitation.email == User.email)
        .outerjoin(Workspace, Workspace.id == WorkspaceInvitation.workspace_id)
        .where(
            User.tenant_id == request.state.user["tenant_id"],
            User.archived == 0,
            User.hashed_password.is_(None),
            InviteToken.used_at.is_(None),
        )
        .order_by(User.created_at.desc())
    )
    out = []
    for r in rows.all():
        out.append({
            "user_id": r[0],
            "email": r[1],
            "name": r[2],
            "role": r[3],
            "invited_at": r[4].isoformat() if r[4] else None,
            "expires_at": r[5].isoformat() if r[5] else None,
            "workspace_id": r[6],
            "workspace_name": r[7],
            "invited_role": r[8],
        })
    return out


@admin_router.delete("/pending-invitations/{user_id}", status_code=204)
async def delete_pending_invitation(
    user_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_permission("admin:users:write")),
):
    """Delete a pending invitation and the associated unactivated user."""
    tenant_id = request.state.user["tenant_id"]
    user = await db.get(User, user_id)
    if not user or user.tenant_id != tenant_id:
        return JSONResponse(status_code=404, content={"error": {"code": "NOT_FOUND", "message": "User not found"}})
    if user.hashed_password is not None:
        return JSONResponse(status_code=400, content={"error": {"code": "ALREADY_ACTIVATED", "message": "User has already activated their account"}})

    # Delete WorkspaceInvitation records
    await db.execute(
        delete(WorkspaceInvitation).where(WorkspaceInvitation.email == user.email)
    )
    # Delete InviteToken records
    await db.execute(
        delete(InviteToken).where(InviteToken.user_id == user_id)
    )
    # Soft-delete the user
    user.archived = 1
    await db.commit()


@admin_router.get("/workspaces")
async def list_workspaces(
    request: Request,
    include_archived: bool = False,
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_permission("admin:workspaces:read")),
):
    user = request.state.user
    admin_ws_ids = await get_admin_workspace_ids(user, db)
    # P3-3 前端集成：默认隐藏 archived workspace；传 include_archived=true
    # 时返回全部（含 archived），用于 Purge 操作入口。
    query = select(Workspace)
    if admin_ws_ids is not None:
        query = query.where(Workspace.id.in_(admin_ws_ids))
    if not include_archived:
        query = query.where(Workspace.archived == 0)
    result = await db.execute(query)
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

        # Batch-lookup each workspace's workspace_admin email
        owner_result = await db.execute(
            select(WorkspaceMember.workspace_id, User.email)
            .join(User, User.id == WorkspaceMember.user_id)
            .where(
                WorkspaceMember.role == "workspace_admin",
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
            "archived": bool(ws.archived),
            "max_tokens_per_day": ws.max_tokens_per_day,
            "max_cost_per_day": ws.max_cost_per_day,
            "max_cost_per_month": ws.max_cost_per_month,
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
    max_tokens_per_day: int | None = None
    max_cost_per_day: float | None = None
    max_cost_per_month: float | None = None


@admin_router.post("/workspaces", status_code=201)
async def admin_create_workspace(
    body: CreateWorkspaceBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_permission("admin:workspaces:write")),
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
        max_tokens_per_day=body.max_tokens_per_day if body.max_tokens_per_day is not None else 1_000_000,
        max_cost_per_day=body.max_cost_per_day if body.max_cost_per_day is not None else 0.0,
        max_cost_per_month=body.max_cost_per_month if body.max_cost_per_month is not None else 0.0,
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
    _ctx=Depends(require_permission("admin:workspaces:write", workspace_id_param="workspace_id")),
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
    if "max_cost_per_day" in body:
        ws.max_cost_per_day = body["max_cost_per_day"]
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
    _ctx=Depends(require_permission("admin:workspaces:write", workspace_id_param="workspace_id")),
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


# ─── Workspace purge (P3-3) ─────────────────────────────────────────────────


@admin_router.delete("/workspaces/{workspace_id}/purge")
async def purge_workspace(
    workspace_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_permission("workspace:delete")),
):
    """Hard-delete an archived workspace AND all of its associated data.

    This is the "manual purge" counterpart to archive (soft-delete). Only
    workspaces that have already been archived may be purged (409 otherwise)
    — archiving requires the workspace to have no members, so the purge
    path mostly cleans up leftover sessions/agents/keys/logs.

    Two-step confirmation: the request body must carry
    ``{"purge_confirm": "<exact workspace name>"}`` to prevent accidental
    destructive deletes. A mismatch returns 400.

    Deletion order respects FK dependencies:
        chat_messages → chat_sessions → chat_session_shares (if present)
        → agent_configs → api_keys → workspace_invitations
        → workspace_members → otel_settings → quota_usage → request_logs
        → workspace itself.
    """
    user = request.state.user
    tenant_id = user.get("tenant_id", "")
    user_id = user.get("sub") or user.get("id", "")

    # Parse body — FastAPI doesn't auto-parse a JSON body on DELETE without
    # a Pydantic model, so read it manually (tolerate missing/invalid body).
    try:
        body = await request.json()
    except Exception:
        body = None
    if not isinstance(body, dict) or body.get("purge_confirm") is None:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": "BAD_REQUEST",
                    "message": "Body must include 'purge_confirm' matching the workspace name",
                }
            },
        )

    ws = await db.get(Workspace, workspace_id)
    if not ws:
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "NOT_FOUND", "message": "Workspace not found"}},
        )

    # Must be archived first.
    if not ws.archived:
        return JSONResponse(
            status_code=409,
            content={
                "error": {
                    "code": "NOT_ARCHIVED",
                    "message": "Workspace must be archived before it can be purged",
                }
            },
        )

    # Two-step confirmation: name must match exactly.
    if body.get("purge_confirm") != ws.name:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": "BAD_REQUEST",
                    "message": "purge_confirm does not match workspace name",
                }
            },
        )

    # Cascade delete associated rows (workspace_id-scoped). chat_messages
    # have no workspace_id FK, so delete via a subquery on chat_sessions.
    await db.execute(
        sa_text(
            "DELETE FROM chat_messages WHERE session_id IN "
            "(SELECT id FROM chat_sessions WHERE workspace_id = :wid)"
        ),
        {"wid": workspace_id},
    )
    # chat_session_shares (P3-5) — guarded so purge works on DBs that
    # haven't been migrated yet (the table is created idempotently by
    # _migrate_schema once P3-5 ships).
    try:
        await db.execute(
            sa_text(
                "DELETE FROM chat_session_shares WHERE session_id IN "
                "(SELECT id FROM chat_sessions WHERE workspace_id = :wid)"
            ),
            {"wid": workspace_id},
        )
    except Exception:
        # Table doesn't exist yet — nothing to delete. Roll back nothing;
        # the statement had no side effects.
        pass
    await db.execute(
        delete(ChatSession).where(ChatSession.workspace_id == workspace_id)
    )
    await db.execute(
        delete(AgentConfig).where(AgentConfig.workspace_id == workspace_id)
    )
    await db.execute(
        delete(ApiKey).where(ApiKey.workspace_id == workspace_id)
    )
    await db.execute(
        delete(WorkspaceInvitation).where(WorkspaceInvitation.workspace_id == workspace_id)
    )
    await db.execute(
        delete(WorkspaceMember).where(WorkspaceMember.workspace_id == workspace_id)
    )
    await db.execute(
        delete(OTelSettings).where(OTelSettings.workspace_id == workspace_id)
    )
    await db.execute(
        delete(QuotaUsage).where(QuotaUsage.workspace_id == workspace_id)
    )
    await db.execute(
        delete(RequestLog).where(RequestLog.workspace_id == workspace_id)
    )

    # Audit log BEFORE the workspace row goes (so workspace_id is still
    # resolvable at insert time; the log row itself is tenant-scoped and
    # survives the workspace delete).
    db.add(
        AuditLogModel(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            action="workspace.purge",
            target_type="workspace",
            target_id=workspace_id,
            details={"name": ws.name},
            ip_address=request.client.host if request.client else "",
        )
    )

    # Finally, drop the workspace row itself.
    await db.delete(ws)
    await db.commit()

    return JSONResponse(
        status_code=200,
        content={"purged": True, "workspace_id": workspace_id},
    )


# ─── Default workspace transfer (P3-4) ──────────────────────────────────────


@admin_router.post("/workspaces/{workspace_id}/set-default")
async def set_default_workspace(
    workspace_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_permission("admin:workspaces:write")),
):
    """Atomically transfer the ``is_default`` flag to ``workspace_id``.

    Within a single transaction:
    1. Clear ``is_default=1`` on every workspace in this tenant.
    2. Set ``is_default=1`` on the target workspace.

    The target must exist, belong to the caller's tenant, and not be
    archived. Newly registered users (via ``auth.register``) auto-join the
    default workspace, so changing the default changes where new users land.
    """
    user = request.state.user
    tenant_id = user.get("tenant_id", "")
    user_id = user.get("sub") or user.get("id", "")

    ws = await db.get(Workspace, workspace_id)
    if not ws or ws.tenant_id != tenant_id:
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "NOT_FOUND", "message": "Workspace not found"}},
        )

    if ws.archived:
        return JSONResponse(
            status_code=409,
            content={
                "error": {
                    "code": "CANNOT_SET_DEFAULT_ARCHIVED",
                    "message": "Cannot set an archived workspace as default",
                }
            },
        )

    # Atomic transfer: clear all defaults in this tenant, then set the new one.
    # Both statements run inside the same transaction (db.commit at the end).
    await db.execute(
        sa_text(
            "UPDATE workspaces SET is_default = 0 WHERE tenant_id = :tid AND is_default = 1"
        ),
        {"tid": tenant_id},
    )
    ws.is_default = 1

    db.add(
        AuditLogModel(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            action="workspace.set_default",
            target_type="workspace",
            target_id=workspace_id,
            details={"name": ws.name},
            ip_address=request.client.host if request.client else "",
        )
    )
    await db.commit()
    await db.refresh(ws)
    return JSONResponse(
        status_code=200,
        content={"id": ws.id, "is_default": bool(ws.is_default)},
    )


# ─── Usage ───────────────────────────────────────────────────────────────────


@admin_router.get("/requests")
async def list_admin_requests(
    request: Request,
    workspace_id: Optional[str] = Query(None),
    user_id: Optional[str] = Query(None),
    agent: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
    status: Optional[int] = Query(None),
    since: Optional[str] = Query(None),
    until: Optional[str] = Query(None),
    limit: int = Query(100),
    offset: int = Query(0),
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_permission("admin:audit:read")),
):
    """跨 workspace 查询 request_logs，供 Audit 页 Requests tab 使用。

    权限：admin:audit:read（workspace_admin / tenant_admin）
    - tenant_admin: 看整个 tenant 的所有 workspace 请求
    - workspace_admin: 只看自己管理的 workspace 请求
    """
    user = request.state.user
    tid = user.get("tenant_id", "")
    admin_ws_ids = await get_admin_workspace_ids(user, db)
    collector = TelemetryCollector()
    data = await collector.get_requests_admin(
        tid, admin_ws_ids,
        limit=limit, offset=offset,
        workspace_id=workspace_id, user_id=user_id, agent=agent,
        model=model, status=status, since=since, until=until,
    )
    return data


@admin_router.get("/usage")
async def get_usage(
    request: Request,
    tenant_id: Optional[str] = Query(None),
    workspace_id: Optional[str] = Query(None),
    since: Optional[str] = Query(None),
    until: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_permission("usage:read")),
):
    user = request.state.user
    admin_ws_ids = await get_admin_workspace_ids(user, db)
    tid = tenant_id or request.state.user.get("tenant_id")
    since_dt = datetime.fromisoformat(since) if since else None
    until_dt = datetime.fromisoformat(until) if until else None
    # If until is a bare date (e.g. "2026-07-11"), fromisoformat parses it
    # as 00:00:00, which excludes all records later that day. Extend to
    # end-of-day so the entire "until" date is included.
    if until_dt and until_dt.hour == 0 and until_dt.minute == 0 and until_dt.second == 0:
        until_dt = until_dt + timedelta(days=1) - timedelta(microseconds=1)
    # Attach UTC tzinfo so SQLAlchemy serializes consistently with
    # created_at (stored as UTC ISO strings). Without this, naive
    # datetimes serialize with a space separator ("2026-07-11 23:59:59")
    # which breaks string comparison against "2026-07-11T11:08...+00:00".
    if since_dt and since_dt.tzinfo is None:
        since_dt = since_dt.replace(tzinfo=timezone.utc)
    if until_dt and until_dt.tzinfo is None:
        until_dt = until_dt.replace(tzinfo=timezone.utc)
    collector = TelemetryCollector()
    result = await collector.get_tenant_usage(tid, since_dt, until_dt)

    # Permission scoping:
    # - workspace_id param provided (member/viewer single-workspace view):
    #       filter to just that workspace
    # - tenant_admin (admin_ws_ids is None): sees all
    # - workspace_admin (admin_ws_ids is a list): filter to managed workspaces
    # - member/viewer without workspace_id param: admin_ws_ids is [] (empty),
    #       which would return nothing — frontend should always pass workspace_id
    if workspace_id:
        filtered = [
            ws for ws in result.get("by_workspace", [])
            if ws["workspace_id"] == workspace_id
        ]
        result["by_workspace"] = filtered
        result["total_requests"] = sum(ws["total_requests"] for ws in filtered)
        result["input_tokens"] = sum(ws["input_tokens"] for ws in filtered)
        result["output_tokens"] = sum(ws["output_tokens"] for ws in filtered)
        result["total_tokens"] = sum(ws["total_tokens"] for ws in filtered)
        result["total_cost"] = sum(ws["total_cost"] for ws in filtered)
    elif admin_ws_ids is not None:
        # workspace_admin: only return usage for their workspaces
        filtered = [
            ws for ws in result.get("by_workspace", [])
            if ws["workspace_id"] in admin_ws_ids
        ]
        result["by_workspace"] = filtered
        result["total_requests"] = sum(ws["total_requests"] for ws in filtered)
        result["input_tokens"] = sum(ws["input_tokens"] for ws in filtered)
        result["output_tokens"] = sum(ws["output_tokens"] for ws in filtered)
        result["total_tokens"] = sum(ws["total_tokens"] for ws in filtered)
        result["total_cost"] = sum(ws["total_cost"] for ws in filtered)

    # Enrich by_workspace with workspace name + quota info so the frontend
    # can render quota columns alongside usage without N+1 API calls.
    ws_ids = [ws["workspace_id"] for ws in result.get("by_workspace", [])]
    if ws_ids:
        ws_rows = (await db.execute(
            select(Workspace.id, Workspace.name, Workspace.max_tokens_per_day, Workspace.max_cost_per_day, Workspace.max_cost_per_month)
            .where(Workspace.id.in_(ws_ids))
        )).all()
        ws_meta = {
            r.id: {
                "name": r.name,
                "max_tokens_per_day": r.max_tokens_per_day,
                "max_cost_per_day": r.max_cost_per_day,
                "max_cost_per_month": r.max_cost_per_month,
            }
            for r in ws_rows
        }
        # Fetch today's quota usage for all workspaces in one query
        today_str = datetime.now(timezone.utc).date().isoformat()
        quota_rows = (await db.execute(
            select(QuotaUsage.workspace_id, QuotaUsage.tokens_used, QuotaUsage.cost)
            .where(QuotaUsage.workspace_id.in_(ws_ids), QuotaUsage.date == today_str)
        )).all()
        quota_meta = {
            r.workspace_id: {"tokens_used_today": r.tokens_used, "cost_today": float(r.cost)}
            for r in quota_rows
        }
        for ws in result["by_workspace"]:
            wid = ws["workspace_id"]
            meta = ws_meta.get(wid, {})
            qm = quota_meta.get(wid, {"tokens_used_today": 0, "cost_today": 0.0})
            ws["name"] = meta.get("name", wid)
            ws["max_tokens_per_day"] = meta.get("max_tokens_per_day", 0)
            ws["max_cost_per_day"] = meta.get("max_cost_per_day", 0.0)
            ws["max_cost_per_month"] = meta.get("max_cost_per_month", 0.0)
            ws["tokens_used_today"] = qm["tokens_used_today"]
            ws["cost_today"] = qm["cost_today"]

    return result


# ─── Quota ───────────────────────────────────────────────────────────────────


@admin_router.put("/workspaces/{workspace_id}/quota")
async def update_quota(
    workspace_id: str,
    body: dict,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _ctx=Depends(
        require_permission("quota:write", workspace_id_param="workspace_id")
    ),
):
    ws = await db.get(Workspace, workspace_id)
    if not ws:
        return JSONResponse(status_code=404, content={"error": {"code": "NOT_FOUND", "message": "Workspace not found"}})
    if "max_tokens_per_day" in body:
        ws.max_tokens_per_day = body["max_tokens_per_day"]
    if "max_cost_per_day" in body:
        ws.max_cost_per_day = body["max_cost_per_day"]
    if "max_cost_per_month" in body:
        ws.max_cost_per_month = body["max_cost_per_month"]
    await db.commit()
    return {
        "max_tokens_per_day": ws.max_tokens_per_day,
        "max_cost_per_day": ws.max_cost_per_day,
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
    _user=Depends(require_permission("admin:audit:read")),
):
    user = request.state.user
    admin_ws_ids = await get_admin_workspace_ids(user, db)
    query = select(AuditLogModel)
    tid = tenant_id or request.state.user.get("tenant_id")
    query = query.where(AuditLogModel.tenant_id == tid)
    if admin_ws_ids is not None:
        query = query.where(AuditLogModel.workspace_id.in_(admin_ws_ids))
    if action:
        query = query.where(AuditLogModel.action == action)
    if user_id:
        query = query.where(AuditLogModel.user_id == user_id)
    if since:
        _since = datetime.fromisoformat(since)
        if _since.tzinfo is None:
            _since = _since.replace(tzinfo=timezone.utc)
        query = query.where(AuditLogModel.created_at >= _since.isoformat())
    if until:
        _until = datetime.fromisoformat(until)
        # Extend bare date to end-of-day so the entire "until" date is included
        if _until.hour == 0 and _until.minute == 0 and _until.second == 0:
            _until = _until + timedelta(days=1) - timedelta(microseconds=1)
        if _until.tzinfo is None:
            _until = _until.replace(tzinfo=timezone.utc)
        query = query.where(AuditLogModel.created_at <= _until.isoformat())
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


# ─── SSO Provider CRUD (Phase 1 — SSO authentication) ──────────────────────


class SsoProviderCreate(BaseModel):
    name: str
    slug: str
    provider_type: str  # google | microsoft | custom_oidc
    client_id: str
    client_secret: str
    tenant_id: str | None = None
    auto_provision: bool = True
    default_role: str = "member"
    enabled: bool = True
    ms_tenant: str | None = None
    scopes: list[str] | None = None
    authorize_url: str | None = None
    token_url: str | None = None
    userinfo_url: str | None = None
    issuer_url: str | None = None


class SsoProviderUpdate(BaseModel):
    name: str | None = None
    client_id: str | None = None
    client_secret: str | None = None
    auto_provision: bool | None = None
    default_role: str | None = None
    enabled: bool | None = None
    ms_tenant: str | None = None
    scopes: list[str] | None = None
    authorize_url: str | None = None
    token_url: str | None = None
    userinfo_url: str | None = None
    issuer_url: str | None = None


def _resolve_provider_urls(p: SsoProvider) -> None:
    """Auto-fill OIDC URLs for built-in provider types (google/microsoft).

    For ``custom_oidc``, the manually-supplied URLs are preserved.
    """
    from src.gateway.auth.oidc import PROVIDER_PRESETS, resolve_endpoints

    if p.provider_type in PROVIDER_PRESETS:
        endpoints = resolve_endpoints({
            "provider_type": p.provider_type,
            "ms_tenant": p.ms_tenant,
            "authorize_url": p.authorize_url,
            "token_url": p.token_url,
            "userinfo_url": p.userinfo_url,
            "issuer_url": p.issuer_url,
            "scopes": p.scopes,
        })
        p.authorize_url = endpoints["authorize_url"]
        p.token_url = endpoints["token_url"]
        p.userinfo_url = endpoints["userinfo_url"]
        if not p.issuer_url:
            p.issuer_url = endpoints.get("issuer_url")
        if not p.scopes or p.scopes == ["openid", "email", "profile"]:
            p.scopes = endpoints["scopes"]


def _sso_provider_to_dict(p: SsoProvider) -> dict:
    """Serialize an SsoProvider for API responses.

    Never includes ``client_secret``.
    """
    return {
        "id": p.id,
        "tenant_id": p.tenant_id,
        "name": p.name,
        "slug": p.slug,
        "provider_type": p.provider_type,
        "client_id": p.client_id,
        "auto_provision": bool(p.auto_provision),
        "default_role": p.default_role,
        "enabled": bool(p.enabled),
        "ms_tenant": p.ms_tenant,
        "scopes": p.scopes or ["openid", "email", "profile"],
        "authorize_url": p.authorize_url,
        "token_url": p.token_url,
        "userinfo_url": p.userinfo_url,
        "issuer_url": p.issuer_url,
        "jwks_uri": p.jwks_uri,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


@router.post("/api/v1/admin/sso-providers")
async def create_sso_provider(
    body: SsoProviderCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    ctx: dict = Depends(require_permission("admin:tenant:write")),
):
    """Create a new SSO provider configuration."""
    user = ctx
    tenant_id = body.tenant_id or user.get("tenant_id", "")

    # Check slug uniqueness within tenant scope.
    existing = await db.execute(
        select(SsoProvider).where(
            SsoProvider.tenant_id == tenant_id,
            SsoProvider.slug == body.slug,
        )
    )
    if existing.scalar_one_or_none():
        return JSONResponse(
            status_code=409,
            content={"error": {"code": "CONFLICT", "message": f"SSO provider with slug '{body.slug}' already exists"}},
        )

    # For custom_oidc: if manual URLs are missing but issuer_url is provided,
    # attempt OIDC Discovery to auto-fetch endpoints.
    discovered_jwks_uri: str | None = None
    if body.provider_type == "custom_oidc":
        if not (body.authorize_url and body.token_url and body.userinfo_url):
            if body.issuer_url:
                try:
                    from src.gateway.auth.oidc import discover_endpoints
                    discovered = await discover_endpoints(body.issuer_url)
                    body.authorize_url = body.authorize_url or discovered["authorize_url"]
                    body.token_url = body.token_url or discovered["token_url"]
                    body.userinfo_url = body.userinfo_url or discovered["userinfo_url"]
                    body.issuer_url = discovered["issuer_url"]
                    discovered_jwks_uri = discovered.get("jwks_uri")
                except Exception as exc:
                    return JSONResponse(
                        status_code=400,
                        content={"error": {"code": "BAD_REQUEST", "message": f"OIDC Discovery failed: {exc}"}},
                    )
            else:
                return JSONResponse(
                    status_code=400,
                    content={"error": {"code": "BAD_REQUEST", "message": "custom_oidc requires authorize_url/token_url/userinfo_url or issuer_url for discovery"}},
                )

    provider = SsoProvider(
        tenant_id=tenant_id,
        name=body.name,
        slug=body.slug,
        provider_type=body.provider_type,
        client_id=body.client_id,
        client_secret=body.client_secret,
        authorize_url=body.authorize_url,
        token_url=body.token_url,
        userinfo_url=body.userinfo_url,
        issuer_url=body.issuer_url,
        jwks_uri=discovered_jwks_uri,
        scopes=body.scopes or ["openid", "email", "profile"],
        ms_tenant=body.ms_tenant,
        auto_provision=1 if body.auto_provision else 0,
        default_role=body.default_role,
        enabled=1 if body.enabled else 0,
    )
    _resolve_provider_urls(provider)
    db.add(provider)
    await db.flush()

    user_id = user.get("sub") or user.get("id", "")
    db.add(
        AuditLogModel(
            tenant_id=tenant_id,
            workspace_id=None,
            user_id=user_id,
            action="sso_provider.create",
            target_type="sso_provider",
            target_id=provider.id,
            details={"name": provider.name, "provider_type": provider.provider_type},
            ip_address=request.client.host if request.client else "",
        )
    )
    await db.commit()
    await db.refresh(provider)
    return JSONResponse(status_code=201, content=_sso_provider_to_dict(provider))


@router.get("/api/v1/admin/sso-providers")
async def list_sso_providers(
    db: AsyncSession = Depends(get_db),
    ctx: dict = Depends(require_permission("admin:tenant:write")),
):
    """List SSO providers for the current tenant (+ global providers)."""
    user = ctx
    tenant_id = user.get("tenant_id", "")
    result = await db.execute(
        select(SsoProvider).where(
            or_(SsoProvider.tenant_id == tenant_id, SsoProvider.tenant_id.is_(None))
        ).order_by(SsoProvider.created_at)
    )
    providers = result.scalars().all()
    return [_sso_provider_to_dict(p) for p in providers]


@router.get("/api/v1/admin/sso-providers/{provider_id}")
async def get_sso_provider(
    provider_id: str,
    db: AsyncSession = Depends(get_db),
    ctx: dict = Depends(require_permission("admin:tenant:write")),
):
    """Get a single SSO provider by ID."""
    provider = await db.get(SsoProvider, provider_id)
    if not provider:
        return JSONResponse(status_code=404, content={"error": {"code": "NOT_FOUND", "message": "SSO provider not found"}})
    # Tenant isolation: non-tenant_admin cannot see other tenants' providers
    # (but can see global providers where tenant_id is NULL).
    user = ctx
    tenant_id = user.get("tenant_id", "")
    if provider.tenant_id is not None and provider.tenant_id != tenant_id and user.get("role") != "tenant_admin":
        return JSONResponse(status_code=404, content={"error": {"code": "NOT_FOUND", "message": "SSO provider not found"}})
    return _sso_provider_to_dict(provider)


@router.put("/api/v1/admin/sso-providers/{provider_id}")
async def update_sso_provider(
    provider_id: str,
    body: SsoProviderUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    ctx: dict = Depends(require_permission("admin:tenant:write")),
):
    """Update an SSO provider configuration."""
    provider = await db.get(SsoProvider, provider_id)
    if not provider:
        return JSONResponse(status_code=404, content={"error": {"code": "NOT_FOUND", "message": "SSO provider not found"}})
    user = ctx
    tenant_id = user.get("tenant_id", "")
    if provider.tenant_id is not None and provider.tenant_id != tenant_id and user.get("role") != "tenant_admin":
        return JSONResponse(status_code=404, content={"error": {"code": "NOT_FOUND", "message": "SSO provider not found"}})

    updates = body.model_dump(exclude_unset=True)
    for field, value in updates.items():
        if field == "auto_provision":
            provider.auto_provision = 1 if value else 0
        elif field == "enabled":
            provider.enabled = 1 if value else 0
        elif value is not None:
            setattr(provider, field, value)

    # Re-resolve URLs if provider_type-relevant fields changed.
    if provider.provider_type in ("google", "microsoft"):
        _resolve_provider_urls(provider)

    user_id = user.get("sub") or user.get("id", "")
    db.add(
        AuditLogModel(
            tenant_id=tenant_id,
            workspace_id=None,
            user_id=user_id,
            action="sso_provider.update",
            target_type="sso_provider",
            target_id=provider.id,
            details=updates,
            ip_address=request.client.host if request.client else "",
        )
    )
    await db.commit()
    await db.refresh(provider)
    return _sso_provider_to_dict(provider)


@router.delete("/api/v1/admin/sso-providers/{provider_id}")
async def delete_sso_provider(
    provider_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    ctx: dict = Depends(require_permission("admin:tenant:write")),
):
    """Delete an SSO provider configuration."""
    provider = await db.get(SsoProvider, provider_id)
    if not provider:
        return JSONResponse(status_code=404, content={"error": {"code": "NOT_FOUND", "message": "SSO provider not found"}})
    user = ctx
    tenant_id = user.get("tenant_id", "")
    if provider.tenant_id is not None and provider.tenant_id != tenant_id and user.get("role") != "tenant_admin":
        return JSONResponse(status_code=404, content={"error": {"code": "NOT_FOUND", "message": "SSO provider not found"}})

    await db.delete(provider)

    user_id = user.get("sub") or user.get("id", "")
    db.add(
        AuditLogModel(
            tenant_id=tenant_id,
            workspace_id=None,
            user_id=user_id,
            action="sso_provider.delete",
            target_type="sso_provider",
            target_id=provider_id,
            details={"name": provider.name},
            ip_address=request.client.host if request.client else "",
        )
    )
    await db.commit()
    return {"status": "ok"}
