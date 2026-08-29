import json
from collections.abc import AsyncIterator

from fastapi.testclient import TestClient

from medipet.delivery.http import create_app
from medipet.model.port import ModelChunk, ModelPort, ModelRequest


class DeterministicModel(ModelPort):
    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        del request
        yield ModelChunk(text="第一段")
        yield ModelChunk(text="，第二段。")


def test_liveness_remains_available_without_model_configuration() -> None:
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_reports_missing_model_configuration_safely() -> None:
    client = TestClient(create_app())

    response = client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {"detail": "模型服务配置不可用"}


def test_chat_uses_ai_sdk_ui_stream_protocol() -> None:
    client = TestClient(create_app(model=DeterministicModel()))
    response = client.post(
        "/v1/chat/turns",
        json={
            "id": "conversation-1",
            "messages": [
                {
                    "id": "message-1",
                    "role": "user",
                    "parts": [{"type": "text", "text": "我头痛，应该做什么准备？"}],
                }
            ],
        },
    )

    assert response.status_code == 200
    assert response.headers["x-vercel-ai-ui-message-stream"] == "v1"
    lines = [line.removeprefix("data: ") for line in response.text.splitlines() if line]
    payloads = [json.loads(line) for line in lines[:-1]]
    assert payloads[0]["type"] == "start"
    assert payloads[2]["type"] == "text-start"
    assert payloads[3]["delta"] == "第一段"
    assert payloads[4]["delta"] == "，第二段。"
    assert payloads[-1]["type"] == "finish"
    assert lines[-1] == "[DONE]"
    assert "Thought" not in response.text
    assert "data-department-candidates" not in response.text


def test_chat_reports_unconfigured_model_without_leaking_configuration() -> None:
    client = TestClient(create_app())
    response = client.post(
        "/v1/chat/turns",
        json={
            "messages": [
                {
                    "id": "message-1",
                    "role": "user",
                    "parts": [{"type": "text", "text": "你好"}],
                }
            ]
        },
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "模型服务配置不可用"}
