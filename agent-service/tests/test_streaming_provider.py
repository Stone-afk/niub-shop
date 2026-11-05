import json
from collections.abc import Callable

import anyio
import httpx
import pytest

from agent_service.config import LLMConfig
from agent_service.providers import (
    ModelProtocolError,
    ModelResponse,
    ModelToolCall,
    ModelUpstreamError,
    OpenAICompatibleProvider,
)


SECRET = "provider-test-secret"
MESSAGES = [
    {"role": "system", "content": "Be helpful."},
    {"role": "user", "content": "你好"},
]


class ChunkedStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


class TrackingTransport(httpx.MockTransport):
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        super().__init__(handler)
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True
        await super().aclose()


class BlockingTransport(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.started = anyio.Event()
        self.closed = False

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.started.set()
        await anyio.sleep_forever()
        raise AssertionError("unreachable")

    async def aclose(self) -> None:
        self.closed = True


class DelayedCloseStream(ChunkedStream):
    def __init__(self) -> None:
        super().__init__([b"data: [DONE]\n\n"])
        self.close_started = anyio.Event()
        self.allow_close = anyio.Event()

    async def aclose(self) -> None:
        self.close_started.set()
        await self.allow_close.wait()
        self.closed = True


class FailingCloseStream(ChunkedStream):
    async def aclose(self) -> None:
        raise RuntimeError("response close failed")


class ReadErrorStream(ChunkedStream):
    async def __aiter__(self):
        raise httpx.ReadError(f"read failed {SECRET}")
        yield b""  # pragma: no cover


class BlockingBodyStream(ChunkedStream):
    def __init__(self) -> None:
        super().__init__([])
        self.started = anyio.Event()

    async def __aiter__(self):
        self.started.set()
        await anyio.sleep_forever()
        yield b""  # pragma: no cover


def config() -> LLMConfig:
    return LLMConfig(
        provider="openai-compatible",
        base_url="https://model.example/v1",
        api_key=SECRET,
        model="example-chat",
        timeout_seconds=12.0,
        system_prompt="Be helpful.",
    )


def sse_response(stream: ChunkedStream) -> httpx.Response:
    return httpx.Response(
        200,
        headers={"content-type": "text/event-stream; charset=utf-8"},
        stream=stream,
    )


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_products",
            "description": "Search products",
            "parameters": {"type": "object", "additionalProperties": False},
        },
    }
]


@pytest.mark.anyio
async def test_complete_response_posts_tools_and_buffers_split_text() -> None:
    captured: dict[str, object] = {}
    upstream = (
        b'data: {"choices":[{"delta":{"content":"\xe4\xbd\xa0"}}]}\n\n'
        b'data: {"choices":[{"delta":{"content":"\xe5\xa5\xbd"}}]}\n\n'
        b"data: [DONE]\n\n"
    )
    stream = ChunkedStream([upstream[:17], upstream[17:61], upstream[61:]])

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        captured["authorization"] = request.headers.get("authorization")
        return sse_response(stream)

    transport = TrackingTransport(handler)
    result = await OpenAICompatibleProvider(
        config(), transport=transport
    ).complete_response(MESSAGES, tools=TOOLS, tool_choice="auto")

    assert result == ModelResponse(content="你好", tool_calls=())
    assert captured == {
        "authorization": f"Bearer {SECRET}",
        "body": {
            "model": "example-chat",
            "messages": MESSAGES,
            "stream": True,
            "tools": TOOLS,
            "tool_choice": "auto",
        },
    }
    assert stream.closed
    assert transport.closed


