# 商品推荐工具与对话商品卡片实施计划 / Product Recommendation Tools and Chat Product Cards Implementation Plan

> **供智能体执行 / For agentic workers:** 必须使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans`，按任务逐项实施。所有步骤使用复选框跟踪。 / REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` task by task. Steps use checkbox (`- [ ]`) syntax for tracking.

**目标 / Goal:** 让持久化 Agent 使用 OpenAI-compatible tool calling 按需查询 mock 商品，原子保存“推荐文本 + 商品 ID”，并让 Web 在实时和历史回答下渲染只读商品卡片。 / Let the persisted Agent query mock products through OpenAI-compatible tool calling, atomically save recommendation prose plus product IDs, and render read-only cards under live and historical replies.

**架构 / Architecture:** Mall mock 扩展可信商品字段；Agent 通过结构化 `ProductSearchTool` 查询目录，`RecommendationAgent` 驱动最多三次模型工具循环，并通过 `submit_product_recommendation` 获得缓冲、可校验的最终结果。SQLite 使用关系表保存有序商品 ID，conversation SSE 增加 `recommendations` 事件，Web 使用当前 Mall 目录解析 ID 并展示卡片。 / Mall mock gains trusted product fields. `RecommendationAgent` drives up to three structured search calls and obtains a buffered, validated final submission. SQLite stores ordered IDs relationally, conversation SSE adds a `recommendations` event, and Web resolves those IDs against the current Mall catalog.

**技术栈 / Tech Stack:** Python 3.11、Pydantic v2、AnyIO、httpx、FastAPI、SQLite、pytest、OpenAI-compatible Chat Completions、Next.js App Router、React、TypeScript、Vitest、Testing Library。

**设计文档 / Spec:** `docs/superpowers/specs/2026-11-05-product-recommendation-tools-design.md`

## 全局约束 / Global Constraints

- 仅持久化最终用户消息、完整 Agent 回答和推荐商品 ID；不持久化工具参数、工具结果、部分文本或供应商错误。 / Persist only user content, a complete Agent answer, and recommended IDs—not tool traces, partial text, or vendor errors.
- `search_products` 最多调用 3 次；`limit` 默认 5、范围 1–10；排序仅允许 `relevance | price_asc | price_desc`。 / Allow at most three searches; default limit 5, range 1–10; only the three specified sort values.
- keyword 最长 200 字符，category 最长 100 字符；价格为非负整数且 min 不得大于 max。 / Cap keyword at 200 and category at 100 characters; prices are non-negative integers with min ≤ max.
- mock 搜索只返回查询时 `in_stock` 的商品；最终 ID 必须来自本轮成功搜索结果，保持顺序、去重后最多 10 个。 / Mock search returns only currently in-stock products; final IDs must come from this turn's successful results, stay ordered, and deduplicate to at most ten.
- 搜索后必须通过 `submit_product_recommendation` 完成；未搜索的普通对话可以返回缓冲文本和空 ID。 / After any search, finalization must use `submit_product_recommendation`; non-search chat may return buffered prose with no IDs.
- 最终 answer 去空白后非空且最长 20,000 字符；校验完成前不向下游发送文本。 / Final prose is non-empty after trimming, capped at 20,000 characters, and emits nothing before validation.
- 成功 conversation SSE 必须严格为 `delta* → recommendations（恰好一次）→ done`；旧 `/chat` 协议保持不变。 / Successful conversation SSE is strictly delta* → exactly one recommendations → done; legacy `/chat` is unchanged.
- 商品卡片字段只来自当前 Mall API；模型文本和 tool arguments 不是卡片事实来源。 / Product-card facts come only from the current Mall API, never model prose or tool arguments.
- 固定 `user_id = 1`、单 pending turn、单 Agent worker、取消清理和失败可重试语义保持不变。 / Preserve fixed ownership, one pending turn, one Agent worker, cancellation cleanup, and retry semantics.
- 所有新 UI、错误提示和文档为中英双语。 / All new UI, errors, and documentation are bilingual.

## 审查重点 / Review Focus

