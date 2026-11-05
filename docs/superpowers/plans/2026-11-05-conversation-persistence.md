# Conversation Persistence and History Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist fixed-user (`user_id = 1`) conversations in local SQLite and let the Next.js chat page create, select, inspect, continue, and retry historical conversations.

**Architecture:** A standard-library SQLite repository owns conversations and turns, while an async conversation service moves blocking database work to AnyIO worker threads and wraps the existing model stream with transactional completion/failure semantics. FastAPI exposes conversation resources and named SSE; Next.js provides same-origin BFF routes and a browser UI whose selected conversation is server-backed rather than browser-memory-backed.

**Tech Stack:** Python 3.11, sqlite3, AnyIO, FastAPI, httpx, pytest, Next.js App Router, React, TypeScript, Vitest, Testing Library

**Spec:** `docs/superpowers/specs/2026-11-05-conversation-persistence-design.md`

## Global Constraints

- Resolve the current user only on the server; version one always returns integer `1`, and clients cannot submit or override `user_id`.
- Scope every conversation and turn lookup by both resource ID and user ID; an out-of-scope resource returns the same 404 as a missing resource.
- Use Python standard-library `sqlite3`; do not add SQLAlchemy, an ORM, or a migration framework.
- Default `AGENT_DB_FILE` to `data/agent.db` relative to the process working directory; create its parent and ignore `agent-service/data/`.
- Enable SQLite foreign keys, WAL, and a busy timeout; support exactly one serving `agent-service` worker.
- Persist no partial assistant text. A response becomes `completed` only after valid upstream `[DONE]`; cancellation or failure produces `failed` and no downstream `done`.
- Keep legacy stateless `POST /chat` working. Persisted chat uses new conversation endpoints.
- Only the newest failed turn is retryable; completed turns alone enter later model context.
- Version one excludes authentication, deletion, renaming, search, pagination, branching, attachments, tools, and provider-level retries.

## Review Focus

- Two simultaneous sends to the same conversation must create at most one pending turn and return stable 409 for the loser; Task 1 repository and Task 2 API tests pin this.
- Cancellation, premature upstream EOF, or downstream disconnect must close model resources, mark the turn failed, and persist no partial answer; Task 2 service/API tests pin this.
- A conversation owned by another synthetic user must be indistinguishable from a missing conversation; Tasks 1 and 2 ownership tests pin this.
- A pending turn left by a process crash must become failed during startup recovery without changing completed turns; Task 1 recovery tests pin this.
- Switching the visible conversation while another stream is active must never append deltas to the newly selected conversation; Task 4 controlled-stream UI tests pin this.

---

### Task 1: Fixed-user SQLite conversation repository

**Files:**
- Create: `agent-service/src/agent_service/conversations.py`
- Create: `agent-service/tests/test_conversations.py`
- Modify: one existing Git ignore file for `agent-service/data/`

**Interfaces:**
- Produces immutable `ConversationSummary`, `Conversation`, and `Turn` dataclasses; `TurnStatus = Literal["pending", "completed", "failed"]`.
- Produces `ConversationRepository(path: str | Path)` with synchronous `initialize()`, `recover_pending(user_id)`, `create_conversation(user_id)`, `list_conversations(user_id)`, `get_conversation(user_id, conversation_id)`, `create_turn(user_id, conversation_id, content)`, `complete_turn(user_id, conversation_id, turn_id, assistant_content)`, `fail_turn(user_id, conversation_id, turn_id, error_code)`, `prepare_retry(user_id, conversation_id, turn_id)`, and `model_messages(user_id, conversation_id, active_turn_id)`.
- Produces domain exceptions `ConversationNotFound`, `ConversationBusy`, and `TurnNotRetryable`; exceptions contain no database rows or message content.
- `get_conversation` returns a conversation with ordered turns; `model_messages` returns only completed user/assistant pairs followed by the active turn’s user message.

