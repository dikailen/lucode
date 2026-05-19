# agents_demo (Lucode) vs Claude Code / OpenCode 架构差距分析

> 分析日期：2026-05-11
> 基于 Claude Code CLI vs OpenCode 架构报告 与 agents_demo 项目源码逐层对比

---

## 一、agents_demo 现有架构总览

### 1.1 分层结构（7 层）

```
┌────────────────────────────────────────────────────────────┐
│  第 7 层 — Skills 层 (skills/)                              │
│  SKILL.md 加载 → Agent instructions 注入                    │
├────────────────────────────────────────────────────────────┤
│  第 6 层 — Planning 层 (planning/)                          │
│  query_refiner → orchestrator_planner → plan_validator      │
│  → plan_reviewer → PlannerResult                            │
├────────────────────────────────────────────────────────────┤
│  第 5 层 — Execution 层 (runtime/execution/)                │
│  dynamic.py: Pipeline 编排、Gate 决策、单/多 Agent 调度      │
│  pipeline.py: GateDecision、PipelineRunState、Verifier       │
├────────────────────────────────────────────────────────────┤
│  第 4 层 — MCP 工具层 (mcp_servers/)                        │
│  7 个 MCP Server：filesystem/code_locator/edit/backup/      │
│  command/git/web_search — 全部 stdio 子进程                  │
├────────────────────────────────────────────────────────────┤
│  第 3 层 — Agent Factory 层 (runtime/agents/)               │
│  OpenAI Agents SDK 封装：Agent/Runner/RunHooks/AsyncOpenAI   │
│  → 懒加载 SDK、tracing 关闭、fallback 兼容                   │
├────────────────────────────────────────────────────────────┤
│  第 2 层 — Config/Runtime 层 (runtime/config/ + settings)    │
│  .env + lucode config.toml + auth.json 三级配置              │
│  ModelRegistry → OpenAIChatCompletionsModel 创建             │
├────────────────────────────────────────────────────────────┤
│  第 1 层 — Entry 层 (lucode/entry.py + main.py)              │
│  argparse CLI → chat/run/init/doctor/connect/models/auth     │
│  交互式 chat_loop + RuntimeCommandSession                    │
└────────────────────────────────────────────────────────────┘
```

### 1.2 代码规模

| 模块 | 文件数 | 估算行数 | 职责 |
|------|--------|---------|------|
| main.py | 1 | ~965 | 交互 REPL + 审批流程 + Token 日志 |
| lucode/ | 3 | ~386 | CLI 入口 + argparse 子命令 |
| runtime/execution/ | 2 | ~1200 | 动态执行管线 + Gate/Verifier |
| runtime/config/ | 5 | ~1100 | 配置管理 + CLI 渲染 + 模型选择 |
| runtime/agents/ | 3 | ~300 | SDK 封装 + Agent 工厂 + 能力策略 |
| runtime/modes/ | 3 | ~60 | solo/serial/full 模式路由 |
| runtime/safety/ | 5 | ~500 | 权限 + 隐私 + 审计 + 回滚 |
| catalog_system/ | 4 | ~850 | 模型目录 + 刷新 + 探测 |
| mcp_servers/ | 10 | ~2500 | 7 个 MCP Server 实现 |
| planning/ | 4 | ~600 | Planner + Schema + Validator + Reviewer |
| skills/ | 5+ | ~300 | Skill 加载 + 注册表 |

---

## 二、与 Claude Code 体验差距：逐项分析

### 2.1 启动性能 — 最大差距

**Claude Code：先判断再加载（~10ms 轻量命令，~500ms 完整启动）**

```
cli.tsx → 手工解析 argv → 匹配快速路径 → 零导入返回
                              ↓ 未匹配
                         startCapturingEarlyInput()
                              ↓
                         await import('../main.js')  ← 仅此时加载完整运行时
```

**agents_demo 现状：**
```python
# lucode/entry.py — 所有子命令都先构造完整 argparse.ArgumentParser
# main.py — 顶层 import 链在模块求值时已加载几乎所有模块
import sys, asyncio, json, os, threading, ...
from catalog_system.refresher import refresh_catalogs
from catalog_system.model_catalog import ModelRegistry
from mcp_servers import MCPServerManager
from planning.planner_schema import sanitize_text
from planning.planner import format_plan_preview, preview_plan
...  # 30+ 顶层 imports
```

