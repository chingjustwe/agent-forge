"""Diagnostic: verify `max_tokens` reaches the wire for the `openai:` provider.

Reproduces exactly what ``DeepAgentsAdapter.run`` does when building the
model, then performs a real (mocked-HTTP) invocation and inspects the
request body. If ``max_tokens`` is missing or renamed (e.g. to
``max_completion_tokens``) on the wire, this test fails and pinpoints the
root cause of "Agent Max Tokens not applied".
"""
from __future__ import annotations

import json

import httpx
import pytest


@pytest.mark.asyncio
async def test_init_chat_model_sends_max_tokens(httpx_mock):
    httpx_mock.add_response(
        url=__import__("re").compile(r"https://api\.deepseek\.com/.*chat/completions.*"),
        json={
            "id": "1",
            "object": "chat.completion",
            "model": "deepseek-v4-flash",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "hi"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "total_tokens": 2,
            },
        },
    )

    from langchain.chat_models import init_chat_model

    model = init_chat_model(
        "openai:deepseek-v4-flash",
        base_url="https://api.deepseek.com",
        api_key="test-key",
        temperature=0.7,
        extra_body={"max_tokens": 10},
    )

    await model.ainvoke("hello")

    request = httpx_mock.get_request()
    body = json.loads(request.content)
    print("REQUEST BODY:", body)
    assert body.get("max_tokens") == 10, (
        f"max_tokens not sent correctly; body keys: {list(body.keys())}"
    )
    assert "max_completion_tokens" not in body, (
        f"max_completion_tokens should not be sent to DeepSeek; body: {body}"
    )
