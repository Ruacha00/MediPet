"""H01 数据契约测试；医院服务和真实 Redis 事务在后续 issue 验证。"""

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import re

import pytest
from pydantic import ValidationError

from hospital.models import (
    ARTIFACT_DATA_MODELS,
    DEFAULT_PROPOSAL_TTL_SECONDS,
    DEFAULT_USER_ID,
    AppointmentProposal,
    AppointmentRecord,
    Artifact,
    BusinessError,
    ConfirmRequest,
    ConfirmResponse,
    DemoData,
    ExecutionReceipt,
    Patient,
    ScheduleTemplate,
    SelectionState,
    ServiceResult,
    Slot,
    SlotQuery,
    Visit,
    VisitMessage,
)
from hospital.service import HospitalService
from hospital.store import HospitalStore


CONTRACT_PATH = Path(__file__).parents[1] / "docs/internal/rebuild/contracts.md"
IDENTITY = {"user_id": "anonymous", "patient_id": "patient_child", "conv_id": "visit-example"}


def contract_example(marker):
    text = CONTRACT_PATH.read_text(encoding="utf-8")
    match = re.search(rf"<!-- {marker} -->\s*```json\s*\n(.*?)\n```", text, re.DOTALL)
    assert match, f"契约缺少 {marker} JSON 样例"
    return json.loads(match.group(1))


@pytest.fixture
def artifacts():
    return {item["type"]: item for item in contract_example("artifact-examples")}


def create_receipt(artifacts):
    return {
        **IDENTITY,
        "receipt_id": "receipt:proposal-example",
        "proposal_id": "proposal-example",
        "operation": "create",
        "appointment": deepcopy(artifacts["appointment_record"]["data"]),
        "executed_at": "2026-09-16T10:02:00+08:00",
    }


@pytest.mark.parametrize("kind", list(ARTIFACT_DATA_MODELS))
def test_documented_artifact_is_valid_and_survives_json_roundtrip(kind, artifacts):
    artifact = Artifact.model_validate(artifacts[kind])
    restored = Artifact.model_validate_json(artifact.model_dump_json())
    assert restored == artifact
    assert json.loads(json.dumps(artifact.model_dump(mode="json"))) == artifacts[kind]


def test_artifact_rejects_unknown_type_or_wrong_payload(artifacts):
    item = deepcopy(artifacts["slot_list"])
    item["type"] = "invented_appointment"
    with pytest.raises(ValidationError):
        Artifact.model_validate(item)
    item["type"] = "appointment_record"
    with pytest.raises(ValidationError):
        Artifact.model_validate(item)
    item = deepcopy(artifacts["catalog"])
    item["data"]["category"] = "doctor"
    with pytest.raises(ValidationError, match="目录条目类型"):
        Artifact.model_validate(item)


def test_pending_and_executed_proposals_preserve_snapshot_and_saved_receipt(artifacts):
    pending = AppointmentProposal.model_validate(artifacts["appointment_proposal"]["data"])
    assert pending.result is None
    assert pending.expires_at - pending.created_at == timedelta(seconds=DEFAULT_PROPOSAL_TTL_SECONDS)
    executed = AppointmentProposal.model_validate({
        **pending.model_dump(mode="json"),
        "status": "executed",
        "result": create_receipt(artifacts),
    })
    restored = AppointmentProposal.model_validate_json(executed.model_dump_json())
    assert restored.result.receipt_id == "receipt:proposal-example"
    assert restored.result.appointment.snapshot == pending.snapshot
    assert restored.result.appointment.appointment_id == "appointment-example"
    assert restored.result.executed_at.utcoffset() == timedelta(hours=8)


