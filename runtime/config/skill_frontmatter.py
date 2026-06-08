from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def read_skill_frontmatter(skill_file: Path) -> dict[str, Any]:
    """Read the small YAML subset Lucode supports for SKILL.md metadata."""

    try:
        text = skill_file.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return {}
    return parse_skill_frontmatter(text)


def parse_skill_frontmatter(text: str) -> dict[str, Any]:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    return _parse_frontmatter_block(text[3:end])


def frontmatter_list(meta: dict[str, Any], *keys: str) -> list[str]:
    for key in keys:
        if key in meta:
            return _as_list(meta.get(key))
    return []


def frontmatter_bool(meta: dict[str, Any], *keys: str, default: bool = False) -> bool:
    for key in keys:
        if key in meta:
            return _as_bool(meta.get(key), default=default)
    return default


def frontmatter_text(meta: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = meta.get(key)
        if isinstance(value, list):
            return ", ".join(str(item).strip() for item in value if str(item).strip())
        if value is not None:
            return str(value).strip()
    return ""


def _parse_frontmatter_block(block: str) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    current_key = ""
    current_mode = ""
    scalar_parts: list[str] = []

    def flush_scalar() -> None:
        nonlocal scalar_parts, current_key, current_mode
        if current_key and current_mode == "scalar":
            meta[current_key] = " ".join(part.strip() for part in scalar_parts if part.strip()).strip()
        scalar_parts = []
        current_mode = ""

    for raw_line in block.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        line = raw_line.rstrip()
        key_match = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(.*)$", line)
        if key_match:
            flush_scalar()
            current_key = key_match.group(1).strip()
            raw_value = key_match.group(2).strip()
            if raw_value in {">", "|"}:
                current_mode = "scalar"
                scalar_parts = []
            elif raw_value == "":
                meta[current_key] = []
                current_mode = "list"
            else:
                meta[current_key] = _parse_inline_value(raw_value)
                current_mode = ""
            continue
        if not current_key or not line.startswith((" ", "\t")):
            continue
        stripped = line.strip()
        if current_mode == "list" and stripped.startswith("- "):
            value = stripped[2:].strip()
            meta.setdefault(current_key, [])
            if isinstance(meta[current_key], list):
                meta[current_key].append(_strip_quotes(value))
            continue
        if current_mode == "scalar":
            scalar_parts.append(stripped)
            continue
        if current_key:
            previous = meta.get(current_key, "")
            meta[current_key] = f"{previous} {stripped}".strip()

    flush_scalar()
    return meta


def _parse_inline_value(value: str) -> Any:
    value = _strip_quotes(value.strip())
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_strip_quotes(part.strip()) for part in inner.split(",") if part.strip()]
    lower = value.lower()
    if lower in {"true", "false"}:
        return lower == "true"
    return value


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            return _as_list(_parse_inline_value(stripped))
        return [item.strip() for item in re.split(r"[,;\n]+", stripped) if item.strip()]
    if value is None:
        return []
    return [str(value).strip()] if str(value).strip() else []


def _as_bool(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value or "").strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _strip_quotes(value: str) -> str:
    value = str(value or "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value
