from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Literal, Protocol


class ModelUnavailableError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class ModelMessage:
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tool_calls: tuple[ModelToolCall, ...] = ()
    tool_call_id: str | None = None


@dataclass(frozen=True)
class ModelTool:
    name: str
    description: str
    input_schema: Mapping[str, object]


@dataclass(frozen=True)
class ModelToolCall:
    id: str
    name: str
    arguments: dict[str, object]


@dataclass(frozen=True)
class ModelRequest:
    messages: tuple[ModelMessage, ...]
    tools: tuple[ModelTool, ...] = ()


@dataclass(frozen=True)
class ModelChunk:
    text: str = ""
    tool_calls: tuple[ModelToolCall, ...] = ()
    input_tokens: int = 0
    output_tokens: int = 0


class ModelPort(Protocol):
    def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]: ...
