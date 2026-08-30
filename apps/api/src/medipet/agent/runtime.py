from __future__ import annotations

import json
from collections.abc import AsyncGenerator, Mapping
from contextlib import aclosing
from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Any, Literal, Protocol, TypedDict, cast

from langgraph.config import get_stream_writer
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph

from medipet.agent.capabilities import (
    CapabilityProvider,
    CapabilitySnapshot,
    StaticCapabilityProvider,
    ToolContext,
    ToolDefinition,
)
from medipet.agent.prompts import OUTPATIENT_ASSISTANT_SYSTEM_PROMPT
from medipet.model.port import (
    ModelMessage,
    ModelPort,
    ModelRequest,
    ModelTool,
    ModelToolCall,
    ModelUnavailableError,
)

TOOL_REJECTION = {
    "code": "tool_call_rejected",
    "message": "该操作不可用，请改为安全回答。",
}
LOOP_FAILURE = "模型重复请求了不可用操作，本次协助已停止。"
BUDGET_FAILURE = "本次协助已达到步骤上限，请重新发起请求。"
TOOL_FAILURE = "查询暂时无法完成，请稍后重试。"


@dataclass(frozen=True)
class AgentRequest:
    messages: tuple[ModelMessage, ...]
    context: ToolContext = ToolContext()


@dataclass(frozen=True)
class AgentEvent:
    kind: Literal["status", "text", "data", "failed"]
    data: dict[str, Any]


class AgentRuntime(Protocol):
    def run(self, request: AgentRequest) -> AsyncGenerator[AgentEvent, None]: ...


class ReActState(TypedDict):
    messages: tuple[ModelMessage, ...]
    context: ToolContext
    capabilities: CapabilitySnapshot
    available_tool_names: tuple[str, ...]
    pending_calls: tuple[ModelToolCall, ...]
    correction_used: bool
    invalid_signatures: tuple[str, ...]
    model_calls: int
    terminal: bool


class LangGraphAgentRuntime:
    def __init__(
        self,
        model: ModelPort,
        *,
        capability_provider: CapabilityProvider | None = None,
        max_steps: int = 8,
        profile_version: str = "static",
    ) -> None:
        self._graph = _build_react_graph(model, max_steps=max_steps)
        self._capability_provider = capability_provider or StaticCapabilityProvider()
        self._max_steps = max_steps
        self._profile_version = profile_version

    async def run(self, request: AgentRequest) -> AsyncGenerator[AgentEvent, None]:
        context = replace(request.context, profile_version=self._profile_version)
        source_capabilities = await self._capability_provider.snapshot(context)
        capabilities = CapabilitySnapshot(
            skill_versions=tuple(source_capabilities.skill_versions),
            tools=tuple(
                replace(tool, input_schema=deepcopy(tool.input_schema))
                for tool in source_capabilities.tools
            ),
        )
        available_tool_names = tuple(
            tool.name
            for tool in capabilities.tools
            if tool.enabled and tool.bound and tool.effect == "read" and tool.authorize(context)
        )
        yield AgentEvent("status", {"label": "正在连接门诊协助模型"})
        try:
            async with aclosing(
                cast(
                    AsyncGenerator[dict[str, Any], None],
                    self._graph.astream(
                        {
                            "messages": request.messages,
                            "context": context,
                            "capabilities": capabilities,
                            "available_tool_names": available_tool_names,
                            "pending_calls": (),
                            "correction_used": False,
                            "invalid_signatures": (),
                            "model_calls": 0,
                            "terminal": False,
                        },
                        config={"recursion_limit": self._max_steps * 2 + 4},
                        stream_mode="custom",
                    ),
                )
            ) as graph_events:
                async for event in graph_events:
                    yield AgentEvent(kind=event["kind"], data=event["data"])
        except ModelUnavailableError:
            yield AgentEvent("failed", {"message": "模型服务暂时不可用，请稍后重试。"})
        except GraphRecursionError:
            yield AgentEvent("failed", {"message": BUDGET_FAILURE})


def _build_react_graph(model: ModelPort, *, max_steps: int):
    async def call_model(state: ReActState) -> dict[str, object]:
        writer = get_stream_writer()
        if state["model_calls"] >= max_steps:
            writer({"kind": "failed", "data": {"message": BUDGET_FAILURE}})
            return {"terminal": True, "pending_calls": ()}

        visible_tools = tuple(
            ModelTool(
                name=tool.name,
                description=tool.description,
                input_schema=deepcopy(tool.input_schema),
            )
            for tool in state["capabilities"].tools
            if tool.name in state["available_tool_names"]
        )
        model_request = ModelRequest(
            messages=(
                ModelMessage(role="system", content=OUTPATIENT_ASSISTANT_SYSTEM_PROMPT),
                *state["messages"],
            ),
            tools=visible_tools,
        )
        response_text: list[str] = []
        calls: list[ModelToolCall] = []
        async for chunk in model.stream(model_request):
            if chunk.text:
                response_text.append(chunk.text)
                writer({"kind": "text", "data": {"text": chunk.text}})
            calls.extend(chunk.tool_calls)
        assistant_message = ModelMessage(
            role="assistant",
            content="".join(response_text),
            tool_calls=tuple(calls),
        )
        return {
            "messages": (*state["messages"], assistant_message),
            "pending_calls": tuple(calls),
            "model_calls": state["model_calls"] + 1,
        }

    async def execute_tools(state: ReActState) -> dict[str, object]:
        writer = get_stream_writer()
        messages = list(state["messages"])
        correction_used = state["correction_used"]
        invalid_signatures = list(state["invalid_signatures"])
        tools_by_name = {tool.name: tool for tool in state["capabilities"].tools}

        for call in state["pending_calls"]:
            tool = tools_by_name.get(call.name)
            rejection = _rejection_reason(
                tool,
                call.arguments,
                state["available_tool_names"],
            )
            if rejection is not None:
                signature = _call_signature(call)
                if correction_used or signature in invalid_signatures:
                    writer({"kind": "failed", "data": {"message": LOOP_FAILURE}})
                    return {"terminal": True, "pending_calls": ()}
                correction_used = True
                invalid_signatures.append(signature)
                messages.append(
                    ModelMessage(
                        role="tool",
                        content=json.dumps(TOOL_REJECTION, ensure_ascii=False),
                        tool_call_id=call.id,
                    )
                )
                continue

            assert tool is not None
            writer({"kind": "status", "data": {"label": "正在查询可用信息"}})
            try:
                observation = await tool.execute(call.arguments, state["context"])
                observation_json = json.dumps(
                    observation,
                    ensure_ascii=False,
                    sort_keys=True,
                )
            except Exception:
                writer({"kind": "failed", "data": {"message": TOOL_FAILURE}})
                return {"terminal": True, "pending_calls": ()}
            messages.append(
                ModelMessage(
                    role="tool",
                    content=observation_json,
                    tool_call_id=call.id,
                )
            )

        return {
            "messages": tuple(messages),
            "pending_calls": (),
            "correction_used": correction_used,
            "invalid_signatures": tuple(invalid_signatures),
        }

    def route_after_model(state: ReActState) -> str:
        if state["terminal"] or not state["pending_calls"]:
            return END
        return "execute_tools"

    def route_after_tools(state: ReActState) -> str:
        return END if state["terminal"] else "call_model"

    graph = StateGraph(ReActState)
    graph.add_node("call_model", call_model)
    graph.add_node("execute_tools", execute_tools)
    graph.add_edge(START, "call_model")
    graph.add_conditional_edges("call_model", route_after_model)
    graph.add_conditional_edges("execute_tools", route_after_tools)
    return graph.compile()


def _rejection_reason(
    tool: ToolDefinition | None,
    arguments: dict[str, object],
    available_tool_names: tuple[str, ...],
) -> str | None:
    if tool is None:
        return "unknown"
    if tool.name not in available_tool_names:
        return "unavailable"
    return _validate_arguments(arguments, tool.input_schema)


def _validate_arguments(arguments: dict[str, object], schema: Mapping[str, object]) -> str | None:
    if schema.get("type") != "object":
        return "schema"
    required = schema.get("required", ())
    if not isinstance(required, (list, tuple)) or any(name not in arguments for name in required):
        return "required"
    properties = schema.get("properties", {})
    if not isinstance(properties, Mapping):
        return "schema"
    if schema.get("additionalProperties") is False and any(
        name not in properties for name in arguments
    ):
        return "additional"
    type_checks = {
        "string": lambda value: isinstance(value, str),
        "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
        "number": lambda value: isinstance(value, int | float) and not isinstance(value, bool),
        "boolean": lambda value: isinstance(value, bool),
        "object": lambda value: isinstance(value, dict),
        "array": lambda value: isinstance(value, list),
    }
    for name, value in arguments.items():
        property_schema = properties.get(name)
        if not isinstance(property_schema, Mapping):
            continue
        expected = property_schema.get("type")
        if not isinstance(expected, str):
            continue
        check = type_checks.get(expected)
        if check is not None and not check(value):
            return "type"
    return None


def _call_signature(call: ModelToolCall) -> str:
    return f"{call.name}:{json.dumps(call.arguments, sort_keys=True, ensure_ascii=True)}"
