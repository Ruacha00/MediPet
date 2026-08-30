from __future__ import annotations

from datetime import date

from medipet.agent.capabilities import ToolConfirmationContract, ToolContext
from medipet.hospital.operations import (
    Appointment,
    AppointmentSlot,
    CreateAppointmentAction,
    Department,
    Doctor,
    GetAppointmentQuery,
    GetHospitalQuery,
    Hospital,
    HospitalNotFoundError,
    HospitalOperations,
    HospitalOperationsError,
    InvalidHospitalRequestError,
    ListAppointmentsQuery,
    ListDepartmentsQuery,
    ListDoctorsQuery,
    SearchSlotsQuery,
)
from medipet.tools.registry import TrustedTool

_EMPTY_INPUT_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {},
    "required": [],
    "additionalProperties": False,
}
_HOSPITAL_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "hospital_id": {"type": "string"},
        "name": {"type": "string"},
        "timezone": {"type": "string"},
    },
    "required": ["hospital_id", "name", "timezone"],
    "additionalProperties": False,
}
_DEPARTMENT_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "department_id": {"type": "string"},
        "name": {"type": "string"},
        "description": {"type": "string"},
    },
    "required": ["department_id", "name", "description"],
    "additionalProperties": False,
}
_DOCTOR_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "doctor_id": {"type": "string"},
        "department_id": {"type": "string"},
        "name": {"type": "string"},
        "title": {"type": "string"},
    },
    "required": ["doctor_id", "department_id", "name", "title"],
    "additionalProperties": False,
}
_SLOT_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "slot_id": {"type": "string"},
        "department_id": {"type": "string"},
        "doctor_id": {"type": "string"},
        "starts_at": {"type": "string", "format": "date-time"},
        "ends_at": {"type": "string", "format": "date-time"},
        "fee_cents": {"type": "integer", "minimum": 0},
        "currency": {"type": "string", "enum": ["CNY"]},
    },
    "required": [
        "slot_id",
        "department_id",
        "doctor_id",
        "starts_at",
        "ends_at",
        "fee_cents",
        "currency",
    ],
    "additionalProperties": False,
}
_APPOINTMENT_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "appointment_id": {"type": "string"},
        "patient_id": {"type": "string"},
        "slot_id": {"type": "string"},
        "department_id": {"type": "string"},
        "doctor_id": {"type": "string"},
        "starts_at": {"type": "string", "format": "date-time"},
        "ends_at": {"type": "string", "format": "date-time"},
        "fee_cents": {"type": "integer", "minimum": 0},
        "currency": {"type": "string", "enum": ["CNY"]},
        "status": {"type": "string", "enum": ["booked"]},
    },
    "required": [
        "appointment_id",
        "patient_id",
        "slot_id",
        "department_id",
        "doctor_id",
        "starts_at",
        "ends_at",
        "fee_cents",
        "currency",
        "status",
    ],
    "additionalProperties": False,
}
_PATIENT_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {"patient_id": {"type": "string"}},
    "required": ["patient_id"],
    "additionalProperties": False,
}
_APPOINTMENT_CONFIRMATION_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "patient": _PATIENT_SCHEMA,
        "hospital": _HOSPITAL_SCHEMA,
        "department": _DEPARTMENT_SCHEMA,
        "doctor": _DOCTOR_SCHEMA,
        "slot_id": {"type": "string"},
        "starts_at": {"type": "string", "format": "date-time"},
        "ends_at": {"type": "string", "format": "date-time"},
        "fee_cents": {"type": "integer", "minimum": 0},
        "currency": {"type": "string", "enum": ["CNY"]},
    },
    "required": [
        "patient",
        "hospital",
        "department",
        "doctor",
        "slot_id",
        "starts_at",
        "ends_at",
        "fee_cents",
        "currency",
    ],
    "additionalProperties": False,
}


