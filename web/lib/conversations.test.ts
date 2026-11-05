import { afterEach, describe, expect, it, vi } from "vitest";

import {
  createConversation,
  getConversation,
  listConversations,
  retryConversationTurn,
  streamConversationTurn,
} from "./conversations";

afterEach(() => vi.unstubAllGlobals());

function sseResponse(body: string): Response {
  return new Response(body, {
    headers: { "content-type": "text/event-stream" },
  });
}

const summary = {
  id: "c/1",
  title: "问题",
  created_at: "2026-11-05T00:00:00+00:00",
  updated_at: "2026-11-05T00:00:01+00:00",
  last_turn_status: "completed" as const,
};

describe("conversation JSON client", () => {
  it("lists, creates, and loads an encoded conversation id", async () => {
    const detail = { ...summary, turns: [] };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(Response.json([summary]))
      .mockResolvedValueOnce(Response.json(summary, { status: 201 }))
      .mockResolvedValueOnce(Response.json(detail));
    vi.stubGlobal("fetch", fetchMock);

    await expect(listConversations()).resolves.toEqual([summary]);
    await expect(createConversation()).resolves.toEqual(summary);
    await expect(getConversation("c/1")).resolves.toEqual(detail);

    expect(fetchMock).toHaveBeenNthCalledWith(1, "/api/conversations");
    expect(fetchMock).toHaveBeenNthCalledWith(2, "/api/conversations", {
      method: "POST",
    });
    expect(fetchMock).toHaveBeenNthCalledWith(3, "/api/conversations/c%2F1");
  });

  it("rejects non-successful JSON responses", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(Response.json({ error: "offline" }, { status: 502 })),
    );

    await expect(listConversations()).rejects.toThrow("Conversation request failed");
  });
});

describe("conversation streaming client", () => {
  it("sends only new content and emits ordered UTF-8 deltas", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      sseResponse(
        'event: delta\ndata: {"content":"你好"}\n\n' +
          'event: delta\ndata: {"content":"！"}\n\n' +
          "event: done\ndata: {}\n\n",
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const deltas: string[] = [];

    await streamConversationTurn("c/1", "问题", (chunk) => deltas.push(chunk));

    expect(deltas).toEqual(["你好", "！"]);
    expect(fetchMock).toHaveBeenCalledWith("/api/conversations/c%2F1/turns", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ content: "问题" }),
    });
  });

  it("retries a turn without resending user content", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(sseResponse("event: done\ndata: {}\n\n"));
    vi.stubGlobal("fetch", fetchMock);

    await retryConversationTurn("c/1", "t?1", () => undefined);

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/conversations/c%2F1/turns/t%3F1/retry",
      { method: "POST" },
    );
  });

  it("rejects named error events", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        sseResponse(
          'event: error\ndata: {"code":"model_stream_failed","message":"safe"}\n\n',
        ),
      ),
    );

    await expect(
      streamConversationTurn("c1", "问题", () => undefined),
    ).rejects.toThrow("Model stream failed");
  });

  it.each([
    ["malformed delta", "event: delta\ndata: nope\n\n"],
    ["unexpected event", "event: mystery\ndata: {}\n\n"],
    ["premature EOF", 'event: delta\ndata: {"content":"partial"}\n\n'],
  ])("rejects %s", async (_name, stream) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sseResponse(stream)));

    await expect(
      streamConversationTurn("c1", "问题", () => undefined),
    ).rejects.toThrow();
  });
});
