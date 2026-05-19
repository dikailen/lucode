# agents_demo 项目升级路线图

本文档用于记录 `agents_demo` 从当前动态多智能体原型，逐步升级到稳定编码助手、再到类 opencode / Claude Code 终端代理的长期计划。

总体建议路线：

```text
方案 A：安全工具层优先
  -> 方案 A+：本地优先模型与隐私边界
    -> 方案 B：KWCode 混合流水线
      -> 方案 C：类 opencode / Claude Code 终端代理
```

## 总览计划表

| 阶段 | 目标 | 核心能力 | 适合现在做吗 | 主要收益 | 主要风险 |
| --- | --- | --- | --- | --- | --- |
| A. 安全工具层优先 | 先让系统安全地读、写、删、测 | 文件读写、备份删除、命令执行、Git 只读、审批机制 | 已开始，继续完善 | 最实用，风险最可控 | 工具多了以后规则可能分散 |
| A+. 本地优先模型与隐私边界 | 统一本地模型、Ollama 和 OpenAI-compatible API | llama.cpp、Ollama、DeepSeek/SiliconFlow 等兼容 API、隐私模式 | 下一步优先补 | 支持代码不出网，也兼容云 API | 后端抽象没设计好会拖累后续 CLI |
| B. KWCode 混合流水线 | 提高代码任务稳定性和 token 效率 | Gate、BM25+AST Locator、Planner、Executor、Verifier、Flywheel | A+ 后重点 | 小模型也能更稳，减少乱读文件 | 工程量更大，需要测试体系 |
| C. 终端代理产品化 | 做成可长期使用的本地 CLI 编码助手 | `/plan`、`/build`、`/review`、`/test`、`/commit`、会话、记忆、配置 | 长期目标 | 体验最接近 opencode / Claude Code | 基础没打牢会变臃肿 |

## KWCode 参考能力吸收计划

参考仓库已放在项目内 `lucode/kwcode`，只作为架构参考，不参与当前主程序运行。

| KWCode 能力 | 是否吸收 | 在 agents_demo 中的落点 | 注意事项 |
| --- | --- | --- | --- |
| llama.cpp 原生加载 | 吸收 | A+ 阶段新增本地 GGUF backend | `llama-cpp-python` 作为可选依赖，不能影响现有安装 |
| Ollama HTTP API | 吸收 | A+ 阶段新增 Ollama backend | 默认本地地址 `http://localhost:11434`，失败时给中文提示 |
| OpenAI-compatible API | 吸收并统一 | A+ 阶段统一 DeepSeek、SiliconFlow、Qwen、OpenRouter 等 API | API key 只存 `.env` 或后续本地配置，不写入日志 |
| 本地优先，代码不出网 | 必须吸收 | A+ 阶段加入隐私模式 | `offline` 模式禁用联网和云端模型 |
| 模型能力自适应 | 吸收 | A+ / B 阶段按 small / medium / large 调整策略 | 小模型强制缩小任务范围，大模型放宽上下文 |
| BM25 + AST 调用图两阶段定位 | 必须吸收 | B 阶段升级 `code_locator` | 先 Python，后续再扩展 JS/TS/Java/Go/Rust |
| SQLite 调用图缓存 | 吸收 | `.agent_cache/code_graph.db` | 必须可增量更新，不能每次全量扫描大项目 |
| 三层记忆 | 分阶段吸收 | Flywheel 扩展为项目记忆、失败模式、会话摘要 | 先本地 Markdown/JSONL，后续再接知识图谱 |
| Checkpoint 快照回滚 | 吸收 | A/B 阶段补 task-level checkpoint | Git 项目和非 Git 项目策略不同，需要安全测试 |
| CLI onboarding 和配置命令 | 吸收 | C 阶段 `/config`、`/api`、`/model`、`/privacy` | 先做只读查看，再做写配置 |

## 阶段 A：安全工具层优先

### 目标

让 Agent 具备可靠的基础操作能力，并且所有有风险的动作都能被限制、审批、备份和追踪。

### 当前状态

