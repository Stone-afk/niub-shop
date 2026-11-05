# Python Agent Skeleton Implementation Plan / Python Agent 最小骨架实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task by task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **给自动化实施者：** 必须使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans`，逐项执行本计划。所有步骤使用复选框（`- [ ]`）跟踪进度。

**Goal / 目标：** Create a runnable, vendor-neutral Python agent skeleton with no third-party dependencies. / 创建一个可运行、厂商无关且不依赖第三方库的 Python Agent 最小骨架。

**Architecture / 架构：** A `src/niub_agent` package defines a provider protocol, a delegating agent, a local echo provider, and a CLI. Standard-library `unittest` verifies behavior without network access. / `src/niub_agent` 包包含提供方协议、负责委托的 Agent、本地回显提供方和 CLI。使用标准库 `unittest` 在无网络环境下验证行为。

**Tech Stack / 技术栈：** Python 3.11+, standard library, `unittest`, `pyproject.toml` / Python 3.11 及以上版本、标准库、`unittest`、`pyproject.toml`

**Spec / 设计文档：** `docs/superpowers/specs/2026-11-05-python-agent-skeleton-design.md`

## Global Constraints / 全局约束

- Runtime and test code use only the Python standard library. / 运行时和测试代码仅使用 Python 标准库。
- Use a `src/` package layout and require Python 3.11 or newer. / 使用 `src/` 包布局，并要求 Python 3.11 或更高版本。
- Preserve `LICENSE` and ignore `.idea/`. / 保留 `LICENSE`，并忽略 `.idea/`。
- Project documentation is written in Chinese and English. / 项目文档使用中英双语。
- Code identifiers and commands remain in English. / 代码标识符和命令保持英文。

## Review Focus / 审查重点

- Empty prompts must still delegate predictably rather than crash. / 空提示词也必须得到可预测的委托结果，不能导致程序崩溃。
- Prompt words passed through the CLI must retain spaces when joined. / CLI 收到的多个提示词在拼接后必须保留空格。
- Provider implementations must be replaceable without inheritance. / 模型提供方实现必须无需继承即可替换。
- Module execution must return exit status zero on success. / 模块成功执行时必须返回零退出状态。
- Tests must run from a clean checkout without installing test dependencies. / 从干净检出的仓库中，无需安装测试依赖即可运行测试。

---

### Task 1: Agent core and tests / 任务 1：Agent 核心与测试

**Files / 文件：**

- Create / 创建：`pyproject.toml`
- Create / 创建：`src/niub_agent/__init__.py`
- Create / 创建：`src/niub_agent/agent.py`
- Create / 创建：`src/niub_agent/providers.py`
- Create / 创建：`tests/test_agent.py`

**Interfaces / 接口：**

- Consumes / 输入依赖：none / 无
- Produces / 对外提供：`ModelProvider.complete(prompt: str) -> str`、`EchoProvider.complete(prompt: str) -> str` 和 `Agent.run(prompt: str) -> str`

- [ ] **Step 1 / 步骤 1：Write tests for provider delegation, structural provider compatibility, and empty prompts. / 编写测试，覆盖提供方委托、结构化协议兼容性和空提示词。**
- [ ] **Step 2 / 步骤 2：Run `python -m unittest discover -s tests -v` and verify it fails because the package is absent. / 运行 `python -m unittest discover -s tests -v`，确认测试因包尚不存在而失败。**
- [ ] **Step 3 / 步骤 3：Add minimal package metadata, protocol, echo provider, and agent implementation. / 添加最小包元数据、协议、回显提供方和 Agent 实现。**
- [ ] **Step 4 / 步骤 4：Run `PYTHONPATH=src python -m unittest discover -s tests -v` and verify all core tests pass. / 运行 `PYTHONPATH=src python -m unittest discover -s tests -v`，确认所有核心测试通过。**

### Task 2: CLI, documentation, and repository hygiene / 任务 2：CLI、文档与仓库清理

**Files / 文件：**

- Create / 创建：`src/niub_agent/__main__.py`
- Create / 创建：`tests/test_cli.py`
- Modify / 修改：`README.md`
- Modify / 修改：`.gitignore`

**Interfaces / 接口：**

- Consumes / 输入依赖：Task 1 中的 `Agent` 和 `EchoProvider` / `Agent` and `EchoProvider` from Task 1
- Produces / 对外提供：`main(argv: list[str] | None = None) -> int` 和 `niub-agent` 命令行脚本 / the `niub-agent` console script

- [ ] **Step 1 / 步骤 1：Write CLI tests for joined prompt words, printed output, and successful return status. / 编写 CLI 测试，覆盖提示词拼接、打印输出和成功返回状态。**
- [ ] **Step 2 / 步骤 2：Run the CLI tests and verify they fail because `__main__` is absent. / 运行 CLI 测试，确认其因缺少 `__main__` 而失败。**
- [ ] **Step 3 / 步骤 3：Implement the CLI, write a bilingual README, and add `.idea/` to `.gitignore`. / 实现 CLI、编写中英双语 README，并将 `.idea/` 加入 `.gitignore`。**
- [ ] **Step 4 / 步骤 4：Run `PYTHONPATH=src python -m unittest discover -s tests -v` and verify the full suite passes. / 运行 `PYTHONPATH=src python -m unittest discover -s tests -v`，确认完整测试套件通过。**
- [ ] **Step 5 / 步骤 5：Run `PYTHONPATH=src python -m niub_agent hello agent` and verify it prints `hello agent`. / 运行 `PYTHONPATH=src python -m niub_agent hello agent`，确认输出 `hello agent`。**

