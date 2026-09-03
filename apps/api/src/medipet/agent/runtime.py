from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, Callable
from contextlib import aclosing
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any, Literal, Protocol, TypedDict, cast

from langgraph.config import get_stream_writer
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph

from medipet.actions import (
    ActionDecisionError,
    ActionProposalExpiredError,
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
from medipet.agent.prompts import outpatient_assistant_system_prompt
from medipet.model.port import (
    ModelMessage,
    ModelPort,
    ModelRequest,
    ModelTool,
    ModelToolCall,
    ModelUnavailableError,
)
from medipet.run_audits import (
    NullRunAuditStore,
    RunAuditContext,
    RunAuditKind,
    RunAuditStore,
)
from medipet.run_metrics import RunMetricsRecorder
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
    trace_id: str = ""
    metrics: RunMetricsRecorder | None = None


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
        *,
        proposal_visible: bool = True,
    ) -> AgentEvent: ...


class ReActState(TypedDict):
    messages: tuple[ModelMessage, ...]
    context: ToolContext
    capabilities: CapabilitySnapshot
    available_tool_names: tuple[str, ...]
    loaded_skill_ids: tuple[str, ...]
    hospital_data_available: bool
    business_date: date
    business_timezone: str
    pending_calls: tuple[ModelToolCall, ...]
    correction_used: bool
    invalid_signatures: tuple[str, ...]
    model_calls: int
    terminal: bool
    trace_id: str
    metrics: RunMetricsRecorder | None


def _resolve_relative_date_messages(
    messages: tuple[ModelMessage, ...],
    *,
    business_date: date,
    business_timezone: str,
) -> tuple[ModelMessage, ...]:
    user_index = next(
        (index for index in range(len(messages) - 1, -1, -1) if messages[index].role == "user"),
        None,
    )
    if user_index is None:
        return messages
    message = messages[user_index]
    resolved = []
    if "明天" in message.content:
        resolved.append(f"明天={(business_date + timedelta(days=1)).isoformat()}")
    if not resolved:
        return messages
    annotation = f"\n\n[服务医院业务日期解析（{business_timezone}）：" + "；".join(resolved) + "。]"
    updated = list(messages)
    updated[user_index] = replace(message, content=message.content + annotation)
    return tuple(updated)


class LangGraphAgentRuntime:
    def __init__(
        self,
        model: ModelPort,
        *,
        capability_provider: CapabilityProvider | None = None,
        max_steps: int = 8,
        profile_version: str = "static",
        action_store: ActionStore | None = None,
        model_timeout_seconds: float = 30.0,
        audit_store: RunAuditStore | None = None,
        clock: Callable[[], datetime] | None = None,
        business_timezone: str = "Asia/Shanghai",
    ) -> None:
        self._action_store = action_store or UnavailableActionStore()
        self._audit_store = audit_store or NullRunAuditStore()
        self._graph = _build_react_graph(
            model,
            max_steps=max_steps,
            action_store=self._action_store,
            model_timeout_seconds=model_timeout_seconds,
            audit_store=self._audit_store,
        )
        self._capability_provider = capability_provider or StaticCapabilityProvider()
        self._max_steps = max_steps
        self._profile_version = profile_version
        self._clock = clock or (lambda: datetime.now(UTC))
        if business_timezone != "Asia/Shanghai":
            raise ValueError("unsupported business timezone")
        self._business_timezone = business_timezone
        self._business_zone = timezone(timedelta(hours=8), business_timezone)

    async def run(self, request: AgentRequest) -> AsyncGenerator[AgentEvent, None]:
        context = replace(request.context, profile_version=self._profile_version)
        current = self._clock()
        if current.tzinfo is None:
            current = current.replace(tzinfo=UTC)
        business_date = current.astimezone(self._business_zone).date()
        model_messages = _resolve_relative_date_messages(
            request.messages,
            business_date=business_date,
            business_timezone=self._business_timezone,
        )
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
                        "skill_id": skill.skill_id,
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
                record_unknown_tool_rejection=(source_capabilities.record_unknown_tool_rejection),
            )
            available_tool_names = _available_tool_names(capabilities, context, ())
            hospital_data_is_available = _hospital_data_available(capabilities, context)
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
                            "messages": model_messages,
                            "context": context,
                            "capabilities": capabilities,
                            "available_tool_names": available_tool_names,
                            "loaded_skill_ids": (),
                            "hospital_data_available": hospital_data_is_available,
                            "business_date": business_date,
                            "business_timezone": self._business_timezone,
                            "pending_calls": (),
                            "correction_used": False,
                            "invalid_signatures": (),
                            "model_calls": 0,
                            "terminal": False,
                            "trace_id": request.trace_id,
                            "metrics": request.metrics,
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
        except ModelCallTimeoutError:
            yield AgentEvent("failed", {"message": "模型响应已超时，请稍后重试。"})
        except GraphRecursionError:
            yield AgentEvent("failed", {"message": BUDGET_FAILURE})

    async def decide(
        self,
        proposal_id: str,
        decision: Literal["confirm", "reject"],
        context: ToolContext,
        *,
        proposal_visible: bool = True,
    ) -> AgentEvent:
        context = replace(context, profile_version=self._profile_version)
        try:
            persisted = await self._action_store.validate_decision_scope(proposal_id, context)
            if not proposal_visible:
                raise ActionDecisionError("预约确认已不在当前聊天历史中，请重新发起。")
            if decision == "reject":
                proposal = await self._action_store.reject(proposal_id, context)
            else:
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
                    raise ActionDecisionError("操作参数、Tool 版本或作用域已变化，请重新发起")
                proposal, _ = await self._action_store.confirm(
                    proposal_id,
                    context,
                    tool,
                )
            return AgentEvent("data", proposal.event_data())
        except ActionProposalExpiredError as error:
            await self._action_store.record_decision_rejection(proposal_id, context)
            return AgentEvent("data", error.proposal.event_data())
        except ActionDecisionError as error:
            await self._action_store.record_decision_rejection(proposal_id, context)
            return AgentEvent("failed", {"message": str(error)})
        except Exception:
            await self._action_store.record_decision_rejection(proposal_id, context)
            return AgentEvent("failed", {"message": CAPABILITY_FAILURE})


