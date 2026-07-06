"""ContentFilterGuardrail — regex/keyword block.

Blocks messages matching configured patterns. Operates on both input
and output. Uses the per-workspace ``GuardrailConfig`` rows loaded by
``HarnessRegistry``.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .base import Guardrail, GuardrailResult

if TYPE_CHECKING:
    from src.runtime.harness.context import HarnessContext


class ContentFilterGuardrail(Guardrail):
    """Block content matching any of the configured regex patterns."""

    name = "content_filter"
    direction = "both"

    def __init__(self, patterns: list[str] | None = None) -> None:
        # Pre-compile patterns for efficiency.
        self._patterns: list[re.Pattern] = []
        for p in patterns or []:
            try:
                self._patterns.append(re.compile(p, re.IGNORECASE))
            except re.error:
                # Skip invalid patterns at registration time.
                continue

    def add_patterns(self, patterns: list[str]) -> None:
        for p in patterns:
            try:
                self._patterns.append(re.compile(p, re.IGNORECASE))
            except re.error:
                continue

    async def check(
        self,
        content: str | list[dict],
        ctx: "HarnessContext",
    ) -> GuardrailResult:
        if not self._patterns:
            return GuardrailResult(passed=True, action="allow")

        text = _extract_text(content)
        for pattern in self._patterns:
            match = pattern.search(text)
            if match:
                return GuardrailResult(
                    passed=False,
                    action="block",
                    reason=(
                        f"Content blocked by pattern {pattern.pattern!r} "
                        f"(matched {match.group(0)!r})"
                    ),
                )
        return GuardrailResult(passed=True, action="allow")


def _extract_text(content: str | list[dict]) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for msg in content:
            if isinstance(msg, dict):
                c = msg.get("content", "")
                if isinstance(c, str):
                    parts.append(c)
        return "\n".join(parts)
    return ""
