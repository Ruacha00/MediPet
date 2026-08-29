from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

from medipet.agent.runtime import AgentRequest, AgentRuntime
from medipet.contracts import TurnCommand, TurnEvent


class MediPetAssistant:
    def __init__(self, agent_runtime: AgentRuntime) -> None:
        self._agent_runtime = agent_runtime
        self._proposal_receipts: dict[str, str] = {}

    async def handle_turn(self, command: TurnCommand) -> AsyncIterator[TurnEvent]:
        trace_id = f"trace-{uuid4().hex[:12]}"

        if command.confirmation is not None:
            async for event in self._handle_confirmation(command, trace_id):
                yield event
            return

        if command.message is None or not command.message.strip():
            yield TurnEvent(kind="failed", data={"message": "缺少就诊参与者消息"})
            return

        async for event in self._agent_runtime.run(AgentRequest(message=command.message)):
            yield TurnEvent(kind=event.kind, data=event.data)

        yield TurnEvent(kind="completed", data={"traceId": trace_id})

    async def _handle_confirmation(
        self,
        command: TurnCommand,
        trace_id: str,
    ) -> AsyncIterator[TurnEvent]:
        confirmation = command.confirmation
        assert confirmation is not None

        if confirmation.proposal_id != "proposal-demo-001":
            yield TurnEvent(kind="failed", data={"message": "预约方案不存在或已失效"})
            return

        if confirmation.decision == "reject":
            yield TurnEvent(kind="text", data={"text": "已取消这次预约方案，没有创建挂号。"})
            yield TurnEvent(
                kind="data",
                data={
                    "type": "data-action-proposal",
                    "id": confirmation.proposal_id,
                    "data": {"proposalId": confirmation.proposal_id, "status": "rejected"},
                },
            )
            yield TurnEvent(kind="completed", data={"traceId": trace_id})
            return

        receipt_id = self._proposal_receipts.setdefault(
            confirmation.proposal_id,
            "receipt-demo-001",
        )
        yield TurnEvent(kind="text", data={"text": "预约已确认，演示挂号创建成功。"})
        yield TurnEvent(
            kind="data",
            data={
                "type": "data-action-proposal",
                "id": confirmation.proposal_id,
                "data": {
                    "proposalId": confirmation.proposal_id,
                    "status": "confirmed",
                    "receiptId": receipt_id,
                },
            },
        )
        yield TurnEvent(kind="completed", data={"traceId": trace_id})
