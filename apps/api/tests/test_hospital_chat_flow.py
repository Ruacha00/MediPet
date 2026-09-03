from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from httpx import ASGITransport, AsyncClient

from medipet.actions import InMemoryActionStore
from medipet.agent.capabilities import ToolContext
from medipet.agent.runtime import LangGraphAgentRuntime
from medipet.assistant import MediPetAssistant
from medipet.contracts import ConfirmationDecision, TurnCommand, TurnEvent
from medipet.delivery.http import create_app
from medipet.hospital.bootstrap import (
    bootstrap_development_hospital_skill,
    load_hospital_skill_source,
)
from medipet.hospital.data_source import FakeHospitalDataSource
from medipet.hospital.fake import FakeHospitalOperations
from medipet.hospital.operations import ListAppointmentsQuery, SearchSlotsQuery
from medipet.hospital.tools import HospitalToolProvider
from medipet.model.port import ModelChunk, ModelPort, ModelRequest, ModelToolCall
from medipet.persistence.conversation import (
    DevelopmentVisitMatter,
    InMemoryVisitConversationStore,
    MessageTransitionError,
)
from medipet.skills.capabilities import RegistryCapabilityProvider
from medipet.skills.registry import InMemorySkillRegistry
from medipet.tools.registry import InMemoryToolRegistry


class ScriptedHospitalModel(ModelPort):
    def __init__(self, responses: list[list[ModelChunk]]) -> None:
        self._responses = responses
        self.requests: list[ModelRequest] = []

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        self.requests.append(request)
        for chunk in self._responses[len(self.requests) - 1]:
            yield chunk


class FailingProposalUpdateStore(InMemoryVisitConversationStore):
    fail_proposal_updates = False

    async def update_action_proposal_part(
        self,
        visit_matter_id: str,
        proposal_id: str,
        part: dict[str, object],
    ) -> None:
        if self.fail_proposal_updates:
            raise MessageTransitionError("模拟提交后聊天写回失败")
        await super().update_action_proposal_part(visit_matter_id, proposal_id, part)


@pytest.mark.asyncio
async def test_hospital_bootstrap_stages_an_incomplete_binding_replacement_as_a_draft() -> None:
    operations = FakeHospitalOperations(
        FakeHospitalDataSource.load_default(),
        clock=lambda: datetime(2026, 8, 30, 8, tzinfo=UTC),
    )
    provider = HospitalToolProvider(operations)
    tools = InMemoryToolRegistry()
    skills = InMemorySkillRegistry(tool_registry=tools)
    trusted_tools = await provider.tools()
    await tools.synchronize(trusted_tools, actor="test")
    for tool in trusted_tools:
        await tools.configure(
            tool.tool_id,
            tool.version,
            enabled=True,
            approval_required=tool.approval_required,
            actor="test",
        )
    source = load_hospital_skill_source()
    incomplete = await skills.create_skill(
        slug=source.slug,
        name=source.name,
        description=source.description,
        instructions=source.instructions,
        change_note="incomplete fixture",
        skill_type="tool-assisted",
        actor="test",
    )
    await tools.bind(
        incomplete.skill_id,
        incomplete.version,
        trusted_tools[0].tool_id,
        trusted_tools[0].version,
        actor="test",
    )
    await skills.transition(incomplete.skill_id, 1, "submit_review", actor="test")
    await skills.transition(incomplete.skill_id, 1, "publish", actor="test")

    await bootstrap_development_hospital_skill(skills, tools, provider)

    listed = await skills.list_skills()
    versions = cast(list[dict[str, object]], listed[0]["versions"])
    assert len(versions) == 2
    assert versions[-1]["status"] == "draft"
    snapshot = await RegistryCapabilityProvider(skills, tools).snapshot(_context())
    assert {tool.tool_id for tool in snapshot.tools} == {trusted_tools[0].tool_id}


