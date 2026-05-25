from __future__ import annotations

import inspect
from pathlib import Path

from mcp_servers import apply_full_supervisor_readonly_budget_profile
from runtime.execution.fast_paths import (
    _can_fast_path_git_diff,
    _can_fast_path_git_status,
    _can_fast_path_url_search,
    _run_git_diff_fast_path,
    _run_git_status_fast_path,
    _run_url_search_fast_path,
)
from runtime.execution.failure_memory import _record_flywheel_safely
from runtime.execution.inline_context import _inline_project_file_context, _latest_workspace_context
from runtime.execution.pipeline import PipelineRunState
from runtime.execution.progress import _print_progress_snapshot
from runtime.execution.task_runner import (
    _friendly_task_error,
    _max_turns_for_task,
    _readonly_fast_path_output,
    _record_declared_read_set_context,
    _run_agent_kwargs,
    _task_prompt,
    _task_failure_output,
    _with_verification_report,
)
from runtime.memory.flywheel import FlywheelStore
from runtime.safety.auditor import audit_execution, format_final_report


async def _run_single_agent(
    refined_request: str,
    plan,
    project_root: Path,
    factory,
    hooks,
    run_agent,
    run_state: PipelineRunState,
    flywheel: FlywheelStore,
    execution_mode: str,
    show_plan: bool,
    attempt: int,
) -> tuple[str, object]:
    task = plan.tasks[0]
    _apply_full_supervisor_budget_profile(factory, task, execution_mode)
    approval_policy = _full_mode_approval_policy_for_task(execution_mode, task)
    if show_plan:
        run_state.record_task_started(task)
        _print_progress_snapshot(run_state, mode=execution_mode, attempt=attempt, active=task.title)
    if _can_fast_path_url_search(task):
        output = _run_url_search_fast_path(refined_request, task)
        run_state.record_fast_path_used(task, tool="web_search", action="url_search")
        run_state.record_task_result(task, output)
        if show_plan:
            _print_progress_snapshot(run_state, mode=execution_mode, attempt=attempt, active="已完成")
        _record_flywheel_safely(flywheel, run_state)
        audit = audit_execution(plan, run_state, output)
        return format_final_report(output, audit), audit
    if _can_fast_path_git_status(task):
        output = _run_git_status_fast_path(project_root, task)
        run_state.record_fast_path_used(task, tool="git", action="status")
        _record_single_fast_path_context(run_state, "git", "status", output, task)
        run_state.record_task_result(task, output)
        if show_plan:
            _print_progress_snapshot(run_state, mode=execution_mode, attempt=attempt, active="已完成")
        _record_flywheel_safely(flywheel, run_state)
        audit = audit_execution(plan, run_state, output)
        return format_final_report(output, audit), audit
    if _can_fast_path_git_diff(task):
        output = _run_git_diff_fast_path(project_root, task)
        run_state.record_fast_path_used(task, tool="git", action="diff")
        _record_single_fast_path_context(run_state, "git", "diff", output, task)
        run_state.record_task_result(task, output)
        if show_plan:
            _print_progress_snapshot(run_state, mode=execution_mode, attempt=attempt, active="已完成")
        _record_flywheel_safely(flywheel, run_state)
        audit = audit_execution(plan, run_state, output)
        return format_final_report(output, audit), audit

    fast_path_output = _readonly_fast_path_output(project_root, task, run_context=getattr(run_state, "run_context", None))
    if fast_path_output is not None:
        output = _with_verification_report(project_root, task, fast_path_output, run_state)
        run_state.record_task_result(task, output)
        if show_plan:
            _print_progress_snapshot(run_state, mode=execution_mode, attempt=attempt, active="已完成")
        _record_flywheel_safely(flywheel, run_state)
        audit = audit_execution(plan, run_state, output)
        return format_final_report(output, audit), audit

    workspace_context = _latest_workspace_context(project_root, task)
    inline_context = _inline_project_file_context(
        project_root,
        task,
        refined_request,
        run_context=getattr(run_state, "run_context", None),
    )
    shared_context = _single_shared_context(run_state, task)
    if inline_context:
        agent = factory.create_direct_answer_agent(
            task.model,
            (
                "请只基于用户请求、任务说明和提供的项目文件片段完成只读分析；"
                "不要声称已经调用工具，不要要求用户再粘贴文件。"
            ),
        )
        try:
            run_agent_kwargs = _run_agent_kwargs(run_agent, max_turns=4, approval_policy=approval_policy)
            result = await run_agent(
                agent,
                _task_prompt(
                    refined_request,
                    task.instruction,
                    workspace_context=f"{shared_context}\n\n{workspace_context}\n\n{inline_context}",
                ),
                hooks,
                **run_agent_kwargs,
            )
        except Exception as exc:
            message = _friendly_task_error(exc)
            run_state.record_task_error(task, message)
            if show_plan:
                _print_progress_snapshot(
                    run_state,
                    mode=execution_mode,
                    attempt=attempt,
                    active=f"失败：{task.title}",
                )
            _record_flywheel_safely(flywheel, run_state)
            return _single_agent_failure_result(plan, run_state, task, message)
        output = _with_verification_report(project_root, task, str(result.final_output), run_state)
        _record_declared_read_set_context(
            getattr(run_state, "run_context", None),
            project_root,
            task,
            refined_request=refined_request,
        )
        run_state.record_task_result(task, output)
        if show_plan:
            _print_progress_snapshot(run_state, mode=execution_mode, attempt=attempt, active="已完成")
        _record_flywheel_safely(flywheel, run_state)
        audit = audit_execution(plan, run_state, output)
        return format_final_report(output, audit), audit

    agent = await _create_task_agent(factory, task, execution_mode=execution_mode)
    try:
        run_agent_kwargs = _run_agent_kwargs(
            run_agent,
            max_turns=_max_turns_for_task(task),
            approval_policy=approval_policy,
        )
        result = await run_agent(
            agent,
            _task_prompt(
                refined_request,
                task.instruction,
                workspace_context=workspace_context,
                shared_context=shared_context,
            ),
            hooks,
            **run_agent_kwargs,
        )
    except Exception as exc:
        message = _friendly_task_error(exc)
        run_state.record_task_error(task, message)
        if show_plan:
            _print_progress_snapshot(
                run_state,
                mode=execution_mode,
                attempt=attempt,
                active=f"失败：{task.title}",
            )
        _record_flywheel_safely(flywheel, run_state)
        return _single_agent_failure_result(plan, run_state, task, message)
    output = _with_verification_report(project_root, task, str(result.final_output), run_state)
    _record_declared_read_set_context(
        getattr(run_state, "run_context", None),
        project_root,
        task,
        refined_request=refined_request,
    )
    run_state.record_task_result(task, output)
    if show_plan:
        _print_progress_snapshot(run_state, mode=execution_mode, attempt=attempt, active="已完成")
    _record_flywheel_safely(flywheel, run_state)
    audit = audit_execution(plan, run_state, output)
    return format_final_report(output, audit), audit


