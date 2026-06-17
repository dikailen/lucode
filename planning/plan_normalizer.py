from __future__ import annotations

from dataclasses import replace

from planning.plan_validator import _looks_like_over_split_readonly_single_file
from planning.planner_schema import PlannerResult


def normalize_plan_for_execution(plan: PlannerResult) -> tuple[PlannerResult, list[str]]:
    """Normalize planner output when the correction is deterministic and lossless."""

    if plan.route_type != "multi_agent":
        return plan, []

    tasks = list(plan.tasks or [])
    if len(tasks) == 1:
        task = tasks[0]
        normalized_task = replace(task, depends_on=[], parallel_group=1)
        normalized_plan = replace(
            plan,
            route_type="single_agent",
            tasks=[normalized_task],
            needs_synthesis=False,
            synthesis_instruction="",
        )
        return normalized_plan, ["multi_agent 单任务 -> 降级 single_agent"]

    if _looks_like_over_split_readonly_single_file(plan):
        return plan, [
            "疑似只读单文件过度拆分，建议人工合并为 single_agent（未自动改）"
        ]

    return plan, []