- tool-call arguments 跨 chunk 且多个 call index 交错时，Provider 必须按 index 正确组装，不串 call；Task 2 测试覆盖。 / Interleaved streamed tool-call fragments must assemble by index without cross-contamination; Task 2 covers it.
- 模型同时返回搜索与最终提交、重复 call ID 或未知工具时，本轮必须安全失败且不执行部分工具集合；Task 3 覆盖。 / Mixed final/search calls, duplicate call IDs, and unknown tools fail before partial execution; Task 3 covers it.
- 客户端在已发送部分 synthetic delta 后断开时，turn 必须 failed 且 recommendation rows 为空；Task 4 覆盖。 / Disconnect after synthetic deltas must fail the turn and leave no recommendation rows; Task 4 covers it.
- 历史商品 ID 在当前目录消失时，Web 必须显示双语 unavailable 占位且不丢失回答；Task 5 覆盖。 / Missing historical IDs render a bilingual unavailable placeholder without hiding the answer; Task 5 covers it.
- 推荐后库存从 in_stock 变为 out_of_stock 时，历史卡片必须显示当前缺货状态，不把历史 turn 改为失败；Task 5 覆盖。 / A later stock change updates the card to current out-of-stock state without invalidating history; Task 5 covers it.

---

### Task 1：Mock 商品契约与结构化搜索工具 / Mock Product Contract and Structured Search Tool

**文件 / Files:**

- 修改 / Modify: `mall-service/src/mall_service/models.py`
- 修改 / Modify: `mall-service/src/mall_service/catalog.py`
- 修改 / Modify: `mall-service/tests/test_app.py`
- 修改 / Modify: `agent-service/src/agent_service/mall_client.py`
- 修改 / Modify: `agent-service/tests/test_mall_client.py`
- 新建 / Create: `agent-service/src/agent_service/product_tools.py`
- 新建 / Create: `agent-service/tests/test_product_tools.py`

**接口 / Interfaces:**

- 产出 / Produces: `Availability = Literal["in_stock", "out_of_stock"]`；Mall/API/Agent `ProductData` 均含 `category: str`、`tags: tuple[str, ...]`、`availability: Availability`。
- 产出 / Produces: `SearchSort = Literal["relevance", "price_asc", "price_desc"]`。
- 产出 / Produces: 严格 Pydantic `ProductSearchFilters(keyword: str | None, category: str | None, min_price_cents: int | None, max_price_cents: int | None, sort: SearchSort = "relevance", limit: int = 5)`，执行 strip、长度、数值和价格区间校验，`extra="forbid"`。
- 产出 / Produces: `ProductSearchTool` async protocol：`search(filters: ProductSearchFilters) -> tuple[ProductData, ...]`。
- 产出 / Produces: `MallProductSearchTool(client: MallClient)`，异步 `search` 使用 `anyio.to_thread.run_sync(client.list_products)`，过滤缺货/分类/价格，确定性相关度或价格排序，再按 limit 截断。

- [ ] **Step 1：编写失败的 Mall 与 MallClient 契约测试 / Write failing catalog contract tests**

新增断言：`GET /products` 和 `MallClient.list_products()` 保留旧字段并返回 category、完整 tags、`in_stock | out_of_stock`；严格拒绝缺字段、错误 tags 类型和未知 availability。现有两个 fixture 至少一个 in-stock，并新增一个 out-of-stock fixture，供 Web 历史状态测试复用。 / Assert new trusted fields while preserving old ones, plus strict invalid-payload rejection.

- [ ] **Step 2：运行测试并确认 RED / Run tests and verify RED**

Run:

```bash
PYTHONPATH=mall-service/src mall-service/.venv/bin/pytest mall-service/tests/test_app.py -v
PYTHONPATH=agent-service/src agent-service/.venv/bin/pytest agent-service/tests/test_mall_client.py -v
```

Expected: FAIL，因为模型和 `ProductData` 尚无新字段。 / FAIL because product contracts lack the fields.

- [ ] **Step 3：实现 mock 商品与客户端契约 / Implement mock catalog and client contract**

为每个 fixture 提供稳定 category/tags/availability；Pydantic 与 dataclass 使用不可变/严格数据边界，现有 `GET /products` 路径和旧字段不变。 / Add deterministic fields without changing the existing endpoint.

- [ ] **Step 4：编写失败的结构化筛选测试 / Write failing structured-search tests**

测试名与行为：

- `test_filters_by_trimmed_keyword_category_price_and_in_stock`
- `test_relevance_is_deterministic_and_matches_name_description_then_tags`
- `test_sorts_price_both_directions_and_applies_limit`
- `test_defaults_to_relevance_and_five_results`
- `test_rejects_unknown_fields_negative_prices_inverted_range_and_limit_bounds`
- `test_runs_sync_mall_client_in_worker_thread_and_maps_mall_failure`

