# Three-Module Commerce Monorepo Design / 三模块商城单仓库设计

## Intent / 目标

Restructure the current minimal Python agent skeleton into a monorepo with three independently runnable modules: a mall HTTP service, an agent HTTP service, and a Next.js web application.

将当前最小化 Python Agent 骨架改造成一个包含三个可独立运行模块的单仓库：商城 HTTP 服务、Agent HTTP 服务和 Next.js Web 应用。

The browser communicates only with the Next.js application. The web application proxies product requests to the mall service and chat requests to the agent service. The agent service reaches the mall service over HTTP.

浏览器只与 Next.js 应用通信。Web 应用把商品请求代理到商城服务，把聊天请求代理到 Agent 服务；Agent 服务通过 HTTP 访问商城服务。

## Goals / 具体目标

- Create `agent-service`, `mall-service`, and `web` as separate top-level modules. / 创建 `agent-service`、`mall-service` 和 `web` 三个顶层模块。
- Give each Python service its own package metadata, source tree, tests, and runtime command. / 每个 Python 服务拥有独立的包配置、源码、测试和运行命令。
- Expose a minimal product-list HTTP API from `mall-service` on port 8001. / `mall-service` 在 8001 端口提供最小商品列表 HTTP API。
- Expose a minimal chat HTTP API from `agent-service` on port 8002. / `agent-service` 在 8002 端口提供最小聊天 HTTP API。
- Prove that `agent-service` calls `mall-service` through HTTP for each chat request. / 证明 `agent-service` 在每次聊天请求中通过 HTTP 调用 `mall-service`。
- Expose browser-facing `/api/products` and `/api/chat` routes from Next.js on port 3000. / Next.js 在 3000 端口提供面向浏览器的 `/api/products` 和 `/api/chat` 路由。
- Provide a minimal product page and chat form. / 提供最小商品页面和聊天表单。
- Keep repository and user-facing documentation in Chinese and English. / 仓库文档和面向用户的文案采用中英双语。

## Non-goals / 非目标

- No recommendation rules, cart, checkout, order creation, authentication, or payment. / 不实现推荐规则、购物车、结算、订单创建、认证或支付。
- No database or persistent state; the mall service uses deterministic in-memory sample products. / 不使用数据库或持久化状态；商城服务使用确定的内存示例商品。
- No production model vendor integration; the agent keeps a vendor-neutral provider boundary and a local deterministic provider. / 不接入生产模型厂商；Agent 保留厂商无关的提供方边界和本地确定性实现。
- No Docker, Compose, deployment manifests, service discovery, queues, or shared application package. / 不引入 Docker、Compose、部署清单、服务发现、消息队列或共享应用包。
- No direct browser access to either Python service. / 浏览器不直接访问任何 Python 服务。

## Confirmed Architecture / 已确认架构

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

## Approaches Considered / 方案比较

### Independent modules / 独立模块（已选）

Each service owns its configuration and dependencies. Communication crosses explicit HTTP boundaries. This is slightly more repetitive but preserves deployability and prevents accidental in-process coupling.

每个服务独立管理配置和依赖，模块之间只通过明确的 HTTP 边界通信。该方案会有少量重复配置，但能保持独立部署能力，并防止意外的进程内耦合。

### Shared Python workspace / Python 共享工作区

This centralizes Python configuration but encourages direct imports between services and weakens the intended HTTP boundary.

该方案集中管理 Python 配置，但容易促使服务之间直接导入代码，从而削弱预期的 HTTP 边界。

### Container-first modules / 容器优先模块

This standardizes startup but adds image builds, service networking, and health orchestration before the application boundaries are proven.

该方案可以统一启动方式，但会在应用边界尚未验证之前引入镜像构建、服务网络和健康编排。

## Repository Structure / 仓库结构

```text
niub-shop/
├── agent-service/
│   ├── pyproject.toml
│   ├── src/agent_service/
│   │   ├── agent.py
│   │   ├── app.py
│   │   ├── mall_client.py
│   │   └── providers.py
│   └── tests/
├── mall-service/
│   ├── pyproject.toml
│   ├── src/mall_service/
│   │   └── app.py
│   └── tests/
├── web/
│   ├── app/
│   │   ├── api/chat/route.ts
│   │   ├── api/products/route.ts
│   │   └── page.tsx
│   ├── package.json
│   └── tests/
├── docs/
├── .gitignore
├── LICENSE
└── README.md
```

