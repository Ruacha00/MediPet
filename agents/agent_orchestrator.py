"""
亮点：多 Agent 路由与编排

核心问题：多 Agent 情况下如何做 Routing？

路由策略（三层决策）：
  1. 意图路由 —— 根据 IntentCategory 直接映射到专属 Agent
  2. 性能路由 —— 同类 Agent 有多个时，选成功率最高、延迟最低的
  3. 降级路由 —— 专属 Agent 不可用时，自动降级到 GeneralAgent

并行协作：
  - 复合需求（如"查号源 + 就诊准备"）可同时派发给多个 Agent
  - 结果由 Orchestrator 合并后返回

升级机制：
  - 人工导诊或明确急症意图 → EscalationAgent
"""
import asyncio
import inspect
import json
import logging
import os
import time
import uuid
from collections import deque
from datetime import datetime
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from anthropic import AsyncAnthropic

from agents.tools import (
    AgentToolSpec,
    build_health_tools,
    build_hospital_tools,
    build_shared_rag_tools,
)
from core.intent_recognizer import IntentCategory, IntentRecognizer, UrgencyLevel
from core.llm_utils import extract_text_content, llm_request_options
from core.emergency import EMERGENCY_RESPONSE, detect_emergency, is_emergency_reference
from hospital.models import Artifact
from hospital.service import HospitalService

logger = logging.getLogger(__name__)


# ── 数据结构 ──────────────────────────────────────────────────────────────────

class AgentType(Enum):
    GENERAL   = "general"       # 医院公开信息与澄清
    GUIDANCE = "guidance"       # 就诊准备、流程和文字指引
    APPOINTMENT = "appointment" # 号源与预约事务
    TRIAGE = "triage"           # 初步科室建议和报告预处理
    MEDICATION = "medication"   # 有来源的药品说明书信息
    ESCALATION = "escalation"   # 人工导诊与急症固定响应


@dataclass(frozen=True)
class AgentProfile:

    role: str
    mission: str
    workflow: Tuple[str, ...]
    input_contract: Tuple[str, ...]
    output_contract: Tuple[str, ...]
    handoff_conditions: Tuple[str, ...] = ()
    tool_scope: Tuple[str, ...] = ()
    model: Optional[str] = None
    temperature: float = 0.2
    max_tokens: int = 1024


def _env_float(name: str, default: float) -> float:
    """读取可选浮点配置；错误配置不应阻塞服务启动。"""
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        logger.warning("忽略非法浮点配置 %s=%r", name, os.getenv(name))
        return default


def _env_int(name: str, default: int) -> int:
    """读取可选整数配置；错误配置不应阻塞服务启动。"""
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        logger.warning("忽略非法整数配置 %s=%r", name, os.getenv(name))
        return default


@dataclass
class AgentStats:
    """Agent 运行时统计，供 Monitor 和路由决策使用。"""
    total:     int   = 0
    success:   int   = 0
    total_ms:  float = 0.0
    monitor_penalty: float = 0.0

    @property
    def success_rate(self) -> float:
        return self.success / self.total if self.total else 1.0

    @property
    def avg_ms(self) -> float:
        return self.total_ms / self.total if self.total else 0.0

    def routing_score(self) -> float:
        """路由评分：成功率高、延迟低的 Agent 得分高。"""
        latency_score = 1.0 / (1.0 + self.avg_ms / 1000)
        base_score = self.success_rate * 0.7 + latency_score * 0.3
        return base_score * max(0.0, 1.0 - self.monitor_penalty)


@dataclass
class AgentResponse:
    agent_type:  AgentType
    content:     str
    success:     bool
    confidence:  float = 1.0
    latency_ms:  float = 0.0
    escalate:    bool  = False   # 是否需要升级
    tools_used:  List[str] = field(default_factory=list)
    tool_traces: List[Dict[str, Any]] = field(default_factory=list)
    artifacts: List[Artifact] = field(default_factory=list)


@dataclass
class AgentTurn:
    """单次 handle 独占的执行记录，不保存在可复用 Agent 上。"""
    tools_used: List[str] = field(default_factory=list)
    tool_traces: List[Dict[str, Any]] = field(default_factory=list)
    artifacts: List[Artifact] = field(default_factory=list)


def _merge_artifacts(artifacts: List[Artifact], traces: List[Dict[str, Any]]) -> List[Artifact]:
    """同一快照去重；同 ID 冲突排除全部版本，跨降级/并行合并仍不可复活。"""
    blocked = {trace["artifact_id"] for trace in traces
               if trace.get("error_code") == "artifact_conflict" and trace.get("artifact_id")}
    unique: Dict[str, Artifact] = {}
    for artifact in artifacts:
        if artifact.id in blocked:
            continue
        previous = unique.get(artifact.id)
        if previous is not None and previous != artifact:
            blocked.add(artifact.id)
            unique.pop(artifact.id)
            traces.append({"kind": "artifact_validation", "success": False,
                           "error_code": "artifact_conflict", "artifact_id": artifact.id,
                           "error": "同一展示卡编号存在不同业务快照，已排除所有冲突版本。"})
        elif previous is None:
            unique[artifact.id] = artifact.model_copy(deep=True)
    return list(unique.values())


@dataclass
class Request:
    message:     str
    user_id:     str
    conv_id:     str
    context:     str = ""        # 来自 MemoryManager 的格式化上下文
    history:     Optional[List[Dict[str, str]]] = None  # 对话历史，传给意图识别
    entities:    Dict[str, List[str]] = field(default_factory=dict)
    intent:      Optional[IntentCategory] = None
    intent_group: Optional[str] = None
    urgency:     Optional[UrgencyLevel]   = None
    intent_confidence: float = 1.0
    request_id:  str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    patient_id: Optional[str] = None  # 业务路径由服务器校验事项后提供，不从模型补写。


@dataclass
class OrchestratorResult:
    request_id:  str
    response:    str
    agent_type:  AgentType
    intent:      Optional[IntentCategory]
    escalated:   bool  = False
    latency_ms:  float = 0.0
    agent_types: List[AgentType] = field(default_factory=list)
    primary_agent: Optional[AgentType] = None
    supporting_agents: List[AgentType] = field(default_factory=list)
    tools_used: List[str] = field(default_factory=list)
    tool_traces: List[Dict[str, Any]] = field(default_factory=list)
    routing_reason: str = ""
    routing_confidence: float = 0.0
    artifacts: List[Artifact] = field(default_factory=list)


@dataclass
class RoutingDecision:
    """一次请求的结构化路由决策。"""
    primary_agent: AgentType
    supporting_agents: List[AgentType] = field(default_factory=list)
    reason: str = ""
    confidence: float = 0.0

    @property
    def agent_types(self) -> List[AgentType]:
        return [self.primary_agent] + self.supporting_agents

    @property
    def multi_agent(self) -> bool:
        return bool(self.supporting_agents)


# ── 基础 Agent ────────────────────────────────────────────────────────────────