| 能力 | 状态 | 说明 |
| --- | --- | --- |
| 只读文件 MCP | 已实现 | 带读取次数、文件数、字符数预算 |
| 代码定位 MCP | 已实现 | 已有本地索引缓存和变更感知 |
| 文件编辑 MCP | 已实现 | 写入、替换、patch、删除前备份 |
| 命令执行 MCP | 已实现 | 不经过 shell，危险命令拒绝，执行前审批 |
| Git MCP | 已实现 | status、diff、log 只读；commit 需要审批 |
| 联网搜索 MCP | 已实现 | 已加入来源分级和失败兜底 |
| 权限策略图书馆 | 已实现基础版 | 仍可继续细化 |
| 回归测试 | 已扩展 | 覆盖文件编辑、删除备份、命令拒绝、Git diff、web 失败、Gate/Verifier |

### 下一步任务

| 状态 | 优先级 | 任务 | 说明 | 验收标准 |
| --- | --- | --- | --- | --- |
| [x] | 高 | 完善工具失败兜底 | MCP 工具和主循环增加结构化失败兜底，不让主程序退出 | 任意工具失败后仍可继续下一轮对话 |
| [x] | 高 | 扩展回归测试 | 覆盖文件写入、删除备份、命令拒绝、Git diff、web 失败 | `tests/run_regression.py` 稳定通过 |
| [x] | 中 | 权限策略细化 | 将读、写、删、命令、Git、联网的策略统一文档化，并保护 `.agent_cache` | 主脑规划时能引用清晰权限说明 |
| [x] | 中 | 操作日志增强 | 已统一记录工具名、动作、参数摘要、审批要求、备份路径、成功/失败状态和错误信息 | 能追踪每次项目变更来源 |
| [x] | 低 | MCP 配置集中化 | 文件读取预算和 code_locator 索引预算已开放到 `.env.example` | 修改预算不需要改代码 |

## 阶段 A+：本地优先模型与隐私边界

### 目标

把当前“按模型图书馆直接选 API 模型”的方式，升级为统一模型后端层。后续无论用户使用 DeepSeek、SiliconFlow、Ollama、本地 llama.cpp GGUF，都走同一套调用接口和隐私策略。

### 模型后端设计

| 后端 | 说明 | 典型配置 | 隐私等级 | 是否优先 |
| --- | --- | --- | --- | --- |
| `llama_cpp` | 本地 GGUF 原生加载 | `MODEL_LOCAL_BACKEND=llama_cpp`、`MODEL_LOCAL_PATH=...gguf` | 最高，代码不出机 | 离线模式优先 |
| `ollama` | 本地 Ollama HTTP API | `MODEL_LOCAL_BASE_URL=http://localhost:11434`、`MODEL_LOCAL_MODEL=qwen3:8b` | 高，代码只到本机服务 | 本地优先模式优先 |
| `openai_compatible` | OpenAI 格式兼容 API | DeepSeek、SiliconFlow、Qwen、OpenRouter 等 | 中，代码会发到对应服务 | 仅 cloud_allowed 或用户明确允许 |
| `openai` | 官方 OpenAI API | 官方 OpenAI 模型 | 中，代码会发到 OpenAI | 用户明确配置后使用 |

### 隐私模式

| 模式 | 行为 | 适合场景 |
| --- | --- | --- |
| `offline` | 禁止联网搜索，禁止云端模型，只允许 `llama_cpp` / 本地 `ollama` | 公司代码、敏感项目、内网环境 |
| `local_first` | 优先本地模型；本地不可用时询问或按配置降级云端 | 默认推荐 |
| `cloud_allowed` | 允许使用云端 API 和联网搜索，但仍需日志脱敏 | 普通个人项目、资料检索任务 |

### A+ 阶段任务拆解