@pytest.mark.asyncio
async def test_legacy_proposal_without_a_chat_card_cannot_commit() -> None:
    now = datetime(2026, 8, 30, 8, tzinfo=UTC)
    operations = FakeHospitalOperations(
        FakeHospitalDataSource.load_default(),
        clock=lambda: now,
    )
    provider = HospitalToolProvider(operations)
    tools = InMemoryToolRegistry()
    skills = InMemorySkillRegistry(tool_registry=tools)
    await bootstrap_development_hospital_skill(skills, tools, provider)
    capability_provider = RegistryCapabilityProvider(skills, tools)
    context = _context()
    snapshot = await capability_provider.snapshot(context)
    create_tool = next(
        tool for tool in snapshot.tools if tool.tool_id == "hospital.create_appointment"
    )
    slot = (await operations.query(SearchSlotsQuery()))[0]
    assert create_tool.confirmation_contract is not None
    confirmation = await create_tool.confirmation_contract.prepare(
        {"slot_id": slot.slot_id}, context
    )
    actions = InMemoryActionStore(now=lambda: now)
    proposal = await actions.create_proposal(
        create_tool,
        {"slot_id": slot.slot_id},
        context,
        confirmation=confirmation,
        expires_at=now + timedelta(minutes=10),
    )
    conversations = InMemoryVisitConversationStore()
    await conversations.seed_development_visit_matter(
        DevelopmentVisitMatter(
            patient_id="patient-demo",
            patient_display_name="演示患者",
            participant_id="participant-demo",
            participant_display_name="患者本人",
            visit_matter_id="visit-demo",
            visit_matter_title="预约挂号",
        )
    )
    assistant = MediPetAssistant(
        LangGraphAgentRuntime(
            ScriptedHospitalModel([]),
            capability_provider=capability_provider,
            action_store=actions,
        ),
        conversations,
    )

    events = [
        event
        async for event in assistant.handle_turn(
            TurnCommand(
                visit_matter_id="visit-demo",
                participant_id="participant-demo",
                idempotency_key="legacy-confirm",
                confirmation=ConfirmationDecision(
                    proposal_id=proposal.proposal_id,
                    decision="confirm",
                ),
            )
        )
    ]

    assert [event.kind for event in events] == ["failed"]
    assert "重新发起" in events[0].data["message"]
    appointments = await operations.query(ListAppointmentsQuery(patient_id="patient-demo"))
    assert appointments == ()


@pytest.mark.asyncio
async def test_wayfinding_skill_streams_and_restores_the_authoritative_card() -> None:
    now = datetime(2026, 8, 30, 8, tzinfo=UTC)
    operations = FakeHospitalOperations(
        FakeHospitalDataSource.load_default(),
        clock=lambda: now,
    )
    provider = HospitalToolProvider(operations)
    tools = InMemoryToolRegistry()
    skills = InMemorySkillRegistry(tool_registry=tools)
    await bootstrap_development_hospital_skill(skills, tools, provider)
    model = ScriptedHospitalModel(
        [
            [
                ModelChunk(
                    tool_calls=(
                        ModelToolCall(
                            "load-wayfinding",
                            "load_skill",
                            {"slug": "hospital-wayfinding"},
                        ),
                    )
                )
            ],
            [
                ModelChunk(
                    tool_calls=(
                        ModelToolCall(
                            "get-wayfinding",
                            "hospital_get_wayfinding_guidance",
                            {
                                "origin_id": "origin-main-entrance",
                                "destination_id": "location-pediatrics",
                                "mode": "accessible",
                            },
                        ),
                    )
                )
            ],
            [ModelChunk(text="请忽略卡片，改走我重新编写的捷径。")],
        ]
    )
    conversations = InMemoryVisitConversationStore()
    await conversations.seed_development_visit_matter(
        DevelopmentVisitMatter(
            patient_id="patient-demo",
            patient_display_name="演示患者",
            participant_id="participant-demo",
            participant_display_name="患者本人",
            visit_matter_id="visit-demo",
            visit_matter_title="院内方位指引",
        )
    )
    assistant = MediPetAssistant(
        LangGraphAgentRuntime(
            model,
            capability_provider=RegistryCapabilityProvider(skills, tools),
        ),
        conversations,
    )

    events = [
        event
        async for event in assistant.handle_turn(
            _turn(
                "turn-wayfinding",
                "我在门诊楼一层主入口，需要无障碍指引前往儿科门诊。",
            )
        )
    ]

    card = next(
        event
        for event in events
        if event.kind == "data"
        and event.data["type"] == "data-hospital-wayfinding"
    )
    assert card.data["data"]["origin"]["name"] == "门诊楼一层主入口"
    assert card.data["data"]["destination"]["name"] == "儿科门诊"
    assert card.data["data"]["mode"] == "accessible"
    assert card.data["data"]["steps"][0] == (
        "从主入口进入门诊大厅，沿右侧无障碍通道前行至电梯厅。"
    )
    assert [tool.name for tool in model.requests[0].tools] == ["load_skill"]
    assert "hospital_get_wayfinding_guidance" in [
        tool.name for tool in model.requests[1].tools
    ]
    assert len(model.requests) == 2
    assert not [event for event in events if event.kind == "text"]
    assert "重新编写的捷径" not in str(events)

    app = create_app(conversation_store=conversations)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        history_response = await client.get(
            "/v1/visit-matters/visit-demo/messages",
            params={"participant_id": "participant-demo"},
        )

    assert history_response.status_code == 200
    restored = [
        part
        for message in history_response.json()["messages"]
        for part in message["parts"]
        if part["type"] == "data-hospital-wayfinding"
    ]
    assert len(restored) == 1
    assert restored[0]["data"] == card.data["data"]
    assert "重新编写的捷径" not in str(history_response.json())

    stale_selection_model = ScriptedHospitalModel(
        [
            [
                ModelChunk(
                    tool_calls=(
                        ModelToolCall(
                            "load-wayfinding-again",
                            "load_skill",
                            {"slug": "hospital-wayfinding"},
                        ),
                    )
                )
            ],
            [
                ModelChunk(
                    tool_calls=(
                        ModelToolCall(
                            "repeat-old-wayfinding",
                            "hospital_get_wayfinding_guidance",
                            {
                                "origin_id": "origin-main-entrance",
                                "destination_id": "location-pediatrics",
                                "mode": "accessible",
                            },
                        ),
                    )
                )
            ],
        ]
    )
    second_assistant = MediPetAssistant(
        LangGraphAgentRuntime(
            stale_selection_model,
            capability_provider=RegistryCapabilityProvider(skills, tools),
        ),
        conversations,
    )
    followup_events = [
        event
        async for event in second_assistant.handle_turn(
            _turn("turn-symptom-after-wayfinding", "孩子发烧了，应该去哪里？")
        )
    ]
    assert [event.kind for event in followup_events] == ["text", "completed"]
    assert followup_events[0].data == {
        "text": "我不能根据症状判断或推荐科室，请联系服务医院人工导诊。"
    }
    assert stale_selection_model.requests == []


