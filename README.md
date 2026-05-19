# Lucode V1.2

Lucode 是一个中文优先的终端代码代理。它把多模型、多 Agent、Skill、MCP、CLI 命令和本地项目上下文放到同一个工作流里，目标是让开发者在任意项目目录中运行 `lucode`，就能获得类似 Claude Code / OpenCode 的终端协作体验，同时保留更清晰的中文配置、中文审批和多脑模型调音能力。

> 当前 V1.2 是产品化推进版本：核心链路已经可用，但仍建议先在个人项目或小范围测试环境中使用。公开分发前还需要继续完善原生 Provider、错误恢复、沙箱隔离和文档。

## 核心特点

### 中文优先的终端体验

- 启动页使用蓝色边框仪表盘，显示当前项目、模式、主脑、执行脑、隐私、工具和备份状态。
- Slash 命令支持上下键、回车和鼠标选择；异常终端会自动回退到普通文本输入。
- `/connect`、`/models`、审批面板和主要配置页都尽量使用中文说明，降低新用户理解成本。

### 三种执行模式

| 模式 | 适合场景 | 行为 |
| --- | --- | --- |
| `solo` | 小任务、单文件阅读、快速问答 | 单 Agent 直接执行，响应最快 |
| `serial` | 需要逐步分析和校验的任务 | 多 Agent 串行推进，先分析再执行 |
| `full` | 复杂项目审查、并行分析 | 多 Agent 并行拆解，再汇总结论 |

在聊天内可用 `/mode solo`、`/mode serial`、`/mode full` 切换；切换后会重新显示欢迎仪表盘，方便确认当前状态。

### 多脑模型调音台

Lucode 把模型角色拆成四个脑位，用户可以按个人偏好单独配置：

| 脑位 | 内部角色 | 用途 |
| --- | --- | --- |
| 前置优化脑 | `query_refiner` | 优化用户输入、澄清任务边界 |
| 主脑规划脑 | `orchestrator` | 规划任务、拆解步骤、决定工具 |
| 执行专家脑 | `executor` | 阅读文件、调用工具、执行子任务 |
| 汇总脑 | `final_synthesizer` | 汇总多 Agent 结果、给出最终回答 |

输入 `/models` 会进入独立调音台，可以用上下键或鼠标选择脑位和模型；输入 `q` 或选择退出返回主聊天。也可以用命令方式设置：

```bash
/models select deepseek/deepseek-v4-pro deepseek/deepseek-v4-flash
/models role orchestrator deepseek/deepseek-v4-pro
/models brain 主脑 deepseek/deepseek-v4-flash
/models brain reset
```

### Provider 连接向导

`/connect` 提供 Provider 连接和删除入口。内置预设包括：

- DeepSeek
- OpenAI
- OpenRouter
- Qwen / DashScope
- SiliconFlow
- MiMo
- Ollama / LM Studio / llama.cpp 本地服务
- 自定义 OpenAI-compatible 中转

自定义中转必须同时配置：

- Provider 名称
- 官网或控制台地址 `homepage`
- 真实请求地址 `base_url`
- API key
- 模型名

API key 保存到用户级 `~/.lucode/auth.json`，项目配置保存到当前项目的 `.lucode/config.toml`。README 不会要求用户把密钥写进仓库。

### 模型能力探测 v2.3

`/models probe` 会检查已配置模型的可用性，并把结果缓存到 `.agent_cache/model_capabilities.json`。当前探测范围包括：

- API key 是否存在
- `base_url` 是否可连接
- 模型名是否可用
- chat 是否可用
- JSON 输出能力
- tools / function calling 支持情况
- stream 支持情况
- 延迟估计
- 上下文长度档位
- 适合主脑、执行脑或汇总脑的推荐

如果你刚改完 Provider、模型名或中转地址，建议运行：

```bash
/models probe force
```

探测结果是“运行时实测 + 厂商预设表 + 保守回退”的组合。部分厂商对 tools、JSON 或 stream 的错误提示不完全一致，所以结果可能需要通过强制探测或手动配置修正。

### Skill、MCP 和 Slash 命令融合

Lucode 会发现多种命令来源：

- 内置命令，例如 `/status`、`/config`、`/diff`
- 项目命令：`.lucode/commands/*.md`
- 用户命令：`~/.lucode/commands/*.md`
- Skill 命令：项目、用户和内置 `skills/*/SKILL.md`
- MCP prompt 命令：来自 MCP catalog 的 prompt 定义

