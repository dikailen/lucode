# Lucode V1.0

> 中文优先、本地优先、模型中立的多 Agent 终端编码助手

Lucode 是一个运行在终端里的多 Agent 编码代理，支持单 Agent / 串行多 Agent / 并行多 Agent 三种执行模式，内置 7 个 MCP 工具服务器、8 个技能模块、模型能力探测、Git 检查点回滚、SHA256 文件安全校验等特性。

> **⚠️ 当前版本：V1.0 实验版**
>
> 本项目处于早期实验阶段，诸多功能模块和代码结构仍在持续迭代完善中。部分特性可能不稳定，接口和行为在后续版本中可能发生变更。欢迎试用和反馈，但不建议直接用于生产环境。

---

## 核心特点

### 三脑架构

| 角色 | 说明 |
|------|------|
| **前置优化脑** (Query Refiner) | 把用户原始问题整理清晰，可选开关 |
| **主脑** (Orchestrator Planner) | 读取技能/MCP/模型目录，生成动态执行计划 |
| **汇总脑** (Final Synthesizer) | 合并多 Agent 执行结果，输出连贯回答 |

### 三种执行模式

- **Solo** — 默认模式，单 Agent 携带 MCP 工具（读写文件、代码定位、命令执行、Git、联网搜索），智能匹配工具
- **Serial** — 多 Agent 串行模式，主脑规划任务 → 专家 Agent 按依赖顺序执行 → 汇总脑整合
- **Full** — 多 Agent 并行模式，在 Serial 基础上自动检测写冲突，安全的并行任务同时执行

### 模型中立

通过 OpenAI-compatible 协议统一接入，支持一行 `.env` 配置注册任意模型：

- **云端**: DeepSeek、MiMo、DashScope (Qwen)、SiliconFlow、OpenRouter 等
- **本地**: Ollama、llama.cpp / GGUF
- **自建**: 任意 OpenAI-compatible 中转服务

启动时自动探测本地模型的 tools/function calling 能力，缓存结果供下次使用。

### 7 个 MCP 工具服务器

| MCP 服务器 | 功能 |
|------------|------|
| `project_filesystem_readonly` | 预算限制的只读文件系统访问 |
| `code_locator` | BM25 + AST 符号索引 + SQLite 调用图，精准定位代码 |
| `workspace_edit` | 文件创建/写入/替换/补丁/删除，带 SHA256 安全校验 |
| `safe_backup` | 删除前自动 zip 备份到隔离区 |
| `command_runner` | 安全的本地命令执行（禁用 shell、内置拒绝列表） |
| `git_tools` | Git status/diff/log/commit（commit 需用户审批） |
| `web_search` | 联网搜索，结果按来源优先级排序：官方文档 > GitHub > 社区文章 |

### 多层安全防护

- **SHA256 严格模式**: 修改文件前必须先读取并校验当前内容哈希，防止基于过期上下文覆盖
- **Git 检查点回滚**: 每轮自动创建 Git checkpoint，支持 `/rollback` 一键回退
- **权限策略**: 读/写/Shell/MCP 四级可配置权限（allow / ask / deny）
- **隔离备份**: 所有删除文件自动 zip 备份到 `.agent_quarantine/`
- **隐私模式**: `offline`（禁止云模型和联网）/ `local_first`（优先本地）/ `cloud_allowed`
- **执行后审计**: 自动检查交付物是否符合计划，失败自动修复（最多 3 次）

### 8 个技能模块

每个技能是独立的 `SKILL.md` 文件夹，可扩展：

| 技能 | 用途 |
|------|------|
| `jpc_now_skill` | Java/Python/C++ 代码开发、评审、重构 |
| `humanizer_zh` | 中文文本去 AI 痕迹，拟人化润色 |
| `project_explorer` | 项目结构分析与架构理解 |
| `skill_creator` | 技能创建、修改和效果评估 |
| `task_router` | 用户查询路由到专家 Agent |
| `query_refiner` | 用户查询优化和意图澄清 |
| `orchestrator_planner` | 动态规划（读取目录，生成任务图） |
| `final_synthesizer` | 多 Agent 结果合成 |

