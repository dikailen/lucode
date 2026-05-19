# 项目评估报告复核与优化建议

> 生成日期：2026-05-07  
> 复核对象：`C:\Users\林凯东\OneDrive\Desktop\项目评估报告.md`  
> 项目路径：`D:\pycharm\code\agents_demo`

## 一、总体结论

我基本认同这份评估报告对项目主线架构的判断：当前项目已经从“多 Agent Demo”逐步演进成了一个具备动态模型库、Skill/MCP 图书馆、计划校验、并行冲突审查、代码定位、工具审批、失败记忆和回滚机制的本地 Agent 编程助手雏形。

报告中指出的高价值问题大多属实，尤其是：

- `load_model_catalog()` 多处重复构建，应该加缓存或运行期快照。
- `PrivacyPolicy.mcp_allowed()` 当前没有真正执行 MCP 隐私过滤。
- Verifier 和 Auditor 还偏“流程检查”，没有升级到足够强的语义验收。
- Repair Loop 目前只是重组提示词，没有策略升级。
- Git checkpoint 只在干净工作区可回滚，真实使用场景里保护能力还不够。
- `recent_turns`、整体执行超时、搜索源质量、CLI 配置体验仍有明显优化空间。

但报告里也有几处需要修正，不能直接照单全收：

- 不能简单删除空的 `skills/__init__.py`。它虽然为空，但可能用于保持 Python package 行为。
- `cloud_model` 不一定是死占位。当前测试和动态 `MODEL_CLOUD_*` 配置里确实会用到这个模型 id。
- “DuckDuckGo HTML 抓取违反 ToS”不应直接下法律结论，更准确的说法是：稳定性、合规性、反爬和可维护性风险较高，应抽象成可替换搜索 Provider。
- “MCP 子进程一定泄露 API Key”还需要验证 `MCPServerStdio` 的 `env` 是替换还是合并父环境。当前代码显式传入的 env 并未包含 API Key，但仍建议增加环境白名单和敏感变量审计。
- `future_memory_interface` 是此前知识图谱预留接口，不算无意义死代码，但应该移动到更清楚的位置，避免误导主脑认为该能力已经实现。

因此，我建议把原报告看作“可执行优化清单的草稿”，但在落地时要按风险和收益重新排序。

## 二、源码核对摘要

本次重点核对了以下模块：

| 模块 | 核对结论 |
| --- | --- |
| `runtime/privacy.py` | `mcp_allowed()` 当前确实始终返回 `True`，隐私策略对 MCP 的约束不完整。 |
| `runtime/checkpoint.py` | rollback 使用 `git reset --hard` 和 `git clean -fd`，只有干净工作区才自动回滚；脏工作区进入保护模式。 |
| `runtime/repair_loop.py` | 修复循环当前主要是把失败原因拼进下一轮提示词，没有更换模型、拆分任务、降级路线等自适应策略。 |
| `runtime/auditor.py` | 审计主要检查任务状态、输出、verification 是否存在，未逐条验证 `acceptance_criteria` 是否满足。 |
| `runtime/pipeline.py` | Verifier 主要执行 `git status --short` 和 `git diff --stat`，尚未正式接入测试命令、lint、语义检查。 |
| `catalog_system/model_catalog.py` | `load_model_catalog()` 会被 CLI、Validator、Planner、Runtime settings、Pipeline 等多处调用，尚无统一缓存。 |
| `catalog_system/refresher.py` | `future_memory_interface` 是明确预留字段，但目前未实现知识图谱能力。 |
| `mcp_servers/web_search_mcp.py` | 已做来源分级排序，但底层仍是 DuckDuckGo HTML 抓取。 |
| `mcp_servers/__init__.py` | MCP 已懒加载，且显式传 env；API Key 泄露风险需要进一步验证，不宜直接定性。 |
| `main.py` | 存在 daemon stdin 线程、`BaseException` late-result 消费、`recent_turns` 截断较粗、整体执行超时缺失等可优化点。 |

## 三、认同并建议优先优化的问题

### 1. 模型图书馆缓存

问题：`load_model_catalog()` 负责动态发现 `.env` 模型、合并探测结果、计算执行策略。现在多个模块反复调用，会造成启动和每轮规划阶段重复构建。

建议：

- 增加 `ModelCatalogSnapshot` 或轻量缓存层。
- 缓存 key 至少包含 `.env` 文件修改时间、关键环境变量快照、probe cache 修改时间。
- 提供 `force_reload=True`，供 `/reload` 或启动刷新使用。
- `ModelRegistry` 应持有一次运行期模型快照，避免 Planner、Validator、CLI 各自重扫。

测试建议：

- 同一轮运行中多次读取模型库时，只构建一次。
- 修改 `.env` 后调用强制刷新，模型库能更新。
- probe cache 变化后，能力字段能重新合并。

