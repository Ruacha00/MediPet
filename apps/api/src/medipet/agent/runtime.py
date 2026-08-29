from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from medipet.agent.prompts import OUTPATIENT_ASSISTANT_SYSTEM_PROMPT
from medipet.model.port import ModelMessage, ModelPort, ModelRequest


@dataclass(frozen=True)
class AgentRequest:
    message: str


@dataclass(frozen=True)
class AgentEvent:
    kind: Literal["status", "text", "data"]
    data: dict[str, Any]


class AgentRuntime(Protocol):
    def run(self, request: AgentRequest) -> AsyncIterator[AgentEvent]: ...


class ModelAgentRuntime:
    def __init__(self, model: ModelPort) -> None:
        self._model = model

    async def run(self, request: AgentRequest) -> AsyncIterator[AgentEvent]:
        yield AgentEvent("status", {"label": "正在连接门诊协助模型"})
        model_request = ModelRequest(
            messages=(
                ModelMessage(role="system", content=OUTPATIENT_ASSISTANT_SYSTEM_PROMPT),
                ModelMessage(role="user", content=request.message),
            )
        )
        async for chunk in self._model.stream(model_request):
            yield AgentEvent("text", {"text": chunk.text})
