# C 阶段框架处理

生成日期：2026-05-10  
架构校准：2026-05-11，融合 `ARCHITECTURE_GAP_ANALYSIS.md`  
项目路径：`D:\pycharm\code\agents_demo`

本文档用于汇总 C 阶段方案：把当前 `agents_demo` 从“本地多智能体工程原型”升级为类似 Claude Code / opencode 的可安装、可配置、可交互的终端代理产品。本文只记录方案，不包含 `.env`、API key、token 或其它敏感值。

---

## 1. C 阶段核心定位

当前项目已经具备较强的代理内核：

- Model 图书馆：支持从 `.env` 动态发现模型，支持本地 / 云端 / OpenAI-compatible / Ollama 元数据。
- MCP 工具：已有只读文件、代码定位、文件编辑、命令执行、Git、联网搜索、安全备份等能力。
- 执行模式：已有 `solo`、`serial`、`full` 三种模式。
- 安全基础：已有 checkpoint、patch ledger、auditor、workspace edit sha256、备份和操作日志。
- 运行体验：已有 `/status`、`/model`、`/mode`、`/diff`、`/rollback`、`/plan` 等命令。

C 阶段不建议重写代理内核，而是在现有能力上补齐“产品外壳”：

```text
现有内核：
  models + MCP + solo/serial/full + planner + auditor + checkpoint + flywheel

C 阶段新增：
  CLI 命令壳 + 品牌启动界面 + 项目级配置 + 权限系统 + TUI/命令菜单 + npm/二进制发布
```

### 1.1 2026-05-11 架构差距校准结论

`ARCHITECTURE_GAP_ANALYSIS.md` 对比 Claude Code / opencode 后给出的结论是：Lucode 的内核能力已经有基础，但和成熟 CLI Agent 的差距不只在 UI，还在启动路径、Provider 抽象、上下文生命周期、权限防线、Hooks 和 Agent Loop 控制权。C 阶段因此调整为两条线并行：

```text
近期产品化主线：
  C1-C5 继续补齐 CLI、工作区、Provider 配置、权限审批、任务状态、slash 命令和 npm wrapper。

架构增强主线：
  在不推翻现有内核的前提下，逐步加入快速路径启动、Context 压缩、Hooks、多 Provider 原生 SDK、
  消息转换、会话持久化和可替换 Agent Loop。
```

当前项目已经部分完成：

- `lucode/entry.py` 已有 CLI 子命令壳，`chat/run/init/doctor/connect/models/auth` 已可作为产品入口继续演进。
- `runtime/config/model_config.py` 已有 `auth.json`、`.lucode/config.toml`、Provider 预设和角色模型优先级的基础。
- `runtime/config/workspace.py` 已实现 `APP_HOME / USER_HOME / WORKSPACE_ROOT` 分离。
- `runtime/ui/welcome.py`、`runtime/ui/command_palette.py`、`runtime/ui/progress.py` 已有欢迎页、命令菜单和任务状态 MVP。
- `runtime/tools/registry.py`、`runtime/safety/permissions.py` 已有 Tool Registry 和权限文件雏形。

仍需重点补齐：

- 轻量命令快速路径：`--version`、`--help`、`doctor` 等不应导入完整模型/Agent/MCP 运行时。
- 早期输入捕获：`lucode` 启动期间用户输入不应丢失。
- Context 压缩管线：不能只保留最近 6 轮文本，需要结构化摘要、文件线索和失败断路器。
- Hooks 系统：允许用户在 `PreToolUse`、`PostToolUse`、`SessionStart` 等阶段扩展安全和流程。
- 多 Provider 原生 SDK：保留 OpenAI-compatible，同时支持 Anthropic、Gemini、Bedrock 等原生协议。
- 消息转换管线：按 Provider 修正空内容、tool_call id、工具序列、reasoning 字段，避免跨厂商 API 错误。
- 会话 JSONL 持久化：支持 `/resume`、`/fork-session` 和历史消息重放。
- 自研 Agent Loop：作为长期方向，不在 C1-C5 强行替换 SDK Runner。

目标体验：

```bash
lucode
lucode run "修复这个 bug"
lucode init
lucode doctor
lucode config
lucode model
lucode auth
lucode mcp
lucode session
```

---

## 2. 总体架构：安装目录、用户目录、工作区目录分离

当前项目大量逻辑把 `BASE_DIR` 直接当成程序根目录、项目根目录、skills/mcp 来源目录。C 阶段必须拆成三层：

```text
APP_HOME
  Lucode 安装目录，放核心运行代码、内置 skills、内置 MCP、默认模板。

USER_HOME
  用户全局目录，默认 ~/.lucode，放用户级 auth、全局配置、全局 skills、全局 mcp。

WORKSPACE_ROOT
  当前命令运行目录，或向上查找到的 .lucode 所在目录。
  这是文件读写、命令执行、Git 检查、checkpoint、项目扩展 skills/mcp 的工作区。
```

建议目录：

```text
lucode_install/
  skills/
    orchestrator-planner/
    final-synthesizer/
    query-refiner/
    task-router/
    project-explorer/
    skill-creator/
  mcp_servers/
    readonly/
    mutation/
    execution/
    network/

~/.lucode/
  auth.json
  config.toml
  skills/
  mcp/
  cache/

some_project/
  .lucode/
    config.toml
    permissions.toml
    memory/
    sessions/
    skills/
      api-reviewer/
        SKILL.md
    mcp/
      my-local-tool.json
```

### 2.1 工作区识别规则

启动 `lucode` 时：

1. 记录当前 `cwd`。
2. 从 `cwd` 向父目录查找 `.lucode/`。
3. 如果找到，使用该目录的父目录作为 `WORKSPACE_ROOT`。
4. 如果没找到：
   - 交互启动时提示“当前目录未初始化，可运行 `/init` 或 `lucode init`”。
   - 仍可将当前 `cwd` 作为临时工作区，但项目级配置为空。

启动仪表盘中的“项目”必须显示完整工作区路径，而不是 Lucode 安装目录：

```text
项目    D:\pycharm\code\some_project
配置    .lucode 已发现
```

---

## 3. C1：CLI 产品化

### 3.1 目标

从：

```bash
python main.py
```

升级为：

```bash
lucode
lucode run "修复这个 bug"
lucode init
lucode doctor
lucode config
lucode model
lucode auth
lucode mcp
lucode session
```

### 3.2 建议命令

```text
lucode
  启动交互式终端代理。

lucode run "..."
  非交互运行一次任务，适合脚本和 CI。

lucode init
  在当前目录创建 .lucode/。

lucode doctor
  检查 Python、Git、rg、模型配置、MCP、权限、终端能力。

lucode config
  查看或修改配置。

lucode model
  查看模型、Provider、角色优先级。

lucode auth
  管理 API key 和 Provider 登录状态。

lucode mcp
  管理 MCP。

lucode session
  管理会话、恢复、历史。
```

