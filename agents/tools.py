"""角色白名单中的确定性医院工具；只查询或准备方案，执行确认留给页面接口。"""
from __future__ import annotations

import inspect
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, TYPE_CHECKING, Union

from pydantic import ValidationError
from redis.exceptions import RedisError

from hospital.models import Artifact, SelectionState, ServiceResult, SlotList, SlotQuery, VisitIdentity
from memory.visit_store import VisitStoreError

if TYPE_CHECKING:
    from agents.agent_orchestrator import Request


AgentToolHandler = Callable[["Request", Dict[str, Any]], Union[Any, Awaitable[Any]]]


@dataclass(frozen=True)
class AgentToolSpec:
    """Agent 可见工具的定义和执行函数。"""

    name: str
    description: str
    input_schema: Dict[str, Any]
    handler: AgentToolHandler


def make_tool(
    name: str,
    description: str,
    properties: Dict[str, Any],
    handler: AgentToolHandler,
    required: Optional[List[str]] = None,
) -> AgentToolSpec:
    """创建带 JSON Schema 的 Agent 工具。"""
    return AgentToolSpec(
        name=name,
        description=description,
        input_schema={
            "type": "object",
            "properties": properties,
            "required": required or [],
            "additionalProperties": False,
        },
        handler=handler,
    )


def inspect_request_context(req: Request, args: Dict[str, Any]) -> Dict[str, Any]:
    """只返回服务端请求快照，不接受患者或事项覆盖参数。"""
    return {
        "success": True,
        "data": {
            "intent": req.intent.value if req.intent else None,
            "intent_group": req.intent_group,
            "urgency": req.urgency.name if req.urgency else None,
            "intent_confidence": round(req.intent_confidence, 4),
            "entities": req.entities or {},
            "context_available": bool(req.context),
        },
    }


def _failure(code: str, message: str, *, retryable: bool = False) -> Dict[str, Any]:
    return ServiceResult(success=False, error_code=code, error=message, retryable=retryable).model_dump(mode="json")


