"""P3a: HarnessRegistry — platform-level singleton container.

Wired in ``main.py`` lifespan. Holds all platform-level registries and
stores. ``HarnessRuntime`` receives a ``HarnessRegistry`` instance and
uses it to resolve agents + build per-run ``HarnessContext`` objects.

P0 wires: AgentRegistry, ToolRegistry (+12 builtin tools),
GuardrailPipeline (+4 builtin guardrails), TelemetryCollector.
P1 adds: MCPManager, SandboxManager, HookRegistry (+3 builtin hooks),
CheckpointStore (SQLiteCheckpointStore), PromptAssembler.
P2 adds: MemoryStore (SQLiteMemoryStore), SkillRegistry.
P3 will add: Scheduler.
"""
from __future__ import annotations

import logging

from src.infra.telemetry.collector import TelemetryCollector

from .agents import AgentRegistry, agents as _agents_singleton
from .checkpoint import SQLiteCheckpointStore
from .guardrails import (
    ContentFilterGuardrail,
    GuardrailPipeline,
    PIIRedactionGuardrail,
    PolicyGuardrail,
    QuotaGuardrail,
)
from .hooks import (
    AuditLogHook,
    HookRegistry,
    MetricHook,
    TraceHook,
)
from .mcp import MCPManager
from .memory import SQLiteMemoryStore
from .prompt import PromptAssembler
from .sandbox import SandboxManager
from .scheduler import Scheduler
from .skills import SkillRegistry
from .tool_engine import ToolRegistry, tools as _tools_singleton

logger = logging.getLogger(__name__)


class HarnessRegistry:
    """Singleton container for all platform-level harness subsystems."""

    def __init__(
        self,
        *,
        agents: AgentRegistry | None = None,
        tools: ToolRegistry | None = None,
        guardrails: GuardrailPipeline | None = None,
        collector: TelemetryCollector | None = None,
        mcp: MCPManager | None = None,
        sandbox: SandboxManager | None = None,
        memory: object | None = None,
        hooks: HookRegistry | None = None,
        checkpoints: SQLiteCheckpointStore | None = None,
        skills: object | None = None,
        scheduler: object | None = None,
        prompt_assembler: PromptAssembler | None = None,
    ) -> None:
        self.agents = agents or _agents_singleton
        self.tools = tools or _tools_singleton
        self.guardrails = guardrails or GuardrailPipeline()
        self.collector = collector or TelemetryCollector()
        # P1 components
        self.mcp = mcp or MCPManager()
        self.sandbox = sandbox or SandboxManager()
        self.hooks = hooks or HookRegistry()
        self.checkpoints = checkpoints or SQLiteCheckpointStore()
        self.prompt_assembler = prompt_assembler or PromptAssembler()
        # P2 components
        self.memory = memory or SQLiteMemoryStore()
        self.skills = skills or SkillRegistry()
        # P3 component
        self.scheduler = scheduler or Scheduler()

    @classmethod
    def create(cls) -> "HarnessRegistry":
        """Factory: wire all subsystems with sensible defaults.

        - Registers 12 builtin tool definitions into ``ToolRegistry``.
        - Registers 4 builtin guardrails into ``GuardrailPipeline``.
        - Registers 3 builtin hooks into ``HookRegistry``.
        - Scans ``.agents/skills/`` for skill markdown files (P2).
        - Reuses the module-level ``agents`` / ``tools`` singletons so
          routes can import them directly.
        """
        registry = cls()

        # ── Scan for skill files (P2) ──
        import asyncio
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            # We're in an async context — schedule the scan
            asyncio.ensure_future(registry.skills.scan())
        else:
            # Sync context (e.g., tests) — skip auto-scan
            pass

        # ── Register builtin tools ──
        from .tools import BUILTIN_TOOL_DEFINITIONS

        registered = 0
        for tool_def in BUILTIN_TOOL_DEFINITIONS:
            if registry.tools.get(tool_def.name) is None:
                registry.tools.register(tool_def)
                registered += 1
        if registered:
            logger.info("HarnessRegistry: registered %d builtin tools", registered)

        # ── Register builtin guardrails (idempotent) ──
        existing = {g.name for g in registry.guardrails.list()}
        if "content_filter" not in existing:
            registry.guardrails.add(ContentFilterGuardrail())
        if "pii_redaction" not in existing:
            registry.guardrails.add(PIIRedactionGuardrail())
        if "quota" not in existing:
            registry.guardrails.add(QuotaGuardrail())
        if "policy" not in existing:
            registry.guardrails.add(PolicyGuardrail())

        # ── Register builtin hooks (idempotent) ──
        hook_names = {h.name for h in registry.hooks.list()}
        if "trace" not in hook_names:
            registry.hooks.register(TraceHook())
        if "metric" not in hook_names:
            registry.hooks.register(MetricHook())
        if "audit_log" not in hook_names:
            registry.hooks.register(AuditLogHook())

        return registry

    async def shutdown(self) -> None:
        """Release any held resources."""
        # Shut down scheduler
        if self.scheduler is not None:
            await self.scheduler.shutdown()
        # Close MCP connections
        if self.mcp is not None:
            await self.mcp.close_all()
        logger.info("HarnessRegistry: shutdown complete")


# Module-level singleton — wired by main.py lifespan.
# Routes and tests import this directly.
registry: HarnessRegistry | None = None


def get_registry() -> HarnessRegistry:
    """Return the initialized singleton. Raises if not yet wired."""
    global registry
    if registry is None:
        registry = HarnessRegistry.create()
    return registry


def reset_registry() -> None:
    """Reset the singleton. Used by tests for isolation."""
    global registry
    registry = None
