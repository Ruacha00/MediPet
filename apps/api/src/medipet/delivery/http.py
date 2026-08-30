from __future__ import annotations

import os
from collections.abc import Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from medipet.agent.capabilities import CapabilityProvider
from medipet.agent.runtime import LangGraphAgentRuntime
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
    TurnCommand,
    UIMessagePart,
)
from medipet.delivery.streaming import to_ui_message_stream
from medipet.model.openai import ChatOpenAIModelAdapter
from medipet.model.port import ModelPort
from medipet.persistence.conversation import (
    VisitConversationStore,
    VisitMatterNotFoundError,
)
from medipet.persistence.postgres import (
    DatabaseConfigurationError,
    PostgresVisitConversationStore,
)

MODEL_UNAVAILABLE_MESSAGE = "模型服务配置不可用"
DATABASE_UNAVAILABLE_MESSAGE = "数据库服务配置不可用"


def create_app(
    *,
    model: ModelPort | None = None,
    conversation_store: VisitConversationStore | None = None,
    close_conversation_store: bool = False,
    runtime_config: RuntimeConfig | None = None,
    model_factory: Callable[[ModelSettings], ModelPort] = ChatOpenAIModelAdapter,
    capability_provider: CapabilityProvider | None = None,
) -> FastAPI:
    if model is not None and runtime_config is not None:
        raise ValueError("model and runtime_config cannot both be provided")

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        if close_conversation_store and isinstance(
            conversation_store,
            PostgresVisitConversationStore,
        ):
            await conversation_store.close()

    app = FastAPI(title="MediPet", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[os.getenv("MEDIPET_WEB_ORIGIN", "http://localhost:3000")],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    static_assistant = (
        MediPetAssistant(
            LangGraphAgentRuntime(model, capability_provider=capability_provider),
            conversation_store,
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
                capability_provider=capability_provider,
                max_steps=settings.max_steps,
                profile_version=snapshot.fingerprint,
            ),
            conversation_store,
            context_message_limit=settings.context_message_limit,
            turn_timeout_seconds=settings.turn_timeout_seconds,
        )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

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
        )
        try:
            await conversation_store.validate_visit_participant(
                request.visit_matter_id,
                request.participant_id,
            )
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
        return ConversationHistoryResponse(
            visit_matter_id=visit_matter_id,
            messages=[
                ConversationHistoryMessage(
                    id=message.id,
                    role="user" if message.role == "participant" else "assistant",
                    state=message.state,
                    parts=(
                        [UIMessagePart(type="text", text=message.content)]
                        if message.content
                        else []
                    ),
                    created_at=message.created_at,
                    updated_at=message.updated_at,
                )
                for message in messages
            ],
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

    return app


def _store_from_environment() -> PostgresVisitConversationStore | None:
    try:
        return PostgresVisitConversationStore.from_url(os.getenv("MEDIPET_DATABASE_URL", ""))
    except DatabaseConfigurationError:
        return None


app = create_app(
    runtime_config=runtime_config_from_startup_environment(os.environ),
    conversation_store=_store_from_environment(),
    close_conversation_store=True,
)
