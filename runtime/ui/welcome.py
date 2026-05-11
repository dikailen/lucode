from __future__ import annotations

import os
import unicodedata

from catalog_system.model_catalog import load_model_catalog
from runtime.config.settings import RuntimeSettings
from runtime.config.workspace import WorkspaceContext


BLUE = "\033[94m"
RESET = "\033[0m"
LOGO_STATUS_GAP = 10
BOX_TOP_LEFT = "\u256d"
BOX_TOP_RIGHT = "\u256e"
BOX_BOTTOM_LEFT = "\u2570"
BOX_BOTTOM_RIGHT = "\u256f"
BOX_HORIZONTAL = "\u2500"
BOX_VERTICAL = "\u2502"

MASCOT_LOGO = [
    "      lucode",
    "   \\  \\    /  /",
    "    \\_/\\__/\\_/",
    "      / o  o \\",
    "     /   __   \\",
    "      \\_/  \\_/",
    "        \\__/",
]


def render_welcome_dashboard(
    workspace: WorkspaceContext,
    settings: RuntimeSettings,
    model_catalog: dict | None = None,
    use_color: bool | None = None,
    show_logo: bool = True,
) -> str:
    """Render the concise C1.5 startup dashboard."""

    catalog = model_catalog if model_catalog is not None else load_model_catalog()
    color_enabled = _color_enabled(use_color)
    logo = [_blue(line, color_enabled) for line in MASCOT_LOGO] if show_logo else []
    status = _status_lines(workspace, settings, catalog)
    width = max((_display_width(line) for line in logo), default=0) + (LOGO_STATUS_GAP if logo else 0)

    rows = []
    for index in range(max(len(logo), len(status))):
        left = logo[index] if index < len(logo) else ""
        right = status[index] if index < len(status) else ""
        rows.append(f"{left}{_visible_padding(left, width)}{right}".rstrip())
    return _render_box(rows, color_enabled)


def _status_lines(workspace: WorkspaceContext, settings: RuntimeSettings, catalog: dict) -> list[str]:
    model_text = _model_summary(settings, catalog)
    lines = [
        f"项目    {workspace.workspace_root}",
        f"配置    {' .lucode 已发现'.strip() if workspace.has_project_config else '未初始化'}",
    ]
    if settings.execution_mode == "serial":
        lines.extend(
            [
                "模式    serial 串行多代理",
                f"主脑    {model_text}",
                "执行    多任务串行",
                "副脑    final-synthesizer",
                "审查    计划校验开启",
                "并行    关闭",
            ]
        )
    elif settings.execution_mode == "full":
        lines.extend(
            [
                "模式    full 审核并行",
                f"主脑    {model_text}",
                "执行组  多 Agent 安全批次",
                "副脑    synthesizer / auditor",
                "账本    patch ledger 开启",
                "并行    仅无冲突任务",
            ]
        )
    else:
        lines.extend(
            [
                "模式    solo 单代理",
                f"模型    {model_text}",
                f"隐私    {_privacy_label(settings.privacy_mode)}",
                "工具    按需加载",
                "备份    已开启",
                "输入 / 查看命令",
            ]
        )
    return lines


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


def _privacy_label(mode: str) -> str:
    return {
        "offline": "离线本地",
        "local_first": "本地优先",
        "cloud_allowed": "允许云端",
    }.get(str(mode or "").strip(), str(mode or "未知"))


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
