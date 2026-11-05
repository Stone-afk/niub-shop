from collections.abc import AsyncIterator, Callable, Sequence
from pathlib import Path

import httpx
import pytest

from agent_service.app import create_app
from agent_service.config import LLMConfig
from agent_service.conversations import ConversationRepository
from agent_service.providers import (
    ModelMessage,
    ModelProtocolError,
    ModelStream,
    ModelUpstreamError,
    StreamingModelProvider,
)


class FakeModelStream:
    def __init__(
        self,
        chunks: Sequence[str],
        *,
        fail_after_chunks: bool = False,
    ) -> None:
        self._chunks = chunks
        self._fail_after_chunks = fail_after_chunks
        self.closed = False

    def __aiter__(self) -> AsyncIterator[str]:
        return self._events()

    async def _events(self) -> AsyncIterator[str]:
        for chunk in self._chunks:
            yield chunk
        if self._fail_after_chunks:
            raise ModelProtocolError("secret upstream response")

    async def aclose(self) -> None:
        self.closed = True


class RecordingProvider:
    def __init__(
        self,
        chunks: Sequence[str] = ("你好", "！"),
        *,
        open_error: Exception | None = None,
        fail_after_chunks: bool = False,
    ) -> None:
        self.messages: list[list[ModelMessage]] = []
        self.open_error = open_error
        self.stream = FakeModelStream(
            chunks,
            fail_after_chunks=fail_after_chunks,
        )

    async def open_stream(self, messages: Sequence[ModelMessage]) -> ModelStream:
        self.messages.append(list(messages))
        if self.open_error is not None:
            raise self.open_error
        return self.stream


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def make_client(
    provider: StreamingModelProvider | None = None,
    *,
    system_prompt: str | None = None,
    config_path: str | Path | None = None,
    provider_factory: Callable[[LLMConfig], StreamingModelProvider] | None = None,
) -> httpx.AsyncClient:
    arguments: dict[str, object] = {
        "provider": provider,
        "system_prompt": system_prompt,
        "config_path": config_path,
    }
    if provider_factory is not None:
        arguments["provider_factory"] = provider_factory
    app = create_app(**arguments)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    )


def make_persistent_client(
    tmp_path: Path,
    provider: StreamingModelProvider | None = None,
    *,
    system_prompt: str | None = "system",
) -> tuple[httpx.AsyncClient, ConversationRepository]:
    repository = ConversationRepository(tmp_path / "agent.db")
    repository.initialize()
    app = create_app(
        provider,
        system_prompt=system_prompt,
        repository=repository,
    )
    return (
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ),
        repository,
    )


@pytest.mark.anyio
async def test_health_returns_ok_when_model_is_unconfigured() -> None:
    async with make_client() as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.anyio
async def test_chat_sends_system_prompt_and_complete_history_to_provider() -> None:
    provider = RecordingProvider(("第一段", "第二段"))
    async with make_client(provider, system_prompt="你是商城助手。") as client:
        response = await client.post(
            "/chat",
            json={
                "messages": [
                    {"role": "user", "content": "  第一轮  "},
                    {"role": "assistant", "content": "固定回复"},
                    {"role": "user", "content": "第二轮"},
                ]
            },
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.text == (
        'event: delta\ndata: {"content": "第一段"}\n\n'
        'event: delta\ndata: {"content": "第二段"}\n\n'
        "event: done\ndata: {}\n\n"
    )
    assert provider.messages == [
        [
            {"role": "system", "content": "你是商城助手。"},
            {"role": "user", "content": "第一轮"},
            {"role": "assistant", "content": "固定回复"},
            {"role": "user", "content": "第二轮"},
        ]
    ]
    assert provider.stream.closed is True


@pytest.mark.anyio
async def test_chat_omits_done_after_midstream_protocol_failure() -> None:
    provider = RecordingProvider(("partial",), fail_after_chunks=True)
    async with make_client(provider, system_prompt="system") as client:
        response = await client.post(
            "/chat",
            json={"messages": [{"role": "user", "content": "hello"}]},
        )

    assert response.status_code == 200
    assert response.text == 'event: delta\ndata: {"content": "partial"}\n\n'
    assert provider.stream.closed is True


@pytest.mark.anyio
async def test_chat_returns_503_when_model_is_unconfigured() -> None:
    async with make_client() as client:
        response = await client.post(
            "/chat",
            json={"messages": [{"role": "user", "content": "hello"}]},
        )

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "model_not_configured",
            "message": "Model service is not configured",
        }
    }


