from __future__ import annotations

import os
import shutil
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from runtime.config.connect_command import (
    ProviderConnectRequest,
    apply_provider_connect_request,
    provider_requires_api_key,
    render_provider_connect_success,
)
from runtime.config.model_config import (
    auth_path,
    load_auth,
    load_effective_lucode_config,
    load_provider_catalog,
    normalize_provider_id,
    project_config_path,
    provider_has_api_key,
)

LUCODE_BLUE = "\033[94m"
ANSI_RESET = "\033[0m"
PANEL_WIDTH = 96


@dataclass
class ConnectWizardState:
    workspace_root: Path
    user_home: Path
    provider_catalog: dict[str, dict]
    selected_provider: str = ""
    api_key: str = ""
    model: str = ""
    homepage: str = ""
    base_url: str = ""
    display_name: str = ""
    custom: bool = False
    last_message: str = ""


@dataclass(frozen=True)
class ConnectWizardCommandItem:
    command: str
    display: str
    meta: str = ""


@dataclass(frozen=True)
class ConnectWizardApplyResult:
    request: ProviderConnectRequest
    message: str


def build_connect_wizard_state(workspace_context=None, *, selected_provider: str = "") -> ConnectWizardState:
    workspace_root = _workspace_root(workspace_context)
    user_home = _user_home(workspace_context)
    state = ConnectWizardState(
        workspace_root=workspace_root,
        user_home=user_home,
        provider_catalog=load_provider_catalog(),
    )
    if selected_provider:
        state, _ = apply_connect_wizard_input(state, f"provider {selected_provider}")
    return state


def render_connect_wizard_snapshot(state: ConnectWizardState, *, message: str = "") -> str:
    lines = [
        f"项目：{state.workspace_root}",
        f"凭据：{auth_path(state.user_home)}",
        f"配置：{project_config_path(state.workspace_root)}",
        "",
    ]
    if state.selected_provider:
        lines.extend(_render_selected_provider(state))
    else:
        lines.extend(_render_provider_picker(state))
        delete_count = len(connected_provider_delete_items(state))
        if delete_count:
            lines.append(f"已连接模型/Provider：{delete_count} 个；可从删除模型入口清理。")
    lines.append("")
    lines.append("操作：↑↓ 选择，Enter 填写；滚轮可滚动终端历史；保存前不会写入配置；q 退出。")
    status = message or state.last_message
    if status:
        lines.append(f"状态：{_redact_secret(status, state)}")
    return _render_wizard_panel("Lucode Provider 连接", lines)


def connect_wizard_command_items(state: ConnectWizardState, *, max_providers: int = 12) -> list[ConnectWizardCommandItem]:
    items = [
        ConnectWizardCommandItem("q", "退出连接向导", "返回聊天，不改动配置"),
        ConnectWizardCommandItem("refresh", "刷新 Provider 列表", "重新读取内置预设和项目配置"),
    ]
    delete_items = connected_provider_delete_items(state)
    delete_meta = f"{len(delete_items)} 个可删除；二次确认后才会写配置" if delete_items else "当前没有已保存模型，进入后会提示为空"
    items.append(ConnectWizardCommandItem("delete", "删除模型/Provider 配置", delete_meta))
    if state.selected_provider:
        items.extend(_selected_provider_items(state))
    items.append(ConnectWizardCommandItem("custom my_proxy", "自定义中转", "填写自定义 provider、homepage、base_url、key、model"))
    for provider_id, info in sorted(state.provider_catalog.items())[:max_providers]:
        if provider_id == "custom_openai_compatible":
            continue
        display_name = str(info.get("display_name") or provider_id)
        base_url = str(info.get("base_url") or "需自定义")
        items.append(ConnectWizardCommandItem(f"provider {provider_id}", f"选择 Provider：{display_name}", base_url))
    return items


