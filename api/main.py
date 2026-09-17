"""
MediPet 门诊就诊助手 — FastAPI 入口

启动时打印小熊饼干图案。
所有核心组件在 lifespan 中初始化，通过环境变量配置。
"""
import asyncio
import logging
import os
import pathlib
import sys
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Any, Dict, List, Optional


_ROOT = str(pathlib.Path(__file__).parent.parent.resolve())
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import uvicorn
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Response, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from redis.exceptions import RedisError
from hospital.models import (Artifact, BusinessError, ConfirmRequest, ConfirmResponse, ContractModel,
                             DEFAULT_USER_ID, ExecutionReceipt, Identifier, Text, Visit, VisitIdentity, VisitMessage)
from core.emergency import detect_emergency
from hospital.service import HospitalService
from hospital.store import HospitalStore
from memory.visit_store import VisitStore, VisitStoreError
from health.report_upload import MAX_FILE_BYTES, ReportUploadError, process_report_file

load_dotenv()

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO")),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

BANNER = r"""
    ʕ•ᴥ•ʔ  ʕ•ᴥ•ʔ  ʕ•ᴥ•ʔ
   ╔══════════════════════╗
   ║   MediPet   v2.0     ║
   ║   门诊就诊助手       ║
   ╚══════════════════════╝
    ʕ•ᴥ•ʔ  ʕ•ᴥ•ʔ  ʕ•ᴥ•ʔ
"""

# ── 全局组件（lifespan 中初始化）─────────────────────────────────────────────
_orchestrator = None
_memory       = None
_tool_manager = None
_monitor      = None
_evaluator    = None
_skill_manager = None
_business_redis = None
_visit_store = None
_hospital_service = None
_intent_embeddings = None

