"""P3a §6.3: MCP server management API.

Workspace-scoped MCP server registry: CRUD + tool discovery + health check.

Access:
- Reads: any workspace member (``mcp:read``).
- Mutations: ``workspace_admin`` (``mcp:write``).
"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from src.gateway.auth.rbac import require_permission
from src.runtime.harness.registry import get_registry

router = APIRouter()


class MCPServerOut(BaseModel):
    name: str
    endpoint: str
    transport: str
    enabled: bool


def _auto_detect_transport(endpoint: str, transport: str) -> str:
    """Auto-correct the transport based on the endpoint URL.

    An endpoint ending in ``/sse`` speaks the MCP SSE protocol; using the
    default ``http`` (Streamable HTTP) transport against it will fail.  We
    only auto-switch from ``http`` → ``sse`` so an explicit ``stdio`` choice
    is never overridden.
    """
    if (
        transport == "http"
        and isinstance(endpoint, str)
        and endpoint.rstrip("/").endswith("/sse")
    ):
        return "sse"
    return transport


class CreateMCPServerRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    endpoint: str = Field(..., min_length=1)
    transport: str = Field(default="http")
    auth_token: str | None = None
    enabled: bool = True


class UpdateMCPServerRequest(BaseModel):
    endpoint: str | None = None
    transport: str | None = None
    auth_token: str | None = None
    enabled: bool | None = None


@router.get("/api/v1/workspaces/{workspace_id}/mcp/servers")
async def list_mcp_servers(
    workspace_id: str,
    _ctx=Depends(require_permission("mcp:read", workspace_id_param="workspace_id")),
):
    """List all MCP servers registered for this workspace."""
    mcp = get_registry().mcp
    servers = mcp.list_servers(workspace_id)
    return [
        MCPServerOut(
            name=s.name,
            endpoint=s.endpoint,
            transport=s.transport,
            enabled=s.enabled,
        ).model_dump()
        for s in servers
    ]


@router.post("/api/v1/workspaces/{workspace_id}/mcp/servers")
async def create_mcp_server(
    workspace_id: str,
    body: CreateMCPServerRequest,
    _ctx=Depends(require_permission("mcp:write", workspace_id_param="workspace_id")),
):
    """Register a new MCP server for this workspace."""
    from src.runtime.harness.mcp import MCPServerConfig

    mcp = get_registry().mcp
    if mcp.get_server(body.name, workspace_id) is not None:
        return JSONResponse(
            status_code=409,
            content={"error": {"code": "CONFLICT", "message": f"MCP server {body.name!r} already exists"}},
        )
    transport = _auto_detect_transport(body.endpoint, body.transport)
    config = MCPServerConfig(
        name=body.name,
        workspace_id=workspace_id,
        endpoint=body.endpoint,
        transport=transport,
        auth_token=body.auth_token,
        enabled=body.enabled,
    )
    await mcp.register_server(config)
    return JSONResponse(
        status_code=201,
        content=MCPServerOut(
            name=config.name,
            endpoint=config.endpoint,
            transport=config.transport,
            enabled=config.enabled,
        ).model_dump(),
    )


@router.put("/api/v1/workspaces/{workspace_id}/mcp/servers/{name}")
async def update_mcp_server(
    workspace_id: str,
    name: str,
    body: UpdateMCPServerRequest,
    _ctx=Depends(require_permission("mcp:write", workspace_id_param="workspace_id")),
):
    """Update an MCP server's configuration."""
    from src.runtime.harness.mcp import MCPServerConfig
    from datetime import datetime, timezone

    mcp = get_registry().mcp
    existing = mcp.get_server(name, workspace_id)
    if existing is None:
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "NOT_FOUND", "message": f"MCP server {name!r} not found"}},
        )
    # Re-register with updated fields (preserving created_at).
    new_endpoint = body.endpoint or existing.endpoint
    new_transport = body.transport or existing.transport
    new_transport = _auto_detect_transport(new_endpoint, new_transport)
    updated = MCPServerConfig(
        name=existing.name,
        workspace_id=existing.workspace_id,
        endpoint=new_endpoint,
        transport=new_transport,
        auth_token=body.auth_token if body.auth_token is not None else existing.auth_token,
        enabled=body.enabled if body.enabled is not None else existing.enabled,
        created_at=existing.created_at,
    )
    await mcp.register_server(updated)
    return MCPServerOut(
        name=updated.name,
        endpoint=updated.endpoint,
        transport=updated.transport,
        enabled=updated.enabled,
    ).model_dump()


@router.delete("/api/v1/workspaces/{workspace_id}/mcp/servers/{name}")
async def delete_mcp_server(
    workspace_id: str,
    name: str,
    _ctx=Depends(require_permission("mcp:write", workspace_id_param="workspace_id")),
):
    """Unregister an MCP server."""
    mcp = get_registry().mcp
    removed = await mcp.unregister_server(name, workspace_id)
    if not removed:
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "NOT_FOUND", "message": f"MCP server {name!r} not found"}},
        )
    return Response(status_code=204)


@router.get("/api/v1/workspaces/{workspace_id}/mcp/servers/{name}/tools")
async def discover_mcp_tools(
    workspace_id: str,
    name: str,
    _ctx=Depends(require_permission("mcp:read", workspace_id_param="workspace_id")),
):
    """Discover tools exposed by an MCP server."""
    mcp = get_registry().mcp
    if mcp.get_server(name, workspace_id) is None:
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "NOT_FOUND", "message": f"MCP server {name!r} not found"}},
        )
    try:
        tools = await mcp.list_tools(name, workspace_id)
        return {"tools": tools}
    except Exception as exc:
        return JSONResponse(
            status_code=502,
            content={"error": {"code": "MCP_ERROR", "message": str(exc)}},
        )


@router.get("/api/v1/workspaces/{workspace_id}/mcp/servers/{name}/health")
async def check_mcp_health(
    workspace_id: str,
    name: str,
    _ctx=Depends(require_permission("mcp:read", workspace_id_param="workspace_id")),
):
    """Check if an MCP server is reachable."""
    mcp = get_registry().mcp
    if mcp.get_server(name, workspace_id) is None:
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "NOT_FOUND", "message": f"MCP server {name!r} not found"}},
        )
    healthy, error = await mcp.health_check(name, workspace_id)
    return {"healthy": healthy, "error": error}
