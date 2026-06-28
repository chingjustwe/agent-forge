from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from src.gateway.auth.roles import has_permission
from src.infra.telemetry.collector import TelemetryCollector

router = APIRouter()


def _get_user(request: Request) -> dict | None:
    return getattr(request.state, "user", None)


def _check_member(user: dict | None) -> bool:
    if not user:
        return False
    return has_permission(user.get("role", "viewer"), "member")


def _ws_check(user: dict, ws_id: str) -> bool:
    return ws_id in user.get("workspace_ids", []) or has_permission(user.get("role", "viewer"), "tenant_admin")


@router.get("/api/v1/workspaces/{ws_id}/observability/summary")
async def get_summary(request: Request, ws_id: str):
    user = _get_user(request)
    if not _check_member(user):
        return JSONResponse(status_code=401, content={"error": {"code": "UNAUTHORIZED", "message": "Not authenticated"}})

    since = request.query_params.get("since")
    collector = TelemetryCollector()
    summary = await collector.get_summary(ws_id, since)
    return summary


@router.get("/api/v1/workspaces/{ws_id}/observability/requests")
async def get_requests(request: Request, ws_id: str):
    user = _get_user(request)
    if not _check_member(user):
        return JSONResponse(status_code=401, content={"error": {"code": "UNAUTHORIZED", "message": "Not authenticated"}})

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
async def get_request_detail(request: Request, ws_id: str, trace_id: str):
    user = _get_user(request)
    if not _check_member(user):
        return JSONResponse(status_code=401, content={"error": {"code": "UNAUTHORIZED", "message": "Not authenticated"}})

    collector = TelemetryCollector()
    detail = await collector.get_request_detail(ws_id, trace_id)
    if not detail:
        return JSONResponse(status_code=404, content={"error": {"code": "NOT_FOUND", "message": "Request not found"}})
    return detail


@router.get("/api/v1/workspaces/{ws_id}/observability/tokens/daily")
async def get_token_daily(request: Request, ws_id: str):
    user = _get_user(request)
    if not _check_member(user):
        return JSONResponse(status_code=401, content={"error": {"code": "UNAUTHORIZED", "message": "Not authenticated"}})

    since = request.query_params.get("since")
    until = request.query_params.get("until")

    collector = TelemetryCollector()
    data = await collector.get_daily_tokens(ws_id, since, until)
    return data


@router.get("/api/v1/workspaces/{ws_id}/observability/latency")
async def get_latency(request: Request, ws_id: str):
    user = _get_user(request)
    if not _check_member(user):
        return JSONResponse(status_code=401, content={"error": {"code": "UNAUTHORIZED", "message": "Not authenticated"}})

    since = request.query_params.get("since")
    until = request.query_params.get("until")

    collector = TelemetryCollector()
    data = await collector.get_latency(ws_id, since, until)
    return data


@router.get("/api/v1/workspaces/{ws_id}/observability/errors")
async def get_errors(request: Request, ws_id: str):
    user = _get_user(request)
    if not _check_member(user):
        return JSONResponse(status_code=401, content={"error": {"code": "UNAUTHORIZED", "message": "Not authenticated"}})

    since = request.query_params.get("since")

    collector = TelemetryCollector()
    data = await collector.get_errors(ws_id, since)
    return data
