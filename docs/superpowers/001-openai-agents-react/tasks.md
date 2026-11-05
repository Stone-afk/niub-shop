# OpenAI Agents SDK ReAct 任务清单 / OpenAI Agents SDK ReAct Task List

**规格文档 / Spec**: `docs/superpowers/001-openai-agents-react/spec.md`  
**技术方案 / Plan**: `docs/superpowers/001-openai-agents-react/plan.md`  
**任务清单 / Tasks**: `docs/superpowers/001-openai-agents-react/tasks.md`  
**基线版本 / Baseline Version**: `PLAN-v1`  
**日期 / Date**: 2026-11-05  
**状态 / Status**: 已授权实施 / Authorized for Implementation

## 任务格式 / Task Format

`[ ] T### [P] 中文动作 / English action`；`[P]` 表示依赖满足后可以并行，不自动授权额外 Agent。所有行为实现任务依赖更早的失败测试任务。 / `[P]` means parallelizable after dependencies are met; it does not authorize extra agents. Every behavior implementation task depends on an earlier failing-test task.

## 依赖总览 / Dependency Overview

PLAN-001 建立 SDK 依赖和装配边界；PLAN-002 在该边界内实现 ReAct；PLAN-003 清理旧 surface 并执行总体验收。顺序为 T001 → T002 → T003 → T004 → T005 → T006 → T007。 / PLAN-001 establishes the SDK dependency and wiring boundary; PLAN-002 implements ReAct inside it; PLAN-003 removes obsolete surfaces and performs final acceptance. Execution order is T001 through T007.

## PLAN-001：SDK 模型与应用装配 / SDK Model and App Wiring

### Tests First / 测试先行

- [ ] T001 修改 `agent-service/tests/test_app.py`，增加配置装配测试，断言生产创建路径接收 base URL、模型、超时和密钥但不泄露密钥，并保留无配置行为。 / Update app tests for configured SDK wiring and unchanged unconfigured behavior.
  - Plan: PLAN-001.
  - Scope: `agent-service/tests/test_app.py`.
  - Dependencies: none.
  - Acceptance: AC-006, AC-007；测试在 SDK factory 尚未存在时因接口缺失而 RED。 / Tests fail RED because the SDK factory does not yet exist.
  - Verification: `cd agent-service && pytest tests/test_app.py -q`.
  - Budget: 1 文件，约 80 测试行；40/5/55。 / 1 file, about 80 test lines; 40/5/55.
  - Done when: 新测试以预期原因失败且既有测试结果已记录。 / New tests fail for the intended reason and the existing baseline is recorded.

### Implementation / 实现

- [ ] T002 修改 `agent-service/pyproject.toml` 和 `agent-service/src/agent_service/app.py`，添加受限版本的 `openai-agents` 并从 `LLMConfig` 构造 SDK-backed `RecommendationAgent`，同时保留 legacy `/chat` provider。 / Add the bounded dependency and construct the SDK-backed agent from `LLMConfig` while retaining the legacy `/chat` provider.
  - Plan: PLAN-001.
  - Scope: `agent-service/pyproject.toml`, `agent-service/src/agent_service/app.py`.
  - Dependencies: T001.
  - Acceptance: AC-006, AC-007.
  - Verification: `cd agent-service && pytest tests/test_app.py -q`.
  - Budget: 2 文件，约 60 生产行；40/5/55。 / 2 files, about 60 production lines; 40/5/55.
  - Done when: 装配测试 GREEN，`/chat` 测试无回归。 / Wiring tests are GREEN and `/chat` tests do not regress.

## PLAN-002：SDK ReAct 工具循环 / SDK ReAct Tool Loop

### Tests First / 测试先行

- [ ] T003 修改 `agent-service/tests/test_recommendation_agent.py`，以 fake SDK runner/tool invocation 覆盖普通回复、搜索提交、三次上限、非空后禁搜、grounding、非法参数、Mall 错误和 SDK 错误映射。 / Replace orchestration tests with fake SDK-runner/tool-invocation coverage for all approved behaviors.
  - Plan: PLAN-002.
  - Scope: `agent-service/tests/test_recommendation_agent.py`.
  - Dependencies: T002.
  - Acceptance: AC-001 through AC-005.
  - Verification: `cd agent-service && pytest tests/test_recommendation_agent.py -q`.
  - Budget: 1 文件，约 350 测试行；40/5/55。 / 1 file, about 350 test lines; 40/5/55.
  - Done when: 新测试因仍使用手写 provider 循环而 RED，失败原因不是环境损坏。 / Tests are RED because the old hand-written loop remains, not because of environment damage.

### Implementation / 实现

