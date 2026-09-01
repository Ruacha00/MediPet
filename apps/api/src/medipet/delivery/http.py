from __future__ import annotations

import os
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from medipet.action_postgres import PostgresActionStore
from medipet.actions import ActionDecisionError, ActionStore, UnavailableActionStore
from medipet.agent.capabilities import CapabilityProvider, ToolContext
from medipet.agent.runtime import LangGraphAgentRuntime, hospital_data_available
from medipet.assistant import MediPetAssistant
from medipet.config import (
    ModelConfigurationError,
    ModelSettings,
    RuntimeConfig,
    RuntimeConfigSnapshot,
    runtime_config_from_startup_environment,
)
from medipet.contracts import (
    ActionDecisionRequest,
    ActionDecisionResponse,
    ChatTurnRequest,
    ConfirmationDecision,
    ConversationHistoryMessage,
    ConversationHistoryResponse,
    CreateVisitMatterRequest,
    RenameVisitMatterRequest,
    TurnCommand,
    UIMessagePart,
    VisitMatterLifecycleRequest,
    VisitMatterListResponse,
    VisitMatterResponse,
)
from medipet.delivery.streaming import to_ui_message_stream
from medipet.hospital.data_source import FakeHospitalDataSource
from medipet.hospital.fake import FakeHospitalOperations
from medipet.hospital.tools import HospitalToolProvider
from medipet.model.openai import ChatOpenAIModelAdapter
from medipet.model.port import ModelPort
from medipet.persistence.conversation import (
    VisitConversationStore,
    VisitMatterArchivedError,
    VisitMatterBusyError,
    VisitMatterNotFoundError,
)
from medipet.persistence.postgres import (
    DatabaseConfigurationError,
    PostgresVisitConversationStore,
)
from medipet.run_audit_http import run_audit_router
from medipet.run_audit_postgres import PostgresRunAuditStore
from medipet.run_audits import RunAuditStore
from medipet.run_metric_postgres import PostgresRunMetricStore
from medipet.run_metrics import RunMetricStore
from medipet.skills.capabilities import RegistryCapabilityProvider
from medipet.skills.http import management_router
from medipet.skills.postgres import PostgresSkillRegistry
from medipet.skills.registry import SkillRegistry
from medipet.tools.http import tool_management_router
from medipet.tools.postgres import PostgresToolRegistry
from medipet.tools.registry import ToolProvider, ToolRegistry

MODEL_UNAVAILABLE_MESSAGE = "模型服务配置不可用"
DATABASE_UNAVAILABLE_MESSAGE = "数据库服务配置不可用"


def _default_visit_matter_title() -> str:
    timezone_name = os.getenv("MEDIPET_HOSPITAL_TIMEZONE", "Asia/Shanghai")
    try:
        local_now = datetime.now(ZoneInfo(timezone_name))
    except (KeyError, ValueError):
        local_now = datetime.now(UTC)
    return f"{local_now.month} 月 {local_now.day} 日 {local_now:%H:%M} 就诊事项"


