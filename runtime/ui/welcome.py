from __future__ import annotations

import os
import unicodedata
from io import StringIO

from catalog_system.model_catalog import load_model_catalog
from runtime.config.settings import RuntimeSettings
from runtime.config.workspace import WorkspaceContext
from runtime.ui.capabilities import normalize_dynamic_ui_mode
from runtime.ui.theme import DEFAULT_UI_THEME, resolve_ui_theme


BLUE = "\033[94m"
RESET = "\033[0m"
LOGO_STATUS_GAP = 8
BOX_TOP_LEFT = "\u256d"
BOX_TOP_RIGHT = "\u256e"
BOX_BOTTOM_LEFT = "\u2570"
BOX_BOTTOM_RIGHT = "\u256f"
BOX_HORIZONTAL = "\u2500"
BOX_VERTICAL = "\u2502"

MASCOT_LOGO = [
    "      /\\_/\\",
    "     ( o.o )",
    "      > ^ <",
    "     /     \\",
    "    (_|   |_)",
]
COMPACT_BRAND = "lucode"


def render_welcome_dashboard(
    workspace: WorkspaceContext,
    settings: RuntimeSettings,
    model_catalog: dict | None = None,
    use_color: bool | None = None,
    show_logo: bool = True,
) -> str:
    """Render the startup dashboard."""

    catalog = model_catalog if model_catalog is not None else load_model_catalog()
    if _should_use_rich_welcome():
        try:
            return _render_rich_welcome_dashboard(workspace, settings, catalog, show_logo=show_logo)
        except Exception:
            pass
    return _render_plain_welcome_dashboard(workspace, settings, catalog, use_color=use_color, show_logo=show_logo)


def _render_plain_welcome_dashboard(
    workspace: WorkspaceContext,
    settings: RuntimeSettings,
    catalog: dict,
    *,
    use_color: bool | None = None,
    show_logo: bool = True,
) -> str:
    color_enabled = _color_enabled(use_color)
    rows = _welcome_rows(workspace, settings, catalog, color_enabled=color_enabled, show_logo=show_logo)
    return _render_box(rows, color_enabled)


def _render_rich_welcome_dashboard(
    workspace: WorkspaceContext,
    settings: RuntimeSettings,
    catalog: dict,
    *,
    show_logo: bool = True,
) -> str:
    from rich.console import Console
    from rich.panel import Panel
    from rich.text import Text

    theme = resolve_ui_theme(workspace_root=workspace.workspace_root, user_home=workspace.user_home)
    console_file = StringIO()
    rows = _welcome_rows(workspace, settings, catalog, color_enabled=False, show_logo=show_logo)
    content_width = max((_display_width(line) for line in rows), default=0)
    panel_width = max(content_width + 8, 72)
    console = Console(
        file=console_file,
        force_terminal=True,
        color_system="truecolor",
        width=panel_width,
        height=max(len(rows) + 4, 10),
        legacy_windows=False,
        record=False,
    )
    title = Text(" lucode ", style=f"bold {theme.brand or DEFAULT_UI_THEME.brand}")
    console.print(
        Panel(
            "\n".join(rows),
            title=title,
            border_style=theme.border or DEFAULT_UI_THEME.border,
            width=panel_width,
            expand=False,
        )
    )
    return console_file.getvalue().rstrip()


def _should_use_rich_welcome() -> bool:
    return normalize_dynamic_ui_mode() == "on"


def _welcome_rows(
    workspace: WorkspaceContext,
    settings: RuntimeSettings,
    catalog: dict,
    *,
    color_enabled: bool,
    show_logo: bool,
) -> list[str]:
    logo_lines = MASCOT_LOGO if show_logo else [COMPACT_BRAND]
    logo = [_blue(line, color_enabled) for line in logo_lines]
    status = _status_lines(workspace, settings, catalog)
    width = max((_display_width(line) for line in logo), default=0) + (LOGO_STATUS_GAP if logo else 0)
    row_count = max(len(logo), len(status))
    logo_top_padding = _center_offset(row_count, len(logo))
    status_top_padding = _center_offset(row_count, len(status))

    rows = []
    for index in range(row_count):
        logo_index = index - logo_top_padding
        status_index = index - status_top_padding
        left = logo[logo_index] if 0 <= logo_index < len(logo) else ""
        right = status[status_index] if 0 <= status_index < len(status) else ""
        rows.append(f"{left}{_visible_padding(left, width)}{right}".rstrip())
    return rows