@pytest.mark.anyio
async def test_chat_builds_provider_from_yaml_config(tmp_path: Path) -> None:
    config_file = tmp_path / "llm.yaml"
    config_file.write_text(
        """\
llm:
  provider: openai-compatible
  base_url: https://model.example/v1
  api_key: local-test-key
  model: example-model
  timeout_seconds: 15
  system_prompt: YAML 中的系统提示
""",
        encoding="utf-8",
    )
    provider = RecordingProvider(("configured",))
    received_configs: list[LLMConfig] = []

    def factory(config: LLMConfig) -> StreamingModelProvider:
        received_configs.append(config)
        return provider

    async with make_client(config_path=config_file, provider_factory=factory) as client:
        response = await client.post(
            "/chat",
            json={"messages": [{"role": "user", "content": "hello"}]},
        )

    assert response.status_code == 200
    assert len(received_configs) == 1
    assert received_configs[0].model == "example-model"
    assert provider.messages[0][0] == {
        "role": "system",
        "content": "YAML 中的系统提示",
    }


@pytest.mark.anyio
async def test_chat_maps_open_failure_to_secret_free_502() -> None:
    exposed_secret = "must-not-appear"
    provider = RecordingProvider(
        open_error=ModelUpstreamError(f"upstream rejected {exposed_secret}")
    )
    async with make_client(provider, system_prompt="system") as client:
        response = await client.post(
            "/chat",
            json={"messages": [{"role": "user", "content": "hello"}]},
        )

    assert response.status_code == 502
    assert response.json() == {
        "error": {
            "code": "model_service_unavailable",
            "message": "Model service is unavailable",
        }
    }
    assert exposed_secret not in response.text


@pytest.mark.anyio
async def test_injected_provider_takes_precedence_over_config_path() -> None:
    provider = RecordingProvider(("injected",))
    async with make_client(
        provider,
        system_prompt="injected system",
        config_path="does-not-exist.yaml",
    ) as client:
        response = await client.post(
            "/chat",
            json={"messages": [{"role": "user", "content": "hello"}]},
        )

    assert response.status_code == 200
    assert provider.messages[0][0]["content"] == "injected system"


@pytest.mark.anyio
async def test_chat_rejects_history_without_user_message() -> None:
    async with make_client(RecordingProvider(), system_prompt="system") as client:
        response = await client.post(
            "/chat",
            json={"messages": [{"role": "assistant", "content": "only reply"}]},
        )

    assert response.status_code == 422


@pytest.mark.anyio
async def test_empty_messages_returns_422() -> None:
    async with make_client(RecordingProvider(), system_prompt="system") as client:
        response = await client.post("/chat", json={"messages": []})

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "messages"]
    assert response.json()["detail"][0]["type"] == "too_short"


@pytest.mark.anyio
async def test_whitespace_message_returns_422() -> None:
    async with make_client(RecordingProvider(), system_prompt="system") as client:
        response = await client.post(
            "/chat",
            json={"messages": [{"role": "user", "content": "   "}]},
        )

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == [
        "body",
        "messages",
        0,
        "content",
    ]
    assert response.json()["detail"][0]["type"] == "string_too_short"


@pytest.mark.anyio
async def test_unknown_role_returns_422() -> None:
    async with make_client(RecordingProvider(), system_prompt="system") as client:
        response = await client.post(
            "/chat",
            json={"messages": [{"role": "system", "content": "hello"}]},
        )

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "messages", 0, "role"]
    assert response.json()["detail"][0]["type"] == "literal_error"


@pytest.mark.anyio
async def test_conversation_list_create_and_detail_hide_user_id(tmp_path: Path) -> None:
    client, _ = make_persistent_client(tmp_path, RecordingProvider())
    async with client:
        assert (await client.get("/conversations")).json() == []
        created = await client.post("/conversations", json={"user_id": 999})
        listed = await client.get("/conversations")
        detail = await client.get(f"/conversations/{created.json()['id']}")

    assert created.status_code == 201
    assert "user_id" not in created.json()
    assert listed.json() == [created.json()]
    assert detail.status_code == 200
    assert detail.json()["turns"] == []
    assert "user_id" not in detail.json()


