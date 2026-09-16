"""预约业务的确定性测试；可用独立 DB15 前缀重跑同组真实 Redis 语义。"""

from datetime import datetime, timedelta
import asyncio
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from redis.asyncio import Redis

from hospital.models import AppointmentProposal, AppointmentRecord, SelectionState, Slot, SlotList, VisitIdentity
from hospital.service import HospitalService
from hospital.store import HospitalStore
from memory.visit_store import VisitStore
from test_visit_memory import MemoryRedis, MemoryPipeline


class BusinessRedis(MemoryRedis):
    async def set(self, key, value, nx=False):
        if nx and key in self.strings:
            return False
        return await super().set(key, value)

    async def mget(self, keys):
        return [await self.get(key) for key in keys]

    def pipeline(self, transaction=True):
        return BusinessPipeline(self)


class BusinessPipeline(MemoryPipeline):
    def set(self, *args, **kwargs):
        self.commands.append(("set", args, kwargs))


@pytest_asyncio.fixture(params=["fake", "real"])
async def business(request):
    if request.param == "real":
        url = os.getenv("MEDIPET_VISIT_TEST_REDIS_URL")
        if not url:
            pytest.skip("显式配置专用 Redis DB15 后执行真实业务事务核验")
        redis = Redis.from_url(url, decode_responses=True, socket_timeout=5, socket_connect_timeout=5)
        assert redis.connection_pool.connection_kwargs.get("db") == 15
    else:
        redis = BusinessRedis()
    prefix = f"medipet:test:appointments:{uuid4().hex}:"
    clock = SimpleNamespace(value=datetime.fromisoformat("2026-09-16T10:00:00+08:00"))
    store = HospitalStore(redis, prefix)
    visits = VisitStore(redis, prefix=prefix, clock=lambda: clock.value)
    service = HospitalService(store=store, visit_store=visits, clock=lambda: clock.value)
    await visits.initialize_patients(service.data.patients)
    await service.initialize_slots()
    context = SimpleNamespace(service=service, store=store, visits=visits, redis=redis, clock=clock, real=request.param == "real")
    yield context
    if context.real:
        keys = [key async for key in redis.scan_iter(match=f"{prefix}*")]
        if keys:
            await redis.delete(*keys)
        await redis.aclose()


async def identity_for(business, patient="patient_child"):
    visit = await business.visits.create_visit("anonymous", patient)
    return VisitIdentity(user_id=visit.user_id, patient_id=visit.patient_id, conv_id=visit.conv_id)


async def select_slots(business, identity):
    result = await business.service.search_slots(department="儿科", date="明天")
    slots = SlotList.model_validate(result.data)
    state = SelectionState(**identity.model_dump(), status="ready", **slots.model_dump())
    await business.visits.save_selection(state)
    return slots.slots


@pytest.mark.asyncio
async def test_preparation_persists_snapshot_without_booking_or_consuming_stock(business):
    identity = await identity_for(business)
    slots = await select_slots(business, identity)
    result = await business.service.prepare_appointment(identity, selection_index=1)
    assert result.success
    proposal = AppointmentProposal.model_validate(result.data)
    assert proposal.target_id == slots[0].slot_id and proposal.snapshot.patient_name == "林小满"
    assert proposal.expires_at - proposal.created_at == timedelta(minutes=15)
    assert (await business.store.get_slot(slots[0].slot_id)).remaining == slots[0].remaining
    assert (await business.service.get_appointments(identity)).data == {"items": []}
    assert await business.store.get_proposal(proposal.proposal_id) == proposal
    assert (await business.service.get_current_proposal(identity)).data == result.data
    assert result.artifacts[0].data == result.data


