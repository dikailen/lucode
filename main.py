import sys
import asyncio
import os
from pathlib import Path
from dotenv import load_dotenv
from catalog_system.refresher import refresh_catalogs
from catalog_system.model_catalog import ModelRegistry
from lucode.shell.input_adapter import RuntimeCommandSession, StdinConsoleAdapter
from lucode.shell.runtime_env import (
    runtime_logo_enabled as _runtime_logo_enabled,
    runtime_verbose_enabled as _runtime_verbose_enabled,
    turn_timeout_seconds,
)
from lucode.shell.turn_display import (
    format_turn_error as _format_turn_error,
    is_exit_command as _is_exit_command,
    is_new_command as _is_new_command,
    is_stop_command as _is_stop_command,
    should_print_final_output as _should_print_final_output,
    turn_status_label as _turn_status_label,
)
from runtime.config.workspace import discover_workspace_context
from runtime.config.settings import RuntimeSettings
from runtime.ui.welcome import render_welcome_dashboard

# 当前 main.py 所在目录，也就是项目根目录。
BASE_DIR = Path(__file__).resolve().parent

# .env 仍作为兼容设置读取；主 Provider 配置优先使用 .lucode/config.toml，
# API key 优先保存到用户级 auth.json。设置 LUCODE_DISABLE_DOTENV=1 可测试禁用兼容层。
if str(os.environ.get("LUCODE_DISABLE_DOTENV") or "").strip().lower() not in {"1", "true", "yes", "on"}:
    load_dotenv(BASE_DIR / ".env")

# Windows PowerShell 有时默认使用 GBK 编码。
# 这里把 Python 标准输出改成 UTF-8，避免中文、特殊符号打印时报编码错误。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stdin, "reconfigure"):
    sys.stdin.reconfigure(encoding="utf-8")


def runtime_verbose_enabled() -> bool:
    return _runtime_verbose_enabled()


def runtime_logo_enabled() -> bool:
    return _runtime_logo_enabled()


def _turn_timeout_seconds() -> float | None:
    return turn_timeout_seconds()


def create_token_logger_hooks(verbose: bool | None = None):
    from runtime.kernel.session import create_token_logger_hooks as _create_token_logger_hooks

    return _create_token_logger_hooks(verbose=verbose)


def _approval_prompt() -> str:
    from runtime.agent.approval import approval_prompt

    return approval_prompt()


def _approval_tool_rule(tool_name: str) -> str:
    from runtime.agent.approval import approval_tool_rule

    return approval_tool_rule(tool_name)


def _format_tool_arguments(arguments):
    from runtime.agent.approval import format_tool_arguments

    return format_tool_arguments(arguments)


def _format_tool_preview(tool_name: str, arguments: str | None) -> str:
    from runtime.agent.approval import format_tool_preview

    return format_tool_preview(tool_name, arguments)


def _patch_preview_lines(patch_text: str, max_lines: int = 18, max_chars: int = 1800) -> list[str]:
    from runtime.agent.approval import patch_preview_lines

    return patch_preview_lines(patch_text, max_lines=max_lines, max_chars=max_chars)


async def _run_agent_once(agent, run_input, hooks, max_turns=20):
    from runtime.agent.runner import run_agent_once

    return await run_agent_once(agent, run_input, hooks, max_turns=max_turns)


def _streaming_enabled() -> bool:
    from runtime.agent.runner import streaming_enabled

    return streaming_enabled()


def _stream_delta_text(event) -> str:
    from runtime.agent.runner import stream_delta_text

    return stream_delta_text(event)


async def main():
    workspace_override = os.environ.get("LUCODE_WORKSPACE_ROOT")
    workspace_context = discover_workspace_context(
        BASE_DIR,
        cwd=Path(workspace_override) if workspace_override else None,
    )
    project_root = workspace_context.workspace_root
    quarantine_dir = project_root / ".agent_quarantine"
    os.environ["LUCODE_APP_HOME"] = str(workspace_context.app_home)
    os.environ["LUCODE_USER_HOME"] = str(workspace_context.user_home)
    os.environ["LUCODE_WORKSPACE_ROOT"] = str(workspace_context.workspace_root)
    refresh_catalogs(BASE_DIR)
    model_registry = ModelRegistry()
    runtime_settings = RuntimeSettings.from_env()
    console = StdinConsoleAdapter()

    use_color = bool(getattr(sys.stdout, "isatty", lambda: False)()) and not os.environ.get("NO_COLOR")
    show_logo = runtime_logo_enabled()
    print(render_welcome_dashboard(workspace_context, runtime_settings, use_color=use_color, show_logo=show_logo))

    from lucode.shell.chat_loop import chat_loop as shell_chat_loop

    await shell_chat_loop(
        model_registry,
        quarantine_dir,
        runtime_settings,
        console,
        app_home=BASE_DIR,
        project_root=project_root,
        workspace_context=workspace_context,
        use_color=use_color,
    )


async def chat_loop(*args, **kwargs):
    from lucode.shell.chat_loop import chat_loop as shell_chat_loop

    return await shell_chat_loop(*args, **kwargs)


if __name__ == "__main__":
    # 因为 Runner.run 是异步函数，所以需要用 asyncio.run(...) 启动。
    asyncio.run(main())