class HospitalToolProvider:
    def __init__(self, operations: HospitalOperations) -> None:
        self._operations = operations

    async def tools(self) -> tuple[TrustedTool, ...]:
        async def get_hospital(
            arguments: dict[str, object], context: ToolContext
        ) -> dict[str, object]:
            del arguments, context
            hospital = await self._operations.query(GetHospitalQuery())
            return {"hospital": _hospital_data(hospital)}

        async def list_departments(
            arguments: dict[str, object], context: ToolContext
        ) -> dict[str, object]:
            del arguments, context
            departments = await self._operations.query(ListDepartmentsQuery())
            return {"departments": [_department_data(item) for item in departments]}

        async def list_doctors(
            arguments: dict[str, object], context: ToolContext
        ) -> dict[str, object]:
            del context
            doctors = await self._operations.query(
                ListDoctorsQuery(department_id=_optional_text(arguments, "department_id"))
            )
            return {"doctors": [_doctor_data(item) for item in doctors]}

        async def search_slots(
            arguments: dict[str, object], context: ToolContext
        ) -> dict[str, object]:
            del context
            slots = await self._operations.query(
                SearchSlotsQuery(
                    department_id=_optional_text(arguments, "department_id"),
                    doctor_id=_optional_text(arguments, "doctor_id"),
                    start_date=_optional_date(arguments, "start_date"),
                    end_date=_optional_date(arguments, "end_date"),
                )
            )
            return {"slots": [_slot_data(item) for item in slots]}

        async def get_appointment(
            arguments: dict[str, object], context: ToolContext
        ) -> dict[str, object]:
            patient_id = _patient_id(context)
            try:
                appointment = await self._operations.query(
                    GetAppointmentQuery(
                        patient_id=patient_id,
                        appointment_id=_required_text(arguments, "appointment_id"),
                    )
                )
            except HospitalNotFoundError:
                return {"appointment": None}
            return {"appointment": _appointment_data(appointment)}

        async def list_appointments(
            arguments: dict[str, object], context: ToolContext
        ) -> dict[str, object]:
            del arguments
            appointments = await self._operations.query(
                ListAppointmentsQuery(patient_id=_patient_id(context))
            )
            return {
                "appointments": [_appointment_data(item) for item in appointments]
            }

        async def prepare_create_appointment(
            arguments: dict[str, object], context: ToolContext
        ) -> dict[str, object]:
            return await _appointment_confirmation(
                self._operations, arguments, context
            )

        async def revalidate_create_appointment(
            arguments: dict[str, object],
            confirmation: dict[str, object],
            context: ToolContext,
        ) -> bool:
            try:
                current = await _appointment_confirmation(
                    self._operations, arguments, context
                )
            except HospitalOperationsError:
                return False
            return current == confirmation

        async def create_appointment(
            arguments: dict[str, object], context: ToolContext
        ) -> dict[str, object]:
            receipt = await self._operations.commit(
                CreateAppointmentAction(
                    patient_id=_patient_id(context),
                    slot_id=_required_text(arguments, "slot_id"),
                    idempotency_key=_required_context_key(context),
                )
            )
            return {
                "receipt_id": receipt.receipt_id,
                "appointment": _appointment_data(receipt.appointment),
            }

        return (
            TrustedTool(
                tool_id="hospital.get_hospital",
                version="1",
                name="hospital_get_hospital",
                description="查询服务医院的基本资料。",
                input_schema=_EMPTY_INPUT_SCHEMA,
                output_schema={
                    "type": "object",
                    "properties": {"hospital": _HOSPITAL_SCHEMA},
                    "required": ["hospital"],
                    "additionalProperties": False,
                },
                effect="read",
                approval_required=False,
                execute=get_hospital,
            ),
            TrustedTool(
                tool_id="hospital.list_departments",
                version="1",
                name="hospital_list_departments",
                description="列出服务医院的科室。",
                input_schema=_EMPTY_INPUT_SCHEMA,
                output_schema={
                    "type": "object",
                    "properties": {
                        "departments": {
                            "type": "array",
                            "items": _DEPARTMENT_SCHEMA,
                        }
                    },
                    "required": ["departments"],
                    "additionalProperties": False,
                },
                effect="read",
                approval_required=False,
                execute=list_departments,
            ),
            TrustedTool(
                tool_id="hospital.list_doctors",
                version="1",
                name="hospital_list_doctors",
                description="列出服务医院的医生，可按科室筛选。",
                input_schema={
                    "type": "object",
                    "properties": {"department_id": {"type": "string"}},
                    "required": [],
                    "additionalProperties": False,
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "doctors": {"type": "array", "items": _DOCTOR_SCHEMA}
                    },
                    "required": ["doctors"],
                    "additionalProperties": False,
                },
                effect="read",
                approval_required=False,
                execute=list_doctors,
            ),
            TrustedTool(
                tool_id="hospital.search_slots",
                version="1",
                name="hospital_search_slots",
                description="查询服务医院当前可预约的号源。",
                input_schema={
                    "type": "object",
                    "properties": {
                        "department_id": {"type": "string"},
                        "doctor_id": {"type": "string"},
                        "start_date": {"type": "string", "format": "date"},
                        "end_date": {"type": "string", "format": "date"},
                    },
                    "required": [],
                    "additionalProperties": False,
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "slots": {"type": "array", "items": _SLOT_SCHEMA}
                    },
                    "required": ["slots"],
                    "additionalProperties": False,
                },
                effect="read",
                approval_required=False,
                execute=search_slots,
            ),
            TrustedTool(
                tool_id="hospital.get_appointment",
                version="1",
                name="hospital_get_appointment",
                description="查询当前患者的一条预约挂号。",
                input_schema={
                    "type": "object",
                    "properties": {"appointment_id": {"type": "string"}},
                    "required": ["appointment_id"],
                    "additionalProperties": False,
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "appointment": {
                            "anyOf": [_APPOINTMENT_SCHEMA, {"type": "null"}]
                        }
                    },
                    "required": ["appointment"],
                    "additionalProperties": False,
                },
                effect="read",
                approval_required=False,
                execute=get_appointment,
            ),
            TrustedTool(
                tool_id="hospital.list_appointments",
                version="1",
                name="hospital_list_appointments",
                description="列出当前患者的预约挂号。",
                input_schema=_EMPTY_INPUT_SCHEMA,
                output_schema={
                    "type": "object",
                    "properties": {
                        "appointments": {
                            "type": "array",
                            "items": _APPOINTMENT_SCHEMA,
                        }
                    },
                    "required": ["appointments"],
                    "additionalProperties": False,
                },
                effect="read",
                approval_required=False,
                execute=list_appointments,
            ),
            TrustedTool(
                tool_id="hospital.create_appointment",
                version="1",
                name="hospital_create_appointment",
                description="为当前患者创建预约挂号。",
                input_schema={
                    "type": "object",
                    "properties": {"slot_id": {"type": "string"}},
                    "required": ["slot_id"],
                    "additionalProperties": False,
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "receipt_id": {"type": "string"},
                        "appointment": _APPOINTMENT_SCHEMA,
                    },
                    "required": ["receipt_id", "appointment"],
                    "additionalProperties": False,
                },
                effect="write",
                approval_required=True,
                allowed_stages=("pre_visit",),
                confirmation_contract=ToolConfirmationContract(
                    schema=_APPOINTMENT_CONFIRMATION_SCHEMA,
                    prepare=prepare_create_appointment,
                    revalidate=revalidate_create_appointment,
                ),
                execute=create_appointment,
            ),
        )


