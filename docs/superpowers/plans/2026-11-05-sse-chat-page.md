# SSE Multi-turn Chat Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a bilingual `/chat` page that sends complete conversation history and renders a fixed Agent reply incrementally through SSE.

**Architecture:** `agent-service POST /chat` validates a `messages` array and emits named `delta` and `done` SSE events. The existing Next.js `/api/chat` route becomes a transparent streaming proxy; a shared browser client parses SSE for both the storefront and the new multi-turn page.

**Tech Stack:** Python 3.11, FastAPI, Pydantic, pytest/httpx, Next.js 16, React 19, TypeScript, Vitest, Testing Library, CSS Modules

**Spec:** `docs/superpowers/specs/2026-11-05-sse-chat-page-design.md`

## Global Constraints

- Use `POST /chat` and `POST /api/chat`; do not retain a JSON success-response compatibility path.
- Request shape is `{ messages: Array<{ role: "user" | "assistant"; content: string }> }` and contains the complete current conversation.
- Successful responses use `text/event-stream` with named `delta` events containing `{ "content": string }`, followed by one named `done` event.
- Reply content is fixed, bilingual, and split into multiple chunks; no model, persistence, authentication, Markdown renderer, cancellation, retry, or new dependency.
- Preserve the existing bilingual copy style and Node.js requirement `^20.19.0 || >=22.12.0`.

## Review Focus

- Empty `messages` arrays and whitespace-only content must fail validation without opening a success stream; covered in Task 1.
- Unsupported message roles must return 422; covered in Task 1.
- SSE frames split across arbitrary network chunks must parse without losing or duplicating text; covered in Task 3.
- A successful upstream response with the wrong content type must become a stable 502 rather than being treated as chat text; covered in Task 2.
- A second submission must include all earlier user and assistant messages in order; covered in Task 4.

---

### Task 1: Agent-service SSE contract

**Files:**
- Modify: `agent-service/src/agent_service/app.py`
- Modify: `agent-service/tests/test_app.py`

**Interfaces:**
- Consumes: JSON `{ messages: ChatMessage[] }` where `ChatMessage.role` is `user | assistant` and trimmed `content` is non-empty.
- Produces: `StreamingResponse` with `media_type="text/event-stream"`; exported module constant `FIXED_REPLY_CHUNKS: tuple[str, ...]` defines the deterministic reply.

- [ ] **Step 1: Replace JSON chat tests with failing SSE contract tests**

Add tests named `test_chat_streams_fixed_reply_as_delta_and_done_events`, `test_chat_accepts_complete_history`, `test_empty_messages_returns_422`, `test_whitespace_message_returns_422`, and `test_unknown_role_returns_422`. Assert status, `content-type`, exact event order, reconstructed fixed reply, and validation failures.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `cd agent-service && .venv/bin/pytest tests/test_app.py -v`

Expected: FAIL because `/chat` still accepts `{ message }` and returns JSON.

- [ ] **Step 3: Implement the minimal FastAPI streaming endpoint**

Define `ChatMessage`, change `ChatRequest` to a non-empty `messages` list, and return a `StreamingResponse` whose async iterator serializes every fixed chunk as a `delta` event and terminates with `done`. Remove the chat endpoint's model and mall calls while preserving `create_app(...)` compatibility for existing callers.

- [ ] **Step 4: Run agent-service tests and verify GREEN**

Run: `cd agent-service && .venv/bin/pytest -v`

Expected: all agent-service tests pass.

- [ ] **Step 5: Commit the backend contract**

```bash
git add agent-service/src/agent_service/app.py agent-service/tests/test_app.py
git commit -m "feat: stream fixed agent replies over SSE"
```

### Task 2: Next.js streaming proxy

**Files:**
- Modify: `web/lib/proxy.ts`
- Modify: `web/app/api/chat/route.ts`
- Modify: `web/app/api/chat/route.test.ts`

**Interfaces:**
- Consumes: the browser request body and `AGENT_SERVICE_URL`.
- Produces: `proxyStream(request: Request, upstreamUrl: string): Promise<Response>` that forwards request bytes and streams upstream response bytes, status, and content type without buffering.

- [ ] **Step 1: Write failing proxy tests**

Update route tests to send a complete `messages` array. Assert that an SSE `ReadableStream` remains streamed with exact bytes and `text/event-stream`, configured URLs are normalized, upstream 422 JSON is preserved, network failures return the existing stable 502 body, and a 200 response with a non-SSE content type returns 502.

- [ ] **Step 2: Run the route test and verify RED**

Run: `cd web && npm test -- app/api/chat/route.test.ts`

Expected: FAIL because `proxyJson` buffers and requires JSON.

- [ ] **Step 3: Implement and connect `proxyStream`**

Forward the request method, content type, and raw body. On successful upstream status require `text/event-stream`; return a new `Response` around `upstream.body`. Preserve non-success response body/status/content type, and map fetch failures or invalid successful content types to the stable 502 response.

