"""M01 元数据算法测试；唯一前缀的真实 Redis 核验通过专用环境变量启用。"""

from datetime import datetime, timedelta
import os
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from redis.asyncio import Redis
from redis.exceptions import ConnectionError, WatchError

from hospital.models import Artifact, DemoData, Patient, SelectionState, Slot, SlotQuery, VisitMessage
from memory.visit_store import DEFAULT_VISIT_TITLE, VisitStore, VisitStoreError


class MemoryRedis:
    """只实现 M01 使用的 hash/set/WATCH 命令，不作为真实事务证据。"""

    def __init__(self, *, decode_responses=True):
        self.hashes = {}
        self.sets = {}
        self.strings = {}
        self.lists = {}
        self.versions = {}
        self.decode_responses = decode_responses
        self.fail = False
        self.before_execute = None
        self.ttls = {}

    def _check(self):
        if self.fail:
            raise ConnectionError("test backend unavailable")

    def _wire(self, value):
        return value if self.decode_responses or value is None else value.encode("utf-8")

    def _changed(self, key):
        self.versions[key] = self.versions.get(key, 0) + 1

    async def hsetnx(self, key, field, value):
        self._check()
        target = self.hashes.setdefault(key, {})
        if field in target:
            return 0
        target[field] = str(value)
        self._changed(key)
        return 1

    async def hset(self, key, *, mapping):
        self._check()
        target = self.hashes.setdefault(key, {})
        added = sum(field not in target for field in mapping)
        target.update({field: str(value) for field, value in mapping.items()})
        self._changed(key)
        return added

    async def hget(self, key, field):
        self._check()
        return self._wire(self.hashes.get(key, {}).get(field))

    async def hvals(self, key):
        self._check()
        return [self._wire(value) for value in self.hashes.get(key, {}).values()]

    async def hgetall(self, key):
        self._check()
        return {self._wire(field): self._wire(value) for field, value in self.hashes.get(key, {}).items()}

    async def sadd(self, key, value):
        self._check()
        target = self.sets.setdefault(key, set())
        added = value not in target
        target.add(value)
        self._changed(key)
        return int(added)

    async def smembers(self, key):
        self._check()
        return {self._wire(value) for value in self.sets.get(key, set())}

    async def get(self, key):
        self._check()
        return self._wire(self.strings.get(key))

    async def set(self, key, value):
        self._check()
        self.strings[key] = value
        self._changed(key)
        return True

    async def rpush(self, key, value):
        self._check()
        self.lists.setdefault(key, []).append(value)
        self._changed(key)
        return len(self.lists[key])

    async def lrange(self, key, start, end):
        self._check()
        values = self.lists.get(key, [])
        return [self._wire(value) for value in values[start:None if end == -1 else end + 1]]

    async def lpush(self, key, value):
        self.lists.setdefault(key, []).insert(0, value)
        self._changed(key)
        return len(self.lists[key])

    async def llen(self, key):
        return len(self.lists.get(key, []))

    async def expire(self, key, seconds):
        self.ttls[key] = seconds
        return True

    async def ttl(self, key):
        return self.ttls.get(key, -1)

    async def setex(self, key, seconds, value):
        await self.set(key, value)
        await self.expire(key, seconds)

    async def delete(self, *keys):
        for key in keys:
            for values in (self.hashes, self.sets, self.strings, self.lists, self.ttls):
                values.pop(key, None)
            self._changed(key)
        return len(keys)

    def pipeline(self, transaction=True):
        return MemoryPipeline(self)


class MemoryPipeline:
    def __init__(self, redis):
        self.redis = redis
        self.commands = []
        self.watched = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        self.commands.clear()

    async def watch(self, *keys):
        for key in keys:
            self.watched[key] = self.redis.versions.get(key, 0)

    async def get(self, key):
        return await self.redis.get(key)

    async def smembers(self, key):
        return await self.redis.smembers(key)

    async def lrange(self, key, start, end):
        return await self.redis.lrange(key, start, end)

    async def hgetall(self, key):
        return await self.redis.hgetall(key)

    def multi(self):
        return None

    def hsetnx(self, *args):
        self.commands.append(("hsetnx", args, {}))

    def hset(self, *args, **kwargs):
        self.commands.append(("hset", args, kwargs))

    def sadd(self, *args):
        self.commands.append(("sadd", args, {}))

    def set(self, *args):
        self.commands.append(("set", args, {}))

    def rpush(self, *args):
        self.commands.append(("rpush", args, {}))

    def lpush(self, *args):
        self.commands.append(("lpush", args, {}))

    def expire(self, *args):
        self.commands.append(("expire", args, {}))

    async def execute(self):
        if self.redis.before_execute:
            callback, self.redis.before_execute = self.redis.before_execute, None
            await callback()
        if any(self.redis.versions.get(key, 0) != version for key, version in self.watched.items()):
            raise WatchError("another write")
        return [await getattr(self.redis, name)(*args, **kwargs) for name, args, kwargs in self.commands]