使用手写 expected product ID 顺序，不复用实现评分函数。 / Use literal expected ID order independent of implementation helpers.

- [ ] **Step 5：运行搜索测试并确认 RED / Run search tests and verify RED**

Run: `PYTHONPATH=agent-service/src agent-service/.venv/bin/pytest agent-service/tests/test_product_tools.py -v`

Expected: FAIL，因为 `product_tools` 尚不存在。 / FAIL because the module does not exist.

- [ ] **Step 6：实现筛选 schema、protocol 与工具 / Implement filters, protocol, and tool**

相关度规则固定为：规范化大小写后，keyword 在 name 命中 3 分、description 命中 2 分、每个命中 tag 1 分；有 keyword 时过滤总分 0 项，按 `(-score, catalog_position)` 排序；无 keyword 时保持 catalog 顺序。category 使用大小写不敏感精确匹配。只返回 `in_stock`。 / Pin deterministic relevance scoring and filtering exactly.

- [ ] **Step 7：运行 Task 1 与两服务全量测试 / Verify Task 1 and service regressions**

```bash
PYTHONPATH=agent-service/src agent-service/.venv/bin/pytest agent-service/tests/test_product_tools.py agent-service/tests/test_mall_client.py -v
PYTHONPATH=agent-service/src agent-service/.venv/bin/pytest -q agent-service/tests
PYTHONPATH=mall-service/src mall-service/.venv/bin/pytest -q mall-service/tests
```

Expected: 全部通过。 / All pass.

- [ ] **Step 8：提交 / Commit**

```bash
git add mall-service/src/mall_service/models.py mall-service/src/mall_service/catalog.py mall-service/tests/test_app.py agent-service/src/agent_service/mall_client.py agent-service/src/agent_service/product_tools.py agent-service/tests/test_mall_client.py agent-service/tests/test_product_tools.py
git commit -m "feat: add structured product search tool"
```

### Task 2：OpenAI-compatible 工具调用 Provider / OpenAI-compatible Tool-calling Provider

**文件 / Files:**

- 修改 / Modify: `agent-service/src/agent_service/providers.py`
- 修改 / Modify: `agent-service/tests/test_streaming_provider.py`

**接口 / Interfaces:**

- 消费 / Consumes: 现有 `LLMConfig` 和旧 `open_stream(messages) -> ModelStream`，旧接口不得回归。
- 产出 / Produces: immutable `ModelToolCall(id: str, name: str, arguments: str)`、`ModelResponse(content: str, tool_calls: tuple[ModelToolCall, ...])`。
- 产出 / Produces: `ToolDefinition` JSON-compatible TypedDict；`ToolChoice = Literal["auto"] | dict[str, object]`。
- 产出 / Produces: 扩展 `ModelMessage` 以表达 assistant `tool_calls` 与 `tool` message 的 `tool_call_id`，同时保留现有文本消息。
- 产出 / Produces: `ToolCallingModelProvider.complete_response(messages: Sequence[ModelMessage], *, tools: Sequence[ToolDefinition], tool_choice: ToolChoice = "auto") -> ModelResponse` async protocol。
- 产出 / Produces: `OpenAICompatibleProvider.complete_response(...)` 使用 `stream: true`，完整消费并关闭响应，只有有效 `[DONE]` 才返回。

- [ ] **Step 1：编写失败的请求与纯文本响应测试 / Write failing request and buffered-text tests**

断言 exact request JSON 包含 model、messages、stream、tools、tool_choice；文本 delta 跨 UTF-8/事件分片后按顺序组成 `ModelResponse(content="...", tool_calls=())`；authorization 和安全错误行为保持现有约定。 / Assert exact request and buffered text behavior.

- [ ] **Step 2：运行目标测试并确认 RED / Run target tests and verify RED**

Run: `PYTHONPATH=agent-service/src agent-service/.venv/bin/pytest agent-service/tests/test_streaming_provider.py -k 'tool or complete_response' -v`

Expected: FAIL，因为新类型和方法不存在。 / FAIL because the interface is absent.

- [ ] **Step 3：实现类型与 `complete_response` 基础路径 / Implement types and text path**

复用现有 client 创建、HTTP 状态/content-type 校验和安全关闭 helper；不要让旧 `_OpenAIModelStream` 解析工具事件。新私有 parser 独立缓冲 response。 / Reuse transport safety but keep legacy parsing isolated.

