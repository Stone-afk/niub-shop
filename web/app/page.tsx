"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";

import { streamChat } from "@/lib/chat";
import type { ChatMessage, Product, ProductList } from "@/lib/types";
import styles from "./page.module.css";


type ProductState = "loading" | "ready" | "error";


function formatPrice(product: Product): string {
  const amount = product.price_cents / 100;
  return product.currency === "TWD"
    ? `NT$${amount.toLocaleString("en-US")}`
    : `${product.currency} ${amount.toLocaleString("en-US")}`;
}


export default function Home() {
  const [products, setProducts] = useState<Product[]>([]);
  const [productState, setProductState] = useState<ProductState>("loading");
  const [message, setMessage] = useState("");
  const [reply, setReply] = useState<string | null>(null);
  const [chatError, setChatError] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const loadProducts = useCallback(async () => {
    setProductState("loading");
    try {
      const response = await fetch("/api/products");
      if (!response.ok) throw new Error("product request failed");
      const payload = (await response.json()) as ProductList;
      if (!Array.isArray(payload.items)) throw new Error("invalid product response");
      setProducts(payload.items);
      setProductState("ready");
    } catch {
      setProductState("error");
    }
  }, []);

  useEffect(() => {
    void loadProducts();
  }, [loadProducts]);

  async function submitChat(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const submittedDraft = message;
    const trimmedMessage = message.trim();
    if (!trimmedMessage || submitting) return;

    setSubmitting(true);
    setChatError(false);
    setReply("");
    try {
      const messages: ChatMessage[] = [
        { role: "user", content: trimmedMessage },
      ];
      await streamChat(messages, (content) => {
        setReply((current) => `${current ?? ""}${content}`);
      });
      setMessage((current) => (current === submittedDraft ? "" : current));
    } catch {
      setChatError(true);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className={styles.page}>
      <div className={styles.shell}>
        <p className={styles.eyebrow}>Thoughtful goods · 智选好物</p>
        <h1 className={styles.title}>牛布商城 / Niub Shop</h1>
        <p className={styles.lede}>
          浏览我们的精选商品，或询问智能助手。Explore a focused collection and ask
          our assistant about what is available.
        </p>

        <section aria-labelledby="products-heading">
          <div className={styles.sectionHeader}>
            <h2 id="products-heading">商品 / Products</h2>
            <span>mall-service · :8001</span>
          </div>

          {productState === "loading" && (
            <p className={styles.status} role="status">
              正在加载商品… / Loading products…
            </p>
          )}
          {productState === "error" && (
            <div className={`${styles.status} ${styles.error}`} role="alert">
              <span>商品加载失败 / Failed to load products</span>
              <button className={styles.retry} onClick={() => void loadProducts()}>
                重试 / Retry
              </button>
            </div>
          )}
          {productState === "ready" && products.length === 0 && (
            <p className={styles.status}>暂无商品 / No products available</p>
          )}
          {productState === "ready" && products.length > 0 && (
            <div className={styles.grid}>
              {products.map((product) => (
                <article className={styles.card} key={product.id}>
                  <h3>{product.name}</h3>
                  <p>{product.description}</p>
                  <strong className={styles.price}>{formatPrice(product)}</strong>
                </article>
              ))}
            </div>
          )}
        </section>

        <section className={styles.chat} aria-labelledby="chat-heading">
          <h2 id="chat-heading">智能助手 / Shopping Assistant</h2>
          <p className={styles.chatIntro}>
            询问当前商品信息。Ask about the current catalog.
          </p>
          <a className={styles.chatLink} href="/chat">
            打开完整对话 / Open full chat
          </a>
          <form className={styles.form} onSubmit={submitChat}>
            <label htmlFor="chat-message">消息 / Message</label>
            <input
              id="chat-message"
              value={message}
              onChange={(event) => setMessage(event.target.value)}
              placeholder="想找什么？ / What are you looking for?"
            />
            <button disabled={!message.trim() || submitting} type="submit">
              {submitting ? "发送中… / Sending…" : "发送 / Send"}
            </button>
          </form>
          {chatError && (
            <p className={styles.reply} role="alert">
              聊天请求失败 / Chat request failed
            </p>
          )}
          {reply && (
            <p className={styles.reply} role="status">
              {reply}
            </p>
          )}
        </section>
      </div>
    </main>
  );
}
