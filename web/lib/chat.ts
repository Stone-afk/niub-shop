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


function handleFrame(frame: string, onDelta: (content: string) => void): boolean {
  const lines = frame.split("\n");
  const event = lines
    .map((line) => readField(line, "event"))
    .find((value) => value !== null);
  if (event === "done") return true;
  if (event !== "delta") return false;

  const data = lines
    .map((line) => readField(line, "data"))
    .filter((value): value is string => value !== null)
    .join("\n");
  try {
    const payload = JSON.parse(data) as { content?: unknown };
    if (typeof payload.content !== "string") throw new Error("missing content");
    onDelta(payload.content);
  } catch {
    throw new Error("Invalid chat stream");
  }
  return false;
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
  if (!response.ok) throw new Error("Chat request failed");
  if (!response.body) throw new Error("Chat stream unavailable");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

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
      if (handleFrame(frame, onDelta)) {
        await reader.cancel();
        return;
      }
      boundary = buffer.indexOf("\n\n");
    }

    if (done) return;
  }
}
