from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


SECRET_MARKERS = ("api_key", "apikey", "token", "secret", "password", "authorization")


def append_operation_log(
    log_path: Path,
    *,
    tool: str,
    action: str,
    reason: str,
    status: str,
    params_summary: dict[str, Any] | None = None,
    approval_required: bool = False,
    approval_note: str = "",
    backup_path: Path | str | None = None,
    result_summary: str = "",
    error: str = "",
) -> dict[str, Any]:
    """Append one structured operation record for audit and future Flywheel use."""

    record = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "tool": tool,
        "action": action,
        "reason": reason,
        "status": status,
        "approval": {
            "required": bool(approval_required),
            "note": approval_note,
        },
        "params_summary": _redact(params_summary or {}),
        "backup": _backup_payload(backup_path),
        "result_summary": _redact_text(result_summary),
        "error": _redact_text(error),
    }
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def _backup_payload(backup_path: Path | str | None) -> dict[str, Any]:
    if not backup_path:
        return {"created": False}
    return {"created": True, "backup_path": str(backup_path)}


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _redact_sensitive_value(str(key), item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _redact_sensitive_value(key: str, value: Any) -> Any:
    if any(marker in key.lower() for marker in SECRET_MARKERS):
        return "[REDACTED_SECRET]"
    return _redact(value)


def _redact_text(value: str) -> str:
    text = str(value)
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
