from __future__ import annotations

import os
import shutil
import unicodedata

from runtime.commands.registry import search_command_specs

LUCODE_BLUE = "\033[94m"
ANSI_RESET = "\033[0m"
PANEL_WIDTH = 96


def render_command_palette(filter_text: str = "", workspace_context=None) -> str:
    items = search_command_specs(filter_text, workspace_context=workspace_context)
    lines = [
        "提示：每条命令带中文说明；交互式终端可用上下键选择，管道环境显示纯文本菜单。",
        "",
    ]
    if not items:
        lines.append("- 没有匹配命令")
        return _render_panel("命令菜单", lines)
    for spec in items:
        lines.append(f"{spec.display:<34} {_compact_description(spec.description)}")
    return _render_panel("命令菜单", lines)


def _compact_description(value: str, *, limit: int = 64) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _render_panel(title: str, lines: list[str]) -> str:
    width = _resolved_panel_width()
    body_width = width - 4
    label = f" {title} "
    output = [_ansi_blue("╭─" + label + "─" * max(0, width - 3 - _display_width(label)) + "╮")]
    for line in lines:
        for wrapped in _wrap_visible(line, body_width):
            padding = " " * max(0, body_width - _display_width(wrapped))
            output.append(f"{_ansi_blue('│')} {wrapped}{padding} {_ansi_blue('│')}")
    output.append(_ansi_blue("╰" + "─" * (width - 2) + "╯"))
    return "\n".join(output)


def _resolved_panel_width() -> int:
    columns = shutil.get_terminal_size((PANEL_WIDTH + 10, 24)).columns
    return max(60, min(PANEL_WIDTH, columns - 10))


def _wrap_visible(text: str, width: int) -> list[str]:
    text = str(text or "")
    if _display_width(text) <= width:
        return [text]
    lines: list[str] = []
    current = ""
    current_width = 0
    for char in text:
        char_width = _display_width(char)
        if current and current_width + char_width > width:
            lines.append(current)
            current = char
            current_width = char_width
            continue
        current += char
        current_width += char_width
    if current:
        lines.append(current)
    return lines or [""]


def _display_width(value: str) -> int:
    width = 0
    for char in str(value or ""):
        if unicodedata.combining(char):
            continue
        width += 2 if unicodedata.east_asian_width(char) in {"F", "W"} else 1
    return width


def _ansi_blue(value: str) -> str:
    if os.environ.get("NO_COLOR"):
        return value
    return f"{LUCODE_BLUE}{value}{ANSI_RESET}"
