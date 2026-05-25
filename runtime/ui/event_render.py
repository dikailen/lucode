from __future__ import annotations

from collections.abc import Iterable
from typing import Any


EVENT_LABELS = {
    "ToolApproved": "主管审批",
    "PlanningStarted": "开始规划",
    "PlanningCompleted": "规划完成",
    "PlanningFailed": "规划失败",
    "TaskStarted": "任务开始",
    "TaskCompleted": "任务完成",
    "TaskFailed": "任务失败",
    "FastPathUsed": "快速路径",
    "SynthesisStarted": "开始汇总",
    "SynthesisCompleted": "汇总完成",
}


def render_execution_events(events: Iterable[Any], limit: int = 8) -> str:
    """Render a compact plain-text execution timeline.

    This is intentionally independent from Rich/Textual so execution observability
    has a safe fallback in every terminal.
    """

    items = list(events or [])
    limit = max(1, int(limit or 8))
    visible = items[-limit:]
    lines = ["执行事件"]
    if not visible:
        lines.append("  暂无事件。")
        return "\n".join(lines)

    omitted = len(items) - len(visible)
    if omitted > 0:
        lines.append(f"  ... 已折叠 {omitted} 条更早事件")
    for event in visible:
        lines.append(_render_event_line(event))
    return "\n".join(lines)


def _render_event_line(event: Any) -> str:
    event_type = str(getattr(event, "event_type", "") or "Event")
    label = EVENT_LABELS.get(event_type, event_type)
    status = str(getattr(event, "status", "") or "").strip()
    task_id = str(getattr(event, "task_id", "") or "").strip()
    agent = str(getattr(event, "agent", "") or "").strip()
    mode = str(getattr(event, "mode", "") or "").strip()
    message = str(getattr(event, "message", "") or "").strip()
    payload = getattr(event, "payload", {}) or {}
    meta = _event_meta(payload)

    parts = [event_type]
    if label != event_type:
        parts.append(label)
    if task_id:
        parts.append(f"任务 {task_id}")
    if agent:
        parts.append(f"Agent {agent}")
    if mode:
        parts.append(f"模式 {mode}")
    if status:
        parts.append(f"状态 {status}")
    if meta:
        parts.append(meta)
    if message:
        parts.append(message)
    return "  - " + " | ".join(parts)


def _event_meta(payload: dict[str, Any]) -> str:
    if not isinstance(payload, dict) or not payload:
        return ""
    tool = str(payload.get("tool") or "").strip()
    action = str(payload.get("action") or "").strip()
    model = str(payload.get("planner_model_id") or payload.get("model_id") or payload.get("model") or "").strip()
    reason = str(payload.get("reason") or "").strip()
    if tool and action:
        return f"{tool} {action}"
    if tool:
        return tool
    if action:
        return action
    if model:
        return f"模型 {model}"
    if reason:
        return reason[:80]
    return ""
