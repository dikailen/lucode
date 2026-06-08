from __future__ import annotations

import contextlib
import inspect
from dataclasses import dataclass
from pathlib import Path

from planning.planner_schema import PlannedTask, PlannerResult
from runtime.execution.fast_paths import (
    _can_fast_path_config_summary,
    _can_fast_path_directory_summary,
    _can_fast_path_git_diff,
    _can_fast_path_mcp_catalog_count,
    _can_fast_path_project_manifest_summary,
    _can_fast_path_readme_mcp_count,
    _is_url_only_task,
    _run_config_summary_fast_path,
    _run_directory_summary_fast_path,
    _run_git_diff_fast_path,
    _run_mcp_catalog_count_fast_path,
    _run_project_manifest_summary_fast_path,
    _run_readme_mcp_count_fast_path,
)
from runtime.execution.inline_context import (
    _inline_project_file_context,
    _latest_workspace_context,
    _read_project_file_excerpt,
    _resolve_explicit_project_file_paths,
)
from runtime.execution.pipeline import PipelineRunState, build_verification_report
from runtime.hooks import TaskScopedHooks
from runtime.ui.live_status import dynamic_status
from runtime.workspace.patch_ledger import PatchProposalLedger


@dataclass(frozen=True)
class ReadonlyFastPathResult:
    output: str
    tool: str
    action: str


async def _run_direct_answer(
    raw_user_input,
    plan: PlannerResult,
    model_id,
    factory,
    hooks,
    run_agent,
    execution_mode: str = "",
) -> str:
    agent = factory.create_direct_answer_agent(
        model_id,
        plan.direct_answer_instruction,
        execution_mode=execution_mode,
    )
    run_agent_kwargs = {}
    if _run_agent_accepts_stream_output(run_agent):
        run_agent_kwargs["stream_output"] = False
    with dynamic_status("direct answer", stage="direct"):
        result = await run_agent(agent, raw_user_input, hooks, **run_agent_kwargs)
    return result.final_output


def _direct_answer_input_with_inline_context(
    raw_user_input: str,
    refined_request: str,
    project_root: Path,
    model_id: str,
    run_state: PipelineRunState | None,
) -> str:
    """Attach explicit safe project file excerpts when direct-answer routing references files."""

    task = PlannedTask(
        id="direct_context",
        title="direct answer context",
        instruction=refined_request or raw_user_input,
        skill_id="project_explorer",
        model=model_id,
        mcp=["project_filesystem_readonly"],
    )
    inline_context = _inline_project_file_context(
        project_root,
        task,
        "\n".join([raw_user_input, refined_request]),
        run_context=getattr(run_state, "run_context", None),
    )
    if not inline_context.strip():
        return raw_user_input
    return (
        f"{raw_user_input}\n\n"
        "下面是系统已经只读读取到的项目文件片段，请基于这些真实上下文回答：\n"
        f"{inline_context}"
    )


def _task_prompt(
    refined_request: str,
    task_instruction: str,
    dependency_context: str = "",
    workspace_context: str = "",
    shared_context: str = "",
) -> str:
    prefix = "优化后的用户请求：\n" f"{refined_request}\n\n"
    if dependency_context.strip():
        prefix += "前序任务输出：\n" f"{dependency_context}\n\n"
    if shared_context.strip():
        prefix += f"{shared_context.strip()}\n\n"
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
    approval_policy=None,
    execution_mode: str = "",
    show_status: bool = True,
) -> tuple[str, str]:
    if ledger:
        ledger.record_proposal(task, task.instruction)
    failed_dependencies = _failed_dependencies_for_task(task, run_state)
    if failed_dependencies:
        message = "依赖任务未完成或已失败：" + ", ".join(failed_dependencies)
        if run_state:
            run_state.record_task_error(task, message)
        if ledger:
            ledger.record_task_status(task.id, "failed", message)
        return task.title, _task_failure_output(task, message)

    run_context = getattr(run_state, "run_context", None)
    fast_path = _readonly_fast_path_result(project_root, task, run_context=run_context)
    if fast_path is not None:
        if run_state:
            run_state.record_fast_path_used(task, tool=fast_path.tool, action=fast_path.action)
        output = _with_verification_report(project_root, task, fast_path.output, run_state)
        if run_state:
            run_state.record_task_result(task, output)
        if ledger:
            ledger.record_task_status(task.id, "completed", output)
        return task.title, output
    agent = await _create_task_agent(factory, task, execution_mode=execution_mode)
    dependency_context = _dependency_context_for_task(task, _task_output_map(run_state))
    workspace_context = _latest_workspace_context(project_root, task)
    shared_context = _shared_context_for_task(run_state, task)
    try:
        run_agent_kwargs = _run_agent_kwargs(
            run_agent,
            max_turns=_max_turns_for_task(task),
            approval_policy=approval_policy,
            stream_output=False,
        )
        scoped_hooks = _task_scoped_hooks(hooks, run_state, task)
        status_context = (
            dynamic_status(_task_status_label(task), mode=execution_mode, stage="worker")
            if show_status
            else contextlib.nullcontext()
        )
        with status_context:
            result = await run_agent(
                agent,
                _task_prompt(
                    refined_request,
                    task.instruction,
                    dependency_context,
                    workspace_context,
                    shared_context,
                ),
                scoped_hooks,
                **run_agent_kwargs,
            )
    except Exception as exc:
        message = _friendly_task_error(exc)
        if run_state:
            run_state.record_task_error(task, message)
        if ledger:
            ledger.record_task_status(task.id, "failed", message)
        return task.title, _task_failure_output(task, message)
    output = _with_verification_report(project_root, task, str(result.final_output), run_state)
    _record_declared_read_set_context(run_context, project_root, task, refined_request=refined_request)
    if run_state:
        run_state.record_task_result(task, output)
    if ledger:
        ledger.record_task_status(task.id, "completed", output)
    return task.title, output


