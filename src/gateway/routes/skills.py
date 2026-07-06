"""P3a §6.4: Skill management API.

Lists skills available from the SkillRegistry (loaded from
``.agents/skills/*.md`` markdown files). Skills are read-only via API;
to add or modify skills, edit the markdown files.

Access: any workspace member (``skills:read``).
"""
from fastapi import APIRouter, Depends

from src.gateway.auth.rbac import require_permission
from src.runtime.harness.registry import get_registry

router = APIRouter()


@router.get("/api/v1/workspaces/{workspace_id}/skills")
async def list_skills(
    workspace_id: str,
    _ctx=Depends(require_permission("skills:read", workspace_id_param="workspace_id")),
):
    """List all available skills from the SkillRegistry."""
    skills = await get_registry().skills.list()
    return [
        {
            "name": s.name,
            "description": s.description,
            "tools": s.tools,
            "version": s.version,
        }
        for s in skills
    ]


@router.get("/api/v1/workspaces/{workspace_id}/skills/{name}")
async def get_skill(
    workspace_id: str,
    name: str,
    _ctx=Depends(require_permission("skills:read", workspace_id_param="workspace_id")),
):
    """Get a single skill by name, including its instructions."""
    from fastapi.responses import JSONResponse

    try:
        skill = await get_registry().skills.load(name)
    except KeyError:
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "NOT_FOUND", "message": f"Skill {name!r} not found"}},
        )
    return {
        "name": skill.name,
        "description": skill.description,
        "instructions": skill.instructions,
        "tools": skill.tools,
        "required_memory": skill.required_memory,
        "version": skill.version,
    }


@router.post("/api/v1/workspaces/{workspace_id}/skills/{name}/reload")
async def reload_skill(
    workspace_id: str,
    name: str,
    _ctx=Depends(require_permission("tools:write", workspace_id_param="workspace_id")),
):
    """Hot-reload a skill from disk (workspace_admin only)."""
    from fastapi.responses import JSONResponse

    try:
        skill = await get_registry().skills.reload(name)
    except KeyError:
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "NOT_FOUND", "message": f"Skill {name!r} not found"}},
        )
    return {
        "name": skill.name,
        "description": skill.description,
        "version": skill.version,
    }