class BaseAgent:
    """所有 Agent 的基类，封装 LLM 调用、角色契约和统计。"""

    agent_type: AgentType
    system_prompt: str
    profile: AgentProfile

    def __init__(
        self,
        client: AsyncAnthropic,
        model: str,
        skill_manager: Optional[Any] = None,
        profile: Optional[AgentProfile] = None,
        *,
        hospital_service: Optional[Any] = None,
        visit_store: Optional[Any] = None,
    ):
        self._client = client
        self.profile = profile or self.profile
        self._model  = self.profile.model or model
        self._skill_manager = skill_manager
        self.stats   = AgentStats()
        self._shared_tools: Dict[str, AgentToolSpec] = {}
        self._hospital_tools = {
            **build_hospital_tools(self.agent_type.value, hospital_service, visit_store),
            **build_health_tools(self.agent_type.value, visit_store or getattr(hospital_service, "visits", None)),
        }

    def get_tools(self) -> Dict[str, AgentToolSpec]:
        """返回该角色真实可调用的工具白名单。"""
        available = {**self._shared_tools, **self._hospital_tools}
        return {name: spec for name, spec in available.items() if name in self.profile.tool_scope}

    def set_shared_tools(self, tools: Optional[Dict[str, AgentToolSpec]]) -> None:
        self._shared_tools = dict(tools or {})

    async def handle(self, req: Request) -> AgentResponse:
        t0 = time.monotonic()
        self.stats.total += 1
        turn = AgentTurn()
        try:
            content = await self._call_llm(req, turn)
            ms = (time.monotonic() - t0) * 1000
            self.stats.success += 1
            self.stats.total_ms += ms
            escalate = self._needs_escalation(content)
            return AgentResponse(
                agent_type=self.agent_type,
                content=content,
                success=True,
                latency_ms=ms,
                escalate=escalate,
                tools_used=list(turn.tools_used),
                tool_traces=turn.tool_traces,
                artifacts=_merge_artifacts(turn.artifacts, turn.tool_traces),
            )
        except Exception as ex:
            ms = (time.monotonic() - t0) * 1000
            self.stats.total_ms += ms
            logger.error(f"{self.agent_type.value} 处理失败: {ex}")
            turn.tool_traces.append({"kind": "agent_execution", "agent_type": self.agent_type.value,
                                    "success": False, "error": str(ex)})
            return AgentResponse(
                agent_type=self.agent_type,
                content="抱歉，处理您的请求时出现问题，请稍后重试。",
                success=False,
                latency_ms=ms,
                tools_used=list(turn.tools_used),
                tool_traces=turn.tool_traces,
                artifacts=_merge_artifacts(turn.artifacts, turn.tool_traces),
            )

    async def _call_llm(self, req: Request, turn: AgentTurn) -> str:
        def _clean(s: str) -> str:
            return s.encode("utf-8", errors="ignore").decode("utf-8")

        messages = []
        if req.context:
            messages.append({"role": "user", "content": f"[背景信息]\n{_clean(req.context)}"})
            messages.append({"role": "assistant", "content": "好的，我已了解背景信息。"})
        if req.entities:
            entities_text = json.dumps(req.entities, ensure_ascii=False)
            messages.append({"role": "user", "content": f"[结构化实体]\n{_clean(entities_text)}"})
            messages.append({"role": "assistant", "content": "好的，我会结合这些结构化实体处理。"})
        role_packet = self._build_role_packet(req)
        if role_packet:
            messages.append({"role": "user", "content": f"[角色输入契约]\n{_clean(role_packet)}"})
            messages.append({"role": "assistant", "content": "好的，我会按照该角色的输入和输出契约处理。"})
        messages.append({"role": "user", "content": _clean(req.message)})

        tools = self.get_tools()
        tools_used = turn.tools_used
        tool_traces = turn.tool_traces
        for _ in range(3):
            request_kwargs: Dict[str, Any] = {
                **llm_request_options(),
                "model": self._model,
                "max_tokens": self.profile.max_tokens,
                "temperature": self.profile.temperature,
                "system": self._build_system_prompt(req),
                "messages": messages,
            }
            if tools:
                request_kwargs["tools"] = [
                    {
                        "name": spec.name,
                        "description": spec.description,
                        "input_schema": spec.input_schema,
                    }
                    for spec in tools.values()
                ]
            resp = await self._client.messages.create(**request_kwargs)
            tool_uses = [block for block in (resp.content or []) if self._block_type(block) == "tool_use"]
            if not tool_uses:
                return extract_text_content(resp.content)

            messages.append({"role": "assistant", "content": resp.content})
            tool_results = []
            for block in tool_uses:
                name = self._block_value(block, "name")
                tool_use_id = self._block_value(block, "id")
                args = self._block_value(block, "input") or {}
                spec = tools.get(name)
                tool_t0 = time.monotonic()
                call_success = True
                result_success: Optional[bool] = None
                error_text = ""
                if spec is None:
                    call_success = False
                    result: Any = {"success": False, "error": f"工具不在 {self.agent_type.value} Agent 白名单中"}
                    error_text = result["error"]
                else:
                    try:
                        self._validate_tool_input(spec, args)
                        result = spec.handler(req, args)
                        if inspect.isawaitable(result):
                            result = await result
                        tools_used.append(name)
                        if isinstance(result, dict) and "success" in result:
                            result_success = bool(result.get("success"))
                        if isinstance(result, dict):
                            turn.artifacts.extend(
                                Artifact.model_validate(artifact).model_copy(deep=True)
                                for artifact in result.get("artifacts", [])
                            )
                    except Exception as ex:
                        call_success = False
                        logger.warning("Agent 工具 %s 执行失败: %s", name, ex)
                        error_text = str(ex)
                        result = {"success": False, "error": error_text}
                tool_latency_ms = (time.monotonic() - tool_t0) * 1000
                if not error_text and isinstance(result, dict):
                    error_text = str(result.get("error", "") or "")
                source_details = {}
                if name == "search_knowledge_base":
                    sources = []
                    if call_success and result_success is True and isinstance(result, dict):
                        retrieved = result.get("results", [])
                        if isinstance(retrieved, list):
                            for item in retrieved:
                                if not isinstance(item, dict):
                                    continue
                                source = {key: item[key] for key in ("title", "source", "source_id", "doc_id", "chunk_id")
                                          if isinstance(item.get(key), str) and item[key]}
                                if source:
                                    sources.append(source)
                    source_details["sources"] = sources
                tool_traces.append(
                    {
                        "agent_type": self.agent_type.value,
                        "tool_name": name,
                        "tool_use_id": tool_use_id,
                        "input": dict(args) if isinstance(args, dict) else args,
                        "kind": "tool_call",
                        "success": call_success and result_success is not False,
                        "call_success": call_success,
                        "result_success": result_success,
                        "latency_ms": round(tool_latency_ms, 1),
                        "cached": bool(result.get("cached")) if isinstance(result, dict) else False,
                        "reranked": bool(result.get("reranked")) if isinstance(result, dict) else False,
                        "error": error_text,
                        "error_code": result.get("error_code") if isinstance(result, dict) else None,
                        **source_details,
                        "result_summary": {
                            "success": result_success,
                            "data_keys": list(result.get("data", {})) if isinstance(result, dict) and isinstance(result.get("data", {}), dict) else [],
                            "artifact_ids": [item.get("id") for item in result.get("artifacts", []) if isinstance(item, dict)] if isinstance(result, dict) else [],
                            "result_count": len(result.get("results", [])) if isinstance(result, dict) and isinstance(result.get("results", []), list) else 0,
                        },
                        **({key: result[key] for key in ("rewrite_error", "rerank_error", "recall_errors", "partial", "fallback_used") if key in result} if isinstance(result, dict) else {}),
                    }
                )
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": json.dumps(result, ensure_ascii=False),
                })
            messages.append({"role": "user", "content": tool_results})

        raise RuntimeError(f"{self.agent_type.value} 工具调用超过最大轮数")

    @staticmethod
    def _block_type(block: Any) -> Optional[str]:
        if isinstance(block, dict):
            return block.get("type")
        return getattr(block, "type", None)

    @staticmethod
    def _block_value(block: Any, key: str) -> Any:
        if isinstance(block, dict):
            return block.get(key)
        return getattr(block, key, None)

    @staticmethod
    def _validate_tool_input(spec: AgentToolSpec, args: Any) -> None:
        if not isinstance(args, dict):
            raise ValueError("工具参数必须是 JSON 对象")
        schema = spec.input_schema
        for field_name in schema.get("required", []):
            if field_name not in args:
                raise ValueError(f"缺少必需参数: {field_name}")
        properties = schema.get("properties", {})
        unknown = set(args) - set(properties)
        if unknown and schema.get("additionalProperties") is False:
            raise ValueError(f"不允许的工具参数: {', '.join(sorted(unknown))}")
        type_map = {"string": str, "number": (int, float), "integer": int, "boolean": bool}
        for key, value in args.items():
            expected = properties.get(key, {}).get("type")
            if expected in type_map and not isinstance(value, type_map[expected]):
                raise ValueError(f"参数 {key} 类型错误，期望 {expected}")
            if expected in {"integer", "number"} and isinstance(value, bool):
                raise ValueError(f"参数 {key} 不能使用布尔值代替数字")
            rules = properties.get(key, {})
            if "enum" in rules and value not in rules["enum"]:
                raise ValueError(f"参数 {key} 不在允许值中")
            if "minimum" in rules and value < rules["minimum"]:
                raise ValueError(f"参数 {key} 小于允许的最小值")

    def _build_system_prompt(self, req: Request) -> str:
        """把角色契约和动态 Skills 拼入 system prompt。"""
        profile_prompt = (
            f"\n\n[角色契约]\n"
            f"角色：{self.profile.role}\n"
            f"职责：{self.profile.mission}\n"
            f"处理流程：{' -> '.join(self.profile.workflow)}\n"
            f"可用输入：{'；'.join(self.profile.input_contract)}\n"
            f"输出要求：{'；'.join(self.profile.output_contract)}\n"
            f"升级条件：{'；'.join(self.profile.handoff_conditions) or '无，按通用就诊规则处理'}\n"
            f"允许的数据/工具范围：{'、'.join(self.profile.tool_scope) or '仅使用当前请求上下文'}\n"
            "不要声称执行了未提供的查询、预约或取消操作；缺少证据时明确说明需要核验。"
            "患者和事项身份由请求绑定，不能根据消息中的自称或模型推测更换。"
        )
        base_prompt = f"{self.system_prompt}{profile_prompt}"
        if self._skill_manager is None:
            return base_prompt
        skill_prompt = self._skill_manager.prompt_for(req.message, self.agent_type.value)
        if not skill_prompt:
            return base_prompt
        return f"{base_prompt}\n\n[动态 Skills]\n{skill_prompt}"

    def _build_role_packet(self, req: Request) -> str:
        """给子 Agent 的确定性输入包；子类可补充领域字段。"""
        packet = {
            "agent_type": self.agent_type.value,
            "intent": req.intent.value if req.intent else None,
            "intent_group": req.intent_group,
            "urgency": req.urgency.name if req.urgency else None,
            "intent_confidence": round(req.intent_confidence, 4),
            "available_entities": req.entities or {},
            "visit_identity": {"user_id": req.user_id, "patient_id": req.patient_id, "conv_id": req.conv_id},
        }
        return json.dumps(packet, ensure_ascii=False)

    def _needs_escalation(self, content: str) -> bool:
        """检测 Agent 是否建议升级（简单关键词检测）。"""
        keywords = ["转人工", "人工导诊", "escalate", "无法处理"]
        return any(kw in content for kw in keywords)


