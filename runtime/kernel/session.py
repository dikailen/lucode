from __future__ import annotations

import os
from collections import defaultdict

from runtime.agents.sdk import run_hooks_class
from runtime.hooks import append_tool_event_audit


def runtime_verbose_enabled() -> bool:
    raw = str(os.environ.get("LUCODE_VERBOSE_RUNTIME") or os.environ.get("AGENTS_VERBOSE_RUNTIME") or "").strip().lower()
    return raw in {"1", "true", "yes", "on", "debug", "verbose"}


def create_token_logger_hooks(verbose: bool | None = None):
    """Create SDK hooks lazily so importing CLI modules does not import the Agents SDK."""

    RunHooks = run_hooks_class()
    quiet_default = runtime_verbose_enabled() if verbose is None else bool(verbose)

    class TokenLoggerHooks(RunHooks):
        """监听运行过程，并用更清爽的中文日志展示关键信息。"""

        def __init__(self):
            self.usage_by_agent = defaultdict(
                lambda: {"requests": 0, "input": 0, "output": 0, "reasoning": 0, "total": 0}
            )
            self.tools_by_agent = defaultdict(lambda: defaultdict(int))
            self.llm_calls_by_agent = defaultdict(int)
            self.started_agents = set()
            self.streamed_output_seen = False
            self.streamed_output_chars = 0
            self.verbose = quiet_default
            self.tool_events = []

        def record_tool_event(self, event):
            self.tool_events.append(event)
            try:
                append_tool_event_audit(event)
            except Exception:
                if self.verbose:
                    print("工具审计记录写入失败，已跳过本条记录。")

        async def on_agent_start(self, context, agent):
            if agent.name not in self.started_agents:
                self.started_agents.add(agent.name)
                if self.verbose:
                    print(f"\n阶段开始：{agent.name}（模型：{get_model_name(agent)}）")

        async def on_handoff(self, context, from_agent, to_agent):
            if self.verbose:
                print(f"任务分配：{from_agent.name} -> {to_agent.name}")

        async def on_tool_start(self, context, agent, tool):
            tool_name = getattr(tool, "name", str(tool))
            if self.verbose:
                print(f"工具调用：{agent.name} -> {tool_name}")

        async def on_tool_end(self, context, agent, tool, result):
            tool_name = getattr(tool, "name", str(tool))
            self.tools_by_agent[agent.name][tool_name] += 1
            result_text = str(result) if result is not None else ""
            if self.verbose:
                print(f"工具完成：{agent.name} <- {tool_name}（结果约 {len(result_text)} 字符）")

        async def on_llm_start(self, context, agent, system_prompt, input_items):
            self.llm_calls_by_agent[agent.name] += 1
            call_no = self.llm_calls_by_agent[agent.name]
            if self.verbose:
                print(f"模型调用：{agent.name} 第 {call_no} 次分析中...")

        async def on_llm_end(self, context, agent, response):
            usage = response.usage
            if not usage:
                if self.verbose:
                    print(f"模型返回：{agent.name}")
                return

            reasoning_tokens = 0
            if usage.output_tokens_details:
                reasoning_tokens = usage.output_tokens_details.reasoning_tokens or 0

            data = self.usage_by_agent[agent.name]
            data["requests"] += usage.requests
            data["input"] += usage.input_tokens
            data["output"] += usage.output_tokens
            data["reasoning"] += reasoning_tokens
            data["total"] += usage.total_tokens
            if self.verbose:
                print(
                    f"模型返回：{agent.name}（本次 {usage.total_tokens} tokens，"
                    f"思考 {reasoning_tokens}）"
                )

        def print_summary(self):
            if not self.verbose:
                return
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

    return TokenLoggerHooks()


def get_model_name(agent):
    """从 Agent 对象中取出模型名称，方便日志显示。"""

    model = getattr(agent, "model", None)
    model_name = getattr(model, "model", None)
    if model_name:
        return model_name
    return str(model)
