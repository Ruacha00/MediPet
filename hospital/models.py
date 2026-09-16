"""MediPet 医院、事项及展示契约；只定义数据，不执行存储或业务操作。

工具和 HTTP 边界使用 model_dump(mode="json")；Redis 恢复使用
model_validate_json()。身份归属、实时号源和状态转换由业务服务校验。
"""

from datetime import date, datetime
from typing import Annotated, Literal, Self

from health.models import MedicationInfo, ReportSummary, TriageGuidance

from pydantic import (
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    model_validator,
)


DEFAULT_USER_ID = "anonymous"
BUSINESS_TIMEZONE = "Asia/Shanghai"
DEFAULT_PROPOSAL_TTL_SECONDS = 15 * 60

Identifier = Annotated[str, Field(strict=True, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")]
Text = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)]
NonNegativeInt = Annotated[int, Field(strict=True, ge=0)]
PositiveInt = Annotated[int, Field(strict=True, gt=0)]
LocalTime = Annotated[str, Field(pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")]
Period = Literal["morning", "afternoon"]
Operation = Literal["create", "cancel"]
ProposalStatus = Literal["pending", "executed", "expired", "superseded"]
AppointmentStatus = Literal["active", "cancelled"]
ErrorCode = Literal[
    "not_found", "missing_fields", "invalid_input", "no_slots",
    "identity_conflict", "visit_archived", "selection_unavailable",
    "proposal_expired", "proposal_superseded", "target_unavailable",
    "conflict", "storage_unavailable", "artifact_conflict",
]


def _timestamp_input(value: object) -> object:
    if not isinstance(value, (str, datetime)):
        raise ValueError("时间必须为带时区的 ISO 8601 字符串或 datetime")
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    return value


def _date_input(value: object) -> object:
    if isinstance(value, datetime) or not isinstance(value, (str, date)):
        raise ValueError("日期必须为 YYYY-MM-DD 字符串或 date")
    if isinstance(value, str) and (len(value) != 10 or value[4] != "-" or value[7] != "-"):
        raise ValueError("日期必须使用 YYYY-MM-DD")
    return value


Timestamp = Annotated[AwareDatetime, BeforeValidator(_timestamp_input)]
BusinessDate = Annotated[date, BeforeValidator(_date_input)]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Source(ContractModel):
    source_id: Identifier
    title: Text


class HospitalInfo(ContractModel):
    hospital_id: Identifier
    name: Text
    description: Text
    address: Text
    outpatient_hours: Text
    source: Source


class Department(ContractModel):
    department_id: Identifier
    name: Text
    description: Text
    location_id: Identifier
    source: Source


class Doctor(ContractModel):
    doctor_id: Identifier
    department_id: Identifier
    name: Text
    title: Text
    introduction: Text
    source: Source


class Location(ContractModel):
    location_id: Identifier
    name: Text
    building: Text
    floor: Text
    source: Source


class ScheduleTemplate(ContractModel):
    schedule_id: Identifier
    doctor_id: Identifier
    department_id: Identifier
    weekdays: Annotated[list[Annotated[int, Field(strict=True, ge=1, le=7)]], Field(min_length=1)]
    period: Period
    start_time: LocalTime
    end_time: LocalTime
    capacity: PositiveInt
    fee_fen: NonNegativeInt

    @model_validator(mode="after")
    def valid_schedule(self) -> Self:
        if self.end_time <= self.start_time:
            raise ValueError("排班结束时间必须晚于开始时间")
        if len(self.weekdays) != len(set(self.weekdays)):
            raise ValueError("排班星期不能重复；1=周一，7=周日")
        return self


class VisitChecklist(ContractModel):
    checklist_id: Identifier
    department_id: Identifier | None = None
    visit_type: Literal["general", "first", "child"]
    title: Text
    items: Annotated[list[Text], Field(min_length=1)]
    source: Source


class Wayfinding(ContractModel):
    route_id: Identifier
    origin_id: Identifier
    destination_id: Identifier
    mode: Literal["normal", "accessible"]
    steps: Annotated[list[Text], Field(min_length=1)]
    source: Source


class ContactInfo(ContractModel):
    contact_id: Identifier
    label: Text
    phone: Text
    hours: Text
    location: Text
    source: Source
    summary: str = ""
    delivery: Literal["contact_only"] = "contact_only"


class Patient(ContractModel):
    patient_id: Identifier
    user_id: Identifier
    name: Text
    relationship: Literal["self", "family"]


class VisitIdentity(ContractModel):
    """服务器加载并校验事项后构造；模型工具输入不能提供或覆盖这些字段。"""

    user_id: Identifier
    patient_id: Identifier
    conv_id: Identifier


class Visit(VisitIdentity):
    title: Text
    archived: bool = False
    created_at: Timestamp
    updated_at: Timestamp

    @model_validator(mode="after")
    def valid_times(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("事项更新时间不能早于创建时间")
        return self


class DemoData(ContractModel):
    hospital: HospitalInfo
    departments: list[Department]
    doctors: list[Doctor]
    locations: list[Location]
    schedules: list[ScheduleTemplate]
    checklists: list[VisitChecklist]
    wayfinding: list[Wayfinding]
    contact_info: ContactInfo
    patients: list[Patient]

    @model_validator(mode="after")
    def valid_references(self) -> Self:
        groups = [
            (self.departments, "department_id"), (self.doctors, "doctor_id"),
            (self.locations, "location_id"), (self.schedules, "schedule_id"),
            (self.checklists, "checklist_id"), (self.wayfinding, "route_id"),
            (self.patients, "patient_id"),
        ]
        for items, key in groups:
            ids = [getattr(item, key) for item in items]
            if len(ids) != len(set(ids)):
                raise ValueError(f"{key} 不能重复")
        locations = {item.location_id for item in self.locations}
        departments = {item.department_id for item in self.departments}
        doctors = {item.doctor_id: item for item in self.doctors}
        if any(item.location_id not in locations for item in self.departments):
            raise ValueError("科室引用了未知地点")
        if any(item.department_id not in departments for item in self.doctors):
            raise ValueError("医生引用了未知科室")
        for item in self.schedules:
            doctor = doctors.get(item.doctor_id)
            if doctor is None or doctor.department_id != item.department_id:
                raise ValueError("排班医生与科室不匹配")
        if any(item.department_id is not None and item.department_id not in departments for item in self.checklists):
            raise ValueError("准备清单引用了未知科室")
        if any(item.origin_id not in locations or item.destination_id not in locations for item in self.wayfinding):
            raise ValueError("文字指引引用了未知地点")
        return self


class SlotQuery(ContractModel):
    department_id: Identifier | None = None
    doctor_id: Identifier | None = None
    date: BusinessDate | None = None
    period: Period | None = None


class SlotDetails(ContractModel):
    """可保存到预约快照的号源说明；不包含会变化的剩余数量。"""

    slot_id: Identifier
    hospital_name: Text
    department_id: Identifier
    department_name: Text
    doctor_id: Identifier
    doctor_name: Text
    date: BusinessDate
    period: Period
    start_time: LocalTime
    end_time: LocalTime
    fee_fen: NonNegativeInt
    location_id: Identifier
    location_name: Text

    @model_validator(mode="after")
    def valid_time_range(self) -> Self:
        if self.end_time <= self.start_time:
            raise ValueError("号源结束时间必须晚于开始时间")
        return self


class Slot(SlotDetails):
    capacity: PositiveInt
    remaining: NonNegativeInt

    @model_validator(mode="after")
    def within_capacity(self) -> Self:
        if self.remaining > self.capacity:
            raise ValueError("剩余号源不能超过容量")
        return self


class SlotList(ContractModel):
    list_id: Identifier
    query: SlotQuery
    slots: list[Slot]
    queried_at: Timestamp


class AppointmentSnapshot(ContractModel):
    patient_name: Text
    slot: SlotDetails


class AppointmentRecord(VisitIdentity):
    appointment_id: Identifier
    snapshot: AppointmentSnapshot
    status: AppointmentStatus
    created_at: Timestamp
    cancelled_at: Timestamp | None = None

    @model_validator(mode="after")
    def valid_cancellation(self) -> Self:
        if self.status == "cancelled":
            if self.cancelled_at is None or self.cancelled_at < self.created_at:
                raise ValueError("取消记录必须包含不早于创建时间的取消时间")
        elif self.cancelled_at is not None:
            raise ValueError("有效预约不能包含取消时间")
        return self


class ExecutionReceipt(VisitIdentity):
    receipt_id: Identifier
    proposal_id: Identifier
    operation: Operation
    status: Literal["executed"] = "executed"
    appointment: AppointmentRecord
    executed_at: Timestamp

    @model_validator(mode="after")
    def matches_operation(self) -> Self:
        if (self.appointment.user_id, self.appointment.patient_id) != (self.user_id, self.patient_id):
            raise ValueError("回执与预约必须属于同一参与者和患者")
        if self.operation == "create" and self.appointment.conv_id != self.conv_id:
            raise ValueError("创建回执与新预约必须属于同一事项")
        expected = "active" if self.operation == "create" else "cancelled"
        if self.appointment.status != expected:
            raise ValueError("回执操作与预约状态不匹配")
        recorded_at = self.appointment.created_at if self.operation == "create" else self.appointment.cancelled_at
        if self.executed_at < recorded_at:
            raise ValueError("执行时间不能早于预约变更时间")
        return self


class AppointmentProposal(VisitIdentity):
    proposal_id: Identifier
    operation: Operation
    target_id: Identifier
    snapshot: AppointmentSnapshot
    status: ProposalStatus = "pending"
    created_at: Timestamp
    expires_at: Timestamp
    result: ExecutionReceipt | None = None

    @model_validator(mode="after")
    def coherent_proposal(self) -> Self:
        if self.expires_at <= self.created_at:
            raise ValueError("方案到期时间必须晚于创建时间")
        if self.operation == "create" and self.target_id != self.snapshot.slot.slot_id:
            raise ValueError("创建方案目标必须等于展示快照的号源")
        if (self.status == "executed") != (self.result is not None):
            raise ValueError("只有已执行方案必须且能够保存执行回执")
        if self.result is not None:
            receipt = self.result
            record = receipt.appointment
            if receipt.proposal_id != self.proposal_id or receipt.operation != self.operation:
                raise ValueError("回执与方案不匹配")
            if (receipt.user_id, receipt.patient_id, receipt.conv_id) != (self.user_id, self.patient_id, self.conv_id):
                raise ValueError("回执身份与方案不匹配")
            if record.snapshot != self.snapshot:
                raise ValueError("回执快照与确认资料不匹配")
            if self.operation == "cancel" and self.target_id != record.appointment_id:
                raise ValueError("取消方案目标必须等于回执的预约")
            if not self.created_at <= receipt.executed_at < self.expires_at:
                raise ValueError("方案必须在有效期内执行")
        return self


class CatalogData(ContractModel):
    category: Literal["hospital", "department", "doctor", "location"]
    items: list[HospitalInfo | Department | Doctor | Location]

    @model_validator(mode="after")
    def matches_category(self) -> Self:
        item_type = {"hospital": HospitalInfo, "department": Department, "doctor": Doctor, "location": Location}[self.category]
        if any(not isinstance(item, item_type) for item in self.items):
            raise ValueError("目录条目类型与查询类别不匹配")
        return self


ArtifactType = Literal[
    "catalog", "slot_list", "appointment_proposal", "appointment_record",
    "visit_checklist", "wayfinding", "contact_info",
    "triage_guidance", "medication_info", "report_summary",
]
ARTIFACT_DATA_MODELS: dict[str, type[BaseModel]] = {
    "catalog": CatalogData,
    "slot_list": SlotList,
    "appointment_proposal": AppointmentProposal,
    "appointment_record": AppointmentRecord,
    "visit_checklist": VisitChecklist,
    "wayfinding": Wayfinding,
    "contact_info": ContactInfo,
    "triage_guidance": TriageGuidance,
    "medication_info": MedicationInfo,
    "report_summary": ReportSummary,
}


class Artifact(ContractModel):
    """data 保持 JSON 字典，按固定 type 检查内容，便于沿基准工具链透传。"""

    id: Identifier
    type: ArtifactType
    data: dict[str, JsonValue]

    @model_validator(mode="after")
    def valid_data(self) -> Self:
        self.data = ARTIFACT_DATA_MODELS[self.type].model_validate(self.data).model_dump(mode="json")
        return self


class ServiceResult(ContractModel):
    success: bool
    data: dict[str, JsonValue] = Field(default_factory=dict)
    artifacts: list[Artifact] = Field(default_factory=list)
    error_code: ErrorCode | None = None
    error: Text | None = None
    retryable: bool = False

    @model_validator(mode="after")
    def coherent_result(self) -> Self:
        if self.success:
            if self.error_code is not None or self.error is not None or self.retryable:
                raise ValueError("成功结果不能带错误或重试标记")
        elif self.error_code is None or self.error is None:
            raise ValueError("失败结果必须提供稳定错误类别和说明")
        if self.retryable and self.error_code not in ("conflict", "storage_unavailable"):
            raise ValueError("只有并发冲突或存储不可用可标记重试")
        return self


class BusinessError(ContractModel):
    code: ErrorCode
    message: Text
    retryable: bool = False


class ConfirmRequest(ContractModel):
    user_id: Identifier = DEFAULT_USER_ID
    conv_id: Identifier


class ConfirmResponse(ContractModel):
    """只用于成功执行或重放；业务错误经 HTTP detail 返回，不伪造模型字段。"""

    user_id: Identifier
    patient_id: Identifier
    conv_id: Identifier
    receipt: ExecutionReceipt
    artifacts: list[Artifact] = Field(default_factory=list)

    @model_validator(mode="after")
    def matches_identity(self) -> Self:
        receipt = self.receipt
        if (receipt.user_id, receipt.patient_id, receipt.conv_id) != (self.user_id, self.patient_id, self.conv_id):
            raise ValueError("确认响应身份与回执不匹配")
        return self


class VisitMessage(VisitIdentity):
    message_id: Identifier
    role: Literal["user", "assistant", "system"]
    content: str = ""
    created_at: Timestamp
    kind: Literal["chat", "confirmation_event", "operation_result"] = "chat"
    artifacts: list[Artifact] = Field(default_factory=list)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    proposal_id: Identifier | None = None
    receipt_id: Identifier | None = None

    @model_validator(mode="after")
    def valid_message(self) -> Self:
        if not self.content.strip() and not self.artifacts:
            raise ValueError("消息必须包含正文或业务卡片")
        if self.kind != "chat" and self.proposal_id is None:
            raise ValueError("确认事件及操作结果必须关联方案")
        if self.kind == "operation_result" and self.receipt_id is None:
            raise ValueError("成功操作结果必须关联执行回执")
        return self


class SelectionState(VisitIdentity):
    status: Literal["ready", "empty", "failed"] = "empty"
    list_id: Identifier | None = None
    query: SlotQuery = Field(default_factory=SlotQuery)
    slots: list[Slot] = Field(default_factory=list)
    queried_at: Timestamp | None = None
    current_proposal_id: Identifier | None = None

    @model_validator(mode="after")
    def valid_selection(self) -> Self:
        if self.status == "ready":
            if not self.slots or self.list_id is None or self.queried_at is None:
                raise ValueError("可选择状态必须包含真实列表、列表 ID 和查询时间")
        elif self.slots or self.list_id is not None:
            raise ValueError("空结果或查询失败必须清除可选择列表及其 ID")
        return self