async def _appointment_confirmation(
    operations: HospitalOperations,
    arguments: dict[str, object],
    context: ToolContext,
) -> dict[str, object]:
    slot_id = _required_text(arguments, "slot_id")
    slots = await operations.query(SearchSlotsQuery())
    slot = next((item for item in slots if item.slot_id == slot_id), None)
    if slot is None:
        raise HospitalNotFoundError("号源不存在或已不可预约")
    hospital = await operations.query(GetHospitalQuery())
    departments = await operations.query(ListDepartmentsQuery())
    department = next(
        (item for item in departments if item.department_id == slot.department_id),
        None,
    )
    doctors = await operations.query(ListDoctorsQuery(department_id=slot.department_id))
    doctor = next((item for item in doctors if item.doctor_id == slot.doctor_id), None)
    if department is None or doctor is None:
        raise HospitalNotFoundError("号源对应的科室或医生不存在")
    return {
        "patient": {"patient_id": _patient_id(context)},
        "hospital": _hospital_data(hospital),
        "department": _department_data(department),
        "doctor": _doctor_data(doctor),
        "slot_id": slot.slot_id,
        "starts_at": slot.starts_at.isoformat(),
        "ends_at": slot.ends_at.isoformat(),
        "fee_cents": slot.fee_cents,
        "currency": slot.currency,
}


def _hospital_data(hospital: Hospital) -> dict[str, object]:
    return {
        "hospital_id": hospital.hospital_id,
        "name": hospital.name,
        "timezone": hospital.timezone,
    }


def _department_data(department: Department) -> dict[str, object]:
    return {
        "department_id": department.department_id,
        "name": department.name,
        "description": department.description,
    }


def _doctor_data(doctor: Doctor) -> dict[str, object]:
    return {
        "doctor_id": doctor.doctor_id,
        "department_id": doctor.department_id,
        "name": doctor.name,
        "title": doctor.title,
    }


def _slot_data(slot: AppointmentSlot) -> dict[str, object]:
    return {
        "slot_id": slot.slot_id,
        "department_id": slot.department_id,
        "doctor_id": slot.doctor_id,
        "starts_at": slot.starts_at.isoformat(),
        "ends_at": slot.ends_at.isoformat(),
        "fee_cents": slot.fee_cents,
        "currency": slot.currency,
    }


def _appointment_data(appointment: Appointment) -> dict[str, object]:
    return {
        "appointment_id": appointment.appointment_id,
        "patient_id": appointment.patient_id,
        "slot_id": appointment.slot_id,
        "department_id": appointment.department_id,
        "doctor_id": appointment.doctor_id,
        "starts_at": appointment.starts_at.isoformat(),
        "ends_at": appointment.ends_at.isoformat(),
        "fee_cents": appointment.fee_cents,
        "currency": appointment.currency,
        "status": appointment.status,
    }


def _optional_text(arguments: dict[str, object], name: str) -> str | None:
    value = arguments.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise InvalidHospitalRequestError(f"{name} 必须是非空文本")
    return value.strip()


def _required_text(arguments: dict[str, object], name: str) -> str:
    value = _optional_text(arguments, name)
    if value is None:
        raise InvalidHospitalRequestError(f"缺少 {name}")
    return value


def _patient_id(context: ToolContext) -> str:
    if not context.patient_id.strip():
        raise InvalidHospitalRequestError("ToolContext 缺少权威患者 ID")
    return context.patient_id.strip()


def _required_context_key(context: ToolContext) -> str:
    if not context.idempotency_key.strip():
        raise InvalidHospitalRequestError("ToolContext 缺少医院幂等键")
    return context.idempotency_key.strip()


def _optional_date(arguments: dict[str, object], name: str) -> date | None:
    value = _optional_text(arguments, name)
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise InvalidHospitalRequestError(f"{name} 必须是 ISO 8601 日期") from error
