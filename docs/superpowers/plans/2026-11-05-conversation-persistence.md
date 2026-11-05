# 会话持久化与历史选择实施计划 / Conversation Persistence and History Selection Implementation Plan

> **供智能体执行 / For agentic workers:** 必须使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans`，按任务逐项实施。所有步骤使用复选框跟踪。 / REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` task by task. Steps use checkboxes for tracking.

**目标 / Goal:** 使用本地 SQLite 持久化固定用户（`user_id = 1`）的会话，并让 Next.js 聊天页能够创建、选择、查看、继续和重试历史会话。 / Persist fixed-user (`user_id = 1`) conversations in local SQLite and let the Next.js chat page create, select, inspect, continue, and retry historical conversations.

**架构 / Architecture:** Python 标准库 SQLite Repository 负责 conversations 和 turns；异步 Conversation Service 使用 AnyIO 线程池执行阻塞数据库操作，并在现有模型流外增加成功提交和失败回滚语义。FastAPI 暴露会话资源和命名 SSE，Next.js 提供同源 BFF 与服务端历史驱动的聊天页面。 / A standard-library SQLite repository owns conversations and turns. An async conversation service moves blocking database work to AnyIO worker threads and wraps the current model stream with transactional completion and failure semantics. FastAPI exposes conversation resources and named SSE; Next.js provides same-origin BFF routes and a server-backed chat UI.

**技术栈 / Tech Stack:** Python 3.11、sqlite3、AnyIO、FastAPI、httpx、pytest、Next.js App Router、React、TypeScript、Vitest、Testing Library。

**设计文档 / Spec:** `docs/superpowers/specs/2026-11-05-conversation-persistence-design.md`

## 全局约束 / Global Constraints

- 当前用户只能由服务端解析；第一版固定返回整数 `1`，客户端不能提交或覆盖 `user_id`。 / Resolve the current user only on the server; version one always returns integer `1`, and clients cannot submit or override `user_id`.
- 所有 conversation 和 turn 查询必须同时按资源 ID 与用户 ID 限定；越权资源与不存在资源统一返回 404。 / Scope every lookup by resource ID and user ID; out-of-scope and missing resources both return 404.
- 只使用 Python 标准库 `sqlite3`，不增加 SQLAlchemy、ORM 或迁移框架。 / Use standard-library `sqlite3`; add no ORM or migration framework.
- `AGENT_DB_FILE` 默认是相对当前工作目录的 `data/agent.db`；自动创建父目录并忽略 `agent-service/data/`。 / Default `AGENT_DB_FILE` to `data/agent.db`, create its parent, and ignore `agent-service/data/`.
- 启用 SQLite foreign keys、WAL 与 5000ms busy timeout；只支持一个 `agent-service` worker。 / Enable foreign keys, WAL, and a 5000ms busy timeout; support one serving worker.
- 不持久化部分 Agent 回复；只有有效上游 `[DONE]` 才能把 turn 改为 `completed`，取消或失败必须改为 `failed`，且不能发送下游 `done`。 / Persist no partial assistant text. Only valid upstream `[DONE]` completes a turn; cancellation or failure marks it failed and emits no downstream `done`.
- 保留旧的无状态 `POST /chat`；新的持久化聊天使用 conversation API。 / Preserve legacy stateless `POST /chat`; persisted chat uses conversation APIs.
- 只有最新的 failed turn 可以重试；后续模型上下文只包含 completed turns。 / Only the newest failed turn is retryable; only completed turns enter later model context.
- 第一版不包含认证、删除、重命名、搜索、分页、分支会话、附件、工具调用或供应商级重试。 / Version one excludes authentication, deletion, renaming, search, pagination, branching, attachments, tools, and provider retries.

## 审查重点 / Review Focus

