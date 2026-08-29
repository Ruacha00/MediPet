from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

from medipet.agent.runtime import AgentRequest, AgentRuntime
from medipet.contracts import TurnCommand, TurnEvent
from medipet.model.port import ModelUnavailableError


class MediPetAssistant:
    def __init__(self, agent_runtime: AgentRuntime) -> None:
        self._agent_runtime = agent_runtime

    async def handle_turn(self, command: TurnCommand) -> AsyncIterator[TurnEvent]:
        trace_id = f"trace-{uuid4().hex[:12]}"

        if command.confirmation is not None:
            yield TurnEvent(kind="failed", data={"message": "当前没有待确认的操作"})
            return

        if command.message is None or not command.message.strip():
            yield TurnEvent(kind="failed", data={"message": "缺少就诊参与者消息"})
            return

        try:
            async for event in self._agent_runtime.run(AgentRequest(message=command.message)):
                yield TurnEvent(kind=event.kind, data=event.data)
        except ModelUnavailableError:
            yield TurnEvent(kind="failed", data={"message": "模型服务暂时不可用，请稍后重试。"})
            return

        yield TurnEvent(kind="completed", data={"traceId": trace_id})
