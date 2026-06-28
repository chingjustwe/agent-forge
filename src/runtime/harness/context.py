from src.infra.telemetry.collector import TelemetryCollector
from src.infra.telemetry.metrics import metrics
from src.infra.telemetry.spans import tracer


class HarnessContext:
    def __init__(self, collector: TelemetryCollector | None = None):
        self.collector = collector or TelemetryCollector()
        self.metrics = metrics
        self.tracer = tracer

    async def record_request(self, **kwargs) -> str:
        return await self.collector.record_request(**kwargs)

    async def record_tool_call(self, **kwargs) -> None:
        await self.collector.record_tool_call(**kwargs)

    async def record_event(self, **kwargs) -> None:
        await self.collector.record_event(**kwargs)
