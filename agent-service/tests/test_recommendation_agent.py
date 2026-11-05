import json

import anyio
import pytest

from agent_service.mall_client import MallServiceUnavailable, ProductData
from agent_service.providers import ModelResponse, ModelToolCall
from agent_service.recommendation_agent import (
    RECOMMENDATION_POLICY_PROMPT,
    SEARCH_PRODUCTS_TOOL,
    SUBMIT_RECOMMENDATION_TOOL,
    RecommendationAgent,
    RecommendationInvalid,
    RecommendationResult,
    ToolArgumentsInvalid,
    ToolLoopExceeded,
    UnknownTool,
)


def call(call_id: str, name: str, arguments: object) -> ModelToolCall:
    encoded = arguments if isinstance(arguments, str) else json.dumps(arguments)
    return ModelToolCall(id=call_id, name=name, arguments=encoded)


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


class RecordingProvider:
    def __init__(self, responses: list[ModelResponse]) -> None:
        self.responses = responses
        self.requests: list[dict[str, object]] = []

    async def complete_response(self, messages, *, tools, tool_choice="auto"):
        self.requests.append(
            {
                "messages": list(messages),
                "tools": list(tools),
                "tool_choice": tool_choice,
            }
        )
        return self.responses.pop(0)


class RecordingProductTool:
    def __init__(self, results: list[object] | None = None) -> None:
        self.results = results or []
        self.filters = []

    async def search(self, filters):
        self.filters.append(filters)
        result = self.results.pop(0) if self.results else ()
        if isinstance(result, BaseException):
            raise result
        return result


def test_tool_definitions_have_exact_strict_schemas() -> None:
    assert SEARCH_PRODUCTS_TOOL == {
        "type": "function",
        "function": {
            "name": "search_products",
            "description": "Search the trusted mall catalog using structured filters.",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "maxLength": 200},
                    "category": {"type": "string", "maxLength": 100},
                    "min_price_cents": {"type": "integer", "minimum": 0},
                    "max_price_cents": {"type": "integer", "minimum": 0},
                    "sort": {
                        "type": "string",
                        "enum": ["relevance", "price_asc", "price_desc"],
                        "default": "relevance",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10,
                        "default": 5,
                    },
                },
                "additionalProperties": False,
            },
        },
    }
    assert SUBMIT_RECOMMENDATION_TOOL == {
        "type": "function",
        "function": {
            "name": "submit_product_recommendation",
            "description": "Submit the final answer and verified product IDs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "answer": {"type": "string", "minLength": 1, "maxLength": 20000},
                    "product_ids": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                        "maxItems": 10,
                    },
                },
                "required": ["answer", "product_ids"],
                "additionalProperties": False,
            },
        },
    }


@pytest.mark.anyio
async def test_plain_conversation_returns_trimmed_text_without_products() -> None:
    provider = RecordingProvider([ModelResponse(content="  你好  ", tool_calls=())])

    result = await RecommendationAgent(provider, RecordingProductTool()).generate(
        [{"role": "user", "content": "hello"}]
    )

    assert result == RecommendationResult(answer="你好", product_ids=())
    request = provider.requests[0]
    assert request["messages"][-1] == {
        "role": "system",
        "content": RECOMMENDATION_POLICY_PROMPT,
    }
    assert request["tools"] == [SEARCH_PRODUCTS_TOOL, SUBMIT_RECOMMENDATION_TOOL]
    assert request["tool_choice"] == "auto"


@pytest.mark.anyio
async def test_plain_empty_response_is_invalid() -> None:
    provider = RecordingProvider([ModelResponse(content=" \n ", tool_calls=())])

    with pytest.raises(RecommendationInvalid):
        await RecommendationAgent(provider, RecordingProductTool()).generate([])


@pytest.mark.anyio
async def test_search_result_is_returned_to_model_then_submit_is_validated() -> None:
    provider = RecordingProvider(
        [
            ModelResponse(
                content="",
                tool_calls=(call("search-1", "search_products", {"keyword": "mug"}),),
            ),
            ModelResponse(
                content="",
                tool_calls=(
                    call(
                        "submit-1",
                        "submit_product_recommendation",
                        {"answer": "  推荐这款杯子。 ", "product_ids": ["mug"]},
                    ),
                ),
            ),
        ]
    )
    product_tool = RecordingProductTool([(item("mug", "Ceramic Mug"),)])

    result = await RecommendationAgent(provider, product_tool).generate(
        [{"role": "user", "content": "推荐杯子"}]
    )

    assert result == RecommendationResult(answer="推荐这款杯子。", product_ids=("mug",))
    assert product_tool.filters[0].keyword == "mug"
    transcript = provider.requests[1]["messages"]
    assert transcript[-2] == {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "search-1",
                "type": "function",
                "function": {
                    "name": "search_products",
                    "arguments": '{"keyword": "mug"}',
                },
            }
        ],
    }
    assert transcript[-1]["role"] == "tool"
    assert transcript[-1]["tool_call_id"] == "search-1"
    assert json.loads(transcript[-1]["content"]) == {
        "items": [
            {
                "id": "mug",
                "name": "Ceramic Mug",
                "description": "Description",
                "price_cents": 100,
                "currency": "TWD",
                "category": "general",
                "tags": ["useful"],
                "availability": "in_stock",
            }
        ]
    }


