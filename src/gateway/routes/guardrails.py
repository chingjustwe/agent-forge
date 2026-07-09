"""P3a §6.6: Guardrail management API.

Workspace-scoped guardrail configurations: list, create, update, delete.
Guardrails are stored in-memory in the ``GuardrailPipeline``; P3 will
add DB persistence via ``guardrail_configs`` table.

Access:
- Reads: any workspace member (``guardrails:read``).
- Mutations: ``workspace_admin`` (``guardrails:write``).
"""
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from src.gateway.auth.rbac import require_permission
from src.runtime.harness.guardrails import (
    ContentFilterGuardrail,
    GuardrailPipeline,
    PIIRedactionGuardrail,
    PolicyGuardrail,
)
from src.runtime.harness.registry import get_registry

router = APIRouter()


class GuardrailOut(BaseModel):
    name: str
    direction: str
    type: str = ""
    description: str = ""


class CreateGuardrailRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    type: str = Field(..., description="content_filter|pii_redaction|policy")
    direction: str = Field(default="both")
    patterns: list[str] = Field(default_factory=list)
    action: str = Field(default="block")


@router.get("/api/v1/workspaces/{workspace_id}/guardrails")
async def list_guardrails(
    workspace_id: str,
    _ctx=Depends(require_permission("guardrails:read", workspace_id_param="workspace_id")),
):
    """List all registered guardrails."""
    pipeline = get_registry().guardrails
    return [
        GuardrailOut(
            name=g.name,
            direction=getattr(g, "direction", "both"),
            type=_infer_type(g),
            description=_infer_description(g, _infer_type(g)),
        ).model_dump()
        for g in pipeline.list()
    ]


@router.post("/api/v1/workspaces/{workspace_id}/guardrails")
async def create_guardrail(
    workspace_id: str,
    body: CreateGuardrailRequest,
    _ctx=Depends(require_permission("guardrails:write", workspace_id_param="workspace_id")),
):
    """Register a new guardrail."""
    pipeline = get_registry().guardrails
    # Check for name conflict
    existing = {g.name for g in pipeline.list()}
    if body.name in existing:
        return JSONResponse(
            status_code=409,
            content={"error": {"code": "CONFLICT", "message": f"Guardrail {body.name!r} already exists"}},
        )

    if body.type == "content_filter":
        guardrail = ContentFilterGuardrail(patterns=body.patterns)
        guardrail.name = body.name
    elif body.type == "pii_redaction":
        guardrail = PIIRedactionGuardrail()
        guardrail.name = body.name
    elif body.type == "policy":
        guardrail = PolicyGuardrail()
        guardrail.name = body.name
    else:
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "VALIDATION_ERROR", "message": f"Unknown guardrail type: {body.type!r}"}},
        )

    pipeline.add(guardrail)
    return JSONResponse(
        status_code=201,
        content=GuardrailOut(
            name=guardrail.name,
            direction=getattr(guardrail, "direction", "both"),
            type=body.type,
        ).model_dump(),
    )


@router.delete("/api/v1/workspaces/{workspace_id}/guardrails/{name}")
async def delete_guardrail(
    workspace_id: str,
    name: str,
    _ctx=Depends(require_permission("guardrails:write", workspace_id_param="workspace_id")),
):
    """Remove a guardrail by name."""
    pipeline = get_registry().guardrails
    existing = {g.name for g in pipeline.list()}
    if name not in existing:
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "NOT_FOUND", "message": f"Guardrail {name!r} not found"}},
        )
    pipeline.remove(name)
    return Response(status_code=204)


def _infer_type(guardrail) -> str:
    """Infer the type string from a guardrail instance."""
    cls_name = type(guardrail).__name__.lower()
    if "content" in cls_name:
        return "content_filter"
    if "pii" in cls_name:
        return "pii_redaction"
    if "policy" in cls_name:
        return "policy"
    if "quota" in cls_name:
        return "quota"
    return "custom"


def _infer_description(guardrail, gtype: str) -> str:
    """Best-effort human-readable description for the frontend picker."""
    direction = getattr(guardrail, "direction", "both")
    base = {
        "content_filter": "Blocks messages matching forbidden keywords/patterns.",
        "pii_redaction": "Redacts emails, phone numbers, and SSNs from messages.",
        "policy": "Enforces per-workspace model/tool allow-list policy.",
        "quota": "Enforces workspace token/cost quota limits.",
        "custom": f"Custom guardrail: {getattr(guardrail, 'name', gtype)}.",
    }.get(gtype, f"Guardrail: {getattr(guardrail, 'name', gtype)}.")
    return f"{base} (applies to {direction})"
