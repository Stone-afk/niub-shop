import { afterEach, describe, expect, it, vi } from "vitest";

import { streamChat } from "./chat";


const HISTORY = [
  { role: "user" as const, content: "推荐商品" },
  { role: "assistant" as const, content: "好的" },
  { role: "user" as const, content: "它多少钱？" },
];


afterEach(() => vi.unstubAllGlobals());


function sseResponse(chunks: string[]): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
  return new Response(body, {
    headers: { "content-type": "text/event-stream" },
  });
}


describe("streamChat", () => {
  it("posts complete history and emits ordered UTF-8 deltas", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      sseResponse([
        'event: delta\ndata: {"content":"你好"}\n\n' +
          'event: delta\ndata: {"content":"，世界"}\n\n' +
          "event: done\ndata: {}\n\n",
      ]),
    );
    vi.stubGlobal("fetch", fetchMock);
    const deltas: string[] = [];

    await streamChat(HISTORY, (content) => deltas.push(content));

    expect(deltas).toEqual(["你好", "，世界"]);
    expect(fetchMock).toHaveBeenCalledWith("/api/chat", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ messages: HISTORY }),
    });
  });

  it("parses an event split across network chunks with CRLF separators", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        sseResponse([
          "event: del",
          'ta\r\ndata: {"content":"分',
          '块"}\r\n\r',
          "\nevent: done\r\ndata: {}\r\n\r\n",
        ]),
      ),
    );
    const deltas: string[] = [];

    await streamChat(HISTORY, (content) => deltas.push(content));

    expect(deltas).toEqual(["分块"]);
  });

  it("stops processing when the done event arrives", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        sseResponse([
          "event: done\ndata: {}\n\n" +
            'event: delta\ndata: {"content":"不应出现"}\n\n',
        ]),
      ),
    );
    const deltas: string[] = [];

    await streamChat(HISTORY, (content) => deltas.push(content));

    expect(deltas).toEqual([]);
  });

  it("rejects malformed delta JSON", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        sseResponse(["event: delta\ndata: not-json\n\n"]),
      ),
    );

    await expect(streamChat(HISTORY, () => undefined)).rejects.toThrow(
      "Invalid chat stream",
    );
  });

  it("rejects a successful response without a body", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null)));

    await expect(streamChat(HISTORY, () => undefined)).rejects.toThrow(
      "Chat stream unavailable",
    );
  });

  it("rejects a non-successful response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(Response.json({ error: "offline" }, { status: 502 })),
    );

    await expect(streamChat(HISTORY, () => undefined)).rejects.toThrow(
      "Chat request failed",
    );
  });
});