- [ ] **Step 4：编写失败的流式 tool-call 组装测试 / Write failing streamed tool-call assembly tests**

覆盖：单个 arguments 分片、两个 call index 交错、ID/name 只在首 chunk 出现、arguments 含 Unicode、空 content、tool call 与文本并存、重复 index 元数据冲突、缺 ID/name、非法 arguments 仍原样返回给 Task 3 校验、提前 EOF/非法 JSON/网络 ReadError/取消与幂等关闭。 / Cover fragmented and malformed protocol boundaries.

Review Focus test: `test_interleaved_tool_call_chunks_are_assembled_by_index` 必须用交错 index `0,1,0,1` 并断言两个 literal argument strings。 / Pin the interleaving bug class.

- [ ] **Step 5：实现 tool-call parser / Implement tool-call parser**

按 `delta.tool_calls[*].index` 保存 builder；同 index 的 id/name 后续若出现必须相同。收到 `[DONE]` 后按 index 升序生成 tuple；缺失或冲突抛 `ModelProtocolError`，异常文本不包含原始 arguments。 / Assemble strictly by index and keep errors secret-safe.

- [ ] **Step 6：运行 Provider 与 Agent 全量回归 / Verify provider and Agent regressions**

```bash
PYTHONPATH=agent-service/src agent-service/.venv/bin/pytest agent-service/tests/test_streaming_provider.py -v
PYTHONPATH=agent-service/src agent-service/.venv/bin/pytest -q agent-service/tests
```

Expected: 新旧 Provider 测试全部通过。 / New and legacy provider tests pass.

- [ ] **Step 7：提交 / Commit**

```bash
git add agent-service/src/agent_service/providers.py agent-service/tests/test_streaming_provider.py
git commit -m "feat: support model tool calls"
```

### Task 3：RecommendationAgent 工具循环与最终校验 / RecommendationAgent Tool Loop and Final Validation

**文件 / Files:**

- 新建 / Create: `agent-service/src/agent_service/recommendation_agent.py`
- 新建 / Create: `agent-service/tests/test_recommendation_agent.py`

**接口 / Interfaces:**

- 消费 / Consumes: Task 1 `ProductSearchTool`、`ProductSearchFilters`、`ProductData`；Task 2 `ToolCallingModelProvider`、`ModelMessage`、`ModelResponse`、`ModelToolCall`、`ToolDefinition`。
- 产出 / Produces: immutable `RecommendationResult(answer: str, product_ids: tuple[str, ...])`。
- 产出 / Produces: `RecommendationAgent(provider, product_tool, *, max_search_calls: int = 3)`；`async generate(messages: Sequence[ModelMessage]) -> RecommendationResult`。
- 产出 / Produces: secret-safe `ToolArgumentsInvalid`、`ToolLoopExceeded`、`RecommendationInvalid`、`UnknownTool`；不得把 arguments、Mall 响应或模型正文放进异常文本。
- 产出 / Produces: module constants `SEARCH_PRODUCTS_TOOL` 与 `SUBMIT_RECOMMENDATION_TOOL`，均为 exact JSON schema；answer 最大 20,000、IDs 最大 10。
- 产出 / Produces: 固定 `RECOMMENDATION_POLICY_PROMPT` 作为附加 system message，要求商品事实先搜索、搜索后用 submit 工具结束、不得提交搜索结果外 ID；客户端用户内容不能覆盖该策略。

- [ ] **Step 1：编写失败的工具定义与普通对话测试 / Write failing schema and no-tool tests**

断言两个 tool definition 的 required/additionalProperties/schema bounds；模型零 tool calls 且非空文本返回 trimmed answer、空 IDs；空文本无 tool calls 抛 `RecommendationInvalid`。 / Pin exact schemas and general-chat behavior.

- [ ] **Step 2：运行测试并确认 RED / Run tests and verify RED**

Run: `PYTHONPATH=agent-service/src agent-service/.venv/bin/pytest agent-service/tests/test_recommendation_agent.py -v`

Expected: FAIL，因为模块不存在。 / FAIL because the module does not exist.

- [ ] **Step 3：实现单次搜索与最终提交 happy path / Implement one-search happy path**

测试先断言 provider 两次请求：首次 tools + auto；搜索后追加 assistant tool_calls 和对应 `role="tool"` JSON（含准确 tool_call_id）；第二次模型调用后解析 submit arguments，校验 ID 来源并返回结果。实现只满足此路径。 / First implement the exact one-search transcript.

