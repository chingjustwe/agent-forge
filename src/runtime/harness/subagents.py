"""Phase 4b: SubagentSpec model + SubagentMapper.

``SubagentSpec`` was added to ``agents.py`` in Phase 4a (it lives there
because it's part of the ``AgentDefinition`` aggregate). This module
hosts the mapper that converts a list of specs into the dict shape that
``deepagents.create_deep_agent`` expects.

Per spec D9: subagent tool inheritance is disabled. If ``spec.tools``
is empty the subagent receives NO tools (not the parent's set). This
is the opposite of deepagents' default behavior.

Per spec D6: tool names in ``spec.tools`` are resolved to
``LangChainToolShim`` instances against the parent's ``ToolEngine``,
so the same sandbox / guardrail / hook pipeline applies inside
subagents.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.runtime.harness.agents import SubagentSpec
    from src.runtime.harness.context import HarnessContext


class SubagentMapper:
    """Maps our ``SubagentSpec`` list → deepagents SubAgent dict list.

    The output dict shape matches what ``deepagents.create_deep_agent``
    accepts under the ``subagents=`` parameter. Tools are bridged via
    ``LangChainToolShim`` so the Phase 3a pipeline still runs.

    Unknown tool names (not in the registry) are silently dropped — a
    misconfigured subagent should not crash the parent run.
    """

    @classmethod
    def to_subagents(
        cls,
        specs: "list[SubagentSpec]",
        ctx: "HarnessContext",
    ) -> list[dict]:
        from src.runtime.harness.langgraph_shims import LangChainToolShim

        out: list[dict] = []
        for spec in specs:
            # Resolve each tool name → LangChainToolShim bound to the
            # parent's ToolEngine (so sandbox/guardrail/hooks apply).
            tools: list[Any] = []
            if ctx.tool_engine is not None:
                for tool_name in spec.tools:
                    tool_def = ctx.tool_engine._registry.get(
                        tool_name, ctx.workspace_id
                    )
                    if tool_def is not None:
                        tools.append(LangChainToolShim(tool_def, ctx))
                    # Unknown tool names are silently dropped.

            entry: dict[str, Any] = {
                "name": spec.name,
                "description": spec.description,
                "system_prompt": spec.system_prompt,
                "tools": tools,  # Empty list = no tools (spec D9)
            }
            # model omitted → deepagents inherits parent's model
            if spec.model:
                entry["model"] = spec.model
            out.append(entry)
        return out
