import json
import os
import hashlib
from datetime import datetime
from pathlib import Path

from mcp.server.fastmcp import FastMCP


mcp = FastMCP("budgeted_filesystem", log_level="ERROR")

SKIP_DIR_NAMES = {
    ".git",
    ".idea",
    ".agent_quarantine",
    ".agent_runs",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
}
PROTECTED_FILE_NAMES = {".env"}

READ_CALLS = 0
TOTAL_CHARS = 0


def _root() -> Path:
    return Path(os.environ["BUDGETED_FS_ROOT"]).resolve()


def _label() -> str:
    return os.environ.get("BUDGETED_FS_LABEL", "workspace")


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _max_read_calls() -> int:
    return _env_int("BUDGETED_FS_MAX_READ_CALLS", 10)


def _max_files_per_call() -> int:
    return _env_int("BUDGETED_FS_MAX_FILES_PER_CALL", 5)


def _max_chars_per_file() -> int:
    return _env_int("BUDGETED_FS_MAX_CHARS_PER_FILE", 6000)


def _max_total_chars() -> int:
    return _env_int("BUDGETED_FS_MAX_TOTAL_CHARS", 30000)


def _max_tree_depth() -> int:
    return _env_int("BUDGETED_FS_MAX_TREE_DEPTH", 3)


def _max_tree_entries() -> int:
    return _env_int("BUDGETED_FS_MAX_TREE_ENTRIES", 350)


def _resolve_path(path: str | None = ".") -> Path:
    root = _root()
    raw = Path(path or ".")
    if not raw.is_absolute():
        raw = root / raw
    resolved = raw.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"Path escapes allowed root: {resolved}")
    _ensure_allowed_path(resolved)
    return resolved


def _relative(path: Path) -> str:
    relative = path.resolve().relative_to(_root())
    return "." if str(relative) == "." else str(relative).replace("\\", "/")


def _ensure_allowed_path(path: Path) -> None:
    root = _root()
    relative = path.resolve().relative_to(root)
    parts = relative.parts
    if any(part in SKIP_DIR_NAMES for part in parts):
        raise ValueError(f"Path is intentionally hidden from this MCP: {_relative(path)}")
    if parts and parts[-1] in PROTECTED_FILE_NAMES:
        raise ValueError(f"Protected file is not readable through this MCP: {_relative(path)}")


def _is_visible_child(path: Path) -> bool:
    try:
        _ensure_allowed_path(path.resolve())
    except (OSError, ValueError):
        return False
    return True


def _start_read_call() -> None:
    global READ_CALLS
    if READ_CALLS >= _max_read_calls():
        raise RuntimeError(
            "Read budget exhausted for this MCP session. "
            "Use the files already read, or ask the user to narrow the target files."
        )
    READ_CALLS += 1


def _remaining_chars() -> int:
    return max(0, _max_total_chars() - TOTAL_CHARS)


def _consume_chars(text: str) -> str:
    global TOTAL_CHARS
    remaining = _remaining_chars()
    if remaining <= 0:
        raise RuntimeError(
            "Character budget exhausted for this MCP session. "
            "Use the information already gathered instead of reading more files."
        )
    if len(text) > remaining:
        visible = text[:remaining]
        TOTAL_CHARS += len(visible)
        return visible + f"\n...[truncated by total read budget: {len(text) - remaining} chars omitted]"
    TOTAL_CHARS += len(text)
    return text


