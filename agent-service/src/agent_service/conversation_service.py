"""Async orchestration for persisted, buffered recommendation streams."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from functools import partial
from pathlib import Path
from typing import Protocol

import anyio

from .conversations import (
    Conversation,
    ConversationRepository,
    ConversationSummary,
    Turn,
)
from .mall_client import MallServiceUnavailable
from .providers import ModelMessage, ModelProtocolError, ModelUpstreamError
from .recommendation_agent import (
    RecommendationInvalid,
    RecommendationResult,
    ToolArgumentsInvalid,
    ToolLoopExceeded,
    UnknownTool,
)
from .prompt_loader import load_prompt
from .response_language import detect_language, language_instruction


LOCAL_USER_ID = 1
DEFAULT_SYSTEM_PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "system.md"


def resolve_current_user_id() -> int:
    return LOCAL_USER_ID


class RecommendationGenerator(Protocol):
    async def generate(
        self, messages: Sequence[ModelMessage]
    ) -> RecommendationResult: ...


class BufferedRecommendationStream:
    def __init__(
        self,
        repository: ConversationRepository,
        result: RecommendationResult,
        user_id: int,
        conversation_id: str,
        turn: Turn,
        *,
        chunk_size: int = 64,
    ) -> None:
        if chunk_size < 1:
            raise ValueError("chunk_size must be positive")
        self.repository = repository
        self.result = result
        self.user_id = user_id
        self.conversation_id = conversation_id
        self.turn = turn
        self.chunk_size = chunk_size
        self.recommended_product_ids = result.product_ids
        self.recommended_product_sources = result.product_sources
        self.orders = result.orders
        self.plan_state = result.plan_state
        self.plan_version = result.plan_version or 1
        self._finished = False

    def __aiter__(self) -> AsyncIterator[str]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[str]:
        completed = False
        try:
            for offset in range(0, len(self.result.answer), self.chunk_size):
                yield self.result.answer[offset : offset + self.chunk_size]
            await anyio.to_thread.run_sync(
                partial(
                    self.repository.complete_turn,
                    self.user_id,
                    self.conversation_id,
                    self.turn.id,
                    self.result.answer,
                    self.result.product_ids,
                    self.result.product_sources,
                    self.plan_state,
                    self.plan_version,
                    [
                        {
                            "order_id": order.order_id,
                            "preview_path": order.preview_path,
                            "status": order.status,
                        }
                        for order in self.result.orders
                    ],
                )
            )
            completed = True
            self._finished = True
        except BaseException:
            if not completed:
                with anyio.CancelScope(shield=True):
                    await anyio.to_thread.run_sync(
                        partial(
                            self.repository.fail_turn,
                            self.user_id,
                            self.conversation_id,
                            self.turn.id,
                            "model_stream_failed",
                        ),
                        abandon_on_cancel=False,
                    )
                self._finished = True
            raise

    async def aclose(self) -> None:
        """Fail an unopened or partially consumed turn during HTTP cleanup."""
        if self._finished:
            return
        with anyio.CancelScope(shield=True):
            await anyio.to_thread.run_sync(
                partial(
                    self.repository.fail_turn,
                    self.user_id,
                    self.conversation_id,
                    self.turn.id,
                    "model_stream_failed",
                ),
                abandon_on_cancel=False,
            )
        self._finished = True


PersistedTurnStream = BufferedRecommendationStream


class ConversationService:
    def __init__(
        self,
        repository: ConversationRepository,
        recommendation_agent: RecommendationGenerator,
        system_prompt: str | None,
        system_prompt_path: str | Path | None = None,
    ) -> None:
        self.repository = repository
        self.recommendation_agent = recommendation_agent
        self.system_prompt = system_prompt
        self.system_prompt_path = Path(system_prompt_path) if system_prompt_path else None

    async def list_conversations(self, user_id: int) -> list[ConversationSummary]:
        return await anyio.to_thread.run_sync(
            self.repository.list_conversations, user_id
        )

    async def create_conversation(self, user_id: int) -> ConversationSummary:
        return await anyio.to_thread.run_sync(
            self.repository.create_conversation, user_id
        )

    async def delete_conversation(self, user_id: int, conversation_id: str) -> None:
        await anyio.to_thread.run_sync(
            self.repository.delete_conversation, user_id, conversation_id
        )

    async def get_conversation(
        self, user_id: int, conversation_id: str
    ) -> Conversation:
        return await anyio.to_thread.run_sync(
            self.repository.get_conversation, user_id, conversation_id
        )

    async def recover_pending(self, user_id: int) -> int:
        return await anyio.to_thread.run_sync(
            self.repository.recover_pending, user_id
        )

    async def open_turn(
        self, user_id: int, conversation_id: str, content: str
    ) -> BufferedRecommendationStream:
        turn = await anyio.to_thread.run_sync(
            self.repository.create_turn, user_id, conversation_id, content
        )
        return await self._open(user_id, conversation_id, turn)

    async def retry_turn(
        self, user_id: int, conversation_id: str, turn_id: str
    ) -> BufferedRecommendationStream:
        turn = await anyio.to_thread.run_sync(
            self.repository.prepare_retry, user_id, conversation_id, turn_id
        )
        return await self._open(user_id, conversation_id, turn)

    async def _open(
        self, user_id: int, conversation_id: str, turn: Turn
    ) -> BufferedRecommendationStream:
        stored_messages = await anyio.to_thread.run_sync(
            self.repository.model_messages,
            user_id,
            conversation_id,
            turn.id,
        )
        try:
            messages: list[ModelMessage] = []
            previous_language = None
            for message in reversed(stored_messages[:-1]):
                if message["role"] == "user":
                    previous_language = detect_language(message["content"])
                    break
            language = detect_language(turn.user_content, previous_language)
            system_prompt = (
                load_prompt(self.system_prompt_path)
                if self.system_prompt_path is not None
                else self.system_prompt
            )
            if system_prompt:
                messages.append(
                    {
                        "role": "system",
                        "content": f"{system_prompt}\n\n{language_instruction(language)}",
                    }
                )
            else:
                messages.append({"role": "system", "content": language_instruction(language)})
            messages.extend(stored_messages)
            set_turn_context = getattr(self.recommendation_agent, "set_turn_context", None)
            if set_turn_context is not None:
                set_turn_context(conversation_id, turn.id)
            result = await self.recommendation_agent.generate(messages)
            self._validate_result(result)
        except BaseException as error:
            with anyio.CancelScope(shield=True):
                await anyio.to_thread.run_sync(
                    partial(
                        self.repository.fail_turn,
                        user_id,
                        conversation_id,
                        turn.id,
                        _generation_error_code(error),
                    ),
                    abandon_on_cancel=False,
                )
            raise
        return BufferedRecommendationStream(
            self.repository,
            result,
            user_id,
            conversation_id,
            turn,
        )

    @staticmethod
    def _validate_result(result: RecommendationResult) -> None:
        if (
            not isinstance(result.answer, str)
            or not result.answer
            or len(result.answer) > 20000
            or len(result.product_ids) > 3
            or len(set(result.product_ids)) != len(result.product_ids)
            or any(
                not isinstance(product_id, str) or not product_id
                for product_id in result.product_ids
            )
            or (
                result.product_sources
                and len(result.product_sources) != len(result.product_ids)
            )
            or any(
                source not in {"organic", "sponsored"}
                for source in result.product_sources
            )
            or result.product_sources.count("sponsored") > 1
            or result.product_sources.count("organic") > 2
            or (
                result.product_sources
                and result.product_sources[0] == "sponsored"
                and "organic" in result.product_sources
            )
        ):
            raise RecommendationInvalid("Recommendation response is invalid")


def _generation_error_code(error: BaseException) -> str:
    if isinstance(error, ModelUpstreamError):
        return "model_service_unavailable"
    if isinstance(error, ModelProtocolError):
        return "model_stream_failed"
    if isinstance(error, MallServiceUnavailable):
        return "mall_service_unavailable"
    if isinstance(error, ToolArgumentsInvalid):
        return "tool_arguments_invalid"
    if isinstance(error, ToolLoopExceeded):
        return "tool_loop_exceeded"
    if isinstance(error, (RecommendationInvalid, UnknownTool)):
        return "recommendation_invalid"
    return "model_stream_failed"
