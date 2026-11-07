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
    items = response.json()["items"]

    assert len(items) >= 7
    ceramic = next(item for item in items if item["id"] == "ceramic-mug")
    assert ceramic["price_cents"] == 39000
    assert ceramic["capacity_ml"] == 350
    assert ceramic["material"] == "ceramic"
    assert ceramic["has_lid"] is False


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
