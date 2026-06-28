import pytest
from src.runtime.abc import AgentRuntime
from src.runtime.models import RuntimeConfig, StreamEvent


class TestAgentRuntime:
    def test_abc_cannot_instantiate(self):
        with pytest.raises(TypeError):
            AgentRuntime()

    def test_concrete_runtime(self):
        class DummyRuntime(AgentRuntime):
            async def run(self, session_id, messages, config):
                yield StreamEvent(type="status", data={"state": "done"})

        runtime = DummyRuntime()
        import inspect
        assert inspect.isasyncgenfunction(runtime.run)
