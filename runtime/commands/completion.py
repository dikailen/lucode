from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import time

from catalog_system.refresher import build_skill_catalog
from catalog_system.model_catalog import load_model_catalog
from runtime.commands.registry import search_command_specs
from runtime.config.model_config import load_auth, load_effective_lucode_config, load_provider_catalog
from runtime.ui.theme import list_theme_presets


@dataclass(frozen=True)
class CommandCompletionItem:
    text: str
    display: str
    meta: str
    start_position: int


@dataclass(frozen=True)
class ReferenceToken:
    kind: str
    token: str
    query: str
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

MODEL_BRAIN_COMPLETION_ROLES = (
    ("前置优化", "前置优化脑"),
    ("主脑", "主脑规划脑"),
    ("执行", "执行专家脑"),
    ("汇总", "汇总脑"),
)

_COMMAND_COMPLETION_CACHE_SECONDS = 1.5
_MODEL_COMPLETION_CACHE_SECONDS = 5.0
_MAX_COMPLETION_CACHE_ITEMS = 128
_REFERENCE_COMPLETION_LIMIT = 40
_REFERENCE_FILE_SUFFIXES = {
    ".bat",
    ".cfg",
    ".css",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".ps1",
    ".py",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}
_REFERENCE_EXCLUDED_DIRS = {
    ".agent_cache",
    ".agent_quarantine",
    ".agent_runs",
    ".agent_test_tmp",
    ".git",
    ".idea",
    ".lucode/history",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "node_modules",
}
_COMMAND_SEARCH_CACHE: dict[tuple[object, ...], tuple[float, tuple[object, ...]]] = {}
_MODEL_CHOICE_CACHE: dict[tuple[int, int], tuple[float, tuple[tuple[str, str, str], ...]]] = {}


def command_completion_items(text_before_cursor: str, workspace_context=None) -> list[CommandCompletionItem]:
    raw_text = str(text_before_cursor or "")
    query = raw_text.lstrip()
    if not query.startswith("/"):
        return []

    start_position = -len(query)
    items: list[CommandCompletionItem] = []
    seen: set[str] = set()
    for item in _argument_completion_items(query, start_position, workspace_context=workspace_context):
        items.append(item)
        seen.add(item.text)
    if items and _is_argument_completion_context(query):
        return items
    for spec in _cached_search_command_specs(query, workspace_context=workspace_context):
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


def active_reference_token(text_before_cursor: str) -> ReferenceToken | None:
    text = str(text_before_cursor or "")
    match = re.search(r"(^|\s)(@[^@\s#]*|#[^@\s#]*|~/[^\s]*)$", text)
    if not match:
        return None
    token = match.group(2)
    if token.startswith("@"):
        kind = "file"
        query = token[1:]
    elif token.startswith("#"):
        kind = "skill"
        query = token[1:]
    else:
        kind = "home"
        query = token[2:]
    return ReferenceToken(kind=kind, token=token, query=query, start_position=-len(token))


def reference_completion_items(text_before_cursor: str, workspace_context=None) -> list[CommandCompletionItem]:
    token = active_reference_token(text_before_cursor)
    if token is None:
        return []
    if token.kind == "file":
        return _file_reference_items(token, workspace_context=workspace_context)
    if token.kind == "skill":
        return _skill_reference_items(token, workspace_context=workspace_context)
    if token.kind == "home":
        return _home_reference_items(token, workspace_context=workspace_context)
    return []


def _argument_completion_items(
    query: str,
    start_position: int,
    workspace_context=None,
) -> list[CommandCompletionItem]:
    command, partial = _split_argument_query(query)
    items: list[CommandCompletionItem] = []
    if command in ARGUMENT_COMPLETIONS:
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
    if command == "/theme":
        return _theme_completion_items(partial, start_position)
    if command == "/connect":
        return _connect_completion_items(partial, start_position, workspace_context=workspace_context)
    return _model_tuner_completion_items(query, start_position, workspace_context=workspace_context)


def _split_argument_query(query: str) -> tuple[str, str]:
    normalized = str(query or "").strip().lower()
    if not normalized:
        return "", ""
    argument_commands = set(ARGUMENT_COMPLETIONS) | {"/connect", "/theme"}
    if " " not in normalized:
        return (normalized, "") if normalized in argument_commands else ("", "")
    command, partial = normalized.split(maxsplit=1)
    return (command, partial.strip()) if command in argument_commands else ("", "")


