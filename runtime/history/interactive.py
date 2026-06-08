from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from lucode.shell.input_adapter import ConsoleChoice
from runtime.history.render import render_history_panel
from runtime.history.store import HistoryFacade


@dataclass(frozen=True)
class HistoryBrowserSelection:
    action: str
    history_id: str = ""


async def run_history_browser(
    *,
    console,
    facade: HistoryFacade,
    workspace_root: Path,
    limit: int = 20,
    purpose: str = "resume",
) -> HistoryBrowserSelection:
    items = facade.list_items(limit=limit)
    choice_reader = getattr(console, "read_choice_line", None)
    if not items:
        if not callable(choice_reader):
            print(render_history_panel(workspace_root=workspace_root, items=[], selected_index=0))
        return HistoryBrowserSelection("cancel")

    selected_index = 0
    message = ""
    while True:
        selected = items[selected_index]
        preview = facade.preview(selected.history_id)
        if not callable(choice_reader):
            print(
                render_history_panel(
                    workspace_root=workspace_root,
                    items=items,
                    selected_index=selected_index,
                    preview=preview,
                    message=message,
                    footer=_history_footer(purpose),
                )
            )
        try:
            command = await _read_history_choice(console, items, selected_index, purpose=purpose)
        except EOFError:
            return HistoryBrowserSelection("cancel")
        command = str(command or "").strip()
        lower = command.lower()
        if lower in {"q", "quit", "exit", "/exit", "back", "/back"}:
            return HistoryBrowserSelection("cancel")
        if lower in {"v", "view", "preview"}:
            message = "当前预览已展开在面板下方。"
            continue
        if lower in {"", "enter", "resume"}:
            return HistoryBrowserSelection("resume", selected.history_id)
        if lower in {"up", "k"}:
            selected_index = (selected_index - 1) % len(items)
            continue
        if lower in {"down", "j"}:
            selected_index = (selected_index + 1) % len(items)
            continue
        if lower.isdigit() and 1 <= int(lower) <= len(items):
            selected_index = int(lower) - 1
            if str(purpose or "").strip().lower() in {"export", "remove", "delete"}:
                return HistoryBrowserSelection("resume", items[selected_index].history_id)
            continue
        resolved = _resolve_choice_command(command, items)
        if resolved:
            return HistoryBrowserSelection("resume", resolved)
        message = f"未识别选择：{command}。可输入序号、q 退出，或直接选择候选项。"


async def _read_history_choice(console, items, selected_index: int, *, purpose: str = "resume") -> str:
    choices = [
        ConsoleChoice(item.history_id, f"{index + 1}. {item.title}", f"{_short_time(item.updated_at)} | {item.message_count} 条消息")
        for index, item in enumerate(items[:20])
    ]
    choices.append(ConsoleChoice("q", "退出历史面板", "返回主聊天"))
    choice_reader = getattr(console, "read_choice_line", None)
    if callable(choice_reader):
        return await choice_reader(
            "\n历史会话> ",
            choices,
            bottom_toolbar=_history_footer(purpose),
            reserve_space_for_menu=min(12, max(6, len(choices) + 2)),
        )
    prompt_reader = getattr(console, "read_line", None)
    if callable(prompt_reader):
        return await prompt_reader("\n历史会话> ")
    return ""


def _resolve_choice_command(command: str, items) -> str:
    for item in items:
        if command == item.history_id or item.history_id.startswith(command):
            return item.history_id
    return ""


def _short_time(value: str) -> str:
    text = str(value or "").strip()
    return text[:16].replace("T", " ") if text else "未知时间"


def _history_footer(purpose: str) -> str:
    normalized = str(purpose or "").strip().lower()
    if normalized in {"remove", "delete"}:
        return "↑↓ 选择，Enter 选择删除；随后会二次确认；输入 q 返回"
    if normalized in {"export"}:
        return "↑↓ 选择，Enter 导出当前会话；输入 q 返回"
    return "↑↓ 选择，Enter 恢复；输入 q 退出历史面板"