- [ ] T004 修改 `agent-service/src/agent_service/recommendation_agent.py`，实现 `RecommendationRunContext`、SDK function tools、动态工具启用、stop-at-submit 和 `Runner.run` 结果转换。 / Implement the run context, SDK function tools, dynamic enablement, stop-at-submit, and result conversion.
  - Plan: PLAN-002.
  - Scope: `agent-service/src/agent_service/recommendation_agent.py`.
  - Dependencies: T003.
  - Acceptance: AC-001 through AC-005.
  - Verification: `cd agent-service && pytest tests/test_recommendation_agent.py -q`.
  - Budget: 1 文件，约 190 生产行；40/5/55。 / 1 file, about 190 production lines; 40/5/55.
  - Done when: 所有聚焦 agent 测试 GREEN，且没有真实网络访问。 / All focused agent tests are GREEN with no real network access.

- [ ] T005 [P] 修改 `agent-service/tests/test_conversation_service.py` 和必要的 `agent-service/tests/test_app.py` 断言领域异常仍持久化为原有失败码，SSE recommendations 契约不变。 / Update conversation and app contract tests for unchanged persistence error codes and SSE recommendations.
  - Plan: PLAN-002.
  - Scope: `agent-service/tests/test_conversation_service.py`, `agent-service/tests/test_app.py`.
  - Dependencies: T004.
  - Acceptance: AC-005, AC-007.
  - Verification: `cd agent-service && pytest tests/test_conversation_service.py tests/test_app.py -q`.
  - Budget: 2 文件，约 70 测试行；40/5/55。 / 2 files, about 70 test lines; 40/5/55.
  - Done when: 公开错误码、失败状态和 SSE 事件断言全部 GREEN。 / Public error-code, failed-state, and SSE-event assertions are GREEN.

## PLAN-003：兼容验证与文档 / Compatibility Verification and Documentation

### Implementation / 实现

- [ ] T006 在 `agent-service/src/agent_service/providers.py` 保留旧 tool-completion surface 作为无状态 `/chat` 兼容能力（持久化 ReAct 不再调用），并更新 `README.md` 的 Agent SDK 架构说明；不得删除 `/chat` streaming 能力。 / Retain the legacy tool-completion surface in `providers.py` for stateless `/chat` compatibility (persisted ReAct no longer calls it) and update README architecture notes without removing `/chat` streaming.
  - Plan: PLAN-003.
  - Scope: `agent-service/src/agent_service/providers.py`, `README.md`.
  - Dependencies: T005.
  - Acceptance: AC-007.
  - Verification: `cd agent-service && python -m compileall -q src tests`.
  - Budget: 2 文件，净删除优先，文档约 30 行；40/5/55。 / 2 files, prefer net deletion, about 30 documentation lines; 40/5/55.
  - Done when: 持久化推荐生产路径无旧 tool-completion API 引用，legacy streaming provider 保持可编译且 README 说明两条路径。 / The persisted recommendation path has no old tool-completion API reference, the legacy streaming provider compiles, and README documents both paths.

### Final Acceptance / 总体验收

- [ ] T007 执行 agent-service 全量测试，并在本地运行配置的 DeepSeek + mall-service 完成一次真实商品查询，核对 SSE recommendations 与 SQLite 记录。 / Run the full agent-service suite and one local configured DeepSeek + mall-service product query, verifying SSE recommendations and SQLite persistence.
  - Plan: PLAN-003.
  - Scope: 只读验证；不修改生产数据。 / Read-only verification; no production data mutation.
  - Dependencies: T006.
  - Acceptance: AC-001 through AC-007.
  - Verification: `cd agent-service && pytest -q`; local HTTP request and SQLite readback using the development database.
  - Budget: 0 代码行；验证投入 100%。 / 0 code lines; 100% verification effort.
  - Done when: 全量测试通过；真实流程返回至少一个 grounded ID 并以 completed 状态持久化，或明确记录外部服务阻塞。 / Full tests pass and the real flow returns at least one grounded ID persisted as completed, or an external-service blocker is documented.

## Final Validation Checklist / 最终验证清单

- [ ] 每个行为实现均有更早的 RED 测试。 / Every behavior implementation has an earlier RED test.
- [ ] 依赖顺序与授权路径没有漂移。 / Dependency order and authorized paths have not drifted.
- [ ] HTTP、SSE、SQLite、Mall 和 Web 契约保持不变。 / HTTP, SSE, SQLite, Mall, and Web contracts remain unchanged.
- [ ] API key 未进入 Git Diff、日志或错误消息。 / The API key does not appear in Git diff, logs, or errors.
- [ ] 聚焦测试、compileall 和全量 agent-service 测试完成。 / Focused tests, compileall, and the full agent-service suite are complete.
- [ ] 一次去重的真实 DeepSeek 总体验收完成或有明确阻塞证据。 / One deduplicated real DeepSeek acceptance run is complete or has explicit blocker evidence.
