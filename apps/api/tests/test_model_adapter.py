import json

import httpx
import pytest

from medipet.config import ModelConfigurationError, ModelSettings
from medipet.model.openai import ChatOpenAIModelAdapter
from medipet.model.port import ModelMessage, ModelRequest, ModelUnavailableError


def test_model_settings_require_a_complete_api_root() -> None:
    with pytest.raises(ModelConfigurationError):
        ModelSettings.from_environment({})

    with pytest.raises(ModelConfigurationError):
        ModelSettings(
            base_url="https://provider.example",
            api_key="secret",
            model="test-model",
        )


def _chunk(text: str) -> dict:
    return {
        "id": "chunk",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "test-model",
        "choices": [{"index": 0, "delta": {"content": text}}],
    }


@pytest.mark.asyncio
async def test_chat_openai_adapter_streams_chat_completion_chunks() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://provider.example/openai/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-secret"
        payload = json.loads(request.content)
        assert payload["model"] == "test-model"
        assert payload["stream"] is True
        events = "".join(f"data: {json.dumps(_chunk(text))}\n\n" for text in ["第一", "段"])
        return httpx.Response(200, text=events + "data: [DONE]\n\n")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = ChatOpenAIModelAdapter(
            ModelSettings(
                base_url="https://provider.example/openai/v1/",
                api_key="test-secret",
                model="test-model",
            ),
            http_async_client=client,
        )
        request = ModelRequest(
            messages=(
                ModelMessage(role="system", content="system boundary"),
                ModelMessage(role="user", content="participant message"),
            )
        )
        chunks = [chunk.text async for chunk in adapter.stream(request)]

    assert chunks == ["第一", "段"]


@pytest.mark.asyncio
async def test_chat_openai_adapter_hides_upstream_failure_details() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(500, text="provider secret diagnostic body")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = ChatOpenAIModelAdapter(
            ModelSettings(
                base_url="https://provider.example/openai/v1",
                api_key="test-secret",
                model="test-model",
            ),
            http_async_client=client,
        )
        request = ModelRequest(messages=(ModelMessage(role="user", content="hello"),))

        with pytest.raises(ModelUnavailableError) as caught:
            _ = [chunk async for chunk in adapter.stream(request)]

    assert "provider secret" not in str(caught.value)
