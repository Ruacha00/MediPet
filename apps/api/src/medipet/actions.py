from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol
from uuid import uuid4

from medipet.agent.capabilities import ToolContext, ToolDefinition
from medipet.schema import validate_object

ActionProposalStatus = Literal["pending", "confirmed", "rejected", "expired"]


@dataclass(frozen=True)
class ActionProposal:
    proposal_id: str
    visit_matter_id: str
    participant_id: str
    request_key: str
    idempotency_key: str
    tool_id: str
    tool_name: str
    tool_version: str
    arguments: dict[str, object]
    status: ActionProposalStatus
    created_at: datetime
    expires_at: datetime
    receipt_id: str | None = None

    def event_data(self) -> dict[str, object]:
        data: dict[str, object] = {
            "proposalId": self.proposal_id,
            "visitMatterId": self.visit_matter_id,
            "participantId": self.participant_id,
            "toolId": self.tool_id,
            "toolName": self.tool_name,
            "toolVersion": self.tool_version,
            "arguments": self.arguments,
            "status": self.status,
            "idempotencyKey": self.idempotency_key,
            "expiresAt": self.expires_at.isoformat(),
        }
        if self.receipt_id is not None:
            data["receiptId"] = self.receipt_id
        return {"type": "data-action-proposal", "data": data}


@dataclass(frozen=True)
class ActionReceipt:
    receipt_id: str
    proposal_id: str
    idempotency_key: str
    result: dict[str, object]
    created_at: datetime


@dataclass(frozen=True)
class ActionAudit:
    action: str
    proposal_id: str
    participant_id: str
    visit_matter_id: str
    decision_key: str
    created_at: datetime
    receipt_id: str | None = None


class ActionDecisionError(ValueError):
    pass


class ActionStore(Protocol):
    async def create_proposal(
        self,
        tool: ToolDefinition,
        arguments: dict[str, object],
        context: ToolContext,
        *,
        expires_at: datetime,
    ) -> ActionProposal: ...

    async def get_proposal(self, proposal_id: str) -> ActionProposal: ...

    async def reject(
        self,
        proposal_id: str,
        context: ToolContext,
    ) -> ActionProposal: ...

    async def confirm(
        self,
        proposal_id: str,
        context: ToolContext,
        tool: ToolDefinition,
    ) -> tuple[ActionProposal, ActionReceipt]: ...

    async def record_decision_rejection(
        self,
        proposal_id: str,
        context: ToolContext,
    ) -> None: ...


