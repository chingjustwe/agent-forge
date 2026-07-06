"""PIIRedactionGuardrail — PII detection + redaction.

Detects common PII patterns (email, phone, SSN) and redacts them in
both input and output. Returns ``modified_content`` /
``modified_messages`` with matches replaced by ``[REDACTED]``.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .base import Guardrail, GuardrailResult

if TYPE_CHECKING:
    from src.runtime.harness.context import HarnessContext


# PII patterns (US-centric defaults; extend via constructor).
_DEFAULT_PATTERNS: dict[str, str] = {
    "email": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
    "ssn": r"\b\d{3}-\d{2}-\d{4}\b",
    "phone": r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b",
}


class PIIRedactionGuardrail(Guardrail):
    """Redact PII (email / phone / SSN) from content."""

    name = "pii_redaction"
    direction = "both"

    def __init__(self, patterns: dict[str, str] | None = None) -> None:
        source = _DEFAULT_PATTERNS if patterns is None else patterns
        self._compiled: list[tuple[str, re.Pattern]] = [
            (label, re.compile(pat))
            for label, pat in source.items()
        ]

    async def check(
        self,
        content: str | list[dict],
        ctx: "HarnessContext",
    ) -> GuardrailResult:
        if not self._compiled:
            return GuardrailResult(passed=True, action="allow")

        if isinstance(content, str):
            redacted, count = _redact_text(content, self._compiled)
            if count == 0:
                return GuardrailResult(passed=True, action="allow")
            return GuardrailResult(
                passed=True,
                action="redact",
                reason=f"Redacted {count} PII occurrence(s)",
                modified_content=redacted,
            )

        if isinstance(content, list):
            modified = False
            new_messages: list[dict] = []
            total_count = 0
            for msg in content:
                if not isinstance(msg, dict):
                    new_messages.append(msg)
                    continue
                c = msg.get("content", "")
                if not isinstance(c, str):
                    new_messages.append(msg)
                    continue
                redacted, count = _redact_text(c, self._compiled)
                total_count += count
                if count > 0:
                    modified = True
                    new_messages.append({**msg, "content": redacted})
                else:
                    new_messages.append(msg)
            if not modified:
                return GuardrailResult(passed=True, action="allow")
            return GuardrailResult(
                passed=True,
                action="redact",
                reason=f"Redacted {total_count} PII occurrence(s)",
                modified_messages=new_messages,
            )

        return GuardrailResult(passed=True, action="allow")


def _redact_text(
    text: str, patterns: list[tuple[str, re.Pattern]]
) -> tuple[str, int]:
    """Apply all patterns; return (redacted_text, total_replacements)."""
    total = 0
    result = text
    for _label, pattern in patterns:
        result, n = pattern.subn("[REDACTED]", result)
        total += n
    return result, total