def _readonly_fast_path_output(project_root: Path, task, run_context=None) -> str | None:
    result = _readonly_fast_path_result(project_root, task, run_context=run_context)
    return result.output if result else None


def _readonly_fast_path_result(project_root: Path, task, run_context=None) -> ReadonlyFastPathResult | None:
    if _can_fast_path_git_diff(task):
        output = _run_git_diff_fast_path(project_root, task)
        _record_fast_path_context(run_context, "git", "diff", output, task)
        return ReadonlyFastPathResult(output=output, tool="git", action="diff")
    if _can_fast_path_project_manifest_summary(task):
        output = _run_project_manifest_summary_fast_path(project_root, task)
        _record_fast_path_context(run_context, "project_manifest", "summary", output, task)
        return ReadonlyFastPathResult(output=output, tool="project_manifest", action="summary")
    if _can_fast_path_config_summary(task):
        output = _run_config_summary_fast_path(project_root, task)
        _record_fast_path_context(run_context, "config", "summary", output, task)
        return ReadonlyFastPathResult(output=output, tool="config", action="summary")
    if _can_fast_path_directory_summary(project_root, task):
        output = _run_directory_summary_fast_path(project_root, task)
        _record_fast_path_context(run_context, "project_filesystem_readonly", "directory_summary", output, task)
        return ReadonlyFastPathResult(
            output=output,
            tool="project_filesystem_readonly",
            action="directory_summary",
        )
    if _can_fast_path_mcp_catalog_count(task):
        output = _run_mcp_catalog_count_fast_path(project_root, task)
        _record_fast_path_context(run_context, "mcp_catalog", "count", output, task)
        return ReadonlyFastPathResult(output=output, tool="mcp_catalog", action="count")
    if _can_fast_path_readme_mcp_count(task):
        output = _run_readme_mcp_count_fast_path(project_root, task)
        _record_fast_path_context(run_context, "readme", "mcp_count", output, task)
        return ReadonlyFastPathResult(output=output, tool="readme", action="mcp_count")
    return None


def _record_fast_path_context(run_context, tool: str, action: str, output: str, task) -> None:
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


def _task_status_label(task) -> str:
    task_id = str(getattr(task, "id", "") or "").strip()
    title = str(getattr(task, "title", "") or "").strip()
    if task_id and title and task_id != title:
        return f"{task_id} - {title}"
    return title or task_id or "worker task"


def _task_scoped_hooks(hooks, run_state: PipelineRunState | None, task):
    event_bus = getattr(run_state, "event_bus", None)
    if hooks is None or event_bus is None:
        return hooks
    return TaskScopedHooks(hooks, task_id=str(getattr(task, "id", "") or ""), event_bus=event_bus)


def _shared_context_for_task(run_state: PipelineRunState | None, task) -> str:
    run_context = getattr(run_state, "run_context", None)
    if run_context is None or not hasattr(run_context, "render_for_task"):
        return ""
    try:
        return run_context.render_for_task(str(getattr(task, "id", "") or ""))
    except Exception:
        return ""


