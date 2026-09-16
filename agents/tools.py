"""角色白名单中的确定性医院工具；只查询或准备方案，执行确认留给页面接口。"""
from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, TYPE_CHECKING, Union

from pydantic import ValidationError
from redis.exceptions import RedisError

from hospital.models import SelectionState, ServiceResult, SlotList, SlotQuery, VisitIdentity
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
                return result.model_dump(mode="json")
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