def connected_provider_delete_items(state: ConnectWizardState) -> list[ConnectWizardCommandItem]:
    config = load_effective_lucode_config(workspace_root=state.workspace_root, user_home=state.user_home)
    configured_providers = config.get("provider") if isinstance(config.get("provider"), dict) else {}
    auth_providers = (load_auth(user_home=state.user_home).get("providers") or {})
    provider_ids = sorted(set(configured_providers or {}) | set(auth_providers or {}))
    items: list[ConnectWizardCommandItem] = []
    for provider_id in provider_ids:
        normalized = normalize_provider_id(provider_id)
        provider_config = dict(state.provider_catalog.get(normalized) or {})
        configured = (configured_providers or {}).get(normalized)
        if isinstance(configured, dict):
            provider_config.update(configured)
        display_name = str(provider_config.get("display_name") or normalized)
        models = [str(item).strip() for item in (provider_config.get("models") or []) if str(item).strip()]
        sources = []
        if normalized in (configured_providers or {}):
            sources.append("项目配置")
        if normalized in (auth_providers or {}):
            sources.append("API key")
        source_text = " + ".join(sources) if sources else "已发现"
        model_text = ", ".join(f"{normalized}/{model}" for model in models[:3]) if models else "无模型"
        items.append(
            ConnectWizardCommandItem(
                f"delete {normalized}",
                f"删除模型/Provider：{display_name}（{normalized}）",
                f"{source_text} | {model_text}",
            )
        )
    return items


def apply_connect_wizard_input(state: ConnectWizardState, command: str) -> tuple[ConnectWizardState, str]:
    command = str(command or "").strip()
    lower = command.lower()
    if not command or lower in {"help", "?", "/help"}:
        state.last_message = "先选择 Provider；已选后可填 key/model，再输入 connect。"
        return state, state.last_message
    if lower in {"refresh", "list", "/list", "刷新"}:
        state.provider_catalog = load_provider_catalog()
        state.last_message = "已刷新 Provider 列表。"
        return state, state.last_message
    if lower.startswith("provider "):
        provider_id = command.split(maxsplit=1)[1].strip()
        return _select_provider(state, provider_id, custom=False)
    if lower.startswith("custom"):
        parts = command.split(maxsplit=1)
        provider_id = parts[1].strip() if len(parts) > 1 else "my_proxy"
        return _select_provider(state, provider_id, custom=True)
    if lower.startswith(("key ", "api-key ", "apikey ")):
        value = command.split(maxsplit=1)[1].strip()
        if not value or _is_placeholder(value):
            raise ValueError("请把占位符替换成真实 API key。")
        state.api_key = value
        state.last_message = "API key 已填写，只会保存到用户级 auth.json。"
        return state, state.last_message
    if lower.startswith(("model ", "models ")):
        value = command.split(maxsplit=1)[1].strip()
        state.model = _resolve_model_value(state, value)
        state.last_message = f"已选择模型：{state.model}"
        return state, state.last_message
    if lower.startswith(("homepage ", "home ")):
        value = command.split(maxsplit=1)[1].strip()
        if not value or _is_placeholder(value):
            raise ValueError("请填写真实官网/控制台地址。")
        state.homepage = value
        state.last_message = "已填写官网/控制台地址。"
        return state, state.last_message
    if lower.startswith(("base-url ", "base_url ", "url ")):
        value = command.split(maxsplit=1)[1].strip()
        if not value or _is_placeholder(value):
            raise ValueError("请填写真实模型请求地址。")
        state.base_url = value
        state.last_message = "已填写真实模型请求地址。"
        return state, state.last_message
    if lower.startswith(("display-name ", "name ")):
        value = command.split(maxsplit=1)[1].strip()
        if not value or _is_placeholder(value):
            raise ValueError("请填写真实显示名称，或跳过这个可选项。")
        state.display_name = value
        state.last_message = "已填写显示名称。"
        return state, state.last_message
    raise ValueError("无法识别。可用：provider deepseek、custom my_proxy、key <key>、model <name>、connect、q。")


def build_connect_request_from_state(state: ConnectWizardState) -> ProviderConnectRequest:
    if not state.selected_provider:
        raise ValueError("请先选择 Provider。")
    preset = _selected_preset(state)
    request = ProviderConnectRequest(
        provider=state.selected_provider,
        api_key=state.api_key.strip(),
        homepage=state.homepage.strip() if state.custom else "",
        base_url=state.base_url.strip() if state.custom else "",
        display_name=state.display_name.strip() if state.custom else "",
        models=tuple([state.model.strip()] if state.model.strip() else []),
        custom=state.custom,
    )
    if not request.models and preset.get("models"):
        request = ProviderConnectRequest(
            provider=request.provider,
            api_key=request.api_key,
            homepage=request.homepage,
            base_url=request.base_url,
            display_name=request.display_name,
            models=(str((preset.get("models") or [""])[0]).strip(),),
            custom=request.custom,
        )
    if provider_requires_api_key(request) and not request.api_key:
        raise ValueError("还缺 API key。请用 key <你的 key> 填写。")
    return request


