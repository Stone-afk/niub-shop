# 上下文商品推荐增强设计

## 1. 目标 / Goal

将当前商品推荐流程升级为多来源、可降级的 Agent 推荐流程：

- Agent 同时使用 `agent-service` 内部维护的用户画像和 mock 推荐平台画像。
- 从 `mall-service` 获取浏览、订单、购物车、商品推荐和广告推荐数据。
- 任意记录为空时仍可继续执行；空数据代表新用户或暂无记录，不代表流程失败。
- 最终由大模型综合可用信息，最多返回 3 个商品。
- 最多 1 个广告商品，广告商品必须标记为 `sponsored` 并在 Web 展示“广告 / Sponsored”。
- 没有足够可信候选时宁缺毋滥，返回空商品列表和双语提示，不生成虚假商品。
- 复杂且暂不接入的能力记录到根目录 `TODO.md`。

固定演示用户仍为 `user_id = 1`。

## 2. 方案选择 / Selected Approach

采用“Agent 多工具编排”方案，而不是由商城服务聚合全部数据。

```text
get_agent_profile
        │
get_external_profile
        │
get_user_context
        │
get_product_recommendations
        │
get_ad_recommendations
        │
search_products（必要时）
        │
        ▼
模型综合判断 / Model synthesis
        │
        ▼
submit_product_recommendation
```

这样可以保持每个数据源边界清晰，让模型决定后续是否需要进一步搜索，也方便未来替换真实画像、推荐和广告服务。

## 3. 数据源设计 / Data Sources

### 3.1 Agent 内部画像

`agent-service` 提供 `AgentProfileProvider`，第一版使用服务内部 mock 数据。画像至少包含：

```json
{
  "user_id": 1,
  "source": "agent",
  "available": true,
  "profile": {
    "preferred_categories": ["drinkware"],
    "preferred_tags": ["coffee", "tea"],
    "price_range": {"min_cents": 20000, "max_cents": 80000},
    "style_preferences": ["simple", "practical"]
  }
}
```

该接口边界与后续真实画像数据库或画像服务保持一致，但本次不接入外部持久化画像库。

### 3.2 外部推荐平台画像

`mall-service` 增加 mock 接口：

```text
GET /users/{user_id}/external-profile
```

示例返回：

```json
{
  "user_id": 1,
  "source": "recommendation-platform",
  "available": true,
  "profile": {
    "interest_categories": ["drinkware", "home"],
    "interest_tags": ["coffee"],
    "price_sensitivity": "medium",
    "preferred_availability": "in_stock"
  }
}
```

### 3.3 用户行为记录

`mall-service` 的用户上下文 mock 使用结构化记录：

```json
{
  "user_id": 1,
  "available": true,
  "is_new_user": false,
  "browsing": [
    {
      "product_id": "ceramic-mug",
      "viewed_at": "2026-11-06T10:00:00Z",
      "count": 3
    }
  ],
  "orders": [
    {
      "product_id": "canvas-tote",
      "ordered_at": "2026-10-20T10:00:00Z",
      "quantity": 1,
      "status": "completed"
    }
  ],
  "cart": [
    {
      "product_id": "ceramic-mug",
      "added_at": "2026-11-06T09:00:00Z",
      "quantity": 1
    }
  ]
}
```

新用户使用空数组表示暂无记录：

```json
{
  "browsing": [],
  "orders": [],
  "cart": [],
  "available": true,
  "is_new_user": true
}
```

## 4. Agent 工具与 ReAct 流程 / Tools and ReAct Flow

新增或调整以下工具：

```text
get_agent_profile
get_external_profile
get_user_context
get_product_recommendations
get_ad_recommendations
search_products
submit_product_recommendation
```

推荐提示词要求模型优先加载画像和用户上下文，再获取自然推荐和广告推荐；当候选不足或用户提出明确条件时，模型可以调用 `search_products`。