@pytest.mark.asyncio
async def test_selection_requires_current_real_list_and_reselection_supersedes(business):
    identity = await identity_for(business)
    assert (await business.service.prepare_appointment(identity, selection_index=1)).error_code == "selection_unavailable"
    slots = await select_slots(business, identity)
    for index in (0, 3, True):
        assert (await business.service.prepare_appointment(identity, selection_index=index)).error_code == "selection_unavailable"
    first = await business.service.prepare_appointment(identity, selection_index=1)
    second = await business.service.prepare_appointment(identity, slot_id=slots[1].slot_id)
    old = await business.store.get_proposal(first.data["proposal_id"])
    assert old.status == "superseded" and old.result is None
    assert (await business.visits.get_selection(identity.user_id, identity.conv_id)).current_proposal_id == second.data["proposal_id"]
    assert (await business.service.get_current_proposal(identity)).data == second.data
    assert (await business.service.get_appointments(identity)).data["items"] == []


@pytest.mark.asyncio
async def test_prepare_errors_and_expiry_preserve_inventory_and_patient_scope(business):
    identity = await identity_for(business)
    slots = await select_slots(business, identity)
    assert (await business.service.prepare_appointment(identity, slot_id="unknown-slot")).error_code == "not_found"
    assert (await business.service.prepare_appointment(identity)).error_code == "missing_fields"
    forged = identity.model_copy(update={"patient_id": "patient_self"})
    assert (await business.service.prepare_appointment(forged, slot_id=slots[0].slot_id)).error_code == "identity_conflict"
    prepared = await business.service.prepare_appointment(identity, slot_id=slots[0].slot_id)
    business.clock.value += timedelta(minutes=15)
    assert (await business.service.get_current_proposal(identity)).error_code == "proposal_expired"
    assert (await business.store.get_proposal(prepared.data["proposal_id"])).status == "expired"
    assert (await business.store.get_slot(slots[0].slot_id)).remaining == slots[0].remaining
    await business.visits.update_visit(identity.user_id, identity.conv_id, archived=True)
    assert (await business.service.prepare_appointment(identity, slot_id=slots[0].slot_id)).error_code == "visit_archived"


@pytest.mark.asyncio
async def test_cancellation_prepares_existing_patient_record_in_another_visit(business):
    original = await identity_for(business)
    slots = await select_slots(business, original)
    prepared = await business.service.prepare_appointment(original, slot_id=slots[0].slot_id)
    # H05 没有执行入口；此真实业务结构是已存在预约的测试输入。
    record = AppointmentRecord(**original.model_dump(), appointment_id="appointment-existing", snapshot=prepared.data["snapshot"],
                               status="active", created_at=business.clock.value)
    await business.redis.set(business.store.appointment_key(record.appointment_id), record.model_dump_json())
    await business.redis.sadd(business.store.appointments_key(original.user_id, original.patient_id), record.appointment_id)
    another = await identity_for(business)
    proposal = await business.service.prepare_cancellation(another, record.appointment_id)
    assert proposal.success and proposal.data["operation"] == "cancel" and proposal.data["conv_id"] == another.conv_id
    assert await business.store.get_appointment(record.appointment_id) == record
    assert (await business.service.get_appointments(another)).data["items"] == [record.model_dump(mode="json")]
    own = await identity_for(business, "patient_self")
    assert (await business.service.get_appointments(own)).data["items"] == []
    assert (await business.service.prepare_cancellation(own, record.appointment_id)).error_code == "identity_conflict"
    assert (await business.service.prepare_cancellation(another, "missing-appointment")).error_code == "not_found"