class ModelCallTimeoutError(RuntimeError):
    pass


def _build_react_graph(
    model: ModelPort,
    *,
    max_steps: int,
    action_store: ActionStore,
    model_timeout_seconds: float,
    audit_store: RunAuditStore,
):
    async def audit(kind: RunAuditKind, state: ReActState) -> None:
        await audit_store.record(
            kind,
            RunAuditContext(
                trace_id=state["trace_id"],
                visit_matter_id=state["context"].visit_matter_id,
                turn_id=state["context"].idempotency_key,
                profile_version=state["context"].profile_version,
            ),
        )

    async def call_model(state: ReActState) -> dict[str, object]:
        writer = get_stream_writer()
        metrics = state["metrics"]
        if state["model_calls"] >= max_steps:
            await audit("budget_exhausted", state)
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
                ModelMessage(
                    role="system",
                    content=outpatient_assistant_system_prompt(
                        hospital_data_available=state["hospital_data_available"],
                        business_date=state["business_date"],
                        business_timezone=state["business_timezone"],
                    ),
                ),
                *state["messages"],
            ),
            tools=visible_tools,
        )
        response_text: list[str] = []
        calls: list[ModelToolCall] = []
        visible_text_started = False
        for attempt in range(2):
            response_text.clear()
            calls.clear()
            input_tokens = 0
            output_tokens = 0
            model_started = metrics.begin_model_call() if metrics is not None else None
            try:
                async with asyncio.timeout(model_timeout_seconds):
                    async for chunk in model.stream(model_request):
                        input_tokens += chunk.input_tokens
                        output_tokens += chunk.output_tokens
                        if chunk.text:
                            response_text.append(chunk.text)
                            if not visible_tools:
                                if metrics is not None:
                                    metrics.mark_first_token()
                                writer({"kind": "text", "data": {"text": chunk.text}})
                                visible_text_started = True
                        calls.extend(chunk.tool_calls)
                break
            except ModelUnavailableError as error:
                if error.retryable and attempt == 0 and not visible_text_started:
                    await audit("retry", state)
                    continue
                raise
            except TimeoutError as error:
                await audit("model_timeout", state)
                raise ModelCallTimeoutError from error
            finally:
                if metrics is not None and model_started is not None:
                    metrics.end_model_call(
                        model_started,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                    )
        if visible_tools and not calls:
            for text in response_text:
                if metrics is not None:
                    metrics.mark_first_token()
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
        metrics = state["metrics"]
        messages = list(state["messages"])
        correction_used = state["correction_used"]
        invalid_signatures = list(state["invalid_signatures"])
        loaded_skill_ids = list(state["loaded_skill_ids"])
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
                    tool is None and state["capabilities"].record_unknown_tool_rejection is not None
                ):
                    await state["capabilities"].record_unknown_tool_rejection(
                        call.name, state["context"]
                    )
                signature = _call_signature(call)
                if correction_used or signature in invalid_signatures:
                    await audit("loop_detected", state)
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
                    existing_proposal = await action_store.find_request_proposal(
                        tool,
                        call.arguments,
                        state["context"],
                    )
                    if existing_proposal is not None:
                        writer({"kind": "data", "data": existing_proposal.event_data()})
                        return {"terminal": True, "pending_calls": ()}
                    confirmation: dict[str, object] | None = None
                    confirmation_contract = tool.confirmation_contract
                    if confirmation_contract is not None:
                        confirmation = await confirmation_contract.prepare(
                            dict(call.arguments), state["context"]
                        )
                    if confirmation_contract is not None and (
                        confirmation is None
                        or validate_object(confirmation, confirmation_contract.schema) is not None
                    ):
                        raise ActionDecisionError("写 Tool 的确认快照不符合 Schema")
                    proposal = await action_store.create_proposal(
                        tool,
                        call.arguments,
                        state["context"],
                        confirmation=confirmation,
                        expires_at=proposal_expiry(),
                    )
                except Exception:
                    writer({"kind": "failed", "data": {"message": PROPOSAL_FAILURE}})
                    return {"terminal": True, "pending_calls": ()}
                writer({"kind": "data", "data": proposal.event_data()})
                return {"terminal": True, "pending_calls": ()}
            writer({"kind": "status", "data": {"label": "正在查询可用信息"}})
            tool_started = metrics.begin_tool_call() if metrics is not None else None
            terminal_after_presentation = False
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
                if call.name == "load_skill":
                    loaded_skill_id = observation.get("skill_id")
                    if isinstance(loaded_skill_id, str) and loaded_skill_id not in loaded_skill_ids:
                        loaded_skill_ids.append(loaded_skill_id)
                if tool.present is not None:
                    for data_part in await tool.present(observation, state["context"]):
                        writer({"kind": "data", "data": data_part})
                        if data_part.get("type") in {
                            "data-hospital-wayfinding",
                            "data-hospital-wayfinding-unavailable",
                        }:
                            terminal_after_presentation = True
            except Exception:
                writer({"kind": "failed", "data": {"message": TOOL_FAILURE}})
                return {"terminal": True, "pending_calls": ()}
            finally:
                if metrics is not None and tool_started is not None:
                    metrics.end_tool_call(tool_started)
            messages.append(
                ModelMessage(
                    role="tool",
                    content=observation_json,
                    tool_call_id=call.id,
                )
            )
            if terminal_after_presentation:
                return {
                    "messages": tuple(messages),
                    "pending_calls": (),
                    "terminal": True,
                    "loaded_skill_ids": tuple(loaded_skill_ids),
                }

        return {
            "messages": tuple(messages),
            "pending_calls": (),
            "correction_used": correction_used,
            "invalid_signatures": tuple(invalid_signatures),
            "loaded_skill_ids": tuple(loaded_skill_ids),
            "available_tool_names": _available_tool_names(
                state["capabilities"],
                state["context"],
                tuple(loaded_skill_ids),
            ),
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


def _available_tool_names(
    capabilities: CapabilitySnapshot,
    context: ToolContext,
    loaded_skill_ids: tuple[str, ...],
) -> tuple[str, ...]:
    loaded = set(loaded_skill_ids)
    return tuple(
        tool.name
        for tool in capabilities.tools
        if tool.enabled
        and tool.bound
        and (tool.effect == "read" or tool.approval_required)
        and tool.authorize(context)
        and (not tool.required_skill_ids or bool(loaded.intersection(tool.required_skill_ids)))
    )


def hospital_data_available(
    capabilities: CapabilitySnapshot,
    context: ToolContext,
) -> bool:
    return _hospital_data_available(capabilities, context)


def _hospital_data_available(
    capabilities: CapabilitySnapshot,
    context: ToolContext,
) -> bool:
    return any(
        tool.tool_id.startswith("hospital.")
        and tool.enabled
        and tool.bound
        and tool.authorize(context)
        for tool in capabilities.tools
    )


def _call_signature(call: ModelToolCall) -> str:
    return f"{call.name}:{json.dumps(call.arguments, sort_keys=True, ensure_ascii=True)}"
