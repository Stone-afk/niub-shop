import { afterEach, expect, it, vi } from "vitest";

import { POST } from "./route";

afterEach(() => vi.unstubAllGlobals());

it("forwards turn JSON and transparently returns SSE", async () => {
  const body = 'event: delta\ndata: {"content":"你好"}\n\n';
  const upstream = new Response(body, {
    headers: { "content-type": "text/event-stream; charset=utf-8" },
  });
  const upstreamBody = upstream.body;
  const fetchMock = vi.fn().mockResolvedValue(upstream);
  vi.stubGlobal("fetch", fetchMock);
  const request = new Request("http://web.test/api/conversations/c%2F1/turns", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ content: "问题" }),
  });

  const response = await POST(request, {
    params: Promise.resolve({ conversationId: "c/1" }),
  });

  expect(fetchMock).toHaveBeenCalledWith(
    "http://localhost:8002/conversations/c%2F1/turns",
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ content: "问题" }),
    },
  );
  expect(response.body).toBe(upstreamBody);
});

