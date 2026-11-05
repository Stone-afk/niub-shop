# 会话持久化与历史选择设计 / Conversation Persistence and History Selection Design

## 目标 / Intent

为 `agent-service` 增加单用户本地 SQLite 会话存储，并把 `/chat` 页面从浏览器内存历史改造成可创建、选择、查看和继续历史会话的界面。所有浏览器共享同一份数据；刷新页面、关闭浏览器或重启服务后历史仍然存在。

Add single-user local SQLite conversation storage to `agent-service`, then change the `/chat` page from browser-memory history to a UI that can create, select, inspect, and continue persisted conversations. Every browser shares the same data, which survives refreshes, browser restarts, and service restarts.

## 已确认范围 / Confirmed Scope

- 单用户本地模式；不增加登录、用户表或浏览器身份隔离。
- 历史会话可以继续发送消息，并延续原来的模型上下文。
- 不完整的流式回复不作为正常 Agent 消息保存；本轮显示“回复失败，可重试”。
- 第一版不提供删除、重命名或搜索。
- 会话标题由第一条用户消息自动生成并截断。
- 会话按最近活动时间倒序排列。

- Single-user local mode, with no authentication, user table, or browser identity.
- A historical conversation can be continued with its prior model context.
- A partial streamed answer is never stored as a successful Agent message; the turn becomes “failed, retry available.”
- Version one excludes deletion, renaming, and search.
- The first user message generates a truncated conversation title.
- Conversations are ordered by most recent activity.

## 方案 / Approach

采用 Python 标准库 `sqlite3` 和显式 Repository 层，不引入 ORM。数据库工作通过 AnyIO 线程池执行，避免阻塞 FastAPI/SSE 事件循环。SQLite 开启 foreign keys、WAL 和 busy timeout。

Use Python’s standard-library `sqlite3` behind an explicit repository layer, without an ORM. Database work runs in an AnyIO worker thread so it does not block the FastAPI/SSE event loop. SQLite enables foreign keys, WAL, and a busy timeout.

未采用的方案：

- SQLAlchemy：迁移和模型能力更强，但对当前最小项目增加过多结构与依赖。
- LocalStorage：无法让不同浏览器共享历史，也不能让后端成为模型上下文的可信来源。

Rejected approaches:

- SQLAlchemy offers richer models and migrations but adds disproportionate structure and dependencies here.
- LocalStorage cannot share history across browsers and cannot make the backend the authoritative source of model context.

## 数据模型 / Data Model

### `conversations`

- `id TEXT PRIMARY KEY`：由服务端生成的 UUID。
- `title TEXT NOT NULL`：新会话初始为“新对话 / New conversation”，第一条用户消息写入时自动更新。
- `created_at TEXT NOT NULL`：UTC ISO-8601。
- `updated_at TEXT NOT NULL`：创建轮次、成功、失败或重试时更新。

### `turns`

- `id TEXT PRIMARY KEY`：UUID。
- `conversation_id TEXT NOT NULL`：外键，级联删除为未来能力预留。
- `user_content TEXT NOT NULL`。
- `assistant_content TEXT NULL`：仅在完整成功后写入。
- `status TEXT NOT NULL`：`pending | completed | failed`，带 CHECK constraint。
- `error_code TEXT NULL`：稳定的内部错误代码，不保存供应商正文或密钥。
- `created_at TEXT NOT NULL`、`updated_at TEXT NOT NULL`。

每个 conversation 最多允许一个 `pending` turn，通过 SQLite partial unique index 保证。服务启动时将遗留的 `pending` turn 标记为 `failed`，覆盖进程崩溃场景。第一版只支持一个 `agent-service` worker；不得使用 `uvicorn --workers > 1` 共享这个本地数据库。开发环境的 `--reload` 仍只有一个服务 worker，并依赖 Uvicorn 的优雅关闭先处理旧进程请求。

Each conversation allows at most one `pending` turn, enforced with a partial unique index. At service startup, stale `pending` turns become `failed`, covering process crashes. Version one supports exactly one `agent-service` worker; `uvicorn --workers > 1` against this local database is unsupported. Development `--reload` still runs one serving worker and relies on Uvicorn graceful shutdown before replacement.

## Agent API

保留现有 `POST /chat` 作为旧的无状态兼容接口，商城首页和既有调用不会被本功能破坏。持久化聊天使用以下新接口：

Keep the existing stateless `POST /chat` for backward compatibility. Persisted chat uses these new endpoints:

- `GET /conversations`：返回会话摘要列表 `{id, title, created_at, updated_at, last_turn_status}`。
- `POST /conversations`：创建空会话并返回摘要。Web 只在新会话第一次发送消息时调用，避免点击“新对话”后留下空记录。
- `GET /conversations/{conversation_id}`：返回会话及全部 turns；不存在返回稳定 404。
- `POST /conversations/{conversation_id}/turns`，请求 `{content}`：创建 `pending` turn，打开模型上游，并返回现有 named SSE `delta` / `done` 协议。
- `POST /conversations/{conversation_id}/turns/{turn_id}/retry`：仅允许重试该会话最新且状态为 `failed` 的 turn；成功和失败使用同一 SSE/错误约定。

