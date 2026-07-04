"""P2-2: Workspace-scoped agent configurations.

Each agent config is bound to a workspace and references a framework
(``direct_llm`` / ``adk`` / ``langgraph``). The ``config`` JSON holds
framework-specific settings (model, system_prompt, temperature, tools, ...).

Access rules:
- Reads (list / detail): any workspace member.
- Mutations (create / patch / delete): ``workspace_admin`` / ``workspace_owner``
  (and ``tenant_admin`` short-circuits to owner).

Cross-workspace isolation: every query filters on ``workspace_id`` AND
``id``, so an agent from another workspace is never visible (returns 404).
"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.gateway.auth.rbac import require_workspace_role
from src.infra.db.models import AgentConfig, AuditLog
from src.infra.db.session import get_db

router = APIRouter()

ALLOWED_FRAMEWORKS = ("direct_llm", "adk", "langgraph")
# All workspace-level roles that may read agent configs. ``tenant_admin``
# short-circuits in RBAC and is not listed here.
_READ_ROLES = ("viewer", "member", "workspace_admin", "workspace_owner")
_WRITE_ROLES = ("workspace_admin", "workspace_owner")


class CreateAgentRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    framework: str
    config: dict = Field(default_factory=dict)


class UpdateAgentRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    framework: str | None = None
    config: dict | None = None


def _serialize_agent(a: AgentConfig) -> dict:
    return {
        "id": a.id,
        "workspace_id": a.workspace_id,
        "name": a.name,
        "framework": a.framework,
        "config": a.config or {},
        "created_by": a.created_by,
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "updated_at": a.updated_at.isoformat() if a.updated_at else None,
    }


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


@router.post("/api/v1/workspaces/{workspace_id}/agents")
async def create_agent(
    workspace_id: str,
    body: CreateAgentRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _ctx=Depends(require_workspace_role("workspace_id", *_WRITE_ROLES)),
):
    """Create a new agent config in this workspace."""
    if body.framework not in ALLOWED_FRAMEWORKS:
        return _bad_request(
            f"framework must be one of {ALLOWED_FRAMEWORKS}"
        )

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
    _ctx=Depends(require_workspace_role("workspace_id", *_READ_ROLES)),
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
    _ctx=Depends(require_workspace_role("workspace_id", *_READ_ROLES)),
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
    _ctx=Depends(require_workspace_role("workspace_id", *_WRITE_ROLES)),
):
    """Update name / framework / config. Cross-workspace lookups return 404."""
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
    _ctx=Depends(require_workspace_role("workspace_id", *_WRITE_ROLES)),
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