@pytest.mark.anyio
async def test_multiple_searches_union_candidates_and_submit_deduplicates_ids() -> None:
    provider = RecordingProvider(
        [
            ModelResponse("", (call("s1", "search_products", {}),)),
            ModelResponse("", (call("s2", "search_products", {"limit": 2}),)),
            ModelResponse(
                "",
                (
                    call(
                        "done",
                        "submit_product_recommendation",
                        {"answer": "选好了", "product_ids": ["two", "one", "two"]},
                    ),
                ),
            ),
        ]
    )
    tool = RecordingProductTool([(item("one"),), (item("two"),)])

    result = await RecommendationAgent(provider, tool).generate([])

    assert result.product_ids == ("two", "one")
    assert tool.filters[0].limit == 5
    assert tool.filters[1].limit == 2


@pytest.mark.anyio
async def test_empty_search_can_submit_explanation_with_no_ids() -> None:
    provider = RecordingProvider(
        [
            ModelResponse("", (call("s", "search_products", {}),)),
            ModelResponse(
                "",
                (
                    call(
                        "done",
                        "submit_product_recommendation",
                        {"answer": "没有合适商品", "product_ids": []},
                    ),
                ),
            ),
        ]
    )

    result = await RecommendationAgent(provider, RecordingProductTool([()])).generate([])

    assert result == RecommendationResult("没有合适商品", ())


@pytest.mark.parametrize(
    "arguments",
    [
        "not-json",
        {"unknown": True},
        {"min_price_cents": -1},
        {"min_price_cents": 10, "max_price_cents": 1},
        {"limit": 11},
    ],
)
@pytest.mark.anyio
async def test_invalid_search_arguments_are_rejected_before_execution(arguments) -> None:
    provider = RecordingProvider(
        [ModelResponse("", (call("s", "search_products", arguments),))]
    )
    tool = RecordingProductTool()

    with pytest.raises(ToolArgumentsInvalid) as caught:
        await RecommendationAgent(provider, tool).generate([])

    assert not tool.filters
    assert "not-json" not in str(caught.value)


@pytest.mark.anyio
async def test_mall_failure_is_remapped_without_leaking_detail() -> None:
    provider = RecordingProvider(
        [ModelResponse("", (call("s", "search_products", {}),))]
    )
    tool = RecordingProductTool([MallServiceUnavailable("secret upstream body")])

    with pytest.raises(MallServiceUnavailable) as caught:
        await RecommendationAgent(provider, tool).generate([])

    assert str(caught.value) == "Mall service is unavailable"


@pytest.mark.anyio
async def test_fourth_search_is_rejected_before_execution() -> None:
    provider = RecordingProvider(
        [
            ModelResponse("", (call("s1", "search_products", {}),)),
            ModelResponse("", (call("s2", "search_products", {}),)),
            ModelResponse("", (call("s3", "search_products", {}),)),
            ModelResponse("", (call("s4", "search_products", {}),)),
        ]
    )
    tool = RecordingProductTool([(), (), (), ()])

    with pytest.raises(ToolLoopExceeded):
        await RecommendationAgent(provider, tool).generate([])

    assert len(tool.filters) == 3


@pytest.mark.parametrize(
    "arguments",
    [
        "not-json",
        {"answer": "ok"},
        {"answer": "", "product_ids": []},
        {"answer": "x" * 20001, "product_ids": []},
        {"answer": "ok", "product_ids": ["invented"]},
        {"answer": "ok", "product_ids": ["one"] * 11},
        {"answer": "ok", "product_ids": [], "extra": True},
    ],
)
@pytest.mark.anyio
async def test_invalid_submit_is_rejected_without_leaking_arguments(arguments) -> None:
    provider = RecordingProvider(
        [
            ModelResponse("", (call("s", "search_products", {}),)),
            ModelResponse(
                "", (call("done", "submit_product_recommendation", arguments),)
            ),
        ]
    )

    with pytest.raises(RecommendationInvalid) as caught:
        await RecommendationAgent(
            provider, RecordingProductTool([(item("one"),)])
        ).generate([])

    assert "invented" not in str(caught.value)


@pytest.mark.anyio
async def test_text_after_search_and_unknown_tool_are_rejected() -> None:
    text_provider = RecordingProvider(
        [
            ModelResponse("", (call("s", "search_products", {}),)),
            ModelResponse("text only", ()),
        ]
    )
    with pytest.raises(RecommendationInvalid):
        await RecommendationAgent(text_provider, RecordingProductTool([()])).generate([])

    unknown_provider = RecordingProvider(
        [ModelResponse("", (call("x", "delete_catalog", {}),))]
    )
    with pytest.raises(UnknownTool):
        await RecommendationAgent(unknown_provider, RecordingProductTool()).generate([])


@pytest.mark.parametrize(
    "tool_calls",
    [
        (
            call("s", "search_products", {}),
            call("done", "submit_product_recommendation", {"answer": "x", "product_ids": []}),
        ),
        (call("same", "search_products", {}), call("same", "search_products", {})),
        (
            call("d1", "submit_product_recommendation", {"answer": "x", "product_ids": []}),
            call("d2", "submit_product_recommendation", {"answer": "y", "product_ids": []}),
        ),
    ],
    ids=["mixed-search-submit", "duplicate-call-id", "two-submit-calls"],
)
@pytest.mark.anyio
async def test_invalid_tool_batches_fail_before_executing_any_tool(tool_calls) -> None:
    provider = RecordingProvider([ModelResponse("", tool_calls)])
    tool = RecordingProductTool()

    with pytest.raises(RecommendationInvalid):
        await RecommendationAgent(provider, tool).generate([])

    assert not tool.filters


@pytest.mark.anyio
async def test_cancellation_from_product_tool_propagates() -> None:
    cancelled = anyio.get_cancelled_exc_class()
    provider = RecordingProvider(
        [ModelResponse("", (call("s", "search_products", {}),))]
    )
    tool = RecordingProductTool([cancelled()])

    with pytest.raises(cancelled):
        await RecommendationAgent(provider, tool).generate([])