@pytest.mark.asyncio
async def test_confirm_create_cancel_and_replay_keep_original_receipts(business):
    identity = await identity_for(business)
    slots = await select_slots(business, identity)
    prepared = await business.service.prepare_appointment(identity, selection_index=1)
    first = await business.service.confirm_proposal(identity, prepared.data["proposal_id"])
    assert first.success and first.data["appointment"]["status"] == "active"
    appointment_id = first.data["appointment"]["appointment_id"]
    assert (await business.store.get_slot(slots[0].slot_id)).remaining == slots[0].remaining - 1
    assert (await business.service.confirm_proposal(identity, prepared.data["proposal_id"])).model_dump() == first.model_dump()
    other_visit = await identity_for(business)
    cancellation = await business.service.prepare_cancellation(other_visit, appointment_id)
    cancelled = await business.service.confirm_proposal(other_visit, cancellation.data["proposal_id"])
    assert cancelled.success and cancelled.data["appointment"]["status"] == "cancelled"
    assert cancelled.data["conv_id"] == other_visit.conv_id
    assert cancelled.data["appointment"]["conv_id"] == identity.conv_id
    assert (await business.store.get_slot(slots[0].slot_id)).remaining == slots[0].remaining
    business.clock.value += timedelta(hours=1)
    assert (await business.service.confirm_proposal(other_visit, cancellation.data["proposal_id"])).data == cancelled.data
    assert (await business.service.confirm_proposal(identity, prepared.data["proposal_id"])).data == first.data
    assert (await business.service.prepare_cancellation(other_visit, appointment_id)).error_code == "target_unavailable"
    records = (await business.service.get_appointments(identity)).data["items"]
    assert len(records) == 1 and records[0]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_stale_expired_wrong_visit_and_wrong_patient_cannot_confirm(business):
    identity = await identity_for(business)
    slots = await select_slots(business, identity)
    first = await business.service.prepare_appointment(identity, selection_index=1)
    latest = await business.service.prepare_appointment(identity, selection_index=2)
    assert (await business.service.confirm_proposal(identity, first.data["proposal_id"])).error_code == "proposal_superseded"
    another = await identity_for(business)
    own = await identity_for(business, "patient_self")
    for wrong in (another, own):
        assert (await business.service.confirm_proposal(wrong, latest.data["proposal_id"])).error_code == "identity_conflict"
    business.clock.value += timedelta(minutes=15)
    assert (await business.service.confirm_proposal(identity, latest.data["proposal_id"])).error_code == "proposal_expired"
    assert (await business.store.get_proposal(latest.data["proposal_id"])).status == "expired"
    assert (await business.visits.get_selection(identity.user_id, identity.conv_id)).current_proposal_id is None
    assert (await business.service.get_appointments(identity)).data["items"] == []
    assert all([(await business.store.get_slot(slot.slot_id)).remaining == slot.remaining for slot in slots])


@pytest.mark.asyncio
async def test_changed_snapshot_and_unavailable_inventory_do_not_partially_execute(business):
    identity = await identity_for(business)
    slots = await select_slots(business, identity)
    prepared = await business.service.prepare_appointment(identity, selection_index=1)
    for changes in ({"fee_fen": slots[0].fee_fen + 100}, {"remaining": 0}):
        changed = slots[0].model_copy(update=changes)
        await business.redis.set(business.store.slot_key(changed.slot_id), changed.model_dump_json())
        result = await business.service.confirm_proposal(identity, prepared.data["proposal_id"])
        assert result.error_code == "target_unavailable"
        assert (await business.service.get_appointments(identity)).data["items"] == []
        assert (await business.store.get_proposal(prepared.data["proposal_id"])).status == "pending"
        assert await business.store.get_slot(changed.slot_id) == changed


def overlap_transactions(monkeypatch, redis):
    """两次业务写入都到达 EXEC 前才放行，验证真实 WATCH 冲突而非顺序重复。"""
    pipeline_class = type(redis.pipeline())
    original = pipeline_class.execute
    reached = 0
    gate = asyncio.Event()

    async def execute(self, *args, **kwargs):
        nonlocal reached
        reached += 1
        if reached == 2:
            gate.set()
        await asyncio.wait_for(gate.wait(), timeout=5)
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(pipeline_class, "execute", execute)