def _anthropic_cfg() -> Dict[str, Any]:
    key = os.getenv("ANTHROPIC_API_KEY", "")
    if not key:
        raise RuntimeError("未设置 ANTHROPIC_API_KEY")
    cfg: Dict[str, Any] = {
        "api_key":  key,
        "model":    os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022").strip(),
    }
    base_url = os.getenv("ANTHROPIC_BASE_URL", "").strip()
    if base_url:
        cfg["base_url"] = base_url
    return cfg


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _orchestrator, _memory, _tool_manager, _monitor, _evaluator, _skill_manager
    global _business_redis, _visit_store, _hospital_service, _intent_embeddings

    print(BANNER, flush=True)

    from agents.agent_orchestrator import AgentOrchestrator, Request, build_shared_rag_tools
    from core.intent_recognizer import IntentRecognizer
    from core.intent_embeddings import IntentEmbeddingProvider
    from evaluation.evaluator import EndToEndEvaluator
    from api.evaluation_runtime import build_case_runtime_factory
    from mcp.knowledge_base import KnowledgeBase
    from mcp.tool_manager import MCPToolManager, Tool
    from memory.conversation_memory import MemoryManager
    from monitor.performance_monitor import PerformanceMonitor
    from core.skill_loader import SkillManager

    cfg = _anthropic_cfg()
    _intent_embeddings = IntentEmbeddingProvider()
    await _intent_embeddings.initialize()
    logger.info(f"模型: {cfg['model']}  base_url: {cfg.get('base_url', '(官方)')}")

    _business_redis = Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"), decode_responses=True)
    _visit_store = VisitStore(_business_redis)
    _hospital_service = HospitalService(store=HospitalStore(_business_redis), visit_store=_visit_store)
    await _visit_store.initialize_patients(_hospital_service.data.patients)
    initialized = await _hospital_service.initialize_slots()
    if not initialized.success:
        raise RuntimeError(initialized.error)

    # 意图识别器（Orchestrator 内部也会创建，这里单独暴露给 Evaluator）
    recognizer = IntentRecognizer(
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model=cfg["model"],
        embedding_provider=_intent_embeddings,
    )

    # Skills：启动时从目录加载业务能力说明，并在 Agent 调用 LLM 时动态注入。
    skills_dir = os.getenv("MEDIPET_SKILLS_DIR", str(pathlib.Path(_ROOT) / "skills"))
    _skill_manager = SkillManager(
        root_dir=skills_dir,
        max_prompt_chars=int(os.getenv("MEDIPET_SKILLS_MAX_PROMPT_CHARS", "5000")),
    )
    _skill_manager.load()

    # Agent 编排器
    _orchestrator = AgentOrchestrator(
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model=cfg["model"],
        skill_manager=_skill_manager,
        hospital_service=_hospital_service,
        visit_store=_visit_store,
        intent_embedding_provider=_intent_embeddings,
    )

    # 记忆管理器（Redis 工作记忆 + ChromaDB 情景记忆/用户画像）
    _memory = MemoryManager(
        redis_client=_business_redis,
        visit_store=_visit_store,
        redis_url=os.getenv("REDIS_URL", "redis://redis:6379/0"),
        chroma_host=os.getenv("CHROMA_HOST", "chromadb"),
        chroma_port=int(os.getenv("CHROMA_PORT", "8000")),
        chroma_path=os.getenv("CHROMA_PERSIST_DIRECTORY", "/app/data/chroma"),
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model=cfg["model"],
    )

    # MCP 工具管理器 + RAG 知识库（基于 ChromaDB 的真实检索）
    _tool_manager = MCPToolManager(
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model=cfg["model"],
    )
    kb = KnowledgeBase(
        chroma_host=os.getenv("CHROMA_HOST", "chromadb"),
        chroma_port=int(os.getenv("CHROMA_PORT", "8000")),
        chroma_path=os.getenv("CHROMA_PERSIST_DIRECTORY", "/app/data/chroma"),
    )
    logger.info(f"知识库已加载: {await kb.doc_count_async()} 个文档片段")

    def knowledge_fallback(params: Dict[str, Any], context: Optional[Dict[str, Any]], error: str):
        return []

    _tool_manager.register(Tool(
        name="knowledge_search",
        description="搜索知识库（基于 ChromaDB 向量检索）",
        handler=kb.search_handler,
        schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "top_k": {"type": "integer"},
            },
            "required": ["query"],
        },
        cache_ttl=300.0,
        supports_rerank=True,
        fallback=knowledge_fallback,
    ))
    if _orchestrator is not None:
        _orchestrator.set_shared_tools(build_shared_rag_tools(_tool_manager))

    # 性能监控（可选启动 Prometheus）
    prom_port = int(os.getenv("PROMETHEUS_PORT", "0")) or None
    _monitor = PerformanceMonitor(
        orchestrator=_orchestrator,
        tool_manager=_tool_manager,
        interval_s=float(os.getenv("MONITOR_INTERVAL", "10")),
        webhook_url=os.getenv("ALERT_WEBHOOK_URL") or None,
        prometheus_port=prom_port,
    )
    await _monitor.start()

    # 评测器
    _evaluator = EndToEndEvaluator(
        orchestrator=_orchestrator,
        recognizer=recognizer,
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model=cfg["model"],
        baseline_path=os.getenv("EVAL_BASELINE_PATH", "/app/data/eval/baseline.json"),
        case_runtime_factory=build_case_runtime_factory(config=cfg, redis_client=_business_redis,
                                                        chroma_client=kb._client, knowledge=kb, skill_manager=_skill_manager,
                                                        intent_embedding_provider=_intent_embeddings),
    )

    logger.info("MediPet 已就绪")
    yield

    await _monitor.stop()
    await _intent_embeddings.aclose()
    if _memory is not None:
        await _memory.close()
    if _business_redis is not None:
        await _business_redis.aclose()
    logger.info("MediPet 已关闭")


