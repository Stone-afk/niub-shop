# niub-shop

一个由三个独立模块组成的最小智能商城单仓库：商城 HTTP 服务、Agent HTTP 服务和 Next.js Web 应用。

A minimal intelligent-commerce monorepo with three independent modules: a mall HTTP service, an agent HTTP service, and a Next.js web application.

## 架构 / Architecture

```text
Browser / 浏览器
        │
        ▼
web · Next.js :3000
├── /api/products ──► mall-service :8001
├── /api/chat ──────► agent-service :8002
└── /api/conversations/* ──► agent-service :8002
                           │
                           └── HTTP ──► mall-service :8001
```

浏览器只访问 Next.js。`web` 是 BFF：商品请求转发给 `mall-service`，聊天请求转发给 `agent-service`；Agent 再通过 HTTP 获取商城商品。

The browser talks only to Next.js. `web` acts as a BFF: product requests go to `mall-service`, chat requests go to `agent-service`, and the agent fetches the mall catalog over HTTP.

## 模块 / Modules

- `mall-service`：FastAPI 商品目录服务，使用带分类、标签和库存状态的确定性内存示例数据。 / FastAPI product catalog backed by deterministic in-memory data with categories, tags, and availability.
- `agent-service`：聊天 API 与 OpenAI Agents SDK ReAct Agent；通过配置的 OpenAI-compatible Chat Completions endpoint，结合用户上下文、商城推荐和广告推荐按需查询商城。 / Chat API and an OpenAI Agents SDK ReAct agent that combines user context, organic mall recommendations, and sponsored recommendations through the configured OpenAI-compatible Chat Completions endpoint.
- `web`：Next.js App Router 商城页面和服务端代理。 / Next.js App Router storefront and server-side proxies.

## 环境要求 / Prerequisites

- Python 3.11 或更高版本 / Python 3.11 or newer
- Node.js 20.19 或更高的 20.x，或 Node.js 22.12 及更高版本 / Node.js 20.19+ on 20.x, or Node.js 22.12+
- npm 10 或更高版本 / npm 10 or newer

本机默认 `python` 可能不是 3.11，请在创建环境时明确使用 `python3.11`。

The system `python` command may not resolve to 3.11; use `python3.11` explicitly when creating environments.

## 安装 / Installation

每个 Python 服务使用自己的虚拟环境：

Each Python service uses its own virtual environment:

```bash
python3.11 -m venv mall-service/.venv
mall-service/.venv/bin/python -m pip install -e './mall-service[test]'

python3.11 -m venv agent-service/.venv
agent-service/.venv/bin/python -m pip install -e './agent-service[test]'

cp agent-service/config/llm.example.yaml agent-service/config/llm.local.yaml
# 编辑 llm.local.yaml，填入新生成的 API key、base_url 和 model。
# Edit llm.local.yaml with a newly generated API key, base_url, and model.

cd web
npm install
cd ..
```

## 启动 / Run

按顺序打开三个终端。Open three terminals in this order.

### 1. 商城服务 / Mall service

```bash
cd mall-service
.venv/bin/uvicorn mall_service.app:app --reload --port 8001
```

### 2. Agent 服务 / Agent service

```bash
cd agent-service
AGENT_CONFIG_FILE=./config/llm.local.yaml \
AGENT_DB_FILE=./data/agent.db \
MALL_SERVICE_URL=http://localhost:8001 \
  .venv/bin/uvicorn agent_service.app:app \
    --reload --reload-include '*.yaml' --port 8002
```

提示词保存在 `agent-service/prompts/*.md`，`llm.local.yaml` 通过 `prompt_file` 和 `recommendation_policy_prompt_file` 指向它们。每次请求都会重新读取 Markdown，因此修改提示词后无需重启服务；`--reload` 仍用于 Python 和 YAML 配置变更。

Prompt files are reloaded on every request. `--reload` remains available for Python and YAML configuration changes.

提示词文件不会在进程内缓存。文件缺失、为空、不是合法 UTF-8 或超过 1 MiB 时，服务会返回安全的提示词配置错误。

Prompt files are not cached in-process. Missing, empty, invalid UTF-8, or over-1 MiB files return a safe prompt-configuration error.

### 3. Web 应用 / Web application

```bash
cd web
MALL_SERVICE_URL=http://localhost:8001 \
AGENT_SERVICE_URL=http://localhost:8002 \
  npm run dev
```

