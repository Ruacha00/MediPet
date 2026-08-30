from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest

from medipet.contracts import TurnEvent
from medipet.delivery.streaming import to_ui_message_stream


@pytest.mark.asyncio
async def test_closing_browser_stream_closes_the_upstream_turn() -> None:
    closed = False

    async def turn_events() -> AsyncGenerator[TurnEvent, None]:
        nonlocal closed
        try:
            yield TurnEvent(kind="status", data={"label": "working"})
            yield TurnEvent(kind="text", data={"text": "partial"})
            yield TurnEvent(kind="completed")
        finally:
            closed = True

    stream = to_ui_message_stream(turn_events())
    _ = await anext(stream)
    _ = await anext(stream)
    await stream.aclose()

    assert closed is True
