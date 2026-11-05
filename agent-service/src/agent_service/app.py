"""FastAPI application for the agent service."""

import json
from typing import Annotated
from typing import Literal

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, StringConstraints

from .mall_client import MallClient
from .providers import ModelProvider


FIXED_REPLY_CHUNKS = (
    "你好！我是牛布商城智能助手。",
    " / Hi! I’m the Niub Shop assistant.",
    "\n这是一个固定的 SSE 流式回复演示。",
    " / This is a fixed SSE streaming demo.",
)


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ChatRequest(BaseModel):
    messages: Annotated[list[ChatMessage], Field(min_length=1)]


async def fixed_reply_events():
    for chunk in FIXED_REPLY_CHUNKS:
        payload = json.dumps({"content": chunk}, ensure_ascii=False)
        yield f"event: delta\ndata: {payload}\n\n"
    yield "event: done\ndata: {}\n\n"


def create_app(
    mall_client: MallClient | None = None,
    provider: ModelProvider | None = None,
) -> FastAPI:
    application = FastAPI(title="niub agent service")

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.post("/chat")
    def chat(request: ChatRequest) -> StreamingResponse:
        return StreamingResponse(fixed_reply_events(), media_type="text/event-stream")

    return application


app = create_app()
