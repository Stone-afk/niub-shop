from collections.abc import AsyncIterator, Callable, Sequence
import json
from pathlib import Path

import anyio
import httpx
import pytest

from agent_service.app import _persisted_model_events, create_app
from agent_service.config import LLMConfig
from agent_service.conversation_service import ConversationService
from agent_service.conversations import ConversationRepository
from agent_service.providers import (
    ModelMessage,
    ModelProtocolError,
    ModelResponse,
    ModelStream,
    ModelUpstreamError,
    StreamingModelProvider,
)
from agent_service.recommendation_agent import (
    RecommendationInvalid,
    RecommendationResult,
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


class NetworkFailureStream:
    def __init__(self) -> None:
        self.closed = False

    def __aiter__(self) -> AsyncIterator[str]:
        return self._events()

    async def _events(self) -> AsyncIterator[str]:
        yield "partial"
        raise httpx.ReadError("secret network failure")

    async def aclose(self) -> None:
        self.closed = True


class FakeRecommendationAgent:
    def __init__(
        self,
        result: RecommendationResult = RecommendationResult("你好！", ()),
        *,
        error: BaseException | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.messages = []

    async def generate(self, messages):
        self.messages.append(list(messages))
        if self.error is not None:
            raise self.error
        return self.result


class ToolCapableProvider(RecordingProvider):
    async def complete_response(self, messages, *, tools, tool_choice="auto"):
        return ModelResponse("wired answer", ())


class UnusedProductTool:
    async def search(self, filters):
        raise AssertionError("plain conversation must not search products")


class BlockingRecommendationAgent:
    def __init__(self) -> None:
        self.started = anyio.Event()
        self.cancelled = False

    async def generate(self, messages):
        self.started.set()
        try:
            await anyio.sleep_forever()
        finally:
            self.cancelled = True


async def invoke_asgi_disconnect(
    application,
    path: str,
    payload: dict[str, object],
    *,
    disconnect_ready: anyio.Event,
    block_first_body: bool = False,
) -> list[dict[str, object]]:
    request_sent = False
    sent: list[dict[str, object]] = []

    async def receive():
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {
                "type": "http.request",
                "body": json.dumps(payload).encode(),
                "more_body": False,
            }
        await disconnect_ready.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)
        if (
            block_first_body
            and message["type"] == "http.response.body"
            and message.get("body")
        ):
            disconnect_ready.set()
            await anyio.sleep_forever()

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [(b"content-type", b"application/json")],
        "client": ("127.0.0.1", 12345),
        "server": ("test", 80),
    }
    with anyio.fail_after(2):
        await application(scope, receive, send)
    return sent


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def make_client(
    provider: StreamingModelProvider | None = None,
    *,
    system_prompt: str | None = None,
    config_path: str | Path | None = None,
    provider_factory: Callable[[LLMConfig], StreamingModelProvider] | None = None,
    recommendation_agent_factory: Callable[[LLMConfig, object], object] | None = None,
) -> httpx.AsyncClient:
    arguments: dict[str, object] = {
        "provider": provider,
        "system_prompt": system_prompt,
        "config_path": config_path,
    }
    if provider_factory is not None:
        arguments["provider_factory"] = provider_factory
    if recommendation_agent_factory is not None:
        arguments["recommendation_agent_factory"] = recommendation_agent_factory
    app = create_app(**arguments)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    )


def write_prompt_files(tmp_path: Path, system: str = "YAML 中的系统提示") -> None:
    (tmp_path / "system.md").write_text(system, encoding="utf-8")
    (tmp_path / "recommendation-policy.md").write_text(
        "推荐策略", encoding="utf-8"
    )


