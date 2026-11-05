import httpx
import pytest

from agent_service.app import create_app
from agent_service.mall_client import MallServiceUnavailable, ProductData
from agent_service.providers import EchoProvider


class RecordingMallClient:
    def __init__(self, *, unavailable: bool = False) -> None:
        self.calls = 0
        self.unavailable = unavailable

    def list_products(self) -> list[ProductData]:
        self.calls += 1
        if self.unavailable:
            raise MallServiceUnavailable
        return [
            ProductData(
                id="canvas-tote",
                name="Canvas Tote",
                description="durable",
                price_cents=59000,
                currency="TWD",
            )
        ]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def make_client(mall_client: RecordingMallClient) -> httpx.AsyncClient:
    app = create_app(mall_client=mall_client, provider=EchoProvider())
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    )


@pytest.mark.anyio
async def test_health_returns_ok() -> None:
    async with await make_client(RecordingMallClient()) as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.anyio
async def test_chat_trims_message_queries_mall_and_returns_reply() -> None:
    mall_client = RecordingMallClient()
    async with await make_client(mall_client) as client:
        response = await client.post("/chat", json={"message": "  hello  "})

    assert response.status_code == 200
    assert response.json() == {
        "reply": "User message: hello\nAvailable products:\n- Canvas Tote: 59000 TWD"
    }
    assert mall_client.calls == 1


@pytest.mark.anyio
async def test_chat_maps_mall_failure_to_stable_502() -> None:
    async with await make_client(RecordingMallClient(unavailable=True)) as client:
        response = await client.post("/chat", json={"message": "hello"})

    assert response.status_code == 502
    assert response.json() == {
        "error": {
            "code": "mall_service_unavailable",
            "message": "Mall service is unavailable",
        }
    }


@pytest.mark.anyio
async def test_whitespace_chat_returns_422_without_calling_mall() -> None:
    mall_client = RecordingMallClient()
    async with await make_client(mall_client) as client:
        response = await client.post("/chat", json={"message": "   "})

    assert response.status_code == 422
    assert mall_client.calls == 0
