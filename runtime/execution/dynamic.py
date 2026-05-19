from pathlib import Path

from catalog_system.model_catalog import ModelRegistry
from planning.plan_reviewer import format_plan_review, review_plan
from planning.plan_validator import format_validation, validate_plan
from planning.planner import format_execution_plan, preview_plan
from runtime.agents.factory import AgentFactory
from runtime.safety.auditor import audit_execution, audit_plan_review_failure, format_final_report
from runtime.safety.checkpoint import create_checkpoint, rollback_checkpoint
from runtime.memory.flywheel import FlywheelStore
from runtime.execution.pipeline import (
    PipelineRunState,
    apply_pipeline_gate,
    format_gate_decision,
)
# Keep private imports available here as compatibility re-exports while dynamic.py is being split.
from runtime.execution.fast_paths import (
    _build_url_search_query,
    _can_fast_path_git_status,
    _can_fast_path_url_search,
    _is_url_only_task,
    _log_runtime_fast_path,
    _parse_git_status_short,
    _run_git_status_fast_path,
    _run_url_search_fast_path,
    _runtime_operation_log,
)
from runtime.execution.failure_memory import (
    _audit_files_touched,
    _record_failure_case_safely,
    _record_flywheel_safely,
)
from runtime.execution.inline_context import (
    _excerpt_query_tokens,
    _inline_project_file_context,
    _latest_workspace_context,
    _read_project_file_excerpt,
    _render_numbered_lines,
    _resolve_explicit_project_file_paths,
    _resolve_project_file_candidate,
    _safe_inline_project_file,
    _should_inline_readonly_file_task,
    _truncate_excerpt,
)
from runtime.execution.multi_agent_runner import (
    _ordered_tasks_for_execution,
    _run_multi_agent,
    _tasks_by_parallel_group,
)
from runtime.execution.parallel_scheduler import (
    _can_run_group_in_parallel,
    _execution_batches_for_group,
    _execution_batches_for_mode,
    _format_parallel_batch_audit,
    _normalize_write_path,
    _normalized_write_intent,
    _requires_serial_execution,
    _task_conflicts_with_batch,
    _write_paths_conflict,
    _write_sets_conflict,
)
from runtime.execution.progress import _print_progress_snapshot
from runtime.execution.single_agent_runner import _run_single_agent
from runtime.execution.task_runner import (
    _dependency_context_for_task,
    _max_turns_for_task,
    _run_direct_answer,
    _run_planned_task,
    _task_output_map,
    _task_prompt,
    _with_verification_report,
)
from runtime.safety.privacy import PrivacyPolicy
from runtime.safety.repair_loop import build_repair_request, should_retry
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
    _apply_executor_model_defaults(plan, settings, model_registry)
    gate_decision = apply_pipeline_gate(plan, refined.refined_request)
    run_state = PipelineRunState.create(refined.refined_request, plan)
    run_state.record_gate(gate_decision)
    validation = validate_plan(plan, privacy_policy=privacy_policy)
    review = review_plan(plan)
    if show_plan:
        _print_progress_snapshot(run_state, mode=settings.execution_mode, attempt=attempt, active="规划完成")
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
        return await _run_single_agent(
            refined.refined_request,
            plan,
            project_root,
            factory,
            hooks,
            run_agent,
            run_state,
            flywheel,
            execution_mode=settings.execution_mode,
            show_plan=show_plan,
            attempt=attempt,
        )

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
                show_progress=show_plan,
                attempt=attempt,
            )
        finally:
            _record_flywheel_safely(flywheel, run_state)
        audit = audit_execution(plan, run_state, output)
        return format_final_report(output, audit), audit

    return "主脑没有给出可执行路线。", None


def _apply_executor_model_defaults(plan, settings, model_registry) -> None:
    """Fill empty or invalid task.model with executor default model.

    Conservative strategy (v1):
    1. If task.model is empty, fill executor default.
    2. If task.model is not in configured models and executor has a usable model, replace.
    3. If task.model is already a valid configured model, leave it alone.
    4. If task requires MCP/tools but executor model doesn't support tools, keep original
       model and log a warning.
    """
    if not plan.tasks:
        return

    try:
        executor_model_id = settings.select_model_id(model_registry, "executor")
    except Exception:
        executor_model_id = None

    if not executor_model_id:
        return

    try:
        executor_info = model_registry.get_model_info(executor_model_id)
    except Exception:
        executor_info = None

    executor_supports_tools = executor_info.get("supports_tools", True) if executor_info else True

    for task in plan.tasks:
        if not task.model or not _task_model_is_usable(model_registry, task.model):
            needs_tools = bool(task.mcp)
            if needs_tools and not executor_supports_tools:
                # Executor model doesn't support tools, keep a usable explicit task model.
                if task.model and _task_model_is_usable(model_registry, task.model):
                    continue
            task.model = executor_model_id
            if not needs_tools or executor_supports_tools:
                continue


def _task_model_is_usable(model_registry, model_id: str) -> bool:
    if not model_id:
        return False
    try:
        info = model_registry.get_model_info(model_id)
    except Exception:
        definitions = getattr(model_registry, "definitions", {}) or {}
        return model_id in definitions
    if info.get("configured") is False:
        return False
    probe = info.get("probe") or {}
    status = str(probe.get("status") or "").strip()
    return status not in {
        "chat_failed",
        "probe_failed",
        "service_unavailable",
        "model_missing",
        "capability_probe_failed",
    }