| 状态 | 优先级 | 任务 | 说明 | 验收标准 |
| --- | --- | --- | --- | --- |
| [x] | 高 | 抽象模型后端元数据 | 已统一记录 `backend_type`、`is_local`、`privacy_level`，并保留 OpenAI-compatible 现有调用路径 | DeepSeek 现有调用不破坏 |
| [x] | 高 | 接入 Ollama backend 元数据 | 支持 `MODEL_<ID>_BACKEND=ollama`，本地 Ollama 不需要 API key，继续走 OpenAI-compatible 客户端路径 | 本地 Ollama 模型可进入模型图书馆并被优先选择 |
| [x] | 高 | 隐私模式配置 | 增加 `AGENTS_PRIVACY_MODE`，控制联网和云端模型；Plan 校验和 MCP 启动双层拦截 | `offline` 模式下 web_search 和云端 API 被拒绝 |
| [ ] | 中 | 接入 llama.cpp 原生推理 | 可选依赖，支持本地 GGUF；当前仅完成 backend 元数据预留 | 未安装依赖时给清晰提示，不影响其他后端 |
| [x] | 中 | 模型能力自适应 | 已按 small/medium/large 生成执行策略，并让 Gate 根据模型能力收窄代码任务读取范围、强制小模型先计划 | 小模型任务自动收窄 |
| [x] | 中 | CLI 配置只读查看 | 已支持 `/config`、`/api show`、`/privacy`、`/model` 只读展示，不泄露 API key；未在 `.env` 注册的模型不会再显示，三脑默认优先级会按已注册模型动态生成 | 用户能看到本地/云端模型、隐私模式和真实生效的模型优先级 |
| [ ] | 中 | CLI 一键切换预留 | 后续支持 `/privacy offline`、`/model local`、`/model cloud` 改写本地配置；当前只提示不改 `.env` | 本地模型和云端模型可一键切换，且有确认和回滚 |
| [ ] | 中 | Ollama 原生 `/api/chat` 适配 | 当前 Ollama 更适合 OpenAI-compatible 网关；后续再实现原生接口 | 不破坏 Agents SDK 工具调用和 token 统计 |

## 阶段 B：KWCode 混合流水线

### 目标

把“一个 Agent 自己读、想、改、测”的流程拆成更稳定的编码流水线。让每一步职责更清楚，降低误读、误改和 token 浪费。

### 建议流水线

```text
User Request
  -> Gate：判断任务类型、风险、是否需要代码流水线
  -> Locator：BM25 召回 + AST 调用图展开，定位相关文件、符号、入口、测试
  -> Planner：生成小步修改计划
  -> Executor：执行文件修改
  -> Verifier：运行测试/静态检查/自查 diff
  -> Reviewer：对照用户意图审查改动是否过度或遗漏
  -> Flywheel：沉淀经验、失败案例、项目规则
  -> Final：输出结果和下一步建议
```

### 子模块计划表

| 模块 | 职责 | 可用模型 | 工具 | 输出 |
| --- | --- | --- | --- | --- |
| Gate | 判断是否需要代码流水线、是否要联网、是否有风险操作 | Flash / Pro | 无或少量图书馆 | 路由决策 |
| Locator | 先 BM25 召回，再沿 AST 调用图展开隐藏依赖 | 本地算法优先，LLM 仅兜底 | `code_locator`、只读文件 MCP、后续 `code_graph` | 候选文件、函数、调用链 |
| Planner | 把任务拆成可执行小步骤 | Pro / MiMo | 少量只读上下文 | 修改计划 |
| Executor | 实际改文件 | MiMo | `workspace_edit` | patch / 文件变更 |
| Verifier | 运行测试、检查 diff、判断是否完成 | MiMo / Flash | `command_runner`、`git_tools` | 验证报告 |
| Reviewer | 审查修改是否符合意图，是否有过度修改 | Pro / MiMo | `git_tools`、只读文件 MCP | 审查结论 |
| Flywheel | 记录项目经验、常见错误、用户偏好 | 后续可接知识图谱 | 本地记忆接口 | 可复用经验 |

### BM25 + AST 调用图定位计划

| 层级 | 做什么 | 存储 | 说明 |
| --- | --- | --- | --- |
| 文件索引 | 扫描源码文件、排除 `.git`、`.venv`、`node_modules`、缓存目录 | `.agent_cache/code_locator_index.json` | 当前已有基础版本，后续增强 |
| 符号索引 | 提取函数、类、方法、起止行、docstring、import | `.agent_cache/code_graph.db` | 先 Python AST，后续 tree-sitter 多语言 |
| BM25 召回 | 用用户问题召回 Top-K 相关符号/文件 | 内存缓存 | 不调模型，速度快 |
| 调用图展开 | 从 BM25 命中节点向上/向下展开 1-2 跳 | SQLite edges 表 | 找隐藏依赖，不靠 LLM 猜 |
| 增量更新 | 文件修改后只更新相关节点和边 | SQLite metadata | 避免每次全量扫描 |
| LLM 兜底 | 图索引不可用或无结果时再让模型判断 | 无 | 兜底路径，不是主路径 |

