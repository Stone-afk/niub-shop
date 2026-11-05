# Python Agent Skeleton Design / Python Agent 最小骨架设计

## Intent / 目标

Initialize the nearly empty repository as a minimal, vendor-neutral Python agent project that can run and be tested without network access or API credentials.

将当前近乎空白的仓库初始化为一个最小化、厂商无关的 Python Agent 项目，无需网络或 API 凭证即可运行和测试。

## Goals / 具体目标

- Provide a small `Agent` abstraction. / 提供一个小型 `Agent` 抽象。
- Define a structural `ModelProvider` interface with `typing.Protocol`. / 使用 `typing.Protocol` 定义结构化的 `ModelProvider` 接口。
- Include a deterministic echo provider so the project works immediately. / 提供结果确定的回显模型，使项目初始化后即可运行。
- Expose a minimal command-line entry point. / 提供最小化的命令行入口。
- Use only the Python standard library at runtime and for tests. / 运行时和测试仅使用 Python 标准库。
- Ignore JetBrains `.idea/` project files. / 忽略 JetBrains 的 `.idea/` 项目文件。
- Write project documentation in Chinese and English. / 项目文档使用中英双语。

## Non-goals / 非目标

- No vendor SDK or network transport. / 不引入任何厂商 SDK 或网络传输实现。
- No tool-calling framework or memory store. / 不引入工具调用框架或记忆存储。
- No plugin registry or configuration framework. / 不引入插件注册器或配置框架。
- No packaging or test dependencies beyond standard Python tooling. / 除 Python 标准工具外，不引入打包或测试依赖。

## Constraints / 约束

- Use a `src/` package layout. / 使用 `src/` 包布局。
- Keep the implementation intentionally small and typed. / 保持实现精简，并提供类型标注。
- Preserve the existing Apache 2.0 license. / 保留现有的 Apache 2.0 许可证。
- Keep code identifiers and commands in English. / 代码标识符和命令保持英文。

## Approaches / 方案比较

### Single-file script / 单文件脚本

This has the fewest files, but it becomes awkward to extend when a real model provider is added.

该方案文件数量最少，但加入真实模型提供方后会难以扩展。

### Small package with a protocol boundary / 带协议边界的小型包（已选）

This stays minimal while making providers replaceable without inheritance or a vendor dependency.

该方案在保持精简的同时，允许在不继承基类、不绑定厂商依赖的情况下替换模型提供方。

### Extensible plugin framework / 可扩展插件框架

This was rejected because it adds configuration and registration concepts before they are needed.

该方案未被采用，因为它会在实际需要之前引入配置和注册等额外概念。

## Design / 设计

The `niub_agent` package owns an `Agent` that delegates a prompt to any object satisfying `ModelProvider.complete(prompt: str) -> str`. `EchoProvider` supplies a local default implementation. A small CLI accepts prompt words, runs the agent, and prints the response. Tests use `unittest` and exercise delegation and CLI behavior.

`niub_agent` 包中的 `Agent` 会把提示词委托给任何满足 `ModelProvider.complete(prompt: str) -> str` 的对象。`EchoProvider` 提供本地默认实现。一个小型 CLI 接收提示词、运行 Agent 并打印响应。测试使用 `unittest`，覆盖委托逻辑和 CLI 行为。

