from __future__ import annotations

from typing import Any

from runtime.hooks.tool_events import ToolHookEvent


def emit_tool_event_bridge(event_bus, event: ToolHookEvent, *, task_id: str = ""):
    """Mirror tool hook events into the run event bus with distinct semantics."""

    if _is_pre_approval(event):
        return _emit_tool_event(event_bus, "ToolApprovalPre", event, task_id=task_id)
    if _is_post_approval(event):
        return _emit_tool_event(event_bus, "ToolApprovalPost", event, task_id=task_id)
    return emit_tool_invoked_event(event_bus, event, task_id=task_id)


def emit_tool_invoked_event(event_bus, event: ToolHookEvent, *, task_id: str = ""):
    """Mirror a tool hook event into the per-run execution event bus."""

    return _emit_tool_event(event_bus, "ToolInvoked", event, task_id=task_id)


def _emit_tool_event(event_bus, event_type: str, event: ToolHookEvent, *, task_id: str = ""):
    if event_bus is None or not hasattr(event_bus, "emit"):
        return None
    payload = tool_invoked_payload(event)
    label = "Tool invoked" if event_type == "ToolInvoked" else "Tool approval"
    try:
        return event_bus.emit(
            event_type,
            f"{label}: {payload.get('tool') or 'unknown'}",
            agent="tool",
            task_id=str(task_id or ""),
            status=str(getattr(event, "status", "") or ""),
            payload=payload,
        )
    except Exception:
        return None


def _is_pre_approval(event: ToolHookEvent) -> bool:
    return str(getattr(event, "event_type", "") or "") == "pre_tool_use"


def _is_post_approval(event: ToolHookEvent) -> bool:
    return str(getattr(event, "event_type", "") or "") == "post_tool_use"


def tool_invoked_payload(event: ToolHookEvent) -> dict[str, Any]:
    tool = str(getattr(event, "tool_name", "") or "")
    arguments_summary = dict(getattr(event, "arguments_summary", {}) or {})
    files_touched = _files_touched(tool, arguments_summary)
    return {
        "tool": tool,
        "tool_name": tool,
        "tool_rule": str(getattr(event, "tool_rule", "") or ""),
        "event_type": str(getattr(event, "event_type", "") or ""),
        "action": _tool_action(tool),
        "decision": str(getattr(event, "decision", "") or ""),
        "outcome": _outcome(event),
        "reason": str(getattr(event, "reason", "") or ""),
        "arguments_summary": arguments_summary,
        "files_touched": files_touched,
        "risk": dict(getattr(event, "risk", {}) or {}),
        "timestamp": str(getattr(event, "timestamp", "") or ""),
    }


def _outcome(event: ToolHookEvent) -> str:
    decision = str(getattr(event, "decision", "") or "").strip()
    if decision:
        return decision
    status = str(getattr(event, "status", "") or "").strip()
    return status


def _tool_action(tool_name: str) -> str:
    name = str(tool_name or "").strip()
    if "." in name:
        return name.rsplit(".", 1)[-1]
    return name


def _files_touched(tool_name: str, arguments_summary: dict[str, Any]) -> list[dict[str, str]]:
    tool = str(tool_name or "").lower()
    access = _file_access_for_tool(tool)
    if not access:
        return []
    paths = []
    for key in ("target_path", "path", "target", "file_path"):
        value = _normalize_path(arguments_summary.get(key))
        if value:
            paths.append(value)
    if "apply_unified_patch" in tool:
        patch_paths = arguments_summary.get("patch_paths")
        if isinstance(patch_paths, list):
            paths.extend(str(path or "") for path in patch_paths)
        else:
            paths.extend(_patch_paths(str(arguments_summary.get("patch") or "")))
    result = []
    seen = set()
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        result.append({"path": path, "access": access})
    return result


def _file_access_for_tool(tool: str) -> str:
    if any(
        marker in tool
        for marker in (
            "create_file",
            "write_file",
            "replace_in_file",
            "apply_unified_patch",
            "delete_file",
            "safe_delete_file",
        )
    ):
        return "write"
    if any(marker in tool for marker in ("read_file", "read_multiple_files", "git_diff", "git_status")):
        return "read"
    return ""


def _patch_paths(patch: str) -> list[str]:
    paths: list[str] = []
    for line in str(patch or "").splitlines():
        if not (line.startswith("+++ ") or line.startswith("--- ")):
            continue
        raw = line[4:].strip()
        if raw == "/dev/null":
            continue
        if raw.startswith(("a/", "b/")):
            raw = raw[2:]
        clean = _normalize_path(raw)
        if clean and clean not in paths:
            paths.append(clean)
    return paths


def _normalize_path(value: Any) -> str:
    clean = str(value or "").strip().strip("`'\"()[]{}<>")
    if not clean or "://" in clean:
        return ""
    clean = clean.replace("\\", "/").lstrip("./")
    parts = [part for part in clean.split("/") if part and part != "."]
    if any(part == ".." for part in parts):
        return ""
    return "/".join(parts)
