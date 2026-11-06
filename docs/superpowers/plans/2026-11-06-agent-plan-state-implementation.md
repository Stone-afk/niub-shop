# Agent Plan State Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a model-owned Plan/Plan State loop to the Agents SDK recommendation flow and persist the latest plan state with each conversation turn.

**Architecture:** Add a strict `PlanState` schema and an `update_plan` FunctionTool. The model decides plan steps and the next action; code only validates and records state, executes selected tools, and applies safety/resource limits. Persist the latest validated state in a `turn_plans` SQLite table and expose it as optional conversation detail metadata without changing the Web layout.

**Tech Stack:** Python 3.11, Pydantic, OpenAI Agents SDK, FastAPI, SQLite, SSE, pytest, TypeScript.

## Global Constraints

- Plan decisions remain model-owned; code never maps `next_action` to a tool or auto-completes steps.
- `PlanState.status` is one of `draft`, `in_progress`, `completed`, `blocked`, `cancelled`.
- Step status is one of `pending`, `in_progress`, `completed`, `blocked`, `skipped`.
- A Plan has unique step IDs and a non-empty goal.
- Product recommendations remain limited to 3 unique IDs and at most 1 `sponsored` source.
- Tool/model resource limits remain enforced by `max_turns` and `tool_timeout_seconds`.
- Fixed demo user remains `user_id = 1`.
- Existing SSE event order remains `delta`, `recommendations`, `done`; no mandatory Web Plan UI is added.

---

### Task 1: Define and validate Plan State

**Files:**
- Create: `agent-service/src/agent_service/plan_state.py`
- Test: `agent-service/tests/test_plan_state.py`

**Interfaces:**
- `PlanStatus = Literal["draft", "in_progress", "completed", "blocked", "cancelled"]`.
- `StepStatus = Literal["pending", "in_progress", "completed", "blocked", "skipped"]`.
- `PlanStep(BaseModel)` fields: `id`, `title`, `status`, `depends_on`.
- `PlanState(BaseModel)` fields: `plan_id`, `goal`, `status`, `current_step_id`, `steps`, `next_action`, `reason`.
- `validate_plan_state(payload: object) -> PlanState` rejects blank IDs/goals, duplicate step IDs, unknown current step IDs, invalid enum values, and more than 64 steps.

- [ ] **Step 1: Write failing schema tests**

Cover valid states, duplicate step IDs, missing current step IDs, blank goals, invalid statuses, and the 64-step bound.

- [ ] **Step 2: Run focused tests and verify failure**

Run: `cd agent-service && PYTHONPATH=src .venv/bin/pytest -q tests/test_plan_state.py`

Expected: FAIL because the module does not exist.

- [ ] **Step 3: Implement strict Pydantic models**

Use `ConfigDict(strict=True, extra="forbid", frozen=True)`, bounded strings, a maximum of 64 steps, and a model validator for unique step IDs and `current_step_id` membership.

- [ ] **Step 4: Run focused tests and commit**

Run the focused test command again; expected PASS. Commit `feat: add strict agent plan state models`.

### Task 2: Add `update_plan` to the Agents SDK loop

**Files:**
- Modify: `agent-service/src/agent_service/recommendation_agent.py`
- Modify: `agent-service/prompts/recommendation-policy.md`
- Test: `agent-service/tests/test_recommendation_agent.py`

**Interfaces:**
- `RecommendationRunContext.plan_state: PlanState | None`.
- `RecommendationRunContext.plan_history: list[PlanState]`.
- `_update_plan(run_context, payload) -> str` validates and stores the model-submitted state, then returns normalized JSON.
- New FunctionTool name: `update_plan`.
- Existing tool handlers do not mutate Plan State or infer the next action.

- [ ] **Step 1: Write failing Agent tests**

Use `ScriptedModel` to submit a draft/in-progress Plan, call a mall tool, submit an updated Plan, and then submit a recommendation. Assert the normalized Plan history contains both states. Add an invalid-plan test that maps to `RecommendationInvalid` without changing prior state.

- [ ] **Step 2: Run focused tests and verify failure**

Run: `cd agent-service && PYTHONPATH=src .venv/bin/pytest -q tests/test_recommendation_agent.py`

Expected: FAIL because `update_plan` is not registered and `RecommendationRunContext` has no plan state.

- [ ] **Step 3: Implement the tool and context state**

Register `update_plan` alongside existing tools. Its handler must only validate, append to `plan_history`, replace `plan_state`, and return JSON. Do not use `next_action` or `current_step_id` to enable/disable or invoke another tool.

- [ ] **Step 4: Update the policy prompt**

