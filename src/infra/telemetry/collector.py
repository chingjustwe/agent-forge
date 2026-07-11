import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.infra.db.engine import async_session
from src.infra.db.models import RequestLog
from src.infra.telemetry.logs import info as log_info
from src.infra.telemetry.metrics import metrics
from src.infra.telemetry.otlp import OTelExporter
from src.infra.telemetry.spans import tracer


class TelemetryCollector:
    def __init__(self, otel_exporter: OTelExporter | None = None):
        self.otel = otel_exporter or OTelExporter()

    def _trace_id(self, trace_id: str | None = None) -> str:
        return trace_id or uuid.uuid4().hex

    async def record_request(
        self,
        trace_id: str | None = None,
        user_id: str = "",
        ws_id: str = "",
        agent: str = "",
        model: str = "",
        status: int = 200,
        duration_ms: int = 0,
        tokens: dict | None = None,
        error: str = "",
        tenant_id: str = "",
        cost: float = 0.0,
    ) -> str:
        tid = self._trace_id(trace_id)
        tokens_data = tokens or {}
        input_tokens = tokens_data.get("input", 0)
        output_tokens = tokens_data.get("output", 0)

        tags = {"ws": ws_id} if ws_id else {}
        metrics.increment_counter("agent.requests.total", tags)
        metrics.observe_histogram("agent.requests.duration", float(duration_ms), tags)
        metrics.increment_counter("agent.tokens.total", {**tags, "model": model})
        if error:
            metrics.increment_counter("agent.errors.total", {**tags, "error_type": "error"})

        async with async_session() as session:
            await session.execute(
                text("""
                    INSERT INTO request_logs (id, trace_id, user_id, workspace_id, tenant_id, agent, model, status_code, duration_ms, input_tokens, output_tokens, cost, error, created_at)
                    VALUES (:id, :trace_id, :user_id, :ws_id, :tenant_id, :agent, :model, :status, :duration_ms, :input_tokens, :output_tokens, :cost, :error, :created_at)
                """),
                {
                    "id": uuid.uuid4().hex,
                    "trace_id": tid,
                    "user_id": user_id,
                    "ws_id": ws_id,
                    "tenant_id": tenant_id,
                    "agent": agent,
                    "model": model,
                    "status": status,
                    "duration_ms": duration_ms,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cost": cost,
                    "error": error,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            await session.commit()

        log_info(tid, "request.recorded", agent=agent, model=model, status=status, duration_ms=duration_ms)
        return tid

    async def record_tool_call(
        self,
        trace_id: str,
        tool_name: str,
        args: dict | None = None,
        result: Any = None,
        duration_ms: int = 0,
        success: bool = True,
    ) -> None:
        tags = {"tool": tool_name}
        metrics.increment_counter("agent.tools.called", tags)
        if not success:
            metrics.increment_counter("agent.errors.total", {**tags, "error_type": "tool_error"})

        async with async_session() as session:
            await session.execute(
                text("""
                    INSERT INTO tool_calls (id, trace_id, tool_name, args, result, duration_ms, success, created_at)
                    VALUES (:id, :trace_id, :tool_name, :args, :result, :duration_ms, :success, :created_at)
                """),
                {
                    "id": uuid.uuid4().hex,
                    "trace_id": trace_id,
                    "tool_name": tool_name,
                    "args": json.dumps(args) if args else "{}",
                    "result": json.dumps({"truncated": True}) if result else "{}",
                    "duration_ms": duration_ms,
                    "success": 1 if success else 0,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            await session.commit()

        log_info(trace_id, "tool_call.recorded", tool_name=tool_name, duration_ms=duration_ms, success=success)

    async def record_event(
        self,
        trace_id: str,
        level: str,
        event: str,
        data: dict | None = None,
    ) -> None:
        async with async_session() as session:
            await session.execute(
                text("""
                    INSERT INTO events_log (trace_id, level, event, data, created_at)
                    VALUES (:trace_id, :level, :event, :data, :created_at)
                """),
                {
                    "trace_id": trace_id,
                    "level": level,
                    "event": event,
                    "data": json.dumps(data) if data else "{}",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            await session.commit()

    async def get_summary(
        self,
        ws_id: str,
        since: str | None = None,
        user_id: str | None = None,
    ) -> dict:
        async with async_session() as session:
            conditions = "WHERE workspace_id = :ws_id"
            params: dict[str, Any] = {"ws_id": ws_id}
            if since:
                conditions += " AND created_at >= :since"
                params["since"] = since
            if user_id:
                conditions += " AND user_id = :user_id"
                params["user_id"] = user_id

            row = (await session.execute(
                text(f"""
                    SELECT
                        COUNT(*) as total_requests,
                        COALESCE(AVG(duration_ms), 0) as avg_latency_ms,
                        COALESCE(SUM(input_tokens), 0) as input_tokens,
                        COALESCE(SUM(output_tokens), 0) as output_tokens,
                        COALESCE(SUM(input_tokens + output_tokens), 0) as total_tokens,
                        COALESCE(SUM(cost), 0) as total_cost,
                        COALESCE(SUM(CASE WHEN error != '' THEN 1 ELSE 0 END) * 1.0 / NULLIF(COUNT(*), 0), 0) as error_rate
                    FROM request_logs {conditions}
                """),
                params,
            )).one()

            return {
                "total_requests": row.total_requests,
                "avg_latency_ms": round(float(row.avg_latency_ms), 2),
                "input_tokens": row.input_tokens or 0,
                "output_tokens": row.output_tokens or 0,
                "total_tokens": row.total_tokens or 0,
                "total_cost": round(float(row.total_cost or 0), 4),
                "error_rate": round(float(row.error_rate), 4),
                "active_sessions": 0,
            }

    async def get_requests(
        self,
        ws_id: str,
        limit: int = 50,
        offset: int = 0,
        status: int | None = None,
        model: str | None = None,
        since: str | None = None,
    ) -> list[dict]:
        async with async_session() as session:
            conditions = ["WHERE workspace_id = :ws_id"]
            params: dict[str, Any] = {"ws_id": ws_id, "limit": limit, "offset": offset}
            if status is not None:
                conditions.append("AND status_code = :status")
                params["status"] = status
            if model:
                conditions.append("AND model = :model")
                params["model"] = model
            if since:
                conditions.append("AND created_at >= :since")
                params["since"] = since

            rows = (await session.execute(
                text(f"""
                    SELECT id, trace_id, user_id, workspace_id, agent, model, status_code, duration_ms, input_tokens, output_tokens, error, created_at
                    FROM request_logs {' '.join(conditions)}
                    ORDER BY created_at DESC
                    LIMIT :limit OFFSET :offset
                """),
                params,
            )).all()

            return [dict(r._mapping) for r in rows]

    async def get_request_detail(self, ws_id: str, trace_id: str) -> dict | None:
        async with async_session() as session:
            row = (await session.execute(
                text("""
                    SELECT id, trace_id, user_id, workspace_id, agent, model, status_code, duration_ms, input_tokens, output_tokens, error, created_at
                    FROM request_logs WHERE trace_id = :trace_id AND workspace_id = :ws_id
                """),
                {"trace_id": trace_id, "ws_id": ws_id},
            )).one_or_none()

            if not row:
                return None

            tool_calls = (await session.execute(
                text("""
                    SELECT id, trace_id, tool_name, args, result, duration_ms, success, created_at
                    FROM tool_calls WHERE trace_id = :trace_id
                """),
                {"trace_id": trace_id},
            )).all()

            events = (await session.execute(
                text("""
                    SELECT id, trace_id, level, event, data, created_at
                    FROM events_log WHERE trace_id = :trace_id
                """),
                {"trace_id": trace_id},
            )).all()

            spans = [
                s.to_dict()
                for s in tracer.get_spans(trace_id)
            ]

            return {
                "request": dict(row._mapping),
                "spans": spans,
                "tool_calls": [dict(t._mapping) for t in tool_calls],
                "events": [dict(e._mapping) for e in events],
            }

    async def get_daily_tokens(
        self,
        ws_id: str,
        since: str | None = None,
        until: str | None = None,
        user_id: str | None = None,
    ) -> list[dict]:
        async with async_session() as session:
            conditions = ["WHERE workspace_id = :ws_id"]
            params: dict[str, Any] = {"ws_id": ws_id}
            if since:
                conditions.append("AND date(created_at) >= :since")
                params["since"] = since
            if until:
                conditions.append("AND date(created_at) <= :until")
                params["until"] = until
            if user_id:
                conditions.append("AND user_id = :user_id")
                params["user_id"] = user_id

            rows = (await session.execute(
                text(f"""
                    SELECT date(created_at) as date, SUM(input_tokens) as input_tokens, SUM(output_tokens) as output_tokens, 0.0 as cost_usd
                    FROM request_logs {' '.join(conditions)}
                    GROUP BY date(created_at)
                    ORDER BY date ASC
                """),
                params,
            )).all()

            return [dict(r._mapping) for r in rows]

    async def get_latency(
        self,
        ws_id: str,
        since: str | None = None,
        until: str | None = None,
        user_id: str | None = None,
    ) -> dict:
        async with async_session() as session:
            conditions = ["WHERE workspace_id = :ws_id"]
            params: dict[str, Any] = {"ws_id": ws_id}
            if since:
                conditions.append("AND created_at >= :since")
                params["since"] = since
            if until:
                conditions.append("AND created_at <= :until")
                params["until"] = until
            if user_id:
                conditions.append("AND user_id = :user_id")
                params["user_id"] = user_id

            durations_row = (await session.execute(
                text(f"""
                    SELECT duration_ms FROM request_logs {' '.join(conditions)}
                """),
                params,
            )).all()

            durations = sorted([r.duration_ms for r in durations_row if r.duration_ms])
            if not durations:
                return {"p50_ms": 0, "p95_ms": 0, "p99_ms": 0, "over_time": []}

            n = len(durations)
            p50 = durations[int(n * 0.5)]
            p95 = durations[int(n * 0.95)]
            p99 = durations[int(n * 0.99)]

            time_rows = (await session.execute(
                text(f"""
                    SELECT
                        strftime('%Y-%m-%dT%H:00:00', created_at) as bucket,
                        AVG(duration_ms) as avg_dur
                    FROM request_logs {' '.join(conditions)}
                    GROUP BY bucket
                    ORDER BY bucket ASC
                    LIMIT 100
                """),
                params,
            )).all()

            return {
                "p50_ms": p50,
                "p95_ms": p95,
                "p99_ms": p99,
                "over_time": [
                    {
                        "bucket": r.bucket,
                        "p50": float(r.avg_dur) if r.avg_dur else 0,
                        "p95": float(r.avg_dur) if r.avg_dur else 0,
                        "p99": float(r.avg_dur) if r.avg_dur else 0,
                    }
                    for r in time_rows
                ],
            }

    async def get_errors(self, ws_id: str, since: str | None = None, user_id: str | None = None) -> list[dict]:
        async with async_session() as session:
            conditions = ["WHERE workspace_id = :ws_id AND error != ''"]
            params: dict[str, Any] = {"ws_id": ws_id}
            if since:
                conditions.append("AND created_at >= :since")
                params["since"] = since
            if user_id:
                conditions.append("AND user_id = :user_id")
                params["user_id"] = user_id

            rows = (await session.execute(
                text(f"""
                    SELECT error as error_type, COUNT(*) as count, MAX(created_at) as last_seen
                    FROM request_logs {' '.join(conditions)}
                    GROUP BY error
                    ORDER BY count DESC
                """),
                params,
            )).all()

            return [dict(r._mapping) for r in rows]

    async def get_tenant_usage(
        self,
        tenant_id: str,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> dict:
        async with async_session() as session:
            query = select(
                func.count(RequestLog.id),
                func.coalesce(func.sum(RequestLog.input_tokens), 0),
                func.coalesce(func.sum(RequestLog.output_tokens), 0),
                func.coalesce(func.sum(RequestLog.cost), 0.0),
                RequestLog.workspace_id,
            ).where(RequestLog.tenant_id == tenant_id)

            if since:
                query = query.where(RequestLog.created_at >= since.isoformat())
            if until:
                query = query.where(RequestLog.created_at <= until.isoformat())

            query = query.group_by(RequestLog.workspace_id)
            rows = await session.execute(query)

            by_workspace = []
            total_requests = 0
            total_input = 0
            total_output = 0
            total_cost = 0.0

            for row in rows:
                ws_req, ws_in, ws_out, ws_cost, ws_id = row
                total_requests += ws_req
                total_input += ws_in
                total_output += ws_out
                total_cost += ws_cost
                by_workspace.append({
                    "workspace_id": ws_id,
                    "total_requests": ws_req,
                    "input_tokens": ws_in,
                    "output_tokens": ws_out,
                    "total_tokens": ws_in + ws_out,
                    "total_cost": float(ws_cost),
                })

            return {
                "total_requests": total_requests,
                "input_tokens": total_input,
                "output_tokens": total_output,
                "total_tokens": total_input + total_output,
                "total_cost": total_cost,
                "by_workspace": by_workspace,
            }
