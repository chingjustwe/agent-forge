import pytest
from src.infra.telemetry.collector import TelemetryCollector
from src.infra.telemetry.quota import QuotaGuardrail
from src.infra.telemetry.metrics import metrics
from src.infra.telemetry.spans import tracer


@pytest.mark.asyncio
async def test_record_and_retrieve_request():
    collector = TelemetryCollector()
    tid = await collector.record_request(
        user_id="u1",
        ws_id="ws1",
        agent="test-agent",
        model="test-model",
        status=200,
        duration_ms=100,
        tokens={"input": 10, "output": 20},
    )
    assert tid is not None
    assert len(tid) > 0

    summary = await collector.get_summary("ws1")
    assert summary["total_requests"] >= 1
    assert summary["total_tokens"] >= 30
    assert summary["avg_latency_ms"] > 0


@pytest.mark.asyncio
async def test_record_tool_call():
    collector = TelemetryCollector()
    tid = await collector.record_request(ws_id="ws1")
    await collector.record_tool_call(
        trace_id=tid,
        tool_name="search",
        args={"query": "hello"},
        duration_ms=50,
        success=True,
    )
    detail = await collector.get_request_detail("ws1", tid)
    assert detail is not None
    assert len(detail["tool_calls"]) >= 1
    assert detail["tool_calls"][0]["tool_name"] == "search"


@pytest.mark.asyncio
async def test_record_event():
    collector = TelemetryCollector()
    tid = await collector.record_request(ws_id="ws1")
    await collector.record_event(tid, "info", "test.event", {"key": "val"})
    detail = await collector.get_request_detail("ws1", tid)
    assert detail is not None
    assert len(detail["events"]) >= 1


@pytest.mark.asyncio
async def test_get_requests_pagination():
    collector = TelemetryCollector()
    for i in range(3):
        await collector.record_request(ws_id="ws_pag", agent=f"a{i}")

    requests = await collector.get_requests("ws_pag", limit=2, offset=0)
    assert len(requests) <= 2


@pytest.mark.asyncio
async def test_errors():
    collector = TelemetryCollector()
    await collector.record_request(ws_id="ws_err", error="test error")
    errors = await collector.get_errors("ws_err")
    assert len(errors) >= 1


@pytest.mark.asyncio
async def test_metrics_collector():
    metrics.reset()
    metrics.increment_counter("test.counter", {"tag": "val"})
    assert metrics.counter("test.counter", {"tag": "val"}) == 1

    metrics.observe_histogram("test.histogram", 100.0)
    snap = metrics.histogram_snapshot("test.histogram")
    assert snap["count"] == 1


@pytest.mark.asyncio
async def test_quota_guardrail():
    from src.infra.db.engine import async_session
    from src.infra.db.models import Workspace

    async with async_session() as session:
        ws = Workspace(
            id="quota-ws",
            tenant_id="t1",
            name="Quota Test",
            max_tokens_per_day=100,
        )
        session.add(ws)
        await session.commit()

    guardrail = QuotaGuardrail()
    result = await guardrail.check("quota-ws")
    assert result.passed is True

    await guardrail.record_usage("quota-ws", 200)
    result = await guardrail.check("quota-ws")
    assert result.passed is False
    assert "exceeded" in result.reason.lower()

    usage = await guardrail.get_usage("quota-ws")
    assert usage["tokens_used"] >= 200


@pytest.mark.asyncio
async def test_quota_unlimited():
    from src.infra.db.engine import async_session
    from src.infra.db.models import Workspace

    async with async_session() as session:
        ws = Workspace(
            id="quota-unlimited",
            tenant_id="t1",
            name="Unlimited",
            max_tokens_per_day=0,
        )
        session.add(ws)
        await session.commit()

    guardrail = QuotaGuardrail()
    result = await guardrail.check("quota-unlimited")
    assert result.passed is True


@pytest.mark.asyncio
async def test_tracer_spans():
    tracer.reset()
    trace_id = "trace-test-1"
    async with tracer.span("chat.handler", trace_id):
        async with tracer.span("adapter.run", trace_id):
            pass

    spans = tracer.get_spans(trace_id)
    assert len(spans) == 2
    assert spans[0].name == "chat.handler"
    assert spans[1].name == "adapter.run"
    assert spans[1].duration_ms is not None