def _status_lines(workspace: WorkspaceContext, settings: RuntimeSettings, catalog: dict) -> list[str]:
    model_text = _model_summary(settings, catalog)
    mode = str(settings.execution_mode or "solo").strip().lower()
    model_label = "主脑" if mode in {"serial", "full"} else "模型"
    return [
        f"项目  {workspace.workspace_root}",
        "",
        f"模式  {_mode_label(mode)}",
        "",
        f"{model_label}  {model_text}",
        "",
        f"工具  {_tool_summary(mode)}",
    ]


def _center_offset(outer_count: int, inner_count: int) -> int:
    return max((outer_count - inner_count) // 2, 0)


def _mode_label(mode: str) -> str:
    return {
        "solo": "solo 单代理",
        "serial": "serial 串行多代理",
        "full": "full 审核并行",
    }.get(mode, f"{mode or 'solo'} 单代理")


def _tool_summary(mode: str) -> str:
    if mode == "full":
        return "按需加载 · 审批保护"
    if mode == "serial":
        return "按需加载 · 计划校验"
    return "按需加载"


def _model_summary(settings: RuntimeSettings, catalog: dict) -> str:
    priority = list(settings.orchestrator_model_priority or [])
    models = {item.get("id"): item for item in catalog.get("models", [])}
    primary_id = next((model_id for model_id in priority if model_id in models), None)
    if primary_id is None and priority:
        primary_id = priority[0]
    if primary_id is None:
        configured = [item for item in catalog.get("models", []) if item.get("configured")]
        primary_id = configured[0].get("id") if configured else ""

    model_info = models.get(primary_id, {})
    name = model_info.get("model_name") or model_info.get("display_name_zh") or primary_id or "未配置"
    fallback_count = max(len([item for item in priority if item != primary_id]), 0)
    if fallback_count:
        return f"{name}  +{fallback_count} 备用"
    return str(name)


def _color_enabled(value: bool | None) -> bool:
    if value is not None:
        return bool(value)
    return not os.environ.get("NO_COLOR")


def _blue(value: str, enabled: bool) -> str:
    if not enabled or not value:
        return value
    return f"{BLUE}{value}{RESET}"


def _strip_ansi(value: str) -> str:
    return value.replace(BLUE, "").replace(RESET, "")


def _visible_padding(value: str, width: int) -> str:
    visible_width = _display_width(value)
    return " " * max(width - visible_width, 0)


def _render_box(lines: list[str], color_enabled: bool) -> str:
    inner_width = max((_display_width(line) for line in lines), default=0)
    top = _blue(f"{BOX_TOP_LEFT}{BOX_HORIZONTAL * (inner_width + 2)}{BOX_TOP_RIGHT}", color_enabled)
    bottom = _blue(f"{BOX_BOTTOM_LEFT}{BOX_HORIZONTAL * (inner_width + 2)}{BOX_BOTTOM_RIGHT}", color_enabled)
    rendered = [top]
    left_border = _blue(BOX_VERTICAL, color_enabled)
    right_border = _blue(BOX_VERTICAL, color_enabled)
    for line in lines:
        rendered.append(f"{left_border} {line}{_visible_padding(line, inner_width)} {right_border}")
    rendered.append(bottom)
    return "\n".join(rendered)


def _display_width(value: str) -> int:
    width = 0
    text = _strip_ansi(value)
    for char in text:
        if unicodedata.combining(char):
            continue
        width += 2 if unicodedata.east_asian_width(char) in {"F", "W"} else 1
    return width
