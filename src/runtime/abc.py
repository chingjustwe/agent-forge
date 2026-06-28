from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from src.runtime.models import RuntimeConfig, StreamEvent


class AgentRuntime(ABC):

    @abstractmethod
    async def run(
        self,
        session_id: str,
        messages: list[dict],
        config: RuntimeConfig,
    ) -> AsyncIterator[StreamEvent]:
        ...
