from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from runtime.ui.collapse import collapse_text_block
from runtime.ui.output_model import OutputItem, OutputItemKind, OutputViewModel, OutputVisibility


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


@dataclass(frozen=True)
class PlainTextRenderOptions:
    limit: int = 12
    include_transient: bool = False
    expand_store: Any = None
    section_title: str = "执行摘要"


class PlainTextRenderer:
    """Plain-text renderer for OutputViewModel.

    It does not own cursor state, Rich Live, prompt_toolkit sessions, or
    execution behavior. That keeps this knife limited to the output adapter.
    """

    def render_timeline(
        self,
        view: OutputViewModel,
        options: PlainTextRenderOptions | None = None,
    ) -> str:
        opts = options or PlainTextRenderOptions(section_title="执行事件")
        items = _visible_items(view, opts)
        limit = _safe_limit(opts.limit, 8)
        visible = items[-limit:]
        lines = [opts.section_title or "执行事件"]
        if not visible:
            lines.append("  暂无事件。")
            return "\n".join(lines)

        omitted = len(items) - len(visible)
        if omitted > 0:
            lines.append(f"  ... 已折叠 {omitted} 条更早事件")
        for item in visible:
            lines.append(_render_event_line(item))
        return "\n".join(lines)

    def render_event_summary(
        self,
        view: OutputViewModel,
        options: PlainTextRenderOptions | None = None,
    ) -> str:
        opts = options or PlainTextRenderOptions()
        items = _visible_items(view, opts)
        if not items:
            return ""

        limit = _safe_limit(opts.limit, 12)
        visible = items[-limit:]
        lines = [opts.section_title or "执行摘要"]
        omitted = len(items) - len(visible)
        if omitted > 0:
            lines.append(f"  ... 已折叠 {omitted} 条更早事件")

        batch_lines = _parallel_batch_summary_lines(visible)
        if batch_lines:
            lines.extend(["  Parallel Batch", *batch_lines])

        tool_lines = _tool_summary_lines(visible, expand_store=opts.expand_store)
        if tool_lines:
            lines.extend(["  工具摘要", *tool_lines])

        worker_lines = _worker_summary_lines(visible)
        if worker_lines:
            lines.extend(["  Worker 分组", *worker_lines])

        lead_lines = _lead_review_summary_lines(visible)
        if lead_lines:
            lines.extend(["  LeadReview", *lead_lines])

        supervisor_lines = _supervisor_summary_lines(visible)
        if supervisor_lines:
            lines.extend(["  Supervisor", *supervisor_lines])

        if len(lines) == 1 or (len(lines) == 2 and omitted > 0):
            lines.extend(_timeline_fallback_lines(visible))
        return "\n".join(lines)

    def render_operation_items(
        self,
        view: OutputViewModel,
        options: PlainTextRenderOptions | None = None,
    ) -> str:
        opts = options or PlainTextRenderOptions()
        lines: list[str] = []
        for item in _visible_items(view, opts):
            if item.kind not in {
                OutputItemKind.OPERATION_RESULT,
                OutputItemKind.DIAGNOSTIC,
                OutputItemKind.INTERACTIVE_PANEL,
                OutputItemKind.TRANSIENT_HINT,
            }:
                continue
            text = item.body or item.summary or item.title
            if text:
                lines.append(text)
        return "\n".join(lines)


def _visible_items(view: OutputViewModel, options: PlainTextRenderOptions) -> list[OutputItem]:
    items = list(getattr(view, "items", []) or [])
    if options.include_transient:
        return items
    return [item for item in items if item.visibility == OutputVisibility.PERSISTENT]


def _render_event_line(item: OutputItem) -> str:
    event_type = _clean(item.event_type or item.title or "Event")
    label = EVENT_LABELS.get(event_type, event_type)
    meta = _event_meta(item)
    parts = [event_type]
    if label != event_type:
        parts.append(label)
    if item.task_id:
        parts.append(f"任务 {item.task_id}")
    if item.agent:
        parts.append(f"Agent {item.agent}")
    mode = _clean(item.metadata.get("mode") or "")
    if mode:
        parts.append(f"模式 {mode}")
    if item.status:
        parts.append(f"状态 {item.status}")
    if meta:
        parts.append(meta)
    message = _clean(item.body or item.summary)
    if message:
        parts.append(message)
    return "  - " + " | ".join(parts)