# ── FastAPI ───────────────────────────────────────────────────────────────────
app = FastAPI(
    title="MediPet 门诊就诊助手",
    version="2.0.0",
    lifespan=lifespan,
    docs_url="/docs",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── 请求/响应模型 ─────────────────────────────────────────────────────────────
class CreateVisitRequest(ContractModel):
    user_id: Identifier = DEFAULT_USER_ID
    patient_id: Identifier
    title: Text | None = None


class UpdateVisitRequest(ContractModel):
    user_id: Identifier = DEFAULT_USER_ID
    title: Text | None = None
    archived: bool | None = Field(default=None, strict=True)


class ChatRequest(ContractModel):
    message: Text
    user_id: Identifier = DEFAULT_USER_ID
    conv_id: Identifier | None = None
    patient_id: Identifier | None = None


class ChatResponse(BaseModel):
    conv_id:     str
    patient_id: str
    visit: Visit
    artifacts: List[Artifact] = Field(default_factory=list)
    tool_traces: List[Dict[str, Any]] = Field(default_factory=list)
    request_id:  str = ""
    response:    str
    intent:      str
    intent_group: str = "other"
    agent_type:  str
    agent_types: List[str] = Field(default_factory=list)
    primary_agent: str = ""
    supporting_agents: List[str] = Field(default_factory=list)
    tools_used: List[str] = Field(default_factory=list)
    routing_reason: str = ""
    routing_confidence: float = 0.0
    escalated:   bool
    latency_ms:  float
    knowledge_used: bool = False
    entities: Dict[str, List[str]] = Field(default_factory=dict)
    intent_confidence: float = 0.0
    intent_source_scores: Dict[str, float] = Field(default_factory=dict)
    intent_embedding: Dict[str, Any] = Field(default_factory=dict)


class ToolTraceResponse(BaseModel):
    request_id: str
    found: bool
    trace: Dict[str, Any] = Field(default_factory=dict)


class RecentToolTracesResponse(BaseModel):
    items: List[Dict[str, Any]] = Field(default_factory=list)


# ── 路由 ──────────────────────────────────────────────────────────────────────
def _business_http(error: VisitStoreError) -> HTTPException:
    status = {"not_found": 404, "identity_conflict": 403, "missing_fields": 422, "invalid_input": 422,
              "proposal_expired": 410, "storage_unavailable": 503, "artifact_conflict": 500}.get(error.code, 409)
    return HTTPException(status, detail=BusinessError(code=error.code, message=error.message, retryable=error.retryable).model_dump())


def _visits_ready() -> VisitStore:
    if _visit_store is None:
        raise _business_http(VisitStoreError("storage_unavailable", "事项存储尚未就绪。", retryable=True))
    return _visit_store


async def _load_visit(user_id: str, conv_id: str | None = None, patient_id: str | None = None) -> Visit:
    """聊天/确认共用：已有事项必须存在且绑定患者一致，无事项时创建指定或默认本人事项。"""
    visits = _visits_ready()
    try:
        if conv_id is not None:
            await visits.require_identity(user_id, conv_id, patient_id=patient_id)
            return await visits.get_visit(user_id, conv_id)
        if patient_id is None:
            patient_id = (await visits.default_patient(user_id)).patient_id
        return await visits.create_visit(user_id, patient_id)
    except VisitStoreError as exc:
        raise _business_http(exc) from exc


@app.get("/patients", tags=["就诊事项"])
async def patients(user_id: Identifier = DEFAULT_USER_ID):
    try:
        return {"items": await _visits_ready().list_patients(user_id)}
    except VisitStoreError as exc:
        raise _business_http(exc) from exc


@app.get("/visits", tags=["就诊事项"])
async def visits(patient_id: Identifier, user_id: Identifier = DEFAULT_USER_ID, archived: bool = False):
    try:
        return {"items": await _visits_ready().list_visits(user_id, patient_id, archived=archived)}
    except VisitStoreError as exc:
        raise _business_http(exc) from exc


@app.post("/visits", response_model=Visit, status_code=201, tags=["就诊事项"])
async def create_visit(req: CreateVisitRequest):
    try:
        return await _visits_ready().create_visit(req.user_id, req.patient_id, req.title)
    except VisitStoreError as exc:
        raise _business_http(exc) from exc


@app.patch("/visits/{conv_id}", response_model=Visit, tags=["就诊事项"])
async def update_visit(conv_id: Identifier, req: UpdateVisitRequest):
    try:
        return await _visits_ready().update_visit(req.user_id, conv_id, title=req.title, archived=req.archived)
    except VisitStoreError as exc:
        raise _business_http(exc) from exc


@app.get("/visits/{conv_id}/messages", tags=["就诊事项"])
async def visit_messages(conv_id: Identifier, user_id: Identifier = DEFAULT_USER_ID):
    store = _visits_ready()
    try:
        return {"visit": await store.get_visit(user_id, conv_id), "items": await store.get_messages(user_id, conv_id)}
    except VisitStoreError as exc:
        raise _business_http(exc) from exc


@app.get("/health")
async def health():
    if _orchestrator is None:
        raise HTTPException(503, "服务未就绪")
    return {"status": "ok", "agents": _orchestrator.get_stats(),
            "intent_embedding": _intent_embeddings.status() if _intent_embeddings is not None else {"status": "uninitialized"}}


@app.get("/skills", tags=["Skills"])
async def skills_summary():
    """查看当前已加载的 Skills，便于确认热加载结果和排查解析错误。"""
    if _skill_manager is None:
        raise HTTPException(503, "Skills 未初始化")
    return _skill_manager.summary()


@app.post("/skills/reload", tags=["Skills"])
async def reload_skills():
    """运行时重新扫描 Skill 目录，不需要重启服务。"""
    if _skill_manager is None:
        raise HTTPException(503, "Skills 未初始化")
    _skill_manager.reload()
    if _orchestrator is not None:
        _orchestrator.set_skill_manager(_skill_manager)
    return _skill_manager.summary()


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, background_tasks: BackgroundTasks):
    """
    主对话接口。完整流程：
      记忆读取 → 意图识别 → Agent 路由 → 执行 → 记忆写入
    """
    if _orchestrator is None or _memory is None:
        raise HTTPException(503, "服务未就绪")

    from agents.agent_orchestrator import Request as OrcReq
    from memory.conversation_memory import MsgRole
    from core.intent_recognizer import IntentCategory, IntentResult, UrgencyLevel

    visit = await _load_visit(req.user_id, req.conv_id, req.patient_id)
    identity = VisitIdentity(user_id=visit.user_id, patient_id=visit.patient_id, conv_id=visit.conv_id)
    emergency = detect_emergency(req.message)
    history, full_context = None, ""
    if emergency:
        intent_result = IntentResult(IntentCategory.EMERGENCY, 1.0, UrgencyLevel.CRITICAL,
                                     "escalation", {}, "预设急症信号", 0.0)
    else:
        try:
            mem_ctx = await _memory.get_context(identity, query=req.message)
        except VisitStoreError as exc:
            raise _business_http(exc) from exc
        history = [{"role": m.role.value, "content": m.content} for m in mem_ctx.recent_messages[-5:]] or None
        full_context = mem_ctx.to_prompt_text()
        intent_result = await _orchestrator.recognize_intent(req.message, history=history)

    result = await _orchestrator.run(OrcReq(
        message=req.message, **identity.model_dump(), context=full_context, history=history,
        entities=intent_result.entities, intent=intent_result.intent, intent_group=intent_result.intent_group,
        urgency=intent_result.urgency, intent_confidence=intent_result.confidence,
    ))
    response = ChatResponse(
        conv_id=visit.conv_id,
        patient_id=visit.patient_id,
        visit=visit,
        artifacts=result.artifacts,
        tool_traces=result.tool_traces,
        request_id=result.request_id,
        response=result.response,
        intent=result.intent.value if result.intent else "other",
        intent_group=intent_result.intent_group,
        agent_type=result.agent_type.value,
        agent_types=[agent_type.value for agent_type in result.agent_types],
        primary_agent=result.primary_agent.value if result.primary_agent else result.agent_type.value,
        supporting_agents=[agent_type.value for agent_type in result.supporting_agents],
        tools_used=result.tools_used,
        routing_reason=result.routing_reason,
        routing_confidence=result.routing_confidence,
        escalated=result.escalated,
        latency_ms=round(result.latency_ms, 1),
        knowledge_used=any(trace.get("tool_name", trace.get("tool")) == "search_knowledge_base"
                           and trace.get("success") for trace in result.tool_traces),
        entities=intent_result.entities,
        intent_confidence=round(intent_result.confidence, 4),
        intent_source_scores=intent_result.source_scores,
        intent_embedding=intent_result.embedding_info,
    )
    # 完整历史先落盘；摘要和工作窗口都不是业务记录的来源。
    metadata = response.model_dump(mode="json", exclude={"visit", "artifacts", "response", "conv_id", "patient_id"})
    message_id = uuid.uuid4().hex
    now = _visits_ready().now()
    messages = [
        VisitMessage(**identity.model_dump(), message_id=f"user:{message_id}", role="user", content=req.message, created_at=now),
        VisitMessage(**identity.model_dump(), message_id=f"assistant:{message_id}", role="assistant", content=result.response,
                     artifacts=result.artifacts, metadata=metadata, created_at=now),
    ]
    try:
        await _visits_ready().append_messages(identity.user_id, identity.conv_id, messages)
        response.visit = await _visits_ready().get_visit(identity.user_id, identity.conv_id)
    except VisitStoreError as exc:
        raise _business_http(exc) from exc
    # 急症只追加窗口，不触发压缩/画像模型；完整历史与窗口都保留此轮。
    if emergency:
        await _memory.add_message(identity, MsgRole.USER, req.message, compress=False)
        await _memory.add_message(identity, MsgRole.ASSISTANT, result.response,
                                  metadata={"artifacts": [a.model_dump(mode="json") for a in result.artifacts]}, compress=False)
    else:
        await _memory.add_message(identity, MsgRole.USER, req.message)
        await _memory.add_message(identity, MsgRole.ASSISTANT, result.response,
                                  metadata={"artifacts": [a.model_dump(mode="json") for a in result.artifacts]})
        background_tasks.add_task(_memory.update_profile, identity)
    return response


