from __future__ import annotations

from dataclasses import dataclass, field

from planning.planner_schema import PlannerResult


@dataclass
class PlanReview:
    approved: bool
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    requires_clarification: bool = False


def review_plan(plan: PlannerResult) -> PlanReview:
    issues: list[str] = []
    warnings: list[str] = []
    task_ids = {task.id for task in plan.tasks}

    if plan.route_type in {"direct_answer", "clarify"}:
        return PlanReview(approved=True, issues=[], warnings=warnings)

    for task in plan.tasks:
        if not task.acceptance_criteria:
            warnings.append(f"任务 {task.id} 缺少 acceptance_criteria，后续 Auditor 验收会偏弱。")

    issues.extend(_review_dependencies(plan, task_ids))
    warnings.extend(_review_parallel_write_conflicts(plan))

    return PlanReview(
        approved=not issues,
        issues=issues,
        warnings=warnings,
        requires_clarification=False,
    )


def format_plan_review(review: PlanReview) -> str:
    if review.approved and not review.warnings:
        return "计划审查：通过"

    lines = ["计划审查：通过" if review.approved else "计划审查：未通过"]
    if review.issues:
        lines.append("问题：")
        lines.extend(f"- {issue}" for issue in review.issues)
    if review.warnings:
        lines.append("提醒：")
        lines.extend(f"- {warning}" for warning in review.warnings)
    return "\n".join(lines)


def _review_dependencies(plan: PlannerResult, task_ids: set[str]) -> list[str]:
    issues: list[str] = []
    graph = {task.id: list(task.depends_on) for task in plan.tasks}

    for task in plan.tasks:
        for dep in task.depends_on:
            if dep not in task_ids:
                issues.append(f"任务 {task.id} 存在未知依赖：{dep}")

    visiting: set[str] = set()
    visited: set[str] = set()

    def walk(task_id: str) -> bool:
        if task_id in visited:
            return False
        if task_id in visiting:
            return True
        visiting.add(task_id)
        for dep in graph.get(task_id, []):
            if dep in graph and walk(dep):
                return True
        visiting.remove(task_id)
        visited.add(task_id)
        return False

    for task_id in graph:
        if walk(task_id):
            issues.append(f"检测到循环依赖，涉及任务：{task_id}")
            break

    return issues


def _review_parallel_write_conflicts(plan: PlannerResult) -> list[str]:
    issues: list[str] = []
    by_group: dict[int, dict[str, list[str]]] = {}

    for task in plan.tasks:
        if not task.write_intent:
            continue
        group = by_group.setdefault(task.parallel_group, {})
        for path in task.write_intent:
            group.setdefault(path, []).append(task.id)

    for group_id, path_map in by_group.items():
        for path, task_ids in path_map.items():
            if len(task_ids) > 1:
                issues.append(
                    f"同一并行组 {group_id} 中多个任务声明会写入同一文件：{path}（任务：{', '.join(task_ids)}）"
                )

    return issues
