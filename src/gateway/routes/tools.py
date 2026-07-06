"""P3a §6.2: Tool management API.

Workspace-scoped tool registry: list, register (custom or MCP-backed),
and delete tools. Builtin tools are read-only (cannot be deleted).

Access:
- Reads: any workspace member (``tools:read``).
- Mutations: ``workspace_admin`` (``tools:write``).
"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from src.gateway.auth.rbac import require_permission
from src.runtime.harness.registry import get_registry

router = APIRouter()


class ToolOut(BaseModel):
    name: str
    description: str
    input_schema: dict
    source: str
    timeout: int = 60


class CreateToolRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str = ""
    input_schema: dict = Field(default_factory=dict)
    mcp_server: str | None = None
    timeout: int = Field(default=60, ge=1, le=300)


@router.get("/api/v1/workspaces/{workspace_id}/tools")
async def list_tools(
    workspace_id: str,
    _ctx=Depends(require_permission("tools:read", workspace_id_param="workspace_id")),
):
    """List all tools available to this workspace (builtin + custom + MCP)."""
    registry = get_registry().tools
    tools = registry.list(workspace_id=workspace_id)
    return [
        ToolOut(
            name=t.name,
            description=t.description,
            input_schema=t.input_schema,
            source=t.source,
            timeout=t.timeout,
        ).model_dump()
        for t in tools
    ]


@router.post("/api/v1/workspaces/{workspace_id}/tools")
async def create_tool(
    workspace_id: str,
    body: CreateToolRequest,
    _ctx=Depends(require_permission("tools:write", workspace_id_param="workspace_id")),
):
    """Register a custom or MCP-backed tool for this workspace."""
    from src.runtime.harness.tool_engine import ToolDefinition

    source = "mcp" if body.mcp_server else "custom"
    tool_def = ToolDefinition(
        name=body.name,
        description=body.description,
        input_schema=body.input_schema,
        source=source,
        mcp_server=body.mcp_server,
        timeout=body.timeout,
        workspace_id=workspace_id,
    )
    registry = get_registry().tools
    if registry.get(body.name, workspace_id=workspace_id) is not None:
        return JSONResponse(
            status_code=409,
            content={"error": {"code": "CONFLICT", "message": f"Tool {body.name!r} already exists"}},
        )
    registry.register(tool_def)
    return JSONResponse(
        status_code=201,
        content=ToolOut(
            name=tool_def.name,
            description=tool_def.description,
            input_schema=tool_def.input_schema,
            source=tool_def.source,
            timeout=tool_def.timeout,
        ).model_dump(),
    )


@router.delete("/api/v1/workspaces/{workspace_id}/tools/{name}")
async def delete_tool(
    workspace_id: str,
    name: str,
    _ctx=Depends(require_permission("tools:write", workspace_id_param="workspace_id")),
):
    """Delete a custom tool. Builtin tools cannot be deleted."""
    registry = get_registry().tools
    tool = registry.get(name, workspace_id=workspace_id)
    if tool is None:
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "NOT_FOUND", "message": f"Tool {name!r} not found"}},
        )
    if tool.source == "builtin":
        return JSONResponse(
            status_code=403,
            content={"error": {"code": "FORBIDDEN", "message": "Builtin tools cannot be deleted"}},
        )
    registry.unregister(name, workspace_id=workspace_id)
    return Response(status_code=204)
