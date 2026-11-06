# Contextual Product Recommendation Enhancement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task with checkpoints.

**Goal:** Upgrade the recommendation flow to combine Agent-owned and external profiles with mock user behavior and mall/ad candidates, while enforcing at most 3 products and at most 1 sponsored product.

**Architecture:** Keep source boundaries explicit in the Agents SDK ReAct loop. `agent-service` owns an internal profile provider; `mall-service` exposes mock external profile, browsing, order, cart, organic recommendation, and sponsored recommendation endpoints. Each source returns an availability-aware result so empty or unavailable data can be skipped, then the final submission tool validates grounded IDs, product count, and sponsorship limits before SQLite persistence and SSE delivery.

**Tech Stack:** Python 3.11, FastAPI, SQLite, httpx, Pydantic, OpenAI Agents SDK, Next.js, React, TypeScript, Vitest.

## Global Constraints

- The demo user remains fixed at `user_id = 1`.
- Empty records represent a new user or no activity and must not fail the recommendation turn.
- A recommendation contains at most 3 unique product IDs.
- A recommendation contains at most 1 `sponsored` product.
- Product IDs must come from tool results in the current turn; invented IDs are rejected.
- Product sources are only `organic` or `sponsored` and are persisted with product IDs.
- Missing or unavailable individual sources are soft failures; the Agent continues with remaining sources.
- No real profile, recommendation, advertising, analytics, or authentication platform is added in this iteration; deferred scope is recorded in root `TODO.md`.
- User-facing empty-result and sponsored copy remains bilingual Chinese/English.

---

### Task 1: Add availability-aware profile and behavior contracts

**Files:**
- Modify: `mall-service/src/mall_service/models.py`
- Modify: `mall-service/src/mall_service/app.py`
- Modify: `agent-service/src/agent_service/mall_client.py`
- Modify: `agent-service/src/agent_service/product_tools.py`
- Test: `mall-service/tests/test_app.py`
- Test: `agent-service/tests/test_mall_client.py`
- Test: `agent-service/tests/test_product_tools.py`

**Interfaces:**
- `mall-service` exposes `GET /users/{user_id}/context` and `GET /users/{user_id}/external-profile`.
- The context response has `user_id`, `available`, `is_new_user`, `browsing`, `orders`, and `cart`; each record includes a product ID, timestamp, and relevant count/quantity/status.
- The external profile response has `user_id`, `source="recommendation-platform"`, `available`, and a profile object.
- `MallClient.get_user_context(user_id: int) -> UserContextData` and `MallClient.get_external_profile(user_id: int) -> ExternalProfileData` return typed dataclasses and raise `MallServiceUnavailable` only for unusable transport/payloads.
- `MallProductSearchTool.get_user_context` and `.get_external_profile` delegate to the client through `anyio.to_thread.run_sync`.

- [ ] **Step 1: Write failing endpoint tests**

Add tests asserting the fixed demo user returns structured records and that a new-user mock response can be represented with empty arrays and `is_new_user=true` without validation errors.

- [ ] **Step 2: Run the focused mall tests and verify failure**

Run: `cd mall-service && PYTHONPATH=src .venv/bin/pytest -q tests/test_app.py`

Expected: FAIL because the response models and external-profile route do not exist yet.

- [ ] **Step 3: Implement response models and mock routes**

Add strict Pydantic models for browsing/order/cart records, `UserContext`, and `ExternalProfile`. Keep the deterministic demo response for user 1 and use empty arrays for a new-user fixture path in tests.

- [ ] **Step 4: Add typed client models and tool delegates**

Parse the new payloads with strict Pydantic models, convert list fields to tuples in dataclasses, preserve `available` and `is_new_user`, and map transport/validation errors to `MallServiceUnavailable`.

- [ ] **Step 5: Run focused tests and commit**

Run:

```bash
cd mall-service && PYTHONPATH=src .venv/bin/pytest -q tests/test_app.py
cd ../agent-service && PYTHONPATH=src .venv/bin/pytest -q tests/test_mall_client.py tests/test_product_tools.py
```

Expected: PASS. Commit with `feat: add profile and behavior source contracts`.

### Task 2: Add Agent-owned profile provider and soft-failure source results

