# Agent Plan / Plan State 设计

## 1. 目标 / Goal

将当前以代码局部状态和固定工具顺序为主的 Agent 循环，升级为由大模型维护显式 Plan 和 Plan State 的执行循环。

目标是：

- 模型负责创建计划、更新状态、决定当前步骤和下一步工具。
- 代码只负责校验结构、执行模型选择的工具、回传工具结果、持久化状态和运行安全限制。
- 设计可以扩展到更多画像、推荐、订单、营销和跨系统步骤。
- 现有商品推荐约束继续生效：最多 3 个商品，最多 1 个广告商品。

固定演示用户继续使用 `user_id = 1`。

## 2. 核心原则 / Principles

### 2.1 模型决定业务流程

代码不根据本地业务状态自动决定：

- 当前应该调用哪个工具。
- 是否查询画像、行为、商城或广告系统。
- 是否重试、跳过或重新规划。
- 是否结束当前计划。

模型通过 `update_plan` 工具提交这些决策。

### 2.2 代码保留运行安全边界

代码仍然执行以下非业务决策边界：

- Plan State 的结构和字段类型校验。
- 工具参数校验和工具执行超时。
- Agents SDK 的 `max_turns`。
- 商品 ID 必须来自可信工具结果。
- 最多 3 个商品、最多 1 个 `sponsored` 商品。
- 无效 JSON、模型协议错误和资源异常的安全映射。

这些边界用于防止无限循环、数据伪造和资源失控，不替模型选择业务路径。

## 3. Plan 数据模型 / Plan Model

Plan 由模型通过 `update_plan` 创建和更新：

```json
{
  "plan_id": "plan-001",
  "goal": "为用户推荐适合办公室使用的咖啡杯",
  "status": "in_progress",
  "current_step_id": "step-2",
  "steps": [
    {
      "id": "step-1",
      "title": "加载用户画像",
      "status": "completed",
      "depends_on": []
    },
    {
      "id": "step-2",
      "title": "查询用户行为记录",
      "status": "in_progress",
      "depends_on": ["step-1"]
    },
    {
      "id": "step-3",
      "title": "查询商城和广告候选",
      "status": "pending",
      "depends_on": ["step-2"]
    },
    {
      "id": "step-4",
      "title": "综合筛选并输出推荐",
      "status": "pending",
      "depends_on": ["step-3"]
    }
  ],
  "next_action": "get_user_context",
  "reason": "需要先确认用户近期浏览和购买行为"
}
```

Plan 状态：

```text
draft | in_progress | completed | blocked | cancelled
```

Step 状态：

```text
pending | in_progress | completed | blocked | skipped
```

### 3.1 结构约束

- `plan_id` 非空，单次 Agent 运行内保持稳定。
- `goal` 非空且有长度上限。
- `steps` 至少一个步骤，步骤 ID 在同一 Plan 内唯一。
- `current_step_id` 必须为空或存在于 `steps`。
- `next_action` 是模型计划的下一步标识，不由代码解释或映射到工具。
- `reason` 用于说明模型当前判断，代码只保存不评估语义。
- `status=completed` 或 `status=blocked` 时允许没有 `current_step_id`。

## 4. Agent 工具和执行流 / Tools and Flow

新增专用工具：

```text
update_plan
```

现有工具继续保留：

```text
get_agent_profile
get_external_profile
get_user_context
get_product_recommendations
get_ad_recommendations
search_products
submit_product_recommendation
```

执行流：

```text
模型创建或更新 Plan
    ↓
模型选择当前步骤和下一步工具
    ↓
代码执行模型选择的工具
    ↓
工具结果回传模型
    ↓
模型再次更新 Plan State
    ↓
模型决定继续、重试、跳过、重规划或结束
    ↓
模型提交最终结果
```

工具执行完成后，代码不会自动把某个步骤设置为 `completed`，也不会自动选择下一个工具。模型必须通过下一次 `update_plan` 表达状态变化。

## 5. 运行上下文 / Runtime Context

`RecommendationRunContext` 增加：

