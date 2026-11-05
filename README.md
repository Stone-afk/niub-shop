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
MALL_SERVICE_URL=http://localhost:8001 \
  .venv/bin/uvicorn agent_service.app:app --reload --port 8002
```

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

这些变量只在服务端使用，不会作为 `NEXT_PUBLIC_*` 暴露给浏览器。

These variables are server-only and are never exposed to the browser as `NEXT_PUBLIC_*` values.

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
curl -X POST http://localhost:8002/chat \
  -H 'content-type: application/json' \
  -d '{"message":"有哪些商品？"}'
```

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

生产构建完成后，可运行三模块冒烟测试；它会在空闲端口启动并清理三个临时服务：

After the production build, run the three-module smoke test. It starts all three services on free ports and cleans them up afterward:

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

本阶段不包含数据库、用户认证、购物车、结算、订单、支付或生产模型厂商集成。

This phase intentionally excludes a database, authentication, cart, checkout, orders, payments, and production model-vendor integration.

## 许可证 / License

Apache License 2.0