@app.post("/reports/preprocess", tags=["报告"])
async def preprocess_uploaded_report(
    file: UploadFile = File(...), user_id: Identifier = Form(DEFAULT_USER_ID),
    patient_id: Identifier | None = Form(None), conv_id: Identifier | None = Form(None),
):
    """Local extraction only; retain derived text/cards in the bound visit, not original bytes."""
    try:
        visit = await _load_visit(user_id, conv_id, patient_id)
        identity = VisitIdentity(user_id=visit.user_id, patient_id=visit.patient_id, conv_id=visit.conv_id)
        content = await file.read(MAX_FILE_BYTES + 1)
        report = await process_report_file(content, file.filename or "", file.content_type)
        artifact = Artifact(id=f"report_summary:{uuid.uuid4().hex}", type="report_summary", data=report.model_dump(mode="json"))
        message_id, now = uuid.uuid4().hex, _visits_ready().now()
        filename = (file.filename or "报告").replace("\\", "/").split("/")[-1][:120]
        messages = [
            VisitMessage(**identity.model_dump(), message_id=f"user:{message_id}", role="user",
                         content=f"上传报告：{filename}", created_at=now),
            VisitMessage(**identity.model_dump(), message_id=f"assistant:{message_id}", role="assistant",
                         content=report.summary, artifacts=[artifact],
                         metadata={"processing": "report_preprocessor", "report_input_kind": report.input_kind}, created_at=now),
        ]
        # Invalidate only working memory. The authoritative history and slot selection stay intact.
        if _memory is not None:
            await _memory.invalidate_working_memory(identity)
        await _visits_ready().append_messages(identity.user_id, identity.conv_id, messages)
        return {**identity.model_dump(), "visit": await _visits_ready().get_visit(identity.user_id, identity.conv_id),
                "response": report.summary, "artifacts": [artifact], "extracted_text": report.extracted_text,
                "input_kind": report.input_kind}
    except ReportUploadError as exc:
        raise HTTPException(exc.status, detail={"code": exc.code, "message": exc.message, "retryable": exc.status in {408, 503}}) from exc
    except VisitStoreError as exc:
        raise _business_http(exc) from exc
    except RedisError as exc:
        raise _business_http(VisitStoreError("storage_unavailable", "报告记录暂时无法保存，请稍后重试。", retryable=True)) from exc
    finally:
        await file.close()


