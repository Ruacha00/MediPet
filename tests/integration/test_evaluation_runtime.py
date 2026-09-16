"""真实存储上的评测依赖隔离；不调用真实语言模型。"""
import os
from types import SimpleNamespace

import chromadb
import pytest
from redis.asyncio import Redis

from api.evaluation_runtime import build_case_runtime_factory
from hospital.models import VisitIdentity


@pytest.mark.asyncio
@pytest.mark.skipif(not os.getenv("MEDIPET_VISIT_TEST_REDIS_URL") or not os.getenv("MEDIPET_MEMORY_TEST_CHROMA"), reason="需显式开启真实评测存储")
async def test_real_evaluation_runtime_isolates_cases_and_cleans_only_owned_data(monkeypatch):
    redis = Redis.from_url(os.environ["MEDIPET_VISIT_TEST_REDIS_URL"], decode_responses=True)
    assert redis.connection_pool.connection_kwargs["db"] == 15
    chroma = chromadb.HttpClient(host="127.0.0.1", port=8001, settings=chromadb.Settings(anonymized_telemetry=False))
    async def unused(params, context=None):
        raise AssertionError("本测试不调用检索")
    factory = build_case_runtime_factory(config={"api_key": "test", "model": "test"}, redis_client=redis,
                                         chroma_client=chroma, knowledge=SimpleNamespace(search_handler=unused), skill_manager=None)
    prefixes, collections = [], []
    case = {"clock": "2026-09-16T10:00:00+08:00", "id": "emergency-before-model"}
    from anthropic.resources.messages import AsyncMessages
    async def model_reply(*args, **kwargs):
        return SimpleNamespace(content=[])
    monkeypatch.setattr(AsyncMessages, "create", model_reply)
    try:
        async with factory(case, "test-run") as first:
            prefixes.append(first.visit_store.prefix)
            collections += [first.memory_manager._episodic.name, first.memory_manager._profile.name]
            visit = await first.visit_store.create_visit("anonymous", "patient_child")
            identity = VisitIdentity(user_id=visit.user_id, patient_id=visit.patient_id, conv_id=visit.conv_id)
            result = await first.hospital_service.search_slots(department="儿科", date="明天")
            slot_id = result.data["slots"][0]["slot_id"]
            pending = await first.hospital_service.prepare_appointment(identity, slot_id=slot_id)
            assert (await first.hospital_service.confirm_proposal(identity, pending.data["proposal_id"])).success
            async with factory(case, "test-run") as second:
                prefixes.append(second.visit_store.prefix)
                collections += [second.memory_manager._episodic.name, second.memory_manager._profile.name]
                assert second.visit_store.prefix != first.visit_store.prefix
                assert await second.visit_store.list_visits("anonymous", "patient_child") == []
                assert (await second.hospital_service.store.get_slot(slot_id)).remaining == 5
                assert (await first.hospital_service.store.get_slot(slot_id)).remaining == 4
                assert first.isolated and second.isolated
                assert first.model_calls() == second.model_calls() == 0
                assert "model_unavailable" in second.faults
                second.advance_clock(900)
                assert (second.hospital_service.now() - first.hospital_service.now()).total_seconds() == 900
                with pytest.raises(RuntimeError, match="模型不可用"):
                    await second.memory_manager._client.messages.create(model="test", max_tokens=1, messages=[])
                assert second.model_calls() == 1
        async with factory({"id": "normal"}, "test-run") as normal:
            prefixes.append(normal.visit_store.prefix)
            collections += [normal.memory_manager._episodic.name, normal.memory_manager._profile.name]
            await normal.memory_manager._client.messages.create(model="test", max_tokens=1, messages=[])
            assert normal.model_calls() == 1 and normal.faults == ()
        for prefix in prefixes:
            assert [key async for key in redis.scan_iter(match=f"{prefix}*")] == []
        names = {collection.name for collection in chroma.list_collections()}
        assert not names.intersection(collections)
    finally:
        await redis.aclose()
