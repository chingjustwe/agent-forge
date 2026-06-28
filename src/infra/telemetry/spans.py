import time
import uuid
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from src.infra.telemetry.logs import info as log_info


class Span:
    def __init__(
        self,
        name: str,
        trace_id: str,
        parent_span_id: str | None = None,
        attributes: dict | None = None,
    ):
        self.span_id = uuid.uuid4().hex[:16]
        self.trace_id = trace_id
        self.parent_span_id = parent_span_id
        self.name = name
        self.attributes = attributes or {}
        self.start_time: float | None = None
        self.end_time: float | None = None
        self.duration_ms: float | None = None

    def start(self) -> None:
        self.start_time = time.monotonic()

    def finish(self) -> None:
        self.end_time = time.monotonic()
        if self.start_time is not None:
            self.duration_ms = (self.end_time - self.start_time) * 1000

    def to_dict(self) -> dict:
        return {
            "span_id": self.span_id,
            "trace_id": self.trace_id,
            "parent_span_id": self.parent_span_id,
            "name": self.name,
            "attributes": self.attributes,
            "start_time": self.start_time,
            "duration_ms": self.duration_ms,
        }


SPAN_NAMES = frozenset({
    "chat.handler",
    "runtime.run",
    "harness.pre_guardrails",
    "adapter.run",
    "tool.execute",
    "harness.post_guardrails",
    "llm.call",
})


class Tracer:
    def __init__(self):
        self._spans: list[Span] = []

    @asynccontextmanager
    async def span(
        self,
        name: str,
        trace_id: str,
        parent_span_id: str | None = None,
        attributes: dict | None = None,
    ) -> AsyncIterator[Span]:
        span = Span(name, trace_id, parent_span_id, attributes)
        span.start()
        self._spans.append(span)
        log_info(trace_id, f"span.start", span_name=name, span_id=span.span_id)
        try:
            yield span
        finally:
            span.finish()
            log_info(
                trace_id,
                f"span.finish",
                span_name=name,
                span_id=span.span_id,
                duration_ms=span.duration_ms,
            )

    def get_spans(self, trace_id: str) -> list[Span]:
        return [s for s in self._spans if s.trace_id == trace_id]

    def reset(self) -> None:
        self._spans.clear()


tracer = Tracer()
