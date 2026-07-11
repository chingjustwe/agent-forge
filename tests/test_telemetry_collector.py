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
    # Input/output tokens are tracked separately
    assert summary["input_tokens"] >= 10
    assert summary["output_tokens"] >= 20
    assert summary["input_tokens"] + summary["output_tokens"] == summary["total_tokens"]
    # Cost is aggregated
    assert "total_cost" in summary
    assert summary["total_cost"] >= 0


@pytest.mark.asyncio
async def test_get_summary_filters_by_user_id():
    """get_summary(user_id=...) should only count that user's requests."""
    collector = TelemetryCollector()
    await collector.record_request(
        user_id="u-alice",
        ws_id="ws-filter-test",
        agent="a1",
        model="m1",
        status=200,
        duration_ms=10,
        tokens={"input": 100, "output": 200},
    )
    await collector.record_request(
        user_id="u-bob",
        ws_id="ws-filter-test",
        agent="a1",
        model="m1",
        status=200,
        duration_ms=20,
        tokens={"input": 50, "output": 50},
    )

    # Workspace view: sees both users
    all_summary = await collector.get_summary("ws-filter-test")
    assert all_summary["total_requests"] >= 2
    assert all_summary["input_tokens"] >= 150
    assert all_summary["output_tokens"] >= 250

    # User-scoped view: only Alice's data
    alice_summary = await collector.get_summary("ws-filter-test", user_id="u-alice")
    assert alice_summary["total_requests"] >= 1
    assert alice_summary["input_tokens"] >= 100
    assert alice_summary["output_tokens"] >= 200
    # Alice's input should be less than the workspace total (which includes Bob)
    assert alice_summary["input_tokens"] < all_summary["input_tokens"]


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
async def test_record_request_writes_tenant_id_and_cost():
    """record_request should persist the real tenant_id (not user_id) and cost."""
    from src.infra.db.engine import async_session
    from sqlalchemy import text

    collector = TelemetryCollector()
    await collector.record_request(
        user_id="u-tenant-test",
        ws_id="ws-tenant-test",
        agent="a1",
        model="deepseek-v4-flash",
        status=200,
        duration_ms=50,
        tokens={"input": 100, "output": 200},
        tenant_id="real-tenant-id",
        cost=0.05,
    )

    async with async_session() as session:
        row = (
            await session.execute(
                text("SELECT tenant_id, cost FROM request_logs WHERE workspace_id = 'ws-tenant-test' ORDER BY created_at DESC LIMIT 1")
            )
        ).one_or_none()

    assert row is not None
    assert row.tenant_id == "real-tenant-id"
    assert row.cost == 0.05


@pytest.mark.asyncio
async def test_get_tenant_usage_returns_data():
    """get_tenant_usage should aggregate by the real tenant_id."""
    collector = TelemetryCollector()
    await collector.record_request(
        user_id="u-usage-test",
        ws_id="ws-usage-test",
        agent="a1",
        model="deepseek-v4-flash",
        status=200,
        duration_ms=50,
        tokens={"input": 500, "output": 500},
        tenant_id="tenant-usage-test",
        cost=1.5,
    )

    result = await collector.get_tenant_usage("tenant-usage-test")
    assert result["total_requests"] >= 1
    assert result["total_tokens"] >= 1000
    assert result["total_cost"] >= 1.5
    # Input/output tokens are tracked separately
    assert result["input_tokens"] >= 500
    assert result["output_tokens"] >= 500
    assert result["input_tokens"] + result["output_tokens"] == result["total_tokens"]
    ws_ids = [ws["workspace_id"] for ws in result["by_workspace"]]
    assert "ws-usage-test" in ws_ids
    # Per-workspace items also carry input/output breakdown
    ws_item = next(ws for ws in result["by_workspace"] if ws["workspace_id"] == "ws-usage-test")
    assert ws_item["input_tokens"] >= 500
    assert ws_item["output_tokens"] >= 500
    assert ws_item["input_tokens"] + ws_item["output_tokens"] == ws_item["total_tokens"]


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
