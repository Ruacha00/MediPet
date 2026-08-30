from __future__ import annotations

import json
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
            stream_usage=True,
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
                tool_calls = _model_tool_calls(aggregate)
                usage = aggregate.usage_metadata or {}
                input_tokens = int(usage.get("input_tokens", 0))
                output_tokens = int(usage.get("output_tokens", 0))
                if tool_calls or input_tokens or output_tokens:
                    yield ModelChunk(
                        tool_calls=tool_calls,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                    )
        except Exception as error:
            raise ModelUnavailableError(
                "model request failed",
                retryable=_is_transient_upstream_error(error),
            ) from None


def _is_transient_upstream_error(error: Exception) -> bool:
    current: BaseException | None = error
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        response = getattr(current, "response", None)
        status_code = getattr(current, "status_code", None) or getattr(
            response, "status_code", None
        )
        if isinstance(status_code, int):
            return status_code in {408, 409, 429} or status_code >= 500
        if isinstance(current, (TimeoutError, ConnectionError)):
            return True
        error_type = type(current).__name__.lower()
        if "timeout" in error_type or "connection" in error_type:
            return True
        current = current.__cause__ or current.__context__
    return False


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


def _model_tool_calls(message: AIMessageChunk) -> tuple[ModelToolCall, ...]:
    if message.tool_call_chunks:
        calls: list[ModelToolCall] = []
        for call in message.tool_call_chunks:
            raw_arguments = call.get("args") or ""
            try:
                parsed_arguments = json.loads(raw_arguments) if raw_arguments else {}
            except (json.JSONDecodeError, TypeError):
                parsed_arguments = None
            arguments: dict[str, object]
            if isinstance(parsed_arguments, dict) and all(
                isinstance(key, str) for key in parsed_arguments
            ):
                arguments = dict(parsed_arguments)
            else:
                arguments = {"_invalid_arguments": raw_arguments}
            calls.append(
                ModelToolCall(
                    id=str(call.get("id") or ""),
                    name=str(call.get("name") or ""),
                    arguments=arguments,
                )
            )
        return tuple(calls)
    return tuple(
        ModelToolCall(
            id=str(call.get("id") or ""),
            name=str(call["name"]),
            arguments=(
                call["args"]
                if isinstance(call.get("args"), dict)
                else {"_invalid_arguments": call.get("args")}
            ),
        )
        for call in message.tool_calls
    )
