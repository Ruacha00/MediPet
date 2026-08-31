from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class UIMessagePart(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str
    text: str | None = None


class UIMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    role: Literal["system", "user", "assistant"]
    parts: list[UIMessagePart] = Field(default_factory=list)


class ChatTurnRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str | None = None
    messages: list[UIMessage]
    visit_matter_id: str = "visit-matter-demo"
    participant_id: str = "participant-demo"
    idempotency_key: str = Field(default_factory=lambda: uuid4().hex)
    selected_slot_id: str | None = Field(default=None, min_length=1, max_length=256)

    def latest_participant_text(self) -> str:
        for message in reversed(self.messages):
            if message.role != "user":
                continue
            text = "".join(part.text or "" for part in message.parts if part.type == "text")
            if text.strip():
                return text.strip()
        raise ValueError("A participant text message is required")


class ConfirmationDecision(BaseModel):
    proposal_id: str
    decision: Literal["confirm", "reject"]


class ActionDecisionRequest(BaseModel):
    decision: Literal["confirm", "reject"]
    participant_id: str = "participant-demo"
    visit_matter_id: str = "visit-matter-demo"
    idempotency_key: str = Field(default_factory=lambda: uuid4().hex)


class TurnCommand(BaseModel):
    visit_matter_id: str
    participant_id: str
    idempotency_key: str
    message: str | None = None
    confirmation: ConfirmationDecision | None = None
    selected_slot_id: str | None = Field(default=None, min_length=1, max_length=256)


class TurnEvent(BaseModel):
    kind: Literal["status", "text", "data", "completed", "failed"]
    data: dict[str, Any] = Field(default_factory=dict)


class ActionDecisionResponse(BaseModel):
    proposal_id: str
    decision: Literal["confirm", "reject"]
    events: list[TurnEvent]


class CreateVisitMatterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    participant_id: str = Field(min_length=1, max_length=128)
    title: str = Field(default="新的就诊事项", min_length=1, max_length=200)


class VisitMatterResponse(BaseModel):
    visit_matter_id: str
    title: str
    visit_stage: Literal["pre_visit", "in_visit"]
    patient_display_name: str
    participant_display_name: str


class VisitMatterListResponse(BaseModel):
    visit_matters: list[VisitMatterResponse]


class ConversationHistoryMessage(BaseModel):
    id: str
    role: Literal["user", "assistant"]
    state: Literal["pending", "streaming", "completed", "failed", "cancelled"]
    parts: list[UIMessagePart]
    created_at: datetime
    updated_at: datetime


class ConversationHistoryResponse(BaseModel):
    visit_matter_id: str
    messages: list[ConversationHistoryMessage]
