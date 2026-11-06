"""FastAPI application for the agent service."""

import json
import os
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Annotated, Literal

import anyio
from fastapi import FastAPI, Request
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
from .mall_client import MallClient, MallServiceUnavailable
from .product_tools import MallProductSearchTool, ProductSearchTool
from .prompt_loader import PromptLoadError, load_prompt
from .recommendation_agent import (
    RecommendationAgent,
    RecommendationInvalid,
    ToolArgumentsInvalid,
    ToolLoopExceeded,
    UnknownTool,
    create_recommendation_agent,
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


class _UnconfiguredRecommendationAgent:
    async def generate(self, messages: list[ModelMessage]):
        raise ModelUpstreamError("Model service is not configured")


class _ClosingStreamingResponse(StreamingResponse):
    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            close = getattr(self.body_iterator, "aclose", None)
            if close is not None:
                with anyio.CancelScope(shield=True):
                    await close()


async def _run_until_disconnect(
    request: Request,
    operation: Callable[[], Awaitable[PersistedTurnStream]],
) -> tuple[bool, PersistedTurnStream | None]:
    stream: PersistedTurnStream | None = None
    operation_error: BaseException | None = None
    disconnected = False

    async with anyio.create_task_group() as task_group:
        async def run_operation() -> None:
            nonlocal stream, operation_error
            try:
                stream = await operation()
            except BaseException as error:
                operation_error = error
            finally:
                task_group.cancel_scope.cancel()

        async def watch_disconnect() -> None:
            nonlocal disconnected
            while True:
                message = await request.receive()
                if message["type"] == "http.disconnect":
                    disconnected = True
                    task_group.cancel_scope.cancel()
                    return

        task_group.start_soon(run_operation)
        task_group.start_soon(watch_disconnect)

    if disconnected:
        if stream is not None:
            await stream.aclose()
        return True, None
    if operation_error is not None:
        raise operation_error
    return False, stream


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
        recommendation_payload = {
            "product_ids": list(stream.recommended_product_ids)
        }
        if stream.recommended_product_sources:
            recommendation_payload["product_sources"] = list(
                stream.recommended_product_sources
            )
        payload = json.dumps(recommendation_payload, ensure_ascii=False)
        yield f"event: recommendations\ndata: {payload}\n\n"
        yield "event: done\ndata: {}\n\n"
    finally:
        await iterator.aclose()
        close = getattr(stream, "aclose", None)
        if close is not None:
            await close()


def create_app(
    provider: StreamingModelProvider | None = None,
    *,
    system_prompt: str | None = None,
    config_path: str | Path | None = None,
    provider_factory: Callable[
        [LLMConfig], StreamingModelProvider
    ] = OpenAICompatibleProvider,
    repository: ConversationRepository | None = None,
    recommendation_agent=None,
    recommendation_agent_factory: Callable[[LLMConfig, ProductSearchTool], object]
    | None = None,
    product_tool_factory: Callable[[str], ProductSearchTool] | None = None,
) -> FastAPI:
    resolved_provider = provider
    resolved_system_prompt = system_prompt
    resolved_system_prompt_path: Path | None = None
    resolved_config: LLMConfig | None = None

    if resolved_provider is None:
        resolved_config_path = config_path or os.getenv("AGENT_CONFIG_FILE")
        if resolved_config_path:
            resolved_config = load_llm_config(resolved_config_path)
            resolved_provider = provider_factory(resolved_config)
            resolved_system_prompt_path = resolved_config.prompt_file

    resolved_recommendation_agent = recommendation_agent
    if resolved_recommendation_agent is None and resolved_config is not None:
        mall_service_url = os.getenv("MALL_SERVICE_URL", "http://localhost:8001")
        product_tool = (
            product_tool_factory(mall_service_url)
            if product_tool_factory is not None
            else MallProductSearchTool(
                MallClient(
                    mall_service_url,
                    timeout=resolved_config.tool_timeout_seconds,
                )
            )
        )
        if recommendation_agent_factory is not None:
            resolved_recommendation_agent = recommendation_agent_factory(
                resolved_config,
                product_tool,
            )
        else:
            resolved_recommendation_agent = create_recommendation_agent(
                resolved_config,
                product_tool,
            )
    persistent_model_configured = resolved_recommendation_agent is not None

    resolved_repository = repository or ConversationRepository(
        Path(os.getenv("AGENT_DB_FILE", "data/agent.db"))
    )
    service = ConversationService(
        resolved_repository,
        resolved_recommendation_agent or _UnconfiguredRecommendationAgent(),
        resolved_system_prompt,
        resolved_system_prompt_path,
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
        try:
            chat_system_prompt = (
                load_prompt(resolved_system_prompt_path)
                if resolved_system_prompt_path is not None
                else resolved_system_prompt
            )
        except PromptLoadError:
            return _error_response(
                503,
                "prompt_load_failed",
                "Prompt configuration error / 提示词配置错误",
            )
        if chat_system_prompt:
            messages.append({"role": "system", "content": chat_system_prompt})
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
                    "recommended_product_ids": list(
                        turn.recommended_product_ids
                    ),
                    "recommended_product_sources": list(
                        turn.recommended_product_sources
                    ),
                }
                for turn in item.turns
            ],
        }

    async def stream_result(
        operation: Callable[[], Awaitable[PersistedTurnStream]],
        request: Request,
    ) -> StreamingResponse | JSONResponse:
        if not persistent_model_configured:
            return _error_response(
                503,
                "model_not_configured",
                "Model service is not configured",
            )
        try:
            disconnected, stream = await _run_until_disconnect(request, operation)
            if disconnected:
                return _error_response(
                    499,
                    "client_disconnected",
                    "Client disconnected / 客户端已断开",
                )
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
        except ModelProtocolError:
            return _error_response(
                502,
                "model_stream_failed",
                "Model response failed / 模型响应失败",
            )
        except MallServiceUnavailable:
            return _error_response(
                502,
                "mall_service_unavailable",
                "Mall service is unavailable / 商城服务不可用",
            )
        except PromptLoadError:
            return _error_response(
                503,
                "prompt_load_failed",
                "Prompt configuration error / 提示词配置错误",
            )
        except ToolArgumentsInvalid:
            return _error_response(
                502,
                "tool_arguments_invalid",
                "Product search arguments are invalid / 商品搜索参数无效",
            )
        except ToolLoopExceeded:
            return _error_response(
                502,
                "tool_loop_exceeded",
                "Product search limit exceeded / 商品搜索次数超限",
            )
        except (RecommendationInvalid, UnknownTool):
            return _error_response(
                502,
                "recommendation_invalid",
                "Recommendation result is invalid / 推荐结果无效",
            )
        assert stream is not None
        return _ClosingStreamingResponse(
            _persisted_model_events(stream),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

    @application.post("/conversations/{conversation_id}/turns")
    async def create_turn(
        conversation_id: str,
        request: TurnRequest,
        http_request: Request,
    ):
        return await stream_result(
            lambda: service.open_turn(
                resolve_current_user_id(), conversation_id, request.content
            ),
            http_request,
        )

    @application.post(
        "/conversations/{conversation_id}/turns/{turn_id}/retry"
    )
    async def retry_turn(
        conversation_id: str,
        turn_id: str,
        http_request: Request,
    ):
        return await stream_result(
            lambda: service.retry_turn(
                resolve_current_user_id(), conversation_id, turn_id
            ),
            http_request,
        )

    return application


app = create_app()
