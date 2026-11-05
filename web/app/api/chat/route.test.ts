import { afterEach, describe, expect, it, vi } from "vitest";

import { POST } from "./route";


const HISTORY = {
  messages: [
    { role: "user", content: "推荐商品" },
    { role: "assistant", content: "固定回复" },
    { role: "user", content: "它多少钱？" },
  ],
};


afterEach(() => {
  vi.unstubAllGlobals();
  delete process.env.AGENT_SERVICE_URL;
});


function chatRequest(body = HISTORY): Request {
  return new Request("http://web.test/api/chat", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}


describe("POST /api/chat", () => {
  it("forwards complete history and transparently returns the SSE stream", async () => {
    const eventStream =
      'event: delta\ndata: {"content":"你好"}\n\nevent: done\ndata: {}\n\n';
    const upstream = new Response(eventStream, {
      headers: { "content-type": "text/event-stream; charset=utf-8" },
    });
    const upstreamBody = upstream.body;
    const fetchMock = vi.fn().mockResolvedValue(upstream);
    vi.stubGlobal("fetch", fetchMock);

    const response = await POST(chatRequest());

    expect(fetchMock).toHaveBeenCalledWith("http://localhost:8002/chat", {
      body: JSON.stringify(HISTORY),
      headers: { "content-type": "application/json" },
      method: "POST",
    });
    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toBe(
      "text/event-stream; charset=utf-8",
    );
    expect(response.body).toBe(upstreamBody);
    await expect(response.text()).resolves.toBe(eventStream);
  });

  it("normalizes the configured agent URL and preserves upstream JSON errors", async () => {
    process.env.AGENT_SERVICE_URL = "http://agent.test/";
    const upstreamError = { detail: [{ loc: ["body", "messages"] }] };
    const fetchMock = vi.fn().mockResolvedValue(
      Response.json(upstreamError, { status: 422 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const response = await POST(chatRequest({ messages: [] }));

    expect(fetchMock).toHaveBeenCalledWith(
      "http://agent.test/chat",
      expect.objectContaining({ method: "POST" }),
    );
    expect(response.status).toBe(422);
    expect(response.headers.get("content-type")).toContain("application/json");
    await expect(response.json()).resolves.toEqual(upstreamError);
  });

  it("returns a stable 502 when the agent service cannot be reached", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));

    const response = await POST(chatRequest());

    expect(response.status).toBe(502);
    await expect(response.json()).resolves.toEqual({
      error: {
        code: "upstream_unavailable",
        message: "Upstream service is unavailable",
      },
    });
  });

  it("rejects a successful upstream response that is not SSE", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ reply: "old" })));

    const response = await POST(chatRequest());

    expect(response.status).toBe(502);
    await expect(response.json()).resolves.toEqual({
      error: {
        code: "upstream_unavailable",
        message: "Upstream service is unavailable",
      },
    });
  });
});