### 3.3 代码改造边界

`main.py` 先保留兼容，但逐步抽出：

```text
cli_app/
  entry.py
  commands.py
  workspace.py
  welcome.py
  interactive.py
  command_palette.py

runtime/
  继续保留现有执行内核。
```

入口层只负责：

- 解析命令。
- 发现工作区。
- 加载配置。
- 显示启动界面。
- 调用现有 runtime。

### 3.4 快速路径启动优化

Claude Code 的一个关键体验是“轻量命令极快返回，完整运行时延迟加载”。Lucode 当前 `lucode/entry.py` 已经把 `main.py` 的导入推迟到 `chat/run`，但仍然有以下优化空间：

```text
lucode --version / -v
  零运行时导入，直接输出版本。

lucode --help
  只构建轻量命令帮助，不刷新模型目录，不导入 Agent SDK。

lucode doctor/config/model/mcp/session
  仅导入各自需要的 config 或 registry 模块。

lucode chat/run
  才导入 main.py、MCPServerManager、ModelRegistry 和模式执行层。
```

建议新增：

```text
lucode/entry_fast.py 或 entry.py 顶部手写 fast dispatch
  在 argparse 构建前处理 --version、-v、help、completion 等超轻命令。

runtime/startup/profiler.py
  记录启动阶段耗时：入口解析、工作区发现、目录刷新、模型探测、MCP 准备、首屏渲染。
```

验收目标：

- `lucode --version` 感知时间小于 50ms，不导入 `main.py`、`mcp_servers`、`agents`。
- `lucode --help` 不触发模型目录刷新和 provider 探测。
- `lucode chat` 首屏先显示欢迎页，再异步或延后进行可慢启动的刷新任务。

### 3.5 早期输入捕获

启动 `lucode chat` 时，如果用户在首屏完成前已经开始输入，输入不应丢失。建议：

```text
entry.py chat 路径
  1. 进入 raw/stdin 轻量读取模式。
  2. 启动输入缓冲线程。
  3. import main.py 和初始化运行时。
  4. chat_loop 启动后重放缓冲输入。
```

第一阶段可以只捕获普通字符和回车；第二阶段接入 `prompt_toolkit` 后再统一处理方向键、鼠标、历史搜索和多行输入。

---

## 4. C1.5：蓝色鹿头启动仪表盘

### 4.1 问题

当前启动界面输出大量文字：

- 模型优先级过长。
- MCP / 备份 / 命令说明都堆在首屏。
- 第一眼不像产品，更像调试日志。

### 4.2 目标界面

启动后显示“蓝色像素鹿头 + 像素 lucode 标识 + 中文状态栏”：

```text
        [像素 lucode 标识]
        [蓝色像素鹿头]

                         项目    D:\pycharm\code\some_project
                         配置    .lucode 已发现
                         模式    solo 单代理
                         模型    deepseek-v4-pro  +3 备用
                         隐私    允许云端
                         工具    按需加载
                         备份    已开启

                         输入 / 查看命令
```

### 4.3 Logo 渲染方案

后续做一个 logo 转换器，不建议长期手写 ASCII：

```text
输入：
  blue-deer-head.png / svg

输出：
  rich    高精度彩色像素块
  color   ANSI 蓝色字符块
  plain   无颜色纯文本 fallback
```

支持：

- 终端宽度检测，窄屏隐藏 logo。
- `NO_COLOR=1` 自动无颜色。
- `--no-logo` 禁用 logo。
- `--verbose` 显示旧式详细启动日志。
- `lucode` 标识也用像素字体渲染，放在鹿头上方。

### 4.4 不同模式状态栏

`solo`：

```text
项目    D:\path\to\project
配置    .lucode 已发现
模式    solo 单代理
模型    deepseek-v4-pro +3 备用
隐私    允许云端
工具    按需加载
备份    已开启
```

`serial`：

```text
项目    D:\path\to\project
模式    serial 串行多代理
主脑    deepseek-v4-pro
执行    多任务串行
副脑    final-synthesizer
审查    计划校验开启
并行    关闭
回滚    checkpoint 开启
```

`full`：

```text
项目    D:\path\to\project
模式    full 审核并行
主脑    deepseek-v4-pro
执行组  多 Agent 安全批次
副脑    synthesizer / auditor
审查    plan + patch + final audit
账本    patch ledger 开启
回滚    checkpoint 开启
并行    仅无冲突任务
```

详细信息移动到：

```text
/help
/status
/model
/config
/mcp
/skills
```

---

## 5. C2：配置、API Key、Provider、模型优先级

### 5.1 设计原则

借鉴 opencode：凭据和配置分开。

```text
API key / secret:
  用户级 auth 文件保存，不进项目，不进日志。

Provider / model / mode / priority:
  项目级或用户级 config 保存，可共享非敏感配置。
```

当前项目已有 `.env` 动态模型发现能力，但还不够产品化：

- 已支持 `MODEL_<NAME>_API_KEY`、`MODEL_<NAME>_BASE_URL`、`MODEL_<NAME>_MODEL`。
- 已支持 `MODEL_<GROUP>_MODELS`，可在同一个 key / base_url 下展开多个模型。
- 已支持三脑优先级环境变量。
- 但 `/model` 目前偏查看，不够适合普通用户交互切换。
- 还缺少 Provider 预设库、`/connect` 厂商连接流程、`/models` 选择模型流程。

因此 C2 的目标不是废掉现有模型图书馆，而是在它前面加一层更友好的 Provider/Auth/Config UI。

### 5.2 用户级凭据

新增：

```text
~/.lucode/auth.json
```

只存敏感信息：

```json
{
  "providers": {
    "deepseek": {
      "api_key": "..."
    },
    "mimo": {
      "api_key": "..."
    },
    "openrouter": {
      "api_key": "..."
    }
  }
}
```

对应命令：

```text
lucode auth login
lucode auth list
lucode auth logout deepseek
/connect
```

`/connect` 应提供交互式 Provider 添加菜单，不要求普通用户手改 `.env`。

### 5.3 项目级配置

新增：

```text
.lucode/config.toml
```

示例：

```toml
mode = "solo"
privacy = "local_first"

[model]
primary = "deepseek/deepseek-v4-pro"
fallback = [
  "mimo/mimo-v2.5-pro",
  "deepseek/deepseek-v4-flash"
]

[roles]
query_refiner = ["deepseek/deepseek-v4-flash", "mimo/mimo-v2.5"]
orchestrator = ["deepseek/deepseek-v4-pro", "mimo/mimo-v2.5-pro"]
final_synthesizer = ["deepseek/deepseek-v4-pro"]

[provider.deepseek]
base_url = "https://api.deepseek.com"
homepage = "https://platform.deepseek.com"
models = ["deepseek-v4-pro", "deepseek-v4-flash"]

[provider.ollama]
base_url = "http://localhost:11434/v1"
homepage = "http://localhost:11434"
local = true
models = ["qwen3:8b"]
```

