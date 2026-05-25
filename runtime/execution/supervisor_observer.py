from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

from planning.planner_schema import PlannerResult
from runtime.agent.spec import TaskSpec
from runtime.agent.supervisor import ContextPack, ResourceLease, SupervisorDecision, SupervisorPlanView
from runtime.config.execution_mode import normalize_execution_mode


def build_supervisor_plan_view(plan: PlannerResult, *, mode: str = "full") -> SupervisorPlanView:
    """Build a read-only supervised-full view over the existing plan."""

    normalized_mode = normalize_execution_mode(mode)
    task_specs = [TaskSpec.from_planned_task(task, mode_hint=normalized_mode) for task in plan.tasks]
    leases = _resource_leases_for_tasks(plan.tasks)
    conflicts = _detect_resource_conflicts(leases)
    context_packs = _context_packs_for_tasks(plan.tasks, leases)
    decisions = _decisions_for_conflicts(conflicts)
    notes = _notes_for_view(task_specs, conflicts)
    return SupervisorPlanView(
        mode=normalized_mode,
        route_type=str(plan.route_type or ""),
        task_specs=task_specs,
        resource_leases=leases,
        context_packs=context_packs,
        decisions=decisions,
        conflicts=conflicts,
        notes=notes,
    )


def emit_supervisor_observation(plan: PlannerResult, *, mode: str, event_bus=None) -> SupervisorPlanView | None:
    """Emit a non-blocking observation event for full mode, without changing execution."""

    normalized_mode = normalize_execution_mode(mode)
    if normalized_mode != "full" or plan.route_type != "multi_agent":
        return None
    view = build_supervisor_plan_view(plan, mode=normalized_mode)
    if event_bus is not None and hasattr(event_bus, "emit"):
        try:
            event_bus.emit(
                "SupervisorObservation",
                _event_message_for_view(view),
                mode=normalized_mode,
                agent="supervisor",
                status="warning" if view.has_conflicts else "completed",
                payload={
                    "task_count": len(view.task_specs),
                    "lease_count": len(view.resource_leases),
                    "conflict_count": len(view.conflicts),
                    "conflicts": list(view.conflicts),
                    "decision_count": len(view.decisions),
                },
            )
        except Exception:
            return view
    return view


def render_supervisor_context_for_workers(view: SupervisorPlanView | None) -> str:
    """Render the supervisor contract that every full-mode worker should see."""

    if view is None or not view.context_packs:
        return ""
    read_scopes: list[str] = []
    write_scopes: list[str] = []
    for lease in view.resource_leases:
        if lease.lease_type == "write":
            write_scopes.append(lease.resource_id)
        else:
            read_scopes.append(lease.resource_id)
    conflict_count = len(view.conflicts)
    return (
        "主管上下文包："
        f"团队任务 {len(view.task_specs)} 个；"
        f"公共读取范围 {_join_unique(read_scopes) or '未声明'}；"
        f"写入租约 {_join_unique(write_scopes) or '无'}；"
        f"资源冲突 {conflict_count} 个。"
        "WorkerReport 必须说明 status、summary、evidence_refs、files_read、files_written、blockers；"
        "worker 只处理 TaskSpec 授权范围，信息不足时报告限制，不要自行扩权。"
    )


def _resource_leases_for_tasks(tasks: list) -> list[ResourceLease]:
    leases: list[ResourceLease] = []
    for task in tasks:
        task_id = str(getattr(task, "id", "") or "")
        parallel_group = int(getattr(task, "parallel_group", 1) or 1)
        for resource in _string_list(getattr(task, "read_set", [])):
            leases.append(ResourceLease.read(_normalize_resource(resource), owner_task_id=task_id, parallel_group=parallel_group))
        for resource in _string_list(getattr(task, "write_intent", [])):
            leases.append(
                ResourceLease.write(_normalize_resource(resource), owner_task_id=task_id, parallel_group=parallel_group)
            )
        mcp = set(_string_list(getattr(task, "mcp", [])))
        if "git_tools" in mcp:
            leases.append(ResourceLease.read("git:index", owner_task_id=task_id, parallel_group=parallel_group, reason="git_tools"))
        if "workspace_edit" in mcp and not _string_list(getattr(task, "write_intent", [])):
            leases.append(
                ResourceLease.write(
                    "workspace:*",
                    owner_task_id=task_id,
                    parallel_group=parallel_group,
                    reason="workspace_edit without declared write_intent",
                )
            )
    return leases


