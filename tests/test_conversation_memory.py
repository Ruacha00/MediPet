"""记忆的作用域/恢复检查；真实存储与假模型按环境显式分层。"""
import json
import os
from types import SimpleNamespace
from uuid import uuid4

import chromadb
import pytest
import pytest_asyncio
from redis.asyncio import Redis

from datetime import datetime, timezone
from hospital.models import VisitIdentity, VisitMessage
from hospital.service import HospitalService
from hospital.store import HospitalStore
from memory.conversation_memory import MemoryManager, MsgRole
from memory.visit_store import VisitStore, VisitStoreError
from test_visit_memory import MemoryRedis, sample_selection
from test_appointment_flow import BusinessRedis


class Collection:
    def __init__(self):
        self.rows = {}

    def add(self, *, ids, documents, metadatas):
        self.rows.update({key: (doc, meta) for key, doc, meta in zip(ids, documents, metadatas)})

    upsert = add

    @classmethod
    def matches(cls, meta, where):
        if "$and" in where:
            return all(cls.matches(meta, part) for part in where["$and"])
        return all(meta.get(key) != value["$ne"] if isinstance(value, dict) else meta.get(key) == value for key, value in where.items())

    def get(self, ids=None, where=None):
        rows = [(key, doc, meta) for key, (doc, meta) in self.rows.items() if (ids is None or key in ids) and (where is None or self.matches(meta, where))]
        return {"ids": [row[0] for row in rows], "documents": [row[1] for row in rows], "metadatas": [row[2] for row in rows]}

    def query(self, *, query_texts, n_results, where):
        result = self.get(where=where)
        return {"documents": [result["documents"][:n_results]]}


class Chroma:
    def __init__(self):
        self.collections = {}

    def heartbeat(self):
        return 1

    def get_or_create_collection(self, name):
        return self.collections.setdefault(name, Collection())


class Model:
    def __init__(self):
        self.reply = "就诊事项的历史摘要。"
        self.calls = []
        self.messages = self

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=self.reply)])


@pytest_asyncio.fixture(params=["fake", "real"])
async def memory(request):
    real = request.param == "real"
    if real:
        if not os.getenv("MEDIPET_VISIT_TEST_REDIS_URL") or not os.getenv("MEDIPET_MEMORY_TEST_CHROMA"):
            pytest.skip("显式开启 Redis DB15/Chroma 隔离集合验证")
        client = Redis.from_url(os.environ["MEDIPET_VISIT_TEST_REDIS_URL"], decode_responses=True, socket_timeout=5)
        assert client.connection_pool.connection_kwargs.get("db") == 15
        chroma = chromadb.HttpClient(host="127.0.0.1", port=8001, settings=chromadb.Settings(anonymized_telemetry=False))
    else:
        client, chroma = MemoryRedis(), Chroma()
    suffix = uuid4().hex
    visits = VisitStore(client, prefix=f"medipet:test:memory:{suffix}:")
    await visits.initialize_patients(HospitalService().data.patients)
    identities = []
    for patient in ("patient_self", "patient_child", "patient_child"):
        visit = await visits.create_visit("anonymous", patient)
        identities.append(VisitIdentity(user_id=visit.user_id, patient_id=visit.patient_id, conv_id=visit.conv_id))
    prefix = f"medipet_test_{suffix}_"
    manager = MemoryManager(api_key="test-key", redis_client=client, chroma_client=chroma, visit_store=visits, collection_prefix=prefix)
    model = Model()
    manager._client = model
    yield SimpleNamespace(manager=manager, visits=visits, identities=identities, redis=client, chroma=chroma, model=model, real=real)
    if real:
        keys = [key async for key in client.scan_iter(match=f"{visits.prefix}*")]
        if keys:
            await client.delete(*keys)
        for name in (f"{prefix}episodic", f"{prefix}profiles"):
            chroma.delete_collection(name)
        await client.aclose()


@pytest.mark.asyncio
async def test_working_memory_and_summary_keep_patient_and_visit_scope(memory):
    own, child, other = memory.identities
    manager = memory.manager
    for index in range(15):
        await manager.add_message(child, MsgRole.USER, f"儿童就诊资料第{index}条")
    await manager.add_message(own, MsgRole.USER, "本人事项只说这一句")
    child_context = await manager.get_context(child, "就诊资料")
    own_context = await manager.get_context(own, "就诊资料")
    other_context = await manager.get_context(other, "就诊资料")
    assert [m.content for m in child_context.recent_messages] == [f"儿童就诊资料第{index}条" for index in range(10, 15)]
    assert child_context.summary == "就诊事项的历史摘要。"
    assert [m.content for m in own_context.recent_messages] == ["本人事项只说这一句"]
    assert own_context.summary == "" and other_context.recent_messages == []
    assert own_context.relevant_history == []
    assert other_context.relevant_history == ["就诊事项的历史摘要。"]
    assert 0 < await memory.redis.ttl(manager._wm_key(child)) <= 86400