def test_cancellation_from_another_visit_preserves_original_appointment_visit():
    receipt = ExecutionReceipt.model_validate(contract_example("cancellation-example"))
    assert receipt.conv_id == "visit-followup"
    assert receipt.appointment.conv_id == "visit-example"
    proposal = AppointmentProposal(
        user_id=receipt.user_id,
        patient_id=receipt.patient_id,
        conv_id=receipt.conv_id,
        proposal_id=receipt.proposal_id,
        operation="cancel",
        target_id=receipt.appointment.appointment_id,
        snapshot=receipt.appointment.snapshot,
        status="executed",
        created_at="2026-09-16T10:05:00+08:00",
        expires_at="2026-09-16T10:20:00+08:00",
        result=receipt,
    )
    response = ConfirmResponse(
        user_id=receipt.user_id, patient_id=receipt.patient_id, conv_id=receipt.conv_id,
        receipt=receipt,
        artifacts=[Artifact(
            id="appointment:appointment-example:receipt:proposal-cancel-example",
            type="appointment_record", data=receipt.appointment.model_dump(mode="json"),
        )],
    )
    assert ConfirmResponse.model_validate_json(response.model_dump_json()).receipt == proposal.result
    assert proposal.target_id == receipt.appointment.appointment_id


@pytest.mark.parametrize("change", [
    {"status": "executed"},
    {"target_id": "different-slot"},
    {"expires_at": "2026-09-16T10:00:00+08:00"},
    {"status": "confirmed"},
])
def test_proposal_rejects_inconsistent_state_target_or_deadline(change, artifacts):
    data = {**artifacts["appointment_proposal"]["data"], **change}
    with pytest.raises(ValidationError):
        AppointmentProposal.model_validate(data)


@pytest.mark.parametrize("change", [
    {"conv_id": "another-visit"},
    {"patient_id": "patient_self"},
    {"operation": "cancel"},
])
def test_create_receipt_rejects_identity_or_operation_conflicts(change, artifacts):
    with pytest.raises(ValidationError):
        ExecutionReceipt.model_validate({**create_receipt(artifacts), **change})


def test_cancellation_receipt_still_rejects_another_patient():
    data = contract_example("cancellation-example")
    data["patient_id"] = "patient_self"
    with pytest.raises(ValidationError, match="同一参与者和患者"):
        ExecutionReceipt.model_validate(data)


def test_saved_receipt_must_match_proposal_identity_snapshot_and_execution_window(artifacts):
    data = {**artifacts["appointment_proposal"]["data"], "status": "executed", "result": create_receipt(artifacts)}
    for mutate in (
        lambda value: value.update(conv_id="other-visit"),
        lambda value: value["snapshot"]["slot"].update(fee_fen=9999),
        lambda value: value["result"].update(executed_at="2026-09-16T10:16:00+08:00"),
        lambda value: value.update(status="superseded"),
    ):
        changed = deepcopy(data)
        mutate(changed)
        with pytest.raises(ValidationError):
            AppointmentProposal.model_validate(changed)


@pytest.mark.parametrize("change", [
    {"fee_fen": -1}, {"fee_fen": 20.1}, {"fee_fen": "2000"}, {"fee_fen": True},
    {"remaining": 6}, {"remaining": -1}, {"capacity": 0},
    {"date": "2026-02-30"}, {"date": "2026-09-17T00:00:00+08:00"},
    {"start_time": "25:00"}, {"end_time": "08:00"},
])
def test_slot_rejects_invalid_money_capacity_date_and_time(change, artifacts):
    data = {**artifacts["slot_list"]["data"]["slots"][0], **change}
    with pytest.raises(ValidationError):
        Slot.model_validate(data)


@pytest.mark.parametrize("timestamp", ["2026-09-16T10:01:00", 1789524000, "1789524000", "not-a-time"])
def test_record_rejects_naive_or_non_iso_timestamp(timestamp, artifacts):
    with pytest.raises(ValidationError):
        AppointmentRecord.model_validate({**artifacts["appointment_record"]["data"], "created_at": timestamp})


def test_appointment_requires_coherent_cancellation_time(artifacts):
    record = artifacts["appointment_record"]["data"]
    for change in (
        {"status": "cancelled"},
        {"cancelled_at": "2026-09-16T10:10:00+08:00"},
        {"status": "cancelled", "cancelled_at": "2026-09-16T09:00:00+08:00"},
    ):
        with pytest.raises(ValidationError):
            AppointmentRecord.model_validate({**record, **change})