def _single_agent_failure_result(plan, run_state: PipelineRunState, task, message: str):
    output = _task_failure_output(task, message)
    audit = audit_execution(plan, run_state, output)
    return format_final_report(output, audit), audit


def _record_single_fast_path_context(run_state, tool: str, action: str, output: str, task) -> None:
    run_context = getattr(run_state, "run_context", None)
    if run_context is None or not hasattr(run_context, "record_tool_output"):
        return
    try:
        run_context.record_tool_output(
            tool=tool,
            action=action,
            summary=output,
            task_id=str(getattr(task, "id", "") or ""),
        )
    except Exception:
        return


def _single_shared_context(run_state, task) -> str:
    run_context = getattr(run_state, "run_context", None)
    if run_context is None or not hasattr(run_context, "render_for_task"):
        return ""
    try:
        return run_context.render_for_task(str(getattr(task, "id", "") or ""))
    except Exception:
        return ""


def _full_mode_approval_policy_for_task(execution_mode: str, task):
    if str(execution_mode or "").strip().lower() != "full":
        return None
    try:
        from runtime.agent.approval_policy import FullModeApprovalPolicy

        return FullModeApprovalPolicy.from_task(task)
    except Exception:
        return None


def _apply_full_supervisor_budget_profile(factory, task, execution_mode: str) -> bool:
    if str(execution_mode or "").strip().lower() != "full":
        return False
    manager = getattr(factory, "mcp_manager", None)
    mcp_ids = [str(mcp_id) for mcp_id in list(getattr(task, "mcp", []) or []) if str(mcp_id or "").strip()]
    return apply_full_supervisor_readonly_budget_profile(manager, mcp_ids)


async def _create_task_agent(factory, task, *, execution_mode: str = ""):
    create_task_agent = factory.create_task_agent
    if _create_task_agent_accepts_execution_mode(create_task_agent):
        return await create_task_agent(task, execution_mode=execution_mode)
    agent = await factory.create_task_agent(task)
    return agent


def _create_task_agent_accepts_execution_mode(create_task_agent) -> bool:
    try:
        parameters = inspect.signature(create_task_agent).parameters
    except (TypeError, ValueError):
        return True
    if "execution_mode" in parameters:
        return True
    return any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values())
