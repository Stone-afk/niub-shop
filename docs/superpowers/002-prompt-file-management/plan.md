# Prompt File Management Implementation Plan

> **For agentic workers:** Execute task-by-task with test-first development and record verification in the SDD ledger.

**Goal:** Load bilingual agent prompts from local Markdown files referenced by YAML and re-read them for every request.

**Architecture:** A shared `prompt_loader.load_prompt` validates and returns uncached text. The application resolves prompt paths relative to the YAML file; conversation turns load the general prompt, while each recommendation generation loads the policy prompt.

**Tech Stack:** Python 3.11, FastAPI, Pydantic, PyYAML, OpenAI Agents SDK, pytest.

**Spec:** `docs/superpowers/002-prompt-file-management/spec.md`

## Global Constraints

- Prompt files are UTF-8 Markdown and at most 1 MiB.
- Prompt content is never cached between requests.
- API keys and full prompt contents must not appear in errors.
- Existing persisted conversation and recommendation behavior remains intact.

## Review Focus

- Relative prompt paths resolve from the YAML file directory; test in `test_config.py`.
- Empty, invalid UTF-8, and >1 MiB files are rejected; test in `test_prompt_loader.py`.
- Prompt edits are visible on the next request; test loader and service boundaries.
- Recommendation policy is loaded per generation rather than at app startup; test in `test_recommendation_agent.py`.
- Prompt-load failures do not reach the model; test app/service error mapping.

### Task 1: Prompt loader and configuration

**Files:** Create `agent-service/src/agent_service/prompt_loader.py`; modify `agent-service/src/agent_service/config.py`; test `agent-service/tests/test_prompt_loader.py` and `agent-service/tests/test_config.py`.

- Write failing tests for validation and YAML-relative path fields.
- Implement `PromptLoadError`, `load_prompt`, and validated prompt path fields on `LLMConfig`.
- Run focused tests, then the config suite.

### Task 2: Prompt files and Agent integration

**Files:** Create `agent-service/prompts/system.md` and `agent-service/prompts/recommendation-policy.md`; modify `recommendation_agent.py`, `conversation_service.py`, and `app.py`; test affected suites.

- Write failing tests proving per-request reload and policy injection.
- Replace the hard-coded recommendation policy and startup-loaded system text with paths and `load_prompt` calls.
- Preserve existing factory seams and safe error mapping.

### Task 3: Documentation and full verification

**Files:** Modify `agent-service/config/llm.example.yaml`, `README.md`, and relevant tests.

- Document Markdown paths, reload semantics, and bilingual editing rules.
- Run the full agent and mall test suites, compile checks, and `git diff --check`.

