from __future__ import annotations

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


class TurnEvent(BaseModel):
    kind: Literal["status", "text", "data", "completed", "failed"]
    data: dict[str, Any] = Field(default_factory=dict)


class ActionDecisionResponse(BaseModel):
    proposal_id: str
    decision: Literal["confirm", "reject"]
    events: list[TurnEvent]
