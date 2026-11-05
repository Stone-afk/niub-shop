# 商品推荐工具与对话商品卡片设计 / Product Recommendation Tools and Chat Product Cards Design

## 意图 / Intent

让持久化聊天 Agent 按需调用结构化商品查询工具，再基于真实商城结果生成推荐。最终回答同时包含自然语言和经过校验的商品 ID；成功轮次持久化这些 ID，Web 在实时和历史消息中渲染只读商品卡片。

Enable the persisted chat Agent to call a structured product-search tool on demand and recommend only from real catalog results. A final answer contains both natural language and validated product IDs. Successful turns persist those IDs, and Web renders read-only product cards for live and historical replies.

## 已确认目标 / Confirmed Goals

- 模型决定何时调用商品工具，链路为“模型选工具 → Agent 执行 → 工具结果回传模型 → 模型提交最终推荐”。
- `search_products` 使用结构化条件：关键词、分类、最低价、最高价、排序和数量上限。
- 最终推荐包含文本与商品 ID；商品 ID 必须来自本轮工具结果。
- completed turn 持久化推荐商品 ID，刷新和重新选择历史会话后仍可恢复商品卡片。
- Web 在对应 Agent 回复下展示名称、价格、描述和库存状态；第一版没有详情页跳转或加入购物车操作。
- Mall 侧保持确定性的 mock 能力；实现重点是 Agent 编排、协议、持久化和 Web 展示。
- UI、公开错误和运维文档保持中英双语。

- The model chooses when to search products, using a model → tool → result → final-recommendation loop.
- `search_products` accepts structured keyword, category, price range, sort, and limit filters.
- Final recommendations contain prose plus product IDs validated against this turn's tool results.
- Completed turns persist IDs so cards survive refresh and history selection.
- Web shows name, price, description, and availability below the matching Agent reply, with no actions in version one.
- Mall remains deterministic and mock-backed; the engineering focus is Agent orchestration, protocols, persistence, and presentation.
- UI, public errors, and operational documentation remain bilingual.

## 非目标与反目标 / Non-goals and Anti-goals

第一版不包含购物车、详情页、个性化画像、协同过滤、向量检索、库存预留、跨币种换算、埋点、推荐解释评分或后台工具管理。不得把完整商品目录无条件塞进每次 prompt，不得展示模型虚构的商品字段，不得把工具调用过程写成正常聊天消息。

Version one excludes cart actions, detail pages, user profiles, collaborative filtering, vector search, inventory reservation, currency conversion, analytics, recommendation scoring, and dynamic tool administration. It must not inject the full catalog into every prompt, display model-invented product facts, or persist tool traces as ordinary chat messages.

## 方案比较与选择 / Approaches and Decision

### 方案一：原生 OpenAI-compatible Tool Calling（采用） / Native OpenAI-compatible tool calling (selected)

扩展现有 provider，解析流式 `tool_calls`，由 Agent 在白名单中执行工具，并把 `assistant.tool_calls` 与 `tool` 消息回传模型。最终推荐通过一个受控的 `submit_product_recommendation` 工具提交，Agent 在对外发送前缓冲并校验参数。

Extend the provider to parse streamed `tool_calls`. Agent executes only registered tools, then returns `assistant.tool_calls` and `tool` messages to the model. The model submits its final result through a controlled `submit_product_recommendation` tool whose buffered arguments are validated before anything is emitted downstream.

优点是符合目标流程、参数可严格校验，并与当前 OpenAI-compatible DeepSeek 接入一致。代价是 provider 不再只处理文本 delta，需要引入类型化模型事件和工具循环。

### 方案二：让模型输出约定 JSON 文本（不采用） / Convention-based JSON text (rejected)

实现较少，但模型可能在 JSON 外增加解释、生成不完整 JSON 或使用错误字段；错误恢复和流式解析都更脆弱。

### 方案三：Agent 预取商品并注入 prompt（不采用） / Agent-side catalog prefetch (rejected)

改动最小，但每轮都读取并发送目录，违背“模型按需选择工具”的目标，也会随目录增长扩大 token 与隐私边界。

## 总体流程 / End-to-end Flow

