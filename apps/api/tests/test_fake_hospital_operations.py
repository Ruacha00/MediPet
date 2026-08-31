from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from medipet.hospital import (
    ActionReceipt,
    Appointment,
    AppointmentNotCancellableError,
    CancelAppointmentAction,
    CreateAppointmentAction,
    FakeHospitalDataSource,
    FakeHospitalFailurePlan,
    FakeHospitalOperations,
    GetAppointmentQuery,
    GetHospitalQuery,
    Hospital,
    HospitalDataError,
    HospitalNotFoundError,
    HospitalUnavailableError,
    IdempotencyConflictError,
    InvalidHospitalRequestError,
    ListAppointmentsQuery,
    ListDepartmentsQuery,
    ListDoctorsQuery,
    SearchSlotsQuery,
    SlotUnavailableError,
)

SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")
FIXED_NOW = datetime(2026, 8, 30, 10, 0, tzinfo=SHANGHAI)
BASIC_DEPARTMENT_NAMES = [
    "全科医学科",
    "儿科",
    "骨科",
    "皮肤科",
    "普通内科",
    "心血管内科",
    "呼吸内科",
    "消化内科",
    "内分泌科",
    "神经内科",
    "肾内科",
    "风湿免疫科",
    "血液内科",
    "普通外科",
    "泌尿外科",
    "妇科",
    "产科",
    "眼科",
    "耳鼻咽喉科",
    "口腔科",
    "精神心理科",
    "康复医学科",
    "中医科",
    "感染性疾病科",
]


def fixed_clock() -> datetime:
    return FIXED_NOW


@pytest.mark.asyncio
async def test_default_fake_hospital_catalog_is_queryable() -> None:
    operations = FakeHospitalOperations(
        FakeHospitalDataSource.load_default(),
        clock=fixed_clock,
    )

    hospital = await operations.query(GetHospitalQuery())
    departments = await operations.query(ListDepartmentsQuery())
    doctors = await operations.query(ListDoctorsQuery(department_id="department-general"))
    slots = await operations.query(
        SearchSlotsQuery(
            department_id="department-general",
            start_date=FIXED_NOW.date(),
            end_date=FIXED_NOW.date() + timedelta(days=14),
        )
    )

    assert hospital == Hospital(
        hospital_id="hospital-minghe",
        name="明和虚构医院",
        timezone="Asia/Shanghai",
    )
    assert [department.name for department in departments] == BASIC_DEPARTMENT_NAMES
    assert [doctor.name for doctor in doctors] == ["陈明远"]
    assert slots
    assert all(slot.department_id == "department-general" for slot in slots)
    assert all(slot.starts_at.tzname() == "Asia/Shanghai" for slot in slots)
    assert all(slot.starts_at.utcoffset() == timedelta(hours=8) for slot in slots)
    assert all(slot.fee_cents > 0 and slot.currency == "CNY" for slot in slots)


@pytest.mark.asyncio
async def test_every_basic_department_has_a_doctor_and_available_slots() -> None:
    source = FakeHospitalDataSource.load_default()
    operations = FakeHospitalOperations(source, clock=fixed_clock)

    assert len(source.departments) == len(BASIC_DEPARTMENT_NAMES)
    assert len(source.doctors) == len(BASIC_DEPARTMENT_NAMES)
    assert len(source.schedules) == len(BASIC_DEPARTMENT_NAMES)
    for department in source.departments:
        doctors = await operations.query(
            ListDoctorsQuery(department_id=department.department_id)
        )
        slots = await operations.query(
            SearchSlotsQuery(
                department_id=department.department_id,
                start_date=FIXED_NOW.date(),
                end_date=FIXED_NOW.date() + timedelta(days=14),
            )
        )

        assert doctors, department.name
        assert slots, department.name
        assert {slot.doctor_id for slot in slots} <= {
            doctor.doctor_id for doctor in doctors
        }


@pytest.mark.asyncio
async def test_created_appointment_is_patient_scoped_and_occupies_the_slot() -> None:
    operations = FakeHospitalOperations(
        FakeHospitalDataSource.load_default(),
        clock=fixed_clock,
    )
    available = await operations.query(
        SearchSlotsQuery(department_id="department-pediatrics")
    )
    slot = available[0]

    receipt = await operations.commit(
        CreateAppointmentAction(
            patient_id="patient-001",
            slot_id=slot.slot_id,
            idempotency_key="appointment-action-001",
        )
    )

    assert receipt.idempotency_key == "appointment-action-001"
    assert receipt.appointment == Appointment(
        appointment_id=receipt.appointment.appointment_id,
        patient_id="patient-001",
        slot_id=slot.slot_id,
        department_id=slot.department_id,
        doctor_id=slot.doctor_id,
        starts_at=slot.starts_at,
        ends_at=slot.ends_at,
        fee_cents=slot.fee_cents,
    )
    assert await operations.query(
        GetAppointmentQuery(
            patient_id="patient-001",
            appointment_id=receipt.appointment.appointment_id,
        )
    ) == receipt.appointment
    assert await operations.query(ListAppointmentsQuery(patient_id="patient-001")) == (
        receipt.appointment,
    )
    assert slot.slot_id not in {
        item.slot_id
        for item in await operations.query(
            SearchSlotsQuery(department_id="department-pediatrics")
        )
    }


