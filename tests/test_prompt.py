"""Tests for PromptAssembler.

Covers:
- Empty agent → empty prompt
- Persona-only, tools-only, persona + tools (separator "---")
- Workspace policy section: populated, empty dict, non-dict value
- system_prompt stored into ctx.working_memory
- Missing tool_engine does not crash and skips the tools section
"""
import pytest

from src.runtime.harness.agents import AgentDefinition, MemoryConfig
from src.runtime.harness.context import HarnessContext
from src.runtime.harness.memory import MemoryRecord
from src.runtime.harness.prompt import PromptAssembler
from src.runtime.harness.skills import SkillPackage
from src.runtime.harness.tool_engine import (
    ToolDefinition,
    ToolEngine,
    ToolRegistry,
)
from src.runtime.harness.tools import BUILTIN_HANDLERS


def _make_agent(system_prompt="", tools=None, skills=None, memory=None):
    return AgentDefinition(
        id="a-1",
        name="test",
        workspace_id="ws-1",
        system_prompt=system_prompt,
        tools=tools or [],
        skills=skills or [],
        memory=memory,
        adapter="deepagents",
    )


def _make_ctx(
    agent,
    tool_engine=None,
    workspace_settings=None,
    memory=None,
    skills=None,
):
    return HarnessContext(
        workspace_id="ws-1",
        user_id="u-1",
        session_id="s-1",
        trace_id="t-1",
        agent=agent,
        tool_engine=tool_engine,
        workspace_settings=workspace_settings or {},
        memory=memory,
        skills=skills,
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
        # Tools are no longer injected into the system prompt (deepagents
        # passes tool schemas via the native tool-use API). Having tools
        # in the whitelist must not change the prompt text.
        agent = _make_agent(
            system_prompt="You are a helpful assistant.",
            tools=["todo_write"],
        )
        engine = _make_tool_engine(["todo_write"])
        ctx = _make_ctx(agent, tool_engine=engine)
        assembler = PromptAssembler()
        prompt = await assembler.assemble(agent, ctx)
        assert prompt == "You are a helpful assistant."
        assert "## Available Tools" not in prompt

    @pytest.mark.asyncio
    async def test_tools_only(self):
        # With no persona and no tools section, the prompt is empty.
        agent = _make_agent(tools=["todo_write"])
        engine = _make_tool_engine(["todo_write"])
        ctx = _make_ctx(agent, tool_engine=engine)
        assembler = PromptAssembler()
        prompt = await assembler.assemble(agent, ctx)
        assert prompt == ""
        assert "## Available Tools" not in prompt

    @pytest.mark.asyncio
    async def test_policy_section(self):
        agent = _make_agent(system_prompt="You are a helpful assistant.")
        ctx = _make_ctx(
            agent,
            workspace_settings={
                "policy": {"allowed_models": ["deepseek-v4-flash"]}
            },
        )
        assembler = PromptAssembler()
        prompt = await assembler.assemble(agent, ctx)
        assert "## Workspace Policy" in prompt
        assert "deepseek-v4-flash" in prompt
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


# ── P2: Skill injection ─────────────────────────────────────────────────


class _MockSkillRegistry:
    """Minimal SkillRegistry mock for PromptAssembler tests."""

    def __init__(
        self,
        skills: dict[str, SkillPackage] | None = None,
        ws_skills: dict[str, SkillPackage] | None = None,
    ):
        self._skills = skills or {}
        # Workspace-layer skills keyed by name — take priority when a
        # workspace_id is supplied (mirrors SkillRegistry resolution).
        self._ws_skills = ws_skills or {}

    async def load(
        self, name: str, workspace_id: str | None = None
    ) -> SkillPackage:
        if workspace_id and name in self._ws_skills:
            return self._ws_skills[name]
        if name not in self._skills:
            raise KeyError(f"Skill {name!r} not found")
        return self._skills[name]


class TestPromptAssemblerSkills:
    """P2: skill injection into the system prompt."""

    @pytest.mark.asyncio
    async def test_skill_injected_when_agent_has_skills(self):
        skill = SkillPackage(
            name="my-skill",
            description="A test skill",
            instructions="Always respond with kindness.",
        )
        agent = _make_agent(
            system_prompt="You are a helpful assistant.",
            skills=["my-skill"],
        )
        ctx = _make_ctx(agent, skills=_MockSkillRegistry({"my-skill": skill}))
        assembler = PromptAssembler()
        prompt = await assembler.assemble(agent, ctx)
        assert "## Skill: my-skill" in prompt
        assert "Always respond with kindness." in prompt
        # Persona still present and comes first.
        assert "You are a helpful assistant." in prompt
        assert prompt.index("You are a helpful assistant.") < prompt.index(
            "## Skill: my-skill"
        )

    @pytest.mark.asyncio
    async def test_skill_missing_silently_skipped(self):
        # No SkillRegistry wired (ctx.skills is None) — must not crash,
        # and no skill section should appear even though agent.skills is set.
        agent = _make_agent(
            system_prompt="You are a helpful assistant.",
            skills=["nonexistent"],
        )
        ctx = _make_ctx(agent, skills=None)
        assembler = PromptAssembler()
        prompt = await assembler.assemble(agent, ctx)
        assert "## Skill" not in prompt
        assert prompt == "You are a helpful assistant."

    @pytest.mark.asyncio
    async def test_skill_load_error_silently_skipped(self):
        # SkillRegistry.load raises — should be caught and skipped silently.
        class _BrokenRegistry:
            async def load(self, name: str) -> SkillPackage:
                raise RuntimeError("disk on fire")

        agent = _make_agent(
            system_prompt="You are a helpful assistant.",
            skills=["broken"],
        )
        ctx = _make_ctx(agent, skills=_BrokenRegistry())
        assembler = PromptAssembler()
        prompt = await assembler.assemble(agent, ctx)
        assert "## Skill" not in prompt
        assert "You are a helpful assistant." in prompt

    @pytest.mark.asyncio
    async def test_skills_section_after_persona(self):
        skill = SkillPackage(
            name="my-skill",
            instructions="Be concise.",
        )
        agent = _make_agent(
            system_prompt="You are a helpful assistant.",
            skills=["my-skill"],
        )
        ctx = _make_ctx(agent, skills=_MockSkillRegistry({"my-skill": skill}))
        assembler = PromptAssembler()
        prompt = await assembler.assemble(agent, ctx)
        # Persona comes first, skills second.
        assert prompt.index("You are a helpful assistant.") < prompt.index(
            "## Skill: my-skill"
        )

    @pytest.mark.asyncio
    async def test_workspace_skill_overrides_global(self):
        # Same-named skill in both the global (directory) and workspace
        # layers — passing workspace_id must resolve to the workspace one.
        global_skill = SkillPackage(name="shared", instructions="Global body.")
        ws_skill = SkillPackage(
            name="shared",
            instructions="Workspace body.",
            layer="workspace",
            editable=True,
            workspace_id="ws-1",
        )
        agent = _make_agent(
            system_prompt="You are a helpful assistant.",
            skills=["shared"],
        )
        ctx = _make_ctx(
            agent,
            skills=_MockSkillRegistry(
                {"shared": global_skill}, ws_skills={"shared": ws_skill}
            ),
        )
        assembler = PromptAssembler()
        prompt = await assembler.assemble(agent, ctx)
        assert "Workspace body." in prompt
        assert "Global body." not in prompt


# ── P2: Memory recall ───────────────────────────────────────────────────


class _MockMemoryScope:
    """Minimal MemoryScope mock for PromptAssembler tests."""

    def __init__(
        self,
        records: list[MemoryRecord] | None = None,
        profiles: list[MemoryRecord] | None = None,
        raise_error: bool = False,
    ):
        self._records = records or []
        self._profiles = profiles or []
        self._raise = raise_error

    async def recall(self, query, scope="user", limit=5, memory_type=None):
        if self._raise:
            raise RuntimeError("recall failed")
        return self._records

    async def recall_profiles(self, scope="user", limit=50):
        if self._raise:
            raise RuntimeError("recall_profiles failed")
        return self._profiles

    async def remember(self, key, content, scope="session", metadata=None, memory_type="episodic"):
        return "mock-id"

    async def list(self, scope="session", limit=100, memory_type=None):
        return self._records

    async def get(self, record_id):
        return None

    async def delete(self, record_id):
        pass


class TestPromptAssemblerMemory:
    """P2: long-term memory recall injection into the system prompt."""

    @staticmethod
    def _make_memory_agent(enable_long_term=True, recall_top_k=3):
        return _make_agent(
            system_prompt="You are a helpful assistant.",
            memory=MemoryConfig(
                enable_long_term=enable_long_term,
                recall_top_k=recall_top_k,
            ),
        )

    @staticmethod
    def _make_record(content="User prefers concise answers."):
        return MemoryRecord(
            id="m-1",
            scope="user",
            scope_id="u-1",
            content=content,
        )

    @pytest.mark.asyncio
    async def test_memory_not_recalled_when_disabled(self):
        agent = self._make_memory_agent(enable_long_term=False)
        ctx = _make_ctx(
            agent,
            memory=_MockMemoryScope(records=[self._make_record()]),
        )
        ctx.working_memory["last_user_message"] = "what do you remember?"
        assembler = PromptAssembler()
        prompt = await assembler.assemble(agent, ctx)
        assert "## Relevant Memories" not in prompt

    @pytest.mark.asyncio
    async def test_memory_not_recalled_when_no_memory_scope(self):
        agent = self._make_memory_agent(enable_long_term=True)
        ctx = _make_ctx(agent, memory=None)
        ctx.working_memory["last_user_message"] = "what do you remember?"
        assembler = PromptAssembler()
        # Must not raise even though ctx.memory is None.
        prompt = await assembler.assemble(agent, ctx)
        assert "## Relevant Memories" not in prompt

    @pytest.mark.asyncio
    async def test_memory_recalled_when_enabled(self):
        agent = self._make_memory_agent(enable_long_term=True)
        record = self._make_record(content="User prefers concise answers.")
        ctx = _make_ctx(agent, memory=_MockMemoryScope(records=[record]))
        ctx.working_memory["last_user_message"] = "what do you remember?"
        assembler = PromptAssembler()
        prompt = await assembler.assemble(agent, ctx)
        assert "## Relevant Memories" in prompt
        assert "User prefers concise answers." in prompt

    @pytest.mark.asyncio
    async def test_memory_recall_error_silently_skipped(self):
        agent = self._make_memory_agent(enable_long_term=True)
        ctx = _make_ctx(agent, memory=_MockMemoryScope(raise_error=True))
        ctx.working_memory["last_user_message"] = "what do you remember?"
        assembler = PromptAssembler()
        prompt = await assembler.assemble(agent, ctx)
        assert "## Relevant Memories" not in prompt
        assert "You are a helpful assistant." in prompt

    @pytest.mark.asyncio
    async def test_memory_section_after_tools(self):
        # Verify full ordering: persona → skills → tools → memories → policy.
        skill = SkillPackage(name="my-skill", instructions="Be concise.")
        record = self._make_record(content="User prefers concise answers.")
        agent = _make_agent(
            system_prompt="You are a helpful assistant.",
            tools=["todo_write"],
            skills=["my-skill"],
            memory=MemoryConfig(enable_long_term=True, recall_top_k=3),
        )
        engine = _make_tool_engine(["todo_write"])
        ctx = _make_ctx(
            agent,
            tool_engine=engine,
            skills=_MockSkillRegistry({"my-skill": skill}),
            memory=_MockMemoryScope(records=[record]),
            workspace_settings={"policy": {"allowed_models": ["deepseek-v4-flash"]}},
        )
        ctx.working_memory["last_user_message"] = "what do you remember?"
        assembler = PromptAssembler()
        prompt = await assembler.assemble(agent, ctx)
        # Four sections present (tools are no longer in the prompt).
        assert "You are a helpful assistant." in prompt
        assert "## Skill: my-skill" in prompt
        assert "## Relevant Memories" in prompt
        assert "## Workspace Policy" in prompt
        # And in the expected order.
        order = [
            prompt.index("You are a helpful assistant."),
            prompt.index("## Skill: my-skill"),
            prompt.index("## Relevant Memories"),
            prompt.index("## Workspace Policy"),
        ]
        assert order == sorted(order)

    @pytest.mark.asyncio
    async def test_memory_not_recalled_when_no_last_user_message(self):
        agent = self._make_memory_agent(enable_long_term=True)
        ctx = _make_ctx(agent, memory=_MockMemoryScope(records=[self._make_record()]))
        # Deliberately do NOT set working_memory["last_user_message"].
        assembler = PromptAssembler()
        prompt = await assembler.assemble(agent, ctx)
        assert "## Relevant Memories" not in prompt


# ── Wave 3: Profile / Episodic split ───────────────────────────────────


class TestPromptAssemblerProfileMemory:
    """Wave 3: profile records are always injected, episodic by query."""

    @staticmethod
    def _make_profile_agent(enable_long_term=True):
        return _make_agent(
            system_prompt="You are a helpful assistant.",
            memory=MemoryConfig(
                enable_long_term=enable_long_term,
                recall_top_k=3,
            ),
        )

    @staticmethod
    def _make_profile(content, record_id="prof-1"):
        return MemoryRecord(
            id=record_id,
            scope="user",
            scope_id="u-1",
            content=content,
            memory_type="profile",
        )

    @pytest.mark.asyncio
    async def test_profile_always_injected_without_last_message(self):
        """Profile memories appear even when there's no last_user_message."""
        agent = self._make_profile_agent()
        profile = self._make_profile("User prefers concise answers.")
        ctx = _make_ctx(
            agent,
            memory=_MockMemoryScope(profiles=[profile]),
        )
        assembler = PromptAssembler()
        prompt = await assembler.assemble(agent, ctx)
        assert "## User Profile" in prompt
        assert "User prefers concise answers." in prompt

    @pytest.mark.asyncio
    async def test_profile_not_injected_when_disabled(self):
        """Profiles are skipped when long-term memory is disabled."""
        agent = self._make_profile_agent(enable_long_term=False)
        profile = self._make_profile("User prefers Python.")
        ctx = _make_ctx(
            agent,
            memory=_MockMemoryScope(profiles=[profile]),
        )
        assembler = PromptAssembler()
        prompt = await assembler.assemble(agent, ctx)
        assert "## User Profile" not in prompt

    @pytest.mark.asyncio
    async def test_profile_and_episodic_coexist(self):
        """Both profile and episodic sections appear when both have data."""
        agent = self._make_profile_agent()
        profile = self._make_profile("User uses uv.")
        episodic = MemoryRecord(
            id="ep-1",
            scope="user",
            scope_id="u-1",
            content="Project is in /data folder.",
        )
        ctx = _make_ctx(
            agent,
            memory=_MockMemoryScope(
                profiles=[profile],
                records=[episodic],
            ),
        )
        ctx.working_memory["last_user_message"] = "where is the project?"
        assembler = PromptAssembler()
        prompt = await assembler.assemble(agent, ctx)
        assert "## User Profile" in prompt
        assert "User uses uv." in prompt
        assert "## Relevant Memories" in prompt
        assert "Project is in /data folder." in prompt
        assert prompt.index("## User Profile") < prompt.index(
            "## Relevant Memories"
        )

    @pytest.mark.asyncio
    async def test_episodic_not_shown_without_last_message(self):
        """Episodic records are skipped when no last_user_message,
        but profile records still appear."""
        agent = self._make_profile_agent()
        profile = self._make_profile("User prefers dark mode.")
        episodic = MemoryRecord(
            id="ep-2",
            scope="user",
            scope_id="u-1",
            content="Meeting at 3pm.",
        )
        ctx = _make_ctx(
            agent,
            memory=_MockMemoryScope(
                profiles=[profile],
                records=[episodic],
            ),
        )
        assembler = PromptAssembler()
        prompt = await assembler.assemble(agent, ctx)
        assert "## User Profile" in prompt
        assert "User prefers dark mode." in prompt
        assert "## Relevant Memories" not in prompt

    @pytest.mark.asyncio
    async def test_profile_error_silently_skipped(self):
        """If recall_profiles raises, the whole memory block is skipped."""
        agent = self._make_profile_agent()
        ctx = _make_ctx(
            agent,
            memory=_MockMemoryScope(raise_error=True),
        )
        ctx.working_memory["last_user_message"] = "test query"
        assembler = PromptAssembler()
        prompt = await assembler.assemble(agent, ctx)
        assert "## User Profile" not in prompt
        assert "## Relevant Memories" not in prompt
        assert "You are a helpful assistant." in prompt

    @pytest.mark.asyncio
    async def test_full_ordering_with_profiles(self):
        """Verify full section ordering: persona, skills, tools, profile, episodic, policy."""
        skill = SkillPackage(name="my-skill", instructions="Be concise.")
        profile = self._make_profile("User uses Python.")
        episodic = MemoryRecord(
            id="ep-3",
            scope="user",
            scope_id="u-1",
            content="Relevant fact.",
        )
        agent = _make_agent(
            system_prompt="You are a helpful assistant.",
            tools=["todo_write"],
            skills=["my-skill"],
            memory=MemoryConfig(enable_long_term=True, recall_top_k=3),
        )
        engine = _make_tool_engine(["todo_write"])
        ctx = _make_ctx(
            agent,
            tool_engine=engine,
            skills=_MockSkillRegistry({"my-skill": skill}),
            memory=_MockMemoryScope(
                profiles=[profile],
                records=[episodic],
            ),
            workspace_settings={"policy": {"allowed_models": ["deepseek-v4-flash"]}},
        )
        ctx.working_memory["last_user_message"] = "test query"
        assembler = PromptAssembler()
        prompt = await assembler.assemble(agent, ctx)
        assert "You are a helpful assistant." in prompt
        assert "## Skill: my-skill" in prompt
        assert "## User Profile" in prompt
        assert "## Relevant Memories" in prompt
        assert "## Workspace Policy" in prompt
        order = [
            prompt.index("You are a helpful assistant."),
            prompt.index("## Skill: my-skill"),
            prompt.index("## User Profile"),
            prompt.index("## Relevant Memories"),
            prompt.index("## Workspace Policy"),
        ]
        assert order == sorted(order)