如果 conversation 已有 `pending` turn，新的发送或重试返回 409。输入继续使用 Pydantic 去空白和非空校验。

If a conversation already has a pending turn, send or retry returns 409. Input retains Pydantic trimming and non-empty validation.

## 模型上下文 / Model Context

模型请求由后端数据库构造，浏览器不再提交完整历史：

1. 配置中的 system prompt。
2. 按时间排序的所有 `completed` turns，展开为 user/assistant 消息。
3. 当前 pending 或 retry turn 的 user 消息。

`failed` turns 不进入后续模型上下文。只有最新的 failed turn 可重试；一旦用户继续发送新消息，较早失败轮次仍可查看，但不再提供重试按钮，避免在已经继续的时间线上插入旧回答。

The backend builds model context from the database: the configured system prompt, every completed turn expanded to user/assistant messages in order, then the current turn’s user message. Failed turns are excluded. Only the newest failed turn is retryable; after a later turn exists, older failures remain visible but cannot rewrite the timeline.

## 流式写入与失败 / Streaming and Failure Semantics

1. 在连接模型前创建 `pending` turn，并更新会话标题和活动时间。
2. 在内存中累计已发送给浏览器的 assistant delta，不逐 token 写 SQLite。
3. 收到有效上游 `[DONE]` 后，在一次事务中写入完整 `assistant_content` 并改为 `completed`，然后发送下游 `done`。
4. 上游打开失败时将 turn 标记为 `failed`，返回稳定 JSON 502。
5. 中途协议错误、超时或浏览器断开时，在取消屏蔽的清理路径中将 turn 标记为 `failed`，不保存部分 assistant 内容，也不发送 `done`。
6. Retry 将同一 turn 改回 `pending`；成功覆盖为空的 assistant 内容并改为 `completed`，失败仍为 `failed`。

The complete assistant message is committed only after valid upstream completion. Open-time failures return stable JSON errors; midstream failures and cancellation leave no partial assistant text and never emit a false `done`.

## Web 与 BFF / Web and BFF

Next.js 增加对上述 conversation API 的同源代理。`/chat` 页面：

- 首次加载会话列表，并默认选择最近会话；没有历史时显示新对话空态。
- 提供“新对话 / New chat”，进入尚未持久化的空白状态。
- 展示历史会话列表；选择后加载完整 turns。
- completed turn 展示 user 和 Agent 消息；failed turn 展示用户消息及“回复失败，可重试”。
- 新消息或 retry 期间锁定当前会话输入，其他会话仍可查看；为避免流写入错误会话，切换会话不会取消后台请求，delta 按 conversation/turn id 更新对应缓存。
- 每次成功或失败后刷新摘要排序和状态。

Next.js adds same-origin proxies for the conversation API. The page loads and selects the most recent conversation, supports an unsaved “new chat” state, loads complete turns on selection, shows failed turns with retry, and refreshes summary ordering after activity.

## 数据位置与配置 / Storage Location and Configuration

- 新环境变量 `AGENT_DB_FILE`，默认值是 `data/agent.db`，相对启动进程的当前工作目录解析；项目文档要求从 `agent-service/` 启动，因此标准位置为 `agent-service/data/agent.db`。从其他目录启动时必须显式设置绝对路径。
- 自动创建父目录和 schema；`agent-service/data/` 被 Git 忽略。
- 不把数据库文件、模型密钥、供应商错误正文或部分回复提交到 Git。

- `AGENT_DB_FILE` selects the database. The default `data/agent.db` is resolved from the process working directory; documented commands start inside `agent-service/`, yielding `agent-service/data/agent.db`. Launches from elsewhere must provide an absolute path.
- The service creates the parent directory and schema automatically; `agent-service/data/` is Git-ignored.
- Database files, model keys, vendor error bodies, and partial replies are never committed.

## 测试 / Verification

- Repository 测试：schema、排序、完整上下文、并发 pending 约束、失败、重试和重启恢复。
- Agent API 测试：列表/详情/404/409、SSE 成功、打开失败、中途失败、取消以及不保存部分内容。
- Web 测试：加载列表、选择历史、新建会话、继续对话、失败重试和会话切换时的流归属。
- BFF 测试：路径、JSON 状态和 SSE 透明转发。
- Native smoke test 使用临时 SQLite 和本地假模型，验证创建、对话、刷新列表、重新读取历史和继续对话，全程不调用计费模型。

## 非目标 / Non-goals

用户认证、多用户隔离、云数据库、跨设备同步服务、删除、重命名、搜索、分页、附件、工具调用、分支对话和供应商级重试均不在第一版范围内。

Authentication, multi-user isolation, cloud storage, deletion, renaming, search, pagination, attachments, tools, conversation branching, and provider-level retries are outside version one.