### B 阶段任务拆解

| 状态 | 优先级 | 任务 | 说明 | 验收标准 |
| --- | --- | --- | --- | --- |
| [x] | 高 | 增加 Gate 规则 | 已加入确定性 Gate 兜底，代码任务会补齐 Locator/只读上下文/编辑工具 | 代码任务不再被误判成闲聊或解释 |
| [x] | 高 | 增强 Locator | 已有本地索引缓存、符号轮廓和变更感知；AST 深化后续继续 | 能快速找到目标文件和测试文件 |
| [x] | 高 | 增加 Verifier | 已加入代码任务后的只读 git status / diff --stat 校验摘要 | 修改任务有明确验证结果 |
| [x] | 中 | 引入任务状态对象 | 已加入 `PipelineRunState`，记录 Gate、任务输出预览、Verifier 和错误 | 出错后可恢复或继续 |
| [x] | 中 | Flywheel 雏形 | 已加入本地 JSONL 经验库，可记录 Pipeline 摘要、手动经验、标签搜索，并做基础敏感信息脱敏 | 下一次相似任务能引用经验 |
| [x] | 高 | BM25 + AST 调用图 Locator | 已完成 Python MVP：BM25 召回、AST 符号起止行、SQLite 调用图缓存、调用链展开；多语言 tree-sitter 后续继续 | 能返回相关文件、函数、调用链和置信度 |
| [ ] | 中 | Reviewer 审查环节 | 对照用户意图、diff、测试结果做最终审查 | 能发现过度修改和遗漏 |
| [ ] | 中 | Checkpoint 快照 | 修改前创建任务级快照，失败可恢复 | Git / 非 Git 项目都有安全回退 |
| [ ] | 中 | Flywheel 检索注入 | 规划前检索相关失败模式、项目规则、用户偏好 | 主脑能引用历史经验 |
| [ ] | 低 | 语义索引 | 后续考虑 embeddings 或轻量向量库 | 大项目定位准确率提升 |

## 阶段 C：终端代理产品化

### 目标

把当前 Python 项目升级成一个可长期使用的本地终端编码助手，接近 opencode / Claude Code 的使用体验。

### 核心概念

终端代理不是简单聊天窗口，而是一个“项目工作台”。它应该知道当前项目、当前任务、当前修改、当前测试结果，并能用命令式交互管理 Agent 工作流。

### 命令设计草案

| 命令 | 用途 | 示例 |
| --- | --- | --- |
| `/plan` | 只规划，不执行 | `/plan 修复登录接口报错` |
| `/build` | 执行代码实现 | `/build 给用户模块加分页查询` |
| `/review` | 审查当前改动 | `/review` |
| `/test` | 运行测试或建议测试 | `/test` |
| `/status` | 查看当前任务、MCP、模型、token、git 状态 | `/status` |
| `/diff` | 查看本轮修改摘要 | `/diff` |
| `/commit` | 生成本地 commit，需要审批 | `/commit 修复 MCP 启动错误` |
| `/memory` | 查看或管理项目记忆 | `/memory list` |
| `/config` | 查看模型、MCP、预算配置 | `/config` |
| `/api` | 查看或配置 OpenAI-compatible API | `/api show`、`/api default deepseek` |
| `/model` | 切换主脑/副脑/专家默认模型 | `/model orchestrator deepseek_V4_pro_model` |
| `/privacy` | 查看或切换隐私模式 | `/privacy offline` |
| `/rollback` | 回滚本轮未确认修改，需谨慎设计 | `/rollback` |

### 终端代理架构草案