class GeneralAgent(BaseAgent):
    agent_type    = AgentType.GENERAL
    profile = AgentProfile(
        role="医院公开信息与需求澄清",
        mission="回答医院、科室和医生的公开信息，澄清缺失条件，并识别就诊事务分工。",
        workflow=("理解就诊诉求", "核对公开信息", "回答或澄清必要条件", "给出下一步"),
        input_contract=("当前患者及事项", "最近对话", "意图与实体", "医院公开信息及来源"),
        output_contract=("先回应核心问题", "信息不足时只询问必要字段", "明确下一步和边界"),
        handoff_conditions=("公开信息不足且需要人工核实", "用户明确要求人工导诊"),
        tool_scope=("search_knowledge_base", "inspect_request_context", "query_hospital_catalog"),
        temperature=0.3,
        max_tokens=900,
    )
    system_prompt = (
        "你是 MediPet 门诊就诊助手，负责医院、科室和医生的公开信息及需求澄清。"
        "依据已提供的数据回答，不编造医院事实。症状分诊、药品信息和报告预处理由对应专业角色提供；"
        "若其工具失败，说明需要补充资料或咨询医师药师，不用模型常识补造诊断、剂量或报告结论。"
    )

    def _build_role_packet(self, req: Request) -> str:
        packet = json.loads(super()._build_role_packet(req))
        packet["triage_targets"] = ["guidance", "appointment", "escalation"]
        packet["response_mode"] = "answer_or_clarify"
        return json.dumps(packet, ensure_ascii=False)

