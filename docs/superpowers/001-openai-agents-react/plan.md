# OpenAI Agents SDK ReAct 实施计划 / OpenAI Agents SDK ReAct Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**规格文档 / Spec**: `docs/superpowers/001-openai-agents-react/spec.md`  
**技术方案 / Plan**: `docs/superpowers/001-openai-agents-react/plan.md`  
**任务清单 / Tasks**: `docs/superpowers/001-openai-agents-react/tasks.md`  
**基线版本 / Baseline Version**: `PLAN-v1`  
**日期 / Date**: 2026-11-05  
**状态 / Status**: 已授权实施 / Authorized for Implementation

**目标 / Goal:** 使用 OpenAI Agents SDK 替换手写商品推荐 ReAct 循环，同时保持外部行为兼容。 / Replace the hand-written product-recommendation ReAct loop with the OpenAI Agents SDK while preserving external behavior.

**架构 / Architecture:** `RecommendationAgent` 保留为 `ConversationService` 的稳定边界，但内部用一个运行上下文、两个 function tools 和 `Runner.run` 编排。模型使用 `AsyncOpenAI` + `OpenAIChatCompletionsModel` 连接 YAML 指定的 OpenAI-compatible endpoint；提交工具采用 stop-at-tool 语义产生最终结构化结果。 / `RecommendationAgent` remains the stable boundary for `ConversationService`, but internally delegates orchestration to a run context, two function tools, and `Runner.run`. The model uses `AsyncOpenAI` plus `OpenAIChatCompletionsModel` against the YAML-configured OpenAI-compatible endpoint; the submit tool uses stop-at-tool behavior to produce the final structured result.

**技术栈 / Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, `openai-agents`, OpenAI Python SDK, pytest, anyio.

## 全局约束 / Global Constraints

- 保留现有 `RecommendationGenerator.generate(messages) -> RecommendationResult` 协议。 / Preserve the existing generator protocol.
- 不改变 HTTP、SSE、SQLite Schema、Mall API 或 Web。 / Do not change HTTP, SSE, SQLite schema, Mall API, or Web.
- SDK tracing 对 DeepSeek-compatible runtime 默认关闭。 / SDK tracing is disabled by default for the DeepSeek-compatible runtime.
- 行为变更测试先行，测试不访问网络。 / Behavior changes are test-first and tests do not access the network.

## 审核重点 / Review Focus

1. SDK 对同一模型响应中的并行工具调用处理不得绕过搜索计数或提交校验。 / Parallel tool calls must not bypass search limits or submission validation.
2. 搜索找到商品后不得再次访问 Mall。 / The mall must not be called again after products are found.
3. SDK 异常必须映射为既有公开错误，不泄露密钥或上游正文。 / SDK failures must map to existing public errors without leaking keys or upstream bodies.
4. 多轮历史必须只传一次系统提示且保持角色顺序。 / Multi-turn history must preserve role order and avoid duplicating system instructions.
5. DeepSeek 不支持的 Responses-only 或强制 named tool-choice 能力不得进入请求。 / Responses-only features and unsupported forced named tool choice must not reach DeepSeek.

## 输入依据与调研登记 / Inputs and Research Register

| 类型 / Type | 结论 / Finding | 来源 / Source | 状态 / Status |
|---|---|---|---|
| 用户 / User | 项目使用 OpenAI Agents SDK 构建 ReAct 循环。 / Use the OpenAI Agents SDK for the ReAct loop. | 2026-11-05 用户指令 / user request | 事实 / Fact |
| 仓库 / Repository | `ConversationService` 只依赖 `generate` 和 `RecommendationResult`，适合作为迁移边界。 / `ConversationService` depends only on `generate` and `RecommendationResult`. | `conversation_service.py` at `a63bd4c` | 已观察 / Observed |
| 仓库 / Repository | 无状态 `/chat` 仍需要现有 streaming provider。 / Stateless `/chat` still needs the current streaming provider. | `app.py`, `providers.py` at `a63bd4c` | 已观察 / Observed |
| 外部 / External | Agents SDK Python 包名为 `openai-agents`，基础循环使用 `Agent`、`Runner` 和 `function_tool`。 / The Python package is `openai-agents`; its basic loop uses `Agent`, `Runner`, and `function_tool`. | Official OpenAI Agents SDK Quickstart, accessed 2026-11-05 | 已验证 / Verified |
| 外部 / External | 非 OpenAI 模型应使用语言 SDK 的 provider/adapter surface。 / Non-OpenAI models should use the language SDK provider/adapter surface. | Official OpenAI Models and Providers guide, accessed 2026-11-05 | 已验证 / Verified |

## 方案对比 / Solution Comparison