@pytest.fixture
def patients():
    path = Path(__file__).parents[1] / "hospital/demo_data.json"
    return DemoData.model_validate_json(path.read_text(encoding="utf-8")).patients


@pytest.fixture
def clock():
    return SimpleNamespace(value=datetime.fromisoformat("2026-09-16T10:00:00+08:00"))


@pytest_asyncio.fixture
async def store(patients, clock):
    result = VisitStore(MemoryRedis(), prefix="medipet:test:m01:", clock=lambda: clock.value)
    await result.initialize_patients(patients)
    return result


@pytest.mark.asyncio
async def test_initialization_lists_only_current_users_patients_and_resolves_default_self(store):
    patients = await store.list_patients()
    assert [(item.patient_id, item.name) for item in patients] == [("patient_self", "林晓"), ("patient_child", "林小满")]
    assert (await store.default_patient()).patient_id == "patient_self"
    assert await store.list_patients("unknown-user") == []
    with pytest.raises(VisitStoreError) as missing:
        await store.default_patient("unknown-user")
    assert missing.value.code == "not_found"


@pytest.mark.asyncio
async def test_reinitialization_preserves_patient_and_renamed_archived_visit(store, patients, clock):
    visit = await store.create_visit("anonymous", "patient_child", "儿童初诊")
    clock.value += timedelta(minutes=2)
    changed = await store.update_visit("anonymous", visit.conv_id, title="儿童复诊", archived=True)
    replacement = Patient.model_validate({**patients[0].model_dump(), "name": "不应覆盖已有姓名"})
    assert await store.initialize_patients([replacement, patients[1]]) == 0
    assert (await store.default_patient()).name == "林晓"
    restarted = VisitStore(store.redis, prefix=store.prefix, clock=lambda: clock.value)
    assert await restarted.get_visit("anonymous", visit.conv_id) == changed
    assert changed.created_at == visit.created_at and changed.updated_at == clock.value


@pytest.mark.asyncio
async def test_create_visit_uses_stable_patient_binding_and_default_title(store):
    created = await store.create_visit("anonymous", "patient_child")
    assert created.title == DEFAULT_VISIT_TITLE
    assert created.conv_id.startswith("visit-") and len(created.conv_id) == 38
    assert created.patient_id == "patient_child" and created.archived is False
    assert created.created_at == created.updated_at
    assert await store.get_visit("anonymous", created.conv_id) == created
    identity = await store.require_identity("anonymous", created.conv_id)
    assert identity.patient_id == "patient_child"
    with pytest.raises(TypeError):
        await store.update_visit("anonymous", created.conv_id, patient_id="patient_self")
    assert (await store.get_visit("anonymous", created.conv_id)).patient_id == "patient_child"


@pytest.mark.asyncio
async def test_patient_and_visit_access_reject_missing_and_conflicting_identities(store):
    created = await store.create_visit("anonymous", "patient_child")
    cases = [
        (store.get_patient("anonymous", "missing"), "not_found"),
        (store.create_visit("other-user", "patient_child"), "identity_conflict"),
        (store.get_visit("anonymous", "missing"), "not_found"),
        (store.get_visit("other-user", created.conv_id), "identity_conflict"),
        (store.require_identity("anonymous", created.conv_id, patient_id="patient_self"), "identity_conflict"),
        (store.list_visits("other-user", "patient_child"), "identity_conflict"),
        (store.get_visit("anonymous", "../bad"), "invalid_input"),
    ]
    for operation, code in cases:
        with pytest.raises(VisitStoreError) as failure:
            await operation
        assert failure.value.code == code and failure.value.retryable is False


@pytest.mark.asyncio
async def test_visit_lists_filter_patient_and_archive_status_and_sort_latest_first(store, clock):
    first = await store.create_visit("anonymous", "patient_child", "第一次")
    clock.value += timedelta(minutes=1)
    second = await store.create_visit("anonymous", "patient_child", "第二次")
    own = await store.create_visit("anonymous", "patient_self", "本人事项")
    assert [item.conv_id for item in await store.list_visits("anonymous", "patient_child")] == [second.conv_id, first.conv_id]
    assert [item.conv_id for item in await store.list_visits("anonymous", "patient_self")] == [own.conv_id]
    await store.update_visit("anonymous", first.conv_id, archived=True)
    assert [item.conv_id for item in await store.list_visits("anonymous", "patient_child")] == [second.conv_id]
    assert [item.conv_id for item in await store.list_visits("anonymous", "patient_child", archived=True)] == [first.conv_id]


