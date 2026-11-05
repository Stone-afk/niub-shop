# Three-Module Commerce Monorepo Implementation Plan / 三模块商城单仓库实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **给自动化实施者：** 必须使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans` 逐项实施本计划。所有步骤使用复选框（`- [ ]`）跟踪进度。

**Goal / 目标：** Restructure the repository into an independently runnable FastAPI agent service, FastAPI mall service, and Next.js web application connected through the approved HTTP boundaries. / 将仓库改造成可独立运行的 FastAPI Agent 服务、FastAPI 商城服务和 Next.js Web 应用，并按已确认的 HTTP 边界连接。

**Architecture / 架构：** The browser talks only to the Next.js BFF. The BFF proxies products to `mall-service` and chat to `agent-service`; each chat request makes an HTTP product request from `agent-service` to `mall-service`. Each top-level module owns its dependencies and tests. / 浏览器只访问 Next.js BFF；BFF 把商品请求代理到 `mall-service`，把聊天请求代理到 `agent-service`；每次聊天请求都会触发 `agent-service` 通过 HTTP 查询 `mall-service`。每个顶层模块独立管理依赖和测试。

**Tech Stack / 技术栈：** Python 3.11+, FastAPI 0.x, Uvicorn 0.x, HTTPX 0.x, pytest 8/9; Node.js 20.9+, Next.js 16.4.5, React 19.3.1, TypeScript 5.x, Vitest 4.x, Testing Library. Python dependencies use bounded major-version constraints; `package-lock.json` pins the web dependency graph. / Python 3.11+、FastAPI 0.x、Uvicorn 0.x、HTTPX 0.x、pytest 8/9；Node.js 20.9+、Next.js 16.4.5、React 19.3.1、TypeScript 5.x、Vitest 4.x、Testing Library。Python 依赖使用主版本边界，Web 依赖图由 `package-lock.json` 锁定。

**Spec / 设计文档：** `docs/superpowers/specs/2026-11-05-three-module-monorepo-design.md`

## Global Constraints / 全局约束

- Keep `agent-service`, `mall-service`, and `web` independently installable and runnable. / 保持三个模块可独立安装和运行。
- Service communication crosses HTTP boundaries only; do not create a shared runtime package or cross-import service source. / 服务之间只通过 HTTP 通信，不创建共享运行时包，也不跨服务导入源码。
- Use ports 8001 for `mall-service`, 8002 for `agent-service`, and 3000 for `web`. / 固定使用 8001、8002 和 3000 端口。
- Browser code calls only `/api/products` and `/api/chat`; Python service URLs remain server-only. / 浏览器代码只调用 `/api/products` 和 `/api/chat`，Python 服务地址仅在服务端使用。
- Keep documentation and visible UI copy complete in Chinese and English. / 文档和可见 UI 文案提供完整中英双语。
- Do not add a database, authentication, cart, checkout, order, payment, model-vendor integration, Docker, or Compose. / 不添加数据库、认证、购物车、结算、订单、支付、模型厂商集成、Docker 或 Compose。
- Preserve `LICENSE` and the historical documents under `docs/`. / 保留 `LICENSE` 和 `docs/` 下的历史文档。

## Review Focus / 审查重点

- A whitespace-only chat message must return 422 without making a mall request; Task 3 pins this with a dependency spy. / 纯空白聊天消息必须返回 422 且不调用商城；任务 3 使用依赖记录器固定该行为。
- Mall network failures, non-2xx responses, non-JSON responses, and invalid product schemas must all map to the stable 502 contract; Task 2 tests all four classes. / 商城网络失败、非 2xx、非 JSON 和商品结构无效都必须映射为稳定 502 契约；任务 2 覆盖这四类情况。
- Base URLs with a trailing slash must not produce double-slash paths; Tasks 2 and 4 exercise trailing-slash configuration. / 带尾部斜杠的服务地址不能生成双斜杠路径；任务 2 和任务 4 覆盖该配置。
- Web proxies must preserve JSON application errors and statuses while converting transport and non-JSON failures to 502; Task 4 covers both branches. / Web 代理必须保留 JSON 应用错误及状态码，并把传输与非 JSON 错误转换为 502；任务 4 覆盖两个分支。
- The page must recover from product and chat request failures without losing the current form input; Task 5 asserts both error states and retained input. / 页面必须能从商品和聊天请求失败中恢复，且不丢失当前表单输入；任务 5 断言两类错误状态和输入保留。

