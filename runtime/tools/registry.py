from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from runtime.config.extensions import discover_mcp_layers
from runtime.safety.privacy import normalize_privacy_mode


@dataclass(frozen=True)
class ToolServerRecord:
    id: str
    display_name: str
    summary: str
    tools: tuple[str, ...]
    source: str
    capability: str
    risk_level: str
    approval_policy: str
    offline_allowed: bool
    budget_policy: str
    log_policy: str
    backup_policy: str
    model_requirement: str
    side_effects: str
    trusted: bool
    enabled: bool
    available: bool
    unavailable_reason: str = ""


class ToolRegistry:
    def __init__(self, servers: list[ToolServerRecord]):
        self.servers = list(servers)
        self._by_id = {server.id: server for server in self.servers}

    def server(self, server_id: str) -> ToolServerRecord | None:
        return self._by_id.get(str(server_id or "").strip())

    def require_server(self, server_id: str) -> ToolServerRecord:
        record = self.server(server_id)
        if record is None:
            raise KeyError(f"Unknown tool server id: {server_id}")
        return record

    def validate_core_mcp_start(self, server_id: str) -> ToolServerRecord:
        record = self.server(server_id)
        if record is None or record.source != "core" or not record.enabled or not record.available:
            reason = record.unavailable_reason if record else "unknown or extension MCP"
            raise KeyError(f"{server_id} is not registered as an enabled core MCP ({reason})")
        return record

    def by_source(self, source: str) -> list[ToolServerRecord]:
        return [server for server in self.servers if server.source == source]


CORE_SERVER_METADATA: dict[str, dict[str, Any]] = {
    "project_filesystem_readonly": {
        "capability": "read",
        "offline_allowed": True,
        "budget_policy": "read-only budget: max read calls/files/chars/tree entries",
        "log_policy": "read-only MCP session; no mutation operation log",
        "backup_policy": "none",
        "model_requirement": "basic chat or tool-capable model",
    },
    "skills_filesystem_readonly": {
        "capability": "read",
        "offline_allowed": True,
        "budget_policy": "read-only budget: max read calls/files/chars/tree entries",
        "log_policy": "read-only MCP session; no mutation operation log",
        "backup_policy": "none",
        "model_requirement": "basic chat or tool-capable model",
    },
    "code_locator": {
        "capability": "code_index",
        "offline_allowed": True,
        "budget_policy": "local index budget: max files/file bytes plus SQLite graph cache",
        "log_policy": "cache rebuild/hit only; no mutation operation log",
        "backup_policy": "none",
        "model_requirement": "basic chat or tool-capable model",
    },
    "safe_backup": {
        "capability": "backup",
        "offline_allowed": True,
        "budget_policy": "backup budget: max bytes/files before zip creation",
        "log_policy": "writes unified operation log for backup attempts",
        "backup_policy": "creates zip backup without deleting original files",
        "model_requirement": "tool-capable model",
    },
    "workspace_edit": {
        "capability": "write",
        "offline_allowed": True,
        "budget_policy": "strict sha256 optimistic concurrency plus backup size/file budgets",
        "log_policy": "writes unified operation log for every mutation attempt",
        "backup_policy": "zip backup before overwrite, patch, or delete",
        "model_requirement": "tool-capable model",
    },
    "command_runner": {
        "capability": "shell",
        "offline_allowed": True,
        "budget_policy": "no shell execution; argv parsing, timeout, cwd confinement, deny list",
        "log_policy": "writes unified operation log before local process execution",
        "backup_policy": "none",
        "model_requirement": "tool-capable model",
    },
    "git_tools": {
        "capability": "git",
        "offline_allowed": True,
        "budget_policy": "read-only git status/diff/log fast path; commit only with approval",
        "log_policy": "logs commit and runtime fast-path git reads",
        "backup_policy": "none",
        "model_requirement": "tool-capable model",
        "summary": "Read-only git status/diff/log are available; local commit requires approval.",
    },
    "web_search": {
        "capability": "web",
        "offline_allowed": False,
        "budget_policy": "network timeout and max result/fetch limits",
        "log_policy": "logs network search/fetch fast path metadata",
        "backup_policy": "none",
        "model_requirement": "tool-capable model with network permission",
    },
    "context7_docs": {
        "capability": "docs",
        "offline_allowed": False,
        "budget_policy": "remote MCP timeout and narrow library-query budget",
        "log_policy": "remote docs lookup metadata only; no project file mutation",
        "backup_policy": "none",
        "model_requirement": "tool-capable model with network permission",
    },
    "grep_code_search": {
        "capability": "code_search",
        "offline_allowed": False,
        "budget_policy": "remote MCP timeout and narrow public GitHub query budget",
        "log_policy": "remote public code search metadata only; no project file mutation",
        "backup_policy": "none",
        "model_requirement": "tool-capable model with network permission",
    },
}


def build_tool_registry(settings=None, workspace_context=None) -> ToolRegistry:
    privacy_mode = normalize_privacy_mode(
        getattr(settings, "privacy_mode", None) or os.environ.get("AGENTS_PRIVACY_MODE") or "local_first"
    )
    servers: list[ToolServerRecord] = []
    layers = discover_mcp_layers(workspace_context)
    for source in ("core", "user", "workspace"):
        for item in layers.get(source) or []:
            servers.append(_record_from_mcp_item(item, privacy_mode))
    return ToolRegistry(servers)