@pytest.mark.asyncio
async def test_archive_allows_reading_but_blocks_actions_until_restored(store):
    visit = await store.create_visit("anonymous", "patient_child")
    await store.update_visit("anonymous", visit.conv_id, archived=True)
    assert (await store.get_visit("anonymous", visit.conv_id)).archived is True
    with pytest.raises(VisitStoreError) as blocked:
        await store.require_identity("anonymous", visit.conv_id)
    assert blocked.value.code == "visit_archived"
    assert (await store.require_identity("anonymous", visit.conv_id, allow_archived=True)).patient_id == "patient_child"
    await store.update_visit("anonymous", visit.conv_id, archived=False)
    assert (await store.require_identity("anonymous", visit.conv_id)).patient_id == "patient_child"


@pytest.mark.asyncio
async def test_invalid_metadata_updates_leave_existing_record_unchanged(store):
    visit = await store.create_visit("anonymous", "patient_self")
    for kwargs, code in (({}, "missing_fields"), ({"title": "   "}, "invalid_input"), ({"archived": "true"}, "invalid_input")):
        with pytest.raises(VisitStoreError) as rejected:
            await store.update_visit("anonymous", visit.conv_id, **kwargs)
        assert rejected.value.code == code
        assert await store.get_visit("anonymous", visit.conv_id) == visit
    with pytest.raises(VisitStoreError) as rejected:
        await store.create_visit("anonymous", "patient_self", title="")
    assert rejected.value.code == "invalid_input"


@pytest.mark.asyncio
async def test_concurrent_metadata_change_returns_retryable_conflict_without_overwriting(store):
    visit = await store.create_visit("anonymous", "patient_self")

    async def concurrent_rename():
        await store.redis.hset(store.visit_key(visit.conv_id), mapping={"title": "另一请求的标题"})

    store.redis.before_execute = concurrent_rename
    with pytest.raises(VisitStoreError) as conflict:
        await store.update_visit("anonymous", visit.conv_id, archived=True)
    assert conflict.value.code == "conflict" and conflict.value.retryable is True
    unchanged = await store.get_visit("anonymous", visit.conv_id)
    assert unchanged.title == "另一请求的标题" and unchanged.archived is False
    retried = await store.update_visit("anonymous", visit.conv_id, archived=True)
    assert retried.title == "另一请求的标题" and retried.archived is True


@pytest.mark.asyncio
async def test_storage_failure_has_stable_error_and_does_not_expose_backend_detail(store):
    store.redis.fail = True
    with pytest.raises(VisitStoreError) as failure:
        await store.list_patients()
    assert failure.value.code == "storage_unavailable" and failure.value.retryable is True
    assert "test backend" not in failure.value.message


@pytest.mark.asyncio
async def test_binary_redis_responses_and_independent_prefixes_are_supported(patients, clock):
    redis = MemoryRedis(decode_responses=False)
    first = VisitStore(redis, prefix="medipet:test:first", clock=lambda: clock.value)
    second = VisitStore(redis, prefix="medipet:test:second", clock=lambda: clock.value)
    await first.initialize_patients(patients)
    await second.initialize_patients(patients)
    visit = await first.create_visit("anonymous", "patient_self")
    assert first.redis is second.redis and first.prefix.endswith(":")
    assert (await first.list_visits("anonymous", "patient_self"))[0] == visit
    assert await second.list_visits("anonymous", "patient_self") == []


@pytest.mark.asyncio
async def test_clock_is_injected_and_converted_to_shanghai_without_changing_creation_time(store, clock):
    clock.value = datetime.fromisoformat("2026-09-16T02:00:00+00:00")
    visit = await store.create_visit("anonymous", "patient_self")
    assert visit.created_at.isoformat() == "2026-09-16T10:00:00+08:00"
    clock.value -= timedelta(hours=1)
    updated = await store.update_visit("anonymous", visit.conv_id, title="改名")
    assert updated.updated_at == visit.updated_at and updated.created_at == visit.created_at


def sample_message(visit, clock, message_id="message-1", **changes):
    return VisitMessage(**{key: getattr(visit, key) for key in ("user_id", "patient_id", "conv_id")},
                        message_id=message_id, role="assistant", content="已找到就诊资料。",
                        created_at=clock.value, **changes)


