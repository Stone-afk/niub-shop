import { afterEach, expect, it, vi } from "vitest";

import { POST } from "./route";

afterEach(() => vi.unstubAllGlobals());

it("encodes both ids and forwards retry without a request body", async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response("event: done\ndata: {}\n\n", {
      headers: { "content-type": "text/event-stream" },
    }),
  );
  vi.stubGlobal("fetch", fetchMock);

  const response = await POST(
    new Request("http://web.test/retry", { method: "POST" }),
    {
      params: Promise.resolve({ conversationId: "c/1", turnId: "t?1" }),
    },
  );

  expect(fetchMock).toHaveBeenCalledWith(
    "http://localhost:8002/conversations/c%2F1/turns/t%3F1/retry",
    { body: "", method: "POST" },
  );
  expect(response.status).toBe(200);
});
