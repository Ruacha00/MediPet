from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from typing import Literal, overload

from medipet.hospital.data_source import FakeHospitalDataSource, hospital_timezone
from medipet.hospital.operations import (
    ActionReceipt,
    Appointment,
    AppointmentNotCancellableError,
    AppointmentSlot,
    CancelAppointmentAction,
    ConfirmedHospitalAction,
    CreateAppointmentAction,
    Department,
    Doctor,
    GetAppointmentQuery,
    GetHospitalQuery,
    Hospital,
    HospitalNotFoundError,
    HospitalQuery,
    HospitalQueryResult,
    HospitalUnavailableError,
    IdempotencyConflictError,
    InvalidHospitalRequestError,
    ListAppointmentsQuery,
    ListDepartmentsQuery,
    ListDoctorsQuery,
    SearchSlotsQuery,
    SlotUnavailableError,
)


@dataclass(frozen=True)
class FakeHospitalFailurePlan:
    query_failures: int = 0
    commit_failures: int = 0

    def __post_init__(self) -> None:
        if self.query_failures < 0 or self.commit_failures < 0:
            raise ValueError("故障次数不能为负数")


@dataclass(frozen=True)
class _SlotOccurrence:
    template_id: str
    slot_date: date
    local_time: time

    @property
    def slot_id(self) -> str:
        date_value = self.slot_date.strftime("%Y%m%d")
        time_value = self.local_time.strftime("%H%M")
        return f"slot-{self.template_id}-{date_value}-{time_value}"