def _is_argument_completion_context(query: str) -> bool:
    normalized = str(query or "").strip().lower()
    if not normalized:
        return False
    command = normalized.split(maxsplit=1)[0]
    if command in ARGUMENT_COMPLETIONS and (normalized == command or normalized.startswith(f"{command} ")):
        return True
    if command == "/connect" and (normalized == command or normalized.startswith("/connect ")):
        return True
    if command == "/theme" and (normalized == command or normalized.startswith("/theme ")):
        return True
    return normalized in {"/models", "/model"} or normalized.startswith(("/models ", "/model "))


def _theme_completion_items(partial: str, start_position: int) -> list[CommandCompletionItem]:
    partial = str(partial or "").strip().lower()
    items: list[CommandCompletionItem] = []
    if partial.startswith("preview"):
        tokens = partial.split(maxsplit=1)
        theme_partial = tokens[1] if len(tokens) > 1 else ""
        for name in list_theme_presets():
            if theme_partial and not name.startswith(theme_partial):
                continue
            text = f"/theme preview {name}"
            items.append(
                CommandCompletionItem(
                    text=text,
                    display=text,
                    meta="预览主题，不写入配置",
                    start_position=start_position,
                )
            )
        return items
    for action, meta in (
        ("list", "列出可用 UI 主题"),
        ("preview", "预览主题，不写入配置"),
    ):
        text = f"/theme {action}"
        if not partial or text.removeprefix("/theme ").startswith(partial):
            items.append(CommandCompletionItem(text=text, display=text, meta=meta, start_position=start_position))
    for name in list_theme_presets():
        if partial and not name.startswith(partial):
            continue
        text = f"/theme {name}"
        items.append(CommandCompletionItem(text=text, display=text, meta=f"切换到 {name} 主题", start_position=start_position))
    return items


def _connect_completion_items(partial: str, start_position: int, workspace_context=None) -> list[CommandCompletionItem]:
    try:
        catalog = load_provider_catalog()
    except Exception:
        return []
    partial = str(partial or "").strip().lower()
    items: list[CommandCompletionItem] = []
    if partial.startswith(("remove", "delete", "rm", "logout")):
        tokens = partial.split()
        provider_partial = tokens[1] if len(tokens) > 1 else ""
        for provider_id in _connected_provider_ids(workspace_context):
            if provider_partial and not provider_id.startswith(provider_partial):
                continue
            items.append(
                CommandCompletionItem(
                    text=f"/connect remove {provider_id}",
                    display=f"/connect remove {provider_id}",
                    meta="删除 Provider 配置、API key，并清理失效模型引用",
                    start_position=start_position,
                )
            )
        return items
    for action, meta in (
        ("remove", "删除已连接 Provider"),
        ("delete", "remove 的别名"),
    ):
        text = f"/connect {action}"
        if not partial or text.removeprefix("/connect ").startswith(partial):
            items.append(CommandCompletionItem(text=text, display=text, meta=meta, start_position=start_position))
    for provider_id, info in sorted(catalog.items()):
        if partial and not provider_id.startswith(partial):
            continue
        display_name = str(info.get("display_name") or provider_id)
        base_url = str(info.get("base_url") or "需自定义")
        models = ", ".join(str(item) for item in (info.get("models") or [])[:3]) or "需手动填写模型名"
        items.append(
            CommandCompletionItem(
                text=f"/connect {provider_id}",
                display=f"/connect {provider_id}",
                meta=f"{display_name} | {base_url} | {models}",
                start_position=start_position,
            )
        )
    return items


def _connected_provider_ids(workspace_context=None) -> list[str]:
    try:
        workspace_root = getattr(workspace_context, "workspace_root", None)
        user_home = getattr(workspace_context, "user_home", None)
        config = load_effective_lucode_config(workspace_root=workspace_root, user_home=user_home)
        auth = load_auth(user_home=user_home)
    except Exception:
        return []
    providers = set((config.get("provider") or {}).keys()) | set((auth.get("providers") or {}).keys())
    return sorted(str(item).strip().lower() for item in providers if str(item).strip())


def _file_reference_items(token: ReferenceToken, workspace_context=None) -> list[CommandCompletionItem]:
    root = _workspace_root(workspace_context)
    query = token.query.replace("\\", "/").lower()
    items: list[CommandCompletionItem] = []
    for path in _iter_reference_files(root):
        if len(items) >= _REFERENCE_COMPLETION_LIMIT:
            break
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            continue
        if _is_excluded_reference_path(rel):
            continue
        if not _is_reference_file_candidate(path):
            continue
        if query and query not in rel.lower():
            continue
        items.append(
            CommandCompletionItem(
                text=f"@{rel}",
                display=f"@{rel}",
                meta="项目文件引用（不读取内容）",
                start_position=token.start_position,
            )
        )
    return items