### 5.4 配置优先级

建议优先级：

```text
命令行参数
> 项目 .lucode/config.toml
> 用户 ~/.lucode/config.toml
> 用户 ~/.lucode/auth.json
> 环境变量 / .env
> 默认值
```

`.env` 先保留兼容，后续提供迁移：

```text
lucode config migrate-env
```

迁移目标：

- secret 进入 `~/.lucode/auth.json`
- provider / model / priority 进入 `.lucode/config.toml`

### 5.5 模型选择交互

新增：

```text
/models
/model select
/model provider
/model priority
/model role orchestrator
```

示例：

```text
请选择主模型

> deepseek/deepseek-v4-pro      已配置 | 主脑推荐
  mimo/mimo-v2.5-pro            已配置 | 代码任务
  ollama/qwen3:8b               本地 | 工具支持未知
```

支持：

- 用户自定义主模型。
- 用户自定义 fallback 顺序。
- 用户分别配置主脑、前置优化、副脑、执行模型。
- 隐私模式自动过滤云端模型。

### 5.6 Provider 预设库

新增：

```text
catalogs/provider_catalog.json
```

用途：

- 内置常见厂商的显示名、官网链接、默认请求地址、兼容协议和推荐模型。
- 用户只需要选择厂商并填写 API key，即可进入 `/models` 选择模型。
- 避免普通用户手动查 base_url、拼环境变量。

示例：

```json
{
  "deepseek": {
    "display_name": "DeepSeek",
    "homepage": "https://platform.deepseek.com",
    "base_url": "https://api.deepseek.com",
    "compatible_type": "openai_compatible",
    "models": ["deepseek-v4-pro", "deepseek-v4-flash"],
    "supports_tools": true
  },
  "siliconflow": {
    "display_name": "硅基流动",
    "homepage": "https://cloud.siliconflow.cn",
    "base_url": "https://api.siliconflow.cn/v1",
    "compatible_type": "openai_compatible",
    "models": [
      "Qwen/Qwen3-8B",
      "deepseek-ai/DeepSeek-R1"
    ],
    "supports_tools": "probe"
  },
  "ollama": {
    "display_name": "Ollama",
    "homepage": "https://ollama.com",
    "base_url": "http://localhost:11434/v1",
    "compatible_type": "openai_compatible",
    "local": true,
    "models": []
  }
}
```

第一批建议内置：

```text
DeepSeek
MiMo
Qwen / DashScope
SiliconFlow
OpenRouter
OpenAI-compatible custom
Ollama
LM Studio
llama.cpp server
```

后续可以继续扩展 Anthropic、Gemini、Groq、Together、Moonshot、MiniMax、xAI 等。

### 5.7 `/connect` 厂商连接流程

目标体验：

```text
/connect
  选择厂商
  输入 API key
  使用预设 homepage / base_url
  连接测试
  保存凭据到 ~/.lucode/auth.json
  写入 provider 到 .lucode/config.toml 或 ~/.lucode/config.toml
  进入 /models 选择主模型和 fallback
```

示例：

```text
请选择模型厂商

> DeepSeek        https://platform.deepseek.com
  硅基流动        https://cloud.siliconflow.cn
  OpenRouter      https://openrouter.ai
  Ollama          本地模型
  自定义中转      自定义官网和请求地址
```

保存结果：

```json
{
  "providers": {
    "deepseek": {
      "api_key": "..."
    }
  }
}
```

Provider 非敏感配置：

```toml
[provider.deepseek]
display_name = "DeepSeek"
homepage = "https://platform.deepseek.com"
base_url = "https://api.deepseek.com"
compatible_type = "openai_compatible"
models = ["deepseek-v4-pro", "deepseek-v4-flash"]
```

### 5.8 `/models` 模型选择与优先级

`/models` 应展示所有已连接 Provider 的模型：

```text
请选择主模型

> deepseek/deepseek-v4-pro      已配置 | 主脑推荐 | tools
  deepseek/deepseek-v4-flash    已配置 | 低成本
  siliconflow/Qwen/Qwen3-8B     已配置 | 中文/本地友好
  ollama/qwen3:8b               本地 | 隐私优先
```

选择后可写入：

```toml
[model]
primary = "deepseek/deepseek-v4-pro"
fallback = [
  "mimo/mimo-v2.5-pro",
  "deepseek/deepseek-v4-flash"
]

[roles]
query_refiner = ["deepseek/deepseek-v4-flash"]
orchestrator = ["deepseek/deepseek-v4-pro", "mimo/mimo-v2.5-pro"]
final_synthesizer = ["deepseek/deepseek-v4-pro"]
```

同时保留自动推荐：

- 主脑优先高推理、支持 JSON / tools、规划适配好的模型。
- 执行 Agent 优先支持工具调用、代码能力强、成本可控的模型。
- 汇总副脑优先高质量长文本模型。
- `offline` 模式只展示本地 Provider。
- `local_first` 模式优先本地，但允许已配置云端 fallback。

### 5.9 自定义 base_url 与中转模型

自定义中转必须区分两类地址：

```text
homepage
  给用户看的官网 / 控制台 / 充值 / key 管理页面。

base_url
  真正请求模型的 API 地址。
  模型调用只能走 base_url，不能走 homepage。
```

示例：

```toml
[provider.my_proxy]
display_name = "我的中转服务"
type = "openai_compatible"

# 官网或控制台地址，只用于展示、帮助用户打开控制台。
homepage = "https://example-proxy.com"

# 真正请求模型的地址。模型调用只走这个地址。
base_url = "https://api.example-proxy.com/v1"

models = [
  "gpt-4o",
  "claude-3-5-sonnet",
  "deepseek-chat"
]
```

API key 仍然只放用户级 auth：

```json
{
  "providers": {
    "my_proxy": {
      "api_key": "..."
    }
  }
}
```

交互流程：

```text
/connect custom
  1. 输入 Provider ID：my_proxy
  2. 输入显示名称：我的中转服务
  3. 输入官网链接 homepage：https://example-proxy.com
  4. 输入请求地址 base_url：https://api.example-proxy.com/v1
  5. 输入 API key
  6. 输入模型列表，或尝试从 /models 接口拉取
  7. 测试连接
  8. 保存配置
```

展示时：

```text
Provider      我的中转服务
官网          https://example-proxy.com
请求地址      https://api.example-proxy.com/v1
模型          gpt-4o, claude-3-5-sonnet, deepseek-chat
状态          已连接
```

安全规则：

- `homepage` 只用于显示和帮助用户定位控制台。
- `base_url` 才是实际请求地址。
- 模型调用不得 fallback 到官网链接。
- `base_url` 修改属于敏感配置变更，应提示用户确认。
- 项目配置可以声明中转 Provider，但 API key 不能进入项目。

