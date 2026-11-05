# OpenAI Agents SDK ReAct 迁移规格 / OpenAI Agents SDK ReAct Migration Specification

**规格文档 / Spec**: `docs/superpowers/001-openai-agents-react/spec.md`  
**技术方案 / Plan**: `docs/superpowers/001-openai-agents-react/plan.md`  
**任务清单 / Tasks**: `docs/superpowers/001-openai-agents-react/tasks.md`  
**基线版本 / Baseline Version**: `PLAN-v1`  
**日期 / Date**: 2026-11-05  
**状态 / Status**: 已授权实施 / Authorized for Implementation

## 变更记录 / Change Log

| 日期 / Date | 版本 / Version | 变更 / Change |
|---|---|---|
| 2026-11-05 | PLAN-v1 | 将商品推荐 ReAct 循环迁移到 OpenAI Agents SDK，同时保持既有 API、持久化和 Web 契约。 / Migrate the product-recommendation ReAct loop to the OpenAI Agents SDK while preserving existing API, persistence, and Web contracts. |

## 概述 / Overview

`agent-service` 当前自行解析模型工具调用并维护搜索循环。本次改造由 `openai-agents` 的 `Agent`、`Runner` 和 function tools 接管模型选择工具、执行工具、回传结果及继续推理的循环。商城数据仍只能来自 `mall-service`，最终商品 ID 仍由服务端校验并持久化。

`agent-service` currently parses model tool calls and maintains the search loop itself. This change delegates tool selection, execution, result feedback, and continued reasoning to the `openai-agents` `Agent`, `Runner`, and function tools. Mall facts remain sourced only from `mall-service`, and final product IDs remain server-validated and persisted.

## 调用场景 / Call Scenarios

- 作为用户，我希望普通问题得到普通文本回答，且不会出现商品卡片。 / As a user, I want ordinary questions to receive text-only answers without product cards.
- 作为购物用户，我希望模型按需查询商城后推荐真实商品。 / As a shopper, I want the model to query the mall as needed before recommending real products.
- 作为维护者，我希望 ReAct 循环由标准 SDK 管理，同时保留现有 HTTP/SSE/SQLite 行为。 / As a maintainer, I want a standard SDK to manage the ReAct loop without changing HTTP/SSE/SQLite behavior.

## 功能性需求 / Functional Requirements

### FR-001 SDK 编排 / SDK orchestration

- 持久化会话的回复必须通过 OpenAI Agents SDK `Runner` 执行。 / Persisted conversation replies must run through the OpenAI Agents SDK `Runner`.
- 模型必须由现有 YAML 中的 `base_url`、`api_key`、`model` 和超时配置构造，支持 OpenAI-compatible Chat Completions。 / The model must be built from the existing YAML `base_url`, `api_key`, `model`, and timeout settings and support OpenAI-compatible Chat Completions.
- 不得把密钥写入仓库、日志或错误响应。 / API keys must not be written to the repository, logs, or error responses.

### FR-002 商品工具 / Product tools

- Agent 可以调用 `search_products`，输入仍包含关键词、分类、最低价、最高价、排序和数量上限。 / The agent may call `search_products` with keyword, category, minimum price, maximum price, sort, and limit.
- 单轮最多执行三次商城搜索；一旦某次搜索返回商品，后续不得再次搜索。 / A turn may perform at most three mall searches; after a search returns products, further searches are forbidden.
- Mall 不可用、参数非法和循环超限必须继续映射到已有错误码。 / Mall unavailability, invalid arguments, and loop exhaustion must retain their existing error-code mappings.

### FR-003 最终输出约束 / Final-output constraints

- 无搜索的普通回复可以直接输出文本，商品 ID 必须为空。 / A no-search ordinary reply may return text directly and must have no product IDs.
- 搜索后必须调用 `submit_product_recommendation` 完成回复。 / After searching, the model must call `submit_product_recommendation` to finish the reply.
- 提交的商品 ID 必须来自本轮搜索结果，去重后最多十个。 / Submitted product IDs must come from this turn's search results, be deduplicated, and contain at most ten IDs.
- 回复正文必须非空且不超过 20,000 字符。 / The answer must be non-empty and at most 20,000 characters.

### FR-004 兼容性 / Compatibility

- `/chat`、会话创建/读取、重试接口、SSE 事件和 Web 商品卡片协议不得改变。 / `/chat`, conversation creation/read, retry endpoints, SSE events, and the Web product-card protocol must not change.
- SQLite Schema 和历史数据不得迁移。 / The SQLite schema and existing history must not be migrated.
- 无配置启动时仍返回现有 `model_not_configured` 行为。 / Starting without model configuration must retain the existing `model_not_configured` behavior.