输入 `/` 会出现命令菜单，左侧是命令，右侧是中文说明。输入 `/mo` 会过滤到模型相关命令。

### JSONL 会话、恢复和上下文压缩

- 会话保存在 `.lucode/sessions/*.jsonl`。
- `/resume` 可以查看或恢复最近会话。
- 当前版本使用轻量分级压缩：短期上下文优先保留最近对话，完整历史保留在 JSONL 中，后续可继续接语义压缩或知识图谱。

### 审批、安全和审计

Lucode 已经把危险命令分析从执行链里拆出来：

- `CommandAnalyzer v2` 会给出 `allow`、`allow_limited`、`ask`、`sandbox_preview`、`deny` 决策。
- 高风险命令如 `git reset --hard`、`git clean`、递归删除、发布命令会被直接拦截或要求审批。
- 交互式审批支持上下键和鼠标选择；非交互环境回退到 `y` / `n`。
- `/audit` 或 `/hooks` 可以查看最近工具审批和事件记录。

### CLI 优先，MCP 兜底

为了减少 token 消耗和等待时间，V1.2 已开始把部分只读任务走本地 fast path：

- `git status`
- `git diff`
- `package.json` / `pyproject.toml` 摘要
- JSON / TOML / YAML 配置摘要
- README 与 MCP catalog 的数量统计类任务

MCP 仍然作为兜底工具，用于外部文档、GitHub 代码搜索、复杂协议和模型需要工具上下文的任务。

## 快速开始

### 1. 准备环境

建议使用 Python 3.11+。如果你使用 conda，本项目测试环境名为 `agents-demo`：

```powershell
conda activate agents-demo
python -m pip install -e .
lucode doctor
```

如果通过 npm wrapper 启动，可以指定当前 conda 环境里的 Python：

```powershell
$env:LUCODE_PYTHON="D:\develop\Data_anaconda2024\envs\agents-demo\python.exe"
npm link
lucode doctor
```

`LUCODE_PYTHON` 的优先级高于系统 Python，适合避免“命令行找不到 openai-agents 依赖”的问题。

### 2. 初始化项目

在任意项目目录执行：

```bash
lucode init
```

Lucode 会创建：

```text
.lucode/
  config.toml
  permissions.toml
  skills/
  mcp/
  memory/
  sessions/
```

以后在这个目录或子目录运行 `lucode`，都会识别当前项目的 `.lucode` 配置。

### 3. 连接模型

推荐进入交互式连接向导：

```bash
lucode chat
/connect
```

也可以直接用 CLI：

```bash
lucode connect deepseek --api-key <你的 key>
lucode connect openai --api-key <你的 key>
lucode connect my_proxy --custom --homepage https://proxy.example.com --base-url https://api.proxy.example.com/v1 --model gpt-5.2 --api-key <你的 key>
```

删除已保存 Provider 或模型：

```bash
/connect
# 选择“删除模型/Provider”
```

删除时会二次确认，并清理 API key、Provider 配置和失效脑位引用。

### 4. 开始使用

交互式聊天：

```bash
lucode chat
```

非交互执行一次任务：

```bash
lucode run "解释这个项目的目录结构"
```

常用命令：

```text
/status              查看运行状态
/config              查看当前配置
/mode serial         切换执行模式
/models              打开多脑模型调音台
/models list         查看 Provider 模型列表
/models probe force  强制重新探测模型能力
/connect             打开 Provider 连接向导
/skills              查看当前项目 Skills
/skills_all          查看全部 Skills
/mcp                 查看当前项目 MCP
/mcp_all             查看全部 MCP
/tools               查看核心工具
/audit               查看工具审批审计
/resume              恢复会话
/new                 开始新对话
/exit                退出
```

## 项目结构

```text
lucode/
  entry.py              # lucode CLI 入口、子命令分发
  shell/                # 交互式聊天循环、Slash 命令和输入体验

runtime/
  agents/               # Agent 工厂和模型角色
  commands/             # Slash 命令注册、外部命令发现
  config/               # Provider、模型调音台、工作区配置
  context/              # 上下文压缩和会话注入
  execution/            # 执行编排、fast path、动态任务路由
  hooks/                # 工具事件和审计
  kernel/               # 对外统一运行入口
  memory/               # 失败记忆、checkpoint 等辅助状态
  modes/                # solo / serial / full 兼容层
  providers/            # Provider registry 和 OpenAI-compatible adapter
  safety/               # 命令风险分析、审批策略
  sessions/             # JSONL SessionStore
  tools/                # MCP / 工具注册表
  ui/                   # 欢迎页、面板、prompt_toolkit UI
  workspace/            # .lucode 工作区发现

catalog_system/         # catalog 刷新与生成逻辑
catalogs/               # Provider / MCP / Skill / Model catalog
mcp_servers/            # 内置 MCP 服务
skills/                 # 内置 Skills
planning/               # 内置规划资源
bin/lucode.js           # npm wrapper
tests/                  # 回归测试
main.py                 # 本地开发入口
```