| 方案 / Option | 优点 / Advantages | 代价 / Trade-offs | 结论 / Decision |
|---|---|---|---|
| A. SDK Runner + function tools + Chat Completions adapter | 真正由 SDK 管理 ReAct；DeepSeek 兼容；保留现有边界。 / Real SDK-managed ReAct; DeepSeek compatible; preserves boundaries. | 需要适配 SDK 异常和工具上下文。 / Requires error and context adapters. | 采用 / Selected |
| B. 继续手写循环，只用 OpenAI SDK 发请求 | 改动小。 / Small delta. | 不满足“Agents SDK 构建 ReAct”。 / Does not satisfy the requested SDK-managed loop. | 拒绝 / Rejected |
| C. SDK Sessions + Responses API 全量迁移 | SDK 能力最完整。 / Richest SDK surface. | DeepSeek 和现有 SQLite/SSE 不兼容，范围过大。 / Incompatible with DeepSeek and existing SQLite/SSE; excessive scope. | 拒绝 / Rejected |

## 目标架构 / Target Architecture

```text
ConversationService
  -> RecommendationAgent.generate(history)
       -> Runner.run(Agent, history, context=RecommendationRunContext)
            -> search_products -> ProductSearchTool -> mall-service
            -> submit_product_recommendation -> validated RecommendationResult
       -> RecommendationResult
  -> SQLite + existing SSE events
```

## 模块职责 / Module Responsibilities

- `recommendation_agent.py`: SDK Agent 构造、运行上下文、工具安全规则、SDK 结果到领域结果的转换；不得负责 HTTP 或数据库。 / SDK agent construction, run context, tool policy, and result conversion; no HTTP or database ownership.
- `app.py`: 从 `LLMConfig` 构造 SDK-backed recommendation agent，同时保留 legacy streaming provider；不得改变路由协议。 / Build the SDK-backed recommendation agent from `LLMConfig` while retaining the legacy streaming provider; no route contract changes.
- `providers.py`: 保留无状态 streaming provider；移除或停止生产使用手写 tool-completion API。 / Retain the stateless streaming provider; remove or stop production use of hand-written tool-completion APIs.
- `test_recommendation_agent.py`: 用 fake Runner/model boundary 验证领域规则，不访问网络。 / Verify domain rules through a fake Runner/model boundary without network access.

## 详细功能 Plan / Detailed Functional Plans

### PLAN-001 SDK 模型与应用装配 / SDK Model and App Wiring

- 范围 / Scope: 添加 `openai-agents` 依赖；从 `LLMConfig` 构造 `AsyncOpenAI`、Chat Completions model 和 SDK-backed agent；禁用 tracing。 / Add the dependency and construct the configured SDK model and agent with tracing disabled.
- 非目标 / Non-goal: 不改造 `/chat` streaming。 / Do not migrate `/chat` streaming.
- 验收 / Acceptance: AC-006, AC-007.
- Per-plan 验证 / Verification: 配置装配单元测试、应用测试。 / Configuration wiring unit tests and app tests.
- 风险 / Risk: SDK API 版本漂移；通过版本上限和安装后 API 检查控制。 / SDK API drift, controlled by a version ceiling and post-install API inspection.
- 回滚 / Rollback: 移除依赖与 SDK factory，恢复现有 `RecommendationAgent(provider, tool)` 装配。 / Remove dependency/factory and restore current wiring.

### PLAN-002 SDK ReAct 工具循环 / SDK ReAct Tool Loop

- 范围 / Scope: 将搜索、提交、grounding、次数限制及停止语义实现为 SDK function tools 和 run context。 / Implement search, submit, grounding, search limits, and stop semantics as SDK function tools and run context.
- 非目标 / Non-goal: 不增加新工具或多 Agent。 / No new tools or multi-agent setup.
- 验收 / Acceptance: AC-001 through AC-005.
- Per-plan 验证 / Verification: 聚焦 agent 单元测试；既有 conversation tests。 / Focused agent unit tests and existing conversation tests.
- 风险 / Risk: DeepSeek 对严格 JSON Schema 或动态工具启用的支持差异；使用 Chat Completions function tools、`auto` tool choice 和服务端校验。 / DeepSeek schema/dynamic-tool compatibility; use Chat Completions function tools, auto tool choice, and server validation.
- 回滚 / Rollback: 恢复 `a63bd4c` 的手写循环。 / Restore the hand-written loop from `a63bd4c`.

### PLAN-003 兼容验证与文档 / Compatibility Verification and Documentation

- 范围 / Scope: 将旧 tool-completion parser 明确保留为无状态 `/chat` 的兼容实现（不再由持久化 ReAct 生产路径调用），更新 README 架构说明，运行完整 agent-service 测试。 / Retain the legacy tool-completion parser explicitly as a compatibility implementation for stateless `/chat` (it is no longer called by the persisted ReAct production path), update README architecture notes, and run the full agent-service suite.
- 验收 / Acceptance: AC-007.
- Final acceptance: 使用本地 DeepSeek 配置完成一次真实“你有哪些商品？”流程，验证 SSE recommendations 与 SQLite 持久化。 / Run one real local DeepSeek-backed “你有哪些商品？” flow and verify SSE recommendations plus SQLite persistence.
- 回滚 / Rollback: 保留外部协议，因此可仅回滚 agent-service 代码和依赖。 / External protocols are unchanged, so only agent-service code and dependency need rollback.

## Task Contract / 任务契约

