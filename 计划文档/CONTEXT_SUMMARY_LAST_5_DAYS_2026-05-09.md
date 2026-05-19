# 最近 5 天上下文压缩包

生成时间：2026-05-09  
覆盖范围：围绕 `D:\pycharm\code\agents_demo` 项目的最近连续讨论与改造上下文。  
注意：本文不包含 `.env` 里的真实 API key、token 或其它敏感值。

---

## 1. 当前项目定位

这个项目最初是基于 `openai-agents-python` 做的多智能体实验项目，现在已经逐步演进成一个“本地终端工程代理”雏形。

长期目标：

- 像 Claude Code / opencode 一样，在终端里直接输入问题或指令。
- 支持读文件、改文件、删除前备份、运行命令、查看 Git、联网搜索。
- 支持三种解耦模式：
  - `solo`：默认模式，单模型工具 Agent，类似 Claude CLI 的单代理体验。
  - `serial`：多 Agent 串行处理，避免并行写冲突。
  - `full`：高级多 Agent，允许经过安全审查后的并行批次。
- 支持本地优先 / 隐私模式 / 云端模型兼容。
- 后续希望继续发展成真正的 CLI 终端代理服务。

当前默认模式已经明确为 `solo`，不再使用旧的“默认/auto”混合模式。

---

## 2. 关键架构决策

### 2.1 三模式解耦

最终确定只保留三种模式：

- `solo`
  - 不调用主脑规划。
  - 不创建多 Agent。
  - 一个模型直接理解问题并按需挂载工具。
  - 可以读写文件、联网、运行命令，但仍经过审批与安全工具。
  - 不主动建议用户切换模式，除非用户明确要求“多 Agent / 多专家 / 并行”。

- `serial`
  - 使用主脑规划。
  - 多 Agent 按依赖和安全顺序串行执行。
  - 适合复杂工程任务，但避免并行冲突。

- `full`
  - 使用完整规划、审查、执行、审核、修复循环。
  - 只有确认无依赖、写入范围不冲突的任务才允许并行。
  - 仍需要安全门、审查 Agent、Patch Ledger、Checkpoint 等约束。

### 2.2 并行冲突处理方向

讨论过 CRDT、Agent 对话通道、监管 Agent、共享任务状态等方案。最后阶段性采用更稳的工程路线：

- 默认不做自由并行写入。
- 主脑可以拆任务，但执行层必须基于依赖排序。
- 同一文件、父子路径、未知写入范围都必须串行。
- 并行只允许在 `full` 模式下，且必须满足安全条件。
- 写文件工具要求 strict sha256，防止基于旧版本覆盖新内容。
- 如果多轮修复失败，后续应结合 checkpoint / Git 回滚，并记录失败案例到 Flywheel。

### 2.3 汇总副脑调整

旧架构中有固定“汇总副脑”。后续讨论后，汇总副脑在某些场景被弱化：

- 单 Agent 或单任务不需要额外汇总，避免浪费 token。
- 多 Agent 时仍可能需要汇总。
- 更长期的新方向是把“汇总副脑”部分职能转成 Auditor：
  - 审查是否完成用户目标。
  - 输出修改内容、验证结果、剩余风险。
  - 不通过时把问题回传给主脑进入修复循环。

---

## 3. 已完成的重要能力

### 3.1 Skill / MCP / Model 图书馆

项目已经有自动刷新图书馆能力：

- Skill 图书馆：扫描 `skills/` 下的 `SKILL.md`。
- MCP 图书馆：登记只读文件、代码定位、编辑、命令、Git、联网搜索、安全删除等能力。
- Model 图书馆：从 `.env` 动态发现模型配置。

模型注册已经从固定模型逐步改成动态发现：

- 支持 `MODEL_<NAME>_*` 风格。
- 支持同一 API key / base_url 下多个模型版本：
  - 例如 `MODEL_SILICONFLOW_MODELS=alias:model-name,...`
  - 例如 `MIMO_API_MODELS=mimo_v25:mimo-v2.5,mimo_v25_pro:mimo-v2.5-pro`
- 未配置模型不应该出现在可用视图中。
- 本地模型需要探测后才显示为真正可用，避免误导。

### 3.2 本地 / 云端模型与隐私模式

已加入隐私模式：

- `offline`：只允许本地模型，默认禁止云端模型。
- `local_first`：本地优先，本地不可用时可以用云端。
- `cloud_allowed`：允许云端模型和联网能力。

本地模型方向：

- 支持 Ollama OpenAI-compatible 接口。
- deepseek-r1:7b 这类不支持 tools 的模型会被标记为不适合工具调用。
- 讨论过 llama.cpp / GGUF 原生加载，当前主要是架构预留，尚未完整落地。

重要修复：

- 本地模型不支持 tools 时不能强行挂载 MCP。
- offline 模式不能误选云端模型。
- 本地模型服务连接状态需要探测，避免 `/model` 误显示“已配置可用”。

### 3.3 MCP 基础工具

当前主要 MCP 能力：

