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
└── /api/chat ──────► agent-service :8002
                           │
                           └── HTTP ──► mall-service :8001
```

浏览器只访问 Next.js。`web` 是 BFF：商品请求转发给 `mall-service`，聊天请求转发给 `agent-service`；Agent 再通过 HTTP 获取商城商品。

The browser talks only to Next.js. `web` acts as a BFF: product requests go to `mall-service`, chat requests go to `agent-service`, and the agent fetches the mall catalog over HTTP.

## 模块 / Modules

- `mall-service`：FastAPI 商品目录服务，使用内存示例数据。 / FastAPI product catalog backed by deterministic in-memory data.
- `agent-service`：厂商无关 Agent 和聊天 API，通过 HTTP 查询商城。 / Vendor-neutral agent and chat API that queries the mall over HTTP.
- `web`：Next.js App Router 商城页面和服务端代理。 / Next.js App Router storefront and server-side proxies.

## 环境要求 / Prerequisites

- Python 3.11 或更高版本 / Python 3.11 or newer
- Node.js 20.9 或更高版本 / Node.js 20.9 or newer
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
MALL_SERVICE_URL=http://localhost:8001 \
  .venv/bin/uvicorn agent_service.app:app \
    --reload --reload-include '*.yaml' --port 8002
```

`--reload` 监视 Python 文件；安装的 `uvicorn[standard]` 还会通过 `--reload-include '*.yaml'` 监视 YAML。修改配置后，Uvicorn 会重启进程并重新读取配置，这属于开发环境热重载，并不是在运行中的进程内动态修改配置。

`--reload` watches Python files. With the installed `uvicorn[standard]`, `--reload-include '*.yaml'` also watches YAML files. A config edit restarts the process and reloads the file; this is development-time hot reload, not an in-process configuration mutation.

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

这些变量只在服务端使用，不会作为 `NEXT_PUBLIC_*` 暴露给浏览器。

These variables are server-only and are never exposed to the browser as `NEXT_PUBLIC_*` values. If `AGENT_CONFIG_FILE` is unset, health checks still work, but `/chat` returns `503 model_not_configured`.

`llm.local.yaml` 已被 Git 忽略。不要提交、打印或在聊天中粘贴真实密钥；如果密钥曾暴露，请立即在供应商控制台撤销并重新生成。

`llm.local.yaml` is Git-ignored. Never commit, print, or paste a real key into chat. If a key has been exposed, revoke and rotate it immediately in the provider console.

## HTTP 接口 / HTTP APIs

### Mall service · `:8001`

- `GET /health`
- `GET /products`

```bash
curl http://localhost:8001/products
```

### Agent service · `:8002`

- `GET /health`
- `POST /chat`

```bash
curl -N -X POST http://localhost:8002/chat \
  -H 'content-type: application/json' \
  -d '{"messages":[{"role":"user","content":"有哪些商品？"}]}'
```

成功响应是命名 SSE 事件流：多个 `event: delta`，最后是 `event: done`。请求中的 `messages` 是完整多轮历史；服务会在最前面加入 YAML 中的 `system_prompt`。

A successful response is a named SSE stream: one or more `event: delta` events followed by `event: done`. `messages` contains the complete conversation history, and the service prepends the YAML `system_prompt`.

### Web BFF · `:3000`

- `GET /api/products`
- `POST /api/chat`

浏览器端代码只调用这两个相对路径。Browser code calls only these two relative paths.

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

本阶段不包含数据库、用户认证、购物车、结算、订单、支付、模型工具调用、重试或对话持久化。模型接入使用通用 OpenAI-compatible HTTP 协议，不绑定厂商 SDK。

This phase excludes a database, authentication, cart, checkout, orders, payments, model tool calls, retries, and conversation persistence. Model access uses the generic OpenAI-compatible HTTP protocol without a vendor SDK.

## 许可证 / License

Apache License 2.0
