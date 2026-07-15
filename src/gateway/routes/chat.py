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

from src.gateway.auth.permissions import has_permission
from src.gateway.auth.rbac import get_workspace_member_role
from src.infra.db.engine import async_session
from src.infra.db.models import ChatMessage, ChatSession, WorkspaceMember
from src.infra.db.session import get_db
from src.infra.settings import settings
from src.infra.telemetry.quota import QuotaGuardrail
from src.runtime.models import RuntimeConfig, StreamEvent

router = APIRouter()
_quota_guardrail = QuotaGuardrail()
logger = logging.getLogger(__name__)


def _derive_title(content: str) -> str:
    """Derive a human-readable session title from the first user message."""
    text = content.strip().replace("\n", " ")
    if not text:
        return "New Chat"
    return text[:50] + ("…" if len(text) > 50 else "")


async def _persist_user_message(session_id: str, content: str) -> None:
    """Write the user's prompt as a ChatMessage."""
    try:
        async with async_session() as db:
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


async def _persist_assistant_message(
    session_id: str, content: str, tokens: int, steps: list | None = None
) -> None:
    """Write the assistant's accumulated reply as a ChatMessage.

    ``steps`` carries the ReAct trace (reasoning + tool calls) so the
    frontend can replay the full thinking process on page reload.
    """
    if not content and not steps:
        return
    try:
        async with async_session() as db:
            db.add(
                ChatMessage(
                    session_id=session_id,
                    role="assistant",
                    content=content or "",
                    tokens=tokens,
                    steps=steps if steps else None,
                )
            )
            await db.commit()
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("Failed to persist assistant message to session %s: %s", session_id, exc)


async def _touch_workspace_member(workspace_id: str, user_id: str) -> None:
    """P3-1: bump WorkspaceMember.last_active_at on a successful chat request."""
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
    trace_id: str,
    user_id: str,
    session_id: str | None = None,
    workspace_settings: dict | None = None,
    workspace_root: str = "",
    tenant_id: str = "",
) -> AsyncIterator[str]:
    """SSE stream backed by HarnessRuntime.run().

    P1: chat.py no longer instantiates adapters directly. All agent
    execution flows through the HarnessRuntime pipeline (guardrails →
    hooks → adapter → tools → checkpoint).
    """
    from src.runtime.harness.runtime import get_runtime

    ws_id = config.workspace_id
    await _touch_workspace_member(ws_id, user_id)

    # Lazy session creation: when no session_id is provided (e.g. the user
    # clicked "New Session" and hasn't sent a message yet), create the
    # ChatSession record here, on the first message. Notify the frontend
    # via a ``session.created`` SSE event so it can update the URL.
    if not session_id:
        last_user = next(
            (m.get("content", "") for m in reversed(messages) if m.get("role") == "user"),
            "",
        )
        title = _derive_title(last_user) if last_user else "New Chat"
        try:
            async with async_session() as db:
                cs = ChatSession(
                    workspace_id=ws_id,
                    owner_id=user_id,
                    title=title,
                    visibility="private",
                )
                db.add(cs)
                await db.commit()
                await db.refresh(cs)
                session_id = cs.id
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("Failed to create session: %s", exc)
            session_id = None

        if session_id:
            yield f"data: {StreamEvent(type='session.created', data={'session_id': session_id, 'title': title}).model_dump_json()}\n\n"

    # P1-1: persist the latest user prompt BEFORE streaming starts.
    if session_id:
        last_user = next(
            (m.get("content", "") for m in reversed(messages) if m.get("role") == "user"),
            "",
        )
        if last_user:
            await _persist_user_message(session_id, last_user)

    assistant_text = ""
    start = time.monotonic()
    error = ""
    total_tokens = {"input": 0, "output": 0}
    # Collect ReAct steps (reasoning + tool calls) for persistence.
    react_steps: list[dict] = []

    runtime = get_runtime()
    try:
        async for event in runtime.run(
            session_id=session_id or "",
            messages=messages,
            config=config,
            user_id=user_id,
            trace_id=trace_id,
            workspace_settings=workspace_settings or {},
            workspace_root=workspace_root,
        ):
            yield f"data: {event.model_dump_json()}\n\n"
            if event.type == "text":
                assistant_text += event.data.get("content", "") or ""
            if event.type == "status":
                usage = event.data.get("usage", {})
                total_tokens["input"] = usage.get("input_tokens", 0)
                total_tokens["output"] = usage.get("output_tokens", 0)
                # Detect truncation: finish_reason="length" means the reply
                # was cut off by max_tokens. Flag it as a timeline step so
                # the user sees why the response is incomplete.
                if event.data.get("finish_reason") == "length":
                    react_steps.append({"kind": "truncated", "content": ""})
            # Persist ReAct steps: reasoning (incremental) and tool calls.
            if event.type == "reasoning":
                chunk = event.data.get("content", "") or ""
                last = react_steps[-1] if react_steps else None
                if last and last.get("kind") == "reasoning":
                    last["content"] += chunk
                else:
                    react_steps.append({"kind": "reasoning", "content": chunk})
            elif event.type == "tool_call":
                react_steps.append({
                    "kind": "tool",
                    "id": event.data.get("call_id", "") or event.data.get("id", ""),
                    "name": event.data.get("name", ""),
                    "args": event.data.get("args"),
                    "result": None,
                    "error": None,
                    "status": "running",
                })
            elif event.type == "tool_awaiting_approval":
                # HITL: track the approval prompt as a pending step so the
                # timeline shows why the assistant paused. When the
                # corresponding tool_result arrives (after user decision),
                # the step is updated to "done" via the call_id match.
                react_steps.append({
                    "kind": "tool_approval",
                    "id": event.data.get("call_id", ""),
                    "name": event.data.get("tool_name", ""),
                    "args": event.data.get("args"),
                    "reason": event.data.get("reason", ""),
                    "status": "awaiting_approval",
                })
            elif event.type == "tool_result":
                call_id = event.data.get("call_id", "")
                name = event.data.get("name", "")
                for s in reversed(react_steps):
                    if s.get("kind") == "tool" and s.get("status") == "running":
                        match_by_id = call_id and s.get("id") == call_id
                        match_by_name = not call_id and s.get("name") == name
                        if match_by_id or match_by_name:
                            s["result"] = event.data.get("output")
                            s["error"] = event.data.get("error")
                            s["status"] = "done"
                            break
                # HITL: also resolve the nearest pending approval step for
                # the same tool so the timeline reflects the decision.
                for s in reversed(react_steps):
                    if (
                        s.get("kind") == "tool_approval"
                        and s.get("status") == "awaiting_approval"
                        and s.get("name") == name
                    ):
                        s["status"] = "resolved"
                        s["result"] = event.data.get("output")
                        s["error"] = event.data.get("error")
                        break
    except Exception as e:
        error = str(e)
        yield f"data: {StreamEvent(type='error', data={'code': 'LLM_ERROR', 'message': error}).model_dump_json()}\n\n"

    duration_ms = int((time.monotonic() - start) * 1000)
    input_tokens = total_tokens.get("input", 0)
    output_tokens = total_tokens.get("output", 0)

    # Compute cost from model_pricing (synced from models.dev); unknown
    # models default to 0.0. Computed once and shared with record_request
    # and record_usage.
    try:
        from src.infra.telemetry.pricing import ModelPricingSync
        cost = await ModelPricingSync().get_cost(
            config.model, input_tokens, output_tokens
        )
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("Failed to compute cost for %s: %s", config.model, exc)
        cost = 0.0

    try:
        from src.infra.telemetry.collector import TelemetryCollector
        collector = TelemetryCollector()
        await collector.record_request(
            trace_id=trace_id,
            user_id=user_id,
            ws_id=ws_id,
            agent=config.agent,
            model=config.model,
            status=200 if not error else 500,
            duration_ms=duration_ms,
            tokens=total_tokens,
            error=error,
            tenant_id=tenant_id,
            cost=cost,
        )
    except Exception:
        pass

    # Record token usage + cost into quota_usage so the Quota page
    # reflects real consumption.
    try:
        await _quota_guardrail.record_usage(
            ws_id, input_tokens + output_tokens, cost
        )
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("Failed to record quota usage for %s: %s", ws_id, exc)

    # P1-1: persist the assistant reply AFTER the stream completes.
    if session_id:
        await _persist_assistant_message(
            session_id, assistant_text, total_tokens.get("output", 0),
            steps=react_steps if react_steps else None,
        )


