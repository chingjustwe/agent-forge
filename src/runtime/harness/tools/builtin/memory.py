"""save_memory / recall_memory — long-term memory tools.

In P0 these operate on ``ctx.working_memory["memory"]`` (a per-run
dict) because the ``MemoryStore`` abstraction lands in P2. The handler
signature is fixed now so adapters can call them; only the backend
changes later (swap in ``SQLiteMemoryStore`` without touching callers).

The shape of stored records matches ``MemoryRecord`` in the spec so
the P2 migration is a pure storage swap.
"""
from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.runtime.harness.context import HarnessContext


_VALID_SCOPES = {"session", "user", "workspace", "agent"}


def _bucket(ctx: "HarnessContext") -> dict[str, list[dict]]:
    """Return (creating if needed) the per-run memory bucket dict."""
    return ctx.working_memory.setdefault("memory", {})


async def save(args: dict, ctx: "HarnessContext") -> dict:
    content = args.get("content", "")
    if not isinstance(content, str) or not content.strip():
        return {"output": "", "error": "content must be a non-empty string"}

    scope = args.get("scope", "session")
    if scope not in _VALID_SCOPES:
        return {"output": "", "error": f"scope must be one of {sorted(_VALID_SCOPES)}"}

    key = args.get("key") or ""
    record = {
        "id": uuid.uuid4().hex[:32],
        "scope": scope,
        "scope_id": _scope_id(ctx, scope),
        "key": str(key),
        "content": content,
        "metadata": {},
        "created_at": time.time(),
    }

    bucket = _bucket(ctx)
    bucket.setdefault(scope, []).append(record)
    return {
        "output": f"Saved memory record {record['id']} (scope={scope}).",
        "metadata": {"id": record["id"], "scope": scope},
    }


async def recall(args: dict, ctx: "HarnessContext") -> dict:
    query = args.get("query", "")
    if not isinstance(query, str) or not query.strip():
        return {"output": "", "error": "query must be a non-empty string"}

    scope = args.get("scope", "session")
    if scope not in _VALID_SCOPES:
        return {"output": "", "error": f"scope must be one of {sorted(_VALID_SCOPES)}"}

    limit = args.get("limit", 5)
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 5
    limit = max(1, min(limit, 50))

    bucket = _bucket(ctx)
    records = bucket.get(scope, [])

    # P0: naive substring search (case-insensitive). P2 swaps in FTS5.
    q_lower = query.lower()
    matched = [r for r in records if q_lower in r["content"].lower()]
    # Most recent first.
    matched.sort(key=lambda r: r.get("created_at", 0), reverse=True)
    matched = matched[:limit]

    return {
        "output": str(matched),
        "metadata": {"count": len(matched), "scope": scope},
    }


def _scope_id(ctx: "HarnessContext", scope: str) -> str:
    """Resolve scope_id from ctx identity fields."""
    if scope == "session":
        return ctx.session_id
    if scope == "user":
        return ctx.user_id
    if scope == "workspace":
        return ctx.workspace_id
    if scope == "agent":
        return ctx.agent.id
    return ""
