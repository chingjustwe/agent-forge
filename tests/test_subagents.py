"""Phase 4b: Tests for SubagentMapper.

Covers spec §8.2:
- to_subagents produces dict shape deepagents expects
- Empty spec.tools → empty list (NOT inherited) — spec D9
- Unknown tool name in spec.tools is silently dropped
- spec.model=None → model key omitted from output dict
- SubagentSpec validates required fields
"""
from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from src.runtime.harness.agents import SubagentSpec
from src.runtime.harness.subagents import SubagentMapper
from src.runtime.harness.tool_engine import ToolDefinition, ToolRegistry


# ── Helpers ─────────────────────────────────────────────────────────────


class _FakeToolEngine:
    """Minimal stand-in exposing the ``_registry`` and ``workspace_id``
    attributes that SubagentMapper.to_subagents accesses."""

    def __init__(self, registry: ToolRegistry, workspace_id: str = "ws"):
        self._registry = registry
        self.workspace_id = workspace_id


class _FakeCtx:
    """Minimal stand-in for HarnessContext."""

    def __init__(self, tool_engine: Any, workspace_id: str = "ws"):
        self.tool_engine = tool_engine
        self.workspace_id = workspace_id


def _make_tool(name: str) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=f"{name} tool",
        input_schema={"type": "object", "properties": {}},
    )


# ── Tests ───────────────────────────────────────────────────────────────


class TestSubagentMapper:
    def test_to_subagents_produces_expected_dict_shape(self):
        """Output dict has the keys deepagents.create_deep_agent expects."""
        registry = ToolRegistry()
        registry.register(_make_tool("search"))
        ctx = _FakeCtx(_FakeToolEngine(registry))

        specs = [
            SubagentSpec(
                name="web-searcher",
                description="Delegates web searches.",
                system_prompt="You are a search specialist.",
                tools=["search"],
                model="deepseek-v4-flash",
            ),
        ]
        out = SubagentMapper.to_subagents(specs, ctx)
        assert len(out) == 1
        entry = out[0]
        assert entry["name"] == "web-searcher"
        assert entry["description"] == "Delegates web searches."
        assert entry["system_prompt"] == "You are a search specialist."
        assert entry["model"] == "deepseek-v4-flash"
        # tools is a list of LangChainToolShim instances.
        assert len(entry["tools"]) == 1

    def test_empty_tools_yields_empty_list_not_inherited(self):
        """Spec D9: empty spec.tools → empty list (NOT parent's tools)."""
        registry = ToolRegistry()
        registry.register(_make_tool("search"))
        ctx = _FakeCtx(_FakeToolEngine(registry))

        specs = [
            SubagentSpec(
                name="summarizer",
                description="Summarizes text.",
                system_prompt="You summarize faithfully.",
                tools=[],  # explicit empty
            ),
        ]
        out = SubagentMapper.to_subagents(specs, ctx)
        assert out[0]["tools"] == []

    def test_unknown_tool_name_silently_dropped(self):
        """A misconfigured subagent should not crash the parent run."""
        registry = ToolRegistry()
        # Only "search" is registered; "bogus" is not.
        registry.register(_make_tool("search"))
        ctx = _FakeCtx(_FakeToolEngine(registry))

        specs = [
            SubagentSpec(
                name="mixed",
                description="Has known + unknown tools.",
                system_prompt="x",
                tools=["search", "bogus"],
            ),
        ]
        out = SubagentMapper.to_subagents(specs, ctx)
        # Only the known tool is bridged; bogus is dropped.
        assert len(out[0]["tools"]) == 1

    def test_model_none_omits_model_key(self):
        """spec.model=None → 'model' key omitted → deepagents inherits parent."""
        registry = ToolRegistry()
        ctx = _FakeCtx(_FakeToolEngine(registry))

        specs = [
            SubagentSpec(
                name="inheritor",
                description="Inherits parent model.",
                system_prompt="x",
                tools=[],
                model=None,
            ),
        ]
        out = SubagentMapper.to_subagents(specs, ctx)
        assert "model" not in out[0]

    def test_model_set_includes_model_key(self):
        """spec.model="X" → output dict has model="X"."""
        registry = ToolRegistry()
        ctx = _FakeCtx(_FakeToolEngine(registry))

        specs = [
            SubagentSpec(
                name="overrider",
                description="Uses custom model.",
                system_prompt="x",
                tools=[],
                model="deepseek-v4-pro",
            ),
        ]
        out = SubagentMapper.to_subagents(specs, ctx)
        assert out[0]["model"] == "deepseek-v4-pro"

    def test_no_tool_engine_yields_empty_tools(self):
        """When ctx.tool_engine is None, every subagent gets empty tools."""
        ctx = _FakeCtx(tool_engine=None)

        specs = [
            SubagentSpec(
                name="orphan",
                description="No engine available.",
                system_prompt="x",
                tools=["search"],  # requested but engine is None
            ),
        ]
        out = SubagentMapper.to_subagents(specs, ctx)
        assert out[0]["tools"] == []

    def test_empty_specs_yields_empty_list(self):
        """No subagent specs → empty output list."""
        registry = ToolRegistry()
        ctx = _FakeCtx(_FakeToolEngine(registry))
        out = SubagentMapper.to_subagents([], ctx)
        assert out == []


class TestSubagentSpecValidation:
    def test_name_required(self):
        with pytest.raises(ValidationError):
            SubagentSpec(
                description="d",
                system_prompt="s",
            )

    def test_description_required(self):
        with pytest.raises(ValidationError):
            SubagentSpec(
                name="x",
                system_prompt="s",
            )

    def test_system_prompt_required(self):
        with pytest.raises(ValidationError):
            SubagentSpec(
                name="x",
                description="d",
            )

    def test_defaults(self):
        """tools, model, skills have sensible defaults."""
        spec = SubagentSpec(
            name="x",
            description="d",
            system_prompt="s",
        )
        assert spec.tools == []
        assert spec.model is None
        assert spec.skills == []