def create_app(
    *,
    model: ModelPort | None = None,
    model_provider: str = "unknown",
    model_name: str = "unknown",
    conversation_store: VisitConversationStore | None = None,
    close_conversation_store: bool = False,
    runtime_config: RuntimeConfig | None = None,
    model_factory: Callable[[ModelSettings], ModelPort] = ChatOpenAIModelAdapter,
    capability_provider: CapabilityProvider | None = None,
    skill_registry: SkillRegistry | None = None,
    close_skill_registry: bool = False,
    management_token: str | None = None,
    environment: str = "development",
    tool_registry: ToolRegistry | None = None,
    tool_provider: ToolProvider | None = None,
    close_tool_registry: bool = False,
    action_store: ActionStore | None = None,
    close_action_store: bool = False,
    run_audit_store: RunAuditStore | None = None,
    close_run_audit_store: bool = False,
    run_metric_store: RunMetricStore | None = None,
    close_run_metric_store: bool = False,
) -> FastAPI:
    if model is not None and runtime_config is not None:
        raise ValueError("model and runtime_config cannot both be provided")

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if tool_provider is not None and tool_registry is not None:
            await tool_registry.synchronize(await tool_provider.tools(), actor="deployment")
        yield
        if close_conversation_store and isinstance(
            conversation_store,
            PostgresVisitConversationStore,
        ):
            await conversation_store.close()
        if close_skill_registry and isinstance(skill_registry, PostgresSkillRegistry):
            await skill_registry.close()
        if close_tool_registry and isinstance(tool_registry, PostgresToolRegistry):
            await tool_registry.close()
        if close_action_store and isinstance(action_store, PostgresActionStore):
            await action_store.close()
        if close_run_audit_store and isinstance(run_audit_store, PostgresRunAuditStore):
            await run_audit_store.close()
        if close_run_metric_store and isinstance(run_metric_store, PostgresRunMetricStore):
            await run_metric_store.close()

    app = FastAPI(title="MediPet", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[os.getenv("MEDIPET_WEB_ORIGIN", "http://localhost:3000")],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    effective_capability_provider = capability_provider or (
        RegistryCapabilityProvider(skill_registry, tool_registry)
        if skill_registry is not None
        else None
    )
    effective_action_store = action_store or UnavailableActionStore()
    running_action_decisions: dict[tuple[str, str], int] = {}
    static_assistant = (
        MediPetAssistant(
            LangGraphAgentRuntime(
                model,
                capability_provider=effective_capability_provider,
                action_store=effective_action_store,
                audit_store=run_audit_store,
            ),
            conversation_store,
            audit_store=run_audit_store,
            provider=model_provider,
            model=model_name,
            metric_store=run_metric_store,
        )
        if model is not None and conversation_store is not None
        else None
    )

    def runtime_snapshot_or_unavailable() -> RuntimeConfigSnapshot | None:
        try:
            if runtime_config is None:
                return None
            return runtime_config.snapshot()
        except ModelConfigurationError as error:
            raise HTTPException(status_code=503, detail=MODEL_UNAVAILABLE_MESSAGE) from error

    def assistant_for_new_turn(
        snapshot: RuntimeConfigSnapshot | None,
    ) -> MediPetAssistant | None:
        if snapshot is None:
            return static_assistant
        if conversation_store is None:
            return None
        settings = snapshot.settings
        return MediPetAssistant(
            LangGraphAgentRuntime(
                model_factory(settings.model),
                capability_provider=effective_capability_provider,
                max_steps=settings.max_steps,
                profile_version=snapshot.fingerprint,
                action_store=effective_action_store,
                model_timeout_seconds=settings.model.timeout_seconds,
                audit_store=run_audit_store,
            ),
            conversation_store,
            context_message_limit=settings.context_message_limit,
            turn_timeout_seconds=settings.turn_timeout_seconds,
            audit_store=run_audit_store,
            profile_version=snapshot.fingerprint,
            provider=settings.model.provider,
            model=settings.model.model,
            metric_store=run_metric_store,
        )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/capabilities/status")
    async def capability_status() -> dict[str, bool]:
        if effective_capability_provider is None:
            return {"hospital_data_available": False}
        try:
            context = ToolContext()
            capabilities = await effective_capability_provider.snapshot(context)
        except Exception as error:
            raise HTTPException(
                status_code=503,
                detail="运行时能力暂时不可用",
            ) from error
        return {"hospital_data_available": hospital_data_available(capabilities, context)}

    @app.get("/v1/visit-matters", response_model=VisitMatterListResponse)
    async def visit_matters(
        participant_id: str,
        archived: bool = False,
    ) -> VisitMatterListResponse:
        if conversation_store is None:
            raise HTTPException(status_code=503, detail=DATABASE_UNAVAILABLE_MESSAGE)
        summaries = await conversation_store.list_visit_matters(
            participant_id,
            archived=archived,
        )
        return VisitMatterListResponse(
            visit_matters=[
                VisitMatterResponse.model_validate(summary, from_attributes=True)
                for summary in summaries
            ]
        )

    @app.post(
        "/v1/visit-matters",
        response_model=VisitMatterResponse,
        status_code=201,
    )
    async def create_visit_matter(request: CreateVisitMatterRequest) -> VisitMatterResponse:
        if conversation_store is None:
            raise HTTPException(status_code=503, detail=DATABASE_UNAVAILABLE_MESSAGE)
        try:
            summary = await conversation_store.create_visit_matter(
                participant_id=request.participant_id,
                title=request.title or _default_visit_matter_title(),
            )
        except VisitMatterNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return VisitMatterResponse.model_validate(summary, from_attributes=True)

    @app.patch(
        "/v1/visit-matters/{visit_matter_id}/title",
        response_model=VisitMatterResponse,
    )
    async def rename_visit_matter(
        visit_matter_id: str,
        request: RenameVisitMatterRequest,
    ) -> VisitMatterResponse:
        if conversation_store is None:
            raise HTTPException(status_code=503, detail=DATABASE_UNAVAILABLE_MESSAGE)
        try:
            summary = await conversation_store.rename_visit_matter(
                visit_matter_id,
                request.participant_id,
                title=request.title,
            )
        except VisitMatterNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return VisitMatterResponse.model_validate(summary, from_attributes=True)

    @app.post(
        "/v1/visit-matters/{visit_matter_id}/archive",
        response_model=VisitMatterResponse,
    )
    async def archive_visit_matter(
        visit_matter_id: str,
        request: VisitMatterLifecycleRequest,
    ) -> VisitMatterResponse:
        if conversation_store is None:
            raise HTTPException(status_code=503, detail=DATABASE_UNAVAILABLE_MESSAGE)
        try:
            decision_scope = (visit_matter_id, request.participant_id)
            pending_action = await effective_action_store.has_pending_proposal(
                visit_matter_id,
                request.participant_id,
            )
            if running_action_decisions.get(decision_scope, 0) > 0:
                pending_action = True
            summary = await conversation_store.archive_visit_matter(
                visit_matter_id,
                request.participant_id,
                pending_action=pending_action,
            )
        except VisitMatterBusyError as error:
            raise HTTPException(
                status_code=409,
                detail={"code": "visit_matter_busy", "message": str(error)},
            ) from error
        except VisitMatterNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return VisitMatterResponse.model_validate(summary, from_attributes=True)

    @app.post(
        "/v1/visit-matters/{visit_matter_id}/restore",
        response_model=VisitMatterResponse,
    )
    async def restore_visit_matter(
        visit_matter_id: str,
        request: VisitMatterLifecycleRequest,
    ) -> VisitMatterResponse:
        if conversation_store is None:
            raise HTTPException(status_code=503, detail=DATABASE_UNAVAILABLE_MESSAGE)
        try:
            summary = await conversation_store.restore_visit_matter(
                visit_matter_id,
                request.participant_id,
            )
        except VisitMatterNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return VisitMatterResponse.model_validate(summary, from_attributes=True)

    if environment.strip().lower() != "production" and skill_registry is not None:
        app.include_router(management_router(skill_registry, management_token))
    if environment.strip().lower() != "production" and tool_registry is not None:
        app.include_router(tool_management_router(tool_registry, management_token, skill_registry))
    if environment.strip().lower() != "production" and run_audit_store is not None:
        app.include_router(run_audit_router(run_audit_store, management_token, run_metric_store))

    @app.get("/ready")
    async def ready() -> dict[str, str]:
        snapshot = runtime_snapshot_or_unavailable()
        if model is None and snapshot is None:
            raise HTTPException(status_code=503, detail=MODEL_UNAVAILABLE_MESSAGE)
        if conversation_store is None:
            raise HTTPException(status_code=503, detail=DATABASE_UNAVAILABLE_MESSAGE)
        try:
            await conversation_store.ping()
        except Exception as error:
            raise HTTPException(
                status_code=503,
                detail=DATABASE_UNAVAILABLE_MESSAGE,
            ) from error
        return {"status": "ready"}

    @app.post("/v1/chat/turns")
    async def chat_turn(request: ChatTurnRequest) -> StreamingResponse:
        snapshot = runtime_snapshot_or_unavailable()
        if model is None and snapshot is None:
            raise HTTPException(status_code=503, detail=MODEL_UNAVAILABLE_MESSAGE)
        assistant = assistant_for_new_turn(snapshot)
        if assistant is None or conversation_store is None:
            raise HTTPException(status_code=503, detail=DATABASE_UNAVAILABLE_MESSAGE)
        try:
            message = request.latest_participant_text()
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

        command = TurnCommand(
            visit_matter_id=request.visit_matter_id,
            participant_id=request.participant_id,
            idempotency_key=request.idempotency_key,
            message=message,
            selected_slot_id=request.selected_slot_id,
        )
        try:
            await conversation_store.visit_context(
                request.visit_matter_id,
                request.participant_id,
            )
        except VisitMatterArchivedError as error:
            raise HTTPException(
                status_code=409,
                detail={"code": "visit_matter_archived", "message": str(error)},
            ) from error
        except VisitMatterNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return StreamingResponse(
            to_ui_message_stream(assistant.handle_turn(command)),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "x-vercel-ai-ui-message-stream": "v1",
            },
        )

    @app.get(
        "/v1/visit-matters/{visit_matter_id}/messages",
        response_model=ConversationHistoryResponse,
    )
    async def conversation_history(
        visit_matter_id: str,
        participant_id: str,
    ) -> ConversationHistoryResponse:
        if conversation_store is None:
            raise HTTPException(status_code=503, detail=DATABASE_UNAVAILABLE_MESSAGE)
        try:
            await conversation_store.validate_visit_participant(
                visit_matter_id,
                participant_id,
            )
        except VisitMatterNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        messages = await conversation_store.list_messages(visit_matter_id)
        history_messages: list[ConversationHistoryMessage] = []
        for message in messages:
            authoritative_parts: list[dict[str, object]] = []
            for part in message.parts:
                authoritative_part = part
                data = part.get("data")
                proposal_id = data.get("proposalId") if isinstance(data, dict) else None
                if part.get("type") == "data-action-proposal" and isinstance(proposal_id, str):
                    try:
                        proposal = await effective_action_store.get_proposal(proposal_id)
                    except ActionDecisionError:
                        pass
                    else:
                        if (
                            proposal.visit_matter_id == visit_matter_id
                            and proposal.participant_id == participant_id
                        ):
                            authoritative_part = proposal.event_data()
                authoritative_parts.append(authoritative_part)
            history_messages.append(
                ConversationHistoryMessage(
                    id=message.id,
                    role="user" if message.role == "participant" else "assistant",
                    state=message.state,
                    parts=[
                        *(UIMessagePart.model_validate(part) for part in authoritative_parts),
                        *(
                            [UIMessagePart(type="text", text=message.content)]
                            if message.content
                            else []
                        ),
                    ],
                    created_at=message.created_at,
                    updated_at=message.updated_at,
                )
            )
        return ConversationHistoryResponse(
            visit_matter_id=visit_matter_id,
            messages=history_messages,
        )

    @app.post(
        "/v1/action-proposals/{proposal_id}/decision",
        response_model=ActionDecisionResponse,
    )
    async def decide_action(
        proposal_id: str,
        request: ActionDecisionRequest,
    ) -> ActionDecisionResponse:
        snapshot = runtime_snapshot_or_unavailable()
        assistant = assistant_for_new_turn(snapshot)
        if assistant is None:
            raise HTTPException(status_code=503, detail=MODEL_UNAVAILABLE_MESSAGE)
        if conversation_store is None:
            raise HTTPException(status_code=503, detail=DATABASE_UNAVAILABLE_MESSAGE)
        decision_scope = (request.visit_matter_id, request.participant_id)
        running_action_decisions[decision_scope] = (
            running_action_decisions.get(decision_scope, 0) + 1
        )
        try:
            try:
                await conversation_store.visit_context(
                    request.visit_matter_id,
                    request.participant_id,
                )
            except VisitMatterArchivedError as error:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "visit_matter_archived", "message": str(error)},
                ) from error
            except VisitMatterNotFoundError as error:
                raise HTTPException(status_code=404, detail=str(error)) from error
            command = TurnCommand(
                visit_matter_id=request.visit_matter_id,
                participant_id=request.participant_id,
                idempotency_key=request.idempotency_key,
                confirmation=ConfirmationDecision(
                    proposal_id=proposal_id,
                    decision=request.decision,
                ),
            )
            events = [event async for event in assistant.handle_turn(command)]
            return ActionDecisionResponse(
                proposal_id=proposal_id,
                decision=request.decision,
                events=events,
            )
        finally:
            remaining = running_action_decisions[decision_scope] - 1
            if remaining > 0:
                running_action_decisions[decision_scope] = remaining
            else:
                running_action_decisions.pop(decision_scope, None)

    return app


