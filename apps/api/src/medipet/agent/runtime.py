from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from medipet.agent.graph import build_demo_graph


@dataclass(frozen=True)
class AgentRequest:
    message: str


@dataclass(frozen=True)
class AgentEvent:
    kind: Literal["status", "text", "data"]
    data: dict[str, Any]


class AgentRuntime(Protocol):
    def run(self, request: AgentRequest) -> AsyncIterator[AgentEvent]: ...


class LangGraphAgentRuntime:
    def __init__(self) -> None:
        self._graph = build_demo_graph()

    async def run(self, request: AgentRequest) -> AsyncIterator[AgentEvent]:
        yield AgentEvent("status", {"label": "正在整理本次就诊信息"})
        result = await self._graph.ainvoke(
            {"message": request.message, "reply": "", "data_parts": []}
        )
        yield AgentEvent("text", {"text": result["reply"]})
        for part in result["data_parts"]:
            yield AgentEvent("data", part)
