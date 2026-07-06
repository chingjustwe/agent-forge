"""P3a: HarnessContext — per-run context injected into Adapter.run().

Built by ``HarnessRuntime._build_context`` from the platform
``HarnessRegistry`` + the resolved ``AgentDefinition``. Holds:
- Identity (workspace, user, session, trace, agent)
- Capability systems (tool_engine, sandbox, guardrails, memory, hooks,
  checkpoint, prompt_assembler)
- Credentials (workspace secrets)
- Mutable per-run state (working_memory, workspace_settings,
  workspace_root)
- Telemetry (collector, tracer, metrics)

P0 wires the fields the harness needs to actually run; P1 adds
SandboxManager, HookRegistry, CheckpointStore, PromptAssembler.
P2/P3 add MemoryStore, SkillRegistry, Scheduler etc.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.infra.telemetry.collector import TelemetryCollector
    from src.infra.telemetry.metrics import Metrics
    from src.infra.telemetry.spans import Tracer

    from src.runtime.harness.agents import AgentDefinition
    from src.runtime.harness.guardrails import GuardrailPipeline
    from src.runtime.harness.tool_engine import ToolEngine


class HarnessContext:
    """Per-run context. Injected into ``RunAdapter.run()``."""

    # ── Identity ──
    workspace_id: str
    user_id: str
    session_id: str
    trace_id: str
    agent: "AgentDefinition"

    # ── Capability systems (scoped to this run) ──
    tool_engine: "ToolEngine"
    guardrails: "GuardrailPipeline"
    # The following are None in P0; populated by P1 wiring.
    sandbox: Any | None = None
    memory: Any | None = None
    hooks: Any | None = None
    checkpoint: Any | None = None
    prompt_assembler: Any | None = None

    # ── Credentials ──
    secrets: dict[str, str]

    # ── Mutable per-run state ──
    working_memory: dict
    workspace_settings: dict
    workspace_root: str

    # ── Telemetry ──
    collector: "TelemetryCollector"
    tracer: "Tracer"
    metrics: "Metrics"

    def __init__(
        self,
        *,
        workspace_id: str,
        user_id: str,
        session_id: str,
        trace_id: str,
        agent: "AgentDefinition",
        tool_engine: "ToolEngine | None" = None,
        guardrails: "GuardrailPipeline | None" = None,
        collector: "TelemetryCollector | None" = None,
        tracer: "Tracer | None" = None,
        metrics: "Metrics | None" = None,
        secrets: dict[str, str] | None = None,
        workspace_settings: dict | None = None,
        workspace_root: str = "",
        sandbox: Any | None = None,
        memory: Any | None = None,
        hooks: Any | None = None,
        checkpoint: Any | None = None,
        prompt_assembler: Any | None = None,
    ) -> None:
        self.workspace_id = workspace_id
        self.user_id = user_id
        self.session_id = session_id
        self.trace_id = trace_id
        self.agent = agent
        self.tool_engine = tool_engine
        self.guardrails = guardrails
        self.sandbox = sandbox
        self.memory = memory
        self.hooks = hooks
        self.checkpoint = checkpoint
        self.prompt_assembler = prompt_assembler
        self.secrets = secrets or {}
        self.working_memory = {}
        self.workspace_settings = workspace_settings or {}
        self.workspace_root = workspace_root or ""

        # Telemetry — fall back to module-level singletons so existing
        # chat.py code paths keep working without explicit wiring.
        if collector is None:
            from src.infra.telemetry.collector import TelemetryCollector
            collector = TelemetryCollector()
        if tracer is None:
            from src.infra.telemetry.spans import tracer as _tracer
            tracer = _tracer
        if metrics is None:
            from src.infra.telemetry.metrics import metrics as _metrics
            metrics = _metrics
        self.collector = collector
        self.tracer = tracer
        self.metrics = metrics

    # ── Convenience methods for telemetry passthrough ──
    async def record_request(self, **kwargs) -> str:
        return await self.collector.record_request(**kwargs)

    async def record_tool_call(self, **kwargs) -> None:
        await self.collector.record_tool_call(**kwargs)

    async def record_event(self, **kwargs) -> None:
        await self.collector.record_event(**kwargs)
