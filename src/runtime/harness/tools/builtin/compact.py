"""compact — conversation history compression.

The agent calls this with a summary; the harness records the summary in
``ctx.working_memory`` so the runtime / checkpoint store can replace
prior messages with the summary on the next state snapshot.

Actual message-history mutation is deferred to the runtime layer (P1),
since it requires coordination with the adapter's message buffer. This
handler just stores the summary and signals that compaction occurred.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.runtime.harness.context import HarnessContext


async def run(args: dict, ctx: "HarnessContext") -> dict:
    summary = args.get("summary", "")
    if not isinstance(summary, str) or not summary.strip():
        return {"output": "", "error": "summary must be a non-empty string"}

    ctx.working_memory["compact_summary"] = summary
    ctx.working_memory["compacted"] = True
    return {
        "output": (
            "Conversation compacted. Prior messages will be replaced by the "
            "summary on the next checkpoint. Continue as if the history was "
            "summarized."
        ),
        "metadata": {"summary_length": len(summary)},
    }