**差距量化：**
- 无快速路径分发：`lucode --version` 也要走完整 import 链
- 无早期输入捕获：启动期间的按键会丢失
- 无并行预取：模型目录刷新可能阻塞启动
- 无"先渲染再预取"：`refresh_catalogs()` 在 `main()` 入口就同步调用

### 2.2 核心 Agent Loop — 委托 vs 自主

**Claude Code：自研双层循环**

```
外层 QueryEngine (~46K行)   → 会话状态、Compaction、权限模式
内层 query.ts (Agent Loop)   → 流式调用、工具执行、7种继续站点
  - 消息即状态（追加式 JSONL）
  - 错误即反馈（失败注入回消息流）
  - 流式工具预执行（隐藏 I/O 延迟）
  - LLM 自主决定停止（无显式终止逻辑）
```

**agents_demo 现状：完全委托给 OpenAI Agents SDK**

```python
# main.py:777 — SDK 内部的 loop 对项目完全黑盒
result = Runner.run_streamed(agent, run_input, hooks=hooks, max_turns=max_turns)
```

**差距量化：**
- 无自研 Agent Loop：无法控制单次 turn 内的工具调用策略
- 无流式工具预执行：工具 I/O 等待暴露在用户面前
- 无 Compaction 管线：上下文超长时无自动压缩
- 无 7 种继续站点：只有 `max_turns` 硬限制
- 状态委托给 SDK：无法自主实现消息持久化/重放

### 2.3 终端 UI — 纯文本 vs React+Ink

**Claude Code：**
- React + Ink (251KB ink.tsx)
- Yoga Flexbox（CSS Flexbox 的 C 实现）
- 屏幕缓冲区差分（仅变化单元格生成 ANSI）
- 虚拟滚动（VirtualMessageList）
- 同步动画系统（ClockContext 共享时钟）
- Spinner 状态机（requesting/thinking/responding/tool-use/stalled）
- StreamMarkdown 增量解析

**agents_demo 现状：**
```python
# main.py — 纯 print 输出
print(render_welcome_dashboard(...))  # 文本拼接
print(f"\n阶段开始：{agent.name}")     # 简单日志
print(delta, end="", flush=True)      # 基础流式输出
```

**差距量化：**
- 无终端渲染引擎：纯文本，无 ANSI 差分
- 无虚拟滚动：历史消息全部在终端缓冲区
- 无动画系统：无 spinner、无 shimmer、无进度指示器
- 流式输出基础：逐 token 打印但无结构化渲染
- 无 Vim 模式：无组合式文本编辑

### 2.4 权限系统 — 单层 vs 5 层防御

**Claude Code 5 层：**
```
Layer 1: Permission Mode  → 信任级别
Layer 2: Rule Matching    → 命令模式白名单/黑名单
Layer 3: Bash AST 分析    → tree-sitter 23项安全检查
Layer 4: User Confirmation → 200ms 防抖
Layer 5: Hook Validation  → 用户定义规则
```

**agents_demo 现状：**
```python
# main.py:680 — 单层用户确认
async def run_with_approval(agent, run_input, hooks, session=None, max_turns=20):
    # 仅检查 tool_name + arguments 签名
    # 无 Bash AST 分析
    # 无命令模式匹配
    # 无 200ms 防抖
```

**差距：**
- 无 Bash AST 分析（tree-sitter）
- 无 200ms 防误点延迟
- 无 Hook 系统对工具输入的变更能力
- 无增量信任升级（信任随使用增长）

### 2.5 Context 管理 — 无压缩管线

**Claude Code 4 级压缩：**
```
TRUNCATE (80%) → DEDUPLICATE (85%) → FOLD (90%) → SUMMARIZE (95%)
压缩后自动恢复：最近编辑的 5 个文件 + 活跃 Skill 上下文
```

**agents_demo 现状：**
```python
# main.py:637 — 仅保留最近 6 轮文本摘要
append_recent_turn(recent_turns, "user", user_input)
append_recent_turn(recent_turns, "assistant", str(final_output), max_chars=800)
recent_turns = recent_turns[-6:]
```

**差距：**
- 无自动压缩触发（Claude Code 约 83.5% 利用率时触发）
- 无结构化摘要（保留意图/技术概念/文件/错误/剩余任务）
- 无用户消息保留策略
- 无断路器（3 次连续失败停止）

### 2.6 Hooks 系统 — 完全缺失

**Claude Code：23+ 事件类型**
```json
{
  "hooks": {
    "PreToolUse": [{ "matcher": "Bash", "hooks": [...] }],
    "PostToolUse": [{ "matcher": "Write", "hooks": [...] }]
  }
}
```