def sample_selection(visit, clock):
    from hospital.service import HospitalService
    slot = HospitalService(clock=lambda: clock.value)._window_slots(clock.value)[0]
    return SelectionState(**{key: getattr(visit, key) for key in ("user_id", "patient_id", "conv_id")},
                          status="ready", list_id="list-1", query=SlotQuery(date=slot.date),
                          slots=[slot], queried_at=clock.value)


@pytest.mark.asyncio
async def test_complete_history_preserves_cards_order_and_idempotent_receipt_ids(store, clock):
    visit = await store.create_visit("anonymous", "patient_child")
    from hospital.service import HospitalService
    card = HospitalService(clock=lambda: clock.value).get_visit_checklist().artifacts[0]
    messages = [sample_message(visit, clock),
                sample_message(visit, clock, "confirm:proposal-1", kind="confirmation_event", proposal_id="proposal-1"),
                sample_message(visit, clock, "result:proposal-1", kind="operation_result", proposal_id="proposal-1",
                               receipt_id="receipt:proposal-1", artifacts=[card])]
    assert await store.append_messages("anonymous", visit.conv_id, messages) == 3
    assert await store.append_messages("anonymous", visit.conv_id, messages[1:]) == 0
    restarted = VisitStore(store.redis, prefix=store.prefix)
    assert await restarted.get_messages("anonymous", visit.conv_id) == messages
    # 工作记忆是独立键；删除或裁剪不触碰完整消息。
    store.redis.lists["working-memory-key"] = []
    assert await restarted.get_messages("anonymous", visit.conv_id) == messages


@pytest.mark.asyncio
async def test_history_and_selections_are_isolated_and_archive_read_only(store, clock):
    own = await store.create_visit("anonymous", "patient_self")
    child = await store.create_visit("anonymous", "patient_child")
    another = await store.create_visit("anonymous", "patient_child")
    await store.append_messages("anonymous", child.conv_id, [sample_message(child, clock)])
    selection = await store.save_selection(sample_selection(child, clock))
    assert await store.get_messages("anonymous", own.conv_id) == []
    assert await store.get_messages("anonymous", another.conv_id) == []
    assert (await store.get_selection("anonymous", own.conv_id)).status == "empty"
    with pytest.raises(VisitStoreError, match="身份不一致"):
        await store.append_messages("anonymous", own.conv_id, [sample_message(child, clock)])
    with pytest.raises(VisitStoreError) as rejected:
        await store.save_selection(selection.model_copy(update={"patient_id": "patient_self"}))
    assert rejected.value.code == "identity_conflict"
    await store.update_visit("anonymous", child.conv_id, archived=True)
    assert len(await store.get_messages("anonymous", child.conv_id)) == 1
    assert await store.get_selection("anonymous", child.conv_id) == selection
    for operation in (store.append_messages("anonymous", child.conv_id, [sample_message(child, clock, "new")]),
                      store.save_selection(selection), store.resolve_selection("anonymous", child.conv_id, 1)):
        with pytest.raises(VisitStoreError) as archived:
            await operation
        assert archived.value.code == "visit_archived"
    await store.update_visit("anonymous", child.conv_id, archived=False)
    assert (await store.resolve_selection("anonymous", child.conv_id, 1)).slot_id == selection.slots[0].slot_id


@pytest.mark.asyncio
async def test_selection_empty_failure_and_query_refresh_keep_current_proposal(store, clock):
    visit = await store.create_visit("anonymous", "patient_self")
    ready = await store.save_selection(sample_selection(visit, clock))
    async with store.redis.pipeline(transaction=True) as pipe:
        pipe.multi()
        store.queue_selection(pipe, ready.model_copy(update={"current_proposal_id": "proposal-1"}))
        await pipe.execute()
    for index in (0, 2, True, "1"):
        with pytest.raises(VisitStoreError) as bad:
            await store.resolve_selection("anonymous", visit.conv_id, index)
        assert bad.value.code == "selection_unavailable"
    for status in ("empty", "failed"):
        state = ready.model_copy(update={"status": status, "slots": [], "list_id": None})
        saved = await store.save_selection(state)
        assert saved.current_proposal_id == "proposal-1" and saved.query == ready.query
        with pytest.raises(VisitStoreError) as no_list:
            await store.resolve_selection("anonymous", visit.conv_id, 1)
        assert no_list.value.code == "selection_unavailable"


