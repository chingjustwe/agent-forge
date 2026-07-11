from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from src.runtime.models import StreamEvent

if TYPE_CHECKING:
    from src.runtime.harness.context import HarnessContext


class RunAdapter(ABC):
    """Abstract base for agent run adapters (deepagents).

    Per spec D6: adapters receive ``HarnessContext`` (not ``dict``). The
    context carries identity, capability systems (tool_engine, guardrails,
    sandbox, memory, hooks), credentials, and telemetry — everything the
    adapter needs to execute one agent run without reaching into
    platform-level singletons directly.
    """

    name: str = ""

    @abstractmethod
    def run(
        self,
        messages: list[dict],
        ctx: "HarnessContext",
    ) -> AsyncIterator[StreamEvent]:
        ...
