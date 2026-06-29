import json
from collections.abc import AsyncIterator

import httpx

from src.infra.settings import settings
from src.runtime.adapters.base import RunAdapter
from src.runtime.models import StreamEvent


class DirectLLMAdapter(RunAdapter):
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
        session: dict,
        messages: list[dict],
        context: dict,
    ) -> AsyncIterator[StreamEvent]:
        if not messages:
            raise ValueError("messages must not be empty")

        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
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
                        yield StreamEvent(
                            type="status",
                            data={"usage": usage},
                        )