def test_selection_cannot_reuse_stale_lists_after_empty_or_failed_query(artifacts):
    result = artifacts["slot_list"]["data"]
    ready = SelectionState(**IDENTITY, status="ready", **result, current_proposal_id="proposal-example")
    assert ready.slots[0].slot_id == "slot-example"
    for status in ("empty", "failed"):
        with pytest.raises(ValidationError, match="清除"):
            SelectionState.model_validate({**ready.model_dump(mode="json"), "status": status})
        cleared = SelectionState(**IDENTITY, status=status, query=result["query"], current_proposal_id="proposal-example")
        assert cleared.slots == [] and cleared.list_id is None
        assert cleared.current_proposal_id == "proposal-example"
    with pytest.raises(ValidationError, match="真实列表"):
        SelectionState(**IDENTITY, status="ready")


def test_service_result_is_compatible_with_existing_tool_json_and_error_string(artifacts):
    result = ServiceResult(success=True, data={"receipt": create_receipt(artifacts)}, artifacts=[artifacts["appointment_record"]])
    wire = json.loads(json.dumps(result.model_dump(mode="json"), ensure_ascii=False))
    assert wire["success"] is True and wire["error"] is None
    assert wire["artifacts"][0]["data"]["appointment_id"] == "appointment-example"
    failed = ServiceResult(success=False, error_code="conflict", error="号源同时被其他请求修改，请重试。", retryable=True)
    detail = BusinessError(code=failed.error_code, message=failed.error, retryable=failed.retryable)
    assert {"detail": detail.model_dump(mode="json")} == {
        "detail": {"code": "conflict", "message": failed.error, "retryable": True},
    }


@pytest.mark.parametrize("data", [
    {"success": False},
    {"success": True, "error_code": "not_found", "error": "不存在"},
    {"success": False, "error_code": "proposal_expired", "error": "已过期", "retryable": True},
])
def test_service_result_rejects_inconsistent_success_and_error(data):
    with pytest.raises(ValidationError):
        ServiceResult.model_validate(data)


def test_patient_visit_and_full_message_roundtrip_preserves_cards_and_operation_identity(artifacts):
    patient = Patient(patient_id="patient_child", user_id=DEFAULT_USER_ID, name="林小满", relationship="family")
    visit = Visit(**IDENTITY, title="儿科就诊", created_at="2026-09-16T09:00:00+08:00", updated_at="2026-09-16T10:02:00+08:00")
    message = VisitMessage(
        **IDENTITY, message_id="result:receipt:proposal-example", role="system", kind="operation_result",
        content="预约已创建。", created_at="2026-09-16T10:02:00+08:00",
        proposal_id="proposal-example", receipt_id="receipt:proposal-example",
        artifacts=[artifacts["appointment_record"]], metadata={"source": "confirmation"},
    )
    assert patient.patient_id == visit.patient_id
    assert Visit.model_validate_json(visit.model_dump_json()) == visit
    restored = VisitMessage.model_validate_json(message.model_dump_json())
    assert restored.artifacts[0].data == artifacts["appointment_record"]["data"]
    assert restored.receipt_id == message.receipt_id
    assert "agent_type" not in restored.model_dump() and "tool_traces" not in restored.model_dump()
    with pytest.raises(ValidationError, match="执行回执"):
        VisitMessage.model_validate({**message.model_dump(mode="json"), "receipt_id": None})


def test_confirm_request_has_existing_default_and_rejects_identity_override():
    assert ConfirmRequest(conv_id="visit-example").user_id == "anonymous"
    with pytest.raises(ValidationError):
        ConfirmRequest.model_validate({"conv_id": "visit-example", "patient_id": "patient_self"})
    with pytest.raises(ValidationError):
        ConfirmRequest(conv_id="../other-visit")


