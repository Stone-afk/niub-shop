"""FastAPI application for the agent service."""

import asyncio
import json
import time
from collections.abc import Callable
from typing import Annotated, Literal

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, StringConstraints, model_validator

from .mall_client import MallClient
from .providers import ModelProvider


DEMO_CHUNK_DELAY_SECONDS = 0.03


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


async def simulated_reply_events(user_input: str, timestamp: int):
    chunks = (
        "你输入的是 ",
        user_input,
        "\n当前时间戳：",
        str(timestamp),
    )
    for index, chunk in enumerate(chunks):
        payload = json.dumps({"content": chunk}, ensure_ascii=False)
        yield f"event: delta\ndata: {payload}\n\n"
        if index < len(chunks) - 1:
            await asyncio.sleep(DEMO_CHUNK_DELAY_SECONDS)
    yield "event: done\ndata: {}\n\n"


def create_app(
    mall_client: MallClient | None = None,
    provider: ModelProvider | None = None,
    clock: Callable[[], int] | None = None,
) -> FastAPI:
    application = FastAPI(title="niub agent service")
    resolved_clock = clock or (lambda: time.time_ns() // 1_000_000)

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.post("/chat")
    def chat(request: ChatRequest) -> StreamingResponse:
        user_input = next(
            message.content
            for message in reversed(request.messages)
            if message.role == "user"
        )
        return StreamingResponse(
            simulated_reply_events(user_input, resolved_clock()),
            media_type="text/event-stream",
        )

    return application


app = create_app()