- `plan_state`：当前已校验的 Plan State。
- `plan_history`：本次运行中的 Plan 更新序列，用于审计和测试。

`update_plan` 工具：

1. 严格解析模型提交的 Plan State。
2. 校验结构、步骤 ID 唯一性和状态枚举。
3. 保存最新状态和历史快照。
4. 将规范化后的 Plan State JSON 返回给模型。

代码不根据 `next_action` 直接调用工具，也不根据 `current_step_id` 阻止模型调用其它工具。

## 6. 提示词策略 / Prompt Policy

推荐策略提示词要求模型：

1. 在开始复杂任务时先创建 Plan。
2. 每次执行下一步前更新 Plan State。
3. 工具执行后根据结果更新 Plan State。
4. 可以修改、跳过或扩展步骤。
5. 工具失败时自行决定重试、换工具、重规划或结束。
6. 最终提交前将 Plan 标记为 `completed` 或 `blocked`。
7. 不得伪造工具结果、商品事实或商品 ID。

对于简单闲聊，模型可以不创建商品推荐 Plan，直接返回自然语言答案；一旦需要调用商品工具，则应先建立或更新 Plan。

## 7. 持久化 / Persistence

新增表：

```text
turn_plans
```

字段：

```text
id
turn_id
goal
status
current_step_id
state_json
version
created_at
updated_at
```

设计选择：

- 每个 turn 绑定一个 Plan。
- `state_json` 保存完整扩展状态，方便未来增加字段而不频繁迁移表结构。
- `version` 记录 Plan 更新次数并支持后续并发保护。
- 可选的历史快照暂时保存在运行内存中，不单独建立事件表。
- 最终推荐结果和最终 Plan State 在同一轮生命周期内完成保存。

第一版不要求 Web 展示 Plan，但会在会话详情接口提供最新 Plan State 的可选字段，便于未来开发调试面板和执行进度视图。

## 8. 失败和重规划 / Failure and Replanning

工具失败时返回结构化结果，而不是替模型选择下一步：

```json
{
  "available": false,
  "error": "source_unavailable",
  "retryable": true
}
```

模型可以选择：

- 继续下一步。
- 重试当前步骤。
- 调整 Plan。
- 跳过当前步骤。
- 将 Plan 标记为 `blocked`。
- 直接输出空结果。

没有可信商品时，模型应提交：

```json
{
  "answer": "暂时没有找到合适的商品。你可以尝试其它平台。 / No suitable products found. You may try other platforms.",
  "product_ids": [],
  "product_sources": []
}
```

## 9. SSE 和 Web 范围 / SSE and Web Scope

第一版继续发送现有 `delta`、`recommendations` 和 `done` 事件，不强制 Web 展示 Plan。

会话详情接口可增加：

```json
{
  "plan_state": { "status": "completed", "steps": [] }
}
```

未来如果需要实时进度，再增加可选的：

```text
event: plan
```

本次不修改 Web 布局，不把内部 Plan 细节直接展示给终端用户。

## 10. 测试范围 / Testing

- 模型创建 Plan 后调用工具。
- 模型根据工具结果更新 `current_step_id`。
- 模型跳过步骤或重新规划。
- 工具失败后模型选择重试或进入 `blocked`。
- Plan State 结构非法时被拒绝且不会污染持久化数据。
- 多次 Plan 更新按版本顺序保存。
- Plan State 与 turn 正确绑定。
- 超过 `max_turns` 时安全结束。
- 工具超时后模型仍能收到结构化错误。
- 现有商品 ID、商品数量和广告数量约束继续生效。

## 11. 实施阶段 / Implementation Phases

1. 增加 Plan / Plan State Pydantic 模型和 `update_plan` 工具。
2. 将 Plan State 放入 Agents SDK 运行上下文和提示词协议。
3. 移除基于本地业务状态的工具选择干预，只保留结构和安全校验。
4. 增加 SQLite `turn_plans` 持久化与会话详情返回。
5. 增加失败、重规划、超时和版本测试。
6. 更新 README 和开发文档，保留 Web UI 为后续扩展点。
