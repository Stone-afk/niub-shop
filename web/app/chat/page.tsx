"use client";

import { FormEvent, useEffect, useRef, useState } from "react";

import {
  createConversation,
  getConversation,
  listConversations,
  retryConversationTurn,
  streamConversationTurn,
} from "@/lib/conversations";
import { listProducts } from "@/lib/products";
import type { ConversationDetail, ConversationSummary, Product } from "@/lib/types";
import styles from "./page.module.css";

const NEW_CHAT_KEY = "__new_chat__";

type StreamView = {
  userContent: string;
  assistantContent: string;
  showUser: boolean;
  productIds: string[];
  productSources: string[];
};

function formatPrice(product: Product): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: product.currency,
    currencyDisplay: "code",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(product.price_cents / 100);
}

function ProductCards({
  ids,
  sources = [],
  catalog,
  catalogError,
}: {
  ids: string[];
  sources?: string[];
  catalog: Map<string, Product> | null;
  catalogError: boolean;
}) {
  if (ids.length === 0) return null;
  if (catalogError) {
    return <p className={styles.productState}>商品信息加载失败 / Failed to load products</p>;
  }
  if (!catalog) {
    return <p className={styles.productState}>正在加载商品… / Loading products…</p>;
  }
  return (
    <ul className={styles.productGrid} aria-label="推荐商品 / Recommended products">
      {ids.map((id, index) => {
        const product = catalog.get(id);
        if (!product) {
          return (
            <li className={styles.unavailableProduct} key={id}>
              <strong>商品暂不可用 / Product unavailable</strong>
              <span>{id}</span>
            </li>
          );
        }
        return (
          <li className={styles.productCard} key={id}>
            <strong>{product.name}</strong>
            {sources[index] === "sponsored" && (
              <span className={styles.sponsored}>广告 / Sponsored</span>
            )}
            <span className={styles.productPrice}>{formatPrice(product)}</span>
            <p>{product.description}</p>
            <span
              className={product.availability === "in_stock" ? styles.inStock : styles.outOfStock}
            >
              {product.availability === "in_stock"
                ? "有货 / In stock"
                : "暂时缺货 / Out of stock"}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

export default function ChatPage() {
  const [summaries, setSummaries] = useState<ConversationSummary[]>([]);
  const [details, setDetails] = useState<Record<string, ConversationDetail>>({});
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [listLoading, setListLoading] = useState(true);
  const [listError, setListError] = useState(false);
  const [detailLoading, setDetailLoading] = useState<string | null>(null);
  const [busy, setBusy] = useState<Record<string, boolean>>({});
  const [streams, setStreams] = useState<Record<string, StreamView>>({});
  const [operationErrors, setOperationErrors] = useState<Record<string, boolean>>({});
  const [catalog, setCatalog] = useState<Map<string, Product> | null>(null);
  const [catalogError, setCatalogError] = useState(false);
  const endMarker = useRef<HTMLDivElement>(null);
  const requestedDetails = useRef(new Set<string>());

  useEffect(() => {
    let active = true;
    listConversations()
      .then((items) => {
        if (!active) return;
        setSummaries(items);
        if (items.length > 0) setSelectedId(items[0].id);
      })
      .catch(() => {
        if (active) setListError(true);
      })
      .finally(() => {
        if (active) setListLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    let active = true;
    listProducts()
      .then((products) => {
        if (active) setCatalog(new Map(products.map((product) => [product.id, product])));
      })
      .catch(() => {
        if (active) setCatalogError(true);
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!selectedId || details[selectedId] || requestedDetails.current.has(selectedId)) return;
    requestedDetails.current.add(selectedId);
    let active = true;
    setDetailLoading(selectedId);
    getConversation(selectedId)
      .then((detail) => {
        if (active) setDetails((current) => ({ ...current, [selectedId]: detail }));
      })
      .catch(() => {
        if (active) setOperationErrors((current) => ({ ...current, [selectedId]: true }));
      })
      .finally(() => {
        if (active) setDetailLoading((current) => current === selectedId ? null : current);
      });
    return () => {
      active = false;
    };
  }, [details, selectedId]);

  useEffect(() => {
    endMarker.current?.scrollIntoView?.({ behavior: "smooth", block: "end" });
  }, [selectedId, details, streams]);

  async function refreshConversation(conversationId: string) {
    const [detail, items] = await Promise.all([
      getConversation(conversationId),
      listConversations(),
    ]);
    setDetails((current) => ({ ...current, [conversationId]: detail }));
    setSummaries(items);
  }

  function appendDelta(conversationId: string, delta: string) {
    setStreams((current) => {
      const stream = current[conversationId];
      if (!stream) return current;
      return {
        ...current,
        [conversationId]: {
          ...stream,
          assistantContent: `${stream.assistantContent}${delta}`,
        },
      };
    });
  }

  function setRecommendations(
    conversationId: string,
    productIds: string[],
    productSources: string[],
  ) {
    setStreams((current) => {
      const stream = current[conversationId];
      if (!stream) return current;
      return { ...current, [conversationId]: { ...stream, productIds, productSources } };
    });
  }

  async function submitMessage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const content = draft.trim();
    const initialKey = selectedId ?? NEW_CHAT_KEY;
    if (!content || busy[initialKey]) return;

    setDraft("");
    setBusy((current) => ({ ...current, [initialKey]: true }));
    setOperationErrors((current) => ({ ...current, [initialKey]: false }));
    let conversationId = selectedId;
    try {
      if (!conversationId) {
        const created = await createConversation();
        conversationId = created.id;
        setSelectedId(created.id);
        setSummaries((current) => [created, ...current]);
        setBusy((current) => {
          const { [NEW_CHAT_KEY]: _removed, ...rest } = current;
          return { ...rest, [created.id]: true };
        });
      }
      const targetId = conversationId;
      setStreams((current) => ({
        ...current,
        [targetId]: {
          userContent: content,
          assistantContent: "",
          showUser: true,
          productIds: [],
          productSources: [],
        },
      }));
      await streamConversationTurn(
        targetId,
        content,
        (delta) => appendDelta(targetId, delta),
        (ids, sources) => setRecommendations(targetId, ids, sources),
      );
    } catch {
      const errorKey = conversationId ?? NEW_CHAT_KEY;
      setOperationErrors((current) => ({ ...current, [errorKey]: true }));
    } finally {
      if (conversationId) {
        try {
          await refreshConversation(conversationId);
        } catch {
          setOperationErrors((current) => ({ ...current, [conversationId!]: true }));
        }
      }
      const finalKey = conversationId ?? NEW_CHAT_KEY;
      setBusy((current) => ({ ...current, [initialKey]: false, [finalKey]: false }));
      if (conversationId) {
        setStreams((current) => {
          const { [conversationId!]: _removed, ...rest } = current;
          return rest;
        });
      }
    }
  }

  async function retry(turnId: string) {
    if (!selectedId || busy[selectedId]) return;
    const conversationId = selectedId;
    setBusy((current) => ({ ...current, [conversationId]: true }));
    setOperationErrors((current) => ({ ...current, [conversationId]: false }));
    setStreams((current) => ({
      ...current,
      [conversationId]: {
        userContent: "",
        assistantContent: "",
        showUser: false,
        productIds: [],
        productSources: [],
      },
    }));
    try {
      await retryConversationTurn(
        conversationId,
        turnId,
        (delta) => appendDelta(conversationId, delta),
        (ids, sources) => setRecommendations(conversationId, ids, sources),
      );
    } catch {
      setOperationErrors((current) => ({ ...current, [conversationId]: true }));
    } finally {
      try {
        await refreshConversation(conversationId);
      } catch {
        setOperationErrors((current) => ({ ...current, [conversationId]: true }));
      }
      setBusy((current) => ({ ...current, [conversationId]: false }));
      setStreams((current) => {
        const { [conversationId]: _removed, ...rest } = current;
        return rest;
      });
    }
  }

  const detail = selectedId ? details[selectedId] : undefined;
  const stream = selectedId ? streams[selectedId] : streams[NEW_CHAT_KEY];
  const currentKey = selectedId ?? NEW_CHAT_KEY;
  const isBusy = Boolean(busy[currentKey]);

  return (
    <main className={styles.page}>
      <div className={styles.shell}>
        <header className={styles.header}>
          <a className={styles.backLink} href="/">
            <span aria-hidden="true">← </span>返回商城 / Back to store
          </a>
          <div className={styles.titleRow}>
            <div>
              <p className={styles.eyebrow}>持久化 Agent 对话 / Persistent Agent chat</p>
              <h1>Agent 对话 / Agent Chat</h1>
              <p>历史记录保存在本地服务，可随时继续。History is saved by the local service and can be continued.</p>
            </div>
            <span className={styles.badge}>SSE · SQLite</span>
          </div>
        </header>

        <div className={styles.workspace}>
          <nav className={styles.sidebar} aria-label="历史对话 / Conversation history">
            <div className={styles.sidebarHeader}>
              <h2>历史对话 / History</h2>
              <button type="button" onClick={() => setSelectedId(null)}>
                新对话 / New chat
              </button>
            </div>
            {listLoading && <p className={styles.sidebarState}>正在加载历史… / Loading history…</p>}
            {listError && (
              <p className={styles.sidebarState} role="alert" aria-label="历史加载失败 / Failed to load history">
                历史加载失败 / Failed to load history
              </p>
            )}
            {!listLoading && !listError && summaries.length === 0 && (
              <p className={styles.sidebarState}>还没有历史对话 / No conversation history yet</p>
            )}
            <div className={styles.historyList}>
              {summaries.map((summary) => (
                <button
                  type="button"
                  className={summary.id === selectedId ? styles.selectedHistory : styles.historyItem}
                  aria-current={summary.id === selectedId ? "true" : undefined}
                  key={summary.id}
                  onClick={() => setSelectedId(summary.id)}
                >
                  <strong>{summary.title}</strong>
                  <span>{summary.last_turn_status === "failed" ? "回复失败 / Failed" : "已保存 / Saved"}</span>
                </button>
              ))}
            </div>
          </nav>

          <section className={styles.chatPanel}>
            <div className={styles.conversation} aria-label="消息记录 / Message history" aria-live="polite">
              {detailLoading === selectedId && <div className={styles.empty}>正在加载对话… / Loading conversation…</div>}
              {!detailLoading && (!detail || detail.turns.length === 0) && !stream && (
                <div className={styles.empty}>
                  <strong>开始一段对话 / Start a conversation</strong>
                  <span>试着询问商品推荐。Try asking for a product recommendation.</span>
                </div>
              )}
              {detail?.turns.map((turn, index) => (
                <div className={styles.turn} key={turn.id}>
                  <article className={`${styles.message} ${styles.userMessage}`}>
                    <span className={styles.messageLabel}>你 / You</span>
                    <p>{turn.user_content}</p>
                  </article>
                  {turn.status === "completed" && (
                    <>
                      <article className={`${styles.message} ${styles.agentMessage}`}>
                        <span className={styles.messageLabel}>Agent / 智能助手</span>
                        <p>{turn.assistant_content}</p>
                      </article>
                      <ProductCards
                        ids={turn.recommended_product_ids ?? []}
                        sources={turn.recommended_product_sources ?? []}
                        catalog={catalog}
                        catalogError={catalogError}
                      />
                    </>
                  )}
                  {turn.status === "failed" && (
                    <article className={`${styles.message} ${styles.failedMessage}`}>
                      <span className={styles.messageLabel}>Agent / 智能助手</span>
                      <p>回复失败，可重试 / Reply failed, retry available</p>
                      {index === detail.turns.length - 1 && (
                        <button type="button" disabled={isBusy} onClick={() => retry(turn.id)}>
                          重试 / Retry
                        </button>
                      )}
                    </article>
                  )}
                </div>
              ))}
              {stream && (
                <div className={styles.turn}>
                  {stream.showUser && (
                    <article className={`${styles.message} ${styles.userMessage}`}>
                      <span className={styles.messageLabel}>你 / You</span>
                      <p>{stream.userContent}</p>
                    </article>
                  )}
                  <article className={`${styles.message} ${styles.agentMessage}`}>
                    <span className={styles.messageLabel}>Agent / 智能助手</span>
                    <p>{stream.assistantContent || "正在思考… / Thinking…"}</p>
                  </article>
                  <ProductCards
                    ids={stream.productIds}
                    sources={stream.productSources}
                    catalog={catalog}
                    catalogError={catalogError}
                  />
                </div>
              )}
              <div ref={endMarker} />
            </div>

            {operationErrors[currentKey] && (
              <p className={styles.error} role="alert">操作失败，请重试 / Operation failed, please retry</p>
            )}

            <form className={styles.composer} onSubmit={submitMessage}>
              <label htmlFor="chat-message">消息 / Message</label>
              <div className={styles.composerRow}>
                <textarea
                  id="chat-message"
                  value={draft}
                  onChange={(event) => setDraft(event.target.value)}
                  placeholder="输入你的问题… / Ask anything about the shop…"
                  rows={2}
                  disabled={isBusy}
                />
                <button disabled={!draft.trim() || isBusy} type="submit">
                  {isBusy ? "回复中… / Replying…" : "发送 / Send"}
                </button>
              </div>
            </form>
          </section>
        </div>
      </div>
    </main>
  );
}
