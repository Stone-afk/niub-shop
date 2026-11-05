"""Prompt construction and provider delegation."""

from .mall_client import ProductData
from .providers import ModelProvider


class Agent:
    def __init__(self, provider: ModelProvider) -> None:
        self._provider = provider

    def run(self, message: str, products: list[ProductData]) -> str:
        catalog_lines = [
            f"- {product.name}: {product.price_cents} {product.currency}"
            for product in products
        ]
        prompt = "\n".join(
            [
                f"User message: {message}",
                "Available products:",
                *catalog_lines,
            ]
        )
        return self._provider.complete(prompt)
