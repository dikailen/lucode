from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from runtime.ui.output_view import build_output_view_model
from runtime.ui.plain_text_renderer import PlainTextRenderer, PlainTextRenderOptions


def render_execution_events(events: Iterable[Any], limit: int = 8) -> str:
    """Render a compact plain-text execution timeline."""

    view = build_output_view_model(events)
    return PlainTextRenderer().render_timeline(
        view,
        PlainTextRenderOptions(limit=limit, section_title="执行事件"),
    )


def render_execution_event_summary(events: Iterable[Any], limit: int = 12, *, expand_store=None) -> str:
    """Render grouped execution events without changing runtime behavior."""

    view = build_output_view_model(events)
    return PlainTextRenderer().render_event_summary(
        view,
        PlainTextRenderOptions(limit=limit, expand_store=expand_store, section_title="执行摘要"),
    )