```text
Persisted user turn
  → build completed conversation context
  → model call with search_products + submit_product_recommendation
  → zero or more search_products tool calls (maximum 3 total)
  → validate filters → execute mock-backed ProductSearchTool
  → return bounded tool results as tool messages
  → model calls submit_product_recommendation
  → buffer and validate {answer, product_ids}
  → atomically persist answer + product IDs
  → SSE delta* → recommendations → done
  → Web resolves IDs from Mall catalog and renders cards
```

模型可以在同一轮调整条件并再次搜索，但所有 `search_products` 调用合计最多 3 次。一个模型响应若包含多个搜索调用，Agent 按返回顺序执行，仍计入总上限。未知工具、混合的最终提交与搜索调用、重复 tool-call ID 或超过上限都视为协议失败。

The model may refine filters and search again, with at most three search calls per user turn. Multiple search calls in one model response execute in returned order and count toward the same limit. Unknown tools, mixed search/finalization calls, duplicate tool-call IDs, or limit overflow are protocol failures.

## 工具契约 / Tool Contracts

### `search_products`

输入使用严格 JSON Schema，`additionalProperties: false`：

```json
{
  "keyword": "coffee",
  "category": "drinkware",
  "min_price_cents": 10000,
  "max_price_cents": 50000,
  "sort": "relevance",
  "limit": 5
}
```

- 所有筛选字段可选；预算筛选或分类筛选本身就是合法查询。
- `min_price_cents`、`max_price_cents` 为非负整数，且最小值不得大于最大值。
- `sort` 为 `relevance | price_asc | price_desc`，默认 `relevance`。
- `limit` 默认 5，范围 1–10。
- `keyword` 和 `category` 去除首尾空白；空字符串等同未提供。
- 第一版 mock 商品使用同一币种 TWD，价格条件直接按 `price_cents` 比较；跨币种查询留待真实商城搜索 API 处理。
- 第一版搜索默认只返回查询时 `in_stock` 的商品，避免推荐已知缺货商品；历史卡片仍显示商城当前库存，因此推荐后变为缺货的商品会显示 `out_of_stock`。

输出最多 10 项，只包含模型做推荐所需的可信字段：

```json
{
  "items": [
    {
      "id": "ceramic-mug",
      "name": "陶瓷马克杯 / Ceramic Mug",
      "description": "...",
      "price_cents": 39000,
      "currency": "TWD",
      "category": "drinkware",
      "tags": ["coffee", "tea"],
      "availability": "in_stock"
    }
  ]
}
```

`relevance` 采用确定性的简单匹配：名称、描述和标签中的关键词命中优先，分数相同时保持目录顺序。分类和价格先过滤，再排序和截断。无匹配时返回 `{"items": []}`，模型必须如实说明没有合适商品。

### `submit_product_recommendation`

这是最终结果通道，不访问外部服务：

```json
{
  "answer": "根据你的预算，我推荐……",
  "product_ids": ["ceramic-mug"]
}
```

- `answer` 去空白后必须非空，并有与现有消息一致的合理长度上限。
- `product_ids` 最多 10 个，保持模型顺序并去重。
- 每个 ID 必须出现在本轮至少一次成功的 `search_products` 结果中；否则整次最终提交无效，而不是静默展示虚构商品。
- 每个 ID 在被工具返回时必须为 `in_stock`；当前 mock 搜索已保证这一点。
- 若本轮没有搜索，普通非商品对话可以直接返回缓冲文本，商品 ID 固定为空；一旦执行过商品搜索，最终结果必须使用 `submit_product_recommendation`。

The finalization tool does not call Mall. It supplies non-empty prose and up to ten ordered, unique IDs. Every ID must have appeared in a successful search result from this turn. General non-product chat may finish with buffered text and no IDs if no search occurred; after any product search, finalization through this tool is mandatory.

## Agent 与 Provider 架构 / Agent and Provider Architecture

### 缓冲的模型响应 / Buffered model responses

保留当前只产出字符串的 legacy `ModelStream`，并为工具编排增加独立的 buffered `ModelResponse` 接口。Provider 完整消费上游 SSE，组装文本以及分片的 tool call 名称与 arguments，并验证 OpenAI-compatible 响应结构；Agent 负责业务级工具选择和参数校验。这样旧 `/chat` 无需改变，而工具阶段在进入业务状态机前已有完整、可验证的响应。

`ModelMessage` 扩展支持：

- `system | user | assistant` 文本消息；
- 带 `tool_calls` 的 assistant 消息；
- 带 `tool_call_id` 和工具 JSON 结果的 tool 消息。