## 非功能性需求 / Non-Functional Requirements

- 使用 `openai-agents` 的 Chat Completions 模型适配器，不依赖仅支持 OpenAI Responses API 的能力。 / Use the `openai-agents` Chat Completions model adapter and avoid Responses-only capabilities.
- 非 OpenAI provider 默认禁用 SDK tracing，避免使用 DeepSeek 密钥向 OpenAI tracing 端点认证。 / Disable SDK tracing by default for the non-OpenAI provider so a DeepSeek key is not used against OpenAI tracing endpoints.
- 依赖版本必须限制在兼容的主版本范围。 / The dependency must be constrained to a compatible major-version range.
- 单元测试不得访问真实网络。 / Unit tests must not access the real network.

## 验收标准 / Acceptance Criteria

- **AC-001**：Given 普通对话，When SDK Runner 返回文本且未调用搜索，Then 返回相同文本和空商品 ID。 / Given an ordinary conversation, when the SDK Runner returns text without a search, then the same text and no product IDs are returned.
- **AC-002**：Given 商品需求，When Agent 搜索并提交，Then Mall 结果进入工具上下文，且只有已检索 ID 被返回。 / Given a product request, when the agent searches and submits, then mall results enter the tool context and only retrieved IDs are returned.
- **AC-003**：Given 空搜索结果，When Agent 调整筛选，Then 最多允许三次搜索；第四次在调用 Mall 前失败。 / Given empty search results, when the agent refines filters, then at most three searches are allowed and a fourth fails before calling the mall.
- **AC-004**：Given 非空搜索结果，Then `search_products` 在余下 run 中被禁用，Agent 只能提交或失败。 / Given a non-empty search result, `search_products` is disabled for the rest of the run and the agent can only submit or fail.
- **AC-005**：Given 提交未知商品 ID、空正文或非法参数，Then 结果被拒绝且不持久化为成功。 / Given an unknown product ID, empty answer, or invalid arguments, the result is rejected and not persisted as successful.
- **AC-006**：Given DeepSeek-compatible 配置，Then SDK 使用配置的 base URL、模型、超时和 API key，并禁用 tracing。 / Given DeepSeek-compatible configuration, the SDK uses the configured base URL, model, timeout, and API key with tracing disabled.
- **AC-007**：Given 现有 API 测试和会话测试，Then 外部协议与错误映射保持通过。 / Given the existing API and conversation tests, external contracts and error mappings remain passing.

## 具体测试 Case / Concrete Test Cases

| 输入 / Input | 预期 / Expected | 映射 / Mapping |
|---|---|---|
| `你好` | 文本回复，`product_ids=[]` / Text reply with `product_ids=[]` | AC-001 |
| `推荐一个杯子` | 搜索一次并提交已检索 ID / Search once and submit retrieved IDs | AC-002 |
| 连续空结果 / Repeated empty results | 第四次搜索抛出 `ToolLoopExceeded` / Fourth search raises `ToolLoopExceeded` | AC-003 |
| 搜索返回 `mug` 后再次搜索 / Search again after finding `mug` | 工具被禁用或请求失败，不再次调用 Mall / Tool disabled or run fails without another mall call | AC-004 |
| 提交 `invented-id` | `RecommendationInvalid` | AC-005 |
| DeepSeek YAML | `AsyncOpenAI(base_url=...)` + Chat Completions adapter + tracing off | AC-006 |

## 输出契约示例 / Output Contract Example

```json
{
  "answer": "推荐这款陶瓷杯。",
  "product_ids": ["ceramic-mug"]
}
```

外部 SSE 仍发送 `delta`、`recommendations`、`done`；本次不增加新的事件。 / External SSE continues to emit `delta`, `recommendations`, and `done`; no new event is added.

## 治理审查 / Governance Review

本次新增第三方依赖已由用户明确指定。实现采用最小替换边界：只替换持久化推荐的编排层，不修改数据库、Mall API 或 Web。 / The new third-party dependency is explicitly requested by the user. The implementation uses the narrowest replacement boundary: only persisted-recommendation orchestration changes; the database, Mall API, and Web remain unchanged.

## 非目标 / Out of Scope

- 不引入多 Agent handoff、MCP、OpenAI hosted tools 或 SDK Sessions。 / No multi-agent handoffs, MCP, OpenAI hosted tools, or SDK Sessions.
- 不把 SQLite 会话历史迁移到 SDK Session。 / Do not migrate SQLite history to an SDK Session.
- 不改造无状态 `/chat` 的逐 token provider。 / Do not migrate the stateless `/chat` token-streaming provider.
- 不修改 Web UI、Mall 数据或登录系统。 / Do not modify the Web UI, mall data, or authentication.
