from __future__ import annotations

import contextlib
from collections.abc import Iterator

from runtime.ui.capabilities import detect_dynamic_ui_capability, normalize_dynamic_ui_mode
from runtime.ui.terminal_owner import rich_live_owns_terminal

SNOW_SPINNER_NAME = "lucode_snow"
SNOW_SPINNER_FRAMES = ["\u2726", "\u2727", "\u2736", "\u2737", "\u2738", "\u2739", "\u273a", "\u2739", "\u2738", "\u2737", "\u2736", "\u2727"]


@contextlib.contextmanager
def dynamic_status(
    message: str,
    *,
    mode: str = "",
    stage: str = "",
    enabled: bool = True,
    spinner: str = "dots",
) -> Iterator[None]:
    """Show a transient Rich status line for long-running runtime phases."""

    if not enabled or rich_live_owns_terminal() or not _should_use_rich_status():
        yield
        return
    try:
        _register_snow_spinner()
        from rich.console import Console

        console = Console(force_terminal=True, color_system="auto")
        with console.status(
            _status_text(message, mode=mode, stage=stage),
            spinner=_spinner_name(spinner=spinner, stage=stage),
        ):
            yield
    except Exception:
        yield


def render_status_text(message: str, *, mode: str = "", stage: str = "") -> str:
    return _status_text(message, mode=mode, stage=stage)


def _should_use_rich_status() -> bool:
    ui_mode = normalize_dynamic_ui_mode()
    if ui_mode == "off":
        return False
    if ui_mode == "on":
        return True
    return bool(detect_dynamic_ui_capability().enabled)


def _register_snow_spinner() -> None:
    try:
        from rich.spinner import SPINNERS

        SPINNERS.setdefault(SNOW_SPINNER_NAME, {"interval": 90, "frames": SNOW_SPINNER_FRAMES})
    except Exception:
        return


def _spinner_name(*, spinner: str, stage: str) -> str:
    del stage
    if spinner == "dots":
        return SNOW_SPINNER_NAME
    return spinner


def _status_text(message: str, *, mode: str = "", stage: str = "") -> str:
    if str(stage or "").strip().lower() == "planning":
        return "Planning"
    parts = [_stage_label(stage)]
    clean_mode = str(mode or "").strip()
    if clean_mode:
        parts.append(clean_mode)
    clean_message = _one_line(message, 72)
    if clean_message:
        parts.append(clean_message)
    return "  ".join(part for part in parts if part)


def _stage_label(stage: str) -> str:
    normalized = str(stage or "").strip().lower()
    return {
        "planning": "Planning",
        "direct": "Thinking",
        "worker": "Working",
        "batch": "Working",
        "review": "Reviewing",
        "summary": "Summarizing",
        "supervisor": "Supervising",
    }.get(normalized, "Working")


def _one_line(value: str, limit: int) -> str:
    text = str(value or "").replace("\r", "").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 3)].rstrip() + "..."