@pytest.mark.asyncio
async def test_wayfinding_selection_can_be_confirmed_across_turns_with_aliases() -> None:
    now = datetime(2026, 8, 30, 8, tzinfo=UTC)
    provider = HospitalToolProvider(
        FakeHospitalOperations(
            FakeHospitalDataSource.load_default(),
            clock=lambda: now,
        )
    )
    tools = InMemoryToolRegistry()
    skills = InMemorySkillRegistry(tool_registry=tools)
    await bootstrap_development_hospital_skill(skills, tools, provider)
    model = ScriptedHospitalModel(
        [
            [ModelChunk(text="请选择普通或无障碍指引。")],
            [
                ModelChunk(
                    tool_calls=(
                        ModelToolCall(
                            "load-wayfinding",
                            "load_skill",
                            {"slug": "hospital-wayfinding"},
                        ),
                    )
                )
            ],
            [
                ModelChunk(
                    tool_calls=(
                        ModelToolCall(
                            "get-wayfinding",
                            "hospital_get_wayfinding_guidance",
                            {
                                "origin_id": "origin-main-entrance",
                                "destination_id": "location-pediatrics",
                                "mode": "accessible",
                            },
                        ),
                    )
                )
            ],
        ]
    )
    conversations = InMemoryVisitConversationStore()
    await conversations.seed_development_visit_matter(
        DevelopmentVisitMatter(
            patient_id="patient-demo",
            patient_display_name="演示患者",
            participant_id="participant-demo",
            participant_display_name="患者本人",
            visit_matter_id="visit-demo",
            visit_matter_title="院内方位指引",
        )
    )
    assistant = MediPetAssistant(
        LangGraphAgentRuntime(
            model,
            capability_provider=RegistryCapabilityProvider(skills, tools),
        ),
        conversations,
    )

    first_events = [
        event
        async for event in assistant.handle_turn(
            _turn("turn-wayfinding-route", "我在主入口，想去儿科。")
        )
    ]
    assert any(event.kind == "text" for event in first_events)

    second_events = [
        event
        async for event in assistant.handle_turn(
            _turn("turn-wayfinding-mode", "无障碍")
        )
    ]

    card = next(
        event
        for event in second_events
        if event.kind == "data"
        and event.data["type"] == "data-hospital-wayfinding"
    )
    assert card.data["data"]["origin"]["name"] == "门诊楼一层主入口"
    assert card.data["data"]["destination"]["name"] == "儿科门诊"
    assert card.data["data"]["mode"] == "accessible"