---

### Task 1: Mall service product API / 任务 1：商城商品 API

**Files / 文件：**

- Create / 创建：`mall-service/pyproject.toml`
- Create / 创建：`mall-service/src/mall_service/__init__.py`
- Create / 创建：`mall-service/src/mall_service/models.py`
- Create / 创建：`mall-service/src/mall_service/catalog.py`
- Create / 创建：`mall-service/src/mall_service/app.py`
- Create / 创建：`mall-service/tests/test_app.py`

**Interfaces / 接口：**

- Consumes / 输入依赖：none / 无
- Produces / 对外提供：`Product(id, name, description, price_cents, currency)`, `GET /health -> {"status":"ok"}`, `GET /products -> {"items": Product[]}`

- [ ] **Step 1 / 步骤 1：Create package metadata with `fastapi>=0.115,<1`, `uvicorn>=0.30,<1`, and test extras `httpx>=0.27,<1`, `pytest>=8,<10`; install with `/Users/stone/.local/bin/python3.11 -m pip install -e './mall-service[test]'`. / 创建包配置并安装商城服务及测试依赖。**
- [ ] **Step 2 / 步骤 2：Write `test_health_returns_ok`, `test_products_returns_deterministic_typed_catalog`, and `test_product_prices_are_non_negative_integer_minor_units` in `mall-service/tests/test_app.py`; assert the exact response keys and at least two deterministic products. / 编写健康检查、确定商品目录和整数价格测试，断言精确响应字段及至少两个确定商品。**
- [ ] **Step 3 / 步骤 3：Run `cd mall-service && /Users/stone/.local/bin/python3.11 -m pytest -v`; expect import/collection failure because `mall_service.app` is absent. / 运行测试，确认因商城应用尚不存在而失败。**
- [ ] **Step 4 / 步骤 4：Implement `Product` and `ProductList` Pydantic models, `list_products() -> tuple[Product, ...]`, and FastAPI `app` with the two routes. / 实现商品模型、确定目录函数和两个 FastAPI 路由。**
- [ ] **Step 5 / 步骤 5：Run `cd mall-service && /Users/stone/.local/bin/python3.11 -m pytest -v`; expect all mall tests to pass. / 运行商城测试，确认全部通过。**
- [ ] **Step 6 / 步骤 6：Commit `mall-service` as `feat: add mall product service`. / 提交商城商品服务。**

### Task 2: Agent core and mall HTTP client / 任务 2：Agent 核心与商城 HTTP 客户端

**Files / 文件：**

- Create / 创建：`agent-service/pyproject.toml`
- Create / 创建：`agent-service/src/agent_service/__init__.py`
- Create / 创建：`agent-service/src/agent_service/agent.py`
- Create / 创建：`agent-service/src/agent_service/providers.py`
- Create / 创建：`agent-service/src/agent_service/mall_client.py`
- Create / 创建：`agent-service/tests/test_agent.py`
- Create / 创建：`agent-service/tests/test_mall_client.py`

**Interfaces / 接口：**

- Consumes / 输入依赖：Task 1 `GET /products` response shape / 任务 1 的商品响应结构
- Produces / 对外提供：`ModelProvider.complete(prompt: str) -> str`, `EchoProvider.complete(prompt: str) -> str`, `Agent.run(message: str, products: list[ProductData]) -> str`, `MallClient.list_products() -> list[ProductData]`, `MallServiceUnavailable`

