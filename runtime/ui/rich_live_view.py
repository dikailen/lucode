from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RichPlanItem:
    id: str
    title: str
    status: str


@dataclass(frozen=True)
class RichActorBlock:
    role: str
    title: str
    subtitle: str
    model_label: str
    current_action: str
    status: str


@dataclass(frozen=True)
class RichLiveView:
    mode: str
    route: str
    attempt: int
    plan_items: list[RichPlanItem] = field(default_factory=list)
    actor_blocks: list[RichActorBlock] = field(default_factory=list)


def build_rich_live_view(run_state, *, mode: str, attempt: int, active: str = "") -> RichLiveView:
    """Convert run state into a compact Live view without rendering decisions."""

    tasks = list(getattr(run_state, "tasks", []) or [])
    route = _route_label(getattr(run_state, "route_type", ""))
    events = _event_snapshot(run_state)
    controller_state = _controller_snapshot(run_state)
    model_labels = _model_labels(run_state)
    if _clean(mode).lower() == "solo" or route == "solo":
        return _solo_live_view(
            run_state,
            mode=mode,
            route=route,
            attempt=attempt,
            active=active,
            tasks=tasks,
            events=events,
            controller_state=controller_state,
            model_labels=model_labels,
        )

    plan_items = [
        RichPlanItem(
            id=_clean(getattr(task, "id", "")) or f"task_{index + 1}",
            title=_clean(getattr(task, "title", "")) or _clean(getattr(task, "id", "")) or f"Task {index + 1}",
            status=_status_label(getattr(task, "status", "")),
        )
        for index, task in enumerate(tasks)
    ]

    actor_blocks = [
        _supervisor_block(
            mode=mode,
            route=route,
            active=active,
            events=events,
            controller_state=controller_state,
            tasks=tasks,
            model_labels=model_labels,
        )
    ]
    worker_index = 1
    for task in tasks:
        status = _status_label(getattr(task, "status", ""))
        if status == "pending":
            continue
        actor_blocks.append(
            RichActorBlock(
                role="worker",
                title=f"Worker {worker_index}",
                subtitle=_clean(getattr(task, "id", "")) or f"task_{worker_index}",
                model_label=_compact_model_label(_display_model_label(getattr(task, "model", ""), model_labels)),
                current_action=_task_current_action(task, events, active=active),
                status=status,
            )
        )
        worker_index += 1

    return RichLiveView(
        mode=_clean(mode),
        route=route,
        attempt=int(attempt or 0),
        plan_items=plan_items,
        actor_blocks=actor_blocks,
    )


def _solo_live_view(
    run_state,
    *,
    mode: str,
    route: str,
    attempt: int,
    active: str,
    tasks: list[Any],
    events: list[Any],
    controller_state: Any,
    model_labels: dict[str, str],
) -> RichLiveView:
    task = tasks[0] if tasks else None
    status = _status_label(getattr(task, "status", "")) if task is not None else _phase_value(getattr(controller_state, "phase", ""))
    model_id = _clean(getattr(task, "model", "")) if task is not None else ""
    action = _task_current_action(task, events, active=active) if task is not None else _clean(active)
    if not action:
        action = "Answering request"
    return RichLiveView(
        mode=_clean(mode),
        route=route,
        attempt=int(attempt or 0),
        actor_blocks=[
            RichActorBlock(
                role="agent",
                title="Agent",
                subtitle="solo",
                model_label=_compact_model_label(_display_model_label(model_id, model_labels)),
                current_action=action,
                status=status or "running",
            )
        ],
    )


def _supervisor_block(
    *,
    mode: str,
    route: str,
    active: str,
    events: list[Any],
    controller_state: Any,
    tasks: list[Any],
    model_labels: dict[str, str],
) -> RichActorBlock:
    status = _phase_value(getattr(controller_state, "phase", ""))
    action = _latest_supervisor_action(events)
    if not action:
        if _any_running(tasks):
            action = "Waiting for workers"
        elif active:
            action = _clean(active)
        else:
            action = "Planning"
    return RichActorBlock(
        role="supervisor",
        title="Supervisor",
        subtitle=" / ".join(part for part in (_clean(mode), route) if part),
        model_label=_compact_model_label(_display_model_label(_latest_planner_model(events), model_labels)),
        current_action=action,
        status=status or "running",
    )


def _latest_planner_model(events: list[Any]) -> str:
    for event in reversed(events):
        if _clean(getattr(event, "event_type", "")) != "PlanningStarted":
            continue
        payload = getattr(event, "payload", {}) or {}
        if not isinstance(payload, dict):
            continue
        model = _clean(payload.get("planner_model_id") or payload.get("model_id") or payload.get("model"))
        if model:
            return model
    return ""


