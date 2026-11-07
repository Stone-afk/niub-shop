import httpx
import anyio
import pytest
from pathlib import Path
from agents.testing import ScriptedModel, assistant_message, function_call
from openai import APIConnectionError
from pydantic import ValidationError

from agent_service.config import LLMConfig
from agent_service.mall_client import (
    CandidateProducts,
    MallServiceUnavailable,
    OrderData,
    ProductData,
    UserContextData,
)
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


class SourceProductTool(RecordingProductTool):
    async def get_user_context(self, user_id: int):
        return UserContextData(
            user_id,
            True,
            False,
            (),
            ({"product_id": "purchased", "status": "completed"},),
            (),
        )

    async def get_product_recommendations(self, user_id: int):
        return CandidateProducts("organic", tuple(item(str(i)) for i in range(1, 5)))

    async def get_ad_recommendations(self, user_id: int):
        return CandidateProducts("sponsored", (item("5"), item("purchased")))


class OrderProductTool(SourceProductTool):
    def __init__(self) -> None:
        super().__init__()
        self.created_requests = []

    async def create_order(self, items, client_request_id: str) -> OrderData:
        self.created_requests.append((items, client_request_id))
        return OrderData(
            id=f"ord-{len(self.created_requests)}",
            user_id=1,
            status="pending_payment",
            items=tuple(items),
            subtotal_cents=39000,
            total_cents=39000,
            currency="TWD",
            created_at="2026-11-06T10:00:00+00:00",
        )


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
        "search_products",
        "semantic_rerank_recommendations",
        "submit_product_recommendation",
    ]


@pytest.mark.anyio
async def test_explicit_purchase_creates_one_order_per_distinct_intent() -> None:
    model = ScriptedModel(
        [
            [
                function_call(
                    "create_order_draft",
                    {"items": [{"product_id": "1", "quantity": 1}]},
                    call_id="order-1",
                ),
                function_call(
                    "create_order_draft",
                    {"items": [{"product_id": "2", "quantity": 1}]},
                    call_id="order-2",
                ),
            ],
            [
                function_call(
                    "submit_product_recommendation",
                    {"answer": "已创建订单", "product_ids": ["1", "2"]},
                    call_id="submit",
                )
            ],
        ]
    )
    tool = OrderProductTool()
    agent = RecommendationAgent(model, tool)
    agent.set_turn_context("conversation-1", "turn-1")

    result = await agent.generate([])

    assert [order.order_id for order in result.orders] == ["ord-1", "ord-2"]
    assert result.product_ids == ("1", "2")
    assert len(tool.created_requests) == 2
    assert tool.created_requests[0][1] != tool.created_requests[1][1]


@pytest.mark.anyio
async def test_created_order_survives_invalid_final_submission() -> None:
    model = ScriptedModel(
        [
            [
                function_call(
                    "create_order_draft",
                    {"items": [{"product_id": "1", "quantity": 1}]},
                    call_id="order-1",
                )
            ],
            [
                function_call(
                    "submit_product_recommendation",
                    {"answer": "订单已创建", "product_ids": ["unknown"]},
                    call_id="invalid-submit",
                )
            ],
        ]
    )

    result = await RecommendationAgent(model, OrderProductTool()).generate([])

    assert result.orders[0].order_id == "ord-1"
    assert "订单" in result.answer


@pytest.mark.anyio
async def test_agent_combines_context_organic_and_sponsored_candidates() -> None:
    model = ScriptedModel(
        [
            [
                function_call("get_user_context", {}, call_id="context"),
                function_call("get_product_recommendations", {}, call_id="organic"),
                function_call("get_ad_recommendations", {}, call_id="ads"),
            ],
            [
                function_call(
                    "submit_product_recommendation",
                    {"answer": "综合推荐", "product_ids": ["1", "2", "5"]},
                    call_id="submit",
                )
            ],
        ]
    )

    result = await RecommendationAgent(model, SourceProductTool()).generate([])

    assert result.product_ids == ("1", "2", "5")
    assert result.product_sources == ("organic", "organic", "sponsored")
    assert "get_ad_recommendations" in [tool.name for tool in model.first_call.tools]


@pytest.mark.anyio
async def test_model_owned_plan_state_is_updated_and_returned() -> None:
    plan_draft = {
        "plan_id": "plan-1",
        "goal": "推荐杯子",
        "status": "in_progress",
        "current_step_id": "profile",
        "steps": [
            {
                "id": "profile",
                "title": "加载画像",
                "status": "in_progress",
                "depends_on": [],
            },
            {
                "id": "recommend",
                "title": "输出推荐",
                "status": "pending",
                "depends_on": ["profile"],
            },
        ],
        "next_action": "get_agent_profile",
        "reason": "先了解用户偏好",
    }
    plan_done = {
        **plan_draft,
        "status": "completed",
        "current_step_id": None,
        "steps": [
            plan_draft["steps"][0] | {"status": "completed"},
            plan_draft["steps"][1] | {"status": "completed"},
        ],
        "next_action": "submit_product_recommendation",
        "reason": "已获得足够信息",
    }
    model = ScriptedModel(
        [
            [function_call("update_plan", plan_draft, call_id="plan-1")],
            [
                function_call(
                    "get_product_recommendations", {}, call_id="recommendations"
                )
            ],
            [function_call("update_plan", plan_done, call_id="plan-2")],
            [
                function_call(
                    "submit_product_recommendation",
                    {"answer": "推荐杯子", "product_ids": []},
                    call_id="submit",
                )
            ],
        ]
    )

    result = await RecommendationAgent(model, SourceProductTool()).generate([])

    assert result.plan_state is not None
    assert result.plan_state.status == "completed"
    assert result.plan_state.plan_id == "plan-1"
    assert any(
        entry.get("type") == "function_call_output"
        and '"status":"completed"' in entry.get("output", "")
        for entry in model.calls[-1].input
        if isinstance(entry, dict)
    )


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
        "search_products",
        "semantic_rerank_recommendations",
        "submit_product_recommendation",
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
        "semantic_rerank_recommendations",
        "submit_product_recommendation",
    ]
    assert [registered.name for registered in model.calls[2].tools] == [
        "search_products",
        "semantic_rerank_recommendations",
        "submit_product_recommendation",
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