**Files:**
- Create: `agent-service/src/agent_service/profile_provider.py`
- Modify: `agent-service/src/agent_service/recommendation_agent.py`
- Test: `agent-service/tests/test_profile_provider.py`
- Test: `agent-service/tests/test_recommendation_agent.py`

**Interfaces:**
- `AgentProfileProvider.get_profile(user_id: int) -> AgentProfileData` returns a deterministic internal profile for user 1 with `source="agent"` and `available=True`.
- `RecommendationRunContext` gains `profile_provider`, `agent_profile`, `external_profile`, `user_context`, and a per-source availability/error map.
- New tool handlers are named `get_agent_profile` and `get_external_profile`.
- Tool output always contains `available`; unavailable sources return an empty profile and a safe error code rather than raising the whole turn.

- [ ] **Step 1: Write failing provider and tool tests**

Test that the internal provider returns the fixed bilingual/demo-safe profile and that a simulated external profile failure produces a tool result with `available=false` while the Agent can continue.

- [ ] **Step 2: Run focused tests and verify failure**

Run: `cd agent-service && PYTHONPATH=src .venv/bin/pytest -q tests/test_profile_provider.py tests/test_recommendation_agent.py`

Expected: FAIL because the provider and tools are not registered.

- [ ] **Step 3: Implement the provider and availability wrappers**

Create the provider with immutable dataclasses. Wrap source calls in a helper that returns `{source, available, data}` for expected `MallServiceUnavailable` and validation failures, while preserving hard failures for invalid final submissions.

- [ ] **Step 4: Register the new tools in the SDK Agent**

Register `get_agent_profile` and `get_external_profile` before mall recommendation tools. Keep `parallel_tool_calls=False`; automatically load user context before candidate tools if the model skips it, so purchased IDs are available for candidate filtering.

- [ ] **Step 5: Run focused tests and commit**

Run: `cd agent-service && PYTHONPATH=src .venv/bin/pytest -q tests/test_profile_provider.py tests/test_recommendation_agent.py`

Expected: PASS. Commit with `feat: add internal and external profile tools`.

### Task 3: Rework the recommendation policy and candidate aggregation

**Files:**
- Modify: `agent-service/src/agent_service/recommendation_agent.py`
- Modify: `agent-service/prompts/recommendation-policy.md`
- Modify: `agent-service/src/agent_service/app.py`
- Test: `agent-service/tests/test_recommendation_agent.py`
- Test: `agent-service/tests/test_app.py`

**Interfaces:**
- The ReAct order is profile/context first, organic and sponsored candidates next, `search_products` only when needed, then exactly one `submit_product_recommendation`.
- Candidate state tracks `candidate_ids` and `candidate_sources` across all successful tools.
- `RecommendationResult` remains `(answer, product_ids, product_sources)`.
- Submission schema changes to `Field(max_length=3)` and server validation rejects more than 3 IDs or more than one `sponsored` source.

- [ ] **Step 1: Write failing aggregation and constraint tests**

Add tests for: combining internal/external profiles and context with organic/ad candidates; accepting three organic products; accepting two organic plus one sponsored; rejecting four IDs; rejecting two sponsored IDs; and continuing when one source returns unavailable.

- [ ] **Step 2: Run focused tests and verify failure**

Run: `cd agent-service && PYTHONPATH=src .venv/bin/pytest -q tests/test_recommendation_agent.py tests/test_app.py`

Expected: FAIL because the current submission limit is 5 and the new profile/source flow is absent.

- [ ] **Step 3: Implement the bounded aggregation flow**

Update the run context and tool handlers to record only available, trusted candidates. Preserve organic/sponsored source labels, filter purchased IDs where context supplies them, and treat unavailable source payloads as empty candidate sets. Update the prompt to tell the model to prefer organic products, use at most one ad, avoid invented IDs, and return an empty result when no trusted candidate exists.

- [ ] **Step 4: Implement bilingual empty-result and SSE behavior**

Keep `product_ids` and `product_sources` in the recommendations SSE event. Ensure an empty recommendation still emits the event with empty arrays, followed by `done`; the natural-language answer contains the bilingual “no suitable products / try other platforms” message.

- [ ] **Step 5: Run focused tests and commit**