- [ ] **Step 1: Write failing schema and CRUD tests**

Add tests named `test_initializes_sqlite_pragmas_and_schema`, `test_creates_lists_and_reads_fixed_user_conversation`, and `test_first_turn_sets_truncated_title_and_recent_order`. Assert `user_id == 1`, UUID IDs, UTC timestamps, newest-first summaries, ordered turns, a deterministic 60-character title limit, foreign keys enabled, WAL mode, and `agent-service/data/` ignored.

- [ ] **Step 2: Run repository tests and verify RED**

Run: `agent-service/.venv/bin/pytest agent-service/tests/test_conversations.py -v`

Expected: FAIL because `agent_service.conversations` does not exist.

- [ ] **Step 3: Implement dataclasses, schema initialization, and basic CRUD**

Create the exact interfaces above in `conversations.py`. Use one connection per repository operation, `sqlite3.Row`, explicit transactions, `PRAGMA foreign_keys = ON`, `PRAGMA journal_mode = WAL`, and `PRAGMA busy_timeout = 5000`. Create the parent directory during `initialize()` and add `agent-service/data/` to the root `.gitignore` without altering unrelated rules.

- [ ] **Step 4: Write failing ownership, state, concurrency, context, and recovery tests**

Add tests named `test_every_lookup_is_scoped_by_user_id`, `test_partial_unique_index_rejects_second_pending_turn`, `test_complete_turn_persists_full_answer_atomically`, `test_fail_turn_discards_assistant_content`, `test_only_latest_failed_turn_can_prepare_retry`, `test_model_messages_exclude_failed_turns_and_include_active_user`, and `test_recover_pending_marks_only_fixed_users_pending_turns_failed`. Use a second synthetic owner (`user_id = 2`) only to prove isolation; public production resolution remains fixed at `1`.

- [ ] **Step 5: Implement turn transitions and repository exceptions**

Use SQL constraints plus transaction-guarded state checks. `prepare_retry` changes only the newest failed turn back to pending and clears `error_code`; `complete_turn` accepts non-empty complete content; `fail_turn` always sets `assistant_content = NULL`. Map missing and cross-user resources to `ConversationNotFound`.

- [ ] **Step 6: Run repository tests and full Agent regression**

Run:

```bash
agent-service/.venv/bin/pytest agent-service/tests/test_conversations.py -v
agent-service/.venv/bin/pytest agent-service/tests -q
```

Expected: repository tests pass and the full Agent suite has zero failures.

- [ ] **Step 7: Commit**

```bash
git add agent-service/src/agent_service/conversations.py agent-service/tests/test_conversations.py .gitignore
git commit -m "feat: persist fixed-user conversations"
```

Stage only the intended `.gitignore` hunk; preserve unrelated existing worktree edits.

### Task 2: Persisted streaming service and FastAPI conversation API

**Files:**
- Create: `agent-service/src/agent_service/conversation_service.py`
- Create: `agent-service/tests/test_conversation_service.py`
- Modify: `agent-service/src/agent_service/app.py`
- Modify: `agent-service/tests/test_app.py`

**Interfaces:**
- Consumes Task 1 repository/dataclasses/exceptions and existing `StreamingModelProvider`, `ModelStream`, `ModelMessage`, `ModelUpstreamError`, and `ModelProtocolError`.
- Produces `LOCAL_USER_ID = 1` and `resolve_current_user_id() -> int`.
- Produces async `ConversationService(repository, provider, system_prompt)` methods `list_conversations(user_id) -> list[ConversationSummary]`, `create_conversation(user_id) -> ConversationSummary`, `get_conversation(user_id, conversation_id) -> Conversation`, `open_turn(user_id, conversation_id, content) -> PersistedTurnStream`, `retry_turn(user_id, conversation_id, turn_id) -> PersistedTurnStream`, and `recover_pending(user_id) -> int`; all repository calls use `anyio.to_thread.run_sync`.
- Produces `PersistedTurnStream`, an async iterable of content deltas with `aclose()`. It buffers assistant content in memory, commits only after normal non-empty upstream completion, and marks the turn failed under cancellation shielding on every other exit.
- Extends `create_app` with injected `conversation_repository` and `conversation_service` seams while preserving existing provider/config injection and legacy `/chat`.
- JSON summaries expose `{id, title, created_at, updated_at, last_turn_status}`; details add ordered `turns`, whose fields are `{id, user_content, assistant_content, status, error_code, created_at, updated_at}`. Public JSON never includes `user_id`.

