from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.gateway.auth.roles import has_permission
from src.infra.db.engine import async_session
from src.infra.db.models import Workspace
from src.infra.telemetry.quota import QuotaGuardrail

router = APIRouter()


class QuotaUpdate(BaseModel):
    max_tokens_per_day: int | None = None
    max_cost_per_month: float | None = None


@router.get("/api/v1/workspaces/{ws_id}/quota")
async def get_quota(request: Request, ws_id: str):
    user = getattr(request.state, "user", None)
    if not user:
        return JSONResponse(status_code=401, content={"error": {"code": "UNAUTHORIZED", "message": "Not authenticated"}})
    if not has_permission(user.get("role", "viewer"), "member"):
        return JSONResponse(status_code=403, content={"error": {"code": "FORBIDDEN", "message": "Insufficient permissions"}})

    guardrail = QuotaGuardrail()
    usage = await guardrail.get_usage(ws_id)
    return {
        "max_tokens_per_day": usage["max_tokens_per_day"],
        "max_cost_per_month": usage["max_cost_per_month"],
        "usage_today": usage["tokens_used"],
        "tokens_used": usage["tokens_used"],
        "cost_today": usage["cost_today"],
    }


@router.put("/api/v1/workspaces/{ws_id}/quota")
async def update_quota(request: Request, ws_id: str, body: QuotaUpdate):
    user = getattr(request.state, "user", None)
    if not user:
        return JSONResponse(status_code=401, content={"error": {"code": "UNAUTHORIZED", "message": "Not authenticated"}})
    if not has_permission(user.get("role", "viewer"), "workspace_admin"):
        return JSONResponse(status_code=403, content={"error": {"code": "FORBIDDEN", "message": "Workspace admin role required"}})

    async with async_session() as session:
        ws = await session.get(Workspace, ws_id)
        if not ws:
            return JSONResponse(status_code=404, content={"error": {"code": "NOT_FOUND", "message": "Workspace not found"}})

        if body.max_tokens_per_day is not None:
            ws.max_tokens_per_day = body.max_tokens_per_day
        if body.max_cost_per_month is not None:
            ws.max_cost_per_month = body.max_cost_per_month
        await session.commit()
        await session.refresh(ws)

    return {
        "quota": {
            "max_tokens_per_day": ws.max_tokens_per_day,
            "max_cost_per_month": ws.max_cost_per_month,
        }
    }
