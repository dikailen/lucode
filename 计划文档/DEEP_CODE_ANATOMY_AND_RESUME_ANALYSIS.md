# agents_demo (Lucode) 全代码层级解剖与简历定位分析

> 分析日期：2026-05-11
> 范围：agents_demo 项目全部 ~5000+ 行 Python 代码，逐层逐函数解剖
> 目标：1) 理解每一段代码的设计意图与执行逻辑；2) 在简历中突出项目与 Claude Code CLI、OpenCode、Cursor、GitHub Copilot 的差异化优势

---

## 目录

1. [项目宏观架构](#一项目宏观架构)
2. [第 1 层：CLI 入口层 — lucode/entry.py + main.py 前半](#二第-1-层cli-入口层)
3. [第 2 层：配置与模型目录层](#三第-2-层配置与模型目录层)
4. [第 3 层：Agent 工厂与 SDK 封装层](#四第-3-层agent-工厂与-sdk-封装层)
5. [第 4 层：MCP 工具服务器层](#五第-4-层mcp-工具服务器层)
6. [第 5 层：执行引擎层 — Pipeline + Gate + Multi-Agent](#六第-5-层执行引擎层)
7. [第 6 层：规划层 — Planner + Validator + Reviewer](#七第-6-层规划层)
8. [第 7 层：Skills 层](#八第-7-层skills-层)
9. [第 8 层：安全架构层 — 权限 + 隐私 + 审计 + 回滚](#九第-8-层安全架构层)
10. [第 9 层：UI 与运行时辅助层](#十第-9-层ui-与运行时辅助层)
11. [简历定位分析](#十一简历定位分析)
12. [与市面工具的差异化矩阵](#十二与市面工具的差异化矩阵)

---

## 一、项目宏观架构

### 1.1 整体分层图

```
┌──────────────────────────────────────────────────────────────────┐
│  第 9 层 — UI 与辅助层                                             │
│  welcome.py / progress.py / command_palette.py                    │
│  flywheel.py / patch_ledger.py / run_workspace.py / conversation.py│
├──────────────────────────────────────────────────────────────────┤
│  第 8 层 — 安全架构层                                              │
│  auditor.py / checkpoint.py / session_checkpoint.py               │
│  repair_loop.py / permissions.py / privacy.py                     │
├──────────────────────────────────────────────────────────────────┤
│  第 7 层 — Skills 层 (skills/)                                     │
│  8 个 SKILL.md → 按需注入 Agent system prompt                       │
├──────────────────────────────────────────────────────────────────┤
│  第 6 层 — 规划层 (planning/)                                       │
│  query_refiner → orchestrator_planner → plan_validator → plan_reviewer│
├──────────────────────────────────────────────────────────────────┤
│  第 5 层 — 执行引擎层 (runtime/execution/)                           │
│  dynamic.py: Pipeline编排、Gate决策、单/多Agent调度、并行安全检测     │
│  pipeline.py: GateDecision、PipelineRunState、Verifier              │
├──────────────────────────────────────────────────────────────────┤
│  第 4 层 — MCP 工具层 (mcp_servers/)                                │
│  7 个 MCP Server: filesystem/code_locator/edit/backup/             │
│  command/git/web_search — 全部 stdio 子进程                         │
├──────────────────────────────────────────────────────────────────┤
│  第 3 层 — Agent Factory 层 (runtime/agents/)                       │
│  sdk.py: OpenAI Agents SDK 懒加载 + Fallback 兼容                   │
│  factory.py: 5 种 Agent 创建策略（task/direct/solo/synthesizer）     │
├──────────────────────────────────────────────────────────────────┤
│  第 2 层 — Config/Runtime 层 (runtime/config/ + catalog_system/)    │
│  model_config.py: Provider→Model 配置与选择                         │
│  model_catalog.py: ModelRegistry + 探测 + 隐私排序                   │
│  settings.py / workspace.py / execution_mode.py / extensions.py     │
├──────────────────────────────────────────────────────────────────┤
│  第 1 层 — Entry 层 (lucode/entry.py + main.py)                     │
│  argparse CLI → 10 种子命令分发 → 交互式 chat_loop                   │
└──────────────────────────────────────────────────────────────────┘
```

### 1.2 核心执行流程（一次用户请求的完整生命周期）

```
用户输入 "帮我写一个 Flask API"
  │
  ▼
main.py: chat_loop()  ← 异步 REPL，读取 stdin
  │
  ▼
planning/planner.py: preview_plan()
  ├─ query_refiner Agent    → 提取意图、约束、上下文（RefinedRequest）
  ├─ orchestrator_planner Agent → 拆解为 PlannedTask[] + 路由决策
  ├─ plan_validator          → 校验 skill/mcp/model 可用性
  └─ plan_reviewer           → 安全审查（循环依赖、写冲突）
  │
  ▼
runtime/execution/dynamic.py: execute_dynamic_request()
  ├─ apply_pipeline_gate()   → 判断是否需要代码管线（GateDecision）
  ├─ _run_single_agent() 或 _run_multi_agent()
  │   ├─ AgentFactory 创建 Agent + 注入 Skill 指令
  │   ├─ MCPServerManager 提供工具
  │   └─ Runner.run_streamed() 执行
  ├─ build_verification_report() → git diff/status + 自定义验证命令
  └─ audit_execution()       → 对照验收标准逐项检查
  │
  ▼
repair_loop.py: should_retry() → 不通过则重新规划并执行（最多3轮）
  │
  ▼
flywheel.py: 记录执行总结（成功模式 + 失败教训）
  │
  ▼
返回结果 → chat_loop 展示给用户
```

### 1.3 为什么这个架构在市面上独一无二

**市面产品的主力架构：**
- **Claude Code CLI**：单 Agent + 工具调用，无规划层，无多 Agent 协调
- **OpenCode**：单 Agent + 多 Provider SDK 切换，无规划管线
- **Cursor**：IDE 内嵌补全 + Chat，无 Agent 自主执行
- **GitHub Copilot**：纯补全引擎，无 Agent 架构

**agents_demo 的架构差异：**
- ✅ **规划-执行-审计 三层分离**：不是"一个 Agent 一把梭"
- ✅ **Gate 决策系统**：自动判断任务是否需要代码管线、测试、验证
- ✅ **安全四维模型**：权限(policy) × 隐私(mode) × 审计(criteria) × 回滚(checkpoint)
- ✅ **多 Agent 并行 + 写冲突检测**：真正的并行协调，非串行链式调用
- ✅ **7 个独立 MCP Server**：工具按风险分离部署，非一个大杂烩

---

## 二、第 1 层：CLI 入口层

### 2.1 lucode/entry.py — argparse 子命令分发

**文件定位**：用户交互的第一接触点，所有 CLI 命令的路由中枢。

**核心函数逐个解剖：**

#### `build_parser()` (行 10-78)
```
功能：构建完整的 argparse 参数解析树
设计意图：
  - 10 种子命令：chat / run / init / doctor / config / model / mcp / session / connect / models / auth
  - 子命令嵌套：models 下有 select 和 role 两个子子命令
  - auth 下有 list / login / logout 三个子子命令

设计亮点：
  - connect 命令支持 --custom 标志，区分"内置 Provider 预设"和"自定义 OpenAI-compatible 中转"
  - models select 支持 primary + fallback 列表，实现模型降级链
  - models role 支持三脑角色独立配置（query_refiner / orchestrator / final_synthesizer）

与 Claude Code 的关键差异：
  - Claude Code 手工解析 argv 实现快速路径（--version 零导入返回）
  - 此处每次构建完整 ArgumentParser，即使只是 --help 也要走完整 Python 启动
```

#### `main(argv)` (行 81-115) — 核心分发逻辑
```
设计意图：
  1. args.command == "chat" → 进入交互模式（导入 main.py）
  2. args.command == "run"  → 单次非交互执行
  3. 其他子命令 → 各自 handler

关键代码路径：
  - 行 87: from main import main as interactive_main
    这是整个项目最重的导入点——main.py 顶层有 30+ imports
  - 行 89: asyncio.run(interactive_main())
    进入交互 REPL

架构问题（ARCHITECTURE_GAP_ANALYSIS.md 已详述）：
  - 无快速路径：lucode --version 也会加载完整运行时
  - 无早期输入捕获：import main.py 期间的按键会丢失
```

#### `_workspace_context(args)` (行 118-123)
```
功能：发现工作区上下文
输入：argparse Namespace（含 --workspace 路径）
输出：WorkspaceContext（app_home, user_home, workspace_root, has_project_config）
实现：
  - app_home = entry.py 的上两级目录（即项目根目录）
  - cwd = 用户指定工作区 或 当前目录
  - discover_workspace_context() 向上查找 .lucode 目录确定 workspace_root
```

#### `_export_context(context, args)` (行 126-133)
```
功能：将 WorkspaceContext 注入环境变量
设计意图：让所有子模块通过 os.environ 获取上下文，避免参数层层传递
环境变量：
  - LUCODE_APP_HOME    → 项目安装路径
  - LUCODE_USER_HOME   → 用户配置目录
  - LUCODE_WORKSPACE_ROOT → 当前工作区根目录
  - LUCODE_NO_LOGO     → 是否隐藏欢迎页
  - LUCODE_VERBOSE_RUNTIME → 是否详细日志
```

#### `_handle_run(args, context)` (行 136-190) — 非交互执行
```
功能：单次执行用户任务并输出结果
执行流程：
  1. 构建 RuntimeSettings（从环境变量）
  2. 创建 TokenLoggerHooks（记录 token 用量）
  3. 创建 ModelRegistry + MCPServerManager
  4. runtime_route_for_input() 判断执行模式
     - "solo" → run_solo_request()   直接执行
     - "full" → run_full_request()   完整规划→执行→审计
     - 默认   → run_serial_request() 串行规划→执行

核心设计：run_agent 闭包
  lambda agent, turn_input, turn_hooks, max_turns=20:
      run_with_approval(agent, turn_input, turn_hooks, session=None, max_turns=max_turns)
  这个闭包将"审批式 Agent 运行"封装为可注入的函数，让不同模式共用同一审批逻辑
```

#### `_handle_init(context)` (行 193-242)
```
功能：初始化 .lucode 工作区目录结构
创建的目录：skills、mcp、memory、sessions
创建的文件：
  - config.toml：mode="solo", privacy="local_first"
  - permissions.toml：完整的权限策略模板
    · read 默认 allow，禁止 .env/*.pem/*secret*/*token*
    · write 默认 ask，禁止 .git/.agent_quarantine/auth.json
    · shell 默认 ask，禁止危险命令
    · mcp.workspace 默认 ask
```

#### `_handle_connect(args, context)` (行 288-320)
```
功能：连接 Provider 并保存凭据
调用链：connect_provider() → 保存到 .lucode/config.toml + auth.json
关键：API key 保存到用户级 auth.json（~/.lucode/auth.json），不写入项目配置
安全设计：避免 API key 被 git 跟踪或泄露到项目共享中
```

#### `_handle_models(args, context)` (行 323-356)
```
功能：模型优先级管理
支持操作：
  - models select primary [fallback...]  → 设置主模型 + 降级链
  - models role <role> <refs...>        → 为三脑角色独立配置模型
    三脑角色：query_refiner、orchestrator、final_synthesizer
  - models（无参数）                     → 查看当前配置
```

### 2.2 main.py — 交互式 REPL 核心（~965 行）

**文件定位**：整个项目的交互中枢，包含 chat_loop、审批系统、token 监控、slash 命令系统。

#### 顶层导入（行 1-33）
```
导入量：30+ 顶层 import
问题：模块求值时即加载几乎所有子系统
  - catalog_system（模型目录 + 刷新）
  - mcp_servers（7 个 MCP Server）
  - planning（planner + schema）
  - runtime（config/execution/safety/ui）
  - 标准库：sys, asyncio, json, os, threading, pathlib, textwrap, time, re

这是启动慢的根源之一——Python 模块导入是同步阻塞的
```

#### `TokenLoggerHooks` 类（行 36-103）
```
继承：RunHooks（OpenAI Agents SDK 的 Hook 基类）
功能：每次 Agent 调用的 token 用量追踪

核心方法：
  - on_agent_start(): 记录开始时间（time.monotonic() 高精度计时器）
  - on_agent_end(): 从 TurnUsage 提取 input_tokens + output_tokens
    注意：output_tokens 细分为 reasoning_tokens + output_tokens
    累计到 self._agent_stats[agent_name] 字典
    计算 cost = input_tokens * INPUT_PRICE + output_tokens * OUTPUT_PRICE

设计亮点：
  - 按 agent_name 分组统计 → 多 Agent 场景下可区分每个 Agent 的消耗
  - 使用 monotonic() 而非 time() → 不受系统时间调整影响
  - 费用计算支持分离的 input/output 价格

  print_summary(): 格式化输出每 Agent 的调用次数、token 量、费用
  get_cost_summary(): 返回 Python 字典供程序化使用
```

#### `StdinConsoleAdapter` 类（行 106-143）
```
功能：单读取器 stdin 适配器
设计背景：
  - Agent 运行期间，input() 会与 SDK 内部的 stdin 读取冲突
  - 需要一个统一的 stdin 读取入口，所有输入经由此处排队

实现机制：
  - 使用 threading.Lock() 保护 stdin 读取
  - readline(): 独占锁 + sys.stdin.readline()
  - 所有需要读取用户输入的地方都通过此适配器

这是"生产级细节"——处理异步 stdin 竞争条件
```

#### `RuntimeCommandSession` 类（行 146-274）
```
功能：Agent 执行期间监听 /stop 等运行时命令
设计意图：在 Agent 流式输出期间，用户仍可输入命令打断

核心机制：
  - 后台线程（threading.Thread + daemon=True）
  - 使用 select.select() 检查 stdin 是否有数据（非阻塞轮询，50ms 间隔）
  - 每次检测到输入→加锁读取→检查是否为 /stop → 设置 stop_event

支持的运行时命令：
  - /stop → 设置 asyncio.Event，中断当前 Agent 执行
  - /exit → 设置 exit_event

  start_watching(): 启动后台监听线程
  stop_watching(): 停止线程 + 等待 join
  is_stop_requested(): 返回 stop_event.is_set()

为什么需要这个？
  OpenAI Agents SDK 在 Runner.run_streamed() 期间会阻塞
  用户按 Ctrl+C 只能杀死进程，无法优雅停止当前 turn
  此设计让用户可以在 Agent 长时间运行时发送 /stop 优雅中断
```

#### `create_token_logger_hooks()` (行 277-280)
```
功能：工厂函数，创建 TokenLoggerHooks 实例
独立为函数的原因：方便在 run 模式和 chat 模式间复用
```

#### `render_welcome_dashboard()` 包装 (行 283-330)
```
功能：封装 runtime.ui.welcome 的调用
参数：workspace, mode_label, model_summary, privacy_label, mcp_count, skill_count

输出：ANSI 彩色盒子风格的 ASCII 面板
  ┌─────────────────────────────┐
  │  Lucode Terminal Agent      │
  │  工作区: /path/to/project   │
  │  模式: solo · 隐私: local   │
  │  模型: deepseek-v4-pro      │
  │  MCP: 7 · Skills: 8        │
  └─────────────────────────────┘
```

#### `main()` (行 376-405) — 启动主流程
```
执行步骤：
  1. 强制关闭 OpenAI SDK tracing（性能优化）
  2. refresh_catalogs() — 刷新模型探测缓存（阻塞式，非懒加载）
  3. 构建 RuntimeSettings + WorkspaceContext + ModelRegistry
  4. 渲染欢迎面板
  5. 进入 chat_loop()

架构问题：
  - refresh_catalogs() 在 main() 入口同步调用→可能阻塞数秒
  - Claude Code 的做法：先渲染 UI，再后台预取
```

#### `chat_loop()` (行 408-678) — 交互 REPL 主循环
```
这是整个项目最复杂的函数，约 270 行

状态变量：
  - recent_turns: list[dict] — 最近 6 轮对话的压缩摘要
  - session_checkpoints: SessionCheckpointManager — 每轮的回滚检查点
  - console: StdinConsoleAdapter — 统一 stdin 入口
  - token_logger: TokenLoggerHooks — token 追踪
  - hooks: RunHooks — Agent 生命周期钩子

  主循环逻辑：
  while True:
    1. 读取用户输入（console.readline()）
    2. 检查是否为 slash 命令 → 分发处理
    3. 检查是否空输入 → 跳过
    4. 追加到 recent_turns（用户消息）
    5. 调用 preview_plan() → 展示执行计划→用户确认
    6. 创建 MCPServerManager（异步上下文管理器）
    7. 调用 execute_dynamic_request() → 执行
    8. 审计 → 可能的修复循环
    9. 追加到 recent_turns（assistant 消息，截断 800 字符）
    10. 清理 + 展示 token 用量

  Slash 命令系统（23 个命令）：
  ┌──────────────┬──────────────────────────────────────┐
  │ 命令         │ 功能                                  │
  ├──────────────┼──────────────────────────────────────┤
  │ /exit        │ 退出程序                              │
  │ /stop        │ 停止当前 Agent 执行                   │
  │ /new         │ 清空上下文 + 重新开始                  │
  │ /plan        │ 预览当前任务计划（不执行）              │
  │ /status      │ 查看当前项目 git 状态                  │
  │ /diff        │ 查看当前修改的 diff                   │
  │ /rollback    │ 回滚最近一轮修改                      │
  │ /mode        │ 切换执行模式（solo/serial/full）       │
  │ /refiner     │ 切换 query_refiner 开关               │
  │ /config      │ 查看当前配置                          │
  │ /model       │ 查看/切换模型                         │
  │ /connect     │ 连接新 Provider                       │
  │ /api         │ 设置 API key                          │
  │ /privacy     │ 切换隐私模式                          │
  │ /skills      │ 列出可用 Skill                        │
  │ /mcp         │ 列出 MCP 工具                         │
  │ /tools       │ 列出所有工具                          │
  │ /permissions │ 查看当前权限策略                      │
  │ /cost        │ 查看会话累计费用                      │
  │ /help        │ 帮助信息                              │
  │ /doctor      │ 系统诊断                              │
  │ /init        │ 初始化工作区                          │
  └──────────────┴──────────────────────────────────────┘
```

#### `run_with_approval()` (行 680-757) — 审批式 Agent 执行
```
功能：在 Agent 每次工具调用前要求用户确认
参数：agent, run_input（用户输入或计划文本）, hooks, session, max_turns

审批流程：
  1. 创建 RuntimeCommandSession（后台 /stop 监听）
  2. 配置 RunConfig（workdir + 输入守卫）
  3. 使用 hooks.on_tool_start() 拦截工具调用
  4. 展示工具名称 + 参数摘要
  5. ask/allow/deny 三种响应：
     - allow → 继续执行
     - deny  → 注入 "Permission denied by user" 错误回消息流
     - ask   → 调用 _request_user_approval() 等待用户输入

  关键设计决策：
  - 工具被拒绝后不崩溃——错误作为 ToolResult 注入回消息流
  - 让 LLM 看到"这个工具被用户拒绝了"并自适应选择替代策略
  - 这与 Claude Code 的"错误即反馈"机制一致

  _request_user_approval():
    展示：tool_name + arguments（JSON 格式化）
    选项：[A]llow / [D]eny / Allow [T]his Tool / Deny Thi[s] Tool / [Q]uit
    使用 console.readline() 读取用户选择
    支持 This Tool 粒度的批量决策（当前 turn 内同类工具自动允许/拒绝）
```

#### `_run_agent_once()` (行 777-797) — SDK 调用
```
功能：单次 Agent 执行的 SDK 封装
实现：
  result = Runner.run_streamed(
      agent,
      run_input,       # 用户输入或完整计划文本
      hooks=hooks,     # Token 日志 + 审批钩子
      max_turns=max_turns,  # 最大工具调用轮次
  )
  # 流式消费 delta 文本（逐 token 打印）
  while True:
      async for event in result.stream_events():
          if event.type == "raw_response_event":
              delta = event.data.delta
              print(delta, end="", flush=True)

核心问题（如前分析）：
  - 完全委托给 OpenAI Agents SDK 的 Runner
  - 无法控制工具预执行（Claude Code 的流式工具预执行可隐藏 I/O 延迟）
  - 无法控制 Compaction（上下文超长时无自动压缩）
  - 无法随信任增长自动升级权限（Claude Code 的信任升级机制）
```

#### `_stream_delta_text()` (行 760-775)
```
功能：解析 SDK 流式事件中的文本 delta
处理两种事件：
  - raw_response_event → 提取 delta 文本
  - run_item_stream_event → 处理工具调用结果（不打印，记录即可）

当前限制：不区分 thinking delta 和 text delta（推理模型的思考过程与正文混在一起）
```

---

## 三、第 2 层：配置与模型目录层

### 3.1 catalog_system/model_catalog.py — 模型注册中心

**文件定位**：整个项目的"模型大脑"——决定"用哪个模型、怎么用、是否可用"

#### `ModelRegistry` 类（~500 行）
```
职责：模型发现→探测→排序→创建 的完整生命周期

构造流程：
  1. load_model_catalog() → 从 catalogs/model_catalog.json 加载预设
  2. discover_model_definitions() → 从 .env 发现用户配置的模型
  3. refresh_model_probe_cache() → 探测每个模型是否可用
  4. _build_model_catalog() → 合并探测结果，计算模型层级

核心方法：

  get_model(model_id: str) → OpenAIChatCompletionsModel
    1. 从 catalog 查找模型定义（model_name, api_key, base_url）
    2. 创建 AsyncOpenAI 客户端
    3. 包装为 OpenAIChatCompletionsModel
    ⚠️ 每个模型都走 OpenAI-compatible 协议（项目最大的架构限制）

  first_configured(preferred_ids, privacy_policy) → model_id | None
    1. 按 preferred_ids 顺序遍历
    2. 使用 privacy_policy.model_allowed() 过滤
    3. 选择第一个已配置 + 探测通过 + 隐私合规的模型
    返回: 模型 ID（如 "deepseek_v4_pro"）或 None

  resolve_model_ref(ref: str) → model_id
    将 "provider/model_name" 引用解析为内部 model_id
    例如 "deepseek/deepseek-chat" → "deepseek_v4_flash"

  resolve_role_models(role, privacy_policy) → list[model_id]
    为三脑角色返回候选模型列表（含 fallback）
```

#### `KNOWN_MODEL_DEFINITIONS` 常量
```
预设的模型定义，按 compat_group 分组以减少重复配置：

DEEPSEEK 共享组：
  - deepseek_v4_flash: deepseek-v4-flash
  - deepseek_v4_pro:  deepseek-v4-pro

MIMO 共享组：
  - mimo_v25:     mimo-v2.5
  - mimo_v25_pro: mimo-v2.5-pro

设计意图：同一 Provider 的多个模型共享 base_url/api_key，
只用 compat_group 引用公共配置，避免 .env 中重复声明
```

#### `discover_model_definitions()` (行 ~280-360)
```
功能：从环境变量发现用户配置的模型
模式：MODEL_<ID>_*  或  <PROVIDER_ID>_API_*

解析规则：
  - MODEL_DEEPSEEK_API_KEY → Provider DEEPSEEK 的 API key
  - MODEL_DEEPSEEK_BASE_URL → Provider DEEPSEEK 的 base URL
  - MODEL_DEEPSEEK_MODELS  → "alias1:realname1,alias2:realname2"
  - 每个 alias 生成一个模型定义条目

还支持兼容格式：
  - OPENAI_API_KEY → 自动识别为 OpenAI Provider
  - 任何 _API_KEY 结尾的变量 → 尝试匹配内置 Provider catalog
```

#### `_build_model_catalog()` (行 ~400-500)
```
功能：合并探测结果 → 计算模型执行策略
输出结构（每个模型条目）：
  {
    "id": "deepseek_v4_pro",
    "name": "deepseek-v4-pro",
    "provider": "deepseek",
    "backend": "cloud",            # cloud / local / ollama
    "base_url": "https://api.deepseek.com",
    "configured": true,             # 用户是否配置了 API key
    "probe_status": "ok",           # ok / failed / unknown / pending
    "probe_detail": {...},          # 探测详情
    "tier": "primary",              # primary / fallback / unavailable
    "capabilities": {               # 从探测结果提取
      "json": true,
      "tools": true,
      "reasoning": false
    }
  }

模型层级（tier）计算逻辑：
  - primary:   已配置 + 探测通过 + 隐私合规
  - fallback:  已配置 + 探测未知（可能未探测但配置了）
  - unavailable: 探测失败 / 隐私阻止 / 未配置
```

#### `first_configured()` 方法
```
功能：隐私感知的模型选择
算法：
  1. 如果 preferred_ids 为空 → 使用默认优先级列表
  2. 遍历 preferred_ids：
     a. model_allowed(backend, privacy_policy) → 隐私检查
     b. configured == true                    → 配置检查
     c. probe_status in {ok, unknown}         → 可用性检查
  3. 返回第一个通过三步检查的模型 ID
  4. 如果全部失败 → 抛出 RuntimeError("No model available")

隐私策略映射：
  - offline:     只允许 local/ollama backend
  - local_first: 优先 local，但允许 cloud fallback
  - cloud_allowed: 允许所有 backend
```

### 3.2 catalog_system/model_probe.py — 模型能力探测

```
功能：在启动时探测每个模型是否实际可用
设计哲学："声明了不代表能用，必须实际测过"

探测流程（refresh_model_probe_cache）：
  1. 检查缓存 TTL：
     - 成功缓存：86400 秒（24 小时）
     - 失败缓存：300 秒（5 分钟，避免频繁重试）
  2. 对每个 configured 的模型：
     a. 本地模型 → 先 probe_ollama_service() 检查服务健康
     b. probe_model_capabilities() 三步探测：
        Step 1: 基本对话 → 检查是否返回有效 JSON
        Step 2: JSON 输出 → 检查 ok=true 响应
        Step 3: 工具调用 → 提供 mock tool，检查是否返回 tool_calls
     c. SHA256 指纹（model_fingerprint）：用于缓存失效
        hash(id + backend + base_url + model_name)
  3. 写入探针缓存文件（JSON）

关键设计细节：
  - MODEL_PROBE_ENABLED 环境变量控制开关
  - MODEL_PROBE_LOCAL_ONLY=true → 只探测本地模型（不消耗 API 配额）
  - PROBE_TIMEOUT_SECONDS 控制超时
  - 探测是 best-effort——失败不阻塞启动，只标记 probe_status="failed"

为什么需要探针？
  - .env 中可能有拼写错误的模型名
  - API key 可能过期
  - 本地 Ollama 可能未启动
  - 提前探测可以快速发现配置问题，而不是等到 Agent 执行时报错
```

### 3.3 catalog_system/refresher.py — 模型目录刷新

```
功能：按需刷新模型探测缓存
调用时机：main() 启动时 + /model refresh 命令

实现：
  refresh_catalogs():
    1. 加载模型目录
    2. 发现环境变量中的模型
    3. 调用 refresh_model_probe_cache()
    4. 输出刷新统计

为什么这是独立的？
  探测可能耗时（网络请求），所以从 Registry 构造中分离出来
  允许用户选择跳过探测（通过环境变量控制）
```

### 3.4 runtime/config/model_config.py — Provider 配置管理

**文件定位**：Provider 的"连接-存储-加载-选择"全生命周期。

#### Provider Catalog 系统
```
数据来源：catalogs/provider_catalog.json
内容：~50+ Provider 预设
每个 Provider 条目：
  {
    "id": "deepseek",
    "display_name": "DeepSeek",
    "homepage": "https://platform.deepseek.com",
    "base_url": "https://api.deepseek.com",
    "models": ["deepseek-chat", "deepseek-reasoner"],
    "requires_api_key": true,
    "api_key_env": "DEEPSEEK_API_KEY"
  }

用途：
  - /connect deepseek → 自动填充 base_url、模型列表
  - 用户只需提供 API key，其他信息从 Catalog 自动补全
```

#### `connect_provider()` 函数 (行 ~150-280)
```
功能：保存 Provider 配置到文件系统
参数：provider_id, api_key, base_url, homepage, models, custom, workspace_root, user_home

执行：
  1. 查找 Provider Catalog 预设（如果是内置 Provider）
  2. 如果 --custom → 创建自定义 Provider 条目
  3. API key → 保存到 user_home/auth.json（用户级，chmod 600）
  4. Provider 元数据 → 保存到 workspace/.lucode/config.toml（项目级）

安全设计分层：
  - API key 永远在用户级（auth.json），不进入项目目录
  - Provider 配置在项目级（config.toml），可被团队共享
  - auth.json 加入 .gitignore + permissions.toml 禁止读取

返回：{"provider_id": ..., "provider": {...}}
```

#### `load_effective_lucode_config()` 函数
```
功能：合并用户级 + 项目级配置
合并优先级：项目配置 > 用户配置 > 默认值

配置项：
  - mode: solo / serial / full
  - privacy: offline / local_first / cloud_allowed
  - model_priority: [主模型, fallback1, fallback2, ...]
  - role_models: {query_refiner: [...], orchestrator: [...], final_synthesizer: [...]}
```

#### `configured_provider_model_definitions()` 函数
```
功能：从 config.toml 生成模型定义
输出：模型定义列表，每个包含 model_name、api_key、base_url 等
供 ModelRegistry 合并到模型目录
```

#### 模型选择函数
```
select_model_priority(workspace_root, primary_ref, fallback_refs):
  - 将 "provider/model" 引用解析为内部 model_id
  - 写入 config.toml 的 model_priority 字段

select_role_model_priority(workspace_root, role, refs):
  - 为三脑角色独立设置模型优先级
  - role ∈ {query_refiner, orchestrator, final_synthesizer}
  - 每个角色可以有独立的模型 + fallback 链
```

### 3.5 runtime/config/settings.py — 运行时设置

```
RuntimeSettings 类：
  - execution_mode: solo / serial / full（从 env AGENTS_EXECUTION_MODE 读取）
  - privacy_mode: offline / local_first / cloud_allowed（从 env AGENTS_PRIVACY_MODE 读取）

  from_env(): 工厂方法，从环境变量构建
```

### 3.6 runtime/config/execution_mode.py

```
runtime_route_for_input(input_text, execution_mode):
  功能：根据用户输入内容自动判断执行模式
  逻辑：
    - 包含 "创建"/"写"/"实现"/"fix" 等关键词 → 倾向 full/serial
    - 包含 "解释"/"是什么"/"怎么用" → 倾向 solo
    - 但最终受 execution_mode 设置限制

  这个函数实现了"任务感知的路由" —— 不是所有问题都需要走重量级规划管线
```

### 3.7 runtime/config/workspace.py

```
discover_workspace_context(app_home, cwd):
  功能：发现工作区上下文
  逻辑：
    1. 从 cwd 向上查找 .lucode 目录
    2. 找到 → has_project_config = True, workspace_root = 该目录的父目录
    3. 未找到 → workspace_root = cwd, has_project_config = False
    4. user_home = ~/.lucode（用户级配置目录）

  WorkspaceContext 结构：
    - app_home: Path       # 项目安装路径
    - user_home: Path      # 用户配置目录
    - workspace_root: Path # 工作区根目录
    - has_project_config: bool  # 是否已初始化
```

### 3.8 runtime/config/extensions.py — Skill/MCP 三层发现

```
discover_skill_layers(app_home, user_home, workspace_root):
  三层 Skill 发现：
    1. core（app_home/skills/）        — 系统内置
    2. user（user_home/skills/）       — 用户自定义
    3. workspace（workspace_root/.lucode/skills/） — 项目专属

  优先级：workspace > user > core（同名覆盖）
  安全：_mark_skill_safety() 阻止用户层覆盖系统保护型 Skill

discover_mcp_layers():
  同样的三层发现逻辑应用于 MCP Server 配置
  优先级相同：workspace > user > core
```

### 3.9 runtime/config/cli.py — CLI 信息渲染

```
render_readonly_command(command_name, settings, context):
  功能：渲染只读信息命令的输出
  用途：/config, /model, /mcp, /connect（无参数时）等命令
  返回：格式化的多行文本，包含当前配置摘要
```

---

## 四、第 3 层：Agent 工厂与 SDK 封装层

### 4.1 runtime/agents/sdk.py — SDK 懒加载与 Fallback

**文件定位**：隔离 OpenAI Agents SDK 的导入点，提供统一的 SDK 访问接口。

#### SDK 懒加载机制
```
核心函数：_ensure_sdk()
功能：第一次调用时才 import agents 包
原因：
  1. agents 包导入慢（~200-500ms）
  2. --help / --version 等快速路径不需要 SDK
  3. 如果用户在无 SDK 环境中运行 doctor 命令，不应崩溃

实现：
  - 模块级 _sdk_loaded 标志
  - import agents 放在 try/except 中
  - 导入失败 → 使用 Fallback 类

导出的工厂函数：
  - agent_class()        → Agent 或 _FallbackAgent
  - runner_class()       → Runner 或 _FallbackRunner
  - run_hooks_class()    → RunHooks 或 _FallbackRunHooks
  - async_openai_class() → AsyncOpenAI
  - openai_chat_completions_model_class() → OpenAIChatCompletionsModel
  - mcp_stdio_class()    → MCPServerStdio
```

#### `ensure_tracing_disabled()`
```
功能：设置 OPENAI_AGENTS_DISABLE_TRACING=true
原因：OpenAI Agents SDK 默认启用 tracing，会向 OpenAI 发送遥测数据
影响：
  - 隐私保护（不泄露任务内容）
  - 性能提升（无网络发送开销）
```

#### Fallback 类设计
```
_FallbackAgent: 占位类，任何操作都抛出 RuntimeError
_FallbackRunner: 同上
_FallbackRunHooks: 空实现，允许 Hooks 接口在不导入 SDK 时存在
```

### 4.2 runtime/agents/factory.py — Agent 工厂

**文件定位**：将 PlannerResult 中的 PlannedTask 转换为实际可执行的 Agent 实例。

#### `AgentFactory` 类
```
构造参数：
  - model_registry: ModelRegistry  — 模型来源
  - privacy_policy: PrivacyPolicy  — 隐私约束
  - skill_registry: SkillRegistry  — 指令注入来源
  - mcp_manager: MCPServerManager  — 工具来源
  - settings: RuntimeSettings      — 运行时配置

核心方法：
```

#### `create_task_agent()` — 创建任务执行 Agent
```
功能：从 PlannedTask 构建完整的 Agent
流程：
  1. 解析 task.skill_id → 加载对应 SKILL.md 指令
  2. 从 task.model/model_ref 选择模型（或使用默认）
  3. 从 task.mcp 筛选需要的 MCP Server
  4. 构建 system prompt（Skill 指令 + 执行契约）
  5. 注入工具使用规则（允许的工具列表 + 预算）

生成的 System Prompt 结构：
  ---
  [Skill 指令正文]
  ---
  ## 执行契约
  - 依赖项：[task.depends_on]
  - 验收标准：[task.acceptance_criteria]
  - 预期产出：[task.expected_outputs]
  - 读取范围：[task.read_set]
  - 写入范围：[task.write_intent]
  - 编辑模式：[task.edit_mode: strict / compat]
  ---
  ## 工具规则
  - 可用工具：[筛选后的 MCP 工具列表]
  - 最大工具调用次数：[task.tool_budget or 默认值]
  - 写入操作需要确认
```

#### `create_direct_answer_agent()` — 直接回答 Agent
```
功能：不需要工具调用的简单问答
特点：
  - 不加载 MCP Server（无工具）
  - model 使用轻量模型（优先 query_refiner 角色模型）
  - instructions = "直接、简洁地回答用户问题"
```

#### `create_solo_agent()` — Solo 模式 Agent
```
功能：单 Agent 直接执行（无规划管线）
特点：
  - 加载全部 7 个 MCP Server
  - instructions 明确声明 "类似 Claude CLI"
  - 无执行契约（因为无 Planner 产出）
  - 工具权限：所有可用，但写入需确认
```

#### `create_synthesizer_agent()` — 最终合成 Agent
```
功能：多 Agent 执行后的结果合并
特点：
  - 只加载只读 filesystem MCP（不需要写入）
  - model 使用 final_synthesizer 角色模型
  - instructions：整合各子任务产出 → 形成统一回答
```

#### 关键辅助方法
```
_resolve_task_model(task, default_model_id):
  - 解析 task.model 引用 → 实际 model_id
  - 回退到 task.model_ref → 回退到 default_model_id

_select_mcp_servers(task, available_servers):
  - 根据 task.mcp（可能是 server_id 列表）筛选
  - 默认加载所有可用的 MCP Server
  - 离线模式下排除网络 MCP（web_search）

_generate_tool_rules(task):
  - 从 task 生成工具使用限制
  - read_set → 限制可读路径
  - write_intent → 限制可写路径
  - tool_budget → 限制工具调用次数
```

### 4.3 runtime/modes/ — 三种执行模式

#### solo.py — 直接执行模式
```
run_solo_request(prompt, model_registry, mcp_manager, hooks, run_agent, settings):
  1. 创建 solo Agent（create_solo_agent）
  2. 直接调用 run_agent(agent, prompt)
  3. 返回结果（无规划、无审计、无回滚）

适用场景：简单问答、代码解释、快速操作
优点：零规划开销，响应快
```

#### serial.py — 串行规划执行模式
```
run_serial_request(prompt, workspace_root, model_registry, mcp_manager, hooks, run_agent, settings, show_plan):
  1. preview_plan() → 展示执行计划
  2. execute_dynamic_request() → 按计划串行执行
  3. audit_execution() → 审计结果
  4. 如有问题 → repair_loop

适用场景：中等复杂度的多步骤任务
特点：task 一个接一个执行，无并行
```

#### full.py — 完整管线模式
```
run_full_request(prompt, workspace_root, model_registry, mcp_manager, hooks, run_agent, settings, show_plan):
  1. preview_plan() → 完整规划管线（refiner → planner → validator → reviewer）
  2. execute_dynamic_request() → 智能并行/串行调度
  3. audit_execution() → 逐项审计验收标准
  4. repair_loop() → 不通过则重试（最多 3 轮）
  5. flywheel → 记录经验教训

适用场景：复杂的多文件、多步骤开发任务
特点：支持并行 Agent 执行 + 写冲突检测
```

---

## 五、第 4 层：MCP 工具服务器层

### 5.1 mcp_servers/__init__.py — MCP Server 管理器

**文件定位**：统一管理 7 个 MCP Server 的生命周期（启动/停止/清理）

#### `MCPServerManager` 类
```
功能：异步上下文管理器，统一 MCP Server 生命周期
构造参数：
  - workspace_root: Path     → 工作区根目录
  - quarantine_dir: Path     → 安全隔离目录（暂存删除的文件）
  - verbose: bool = False    → 是否输出调试日志

实现：
  - 使用 AsyncExitStack 管理异步资源
  - enter_async_context() → 懒启动所有 MCP Server（首次访问时启动）
  - 每个 Server 作为子进程运行：sys.executable -m <module_path>
  - 环境变量清理：_safe_mcp_env() 移除 API_KEY/TOKEN/SECRET/PASSWORD

7 个 MCP Server 工厂：
  1. create_readonly_filesystem_server()  — 文件读取/搜索
  2. create_safe_delete_server()          — 安全删除（移入 quarantine）
  3. create_web_search_server()           — Web 搜索
  4. create_code_locator_server()         — 代码定位（grep/ast）
  5. create_workspace_edit_server()       — 文件写入/编辑
  6. create_command_runner_server()       — Shell 命令执行
  7. create_git_tools_server()            — Git 操作

静态工具过滤：
  - 每个 Server 只暴露预定义的工具列表
  - 例如 filesystem Server 不暴露 write_file，edit Server 不暴露 execute_command
  - 即使用户通过 MCP 扩展添加工具，也不会意外暴露危险操作
```

#### 安全设计细节
```
_safe_mcp_env():
  功能：清理子进程环境变量
  逻辑：移除所有包含 API_KEY/TOKEN/SECRET/PASSWORD 的环境变量
  原因：MCP Server 是子进程，默认继承父进程的全部环境变量
        如果不清除，API key 会泄露给所有 MCP Server 子进程

Server 隔离：
  - 每个 Server 独立子进程
  - 一个 Server 崩溃不影响其他 Server
  - 支持单独重启故障 Server
```

### 5.2 MCP Server 工具分类

```
┌──────────────────────┬──────────┬────────────────────────────────┐
│ Server               │ 风险等级 │ 工具示例                        │
├──────────────────────┼──────────┼────────────────────────────────┤
│ readonly_filesystem  │ read     │ read_file, list_directory,     │
│                      │          │ search_files, glob             │
│ code_locator         │ read     │ search_code, find_definition,  │
│                      │          │ list_symbols                    │
│ web_search           │ read     │ web_search, fetch_url           │
│ git_tools            │ read     │ git_status, git_diff, git_log  │
│ workspace_edit       │ write    │ write_file, edit_file,         │
│                      │          │ create_directory                │
│ command_runner       │ shell    │ execute_command                 │
│ safe_delete          │ delete   │ delete_file, restore_file      │
└──────────────────────┴──────────┴────────────────────────────────┘

为什么要把读写分离到不同 Server？
  1. 权限控制粒度：可以独立配置每个 Server 的允许/询问/拒绝策略
  2. 安全隔离：读 Server 不暴露写工具，写 Server 不暴露执行能力
  3. 审计追踪：日志中清晰区分读操作和写操作
  4. 隐私模式：离线模式下可自动禁用 web_search Server
```

---

## 六、第 5 层：执行引擎层

### 6.1 runtime/execution/dynamic.py — 动态执行管线（~1200 行）

**文件定位**：项目的"执行大脑"——将计划转化为实际 Agent 运行。

#### `execute_dynamic_request()` — 主执行函数
```
参数：
  - refined_request: RefinedRequest  — 精炼后的用户需求
  - planner_result: PlannerResult    — 执行计划
  - workspace_root: Path
  - model_registry: ModelRegistry
  - mcp_manager: MCPServerManager
  - hooks: RunHooks
  - run_agent: Callable              — Agent 运行闭包
  - settings: RuntimeSettings
  - show_plan: bool
  - session_checkpoints: SessionCheckpointManager | None

执行流程：
  1. 创建 RunWorkspace（临时多 Agent 输出共享目录）
  2. 根据 planner_result.route_type 分发：
     a. direct_answer  → _run_direct_answer()
     b. single_agent   → _run_single_agent()
     c. multi_agent    → _run_multi_agent()
     d. clarify        → 返回澄清问题
  3. 代码管线 Gate 决策：
     apply_pipeline_gate(planner_result) → GateDecision
     判断是否需要 code_locator + verifier
  4. 验证：build_verification_report()
  5. 审计：audit_execution() → 检查验收标准达成情况
  6. 修复循环：should_retry() → 重新规划 + 执行
  7. Flywheel 记录：成功模式 + 失败教训
```

#### `_run_single_agent()` — 单 Agent 执行
```
功能：使用 AgentFactory 创建 Agent 并执行
流程：
  1. 解析 task 的 model/mcp/skill
  2. AgentFactory.create_task_agent() 创建 Agent
  3. run_agent(agent, task.instruction)
  4. 返回 Agent 输出
```

#### `_run_multi_agent()` — 多 Agent 协调执行
```
功能：按依赖关系调度多个 Agent
核心数据结构：
  - RunWorkspace: 临时目录，Agent 间共享输出文件
  - 任务状态追踪（pending → running → completed → failed）

调度算法：
  1. 构建依赖图（task.depends_on 列表）
  2. 拓扑排序 → 确定执行层级
  3. 每层内部：
     a. 检查 parallel_group → 同组任务可并行
     b. 并行前检查 _write_sets_conflict() → 写文件路径冲突检测
     c. 无冲突 → asyncio.gather() 并行执行
     d. 有冲突 → 降级为串行执行
  4. 每层完成后 → 收集结果 → 传递给下一层的依赖任务
  5. 所有任务完成后 → 可选合成 Agent（create_synthesizer_agent）

核心安全机制：
  _write_sets_conflict(task_a, task_b):
    检查两个任务的 write_intent 是否有路径重叠
    例如：Task A 写 src/api.py，Task B 写 src/api.py → 冲突，串行化
    例如：Task A 写 src/api.py，Task B 写 src/models.py → 可并行
```

#### `apply_pipeline_gate()` — Gate 决策
```
功能：判断当前任务是否需要代码管线增强
输入：PlannerResult
输出：GateDecision

决策逻辑：
  needs_code_pipeline = any of:
    - task.write_intent 不为空（需要写文件）
    - task.edit_mode == "strict"（严格编辑模式）
    - route_type == "multi_agent"（多 Agent 协作）
    - task.acceptance_criteria 含测试相关词汇

  should_verify = needs_code_pipeline OR user_explicitly_requests_verification
  risk_level = "low" / "medium" / "high" 基于：
    - low: 只读操作
    - medium: 单文件写入
    - high: 多文件写入 / shell 命令执行
```

#### 快速路径
```
_can_fast_path_url_search(query):
  检测是否为纯 URL 搜索请求 → 跳过规划，直接调用 web_search MCP

_can_fast_path_git_status(query):
  检测是否为纯 git 状态查询 → 直接调用 git MCP

内联文件上下文：
  对于简单文件分析请求 → 直接 read 文件内容注入 prompt
  不消耗 MCP 工具调用轮次
```

### 6.2 runtime/execution/pipeline.py — 管线数据结构

```
GateDecision:
  - needs_code_pipeline: bool     # 是否需要代码定位 + 验证管线
  - edit_intent: bool             # 是否有编辑意图
  - test_intent: bool             # 是否有测试意图
  - should_verify: bool           # 是否需要执行后验证
  - risk_level: str               # low / medium / high

PipelineRunState:
  跟踪任务执行状态：
  - status: pending / running / completed / failed
  - TaskRunRecord: 每个任务的执行记录
    - id, title, skill_id, model, mcp
    - status, output_preview
    - verification (验证报告引用)
```

---

## 七、第 6 层：规划层

### 7.1 planning/planner.py — 规划引擎

**文件定位**：将用户的自然语言需求转化为结构化的执行计划。

#### `preview_plan()` — 规划预览
```
参数：
  - user_input: str
  - workspace_root: Path
  - model_registry: ModelRegistry
  - skill_registry: SkillRegistry
  - privacy_policy: PrivacyPolicy
  - settings: RuntimeSettings

执行流程：
  1. 选择 refiner_model（query_refiner 角色模型）
  2. 如果 refiner 启用：
     build_query_refiner() → Agent → refined_request (RefinedRequest)
  3. 如果 refiner 禁用：
     build_refined_request_without_refiner() → Fallback
  4. 选择 planner_model（orchestrator 角色模型）
  5. build_orchestrator_planner() → Agent → PlannerResult
  6. plan_validator → 校验
  7. plan_reviewer → 安全审查
  8. format_plan_preview() → 终端展示
  9. 用户确认（y/n/modify）
```

#### `build_query_refiner()` — 需求精炼 Agent
```
功能：将模糊的用户需求转化为结构化的需求描述
System prompt 包含：
  - 项目上下文（workspace_root 文件结构摘要）
  - 精炼要求：提取核心意图、约束条件、上下文信息
  - 输出格式：结构化 JSON

输入：用户原始输入
输出：RefinedRequest（JSON）
  {
    "intent": "创建 Flask REST API",
    "constraints": ["使用 SQLAlchemy", "需要 Token 认证"],
    "context": "已有 Django 项目，想迁移到 Flask",
    "missing_info": ["数据库类型未指定"]
  }
```

#### `build_orchestrator_planner()` — 编排规划 Agent
```
功能：将精炼需求拆解为可执行任务列表
System prompt 包含：
  - 完整的 Skill 目录（每个 Skill 的名称、描述、能力）
  - MCP Server 目录（可用的工具类别）
  - Model 目录（可用模型及其能力）
  - 权限策略摘要（read/write/shell 允许范围）
  - 隐私模式约束

输出：PlannerResult（JSON）
  {
    "route_type": "multi_agent",
    "tasks": [
      {
        "id": "task_1",
        "title": "创建 Flask 项目结构",
        "instruction": "创建 app.py, models.py, routes.py...",
        "skill_id": "project_explorer",
        "model": "deepseek_v4_pro",
        "mcp": ["readonly_filesystem", "workspace_edit"],
        "parallel_group": null,
        "depends_on": [],
        "acceptance_criteria": ["文件存在", "导入无错误"],
        "expected_outputs": ["app.py", "models.py", "routes.py"],
        "read_set": ["*.py"],
        "write_intent": ["app.py", "models.py", "routes.py"]
      },
      ...
    ]
  }

返回格式处理：
  - parse_planner_result() 支持多种 JSON 格式
    · 纯 JSON
    · ```json 代码块包裹
    · 模型"思考"文本后的 JSON（strip reasoning noise）
  - build_fallback_planner_result() 保守回退
    · 弱模型不输出 JSON 时，基于关键词检测生成计划
```

### 7.2 planning/planner_schema.py — 规划数据结构

```
RefinedRequest:
  - intent: str            # 核心意图
  - constraints: list[str] # 约束条件
  - context: str           # 上下文信息
  - missing_info: list[str] # 缺失信息

PlannedTask:
  - id: str                # 唯一标识
  - title: str             # 任务标题
  - instruction: str       # 执行指令
  - skill_id: str          # 使用的 Skill
  - model: str | None      # 指定模型
  - mcp: list[str]         # 需要的 MCP Server
  - parallel_group: str | None  # 并行组（同组可并行）
  - depends_on: list[str]  # 依赖的任务 ID
  - acceptance_criteria: list[str]  # 验收标准
  - expected_outputs: list[str]     # 预期产出
  - read_set: list[str]    # 需要读取的文件（glob）
  - write_intent: list[str] # 意图写入的文件
  - edit_mode: str         # strict / compat

PlannerResult:
  - route_type: str        # direct_answer / single_agent / multi_agent / clarify
  - tasks: list[PlannedTask]
  - synthesis_instruction: str | None
  - notes: str | None

核心函数：
  parse_planner_result(raw_text) → PlannerResult
    多层 JSON 解析策略：
    1. 尝试直接 json.loads()
    2. 提取 ```json...``` 代码块
    3. 正则提取 {...}
    4. 都失败 → build_fallback_planner_result()

  build_fallback_planner_result(user_input) → PlannerResult
    保守策略：
    - 检测代码相关词汇 → single_agent + 代码 Skill
    - 检测聊天/问答词汇 → direct_answer
    - 检测写入词汇 → single_agent + edit MCP
    - 检测搜索词汇 → single_agent + web_search MCP

  _normalize_planner_result(result):
    - 检测 web 搜索意图（URL 或 "搜索" 关键词）
    - 规范化 model 引用（"deepseek" → "deepseek_v4_pro"）
    - 多 Agent 无 synthesis_instruction → 自动补充

  _lost_action_intent(original, refined):
    检测 refiner 是否削弱了原始输入中的动作意图
    例如：原始"帮我修复 bug" → refiner "分析这个 bug"
    检测到则重新注入原始动作动词
```

### 7.3 planning/plan_validator.py — 计划校验器

```
功能：在计划执行前验证其可行性
检查项：
  1. task 数量检查：
     - direct_answer: 0 个 task
     - single_agent: 1 个 task
     - multi_agent: ≥2 个 task
     - 不符合 → 警告 + 修正

  2. 引用的 skill_id 在 SkillRegistry 中是否存在
     - 不存在 → 移除该 skill 引用

  3. 引用的 model 是否已配置且可用
     - 不可用 → 替换为默认模型

  4. 隐私策略检查：
     - 离线模式 + web_search mcp → 警告
     - local_first + web_search mcp → 警告但允许

  5. MCP Server 授权检查：
     - 请求的 mcp 是否在 permissions.toml 中被阻止
     - 被阻止 → 移除该 MCP

  6. 工具能力检查：
     - task.instruction 是否要求了工具不支持的操作
     - 例如：无 edit MCP 但指令要求写文件 → 警告

  7. 依赖关系检查：
     - depends_on 引用的 task_id 是否存在
     - 不存在 → 移除无效依赖

返回：经过修正的 PlannerResult + 警告列表
```

### 7.4 planning/plan_reviewer.py — 计划安全审查器

```
功能：在计划执行前审查安全风险
审查项：

  1. 验收标准审查：
     - 每个 task 是否有 acceptance_criteria → 无则警告
     - 验收标准是否可验证（检查是否含可观察行为）

  2. 依赖关系审查：
     _review_dependencies(tasks):
       - 检查未知依赖（depends_on 指向不存在的 task id）
       - DFS 循环依赖检测：
         对每个 task，沿着 depends_on 链 DFS 遍历
         若回到自身 → 报告循环依赖
       - 同一 parallel_group 内有依赖 → 警告（冲突）

  3. 并行写冲突审查：
     _review_parallel_write_conflicts(tasks):
       - 同一 parallel_group 内的 task
       - 检查 write_intent 是否有路径重叠
       - 重叠 → 冲突警告，建议拆分到不同 group 或串行化

返回：审查报告（warnings + issues）
```

---

## 八、第 7 层：Skills 层

### 8.1 skills/registry.py — Skill 注册表

```
功能：管理 8 个内置 Skill 的注册与查询
数据格式（SKILL.md）：
  ---
  name: jpc_now_skill
  description: 提供日期/时间信息
  ---
  # Skill 指令正文...

当前注册的 8 个 Skill：

┌─────────────────────┬──────────────────────────────────────────────┐
│ Skill ID            │ 功能                                         │
├─────────────────────┼──────────────────────────────────────────────┤
│ jpc_now_skill       │ 提供日期/时间（让 Agent 知道"现在是什么时候"） │
│ humanizer_zh        │ 中文输出人性化（自然对话风格）                  │
│ project_explorer    │ 项目结构探索与分析                             │
│ skill_creator       │ 创建新的 Skill（元能力）                       │
│ task_router         │ 任务路由与分发                                │
│ query_refiner       │ 用户需求精炼（Plan 管线中的 refiner）          │
│ orchestrator_planner│ 任务编排规划（Plan 管线中的 planner）           │
│ final_synthesizer   │ 多 Agent 结果合成                              │
└─────────────────────┴──────────────────────────────────────────────┘

Skill 选择逻辑：
  1. Planner 根据任务类型自动选择 skill_id
  2. AgentFactory.create_task_agent() 加载对应 SKILL.md
  3. Skill 内容注入 system prompt（在工具/约束之上）
```

### 8.2 Skill 设计模式

```
每个 SKILL.md 的结构：
  ---
  name: <skill_id>
  description: <简短描述>  ← 供 Planner 判断是否需要此 Skill
  mcp: [所需 MCP 列表]     ← 声明工具依赖
  ---
  # 角色定义
  你是 [角色描述]
  
  # 工作流程
  1. [步骤 1]
  2. [步骤 2]
  
  # 约束
  - [限制 1]
  - [限制 2]
  
  # 输出格式
  [期望的输出结构]

Skill 的三层优先级：
  - workspace > user > core
  - 项目可以覆盖内置 Skill（例如自定义 project_explorer）
  - _mark_skill_safety() 阻止覆盖系统保护型 Skill
```

---

## 九、第 8 层：安全架构层

这是 agents_demo 与市面上其他 Agent 工具**最大的差异化层**。大部分 Agent 工具只有简单的 "允许/拒绝" 权限，而 agents_demo 构建了四维安全模型。

### 9.1 runtime/safety/permissions.py — 权限策略

```
DEFAULT_PERMISSION_POLICY:
  read:   allow   # 读文件默认允许
  write:  ask     # 写文件默认询问
  delete: ask     # 删除默认询问
  shell:  ask     # 执行命令默认询问
  git:    ask     # Git 操作默认询问
  web:    ask     # 网络请求默认询问
  mcp:    ask     # 扩展 MCP 默认询问

load_effective_permissions(workspace_root):
  1. 加载默认策略
  2. 合并 .lucode/permissions.toml（项目级）
  3. 合并用户自定义规则（未来扩展）

evaluate_permission(action_type, target, policy):
  支持两种匹配模式：
    1. 路径匹配（fnmatch）
       例如：deny = [".env", "**/*.pem", "**/*secret*"]
    2. 命令匹配（前缀匹配）
       例如：deny = ["git reset --hard", "rm -rf"]

返回：allow / ask / deny
```

### 9.2 runtime/safety/privacy.py — 隐私策略

```
PrivacyPolicy（三种模式）：

  offline:
    - 只允许本地模型（local/ollama backend）
    - 禁止 web_search MCP
    - 禁止任何网络请求
    - 适用场景：完全离线环境，代码不出本地

  local_first:
    - 优先使用本地模型
    - 允许 cloud fallback（本地模型不可用时）
    - 允许 web_search（但会提示）
    - 适用场景：日常开发，倾向本地但不绝对

  cloud_allowed:
    - 允许所有模型和网络请求
    - 适用场景：复杂任务需要最强模型

model_allowed(backend, policy):
  - offline + "cloud" → False
  - local_first + "cloud" → True（fallback）
  - cloud_allowed → True

mcp_allowed(mcp_type, policy):
  - offline + "web_search" → False
  - 其他 → True

sort_model_ids(model_ids, policy):
  - local_first 模式：local 模型排在 cloud 模型前面
```

### 9.3 runtime/safety/auditor.py — 执行审计

```
audit_execution(tasks, outputs, acceptance_criteria, verification_report):
  功能：执行后审计——检查任务是否真正完成了预期目标

  审计流程：
  1. 验收标准检查：
     for each acceptance_criteria:
       _criterion_looks_satisfied(criterion, outputs, verification)
       
  2. _criterion_looks_satisfied() 的语义检查：
     - 提取 criterion 的关键词
     - 在 outputs + verification 中搜索
     - 关键词匹配率达到 70% → 认为 satisfied
     - 例如 criterion "文件 api.py 存在" → 搜索 outputs 中是否含 "api.py" + "创建"/"写入"
     
  3. 预期产出检查：
     for each expected_outputs:
       检查文件是否实际存在（或 git diff 中是否出现）

  4. 验证报告分析：
     - 检查 git diff 是否包含预期修改
     - 检查自定义验证命令（AGENTS_VERIFY_COMMANDS）是否通过

  5. 生成 AuditReport：
     - passed_criteria: 通过的验收标准
     - failed_criteria: 未通过的验收标准
     - warnings: 警告信息
     - remaining_issues: 遗留问题
     - rollback_status: 是否已回滚

format_final_report(audit_result):
  结构化输出审计结果：
    ✅ 已通过：...
    ❌ 未通过：...
    ⚠️ 警告：...
    📝 遗留问题：...
    🔄 回滚状态：...
```

### 9.4 runtime/safety/checkpoint.py — Git 检查点

```
create_checkpoint(workspace_root, planned_files):
  功能：在 Agent 修改文件前创建回滚检查点
  
  5 种检查点模式：
  
  mode 1: "none"
    条件：不在 git 仓库中
    行为：无法回滚，记录警告
  
  mode 2: "scoped_conflict_protected"
    条件：用户有未暂存修改，且修改的文件与 Agent 计划修改的文件有交集
    行为：拒绝执行！用户需要先 commit 或 stash
    保护：防止 Agent 覆盖用户的工作
  
  mode 3: "scoped_patch_rollback"
    条件：用户有未暂存修改，但与 Agent 的计划文件不冲突
    行为：记录 Agent 将修改的文件列表，支持 scoped 回滚
         回滚时只 git checkout Agent 修改的文件，不影响用户的其他修改
  
  mode 4: "git_dirty_protected"
    条件：用户有未暂存修改，且无法安全隔离
    行为：不支持自动回滚，但继续执行（记录警告）
  
  mode 5: "git_clean_head"
    条件：工作区完全干净
    行为：支持完整 git reset --hard + git clean -fd 回滚

rollback_checkpoint(checkpoint):
  - clean_head → git reset --hard HEAD + git clean -fd
  - scoped → git checkout -- <agent_modified_files>
  - dirty_protected → 输出警告，无法自动回滚
```

### 9.5 runtime/safety/session_checkpoint.py — 会话级检查点

```
SessionCheckpointManager:
  功能：管理整个 chat 会话的回滚状态
  每轮对话对应一个检查点

  pre_turn_checkpoint():
    在 Agent 开始执行前调用
    记录当前 git 状态 + 已有 dirty files 列表

  post_turn_checkpoint():
    在 Agent 执行完成后调用
    计算 Agent 修改的文件（当前 dirty files - baseline dirty files）
    存储检查点信息

  rollback_last_turn():
    回滚最近完成的轮次
    只回滚 Agent 修改的文件，不影响用户自己的修改
```

### 9.6 runtime/safety/repair_loop.py — 修复循环

```
should_retry(audit_result, attempt_count, max_attempts=3):
  条件：
    - audit_result.needs_replan == True
    - attempt_count < max_attempts
  返回：True/False

build_repair_request(audit_result, original_request):
  根据审计失败类型构建修复指令：
    - "验收标准 '...' 未通过。原始任务：... 请修复并重试。"

repair_strategy_for_audit(audit_result):
  4 种修复策略：
    1. verification_failed  → 重新执行失败的验证命令
    2. tool_capability_mismatch → 更换 MCP Server 或 Skill
    3. write_conflict → 串行化冲突任务
    4. general_replan → 完全重新规划

修复流程：
  1. 检测失败类型 → 选择修复策略
  2. 构建修复请求（含失败上下文）
  3. 重新规划 → 重新执行
  4. 重新审计
  5. 最多 3 轮（防止无限循环）
```

---

## 十、第 9 层：UI 与运行时辅助层

### 10.1 runtime/ui/welcome.py — 欢迎面板

```
render_welcome_dashboard(workspace, mode_label, model_summary, privacy_label, mcp_count, skill_count):
  使用 ANSI box-drawing 字符构建 ASCII 欢迎面板
  ┌───────────────┐
  │  Lucode       │
  │  Terminal     │
  │  Agent        │
  └───────────────┘
  工作区：/path
  模式：solo  · 隐私：local_first
  模型：deepseek-v4-pro
  MCP 工具：7  ·  Skills：8
  
  启动提示：输入任务开始...

支持 NO_COLOR 环境变量 → 禁用 ANSI 颜色
支持 LUCODE_NO_LOGO → 跳过 Logo 动画
```

### 10.2 runtime/ui/progress.py — 进度展示

```
render_task_status_board(tasks, run_state):
  展示 C5 风格的任务进度表：
     ✓ task_1  创建 Flask 项目结构    [已完成]
     > task_2  实现认证 API            [执行中]
     ✗ task_3  编写测试                [失败]
     · task_4  部署配置                [等待中]

  符号含义：
    ✓ → completed
    > → running
    ✗ → failed
    · → pending

render_runtime_statusline(mode, mcp_list, active_task):
  单行状态栏：
    [solo] MCP:7 | 执行中: task_2 | 耗时: 12s
```

### 10.3 runtime/ui/command_palette.py — 命令面板

```
23 个 slash 命令的注册表与搜索
render_command_palette(filter=""):
  支持过滤搜索：
    输入 /mod → 显示 /mode, /model 两个匹配
  每个命令显示中文描述
```

### 10.4 runtime/memory/flywheel.py — 飞轮记忆

```
FlywheelStore:
  功能：JSONL 格式的经验记忆库
  存储内容：
    - 成功执行总结（pipeline_summary）
    - 失败案例（failure_cases）
    - 修复经验（repair_lessons）
  
  search(query):
    简单的关键词匹配搜索
    返回相关的历史经验

  设计意图：
    - 让 Agent 从过去的执行中学习
    - 类似 Claude Code 的 "最近编辑的 5 个文件" 恢复机制
    - 但更强调"经验积累"而非"状态恢复"
```

### 10.5 runtime/workspace/patch_ledger.py — 补丁账本

```
PatchProposalLedger:
  功能：追加式 JSONL 记录所有文件修改提案
  每条记录：
    - task_id: 任务标识
    - file_path: 修改的文件
    - hash_before: SHA256 修改前哈希
    - proposed_content: 修改内容摘要
    - outcome: applied / rejected / rolled_back

  用途：
    - 审计追踪（谁改了哪个文件）
    - 回滚验证（对比修改前后哈希）
    - 问题诊断（查看修改历史）
```

### 10.6 runtime/workspace/run_workspace.py — 运行工作区

```
RunWorkspace:
  功能：多 Agent 协作的临时共享目录
  路径：.agent_quarantine/run_<timestamp>/
  
  使用场景：
    - Agent A 生成中间结果 → 写入 RunWorkspace
    - Agent B 读取中间结果 → 继续处理
    - 合成 Agent 整合所有中间结果

  安全：路径禁锢检查
    - 确保 Agent 的写入操作锁定在 RunWorkspace 内
    - 防止意外写入到项目目录
```

### 10.7 runtime/common/conversation.py — 对话压缩

```
append_recent_turn(recent_turns, role, content, max_chars=800):
  功能：追加一轮对话到最近记录
  截断策略：如果 content 超过 max_chars → 截断 + "..."

compose_recent_context(recent_turns):
  功能：将最近对话记录打包为上下文前缀
  格式：
    最近对话：
    USER: ...
    ASSISTANT: ...
    USER: ...
    ASSISTANT: ...

与 Claude Code 的对比：
  - Claude Code：4 级压缩（TRUNCATE→DEDUPLICATE→FOLD→SUMMARIZE）+ 自动触发
  - agents_demo：简单截断到最近 6 轮 + 800 字符限制
  - 差距较大，但基本够用
```

---

## 十一、简历定位分析

### 11.1 这个项目"是什么"——一句话定位

> **Lucode** 是一个具备**规划-执行-审计三层分离架构**、**多 Agent 并行协调能力**和**四维安全模型**的终端编码 Agent——它在架构深度上超越了市面上单纯的"LLM + 工具调用"包装器。

### 11.2 与市面上每个产品的差异化对比

#### vs Claude Code CLI
| 维度 | Claude Code CLI | agents_demo (Lucode) |
|------|----------------|---------------------|
| Agent 架构 | 单 Agent + 自研 Loop | **多 Agent 并行协调** + 规划/执行/审计三层分离 |
| 任务分解 | LLM 自行判断 | **Planner→Validator→Reviewer 三阶段规划管线** |
| 安全模型 | 5 层权限（命令级别） | **四维安全**：权限 × 隐私 × 审计 × 回滚 |
| 终端 UI | React+Ink+Yoga（顶级） | 纯文本（差距大，但可替换） |
| 多厂商模型 | 仅 Anthropic | **多 Provider + 三层配置** |

**简历突出点**：设计了 Claude Code 不具备的多 Agent 规划-执行-审计三层架构，实现了任务自动分解、并行调度和结果审计的完整闭环。

#### vs OpenCode
| 维度 | OpenCode | agents_demo (Lucode) |
|------|----------|---------------------|
| Provider 支持 | 19+ Provider SDK 抽象 | 3+ Provider（可扩展） |
| 模型切换 | 多层懒加载 + SDK 缓存 | 隐私感知的三层模型选择 |
| 规划管线 | 无（直接执行） | **完整的规划-校验-审查管线** |
| Agent 协调 | 无 | **拓扑排序 + 写冲突检测的并行调度** |
| 配置系统 | opencode.json 结构化 | **三级配置合并**（用户/项目/环境变量） |

**简历突出点**：项目具备 OpenCode 缺乏的规划管线和多 Agent 协调能力——后者只是模型切换工具，而前者是完整的自主执行系统。

#### vs Cursor
| 维度 | Cursor | agents_demo (Lucode) |
|------|--------|---------------------|
| 定位 | IDE 插件（补全+Chat） | **独立终端 Agent（完整执行）** |
| 文件操作 | 用户手动 | **Agent 自主规划+执行+验证** |
| 多步骤任务 | 逐轮手动引导 | **自动拆解→并行执行→审计** |
| 离线能力 | 依赖云端模型 | **Ollama 本地模型 + 离线隐私模式** |
| 安全模型 | IDE 权限沙箱 | **Git checkpoint + 权限矩阵 + 隐私策略** |

**简历突出点**：与 Cursor 的被动辅助不同，Lucode 实现了主动式多步骤任务自主执行——从需求理解到代码交付的全自动管线。

#### vs GitHub Copilot
| 维度 | GitHub Copilot | agents_demo (Lucode) |
|------|---------------|---------------------|
| 能力范围 | 代码补全 + Chat | **完整软件工程任务执行** |
| 自主性 | 零（纯被动响应） | **全自主（规划→执行→审计→修复）** |
| 架构可见性 | 闭源黑盒 | **完全开源，分层清晰** |
| 多模型 | 仅 OpenAI/GitHub 模型 | **多厂商 + 本地模型 + 隐私感知路由** |

**简历突出点**：从代码补全到全自主软件工程——Lucode 证明了独立开发者也能构建Copilot 级别之上的、具备真正自主性的 Agent 系统。

### 11.3 简历中应突出的 7 个技术亮点

#### 亮点 1：三层分离的 Agent 架构（核心差异化）
```
规划层（Plan）→ 执行层（Execute）→ 审计层（Audit）
不是"一个 Agent 一把梭"，而是像编译器一样：
  前端（词法/语法分析）  → Planner
  中端（优化/代码生成）  → Executor
  后端（验证/纠错）      → Auditor

这在市面上的 Agent 工具中独一无二。
```

#### 亮点 2：多 Agent 并行协调系统
```
- 拓扑排序的任务依赖解析
- 并行组内的写文件冲突检测（自动降级为串行）
- RunWorkspace 共享输出机制
- 子任务产出 → 合成 Agent 自动整合
```

#### 亮点 3：四维安全模型
```
权限（Permissions）：路径 + 命令双层匹配
隐私（Privacy）：offline/local_first/cloud_allowed 三模式
审计（Auditor）：验收标准语义检查 + 预期产出验证
回滚（Checkpoint）：5 种 Git 检查点模式 + scoped 回滚
```

#### 亮点 4：隐私感知的模型路由
```
- offline 模式完全本地执行
- local_first 模式优先本地，cloud 作为 fallback
- 根据隐私模式自动过滤可用模型和 MCP 工具
- 模型探测系统（实际可用性验证，非声明式）
```

#### 亮点 5：Gate 决策系统
```
自动判断任务是否需要：
  - 代码定位管线（code_locator MCP）
  - 测试生成（test_intent 检测）
  - 执行后验证（git diff + 自定义验证命令）
  - 风险等级评估（low/medium/high）
```

#### 亮点 6：修复循环与飞轮记忆
```
- 审计失败 → 自动修复策略选择 → 重新规划 → 重新执行（最多 3 轮）
- Flywheel JSONL 记忆：过去的成功/失败经验影响未来的执行策略
```

#### 亮点 7：全异步 + 运行时安全
```
- 7 个 MCP Server 独立子进程（环境变量清理 + 风险隔离）
- 统一的 stdin 适配器（防止异步竞争）
- 运行时 /stop 打断机制（后台线程监听）
- API key 用户级隔离（auth.json chmod 600，不进入项目目录）
```

### 11.4 建议的简历 Bullet Points

**项目描述（1-2 句）**：
> 从零设计并实现了一个具备规划-执行-审计三层分离架构的终端编码 Agent 系统。支持多 Agent 并行协调、四维安全模型（权限 × 隐私 × 审计 × 回滚）、多厂商模型路由和 MCP 协议工具扩展。区别于市面上的 LLM 包装器，实现了真正的自主软件工程能力。

**核心技术贡献（5-7 条）**：

1. **设计了规划-执行-审计三层分离的 Agent 架构**，通过 Query Refiner → Orchestrator Planner → Plan Validator → Plan Reviewer 四阶段管线将模糊需求转化为结构化可执行计划，并将执行结果对照验收标准进行自动化审计

2. **实现了多 Agent 并行协调系统**：拓扑排序解析任务依赖图，写文件冲突检测自动降级并行任务为串行，RunWorkspace 临时目录实现 Agent 间输出共享，Synthesizer Agent 自动整合子任务结果

3. **构建了四维安全模型**：(1) 路径+命令双层匹配的权限策略；(2) offline/local_first/cloud_allowed 三模式隐私感知路由；(3) 语义级验收标准审计；(4) 5 种 Git 检查点模式的 scoped 回滚

4. **设计了三层 Provider→Model 配置架构**，支持多厂商模型（DeepSeek/MiMo/Ollama）的隐私感知自动选择和运行时切换，每模型独立配置 API endpoint 和能力参数

5. **实现了 MCP 协议工具系统**：7 个独立子进程的 MCP Server（文件读写/代码定位/命令执行/Git/Web 搜索），按风险等级分离部署，环境变量自动清理防泄露

6. **实现了修复循环与飞轮记忆**：审计失败后自动选择修复策略（验证重试/工具替换/冲突串行化/重新规划），JSONL 格式的 Flywheel 经验库持续优化后续执行质量

7. **全异步架构**：asyncio 驱动的 REPL 交互循环，后台线程 stdin 监听支持运行时任务中断，7 个 MCP Server 独立子进程隔离单点故障

---

## 十二、与市面工具的差异化矩阵

```
┌─────────────────────┬──────────┬──────────┬────────┬────────┬──────────────┐
│ 能力维度             │ Lucode   │ Claude   │OpenCode│ Cursor │ GitHub       │
│                     │          │ Code CLI │        │        │ Copilot      │
├─────────────────────┼──────────┼──────────┼────────┼────────┼──────────────┤
│ 多 Agent 并行协调   │    ✅    │    ❌    │   ❌   │   ❌   │     ❌       │
│ 规划管线(refiner+   │    ✅    │    ❌    │   ❌   │   ❌   │     ❌       │
│   planner+validator │          │          │        │        │              │
│   +reviewer)        │          │          │        │        │              │
│ 自动化审计+修复循环 │    ✅    │    ❌    │   ❌   │   ❌   │     ❌       │
│ Git Checkpoint回滚  │    ✅    │    ❌    │   ❌   │   ❌   │     ❌       │
│ 隐私三模式感知路由  │    ✅    │    ❌    │   ❌   │   ❌   │     ❌       │
│ 多厂商Provider切换  │    ✅    │    ❌    │   ✅   │   ✅   │     ❌       │
│ 自研Agent Loop      │    ❌    │    ✅    │   ✅   │   ❌   │     ❌       │
│ 终端渲染引擎       │    ❌    │    ✅    │   ❌   │   ✅   │     ❌       │
│ Context压缩管线    │    ❌    │    ✅    │   ❌   │   ❌   │     ❌       │
│ Hooks系统          │    ❌    │    ✅    │   ❌   │   ❌   │     ❌       │
│ IDE深度集成        │    ❌    │    ❌    │   ❌   │   ✅   │     ✅       │
│ 开源               │    ✅    │    ❌    │   ✅   │   ❌   │     ❌       │
├─────────────────────┼──────────┼──────────┼────────┼────────┼──────────────┤
│ 独特优势数量        │    6     │    4     │   2    │   2    │     1       │
└─────────────────────┴──────────┴──────────┴────────┴────────┴──────────────┘
```

**Lucode 的独特定位**：在"自主规划执行"和"安全模型"两个维度上超越了所有对比产品，但在"终端体验"和"Context 管理"维度上存在明显差距。这个项目更适合定位为**"具备自主软件工程能力的安全型 Agent 系统"**，而非"用户体验优秀的终端工具"。

---

## 附录：关键代码索引速查

| 文件路径 | 核心类/函数 | 行数(约) | 一句话职责 |
|---------|-----------|---------|----------|
| `lucode/entry.py` | `main()`, `build_parser()` | 386 | CLI 入口与子命令分发 |
| `main.py` | `chat_loop()`, `run_with_approval()`, `TokenLoggerHooks` | 965 | 交互 REPL + 审批系统 |
| `runtime/execution/dynamic.py` | `execute_dynamic_request()`, `_run_multi_agent()` | 1200 | 执行引擎 + 多 Agent 调度 |
| `runtime/execution/pipeline.py` | `GateDecision`, `PipelineRunState` | 200 | Gate 决策 + 管线状态 |
| `planning/planner.py` | `preview_plan()`, `build_orchestrator_planner()` | 400 | 规划引擎 |
| `planning/planner_schema.py` | `parse_planner_result()`, `build_fallback_planner_result()` | 200 | 规划数据结构 + JSON 解析 |
| `planning/plan_validator.py` | `validate_plan()` | 150 | 计划可行性校验 |
| `planning/plan_reviewer.py` | `review_plan()` | 120 | 计划安全审查 |
| `catalog_system/model_catalog.py` | `ModelRegistry`, `first_configured()` | 550 | 模型发现+探测+选择 |
| `catalog_system/model_probe.py` | `refresh_model_probe_cache()`, `probe_model_capabilities()` | 200 | 模型能力探测 |
| `catalog_system/refresher.py` | `refresh_catalogs()` | 50 | 模型目录刷新 |
| `runtime/config/model_config.py` | `connect_provider()`, `load_effective_lucode_config()` | 450 | Provider 配置管理 |
| `runtime/config/extensions.py` | `discover_skill_layers()`, `discover_mcp_layers()` | 150 | Skill/MCP 三层发现 |
| `runtime/config/settings.py` | `RuntimeSettings` | 50 | 运行时设置 |
| `runtime/config/workspace.py` | `discover_workspace_context()` | 80 | 工作区发现 |
| `runtime/config/execution_mode.py` | `runtime_route_for_input()` | 40 | 执行模式路由 |
| `runtime/agents/sdk.py` | `_ensure_sdk()`, `ensure_tracing_disabled()` | 100 | SDK 懒加载 + Fallback |
| `runtime/agents/factory.py` | `AgentFactory`, `create_task_agent()` | 250 | Agent 创建策略 |
| `mcp_servers/__init__.py` | `MCPServerManager` | 250 | MCP Server 生命周期管理 |
| `runtime/safety/permissions.py` | `evaluate_permission()` | 200 | 权限策略评估 |
| `runtime/safety/privacy.py` | `PrivacyPolicy`, `model_allowed()` | 100 | 隐私策略 |
| `runtime/safety/auditor.py` | `audit_execution()` | 250 | 执行后审计 |
| `runtime/safety/checkpoint.py` | `create_checkpoint()`, `rollback_checkpoint()` | 220 | Git 检查点 |
| `runtime/safety/session_checkpoint.py` | `SessionCheckpointManager` | 150 | 会话级回滚管理 |
| `runtime/safety/repair_loop.py` | `should_retry()`, `repair_strategy_for_audit()` | 120 | 修复循环 |
| `runtime/modes/solo.py` | `run_solo_request()` | 60 | Solo 执行模式 |
| `runtime/modes/serial.py` | `run_serial_request()` | 80 | Serial 执行模式 |
| `runtime/modes/full.py` | `run_full_request()` | 100 | Full 执行模式 |
| `runtime/ui/welcome.py` | `render_welcome_dashboard()` | 100 | 欢迎面板 |
| `runtime/ui/progress.py` | `render_task_status_board()` | 80 | 任务进度 |
| `runtime/ui/command_palette.py` | `render_command_palette()` | 100 | 命令面板 |
| `runtime/memory/flywheel.py` | `FlywheelStore` | 80 | 飞轮记忆 |
| `runtime/workspace/patch_ledger.py` | `PatchProposalLedger` | 100 | 补丁账本 |
| `runtime/workspace/run_workspace.py` | `RunWorkspace` | 60 | 多 Agent 共享目录 |
| `runtime/common/conversation.py` | `append_recent_turn()` | 40 | 对话压缩 |
| `skills/registry.py` | `SkillRegistry` | 100 | Skill 注册表 |

---

> **全文总结**：agents_demo 在"架构深度"（规划管线 + 多 Agent 协调 + 四维安全）上超越了市面上的 LLM 包装器，但在"终端体验"和"Context 管理"上落后于 Claude Code。项目的核心竞争力在于**将编译器式的多阶段管线引入 Agent 系统**，使从需求到交付的全流程可审计、可回滚、可修复——这是目前开源 Agent 工具中最完整的工程化实现。