- [ ] **Step 1: Write failing conversation service tests**

Using the existing recording provider style and a temporary repository, add tests named `test_open_turn_builds_context_from_completed_database_turns`, `test_successful_stream_commits_answer_only_after_upstream_done`, `test_empty_successful_stream_becomes_failed`, `test_open_failure_marks_turn_failed_and_preserves_safe_exception`, `test_protocol_failure_discards_partial_answer`, `test_cancellation_marks_failed_and_closes_upstream`, and `test_retry_reuses_original_user_message_without_duplicate_turn`. Assert fixed ordering of system/completed/current messages, no partial database content before completion, and no secret text in persisted errors.

- [ ] **Step 2: Run service tests and verify RED**

Run: `agent-service/.venv/bin/pytest agent-service/tests/test_conversation_service.py -v`

Expected: FAIL because `agent_service.conversation_service` does not exist.

- [ ] **Step 3: Implement `ConversationService` and `PersistedTurnStream`**

Run synchronous repository calls through AnyIO threads. Open the upstream before returning the stream. If open fails, mark the turn failed and re-raise the existing safe provider exception. In the stream wrapper, use cancellation-shielded repository finalization and upstream cleanup; only a normal exhausted upstream stream may call `complete_turn`.

- [ ] **Step 4: Write failing FastAPI endpoint tests**

Extend `test_app.py` to assert: list/create resolve user `1` and ignore any client `user_id`; detail returns ordered turns and stable 404 for missing/cross-user IDs; send trims `{content}`, returns named SSE, and persists completion; open failure returns safe JSON 502 after failed persistence; a midstream/empty-response failure emits stable `event: error` without partial persistence or `done`; concurrent pending maps to stable JSON 409; retry succeeds only for the newest failed turn and otherwise returns stable 409; legacy `/chat` stays green.

- [ ] **Step 5: Implement lifespan wiring, serializers, and routes in `app.py`**

Resolve `AGENT_DB_FILE` to `Path(os.getenv("AGENT_DB_FILE", "data/agent.db"))`. Use FastAPI lifespan to initialize the repository and call `recover_pending(LOCAL_USER_ID)` without creating a database merely by importing test helpers. Route handlers obtain the server-side resolver value and translate only the three domain exceptions into stable 404/409 responses. Persisted SSE routes emit `delta`, then `done` on committed success; after downstream streaming begins, a safe `event: error` with code `model_stream_failed` replaces `done` on terminal failure.

- [ ] **Step 6: Run the complete Agent suite**

Run: `agent-service/.venv/bin/pytest agent-service/tests -v`

Expected: all Agent tests pass, including legacy stateless chat.

- [ ] **Step 7: Commit**

```bash
git add agent-service/src/agent_service/conversation_service.py agent-service/src/agent_service/app.py agent-service/tests/test_conversation_service.py agent-service/tests/test_app.py
git commit -m "feat: expose persisted conversation streams"
```

### Task 3: Next.js conversation BFF and typed client

