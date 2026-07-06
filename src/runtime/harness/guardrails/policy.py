"""PolicyGuardrail — workspace-level model/tool whitelist.

Enforces per-workspace policies:
- Allowed models: if set, the agent's model must be in the list.
- Allowed tools: if set, every tool in agent.tools must be in the list.

Policies are loaded from workspace settings (``settings`` JSON column)
or a dedicated policy store. P0 reads from ``ctx.workspace_settings``
(defaults to empty dict = no restrictions).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .base import Guardrail, GuardrailResult

if TYPE_CHECKING:
    from src.runtime.harness.context import HarnessContext


class PolicyGuardrail(Guardrail):
    """Block runs that violate workspace model/tool policy."""

    name = "policy"
    direction = "input"

    async def check(
        self,
        content: str | list[dict],
        ctx: "HarnessContext",
    ) -> GuardrailResult:
        policy = (
            ctx.workspace_settings.get("policy", {})
            if hasattr(ctx, "workspace_settings")
            else {}
        )
        if not isinstance(policy, dict):
            policy = {}

        # ── Model whitelist ──
        allowed_models = policy.get("allowed_models") or []
        if allowed_models and ctx.agent.model not in allowed_models:
            return GuardrailResult(
                passed=False,
                action="block",
                reason=(
                    f"Model {ctx.agent.model!r} not allowed for workspace "
                    f"{ctx.workspace_id} (allowed: {allowed_models})"
                ),
            )

        # ── Tool whitelist ──
        allowed_tools = policy.get("allowed_tools") or []
        if allowed_tools:
            disallowed = [
                t for t in ctx.agent.tools if t not in allowed_tools
            ]
            if disallowed:
                return GuardrailResult(
                    passed=False,
                    action="block",
                    reason=(
                        f"Tools {disallowed} not allowed for workspace "
                        f"{ctx.workspace_id}"
                    ),
                )

        return GuardrailResult(passed=True, action="allow")