class GuidanceAgent(BaseAgent):
    agent_type    = AgentType.GUIDANCE
    profile = AgentProfile(
        role="就诊准备、流程与院内文字指引",
        mission="说明就诊材料和报到流程，按预置医院文字提供普通或无障碍指引。",
        workflow=("确认科室及就诊需求", "核对材料或地点", "检索既有流程和文字指引", "按步骤说明并给出来源"),
        input_contract=("当前患者及事项", "科室及日期", "起点与目的地", "无障碍模式", "知识库上下文"),
        output_contract=("材料清单或办理步骤", "预置文字指引", "信息来源", "缺失条件"),
        handoff_conditions=("未知地点或没有预置路线", "需要人工导诊核实"),
        tool_scope=("search_knowledge_base", "get_visit_checklist", "get_wayfinding"),
        temperature=0.1,
        max_tokens=1200,
    )
    system_prompt = (
        "你是 MediPet 就诊指引助手，负责材料准备、报到流程和院内文字指引。"
        "仅使用给定清单、检索文档和预置路线，不计算实时导航，不给出诊断或用药建议。"
        "先结合当前绑定患者、同一事项的上下文核对条件；本轮未重复起点或目的地不等于缺失。"
        "同事项已有唯一明确起终点，用户仅要求改为无障碍或普通路线时，沿用这些地点，"
        "立即调用 get_wayfinding，将 mode 分别设为 accessible 或 normal，再按本轮工具结果回答。"
        "这是只读查询，无需用户再次确认已有地点，不能只口头改写上一条路线。"
        "身份绑定缺失或冲突、地点未知、条件确实缺失或有多组起终点无法确定时应澄清，不能猜测。"
        "此规则不改变预约或取消必须由页面确认接口执行的边界，不能编造办理窗口或路线。"
    )

    def _build_role_packet(self, req: Request) -> str:
        packet = json.loads(super()._build_role_packet(req))
        packet["guidance_fields"] = {
            key: req.entities.get(key, [])
            for key in ("department", "date", "origin", "destination", "accessibility")
        }
        return json.dumps(packet, ensure_ascii=False)

class AppointmentAgent(BaseAgent):
    agent_type    = AgentType.APPOINTMENT
    profile = AgentProfile(
        role="号源查询与预约事务",
        mission="查询号源和当前患者的预约，按真实选择准备创建或取消方案，等待明确确认。",
        workflow=("核对当前事项与条件", "查询真实号源或预约", "核对选择", "准备确认资料", "提示页面确认"),
        input_contract=("当前患者及事项", "科室或医生", "日期与时段", "真实号源或预约编号", "当前列表选择位置"),
        output_contract=("有来源的号源或预约信息", "待确认资料", "缺失条件及下一步"),
        handoff_conditions=("当前信息无法核实", "需要人工导诊协助"),
        tool_scope=("search_knowledge_base", "search_slots", "list_appointments", "prepare_appointment", "prepare_cancellation"),
        temperature=0.0,
        max_tokens=1100,
    )
    system_prompt = (
        "你是 MediPet 预约事务助手，负责号源查询、预约记录和待确认方案。"
        "不得猜测号源或预约 ID；“第一个”只能对应当前事项的真实可选列表。"
        "对话中的确认不能执行预约或取消，执行以页面确认接口返回为准，未确认不得声称成功。"
    )

    def _build_role_packet(self, req: Request) -> str:
        packet = json.loads(super()._build_role_packet(req))
        packet["appointment_fields"] = {
            key: req.entities.get(key, [])
            for key in ("department", "doctor", "date", "period", "slot_id", "appointment_id", "selection_index")
        }
        packet["confirmation_boundary"] = "仅准备方案；执行结果必须来自页面确认接口"
        return json.dumps(packet, ensure_ascii=False)

class TriageAgent(BaseAgent):
    agent_type = AgentType.TRIAGE
    profile = AgentProfile(
        role="初步分诊与报告预处理",
        mission="根据用户原话提供有限科室建议，整理报告项目和原文参考范围，不替代医生诊断。",
        workflow=("核对当前描述", "调用分诊或报告工具", "说明依据与待补信息", "提示专业复核"),
        input_contract=("当前患者事项", "用户症状原文", "用户报告原文或已提取报告"),
        output_contract=("初步科室或报告整理", "来源及待核对内容", "不替代医生诊断的说明"),
        handoff_conditions=("急症信号", "资料不足", "需要医生诊断"),
        tool_scope=("triage_symptoms", "preprocess_report", "read_current_report", "search_knowledge_base"),
        temperature=0.0, max_tokens=1200,
    )
    system_prompt = (
        "你是 MediPet 预检分诊与报告预处理助手。症状问题先调用 triage_symptoms；"
        "粘贴了报告内容时调用 preprocess_report，若只问上传方式，提示使用页面报告上传按钮（PDF或图片）。"
        "用户追问此前上传或粘贴的报告时先调用 read_current_report，不能从记忆摘要猜测报告值或跨患者取数。"
        "科室、数值和参考区间以本轮工具为准，保留未评估和待核对项，不根据模型常识补造。"
        "只能给初步科室建议和信息参考，不替代医生诊断、不开处方，不评论其他医院或医生的诊疗方案。"
        "没有查到内容时说明范围并澄清，不能把工具失败包装成成功，不替用户直接预约。"
    )


class MedicationAgent(BaseAgent):
    agent_type = AgentType.MEDICATION
    profile = AgentProfile(
        role="药品说明书信息咨询",
        mission="查询有来源的指定剂型说明书、禁忌和相互作用警示，不开处方或生成个体剂量。",
        workflow=("核对药名规格剂型", "调用药品查询工具", "展示来源与未知内容", "提示医师药师复核"),
        input_contract=("用户明确提及的药品", "药品规格剂型", "用户提及的其他药品"),
        output_contract=("说明书参考信息", "禁忌与组合警示", "资料来源与覆盖范围"),
        handoff_conditions=("特殊人群", "个体治疗或剂量", "未收录药物或组合", "疑似过量"),
        tool_scope=("medication_information", "search_knowledge_base"),
        temperature=0.0, max_tokens=1400,
    )
    system_prompt = (
        "你是 MediPet 药品信息助手。涉及药品先调用 medication_information；参数逐字来自用户药名，"
        "不得为了适配目录擅改药名、规格、剂型。仅转述工具返回的指定地区产品说明书，"
        "明确不替代手中药品说明书和医师药师，不将说明书剂量改写成对当前患者的服药指令。"
        "儿童、孕哺、肝肾异常及个体剂量问题需要医师药师；未知相互作用不能说安全或可同服。"
        "不开处方、不替用户选药、不建议停改其他医生用药方案，不评论其他医院或医生的诊疗方案。"
        "不能用模型常识补充未检索到的药品事实，失败时如实说明。"
    )


