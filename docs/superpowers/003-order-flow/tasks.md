# 订单流程任务清单 / Order Flow Task Inventory

- Baseline: `order-flow-v1`
- Date: 2026-11-06
- Status: `APPROVED — implementation plan active`
- Related: [spec.md](./spec.md), [plan.md](./plan.md)
- Default-deny: 未列出的生产文件、公开接口、依赖和行为变化均不在授权范围内。/ Any unlisted production path, public API, dependency, or behavior change is out of scope.

## P1 — Mall order mock contract

| ID | Action / 动作 | Scope | Depends | Acceptance | Done when |
|---|---|---|---|---|---|
| T101 | Add order snapshot/request/response models and status rules | `mall-service/src/mall_service/models.py` | — | AC-05, AC-06 | strict validation covers positive quantity, money, status |
| T102 | Add idempotent mock order store and `POST /orders` | `mall-service/src/mall_service/app.py`, new mall module if needed | T101 | AC-01, AC-04, AC-07 | same key returns same order; invalid/out-of-stock rejected |
| T103 | Add preview and Mock pay endpoints | `mall-service/src/mall_service/app.py` | T101,T102 | AC-05, AC-06 | pending→paid and repeated pay are deterministic |
| T104 | Write focused mall contract tests first, then implementation | `mall-service/tests/` | T101–T103 | FA-01 | tests fail before behavior and pass after implementation |

## P2 — Agent order tool and persistence

| ID | Action / 动作 | Scope | Depends | Acceptance | Done when |
|---|---|---|---|---|---|
| T201 | Add strict MallClient order schemas and calls | `agent-service/src/agent_service/mall_client.py` | T103 | AC-07 | malformed/unavailable mall responses map to typed errors |
| T202 | Add `create_order_draft` Agents SDK tool and confirmation instructions | `recommendation_agent.py`, prompt/config paths | T201 | AC-01, AC-02 | tool is callable only with explicit items; no auto code-side intent gate |
| T203 | Add `turn_orders` migration/read/write and deterministic per-intent idempotency metadata | `conversations.py`, `conversation_service.py`, `recommendation_agent.py` | T201 | AC-04, AC-08 | same `turn_id + intent_hash` reuses key; distinct intents can produce distinct orders |
| T204 | Emit and test SSE `order` event | `agent-service/src/agent_service/app.py`, agent tests | T202,T203 | AC-03, AC-07 | event is validated and absent on failures |

## P3 — Web preview and navigation

| ID | Action / 动作 | Scope | Depends | Acceptance | Done when |
|---|---|---|---|---|---|
| T301 | Extend SSE parser/client types for order events | `web/lib/chat.ts`, `web/lib/conversations.ts`, `web/lib/types.ts` | T204 | AC-03 | malformed order event fails safely |
| T302 | Add order BFF proxy routes | `web/app/api/orders/[orderId]/route.ts`, `pay/route.ts` | T103 | AC-05, AC-06 | browser never needs mall URL |
| T303 | Add order preview page and Mock pay UX | `web/app/orders/[orderId]/preview/*` | T302 | AC-05, AC-06 | renders loading/error/pending/paid states |
| T304 | Navigate from chat and show historical order links | `web/app/chat/page.tsx` and styles | T301,T303 | AC-01, AC-08 | redirect happens after successful stream completion |

## P4 — Final acceptance

| ID | Action / 动作 | Scope | Depends | Acceptance | Done when |
|---|---|---|---|---|---|
| T401 | Run focused unit/contract checks for P1–P3 | existing test paths | T104,T204,T301–T304 | FA-01, FA-07 | all focused checks have bound command output |
| T402 | Run one local three-service multi-turn E2E | local services only | T401 | FA-02–FA-06 | explicit purchase, retry, preview, mock pay observed |
| T403 | Fresh-context audit and human review packet | planning + diff + evidence | T402 | overall approval | unresolved P0/P1 = 0 |

## Stop conditions / 停止条件

- 需要真实支付、登录、地址、库存预占或新外部依赖时暂停并新增审批项。
- 订单幂等无法在现有 mock 边界内保证时暂停，不以“先创建再修复”替代设计。
- 任何任务需要修改未列出的公开契约或跨服务数据所有权时，回到 Planner 更新本基线。
