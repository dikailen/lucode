import asyncio
import json
import os
import subprocess
from collections import defaultdict
from pathlib import Path

from mcp_servers import create_readonly_filesystem_server
from mcp_servers.core.operation_log import append_operation_log
from mcp_servers.network.web_search_mcp import web_search
from catalog_system.model_catalog import ModelRegistry
from planning.plan_reviewer import format_plan_review, review_plan
from planning.plan_validator import format_validation, validate_plan
from planning.planner import format_execution_plan, preview_plan
from planning.planner_schema import PlannerResult
from runtime.agents.factory import AgentFactory
from runtime.safety.auditor import audit_execution, audit_plan_review_failure, format_final_report
from runtime.safety.checkpoint import RollbackResult, create_checkpoint, rollback_checkpoint
from runtime.config.execution_mode import normalize_execution_mode
from runtime.memory.flywheel import FlywheelStore
from runtime.workspace.patch_ledger import PatchProposalLedger
from runtime.execution.pipeline import (
    PipelineRunState,
    apply_pipeline_gate,
    build_verification_report,
    format_gate_decision,
)
from runtime.safety.privacy import PrivacyPolicy
from runtime.safety.repair_loop import build_repair_request, should_retry
from runtime.workspace.run_workspace import RunWorkspace
from runtime.config.settings import RuntimeSettings


async def execute_dynamic_request(
    raw_user_input: str,
    project_root: Path,
    model_registry: ModelRegistry,
    mcp_manager,
    hooks,
    run_agent,
    show_plan: bool = False,
    settings: RuntimeSettings | None = None,
) -> str:
    """Plan and execute a request using dynamic Agents."""

    settings = settings or RuntimeSettings.from_env()
    privacy_policy = PrivacyPolicy(settings.privacy_mode)
    checkpoint = create_checkpoint(project_root)
    flywheel = FlywheelStore(project_root)
    current_input = raw_user_input
    last_output = ""
    last_audit = None
    max_attempts = 3

    for attempt in range(1, max_attempts + 1):
        output, audit = await _execute_dynamic_attempt(
            current_input,
            project_root,
            model_registry,
            mcp_manager,
            hooks,
            run_agent,
            show_plan=show_plan,
            settings=settings,
            privacy_policy=privacy_policy,
            flywheel=flywheel,
            attempt=attempt,
        )
        last_output = output
        last_audit = audit
        if audit is not None and checkpoint.mode == "git_dirty_protected":
            scoped_paths = _audit_files_touched(audit)
            if scoped_paths:
                checkpoint = create_checkpoint(project_root, scoped_paths=scoped_paths)
        if audit is None or audit.passed:
            return output
        if not should_retry(attempt, max_attempts, audit):
            break
        print(f"最终审核未通过，开始第 {attempt + 1} 轮重规划。")
        current_input = build_repair_request(raw_user_input, audit, attempt + 1)

    if last_audit is not None and not last_audit.passed:
        rollback = rollback_checkpoint(checkpoint)
        _record_failure_case_safely(flywheel, raw_user_input, max_attempts, last_audit, rollback)
        last_audit.rollback_happened = rollback.rolled_back
        last_audit.rollback_message = rollback.message
        return format_final_report(last_output, last_audit)

    return last_output or "主脑没有给出可执行路线。"


