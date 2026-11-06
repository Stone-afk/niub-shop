"use client";

import { useEffect, useState } from "react";
import styles from "./page.module.css";

type OrderItem = {
  product_id: string;
  name: string;
  description: string;
  quantity: number;
  unit_price_cents: number;
  line_total_cents: number;
  currency: string;
};

type Order = {
  id: string;
  status: "pending_payment" | "paid";
  items: OrderItem[];
  subtotal_cents: number;
  total_cents: number;
  currency: string;
  created_at: string;
};

function formatPrice(cents: number, currency: string): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency,
    currencyDisplay: "code",
    minimumFractionDigits: 2,
  }).format(cents / 100);
}

export default function OrderPreviewPage({
  params,
}: {
  params: Promise<{ orderId: string }>;
}) {
  const [orderId, setOrderId] = useState<string | null>(null);
  const [order, setOrder] = useState<Order | null>(null);
  const [loading, setLoading] = useState(true);
  const [paying, setPaying] = useState(false);
  const [error, setError] = useState(false);

  async function load(id: string) {
    setLoading(true);
    try {
      const response = await fetch(`/api/orders/${encodeURIComponent(id)}`);
      if (!response.ok) throw new Error("order request failed");
      setOrder((await response.json()) as Order);
      setError(false);
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    let active = true;
    params.then(({ orderId: id }) => {
      if (!active) return;
      setOrderId(id);
      void load(id);
    });
    return () => {
      active = false;
    };
  }, [params]);

  async function pay() {
    if (!orderId || paying) return;
    setPaying(true);
    try {
      const response = await fetch(`/api/orders/${encodeURIComponent(orderId)}/pay`, {
        method: "POST",
      });
      if (!response.ok) throw new Error("payment failed");
      setOrder((await response.json()) as Order);
      setError(false);
    } catch {
      setError(true);
    } finally {
      setPaying(false);
    }
  }

  return (
    <main className={styles.page}>
      <section className={styles.card} aria-live="polite">
        <a href="/chat" className={styles.backLink}>返回对话 / Back to chat</a>
        <p className={styles.eyebrow}>订单预览 / Order preview</p>
        <h1>确认订单 / Review order</h1>
        {loading && <p>正在加载订单… / Loading order…</p>}
        {error && (
          <p className={styles.error} role="alert">
            操作失败，请重试 / Operation failed, please retry
          </p>
        )}
        {!loading && order && (
          <>
            <p className={styles.orderId}>订单号 / Order ID：{order.id}</p>
            <p className={order.status === "paid" ? styles.paid : styles.pending}>
              {order.status === "paid" ? "已支付 / Paid" : "待支付 / Pending payment"}
            </p>
            <ul className={styles.items}>
              {order.items.map((item) => (
                <li key={item.product_id}>
                  <div>
                    <strong>{item.name}</strong>
                    <span>{item.description}</span>
                    <span>数量 / Quantity：{item.quantity}</span>
                  </div>
                  <strong>{formatPrice(item.line_total_cents, item.currency)}</strong>
                </li>
              ))}
            </ul>
            <div className={styles.total}>
              <span>总额 / Total</span>
              <strong>{formatPrice(order.total_cents, order.currency)}</strong>
            </div>
            {order.status === "pending_payment" && (
              <button type="button" onClick={pay} disabled={paying}>
                {paying ? "支付中… / Paying…" : "确认支付（Mock） / Pay (Mock)"}
              </button>
            )}
          </>
        )}
      </section>
    </main>
  );
}