@pytest.fixture
def demo_data(artifacts):
    source = {"source_id": "hospital-public", "title": "演示医院资料"}
    return {
        "hospital": deepcopy(artifacts["catalog"]["data"]["items"][0]),
        "departments": [{"department_id": "dep_pediatrics", "name": "儿科", "description": "儿科公开说明", "location_id": "pediatrics-room", "source": source}],
        "doctors": [{"doctor_id": "doctor_xu", "department_id": "dep_pediatrics", "name": "许知宁", "title": "医师", "introduction": "虚构医生", "source": source}],
        "locations": [{"location_id": value, "name": value, "building": "门诊楼", "floor": "一层", "source": source} for value in ("pediatrics-room", "hall", "pharmacy")],
        "schedules": [{"schedule_id": "schedule-child", "doctor_id": "doctor_xu", "department_id": "dep_pediatrics", "weekdays": [1, 3, 5], "period": "morning", "start_time": "09:00", "end_time": "11:00", "capacity": 5, "fee_fen": 2000}],
        "checklists": [deepcopy(artifacts["visit_checklist"]["data"])],
        "wayfinding": [deepcopy(artifacts["wayfinding"]["data"])],
        "contact_info": deepcopy(artifacts["contact_info"]["data"]),
        "patients": [{"patient_id": "patient_child", "user_id": "anonymous", "name": "林小满", "relationship": "family"}],
    }


def test_static_fact_models_validate_references_without_loading_runtime_services(demo_data):
    parsed = DemoData.model_validate(demo_data)
    assert DemoData.model_validate_json(parsed.model_dump_json()) == parsed
    for mutate in (
        lambda value: value["departments"][0].update(location_id="missing"),
        lambda value: value["schedules"][0].update(doctor_id="missing"),
        lambda value: value["wayfinding"][0].update(destination_id="missing"),
        lambda value: value["patients"].append(deepcopy(value["patients"][0])),
    ):
        changed = deepcopy(demo_data)
        mutate(changed)
        with pytest.raises(ValidationError):
            DemoData.model_validate(changed)


def test_schedule_rejects_repeated_weekdays_and_query_retains_optional_filters(demo_data):
    schedule = demo_data["schedules"][0]
    with pytest.raises(ValidationError, match="星期不能重复"):
        ScheduleTemplate.model_validate({**schedule, "weekdays": [1, 1]})
    with pytest.raises(ValidationError):
        ScheduleTemplate.model_validate({**schedule, "weekdays": [0]})
    assert SlotQuery(date="2026-09-17").model_dump(mode="json") == {
        "department_id": None, "doctor_id": None, "date": "2026-09-17", "period": None,
    }


@pytest.fixture
def hospital_facts():
    path = Path(__file__).parents[1] / "hospital/demo_data.json"
    return DemoData.model_validate_json(path.read_text(encoding="utf-8"))


def test_demo_facts_define_one_hospital_three_departments_and_two_related_patients(hospital_facts):
    facts = hospital_facts
    assert facts.hospital.name == "明和虚构医院"
    assert "虚构" in facts.hospital.description
    assert {item.name for item in facts.departments} == {"内科", "儿科", "眼科"}
    assert {item.doctor_id for item in facts.doctors} == {"doctor_lin", "doctor_xu", "doctor_gu"}
    assert [(item.patient_id, item.user_id, item.relationship) for item in facts.patients] == [
        ("patient_self", DEFAULT_USER_ID, "self"),
        ("patient_child", DEFAULT_USER_ID, "family"),
    ]
    assert facts.contact_info.delivery == "contact_only"
    assert "演示号码" in facts.contact_info.phone
    assert facts.contact_info.summary == ""


def test_fixed_week_has_complete_consistent_schedule_inputs(hospital_facts):
    # Verify static inputs here; H04 separately exercises persisted generation.
    start = date(2026, 9, 16)
    doctor_fees = {"doctor_lin": 1500, "doctor_xu": 2000, "doctor_gu": 2000}
    for day_offset in range(7):
        day = start + timedelta(days=day_offset)
        schedules = [item for item in hospital_facts.schedules if day.isoweekday() in item.weekdays]
        assert len(schedules) == 6
        assert {(item.doctor_id, item.period) for item in schedules} == {
            (doctor_id, period) for doctor_id in doctor_fees for period in ("morning", "afternoon")
        }
        for item in schedules:
            assert item.capacity == 5
            assert item.fee_fen == doctor_fees[item.doctor_id]
            expected = ("09:00", "11:00") if item.period == "morning" else ("14:00", "16:00")
            assert (item.start_time, item.end_time) == expected


