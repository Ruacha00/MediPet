import asyncio
import json

import httpx
import pytest

from medipet.config import ModelConfigurationError, ModelSettings
from medipet.model.openai import ChatOpenAIModelAdapter, _is_transient_upstream_error
from medipet.model.port import (
    ModelMessage,
    ModelRequest,
    ModelTool,
    ModelToolCall,
    ModelUnavailableError,
)


def test_model_settings_require_a_complete_api_root() -> None:
    with pytest.raises(ModelConfigurationError):
        ModelSettings.from_environment({})

    with pytest.raises(ModelConfigurationError):
        ModelSettings(
            base_url="https://provider.example",
            api_key="secret",
            model="test-model",
        )

    with pytest.raises(ModelConfigurationError):
        ModelSettings(
            base_url="https://provider.example/openai",
            api_key="secret",
            model="test-model",
        )


@pytest.mark.parametrize("timeout_seconds", [float("nan"), float("inf"), float("-inf")])
def test_model_settings_reject_non_finite_timeout(timeout_seconds: float) -> None:
    with pytest.raises(ModelConfigurationError):
        ModelSettings(
            base_url="https://provider.example/v1",
            api_key="secret",
            model="test-model",
            timeout_seconds=timeout_seconds,
        )


def _chunk(text: str) -> dict:
    return {
        "id": "chunk",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "delta": {"content": text, "reasoning_content": "Thought: private"},
            }
        ],
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
    assert "Thought" not in "".join(chunks)


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


@pytest.mark.asyncio
async def test_chat_openai_adapter_marks_client_errors_as_non_retryable() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(400, text="invalid request detail")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = ChatOpenAIModelAdapter(
            ModelSettings(
                base_url="https://provider.example/openai/v1",
                api_key="test-secret",
                model="test-model",
            ),
            http_async_client=client,
        )

        with pytest.raises(ModelUnavailableError) as caught:
            _ = [
                chunk
                async for chunk in adapter.stream(
                    ModelRequest(messages=(ModelMessage(role="user", content="hello"),))
                )
            ]

    assert caught.value.retryable is False


def test_unknown_local_failures_are_not_classified_as_transient() -> None:
    assert _is_transient_upstream_error(ValueError("local failure")) is False


@pytest.mark.asyncio
async def test_chat_openai_adapter_releases_the_http_request_when_cancelled() -> None:
    started = asyncio.Event()
    released = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            released.set()
        raise AssertionError("unreachable")  # pragma: no cover

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = ChatOpenAIModelAdapter(
            ModelSettings(
                base_url="https://provider.example/openai/v1",
                api_key="test-secret",
                model="test-model",
            ),
            http_async_client=client,
        )

        async def consume() -> None:
            _ = [
                chunk
                async for chunk in adapter.stream(
                    ModelRequest(messages=(ModelMessage(role="user", content="hello"),))
                )
            ]

        task = asyncio.create_task(consume())
        await asyncio.wait_for(started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    await asyncio.wait_for(released.wait(), timeout=1)


@pytest.mark.asyncio
async def test_chat_openai_adapter_streams_tool_calls_and_observations() -> None:
    requests: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 1:
            assert payload["tools"] == [
                {
                    "type": "function",
                    "function": {
                        "name": "dummy_read",
                        "description": "test-only read",
                        "parameters": {
                            "type": "object",
                            "properties": {"query": {"type": "string"}},
                            "required": ["query"],
                            "additionalProperties": False,
                        },
                    },
                }
            ]
            chunks = [
                {
                    "id": "chunk",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "test-model",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call-1",
                                        "type": "function",
                                        "function": {
                                            "name": "dummy_read",
                                            "arguments": '{"query":',
                                        },
                                    }
                                ]
                            },
                        }
                    ],
                },
                {
                    "id": "chunk",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "test-model",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "function": {"arguments": '"示例"}'},
                                    }
                                ]
                            },
                        }
                    ],
                },
            ]
            events = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks)
            return httpx.Response(200, text=events + "data: [DONE]\n\n")

        assert payload["messages"][-2]["tool_calls"][0]["function"]["name"] == "dummy_read"
        assert payload["messages"][-1] == {
            "role": "tool",
            "content": '{"value":"观测结果"}',
            "tool_call_id": "call-1",
        }
        event = f"data: {json.dumps(_chunk('最终回答'))}\n\ndata: [DONE]\n\n"
        return httpx.Response(200, text=event)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = ChatOpenAIModelAdapter(
            ModelSettings(
                base_url="https://provider.example/openai/v1",
                api_key="test-secret",
                model="test-model",
            ),
            http_async_client=client,
        )
        tool = ModelTool(
            name="dummy_read",
            description="test-only read",
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
        )
        first = [
            chunk
            async for chunk in adapter.stream(
                ModelRequest(
                    messages=(ModelMessage(role="user", content="query"),),
                    tools=(tool,),
                )
            )
        ]
        assert len(first) == 1
        assert first[0].tool_calls == (
            ModelToolCall(id="call-1", name="dummy_read", arguments={"query": "示例"}),
        )

        second = [
            chunk
            async for chunk in adapter.stream(
                ModelRequest(
                    messages=(
                        ModelMessage(role="user", content="query"),
                        ModelMessage(
                            role="assistant",
                            content="",
                            tool_calls=first[0].tool_calls,
                        ),
                        ModelMessage(
                            role="tool",
                            content='{"value":"观测结果"}',
                            tool_call_id="call-1",
                        ),
                    ),
                    tools=(tool,),
                )
            )
        ]

    assert [chunk.text for chunk in second] == ["最终回答"]


@pytest.mark.asyncio
async def test_chat_openai_adapter_preserves_malformed_tool_calls_for_runtime_rejection() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        chunk = {
            "id": "chunk",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "test-model",
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call-bad",
                                "type": "function",
                                "function": {
                                    "name": "dummy_read",
                                    "arguments": '{"query":',
                                },
                            }
                        ]
                    },
                }
            ],
        }
        event = f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n"
        return httpx.Response(200, text=event)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = ChatOpenAIModelAdapter(
            ModelSettings(
                base_url="https://provider.example/openai/v1",
                api_key="test-secret",
                model="test-model",
            ),
            http_async_client=client,
        )
        chunks = [
            chunk
            async for chunk in adapter.stream(
                ModelRequest(
                    messages=(ModelMessage(role="user", content="query"),),
                    tools=(
                        ModelTool(
                            name="dummy_read",
                            description="test-only read",
                            input_schema={"type": "object"},
                        ),
                    ),
                )
            )
        ]

    assert chunks[0].tool_calls == (
        ModelToolCall(
            id="call-bad",
            name="dummy_read",
            arguments={"_invalid_arguments": '{"query":'},
        ),
    )
