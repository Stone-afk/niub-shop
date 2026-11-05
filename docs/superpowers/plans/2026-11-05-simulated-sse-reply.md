# Simulated SSE Reply Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the fixed Agent demo text with a paced SSE reply that echoes the latest user input and includes one request-time Unix millisecond timestamp.

**Architecture:** Keep the existing `{ messages }`, named `delta`, and `done` protocol. `agent-service` extracts the latest `user` message, captures the clock once, and streams four semantic chunks; the Web UI needs only a copy update because its parser already handles arbitrary deltas.

**Tech Stack:** Python 3.11, FastAPI, Pydantic, pytest/httpx, React 19, Vitest

**Spec:** `docs/superpowers/specs/2026-11-05-sse-chat-page-design.md`

## Global Constraints

- No model call or new dependency.
- Stream exactly four `delta` events followed by one `done` event.
- Delta contents are `你输入的是 `, the latest user content, `\n当前时间戳：`, and a Unix millisecond timestamp captured once per request.
- Preserve the existing 30 ms interval between demo deltas and the complete-history request format.

## Review Focus

- Histories with several user turns must echo the last user turn, not the first; covered by the backend contract test.
- Assistant messages after an earlier user turn must not be mistaken for current user input; covered by the backend contract test.
- A history containing no user message must return 422 instead of raising a server error; covered by a validation test.
- The clock must be sampled exactly once so all chunks represent one request timestamp; covered through an injected recording clock.
- Unicode and whitespace-trimmed user content must remain intact in the SSE payload; covered by the backend contract test.

---

### Task 1: Dynamic simulated SSE reply

**Files:**
- Modify: `agent-service/src/agent_service/app.py`
- Modify: `agent-service/tests/test_app.py`
- Modify: `web/app/chat/page.tsx`
- Modify: `web/app/chat/page.test.tsx`
- Modify: `docs/superpowers/specs/2026-11-05-sse-chat-page-design.md`

**Interfaces:**
- Consumes: the existing `ChatRequest.messages` and optional `create_app(..., clock: Callable[[], int])` test seam.
- Produces: four paced `delta` events containing the latest user input and one Unix millisecond timestamp, then `done`.

- [ ] **Step 1: Write failing backend behavior tests**

Replace the fixed-content assertion with a deterministic clock and assert the exact four dynamic delta payloads plus `done`. Add coverage for selecting the last user message from mixed history, trimming Unicode input, rejecting histories without a user role, and invoking the injected clock once.

- [ ] **Step 2: Run the focused backend tests and verify RED**

Run: `cd agent-service && .venv/bin/pytest tests/test_app.py -v`

Expected: FAIL because the endpoint still emits fixed reply chunks and has no injectable clock.

- [ ] **Step 3: Implement dynamic reply generation**

Extend `create_app` with `clock: Callable[[], int] | None`, defaulting to Unix milliseconds. Validate that history includes a user message, select the last user content, capture the clock once, and pass the four specified chunks through the existing paced SSE generator.

- [ ] **Step 4: Run the Agent suite and verify GREEN**

Run: `cd agent-service && .venv/bin/pytest -v`

Expected: all Agent tests pass.

- [ ] **Step 5: Write and verify the Web copy test RED→GREEN**

Update the chat page test to require the description “根据你的输入模拟流式回复 / Streams a simulated reply from your input”, verify it fails, then replace the old fixed-reply page copy with that text and rerun the test.

- [ ] **Step 6: Run full verification**

Run both Python suites, the full Web test suite, `npm run build`, and `git diff --check`.

Expected: all suites pass, the build lists `/chat`, and the diff check exits 0.

- [ ] **Step 7: Commit**

```bash
git add agent-service/src/agent_service/app.py agent-service/tests/test_app.py web/app/chat/page.tsx web/app/chat/page.test.tsx docs/superpowers/specs/2026-11-05-sse-chat-page-design.md docs/superpowers/plans/2026-11-05-simulated-sse-reply.md
git commit -m "feat: simulate contextual SSE replies"
```