@pytest.mark.anyio
async def test_interleaved_tool_call_chunks_are_assembled_by_index() -> None:
    stream = ChunkedStream(
        [
            b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-0","type":"function","function":{"name":"search_products","arguments":"{\\"keyword\\":"}}]}}]}\n\n',
            b'data: {"choices":[{"delta":{"tool_calls":[{"index":1,"id":"call-1","type":"function","function":{"name":"search_products","arguments":"{\\"category\\":"}}]}}]}\n\n',
            b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\\"coffee\\"}"}}]}}]}\n\n',
            b'data: {"choices":[{"delta":{"content":"thinking","tool_calls":[{"index":1,"function":{"arguments":"\\"bags\\"}"}}]}}]}\n\n',
            b"data: [DONE]\n\n",
        ]
    )
    transport = TrackingTransport(lambda _request: sse_response(stream))

    result = await OpenAICompatibleProvider(
        config(), transport=transport
    ).complete_response(MESSAGES, tools=TOOLS)

    assert result == ModelResponse(
        content="thinking",
        tool_calls=(
            ModelToolCall(
                id="call-0",
                name="search_products",
                arguments='{"keyword":"coffee"}',
            ),
            ModelToolCall(
                id="call-1",
                name="search_products",
                arguments='{"category":"bags"}',
            ),
        ),
    )


@pytest.mark.parametrize(
    "events",
    [
        [b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"name":"search_products","arguments":"{}"}}]}}]}\n\n'],
        [b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"id","function":{"arguments":"{}"}}]}}]}\n\n'],
        [
            b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"a","function":{"name":"search_products","arguments":""}}]}}]}\n\n',
            b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"b","function":{"name":"search_products","arguments":"{}"}}]}}]}\n\n',
        ],
    ],
    ids=["missing-id", "missing-name", "conflicting-metadata"],
)
@pytest.mark.anyio
async def test_complete_response_rejects_invalid_tool_call_metadata(
    events: list[bytes],
) -> None:
    stream = ChunkedStream([*events, b"data: [DONE]\n\n"])
    transport = TrackingTransport(lambda _request: sse_response(stream))

    with pytest.raises(ModelProtocolError, match="invalid data") as caught:
        await OpenAICompatibleProvider(
            config(), transport=transport
        ).complete_response(MESSAGES, tools=TOOLS)

    assert SECRET not in str(caught.value)
    assert stream.closed
    assert transport.closed


@pytest.mark.anyio
async def test_complete_response_preserves_invalid_arguments_for_agent_validation() -> None:
    stream = ChunkedStream(
        [
            b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"id","function":{"name":"search_products","arguments":"not-json"}}]}}]}\n\n',
            b"data: [DONE]\n\n",
        ]
    )
    transport = TrackingTransport(lambda _request: sse_response(stream))

    result = await OpenAICompatibleProvider(
        config(), transport=transport
    ).complete_response(MESSAGES, tools=TOOLS)

    assert result.tool_calls[0].arguments == "not-json"


