"""QuotaGuardrail — wraps the existing telemetry quota check.

Bridges the legacy ``src.infra.telemetry.quota.QuotaGuardrail`` (which
takes ``workspace_id``) into the new ``Guardrail`` ABC (which takes
``HarnessContext``). The underlying quota logic (workspace + tenant
limits) stays unchanged.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from src.infra.telemetry.quota import QuotaGuardrail as _LegacyQuota

from .base import Guardrail, GuardrailResult

if TYPE_CHECKING:
    from src.runtime.harness.context import HarnessContext


class QuotaGuardrail(Guardrail):
    """Block requests when the workspace or tenant quota is exhausted."""

    name = "quota"
    direction = "input"

    def __init__(self) -> None:
        self._inner = _LegacyQuota()

    async def check(
        self,
        content: str | list[dict],
        ctx: "HarnessContext",
    ) -> GuardrailResult:
        result = await self._inner.check(ctx.workspace_id)
        if result.passed:
            return GuardrailResult(passed=True, action="allow")
        return GuardrailResult(
            passed=False,
            action="block",
            reason=result.reason or "Quota exceeded",
        )
