"""P2-2 + P3a: Workspace-scoped agent configurations.

Each agent config is bound to a workspace and references an adapter
(``deepagents``). The legacy ``framework`` JSON column is kept as the
canonical adapter field (P3a maps it to ``AgentDefinition.adapter``);
Wave 2.5 removed ``direct_llm`` / ``adk`` / ``langgraph``. The legacy
``config`` JSON column is kept as free-form ``metadata``.

P3a adds structured fields (system_prompt, model, temperature, max_tokens,
tools, guardrails, skills, hooks, memory_config) so the harness can build
a ``HarnessContext`` without parsing free-form JSON. All new fields are
optional with sensible defaults — existing API clients that only send
``name`` / ``framework`` / ``config`` keep working unchanged.

Access rules:
- Reads (list / detail): any workspace member.
- Mutations (create / patch / delete): ``workspace_admin``
  (and ``tenant_admin`` short-circuits to admin).

Cross-workspace isolation: every query filters on ``workspace_id`` AND
``id``, so an agent from another workspace is never visible (returns 404).
"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.gateway.auth.rbac import require_permission
from src.infra.db.models import AgentConfig, AuditLog, Workspace
from src.infra.db.session import get_db
from src.infra.llm.models import resolve_default_model

router = APIRouter()

# Phase 4: "adk" and "langgraph" removed (never functional); "deepagents" added.
# Wave 2.5: "direct_llm" removed; deepagents is the sole framework.
ALLOWED_FRAMEWORKS = ("deepagents",)


class MemoryConfigPayload(BaseModel):
    enable_short_term: bool = True
    enable_long_term: bool = False
    recall_top_k: int = 5


class SubagentRefPayload(BaseModel):
    """Phase 4b: a subagent is a *reference* to an existing agent in the
    same workspace (selected from a dropdown in the UI), not an inline
    definition. The runtime resolves ``agent_id`` into a full subagent
    spec at run time (see ``HarnessRuntime._resolve_subagent_refs``).
    """
    agent_id: str = Field(..., min_length=1)
    name: str | None = None  # denormalized display name (optional)


class CreateAgentRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    framework: str
    config: dict = Field(default_factory=dict)
    # P3a structured fields — all optional, override defaults when provided.
    system_prompt: str | None = None
    model: str | None = None
    temperature: float | None = Field(None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(None, ge=1)
    tools: list[str] | None = None
    guardrails: list[str] | None = None
    skills: list[str] | None = None
    hooks: list[str] | None = None
    memory_config: MemoryConfigPayload | None = None
    # Phase 4b: subagent *references* (only used when framework="deepagents").
    subagents: list[SubagentRefPayload] | None = None
    # Phase 5: explicitly bound MCP server names (workspace-scoped). The
    # agent receives every tool exposed by each selected server.
    mcp_servers: list[str] | None = None


class UpdateAgentRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    framework: str | None = None
    config: dict | None = None
    # P3a structured fields — None means "leave unchanged".
    system_prompt: str | None = None
    model: str | None = None
    temperature: float | None = Field(None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(None, ge=1)
    tools: list[str] | None = None
    guardrails: list[str] | None = None
    skills: list[str] | None = None
    hooks: list[str] | None = None
    memory_config: MemoryConfigPayload | None = None
    # Phase 4b: None = leave unchanged; [] = clear all subagents.
    subagents: list[SubagentRefPayload] | None = None
    # Phase 5: None = leave unchanged; [] = unbind all MCP servers.
    mcp_servers: list[str] | None = None


def _serialize_agent(a: AgentConfig) -> dict:
    """Serialize an AgentConfig ORM row to a JSON dict.

    Includes both legacy fields (framework, config) and P3a structured
    fields. The ``framework`` value is echoed back as-is for backward
    compat; clients that know about P3a can read the structured fields.
    """
    return {
        "id": a.id,
        "workspace_id": a.workspace_id,
        "name": a.name,
        "framework": a.framework,
        "config": a.config or {},
        # P3a structured fields
        "system_prompt": a.system_prompt or "",
        "model": a.model or resolve_default_model(),
        "temperature": a.temperature if a.temperature is not None else 0.7,
        "max_tokens": a.max_tokens if a.max_tokens is not None else 4096,
        "tools": a.tools or [],
        "guardrails": a.guardrails or [],
        "skills": a.skills or [],
        "hooks": a.hooks or [],
        "memory_config": a.memory_config,
        # Phase 4b: subagent specs (only meaningful for adapter="deepagents").
        "subagents": a.subagents or [],
        # Phase 5: bound MCP servers (agent gets all their tools).
        "mcp_servers": a.mcp_servers or [],
        # Metadata
        "created_by": a.created_by,
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "updated_at": a.updated_at.isoformat() if a.updated_at else None,
    }


def _apply_create_fields(agent: AgentConfig, body: CreateAgentRequest) -> None:
    """Copy P3a structured fields from the request onto a new ORM row."""
    if body.system_prompt is not None:
        agent.system_prompt = body.system_prompt
    if body.model is not None:
        agent.model = body.model
    if body.temperature is not None:
        agent.temperature = body.temperature
    if body.max_tokens is not None:
        agent.max_tokens = body.max_tokens
    if body.tools is not None:
        agent.tools = body.tools
    if body.guardrails is not None:
        agent.guardrails = body.guardrails
    if body.skills is not None:
        agent.skills = body.skills
    if body.hooks is not None:
        agent.hooks = body.hooks
    if body.memory_config is not None:
        agent.memory_config = body.memory_config.model_dump()
    if body.subagents is not None:
        agent.subagents = [s.model_dump() for s in body.subagents]
    if body.mcp_servers is not None:
        agent.mcp_servers = body.mcp_servers


async def _write_audit(
    db: AsyncSession,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    action: str,
    target_id: str,
    details: dict | None = None,
    ip_address: str = "",
) -> None:
    db.add(
        AuditLog(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            action=action,
            target_type="agent",
            target_id=target_id,
            details=details or {},
            ip_address=ip_address or "",
        )
    )


def _bad_request(message: str) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={"error": {"code": "BAD_REQUEST", "message": message}},
    )


def _not_found(message: str = "Agent not found") -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"error": {"code": "NOT_FOUND", "message": message}},
    )


def _validate_mcp_servers(workspace_id: str, names: list[str]) -> str | None:
    """Return an error message if any named MCP server is not registered in
    ``workspace_id`` (so we don't bind an agent to a non-existent server).

    Reads the ``MCPManager`` in-memory cache — the same source of truth the
    runtime uses to resolve servers at run time. Returns ``None`` when valid.
    """
    if not names:
        return None
    from src.runtime.harness.registry import get_registry

    mcp = get_registry().mcp
    if mcp is None:
        return None
    available = {c.name for c in mcp.list_servers(workspace_id)}
    missing = sorted(n for n in names if n not in available)
    if missing:
        return f"MCP server(s) not found in workspace: {', '.join(missing)}"
    return None


@router.post("/api/v1/workspaces/{workspace_id}/agents")
async def create_agent(
    workspace_id: str,
    body: CreateAgentRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _ctx=Depends(require_permission("agents:write", workspace_id_param="workspace_id")),
):
    """Create a new agent config in this workspace."""
    if body.framework not in ALLOWED_FRAMEWORKS:
        return _bad_request(
            f"framework must be one of {ALLOWED_FRAMEWORKS}"
        )

    # Validate bound MCP servers exist in this workspace (400 otherwise).
    if body.mcp_servers:
        err = _validate_mcp_servers(workspace_id, body.mcp_servers)
        if err:
            return _bad_request(err)

    user = request.state.user
    user_id = user.get("sub") or user.get("id", "")
    tenant_id = user.get("tenant_id", "")

    agent = AgentConfig(
        workspace_id=workspace_id,
        name=body.name,
        framework=body.framework,
        config=body.config,
        created_by=user_id,
    )
    _apply_create_fields(agent, body)
    db.add(agent)
    await db.flush()  # populate agent.id before referencing it in AuditLog
    await _write_audit(
        db,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
        action="agent.create",
        target_id=agent.id,
        details={"name": body.name, "framework": body.framework},
        ip_address=request.client.host if request.client else "",
    )
    await db.commit()
    await db.refresh(agent)
    return JSONResponse(status_code=201, content=_serialize_agent(agent))


@router.get("/api/v1/workspaces/{workspace_id}/agents")
async def list_agents(
    workspace_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _ctx=Depends(require_permission("agents:read", workspace_id_param="workspace_id")),
):
    """List all agent configs in this workspace (newest first)."""
    result = await db.execute(
        select(AgentConfig)
        .where(AgentConfig.workspace_id == workspace_id)
        .order_by(AgentConfig.created_at.desc())
    )
    items = result.scalars().all()
    return [_serialize_agent(a) for a in items]


@router.get("/api/v1/workspaces/{workspace_id}/agents/{agent_id}")
async def get_agent(
    workspace_id: str,
    agent_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _ctx=Depends(require_permission("agents:read", workspace_id_param="workspace_id")),
):
    """Fetch a single agent config. Cross-workspace lookups return 404."""
    result = await db.execute(
        select(AgentConfig).where(
            AgentConfig.id == agent_id,
            AgentConfig.workspace_id == workspace_id,
        )
    )
    agent = result.scalar_one_or_none()
    if not agent:
        return _not_found()
    return _serialize_agent(agent)


@router.patch("/api/v1/workspaces/{workspace_id}/agents/{agent_id}")
async def update_agent(
    workspace_id: str,
    agent_id: str,
    body: UpdateAgentRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _ctx=Depends(require_permission("agents:write", workspace_id_param="workspace_id")),
):
    """Update name / framework / config / structured fields.

    Cross-workspace lookups return 404. P3a structured fields are patched
    only when explicitly provided (None means "leave unchanged").
    """
    result = await db.execute(
        select(AgentConfig).where(
            AgentConfig.id == agent_id,
            AgentConfig.workspace_id == workspace_id,
        )
    )
    agent = result.scalar_one_or_none()
    if not agent:
        return _not_found()

    if body.framework is not None and body.framework not in ALLOWED_FRAMEWORKS:
        return _bad_request(
            f"framework must be one of {ALLOWED_FRAMEWORKS}"
        )

    # Validate bound MCP servers exist in this workspace (400 otherwise).
    if body.mcp_servers is not None:
        err = _validate_mcp_servers(workspace_id, body.mcp_servers)
        if err:
            return _bad_request(err)

    details: dict = {}
    if body.name is not None:
        agent.name = body.name
        details["name"] = body.name
    if body.framework is not None:
        agent.framework = body.framework
        details["framework"] = body.framework
    if body.config is not None:
        agent.config = body.config
        details["config"] = body.config
    # P3a structured fields
    if body.system_prompt is not None:
        agent.system_prompt = body.system_prompt
        details["system_prompt"] = body.system_prompt
    if body.model is not None:
        agent.model = body.model
        details["model"] = body.model
    if body.temperature is not None:
        agent.temperature = body.temperature
        details["temperature"] = body.temperature
    if body.max_tokens is not None:
        agent.max_tokens = body.max_tokens
        details["max_tokens"] = body.max_tokens
    if body.tools is not None:
        agent.tools = body.tools
        details["tools"] = body.tools
    if body.guardrails is not None:
        agent.guardrails = body.guardrails
        details["guardrails"] = body.guardrails
    if body.skills is not None:
        agent.skills = body.skills
        details["skills"] = body.skills
    if body.hooks is not None:
        agent.hooks = body.hooks
        details["hooks"] = body.hooks
    if body.memory_config is not None:
        agent.memory_config = body.memory_config.model_dump()
        details["memory_config"] = body.memory_config.model_dump()
    if body.subagents is not None:
        agent.subagents = [s.model_dump() for s in body.subagents]
        details["subagents"] = agent.subagents
    if body.mcp_servers is not None:
        agent.mcp_servers = body.mcp_servers
        details["mcp_servers"] = body.mcp_servers

    user = request.state.user
    await _write_audit(
        db,
        tenant_id=user.get("tenant_id", ""),
        workspace_id=workspace_id,
        user_id=user.get("sub") or user.get("id", ""),
        action="agent.update",
        target_id=agent.id,
        details=details,
        ip_address=request.client.host if request.client else "",
    )
    await db.commit()
    await db.refresh(agent)
    return _serialize_agent(agent)


@router.delete("/api/v1/workspaces/{workspace_id}/agents/{agent_id}")
async def delete_agent(
    workspace_id: str,
    agent_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _ctx=Depends(require_permission("agents:write", workspace_id_param="workspace_id")),
):
    """Hard-delete an agent config. Cross-workspace lookups return 404."""
    result = await db.execute(
        select(AgentConfig).where(
            AgentConfig.id == agent_id,
            AgentConfig.workspace_id == workspace_id,
        )
    )
    agent = result.scalar_one_or_none()
    if not agent:
        return _not_found()

    user = request.state.user
    await _write_audit(
        db,
        tenant_id=user.get("tenant_id", ""),
        workspace_id=workspace_id,
        user_id=user.get("sub") or user.get("id", ""),
        action="agent.delete",
        target_id=agent.id,
        details={"name": agent.name, "framework": agent.framework},
        ip_address=request.client.host if request.client else "",
    )
    await db.delete(agent)
    await db.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# P3-2: cross-workspace copy (tenant_admin only)
# ---------------------------------------------------------------------------
@router.post(
    "/api/v1/workspaces/{workspace_id}/agents/{agent_id}/copy-to/{target_workspace_id}"
)
async def copy_agent_to(
    workspace_id: str,
    agent_id: str,
    target_workspace_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_permission("admin:workspaces:write")),
):
    """Copy an agent config from one workspace to another (same tenant).

    Produces a new AgentConfig row with a fresh id, ``workspace_id`` set to
    the target, and the same name / framework / config / structured fields
    as the source. The source row is left untouched (deep copy — mutations
    after the copy do not propagate). An ``agent.copy`` AuditLog entry is
    written against the destination workspace.

    Cross-workspace isolation: looking up the source agent under a
    workspace it doesn't belong to returns 404 (no leak). Target workspace
    must exist and belong to the same tenant as the caller.
    """
    user = request.state.user
    tenant_id = user.get("tenant_id", "")
    user_id = user.get("sub") or user.get("id", "")

    # 1. Source agent must exist in the path's workspace (cross-ws → 404).
    result = await db.execute(
        select(AgentConfig).where(
            AgentConfig.id == agent_id,
            AgentConfig.workspace_id == workspace_id,
        )
    )
    source = result.scalar_one_or_none()
    if not source:
        return _not_found()

    # 2. Target workspace must exist and belong to the caller's tenant.
    target_ws = await db.get(Workspace, target_workspace_id)
    if not target_ws or target_ws.tenant_id != tenant_id:
        return JSONResponse(
            status_code=404,
            content={
                "error": {
                    "code": "NOT_FOUND",
                    "message": "Target workspace not found",
                }
            },
        )

    # 3. Deep-copy into a fresh row (including P3a structured fields).
    copy = AgentConfig(
        workspace_id=target_workspace_id,
        name=source.name,
        framework=source.framework,
        config=source.config or {},
        created_by=user_id,
        system_prompt=source.system_prompt,
        model=source.model,
        temperature=source.temperature,
        max_tokens=source.max_tokens,
        tools=source.tools,
        guardrails=source.guardrails,
        skills=source.skills,
        hooks=source.hooks,
        memory_config=source.memory_config,
        subagents=source.subagents or [],
        mcp_servers=source.mcp_servers or [],
    )
    db.add(copy)
    await db.flush()  # populate copy.id before audit log references it

    # 4. Audit log against the destination workspace.
    await _write_audit(
        db,
        tenant_id=tenant_id,
        workspace_id=target_workspace_id,
        user_id=user_id,
        action="agent.copy",
        target_id=copy.id,
        details={
            "source_agent_id": source.id,
            "source_workspace_id": workspace_id,
            "target_workspace_id": target_workspace_id,
            "name": source.name,
            "framework": source.framework,
        },
        ip_address=request.client.host if request.client else "",
    )
    await db.commit()
    await db.refresh(copy)
    return JSONResponse(status_code=201, content=_serialize_agent(copy))
