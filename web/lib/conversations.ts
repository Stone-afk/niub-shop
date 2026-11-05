import { consumeChatStream } from "./chat";
import type { ConversationDetail, ConversationSummary } from "./types";

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = init ? await fetch(url, init) : await fetch(url);
  if (!response.ok) throw new Error("Conversation request failed");
  try {
    return (await response.json()) as T;
  } catch {
    throw new Error("Invalid conversation response");
  }
}

export function listConversations(): Promise<ConversationSummary[]> {
  return requestJson<ConversationSummary[]>("/api/conversations");
}

export function createConversation(): Promise<ConversationSummary> {
  return requestJson<ConversationSummary>("/api/conversations", {
    method: "POST",
  });
}

export function getConversation(id: string): Promise<ConversationDetail> {
  return requestJson<ConversationDetail>(
    `/api/conversations/${encodeURIComponent(id)}`,
  );
}

export async function streamConversationTurn(
  conversationId: string,
  content: string,
  onDelta: (content: string) => void,
): Promise<void> {
  const response = await fetch(
    `/api/conversations/${encodeURIComponent(conversationId)}/turns`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ content }),
    },
  );
  return consumeChatStream(response, onDelta, "Conversation request failed");
}

export async function retryConversationTurn(
  conversationId: string,
  turnId: string,
  onDelta: (content: string) => void,
): Promise<void> {
  const response = await fetch(
    `/api/conversations/${encodeURIComponent(conversationId)}/turns/${encodeURIComponent(turnId)}/retry`,
    { method: "POST" },
  );
  return consumeChatStream(response, onDelta, "Conversation request failed");
}
