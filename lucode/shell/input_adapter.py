from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import threading
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Sequence

from runtime.commands.completion import (
    create_slash_command_completer,
    slash_auto_suggestion,
    slash_prompt_message,
    slash_prompt_session_kwargs,
)
from runtime.common.text_utils import sanitize_text
from runtime.ui.theme import prompt_toolkit_prompt_style, resolve_ui_theme


_STD_INPUT_HANDLE = -10
_STD_OUTPUT_HANDLE = -11


def _null_context():
    return nullcontext()


@dataclass
class RuntimeTurnResult:
    final_output: str
    stopped: bool = False


@dataclass(frozen=True)
class ConsoleChoice:
    command: str
    display: str
    meta: str = ""


@dataclass(frozen=True)
class ConsoleFormField:
    name: str
    label: str
    value: str = ""
    required: bool = False
    secret: bool = False
    help: str = ""


@dataclass(frozen=True)
class ConsoleFormResult:
    action: str
    values: dict[str, str]


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
    return _is_interactive_terminal_stream(stdin, sys.stdin, _STD_INPUT_HANDLE) and _is_interactive_terminal_stream(
        stdout,
        sys.stdout,
        _STD_OUTPUT_HANDLE,
    )


def prompt_toolkit_input_diagnostics(
    stdin=None,
    stdout=None,
    *,
    prompt_toolkit_available: bool | None = None,
    env=None,
) -> dict[str, object]:
    env = os.environ if env is None else env
    if prompt_toolkit_available is None:
        prompt_toolkit_available = importlib.util.find_spec("prompt_toolkit") is not None
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    return {
        "prompt_toolkit_available": bool(prompt_toolkit_available),
        "disabled_by_env": str(env.get("LUCODE_DISABLE_PROMPT_TOOLKIT", "")).strip().lower()
        in {"1", "true", "yes", "on"},
        "stdin_isatty": _is_tty(stdin),
        "stdout_isatty": _is_tty(stdout),
        "stdin_windows_console": _windows_console_stream_available(stdin, sys.stdin, _STD_INPUT_HANDLE),
        "stdout_windows_console": _windows_console_stream_available(stdout, sys.stdout, _STD_OUTPUT_HANDLE),
        "enabled": should_enable_prompt_toolkit(
            stdin,
            stdout,
            prompt_toolkit_available=prompt_toolkit_available,
            env=env,
        ),
    }


def prompt_mouse_support_enabled(env=None) -> bool:
    env = os.environ if env is None else env
    value = str(env.get("LUCODE_PROMPT_MOUSE_SUPPORT", "")).strip().lower()
    return value in {"1", "true", "yes", "on"}


def choice_mouse_support_enabled(env=None) -> bool:
    env = os.environ if env is None else env
    value = str(env.get("LUCODE_TUNER_MOUSE_SUPPORT", "")).strip().lower()
    return value in {"1", "true", "yes", "on"}


def fullscreen_form_mouse_support_enabled(env=None) -> bool:
    env = os.environ if env is None else env
    value = str(env.get("LUCODE_FORM_MOUSE_SUPPORT", "1")).strip().lower()
    return value not in {"0", "false", "no", "off"}


def fullscreen_form_enabled(env=None) -> bool:
    env = os.environ if env is None else env
    value = str(env.get("LUCODE_DISABLE_FULLSCREEN_FORMS", "")).strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return False
    mode = str(env.get("LUCODE_CONNECT_FORM", "")).strip().lower()
    return mode not in {"light", "lite", "simple", "basic"}