async def _execute_dynamic_attempt(
    raw_user_input: str,
    project_root: Path,
    model_registry: ModelRegistry,
    mcp_manager,
    hooks,
    run_agent,
    show_plan: bool,
    settings: RuntimeSettings,
    privacy_policy: PrivacyPolicy,
    flywheel: FlywheelStore,
    attempt: int,
) -> tuple[str, object | None]:
    refiner_model_id = (
        settings.select_model_id(model_registry, "query_refiner") if settings.query_refiner_enabled else None
    )
    planner_model_id = settings.select_model_id(model_registry, "orchestrator")
    synthesizer_model_id = settings.select_model_id(model_registry, "final_synthesizer")

    refined, plan = await preview_plan(
        raw_user_input,
        refiner_model=model_registry.get_model(refiner_model_id) if refiner_model_id else None,
        planner_model=model_registry.get_model(planner_model_id),
        hooks=hooks,
        refiner_enabled=settings.query_refiner_enabled,
    )
    gate_decision = apply_pipeline_gate(plan, refined.refined_request)
    run_state = PipelineRunState.create(refined.refined_request, plan)
    run_state.record_gate(gate_decision)
    validation = validate_plan(plan, privacy_policy=privacy_policy)
    review = review_plan(plan)
    if show_plan:
        if attempt > 1:
            print(f"========== 第 {attempt} 轮重规划 ==========")
        print(format_execution_plan(refined, plan, validation))
        print(format_plan_review(review))
        print(format_gate_decision(gate_decision))

    if not validation.valid:
        return (
            "主脑规划未通过校验，已停止执行。\n\n"
            f"{format_validation(validation)}\n\n"
            "你可以用 /plan 查看规划详情，或把问题说得更具体一点。"
        ), None

    if not review.approved:
        message = (
            "计划审查未通过，已停止执行，避免按不安全或不完整的计划修改项目。\n\n"
            f"{format_plan_review(review)}\n\n"
            "系统将把这些问题回传给主脑进行重规划。"
        )
        return message, audit_plan_review_failure(review)

    factory = AgentFactory(model_registry, mcp_manager)

    if plan.route_type == "direct_answer":
        return await _run_direct_answer(raw_user_input, plan, planner_model_id, factory, hooks, run_agent), None

    if plan.route_type == "clarify":
        return plan.clarifying_question or "这个问题还需要你补充一点信息。", None

    if plan.route_type == "single_agent":
        task = plan.tasks[0]
        if _can_fast_path_url_search(task):
            output = _run_url_search_fast_path(refined.refined_request, task)
            run_state.record_task_result(task, output)
            _record_flywheel_safely(flywheel, run_state)
            audit = audit_execution(plan, run_state, output)
            return format_final_report(output, audit), audit
        if _can_fast_path_git_status(task):
            output = _run_git_status_fast_path(project_root, task)
            run_state.record_task_result(task, output)
            _record_flywheel_safely(flywheel, run_state)
            audit = audit_execution(plan, run_state, output)
            return format_final_report(output, audit), audit

        agent = await factory.create_task_agent(task)
        workspace_context = _latest_workspace_context(project_root, task)
        try:
            result = await run_agent(
                agent,
                _task_prompt(refined.refined_request, task.instruction, workspace_context=workspace_context),
                hooks,
                max_turns=_max_turns_for_task(task),
            )
        except Exception as exc:
            run_state.record_task_error(task, exc)
            _record_flywheel_safely(flywheel, run_state)
            raise
        output = _with_verification_report(project_root, task, str(result.final_output), run_state)
        run_state.record_task_result(task, output)
        _record_flywheel_safely(flywheel, run_state)
        audit = audit_execution(plan, run_state, output)
        return format_final_report(output, audit), audit

    if plan.route_type == "multi_agent":
        try:
            output = await _run_multi_agent(
                refined.refined_request,
                plan,
                project_root,
                synthesizer_model_id,
                factory,
                hooks,
                run_agent,
                run_state,
                execution_mode=settings.execution_mode,
            )
        finally:
            _record_flywheel_safely(flywheel, run_state)
        audit = audit_execution(plan, run_state, output)
        return format_final_report(output, audit), audit

    return "主脑没有给出可执行路线。", None


async def _run_direct_answer(raw_user_input, plan: PlannerResult, model_id, factory, hooks, run_agent) -> str:
    agent = factory.create_direct_answer_agent(model_id, plan.direct_answer_instruction)
    result = await run_agent(agent, raw_user_input, hooks)
    return result.final_output


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
                    title, output = await _run_planned_task(
                        refined_request, task, project_root, factory, hooks, run_agent, run_state, ledger
                    )
                    workspace.write_task_output(task.id, title, output)
                    continue

                print(_format_parallel_batch_audit(group_id, batch))
                results = await asyncio.gather(
                    *(
                        _run_planned_task(
                            refined_request, task, project_root, factory, hooks, run_agent, run_state, ledger
                        )
                        for task in batch
                    )
                )
                for task, (title, output) in zip(batch, results):
                    workspace.write_task_output(task.id, title, output)

        async with create_readonly_filesystem_server(
            run_dir,
            "run_workspace_readonly",
        ) as run_workspace_server:
            synthesizer = factory.create_synthesizer_agent(model_id, run_workspace_server)
            synthesis_prompt = (
                "请读取当前运行工作目录中的所有任务输出文件，按照以下要求汇总：\n"
                f"{plan.synthesis_instruction}\n\n"
                "请输出面向用户的最终中文答案。"
            )
            result = await run_agent(synthesizer, synthesis_prompt, hooks, max_turns=10)
            return result.final_output
    finally:
        workspace.cleanup()


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


