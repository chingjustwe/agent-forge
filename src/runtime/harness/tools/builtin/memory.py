"""save_memory / recall_memory — long-term memory tools.

P2: routes through ``MemoryScope`` (SQLiteMemoryStore + FTS5) when
available in the context. Falls back to ``ctx.working_memory["memory"]``
(per-run dict) when no MemoryScope is configured (e.g., in tests or
when long-term memory is disabled for the agent).
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
    memory_type = args.get("memory_type", "episodic")
    if memory_type not in ("profile", "episodic"):
        return {
            "output": "",
            "error": "memory_type must be 'profile' or 'episodic'",
        }

    # P2: route through MemoryScope if available
    if ctx.memory is not None:
        try:
            record_id = await ctx.memory.remember(
                key=key,
                content=content,
                scope=scope,  # type: ignore
                metadata={},
                memory_type=memory_type,  # type: ignore
            )
            return {
                "output": (
                    f"Saved memory record {record_id} "
                    f"(scope={scope}, type={memory_type})."
                ),
                "metadata": {
                    "id": record_id,
                    "scope": scope,
                    "memory_type": memory_type,
                },
            }
        except Exception as exc:
            return {"output": "", "error": f"Memory store error: {exc}"}

    # Fallback: per-run working_memory (P0 behavior)
    record = {
        "id": uuid.uuid4().hex[:32],
        "scope": scope,
        "scope_id": _scope_id(ctx, scope),
        "key": str(key),
        "content": content,
        "memory_type": memory_type,
        "metadata": {},
        "created_at": time.time(),
    }

    bucket = _bucket(ctx)
    bucket.setdefault(scope, []).append(record)
    return {
        "output": (
            f"Saved memory record {record['id']} "
            f"(scope={scope}, type={memory_type})."
        ),
        "metadata": {
            "id": record["id"],
            "scope": scope,
            "memory_type": memory_type,
        },
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

    memory_type = args.get("memory_type")
    if memory_type is not None and memory_type not in ("profile", "episodic"):
        return {
            "output": "",
            "error": "memory_type must be 'profile' or 'episodic'",
        }

    # P2: route through MemoryScope if available
    if ctx.memory is not None:
        try:
            records = await ctx.memory.recall(
                query=query,
                scope=scope,  # type: ignore
                limit=limit,
                memory_type=memory_type,
            )
            result = [
                {
                    "id": r.id,
                    "scope": r.scope,
                    "scope_id": r.scope_id,
                    "key": r.key,
                    "content": r.content,
                    "memory_type": r.memory_type,
                    "metadata": r.metadata,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in records
            ]
            return {
                "output": str(result),
                "metadata": {"count": len(result), "scope": scope},
            }
        except Exception as exc:
            return {"output": "", "error": f"Memory store error: {exc}"}

    # Fallback: per-run working_memory (P0 behavior)
    bucket = _bucket(ctx)
    records = bucket.get(scope, [])

    q_lower = query.lower()
    matched = [r for r in records if q_lower in r["content"].lower()]
    if memory_type:
        matched = [r for r in matched if r.get("memory_type", "episodic") == memory_type]
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
