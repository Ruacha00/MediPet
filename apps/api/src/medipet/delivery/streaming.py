from __future__ import annotations

import json
from collections.abc import AsyncIterator
from uuid import uuid4

from medipet.contracts import TurnEvent


def _sse(payload: dict | str) -> str:
    if isinstance(payload, str):
        return f"data: {payload}\n\n"
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"data: {body}\n\n"


async def to_ui_message_stream(events: AsyncIterator[TurnEvent]) -> AsyncIterator[str]:
    message_id = f"message-{uuid4().hex}"
    text_id = f"text-{uuid4().hex}"
    text_open = False

    yield _sse({"type": "start", "messageId": message_id})

    async for event in events:
        if event.kind == "status":
            yield _sse(
                {
                    "type": "data-agent-status",
                    "data": event.data,
                    "transient": True,
                }
            )
            continue

        if event.kind == "text":
            if not text_open:
                yield _sse({"type": "text-start", "id": text_id})
                text_open = True
            yield _sse({"type": "text-delta", "id": text_id, "delta": event.data["text"]})
            continue

        if event.kind == "data":
            if text_open:
                yield _sse({"type": "text-end", "id": text_id})
                text_open = False
            yield _sse(event.data)
            continue

        if event.kind == "failed":
            if text_open:
                yield _sse({"type": "text-end", "id": text_id})
                text_open = False
            yield _sse({"type": "error", "errorText": event.data["message"]})
            continue

        if event.kind == "completed":
            if text_open:
                yield _sse({"type": "text-end", "id": text_id})
                text_open = False
            yield _sse({"type": "finish", "finishReason": "stop"})

    if text_open:
        yield _sse({"type": "text-end", "id": text_id})
    yield _sse("[DONE]")