### JSONL 会话持久化

- 追加式 JSONL 存储，支持断点恢复
- `/resume last` 继续上次会话
- `/resume <session_id>` 恢复指定会话
- 会话自动记录 token 消耗、工具调用、模型选择

---

## 快速开始

### 环境要求

- Python >= 3.11
- Git
- [可选] Node.js（npm 入口包装器）
- [可选] ripgrep（提升搜索效率，无 rg 则降级为 PowerShell 搜索）

### 安装

```powershell
# 使用 conda 环境
conda activate agents-demo
python -m pip install -e .

# 验证安装
lucode doctor
```

### 配置模型

复制 `.env.example` 为 `.env`，填入至少一个模型的 API Key：

```env
DEEPSEEK_API_KEY=sk-xxx
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
```

支持一键注册多个模型、配置三脑模型优先级、本地模型等，详见 `.env.example` 注释。

### 启动

```powershell
# 进入交互式终端
lucode chat

# 一次性执行任务
lucode run "解释这个项目的架构"

# 初始化工作区
lucode init
```

---

## 命令参考

### CLI 命令

| 命令 | 说明 |
|------|------|
| `lucode chat` | 启动交互式终端 |
| `lucode run "<任务>"` | 非交互执行一次任务 |
| `lucode init` | 创建 `.lucode/` 工作区 |
| `lucode doctor` | 检查环境状态 |
| `lucode config` | 查看当前配置 |
| `lucode model` | 查看模型优先级 |
| `lucode mcp` | 查看 MCP 注册状态 |
| `lucode session` | 查看会话列表 |
| `lucode connect <provider>` | 连接并保存 Provider |
| `lucode auth login/logout/list` | 管理凭据 |

### 交互终端命令

| 命令 | 说明 |
|------|------|
| `/mode solo\|serial\|full` | 切换执行模式 |
| `/plan <查询>` | 预览执行计划（不执行） |
| `/resume last\|<id>` | 恢复会话 |
| `/rollback` | 回滚上一轮文件变更 |
| `/status` | 查看当前状态 |
| `/diff` | 查看变更摘要 |
| `/new` | 开始新对话 |

---

## 项目结构

```
agents_demo/
├── lucode/               # CLI 入口（chat、run、init、doctor 等）
├── runtime/              # 核心运行时引擎
│   ├── kernel/           # Shell ↔ Runtime 边界、执行策略
│   ├── agent/            # Agent 运行器、审批流程
│   ├── agents/           # Agent 工厂、SDK 封装、能力检测
│   ├── config/           # 配置加载、模型配置、工作区发现
│   ├── execution/        # 编排器：计划→执行→审计→修复
│   ├── safety/           # 隐私、审计、检查点、权限、命令分析
│   ├── context/          # 上下文压缩（规则/语义）
│   ├── sessions/         # JSONL 会话存储
│   ├── providers/        # OpenAI-compatible Provider 注册
│   ├── tools/            # MCP 工具注册
│   └── hooks/            # 工具生命周期事件
├── catalog_system/       # 模型/技能/MCP 目录系统
├── planning/             # 编排器规划系统
├── mcp_servers/          # 7 个 MCP 服务器实现
├── skills/               # 8 个技能模块（SKILL.md）
├── catalogs/             # 自动生成的 JSON 目录
├── tests/                # 回归测试
├── bin/lucode.js         # npm CLI 入口包装器
└── main.py               # 交互模式主入口
```

---

## 配置

### 工作区配置 (`.lucode/config.toml`)

```toml
mode = "solo"
privacy = "local_first"
```

### 权限配置 (`.lucode/permissions.toml`)

```toml
[read]
default = "allow"
deny = [".env", "**/*.pem"]

[write]
default = "ask"
deny = [".git/**"]

[shell]
default = "ask"
deny = ["git reset --hard", "git clean", "rm -rf"]
```

### 环境变量（`.env`）

所有可配置项参见 `.env.example`，包含完整的中文注释说明。

---

## License

UNLICENSED — 内部使用
