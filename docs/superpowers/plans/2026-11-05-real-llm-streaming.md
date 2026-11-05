# Real LLM Streaming Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the simulated Agent reply with a real, configurable OpenAI-compatible streaming Chat Completions call loaded from a local YAML file.

**Architecture:** A focused YAML loader produces a secret-safe immutable configuration. An `httpx` streaming provider opens and validates the upstream response before FastAPI starts the downstream SSE response, then translates upstream content deltas and `[DONE]` into the existing browser protocol; the current Next.js and React streaming code remains unchanged.

**Tech Stack:** Python 3.11, FastAPI, httpx, PyYAML, Uvicorn with watchfiles, pytest, existing Next.js SSE client

**Spec:** `docs/superpowers/specs/2026-11-05-real-llm-streaming-design.md`

## Global Constraints

- Configuration example path is exactly `agent-service/config/llm.example.yaml`; local secrets use `llm.local.yaml` and must be ignored.
- Configuration is selected with `AGENT_CONFIG_FILE`; never log, serialize, or commit the API key.
- Use OpenAI-compatible `POST {base_url}/chat/completions`, Bearer auth, the configured model, full conversation history, and `stream: true`.
- Downstream success remains named SSE `delta` events followed by `done`; the Web request and response contracts do not change.
- Do not add the OpenAI SDK, retry behavior, tools, images, persistence, reasoning-content display, or usage display.
- Tests must use local controlled transports and fake servers; never call a billable external API.

## Review Focus

- A malformed or missing YAML field, invalid URL, invalid timeout, or unsupported provider must fail without exposing the API key; covered in Task 1.
- An upstream 401/429/500, timeout, network failure, or successful non-SSE response must map to a stable secret-free 502 before downstream streaming begins; covered in Tasks 2 and 3.
- Upstream SSE split across arbitrary byte chunks, role-only chunks, `null` content, malformed JSON, invalid structures, and EOF before `[DONE]` must not corrupt the downstream reply; covered in Task 2.
- Browser disconnect or downstream cancellation must close both the upstream response and its `httpx.AsyncClient`; covered in Task 2.
- Absence of `AGENT_CONFIG_FILE` must keep `/health` usable while `/chat` returns stable 503; covered in Task 3.

---

### Task 1: Secret-safe YAML configuration

**Files:**
- Create: `agent-service/src/agent_service/config.py`
- Create: `agent-service/tests/test_config.py`
- Create: `agent-service/config/llm.example.yaml`
- Create: `agent-service/config/.gitignore`
- Modify: `agent-service/pyproject.toml`

**Interfaces:**
- Produces: immutable `LLMConfig(provider, base_url, api_key, model, timeout_seconds, system_prompt)` with `api_key` excluded from `repr`; `load_llm_config(path: str | Path) -> LLMConfig`; `ConfigurationError` with secret-free messages.
- Consumes: YAML root mapping with a required `llm` mapping and exactly the documented keys.

- [ ] **Step 1: Write failing configuration tests**

Add tests for the complete example shape, trimming the base URL slash, missing file, malformed YAML, non-mapping roots, missing/empty/wrong-type fields, unsupported provider, non-HTTP(S) URL, non-positive timeout, unknown keys, and proof that neither `repr(config)` nor any error contains the supplied API key.

- [ ] **Step 2: Run tests and verify RED**

Run: `cd agent-service && .venv/bin/pytest tests/test_config.py -v`

Expected: FAIL because `agent_service.config` does not exist.

- [ ] **Step 3: Implement the loader and dependency metadata**

Parse only with `yaml.safe_load`, validate explicitly without echoing field values, add `PyYAML>=6,<7`, change Uvicorn to `uvicorn[standard]>=0.30,<1`, commit the placeholder-only YAML example, and ignore `*.local.yaml` inside `agent-service/config/`.

- [ ] **Step 4: Install the updated Agent dependencies**

Run: `agent-service/.venv/bin/python -m pip install -e './agent-service[test]'`

Expected: editable install succeeds with PyYAML and watchfiles available.

- [ ] **Step 5: Run configuration tests and verify GREEN**

Run: `cd agent-service && .venv/bin/pytest tests/test_config.py -v`

Expected: all configuration tests pass.

- [ ] **Step 6: Commit**

```bash
git add agent-service/src/agent_service/config.py agent-service/tests/test_config.py agent-service/config/llm.example.yaml agent-service/config/.gitignore agent-service/pyproject.toml
git commit -m "feat: load secret-safe LLM YAML config"
```

### Task 2: OpenAI-compatible upstream stream

**Files:**
- Modify: `agent-service/src/agent_service/providers.py`
- Create: `agent-service/tests/test_streaming_provider.py`

**Interfaces:**
- Consumes: `LLMConfig` from Task 1 and ordered `ModelMessage` records with `system | user | assistant` roles.
- Produces: `StreamingModelProvider.open_stream(messages) -> ModelStream`; `OpenAICompatibleProvider`; `ModelUpstreamError`; `ModelProtocolError`. `ModelStream` is an async iterable of visible content strings and exposes `aclose()`.

- [ ] **Step 1: Write failing provider tests**

