from collections.abc import AsyncIterator, Sequence
from pathlib import Path

import pytest

from agent_service.conversation_service import ConversationService
from agent_service.conversations import ConversationRepository
from agent_service.providers import (
    ModelMessage,
    ModelProtocolError,
    ModelStream,
    ModelUpstreamError,
)


class FakeStream:
    def __init__(
        self,
        chunks: Sequence[str],
        *,
        failure: BaseException | None = None,
    ) -> None:
        self.chunks = chunks
        self.failure = failure
        self.closed = False

    def __aiter__(self) -> AsyncIterator[str]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[str]:
        for chunk in self.chunks:
            yield chunk
        if self.failure:
            raise self.failure

    async def aclose(self) -> None:
        self.closed = True


class RecordingProvider:
    def __init__(
        self,
        stream: ModelStream,
        *,
        open_error: BaseException | None = None,
    ) -> None:
        self.stream = stream
        self.open_error = open_error
        self.messages: list[list[ModelMessage]] = []

    async def open_stream(self, messages: Sequence[ModelMessage]) -> ModelStream:
        self.messages.append(list(messages))
        if self.open_error:
            raise self.open_error
        return self.stream


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def make_repository(tmp_path: Path) -> ConversationRepository:
    repository = ConversationRepository(tmp_path / "agent.db")
    repository.initialize()
    return repository


@pytest.mark.anyio
async def test_builds_model_context_from_persisted_completed_turns(
    tmp_path: Path,
) -> None:
    repository = make_repository(tmp_path)
    conversation = repository.create_conversation(1)
    earlier = repository.create_turn(1, conversation.id, "第一问")
    repository.complete_turn(1, conversation.id, earlier.id, "第一答")
    provider = RecordingProvider(FakeStream(["第二答"]))
    service = ConversationService(repository, provider, "系统提示")

    stream = await service.open_turn(1, conversation.id, "第二问")
    assert [chunk async for chunk in stream] == ["第二答"]

    assert provider.messages == [[
        {"role": "system", "content": "系统提示"},
        {"role": "user", "content": "第一问"},
        {"role": "assistant", "content": "第一答"},
        {"role": "user", "content": "第二问"},
    ]]
    assert repository.get_conversation(1, conversation.id).turns[-1].status == "completed"


@pytest.mark.anyio
async def test_commits_assistant_only_after_upstream_completes(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    conversation = repository.create_conversation(1)
    provider = RecordingProvider(FakeStream(["A", "B"]))
    stream = await ConversationService(repository, provider, None).open_turn(
        1, conversation.id, "问题"
    )
    iterator = stream.__aiter__()

    assert await anext(iterator) == "A"
    pending = repository.get_conversation(1, conversation.id).turns[0]
    assert pending.status == "pending"
    assert pending.assistant_content is None
    assert await anext(iterator) == "B"
    with pytest.raises(StopAsyncIteration):
        await anext(iterator)

    completed = repository.get_conversation(1, conversation.id).turns[0]
    assert completed.status == "completed"
    assert completed.assistant_content == "AB"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("chunks", "failure"),
    [([], None), (["partial"], ModelProtocolError("vendor secret"))],
)
async def test_empty_or_failed_stream_discards_partial_answer(
    tmp_path: Path,
    chunks: list[str],
    failure: BaseException | None,
) -> None:
    repository = make_repository(tmp_path)
    conversation = repository.create_conversation(1)
    upstream = FakeStream(chunks, failure=failure)
    stream = await ConversationService(
        repository, RecordingProvider(upstream), None
    ).open_turn(1, conversation.id, "问题")

    with pytest.raises(ModelProtocolError):
        [chunk async for chunk in stream]

    stored = repository.get_conversation(1, conversation.id).turns[0]
    assert stored.status == "failed"
    assert stored.assistant_content is None
    assert stored.error_code == "model_stream_failed"
    assert upstream.closed is True


@pytest.mark.anyio
async def test_open_failure_marks_created_turn_failed(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    conversation = repository.create_conversation(1)
    provider = RecordingProvider(
        FakeStream([]), open_error=ModelUpstreamError("vendor secret")
    )

    with pytest.raises(ModelUpstreamError):
        await ConversationService(repository, provider, None).open_turn(
            1, conversation.id, "问题"
        )

    failed = repository.get_conversation(1, conversation.id).turns[0]
    assert failed.status == "failed"
    assert failed.error_code == "model_service_unavailable"


@pytest.mark.anyio
async def test_early_consumer_close_marks_turn_failed_and_closes_upstream(
    tmp_path: Path,
) -> None:
    repository = make_repository(tmp_path)
    conversation = repository.create_conversation(1)
    upstream = FakeStream(["partial", "unused"])
    stream = await ConversationService(
        repository, RecordingProvider(upstream), None
    ).open_turn(1, conversation.id, "问题")
    iterator = stream.__aiter__()
    assert await anext(iterator) == "partial"

    await iterator.aclose()

    failed = repository.get_conversation(1, conversation.id).turns[0]
    assert failed.status == "failed"
    assert failed.assistant_content is None
    assert upstream.closed is True


@pytest.mark.anyio
async def test_retry_reuses_failed_turn_and_original_user_content(
    tmp_path: Path,
) -> None:
    repository = make_repository(tmp_path)
    conversation = repository.create_conversation(1)
    failed = repository.create_turn(1, conversation.id, "原问题")
    repository.fail_turn(1, conversation.id, failed.id, "failed")
    provider = RecordingProvider(FakeStream(["重试回答"]))

    stream = await ConversationService(repository, provider, None).retry_turn(
        1, conversation.id, failed.id
    )
    assert [chunk async for chunk in stream] == ["重试回答"]

    turns = repository.get_conversation(1, conversation.id).turns
    assert len(turns) == 1
    assert turns[0].id == failed.id
    assert turns[0].assistant_content == "重试回答"
    assert provider.messages == [[{"role": "user", "content": "原问题"}]]
