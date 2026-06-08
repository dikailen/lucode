from __future__ import annotations

import os
import re
import shutil
import unicodedata
from pathlib import Path

from runtime.history.model import HistoryItem, HistoryPreview


LUCODE_BLUE = "\033[94m"
LUCODE_HIGHLIGHT = "\033[96;1m"
ANSI_RESET = "\033[0m"
PANEL_WIDTH = 96
ANSI_PATTERN = re.compile(r"\x1b\[[0-9;]*m")


def render_history_panel(
    *,
    workspace_root: Path,
    items: list[HistoryItem],
    selected_index: int = 0,
    preview: HistoryPreview | None = None,
    message: str = "",
    footer: str = "↑↓ 选择，Enter 恢复，v 查看预览，q 退出",
    width: int = PANEL_WIDTH,
    highlight_terms: list[str] | tuple[str, ...] | None = None,
) -> str:
    body_width = _resolved_panel_body_width(width)
    selected_index = _clamp_index(selected_index, len(items))
    selected = items[selected_index] if items else None
    preview = preview or (HistoryPreview(selected.history_id, selected.session_id) if selected else None)

    lines: list[str] = []
    lines.append(_panel_top("Lucode History 会话历史", body_width))
    for body_line in _wrap_visible(f"项目  {Path(workspace_root).resolve()}", body_width):
        lines.append(_panel_line(body_line, body_width))
    lines.append(_panel_line("目录  .lucode/history（兼容 .lucode/sessions）", body_width))
    lines.append(_panel_line(f"会话  {len(items)} 个", body_width))
    if message:
        for body_line in _wrap_visible(f"提示  {message}", body_width):
            lines.append(_panel_line(body_line, body_width))

    lines.append(_panel_section("最近会话", body_width))
    if not items:
        lines.append(_panel_line("暂无可恢复会话。完成一次普通对话后，这里会出现 JSONL 历史记录。", body_width))
    else:
        for index, item in enumerate(items[:8]):
            marker = ">" if index == selected_index else " "
            row = f"{marker} {_format_time(item.updated_at)}  {item.title}  {max(1, item.message_count // 2)}轮"
            for body_line in _wrap_visible(row, body_width):
                lines.append(_panel_line(_highlight_terms(body_line, highlight_terms), body_width))

    lines.append(_panel_section("预览", body_width))
    if preview and preview.session_id:
        preview_lines = [
            f"用户：{preview.last_user or preview.first_user or '无'}",
            f"助手：{preview.last_assistant or '无'}",
        ]
        if preview.run_context_summary:
            preview_lines.append(f"Context：{preview.run_context_summary}")
        for line in preview_lines:
            for body_line in _wrap_visible(line, body_width):
                lines.append(_panel_line(_highlight_terms(body_line, highlight_terms), body_width))
    else:
        lines.append(_panel_line("选择一条历史会话后会在这里显示最近问答摘要。", body_width))

    lines.append(_panel_line(footer, body_width))
    lines.append(_panel_bottom(body_width))
    return "\n".join(lines)


def _clamp_index(index: int, length: int) -> int:
    if length <= 0:
        return 0
    return max(0, min(int(index or 0), length - 1))


def _format_time(value: str) -> str:
    text = str(value or "").strip()
    if len(text) >= 16 and "T" in text:
        return text[:16].replace("T", " ")
    return text[:16] or "未知时间"


def _resolved_panel_body_width(width: int) -> int:
    columns = shutil.get_terminal_size((width + 10, 24)).columns
    safe_width = max(60, min(int(width or PANEL_WIDTH), columns - 10))
    return max(48, safe_width - 4)


def _panel_top(title: str, body_width: int) -> str:
    label = f" {title} "
    line = "╭─" + label + "─" * max(0, body_width + 1 - _display_width(label)) + "╮"
    return _ansi_blue(line)


def _panel_bottom(body_width: int) -> str:
    return _ansi_blue("╰" + "─" * (body_width + 2) + "╯")


def _panel_section(title: str, body_width: int) -> str:
    label = f" {title} "
    line = "├─" + label + "─" * max(0, body_width + 1 - _display_width(label)) + "┤"
    return _ansi_blue(line)


def _panel_line(value: str, body_width: int) -> str:
    return f"{_ansi_blue('│')} {value}{' ' * max(0, body_width - _display_width(value))} {_ansi_blue('│')}"


def _wrap_visible(value: str, width: int) -> list[str]:
    text = str(value or "")
    if _display_width(text) <= width:
        return [text]
    indent = len(text) - len(text.lstrip(" "))
    prefix = " " * min(indent, 6)
    lines: list[str] = []
    current = ""
    current_width = 0
    for char in text:
        char_width = _display_width(char)
        if current and current_width + char_width > width:
            lines.append(current.rstrip())
            current = prefix + char.lstrip() if char == " " else prefix + char
            current_width = _display_width(current)
            continue
        current += char
        current_width += char_width
    if current:
        lines.append(current.rstrip())
    return lines or [""]


def _display_width(value: str) -> int:
    width = 0
    for char in ANSI_PATTERN.sub("", str(value or "")):
        if unicodedata.combining(char):
            continue
        width += 2 if unicodedata.east_asian_width(char) in {"F", "W"} else 1
    return width


def _ansi_blue(value: str) -> str:
    if os.environ.get("NO_COLOR"):
        return value
    return f"{LUCODE_BLUE}{value}{ANSI_RESET}"


def _highlight_terms(value: str, terms: list[str] | tuple[str, ...] | None) -> str:
    text = str(value or "")
    normalized_terms = [str(term or "").strip() for term in terms or [] if str(term or "").strip()]
    if not normalized_terms or os.environ.get("NO_COLOR"):
        return text
    for term in sorted(set(normalized_terms), key=len, reverse=True):
        pattern = re.compile(re.escape(term), re.IGNORECASE)
        text = pattern.sub(lambda match: f"{LUCODE_HIGHLIGHT}{match.group(0)}{ANSI_RESET}", text)
    return text
