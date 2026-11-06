# 订单创建与结算预览实施方案 / Implementation Plan

- Baseline: `order-flow-v1`
- Date: 2026-11-06
- Status: `APPROVED — implementation plan active`
- Related: [spec.md](./spec.md), [tasks.md](./tasks.md)

## 1. Current evidence / 当前事实

- mall-service 当前是 FastAPI 内存商品目录，已有 `/products`、用户上下文、外部画像和推荐 Mock API，但无订单领域。
- agent-service 当前通过 OpenAI Agents SDK 工具获取上下文、画像、商品推荐，并在 SQLite 中持久化会话、轮次、推荐商品和 Plan State。
- Web 当前只有 `/chat`；SSE 已解析 `delta`、`recommendations`、`done`，尚无订单页。
- fixed demo user is server-side `user_id=1`.

以上是代码检查得到的事实；以下为本方案建议。

## 2. Approaches / 方案比较

### A（选定）：Agent tool creates a pending order

Agent 通过 `create_order_draft` 调用商城。优点是购买意图、商品选择、上下文和未来 Plan State 保持在 Agent 闭环中；缺点是需要显式确认规则、幂等键和跨服务错误处理。

### B：Web detects intent and calls mall directly

实现较快，但把自然语言意图判断放进前端，绕过 Agent 上下文，扩展复杂流程时会产生重复业务逻辑；不采用。

### C：Agent returns a confirmation card, UI button creates order

误下单风险最低，但每次都增加一次 UI 点击，不符合本版“明确确认后直接创建”的目标；作为后续高风险支付场景的演进方向。

本版采用 A，并吸收 C 的安全约束：意图不明确必须先追问；订单工具只创建待支付订单；真实支付前保留显式 Mock 支付按钮。

## 3. Architecture / 架构

### mall-service owns order domain

新增 Pydantic models：`OrderItemSnapshot`、`Order`、`CreateOrderRequest`、`PaymentResult`。首版使用 Python 标准库 `sqlite3` 保存订单（不新增第三方依赖），默认数据库为 `data/mall.db`，保证预览请求、支付请求和服务重启后仍能读取同一订单。推荐接口：

- `POST /orders`：服务端固定 user 1，校验商品和库存，按 `client_request_id` 幂等创建 `pending_payment`。
- `GET /orders/{order_id}/preview`：返回订单快照和当前状态。
- `POST /orders/{order_id}/pay`：Mock 支付；仅允许 `pending_payment`，成功转 `paid`，重复调用返回当前已支付结果。

请求不接受 `user_id`。金额以整数分保存，合计由商城根据快照计算。

### agent-service owns Agent orchestration and link persistence

- `MallClient` 增加创建订单、查询订单和支付结果的严格响应模型。
- 新增 Agents SDK 工具 `create_order_draft`：输入 `items[{product_id, quantity}]` 和 `client_request_id`；工具返回结构化订单摘要。
- 系统提示词增加购买意图、追问和禁止提前下单规则。
- `RecommendationResult`/stream result 增加可选 `order` 元数据。
- SQLite 持久化 `turn_orders` 关联（`turn_id`, `order_id`, `status`, `preview_path`, `position`）；Agent 在调用商城前对规范化商品集合计算 `intent_hash` 并生成确定性的 `client_request_id`，商城 SQLite 以 `(user_id, client_request_id)` 唯一约束承担跨重试幂等。同一轮可拥有多个不同购买意图。
- SSE 增加 `order` 事件，事件顺序固定在最终文本/推荐之后、`done` 之前；若没有订单则不发送。

### Web owns presentation and navigation

- SSE parser 增加可选 `order` 事件解析，严格校验 `order_id`、`preview_path`、`status`。
- `/chat` 收到订单事件后在流完成时调用 Next Router 跳转 `/orders/{orderId}/preview`；在历史轮次中显示“查看订单 / View order”链接。
- 新增 `/orders/[orderId]/preview` 页面，调用 Web `/api/orders/[orderId]` BFF 代理查询商城；显示快照、金额、状态和 Mock 支付按钮。
- 新增 Web `/api/orders/[orderId]`（GET）与 `/api/orders/[orderId]/pay`（POST）代理，避免浏览器直接依赖商城地址。

## 4. Failure and idempotency / 失败与幂等

- agent-service 规范化 `items[{product_id, quantity}]` 后计算 `intent_hash`，并在 `order_intents` 中为 `turn_id + intent_hash` 原子生成/复用 UUID `client_request_id`。同一购买意图重试复用同一键，不同意图在同一轮生成不同键。
- 商城不可用：Agent 轮次失败并返回双语可重试错误，不发 `order` 成功事件。
- 商品下架/缺货/数量非法：工具返回可解释错误，模型可澄清或推荐替代品，不创建部分订单。
- Agent 生成成功但 SQLite 写关联失败：不伪造 SSE；记录可恢复错误，依靠幂等键允许重试。
- SSE 断线发生在商城已创建后：重试使用同一购买意图的幂等键，返回原订单；同一轮的其他购买意图仍可创建独立订单。

## 5. Plan decomposition / 子 Plan

### P1 — Mall order mock contract

订单模型、校验、幂等创建、预览和 Mock 支付 API；不接 Agent/Web。

### P2 — Agent order tool and persistence

Agents SDK 工具、提示词规则、MallClient、turn-order SQLite 关联和 SSE `order` 事件。

### P3 — Web preview and navigation

SSE parser、chat 跳转、order BFF、订单预览页和 Mock 支付交互。

### P4 — Integrated acceptance

启动三个服务，执行自然语言多轮购买、重复重试、缺货和支付状态流程；此阶段才执行业务级 E2E。

依赖顺序：`P1 → P2 → P3 → P4`。

## 6. Risk, rollback, observability / 风险、回滚、可观测性

- 风险：模型误判购买意图；缓解：提示词规则 + 工具输入必须含明确商品/数量 + 工具只创建 pending。
- 风险：重复下单；缓解：服务端幂等键和重试测试。
- 风险：价格变化；缓解：创建时保存价格快照。
- 风险：跨服务部分成功；缓解：幂等恢复、错误码、结构化日志；首版不宣称分布式事务。
- 回滚：保留既有推荐工具和 SSE 事件；移除订单工具注册后旧聊天路径仍可用；数据库新表可留存但不参与旧流程。

## 7. Final acceptance checks / 总体验收去重清单

- `FA-01` mall order contract: create/preview/pay and idempotent retry.
- `FA-02` agent multi-turn: recommendation → explicit purchase → order event.
- `FA-03` ambiguous intent does not create order.
- `FA-04` SSE reconnect/retry does not duplicate order.
- `FA-05` Web navigates to preview and renders snapshot.
- `FA-06` mock payment transitions `pending_payment → paid` idempotently.
- `FA-07` failure paths return bilingual errors and preserve existing chat/recommendation behavior.

## 8. Budget / 预算

Planning/design/documentation 40%; coding 5%; verification/audit/acceptance 55% at requirement and Plan levels. Expected production delta: roughly 6–10 files across three services; tests: focused mall, agent, and Web contract tests plus one final local E2E script. If the delta exceeds the repository review warning (~400 production lines or ~1,200 test lines), pause for scope review.
