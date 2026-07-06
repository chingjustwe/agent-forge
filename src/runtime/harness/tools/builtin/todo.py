"""todo_write / todo_read — agent task-list management.

Task state lives in ``ctx.working_memory["todos"]`` so it is scoped to
the current run and snapshot-able by the checkpoint store.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.runtime.harness.context import HarnessContext


_VALID_STATUSES = {"pending", "in_progress", "completed"}


async def write(args: dict, ctx: "HarnessContext") -> dict:
    """Replace the task list. Validates each task's status field."""
    todos = args.get("todos") or []
    if not isinstance(todos, list):
        return {"output": "", "error": "todos must be an array"}

    cleaned: list[dict] = []
    for idx, item in enumerate(todos):
        if not isinstance(item, dict):
            return {"output": "", "error": f"todo[{idx}] must be an object"}
        content = item.get("content", "")
        status = item.get("status", "pending")
        if status not in _VALID_STATUSES:
            return {
                "output": "",
                "error": f"todo[{idx}].status must be one of {sorted(_VALID_STATUSES)}",
            }
        cleaned.append({"content": str(content), "status": status})

    ctx.working_memory["todos"] = cleaned
    return {
        "output": f"Replaced task list with {len(cleaned)} item(s).",
        "metadata": {"count": len(cleaned)},
    }


async def read(args: dict, ctx: "HarnessContext") -> dict:
    """Return the current task list as a JSON string."""
    todos = ctx.working_memory.get("todos", [])
    return {
        "output": json.dumps(todos, ensure_ascii=False),
        "metadata": {"count": len(todos)},
    }
