"""Async orchestration for persisted conversation streams."""

from __future__ import annotations

from collections.abc import AsyncIterator
from functools import partial

import anyio

from .conversations import (
    Conversation,
    ConversationRepository,
    ConversationSummary,
    Turn,
)
from .providers import (
    ModelMessage,
    ModelProtocolError,
    ModelStream,
    ModelUpstreamError,
    StreamingModelProvider,
)


LOCAL_USER_ID = 1


def resolve_current_user_id() -> int:
    return LOCAL_USER_ID


class PersistedTurnStream:
    def __init__(
        self,
        repository: ConversationRepository,
        upstream: ModelStream,
        user_id: int,
        conversation_id: str,
        turn: Turn,
    ) -> None:
        self.repository = repository
        self.upstream = upstream
        self.user_id = user_id
        self.conversation_id = conversation_id
        self.turn = turn

    def __aiter__(self) -> AsyncIterator[str]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[str]:
        chunks: list[str] = []
        completed = False
        try:
            async for chunk in self.upstream:
                chunks.append(chunk)
                yield chunk
            answer = "".join(chunks)
            if not answer:
                raise ModelProtocolError("Model stream returned no content")
            await anyio.to_thread.run_sync(
                partial(
                    self.repository.complete_turn,
                    self.user_id,
                    self.conversation_id,
                    self.turn.id,
                    answer,
                )
            )
            completed = True
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
            raise
        finally:
            with anyio.CancelScope(shield=True):
                try:
                    await self.upstream.aclose()
                except BaseException:
                    pass


class ConversationService:
    def __init__(
        self,
        repository: ConversationRepository,
        provider: StreamingModelProvider,
        system_prompt: str | None,
    ) -> None:
        self.repository = repository
        self.provider = provider
        self.system_prompt = system_prompt

    async def list_conversations(self, user_id: int) -> list[ConversationSummary]:
        return await anyio.to_thread.run_sync(
            self.repository.list_conversations, user_id
        )

    async def create_conversation(self, user_id: int) -> ConversationSummary:
        return await anyio.to_thread.run_sync(
            self.repository.create_conversation, user_id
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
    ) -> PersistedTurnStream:
        turn = await anyio.to_thread.run_sync(
            self.repository.create_turn, user_id, conversation_id, content
        )
        return await self._open(user_id, conversation_id, turn)

    async def retry_turn(
        self, user_id: int, conversation_id: str, turn_id: str
    ) -> PersistedTurnStream:
        turn = await anyio.to_thread.run_sync(
            self.repository.prepare_retry, user_id, conversation_id, turn_id
        )
        return await self._open(user_id, conversation_id, turn)

    async def _open(
        self, user_id: int, conversation_id: str, turn: Turn
    ) -> PersistedTurnStream:
        stored_messages = await anyio.to_thread.run_sync(
            self.repository.model_messages,
            user_id,
            conversation_id,
            turn.id,
        )
        messages: list[ModelMessage] = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        messages.extend(stored_messages)
        try:
            upstream = await self.provider.open_stream(messages)
        except BaseException:
            with anyio.CancelScope(shield=True):
                await anyio.to_thread.run_sync(
                    partial(
                        self.repository.fail_turn,
                        user_id,
                        conversation_id,
                        turn.id,
                        "model_service_unavailable",
                    ),
                    abandon_on_cancel=False,
                )
            raise
        return PersistedTurnStream(
            self.repository,
            upstream,
            user_id,
            conversation_id,
            turn,
        )