@app.post("/appointment-proposals/{proposal_id}/confirm", response_model=ConfirmResponse, tags=["预约"])
async def confirm_appointment(proposal_id: Identifier, req: ConfirmRequest):
    visit = await _load_visit(req.user_id, req.conv_id)
    identity = VisitIdentity(user_id=visit.user_id, patient_id=visit.patient_id, conv_id=visit.conv_id)
    if _hospital_service is None:
        raise _business_http(VisitStoreError("storage_unavailable", "预约服务尚未就绪。", retryable=True))
    result = await _hospital_service.confirm_proposal(identity, proposal_id)
    if not result.success:
        raise _business_http(VisitStoreError(result.error_code, result.error, retryable=result.retryable))
    receipt = ExecutionReceipt.model_validate(result.data)
    action = "预约" if receipt.operation == "create" else "取消预约"
    common = {**identity.model_dump(), "created_at": receipt.executed_at,
              "proposal_id": receipt.proposal_id, "receipt_id": receipt.receipt_id}
    messages = [
        VisitMessage(**common, message_id=f"confirm:{receipt.receipt_id}", role="user", kind="confirmation_event",
                     content=f"已明确确认{action}资料。"),
        VisitMessage(**common, message_id=f"result:{receipt.receipt_id}", role="assistant", kind="operation_result",
                     content=f"{action}已完成。", artifacts=result.artifacts, metadata={"receipt": receipt.model_dump(mode="json")}),
    ]
    try:
        # 执行后历史暂不可用时允许重试；服务重放原回执，稳定 message_id 防止重复历史。
        await _visits_ready().append_messages(identity.user_id, identity.conv_id, messages)
        if _memory is not None:
            await _memory.invalidate_working_memory(identity)
    except VisitStoreError as exc:
        raise _business_http(exc) from exc
    except RedisError as exc:
        raise _business_http(VisitStoreError("storage_unavailable", "回执已保存，上下文暂未刷新，请重试。", retryable=True)) from exc
    return ConfirmResponse(**identity.model_dump(), receipt=receipt, artifacts=result.artifacts)


