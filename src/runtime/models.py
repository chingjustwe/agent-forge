from typing import Literal
from pydantic import BaseModel, Field


class RuntimeConfig(BaseModel):
    agent: str = ""
    model: str = "deepseek-chat"
    max_tokens: int = 4096
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    workspace_id: str = ""
    extra: dict = {}


class Usage(BaseModel):
    """Standardized token usage across all LLM providers.

    Adapters are responsible for normalizing provider-specific usage
    formats (OpenAI `prompt_tokens`/`completion_tokens`, Anthropic
    `input_tokens`/`output_tokens`, etc.) into this canonical form.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class StreamEvent(BaseModel):
    type: Literal["text", "tool_call", "tool_result", "error", "status"]
    data: dict
    metadata: dict = {}
