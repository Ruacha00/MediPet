from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal, Protocol, overload


class HospitalOperationsError(RuntimeError):
    """Base error exposed by the hospital operations boundary."""


class HospitalDataError(ValueError):
    """The configured hospital data source is invalid."""


class HospitalNotFoundError(HospitalOperationsError):
    """A hospital resource does not exist in the caller's scope."""


class InvalidHospitalRequestError(HospitalOperationsError):
    """A hospital operation is missing required or coherent input."""


class SlotUnavailableError(HospitalOperationsError):
    """A known slot can no longer be booked."""


class AppointmentNotCancellableError(HospitalOperationsError):
    """An appointment cannot be cancelled in its current state."""


class IdempotencyConflictError(HospitalOperationsError):
    """An idempotency key was reused for a different action."""


class HospitalUnavailableError(HospitalOperationsError):
    """The hospital operation failed before producing a result."""


@dataclass(frozen=True)
class Hospital:
    hospital_id: str
    name: str
    timezone: str


@dataclass(frozen=True)
class Department:
    department_id: str
    name: str
    description: str


@dataclass(frozen=True)
class Doctor:
    doctor_id: str
    department_id: str
    name: str
    title: str


@dataclass(frozen=True)
class AppointmentSlot:
    slot_id: str
    department_id: str
    doctor_id: str
    starts_at: datetime
    ends_at: datetime
    fee_cents: int
    currency: Literal["CNY"] = "CNY"


@dataclass(frozen=True)
class Appointment:
    appointment_id: str
    patient_id: str
    slot_id: str
    department_id: str
    doctor_id: str
    starts_at: datetime
    ends_at: datetime
    fee_cents: int
    currency: Literal["CNY"] = "CNY"
    status: Literal["booked", "cancelled"] = "booked"


@dataclass(frozen=True)
class GetHospitalQuery:
    pass


@dataclass(frozen=True)
class ListDepartmentsQuery:
    pass


@dataclass(frozen=True)
class ListDoctorsQuery:
    department_id: str | None = None


@dataclass(frozen=True)
class SearchSlotsQuery:
    department_id: str | None = None
    doctor_id: str | None = None
    start_date: date | None = None
    end_date: date | None = None


@dataclass(frozen=True)
class GetAppointmentQuery:
    patient_id: str
    appointment_id: str


@dataclass(frozen=True)
class ListAppointmentsQuery:
    patient_id: str


type HospitalQuery = (
    GetHospitalQuery
    | ListDepartmentsQuery
    | ListDoctorsQuery
    | SearchSlotsQuery
    | GetAppointmentQuery
    | ListAppointmentsQuery
)
type HospitalQueryResult = (
    Hospital
    | tuple[Department, ...]
    | tuple[Doctor, ...]
    | tuple[AppointmentSlot, ...]
    | Appointment
    | tuple[Appointment, ...]
)


@dataclass(frozen=True)
class CreateAppointmentAction:
    patient_id: str
    slot_id: str
    idempotency_key: str


@dataclass(frozen=True)
class CancelAppointmentAction:
    patient_id: str
    appointment_id: str
    idempotency_key: str


type ConfirmedHospitalAction = CreateAppointmentAction | CancelAppointmentAction


@dataclass(frozen=True)
class ActionReceipt:
    receipt_id: str
    idempotency_key: str
    appointment: Appointment


class HospitalOperations(Protocol):
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

    async def query(self, query: HospitalQuery) -> HospitalQueryResult: ...

    async def commit(self, action: ConfirmedHospitalAction) -> ActionReceipt: ...
