from __future__ import annotations

import sys
from typing import Any

from runtime.ui.markdown_blocks import MarkdownBlock, split_markdown_blocks


FINAL_ANSWER_TITLE = "最终回答"


def render_final_answer_text(output: str) -> str:
    """Render final answer as complete plain text.

    Final answers are user-facing results, not process material, so this function
    never folds, stores, or replaces content with /expand.
    """

    text = str(output or "").strip()
    if not text:
        return FINAL_ANSWER_TITLE
    return f"{FINAL_ANSWER_TITLE}\n\n{text}"


def print_final_answer(
    output: str,
    *,
    use_rich: bool = False,
    file=None,
    force_terminal: bool = True,
) -> None:
    stream = file if file is not None else sys.stdout
    if use_rich:
        try:
            from rich.console import Console, Group
        except Exception:
            print(render_final_answer_text(output), file=stream)
            return
        console = Console(file=stream, force_terminal=force_terminal, color_system="auto", width=110)
        console.print(Group(*_rich_final_answer_renderables(str(output or ""))))
        return
    print(render_final_answer_text(output), file=stream)


def _rich_final_answer_renderables(output: str) -> list[Any]:
    from rich.markdown import Markdown
    from rich.panel import Panel
    from rich.text import Text

    renderables: list[Any] = [Text(FINAL_ANSWER_TITLE, style="bold cyan")]
    blocks = split_markdown_blocks(output)
    if not blocks:
        return renderables
    for block in blocks:
        renderables.append(_rich_block(block, Markdown=Markdown, Panel=Panel, Text=Text))
    return renderables


def _rich_block(block: MarkdownBlock, *, Markdown, Panel, Text) -> Any:
    if block.kind == "heading":
        return Text(_strip_heading_marker(block.text), style="bold cyan")
    if block.kind == "code":
        return Panel(Markdown(block.text), border_style="bright_black")
    if block.kind in {"list", "table"}:
        return Markdown(block.text)
    return Text(block.text)


def _strip_heading_marker(text: str) -> str:
    value = str(text or "").strip()
    if value.startswith("#"):
        return value.lstrip("#").strip()
    return value