def _compact_model_label(value: Any, max_chars: int = 32) -> str:
    text = _clean(value)
    if not text or text.lower() in {"unknown", "none", "null"}:
        return ""
    max_chars = max(8, int(max_chars or 32))
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip("_-/ .") + "..."


def _model_labels(run_state) -> dict[str, str]:
    raw = getattr(run_state, "model_labels", {}) or {}
    if not isinstance(raw, dict):
        return {}
    return {_clean(key): _clean(value) for key, value in raw.items() if _clean(key) and _clean(value)}


def _display_model_label(model_id: Any, model_labels: dict[str, str]) -> str:
    clean_id = _clean(model_id)
    if not clean_id:
        return ""
    return model_labels.get(clean_id) or clean_id


def _task_current_action(task: Any, events: list[Any], *, active: str = "") -> str:
    task_id = _clean(getattr(task, "id", ""))
    tool_action = _tool_action_for_task(events, task_id)
    if tool_action:
        return tool_action
    event = _latest_task_event(events, task_id)
    if event is not None:
        action = _event_action(event)
        if action:
            return action
    status = _status_label(getattr(task, "status", ""))
    if status == "completed":
        return "Completed"
    if status == "failed":
        error = _clean(getattr(task, "error", ""))
        return f"Failed: {error}" if error else "Failed"
    if active:
        return _clean(active)
    return _clean(getattr(task, "title", "")) or "Working"


def _latest_task_event(events: list[Any], task_id: str) -> Any | None:
    for event in reversed(events):
        if _clean(getattr(event, "task_id", "")) != task_id:
            continue
        if _clean(getattr(event, "event_type", "")) in {"FastPathUsed", "TaskCompleted", "TaskFailed"}:
            return event
    return None


def _tool_action_for_task(events: list[Any], task_id: str) -> str:
    tool_events = [
        event
        for event in events
        if _clean(getattr(event, "task_id", "")) == task_id
        and _clean(getattr(event, "event_type", "")) == "ToolInvoked"
    ]
    if not tool_events:
        return ""
    latest = tool_events[-1]
    payload = getattr(latest, "payload", {}) or {}
    if not isinstance(payload, dict):
        payload = {}
    group = _action_group(payload)
    if group == "read":
        return _read_action(tool_events)
    if group == "write":
        return _write_action(payload)
    if group == "search":
        return _search_action(payload)
    if group == "run":
        return _run_action(payload)
    return _tool_action(payload) or _clean(getattr(latest, "message", "")) or "Using tool"


def _latest_supervisor_action(events: list[Any]) -> str:
    supervisor_types = {
        "PlanningStarted",
        "PlanningCompleted",
        "SupervisorObservation",
        "LeadReviewFinding",
        "LeadReviewCompleted",
        "LeadFinalizing",
        "LeadCompleted",
        "ToolApproved",
    }
    for event in reversed(events):
        event_type = _clean(getattr(event, "event_type", ""))
        if event_type not in supervisor_types:
            continue
        message = _clean(getattr(event, "message", ""))
        if event_type == "PlanningStarted":
            return "Planning"
        if event_type == "PlanningCompleted":
            return "Plan ready"
        if event_type == "LeadFinalizing":
            return "Finalizing"
        if event_type == "LeadCompleted":
            return "Final answer ready"
        if event_type == "ToolApproved":
            return message or "Approving tool use"
        if event_type.startswith("LeadReview"):
            return message or "Reviewing worker reports"
        return message or event_type
    return ""


def _event_action(event: Any) -> str:
    event_type = _clean(getattr(event, "event_type", ""))
    payload = getattr(event, "payload", {}) or {}
    if not isinstance(payload, dict):
        payload = {}
    if event_type == "ToolInvoked":
        group = _action_group(payload)
        if group == "read":
            return _read_action([event])
        if group == "write":
            return _write_action(payload)
        if group == "search":
            return _search_action(payload)
        if group == "run":
            return _run_action(payload)
        return _tool_action(payload) or _clean(getattr(event, "message", "")) or "Using tool"
    if event_type == "FastPathUsed":
        label = _tool_action(payload)
        return f"Using fast path: {label}" if label else "Using fast path"
    if event_type == "TaskStarted":
        return _clean(getattr(event, "message", "")) or "Working"
    if event_type == "TaskCompleted":
        return "Completed"
    if event_type == "TaskFailed":
        return _clean(getattr(event, "message", "")) or "Failed"
    return _clean(getattr(event, "message", ""))


