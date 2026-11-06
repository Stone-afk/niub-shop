import { consumeChatStream } from "./chat";
import type { ConversationDetail, ConversationSummary, OrderLink } from "./types";

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

export async function getConversation(id: string): Promise<ConversationDetail> {
  const detail = await requestJson<ConversationDetail>(
    `/api/conversations/${encodeURIComponent(id)}`,
  );
  if (
    !Array.isArray(detail.turns) ||
    detail.turns.some(
      (turn) =>
        !Array.isArray(turn.recommended_product_ids) ||
        turn.recommended_product_ids.some((productId) => typeof productId !== "string" || !productId) ||
        new Set(turn.recommended_product_ids).size !== turn.recommended_product_ids.length ||
        (turn.recommended_product_sources !== undefined &&
          (!Array.isArray(turn.recommended_product_sources) ||
            turn.recommended_product_sources.length !== turn.recommended_product_ids.length ||
            turn.recommended_product_sources.some(
              (source) => source !== "organic" && source !== "sponsored",
            ))) ||
        (turn.orders !== undefined &&
        (!Array.isArray(turn.orders) ||
          turn.orders.some(
            (order) =>
              !order ||
              typeof order.order_id !== "string" ||
              !order.order_id ||
              typeof order.preview_path !== "string" ||
              !order.preview_path.startsWith("/orders/") ||
              (order.status !== "pending_payment" && order.status !== "paid"),
          ))),
    )
  ) {
    throw new Error("Invalid conversation response");
  }
  return detail;
}

export async function streamConversationTurn(
  conversationId: string,
  content: string,
  onDelta: (content: string) => void,
  onRecommendations?: (ids: string[], sources: string[]) => void,
  onOrder?: (order: OrderLink) => void,
): Promise<void> {
  const response = await fetch(
    `/api/conversations/${encodeURIComponent(conversationId)}/turns`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ content }),
    },
  );
  return consumeChatStream(response, onDelta, {
    onRecommendations,
    onOrder,
    requireRecommendations: true,
    requestError: "Conversation request failed",
  });
}

export async function retryConversationTurn(
  conversationId: string,
  turnId: string,
  onDelta: (content: string) => void,
  onRecommendations?: (ids: string[], sources: string[]) => void,
  onOrder?: (order: OrderLink) => void,
): Promise<void> {
  const response = await fetch(
    `/api/conversations/${encodeURIComponent(conversationId)}/turns/${encodeURIComponent(turnId)}/retry`,
    { method: "POST" },
  );
  return consumeChatStream(response, onDelta, {
    onRecommendations,
    onOrder,
    requireRecommendations: true,
    requestError: "Conversation request failed",
  });
}