**agents_demo 现状：** 无 Hooks 系统。工具审批只有内置的 ask/allow/deny。

### 2.7 多 Agent — SDK Handoff vs AgentTool

**Claude Code：3 种协调模式**
- Sub-Agent：主 Agent 分发 → 子 Agent 返回（AgentTool 作为工具被调用）
- Coordinator：纯编排，不直接读写
- Swarm：对等 Agent 通过邮箱通信，各自独立 worktree

**agents_demo 现状：**
```python
# runtime/execution/dynamic.py — 串行或分批并行
# 使用 OpenAI SDK 的 handoff 机制
# 无 Swarm 模式
# 无独立 worktree 隔离
```

### 2.8 会话持久化 — 内存 vs JSONL

**Claude Code：**
- JSONL 格式持久化
- `/resume` 恢复历史会话
- `/fork-session` 分支对话
- 会话间消息复用

**agents_demo 现状：**
```python
# main.py:427 — 内存中的 recent_turns 列表
recent_turns = []
# SessionCheckpointManager 仅管理文件回滚，不管消息持久化
```

---

## 三、与 OpenCode 模型厂商切换差距：逐项分析

### 3.1 提供者抽象层 — 单一 vs 多层

**OpenCode 三层架构：**
```
Provider.getModel()
  ├─ BUNDLED_PROVIDERS 查找（19+ 提供者）
  ├─ 动态 import 创建函数（懒加载）
  ├─ SDK 缓存（providerID + npm + options 哈希）
  ├─ 选项合并（provider options + model options + env vars）
  └─ LanguageModelV3 统一接口
```

**agents_demo 现状：**
```python
# catalog_system/model_catalog.py:507 — 所有模型强制走 OpenAI-compatible
class ModelRegistry:
    def get_model(self, model_id: str):
        # ...
        client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        return OpenAIChatCompletionsModel(model=model_name, openai_client=client)
```

**关键限制：**
- 所有外部模型都通过 OpenAI-compatible 协议
- 无法使用 Anthropic 原生 SDK（thinking、prompt caching 等功能）
- 无法使用 Google Gemini 原生 SDK
- 无法使用 AWS Bedrock 原生 SDK
- 无消息转换管线（Anthropic 空内容过滤、工具 ID 消毒等）

### 3.2 配置系统 — .env 平铺 vs opencode.json 结构化

**OpenCode 结构化配置：**
```json
{
  "provider": {
    "my-gateway": {
      "npm": "@ai-sdk/openai-compatible",
      "options": { "baseURL": "...", "apiKey": "..." },
      "models": {
        "openai/gpt-5": {
          "options": { "reasoningEffort": "high" }
        },
        "anthropic/claude-sonnet": {
          "options": { "thinking": { "type": "enabled", "budgetTokens": 16000 } }
        }
      }
    }
  },
  "model": "openai/gpt-5"
}
```

**agents_demo 现状：**
```bash
# .env — 平铺式，无 Provider→Model 层级
MODEL_DEEPSEEK_API_KEY=sk-xxx
MODEL_DEEPSEEK_BASE_URL=https://api.deepseek.com
MODEL_DEEPSEEK_MODELS=deepseek_v4_flash:deepseek-v4-flash,deepseek_v4_pro:deepseek-v4-pro
```

**差距：**
- 无 Provider→Model 层级结构
- 无模型级别的 options 覆盖（reasoningEffort、thinking budget 等）
- 无 Provider 级别的 SDK 类型声明（Anthropic SDK vs OpenAI SDK）
- 无用户级/项目级配置合并

### 3.3 运行时模型切换 — 环境变量 vs 实时 TUI

**OpenCode：**
- `/models` 打开 TUI 模型选择对话框
- `Ctrl+O` 快捷键
- 每个 Session 可有不同模型

**agents_demo 现状：**
```python
# /models select 写 config.toml，依赖 .env 变量
# 无实时模型切换，需重启或在 chat_loop 中手动输入命令
```

### 3.4 消息转换管线 — 完全缺失

**OpenCode transform.ts 的转换：**

| 转换 | 目标 | 目的 |
|------|------|------|
| 空内容过滤 | Anthropic/Bedrock | 移除导致 API 错误的空字符串 |
| 工具 ID 消毒 | Claude/Mistral | 规范化 tool_call ID |
| 序列校正 | Mistral | 注入 assistant 消息 |
| 推理提取 | 推理模型 | 移入 providerOptions |

