import sys
import asyncio
import json
import os
import threading
from collections import defaultdict
from dataclasses import dataclass
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
from planning.planner_schema import sanitize_text
from planning.planner import format_plan_preview, preview_plan
from runtime.config.cli import (
    apply_writable_config_command,
    parse_writable_config_command,
    render_diff_command,
    render_readonly_command,
    render_status_command,
)
from runtime.common.conversation import append_recent_turn, compose_recent_context
from runtime.config.execution_mode import runtime_route_for_input
from runtime.modes.full import run_full_request
from runtime.modes.serial import run_serial_request
from runtime.modes.solo import run_solo_request
from runtime.config.settings import RuntimeSettings
from runtime.safety.session_checkpoint import SessionCheckpointManager

# 当前 main.py 所在目录，也就是项目根目录。
BASE_DIR = Path(__file__).resolve().parent

# 读取当前项目目录下的 .env 文件。
# 你的 API Key、base_url、模型名都放在 .env 里，代码通过 os.getenv(...) 读取。
load_dotenv(BASE_DIR / ".env")

# Windows PowerShell 有时默认使用 GBK 编码。
# 这里把 Python 标准输出改成 UTF-8，避免中文、特殊符号打印时报编码错误。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stdin, "reconfigure"):
    sys.stdin.reconfigure(encoding="utf-8")

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


@dataclass
class RuntimeTurnResult:
    final_output: str
    stopped: bool = False


