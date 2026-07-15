from fastapi import APIRouter, Depends, Request

from src.gateway.auth.rbac import require_workspace_role
from src.infra.telemetry.collector import TelemetryCollector

router = APIRouter()


def _resolve_user_id(request: Request, ctx: dict) -> str | None:
    """统一权限规则：member/viewer 只能看自己的数据，admin 看整个 workspace。

    前端传 user_id=me 时解析为当前用户 ID；
    member/viewer 无论传什么都强制改为自己的 user_id（防止越权）。
    """
    workspace_role = ctx.get("workspace_role", "")
    user = ctx.get("user") or request.state.user
    current_uid = user.get("sub") or user.get("id")

    # member/viewer 强制只看自己
    if workspace_role in ("member", "viewer"):
        return current_uid

    # admin 传 user_id=me 时解析为当前用户
    requested = request.query_params.get("user_id")
    if requested == "me":
        return current_uid
    # admin 不传 user_id → 看整个 workspace
    return None


@router.get("/api/v1/workspaces/{ws_id}/observability/summary")
async def get_summary(
    request: Request,
    ws_id: str,
    ctx: dict = Depends(
        require_workspace_role("ws_id", "member", "workspace_admin")
    ),
):
    since = request.query_params.get("since")
    until = request.query_params.get("until")
    user_id = _resolve_user_id(request, ctx)
    collector = TelemetryCollector()
    summary = await collector.get_summary(ws_id, since, until=until, user_id=user_id)
    return summary


@router.get("/api/v1/workspaces/{ws_id}/observability/requests")
async def get_requests(
    request: Request,
    ws_id: str,
    _ctx=Depends(
        require_workspace_role("ws_id", "member", "workspace_admin")
    ),
):
    limit = int(request.query_params.get("limit", 50))
    offset = int(request.query_params.get("offset", 0))
    status = request.query_params.get("status")
    model = request.query_params.get("model")
    since = request.query_params.get("since")

    collector = TelemetryCollector()
    data = await collector.get_requests(
        ws_id, limit=limit, offset=offset,
        status=int(status) if status else None,
        model=model, since=since,
    )
    return data


@router.get("/api/v1/workspaces/{ws_id}/observability/requests/{trace_id}")
async def get_request_detail(
    request: Request,
    ws_id: str,
    trace_id: str,
    _ctx=Depends(
        require_workspace_role("ws_id", "member", "workspace_admin")
    ),
):
    collector = TelemetryCollector()
    detail = await collector.get_request_detail(ws_id, trace_id)
    if not detail:
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "NOT_FOUND", "message": "Request not found"}},
        )
    return detail


@router.get("/api/v1/workspaces/{ws_id}/observability/tokens/daily")
async def get_token_daily(
    request: Request,
    ws_id: str,
    ctx: dict = Depends(
        require_workspace_role("ws_id", "member", "workspace_admin")
    ),
):
    since = request.query_params.get("since")
    until = request.query_params.get("until")
    user_id = _resolve_user_id(request, ctx)
    collector = TelemetryCollector()
    data = await collector.get_daily_tokens(ws_id, since, until, user_id=user_id)
    return data


@router.get("/api/v1/workspaces/{ws_id}/observability/latency")
async def get_latency(
    request: Request,
    ws_id: str,
    ctx: dict = Depends(
        require_workspace_role("ws_id", "member", "workspace_admin")
    ),
):
    since = request.query_params.get("since")
    until = request.query_params.get("until")
    user_id = _resolve_user_id(request, ctx)
    collector = TelemetryCollector()
    data = await collector.get_latency(ws_id, since, until, user_id=user_id)
    return data


@router.get("/api/v1/workspaces/{ws_id}/observability/errors")
async def get_errors(
    request: Request,
    ws_id: str,
    ctx: dict = Depends(
        require_workspace_role("ws_id", "member", "workspace_admin")
    ),
):
    since = request.query_params.get("since")
    until = request.query_params.get("until")
    user_id = _resolve_user_id(request, ctx)
    collector = TelemetryCollector()
    data = await collector.get_errors(ws_id, since, until=until, user_id=user_id)
    return data