def _iter_reference_files(root: Path):
    try:
        for current, dirnames, filenames in os.walk(root):
            current_path = Path(current)
            try:
                rel_dir = current_path.relative_to(root).as_posix()
            except ValueError:
                rel_dir = ""
            dirnames[:] = [
                dirname
                for dirname in sorted(dirnames, key=str.lower)
                if not _is_excluded_reference_path(f"{rel_dir}/{dirname}".strip("/"))
            ]
            for filename in sorted(filenames, key=str.lower):
                yield current_path / filename
    except OSError:
        return


def _skill_reference_items(token: ReferenceToken, workspace_context=None) -> list[CommandCompletionItem]:
    root = _workspace_root(workspace_context)
    query = token.query.lower().replace("-", "_")
    try:
        skills = build_skill_catalog(root, include_dynamic=True).get("skills", [])
    except Exception:
        return []
    items: list[CommandCompletionItem] = []
    for skill in sorted(skills, key=lambda item: str(item.get("id") or "")):
        if len(items) >= _REFERENCE_COMPLETION_LIMIT:
            break
        if not skill.get("borrowable"):
            continue
        skill_id = str(skill.get("id") or "").strip()
        if not skill_id:
            continue
        display_name = str(skill.get("display_name_zh") or skill_id)
        summary = str(skill.get("summary_zh") or "Skill 引用")
        haystack = f"{skill_id} {display_name} {summary}".lower().replace("-", "_")
        if query and query not in haystack:
            continue
        items.append(
            CommandCompletionItem(
                text=f"#{skill_id}",
                display=f"#{skill_id}",
                meta=f"{display_name} | {summary[:80]}",
                start_position=token.start_position,
            )
        )
    return items


def _home_reference_items(token: ReferenceToken, workspace_context=None) -> list[CommandCompletionItem]:
    home = Path(getattr(workspace_context, "user_home", None) or Path.home()).expanduser()
    query = token.query.replace("\\", "/")
    base = home
    prefix = ""
    if "/" in query:
        parent, partial = query.rsplit("/", 1)
        prefix = f"{parent}/"
        base = home / parent
        query = partial
    items: list[CommandCompletionItem] = []
    try:
        children = sorted(base.iterdir(), key=lambda path: path.name.lower())
    except OSError:
        return []
    for child in children:
        if len(items) >= _REFERENCE_COMPLETION_LIMIT:
            break
        if query and not child.name.lower().startswith(query.lower()):
            continue
        suffix = "/" if child.is_dir() else ""
        ref = f"~/{prefix}{child.name}{suffix}".replace("\\", "/")
        items.append(
            CommandCompletionItem(
                text=ref,
                display=ref,
                meta="用户目录路径引用（不读取内容）",
                start_position=token.start_position,
            )
        )
    return items


def _model_tuner_completion_items(
    query: str,
    start_position: int,
    workspace_context=None,
) -> list[CommandCompletionItem]:
    del workspace_context
    normalized = str(query or "").strip()
    lower = normalized.lower()
    if not (lower in {"/models", "/model"} or lower.startswith(("/models ", "/model "))):
        return []

    items: list[CommandCompletionItem] = []
    fixed = (
        ("/models", "进入独立模型调音台"),
        ("/models available", "查看当前可运行模型"),
        ("/models list", "查看 Provider 模型列表"),
        ("/models probe", "探测 key、base_url、模型名、chat、JSON、tools、stream"),
        ("/models probe force", "忽略缓存重新探测所有已配置模型"),
        ("/models roles", "查看四脑原始角色配置"),
        ("/models brain", "高级命令：切换指定脑位模型"),
        ("/models brain reset", "重置项目多脑模型覆盖配置"),
        ("/models select", "高级命令：统一默认模型"),
    )
    for text, meta in fixed:
        if _completion_matches(text, normalized):
            items.append(CommandCompletionItem(text=text, display=text, meta=meta, start_position=start_position))

    if not _should_expand_model_command_candidates(lower):
        return items

    model_choices = _configured_model_completion_choices()
    for ref, label, capability in model_choices:
        select_text = f"/models select {ref}"
        if _completion_matches(select_text, normalized):
            items.append(
                CommandCompletionItem(
                    text=select_text,
                    display=select_text,
                    meta=f"统一默认模型：{label}",
                    start_position=start_position,
                )
            )
        for role, role_label in MODEL_BRAIN_COMPLETION_ROLES:
            text = f"/models brain {role} {ref}"
            if not _completion_matches(text, normalized):
                continue
            items.append(
                CommandCompletionItem(
                    text=text,
                    display=text,
                    meta=f"{role_label}切到 {label}{capability}",
                    start_position=start_position,
                )
            )
    return items


