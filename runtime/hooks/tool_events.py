from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from runtime.safety.command_analyzer import analyze_command


SECRET_MARKERS = ("api_key", "apikey", "token", "secret", "password", "authorization")
DEFAULT_AUDIT_LIMIT = 12


@dataclass(frozen=True)
class ToolHookEvent:
    event_type: str
    tool_name: str
    tool_rule: str = ""
    status: str = ""
    decision: str = ""
    reason: str = ""
    arguments_summary: dict[str, Any] = field(default_factory=dict)
    risk: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "time": self.timestamp,
            "event_type": self.event_type,
            "tool_name": self.tool_name,
            "tool_rule": self.tool_rule,
            "status": self.status,
            "decision": self.decision,
            "reason": self.reason,
            "arguments_summary": self.arguments_summary,
            "risk": self.risk,
        }


def build_tool_event(
    event_type: str,
    tool_name: str,
    arguments: str | None,
    *,
    tool_rule: str = "",
    status: str = "",
    decision: str = "",
    reason: str = "",
) -> ToolHookEvent:
    parsed = _parse_arguments(arguments)
    return ToolHookEvent(
        event_type=str(event_type or ""),
        tool_name=str(tool_name or ""),
        tool_rule=str(tool_rule or ""),
        status=str(status or ""),
        decision=str(decision or ""),
        reason=str(reason or ""),
        arguments_summary=_summarize_arguments(parsed, arguments),
        risk=_risk_payload(tool_name, parsed),
    )


def record_pre_tool_use(hooks, tool_name: str, arguments: str | None, *, tool_rule: str = "") -> ToolHookEvent:
    event = build_tool_event("pre_tool_use", tool_name, arguments, tool_rule=tool_rule, status="pending")
    _emit_tool_event(hooks, event)
    return event


def record_post_tool_use(
    hooks,
    pre_event: ToolHookEvent | None,
    *,
    decision: str,
    status: str,
    reason: str = "",
) -> ToolHookEvent:
    if pre_event is None:
        event = ToolHookEvent(
            event_type="post_tool_use",
            decision=str(decision or ""),
            status=str(status or ""),
            reason=str(reason or ""),
        )
    else:
        event = ToolHookEvent(
            event_type="post_tool_use",
            tool_name=pre_event.tool_name,
            tool_rule=pre_event.tool_rule,
            status=str(status or ""),
            decision=str(decision or ""),
            reason=str(reason or ""),
            arguments_summary=pre_event.arguments_summary,
            risk=pre_event.risk,
        )
    _emit_tool_event(hooks, event)
    return event


def audit_log_path(workspace_root: str | Path | None = None) -> Path:
    root = Path(workspace_root or os.environ.get("LUCODE_WORKSPACE_ROOT") or os.getcwd()).resolve()
    return root / ".lucode" / "audit" / "tool_events.jsonl"


def append_tool_event_audit(event: ToolHookEvent, workspace_root: str | Path | None = None) -> Path:
    path = audit_log_path(workspace_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(_redact_payload(event.to_dict()), ensure_ascii=False) + "\n")
    return path


def load_tool_event_audit(workspace_root: str | Path | None = None, limit: int = DEFAULT_AUDIT_LIMIT) -> list[dict[str, Any]]:
    path = audit_log_path(workspace_root)
    if not path.exists():
        return []
    max_items = max(1, min(int(limit or DEFAULT_AUDIT_LIMIT), 100))
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    records: list[dict[str, Any]] = []
    for line in lines[-max_items:]:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def render_tool_event_audit(workspace_root: str | Path | None = None, limit: int = DEFAULT_AUDIT_LIMIT) -> str:
    path = audit_log_path(workspace_root)
    records = load_tool_event_audit(workspace_root, limit=limit)
    lines = [
        "工具审计",
        f"记录文件：{path}",
    ]
    if not records:
        lines.append("状态：暂无工具审批事件。")
        return "\n".join(lines)
    lines.append(f"最近 {len(records)} 条：")
    for record in records:
        event_type = str(record.get("event_type") or "unknown")
        tool_name = str(record.get("tool_name") or "unknown")
        status = str(record.get("status") or "-")
        decision = str(record.get("decision") or "-")
        risk = record.get("risk") if isinstance(record.get("risk"), dict) else {}
        risk_text = str(risk.get("risk_level") or "-")
        lines.append(f"- {record.get('time', '')} | {event_type} | {tool_name} | {status}/{decision} | 风险 {risk_text}")
    return "\n".join(lines)


def _emit_tool_event(hooks, event: ToolHookEvent) -> None:
    if hooks is None:
        return
    recorder = getattr(hooks, "record_tool_event", None)
    if callable(recorder):
        recorder(event)
        return
    events = getattr(hooks, "tool_events", None)
    if isinstance(events, list):
        events.append(event)


def _parse_arguments(arguments: str | None) -> Any:
    if not arguments:
        return {}
    try:
        return json.loads(arguments)
    except json.JSONDecodeError:
        return str(arguments)


def _summarize_arguments(parsed: Any, raw_arguments: str | None) -> dict[str, Any]:
    if isinstance(parsed, dict):
        summary: dict[str, Any] = {"keys": sorted(str(key) for key in parsed.keys())}
        for key in ("path", "target", "target_path", "file_path", "command", "message", "reason"):
            if key in parsed:
                summary[key] = _redact_text(_truncate(parsed.get(key)))
        if "patch" in parsed:
            summary["patch_paths"] = _patch_paths(str(parsed.get("patch") or ""))
        for key in ("content", "patch", "old_text", "new_text"):
            if key in parsed:
                summary[f"{key}_length"] = len(str(parsed.get(key) or ""))
        return summary
    if isinstance(parsed, str):
        return {"raw": _redact_text(_truncate(parsed))}
    return {"raw": _redact_text(_truncate(raw_arguments or ""))}


def _risk_payload(tool_name: str, parsed: Any) -> dict[str, Any]:
    if not isinstance(parsed, dict):
        return {}
    name = str(tool_name or "").lower()
    command = parsed.get("command") if "command" in name or "run_command" in name else None
    if not command:
        return {}
    analysis = analyze_command(str(command))
    return {
        "kind": "command",
        "risk_level": analysis.risk_level,
        "should_deny": analysis.should_deny,
        "findings": [
            {
                "severity": finding.severity,
                "category": finding.category,
                "message": finding.message,
                "evidence": _redact_text(finding.evidence),
                "blocks_execution": finding.blocks_execution,
            }
            for finding in analysis.findings
        ],
    }


def _truncate(value: Any, limit: int = 500) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[:limit] + f"...[truncated {len(text) - limit} chars]"


def _redact_text(value: Any) -> str:
    text = str(value or "")
    lowered = text.lower()
    if any(marker in lowered for marker in SECRET_MARKERS):
        return "[REDACTED_SECRET]"
    for prefix in ("sk-", "sk_"):
        start = text.find(prefix)
        while start >= 0:
            end = start + len(prefix)
            while end < len(text) and (text[end].isalnum() or text[end] in "-_"):
                end += 1
            if end - start >= 8:
                text = text[:start] + "[REDACTED_SECRET]" + text[end:]
                start = text.find(prefix, start + len("[REDACTED_SECRET]"))
            else:
                start = text.find(prefix, end)
    return text


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


def _redact_payload(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            text_key = str(key)
            redacted[text_key] = "[REDACTED_SECRET]" if _is_secret_key(text_key) else _redact_payload(item)
        return redacted
    if isinstance(value, list):
        return [_redact_payload(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _is_secret_key(key: str) -> bool:
    return any(marker in key.lower() for marker in SECRET_MARKERS)
