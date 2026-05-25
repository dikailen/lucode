from __future__ import annotations

import asyncio
import inspect
from collections import defaultdict

from mcp_servers import apply_full_supervisor_readonly_budget_profile, create_readonly_filesystem_server
from planning.planner_schema import PlannerResult
from runtime.config.execution_mode import normalize_execution_mode
from runtime.execution.execution_contract import summary_helper_enabled, supervisor_route
from runtime.execution.parallel_scheduler import _execution_batches_for_mode, _format_parallel_batch_audit
from runtime.execution.pipeline import PipelineRunState
from runtime.execution.progress import _print_progress_snapshot
from runtime.execution.supervisor_observer import emit_supervisor_observation, render_supervisor_context_for_workers
from runtime.execution.task_runner import _run_planned_task
from runtime.workspace.patch_ledger import PatchProposalLedger
from runtime.workspace.run_workspace import RunWorkspace


async def _run_multi_agent(
    refined_request,
    plan: PlannerResult,
    project_root,
    model_id,
    factory,
    hooks,
    run_agent,
    run_state: PipelineRunState | None = None,
    execution_mode: str = "serial",
    show_progress: bool = True,
    attempt: int = 1,
    approval_policy_factory=None,
):
    workspace = RunWorkspace(project_root)
    run_dir = workspace.create()
    ledger = PatchProposalLedger(project_root)
    mode = normalize_execution_mode(execution_mode)
    _apply_full_supervisor_budget_profile(factory, plan.tasks, mode)
    route = supervisor_route(plan)
    use_summary_helper = summary_helper_enabled(plan)
    worker_outputs: list[tuple[str, str, str]] = []
    supervisor_view = None
    if run_state:
        supervisor_view = emit_supervisor_observation(plan, mode=mode, event_bus=run_state.event_bus)
        _seed_supervisor_context_pack(run_state, supervisor_view, mode=mode, route=route)

    try:
        for group_id, tasks in _tasks_by_parallel_group(plan).items():
            batches = _execution_batches_for_mode(tasks, mode)
            if mode != "full" and len(tasks) > 1:
                print(f"执行模式 {mode}：并行组 {group_id} 按依赖顺序串行执行，避免多 Agent 同时修改。")
            elif len(tasks) > 1 and len(batches) > 1:
                print(f"执行并行组 {group_id}：检测到写入冲突或未声明写入范围，改为分批半串行执行。")
            for batch in batches:
                if len(batch) == 1:
                    task = batch[0]
                    if show_progress and run_state:
                        run_state.record_task_started(task)
                        _print_progress_snapshot(run_state, mode=mode, attempt=attempt, active=task.title)
                    kwargs = _task_runner_kwargs(approval_policy_factory, task)
                    kwargs.update(_execution_mode_kwarg(mode))
                    title, output = await _run_planned_task(
                        refined_request,
                        task,
                        project_root,
                        factory,
                        hooks,
                        run_agent,
                        run_state,
                        ledger,
                        **kwargs,
                    )
                    workspace.write_task_output(task.id, title, output)
                    worker_outputs.append((str(getattr(task, "id", "") or ""), title, output))
                    if show_progress and run_state:
                        _print_progress_snapshot(run_state, mode=mode, attempt=attempt, active=f"已完成：{task.title}")
                    continue

                print(_format_parallel_batch_audit(group_id, batch))
                if show_progress and run_state:
                    for task in batch:
                        run_state.record_task_started(task)
                    active = "并行批次 " + ", ".join(getattr(task, "id", "task") for task in batch)
                    _print_progress_snapshot(run_state, mode=mode, attempt=attempt, active=active)
                results = await asyncio.gather(
                    *(
                        _run_planned_task(
                            refined_request,
                            task,
                            project_root,
                            factory,
                            hooks,
                            run_agent,
                            run_state,
                            ledger,
                            **_task_runner_kwargs_with_mode(approval_policy_factory, task, mode),
                        )
                        for task in batch
                    ),
                    return_exceptions=True,
                )
                normalized_results = []
                for task, result in zip(batch, results):
                    if isinstance(result, Exception):
                        message = str(result) or result.__class__.__name__
                        if run_state:
                            run_state.record_task_error(task, message)
                        normalized_results.append(
                            (
                                task.title,
                                f"任务失败：{task.title}\n原因：{message}\n系统已记录该失败并交给最终审核判断。",
                            )
                        )
                    else:
                        normalized_results.append(result)
                if show_progress and run_state and any(
                    getattr(record, "status", "") == "failed"
                    for record in run_state.tasks
                    if record.id in {getattr(task, "id", "") for task in batch}
                ):
                    _print_progress_snapshot(
                        run_state,
                        mode=mode,
                        attempt=attempt,
                        active=f"批次存在失败：{active}",
                    )
                for task, (title, output) in zip(batch, normalized_results):
                    workspace.write_task_output(task.id, title, output)
                    worker_outputs.append((str(getattr(task, "id", "") or ""), title, output))
                if show_progress and run_state:
                    _print_progress_snapshot(run_state, mode=mode, attempt=attempt, active=f"已完成：{active}")

        if mode == "full" and route == "team" and not use_summary_helper:
            if run_state:
                run_state.emit_event(
                    "LeadFinalizing",
                    "主管正在收口并生成最终汇报",
                    mode=mode,
                    agent="supervisor",
                    status="running",
                    payload={"route": route, "summary_helper": False},
                )
            if show_progress and run_state:
                _print_progress_snapshot(run_state, mode=mode, attempt=attempt, active="主管收口")
            output = _render_lead_supervisor_output(plan, run_dir, run_state, mode, worker_outputs)
            if run_state:
                run_state.emit_event(
                    "LeadCompleted",
                    "主管最终汇报完成",
                    mode=mode,
                    agent="supervisor",
                    status="completed",
                    payload={"route": route, "summary_helper": False},
                )
            return output

        async with create_readonly_filesystem_server(
            run_dir,
            "run_workspace_readonly",
        ) as run_workspace_server:
            synthesizer = factory.create_synthesizer_agent(model_id, run_workspace_server)
            synthesis_prompt = (
                "请读取当前运行工作目录中的所有任务输出文件，按照以下要求汇总：\n"
                f"{plan.synthesis_instruction}\n\n"
                "这些文件只是本轮临时 Agent 输出，不是用户项目文件；"
                "最终回答请称为“专家输出/任务输出”，不要声称读取了用户项目文件。\n"
                "请输出面向用户的最终中文答案。"
            )
            result = await run_agent(synthesizer, synthesis_prompt, hooks, max_turns=10)
            if show_progress and run_state:
                _print_progress_snapshot(run_state, mode=mode, attempt=attempt, active="汇总完成")
            return result.final_output
    finally:
        workspace.cleanup()


