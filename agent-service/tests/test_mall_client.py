import httpx
import pytest

from agent_service.mall_client import MallClient, MallServiceUnavailable, ProductData


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