@pytest.mark.asyncio
async def test_emergency_window_write_can_skip_model_compression_at_threshold(memory):
    manager = memory.manager
    child = memory.identities[1]
    for index in range(manager.COMPRESS_AT - 1):
        await manager.add_message(child, MsgRole.USER, f"窗口原消息{index}")
    await manager.add_message(child, MsgRole.USER, "现在呼吸困难", compress=False)
    await manager.add_message(child, MsgRole.ASSISTANT, "请立即联系现场医护人员。",
                              {"emergency": True}, compress=False)
    assert memory.model.calls == []
    assert await memory.redis.llen(manager._wm_key(child)) == manager.COMPRESS_AT + 1
    assert 0 < await memory.redis.ttl(manager._wm_key(child)) <= 86400
    assert await memory.redis.get(manager._summary_key(child)) is None
    context = await manager.get_context(child, "")
    assert [message.content for message in context.recent_messages[-2:]] == ["现在呼吸困难", "请立即联系现场医护人员。"]
    assert context.recent_messages[-1].metadata == {"emergency": True}
    assert memory.model.calls == []
    # 不传开关的普通写入继续走原阈值压缩：一次摘要模型调用、保留最近五条。
    await manager.add_message(child, MsgRole.USER, "恢复普通就诊咨询")
    assert len(memory.model.calls) == 1
    assert await memory.redis.llen(manager._wm_key(child)) == 5
    assert await memory.redis.get(manager._summary_key(child)) == "就诊事项的历史摘要。"


@pytest.mark.asyncio
async def test_invalidate_window_restores_saved_confirmation_and_receipt_without_model_call():
    redis, chroma = BusinessRedis(), Chroma()
    now = datetime.fromisoformat("2026-09-16T10:00:00+08:00")
    visits = VisitStore(redis, prefix="medipet:test:receipt-memory:", clock=lambda: now)
    service = HospitalService(store=HospitalStore(redis, visits.prefix), visit_store=visits, clock=lambda: now)
    await visits.initialize_patients(service.data.patients)
    identities = []
    for patient in ("patient_child", "patient_self", "patient_child"):
        visit = await visits.create_visit("anonymous", patient)
        identities.append(VisitIdentity(user_id=visit.user_id, patient_id=visit.patient_id, conv_id=visit.conv_id))
    child, own, other = identities
    manager = MemoryManager(api_key="test-key", redis_client=redis, chroma_client=chroma, visit_store=visits)
    manager._client = model = Model()
    slots = await service.search_slots(department="儿科", date="明天")
    prepared = await service.prepare_appointment(child, slot_id=slots.data["slots"][0]["slot_id"])
    assert prepared.success
    pending = VisitMessage(**child.model_dump(), message_id="pending-message", role="assistant",
                           content="预约资料待确认。", created_at=now, artifacts=prepared.artifacts)
    await visits.append_messages(child.user_id, child.conv_id, [pending])
    for identity in identities:
        await manager.add_message(identity, MsgRole.ASSISTANT, "预约资料待确认。", compress=False)
    await redis.setex(manager._summary_key(child), 86400, "已有摘要")
    manager._episodic.add(ids=["old-episode"], documents=["已有情景"], metadatas=[child.model_dump()])
    manager._profile.add(ids=["old-profile"], documents=["{}"], metadatas=[child.model_dump()])
    episodic_before = dict(manager._episodic.rows)
    profiles_before = dict(manager._profile.rows)

    confirmed = await service.confirm_proposal(child, prepared.data["proposal_id"])
    assert confirmed.success
    receipt = confirmed.data
    history = [
        VisitMessage(**child.model_dump(), message_id="confirmation-message", role="user", kind="confirmation_event",
                     content="确认预约。", created_at=now, proposal_id=receipt["proposal_id"]),
        VisitMessage(**child.model_dump(), message_id="receipt-message", role="assistant", kind="operation_result",
                     content="预约已完成。", created_at=now, proposal_id=receipt["proposal_id"], receipt_id=receipt["receipt_id"],
                     artifacts=confirmed.artifacts),
    ]
    await visits.append_messages(child.user_id, child.conv_id, history)
    selection_before = await visits.get_selection(child.user_id, child.conv_id)
    assert [m.content for m in await manager._get_working_memory(child)] == ["预约资料待确认。"]
    await manager.invalidate_working_memory(child)
    assert await redis.llen(manager._wm_key(child)) == 0 and model.calls == []
    assert await redis.llen(manager._wm_key(own)) == 1 and await redis.llen(manager._wm_key(other)) == 1
    assert await redis.get(manager._summary_key(child)) == "已有摘要"
    assert manager._episodic.rows == episodic_before and manager._profile.rows == profiles_before
    assert await visits.get_selection(child.user_id, child.conv_id) == selection_before
    assert await visits.get_messages(child.user_id, child.conv_id) == [pending, *history]

    restored = await manager.get_context(child, "查看预约记录")
    assert [m.content for m in restored.recent_messages] == ["预约资料待确认。", "确认预约。", "预约已完成。"]
    assert restored.recent_messages[-1].metadata["message_id"] == "receipt-message"
    assert restored.recent_messages[-1].metadata["artifacts"] == [a.model_dump(mode="json") for a in confirmed.artifacts]
    assert restored.recent_messages[-1].metadata["artifacts"][1]["data"]["status"] == "active"
    assert model.calls == [] and 0 < await redis.ttl(manager._wm_key(child)) <= 86400