def _store_from_environment() -> PostgresVisitConversationStore | None:
    try:
        return PostgresVisitConversationStore.from_url(os.getenv("MEDIPET_DATABASE_URL", ""))
    except DatabaseConfigurationError:
        return None


def _tool_registry_from_environment() -> PostgresToolRegistry | None:
    try:
        return PostgresToolRegistry.from_url(os.getenv("MEDIPET_DATABASE_URL", ""))
    except DatabaseConfigurationError:
        return None


def default_hospital_tool_provider(environment: str) -> ToolProvider | None:
    if environment.strip().lower() == "production":
        return None
    operations = FakeHospitalOperations(
        FakeHospitalDataSource.load_default(),
        clock=lambda: datetime.now(UTC),
    )
    return HospitalToolProvider(operations)


_tool_registry = _tool_registry_from_environment()
_environment = os.getenv("MEDIPET_ENVIRONMENT", "development")


def _action_store_from_environment() -> PostgresActionStore | None:
    try:
        return PostgresActionStore.from_url(os.getenv("MEDIPET_DATABASE_URL", ""))
    except DatabaseConfigurationError:
        return None


_action_store = _action_store_from_environment()


def _run_audit_store_from_environment() -> PostgresRunAuditStore | None:
    try:
        return PostgresRunAuditStore.from_url(os.getenv("MEDIPET_DATABASE_URL", ""))
    except DatabaseConfigurationError:
        return None