def apply_connect_wizard_connection(state: ConnectWizardState) -> ConnectWizardApplyResult:
    request = build_connect_request_from_state(state)
    result = apply_provider_connect_request(
        request,
        workspace_root=state.workspace_root,
        user_home=state.user_home,
    )
    message = render_provider_connect_success(result, api_key_provided=bool(request.api_key))
    return ConnectWizardApplyResult(request=request, message=message)


def _render_provider_picker(state: ConnectWizardState) -> list[str]:
    lines = [
        "请选择 Provider",
        "  选择厂商后进入完整表单；已有配置可从管理操作删除。",
        "管理操作",
        "  [d] 删除模型/Provider",
        "      二次确认后清理 API key、Provider 配置和失效脑位引用",
        "",
        "内置预设",
    ]
    for index, (provider_id, info) in enumerate(_visible_providers(state), start=1):
        display_name = str(info.get("display_name") or provider_id)
        key_state = _provider_key_state(provider_id, info, state.user_home)
        model_items = [str(item) for item in (info.get("models") or []) if str(item).strip()]
        models = ", ".join(model_items[:2]) if model_items else "需填写模型名"
        if len(model_items) > 2:
            models = f"{models}, 另有 {len(model_items) - 2} 个"
        lines.append(f"  [{index}] {display_name}（{provider_id}）")
        lines.append(f"      状态：{key_state} · 推荐模型：{models}")
    lines.append("  [c] 自定义中转")
    lines.append("      需要 homepage、base_url、API key、模型名")
    return lines


def _render_selected_provider(state: ConnectWizardState) -> list[str]:
    provider_id = state.selected_provider
    preset = _selected_preset(state)
    display_name = state.display_name or preset.get("display_name") or provider_id
    homepage = state.homepage or preset.get("homepage") or "未填写"
    base_url = state.base_url or preset.get("base_url") or "未填写"
    models = [str(item) for item in (preset.get("models") or []) if str(item).strip()]
    key_state = "本地无需 key" if preset.get("local") else (
        "key 已填写" if state.api_key else (
            "已保存 key" if provider_has_api_key(provider_id, user_home=state.user_home) else "还缺 key"
        )
    )
    selected_model = state.model or (models[0] if models else "未选择")
    lines = [
        f"当前 Provider：{display_name}（{provider_id}）",
        f"类型：{'自定义中转' if state.custom else '内置预设'}",
        f"官网：{homepage}",
        f"请求地址：{base_url}",
        f"API key：{key_state}",
        f"模型：{selected_model}",
    ]
    if models:
        lines.append(f"推荐模型：{', '.join(models[:5])}")
    if state.custom:
        missing = []
        if not state.homepage:
            missing.append("homepage")
        if not state.base_url:
            missing.append("base-url")
        if not state.api_key:
            missing.append("key")
        if not state.model:
            missing.append("model")
        lines.append(f"自定义必填：{', '.join(missing) if missing else '已填齐'}")
    return lines


def _selected_provider_items(state: ConnectWizardState) -> list[ConnectWizardCommandItem]:
    items = [
        ConnectWizardCommandItem("connect", "保存当前 Provider", "写入 .lucode/config.toml；key 写入用户级 auth.json"),
        ConnectWizardCommandItem("key <API_KEY>", "填写 API key", "真实输入 key <你的 key>；面板不会回显密钥"),
    ]
    preset = _selected_preset(state)
    models = [str(item) for item in (preset.get("models") or []) if str(item).strip()]
    if models:
        for model in models[:8]:
            items.append(ConnectWizardCommandItem(f"model {model}", f"选择模型：{model}", state.selected_provider))
    else:
        items.append(ConnectWizardCommandItem("model <模型名>", "填写模型名", "例如 qwen-max 或 gpt-5.2"))
    if state.custom:
        items.extend(
            [
                ConnectWizardCommandItem("homepage <官网>", "填写官网/控制台地址", "只用于展示和用户确认"),
                ConnectWizardCommandItem("base-url <请求地址>", "填写真实请求地址", "模型请求会走这个地址"),
                ConnectWizardCommandItem("name <显示名>", "填写显示名称", "可选"),
            ]
        )
    return items


