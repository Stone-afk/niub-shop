import { afterEach, describe, expect, it, vi } from "vitest";

import { GET } from "./route";

afterEach(() => {
  vi.unstubAllGlobals();
  delete process.env.MALL_SERVICE_URL;
});

describe("GET /api/products", () => {
  it("forwards to the default mall service and returns JSON", async () => {
    const upstreamBody = { items: [{ id: "canvas-tote" }] };
    const fetchMock = vi.fn().mockResolvedValue(Response.json(upstreamBody));
    vi.stubGlobal("fetch", fetchMock);

    const response = await GET(new Request("http://web.test/api/products"));

    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8001/products",
      expect.objectContaining({ method: "GET" }),
    );
    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toEqual(upstreamBody);
  });

  it("normalizes a trailing slash in the configured mall URL", async () => {
    process.env.MALL_SERVICE_URL = "http://mall.test/";
    const fetchMock = vi.fn().mockResolvedValue(Response.json({ items: [] }));
    vi.stubGlobal("fetch", fetchMock);

    await GET(new Request("http://web.test/api/products"));

    expect(fetchMock).toHaveBeenCalledWith(
      "http://mall.test/products",
      expect.any(Object),
    );
  });

  it("maps a network failure to the stable 502 response", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));

    const response = await GET(new Request("http://web.test/api/products"));

    expect(response.status).toBe(502);
    await expect(response.json()).resolves.toEqual({
      error: {
        code: "upstream_unavailable",
        message: "Upstream service is unavailable",
      },
    });
  });

  it("maps a non-JSON upstream response to 502", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response("bad gateway", {
          status: 503,
          headers: { "content-type": "text/plain" },
        }),
      ),
    );

    const response = await GET(new Request("http://web.test/api/products"));

    expect(response.status).toBe(502);
    await expect(response.json()).resolves.toEqual({
      error: {
        code: "upstream_unavailable",
        message: "Upstream service is unavailable",
      },
    });
  });
});
