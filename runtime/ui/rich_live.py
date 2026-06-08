from __future__ import annotations

from contextlib import contextmanager
from io import StringIO
from typing import Any, Iterable

from runtime.ui.rich_live_view import RichActorBlock, RichLiveView, build_rich_live_view
from runtime.ui.theme import DEFAULT_UI_THEME, UiTheme, resolve_ui_theme

SNOW_FRAMES = [
    "\u2726",
    "\u2727",
    "\u2736",
    "\u2737",
    "\u2738",
    "\u2739",
    "\u273a",
    "\u2739",
    "\u2738",
    "\u2737",
    "\u2736",
    "\u2727",
]
ASCII_FRAMES = ["*", "+", "x", "+"]


@contextmanager
def rich_live_session(enabled: bool = True):
    """Placeholder lifecycle for the Rich dynamic layer.

    V1 renders deterministic frames through ``render_rich_live_snapshot``.
    The context manager gives later knives a stable place to attach a real
    ``rich.live.Live`` instance without changing execution code again.
    """

    yield None


def render_rich_live_snapshot(
    run_state,
    *,
    mode: str,
    attempt: int,
    active: str = "",
    frame_index: int = 0,
    theme: UiTheme | None = None,
) -> str:
    """Render one Rich dynamic-frame snapshot for the current run state."""

    return _render_rich_frame(run_state, mode=mode, attempt=attempt, active=active, frame_index=frame_index, theme=theme)


def render_rich_preview_snapshot(
    run_state,
    *,
    mode: str,
    attempt: int,
    active: str = "",
    frame_index: int = 0,
    theme: UiTheme | None = None,
) -> str:
    """Compatibility wrapper for older preview callers."""

    return render_rich_live_snapshot(run_state, mode=mode, attempt=attempt, active=active, frame_index=frame_index, theme=theme)


def _render_rich_frame(
    run_state,
    *,
    mode: str,
    attempt: int,
    active: str = "",
    frame_index: int = 0,
    theme: UiTheme | None = None,
) -> str:
    """Render a Rich live frame without owning terminal input."""

    view = build_rich_live_view(run_state, mode=mode, attempt=attempt, active=active)
    resolved_theme = theme or _theme_from_run_state(run_state)
    try:
        from rich.console import Console
    except Exception:
        return "\n".join(_preview_lines(view, frame_index=frame_index))

    console_file = StringIO()
    console = Console(file=console_file, force_terminal=True, color_system=None, width=110, record=False)
    console.print(_build_renderable_from_view(view, frame_index=frame_index, theme=resolved_theme))
    return console_file.getvalue().rstrip()


def build_rich_live_renderable(
    run_state,
    *,
    mode: str,
    attempt: int,
    active: str = "",
    frame_index: int = 0,
    theme: UiTheme | None = None,
) -> Any:
    """Build the Rich renderable used by real Live and deterministic snapshots."""

    view = build_rich_live_view(run_state, mode=mode, attempt=attempt, active=active)
    return AnimatedRichLiveRenderable(view, frame_index=frame_index, theme=theme or _theme_from_run_state(run_state))


class AnimatedRichLiveRenderable:
    """Rich renderable that advances actor symbols on each Live repaint."""

    def __init__(self, view: RichLiveView, *, frame_index: int = 0, theme: UiTheme | None = None) -> None:
        self.view = view
        self.frame_index = int(frame_index or 0)
        self.theme = theme or DEFAULT_UI_THEME

    def __rich_console__(self, console, options) -> Iterable[Any]:
        del console, options
        renderable = _build_renderable_from_view(self.view, frame_index=self.frame_index, theme=self.theme)
        self.frame_index += 1
        yield renderable


def _build_renderable_from_view(view: RichLiveView, *, frame_index: int = 0, theme: UiTheme | None = None) -> Any:
    try:
        from rich.console import Group
        from rich.text import Text
    except Exception:
        return "\n".join(_preview_lines(view, frame_index=frame_index))

    return Group(*_rich_renderables(view, frame_index=frame_index, text_type=Text, theme=theme or DEFAULT_UI_THEME))


