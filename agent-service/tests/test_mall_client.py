import httpx
import pytest

from agent_service.mall_client import (
    CandidateProducts,
    ExternalProfileData,
    MallClient,
    MallServiceUnavailable,
    ProductData,
    UserContextData,
)


PRODUCT_RESPONSE = {
    "items": [
        {
            "id": "canvas-tote",
            "name": "经典帆布袋 / Classic Canvas Tote",
            "description": "durable",
            "price_cents": 59000,
            "currency": "TWD",
            "category": "bags",
            "tags": ["everyday", "durable"],
            "availability": "in_stock",
        }
    ]
}


def test_list_products_returns_validated_product_data() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json=PRODUCT_RESPONSE)
    )

    products = MallClient("http://mall.test", transport=transport).list_products()

    assert products == [
        ProductData(
            id="canvas-tote",
            name="经典帆布袋 / Classic Canvas Tote",
            description="durable",
            price_cents=59000,
            currency="TWD",
            category="bags",
            tags=("everyday", "durable"),
            availability="in_stock",
        )
    ]


def test_list_products_normalizes_trailing_slash() -> None:
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        return httpx.Response(200, json=PRODUCT_RESPONSE)

    MallClient("http://mall.test/", transport=httpx.MockTransport(handler)).list_products()

    assert requested_urls == ["http://mall.test/products"]


@pytest.mark.parametrize(
    "handler",
    [
        lambda request: (_ for _ in ()).throw(httpx.ConnectError("offline")),
        lambda request: httpx.Response(503, json={"error": "offline"}),
        lambda request: httpx.Response(200, content=b"not-json"),
        lambda request: httpx.Response(
            200,
            json={"items": [{"id": "broken", "price_cents": "59000"}]},
        ),
        lambda request: httpx.Response(
            200,
            json={
                "items": [
                    {
                        **PRODUCT_RESPONSE["items"][0],
                        "tags": "everyday",
                    }
                ]
            },
        ),
        lambda request: httpx.Response(
            200,
            json={
                "items": [
                    {
                        **PRODUCT_RESPONSE["items"][0],
                        "availability": "backordered",
                    }
                ]
            },
        ),
    ],
    ids=[
        "network",
        "non-2xx",
        "non-json",
        "missing-fields",
        "invalid-tags",
        "invalid-availability",
    ],
)
def test_list_products_maps_upstream_failures(handler) -> None:
    client = MallClient("http://mall.test", transport=httpx.MockTransport(handler))

    with pytest.raises(MallServiceUnavailable):
        client.list_products()


def test_client_reads_user_context_and_both_recommendation_sources() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/context"):
            return httpx.Response(
                200,
                json={
                    "user_id": 1,
                    "available": True,
                    "is_new_user": False,
                    "browsing": [
                        {
                            "product_id": "ceramic-mug",
                            "viewed_at": "2026-11-06T10:00:00Z",
                            "count": 2,
                        }
                    ],
                    "orders": [
                        {
                            "product_id": "canvas-tote",
                            "ordered_at": "2026-10-20T10:00:00Z",
                            "quantity": 1,
                            "status": "completed",
                        }
                    ],
                    "cart": [
                        {
                            "product_id": "ceramic-mug",
                            "added_at": "2026-11-06T09:00:00Z",
                            "quantity": 1,
                        }
                    ],
                },
            )
        if request.url.path.endswith("/external-profile"):
            return httpx.Response(
                200,
                json={
                    "user_id": 1,
                    "source": "recommendation-platform",
                    "available": True,
                    "profile": {"interest_tags": ["coffee"]},
                },
            )
        source = "sponsored" if "/ads/" in request.url.path else "organic"
        return httpx.Response(200, json={"source": source, **PRODUCT_RESPONSE})

    client = MallClient("http://mall.test", transport=httpx.MockTransport(handler))

    assert client.get_user_context(1) == UserContextData(
        1,
        True,
        False,
        ({"product_id": "ceramic-mug", "viewed_at": "2026-11-06T10:00:00Z", "count": 2},),
        ({"product_id": "canvas-tote", "ordered_at": "2026-10-20T10:00:00Z", "quantity": 1, "status": "completed"},),
        ({"product_id": "ceramic-mug", "added_at": "2026-11-06T09:00:00Z", "quantity": 1},),
    )
    assert client.get_user_context(1).purchased_product_ids == ("canvas-tote",)
    assert client.get_user_context(1).cart_product_ids == ("ceramic-mug",)
    assert client.get_external_profile(1) == ExternalProfileData(
        1, "recommendation-platform", True, {"interest_tags": ["coffee"]}
    )
    assert client.get_recommendations(1) == CandidateProducts(
        "organic", (ProductData(
            id="canvas-tote", name="经典帆布袋 / Classic Canvas Tote", description="durable",
            price_cents=59000, currency="TWD", category="bags",
            tags=("everyday", "durable"), availability="in_stock",
        ),)
    )
    assert client.get_ad_recommendations(1).source == "sponsored"