每个来源结果均带有 `available` 状态。数据为空时返回空数组和说明；单个来源失败时返回来源不可用状态，由模型继续综合其它来源。

## 5. 候选商品约束 / Candidate Constraints

服务端在 `submit_product_recommendation` 和持久化层双重校验：

```text
最多 3 个商品
最多 1 个 sponsored 商品
商品 ID 必须来自本轮工具结果
商品 ID 不允许重复
商品来源只能是 organic 或 sponsored
```

推荐优先级：

1. 优先使用画像和行为记录匹配的自然商品。
2. 广告商品只作为补充，最多一个。
3. 商品不足 3 个时不强行补足。
4. 无可信候选时返回空列表，不编造商品。

空结果示例：

```json
{
  "answer": "暂时没有找到足够匹配的商品。你可以尝试在其他平台搜索，或告诉我更具体的预算、类型和用途。 / No suitable products found. You may try other platforms or provide a budget, type, or use case.",
  "product_ids": [],
  "product_sources": []
}
```

## 6. 缺失数据与错误处理 / Degradation

- 画像为空：继续执行。
- 浏览记录为空：继续执行。
- 订单为空：继续执行。
- 购物车为空：继续执行。
- 商品推荐为空：继续执行。
- 广告推荐为空：继续执行。
- 单个来源不可用：标记该来源不可用，继续使用其它来源。
- 所有来源都没有可信候选：返回双语无结果说明和空商品列表。
- 不允许因为缺少某一类记录而直接让整轮推荐失败。

## 7. 持久化与 Web / Persistence and Web

继续使用：

```text
turn_recommendations
turn_recommendation_sources
```

最终商品上限从当前版本的 5 调整为 3，来源仍持久化为 `organic` 或 `sponsored`。

Web 商品卡片继续展示：

- 名称 / Name
- 价格 / Price
- 描述 / Description
- 库存状态 / Availability
- 广告标识 / Sponsored badge

无推荐商品时展示双语空状态，不渲染虚假卡片。

本次不持久化完整画像、浏览明细、订单明细和工具中间结果，只持久化最终推荐结果。

## 8. TODO 范围 / Deferred Work

本次实现会新增根目录 `TODO.md`，记录暂不接入的复杂能力：

- 真实用户画像服务、画像版本、冲突合并和授权控制。
- 浏览、点击、收藏、加购、购买的实时采集和时间衰减。
- 订单取消、退款、售后状态。
- 真实推荐平台、候选多样性、互补性和反馈闭环。
- 实时库存、价格、促销、优惠券和物流限制。
- 广告竞价、频控、合规审查和归因。
- 多用户登录、数据隔离、工具观测、缓存、熔断和脱敏。

## 9. 实施步骤 / Implementation Steps

1. 增加 Agent 内部画像 provider 和外部画像 mock 接口。
2. 调整用户行为记录 mock 结构，支持新用户空数据。
3. 扩展 MallClient 和 Agent 工具注册。
4. 更新推荐策略提示词，要求综合双画像、行为和候选来源。
5. 将最终候选限制调整为最多 3 个、最多 1 个广告。
6. 调整 SQLite、SSE、Web 类型和卡片空状态。
7. 增加成功、空数据、部分失败、全空结果和约束校验测试。
8. 运行 Python 测试、TypeScript 检查，并检查 `git diff --check`。

## 10. 验收标准 / Acceptance Criteria

1. Agent 能同时读取内部画像和外部画像。
2. 外部画像通过 `mall-service` mock 接口提供。
3. 新用户空记录不会导致流程失败。
4. 单个来源失败时，其它来源仍可继续。
5. 最终最多返回 3 个商品。
6. 最多 1 个商品来源为 `sponsored`。
7. Web 明确展示广告标识。
8. 无合适商品时返回空列表和双语提示。
9. 商品 ID 和来源继续持久化。
10. 复杂未接入能力写入 `TODO.md`。
11. 继续使用固定演示用户 `user_id = 1`。
