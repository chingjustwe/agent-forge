from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from src.runtime.models import StreamEvent


class RunAdapter(ABC):
    name: str = ""

    @abstractmethod
    async def run(
        self,
        session: dict,
        messages: list[dict],
        context: dict,
    ) -> AsyncIterator[StreamEvent]:
        ...
