from __future__ import annotations

from typing import Any

from runtime.agent.supervisor import WorkerReport


def build_worker_report(task, output: str, *, run_state=None) -> WorkerReport:
    """Build a deterministic WorkerReport without relying on model output format.

    PlannedTask.write_intent is the declared write scope; WorkerReport.files_written
    is actual tool evidence observed in the run event stream.
    """

    task_id = str(getattr(task, "id", "") or "")
    status = _task_status(task_id, run_state) or ("completed" if str(output or "").strip() else "unknown")
    files_read = _unique_strings([*list(getattr(task, "read_set", []) or []), *_files_read_from_context(task_id, run_state)])
    files_written = _files_written_from_events(task_id, run_state)
    tool_calls = _tool_calls_for_task(task_id, run_state)
    blockers = _blockers_for_task(task_id, run_state)
    evidence_refs = _evidence_refs(task_id, run_state, files_read=files_read, files_written=files_written)
    claimed_artifacts = _claimed_artifacts_from_output(output)
    return WorkerReport(
        task_id=task_id,
        status=status,
        summary=_summary_from_output(output),
        evidence_refs=evidence_refs,
        files_read=files_read,
        files_written=files_written,
        tool_calls=tool_calls,
        blockers=blockers,
        artifacts=claimed_artifacts,
    )


def render_worker_report(report: WorkerReport) -> str:
    lines = [
        "WorkerReport",
        f"- task_id: {report.task_id or 'unknown'}",
        f"- status: {report.status or 'unknown'}",
        f"- summary: {report.summary or '(empty)'}",
        f"- files_read: {_join_or_none(report.files_read)}",
        f"- files_written: {_join_or_none(report.files_written)}",
        f"- tool_calls: {_format_tool_calls(report.tool_calls)}",
    ]
    if report.blockers:
        lines.append(f"- blockers: {_join_or_none(report.blockers)}")
    if report.evidence_refs:
        lines.append(f"- evidence_refs: {_join_or_none(report.evidence_refs)}")
    if report.artifacts:
        lines.append("- claims:")
        lines.extend(f"  - {artifact}" for artifact in report.artifacts)
    return "\n".join(lines)


def _task_status(task_id: str, run_state) -> str:
    for record in list(getattr(run_state, "tasks", []) or []):
        if str(getattr(record, "id", "") or "") == task_id:
            return str(getattr(record, "status", "") or "")
    return ""


def _files_read_from_context(task_id: str, run_state) -> list[str]:
    run_context = getattr(run_state, "run_context", None)
    if run_context is None:
        return []
    paths: list[str] = []
    snapshots = getattr(run_context, "file_snapshots", {}) or {}
    artifacts = snapshots.values() if isinstance(snapshots, dict) else list(snapshots)
    for artifact in artifacts:
        task_ids = set(getattr(artifact, "task_ids", ()) or ())
        if task_id in task_ids:
            paths.append(str(getattr(artifact, "path", "") or ""))
    return paths


def _tool_calls_for_task(task_id: str, run_state) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for event in _events_for_task(task_id, run_state):
        event_type = str(getattr(event, "event_type", "") or "")
        if event_type not in {"ToolInvoked", "FastPathUsed"}:
            continue
        payload = dict(getattr(event, "payload", {}) or {})
        tool = str(payload.get("tool") or payload.get("tool_name") or getattr(event, "agent", "") or "")
        action = str(payload.get("action") or payload.get("command") or event_type or "")
        if not tool and event_type == "FastPathUsed":
            tool = "fast_path"
        if not tool:
            continue
        calls.append(
            {
                "tool": tool,
                "action": action or event_type,
                "status": str(getattr(event, "status", "") or ""),
                "outcome": str(payload.get("outcome") or payload.get("decision") or ""),
                "tool_rule": str(payload.get("tool_rule") or ""),
                "arguments_summary": dict(payload.get("arguments_summary") or {}),
                "files_touched": list(payload.get("files_touched") or []),
                "risk": dict(payload.get("risk") or {}),
                "timestamp": str(payload.get("timestamp") or ""),
            }
        )
    return _dedupe_tool_calls(calls)


def _files_written_from_events(task_id: str, run_state) -> list[str]:
    paths: list[str] = []
    for event in _events_for_task(task_id, run_state):
        if str(getattr(event, "event_type", "") or "") != "ToolInvoked":
            continue
        payload = dict(getattr(event, "payload", {}) or {})
        outcome = str(payload.get("outcome") or payload.get("decision") or "").strip().lower()
        status = str(getattr(event, "status", "") or "").strip().lower()
        if not _is_completed_tool_outcome(outcome, status):
            continue
        for touched in list(payload.get("files_touched") or []):
            if not isinstance(touched, dict):
                continue
            access = str(touched.get("access") or "").strip().lower()
            if access != "write":
                continue
            paths.append(_normalize_report_path(touched.get("path")))
    return _unique_strings(paths)


def _blockers_for_task(task_id: str, run_state) -> list[str]:
    blockers = []
    for event in _events_for_task(task_id, run_state):
        if str(getattr(event, "status", "") or "") == "failed" or str(getattr(event, "event_type", "") or "") == "TaskFailed":
            message = str(getattr(event, "message", "") or "")
            if message:
                blockers.append(message)
    return _unique_strings(blockers)


