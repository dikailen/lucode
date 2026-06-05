from __future__ import annotations

import os
from pathlib import Path

from runtime.config.model_config import load_effective_lucode_config, load_lucode_config, save_lucode_config
from runtime.ui.theme import DEFAULT_UI_THEME, get_theme_preset, normalize_theme_name


def load_theme_name(
    *,
    workspace_root: Path | str | None = None,
    user_home: Path | str | None = None,
) -> str:
    env_theme = normalize_theme_name(os.environ.get("LUCODE_THEME"))
    if env_theme and get_theme_preset(env_theme):
        return env_theme

    config = load_effective_lucode_config(workspace_root=workspace_root, user_home=user_home)
    ui_config = config.get("ui") if isinstance(config.get("ui"), dict) else {}
    configured = normalize_theme_name((ui_config or {}).get("theme") or config.get("ui_theme"))
    return configured if get_theme_preset(configured) else DEFAULT_UI_THEME.name


def save_theme_name(
    name: str,
    *,
    workspace_root: Path | str | None = None,
) -> str:
    theme_name = normalize_theme_name(name)
    if get_theme_preset(theme_name) is None:
        raise ValueError(f"未知主题：{name}")

    config = load_lucode_config(workspace_root=workspace_root)
    config.pop("ui_theme", None)
    ui_config = dict(config.get("ui") or {})
    ui_config["theme"] = theme_name
    config["ui"] = ui_config
    save_lucode_config(config, workspace_root=workspace_root)
    return theme_name
