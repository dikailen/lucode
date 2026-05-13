from __future__ import annotations

from dataclasses import dataclass

from runtime.commands.registry import search_command_specs


@dataclass(frozen=True)
class CommandCompletionItem:
    text: str
    display: str
    meta: str
    start_position: int


ARGUMENT_COMPLETIONS = {
    "/mode": (
        ("solo", "单代理：默认工具 Agent，适合日常读写、命令和测试"),
        ("serial", "串行多代理：主脑规划，多专家按顺序执行"),
        ("full", "审核并行：通过安全门后并行执行无冲突任务"),
    ),
    "/refiner": (
        ("on", "开启前置优化副脑"),
        ("off", "关闭前置优化副脑"),
    ),
}


def command_completion_items(text_before_cursor: str, workspace_context=None) -> list[CommandCompletionItem]:
    raw_text = str(text_before_cursor or "")
    query = raw_text.lstrip()
    if not query.startswith("/"):
        return []

    start_position = -len(query)
    items: list[CommandCompletionItem] = []
    seen: set[str] = set()
    for item in _argument_completion_items(query, start_position):
        items.append(item)
        seen.add(item.text)
    if items and _is_argument_completion_context(query):
        return items
    for spec in search_command_specs(query, workspace_context=workspace_context):
        if spec.command in seen:
            continue
        seen.add(spec.command)
        meta = spec.description
        if spec.argument_hint:
            meta = f"{spec.argument_hint}  {meta}"
        items.append(
            CommandCompletionItem(
                text=spec.command,
                display=spec.command,
                meta=meta,
                start_position=start_position,
            )
        )
    return items


def _argument_completion_items(query: str, start_position: int) -> list[CommandCompletionItem]:
    command, partial = _split_argument_query(query)
    if command not in ARGUMENT_COMPLETIONS:
        return []

    items: list[CommandCompletionItem] = []
    for value, description in ARGUMENT_COMPLETIONS[command]:
        if partial and not value.startswith(partial):
            continue
        text = f"{command} {value}"
        items.append(
            CommandCompletionItem(
                text=text,
                display=text,
                meta=description,
                start_position=start_position,
            )
        )
    return items


def _split_argument_query(query: str) -> tuple[str, str]:
    normalized = str(query or "").strip().lower()
    if not normalized:
        return "", ""
    if " " not in normalized:
        return (normalized, "") if normalized in ARGUMENT_COMPLETIONS else ("", "")
    command, partial = normalized.split(maxsplit=1)
    return command, partial.strip()


def _is_argument_completion_context(query: str) -> bool:
    normalized = str(query or "").strip().lower()
    if not normalized:
        return False
    command = normalized.split(maxsplit=1)[0]
    return command in ARGUMENT_COMPLETIONS and (normalized == command or normalized.startswith(f"{command} "))


def should_refresh_slash_completion(text_before_cursor: str) -> bool:
    return str(text_before_cursor or "").lstrip().startswith("/")


def slash_prompt_message(prompt: str):
    del prompt
    return [("class:prompt", "\n> ")]


def slash_prompt_session_kwargs() -> dict:
    try:
        from prompt_toolkit.shortcuts.prompt import CompleteStyle
        from prompt_toolkit.styles import Style
    except Exception:
        return {}

    return {
        "complete_style": CompleteStyle.COLUMN,
        "reserve_space_for_menu": 10,
        "style": Style.from_dict(
            {
                "prompt": "bold #d4d4d4",
                "completion-menu.completion": "#a3a3a3 bg:#080808",
                "completion-menu.completion.current": "bold #8ab4ff bg:#1f2937",
                "completion-menu.meta.completion": "#a3a3a3 bg:#080808",
                "completion-menu.meta.completion.current": "#e5e7eb bg:#1f2937",
            }
        ),
        "key_bindings": create_slash_command_key_bindings(),
    }


def create_slash_command_key_bindings():
    try:
        from prompt_toolkit.filters import has_completions
        from prompt_toolkit.key_binding import KeyBindings
    except Exception:
        return None

    key_bindings = KeyBindings()

    @key_bindings.add("enter", filter=has_completions)
    def _accept_current_completion(event):
        buffer = event.current_buffer
        completion = getattr(getattr(buffer, "complete_state", None), "current_completion", None)
        if completion is not None:
            buffer.apply_completion(completion)
        buffer.validate_and_handle()

    @key_bindings.add("backspace")
    def _delete_before_cursor_and_refresh(event):
        buffer = event.current_buffer
        if getattr(buffer, "selection_state", None):
            buffer.cut_selection()
        else:
            buffer.delete_before_cursor(count=1)
        _refresh_slash_completion(buffer)

    @key_bindings.add("delete")
    def _delete_at_cursor_and_refresh(event):
        buffer = event.current_buffer
        if getattr(buffer, "selection_state", None):
            buffer.cut_selection()
        else:
            buffer.delete(count=1)
        _refresh_slash_completion(buffer)

    return key_bindings


def _refresh_slash_completion(buffer) -> None:
    if should_refresh_slash_completion(getattr(buffer.document, "text_before_cursor", "")):
        try:
            buffer.start_completion(select_first=False)
        except Exception:
            pass
        return
    cancel = getattr(buffer, "cancel_completion", None)
    if callable(cancel):
        cancel()


def create_slash_command_completer(workspace_context=None):
    try:
        from prompt_toolkit.completion import Completer, Completion
    except Exception:
        return None

    class SlashCommandCompleter(Completer):
        def get_completions(self, document, complete_event):
            del complete_event
            for item in command_completion_items(document.text_before_cursor, workspace_context=workspace_context):
                yield Completion(
                    item.text,
                    start_position=item.start_position,
                    display=item.display,
                    display_meta=item.meta,
                    style="class:completion-menu.completion",
                    selected_style="class:completion-menu.completion.current",
                )

    return SlashCommandCompleter()