def _events_for_task(task_id: str, run_state) -> list:
    bus = getattr(run_state, "event_bus", None)
    if bus is None or not hasattr(bus, "snapshot"):
        return []
    events = []
    for event in bus.snapshot():
        if str(getattr(event, "task_id", "") or "") == task_id:
            events.append(event)
    return events


def _evidence_refs(task_id: str, run_state, *, files_read: list[str], files_written: list[str]) -> list[str]:
    refs = [f"task:{task_id}"] if task_id else []
    refs.extend(f"read:{path}" for path in files_read)
    refs.extend(f"write:{path}" for path in files_written)
    return _unique_strings(refs)


def _summary_from_output(output: str, limit: int = 500) -> str:
    text = " ".join(str(output or "").strip().split())
    if len(text) <= limit:
        return text
    return text[:limit] + f"...[truncated {len(text) - limit} chars]"


def _claimed_artifacts_from_output(output: str) -> list[str]:
    fields = _parse_worker_report_block(output)
    if not fields:
        return []
    aliases = {
        "完成内容": "claimed_completed",
        "completed": "claimed_completed",
        "summary": "claimed_completed",
        "读取依据": "claimed_evidence",
        "evidence": "claimed_evidence",
        "修改内容": "claimed_changes",
        "changes": "claimed_changes",
        "验证结果": "claimed_verification",
        "verification": "claimed_verification",
        "风险/未完成": "claimed_risks",
        "风险": "claimed_risks",
        "未完成": "claimed_risks",
        "risks": "claimed_risks",
        "blockers": "claimed_risks",
    }
    artifacts: list[str] = []
    for label, value in fields:
        key = aliases.get(label.strip().lower()) or aliases.get(label.strip())
        if key and value:
            artifacts.append(f"{key}: {value}")
    return _unique_artifact_strings(artifacts)


def _parse_worker_report_block(output: str) -> list[tuple[str, str]]:
    lines = str(output or "").splitlines()
    in_block = False
    fields: list[tuple[str, str]] = []
    current_label = ""
    current_value: list[str] = []
    for raw_line in lines:
        line = raw_line.strip()
        if not in_block:
            if line.lower().lstrip("#").strip() == "workerreport":
                in_block = True
            continue
        if line.startswith("#") and line.lower().lstrip("#").strip() != "workerreport":
            break
        parsed = _parse_worker_report_item(line)
        if parsed:
            if current_label:
                fields.append((current_label, " ".join(current_value).strip()))
            current_label, value = parsed
            current_value = [value] if value else []
            continue
        if current_label and line:
            current_value.append(line)
    if current_label:
        fields.append((current_label, " ".join(current_value).strip()))
    return fields


def _parse_worker_report_item(line: str) -> tuple[str, str] | None:
    text = line.strip()
    if text.startswith(("- ", "* ")):
        text = text[2:].strip()
    if not text:
        return None
    for sep in (":", "："):
        if sep in text:
            label, value = text.split(sep, 1)
            label = label.strip().strip("-* ")
            if label:
                return label, value.strip()
    return None


def _format_tool_calls(tool_calls: list[dict[str, Any]]) -> str:
    values = []
    for call in tool_calls:
        tool = str(call.get("tool") or "").strip()
        action = str(call.get("action") or "").strip()
        if tool and action:
            values.append(f"{tool}.{action}")
        elif tool:
            values.append(tool)
    return _join_or_none(values)


def _join_or_none(values: list[str]) -> str:
    cleaned = _unique_strings(values)
    return ", ".join(cleaned) if cleaned else "none"


def _unique_strings(values) -> list[str]:
    result: list[str] = []
    seen = set()
    for value in values:
        clean = _normalize_report_path(value)
        if not clean or clean in seen:
            continue
        seen.add(clean)
        result.append(clean)
    return result


def _normalize_report_path(value) -> str:
    return str(value or "").strip().replace("\\", "/")


def _is_completed_tool_outcome(outcome: str, status: str) -> bool:
    blocked = {"pending", "rejected", "denied", "blocked", "failed", "error", "cancelled", "canceled"}
    completed = {"approved", "success", "completed", "auto_approved", "ok", "done", "supervisor_auto_approved"}
    if outcome in blocked or status in blocked:
        return False
    if outcome in completed or status in completed:
        return True
    return not outcome and not status


def _unique_artifact_strings(values) -> list[str]:
    result: list[str] = []
    seen = set()
    for value in values:
        clean = str(value or "").strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        result.append(clean)
    return result


def _dedupe_tool_calls(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen = set()
    for call in calls:
        key = (
            str(call.get("tool") or ""),
            str(call.get("action") or ""),
            str(call.get("status") or ""),
            str(call.get("outcome") or ""),
            _stable_mapping_items(call.get("arguments_summary") or {}),
            _stable_files_touched(call.get("files_touched") or []),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(call)
    return result


def _stable_mapping_items(value) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, dict):
        return ()
    return tuple(sorted((str(key), str(item)) for key, item in value.items()))


def _stable_files_touched(value) -> tuple[tuple[str, str], ...]:
    items = []
    for item in list(value or []):
        if not isinstance(item, dict):
            continue
        items.append(
            (
                _normalize_report_path(item.get("path")),
                str(item.get("access") or "").strip().lower(),
            )
        )
    return tuple(sorted(items))
