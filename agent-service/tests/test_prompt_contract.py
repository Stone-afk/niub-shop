from pathlib import Path


PROMPTS = Path(__file__).parents[1] / "prompts"


def test_shopping_agent_prompt_uses_reviewable_sections_and_real_tools() -> None:
    prompt = (PROMPTS / "shopping_agent.md").read_text(encoding="utf-8")

    for section in (
        "## 基本约束",
        "## 显式 Plan 和 Plan State",
        "## 推荐主流程",
        "## 候选商品与广告",
        "## 购买与订单",
        "## 多轮对话",
    ):
        assert section in prompt

    for tool_name in (
        "update_plan",
        "get_agent_profile",
        "get_external_profile",
        "get_user_context",
        "get_product_recommendations",
        "get_ad_recommendations",
        "search_products",
        "submit_product_recommendation",
        "create_order_draft",
    ):
        assert f"`{tool_name}`" in prompt

    assert "`recommend_products`" not in prompt
    assert "`rerank_recommendations`" not in prompt


def test_reranker_prompt_declares_candidate_only_output_contract() -> None:
    prompt = (PROMPTS / "recommendation_reranker.md").read_text(encoding="utf-8")

    assert "## 输入 / Input" in prompt
    assert "## 数量与顺序 / Count and order" in prompt
    assert '"product_id"' in prompt
    assert "只能选择输入候选中的商品" in prompt


def test_agent_prompts_define_single_language_response_contract() -> None:
    for name in ("system.md", "shopping_agent.md"):
        prompt = (PROMPTS / name).read_text(encoding="utf-8")
        assert "response_language" in prompt
        assert "只使用" in prompt or "only" in prompt.lower()