def _latest_workspace_context(project_root: Path, task) -> str:
    """Return a compact current workspace snapshot immediately before a task runs."""

    lines = ["最新项目状态："]
    read_set = [str(item).strip() for item in list(getattr(task, "read_set", []) or []) if str(item).strip()]
    write_intent = [
        str(item).strip() for item in list(getattr(task, "write_intent", []) or []) if str(item).strip()
    ]
    if read_set:
        lines.append("本任务声明读取范围：" + ", ".join(read_set[:8]))
    if write_intent:
        lines.append("本任务声明写入范围：" + ", ".join(write_intent[:8]))

    try:
        result = subprocess.run(
            ["git", "status", "--short"],
            cwd=project_root,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=10,
            shell=False,
        )
    except Exception as exc:
        lines.append(f"git status 暂不可用：{exc}")
        return "\n".join(lines)

    if result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip()
        lines.append(f"git status 暂不可用：{message[:300] or '未知错误'}")
        return "\n".join(lines)

    changed = _parse_git_status_short(result.stdout)
    if not changed:
        lines.append("当前 git 工作区没有改动。")
        return "\n".join(lines)

    lines.append("当前 git 工作区改动文件：")
    for item in changed[:12]:
        lines.append(f"- {item['path']}（{item['status']}）")
    if len(changed) > 12:
        lines.append(f"- 其余 {len(changed) - 12} 个改动文件已省略。")
    return "\n".join(lines)


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


def _can_run_group_in_parallel(tasks: list) -> bool:
    return len(_execution_batches_for_group(tasks)) == 1 and len(tasks) > 1


def _format_parallel_batch_audit(group_id: int, batch: list) -> str:
    parts = []
    for task in batch:
        writes = _normalized_write_intent(task)
        read_set = [str(item).strip().replace("\\", "/") for item in list(getattr(task, "read_set", []) or [])]
        scope = ", ".join(writes or [item for item in read_set if item] or ["只读/未声明写入"])
        parts.append(f"{getattr(task, 'id', 'unknown')}[{scope}]")
    return (
        f"执行并行组 {group_id}：安全并行启动 {len(batch)} 个临时 Agent。"
        "依据：任务无依赖关系，写入路径无重叠、无父子包含关系。"
        f"批次范围：{'；'.join(parts)}"
    )


def _execution_batches_for_mode(tasks: list, execution_mode: str) -> list[list]:
    mode = normalize_execution_mode(execution_mode)
    if mode == "full":
        return _execution_batches_for_group(tasks)
    return [[task] for task in tasks]


def _execution_batches_for_group(tasks: list) -> list[list]:
    if not tasks:
        return []

    batches: list[list] = []
    current_batch: list = []
    current_writes: set[str] = set()

    for task in tasks:
        if not current_batch:
            current_batch = [task]
            current_writes = set(_normalized_write_intent(task))
            if _requires_serial_execution(task):
                batches.append(current_batch)
                current_batch = []
                current_writes = set()
            continue

        if _task_conflicts_with_batch(task, current_batch, current_writes):
            batches.append(current_batch)
            current_batch = [task]
            current_writes = set(_normalized_write_intent(task))
            if _requires_serial_execution(task):
                batches.append(current_batch)
                current_batch = []
                current_writes = set()
            continue

        current_batch.append(task)
        current_writes.update(_normalized_write_intent(task))

    if current_batch:
        batches.append(current_batch)
    return batches


def _task_conflicts_with_batch(task, batch: list, batch_writes: set[str]) -> bool:
    task_deps = set(getattr(task, "depends_on", []) or [])
    batch_ids = {getattr(existing, "id", "") for existing in batch}
    if task_deps & batch_ids:
        return True
    for existing in batch:
        existing_deps = set(getattr(existing, "depends_on", []) or [])
        if getattr(task, "id", "") in existing_deps:
            return True

    if _requires_serial_execution(task):
        return True

    writes = set(_normalized_write_intent(task))
    if _write_sets_conflict(writes, batch_writes):
        return True

    for existing in batch:
        if _requires_serial_execution(existing):
            return True
        if _write_sets_conflict(writes, set(_normalized_write_intent(existing))):
            return True
    return False