- [ ] **Step 4：编写失败的多轮、输入与失败测试 / Write failing loop and failure tests**

覆盖：

- 两次调整搜索后第三次 submit，候选 ID 为所有成功结果并集；
- 搜索空结果仍可提交说明文字与空 IDs；
- Pydantic 默认值、未知字段、负数、倒置区间和 limit；
- Mall failure 映射为 stable Agent error；
- 第四次搜索在执行前触发 `ToolLoopExceeded`；
- submit 前重复 IDs 保序去重；虚构 ID、过长 answer、非法 JSON、错误 submit schema 触发 `RecommendationInvalid`；
- 搜索后返回普通文本而不 submit 失败；
- 未知工具失败；
- Review Focus：同一 response 混合 search+submit、重复 call ID 和两个 submit call 均在执行任何 tool 前失败；
- 取消传播，不转换为普通错误。

- [ ] **Step 5：实现完整状态机 / Implement the complete state machine**

每个 response 先整体分类和验证，再执行其中的 search calls，避免半批执行。总体模型调用最多 `max_search_calls + 2`；每轮 assistant tool_calls 原样（安全字段）加入上下文，每个 tool result 使用 `json.dumps(..., ensure_ascii=False)`。 / Validate each response as a whole before execution and bound total calls.

- [ ] **Step 6：运行 RecommendationAgent 与 Agent 全量测试 / Verify agent loop and regressions**

```bash
PYTHONPATH=agent-service/src agent-service/.venv/bin/pytest agent-service/tests/test_recommendation_agent.py -v
PYTHONPATH=agent-service/src agent-service/.venv/bin/pytest -q agent-service/tests
```

Expected: 全部通过。 / All pass.

- [ ] **Step 7：提交 / Commit**

```bash
git add agent-service/src/agent_service/recommendation_agent.py agent-service/tests/test_recommendation_agent.py
git commit -m "feat: orchestrate product recommendation tools"
```

### Task 4：推荐持久化、Conversation API 与 SSE / Recommendation Persistence, Conversation API, and SSE

**文件 / Files:**

- 修改 / Modify: `agent-service/src/agent_service/conversations.py`
- 修改 / Modify: `agent-service/src/agent_service/conversation_service.py`
- 修改 / Modify: `agent-service/src/agent_service/app.py`
- 修改 / Modify: `agent-service/tests/test_conversations.py`
- 修改 / Modify: `agent-service/tests/test_conversation_service.py`
- 修改 / Modify: `agent-service/tests/test_app.py`

**接口 / Interfaces:**

- 消费 / Consumes: Task 3 `RecommendationAgent.generate(messages) -> RecommendationResult`。
- 产出 / Produces: `Turn.recommended_product_ids: tuple[str, ...]`；公开 detail turn 增加 `recommended_product_ids: list[str]`。
- 产出 / Produces: `ConversationRepository.complete_turn(user_id, conversation_id, turn_id, assistant_content, recommended_product_ids: Sequence[str] = ()) -> Turn`，answer 与推荐行单事务完成。
- 产出 / Produces: `turn_recommendations(turn_id, product_id, position)` schema，主键/position unique/foreign-key cascade 如 spec。
- 产出 / Produces: `BufferedRecommendationStream(result, chunk_size=64)` 实现现有 stream contract，暴露 immutable `recommended_product_ids`；`ConversationService` 使用可注入 `RecommendationAgent` 生成已校验结果。
- 产出 / Produces: conversation SSE 增加 `event: recommendations`；旧 `/chat` 保持 delta/done。

- [ ] **Step 1：编写失败的 Repository schema 与原子性测试 / Write failing persistence tests**

新增测试：schema constraints；IDs 保序读取；answer+IDs 原子完成；重复 ID 输入在 repository 边界拒绝；failed/cancel/retry 清空推荐；跨用户隔离；旧数据库 `initialize()` 原地增加表且保留 turns；model_messages 不包含推荐元数据。 / Cover schema, atomicity, upgrade, and context separation.

- [ ] **Step 2：运行 Repository 测试并确认 RED / Run repository tests and verify RED**

Run: `PYTHONPATH=agent-service/src agent-service/.venv/bin/pytest agent-service/tests/test_conversations.py -v`

Expected: FAIL，缺少 recommendation schema/field/signature。 / FAIL because recommendation persistence is absent.

