import httpx
import pytest

from mall_service.app import app


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def client() -> httpx.AsyncClient:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as test_client:
        yield test_client


@pytest.mark.anyio
async def test_health_returns_ok(client: httpx.AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.anyio
async def test_products_returns_deterministic_typed_catalog(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/products")

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "id": "canvas-tote",
                "name": "经典帆布袋 / Classic Canvas Tote",
                "description": "轻量耐用的日常托特包 / A lightweight, durable everyday tote.",
                "price_cents": 59000,
                "currency": "TWD",
                "category": "bags",
                "tags": ["everyday", "durable"],
                "availability": "in_stock",
            },
            {
                "id": "ceramic-mug",
                "name": "陶瓷马克杯 / Ceramic Mug",
                "description": "适合咖啡与茶的简约杯款 / A simple mug for coffee or tea.",
                "price_cents": 39000,
                "currency": "TWD",
                "category": "drinkware",
                "tags": ["coffee", "tea"],
                "availability": "in_stock",
            },
            {
                "id": "wool-scarf",
                "name": "羊毛围巾 / Wool Scarf",
                "description": "适合冬季的保暖围巾 / A warm scarf for winter.",
                "price_cents": 79000,
                "currency": "TWD",
                "category": "accessories",
                "tags": ["winter", "warm"],
                "availability": "out_of_stock",
            },
        ]
    }


@pytest.mark.anyio
async def test_product_prices_are_non_negative_integer_minor_units(
    client: httpx.AsyncClient,
) -> None:
    items = (await client.get("/products")).json()["items"]

    assert len(items) >= 2
    assert all(type(item["price_cents"]) is int for item in items)
    assert all(item["price_cents"] >= 0 for item in items)
    assert {item["availability"] for item in items} == {
        "in_stock",
        "out_of_stock",
    }


@pytest.mark.anyio
async def test_mock_user_context_and_recommendation_sources_are_available(
    client: httpx.AsyncClient,
) -> None:
    context = await client.get("/users/1/context")
    organic = await client.get("/users/1/recommendations")
    ads = await client.get("/users/1/ads/recommendations")
    external_profile = await client.get("/users/1/external-profile")

    assert (
        context.status_code
        == organic.status_code
        == ads.status_code
        == external_profile.status_code
        == 200
    )
    assert context.json()["orders"][0]["product_id"] == "canvas-tote"
    assert context.json()["browsing"][0]["product_id"] == "ceramic-mug"
    assert organic.json()["source"] == "organic"
    assert ads.json()["source"] == "sponsored"
    assert external_profile.json()["source"] == "recommendation-platform"
