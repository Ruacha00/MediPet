"""明和虚构医院的具体业务服务，事实来自 demo_data.json。"""

from datetime import date as Date, datetime, timedelta
from pathlib import Path
from typing import Callable
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from redis.exceptions import RedisError, WatchError

from hospital.models import (
    Artifact,
    AppointmentProposal,
    AppointmentRecord,
    AppointmentSnapshot,
    BUSINESS_TIMEZONE,
    CatalogData,
    DemoData,
    DEFAULT_PROPOSAL_TTL_SECONDS,
    ExecutionReceipt,
    ServiceResult,
    Slot,
    SlotDetails,
    SlotList,
    SlotQuery,
    VisitIdentity,
)
from hospital.store import HospitalStore
from memory.visit_store import VisitStore, VisitStoreError


class HospitalService:
    def __init__(
        self, data: DemoData | None = None, clock: Callable[[], datetime] | None = None,
        store: HospitalStore | None = None,
        visit_store: VisitStore | None = None,
        proposal_ttl_seconds: int = DEFAULT_PROPOSAL_TTL_SECONDS,
    ):
        self.data = data or DemoData.model_validate_json(
            Path(__file__).with_name("demo_data.json").read_text(encoding="utf-8")
        )
        self._clock = clock or (lambda: datetime.now(ZoneInfo(BUSINESS_TIMEZONE)))
        self.store = store
        self.visits = visit_store or (VisitStore(store.redis, prefix=store.prefix, clock=self._clock) if store else None)
        if self.visits and store and (self.visits.redis is not store.redis or self.visits.prefix != store.prefix):
            raise ValueError("事项和医院存储必须共享 Redis 客户端及前缀，以便同一事务更新")
        if type(proposal_ttl_seconds) is not int or proposal_ttl_seconds <= 0:
            raise ValueError("方案有效期必须为正整数秒")
        self.proposal_ttl_seconds = proposal_ttl_seconds

    async def _require_identity(self, identity: VisitIdentity):
        if self.store is None or self.visits is None:
            raise VisitStoreError("storage_unavailable", "预约存储尚未连接。", retryable=True)
        return await self.visits.require_identity(identity.user_id, identity.conv_id, patient_id=identity.patient_id)

    async def prepare_appointment(
        self, identity: VisitIdentity, slot_id: str | None = None, selection_index: int | None = None,
    ) -> ServiceResult:
        """仅准备确认资料；序号与当前列表在同一 WATCH 范围内读取。"""
        try:
            await self._require_identity(identity)
            if (slot_id is None) == (selection_index is None):
                return self._failure("missing_fields", "请提供一个号源编号，或最近列表中的一个序号。")
            async with self.store.redis.pipeline(transaction=True) as pipe:
                await pipe.watch(self.visits.visit_key(identity.conv_id), self.visits.selection_key(identity.conv_id))
                await self._require_identity(identity)
                state = self.visits.decode_selection(identity, await pipe.get(self.visits.selection_key(identity.conv_id)))
                if selection_index is not None:
                    if type(selection_index) is not int or state.status != "ready" or not 1 <= selection_index <= len(state.slots):
                        return self._failure("selection_unavailable", "请先查询号源，再选择列表中的有效序号。")
                    slot_id = state.slots[selection_index - 1].slot_id
                await pipe.watch(self.store.slot_key(slot_id))
                raw = await pipe.get(self.store.slot_key(slot_id))
                slot = Slot.model_validate_json(raw) if raw else None
                if slot is None:
                    return self._failure("not_found", "未找到该号源，请重新查询。")
                now = self.now()
                if not self._slot_available(slot, now):
                    return self._failure("target_unavailable", "该号源已无余量或已过就诊时段，请重新选择。")
                patient = await self.visits.get_patient(identity.user_id, identity.patient_id)
                snapshot = AppointmentSnapshot(patient_name=patient.name, slot=SlotDetails.model_validate(slot.model_dump(exclude={"capacity", "remaining"})))
                proposal = self._new_proposal(identity, "create", slot.slot_id, snapshot, now)
                await self._replace_proposal(pipe, state, proposal)
            return self._proposal_result(proposal)
        except (VisitStoreError, RedisError, ValidationError) as exc:
            return self._business_error(exc)

    async def prepare_cancellation(self, identity: VisitIdentity, appointment_id: str) -> ServiceResult:
        try:
            await self._require_identity(identity)
            async with self.store.redis.pipeline(transaction=True) as pipe:
                await pipe.watch(self.visits.visit_key(identity.conv_id), self.visits.selection_key(identity.conv_id), self.store.appointment_key(appointment_id))
                await self._require_identity(identity)
                raw = await pipe.get(self.store.appointment_key(appointment_id))
                record = AppointmentRecord.model_validate_json(raw) if raw else None
                if record is None:
                    return self._failure("not_found", "未找到该预约记录。")
                self._check_owner(record, identity, same_visit=False)
                if record.status != "active":
                    return self._failure("target_unavailable", "该预约已取消，无需再次取消。")
                state = self.visits.decode_selection(identity, await pipe.get(self.visits.selection_key(identity.conv_id)))
                proposal = self._new_proposal(identity, "cancel", record.appointment_id, record.snapshot, self.now())
                await self._replace_proposal(pipe, state, proposal)
            return self._proposal_result(proposal)
        except (VisitStoreError, RedisError, ValidationError) as exc:
            return self._business_error(exc)

    async def get_appointments(self, identity: VisitIdentity, status: str | None = None) -> ServiceResult:
        try:
            await self._require_identity(identity)
            if status is not None and status not in {"active", "cancelled"}:
                return self._failure("invalid_input", "预约状态应为有效或已取消。")
            records = await self.store.get_appointments(identity.user_id, identity.patient_id)
            for record in records:
                self._check_owner(record, identity, same_visit=False)
            records = sorted((r for r in records if status is None or r.status == status), key=lambda r: (r.created_at, r.appointment_id), reverse=True)
            return ServiceResult(success=True, data={"items": [r.model_dump(mode="json") for r in records]},
                                 artifacts=[self._record_artifact(r, r.status) for r in records])
        except (VisitStoreError, RedisError, ValidationError) as exc:
            return self._business_error(exc)

    async def get_current_proposal(self, identity: VisitIdentity) -> ServiceResult:
        try:
            await self._require_identity(identity)
            async with self.store.redis.pipeline(transaction=True) as pipe:
                await pipe.watch(self.visits.visit_key(identity.conv_id), self.visits.selection_key(identity.conv_id))
                await self._require_identity(identity)
                state = self.visits.decode_selection(identity, await pipe.get(self.visits.selection_key(identity.conv_id)))
                if state.current_proposal_id is None:
                    return self._failure("not_found", "当前事项没有待确认方案。")
                key = self.store.proposal_key(state.current_proposal_id)
                await pipe.watch(key)
                raw = await pipe.get(key)
                proposal = AppointmentProposal.model_validate_json(raw) if raw else None
                if proposal is None:
                    return self._failure("not_found", "未找到当前方案，请重新选择。")
                self._check_owner(proposal, identity)
                if proposal.status == "pending" and self.now() >= proposal.expires_at:
                    proposal = proposal.model_copy(update={"status": "expired"})
                    pipe.multi()
                    pipe.set(key, proposal.model_dump_json())
                    self.visits.queue_selection(pipe, state.model_copy(update={"current_proposal_id": None}))
                    await pipe.execute()
                    return self._failure("proposal_expired", "确认资料已过期，请重新查询并选择。")
                return self._proposal_result(proposal)
        except (VisitStoreError, RedisError, ValidationError) as exc:
            return self._business_error(exc)

    async def _replace_proposal(self, pipe, state, proposal: AppointmentProposal):
        previous = None
        if state.current_proposal_id:
            previous_key = self.store.proposal_key(state.current_proposal_id)
            await pipe.watch(previous_key)
            raw = await pipe.get(previous_key)
            previous = AppointmentProposal.model_validate_json(raw) if raw else None
            if previous:
                self._check_owner(previous, proposal)
        pipe.multi()
        if previous and previous.status == "pending":
            pipe.set(previous_key, previous.model_copy(update={"status": "superseded"}).model_dump_json())
        pipe.set(self.store.proposal_key(proposal.proposal_id), proposal.model_dump_json())
        self.visits.queue_selection(pipe, state.model_copy(update={"current_proposal_id": proposal.proposal_id}))
        await pipe.execute()

    async def confirm_proposal(self, identity: VisitIdentity, proposal_id: str) -> ServiceResult:
        """只供显式确认入口调用；事务一起保存库存、预约、方案和原始回执。"""
        try:
            await self._require_identity(identity)
            proposal_key = self.store.proposal_key(proposal_id)
            async with self.store.redis.pipeline(transaction=True) as pipe:
                await pipe.watch(proposal_key, self.visits.visit_key(identity.conv_id), self.visits.selection_key(identity.conv_id))
                await self._require_identity(identity)
                raw = await pipe.get(proposal_key)
                proposal = AppointmentProposal.model_validate_json(raw) if raw else None
                if proposal is None:
                    return self._failure("not_found", "未找到该确认方案。")
                self._check_owner(proposal, identity)
                # 已执行回执永远从原方案读取，不因后来取消或到期改写其历史快照。
                if proposal.status == "executed":
                    return self._receipt_result(proposal)
                if proposal.status == "superseded":
                    return self._failure("proposal_superseded", "已经选择了新方案，请查看最新确认资料。")
                if proposal.status == "expired":
                    return self._failure("proposal_expired", "确认资料已过期，请重新选择。")
                state = self.visits.decode_selection(identity, await pipe.get(self.visits.selection_key(identity.conv_id)))
                if state.current_proposal_id != proposal.proposal_id:
                    return self._failure("proposal_superseded", "该方案已不是当前待确认方案。")
                now = self.now()
                if now >= proposal.expires_at:
                    pipe.multi()
                    pipe.set(proposal_key, proposal.model_copy(update={"status": "expired"}).model_dump_json())
                    self.visits.queue_selection(pipe, state.model_copy(update={"current_proposal_id": None}))
                    await pipe.execute()
                    return self._failure("proposal_expired", "确认资料已过期，请重新查询并选择。")

                slot_key = self.store.slot_key(proposal.snapshot.slot.slot_id)
                await pipe.watch(slot_key)
                raw_slot = await pipe.get(slot_key)
                slot = Slot.model_validate_json(raw_slot) if raw_slot else None
                if slot is None:
                    return self._failure("target_unavailable", "对应号源已经不可用，请重新查询。")
                if SlotDetails.model_validate(slot.model_dump(exclude={"capacity", "remaining"})) != proposal.snapshot.slot:
                    return self._failure("target_unavailable", "号源资料已变化，请重新查看并确认新方案。")
                if proposal.operation == "create":
                    if not self._slot_available(slot, now):
                        return self._failure("target_unavailable", "该号源已无余量或已过就诊时段，请重新选择。")
                    record = AppointmentRecord(**identity.model_dump(), appointment_id=f"appointment-{uuid4().hex}",
                                               snapshot=proposal.snapshot, status="active", created_at=now)
                    updated_slot = slot.model_copy(update={"remaining": slot.remaining - 1})
                else:
                    appointment_key = self.store.appointment_key(proposal.target_id)
                    await pipe.watch(appointment_key)
                    raw_record = await pipe.get(appointment_key)
                    previous = AppointmentRecord.model_validate_json(raw_record) if raw_record else None
                    if previous is None:
                        return self._failure("not_found", "未找到该预约记录。")
                    self._check_owner(previous, identity, same_visit=False)
                    if previous.status != "active":
                        return self._failure("target_unavailable", "该预约已取消，无需再次取消。")
                    if previous.snapshot != proposal.snapshot or slot.remaining >= slot.capacity:
                        return self._failure("target_unavailable", "预约或号源资料已变化，请重新查询。")
                    record = AppointmentRecord.model_validate({**previous.model_dump(), "status": "cancelled", "cancelled_at": now})
                    updated_slot = slot.model_copy(update={"remaining": slot.remaining + 1})
                receipt = ExecutionReceipt(**identity.model_dump(), receipt_id=f"receipt:{proposal.proposal_id}",
                                           proposal_id=proposal.proposal_id, operation=proposal.operation,
                                           appointment=record, executed_at=now)
                executed = AppointmentProposal.model_validate({**proposal.model_dump(), "status": "executed", "result": receipt})
                pipe.multi()
                pipe.set(slot_key, updated_slot.model_dump_json())
                pipe.set(self.store.appointment_key(record.appointment_id), record.model_dump_json())
                pipe.sadd(self.store.appointments_key(identity.user_id, identity.patient_id), record.appointment_id)
                pipe.set(proposal_key, executed.model_dump_json())
                self.visits.queue_selection(pipe, state.model_copy(update={"current_proposal_id": None}))
                await pipe.execute()
                return self._receipt_result(executed)
        except (VisitStoreError, RedisError, ValidationError) as exc:
            return self._business_error(exc)

    def _receipt_result(self, proposal):
        receipt = proposal.result
        return ServiceResult(success=True, data=receipt.model_dump(mode="json"),
                             artifacts=[self._proposal_result(proposal).artifacts[0],
                                        self._record_artifact(receipt.appointment, receipt.receipt_id)])

    def _new_proposal(self, identity, operation, target_id, snapshot, now):
        return AppointmentProposal(**identity.model_dump(), proposal_id=f"proposal-{uuid4().hex}", operation=operation,
                                   target_id=target_id, snapshot=snapshot, created_at=now,
                                   expires_at=now + timedelta(seconds=self.proposal_ttl_seconds))

    @staticmethod
    def _check_owner(record, identity, *, same_visit=True):
        fields = ("user_id", "patient_id", "conv_id") if same_visit else ("user_id", "patient_id")
        if any(getattr(record, field) != getattr(identity, field) for field in fields):
            raise VisitStoreError("identity_conflict", "该方案或预约不属于当前就诊人和事项。")

    @staticmethod
    def _slot_available(slot, now):
        return slot.remaining > 0 and (slot.date > now.date() or (slot.date == now.date() and slot.end_time > now.strftime("%H:%M")))

    @staticmethod
    def _proposal_result(proposal):
        data = proposal.model_dump(mode="json")
        return ServiceResult(success=True, data=data, artifacts=[Artifact(id=f"proposal:{proposal.proposal_id}:{proposal.status}", type="appointment_proposal", data=data)])

    @staticmethod
    def _record_artifact(record, version):
        return Artifact(id=f"appointment:{record.appointment_id}:{version}", type="appointment_record", data=record.model_dump(mode="json"))

    def _business_error(self, exc):
        if isinstance(exc, VisitStoreError):
            return self._failure(exc.code, exc.message, retryable=exc.retryable)
        if isinstance(exc, WatchError):
            return self._failure("conflict", "相关资料刚刚发生变化，请重新读取后重试。", retryable=True)
        if isinstance(exc, ValidationError):
            return self._failure("invalid_input", "预约参数不符合要求，请重新查询并选择。")
        return self._failure("storage_unavailable", "预约存储暂时不可用，请稍后重试。", retryable=True)

    def now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("业务时钟必须携带时区")
        return value.astimezone(ZoneInfo(BUSINESS_TIMEZONE))

    def query_hospital_catalog(
        self, category: str = "hospital", department: str | None = None,
        doctor: str | None = None, date: str | Date | None = None,
    ) -> ServiceResult:
        """按真实 ID 或完整名称查询；医生日期筛选使用七天公开排班。"""
        if category not in {"hospital", "department", "doctor", "location"}:
            return self._failure("invalid_input", "查询类别应为医院、科室、医生或地点。")
        department_item = self._find(self.data.departments, "department_id", department)
        doctor_item = self._find(self.data.doctors, "doctor_id", doctor)
        if department and department_item is None:
            return self._failure("not_found", "未找到该科室，请使用医院目录中的科室名称。")
        if doctor and doctor_item is None:
            return self._failure("not_found", "未找到该医生，请使用医生目录中的姓名。")
        if category == "hospital":
            items = [self.data.hospital]
        elif category == "department":
            items = [department_item] if department_item else self.data.departments
            items = sorted(items, key=lambda item: item.department_id)
        elif category == "location":
            items = sorted(self.data.locations, key=lambda item: item.location_id)
        else:
            items = [doctor_item] if doctor_item else self.data.doctors
            if department_item:
                items = [item for item in items if item.department_id == department_item.department_id]
            if date is not None:
                try:
                    day = self.resolve_date(date)
                except (ValidationError, ValueError, TypeError):
                    return self._failure("invalid_input", "日期应为今天、明天、后天或 YYYY-MM-DD。")
                today = self.now().date()
                scheduled = {
                    item.doctor_id for item in self.data.schedules
                    if day.isoweekday() in item.weekdays
                } if today <= day <= today + timedelta(days=6) else set()
                items = [item for item in items if item.doctor_id in scheduled]
            items = sorted(items, key=lambda item: item.doctor_id)
        result = self._success("catalog", CatalogData(category=category, items=items))
        if category == "doctor":
            ids = {item.doctor_id for item in items}
            result.data["schedules"] = [
                item.model_dump(mode="json") for item in self.data.schedules if item.doctor_id in ids
            ]
        return result

    def get_visit_checklist(self, department: str | None = None, visit_type: str = "general") -> ServiceResult:
        if visit_type not in {"general", "first", "child"}:
            return self._failure("invalid_input", "就诊类型应为一般、首次或儿童就诊。")
        department_item = self._find(self.data.departments, "department_id", department)
        if department and department_item is None:
            return self._failure("not_found", "未找到该科室的就诊准备资料。")
        candidates = [item for item in self.data.checklists if item.visit_type == visit_type]
        if department_item:
            matching = [item for item in candidates if item.department_id == department_item.department_id]
            candidates = matching or [item for item in candidates if item.department_id is None]
        if not candidates:
            return self._failure("not_found", "未收录该科室和就诊类型的准备清单，请联系导诊台。")
        checklist = sorted(candidates, key=lambda item: item.checklist_id)[0]
        return self._success("visit_checklist", checklist)

    def get_wayfinding(self, origin: str | None, destination: str | None, mode: str = "normal") -> ServiceResult:
        if not origin or not destination:
            return self._failure("missing_fields", "请提供起点和目的地。")
        if mode not in {"normal", "accessible"}:
            return self._failure("invalid_input", "指引方式应为普通或无障碍。")
        start = self._find(self.data.locations, "location_id", origin)
        end = self._find(self.data.locations, "location_id", destination)
        if start is None or end is None:
            return self._failure("not_found", "未找到该地点，请使用医院目录中的地点名称。")
        route = next((item for item in self.data.wayfinding if (
            item.origin_id == start.location_id
            and item.destination_id == end.location_id
            and item.mode == mode
        )), None)
        if route is None:
            return self._failure("not_found", "未收录这两个地点之间的文字指引，请联系导诊台。")
        return self._success("wayfinding", route)

    async def initialize_slots(self) -> ServiceResult:
        if self.store is None:
            return self._failure("storage_unavailable", "号源存储尚未连接，请稍后重试。", retryable=True)
        try:
            count = await self.store.ensure_slots(self._window_slots(self.now()))
        except RedisError:
            return self._failure("storage_unavailable", "号源存储暂时不可用，请稍后重试。", retryable=True)
        return ServiceResult(success=True, data={"created": count})

    async def search_slots(
        self, department: str | None = None, doctor: str | None = None,
        date: str | Date | None = None, period: str | None = None,
    ) -> ServiceResult:
        """读取当日及后六天的可选号源，实时读取库存，不走工具结果缓存。"""
        department_item = self._find(self.data.departments, "department_id", department)
        doctor_item = self._find(self.data.doctors, "doctor_id", doctor)
        if department and department_item is None:
            return self._failure("not_found", "未找到该科室，无法查询号源。")
        if doctor and doctor_item is None:
            return self._failure("not_found", "未找到该医生，无法查询号源。")
        if period is not None and period not in {"morning", "afternoon"}:
            return self._failure("invalid_input", "时段应为上午或下午。")
        try:
            query = SlotQuery(
                department_id=department_item.department_id if department_item else None,
                doctor_id=doctor_item.doctor_id if doctor_item else None,
                date=self.resolve_date(date) if date is not None else None,
                period=period,
            )
        except (ValidationError, ValueError, TypeError):
            return self._failure("invalid_input", "日期应为今天、明天、后天或 YYYY-MM-DD。")
        if self.store is None:
            return self._failure("storage_unavailable", "号源存储尚未连接，请稍后重试。", retryable=True)
        now = self.now()
        generated = self._window_slots(now)
        try:
            await self.store.ensure_slots(generated)
            saved = await self.store.get_slots([item.slot_id for item in generated])
        except RedisError:
            return self._failure("storage_unavailable", "号源存储暂时不可用，请稍后重试。", retryable=True)
        slots = [item for item in saved if (
            item.remaining > 0
            and (item.date > now.date() or item.end_time > now.strftime("%H:%M"))
            and (query.department_id is None or item.department_id == query.department_id)
            and (query.doctor_id is None or item.doctor_id == query.doctor_id)
            and (query.date is None or item.date == query.date)
            and (query.period is None or item.period == query.period)
        )]
        slots.sort(key=lambda item: (item.date, item.start_time, item.doctor_id, item.slot_id))
        listing = SlotList(list_id=f"list-{uuid4().hex}", query=query, slots=slots, queried_at=now)
        data = listing.model_dump(mode="json")
        return ServiceResult(
            success=bool(slots), data=data,
            artifacts=[Artifact(id=f"slots:{listing.list_id}", type="slot_list", data=data)],
            error_code=None if slots else "no_slots",
            error=None if slots else "当前筛选条件下没有可用号源，请更换日期或时段。",
        )

    def _window_slots(self, now: datetime) -> list[Slot]:
        departments = {item.department_id: item for item in self.data.departments}
        doctors = {item.doctor_id: item for item in self.data.doctors}
        locations = {item.location_id: item for item in self.data.locations}
        slots = []
        for offset in range(7):
            day = now.date() + timedelta(days=offset)
            for schedule in self.data.schedules:
                if day.isoweekday() not in schedule.weekdays:
                    continue
                department = departments[schedule.department_id]
                doctor = doctors[schedule.doctor_id]
                location = locations[department.location_id]
                slots.append(Slot(
                    slot_id=f"slot:{day.isoformat()}:{schedule.schedule_id}",
                    hospital_name=self.data.hospital.name,
                    department_id=department.department_id, department_name=department.name,
                    doctor_id=doctor.doctor_id, doctor_name=doctor.name,
                    date=day, period=schedule.period, start_time=schedule.start_time, end_time=schedule.end_time,
                    fee_fen=schedule.fee_fen, location_id=location.location_id,
                    location_name=f"{location.building}{location.floor}{location.name}",
                    capacity=schedule.capacity, remaining=schedule.capacity,
                ))
        return slots

    def resolve_date(self, value: str | Date) -> Date:
        offsets = {"今天": 0, "明天": 1, "后天": 2}
        if isinstance(value, str) and value.strip() in offsets:
            return self.now().date() + timedelta(days=offsets[value.strip()])
        return SlotQuery(date=value).date

    @staticmethod
    def _find(items, id_field: str, value: str | None):
        if not value:
            return None
        return next((item for item in items if value.strip() in {getattr(item, id_field), item.name}), None)

    @staticmethod
    def _success(artifact_type, model) -> ServiceResult:
        data = model.model_dump(mode="json")
        return ServiceResult(
            success=True, data=data,
            artifacts=[Artifact(id=f"{artifact_type}:{uuid4().hex}", type=artifact_type, data=data)],
        )

    @staticmethod
    def _failure(code, message: str, *, retryable: bool = False) -> ServiceResult:
        return ServiceResult(success=False, error_code=code, error=message, retryable=retryable)
