from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any

from runtime.ui.live_status import dynamic_status


def render_compact_plan_summary(
    refined,
    plan,
    validation,
    review,
    gate,
    *,
    mode: str,
    detail_selector: str = "plan-last",
) -> str:
    """Render a compact user-facing plan summary without dumping planner internals."""

    route = str(getattr(plan, "route_type", "") or "unknown")
    tasks = list(getattr(plan, "tasks", []) or [])
    access = _access_label(tasks)
    header = f"● Plan ready  {mode} / {route} / {access} / {len(tasks)} {_task_word(len(tasks))}"
    lines = [header]
    summary = _task_summary(tasks)
    if summary:
        lines.append(f"└ {summary}")
    risk = _risk_summary(tasks, validation, review, gate)
    if risk:
        lines.append(f"└ {risk}")
    if detail_selector:
        lines.append(f"└ 详情：/expand {detail_selector}")
    return "\n".join(lines)


def render_planning_status(request: str, *, mode: str, stage: str = "planning") -> str:
    """Render one visible planning status frame for non-Live terminals."""

    if str(stage or "").strip().lower() == "planning":
        return "Planning"
    label = _stage_label(stage)
    request_line = _one_line(request, 96)
    return "\n".join([f"⠋ Planning  {label}", f"└ {mode} · {request_line}"])


@contextlib.contextmanager
def planning_status(request: str, *, mode: str, enabled: bool = True) -> Iterator[None]:
    """Show a small planning spinner when supported, otherwise let callers print a static frame."""

    with dynamic_status(request, mode=mode, stage="planning", enabled=enabled):
        yield


def _access_label(tasks: list[Any]) -> str:
    if any(list(getattr(task, "write_intent", []) or []) for task in tasks):
        return "write"
    mcps = {str(mcp) for task in tasks for mcp in list(getattr(task, "mcp", []) or [])}
    if "workspace_edit" in mcps or "command_runner" in mcps:
        return "elevated"
    return "readonly"


def _task_word(count: int) -> str:
    return "task" if count == 1 else "tasks"


def _task_summary(tasks: list[Any]) -> str:
    if not tasks:
        return ""
    if len(tasks) == 1:
        task = tasks[0]
        skill = str(getattr(task, "skill_id", "") or "skill")
        read_set = [str(item) for item in list(getattr(task, "read_set", []) or []) if str(item).strip()]
        if read_set:
            return f"{skill} · {', '.join(read_set[:3])}"
        title = str(getattr(task, "title", "") or getattr(task, "id", "") or "task")
        return f"{skill} · {_one_line(title, 72)}"
    groups = sorted({int(getattr(task, "parallel_group", 1) or 1) for task in tasks})
    return f"{len(tasks)} tasks · {len(groups)} groups"


def _risk_summary(tasks: list[Any], validation, review, gate) -> str:
    errors = list(getattr(validation, "errors", []) or [])
    issues = list(getattr(review, "issues", []) or [])
    if errors or issues:
        return f"blocked · errors={len(errors)} issues={len(issues)}"
    writes = [path for task in tasks for path in list(getattr(task, "write_intent", []) or [])]
    if writes:
        return f"writes: {', '.join(str(path) for path in writes[:3])}"
    if getattr(gate, "needs_code_pipeline", False):
        return f"gate: {getattr(gate, 'risk_level', 'unknown')}"
    return ""


def _stage_label(stage: str) -> str:
    normalized = str(stage or "").strip().lower()
    return {
        "planning": "正在理解任务...",
        "routing": "正在生成执行路线...",
        "review": "正在检查计划风险...",
        "ready": "规划完成",
    }.get(normalized, stage or "正在规划...")


def _one_line(value: str, limit: int) -> str:
    text = str(value or "").replace("\r", "").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 3)].rstrip() + "..."