def _turn_timeout_seconds() -> float | None:
    raw = str(os.environ.get("AGENTS_TURN_TIMEOUT_SECONDS") or "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value > 0 else None


class StdinConsoleAdapter:
    """Single-reader stdin adapter so runtime stop/approval won't compete for input()."""

    interactive = True
    _EOF = object()

    def __init__(self):
        self._deferred_lines = []
        self._loop = None
        self._queue = None
        self._reader_started = False
        self._reader_lock = threading.Lock()

    async def read_line(self, prompt: str = "\n你：") -> str:
        print(prompt, end="", flush=True)
        if self._deferred_lines:
            return self._deferred_lines.pop(0)
        self._ensure_reader()
        item = await self._queue.get()
        if item is self._EOF:
            raise EOFError
        return item

    async def read_runtime_line(self) -> str:
        if self._deferred_lines:
            return self._deferred_lines.pop(0)
        self._ensure_reader()
        item = await self._queue.get()
        if item is self._EOF:
            raise EOFError
        return item

    def defer(self, line: str) -> None:
        if line is None:
            return
        self._deferred_lines.append(line)

    def _ensure_reader(self) -> None:
        if self._reader_started:
            return
        with self._reader_lock:
            if self._reader_started:
                return
            self._loop = asyncio.get_running_loop()
            self._queue = asyncio.Queue()
            thread = threading.Thread(target=self._reader_loop, daemon=True)
            thread.start()
            self._reader_started = True

    def _reader_loop(self) -> None:
        while True:
            line = sys.stdin.readline()
            if line == "":
                self._loop.call_soon_threadsafe(self._queue.put_nowait, self._EOF)
                return
            self._loop.call_soon_threadsafe(self._queue.put_nowait, line.rstrip("\r\n"))


class RuntimeCommandSession:
    """Watch stdin while a turn is running so /stop can cancel in-flight work."""

    def __init__(self, console, timeout_seconds: float | None = None):
        self.console = console
        self.timeout_seconds = timeout_seconds if timeout_seconds and timeout_seconds > 0 else None
        self._approval_future = None

    async def run(self, work_coro):
        if callable(work_coro):
            work_coro = work_coro()
        work_task = asyncio.create_task(work_coro)
        interactive = bool(getattr(self.console, "interactive", False))
        input_task = asyncio.create_task(self.console.read_runtime_line()) if interactive else None
        timeout_task = (
            asyncio.create_task(asyncio.sleep(self.timeout_seconds)) if self.timeout_seconds is not None else None
        )

        if not interactive and timeout_task is None:
            try:
                return RuntimeTurnResult(final_output=await work_task)
            finally:
                if not work_task.done():
                    await _cancel_task_without_blocking(work_task)

        try:
            while True:
                wait_set = {work_task}
                if input_task is not None:
                    wait_set.add(input_task)
                if timeout_task is not None:
                    wait_set.add(timeout_task)
                done, _ = await asyncio.wait(
                    wait_set,
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if work_task in done:
                    if timeout_task is not None and not timeout_task.done():
                        timeout_task.cancel()
                        await asyncio.gather(timeout_task, return_exceptions=True)
                    if input_task is not None and not input_task.done():
                        input_task.cancel()
                        await asyncio.gather(input_task, return_exceptions=True)
                    return RuntimeTurnResult(final_output=await work_task)

                if timeout_task is not None and timeout_task in done:
                    if input_task is not None and not input_task.done():
                        input_task.cancel()
                        await asyncio.gather(input_task, return_exceptions=True)
                    await _cancel_task_without_blocking(work_task)
                    return RuntimeTurnResult(
                        final_output="本轮执行超过超时时间，已自动中断。你可以把任务拆小一点，或稍后重试。",
                        stopped=True,
                    )

                line = sanitize_text(await input_task).lstrip("\ufeff").strip() if input_task is not None else ""

                if self._approval_future is not None and not self._approval_future.done():
                    if _is_stop_command(line):
                        self._approval_future.set_result("")
                        await _cancel_task_without_blocking(work_task)
                        return RuntimeTurnResult(
                            final_output="已收到 /stop，本轮执行已中断。你可以直接重新输入新的问题。",
                            stopped=True,
                        )
                    self._approval_future.set_result(line)
                elif _is_stop_command(line):
                    await _cancel_task_without_blocking(work_task)
                    return RuntimeTurnResult(
                        final_output="已收到 /stop，本轮执行已中断。你可以直接重新输入新的问题。",
                        stopped=True,
                    )
                elif line:
                    self.console.defer(line)

                if input_task is not None:
                    input_task = asyncio.create_task(self.console.read_runtime_line())
        finally:
            if timeout_task and not timeout_task.done():
                timeout_task.cancel()
                await asyncio.gather(timeout_task, return_exceptions=True)
            if input_task and not input_task.done():
                input_task.cancel()
                await asyncio.gather(input_task, return_exceptions=True)
            if work_task and not work_task.done():
                await _cancel_task_without_blocking(work_task)

    async def request_approval(self, prompt: str) -> str:
        print(prompt)
        if not getattr(self.console, "interactive", False):
            try:
                return sanitize_text(await self.console.read_runtime_line()).strip().lower()
            except EOFError:
                return ""

        loop = asyncio.get_running_loop()
        self._approval_future = loop.create_future()
        try:
            answer = await self._approval_future
            return sanitize_text(answer).strip().lower()
        finally:
            self._approval_future = None


async def _cancel_task_without_blocking(task: asyncio.Task, timeout: float = 2.0) -> bool:
    if task.done():
        await asyncio.gather(task, return_exceptions=True)
        return True
    task.cancel()
    done, pending = await asyncio.wait({task}, timeout=timeout)
    if done:
        await asyncio.gather(task, return_exceptions=True)
        return True

    def _consume_late_result(late_task):
        try:
            late_task.result()
        except (asyncio.CancelledError, Exception):
            pass

    for pending_task in pending:
        pending_task.add_done_callback(_consume_late_result)
    return False


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
    runtime_settings = RuntimeSettings.from_env()
    console = StdinConsoleAdapter()

    print("终端工程代理已启动。Skill/MCP/Model 图书馆已刷新。")
    print(f"MCP 将按需启动；删除前备份目录为 {quarantine_dir / 'backups'}")
    print(runtime_settings.summary_zh())
    print("当前执行模式由 /mode 查看；solo 为默认单模型工具 Agent，serial/full 才进入多 Agent 编排。")
    print("输入 /plan 加问题可只预览多 Agent 规划，不会执行任务。")
    print("配置查看：/config、/api show、/privacy、/model、/model available、/status、/diff。")
    print("配置切换：/mode solo|serial|full，/refiner on|off。")
    print("提示：终端无法折叠内容，当前展示的是可见执行日志，不展示模型隐藏思考链。")
    print("输入 /stop 可以中止当前输入并重来；/new 可以开始新对话；/rollback 回滚最近一轮；/exit 可以结束。")

    await chat_loop(model_registry, quarantine_dir, runtime_settings, console)


async def chat_loop(model_registry, quarantine_dir, runtime_settings, console):
    """Run the interactive command-line chat loop."""

    # recent_turns 是一个轻量短期上下文，避免当前会话完全忘记上一轮。
    # 长期记忆/知识图谱后续再接，这里只保留最近几轮文本。
    recent_turns = []
    checkpoint_manager = SessionCheckpointManager(BASE_DIR)
    started_mcp_ids: list[str] = []

    while True:
        try:
            user_input = sanitize_text(await console.read_line()).lstrip("\ufeff").strip()
        except EOFError:
            # 当输入流被关闭时触发，例如从文件或管道读取输入读完了。
            # 手动在命令行聊天时一般不会遇到，这里只是让程序能优雅退出。
            print("\n输入结束，已退出。")
            break

        if _is_exit_command(user_input):
            print("已退出。")
            break

        if _is_stop_command(user_input):
            print("已停止当前输入，你可以重新输入新的问题。")
            continue

        if _is_new_command(user_input):
            recent_turns = []
            print("已创建新对话，历史上下文已清空。")
            continue

        if not user_input:
            continue

        if parse_writable_config_command(user_input) is not None or user_input.lower().startswith(("/mode ", "/refiner ")):
            output, _ = apply_writable_config_command(user_input, BASE_DIR / ".env", runtime_settings)
            print(output)
            continue

        if user_input.lower() == "/status":
            print(
                render_status_command(
                    BASE_DIR,
                    runtime_settings,
                    started_mcp_ids=started_mcp_ids,
                    rollback_status=checkpoint_manager.render_status(),
                )
            )
            continue

        if user_input.lower().startswith("/diff"):
            print(render_diff_command(BASE_DIR))
            continue

        if user_input.lower() == "/rollback":
            result = checkpoint_manager.rollback_last_turn()
            print(result.message)
            continue

        config_output = render_readonly_command(user_input, runtime_settings)
        if config_output:
            print(config_output)
            continue

        if user_input.startswith("/plan"):
            plan_input = user_input.removeprefix("/plan").strip()
            if not plan_input:
                print("请在 /plan 后面输入要规划的问题。")
                continue

            print("\n正在生成规划预览，不会执行任务...")
            hooks = TokenLoggerHooks()
            try:
                refiner_model_id = (
                    runtime_settings.select_model_id(model_registry, "query_refiner")
                    if runtime_settings.query_refiner_enabled
                    else None
                )
                planner_model_id = runtime_settings.select_model_id(model_registry, "orchestrator")
                session = RuntimeCommandSession(console, timeout_seconds=_turn_timeout_seconds())

                async def _preview_work():
                    refined, plan = await preview_plan(
                        plan_input,
                        refiner_model=model_registry.get_model(refiner_model_id) if refiner_model_id else None,
                        planner_model=model_registry.get_model(planner_model_id),
                        hooks=hooks,
                        refiner_enabled=runtime_settings.query_refiner_enabled,
                    )
                    return format_plan_preview(refined, plan)

                turn_result = await session.run(_preview_work)
                print(turn_result.final_output)
            except Exception as exc:
                print(_format_turn_error(exc))
            finally:
                hooks.print_summary()
            continue

        # 每一轮都新建 hooks，这样本轮 token 用量会单独统计。
        hooks = TokenLoggerHooks()

        run_input = compose_recent_context(recent_turns, user_input)
        checkpoint_manager.begin_turn()
        try:
            session = RuntimeCommandSession(console, timeout_seconds=_turn_timeout_seconds())
            route = runtime_route_for_input(user_input, runtime_settings.execution_mode)

            if route == "solo":
                async with MCPServerManager(BASE_DIR, quarantine_dir, verbose=True) as mcp_manager:

                    async def _solo_work():
                        return await run_solo_request(
                            run_input,
                            model_registry,
                            mcp_manager,
                            hooks,
                            run_agent=lambda agent, turn_input, turn_hooks, max_turns=20: run_with_approval(
                                agent,
                                turn_input,
                                turn_hooks,
                                session=session,
                                max_turns=max_turns,
                            ),
                            settings=runtime_settings,
                        )

                    turn_result = await session.run(_solo_work)
                    started_mcp_ids = mcp_manager.started_ids
                    final_output = turn_result.final_output
            elif runtime_settings.execution_mode == "full":
                async with MCPServerManager(BASE_DIR, quarantine_dir, verbose=True) as mcp_manager:

                    async def _turn_work():
                        return await run_full_request(
                            run_input,
                            BASE_DIR,
                            model_registry,
                            mcp_manager,
                            hooks,
                            run_agent=lambda agent, turn_input, turn_hooks, max_turns=20: run_with_approval(
                                agent,
                                turn_input,
                                turn_hooks,
                                session=session,
                                max_turns=max_turns,
                            ),
                            settings=runtime_settings,
                            show_plan=True,
                        )

                    turn_result = await session.run(_turn_work)
                    started_mcp_ids = mcp_manager.started_ids
                    final_output = turn_result.final_output
            else:
                async with MCPServerManager(BASE_DIR, quarantine_dir, verbose=True) as mcp_manager:

                    async def _turn_work():
                        return await run_serial_request(
                            run_input,
                            BASE_DIR,
                            model_registry,
                            mcp_manager,
                            hooks,
                            run_agent=lambda agent, turn_input, turn_hooks, max_turns=20: run_with_approval(
                                agent,
                                turn_input,
                                turn_hooks,
                                session=session,
                                max_turns=max_turns,
                            ),
                            settings=runtime_settings,
                            show_plan=True,
                        )

                    turn_result = await session.run(_turn_work)
                    started_mcp_ids = mcp_manager.started_ids
                    final_output = turn_result.final_output
        except MaxTurnsExceeded:
            final_output = (
                "本轮任务超过最大工具/模型轮数，已自动停止。"
                "建议用 /plan 先查看规划，或把任务拆得更具体一点。"
            )
        except Exception as exc:
            final_output = _format_turn_error(exc)
        finally:
            checkpoint_manager.complete_turn()

        # final_output 是最终回答内容。
        print("\n========== Final output ==========")
        print(final_output)

        append_recent_turn(recent_turns, "user", user_input)
        append_recent_turn(recent_turns, "assistant", str(final_output), max_chars=800)
        recent_turns = recent_turns[-6:]

        # 打印本轮每个 Agent 的 token 汇总。
        hooks.print_summary()


async def run_with_approval(agent, run_input, hooks, session=None, max_turns=20):
    """Run an agent and ask the user before executing approval-required tools."""

    approved_signatures = set()
    result = await _run_agent_once(agent, run_input, hooks, max_turns=max_turns)

    while result.interruptions:
        state = result.to_state()

        for item in result.interruptions:
            signature = (item.qualified_name or item.name, item.arguments or "")
            if signature in approved_signatures:
                state.reject(
                    item,
                    rejection_message=(
                        "同一工具调用已经批准并执行过一次。请不要重复请求相同工具，"
                        "请根据上一次工具结果直接给出最终回答。"
                    ),
                )
                continue

            print("\n--- 需要你的确认 ---")
            print(f"工具：{item.qualified_name or item.name}")
            preview = _format_tool_preview(item.qualified_name or item.name, item.arguments)
            if preview:
                print(preview)
            print("参数：")
            print(_format_tool_arguments(item.arguments))
            print("说明：请检查参数。写入、删除、命令或提交类工具可能改变项目状态；删除/覆盖会先做备份。")

            if session is not None:
                answer = await session.request_approval("是否批准执行？输入 yes 批准，其它输入表示拒绝：")
            else:
                try:
                    answer = sanitize_text(
                        input("是否批准执行？输入 yes 批准，其它输入表示拒绝：")
                    ).strip().lower()
                except EOFError:
                    answer = ""
            if answer in {"yes", "y"}:
                state.approve(item)
                approved_signatures.add(signature)
            else:
                state.reject(
                    item,
                    rejection_message=(
                        "用户未批准该工具调用，或当前输入流无法交互审批。"
                        "请停止请求写入、删除、命令或提交工具，并给出替代建议。"
                    ),
                )

        result = await _run_agent_once(agent, state, hooks, max_turns=max_turns)

    return result


async def _run_agent_once(agent, run_input, hooks, max_turns=20):
    """Run one SDK segment; stream visible answer deltas when the provider supports it."""

    if not _streaming_enabled():
        return await Runner.run(agent, run_input, hooks=hooks, max_turns=max_turns)

    result = Runner.run_streamed(agent, run_input, hooks=hooks, max_turns=max_turns)
    printed_any = False
    async for event in result.stream_events():
        delta = _stream_delta_text(event)
        if not delta:
            continue
        if not printed_any:
            print("\n流式输出：", end="", flush=True)
            printed_any = True
        print(delta, end="", flush=True)
    if printed_any:
        print()
    return result


def _streaming_enabled() -> bool:
    raw = str(os.environ.get("AGENTS_STREAM_OUTPUT") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off", "disable", "disabled"}


def _stream_delta_text(event) -> str:
    if getattr(event, "type", "") != "raw_response_event":
        return ""
    data = getattr(event, "data", None)
    event_type = str(getattr(data, "type", ""))
    if event_type not in {"response.output_text.delta", "response.text.delta"}:
        return ""
    return str(getattr(data, "delta", "") or "")


def _format_tool_arguments(arguments):
    if not arguments:
        return "无"

    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError:
        return arguments

    return json.dumps(parsed, ensure_ascii=False, indent=2)


def _format_tool_preview(tool_name: str, arguments: str | None) -> str:
    if not arguments:
        return ""
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError:
        return ""
    name = str(tool_name or "")
    path = parsed.get("path") or parsed.get("target") or parsed.get("file_path") or ""
    reason = parsed.get("reason") or ""
    if any(marker in name for marker in ["write_file", "create_file", "replace_in_file", "apply_unified_patch"]):
        lines = ["写入预览"]
        if path:
            lines.append(f"- 目标：{path}")
        if "content" in parsed:
            lines.append(f"- 内容长度：{len(str(parsed.get('content') or ''))} 字符")
        if "old_text" in parsed:
            lines.append(f"- 将替换文本长度：{len(str(parsed.get('old_text') or ''))} 字符")
        if "new_text" in parsed:
            lines.append(f"- 新文本长度：{len(str(parsed.get('new_text') or ''))} 字符")
        if "patch" in parsed:
            lines.append(f"- Patch 长度：{len(str(parsed.get('patch') or ''))} 字符")
        if parsed.get("expected_sha256") or parsed.get("expected_sha256_map"):
            lines.append("- 已提供 sha256 基线")
        return "\n".join(lines)
    if "delete" in name or "safe_delete" in name:
        lines = ["删除/备份预览"]
        if path:
            lines.append(f"- 目标：{path}")
        if reason:
            lines.append(f"- 说明：{reason}")
        lines.append("- 删除或覆盖前会按工具策略创建备份。")
        return "\n".join(lines)
    if "command" in name or "git_commit" in name:
        command = parsed.get("command") or parsed.get("message") or ""
        return "\n".join(["执行预览", f"- 内容：{command or '未提供'}"])
    return ""


def _format_turn_error(exc: Exception) -> str:
    """Format a recoverable per-turn error without crashing the chat loop."""

    message = sanitize_text(str(exc)).strip() or "无详细错误信息"
    class_name = exc.__class__.__name__
    friendly = _friendly_error_hint(message)
    if friendly:
        return (
            "本轮执行失败，但程序没有退出，你可以继续输入下一条问题。\n"
            f"错误类型：{class_name}\n"
            f"{friendly}"
        )
    return (
        "本轮执行失败，但程序没有退出，你可以继续输入下一条问题。\n"
        f"错误类型：{class_name}\n"
        f"错误信息：{message}\n"
        "如果是 APIConnectionError / ConnectError，通常是模型 API 网络连接临时失败，"
        "稍后重试即可。"
    )


def _is_exit_command(user_input: str) -> bool:
    return sanitize_text(user_input).strip().lower() == "/exit"


def _is_stop_command(user_input: str) -> bool:
    return sanitize_text(user_input).strip().lower() == "/stop"


def _is_new_command(user_input: str) -> bool:
    return sanitize_text(user_input).strip().lower() == "/new"


def _friendly_error_hint(message: str) -> str:
    normalized = message.lower()
    if "tool " in normalized and " not found in agent " in normalized:
        tool_name = _extract_missing_tool_name(message)
        return (
            "原因：模型尝试调用未分配的工具"
            f"{f'：{tool_name}' if tool_name else ''}。\n"
            "这通常是模型把其它任务的工具规则误认为当前任务也可用，或计划没有给该 Agent 分配对应 MCP。\n"
            "解决办法：系统已限制后续任务提示只展示已分配工具；你也可以用 /plan 查看规划，"
            "必要时把需求说得更具体后重新规划。"
        )
    if "no configured models allowed by privacy mode: offline" in normalized:
        return (
            "原因：当前是隐私模式 offline，但没有可用的本地模型配置。\n"
            "offline 模式会禁止 DeepSeek、SiliconFlow、MiMo 等云端模型，也会禁止联网搜索。\n"
            "解决办法：\n"
            "1. 如果你要严格本地运行，请在 .env 里配置本地 Ollama 模型，例如 "
            "`MODEL_LOCAL_BACKEND=ollama`、`MODEL_LOCAL_BASE_URL=http://localhost:11434`、"
            "`MODEL_LOCAL_MODEL=qwen3:8b`。\n"
            "2. 如果你想继续使用 DeepSeek/MiMo 这类云端 API，请把 `.env` 里的 "
            "`AGENTS_PRIVACY_MODE=offline` 改成 `AGENTS_PRIVACY_MODE=local_first` "
            "或 `AGENTS_PRIVACY_MODE=cloud_allowed`。"
        )
    return ""


def _extract_missing_tool_name(message: str) -> str:
    marker = "Tool "
    if marker not in message:
        return ""
    rest = message.split(marker, 1)[1]
    return rest.split(" not found in agent ", 1)[0].strip()

if __name__ == "__main__":
    # 因为 Runner.run 是异步函数，所以需要用 asyncio.run(...) 启动。
    asyncio.run(main())
