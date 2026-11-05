# Prompt File Management Specification / 提示词文件管理规格

**Status:** approved / 已批准  
**Date:** 2026-11-05  

## Goal / 目标

Move agent prompts from inline Python/YAML text into local UTF-8 Markdown files, referenced by YAML paths and reloaded on every request. / 将 Agent 提示词从 Python/YAML 内联文本迁移到本地 UTF-8 Markdown 文件，由 YAML 配置路径，并在每次请求时重新读取。

## Scope / 范围

- Add `agent-service/prompts/system.md` and `agent-service/prompts/recommendation-policy.md`.
- Add `prompt_loader.load_prompt(path)` with existence, UTF-8, non-empty, and 1 MiB checks.
- Add `llm.prompt_file` and `llm.recommendation_policy_prompt_file`; remove the required inline `system_prompt` field.
- Load the general prompt when opening a persisted turn and the recommendation policy when generating a recommendation.
- Keep prompts uncached; a later request observes file edits without restarting the service.
- Preserve secret-safe error mapping and bilingual documentation.

## Non-goals / 非目标

No web editor, database prompt registry, prompt version UI, multi-tenant prompts, or in-process background watcher.

## Acceptance / 验收

Changing either Markdown file affects the next request without restarting Agent service. Missing, empty, invalid UTF-8, or oversized files fail safely and do not call the model.