The current root Python package and tests move into `agent-service`; the root `pyproject.toml` is removed. Historical design and plan documents remain under `docs/`.

当前根目录 Python 包和测试迁移到 `agent-service`，根目录 `pyproject.toml` 被移除；历史设计和实施计划继续保留在 `docs/` 中。

The core `Agent` and `ModelProvider` concepts are retained under the new `agent_service` import path. The old `niub_agent` import path and `niub-agent` CLI are intentionally removed because the current skeleton is uncommitted and has no external compatibility contract.

核心 `Agent` 和 `ModelProvider` 概念迁移到新的 `agent_service` 导入路径。旧的 `niub_agent` 导入路径和 `niub-agent` CLI 将被明确移除，因为当前骨架尚未提交，也不存在外部兼容性契约。

## Mall Service / 商城服务

`mall-service` uses Python 3.11+, FastAPI, and Uvicorn. It owns the product representation and the deterministic sample catalog.

`mall-service` 使用 Python 3.11+、FastAPI 和 Uvicorn，负责商品结构和确定的示例商品目录。

### HTTP contract / HTTP 契约

- `GET /health` returns `{"status": "ok"}`. / `GET /health` 返回 `{"status": "ok"}`。
- `GET /products` returns `{"items": Product[]}`. / `GET /products` 返回 `{"items": Product[]}`。

Each product contains `id: str`, `name: str`, `description: str`, `price_cents: int`, and `currency: str`. Prices use integer minor units to avoid floating-point ambiguity.

每个商品包含 `id: str`、`name: str`、`description: str`、`price_cents: int` 和 `currency: str`。价格使用整数最小货币单位，避免浮点歧义。

## Agent Service / Agent 服务

`agent-service` uses Python 3.11+, FastAPI, Uvicorn, and HTTPX. It retains the vendor-neutral `ModelProvider` protocol and `Agent` abstraction from the current skeleton.

`agent-service` 使用 Python 3.11+、FastAPI、Uvicorn 和 HTTPX，并保留当前骨架中的厂商无关 `ModelProvider` 协议和 `Agent` 抽象。

### HTTP contract / HTTP 契约

- `GET /health` returns `{"status": "ok"}`. / `GET /health` 返回 `{"status": "ok"}`。
- `POST /chat` accepts `{"message": str}` and returns `{"reply": str}`. / `POST /chat` 接收 `{"message": str}`，返回 `{"reply": str}`。

The API trims the message and rejects an empty or whitespace-only value with HTTP 422 before making a mall request.

API 会先去除消息首尾空白，并在调用商城服务之前，以 HTTP 422 拒绝空字符串或纯空白消息。

For every valid chat request, the service obtains `GET {MALL_SERVICE_URL}/products`, builds a prompt containing the user message and catalog context, and passes it to the configured provider. The default deterministic provider makes the flow runnable without credentials. Chat quality and commerce actions are intentionally outside this phase.

每个有效聊天请求都会调用 `GET {MALL_SERVICE_URL}/products`，使用用户消息和商品目录上下文构造提示词，再交给已配置的提供方。默认的确定性提供方无需凭证即可运行。聊天质量和商城操作明确不在本阶段范围内。

`MALL_SERVICE_URL` defaults to `http://localhost:8001`. If the mall service is unavailable or returns an invalid response, `agent-service` returns HTTP 502 with a stable JSON error rather than an internal traceback.

`MALL_SERVICE_URL` 默认值为 `http://localhost:8001`。如果商城服务不可用或返回无效响应，`agent-service` 返回 HTTP 502 和稳定的 JSON 错误，而不是内部堆栈信息。

Service-owned upstream errors use `{"error": {"code": "mall_service_unavailable", "message": "Mall service is unavailable"}}`. Network errors, non-2xx responses, and non-JSON or schema-invalid responses from the mall service all map to this contract without exposing internal details.

服务自身产生的上游错误统一使用 `{"error": {"code": "mall_service_unavailable", "message": "Mall service is unavailable"}}`。商城服务的网络错误、非 2xx 响应、非 JSON 响应或结构无效响应都映射到该契约，不暴露内部细节。

## Web Application / Web 应用