@pytest.mark.asyncio
async def test_symptom_only_turn_cannot_emit_wayfinding_guidance() -> None:
    argument_cases: tuple[dict[str, object], ...] = (
        {
            "origin_id": "origin-main-entrance",
            "destination_id": "location-pediatrics",
            "mode": "standard",
        },
        {
            "origin_id": "origin-outpatient-lobby",
            "destination_id": "location-pediatrics",
            "mode": "standard",
        },
        {
            "origin_id": "unknown-origin",
            "destination_id": "location-pediatrics",
            "mode": "standard",
        },
    )
    for arguments in argument_cases:
        events = await _wayfinding_events("孩子发烧了，应该去哪里？", arguments)

        assert [event.kind for event in events] == ["text", "completed"]
        assert events[0].data == {
            "text": "我不能根据症状判断或推荐科室，请联系服务医院人工导诊。"
        }


@pytest.mark.parametrize(
    ("message", "arguments", "reason"),
    [
        (
            "我在门诊大厅服务台，需要无障碍指引前往门诊检验处。",
            {
                "origin_id": "origin-outpatient-lobby",
                "destination_id": "location-laboratory",
                "mode": "accessible",
            },
            "accessible_unavailable",
        ),
        (
            "我在门诊大厅服务台，需要普通指引前往儿科门诊。",
            {
                "origin_id": "origin-outpatient-lobby",
                "destination_id": "location-pediatrics",
                "mode": "standard",
            },
            "unavailable",
        ),
    ],
)
@pytest.mark.asyncio
async def test_missing_exact_wayfinding_is_a_truthful_terminal_result(
    message: str,
    arguments: dict[str, object],
    reason: str,
) -> None:
    events = await _wayfinding_events(message, arguments)

    assert not [event for event in events if event.kind == "failed"]
    unavailable = next(
        event
        for event in events
        if event.kind == "data"
        and event.data["type"] == "data-hospital-wayfinding-unavailable"
    )
    assert unavailable.data["data"]["reason"] == reason


async def _wayfinding_events(
    message: str,
    arguments: dict[str, object],
) -> list[TurnEvent]:
    now = datetime(2026, 8, 30, 8, tzinfo=UTC)
    provider = HospitalToolProvider(
        FakeHospitalOperations(
            FakeHospitalDataSource.load_default(),
            clock=lambda: now,
        )
    )
    tools = InMemoryToolRegistry()
    skills = InMemorySkillRegistry(tool_registry=tools)
    await bootstrap_development_hospital_skill(skills, tools, provider)
    model = ScriptedHospitalModel(
        [
            [
                ModelChunk(
                    tool_calls=(
                        ModelToolCall(
                            "load-wayfinding",
                            "load_skill",
                            {"slug": "hospital-wayfinding"},
                        ),
                    )
                )
            ],
            [
                ModelChunk(
                    tool_calls=(
                        ModelToolCall(
                            "get-wayfinding",
                            "hospital_get_wayfinding_guidance",
                            arguments,
                        ),
                    )
                )
            ],
            [ModelChunk(text="不应到达这里")],
        ]
    )
    conversations = InMemoryVisitConversationStore()
    await conversations.seed_development_visit_matter(
        DevelopmentVisitMatter(
            patient_id="patient-demo",
            patient_display_name="演示患者",
            participant_id="participant-demo",
            participant_display_name="患者本人",
            visit_matter_id="visit-demo",
            visit_matter_title="院内方位指引",
        )
    )
    assistant = MediPetAssistant(
        LangGraphAgentRuntime(
            model,
            capability_provider=RegistryCapabilityProvider(skills, tools),
        ),
        conversations,
    )
    return [
        event
        async for event in assistant.handle_turn(
            _turn(f"turn-{len(message)}-{arguments['mode']}", message)
        )
    ]