def fullscreen_form_style_rules() -> dict[str, str]:
    return {
        "form.root": "bg:#05070d #d6e5ff",
        "dialog": "bg:#07111f #d6e5ff",
        "dialog.body": "bg:#07111f #d6e5ff",
        "frame": "bg:#07111f #d6e5ff",
        "frame.border": "#1f7aff bg:#07111f",
        "frame.label": "#59d7ff bg:#07111f bold",
        "shadow": "bg:#02040a",
        "form.message": "#d6e5ff bg:#07111f",
        "form.label": "#7db4ff bg:#07111f bold",
        "form.required": "#59d7ff bg:#07111f bold",
        "form.help": "#7b8aa3 bg:#07111f",
        "form.footer": "#7db4ff bg:#07111f",
        "form.input": "bg:#0d1b2e #f2f7ff",
        "form.input.prompt": "#59d7ff bg:#0d1b2e bold",
        "form.button": "bg:#10223a #9fb7d9",
        "form.button.focused": "bg:#1f7aff #ffffff bold",
        "button": "bg:#10223a #9fb7d9",
        "button.focused": "bg:#1f7aff #ffffff bold",
        "button.arrow": "#59d7ff",
        "button.text": "bold",
    }


class StdinConsoleAdapter:
    """Single-reader stdin adapter so runtime stop/approval won't compete for input()."""

    interactive = True
    _EOF = object()

    def __init__(self, enable_prompt_toolkit: bool | None = None, output_controller=None):
        self._deferred_lines = []
        self._enable_prompt_toolkit = enable_prompt_toolkit
        self.output_controller = output_controller
        self._loop = None
        self._queue = None
        self._reader_started = False
        self._reader_lock = threading.Lock()
        self._main_prompt_session = None
        self._runtime_prompt_session = None
        self._choice_prompt_session = None
        self._secret_prompt_session = None
        self._prompt_style = _current_prompt_style()

    async def read_line(self, prompt: str = "\n你：") -> str:
        if self._deferred_lines:
            return self._deferred_lines.pop(0)
        with self._temporary_interactive_phase("main input"):
            self._refresh_prompt_theme()
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
        with self._temporary_interactive_phase("runtime input"):
            if self._should_use_prompt_toolkit():
                return await self._read_prompt_toolkit_line("", complete_slash=False)
            self._ensure_reader()
            item = await self._queue.get()
            if item is self._EOF:
                raise EOFError
            return item

    async def read_runtime_control_line(self) -> str:
        """Read control input during a running turn without prompt_toolkit repaint."""

        if self._deferred_lines:
            return self._deferred_lines.pop(0)
        self._ensure_reader()
        item = await self._queue.get()
        if item is self._EOF:
            raise EOFError
        return item

    async def read_choice_line(
        self,
        prompt: str,
        choices: Sequence[ConsoleChoice],
        *,
        bottom_toolbar: str = "",
        reserve_space_for_menu: int = 10,
    ) -> str:
        if self._deferred_lines:
            return self._deferred_lines.pop(0)
        with self._temporary_interactive_phase("choice input"):
            self._refresh_prompt_theme()
            if self._should_use_prompt_toolkit():
                try:
                    return await self._read_prompt_toolkit_choice(
                        prompt,
                        choices,
                        bottom_toolbar=bottom_toolbar,
                        reserve_space_for_menu=reserve_space_for_menu,
                    )
                except Exception:
                    pass
            print(prompt, end="", flush=True)
            self._ensure_reader()
            item = await self._queue.get()
            if item is self._EOF:
                raise EOFError
            return item

    async def read_secret_line(self, prompt: str) -> str:
        if self._deferred_lines:
            return self._deferred_lines.pop(0)
        with self._temporary_interactive_phase("secret input"):
            self._refresh_prompt_theme()
            if self._should_use_prompt_toolkit():
                try:
                    return await self._read_prompt_toolkit_secret(prompt)
                except Exception:
                    pass
            print(prompt, end="", flush=True)
            self._ensure_reader()
            item = await self._queue.get()
            if item is self._EOF:
                raise EOFError
            return item

    async def read_form(
        self,
        *,
        title: str,
        fields: Sequence[ConsoleFormField],
        actions: Sequence[ConsoleChoice],
        message: str = "",
        footer: str = "",
    ) -> ConsoleFormResult | None:
        if self._deferred_lines:
            return None
        if not fullscreen_form_enabled():
            return None
        if not self._should_use_prompt_toolkit():
            return None
        with self._temporary_interactive_phase("form input"):
            try:
                return await self._read_prompt_toolkit_form(
                    title=title,
                    fields=fields,
                    actions=actions,
                    message=message,
                    footer=footer,
                )
            except Exception:
                return None

    def defer(self, line: str) -> None:
        if line is None:
            return
        self._deferred_lines.append(line)

    def runtime_control_input_enabled(self) -> bool:
        """Whether a running turn may start a background stdin control reader."""

        return not self._should_use_prompt_toolkit()

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

    def _temporary_interactive_phase(self, reason: str):
        controller = getattr(self, "output_controller", None)
        if controller is None or not hasattr(controller, "temporary_phase"):
            return _null_context()
        try:
            from runtime.ui.output_controller import OutputPhase

            snapshot = controller.snapshot() if hasattr(controller, "snapshot") else None
            if getattr(snapshot, "phase", None) == OutputPhase.APPROVAL_WAITING:
                return _null_context()
            return controller.temporary_phase(OutputPhase.INTERACTIVE_INPUT, reason=reason)
        except Exception:
            return _null_context()

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
                self._main_prompt_session = _create_main_prompt_session(PromptSession, prompt_style=self._prompt_style)
            session = self._main_prompt_session
        else:
            if self._runtime_prompt_session is None:
                self._runtime_prompt_session = PromptSession()
            session = self._runtime_prompt_session

        with patch_stdout():
            prompt_message = slash_prompt_message(prompt) if complete_slash else prompt
            if complete_slash:
                return await session.prompt_async(prompt_message, style=_prompt_style_dict(self._prompt_style))
            return await session.prompt_async(prompt_message)

    async def _read_prompt_toolkit_choice(
        self,
        prompt: str,
        choices: Sequence[ConsoleChoice],
        *,
        bottom_toolbar: str,
        reserve_space_for_menu: int,
    ) -> str:
        try:
            from prompt_toolkit import PromptSession
            from prompt_toolkit.application import get_app
            from prompt_toolkit.completion import Completer, Completion
            from prompt_toolkit.key_binding import KeyBindings
            from prompt_toolkit.patch_stdout import patch_stdout
            from prompt_toolkit.shortcuts.prompt import CompleteStyle
            from prompt_toolkit.styles import Style
        except Exception:
            print(prompt, end="", flush=True)
            self._ensure_reader()
            item = await self._queue.get()
            if item is self._EOF:
                raise EOFError
            return item

        class ChoiceCompleter(Completer):
            def get_completions(self, document, complete_event):
                del complete_event
                query = document.text_before_cursor.strip().lower()
                start_position = -len(document.text_before_cursor)
                for choice in choices:
                    haystack = f"{choice.command} {choice.display} {choice.meta}".lower()
                    if query and query not in haystack:
                        continue
                    yield Completion(
                        choice.command,
                        start_position=start_position,
                        display=choice.display,
                        display_meta=choice.meta,
                    )

        bindings = create_choice_prompt_key_bindings()

        if self._choice_prompt_session is None:
            self._choice_prompt_session = PromptSession()

        def start_choice_completion() -> None:
            get_app().current_buffer.start_completion(select_first=True)

        style = Style.from_dict(
            {
                "prompt": self._prompt_style,
                "completion-menu.completion": "bg:#202020 #d0d0d0",
                "completion-menu.completion.current": "bg:#005fff #ffffff bold",
                "scrollbar.background": "bg:#303030",
                "scrollbar.button": "bg:#707070",
            }
        )
        with patch_stdout():
            return await self._choice_prompt_session.prompt_async(
                [("class:prompt", prompt)],
                completer=ChoiceCompleter(),
                complete_while_typing=True,
                complete_style=CompleteStyle.COLUMN,
                key_bindings=bindings,
                mouse_support=choice_mouse_support_enabled(),
                bottom_toolbar=bottom_toolbar,
                reserve_space_for_menu=reserve_space_for_menu,
                style=style,
                pre_run=start_choice_completion,
            )

    async def _read_prompt_toolkit_secret(self, prompt: str) -> str:
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

        if self._secret_prompt_session is None:
            self._secret_prompt_session = PromptSession(is_password=True)
        with patch_stdout():
            return await self._secret_prompt_session.prompt_async([("class:prompt", prompt)], style=_prompt_style_dict(self._prompt_style))

    def _refresh_prompt_theme(self) -> None:
        self._prompt_style = _current_prompt_style()

    async def _read_prompt_toolkit_form(
        self,
        *,
        title: str,
        fields: Sequence[ConsoleFormField],
        actions: Sequence[ConsoleChoice],
        message: str,
        footer: str,
    ) -> ConsoleFormResult:
        try:
            from prompt_toolkit.application import Application, get_app
            from prompt_toolkit.key_binding import KeyBindings
            from prompt_toolkit.layout.containers import HSplit
            from prompt_toolkit.layout import Layout, Window, WindowAlign
            from prompt_toolkit.layout.controls import FormattedTextControl
            from prompt_toolkit.patch_stdout import patch_stdout
            from prompt_toolkit.styles import Style
            from prompt_toolkit.mouse_events import MouseEventType
            from prompt_toolkit.utils import get_cwidth
            from prompt_toolkit.widgets import Box, Dialog, Label, TextArea
        except Exception:
            raise

        class FormButton:
            def __init__(self, text: str, command: str, handler, width: int) -> None:
                self.text = text
                self.command = command
                self.handler = handler
                self.width = width
                self.control = FormattedTextControl(
                    self._get_text_fragments,
                    key_bindings=self._get_key_bindings(),
                    focusable=True,
                )
                self.window = Window(
                    self.control,
                    align=WindowAlign.CENTER,
                    height=1,
                    width=width,
                    style=self._get_style,
                    dont_extend_width=False,
                    dont_extend_height=True,
                )

            def _has_focus(self) -> bool:
                try:
                    return get_app().layout.has_focus(self)
                except Exception:
                    return False

            def _get_style(self) -> str:
                return "class:form.button.focused" if self._has_focus() else "class:form.button"

            def _get_text_fragments(self):
                style = "class:form.button.focused" if self._has_focus() else "class:form.button"
                text_width = get_cwidth(self.text)
                pad = max(2, self.width - text_width)
                left = pad // 2
                right = pad - left

                def mouse_handler(mouse_event):
                    if mouse_event.event_type == MouseEventType.MOUSE_UP:
                        self.handler()

                return [(style, f"{' ' * left}{self.text}{' ' * right}", mouse_handler)]

            def _get_key_bindings(self) -> KeyBindings:
                bindings = KeyBindings()

                @bindings.add(" ")
                @bindings.add("enter")
                def _accept(event):
                    del event
                    self.handler()

                return bindings

            def __pt_container__(self):
                return self.window

        text_areas = []
        rows = []
        for field in fields:
            marker = " *" if field.required else ""
            area = TextArea(
                text=str(field.value or ""),
                multiline=False,
                password=field.secret,
                height=1,
                prompt=[("class:form.input.prompt", "> ")],
                focus_on_click=True,
                style="class:form.input",
            )
            text_areas.append((field, area))
            label = Label(
                [
                    ("class:form.label", field.label),
                    ("class:form.required", marker),
                ]
            )
            if field.help:
                rows.append(HSplit([label, area, Label(field.help, style="class:form.help")], padding=0))
            else:
                rows.append(HSplit([label, area], padding=0))

        app_holder = {}

        def finish(action: str) -> None:
            values = {field.name: area.text for field, area in text_areas}
            app_holder["app"].exit(result=ConsoleFormResult(action=action, values=values))

        buttons = []
        for action in actions:
            width = max(16, min(28, get_cwidth(str(action.display)) + 8))
            buttons.append(
                FormButton(
                    action.display,
                    command=action.command,
                    handler=lambda command=action.command: finish(command),
                    width=width,
                )
            )
        focusables = [area for _, area in text_areas] + buttons

        body_items = []
        if message:
            body_items.append(Label(message, style="class:form.message"))
        body_items.extend(rows)
        if footer:
            body_items.append(Label(footer, style="class:form.footer"))

        dialog = Dialog(
            title=title,
            body=HSplit(body_items, padding=1),
            buttons=buttons,
            width=90,
            with_background=True,
        )
        root_container = Box(dialog, padding=1, style="class:form.root")
        layout = Layout(root_container, focused_element=text_areas[0][1] if text_areas else buttons[0])

        bindings = KeyBindings()

        def move_focus(offset: int) -> None:
            current = layout.current_window
            widgets = [item.window if hasattr(item, "window") else item for item in focusables]
            try:
                index = widgets.index(current)
            except ValueError:
                index = 0
            target = focusables[(index + offset) % len(focusables)]
            layout.focus(target)

        @bindings.add("tab")
        @bindings.add("down")
        def _focus_next(event):
            del event
            move_focus(1)

        @bindings.add("s-tab")
        @bindings.add("up")
        def _focus_previous(event):
            del event
            move_focus(-1)

        @bindings.add("c-s")
        def _save_default(event):
            del event
            finish("save_default")

        @bindings.add("c-o")
        def _save_only(event):
            del event
            finish("save_only")

        @bindings.add("c-p")
        def _change_provider(event):
            del event
            finish("change_provider")

        @bindings.add("escape")
        @bindings.add("c-c")
        def _cancel(event):
            del event
            finish("cancel")

        style = Style.from_dict(fullscreen_form_style_rules())
        app = Application(
            layout=layout,
            key_bindings=bindings,
            full_screen=True,
            mouse_support=fullscreen_form_mouse_support_enabled(),
            style=style,
        )
        app_holder["app"] = app
        with patch_stdout():
            return await app.run_async()

    def _reader_loop(self) -> None:
        while True:
            line = sys.stdin.readline()
            if line == "":
                self._loop.call_soon_threadsafe(self._queue.put_nowait, self._EOF)
                return
            self._loop.call_soon_threadsafe(self._queue.put_nowait, line.rstrip("\r\n"))