- 同一 conversation 的两个并发发送只能产生一个 pending turn，另一个稳定返回 409；Task 1 和 Task 2 测试覆盖。 / Two simultaneous sends create at most one pending turn; the loser receives stable 409.
- 取消、上游提前 EOF 或浏览器断开必须关闭模型资源、标记失败且不保存部分回复；Task 2 覆盖。 / Cancellation, premature EOF, or disconnect must close resources, mark failure, and persist no partial answer.
- 属于其他测试用户的 conversation 必须与不存在资源表现一致；Task 1 和 Task 2 覆盖。 / Cross-user resources must be indistinguishable from missing resources.
- 进程崩溃遗留的 pending turn 必须在启动恢复时改为 failed，不能影响 completed turns；Task 1 覆盖。 / Startup recovery must fail stale pending turns without changing completed turns.
- 流式回复期间切换选中会话，delta 不能写入新选中的会话；Task 4 覆盖。 / Switching selection during streaming must not append deltas to the newly selected conversation.

---

### Task 1：固定用户 SQLite 会话 Repository / Fixed-user SQLite Conversation Repository

**文件 / Files:**

- 新建 / Create: `agent-service/src/agent_service/conversations.py`
- 新建 / Create: `agent-service/tests/test_conversations.py`
- 修改 / Modify: 一个现有 Git ignore 文件，用于忽略 `agent-service/data/`

**接口 / Interfaces:**

- 产出不可变 dataclasses：`ConversationSummary`、`Conversation`、`Turn`；`TurnStatus = Literal["pending", "completed", "failed"]`。 / Produce immutable conversation dataclasses and the exact status literal.
- 产出 `ConversationRepository(path: str | Path)`，包含同步方法：`initialize()`、`recover_pending(user_id)`、`create_conversation(user_id)`、`list_conversations(user_id)`、`get_conversation(user_id, conversation_id)`、`create_turn(user_id, conversation_id, content)`、`complete_turn(user_id, conversation_id, turn_id, assistant_content)`、`fail_turn(user_id, conversation_id, turn_id, error_code)`、`prepare_retry(user_id, conversation_id, turn_id)`、`model_messages(user_id, conversation_id, active_turn_id)`。
- 产出领域异常 `ConversationNotFound`、`ConversationBusy`、`TurnNotRetryable`，异常内容不能包含消息正文或数据库行。 / Produce secret-safe domain exceptions.
- `get_conversation` 返回按时间排序的 turns；`model_messages` 只返回 completed user/assistant 对和当前 active turn 的 user 消息。 / Return ordered turns and server-owned model history.

- [ ] **Step 1：编写失败的 schema 与 CRUD 测试 / Write failing schema and CRUD tests**

新增 `test_initializes_sqlite_pragmas_and_schema`、`test_creates_lists_and_reads_fixed_user_conversation`、`test_first_turn_sets_truncated_title_and_recent_order`。断言 `user_id == 1`、UUID、UTC 时间、最近优先排序、turn 顺序、60 字符标题上限、foreign keys、WAL 以及 data 目录被忽略。 / Assert fixed ownership, identifiers, timestamps, ordering, title truncation, pragmas, and ignore behavior.

- [ ] **Step 2：运行测试并确认 RED / Run tests and verify RED**

Run: `agent-service/.venv/bin/pytest agent-service/tests/test_conversations.py -v`

Expected: FAIL，因为 `agent_service.conversations` 尚不存在。 / FAIL because the module does not exist.

- [ ] **Step 3：实现 dataclasses、schema 和基础 CRUD / Implement dataclasses, schema, and CRUD**

每个 Repository 操作使用独立连接、`sqlite3.Row` 和显式事务；设置 `PRAGMA foreign_keys = ON`、`journal_mode = WAL`、`busy_timeout = 5000`。`initialize()` 创建父目录；只向根 `.gitignore` 增加目标规则，不覆盖无关修改。 / Use one connection per operation, explicit transactions, required pragmas, parent creation, and a surgical ignore update.

- [ ] **Step 4：编写失败的归属、状态、并发、上下文与恢复测试 / Write failing ownership, state, concurrency, context, and recovery tests**