_run_audit_store = _run_audit_store_from_environment()


def _run_metric_store_from_environment() -> PostgresRunMetricStore | None:
    try:
        return PostgresRunMetricStore.from_url(os.getenv("MEDIPET_DATABASE_URL", ""))
    except DatabaseConfigurationError:
        return None


_run_metric_store = _run_metric_store_from_environment()


def _skill_registry_from_environment() -> PostgresSkillRegistry | None:
    try:
        return PostgresSkillRegistry.from_url(
            os.getenv("MEDIPET_DATABASE_URL", ""), tool_registry=_tool_registry
        )
    except DatabaseConfigurationError:
        return None


app = create_app(
    runtime_config=runtime_config_from_startup_environment(os.environ),
    conversation_store=_store_from_environment(),
    close_conversation_store=True,
    skill_registry=_skill_registry_from_environment(),
    close_skill_registry=True,
    tool_registry=_tool_registry,
    tool_provider=default_hospital_tool_provider(_environment),
    close_tool_registry=True,
    action_store=_action_store,
    close_action_store=True,
    run_audit_store=_run_audit_store,
    close_run_audit_store=True,
    run_metric_store=_run_metric_store,
    close_run_metric_store=True,
    management_token=os.getenv("MEDIPET_MANAGEMENT_TOKEN"),
    environment=_environment,
)