def _record_declared_read_set_context(run_context, project_root: Path, task, refined_request: str = "") -> None:
    if run_context is None or not hasattr(run_context, "record_file_snapshot"):
        return
    inferred: list[Path] = []
    seen: set[str] = set()
    for path in _resolve_explicit_project_file_paths(project_root, task, refined_request):
        key = str(path.resolve()).lower()
        if key in seen:
            continue
        seen.add(key)
        inferred.append(path)
        if len(inferred) >= 8:
            break
    for candidate in inferred:
        try:
            relative = candidate.relative_to(project_root.resolve()).as_posix()
            run_context.record_file_snapshot(
                path=candidate,
                task_id=str(getattr(task, "id", "") or ""),
                summary=f"{relative} 已由任务 {getattr(task, 'id', '') or 'unknown'} 读取或明确引用。",
                excerpt=_read_project_file_excerpt(
                    candidate,
                    query=refined_request or str(getattr(task, "instruction", "") or ""),
                ),
            )
        except Exception:
            continue
    for item in list(getattr(task, "read_set", []) or [])[:8]:
        candidate = _resolve_read_set_file(project_root, str(item or ""))
        if candidate is None:
            continue
        try:
            run_context.record_file_snapshot(
                path=candidate,
                task_id=str(getattr(task, "id", "") or ""),
                summary=f"{candidate.relative_to(project_root.resolve()).as_posix()} 已由任务 {getattr(task, 'id', '') or 'unknown'} 读取。",
            )
        except Exception:
            continue


def _resolve_read_set_file(project_root: Path, value: str) -> Path | None:
    clean = str(value or "").strip().strip("`'\"“”‘’（）()[]<>，,。；;：:")
    if not clean or "://" in clean:
        return None
    clean = clean.replace("\\", "/")
    if any(part == ".." for part in clean.split("/")):
        return None
    root = project_root.resolve()
    path = (root / clean).resolve()
    try:
        relative = path.relative_to(root)
    except ValueError:
        return None
    if _is_sensitive_context_path(relative):
        return None
    return path if path.is_file() else None


def _is_sensitive_context_path(relative: Path) -> bool:
    ignored_dirs = {
        ".git",
        ".agent_cache",
        ".agent_quarantine",
        ".agent_runs",
        ".pytest_cache",
        "__pycache__",
        "node_modules",
    }
    parts = {part.lower() for part in relative.parts}
    if parts & ignored_dirs:
        return True
    name = relative.name.lower()
    if name in {".env", "auth.json"}:
        return True
    sensitive_markers = ("secret", "token", "apikey", "api_key", "password", "credential")
    return any(marker in name for marker in sensitive_markers)


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


def _failed_dependencies_for_task(task, run_state: PipelineRunState | None) -> list[str]:
    if not run_state:
        return []
    records = {record.id: record for record in run_state.tasks}
    failed = []
    for dep_id in getattr(task, "depends_on", []) or []:
        record = records.get(dep_id)
        if record is None:
            continue
        if record.status != "completed":
            failed.append(dep_id)
    return failed


def _friendly_task_error(exc: Exception | str) -> str:
    name = exc.__class__.__name__ if isinstance(exc, Exception) else ""
    text = str(exc)
    if name == "MaxTurnsExceeded" or "max turns" in text.lower():
        return "任务超过最大工具/模型轮数，已停止该专家以避免无限循环。"
    return text or name or "任务执行失败。"


def _task_failure_output(task, message: str) -> str:
    return (
        f"任务失败：{getattr(task, 'title', '') or getattr(task, 'id', 'task')}\n"
        f"原因：{message}\n"
        "系统已记录该失败并交给最终审核判断；如果需要继续，请缩小任务范围、减少工具读取，或让主脑重新规划。"
    )


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


def _run_agent_kwargs(run_agent, *, max_turns: int, approval_policy=None, stream_output=None) -> dict:
    kwargs = {"max_turns": max_turns}
    if approval_policy is not None and _run_agent_accepts_approval_policy(run_agent):
        kwargs["approval_policy"] = approval_policy
    if stream_output is not None and _run_agent_accepts_stream_output(run_agent):
        kwargs["stream_output"] = stream_output
    return kwargs


def _run_agent_accepts_approval_policy(run_agent) -> bool:
    try:
        parameters = inspect.signature(run_agent).parameters
    except (TypeError, ValueError):
        return True
    if "approval_policy" in parameters:
        return True
    return any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values())


def _run_agent_accepts_stream_output(run_agent) -> bool:
    try:
        parameters = inspect.signature(run_agent).parameters
    except (TypeError, ValueError):
        return True
    if "stream_output" in parameters:
        return True
    return any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values())


async def _create_task_agent(factory, task, *, execution_mode: str = ""):
    create_task_agent = factory.create_task_agent
    if _create_task_agent_accepts_execution_mode(create_task_agent):
        return await create_task_agent(task, execution_mode=execution_mode)
    return await create_task_agent(task)


def _create_task_agent_accepts_execution_mode(create_task_agent) -> bool:
    try:
        parameters = inspect.signature(create_task_agent).parameters
    except (TypeError, ValueError):
        return True
    if "execution_mode" in parameters:
        return True
    return any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values())