新增：`test_every_lookup_is_scoped_by_user_id`、`test_partial_unique_index_rejects_second_pending_turn`、`test_complete_turn_persists_full_answer_atomically`、`test_fail_turn_discards_assistant_content`、`test_only_latest_failed_turn_can_prepare_retry`、`test_model_messages_exclude_failed_turns_and_include_active_user`、`test_recover_pending_marks_only_fixed_users_pending_turns_failed`。测试中可使用 `user_id = 2` 验证隔离，生产解析仍固定为 `1`。 / Use synthetic user `2` only to prove ownership isolation.

- [ ] **Step 5：实现 turn 状态转换与 Repository 异常 / Implement turn transitions and exceptions**

使用 SQL constraints 和事务内状态检查。`prepare_retry` 只能把最新 failed turn 改回 pending 并清空错误；`complete_turn` 只接受非空完整回复；`fail_turn` 必须把 `assistant_content` 设为 NULL。 / Enforce state transitions transactionally and map cross-user access to `ConversationNotFound`.

- [ ] **Step 6：运行 Repository 与 Agent 全量测试 / Run repository and Agent suites**

```bash
agent-service/.venv/bin/pytest agent-service/tests/test_conversations.py -v
agent-service/.venv/bin/pytest agent-service/tests -q
```

Expected: 全部通过、零失败。 / All tests pass with zero failures.

- [ ] **Step 7：提交 / Commit**

```bash
git add agent-service/src/agent_service/conversations.py agent-service/tests/test_conversations.py .gitignore
git commit -m "feat: persist fixed-user conversations"
```

只暂存目标 `.gitignore` hunk，保留现有无关工作区修改。 / Stage only the intended ignore hunk.

### Task 2：持久化流式服务与 FastAPI Conversation API / Persisted Streaming Service and FastAPI Conversation API

**文件 / Files:**

- 新建 / Create: `agent-service/src/agent_service/conversation_service.py`
- 新建 / Create: `agent-service/tests/test_conversation_service.py`
- 修改 / Modify: `agent-service/src/agent_service/app.py`
- 修改 / Modify: `agent-service/tests/test_app.py`

**接口 / Interfaces:**

- 使用 Task 1 Repository 和现有 `StreamingModelProvider`、`ModelStream`、`ModelMessage`、`ModelUpstreamError`、`ModelProtocolError`。 / Consume Task 1 and existing model interfaces.
- 产出 `LOCAL_USER_ID = 1` 与 `resolve_current_user_id() -> int`。 / Produce the fixed current-user boundary.
- 产出异步 `ConversationService(repository, provider, system_prompt)`：`list_conversations(user_id) -> list[ConversationSummary]`、`create_conversation(user_id) -> ConversationSummary`、`get_conversation(user_id, conversation_id) -> Conversation`、`open_turn(...) -> PersistedTurnStream`、`retry_turn(...) -> PersistedTurnStream`、`recover_pending(user_id) -> int`；数据库调用统一使用 `anyio.to_thread.run_sync`。
- 产出 `PersistedTurnStream`：在内存累计 delta，仅在正常且非空结束后提交；其他退出路径在取消屏蔽下标记失败。 / Buffer deltas and commit only a normal non-empty completion.
- JSON summary 为 `{id, title, created_at, updated_at, last_turn_status}`；detail 额外包含 turns `{id, user_content, assistant_content, status, error_code, created_at, updated_at}`；公开 JSON 不包含 `user_id`。 / Public JSON never exposes user ownership.

- [ ] **Step 1：编写失败的 Conversation Service 测试 / Write failing service tests**

覆盖：数据库历史构造模型上下文；只有上游完成后才提交；空成功流转为 failed；打开失败；协议错误丢弃部分回复；取消时失败并关闭上游；retry 复用原 user 消息且不重复创建 turn。 / Cover context construction, delayed commit, empty output, open/protocol/cancellation failure, and retry identity.

- [ ] **Step 2：运行测试并确认 RED / Run tests and verify RED**

Run: `agent-service/.venv/bin/pytest agent-service/tests/test_conversation_service.py -v`

