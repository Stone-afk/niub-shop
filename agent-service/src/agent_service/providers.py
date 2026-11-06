"""Vendor-neutral model provider contract and local implementation."""

import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Literal, NotRequired, Protocol, Required, TypedDict, runtime_checkable

import anyio
import httpx

from .config import LLMConfig


@runtime_checkable
class ModelProvider(Protocol):
    def complete(self, prompt: str) -> str:
        ...


class EchoProvider:
    def complete(self, prompt: str) -> str:
        return prompt


class ModelFunctionCall(TypedDict):
    name: str
    arguments: str


class ModelAssistantToolCall(TypedDict):
    id: str
    type: Literal["function"]
    function: ModelFunctionCall


class ModelMessage(TypedDict, total=False):
    role: Required[Literal["system", "user", "assistant", "tool"]]
    content: NotRequired[str | None]
    tool_calls: NotRequired[list[ModelAssistantToolCall]]
    tool_call_id: NotRequired[str]


class ToolFunctionDefinition(TypedDict):
    name: str
    description: str
    parameters: dict[str, object]


class ToolDefinition(TypedDict):
    type: Literal["function"]
    function: ToolFunctionDefinition


ToolChoice = Literal["auto"] | dict[str, object]


@dataclass(frozen=True)
class ModelToolCall:
    id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class ModelResponse:
    content: str
    tool_calls: tuple[ModelToolCall, ...]


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


class ToolCallingModelProvider(Protocol):
    async def complete_response(
        self,
        messages: Sequence[ModelMessage],
        *,
        tools: Sequence[ToolDefinition],
        tool_choice: ToolChoice = "auto",
    ) -> ModelResponse: ...


async def _close_client_safely(client: httpx.AsyncClient) -> None:
    with anyio.CancelScope(shield=True):
        try:
            await client.aclose()
        except BaseException:
            pass


async def _close_response_and_client(
    response: httpx.Response,
    client: httpx.AsyncClient,
) -> None:
    with anyio.CancelScope(shield=True):
        try:
            await response.aclose()
        finally:
            await client.aclose()


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
        await _close_response_and_client(self._response, self._client)
        self._closed = True


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
        client, response = await self._open_response(messages)
        return _OpenAIModelStream(client, response)

    async def complete_response(
        self,
        messages: Sequence[ModelMessage],
        *,
        tools: Sequence[ToolDefinition],
        tool_choice: ToolChoice = "auto",
    ) -> ModelResponse:
        client, response = await self._open_response(
            messages,
            extra_body={"tools": list(tools), "tool_choice": tool_choice},
        )
        parser = _BufferedToolResponseParser()
        try:
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data:
                    continue
                if data == "[DONE]":
                    return parser.finish()
                parser.consume(data)
        except httpx.HTTPError as error:
            raise ModelProtocolError("Model stream failed") from error
        finally:
            await _close_response_and_client(response, client)
        raise ModelProtocolError("Model stream ended unexpectedly")

    async def _open_response(
        self,
        messages: Sequence[ModelMessage],
        *,
        extra_body: dict[str, object] | None = None,
    ) -> tuple[httpx.AsyncClient, httpx.Response]:
        client = httpx.AsyncClient(
            timeout=self._config.timeout_seconds,
            transport=self._transport,
        )
        try:
            body: dict[str, object] = {
                "model": self._config.model,
                "messages": list(messages),
                "stream": True,
            }
            if self._config.temperature is not None:
                body["temperature"] = self._config.temperature
            if self._config.max_tokens is not None:
                body["max_tokens"] = self._config.max_tokens
            if extra_body:
                body.update(extra_body)
            request = client.build_request(
                "POST",
                f"{self._config.base_url}/chat/completions",
                headers={
                    "authorization": f"Bearer {self._config.api_key}",
                    "content-type": "application/json",
                },
                json=body,
            )
            response = await client.send(request, stream=True)
        except httpx.HTTPError as error:
            await _close_client_safely(client)
            raise ModelUpstreamError("Model service is unavailable") from error
        except BaseException:
            await _close_client_safely(client)
            raise

        if not response.is_success:
            status_code = response.status_code
            try:
                await _close_response_and_client(response, client)
            except BaseException:
                pass
            raise ModelUpstreamError(
                f"Model service rejected the request with status {status_code}"
            )

        if "text/event-stream" not in response.headers.get("content-type", ""):
            try:
                await _close_response_and_client(response, client)
            except BaseException:
                pass
            raise ModelUpstreamError("Model service returned an invalid content type")

        return client, response


@dataclass
class _ToolCallBuilder:
    id: str | None = None
    name: str | None = None
    arguments: str = ""


class _BufferedToolResponseParser:
    def __init__(self) -> None:
        self._content: list[str] = []
        self._tool_calls: dict[int, _ToolCallBuilder] = {}

    def consume(self, data: str) -> None:
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
            if content is not None:
                if not isinstance(content, str):
                    raise TypeError
                self._content.append(content)
            raw_calls = delta.get("tool_calls", [])
            if not isinstance(raw_calls, list):
                raise TypeError
            for raw_call in raw_calls:
                self._consume_tool_call(raw_call)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            raise ModelProtocolError("Model stream contained invalid data") from error

    def _consume_tool_call(self, raw_call: object) -> None:
        if not isinstance(raw_call, dict):
            raise TypeError
        index = raw_call.get("index")
        if type(index) is not int or index < 0:
            raise TypeError
        builder = self._tool_calls.setdefault(index, _ToolCallBuilder())
        call_id = raw_call.get("id")
        if call_id is not None:
            if not isinstance(call_id, str) or not call_id:
                raise TypeError
            if builder.id is not None and builder.id != call_id:
                raise ValueError
            builder.id = call_id
        function = raw_call.get("function")
        if not isinstance(function, dict):
            raise TypeError
        name = function.get("name")
        if name is not None:
            if not isinstance(name, str) or not name:
                raise TypeError
            if builder.name is not None and builder.name != name:
                raise ValueError
            builder.name = name
        arguments = function.get("arguments")
        if arguments is not None:
            if not isinstance(arguments, str):
                raise TypeError
            builder.arguments += arguments

    def finish(self) -> ModelResponse:
        calls: list[ModelToolCall] = []
        for index in sorted(self._tool_calls):
            builder = self._tool_calls[index]
            if builder.id is None or builder.name is None:
                raise ModelProtocolError("Model stream contained invalid data")
            calls.append(
                ModelToolCall(
                    id=builder.id,
                    name=builder.name,
                    arguments=builder.arguments,
                )
            )
        return ModelResponse(content="".join(self._content), tool_calls=tuple(calls))