class RuntimeCommandSession:
    """Watch stdin while a turn is running so /stop can cancel in-flight work."""

    def __init__(self, console, timeout_seconds: float | None = None, output_controller=None):
        self.console = console
        self.timeout_seconds = timeout_seconds if timeout_seconds and timeout_seconds > 0 else None
        self.output_controller = output_controller
        self._approval_future = None
        self._approval_requested = None
        self.approval_waiting = False
        self._approval_phase_token = None

    async def run(self, work_coro):
        if callable(work_coro):
            work_coro = work_coro()
        work_task = asyncio.create_task(work_coro)
        interactive = bool(getattr(self.console, "interactive", False))
        control_input_enabled = interactive and self._control_input_enabled()
        input_task = asyncio.create_task(self._read_control_line()) if control_input_enabled else None
        approval_task = None
        if interactive:
            self._approval_requested = asyncio.Event()
            approval_task = asyncio.create_task(self._approval_requested.wait())
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
                if approval_task is not None:
                    wait_set.add(approval_task)
                if timeout_task is not None:
                    wait_set.add(timeout_task)
                done, _ = await asyncio.wait(
                    wait_set,
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if approval_task is not None and approval_task in done:
                    if self._approval_requested is not None:
                        self._approval_requested.clear()
                    if self._approval_future is not None and not self._approval_future.done():
                        self.approval_waiting = True
                        self._enter_approval_phase()
                        if input_task is not None and not input_task.done():
                            input_task.cancel()
                            await asyncio.gather(input_task, return_exceptions=True)
                        input_task = asyncio.create_task(self._read_approval_line())
                    approval_task = asyncio.create_task(self._approval_requested.wait())
                    continue

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
                    self._mark_failed("turn timeout")
                    return RuntimeTurnResult(
                        final_output="本轮执行超过超时时间，已自动中断。你可以把任务拆小一点，或稍后重试。",
                        stopped=True,
                    )

                line = sanitize_text(await input_task).lstrip("\ufeff").strip() if input_task is not None else ""

                if self._approval_future is not None and not self._approval_future.done():
                    if _is_stop_command(line):
                        self._approval_future.set_result("")
                        self.approval_waiting = False
                        self._restore_approval_phase()
                        await _cancel_task_without_blocking(work_task)
                        self._mark_failed("stopped")
                        return RuntimeTurnResult(
                            final_output="已收到 /stop，本轮执行已中断。你可以直接重新输入新的问题。",
                            stopped=True,
                        )
                    self._approval_future.set_result(line)
                    self.approval_waiting = False
                    self._restore_approval_phase()
                elif _is_stop_command(line):
                    await _cancel_task_without_blocking(work_task)
                    self._mark_failed("stopped")
                    return RuntimeTurnResult(
                        final_output="已收到 /stop，本轮执行已中断。你可以直接重新输入新的问题。",
                        stopped=True,
                    )
                elif self._should_drop_orphan_approval_token(line):
                    pass
                elif line:
                    self.console.defer(line)

                if control_input_enabled and input_task is not None:
                    input_task = asyncio.create_task(self._read_control_line())
        finally:
            self._restore_approval_phase()
            if approval_task and not approval_task.done():
                approval_task.cancel()
                await asyncio.gather(approval_task, return_exceptions=True)
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
        if self._approval_requested is not None:
            self._approval_requested.set()
        try:
            answer = await self._approval_future
            return sanitize_text(answer).strip().lower()
        finally:
            self._approval_future = None
            self.approval_waiting = False
            self._restore_approval_phase()

    async def _read_control_line(self) -> str:
        reader = getattr(self.console, "read_runtime_control_line", None)
        if callable(reader):
            return await reader()
        return await self.console.read_runtime_line()

    def _control_input_enabled(self) -> bool:
        checker = getattr(self.console, "runtime_control_input_enabled", None)
        if callable(checker):
            try:
                return bool(checker())
            except Exception:
                return False
        return True

    def _should_drop_orphan_approval_token(self, line: str) -> bool:
        return not self.approval_waiting and _is_orphan_approval_token(line)

    def _enter_approval_phase(self) -> None:
        if self._approval_phase_token is not None:
            return
        controller = getattr(self, "output_controller", None)
        if controller is None or not hasattr(controller, "push_phase"):
            return
        try:
            from runtime.ui.output_controller import OutputPhase

            self._approval_phase_token = controller.push_phase(OutputPhase.APPROVAL_WAITING, reason="approval waiting")
        except Exception:
            self._approval_phase_token = None

    def _restore_approval_phase(self) -> None:
        if self._approval_phase_token is None:
            return
        controller = getattr(self, "output_controller", None)
        token = self._approval_phase_token
        self._approval_phase_token = None
        if controller is not None and hasattr(controller, "restore"):
            try:
                controller.restore(token)
            except Exception:
                pass

    def _mark_failed(self, reason: str) -> None:
        controller = getattr(self, "output_controller", None)
        if controller is not None and hasattr(controller, "enter_failed"):
            try:
                controller.enter_failed(reason)
            except Exception:
                pass

    async def _read_approval_line(self) -> str:
        read_choice_line = getattr(self.console, "read_choice_line", None)
        if callable(read_choice_line):
            return await read_choice_line(
                "审批> ",
                _approval_choices(),
                bottom_toolbar="↑↓ 选择，Enter 确认；y/n 可直接输入；/stop 中断本轮",
                reserve_space_for_menu=7,
            )
        return await self.console.read_runtime_line()


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


def create_choice_prompt_key_bindings():
    from prompt_toolkit.key_binding import KeyBindings

    bindings = KeyBindings()

    @bindings.add("q")
    def _exit_on_empty_q(event):
        buffer = event.current_buffer
        if buffer.text:
            buffer.insert_text("q")
            return
        buffer.insert_text("q")
        buffer.validate_and_handle()

    @bindings.add("j")
    def _choice_next(event):
        buffer = event.current_buffer
        if buffer.text:
            buffer.insert_text("j")
            return
        try:
            buffer.complete_next()
        except Exception:
            pass

    @bindings.add("k")
    def _choice_previous(event):
        buffer = event.current_buffer
        if buffer.text:
            buffer.insert_text("k")
            return
        try:
            buffer.complete_previous()
        except Exception:
            pass

    return bindings


def _current_prompt_style() -> str:
    return prompt_toolkit_prompt_style(resolve_ui_theme())


def _prompt_style_dict(prompt_style: str):
    return slash_prompt_session_kwargs(prompt_style=prompt_style).get("style")


def _approval_choices() -> list[ConsoleChoice]:
    return [
        ConsoleChoice("y", "允许一次", "只批准当前这一次工具调用"),
        ConsoleChoice("n", "拒绝", "不执行当前工具调用"),
        ConsoleChoice("session", "本会话允许同一工具", "后续同一工具自动批准"),
        ConsoleChoice("rule", "本会话允许同类工具", "同类工具本轮自动批准"),
        ConsoleChoice("edit", "让模型改方案", "不执行，要求模型换更安全方案"),
    ]


def _is_stop_command(user_input: str) -> bool:
    return sanitize_text(user_input).strip().lower() == "/stop"


def _is_orphan_approval_token(user_input: str) -> bool:
    return sanitize_text(user_input).strip().lower() in {
        "0",
        "1",
        "2",
        "3",
        "4",
        "all",
        "deny",
        "e",
        "edit",
        "n",
        "no",
        "o",
        "once",
        "r",
        "reject",
        "rule",
        "s",
        "session",
        "y",
        "yes",
    }


def _is_tty(stream) -> bool:
    isatty = getattr(stream, "isatty", None)
    if not callable(isatty):
        return False
    try:
        return bool(isatty())
    except Exception:
        return False


def _is_interactive_terminal_stream(stream, canonical_stream, std_handle_id: int) -> bool:
    if _is_tty(stream):
        return True
    return _windows_console_stream_available(stream, canonical_stream, std_handle_id)


def _windows_console_stream_available(stream, canonical_stream, std_handle_id: int) -> bool:
    if os.name != "nt" or stream is not canonical_stream:
        return False
    return _windows_console_handle_available(std_handle_id)


def _windows_console_handle_available(std_handle_id: int) -> bool:
    try:
        import ctypes

        handle = ctypes.windll.kernel32.GetStdHandle(std_handle_id)
        if handle in (0, -1):
            return False
        mode = ctypes.c_uint()
        return bool(ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode)))
    except Exception:
        return False


