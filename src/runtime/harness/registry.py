"""P3a: HarnessRegistry — platform-level singleton container.

Wired in ``main.py`` lifespan. Holds all platform-level registries and
stores. ``HarnessRuntime`` receives a ``HarnessRegistry`` instance and
uses it to resolve agents + build per-run ``HarnessContext`` objects.

P0 wires: AgentRegistry, ToolRegistry (+12 builtin tools),
GuardrailPipeline (+4 builtin guardrails), TelemetryCollector.
P1 will add: MCPManager, SandboxManager, HookRegistry, CheckpointStore.
P2 will add: MemoryStore, SkillRegistry.
P3 will add: Scheduler.
"""
from __future__ import annotations

import logging

from src.infra.telemetry.collector import TelemetryCollector

from .agents import AgentRegistry, agents as _agents_singleton
from .guardrails import (
    ContentFilterGuardrail,
    GuardrailPipeline,
    PIIRedactionGuardrail,
    PolicyGuardrail,
    QuotaGuardrail,
)
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
        mcp: object | None = None,
        sandbox: object | None = None,
        memory: object | None = None,
        hooks: object | None = None,
        checkpoints: object | None = None,
        skills: object | None = None,
        scheduler: object | None = None,
    ) -> None:
        self.agents = agents or _agents_singleton
        self.tools = tools or _tools_singleton
        self.guardrails = guardrails or GuardrailPipeline()
        self.collector = collector or TelemetryCollector()
        # P1+ placeholders — None until their respective phases wire them.
        self.mcp = mcp
        self.sandbox = sandbox
        self.memory = memory
        self.hooks = hooks
        self.checkpoints = checkpoints
        self.skills = skills
        self.scheduler = scheduler

    @classmethod
    def create(cls) -> "HarnessRegistry":
        """Factory: wire all P0 subsystems with sensible defaults.

        - Registers 12 builtin tool definitions into ``ToolRegistry``.
        - Registers 4 builtin guardrails into ``GuardrailPipeline``.
        - Reuses the module-level ``agents`` / ``tools`` singletons so
          routes can import them directly.
        """
        registry = cls()

        # ── Register builtin tools ──
        from .tools import BUILTIN_TOOL_DEFINITIONS

        registered = 0
        for tool_def in BUILTIN_TOOL_DEFINITIONS:
            # Don't double-register on repeated create() calls.
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

        return registry

    async def shutdown(self) -> None:
        """Release any held resources. P0: no-op (no async resources yet)."""
        # P1: close MCP connections, scheduler.stop(), etc.
        pass


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