def build_hospital_tools(role: str, hospital_service: Any = None, visit_store: Any = None) -> Dict[str, AgentToolSpec]:
    """闭包注入业务依赖。动态事实直接访问医院服务，不经过 MCP 公共结果缓存。"""
    visits = visit_store if visit_store is not None else getattr(hospital_service, "visits", None)

    async def identity(req: Request) -> VisitIdentity:
        if not req.patient_id:
            raise VisitStoreError("missing_fields", "请先选择当前就诊人及事项。")
        if visits is None:
            raise VisitStoreError("storage_unavailable", "事项存储尚未连接。", retryable=True)
        return await visits.require_identity(req.user_id, req.conv_id, patient_id=req.patient_id)

    def selection_query(args: Dict[str, Any], previous: SlotQuery) -> SlotQuery:
        """仅整理查询上下文；未知条件保留上次筛选，不猜内部 ID。"""
        filters = {}
        for name, id_field, collection in (
            ("department", "department_id", "departments"),
            ("doctor", "doctor_id", "doctors"),
        ):
            value = args.get(name)
            if value:
                item = next((item for item in getattr(hospital_service.data, collection)
                             if value in (getattr(item, id_field), item.name)), None)
                if item is None:
                    return previous
                filters[id_field] = getattr(item, id_field)
        try:
            return SlotQuery(
                **filters,
                date=hospital_service.resolve_date(args["date"]) if args.get("date") is not None else None,
                period=args.get("period"),
            )
        except (ValidationError, ValueError, TypeError):
            return previous

    def handler_for(method: str, *, personal: bool = False):
        async def handler(req: Request, args: Dict[str, Any]):
            if hospital_service is None:
                return _failure("storage_unavailable", "医院服务尚未初始化。", retryable=True)
            try:
                if method == "search_slots":
                    from agents.task_requirements import declines_slot_query
                    if declines_slot_query(req.message):
                        return _failure("invalid_input", "本轮明确不执行号源查询，可仅说明查询流程。")
                effective_input = None
                if method == "get_visit_checklist":
                    from agents.task_requirements import explicit_visit_type
                    visit_type = explicit_visit_type(req.message)
                    if visit_type:
                        args = {**args, "visit_type": visit_type}
                        effective_input = dict(args)
                bound_identity = await identity(req) if personal else None
                # 先使旧列表失效；查询或保存异常均不得继续使用上一轮序号。
                if method == "search_slots":
                    previous = await visits.get_selection(bound_identity.user_id, bound_identity.conv_id)
                    await visits.save_selection(SelectionState(
                        **bound_identity.model_dump(), status="failed", query=selection_query(args, previous.query),
                    ))
                if method == "prepare_appointment" and not args:
                    result = hospital_service.get_current_proposal(bound_identity)
                else:
                    target = getattr(hospital_service, method)
                    result = target(bound_identity, **args) if personal and method != "search_slots" else target(**args)
                if inspect.isawaitable(result):
                    result = await result
                if method == "search_slots" and (result.success or result.error_code == "no_slots"):
                    listing = SlotList.model_validate(result.data)
                    await visits.save_selection(SelectionState(
                        **bound_identity.model_dump(), status="ready" if listing.slots else "empty",
                        list_id=listing.list_id if listing.slots else None, query=listing.query,
                        slots=listing.slots, queried_at=listing.queried_at,
                    ))
                payload = result.model_dump(mode="json")
                if effective_input is not None:
                    payload["effective_input"] = effective_input
                return payload
            except VisitStoreError as exc:
                return _failure(exc.code, exc.message, retryable=exc.retryable)
            except RedisError:
                return _failure("storage_unavailable", "医院或事项存储暂时不可用，请稍后重试。", retryable=True)
            except (ValidationError, ValueError, TypeError):
                return _failure("invalid_input", "工具输入或业务返回不符合当前医院契约。")
        return handler

    text = lambda description: {"type": "string", "description": description}
    if role == "general":
        return {
            "inspect_request_context": make_tool("inspect_request_context", "查看当前请求的意图、实体和上下文，不接受身份参数。", {}, inspect_request_context),
            "query_hospital_catalog": make_tool("query_hospital_catalog", "查询医院、科室、医生及地点的真实公开目录。", {
                "category": {**text("目录类别"), "enum": ["hospital", "department", "doctor", "location"]},
                "department": text("真实科室名称或 ID"), "doctor": text("真实医生姓名或 ID"),
                "date": text("排班日期 YYYY-MM-DD、今天、明天或后天"),
            }, handler_for("query_hospital_catalog")),
        }
    if role == "guidance":
        return {
            "get_visit_checklist": make_tool("get_visit_checklist", "读取预置就诊材料清单及来源。", {
                "department": text("真实科室名称或 ID"),
                "visit_type": {**text("一般、首次或儿童就诊"), "enum": ["general", "first", "child"]},
            }, handler_for("get_visit_checklist")),
            "get_wayfinding": make_tool("get_wayfinding", "读取两个已知地点之间的预置文字指引，不计算实时导航。", {
                "origin": text("起点完整名称或 ID"), "destination": text("目的地完整名称或 ID"),
                "mode": {**text("普通或无障碍路线"), "enum": ["normal", "accessible"]},
            }, handler_for("get_wayfinding"), required=["origin", "destination"]),
        }
    if role == "appointment":
        return {
            "search_slots": make_tool("search_slots", "查询实时号源，将有序结果保存到当前事项；序号只引用这次列表。", {
                "department": text("真实科室名称或 ID"), "doctor": text("真实医生姓名或 ID"),
                "date": text("YYYY-MM-DD、今天、明天或后天"),
                "period": {**text("上午或下午"), "enum": ["morning", "afternoon"]},
            }, handler_for("search_slots", personal=True)),
            "list_appointments": make_tool("list_appointments", "查询当前患者的真实预约记录，不接受身份覆盖。", {
                "status": {**text("预约状态；不填查询全部"), "enum": ["active", "cancelled"]},
            }, handler_for("get_appointments", personal=True)),
            "prepare_appointment": make_tool("prepare_appointment", "只准备待确认预约方案，不扣号；slot_id 与 selection_index 二选一。聊天确认时留空参数读取当前方案，提示在页面确认。", {
                "slot_id": text("实际号源 ID"), "selection_index": {"type": "integer", "minimum": 1, "description": "最近真实号源列表的一基序号"},
            }, handler_for("prepare_appointment", personal=True)),
            "prepare_cancellation": make_tool("prepare_cancellation", "只准备当前患者的取消方案，不取消预约、不释放号源。", {
                "appointment_id": text("实际预约记录 ID"),
            }, handler_for("prepare_cancellation", personal=True), required=["appointment_id"]),
        }
    return {}