class InMemoryActionStore:
    def __init__(self, *, now: Callable[[], datetime] | None = None) -> None:
        self._now = now or (lambda: datetime.now(UTC))
        self._lock = asyncio.Lock()
        self._proposals: dict[str, ActionProposal] = {}
        self._request_proposals: dict[tuple[str, str, str], str] = {}
        self._receipts: dict[str, ActionReceipt] = {}
        self._audits: list[ActionAudit] = []

    async def create_proposal(
        self,
        tool: ToolDefinition,
        arguments: dict[str, object],
        context: ToolContext,
        *,
        expires_at: datetime,
    ) -> ActionProposal:
        async with self._lock:
            request_identity = (
                context.visit_matter_id,
                context.participant_id,
                context.idempotency_key,
            )
            existing_id = self._request_proposals.get(request_identity)
            if existing_id is not None:
                existing = self._proposals[existing_id]
                if (
                    existing.tool_id != tool.tool_id
                    or existing.tool_version != tool.version
                    or existing.arguments != arguments
                ):
                    raise ActionDecisionError("同一请求不能改变操作参数或 Tool 版本")
                return existing
            proposal_id = f"proposal-{uuid4().hex}"
            proposal = ActionProposal(
                proposal_id=proposal_id,
                visit_matter_id=context.visit_matter_id,
                participant_id=context.participant_id,
                request_key=context.idempotency_key,
                idempotency_key=f"action-{proposal_id}",
                tool_id=tool.tool_id,
                tool_name=tool.name,
                tool_version=tool.version,
                arguments=dict(arguments),
                status="pending",
                created_at=self._now(),
                expires_at=expires_at,
            )
            self._proposals[proposal_id] = proposal
            self._request_proposals[request_identity] = proposal_id
            self._audit("propose", proposal, context.idempotency_key)
            return proposal

    async def get_proposal(self, proposal_id: str) -> ActionProposal:
        async with self._lock:
            return self._require(proposal_id)

    async def reject(
        self,
        proposal_id: str,
        context: ToolContext,
    ) -> ActionProposal:
        async with self._lock:
            proposal = self._require(proposal_id)
            self._validate_scope(proposal, context)
            if proposal.status == "confirmed":
                raise ActionDecisionError("该操作已经确认，不能拒绝")
            if proposal.status == "expired" or self._now() >= proposal.expires_at:
                self._expire(proposal, context.idempotency_key)
                raise ActionDecisionError("待确认操作已过期")
            if proposal.status == "rejected":
                return proposal
            rejected = replace(proposal, status="rejected")
            self._proposals[proposal_id] = rejected
            self._audit("reject", rejected, context.idempotency_key)
            return rejected

    async def confirm(
        self,
        proposal_id: str,
        context: ToolContext,
        tool: ToolDefinition,
    ) -> tuple[ActionProposal, ActionReceipt]:
        async with self._lock:
            proposal = self._require(proposal_id)
            self._validate_scope(proposal, context)
            if proposal.status == "confirmed":
                assert proposal.receipt_id is not None
                return proposal, self._receipts[proposal.receipt_id]
            if proposal.status == "rejected":
                raise ActionDecisionError("待确认操作已被拒绝")
            if proposal.status == "expired" or self._now() >= proposal.expires_at:
                self._expire(proposal, context.idempotency_key)
                raise ActionDecisionError("待确认操作已过期")
            if (
                tool.tool_id != proposal.tool_id
                or tool.version != proposal.tool_version
                or tool.name != proposal.tool_name
                or tool.effect != "write"
                or not tool.approval_required
                or not tool.enabled
                or not tool.bound
                or not tool.authorize(context)
            ):
                raise ActionDecisionError("操作参数、Tool 版本或作用域已变化，请重新发起")
            if tool.revalidate is not None and not await tool.revalidate(context):
                raise ActionDecisionError("操作参数、Tool 版本或作用域已变化，请重新发起")
            execution_context = replace(context, idempotency_key=proposal.idempotency_key)
            try:
                result = await tool.execute(dict(proposal.arguments), execution_context)
                if (
                    tool.output_schema is not None
                    and validate_object(result, tool.output_schema) is not None
                ):
                    raise ValueError("Tool output did not match its declared Schema")
            except Exception as error:
                self._audit("commit_failed", proposal, context.idempotency_key)
                raise ActionDecisionError("操作暂时无法完成，请稍后重试") from error
            receipt = ActionReceipt(
                receipt_id=f"receipt-{uuid4().hex}",
                proposal_id=proposal.proposal_id,
                idempotency_key=proposal.idempotency_key,
                result=dict(result),
                created_at=self._now(),
            )
            confirmed = replace(
                proposal,
                status="confirmed",
                receipt_id=receipt.receipt_id,
            )
            self._receipts[receipt.receipt_id] = receipt
            self._proposals[proposal_id] = confirmed
            self._audit(
                "confirm",
                confirmed,
                context.idempotency_key,
                receipt_id=receipt.receipt_id,
            )
            return confirmed, receipt

    async def list_proposals(self) -> list[ActionProposal]:
        async with self._lock:
            return list(self._proposals.values())

    async def list_receipts(self) -> list[ActionReceipt]:
        async with self._lock:
            return list(self._receipts.values())

    async def list_audits(self) -> list[ActionAudit]:
        async with self._lock:
            return list(self._audits)

    async def record_decision_rejection(
        self,
        proposal_id: str,
        context: ToolContext,
    ) -> None:
        async with self._lock:
            proposal = self._proposals.get(proposal_id)
            self._audits.append(
                ActionAudit(
                    action="reject_decision",
                    proposal_id=proposal_id,
                    participant_id=(
                        proposal.participant_id if proposal is not None else context.participant_id
                    ),
                    visit_matter_id=(
                        proposal.visit_matter_id
                        if proposal is not None
                        else context.visit_matter_id
                    ),
                    decision_key=context.idempotency_key,
                    created_at=self._now(),
                )
            )

    def _require(self, proposal_id: str) -> ActionProposal:
        try:
            return self._proposals[proposal_id]
        except KeyError:
            raise ActionDecisionError("待确认操作不存在") from None

    @staticmethod
    def _validate_scope(proposal: ActionProposal, context: ToolContext) -> None:
        if (
            proposal.visit_matter_id != context.visit_matter_id
            or proposal.participant_id != context.participant_id
        ):
            raise ActionDecisionError("该操作不属于当前就诊事项或参与者")

    def _expire(self, proposal: ActionProposal, decision_key: str) -> None:
        if proposal.status != "expired":
            expired = replace(proposal, status="expired")
            self._proposals[proposal.proposal_id] = expired
            self._audit("expire", expired, decision_key)

    def _audit(
        self,
        action: str,
        proposal: ActionProposal,
        decision_key: str,
        *,
        receipt_id: str | None = None,
    ) -> None:
        self._audits.append(
            ActionAudit(
                action=action,
                proposal_id=proposal.proposal_id,
                participant_id=proposal.participant_id,
                visit_matter_id=proposal.visit_matter_id,
                decision_key=decision_key,
                receipt_id=receipt_id,
                created_at=self._now(),
            )
        )


def proposal_expiry(*, now: datetime | None = None, minutes: int = 10) -> datetime:
    return (now or datetime.now(UTC)) + timedelta(minutes=minutes)
