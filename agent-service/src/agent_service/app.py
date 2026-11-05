"""FastAPI application for the agent service."""

import json
import os
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, StringConstraints, model_validator

from .config import LLMConfig, load_llm_config
from .conversation_service import (
    LOCAL_USER_ID,
    ConversationService,
    PersistedTurnStream,
    resolve_current_user_id,
)
from .conversations import (
    ConversationBusy,
    ConversationNotFound,
    ConversationRepository,
    TurnNotRetryable,
)
from .providers import (
    ModelMessage,
    ModelProtocolError,
    ModelStream,
    ModelUpstreamError,
    OpenAICompatibleProvider,
    StreamingModelProvider,
)


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ChatRequest(BaseModel):
    messages: Annotated[list[ChatMessage], Field(min_length=1)]

    @model_validator(mode="after")
    def require_user_message(self) -> "ChatRequest":
        if not any(message.role == "user" for message in self.messages):
            raise ValueError("at least one user message is required")
        return self


class TurnRequest(BaseModel):
    content: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class _UnconfiguredProvider:
    async def open_stream(self, messages: list[ModelMessage]) -> ModelStream:
        raise ModelUpstreamError("Model service is not configured")


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


async def _model_events(stream: ModelStream) -> AsyncIterator[str]:
    try:
        async for content in stream:
            payload = json.dumps({"content": content}, ensure_ascii=False)
            yield f"event: delta\ndata: {payload}\n\n"
    except ModelProtocolError:
        return
    finally:
        await stream.aclose()
    yield "event: done\ndata: {}\n\n"


async def _persisted_model_events(
    stream: PersistedTurnStream,
) -> AsyncIterator[str]:
    iterator = stream.__aiter__()
    try:
        async for content in iterator:
            payload = json.dumps({"content": content}, ensure_ascii=False)
            yield f"event: delta\ndata: {payload}\n\n"
    except Exception:
        payload = json.dumps(
            {
                "code": "model_stream_failed",
                "message": "Model stream failed",
            },
            ensure_ascii=False,
        )
        yield f"event: error\ndata: {payload}\n\n"
        return
    else:
        yield "event: done\ndata: {}\n\n"
    finally:
        await iterator.aclose()


def create_app(
    provider: StreamingModelProvider | None = None,
    *,
    system_prompt: str | None = None,
    config_path: str | Path | None = None,
    provider_factory: Callable[
        [LLMConfig], StreamingModelProvider
    ] = OpenAICompatibleProvider,
    repository: ConversationRepository | None = None,
) -> FastAPI:
    resolved_provider = provider
    resolved_system_prompt = system_prompt

    if resolved_provider is None:
        resolved_config_path = config_path or os.getenv("AGENT_CONFIG_FILE")
        if resolved_config_path:
            config = load_llm_config(resolved_config_path)
            resolved_provider = provider_factory(config)
            resolved_system_prompt = config.system_prompt

    resolved_repository = repository or ConversationRepository(
        Path(os.getenv("AGENT_DB_FILE", "data/agent.db"))
    )
    service = ConversationService(
        resolved_repository,
        resolved_provider or _UnconfiguredProvider(),
        resolved_system_prompt,
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        resolved_repository.initialize()
        await service.recover_pending(LOCAL_USER_ID)
        yield

    application = FastAPI(title="niub agent service", lifespan=lifespan)

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.post("/chat")
    async def chat(request: ChatRequest):
        if resolved_provider is None:
            return _error_response(
                503,
                "model_not_configured",
                "Model service is not configured",
            )

        messages: list[ModelMessage] = []
        if resolved_system_prompt:
            messages.append(
                {"role": "system", "content": resolved_system_prompt}
            )
        messages.extend(
            {"role": message.role, "content": message.content}
            for message in request.messages
        )

        try:
            stream = await resolved_provider.open_stream(messages)
        except ModelUpstreamError:
            return _error_response(
                502,
                "model_service_unavailable",
                "Model service is unavailable",
            )

        return StreamingResponse(
            _model_events(stream),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

    @application.get("/conversations")
    async def list_conversations():
        summaries = await service.list_conversations(resolve_current_user_id())
        return [
            {
                "id": item.id,
                "title": item.title,
                "created_at": item.created_at,
                "updated_at": item.updated_at,
                "last_turn_status": item.last_turn_status,
            }
            for item in summaries
        ]

    @application.post("/conversations", status_code=201)
    async def create_conversation():
        item = await service.create_conversation(resolve_current_user_id())
        return {
            "id": item.id,
            "title": item.title,
            "created_at": item.created_at,
            "updated_at": item.updated_at,
            "last_turn_status": item.last_turn_status,
        }

    @application.get("/conversations/{conversation_id}")
    async def get_conversation(conversation_id: str):
        try:
            item = await service.get_conversation(
                resolve_current_user_id(), conversation_id
            )
        except ConversationNotFound:
            return _error_response(
                404, "conversation_not_found", "Conversation was not found"
            )
        return {
            "id": item.id,
            "title": item.title,
            "created_at": item.created_at,
            "updated_at": item.updated_at,
            "turns": [
                {
                    "id": turn.id,
                    "user_content": turn.user_content,
                    "assistant_content": turn.assistant_content,
                    "status": turn.status,
                    "error_code": turn.error_code,
                    "created_at": turn.created_at,
                    "updated_at": turn.updated_at,
                }
                for turn in item.turns
            ],
        }

    async def stream_result(
        operation: Callable[[], Awaitable[PersistedTurnStream]],
    ) -> StreamingResponse | JSONResponse:
        if resolved_provider is None:
            return _error_response(
                503,
                "model_not_configured",
                "Model service is not configured",
            )
        try:
            stream = await operation()
        except ConversationNotFound:
            return _error_response(
                404, "conversation_not_found", "Conversation was not found"
            )
        except ConversationBusy:
            return _error_response(
                409, "conversation_busy", "Conversation already has an active turn"
            )
        except TurnNotRetryable:
            return _error_response(
                409, "turn_not_retryable", "Turn cannot be retried"
            )
        except ModelUpstreamError:
            return _error_response(
                502,
                "model_service_unavailable",
                "Model service is unavailable",
            )
        return StreamingResponse(
            _persisted_model_events(stream),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

    @application.post("/conversations/{conversation_id}/turns")
    async def create_turn(conversation_id: str, request: TurnRequest):
        return await stream_result(
            lambda: service.open_turn(
                resolve_current_user_id(), conversation_id, request.content
            )
        )

    @application.post(
        "/conversations/{conversation_id}/turns/{turn_id}/retry"
    )
    async def retry_turn(conversation_id: str, turn_id: str):
        return await stream_result(
            lambda: service.retry_turn(
                resolve_current_user_id(), conversation_id, turn_id
            )
        )

    return application


app = create_app()