def _looks_binary(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            sample = handle.read(2048)
    except OSError:
        return True
    return b"\x00" in sample


def _read_text(path: Path, max_chars: int) -> str:
    if not path.is_file():
        raise ValueError(f"Target is not a file: {_relative(path)}")
    if _looks_binary(path):
        raise ValueError(f"Refusing to read binary file: {_relative(path)}")

    byte_limit = max(4096, max_chars * 4)
    with path.open("rb") as handle:
        data = handle.read(byte_limit + 1)
    text = data[:byte_limit].decode("utf-8", errors="replace")
    if len(text) > max_chars:
        omitted = len(text) - max_chars
        text = text[:max_chars] + f"\n...[truncated by file read budget: {omitted} chars omitted]"
    elif len(data) > byte_limit:
        text += "\n...[truncated because file is larger than the byte read window]"
    return _consume_chars(text)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iter_children(path: Path) -> list[Path]:
    try:
        children = [child for child in path.iterdir() if _is_visible_child(child)]
    except OSError:
        return []
    return sorted(children, key=lambda item: (not item.is_dir(), item.name.lower()))


def _walk_visible(path: Path):
    stack = [path]
    while stack:
        current = stack.pop()
        children = _iter_children(current)
        for child in children:
            yield child
        for child in reversed(children):
            if child.is_dir():
                stack.append(child)


@mcp.tool(
    name="list_allowed_directories",
    description="List the single directory this read-only MCP can access.",
)
def list_allowed_directories() -> str:
    return json.dumps(
        {
            "label": _label(),
            "root": str(_root()),
            "hidden": sorted(SKIP_DIR_NAMES | PROTECTED_FILE_NAMES),
            "budgets": {
                "max_read_calls": _max_read_calls(),
                "max_files_per_call": _max_files_per_call(),
                "max_chars_per_file": _max_chars_per_file(),
                "max_total_chars": _max_total_chars(),
                "read_calls_used": READ_CALLS,
                "total_chars_used": TOTAL_CHARS,
            },
        },
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool(
    name="list_directory",
    description="List visible children under a directory inside the allowed root.",
)
def list_directory(path: str = ".") -> str:
    target = _resolve_path(path)
    if not target.is_dir():
        raise ValueError(f"Target is not a directory: {_relative(target)}")

    entries = []
    for child in _iter_children(target):
        entries.append(
            {
                "name": child.name,
                "path": _relative(child),
                "type": "directory" if child.is_dir() else "file",
            }
        )
    return json.dumps({"path": _relative(target), "entries": entries}, ensure_ascii=False, indent=2)


@mcp.tool(
    name="directory_tree",
    description="Return a bounded directory tree. Depth and entry count are capped to avoid context blowups.",
)
def directory_tree(path: str = ".", max_depth: int | None = None) -> str:
    target = _resolve_path(path)
    if not target.is_dir():
        raise ValueError(f"Target is not a directory: {_relative(target)}")

    depth_limit = min(max(0, int(max_depth if max_depth is not None else _max_tree_depth())), _max_tree_depth())
    state = {"count": 0, "truncated": False}

    def build_node(current: Path, depth: int) -> dict:
        state["count"] += 1
        if state["count"] > _max_tree_entries():
            state["truncated"] = True
            return {"name": current.name, "path": _relative(current), "type": "truncated"}

        node = {
            "name": current.name or _label(),
            "path": _relative(current),
            "type": "directory" if current.is_dir() else "file",
        }
        if current.is_dir() and depth < depth_limit:
            children = []
            for child in _iter_children(current):
                if state["count"] >= _max_tree_entries():
                    state["truncated"] = True
                    break
                children.append(build_node(child, depth + 1))
            node["children"] = children
        return node

    return json.dumps(
        {
            "root": _relative(target),
            "max_depth": depth_limit,
            "max_entries": _max_tree_entries(),
            "truncated": state["truncated"],
            "tree": build_node(target, 0),
        },
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool(
    name="read_file",
    description="Read one UTF-8 text file with per-file and per-session character budgets.",
)
def read_file(path: str, max_chars: int | None = None) -> str:
    target = _resolve_path(path)
    _start_read_call()
    limit = min(max(1, int(max_chars or _max_chars_per_file())), _max_chars_per_file())
    text = _read_text(target, limit)
    return json.dumps(
        {
            "path": _relative(target),
            "sha256": _sha256_file(target),
            "content": text,
            "budget": {
                "read_calls_used": READ_CALLS,
                "total_chars_used": TOTAL_CHARS,
                "total_chars_limit": _max_total_chars(),
            },
        },
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool(
    name="read_multiple_files",
    description="Read several UTF-8 text files. File count and character budgets are capped.",
)
def read_multiple_files(paths: list[str], max_chars_per_file: int | None = None) -> str:
    _start_read_call()
    if not paths:
        raise ValueError("paths must not be empty")
    file_limit = _max_files_per_call()
    selected_paths = list(paths)[:file_limit]
    char_limit = min(max(1, int(max_chars_per_file or _max_chars_per_file())), _max_chars_per_file())

    files = []
    for raw_path in selected_paths:
        target = _resolve_path(raw_path)
        try:
            content = _read_text(target, char_limit)
            files.append({"path": _relative(target), "sha256": _sha256_file(target), "content": content})
        except Exception as exc:
            files.append({"path": str(raw_path), "error": str(exc)})

    return json.dumps(
        {
            "files": files,
            "omitted_files": max(0, len(paths) - len(selected_paths)),
            "budget": {
                "read_calls_used": READ_CALLS,
                "total_chars_used": TOTAL_CHARS,
                "total_chars_limit": _max_total_chars(),
            },
        },
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool(
    name="search_files",
    description="Search visible file and directory names under a path. Content search belongs to code_locator.",
)
def search_files(path: str = ".", pattern: str = "", max_results: int = 50) -> str:
    target = _resolve_path(path)
    if not target.is_dir():
        raise ValueError(f"Target is not a directory: {_relative(target)}")
    if not pattern.strip():
        raise ValueError("pattern must not be empty")

    needle = pattern.lower()
    max_results = max(1, min(int(max_results or 50), 100))
    results = []
    for child in _walk_visible(target):
        if len(results) >= max_results:
            break
        rel = _relative(child)
        if needle in child.name.lower() or needle in rel.lower():
            results.append(
                {
                    "path": rel,
                    "type": "directory" if child.is_dir() else "file",
                }
            )
    return json.dumps({"pattern": pattern, "results": results}, ensure_ascii=False, indent=2)


@mcp.tool(
    name="get_file_info",
    description="Return metadata for one visible file or directory inside the allowed root.",
)
def get_file_info(path: str) -> str:
    target = _resolve_path(path)
    stat = target.stat()
    return json.dumps(
        {
            "path": _relative(target),
            "type": "directory" if target.is_dir() else "file",
            "size_bytes": stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            "suffix": target.suffix,
            "sha256": _sha256_file(target) if target.is_file() and not _looks_binary(target) else None,
        },
        ensure_ascii=False,
        indent=2,
    )


if __name__ == "__main__":
    mcp.run("stdio")
