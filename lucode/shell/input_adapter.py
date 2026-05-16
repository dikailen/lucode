from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import threading
from dataclasses import dataclass

from runtime.commands.completion import (
    create_slash_command_completer,
    slash_prompt_message,
    slash_prompt_session_kwargs,
)
from runtime.common.text_utils import sanitize_text


@dataclass
class RuntimeTurnResult:
    final_output: str
    stopped: bool = False


def should_enable_prompt_toolkit(
    stdin=None,
    stdout=None,
    *,
    prompt_toolkit_available: bool | None = None,
    env=None,
) -> bool:
    env = os.environ if env is None else env
    if str(env.get("LUCODE_DISABLE_PROMPT_TOOLKIT", "")).strip().lower() in {"1", "true", "yes", "on"}:
        return False
    if prompt_toolkit_available is None:
        prompt_toolkit_available = importlib.util.find_spec("prompt_toolkit") is not None
    if not prompt_toolkit_available:
        return False
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    return _is_tty(stdin) and _is_tty(stdout)


def prompt_mouse_support_enabled(env=None) -> bool:
    env = os.environ if env is None else env
    value = str(env.get("LUCODE_PROMPT_MOUSE_SUPPORT", "")).strip().lower()
    return value in {"1", "true", "yes", "on"}


class StdinConsoleAdapter:
    """Single-reader stdin adapter so runtime stop/approval won't compete for input()."""

    interactive = True
    _EOF = object()

    def __init__(self, enable_prompt_toolkit: bool | None = None):
        self._deferred_lines = []
        self._enable_prompt_toolkit = enable_prompt_toolkit
        self._loop = None
        self._queue = None
        self._reader_started = False
        self._reader_lock = threading.Lock()
        self._main_prompt_session = None
        self._runtime_prompt_session = None

    async def read_line(self, prompt: str = "\n你：") -> str:
        if self._deferred_lines:
            return self._deferred_lines.pop(0)
        if self._should_use_prompt_toolkit():
            return await self._read_prompt_toolkit_line(prompt, complete_slash=True)
        print(prompt, end="", flush=True)
        self._ensure_reader()
        item = await self._queue.get()
        if item is self._EOF:
            raise EOFError
        return item

    async def read_runtime_line(self) -> str:
        if self._deferred_lines:
            return self._deferred_lines.pop(0)
        if self._should_use_prompt_toolkit():
            return await self._read_prompt_toolkit_line("", complete_slash=False)
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

    def _should_use_prompt_toolkit(self) -> bool:
        if self._reader_started:
            return False
        if self._enable_prompt_toolkit is False:
            return False
        return should_enable_prompt_toolkit(prompt_toolkit_available=self._enable_prompt_toolkit)

    async def _read_prompt_toolkit_line(self, prompt: str, *, complete_slash: bool) -> str:
        try:
            from prompt_toolkit import PromptSession
            from prompt_toolkit.patch_stdout import patch_stdout
        except Exception:
            print(prompt, end="", flush=True)
            self._ensure_reader()
            item = await self._queue.get()
            if item is self._EOF:
                raise EOFError
            return item

        if complete_slash:
            if self._main_prompt_session is None:
                self._main_prompt_session = PromptSession(
                    completer=create_slash_command_completer(),
                    complete_while_typing=True,
                    mouse_support=prompt_mouse_support_enabled(),
                    **slash_prompt_session_kwargs(),
                )
            session = self._main_prompt_session
        else:
            if self._runtime_prompt_session is None:
                self._runtime_prompt_session = PromptSession()
            session = self._runtime_prompt_session

        with patch_stdout():
            prompt_message = slash_prompt_message(prompt) if complete_slash else prompt
            return await session.prompt_async(prompt_message)

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


def _is_stop_command(user_input: str) -> bool:
    return sanitize_text(user_input).strip().lower() == "/stop"


def _is_tty(stream) -> bool:
    isatty = getattr(stream, "isatty", None)
    if not callable(isatty):
        return False
    try:
        return bool(isatty())
    except Exception:
        return False