Require Plan creation before product tools, Plan updates after tool results, model-owned replanning, and a terminal `completed` or `blocked` state before final submission. Permit simple non-product chat without a Plan.

- [ ] **Step 5: Run focused tests and commit**

Expected PASS. Commit `feat: add model-owned update plan tool`.

### Task 3: Persist the latest Plan State with a turn

**Files:**
- Modify: `agent-service/src/agent_service/conversations.py`
- Modify: `agent-service/src/agent_service/conversation_service.py`
- Modify: `agent-service/src/agent_service/app.py`
- Test: `agent-service/tests/test_conversations.py`
- Test: `agent-service/tests/test_conversation_service.py`
- Test: `agent-service/tests/test_app.py`

**Interfaces:**
- `ConversationRepository.save_plan_state(user_id, conversation_id, turn_id, plan_state: PlanState, version: int) -> None`.
- `ConversationRepository.get_conversation` returns `Turn.plan_state: dict[str, object] | None`.
- `BufferedRecommendationStream` receives the final plan state and saves it with the completed turn.
- Conversation detail JSON includes optional `plan_state` on each turn; existing clients may ignore it.

- [ ] **Step 1: Write failing persistence/API tests**

Test latest Plan State retrieval, turn ownership checks, version increments, cleanup on failed/retried turns, and JSON exposure from `GET /conversations/{id}`.

- [ ] **Step 2: Run focused tests and verify failure**

Run: `cd agent-service && PYTHONPATH=src .venv/bin/pytest -q tests/test_conversations.py tests/test_conversation_service.py tests/test_app.py`

Expected: FAIL because the table and Turn field do not exist.

- [ ] **Step 3: Add the `turn_plans` table and repository methods**

Create the table with a unique `turn_id`, foreign key cascade, status, current step, JSON state, version, and timestamps. Keep the write in the same transaction as successful turn completion; delete plan rows when a pending/failed turn is reset.

- [ ] **Step 4: Thread Plan State through the service and API**

Pass the final `RecommendationRunContext.plan_state` into `RecommendationResult`/stream metadata, persist it only when the turn completes, and include it in conversation detail responses. Do not change the existing SSE event order.

- [ ] **Step 5: Run focused tests and commit**

Expected PASS. Commit `feat: persist plan state with conversation turns`.

### Task 4: Remove business-step interference while retaining safety limits

**Files:**
- Modify: `agent-service/src/agent_service/recommendation_agent.py`
- Modify: `agent-service/src/agent_service/conversation_service.py`
- Modify: `README.md`
- Test: `agent-service/tests/test_recommendation_agent.py`

**Interfaces:**
- `search_products` and recommendation tools remain callable based on SDK/model selection, not Plan State transitions.
- `max_turns` and `tool_timeout_seconds` remain hard resource limits.
- Grounding, product count, sponsored count, JSON, and timeout validation remain code-owned safety checks.

- [ ] **Step 1: Add regression tests**

Script a model that selects tools in a non-default order, skips context, revises its Plan, and ends blocked. Assert no code-side tool reorder or forced step completion occurs. Add a max-turn and tool-timeout test to confirm safety limits still fire.

- [ ] **Step 2: Implement the minimal removal of local step gating**

Remove only business-order assumptions that block tools because `searched` or `found_products` is false/true. Keep duplicate search protection only as a resource/safety rule if it does not select the model’s next action; otherwise report the condition to the model as a structured tool result.

- [ ] **Step 3: Update bilingual README documentation**

Document Plan State, `update_plan`, model-owned next actions, persistence, safety boundaries, and the unchanged SSE contract.

- [ ] **Step 4: Run focused tests and commit**

Expected PASS. Commit `feat: let plan state drive agent tool selection`.

### Task 5: Full verification and handoff

**Files:**
- Modify: `docs/superpowers/specs/2026-11-06-agent-plan-state-design.md` only if implementation constraints require clarification.

- [ ] **Step 1: Run all checks**

```bash
cd mall-service && PYTHONPATH=src .venv/bin/pytest -q
cd ../agent-service && PYTHONPATH=src .venv/bin/pytest -q
cd ../web && ./node_modules/.bin/tsc --noEmit --incremental false
cd .. && git diff --check
```

- [ ] **Step 2: Verify API shape**

Create one conversation with a scripted/deterministic test agent, update the plan twice, complete the turn, and assert the detail endpoint returns the latest `plan_state` while recommendation SSE remains `delta → recommendations → done`.

- [ ] **Step 3: Commit documentation and report**

Report test counts, the persisted Plan State shape, and any Web Vitest environment limitation without changing dependency directories.
