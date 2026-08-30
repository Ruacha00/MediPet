from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

from medipet.agent.runtime import AgentRequest, AgentRuntime
from medipet.contracts import TurnCommand, TurnEvent


class MediPetAssistant:
    def __init__(self, agent_runtime: AgentRuntime) -> None:
        self._agent_runtime = agent_runtime

    async def handle_turn(self, command: TurnCommand) -> AsyncIterator[TurnEvent]:
        trace_id = f"trace-{uuid4().hex[:12]}"

        if command.confirmation is not None:
            yield TurnEvent(
                kind="failed",
                data={"message": "当前没有待确认的操作", "traceId": trace_id},
            )
            return

        if command.message is None or not command.message.strip():
            yield TurnEvent(
                kind="failed",
                data={"message": "缺少就诊参与者消息", "traceId": trace_id},
            )
            return

        async for event in self._agent_runtime.run(AgentRequest(message=command.message)):
            if event.kind == "failed":
                yield TurnEvent(kind="failed", data={**event.data, "traceId": trace_id})
                return
            yield TurnEvent(kind=event.kind, data=event.data)

        yield TurnEvent(kind="completed", data={"traceId": trace_id})
