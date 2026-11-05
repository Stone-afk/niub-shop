import json
from collections.abc import Callable

import httpx
import pytest

from agent_service.config import LLMConfig
from agent_service.providers import (
    ModelProtocolError,
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