### 5.10 与当前模型图书馆的衔接

当前 `model_catalog.py` 已经支持动态模型发现、共享 base_url、多模型展开和 fallback 选择。后续实现时建议：

```text
provider_catalog.json
  -> 生成 Provider 定义

~/.lucode/auth.json
  -> 提供 API key

.lucode/config.toml
  -> 提供 provider/base_url/models/priority

model_catalog.py
  -> 合并上述来源，继续生成现有 Model Catalog
```

这样不需要推倒重写现有模型图书馆，只是把 `.env` 人工配置升级成更友好的配置 UI。

### 5.11 多 Provider 原生 SDK 抽象

`ARCHITECTURE_GAP_ANALYSIS.md` 指出当前 `ModelRegistry.get_model()` 仍然把所有外部模型统一包成 OpenAI-compatible 调用，这对 DeepSeek、OpenRouter、自定义中转很合适，但会限制 Anthropic、Gemini、Bedrock 等原生能力：

```text
当前：
  ModelRegistry -> AsyncOpenAI(api_key, base_url) -> OpenAIChatCompletionsModel

目标：
  ModelRegistry -> ProviderRegistry -> ProviderAdapter -> LanguageModel 统一包装
```

建议新增目录：

```text
runtime/providers/
  __init__.py
  registry.py              SDK 懒加载与缓存
  base.py                  ProviderAdapter 协议
  openai_compatible.py     复用当前逻辑
  anthropic_provider.py    Anthropic 原生 SDK 适配
  google_provider.py       Gemini 原生 SDK 适配
  bedrock_provider.py      AWS Bedrock 适配
  ollama_provider.py       本地模型适配
  transform.py             消息转换管线
```

Provider Adapter 职责：

```text
输入：
  provider_id
  sdk_type
  auth
  provider options
  model options
  privacy policy

输出：
  可供 Agent 层调用的 model 对象，或统一的 chat/stream/tool 调用接口。
```

第一阶段不替换全部 Agent SDK，只把 `ModelRegistry.get_model()` 的 OpenAI-compatible 创建逻辑下沉到 `runtime/providers/openai_compatible.py`，再逐步扩展其它 SDK。

### 5.12 Provider→Model 结构化配置

opencode 的优势是 Provider、模型和模型 options 有明确层级。Lucode 的 `.lucode/config.toml` 建议继续 TOML，但增强 schema：

```toml
[provider.anthropic]
display_name = "Anthropic"
sdk_type = "anthropic"
homepage = "https://console.anthropic.com"
base_url = "https://api.anthropic.com"
models = ["claude-3-5-sonnet-latest"]

[provider.anthropic.options]
version = "2023-06-01"

[provider.anthropic.model."claude-3-5-sonnet-latest"]
thinking_budget_tokens = 16000
prompt_cache = true
max_tokens = 8192

[provider.openrouter]
display_name = "OpenRouter"
sdk_type = "openai_compatible"
homepage = "https://openrouter.ai"
base_url = "https://openrouter.ai/api/v1"
models = ["anthropic/claude-3.5-sonnet", "openai/gpt-4o"]
```

合并优先级：

```text
命令行临时覆盖
> 当前 Session 模型选择
> 项目 .lucode/config.toml 的 model options
> 项目 .lucode/config.toml 的 provider options
> 用户 ~/.lucode/config.toml
> 用户 ~/.lucode/auth.json
> 环境变量 / .env
> provider_catalog.json 默认值
```

注意：

- API key 仍只允许在 `auth.json` 或环境变量中出现。
- 项目配置可以声明 `sdk_type`、`base_url`、`models` 和非敏感 options。
- `homepage` 只用于展示和控制台入口，模型请求仍只走 `base_url`。

### 5.13 消息转换管线

跨厂商模型最容易出错的不是 key，而是消息格式。建议新增 `runtime/providers/transform.py`：

```text
通用转换：
  - 移除空内容消息。
  - 标准化 system/user/assistant/tool 的顺序。
  - 规范 tool_call_id，避免 Claude/Mistral 对字符集或长度报错。
  - 对过长工具输出做摘要或折叠标记。

Anthropic:
  - 空 content block 过滤。
  - thinking / prompt cache 写入 provider options。
  - tool_use / tool_result block 转换。

Mistral / OpenAI-compatible:
  - 修复 tool 消息前缺 assistant tool_call 的序列。

推理模型：
  - reasoning_content 单独提取，避免污染普通 assistant 文本。
```

验收目标：

- 同一轮消息可分别转换成 OpenAI-compatible、Anthropic、Gemini 的请求格式。
- 转换结果不包含空字符串 content、不合法 tool id、错序 tool result。
- 转换前后可生成调试摘要，但不得打印 secret。

### 5.14 SDK 实例缓存

避免每次 `get_model()` 都创建新的 SDK client。建议：

```text
cache_key = hash(provider_id, sdk_type, base_url, auth_fingerprint, provider_options)
```

缓存策略：

- 同一 Provider + 同一 base_url + 同一 auth 指纹复用 SDK client。
- API key 不进入日志，auth 指纹只存哈希前缀。
- `.lucode/config.toml` 或 `auth.json` 变更后失效。
- `lucode doctor` 可显示 SDK 缓存数量和 Provider 健康状态。

---

## 6. C2.6：工作区 `.lucode` 扩展

### 6.1 目标

Lucode 安装到 C 盘或其它固定位置后，用户可以在任意项目目录运行：

```bash
lucode
```

只要当前目录或父目录存在 `.lucode/`，就识别为当前项目工作区，并加载项目级 skills 和 MCP。

### 6.2 Skills 分层

来源：

```text
内置核心 skills:
  APP_HOME/skills

用户全局 skills:
  ~/.lucode/skills

当前项目 skills:
  WORKSPACE_ROOT/.lucode/skills
```

合并 catalog 时记录：

```text
source = core | user | workspace
path
selectable
internal
risk_level
```

### 6.3 核心 Skills 保护

必须保护以下系统级 skills，不允许项目 `.lucode/skills` 覆盖：

```text
task_router
query_refiner
orchestrator_planner
final_synthesizer
```

理由：

- 这些是 Lucode 正常运行的系统大脑。
- 如果陌生项目可以覆盖它们，打开项目就可能被提示词劫持。
- 项目可以扩展 Lucode，但不能接管 Lucode 的系统大脑。

### 6.4 MCP 分层

来源：

```text
内置 MCP:
  APP_HOME/mcp_servers

用户全局 MCP:
  ~/.lucode/mcp

当前项目 MCP:
  WORKSPACE_ROOT/.lucode/mcp
```

项目 MCP 默认不自动运行，应先进入“待信任 / 待启用”状态。

建议状态：

