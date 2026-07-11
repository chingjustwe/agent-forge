"""P3a: Agent definition + registry.

Wraps the ``agent_configs`` ORM table with a Pydantic model that the
harness uses to build a ``HarnessContext``. The legacy ``framework``
column is exposed as ``adapter`` in the Pydantic model — they are the
same field, ``adapter`` is the new canonical name.

``AgentRegistry`` is the single read/write surface for agent definitions.
Routes in ``routes/agents.py`` delegate to it so the ORM details stay
inside the runtime layer.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.infra.db.models import AgentConfig
from src.infra.llm.models import resolve_default_model


# ── Pydantic models ──────────────────────────────────────────────────────


class MemoryConfig(BaseModel):
    """Per-agent memory configuration."""

    enable_short_term: bool = True
    enable_long_term: bool = False
    recall_top_k: int = 5


class SubagentSpec(BaseModel):
    """Declarative subagent definition stored in ``AgentDefinition.subagents``.

    Two shapes are accepted:

    - **Reference** (preferred, UI-driven): only ``agent_id`` set. The
      runtime resolves it to a full spec via the referenced agent's own
      config (system_prompt / tools / model / skills) at run time — see
      ``HarnessRuntime._resolve_subagent_refs``. ``name`` may carry a
      denormalized display label.
    - **Inline** (legacy / API-imported): ``name`` + ``description`` +
      ``system_prompt`` provided directly.

    ``SubagentMapper.to_subagents()`` (Phase 4b) converts these to the
    dict shape deepagents expects. It only ever receives *resolved* (full)
    specs — references are expanded before the adapter runs.

    Per spec D9: if ``tools`` is empty the subagent receives NO tools
    (not the parent's set). This is the opposite of deepagents' default
    and is a defense-in-depth measure.
    """

    agent_id: str | None = None  # reference mode: points at an existing agent
    name: str = ""
    description: str = ""
    system_prompt: str = ""
    tools: list[str] = Field(default_factory=list)
    model: str | None = None  # None = inherit parent agent's model
    skills: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _require_ref_or_full(self) -> "SubagentSpec":
        """A spec must be either a reference or a complete inline definition."""
        if self.agent_id:
            return self
        if not self.name or not self.description or not self.system_prompt:
            raise ValueError(
                "SubagentSpec requires either 'agent_id' or all of "
                "name/description/system_prompt"
            )
        return self


class AgentDefinition(BaseModel):
    """Canonical agent definition consumed by the harness.

    ``adapter`` maps to the ORM ``framework`` column (legacy name kept
    for backward compat with existing API clients and tests).

    Wave 2.5: DirectLLM removed; ``deepagents`` is the sole adapter.
    Legacy ``direct_llm``/``adk``/``langgraph`` values fall back to
    deepagents in ``_resolve_adapter`` (and are rewritten to
    ``deepagents`` by migration M23).
    """

    id: str
    name: str
    workspace_id: str
    system_prompt: str = ""
    model: str = "deepseek-v4-flash"
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = 4096
    tools: list[str] = Field(default_factory=list)
    guardrails: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    hooks: list[str] = Field(default_factory=list)
    memory: MemoryConfig | None = None
    adapter: Literal["deepagents"] = "deepagents"
    # Phase 4 (spec D3): subagent specs; only used when adapter="deepagents"
    subagents: list[SubagentSpec] = Field(default_factory=list)
    # Phase 5: explicitly bound MCP servers (workspace-scoped names). The
    # agent receives every tool exposed by each selected server (union with
    # ``tools``). Empty list = no MCP servers bound.
    mcp_servers: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
    created_by: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None


# ── Registry ────────────────────────────────────────────────────────────


class AgentNotFoundError(LookupError):
    """Raised when an agent id does not exist in the registry."""


class AgentRegistry:
    """CRUD surface over the ``agent_configs`` table.

    All methods are async and take an ``AsyncSession`` so callers control
    the transaction boundary. ``register``/``update``/``delete`` do NOT
    commit — the caller commits after one or more mutations.
    """

    async def register(
        self,
        db: AsyncSession,
        *,
        workspace_id: str,
        name: str,
        adapter: str,
        created_by: str,
        system_prompt: str = "",
        model: str = "deepseek-v4-flash",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: list[str] | None = None,
        guardrails: list[str] | None = None,
        skills: list[str] | None = None,
        hooks: list[str] | None = None,
        memory_config: dict | None = None,
        metadata: dict | None = None,
        subagents: list[dict] | None = None,
        mcp_servers: list[str] | None = None,
    ) -> AgentDefinition:
        """Insert a new agent row and return the Pydantic model.

        Raises ``ValueError`` if (workspace_id, name) is not unique.
        """
        existing = await db.execute(
            select(AgentConfig).where(
                AgentConfig.workspace_id == workspace_id,
                AgentConfig.name == name,
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise ValueError(
                f"Agent named {name!r} already exists in workspace {workspace_id}"
            )

        row = AgentConfig(
            id=uuid.uuid4().hex[:32],
            workspace_id=workspace_id,
            name=name,
            framework=adapter,
            config=metadata or {},
            created_by=created_by,
            system_prompt=system_prompt,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools or [],
            guardrails=guardrails or [],
            skills=skills or [],
            hooks=hooks or [],
            memory_config=memory_config,
            subagents=subagents or [],
            mcp_servers=mcp_servers or [],
        )
        db.add(row)
        await db.flush()
        return self._to_pydantic(row)

    async def get(
        self, db: AsyncSession, agent_id: str
    ) -> AgentDefinition | None:
        row = await db.get(AgentConfig, agent_id)
        return self._to_pydantic(row) if row else None

    async def get_by_name(
        self, db: AsyncSession, workspace_id: str, name: str
    ) -> AgentDefinition | None:
        result = await db.execute(
            select(AgentConfig).where(
                AgentConfig.workspace_id == workspace_id,
                AgentConfig.name == name,
            )
        )
        row = result.scalar_one_or_none()
        return self._to_pydantic(row) if row else None

    async def list(
        self, db: AsyncSession, workspace_id: str
    ) -> list[AgentDefinition]:
        result = await db.execute(
            select(AgentConfig)
            .where(AgentConfig.workspace_id == workspace_id)
            .order_by(AgentConfig.created_at.desc())
        )
        return [self._to_pydantic(r) for r in result.scalars().all()]

    async def update(
        self,
        db: AsyncSession,
        agent_id: str,
        **fields,
    ) -> AgentDefinition:
        """Patch fields. Unknown keys raise ``ValueError``.

        Accepts both Pydantic-style keys (``adapter``, ``metadata``) and
        ORM-style keys (``framework``, ``config``) for flexibility.
        """
        row = await db.get(AgentConfig, agent_id)
        if row is None:
            raise AgentNotFoundError(agent_id)

        # Map Pydantic field names to ORM columns where they differ.
        key_map = {
            "adapter": "framework",
            "metadata": "config",
        }
        allowed = {
            "name",
            "adapter",
            "framework",
            "system_prompt",
            "model",
            "temperature",
            "max_tokens",
            "tools",
            "guardrails",
            "skills",
            "hooks",
        "memory_config",
        "metadata",
        "config",
        "subagents",
        "mcp_servers",
    }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"Unknown agent fields: {sorted(unknown)}")

        for key, value in fields.items():
            orm_key = key_map.get(key, key)
            setattr(row, orm_key, value)

        await db.flush()
        return self._to_pydantic(row)

    async def delete(self, db: AsyncSession, agent_id: str) -> bool:
        row = await db.get(AgentConfig, agent_id)
        if row is None:
            return False
        await db.delete(row)
        await db.flush()
        return True

    # ── ORM → Pydantic ──
    def _to_pydantic(self, row: AgentConfig) -> AgentDefinition:
        memory_cfg = None
        if row.memory_config:
            try:
                memory_cfg = MemoryConfig(**row.memory_config)
            except Exception:
                memory_cfg = None

        # Phase 4: parse subagents JSON column → list[SubagentSpec]
        subagent_specs: list[SubagentSpec] = []
        raw_subagents = getattr(row, "subagents", None)
        if raw_subagents:
            try:
                for item in raw_subagents if isinstance(raw_subagents, list) else []:
                    subagent_specs.append(SubagentSpec(**item))
            except Exception:
                subagent_specs = []

        return AgentDefinition(
            id=row.id,
            name=row.name,
            workspace_id=row.workspace_id,
            system_prompt=row.system_prompt or "",
            model=row.model or resolve_default_model(),
            temperature=row.temperature if row.temperature is not None else 0.7,
            max_tokens=row.max_tokens if row.max_tokens is not None else 4096,
            tools=row.tools or [],
            guardrails=row.guardrails or [],
            skills=row.skills or [],
            hooks=row.hooks or [],
            memory=memory_cfg,
            adapter=row.framework,
            subagents=subagent_specs,
            mcp_servers=row.mcp_servers or [],
            metadata=row.config or {},
            created_by=row.created_by or "",
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


# Module-level singleton — wired by HarnessRegistry.create().
# Routes import this directly for convenience.
agents = AgentRegistry()
