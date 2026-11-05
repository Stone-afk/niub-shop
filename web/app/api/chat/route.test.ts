import { afterEach, describe, expect, it, vi } from "vitest";

import { POST } from "./route";

afterEach(() => {
  vi.unstubAllGlobals();
  delete process.env.AGENT_SERVICE_URL;
});

describe("POST /api/chat", () => {
  it("forwards the JSON request to the default agent service", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(Response.json({ reply: "hello" }));
    vi.stubGlobal("fetch", fetchMock);
    const request = new Request("http://web.test/api/chat", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ message: "hello" }),
    });

    const response = await POST(request);

    expect(fetchMock).toHaveBeenCalledWith("http://localhost:8002/chat", {
      body: '{"message":"hello"}',
      headers: { "content-type": "application/json" },
      method: "POST",
    });
    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toEqual({ reply: "hello" });
  });

  it("normalizes the configured agent URL and preserves JSON errors", async () => {
    process.env.AGENT_SERVICE_URL = "http://agent.test/";
    const upstreamError = { detail: "message required" };
    const fetchMock = vi.fn().mockResolvedValue(
      Response.json(upstreamError, { status: 422 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const request = new Request("http://web.test/api/chat", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ message: "" }),
    });

    const response = await POST(request);

    expect(fetchMock).toHaveBeenCalledWith(
      "http://agent.test/chat",
      expect.objectContaining({ method: "POST" }),
    );
    expect(response.status).toBe(422);
    await expect(response.json()).resolves.toEqual(upstreamError);
  });
});
