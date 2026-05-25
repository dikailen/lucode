from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from runtime.safety.command_analyzer import analyze_command
from runtime.safety.verification_commands import extract_explicit_verification_commands


@dataclass(frozen=True)
class AutoApprovalDecision:
    approve: bool
    reason: str = ""
    reject: bool = False
    rejection_message: str = ""


class FullModeApprovalPolicy:
    """Supervisor-scoped auto approval for planned full-mode tool calls."""

    def __init__(
        self,
        *,
        task_id: str = "",
        mcp: list[str] | None = None,
        read_set: list[str] | None = None,
        write_intent: list[str] | None = None,
        instruction: str = "",
        acceptance_criteria: list[str] | None = None,
    ) -> None:
        self.task_id = str(task_id or "")
        self.mcp = {_normalize_token(item) for item in list(mcp or []) if _normalize_token(item)}
        self.read_set = [_normalize_path(item) for item in list(read_set or []) if _normalize_path(item)]
        self.write_intent = [_normalize_path(item) for item in list(write_intent or []) if _normalize_path(item)]
        self.instruction = str(instruction or "")
        self.acceptance_criteria = [str(item or "") for item in list(acceptance_criteria or []) if str(item or "")]

    @classmethod
    def from_task(cls, task) -> "FullModeApprovalPolicy":
        return cls(
            task_id=str(getattr(task, "id", "") or ""),
            mcp=list(getattr(task, "mcp", []) or []),
            read_set=list(getattr(task, "read_set", []) or []),
            write_intent=list(getattr(task, "write_intent", []) or []),
            instruction=str(getattr(task, "instruction", "") or ""),
            acceptance_criteria=list(getattr(task, "acceptance_criteria", []) or []),
        )

    def decide(self, tool_name: str, arguments: str | None) -> AutoApprovalDecision:
        name = str(tool_name or "")
        parsed = _parse_arguments(arguments)
        if _is_dangerous_tool(name):
            return AutoApprovalDecision(False, "dangerous_tool_requires_user")
        if _is_command_tool(name):
            return self._decide_command(parsed)
        if _is_read_tool(name):
            if self._allows_read_tool(name):
                return AutoApprovalDecision(True, "full_supervisor_planned_scope")
            return AutoApprovalDecision(False, "read_tool_not_declared")
        if _is_workspace_edit_tool(name):
            if not self._allows_workspace_edit_tool(name):
                return AutoApprovalDecision(False, "workspace_edit_not_declared")
            if _is_delete_tool(name):
                return AutoApprovalDecision(False, "delete_requires_user")
            touched = _tool_target_paths(name, parsed)
            if touched and self._paths_within_intent(touched, self.write_intent):
                return AutoApprovalDecision(True, "full_supervisor_planned_scope")
            return AutoApprovalDecision(False, "write_path_out_of_scope")
        return AutoApprovalDecision(False, "tool_not_in_supervisor_policy")

    def _allows_read_tool(self, tool_name: str) -> bool:
        del tool_name
        return bool(
            self.mcp
            & {
                "project_filesystem_readonly",
                "skills_filesystem_readonly",
                "code_locator",
                "git_tools",
            }
        )

    def _allows_workspace_edit_tool(self, tool_name: str) -> bool:
        del tool_name
        return "workspace_edit" in self.mcp or bool(self.write_intent)

    def _paths_within_intent(self, touched: list[str], allowed: list[str]) -> bool:
        if not touched or not allowed:
            return False
        return all(any(_path_is_within(path, intent) for intent in allowed) for path in touched)


    def _decide_command(self, parsed: dict[str, Any]) -> AutoApprovalDecision:
        command = str(parsed.get("command") or "").strip()
        explicit_commands = extract_explicit_verification_commands(
            "\n".join(
                [
                    self.task_id,
                    self.instruction,
                    " ".join(self.acceptance_criteria),
                    " ".join(self.read_set),
                    " ".join(self.write_intent),
                    str(parsed.get("reason") or ""),
                ]
            )
        )
        if explicit_commands and _normalize_command(command) not in {
            _normalize_command(item) for item in explicit_commands
        }:
            joined = "；".join(explicit_commands)
            return AutoApprovalDecision(
                False,
                "command_not_explicitly_requested",
                reject=True,
                rejection_message=(
                    f"主管拒绝了未明确请求的命令：{command}。"
                    f"本任务只能使用明确指定的验证命令：{joined}。"
                    "读取文件请改用 project_filesystem_readonly/code_locator，不要用 command_runner 执行内联脚本读文件。"
                ),
            )
        return _decide_command_by_analyzer(command)


