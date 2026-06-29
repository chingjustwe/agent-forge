import json
import time
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError

from src.gateway.auth.roles import has_permission
from src.infra.settings import settings
from src.runtime.harness.pipeline import GuardrailPipeline
from src.runtime.harness.context import HarnessContext
from src.runtime.models import RuntimeConfig, StreamEvent
from src.runtime.adapters.direct_llm import DirectLLMAdapter

router = APIRouter()
_guardrail_pipeline = GuardrailPipeline.create_default()


def _get_adapter(config: RuntimeConfig) -> DirectLLMAdapter:
    if not settings.llm_api_key:
        raise RuntimeError(
            "LLM_API_KEY is not configured. "
            "Set it in .env file or export LLM_API_KEY environment variable."
        )
    return DirectLLMAdapter(
        api_key=settings.llm_api_key,
        model=config.model,
    )


async def _event_stream(
    messages: list[dict],
    config: RuntimeConfig,
    context: HarnessContext,
    trace_id: str,
    user_id: str,
) -> AsyncIterator[str]:
    ws_id = config.workspace_id
    async with context.tracer.span("chat.handler", trace_id, attributes={"ws_id": ws_id, "user_id": user_id}):
        start = time.monotonic()
        error = ""
        total_tokens = {"input": 0, "output": 0}
        try:
            adapter = _get_adapter(config)
            async with context.tracer.span("adapter.run", trace_id, parent_span_id=context.tracer._spans[-1].span_id if context.tracer._spans else None):
                async for event in adapter.run({}, messages, {}):
                    yield f"data: {event.model_dump_json()}\n\n"
                    if event.type == "status":
                        usage = event.data.get("usage", {})
                        total_tokens["input"] = usage.get("input_tokens", 0)
                        total_tokens["output"] = usage.get("output_tokens", 0)
        except Exception as e:
            error = str(e)
            yield f"data: {StreamEvent(type='error', data={'code': 'LLM_ERROR', 'message': error}).model_dump_json()}\n\n"

        duration_ms = int((time.monotonic() - start) * 1000)
        await context.record_request(
            trace_id=trace_id,
            user_id=user_id,
            ws_id=ws_id,
            agent=config.agent,
            model=config.model,
            status=200 if not error else 500,
            duration_ms=duration_ms,
            tokens=total_tokens,
            error=error,
        )


@router.post("/api/v1/chat")
async def chat(request: Request):
    user = getattr(request.state, "user", None)
    if not user:
        return JSONResponse(
            status_code=401,
            content={"error": {"code": "UNAUTHORIZED", "message": "Not authenticated"}},
        )

    if not has_permission(user.get("role", "viewer"), "member"):
        return JSONResponse(
            status_code=403,
            content={"error": {"code": "FORBIDDEN", "message": "Viewer role cannot send messages"}},
        )

    try:
        body = await request.json()
    except json.JSONDecodeError:
        return JSONResponse(
            status_code=400,
            content={"error": {"code": "BAD_REQUEST", "message": "Invalid JSON body"}},
        )

    try:
        config = RuntimeConfig(**body.get("config", {}))
    except ValidationError as e:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": str(e),
                }
            },
        )

    messages = body.get("messages", [])
    if not messages:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "messages must not be empty",
                }
            },
        )

    user_ws_ids = user.get("workspace_ids", [])
    config.workspace_id = user_ws_ids[0] if user_ws_ids else config.workspace_id
    trace_id = uuid.uuid4().hex
    context = HarnessContext()

    guardrail_result = await _guardrail_pipeline.check(config.workspace_id)
    if not guardrail_result.passed:
        return JSONResponse(
            status_code=429,
            content={"error": {"code": "RATE_LIMITED", "message": guardrail_result.reason}},
        )

    return StreamingResponse(
        _event_stream(messages, config, context, trace_id, user.get("id", "")),
        media_type="text/event-stream",
    )