Expected: FAIL，因为模块尚不存在。 / FAIL because the service module does not exist.

- [ ] **Step 3：实现 ConversationService 与 PersistedTurnStream / Implement service and stream wrapper**

所有同步 Repository 调用放入 AnyIO 线程；返回 SSE 前先打开模型上游。打开失败先持久化 failed 再抛出现有安全异常。流包装器使用取消屏蔽完成数据库收尾和上游关闭。 / Open upstream before response, persist safe failures, and shield finalization.

- [ ] **Step 4：编写失败的 FastAPI endpoint 测试 / Write failing FastAPI endpoint tests**

覆盖固定用户 list/create、客户端 `user_id` 无法覆盖、详情排序与跨用户 404、send 的 `{content}` 校验与 SSE、打开失败 502、中途或空回复的 `event: error`、并发 pending 的 409、最新 failed turn retry、旧 `/chat` 回归。 / Cover ownership, validation, SSE, safe errors, concurrency, retry, and legacy compatibility.

- [ ] **Step 5：实现 lifespan、序列化与 routes / Implement lifespan, serializers, and routes**

从 `Path(os.getenv("AGENT_DB_FILE", "data/agent.db"))` 解析路径。FastAPI lifespan 初始化 Repository 并执行 `recover_pending(1)`，避免仅 import 测试工具就创建数据库。持久化 SSE 成功发送 `delta` 后 `done`；流开始后的失败发送安全 `event: error`，代码为 `model_stream_failed`，不发送 `done`。 / Initialize in lifespan and expose stable JSON/SSE errors.

- [ ] **Step 6：运行 Agent 全量测试 / Run complete Agent suite**

Run: `agent-service/.venv/bin/pytest agent-service/tests -v`

Expected: 所有新旧 Agent 测试通过。 / All new and legacy tests pass.

- [ ] **Step 7：提交 / Commit**

```bash
git add agent-service/src/agent_service/conversation_service.py agent-service/src/agent_service/app.py agent-service/tests/test_conversation_service.py agent-service/tests/test_app.py
git commit -m "feat: expose persisted conversation streams"
```

### Task 3：Next.js Conversation BFF 与类型化客户端 / Next.js Conversation BFF and Typed Client

**文件 / Files:**

- 修改 / Modify: `web/lib/types.ts`、`web/lib/chat.ts`、`web/lib/chat.test.ts`
- 新建 / Create: `web/lib/conversations.ts`、`web/lib/conversations.test.ts`
- 新建 / Create: `web/app/api/conversations/route.ts` 及测试
- 新建 / Create: `web/app/api/conversations/[conversationId]/route.ts` 及测试
- 新建 / Create: `web/app/api/conversations/[conversationId]/turns/route.ts` 及测试
- 新建 / Create: `web/app/api/conversations/[conversationId]/turns/[turnId]/retry/route.ts` 及测试

**接口 / Interfaces:**

- 产出与 Agent JSON 一致的 `TurnStatus`、`ConversationTurn`、`ConversationSummary`、`ConversationDetail`。 / Produce types matching Agent JSON.
- 产出 `listConversations()`、`createConversation()`、`getConversation(id)`、`streamConversationTurn(conversationId, content, onDelta)`、`retryConversationTurn(conversationId, turnId, onDelta)`。
- 把现有命名 SSE 解析抽成共享 `consumeChatStream(response, onDelta)`，但不改变旧 `streamChat` 行为。 / Share SSE consumption without regressing legacy chat.

- [ ] **Step 1：编写失败的 BFF route 测试 / Write failing BFF tests**

断言规范化 `AGENT_SERVICE_URL`、动态 ID URL 编码、method/body/content-type 转发、send/retry SSE 透明代理、JSON 状态保留，以及连接失败或成功但 content-type 错误时映射稳定 502。 / Verify exact proxy behavior and stable failures.

- [ ] **Step 2：运行测试并确认 RED / Run tests and verify RED**