- [ ] **Step 1 / 步骤 1：Create package metadata with `fastapi>=0.115,<1`, `uvicorn>=0.30,<1`, `httpx>=0.27,<1`, and `pytest>=8,<10`; install with `/Users/stone/.local/bin/python3.11 -m pip install -e './agent-service[test]'`. / 创建包配置并安装 Agent 服务及测试依赖。**
- [ ] **Step 2 / 步骤 2：Write Agent tests proving structural provider compatibility and that `run` passes a prompt containing the exact message plus product names and integer prices. / 编写 Agent 测试，证明提供方结构兼容，并断言提示词包含精确消息、商品名称和整数价格。**
- [ ] **Step 3 / 步骤 3：Write MallClient tests using `httpx.MockTransport` for success, trailing-slash base URL, timeout/network error, non-2xx, non-JSON, and schema-invalid product data; the five failure cases raise `MallServiceUnavailable`. / 使用 `httpx.MockTransport` 编写成功、尾部斜杠及四类上游失败测试；所有失败统一抛出 `MallServiceUnavailable`。**
- [ ] **Step 4 / 步骤 4：Run `cd agent-service && /Users/stone/.local/bin/python3.11 -m pytest tests/test_agent.py tests/test_mall_client.py -v`; expect import/collection failure. / 运行核心和客户端测试，确认因实现缺失而失败。**
- [ ] **Step 5 / 步骤 5：Implement the provider protocol, deterministic echo provider, prompt-building Agent, typed product data, and `MallClient(base_url: str, transport: httpx.BaseTransport | None = None, timeout: float = 5.0)`. Normalize with `base_url.rstrip('/')` and validate every product field. / 实现提供方协议、回显提供方、构造提示词的 Agent、类型化商品数据和商城客户端；规范化地址并验证每个商品字段。**
- [ ] **Step 6 / 步骤 6：Run `cd agent-service && /Users/stone/.local/bin/python3.11 -m pytest tests/test_agent.py tests/test_mall_client.py -v`; expect all tests to pass. / 运行核心和客户端测试，确认全部通过。**
- [ ] **Step 7 / 步骤 7：Commit `agent-service` core as `feat: add agent mall client`. / 提交 Agent 核心和商城客户端。**

### Task 3: Agent HTTP API / 任务 3：Agent HTTP API

**Files / 文件：**

- Create / 创建：`agent-service/src/agent_service/app.py`
- Create / 创建：`agent-service/tests/test_app.py`

**Interfaces / 接口：**

- Consumes / 输入依赖：Task 2 `Agent`, `EchoProvider`, `MallClient`, `MallServiceUnavailable`
- Produces / 对外提供：`create_app(mall_client: MallClient | None = None, provider: ModelProvider | None = None) -> FastAPI`, module-level `app`, `GET /health`, `POST /chat`

- [ ] **Step 1 / 步骤 1：Write API tests for health, valid chat delegation, trimmed message, and the exact 502 body `{"error":{"code":"mall_service_unavailable","message":"Mall service is unavailable"}}`. / 编写健康检查、有效聊天委托、消息去空白和精确 502 响应测试。**
- [ ] **Step 2 / 步骤 2：Write a whitespace-only test asserting status 422 and zero calls to a recording mall client. / 编写纯空白消息测试，断言返回 422 且商城客户端调用次数为零。**
- [ ] **Step 3 / 步骤 3：Run `cd agent-service && /Users/stone/.local/bin/python3.11 -m pytest tests/test_app.py -v`; expect import failure for `agent_service.app`. / 运行 API 测试，确认因应用模块缺失而失败。**
- [ ] **Step 4 / 步骤 4：Implement Pydantic `ChatRequest`/`ChatResponse`, whitespace validation, dependency-injectable `create_app`, environment default `MALL_SERVICE_URL=http://localhost:8001`, and the stable 502 exception handler. / 实现聊天模型、空白验证、可注入应用工厂、商城地址默认值和稳定 502 处理。**
- [ ] **Step 5 / 步骤 5：Run `cd agent-service && /Users/stone/.local/bin/python3.11 -m pytest -v`; expect the full agent-service suite to pass. / 运行 Agent 服务完整测试，确认全部通过。**
- [ ] **Step 6 / 步骤 6：Commit the API as `feat: expose agent chat service`. / 提交 Agent 聊天 API。**

### Task 4: Next.js BFF and proxy routes / 任务 4：Next.js BFF 与代理路由

**Files / 文件：**