**Files:**
- Modify: `web/lib/types.ts`
- Create: `web/lib/conversations.ts`
- Create: `web/lib/conversations.test.ts`
- Create: `web/app/api/conversations/route.ts`
- Create: `web/app/api/conversations/route.test.ts`
- Create: `web/app/api/conversations/[conversationId]/route.ts`
- Create: `web/app/api/conversations/[conversationId]/turns/route.ts`
- Create: `web/app/api/conversations/[conversationId]/turns/[turnId]/retry/route.ts`
- Create focused dynamic-route tests beside their handlers
- Modify: `web/lib/chat.ts`
- Modify: `web/lib/chat.test.ts`

**Interfaces:**
- Consumes Task 2 JSON/SSE endpoints through `AGENT_SERVICE_URL`.
- Produces TypeScript `TurnStatus`, `ConversationTurn`, `ConversationSummary`, and `ConversationDetail` matching Agent JSON exactly.
- Produces `listConversations()`, `createConversation()`, `getConversation(id)`, `streamConversationTurn(conversationId, content, onDelta)`, and `retryConversationTurn(conversationId, turnId, onDelta)`.
- Refactors the existing named-SSE parser into a shared internal `consumeChatStream(response, onDelta)` without changing `streamChat` behavior.

- [ ] **Step 1: Write failing BFF route tests**

Assert each handler normalizes `AGENT_SERVICE_URL`, URL-encodes dynamic IDs, forwards method/body/content type, transparently returns SSE for send/retry, preserves JSON status for list/create/detail errors, and maps connection or successful wrong-content-type responses to the existing stable 502 body.

- [ ] **Step 2: Run route tests and verify RED**

Run: `/Users/stone/.nvm/versions/node/v22.22.2/bin/node web/node_modules/vitest/vitest.mjs run web/app/api/conversations`

Expected: FAIL because the routes do not exist.

- [ ] **Step 3: Implement conversation BFF routes**

Use `proxyJson`, `proxyStream`, and `serviceUrl`, with Next.js 16 async route params (`context.params` is a Promise). Do not inspect or rewrite SSE frames in the BFF.

- [ ] **Step 4: Write failing typed-client and SSE parser tests**

Assert exact URLs/bodies, list/detail JSON decoding, ordered UTF-8 deltas, `done`, explicit server `error` events, malformed frames, premature EOF, and non-2xx responses. Retain all existing `streamChat` tests.

- [ ] **Step 5: Implement types, client functions, and shared SSE consumption**

`streamConversationTurn` sends only `{content}`; retry sends no user content. Treat an `event: error` frame or EOF before `done` as a failed promise so UI refreshes persisted failed state.

- [ ] **Step 6: Run Web unit tests**

Run: `/Users/stone/.nvm/versions/node/v22.22.2/bin/node web/node_modules/vitest/vitest.mjs run`

Expected: all current and new Web tests pass.

- [ ] **Step 7: Commit**

```bash
git add web/lib/types.ts web/lib/chat.ts web/lib/chat.test.ts web/lib/conversations.ts web/lib/conversations.test.ts web/app/api/conversations
git commit -m "feat: proxy persisted conversations"
```

### Task 4: Historical conversation selection and retry UI

**Files:**
- Modify: `web/app/chat/page.tsx`
- Modify: `web/app/chat/page.module.css`
- Modify: `web/app/chat/page.test.tsx`

**Interfaces:**
- Consumes Task 3 typed conversation client.
- Produces a bilingual two-pane responsive chat page with conversation summaries, selected detail, unsaved new-chat state, continued streaming, and latest-failure retry.
- Maintains in-memory per-conversation display buffers keyed by conversation/turn ID so a stream can finish without writing into a newly selected conversation.

- [ ] **Step 1: Replace browser-history tests with failing persistence UI tests**

Mock Task 3 client functions rather than global fetch. Cover loading/auto-selection, older-conversation selection, completed and failed turns, unsaved new-chat creation, continuing with only new content, latest-failure retry, summary reorder, switching during a controlled stream, empty/loading/404/list-error states, selected-button accessibility, live region, and disabling only the active conversation composer.

- [ ] **Step 2: Run page tests and verify RED**