**agents_demo 现状：** 无任何消息转换。完全依赖 OpenAI SDK 内部的转换逻辑。

### 3.5 SDK 缓存 — 无实例复用

**OpenCode：**
```typescript
// 使用 { providerID, npm, options } 的哈希进行缓存
// 不同 baseURL/apiKey 的模型自动获得独立缓存 SDK 实例
const hash = hashKey(providerID, npm, mergedOptions)
```

**agents_demo 现状：**
```python
# 每次 get_model() 都创建新的 AsyncOpenAI client
client = AsyncOpenAI(api_key=api_key, base_url=base_url)
```

---

## 四、实现 Claude 体验的改造路线图

### 阶段 A：基础体验提升（1-2周）

**A1. 快速路径启动优化**
```
入口改造：lucode/entry.py
- --version / -v → 零导入直接 print 返回
- --help → 仅在需要时构建 parser
- doctor / config / model / mcp / session → 最小导入
- 仅 chat / run 才 import main.py
```

**A2. 早期输入捕获**
```python
# 在 lucode/entry.py 的 chat 路径中，main.py import 之前：
# 启动 raw mode stdin 读取线程，缓冲按键
# main.py chat_loop 启动后重放
```

**A3. 流式输出增强**
```python
# 改进 main.py 的 _stream_delta_text：
# - 区分 thinking delta vs text delta
# - 添加 spinner 动画（使用 rich 库或简单 ASCII 旋转器）
# - 工具调用时显示进度指示
```

**A4. Token 用量实时显示**
```python
# 在 TokenLoggerHooks 中添加：
# - 实时 token 计数（不是只在一轮结束后汇总）
# - 费用估算（基于模型定价表）
# - /cost 命令查看会话累计费用
```

### 阶段 B：Context 与错误处理（2-3周）

**B5. Context 压缩管线**
```python
# 新建 runtime/context/compaction.py
class ContextCompactor:
    def compact(self, messages, utilization_pct=0.835):
        # Level 1: TRUNCATE - 裁剪大型工具输出
        # Level 2: DEDUPLICATE - 移除重复内容
        # Level 3: FOLD - 折叠非活跃段落
        # Level 4: SUMMARIZE - 用子 Agent 总结历史
        # 压缩后注入最近编辑的 5 个文件 + 活跃 Skill 上下文
```

**B6. 自研 Agent Loop（替代 SDK Runner）**
```python
# 新建 runtime/core/agent_loop.py
class AgentLoop:
    """
    双层循环：
    - 外层：会话状态 + Compaction + 权限
    - 内层：流式调用 + 工具执行 + 7种继续站点
    """
    async def run_turn(self, user_input):
        # 追加用户消息
        # while True:
        #   流式调用 LLM → 解析 tool_calls
        #   无 tool_calls → 停止
        #   并发执行只读工具，串行化写入工具
        #   注入 ToolResult 回消息流
```

**B7. 错误即反馈机制**
```python
# 工具失败/被拒绝后不崩溃，作为 ToolResult 注入消息流
# 模型看到错误后自适应选择替代策略
# 失败成为推理循环的一部分
```

### 阶段 C：权限与 Hooks（2-3周）

**C8. 5 层权限防御**
```python
# Layer 1: Permission Mode（信任级别）
# Layer 2: Rule Matching（命令模式匹配）
# Layer 3: Bash AST 分析（使用 tree-sitter Python binding）
# Layer 4: User Confirmation + 200ms 防抖（已部分实现，需增强）
# Layer 5: Hook Validation（新增）
```

**C9. Hooks 系统**
```python
# 新建 runtime/hooks/
class HookSystem:
    """
    事件类型：
    - PreToolUse / PostToolUse（按工具名匹配）
    - PreCompaction / PostCompaction
    - SessionStart / SessionEnd
    - PreMessage / PostMessage
    """
    # 退出码协议：0=允许, 2=阻止, 其他=允许但记录
```

### 阶段 D：多厂商模型切换（2-3周）

**D10. 多 Provider SDK 抽象层**
```python
# 新建 runtime/providers/
class ProviderRegistry:
    """
    支持 SDK 类型：
    - openai (AsyncOpenAI)
    - anthropic (Anthropic SDK)
    - google (Google Generative AI SDK)
    - bedrock (AWS Bedrock SDK)
    - openai_compatible (现有逻辑)
    - ollama (现有逻辑)
    """
    def get_sdk(self, provider_id, options):
        # 懒加载：首次访问才 import SDK
        # 缓存：{ provider_id + options_hash → SDK 实例 }
```