def _tasks_by_parallel_group(plan: PlannerResult) -> dict[int, list]:
    groups = defaultdict(list)
    for task in _ordered_tasks_for_execution(plan):
        groups[task.parallel_group].append(task)
    return dict(sorted(groups.items(), key=lambda item: item[0]))


def _seed_supervisor_context_pack(run_state: PipelineRunState, supervisor_view, *, mode: str, route: str) -> bool:
    if mode != "full" or route != "team":
        return False
    run_context = getattr(run_state, "run_context", None)
    if run_context is None or not hasattr(run_context, "record_tool_output"):
        return False
    summary = render_supervisor_context_for_workers(supervisor_view)
    if not summary:
        return False
    try:
        run_context.record_tool_output(
            tool="supervisor",
            action="context_pack",
            summary=summary,
            task_id="supervisor",
        )
        return True
    except Exception:
        return False


def _apply_full_supervisor_budget_profile(factory, tasks: list, mode: str) -> bool:
    if normalize_execution_mode(mode) != "full":
        return False
    mcp_ids = sorted(
        {
            str(mcp_id)
            for task in list(tasks or [])
            for mcp_id in list(getattr(task, "mcp", []) or [])
            if str(mcp_id or "").strip()
        }
    )
    manager = getattr(factory, "mcp_manager", None)
    return apply_full_supervisor_readonly_budget_profile(manager, mcp_ids)


def _ordered_tasks_for_execution(plan: PlannerResult) -> list:
    task_by_id = {task.id: task for task in plan.tasks}
    pending = {task.id: set(task.depends_on) for task in plan.tasks}
    emitted: list = []
    remaining = list(plan.tasks)

    while remaining:
        progressed = False
        for task in list(remaining):
            deps = {dep for dep in pending.get(task.id, set()) if dep in task_by_id}
            if deps:
                continue
            emitted.append(task)
            remaining.remove(task)
            progressed = True
            for other in pending.values():
                other.discard(task.id)
        if progressed:
            continue
        emitted.extend(remaining)
        break

    return emitted


def _approval_policy_for_task(approval_policy_factory, task):
    if approval_policy_factory is None:
        return None
    try:
        return approval_policy_factory(task)
    except Exception:
        return None


def _task_runner_kwargs(approval_policy_factory, task) -> dict:
    policy = _approval_policy_for_task(approval_policy_factory, task)
    return {"approval_policy": policy} if policy is not None else {}


def _task_runner_kwargs_with_mode(approval_policy_factory, task, mode: str) -> dict:
    kwargs = _task_runner_kwargs(approval_policy_factory, task)
    kwargs.update(_execution_mode_kwarg(mode))
    return kwargs


def _execution_mode_kwarg(mode: str) -> dict:
    if not _run_planned_task_accepts_execution_mode():
        return {}
    return {"execution_mode": mode}


def _render_lead_supervisor_output(
    plan: PlannerResult,
    run_dir,
    run_state: PipelineRunState | None,
    mode: str,
    worker_outputs: list[tuple[str, str, str]] | None = None,
) -> str:
    lines = [
        "主管最终汇报",
        f"- 模式：{mode}",
        f"- 路线：{supervisor_route(plan) or 'team'}",
        f"- SummaryHelper：{summary_helper_enabled(plan)}",
    ]
    if worker_outputs:
        lines.append("- worker 报告：")
        for task_id, title, output in worker_outputs:
            label = task_id or title or "task"
            lines.append(f"  - {label}: {_compact_worker_output(output)}")
    elif run_state:
        lines.append("- worker 报告：")
        for record in list(getattr(run_state, "tasks", []) or []):
            preview = str(getattr(record, "output_preview", "") or "").strip()
            lines.append(f"  - {record.id}: {preview or '无输出预览'}")
    try:
        names = sorted(path.name for path in run_dir.glob("*.md"))
    except Exception:
        names = []
    if names:
        lines.append("- 产物文件：")
        lines.extend(f"  - {name}" for name in names)
    return "\n".join(lines)


def _compact_worker_output(output: str, limit: int = 1200) -> str:
    value = str(output or "").strip()
    if len(value) <= limit:
        return value
    return value[:limit] + f"...[truncated {len(value) - limit} chars]"


def _run_planned_task_accepts_execution_mode() -> bool:
    try:
        parameters = inspect.signature(_run_planned_task).parameters
    except (TypeError, ValueError):
        return True
    if "execution_mode" in parameters:
        return True
    return any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values())