Run: `/Users/stone/.nvm/versions/node/v22.22.2/bin/node web/node_modules/vitest/vitest.mjs run web/app/chat/page.test.tsx`

Expected: FAIL because the page still owns browser-only `messages`.

- [ ] **Step 3: Implement page state and server-backed actions**

Load summaries on mount. Keep `selectedConversationId`, cached details, loading/error state, and active stream buffers. On first send from new-chat state, create the conversation before opening its turn stream. After every terminal outcome, reload that detail and the summary list so SQLite remains authoritative.

- [ ] **Step 4: Implement bilingual history layout and failed-turn controls**

Extend the CSS with desktop sidebar/content columns and a narrow-screen stacked history selector. Preserve keyboard-visible controls, labels, live region, scroll behavior, and the store back link. Replace “simulated reply / demo” copy with persisted DeepSeek wording.

- [ ] **Step 5: Run Web tests and production build**

Run:

```bash
/Users/stone/.nvm/versions/node/v22.22.2/bin/node web/node_modules/vitest/vitest.mjs run
/Users/stone/.nvm/versions/node/v22.22.2/bin/node web/node_modules/next/dist/bin/next build
```

Expected: all Web tests pass and the production build exits zero.

- [ ] **Step 6: Commit**

```bash
git add web/app/chat/page.tsx web/app/chat/page.module.css web/app/chat/page.test.tsx
git commit -m "feat: browse persisted chat history"
```

### Task 5: Native persisted-history smoke test and bilingual operations docs

**Files:**
- Modify: `scripts/smoke_test.py`
- Modify: `tests/test_smoke.py`
- Modify: `README.md`
- Modify: the approved spec only for a verified design correction

**Interfaces:**
- Consumes all previous public API and UI contracts.
- Produces a no-cost Native smoke flow using a temporary SQLite database and the existing local fake OpenAI-compatible model.

- [ ] **Step 1: Write failing persisted-history smoke assertions**

Extend `run_smoke_test()` with temporary `AGENT_DB_FILE` and verify through Web BFF: initial list empty; create conversation; first turn streams; list/detail reload persisted data and generated title; second turn includes the first completed pair in model context; database file exists; model requests contain no client `user_id` and use server-owned history.

- [ ] **Step 2: Run smoke test and verify RED**

Run: `python3.11 -m unittest discover -s tests -p 'test_smoke.py' -v`

Expected: FAIL because the smoke flow still exercises only legacy `/api/chat`.

- [ ] **Step 3: Implement smoke flow and documentation**

Make the fake model return deterministic content for two requests and capture both histories. Update README in Chinese and English with `AGENT_DB_FILE`, fixed local user `1`, single-worker restriction, database location/ignore behavior, conversation endpoints, history UI, failure retry semantics, and reset instructions that stop the Agent before removing the local database. Do not expose the ignored local LLM key.

- [ ] **Step 4: Run fresh full verification**

Run:

```bash
agent-service/.venv/bin/pytest -q agent-service/tests
mall-service/.venv/bin/pytest -q mall-service/tests
/Users/stone/.nvm/versions/node/v22.22.2/bin/node web/node_modules/vitest/vitest.mjs run
/Users/stone/.nvm/versions/node/v22.22.2/bin/node web/node_modules/next/dist/bin/next build
python3.11 -m unittest discover -s tests -p 'test_smoke.py' -v
git diff --check
```

Expected: all Python suites, Web tests, production build, and the two-turn persisted-history smoke flow pass; diff check exits zero.

- [ ] **Step 5: Commit**

```bash
git add scripts/smoke_test.py tests/test_smoke.py README.md docs/superpowers/specs/2026-11-05-conversation-persistence-design.md docs/superpowers/plans/2026-11-05-conversation-persistence.md
git commit -m "docs: describe persisted conversation history"
```

Stage README and spec/plan hunks selectively so unrelated pre-existing worktree edits remain untouched.