@pytest.mark.asyncio
async def test_cancelled_appointment_is_idempotent_and_releases_the_slot() -> None:
    operations = FakeHospitalOperations(
        FakeHospitalDataSource.load_default(),
        clock=fixed_clock,
    )
    action = CancelAppointmentAction(
        patient_id="patient-seed",
        appointment_id="appointment-seed-001",
        idempotency_key="cancel-action-001",
    )

    receipt = await operations.commit(action)
    duplicate = await operations.commit(action)
    appointment = await operations.query(
        GetAppointmentQuery(
            patient_id="patient-seed",
            appointment_id="appointment-seed-001",
        )
    )

    assert duplicate == receipt
    assert appointment.status == "cancelled"
    assert appointment == receipt.appointment
    assert appointment.slot_id in {
        slot.slot_id for slot in await operations.query(SearchSlotsQuery())
    }
    with pytest.raises(AppointmentNotCancellableError, match="不可取消"):
        await operations.commit(
            CancelAppointmentAction(
                patient_id="patient-seed",
                appointment_id="appointment-seed-001",
                idempotency_key="cancel-action-002",
            )
        )


@pytest.mark.asyncio
async def test_appointment_commit_is_idempotent_and_rejects_changed_parameters() -> None:
    operations = FakeHospitalOperations(
        FakeHospitalDataSource.load_default(),
        clock=fixed_clock,
    )
    slots = await operations.query(SearchSlotsQuery())
    action = CreateAppointmentAction(
        patient_id="patient-001",
        slot_id=slots[0].slot_id,
        idempotency_key="appointment-action-001",
    )

    receipt = await operations.commit(action)
    duplicate = await operations.commit(action)

    assert duplicate == receipt
    with pytest.raises(IdempotencyConflictError, match="幂等键"):
        await operations.commit(
            CreateAppointmentAction(
                patient_id="patient-002",
                slot_id=slots[1].slot_id,
                idempotency_key=action.idempotency_key,
            )
        )


@pytest.mark.asyncio
async def test_injected_query_failure_is_deterministic_and_retryable() -> None:
    operations = FakeHospitalOperations(
        FakeHospitalDataSource.load_default(),
        clock=fixed_clock,
        failures=FakeHospitalFailurePlan(query_failures=1),
    )

    with pytest.raises(HospitalUnavailableError, match="查询"):
        await operations.query(GetHospitalQuery())

    assert await operations.query(GetHospitalQuery()) == Hospital(
        hospital_id="hospital-minghe",
        name="明和虚构医院",
        timezone="Asia/Shanghai",
    )


def test_fake_hospital_data_rejects_duplicate_schedule_times(tmp_path: Path) -> None:
    data = _minimal_hospital_data()
    data["schedules"][0]["times"] = ["09:00", "09:00"]
    path = tmp_path / "hospital.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(HospitalDataError, match="排班时间.*重复"):
        FakeHospitalDataSource.load(path)


def test_fake_hospital_data_rejects_a_department_without_doctors(
    tmp_path: Path,
) -> None:
    data = _minimal_hospital_data()
    data["departments"].append(
        {
            "id": "department-unstaffed",
            "name": "无医生科室",
            "description": "仅用于验证目录完整性。",
        }
    )

    with pytest.raises(HospitalDataError, match="每个科室.*医生"):
        _load_hospital_data(tmp_path, data)


def test_fake_hospital_data_rejects_a_doctor_without_schedules(
    tmp_path: Path,
) -> None:
    data = _minimal_hospital_data()
    data["doctors"].append(
        {
            "id": "doctor-unscheduled",
            "department_id": "department-test",
            "name": "无排班医生",
            "title": "主治医师",
        }
    )

    with pytest.raises(HospitalDataError, match="每名医生.*排班"):
        _load_hospital_data(tmp_path, data)


def _minimal_hospital_data() -> dict[str, Any]:
    return {
        "hospital": {
            "id": "hospital-test",
            "name": "测试虚构医院",
            "timezone": "Asia/Shanghai",
        },
        "departments": [
            {
                "id": "department-test",
                "name": "测试科",
                "description": "仅供自动化测试。",
            }
        ],
        "doctors": [
            {
                "id": "doctor-test",
                "department_id": "department-test",
                "name": "测试医生",
                "title": "主治医师",
            }
        ],
        "schedules": [
            {
                "id": "schedule-test",
                "doctor_id": "doctor-test",
                "day_offsets": [1],
                "times": ["09:00"],
                "duration_minutes": 30,
                "fee_cents": 1000,
            }
        ],
        "initial_bookings": [],
    }