### 2. 隐私模式真正约束 MCP

问题：`PrivacyPolicy.model_allowed()` 已经能限制模型，但 `mcp_allowed()` 当前始终返回 `True`。这会让 offline/local_first/cloud_allowed 的语义不够统一。

建议：

- `offline`：默认不允许联网 MCP，例如 `web_search`，除非用户显式批准本轮外发查询。
- `local_first`：允许联网 MCP，但主脑需要先判断本地信息是否足够；联网前应显示风险提示。
- `cloud_allowed`：允许联网和云模型，但仍保留敏感路径、API Key、`.env` 保护。
- `plan_validator` 应调用 `mcp_allowed()` 并给出中文原因。
- MCP catalog 里给每个 MCP 增加 `network_access`、`external_data_risk`、`requires_privacy_warning` 等字段。

测试建议：

- offline 下普通 `web_search` 规划会被标记需要用户确认或阻断。
- offline 下本地只读文件、code_locator、workspace_edit 不应被误封禁。
- local_first 下使用 web_search 会出现中文风险提示。

### 3. 搜索 Provider 抽象

问题：当前 `web_search_mcp.py` 直接抓取 DuckDuckGo HTML。即使已有来源分级排序，也存在稳定性和合规风险。

建议：

- 引入 `SearchProvider` 接口：
  - `duckduckgo_html`：默认免费 fallback。
  - `bing` / `serpapi` / `brave`：后续可选 API Provider。
  - `official_domain_first`：针对官方文档优先的特殊策略。
- `.env` 增加：
  - `WEB_SEARCH_PROVIDER=duckduckgo_html`
  - `WEB_SEARCH_API_KEY=`
  - `WEB_SEARCH_OFFICIAL_FIRST=true`
- `web_search` 输出必须包含 `source_tier`、`source_provider`、`retrieved_at`。

测试建议：

- Provider 未配置时走 DuckDuckGo fallback。
- 配置 Provider 但无 API Key 时给中文提示，不崩溃。
- 官方文档、官方 GitHub、社区文章排序稳定。

### 4. Verifier 升级

问题：当前 Verifier 只能说明“文件改了什么”，不能说明“功能是否正确”。

建议：

- 读取 `.env` 或项目配置中的验证命令：
  - `AGENTS_VERIFY_COMMANDS=python tests/run_regression.py`
  - 后续可支持多命令。
- 根据任务风险决定验证强度：
  - 只读分析：无需验证。
  - 小修改：运行相关测试或快速回归。
  - 高风险修改：运行完整回归。
- Verifier 报告包含命令、退出码、耗时、stdout/stderr 摘要。

测试建议：

- 修改任务包含 `command_runner` 或 `test_intent=True` 时会触发验证命令。
- 命令失败时 Auditor 能感知并触发修复循环。
- 只读任务不会误触发测试。

### 5. Auditor 语义验收增强

问题：`auditor.py` 尚未真正逐条检查 `acceptance_criteria` 和 `expected_outputs`。

建议：

- 先做确定性审计：
  - 所有任务是否完成。
  - 修改任务是否有 verification。
  - write_intent 是否与实际 git diff 匹配。
  - expected_outputs 是否至少在输出或文件中出现。
- 再做模型审计：
  - 对复杂任务调用审核员模型，按验收标准输出 JSON。
  - 审核员只读，不允许写文件。
- 审核失败时把失败原因结构化传给 Repair Loop。

测试建议：

- 缺少 verification 的写入任务不通过。
- expected_outputs 未出现时不通过。
- 只读解释类任务不会被过度审计。

### 6. Repair Loop 策略升级

问题：当前重试只是“把失败原因重新告诉主脑”，容易重复失败。

建议引入分层修复策略：

| 失败类型 | 修复策略 |
| --- | --- |
| JSON 规划异常 | 切换更强 planner，或降级为保守单 Agent/direct answer。 |
| 工具不支持 | 改用不需要 tools 的模型直接回答，或切换支持 tools 的模型。 |
| 写入冲突 | 强制串行、收窄 write_intent、重建任务依赖。 |
| 测试失败 | 将失败日志交给代码 Agent 修复，并限制修改范围。 |
| 审核不通过 | 只针对未满足验收项重新规划。 |

测试建议：

- 第 1 轮因工具不支持失败，第 2 轮能选择支持 tools 的模型。
- 第 1 轮并行冲突失败，第 2 轮能自动串行。
- 连续失败达到上限后会记录失败案例并尝试回滚。

### 7. Checkpoint 支持脏工作区

问题：真实项目里用户经常已有未提交改动，当前 `git_dirty_protected` 会让自动回滚不可用。

建议：

