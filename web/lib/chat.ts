import type { ChatMessage } from "./types";


function normalizeNewlines(value: string, complete = false): string {
  const normalized = value.replace(/\r\n/g, "\n");
  return complete
    ? normalized.replace(/\r/g, "\n")
    : normalized.replace(/\r(?!$)/g, "\n");
}


function readField(line: string, field: string): string | null {
  const prefix = `${field}:`;
  if (!line.startsWith(prefix)) return null;
  return line.slice(prefix.length).replace(/^ /, "");
}


export interface ConsumeChatStreamOptions {
  onRecommendations?: (ids: string[]) => void;
  requireRecommendations?: boolean;
  requestError?: string;
}


type StreamState = { seenRecommendations: boolean };


function handleFrame(
  frame: string,
  onDelta: (content: string) => void,
  options: ConsumeChatStreamOptions,
  state: StreamState,
): boolean {
  const lines = frame.split("\n");
  const event = lines
    .map((line) => readField(line, "event"))
    .find((value) => value !== null);
  if (event === "done") {
    if (options.requireRecommendations && !state.seenRecommendations) {
      throw new Error("Invalid chat stream");
    }
    return true;
  }
  if (event === "error") throw new Error("Model stream failed");

  const data = lines
    .map((line) => readField(line, "data"))
    .filter((value): value is string => value !== null)
    .join("\n");
  if (event === "recommendations") {
    if (state.seenRecommendations) throw new Error("Invalid chat stream");
    try {
      const payload = JSON.parse(data) as { product_ids?: unknown };
      if (
        !Array.isArray(payload.product_ids) ||
        payload.product_ids.some((id) => typeof id !== "string" || !id) ||
        new Set(payload.product_ids).size !== payload.product_ids.length
      ) {
        throw new Error("invalid products");
      }
      state.seenRecommendations = true;
      options.onRecommendations?.([...payload.product_ids]);
      return false;
    } catch {
      throw new Error("Invalid chat stream");
    }
  }
  if (event !== "delta" || state.seenRecommendations) {
    throw new Error("Invalid chat stream");
  }
  try {
    const payload = JSON.parse(data) as { content?: unknown };
    if (typeof payload.content !== "string") throw new Error("missing content");
    onDelta(payload.content);
  } catch {
    throw new Error("Invalid chat stream");
  }
  return false;
}


export async function consumeChatStream(
  response: Response,
  onDelta: (content: string) => void,
  optionsOrRequestError: ConsumeChatStreamOptions | string = {},
): Promise<void> {
  const options = typeof optionsOrRequestError === "string"
    ? { requestError: optionsOrRequestError }
    : optionsOrRequestError;
  const requestError = options.requestError ?? "Chat request failed";
  if (!response.ok) throw new Error(requestError);
  if (!response.body) throw new Error("Chat stream unavailable");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  const state: StreamState = { seenRecommendations: false };

  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      buffer += decoder.decode();
      buffer = normalizeNewlines(buffer, true);
    } else {
      buffer += decoder.decode(value, { stream: true });
      buffer = normalizeNewlines(buffer);
    }

    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      const frame = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      if (handleFrame(frame, onDelta, options, state)) {
        await reader.cancel();
        return;
      }
      boundary = buffer.indexOf("\n\n");
    }

    if (done) throw new Error("Chat stream ended unexpectedly");
  }
}


export async function streamChat(
  messages: ChatMessage[],
  onDelta: (content: string) => void,
): Promise<void> {
  const response = await fetch("/api/chat", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ messages }),
  });
  return consumeChatStream(response, onDelta);
}