async def _build_knowledge_context(message: str, intent=None, top_k: int = 3) -> tuple[str, bool]:
    """
    为 /chat 主链路构建 RAG 知识上下文。

    这里复用 MCPToolManager 的查询改写、并行召回、重排、fallback 能力。
    """
    if _tool_manager is None:
        return "", False
    if not _should_use_knowledge(message, intent=intent):
        return "", False
    try:
        result = await _tool_manager.search_with_rewrite("knowledge_search", message, top_k=top_k)
        if not result.success or not isinstance(result.data, list) or not result.data:
            return "", False

        parts = ["[知识库检索结果]"]
        used = False
        for i, item in enumerate(result.data[:top_k], start=1):
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", "未命名文档"))
            content = str(item.get("content", "")).strip()
            score = item.get("score", "")
            if not content:
                continue
            used = True
            parts.append(f"{i}. 标题: {title}\n   相关度: {score}\n   内容: {content[:600]}")

        if not used:
            return "", False
        parts.append("请依据以上医院资料回答；资料不足时明确说明，不能补造医院事实。")
        return "\n".join(parts), True
    except Exception as ex:
        logger.warning(f"构建知识库上下文失败: {ex}")
        return "", False


def _should_use_knowledge(message: str, intent=None) -> bool:
    """跳过纯寒暄，业务类问题才检索知识库，避免无关 RAG 干扰回复。"""
    msg = (message or "").strip().lower()
    if not msg:
        return False
    intent_value = getattr(intent, "value", intent)
    if intent_value in {"greeting", "feedback", "escalation", "human_handoff", "other"}:
        return False
    if intent_value in {
        "query", "request", "guidance", "appointment", "complaint", "hospital_info",
        "department_info", "doctor_info", "visit_preparation", "visit_process", "wayfinding",
    }:
        return True
    greetings = {"你好", "您好", "嗨", "hi", "hello", "hey", "早上好", "晚上好"}
    if msg in greetings:
        return False
    business_keywords = [
        "医院", "科室", "医生", "就诊", "材料", "报到", "流程", "指引", "无障碍",
    ]
    return len(msg) >= 4 or any(kw in msg for kw in business_keywords)


@app.get("/monitor")
async def monitor_summary():
    """实时监控摘要：Agent 成功率、工具统计、告警、优化建议。"""
    if _monitor is None:
        raise HTTPException(503, "服务未就绪")
    return _monitor.summary()


