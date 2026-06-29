from typing import Literal
from pydantic import BaseModel, Field


class RuntimeConfig(BaseModel):
    agent: str = ""
    model: str = "deepseek-chat"
    max_tokens: int = 4096
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    workspace_id: str = ""
    extra: dict = {}


class StreamEvent(BaseModel):
    type: Literal["text", "tool_call", "tool_result", "error", "status"]
    data: dict
    metadata: dict = {}
