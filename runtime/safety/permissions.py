from __future__ import annotations

import fnmatch
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore


DEFAULT_PERMISSION_POLICY: dict[str, Any] = {
    "read": {
        "default": "allow",
        "deny": [".env", "**/.env", "**/*.pem", "**/*secret*", "**/*token*", "**/id_rsa"],
    },
    "write": {
        "default": "ask",
        "deny": [".git/**", ".agent_cache/**", ".agent_quarantine/**", ".env", ".lucode/auth.json"],
    },
    "delete": {
        "default": "ask",
        "deny": [".git/**", ".agent_cache/**", ".agent_quarantine/**", ".env", ".lucode/auth.json"],
    },
    "shell": {
        "default": "ask",
        "deny": ["git reset --hard", "git clean", "git push", "rm -rf", "npm publish"],
    },
    "git": {
        "default": "read_allow_write_ask",
        "deny": ["push", "reset --hard", "clean"],
    },
    "web": {
        "default": "allow",
        "deny": [],
    },
    "mcp": {
        "default": "ask",
        "workspace": {"default": "ask"},
        "deny": [],
    },
}


@dataclass(frozen=True)
class PermissionDecision:
    action: str
    decision: str
    reason: str


def permissions_path(workspace_root: Path | str) -> Path:
    return Path(workspace_root).resolve() / ".lucode" / "permissions.toml"


def load_effective_permissions(workspace_root: Path | str | None = None) -> dict[str, Any]:
    policy = deepcopy(DEFAULT_PERMISSION_POLICY)
    if workspace_root is None:
        return policy
    path = permissions_path(workspace_root)
    if not path.exists():
        return policy
    local_policy = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(local_policy, dict):
        policy = _deep_merge(policy, local_policy)
    return policy


def evaluate_permission(
    policy: dict[str, Any],
    action: str,
    *,
    target: str | None = None,
    command: str | None = None,
    source: str | None = None,
) -> PermissionDecision:
    normalized_action = _normalize_action(action)
    section = policy.get(normalized_action) or {}

    if normalized_action == "mcp":
        source_section = section.get(str(source or "").strip().lower()) or {}
        decision = _normalize_decision(source_section.get("default") or section.get("default") or "ask")
        return PermissionDecision(normalized_action, decision, f"MCP 来源 {source or 'unknown'} 默认策略：{decision}")

    if command:
        for pattern in _as_list(section.get("deny")):
            if pattern.lower() in command.lower():
                return PermissionDecision(normalized_action, "deny", f"命令匹配拒绝规则：{pattern}")

    if target:
        normalized_target = str(target).replace("\\", "/").lstrip("/")
        for pattern in _as_list(section.get("deny")):
            if _path_matches(normalized_target, pattern):
                return PermissionDecision(normalized_action, "deny", f"路径匹配拒绝规则：{pattern}")

    default = _normalize_decision(section.get("default") or "ask")
    if default == "read_allow_write_ask":
        default = "ask"
    return PermissionDecision(normalized_action, default, f"默认策略：{default}")


def render_permission_policy(workspace_root: Path | str) -> str:
    path = permissions_path(workspace_root)
    policy = load_effective_permissions(workspace_root)
    lines = [
        "权限策略",
        f"项目权限文件：{path}",
        f"状态：{'已发现 .lucode/permissions.toml' if path.exists() else '未初始化，使用默认策略'}",
        "",
    ]
    for action in ["read", "write", "delete", "shell", "git", "web"]:
        section = policy.get(action) or {}
        lines.append(f"- {action} | 默认：{section.get('default', 'ask')}")
        deny = _as_list(section.get("deny"))
        if deny:
            lines.append(f"  拒绝：{', '.join(deny)}")
    mcp_section = policy.get("mcp") or {}
    workspace_mcp = mcp_section.get("workspace") or {}
    lines.append(f"- mcp.workspace | 默认：{workspace_mcp.get('default') or mcp_section.get('default') or 'ask'}")
    lines.append("")
    lines.append("说明：read/edit/delete/shell/git/web/mcp 均可在 .lucode/permissions.toml 中配置 allow/ask/deny。")
    return "\n".join(lines)


def _normalize_action(action: str) -> str:
    value = str(action or "").strip().lower()
    return {"edit": "write", "bash": "shell"}.get(value, value)


def _normalize_decision(value: Any) -> str:
    text = str(value or "ask").strip().lower()
    return text if text in {"allow", "ask", "deny", "read_allow_write_ask"} else "ask"


def _path_matches(path: str, pattern: str) -> bool:
    normalized_pattern = str(pattern or "").replace("\\", "/").lstrip("/")
    if not normalized_pattern:
        return False
    return fnmatch.fnmatch(path, normalized_pattern) or fnmatch.fnmatch(f"/{path}", normalized_pattern)


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged
