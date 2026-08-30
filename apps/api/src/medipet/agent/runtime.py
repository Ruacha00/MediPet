from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from contextlib import aclosing
from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Any, Literal, Protocol, TypedDict, cast

from langgraph.config import get_stream_writer
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph

from medipet.actions import (
    ActionDecisionError,
    ActionStore,
    UnavailableActionStore,
    proposal_expiry,
)
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
from medipet.schema import validate_object

TOOL_REJECTION = {
    "code": "tool_call_rejected",
    "message": "该操作不可用，请改为安全回答。",
}
LOOP_FAILURE = "模型重复请求了不可用操作，本次协助已停止。"
BUDGET_FAILURE = "本次协助已达到步骤上限，请重新发起请求。"
TOOL_FAILURE = "查询暂时无法完成，请稍后重试。"
CAPABILITY_FAILURE = "运行时能力暂时不可用，请稍后重试。"
PROPOSAL_FAILURE = "待确认操作暂时无法创建，请重新发起。"


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

    async def decide(
        self,
        proposal_id: str,
        decision: Literal["confirm", "reject"],
        context: ToolContext,
    ) -> AgentEvent: ...


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
        action_store: ActionStore | None = None,
    ) -> None:
        self._action_store = action_store or UnavailableActionStore()
        self._graph = _build_react_graph(
            model,
            max_steps=max_steps,
            action_store=self._action_store,
        )
        self._capability_provider = capability_provider or StaticCapabilityProvider()
        self._max_steps = max_steps
        self._profile_version = profile_version

    async def run(self, request: AgentRequest) -> AsyncGenerator[AgentEvent, None]:
        context = replace(request.context, profile_version=self._profile_version)
        try:
            source_capabilities = await self._capability_provider.snapshot(context)
            pinned_skills = tuple(source_capabilities.skills)
            platform_tools: tuple[ToolDefinition, ...] = ()
            if pinned_skills:
                skills_by_slug = {skill.slug: skill for skill in pinned_skills}
                skill_catalog = "\n".join(
                    f"- {skill.slug}: {skill.name} — {skill.description}" for skill in pinned_skills
                )

                async def load_skill(
                    arguments: dict[str, object],
                    _: ToolContext,
                ) -> dict[str, object]:
                    slug = cast(str, arguments["slug"])
                    skill = skills_by_slug[slug]
                    return {
                        "skill": skill.slug,
                        "version": skill.version,
                        "instructions": await skill.load_instructions(),
                    }

                platform_tools = (
                    ToolDefinition(
                        name="load_skill",
                        version="platform-1",
                        description=(
                            f"根据名称和描述选择已发布 Skill，再按需加载完整指令：\n{skill_catalog}"
                        ),
                        input_schema={
                            "type": "object",
                            "properties": {
                                "slug": {
                                    "type": "string",
                                    "enum": [skill.slug for skill in pinned_skills],
                                }
                            },
                            "required": ["slug"],
                            "additionalProperties": False,
                        },
                        effect="read",
                        execute=load_skill,
                    ),
                )
            capabilities = CapabilitySnapshot(
                skill_versions=tuple(source_capabilities.skill_versions),
                skills=pinned_skills,
                tools=tuple(
                    replace(
                        tool,
                        input_schema=deepcopy(tool.input_schema),
                        output_schema=deepcopy(tool.output_schema),
                    )
                    for tool in (*source_capabilities.tools, *platform_tools)
                ),
                record_unknown_tool_rejection=(
                    source_capabilities.record_unknown_tool_rejection
                ),
            )
            available_tool_names = tuple(
                tool.name
                for tool in capabilities.tools
                if tool.enabled
                and tool.bound
                and (tool.effect == "read" or tool.approval_required)
                and tool.authorize(context)
            )
        except Exception:
            yield AgentEvent("failed", {"message": CAPABILITY_FAILURE})
            return
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

    async def decide(
        self,
        proposal_id: str,
        decision: Literal["confirm", "reject"],
        context: ToolContext,
    ) -> AgentEvent:
        context = replace(context, profile_version=self._profile_version)
        try:
            if decision == "reject":
                proposal = await self._action_store.reject(proposal_id, context)
            else:
                persisted = await self._action_store.get_proposal(proposal_id)
                capabilities = await self._capability_provider.snapshot(context)
                tool = next(
                    (
                        candidate
                        for candidate in capabilities.tools
                        if candidate.tool_id == persisted.tool_id
                        and candidate.version == persisted.tool_version
                    ),
                    None,
                )
                if tool is None:
                    raise ActionDecisionError(
                        "操作参数、Tool 版本或作用域已变化，请重新发起"
                    )
                proposal, _ = await self._action_store.confirm(
                    proposal_id,
                    context,
                    tool,
                )
            return AgentEvent("data", proposal.event_data())
        except ActionDecisionError as error:
            await self._action_store.record_decision_rejection(proposal_id, context)
            return AgentEvent("failed", {"message": str(error)})
        except Exception:
            await self._action_store.record_decision_rejection(proposal_id, context)
            return AgentEvent("failed", {"message": CAPABILITY_FAILURE})


def _build_react_graph(model: ModelPort, *, max_steps: int, action_store: ActionStore):
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
                if not visible_tools:
                    writer({"kind": "text", "data": {"text": chunk.text}})
            calls.extend(chunk.tool_calls)
        if visible_tools and not calls:
            for text in response_text:
                writer({"kind": "text", "data": {"text": text}})
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
                state["context"],
            )
            if (
                rejection is None
                and tool is not None
                and tool.revalidate is not None
                and not await tool.revalidate(state["context"])
            ):
                rejection = "unavailable"
            if rejection is not None:
                if tool is not None and tool.record_rejection is not None:
                    await tool.record_rejection(state["context"])
                elif (
                    tool is None
                    and state["capabilities"].record_unknown_tool_rejection is not None
                ):
                    await state["capabilities"].record_unknown_tool_rejection(
                        call.name, state["context"]
                    )
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
            if tool.effect == "write":
                try:
                    proposal = await action_store.create_proposal(
                        tool,
                        call.arguments,
                        state["context"],
                        expires_at=proposal_expiry(),
                    )
                except ActionDecisionError:
                    writer({"kind": "failed", "data": {"message": PROPOSAL_FAILURE}})
                    return {"terminal": True, "pending_calls": ()}
                writer({"kind": "data", "data": proposal.event_data()})
                return {"terminal": True, "pending_calls": ()}
            writer({"kind": "status", "data": {"label": "正在查询可用信息"}})
            try:
                observation = await tool.execute(call.arguments, state["context"])
                if (
                    tool.output_schema is not None
                    and validate_object(observation, tool.output_schema) is not None
                ):
                    raise ValueError("Tool output did not match its declared Schema")
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
    context: ToolContext,
) -> str | None:
    if tool is None:
        return "unknown"
    if tool.name not in available_tool_names:
        return "unavailable"
    if not tool.enabled or not tool.bound or not tool.authorize(context):
        return "unauthorized"
    return validate_object(arguments, tool.input_schema)


def _call_signature(call: ModelToolCall) -> str:
    return f"{call.name}:{json.dumps(call.arguments, sort_keys=True, ensure_ascii=True)}"
