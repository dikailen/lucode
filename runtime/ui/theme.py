from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class UiTheme:
    """Small UI color token set; future /theme commands can swap this object."""

    name: str = "cyan"
    brand: str = "cyan"
    accent: str = "magenta"
    border: str = "cyan"
    label: str = "dim"
    value: str = "default"
    muted: str = "dim"
    model_label: str = "bright_black"
    success: str = "green"
    warning: str = "yellow"
    danger: str = "red"


DEFAULT_UI_THEME = UiTheme()

THEME_PRESETS: dict[str, UiTheme] = {
    "cyan": DEFAULT_UI_THEME,
    "blue": UiTheme(name="blue", brand="blue", border="blue", label="dim", value="default", muted="dim"),
    "green": UiTheme(name="green", brand="green", border="green", label="dim", value="default", muted="dim"),
    "amber": UiTheme(name="amber", brand="yellow", border="yellow", label="dim", value="default", muted="dim"),
    "pink": UiTheme(name="pink", brand="magenta", border="magenta", label="dim", value="default", muted="dim"),
    "mono": UiTheme(name="mono", brand="white", border="white", label="dim", value="default", muted="dim"),
}


def normalize_theme_name(name: str | None) -> str:
    return str(name or "").strip().lower().replace("_", "-")


def list_theme_presets() -> tuple[str, ...]:
    return tuple(THEME_PRESETS)


def get_theme_preset(name: str | None) -> UiTheme | None:
    return THEME_PRESETS.get(normalize_theme_name(name))


def resolve_ui_theme(*, workspace_root: Path | str | None = None, user_home: Path | str | None = None) -> UiTheme:
    try:
        from runtime.config.theme_config import load_theme_name

        configured = load_theme_name(workspace_root=workspace_root, user_home=user_home)
    except Exception:
        configured = ""
    return get_theme_preset(configured) or DEFAULT_UI_THEME


def render_theme_list(*, current: str | None = None) -> str:
    current_name = normalize_theme_name(current) or DEFAULT_UI_THEME.name
    lines = [f"当前主题：{current_name}", "可用主题："]
    for name in list_theme_presets():
        marker = "*" if name == current_name else "-"
        lines.append(f"{marker} {name}")
    lines.append("用法：/theme preview <name> 或 /theme <name>")
    return "\n".join(lines)


def render_theme_preview(name: str | None, *, workspace_root: Path | str | None = None) -> str:
    theme = get_theme_preset(name)
    if theme is None:
        return f"未知主题：{name or ''}\n可用主题：{', '.join(list_theme_presets())}"
    workspace_label = str(workspace_root or "<workspace>")
    return "\n".join(
        [
            f"主题预览：{theme.name}",
            f"brand={theme.brand} border={theme.border} model_label={theme.model_label}",
            f"项目  {workspace_label}",
            "模式  full 审核并行",
            "主脑  DeepSeek V4 Pro",
            "工具  按需加载 · 审批保护",
        ]
    )


def prompt_toolkit_ansi_color(theme: UiTheme | None) -> str:
    theme = theme or DEFAULT_UI_THEME
    color = str(theme.brand or DEFAULT_UI_THEME.brand).strip().lower()
    return {
        "cyan": "ansicyan",
        "blue": "ansiblue",
        "green": "ansigreen",
        "yellow": "ansiyellow",
        "magenta": "ansimagenta",
        "white": "ansiwhite",
        "red": "ansired",
    }.get(color, "ansicyan")


def prompt_toolkit_prompt_style(theme: UiTheme | None) -> str:
    return f"{prompt_toolkit_ansi_color(theme)} bold"