class EscalationAgent(BaseAgent):
    """人工导诊节点；输出联系摘要，不代表已提交给真实工作人员。"""

    agent_type = AgentType.ESCALATION
    profile = AgentProfile(
        role="人工导诊与急症提示",
        mission="整理当前就诊问题并提示联系人工导诊，不表示请求已经送达工作人员。",
        workflow=("确认升级原因", "整理已知信息", "标记优先级", "生成交接摘要"),
        input_contract=("用户消息", "意图", "紧急度", "结构化实体", "对话背景"),
        output_contract=("联系原因", "可复制的问题摘要", "已知信息", "后续说明"),
        handoff_conditions=("用户明确要求人工", "紧急或高风险场景"),
        tool_scope=(),
        temperature=0.0,
        max_tokens=500,
    )
    system_prompt = "你负责人工导诊联系摘要，不得声称已联系真实工作人员或完成预约操作。"

    def __init__(self, *args, hospital_service: Optional[Any] = None, **kwargs):
        super().__init__(*args, hospital_service=hospital_service, **kwargs)
        data = getattr(hospital_service, "data", None)
        self._contact_info = (data.contact_info if data is not None else HospitalService().data.contact_info).model_copy(deep=True)

    async def handle(self, req: Request) -> AgentResponse:
        t0 = time.monotonic()
        self.stats.total += 1
        intent = req.intent.value if req.intent else "unknown"
        urgency = req.urgency.name if req.urgency else "UNKNOWN"
        entities = json.dumps(req.entities or {}, ensure_ascii=False)
        contact = self._contact_info.model_copy(update={"summary": req.message}, deep=True)
        emergency = detect_emergency(req.message)
        content = (
            (EMERGENCY_RESPONSE + "\n\n" if emergency else "") +
            "您可以联系医院人工导诊，并复制以下问题摘要。\n\n"
            f"{contact.label}：{contact.phone}\n服务时间：{contact.hours}\n位置：{contact.location}\n"
            f"问题摘要：{req.message}\n"
            f"联系原因：意图={intent}，紧急度={urgency}\n"
            f"已提供信息：{entities}\n"
            "此处仅整理摘要，尚未向工作人员提交请求。"
        )
        ms = (time.monotonic() - t0) * 1000
        self.stats.success += 1
        self.stats.total_ms += ms
        return AgentResponse(
            agent_type=self.agent_type,
            content=content,
            success=True,
            latency_ms=ms,
            escalate=True,
            tools_used=[],
            artifacts=[Artifact(id=f"contact_info:{uuid.uuid4().hex}", type="contact_info", data=contact.model_dump(mode="json"))],
        )


class ResponseComposer:
    """多 Agent 汇总节点，统一主次、去重和输出边界。"""

    def __init__(self, client: AsyncAnthropic, model: str, skill_manager: Optional[Any] = None):
        self._client = client
        self._model = model
        self._skill_manager = skill_manager

    async def compose(self, req: Request, responses: List[AgentResponse]) -> str:
        successful = [response for response in responses if response.success and response.content.strip()]
        if not successful:
            return "抱歉，所有 Agent 均处理失败。"
        if len(successful) == 1:
            return successful[0].content

        evidence = "\n\n".join(
            f"[{response.agent_type.value} Agent 输出]\n{response.content}"
            for response in successful
        )
        prompt = (
            "你是 MediPet Response Composer，负责把多个就诊助手的结果合并成一条最终回复。\n"
            "要求：以主 Agent 的结论为主，按用户问题优先级组织内容；去掉重复和冲突表述；"
            "不能补造号源、预约、取消结果，也不能更换当前患者；如果结论冲突，明确说明需要核验；"
            "保留材料、流程、来源和确认边界；失败的子任务不能写成已完成。只输出中文回复，不要提及 Agent。\n\n"
            "保留医疗来源及未核对项；科室建议与报告整理不等于诊断，说明书信息不等于个体用药指令。"
            "不替代医生诊断、不开处方、不评论其他医院或医生的诊疗方案。\n"
            f"主 Agent：{successful[0].agent_type.value}\n"
            f"用户问题：{req.message}\n"
            f"候选结果：\n{evidence}"
        )
        if self._skill_manager is not None:
            skill = self._skill_manager.prompt_for(req.message, "general")
            if skill:
                prompt += f"\n\n[通用就诊输出边界]\n{skill}"
        try:
            response = await self._client.messages.create(
                **llm_request_options(),
                model=self._model,
                max_tokens=_env_int("MEDIPET_COMPOSER_MAX_TOKENS", 1000),
                temperature=_env_float("MEDIPET_COMPOSER_TEMPERATURE", 0.1),
                messages=[{"role": "user", "content": prompt}],
            )
            content = extract_text_content(response.content).strip()
            if content:
                return content
        except Exception as ex:
            logger.warning("Response Composer 失败，使用确定性合并: %s", ex)

        # 汇总节点不可用时保留主次标签，避免丢失某个专业 Agent 的结论。
        return "\n\n".join(
            f"{response.content}" if index == 0 else f"补充说明：\n{response.content}"
            for index, response in enumerate(successful)
        )


# ── 编排器 ────────────────────────────────────────────────────────────────────

