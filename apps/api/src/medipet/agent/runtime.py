from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import aclosing
from dataclasses import dataclass
from typing import Any, Literal, Protocol, TypedDict, cast

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from medipet.agent.prompts import OUTPATIENT_ASSISTANT_SYSTEM_PROMPT
from medipet.model.port import ModelMessage, ModelPort, ModelRequest, ModelUnavailableError


@dataclass(frozen=True)
class AgentRequest:
    messages: tuple[ModelMessage, ...]


@dataclass(frozen=True)
class AgentEvent:
    kind: Literal["status", "text", "data", "failed"]
    data: dict[str, Any]


class AgentRuntime(Protocol):
    def run(self, request: AgentRequest) -> AsyncGenerator[AgentEvent, None]: ...


class ModelAgentState(TypedDict):
    messages: tuple[ModelMessage, ...]
    completed: bool


class LangGraphAgentRuntime:
    def __init__(self, model: ModelPort, *, max_steps: int = 8) -> None:
        self._graph = _build_model_graph(model)
        self._max_steps = max_steps

    async def run(self, request: AgentRequest) -> AsyncGenerator[AgentEvent, None]:
        yield AgentEvent("status", {"label": "正在连接门诊协助模型"})
        try:
            async with aclosing(
                cast(
                    AsyncGenerator[dict[str, Any], None],
                    self._graph.astream(
                        {"messages": request.messages, "completed": False},
                        config={"recursion_limit": self._max_steps + 1},
                        stream_mode="custom",
                    ),
                )
            ) as graph_events:
                async for event in graph_events:
                    yield AgentEvent(kind=event["kind"], data=event["data"])
        except ModelUnavailableError:
            yield AgentEvent("failed", {"message": "模型服务暂时不可用，请稍后重试。"})


def _build_model_graph(model: ModelPort):
    async def call_model(state: ModelAgentState) -> dict[str, bool]:
        writer = get_stream_writer()
        model_request = ModelRequest(
            messages=(
                ModelMessage(role="system", content=OUTPATIENT_ASSISTANT_SYSTEM_PROMPT),
                *state["messages"],
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
