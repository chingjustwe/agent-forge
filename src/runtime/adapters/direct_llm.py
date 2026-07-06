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


class DirectLLMAdapter(RunAdapter):
    """Direct OpenAI-compatible chat completions adapter (DeepSeek, etc.).

    Reads per-run configuration (model, max_tokens, temperature,
    system_prompt) from ``ctx.agent`` — the agent definition resolved by
    ``HarnessRuntime``. This means a single adapter instance can serve
    multiple agents with different settings in the same process.
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

    async def run(
        self,
        messages: list[dict],
        ctx: "HarnessContext",
    ) -> AsyncIterator[StreamEvent]:
        if not messages:
            raise ValueError("messages must not be empty")

        # Pull per-run settings from the resolved agent definition.
        agent = ctx.agent
        max_tokens = (agent.max_tokens if agent and agent.max_tokens else 4096)
        temperature = agent.temperature if agent is not None else 0.7
        # Agent model wins over adapter default; allows per-agent model.
        model = (agent.model if agent and agent.model else self.model) or "deepseek-chat"

        # Prepend system_prompt if set and not already present in messages.
        final_messages = messages
        if agent and agent.system_prompt:
            if not messages or messages[0].get("role") != "system":
                final_messages = [
                    {"role": "system", "content": agent.system_prompt},
                    *messages,
                ]

        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "content-type": "application/json",
                },
                json={
                    "model": model,
                    "messages": final_messages,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "stream": True,
                },
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
                    if choices:
                        delta = choices[0].get("delta", {})
                        content = delta.get("content")
                        if content:
                            yield StreamEvent(
                                type="text",
                                data={"content": content},
                            )

                    usage = data.get("usage")
                    if usage:
                        normalized = Usage(
                            input_tokens=usage.get("prompt_tokens", 0),
                            output_tokens=usage.get("completion_tokens", 0),
                            total_tokens=usage.get("total_tokens", 0),
                        )
                        yield StreamEvent(
                            type="status",
                            data={"usage": normalized.model_dump()},
                        )
