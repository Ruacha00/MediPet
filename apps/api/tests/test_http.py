import asyncio
import json
from collections.abc import AsyncIterator

from fastapi.testclient import TestClient

from medipet.delivery.http import create_app
from medipet.model.port import ModelChunk, ModelPort, ModelRequest, ModelUnavailableError
from medipet.persistence.conversation import DevelopmentVisit, InMemoryVisitConversationStore


class DeterministicModel(ModelPort):
    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        del request
        yield ModelChunk(text="第一段")
        yield ModelChunk(text="，第二段。")


class UnavailableModel(ModelPort):
    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        del request
        raise ModelUnavailableError("provider secret diagnostic body; Bearer test-secret")
        yield  # pragma: no cover


def seeded_store() -> InMemoryVisitConversationStore:
    store = InMemoryVisitConversationStore()
    asyncio.run(
        store.seed_development_visit(
            DevelopmentVisit(
                patient_id="patient-demo",
                patient_display_name="演示患者",
                participant_id="participant-demo",
                participant_display_name="患者本人",
                visit_matter_id="visit-matter-demo",
                visit_matter_title="初次咨询",
            )
        )
    )
    return store


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
    client = TestClient(create_app(model=DeterministicModel(), conversation_store=seeded_store()))
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


def test_chat_streams_model_failure_safely() -> None:
    client = TestClient(create_app(model=UnavailableModel(), conversation_store=seeded_store()))
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

    assert response.status_code == 200
    lines = [line.removeprefix("data: ") for line in response.text.splitlines() if line]
    payloads = [json.loads(line) for line in lines[:-1]]
    assert [payload["type"] for payload in payloads] == [
        "start",
        "data-agent-status",
        "error",
    ]
    assert payloads[-1]["errorText"] == "模型服务暂时不可用，请稍后重试。"
    assert lines[-1] == "[DONE]"
    assert "provider secret" not in response.text
    assert "test-secret" not in response.text
    assert "Traceback" not in response.text


def test_history_survives_app_recreation_and_preserves_terminal_states() -> None:
    store = seeded_store()
    first_client = TestClient(create_app(model=DeterministicModel(), conversation_store=store))
    first_client.post(
        "/v1/chat/turns",
        json={
            "idempotency_key": "completed-turn",
            "messages": [
                {
                    "id": "message-1",
                    "role": "user",
                    "parts": [{"type": "text", "text": "请帮我整理症状"}],
                }
            ],
        },
    )
    asyncio.run(_add_cancelled_message(store))

    restarted_client = TestClient(create_app(model=DeterministicModel(), conversation_store=store))
    response = restarted_client.get(
        "/v1/visit-matters/visit-matter-demo/messages",
        params={"participant_id": "participant-demo"},
    )

    assert response.status_code == 200
    messages = response.json()["messages"]
    assert [(message["role"], message["state"]) for message in messages] == [
        ("user", "completed"),
        ("assistant", "completed"),
        ("assistant", "cancelled"),
    ]
    assert messages[-1]["parts"] == [{"type": "text", "text": "半截回答"}]


async def _add_cancelled_message(store: InMemoryVisitConversationStore) -> None:
    assistant = await store.add_assistant_message(
        visit_matter_id="visit-matter-demo",
        participant_id="participant-demo",
        turn_id="cancelled-turn",
    )
    await store.mark_assistant_streaming(assistant.id)
    await store.append_assistant_text(assistant.id, "半截回答")
    await store.finish_assistant_message(assistant.id, "cancelled")