@pytest.mark.anyio
async def test_complete_response_rejects_premature_eof_and_closes() -> None:
    stream = ChunkedStream(
        [b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n']
    )
    transport = TrackingTransport(lambda _request: sse_response(stream))

    with pytest.raises(ModelProtocolError, match="ended unexpectedly"):
        await OpenAICompatibleProvider(
            config(), transport=transport
        ).complete_response(MESSAGES, tools=TOOLS)

    assert stream.closed
    assert transport.closed


@pytest.mark.anyio
async def test_complete_response_maps_invalid_json_and_read_error_safely() -> None:
    invalid_stream = ChunkedStream([b"data: not-json\n\n"])
    invalid_transport = TrackingTransport(
        lambda _request: sse_response(invalid_stream)
    )

    with pytest.raises(ModelProtocolError, match="invalid data"):
        await OpenAICompatibleProvider(
            config(), transport=invalid_transport
        ).complete_response(MESSAGES, tools=TOOLS)

    read_stream = ReadErrorStream([])
    read_transport = TrackingTransport(lambda _request: sse_response(read_stream))
    with pytest.raises(ModelProtocolError, match="stream failed") as caught:
        await OpenAICompatibleProvider(
            config(), transport=read_transport
        ).complete_response(MESSAGES, tools=TOOLS)

    assert SECRET not in str(caught.value)
    assert invalid_stream.closed and invalid_transport.closed
    assert read_stream.closed and read_transport.closed


@pytest.mark.anyio
async def test_complete_response_cancellation_closes_response_and_client() -> None:
    body_stream = BlockingBodyStream()
    transport = TrackingTransport(lambda _request: sse_response(body_stream))
    provider = OpenAICompatibleProvider(config(), transport=transport)

    async def complete() -> None:
        await provider.complete_response(MESSAGES, tools=TOOLS)

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(complete)
        await body_stream.started.wait()
        task_group.cancel_scope.cancel()

    assert body_stream.closed
    assert transport.closed


@pytest.mark.anyio
async def test_posts_openai_compatible_request_and_extracts_split_deltas() -> None:
    captured: dict[str, object] = {}
    upstream_body = (
        b'data: {"choices":[{"delta":{"role":"assistant","content":null}}]}\n\n'
        b'data: {"choices":[{"delta":{"content":"\xe4\xbd\xa0"}}]}\n\n'
        b'data: {"choices":[{"delta":{"content":"\xe5\xa5\xbd"}}]}\n\n'
        b"data: [DONE]\n\n"
    )
    body_stream = ChunkedStream(
        [upstream_body[:11], upstream_body[11:57], upstream_body[57:96], upstream_body[96:]]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        return sse_response(body_stream)

    transport = TrackingTransport(handler)
    provider = OpenAICompatibleProvider(config(), transport=transport)

    model_stream = await provider.open_stream(MESSAGES)
    chunks = [chunk async for chunk in model_stream]

    assert chunks == ["你", "好"]
    assert captured == {
        "url": "https://model.example/v1/chat/completions",
        "authorization": f"Bearer {SECRET}",
        "body": {
            "model": "example-chat",
            "messages": MESSAGES,
            "stream": True,
        },
    }
    assert body_stream.closed
    assert transport.closed


@pytest.mark.anyio
async def test_ignores_sse_comments_unknown_fields_and_empty_content() -> None:
    body_stream = ChunkedStream(
        [
            b": keepalive\n\n",
            b'data: {"id":"chunk","choices":[{"delta":{"content":""}}]}\n\n',
            b'data: {"choices":[{"delta":{"content":"answer"}}]}\n\n',
            b"data: [DONE]\n\n",
        ]
    )
    transport = TrackingTransport(lambda _request: sse_response(body_stream))

    stream = await OpenAICompatibleProvider(config(), transport=transport).open_stream(
        MESSAGES
    )

    assert [chunk async for chunk in stream] == ["answer"]


@pytest.mark.anyio
async def test_premature_eof_is_a_safe_protocol_error_and_closes_resources() -> None:
    body_stream = ChunkedStream(
        [b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n']
    )
    transport = TrackingTransport(lambda _request: sse_response(body_stream))
    stream = await OpenAICompatibleProvider(config(), transport=transport).open_stream(
        MESSAGES
    )

    with pytest.raises(ModelProtocolError, match="ended unexpectedly") as caught:
        [chunk async for chunk in stream]

    assert SECRET not in str(caught.value)
    assert body_stream.closed
    assert transport.closed


@pytest.mark.parametrize(
    "data",
    [
        "not-json",
        '{"choices":[]}',
        '{"choices":[{"delta":[]}]}',
        '{"choices":[{"delta":{"content":123}}]}',
    ],
)
@pytest.mark.anyio
async def test_invalid_stream_data_is_a_safe_protocol_error(data: str) -> None:
    body_stream = ChunkedStream([f"data: {data}\n\n".encode()])
    transport = TrackingTransport(lambda _request: sse_response(body_stream))
    stream = await OpenAICompatibleProvider(config(), transport=transport).open_stream(
        MESSAGES
    )

    with pytest.raises(ModelProtocolError, match="invalid data") as caught:
        [chunk async for chunk in stream]

    assert SECRET not in str(caught.value)
    assert body_stream.closed
    assert transport.closed


@pytest.mark.parametrize("status", [401, 429, 500])
@pytest.mark.anyio
async def test_non_success_status_is_a_safe_upstream_error(status: int) -> None:
    body_stream = ChunkedStream([f'{{"message":"{SECRET}"}}'.encode()])
    transport = TrackingTransport(
        lambda _request: httpx.Response(
            status,
            headers={"content-type": "application/json"},
            stream=body_stream,
        )
    )
    provider = OpenAICompatibleProvider(config(), transport=transport)

    with pytest.raises(ModelUpstreamError, match="rejected") as caught:
        await provider.open_stream(MESSAGES)

    assert SECRET not in str(caught.value)
    assert body_stream.closed
    assert transport.closed


@pytest.mark.anyio
async def test_successful_non_sse_response_is_rejected_and_closed() -> None:
    body_stream = ChunkedStream([b"{}"])
    transport = TrackingTransport(
        lambda _request: httpx.Response(
            200,
            headers={"content-type": "application/json"},
            stream=body_stream,
        )
    )

    with pytest.raises(ModelUpstreamError, match="invalid content type"):
        await OpenAICompatibleProvider(config(), transport=transport).open_stream(
            MESSAGES
        )

    assert body_stream.closed
    assert transport.closed


@pytest.mark.anyio
async def test_connection_failure_is_safe_and_closes_client() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"offline {SECRET}", request=request)

    transport = TrackingTransport(handler)

    with pytest.raises(ModelUpstreamError, match="unavailable") as caught:
        await OpenAICompatibleProvider(config(), transport=transport).open_stream(
            MESSAGES
        )

    assert SECRET not in str(caught.value)
    assert transport.closed


@pytest.mark.anyio
async def test_cancellation_while_opening_stream_closes_client() -> None:
    transport = BlockingTransport()
    provider = OpenAICompatibleProvider(config(), transport=transport)

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(provider.open_stream, MESSAGES)
        await transport.started.wait()
        task_group.cancel_scope.cancel()

    assert transport.closed


@pytest.mark.anyio
async def test_cancellation_during_close_still_releases_all_resources() -> None:
    body_stream = DelayedCloseStream()
    transport = TrackingTransport(lambda _request: sse_response(body_stream))
    stream = await OpenAICompatibleProvider(config(), transport=transport).open_stream(
        MESSAGES
    )

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(stream.aclose)
        await body_stream.close_started.wait()
        task_group.cancel_scope.cancel()
        body_stream.allow_close.set()

    await stream.aclose()
    assert body_stream.closed
    assert transport.closed


@pytest.mark.anyio
async def test_response_close_failure_still_closes_client() -> None:
    body_stream = FailingCloseStream([])
    transport = TrackingTransport(lambda _request: sse_response(body_stream))
    stream = await OpenAICompatibleProvider(config(), transport=transport).open_stream(
        MESSAGES
    )

    with pytest.raises(RuntimeError, match="response close failed"):
        await stream.aclose()

    assert transport.closed


@pytest.mark.anyio
async def test_explicit_close_releases_upstream_after_cancellation() -> None:
    body_stream = ChunkedStream(
        [
            b'data: {"choices":[{"delta":{"content":"first"}}]}\n\n',
            b'data: {"choices":[{"delta":{"content":"second"}}]}\n\n',
        ]
    )
    transport = TrackingTransport(lambda _request: sse_response(body_stream))
    stream = await OpenAICompatibleProvider(config(), transport=transport).open_stream(
        MESSAGES
    )
    iterator = stream.__aiter__()

    assert await anext(iterator) == "first"
    await stream.aclose()

    assert body_stream.closed
    assert transport.closed
