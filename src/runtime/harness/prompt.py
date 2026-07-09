"""P3b: PromptAssembler — system prompt assembly (full P2 version).

Assembles the final system prompt from:
1. Agent persona (``agent.system_prompt``) — always first
2. Loaded skills' instructions (from SkillRegistry — P2)
3. Available tools description (auto-generated from ToolEngine schemas)
4. Recalled long-term memories (from MemoryStore — P2, if enabled)
5. Workspace policy constraints (allowed models, rate limits)

The assembled prompt is set on ``ctx.working_memory["system_prompt"]``
and also returned for the adapter to use directly.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.runtime.harness.agents import AgentDefinition
    from src.runtime.harness.context import HarnessContext
    from src.runtime.harness.memory import MemoryRecord

logger = logging.getLogger(__name__)


class PromptAssembler:
    """Assembles the final system prompt from agent + skills + tools + memory."""

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

        # 2. Loaded skills' instructions (P2)
        if agent.skills and hasattr(ctx, "skills") and ctx.skills is not None:
            for skill_name in agent.skills:
                try:
                    skill = await ctx.skills.load(
                        skill_name, getattr(ctx, "workspace_id", None)
                    )
                    sections.append(
                        f"## Skill: {skill.name}\n{skill.instructions}"
                    )
                except Exception as exc:
                    logger.warning("Failed to load skill %r: %s", skill_name, exc)

        # 3. Available tools description
        if ctx.tool_engine is not None:
            tool_docs = await ctx.tool_engine.schemas(ctx.workspace_id)
            if tool_docs:
                sections.append(self._format_tools_section(tool_docs))

        # 4. Recalled long-term memories (P2)
        if (
            agent.memory
            and agent.memory.enable_long_term
            and ctx.memory is not None
        ):
            try:
                last_msg = ctx.working_memory.get("last_user_message", "")
                if last_msg:
                    memories = await ctx.memory.recall(
                        query=last_msg,
                        scope="user",
                        limit=agent.memory.recall_top_k,
                    )
                    if memories:
                        sections.append(self._format_memories_section(memories))
            except Exception as exc:
                logger.warning("Memory recall failed: %s", exc)

        # 5. Workspace policy constraints
        policy_section = self._format_policy_section(ctx)
        if policy_section:
            sections.append(policy_section)

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

    def _format_memories_section(self, memories: "list[MemoryRecord]") -> str:
        """Format recalled memories as a markdown section."""
        lines = ["## Relevant Memories", ""]
        for mem in memories:
            lines.append(f"- {mem.content}")
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