- `project_filesystem_readonly`
  - 只读文件、目录树、搜索文件、读取多个文件。
  - 有读取预算，避免一次性吃掉太多上下文。

- `code_locator`
  - 已从简单词法搜索升级到 BM25 + Python AST 符号索引 + SQLite 调用图缓存。
  - 支持定位文件、符号、片段和调用链。
  - 后续还可以继续升级 tree-sitter / 多语言 AST / 语义索引。

- `workspace_edit`
  - 创建、写入、替换、应用 unified diff、删除文件。
  - strict sha256 保护已有文件。
  - 写入、覆盖、删除前有备份机制。

- `safe_backup`
  - 删除前压缩备份。
  - 后来取消了“移动到 deleted 隔离区”的设计，保留 zip 备份更简洁。

- `command_runner`
  - 可运行本地命令。
  - 需要审批。
  - 有危险命令拦截。

- `git_tools`
  - 只读 status/diff/log。
  - commit 需要审批。
  - 不提供 push/reset/clean。

- `web_search`
  - 联网搜索与网页读取。
  - 已讨论并部分实现“来源分级”：官方文档 > 官方 GitHub > 文档站 > 包仓库 > GitHub > 普通网页 > 社区文章。
  - 普通闲聊不应触发联网。

### 3.4 CLI 命令

已存在的交互命令：

- `/exit`：退出。
- `/stop`：中止当前运行中的任务并回到输入。
- `/new`：开启新对话，清空短期上下文。
- `/status`：查看运行状态。
- `/diff`：查看当前 Git diff 摘要。
- `/rollback`：回滚最近一轮会话 checkpoint。
- `/config`：查看配置总览。
- `/api show`：查看 API 配置，不显示 key。
- `/privacy`：查看隐私模式。
- `/model`：查看模型优先级和状态。
- `/model available`：只看当前确认可用模型。
- `/mode solo|serial|full`：切换执行模式并写入 `.env`。
- `/refiner on|off`：切换前置优化副脑并写入 `.env`。
- `/plan ...`：只预览规划，不执行。

近期重要体验调整：

- 默认关闭前置优化副脑，因为多数用户普通使用不需要。
- 普通问题不应该联网。
- 普通问题不应该被主脑复杂规划拦截。
- `Final output` 重复输出已经处理：流式输出过的正常答案不再二次打印。

---

## 4. 最近一次关键性能优化

用户提出：

- 延迟导入 OpenAI / Agents 相关依赖。
- 模型探测改为后台或按需。
- 正常流式输出后不再打印重复的 `Final output`。

已完成：

- 新增 `runtime/agents/sdk.py`
  - 统一封装懒加载：
    - `Agent`
    - `Runner`
    - `RunHooks`
    - `AsyncOpenAI`
    - `OpenAIChatCompletionsModel`
    - `MCPServerStdio`
    - `create_static_tool_filter`

- 修改：
  - `main.py`
  - `catalog_system/model_catalog.py`
  - `catalog_system/refresher.py`
  - `mcp_servers/__init__.py`
  - `planning/planner.py`
  - `runtime/agents/factory.py`
  - `.env.example`
  - `tests/run_regression.py`

结果：

- `import main` 后不再提前加载 `agents`。
- `MODEL_PROBE_STARTUP_MODE=background` 成为默认启动探测模式。
- `sync` 可恢复同步探测。
- `off` 可跳过探测。
- 正常流式回答后不再重复打印 `========== Final output ==========`
- 错误、规划失败等重要结果仍会打印，避免静默失败。

验证结果：

- 全量回归测试：`188 tests OK`
- 真实 `/status` 启动测试通过。
- 启动耗时曾测到约 `2.18s`。
- 真实短问答测试通过，正常答案只输出一次。

---

## 5. 当前项目目录概览

核心目录：

- `main.py`
  - 当前终端入口。
  - 负责启动、命令解析、交互循环、流式输出、审批流程。

- `catalog_system/`
  - Skill/MCP/Model 图书馆。
  - 模型动态发现与探测。
  - 权限策略。

- `mcp_servers/`
  - MCP 工具实现。
  - 已分为：
    - `readonly/`
    - `mutation/`
    - `execution/`
    - `network/`
    - `core/`

- `planning/`
  - 主脑规划、planner schema、plan validator、plan reviewer。

- `runtime/`
  - Agent 工厂、执行模式、动态执行、pipeline、Auditor、checkpoint、repair loop、Flywheel、workspace。

- `skills/`
  - 各类 `SKILL.md`。
  - 包括 query refiner、orchestrator planner、final synthesizer、project explorer、code skill、humanizer、skill creator 等。

- `tests/`
  - 当前是单文件回归测试 `tests/run_regression.py`。
  - 覆盖工具、规划、模型、隐私、CLI、checkpoint、Flywheel、MCP 等。

---

## 6. 已知问题和后续优化点

### 6.1 启动性能还能继续优化

已完成 Agents SDK 懒加载和后台探测，但仍可继续拆：