def make_persistent_client(
    tmp_path: Path,
    provider: StreamingModelProvider | None = None,
    *,
    system_prompt: str | None = "system",
    recommendation_agent: FakeRecommendationAgent | None = None,
) -> tuple[httpx.AsyncClient, ConversationRepository]:
    repository = ConversationRepository(tmp_path / "agent.db")
    repository.initialize()
    if recommendation_agent is None:
        error = provider.open_error if isinstance(provider, RecordingProvider) else None
        if isinstance(provider, RecordingProvider) and provider.stream._fail_after_chunks:
            error = ModelProtocolError("secret upstream response")
        answer = (
            "".join(provider.stream._chunks)
            if isinstance(provider, RecordingProvider)
            else "你好！"
        )
        recommendation_agent = FakeRecommendationAgent(
            RecommendationResult(answer, ()), error=error
        )
    app = create_app(
        provider,
        system_prompt=system_prompt,
        repository=repository,
        recommendation_agent=recommendation_agent,
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
  prompt_file: system.md
  recommendation_policy_prompt_file: recommendation-policy.md
""",
        encoding="utf-8",
    )
    write_prompt_files(tmp_path)
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
async def test_chat_returns_prompt_error_without_calling_model(
    tmp_path: Path,
) -> None:
    config_file = tmp_path / "llm.yaml"
    config_file.write_text(
        """\
llm:
  provider: openai-compatible
  base_url: https://model.example/v1
  api_key: local-test-key
  model: example-model
  timeout_seconds: 15
  prompt_file: missing.md
  recommendation_policy_prompt_file: recommendation-policy.md
""",
        encoding="utf-8",
    )
    write_prompt_files(tmp_path)
    provider = RecordingProvider(("must-not-run",))

    async with make_client(config_path=config_file, provider_factory=lambda _: provider) as client:
        response = await client.post(
            "/chat",
            json={"messages": [{"role": "user", "content": "hello"}]},
        )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "prompt_load_failed"
    assert provider.messages == []


@pytest.mark.anyio
async def test_yaml_config_builds_separate_sdk_recommendation_agent(
    tmp_path: Path,
) -> None:
    config_file = tmp_path / "llm.yaml"
    config_file.write_text(
        """\
llm:
  provider: openai-compatible
  base_url: https://model.example/v1
  api_key: local-test-key
  model: example-model
  timeout_seconds: 15
  prompt_file: system.md
  recommendation_policy_prompt_file: recommendation-policy.md
""",
        encoding="utf-8",
    )
    write_prompt_files(tmp_path)
    provider = RecordingProvider(("configured",))
    sdk_agent = FakeRecommendationAgent(RecommendationResult("SDK reply", ()))
    received: list[tuple[LLMConfig, object]] = []

    def provider_factory(config: LLMConfig) -> StreamingModelProvider:
        return provider

    def recommendation_factory(config: LLMConfig, product_tool: object) -> object:
        received.append((config, product_tool))
        return sdk_agent

    async with make_client(
        config_path=config_file,
        provider_factory=provider_factory,
        recommendation_agent_factory=recommendation_factory,
    ) as client:
        response = await client.post(
            "/chat",
            json={"messages": [{"role": "user", "content": "hello"}]},
        )

    assert response.status_code == 200
    assert len(received) == 1
    assert received[0][0].base_url == "https://model.example/v1"
    assert received[0][0].model == "example-model"
    assert received[0][0].timeout_seconds == 15
    assert received[0][0].api_key == "local-test-key"


@pytest.mark.anyio
async def test_create_app_wires_product_tool_from_mall_service_url_without_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = ConversationRepository(tmp_path / "agent.db")
    repository.initialize()
    conversation = repository.create_conversation(1)
    received_urls: list[str] = []

    def tool_factory(url: str):
        received_urls.append(url)
        return UnusedProductTool()

    config_file = tmp_path / "llm.yaml"
    config_file.write_text(
        """\
llm:
  provider: openai-compatible
  base_url: https://model.example/v1
  api_key: local-test-key
  model: example-model
  timeout_seconds: 15
  prompt_file: system.md
  recommendation_policy_prompt_file: recommendation-policy.md
""",
        encoding="utf-8",
    )
    write_prompt_files(tmp_path, "system")

    monkeypatch.setenv("MALL_SERVICE_URL", "http://mall.internal:8001")
    application = create_app(
        config_path=config_file,
        provider_factory=lambda _config: RecordingProvider(),
        repository=repository,
        recommendation_agent_factory=lambda _config, _tool: FakeRecommendationAgent(
            RecommendationResult("wired answer", ())
        ),
        product_tool_factory=tool_factory,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        response = await client.post(
            f"/conversations/{conversation.id}/turns", json={"content": "hello"}
        )

    assert received_urls == ["http://mall.internal:8001"]
    assert response.status_code == 200
    assert "wired answer" in response.text


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
        'event: delta\ndata: {"content": "你好"}\n\n'
        'event: recommendations\ndata: {"product_ids": []}\n\n'
        "event: done\ndata: {}\n\n"
    )
    stored = repository.get_conversation(1, conversation.id).turns[0]
    assert stored.user_content == "问题"
    assert stored.assistant_content == "你好"


@pytest.mark.anyio
async def test_send_turn_emits_and_exposes_persisted_recommendations(
    tmp_path: Path,
) -> None:
    agent = FakeRecommendationAgent(
        RecommendationResult("推荐杯子", ("ceramic-mug", "canvas-tote"))
    )
    client, repository = make_persistent_client(
        tmp_path, RecordingProvider(), recommendation_agent=agent
    )
    conversation = repository.create_conversation(1)
    async with client:
        response = await client.post(
            f"/conversations/{conversation.id}/turns", json={"content": "推荐商品"}
        )
        detail = await client.get(f"/conversations/{conversation.id}")

    assert response.text == (
        'event: delta\ndata: {"content": "推荐杯子"}\n\n'
        'event: recommendations\ndata: '
        '{"product_ids": ["ceramic-mug", "canvas-tote"]}\n\n'
        'event: done\ndata: {}\n\n'
    )
    assert detail.json()["turns"][0]["recommended_product_ids"] == [
        "ceramic-mug",
        "canvas-tote",
    ]


@pytest.mark.anyio
async def test_invalid_recommendation_generation_maps_to_safe_502(
    tmp_path: Path,
) -> None:
    agent = FakeRecommendationAgent(error=RecommendationInvalid("secret answer"))
    client, repository = make_persistent_client(
        tmp_path, RecordingProvider(), recommendation_agent=agent
    )
    conversation = repository.create_conversation(1)

    async with client:
        response = await client.post(
            f"/conversations/{conversation.id}/turns", json={"content": "问题"}
        )

    assert response.status_code == 502
    assert response.json() == {
        "error": {
            "code": "recommendation_invalid",
            "message": "Recommendation result is invalid / 推荐结果无效",
        }
    }
    assert "secret answer" not in response.text


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

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "model_stream_failed"
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


@pytest.mark.anyio
async def test_downstream_disconnect_closes_persisted_stream_and_fails_turn(
    tmp_path: Path,
) -> None:
    repository = ConversationRepository(tmp_path / "agent.db")
    repository.initialize()
    conversation = repository.create_conversation(1)
    agent = FakeRecommendationAgent(RecommendationResult("partial" + "x" * 80, ("one",)))
    stream = await ConversationService(repository, agent, None).open_turn(1, conversation.id, "问题")
    events = _persisted_model_events(stream)
    assert (await anext(events)).startswith('event: delta\ndata: {"content": "partial')

    await events.aclose()

    stored = repository.get_conversation(1, conversation.id).turns[0]
    assert stored.status == "failed"
    assert stored.assistant_content is None
    assert stored.recommended_product_ids == ()


@pytest.mark.anyio
async def test_network_stream_failure_emits_safe_error_event(tmp_path: Path) -> None:
    network_stream = NetworkFailureStream()
    network_stream.recommended_product_ids = ()

    events = [event async for event in _persisted_model_events(network_stream)]

    assert events == [
        'event: delta\ndata: {"content": "partial"}\n\n',
        'event: error\ndata: {"code": "model_stream_failed", '
        '"message": "Model stream failed"}\n\n',
    ]
    assert network_stream.closed is True


@pytest.mark.anyio
async def test_disconnect_during_buffered_generation_cancels_and_fails_turn(
    tmp_path: Path,
) -> None:
    repository = ConversationRepository(tmp_path / "agent.db")
    repository.initialize()
    conversation = repository.create_conversation(1)
    agent = BlockingRecommendationAgent()
    application = create_app(
        RecordingProvider(), repository=repository, recommendation_agent=agent
    )

    sent = await invoke_asgi_disconnect(
        application,
        f"/conversations/{conversation.id}/turns",
        {"content": "问题"},
        disconnect_ready=agent.started,
    )

    stored = repository.get_conversation(1, conversation.id).turns[0]
    assert agent.cancelled is True
    assert stored.status == "failed"
    assert stored.assistant_content is None
    assert stored.recommended_product_ids == ()
    assert sent[0]["status"] == 499


@pytest.mark.anyio
async def test_disconnect_while_sending_first_delta_closes_stream_and_fails_turn(
    tmp_path: Path,
) -> None:
    repository = ConversationRepository(tmp_path / "agent.db")
    repository.initialize()
    conversation = repository.create_conversation(1)
    application = create_app(
        RecordingProvider(),
        repository=repository,
        recommendation_agent=FakeRecommendationAgent(
            RecommendationResult("A" * 100, ("mug",))
        ),
    )
    first_body = anyio.Event()

    await invoke_asgi_disconnect(
        application,
        f"/conversations/{conversation.id}/turns",
        {"content": "问题"},
        disconnect_ready=first_body,
        block_first_body=True,
    )

    stored = repository.get_conversation(1, conversation.id).turns[0]
    assert stored.status == "failed"
    assert stored.assistant_content is None
    assert stored.recommended_product_ids == ()
