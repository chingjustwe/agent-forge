import asyncio

import pytest
from src.runtime.adapters.direct_llm import DirectLLMAdapter


@pytest.fixture
def adapter():
    return DirectLLMAdapter(
        api_key="test-key",
        base_url="https://api.deepseek.com",
        model="deepseek-chat",
    )


class TestDirectLLMAdapter:
    def test_name(self, adapter):
        assert adapter.name == "direct_llm"

    def test_raises_on_empty_messages(self, adapter):
        async def run_empty():
            async for _ in adapter.run({}, [], {}):
                pass

        with pytest.raises(ValueError, match="messages"):
            asyncio.run(run_empty())

    @pytest.mark.asyncio
    async def test_streams_text_events(self, adapter, httpx_mock):
        httpx_mock.add_response(
            url="https://api.deepseek.com/v1/chat/completions",
            content=b'data: {"choices":[{"delta":{"content":"Hello"},"finish_reason":null}]}\n\ndata: {"choices":[{"delta":{}}],"usage":{"prompt_tokens":10,"completion_tokens":5,"total_tokens":15}}\n\ndata: [DONE]\n',
            headers={"Content-Type": "text/event-stream"},
        )

        results = []
        async for event in adapter.run(
            {},
            [{"role": "user", "content": "Hi"}],
            {},
        ):
            results.append(event)

        assert len(results) > 0
        assert any(e.type == "text" for e in results)
        assert any(e.type == "status" for e in results)