def render_tool_registry(settings=None, workspace_context=None, include_all: bool = False) -> str:
    registry = build_tool_registry(settings, workspace_context)
    privacy_mode = normalize_privacy_mode(
        getattr(settings, "privacy_mode", None) or os.environ.get("AGENTS_PRIVACY_MODE") or "local_first"
    )
    lines = [
        "全部工具注册表" if include_all else "工具注册表",
        f"隐私模式：{_privacy_label(privacy_mode)}",
    ]

    sources = [
        ("内置核心", "core"),
        ("用户全局", "user"),
        ("当前项目", "workspace"),
    ] if include_all else [("内置核心", "core")]

    for title, source in sources:
        lines.append("")
        lines.append(title)
        records = registry.by_source(source)
        if not records:
            lines.append("- 无")
            continue
        for record in records:
            lines.extend(_render_server_record(record))
    return "\n".join(lines)


def _record_from_mcp_item(item: dict[str, Any], privacy_mode: str) -> ToolServerRecord:
    server_id = str(item.get("id") or "").strip()
    source = str(item.get("source") or "core").strip() or "core"
    metadata = CORE_SERVER_METADATA.get(server_id, {}) if source == "core" else {}
    trusted = bool(item.get("trusted", source == "core"))
    enabled = bool(item.get("enabled", True))
    offline_allowed = bool(metadata.get("offline_allowed", item.get("offline_allowed", False if source != "core" else True)))
    approval_policy = _approval_policy(item.get("approval_required", metadata.get("approval_policy", False)))
    reason = _unavailable_reason(
        source=source,
        trusted=trusted,
        enabled=enabled,
        offline_allowed=offline_allowed,
        privacy_mode=privacy_mode,
    )
    summary = str(metadata.get("summary") or item.get("summary_zh") or item.get("summary") or "")

    return ToolServerRecord(
        id=server_id,
        display_name=str(item.get("display_name_zh") or item.get("display_name") or server_id),
        summary=summary,
        tools=tuple(str(tool) for tool in (item.get("tools") or []) if str(tool).strip()),
        source=source,
        capability=str(metadata.get("capability") or _infer_capability(item)),
        risk_level=str(item.get("risk_level") or metadata.get("risk_level") or "unknown"),
        approval_policy=approval_policy,
        offline_allowed=offline_allowed,
        budget_policy=str(metadata.get("budget_policy") or item.get("budget_policy") or _default_budget_policy(source)),
        log_policy=str(metadata.get("log_policy") or item.get("log_policy") or _default_log_policy(source)),
        backup_policy=str(metadata.get("backup_policy") or item.get("backup_policy") or "none"),
        model_requirement=str(metadata.get("model_requirement") or item.get("model_requirement") or "tool-capable model"),
        side_effects=str(item.get("side_effects") or metadata.get("side_effects") or "unknown"),
        trusted=trusted,
        enabled=enabled,
        available=not reason,
        unavailable_reason=reason,
    )


def _approval_policy(value: Any) -> str:
    if value is True:
        return "always"
    if value is False or value is None:
        return "never"
    text = str(value).strip()
    if text == "git_commit_only":
        return "git_commit_only"
    return text or "never"


def _infer_capability(item: dict[str, Any]) -> str:
    tools = {str(tool).strip().lower() for tool in (item.get("tools") or [])}
    side_effects = str(item.get("side_effects") or "").lower()
    if tools.intersection({"create_file", "write_file", "replace_in_file", "apply_unified_patch", "delete_file"}):
        return "write"
    if "run_command" in tools:
        return "shell"
    if any(tool.startswith("git_") for tool in tools):
        return "git"
    if tools.intersection({"web_search", "web_fetch"}) or "network" in side_effects:
        return "web"
    if "backup" in side_effects or "safe_delete_file" in tools:
        return "backup"
    if tools.intersection({"read_file", "list_directory", "search_files", "locate_code", "get_file_outline"}):
        return "read"
    return "extension"


def _unavailable_reason(
    *,
    source: str,
    trusted: bool,
    enabled: bool,
    offline_allowed: bool,
    privacy_mode: str,
) -> str:
    reasons = []
    if source != "core" and not trusted:
        reasons.append("未信任")
    if not enabled:
        reasons.append("未启用")
    if privacy_mode == "offline" and not offline_allowed:
        reasons.append("offline 模式禁用联网工具")
    return "；".join(reasons)


def _render_server_record(record: ToolServerRecord) -> list[str]:
    status = "可用" if record.available else f"不可用：{record.unavailable_reason}"
    offline = "offline 可用" if record.offline_allowed else "需联网"
    tools = ", ".join(record.tools) or "未声明"
    return [
        (
            f"- {record.id} | {record.display_name} | {status} | 能力 {record.capability} | "
            f"风险 {record.risk_level} | 审批 {record.approval_policy} | {offline}"
        ),
        f"  工具：{tools}",
        f"  预算：{record.budget_policy}",
        f"  日志：{record.log_policy}",
        f"  备份：{record.backup_policy}",
    ]


def _default_budget_policy(source: str) -> str:
    if source == "core":
        return "registered core budget"
    return "extension budget not declared; require manual review before enable"


def _default_log_policy(source: str) -> str:
    if source == "core":
        return "registered core log policy"
    return "extension log policy not declared; require manual review before enable"


def _privacy_label(value: str) -> str:
    return {
        "offline": "离线模式",
        "local_first": "本地优先",
        "cloud_allowed": "允许云端",
    }.get(str(value or ""), str(value or "未知"))
