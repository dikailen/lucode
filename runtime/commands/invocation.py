from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from typing import Any

from runtime.commands.registry import CommandSpec, all_command_specs


@dataclass(frozen=True)
class CommandInvocation:
    command: str
    expanded_input: str
    spec: CommandSpec


def resolve_mcp_prompt_invocation(user_input: str, workspace_context=None) -> CommandInvocation | None:
    normalized = str(user_input or "").strip()
    if not normalized.lower().startswith("/mcp__"):
        return None
    command, arguments = _split_command_and_arguments(normalized)
    spec = _find_exact_mcp_prompt_spec(command, workspace_context)
    if spec is None:
        return None
    return CommandInvocation(
        command=command,
        expanded_input=expand_mcp_prompt_invocation(spec, arguments),
        spec=spec,
    )


def expand_mcp_prompt_invocation(spec: CommandSpec, arguments: str = "") -> str:
    metadata = spec.metadata or {}
    argument_specs = _argument_specs(metadata.get("arguments"))
    argument_values = _argument_values(arguments)
    placeholders = _placeholder_values(argument_specs, argument_values, arguments)
    prompt_text = _render_prompt_text(str(metadata.get("prompt_text") or spec.description), placeholders)
    missing_arguments = [
        item["name"]
        for item in argument_specs
        if item.get("required") and not str(placeholders.get(str(item["name"]), "")).strip()
    ]

    lines = [
        "请根据下面的 MCP Prompt 执行任务。",
        f"MCP Prompt 命令：{spec.command}",
        f"MCP：{metadata.get('mcp_name') or metadata.get('mcp_id') or 'unknown'}",
        f"来源：{_source_label(metadata.get('source'))}",
        f"状态：{_trust_label(metadata.get('trusted'))}，{_enabled_label(metadata.get('enabled'))}",
    ]
    if not metadata.get("trusted") or not metadata.get("enabled"):
        lines.append("安全边界：这是静态 prompt 展开，不代表该 MCP 已信任或已启用；不要绕过现有权限、信任和审批流程。")
    if spec.allowed_tools:
        lines.append(f"Prompt 建议工具：{', '.join(spec.allowed_tools)}")
    if spec.model:
        lines.append(f"Prompt 建议模型：{spec.model}")
    if spec.disable_model_invocation:
        lines.append("Prompt 标记：disable-model-invocation=true；如需继续调用模型，请先说明原因并保持最小执行范围。")
    if arguments.strip():
        lines.append(f"用户参数：{arguments.strip()}")
    if missing_arguments:
        lines.append(f"缺少参数：{', '.join(missing_arguments)}")
    lines.extend(["", "Prompt 内容：", prompt_text])
    return "\n".join(lines)


def _find_exact_mcp_prompt_spec(command: str, workspace_context=None) -> CommandSpec | None:
    normalized = command.strip().lower()
    for spec in all_command_specs(workspace_context):
        if spec.command.lower() != normalized:
            continue
        if (spec.metadata or {}).get("kind") == "mcp_prompt":
            return spec
    return None


def _split_command_and_arguments(value: str) -> tuple[str, str]:
    parts = value.split(maxsplit=1)
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1].strip()


def _argument_values(arguments: str) -> list[str]:
    if not arguments.strip():
        return []
    try:
        return shlex.split(arguments)
    except ValueError:
        return arguments.split()


def _argument_specs(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, Any]] = []
    for raw in value:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        normalized.append(
            {
                "name": name,
                "description": str(raw.get("description") or "").strip(),
                "required": raw.get("required", True) is not False,
            }
        )
    return normalized


def _placeholder_values(argument_specs: list[dict[str, Any]], argument_values: list[str], raw_arguments: str) -> dict[str, str]:
    values = {
        "input": raw_arguments.strip(),
        "arguments": raw_arguments.strip(),
        "args": raw_arguments.strip(),
    }
    for index, item in enumerate(argument_specs):
        values[str(item["name"])] = argument_values[index] if index < len(argument_values) else ""
    return values


def _render_prompt_text(template: str, placeholders: dict[str, str]) -> str:
    rendered = str(template or "").strip()
    for key, value in placeholders.items():
        rendered = rendered.replace("{{" + key + "}}", value)
        rendered = rendered.replace("{" + key + "}", value)
    return re.sub(r"\n{3,}", "\n\n", rendered).strip()


def _source_label(value: Any) -> str:
    return {
        "core": "内置核心",
        "user": "用户全局",
        "workspace": "当前项目",
    }.get(str(value or ""), str(value or "未知"))


def _trust_label(value: Any) -> str:
    return "已信任" if value else "未信任"


def _enabled_label(value: Any) -> str:
    return "已启用" if value else "未启用"