def _should_expand_model_command_candidates(lower_query: str) -> bool:
    advanced_prefixes = (
        "/models brain ",
        "/model brain ",
        "/models select ",
        "/model select ",
    )
    return any(lower_query.startswith(prefix) for prefix in advanced_prefixes)


def clear_completion_caches() -> None:
    _COMMAND_SEARCH_CACHE.clear()
    _MODEL_CHOICE_CACHE.clear()


def _completion_matches(candidate: str, query: str) -> bool:
    normalized_candidate = str(candidate or "").lower()
    normalized_query = str(query or "").strip().lower()
    if not normalized_query:
        return True
    if normalized_candidate.startswith(normalized_query):
        return True
    tokens = normalized_query.split()
    if len(tokens) <= 1:
        return False
    return all(token in normalized_candidate for token in tokens[1:])


def _configured_model_completion_choices(limit: int = 8) -> list[tuple[str, str, str]]:
    cache_key = (int(limit), id(load_model_catalog))
    now = time.monotonic()
    cached = _MODEL_CHOICE_CACHE.get(cache_key)
    if cached and now - cached[0] <= _MODEL_COMPLETION_CACHE_SECONDS:
        return list(cached[1])

    try:
        models = load_model_catalog().get("models", [])
    except Exception:
        return []
    choices: list[tuple[str, str, str]] = []
    for item in models:
        if not item.get("configured"):
            continue
        ref = _model_ref_for_completion(item)
        if not ref:
            continue
        label = str(item.get("display_name_zh") or item.get("id") or ref)
        flags = []
        if item.get("supports_tools") is True:
            flags.append("工具")
        if item.get("planner_suitable") is True:
            flags.append("规划")
        if item.get("execution_suitable") is True:
            flags.append("执行")
        capability = f"（{','.join(flags)}）" if flags else ""
        choices.append((ref, label, capability))
        if len(choices) >= limit:
            break
    _MODEL_CHOICE_CACHE[cache_key] = (now, tuple(choices))
    _trim_cache(_MODEL_CHOICE_CACHE, _MAX_COMPLETION_CACHE_ITEMS)
    return choices


def _cached_search_command_specs(query: str, workspace_context=None) -> list[object]:
    cache_key = (str(query or ""), id(search_command_specs), *_workspace_cache_key(workspace_context))
    now = time.monotonic()
    cached = _COMMAND_SEARCH_CACHE.get(cache_key)
    if cached and now - cached[0] <= _COMMAND_COMPLETION_CACHE_SECONDS:
        return list(cached[1])

    specs = tuple(search_command_specs(query, workspace_context=workspace_context))
    _COMMAND_SEARCH_CACHE[cache_key] = (now, specs)
    _trim_cache(_COMMAND_SEARCH_CACHE, _MAX_COMPLETION_CACHE_ITEMS)
    return list(specs)


def _workspace_cache_key(workspace_context=None) -> tuple[str, str, str, str]:
    if workspace_context is None:
        return ("", "", "", "")
    return (
        str(getattr(workspace_context, "workspace_root", "") or ""),
        str(getattr(workspace_context, "project_config_dir", "") or ""),
        str(getattr(workspace_context, "user_home", "") or ""),
        str(getattr(workspace_context, "app_home", "") or ""),
    )


def _trim_cache(cache: dict, limit: int) -> None:
    while len(cache) > limit:
        cache.pop(next(iter(cache)))


def _workspace_root(workspace_context=None) -> Path:
    raw_root = getattr(workspace_context, "workspace_root", None)
    if raw_root:
        return Path(raw_root).resolve()
    env_root = os.environ.get("LUCODE_WORKSPACE_ROOT")
    return Path(env_root or Path.cwd()).resolve()


def _is_excluded_reference_path(relative_path: str) -> bool:
    normalized = relative_path.replace("\\", "/")
    parts = normalized.split("/")
    for excluded in _REFERENCE_EXCLUDED_DIRS:
        excluded_parts = excluded.split("/")
        if parts[: len(excluded_parts)] == excluded_parts:
            return True
    return False


