import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.gateway.auth.rbac import get_workspace_member_role
from src.gateway.auth.roles import has_permission
from src.infra.db.engine import async_session
from src.infra.db.models import ChatMessage, ChatSession, WorkspaceMember
from src.infra.db.session import get_db
from src.infra.settings import settings
from src.runtime.harness.pipeline import GuardrailPipeline
from src.runtime.harness.context import HarnessContext
from src.runtime.models import RuntimeConfig, StreamEvent
from src.runtime.adapters.direct_llm import DirectLLMAdapter

router = APIRouter()
_guardrail_pipeline = GuardrailPipeline.create_default()
logger = logging.getLogger(__name__)


def _derive_title(content: str) -> str:
    """Derive a human-readable session title from the first user message."""
    text = content.strip().replace("\n", " ")
    if not text:
        return "New Chat"
    return text[:50] + ("…" if len(text) > 50 else "")


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


async def _persist_user_message(session_id: str, content: str) -> None:
    """Write the user's prompt as a ChatMessage.

    Decoupled from the chat response: any failure is logged and swallowed
    so the SSE stream is not affected by session-persistence errors.

    Q2: if this is the first message in the session and the title is still
    the default "New Chat", derive a title from the message content so the
    session list is distinguishable.
    """
    try:
        async with async_session() as db:
            # Auto-title: only when this is the first message AND title is default.
            existing = await db.execute(
                select(ChatMessage).where(ChatMessage.session_id == session_id).limit(1)
            )
            is_first = existing.first() is None
            if is_first:
                cs = await db.get(ChatSession, session_id)
                if cs and cs.title == "New Chat":
                    cs.title = _derive_title(content)
            db.add(
                ChatMessage(
                    session_id=session_id,
                    role="user",
                    content=content,
                    tokens=0,
                )
            )
            await db.commit()
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("Failed to persist user message to session %s: %s", session_id, exc)


async def _persist_assistant_message(session_id: str, content: str, tokens: int) -> None:
    """Write the assistant's accumulated reply as a ChatMessage."""
    if not content:
        return
    try:
        async with async_session() as db:
            db.add(
                ChatMessage(
                    session_id=session_id,
                    role="assistant",
                    content=content,
                    tokens=tokens,
                )
            )
            await db.commit()
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("Failed to persist assistant message to session %s: %s", session_id, exc)


async def _touch_workspace_member(workspace_id: str, user_id: str) -> None:
    """P3-1: bump WorkspaceMember.last_active_at on a successful chat request.

    Decoupled from the chat response: any failure is logged and swallowed
    so the SSE stream is not affected. Uses a separate session so it does
    not interfere with the request-scoped transaction.
    """
    try:
        async with async_session() as db:
            wm = await db.get(WorkspaceMember, (workspace_id, user_id))
            if wm is not None:
                wm.last_active_at = datetime.now(timezone.utc)
                await db.commit()
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning(
            "Failed to update last_active_at for (%s, %s): %s",
            workspace_id, user_id, exc,
        )


async def _event_stream(
    messages: list[dict],
    config: RuntimeConfig,
    context: HarnessContext,
    trace_id: str,
    user_id: str,
    session_id: str | None = None,
) -> AsyncIterator[str]:
    ws_id = config.workspace_id

    # P3-1: bump WorkspaceMember.last_active_at on a successful chat request
    # (RBAC membership check already passed in the chat handler before the
    # stream started). Fire-and-forget — failures are logged inside the helper.
    await _touch_workspace_member(ws_id, user_id)

    # P1-1: persist the latest user prompt BEFORE streaming starts so the
    # user message survives even if the LLM stream errors out mid-flight.
    if session_id:
        last_user = next(
            (m.get("content", "") for m in reversed(messages) if m.get("role") == "user"),
            "",
        )
        if last_user:
            await _persist_user_message(session_id, last_user)

    assistant_text = ""
    async with context.tracer.span("chat.handler", trace_id, attributes={"ws_id": ws_id, "user_id": user_id}):
        start = time.monotonic()
        error = ""
        total_tokens = {"input": 0, "output": 0}
        try:
            adapter = _get_adapter(config)
            async with context.tracer.span("adapter.run", trace_id, parent_span_id=context.tracer._spans[-1].span_id if context.tracer._spans else None):
                async for event in adapter.run({}, messages, {}):
                    yield f"data: {event.model_dump_json()}\n\n"
                    if event.type == "text":
                        assistant_text += event.data.get("content", "") or ""
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

    # P1-1: persist the assistant reply AFTER the stream completes. Decoupled
    # so a persistence failure never affects the chat response.
    if session_id:
        await _persist_assistant_message(
            session_id, assistant_text, total_tokens.get("output", 0)
        )


