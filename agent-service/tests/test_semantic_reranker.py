import pytest
from agents.testing import ScriptedModel, assistant_message

from agent_service.mall_client import ProductData
from agent_service.recommendation_agent import SemanticReranker
from agent_service.recommendation_scoring import ScoredCandidate


def product(product_id: str) -> ProductData:
    return ProductData(
        id=product_id,
        name=product_id,
        description="candidate",
        price_cents=100,
        currency="TWD",
        category="drinkware",
        tags=("coffee",),
        availability="in_stock",
    )


@pytest.mark.anyio
async def test_semantic_reranker_uses_independent_prompt_and_returns_selection() -> None:
    model = ScriptedModel(
        [[assistant_message('{"product_ids":["organic-2","ad-1"]}')]]
    )
    reranker = SemanticReranker(model)
    candidates = [
        ScoredCandidate(product("organic-1"), "organic", 80, "matched", {"demand": 40}, ("cart",)),
        ScoredCandidate(product("organic-2"), "organic", 70, "matched", {"demand": 35}, ("browsing",)),
        ScoredCandidate(product("ad-1"), "sponsored", 69, "matched", {"demand": 34}, ("external_profile",)),
    ]

    selected = await reranker.rerank(
        "daily coffee mug",
        candidates,
        environment={"weather": "rainy", "season": "winter"},
    )

    assert selected == ("organic-2", "ad-1")
    assert model.first_call is not None
    assert "semantic" in model.first_call.system_instructions.lower()


@pytest.mark.anyio
async def test_semantic_reranker_falls_back_to_score_order_on_invalid_model_output() -> None:
    model = ScriptedModel([[assistant_message("not-json")]])
    reranker = SemanticReranker(model)
    candidates = [
        ScoredCandidate(product("organic-1"), "organic", 80, "matched", {}, ()),
        ScoredCandidate(product("organic-2"), "organic", 70, "matched", {}, ()),
        ScoredCandidate(product("ad-1"), "sponsored", 69, "matched", {}, ()),
    ]

    selected = await reranker.rerank("daily coffee mug", candidates)

    assert selected == ("organic-1", "organic-2", "ad-1")
