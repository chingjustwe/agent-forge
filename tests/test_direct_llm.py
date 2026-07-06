import asyncio

import pytest
from src.runtime.adapters.direct_llm import DirectLLMAdapter
from src.runtime.harness.agents import AgentDefinition
from src.runtime.harness.context import HarnessContext


def _make_ctx(
    *,
    max_tokens: int = 4096,
    model: str = "deepseek-chat",
    system_prompt: str = "",
    temperature: float = 0.7,
) -> HarnessContext:
    """Build a minimal HarnessContext for DirectLLM adapter tests."""
    agent = AgentDefinition(
        id="",
        name="default",
        workspace_id="ws-test",
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        system_prompt=system_prompt,
        adapter="direct_llm",
    )
    return HarnessContext(
        workspace_id="ws-test",
        user_id="u-test",
        session_id="s-test",
        trace_id="t-test",
        agent=agent,
    )


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
            async for _ in adapter.run([], _make_ctx()):
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
            [{"role": "user", "content": "Hi"}],
            _make_ctx(),
        ):
            results.append(event)

        assert len(results) > 0
        assert any(e.type == "text" for e in results)
        assert any(e.type == "status" for e in results)

    @pytest.mark.asyncio
    async def test_prepends_system_prompt(self, adapter, httpx_mock):
        """When agent.system_prompt is set, adapter prepends it to messages."""
        import json as _json

        httpx_mock.add_response(
            url="https://api.deepseek.com/v1/chat/completions",
            content=b'data: [DONE]\n',
            headers={"Content-Type": "text/event-stream"},
        )

        async for _ in adapter.run(
            [{"role": "user", "content": "Hi"}],
            _make_ctx(system_prompt="You are a helpful assistant."),
        ):
            pass

        requests = httpx_mock.get_requests()
        assert len(requests) == 1
        body = _json.loads(requests[0].content)
        msgs = body["messages"]
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"] == "You are a helpful assistant."
        assert msgs[1]["role"] == "user"

    @pytest.mark.asyncio
    async def test_uses_agent_model_and_max_tokens(self, adapter, httpx_mock):
        """Adapter reads model + max_tokens from ctx.agent, not adapter default."""
        import json as _json

        httpx_mock.add_response(
            url="https://api.deepseek.com/v1/chat/completions",
            content=b'data: [DONE]\n',
            headers={"Content-Type": "text/event-stream"},
        )

        async for _ in adapter.run(
            [{"role": "user", "content": "Hi"}],
            _make_ctx(model="deepseek-reasoner", max_tokens=2048),
        ):
            pass

        requests = httpx_mock.get_requests()
        assert len(requests) == 1
        body = _json.loads(requests[0].content)
        assert body["model"] == "deepseek-reasoner"
        assert body["max_tokens"] == 2048