class AgentOrchestrator:
    """
    多 Agent 编排器。

    路由逻辑（三层）：
      1. 意图 → Agent 类型映射
      2. 同类多实例时按 routing_score() 选最优
      3. 专属 Agent 失败时降级到 GeneralAgent
    """

    # 意图 → Agent 类型的静态映射（路由表）
    _INTENT_ROUTING: Dict[IntentCategory, AgentType] = {
        IntentCategory.HOSPITAL_INFO: AgentType.GENERAL,
        IntentCategory.DEPARTMENT_INFO: AgentType.GENERAL,
        IntentCategory.DOCTOR_INFO: AgentType.GENERAL,
        IntentCategory.GUIDANCE: AgentType.GUIDANCE,
        IntentCategory.VISIT_PREPARATION: AgentType.GUIDANCE,
        IntentCategory.VISIT_PROCESS: AgentType.GUIDANCE,
        IntentCategory.WAYFINDING: AgentType.GUIDANCE,
        IntentCategory.APPOINTMENT: AgentType.APPOINTMENT,
        IntentCategory.SLOT_QUERY: AgentType.APPOINTMENT,
        IntentCategory.APPOINTMENT_CREATE: AgentType.APPOINTMENT,
        IntentCategory.APPOINTMENT_STATUS: AgentType.APPOINTMENT,
        IntentCategory.APPOINTMENT_CANCEL: AgentType.APPOINTMENT,
        IntentCategory.ESCALATION: AgentType.ESCALATION,
        IntentCategory.HUMAN_HANDOFF: AgentType.ESCALATION,
        IntentCategory.EMERGENCY: AgentType.ESCALATION,
        IntentCategory.SYMPTOM_QUERY: AgentType.TRIAGE,
        IntentCategory.REPORT_QUERY: AgentType.TRIAGE,
        IntentCategory.MEDICATION_QUERY: AgentType.MEDICATION,
        # 其余意图 → GENERAL（默认）
    }

    # “准备预约/准备取消”是预约操作；资料须有需要/携带/就诊语义，不匹配裸“资料”。
    _GUIDANCE_KEYWORDS = (
        "带什么", "材料", "就诊准备", "准备什么", "准备哪些",
        "需要的资料", "所需资料", "就诊资料", "携带资料", "需要哪些资料", "带哪些资料",
        "报到", "取号", "流程", "怎么走", "怎么去", "无障碍",
    )
    _APPOINTMENT_KEYWORDS = ("号源", "还有号", "的号", "查号", "预约", "取消", "第一个", "挂号")
    _GENERAL_KEYWORDS = ("医院", "科室", "医生", "介绍", "出诊", "地址", "门诊时间", "咨询", "帮助")
    _TRIAGE_KEYWORDS = ("挂哪个科", "看哪个科", "症状", "咳嗽", "流鼻涕", "腹痛", "肚子疼", "眼睛发红", "眼睛痒", "眼睛发痒", "发热", "发烧", "报告解读", "化验单", "报告数值", "参考区间", "参考范围")
    _MEDICATION_KEYWORDS = ("用药", "药物", "药品", "说明书", "禁忌", "相互作用", "同服", "布洛芬", "对乙酰氨基酚", "华法林", "阿司匹林")

    def __init__(
        self,
        api_key:  str,
        base_url: Optional[str] = None,
        model:    str = "claude-3-5-sonnet-20241022",
        skill_manager: Optional[Any] = None,
        rag_tool_manager: Optional[Any] = None,
        hospital_service: Optional[Any] = None,
        visit_store: Optional[Any] = None,
        intent_embedding_provider: Optional[Any] = None,
    ):
        kwargs: Dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        client = AsyncAnthropic(**kwargs)

        self._intent_recognizer = IntentRecognizer(api_key=api_key, base_url=base_url, model=model,
                                                  embedding_provider=intent_embedding_provider)
        self._skill_manager = skill_manager
        self._composer = ResponseComposer(client, model, skill_manager)
        self._shared_tools: Dict[str, AgentToolSpec] = {}
        self._recent_tool_traces = deque(maxlen=_env_int("MEDIPET_TOOL_TRACE_MAX", 200))

        # Agent 池：每种类型可有多个实例（水平扩展）
        self._pool: Dict[AgentType, List[BaseAgent]] = {
            AgentType.GENERAL: [self._make_agent(GeneralAgent, client, model, skill_manager, hospital_service, visit_store)],
            AgentType.GUIDANCE: [self._make_agent(GuidanceAgent, client, model, skill_manager, hospital_service, visit_store)],
            AgentType.APPOINTMENT: [self._make_agent(AppointmentAgent, client, model, skill_manager, hospital_service, visit_store)],
            AgentType.TRIAGE: [self._make_agent(TriageAgent, client, model, skill_manager, hospital_service, visit_store)],
            AgentType.MEDICATION: [self._make_agent(MedicationAgent, client, model, skill_manager, hospital_service, visit_store)],
            AgentType.ESCALATION: [self._make_agent(EscalationAgent, client, model, skill_manager, hospital_service, visit_store)],
        }
        self.set_shared_tools(build_shared_rag_tools(rag_tool_manager))

    @staticmethod
    def _make_agent(
        agent_cls: type[BaseAgent],
        client: AsyncAnthropic,
        default_model: str,
        skill_manager: Optional[Any],
        hospital_service: Optional[Any] = None,
        visit_store: Optional[Any] = None,
    ) -> BaseAgent:
        """按角色创建 Agent，并允许用环境变量覆盖该角色的模型。

        可使用更强模型，通用接待可使用更快模型，升级节点本身不需要调用 LLM。
        """
        profile = agent_cls.profile
        env_name = f"MEDIPET_{agent_cls.agent_type.value.upper()}_MODEL"
        model = os.getenv(env_name, "").strip() or profile.model
        configured_profile = replace(profile, model=model) if model else profile
        return agent_cls(client, default_model, skill_manager, profile=configured_profile,
                         hospital_service=hospital_service, visit_store=visit_store)

    def set_skill_manager(self, skill_manager: Optional[Any]) -> None:
        """更新 SkillManager 引用，供运行时重载或测试替换使用。"""
        self._skill_manager = skill_manager
        self._composer._skill_manager = skill_manager
        for agents in self._pool.values():
            for agent in agents:
                agent._skill_manager = skill_manager

    def set_shared_tools(self, tools: Optional[Dict[str, AgentToolSpec]]) -> None:
        """更新所有 Agent 共享的工具白名单。"""
        self._shared_tools = dict(tools or {})
        for agents in self._pool.values():
            for agent in agents:
                agent.set_shared_tools(self._shared_tools)

    async def recognize_intent(
        self,
        message: str,
        history: Optional[List[Dict[str, str]]] = None,
    ):
        """对外暴露意图识别，供 API 层先判断是否需要 RAG 等前置能力。"""
        return await self._intent_recognizer.recognize(message, history=history)

    def _record_tool_trace(self, result: OrchestratorResult) -> None:
        trace = {
            "request_id": result.request_id,
            "timestamp": datetime.now().isoformat(),
            "intent": result.intent.value if result.intent else None,
            "primary_agent": result.primary_agent.value if result.primary_agent else None,
            "supporting_agents": [agent.value for agent in result.supporting_agents],
            "tools_used": list(result.tools_used),
            "tool_calls": list(result.tool_traces),
            "escalated": result.escalated,
            "latency_ms": round(result.latency_ms, 1),
        }
        self._recent_tool_traces.append(trace)

    def get_tool_trace(self, request_id: str) -> Optional[Dict[str, Any]]:
        for trace in reversed(self._recent_tool_traces):
            if trace.get("request_id") == request_id:
                return trace
        return None

    def get_recent_tool_traces(self, limit: int = 20) -> List[Dict[str, Any]]:
        if not self._recent_tool_traces:
            return []
        limit = max(1, min(int(limit or 20), len(self._recent_tool_traces)))
        return list(reversed(list(self._recent_tool_traces)[-limit:]))

    # ── 主入口 ────────────────────────────────────────────────────────────────

    async def run(self, req: Request) -> OrchestratorResult:
        """
        处理一次请求的完整流程：
          意图识别 → 路由选 Agent → 执行 → 检查升级 → 返回结果
        """
        t0 = time.monotonic()

        # 固定演示信号优先于预分类输入、意图模型、普通角色与预约工具。
        if detect_emergency(req.message):
            emergency_req = replace(req, intent=IntentCategory.EMERGENCY, intent_group="escalation",
                                    urgency=UrgencyLevel.CRITICAL, intent_confidence=1.0)
            agent = self._best_agent(AgentType.ESCALATION) or EscalationAgent(None, "")
            response = await agent.handle(emergency_req)
            result = OrchestratorResult(
                request_id=req.request_id, response=response.content, agent_type=AgentType.ESCALATION,
                intent=IntentCategory.EMERGENCY, escalated=True, latency_ms=(time.monotonic() - t0) * 1000,
                agent_types=[AgentType.ESCALATION], primary_agent=AgentType.ESCALATION,
                artifacts=response.artifacts, routing_reason="预设急症信号：跳过意图识别、普通角色与预约工具", routing_confidence=1.0,
            )
            self._record_tool_trace(result)
            return result

        # 1. 意图识别（如果调用方已识别则跳过）
        if req.intent is None:
            intent_result = await self._intent_recognizer.recognize(req.message, history=req.history)
            req.intent  = intent_result.intent
            req.intent_group = intent_result.intent_group
            req.urgency = intent_result.urgency
            req.intent_confidence = intent_result.confidence
            req.entities = {key: list(values) for key, values in intent_result.entities.items()}

        if req.intent == IntentCategory.EMERGENCY:
            req = replace(req, intent=IntentCategory.QUERY, intent_group="query", urgency=UrgencyLevel.LOW)
        elif is_emergency_reference(req.message) and req.urgency == UrgencyLevel.CRITICAL:
            req = replace(req, urgency=UrgencyLevel.LOW)

        if self._needs_clarification(req):
            result = OrchestratorResult(
                request_id=req.request_id,
                response="请补充您想了解医院信息、查询或办理预约，还是需要就诊准备、流程或院内文字指引？",
                agent_type=AgentType.GENERAL,
                intent=req.intent,
                escalated=False,
                latency_ms=(time.monotonic() - t0) * 1000,
                agent_types=[AgentType.GENERAL],
                primary_agent=AgentType.GENERAL,
                routing_reason="低置信度 OTHER 意图，先澄清用户需求",
                routing_confidence=req.intent_confidence,
            )
            self._record_tool_trace(result)
            return result

        # 复合需求自动并行协作，例如同时查询号源和就诊准备材料。
        decision = self._route_decision(req)
        if decision.multi_agent:
            return await self.run_parallel(req, decision)

        # 2. 执行主 Agent（含降级）
        response = await self._execute(req, decision.primary_agent)

        # 4. 升级检查
        escalated = False
        if response.escalate or req.urgency == UrgencyLevel.CRITICAL or req.intent in (
            IntentCategory.ESCALATION,
            IntentCategory.HUMAN_HANDOFF,
            IntentCategory.EMERGENCY,
        ):
            escalated = True
            logger.warning(f"请求 {req.request_id} 触发升级: urgency={req.urgency}")
            # 只返回联系/升级标记；不声称已通知真实工作人员。

        result = OrchestratorResult(
            request_id=req.request_id,
            response=response.content,
            agent_type=response.agent_type,
            intent=req.intent,
            escalated=escalated,
            latency_ms=(time.monotonic() - t0) * 1000,
            agent_types=[response.agent_type],
            primary_agent=decision.primary_agent,
            supporting_agents=[],
            tools_used=list(response.tools_used),
            tool_traces=list(response.tool_traces),
            artifacts=list(response.artifacts),
            routing_reason=decision.reason,
            routing_confidence=decision.confidence,
        )
        self._record_tool_trace(result)
        return result

    async def run_parallel(self, req: Request, decision: RoutingDecision) -> OrchestratorResult:
        """
        并行派发给多个 Agent，合并结果。
        适用于复合需求（如同时查询号源和就诊材料）。
        """
        t0 = time.monotonic()
        agent_types = decision.agent_types
        tasks = [self._execute(req, at) for at in agent_types]
        responses = await asyncio.gather(*tasks, return_exceptions=True)

        valid_responses = [r for r in responses if isinstance(r, AgentResponse)]
        composition_error = None
        try:
            combined = await self._composer.compose(req, valid_responses)
        except Exception as exc:
            composition_error = str(exc)
            combined = "\n\n".join(response.content for response in valid_responses if response.success and response.content.strip()) or "抱歉，所有 Agent 均处理失败。"
        escalated = any(isinstance(r, AgentResponse) and r.escalate for r in responses)
        tools_used = list(dict.fromkeys(
            tool_name
            for response in valid_responses
            for tool_name in response.tools_used
        ))
        tool_traces = [
            trace
            for response in valid_responses
            for trace in response.tool_traces
        ]
        if composition_error is not None:
            tool_traces.append({"kind": "composition", "success": False, "error": composition_error})
        for agent_type, response in zip(agent_types, responses):
            if isinstance(response, BaseException):
                tool_traces.append({"kind": "agent_execution", "agent_type": agent_type.value,
                                    "success": False, "error": str(response)})
        artifacts = _merge_artifacts([artifact for response in valid_responses for artifact in response.artifacts], tool_traces)
        result = OrchestratorResult(
            request_id=req.request_id,
            response=combined,
            agent_type=decision.primary_agent,
            intent=req.intent,
            escalated=escalated,
            latency_ms=(time.monotonic() - t0) * 1000,
            agent_types=[
                r.agent_type for r in responses
                if isinstance(r, AgentResponse) and r.success
            ] or agent_types,
            primary_agent=decision.primary_agent,
            supporting_agents=decision.supporting_agents,
            tools_used=tools_used,
            tool_traces=tool_traces,
            artifacts=artifacts,
            routing_reason=decision.reason,
            routing_confidence=decision.confidence,
        )
        self._record_tool_trace(result)
        return result

    # ── 路由逻辑 ──────────────────────────────────────────────────────────────

    def _route(self, intent: Optional[IntentCategory], urgency: Optional[UrgencyLevel]) -> AgentType:
        """
        三层路由决策：
          1. 意图映射
          2. 紧急度覆盖（CRITICAL 直接升级）
          3. 默认 GENERAL
        """
        if urgency == UrgencyLevel.CRITICAL:
            return AgentType.ESCALATION

        if intent and intent in self._INTENT_ROUTING:
            target = self._INTENT_ROUTING[intent]
            # 如果目标类型有可用实例则使用，否则降级
            if target in self._pool and self._pool[target]:
                return target

        return AgentType.GENERAL

    def _route_decision(self, req: Request) -> RoutingDecision:
        """
        结构化路由决策。

        先处理紧急/转人工，再用领域分数决定主 Agent 和辅助 Agent。
        这样可以表达“主任务 + 辅助需求”，避免关键词命中后无主次地拼接。
        """
        if req.urgency == UrgencyLevel.CRITICAL:
            return RoutingDecision(
                primary_agent=AgentType.ESCALATION,
                reason="紧急度为 CRITICAL，触发升级路由",
                confidence=1.0,
            )

        if req.intent in (IntentCategory.ESCALATION, IntentCategory.HUMAN_HANDOFF, IntentCategory.EMERGENCY):
            return RoutingDecision(
                primary_agent=AgentType.ESCALATION,
                reason=f"意图为 {req.intent.value if req.intent else 'unknown'}，触发升级路由",
                confidence=max(req.intent_confidence, 0.8),
            )

        scores = self._domain_scores(req)
        available_scores = {
            agent_type: score
            for agent_type, score in scores.items()
            if agent_type == AgentType.GENERAL or self._pool.get(agent_type)
        }
        if not available_scores:
            return RoutingDecision(
                primary_agent=AgentType.GENERAL,
                reason="无可用专属 Agent，降级到 GeneralAgent",
                confidence=0.1,
            )

        ordered = sorted(available_scores.items(), key=lambda item: item[1], reverse=True)
        primary_agent, primary_score = ordered[0]

        collaboration_targets = self._collaboration_targets(req)
        supporting_agents = [
            agent_type
            for agent_type in collaboration_targets
            if agent_type != primary_agent and agent_type in available_scores
        ]

        if not supporting_agents:
            supporting_agents = [
                agent_type
                for agent_type, score in ordered[1:]
                if agent_type != AgentType.GENERAL
                and score >= 0.45
                and score >= primary_score * 0.55
            ]

        reason = self._routing_reason(req, available_scores, primary_agent, supporting_agents)
        return RoutingDecision(
            primary_agent=primary_agent,
            supporting_agents=supporting_agents,
            reason=reason,
            confidence=round(min(primary_score, 1.0), 3),
        )

    def _domain_scores(self, req: Request) -> Dict[AgentType, float]:
        """按意图、关键词和实体为各领域 Agent 打分。"""
        msg = req.message.lower()
        scores = {
            AgentType.GENERAL: 0.1,
            AgentType.GUIDANCE: 0.0,
            AgentType.APPOINTMENT: 0.0,
            AgentType.TRIAGE: 0.0,
            AgentType.MEDICATION: 0.0,
        }

        intent_target = self._INTENT_ROUTING.get(req.intent, AgentType.GENERAL)
        if intent_target is AgentType.GENERAL:
            scores[AgentType.GENERAL] += 0.55
        elif intent_target in scores:
            scores[intent_target] += 0.75

        guidance_hits = sum(1 for kw in self._GUIDANCE_KEYWORDS if kw in msg)
        appointment_hits = sum(1 for kw in self._APPOINTMENT_KEYWORDS if kw in msg)
        general_hits = sum(1 for kw in self._GENERAL_KEYWORDS if kw in msg)

        scores[AgentType.GUIDANCE] += min(0.45, guidance_hits * 0.18)
        scores[AgentType.APPOINTMENT] += min(0.45, appointment_hits * 0.18)
        scores[AgentType.GENERAL] += min(0.35, general_hits * 0.12)
        scores[AgentType.TRIAGE] += min(0.45, sum(1 for kw in self._TRIAGE_KEYWORDS if kw in msg) * 0.18)
        scores[AgentType.MEDICATION] += min(0.45, sum(1 for kw in self._MEDICATION_KEYWORDS if kw in msg) * 0.18)

        entities = req.entities or {}
        if any(entities.get(key) for key in ("origin", "destination", "accessibility")):
            scores[AgentType.GUIDANCE] += 0.2
        if any(entities.get(key) for key in ("slot_id", "appointment_id", "selection_index")):
            scores[AgentType.APPOINTMENT] += 0.15
        if entities.get("date") or entities.get("period"):
            scores[AgentType.APPOINTMENT] += 0.1
        if entities.get("department") or entities.get("doctor"):
            scores[AgentType.GENERAL] += 0.1

        return {agent_type: round(score, 3) for agent_type, score in scores.items()}

    @staticmethod
    def _routing_reason(
        req: Request,
        scores: Dict[AgentType, float],
        primary_agent: AgentType,
        supporting_agents: List[AgentType],
    ) -> str:
        score_text = ", ".join(
            f"{agent_type.value}={score:.2f}"
            for agent_type, score in sorted(scores.items(), key=lambda item: item[1], reverse=True)
        )
        support_text = ", ".join(agent.value for agent in supporting_agents) or "none"
        intent = req.intent.value if req.intent else "unknown"
        return (
            f"intent={intent}, group={req.intent_group or 'unknown'}, "
            f"primary={primary_agent.value}, supporting={support_text}, scores=[{score_text}]"
        )

    def _collaboration_targets(self, req: Request) -> List[AgentType]:
        """
        判断是否需要多个 Agent 并行协作。

        意图识别通常只返回一个主意图；这里用领域关键词补充检测复合问题，
        例如"查询儿科的号，顺便告诉我要带什么"需要预约和指引角色同时处理。
        """
        msg = req.message.lower()
        targets: List[AgentType] = []

        intent_target = self._INTENT_ROUTING.get(req.intent, AgentType.GENERAL)
        if intent_target is AgentType.GUIDANCE or any(kw in msg for kw in self._GUIDANCE_KEYWORDS):
            targets.append(AgentType.GUIDANCE)
        if intent_target is AgentType.APPOINTMENT or any(kw in msg for kw in self._APPOINTMENT_KEYWORDS):
            targets.append(AgentType.APPOINTMENT)
        if intent_target is AgentType.TRIAGE or any(kw in msg for kw in self._TRIAGE_KEYWORDS):
            targets.append(AgentType.TRIAGE)
        if intent_target is AgentType.MEDICATION or any(kw in msg for kw in self._MEDICATION_KEYWORDS):
            targets.append(AgentType.MEDICATION)

        # 保持顺序去重，并只返回当前有实例的 Agent 类型。
        deduped = list(dict.fromkeys(targets))
        return [agent_type for agent_type in deduped if self._pool.get(agent_type)]

    @staticmethod
    def _needs_clarification(req: Request) -> bool:
        """低置信度且无明确意图时，先追问，避免误路由。"""
        if req.intent != IntentCategory.OTHER:
            return False
        text = (req.message or "").strip()
        if len(text) <= 2:
            return False
        return req.intent_confidence < 0.5

    def _best_agent(self, agent_type: AgentType) -> Optional[BaseAgent]:
        """
        性能路由：从同类 Agent 中选 routing_score() 最高的。
        这是"基于在线表现动态调整路由"的核心。
        """
        agents = self._pool.get(agent_type, [])
        if not agents:
            return None
        return max(agents, key=lambda a: a.stats.routing_score())

    async def _execute(self, req: Request, agent_type: AgentType) -> AgentResponse:
        """执行 Agent，失败时降级到 GeneralAgent。"""
        agent = self._best_agent(agent_type)
        if agent is None:
            agent = self._best_agent(AgentType.GENERAL)
        if agent is None:
            return AgentResponse(
                agent_type=AgentType.GENERAL,
                content="服务暂时不可用，请稍后重试。",
                success=False,
            )

        response = await agent.handle(req)

        # 专属 Agent 失败时降级到 GeneralAgent
        if not response.success and agent_type not in (AgentType.GENERAL, AgentType.ESCALATION):
            logger.warning(f"{agent_type.value} 失败，降级到 GeneralAgent")
            fallback = self._best_agent(AgentType.GENERAL)
            if fallback:
                recovered = await fallback.handle(req)
                traces = [*response.tool_traces, *recovered.tool_traces]
                artifacts = _merge_artifacts([*response.artifacts, *recovered.artifacts], traces)
                response = replace(recovered, tools_used=list(dict.fromkeys([*response.tools_used, *recovered.tools_used])),
                                   tool_traces=traces, artifacts=artifacts)

        return response

    # ── 统计（供 Monitor 读取）────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        result = {}
        for agent_type, agents in self._pool.items():
            for i, agent in enumerate(agents):
                key = f"{agent_type.value}_{i}"
                result[key] = {
                    "total":        agent.stats.total,
                    "success_rate": round(agent.stats.success_rate, 3),
                    "avg_ms":       round(agent.stats.avg_ms, 1),
                    "monitor_penalty": round(agent.stats.monitor_penalty, 3),
                    "routing_score": round(agent.stats.routing_score(), 3),
                    "role": agent.profile.role,
                    "workflow": list(agent.profile.workflow),
                    "tool_scope": list(agent.profile.tool_scope),
                    "available_tools": list(agent.get_tools()),
                    "model": agent._model,
                }
        return result

    def update_routing_penalties(self, penalties: Dict[str, float]) -> None:
        """
        接收 Monitor 的在线表现反馈，动态调整路由惩罚项。

        penalties 的 key 使用 get_stats() 中的 agent key，例如 guidance_0。
        """
        for agent_type, agents in self._pool.items():
            for i, agent in enumerate(agents):
                key = f"{agent_type.value}_{i}"
                penalty = penalties.get(key, 0.0)
                agent.stats.monitor_penalty = min(max(penalty, 0.0), 0.9)