**D11. 消息转换管线**
```python
# 新建 runtime/providers/transform.py
class MessageTransformer:
    """
    按 provider 类型转换消息：
    - Anthropic: 空内容过滤、tool_call ID 规范化
    - Mistral: tool→user 序列校正
    - 推理模型: reasoning_content 提取
    """
```

**D12. 结构化 Provider→Model 配置**
```python
# 新建 runtime/config/provider_schema.py
# 定义 provider_config 的 schema，支持：
# - provider 级别: npm/sdk_type, base_url, api_key, headers
# - model 级别: model_name, thinking, reasoning_effort, max_tokens
# - 合并优先级: model options > provider options > env vars
```

**D13. 运行时模型切换 UI**
```python
# 增强 chat_loop：
# - /model 命令打开模型选择菜单（使用 rich 或 questionary）
# - Ctrl+M 快捷键切换模型
# - 实时显示当前模型名称在状态栏
```

---

## 五、实现优先级推荐

### 最高 ROI（立即可做）

| 优先级 | 改造项 | 成本 | 收益 |
|--------|--------|------|------|
| P0 | 快速路径启动（--version 零导入） | 低（30行） | 极大（感知速度） |
| P0 | 多 Provider SDK 抽象层 | 中（300行） | 极大（解锁所有厂商） |
| P0 | 消息转换管线 | 低（150行） | 大（避免 API 错误） |
| P1 | 早期输入捕获 | 低（50行） | 中（UX 细节） |
| P1 | 结构化 Provider→Model 配置 | 中（400行） | 大（配置体验） |
| P1 | Hooks 系统（PreToolUse/PostToolUse） | 中（300行） | 大（可扩展性） |

### 中期投入

| 优先级 | 改造项 | 成本 | 收益 |
|--------|--------|------|------|
| P2 | Context 压缩管线 | 高（800行） | 极大（长对话稳定性） |
| P2 | 权限 Bash AST 分析 | 中（200行） | 中（安全性） |
| P2 | 运行时模型切换 UI | 中（200行） | 中（UX） |

### 长期愿景

| 优先级 | 改造项 | 成本 | 收益 |
|--------|--------|------|------|
| P3 | 自研 Agent Loop（替代 SDK Runner） | 极高（2000行+） | 极大（完全控制） |
| P3 | 终端 UI 升级（rich/ink 级别） | 极高 | 大（体验天花板） |
| P3 | 虚拟滚动 + 动画系统 | 极高 | 中 |

---

## 六、关键代码定位索引

### agents_demo 需要改造的核心文件：

| 文件 | 当前职责 | 需要改造 |
|------|---------|---------|
| `lucode/entry.py:81-89` | CLI 入口，chat 路径直接 import main | 添加快速路径分发 |
| `main.py:1-33` | 顶层 30+ imports | 改为延迟 import |
| `main.py:376-405` | `main()` 启动流程 | 并行化预取 |
| `main.py:777-797` | `_run_agent_once()` 使用 SDK Runner | 替换为自研 Agent Loop |
| `main.py:680-757` | `run_with_approval()` 单层审批 | 增加 Bash AST + 防抖 |
| `runtime/agents/sdk.py:24-71` | SDK 懒加载封装 | 扩展为多 Provider 支持 |
| `catalog_system/model_catalog.py:507-541` | `ModelRegistry.get_model()` | 支持非 OpenAI SDK |
| `runtime/config/model_config.py` | 配置加载 | 改为结构化 Provider→Model schema |
| `runtime/modes/solo.py:130-149` | `run_solo_request()` | 增加 Compaction 触发 |

### 需要新建的文件：

| 文件 | 职责 |
|------|------|
| `runtime/providers/__init__.py` | Provider SDK 抽象层入口 |
| `runtime/providers/anthropic_provider.py` | Anthropic SDK 适配 |
| `runtime/providers/google_provider.py` | Google Gemini SDK 适配 |
| `runtime/providers/transform.py` | 消息转换管线 |
| `runtime/providers/registry.py` | SDK 缓存与懒加载 |
| `runtime/core/agent_loop.py` | 自研 Agent Loop |
| `runtime/core/compaction.py` | Context 压缩管线 |
| `runtime/hooks/__init__.py` | Hooks 系统 |
| `runtime/config/provider_schema.py` | 结构化 Provider 配置 schema |
| `runtime/ui/spinner.py` | 终端 Spinner 动画 |
