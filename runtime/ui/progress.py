from __future__ import annotations

import unicodedata

from runtime.ui.event_render import render_execution_events


BOX_TOP_LEFT = "\u256d"
BOX_TOP_RIGHT = "\u256e"
BOX_BOTTOM_LEFT = "\u2570"
BOX_BOTTOM_RIGHT = "\u256f"
BOX_HORIZONTAL = "\u2500"
BOX_VERTICAL = "\u2502"

STATUS_SYMBOLS = {
    "pending": "[ ]",
    "running": "[>]",
    "completed": "[\u2713]",
    "failed": "[x]",
}


def render_task_status_board(
    run_state,
    mode: str = "serial",
    attempt: int = 1,
    title: str = "任务状态",
    include_events: bool = False,
) -> str:
    """Render a compact C5 task progress board for serial/full execution."""

    route = getattr(run_state, "route_type", "unknown")
    lines = [f"{title} · {mode} · 第 {attempt} 轮 · {route}"]
    tasks = list(getattr(run_state, "tasks", []) or [])
    if not tasks:
        lines.append("[ ] 暂无可执行任务")
        if include_events:
            lines.extend(_event_lines(run_state))
        return _box(lines)

    for record in tasks:
        lines.append(_task_line(record))
        detail = _task_detail_line(record)
        if detail:
            lines.append(detail)
    if include_events:
        lines.extend(_event_lines(run_state))
    return _box(lines)


def render_runtime_statusline(mode: str, started_mcp_ids: list[str] | None = None, active: str = "") -> str:
    mcp_text = ", ".join(started_mcp_ids or []) or "按需加载"
    active_text = active or "等待输入"
    return f"状态 | 模式 {mode} | 工具 {mcp_text} | 当前 {active_text}"


def _task_line(record) -> str:
    status = str(getattr(record, "status", "pending") or "pending")
    symbol = STATUS_SYMBOLS.get(status, STATUS_SYMBOLS["pending"])
    title = str(getattr(record, "title", "") or getattr(record, "id", "") or "未命名任务")
    task_id = str(getattr(record, "id", "") or "task")
    return f"{symbol} {task_id}  {title}"


def _task_detail_line(record) -> str:
    parts = []
    mcp = ", ".join(list(getattr(record, "mcp", []) or [])[:4])
    if mcp:
        parts.append(f"工具 {mcp}")
    depends_on = ", ".join(list(getattr(record, "depends_on", []) or [])[:4])
    if depends_on:
        parts.append(f"依赖 {depends_on}")
    write_intent = ", ".join(list(getattr(record, "write_intent", []) or [])[:4])
    if write_intent:
        parts.append(f"写入 {write_intent}")
    error = str(getattr(record, "error", "") or "").strip()
    if error:
        parts.append(f"错误 {error[:80]}")
    return "    " + " · ".join(parts) if parts else ""


def _event_lines(run_state) -> list[str]:
    event_bus = getattr(run_state, "event_bus", None)
    snapshot = event_bus.snapshot() if event_bus is not None and hasattr(event_bus, "snapshot") else []
    return ["", *render_execution_events(snapshot, limit=6).splitlines()]


def _box(lines: list[str]) -> str:
    width = max((_display_width(line) for line in lines), default=0)
    rendered = [f"{BOX_TOP_LEFT}{BOX_HORIZONTAL * (width + 2)}{BOX_TOP_RIGHT}"]
    for line in lines:
        rendered.append(f"{BOX_VERTICAL} {line}{' ' * max(width - _display_width(line), 0)} {BOX_VERTICAL}")
    rendered.append(f"{BOX_BOTTOM_LEFT}{BOX_HORIZONTAL * (width + 2)}{BOX_BOTTOM_RIGHT}")
    return "\n".join(rendered)


def _display_width(value: str) -> int:
    width = 0
    for char in str(value or ""):
        if unicodedata.combining(char):
            continue
        width += 2 if unicodedata.east_asian_width(char) in {"F", "W"} else 1
    return width

