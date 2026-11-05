"""FastAPI application for the agent service."""

import json
import os
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, StringConstraints, model_validator

from .config import LLMConfig, load_llm_config
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


def create_app(
    provider: StreamingModelProvider | None = None,
    *,
    system_prompt: str | None = None,
    config_path: str | Path | None = None,
    provider_factory: Callable[
        [LLMConfig], StreamingModelProvider
    ] = OpenAICompatibleProvider,
) -> FastAPI:
    resolved_provider = provider
    resolved_system_prompt = system_prompt

    if resolved_provider is None:
        resolved_config_path = config_path or os.getenv("AGENT_CONFIG_FILE")
        if resolved_config_path:
            config = load_llm_config(resolved_config_path)
            resolved_provider = provider_factory(config)
            resolved_system_prompt = config.system_prompt

    application = FastAPI(title="niub agent service")

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

    return application


app = create_app()