def _requires_serial_execution(task) -> bool:
    if "safe_backup" in task.mcp:
        return True
    if "workspace_edit" in task.mcp and not _normalized_write_intent(task):
        return True
    for mcp_id in task.mcp:
        if mcp_id not in {"workspace_edit", "project_filesystem_readonly", "code_locator", "web_search", "git_tools"}:
            return True
    return False


def _normalized_write_intent(task) -> list[str]:
    values = []
    for item in list(getattr(task, "write_intent", []) or []):
        value = _normalize_write_path(item)
        if value:
            values.append(value.lower())
    return values


def _normalize_write_path(path: str) -> str:
    value = str(path or "").strip().replace("\\", "/")
    while "//" in value:
        value = value.replace("//", "/")
    if value.startswith("./"):
        value = value[2:]
    return value.strip("/")


def _write_sets_conflict(first: set[str], second: set[str]) -> bool:
    for left in first:
        for right in second:
            if _write_paths_conflict(left, right):
                return True
    return False


def _write_paths_conflict(left: str, right: str) -> bool:
    left = _normalize_write_path(left).lower()
    right = _normalize_write_path(right).lower()
    if not left or not right:
        return False
    if left == right:
        return True
    return left.startswith(right + "/") or right.startswith(left + "/")


def _max_turns_for_task(task) -> int:
    if "web_search" in task.mcp and _is_url_only_task(task):
        return 2
    if task.mcp:
        return 12
    return 6


def _is_url_only_task(task) -> bool:
    text = f"{task.title}\n{task.instruction}".lower()
    return any(
        marker in text
        for marker in [
            "url",
            "urls",
            "链接",
            "地址",
            "top urls",
            "仅返回",
            "只返回",
        ]
    )


def _can_fast_path_url_search(task) -> bool:
    return task.skill_id == "project_explorer" and task.mcp == ["web_search"] and _is_url_only_task(task)


def _can_fast_path_git_status(task) -> bool:
    if task.mcp != ["git_tools"]:
        return False
    text = f"{task.title}\n{task.instruction}".lower()
    wants_status = any(
        marker in text
        for marker in [
            "git status",
            "working tree",
            "changed file",
            "changed files",
            "changes",
            "status",
            "工作区",
            "改动",
            "文件名",
            "变更",
        ]
    )
    asks_commit = ("commit" in text and "do not commit" not in text) or (
        "提交" in text and "不要提交" not in text and "不提交" not in text
    )
    return wants_status and not asks_commit


def _run_git_status_fast_path(project_root: Path, task) -> str:
    print("执行优化：git 只读状态查询直接调用 git status，避免模型二次返场。")
    result = subprocess.run(
        ["git", "status", "--short"],
        cwd=project_root,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=30,
        shell=False,
    )
    if result.returncode != 0:
        output = (
            "git status 执行失败。\n"
            f"returncode：{result.returncode}\n"
            f"stderr：{result.stderr.strip() or '无'}"
        )
        _log_runtime_fast_path(
            project_root,
            tool="git_status",
            action="git_status",
            task=task,
            params={"task_id": getattr(task, "id", ""), "command": "git status --short"},
            status="failed",
            result_summary=output,
            error=result.stderr.strip() or output,
        )
        return output

    parsed = _parse_git_status_short(result.stdout)
    if not parsed:
        output = "当前 git 工作区没有改动。"
        _log_runtime_fast_path(
            project_root,
            tool="git_status",
            action="git_status",
            task=task,
            params={"task_id": getattr(task, "id", ""), "command": "git status --short", "changed_files": 0},
            status="success",
            result_summary=output,
        )
        return output

    lines = ["当前 git 工作区改动文件："]
    for item in parsed:
        lines.append(f"- {item['path']}（{item['status']}）")
    output = "\n".join(lines)
    _log_runtime_fast_path(
        project_root,
        tool="git_status",
        action="git_status",
        task=task,
        params={
            "task_id": getattr(task, "id", ""),
            "command": "git status --short",
            "changed_files": len(parsed),
        },
        status="success",
        result_summary=output,
    )
    return output


def _parse_git_status_short(stdout: str) -> list[dict[str, str]]:
    items = []
    status_names = {
        "M": "已修改",
        "A": "已新增",
        "D": "已删除",
        "R": "已重命名",
        "C": "已复制",
        "U": "冲突",
        "?": "未跟踪",
        "!": "已忽略",
    }
    for line in stdout.splitlines():
        if not line.strip():
            continue
        code = line[:2]
        raw_path = line[3:].strip() if len(line) > 3 else ""
        if " -> " in raw_path:
            raw_path = raw_path.split(" -> ", 1)[1].strip()
        status_key = code.strip()[:1] or "?"
        items.append(
            {
                "path": raw_path,
                "status": status_names.get(status_key, code.strip() or "未知"),
            }
        )
    return items


