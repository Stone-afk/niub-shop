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
async def test_chat_streams_fixed_reply_as_delta_and_done_events() -> None:
    mall_client = RecordingMallClient()
    async with await make_client(mall_client) as client:
        response = await client.post(
            "/chat",
            json={"messages": [{"role": "user", "content": "推荐商品"}]},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.text == (
        'event: delta\ndata: {"content": "你好！我是牛布商城智能助手。"}\n\n'
        'event: delta\ndata: {"content": " / Hi! I’m the Niub Shop assistant."}\n\n'
        'event: delta\ndata: {"content": "\\n这是一个固定的 SSE 流式回复演示。"}\n\n'
        'event: delta\ndata: {"content": " / This is a fixed SSE streaming demo."}\n\n'
        "event: done\ndata: {}\n\n"
    )
    assert mall_client.calls == 0


@pytest.mark.anyio
async def test_chat_accepts_complete_history() -> None:
    async with await make_client(RecordingMallClient()) as client:
        response = await client.post(
            "/chat",
            json={
                "messages": [
                    {"role": "user", "content": "第一轮"},
                    {"role": "assistant", "content": "固定回复"},
                    {"role": "user", "content": "第二轮"},
                ]
            },
        )

    assert response.status_code == 200
    assert response.text.endswith("event: done\ndata: {}\n\n")


@pytest.mark.anyio
async def test_empty_messages_returns_422() -> None:
    async with await make_client(RecordingMallClient()) as client:
        response = await client.post("/chat", json={"messages": []})

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "messages"]
    assert response.json()["detail"][0]["type"] == "too_short"


@pytest.mark.anyio
async def test_whitespace_message_returns_422() -> None:
    async with await make_client(RecordingMallClient()) as client:
        response = await client.post(
            "/chat",
            json={"messages": [{"role": "user", "content": "   "}]},
        )

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == [
        "body",
        "messages",
        0,
        "content",
    ]
    assert response.json()["detail"][0]["type"] == "string_too_short"


@pytest.mark.anyio
async def test_unknown_role_returns_422() -> None:
    async with await make_client(RecordingMallClient()) as client:
        response = await client.post(
            "/chat",
            json={"messages": [{"role": "system", "content": "hello"}]},
        )

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "messages", 0, "role"]
    assert response.json()["detail"][0]["type"] == "literal_error"
