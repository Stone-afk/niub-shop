import { afterEach, expect, it, vi } from "vitest";

import { GET } from "./route";

afterEach(() => vi.unstubAllGlobals());

it("URL-encodes the conversation id and preserves JSON status", async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    Response.json({ error: { code: "conversation_not_found" } }, { status: 404 }),
  );
  vi.stubGlobal("fetch", fetchMock);

  const response = await GET(
    new Request("http://web.test/api/conversations/a%2Fb"),
    { params: Promise.resolve({ conversationId: "a/b" }) },
  );

  expect(fetchMock).toHaveBeenCalledWith(
    "http://localhost:8002/conversations/a%2Fb",
    { method: "GET" },
  );
  expect(response.status).toBe(404);
});