Provider 的新接口返回 immutable `ModelResponse(content, tool_calls)`，请求可携带 `tools` 和 `tool_choice`。旧的无状态 `/chat` 继续走纯文本兼容入口，外部行为不变；持久化 conversation service 使用新的工具感知入口。

### `RecommendationAgent`

新增独立编排器，输入服务端构造的模型历史和 `ProductSearchTool`，输出经过验证的 `RecommendationResult(answer, product_ids)`。它负责：

1. 调用模型并缓冲本阶段结果；
2. 识别、校验和执行白名单工具；
3. 维护本轮可推荐商品 ID 集合；
4. 追加 assistant/tool 协议消息；
5. 限制 3 次搜索调用和总体模型轮数；
6. 验证最终提交；
7. 关闭每个模型流，并在取消时传播取消而不是转成普通错误。

`ConversationService` 继续负责创建 pending turn、取消屏蔽清理和原子完成；它不包含具体商品筛选规则。模型、工具和商城错误正文均不得写入数据库或公开响应。

### 商品工具抽象 / Product tool abstraction

定义可注入的异步 `ProductSearchTool` protocol。生产装配使用 `MallProductSearchTool`：第一版通过现有 `MallClient` 获取确定性 mock 目录，并在 Agent 侧执行筛选；测试直接注入 recording/fake tool。未来真实 Mall 提供搜索 API 时，只替换此实现，不改变 Agent 循环或公开 API。

Mall mock 商品模型补充 `category`、`tags` 和 `availability: in_stock | out_of_stock`。现有 `GET /products` 仍返回完整目录，兼容商城首页和 Web 商品卡片；既有字段不删除。

## 持久化 / Persistence

不把 ID 数组塞进自由格式字符串。新增规范化表：

```text
turn_recommendations
  turn_id TEXT NOT NULL REFERENCES turns(id) ON DELETE CASCADE
  product_id TEXT NOT NULL
  position INTEGER NOT NULL
  PRIMARY KEY (turn_id, product_id)
  UNIQUE (turn_id, position)
```

`complete_turn` 在同一个 SQLite 事务中写入完整 `assistant_content`、状态和有序商品 ID。失败、取消或空输出不保存任何 recommendation row。Retry 重用原 turn；重试成功前清理该 turn 的旧 recommendation rows，虽然正常 failed turn 本不应有旧数据。启动恢复逻辑不改变 completed recommendations。

Repository `Turn` 增加不可变 `recommended_product_ids: tuple[str, ...]`。公开 detail JSON 增加 `recommended_product_ids: string[]`，不暴露工具参数或工具原始结果。

`initialize()` 使用 `CREATE TABLE IF NOT EXISTS` 增加新表，因此现有数据库可原地升级，无迁移框架。外键、WAL、busy timeout 和单 worker 限制保持不变。

## SSE 与错误语义 / SSE and Error Semantics

最终模型结果先完整缓冲并校验，再对外发送；因此首个 token 会比当前纯文本流稍晚，但不会向用户泄露随后被判无效的推荐。

成功协议：

```text
event: delta
data: {"content":"根据你的预算，"}

event: delta
data: {"content":"推荐这两件商品。"}

event: recommendations
data: {"product_ids":["ceramic-mug","canvas-tote"]}

event: done
data: {}
```

`recommendations` 每个成功持久化 turn 恰好发送一次，即使数组为空，也必须位于所有 delta 之后、done 之前。服务器可按固定字符块拆分已校验 answer，以保留渐进显示；这不宣称是上游 token 原样转发。

失败语义沿用当前安全约定：打开模型失败返回 JSON 502；响应开始后的模型协议错误、非法工具参数、未知工具、循环超限、Mall 不可用、非法最终结果或断开清理都把 turn 标记为 failed，不保存部分文本或商品 ID，不发送 done。响应已开始时发送稳定 `event: error`；取消时不尝试向已断开的客户端发送事件。

建议稳定内部代码包括 `model_service_unavailable`、`model_stream_failed`、`tool_arguments_invalid`、`tool_loop_exceeded`、`mall_service_unavailable` 和 `recommendation_invalid`，公开消息不包含供应商响应、工具原始异常或密钥。

## Web 商品卡片 / Web Product Cards