def _create_main_prompt_session(prompt_session_cls, *, prompt_style: str = "ansiblue bold"):
    kwargs = {
        "completer": create_slash_command_completer(),
        "complete_while_typing": True,
        "mouse_support": prompt_mouse_support_enabled(),
        **slash_prompt_session_kwargs(prompt_style=prompt_style),
    }
    try:
        from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
        from prompt_toolkit.history import FileHistory
        from runtime.history.input_history import ensure_main_input_history_path

        kwargs["history"] = FileHistory(str(ensure_main_input_history_path()))
        kwargs["auto_suggest"] = SlashCommandAutoSuggest(AutoSuggestFromHistory())
    except Exception:
        pass
    return prompt_session_cls(**kwargs)


try:
    from prompt_toolkit.auto_suggest import AutoSuggest as _PromptToolkitAutoSuggest
except Exception:
    _PromptToolkitAutoSuggest = object


class SlashCommandAutoSuggest(_PromptToolkitAutoSuggest):
    def __init__(self, fallback):
        self._fallback = fallback

    def get_suggestion(self, buffer, document):
        try:
            from prompt_toolkit.auto_suggest import Suggestion

            text = slash_auto_suggestion(document.text_before_cursor)
            if text:
                return Suggestion(text)
        except Exception:
            pass
        return self._fallback.get_suggestion(buffer, document)
