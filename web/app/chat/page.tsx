"use client";

import { FormEvent, useEffect, useRef, useState } from "react";

import { streamChat } from "@/lib/chat";
import type { ChatMessage } from "@/lib/types";
import styles from "./page.module.css";


interface DisplayMessage extends ChatMessage {
  id: number;
}


export default function ChatPage() {
  const [messages, setMessages] = useState<DisplayMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState(false);
  const nextId = useRef(0);
  const endMarker = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endMarker.current?.scrollIntoView?.({ behavior: "smooth", block: "end" });
  }, [messages]);

  async function submitMessage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const content = draft.trim();
    if (!content || streaming) return;

    const userMessage: DisplayMessage = {
      id: ++nextId.current,
      role: "user",
      content,
    };
    const assistantMessage: DisplayMessage = {
      id: ++nextId.current,
      role: "assistant",
      content: "",
    };
    const history: ChatMessage[] = [
      ...messages.map(({ role, content: previousContent }) => ({
        role,
        content: previousContent,
      })),
      { role: "user", content },
    ];

    setMessages((current) => [...current, userMessage, assistantMessage]);
    setDraft("");
    setError(false);
    setStreaming(true);

    try {
      await streamChat(history, (delta) => {
        setMessages((current) =>
          current.map((message) =>
            message.id === assistantMessage.id
              ? { ...message, content: `${message.content}${delta}` }
              : message,
          ),
        );
      });
    } catch {
      setError(true);
      setMessages((current) =>
        current.filter(
          (message) =>
            message.id !== assistantMessage.id || message.content.length > 0,
        ),
      );
    } finally {
      setStreaming(false);
    }
  }

  return (
    <main className={styles.page}>
      <div className={styles.shell}>
        <header className={styles.header}>
          <a className={styles.backLink} href="/">
            <span aria-hidden="true">← </span>
            返回商城 / Back to store
          </a>
          <p className={styles.eyebrow}>SSE Agent Playground · 流式对话演示</p>
          <div className={styles.titleRow}>
            <div>
              <h1>Agent 对话 / Agent Chat</h1>
              <p>
                完整对话历史会随每次请求发送，当前回复内容固定。
                Full history is sent with every request; replies are fixed for now.
              </p>
            </div>
            <span className={styles.badge}>SSE · Demo</span>
          </div>
        </header>

        <section
          className={styles.conversation}
          aria-label="对话记录 / Conversation history"
          aria-live="polite"
        >
          {messages.length === 0 && (
            <div className={styles.empty}>
              <strong>开始一段对话 / Start a conversation</strong>
              <span>试着询问商品推荐。Try asking for a product recommendation.</span>
            </div>
          )}

          {messages.map((message) => (
            <article
              className={`${styles.message} ${
                message.role === "user" ? styles.userMessage : styles.agentMessage
              }`}
              key={message.id}
            >
              <span className={styles.messageLabel}>
                {message.role === "user" ? "你 / You" : "Agent"}
              </span>
              <p>
                {message.content || "正在思考… / Thinking…"}
              </p>
            </article>
          ))}
          <div ref={endMarker} />
        </section>

        {error && (
          <p
            className={styles.error}
            role="alert"
            aria-label="回复失败，请稍后重试 / Reply failed, please try again"
          >
            回复失败，请稍后重试 / Reply failed, please try again
          </p>
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
              disabled={streaming}
            />
            <button disabled={!draft.trim() || streaming} type="submit">
              {streaming ? "回复中… / Replying…" : "发送 / Send"}
            </button>
          </div>
        </form>
      </div>
    </main>
  );
}
