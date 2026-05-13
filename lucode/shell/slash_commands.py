from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from lucode.shell.input_adapter import RuntimeCommandSession
from lucode.shell.runtime_env import turn_timeout_seconds
from lucode.shell.turn_display import (
    format_turn_error,
    is_exit_command,
    is_new_command,
    is_stop_command,
)
from planning.planner import format_plan_preview, preview_plan
from runtime.commands.invocation import resolve_mcp_prompt_invocation
from runtime.config.cli import (
    apply_writable_config_command,
    parse_writable_config_command,
    render_diff_command,
    render_readonly_command,
    render_status_command,
)
from runtime.context.semantic_compaction import compact_messages_tiered
from runtime.kernel.session import create_token_logger_hooks
from runtime.sessions import render_resume_preview, render_session_list
from runtime.ui.welcome import render_welcome_dashboard


@dataclass
class SlashCommandResult:
    handled: bool = False
    should_exit: bool = False
    reset_recent_turns: bool = False
    resumed_recent_turns: list[dict[str, str]] | None = None
    resumed_session_summary: str | None = None
    session_id: str | None = None
    expanded_user_input: str | None = None
    expanded_from_command: str | None = None


async def handle_slash_command(
    user_input: str,
    *,
    model_registry,
    runtime_settings,
    console,
    app_home: Path,
    project_root: Path,
    workspace_context,
    use_color: bool | None,
    show_logo: bool,
    started_mcp_ids: list[str],
    checkpoint_manager,
    session_store=None,
    current_session_id: str | None = None,
) -> SlashCommandResult:
    if is_exit_command(user_input):
        print("已退出。")
        return SlashCommandResult(handled=True, should_exit=True)

    if is_stop_command(user_input):
        print("已停止当前输入，你可以重新输入新的问题。")
        return SlashCommandResult(handled=True)

    if is_new_command(user_input):
        new_session_id = session_store.start_session() if session_store is not None else None
        print("已创建新对话，历史上下文已清空。")
        if new_session_id:
            print(f"当前会话：{new_session_id}")
        print(render_welcome_dashboard(workspace_context, runtime_settings, use_color=use_color, show_logo=show_logo))
        return SlashCommandResult(handled=True, reset_recent_turns=True, session_id=new_session_id)

    if not user_input:
        return SlashCommandResult()

    lower_input = user_input.lower()
    if lower_input == "/resume" or lower_input.startswith("/resume "):
        return await _handle_resume_command(
            user_input,
            session_store=session_store,
            current_session_id=current_session_id,
            model_registry=model_registry,
            runtime_settings=runtime_settings,
        )

    parsed_config = parse_writable_config_command(user_input)
    if parsed_config is not None or user_input.lower().startswith(("/mode ", "/refiner ")):
        output, updated = apply_writable_config_command(
            user_input,
            app_home / ".env",
            runtime_settings,
            workspace_context=workspace_context,
        )
        print(output)
        if updated and parsed_config and parsed_config[0] == "mode":
            print(render_welcome_dashboard(workspace_context, runtime_settings, use_color=use_color, show_logo=show_logo))
        return SlashCommandResult(handled=True)

    if user_input.lower() == "/status":
        print(
            render_status_command(
                project_root,
                runtime_settings,
                started_mcp_ids=started_mcp_ids,
                rollback_status=checkpoint_manager.render_status(),
            )
        )
        return SlashCommandResult(handled=True)

    if user_input.lower().startswith("/diff"):
        print(render_diff_command(project_root))
        return SlashCommandResult(handled=True)

    if user_input.lower() == "/rollback":
        result = checkpoint_manager.rollback_last_turn()
        print(result.message)
        return SlashCommandResult(handled=True)

    mcp_prompt_invocation = resolve_mcp_prompt_invocation(user_input, workspace_context)
    if mcp_prompt_invocation is not None:
        print(f"已展开 MCP Prompt：{mcp_prompt_invocation.spec.command}")
        return SlashCommandResult(
            expanded_user_input=mcp_prompt_invocation.expanded_input,
            expanded_from_command=user_input,
        )

    config_output = render_readonly_command(user_input, runtime_settings, workspace_context)
    if config_output:
        print(config_output)
        return SlashCommandResult(handled=True)

    if user_input.startswith("/plan"):
        await _handle_plan_command(
            user_input,
            model_registry=model_registry,
            runtime_settings=runtime_settings,
            console=console,
        )
        return SlashCommandResult(handled=True)

    return SlashCommandResult()


async def _handle_resume_command(
    user_input: str,
    *,
    session_store,
    current_session_id: str | None,
    model_registry,
    runtime_settings,
) -> SlashCommandResult:
    if session_store is None:
        print("当前运行环境没有启用会话存储。")
        return SlashCommandResult(handled=True)

    selector = user_input.split(maxsplit=1)[1].strip() if len(user_input.split(maxsplit=1)) > 1 else ""
    if not selector or selector.lower() == "list":
        print(render_session_list(session_store))
        return SlashCommandResult(handled=True)

    try:
        session_id = session_store.resolve_session_id(selector)
    except ValueError as exc:
        print(f"恢复失败：{exc}")
        print(render_session_list(session_store))
        return SlashCommandResult(handled=True)

    if not session_id:
        print(f"没有找到会话：{selector}")
        print(render_session_list(session_store))
        return SlashCommandResult(handled=True)

    compacted = await compact_messages_tiered(
        session_store.load_messages(session_id),
        tail_messages=6,
        model_registry=model_registry,
        runtime_settings=runtime_settings,
    )
    turns = compacted.recent_turns
    if not turns:
        print(f"会话没有可恢复消息：{session_id}")
        return SlashCommandResult(handled=True, session_id=current_session_id)

    print(render_resume_preview(session_store, session_id, compacted_context=compacted))
    return SlashCommandResult(
        handled=True,
        resumed_recent_turns=turns,
        resumed_session_summary=compacted.summary,
        session_id=session_id,
    )


async def _handle_plan_command(user_input: str, *, model_registry, runtime_settings, console) -> None:
    plan_input = user_input.removeprefix("/plan").strip()
    if not plan_input:
        print("请在 /plan 后面输入要规划的问题。")
        return

    print("\n正在生成规划预览，不会执行任务...")
    hooks = create_token_logger_hooks()
    try:
        refiner_model_id = (
            runtime_settings.select_model_id(model_registry, "query_refiner")
            if runtime_settings.query_refiner_enabled
            else None
        )
        planner_model_id = runtime_settings.select_model_id(model_registry, "orchestrator")
        session = RuntimeCommandSession(console, timeout_seconds=turn_timeout_seconds())

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
        print(format_turn_error(exc))
    finally:
        hooks.print_summary()
