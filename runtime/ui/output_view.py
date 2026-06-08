from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from runtime.ui.output_model import OutputItem, OutputItemKind, OutputViewModel, OutputVisibility


PROGRESS_EVENTS = {
    "PlanningStarted",
    "PlanningCompleted",
    "PlanningFailed",
    "ExecutionContractApplied",
    "ParallelBatchNotice",
    "ParallelBatchStarted",
    "ParallelBatchSerialized",
    "SynthesisStarted",
    "SynthesisCompleted",
    "SynthesisFailed",
}

TOOL_EVENTS = {
    "ToolInvoked",
    "FastPathUsed",
    "ToolApprovalPre",
    "ToolApprovalPost",
}

WORKER_EVENTS = {
    "TaskStarted",
    "TaskCompleted",
    "TaskFailed",
    "WorkerOutputStored",
}

LEAD_REVIEW_EVENTS = {
    "LeadReviewFinding",
    "LeadReviewCompleted",
}

SUPERVISOR_EVENTS = {
    "SupervisorObservation",
    "LeadFinalizing",
    "LeadCompleted",
    "ToolApproved",
}


def build_output_view_model(events: Iterable[Any], *, mode: str = "", route: str = "") -> OutputViewModel:
    """Convert runtime events into display-neutral output items.

    This adapter does not render text or mutate terminal state. Renderers decide
    later which persistent items become scrollback and which transient items stay
    inside an interactive view.
    """

    items = [_item_from_event(event) for event in list(events or [])]
    return OutputViewModel(items=[item for item in items if item is not None], mode=mode, route=route)


def build_interactive_panel_item(source: str, *, title: str, body: str, summary: str = "") -> OutputItem:
    return OutputItem(
        kind=OutputItemKind.INTERACTIVE_PANEL,
        visibility=OutputVisibility.TRANSIENT,
        title=str(title or ""),
        body=str(body or ""),
        summary=str(summary or title or ""),
        source=str(source or ""),
    )


def build_transient_hint_item(message: str, *, source: str = "") -> OutputItem:
    text = str(message or "")
    return OutputItem(
        kind=OutputItemKind.TRANSIENT_HINT,
        visibility=OutputVisibility.TRANSIENT,
        summary=text,
        body=text,
        source=str(source or ""),
    )


def build_operation_result_item(message: str, *, source: str = "", failed: bool = False) -> OutputItem:
    text = str(message or "")
    return OutputItem(
        kind=OutputItemKind.DIAGNOSTIC if failed else OutputItemKind.OPERATION_RESULT,
        visibility=OutputVisibility.PERSISTENT,
        summary=text,
        body=text,
        source=str(source or ""),
        status="failed" if failed else "completed",
    )


def _item_from_event(event: Any) -> OutputItem | None:
    event_type = _clean(getattr(event, "event_type", ""))
    kind = _kind_for_event(event_type)
    if kind is None:
        return None
    payload = _payload(event)
    metadata = dict(payload)
    mode = _clean(getattr(event, "mode", ""))
    if mode:
        metadata.setdefault("mode", mode)
    summary = _summary_for_event(event_type, event, payload)
    return OutputItem(
        kind=kind,
        visibility=OutputVisibility.PERSISTENT,
        title=event_type,
        summary=summary,
        body=_clean(getattr(event, "message", "")),
        source="event_bus",
        event_type=event_type,
        task_id=_clean(getattr(event, "task_id", "")),
        status=_clean(getattr(event, "status", "")),
        agent=_clean(getattr(event, "agent", "")),
        timestamp=_clean(getattr(event, "timestamp", "")),
        metadata=metadata,
    )


def _kind_for_event(event_type: str) -> OutputItemKind | None:
    if event_type in PROGRESS_EVENTS:
        return OutputItemKind.PROGRESS
    if event_type in TOOL_EVENTS:
        return OutputItemKind.TOOL
    if event_type in WORKER_EVENTS:
        return OutputItemKind.WORKER
    if event_type in LEAD_REVIEW_EVENTS:
        return OutputItemKind.LEAD_REVIEW
    if event_type in SUPERVISOR_EVENTS:
        return OutputItemKind.SUPERVISOR
    return None


def _summary_for_event(event_type: str, event: Any, payload: dict[str, Any]) -> str:
    if event_type in TOOL_EVENTS:
        return _tool_summary(event_type, event, payload)
    parts = [
        _clean(getattr(event, "message", "")),
        _clean(getattr(event, "status", "")),
        _clean(payload.get("kind")),
        _clean(payload.get("reason")),
    ]
    return " | ".join(part for part in parts if part) or event_type


def _tool_summary(event_type: str, event: Any, payload: dict[str, Any]) -> str:
    tool = _clean(payload.get("tool") or payload.get("tool_name"))
    action = _clean(payload.get("action") or payload.get("command"))
    message = _clean(getattr(event, "message", ""))
    files = []
    for touched in list(payload.get("files_touched") or []):
        if not isinstance(touched, dict):
            continue
        path = _clean(touched.get("path"))
        access = _clean(touched.get("access"))
        if path and access:
            files.append(f"{path}({access})")
        elif path:
            files.append(path)
    parts = [_tool_label(tool, action), message, ", ".join(files)]
    return " | ".join(part for part in parts if part) or event_type


def _tool_label(tool: str, action: str) -> str:
    if tool and action:
        if tool == action or tool.endswith(f".{action}"):
            return tool
        return f"{tool}.{action}"
    return tool or action


def _payload(event: Any) -> dict[str, Any]:
    payload = getattr(event, "payload", {}) or {}
    return payload if isinstance(payload, dict) else {}


def _clean(value: Any) -> str:
    return str(value or "").strip().replace("\\", "/")
