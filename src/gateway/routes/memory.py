"""P3a §6.5: Memory management API.

Workspace-scoped long-term memory records. Members can save and recall
memories; workspace_admin can delete any record.

Access:
- Reads + writes: ``member``+ (``memory:read`` / ``memory:write``).
- Deletes: ``member``+ (``memory:write``).
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from src.gateway.auth.rbac import require_permission
from src.runtime.harness.memory import MemoryScope, MemoryStore, SQLiteMemoryStore
from src.runtime.harness.registry import get_registry

router = APIRouter()


class MemoryOut(BaseModel):
    id: str
    scope: str
    key: str
    content: str
    created_at: str | None = None


class CreateMemoryRequest(BaseModel):
    key: str = ""
    content: str = Field(..., min_length=1)
    scope: str = Field(default="session")
    metadata: dict = Field(default_factory=dict)


def _get_memory_store() -> MemoryStore:
    store = get_registry().memory
    if store is None:
        store = SQLiteMemoryStore()
    return store


@router.get("/api/v1/workspaces/{workspace_id}/memory")
async def list_memory(
    workspace_id: str,
    scope: str = "user",
    limit: int = 50,
    request_user=Depends(require_permission("memory:read", workspace_id_param="workspace_id")),
):
    """List memory records for the current user in this workspace."""
    if scope not in ("session", "user", "workspace", "agent"):
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "VALIDATION_ERROR", "message": "scope must be session|user|workspace|agent"}},
        )
    limit = max(1, min(limit, 500))
    store = _get_memory_store()
    user_id = request_user.get("sub") or request_user.get("id", "")
    scope_id = user_id if scope == "user" else workspace_id
    records = await store.list(scope=scope, scope_id=scope_id, limit=limit)
    return [
        MemoryOut(
            id=r.id,
            scope=r.scope,
            key=r.key,
            content=r.content,
            created_at=r.created_at.isoformat() if r.created_at else None,
        ).model_dump()
        for r in records
    ]


@router.post("/api/v1/workspaces/{workspace_id}/memory")
async def create_memory(
    workspace_id: str,
    body: CreateMemoryRequest,
    request_user=Depends(require_permission("memory:write", workspace_id_param="workspace_id")),
):
    """Save a memory record."""
    if body.scope not in ("session", "user", "workspace", "agent"):
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "VALIDATION_ERROR", "message": "scope must be session|user|workspace|agent"}},
        )
    store = _get_memory_store()
    user_id = request_user.get("sub") or request_user.get("id", "")
    scope_id = user_id if body.scope == "user" else workspace_id

    from src.runtime.harness.memory import MemoryRecord

    record = MemoryRecord(
        id=uuid.uuid4().hex[:32],
        scope=body.scope,
        scope_id=scope_id,
        key=body.key,
        content=body.content,
        metadata=body.metadata,
        created_at=datetime.now(timezone.utc),
    )
    record_id = await store.save(record)
    return JSONResponse(
        status_code=201,
        content={"id": record_id},
    )


@router.delete("/api/v1/workspaces/{workspace_id}/memory/{record_id}")
async def delete_memory(
    workspace_id: str,
    record_id: str,
    _ctx=Depends(require_permission("memory:write", workspace_id_param="workspace_id")),
):
    """Delete a memory record by id."""
    store = _get_memory_store()
    existing = await store.get(record_id)
    if existing is None:
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "NOT_FOUND", "message": "Memory record not found"}},
        )
    await store.delete(record_id)
    return Response(status_code=204)


@router.post("/api/v1/workspaces/{workspace_id}/memory/search")
async def search_memory(
    workspace_id: str,
    body: dict,
    request_user=Depends(require_permission("memory:read", workspace_id_param="workspace_id")),
):
    """Full-text search memory records."""
    query = body.get("query", "")
    scope = body.get("scope", "user")
    limit = body.get("limit", 5)
    if scope not in ("session", "user", "workspace", "agent"):
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "VALIDATION_ERROR", "message": "scope must be session|user|workspace|agent"}},
        )
    if not query:
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "VALIDATION_ERROR", "message": "query must not be empty"}},
        )
    limit = max(1, min(int(limit), 50))
    store = _get_memory_store()
    user_id = request_user.get("sub") or request_user.get("id", "")
    scope_id = user_id if scope == "user" else workspace_id
    records = await store.recall(query=query, scope=scope, scope_id=scope_id, limit=limit)
    return [
        MemoryOut(
            id=r.id,
            scope=r.scope,
            key=r.key,
            content=r.content,
            created_at=r.created_at.isoformat() if r.created_at else None,
        ).model_dump()
        for r in records
    ]