- 保留当前保护原则，不直接覆盖用户改动。
- 新增更安全的 checkpoint 模式：
  - `git_stash_user_changes`：执行前 stash 用户已有改动，结束后恢复。
  - `worktree_checkpoint`：在临时 worktree 中执行 Agent 修改。
  - `scoped_patch_rollback`：只回滚本轮 Agent 触碰的文件。
- 第一阶段优先实现 `scoped_patch_rollback`，避免 `git reset --hard` + `git clean -fd` 影响用户手工改动。

测试建议：

- 用户已有未提交改动时，不会被 Agent 回滚误删。
- Agent 新增文件失败后，只清理 Agent 新增文件。
- Agent 修改与用户修改同文件时，要求人工确认或停止自动回滚。

## 四、部分认同但需要修正的问题

### 1. MCP 子进程环境变量泄露 API Key

原报告说法偏绝对。当前 `mcp_servers/__init__.py` 创建 MCP 时显式传入了 env 字典，里面主要是 MCP 根目录、预算和 `PYTHONIOENCODING`，没有直接传 API Key。

但风险仍然值得处理，因为需要确认 SDK 的 `MCPServerStdio` 是否会将传入 env 与父进程环境合并。如果是合并，敏感变量仍可能进入子进程。

建议：

- 增加 MCP env 单元测试或运行期自检，确认子进程实际可见变量。
- 提供统一 `_safe_mcp_env()`，默认只允许白名单变量。
- 禁止 `*_API_KEY`、`*_TOKEN`、`SECRET`、`PASSWORD` 进入 MCP 子进程，除非某个 MCP 明确需要且用户配置允许。

### 2. `BaseException` 吞掉 Ctrl+C

`_cancel_task_without_blocking()` 里的 late-result callback 捕获 `BaseException`，确实偏宽。但它只用于任务已取消后异步消费迟到结果，主要目的是避免 “Task exception was never retrieved”。

建议不是简单替换成 `Exception`，而是：

- 对 `asyncio.CancelledError` 单独处理。
- 对 `KeyboardInterrupt`、`SystemExit` 不吞掉。
- late callback 只做日志级别的安全消费。

### 3. `future_memory_interface` 预留字段

这不是完全无用代码，因为你之前已经明确说过后续要预留知识图谱接口。但它现在放在 catalog 输出里，容易让主脑误以为已经有可用 memory 能力。

建议：

- 保留计划，但从主脑可见 catalog 中移除或标记为 `not_available`。
- 在 roadmap 文档中保留“未来知识图谱接口”的结构。
- 等真正实现 memory MCP 后再回到 catalog。

### 4. `cloud_model` 占位

原报告建议删除 `cloud_model`，我不建议马上删。当前测试里存在 `MODEL_CLOUD_MODEL`，说明这也是动态模型配置的一种通用别名。

更合理的优化是：

- 如果 `.env` 没有注册 `cloud_model`，不展示、不选择。
- 如果用户通过 `MODEL_CLOUD_*` 注册了它，则正常参与优先级。
- `_preferred_models_for_skill()` 中可以从静态候选改为“动态 catalog 按能力排序”。

### 5. `catalogs/*.json` 是否 gitignore

这取决于它们是“运行时缓存”还是“可审查配置快照”。

如果它们只是启动时生成的缓存，应加入 `.gitignore`。  
如果它们用于让用户审查 Skill/MCP/Model 图书馆变化，则可以保留，但建议加说明和更新时间字段。

我建议短期保留，等 catalog cache 改造完成后再决定。

## 五、不建议直接采纳的问题

### 1. 删除 `skills/__init__.py`

不建议。空文件不代表无用。它可能维持 `skills` 作为 Python package 的导入行为。除非确认所有导入都不依赖 package 语义，否则不要删。

### 2. 直接删除第三方 Skill README

不建议一刀切。Skill 目录里的 README 可能是给 Skill 自身或人工维护看的。更好的做法是：

- 第三方原始文档放入 `skills/<skill>/docs/`。
- 主脑默认只读取 `SKILL.md` 和 catalog 摘要。
- 只有 skill creator 或用户明确要求时才读 README。

### 3. 把 DuckDuckGo 风险定性为“必然违规”

不建议这样写。工程上应该表达为：HTML 抓取稳定性低、可能被限流、合规边界不清晰、不可作为长期唯一搜索后端。

### 4. 为了支持回滚而放松 dirty workspace 保护

不建议。保护用户已有改动比自动回滚更重要。应该增加更细粒度 checkpoint，而不是让 `git reset --hard` 在脏工作区运行。

## 六、建议优化路线

### P0：安全和稳定性优先

- 修复 `PrivacyPolicy.mcp_allowed()`，让隐私模式真正约束 MCP。
- 增加 MCP env 白名单或泄露自检。
- 增加整体执行超时，例如 `AGENTS_TURN_TIMEOUT_SECONDS`。
- 压缩 `recent_turns`：限制每轮输入/输出保存长度，并保存摘要而不是完整结果。
- 修正 `_cancel_task_without_blocking()` 的 `BaseException` 捕获边界。

