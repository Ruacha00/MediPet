from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from medipet.agent.runtime import ModelAgentRuntime
from medipet.assistant import MediPetAssistant
from medipet.config import ModelConfigurationError, ModelSettings
from medipet.contracts import (
    ActionDecisionRequest,
    ActionDecisionResponse,
    ChatTurnRequest,
    ConfirmationDecision,
    TurnCommand,
)
from medipet.delivery.streaming import to_ui_message_stream
from medipet.model.openai import ChatOpenAIModelAdapter
from medipet.model.port import ModelPort

MODEL_UNAVAILABLE_MESSAGE = "模型服务配置不可用"


def create_app(*, model: ModelPort | None = None) -> FastAPI:
    app = FastAPI(title="MediPet", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[os.getenv("MEDIPET_WEB_ORIGIN", "http://localhost:3000")],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    assistant = MediPetAssistant(ModelAgentRuntime(model)) if model is not None else None

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready")
    async def ready() -> dict[str, str]:
        if assistant is None:
            raise HTTPException(status_code=503, detail=MODEL_UNAVAILABLE_MESSAGE)
        return {"status": "ready"}

    @app.post("/v1/chat/turns")
    async def chat_turn(request: ChatTurnRequest) -> StreamingResponse:
        if assistant is None:
            raise HTTPException(status_code=503, detail=MODEL_UNAVAILABLE_MESSAGE)
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
        return StreamingResponse(
            to_ui_message_stream(assistant.handle_turn(command)),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "x-vercel-ai-ui-message-stream": "v1",
            },
        )

    @app.post(
        "/v1/action-proposals/{proposal_id}/decision",
        response_model=ActionDecisionResponse,
    )
    async def decide_action(
        proposal_id: str,
        request: ActionDecisionRequest,
    ) -> ActionDecisionResponse:
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


def _model_from_environment() -> ModelPort | None:
    try:
        settings = ModelSettings.from_environment(os.environ)
    except ModelConfigurationError:
        return None
    return ChatOpenAIModelAdapter(settings)


app = create_app(model=_model_from_environment())