- Create / 创建：`web/package.json`
- Create / 创建：`web/package-lock.json`
- Create / 创建：`web/tsconfig.json`
- Create / 创建：`web/next-env.d.ts`
- Create / 创建：`web/next.config.ts`
- Create / 创建：`web/vitest.config.ts`
- Create / 创建：`web/test/setup.ts`
- Create / 创建：`web/lib/proxy.ts`
- Create / 创建：`web/app/api/products/route.ts`
- Create / 创建：`web/app/api/products/route.test.ts`
- Create / 创建：`web/app/api/chat/route.ts`
- Create / 创建：`web/app/api/chat/route.test.ts`

**Interfaces / 接口：**

- Consumes / 输入依赖：Task 1 `GET /products`; Task 3 `POST /chat`
- Produces / 对外提供：browser-facing `GET /api/products`, `POST /api/chat`, `proxyJson(request: Request, upstreamUrl: string) -> Promise<Response>`

- [ ] **Step 1 / 步骤 1：Create a minimal Next.js 16.4.5/React 19.3.1 TypeScript package with `dev`, `build`, and `test` scripts; add Vitest 4.x, jsdom, and Testing Library dev dependencies, then run `cd web && npm install` to generate the lockfile. / 创建最小 Next.js/React TypeScript 包和测试配置，并安装依赖生成锁文件。**
- [ ] **Step 2 / 步骤 2：Write route tests that mock global `fetch` and assert default URLs, trailing-slash environment URLs, forwarded method/body/content type, successful JSON, preserved upstream JSON 422, network failure 502, and non-JSON upstream 502. / 编写代理测试，覆盖默认地址、尾部斜杠、方法和请求体转发、成功 JSON、保留上游 422、网络失败及非 JSON 502。**
- [ ] **Step 3 / 步骤 3：Run `cd web && npm test -- app/api/products/route.test.ts app/api/chat/route.test.ts`; expect module-not-found failures for the route handlers. / 运行代理测试，确认因路由处理器缺失而失败。**
- [ ] **Step 4 / 步骤 4：Implement `proxyJson` and both route handlers using server-only `MALL_SERVICE_URL` and `AGENT_SERVICE_URL`, each normalized with `replace(/\/$/, '')`; use the exact stable `upstream_unavailable` 502 body from the spec. / 实现通用 JSON 代理和两个 Route Handler，使用仅服务端环境变量、规范化尾部斜杠，并返回设计规定的稳定 502 结构。**
- [ ] **Step 5 / 步骤 5：Run `cd web && npm test -- app/api/products/route.test.ts app/api/chat/route.test.ts`; expect all proxy tests to pass. / 运行代理测试，确认全部通过。**
- [ ] **Step 6 / 步骤 6：Commit the BFF as `feat: add web service proxies`. / 提交 Web BFF 代理。**

### Task 5: Storefront page and chat UI / 任务 5：商城页面与聊天界面

**Files / 文件：**

- Create / 创建：`web/app/layout.tsx`
- Create / 创建：`web/app/page.tsx`
- Create / 创建：`web/app/page.module.css`
- Create / 创建：`web/app/globals.css`
- Create / 创建：`web/app/page.test.tsx`
- Create / 创建：`web/lib/types.ts`

**Interfaces / 接口：**

- Consumes / 输入依赖：Task 4 `/api/products` and `/api/chat`
- Produces / 对外提供：single bilingual storefront page with product loading/success/empty/error states and chat idle/submitting/reply/error states

