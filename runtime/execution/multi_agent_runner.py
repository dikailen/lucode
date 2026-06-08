from __future__ import annotations

import asyncio
import inspect
from collections import defaultdict
from pathlib import Path

from mcp_servers import apply_full_supervisor_readonly_budget_profile, create_readonly_filesystem_server
from planning.planner_schema import PlannerResult
from runtime.config.execution_mode import normalize_execution_mode
from runtime.execution.execution_contract import summary_helper_enabled, supervisor_route
from runtime.execution.lead_reviewer import (
    emit_lead_review_events,
    readonly_hard_constraint_from_plan,
    render_lead_review_findings,
    review_worker_reports,
)
from runtime.execution.parallel_scheduler import _execution_batches_for_mode, _format_parallel_batch_audit
from runtime.execution.pipeline import PipelineRunState
from runtime.execution.progress import _print_progress_snapshot
from runtime.execution.supervisor_observer import emit_supervisor_observation, render_supervisor_context_for_workers
from runtime.execution.task_runner import _run_agent_kwargs, _run_planned_task
from runtime.execution.worker_reporter import build_worker_report, render_worker_report
from runtime.ui.live_status import dynamic_status
from runtime.workspace.patch_ledger import PatchProposalLedger
from runtime.workspace.run_workspace import RunWorkspace


WORKER_OUTPUT_EXPAND_MIN_LINES = 40
WORKER_OUTPUT_EXPAND_MIN_CHARS = 4000


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
    worker_reports = []
    supervisor_view = None
    if run_state:
        supervisor_view = emit_supervisor_observation(plan, mode=mode, event_bus=run_state.event_bus)
        _seed_supervisor_context_pack(run_state, supervisor_view, mode=mode, route=route)

    try:
        for group_id, tasks in _tasks_by_parallel_group(plan).items():
            batches = _execution_batches_for_mode(tasks, mode)
            if mode != "full" and len(tasks) > 1:
                _emit_parallel_batch_notice(
                    run_state,
                    group_id=group_id,
                    batch=tasks,
                    mode=mode,
                    event_type="ParallelBatchSerialized",
                    status="serialized",
                    reason="non_full_mode_serialized",
                    fallback_message=f"parallel group {group_id} serialized in {mode} mode",
                )
            elif len(tasks) > 1 and len(batches) > 1:
                _emit_parallel_batch_notice(
                    run_state,
                    group_id=group_id,
                    batch=tasks,
                    mode=mode,
                    event_type="ParallelBatchSerialized",
                    status="serialized",
                    reason="write_conflict_or_undeclared_write_scope",
                    fallback_message=f"parallel group {group_id} split into serialized batches",
                )
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
                    _record_worker_output_detail(
                        project_root,
                        task_id=str(getattr(task, "id", "") or ""),
                        title=title,
                        output=output,
                        mode=mode,
                        route=route,
                        run_state=run_state,
                    )
                    worker_outputs.append((str(getattr(task, "id", "") or ""), title, output))
                    worker_reports.append(build_worker_report(task, output, run_state=run_state))
                    if show_progress and run_state:
                        _print_progress_snapshot(run_state, mode=mode, attempt=attempt, active=f"已完成：{task.title}")
                    continue

                _emit_parallel_batch_notice(
                    run_state,
                    group_id=group_id,
                    batch=batch,
                    mode=mode,
                    event_type="ParallelBatchStarted",
                    status="running",
                    reason="readonly_no_write_conflict",
                    fallback_message=_format_parallel_batch_audit(group_id, batch),
                )
                if show_progress and run_state:
                    for task in batch:
                        run_state.record_task_started(task)
                    active = "并行批次 " + ", ".join(getattr(task, "id", "task") for task in batch)
                    _print_progress_snapshot(run_state, mode=mode, attempt=attempt, active=active)
                with dynamic_status(
                    _batch_status_label(group_id, batch),
                    mode=mode,
                    stage="batch",
                    enabled=show_progress,
                ):
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
                                show_status=False,
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
                    _record_worker_output_detail(
                        project_root,
                        task_id=str(getattr(task, "id", "") or ""),
                        title=title,
                        output=output,
                        mode=mode,
                        route=route,
                        run_state=run_state,
                    )
                    worker_outputs.append((str(getattr(task, "id", "") or ""), title, output))
                    worker_reports.append(build_worker_report(task, output, run_state=run_state))
                if show_progress and run_state:
                    _print_progress_snapshot(run_state, mode=mode, attempt=attempt, active=f"已完成：{active}")

        with dynamic_status(
            "Lead Review",
            mode=mode,
            stage="review",
            enabled=show_progress and mode == "full",
        ):
            lead_review_findings = _review_full_worker_reports(plan, worker_reports, run_state, mode=mode)

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
            output = _render_lead_supervisor_output(
                plan,
                run_dir,
                run_state,
                mode,
                worker_outputs,
                worker_reports,
                lead_review_findings,
            )
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
            with dynamic_status(
                "final summary",
                mode=mode,
                stage="summary",
                enabled=show_progress,
            ):
                result = await run_agent(
                    synthesizer,
                    synthesis_prompt,
                    hooks,
                    **_run_agent_kwargs(run_agent, max_turns=10, stream_output=False),
                )
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


