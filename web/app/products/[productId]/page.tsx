"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";

import { listProducts } from "@/lib/products";
import type { Product } from "@/lib/types";

function formatPrice(product: Product): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: product.currency,
    currencyDisplay: "code",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(product.price_cents / 100);
}

export default function ProductDetailPage() {
  const params = useParams<{ productId: string }>();
  const productId = decodeURIComponent(params.productId);
  const [product, setProduct] = useState<Product | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    listProducts()
      .then((items) => setProduct(items.find((item) => item.id === productId) ?? null))
      .catch(() => setFailed(true))
      .finally(() => setLoading(false));
  }, [productId]);

  return (
    <main style={{ maxWidth: 820, margin: "0 auto", padding: "48px 24px" }}>
      <a href="/chat">← 返回对话 / Back to chat</a>
      {loading && <p>正在加载商品… / Loading product…</p>}
      {!loading && failed && <p role="alert">商品加载失败 / Failed to load product</p>}
      {!loading && !failed && !product && (
        <p role="alert">商品不存在 / Product not found</p>
      )}
      {!loading && !failed && product && (
        <article style={{ marginTop: 32 }}>
          <p>{product.category}</p>
          <h1>{product.name}</h1>
          <p style={{ fontSize: "1.5rem", fontWeight: 700 }}>{formatPrice(product)}</p>
          <p>{product.description}</p>
          <p>{product.availability === "in_stock" ? "有货 / In stock" : "暂时缺货 / Out of stock"}</p>
          <p>标签 / Tags：{product.tags.join(" · ")}</p>
        </article>
      )}
    </main>
  );
}
