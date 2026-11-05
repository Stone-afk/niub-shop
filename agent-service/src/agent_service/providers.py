"""Vendor-neutral model provider contract and local implementation."""

from typing import Protocol, runtime_checkable


@runtime_checkable
class ModelProvider(Protocol):
    def complete(self, prompt: str) -> str:
        ...


class EchoProvider:
    def complete(self, prompt: str) -> str:
        return prompt