@pytest.mark.asyncio
async def test_create_appointment_rejects_blank_identifiers() -> None:
    operations = FakeHospitalOperations(
        FakeHospitalDataSource.load_default(),
        clock=fixed_clock,
    )

    with pytest.raises(InvalidHospitalRequestError, match="不能为空"):
        await operations.commit(
            CreateAppointmentAction(
                patient_id=" ",
                slot_id="slot-any",
                idempotency_key="appointment-action-001",
            )
        )


@pytest.mark.asyncio
async def test_concurrent_appointment_commits_only_occupy_a_slot_once() -> None:
    operations = FakeHospitalOperations(
        FakeHospitalDataSource.load_default(),
        clock=fixed_clock,
    )
    slot = (await operations.query(SearchSlotsQuery()))[0]

    results = await asyncio.gather(
        operations.commit(
            CreateAppointmentAction("patient-001", slot.slot_id, "appointment-action-001")
        ),
        operations.commit(
            CreateAppointmentAction("patient-002", slot.slot_id, "appointment-action-002")
        ),
        return_exceptions=True,
    )

    assert sum(isinstance(result, ActionReceipt) for result in results) == 1
    assert sum(isinstance(result, SlotUnavailableError) for result in results) == 1


@pytest.mark.asyncio
async def test_injected_commit_failure_has_no_effect_before_retry() -> None:
    operations = FakeHospitalOperations(
        FakeHospitalDataSource.load_default(),
        clock=fixed_clock,
        failures=FakeHospitalFailurePlan(commit_failures=1),
    )
    slot = (await operations.query(SearchSlotsQuery()))[0]
    action = CreateAppointmentAction("patient-001", slot.slot_id, "appointment-action-001")

    with pytest.raises(HospitalUnavailableError, match="提交"):
        await operations.commit(action)

    receipt = await operations.commit(action)
    assert await operations.query(ListAppointmentsQuery("patient-001")) == (
        receipt.appointment,
    )


@pytest.mark.asyncio
async def test_appointment_queries_do_not_cross_patient_scope() -> None:
    operations = FakeHospitalOperations(
        FakeHospitalDataSource.load_default(),
        clock=fixed_clock,
    )

    with pytest.raises(HospitalNotFoundError, match="预约不存在"):
        await operations.query(
            GetAppointmentQuery(
                patient_id="patient-other",
                appointment_id="appointment-seed-001",
            )
        )

    seeded = await operations.query(
        GetAppointmentQuery(
            patient_id="patient-seed",
            appointment_id="appointment-seed-001",
        )
    )
    assert seeded.patient_id == "patient-seed"


@pytest.mark.asyncio
async def test_slot_search_rejects_an_inverted_date_range() -> None:
    operations = FakeHospitalOperations(
        FakeHospitalDataSource.load_default(),
        clock=fixed_clock,
    )

    with pytest.raises(InvalidHospitalRequestError, match="日期范围"):
        await operations.query(
            SearchSlotsQuery(
                start_date=FIXED_NOW.date() + timedelta(days=2),
                end_date=FIXED_NOW.date() + timedelta(days=1),
            )
        )


@pytest.mark.asyncio
async def test_appointment_queries_reject_a_blank_patient_id() -> None:
    operations = FakeHospitalOperations(
        FakeHospitalDataSource.load_default(),
        clock=fixed_clock,
    )

    with pytest.raises(InvalidHospitalRequestError, match="患者"):
        await operations.query(ListAppointmentsQuery(patient_id=" "))


@pytest.mark.asyncio
async def test_slot_search_excludes_today_slots_that_have_already_started(
    tmp_path: Path,
) -> None:
    data = _minimal_hospital_data()
    data["schedules"][0]["day_offsets"] = [0]
    data["schedules"][0]["times"] = ["09:00", "11:00"]
    source = _load_hospital_data(tmp_path, data)
    operations = FakeHospitalOperations(source, clock=fixed_clock)

    slots = await operations.query(SearchSlotsQuery())

    assert [slot.starts_at.hour for slot in slots] == [11]


@pytest.mark.asyncio
async def test_new_appointment_id_never_overwrites_an_initial_appointment(
    tmp_path: Path,
) -> None:
    data = _minimal_hospital_data()
    data["schedules"][0]["times"] = ["09:00", "09:30"]
    data["initial_bookings"] = [
        {
            "appointment_id": "appointment-fake-0002",
            "patient_id": "patient-seed",
            "template_id": "schedule-test",
            "day_offset": 1,
            "time": "09:00",
        }
    ]
    source = _load_hospital_data(tmp_path, data)
    operations = FakeHospitalOperations(source, clock=fixed_clock)
    slot = (await operations.query(SearchSlotsQuery()))[0]

    created = await operations.commit(
        CreateAppointmentAction("patient-new", slot.slot_id, "appointment-action-new")
    )

    seeded = await operations.query(
        GetAppointmentQuery("patient-seed", "appointment-fake-0002")
    )
    assert seeded.patient_id == "patient-seed"
    assert created.appointment.appointment_id != seeded.appointment_id


def _load_hospital_data(
    tmp_path: Path, data: dict[str, Any]
) -> FakeHospitalDataSource:
    path = tmp_path / "hospital.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return FakeHospitalDataSource.load(path)
