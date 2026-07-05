import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.gateway.auth.rbac import require_permission
from src.infra.db.engine import async_session
from src.infra.db.models import OTelSettings

router = APIRouter()


class OTelConfig(BaseModel):
    enabled: bool = False
    endpoint: str = ""
    headers: dict = {}


@router.get("/api/v1/workspaces/{ws_id}/settings/otel")
async def get_otel_settings(
    request: Request,
    ws_id: str,
    _ctx=Depends(
        require_permission("settings:read", workspace_id_param="ws_id")
    ),
):
    async with async_session() as session:
        settings = await session.get(OTelSettings, ws_id)
        if not settings:
            return {"enabled": False, "endpoint": "", "headers": {}}

        return {
            "enabled": bool(settings.enabled),
            "endpoint": settings.endpoint,
            "headers": json.loads(settings.headers) if settings.headers else {},
        }


@router.put("/api/v1/workspaces/{ws_id}/settings/otel")
async def update_otel_settings(
    request: Request,
    ws_id: str,
    body: OTelConfig,
    _ctx=Depends(
        require_permission("settings:write", workspace_id_param="ws_id")
    ),
):
    async with async_session() as session:
        settings = await session.get(OTelSettings, ws_id)
        if not settings:
            settings = OTelSettings(workspace_id=ws_id)
            session.add(settings)

        settings.enabled = 1 if body.enabled else 0
        settings.endpoint = body.endpoint
        settings.headers = json.dumps(body.headers)
        await session.commit()

        return {
            "otel": {
                "enabled": bool(settings.enabled),
                "endpoint": settings.endpoint,
                "headers": json.loads(settings.headers) if settings.headers else {},
            }
        }