def _is_reference_file_candidate(path: Path) -> bool:
    if path.name in {"README", "Makefile", "Dockerfile"}:
        return True
    return path.suffix.lower() in _REFERENCE_FILE_SUFFIXES


def _model_ref_for_completion(model_info: dict) -> str:
    provider_ref = str(model_info.get("provider_ref") or "").strip()
    if provider_ref:
        return provider_ref
    provider = str(model_info.get("provider") or "").strip()
    model_name = str(model_info.get("model_name") or "").strip()
    if provider and model_name:
        return f"{provider}/{model_name}"
    return str(model_info.get("id") or "").strip()


def should_refresh_slash_completion(text_before_cursor: str) -> bool:
    text = str(text_before_cursor or "")
    return text.lstrip().startswith("/") or active_reference_token(text) is not None


def should_complete_main_input_while_typing(text_before_cursor: str) -> bool:
    """Only run live completion for command/reference tokens, not normal prose."""

    return should_refresh_slash_completion(text_before_cursor)


def slash_auto_suggestion(text_before_cursor: str, workspace_context=None) -> str | None:
    raw_text = str(text_before_cursor or "")
    query = raw_text.lstrip()
    if not query.startswith("/"):
        return None
    if not query.strip() or raw_text.endswith((" ", "\t")):
        return None

    for item in command_completion_items(query, workspace_context=workspace_context):
        candidate = str(item.text or "")
        if candidate == query:
            return None
        if candidate.startswith(query):
            return candidate[len(query) :]
    return None


def slash_prompt_message(prompt: str):
    del prompt
    return [("class:prompt", "\nlucode> ")]


def slash_prompt_bottom_toolbar():
    return [("class:toolbar", "输入 / 打开命令菜单 | ↑ 历史 | Ctrl+R 搜索 | Ctrl+C 中断")]


def slash_prompt_session_kwargs(prompt_style: str = "ansiblue bold") -> dict:
    try:
        from prompt_toolkit.application import get_app
        from prompt_toolkit.filters import Condition
        from prompt_toolkit.shortcuts.prompt import CompleteStyle
        from prompt_toolkit.styles import Style
    except Exception:
        return {}

    @Condition
    def complete_command_or_reference() -> bool:
        try:
            document = get_app().current_buffer.document
        except Exception:
            return False
        return should_complete_main_input_while_typing(document.text_before_cursor)

    return {
        "complete_style": CompleteStyle.COLUMN,
        "reserve_space_for_menu": 12,
        "complete_while_typing": complete_command_or_reference,
        "bottom_toolbar": slash_prompt_bottom_toolbar,
        "style": Style.from_dict(
            {
                "prompt": prompt_style,
                "toolbar": "bg:#080808 #a3a3a3",
                "completion-menu.completion": "bg:#202020 #d0d0d0",
                "completion-menu.completion.current": "bg:#f2f2f2 #005fff bold",
                "completion-menu.meta.completion": "bg:#a8a8a8 #202020",
                "completion-menu.meta.completion.current": "bg:#a8a8a8 #202020",
                "scrollbar.background": "bg:#303030",
                "scrollbar.button": "bg:#707070",
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

    @key_bindings.add("c-r", eager=True)
    def _open_history_completion(event):
        buffer = event.current_buffer
        opener = getattr(buffer, "start_history_lines_completion", None)
        if callable(opener):
            opener()

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


def create_main_input_completer(workspace_context=None):
    try:
        from prompt_toolkit.completion import Completer, Completion
    except Exception:
        return None

    class MainInputCompleter(Completer):
        def get_completions(self, document, complete_event):
            del complete_event
            if not should_complete_main_input_while_typing(document.text_before_cursor):
                return
            reference_items = reference_completion_items(document.text_before_cursor, workspace_context=workspace_context)
            if reference_items:
                for item in reference_items:
                    yield _prompt_toolkit_completion(Completion, item)
                return
            for item in command_completion_items(document.text_before_cursor, workspace_context=workspace_context):
                yield _prompt_toolkit_completion(Completion, item)

    return MainInputCompleter()


def create_slash_command_completer(workspace_context=None):
    return create_main_input_completer(workspace_context=workspace_context)


def _prompt_toolkit_completion(completion_cls, item: CommandCompletionItem):
    return completion_cls(
        item.text,
        start_position=item.start_position,
        display=item.display,
        display_meta=item.meta,
        style="class:completion-menu.completion",
        selected_style="class:completion-menu.completion.current",
    )
