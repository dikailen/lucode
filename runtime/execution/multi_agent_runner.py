from __future__ import annotations

import asyncio
from collections import defaultdict

from mcp_servers import create_readonly_filesystem_server
from planning.planner_schema import PlannerResult
from runtime.config.execution_mode import normalize_execution_mode
from runtime.execution.parallel_scheduler import _execution_batches_for_mode, _format_parallel_batch_audit
from runtime.execution.pipeline import PipelineRunState
from runtime.execution.progress import _print_progress_snapshot
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
):
    workspace = RunWorkspace(project_root)
    run_dir = workspace.create()
    ledger = PatchProposalLedger(project_root)
    mode = normalize_execution_mode(execution_mode)

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
                    title, output = await _run_planned_task(
                        refined_request, task, project_root, factory, hooks, run_agent, run_state, ledger
                    )
                    workspace.write_task_output(task.id, title, output)
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
                            refined_request, task, project_root, factory, hooks, run_agent, run_state, ledger
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
                if show_progress and run_state:
                    _print_progress_snapshot(run_state, mode=mode, attempt=attempt, active=f"已完成：{active}")

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