- `main.py` 顶层仍导入较多 runtime/planning 模块。
- 可以把 `solo`、`serial`、`full` 模式执行逻辑做二级懒加载。
- `/status`、`/model` 等只读命令路径不应加载执行链。

建议方向：

- 建立 `cli_app/` 或 `terminal/` 包。
- 入口只加载：
  - env
  - 命令解析
  - settings
  - 最小 catalog
- 真正执行模型任务时再加载 Agents SDK、MCP、planning。

### 6.2 CLI 终端代理 C 阶段尚未正式开始

用户准备进入 C 阶段：

- 研究 Claude Code / opencode 的 CLI 终端代理设计。
- 重点看：
  - 如何打包成 CLI。
  - 如何设计交互命令。
  - 如何内置读文件、改文件、运行终端。
  - 如何做权限、审批、配置、项目级记忆。
  - 哪些优秀设计适合迁移进本项目。

这一步还在方案讨论阶段，尚未动代码。

### 6.3 测试结构可以继续整理

当前 `tests/run_regression.py` 已经很大。

建议后续拆分：

- `tests/test_cli.py`
- `tests/test_model_catalog.py`
- `tests/test_mcp_readonly.py`
- `tests/test_workspace_edit.py`
- `tests/test_planning.py`
- `tests/test_runtime_modes.py`
- `tests/test_checkpoint.py`

### 6.4 本地模型能力仍可增强

后续建议：

- 增加 `/model probe` 手动探测命令。
- 增加 `/model refresh` 刷新模型图书馆。
- 将本地模型服务健康检查和模型能力探测分开显示。
- 支持 Ollama、llama.cpp、OpenAI-compatible 三类后端的统一后端抽象。
- 后续加入“一键切换本地 / 云端模型”的 CLI。

### 6.5 真实终端体验

当前仍是 Python `main.py` 入口。

C 阶段目标应考虑：

- 包装成命令，例如：
  - `lucode`
  - `lucode chat`
  - `lucode run`
  - `lucode config`
  - `lucode model`
  - `lucode doctor`

- 支持项目级配置文件，例如：
  - `.lucode/config.toml`
  - `.lucode/permissions.json`
  - `.lucode/memory/`

- 支持初始化：
  - `lucode init`

- 支持无交互运行：
  - `lucode run "修复这个 bug"`

- 支持交互 TUI：
  - 过程日志、工具调用、diff、审批更清楚。

---

## 7. 最近讨论中的 C 阶段研究任务

用户明确要求：

> 准备开始 C 阶段方案，先不动代码，重新扫描整个项目，调查 Claude Code 和 opencode 如何把代码打包成 CLI 终端代理，以及它们内置的修改文件、阅读文件、运行终端的 MCP / 工具机制，评估能否借鉴到本项目。

本轮已经开始但未完成最终报告：

- 已只读扫描 `D:\pycharm\code\agents_demo` 项目结构。
- 已准备联网查看：
  - `https://github.com/anthropics/claude-code`
  - `https://github.com/anomalyco/opencode`
  - Claude Code 官方文档
  - opencode 文档

下一轮继续时，应完成：

1. 梳理 Claude Code 的公开能力：
   - CLI 安装与运行方式。
   - 权限系统。
   - 文件读写与 shell 执行。
   - 项目记忆 / 配置。
   - slash commands。
   - hooks / MCP 扩展。

2. 梳理 opencode：
   - 开源结构。
   - CLI/TUI 包装方式。
   - tool 系统。
   - permission 模型。
   - agent / mode / config 设计。

3. 对照本项目：
   - 当前已有能力。
   - 缺失能力。
   - 可迁移设计。
   - 不建议照搬的部分。

4. 输出 C 阶段方案：
   - C1：CLI 包装与命令系统。
   - C2：权限和审批体验升级。
   - C3：工具系统统一抽象。
   - C4：项目级配置和记忆。
   - C5：TUI / 过程日志 / diff 审批。
   - C6：发布与安装。

---

## 8. 重要原则

后续继续工作时请遵守：

- 不要泄露 `.env` 内容。
- 修改项目前先说清楚要改什么。
- 用户要求讨论方案时，不要动代码。
- 修改代码时继续用 TDD：
  - 先写测试。
  - 跑测试看到失败。
  - 再实现。
  - 再清理。
  - 最后真实 CLI 测试。
- 删除文件前必须说明理由。
- 项目外权限需要先说明理由。
- 对于多 Agent 并行，默认保守，避免并行写冲突。
- 普通聊天不联网、不多 Agent、不规划过度。

---

## 9. 快速恢复指令

如果后续上下文丢失，可以让模型先读：

1. `D:\pycharm\code\agents_demo\CONTEXT_SUMMARY_LAST_5_DAYS_2026-05-09.md`
2. `D:\pycharm\code\agents_demo\新修改方向.md`
3. `D:\pycharm\code\agents_demo\new_agent_project.md`
4. `D:\pycharm\code\agents_demo\PROJECT_ROADMAP.md`
5. `D:\pycharm\code\agents_demo\tests\run_regression.py`

然后继续 C 阶段方案讨论，不要直接动代码。

