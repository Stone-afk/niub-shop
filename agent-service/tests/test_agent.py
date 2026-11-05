from agent_service.agent import Agent
from agent_service.mall_client import ProductData
from agent_service.providers import ModelProvider


class RecordingProvider:
    def __init__(self) -> None:
        self.prompt: str | None = None

    def complete(self, prompt: str) -> str:
        self.prompt = prompt
        return "provider reply"


def test_provider_protocol_accepts_structural_implementation() -> None:
    assert isinstance(RecordingProvider(), ModelProvider)


def test_agent_builds_catalog_prompt_and_returns_provider_reply() -> None:
    provider = RecordingProvider()
    products = [
        ProductData(
            id="canvas-tote",
            name="经典帆布袋 / Classic Canvas Tote",
            description="durable",
            price_cents=59000,
            currency="TWD",
        )
    ]

    reply = Agent(provider).run("有什么推荐？", products)

    assert reply == "provider reply"
    assert provider.prompt is not None
    assert "有什么推荐？" in provider.prompt
    assert "经典帆布袋 / Classic Canvas Tote" in provider.prompt
    assert "59000 TWD" in provider.prompt

