"""P3a: Tests for AgentRegistry — CRUD surface over agent_configs table.

Covers register/get/get_by_name/list/update/delete, ORM↔Pydantic
conversion (including legacy framework↔adapter mapping), and the
MemoryConfig parsing fallback for malformed JSON.
"""
import pytest

from src.infra.db.engine import async_session
from src.infra.db.models import AgentConfig, Tenant, Workspace
from src.runtime.harness.agents import (
    AgentDefinition,
    AgentNotFoundError,
    AgentRegistry,
    MemoryConfig,
)


async def _seed_workspace(ws_id: str = "ws-ar", tenant_id: str = "t-ar") -> str:
    """Seed a tenant + workspace so FKs are satisfied. Returns ws_id."""
    async with async_session() as db:
        if not await db.get(Tenant, tenant_id):
            db.add(Tenant(id=tenant_id, name="T", domain=f"{tenant_id}.test"))
        if not await db.get(Workspace, ws_id):
            db.add(Workspace(id=ws_id, tenant_id=tenant_id, name="WS"))
        await db.commit()
    return ws_id


class TestAgentRegistryCRUD:
    @pytest.mark.asyncio
    async def test_register_and_get(self):
        ws_id = await _seed_workspace()
        reg = AgentRegistry()
        async with async_session() as db:
            agent = await reg.register(
                db,
                workspace_id=ws_id,
                name="Helper",
                adapter="direct_llm",
                created_by="u1",
                system_prompt="You are helpful.",
                model="deepseek-chat",
                tools=["todo_write", "ls"],
            )
            await db.commit()

            fetched = await reg.get(db, agent.id)
        assert fetched is not None
        assert fetched.name == "Helper"
        assert fetched.adapter == "direct_llm"
        assert fetched.system_prompt == "You are helpful."
        assert fetched.tools == ["todo_write", "ls"]
        # Legacy ORM column ``framework`` mirrors ``adapter``.
        assert fetched.metadata == {}

    @pytest.mark.asyncio
    async def test_register_duplicate_name_raises(self):
        ws_id = await _seed_workspace()
        reg = AgentRegistry()
        async with async_session() as db:
            await reg.register(
                db, workspace_id=ws_id, name="Dup", adapter="direct_llm",
                created_by="u1",
            )
            await db.commit()
            with pytest.raises(ValueError, match="already exists"):
                await reg.register(
                    db, workspace_id=ws_id, name="Dup", adapter="direct_llm",
                    created_by="u1",
                )

    @pytest.mark.asyncio
    async def test_get_by_name(self):
        ws_id = await _seed_workspace()
        reg = AgentRegistry()
        async with async_session() as db:
            await reg.register(
                db, workspace_id=ws_id, name="Named", adapter="deepagents",
                created_by="u1",
            )
            await db.commit()
            found = await reg.get_by_name(db, ws_id, "Named")
            missing = await reg.get_by_name(db, ws_id, "Nope")
        assert found is not None
        assert found.name == "Named"
        assert found.adapter == "deepagents"
        assert missing is None

    @pytest.mark.asyncio
    async def test_list_isolated_per_workspace(self):
        reg = AgentRegistry()
        ws_a = await _seed_workspace("ws-a", "t-list")
        ws_b = await _seed_workspace("ws-b", "t-list")
        async with async_session() as db:
            await reg.register(
                db, workspace_id=ws_a, name="A1", adapter="direct_llm",
                created_by="u",
            )
            await reg.register(
                db, workspace_id=ws_b, name="B1", adapter="direct_llm",
                created_by="u",
            )
            await db.commit()
            list_a = await reg.list(db, ws_a)
            list_b = await reg.list(db, ws_b)
        assert {a.name for a in list_a} == {"A1"}
        assert {b.name for b in list_b} == {"B1"}

    @pytest.mark.asyncio
    async def test_update_patches_fields(self):
        ws_id = await _seed_workspace()
        reg = AgentRegistry()
        async with async_session() as db:
            agent = await reg.register(
                db, workspace_id=ws_id, name="Up", adapter="direct_llm",
                created_by="u",
            )
            await db.commit()

            updated = await reg.update(
                db, agent.id,
                system_prompt="New prompt",
                tools=["ls"],
                adapter="deepagents",
            )
            await db.commit()
        assert updated.system_prompt == "New prompt"
        assert updated.tools == ["ls"]
        assert updated.adapter == "deepagents"

    @pytest.mark.asyncio
    async def test_update_unknown_field_raises(self):
        ws_id = await _seed_workspace()
        reg = AgentRegistry()
        async with async_session() as db:
            agent = await reg.register(
                db, workspace_id=ws_id, name="U2", adapter="direct_llm",
                created_by="u",
            )
            await db.commit()
            with pytest.raises(ValueError, match="Unknown agent fields"):
                await reg.update(db, agent.id, bogus_field="x")

    @pytest.mark.asyncio
    async def test_update_missing_agent_raises(self):
        reg = AgentRegistry()
        async with async_session() as db:
            with pytest.raises(AgentNotFoundError):
                await reg.update(db, "nonexistent-id", name="x")

    @pytest.mark.asyncio
    async def test_delete(self):
        ws_id = await _seed_workspace()
        reg = AgentRegistry()
        async with async_session() as db:
            agent = await reg.register(
                db, workspace_id=ws_id, name="Del", adapter="direct_llm",
                created_by="u",
            )
            await db.commit()
            ok = await reg.delete(db, agent.id)
            await db.commit()
            gone = await reg.get(db, agent.id)
        assert ok is True
        assert gone is None

    @pytest.mark.asyncio
    async def test_delete_missing_returns_false(self):
        reg = AgentRegistry()
        async with async_session() as db:
            assert await reg.delete(db, "nope") is False


