from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal, cast

from medipet.agent.capabilities import (
    ToolConfirmationContract,
    ToolContext,
    ToolExecutor,
    ToolPresenter,
)
from medipet.hospital.operations import (
    AccessibleWayfindingUnavailableError,
    Appointment,
    AppointmentNotCancellableError,
    AppointmentSlot,
    CancelAppointmentAction,
    CreateAppointmentAction,
    Department,
    Doctor,
    GetAppointmentQuery,
    GetHospitalQuery,
    GetWayfindingGuidanceQuery,
    Hospital,
    HospitalNotFoundError,
    HospitalOperations,
    HospitalOperationsError,
    HospitalServiceLocation,
    HospitalWayfindingGuidance,
    InvalidHospitalRequestError,
    ListAppointmentsQuery,
    ListDepartmentsQuery,
    ListDoctorsQuery,
    ListServiceLocationsQuery,
    ListWayfindingOriginsQuery,
    SearchSlotsQuery,
    ServiceLocationNotFoundError,
    WayfindingOrigin,
    WayfindingOriginNotFoundError,
    WayfindingUnavailableError,
)
from medipet.hospital.tool_manifest import load_hospital_tool_specs
from medipet.tools.registry import ToolRegistryError, TrustedTool


class HospitalToolProvider:
    def __init__(
        self,
        operations: HospitalOperations,
        *,
        manifest_path: Path | None = None,
    ) -> None:
        self._operations = operations
        self._manifest_path = manifest_path

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

        async def present_slots(
            observation: dict[str, object], context: ToolContext
        ) -> tuple[dict[str, object], ...]:
            del context
            departments = {
                item.department_id: item
                for item in await self._operations.query(ListDepartmentsQuery())
            }
            doctors = {
                item.doctor_id: item
                for item in await self._operations.query(ListDoctorsQuery())
            }
            raw_slots = observation.get("slots")
            if not isinstance(raw_slots, list):
                return ()
            slots: list[dict[str, object]] = []
            for raw_slot in raw_slots:
                if not isinstance(raw_slot, dict):
                    continue
                department_id = raw_slot.get("department_id")
                doctor_id = raw_slot.get("doctor_id")
                department = (
                    departments.get(department_id)
                    if isinstance(department_id, str)
                    else None
                )
                doctor = doctors.get(doctor_id) if isinstance(doctor_id, str) else None
                if department is None or doctor is None:
                    continue
                slots.append(
                    {
                        "id": raw_slot["slot_id"],
                        "department": department.name,
                        "doctor": doctor.name,
                        "doctorTitle": doctor.title,
                        "startsAt": raw_slot["starts_at"],
                        "endsAt": raw_slot["ends_at"],
                        "feeCents": raw_slot["fee_cents"],
                        "currency": raw_slot["currency"],
                    }
                )
            return (
                {
                    "type": "data-slot-options",
                    "data": {"slots": slots},
                },
            )

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

        async def list_wayfinding_origins(
            arguments: dict[str, object], context: ToolContext
        ) -> dict[str, object]:
            del arguments, context
            origins = await self._operations.query(ListWayfindingOriginsQuery())
            return {"origins": [_wayfinding_origin_data(item) for item in origins]}

        async def list_service_locations(
            arguments: dict[str, object], context: ToolContext
        ) -> dict[str, object]:
            del arguments, context
            locations = await self._operations.query(ListServiceLocationsQuery())
            return {"locations": [_service_location_data(item) for item in locations]}

        async def get_wayfinding_guidance(
            arguments: dict[str, object], context: ToolContext
        ) -> dict[str, object]:
            origin_id = _required_text(arguments, "origin_id")
            destination_id = _required_text(arguments, "destination_id")
            mode = _wayfinding_mode(arguments)
            if not _participant_selected_wayfinding(
                origin_id, destination_id, mode, context
            ):
                return _wayfinding_unavailable(
                    "selection_required",
                    "请明确说明当前起点、目的地以及普通或无障碍模式。",
                )
            try:
                guidance = await self._operations.query(
                    GetWayfindingGuidanceQuery(
                        origin_id=origin_id,
                        destination_id=destination_id,
                        mode=mode,
                    )
                )
            except WayfindingOriginNotFoundError:
                return _wayfinding_unavailable(
                    "origin_not_found", "未找到这个院内方位指引起点。"
                )
            except ServiceLocationNotFoundError:
                return _wayfinding_unavailable(
                    "destination_not_found", "未找到这个院内服务地点。"
                )
            except AccessibleWayfindingUnavailableError:
                return _wayfinding_unavailable(
                    "accessible_unavailable",
                    "该起点和目的地没有无障碍方位指引，请询问院内工作人员。",
                )
            except WayfindingUnavailableError:
                return _wayfinding_unavailable(
                    "unavailable",
                    "该起点和目的地没有可用方位指引，请询问院内工作人员。",
                )
            return {
                "guidance": _wayfinding_guidance_data(guidance),
                "unavailable": None,
            }

        async def present_wayfinding(
            observation: dict[str, object], context: ToolContext
        ) -> tuple[dict[str, object], ...]:
            del context
            part = _wayfinding_part(observation)
            return () if part is None else (part,)

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

        async def prepare_cancel_appointment(
            arguments: dict[str, object], context: ToolContext
        ) -> dict[str, object]:
            return await _cancellation_confirmation(self._operations, arguments, context)

        async def revalidate_cancel_appointment(
            arguments: dict[str, object],
            confirmation: dict[str, object],
            context: ToolContext,
        ) -> bool:
            try:
                current = await _cancellation_confirmation(
                    self._operations, arguments, context
                )
            except HospitalOperationsError:
                return False
            return current == confirmation

        async def cancel_appointment(
            arguments: dict[str, object], context: ToolContext
        ) -> dict[str, object]:
            receipt = await self._operations.commit(
                CancelAppointmentAction(
                    patient_id=_patient_id(context),
                    appointment_id=_required_text(arguments, "appointment_id"),
                    idempotency_key=_required_context_key(context),
                )
            )
            return {
                "receipt_id": receipt.receipt_id,
                "appointment": _appointment_data(receipt.appointment),
            }

        specs = load_hospital_tool_specs(self._manifest_path)
        executors: dict[str, ToolExecutor] = {
            "hospital.get_hospital": get_hospital,
            "hospital.list_departments": list_departments,
            "hospital.list_doctors": list_doctors,
            "hospital.search_slots": search_slots,
            "hospital.get_appointment": get_appointment,
            "hospital.list_appointments": list_appointments,
            "hospital.list_wayfinding_origins": list_wayfinding_origins,
            "hospital.list_service_locations": list_service_locations,
            "hospital.get_wayfinding_guidance": get_wayfinding_guidance,
            "hospital.create_appointment": create_appointment,
            "hospital.cancel_appointment": cancel_appointment,
        }
        presenters: dict[str, ToolPresenter] = {
            "hospital.search_slots": present_slots,
            "hospital.get_wayfinding_guidance": present_wayfinding,
        }
        manifest_ids = {spec.tool_id for spec in specs}
        if manifest_ids != executors.keys() or len(manifest_ids) != len(specs):
            raise ToolRegistryError(
                "Hospital Tool manifest must match the deployed trusted executors"
            )
        trusted_policies = {
            **{
                tool_id: ("read", False, ("pre_visit", "in_visit"), False)
                for tool_id in executors
                if tool_id not in {
                    "hospital.create_appointment",
                    "hospital.cancel_appointment",
                }
            },
            "hospital.create_appointment": ("write", True, ("pre_visit",), True),
            "hospital.cancel_appointment": ("write", True, ("pre_visit",), True),
        }
        for spec in specs:
            actual_policy = (
                spec.effect,
                spec.approval_required,
                spec.allowed_stages,
                spec.confirmation_schema is not None,
            )
            if actual_policy != trusted_policies[spec.tool_id]:
                raise ToolRegistryError(
                    f"Hospital Tool policy does not match its trusted executor: {spec.tool_id}"
                )
        confirmation_contracts = {
            "hospital.create_appointment": ToolConfirmationContract(
                schema=next(
                    spec.confirmation_schema
                    for spec in specs
                    if spec.tool_id == "hospital.create_appointment"
                    and spec.confirmation_schema is not None
                ),
                prepare=prepare_create_appointment,
                revalidate=revalidate_create_appointment,
            ),
            "hospital.cancel_appointment": ToolConfirmationContract(
                schema=next(
                    spec.confirmation_schema
                    for spec in specs
                    if spec.tool_id == "hospital.cancel_appointment"
                    and spec.confirmation_schema is not None
                ),
                prepare=prepare_cancel_appointment,
                revalidate=revalidate_cancel_appointment,
            ),
        }
        return tuple(
            TrustedTool(
                tool_id=spec.tool_id,
                version=spec.version,
                name=spec.name,
                description=spec.description,
                input_schema=spec.input_schema,
                output_schema=spec.output_schema,
                effect=spec.effect,
                approval_required=spec.approval_required,
                allowed_stages=spec.allowed_stages,
                confirmation_contract=confirmation_contracts.get(spec.tool_id),
                execute=executors[spec.tool_id],
                present=presenters.get(spec.tool_id),
            )
            for spec in specs
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


async def _cancellation_confirmation(
    operations: HospitalOperations,
    arguments: dict[str, object],
    context: ToolContext,
) -> dict[str, object]:
    appointment = await operations.query(
        GetAppointmentQuery(
            patient_id=_patient_id(context),
            appointment_id=_required_text(arguments, "appointment_id"),
        )
    )
    if appointment.status != "booked":
        raise AppointmentNotCancellableError("预约当前不可取消")
    hospital = await operations.query(GetHospitalQuery())
    departments = await operations.query(ListDepartmentsQuery())
    department = next(
        (item for item in departments if item.department_id == appointment.department_id),
        None,
    )
    doctors = await operations.query(
        ListDoctorsQuery(department_id=appointment.department_id)
    )
    doctor = next(
        (item for item in doctors if item.doctor_id == appointment.doctor_id),
        None,
    )
    if department is None or doctor is None:
        raise HospitalNotFoundError("预约对应的科室或医生不存在")
    return {
        "patient": {"patient_id": _patient_id(context)},
        "hospital": _hospital_data(hospital),
        "department": _department_data(department),
        "doctor": _doctor_data(doctor),
        "appointment_id": appointment.appointment_id,
        "starts_at": appointment.starts_at.isoformat(),
        "ends_at": appointment.ends_at.isoformat(),
        "fee_cents": appointment.fee_cents,
        "currency": appointment.currency,
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


def _wayfinding_origin_data(origin: WayfindingOrigin) -> dict[str, object]:
    return {"origin_id": origin.origin_id, "display_name": origin.display_name}


def _service_location_data(location: HospitalServiceLocation) -> dict[str, object]:
    return {
        "location_id": location.location_id,
        "display_name": location.display_name,
        "category": location.category,
    }


def _wayfinding_guidance_data(
    guidance: HospitalWayfindingGuidance,
) -> dict[str, object]:
    return {
        "origin": _wayfinding_origin_data(guidance.origin),
        "destination": _service_location_data(guidance.destination),
        "mode": guidance.mode,
        "steps": list(guidance.steps),
        "notice": guidance.notice,
        "data_version": guidance.data_version,
    }


def _wayfinding_part(observation: dict[str, object]) -> dict[str, object] | None:
    unavailable = observation.get("unavailable")
    if isinstance(unavailable, dict):
        return {
            "type": "data-hospital-wayfinding-unavailable",
            "data": {
                "reason": unavailable.get("reason"),
                "message": unavailable.get("message"),
            },
        }
    guidance = observation.get("guidance")
    if not isinstance(guidance, dict):
        return None
    origin = guidance.get("origin")
    destination = guidance.get("destination")
    steps = guidance.get("steps")
    mode = guidance.get("mode")
    data_version = guidance.get("data_version")
    if (
        not isinstance(origin, dict)
        or not isinstance(destination, dict)
        or not isinstance(steps, list)
        or mode not in {"standard", "accessible"}
        or not isinstance(data_version, str)
    ):
        return None
    return {
        "type": "data-hospital-wayfinding",
        "data": {
            "origin": {"id": origin.get("origin_id"), "name": origin.get("display_name")},
            "destination": {
                "id": destination.get("location_id"),
                "name": destination.get("display_name"),
            },
            "mode": mode,
            "steps": steps,
            "notice": guidance.get("notice"),
            "dataVersion": data_version,
        },
    }


def _wayfinding_unavailable(reason: str, message: str) -> dict[str, object]:
    return {
        "guidance": None,
        "unavailable": {"reason": reason, "message": message},
    }


def _participant_selected_wayfinding(
    origin_id: str,
    destination_id: str,
    mode: Literal["standard", "accessible"],
    context: ToolContext,
) -> bool:
    selection = context.participant_tool_selection
    return selection is not None and selection.tool_name == (
        "hospital_get_wayfinding_guidance"
    ) and dict(selection.arguments) == {
        "origin_id": origin_id,
        "destination_id": destination_id,
        "mode": mode,
    }


def _wayfinding_mode(
    arguments: dict[str, object],
) -> Literal["standard", "accessible"]:
    value = _required_text(arguments, "mode")
    if value not in {"standard", "accessible"}:
        raise InvalidHospitalRequestError("mode 必须是 standard 或 accessible")
    return cast(Literal["standard", "accessible"], value)


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