@app.get("/trace/tool/{request_id}", response_model=ToolTraceResponse)
async def get_tool_trace(request_id: str):
    """查看某次请求的工具调用明细。"""
    if _orchestrator is None:
        raise HTTPException(503, "服务未就绪")
    trace = _orchestrator.get_tool_trace(request_id)
    return ToolTraceResponse(
        request_id=request_id,
        found=trace is not None,
        trace=trace or {},
    )


@app.get("/trace/tools", response_model=RecentToolTracesResponse)
async def list_recent_tool_traces(limit: int = 20):
    """查看最近 N 次请求的工具调用明细。"""
    if _orchestrator is None:
        raise HTTPException(503, "服务未就绪")
    return RecentToolTracesResponse(items=_orchestrator.get_recent_tool_traces(limit=limit))


@app.get("/metrics")
async def prometheus_metrics():
    """Prometheus 指标入口。"""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/search")
async def search(query: str, top_k: int = 5):
    """
    演示检索优化链路：查询改写 → 并行召回 → 重排 → Top-K。
    展示 MCP 工具调用的核心亮点。
    """
    if _tool_manager is None:
        raise HTTPException(503, "服务未就绪")
    result = await _tool_manager.search_with_rewrite("knowledge_search", query, top_k=top_k)
    return {"query": query, "results": result.data, "reranked": result.reranked,
            "success": result.success, "error": result.error, "error_code": result.error_code,
            "rewrite_error": result.rewrite_error, "rerank_error": result.rerank_error,
            "recall_errors": result.recall_errors, "fallback_used": result.fallback_used, "partial": result.partial}


class DocInput(BaseModel):
    """单篇文档输入。"""
    title:   str
    content: str
    doc_id: Optional[str] = None
    source_id: Optional[str] = None
    source: Optional[str] = None


class BatchDocInput(BaseModel):
    """批量文档导入请求体。"""
    documents: List[DocInput]


class EvalIntentInput(BaseModel):
    """意图识别评测用例。"""
    message: str
    expected_intent: str
    context: Optional[Dict[str, Any]] = None


class EvalDialogInput(BaseModel):
    """对话质量评测用例。question 单轮，turns 多轮。"""
    question: Optional[str] = None
    turns: Optional[List[str]] = None
    user_id: Optional[str] = None
    conv_id: Optional[str] = None
    id: Optional[str] = None
    patient_id: Optional[str] = None
    clock: str = "2026-09-16T10:00:00+08:00"
    preconditions: List[str] = Field(default_factory=list)
    assertions: List[str] = Field(default_factory=list)
    setup: List[Dict[str, Any]] = Field(default_factory=list)
    between_turns: Dict[str, List[Dict[str, Any]]] = Field(default_factory=dict)
    after_turns: List[Dict[str, Any]] = Field(default_factory=list)


class EvalRunInput(BaseModel):
    """评测请求。为空时使用内置默认用例。"""
    intent_cases: Optional[List[EvalIntentInput]] = None
    dialog_cases: Optional[List[EvalDialogInput]] = None
    boundary_cases: Optional[List[EvalDialogInput]] = None


@app.post("/knowledge/add", tags=["知识库"])
async def add_knowledge(body: BatchDocInput):
    """
    批量导入文档到知识库。

    文档会自动切片（每片 500 字）并存入 ChromaDB，ChromaDB 内置 Embedding 模型自动向量化。

    示例请求体：
    ```json
    {
      "documents": [
        {"title": "首次就诊准备", "content": "请携带有效身份证件和已有就诊资料。"},
        {"title": "院内指引", "content": "请按明和虚构医院的预置地点说明行走。"}
      ]
    }
    ```
    """
    tool = _tool_manager._tools.get("knowledge_search") if _tool_manager else None
    if tool is None:
        raise HTTPException(503, "知识库未初始化")
    kb = tool.handler.__self__
    count = await kb.add_documents_async([d.model_dump(exclude_none=True) for d in body.documents])
    total = await kb.doc_count_async()
    return {"message": f"成功导入 {count} 个文档片段", "added_chunks": count, "total_chunks": total}


