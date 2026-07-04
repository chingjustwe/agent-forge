from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from src.gateway.auth.rbac import require_tenant_role, require_workspace_role
from src.gateway.routes.me import invalidate_workspace_cache
from src.infra.db.models import User, Workspace, WorkspaceMember
from src.infra.db.session import get_db
from src.utils.slugify import slugify, unique_slug

router = APIRouter()


class CreateWorkspaceRequest(BaseModel):
    name: str
    slug: str | None = None
    description: str | None = None
    icon: str | None = None


class AddMemberRequest(BaseModel):
    user_id: str
    role: str = "member"


@router.get("/api/v1/workspaces")
async def list_workspaces(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_tenant_role("tenant_admin")),
):
    result = await db.execute(select(Workspace))
    workspaces = result.scalars().all()

    # Batch-count members per workspace to avoid N+1
    member_counts: dict[str, int] = {}
    if workspaces:
        ws_ids = [w.id for w in workspaces]
        cnt_result = await db.execute(
            select(WorkspaceMember.workspace_id, func.count())
            .where(WorkspaceMember.workspace_id.in_(ws_ids))
            .group_by(WorkspaceMember.workspace_id)
        )
        for ws_id, cnt in cnt_result.all():
            member_counts[ws_id] = cnt

    return [
        {
            "id": w.id,
            "name": w.name,
            "slug": w.slug,
            "description": w.description,
            "icon": w.icon,
            "owner_id": w.owner_id,
            "member_count": member_counts.get(w.id, 0),
            "created_at": w.created_at.isoformat() if w.created_at else None,
            "updated_at": w.updated_at.isoformat() if w.updated_at else None,
        }
        for w in workspaces
    ]


@router.post("/api/v1/workspaces")
async def create_workspace(
    request: Request,
    body: CreateWorkspaceRequest,
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
    return JSONResponse(
        status_code=201,
        content={
            "id": ws.id,
            "tenant_id": ws.tenant_id,
            "name": ws.name,
            "slug": ws.slug,
            "description": ws.description,
            "icon": ws.icon,
            "owner_id": ws.owner_id,
            "created_at": ws.created_at.isoformat() if ws.created_at else None,
            "updated_at": ws.updated_at.isoformat() if ws.updated_at else None,
        },
    )


@router.get("/api/v1/workspaces/{workspace_id}/members")
async def list_members(
    workspace_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _ctx=Depends(
        require_workspace_role("workspace_id", "workspace_admin", "workspace_owner")
    ),
):
    result = await db.execute(
        select(User, WorkspaceMember.role)
        .join(WorkspaceMember, WorkspaceMember.user_id == User.id)
        .where(WorkspaceMember.workspace_id == workspace_id)
    )
    rows = result.all()
    return [
        {
            "user_id": u.id,
            "email": u.email,
            "name": u.name,
            "role": member_role,
        }
        for u, member_role in rows
    ]


@router.post("/api/v1/workspaces/{workspace_id}/members")
async def add_member(
    workspace_id: str,
    request: Request,
    body: AddMemberRequest,
    db: AsyncSession = Depends(get_db),
    _ctx=Depends(
        require_workspace_role("workspace_id", "workspace_admin", "workspace_owner")
    ),
):
    result = await db.execute(select(User).where(User.id == body.user_id))
    user = result.scalar_one_or_none()
    if not user:
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "NOT_FOUND", "message": "User not found"}},
        )

    existing = await db.get(WorkspaceMember, (workspace_id, body.user_id))
    if not existing:
        db.add(
            WorkspaceMember(
                workspace_id=workspace_id, user_id=body.user_id, role=body.role
            )
        )
        await db.commit()
        invalidate_workspace_cache(body.user_id)

    return JSONResponse(
        status_code=201,
        content={"user_id": user.id, "email": user.email, "role": body.role},
    )


@router.delete("/api/v1/workspaces/{workspace_id}/members/{user_id}")
async def remove_member(
    workspace_id: str,
    user_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _ctx=Depends(
        require_workspace_role("workspace_id", "workspace_admin", "workspace_owner")
    ),
):
    member = await db.get(WorkspaceMember, (workspace_id, user_id))
    if not member:
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "NOT_FOUND", "message": "Member not found"}},
        )

    await db.delete(member)
    await db.commit()
    invalidate_workspace_cache(user_id)
    return Response(status_code=204)
