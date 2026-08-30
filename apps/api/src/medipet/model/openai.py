from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, cast

from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from medipet.config import ModelSettings
from medipet.model.port import (
    ModelChunk,
    ModelMessage,
    ModelRequest,
    ModelToolCall,
    ModelUnavailableError,
)


class ChatOpenAIModelAdapter:
    def __init__(
        self,
        settings: ModelSettings,
        *,
        http_async_client: Any | None = None,
    ) -> None:
        self._model = ChatOpenAI(
            model=settings.model,
            api_key=SecretStr(settings.api_key),
            base_url=settings.base_url,
            temperature=settings.temperature,
            timeout=settings.timeout_seconds,
            max_retries=0,
            stream_usage=False,
            use_responses_api=False,
            http_async_client=http_async_client,
        )

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        messages = [_to_provider_message(message) for message in request.messages]
        runnable = self._model
        if request.tools:
            runnable = self._model.bind_tools(
                [
                    {
                        "type": "function",
                        "function": {
                            "name": tool.name,
                            "description": tool.description,
                            "parameters": dict(tool.input_schema),
                        },
                    }
                    for tool in request.tools
                ]
            )
        aggregate: AIMessageChunk | None = None
        try:
            async for chunk in runnable.astream(messages):
                aggregate = cast(
                    AIMessageChunk,
                    chunk if aggregate is None else aggregate + chunk,
                )
                text = _text_content(chunk.content)
                if text:
                    yield ModelChunk(text=text)
            if aggregate is not None:
                tool_calls = tuple(
                    ModelToolCall(
                        id=str(call.get("id") or ""),
                        name=str(call["name"]),
                        arguments=(
                            call["args"]
                            if isinstance(call.get("args"), dict)
                            else {"_invalid_arguments": call.get("args")}
                        ),
                    )
                    for call in aggregate.tool_calls
                )
                if tool_calls:
                    yield ModelChunk(tool_calls=tool_calls)
        except Exception:
            raise ModelUnavailableError("model request failed") from None


def _to_provider_message(message: ModelMessage) -> BaseMessage:
    if message.role == "system":
        return SystemMessage(content=message.content)
    if message.role == "user":
        return HumanMessage(content=message.content)
    if message.role == "tool":
        return ToolMessage(content=message.content, tool_call_id=message.tool_call_id or "")
    return AIMessage(
        content=message.content,
        tool_calls=[
            {"id": call.id, "name": call.name, "args": call.arguments}
            for call in message.tool_calls
        ],
    )


def _text_content(content: str | list[str | dict[str, Any]]) -> str:
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif block.get("type") in {"text", "output_text"} and isinstance(block.get("text"), str):
            parts.append(block["text"])
    return "".join(parts)
