# Chat Consistency, History Deletion, and Catalog Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make repeated local chat tests more comparable, allow users to delete historical conversations, and expand the mock catalog enough to exercise meaningful multi-turn recommendations.

**Architecture:** Keep recommendation decisions model-owned and tool-grounded. Add structured mock product attributes without changing the existing product-card contract, add a repository/API delete path with SQLite cascade semantics, and expose deletion from the Next.js history sidebar. Keep the confirmed provider settings (`temperature: 0.7`, `max_tokens: 32768`, `timeout_seconds: 1800`, `tool_timeout_seconds: 1800`, `max_turns: 16`).

**Tech Stack:** Python/FastAPI/SQLite, pytest, Next.js/React/TypeScript, Vitest.

**Spec:** User feedback in the current conversation: same question gives inconsistent answers, history has no delete action, and `mall-service` mock catalog is too small for the planned E2E flow.

## Global Constraints

- Product facts must come from mall tools; do not hard-code a recommendation answer in the Agent.
- Product cards continue to render name, price, description, and availability only.
- Conversation deletion is scoped to fixed local `user_id = 1` and must cascade turns, recommendations, plans, and order links.
- A deleted conversation must not be selectable or retrievable afterward.
- Ads remain capped at one in the existing recommendation contract.
- Existing bilingual UI and API behavior must remain compatible.

## Review Focus

- Same request with `temperature: 0.7`: verify the richer catalog gives stable, evidence-based candidates even when prose differs (catalog and recommendation tests).
- Delete the selected conversation: verify selection moves to another conversation or new-chat state (repository/API/UI tests).
- Delete a conversation with turns, recommendations, plans, and orders: verify SQLite cascade cleanup (repository test).
- Search by category, budget, capacity and usage tags: verify multiple in-stock cup candidates are returned (mall tests).
- Existing product-card payloads: verify adding catalog fields does not break Web rendering (Web tests).

### Task 1: Expand mock catalog and product attributes

**Files:**
- Modify: `mall-service/src/mall_service/models.py`
- Modify: `mall-service/src/mall_service/catalog.py`
- Modify: `mall-service/src/mall_service/app.py` (only if response serialization needs no longer-hidden fields)
- Test: `mall-service/tests/test_catalog.py` and relevant API tests

**Interfaces:**
- Preserve existing `Product` response fields and add optional structured fields such as `capacity_ml`, `material`, and `has_lid`.
- Add several in-stock drinkware products spanning ceramic, glass, insulated/travel, and covered options with distinct prices and capacities.

- [ ] Write failing tests asserting at least four distinct in-stock cup candidates and structured attributes.
- [ ] Run the focused mall tests and confirm failure against the single-mug catalog.
- [ ] Implement the expanded catalog and serialization while preserving existing clients.
- [ ] Run all mall tests and confirm pass.

### Task 2: Add repository and API conversation deletion

**Files:**
- Modify: `agent-service/src/agent_service/conversations.py`
- Modify: `agent-service/src/agent_service/conversation_service.py`
- Modify: `agent-service/src/agent_service/app.py`
- Test: `agent-service/tests/test_conversations.py`, `test_conversation_service.py`, `test_app.py`

**Interfaces:**
- Add `ConversationRepository.delete_conversation(user_id: int, conversation_id: str) -> None`.
- Add `ConversationService.delete_conversation(user_id: int, conversation_id: str) -> None`.
- Add `DELETE /conversations/{conversation_id}` returning `204`; map missing conversations to the existing bilingual 404 error.

- [ ] Write failing tests for deletion, missing IDs, and cascade cleanup of all child records.
- [ ] Run focused Agent tests and confirm failure.
- [ ] Implement repository/service/API deletion using existing user scoping and foreign-key cascade.
- [ ] Run all Agent tests and confirm pass.

### Task 3: Add Web delete action and selection behavior

**Files:**
- Modify: `web/lib/conversations.ts`
- Create: `web/app/api/conversations/[conversationId]/route.ts` DELETE handler if required by proxy coverage
- Modify: `web/app/chat/page.tsx`
- Modify: `web/app/chat/page.module.css`
- Test: `web/lib/conversations.test.ts`, `web/app/chat/page.test.tsx`, route tests

**Interfaces:**
- Add `deleteConversation(id: string): Promise<void>` using `DELETE /api/conversations/{id}`.
- Add a bilingual delete control to each history item; stop deletion while that conversation is busy.
- After deletion, remove cached detail/stream/error state and select the next available conversation, otherwise new-chat state.

- [ ] Write failing client, route, and UI tests for delete request and post-delete selection.
- [ ] Run focused Web tests and confirm failure.
- [ ] Implement proxy/client/UI behavior with accessible labels and confirmation.
- [ ] Run Web tests and TypeScript checks.

### Task 4: Improve recommendation evidence and end-to-end verification

**Files:**
- Modify: `agent-service/prompts/shopping_agent.md`
- Test: existing Agent recommendation tests plus a new deterministic mock-catalog E2E fixture if needed
- Verify: `mall-service`, `agent-service`, and `web` running locally

- [ ] Add prompt guidance to use structured attributes when present and distinguish unavailable attributes from non-matching products.
- [ ] Run full Python and Web test suites.
- [ ] Start/verify all three services and exercise: budget/capacity cup request, follow-up “第一个适合送同事吗？”, changed requirement, and explicit purchase intent.
- [ ] Confirm history deletion from the browser and verify deleted conversations disappear after refresh.