def test_demo_wayfinding_is_limited_and_contains_normal_and_accessible_alternatives(hospital_facts):
    routes = {(item.origin_id, item.destination_id, item.mode): item for item in hospital_facts.wayfinding}
    assert len(routes) == len(hospital_facts.wayfinding) == 14
    pairs = {(origin, destination) for origin, destination, _ in routes}
    for origin, destination in pairs:
        normal = routes[origin, destination, "normal"]
        accessible = routes[origin, destination, "accessible"]
        assert normal.steps != accessible.steps
        assert normal.source == accessible.source
        assert all("楼梯" not in step for step in accessible.steps)
    assert ("hall", "pediatrics-room", "accessible") in routes
    assert any("电梯" in step for step in routes["hall", "pediatrics-room", "accessible"].steps)
    assert ("pharmacy", "ophthalmology-room", "normal") not in routes


def test_demo_checklists_and_static_file_have_no_runtime_booking_state(hospital_facts):
    assert {item.visit_type for item in hospital_facts.checklists} == {"general", "first", "child"}
    child = next(item for item in hospital_facts.checklists if item.visit_type == "child")
    assert child.department_id == "dep_pediatrics"
    assert any("监护人" in item for item in child.items)
    wire = hospital_facts.model_dump(mode="json")

    def all_keys(value):
        if isinstance(value, dict):
            return set(value) | set().union(*(all_keys(item) for item in value.values()))
        if isinstance(value, list):
            return set().union(*(all_keys(item) for item in value))
        return set()

    assert not {"remaining", "appointment_id", "proposal_id", "conv_id", "receipt_id"} & all_keys(wire)


@pytest.fixture
def hospital_service(hospital_facts):
    return HospitalService(
        data=hospital_facts,
        clock=lambda: datetime(2026, 9, 16, 2, tzinfo=timezone.utc),
    )


def test_catalog_uses_actual_facts_names_ids_and_stable_order(hospital_service):
    service = hospital_service
    assert service.query_hospital_catalog().data["items"][0]["name"] == "明和虚构医院"
    by_name = service.query_hospital_catalog("doctor", department="儿科", date="明天")
    by_id = service.query_hospital_catalog("doctor", department="dep_pediatrics", date="2026-09-17")
    assert by_name.data == by_id.data
    assert [item["doctor_id"] for item in by_id.data["items"]] == ["doctor_xu"]
    assert {item["fee_fen"] for item in by_id.data["schedules"]} == {2000}
    assert service.query_hospital_catalog("doctor", doctor="许知宁").data["items"] == by_id.data["items"]
    assert service.query_hospital_catalog("department", department="内科").data["items"][0]["department_id"] == "dep_internal"
    locations = service.query_hospital_catalog("location").data["items"]
    assert [item["location_id"] for item in locations] == sorted(item["location_id"] for item in locations)
    assert Artifact.model_validate(by_id.artifacts[0].model_dump(mode="json")).data["items"] == by_id.data["items"]


@pytest.mark.parametrize("arguments,error", [
    ({"category": "treatment"}, "invalid_input"),
    ({"category": "doctor", "department": "不存在科"}, "not_found"),
    ({"category": "doctor", "doctor": "不存在医生"}, "not_found"),
    ({"category": "doctor", "date": "2026-02-30"}, "invalid_input"),
])
def test_catalog_reports_unknown_input_without_fabricating_items(hospital_service, arguments, error):
    result = hospital_service.query_hospital_catalog(**arguments)
    assert not result.success and result.error_code == error
    assert result.artifacts == []


def test_catalog_empty_filters_and_shanghai_date_window(hospital_service):
    result = hospital_service.query_hospital_catalog("doctor", department="内科", doctor="许知宁")
    assert result.success and result.data["items"] == []
    assert hospital_service.query_hospital_catalog("doctor", date="2026-09-23").data["items"] == []
    assert hospital_service.query_hospital_catalog("doctor", date="2026-09-15").data["items"] == []
    assert hospital_service.resolve_date("后天") == date(2026, 9, 18)
    midnight = HospitalService(clock=lambda: datetime(2026, 9, 16, 16, 1, tzinfo=timezone.utc))
    assert midnight.resolve_date("今天") == date(2026, 9, 17)
    assert midnight.resolve_date("明天") == date(2026, 9, 18)


