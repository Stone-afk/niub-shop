import { afterEach, expect, it, vi } from "vitest";

import { GET, POST } from "./route";

afterEach(() => {
  vi.unstubAllGlobals();
  delete process.env.AGENT_SERVICE_URL;
});

it("proxies conversation list and creation with normalized URLs", async () => {
  process.env.AGENT_SERVICE_URL = "http://agent.test///";
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(Response.json([]))
    .mockResolvedValueOnce(Response.json({ id: "c1" }, { status: 201 }));
  vi.stubGlobal("fetch", fetchMock);

  const listed = await GET(new Request("http://web.test/api/conversations"));
  const created = await POST(
    new Request("http://web.test/api/conversations", { method: "POST" }),
  );

  expect(fetchMock).toHaveBeenNthCalledWith(1, "http://agent.test/conversations", {
    method: "GET",
  });
  expect(fetchMock).toHaveBeenNthCalledWith(2, "http://agent.test/conversations", {
    body: "",
    method: "POST",
  });
  expect(listed.status).toBe(200);
  expect(created.status).toBe(201);
});

