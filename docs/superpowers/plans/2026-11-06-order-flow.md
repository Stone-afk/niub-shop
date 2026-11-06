# Order Creation and Checkout Preview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` for Native execution or `superpowers:subagent-driven-development` for delegated execution. Complete tasks in order and keep the verification gates.

**Goal:** Let an explicit purchase intent in Agent chat create an idempotent pending-payment order, navigate Web to an order preview, and complete a local mock payment.

**Architecture:** mall-service owns SQLite-backed order state and exposes create/preview/pay APIs. agent-service exposes an Agents SDK `create_order_draft` tool, persists per-intent idempotency records, and emits an SSE `order` event. Web proxies order APIs, navigates from chat, and renders `/orders/[orderId]/preview`.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic, stdlib `sqlite3`, OpenAI Agents SDK, Next.js App Router, React, TypeScript, Vitest.

**Spec:** `docs/superpowers/003-order-flow/spec.md` and `docs/superpowers/003-order-flow/plan.md`.

## Global Constraints

- Fixed demo user is server-side `user_id=1`; clients never submit a user ID.
- Only explicit purchase intent may call the order tool; ambiguous intent must be clarified.
- Orders are created as `pending_payment`; no real payment provider is added.
- Same `turn_id + intent_hash` reuses the same persisted idempotency key; different intents in one turn may create different orders.
- Product snapshot prices are integer cents and are computed by mall-service.
- Existing recommendation limits and SSE behavior must remain compatible.
- No new third-party dependency is required for mall persistence; use stdlib SQLite.

## Review Focus

- Same purchase intent retried after SSE disconnect must return the same order: covered by T104/T203/T402.
- Two different item sets in one turn must create two orders: covered by T104/T203.
- Ambiguous language must not create an order: covered by T202/T402.
- Out-of-stock or unknown products must fail without a partial order: covered by T101/T102/T204.
- Malformed `order` SSE data must fail safely without navigation: covered by T301/T304.

### Task 1: Mall order models and SQLite contract

**Files:**
- Modify: `mall-service/src/mall_service/models.py`
- Modify: `mall-service/src/mall_service/app.py`
- Create: `mall-service/src/mall_service/orders.py`
- Test: `mall-service/tests/test_orders.py`

**Interfaces:**
- `POST /orders` accepts `{items: [{product_id: str, quantity: int}], client_request_id: str}` and returns an `Order` with `pending_payment` status.
- `GET /orders/{order_id}/preview` returns the stored order snapshot.
- `POST /orders/{order_id}/pay` returns the same order in `paid` status on first and repeated calls.
- `Order` exposes `id`, `user_id`, `status`, `items`, `subtotal_cents`, `total_cents`, `currency`, `created_at`.

- [ ] **Step 1: Write failing contract tests** for create, unknown/out-of-stock rejection, idempotent create, preview, pay, and repeated pay.
- [ ] **Step 2: Run** `cd mall-service && pytest tests/test_orders.py -q`; expected failure because order endpoints/storage do not exist.
- [ ] **Step 3: Implement** strict models and `orders.py` SQLite repository using `data/mall.db`, unique `(user_id, client_request_id)`, product snapshots, and integer-cent totals.
- [ ] **Step 4: Register endpoints** in `app.py`; keep user ownership server-side at `1` and map missing/invalid orders to stable HTTP errors.
- [ ] **Step 5: Run** `cd mall-service && pytest tests/test_orders.py -q`; expected PASS.

### Task 2: Agent order tool and per-intent idempotency

**Files:**
- Modify: `agent-service/src/agent_service/mall_client.py`
- Modify: `agent-service/src/agent_service/recommendation_agent.py`
- Modify: `agent-service/src/agent_service/conversations.py`
- Modify: `agent-service/src/agent_service/conversation_service.py`
- Modify: `agent-service/src/agent_service/app.py`
- Modify: `agent-service/prompts/*.md` (the configured prompt only)
- Test: `agent-service/tests/test_order_tool.py`
- Test: `agent-service/tests/test_conversations.py`

**Interfaces:**
- Mall client method: `create_order(items: Sequence[OrderItemRequest], client_request_id: str) -> OrderData`.
- Agent tool: `create_order_draft(items: list[{product_id: str, quantity: int}]) -> structured order summary`.
- SQLite repository: `get_or_create_order_intent(turn_id, intent_hash) -> OrderIntent`.
- Result metadata: optional `order` containing `order_id`, `preview_path`, and `status`.