@pytest.mark.asyncio
async def test_episodic_current_visit_first_then_same_patient_only(memory):
    own, child, other = memory.identities
    manager = memory.manager
    await manager._store_episodic(own, "本人资料", "本人独有的检查准备")
    await manager._store_episodic(other, "儿童旧事项", "儿童旧事项材料")
    assert await manager._search_episodic(child, "准备材料") == ["儿童旧事项材料"]
    await manager._store_episodic(child, "儿童本事项", "儿童当前事项材料")
    assert await manager._search_episodic(child, "准备材料") == ["儿童当前事项材料", "儿童旧事项材料"]
    assert await manager._search_episodic(own, "准备材料") == ["本人独有的检查准备"]


@pytest.mark.asyncio
async def test_profile_requires_explicit_quotes_and_separates_patient_from_preferences(memory):
    own, child, other = memory.identities
    manager = memory.manager
    await manager.add_message(child, MsgRole.USER, "孩子需要无障碍通道。请简短回答。")
    await manager.add_message(child, MsgRole.ASSISTANT, "助手猜测的疾病不能成为事实。")
    memory.model.reply = json.dumps({"patient_facts": ["孩子需要无障碍通道", "孩子患有某种疾病", "助手猜测的疾病不能成为事实"],
                                    "preferences": ["请简短回答", "喜欢英语"]}, ensure_ascii=False)
    await manager.update_profile(child)
    child_profile = await manager._get_profile(child)
    assert child_profile == {"patient_facts": ["孩子需要无障碍通道"], "preferences": ["请简短回答"]}
    assert await manager._get_profile(other) == child_profile
    assert await manager._get_profile(own) == {"preferences": ["请简短回答"]}


@pytest.mark.asyncio
async def test_expired_window_restores_own_history_cards_and_actual_selection(memory):
    own, child, other = memory.identities
    manager = memory.manager
    clock = SimpleNamespace(value=datetime.now(timezone.utc))
    card = HospitalService().get_visit_checklist().artifacts[0]
    history = [VisitMessage(**child.model_dump(), message_id=f"history-{index}", role="user" if index % 2 == 0 else "assistant",
                            content=f"孩子当前事项第{index}条", created_at=clock.value, artifacts=[card] if index == 21 else [])
               for index in range(22)]
    await memory.visits.append_messages(child.user_id, child.conv_id, history)
    state = sample_selection(child, clock)
    await memory.visits.save_selection(state)
    async with memory.redis.pipeline(transaction=True) as pipe:
        pipe.multi()
        memory.visits.queue_selection(pipe, state.model_copy(update={"current_proposal_id": "proposal-saved"}))
        await pipe.execute()
    await manager.add_message(child, MsgRole.USER, "过期前窗口")
    if memory.real:
        await memory.redis.pexpire(manager._wm_key(child), 1)
        import asyncio
        await asyncio.sleep(0.01)
    else:
        await memory.redis.delete(manager._wm_key(child))
    restored = await manager.get_context(child, "第一个，还是下午吧")
    assert [message.content for message in restored.recent_messages] == [message.content for message in history[-5:]]
    assert restored.recent_messages[-1].metadata["artifacts"] == [card.model_dump(mode="json")]
    assert restored.selection_context["current_proposal_id"] == "proposal-saved"
    assert restored.selection_context["slots"][0]["slot_id"] == state.slots[0].slot_id
    assert "序号从1开始" in restored.to_prompt_text()
    assert await memory.visits.get_messages(child.user_id, child.conv_id) == history
    assert (await manager.get_context(own, "第一个")).selection_context["slots"] == []
    assert (await manager.get_context(other, "明天呢")).recent_messages == []
    assert all(call["messages"][0]["content"] for call in memory.model.calls)
    await memory.visits.update_visit(child.user_id, child.conv_id, archived=True)
    with pytest.raises(VisitStoreError) as archived:
        await manager.get_context(child)
    assert archived.value.code == "visit_archived"
    await memory.visits.update_visit(child.user_id, child.conv_id, archived=False)
    assert (await manager.get_context(child)).selection_context["current_proposal_id"] == "proposal-saved"
    assert await memory.visits.get_messages(child.user_id, child.conv_id) == history
