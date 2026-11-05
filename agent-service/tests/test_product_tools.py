import threading

import pytest
from pydantic import ValidationError

from agent_service.mall_client import MallServiceUnavailable, ProductData
from agent_service.product_tools import MallProductSearchTool, ProductSearchFilters


def product(
    product_id: str,
    *,
    name: str,
    description: str = "",
    price_cents: int = 100,
    category: str = "general",
    tags: tuple[str, ...] = (),
    availability: str = "in_stock",
) -> ProductData:
    return ProductData(
        id=product_id,
        name=name,
        description=description,
        price_cents=price_cents,
        currency="TWD",
        category=category,
        tags=tags,
        availability=availability,  # type: ignore[arg-type]
    )


class StubMallClient:
    def __init__(self, products: list[ProductData], error: Exception | None = None) -> None:
        self.products = products
        self.error = error
        self.thread_id: int | None = None

    def list_products(self) -> list[ProductData]:
        self.thread_id = threading.get_ident()
        if self.error is not None:
            raise self.error
        return self.products


@pytest.mark.anyio
async def test_filters_by_trimmed_keyword_category_price_and_in_stock() -> None:
    client = StubMallClient(
        [
            product("match", name="Travel Coffee Mug", price_cents=400, category="Drinkware"),
            product("cheap", name="Coffee Cup", price_cents=50, category="drinkware"),
            product("other-category", name="Coffee Beans", price_cents=300, category="grocery"),
            product(
                "unavailable",
                name="Coffee Flask",
                price_cents=300,
                category="drinkware",
                availability="out_of_stock",
            ),
        ]
    )

    results = await MallProductSearchTool(client).search(
        ProductSearchFilters(
            keyword="  coffee  ",
            category="  DRINKWARE ",
            min_price_cents=100,
            max_price_cents=500,
        )
    )

    assert [item.id for item in results] == ["match"]


@pytest.mark.anyio
async def test_relevance_is_deterministic_and_matches_name_description_then_tags() -> None:
    client = StubMallClient(
        [
            product("tag-first", name="One", tags=("coffee",)),
            product("description", name="Two", description="Fresh coffee daily"),
            product("name-first", name="Coffee maker"),
            product("tag-second", name="Three", tags=("coffee",)),
            product("irrelevant", name="Tea pot"),
        ]
    )

    results = await MallProductSearchTool(client).search(
        ProductSearchFilters(keyword="COFFEE")
    )

    assert [item.id for item in results] == [
        "name-first",
        "description",
        "tag-first",
        "tag-second",
    ]


@pytest.mark.anyio
async def test_sorts_price_both_directions_and_applies_limit() -> None:
    products = [
        product("middle-first", name="A", price_cents=200),
        product("cheapest", name="B", price_cents=100),
        product("middle-second", name="C", price_cents=200),
        product("highest", name="D", price_cents=300),
    ]
    tool = MallProductSearchTool(StubMallClient(products))

    ascending = await tool.search(ProductSearchFilters(sort="price_asc", limit=3))
    descending = await tool.search(ProductSearchFilters(sort="price_desc", limit=2))

    assert [item.id for item in ascending] == ["cheapest", "middle-first", "middle-second"]
    assert [item.id for item in descending] == ["highest", "middle-first"]


@pytest.mark.anyio
async def test_defaults_to_relevance_and_five_results() -> None:
    products = [product(str(index), name=str(index)) for index in range(6)]

    results = await MallProductSearchTool(StubMallClient(products)).search(
        ProductSearchFilters()
    )

    assert ProductSearchFilters().sort == "relevance"
    assert ProductSearchFilters().limit == 5
    assert [item.id for item in results] == ["0", "1", "2", "3", "4"]


@pytest.mark.parametrize(
    "values",
    [
        {"unexpected": True},
        {"min_price_cents": -1},
        {"max_price_cents": -1},
        {"min_price_cents": 20, "max_price_cents": 10},
        {"limit": 0},
        {"limit": 11},
        {"keyword": "x" * 201},
        {"category": "x" * 101},
    ],
)
def test_rejects_unknown_fields_negative_prices_inverted_range_and_limit_bounds(
    values: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ProductSearchFilters.model_validate(values)


@pytest.mark.anyio
async def test_runs_sync_mall_client_in_worker_thread_and_maps_mall_failure() -> None:
    main_thread = threading.get_ident()
    client = StubMallClient([product("one", name="One")])

    await MallProductSearchTool(client).search(ProductSearchFilters())

    assert client.thread_id is not None
    assert client.thread_id != main_thread

    failing_client = StubMallClient([], MallServiceUnavailable("offline"))
    with pytest.raises(MallServiceUnavailable):
        await MallProductSearchTool(failing_client).search(ProductSearchFilters())
