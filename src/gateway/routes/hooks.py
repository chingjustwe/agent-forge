"""P3a §6.7: Hook listing API.

Lists the lifecycle hooks currently registered in the harness ``HookRegistry``
(audit_log / metric / trace, plus any custom hooks). Read-only: hooks are
enabled per-agent via the agent ``hooks`` whitelist, not created via API.

Access: any workspace member (``hooks:read``).
"""
from fastapi import APIRouter, Depends

from src.gateway.auth.rbac import require_permission
from src.runtime.harness.registry import get_registry

router = APIRouter()

# Human-readable descriptions for the built-in hooks. Custom hooks fall back
# to a generic descriptor built from their name.
HOOK_DESCRIPTIONS: dict[str, str] = {
    "audit_log": "Records run / tool / error events to the audit log.",
    "metric": "Increments counters and records tool-call durations.",
    "trace": "Opens and closes OpenTelemetry spans around events.",
}


@router.get("/api/v1/workspaces/{workspace_id}/hooks")
async def list_hooks(
    workspace_id: str,
    _ctx=Depends(require_permission("hooks:read", workspace_id_param="workspace_id")),
):
    """List all available lifecycle hooks registered in the registry."""
    registry = get_registry().hooks
    return [
        {
            "name": h.name,
            "events": list(getattr(h, "events", [])),
            "description": HOOK_DESCRIPTIONS.get(
                h.name, f"Lifecycle hook: {h.name}"
            ),
        }
        for h in registry.list()
    ]
