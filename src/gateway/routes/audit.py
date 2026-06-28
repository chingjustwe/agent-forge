from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from src.infra.db.session import get_db
from src.infra.db.models import AuditLog as AuditLogModel
from src.gateway.auth.roles import has_permission

router = APIRouter(tags=["audit"])


@router.get("/api/v1/workspaces/{workspace_id}/audit")
async def workspace_audit_log(
    workspace_id: str,
    request: Request,
    action: Optional[str] = Query(None),
    since: Optional[str] = Query(None),
    until: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    user: dict = request.state.user
    user_roles = user.get("workspace_ids", [])
    is_tenant_admin = user.get("role") == "tenant_admin"
    is_member = workspace_id in user_roles or is_tenant_admin
    if not is_member:
        return JSONResponse(status_code=403, content={"error": {"code": "FORBIDDEN", "message": "Insufficient permissions"}})

    query = select(AuditLogModel).where(AuditLogModel.workspace_id == workspace_id)
    if action:
        query = query.where(AuditLogModel.action == action)
    if since:
        query = query.where(AuditLogModel.created_at >= datetime.fromisoformat(since))
    if until:
        query = query.where(AuditLogModel.created_at <= datetime.fromisoformat(until))

    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar()

    query = query.order_by(AuditLogModel.created_at.desc()).offset(offset).limit(limit)
    result = await db.execute(query)
    items = result.scalars().all()

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