- `runtime_routing`: Codex，用户已授权当前会话单 Agent 降级执行；独立 Planner/Coder 固定模型不可用。 / Codex, with user-authorized single-agent degraded execution because pinned independent Planner/Coder models are unavailable.
- `dependency_floor`: `openai-agents>=0.24,<1`；`ToolExecutionConfig` 和当前 Chat Completions adapter 已在 0.24.0 验证。 / `openai-agents>=0.24,<1`; `ToolExecutionConfig` and the current Chat Completions adapter are verified on 0.24.0.
- `allowed_paths`: `agent-service/pyproject.toml`, `agent-service/src/agent_service/{app.py,recommendation_agent.py,providers.py}`, `agent-service/tests/**`, `README.md`, 本规格目录。 / The listed paths only.
- `allowed_behavior_changes`: 仅 ReAct 内部编排与 SDK 错误适配。 / Internal ReAct orchestration and SDK error adaptation only.
- `invariants`: INV-001 外部 API/SSE 不变；INV-002 商品 ID 必须 grounded；INV-003 密钥不得泄露；INV-004 SQLite Schema 不变。 / External API/SSE unchanged, grounded product IDs, no secret leakage, unchanged SQLite schema.
- `requires_approval`: 修改 Web/Mall、HTTP 契约、数据库 Schema、YAML Schema 或增加其它依赖。 / Changes to Web/Mall, HTTP contracts, DB schema, YAML schema, or other dependencies.
- `verification_layers`: L1/L2/L3 必需；L4 需要本地契约测试；L5 仅密钥/错误泄露检查；L6 不适用。 / L1/L2/L3 required, L4 local contract checks, L5 secret/error leakage only, L6 not applicable.
- `change_budget`: 8 个实现/测试文件以内，生产代码约 250 行以内，测试约 500 行以内。 / At most 8 implementation/test files, about 250 production lines and 500 test lines.
- `time_budget`: 每个 Plan 与 Task 使用规划 40% / 编码 5% / 验证 55%。 / Each Plan and Task uses 40% planning, 5% coding, 55% verification.
- `stop_conditions`: SDK 无法通过 Chat Completions 适配 DeepSeek；需要改变公开协议；真实 SDK API 与官方/已安装版本冲突且无法局部适配。 / Stop if the SDK cannot support DeepSeek through the Chat Completions adapter, a public contract must change, or installed SDK APIs cannot be locally reconciled.

## 测试与验证策略 / Testing and Verification Strategy

- Per-plan: `cd agent-service && pytest tests/test_recommendation_agent.py -q`; `pytest tests/test_app.py -q`; `pytest tests/test_conversation_service.py -q`.
- Engineering: `cd agent-service && python -m compileall -q src tests`.
- Final acceptance: `cd agent-service && pytest -q`, then one authorized local real-provider request against the configured DeepSeek endpoint.
- 每项测试只映射一个主要风险；不重复运行无变化的绿色检查。 / Each test maps to one primary risk; unchanged green checks are not repeated.

## 风险与权衡 / Risks and Trade-Offs

- SDK 可能对第三方 Chat Completions endpoint 发送其不支持的字段。缓解：使用 generic model settings，禁止 Responses-only 功能，并通过真实 DeepSeek 验收。 / The SDK may send unsupported fields to third-party endpoints; mitigate with generic settings, no Responses-only features, and real DeepSeek acceptance.
- SDK tracing 默认可能调用 OpenAI。缓解：在 run config 显式关闭 tracing。 / SDK tracing may call OpenAI by default; explicitly disable it in run configuration.
- 工具异常默认可能被转成模型可见文本。缓解：为策略违规抛出领域异常并在 runner boundary 规范化。 / Tool exceptions may become model-visible text; raise domain errors and normalize them at the runner boundary.

## 资源分配与变更预警 / Effort Allocation and Change Warnings

| 层级 / Level | 预计总投入 / Estimate | 规划 / Planning | 编码 / Coding | 验证 / Verification |
|---|---:|---:|---:|---:|
| 整体 / Overall | 100% | 40% | 5% | 55% |
| PLAN-001 | 20% | 40% | 5% | 55% |
| PLAN-002 | 60% | 40% | 5% | 55% |
| PLAN-003 | 20% | 40% | 5% | 55% |

超出文件或行数预算时先检查范围漂移；不得为了比例增加无价值代码或测试。 / If file or line budgets are exceeded, inspect scope drift first; do not add low-value code or tests to satisfy ratios.

## 最终决策摘要 / Final Decision Summary

- 采用 SDK Runner + function tools + Chat Completions adapter。 / Use SDK Runner, function tools, and the Chat Completions adapter.
- 保留现有 SQLite 作为跨请求会话历史来源。 / Keep SQLite as the cross-request conversation-history source.
- 保留 legacy streaming provider 仅服务无状态 `/chat`。 / Keep the legacy streaming provider only for stateless `/chat`.
- 不引入 Sessions、handoffs、MCP 或 Responses-only 能力。 / Do not add Sessions, handoffs, MCP, or Responses-only capabilities.