```text
CLI Shell
  -> Session Manager：会话、历史、当前任务
  -> Command Router：解析 /plan /build /review 等命令
  -> Orchestrator：主脑规划
  -> Pipeline Runtime：A/B 阶段工具与流水线
  -> Tool Layer：MCP、权限、审批、备份、日志
  -> Memory Layer：项目经验、用户偏好、知识图谱接口
  -> Renderer：清爽输出、进度、折叠、token、最终答案
```

### 终端体验改进建议

| 方向 | 建议 | 原因 |
| --- | --- | --- |
| 输出层 | 区分“进度日志 / 工具日志 / 最终回答” | 用户不容易被日志淹没 |
| 折叠层 | 长工具输出默认折叠，只显示摘要 | 终端更清爽 |
| 任务层 | 显示当前阶段：规划、定位、修改、验证、汇总 | 用户知道系统在干什么 |
| 审批层 | 审批时显示风险等级、目标文件、备份路径 | 用户更放心 |
| 配置层 | 已提供 `/config` 只读查看模型和 MCP 状态；后续再做写配置 | 排查 API key 和模型选择更容易 |
| API 层 | 已提供 `/api show` 只读查看；后续支持本地/云端一键切换 | 用户不用反复手改 `.env` |
| 隐私层 | 已提供 `/privacy` 只读查看；后续支持 `/privacy offline/local_first/cloud_allowed` 写配置 | 敏感项目更安心 |
| 恢复层 | 支持任务失败后继续、重试、降级本地执行 | 网络不稳定时体验更好 |
| 记忆层 | 记录项目约定和用户偏好，但可查看、可删除 | 避免“黑箱记忆” |
| 成本层 | 每轮显示 token、模型、工具次数 | 用户能感知成本 |

## 推荐里程碑

| 里程碑 | 目标 | 完成标志 |
| --- | --- | --- |
| M1 | A 阶段稳定 | 基础工具、审批、测试、错误兜底和统一操作日志已跑通 |
| M2 | A+ 阶段模型与隐私 | 统一模型后端、Ollama、本地优先隐私模式可用 |
| M3 | B 阶段雏形 | 已完成初始版：Gate/Locator/Verifier/任务状态对象已跑通，代码任务有兜底骨架 |
| M4 | B 阶段增强 | BM25+AST 调用图、Checkpoint、Flywheel 检索注入稳定 |
| M5 | C 阶段 CLI 原型 | `/plan`、`/build`、`/review`、`/test`、`/status`、`/config` 可用 |
| M6 | C 阶段产品化 | 输出折叠、会话恢复、配置管理、记忆管理稳定 |

## 当前最建议的下一步

1. 先做 A+ 阶段的统一模型后端和隐私模式：这是接入 Ollama、llama.cpp、本地优先策略的地基。
2. 再升级 B 阶段 Locator：把当前词法搜索增强为 BM25 + AST 调用图两阶段定位。
3. 然后补 Checkpoint 和 Flywheel 检索注入，让代码任务能恢复、能复用经验。
4. 最后进入 C 阶段 CLI 原型：`/config`、`/api show`、`/privacy`、`/model` 只读查看已完成；下一步可做 `/status`、`/memory list`，再做带确认的一键切换。

## 已实现的用户配置入口

当前先采用 `.env` 配置方式，避免过早引入复杂 CLI 设置界面。

| 配置项 | 用途 | 示例 |
| --- | --- | --- |
| `AGENTS_QUERY_REFINER_ENABLED` | 是否启用前置优化副脑 | `false` |
| `AGENTS_QUERY_REFINER_MODEL_PRIORITY` | 前置优化副脑模型优先级；不写时自动根据已注册模型生成 | `deepseek_v4_flash_model,mimo_v25_pro_model` |
| `AGENTS_ORCHESTRATOR_MODEL_PRIORITY` | 主脑模型优先级；不写时自动根据已注册模型生成 | `deepseek_v4_pro_model,mimo_v25_pro_model` |
| `AGENTS_FINAL_SYNTHESIZER_MODEL_PRIORITY` | 汇总副脑模型优先级；不写时自动根据已注册模型生成 | `deepseek_v4_pro_model,mimo_v25_pro_model` |

后续进入 C 阶段后，可以再把这些配置做成 `/config` 命令或交互式设置界面。