def build_health_tools(role: str, visit_store: Any = None) -> Dict[str, AgentToolSpec]:
    """健康信息只读工具；症状和报告直接取用户原文，不采用模型编造的数值。"""
    def result(kind, model):
        data = model.model_dump(mode="json")
        artifact = Artifact(id=f"{kind}:{uuid.uuid4().hex}", type=kind, data=data)
        return ServiceResult(success=True, data=data, artifacts=[artifact]).model_dump(mode="json")

    def symptom_handler(req, args):
        from health.triage import triage_symptoms
        return result("triage_guidance", triage_symptoms(req.message))

    def report_handler(req, args):
        from health.reports import preprocess_report
        return result("report_summary", preprocess_report(req.message))

    async def read_report_handler(req, args):
        from health.models import ReportSummary
        if visit_store is None:
            return _failure("storage_unavailable", "事项记录尚未就绪。", retryable=True)
        if not req.patient_id:
            return _failure("missing_fields", "请先选择就诊人和事项。")
        try:
            identity = await visit_store.require_identity(req.user_id, req.conv_id, patient_id=req.patient_id)
            messages = await visit_store.get_messages(identity.user_id, identity.conv_id)
            for message in reversed(messages):
                for artifact in reversed(message.artifacts):
                    if artifact.type == "report_summary":
                        return result("report_summary", ReportSummary.model_validate(artifact.data))
            return _failure("not_found", "当前事项没有已整理的报告，请上传文件或粘贴报告文字。")
        except VisitStoreError as exc:
            return _failure(exc.code, exc.message, retryable=exc.retryable)
        except RedisError:
            return _failure("storage_unavailable", "报告记录暂时不可用，请稍后重试。", retryable=True)

    def medication_handler(req, args):
        import re

        from health.medications import medication_information
        name = args.get("drug_name", "")
        others = args.get("other_drugs", [])
        if (not isinstance(name, str) or not name.strip() or not isinstance(others, list)
                or any(not isinstance(item, str) or not item.strip() for item in others)):
            return _failure("invalid_input", "请提供用户明确提及的药名及剂型。")
        # 查询参数仅来自当前消息或同事项最近的用户原话，不能从模型回复里补出药物。
        user_text = "\n".join([req.message, *[
            str(item.get("content", "")) for item in (req.history or [])[-5:]
            if item.get("role") == "user"
        ]]).casefold()
        if any(item.strip().casefold() not in user_text for item in [name, *others]):
            return _failure("invalid_input", "药名必须来自用户原话；请核对实际药名、规格与剂型。")
        # 字面子串仍可能删去限定：例如把“布洛芬缓释胶囊”截成“布洛芬”。
        # 对紧邻药名的明确剂型/规格要求完整保留，宁可澄清也不套用普通片标签。
        qualifier = (r"(?:缓释|控释|复方|肠溶|分散|咀嚼|泡腾|普通|包衣|薄膜衣|儿童|小儿|"
                     r"胶囊|混悬液|混悬剂|口服液|滴剂|颗粒|糖浆|注射液|注射剂|栓剂|片剂|片|"
                     r"extended[ -]?release|sustained[ -]?release|delayed[ -]?release|"
                     r"tablets?|capsules?|suspension|\d+(?:\.\d+)?\s*(?:毫克|微克|克|mg|mcg|g|ml|毫升))")
        omitted_suffix = re.compile(r"^[ \t（）()]*(?:的|是|为|[，,]?[ \t]*(?:规格|剂型)(?:为|是)?[:：]?)?[ \t（）()]*" + qualifier)
        omitted_prefix = re.compile(r"(?:缓释|控释|复方|肠溶|分散|咀嚼|泡腾|儿童|小儿|"
                                    r"extended[ -]?release|sustained[ -]?release|delayed[ -]?release)[ \t（）()]*$")
        for item in [name, *others]:
            for match in re.finditer(re.escape(item.strip().casefold()), user_text):
                if omitted_prefix.search(user_text[:match.start()]) or omitted_suffix.search(user_text[match.end():]):
                    return _failure("invalid_input", "药名参数省略了原文中的剂型或规格，请完整保留药盒名称后查询；不能套用普通片标签。")
        return result("medication_info", medication_information(name.strip(), other_drugs=others))

    if role == "triage":
        return {
            "triage_symptoms": make_tool("triage_symptoms", "根据当前用户原文提供有限的初步科室建议与待补信息，不作诊断。", {}, symptom_handler),
            "preprocess_report": make_tool("preprocess_report", "整理当前用户粘贴报告的项目、数值、单位和原文参考范围，不添加数值或作诊断。文件请使用页面报告上传入口。", {}, report_handler),
            "read_current_report": make_tool("read_current_report", "读取当前患者当前事项最近一次已经整理的报告，用于上传或粘贴后的追问；不跨患者或事项查找。", {}, read_report_handler),
        }
    if role == "medication":
        return {"medication_information": make_tool("medication_information", "查询已收录明确剂型的药品说明书及组合警示；未知药物或组合不代表安全。药名逐字取自用户原话，不替用户选药。", {
            "drug_name": {"type": "string", "description": "用户明确给出的药名，可包含规格与剂型；保持原文"},
            "other_drugs": {"type": "array", "items": {"type": "string"}, "description": "用户明确提及的其他药名；未知相互作用须咨询药师"},
        }, medication_handler, required=["drug_name"])}
    return {}