`web` uses Next.js App Router and TypeScript. It renders a single minimal page containing a product list and a chat form.

`web` 使用 Next.js App Router 和 TypeScript，渲染一个包含商品列表和聊天表单的最小页面。

### Browser-facing routes / 浏览器接口

- `GET /api/products` forwards to `${MALL_SERVICE_URL}/products`. / `GET /api/products` 转发到 `${MALL_SERVICE_URL}/products`。
- `POST /api/chat` forwards the JSON body to `${AGENT_SERVICE_URL}/chat`. / `POST /api/chat` 把 JSON 请求体转发到 `${AGENT_SERVICE_URL}/chat`。

The server-only environment variables default to `http://localhost:8001` and `http://localhost:8002`. They are not exposed as `NEXT_PUBLIC_*` values. Proxy routes preserve successful upstream status and JSON. Network failures return HTTP 502 with stable JSON errors.

仅服务端使用的环境变量分别默认指向 `http://localhost:8001` 和 `http://localhost:8002`，不会以 `NEXT_PUBLIC_*` 暴露。代理路由保留成功的上游状态码和 JSON；网络失败返回 HTTP 502 和稳定的 JSON 错误。

For an upstream JSON error, a proxy preserves the upstream status and body. Network failures and non-JSON upstream responses return `{"error": {"code": "upstream_unavailable", "message": "Upstream service is unavailable"}}` with HTTP 502.

如果上游返回 JSON 错误，代理保留上游状态码和响应体；网络失败和非 JSON 上游响应统一返回 HTTP 502，以及 `{"error": {"code": "upstream_unavailable", "message": "Upstream service is unavailable"}}`。

The page displays loading, success, empty, and error states for products. The chat form prevents empty submissions, shows the latest reply, and displays request errors. Visual styling remains intentionally lightweight.

页面为商品列表展示加载、成功、空列表和错误状态。聊天表单阻止空提交，显示最新回复和请求错误。视觉样式刻意保持轻量。

## Validation / 校验

- Python service tests cover health routes, response schemas, HTTP delegation, provider boundaries, and upstream failure mapping. / Python 服务测试覆盖健康检查、响应结构、HTTP 委托、提供方边界和上游失败映射。
- Web tests cover both proxy route handlers and the page's product/chat behavior. / Web 测试覆盖两个代理 Route Handler，以及页面的商品和聊天行为。
- Each module has an independent test command. / 每个模块拥有独立测试命令。
- A final smoke check starts or exercises all three boundaries without requiring a model API key. / 最终冒烟检查覆盖三个服务边界，并且不需要模型 API 密钥。

## Documentation and Developer Experience / 文档与开发体验

The root bilingual README explains the architecture, prerequisites, per-module installation, three-terminal startup order, environment variables, test commands, and sample requests. Each module remains independently installable; the root contains no shared runtime dependency manifest.

根目录中英双语 README 说明架构、前置条件、各模块安装方式、三个终端的启动顺序、环境变量、测试命令和示例请求。每个模块都可独立安装；根目录不包含共享运行时依赖清单。

## Builder Decisions / 留给实施者的决定

- Choose exact compatible dependency versions available in the environment while keeping version constraints reproducible. / 根据环境选择兼容的精确依赖版本，同时保证版本约束可复现。
- Choose deterministic sample product names and values. / 选择确定的示例商品名称和数值。
- Choose lightweight CSS details that preserve the specified UI states. / 选择轻量 CSS 细节，同时保留规定的页面状态。

## Acceptance Criteria / 验收标准

- All three modules exist at the repository root and can be started independently. / 三个模块均位于仓库根目录，并可独立启动。
- `web :3000` proxies products to `mall-service :8001` and chat to `agent-service :8002`. / `web :3000` 把商品请求代理到 `mall-service :8001`，把聊天请求代理到 `agent-service :8002`。
- A chat request causes an HTTP product request from `agent-service` to `mall-service`. / 聊天请求会触发 `agent-service` 到 `mall-service` 的 HTTP 商品请求。
- The browser needs no direct Python service URL or CORS configuration. / 浏览器不需要 Python 服务地址或 CORS 配置。
- Tests pass independently in all modules. / 所有模块的测试均可独立通过。
- The root README is complete in Chinese and English. / 根目录 README 提供完整的中英双语内容。

