from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from medipet.actions import (
    ActionAudit,
    ActionAuditKind,
    ActionDecisionError,
    ActionProposal,
    ActionProposalExpiredError,
    ActionProposalStatus,
    ActionReceipt,
)
from medipet.agent.capabilities import ToolContext, ToolDefinition
from medipet.persistence.models import (
    ActionAuditRecord,
    ActionProposalRecord,
    ActionReceiptRecord,
)
from medipet.persistence.postgres import postgres_async_url
from medipet.schema import validate_object


class PostgresActionStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._sessions = async_sessionmaker(engine, expire_on_commit=False)

    @classmethod
    def from_url(cls, url: str) -> PostgresActionStore:
        return cls(create_async_engine(postgres_async_url(url), pool_pre_ping=True))

    async def close(self) -> None:
        await self._engine.dispose()

    async def find_request_proposal(
        self,
        tool: ToolDefinition,
        arguments: dict[str, object],
        context: ToolContext,
    ) -> ActionProposal | None:
        if not context.patient_id.strip():
            raise ActionDecisionError("写操作缺少权威患者作用域")
        async with self._sessions() as session:
            record = await session.scalar(
                select(ActionProposalRecord).where(
                    ActionProposalRecord.visit_matter_id == context.visit_matter_id,
                    ActionProposalRecord.participant_id == context.participant_id,
                    ActionProposalRecord.request_key == context.idempotency_key,
                )
            )
            if record is None:
                return None
            self._validate_request_match(record, tool, arguments, context)
            return self._to_proposal(record)

    async def create_proposal(
        self,
        tool: ToolDefinition,
        arguments: dict[str, object],
        context: ToolContext,
        *,
        confirmation: dict[str, object] | None = None,
        expires_at: datetime,
    ) -> ActionProposal:
        if not context.patient_id.strip():
            raise ActionDecisionError("写操作缺少权威患者作用域")
        proposal_id = f"proposal-{uuid4().hex}"
        created_at = datetime.now(UTC)
        async with self._sessions.begin() as session:
            inserted = (
                await session.execute(
                    insert(ActionProposalRecord)
                    .values(
                        id=proposal_id,
                        visit_matter_id=context.visit_matter_id,
                        participant_id=context.participant_id,
                        patient_id=context.patient_id,
                        patient_display_name=(
                            context.patient_display_name.strip() or "当前患者"
                        ),
                        request_key=context.idempotency_key,
                        idempotency_key=f"action-{proposal_id}",
                        tool_id=tool.tool_id,
                        tool_name=tool.name,
                        tool_version=tool.version,
                        arguments=dict(arguments),
                        confirmation=(
                            dict(confirmation) if confirmation is not None else None
                        ),
                        profile_version=context.profile_version,
                        visit_stage=context.visit_stage,
                        status="pending",
                        created_at=created_at,
                        expires_at=expires_at,
                    )
                    .on_conflict_do_nothing(
                        index_elements=("visit_matter_id", "participant_id", "request_key")
                    )
                    .returning(ActionProposalRecord.id)
                )
            ).scalar_one_or_none()
            record = await session.scalar(
                select(ActionProposalRecord).where(
                    ActionProposalRecord.visit_matter_id == context.visit_matter_id,
                    ActionProposalRecord.participant_id == context.participant_id,
                    ActionProposalRecord.request_key == context.idempotency_key,
                )
            )
            if record is None:
                raise RuntimeError("Action Proposal 写入后无法读取")
            self._validate_request_match(record, tool, arguments, context)
            if inserted is not None:
                self._add_audit(session, "propose", record, context.idempotency_key)
            return self._to_proposal(record)

    async def get_proposal(self, proposal_id: str) -> ActionProposal:
        async with self._sessions() as session:
            record = await session.get(ActionProposalRecord, proposal_id)
            if record is None:
                raise ActionDecisionError("待确认操作不存在")
            return self._to_proposal(record)

    async def validate_decision_scope(
        self, proposal_id: str, context: ToolContext
    ) -> ActionProposal:
        async with self._sessions() as session:
            record = await session.get(ActionProposalRecord, proposal_id)
            if record is None:
                raise ActionDecisionError("待确认操作不存在")
            self._validate_scope(record, context)
            return self._to_proposal(record)

    async def reject(
        self,
        proposal_id: str,
        context: ToolContext,
    ) -> ActionProposal:
        failure: str | None = None
        async with self._sessions.begin() as session:
            record = await self._locked(session, proposal_id)
            self._validate_scope(record, context)
            if record.status == "confirmed":
                failure = "该操作已经确认，不能拒绝"
            elif record.status == "expired" or datetime.now(UTC) >= record.expires_at:
                if record.status != "expired":
                    record.status = "expired"
                    self._add_audit(session, "expire", record, context.idempotency_key)
                failure = "待确认操作已过期"
            elif record.status == "pending":
                record.status = "rejected"
                self._add_audit(session, "reject", record, context.idempotency_key)
            else:
                self._add_audit(session, "reject", record, context.idempotency_key)
        if failure == "待确认操作已过期":
            raise ActionProposalExpiredError(self._to_proposal(record))
        if failure is not None:
            raise ActionDecisionError(failure)
        return self._to_proposal(record)

    async def confirm(
        self,
        proposal_id: str,
        context: ToolContext,
        tool: ToolDefinition,
    ) -> tuple[ActionProposal, ActionReceipt]:
        failure: str | None = None
        failure_cause: Exception | None = None
        receipt: ActionReceipt | None = None
        async with self._sessions.begin() as session:
            record = await self._locked(session, proposal_id)
            self._validate_scope(record, context)
            if record.status == "confirmed":
                receipt_record = await session.scalar(
                    select(ActionReceiptRecord).where(
                        ActionReceiptRecord.proposal_id == record.id
                    )
                )
                if receipt_record is None:
                    raise RuntimeError("已确认操作缺少 receipt")
                receipt = self._to_receipt(receipt_record)
                self._add_audit(
                    session,
                    "confirm",
                    record,
                    context.idempotency_key,
                    receipt_id=receipt_record.id,
                )
            elif record.status == "rejected":
                failure = "待确认操作已被拒绝"
            elif record.status == "expired" or datetime.now(UTC) >= record.expires_at:
                if record.status != "expired":
                    record.status = "expired"
                    self._add_audit(session, "expire", record, context.idempotency_key)
                failure = "待确认操作已过期"
            elif not self._matches_tool(record, tool, context) or (
                tool.revalidate is not None and not await tool.revalidate(context)
            ):
                failure = "操作参数、Tool 版本或作用域已变化，请重新发起"
            else:
                confirmation_contract = tool.confirmation_contract
                confirmation_valid = not (
                    confirmation_contract is not None
                    and (
                        record.confirmation is None
                        or validate_object(record.confirmation, confirmation_contract.schema)
                        is not None
                    )
                )
                if confirmation_valid and confirmation_contract is not None:
                    if record.confirmation is None:
                        confirmation_valid = False
                    else:
                        try:
                            confirmation_valid = await confirmation_contract.revalidate(
                                dict(record.arguments),
                                dict(record.confirmation),
                                context,
                            )
                        except Exception:
                            confirmation_valid = False
                if not confirmation_valid:
                    failure = "操作参数、Tool 版本或作用域已变化，请重新发起"

            if failure is None and receipt is None:
                execution_context = ToolContext(
                    visit_matter_id=context.visit_matter_id,
                    participant_id=context.participant_id,
                    idempotency_key=record.idempotency_key,
                    profile_version=context.profile_version,
                    visit_stage=context.visit_stage,
                    patient_id=context.patient_id,
                    patient_display_name=context.patient_display_name,
                )
                try:
                    result = await tool.execute(dict(record.arguments), execution_context)
                    if (
                        tool.output_schema is not None
                        and validate_object(result, tool.output_schema) is not None
                    ):
                        raise ValueError("Tool output did not match its declared Schema")
                except Exception as error:
                    self._add_audit(session, "commit_failed", record, context.idempotency_key)
                    failure = "操作暂时无法完成，请稍后重试"
                    failure_cause = error
                else:
                    receipt_record = ActionReceiptRecord(
                        id=f"receipt-{uuid4().hex}",
                        proposal_id=record.id,
                        idempotency_key=record.idempotency_key,
                        result=dict(result),
                        created_at=datetime.now(UTC),
                    )
                    session.add(receipt_record)
                    record.status = "confirmed"
                    record.receipt_id = receipt_record.id
                    self._add_audit(
                        session,
                        "confirm",
                        record,
                        context.idempotency_key,
                        receipt_id=receipt_record.id,
                    )
                    await session.flush()
                    receipt = self._to_receipt(receipt_record)
        if failure == "待确认操作已过期":
            raise ActionProposalExpiredError(self._to_proposal(record))
        if failure is not None:
            error = ActionDecisionError(failure)
            if failure_cause is not None:
                raise error from failure_cause
            raise error
        assert receipt is not None
        return self._to_proposal(record), receipt

    async def list_proposals(self) -> list[ActionProposal]:
        async with self._sessions() as session:
            records = (
                await session.scalars(
                    select(ActionProposalRecord).order_by(ActionProposalRecord.created_at)
                )
            ).all()
            return [self._to_proposal(record) for record in records]

    async def list_receipts(self) -> list[ActionReceipt]:
        async with self._sessions() as session:
            records = (
                await session.scalars(
                    select(ActionReceiptRecord).order_by(ActionReceiptRecord.created_at)
                )
            ).all()
            return [self._to_receipt(record) for record in records]

    async def list_audits(self) -> list[ActionAudit]:
        async with self._sessions() as session:
            records = (
                await session.scalars(
                    select(ActionAuditRecord).order_by(ActionAuditRecord.created_at)
                )
            ).all()
            return [
                ActionAudit(
                    action=cast(ActionAuditKind, record.action),
                    proposal_id=record.proposal_id,
                    participant_id=record.participant_id,
                    visit_matter_id=record.visit_matter_id,
                    decision_key=record.decision_key,
                    receipt_id=record.receipt_id,
                    created_at=record.created_at,
                )
                for record in records
            ]

    async def record_decision_rejection(
        self,
        proposal_id: str,
        context: ToolContext,
    ) -> None:
        async with self._sessions.begin() as session:
            session.add(
                ActionAuditRecord(
                    id=f"action-audit-{uuid4().hex}",
                    action="reject_decision",
                    proposal_id=proposal_id,
                    participant_id=context.participant_id,
                    visit_matter_id=context.visit_matter_id,
                    decision_key=context.idempotency_key,
                    created_at=datetime.now(UTC),
                )
            )

    @staticmethod
    async def _locked(session, proposal_id: str) -> ActionProposalRecord:
        record = await session.scalar(
            select(ActionProposalRecord)
            .where(ActionProposalRecord.id == proposal_id)
            .with_for_update()
        )
        if record is None:
            raise ActionDecisionError("待确认操作不存在")
        return record

    @staticmethod
    def _validate_scope(record: ActionProposalRecord, context: ToolContext) -> None:
        if (
            record.visit_matter_id != context.visit_matter_id
            or record.participant_id != context.participant_id
            or record.patient_id != context.patient_id
            or record.profile_version != context.profile_version
            or record.visit_stage != context.visit_stage
        ):
            if (
                record.visit_matter_id != context.visit_matter_id
                or record.participant_id != context.participant_id
            ):
                raise ActionDecisionError("该操作不属于当前就诊事项或参与者")
            raise ActionDecisionError("操作参数、Tool 版本或作用域已变化，请重新发起")

    @staticmethod
    def _validate_request_match(
        record: ActionProposalRecord,
        tool: ToolDefinition,
        arguments: dict[str, object],
        context: ToolContext,
    ) -> None:
        if (
            record.tool_id != tool.tool_id
            or record.tool_version != tool.version
            or record.arguments != arguments
            or record.patient_id != context.patient_id
            or record.profile_version != context.profile_version
            or record.visit_stage != context.visit_stage
        ):
            raise ActionDecisionError("同一请求不能改变操作参数、Tool 版本或作用域")

    @staticmethod
    def _matches_tool(
        record: ActionProposalRecord,
        tool: ToolDefinition,
        context: ToolContext,
    ) -> bool:
        return (
            tool.tool_id == record.tool_id
            and tool.version == record.tool_version
            and tool.name == record.tool_name
            and tool.effect == "write"
            and tool.approval_required
            and tool.enabled
            and tool.bound
            and tool.authorize(context)
        )

    @staticmethod
    def _add_audit(
        session,
        action: ActionAuditKind,
        proposal: ActionProposalRecord,
        decision_key: str,
        *,
        receipt_id: str | None = None,
    ) -> None:
        session.add(
            ActionAuditRecord(
                id=f"action-audit-{uuid4().hex}",
                action=action,
                proposal_id=proposal.id,
                participant_id=proposal.participant_id,
                visit_matter_id=proposal.visit_matter_id,
                decision_key=decision_key,
                receipt_id=receipt_id,
                created_at=datetime.now(UTC),
            )
        )

    @staticmethod
    def _to_proposal(record: ActionProposalRecord) -> ActionProposal:
        return ActionProposal(
            proposal_id=record.id,
            visit_matter_id=record.visit_matter_id,
            participant_id=record.participant_id,
            patient_id=record.patient_id,
            patient_display_name=record.patient_display_name,
            request_key=record.request_key,
            idempotency_key=record.idempotency_key,
            tool_id=record.tool_id,
            tool_name=record.tool_name,
            tool_version=record.tool_version,
            arguments=dict(record.arguments),
            confirmation=(
                dict(record.confirmation) if record.confirmation is not None else None
            ),
            profile_version=record.profile_version,
            visit_stage=record.visit_stage,
            status=cast(ActionProposalStatus, record.status),
            receipt_id=record.receipt_id,
            created_at=record.created_at,
            expires_at=record.expires_at,
        )

    @staticmethod
    def _to_receipt(record: ActionReceiptRecord) -> ActionReceipt:
        return ActionReceipt(
            receipt_id=record.id,
            proposal_id=record.proposal_id,
            idempotency_key=record.idempotency_key,
            result=dict(record.result),
            created_at=record.created_at,
        )
