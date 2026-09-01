import asyncio
import json
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

from fastapi.testclient import TestClient

from medipet.actions import (
    ActionProposal,
    ActionReceipt,
    InMemoryActionStore,
    proposal_expiry,
)
from medipet.agent.capabilities import (
    CapabilitySnapshot,
    StaticCapabilityProvider,
    ToolContext,
    ToolDefinition,
)
from medipet.config import DevelopmentRuntimeConfig, ModelSettings
from medipet.delivery.http import create_app, default_hospital_tool_provider
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
from medipet.run_audits import InMemoryRunAuditStore


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


def test_default_fake_hospital_adapter_is_development_only() -> None:
    assert default_hospital_tool_provider("production") is None
    assert default_hospital_tool_provider(" development ") is not None


def test_visit_matters_can_be_created_listed_and_keep_history_isolated() -> None:
    store = seeded_store()
    client = TestClient(create_app(model=DeterministicModel(), conversation_store=store))

    created = client.post(
        "/v1/visit-matters",
        json={"participant_id": "participant-demo", "title": "复诊准备"},
    )

    assert created.status_code == 201
    created_visit = created.json()
    assert created_visit["visit_matter_id"] != "visit-matter-demo"
    assert created_visit["title"] == "复诊准备"

    listed = client.get(
        "/v1/visit-matters",
        params={"participant_id": "participant-demo"},
    )
    assert listed.status_code == 200
    assert [item["visit_matter_id"] for item in listed.json()["visit_matters"]] == [
        created_visit["visit_matter_id"],
        "visit-matter-demo",
    ]

    turn = client.post(
        "/v1/chat/turns",
        json={
            "visit_matter_id": created_visit["visit_matter_id"],
            "participant_id": "participant-demo",
            "messages": [
                {
                    "id": "message-new-visit",
                    "role": "user",
                    "parts": [{"type": "text", "text": "只属于复诊事项"}],
                }
            ],
        },
    )
    assert turn.status_code == 200

    original_history = client.get(
        "/v1/visit-matters/visit-matter-demo/messages",
        params={"participant_id": "participant-demo"},
    )
    new_history = client.get(
        f"/v1/visit-matters/{created_visit['visit_matter_id']}/messages",
        params={"participant_id": "participant-demo"},
    )
    assert original_history.json()["messages"] == []
    assert new_history.json()["messages"][0]["parts"] == [
        {"type": "text", "text": "只属于复诊事项"}
    ]


