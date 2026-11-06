# 订单创建与结算预览 / Order Creation and Checkout Preview

- Baseline: `order-flow-v1`
- Date: 2026-11-06
- Status: `APPROVED — implementation plan active`
- Related: [plan.md](./plan.md), [tasks.md](./tasks.md)

## 1. Intent / 目标

当用户在多轮对话中明确表示要购买推荐商品时，由 Agent 通过工具创建商城订单；订单先进入 `pending_payment`，Web 自动引导用户进入订单预览页，用户再手动完成首版 Mock 支付。

When a user clearly confirms a purchase in a multi-turn chat, the Agent creates a mall order through a tool. The order remains `pending_payment`; Web navigates to an order preview page where the user manually completes a first-version mock payment.

## 2. Goals / 范围

- 在 Agent SDK 工具集中增加 `create_order_draft`，只接受明确的商品 ID 与数量。
- mall-service 提供创建订单、查询订单预览、Mock 支付接口。
- 订单使用 Python 标准库 SQLite 保存，订单保存商品快照、单价、数量、金额、状态、幂等键。
- agent-service 通过 SSE 增加 `order` 事件，并持久化订单关联。
- Web 新增 `/orders/[orderId]/preview`，展示订单摘要并支持 Mock 支付。
- 固定演示用户仍为服务端 `user_id=1`，客户端不传用户 ID。

## 3. Non-goals / 非目标

- 不接入真实支付、支付网关、退款、优惠券、税费、物流、地址或库存预占。
- 不引入购物车 UI；本版从 Agent 对话上下文直接下单。
- 不允许模型直接写数据库或直接决定已支付状态。
- 不修改既有推荐上限（最多 3 个，广告最多 1 个）。

## 4. Invariants / 不变量

1. 只有明确购买意图时才允许调用创建订单工具；“看看/考虑一下/推荐一个”不得创建订单。意图不明确时 Agent 先追问确认。
2. 创建订单的商品必须来自商城当前目录且为 `in_stock`；数量为正整数并受首版上限约束（每个商品 1–99）。
3. 创建结果永远是 `pending_payment`，不得由 Agent 或 Web 直接标记为已支付。
4. Agent 为规范化后的商品集合生成并持久化 `order_intents`；同一 `turn_id + intent_hash` 复用同一个 `client_request_id`，不同商品集合生成不同幂等键。
5. 订单价格使用创建时的商品快照，预览页不得重新计算为不同价格。
6. 固定演示用户由 agent-service/mall-service 服务端控制，客户端不能越权指定用户。

## 5. User flow / 用户流程

```text
推荐商品 → 用户明确确认购买
  → Agent 判断是否需要澄清
  → create_order_draft(product_ids, quantities, client_request_id)
  → mall-service 创建 pending_payment 订单
  → agent-service 持久化 order_id 并发送 SSE order 事件
  → Web 在流结束后跳转 /orders/{order_id}/preview
  → 用户查看快照并点击 Mock 支付
  → mall-service 将状态改为 paid，Web 刷新预览
```

## 6. Acceptance criteria / 验收标准

- AC-01：对话“我想买第一个”且上下文只有一个已推荐商品时，Agent 创建一个待支付订单并返回订单链接。
- AC-02：对话“给我看看杯子”或“我考虑一下”不得创建订单，仍返回澄清/推荐回复。
- AC-03：SSE 至少按 `delta → recommendations(可选) → order(可选) → done` 发送；订单事件包含 `order_id`、`preview_path`、`status`。
- AC-04：刷新或重试同一购买意图不会因相同幂等键创建重复订单；同一轮的不同购买意图可以创建不同订单。
- AC-05：预览页显示订单号、商品名称快照、数量、单价、行金额、总额、币种、状态和 Mock 支付入口。
- AC-06：支付成功后状态变为 `paid`；重复支付请求保持幂等，不重复扣款。
- AC-07：缺货、未知商品、商城不可用、订单持久化失败均给出双语可重试错误，且不发送伪造的成功事件。
- AC-08：历史对话中可看到已创建订单的预览链接；现有推荐商品卡片和历史会话行为不回归。

## 7. Verification / 验证

详细检查清单见 [plan.md](./plan.md) 的 `final_acceptance_checks`。本规格获批前不得开始生产代码或测试代码修改。
