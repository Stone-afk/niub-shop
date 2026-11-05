import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import ChatPage from "./page";


afterEach(() => vi.unstubAllGlobals());


function chatResponse(deltas: string[]): Response {
  const events = deltas
    .map((content) => `event: delta\ndata: ${JSON.stringify({ content })}\n\n`)
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


describe("multi-turn chat page", () => {
  it("shows bilingual guidance, an empty conversation, and store navigation", async () => {
    const user = userEvent.setup();
    render(<ChatPage />);

    expect(
      screen.getByRole("heading", { name: "Agent 对话 / Agent Chat" }),
    ).toBeVisible();
    expect(
      screen.getByText(
        "根据你的输入模拟流式回复 / Streams a simulated reply from your input",
      ),
    ).toBeVisible();
    expect(screen.getByText("开始一段对话 / Start a conversation")).toBeVisible();
    expect(
      screen.getByRole("link", { name: "返回商城 / Back to store" }),
    ).toHaveAttribute("href", "/");

    await user.type(screen.getByLabelText("消息 / Message"), "   ");
    expect(screen.getByRole("button", { name: "发送 / Send" })).toBeDisabled();
  });

  it("shows the user turn and grows one Agent bubble while streaming", async () => {
    const chat = controlledChatResponse();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(chat.response));
    const user = userEvent.setup();
    render(<ChatPage />);
    const input = screen.getByLabelText("消息 / Message");

    await user.type(input, "推荐一件商品");
    await user.click(screen.getByRole("button", { name: "发送 / Send" }));

    expect(screen.getByText("推荐一件商品")).toBeVisible();
    expect(input).toBeDisabled();
    expect(screen.getByRole("button", { name: "回复中… / Replying…" })).toBeDisabled();

    await act(async () => chat.delta("你好，"));
    expect(await screen.findByText("你好，")).toBeVisible();
    await act(async () => {
      chat.delta("我是助手。");
      chat.done();
    });

    expect(await screen.findByText("你好，我是助手。")).toBeVisible();
    expect(input).toBeEnabled();
  });

  it("announces a streaming request error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(Response.json({ error: "offline" }, { status: 502 })),
    );
    const user = userEvent.setup();
    render(<ChatPage />);

    await user.type(screen.getByLabelText("消息 / Message"), "你好");
    await user.click(screen.getByRole("button", { name: "发送 / Send" }));

    expect(
      await screen.findByRole("alert", {
        name: "回复失败，请稍后重试 / Reply failed, please try again",
      }),
    ).toBeVisible();
  });

  it("sends complete ordered history on the second turn", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(chatResponse(["第一条固定回复"]))
      .mockResolvedValueOnce(chatResponse(["第二条固定回复"]));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<ChatPage />);
    const input = screen.getByLabelText("消息 / Message");

    await user.type(input, "第一问");
    await user.click(screen.getByRole("button", { name: "发送 / Send" }));
    expect(await screen.findByText("第一条固定回复")).toBeVisible();

    await user.type(input, "第二问");
    await user.click(screen.getByRole("button", { name: "发送 / Send" }));
    expect(await screen.findByText("第二条固定回复")).toBeVisible();

    expect(JSON.parse(fetchMock.mock.calls[1][1].body as string)).toEqual({
      messages: [
        { role: "user", content: "第一问" },
        { role: "assistant", content: "第一条固定回复" },
        { role: "user", content: "第二问" },
      ],
    });
  });

  it("recovers from premature EOF without sending an empty assistant turn", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response("", {
          headers: { "content-type": "text/event-stream" },
        }),
      )
      .mockResolvedValueOnce(chatResponse(["恢复成功"]));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<ChatPage />);
    const input = screen.getByLabelText("消息 / Message");

    await user.type(input, "第一问");
    await user.click(screen.getByRole("button", { name: "发送 / Send" }));
    expect(
      await screen.findByRole("alert", {
        name: "回复失败，请稍后重试 / Reply failed, please try again",
      }),
    ).toBeVisible();

    await user.type(input, "第二问");
    await user.click(screen.getByRole("button", { name: "发送 / Send" }));
    expect(await screen.findByText("恢复成功")).toBeVisible();
    expect(JSON.parse(fetchMock.mock.calls[1][1].body as string)).toEqual({
      messages: [
        { role: "user", content: "第一问" },
        { role: "user", content: "第二问" },
      ],
    });
  });
});
