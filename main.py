import sys
import asyncio
import json
from collections import defaultdict
from pathlib import Path
from dotenv import load_dotenv
from agents import (
    Runner,
    RunHooks,
    OpenAIChatCompletionsModel,
    set_tracing_disabled,
)
from agents.exceptions import MaxTurnsExceeded
from catalog_system.refresher import refresh_catalogs
from catalog_system.model_catalog import ModelRegistry
from mcp_servers import MCPServerManager
from planning.planner import format_plan_preview, preview_plan
from runtime.dynamic_runtime import execute_dynamic_request

# 当前 main.py 所在目录，也就是项目根目录。
BASE_DIR = Path(__file__).resolve().parent

# 读取当前项目目录下的 .env 文件。
# 你的 API Key、base_url、模型名都放在 .env 里，代码通过 os.getenv(...) 读取。
load_dotenv(BASE_DIR / ".env")

# Windows PowerShell 有时默认使用 GBK 编码。
# 这里把 Python 标准输出改成 UTF-8，避免中文、特殊符号打印时报编码错误。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# 关闭 OpenAI 官方 tracing 上传。
# 因为你现在主要使用 DeepSeek、MiMo 这类第三方 OpenAI-compatible 接口。
set_tracing_disabled(True)


class TokenLoggerHooks(RunHooks):
    """监听运行过程，并用更清爽的中文日志展示关键信息。"""

    def __init__(self):
        # defaultdict 可以在第一次访问某个 agent 名字时，自动创建一份 token 统计表。
        self.usage_by_agent = defaultdict(
            lambda: {"requests": 0, "input": 0, "output": 0, "reasoning": 0, "total": 0}
        )
        self.tools_by_agent = defaultdict(lambda: defaultdict(int))
        self.llm_calls_by_agent = defaultdict(int)
        self.started_agents = set()

    async def on_agent_start(self, context, agent):
        # 每次某个 Agent 开始工作时触发。
        if agent.name not in self.started_agents:
            self.started_agents.add(agent.name)
            print(f"\n阶段开始：{agent.name}（模型：{get_model_name(agent)}）")

    async def on_handoff(self, context, from_agent, to_agent):
        # 当主 Agent 把任务转交给其他 Agent 时触发。
        print(f"任务分配：{from_agent.name} -> {to_agent.name}")

    async def on_tool_start(self, context, agent, tool):
        # 这里只展示可见执行日志，不展示模型内部隐藏思考。
        tool_name = getattr(tool, "name", str(tool))
        print(f"工具调用：{agent.name} -> {tool_name}")

    async def on_tool_end(self, context, agent, tool, result):
        # 只记录工具调用次数，最后统一汇总。
        tool_name = getattr(tool, "name", str(tool))
        self.tools_by_agent[agent.name][tool_name] += 1
        result_text = str(result) if result is not None else ""
        print(f"工具完成：{agent.name} <- {tool_name}（结果约 {len(result_text)} 字符）")

    async def on_llm_start(self, context, agent, system_prompt, input_items):
        # 不能输出模型隐藏思考链；这里输出的是可见进度。
        self.llm_calls_by_agent[agent.name] += 1
        call_no = self.llm_calls_by_agent[agent.name]
        print(f"模型调用：{agent.name} 第 {call_no} 次分析中...")

    async def on_llm_end(self, context, agent, response):
        # 每次大模型返回结果之后触发，可以从 response.usage 里读取 token 用量。
        usage = response.usage
        if not usage:
            print(f"模型返回：{agent.name}")
            return

        # 有些推理模型会返回 reasoning_tokens，有些模型不会返回。
        # 没有返回时，这里就按 0 处理。
        reasoning_tokens = 0
        if usage.output_tokens_details:
            reasoning_tokens = usage.output_tokens_details.reasoning_tokens or 0

        # 把这一次调用的 token 用量累加到当前 Agent 名下。
        data = self.usage_by_agent[agent.name]
        data["requests"] += usage.requests
        data["input"] += usage.input_tokens
        data["output"] += usage.output_tokens
        data["reasoning"] += reasoning_tokens
        data["total"] += usage.total_tokens
        print(
            f"模型返回：{agent.name}（本次 {usage.total_tokens} tokens，"
            f"思考 {reasoning_tokens}）"
        )

    def print_summary(self):
        # 所有 Agent 跑完以后，打印一个按 Agent 汇总的 token 报表。
        print("\n--- 本轮调用统计 ---")
        for agent_name, usage in self.usage_by_agent.items():
            print(
                f"{agent_name}："
                f"请求 {usage['requests']} 次，"
                f"输入 {usage['input']}，"
                f"输出 {usage['output']}，"
                f"思考 {usage['reasoning']}，"
                f"合计 {usage['total']} tokens"
            )

        if self.tools_by_agent:
            print("\n--- 本轮工具调用 ---")
            for agent_name, tools in self.tools_by_agent.items():
                tool_summary = "，".join(
                    f"{tool_name} x{count}" for tool_name, count in tools.items()
                )
                print(f"{agent_name}：{tool_summary}")


def get_model_name(agent):
    """从 Agent 对象中取出模型名称，方便日志显示。"""

    model = agent.model
    if isinstance(model, OpenAIChatCompletionsModel):
        return model.model
    return str(model)