Run: `/Users/stone/.nvm/versions/node/v22.22.2/bin/node web/node_modules/vitest/vitest.mjs run web/app/api/conversations`

Expected: FAIL，因为 routes 尚不存在。 / FAIL because routes do not exist.

- [ ] **Step 3：实现 Conversation BFF routes / Implement BFF routes**

复用 `proxyJson`、`proxyStream`、`serviceUrl`；Next.js 16 动态 `context.params` 是 Promise；BFF 不解析或改写 SSE frame。 / Reuse proxy helpers and preserve SSE bytes.

- [ ] **Step 4：编写失败的客户端与 SSE 测试 / Write failing client and SSE tests**

覆盖精确 URL/body、JSON 解码、UTF-8 delta 顺序、`done`、`error`、畸形 frame、提前 EOF、非 2xx，并保留旧 `streamChat` 测试。 / Cover exact requests and all terminal stream conditions.

- [ ] **Step 5：实现类型、客户端与共享 SSE 消费 / Implement types, clients, and shared SSE consumption**

`streamConversationTurn` 只发送 `{content}`；retry 不发送 user 内容；`event: error` 或未收到 `done` 的 EOF 必须 reject，使 UI 重新加载数据库状态。 / Reject error events and premature EOF.

- [ ] **Step 6：运行 Web 单元测试 / Run Web unit tests**

Run: `/Users/stone/.nvm/versions/node/v22.22.2/bin/node web/node_modules/vitest/vitest.mjs run`

Expected: 全部通过。 / All tests pass.

- [ ] **Step 7：提交 / Commit**

```bash
git add web/lib/types.ts web/lib/chat.ts web/lib/chat.test.ts web/lib/conversations.ts web/lib/conversations.test.ts web/app/api/conversations
git commit -m "feat: proxy persisted conversations"
```

### Task 4：历史会话选择与失败重试 UI / Historical Conversation Selection and Retry UI

**文件 / Files:**

- 修改 / Modify: `web/app/chat/page.tsx`
- 修改 / Modify: `web/app/chat/page.module.css`
- 修改 / Modify: `web/app/chat/page.test.tsx`

**接口 / Interfaces:**

- 使用 Task 3 类型化客户端。 / Consume Task 3 client.
- 产出中英双语、响应式双区域页面，支持历史列表、选中详情、未保存的新会话、继续流式对话和最新失败重试。 / Produce a bilingual responsive history/chat UI.
- 流式显示缓存必须按 conversation/turn 操作标识隔离，切换页面不能串写 delta。 / Isolate display buffers by conversation and operation.

- [ ] **Step 1：用持久化 UI 失败测试替换浏览器内存历史测试 / Write failing persistence UI tests**

mock Task 3 客户端而不是全局 fetch。覆盖：加载并自动选择最近会话；选择旧会话；显示 completed/failed turns；新对话第一次发送时才落库；已有会话只发送新内容；最新失败重试；成功/失败后摘要重新排序；流中切换会话不串写；empty/loading/404/list error；选中按钮、列表标签、live region 和仅锁定活动会话输入。 / Cover loading, selection, creation, continuation, retry, reorder, stream isolation, errors, and accessibility.

- [ ] **Step 2：运行页面测试并确认 RED / Run page tests and verify RED**

Run: `/Users/stone/.nvm/versions/node/v22.22.2/bin/node web/node_modules/vitest/vitest.mjs run web/app/chat/page.test.tsx`

Expected: FAIL，因为页面仍只保存浏览器内存 messages。 / FAIL because the page still owns browser-only history.

- [ ] **Step 3：实现页面状态与服务端操作 / Implement page state and server-backed actions**

mount 时加载摘要；维护 `selectedConversationId`、详情缓存、loading/error 和活动流缓存。新对话第一次发送时先创建 conversation；每个终态都重新加载详情与摘要，使 SQLite 保持权威。 / Reload authoritative server state after every terminal outcome.

- [ ] **Step 4：实现双语历史布局与 failed turn 控件 / Implement bilingual history layout and retry controls**