class TestAgentRegistryConversion:
    @pytest.mark.asyncio
    async def test_to_pydantic_handles_null_structured_fields(self):
        """Rows created by legacy code (no structured fields) convert cleanly."""
        ws_id = await _seed_workspace()
        async with async_session() as db:
            db.add(AgentConfig(
                id="legacy-1",
                workspace_id=ws_id,
                name="Legacy",
                framework="direct_llm",
                config={"old": "json"},
                created_by="u",
                # system_prompt, model, tools etc. are NULL
            ))
            await db.commit()
            reg = AgentRegistry()
            agent = await reg.get(db, "legacy-1")
        assert agent is not None
        assert agent.system_prompt == ""
        assert agent.model == "deepseek-chat"
        assert agent.temperature == 0.7
        assert agent.max_tokens == 4096
        assert agent.tools == []
        assert agent.guardrails == []
        assert agent.memory is None
        assert agent.metadata == {"old": "json"}

    @pytest.mark.asyncio
    async def test_to_pydantic_parses_valid_memory_config(self):
        ws_id = await _seed_workspace()
        async with async_session() as db:
            db.add(AgentConfig(
                id="mem-1",
                workspace_id=ws_id,
                name="Mem",
                framework="direct_llm",
                config={},
                created_by="u",
                memory_config={
                    "enable_short_term": False,
                    "enable_long_term": True,
                    "recall_top_k": 10,
                },
            ))
            await db.commit()
            reg = AgentRegistry()
            agent = await reg.get(db, "mem-1")
        assert agent is not None
        assert isinstance(agent.memory, MemoryConfig)
        assert agent.memory.enable_short_term is False
        assert agent.memory.enable_long_term is True
        assert agent.memory.recall_top_k == 10

    @pytest.mark.asyncio
    async def test_to_pydantic_handles_malformed_memory_config(self):
        """Malformed memory_config JSON falls back to None (not crash)."""
        ws_id = await _seed_workspace()
        async with async_session() as db:
            db.add(AgentConfig(
                id="bad-mem-1",
                workspace_id=ws_id,
                name="Bad",
                framework="direct_llm",
                config={},
                created_by="u",
                # recall_top_k expects int; a non-coercible string triggers
                # ValidationError which _to_pydantic swallows → None.
                memory_config={"recall_top_k": "not-an-int"},
            ))
            await db.commit()
            reg = AgentRegistry()
            agent = await reg.get(db, "bad-mem-1")
        assert agent is not None
        assert agent.memory is None  # fallback