@router.post("/api/v1/chat")
async def chat(request: Request, db: AsyncSession = Depends(get_db)):
    user = getattr(request.state, "user", None)
    if not user:
        return JSONResponse(
            status_code=401,
            content={"error": {"code": "UNAUTHORIZED", "message": "Not authenticated"}},
        )

    # API-key callers: the ``chat:write`` scope is the sole authority.
    # The creator's role is NOT inherited (virtual ``api_key`` role), so
    # ``has_permission`` would fail — the scope check replaces it.
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
    elif not has_permission(user.get("role", "viewer"), "sessions:write"):
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

    # P0-2: enforce workspace membership.
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
    user_id = user.get("sub") or user.get("id", "")

    # P1-1: optional session_id
    raw_config = body.get("config", {}) or {}
    session_id = raw_config.get("session_id") if isinstance(raw_config, dict) else None
    if session_id is not None and not isinstance(session_id, str):
        session_id = None
    if session_id == "":
        session_id = None

    # Pre-flight quota check (preserves HTTP 429 behavior). The full
    # guardrail pipeline (content_filter, PII, policy) runs inside
    # HarnessRuntime.run().
    quota_result = await _quota_guardrail.check(config.workspace_id)
    if not quota_result.passed:
        return JSONResponse(
            status_code=429,
            content={"error": {"code": "RATE_LIMITED", "message": quota_result.reason}},
        )

    # Q4: shared sessions are view-only for non-owners.
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
            or member_role in ("workspace_admin",)
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

    # Resolve workspace settings + root for the harness.
    workspace_settings = {}
    workspace_root = ""
    try:
        from src.infra.db.models import Workspace
        ws = await db.get(Workspace, config.workspace_id)
        if ws is not None:
            workspace_settings = ws.settings or {}
    except Exception:
        pass

    return StreamingResponse(
        _event_stream(
            messages, config, trace_id, user_id, session_id,
            workspace_settings, workspace_root,
            tenant_id=user.get("tenant_id", "") or "",
        ),
        media_type="text/event-stream",
    )
