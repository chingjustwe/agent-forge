"""P3a-P1: PromptAssembler — system prompt assembly.

Assembles the final system prompt from:
1. Agent persona (``agent.system_prompt``) — always first
2. Available tools description (auto-generated from ToolEngine schemas)
3. Workspace policy constraints (allowed models, rate limits)

P2 will add:
- Skill instructions injection (from SkillRegistry)
- Recalled long-term memories (from MemoryStore)
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.runtime.harness.agents import AgentDefinition
    from src.runtime.harness.context import HarnessContext

logger = logging.getLogger(__name__)


class PromptAssembler:
    """Assembles the final system prompt from agent + tools + policy.

    Called by ``HarnessRuntime`` after building the ``HarnessContext``
    but before the adapter runs. The assembled prompt is set on
    ``ctx.working_memory["system_prompt"]`` and also returned for the
    adapter to use directly.
    """

    async def assemble(
        self,
        agent: "AgentDefinition",
        ctx: "HarnessContext",
    ) -> str:
        """Assemble the system prompt from all sections."""
        sections: list[str] = []

        # 1. Persona (agent.system_prompt) — always first
        if agent.system_prompt:
            sections.append(agent.system_prompt)

        # 2. Available tools description
        if ctx.tool_engine is not None:
            tool_docs = await ctx.tool_engine.schemas(ctx.workspace_id)
            if tool_docs:
                sections.append(self._format_tools_section(tool_docs))

        # 3. Workspace policy constraints
        policy_section = self._format_policy_section(ctx)
        if policy_section:
            sections.append(policy_section)

        # P2 will add:
        # - Skill instructions (from SkillRegistry)
        # - Recalled long-term memories (from MemoryStore)

        prompt = "\n\n---\n\n".join(sections) if sections else ""
        ctx.working_memory["system_prompt"] = prompt
        return prompt

    def _format_tools_section(self, tool_docs: list[dict]) -> str:
        """Format the available tools as a markdown section."""
        lines = ["## Available Tools", ""]
        for tool in tool_docs:
            name = tool.get("name", "")
            desc = tool.get("description", "")
            lines.append(f"- **{name}**: {desc}")
        return "\n".join(lines)

    def _format_policy_section(self, ctx: "HarnessContext") -> str:
        """Format workspace policy constraints as a markdown section."""
        policy = ctx.workspace_settings.get("policy", {})
        if not isinstance(policy, dict) or not policy:
            return ""

        lines = ["## Workspace Policy", ""]
        allowed_models = policy.get("allowed_models")
        if allowed_models and isinstance(allowed_models, list):
            lines.append(f"- Allowed models: {', '.join(allowed_models)}")
        allowed_tools = policy.get("allowed_tools")
        if allowed_tools and isinstance(allowed_tools, list):
            lines.append(f"- Allowed tools: {', '.join(allowed_tools)}")
        rate_limit = policy.get("rate_limit")
        if rate_limit:
            lines.append(f"- Rate limit: {rate_limit}")

        return "\n".join(lines) if len(lines) > 2 else ""
