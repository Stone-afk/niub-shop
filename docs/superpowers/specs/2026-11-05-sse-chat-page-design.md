# SSE 多轮对话页设计 / SSE Multi-turn Chat Page Design

## 目标 / Intent

在 `web` 新增 `/chat` 页面，提供基础的 Agent 多轮对话体验。当前阶段不调用大模型；`agent-service` 通过 SSE 分块返回固定双语回复，用于验证完整流式链路和页面交互。

Add a `/chat` page to `web` with a basic multi-turn Agent experience. No model is called yet; `agent-service` streams a fixed bilingual reply over SSE so the full streaming path and UI behavior can be verified.

## 范围 / Scope

- `/chat` 在浏览器内维护本次会话的用户与 Agent 消息列表。
- 用户可以连续发送多条消息；每条 Agent 回复在同一个气泡中随 SSE 数据逐步追加。
- 输入为空或回复生成中时禁止发送；提供发送状态、流式错误提示和自动滚动。
- 首页现有聊天组件继续保留，但改为消费 SSE，并提供进入完整聊天页的入口。
- 不做会话持久化、身份认证、Markdown 渲染、停止生成、重试或真实模型调用。

## 接口与数据流 / API and Data Flow

```text
Browser POST /api/chat { messages: [{ role, content }] }
          │
          ▼
Next.js route handler (transparent streaming proxy)
          │
          ▼
agent-service POST /chat
          │
          └── text/event-stream
                event: delta  data: {"content":"..."}
                event: done   data: {}
```

- 现有 `/api/chat` 和 `agent-service /chat` 整体由 JSON 回复改为 `text/event-stream`，不提供旧格式兼容。
- 请求升级为 `{ "messages": [{ "role": "user" | "assistant", "content": string }] }`，每次发送当前完整会话历史。后端当前会校验并接收历史，但固定回复暂不根据上下文生成；未来接入大模型时无需再次修改协议。
- SSE 数据使用具名 `delta` 与 `done` 事件。固定回复拆成多个文本片段发送，便于真实呈现流式更新。
- Next.js 代理透传上游状态、`content-type` 和响应流；上游不可用时仍返回明确的 HTTP 错误响应。

## 页面结构 / Page Structure

- 顶部：返回商城的导航、页面标题和当前“固定回复演示”说明。
- 中部：可滚动消息区，含欢迎态、用户气泡、Agent 气泡和生成中状态。
- 底部：固定输入区，支持表单提交；采用中英双语文案并保持键盘与屏幕阅读器可用。
- 视觉延续商城现有的暖色、卡片式设计，同时让 `/chat` 成为独立、专注的对话工作区。

## 测试 / Testing

- Python：验证 Agent 接口返回 SSE、事件顺序与固定内容，并保留请求校验测试。
- Next.js API：验证请求体转发、流式响应透传和服务不可用错误。
- 页面：验证初始状态、连续多轮消息、分块追加、发送期间禁用以及错误状态。
- 首页：更新原 JSON 聊天测试以覆盖 SSE 消费。
- 最终运行两个 Python 服务测试、Web 全量测试与 Next.js 生产构建。

## 方案选择 / Approaches

- 已选择：将现有 `/api/chat` 整体升级为 SSE，并同步修改首页和测试。接口单一、未来接入模型时无需维护两套聊天协议，但这是一次有意的破坏性变更。
- 未选择：新增独立流式端点。兼容性更好，但会同时维护 JSON 与 SSE 两套行为。
- 未选择：只在 Next.js 内生成 SSE。实现更快，但绕过 `agent-service`，不符合服务拆分方向。