def test_visit_matter_history_management_http_contract() -> None:
    store = seeded_store()
    client = TestClient(create_app(model=DeterministicModel(), conversation_store=store))

    created = client.post(
        "/v1/visit-matters",
        json={"participant_id": "participant-demo"},
    )
    assert created.status_code == 201
    visit = created.json()
    assert visit["title"].endswith("就诊事项")
    assert visit["archived_at"] is None

    renamed = client.patch(
        f"/v1/visit-matters/{visit['visit_matter_id']}/title",
        json={"participant_id": "participant-demo", "title": "皮肤复诊"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "皮肤复诊"

    archived = client.post(
        f"/v1/visit-matters/{visit['visit_matter_id']}/archive",
        json={"participant_id": "participant-demo"},
    )
    assert archived.status_code == 200
    assert archived.json()["archived_at"] is not None
    active_items = client.get(
        "/v1/visit-matters",
        params={"participant_id": "participant-demo"},
    ).json()["visit_matters"]
    archived_items = client.get(
        "/v1/visit-matters",
        params={"participant_id": "participant-demo", "archived": True},
    ).json()["visit_matters"]
    assert visit["visit_matter_id"] not in {
        item["visit_matter_id"] for item in active_items
    }
    assert [item["visit_matter_id"] for item in archived_items] == [
        visit["visit_matter_id"]
    ]

    history = client.get(
        f"/v1/visit-matters/{visit['visit_matter_id']}/messages",
        params={"participant_id": "participant-demo"},
    )
    assert history.status_code == 200
    assert history.json()["messages"] == []

    chat = client.post(
        "/v1/chat/turns",
        json={
            "visit_matter_id": visit["visit_matter_id"],
            "participant_id": "participant-demo",
            "messages": [
                {
                    "id": "archived-message",
                    "role": "user",
                    "parts": [{"type": "text", "text": "继续咨询"}],
                }
            ],
        },
    )
    assert chat.status_code == 409
    assert chat.json()["detail"]["code"] == "visit_matter_archived"

    decision = client.post(
        "/v1/action-proposals/proposal-archived/decision",
        json={
            "decision": "confirm",
            "participant_id": "participant-demo",
            "visit_matter_id": visit["visit_matter_id"],
        },
    )
    assert decision.status_code == 409
    assert decision.json()["detail"]["code"] == "visit_matter_archived"

    restored = client.post(
        f"/v1/visit-matters/{visit['visit_matter_id']}/restore",
        json={"participant_id": "participant-demo"},
    )
    assert restored.status_code == 200
    assert restored.json()["archived_at"] is None


def test_archiving_busy_visit_matter_returns_typed_conflict() -> None:
    store = seeded_store()
    asyncio.run(
        store.claim_assistant_message(
            turn=VisitTurn(
                visit_matter_id="visit-matter-demo",
                participant_id="participant-demo",
                turn_id="busy-turn",
            )
        )
    )
    client = TestClient(create_app(model=DeterministicModel(), conversation_store=store))

    response = client.post(
        "/v1/visit-matters/visit-matter-demo/archive",
        json={"participant_id": "participant-demo"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "visit_matter_busy"


def test_archiving_uses_authoritative_pending_action_state() -> None:
    store = seeded_store()
    action_store = InMemoryActionStore()

    async def execute(
        arguments: dict[str, object],
        context: ToolContext,
    ) -> dict[str, object]:
        del arguments, context
        return {"saved": True}

    tool = ToolDefinition(
        tool_id="test.pending-action",
        name="pending_action",
        version="1",
        description="测试待确认操作",
        input_schema={"type": "object", "additionalProperties": False},
        effect="write",
        approval_required=True,
        execute=execute,
    )
    context = ToolContext(
        visit_matter_id="visit-matter-demo",
        participant_id="participant-demo",
        patient_id="patient-demo",
        patient_display_name="演示患者",
        idempotency_key="pending-action-request",
    )
    asyncio.run(
        action_store.create_proposal(
            tool,
            {},
            context,
            expires_at=proposal_expiry(),
        )
    )
    client = TestClient(
        create_app(
            model=DeterministicModel(),
            conversation_store=store,
            action_store=action_store,
        )
    )

    response = client.post(
        "/v1/visit-matters/visit-matter-demo/archive",
        json={"participant_id": "participant-demo"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "visit_matter_busy"


def test_resolved_action_allows_archive_when_history_projection_is_stale() -> None:
    store = seeded_store()
    action_store = InMemoryActionStore()

    async def arrange() -> None:
        async def execute(
            arguments: dict[str, object],
            context: ToolContext,
        ) -> dict[str, object]:
            del arguments, context
            return {"saved": True}

        tool = ToolDefinition(
            tool_id="test.resolved-action",
            name="resolved_action",
            version="1",
            description="测试已处理操作",
            input_schema={"type": "object", "additionalProperties": False},
            effect="write",
            approval_required=True,
            execute=execute,
        )
        context = ToolContext(
            visit_matter_id="visit-matter-demo",
            participant_id="participant-demo",
            patient_id="patient-demo",
            patient_display_name="演示患者",
            idempotency_key="resolved-action-request",
        )
        proposal = await action_store.create_proposal(
            tool,
            {},
            context,
            expires_at=proposal_expiry(),
        )
        assistant = await store.add_assistant_message(
            turn=VisitTurn(
                visit_matter_id="visit-matter-demo",
                participant_id="participant-demo",
                turn_id="resolved-action-turn",
            )
        )
        await store.append_assistant_part(
            assistant.id,
            {
                "type": "data-action-proposal",
                "data": {"proposalId": proposal.proposal_id, "status": "pending"},
            },
        )
        await store.mark_assistant_streaming(assistant.id)
        await store.finish_assistant_message(assistant.id, "completed")
        await action_store.reject(proposal.proposal_id, context)

    asyncio.run(arrange())
    client = TestClient(
        create_app(
            model=DeterministicModel(),
            conversation_store=store,
            action_store=action_store,
        )
    )

    response = client.post(
        "/v1/visit-matters/visit-matter-demo/archive",
        json={"participant_id": "participant-demo"},
    )

    assert response.status_code == 200
    assert response.json()["archived_at"] is not None


def test_action_decision_in_progress_blocks_archive_until_request_finishes() -> None:
    class BlockingAfterConfirmActionStore(InMemoryActionStore):
        def __init__(self) -> None:
            super().__init__()
            self.confirmed = Event()
            self.release = Event()

        async def confirm(
            self,
            proposal_id: str,
            context: ToolContext,
            tool: ToolDefinition,
        ) -> tuple[ActionProposal, ActionReceipt]:
            result = await super().confirm(proposal_id, context, tool)
            self.confirmed.set()
            await asyncio.to_thread(self.release.wait, 5)
            return result

    class WriteCallingModel(ModelPort):
        async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
            del request
            yield ModelChunk(
                tool_calls=(ModelToolCall("call-running", "running_write", {}),)
            )

    async def execute(
        arguments: dict[str, object],
        context: ToolContext,
    ) -> dict[str, object]:
        del arguments, context
        return {"saved": True}

    tool = ToolDefinition(
        tool_id="test.running-write",
        name="running_write",
        version="1",
        description="测试确认中的写操作",
        input_schema={"type": "object", "additionalProperties": False},
        effect="write",
        approval_required=True,
        execute=execute,
    )
    action_store = BlockingAfterConfirmActionStore()
    app = create_app(
        model=WriteCallingModel(),
        conversation_store=seeded_store(),
        capability_provider=StaticCapabilityProvider(CapabilitySnapshot(tools=(tool,))),
        action_store=action_store,
    )
    client = TestClient(app)
    proposal_response = client.post(
        "/v1/chat/turns",
        json={
            "idempotency_key": "running-proposal",
            "messages": [{
                "id": "running-message",
                "role": "user",
                "parts": [{"type": "text", "text": "执行确认中的操作"}],
            }],
        },
    )
    payloads = [
        json.loads(line.removeprefix("data: "))
        for line in proposal_response.text.splitlines()
        if line.startswith("data: {")
    ]
    proposal_id = next(
        payload["data"]["proposalId"]
        for payload in payloads
        if payload["type"] == "data-action-proposal"
    )

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            decision = executor.submit(
                client.post,
                f"/v1/action-proposals/{proposal_id}/decision",
                json={
                    "decision": "confirm",
                    "idempotency_key": "running-decision",
                    "visit_matter_id": "visit-matter-demo",
                    "participant_id": "participant-demo",
                },
            )
            assert action_store.confirmed.wait(5)
            archive = TestClient(app).post(
                "/v1/visit-matters/visit-matter-demo/archive",
                json={"participant_id": "participant-demo"},
            )
            assert archive.status_code == 409
            assert archive.json()["detail"]["code"] == "visit_matter_busy"
            action_store.release.set()
            assert decision.result(timeout=5).status_code == 200
    finally:
        action_store.release.set()


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


def test_write_tool_streams_a_proposal_and_decision_endpoint_confirms_it_once() -> None:
    executions: list[str] = []

    class WriteCallingModel(ModelPort):
        async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
            del request
            yield ModelChunk(
                tool_calls=(ModelToolCall("call-write", "private_dummy_write", {"value": "A"}),)
            )

    async def execute(arguments: dict[str, object], context: ToolContext) -> dict[str, object]:
        executions.append(context.idempotency_key)
        return {"saved": arguments["value"]}

    tool = ToolDefinition(
        tool_id="test.write",
        name="private_dummy_write",
        version="1",
        description="测试专用写 Tool",
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        effect="write",
        approval_required=True,
        execute=execute,
    )
    action_store = InMemoryActionStore()
    client = TestClient(
        create_app(
            model=WriteCallingModel(),
            conversation_store=seeded_store(),
            capability_provider=StaticCapabilityProvider(CapabilitySnapshot(tools=(tool,))),
            action_store=action_store,
        )
    )
    proposal_response = client.post(
        "/v1/chat/turns",
        json={
            "idempotency_key": "write-turn",
            "messages": [
                {
                    "id": "message-write",
                    "role": "user",
                    "parts": [{"type": "text", "text": "执行测试写操作"}],
                }
            ],
        },
    )
    payloads = [
        json.loads(line.removeprefix("data: "))
        for line in proposal_response.text.splitlines()
        if line.startswith("data: {")
    ]
    proposal = next(payload for payload in payloads if payload["type"] == "data-action-proposal")
    proposal_id = proposal["data"]["proposalId"]

    first = client.post(
        f"/v1/action-proposals/{proposal_id}/decision",
        json={"decision": "confirm", "idempotency_key": "decision-write"},
    )
    duplicate = client.post(
        f"/v1/action-proposals/{proposal_id}/decision",
        json={"decision": "confirm", "idempotency_key": "decision-write"},
    )

    assert first.status_code == duplicate.status_code == 200
    assert first.json()["events"][0]["data"]["data"]["status"] == "confirmed"
    assert (
        first.json()["events"][0]["data"]["data"]["receiptId"]
        == duplicate.json()["events"][0]["data"]["data"]["receiptId"]
    )
    assert len(executions) == 1


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


def test_run_audits_are_queryable_through_the_protected_management_api() -> None:
    audits = InMemoryRunAuditStore()
    client = TestClient(
        create_app(
            model=DeterministicModel(),
            conversation_store=seeded_store(),
            run_audit_store=audits,
            management_token="management-secret",
        )
    )
    response = client.post(
        "/v1/chat/turns",
        json={
            "idempotency_key": "audited-turn",
            "messages": [
                {
                    "id": "message-audit",
                    "role": "user",
                    "parts": [{"type": "text", "text": "审计测试"}],
                }
            ],
        },
    )

    unauthorized = client.get("/v1/admin/run-audits")
    queried = client.get(
        "/v1/admin/run-audits",
        headers={"Authorization": "Bearer management-secret"},
    )

    assert response.status_code == 200
    assert unauthorized.status_code == 401
    assert queried.status_code == 200
    assert queried.json()["audits"][-1] == {
        "kind": "completed",
        "traceId": queried.json()["audits"][-1]["traceId"],
        "visitMatterId": "visit-matter-demo",
        "turnId": "audited-turn",
        "profileVersion": "static",
        "createdAt": queried.json()["audits"][-1]["createdAt"],
    }


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