@router.post("/api/v1/chat")
async def chat(request: Request, db: AsyncSession = Depends(get_db)):
    user = getattr(request.state, "user", None)
    if not user:
        return JSONResponse(
            status_code=401,
            content={"error": {"code": "UNAUTHORIZED", "message": "Not authenticated"}},
        )

    # P2-3: API-key callers must carry the ``chat:write`` scope. JWT
    # callers (auth_method != "api_key") bypass this check.
    if user.get("auth_method") == "api_key":
        scopes = user.get("api_key_scopes") or []
        if "chat:write" not in scopes:
            return JSONResponse(
                status_code=403,
                content={
                    "error": {
                        "code": "FORBIDDEN",
                        "message": "API key missing 'chat:write' scope",
                    }
                },
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

    # P0-4: workspace_id must be provided by the client (WorkspaceContext).
    if not config.workspace_id:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": "BAD_REQUEST",
                    "message": "workspace_id is required",
                }
            },
        )

    # P0-2: enforce workspace membership for the resolved workspace.
    member_role = await get_workspace_member_role(config.workspace_id, user, db)
    if member_role is None:
        return JSONResponse(
            status_code=403,
            content={
                "error": {
                    "code": "FORBIDDEN",
                    "message": "Not a member of this workspace",
                }
            },
        )

    trace_id = uuid.uuid4().hex
    context = HarnessContext()

    guardrail_result = await _guardrail_pipeline.check(config.workspace_id)
    if not guardrail_result.passed:
        return JSONResponse(
            status_code=429,
            content={"error": {"code": "RATE_LIMITED", "message": guardrail_result.reason}},
        )

    # Bug fix: JWT puts user id in `sub`, not `id`. Fall back to `id` for
    # legacy tokens.
    user_id = user.get("sub") or user.get("id", "")

    # P1-1: optional session_id — when the client supplies one, the chat
    # handler persists the user prompt and the assistant reply as
    # ChatMessage rows. Persistence is decoupled from the SSE response.
    raw_config = body.get("config", {}) or {}
    session_id = raw_config.get("session_id") if isinstance(raw_config, dict) else None
    if session_id is not None and not isinstance(session_id, str):
        session_id = None
    if session_id == "":
        session_id = None

    # Q4: shared sessions are view-only for non-owners. Only the session
    # owner (or workspace admin/owner / tenant_admin) may append messages.
    if session_id:
        cs = await db.get(ChatSession, session_id)
        if not cs or cs.workspace_id != config.workspace_id:
            return JSONResponse(
                status_code=404,
                content={"error": {"code": "NOT_FOUND", "message": "Session not found"}},
            )
        tenant_role = user.get("role", "member")
        can_write = (
            tenant_role == "tenant_admin"
            or member_role in ("workspace_owner", "workspace_admin")
            or cs.owner_id == user_id
        )
        if not can_write:
            return JSONResponse(
                status_code=403,
                content={
                    "error": {
                        "code": "FORBIDDEN",
                        "message": "Shared sessions are view-only for non-owners",
                    }
                },
            )

    return StreamingResponse(
        _event_stream(messages, config, context, trace_id, user_id, session_id),
        media_type="text/event-stream",
    )
