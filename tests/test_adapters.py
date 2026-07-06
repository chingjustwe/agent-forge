import pytest
from src.runtime.adapters.base import RunAdapter


class TestRunAdapter:
    def test_abc_cannot_instantiate(self):
        with pytest.raises(TypeError):
            RunAdapter()

    def test_name_attribute(self):
        class DummyAdapter(RunAdapter):
            name = "dummy"

            async def run(self, messages, ctx):
                if False:
                    yield

        adapter = DummyAdapter()
        assert adapter.name == "dummy"
        assert hasattr(adapter, "run")
