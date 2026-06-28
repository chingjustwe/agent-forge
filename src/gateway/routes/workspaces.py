from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.gateway.auth.roles import has_permission
from src.infra.db.models import Tenant, Workspace, User
from src.infra.db.session import get_db

router = APIRouter()


class CreateWorkspaceRequest(BaseModel):
    name: str


class AddMemberRequest(BaseModel):
    email: str
    role: str = "member"


def _require_role(request: Request, min_role: str):
    user = getattr(request.state, "user", None)
    if not user:
        return JSONResponse(status_code=401, content={"error": {"code": "UNAUTHORIZED", "message": "Not authenticated"}})
    if not has_permission(user.get("role", "viewer"), min_role):
        return JSONResponse(status_code=403, content={"error": {"code": "FORBIDDEN", "message": "Insufficient permissions"}})
    return None


@router.get("/api/v1/workspaces")
async def list_workspaces(request: Request, db: AsyncSession = Depends(get_db)):
    err = _require_role(request, "tenant_admin")
    if err:
        return err
    result = await db.execute(select(Workspace))
    workspaces = result.scalars().all()
    return [
        {
            "id": w.id,
            "name": w.name,
            "member_count": 0,
            "created_at": w.created_at.isoformat(),
        }
        for w in workspaces
    ]


@router.post("/api/v1/workspaces")
async def create_workspace(request: Request, body: CreateWorkspaceRequest, db: AsyncSession = Depends(get_db)):
    err = _require_role(request, "tenant_admin")
    if err:
        return err
    tenant_id = request.state.user.get("tenant_id", "")
    ws = Workspace(tenant_id=tenant_id, name=body.name)
    db.add(ws)
    await db.commit()
    await db.refresh(ws)
    return JSONResponse(
        status_code=201,
        content={
            "id": ws.id,
            "tenant_id": ws.tenant_id,
            "name": ws.name,
            "created_at": ws.created_at.isoformat(),
        },
    )


@router.get("/api/v1/workspaces/{workspace_id}/members")
async def list_members(workspace_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    err = _require_role(request, "workspace_admin")
    if err:
        return err
    result = await db.execute(
        select(User).where(User.workspace_ids.contains(workspace_id))
    )
    users = result.scalars().all()
    return [
        {
            "user_id": u.id,
            "email": u.email,
            "name": u.name,
            "role": u.role,
        }
        for u in users
    ]


@router.post("/api/v1/workspaces/{workspace_id}/members")
async def add_member(workspace_id: str, request: Request, body: AddMemberRequest, db: AsyncSession = Depends(get_db)):
    err = _require_role(request, "workspace_admin")
    if err:
        return err

    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()
    if not user:
        return JSONResponse(status_code=404, content={"error": {"code": "NOT_FOUND", "message": "User not found"}})

    if workspace_id not in (user.workspace_ids or []):
        current_ids = list(user.workspace_ids or [])
        current_ids.append(workspace_id)
        user.workspace_ids = current_ids
        await db.commit()

    return JSONResponse(
        status_code=201,
        content={"user_id": user.id, "email": user.email, "role": body.role},
    )


@router.delete("/api/v1/workspaces/{workspace_id}/members/{user_id}")
async def remove_member(workspace_id: str, user_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    err = _require_role(request, "workspace_admin")
    if err:
        return err

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        return JSONResponse(status_code=404, content={"error": {"code": "NOT_FOUND", "message": "User not found"}})

    current_ids = list(user.workspace_ids or [])
    if workspace_id in current_ids:
        current_ids.remove(workspace_id)
        user.workspace_ids = current_ids
        await db.commit()

    return JSONResponse(status_code=204)
