from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Literal, Protocol, TypedDict

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

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


class ModelAgentState(TypedDict):
    message: str
    completed: bool


class LangGraphAgentRuntime:
    def __init__(self, model: ModelPort) -> None:
        self._graph = _build_model_graph(model)

    async def run(self, request: AgentRequest) -> AsyncIterator[AgentEvent]:
        yield AgentEvent("status", {"label": "正在连接门诊协助模型"})
        async for event in self._graph.astream(
            {"message": request.message, "completed": False},
            stream_mode="custom",
        ):
            yield AgentEvent(kind=event["kind"], data=event["data"])


def _build_model_graph(model: ModelPort):
    async def call_model(state: ModelAgentState) -> dict[str, bool]:
        writer = get_stream_writer()
        model_request = ModelRequest(
            messages=(
                ModelMessage(role="system", content=OUTPATIENT_ASSISTANT_SYSTEM_PROMPT),
                ModelMessage(role="user", content=state["message"]),
            )
        )
        async for chunk in model.stream(model_request):
            writer({"kind": "text", "data": {"text": chunk.text}})
        return {"completed": True}

    graph = StateGraph(ModelAgentState)
    graph.add_node("call_model", call_model)
    graph.add_edge(START, "call_model")
    graph.add_edge("call_model", END)
    return graph.compile()