验收标准：

- offline 下不会静默使用联网 MCP。
- `/stop`、Ctrl+C、模型卡住时不会导致 CLI 永久阻塞。
- 长输出多轮对话不会明显膨胀 prompt。

### P1：执行质量升级

- 为 `load_model_catalog()` 增加缓存和强制刷新入口。
- Verifier 接入可配置测试命令。
- Auditor 逐条检查 `acceptance_criteria`、`expected_outputs`、verification。
- Repair Loop 根据失败类型切换策略。

验收标准：

- 编码任务完成后能自动给出测试或验证结果。
- 修复循环不会重复同一条失败路线。
- 规划、校验、执行、审计的失败原因都能中文显示。

### P2：搜索和知识来源升级

- 把 `web_search_mcp.py` 拆成 Provider 架构。
- 默认保留 DuckDuckGo fallback，但允许配置 Bing/SerpAPI/Brave 等正式搜索 API。
- 强化“官方文档 > 官方 GitHub > 包仓库 > 社区文章”的来源优先级。
- 输出引用来源、抓取时间和来源等级。

验收标准：

- 相同查询下官方文档稳定排在社区文章前。
- 搜索失败时不会让整轮 Agent 崩溃。
- 用户能看懂联网查询是否发生、用了什么来源。

### P3：回滚和并发写入安全

- 支持 scoped patch rollback。
- 给并行任务增加文件锁/写入意图审查。
- 当多个 Agent 可能修改同一文件时，默认串行化。
- 审核员负责确认最终 diff 是否满足用户目标。

验收标准：

- 并行任务不会同时写同一文件。
- 失败回滚不会误删用户原有未提交修改。
- 多轮修复失败后能保存失败案例，并给出清楚报告。

### P4：用户体验和项目整理

- 增加真正的 CLI 设置命令，例如 `/privacy set local_first`、`/model set orchestrator xxx`。
- 把历史报告归档到 `docs/reports/`。
- 确认 `mcp_servers/quiet_stdio.py` 未使用后删除。
- 明确 `catalogs/` 是缓存还是可审查快照。
- 给 `.env.example` 增加搜索 Provider、验证命令、超时、隐私模式示例。

验收标准：

- 用户能通过 CLI 完成常见配置切换，不必频繁手动改 `.env`。
- 项目根目录更清爽。
- 文档、缓存、报告、源码边界清楚。

## 七、推荐下一步实施顺序

我建议下一步不要先做大重构，而是按下面顺序推进：

1. 先做 P0：隐私 MCP 约束、MCP env 白名单、整体超时、上下文压缩。
2. 再做 P1 的模型库缓存，因为它会影响 CLI、Planner、Validator 和 Runtime settings。
3. 然后升级 Verifier/Auditor/Repair Loop，让项目真正具备“自动改、自动验、自动修”的闭环。
4. 搜索 Provider 和 CLI 设置命令放在执行质量稳定后做。
5. 最后再做项目目录整理和 dead code 清理，避免误删仍被隐式依赖的文件。

## 八、需要新增的测试清单

建议新增或补充以下测试：

- `test_privacy_policy_blocks_network_mcp_in_offline`
- `test_privacy_policy_allows_local_mcp_in_offline`
- `test_mcp_env_does_not_include_api_keys`
- `test_model_catalog_cache_reuses_snapshot`
- `test_model_catalog_force_reload_after_env_change`
- `test_verifier_runs_configured_command_for_edit_task`
- `test_auditor_fails_when_acceptance_criteria_missing`
- `test_repair_loop_switches_strategy_after_tool_failure`
- `test_checkpoint_does_not_destroy_dirty_user_changes`
- `test_recent_turns_are_truncated_before_prompt`
- `test_search_provider_fallback_returns_structured_error`
- `test_official_sources_rank_before_community_sources`

这些测试适合作为下一轮 TDD 的起点。

## 九、最终判断

这份外部评估报告整体是有价值的，尤其对安全、验证、修复循环、模型库性能的批评比较准确。  
我不建议把它当成“直接删除/直接重构清单”，而应该当成“下一阶段优化输入”。真正落地时，应优先保护用户文件、安全边界和运行稳定性，再逐步增强智能化能力。

我的结论是：

- 原报告的主评分区间 `7.0 - 7.5` 基本合理。
- 当前项目最大短板不是架构想法，而是验证深度、失败恢复和隐私策略执行还不够硬。
- 如果完成 P0 和 P1，项目质量可以明显提升到 `8.0` 左右。
- 如果再完成 P2/P3，才真正接近可长期使用的本地多 Agent 编码助手。