def build_shared_rag_tools(tool_manager: Any) -> Dict[str, AgentToolSpec]:
    """医院静态知识共享检索；角色是否开放仍由 Agent 白名单决定。"""
    async def search_knowledge_base(req: Request, args: Dict[str, Any]) -> Dict[str, Any]:
        query = args.get("query", req.message)
        top_k = args.get("top_k", 5)
        if not isinstance(query, str) or not query.strip() or type(top_k) is not int or top_k <= 0:
            return {"success": False, "error_code": "invalid_input", "error": "query 不能为空，top_k 必须是正整数", "results": []}
        if tool_manager is None:
            return {"success": False, "error_code": "retrieval_failed", "error": "RAG 工具未初始化", "results": []}
        result = await tool_manager.search_with_rewrite("knowledge_search", query.strip(), top_k=top_k)
        success = bool(result.success)
        return {
            "success": success, "query": query.strip(), "top_k": top_k,
            "results": result.data if success else [], "error": getattr(result, "error", None),
            "error_code": getattr(result, "error_code", None),
            "reranked": bool(getattr(result, "reranked", False)),
            "cached": bool(getattr(result, "cached", False)),
            "rewrite_error": getattr(result, "rewrite_error", None),
            "rerank_error": getattr(result, "rerank_error", None),
            "recall_errors": getattr(result, "recall_errors", []),
            "partial": bool(getattr(result, "partial", False)),
            "fallback_used": bool(getattr(result, "fallback_used", False)),
        }
    return {"search_knowledge_base": make_tool(
        "search_knowledge_base", "检索医院静态流程与准备材料，返回片段和来源；实时号源和个人预约必须使用业务工具。",
        {"query": {"type": "string", "description": "问题或关键词"},
         "top_k": {"type": "integer", "minimum": 1, "description": "返回条数"}},
        search_knowledge_base, required=["query"],
    )}
