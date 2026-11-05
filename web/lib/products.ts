import type { Product, ProductList } from "./types";


function isProduct(value: unknown): value is Product {
  if (!value || typeof value !== "object") return false;
  const item = value as Record<string, unknown>;
  return (
    typeof item.id === "string" && item.id.length > 0 &&
    typeof item.name === "string" &&
    typeof item.description === "string" &&
    typeof item.price_cents === "number" &&
    Number.isInteger(item.price_cents) && item.price_cents >= 0 &&
    typeof item.currency === "string" && item.currency.length > 0 &&
    typeof item.category === "string" &&
    Array.isArray(item.tags) && item.tags.every((tag) => typeof tag === "string") &&
    (item.availability === "in_stock" || item.availability === "out_of_stock")
  );
}


export async function listProducts(): Promise<Product[]> {
  const response = await fetch("/api/products");
  if (!response.ok) throw new Error("Product request failed");
  let payload: ProductList;
  try {
    payload = (await response.json()) as ProductList;
  } catch {
    throw new Error("Invalid product response");
  }
  if (!payload || !Array.isArray(payload.items) || !payload.items.every(isProduct)) {
    throw new Error("Invalid product response");
  }
  return payload.items;
}