```text
trusted = false
enabled = false
risk_level = unknown
source = workspace
```

首次使用项目级 MCP 前必须询问用户是否信任。

### 6.5 新增查看命令

```text
/skills
  查看当前工作区 .lucode/skills。

/mcp
  查看当前工作区 .lucode/mcp。

/skills_all
  查看全部 skills：内置 + 用户全局 + 当前工作区。

/mcp_all
  查看全部 MCP：内置 + 用户全局 + 当前工作区。
```

示例：

```text
当前项目 Skills：D:\xxx\.lucode\skills

- api-reviewer      项目 API 规范审查
- test-writer       当前项目测试生成规则

提示：核心系统 Skills 已隐藏，可用 /skills_all 查看。
```

`/skills_all`：

```text
内置核心：
- orchestrator_planner
- final_synthesizer
- project_explorer

用户全局：
- my-writing-style

当前项目：
- api-reviewer
- test-writer
```

---

## 7. C3：权限和审批系统

### 7.1 默认策略

借鉴 Claude Code / opencode，但 Lucode 默认更保守：

```text
read
  默认允许，但拒绝 .env、secret、token、私钥。

edit / write
  默认询问。

delete
  默认询问或拒绝，必须备份。

shell
  默认询问，危险命令拒绝。

git
  status / diff / log 默认允许。
  commit 询问。
  push / reset / clean 默认拒绝。

web
  受 privacy mode 控制。

mcp
  按来源和风险等级控制。
```

### 7.2 项目权限文件

新增：

```text
.lucode/permissions.toml
```

支持：

```text
allow / ask / deny
路径规则
命令规则
Provider 规则
MCP 工具规则
```

示例：

```toml
[read]
default = "allow"
deny = [".env", "**/*.pem", "**/*secret*"]

[write]
default = "ask"
deny = [".git/**", ".lucode/auth.json"]

[shell]
default = "ask"
deny = ["git reset --hard", "git clean", "rm -rf"]

[mcp.workspace]
default = "ask"
```

### 7.3 审批体验

高风险动作审批时展示：

```text
动作
目标路径
影响范围
是否备份
命令内容
MCP 来源
风险等级
```

用户可选：

```text
允许一次
本会话允许
对该规则允许
拒绝
编辑指令
```

### 7.4 5 层权限防御增强

当前审批已经有“允许一次 / 本会话允许 / 同类规则允许 / 编辑指令”的体验，但与 Claude Code 的多层防御相比，还需要补齐静态规则和命令分析：

```text
Layer 1: Permission Mode
  trusted / normal / restricted / offline，不同信任等级影响默认 ask/allow/deny。

Layer 2: Rule Matching
  从 .lucode/permissions.toml 和 ~/.lucode/permissions.toml 读取路径、命令、工具、Provider 规则。

Layer 3: Command AST / Token 分析
  shell 命令在审批前解析，不只做字符串包含判断。
  第一阶段用 shlex/PowerShell token 规则；后续再考虑 tree-sitter。

Layer 4: User Confirmation
  高风险动作展示 diff、路径、备份状态、MCP 来源、风险等级。
  增加 200ms 防误触延迟，避免回车连击误批准。

Layer 5: Hook Validation
  执行前后调用用户 Hooks，允许组织或项目自定义策略拦截。
```

命令风险示例：

```text
low:
  git status
  git diff
  pytest tests/test_x.py

medium:
  git commit
  npm install
  pip install

high:
  rm/rmdir/remove-item
  git reset
  git clean
  npm publish
  curl | sh
```

验收目标：

- 写入、删除、shell、git commit/push 均经过统一权限解释器。
- PowerShell 和 Bash 命令都能提取主命令、参数、路径和重定向。
- 危险命令即使用户选择“本会话允许同类工具”，也仍需二次确认或拒绝。

### 7.5 Hooks 系统

Hooks 是项目可扩展性的关键，建议新增：

```text
runtime/hooks/
  __init__.py
  schema.py
  manager.py
  runner.py
```

配置位置：

```toml
[hooks.PreToolUse]
matcher = "workspace_edit|command_runner"
command = "python .lucode/hooks/pre_tool.py"

[hooks.PostToolUse]
matcher = "*"
command = "python .lucode/hooks/audit_log.py"
```

事件类型第一阶段：

```text
SessionStart
SessionEnd
PreMessage
PostMessage
PreToolUse
PostToolUse
PreCompaction
PostCompaction
```

退出码约定：

```text
0  允许继续
2  阻止当前动作，并把原因注入模型上下文
其它 记录警告但默认继续，除非权限模式为 restricted
```

安全边界：

- 项目 Hooks 默认不自动信任。
- 第一次加载 `.lucode/hooks` 前必须询问。
- Hooks 输出不得包含 API key；日志进入 `.agent_quarantine/logs` 或 `.lucode/sessions`。

---

## 8. C4：工具系统统一抽象

### 8.1 当前已有工具

```text
project_filesystem_readonly
code_locator
workspace_edit
safe_backup
command_runner
git_tools
web_search
```

### 8.2 Tool Registry

需要把 MCP 工具统一登记成 Tool Registry：

```text
工具名
能力类型
来源 core/user/workspace
风险等级
是否需要审批
是否允许 offline
预算限制
日志策略
备份策略
可用模型要求
```

### 8.3 保留项目强项

必须保留：

- `workspace_edit` strict sha256。
- 写入/删除前 zip 备份。
- 操作日志。
- 命令执行不经过 shell 的安全策略。
- Git 工具只读优先。
- offline / local_first / cloud_allowed 隐私边界。

---

## 9. C5：TUI、过程日志、Diff 审批

### 9.1 目标

让终端体验从“打印日志”升级为“可浏览过程的工作台”：

```text
左侧：任务步骤和工具调用
右侧：diff / 命令预览 / 文件变更
底部：状态栏 + 审批操作
```

### 9.2 任务列表

复杂任务显示任务状态：

```text
[✓] 扫描相关文件
[>] 生成修改计划
[ ] 执行 patch
[ ] 运行测试
[ ] 审核 diff
```

### 9.3 底部状态栏

参考 Claude Code 的 statusline 思路。

`solo`：

```text
solo | deepseek-v4-pro | cloud_allowed | git:main +2 | ctx 18% | /help
```

`serial`：

```text
serial | 主脑 deepseek-v4-pro | 副脑 mimo-v2.5 | 审查 ON | 并行 OFF
```

`full`：

```text
full | 主脑 deepseek-v4-pro | Auditor ON | PatchLedger ON | Checkpoint ON | 并行安全批次
```

### 9.4 流式输出与 Spinner 状态机

当前项目已有基础流式输出和任务状态框，但还不是完整 TUI 渲染引擎。建议先做轻量状态机，不急于上复杂框架：