class FakeHospitalOperations:
    def __init__(
        self,
        data_source: FakeHospitalDataSource,
        *,
        clock: Callable[[], datetime],
        failures: FakeHospitalFailurePlan | None = None,
    ) -> None:
        failure_plan = failures or FakeHospitalFailurePlan()
        self._source = data_source
        now = clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("虚构医院时钟必须返回带时区的日期时间")
        self._now = now.astimezone(hospital_timezone(data_source.hospital.timezone))
        self._today = self._now.date()
        self._lock = asyncio.Lock()
        self._slots = self._build_slots()
        self._appointments = self._build_initial_appointments()
        self._appointment_by_slot = {
            appointment.slot_id: appointment.appointment_id
            for appointment in self._appointments.values()
        }
        self._next_appointment_number = 1
        self._next_receipt_number = 1
        self._committed_actions: dict[
            str, tuple[ConfirmedHospitalAction, ActionReceipt]
        ] = {}
        self._remaining_failures = {
            "query": failure_plan.query_failures,
            "commit": failure_plan.commit_failures,
        }

    @overload
    async def query(self, query: GetHospitalQuery) -> Hospital: ...

    @overload
    async def query(self, query: ListDepartmentsQuery) -> tuple[Department, ...]: ...

    @overload
    async def query(self, query: ListDoctorsQuery) -> tuple[Doctor, ...]: ...

    @overload
    async def query(self, query: SearchSlotsQuery) -> tuple[AppointmentSlot, ...]: ...

    @overload
    async def query(self, query: GetAppointmentQuery) -> Appointment: ...

    @overload
    async def query(self, query: ListAppointmentsQuery) -> tuple[Appointment, ...]: ...

    async def query(self, query: HospitalQuery) -> HospitalQueryResult:
        if isinstance(query, (GetAppointmentQuery, ListAppointmentsQuery)) and not (
            query.patient_id.strip()
        ):
            raise InvalidHospitalRequestError("患者 ID 不能为空")
        if (
            isinstance(query, SearchSlotsQuery)
            and query.start_date is not None
            and query.end_date is not None
            and query.start_date > query.end_date
        ):
            raise InvalidHospitalRequestError("号源查询日期范围无效")
        async with self._lock:
            self._consume_failure("query")
            if isinstance(query, GetHospitalQuery):
                return self._source.hospital
            if isinstance(query, ListDepartmentsQuery):
                return self._source.departments
            if isinstance(query, ListDoctorsQuery):
                return tuple(
                    doctor
                    for doctor in self._source.doctors
                    if query.department_id is None
                    or doctor.department_id == query.department_id
                )
            if isinstance(query, SearchSlotsQuery):
                return tuple(
                    slot
                    for slot in self._slots.values()
                    if slot.slot_id not in self._appointment_by_slot
                    and slot.starts_at > self._now
                    and (query.department_id is None or slot.department_id == query.department_id)
                    and (query.doctor_id is None or slot.doctor_id == query.doctor_id)
                    and (query.start_date is None or slot.starts_at.date() >= query.start_date)
                    and (query.end_date is None or slot.starts_at.date() <= query.end_date)
                )
            if isinstance(query, GetAppointmentQuery):
                appointment = self._appointments.get(query.appointment_id)
                if appointment is None or appointment.patient_id != query.patient_id:
                    raise HospitalNotFoundError("预约不存在")
                return appointment
            if isinstance(query, ListAppointmentsQuery):
                return tuple(
                    appointment
                    for appointment in self._appointments.values()
                    if appointment.patient_id == query.patient_id
                )
            raise TypeError(f"不支持的医院查询: {type(query).__name__}")

    async def commit(self, action: ConfirmedHospitalAction) -> ActionReceipt:
        if isinstance(action, CreateAppointmentAction):
            resource_id = action.slot_id
        elif isinstance(action, CancelAppointmentAction):
            resource_id = action.appointment_id
        else:
            raise TypeError(f"不支持的医院操作: {type(action).__name__}")
        if (
            not action.patient_id.strip()
            or not resource_id.strip()
            or not action.idempotency_key.strip()
        ):
            raise InvalidHospitalRequestError("患者、业务资源和幂等键不能为空")
        async with self._lock:
            previous = self._committed_actions.get(action.idempotency_key)
            if previous is not None:
                previous_action, previous_receipt = previous
                if previous_action != action:
                    raise IdempotencyConflictError("幂等键不能用于不同的医院操作")
                return previous_receipt
            self._consume_failure("commit")
            if isinstance(action, CancelAppointmentAction):
                appointment = self._appointments.get(action.appointment_id)
                if appointment is None or appointment.patient_id != action.patient_id:
                    raise HospitalNotFoundError("预约不存在")
                if appointment.status != "booked" or appointment.starts_at <= self._now:
                    raise AppointmentNotCancellableError("预约当前不可取消")
                cancelled = replace(appointment, status="cancelled")
                receipt = ActionReceipt(
                    receipt_id=f"receipt-fake-{self._next_receipt_number:04d}",
                    idempotency_key=action.idempotency_key,
                    appointment=cancelled,
                )
                self._appointments[appointment.appointment_id] = cancelled
                self._appointment_by_slot.pop(appointment.slot_id, None)
                self._committed_actions[action.idempotency_key] = (action, receipt)
                self._next_receipt_number += 1
                return receipt
            slot = self._slots.get(action.slot_id)
            if slot is None:
                raise HospitalNotFoundError("号源不存在")
            if slot.slot_id in self._appointment_by_slot:
                raise SlotUnavailableError("号源已不可预约")
            appointment = Appointment(
                appointment_id=self._take_next_appointment_id(),
                patient_id=action.patient_id,
                slot_id=slot.slot_id,
                department_id=slot.department_id,
                doctor_id=slot.doctor_id,
                starts_at=slot.starts_at,
                ends_at=slot.ends_at,
                fee_cents=slot.fee_cents,
            )
            receipt = ActionReceipt(
                receipt_id=f"receipt-fake-{self._next_receipt_number:04d}",
                idempotency_key=action.idempotency_key,
                appointment=appointment,
            )
            self._appointments[appointment.appointment_id] = appointment
            self._appointment_by_slot[slot.slot_id] = appointment.appointment_id
            self._committed_actions[action.idempotency_key] = (action, receipt)
            self._next_receipt_number += 1
            return receipt

    def _take_next_appointment_id(self) -> str:
        while True:
            appointment_id = f"appointment-fake-{self._next_appointment_number:04d}"
            self._next_appointment_number += 1
            if appointment_id not in self._appointments:
                return appointment_id

    def _consume_failure(self, operation: Literal["query", "commit"]) -> None:
        remaining = self._remaining_failures[operation]
        if remaining <= 0:
            return
        self._remaining_failures[operation] = remaining - 1
        label = "查询" if operation == "query" else "提交"
        raise HospitalUnavailableError(f"虚构医院{label}暂时不可用")

    def _build_slots(self) -> dict[str, AppointmentSlot]:
        timezone = hospital_timezone(self._source.hospital.timezone)
        doctors = {doctor.doctor_id: doctor for doctor in self._source.doctors}
        slots: dict[str, AppointmentSlot] = {}
        for schedule in self._source.schedules:
            doctor = doctors[schedule.doctor_id]
            for day_offset in schedule.day_offsets:
                slot_date = self._today + timedelta(days=day_offset)
                for value in schedule.times:
                    occurrence = _SlotOccurrence(schedule.template_id, slot_date, value)
                    starts_at = datetime.combine(slot_date, value, timezone)
                    ends_at = starts_at + timedelta(minutes=schedule.duration_minutes)
                    slots[occurrence.slot_id] = AppointmentSlot(
                        slot_id=occurrence.slot_id,
                        department_id=doctor.department_id,
                        doctor_id=doctor.doctor_id,
                        starts_at=starts_at,
                        ends_at=ends_at,
                        fee_cents=schedule.fee_cents,
                    )
        return slots

    def _build_initial_appointments(self) -> dict[str, Appointment]:
        appointments: dict[str, Appointment] = {}
        for booking in self._source.initial_bookings:
            slot_date = self._today + timedelta(days=booking.day_offset)
            occurrence = _SlotOccurrence(booking.template_id, slot_date, booking.time)
            slot = self._slots[occurrence.slot_id]
            appointments[booking.appointment_id] = Appointment(
                appointment_id=booking.appointment_id,
                patient_id=booking.patient_id,
                slot_id=slot.slot_id,
                department_id=slot.department_id,
                doctor_id=slot.doctor_id,
                starts_at=slot.starts_at,
                ends_at=slot.ends_at,
                fee_cents=slot.fee_cents,
            )
        return appointments