def _select_provider(state: ConnectWizardState, provider_id: str, *, custom: bool) -> tuple[ConnectWizardState, str]:
    provider_id = normalize_provider_id(provider_id)
    if not custom and provider_id not in state.provider_catalog:
        raise ValueError(f"未找到内置 Provider：{provider_id}。自定义中转请用 custom {provider_id}。")
    preset = state.provider_catalog.get(provider_id) or {}
    models = [str(item).strip() for item in (preset.get("models") or []) if str(item).strip()]
    state.selected_provider = provider_id
    state.custom = bool(custom)
    state.api_key = ""
    state.model = models[0] if models and not custom else ""
    state.homepage = "" if custom else str(preset.get("homepage") or "")
    state.base_url = "" if custom else str(preset.get("base_url") or "")
    state.display_name = str(preset.get("display_name") or provider_id)
    state.last_message = f"已选择 Provider：{state.display_name}（{provider_id}）。"
    return state, state.last_message


def _resolve_model_value(state: ConnectWizardState, value: str) -> str:
    value = str(value or "").strip()
    if not value or _is_placeholder(value):
        raise ValueError("请填写真实模型名。")
    models = [str(item).strip() for item in (_selected_preset(state).get("models") or []) if str(item).strip()]
    if value.isdigit():
        index = int(value)
        if 1 <= index <= len(models):
            return models[index - 1]
        raise ValueError(f"没有第 {index} 个推荐模型。")
    return value


def _visible_providers(state: ConnectWizardState) -> list[tuple[str, dict]]:
    return [
        (provider_id, info)
        for provider_id, info in sorted(state.provider_catalog.items())
        if provider_id != "custom_openai_compatible"
    ]


def _selected_preset(state: ConnectWizardState) -> dict:
    if state.custom:
        return {"display_name": state.display_name or state.selected_provider}
    return dict(state.provider_catalog.get(state.selected_provider) or {})


def _provider_key_state(provider_id: str, info: dict, user_home: Path) -> str:
    if info.get("local"):
        return "本地无需 key"
    return "已保存 key" if provider_has_api_key(provider_id, user_home=user_home) else "缺 key"


def _redact_secret(text: str, state: ConnectWizardState) -> str:
    output = str(text or "")
    if state.api_key:
        output = output.replace(state.api_key, "<hidden>")
    return output


def _render_wizard_panel(title: str, lines: list[str], *, width: int = PANEL_WIDTH) -> str:
    body_width = _resolved_panel_body_width(width)
    rendered = [_panel_top(title, body_width)]
    for line in lines:
        if line == "":
            rendered.append(_panel_line("", body_width))
            continue
        if _is_panel_section(line):
            rendered.append(_panel_section(line, body_width))
            continue
        for wrapped in _wrap_visible(line, body_width):
            rendered.append(_panel_line(wrapped, body_width))
    rendered.append(_panel_bottom(body_width))
    return "\n".join(rendered)


def _resolved_panel_body_width(width: int) -> int:
    columns = shutil.get_terminal_size((width + 10, 24)).columns
    safe_width = max(60, min(int(width or PANEL_WIDTH), columns - 10))
    return max(48, safe_width - 4)


def _panel_top(title: str, body_width: int) -> str:
    label = f" {title} "
    return _ansi_blue("╭─" + label + "─" * max(0, body_width + 1 - _display_width(label)) + "╮")


def _panel_bottom(body_width: int) -> str:
    return _ansi_blue("╰" + "─" * (body_width + 2) + "╯")


def _panel_section(title: str, body_width: int) -> str:
    label = f" {title} "
    return _ansi_blue("├─" + label + "─" * max(0, body_width + 1 - _display_width(label)) + "┤")


def _panel_line(value: str, body_width: int) -> str:
    return f"{_ansi_blue('│')} {value}{' ' * max(0, body_width - _display_width(value))} {_ansi_blue('│')}"


def _is_panel_section(value: str) -> bool:
    return str(value or "").strip() in {"请选择 Provider", "管理操作", "内置预设"}


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
    for char in str(value or ""):
        if unicodedata.combining(char):
            continue
        width += 2 if unicodedata.east_asian_width(char) in {"F", "W"} else 1
    return width


def _ansi_blue(value: str) -> str:
    if os.environ.get("NO_COLOR"):
        return value
    return f"{LUCODE_BLUE}{value}{ANSI_RESET}"


def _is_placeholder(value: str) -> bool:
    normalized = str(value or "").strip().lower()
    return normalized.startswith("<") and normalized.endswith(">")


def _workspace_root(workspace_context=None) -> Path:
    value = getattr(workspace_context, "workspace_root", None)
    if value is not None:
        return Path(value).resolve()
    return Path.cwd().resolve()


def _user_home(workspace_context=None) -> Path:
    value = getattr(workspace_context, "user_home", None)
    if value is not None:
        return Path(value).resolve()
    return Path.home() / ".lucode"