def _decide_command_by_analyzer(command: str) -> AutoApprovalDecision:
    if not command:
        return AutoApprovalDecision(False, "command_missing")
    analysis = analyze_command(command)
    if analysis.should_deny or analysis.decision == "deny":
        return AutoApprovalDecision(False, "dangerous_command_requires_user")
    if analysis.decision in {"allow", "allow_limited"}:
        return AutoApprovalDecision(True, "full_supervisor_command_analyzer")
    return AutoApprovalDecision(False, "command_requires_user")


def _parse_arguments(arguments: str | None) -> dict[str, Any]:
    if not arguments:
        return {}
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _is_command_tool(tool_name: str) -> bool:
    lowered = tool_name.lower()
    return "command_runner" in lowered or lowered.endswith("run_command") or "run_command" in lowered


def _is_workspace_edit_tool(tool_name: str) -> bool:
    lowered = tool_name.lower()
    return "workspace_edit" in lowered or any(
        marker in lowered
        for marker in {
            "create_file",
            "write_file",
            "replace_in_file",
            "apply_unified_patch",
        }
    )


def _is_delete_tool(tool_name: str) -> bool:
    lowered = tool_name.lower()
    return "delete" in lowered or "safe_delete" in lowered


def _is_dangerous_tool(tool_name: str) -> bool:
    lowered = tool_name.lower()
    return "git_commit" in lowered or "publish" in lowered or _is_delete_tool(lowered)


def _is_read_tool(tool_name: str) -> bool:
    lowered = tool_name.lower()
    return any(
        marker in lowered
        for marker in {
            "read_file",
            "list_directory",
            "search_files",
            "locate_code",
            "get_file_outline",
            "git_status",
            "git_diff",
            "git_log",
            "git_show",
        }
    )


def _tool_target_paths(tool_name: str, parsed: dict[str, Any]) -> list[str]:
    lowered = tool_name.lower()
    if "apply_unified_patch" in lowered:
        return _patch_paths(str(parsed.get("patch") or ""))
    candidates = []
    for key in ("target_path", "path", "target", "file_path"):
        value = _normalize_path(parsed.get(key))
        if value:
            candidates.append(value)
    return candidates


def _patch_paths(patch: str) -> list[str]:
    paths: list[str] = []
    for line in patch.splitlines():
        if not (line.startswith("+++ ") or line.startswith("--- ")):
            continue
        raw = line[4:].strip()
        if raw == "/dev/null":
            continue
        if raw.startswith("a/") or raw.startswith("b/"):
            raw = raw[2:]
        clean = _normalize_path(raw)
        if clean and clean not in paths:
            paths.append(clean)
    return paths


def _normalize_token(value: Any) -> str:
    return str(value or "").strip().lower()


def _normalize_command(value: str) -> str:
    return " ".join(str(value or "").strip().split()).lower()


def _normalize_path(value: Any) -> str:
    clean = str(value or "").strip().strip("`'\"()[]{}<>")
    if not clean or "://" in clean:
        return ""
    clean = clean.replace("\\", "/").lstrip("./")
    parts = [part for part in clean.split("/") if part and part != "."]
    if any(part == ".." for part in parts):
        return ""
    return "/".join(parts)


def _path_is_within(path: str, intent: str) -> bool:
    path = _normalize_path(path)
    intent = _normalize_path(intent)
    if not path or not intent:
        return False
    if path == intent:
        return True
    intent_parts = PurePosixPath(intent).parts
    path_parts = PurePosixPath(path).parts
    return len(path_parts) > len(intent_parts) and path_parts[: len(intent_parts)] == intent_parts