def _run_url_search_fast_path(refined_request: str, task) -> str:
    print("执行优化：URL-only 联网任务直接调用 web_search 一次，避免模型重复搜索。")
    query = _build_url_search_query(refined_request, task)
    print("工具调用：runtime -> web_search")
    raw_result = web_search(query, max_results=5)
    print(f"工具完成：runtime <- web_search（结果约 {len(raw_result)} 字符）")

    try:
        payload = json.loads(raw_result)
    except json.JSONDecodeError:
        _log_runtime_fast_path(
            Path.cwd(),
            tool="web_search",
            action="web_search",
            task=task,
            params={"task_id": getattr(task, "id", ""), "query": query, "max_results": 5},
            status="success",
            result_summary=raw_result[:500],
        )
        return raw_result

    urls = [item.get("url") for item in payload.get("results", []) if item.get("url")]
    if not urls:
        output = "没有搜索到可靠 URL。"
        _log_runtime_fast_path(
            Path.cwd(),
            tool="web_search",
            action="web_search",
            task=task,
            params={"task_id": getattr(task, "id", ""), "query": query, "max_results": 5, "url_count": 0},
            status="success",
            result_summary=output,
        )
        return output

    output = "\n".join(f"- {url}" for url in urls)
    _log_runtime_fast_path(
        Path.cwd(),
        tool="web_search",
        action="web_search",
        task=task,
        params={
            "task_id": getattr(task, "id", ""),
            "query": query,
            "max_results": 5,
            "url_count": len(urls),
        },
        status="success",
        result_summary=output,
    )
    return output


def _runtime_operation_log(project_root: Path) -> Path:
    override = str(os.environ.get("AGENTS_OPERATION_LOG_PATH") or "").strip()
    if override:
        return Path(override)
    return project_root.resolve() / ".agent_quarantine" / "operations.jsonl"


def _log_runtime_fast_path(
    project_root: Path,
    *,
    tool: str,
    action: str,
    task,
    params: dict,
    status: str,
    result_summary: str,
    error: str = "",
) -> None:
    append_operation_log(
        _runtime_operation_log(project_root),
        tool=f"runtime_fast_path.{tool}",
        action=action,
        reason=f"runtime fast path for task {getattr(task, 'id', '') or 'unknown'}",
        status=status,
        params_summary=params,
        approval_required=False,
        approval_note="Read-only runtime fast path.",
        result_summary=result_summary,
        error=error,
    )


def _build_url_search_query(refined_request: str, task) -> str:
    text = f"{refined_request}\n{task.title}\n{task.instruction}"
    lowered = text.lower()
    if "openai" in lowered and "mcp" in lowered and ("agents" in lowered or "sdk" in lowered):
        return "OpenAI Agents SDK MCP documentation"
    return text[:300]


def _record_flywheel_safely(flywheel: FlywheelStore, run_state: PipelineRunState) -> None:
    try:
        flywheel.record_pipeline_state(run_state)
    except Exception as exc:
        print(f"Flywheel 记录失败，已跳过：{exc}")


def _record_failure_case_safely(
    flywheel: FlywheelStore,
    raw_user_input: str,
    attempt_count: int,
    audit,
    rollback: RollbackResult,
) -> None:
    try:
        flywheel.record_failure_case(
            user_request=raw_user_input,
            attempt_count=attempt_count,
            models_used=list(getattr(audit, "models_used", []) or []),
            files_touched=list(getattr(audit, "files_touched", []) or []),
            failure_reasons=list(audit.remaining_issues),
            rollback_status="rolled_back" if rollback.rolled_back else "not_rolled_back",
            lesson=(
                "自动修复达到上限后仍未通过 Final Auditor。"
                "后续规划应缩小任务范围、补充验收标准或换用更强模型。"
            ),
        )
    except Exception as exc:
        print(f"Failure Memory 记录失败，已跳过：{exc}")


def _audit_files_touched(audit) -> list[str]:
    values = []
    for raw in list(getattr(audit, "files_touched", []) or []):
        path = str(raw or "").strip().replace("\\", "/")
        if path and path not in values:
            values.append(path)
    return values


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