## 配置和本地文件

建议提交到 Git：

- `lucode/`
- `runtime/`
- `catalog_system/`
- `catalogs/` 中稳定的预设文件
- `mcp_servers/`
- `skills/`
- `planning/`
- `tests/`
- `README.md`
- `pyproject.toml`
- `package.json`
- `.env.example`

不建议提交：

- `.env`
- `.lucode/`
- `.agent_cache/`
- `.agent_quarantine/`
- `.agent_runs/`
- `.pytest_cache/`
- `.idea/`
- `__pycache__/`
- `lucode.egg-info/`
- `package-lock.json`
- 本地计划文档和临时评审文档

## MCP 工具服务器

| `MCP 工具服务器` | 说明 |
| --- | --- |
| `project_filesystem_readonly` | 只读项目文件访问 |
| `skills_filesystem_readonly` | 只读 Skill 文件访问 |
| `code_locator` | 代码定位和索引 |
| `workspace_edit` | 受审批保护的工作区编辑 |
| `safe_backup` | 修改前备份 |
| `command_runner` | 受审批保护的命令执行 |
| `git_tools` | Git 状态、diff、log 和受控 commit |
| `web_search` | 网络搜索和网页读取 |
| `context7_docs` | 第三方库文档查询 |
| `grep_code_search` | GitHub 代码片段搜索 |

## 冲突点和边界

1. **npm 包还不是完全自包含二进制。** 当前 `bin/lucode.js` 会寻找 `LUCODE_PYTHON`、conda、venv 或系统 Python。后续产品化可以继续做打包器，降低用户环境依赖。
2. **Provider 以 OpenAI-compatible 为主。** DeepSeek、OpenAI、OpenRouter、DashScope、SiliconFlow、MiMo、自定义中转和本地服务已经能走统一 adapter；Anthropic、Gemini 等原生 SDK 适配仍是后续任务。
3. **模型能力探测不是绝对真理。** 不同厂商对 tools、JSON、stream 和超时错误的表达不一致，探测结果可能需要 `/models probe force` 或手动覆盖。
4. **CLI fast path 当前只覆盖只读任务。** 写入、删除、发布、Git 历史修改等操作仍走审批和安全分析，不会为了速度绕过安全边界。
5. **MCP 是兜底，不是所有任务的首选。** 本地可直接完成的查询优先走 CLI；外部文档、GitHub 代码搜索和复杂协议仍依赖 MCP 或网络。
6. **`rg` 不是硬依赖。** 如果本机 `rg` 不可用或被系统权限拦截，Lucode 会继续运行，但搜索体验会降级。
7. **终端 UI 有兼容差异。** Windows Terminal、PowerShell、CMD、PyCharm Terminal 对鼠标、滚轮和 ANSI 的支持不同；Lucode 会尽量回退到纯文本，但视觉效果可能不完全一致。
8. **本地缓存可能反映旧状态。** 删除模型、切换 Provider 或改中转后，如果调音台或探测结果不一致，可以运行 `/models probe force`，必要时清理 `.agent_cache/model_capabilities.json`。

## 开发和验证

常用验证命令：

```powershell
python -m compileall lucode runtime tests
python -m unittest tests.run_regression
python tests/run_regression.py
git diff --check
npm pack --dry-run --json
```

只验证 CLI 入口和 README 相关内容：

```powershell
python -m unittest tests.run_regression.LucodeCliEntryTests.test_readme_documents_quick_start_and_conda_python
python -m unittest tests.run_regression.LucodeCliEntryTests.test_npm_wrapper_package_declares_lucode_bin
```

## 版本说明

- 文档标题版本：Lucode V1.2。
- Python / npm 包版本当前仍为 `0.1.0`，后续正式发布前需要统一升级版本号。
- License：`UNLICENSED`。