打开 [http://localhost:3000](http://localhost:3000)。

Open [http://localhost:3000](http://localhost:3000).

## 环境变量 / Environment variables

| Variable / 变量 | Used by / 使用方 | Default / 默认值 |
| --- | --- | --- |
| `MALL_SERVICE_URL` | `agent-service`, `web` | `http://localhost:8001` |
| `AGENT_SERVICE_URL` | `web` | `http://localhost:8002` |
| `AGENT_CONFIG_FILE` | `agent-service` | 未设置 / unset |
| `AGENT_DB_FILE` | `agent-service` | `data/agent.db`（相对启动目录 / relative to the working directory） |

这些变量只在服务端使用，不会作为 `NEXT_PUBLIC_*` 暴露给浏览器。

These variables are server-only and are never exposed to the browser as `NEXT_PUBLIC_*` values. If `AGENT_CONFIG_FILE` is unset, health checks still work, but `/chat` returns `503 model_not_configured`.

`llm.local.yaml` 已被 Git 忽略。不要提交、打印或在聊天中粘贴真实密钥；如果密钥曾暴露，请立即在供应商控制台撤销并重新生成。

`llm.local.yaml` is Git-ignored. Never commit, print, or paste a real key into chat. If a key has been exposed, revoke and rotate it immediately in the provider console.

## HTTP 接口 / HTTP APIs

### Mall service · `:8001`

- `GET /health`
- `GET /products`
- `GET /users/{user_id}/context`
- `GET /users/{user_id}/external-profile`
- `GET /users/{user_id}/recommendations`
- `GET /users/{user_id}/ads/recommendations`

```bash
curl http://localhost:8001/products
```

每个商品包含 `id`、双语名称与描述、以最小货币单位表示的 `price_cents`、`currency`、`category`、`tags`，以及 `in_stock | out_of_stock` 库存状态。mock 目录是确定性的；搜索工具只把当前有货商品交给模型，但历史卡片始终显示商城的当前库存。

Each product contains an `id`, bilingual name and description, minor-unit `price_cents`, `currency`, `category`, `tags`, and `in_stock | out_of_stock` availability. The mock catalog is deterministic. Product search exposes only currently in-stock items to the model, while historical cards show the catalog's current availability.

### Agent service · `:8002`

- `GET /health`
- `POST /chat`
- `GET /conversations`
- `POST /conversations`
- `GET /conversations/{conversation_id}`
- `POST /conversations/{conversation_id}/turns`
- `POST /conversations/{conversation_id}/turns/{turn_id}/retry`

```bash
curl -N -X POST http://localhost:8002/chat \
  -H 'content-type: application/json' \
  -d '{"messages":[{"role":"user","content":"有哪些商品？"}]}'
```

无状态 `/chat` 的成功响应是命名 SSE 事件流：多个 `event: delta`，最后是 `event: done`。请求中的 `messages` 是完整多轮历史；服务会在最前面加入 `prompt_file` 指向的系统提示词。

A successful stateless `/chat` response is a named SSE stream: one or more `event: delta` events followed by `event: done`. `messages` contains the complete conversation history, and the service prepends the prompt loaded from `prompt_file`.

`POST /chat` 是无状态兼容接口。`/chat` 页面使用新的 conversation API：浏览器只发送本轮内容，Agent 从 SQLite 读取已完成轮次并构造模型上下文。当前为单用户本地模式，所有请求由服务端固定归属 `user_id = 1`；客户端不能指定或覆盖用户 ID。

`POST /chat` remains the stateless compatibility endpoint. The `/chat` page uses the new conversation API: the browser sends only the new turn, while Agent rebuilds model context from completed SQLite turns. This local version is single-user; the server assigns every request to fixed `user_id = 1`, and clients cannot supply or override it.

会话回复成功时才保存完整 Agent 内容。中断或失败不会保存部分回复，页面显示“回复失败，可重试 / Reply failed, retry available”；仅最新的失败轮次可以重试。会话按最近活动排序，第一条用户消息自动成为标题。

An assistant answer is saved only after the stream completes. Interrupted or failed streams discard partial content and show a bilingual retry state. Only the newest failed turn is retryable. Conversations are ordered by recent activity, and the first user message becomes the title.

## 商品推荐 Agent / Product recommendation agent

持久化 conversation API 使用 OpenAI Agents SDK 管理 ReAct 循环，执行以下受控流程：

The persisted conversation API uses the OpenAI Agents SDK to manage the ReAct loop:

```text
Agents SDK Runner 执行 Agent / Agents SDK Runner executes the Agent
  → 模型加载 Agent 内部画像、外部画像和用户上下文 / model loads Agent-owned profile, external profile, and user context
  → 模型查询商城自然推荐与广告推荐 / model loads organic and sponsored candidates
  → 必要时调用 search_products 做结构化筛选 / optionally narrows candidates with search_products
  → SDK 将工具结果回传模型继续推理 / SDK returns tool results for another reasoning step
  → 模型调用 submit_product_recommendation
  → Agent 校验回答与商品 ID / validates answer and product IDs
  → 原子保存并发送 SSE / atomically persists and emits SSE
```

`get_agent_profile` 返回 Agent 服务内部维护的画像；`get_external_profile` 返回推荐平台画像 mock；`get_user_context` 返回结构化浏览、订单和购物车记录，并支持新用户空数组；`get_product_recommendations` 返回商城自然推荐；`get_ad_recommendations` 返回带 `sponsored` 来源的广告推荐。任意单个来源为空或不可用时，Agent 会继续使用其它来源。最近购买过的商品会在工具层过滤，避免直接重复推荐。`search_products` 支持可选的 `keyword`、`category`、`min_price_cents`、`max_price_cents`、`sort`（`relevance | price_asc | price_desc`）和 `limit`（1–10，默认 5）。模型每轮最多搜索三次，可调整条件后再次查询。未知字段、负数价格、倒置区间和超限数量都会在访问商城前被拒绝。

`search_products` accepts optional `keyword`, `category`, `min_price_cents`, `max_price_cents`, `sort` (`relevance | price_asc | price_desc`), and `limit` (1–10, default 5). The model may refine its filters but can search at most three times per turn. Unknown fields, negative prices, inverted ranges, and invalid limits are rejected before Mall access.

最终提交包含自然语言 `answer` 和最多三个 `product_ids`。每个 ID 必须来自本轮成功工具结果；重复 ID 会按首次出现顺序去重，虚构或未经工具返回的 ID 会使整轮失败，不会部分保存。商品来源同步保存为 `organic` 或 `sponsored`；最多一个广告商品，商品不足三个时允许少于三个。无可信候选时返回空数组，并提示用户可以尝试其它平台。工具参数、原始工具结果和内部调用轨迹不会作为聊天消息持久化。

The final submission contains a natural-language `answer` and up to three `product_ids`. Every ID must come from a successful tool result in the same turn. Duplicate IDs are removed in first-seen order; invented or unreturned IDs invalidate the whole turn, and nothing is partially persisted. Product sources are persisted as `organic` or `sponsored`; at most one sponsored product is allowed, and fewer than three are valid when fewer candidates exist. If no trusted candidate exists, the result contains an empty list and suggests trying other platforms. Tool arguments, raw tool results, and internal traces are not stored as chat messages.

conversation 成功流的事件顺序固定如下，即使没有推荐商品也会发送一次空数组：

A successful conversation stream always uses this order, including one empty array when no products are recommended:

```text
event: delta              # one or more validated answer chunks / 已校验回答分片
event: recommendations    # {"product_ids":[...],"product_sources":["organic", "sponsored"]}
event: done               # {}
```

回答、有序商品 ID 和商品来源在同一个 SQLite 事务中保存。刷新或切换历史会话后，Web 从详情接口恢复 ID 与来源，并用当前 `/api/products` 目录渲染只读卡片：名称、价格、描述、双语库存状态，以及广告标识“广告 / Sponsored”。目录中已不存在的历史 ID 会显示“商品暂不可用 / Product unavailable”，不会伪造商品信息。目录加载失败不影响文字对话。

The answer, ordered product IDs, and product sources are stored in one SQLite transaction. After refresh or history selection, Web restores the IDs and sources from conversation detail and renders read-only cards from the current `/api/products` catalog: name, price, description, bilingual availability, and a “广告 / Sponsored” badge. A historical ID missing from the catalog shows “商品暂不可用 / Product unavailable” instead of invented data. Catalog loading failures do not block text chat.

模型协议、工具参数、商城访问或最终校验失败时，本轮标记为 failed，不保存部分回答或商品 ID；页面允许重试最新失败轮次。所有公开错误使用稳定代码，且不包含模型响应、工具正文或密钥。

If model protocol, tool arguments, Mall access, or final validation fails, the turn becomes failed without partial answer or product IDs. The UI can retry the newest failed turn. Public errors use stable codes and never include model responses, tool bodies, or secrets.

SDK Agent 使用 YAML 的 `base_url`、`api_key`、`model` 和 `timeout_seconds` 构造 OpenAI-compatible Chat Completions 模型；DeepSeek 等非 OpenAI endpoint 默认关闭 Agents SDK tracing。/ The SDK Agent builds an OpenAI-compatible Chat Completions model from YAML `base_url`, `api_key`, `model`, and `timeout_seconds`; Agents SDK tracing is disabled by default for non-OpenAI endpoints such as DeepSeek.

### Web BFF · `:3000`

- `GET /api/products`
- `POST /api/chat`
- `GET`, `POST /api/conversations`
- `GET /api/conversations/{conversation_id}`
- `POST /api/conversations/{conversation_id}/turns`
- `POST /api/conversations/{conversation_id}/turns/{turn_id}/retry`

浏览器端代码只调用这些同源相对路径。Browser code calls only these same-origin relative paths.

## 会话数据运维 / Conversation data operations

默认从 `agent-service/` 启动时，数据库位于 `agent-service/data/agent.db`，该目录已被 Git 忽略。SQLite 会自动启用 foreign keys、WAL 和 5000ms busy timeout。这个本地模式只支持一个 Agent worker；可以使用 `--reload`，但不要使用 `--workers` 设置多个服务 worker。

When Agent starts from `agent-service/`, the default database is `agent-service/data/agent.db`, and that directory is Git-ignored. SQLite automatically enables foreign keys, WAL, and a 5000ms busy timeout. This local mode supports one Agent worker only: development `--reload` is supported, but do not configure multiple `--workers`.

如需重置全部历史，先停止 Agent，再删除数据库及 WAL 辅助文件，之后重新启动：

To reset all history, stop Agent first, remove the database and its WAL sidecars, then restart:

```bash
rm agent-service/data/agent.db \
  agent-service/data/agent.db-shm \
  agent-service/data/agent.db-wal
```

如果文件不存在，`rm` 会提示；可只删除实际存在的文件。不要在 Agent 运行时删除数据库。 / If a file is absent, `rm` reports it; remove only files that exist. Never delete the database while Agent is running.

## 测试 / Tests

分别运行模块测试：

Run each module test suite independently:

```bash
mall-service/.venv/bin/python -m pytest -v mall-service/tests
agent-service/.venv/bin/python -m pytest -v agent-service/tests

cd web
npm test
npm run build
cd ..
```

生产构建完成后，可运行端到端冒烟测试；它会在空闲端口启动并清理 Mall、Agent、Web 和一个本地假的 OpenAI-compatible SSE 模型服务，不会调用或计费真实模型：

After the production build, run the end-to-end smoke test. It starts Mall, Agent, Web, and a local fake OpenAI-compatible SSE model on free ports, then cleans them up. It never calls or bills a real model:

```bash
python3.11 -m unittest discover -s tests -p 'test_smoke.py' -v
```

## 项目结构 / Project structure

```text
agent-service/   # Agent core, mall HTTP client, and chat API / Agent 核心、商城客户端与聊天 API
mall-service/    # Product catalog HTTP API / 商品目录 HTTP API
web/             # Next.js storefront and BFF / Next.js 商城页面与 BFF
scripts/         # Cross-service verification / 跨服务验证
tests/           # Root smoke test / 根目录冒烟测试
docs/            # Bilingual design and implementation documents / 中英双语设计与实施文档
```

## 当前范围 / Current scope

当前已包含单用户本地 SQLite 会话持久化、历史选择、继续对话、失败轮次重试、结构化商品搜索工具和历史/实时商品卡片。不包含登录认证、多用户隔离、云数据库、跨设备同步、购物车、结算、订单、支付、个性化画像或向量检索。模型接入使用通用 OpenAI-compatible HTTP 协议，不绑定厂商 SDK。

The current scope includes single-user local SQLite persistence, history selection, conversation continuation, failed-turn retry, structured product-search tools, and historical/live product cards. It excludes login, multi-user isolation, cloud databases, cross-device sync, cart, checkout, orders, payments, personalization, and vector search. Model access uses the generic OpenAI-compatible HTTP protocol without a vendor SDK.

## 许可证 / License

Apache License 2.0
