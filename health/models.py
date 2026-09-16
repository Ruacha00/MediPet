"""健康信息卡片契约；不包含身份、存储或模型推断。"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class HealthModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceCitation(HealthModel):
    source_id: str
    title: str
    url: str
    reviewed_at: str


class GuidanceSection(HealthModel):
    heading: str
    items: list[str] = Field(default_factory=list)


class RecommendedDepartment(HealthModel):
    department_id: str
    name: str
    reason: str


class TriageGuidance(HealthModel):
    title: str
    summary: str
    recommended_departments: list[RecommendedDepartment] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    sources: list[SourceCitation] = Field(default_factory=list)


class MedicationInfo(HealthModel):
    title: str
    summary: str
    drug_name: str
    formulation: str
    sections: list[GuidanceSection] = Field(default_factory=list)
    sources: list[SourceCitation] = Field(default_factory=list)


class ReportObservation(HealthModel):
    item: str
    value: str
    unit: str = ""
    reference_range: str = ""
    flag: Literal["unassessed", "below", "within", "above"] = "unassessed"
    raw_line: str


class ReportSummary(HealthModel):
    title: str
    summary: str
    input_kind: Literal["text", "pdf", "image"] = "text"
    extracted_text: str
    observations: list[ReportObservation] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    sources: list[SourceCitation] = Field(default_factory=list)