async def main():
    quarantine_dir = BASE_DIR / ".agent_quarantine"
    refresh_catalogs(BASE_DIR)
    model_registry = ModelRegistry()

    print("动态多智能体模式已启动。Skill/MCP/Model 图书馆已刷新。")
    print(f"MCP 将按需启动；删除前备份目录为 {quarantine_dir / 'backups'}")
    print("普通提问会先显示本轮规划再执行；输入 /plan 加问题 可只预览不执行。")
    print("提示：终端无法折叠内容，当前展示的是可见执行日志，不展示模型隐藏思考链。")
    print("输入 exit、quit、q 或 退出 可以结束。")

    async with MCPServerManager(BASE_DIR, quarantine_dir, verbose=True) as mcp_manager:
        await chat_loop(model_registry, mcp_manager)


async def chat_loop(model_registry, mcp_manager):
    """Run the interactive command-line chat loop."""

    # recent_turns 是一个轻量短期上下文，避免动态模式完全忘记上一轮。
    # 长期记忆/知识图谱后续再接，这里只保留最近几轮文本。
    recent_turns = []

    while True:
        try:
            user_input = input("\n你：").strip()
        except EOFError:
            # 当输入流被关闭时触发，例如从文件或管道读取输入读完了。
            # 手动在命令行聊天时一般不会遇到，这里只是让程序能优雅退出。
            print("\n输入结束，已退出。")
            break

        if user_input.lower() in {"exit", "quit", "q"} or user_input == "退出":
            print("已退出。")
            break

        if not user_input:
            continue

        if user_input.startswith("/plan"):
            plan_input = user_input.removeprefix("/plan").strip()
            if not plan_input:
                print("请在 /plan 后面输入要规划的问题。")
                continue

            print("\n正在生成规划预览，不会执行任务...")
            hooks = TokenLoggerHooks()
            refiner_model_id = model_registry.first_configured(
                ["deepseek_V4_flash_model", "deepseek_V4_pro_model", "mimo_model"]
            )
            planner_model_id = model_registry.first_configured(
                ["deepseek_V4_pro_model", "deepseek_V4_flash_model", "mimo_model"]
            )
            refined, plan = await preview_plan(
                plan_input,
                refiner_model=model_registry.get_model(refiner_model_id),
                planner_model=model_registry.get_model(planner_model_id),
                hooks=hooks,
            )
            print(format_plan_preview(refined, plan))
            hooks.print_summary()
            continue

        # 每一轮都新建 hooks，这样本轮 token 用量会单独统计。
        hooks = TokenLoggerHooks()

        run_input = _compose_recent_context(recent_turns, user_input)
        try:
            final_output = await execute_dynamic_request(
                run_input,
                BASE_DIR,
                model_registry,
                mcp_manager,
                hooks,
                run_agent=run_with_approval,
                show_plan=True,
            )
        except MaxTurnsExceeded:
            final_output = (
                "本轮任务超过最大工具/模型轮数，已自动停止。"
                "建议用 /plan 先查看规划，或把任务拆得更具体一点。"
            )

        # final_output 是最终回答内容。
        print("\n========== Final output ==========")
        print(final_output)

        recent_turns.append({"role": "user", "content": user_input})
        recent_turns.append({"role": "assistant", "content": str(final_output)})
        recent_turns = recent_turns[-6:]

        # 打印本轮每个 Agent 的 token 汇总。
        hooks.print_summary()


async def run_with_approval(agent, run_input, hooks, max_turns=20):
    """Run an agent and ask the user before executing approval-required tools."""

    result = await Runner.run(agent, run_input, hooks=hooks, max_turns=max_turns)

    while result.interruptions:
        state = result.to_state()

        for item in result.interruptions:
            print("\n--- 需要你的确认 ---")
            print(f"工具：{item.qualified_name or item.name}")
            print("参数：")
            print(_format_tool_arguments(item.arguments))
            print("说明：该操作只会压缩备份，不会移动或删除原文件。")

            answer = input("是否批准执行？输入 yes 批准，其它输入表示拒绝：").strip().lower()
            if answer in {"yes", "y"}:
                state.approve(item)
            else:
                state.reject(
                    item,
                    rejection_message="用户拒绝了此删除操作。请不要删除文件，并给出替代建议。",
                )

        result = await Runner.run(agent, state, hooks=hooks, max_turns=max_turns)

    return result


def _compose_recent_context(recent_turns, user_input):
    if not recent_turns:
        return user_input

    lines = ["以下是最近几轮对话，供理解上下文。不要把历史内容当成本轮新任务，除非用户明确要求继续。"]
    for turn in recent_turns:
        label = "用户" if turn["role"] == "user" else "助手"
        content = str(turn["content"])
        if len(content) > 800:
            content = content[:800] + "...[已截断]"
        lines.append(f"{label}：{content}")
    lines.append("")
    lines.append(f"本轮用户问题：{user_input}")
    return "\n".join(lines)


def _format_tool_arguments(arguments):
    if not arguments:
        return "无"

    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError:
        return arguments

    return json.dumps(parsed, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    # 因为 Runner.run 是异步函数，所以需要用 asyncio.run(...) 启动。
    asyncio.run(main())
