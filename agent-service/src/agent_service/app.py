"""FastAPI application for the agent service."""

import os
from typing import Annotated

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, StringConstraints

from .agent import Agent
from .mall_client import MallClient, MallServiceUnavailable
from .providers import EchoProvider, ModelProvider


class ChatRequest(BaseModel):
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ChatResponse(BaseModel):
    reply: str


def create_app(
    mall_client: MallClient | None = None,
    provider: ModelProvider | None = None,
) -> FastAPI:
    resolved_mall_client = mall_client or MallClient(
        os.getenv("MALL_SERVICE_URL", "http://localhost:8001")
    )
    agent = Agent(provider or EchoProvider())
    application = FastAPI(title="niub agent service")

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.post("/chat", response_model=ChatResponse)
    def chat(request: ChatRequest) -> ChatResponse | JSONResponse:
        try:
            products = resolved_mall_client.list_products()
        except MallServiceUnavailable:
            return JSONResponse(
                status_code=502,
                content={
                    "error": {
                        "code": "mall_service_unavailable",
                        "message": "Mall service is unavailable",
                    }
                },
            )
        return ChatResponse(reply=agent.run(request.message, products))

    return application


app = create_app()
