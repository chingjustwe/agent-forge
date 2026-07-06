"""Tests for PromptAssembler.

Covers:
- Empty agent → empty prompt
- Persona-only, tools-only, persona + tools (separator "---")
- Workspace policy section: populated, empty dict, non-dict value
- system_prompt stored into ctx.working_memory
- Missing tool_engine does not crash and skips the tools section
"""
import pytest

from src.runtime.harness.agents import AgentDefinition
from src.runtime.harness.context import HarnessContext
from src.runtime.harness.prompt import PromptAssembler
from src.runtime.harness.tool_engine import (
    ToolDefinition,
    ToolEngine,
    ToolRegistry,
)
from src.runtime.harness.tools import BUILTIN_HANDLERS


def _make_agent(system_prompt="", tools=None):
    return AgentDefinition(
        id="a-1",
        name="test",
        workspace_id="ws-1",
        system_prompt=system_prompt,
        tools=tools or [],
        adapter="direct_llm",
    )


def _make_ctx(agent, tool_engine=None, workspace_settings=None):
    return HarnessContext(
        workspace_id="ws-1",
        user_id="u-1",
        session_id="s-1",
        trace_id="t-1",
        agent=agent,
        tool_engine=tool_engine,
        workspace_settings=workspace_settings or {},
    )


def _make_tool_engine(allowed_tools):
    registry = ToolRegistry()
    # Register some tools so schemas() returns them.
    from src.runtime.harness.tools import BUILTIN_TOOL_DEFINITIONS
    for td in BUILTIN_TOOL_DEFINITIONS:
        registry.register(td)
    return ToolEngine(
        registry=registry,
        allowed_tools=allowed_tools,
        builtin_handlers=BUILTIN_HANDLERS,
    )


class TestPromptAssembler:
    @pytest.mark.asyncio
    async def test_empty_agent_empty_prompt(self):
        agent = _make_agent()
        ctx = _make_ctx(agent)
        assembler = PromptAssembler()
        prompt = await assembler.assemble(agent, ctx)
        assert prompt == ""

    @pytest.mark.asyncio
    async def test_persona_only(self):
        agent = _make_agent(system_prompt="You are a helpful assistant.")
        ctx = _make_ctx(agent)
        assembler = PromptAssembler()
        prompt = await assembler.assemble(agent, ctx)
        assert prompt == "You are a helpful assistant."

    @pytest.mark.asyncio
    async def test_persona_and_tools(self):
        agent = _make_agent(
            system_prompt="You are a helpful assistant.",
            tools=["todo_write"],
        )
        engine = _make_tool_engine(["todo_write"])
        ctx = _make_ctx(agent, tool_engine=engine)
        assembler = PromptAssembler()
        prompt = await assembler.assemble(agent, ctx)
        assert "You are a helpful assistant." in prompt
        assert "## Available Tools" in prompt
        # Sections must be separated by a horizontal rule.
        assert "\n\n---\n\n" in prompt
        # Persona comes before tools.
        assert prompt.index("You are a helpful assistant.") < prompt.index(
            "## Available Tools"
        )

    @pytest.mark.asyncio
    async def test_tools_only(self):
        agent = _make_agent(tools=["todo_write"])
        engine = _make_tool_engine(["todo_write"])
        ctx = _make_ctx(agent, tool_engine=engine)
        assembler = PromptAssembler()
        prompt = await assembler.assemble(agent, ctx)
        assert prompt.startswith("## Available Tools")
        assert "You are a helpful assistant." not in prompt

    @pytest.mark.asyncio
    async def test_policy_section(self):
        agent = _make_agent(system_prompt="You are a helpful assistant.")
        ctx = _make_ctx(
            agent,
            workspace_settings={
                "policy": {"allowed_models": ["deepseek-chat"]}
            },
        )
        assembler = PromptAssembler()
        prompt = await assembler.assemble(agent, ctx)
        assert "## Workspace Policy" in prompt
        assert "deepseek-chat" in prompt
        assert "Allowed models" in prompt

    @pytest.mark.asyncio
    async def test_policy_empty(self):
        agent = _make_agent(system_prompt="You are a helpful assistant.")
        ctx = _make_ctx(
            agent,
            workspace_settings={"policy": {}},
        )
        assembler = PromptAssembler()
        prompt = await assembler.assemble(agent, ctx)
        assert "## Workspace Policy" not in prompt
        # Persona still present.
        assert prompt == "You are a helpful assistant."

    @pytest.mark.asyncio
    async def test_policy_non_dict(self):
        agent = _make_agent(system_prompt="You are a helpful assistant.")
        ctx = _make_ctx(
            agent,
            workspace_settings={"policy": "not-a-dict"},
        )
        assembler = PromptAssembler()
        prompt = await assembler.assemble(agent, ctx)
        # Non-dict policy is treated as empty — no policy section.
        assert "## Workspace Policy" not in prompt
        assert prompt == "You are a helpful assistant."

    @pytest.mark.asyncio
    async def test_prompt_stored_in_working_memory(self):
        agent = _make_agent(system_prompt="You are a helpful assistant.")
        ctx = _make_ctx(agent)
        assembler = PromptAssembler()
        prompt = await assembler.assemble(agent, ctx)
        assert ctx.working_memory["system_prompt"] == prompt

    @pytest.mark.asyncio
    async def test_no_tool_engine(self):
        agent = _make_agent(system_prompt="You are a helpful assistant.")
        ctx = _make_ctx(agent, tool_engine=None)
        assembler = PromptAssembler()
        # Must not raise even though tool_engine is None.
        prompt = await assembler.assemble(agent, ctx)
        assert "## Available Tools" not in prompt
        assert prompt == "You are a helpful assistant."