def _rich_renderables(view: RichLiveView, *, frame_index: int = 0, text_type, theme: UiTheme) -> list[Any]:
    renderables: list[Any] = []
    if _show_plan_section(view):
        renderables.append(text_type("Plan", style=f"bold {theme.brand}"))
        if view.plan_items:
            for item in view.plan_items:
                renderables.append(text_type(f"{_plan_symbol(item.status)} {item.title}", style=_plan_line_style(item.status)))
        else:
            renderables.append(text_type("☐ Waiting for plan"))
        renderables.append(text_type(""))
    for index, block in enumerate(view.actor_blocks):
        if index:
            renderables.append(text_type(""))
        renderables.extend(_actor_renderables(block, frame_index=frame_index, text_type=text_type, theme=theme))
    return renderables


def _show_plan_section(view: RichLiveView) -> bool:
    return bool(view.plan_items) or str(getattr(view, "mode", "") or "").strip().lower() != "solo"


def _actor_renderables(block: RichActorBlock, *, frame_index: int = 0, text_type, theme: UiTheme) -> list[Any]:
    header_style = f"bold {theme.brand}" if block.role == "supervisor" else f"bold {theme.accent}"
    header = text_type(style=header_style)
    header.append(f"{_actor_symbol(block.status, frame_index=frame_index)} {block.title}")
    if block.subtitle:
        header.append(f"  {block.subtitle}")
    if block.model_label:
        header.append(f"  {block.model_label}", style=theme.model_label)
    action = block.current_action or "Waiting"
    return [header, text_type(f"└ {action}", style=theme.value)]


def _preview_lines(view: RichLiveView, *, frame_index: int = 0) -> list[str]:
    lines = []
    if _show_plan_section(view):
        lines.append("Plan")
        if view.plan_items:
            for item in view.plan_items:
                lines.append(f"{_plan_symbol(item.status)} {item.title}")
        else:
            lines.append("☐ Waiting for plan")
        lines.append("")
    for index, block in enumerate(view.actor_blocks):
        if index:
            lines.append("")
        lines.extend(_actor_lines(block, frame_index=frame_index))
    return lines


def _actor_lines(block: RichActorBlock, *, frame_index: int = 0) -> list[str]:
    subtitle = f"  {block.subtitle}" if block.subtitle else ""
    model = f"  {block.model_label}" if block.model_label else ""
    action = block.current_action or "Waiting"
    return [
        f"{_actor_symbol(block.status, frame_index=frame_index)} {block.title}{subtitle}{model}",
        f"└ {action}",
    ]


def _plan_symbol(status: str) -> str:
    if status == "completed":
        return "\u2713"
    if status == "running":
        return "◉"
    if status == "failed":
        return "\u00d7"
    return "\u25cb"


def _actor_symbol(status: str, *, frame_index: int = 0) -> str:
    if status == "running":
        return SNOW_FRAMES[int(frame_index or 0) % len(SNOW_FRAMES)]
    if status == "completed":
        return "\u2713"
    if status == "failed":
        return "\u00d7"
    return "\u25cb"


def _line_style(line: str) -> str:
    if line == "Plan":
        return "bold cyan"
    if "Supervisor" in line:
        return "bold cyan"
    if "Worker" in line:
        return "bold magenta"
    if line.startswith("\u2713"):
        return "green"
    if line.startswith("◉"):
        return "bold yellow"
    if line.startswith("\u00d7"):
        return "bold red"
    if line.startswith("└"):
        return "white"
    return ""


def _plan_line_style(status: str) -> str:
    if status == "completed":
        return "green"
    if status == "running":
        return "bold yellow"
    if status == "failed":
        return "bold red"
    return ""


def _theme_from_run_state(run_state) -> UiTheme:
    project_root = getattr(run_state, "project_root", None)
    return resolve_ui_theme(workspace_root=project_root)
