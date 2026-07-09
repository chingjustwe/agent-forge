"""Skill management API (Skills layers spec).

Aggregates skills across three layers:

- ``user``    — read-only directory (``settings.skill_user_dir``)
- ``project`` — read-only directory (``agents/skills``)
- ``workspace`` — writable, per-workspace skills via ``SkillStore``

Read access requires ``skills:read``; create / update / delete require
``skills:write`` (workspace_admin / tenant_admin). Directory-layer skills
are read-only — write operations against them return 403.
"""
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src.gateway.auth.rbac import require_permission
from src.runtime.harness.registry import get_registry
from src.runtime.harness.skills import SKILL_NAME_RE, SkillPackage

router = APIRouter()


def _info(s: SkillPackage) -> dict:
    return {
        "name": s.name,
        "description": s.description,
        "tools": s.tools,
        "version": s.version,
        "layer": s.layer,
        "editable": s.editable,
        "workspace_id": s.workspace_id,
    }


def _detail(s: SkillPackage) -> dict:
    return {
        **_info(s),
        "instructions": s.instructions,
        "required_memory": s.required_memory,
    }


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message}},
    )


class SkillCreate(BaseModel):
    name: str
    description: str = ""
    instructions: str = ""
    tools: list[str] = Field(default_factory=list)
    required_memory: bool = False
    version: str = "1.0"


class SkillUpdate(BaseModel):
    description: str | None = None
    instructions: str | None = None
    tools: list[str] | None = None
    required_memory: bool | None = None
    version: str | None = None


@router.get("/api/v1/workspaces/{workspace_id}/skills")
async def list_skills(
    workspace_id: str,
    _ctx=Depends(require_permission("skills:read", workspace_id_param="workspace_id")),
):
    """List all skills available to this workspace across all three layers."""
    skills = await get_registry().skills.list(workspace_id)
    return [_info(s) for s in skills]


@router.get("/api/v1/workspaces/{workspace_id}/skills/{name}")
async def get_skill(
    workspace_id: str,
    name: str,
    _ctx=Depends(require_permission("skills:read", workspace_id_param="workspace_id")),
):
    """Get a single skill by name (workspace > project > user priority)."""
    try:
        skill = await get_registry().skills.load(name, workspace_id)
    except KeyError:
        return _error(404, "NOT_FOUND", f"Skill {name!r} not found")
    return _detail(skill)


@router.post("/api/v1/workspaces/{workspace_id}/skills")
async def create_skill(
    workspace_id: str,
    body: SkillCreate,
    _ctx=Depends(require_permission("skills:write", workspace_id_param="workspace_id")),
):
    """Create a new workspace-level skill (default: DB backend)."""
    store = get_registry().skills.store
    if store is None:
        return _error(400, "NO_STORE", "No writable skill store configured")
    if not SKILL_NAME_RE.match(body.name or ""):
        return _error(
            400,
            "INVALID_NAME",
            "Skill name must match [a-z0-9_-]+",
        )
    if await store.exists(workspace_id, body.name):
        return _error(
            409, "CONFLICT", f"Skill {body.name!r} already exists in this workspace"
        )
    pkg = SkillPackage(
        name=body.name,
        description=body.description,
        instructions=body.instructions,
        tools=body.tools,
        required_memory=body.required_memory,
        version=body.version,
        layer="workspace",
        editable=True,
        workspace_id=workspace_id,
    )
    saved = await store.save(workspace_id, pkg)
    return JSONResponse(status_code=201, content=_detail(saved))


@router.put("/api/v1/workspaces/{workspace_id}/skills/{name}")
async def update_skill(
    workspace_id: str,
    name: str,
    body: SkillUpdate,
    _ctx=Depends(require_permission("skills:write", workspace_id_param="workspace_id")),
):
    """Update an existing workspace-level skill. Directory layers → 403."""
    store = get_registry().skills.store
    if store is None:
        return _error(400, "NO_STORE", "No writable skill store configured")
    existing = await store.get(workspace_id, name)
    if existing is None:
        return _error(
            403,
            "READ_ONLY",
            f"Skill {name!r} is not editable (only workspace skills can be edited)",
        )
    updated = existing.model_copy(
        update={
            k: v
            for k, v in body.model_dump(exclude_none=True).items()
        }
    )
    saved = await store.save(workspace_id, updated)
    return _detail(saved)


@router.delete("/api/v1/workspaces/{workspace_id}/skills/{name}")
async def delete_skill(
    workspace_id: str,
    name: str,
    _ctx=Depends(require_permission("skills:write", workspace_id_param="workspace_id")),
):
    """Delete a workspace-level skill. Directory layers → 403."""
    store = get_registry().skills.store
    if store is None:
        return _error(400, "NO_STORE", "No writable skill store configured")
    if not await store.exists(workspace_id, name):
        return _error(
            403,
            "READ_ONLY",
            f"Skill {name!r} is not deletable (only workspace skills can be deleted)",
        )
    await store.delete(workspace_id, name)
    return {"ok": True}


@router.post("/api/v1/workspaces/{workspace_id}/skills/{name}/reload")
async def reload_skill(
    workspace_id: str,
    name: str,
    _ctx=Depends(require_permission("skills:write", workspace_id_param="workspace_id")),
):
    """Hot-reload a directory-layer skill from disk (workspace_admin only).

    Workspace-layer skills are served live from the store and cannot be
    reloaded → 400.
    """
    registry = get_registry()
    store = registry.skills.store
    if store is not None and await store.exists(workspace_id, name):
        return _error(
            400,
            "NOT_RELOADABLE",
            f"Skill {name!r} is a workspace skill and does not need reloading",
        )
    try:
        skill = await registry.skills.reload(name)
    except KeyError:
        return _error(404, "NOT_FOUND", f"Skill {name!r} not found")
    return {
        "name": skill.name,
        "description": skill.description,
        "version": skill.version,
        "layer": skill.layer,
        "editable": skill.editable,
        "workspace_id": skill.workspace_id,
    }
