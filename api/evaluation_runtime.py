"""为每个评测场景提供独立的业务数据和同一套应用组件。"""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from types import SimpleNamespace
from uuid import uuid4

from agents.agent_orchestrator import AgentOrchestrator, build_shared_rag_tools
from evaluation.evaluator import EvaluationRuntime
from hospital.service import HospitalService
from hospital.store import HospitalStore
from memory.conversation_memory import MemoryManager
from memory.visit_store import VisitStore
from mcp.tool_manager import MCPToolManager, Tool


def build_case_runtime_factory(*, config, redis_client, chroma_client, knowledge, skill_manager):
    @asynccontextmanager
    async def case_runtime(case, run_id):
        # 随机作用域与演示数据隔离；退出时只清理本场景的键和集合。
        token = uuid4().hex
        prefix = f"medipet:eval:{token}:"
        collection_prefix = f"medipet_eval_{token}_"
        clock = SimpleNamespace(value=datetime.fromisoformat(case["clock"]) if case.get("clock") else datetime.now(ZoneInfo("Asia/Shanghai")))
        now = lambda: clock.value
        visits = VisitStore(redis_client, prefix=prefix, clock=now)
        service = HospitalService(store=HospitalStore(redis_client, prefix), visit_store=visits, clock=now)
        memory = None
        model_clients = {}
        try:
            await visits.initialize_patients(service.data.patients)
            initialized = await service.initialize_slots()
            if not initialized.success:
                raise RuntimeError(initialized.error)
            memory = MemoryManager(**config, redis_client=redis_client, chroma_client=chroma_client,
                                   visit_store=visits, collection_prefix=collection_prefix)
            orchestrator = AgentOrchestrator(**config, skill_manager=skill_manager,
                                            hospital_service=service, visit_store=visits)
            orchestrator._intent_recognizer._clock = now
            rag = MCPToolManager(**config)
            clients = [memory._client, orchestrator._intent_recognizer.client, orchestrator._composer._client, rag._client]
            clients.extend(agent._client for agents in orchestrator._pool.values() for agent in agents)
            model_clients = {id(client): client for client in clients}

            faults = []
            handler = knowledge.search_handler
            if case.get("id") == "retrieval-failure":
                faults.append("knowledge_backend_unavailable")
                async def unavailable_knowledge(params, context=None):
                    raise RuntimeError("评测注入：知识存储不可用")
                handler = unavailable_knowledge
            rag.register(Tool(name="knowledge_search", description="检索医院说明", handler=handler,
                              schema={"type": "object", "properties": {"query": {"type": "string"}, "top_k": {"type": "integer"}},
                                      "required": ["query"]}, supports_rerank=True))
            orchestrator.set_shared_tools(build_shared_rag_tools(rag))
            calls = [0]
            unavailable = case.get("id") == "emergency-before-model"
            if unavailable:
                faults.append("model_unavailable")
            for client in model_clients.values():
                original_create = client.messages.create
                async def counted_create(*, _create=original_create, **kwargs):
                    calls[0] += 1
                    if unavailable:
                        raise RuntimeError("评测注入：模型不可用")
                    return await _create(**kwargs)
                client.messages.create = counted_create

            def advance_clock(seconds):
                clock.value += timedelta(seconds=seconds)

            yield EvaluationRuntime(orchestrator=orchestrator, memory_manager=memory, visit_store=visits,
                                    hospital_service=service, isolated=True, faults=tuple(faults),
                                    advance_clock=advance_clock, model_calls=lambda: calls[0])
        finally:
            # 不共享这些 SDK 客户端，关闭后释放连接；Redis 连接由应用生命周期管理。
            for client in model_clients.values():
                await client.close()
            keys = [key async for key in redis_client.scan_iter(match=f"{prefix}*")]
            if keys:
                await redis_client.delete(*keys)
            if memory is not None:
                for suffix in ("episodic", "profiles"):
                    await asyncio.to_thread(chroma_client.delete_collection, f"{collection_prefix}{suffix}")

    return case_runtime