def _tool_summary_lines(items: list[OutputItem], *, expand_store=None) -> list[str]:
    counts: dict[str, int] = {}
    paths: dict[str, list[str]] = {}
    folded: dict[str, list[str]] = {}
    for item in items:
        if item.kind != OutputItemKind.TOOL:
            continue
        payload = item.metadata
        tool = _clean(payload.get("tool") or payload.get("tool_name"))
        action = _clean(payload.get("action") or payload.get("command"))
        if not tool and item.event_type == "FastPathUsed":
            tool = "fast_path"
        label = _tool_label(tool, action)
        if not label:
            continue
        counts[label] = counts.get(label, 0) + 1
        for touched in list(payload.get("files_touched") or []):
            if not isinstance(touched, dict):
                continue
            path = _clean(touched.get("path"))
            access = _clean(touched.get("access"))
            if path:
                _append_unique(paths.setdefault(label, []), f"{path}({access or 'file'})")
        for hint in _folded_payload_hints(payload, label, expand_store=expand_store):
            _append_unique(folded.setdefault(label, []), hint)

    lines = []
    for label in sorted(counts):
        parts = []
        if paths.get(label):
            parts.append(", ".join(paths.get(label, [])[:4]))
        if folded.get(label):
            parts.extend(folded.get(label, [])[:3])
        suffix = f" | {' | '.join(parts)}" if parts else ""
        lines.append(f"    - {label} x{counts[label]}{suffix}")
    return lines


def _parallel_batch_summary_lines(items: list[OutputItem]) -> list[str]:
    lines: list[str] = []
    for item in items:
        if item.event_type not in {"ParallelBatchNotice", "ParallelBatchStarted", "ParallelBatchSerialized"}:
            continue
        payload = item.metadata
        group_id = _clean(payload.get("group_id")) or "?"
        task_ids = [str(value).strip() for value in list(payload.get("task_ids") or []) if str(value).strip()]
        batch_size = _safe_int(payload.get("batch_size"), len(task_ids))
        reason = _clean(payload.get("reason"))
        status = _clean(item.status)
        parts = [f"group {group_id}", f"{batch_size or len(task_ids)} workers"]
        if status:
            parts.append(status)
        if reason:
            parts.append(reason[:80])
        if task_ids:
            parts.append(", ".join(task_ids[:4]))
        lines.append("    - " + " | ".join(parts))
    return lines[-4:]


def _folded_payload_hints(payload: dict[str, Any], label: str, *, expand_store=None) -> list[str]:
    if expand_store is None:
        return []
    candidates: list[tuple[str, str]] = []
    for key in ("arguments_summary", "output", "stdout", "stderr", "diff"):
        value = payload.get(key)
        if isinstance(value, dict):
            for nested_key, nested_value in value.items():
                if _looks_like_long_payload_field(str(nested_key)):
                    candidates.append((str(nested_key), str(nested_value or "")))
        elif _looks_like_long_payload_field(key):
            candidates.append((key, str(value or "")))

    hints: list[str] = []
    for key, text in candidates:
        block = collapse_text_block(text, kind="tool", title=f"{label} {key}", max_lines=16, max_chars=1200)
        if not block.collapsed:
            continue
        try:
            saved = expand_store.save(block)
        except Exception:
            hints.append(f"已折叠 {key}，但展开存储不可用")
            continue
        hints.append(f"已折叠 {key}: 输入 /expand {saved.block_id}")
    return hints