- [ ] **Step 1 / 步骤 1：Write page tests for bilingual headings, loading then rendered products, empty catalog, product error, disabled whitespace-only chat, successful reply, and failed chat retaining the typed message. / 编写页面测试，覆盖双语标题、商品加载与成功、空目录、商品错误、禁用空白聊天、成功回复及聊天失败后保留输入。**
- [ ] **Step 2 / 步骤 2：Run `cd web && npm test -- app/page.test.tsx`; expect module-not-found failure for the page. / 运行页面测试，确认因页面缺失而失败。**
- [ ] **Step 3 / 步骤 3：Implement typed product/error shapes, a client page that calls only relative `/api/*` routes, accessible form labels/status regions, and lightweight responsive CSS. / 实现类型化响应、只调用相对 `/api/*` 的客户端页面、可访问表单和轻量响应式样式。**
- [ ] **Step 4 / 步骤 4：Run `cd web && npm test`; expect all web tests to pass. / 运行 Web 完整测试，确认全部通过。**
- [ ] **Step 5 / 步骤 5：Run `cd web && npm run build`; expect a successful production build with `/`, `/api/products`, and `/api/chat`. / 运行生产构建，确认主页和两个 API 路由均成功生成。**
- [ ] **Step 6 / 步骤 6：Commit the UI as `feat: add storefront and chat page`. / 提交商城和聊天页面。**

### Task 6: Root migration, documentation, and cross-service verification / 任务 6：根目录迁移、文档与跨服务验证

**Files / 文件：**

- Delete / 删除：`pyproject.toml`
- Delete / 删除：`src/niub_agent/__init__.py`
- Delete / 删除：`src/niub_agent/__main__.py`
- Delete / 删除：`src/niub_agent/agent.py`
- Delete / 删除：`src/niub_agent/providers.py`
- Delete / 删除：`tests/test_agent.py`
- Delete / 删除：`tests/test_cli.py`
- Modify / 修改：`.gitignore`
- Rewrite / 重写：`README.md`
- Create / 创建：`scripts/smoke_test.py`
- Create / 创建：`tests/test_smoke.py`

**Interfaces / 接口：**

- Consumes / 输入依赖：all service commands and HTTP contracts from Tasks 1–5 / 任务 1–5 的所有服务命令和 HTTP 契约
- Produces / 对外提供：clean three-module root, bilingual operator documentation, automated three-boundary smoke test

- [ ] **Step 1 / 步骤 1：Write `tests/test_smoke.py` to launch mall and agent Uvicorn subprocesses on free test ports, launch `next start` after the verified build, poll health endpoints with timeouts, and assert browser-facing product and chat requests traverse all boundaries; always terminate child processes in cleanup. / 编写冒烟测试，在空闲端口启动两个 Uvicorn 和构建后的 Next 服务，轮询健康状态并断言商品与聊天请求穿过全部边界，最后可靠清理子进程。**
- [ ] **Step 2 / 步骤 2：Run `/Users/stone/.local/bin/python3.11 -m unittest discover -s tests -p 'test_smoke.py' -v`; expect failure because `scripts.smoke_test` and the final root layout are absent. / 运行冒烟测试，确认因脚本和最终根目录结构缺失而失败。**
- [ ] **Step 3 / 步骤 3：Implement `scripts/smoke_test.py` with bounded startup/request timeouts and process cleanup; make the test invoke its public `run_smoke_test() -> None`. / 实现带有有界超时和进程清理的冒烟脚本，并让测试调用公开入口。**
- [ ] **Step 4 / 步骤 4：Remove the obsolete root Python package/config/tests, extend `.gitignore` for `.venv`, Python caches, Next.js build output, and `node_modules`, and rewrite `README.md` with the architecture diagram, prerequisites, independent installs, three-terminal startup order, environment variables, tests, and sample curl requests in complete Chinese and English. / 删除旧根 Python 包和测试，扩展忽略规则，并完整重写中英双语 README。**
- [ ] **Step 5 / 步骤 5：Run `/Users/stone/.local/bin/python3.11 -m unittest discover -s tests -p 'test_smoke.py' -v`; expect the three-boundary smoke test to pass. / 运行三边界冒烟测试，确认通过。**
- [ ] **Step 6 / 步骤 6：Run `cd mall-service && /Users/stone/.local/bin/python3.11 -m pytest -v`, `cd agent-service && /Users/stone/.local/bin/python3.11 -m pytest -v`, `cd web && npm test`, `cd web && npm run build`, and root `git diff --check`; expect every command to pass. / 分别运行两个 Python 服务测试、Web 测试、Web 构建和根目录格式检查，确认全部通过。**
- [ ] **Step 7 / 步骤 7：Commit the migration and docs as `chore: complete three-module monorepo`. / 提交根目录迁移和文档。**

