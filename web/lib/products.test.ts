import { afterEach, describe, expect, it, vi } from "vitest";

import { listProducts } from "./products";


afterEach(() => vi.unstubAllGlobals());


const product = {
  id: "mug",
  name: "陶瓷杯 / Ceramic Mug",
  description: "适合咖啡 / For coffee",
  price_cents: 39000,
  currency: "TWD",
  category: "drinkware",
  tags: ["coffee", "tea"],
  availability: "in_stock",
};


describe("listProducts", () => {
  it("returns the validated mall catalog", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ items: [product] })));

    await expect(listProducts()).resolves.toEqual([product]);
    expect(fetch).toHaveBeenCalledWith("/api/products");
  });

  it.each([
    ["missing items", {}],
    ["non-array items", { items: {} }],
    ["missing field", { items: [{ ...product, name: undefined }] }],
    ["invalid tags", { items: [{ ...product, tags: "coffee" }] }],
    ["invalid availability", { items: [{ ...product, availability: "backorder" }] }],
    ["invalid price", { items: [{ ...product, price_cents: -1 }] }],
  ])("rejects %s", async (_name, payload) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json(payload)));
    await expect(listProducts()).rejects.toThrow("Invalid product response");
  });

  it("rejects a non-success response", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 502 })));
    await expect(listProducts()).rejects.toThrow("Product request failed");
  });
});
