# Lucode

Lucode 是一个中文优先、本地优先、模型中立的终端编码 Agent。当前版本保留 Python 运行内核，并提供 npm wrapper，方便后续演进到 `npm install -g lucode` 的产品形态。

## 快速开始

开发和测试阶段推荐显式指定 Python 解释器。你的本机 conda 环境可以这样运行：

```powershell
$env:LUCODE_PYTHON="D:\develop\Data_anaconda2024\envs\agents-demo\python.exe"
node .\bin\lucode.js --version
node .\bin\lucode.js --workspace .agent_test_tmp\lucode_demo init
node .\bin\lucode.js --workspace .agent_test_tmp\lucode_demo doctor
node .\bin\lucode.js --workspace .agent_test_tmp\lucode_demo run "请只回复：hello lucode"
```

也可以直接用 Python 模块入口：

```powershell
D:\develop\Data_anaconda2024\envs\agents-demo\python.exe -m lucode --version
D:\develop\Data_anaconda2024\envs\agents-demo\python.exe -m lucode doctor
```

## 常用命令

```text
lucode                启动交互式终端代理
lucode run "..."      非交互执行一次任务
lucode init           在当前目录创建 .lucode 工作区
lucode doctor         检查 Python、SDK、Provider、MCP 和工作区状态
lucode config         查看当前配置
lucode model          查看模型优先级
lucode mcp            查看 MCP 注册和信任状态
lucode session        查看最近 JSONL 会话
```

交互式会话里可以输入 `/resume` 查看最近会话，或输入 `/resume last` 恢复最近一次上下文。

## 会话恢复与压缩

交互式对话会追加写入 `.lucode/sessions/*.jsonl`。恢复旧会话时，Lucode 会先用规则压缩旧消息，保留最近几条原文；当旧会话足够长时，会尝试用低成本模型生成语义摘要，失败时自动回退到规则摘要。

可选环境变量：

```powershell
$env:LUCODE_SEMANTIC_COMPACTION_ENABLED="true"
$env:LUCODE_SEMANTIC_COMPACTION_MIN_CHARS="16000"
```

## npm 本地验证

```powershell
npm link
lucode --version
lucode doctor
npm run pack:dry
```

`bin/lucode.js` 会按顺序寻找 Python：

1. `LUCODE_PYTHON`
2. `LUCODE_VENV`
3. 当前 `CONDA_PREFIX`
4. `PYTHON`
5. `~/.lucode/venv`
6. 项目或工作区 `.venv`
7. `python` / `python3`

如果没有找到可用 Python，会给出中文修复建议。正式平台二进制发布后，这层会逐步切换到 Lucode 自带 runtime。

## Python 依赖

Lucode 要求 Python 3.11+。最小开发安装：

```powershell
python -m pip install -e .
```

核心模型运行依赖 `openai-agents`。如果 `lucode doctor` 显示 SDK 缺失，请在 Lucode 实际使用的解释器中安装：

```powershell
"D:\develop\Data_anaconda2024\envs\agents-demo\python.exe" -m pip install openai-agents
```

## 工作区

运行 `lucode init` 会创建：

```text
.lucode/
  config.toml
  permissions.toml
  skills/
  mcp/
  memory/
  sessions/
```

Lucode 会从当前目录向上查找最近的 `.lucode/`，并把它的父目录作为工作区根目录。