Run: `cd agent-service && PYTHONPATH=src .venv/bin/pytest -q tests/test_recommendation_agent.py tests/test_app.py`

Expected: PASS. Commit with `feat: bound contextual recommendations to three products`.

### Task 4: Update persistence and Web rendering for the new limits and empty state

**Files:**
- Modify: `agent-service/src/agent_service/conversations.py`
- Modify: `agent-service/src/agent_service/conversation_service.py`
- Modify: `web/lib/types.ts`
- Modify: `web/lib/chat.ts`
- Modify: `web/lib/conversations.ts`
- Modify: `web/app/chat/page.tsx`
- Modify: `web/app/chat/page.module.css`
- Test: `agent-service/tests/test_conversations.py`
- Test: `web/lib/chat.test.ts` or the existing chat-stream test file
- Test: `web/app/chat/page.test.tsx` or the existing page test file

**Interfaces:**
- SQLite persists no more than three product IDs and a same-length source tuple.
- `recommended_product_sources` remains optional in the Web detail type for backward-compatible historical rows.
- Product cards show the sponsored badge only when the aligned source is `sponsored`.
- Empty recommendations render the bilingual no-result message and no product cards.

- [ ] **Step 1: Write failing persistence and Web tests**

Add a repository test rejecting six IDs, a service test rejecting four IDs, a stream parser test accepting an empty recommendation event, and a page test asserting the empty state plus the sponsored badge behavior.

- [ ] **Step 2: Run focused tests and verify failure**

Run:

```bash
cd agent-service && PYTHONPATH=src .venv/bin/pytest -q tests/test_conversations.py tests/test_conversation_service.py
cd ../web && npm test -- web/lib/chat.test.ts web/app/chat/page.test.tsx
```

Expected: the new max-limit and empty-state assertions fail before implementation.

- [ ] **Step 3: Enforce the limit at persistence boundaries**

Reject more than three IDs in `ConversationRepository.complete_turn` and `ConversationService._validate_result`; keep source-length and allowed-source checks. Preserve compatibility with old rows by defaulting absent source rows to `organic`.

- [ ] **Step 4: Update Web parsing and rendering**

Validate aligned source arrays, render `广告 / Sponsored` for sponsored cards, and render the bilingual empty state when the recommendation event contains no IDs. Do not fabricate cards for missing catalog products.

- [ ] **Step 5: Run focused tests and commit**

Run:

```bash
cd agent-service && PYTHONPATH=src .venv/bin/pytest -q tests/test_conversations.py tests/test_conversation_service.py
cd ../web && ./node_modules/.bin/tsc --noEmit --incremental false
```

Expected: PASS. Commit with `feat: persist bounded recommendation results in web`.

### Task 5: Add deferred scope documentation and full verification

**Files:**
- Create: `TODO.md`
- Modify: `README.md`
- Test: all existing Python and Web tests

**Interfaces:**
- `TODO.md` is a user-readable list of deferred real profile, behavior, recommendation, product, advertising, authentication, privacy, and operations integrations.
- README documents the new profile sources, graceful degradation, three-product/two-source limits, and empty-result behavior in Chinese and English.

- [ ] **Step 1: Write the deferred-scope document**

Create `TODO.md` with concrete sections for user profile, behavior data, recommendation, product system, advertising, and engineering capabilities; do not include unbounded placeholders.

- [ ] **Step 2: Update bilingual README documentation**

Document the new mock endpoint, Agent tools, source labels, maximum three products, maximum one sponsored product, and no-result behavior.

- [ ] **Step 3: Run full verification**

Run:

```bash
cd mall-service && PYTHONPATH=src .venv/bin/pytest -q
cd ../agent-service && PYTHONPATH=src .venv/bin/pytest -q
cd ../web && ./node_modules/.bin/tsc --noEmit --incremental false
cd .. && git diff --check
```

Expected: all Python tests pass, TypeScript exits 0, and `git diff --check` reports no whitespace errors. If Web Vitest is blocked by the existing shared `node_modules` symlink permissions, record that limitation without deleting or replacing the linked dependency directory.

- [ ] **Step 4: Commit documentation and verification changes**

Run:

```bash
git add TODO.md README.md
git commit -m "docs: record deferred recommendation integrations"
```
