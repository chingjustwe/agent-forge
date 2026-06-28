import json
from collections.abc import AsyncIterator

import httpx

from src.runtime.adapters.base import RunAdapter
from src.runtime.models import StreamEvent


class DirectLLMAdapter(RunAdapter):
    name = "direct_llm"

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "https://api.anthropic.com/v1",
        model: str = "claude-sonnet-4-20250514",
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model

    async def run(
        self,
        session: dict,
        messages: list[dict],
        context: dict,
    ) -> AsyncIterator[StreamEvent]:
        if not messages:
            raise ValueError("messages must not be empty")

        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/messages",
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": messages,
                    "max_tokens": context.get("max_tokens", 4096),
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

                    event_type = data.get("type", "")
                    if event_type == "content_block_delta":
                        delta = data.get("delta", {})
                        if "text" in delta:
                            yield StreamEvent(
                                type="text",
                                data={"content": delta["text"]},
                            )
                    elif event_type == "message_delta":
                        usage = data.get("usage", {})
                        if usage:
                            yield StreamEvent(
                                type="status",
                                data={"usage": usage},
                            )