def _emit_parallel_batch_notice(
    run_state: PipelineRunState | None,
    *,
    group_id: int,
    batch: list,
    mode: str,
    event_type: str,
    status: str,
    reason: str,
    fallback_message: str,
) -> None:
    task_ids = [str(getattr(task, "id", "") or "task") for task in list(batch or [])]
    message = f"parallel group {group_id}: {len(task_ids)} worker(s)"
    if run_state is None or not hasattr(run_state, "emit_event"):
        print(fallback_message)
        return
    run_state.emit_event(
        event_type,
        message,
        mode=mode,
        agent="supervisor",
        status=status,
        payload={
            "group_id": group_id,
            "task_ids": task_ids,
            "batch_size": len(task_ids),
            "reason": reason,
        },
    )


def _record_worker_output_detail(
    project_root,
    *,
    task_id: str,
    title: str,
    output: str,
    mode: str,
    route: str,
    run_state: PipelineRunState | None = None,
) -> str:
    if str(mode or "").strip().lower() != "full" or str(route or "").strip().lower() != "team":
        return ""
    text = str(output or "")
    if not _should_store_worker_output(text):
        return ""
    try:
        from runtime.history.expand_store import ExpandBlockStore

        block_id = f"worker-{task_id or 'task'}"
        saved = ExpandBlockStore(Path(project_root)).save_text(
            block_id,
            text,
            kind="worker",
            title=f"Worker output: {title or task_id or 'task'}",
            preview=_compact_worker_output(text),
        )
    except Exception:
        return ""
    hint = f"Worker output saved: /expand {saved.block_id}"
    if run_state is not None and hasattr(run_state, "emit_event"):
        run_state.emit_event(
            "WorkerOutputStored",
            hint,
            mode=mode,
            agent="supervisor",
            task_id=task_id,
            status="completed",
            payload={
                "block_id": saved.block_id,
                "kind": saved.kind,
                "title": saved.title,
                "preview": saved.preview,
            },
        )
    return hint


def _should_store_worker_output(text: str) -> bool:
    clean = str(text or "")
    if not clean.strip():
        return False
    return len(clean.splitlines()) > WORKER_OUTPUT_EXPAND_MIN_LINES or len(clean) > WORKER_OUTPUT_EXPAND_MIN_CHARS


def _seed_supervisor_context_pack(run_state: PipelineRunState, supervisor_view, *, mode: str, route: str) -> bool:
    if mode != "full" or route != "team":
        return False
    run_context = getattr(run_state, "run_context", None)
    if run_context is None:
        return False
    if hasattr(run_context, "record_context_pack"):
        recorded = False
        for pack in list(getattr(supervisor_view, "context_packs", []) or []):
            try:
                run_context.record_context_pack(pack, task_id="supervisor")
                recorded = True
            except Exception:
                continue
        if recorded:
            return True
    if not hasattr(run_context, "record_tool_output"):
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


def _review_full_worker_reports(plan: PlannerResult, worker_reports: list, run_state: PipelineRunState | None, *, mode: str) -> list:
    if mode != "full" or not worker_reports:
        return []
    findings = review_worker_reports(
        plan.tasks,
        worker_reports,
        readonly_hard_constraint=readonly_hard_constraint_from_plan(plan),
    )
    emit_lead_review_events(run_state, findings, mode=mode)
    return findings


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


def _batch_status_label(group_id: int, batch: list) -> str:
    task_ids = [str(getattr(task, "id", "") or "task").strip() for task in list(batch or [])]
    clean_ids = [task_id for task_id in task_ids if task_id]
    joined = ", ".join(clean_ids[:4])
    if len(clean_ids) > 4:
        joined += f", +{len(clean_ids) - 4}"
    return f"group {group_id} - {len(clean_ids)} workers" + (f": {joined}" if joined else "")


