"""P3a: Tests for the GuardrailPipeline + 4 builtin guardrails.

Covers:
- GuardrailPipeline: add/remove/list, check_input ordering + short-circuit,
  check_output, direction filtering (input-only guards skipped on output)
- ContentFilterGuardrail: regex block on str + list, allow on no match,
  skip invalid patterns at construction, ignore when no patterns
- PIIRedactionGuardrail: redact email / phone / SSN, return modified_content
  for str and modified_messages for list, no-op when no PII, custom patterns
- QuotaGuardrail: delegates to legacy impl, passes through allow + block,
  preserves reason from inner result
- PolicyGuardrail: block disallowed model, block disallowed tool,
  allow when no policy set, allow when policy is empty dict
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.runtime.harness.agents import AgentDefinition
from src.runtime.harness.context import HarnessContext
from src.runtime.harness.guardrails.base import (
    Guardrail,
    GuardrailPipeline,
    GuardrailResult,
)
from src.runtime.harness.guardrails.content_filter import ContentFilterGuardrail
from src.runtime.harness.guardrails.pii import PIIRedactionGuardrail
from src.runtime.harness.guardrails.policy import PolicyGuardrail
from src.runtime.harness.guardrails.quota import QuotaGuardrail


# ── Helpers ─────────────────────────────────────────────────────────────


def _make_ctx(
    *,
    agent: AgentDefinition | None = None,
    workspace_settings: dict | None = None,
    workspace_id: str = "ws-g",
) -> HarnessContext:
    return HarnessContext(
        workspace_id=workspace_id,
        user_id="u-g",
        session_id="s-g",
        trace_id="t-g",
        agent=agent or _make_agent(),
        workspace_settings=workspace_settings,
    )


def _make_agent(
    *,
    model: str = "deepseek-v4-flash",
    tools: list[str] | None = None,
) -> AgentDefinition:
    return AgentDefinition(
        id="a-g",
        name="g-agent",
        workspace_id="ws-g",
        model=model,
        tools=tools if tools is not None else [],
    )


# ── GuardrailPipeline ───────────────────────────────────────────────────


class TestGuardrailPipeline:
    @pytest.mark.asyncio
    async def test_add_remove_list(self):
        pipe = GuardrailPipeline()
        g1 = ContentFilterGuardrail(patterns=["foo"])
        g2 = ContentFilterGuardrail(patterns=["bar"])
        pipe.add(g1)
        pipe.add(g2)
        assert len(pipe.list()) == 2

        assert pipe.remove("content_filter") is True
        assert len(pipe.list()) == 0
        # Removing a missing guardrail returns False.
        assert pipe.remove("content_filter") is False

    @pytest.mark.asyncio
    async def test_check_input_returns_allow_when_empty(self):
        pipe = GuardrailPipeline()
        ctx = _make_ctx()
        result = await pipe.check_input([{"role": "user", "content": "hi"}], ctx)
        assert result.passed is True
        assert result.action == "allow"

    @pytest.mark.asyncio
    async def test_check_input_short_circuits_on_block(self):
        pipe = GuardrailPipeline()
        # First guard blocks; second should not run.
        blocker = _BlockingGuard("blocker")
        spy = _SpyGuard("spy")
        pipe.add(blocker)
        pipe.add(spy)
        ctx = _make_ctx()
        result = await pipe.check_input(
            [{"role": "user", "content": "x"}], ctx
        )
        assert result.passed is False
        assert result.action == "block"
        assert result.guardrail_name == "blocker"
        assert spy.called is False

    @pytest.mark.asyncio
    async def test_check_input_runs_redact_then_continues(self):
        """Redact is non-terminal: subsequent guards still run."""
        pipe = GuardrailPipeline()
        redactor = _AlwaysRedactGuard("redactor")
        spy = _SpyGuard("spy")
        pipe.add(redactor)
        pipe.add(spy)
        ctx = _make_ctx()
        result = await pipe.check_input(
            [{"role": "user", "content": "x"}], ctx
        )
        # Redact short-circuits the pipeline (first non-allow wins).
        assert result.action == "redact"
        assert result.guardrail_name == "redactor"
        # Spy should NOT run because redact already returned non-allow.
        assert spy.called is False

    @pytest.mark.asyncio
    async def test_check_output_skips_input_only_guards(self):
        """An input-direction guard must not fire on output checks."""
        pipe = GuardrailPipeline()
        # QuotaGuardrail is input-only.
        quota = QuotaGuardrail()
        quota._inner.check = AsyncMock(
            return_value=_LegacyResult(passed=False, action="block", reason="q")
        )
        pipe.add(quota)
        ctx = _make_ctx()
        result = await pipe.check_output("some text", ctx)
        assert result.passed is True
        assert result.action == "allow"
        # Inner check should not have been called for output direction.
        quota._inner.check.assert_not_called()

    @pytest.mark.asyncio
    async def test_check_output_runs_both_direction_guards(self):
        pipe = GuardrailPipeline()
        cf = ContentFilterGuardrail(patterns=["forbidden"])
        pipe.add(cf)
        ctx = _make_ctx()
        result = await pipe.check_output("this is forbidden content", ctx)
        assert result.passed is False
        assert result.action == "block"
        assert result.guardrail_name == "content_filter"


# ── ContentFilterGuardrail ──────────────────────────────────────────────


class TestContentFilterGuardrail:
    @pytest.mark.asyncio
    async def test_blocks_string_match(self):
        g = ContentFilterGuardrail(patterns=["badword"])
        ctx = _make_ctx()
        result = await g.check("this has badword in it", ctx)
        assert result.passed is False
        assert result.action == "block"
        assert "badword" in (result.reason or "")

    @pytest.mark.asyncio
    async def test_blocks_message_list_match(self):
        g = ContentFilterGuardrail(patterns=["secret"])
        ctx = _make_ctx()
        msgs = [
            {"role": "system", "content": "be helpful"},
            {"role": "user", "content": "tell me the secret code"},
        ]
        result = await g.check(msgs, ctx)
        assert result.passed is False
        assert result.action == "block"

    @pytest.mark.asyncio
    async def test_allows_when_no_match(self):
        g = ContentFilterGuardrail(patterns=["badword"])
        ctx = _make_ctx()
        result = await g.check("totally innocent message", ctx)
        assert result.passed is True
        assert result.action == "allow"

    @pytest.mark.asyncio
    async def test_no_patterns_means_allow_all(self):
        g = ContentFilterGuardrail(patterns=None)
        ctx = _make_ctx()
        result = await g.check("anything goes", ctx)
        assert result.passed is True
        assert result.action == "allow"

    @pytest.mark.asyncio
    async def test_invalid_pattern_is_skipped(self):
        # Invalid regex should not crash construction; valid one still works.
        g = ContentFilterGuardrail(patterns=["(unclosed", "good"])
        ctx = _make_ctx()
        # The valid pattern still blocks.
        blocked = await g.check("this is good", ctx)
        assert blocked.passed is False
        # Invalid pattern is silently dropped — does not block.
        allowed = await g.check("(unclosed", ctx)
        assert allowed.passed is True

    @pytest.mark.asyncio
    async def test_add_patterns_appends(self):
        g = ContentFilterGuardrail(patterns=["first"])
        g.add_patterns(["second"])
        ctx = _make_ctx()
        r1 = await g.check("has first", ctx)
        r2 = await g.check("has second", ctx)
        assert r1.passed is False
        assert r2.passed is False

    @pytest.mark.asyncio
    async def test_case_insensitive_match(self):
        g = ContentFilterGuardrail(patterns=["badword"])
        ctx = _make_ctx()
        result = await g.check("BADWORD shouted", ctx)
        assert result.passed is False


# ── PIIRedactionGuardrail ───────────────────────────────────────────────


class TestPIIRedactionGuardrail:
    @pytest.mark.asyncio
    async def test_redacts_email_in_string(self):
        g = PIIRedactionGuardrail()
        ctx = _make_ctx()
        result = await g.check("contact me at alice@example.com", ctx)
        assert result.passed is True
        assert result.action == "redact"
        assert result.modified_content is not None
        assert "alice@example.com" not in result.modified_content
        assert "[REDACTED]" in result.modified_content

    @pytest.mark.asyncio
    async def test_redacts_ssn_and_phone(self):
        g = PIIRedactionGuardrail()
        ctx = _make_ctx()
        text = "ssn 123-45-6789 phone +1 (415) 555-1234"
        result = await g.check(text, ctx)
        assert result.action == "redact"
        redacted = result.modified_content
        assert redacted is not None
        assert "123-45-6789" not in redacted
        assert "555-1234" not in redacted

    @pytest.mark.asyncio
    async def test_redacts_messages_list(self):
        g = PIIRedactionGuardrail()
        ctx = _make_ctx()
        msgs = [
            {"role": "user", "content": "email me at bob@bob.com"},
            {"role": "system", "content": "ok"},
        ]
        result = await g.check(msgs, ctx)
        assert result.action == "redact"
        assert result.modified_messages is not None
        assert len(result.modified_messages) == 2
        assert "bob@bob.com" not in result.modified_messages[0]["content"]
        assert "[REDACTED]" in result.modified_messages[0]["content"]
        # Untouched message is preserved unchanged.
        assert result.modified_messages[1]["content"] == "ok"

    @pytest.mark.asyncio
    async def test_no_pii_returns_allow(self):
        g = PIIRedactionGuardrail()
        ctx = _make_ctx()
        result = await g.check("just plain text", ctx)
        assert result.passed is True
        assert result.action == "allow"
        assert result.modified_content is None

    @pytest.mark.asyncio
    async def test_custom_patterns_replace_defaults(self):
        g = PIIRedactionGuardrail(patterns={"project": r"\bPROJECT-X\b"})
        ctx = _make_ctx()
        # Default patterns (email/phone/ssn) should NOT be active.
        r1 = await g.check("email me at x@y.com", ctx)
        assert r1.action == "allow"
        # Custom pattern should redact.
        r2 = await g.check("work on PROJECT-X tomorrow", ctx)
        assert r2.action == "redact"
        assert "PROJECT-X" not in (r2.modified_content or "")

    @pytest.mark.asyncio
    async def test_empty_patterns_means_allow_all(self):
        g = PIIRedactionGuardrail(patterns={})
        ctx = _make_ctx()
        result = await g.check("alice@example.com 123-45-6789", ctx)
        assert result.passed is True
        assert result.action == "allow"


# ── QuotaGuardrail ──────────────────────────────────────────────────────


class _LegacyResult:
    """Mimic src.infra.telemetry.quota.GuardrailResult for testing."""

    def __init__(self, *, passed: bool, action: str, reason: str = "", scope: str = ""):
        self.passed = passed
        self.action = action
        self.reason = reason
        self.scope = scope


class TestQuotaGuardrail:
    @pytest.mark.asyncio
    async def test_passes_through_allow(self):
        g = QuotaGuardrail()
        g._inner.check = AsyncMock(
            return_value=_LegacyResult(passed=True, action="allow")
        )
        ctx = _make_ctx()
        result = await g.check("anything", ctx)
        assert result.passed is True
        assert result.action == "allow"
        g._inner.check.assert_awaited_once_with(ctx.workspace_id)

    @pytest.mark.asyncio
    async def test_passes_through_block_with_reason(self):
        g = QuotaGuardrail()
        g._inner.check = AsyncMock(
            return_value=_LegacyResult(
                passed=False, action="block",
                reason="Workspace daily quota exceeded (1000/1000)",
                scope="workspace",
            )
        )
        ctx = _make_ctx()
        result = await g.check("anything", ctx)
        assert result.passed is False
        assert result.action == "block"
        assert "quota exceeded" in (result.reason or "").lower()

    @pytest.mark.asyncio
    async def test_block_falls_back_to_default_reason(self):
        g = QuotaGuardrail()
        g._inner.check = AsyncMock(
            return_value=_LegacyResult(passed=False, action="block", reason="")
        )
        ctx = _make_ctx()
        result = await g.check("anything", ctx)
        assert result.passed is False
        assert result.reason == "Quota exceeded"

    @pytest.mark.asyncio
    async def test_direction_is_input_only(self):
        g = QuotaGuardrail()
        assert g.direction == "input"
        assert g.applies_to("input") is True
        assert g.applies_to("output") is False


# ── PolicyGuardrail ─────────────────────────────────────────────────────


class TestPolicyGuardrail:
    @pytest.mark.asyncio
    async def test_allow_when_no_policy(self):
        g = PolicyGuardrail()
        ctx = _make_ctx(workspace_settings=None)
        result = await g.check("anything", ctx)
        assert result.passed is True
        assert result.action == "allow"

    @pytest.mark.asyncio
    async def test_allow_when_policy_empty(self):
        g = PolicyGuardrail()
        ctx = _make_ctx(workspace_settings={"policy": {}})
        result = await g.check("anything", ctx)
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_block_disallowed_model(self):
        g = PolicyGuardrail()
        agent = _make_agent(model="gpt-4")
        ctx = _make_ctx(
            agent=agent,
            workspace_settings={"policy": {"allowed_models": ["deepseek-v4-flash"]}},
        )
        result = await g.check("anything", ctx)
        assert result.passed is False
        assert result.action == "block"
        assert "gpt-4" in (result.reason or "")
        assert "deepseek-v4-flash" in (result.reason or "")

    @pytest.mark.asyncio
    async def test_allow_when_model_in_whitelist(self):
        g = PolicyGuardrail()
        agent = _make_agent(model="deepseek-v4-flash")
        ctx = _make_ctx(
            agent=agent,
            workspace_settings={"policy": {"allowed_models": ["deepseek-v4-flash", "gpt-4"]}},
        )
        result = await g.check("anything", ctx)
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_block_disallowed_tool(self):
        g = PolicyGuardrail()
        agent = _make_agent(tools=["todo_write", "shell_exec"])
        ctx = _make_ctx(
            agent=agent,
            workspace_settings={
                "policy": {"allowed_tools": ["todo_write", "fs_read"]}
            },
        )
        result = await g.check("anything", ctx)
        assert result.passed is False
        assert result.action == "block"
        assert "shell_exec" in (result.reason or "")

    @pytest.mark.asyncio
    async def test_allow_when_all_tools_in_whitelist(self):
        g = PolicyGuardrail()
        agent = _make_agent(tools=["todo_write", "fs_read"])
        ctx = _make_ctx(
            agent=agent,
            workspace_settings={"policy": {"allowed_tools": ["todo_write", "fs_read"]}},
        )
        result = await g.check("anything", ctx)
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_direction_is_input_only(self):
        g = PolicyGuardrail()
        assert g.direction == "input"
        assert g.applies_to("output") is False

    @pytest.mark.asyncio
    async def test_non_dict_policy_is_treated_as_empty(self):
        g = PolicyGuardrail()
        ctx = _make_ctx(workspace_settings={"policy": "not-a-dict"})
        result = await g.check("anything", ctx)
        assert result.passed is True


# ── Test doubles ────────────────────────────────────────────────────────


class _BlockingGuard(Guardrail):
    name = "blocking"
    direction = "input"

    def __init__(self, name: str = "blocking") -> None:
        self.name = name

    async def check(self, content, ctx) -> GuardrailResult:
        return GuardrailResult(passed=False, action="block", reason="blocked")


class _SpyGuard(Guardrail):
    name = "spy"
    direction = "input"

    def __init__(self, name: str = "spy") -> None:
        self.name = name
        self.called = False

    async def check(self, content, ctx) -> GuardrailResult:
        self.called = True
        return GuardrailResult(passed=True, action="allow")


class _AlwaysRedactGuard(Guardrail):
    name = "always_redact"
    direction = "input"

    def __init__(self, name: str = "always_redact") -> None:
        self.name = name

    async def check(self, content, ctx) -> GuardrailResult:
        return GuardrailResult(
            passed=True, action="redact", reason="always redact",
            modified_content="[REDACTED]",
        )
