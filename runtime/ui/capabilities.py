from __future__ import annotations

import importlib.util
import os
import platform
import sys
from dataclasses import dataclass


@dataclass
class DynamicUICapability:
    enabled: bool
    reason: str = ""


def normalize_dynamic_ui_mode(value: str | None = None) -> str:
    raw = str(value if value is not None else os.environ.get("AGENTS_DYNAMIC_UI") or "auto").strip().lower()
    return raw if raw in {"auto", "on", "off"} else "auto"


def detect_dynamic_ui_capability(stdout=None) -> DynamicUICapability:
    mode = normalize_dynamic_ui_mode()
    if mode == "off":
        return DynamicUICapability(False, "disabled")

    stream = stdout if stdout is not None else sys.stdout
    is_tty = bool(getattr(stream, "isatty", lambda: False)())
    if not is_tty:
        return DynamicUICapability(False, "not_tty")
    if str(os.environ.get("TERM") or "").strip().lower() == "dumb":
        return DynamicUICapability(False, "dumb_terminal")
    if _env_flag_disabled("NO_COLOR") or _env_flag_disabled("AGENTS_DISABLE_ANSI"):
        return DynamicUICapability(False, "ansi_disabled")
    if platform.system().lower() == "windows" and _is_legacy_cmd():
        return DynamicUICapability(False, "legacy_cmd")
    if importlib.util.find_spec("rich") is None:
        return DynamicUICapability(False, "rich_missing")
    return DynamicUICapability(True, "")


def _env_flag_disabled(name: str) -> bool:
    raw = os.environ.get(name)
    return raw is not None and str(raw).strip().lower() not in {"", "0", "false", "no", "off"}


def _is_legacy_cmd() -> bool:
    if os.environ.get("WT_SESSION") or os.environ.get("TERM_PROGRAM"):
        return False
    return str(os.environ.get("COMSPEC") or "").lower().endswith("cmd.exe")
