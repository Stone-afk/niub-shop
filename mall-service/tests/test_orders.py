import httpx
import pytest

import mall_service.app as mall_app
from mall_service.app import app
from mall_service.orders import OrderStore


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setattr(mall_app, "order_store", OrderStore(tmp_path / "mall.db"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as test_client:
        yield test_client


@pytest.mark.anyio
async def test_create_order_snapshots_product_and_is_idempotent(client):
    payload = {
        "items": [{"product_id": "ceramic-mug", "quantity": 2}],
        "client_request_id": "intent-1",
    }

    first = await client.post("/orders", json=payload)
    second = await client.post("/orders", json=payload)

    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["status"] == "pending_payment"
    assert first.json()["items"][0]["name"] == "陶瓷马克杯 / Ceramic Mug"
    assert first.json()["subtotal_cents"] == 78000


@pytest.mark.anyio
async def test_order_preview_and_mock_payment_are_idempotent(client):
    created = await client.post(
        "/orders",
        json={
            "items": [{"product_id": "canvas-tote", "quantity": 1}],
            "client_request_id": "intent-2",
        },
    )
    order_id = created.json()["id"]

    preview = await client.get(f"/orders/{order_id}/preview")
    paid = await client.post(f"/orders/{order_id}/pay")
    repeated = await client.post(f"/orders/{order_id}/pay")

    assert preview.status_code == 200
    assert preview.json()["status"] == "pending_payment"
    assert paid.status_code == 200
    assert paid.json()["status"] == "paid"
    assert repeated.status_code == 200
    assert repeated.json() == paid.json()


@pytest.mark.anyio
async def test_unknown_or_out_of_stock_product_does_not_create_order(client):
    unknown = await client.post(
        "/orders",
        json={
            "items": [{"product_id": "missing", "quantity": 1}],
            "client_request_id": "intent-3",
        },
    )
    out_of_stock = await client.post(
        "/orders",
        json={
            "items": [{"product_id": "wool-scarf", "quantity": 1}],
            "client_request_id": "intent-4",
        },
    )

    assert unknown.status_code == 400
    assert out_of_stock.status_code == 409
