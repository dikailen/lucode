from __future__ import annotations

from pathlib import Path

from planning.planner_schema import PlannerResult
from runtime.execution.fast_paths import _is_url_only_task
from runtime.execution.inline_context import _latest_workspace_context
from runtime.execution.pipeline import PipelineRunState, build_verification_report
from runtime.workspace.patch_ledger import PatchProposalLedger


async def _run_direct_answer(raw_user_input, plan: PlannerResult, model_id, factory, hooks, run_agent) -> str:
    agent = factory.create_direct_answer_agent(model_id, plan.direct_answer_instruction)
    result = await run_agent(agent, raw_user_input, hooks)
    return result.final_output


def _task_prompt(
    refined_request: str,
    task_instruction: str,
    dependency_context: str = "",
    workspace_context: str = "",
) -> str:
    prefix = "优化后的用户请求：\n" f"{refined_request}\n\n"
    if dependency_context.strip():
        prefix += "前序任务输出：\n" f"{dependency_context}\n\n"
    if workspace_context.strip():
        prefix += f"{workspace_context.strip()}\n\n"
    prefix += "你的具体任务：\n" f"{task_instruction}"
    return prefix


async def _run_planned_task(
    refined_request,
    task,
    project_root,
    factory,
    hooks,
    run_agent,
    run_state: PipelineRunState | None = None,
    ledger: PatchProposalLedger | None = None,
) -> tuple[str, str]:
    if ledger:
        ledger.record_proposal(task, task.instruction)
    agent = await factory.create_task_agent(task)
    dependency_context = _dependency_context_for_task(task, _task_output_map(run_state))
    workspace_context = _latest_workspace_context(project_root, task)
    try:
        result = await run_agent(
            agent,
            _task_prompt(refined_request, task.instruction, dependency_context, workspace_context),
            hooks,
            max_turns=_max_turns_for_task(task),
        )
    except Exception as exc:
        if run_state:
            run_state.record_task_error(task, exc)
        if ledger:
            ledger.record_task_status(task.id, "failed", str(exc))
        raise
    output = _with_verification_report(project_root, task, str(result.final_output), run_state)
    if run_state:
        run_state.record_task_result(task, output)
    if ledger:
        ledger.record_task_status(task.id, "completed", output)
    return task.title, output


def _with_verification_report(project_root: Path, task, output: str, run_state: PipelineRunState | None = None) -> str:
    report = build_verification_report(project_root, task)
    if not report:
        return output
    if run_state:
        run_state.record_verification(task.id, report)
    return output.rstrip() + "\n\n" + report


def _max_turns_for_task(task) -> int:
    if "web_search" in task.mcp and _is_url_only_task(task):
        return 2
    if task.mcp:
        return 12
    return 6


def _task_output_map(run_state: PipelineRunState | None) -> dict[str, str]:
    if not run_state:
        return {}
    return {
        task.id: task.output_preview
        for task in run_state.tasks
        if task.output_preview
    }


def _dependency_context_for_task(task, outputs: dict[str, str]) -> str:
    parts = []
    for dep in task.depends_on:
        value = outputs.get(dep)
        if not value:
            continue
        parts.append(f"[{dep}]\n{value}")
    return "\n\n".join(parts)
