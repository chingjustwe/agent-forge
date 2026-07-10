import json
import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

import httpx

from src.infra.settings import settings
from src.runtime.adapters.base import RunAdapter
from src.runtime.models import StreamEvent, Usage

if TYPE_CHECKING:
    from src.runtime.harness.context import HarnessContext

logger = logging.getLogger(__name__)

# Safety limit: maximum number of tool-calling round-trips before the
# adapter gives up and returns whatever text it has.  Prevents infinite
# loops if the LLM keeps calling tools without converging on a final
# answer.
_MAX_TOOL_ROUNDS = 10


class DirectLLMAdapter(RunAdapter):
    """Direct OpenAI-compatible chat completions adapter (DeepSeek, etc.).

    Reads per-run configuration (model, max_tokens, temperature,
    system_prompt) from ``ctx.agent`` — the agent definition resolved by
    ``HarnessRuntime``. This means a single adapter instance can serve
    multiple agents with different settings in the same process.

    Tool calling:
        Available tools are discovered from ``ctx.tool_engine`` and passed
        to the LLM via the OpenAI ``tools`` parameter.  When the LLM
        responds with ``tool_calls``, the adapter executes each tool via
        ``ctx.tool_engine.execute()``, emits ``tool_call`` /
        ``tool_result`` events (with ``already_executed=True`` so the
        runtime skips re-execution), appends the results to the message
        history, and loops back for the LLM's final answer.
    """

    name = "direct_llm"

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "https://api.deepseek.com",
        model: str = "deepseek-chat",
    ):
        self.api_key = api_key or settings.llm_api_key
        self.base_url = base_url.rstrip("/")
        self.model = model

    # ── Public API ──

    async def run(
        self,
        messages: list[dict],
        ctx: "HarnessContext",
    ) -> AsyncIterator[StreamEvent]:
        if not messages:
            raise ValueError("messages must not be empty")

        agent = ctx.agent
        max_tokens = (agent.max_tokens if agent and agent.max_tokens else 4096)
        temperature = agent.temperature if agent is not None else 0.7
        model = (agent.model if agent and agent.model else self.model) or "deepseek-chat"

        # Prepend system_prompt if set and not already present.
        final_messages: list[dict] = list(messages)
        if agent and agent.system_prompt:
            if not final_messages or final_messages[0].get("role") != "system":
                final_messages = [
                    {"role": "system", "content": agent.system_prompt},
                    *final_messages,
                ]

        # Discover available tools from the per-run ToolEngine.
        tool_schemas = await self._get_tool_schemas(ctx)
        tools_payload = self._build_tools_payload(tool_schemas) if tool_schemas else None

        # ── Tool-calling loop ──
        for _round in range(_MAX_TOOL_ROUNDS):
            # ``_stream_completion`` is an async generator that yields
            # text/status StreamEvents in real time and stores the
            # accumulated content + tool_calls in ``round_result`` when
            # the HTTP stream ends.
            round_result: dict = {}
            async for event in self._stream_completion(
                model, final_messages, max_tokens, temperature,
                tools_payload, round_result,
            ):
                yield event

            content_text: str = round_result.get("content", "")
            tool_calls: list[dict] = round_result.get("tool_calls", [])

            if not tool_calls:
                # No tool calls — the LLM produced a final text answer.
                # Text was already streamed via yield inside
                # _stream_completion; nothing more to do.
                return

            # Append the assistant message (with tool_calls) to the
            # conversation so the next LLM call sees the full context.
            assistant_msg: dict = {"role": "assistant"}
            if content_text:
                assistant_msg["content"] = content_text
            assistant_msg["tool_calls"] = [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": tc["arguments"],
                    },
                }
                for tc in tool_calls
            ]
            final_messages.append(assistant_msg)

            # Execute each tool and emit events.
            for tc in tool_calls:
                tool_name = tc["name"]
                tool_args = self._parse_args(tc["arguments"], tool_name)

                yield StreamEvent(
                    type="tool_call",
                    data={"name": tool_name, "args": tool_args, "id": tc["id"]},
                    already_executed=True,
                )

                tool_output, tool_error = await self._execute_tool(
                    tool_name, tool_args, ctx
                )

                yield StreamEvent(
                    type="tool_result",
                    data={
                        "name": tool_name,
                        "output": tool_output,
                        "error": tool_error,
                        "metadata": {},
                    },
                    already_executed=True,
                )

                # Append tool result to conversation for the next LLM call.
                final_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": tool_output or tool_error or "",
                    }
                )

            # Loop back: the LLM will now see the tool results and
            # hopefully produce a final text answer (or more tool calls).

        # Exhausted max rounds — emit a warning and stop.
        logger.warning(
            "DirectLLMAdapter: reached max tool rounds (%d) without a "
            "final answer; stopping",
            _MAX_TOOL_ROUNDS,
        )

    # ── Internals ──

    async def _stream_completion(
        self,
        model: str,
        messages: list[dict],
        max_tokens: int,
        temperature: float,
        tools: list[dict] | None,
        result: dict,
    ) -> AsyncIterator[StreamEvent]:
        """Stream one LLM completion request.

        Yields ``text`` / ``status`` StreamEvents as they arrive.  When
        the HTTP stream ends, stores ``{"content": str, "tool_calls":
        list[dict]}`` in *result* so the caller can inspect the outcome.
        """
        accumulated_content: str = ""
        # tool_calls are accumulated by index because the streaming API
        # sends them in fragments across multiple SSE chunks.
        tool_calls_acc: dict[int, dict] = {}

        request_body: dict = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }
        if tools:
            request_body["tools"] = tools

        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "content-type": "application/json",
                },
                json=request_body,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line.startswith("data: "):
                        continue
                    payload = line.removeprefix("data: ").strip()
                    if payload == "[DONE]":
                        break
                    try:
                        data = json.loads(payload)
                    except json.JSONDecodeError:
                        continue

                    choices = data.get("choices", [])
                    if not choices:
                        continue
                    delta = choices[0].get("delta", {})

                    # Stream text content immediately.
                    content = delta.get("content")
                    if content:
                        accumulated_content += content
                        yield StreamEvent(
                            type="text",
                            data={"content": content},
                        )

                    # Accumulate tool_calls fragments by index.
                    for tc_delta in delta.get("tool_calls", []) or []:
                        idx = tc_delta.get("index", 0)
                        slot = tool_calls_acc.setdefault(
                            idx,
                            {"id": "", "name": "", "arguments": ""},
                        )
                        if tc_delta.get("id"):
                            slot["id"] = tc_delta["id"]
                        fn = tc_delta.get("function", {})
                        if fn.get("name"):
                            slot["name"] = fn["name"]
                        if fn.get("arguments"):
                            slot["arguments"] += fn["arguments"]

                    # Usage info (usually on the last chunk).
                    usage = data.get("usage")
                    if usage:
                        yield StreamEvent(
                            type="status",
                            data={
                                "usage": Usage(
                                    input_tokens=usage.get("prompt_tokens", 0),
                                    output_tokens=usage.get("completion_tokens", 0),
                                    total_tokens=usage.get("total_tokens", 0),
                                ).model_dump()
                            },
                        )

        # Store accumulated results for the caller.
        result["content"] = accumulated_content
        result["tool_calls"] = [tool_calls_acc[i] for i in sorted(tool_calls_acc)]

    async def _get_tool_schemas(self, ctx: "HarnessContext") -> list[dict]:
        """Fetch available tool schemas from the per-run ToolEngine."""
        if ctx.tool_engine is None:
            return []
        try:
            return await ctx.tool_engine.schemas(ctx.workspace_id)
        except Exception:
            logger.warning("Failed to get tool schemas", exc_info=True)
            return []

    @staticmethod
    def _build_tools_payload(schemas: list[dict]) -> list[dict]:
        """Convert internal tool schemas to OpenAI ``tools`` format."""
        return [
            {
                "type": "function",
                "function": {
                    "name": s["name"],
                    "description": s.get("description", ""),
                    "parameters": s.get("input_schema", {}),
                },
            }
            for s in schemas
        ]

    @staticmethod
    def _parse_args(raw: str, tool_name: str) -> dict:
        """Parse JSON tool-call arguments, tolerating empty strings."""
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            logger.warning(
                "Failed to parse tool arguments for %s: %s", tool_name, raw
            )
            return {}

    async def _execute_tool(
        self, name: str, args: dict, ctx: "HarnessContext"
    ) -> tuple[str, str | None]:
        """Execute a tool via the per-run ToolEngine.

        Returns ``(output, error)``.  On failure ``output`` is empty and
        ``error`` carries the message.
        """
        from src.runtime.harness.tool_engine import ToolError

        try:
            result = await ctx.tool_engine.execute(name, args, ctx)
            return result.output, result.error
        except ToolError as exc:
            return "", f"{type(exc).__name__}: {exc}"
        except Exception as exc:
            logger.exception("Unexpected error executing tool %s", name)
            return "", f"{type(exc).__name__}: {exc}"
