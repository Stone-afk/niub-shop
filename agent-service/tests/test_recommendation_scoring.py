from datetime import datetime, timedelta, timezone

from agent_service.mall_client import ProductData
from agent_service.recommendation_scoring import (
    Demand,
    SourceSignals,
    apply_sponsored_cutoff,
    score_candidates,
)


def product(
    product_id: str,
    *,
    price_cents: int = 39000,
    capacity_ml: int | None = 350,
    tags: tuple[str, ...] = ("coffee", "office"),
    material: str | None = "ceramic",
    has_lid: bool | None = False,
) -> ProductData:
    return ProductData(
        id=product_id,
        name=product_id,
        description="cup",
        price_cents=price_cents,
        currency="TWD",
        category="drinkware",
        tags=tags,
        availability="in_stock",
        capacity_ml=capacity_ml,
        material=material,
        has_lid=has_lid,
    )


def test_hard_constraints_filter_out_of_budget_or_wrong_capacity() -> None:
    demand = Demand(
        category="drinkware",
        max_price_cents=50000,
        min_capacity_ml=300,
        max_capacity_ml=500,
    )

    scored = score_candidates(
        [product("ok"), product("expensive", price_cents=60000), product("large", capacity_ml=600)],
        demand=demand,
        signals=SourceSignals(),
        channel="organic",
    )

    assert [item.product_id for item in scored] == ["ok"]
    assert scored[0].hard_constraint_status == "matched"


def test_cart_outweighs_browsing_and_orders_have_repeat_penalty() -> None:
    now = datetime(2026, 11, 7, tzinfo=timezone.utc)
    demand = Demand(category="drinkware")
    signals = SourceSignals(
        cart_ids=frozenset({"cart"}),
        browsing={
            "browsed": {"count": 4, "viewed_at": (now - timedelta(days=1)).isoformat()}
        },
        ordered_ids=frozenset({"ordered"}),
    )

    scored = score_candidates(
        [product("cart"), product("browsed"), product("ordered")],
        demand=demand,
        signals=signals,
        channel="organic",
        now=now,
    )

    assert [item.product_id for item in scored] == ["cart", "browsed", "ordered"]
    assert scored[0].score_breakdown["cart"] > scored[1].score_breakdown["browsing"]
    assert scored[2].score_breakdown["repeat_penalty"] < 0


def test_ads_do_not_receive_source_bonus_and_cutoff_discards_lane() -> None:
    demand = Demand(category="drinkware")
    organic = score_candidates(
        [product("o1"), product("o2")],
        demand=demand,
        signals=SourceSignals(),
        channel="organic",
    )
    ads = score_candidates(
        [product("ad", tags=())],
        demand=demand,
        signals=SourceSignals(),
        channel="sponsored",
    )

    assert ads[0].score_breakdown["sponsored_bonus"] == 0
    assert ads[0].score <= organic[-1].score
    assert apply_sponsored_cutoff(organic, ads) == []


def test_ad_must_beat_lowest_organic_top10_and_can_never_be_promoted_by_source() -> None:
    demand = Demand(category="drinkware")
    organic = score_candidates(
        [product(f"o{i}", tags=("coffee",)) for i in range(10)],
        demand=demand,
        signals=SourceSignals(),
        channel="organic",
    )
    ad = score_candidates(
        [product("ad", tags=("coffee",), price_cents=39000)],
        demand=demand,
        signals=SourceSignals(),
        channel="sponsored",
    )

    assert apply_sponsored_cutoff(organic, ad) == []

