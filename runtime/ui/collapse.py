from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class CollapsedBlock:
    block_id: str
    preview: str
    full_text: str
    kind: str
    title: str
    collapsed: bool = False
    omitted_lines: int = 0
    omitted_chars: int = 0


def collapse_text_block(
    text: str,
    kind: str,
    title: str,
    *,
    max_lines: int = 24,
    max_chars: int = 2400,
) -> CollapsedBlock:
    """Collapse long plain-text output while keeping the original text addressable."""

    full_text = str(text or "")
    clean_kind = str(kind or "text").strip() or "text"
    clean_title = str(title or clean_kind).strip() or clean_kind
    line_limit = max(1, int(max_lines or 24))
    char_limit = max(80, int(max_chars or 2400))
    lines = full_text.splitlines()
    needs_collapse = len(lines) > line_limit or len(full_text) > char_limit
    block_id = _block_id(clean_kind, clean_title, full_text)
    if not needs_collapse:
        return CollapsedBlock(
            block_id=block_id,
            preview=full_text,
            full_text=full_text,
            kind=clean_kind,
            title=clean_title,
            collapsed=False,
        )

    preview_lines = lines[:line_limit] if lines else [full_text[:char_limit]]
    preview = "\n".join(preview_lines)
    if len(preview) > char_limit:
        preview = preview[:char_limit].rstrip()
    omitted_lines = max(0, len(lines) - len(preview_lines))
    omitted_chars = max(0, len(full_text) - len(preview))
    hint = (
        f"[已折叠 {clean_kind}:{block_id}，输入 /expand {block_id} 查看完整内容；"
        f"省略 {omitted_lines} 行 / {omitted_chars} 字符]"
    )
    return CollapsedBlock(
        block_id=block_id,
        preview=f"{preview}\n{hint}" if preview else hint,
        full_text=full_text,
        kind=clean_kind,
        title=clean_title,
        collapsed=True,
        omitted_lines=omitted_lines,
        omitted_chars=omitted_chars,
    )


def _block_id(kind: str, title: str, text: str) -> str:
    digest = hashlib.sha1(f"{kind}\n{title}\n{text}".encode("utf-8", errors="replace")).hexdigest()
    return digest[:10]