@app.post("/knowledge/upload", tags=["知识库"])
async def upload_knowledge(file: UploadFile = File(...)):
    """
    上传文件导入知识库。

    支持格式：
    - `.txt` / `.md`：整个文件作为一篇文档，文件名作为标题
    - `.json`：JSON 数组格式 `[{"title": "...", "content": "..."}, ...]`

    文件大小限制：10MB
    """
    tool = _tool_manager._tools.get("knowledge_search") if _tool_manager else None
    if tool is None:
        raise HTTPException(503, "知识库未初始化")
    kb = tool.handler.__self__

    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(413, "文件大小超过 10MB 限制")

    text = content.decode("utf-8", errors="ignore")
    filename = file.filename or "unknown"

    if filename.endswith(".json"):
        import json as _json
        try:
            docs = _json.loads(text)
            if not isinstance(docs, list):
                raise HTTPException(400, "JSON 文件应为数组格式: [{title, content}, ...]")
        except _json.JSONDecodeError as e:
            raise HTTPException(400, f"JSON 解析失败: {e}")
    else:
        # txt / md：整个文件作为一篇文档
        title = filename.rsplit(".", 1)[0] if "." in filename else filename
        docs = [{"title": title, "content": text}]

    count = await kb.add_documents_async(docs)
    total = await kb.doc_count_async()
    return {
        "message": f"文件 {filename} 导入成功",
        "added_chunks": count,
        "total_chunks": total,
    }


@app.get("/knowledge/stats", tags=["知识库"])
async def knowledge_stats():
    """查看知识库统计信息（文档片段总数）。"""
    tool = _tool_manager._tools.get("knowledge_search") if _tool_manager else None
    if tool is None:
        raise HTTPException(503, "知识库未初始化")
    kb = tool.handler.__self__
    return {"total_chunks": await kb.doc_count_async()}


@app.post("/eval/run")
async def run_eval(body: Optional[EvalRunInput] = None):
    """运行内置评测用例，返回评测报告。"""
    if _evaluator is None:
        raise HTTPException(503, "服务未就绪")
    from evaluation.evaluator import DEFAULT_BOUNDARY_CASES, DEFAULT_DIALOG_CASES, DEFAULT_INTENT_CASES, IntentTestCase

    if body and body.intent_cases is not None:
        intent_cases = [
            IntentTestCase(
                message=c.message,
                expected_intent=c.expected_intent,
                context=c.context,
            )
            for c in body.intent_cases
        ]
    else:
        intent_cases = DEFAULT_INTENT_CASES

    if body and body.dialog_cases is not None:
        dialog_cases = [
            c.model_dump(exclude_none=True)
            for c in body.dialog_cases
        ]
    else:
        dialog_cases = DEFAULT_DIALOG_CASES

    report = await _evaluator.run(
        intent_cases=intent_cases,
        dialog_cases=dialog_cases,
        boundary_cases=[c.model_dump(exclude_none=True) for c in body.boundary_cases] if body and body.boundary_cases is not None else DEFAULT_BOUNDARY_CASES,
        metadata={"entrypoint": "api", "model": os.getenv("ANTHROPIC_MODEL", ""),
                  "intent_vectors": _intent_embeddings.status() if _intent_embeddings is not None else {"status": "uninitialized"},
                  "knowledge_vectors": "Chroma all-MiniLM-L6-v2",
                  "storage": "isolated Redis keys and Chroma collections"},
    )
    return asdict(report)


# ── 交互式 CLI ────────────────────────────────────────────────────────────────
async def _cli():
    print("MediPet CLI — 输入 quit 退出；预约确认请使用页面。\n")
    async with lifespan(app):
        conv_id = None
        while True:
            try:
                msg = (await asyncio.to_thread(input, "你: ")).strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not msg or msg.lower() in ("quit", "exit", "退出"):
                break
            background = BackgroundTasks()
            result = await chat(ChatRequest(message=msg, conv_id=conv_id), background)
            conv_id = result.conv_id
            print(f"\nMediPet [{result.agent_type}]: {result.response}\n")
            await background()


if __name__ == "__main__":
    if "--cli" in sys.argv:
        asyncio.run(_cli())
    else:
        uvicorn.run(
            "api.main:app",
            host=os.getenv("API_HOST", "0.0.0.0"),
            port=int(os.getenv("API_PORT", "8000")),
            reload=os.getenv("APP_ENV") == "development",
        )
