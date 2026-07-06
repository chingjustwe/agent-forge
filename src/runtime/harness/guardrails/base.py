"""P3a: Guardrail abstractions + pipeline.

A guardrail is an input/output content check. The pipeline runs all
registered guardrails in order and short-circuits on the first
non-allow result.

Built-in guardrails:
- ContentFilterGuardrail — regex/keyword block (input + output)
- PIIRedactionGuardrail  — email/phone/SSN redact (input + output)
- QuotaGuardrail         — workspace quota (input; wraps existing impl)
- PolicyGuardrail        — model/tool whitelist per workspace (input)
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

if TYPE_CHECKING:
    from src.runtime.harness.context import HarnessContext


# ── Result model ────────────────────────────────────────────────────────


class GuardrailResult(BaseModel):
    """Result of a single guardrail check or the full pipeline."""

    passed: bool
    action: Literal["allow", "block", "redact"] = "allow"
    reason: str | None = None
    modified_content: str | None = None       # for output redact
    modified_messages: list[dict] | None = None  # for input redact
    guardrail_name: str | None = None         # which guardrail produced this


# ── Abstract guardrail ──────────────────────────────────────────────────


class Guardrail(ABC):
    """Abstract guardrail. Subclasses implement ``check``."""

    name: str
    direction: Literal["input", "output", "both"] = "both"

    @abstractmethod
    async def check(
        self,
        content: str | list[dict],
        ctx: "HarnessContext",
    ) -> GuardrailResult:
        ...

    def applies_to(self, direction: Literal["input", "output"]) -> bool:
        return self.direction == "both" or self.direction == direction


# ── Pipeline ────────────────────────────────────────────────────────────


class GuardrailPipeline:
    """Ordered list of guardrails. Runs all matching guards per direction."""

    def __init__(self) -> None:
        self._guardrails: list[Guardrail] = []

    def add(self, guardrail: Guardrail) -> None:
        self._guardrails.append(guardrail)

    def remove(self, name: str) -> bool:
        before = len(self._guardrails)
        self._guardrails = [g for g in self._guardrails if g.name != name]
        return len(self._guardrails) < before

    def list(self) -> list[Guardrail]:
        return list(self._guardrails)

    async def check_input(
        self,
        messages: list[dict],
        ctx: "HarnessContext",
    ) -> GuardrailResult:
        """Run all input-direction guardrails. Returns first non-allow."""
        for g in self._guardrails:
            if not g.applies_to("input"):
                continue
            result = await g.check(messages, ctx)
            result.guardrail_name = g.name
            if result.action != "allow":
                return result
        return GuardrailResult(passed=True, action="allow")

    async def check_output(
        self,
        content: str,
        ctx: "HarnessContext",
    ) -> GuardrailResult:
        """Run all output-direction guardrails. Returns first non-allow."""
        for g in self._guardrails:
            if not g.applies_to("output"):
                continue
            result = await g.check(content, ctx)
            result.guardrail_name = g.name
            if result.action != "allow":
                return result
        return GuardrailResult(passed=True, action="allow")