```text
requesting
  已发送请求，等待模型首 token。

thinking
  推理模型正在输出 reasoning 或未产生可见文本。

responding
  正在流式输出最终回答。

tool-use
  正在执行工具，展示工具名、风险等级、目标文件或命令摘要。

stalled
  超过阈值无 token/无工具结果，提示可能网络慢或模型卡住。
```

展示建议：

```text
状态 | 模式 serial | 主脑 deepseek-v4-pro | thinking 12s | 工具 2 | ctx 41% | /help
```

第一阶段用普通 ANSI 文本和定时刷新；第二阶段再接入 `prompt_toolkit` bottom toolbar；更完整 TUI 后续再考虑 Textual/Rich。

### 9.5 Token 和成本实时显示

`TokenLoggerHooks` 目前偏回合后汇总。建议新增会话级统计：

```text
SessionUsage
  input_tokens
  output_tokens
  reasoning_tokens
  cached_tokens
  estimated_cost
  per_model_breakdown
```

新增命令：

```text
/cost
  查看当前会话 token 和费用估算。

/usage
  查看按 Agent / 模型 / 工具分组的调用统计。
```

费用估算来源：

- `catalogs/provider_catalog.json` 或独立 `catalogs/pricing_catalog.json`。
- 没有定价时显示“未知”，不能胡乱估算。

### 9.6 Context 压缩管线

Claude Code 的长对话稳定性来自分级压缩。Lucode 当前只保留最近几轮文本，容易丢失长期任务状态。建议新增：

```text
runtime/context/
  __init__.py
  compaction.py
  session_state.py
  message_store.py
```

压缩等级：

```text
Level 1: TRUNCATE
  裁剪大型工具输出，只保留路径、命令、摘要、错误关键行。

Level 2: DEDUPLICATE
  移除重复的文件片段、重复报错、重复计划。

Level 3: FOLD
  折叠已完成阶段，保留结论和可恢复引用。

Level 4: SUMMARIZE
  使用汇总副脑生成结构化摘要。
```

触发条件：

```text
ctx >= 80%
  开始 truncate / deduplicate。

ctx >= 90%
  fold 已完成阶段。

ctx >= 95%
  summarize，并强制保留最近用户消息、最近编辑文件、当前计划、未完成任务和审批状态。
```

结构化摘要字段：

```text
用户目标
当前阶段
已修改文件
已运行测试
失败与原因
剩余任务
活跃 skills
关键路径和符号
不能丢失的用户偏好
```

### 9.7 会话 JSONL 持久化

建议把交互消息从纯内存 `recent_turns` 升级为追加式 JSONL：

```text
.lucode/sessions/
  2026-05-11T10-30-00Z.jsonl
  latest.json
```

每条消息记录：

```json
{
  "type": "assistant|user|tool|approval|summary|checkpoint",
  "timestamp": "2026-05-11T10:30:00Z",
  "session_id": "...",
  "turn_id": "...",
  "content": "...",
  "metadata": {}
}
```

新增命令：

```text
/resume
  选择并恢复历史会话。

/fork-session
  从当前会话复制出新分支。

/sessions
  列出最近会话、时间、工作区、最后任务。
```

验收目标：

- 关闭终端后可恢复上一会话。
- 压缩摘要和 checkpoint 可一起写入 JSONL。
- 会话文件不保存 API key。

---

## 10. C5.5：Slash 命令菜单

### 10.1 目标

输入 `/` 后弹出命令菜单，支持上下键、鼠标、中文说明、模糊过滤。

示例：

```text
/status      查看当前运行状态、MCP、回滚点
/model       查看模型优先级和可用状态
/mode        切换 solo / serial / full
/privacy     查看或切换隐私模式
/plan        只生成计划，不执行
/diff        查看当前 Git diff 摘要
/rollback    回滚最近一轮修改
/config      查看配置
/connect     添加模型 Provider API Key
/models      选择当前模型
/skills      查看当前项目 Skills
/mcp         查看当前项目 MCP
/skills_all  查看全部 Skills
/mcp_all     查看全部 MCP
```

### 10.2 补全能力

第一阶段：

- `/` 命令补全。
- 中文说明。
- 上下键选择。
- 鼠标选择。
- `/mo` 模糊过滤 `/model`、`/mode`。
- `/mode ` 后提示 `solo / serial / full`。

第二阶段：

- `@` 文件路径补全。
- `!` shell 快捷模式。
- `Ctrl+R` 历史搜索。
- `Shift+Enter` 多行输入。
- `Ctrl+L` 重绘界面。

### 10.3 技术建议

Python 当前路线建议先用：

```text
prompt_toolkit
```

适合：

- 自动补全。
- 上下键选择。
- 鼠标支持。
- 命令历史。
- 中文提示。
- 底部 toolbar。

更完整 TUI 后续再考虑：

```text
Textual / Rich
```

---

## 11. C5.6：Skills / MCP 浏览与管理

### 11.1 命令

```text
/skills
/mcp
/skills_all
/mcp_all
```

### 11.2 管理命令预留

后续可增加：

```text
lucode skill list
lucode skill enable <id>
lucode skill disable <id>
lucode mcp list
lucode mcp trust <id>
lucode mcp enable <id>
lucode mcp disable <id>
```

### 11.3 展示字段

Skills：

```text
id
显示名
来源 core/user/workspace
是否可选
默认模型
允许 MCP
风险等级
说明
```

MCP：

```text
id
显示名
来源 core/user/workspace
工具列表
风险等级
是否信任
是否启用
是否需要审批
说明
```

---

## 12. C5.8：Agent Loop 与执行内核长期增强

### 12.1 为什么不立刻重写 Agent Loop

`ARCHITECTURE_GAP_ANALYSIS.md` 指出 Claude Code 的核心优势之一是自研双层 Agent Loop，可以完全控制消息、工具、错误反馈、上下文压缩和继续站点。Lucode 当前使用 OpenAI Agents SDK Runner，这让项目能快速获得可运行能力，但也带来黑盒限制。

建议判断：

```text
C1-C5:
  继续使用 SDK Runner，先把产品入口、配置、安全、状态、发布做好。

C5.8-C6:
  在 SDK Runner 外围补消息持久化、Context 压缩、Hooks、Provider Transform。

C7:
  再评估是否以可插拔方式引入自研 AgentLoop，而不是一次性替换全部执行层。
```

### 12.2 可插拔 Agent Loop 设计

新增：

```text
runtime/core/
  agent_loop.py
  turn_state.py
  continuation.py
  tool_executor.py
  error_feedback.py
```

运行接口：

```python
class AgentLoop:
    async def run_turn(self, session, user_input, *, mode, model_selector, tool_registry):
        ...
```

继续站点建议：

```text
model_response
tool_call
tool_result
tool_error
permission_denied
context_compacted
max_turn_guard
user_stop
```

工具执行策略：

