from __future__ import annotations

from runtime.commands.registry import search_command_specs


def render_command_palette(filter_text: str = "", workspace_context=None) -> str:
    items = search_command_specs(filter_text, workspace_context=workspace_context)
    lines = [
        "命令菜单",
        "提示：每条命令带中文说明；交互式终端可用上下键/鼠标选择，管道环境显示纯文本菜单。",
        "",
    ]
    if not items:
        lines.append("- 没有匹配命令")
        return "\n".join(lines)
    for spec in items:
        lines.append(f"{spec.display:<34} {spec.description}")
    return "\n".join(lines)