def _worker_summary_lines(items: list[OutputItem]) -> list[str]:
    workers: dict[str, dict[str, str]] = {}
    for item in items:
        if item.kind != OutputItemKind.WORKER:
            continue
        task_id = _clean(item.task_id) or "unknown"
        worker = workers.setdefault(task_id, {"status": "", "message": ""})
        if item.event_type == "TaskStarted":
            worker["status"] = "running"
        elif item.event_type == "TaskCompleted":
            worker["status"] = "completed"
        elif item.event_type == "TaskFailed":
            worker["status"] = "failed"
        elif item.event_type == "WorkerOutputStored":
            worker["detail"] = _clean(item.metadata.get("block_id"))
        message = _clean(item.body or item.summary)
        if message:
            worker["message"] = message[:80]
    return [
        f"    - {task_id}: {data.get('status') or 'unknown'}"
        f"{_message_suffix(data.get('message'))}"
        f"{_expand_suffix(data.get('detail'))}"
        for task_id, data in sorted(workers.items())
    ]


def _lead_review_summary_lines(items: list[OutputItem]) -> list[str]:
    findings = [item for item in items if item.event_type == "LeadReviewFinding"]
    completed = [item for item in items if item.event_type == "LeadReviewCompleted"]
    if not findings and not completed:
        return []

    error_count = 0
    warning_count = 0
    for item in findings:
        severity = _clean(item.metadata.get("severity") or item.status).lower()
        if severity == "error":
            error_count += 1
        elif severity == "warning":
            warning_count += 1
    if completed:
        payload = completed[-1].metadata
        error_count = _safe_int(payload.get("error_count"), error_count)
        warning_count = _safe_int(payload.get("warning_count"), warning_count)

    lines = [f"    - findings={len(findings)} error={error_count} warning={warning_count}"]
    for item in findings[-3:]:
        kind = _clean(item.metadata.get("kind")) or "finding"
        task_id = _clean(item.task_id) or "unknown"
        evidence = _clean(item.metadata.get("evidence"))
        lines.append(f"    - {kind} task={task_id}{_message_suffix(evidence)}")
    return lines


def _supervisor_summary_lines(items: list[OutputItem]) -> list[str]:
    lines: list[str] = []
    for item in items:
        if item.kind != OutputItemKind.SUPERVISOR:
            continue
        label = EVENT_LABELS.get(item.event_type, item.event_type or item.title or "Supervisor")
        parts = [label]
        if item.task_id:
            parts.append(f"task={item.task_id}")
        if item.status:
            parts.append(f"status={item.status}")
        message = _clean(item.body or item.summary)
        if message:
            parts.append(message[:80])
        lines.append("    - " + " | ".join(parts))
    return lines[-6:]


def _timeline_fallback_lines(items: list[OutputItem]) -> list[str]:
    return ["  时间线", *(_render_event_line(item) for item in items)]


def _event_meta(item: OutputItem) -> str:
    payload = item.metadata
    tool = _clean(payload.get("tool"))
    action = _clean(payload.get("action"))
    model = _clean(payload.get("planner_model_id") or payload.get("model_id") or payload.get("model"))
    reason = _clean(payload.get("reason"))
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


def _tool_label(tool: str, action: str) -> str:
    tool = _clean(tool)
    action = _clean(action)
    if tool and action:
        if tool == action or tool.endswith(f".{action}"):
            return tool
        return f"{tool}.{action}"
    return tool or action


def _looks_like_long_payload_field(key: str) -> bool:
    return str(key or "").lower() in {"stdout", "stderr", "output", "diff", "patch", "content", "result"}


def _clean(value: Any) -> str:
    return str(value or "").strip().replace("\\", "/")


def _append_unique(items: list[str], value: str) -> None:
    if value and value not in items:
        items.append(value)


def _message_suffix(message: str | None) -> str:
    text = _clean(message)
    return f" | {text}" if text else ""


def _expand_suffix(block_id: str | None) -> str:
    text = _clean(block_id)
    return f" | /expand {text}" if text else ""


def _safe_int(value: Any, fallback: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(fallback or 0)


def _safe_limit(value: int, fallback: int) -> int:
    try:
        return max(1, int(value or fallback))
    except (TypeError, ValueError):
        return max(1, int(fallback))