- [ ] **Step 3：实现 recommendation table 与 Repository 读写 / Implement table and repository I/O**

`complete_turn` 在同一显式事务中验证唯一非空 IDs、更新 turn、删除旧 rows、按 position 插入；任一插入失败必须回滚 turn 状态和 answer。`fail_turn` 与 `prepare_retry` 防御性删除该 turn recommendation rows。 / Enforce atomic completion and defensive cleanup.

- [ ] **Step 4：编写失败的 ConversationService 测试 / Write failing service tests**

用 fake RecommendationAgent 覆盖：模型历史输入、结果在流结束前仍 pending、流正常结束原子保存 answer+IDs、固定 64 字符 chunk、生成失败、空/非法结果防御、消费者 early close/cancel 后 failed 且无 IDs、retry 重用 turn。 / Cover buffered generation and lifecycle.

Review Focus test: 读取首 delta 后关闭外层 `_persisted_model_events`，断言 turn failed、`assistant_content is None`、IDs 为空。 / Pin downstream disconnect cleanup.

- [ ] **Step 5：实现 buffered stream 与 service 集成 / Implement buffered stream and service integration**

创建 pending turn 后构造数据库历史并调用 `RecommendationAgent.generate`；生成阶段异常先 fail 再抛安全领域异常。stream 只有完整消费后调用 `complete_turn(..., result.product_ids)`；取消屏蔽清理沿用现有模式。 / Generate before response, persist only after full downstream consumption.

- [ ] **Step 6：编写失败的 API/SSE/装配测试 / Write failing API, SSE, and wiring tests**

覆盖：生产 `create_app` 从 `MALL_SERVICE_URL` 装配 Mall tool 与 RecommendationAgent，import 不联网；成功 SSE exact recommendations frame（含空数组）；detail IDs；生成阶段 model/Mall/tool/final-validation 错误映射稳定 JSON 502；响应阶段异常稳定 error 且无 recommendations/done；固定用户和 legacy `/chat` 回归。 / Cover production wiring and public contracts.

- [ ] **Step 7：实现 app 装配、序列化和 recommendations event / Implement wiring, serialization, and event**

`_persisted_model_events` 在内部 stream 正常完成并已提交后发送一次 recommendations，再发送 done；其 finally 继续显式关闭内部 iterator。`create_app` 提供可注入 recommendation agent/tool factory，测试不得访问真实 Mall。 / Emit metadata only after committed completion.

- [ ] **Step 8：运行 Agent 全量测试 / Run complete Agent suite**

Run: `PYTHONPATH=agent-service/src agent-service/.venv/bin/pytest -q agent-service/tests`

Expected: 全部通过，旧 `/chat` 和持久化历史测试无回归。 / All pass with legacy behavior intact.

- [ ] **Step 9：提交 / Commit**

```bash
git add agent-service/src/agent_service/conversations.py agent-service/src/agent_service/conversation_service.py agent-service/src/agent_service/app.py agent-service/tests/test_conversations.py agent-service/tests/test_conversation_service.py agent-service/tests/test_app.py
git commit -m "feat: persist recommended products"
```

### Task 5：Web recommendations 客户端与历史商品卡片 / Web Recommendations Client and Historical Product Cards

**文件 / Files:**

- 修改 / Modify: `web/lib/types.ts`
- 修改 / Modify: `web/lib/chat.ts`
- 修改 / Modify: `web/lib/chat.test.ts`
- 修改 / Modify: `web/lib/conversations.ts`
- 修改 / Modify: `web/lib/conversations.test.ts`
- 新建 / Create: `web/lib/products.ts`
- 新建 / Create: `web/lib/products.test.ts`
- 修改 / Modify: `web/app/chat/page.tsx`
- 修改 / Modify: `web/app/chat/page.module.css`
- 修改 / Modify: `web/app/chat/page.test.tsx`
- 按需修改 / Modify if required: `web/app/page.tsx`、`web/app/page.test.tsx`（仅适配 Product 新字段，不改变首页行为）

**接口 / Interfaces:**

