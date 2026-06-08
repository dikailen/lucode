from pathlib import Path
import re

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
from runtime.execution.execution_contract import normalize_execution_contract
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
from runtime.execution.progress import _print_progress_snapshot, _safe_print
from runtime.execution.single_agent_runner import _run_single_agent
from runtime.execution.task_runner import (
    _dependency_context_for_task,
    _direct_answer_input_with_inline_context,
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
from runtime.config.model_selection import model_usable_for_task
from runtime.events import ExecutionEventBus
from runtime.ui.plan_display import planning_status, render_compact_plan_summary


class DynamicExecutionResult(str):
    """String-compatible execution output with optional runtime context metadata."""

    def __new__(cls, value: str, *, run_context_summary: str = ""):
        obj = str.__new__(cls, str(value or ""))
        obj.run_context_summary = str(run_context_summary or "")
        return obj


async def execute_dynamic_request(
    raw_user_input: str,
    project_root: Path,
    model_registry: ModelRegistry,
    mcp_manager,
    hooks,
    run_agent,
    show_plan: bool = False,
    settings: RuntimeSettings | None = None,
    display_input: str | None = None,
    output_controller=None,
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
            display_input=display_input,
            output_controller=output_controller,
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
        return DynamicExecutionResult(
            format_final_report(last_output, last_audit),
            run_context_summary=str(getattr(last_output, "run_context_summary", "") or ""),
        )

    return last_output or DynamicExecutionResult("主脑没有给出可执行路线。")


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
    display_input: str | None = None,
    output_controller=None,
) -> tuple[str, object | None]:
    visible_input = str(display_input or raw_user_input or "").strip()
    refiner_model_id = (
        settings.select_model_id(model_registry, "query_refiner") if settings.query_refiner_enabled else None
    )
    planner_model_id = settings.select_model_id(model_registry, "orchestrator")
    synthesizer_model_id = settings.select_model_id(model_registry, "final_synthesizer")
    event_bus = ExecutionEventBus()
    event_bus.emit(
        "PlanningStarted",
        "开始规划本轮任务",
        mode=settings.execution_mode,
        agent="orchestrator",
        payload={"planner_model_id": planner_model_id},
    )
    try:
        with planning_status(visible_input, mode=settings.execution_mode, enabled=show_plan):
            refined, plan = await preview_plan(
                raw_user_input,
                refiner_model=model_registry.get_model(refiner_model_id) if refiner_model_id else None,
                planner_model=model_registry.get_model(planner_model_id),
                hooks=hooks,
                refiner_enabled=settings.query_refiner_enabled,
            )
    except Exception as exc:
        event_bus.emit(
            "PlanningFailed",
            str(exc),
            mode=settings.execution_mode,
            agent="orchestrator",
            status="failed",
            payload={"planner_model_id": planner_model_id, "reason": str(exc)[:200]},
        )
        return DynamicExecutionResult(
            format_planning_error(exc, planner_model_id=planner_model_id),
            run_context_summary=_render_event_summary(event_bus),
        ), None
    _apply_executor_model_defaults(plan, settings, model_registry)
    execution_contract = normalize_execution_contract(
        plan,
        "\n".join([raw_user_input, refined.refined_request]),
        mode=settings.execution_mode,
    )
    gate_decision = apply_pipeline_gate(plan, refined.refined_request)
    run_state = PipelineRunState.create(
        refined.refined_request,
        plan,
        project_root=project_root,
        mode=settings.execution_mode,
        output_controller=output_controller,
    )
    run_state.event_bus = event_bus
    run_state.model_labels = _model_label_map(
        model_registry,
        [planner_model_id, synthesizer_model_id, *(getattr(task, "model", "") for task in plan.tasks)],
    )
    run_state.output_controller.enter_planning("planning completed")
    run_state.emit_event(
        "ExecutionContractApplied",
        "执行契约已收口",
        mode=settings.execution_mode,
        agent="supervisor",
        status="completed",
        payload=execution_contract.to_dict(),
    )
    run_state.emit_event(
        "PlanningCompleted",
        "规划完成",
        mode=settings.execution_mode,
        agent="orchestrator",
        status="completed",
        payload={"route_type": plan.route_type, "task_count": len(plan.tasks)},
    )
    run_state.record_gate(gate_decision)
    validation = validate_plan(plan, privacy_policy=privacy_policy)
    review = review_plan(plan)
    if show_plan:
        if attempt > 1:
            print(f"========== 第 {attempt} 轮重规划 ==========")
        detail_selector = _save_plan_detail(
            project_root,
            refined,
            plan,
            validation,
            review,
            gate_decision,
        )
        _safe_print(
            render_compact_plan_summary(
                refined,
                plan,
                validation,
                review,
                gate_decision,
                mode=settings.execution_mode,
                detail_selector=detail_selector,
            )
        )

    if not validation.valid:
        run_state.output_controller.enter_failed("plan validation failed")
        return (
            _dynamic_result(
            "主脑规划未通过校验，已停止执行。\n\n"
            f"{format_validation(validation)}\n\n"
            "你可以用 /plan 查看规划详情，或把问题说得更具体一点。",
            run_state,
            ),
            None,
        )

    if not review.approved:
        run_state.output_controller.enter_failed("plan review failed")
        message = (
            "计划审查未通过，已停止执行，避免按不安全或不完整的计划修改项目。\n\n"
            f"{format_plan_review(review)}\n\n"
            "系统将把这些问题回传给主脑进行重规划。"
        )
        return _dynamic_result(message, run_state), audit_plan_review_failure(review)

    factory = AgentFactory(model_registry, mcp_manager)

    if plan.route_type == "direct_answer":
        run_state.output_controller.enter_running(reason="direct answer")
        direct_input = _direct_answer_input_with_inline_context(
            raw_user_input,
            refined.refined_request,
            project_root,
            planner_model_id,
            run_state,
        )
        output = await _run_direct_answer(
            direct_input,
            plan,
            planner_model_id,
            factory,
            hooks,
            run_agent,
            execution_mode=settings.execution_mode,
        )
        run_state.output_controller.enter_completed("direct answer completed")
        return _dynamic_result(output, run_state), None

    if plan.route_type == "clarify":
        run_state.output_controller.enter_completed("clarification requested")
        return _dynamic_result(plan.clarifying_question or "这个问题还需要你补充一点信息。", run_state), None

    if plan.route_type == "single_agent":
        run_state.output_controller.enter_running(reason="single agent")
        output, audit = await _run_single_agent(
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
        if getattr(audit, "passed", True):
            run_state.output_controller.enter_completed("single agent completed")
        else:
            run_state.output_controller.enter_failed("single agent audit failed")
        return _dynamic_result(output, run_state), audit

    if plan.route_type == "multi_agent":
        run_state.output_controller.enter_running(reason="multi agent")
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
                approval_policy_factory=_full_mode_approval_policy_factory(settings.execution_mode),
            )
        finally:
            _record_flywheel_safely(flywheel, run_state)
        audit = audit_execution(plan, run_state, output)
        if getattr(audit, "passed", True):
            run_state.output_controller.enter_completed("multi agent completed")
        else:
            run_state.output_controller.enter_failed("multi agent audit failed")
        return _dynamic_result(format_final_report(output, audit), run_state), audit

    run_state.output_controller.enter_failed("unknown route")
    return _dynamic_result("主脑没有给出可执行路线。", run_state), None


def _dynamic_result(output: str, run_state: PipelineRunState | None) -> DynamicExecutionResult:
    run_context = getattr(run_state, "run_context", None)
    summary = ""
    if run_context is not None and hasattr(run_context, "render_for_task"):
        try:
            summary = run_context.render_for_task()
        except Exception:
            summary = ""
    event_summary = _render_event_summary(getattr(run_state, "event_bus", None))
    if event_summary:
        summary = "\n\n".join(part for part in [summary, event_summary] if part)
    return DynamicExecutionResult(str(output or ""), run_context_summary=summary)


def _save_plan_detail(
    project_root: Path,
    refined,
    plan,
    validation,
    review,
    gate_decision,
) -> str:
    selector = "plan-last"
    try:
        from runtime.history.expand_store import ExpandBlockStore

        detail = "\n".join(
            [
                format_execution_plan(refined, plan, validation),
                format_plan_review(review),
                format_gate_decision(gate_decision),
            ]
        )
        ExpandBlockStore(project_root).save_text(
            selector,
            detail,
            kind="plan",
            title="上一轮完整规划",
            preview=f"{getattr(plan, 'route_type', '')} · {len(getattr(plan, 'tasks', []) or [])} task(s)",
        )
    except Exception:
        return ""
    return selector


def _full_mode_approval_policy_factory(execution_mode: str):
    if str(execution_mode or "").strip().lower() != "full":
        return None

    from runtime.agent.approval_policy import FullModeApprovalPolicy

    return FullModeApprovalPolicy.from_task


def _render_event_summary(event_bus) -> str:
    if event_bus is None or not hasattr(event_bus, "snapshot"):
        return ""
    try:
        from runtime.ui.event_render import render_execution_events

        events = event_bus.snapshot()
        if not events:
            return ""
        return render_execution_events(events, limit=8)
    except Exception:
        return ""


def format_planning_error(exc: Exception, planner_model_id: str = "") -> str:
    message = str(exc).strip() or exc.__class__.__name__
    hint = "请检查主脑模型配置、Provider base_url 和模型兼容性。"
    if "tuple" in message and "choices" in message:
        hint = (
            "模型服务返回格式与当前 OpenAI-compatible 适配器不兼容。"
            "建议使用 /models brain 主脑 切换到已验证模型，"
            "或运行 /models probe 重新探测该模型，并检查自定义中转 base_url 是否为 /v1 兼容地址。"
        )
    return (
        "规划阶段失败，Lucode 已停止本轮执行，项目文件没有被修改。\n"
        f"当前主脑模型：{planner_model_id or '未知'}\n"
        f"错误类型：{exc.__class__.__name__}\n"
        f"原因：{message}\n"
        f"建议：{hint}"
    )


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

    for task in plan.tasks:
        needs_tools = bool(task.mcp)
        if not task.model or not _task_model_is_usable(
            model_registry,
            task.model,
            privacy_mode=getattr(settings, "privacy_mode", "local_first"),
            requires_tools=needs_tools,
        ):
            if executor_info and not _model_info_usable_for_task(
                executor_info,
                privacy_mode=getattr(settings, "privacy_mode", "local_first"),
                requires_tools=needs_tools,
            ):
                continue
            if not executor_info and needs_tools:
                continue
            task.model = executor_model_id


def _model_label_map(model_registry, model_ids) -> dict[str, str]:
    labels: dict[str, str] = {}
    for model_id in model_ids:
        clean_id = str(model_id or "").strip()
        if not clean_id or clean_id in labels:
            continue
        labels[clean_id] = _friendly_model_label(model_registry, clean_id)
    return labels


def _friendly_model_label(model_registry, model_id: str) -> str:
    try:
        info = model_registry.get_model_info(model_id)
    except Exception:
        info = {}
    for key in ("display_name_zh", "display_name", "model_name", "provider_ref"):
        value = str((info or {}).get(key) or "").strip()
        if value:
            return _prettify_model_label(value, info or {})
    return model_id


def _prettify_model_label(value: str, info: dict) -> str:
    text = str(value or "").strip()
    provider = str((info or {}).get("provider") or "").strip()
    model_name = str((info or {}).get("model_name") or "").strip()
    if provider and model_name and text.lower() == f"{provider} {model_name}".lower():
        return _provider_model_label(provider, model_name)
    if provider and model_name and text.lower() == f"{provider} {model_name.replace('-', '_')}".lower():
        return _provider_model_label(provider, model_name)
    return _title_model_token(text) if _looks_like_model_slug(text) else text


def _looks_like_model_slug(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_.:/ -]+", str(value or "").strip())) and any(
        marker in str(value or "") for marker in ("_", "-", "/")
    )


