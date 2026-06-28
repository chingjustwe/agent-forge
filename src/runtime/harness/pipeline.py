from collections.abc import Callable, Awaitable

from src.infra.telemetry.quota import QuotaGuardrail, GuardrailResult


class GuardrailPipeline:
    def __init__(self):
        self._rules: list[Callable[[str], Awaitable[GuardrailResult]]] = []

    def add_rule(self, rule: Callable[[str], Awaitable[GuardrailResult]]) -> None:
        self._rules.append(rule)

    async def check(self, workspace_id: str) -> GuardrailResult:
        for rule in self._rules:
            result = await rule(workspace_id)
            if not result.passed:
                return result
        return GuardrailResult(passed=True, action="allow")

    @classmethod
    def create_default(cls) -> "GuardrailPipeline":
        pipeline = cls()
        pipeline.add_rule(QuotaGuardrail().check)
        return pipeline