- 消费 / Consumes: Task 4 detail `recommended_product_ids` 和 SSE recommendations frame；现有 `/api/products` 返回 Task 1 字段。
- 产出 / Produces: `Product` 增加 category、tags、availability；`ConversationTurn.recommended_product_ids: string[]`。
- 产出 / Produces: `consumeChatStream(response, onDelta, options?)` 支持 `{onRecommendations?: (ids: string[]) => void, requireRecommendations?: boolean}`；legacy `streamChat` 不要求 recommendations，conversation send/retry 必须要求恰好一次。
- 产出 / Produces: `listProducts() -> Promise<Product[]>`（从 `ProductList.items` 解码；非 2xx/畸形 payload 拒绝）。
- 产出 / Produces: 页面使用当前 catalog map 渲染每个 completed turn 和活动 stream 的 IDs。

- [ ] **Step 1：编写失败的 SSE 与 JSON 类型客户端测试 / Write failing client contract tests**

覆盖：detail IDs；delta 后 recommendations 后 done；空 IDs；UTF-8/chunk split；重复 recommendations、recommendations 后 delta、缺 recommendations 直接 done、非法/重复/非字符串 ID、error 和 EOF 均 reject；legacy streamChat 仍接受无 recommendations 的旧协议。 / Pin strict conversation metadata without breaking legacy chat.

- [ ] **Step 2：运行客户端测试并确认 RED / Run client tests and verify RED**

Run: `/Users/stone/.nvm/versions/node/v22.22.2/bin/node web/node_modules/vitest/vitest.mjs run --root web lib/chat.test.ts lib/conversations.test.ts lib/products.test.ts`

Expected: FAIL，类型、products client 和 recommendations parser 尚不存在。 / FAIL because contracts are absent.

- [ ] **Step 3：实现 types、products client 与严格 SSE 状态机 / Implement types, product client, and SSE state machine**

conversation consumer 追踪 `seenRecommendations` 和 `seenDone`；只在 requireRecommendations 时强制 metadata；ID 保持服务端顺序，客户端拒绝重复而不是静默修正。`listProducts` 验证 `items` array 和每项所需字段/availability。 / Preserve order and reject malformed metadata.

- [ ] **Step 4：编写失败的商品卡片页面测试 / Write failing product-card page tests**

mock typed clients，覆盖：

- 历史 completed turn 按 ID 顺序显示名称、格式化 TWD 价格、描述和双语库存；
- recommendations 实时到达后在原 conversation 显示，切换会话不串卡片；
- retry 成功重载 authoritative detail；
- Review Focus：目录缺失的历史 ID 显示含 ID 的“商品暂不可用 / Product unavailable”；
- Review Focus：同一历史 turn 在 catalog 当前为 out_of_stock 时显示“暂时缺货 / Out of stock”且回答仍显示；
- catalog loading/error 不阻塞文字对话，使用双语卡片状态；
- 卡片使用语义化 list/listitem，无链接或操作按钮；
- 窄屏 class/layout 保留，输入只锁定活动会话。

- [ ] **Step 5：实现页面 catalog cache、实时状态与卡片 / Implement catalog cache, live IDs, and cards**

页面 mount 并行加载 conversation summaries 与 catalog；用 `Map`/record 按 ID 查当前商品。活动 stream state 增加 `productIds`，`onRecommendations` 只更新目标 conversation；终态仍 reload detail/list。抽出局部 `ProductCards` 组件仅在页面文件过长时放入 `web/app/chat/product-cards.tsx`，并同步测试路径。 / Keep SQLite authoritative and isolate live metadata by conversation.

- [ ] **Step 6：实现响应式双语样式 / Implement responsive bilingual card styles**

桌面使用 `repeat(auto-fit, minmax(...))` 网格，窄屏单列；库存有可见文本，颜色只作辅助；价格使用 `Intl.NumberFormat` 与商品 currency，测试不依赖 locale 模糊输出，使用固定 formatter option。 / Build accessible, read-only cards.

- [ ] **Step 7：运行 Web 全量测试和生产构建 / Run Web tests and production build**

Run from repository root:

`/Users/stone/.nvm/versions/node/v22.22.2/bin/node web/node_modules/vitest/vitest.mjs run --root web`

Then run from `web/`:

`/Users/stone/.nvm/versions/node/v22.22.2/bin/node node_modules/next/dist/bin/next build`

Expected: 测试和构建通过。隔离 worktree 若 node_modules 为外部软链接，可使用 `next build --webpack` 规避 Turbopack root 限制。 / Tests and production build pass.

- [ ] **Step 8：提交 / Commit**