def _context_packs_for_tasks(tasks: list, leases: list[ResourceLease]) -> list[ContextPack]:
    if not tasks:
        return []
    shared_files = []
    seen_resources: set[str] = set()
    for lease in leases:
        if lease.lease_type != "read":
            continue
        resource = _normalize_resource(lease.resource_id)
        if not resource or resource in seen_resources:
            continue
        seen_resources.add(resource)
        shared_files.append(
            {
                "path": resource,
                "lease_type": "read",
                "owner_task_id": lease.owner_task_id,
                "parallel_group": lease.parallel_group,
            }
        )
    source_task_ids = [str(getattr(task, "id", "") or "") for task in tasks if str(getattr(task, "id", "") or "")]
    if not shared_files and not source_task_ids:
        return []
    return [
        ContextPack(
            pack_id="supervisor_context_pack",
            summary=(
                "主管公共侦察包：统一列出团队任务的读取范围、资源租约和 WorkerReport 汇报契约；"
                "worker 必须按授权范围执行。"
            ),
            shared_files=shared_files,
            source_task_ids=source_task_ids,
        )
    ]


def _detect_resource_conflicts(leases: list[ResourceLease]) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    by_group: dict[int, list[ResourceLease]] = {}
    for lease in leases:
        by_group.setdefault(lease.parallel_group, []).append(lease)

    for group_id, group_leases in sorted(by_group.items()):
        writes = [lease for lease in group_leases if lease.lease_type == "write"]
        for index, current in enumerate(writes):
            for other in writes[index + 1 :]:
                if current.owner_task_id == other.owner_task_id:
                    continue
                if _resources_conflict(current.resource_id, other.resource_id):
                    conflicts.append(
                        {
                            "kind": "write_conflict",
                            "parallel_group": group_id,
                            "task_ids": sorted({current.owner_task_id, other.owner_task_id}),
                            "resources": sorted({current.resource_id, other.resource_id}),
                            "severity": "warning",
                            "message": "Parallel workers declare overlapping write resources.",
                        }
                    )
    return _dedupe_conflicts(conflicts)


def _decisions_for_conflicts(conflicts: list[dict[str, Any]]) -> list[SupervisorDecision]:
    if not conflicts:
        return [
            SupervisorDecision(
                action="observe",
                reason="Supervisor v0 only records the plan view; execution scheduling remains unchanged.",
                severity="info",
            )
        ]
    return [
        SupervisorDecision(
            action="observe_conflict",
            reason="Supervisor v0 detected possible resource conflicts but did not alter execution.",
            affected_task_ids=sorted({task_id for conflict in conflicts for task_id in conflict.get("task_ids", [])}),
            resource_conflicts=conflicts,
            severity="warning",
        )
    ]


def _notes_for_view(task_specs: list[TaskSpec], conflicts: list[dict[str, Any]]) -> list[str]:
    notes = ["Supervisor v0 is observation-only; it does not change parallel batches or approvals."]
    remote_tasks = [task.task_id for task in task_specs if task.toolset_id == "remote_lookup"]
    if remote_tasks:
        notes.append("Remote MCP lookup remains a fallback path for tasks: " + ", ".join(remote_tasks))
    if conflicts:
        notes.append("Detected write conflicts should be handled by the existing scheduler until supervised mode is active.")
    return notes


def _event_message_for_view(view: SupervisorPlanView) -> str:
    if view.has_conflicts:
        return f"主管观察：{len(view.task_specs)} 个任务，发现 {len(view.conflicts)} 个资源冲突；本轮仅记录不改调度。"
    return f"主管观察：{len(view.task_specs)} 个任务，未发现资源写入冲突；本轮仅记录不改调度。"


def _normalize_resource(resource: str) -> str:
    text = str(resource or "").strip().replace("\\", "/")
    if not text:
        return ""
    if text == "workspace:*":
        return text
    parts = [part for part in PurePosixPath(text).parts if part not in {"", "."}]
    return "/".join(parts) or text


def _resources_conflict(left: str, right: str) -> bool:
    left = _normalize_resource(left)
    right = _normalize_resource(right)
    if not left or not right:
        return False
    if left == "workspace:*" or right == "workspace:*":
        return True
    if left == right:
        return True
    left_prefix = left.rstrip("/") + "/"
    right_prefix = right.rstrip("/") + "/"
    return left.startswith(right_prefix) or right.startswith(left_prefix)


def _dedupe_conflicts(conflicts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple] = set()
    result: list[dict[str, Any]] = []
    for conflict in conflicts:
        key = (
            conflict.get("kind"),
            conflict.get("parallel_group"),
            tuple(conflict.get("task_ids", [])),
            tuple(conflict.get("resources", [])),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(conflict)
    return result


def _join_unique(values: list[str], limit: int = 6) -> str:
    unique = []
    seen = set()
    for value in values:
        clean = str(value or "").strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        unique.append(clean)
        if len(unique) >= limit:
            break
    if len(seen) > len(unique):
        unique.append(f"...另有 {len(seen) - len(unique)} 项")
    return "、".join(unique)


def _string_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    return [str(item) for item in list(value) if str(item).strip()]