- [ ] **Step 4: Run the route test and verify GREEN**

Run: `cd web && npm test -- app/api/chat/route.test.ts`

Expected: route tests pass.

- [ ] **Step 5: Commit the streaming proxy**

```bash
git add web/lib/proxy.ts web/app/api/chat/route.ts web/app/api/chat/route.test.ts
git commit -m "feat: proxy chat responses as streams"
```

### Task 3: Shared browser SSE client and storefront migration

**Files:**
- Create: `web/lib/chat.ts`
- Create: `web/lib/chat.test.ts`
- Modify: `web/lib/types.ts`
- Modify: `web/app/page.tsx`
- Modify: `web/app/page.test.tsx`
- Modify: `web/app/page.module.css`

**Interfaces:**
- Consumes: `ChatMessage[]` and callbacks supplied by the calling component.
- Produces: `streamChat(messages: ChatMessage[], onDelta: (content: string) => void): Promise<void>`; `ChatMessage` has `role` and `content` fields.

- [ ] **Step 1: Write failing SSE parser tests**

Test named events, UTF-8 content, multiple events in one read, one event split across reads, `done` termination, malformed delta JSON, missing response body, and non-OK responses. Assert ordered `onDelta` calls and rejected errors where appropriate.

- [ ] **Step 2: Run the client test and verify RED**

Run: `cd web && npm test -- lib/chat.test.ts`

Expected: FAIL because `streamChat` does not exist.

- [ ] **Step 3: Implement the minimal typed SSE client**

POST `{ messages }` to `/api/chat`, read `response.body` with `TextDecoder`, normalize CRLF, retain incomplete frame tails between reads, parse only `delta` and `done`, and throw on invalid responses or malformed delta payloads.

- [ ] **Step 4: Run the client test and verify GREEN**

Run: `cd web && npm test -- lib/chat.test.ts`

Expected: client tests pass.

- [ ] **Step 5: Write failing storefront SSE tests**

Replace JSON reply fixtures with SSE streams. Assert the reply grows from multiple deltas, the request body contains one user message, errors retain the current draft, newer input is not cleared when an earlier stream finishes, and the page links to `/chat`.

- [ ] **Step 6: Run the storefront test and verify RED**

Run: `cd web && npm test -- app/page.test.tsx`

Expected: FAIL because the storefront still reads `{ reply }` JSON.

- [ ] **Step 7: Migrate the storefront to `streamChat`**

Accumulate streamed deltas in the existing reply area, keep the current draft-preservation behavior, and add a bilingual link to the full chat page.

- [ ] **Step 8: Run the storefront test and verify GREEN**

Run: `cd web && npm test -- app/page.test.tsx`

Expected: storefront tests pass.

- [ ] **Step 9: Commit the shared client and storefront migration**

```bash
git add web/lib/chat.ts web/lib/chat.test.ts web/lib/types.ts web/app/page.tsx web/app/page.test.tsx web/app/page.module.css
git commit -m "feat: consume streaming chat replies in web"
```

### Task 4: Multi-turn `/chat` page

**Files:**
- Create: `web/app/chat/page.tsx`
- Create: `web/app/chat/page.module.css`
- Create: `web/app/chat/page.test.tsx`

**Interfaces:**
- Consumes: `streamChat(...)` and the shared `ChatMessage` type from Task 3.
- Produces: the `/chat` client page with ordered local conversation state and a complete-history request for every turn.

- [ ] **Step 1: Write failing page behavior tests**

Assert bilingual heading and welcome state, whitespace-only submission disabling, one user message plus incrementally growing Agent bubble, disabled input while streaming, error announcement, return-to-store link, and two submissions whose second request contains the first user message, first fixed Agent reply, and second user message in order.

- [ ] **Step 2: Run the chat page test and verify RED**

Run: `cd web && npm test -- app/chat/page.test.tsx`

Expected: FAIL because the page does not exist.

- [ ] **Step 3: Implement the accessible chat workspace**

Create local ordered message state, append the submitted user turn and an empty assistant turn, send the full history, append each delta to that assistant turn, prevent concurrent sends, announce errors, clear only the submitted draft, and scroll the latest message marker into view. Style it as a responsive warm-toned workspace consistent with the storefront.

- [ ] **Step 4: Run the chat page test and verify GREEN**

Run: `cd web && npm test -- app/chat/page.test.tsx`

Expected: chat page tests pass.

- [ ] **Step 5: Run full verification**

Run:

```bash
cd agent-service && .venv/bin/pytest -v
cd ../mall-service && .venv/bin/pytest -v
cd ../web && npm test
npm run build
cd .. && git diff --check
```

Expected: both Python suites and the Web suite pass, Next.js reports a successful production build including `/chat`, and `git diff --check` exits 0.

- [ ] **Step 6: Commit the chat page**

```bash
git add web/app/chat/page.tsx web/app/chat/page.module.css web/app/chat/page.test.tsx
git commit -m "feat: add multi-turn agent chat page"
```