Web 类型和 conversation client 增加 `recommended_product_ids` 与 `recommendations` SSE 处理。流式状态按 conversation ID 保存文本和 ID，继续遵守“切换会话不串写”的约束；终态仍重新加载 conversation detail 和摘要，以 SQLite 为权威。

聊天页加载商城目录并建立 `id → Product` 映射。mock 目录很小，第一版复用现有 `GET /api/products`，不为每张卡发独立请求。卡片紧跟对应 completed Agent 消息，展示：

- 名称 / Name
- 当前价格和币种 / Current price and currency
- 描述 / Description
- `有库存 / In stock` 或 `暂时缺货 / Out of stock`

模型文本中的价格、名称或库存不作为卡片数据源。若历史 ID 已不在当前目录中，保留一个带 ID 的双语“商品暂不可用 / Product unavailable”占位卡，而不是把整个回答判错。实时 recommendations 事件到达后可立即显示卡片；刷新后使用持久化 ID 恢复相同关联。

布局在桌面消息下使用卡片网格，窄屏改为单列。卡片是语义化列表项，库存状态有文本，不只依赖颜色；第一版没有点击动作。

## 配置与装配 / Configuration and Wiring

沿用 `MALL_SERVICE_URL` 和现有 YAML LLM 配置，不新增密钥。`create_app` 接受可注入的 product tool/agent factory 以便测试。生产 app 在 lifespan 中装配 Mall client、工具与 recommendation agent；import 模块不得触发网络调用。

工具定义使用固定白名单，不允许模型指定 URL、HTTP method、SQL、模块名或任意函数。发送给模型的 Mall 数据受 schema 和数量上限约束。

## 测试策略 / Test Strategy

### Provider

- 请求正确发送 tools/tool choice；
- 流式 tool-call ID、名称和 arguments 跨 chunk 组装；
- 文本、tool call、`[DONE]`、提前 EOF、非法 JSON、网络失败和关闭；
- 旧 `/chat` 文本流回归。

### Recommendation Agent

- 模型不需要工具时返回普通文本和空 IDs；
- 单次和多次结构化搜索；
- 参数默认值、过滤、排序、limit 和空结果；
- assistant/tool 消息与 tool_call_id 精确回传；
- 未知工具、非法参数、混合调用、循环超限；
- 最终 ID 去重、顺序、来源校验和虚构 ID 拒绝；
- Mall、模型和取消失败均关闭资源且不泄露错误正文。

### Repository and API

- schema 原地初始化和外键；
- answer 与 IDs 原子完成，failed/retry 不残留 IDs；
- detail JSON 和固定用户隔离；
- SSE 顺序严格为 delta*、recommendations、done；
- 中途失败无 recommendations/done；
- 历史模型上下文不包含 UI 元数据。

### Web and Mall Mock

- mock 商品的 category/tags/availability 与旧字段兼容；
- 实时与历史卡片、顺序、价格和库存双语状态；
- 未知历史 ID 占位；
- 切换会话时文本和卡片不串写；
- recommendations 缺失、重复、畸形或提前 EOF 使客户端拒绝流；
- 无动作按钮和响应式可访问布局。

### Native Smoke

使用临时 SQLite、本地 fake OpenAI-compatible tool-calling 模型和 mock Mall：验证模型先搜索、工具结果回传、最终提交、SSE recommendations、数据库持久化、刷新历史卡片以及第二轮继续上下文，全程不调用计费模型。

## 实施顺序 / Delivery Sequence

1. 扩展 Mall mock 商品字段和 Agent product tool contract。
2. 扩展 provider 的工具协议与回归兼容层。
3. 实现 RecommendationAgent 工具循环和最终校验。
4. 扩展 Repository、ConversationService、API 和 SSE。
5. 扩展 Web client、历史状态和商品卡片。
6. 更新 Native smoke、双语文档并执行全量验证。

每一步使用测试先行，旧的 `/chat`、商品首页和会话持久化行为必须保持通过。

## 构建者可自行决定的细节 / Builder Discretion

- 已校验 answer 拆分为 SSE delta 的固定块大小；
- 内部 dataclass、protocol 和模块文件的具体命名；
- 卡片网格间距、边框和颜色，只要延续现有页面视觉并满足可访问性；
- mock relevance 的精确权重，只要规则确定、可测试且文档注释清楚；
- 稳定错误代码到内部异常类的具体映射。