Using `httpx.MockTransport` and a controlled `AsyncByteStream`, assert exact URL, Bearer header, model/history/`stream: true` body, delta extraction across byte boundaries, ignored role-only and null-content chunks, `[DONE]` termination, premature EOF, malformed JSON/shape, non-SSE success, non-2xx statuses, connection errors, safe exception text, and resource closure after completion and cancellation.

- [ ] **Step 2: Run tests and verify RED**

Run: `cd agent-service && .venv/bin/pytest tests/test_streaming_provider.py -v`

Expected: FAIL because the streaming provider interfaces do not exist.

- [ ] **Step 3: Implement upstream connection and parsing**

Open the response with `AsyncClient.send(..., stream=True)` so status and content type are known before returning `ModelStream`. Parse data-only SSE using `aiter_lines()`, yield only string `choices[0].delta.content`, require `[DONE]`, and close the response plus client in every success, failure, or cancellation path.

- [ ] **Step 4: Run provider tests and verify GREEN**

Run: `cd agent-service && .venv/bin/pytest tests/test_streaming_provider.py -v`

Expected: all provider tests pass.

- [ ] **Step 5: Commit**

```bash
git add agent-service/src/agent_service/providers.py agent-service/tests/test_streaming_provider.py
git commit -m "feat: stream OpenAI-compatible model output"
```

### Task 3: FastAPI model integration and stable errors

**Files:**
- Modify: `agent-service/src/agent_service/app.py`
- Modify: `agent-service/tests/test_app.py`

**Interfaces:**
- Consumes: `AGENT_CONFIG_FILE`, Task 1 config loader, and Task 2 `StreamingModelProvider`.
- Produces: `/chat` that prepends the configured system message, opens upstream before returning, translates content to `event: delta`, emits `done` only after valid upstream `[DONE]`, returns 503 when unconfigured, and maps safe open-time provider failures to 502.

- [ ] **Step 1: Write failing application tests**

Replace simulated-reply tests with a recording fake provider. Assert complete ordered history plus system prompt, incremental downstream SSE, stream closure, no-config 503, injected/provider-from-YAML construction, and stable secret-free 502 mapping for open-time upstream failures. Retain request validation tests.

- [ ] **Step 2: Run tests and verify RED**

Run: `cd agent-service && .venv/bin/pytest tests/test_app.py -v`

Expected: FAIL because the app still produces simulated chunks.

- [ ] **Step 3: Implement application wiring**

Resolve an injected provider first, otherwise load `AGENT_CONFIG_FILE` when present, otherwise remain unconfigured. Make `/chat` async, open the upstream stream before returning `StreamingResponse`, serialize each model delta into the existing downstream format, terminate without `done` on midstream protocol failure, and always close the model stream.

- [ ] **Step 4: Run the full Agent suite and verify GREEN**

Run: `cd agent-service && .venv/bin/pytest -v`

Expected: all Agent tests pass.

- [ ] **Step 5: Commit**

```bash
git add agent-service/src/agent_service/app.py agent-service/tests/test_app.py
git commit -m "feat: connect chat API to streaming model"
```

### Task 4: Live-chain smoke test and bilingual operations docs

**Files:**
- Modify: `scripts/smoke_test.py`
- Modify: `tests/test_smoke.py`
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-11-05-real-llm-streaming-design.md`

**Interfaces:**
- Consumes: Tasks 1–3 public configuration and SSE contracts.
- Produces: a no-cost four-service smoke chain using an in-process fake OpenAI-compatible SSE server; documented YAML setup and Python/YAML hot reload commands.

- [ ] **Step 1: Write failing smoke-test coverage**

Extend the smoke test to start a local fake model endpoint, create a temporary YAML config, set `AGENT_CONFIG_FILE`, send `{ messages }` through Web `/api/chat`, parse downstream SSE, and assert fake model content reached the browser boundary. Add unit coverage that the fake endpoint receives full messages and Bearer auth without printing the secret.

- [ ] **Step 2: Run the smoke test and verify RED**

Run: `python3.11 -m unittest tests.test_smoke -v`

Expected: FAIL because the current smoke test expects the removed JSON chat contract and has no model endpoint.

- [ ] **Step 3: Implement the live-chain fixture and documentation**

Use only Python standard-library HTTP server/thread/tempfile helpers for the fake model. Update README installation, YAML copy/edit instructions, `AGENT_CONFIG_FILE`, current `{ messages }` curl example, secret rotation warning, and Uvicorn command with `--reload --reload-include '*.yaml'`.

- [ ] **Step 4: Run full verification**

Run:

```bash
agent-service/.venv/bin/pytest -q agent-service/tests
mall-service/.venv/bin/pytest -q mall-service/tests
cd web && npm test && npm run build && cd ..
python3.11 -m unittest discover -s tests -p 'test_smoke.py' -v
git diff --check
```

Expected: both Python suites, Web suite, production build, and live-chain smoke test pass; diff check exits 0.

- [ ] **Step 5: Commit**

```bash
git add scripts/smoke_test.py tests/test_smoke.py README.md docs/superpowers/specs/2026-11-05-real-llm-streaming-design.md docs/superpowers/plans/2026-11-05-real-llm-streaming.md
git commit -m "docs: describe configured model streaming"
```