@pytest.mark.anyio
async def test_conversation_detail_maps_missing_and_other_user_to_same_404(
    tmp_path: Path,
) -> None:
    client, repository = make_persistent_client(tmp_path, RecordingProvider())
    foreign = repository.create_conversation(2)
    async with client:
        missing = await client.get("/conversations/missing")
        hidden = await client.get(f"/conversations/{foreign.id}")

    expected = {
        "error": {
            "code": "conversation_not_found",
            "message": "Conversation was not found",
        }
    }
    assert missing.status_code == hidden.status_code == 404
    assert missing.json() == hidden.json() == expected


@pytest.mark.anyio
async def test_send_turn_streams_and_persists_completed_answer(tmp_path: Path) -> None:
    provider = RecordingProvider(("你", "好"))
    client, repository = make_persistent_client(tmp_path, provider)
    conversation = repository.create_conversation(1)
    async with client:
        response = await client.post(
            f"/conversations/{conversation.id}/turns",
            json={"content": "  问题  ", "user_id": 2},
        )

    assert response.status_code == 200
    assert response.text == (
        'event: delta\ndata: {"content": "你"}\n\n'
        'event: delta\ndata: {"content": "好"}\n\n'
        "event: done\ndata: {}\n\n"
    )
    stored = repository.get_conversation(1, conversation.id).turns[0]
    assert stored.user_content == "问题"
    assert stored.assistant_content == "你好"


@pytest.mark.anyio
@pytest.mark.parametrize("content", ["", "   "])
async def test_send_turn_rejects_empty_content(tmp_path: Path, content: str) -> None:
    client, repository = make_persistent_client(tmp_path, RecordingProvider())
    conversation = repository.create_conversation(1)
    async with client:
        response = await client.post(
            f"/conversations/{conversation.id}/turns", json={"content": content}
        )

    assert response.status_code == 422
    assert repository.get_conversation(1, conversation.id).turns == ()


@pytest.mark.anyio
async def test_send_turn_maps_open_failure_to_safe_502(tmp_path: Path) -> None:
    provider = RecordingProvider(
        open_error=ModelUpstreamError("vendor key must-not-appear")
    )
    client, repository = make_persistent_client(tmp_path, provider)
    conversation = repository.create_conversation(1)
    async with client:
        response = await client.post(
            f"/conversations/{conversation.id}/turns", json={"content": "问题"}
        )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "model_service_unavailable"
    assert "must-not-appear" not in response.text
    assert repository.get_conversation(1, conversation.id).turns[0].status == "failed"


@pytest.mark.anyio
async def test_send_turn_emits_safe_error_without_done_on_midstream_failure(
    tmp_path: Path,
) -> None:
    provider = RecordingProvider(("partial",), fail_after_chunks=True)
    client, repository = make_persistent_client(tmp_path, provider)
    conversation = repository.create_conversation(1)
    async with client:
        response = await client.post(
            f"/conversations/{conversation.id}/turns", json={"content": "问题"}
        )

    assert response.status_code == 200
    assert 'event: delta\ndata: {"content": "partial"}\n\n' in response.text
    assert (
        'event: error\ndata: {"code": "model_stream_failed", '
        '"message": "Model stream failed"}\n\n'
    ) in response.text
    assert "event: done" not in response.text
    assert repository.get_conversation(1, conversation.id).turns[0].status == "failed"


@pytest.mark.anyio
async def test_send_turn_returns_409_when_conversation_is_busy(tmp_path: Path) -> None:
    client, repository = make_persistent_client(tmp_path, RecordingProvider())
    conversation = repository.create_conversation(1)
    repository.create_turn(1, conversation.id, "进行中")
    async with client:
        response = await client.post(
            f"/conversations/{conversation.id}/turns", json={"content": "新问题"}
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conversation_busy"


@pytest.mark.anyio
async def test_retry_streams_on_same_failed_turn(tmp_path: Path) -> None:
    client, repository = make_persistent_client(
        tmp_path, RecordingProvider(("重试成功",))
    )
    conversation = repository.create_conversation(1)
    turn = repository.create_turn(1, conversation.id, "原问题")
    repository.fail_turn(1, conversation.id, turn.id, "failed")
    async with client:
        response = await client.post(
            f"/conversations/{conversation.id}/turns/{turn.id}/retry"
        )

    assert response.status_code == 200
    assert "event: done" in response.text
    turns = repository.get_conversation(1, conversation.id).turns
    assert len(turns) == 1
    assert turns[0].id == turn.id
    assert turns[0].status == "completed"
