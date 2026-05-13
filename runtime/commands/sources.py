from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CommandSourceRoots:
    app_home: Path
    user_home: Path
    workspace_root: Path


def discover_external_command_specs(command_spec_cls, workspace_context=None) -> list[Any]:
    roots = _command_source_roots(workspace_context)
    specs: list[Any] = []
    specs.extend(_discover_markdown_commands(command_spec_cls, roots.workspace_root / ".lucode" / "commands", "project"))
    specs.extend(_discover_markdown_commands(command_spec_cls, roots.user_home / "commands", "user"))
    specs.extend(_discover_skill_commands(command_spec_cls, workspace_context))
    specs.extend(_discover_mcp_prompt_commands(command_spec_cls, workspace_context))
    return specs


def _discover_markdown_commands(command_spec_cls, root: Path, source: str) -> list[Any]:
    if not root.exists():
        return []
    specs: list[Any] = []
    group = "项目命令" if source == "project" else "用户命令"
    for path in sorted(root.glob("*.md")):
        command = _slash_command_from_stem(path.stem)
        if not command:
            continue
        meta, body = _read_markdown_frontmatter(path)
        description = meta.get("description") or _first_markdown_summary(body) or f"{group}：{path.stem}"
        argument_hint = meta.get("argument-hint") or meta.get("argument_hint") or ""
        specs.append(
            command_spec_cls(
                command=command,
                description=description,
                group=group,
                argument_hint=argument_hint,
                source=source,
                path=str(path),
                allowed_tools=tuple(_parse_list(meta.get("allowed-tools") or meta.get("allowed_tools"))),
                model=meta.get("model") or "",
                disable_model_invocation=_parse_bool(
                    meta.get("disable-model-invocation") or meta.get("disable_model_invocation")
                ),
                metadata={"kind": "markdown_command"},
            )
        )
    return specs


def _discover_skill_commands(command_spec_cls, workspace_context=None) -> list[Any]:
    try:
        from runtime.config.extensions import discover_skill_layers
    except Exception:
        return []

    specs: list[Any] = []
    for source, items in discover_skill_layers(workspace_context).items():
        for item in items:
            if item.get("selectable") is False:
                continue
            command = _skill_command(item)
            if not command:
                continue
            aliases = _skill_aliases(item, command)
            description = item.get("summary_zh") or item.get("display_name_zh") or str(item.get("id") or command)
            specs.append(
                command_spec_cls(
                    command=command,
                    description=description,
                    group="Skill",
                    aliases=aliases,
                    source=f"{source}_skill",
                    path=str(item.get("path") or ""),
                    metadata={
                        "kind": "skill",
                        "skill_id": item.get("id") or "",
                        "blocked": bool(item.get("blocked")),
                        "selectable": bool(item.get("selectable", True)),
                    },
                )
            )
    return specs


def _discover_mcp_prompt_commands(command_spec_cls, workspace_context=None) -> list[Any]:
    try:
        from runtime.config.extensions import discover_mcp_layers
    except Exception:
        return []

    specs: list[Any] = []
    for source, items in discover_mcp_layers(workspace_context).items():
        for item in items:
            mcp_id = _normalize_command_part(item.get("id") or item.get("display_name_zh") or "mcp")
            if not mcp_id:
                continue
            for prompt in _iter_mcp_prompts(item.get("prompts")):
                prompt_id = _normalize_command_part(prompt.get("name") or prompt.get("id") or prompt.get("title"))
                if not prompt_id:
                    continue
                prompt_name = prompt.get("name") or prompt.get("id") or prompt.get("title") or prompt_id
                specs.append(
                    command_spec_cls(
                        command=f"/mcp__{mcp_id}__{prompt_id}",
                        description=_mcp_prompt_description(item, prompt),
                        group="MCP Prompt",
                        argument_hint=_mcp_prompt_argument_hint(prompt),
                        source=f"{source}_mcp_prompt",
                        path=str(item.get("path") or ""),
                        allowed_tools=tuple(
                            _parse_list(prompt.get("allowed-tools") or prompt.get("allowed_tools"))
                        ),
                        model=str(prompt.get("model") or ""),
                        disable_model_invocation=_parse_bool(
                            prompt.get("disable-model-invocation") or prompt.get("disable_model_invocation")
                        ),
                        metadata={
                            "kind": "mcp_prompt",
                            "mcp_id": item.get("id") or "",
                            "mcp_name": item.get("display_name_zh") or item.get("display_name") or item.get("id") or "",
                            "prompt_id": prompt_name,
                            "prompt_text": _mcp_prompt_text(prompt),
                            "arguments": _mcp_prompt_arguments(prompt),
                            "trusted": bool(item.get("trusted")),
                            "enabled": bool(item.get("enabled")),
                            "source": source,
                        },
                    )
                )
    return specs


