import httpx
import anyio
import pytest
from pathlib import Path
from agents.testing import ScriptedModel, assistant_message, function_call
from openai import APIConnectionError
from pydantic import ValidationError

from agent_service.config import LLMConfig
from agent_service.mall_client import MallServiceUnavailable, ProductData
from agent_service.providers import ModelProtocolError, ModelUpstreamError
from agent_service.recommendation_agent import (
    DEFAULT_RECOMMENDATION_POLICY_PROMPT_PATH,
    RecommendationAgent,
    RecommendationInvalid,
    RecommendationResult,
    ToolArgumentsInvalid,
    ToolLoopExceeded,
    create_recommendation_agent,
)
from agent_service.prompt_loader import load_prompt


def item(product_id: str, name: str = "Product") -> ProductData:
    return ProductData(
        id=product_id,
        name=name,
        description="Description",
        price_cents=100,
        currency="TWD",
        category="general",
        tags=("useful",),
        availability="in_stock",
    )


class RecordingProductTool:
    def __init__(self, results: list[object] | None = None, *, delay: float = 0) -> None:
        self.results = results or []
        self.filters = []
        self.delay = delay

    async def search(self, filters):
        self.filters.append(filters)
        if self.delay:
            await anyio.sleep(self.delay)
        result = self.results.pop(0) if self.results else ()
        if isinstance(result, BaseException):
            raise result
        return result


@pytest.mark.anyio
async def test_sdk_runner_returns_plain_text_without_searching() -> None:
    model = ScriptedModel([[assistant_message("  你好  ")]])
    tool = RecordingProductTool()

    result = await RecommendationAgent(model, tool).generate(
        [{"role": "user", "content": "hello"}]
    )

    assert result == RecommendationResult(answer="你好", product_ids=())
    assert tool.filters == []
    assert model.first_call is not None
    assert model.first_call.system_instructions == load_prompt(
        DEFAULT_RECOMMENDATION_POLICY_PROMPT_PATH
    )
    assert [registered.name for registered in model.first_call.tools] == [
        "search_products"
    ]


@pytest.mark.anyio
async def test_recommendation_policy_is_reloaded_for_each_generation(
    tmp_path: Path,
) -> None:
    prompt_file = tmp_path / "recommendation-policy.md"
    prompt_file.write_text("policy-v1", encoding="utf-8")
    model = ScriptedModel([[assistant_message("你好")], [assistant_message("再见")]])
    agent = RecommendationAgent(
        model,
        RecordingProductTool(),
        recommendation_policy_prompt_path=prompt_file,
    )

    await agent.generate([])
    prompt_file.write_text("policy-v2", encoding="utf-8")
    await agent.generate([])

    assert model.calls[0].system_instructions == "policy-v1"
    assert model.calls[1].system_instructions == "policy-v2"


@pytest.mark.anyio
async def test_sdk_runner_searches_then_submits_grounded_recommendation() -> None:
    model = ScriptedModel(
        [
            [function_call("search_products", {"keyword": "mug"}, call_id="search-1")],
            [
                function_call(
                    "submit_product_recommendation",
                    {"answer": "  推荐这款杯子。 ", "product_ids": ["mug"]},
                    call_id="submit-1",
                )
            ],
        ]
    )
    tool = RecordingProductTool([(item("mug", "Ceramic Mug"),)])

    result = await RecommendationAgent(model, tool).generate(
        [{"role": "user", "content": "推荐杯子"}]
    )

    assert result == RecommendationResult(answer="推荐这款杯子。", product_ids=("mug",))
    assert tool.filters[0].keyword == "mug"
    assert len(model.calls) == 2
    assert [registered.name for registered in model.calls[1].tools] == [
        "submit_product_recommendation"
    ]
    assert any(
        entry.get("type") == "function_call_output"
        and '"id": "mug"' in entry.get("output", "")
        for entry in model.calls[1].input
        if isinstance(entry, dict)
    )


@pytest.mark.anyio
async def test_submission_decodes_literal_newlines_from_provider_arguments() -> None:
    model = ScriptedModel(
        [
            [function_call("search_products", {}, call_id="search-1")],
            [
                function_call(
                    "submit_product_recommendation",
                    {
                        "answer": "第一行\\n第二行",
                        "product_ids": ["mug"],
                    },
                    call_id="submit-1",
                )
            ],
        ]
    )
    tool = RecordingProductTool([(item("mug", "Ceramic Mug"),)])

    result = await RecommendationAgent(model, tool).generate([])

    assert result.answer == "第一行\n第二行"


@pytest.mark.anyio
async def test_empty_search_can_refine_before_submitting() -> None:
    model = ScriptedModel(
        [
            [function_call("search_products", {"keyword": "商品"}, call_id="s1")],
            [function_call("search_products", {}, call_id="s2")],
            [
                function_call(
                    "submit_product_recommendation",
                    {"answer": "推荐这件商品", "product_ids": ["one"]},
                    call_id="done",
                )
            ],
        ]
    )
    tool = RecordingProductTool([(), (item("one"),)])

    result = await RecommendationAgent(model, tool).generate([])

    assert result == RecommendationResult("推荐这件商品", ("one",))
    assert [registered.name for registered in model.calls[1].tools] == [
        "search_products",
        "submit_product_recommendation",
    ]
    assert [registered.name for registered in model.calls[2].tools] == [
        "submit_product_recommendation"
    ]


@pytest.mark.anyio
async def test_fourth_search_is_rejected_before_mall_execution() -> None:
    model = ScriptedModel(
        [
            [function_call("search_products", {}, call_id="s1")],
            [function_call("search_products", {}, call_id="s2")],
            [function_call("search_products", {}, call_id="s3")],
            [function_call("search_products", {}, call_id="s4")],
        ]
    )
    tool = RecordingProductTool([(), (), (), ()])

    with pytest.raises(ToolLoopExceeded):
        await RecommendationAgent(model, tool).generate([])

    assert len(tool.filters) == 3


@pytest.mark.anyio
async def test_searches_are_serialized_and_stop_after_products_are_found() -> None:
    model = ScriptedModel(
        [
            [
                function_call("search_products", {}, call_id="s1"),
                function_call("search_products", {}, call_id="s2"),
            ],
            [
                function_call(
                    "submit_product_recommendation",
                    {"answer": "推荐这件商品", "product_ids": ["one"]},
                    call_id="done",
                )
            ],
        ]
    )
    tool = RecordingProductTool([(item("one"),), (item("two"),)], delay=0.01)

    result = await RecommendationAgent(model, tool).generate([])

    assert result == RecommendationResult("推荐这件商品", ("one",))
    assert len(tool.filters) == 1


@pytest.mark.anyio
async def test_submission_deduplicates_only_ids_seen_this_turn() -> None:
    model = ScriptedModel(
        [
            [function_call("search_products", {}, call_id="s1")],
            [
                function_call(
                    "submit_product_recommendation",
                    {"answer": "选好了", "product_ids": ["two", "one", "two"]},
                    call_id="done",
                )
            ],
        ]
    )
    tool = RecordingProductTool([(item("one"), item("two"))])

    result = await RecommendationAgent(model, tool).generate([])

    assert result == RecommendationResult("选好了", ("two", "one"))


@pytest.mark.anyio
async def test_unknown_product_id_is_rejected() -> None:
    model = ScriptedModel(
        [
            [function_call("search_products", {}, call_id="s1")],
            [
                function_call(
                    "submit_product_recommendation",
                    {"answer": "错误推荐", "product_ids": ["invented"]},
                    call_id="done",
                )
            ],
        ]
    )

    with pytest.raises(RecommendationInvalid):
        await RecommendationAgent(model, RecordingProductTool([(item("one"),)])).generate(
            []
        )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "arguments",
    [
        {"min_price_cents": 10, "max_price_cents": 1},
        {"limit": "2"},
        {"limit": True},
        {"made_up": "value"},
    ],
)
@pytest.mark.anyio
async def test_invalid_search_arguments_are_rejected_before_execution(arguments) -> None:
    model = ScriptedModel(
        [[function_call("search_products", arguments, call_id="s1")]]
    )
    tool = RecordingProductTool()

    with pytest.raises(ToolArgumentsInvalid):
        await RecommendationAgent(model, tool).generate([])

    assert tool.filters == []


@pytest.mark.anyio
async def test_mall_failure_is_remapped_without_leaking_detail() -> None:
    model = ScriptedModel([[function_call("search_products", {}, call_id="s1")]])
    tool = RecordingProductTool([MallServiceUnavailable("secret upstream body")])

    with pytest.raises(MallServiceUnavailable) as caught:
        await RecommendationAgent(model, tool).generate([])

    assert str(caught.value) == "Mall service is unavailable"


@pytest.mark.anyio
async def test_model_connection_failure_is_mapped_without_leaking_detail() -> None:
    request = httpx.Request("POST", "https://model.example/chat/completions")
    model = ScriptedModel([APIConnectionError(request=request)])

    with pytest.raises(ModelUpstreamError) as caught:
        await RecommendationAgent(model, RecordingProductTool()).generate([])

    assert str(caught.value) == "Model service is unavailable"


@pytest.mark.anyio
async def test_invalid_model_payload_is_mapped_to_protocol_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = ScriptedModel([])
    agent = RecommendationAgent(model, RecordingProductTool())
    invalid_payload = ValidationError.from_exception_data(
        "ChatCompletion",
        [{"type": "string_type", "loc": ("content",), "input": 123}],
    )

    async def raise_invalid_payload(*args, **kwargs):
        raise invalid_payload

    monkeypatch.setattr("agent_service.recommendation_agent.Runner.run", raise_invalid_payload)

    with pytest.raises(ModelProtocolError) as caught:
        await agent.generate([])

    assert str(caught.value) == "Model response was invalid"


@pytest.mark.anyio
async def test_malformed_model_shape_is_mapped_to_protocol_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = RecommendationAgent(ScriptedModel([]), RecordingProductTool())

    async def raise_malformed_shape(*args, **kwargs):
        raise AttributeError("message")

    monkeypatch.setattr(
        "agent_service.recommendation_agent.Runner.run", raise_malformed_shape
    )

    with pytest.raises(ModelProtocolError) as caught:
        await agent.generate([])

    assert str(caught.value) == "Model response was invalid"


def test_factory_uses_configured_chat_completions_model_and_disables_tracing() -> None:
    config = LLMConfig(
        provider="openai-compatible",
        base_url="https://model.example/v1",
        api_key="local-test-key",
        model="deepseek-flash",
        timeout_seconds=15,
        prompt_file=Path("system.md"),
        recommendation_policy_prompt_file=Path("recommendation-policy.md"),
    )
    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    agent = create_recommendation_agent(
        config,
        RecordingProductTool(),
        client_factory=FakeClient,
    )

    assert captured == {
        "api_key": "local-test-key",
        "base_url": "https://model.example/v1",
        "timeout": 15,
    }
    assert agent.model_name == "deepseek-flash"
    assert agent.tracing_disabled is True