@pytest.mark.asyncio
async def test_concurrent_confirmation_one_effect_and_retry_original_receipt(business, monkeypatch):
    identity = await identity_for(business)
    slots = await select_slots(business, identity)
    proposal = await business.service.prepare_appointment(identity, selection_index=1)
    overlap_transactions(monkeypatch, business.redis)
    results = await asyncio.gather(*[business.service.confirm_proposal(identity, proposal.data["proposal_id"]) for _ in range(2)])
    assert sum(item.success for item in results) == 1
    assert [item for item in results if not item.success][0].error_code == "conflict"
    retried = await business.service.confirm_proposal(identity, proposal.data["proposal_id"])
    assert retried.data == [item for item in results if item.success][0].data
    assert (await business.store.get_slot(slots[0].slot_id)).remaining == slots[0].remaining - 1
    assert len((await business.service.get_appointments(identity)).data["items"]) == 1


@pytest.mark.asyncio
async def test_two_proposals_competing_for_last_slot_never_oversell(business, monkeypatch):
    first = await identity_for(business)
    second = await identity_for(business, "patient_self")
    slots = await select_slots(business, first)
    slot = slots[0].model_copy(update={"remaining": 1})
    await business.redis.set(business.store.slot_key(slot.slot_id), slot.model_dump_json())
    proposals = [await business.service.prepare_appointment(identity, slot_id=slot.slot_id) for identity in (first, second)]
    overlap_transactions(monkeypatch, business.redis)
    results = await asyncio.gather(*[business.service.confirm_proposal(identity, proposal.data["proposal_id"]) for identity, proposal in zip((first, second), proposals)])
    assert sum(item.success for item in results) == 1
    assert (await business.store.get_slot(slot.slot_id)).remaining == 0
    loser = 0 if not results[0].success else 1
    assert results[loser].error_code == "conflict" and results[loser].retryable
    retry = await business.service.confirm_proposal((first, second)[loser], proposals[loser].data["proposal_id"])
    assert retry.error_code == "target_unavailable"
    all_records = [(await business.service.get_appointments(identity)).data["items"] for identity in (first, second)]
    assert sum(map(len, all_records)) == 1


@pytest.mark.asyncio
async def test_concurrent_cancellation_proposals_release_capacity_once(business, monkeypatch):
    identity = await identity_for(business)
    slots = await select_slots(business, identity)
    prepared = await business.service.prepare_appointment(identity, selection_index=1)
    created = await business.service.confirm_proposal(identity, prepared.data["proposal_id"])
    appointment_id = created.data["appointment"]["appointment_id"]
    another = await identity_for(business)
    proposals = [await business.service.prepare_cancellation(current, appointment_id) for current in (identity, another)]
    overlap_transactions(monkeypatch, business.redis)
    results = await asyncio.gather(*[business.service.confirm_proposal(current, proposal.data["proposal_id"]) for current, proposal in zip((identity, another), proposals)])
    assert sum(item.success for item in results) == 1
    assert (await business.store.get_slot(slots[0].slot_id)).remaining == slots[0].remaining
    loser = 0 if not results[0].success else 1
    assert results[loser].error_code == "conflict"
    retry = await business.service.confirm_proposal((identity, another)[loser], proposals[loser].data["proposal_id"])
    assert retry.error_code == "target_unavailable"
    assert (await business.store.get_slot(slots[0].slot_id)).remaining == slots[0].remaining


@pytest.mark.asyncio
async def test_archive_race_rejects_transaction_without_writing_appointment(business):
    if business.real:
        pytest.skip("归档竞态注入用替身；真实并发 WATCH 在两项事务竞争测试验证")
    identity = await identity_for(business)
    slots = await select_slots(business, identity)
    proposal = await business.service.prepare_appointment(identity, selection_index=1)

    async def archive():
        await business.redis.hset(business.visits.visit_key(identity.conv_id), mapping={"archived": "1"})

    business.redis.before_execute = archive
    result = await business.service.confirm_proposal(identity, proposal.data["proposal_id"])
    assert result.error_code == "conflict"
    assert (await business.store.get_proposal(proposal.data["proposal_id"])).status == "pending"
    assert (await business.store.get_slot(slots[0].slot_id)).remaining == slots[0].remaining
    assert await business.store.get_appointments(identity.user_id, identity.patient_id) == []
