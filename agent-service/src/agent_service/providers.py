"""Vendor-neutral model provider contract and local implementation."""

import json
from collections.abc import AsyncIterator, Sequence
from typing import Literal, Protocol, TypedDict, runtime_checkable

import httpx

from .config import LLMConfig


@runtime_checkable
class ModelProvider(Protocol):
    def complete(self, prompt: str) -> str:
        ...


class EchoProvider:
    def complete(self, prompt: str) -> str:
        return prompt


class ModelMessage(TypedDict):
    role: Literal["system", "user", "assistant"]
    content: str


class ModelUpstreamError(RuntimeError):
    """Raised before downstream streaming when the model service is unavailable."""


class ModelProtocolError(RuntimeError):
    """Raised when an established model stream violates the expected protocol."""


class ModelStream(Protocol):
    def __aiter__(self) -> AsyncIterator[str]:
        ...

    async def aclose(self) -> None:
        ...


class StreamingModelProvider(Protocol):
    async def open_stream(self, messages: Sequence[ModelMessage]) -> ModelStream:
        ...


class _OpenAIModelStream:
    def __init__(self, client: httpx.AsyncClient, response: httpx.Response) -> None:
        self._client = client
        self._response = response
        self._closed = False

    def __aiter__(self) -> AsyncIterator[str]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[str]:
        try:
            async for line in self._response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data:
                    continue
                if data == "[DONE]":
                    return
                content = self._content_from_data(data)
                if content:
                    yield content
        finally:
            await self.aclose()
        raise ModelProtocolError("Model stream ended unexpectedly")

    @staticmethod
    def _content_from_data(data: str) -> str:
        try:
            payload = json.loads(data)
            choices = payload["choices"]
            if not isinstance(choices, list) or not choices:
                raise TypeError
            choice = choices[0]
            if not isinstance(choice, dict):
                raise TypeError
            delta = choice["delta"]
            if not isinstance(delta, dict):
                raise TypeError
            content = delta.get("content")
            if content is None:
                return ""
            if not isinstance(content, str):
                raise TypeError
            return content
        except (json.JSONDecodeError, KeyError, TypeError) as error:
            raise ModelProtocolError("Model stream contained invalid data") from error

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._response.aclose()
        await self._client.aclose()


class OpenAICompatibleProvider:
    def __init__(
        self,
        config: LLMConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._config = config
        self._transport = transport

    async def open_stream(self, messages: Sequence[ModelMessage]) -> ModelStream:
        client = httpx.AsyncClient(
            timeout=self._config.timeout_seconds,
            transport=self._transport,
        )
        request = client.build_request(
            "POST",
            f"{self._config.base_url}/chat/completions",
            headers={
                "authorization": f"Bearer {self._config.api_key}",
                "content-type": "application/json",
            },
            json={
                "model": self._config.model,
                "messages": list(messages),
                "stream": True,
            },
        )
        try:
            response = await client.send(request, stream=True)
        except httpx.HTTPError as error:
            await client.aclose()
            raise ModelUpstreamError("Model service is unavailable") from error

        if not response.is_success:
            status_code = response.status_code
            await response.aclose()
            await client.aclose()
            raise ModelUpstreamError(
                f"Model service rejected the request with status {status_code}"
            )

        if "text/event-stream" not in response.headers.get("content-type", ""):
            await response.aclose()
            await client.aclose()
            raise ModelUpstreamError("Model service returned an invalid content type")

        return _OpenAIModelStream(client, response)