- [ ] **Step 1: Write failing tests** for typed MallClient order responses, intent normalization/hash reuse, multiple intents in one turn, and no order metadata on tool failure.
- [ ] **Step 2: Run** `cd agent-service && pytest tests/test_order_tool.py tests/test_conversations.py -q`; expected failure.
- [ ] **Step 3: Implement** strict MallClient order schemas and the SQLite `order_intents` table with unique `(turn_id, intent_hash)` and generated UUID `client_request_id`.
- [ ] **Step 4: Implement** the Agents SDK tool. The tool must receive explicit product IDs/quantities, obtain/reuse the intent key before calling mall-service, and return structured order data; it must not set `paid`.
- [ ] **Step 5: Update** prompt instructions so “show/recommend/consider” never creates an order and unclear purchase intent is clarified first.
- [ ] **Step 6: Persist** order associations when a turn completes and emit `event: order` before `event: done`; failed order creation emits no success event.
- [ ] **Step 7: Run** focused tests and `cd agent-service && pytest -q`; expected PASS for affected suites and no regression.

### Task 3: Web order preview and navigation

**Files:**
- Modify: `web/lib/types.ts`
- Modify: `web/lib/chat.ts`
- Modify: `web/lib/conversations.ts`
- Modify: `web/app/chat/page.tsx`
- Create: `web/app/api/orders/[orderId]/route.ts`
- Create: `web/app/api/orders/[orderId]/pay/route.ts`
- Create: `web/app/orders/[orderId]/preview/page.tsx`
- Create/modify: `web/app/orders/[orderId]/preview/page.module.css`
- Test: `web/lib/chat.test.ts`, `web/lib/conversations.test.ts`, `web/app/chat/page.test.tsx`
- Test: order preview/API route tests alongside the new files

**Interfaces:**
- SSE parser callback: `onOrder?: (order: { order_id: string; preview_path: string; status: "pending_payment" }) => void`.
- Web BFF GET proxies `/orders/{orderId}/preview`; POST proxies `/orders/{orderId}/pay`.
- Preview page renders pending/paid/error states and invokes Mock pay explicitly.

- [ ] **Step 1: Write failing tests** for valid/invalid `order` event parsing, navigation only after successful stream completion, BFF proxy paths, and pending→paid UI.
- [ ] **Step 2: Run** `cd web && npm test -- --run ...`; expected failures for missing order behavior (subject to repository Node/Vitest environment constraints).
- [ ] **Step 3: Extend** types and stream consumers with strict `order` event validation while preserving existing recommendation event requirements.
- [ ] **Step 4: Add** order BFF routes using the existing proxy helper and the configured `MALL_SERVICE_URL`.
- [ ] **Step 5: Add** `/orders/[orderId]/preview` with bilingual labels, snapshot price formatting, status, retryable errors, and Mock pay button.
- [ ] **Step 6: Update** chat state to retain order metadata, navigate after `done`, and show historical “查看订单 / View order” links.
- [ ] **Step 7: Run** `cd web && npx tsc --noEmit --incremental false` plus focused Web tests.

### Task 4: Final local integration acceptance

**Files:**
- Modify only if needed: tests or `TODO.md` for deferred real-payment work
- Test: existing service tests and one local E2E/manual acceptance script or documented command sequence

- [ ] **Step 1: Run focused P1–P3 checks** and bind each result to FA-01/FA-07.
- [ ] **Step 2: Start mall-service, agent-service, and web locally** with the existing configuration; verify health endpoints.
- [ ] **Step 3: Exercise** recommendation → explicit purchase → `order` SSE → preview navigation → Mock pay.
- [ ] **Step 4: Exercise** ambiguous intent, duplicate stream retry, two different item sets in one turn, unknown/out-of-stock product, and repeated payment.
- [ ] **Step 5: Run** `git diff --check`, inspect allowed paths, and prepare the review packet; do not claim overall completion until audit and human approval.

## Integration Order

`Task 1 → Task 2 → Task 3 → Task 4`.

Each task must pass its focused verification before the next task starts. Real business/E2E acceptance is deferred to Task 4 only.