```bash
git add web/lib/types.ts web/lib/chat.ts web/lib/chat.test.ts web/lib/conversations.ts web/lib/conversations.test.ts web/lib/products.ts web/lib/products.test.ts web/app/chat/page.tsx web/app/chat/page.module.css web/app/chat/page.test.tsx
git commit -m "feat: render recommended product cards"
```

若首页为适配 Product 字段而修改，再单独加入 `web/app/page.tsx` 和测试；不得宽泛暂存目录。 / Add homepage files only if actually changed; never stage broad directories.

### Task 6：Native Tool-calling 冒烟与双语文档 / Native Tool-calling Smoke and Bilingual Documentation

**文件 / Files:**

- 修改 / Modify: `scripts/smoke_test.py`
- 修改 / Modify: `tests/test_smoke.py`
- 修改 / Modify: `README.md`
- 仅在实现证实设计需修正时修改 spec/plan。 / Modify spec/plan only for verified corrections.

**接口 / Interfaces:**

- 消费 / Consumes: Tasks 1–5 的公开 Mall、Agent、BFF 和 Web contract。
- 产出 / Produces: 本地 fake OpenAI-compatible server，第一次返回 fragmented `search_products` tool call，接收 tool result 后返回 `submit_product_recommendation`，全程零计费。

- [ ] **Step 1：编写失败的端到端工具断言 / Write failing end-to-end tool assertions**

扩展 smoke 测试断言：模型请求包含 exact tools；首个 assistant tool call 的 filters 结构化；第二次请求含匹配 tool_call_id 和可信 Mall result；最终 SSE 含 recommendations；detail 持久化 IDs；`GET /api/products` 能解析卡片字段；第二轮上下文含第一轮文字但不含 UI 元数据/原始工具 trace；临时 DB 存在且响应不含 smoke key。 / Verify the complete tool loop and persistence boundaries.

- [ ] **Step 2：运行 smoke 并确认 RED / Run smoke and verify RED**

Run: `NODE_EXECUTABLE=/Users/stone/.nvm/versions/node/v22.22.2/bin/node python3.11 -m unittest discover -s tests -p 'test_smoke.py' -v`

Expected: FAIL，fake model 仍只返回文本且 API 无 recommendation metadata。 / FAIL because tool calling is not represented.

- [ ] **Step 3：实现 fake tool-calling 模型与断言采集 / Implement fake model and capture**

fake server 根据收到的 messages 状态确定响应，使用 SSE 分片打断 tool arguments，记录每次 request；不得依赖调用次数而忽略 tool_call_id/content。Mall、Agent、Web 和 DB 仍使用临时端口/目录并在 finally 清理。 / Make the fake protocol-driven and deterministic.

- [ ] **Step 4：更新双语 README / Update bilingual README**

说明工具流程、结构化 filters、最多三次搜索、最终 ID 校验、recommendations SSE、持久化、商品卡片、mock Mall 字段、失败/重试和单 worker 约束；保留 Node engines、配置与密钥安全说明。不得粘贴本地 key。 / Document operations and public behavior without secrets.

- [ ] **Step 5：执行最终全量验证 / Run fresh full verification**

Run from repository root, as separate commands:

```bash
PYTHONPATH=agent-service/src agent-service/.venv/bin/pytest -q agent-service/tests
PYTHONPATH=mall-service/src mall-service/.venv/bin/pytest -q mall-service/tests
/Users/stone/.nvm/versions/node/v22.22.2/bin/node web/node_modules/vitest/vitest.mjs run --root web
NODE_EXECUTABLE=/Users/stone/.nvm/versions/node/v22.22.2/bin/node python3.11 -m unittest discover -s tests -p 'test_smoke.py' -v
git diff --check
```

Run the build separately from `web/`:

`/Users/stone/.nvm/versions/node/v22.22.2/bin/node node_modules/next/dist/bin/next build`

Expected: Agent、Mall、Web、build、tool-calling smoke 和 diff 检查全部零失败。隔离 worktree 仅在依赖软链接限制下使用 `--webpack`，并记录原因。 / All suites, build, smoke, and diff checks pass.

- [ ] **Step 6：提交 / Commit**

```bash
git add scripts/smoke_test.py tests/test_smoke.py README.md docs/superpowers/specs/2026-11-05-product-recommendation-tools-design.md docs/superpowers/plans/2026-11-05-product-recommendation-tools.md
git commit -m "docs: describe tool-driven recommendations"
```

对已批准文档只暂存实际修正 hunk。 / Stage only verified documentation corrections.
