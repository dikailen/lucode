from __future__ import annotations

from pathlib import Path

from lucode.shell.input_adapter import RuntimeCommandSession
from lucode.shell.runtime_env import runtime_logo_enabled, runtime_verbose_enabled, turn_timeout_seconds
from lucode.shell.slash_commands import handle_slash_command
from lucode.shell.turn_display import (
    format_turn_error,
    should_print_final_output,
    turn_status_label,
)
from runtime.common.conversation import append_recent_turn, compose_recent_context
from runtime.common.text_utils import sanitize_text
from runtime.config.workspace import discover_workspace_context
from runtime.history import HistoryStore
from runtime.kernel import KernelFacade
from runtime.kernel.session import create_token_logger_hooks
from runtime.safety.session_checkpoint import SessionCheckpointManager
from runtime.ui.progress import render_runtime_statusline


DEFAULT_APP_HOME = Path(__file__).resolve().parents[2]


async def chat_loop(
    model_registry,
    quarantine_dir,
    runtime_settings,
    console,
    app_home: Path | None = None,
    project_root: Path | None = None,
    workspace_context=None,
    use_color: bool | None = None,
):
    """Run the interactive command-line chat loop."""

    app_home = (app_home or DEFAULT_APP_HOME).resolve()
    project_root = (project_root or DEFAULT_APP_HOME).resolve()
    show_logo = runtime_logo_enabled()
    if workspace_context is None:
        workspace_context = discover_workspace_context(app_home, cwd=project_root)
    # recent_turns 是一个轻量短期上下文，避免当前会话完全忘记上一轮。
    # 长期记忆/知识图谱后续再接，这里只保留最近几轮文本。
    recent_turns = []
    checkpoint_manager = SessionCheckpointManager(project_root)
    session_store = HistoryStore(workspace_context.workspace_root)
    current_session_id: str | None = None
    resumed_session_summary = ""
    started_mcp_ids: list[str] = []
    last_run_context_summary = ""

    while True:
        try:
            user_input = sanitize_text(await console.read_line()).lstrip("\ufeff").strip()
        except EOFError:
            # 当输入流被关闭时触发，例如从文件或管道读取输入读完了。
            # 手动在命令行聊天时一般不会遇到，这里只是让程序能优雅退出。
            print("\n输入结束，已退出。")
            break

        slash_result = await handle_slash_command(
            user_input,
            model_registry=model_registry,
            runtime_settings=runtime_settings,
            console=console,
            app_home=app_home,
            project_root=project_root,
            workspace_context=workspace_context,
            use_color=use_color,
            show_logo=show_logo,
            started_mcp_ids=started_mcp_ids,
            checkpoint_manager=checkpoint_manager,
            session_store=session_store,
            current_session_id=current_session_id,
            last_run_context_summary=last_run_context_summary,
        )
        if slash_result.session_id:
            current_session_id = slash_result.session_id
        elif slash_result.session_id == "":
            current_session_id = None
        if slash_result.resumed_recent_turns is not None:
            recent_turns = slash_result.resumed_recent_turns
            resumed_session_summary = slash_result.resumed_session_summary or ""
        if slash_result.reset_recent_turns:
            recent_turns = []
            resumed_session_summary = ""
        if slash_result.should_exit:
            break
        if slash_result.expanded_user_input:
            user_input = slash_result.expanded_user_input
        if slash_result.handled or not user_input:
            continue

        # 每一轮都新建 hooks，这样本轮 token 用量会单独统计。
        hooks = create_token_logger_hooks()

        run_input = compose_recent_context(recent_turns, user_input, session_summary=resumed_session_summary)
        checkpoint_manager.begin_turn()
        turn_stopped = False
        kernel_response = None
        try:
            session = RuntimeCommandSession(console, timeout_seconds=turn_timeout_seconds())
            verbose_runtime = runtime_verbose_enabled()

            async def _kernel_work():
                nonlocal kernel_response
                kernel_response = await KernelFacade(workspace_context).run_once(
                    run_input,
                    show_plan=True,
                    approval_session=session,
                    settings=runtime_settings,
                    model_registry=model_registry,
                    hooks=hooks,
                    routing_input=user_input,
                    verbose_runtime=verbose_runtime,
                )
                return kernel_response.final_output

            turn_result = await session.run(_kernel_work)
            started_mcp_ids = kernel_response.mcp_ids_used if kernel_response is not None else []
            last_run_context_summary = (
                str(getattr(kernel_response, "run_context_summary", "") or "") if kernel_response is not None else ""
            )
            final_output = turn_result.final_output
            turn_stopped = turn_result.stopped
        except Exception as exc:
            if _is_max_turns_exceeded(exc):
                final_output = (
                    "本轮任务超过最大工具/模型轮数，已自动停止。"
                    "建议用 /plan 先查看规划，或把任务拆得更具体一点。"
                )
            else:
                final_output = format_turn_error(exc)
        finally:
            checkpoint_manager.complete_turn()

        # 正常流式回答已经逐字显示过，不再把同一份 final_output 复读一遍。
        if should_print_final_output(hooks, final_output):
            print("\n========== Final output ==========")
            print(final_output)
        print(
            render_runtime_statusline(
                runtime_settings.execution_mode,
                started_mcp_ids=started_mcp_ids,
                active=turn_status_label(final_output, stopped=turn_stopped),
            )
        )

        append_recent_turn(recent_turns, "user", user_input)
        append_recent_turn(recent_turns, "assistant", str(final_output), max_chars=800)
        recent_turns = recent_turns[-6:]
        if not current_session_id:
            current_session_id = session_store.start_session(user_input)
        _record_session_turn(
            session_store,
            current_session_id,
            user_input,
            str(final_output),
            execution_mode=runtime_settings.execution_mode,
            stopped=turn_stopped,
            started_mcp_ids=started_mcp_ids,
            run_context_summary=last_run_context_summary,
        )

        # 打印本轮每个 Agent 的 token 汇总。
        hooks.print_summary()


def _is_max_turns_exceeded(exc: Exception) -> bool:
    return exc.__class__.__name__ == "MaxTurnsExceeded"


def _record_session_turn(
    session_store: HistoryStore,
    session_id: str,
    user_input: str,
    final_output: str,
    *,
    execution_mode: str,
    stopped: bool,
    started_mcp_ids: list[str],
    run_context_summary: str = "",
) -> None:
    try:
        assistant_metadata = {
            "execution_mode": execution_mode,
            "stopped": bool(stopped),
            "mcp_ids": list(started_mcp_ids or []),
        }
        context_summary = str(run_context_summary or "").strip()
        if context_summary:
            assistant_metadata["run_context_summary"] = context_summary
        session_store.append_message(
            session_id,
            "user",
            user_input,
            metadata={"execution_mode": execution_mode},
        )
        session_store.append_message(
            session_id,
            "assistant",
            final_output,
            metadata=assistant_metadata,
        )
    except Exception as exc:
        print(f"会话记录写入失败：{exc}")
