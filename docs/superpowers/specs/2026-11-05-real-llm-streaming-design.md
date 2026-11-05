# 真实大模型流式接入设计 / Real LLM Streaming Integration Design

## 目标 / Intent

将 `agent-service` 的模拟回复替换为真实的 OpenAI-compatible Chat Completions 流式调用，同时保持浏览器现有 `{ messages }` 请求和 `delta` / `done` SSE 响应协议不变。

Replace the simulated reply with a real OpenAI-compatible streaming Chat Completions call while preserving the browser-facing `{ messages }` request and `delta` / `done` SSE response contract.

## 配置 / Configuration

提交不含真实密钥的示例文件 `agent-service/config/llm.example.yaml`：

```yaml
llm:
  provider: openai-compatible
  base_url: https://api.deepseek.com
  api_key: replace-with-your-api-key
  model: deepseek-flash
  timeout_seconds: 60
  system_prompt: 你是牛布商城的智能购物助手。
```

- 本地使用 `agent-service/config/llm.local.yaml`；`agent-service/config/.gitignore` 忽略 `*.local.yaml`。
- 通过 `AGENT_CONFIG_FILE` 传入配置路径，不把密钥放入命令行参数、代码、日志或测试夹具。
- 使用 `PyYAML` 的 `yaml.safe_load` 解析，并对缺失字段、字段类型、URL 和超时值进行明确校验。
- 未设置 `AGENT_CONFIG_FILE` 时服务仍可启动并通过健康检查，但 `/chat` 返回稳定的 503 `model_not_configured`；显式提供但无效的配置在应用创建时快速失败。

- Local development uses `agent-service/config/llm.local.yaml`; `*.local.yaml` is ignored inside the config directory.
- `AGENT_CONFIG_FILE` selects the file. Secrets never belong in command arguments, source code, logs, or test fixtures.
- Parsing uses `yaml.safe_load`, followed by explicit validation of required fields, types, the URL, and timeout.
- Without `AGENT_CONFIG_FILE`, health checks remain available while `/chat` returns a stable 503 `model_not_configured`. An explicitly selected invalid config fails fast during app creation.

## 数据流 / Data Flow

1. `agent-service` 将配置中的 `system_prompt` 与浏览器提交的完整 `user` / `assistant` 历史发送到 `{base_url}/chat/completions`。
2. 请求使用 Bearer 认证、配置的 `model`、`stream: true` 和 JSON 请求体。
3. 上游 `data: {...}` 中 `choices[0].delta.content` 的非空文本转换为下游 `event: delta`。
4. 上游 `data: [DONE]` 转换为下游 `event: done`；提前 EOF、无效 JSON 或无效数据结构视为上游协议错误。
5. 在下游响应开始前建立上游连接，使认证失败、超时和非 2xx 响应可以映射为稳定的 502 JSON 错误；客户端断开时关闭上游响应。

1. `agent-service` sends the configured `system_prompt` followed by the complete browser-supplied `user` / `assistant` history to `{base_url}/chat/completions`.
2. The request uses Bearer authentication, the configured `model`, `stream: true`, and a JSON body.
3. Non-empty `choices[0].delta.content` strings from upstream `data: {...}` records become downstream `event: delta` events.
4. Upstream `data: [DONE]` becomes downstream `event: done`; premature EOF, invalid JSON, or invalid structures are protocol errors.
5. The upstream connection opens before the downstream response starts, so authentication errors, timeouts, and non-2xx responses map to stable 502 JSON errors. Client cancellation closes upstream resources.

## 边界 / Boundaries

- 直接使用现有 `httpx`，不引入 OpenAI SDK，不绑定 DeepSeek SDK。
- 只实现文本对话；暂不暴露推理内容、工具调用、usage、图片、重试或持久化。
- API key 永远不进入错误响应。
- 单元测试使用受控 HTTP transport，不发起真实计费请求；真实连通性由用户使用新生成的 key 本地验证。

- The implementation uses the existing `httpx` dependency directly and adds no OpenAI or vendor SDK.
- Only text chat is supported; reasoning content, tools, usage, images, retries, and persistence remain out of scope.
- API keys never appear in error responses.
- Unit tests use controlled HTTP transports. The end-to-end smoke test uses a local fake OpenAI-compatible SSE server, so automated verification never makes billable requests.

## 热加载 / Hot Reload

- Python 文件继续使用 Uvicorn `--reload`。
- 依赖改为 `uvicorn[standard]` 以获得 `watchfiles`；开发命令加入 `--reload-include '*.yaml'`，使 `llm.local.yaml` 变化后重启并重新加载配置。

- Uvicorn `--reload` continues to watch Python files.
- `uvicorn[standard]` supplies `watchfiles`; `--reload-include '*.yaml'` restarts the development process when `llm.local.yaml` changes, causing the YAML file to be read again.
- This is process restart-based hot reload, not live mutation of configuration inside a running process.

## 方案选择 / Approaches

- 已选择：YAML 配置 + `PyYAML` + 直接 `httpx` 调用。配置清晰、无厂商 SDK 绑定，并复用现有依赖。
- 未选择：TOML。无需解析依赖，但用户明确偏好 YAML。
- 未选择：OpenAI Python SDK。SSE 解析更省代码，但增加 SDK 耦合和依赖。