def _title_model_token(value: str) -> str:
    text = str(value or "").strip()
    if "/" in text:
        text = text.split("/")[-1]
    parts = [part for part in re.split(r"[_\-\s]+", text) if part]
    if not parts:
        return text
    return " ".join(_title_model_part(part) for part in parts)


def _provider_model_label(provider: str, model_name: str) -> str:
    provider_label = _title_model_part(provider)
    model_label = _title_model_token(model_name)
    provider_prefix = _title_model_token(provider)
    if model_label.lower().startswith(provider_prefix.lower() + " "):
        return model_label
    return f"{provider_label} {model_label}"


def _title_model_part(part: str) -> str:
    upper_values = {"v1", "v2", "v3", "v4", "k2", "r1", "gpt", "api"}
    text = str(part or "")
    special = {"deepseek": "DeepSeek", "openai": "OpenAI", "qwen": "Qwen", "kimi": "Kimi"}
    if text.lower() in special:
        return special[text.lower()]
    if text.lower() in upper_values:
        return text.upper()
    if text.isupper():
        return text
    return text[:1].upper() + text[1:]


def _task_model_is_usable(
    model_registry,
    model_id: str,
    *,
    privacy_mode: str = "local_first",
    requires_tools: bool = False,
) -> bool:
    if not model_id:
        return False
    try:
        info = model_registry.get_model_info(model_id)
    except Exception:
        return False
    return _model_info_usable_for_task(
        info,
        privacy_mode=privacy_mode,
        requires_tools=requires_tools,
    )


def _model_info_usable_for_task(
    info: dict | None,
    *,
    privacy_mode: str = "local_first",
    requires_tools: bool = False,
) -> bool:
    if not info:
        return False
    return model_usable_for_task(
        info,
        PrivacyPolicy(privacy_mode),
        requires_tools=requires_tools,
    )