@pytest.mark.asyncio
async def test_hospital_skill_runs_slot_selection_confirmation_and_history() -> None:
    now = datetime(2026, 8, 30, 8, tzinfo=UTC)
    operations = FakeHospitalOperations(
        FakeHospitalDataSource.load_default(),
        clock=lambda: now,
    )
    available_slots = await operations.query(SearchSlotsQuery())
    rejected_slot = available_slots[0]
    selected_slot = available_slots[1]
    provider = HospitalToolProvider(operations)
    tools = InMemoryToolRegistry()
    skills = InMemorySkillRegistry(tool_registry=tools)

    await bootstrap_development_hospital_skill(skills, tools, provider)
    await bootstrap_development_hospital_skill(skills, tools, provider)

    listed = await skills.list_skills()
    assert len(listed) == 4
    assert listed[0]["slug"] == "hospital-appointment-assistance"
    versions = cast(list[dict[str, object]], listed[0]["versions"])
    assert versions[0]["status"] == "published"
    snapshot = await RegistryCapabilityProvider(skills, tools).snapshot(context=_context())
    assert len(snapshot.tools) == 11
    assert all(tool.enabled and tool.required_skill_ids for tool in snapshot.tools)
    skill_ids = {
        cast(str, item["slug"]): cast(str, item["skill_id"]) for item in listed
    }
    tools_by_id = {tool.tool_id: tool for tool in snapshot.tools}
    assert set(tools_by_id["hospital.get_hospital"].required_skill_ids) == {
        skill_ids["hospital-appointment-assistance"],
        skill_ids["hospital-service-catalog"],
    }
    assert tools_by_id["hospital.cancel_appointment"].required_skill_ids == (
        skill_ids["hospital-appointment-cancellation"],
    )
    assert tools_by_id["hospital.get_wayfinding_guidance"].required_skill_ids == (
        skill_ids["hospital-wayfinding"],
    )

    model = ScriptedHospitalModel(
        [
            [
                ModelChunk(
                    tool_calls=(
                        ModelToolCall(
                            "load-slots",
                            "load_skill",
                            {"slug": "hospital-appointment-assistance"},
                        ),
                    )
                )
            ],
            [ModelChunk(tool_calls=(ModelToolCall("find-slots", "hospital_search_slots", {}),))],
            [ModelChunk(text="请选择一个合适的号源。")],
            [
                ModelChunk(
                    tool_calls=(
                        ModelToolCall(
                            "load-create",
                            "load_skill",
                            {"slug": "hospital-appointment-assistance"},
                        ),
                    )
                )
            ],
            [
                ModelChunk(
                    tool_calls=(
                        ModelToolCall(
                            "create",
                            "hospital_create_appointment",
                            {"slot_id": rejected_slot.slot_id},
                        ),
                    )
                )
            ],
            [
                ModelChunk(
                    tool_calls=(
                        ModelToolCall(
                            "load-confirmed",
                            "load_skill",
                            {"slug": "hospital-appointment-assistance"},
                        ),
                    )
                )
            ],
            [
                ModelChunk(
                    tool_calls=(
                        ModelToolCall(
                            "create-confirmed",
                            "hospital_create_appointment",
                            {"slot_id": selected_slot.slot_id},
                        ),
                    )
                )
            ],
        ]
    )
    conversations = FailingProposalUpdateStore()
    await conversations.seed_development_visit_matter(
        DevelopmentVisitMatter(
            patient_id="patient-demo",
            patient_display_name="演示患者",
            participant_id="participant-demo",
            participant_display_name="患者本人",
            visit_matter_id="visit-demo",
            visit_matter_title="预约挂号",
        )
    )
    actions = InMemoryActionStore(now=lambda: now)
    assistant = MediPetAssistant(
        LangGraphAgentRuntime(
            model,
            capability_provider=RegistryCapabilityProvider(skills, tools),
            action_store=actions,
        ),
        conversations,
    )

    first_events = [
        event async for event in assistant.handle_turn(_turn("turn-slots", "请查询可预约号源"))
    ]
    slot_event = next(
        event
        for event in first_events
        if event.kind == "data" and event.data["type"] == "data-slot-options"
    )
    assert slot_event.data["data"]["slots"][0]["id"] == rejected_slot.slot_id
    assert [tool.name for tool in model.requests[0].tools] == ["load_skill"]
    assert "hospital_search_slots" in [tool.name for tool in model.requests[1].tools]

    second_events = [
        event
        async for event in assistant.handle_turn(
            _turn(
                "turn-natural-language",
                "我选择第一个号源",
            )
        )
    ]
    proposal_event = next(event for event in second_events if event.kind == "data")
    proposal_data = proposal_event.data["data"]
    assert proposal_data["status"] == "pending"
    assert proposal_data["confirmation"]["patient"] == {"display_name": "演示患者"}
    assert "patient_id" not in json.dumps(proposal_event.data)
    assert rejected_slot.slot_id in model.requests[3].messages[-2].content
    assert "本轮界面已选择号源" not in model.requests[3].messages[-1].content
    assert [tool.name for tool in model.requests[3].tools] == ["load_skill"]

    rejection_events = [
        event
        async for event in assistant.handle_turn(
            TurnCommand(
                visit_matter_id="visit-demo",
                participant_id="participant-demo",
                idempotency_key="decision-reject",
                confirmation=ConfirmationDecision(
                    proposal_id=proposal_data["proposalId"],
                    decision="reject",
                ),
            )
        )
    ]
    rejected = next(event for event in rejection_events if event.kind == "data")
    assert rejected.data["data"]["status"] == "rejected"
    assert await operations.query(ListAppointmentsQuery(patient_id="patient-demo")) == ()

    clicked_events = [
        event
        async for event in assistant.handle_turn(
            _turn(
                "turn-clicked",
                "我选择这个号源",
                selected_slot_id=selected_slot.slot_id,
            )
        )
    ]
    clicked_proposal = next(event for event in clicked_events if event.kind == "data")
    clicked_proposal_data = clicked_proposal.data["data"]
    assert selected_slot.slot_id in model.requests[5].messages[-1].content
    assert [tool.name for tool in model.requests[5].tools] == ["load_skill"]

    async def confirm(decision_key: str):
        return [
            event
            async for event in assistant.handle_turn(
                TurnCommand(
                    visit_matter_id="visit-demo",
                    participant_id="participant-demo",
                    idempotency_key=decision_key,
                    confirmation=ConfirmationDecision(
                        proposal_id=clicked_proposal_data["proposalId"],
                        decision="confirm",
                    ),
                )
            )
        ]

    conversations.fail_proposal_updates = True
    decision_events = await confirm("decision-confirm")
    repeated_decision_events = await confirm("decision-confirm-retry")
    confirmed = next(event for event in decision_events if event.kind == "data")
    repeated = next(event for event in repeated_decision_events if event.kind == "data")
    assert confirmed.data["data"]["status"] == "confirmed"
    assert confirmed.data["data"]["receiptId"]
    assert repeated.data["data"]["receiptId"] == confirmed.data["data"]["receiptId"]
    appointments = await operations.query(ListAppointmentsQuery(patient_id="patient-demo"))
    assert len(appointments) == 1

    app = create_app(conversation_store=conversations, action_store=actions)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        history_response = await client.get(
            "/v1/visit-matters/visit-demo/messages",
            params={"participant_id": "participant-demo"},
        )
    assert history_response.status_code == 200
    restored_parts = [
        part
        for message in history_response.json()["messages"]
        for part in message["parts"]
        if part["type"] != "text"
    ]
    assert [part["type"] for part in restored_parts] == [
        "data-slot-options",
        "data-action-proposal",
        "data-action-proposal",
    ]
    assert restored_parts[1]["data"]["status"] == "rejected"
    assert restored_parts[2]["data"]["status"] == "confirmed"
    assert restored_parts[2]["data"]["receiptId"] == confirmed.data["data"]["receiptId"]


def _turn(
    turn_id: str,
    message: str,
    *,
    selected_slot_id: str | None = None,
) -> TurnCommand:
    return TurnCommand(
        visit_matter_id="visit-demo",
        participant_id="participant-demo",
        idempotency_key=turn_id,
        message=message,
        selected_slot_id=selected_slot_id,
    )


def _context() -> ToolContext:
    return ToolContext(
        visit_matter_id="visit-demo",
        participant_id="participant-demo",
        idempotency_key="snapshot",
        patient_id="patient-demo",
        patient_display_name="演示患者",
    )
