import pytest
from pydantic import ValidationError
from src.runtime.models import RuntimeConfig, StreamEvent


class TestRuntimeConfig:
    def test_minimal_config(self):
        config = RuntimeConfig()
        assert config.agent == ""
        assert config.max_tokens == 4096
        assert config.temperature == 0.7

    def test_invalid_temperature(self):
        with pytest.raises(ValidationError):
            RuntimeConfig(temperature=3.0)


class TestStreamEvent:
    def test_text_event(self):
        event = StreamEvent(type="text", data={"content": "hello"})
        assert event.type == "text"
        assert event.data["content"] == "hello"

    def test_invalid_type(self):
        with pytest.raises(ValidationError):
            StreamEvent(type="unknown", data={})