def _render_lead_supervisor_output(
    plan: PlannerResult,
    run_dir,
    run_state: PipelineRunState | None,
    mode: str,
    worker_outputs: list[tuple[str, str, str]] | None = None,
    worker_reports: list | None = None,
    lead_review_findings: list | None = None,
) -> str:
    lines = [
        "主管最终汇报",
        "",
        "## 任务判断",
        f"- 模式：{mode}",
        f"- 路线：{supervisor_route(plan) or 'team'}",
        f"- SummaryHelper：{summary_helper_enabled(plan)}",
        f"- 任务数：{len(list(getattr(plan, 'tasks', []) or []))}",
        "",
        "## Worker 执行结果",
    ]
    if worker_reports:
        for report in worker_reports:
            lines.append(_indent_block(render_worker_report(report), "  "))
    elif worker_outputs:
        for task_id, title, output in worker_outputs:
            label = task_id or title or "task"
            lines.append(f"  - {label}: {_compact_worker_output(output)}")
    elif run_state:
        for record in list(getattr(run_state, "tasks", []) or []):
            preview = str(getattr(record, "output_preview", "") or "").strip()
            lines.append(f"  - {record.id}: {preview or '无输出预览'}")
    else:
        lines.append("- none")
    lines.extend(["", "## 文件影响"])
    lines.extend(_render_file_impact_lines(worker_reports))
    lines.extend(["", "## 验证结果"])
    lines.extend(_render_verification_lines(worker_reports))
    lines.extend(["", "## 主管审查"])
    if lead_review_findings:
        lines.append(_indent_block(render_lead_review_findings(lead_review_findings), "  "))
    else:
        lines.append("  LeadReview")
        lines.append("  - findings: none")
    try:
        names = sorted(path.name for path in run_dir.glob("*.md"))
    except Exception:
        names = []
    if names:
        lines.extend(["", "## 产物文件"])
        lines.append("- 产物文件：")
        lines.extend(f"  - {name}" for name in names)
    lines.extend(["", "## 最终结论"])
    if lead_review_findings:
        error_count = sum(1 for finding in lead_review_findings if getattr(finding, "severity", "") == "error")
        warning_count = sum(1 for finding in lead_review_findings if getattr(finding, "severity", "") == "warning")
        lines.append(f"- 主管已完成收口审查：error={error_count}, warning={warning_count}。")
        if error_count:
            lines.append("- 存在 error 级风险，请优先查看“主管审查”。")
    else:
        lines.append("- 主管已完成收口审查，未发现 WorkerReport 风险。")
    return "\n".join(lines)


def _render_file_impact_lines(worker_reports: list | None) -> list[str]:
    reports = list(worker_reports or [])
    if not reports:
        return ["- files_read: none", "- files_written: none"]
    read_paths = _unique_report_values(path for report in reports for path in list(getattr(report, "files_read", []) or []))
    written_paths = _unique_report_values(
        path for report in reports for path in list(getattr(report, "files_written", []) or [])
    )
    return [
        f"- files_read: {', '.join(read_paths) if read_paths else 'none'}",
        f"- files_written: {', '.join(written_paths) if written_paths else 'none'}",
    ]


def _render_verification_lines(worker_reports: list | None) -> list[str]:
    reports = list(worker_reports or [])
    claimed = [
        artifact.split(":", 1)[1].strip()
        for report in reports
        for artifact in list(getattr(report, "artifacts", []) or [])
        if str(artifact).startswith("claimed_verification:")
    ]
    if claimed:
        return [f"- {item}" for item in _unique_report_values(claimed)]
    tool_actions = _unique_report_values(
        f"{call.get('tool')}.{call.get('action')}".strip(".")
        for report in reports
        for call in list(getattr(report, "tool_calls", []) or [])
        if call.get("tool") or call.get("action")
    )
    if tool_actions:
        return [f"- 工具证据：{', '.join(tool_actions)}"]
    return ["- 未收到 worker 自述验证结果；以确定性工具/事件记录为准。"]


def _unique_report_values(values) -> list[str]:
    result: list[str] = []
    seen = set()
    for value in values:
        clean = str(value or "").strip().replace("\\", "/")
        if not clean or clean in seen:
            continue
        seen.add(clean)
        result.append(clean)
    return result


def _compact_worker_output(output: str, limit: int = 1200) -> str:
    value = str(output or "").strip()
    if len(value) <= limit:
        return value
    return value[:limit] + f"...[truncated {len(value) - limit} chars]"


def _indent_block(text: str, prefix: str) -> str:
    return "\n".join(prefix + line for line in str(text or "").splitlines())


def _run_planned_task_accepts_execution_mode() -> bool:
    try:
        parameters = inspect.signature(_run_planned_task).parameters
    except (TypeError, ValueError):
        return True
    if "execution_mode" in parameters:
        return True
    return any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values())
