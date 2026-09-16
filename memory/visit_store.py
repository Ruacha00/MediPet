"""预置就诊人、事项、完整消息和选择上下文的 Redis 存储。

调用方提供并管理 Redis 客户端生命周期。redis、prefix 和 visit_key() 可供
医院服务共享连接及 WATCH 事务；API/Agent 不自行拼接这里的业务键。
"""

from collections.abc import Callable, Sequence
from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import TypeAdapter, ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError, WatchError

from hospital.models import (
    BUSINESS_TIMEZONE,
    DEFAULT_USER_ID,
    ErrorCode,
    Identifier,
    Patient,
    SelectionState,
    Slot,
    Visit,
    VisitIdentity,
    VisitMessage,
)


_IDENTIFIER = TypeAdapter(Identifier)
DEFAULT_VISIT_TITLE = "新的就诊事项"


class VisitStoreError(Exception):
    """由 API 映射为 BusinessError，或由工具映射为 ServiceResult 失败。"""

    def __init__(self, code: ErrorCode, message: str, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class VisitStore:
    def __init__(
        self, redis: Redis, *, prefix: str = "medipet:",
        clock: Callable[[], datetime] | None = None,
    ):
        if not prefix:
            raise ValueError("事项存储必须使用非空 Redis 前缀")
        self.redis = redis
        self.prefix = prefix if prefix.endswith(":") else f"{prefix}:"
        self._clock = clock or (lambda: datetime.now(ZoneInfo(BUSINESS_TIMEZONE)))
        self._patients_key = f"{self.prefix}patients"

    def now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("业务时钟必须携带时区")
        return value.astimezone(ZoneInfo(BUSINESS_TIMEZONE))

    def visit_key(self, conv_id: str) -> str:
        """逐事项 hash 的键；医院事务可以 WATCH，不能借此改写身份字段。"""
        return f"{self.prefix}visit:{self._identifier(conv_id)}"

    def _visits_key(self, patient_id: str) -> str:
        return f"{self.prefix}visits:{self._identifier(patient_id)}"

    def messages_key(self, conv_id: str) -> str:
        return f"{self.prefix}messages:{self._identifier(conv_id)}"

    def selection_key(self, conv_id: str) -> str:
        """医院事务必须 WATCH 此键，再用 queue_selection 在同一事务切换方案引用。"""
        return f"{self.prefix}selection:{self._identifier(conv_id)}"

    @staticmethod
    def decode_selection(identity: VisitIdentity, raw: str | bytes | None) -> SelectionState:
        if raw is None:
            return SelectionState(**identity.model_dump())
        try:
            state = SelectionState.model_validate_json(raw)
        except ValidationError as exc:
            raise VisitStore._storage_error() from exc
        if any(getattr(state, key) != getattr(identity, key) for key in ("user_id", "patient_id", "conv_id")):
            raise VisitStoreError("identity_conflict", "选择记录与当前事项身份不一致。")
        return state

    def queue_selection(self, pipe, state: SelectionState) -> None:
        """只排入调用方已经开启的事务；不执行、不独立提交当前方案引用。"""
        pipe.set(self.selection_key(state.conv_id), state.model_dump_json())

    async def get_selection(self, user_id: str, conv_id: str) -> SelectionState:
        identity = await self.require_identity(user_id, conv_id, allow_archived=True)
        try:
            raw = await self.redis.get(self.selection_key(conv_id))
        except RedisError as exc:
            raise self._storage_error() from exc
        return self.decode_selection(identity, raw)

    async def save_selection(self, state: SelectionState) -> SelectionState:
        """保存本次查询的实际列表；保留医院事务持有的当前方案引用。"""
        key = self.selection_key(state.conv_id)
        try:
            async with self.redis.pipeline(transaction=True) as pipe:
                await pipe.watch(self.visit_key(state.conv_id), key)
                identity = await self.require_identity(state.user_id, state.conv_id, patient_id=state.patient_id)
                previous = self.decode_selection(identity, await pipe.get(key))
                saved = state.model_copy(update={"current_proposal_id": previous.current_proposal_id})
                pipe.multi()
                self.queue_selection(pipe, saved)
                await pipe.execute()
        except WatchError as exc:
            raise VisitStoreError("conflict", "选择列表已更新，请重试本次查询。", retryable=True) from exc
        except RedisError as exc:
            raise self._storage_error() from exc
        return saved

    async def resolve_selection(self, user_id: str, conv_id: str, index: int) -> Slot:
        """一基序号只引用最近成功查询的真实列表；执行前仍须重查库存。"""
        await self.require_identity(user_id, conv_id)
        state = await self.get_selection(user_id, conv_id)
        if type(index) is not int or state.status != "ready" or not 1 <= index <= len(state.slots):
            raise VisitStoreError("selection_unavailable", "请选择最近号源列表中的有效序号，或重新查询号源。")
        return state.slots[index - 1]

    async def append_messages(self, user_id: str, conv_id: str, messages: Sequence[VisitMessage]) -> int:
        """按传入顺序原子追加，稳定 message_id 去重；没有工作记忆 TTL 或裁剪。"""
        identity = await self.require_identity(user_id, conv_id)
        for message in messages:
            if any(getattr(message, key) != getattr(identity, key) for key in ("user_id", "patient_id", "conv_id")):
                raise VisitStoreError("identity_conflict", "消息与当前事项身份不一致。")
        ids_key = f"{self.messages_key(conv_id)}:ids"
        try:
            async with self.redis.pipeline(transaction=True) as pipe:
                await pipe.watch(self.visit_key(conv_id), ids_key)
                await self.require_identity(user_id, conv_id)
                current_visit = await self.get_visit(user_id, conv_id)
                seen = {self._text(value) for value in await pipe.smembers(ids_key)}
                pending = []
                for message in messages:
                    if message.message_id not in seen:
                        seen.add(message.message_id)
                        pending.append(message)
                if not pending:
                    return 0
                pipe.multi()
                for message in pending:
                    pipe.rpush(self.messages_key(conv_id), message.model_dump_json())
                    pipe.sadd(ids_key, message.message_id)
                pipe.hset(self.visit_key(conv_id), mapping={"updated_at": max(self.now(), current_visit.updated_at).isoformat()})
                await pipe.execute()
        except WatchError as exc:
            raise VisitStoreError("conflict", "事项消息已更新，请重试。", retryable=True) from exc
        except RedisError as exc:
            raise self._storage_error() from exc
        return len(pending)

    async def get_messages(self, user_id: str, conv_id: str) -> list[VisitMessage]:
        identity = await self.require_identity(user_id, conv_id, allow_archived=True)
        try:
            messages = [VisitMessage.model_validate_json(raw) for raw in await self.redis.lrange(self.messages_key(conv_id), 0, -1)]
        except (RedisError, ValidationError) as exc:
            raise self._storage_error() from exc
        if any(any(getattr(message, key) != getattr(identity, key) for key in ("user_id", "patient_id", "conv_id")) for message in messages):
            raise VisitStoreError("identity_conflict", "消息记录与当前事项身份不一致。")
        return messages

    async def initialize_patients(self, patients: Sequence[Patient]) -> int:
        """仅 HSETNX 缺失的预置患者，返回新增数量；不遍历或重写事项。"""
        try:
            validated = [Patient.model_validate(item) for item in patients]
        except ValidationError as exc:
            raise VisitStoreError("invalid_input", "预置就诊人资料不符合契约。") from exc
        if not validated:
            return 0
        try:
            async with self.redis.pipeline(transaction=True) as pipe:
                for patient in validated:
                    pipe.hsetnx(self._patients_key, patient.patient_id, patient.model_dump_json())
                results = await pipe.execute()
        except RedisError as exc:
            raise self._storage_error() from exc
        return sum(int(added) for added in results)

    async def list_patients(self, user_id: str = DEFAULT_USER_ID) -> list[Patient]:
        user_id = self._identifier(user_id)
        try:
            values = await self.redis.hvals(self._patients_key)
            patients = [Patient.model_validate_json(raw) for raw in values]
        except (RedisError, ValidationError) as exc:
            raise self._storage_error() from exc
        return sorted(
            (patient for patient in patients if patient.user_id == user_id),
            key=lambda patient: (patient.relationship != "self", patient.patient_id),
        )

    async def get_patient(self, user_id: str, patient_id: str) -> Patient:
        user_id, patient_id = self._identifier(user_id), self._identifier(patient_id)
        try:
            raw = await self.redis.hget(self._patients_key, patient_id)
            patient = Patient.model_validate_json(raw) if raw is not None else None
        except (RedisError, ValidationError) as exc:
            raise self._storage_error() from exc
        if patient is None:
            raise VisitStoreError("not_found", "未找到该就诊人。")
        if patient.user_id != user_id:
            raise VisitStoreError("identity_conflict", "该就诊人不属于当前演示参与者。")
        return patient

    async def default_patient(self, user_id: str = DEFAULT_USER_ID) -> Patient:
        patients = [patient for patient in await self.list_patients(user_id) if patient.relationship == "self"]
        if len(patients) != 1:
            raise VisitStoreError("not_found", "未配置唯一的本人就诊人，请明确选择就诊人。")
        return patients[0]

    async def create_visit(self, user_id: str, patient_id: str, title: str | None = None) -> Visit:
        patient = await self.get_patient(user_id, patient_id)
        now = self.now()
        try:
            visit = Visit(
                user_id=patient.user_id, patient_id=patient.patient_id,
                conv_id=f"visit-{uuid4().hex}",
                title=DEFAULT_VISIT_TITLE if title is None else title,
                created_at=now, updated_at=now,
            )
        except ValidationError as exc:
            raise VisitStoreError("invalid_input", "事项标题不能为空且必须为文字。") from exc
        fields = visit.model_dump(mode="json")
        fields["archived"] = "0"
        try:
            async with self.redis.pipeline(transaction=True) as pipe:
                pipe.hset(self.visit_key(visit.conv_id), mapping=fields)
                pipe.sadd(self._visits_key(patient.patient_id), visit.conv_id)
                await pipe.execute()
        except RedisError as exc:
            raise self._storage_error() from exc
        return visit

    async def get_visit(self, user_id: str, conv_id: str) -> Visit:
        """允许读取已归档事项；需要继续操作时调用 require_identity()。"""
        user_id, conv_id = self._identifier(user_id), self._identifier(conv_id)
        try:
            fields = await self.redis.hgetall(self.visit_key(conv_id))
        except RedisError as exc:
            raise self._storage_error() from exc
        return await self._loaded_visit(user_id, conv_id, fields)

    async def list_visits(self, user_id: str, patient_id: str, *, archived: bool = False) -> list[Visit]:
        await self.get_patient(user_id, patient_id)
        if type(archived) is not bool:
            raise VisitStoreError("invalid_input", "归档筛选必须为布尔值。")
        try:
            ids = await self.redis.smembers(self._visits_key(patient_id))
        except RedisError as exc:
            raise self._storage_error() from exc
        visits = []
        for value in ids:
            visit = await self.get_visit(user_id, self._text(value))
            if visit.patient_id != patient_id:
                raise VisitStoreError("identity_conflict", "事项所属患者与列表范围不一致。")
            if visit.archived == archived:
                visits.append(visit)
        return sorted(visits, key=lambda visit: (visit.updated_at, visit.conv_id), reverse=True)

    async def update_visit(
        self, user_id: str, conv_id: str, *, title: str | None = None,
        archived: bool | None = None,
    ) -> Visit:
        """只更新标题/归档状态；WATCH 冲突交由调用者明确重试，不覆盖另一请求。"""
        user_id, conv_id = self._identifier(user_id), self._identifier(conv_id)
        if title is None and archived is None:
            raise VisitStoreError("missing_fields", "请提供新的事项标题或归档状态。")
        if archived is not None and type(archived) is not bool:
            raise VisitStoreError("invalid_input", "归档状态必须为布尔值。")
        try:
            async with self.redis.pipeline(transaction=True) as pipe:
                await pipe.watch(self.visit_key(conv_id))
                fields = await pipe.hgetall(self.visit_key(conv_id))
                visit = await self._loaded_visit(user_id, conv_id, fields)
                changes = {"updated_at": max(self.now(), visit.updated_at)}
                if title is not None:
                    changes["title"] = title
                if archived is not None:
                    changes["archived"] = archived
                try:
                    updated = Visit.model_validate({**visit.model_dump(), **changes})
                except ValidationError as exc:
                    raise VisitStoreError("invalid_input", "事项标题不能为空且必须为文字。") from exc
                wire = updated.model_dump(mode="json")
                patch = {key: wire[key] for key in changes}
                if archived is not None:
                    patch["archived"] = "1" if archived else "0"
                pipe.multi()
                pipe.hset(self.visit_key(conv_id), mapping=patch)
                await pipe.execute()
        except WatchError as exc:
            raise VisitStoreError("conflict", "事项已被其他请求修改，请刷新后重试。", retryable=True) from exc
        except RedisError as exc:
            raise self._storage_error() from exc
        return updated

    async def require_identity(
        self, user_id: str, conv_id: str, *, patient_id: str | None = None,
        allow_archived: bool = False,
    ) -> VisitIdentity:
        """加载服务端绑定身份；默认拒绝归档事项，显式患者参数只能用于比对。"""
        visit = await self.get_visit(user_id, conv_id)
        if patient_id is not None and self._identifier(patient_id) != visit.patient_id:
            raise VisitStoreError("identity_conflict", "当前事项绑定的就诊人与请求不一致。")
        if visit.archived and not allow_archived:
            raise VisitStoreError("visit_archived", "请先恢复已归档事项，再继续办理。")
        return VisitIdentity(user_id=visit.user_id, patient_id=visit.patient_id, conv_id=visit.conv_id)

    async def _loaded_visit(self, user_id: str, conv_id: str, fields: dict) -> Visit:
        if not fields:
            raise VisitStoreError("not_found", "未找到该就诊事项。")
        try:
            visit = Visit.model_validate({self._text(key): self._text(value) for key, value in fields.items()})
        except ValidationError as exc:
            raise self._storage_error() from exc
        if visit.conv_id != conv_id:
            raise self._storage_error()
        if visit.user_id != user_id:
            raise VisitStoreError("identity_conflict", "该事项不属于当前演示参与者。")
        await self.get_patient(user_id, visit.patient_id)
        return visit

    @staticmethod
    def _identifier(value: str) -> str:
        try:
            return _IDENTIFIER.validate_python(value)
        except ValidationError as exc:
            raise VisitStoreError("invalid_input", "参与者、就诊人或事项编号格式不正确。") from exc

    @staticmethod
    def _text(value):
        return value.decode("utf-8") if isinstance(value, bytes) else value

    @staticmethod
    def _storage_error() -> VisitStoreError:
        return VisitStoreError("storage_unavailable", "就诊事项存储暂时不可用，请稍后重试。", retryable=True)
