"""仅在显式启用时运行真实组件，不使用生产命名空间。"""
import os
from uuid import uuid4

import chromadb
import pytest
from redis.asyncio import Redis

from hospital.models import VisitIdentity
from hospital.service import HospitalService
from hospital.store import HospitalStore
from mcp.knowledge_base import KnowledgeBase
from memory.visit_store import VisitStore


@pytest.mark.skipif(not os.getenv("MEDIPET_MEMORY_TEST_CHROMA"), reason="显式启用真实 Chroma")
def test_real_knowledge_initialization_sources_import_and_empty_collection(monkeypatch):
    client = chromadb.HttpClient(host="127.0.0.1", port=8001, settings=chromadb.Settings(anonymized_telemetry=False))
    collection = f"medipet_test_knowledge_{uuid4().hex}"
    monkeypatch.setattr(KnowledgeBase, "COLLECTION_NAME", collection)
    try:
        knowledge = KnowledgeBase(chroma_host="127.0.0.1", chroma_port=8001)
        assert knowledge._use_server and knowledge.doc_count >= 17
        before = knowledge.doc_count
        second = KnowledgeBase(chroma_host="127.0.0.1", chroma_port=8001)
        assert second.doc_count == before
        retrieved = second.search("儿童首次就诊材料", 3)
        assert len(retrieved) == 3
        assert all(item["chunk_id"] and item["doc_id"] and item["source_id"] and item["source"].startswith("knowledge/") for item in retrieved)
        uploaded = [{"title": "独立集成验证文档", "content": "测试用的静态就诊说明。"}]
        assert knowledge.add_documents(uploaded) == 1
        assert knowledge.add_documents(uploaded) == 0
        assert second.doc_count == before + 1
        records = knowledge._collection.get(include=["documents", "metadatas"])
        missing = next(
            chunk_id
            for chunk_id, metadata in zip(records["ids"], records["metadatas"])
            if metadata["source"].startswith("knowledge/")
        )
        knowledge._collection.delete(ids=[missing])
        assert knowledge.doc_count == before
        third = KnowledgeBase(chroma_host="127.0.0.1", chroma_port=8001)
        assert third.doc_count == before + 1
        assert third._collection.get(ids=[missing], include=[])["ids"] == [missing]
        # 独立空集合的空结果，不把正常集合清空来制造测试条件。
        empty_name = f"medipet_test_empty_{uuid4().hex}"
        monkeypatch.setattr(KnowledgeBase, "COLLECTION_NAME", empty_name)
        try:
            empty = KnowledgeBase(chroma_host="127.0.0.1", chroma_port=8001)
            assert empty._use_server and empty.doc_count == before
            empty._collection.delete(ids=empty._collection.get(include=[])["ids"])
            assert empty.doc_count == 0 and empty.search("材料") == []
        finally:
            client.delete_collection(empty_name)
    finally:
        client.delete_collection(collection)


@pytest.mark.asyncio
@pytest.mark.skipif(not os.getenv("MEDIPET_VISIT_TEST_REDIS_URL"), reason="显式启用真实 Redis DB15")
async def test_real_reinitialization_and_new_connections_preserve_appointment_inventory():
    url = os.environ["MEDIPET_VISIT_TEST_REDIS_URL"]
    client = Redis.from_url(url, decode_responses=True, socket_timeout=5)
    assert client.connection_pool.connection_kwargs.get("db") == 15
    prefix = f"medipet:test:restart:{uuid4().hex}:"
    second = None
    try:
        visits = VisitStore(client, prefix=prefix)
        service = HospitalService(store=HospitalStore(client, prefix), visit_store=visits)
        await visits.initialize_patients(service.data.patients)
        assert (await service.initialize_slots()).data["created"] == 42
        visit = await visits.create_visit("anonymous", "patient_child")
        identity = VisitIdentity(user_id=visit.user_id, patient_id=visit.patient_id, conv_id=visit.conv_id)
        slots = (await service.search_slots(department="儿科", date="明天")).data["slots"]
        prepared = await service.prepare_appointment(identity, slot_id=slots[0]["slot_id"])
        executed = await service.confirm_proposal(identity, prepared.data["proposal_id"])
        assert executed.success
        await client.aclose()
        second = Redis.from_url(url, decode_responses=True, socket_timeout=5)
        restarted = HospitalService(store=HospitalStore(second, prefix), visit_store=VisitStore(second, prefix=prefix))
        assert await restarted.visits.initialize_patients(restarted.data.patients) == 0
        assert (await restarted.initialize_slots()).data["created"] == 0
        assert (await restarted.confirm_proposal(identity, prepared.data["proposal_id"])).data == executed.data
        assert (await restarted.store.get_slot(slots[0]["slot_id"])).remaining == slots[0]["remaining"] - 1
        keys = [key async for key in second.scan_iter(match=f"{prefix}*")]
        assert all([await second.ttl(key) == -1 for key in keys])
    finally:
        cleanup = second or client
        keys = [key async for key in cleanup.scan_iter(match=f"{prefix}*")]
        if keys:
            await cleanup.delete(*keys)
        await cleanup.aclose()