def _action_group(payload: dict[str, Any]) -> str:
    tool = _clean(payload.get("tool") or payload.get("tool_name")).lower()
    action = _clean(payload.get("action") or payload.get("command")).lower()
    text = f"{tool} {action}"
    files = payload.get("files_touched")
    if isinstance(files, list):
        accesses = {_clean(item.get("access")).lower() for item in files if isinstance(item, dict)}
        if "write" in accesses:
            return "write"
        if "read" in accesses:
            return "read"
    if any(marker in text for marker in ("write", "edit", "patch", "replace", "delete", "create_file")):
        return "write"
    if any(marker in text for marker in ("read", "filesystem_readonly", "git_status", "git_diff")):
        return "read"
    if any(marker in text for marker in ("search", "locate", "grep", "rg", "select-string", "code_locator")):
        return "search"
    if any(marker in text for marker in ("run_command", "command_runner", "shell", "pytest", "unittest", "python")):
        return "run"
    return ""


def _read_action(events: list[Any]) -> str:
    paths: list[str] = []
    for event in events:
        payload = getattr(event, "payload", {}) or {}
        if isinstance(payload, dict):
            paths.extend(_file_paths(payload, access="read"))
    paths = _unique(paths)
    if len(paths) <= 1:
        return f"Reading {paths[0]}" if paths else "Reading project files"
    return f"Reading {_path_scope(paths)}"


def _write_action(payload: dict[str, Any]) -> str:
    paths = _file_paths(payload, access="write")
    if len(paths) == 1:
        return f"Writing {paths[0]}"
    if len(paths) > 1:
        return f"Writing {_path_scope(paths)}"
    return "Writing files"


def _search_action(payload: dict[str, Any]) -> str:
    summary = payload.get("arguments_summary")
    query = ""
    if isinstance(summary, dict):
        query = _clean(summary.get("query") or summary.get("pattern") or summary.get("text"))
    return f"Searching {query}" if query else "Searching project"


def _run_action(payload: dict[str, Any]) -> str:
    summary = payload.get("arguments_summary")
    command = ""
    if isinstance(summary, dict):
        command = _clean(summary.get("command") or summary.get("cmd"))
    return f"Running {command}" if command else "Running command"


def _file_paths(payload: dict[str, Any], *, access: str = "") -> list[str]:
    files = payload.get("files_touched")
    if not isinstance(files, list):
        return []
    paths: list[str] = []
    for item in files:
        if not isinstance(item, dict):
            continue
        path = _clean(item.get("path"))
        if not path:
            continue
        item_access = _clean(item.get("access")).lower()
        if access and item_access != access:
            continue
        paths.append(path)
    return paths


def _path_scope(paths: list[str]) -> str:
    normalized = [_clean(path).replace("\\", "/").strip("/") for path in paths if _clean(path)]
    if not normalized:
        return "project files"
    roots = []
    for path in normalized:
        parts = [part for part in path.split("/") if part]
        if len(parts) >= 2 and parts[0] in {"runtime", "tests", "lucode", "planning", "catalog_system"}:
            roots.append("/".join(parts[:2]) if parts[0] == "runtime" else parts[0])
        elif parts:
            roots.append(parts[0])
    roots = _unique(roots)
    if not roots:
        return "project files"
    if len(roots) == 1:
        return f"{roots[0]} files"
    if len(roots) == 2:
        return f"{roots[0]} and {roots[1]}"
    return "project files"


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    seen = set()
    for value in values:
        clean = _clean(value)
        if not clean or clean in seen:
            continue
        seen.add(clean)
        result.append(clean)
    return result


def _tool_action(payload: dict[str, Any]) -> str:
    tool = _clean(payload.get("tool") or payload.get("tool_name"))
    action = _clean(payload.get("action") or payload.get("command"))
    if tool and action:
        if tool == action or tool.endswith(f".{action}"):
            return tool
        return f"{tool}.{action}"
    return tool or action


def _event_snapshot(run_state) -> list[Any]:
    event_bus = getattr(run_state, "event_bus", None)
    if event_bus is None or not hasattr(event_bus, "snapshot"):
        return []
    try:
        return list(event_bus.snapshot())
    except Exception:
        return []


def _controller_snapshot(run_state) -> Any:
    controller = getattr(run_state, "output_controller", None)
    if controller is None or not hasattr(controller, "snapshot"):
        return None
    try:
        return controller.snapshot()
    except Exception:
        return None


def _route_label(route: Any) -> str:
    value = _clean(route) or "unknown"
    if value == "multi_agent":
        return "team"
    return value


def _status_label(status: Any) -> str:
    value = _clean(status).lower()
    if value in {"running", "completed", "failed"}:
        return value
    return "pending"


def _phase_value(phase: Any) -> str:
    return _clean(getattr(phase, "value", phase)).lower()


def _any_running(tasks: list[Any]) -> bool:
    return any(_status_label(getattr(task, "status", "")) == "running" for task in tasks)


def _clean(value: Any) -> str:
    return str(value or "").replace("\r", " ").replace("\n", " ").strip()