桌面使用 sidebar/content，窄屏使用堆叠历史选择器；保留键盘焦点、标签、live region、滚动与返回商城链接；删除“模拟回复 / Demo”文案，改为持久化 DeepSeek 对话。 / Preserve accessibility and update obsolete demo copy.

- [ ] **Step 5：运行 Web 测试与生产构建 / Run Web tests and production build**

```bash
/Users/stone/.nvm/versions/node/v22.22.2/bin/node web/node_modules/vitest/vitest.mjs run
/Users/stone/.nvm/versions/node/v22.22.2/bin/node web/node_modules/next/dist/bin/next build
```

Expected: 测试和构建全部通过。 / Tests and build pass.

- [ ] **Step 6：提交 / Commit**

```bash
git add web/app/chat/page.tsx web/app/chat/page.module.css web/app/chat/page.test.tsx
git commit -m "feat: browse persisted chat history"
```

### Task 5：Native 持久化冒烟测试与双语运维文档 / Native Persistence Smoke Test and Bilingual Operations Docs

**文件 / Files:**

- 修改 / Modify: `scripts/smoke_test.py`
- 修改 / Modify: `tests/test_smoke.py`
- 修改 / Modify: `README.md`
- 仅在实现证实设计需修正时修改已批准 spec。 / Modify the approved spec only for verified corrections.

**接口 / Interfaces:**

- 使用前四项公开接口。 / Consume prior public interfaces.
- 产出使用临时 SQLite 和本地假 OpenAI-compatible 模型的零计费 Native 冒烟流程。 / Produce a no-cost Native smoke flow.

- [ ] **Step 1：编写失败的持久化冒烟断言 / Write failing persistence smoke assertions**

为 Agent 设置临时 `AGENT_DB_FILE`，通过 Web BFF 验证：初始列表为空；创建 conversation；第一轮流式成功；重新加载 list/detail 得到持久化内容和标题；第二轮模型请求包含第一轮完成历史；数据库文件存在；客户端不能提交 `user_id`。 / Verify create, two turns, reload, server-owned context, and database creation.

- [ ] **Step 2：运行冒烟测试并确认 RED / Run smoke test and verify RED**

Run: `python3.11 -m unittest discover -s tests -p 'test_smoke.py' -v`

Expected: FAIL，因为现有流程只测试旧 `/api/chat`。 / FAIL because the current smoke flow only uses legacy chat.

- [ ] **Step 3：实现冒烟流程与文档 / Implement smoke flow and docs**

假模型为两次请求返回确定内容并记录上下文。README 中英双语补充 `AGENT_DB_FILE`、固定用户 `1`、单 worker 限制、数据库位置与忽略规则、conversation API、历史页面、失败重试，以及停止 Agent 后重置数据库的步骤；不得暴露本地 LLM key。 / Document operations without exposing the ignored key.

- [ ] **Step 4：运行最终全量验证 / Run fresh full verification**

```bash
agent-service/.venv/bin/pytest -q agent-service/tests
mall-service/.venv/bin/pytest -q mall-service/tests
/Users/stone/.nvm/versions/node/v22.22.2/bin/node web/node_modules/vitest/vitest.mjs run
/Users/stone/.nvm/versions/node/v22.22.2/bin/node web/node_modules/next/dist/bin/next build
python3.11 -m unittest discover -s tests -p 'test_smoke.py' -v
git diff --check
```

Expected: Python、Web、构建、两轮持久化冒烟和 diff 检查全部通过。 / All suites, build, persisted smoke flow, and diff check pass.

- [ ] **Step 5：提交 / Commit**

```bash
git add scripts/smoke_test.py tests/test_smoke.py README.md docs/superpowers/specs/2026-11-05-conversation-persistence-design.md docs/superpowers/plans/2026-11-05-conversation-persistence.md
git commit -m "docs: describe persisted conversation history"
```

对 README、spec 和 plan 选择性暂存，保留现有无关工作区修改。 / Stage documentation hunks selectively and preserve unrelated worktree changes.