def test_checklists_are_exact_predefined_materials_with_sources(hospital_service):
    child = hospital_service.get_visit_checklist("儿科", "child")
    expected = next(item for item in hospital_service.data.checklists if item.checklist_id == "child-first")
    assert child.data == expected.model_dump(mode="json")
    assert child.artifacts[0].data == child.data
    first = hospital_service.get_visit_checklist("内科", "first")
    assert first.success and first.data["department_id"] is None
    assert hospital_service.get_visit_checklist("眼科", "child").error_code == "not_found"
    assert hospital_service.get_visit_checklist("不存在科").error_code == "not_found"
    assert hospital_service.get_visit_checklist(visit_type="diagnose").error_code == "invalid_input"


def test_wayfinding_returns_stored_steps_only_and_never_computes_missing_route(hospital_service):
    for mode in ("normal", "accessible"):
        result = hospital_service.get_wayfinding("门诊大厅", "儿科诊区", mode)
        expected = next(item for item in hospital_service.data.wayfinding if item.route_id == f"hall-pediatrics-room-{mode}")
        assert result.data == expected.model_dump(mode="json")
        assert result.artifacts[0].type == "wayfinding"
    for origin, destination, mode, code in (
        (None, "pharmacy", "normal", "missing_fields"),
        ("hall", None, "normal", "missing_fields"),
        ("不存在地点", "pharmacy", "normal", "not_found"),
        ("pharmacy", "ophthalmology-room", "normal", "not_found"),
        ("hall", "pharmacy", "driving", "invalid_input"),
    ):
        failed = hospital_service.get_wayfinding(origin, destination, mode)
        assert failed.error_code == code and not failed.artifacts


def test_readonly_queries_leave_facts_unchanged_and_return_serializable_results(hospital_service):
    before = hospital_service.data.model_dump_json()
    results = [
        hospital_service.query_hospital_catalog("doctor", date="明天"),
        hospital_service.get_visit_checklist("儿科", "child"),
        hospital_service.get_wayfinding("hall", "pharmacy", "accessible"),
    ]
    for result in results:
        assert ServiceResult.model_validate_json(result.model_dump_json()) == result
        json.dumps(result.model_dump(mode="json"), ensure_ascii=False)
        assert {artifact.type for artifact in result.artifacts} <= {"catalog", "visit_checklist", "wayfinding"}
    assert hospital_service.data.model_dump_json() == before


class MemorySlotRedis:
    """Small key/value test double; real Redis SET NX is checked separately."""

    def __init__(self):
        self.values = {}
        self.set_options = []

    async def set(self, key, value, **options):
        self.set_options.append(options)
        if options.get("nx") and key in self.values:
            return False
        self.values[key] = value
        return True

    async def get(self, key):
        return self.values.get(key)

    async def mget(self, keys):
        return [self.values.get(key) for key in keys]

    def pipeline(self, transaction=False):
        owner = self

        class Pipeline:
            def __init__(self):
                self.commands = []

            def set(self, key, value, **options):
                self.commands.append((key, value, options))
                return self

            async def execute(self):
                return [await owner.set(key, value, **options) for key, value, options in self.commands]

        return Pipeline()


@pytest.fixture
def inventory_service(hospital_facts):
    clock = [datetime(2026, 9, 16, 2, tzinfo=timezone.utc)]
    client = MemorySlotRedis()
    store = HospitalStore(client, prefix="medipet:test:h04:")
    service = HospitalService(data=hospital_facts, clock=lambda: clock[0], store=store)
    return service, client, clock


