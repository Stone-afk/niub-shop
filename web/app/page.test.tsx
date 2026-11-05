import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import Home from "./page";


const PRODUCT = {
  id: "canvas-tote",
  name: "经典帆布袋 / Classic Canvas Tote",
  description: "轻量耐用 / Lightweight and durable",
  price_cents: 59000,
  currency: "TWD",
};


afterEach(() => vi.unstubAllGlobals());


function productResponse(items = [PRODUCT]): Response {
  return Response.json({ items });
}


function chatResponse(deltas: string[]): Response {
  const events = deltas
    .map(
      (content) =>
        `event: delta\ndata: ${JSON.stringify({ content })}\n\n`,
    )
    .join("");
  return new Response(`${events}event: done\ndata: {}\n\n`, {
    headers: { "content-type": "text/event-stream" },
  });
}


function controlledChatResponse() {
  const encoder = new TextEncoder();
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const response = new Response(
    new ReadableStream<Uint8Array>({
      start(streamController) {
        controller = streamController;
      },
    }),
    { headers: { "content-type": "text/event-stream" } },
  );
  return {
    response,
    delta(content: string) {
      controller.enqueue(
        encoder.encode(`event: delta\ndata: ${JSON.stringify({ content })}\n\n`),
      );
    },
    done() {
      controller.enqueue(encoder.encode("event: done\ndata: {}\n\n"));
      controller.close();
    },
  };
}


describe("storefront", () => {
  it("shows bilingual headings, loading state, and products", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(productResponse()));

    render(<Home />);

    expect(screen.getByRole("heading", { name: "牛布商城 / Niub Shop" })).toBeVisible();
    expect(screen.getByText("正在加载商品… / Loading products…")).toBeVisible();
    expect(await screen.findByText(PRODUCT.name)).toBeVisible();
    expect(screen.getByText("NT$590")).toBeVisible();
  });

  it("shows the empty catalog state", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(productResponse([])));

    render(<Home />);

    expect(
      await screen.findByText("暂无商品 / No products available"),
    ).toBeVisible();
  });

  it("shows a product request error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));

    render(<Home />);

    expect(
      await screen.findByText("商品加载失败 / Failed to load products"),
    ).toBeVisible();
  });

  it("retries a failed product request without losing the chat draft", async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce(productResponse());
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<Home />);
    await screen.findByText("商品加载失败 / Failed to load products");
    const input = screen.getByLabelText("消息 / Message");
    await user.type(input, "保留这段草稿");

    await user.click(screen.getByRole("button", { name: "重试 / Retry" }));

    expect(await screen.findByText(PRODUCT.name)).toBeVisible();
    expect(input).toHaveValue("保留这段草稿");
  });

  it("disables chat submission for whitespace-only input", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(productResponse()));
    const user = userEvent.setup();
    render(<Home />);
    await screen.findByText(PRODUCT.name);

    await user.type(screen.getByLabelText("消息 / Message"), "   ");

    expect(screen.getByRole("button", { name: "发送 / Send" })).toBeDisabled();
  });

  it("grows the latest chat reply as SSE deltas arrive", async () => {
    const chat = controlledChatResponse();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(productResponse())
      .mockResolvedValueOnce(chat.response);
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<Home />);
    await screen.findByText(PRODUCT.name);

    await user.type(screen.getByLabelText("消息 / Message"), "推荐商品");
    await user.click(screen.getByRole("button", { name: "发送 / Send" }));

    await act(async () => chat.delta("这里有"));
    expect(await screen.findByText("这里有")).toBeVisible();
    await act(async () => {
      chat.delta("两个商品。");
      chat.done();
    });
    expect(await screen.findByText("这里有两个商品。")).toBeVisible();
    expect(JSON.parse(fetchMock.mock.calls.at(-1)?.[1]?.body as string)).toEqual({
      messages: [{ role: "user", content: "推荐商品" }],
    });
  });

  it("links to the full multi-turn chat page", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(productResponse()));

    render(<Home />);

    await screen.findByText(PRODUCT.name);
    expect(
      screen.getByRole("link", { name: "打开完整对话 / Open full chat" }),
    ).toHaveAttribute("href", "/chat");
  });

  it("retains the message when chat fails", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(productResponse())
      .mockResolvedValueOnce(
        Response.json({ error: { message: "offline" } }, { status: 502 }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<Home />);
    await screen.findByText(PRODUCT.name);
    const input = screen.getByLabelText("消息 / Message");

    await user.type(input, "请再试一次");
    await user.click(screen.getByRole("button", { name: "发送 / Send" }));

    expect(
      await screen.findByText("聊天请求失败 / Chat request failed"),
    ).toBeVisible();
    expect(input).toHaveValue("请再试一次");
  });

  it("does not clear newer input when an earlier chat request completes", async () => {
    let resolveChat: (response: Response) => void = () => undefined;
    const pendingChatResponse = new Promise<Response>((resolve) => {
      resolveChat = resolve;
    });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(productResponse())
      .mockReturnValueOnce(pendingChatResponse);
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<Home />);
    await screen.findByText(PRODUCT.name);
    const input = screen.getByLabelText("消息 / Message");

    await user.type(input, "第一条");
    await user.click(screen.getByRole("button", { name: "发送 / Send" }));
    await user.type(input, " 新草稿");
    await act(async () => resolveChat(chatResponse(["收到"])));

    expect(await screen.findByText("收到")).toBeVisible();
    expect(input).toHaveValue("第一条 新草稿");
  });
});
