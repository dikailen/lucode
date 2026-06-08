from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class MarkdownBlock:
    kind: str
    text: str
    language: str = ""


def split_markdown_blocks(text: str) -> list[MarkdownBlock]:
    """Split final-answer markdown into terminal-sized display blocks."""

    lines = str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: list[MarkdownBlock] = []
    paragraph: list[str] = []
    list_lines: list[str] = []
    index = 0

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            blocks.append(MarkdownBlock("paragraph", "\n".join(paragraph).strip()))
            paragraph = []

    def flush_list() -> None:
        nonlocal list_lines
        if list_lines:
            blocks.append(MarkdownBlock("list", "\n".join(list_lines).strip()))
            list_lines = []

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if not stripped:
            flush_paragraph()
            flush_list()
            index += 1
            continue
        if stripped.startswith("```"):
            flush_paragraph()
            flush_list()
            language = stripped[3:].strip()
            code_lines = [line]
            index += 1
            while index < len(lines):
                code_lines.append(lines[index])
                if lines[index].strip().startswith("```"):
                    index += 1
                    break
                index += 1
            blocks.append(MarkdownBlock("code", "\n".join(code_lines), language=language))
            continue
        if _is_heading(stripped):
            flush_paragraph()
            flush_list()
            blocks.append(MarkdownBlock("heading", stripped))
            index += 1
            continue
        if _is_list_line(stripped):
            flush_paragraph()
            list_lines.append(stripped)
            index += 1
            continue
        if _is_table_line(stripped):
            flush_paragraph()
            flush_list()
            table_lines = [stripped]
            index += 1
            while index < len(lines) and _is_table_line(lines[index].strip()):
                table_lines.append(lines[index].strip())
                index += 1
            blocks.append(MarkdownBlock("table", "\n".join(table_lines)))
            continue
        flush_list()
        paragraph.append(line)
        index += 1

    flush_paragraph()
    flush_list()
    return blocks


def _is_heading(line: str) -> bool:
    return bool(re.match(r"^#{1,6}\s+\S", line))


def _is_list_line(line: str) -> bool:
    return bool(re.match(r"^([-*+]\s+|\d+\.\s+)", line))


def _is_table_line(line: str) -> bool:
    return line.startswith("|") and line.endswith("|") and line.count("|") >= 2