@pytest.mark.asyncio
async def test_slot_initialization_is_stable_and_does_not_reset_existing_inventory(inventory_service):
    service, client, _ = inventory_service
    assert (await service.initialize_slots()).data == {"created": 42}
    result = await service.search_slots(department="儿科", date="明天")
    assert result.success and len(result.data["slots"]) == 2
    first = Slot.model_validate(result.data["slots"][0])
    first.remaining = 1
    await client.set(service.store.slot_key(first.slot_id), first.model_dump_json())
    assert (await service.initialize_slots()).data == {"created": 0}
    restarted = HospitalService(data=service.data, clock=service._clock, store=HospitalStore(client, service.store.prefix))
    assert (await restarted.initialize_slots()).data == {"created": 0}
    reread = await restarted.search_slots(department="dep_pediatrics", date="2026-09-17")
    assert [item["slot_id"] for item in reread.data["slots"]] == [item["slot_id"] for item in result.data["slots"]]
    assert reread.data["slots"][0]["remaining"] == 1
    assert all("ex" not in item and "px" not in item for item in client.set_options)


@pytest.mark.asyncio
async def test_slots_follow_filters_clock_window_and_stable_order(inventory_service):
    service, _, clock = inventory_service
    result = await service.search_slots(department="儿科", doctor="许知宁", date="后天", period="afternoon")
    assert result.success and len(result.data["slots"]) == 1
    assert result.data["slots"][0]["date"] == "2026-09-18"
    assert result.data["slots"][0]["period"] == "afternoon"
    all_slots = (await service.search_slots()).data["slots"]
    order = [(item["date"], item["start_time"], item["doctor_id"], item["slot_id"]) for item in all_slots]
    assert order == sorted(order) and len(order) == 42
    clock[0] = datetime(2026, 9, 16, 3, tzinfo=timezone.utc)  # 11:00 Shanghai
    today = await service.search_slots(date="今天")
    assert len(today.data["slots"]) == 3 and {item["period"] for item in today.data["slots"]} == {"afternoon"}
    clock[0] = datetime(2026, 9, 17, 2, tzinfo=timezone.utc)
    assert (await service.initialize_slots()).data == {"created": 6}
    next_week = (await service.search_slots()).data["slots"]
    assert {item["date"] for item in next_week} == {(date(2026, 9, 17) + timedelta(days=n)).isoformat() for n in range(7)}


@pytest.mark.asyncio
async def test_zero_inventory_and_unknown_filters_are_distinct(inventory_service):
    service, client, _ = inventory_service
    one = await service.search_slots(doctor="doctor_xu", date="明天", period="morning")
    slot = Slot.model_validate(one.data["slots"][0])
    slot.remaining = 0
    await client.set(service.store.slot_key(slot.slot_id), slot.model_dump_json())
    empty = await service.search_slots(doctor="doctor_xu", date="明天", period="morning")
    assert not empty.success and empty.error_code == "no_slots"
    assert empty.data["slots"] == [] and empty.artifacts[0].type == "slot_list"
    assert empty.artifacts[0].data["slots"] == []
    for args, code in (
        ({"department": "不存在科"}, "not_found"),
        ({"doctor": "不存在医生"}, "not_found"),
        ({"period": "night"}, "invalid_input"),
        ({"date": "2026-02-30"}, "invalid_input"),
        ({"date": "2026-09-23"}, "no_slots"),
        ({"department": "内科", "doctor": "许知宁"}, "no_slots"),
    ):
        assert (await service.search_slots(**args)).error_code == code


@pytest.mark.asyncio
async def test_slot_storage_failure_is_explicit_without_fake_results(hospital_facts):
    from redis.exceptions import ConnectionError as RedisConnectionError

    disconnected = HospitalService(data=hospital_facts)
    result = await disconnected.search_slots(date="明天")
    assert result.error_code == "storage_unavailable" and result.retryable
    assert not result.artifacts

    class FailingStore:
        async def ensure_slots(self, slots):
            raise RedisConnectionError("test storage unavailable")

    disconnected.store = FailingStore()
    initialized = await disconnected.initialize_slots()
    assert initialized.error_code == "storage_unavailable" and initialized.retryable
    queried = await disconnected.search_slots()
    assert not queried.success and queried.data == {} and queried.artifacts == []
