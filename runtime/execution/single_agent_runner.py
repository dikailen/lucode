from __future__ import annotations

from pathlib import Path

from runtime.execution.fast_paths import (
    _can_fast_path_git_status,
    _can_fast_path_url_search,
    _run_git_status_fast_path,
    _run_url_search_fast_path,
)
from runtime.execution.failure_memory import _record_flywheel_safely
from runtime.execution.inline_context import _inline_project_file_context, _latest_workspace_context
from runtime.execution.pipeline import PipelineRunState
from runtime.execution.progress import _print_progress_snapshot
from runtime.execution.task_runner import _max_turns_for_task, _task_prompt, _with_verification_report
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
    if show_plan:
        run_state.record_task_started(task)
        _print_progress_snapshot(run_state, mode=execution_mode, attempt=attempt, active=task.title)
    if _can_fast_path_url_search(task):
        output = _run_url_search_fast_path(refined_request, task)
        run_state.record_task_result(task, output)
        if show_plan:
            _print_progress_snapshot(run_state, mode=execution_mode, attempt=attempt, active="已完成")
        _record_flywheel_safely(flywheel, run_state)
        audit = audit_execution(plan, run_state, output)
        return format_final_report(output, audit), audit
    if _can_fast_path_git_status(task):
        output = _run_git_status_fast_path(project_root, task)
        run_state.record_task_result(task, output)
        if show_plan:
            _print_progress_snapshot(run_state, mode=execution_mode, attempt=attempt, active="已完成")
        _record_flywheel_safely(flywheel, run_state)
        audit = audit_execution(plan, run_state, output)
        return format_final_report(output, audit), audit

    workspace_context = _latest_workspace_context(project_root, task)
    inline_context = _inline_project_file_context(project_root, task, refined_request)
    if inline_context:
        agent = factory.create_direct_answer_agent(
            task.model,
            (
                "请只基于用户请求、任务说明和提供的项目文件片段完成只读分析；"
                "不要声称已经调用工具，不要要求用户再粘贴文件。"
            ),
        )
        try:
            result = await run_agent(
                agent,
                _task_prompt(
                    refined_request,
                    task.instruction,
                    workspace_context=f"{workspace_context}\n\n{inline_context}",
                ),
                hooks,
                max_turns=4,
            )
        except Exception as exc:
            run_state.record_task_error(task, exc)
            if show_plan:
                _print_progress_snapshot(
                    run_state,
                    mode=execution_mode,
                    attempt=attempt,
                    active=f"失败：{task.title}",
                )
            _record_flywheel_safely(flywheel, run_state)
            raise
        output = _with_verification_report(project_root, task, str(result.final_output), run_state)
        run_state.record_task_result(task, output)
        if show_plan:
            _print_progress_snapshot(run_state, mode=execution_mode, attempt=attempt, active="已完成")
        _record_flywheel_safely(flywheel, run_state)
        audit = audit_execution(plan, run_state, output)
        return format_final_report(output, audit), audit

    agent = await factory.create_task_agent(task)
    try:
        result = await run_agent(
            agent,
            _task_prompt(refined_request, task.instruction, workspace_context=workspace_context),
            hooks,
            max_turns=_max_turns_for_task(task),
        )
    except Exception as exc:
        run_state.record_task_error(task, exc)
        if show_plan:
            _print_progress_snapshot(
                run_state,
                mode=execution_mode,
                attempt=attempt,
                active=f"失败：{task.title}",
            )
        _record_flywheel_safely(flywheel, run_state)
        raise
    output = _with_verification_report(project_root, task, str(result.final_output), run_state)
    run_state.record_task_result(task, output)
    if show_plan:
        _print_progress_snapshot(run_state, mode=execution_mode, attempt=attempt, active="已完成")
    _record_flywheel_safely(flywheel, run_state)
    audit = audit_execution(plan, run_state, output)
    return format_final_report(output, audit), audit