def _command_source_roots(workspace_context=None) -> CommandSourceRoots:
    app_home = _context_path(workspace_context, "app_home", "LUCODE_APP_HOME", Path.cwd())
    user_home = _context_path(workspace_context, "user_home", "LUCODE_USER_HOME", Path.home() / ".lucode")
    workspace_root = _context_path(workspace_context, "workspace_root", "LUCODE_WORKSPACE_ROOT", Path.cwd())
    return CommandSourceRoots(app_home=app_home, user_home=user_home, workspace_root=workspace_root)


def _context_path(workspace_context, attr: str, env_name: str, default: Path) -> Path:
    value = getattr(workspace_context, attr, None)
    if value is None:
        value = os.environ.get(env_name) or default
    return Path(value).resolve()


def _slash_command_from_stem(stem: str) -> str:
    slug = str(stem or "").strip().lower()
    slug = re.sub(r"\s+", "-", slug)
    slug = re.sub(r"[^0-9a-zA-Z_\-\u4e00-\u9fff]+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-_")
    return f"/{slug}" if slug else ""


def _skill_command(item: dict[str, Any]) -> str:
    folder = str(item.get("folder") or "").strip()
    if folder:
        return _slash_command_from_stem(folder)
    skill_id = str(item.get("id") or "").strip().replace("_", "-")
    return _slash_command_from_stem(skill_id)


def _skill_aliases(item: dict[str, Any], command: str) -> tuple[str, ...]:
    skill_id = str(item.get("id") or "").strip().lower().replace("_", "-")
    alias = _slash_command_from_stem(skill_id)
    if not alias or alias == command:
        return ()
    return (alias,)


def _iter_mcp_prompts(value: Any) -> list[dict[str, Any]]:
    prompts: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for key, raw in value.items():
            if isinstance(raw, dict):
                item = dict(raw)
                item.setdefault("name", key)
            else:
                item = {"name": key, "description": str(raw or "")}
            prompts.append(item)
        return prompts
    if isinstance(value, list):
        for raw in value:
            if isinstance(raw, dict):
                prompts.append(dict(raw))
            elif str(raw or "").strip():
                prompts.append({"name": str(raw).strip()})
    return prompts


def _mcp_prompt_description(mcp_item: dict[str, Any], prompt: dict[str, Any]) -> str:
    prompt_name = str(prompt.get("name") or prompt.get("id") or prompt.get("title") or "prompt")
    description = (
        prompt.get("description")
        or prompt.get("summary")
        or prompt.get("display_name")
        or prompt.get("display_name_zh")
        or prompt_name
    )
    mcp_name = mcp_item.get("display_name_zh") or mcp_item.get("display_name") or mcp_item.get("id") or "MCP"
    return f"{mcp_name}: {description}"


def _mcp_prompt_argument_hint(prompt: dict[str, Any]) -> str:
    explicit = prompt.get("argument-hint") or prompt.get("argument_hint")
    if explicit:
        return str(explicit)
    arguments = prompt.get("arguments") or prompt.get("args")
    if not isinstance(arguments, list):
        return ""
    parts: list[str] = []
    for raw in arguments:
        if isinstance(raw, dict):
            name = str(raw.get("name") or raw.get("id") or "").strip()
            required = raw.get("required", True) is not False
        else:
            name = str(raw or "").strip()
            required = True
        if not name:
            continue
        parts.append(f"<{name}>" if required else f"[{name}]")
    return " ".join(parts)


def _mcp_prompt_text(prompt: dict[str, Any]) -> str:
    for key in ("prompt", "content", "template", "instruction", "instructions"):
        value = prompt.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _mcp_prompt_arguments(prompt: dict[str, Any]) -> list[dict[str, Any]]:
    arguments = prompt.get("arguments") or prompt.get("args")
    if not isinstance(arguments, list):
        return []
    normalized: list[dict[str, Any]] = []
    for raw in arguments:
        if isinstance(raw, dict):
            name = str(raw.get("name") or raw.get("id") or "").strip()
            description = str(raw.get("description") or raw.get("summary") or "").strip()
            required = raw.get("required", True) is not False
        else:
            name = str(raw or "").strip()
            description = ""
            required = True
        if not name:
            continue
        normalized.append({"name": name, "description": description, "required": required})
    return normalized


def _read_markdown_frontmatter(path: Path) -> tuple[dict[str, str], str]:
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return {}, ""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    end_index = None
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end_index = index
            break
    if end_index is None:
        return {}, text
    meta: dict[str, str] = {}
    for raw_line in lines[1:end_index]:
        line = raw_line.strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip().lower()] = value.strip().strip("\"'")
    body = "\n".join(lines[end_index + 1 :])
    return meta, body


def _first_markdown_summary(body: str) -> str:
    for raw_line in str(body or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            line = line.lstrip("#").strip()
        return line[:120]
    return ""


def _parse_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    stripped = str(value).strip().strip("[]")
    return [item.strip().strip("\"'") for item in re.split(r"[,;\n]+", stripped) if item.strip().strip("\"'")]


def _parse_bool(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _normalize_command_part(value: Any) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_")
    normalized = re.sub(r"[^a-z0-9_]+", "_", normalized)
    return re.sub(r"_+", "_", normalized).strip("_")