```text
只读工具:
  可并发执行，例如 read_file、search_files、git status、web fetch。

写入工具:
  必须串行，必须 checkpoint，必须审批，必须写 patch ledger。

shell:
  先权限分析，再审批，再执行；输出过长进入 Context 压缩。
```

### 12.3 错误即反馈

工具失败、权限拒绝、模型格式错误不应只作为终端报错，而应作为结构化 ToolResult 回灌给模型：

```text
工具失败：
  error_type
  retryable
  command/path/tool_name
  stderr 摘要
  建议替代动作

权限拒绝：
  rejected_by = user | policy | hook
  reason
  allowed_alternatives
```

这样模型可以自动调整策略，例如从写文件改为先展示 patch，或从危险 shell 改为只读检查。

### 12.4 Swarm / Worktree 多 Agent 方向

现有 `serial/full` 已能做串行或安全批次并行，但还不是 Claude Code 风格的 AgentTool / Swarm。长期建议：

```text
Coordinator:
  只规划和分派，不直接改文件。

Worker:
  每个 Worker 负责明确文件范围或任务范围。

Isolated Worktree:
  高风险并行任务进入独立 worktree，合并前做冲突检查。

Mailbox:
  多 Agent 通过结构化消息交换状态，不互相覆盖上下文。
```

验收目标：

- 多 Agent 并行时写入范围不冲突。
- Worker 不能回滚其它 Worker 的变更。
- 合并前必须运行 diff 审核和测试。
- 失败 Worker 的错误进入 Coordinator 汇总，而不是直接污染主工作区。

---

## 13. C6：npm 安装和发布

### 13.1 目标体验

```bash
npm install -g lucode
lucode
```

或：

```bash
npx lucode
```

### 13.2 阶段一：npm wrapper

npm 包提供 `lucode` 命令，内部启动 Python 入口：

```json
{
  "name": "lucode",
  "bin": {
    "lucode": "./bin/lucode.js"
  }
}
```

优点：

- 实现快。
- 可先验证 npm 安装体验。

缺点：

- 用户机器必须有 Python 和依赖。

### 13.3 阶段二：平台二进制

更接近 opencode / Claude Code：

```text
lucode
@lucode/cli-win32-x64
@lucode/cli-linux-x64
@lucode/cli-darwin-arm64
@lucode/cli-darwin-x64
```

安装时根据平台下载或选择对应二进制。

底层可选：

```text
PyInstaller
Nuitka
```

### 13.4 发布流程

```text
GitHub Actions
  -> 跑测试
  -> 打包平台二进制
  -> 上传 GitHub Release
  -> 发布 npm wrapper + platform packages
```

---

## 14. 不建议做或明确拒绝的方向

### 14.1 不建议重写为 TypeScript / Bun

opencode 是 TS/Bun 架构，但本项目 Python 内核已经成型，C 阶段重点是产品外壳，不是推倒重写。

### 14.2 不允许项目覆盖核心系统 Skills

项目 `.lucode/skills` 可以扩展，但不能覆盖：

```text
task_router
query_refiner
orchestrator_planner
final_synthesizer
```

### 14.3 不允许项目 MCP 自动信任

项目 `.lucode/mcp` 默认应为未信任。首次使用前必须审批。

### 14.4 不在启动页展示大量调试文本

启动页只展示核心状态。详细内容进入：

```text
/help
/status
/config
/model
/mcp
/skills
```

### 14.5 不把 API key 写进项目配置

项目 `.lucode/config.toml` 不存 API key。API key 只放用户级 auth 或环境变量。

### 14.6 不在 C1-C5 强行替换 OpenAI Agents SDK

自研 Agent Loop 是长期增强，不应在产品入口尚未稳定时强行替换。C1-C5 应先完成可安装、可配置、可验收的 CLI 产品层；C5.8 以后再以可插拔方式做 Agent Loop 实验。

### 14.7 不让项目 Hooks 静默执行

项目 `.lucode/hooks` 和项目 MCP 一样，默认未信任。第一次运行前必须展示来源、命令、风险和权限，并允许用户拒绝。

---

## 15. 推荐落地顺序

### 第零批：启动性能和可观测性

```text
0. lucode --version / --help 快速路径，不导入完整 runtime。
1. 启动阶段耗时 profiler。
2. chat 首屏先显示，模型目录刷新和探测延后或并行。
3. 早期输入捕获 MVP。
```

### 第一批：低风险体验升级

```text
4. 启动文字瘦身，增加 /help。
5. 蓝色鹿头仪表盘 MVP。
6. 启动状态栏显示完整 WORKSPACE_ROOT。
7. /skills、/mcp、/skills_all、/mcp_all 只读命令。
```

### 第二批：输入体验

```text
8. prompt_toolkit 输入层。
9. / 命令菜单。
10. 中文命令说明。
11. @ 文件路径补全。
12. Ctrl+R 历史搜索、Shift+Enter 多行输入。
```

### 第三批：配置系统和 Provider

```text
13. WorkspaceContext：APP_HOME / USER_HOME / WORKSPACE_ROOT。
14. .lucode/config.toml。
15. ~/.lucode/auth.json。
16. provider_catalog.json 厂商预设库。
17. /connect 厂商连接流程。
18. /models 模型选择和角色优先级配置。
19. custom provider / 中转模型 homepage + base_url。
20. 旧 .env 迁移命令。
21. ProviderRegistry：先迁移 OpenAI-compatible 逻辑，再扩展 Anthropic/Gemini/Bedrock。
22. MessageTransformer：空内容过滤、tool id 消毒、序列修复。
23. SDK client 缓存。
```

### 第四批：扩展、权限和 Hooks

```text
24. 项目级 .lucode/skills 加载。
25. 用户级 ~/.lucode/skills 加载。
26. 项目级 .lucode/mcp 发现。
27. permissions.toml。
28. MCP trust / enable 流程。
29. Tool Registry。
30. 5 层权限解释器。
31. Hooks：PreToolUse / PostToolUse / SessionStart / SessionEnd。
```

### 第五批：TUI、Context 和会话

```text
32. TUI 任务列表和 diff 审批。
33. Spinner 状态机。
34. /cost 和 /usage。
35. ContextCompactor：truncate / deduplicate / fold / summarize。
36. JSONL SessionStore。
37. /resume、/fork-session、/sessions。
```

### 第六批：产品化发布

```text
38. lucode doctor 增强 Provider、Hooks、Context、SDK cache 检查。
39. npm wrapper。
40. PyInstaller / Nuitka 平台二进制。
41. GitHub Release + npm 自动发布。
```

### 第七批：长期执行内核

```text
42. 可插拔 AgentLoop 实验。
43. 错误即反馈 ToolResult。
44. 只读工具并发、写入工具串行。
45. Isolated worktree 多 Agent。
46. Coordinator / Worker / Mailbox。
```

---

## 16. 验收标准

### CLI

- 在任意目录执行 `lucode` 可以启动。
- 如果当前目录有 `.lucode/`，启动页显示完整项目路径。
- `python main.py` 仍可兼容启动。
- `lucode --version` 和 `lucode --help` 不导入完整 Agent/MCP 运行时。
- `lucode doctor` 能输出工作区、Provider、权限、Hooks、SDK cache、Context 状态。

### 启动界面

- 默认启动一屏以内。
- 不再打印完整模型优先级和长命令说明。
- `/status`、`/model`、`/config` 可查看详细信息。

### Skills / MCP

- `/skills` 只显示当前项目 skills。
- `/mcp` 只显示当前项目 MCP。
- `/skills_all` 显示 core/user/workspace 全部来源。
- `/mcp_all` 显示 core/user/workspace 全部来源。
- 项目 skill 不能覆盖核心 system skill。
- 项目 MCP 首次使用必须信任确认。

### 模型配置

- API key 不出现在项目配置中。
- 用户可以通过 `/connect` 添加 Provider。
- 用户可以通过 `/models` 选择主模型和 fallback。
- 三脑角色优先级可配置。
- 内置 Provider 预设可让用户只填 API key 即可使用常见厂商。
- 自定义中转 Provider 同时保存 `homepage` 和 `base_url`。
- 模型实际请求只走 `base_url`，`homepage` 只用于展示和控制台入口。
- 中转 Provider 的 API key 只进入用户级 auth，不进入项目配置。
- Provider 配置支持 `sdk_type` 和模型级 options。
- OpenAI-compatible、Anthropic、Gemini 等 Provider 至少可以通过统一 ProviderRegistry 创建或模拟创建。
- MessageTransformer 能为不同 Provider 清理空内容、tool id 和错序 tool result。

### 权限

- `.env`、secret、私钥默认拒绝读取。
- 写入、删除、命令执行需要审批。
- offline 模式禁止云端模型和联网工具。
- shell 命令经过规则匹配和 token/AST 风险分析。
- Hooks 默认未信任，首次启用必须确认。
- Hook 阻止动作时，原因会注入模型上下文，而不是只在终端报错。

### Context / 会话

- 超过上下文阈值时触发分级压缩，而不是直接丢弃早期历史。
- 压缩摘要保留用户目标、已改文件、已测内容、失败原因和剩余任务。
- 会话以 JSONL 持久化，支持 `/resume` 和 `/fork-session`。
- 会话文件和摘要不保存 API key。

### TUI / 状态

- 复杂任务能看到任务列表、当前步骤、工具调用和底部状态栏。
- 模型 thinking/responding/tool-use/stalled 状态可区分。
- `/cost` 或 `/usage` 可查看 token 使用，未知价格显示未知而不是乱估算。

### 长期 Agent Loop

- 自研 AgentLoop 必须以可插拔方式接入，旧 SDK Runner 路径可回退。
- 工具错误、权限拒绝、Hook 拦截都能作为结构化反馈返回模型。
- 多 Agent 并行必须有写入范围隔离和合并前审核。

### 发布

- `npm install -g lucode` 后可启动。
- `lucode doctor` 能检查环境和给出中文修复建议。

---

## 17. 参考方向

Claude Code 值得借鉴：

- 极简启动。
- slash commands。
- statusline。
- 项目级 `.claude` 配置。
- project/user/plugin/additional-directory 多来源 skills。
- 项目级 MCP 需要信任边界。
- 权限分层。
- 早期输入捕获。
- JSONL 会话、resume/fork。
- Context 分级压缩。
- 错误即反馈的 Agent Loop。

opencode 值得借鉴：

- npm / 平台二进制发布方式。
- `run` / `serve` / TUI 分层。
- auth 和 config 分离。
- Provider / model 配置。
- `/models` 选择。
- 内置工具和 MCP 扩展并存。
- Provider SDK 懒加载和缓存。
- Provider→Model 结构化 options。
- 跨厂商消息转换管线。

参考链接：

- Claude Code CLI Reference: https://code.claude.com/docs/en/cli-reference
- Claude Code Commands: https://code.claude.com/docs/en/commands
- Claude Code Statusline: https://code.claude.com/docs/en/statusline
- Claude Code Settings: https://code.claude.com/docs/en/settings
- Claude Code MCP: https://code.claude.com/docs/en/mcp
- Claude Code `.claude` Directory: https://code.claude.com/docs/en/claude-directory
- opencode Config: https://opencode.ai/docs/config/
- opencode CLI: https://opencode.ai/docs/cli/
- opencode Providers: https://opencode.ai/docs/providers
- opencode Models: https://opencode.ai/docs/models/
- opencode MCP Servers: https://opencode.ai/docs/mcp-servers/

---

## 18. 与 `ARCHITECTURE_GAP_ANALYSIS.md` 的合并摘要

本次融合后，C 阶段不再只是一组 UI/CLI 改造，而是形成三层路线：

```text
产品外壳层：
  CLI、欢迎页、slash 命令、工作区、Provider 配置、npm 发布。

安全与扩展层：
  permissions.toml、Tool Registry、项目 Skills/MCP、Hooks、审批和隐私边界。

运行内核增强层：
  ProviderRegistry、MessageTransformer、ContextCompactor、SessionStore、可插拔 AgentLoop。
```

对 `ARCHITECTURE_GAP_ANALYSIS.md` 的取舍：

- 接受：快速路径启动、Provider 原生 SDK、消息转换、SDK 缓存、Context 压缩、Hooks、JSONL 会话、错误即反馈。
- 分阶段接受：终端 UI 先用 prompt_toolkit/ANSI MVP，后续再考虑 Textual/Rich 级别渲染。
- 延后：完全自研 Agent Loop、Swarm、多 worktree 并行属于 C7 长期路线，不塞进 C1-C5 的验收。
- 拒绝：为了模仿 opencode 而重写成 TS/Bun；当前 Python 内核继续保留。

最优先的下一批建议：

```text
P0:
  lucode --version 快速路径
  启动 profiler
  ProviderRegistry openai_compatible 下沉
  MessageTransformer MVP

P1:
  ContextCompactor MVP
  Hooks PreToolUse/PostToolUse
  JSONL SessionStore
  prompt_toolkit 输入层

P2:
  Anthropic/Gemini 原生 Provider
  /resume /fork-session
  命令 AST 风险分析

P3:
  可插拔 AgentLoop
  isolated worktree 多 Agent
  完整 TUI 渲染引擎
```

---

## 19. 一句话总结

C 阶段的重点不是“再做一个更复杂的多智能体系统”，而是把当前内核包装成真正可长期使用的 CLI 产品；融合架构差距分析后，路线升级为：先稳定产品外壳和安全边界，再补 Provider、Context、Hooks、Session 等运行骨架，最后以可插拔方式探索自研 Agent Loop。

