from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from medipet.config import ModelSettings
from medipet.model.port import ModelChunk, ModelMessage, ModelRequest, ModelUnavailableError


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
        try:
            async for chunk in self._model.astream(messages):
                text = _text_content(chunk.content)
                if text:
                    yield ModelChunk(text=text)
        except Exception:
            raise ModelUnavailableError("model request failed") from None


def _to_provider_message(message: ModelMessage) -> BaseMessage:
    message_types = {
        "system": SystemMessage,
        "user": HumanMessage,
        "assistant": AIMessage,
    }
    return message_types[message.role](content=message.content)


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