@pytest.mark.asyncio
async def test_concurrent_archive_and_current_proposal_writes_are_not_overwritten(store, clock):
    visit = await store.create_visit("anonymous", "patient_self")
    ready = sample_selection(visit, clock)

    async def change_reference():
        await store.redis.set(store.selection_key(visit.conv_id), ready.model_copy(update={"current_proposal_id": "proposal-new"}).model_dump_json())

    store.redis.before_execute = change_reference
    with pytest.raises(VisitStoreError) as conflict:
        await store.save_selection(ready)
    assert conflict.value.code == "conflict" and conflict.value.retryable
    assert (await store.save_selection(ready)).current_proposal_id == "proposal-new"

    async def archive():
        await store.redis.hset(store.visit_key(visit.conv_id), mapping={"archived": "1"})

    store.redis.before_execute = archive
    with pytest.raises(VisitStoreError) as conflict:
        await store.append_messages("anonymous", visit.conv_id, [sample_message(visit, clock)])
    assert conflict.value.code == "conflict"
    assert await store.get_messages("anonymous", visit.conv_id) == []


@pytest.mark.asyncio
@pytest.mark.skipif(not os.getenv("MEDIPET_VISIT_TEST_REDIS_URL"), reason="显式启用真实 Redis DB15")
async def test_real_redis_history_selection_and_dedup_survive_restart(patients, clock):
    client = Redis.from_url(os.environ["MEDIPET_VISIT_TEST_REDIS_URL"], decode_responses=False)
    assert client.connection_pool.connection_kwargs.get("db") == 15
    prefix = f"medipet:test:m02:{uuid4().hex}:"
    store = VisitStore(client, prefix=prefix, clock=lambda: clock.value)
    try:
        await store.initialize_patients(patients)
        visit = await store.create_visit("anonymous", "patient_child")
        message = sample_message(visit, clock)
        assert await store.append_messages("anonymous", visit.conv_id, [message, message]) == 1
        state = await store.save_selection(sample_selection(visit, clock))
        restarted = VisitStore(client, prefix=prefix, clock=lambda: clock.value)
        assert await restarted.append_messages("anonymous", visit.conv_id, [message]) == 0
        assert await restarted.get_messages("anonymous", visit.conv_id) == [message]
        assert await restarted.get_selection("anonymous", visit.conv_id) == state
        assert await restarted.resolve_selection("anonymous", visit.conv_id, 1) == state.slots[0]
        keys = [key async for key in client.scan_iter(match=f"{prefix}*")]
        assert all([await client.ttl(key) == -1 for key in keys])
    finally:
        keys = [key async for key in client.scan_iter(match=f"{prefix}*")]
        if keys:
            await client.delete(*keys)
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.skipif(not os.getenv("MEDIPET_VISIT_TEST_REDIS_URL"), reason="设置专用 DB15 Redis URL 后执行真实存储核验")
async def test_real_redis_metadata_survives_new_store_and_has_no_ttl(patients, clock):
    client = Redis.from_url(os.environ["MEDIPET_VISIT_TEST_REDIS_URL"], decode_responses=False)
    assert client.connection_pool.connection_kwargs.get("db") == 15
    prefix = f"medipet:test:m01:{uuid4().hex}:"
    store = VisitStore(client, prefix=prefix, clock=lambda: clock.value)
    try:
        assert await store.initialize_patients(patients) == 2
        visit = await store.create_visit("anonymous", "patient_child", "真实存储验证")
        archived = await store.update_visit("anonymous", visit.conv_id, title="保留此标题", archived=True)
        restarted = VisitStore(client, prefix=prefix, clock=lambda: clock.value)
        assert await restarted.initialize_patients(patients) == 0
        assert await restarted.get_visit("anonymous", visit.conv_id) == archived
        assert await restarted.list_visits("anonymous", "patient_child") == []
        assert (await restarted.list_visits("anonymous", "patient_child", archived=True))[0] == archived
        with pytest.raises(VisitStoreError) as blocked:
            await restarted.require_identity("anonymous", visit.conv_id)
        assert blocked.value.code == "visit_archived"
        await restarted.update_visit("anonymous", visit.conv_id, archived=False)
        assert (await restarted.require_identity("anonymous", visit.conv_id)).patient_id == "patient_child"
        keys = [key async for key in client.scan_iter(match=f"{prefix}*")]
        assert len(keys) == 3
        assert all([await client.ttl(key) == -1 for key in keys])
    finally:
        keys = [key async for key in client.scan_iter(match=f"{prefix}*")]
        if keys:
            await client.delete(*keys)
        await client.aclose()
