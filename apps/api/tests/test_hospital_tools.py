from datetime import UTC, datetime, timedelta, timezone
from typing import cast

import pytest
from fastapi.testclient import TestClient

from medipet.actions import ActionDecisionError, InMemoryActionStore
from medipet.agent.capabilities import (
    ParticipantToolSelection,
    ToolContext,
    ToolDefinition,
)
from medipet.delivery.http import create_app
from medipet.hospital.data_source import FakeHospitalDataSource
from medipet.hospital.fake import FakeHospitalFailurePlan, FakeHospitalOperations
from medipet.hospital.operations import (
    CreateAppointmentAction,
    HospitalUnavailableError,
    ListAppointmentsQuery,
)
from medipet.hospital.tools import HospitalToolProvider
from medipet.tools.registry import InMemoryToolRegistry, TrustedTool

SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")


def _object_schema(
    properties: dict[str, object], required: list[str]
) -> dict[str, object]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


HOSPITAL_SCHEMA = _object_schema(
    {
        "hospital_id": {"type": "string"},
        "name": {"type": "string"},
        "timezone": {"type": "string"},
    },
    ["hospital_id", "name", "timezone"],
)
DEPARTMENT_SCHEMA = _object_schema(
    {
        "department_id": {"type": "string"},
        "name": {"type": "string"},
        "description": {"type": "string"},
    },
    ["department_id", "name", "description"],
)
DOCTOR_SCHEMA = _object_schema(
    {
        "doctor_id": {"type": "string"},
        "department_id": {"type": "string"},
        "name": {"type": "string"},
        "title": {"type": "string"},
    },
    ["doctor_id", "department_id", "name", "title"],
)
SLOT_SCHEMA = _object_schema(
    {
        "slot_id": {"type": "string"},
        "department_id": {"type": "string"},
        "doctor_id": {"type": "string"},
        "starts_at": {"type": "string", "format": "date-time"},
        "ends_at": {"type": "string", "format": "date-time"},
        "fee_cents": {"type": "integer", "minimum": 0},
        "currency": {"type": "string", "enum": ["CNY"]},
    },
    [
        "slot_id",
        "department_id",
        "doctor_id",
        "starts_at",
        "ends_at",
        "fee_cents",
        "currency",
    ],
)
APPOINTMENT_SCHEMA = _object_schema(
    {
        "appointment_id": {"type": "string"},
        "patient_id": {"type": "string"},
        "slot_id": {"type": "string"},
        "department_id": {"type": "string"},
        "doctor_id": {"type": "string"},
        "starts_at": {"type": "string", "format": "date-time"},
        "ends_at": {"type": "string", "format": "date-time"},
        "fee_cents": {"type": "integer", "minimum": 0},
        "currency": {"type": "string", "enum": ["CNY"]},
        "status": {"type": "string", "enum": ["booked", "cancelled"]},
    },
    [
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
)
CONFIRMATION_SCHEMA = _object_schema(
    {
        "patient": _object_schema(
            {"patient_id": {"type": "string"}}, ["patient_id"]
        ),
        "hospital": HOSPITAL_SCHEMA,
        "department": DEPARTMENT_SCHEMA,
        "doctor": DOCTOR_SCHEMA,
        "slot_id": {"type": "string"},
        "starts_at": {"type": "string", "format": "date-time"},
        "ends_at": {"type": "string", "format": "date-time"},
        "fee_cents": {"type": "integer", "minimum": 0},
        "currency": {"type": "string", "enum": ["CNY"]},
    },
    [
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
)
CANCELLATION_CONFIRMATION_SCHEMA = _object_schema(
    {
        "patient": _object_schema(
            {"patient_id": {"type": "string"}}, ["patient_id"]
        ),
        "hospital": HOSPITAL_SCHEMA,
        "department": DEPARTMENT_SCHEMA,
        "doctor": DOCTOR_SCHEMA,
        "appointment_id": {"type": "string"},
        "starts_at": {"type": "string", "format": "date-time"},
        "ends_at": {"type": "string", "format": "date-time"},
        "fee_cents": {"type": "integer", "minimum": 0},
        "currency": {"type": "string", "enum": ["CNY"]},
    },
    [
        "patient",
        "hospital",
        "department",
        "doctor",
        "appointment_id",
        "starts_at",
        "ends_at",
        "fee_cents",
        "currency",
    ],
)
WAYFINDING_ORIGIN_SCHEMA = _object_schema(
    {
        "origin_id": {"type": "string"},
        "display_name": {"type": "string"},
    },
    ["origin_id", "display_name"],
)
SERVICE_LOCATION_SCHEMA = _object_schema(
    {
        "location_id": {"type": "string"},
        "display_name": {"type": "string"},
        "category": {"type": "string", "enum": ["department", "service"]},
    },
    ["location_id", "display_name", "category"],
)
WAYFINDING_GUIDANCE_SCHEMA = _object_schema(
    {
        "origin": WAYFINDING_ORIGIN_SCHEMA,
        "destination": SERVICE_LOCATION_SCHEMA,
        "mode": {"type": "string", "enum": ["standard", "accessible"]},
        "steps": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        "notice": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "data_version": {"type": "string"},
    },
    ["origin", "destination", "mode", "steps", "notice", "data_version"],
)
WAYFINDING_UNAVAILABLE_SCHEMA = _object_schema(
    {
        "reason": {
            "type": "string",
            "enum": [
                "selection_required",
                "origin_not_found",
                "destination_not_found",
                "accessible_unavailable",
                "unavailable",
            ],
        },
        "message": {"type": "string"},
    },
    ["reason", "message"],
)


@pytest.fixture
def hospital_operations() -> FakeHospitalOperations:
    return FakeHospitalOperations(
        FakeHospitalDataSource.load_default(),
        clock=lambda: datetime(2026, 6, 1, 8, 0, tzinfo=SHANGHAI),
    )


@pytest.mark.asyncio
async def test_provider_exposes_eight_complete_public_contracts(
    hospital_operations: FakeHospitalOperations,
) -> None:
    empty_input = _object_schema({}, [])
    expected = [
        (
            "hospital.get_hospital",
            "hospital_get_hospital",
            "查询服务医院的基本资料。",
            empty_input,
            _object_schema({"hospital": HOSPITAL_SCHEMA}, ["hospital"]),
            "read",
            False,
            ["pre_visit", "in_visit"],
            None,
        ),
        (
            "hospital.list_departments",
            "hospital_list_departments",
            "列出服务医院的科室。",
            empty_input,
            _object_schema(
                {"departments": {"type": "array", "items": DEPARTMENT_SCHEMA}},
                ["departments"],
            ),
            "read",
            False,
            ["pre_visit", "in_visit"],
            None,
        ),
        (
            "hospital.list_doctors",
            "hospital_list_doctors",
            "列出服务医院的医生，可按科室筛选。",
            _object_schema({"department_id": {"type": "string"}}, []),
            _object_schema(
                {"doctors": {"type": "array", "items": DOCTOR_SCHEMA}},
                ["doctors"],
            ),
            "read",
            False,
            ["pre_visit", "in_visit"],
            None,
        ),
        (
            "hospital.search_slots",
            "hospital_search_slots",
            "查询服务医院当前可预约的号源。",
            _object_schema(
                {
                    "department_id": {"type": "string"},
                    "doctor_id": {"type": "string"},
                    "start_date": {"type": "string", "format": "date"},
                    "end_date": {"type": "string", "format": "date"},
                },
                [],
            ),
            _object_schema(
                {"slots": {"type": "array", "items": SLOT_SCHEMA}}, ["slots"]
            ),
            "read",
            False,
            ["pre_visit", "in_visit"],
            None,
        ),
        (
            "hospital.get_appointment",
            "hospital_get_appointment",
            "查询当前患者的一条预约挂号。",
            _object_schema(
                {"appointment_id": {"type": "string"}}, ["appointment_id"]
            ),
            _object_schema(
                {
                    "appointment": {
                        "anyOf": [APPOINTMENT_SCHEMA, {"type": "null"}]
                    }
                },
                ["appointment"],
            ),
            "read",
            False,
            ["pre_visit", "in_visit"],
            None,
        ),
        (
            "hospital.list_appointments",
            "hospital_list_appointments",
            "列出当前患者的预约挂号。",
            empty_input,
            _object_schema(
                {
                    "appointments": {
                        "type": "array",
                        "items": APPOINTMENT_SCHEMA,
                    }
                },
                ["appointments"],
            ),
            "read",
            False,
            ["pre_visit", "in_visit"],
            None,
        ),
        (
            "hospital.list_wayfinding_origins",
            "hospital_list_wayfinding_origins",
            "列出虚构服务医院认可的院内方位指引常用起点。",
            empty_input,
            _object_schema(
                {
                    "origins": {
                        "type": "array",
                        "items": WAYFINDING_ORIGIN_SCHEMA,
                    }
                },
                ["origins"],
            ),
            "read",
            False,
            ["pre_visit", "in_visit"],
            None,
        ),
        (
            "hospital.list_service_locations",
            "hospital_list_service_locations",
            "列出虚构服务医院认可的院内方位指引目的地。",
            empty_input,
            _object_schema(
                {
                    "locations": {
                        "type": "array",
                        "items": SERVICE_LOCATION_SCHEMA,
                    }
                },
                ["locations"],
            ),
            "read",
            False,
            ["pre_visit", "in_visit"],
            None,
        ),
        (
            "hospital.get_wayfinding_guidance",
            "hospital_get_wayfinding_guidance",
            "精确读取虚构服务医院认可的院内文字方位指引，不生成或拼接步骤。",
            _object_schema(
                {
                    "origin_id": {"type": "string"},
                    "destination_id": {"type": "string"},
                    "mode": {
                        "type": "string",
                        "enum": ["standard", "accessible"],
                    },
                },
                ["origin_id", "destination_id", "mode"],
            ),
            _object_schema(
                {
                    "guidance": {
                        "anyOf": [WAYFINDING_GUIDANCE_SCHEMA, {"type": "null"}]
                    },
                    "unavailable": {
                        "anyOf": [WAYFINDING_UNAVAILABLE_SCHEMA, {"type": "null"}]
                    },
                },
                ["guidance", "unavailable"],
            ),
            "read",
            False,
            ["pre_visit", "in_visit"],
            None,
        ),
        (
            "hospital.create_appointment",
            "hospital_create_appointment",
            "为当前患者创建预约挂号。",
            _object_schema({"slot_id": {"type": "string"}}, ["slot_id"]),
            _object_schema(
                {
                    "receipt_id": {"type": "string"},
                    "appointment": APPOINTMENT_SCHEMA,
                },
                ["receipt_id", "appointment"],
            ),
            "write",
            True,
            ["pre_visit"],
            CONFIRMATION_SCHEMA,
        ),
        (
            "hospital.cancel_appointment",
            "hospital_cancel_appointment",
            "取消当前患者的一条预约挂号。",
            _object_schema(
                {"appointment_id": {"type": "string"}}, ["appointment_id"]
            ),
            _object_schema(
                {
                    "receipt_id": {"type": "string"},
                    "appointment": APPOINTMENT_SCHEMA,
                },
                ["receipt_id", "appointment"],
            ),
            "write",
            True,
            ["pre_visit"],
            CANCELLATION_CONFIRMATION_SCHEMA,
        ),
    ]
    tools = await HospitalToolProvider(hospital_operations).tools()

    assert [tool.contract() for tool in tools] == [
        {
            "tool_id": tool_id,
                "version": (
                    "2"
                    if tool_id
                    in {
                        "hospital.get_appointment",
                        "hospital.list_appointments",
                        "hospital.create_appointment",
                    }
                    else "1"
                ),
            "name": name,
            "description": description,
            "input_schema": input_schema,
            "output_schema": output_schema,
            "confirmation_schema": confirmation_schema,
            "effect": effect,
            "allowed_stages": stages,
            "provider_approval_required": approval_required,
        }
        for (
            tool_id,
            name,
            description,
            input_schema,
            output_schema,
            effect,
            approval_required,
            stages,
            confirmation_schema,
        ) in expected
    ]


@pytest.mark.asyncio
async def test_get_hospital_tool_has_public_contract_and_stable_envelope(
    hospital_operations: FakeHospitalOperations,
) -> None:
    provider = HospitalToolProvider(hospital_operations)

    tool = next(
        item for item in await provider.tools() if item.tool_id == "hospital.get_hospital"
    )
    result = await tool.execute({}, ToolContext())

    assert result == {
        "hospital": {
            "hospital_id": "hospital-minghe",
            "name": "明和虚构医院",
            "timezone": "Asia/Shanghai",
        }
    }


@pytest.mark.asyncio
async def test_wayfinding_tools_return_exact_data_and_a_structured_card(
    hospital_operations: FakeHospitalOperations,
) -> None:
    tools = {
        item.tool_id: item
        for item in await HospitalToolProvider(hospital_operations).tools()
    }

    origins = await tools["hospital.list_wayfinding_origins"].execute({}, ToolContext())
    locations = await tools["hospital.list_service_locations"].execute({}, ToolContext())
    guidance_tool = tools["hospital.get_wayfinding_guidance"]
    guidance = await guidance_tool.execute(
        {
            "origin_id": "origin-main-entrance",
            "destination_id": "location-pediatrics",
            "mode": "accessible",
        },
        ToolContext(
            participant_tool_selection=ParticipantToolSelection(
                tool_name="hospital_get_wayfinding_guidance",
                arguments=(
                    ("destination_id", "location-pediatrics"),
                    ("mode", "accessible"),
                    ("origin_id", "origin-main-entrance"),
                ),
            )
        ),
    )
    origin_items = cast(list[dict[str, object]], origins["origins"])
    location_items = cast(list[dict[str, object]], locations["locations"])
    guidance_data = cast(dict[str, object], guidance["guidance"])

    assert origin_items[0] == {
        "origin_id": "origin-main-entrance",
        "display_name": "门诊楼一层主入口",
    }
    assert location_items[0] == {
        "location_id": "location-pediatrics",
        "display_name": "儿科门诊",
        "category": "department",
    }
    assert guidance == {
        "guidance": {
            "origin": origin_items[0],
            "destination": location_items[0],
            "mode": "accessible",
            "steps": [
                "从主入口进入门诊大厅，沿右侧无障碍通道前行至电梯厅。",
                "乘电梯到二层，出电梯后按儿科门诊指示牌左转。",
                "沿走廊前行至儿科门诊报到台。",
            ],
            "notice": None,
            "data_version": "minghe-wayfinding-2026-09-01",
        },
        "unavailable": None,
    }
    assert guidance_tool.present is not None
    assert await guidance_tool.present(guidance, ToolContext()) == (
        {
            "type": "data-hospital-wayfinding",
            "data": {
                "origin": {
                    "id": "origin-main-entrance",
                    "name": "门诊楼一层主入口",
                },
                "destination": {
                    "id": "location-pediatrics",
                    "name": "儿科门诊",
                },
                "mode": "accessible",
                "steps": guidance_data["steps"],
                "notice": None,
                "dataVersion": "minghe-wayfinding-2026-09-01",
            },
        },
    )


@pytest.mark.asyncio
async def test_list_departments_tool_returns_a_stable_list_envelope(
    hospital_operations: FakeHospitalOperations,
) -> None:
    tool = next(
        item
        for item in await HospitalToolProvider(hospital_operations).tools()
        if item.tool_id == "hospital.list_departments"
    )

    result = await tool.execute({}, ToolContext())

    assert tool.version == "1"
    assert tool.name == "hospital_list_departments"
    assert tool.effect == "read"
    assert tool.approval_required is False
    assert tool.allowed_stages == ("pre_visit", "in_visit")
    assert tool.input_schema == {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    }
    assert tool.output_schema["required"] == ["departments"]
    departments = cast(list[dict[str, object]], result["departments"])
    assert len(departments) == 24
    assert departments[0] == {
        "department_id": "department-general",
        "name": "全科医学科",
        "description": "提供常见健康问题的门诊评估与连续照护。",
    }
    assert departments[-1] == {
        "department_id": "department-infectious-diseases",
        "name": "感染性疾病科",
        "description": "提供常见感染性疾病的专科门诊评估与随访。",
    }


@pytest.mark.asyncio
async def test_list_doctors_tool_filters_and_preserves_empty_list_semantics(
    hospital_operations: FakeHospitalOperations,
) -> None:
    tool = next(
        item
        for item in await HospitalToolProvider(hospital_operations).tools()
        if item.tool_id == "hospital.list_doctors"
    )

    matching = await tool.execute(
        {"department_id": "department-pediatrics"}, ToolContext()
    )
    missing = await tool.execute({"department_id": "department-missing"}, ToolContext())

    assert tool.name == "hospital_list_doctors"
    assert tool.input_schema == {
        "type": "object",
        "properties": {"department_id": {"type": "string"}},
        "required": [],
        "additionalProperties": False,
    }
    assert tool.output_schema["required"] == ["doctors"]
    assert matching == {
        "doctors": [
            {
                "doctor_id": "doctor-zhou-an",
                "department_id": "department-pediatrics",
                "name": "周安",
                "title": "主治医师",
            }
        ]
    }
    assert missing == {"doctors": []}


@pytest.mark.asyncio
async def test_search_slots_tool_serializes_dates_times_and_money(
    hospital_operations: FakeHospitalOperations,
) -> None:
    tool = next(
        item
        for item in await HospitalToolProvider(hospital_operations).tools()
        if item.tool_id == "hospital.search_slots"
    )

    matching = await tool.execute(
        {
            "department_id": "department-general",
            "start_date": "2026-06-02",
            "end_date": "2026-06-02",
        },
        ToolContext(),
    )
    missing = await tool.execute(
        {"doctor_id": "doctor-missing", "start_date": "2026-06-02"},
        ToolContext(),
    )

    assert tool.name == "hospital_search_slots"
    assert tool.input_schema == {
        "type": "object",
        "properties": {
            "department_id": {"type": "string"},
            "doctor_id": {"type": "string"},
            "start_date": {"type": "string", "format": "date"},
            "end_date": {"type": "string", "format": "date"},
        },
        "required": [],
        "additionalProperties": False,
    }
    assert tool.output_schema["required"] == ["slots"]
    assert matching == {
        "slots": [
            {
                "slot_id": "slot-general-morning-20260602-0930",
                "department_id": "department-general",
                "doctor_id": "doctor-chen-mingyuan",
                "starts_at": "2026-06-02T09:30:00+08:00",
                "ends_at": "2026-06-02T10:00:00+08:00",
                "fee_cents": 2500,
                "currency": "CNY",
            }
        ]
    }
    assert missing == {"slots": []}


@pytest.mark.asyncio
async def test_appointment_queries_take_patient_scope_only_from_tool_context(
    hospital_operations: FakeHospitalOperations,
) -> None:
    tools = await HospitalToolProvider(hospital_operations).tools()
    get_appointment = next(
        item for item in tools if item.tool_id == "hospital.get_appointment"
    )
    list_appointments = next(
        item for item in tools if item.tool_id == "hospital.list_appointments"
    )

    visible = await get_appointment.execute(
        {"appointment_id": "appointment-seed-001"},
        ToolContext(patient_id="patient-seed"),
    )
    hidden = await get_appointment.execute(
        {"appointment_id": "appointment-seed-001"},
        ToolContext(patient_id="patient-other"),
    )
    scoped_list = await list_appointments.execute(
        {}, ToolContext(patient_id="patient-seed")
    )
    empty_list = await list_appointments.execute(
        {}, ToolContext(patient_id="patient-other")
    )

    assert get_appointment.input_schema == {
        "type": "object",
        "properties": {"appointment_id": {"type": "string"}},
        "required": ["appointment_id"],
        "additionalProperties": False,
    }
    assert list_appointments.input_schema == {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    }
    assert visible == {
        "appointment": {
            "appointment_id": "appointment-seed-001",
            "patient_id": "patient-seed",
            "slot_id": "slot-general-morning-20260602-0900",
            "department_id": "department-general",
            "doctor_id": "doctor-chen-mingyuan",
            "starts_at": "2026-06-02T09:00:00+08:00",
            "ends_at": "2026-06-02T09:30:00+08:00",
            "fee_cents": 2500,
            "currency": "CNY",
            "status": "booked",
        }
    }
    assert hidden == {"appointment": None}
    assert scoped_list == {"appointments": [visible["appointment"]]}
    assert empty_list == {"appointments": []}


@pytest.mark.asyncio
async def test_create_appointment_uses_authoritative_confirmation_and_context_keys(
    hospital_operations: FakeHospitalOperations,
) -> None:
    tools = await HospitalToolProvider(hospital_operations).tools()
    create = next(
        item for item in tools if item.tool_id == "hospital.create_appointment"
    )
    list_appointments = next(
        item for item in tools if item.tool_id == "hospital.list_appointments"
    )
    context = ToolContext(
        patient_id="patient-new",
        idempotency_key="hospital-action-key",
        visit_stage="pre_visit",
    )
    arguments: dict[str, object] = {
        "slot_id": "slot-general-morning-20260602-0930"
    }

    assert create.input_schema == {
        "type": "object",
        "properties": {"slot_id": {"type": "string"}},
        "required": ["slot_id"],
        "additionalProperties": False,
    }
    assert create.effect == "write"
    assert create.approval_required is True
    assert create.allowed_stages == ("pre_visit",)
    assert create.confirmation_contract is not None

    confirmation = await create.confirmation_contract.prepare(arguments, context)
    before_commit = await list_appointments.execute({}, context)

    assert confirmation == {
        "patient": {"patient_id": "patient-new"},
        "hospital": {
            "hospital_id": "hospital-minghe",
            "name": "明和虚构医院",
            "timezone": "Asia/Shanghai",
        },
        "department": {
            "department_id": "department-general",
            "name": "全科医学科",
            "description": "提供常见健康问题的门诊评估与连续照护。",
        },
        "doctor": {
            "doctor_id": "doctor-chen-mingyuan",
            "department_id": "department-general",
            "name": "陈明远",
            "title": "副主任医师",
        },
        "slot_id": "slot-general-morning-20260602-0930",
        "starts_at": "2026-06-02T09:30:00+08:00",
        "ends_at": "2026-06-02T10:00:00+08:00",
        "fee_cents": 2500,
        "currency": "CNY",
    }
    assert before_commit == {"appointments": []}
    assert await create.confirmation_contract.revalidate(
        arguments, confirmation, context
    )

    created = await create.execute(arguments, context)
    duplicate = await create.execute(arguments, context)
    after_commit = await list_appointments.execute({}, context)
    appointments = cast(list[dict[str, object]], after_commit["appointments"])
    created_appointment = cast(dict[str, object], created["appointment"])

    assert duplicate == created
    assert created["receipt_id"] == "receipt-fake-0001"
    assert created_appointment == appointments[0]
    assert created_appointment["patient_id"] == "patient-new"
    assert len(appointments) == 1
    assert not await create.confirmation_contract.revalidate(
        arguments, confirmation, context
    )


@pytest.mark.asyncio
async def test_cancel_appointment_uses_patient_scoped_confirmation(
    hospital_operations: FakeHospitalOperations,
) -> None:
    tools = await HospitalToolProvider(hospital_operations).tools()
    cancel = next(
        item for item in tools if item.tool_id == "hospital.cancel_appointment"
    )
    context = ToolContext(
        patient_id="patient-seed",
        idempotency_key="cancel-action-key",
        visit_stage="pre_visit",
    )
    arguments: dict[str, object] = {"appointment_id": "appointment-seed-001"}

    assert cancel.effect == "write"
    assert cancel.approval_required is True
    assert cancel.allowed_stages == ("pre_visit",)
    assert cancel.confirmation_contract is not None

    confirmation = await cancel.confirmation_contract.prepare(arguments, context)
    assert confirmation["patient"] == {"patient_id": "patient-seed"}
    assert confirmation["appointment_id"] == "appointment-seed-001"
    assert confirmation["doctor"] == {
        "doctor_id": "doctor-chen-mingyuan",
        "department_id": "department-general",
        "name": "陈明远",
        "title": "副主任医师",
    }
    assert await cancel.confirmation_contract.revalidate(
        arguments, confirmation, context
    )

    result = await cancel.execute(arguments, context)
    duplicate = await cancel.execute(arguments, context)
    cancelled = cast(dict[str, object], result["appointment"])

    assert duplicate == result
    assert cancelled["status"] == "cancelled"
    assert not await cancel.confirmation_contract.revalidate(
        arguments, confirmation, context
    )


def test_startup_synchronizes_all_hospital_tools_as_disabled() -> None:
    operations = FakeHospitalOperations(
        FakeHospitalDataSource.load_default(),
        clock=lambda: datetime(2026, 6, 1, 8, 0, tzinfo=SHANGHAI),
    )
    registry = InMemoryToolRegistry()
    app = create_app(
        tool_registry=registry,
        tool_provider=HospitalToolProvider(operations),
        management_token="management-secret",
        environment="development",
    )

    with TestClient(app) as client:
        response = client.get(
            "/v1/admin/tools",
            headers={"Authorization": "Bearer management-secret"},
        )

    assert response.status_code == 200
    tools = response.json()["tools"]
    assert [item["tool_id"] for item in tools] == [
        "hospital.get_hospital",
        "hospital.list_departments",
        "hospital.list_doctors",
        "hospital.search_slots",
        "hospital.get_appointment",
        "hospital.list_appointments",
        "hospital.list_wayfinding_origins",
        "hospital.list_service_locations",
        "hospital.get_wayfinding_guidance",
        "hospital.create_appointment",
        "hospital.cancel_appointment",
    ]
    versions = [item["versions"][0] for item in tools]
    assert [item["version"] for item in versions] == [
        "1",
        "1",
        "1",
        "1",
        "2",
        "2",
        "1",
        "1",
        "1",
        "2",
        "1",
    ]
    assert all(item["available"] is True for item in versions)
    assert all(item["enabled"] is False for item in versions)
    assert [item["name"] for item in versions] == [
        "hospital_get_hospital",
        "hospital_list_departments",
        "hospital_list_doctors",
        "hospital_search_slots",
        "hospital_get_appointment",
        "hospital_list_appointments",
        "hospital_list_wayfinding_origins",
        "hospital_list_service_locations",
        "hospital_get_wayfinding_guidance",
        "hospital_create_appointment",
        "hospital_cancel_appointment",
    ]
    assert [item["effect"] for item in versions] == [
        "read",
        "read",
        "read",
        "read",
        "read",
        "read",
        "read",
        "read",
        "read",
        "write",
        "write",
    ]
    assert [item["output_schema"]["required"] for item in versions] == [
        ["hospital"],
        ["departments"],
        ["doctors"],
        ["slots"],
        ["appointment"],
        ["appointments"],
        ["origins"],
        ["locations"],
        ["guidance", "unavailable"],
        ["receipt_id", "appointment"],
        ["receipt_id", "appointment"],
    ]
    assert [item["provider_approval_required"] for item in versions] == [
        False,
        False,
        False,
        False,
        False,
        False,
        False,
        False,
        False,
        True,
        True,
    ]
    assert [item["approval_required"] for item in versions] == [
        False,
        False,
        False,
        False,
        False,
        False,
        False,
        False,
        False,
        True,
        True,
    ]
    assert [item["confirmation_schema"] is not None for item in versions] == [
        False,
        False,
        False,
        False,
        False,
        False,
        False,
        False,
        False,
        True,
        True,
    ]
    assert [item["allowed_stages"] for item in versions] == [
        ["pre_visit", "in_visit"],
        ["pre_visit", "in_visit"],
        ["pre_visit", "in_visit"],
        ["pre_visit", "in_visit"],
        ["pre_visit", "in_visit"],
        ["pre_visit", "in_visit"],
        ["pre_visit", "in_visit"],
        ["pre_visit", "in_visit"],
        ["pre_visit", "in_visit"],
        ["pre_visit"],
        ["pre_visit"],
    ]


@pytest.mark.asyncio
async def test_action_store_confirms_hospital_proposal_exactly_once(
    hospital_operations: FakeHospitalOperations,
) -> None:
    trusted = next(
        item
        for item in await HospitalToolProvider(hospital_operations).tools()
        if item.tool_id == "hospital.create_appointment"
    )
    tool = _tool_definition(trusted)
    store = InMemoryActionStore()
    request_context = ToolContext(
        visit_matter_id="visit-1",
        participant_id="participant-1",
        patient_id="patient-new",
        idempotency_key="request-1",
        visit_stage="pre_visit",
    )
    arguments: dict[str, object] = {
        "slot_id": "slot-general-morning-20260602-0930"
    }
    assert trusted.confirmation_contract is not None
    confirmation = await trusted.confirmation_contract.prepare(
        arguments, request_context
    )
    proposal = await store.create_proposal(
        tool,
        arguments,
        request_context,
        confirmation=confirmation,
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    decision_context = ToolContext(
        visit_matter_id="visit-1",
        participant_id="participant-1",
        patient_id="patient-new",
        idempotency_key="decision-1",
        visit_stage="pre_visit",
    )

    confirmed, receipt = await store.confirm(
        proposal.proposal_id, decision_context, tool
    )
    duplicate, duplicate_receipt = await store.confirm(
        proposal.proposal_id, decision_context, tool
    )

    assert confirmed.status == duplicate.status == "confirmed"
    assert receipt == duplicate_receipt
    assert receipt.result["receipt_id"] == "receipt-fake-0001"
    appointments = await hospital_operations.query(
        ListAppointmentsQuery(patient_id="patient-new")
    )
    assert len(appointments) == 1


@pytest.mark.asyncio
async def test_confirmation_rejects_a_slot_occupied_after_proposal(
    hospital_operations: FakeHospitalOperations,
) -> None:
    trusted = next(
        item
        for item in await HospitalToolProvider(hospital_operations).tools()
        if item.tool_id == "hospital.create_appointment"
    )
    tool = _tool_definition(trusted)
    store = InMemoryActionStore()
    request_context = ToolContext(
        visit_matter_id="visit-1",
        participant_id="participant-1",
        patient_id="patient-new",
        idempotency_key="request-occupied",
        visit_stage="pre_visit",
    )
    arguments: dict[str, object] = {
        "slot_id": "slot-general-morning-20260602-0930"
    }
    assert trusted.confirmation_contract is not None
    confirmation = await trusted.confirmation_contract.prepare(
        arguments, request_context
    )
    proposal = await store.create_proposal(
        tool,
        arguments,
        request_context,
        confirmation=confirmation,
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    await hospital_operations.commit(
        CreateAppointmentAction(
            patient_id="patient-other",
            slot_id="slot-general-morning-20260602-0930",
            idempotency_key="competing-action",
        )
    )

    with pytest.raises(ActionDecisionError, match="已变化"):
        await store.confirm(
            proposal.proposal_id,
            ToolContext(
                visit_matter_id="visit-1",
                participant_id="participant-1",
                patient_id="patient-new",
                idempotency_key="decision-occupied",
                visit_stage="pre_visit",
            ),
            tool,
        )


@pytest.mark.asyncio
async def test_hospital_service_failure_is_exposed_as_tool_failure() -> None:
    operations = FakeHospitalOperations(
        FakeHospitalDataSource.load_default(),
        clock=lambda: datetime(2026, 6, 1, 8, 0, tzinfo=SHANGHAI),
        failures=FakeHospitalFailurePlan(query_failures=1),
    )
    get_hospital = next(
        item
        for item in await HospitalToolProvider(operations).tools()
        if item.tool_id == "hospital.get_hospital"
    )

    with pytest.raises(HospitalUnavailableError):
        await get_hospital.execute({}, ToolContext())


def _tool_definition(tool: TrustedTool) -> ToolDefinition:
    return ToolDefinition(
        tool_id=tool.tool_id,
        name=tool.name,
        version=tool.version,
        description=tool.description,
        input_schema=tool.input_schema,
        output_schema=tool.output_schema,
        confirmation_contract=tool.confirmation_contract,
        effect=tool.effect,
        approval_required=tool.approval_required,
        execute=tool.execute,
    )
