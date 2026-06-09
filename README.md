# Lucode 0.1.0

Lucode 是一个中文优先、项目本地优先的终端代码代理。它把模型连接、多脑调度、Skill、MCP 工具、命令安全审批、会话记忆和终端交互整合到一个 CLI 工作台里，适合在本地项目中做代码阅读、项目分析、修复规划、受控编辑和多 Agent 协作。

当前版本是 **Python / conda 首发版**。建议先在 Python 3.11+ 环境中安装使用；npm wrapper 和独立 exe 不属于本次发布入口。

## 适合做什么

- 阅读和总结项目结构、配置、README、Git 状态和 diff。
- 按 `solo`、`serial`、`full` 三种模式处理不同复杂度任务。
- 连接 DeepSeek、OpenAI、OpenRouter、DashScope、SiliconFlow、MiMo、本地 Ollama / LM Studio / llama.cpp，以及自定义 OpenAI-compatible 中转。
- 为前置优化脑、主脑规划脑、执行专家脑和汇总脑分别选择模型。
- 使用内置 Skill 和 MCP 工具完成代码定位、只读文件分析、受控编辑、命令执行和审计。
- 保存 JSONL 会话，并通过 `/resume` 恢复上下文。

## 快速开始

### 1. 准备环境

需要 Python 3.11+。推荐使用 conda：

```powershell
conda create -n lucode python=3.11
conda activate lucode
python -m pip install -e .
lucode doctor
```

`lucode doctor` 用于检查入口、依赖和基础配置状态。

### 2. 初始化工作区

在你的项目目录执行：

```powershell
lucode init
```

Lucode 会在当前项目下创建 `.lucode/` 工作区，用于保存项目配置、权限策略、Skill、MCP 和会话数据。

### 3. 启动聊天

```powershell
lucode chat
```

进入聊天后可以直接输入自然语言任务，也可以输入 `/` 打开命令菜单。

### 4. 连接模型

推荐在聊天中使用：

```text
/connect
```

也可以用 CLI 方式添加 Provider：

```powershell
lucode connect deepseek --api-key <你的 key>
lucode connect openai --api-key <你的 key>
lucode connect my_proxy --custom --homepage https://proxy.example.com --base-url https://api.proxy.example.com/v1 --model gpt-5.2 --api-key <你的 key>
```

API key 保存到用户级 `~/.lucode/auth.json`。项目模型和 Provider 配置保存到当前项目的 `.lucode/config.toml`。不要把密钥写进仓库。

## 三种执行模式

| 模式 | 适合场景 | 行为 |
| --- | --- | --- |
| `solo` | 简单问答、小范围阅读、轻量任务 | 单 Agent 直接处理 |
| `serial` | 需要分步分析、逐步校验的任务 | 主脑规划后串行执行 |
| `full` | 复杂项目分析、可并行拆解的任务 | 主脑拆分任务，多 Worker 并行处理后汇总 |

切换方式：

```text
/mode solo
/mode serial
/mode full
```

## 模型和能力检查

Lucode 使用多脑模型配置：

| 脑位 | 用途 |
| --- | --- |
| 前置优化脑 | 优化用户输入、补齐任务边界 |
| 主脑规划脑 | 拆解任务、选择模式和工具 |
| 执行专家脑 | 阅读文件、定位代码、执行子任务 |
| 汇总脑 | 汇总多 Agent 结果，输出最终回答 |

常用命令：

```text
/models              打开多脑模型调音台
/models available    查看模型状态
/models roles        查看四脑配置
/models probe force  强制重新检查模型能力
```

`/models available` 会区分：

- 最近检查：最近一次接口检查结果。
- 运行判断：当前是否可尝试运行。
- 接口能力：OpenAI-compatible 参数是否被接口接受。
- 模型行为：是否实际观察到 chat、JSON、tool_calls、stream 等行为。

模型能力检查不是绝对证明。不同厂商和中转对 tools、JSON、stream 的实现不完全一致，结果应理解为“最近检查”和“能力推断”。

## 常用命令

```text
/status              查看当前运行状态
/config              查看配置概览
/mode                查看或切换执行模式
/models              打开模型调音台
/connect             连接或删除 Provider
/skills              查看当前项目 Skills
/skills_all          查看全部 Skills
/mcp                 查看当前项目 MCP
/mcp_all             查看全部 MCP
/tools               查看核心工具状态
/audit               查看工具审批和事件审计
/resume              恢复会话
/new                 开始新对话
Ctrl-C               运行中中断当前轮，不退出程序
/exit                退出
```

## 安全和审批

Lucode 默认不会绕过危险操作。涉及写文件、运行命令、删除文件、修改 Git 历史、发布等动作时，会走命令分析、权限策略或人工审批。

内置安全能力包括：

- 只读任务优先走 fast path，减少无谓工具调用。
- 高风险命令会被拒绝或要求确认。
- 写入前可触发备份和审计记录。
- `/audit` 可查看最近工具调用和审批事件。

## 会话和本地文件

Lucode 会在项目目录下使用 `.lucode/` 保存项目级状态：

```text
.lucode/
  config.toml
  permissions.toml
  skills/
  mcp/
  memory/
  sessions/
```

常见本地缓存：

```text
.agent_cache/
.agent_runs/
.agent_quarantine/
```

这些目录用于本机运行状态、缓存和临时材料，不应该提交到发布分支。

## 项目结构

```text
lucode/              CLI 入口、聊天循环和终端交互
runtime/             Agent、配置、执行、安全、工具、UI 和工作区逻辑
planning/            任务规划、计划解析和计划校验
catalog_system/      Provider、模型、Skill、MCP catalog 加载和刷新
catalogs/            内置 catalog JSON
mcp_servers/         内置 MCP 工具服务器
skills/              内置 Skill
main.py              本地 Python 入口
pyproject.toml       Python 包配置
```

## 当前边界

- 当前发布入口是 Python / conda，不提供 npm 或 exe 安装承诺。
- Provider 以 OpenAI-compatible 接口为主；非兼容原生 SDK 适配属于后续路线。
- 模型能力检查是最近检查和能力推断，不代表永久、绝对支持。
- 终端 UI 在 Windows Terminal、PowerShell、CMD、PyCharm Terminal 中可能有显示差异；不支持时会回退到普通文本。
- CLI fast path 主要覆盖只读任务；写入和危险命令仍走审批。

## 版本

- 当前版本：`0.1.0`
- Python 要求：`>=3.11`
- License：`UNLICENSED`
