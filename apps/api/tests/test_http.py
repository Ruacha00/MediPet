import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi.testclient import TestClient

from medipet.agent.capabilities import (
    CapabilitySnapshot,
    StaticCapabilityProvider,
    ToolContext,
    ToolDefinition,
)
from medipet.config import DevelopmentRuntimeConfig, ModelSettings
from medipet.delivery.http import create_app
from medipet.model.port import (
    ModelChunk,
    ModelPort,
    ModelRequest,
    ModelToolCall,
    ModelUnavailableError,
)
from medipet.persistence.conversation import (
    DevelopmentVisitMatter,
    InMemoryVisitConversationStore,
    VisitTurn,
)


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


class ToolCallingModel(ModelPort):
    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        del request
        self.calls += 1
        if self.calls == 1:
            yield ModelChunk(
                tool_calls=(ModelToolCall("call-1", "private_dummy_read", {}),)
            )
            return
        yield ModelChunk(text="安全结果")


class ReloadingModel(ModelPort):
    def __init__(self, settings: ModelSettings, env_file: Path) -> None:
        self._settings = settings
        self._env_file = env_file

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        del request
        yield ModelChunk(text=self._settings.model)
        if self._settings.model == "model-one":
            self._env_file.write_text(
                _runtime_env(model="model-two", temperature="0.8"),
                encoding="utf-8",
            )
        yield ModelChunk(text=f"/{self._settings.model}")


def _runtime_env(*, model: str = "model-one", temperature: str = "0.2") -> str:
    return "\n".join(
        [
            "MEDIPET_LLM_BASE_URL=https://provider.example/v1",
            "MEDIPET_LLM_API_KEY=file-secret",
            f"MEDIPET_LLM_MODEL={model}",
            f"MEDIPET_LLM_TEMPERATURE={temperature}",
            "MEDIPET_LLM_TIMEOUT_SECONDS=30",
            "MEDIPET_TURN_TIMEOUT_SECONDS=60",
            "MEDIPET_AGENT_MAX_STEPS=8",
            "MEDIPET_CONTEXT_MESSAGE_LIMIT=20",
        ]
    )


def _streamed_text(response_text: str) -> str:
    payloads = [
        json.loads(line.removeprefix("data: "))
        for line in response_text.splitlines()
        if line.startswith("data: {")
    ]
    return "".join(payload.get("delta", "") for payload in payloads)


def seeded_store() -> InMemoryVisitConversationStore:
    store = InMemoryVisitConversationStore()
    asyncio.run(
        store.seed_development_visit_matter(
            DevelopmentVisitMatter(
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


def test_invalid_hot_reload_makes_readiness_and_new_turns_unavailable(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(_runtime_env(), encoding="utf-8")
    runtime_config = DevelopmentRuntimeConfig(env_file, process_environment={})
    client = TestClient(
        create_app(
            conversation_store=seeded_store(),
            runtime_config=runtime_config,
            model_factory=lambda settings: ReloadingModel(settings, env_file),
        )
    )
    assert client.get("/ready").status_code == 200

    env_file.write_text(_runtime_env(temperature="invalid"), encoding="utf-8")

    readiness = client.get("/ready")
    turn = client.post(
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
    assert readiness.status_code == 503
    assert readiness.json() == {"detail": "模型服务配置不可用"}
    assert turn.status_code == 503
    assert "file-secret" not in readiness.text + turn.text
    assert "invalid" not in readiness.text + turn.text


def test_file_change_during_stream_is_pinned_until_the_next_turn(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(_runtime_env(), encoding="utf-8")
    runtime_config = DevelopmentRuntimeConfig(
        env_file,
        process_environment={"MEDIPET_LLM_API_KEY": "process-secret"},
    )
    configured_models: list[tuple[str, str, float]] = []

    def model_factory(settings: ModelSettings) -> ModelPort:
        configured_models.append((settings.model, settings.api_key, settings.temperature))
        return ReloadingModel(settings, env_file)

    client = TestClient(
        create_app(
            conversation_store=seeded_store(),
            runtime_config=runtime_config,
            model_factory=model_factory,
        )
    )
    first = client.post(
        "/v1/chat/turns",
        json={
            "idempotency_key": "turn-one",
            "messages": [
                {
                    "id": "message-1",
                    "role": "user",
                    "parts": [{"type": "text", "text": "第一轮"}],
                }
            ],
        },
    )
    second = client.post(
        "/v1/chat/turns",
        json={
            "idempotency_key": "turn-two",
            "messages": [
                {
                    "id": "message-2",
                    "role": "user",
                    "parts": [{"type": "text", "text": "第二轮"}],
                }
            ],
        },
    )

    assert _streamed_text(first.text) == "model-one/model-one"
    assert _streamed_text(second.text) == "model-two/model-two"
    assert configured_models == [
        ("model-one", "process-secret", 0.2),
        ("model-two", "process-secret", 0.8),
    ]


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


def test_chat_exposes_only_safe_tool_progress_and_final_text() -> None:
    async def execute(arguments: dict[str, object], context: ToolContext) -> dict[str, object]:
        del arguments, context
        return {"private_schema_result": "not-for-browser"}

    tool = ToolDefinition(
        name="private_dummy_read",
        version="secret-version",
        description="private tool description",
        input_schema={"type": "object", "additionalProperties": False},
        effect="read",
        execute=execute,
    )
    client = TestClient(
        create_app(
            model=ToolCallingModel(),
            conversation_store=seeded_store(),
            capability_provider=StaticCapabilityProvider(
                CapabilitySnapshot(skill_versions=("private-skill@1",), tools=(tool,))
            ),
        )
    )

    response = client.post(
        "/v1/chat/turns",
        json={
            "messages": [
                {
                    "id": "message-1",
                    "role": "user",
                    "parts": [{"type": "text", "text": "测试工具调用"}],
                }
            ]
        },
    )

    assert response.status_code == 200
    assert "正在查询可用信息" in response.text
    assert "安全结果" in response.text
    assert "private_dummy_read" not in response.text
    assert "private_schema_result" not in response.text
    assert "secret-version" not in response.text
    assert "Thought" not in response.text


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
        ("assistant", "failed"),
        ("assistant", "cancelled"),
    ]
    assert messages[-1]["parts"] == [{"type": "text", "text": "半截回答"}]


async def _add_cancelled_message(store: InMemoryVisitConversationStore) -> None:
    failed = await store.add_assistant_message(
        turn=VisitTurn(
            visit_matter_id="visit-matter-demo",
            participant_id="participant-demo",
            turn_id="failed-turn",
        )
    )
    await store.finish_assistant_message(failed.id, "failed")
    assistant = await store.add_assistant_message(
        turn=VisitTurn(
            visit_matter_id="visit-matter-demo",
            participant_id="participant-demo",
            turn_id="cancelled-turn",
        )
    )
    await store.mark_assistant_streaming(assistant.id)
    await store.append_assistant_text(assistant.id, "半截回答")
    await store.finish_assistant_message(assistant.id, "cancelled")
