from __future__ import annotations

import re
import subprocess
from pathlib import Path

from runtime.execution.fast_paths import _parse_git_status_short


INLINE_READONLY_MCP = {"project_filesystem_readonly", "code_locator", "git_tools"}
INLINE_FORBIDDEN_MCP = {"workspace_edit", "safe_backup", "command_runner", "web_search"}
INLINE_TEXT_SUFFIXES = {
    ".cfg",
    ".css",
    ".html",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".py",
    ".rs",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}
INLINE_IGNORED_PARTS = {
    ".agent_quarantine",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "venv",
}
INLINE_PATH_PATTERN = re.compile(
    r"(?:[\w.-]+[\\/])+[\w .@()+\-\[\]]+\.[A-Za-z0-9]{1,8}"
    r"|(?<![\w.-])[\w.-]+\.(?:cfg|css|html|ini|java|js|json|jsx|md|py|rs|toml|ts|tsx|txt|ya?ml)(?![\w.-])",
    re.IGNORECASE,
)


def _latest_workspace_context(project_root: Path, task) -> str:
    """Return a compact current workspace snapshot immediately before a task runs."""

    lines = ["最新项目状态："]
    read_set = [str(item).strip() for item in list(getattr(task, "read_set", []) or []) if str(item).strip()]
    write_intent = [
        str(item).strip() for item in list(getattr(task, "write_intent", []) or []) if str(item).strip()
    ]
    if read_set:
        lines.append("本任务声明读取范围：" + ", ".join(read_set[:8]))
    if write_intent:
        lines.append("本任务声明写入范围：" + ", ".join(write_intent[:8]))

    try:
        result = subprocess.run(
            ["git", "status", "--short"],
            cwd=project_root,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=10,
            shell=False,
        )
    except Exception as exc:
        lines.append(f"git status 暂不可用：{exc}")
        return "\n".join(lines)

    if result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip()
        lines.append(f"git status 暂不可用：{message[:300] or '未知错误'}")
        return "\n".join(lines)

    changed = _parse_git_status_short(result.stdout)
    if not changed:
        lines.append("当前 git 工作区没有改动。")
        return "\n".join(lines)

    lines.append("当前 git 工作区改动文件：")
    for item in changed[:12]:
        lines.append(f"- {item['path']}（{item['status']}）")
    if len(changed) > 12:
        lines.append(f"- 其余 {len(changed) - 12} 个改动文件已省略。")
    return "\n".join(lines)


def _inline_project_file_context(project_root: Path, task, refined_request: str, run_context=None) -> str:
    """Inline explicit readonly file targets so simple analysis does not burn MCP turns."""

    if not _should_inline_readonly_file_task(task):
        return ""
    paths = _resolve_explicit_project_file_paths(project_root, task, refined_request)
    if not paths:
        return ""

    snippets = []
    query = "\n".join([refined_request, str(getattr(task, "title", "") or ""), str(getattr(task, "instruction", "") or "")])
    for path in paths[:3]:
        excerpt = _read_project_file_excerpt(path, query=query)
        if not excerpt:
            continue
        relative = path.relative_to(project_root.resolve()).as_posix()
        snippets.append(f"### {relative}\n{excerpt}")
        _record_inline_file_snapshot(run_context, path, task, relative, excerpt)
    if not snippets:
        return ""
    return "内联只读文件片段：\n" + "\n\n".join(snippets)


def _record_inline_file_snapshot(run_context, path: Path, task, relative: str, excerpt: str) -> None:
    if run_context is None or not hasattr(run_context, "record_file_snapshot"):
        return
    summary = f"{relative} 已为任务 {getattr(task, 'id', '') or 'unknown'} 内联读取。"
    try:
        run_context.record_file_snapshot(
            path=path,
            task_id=str(getattr(task, "id", "") or ""),
            summary=summary,
            excerpt=excerpt,
        )
    except Exception:
        return


def _should_inline_readonly_file_task(task) -> bool:
    mcp = set(getattr(task, "mcp", []) or [])
    if not (mcp & INLINE_READONLY_MCP):
        return False
    if mcp & INLINE_FORBIDDEN_MCP:
        return False
    if list(getattr(task, "write_intent", []) or []):
        return False
    return True


def _resolve_explicit_project_file_paths(project_root: Path, task, refined_request: str) -> list[Path]:
    texts = [
        refined_request,
        str(getattr(task, "title", "") or ""),
        str(getattr(task, "instruction", "") or ""),
        " ".join(str(item) for item in list(getattr(task, "read_set", []) or [])),
    ]
    raw_candidates = []
    for item in list(getattr(task, "read_set", []) or []):
        raw_candidates.append(str(item))
    for match in INLINE_PATH_PATTERN.findall("\n".join(texts)):
        raw_candidates.append(str(match))

    resolved: list[Path] = []
    seen: set[str] = set()
    for candidate in raw_candidates:
        path = _resolve_project_file_candidate(project_root, candidate)
        if path is None:
            continue
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        resolved.append(path)
        if len(resolved) >= 5:
            break
    return resolved


def _resolve_project_file_candidate(project_root: Path, candidate: str) -> Path | None:
    value = str(candidate or "").strip().strip("`'\"“”‘’（）()[]<>，,。；;：:")
    if not value or "://" in value:
        return None
    value = value.replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    if not value or any(part == ".." for part in value.split("/")):
        return None

    root = project_root.resolve()
    candidate_path = Path(value)
    direct = (root / candidate_path).resolve() if not candidate_path.is_absolute() else candidate_path.resolve()
    if _safe_inline_project_file(root, direct):
        return direct

    if "/" not in value:
        matches = []
        try:
            for path in root.rglob(value):
                resolved = path.resolve()
                if _safe_inline_project_file(root, resolved):
                    matches.append(resolved)
                    if len(matches) > 1:
                        break
        except OSError:
            return None
        if len(matches) == 1:
            return matches[0]
    return None


def _safe_inline_project_file(project_root: Path, path: Path) -> bool:
    try:
        relative = path.relative_to(project_root)
    except ValueError:
        return False
    if not path.is_file():
        return False
    parts = {part.lower() for part in relative.parts}
    if parts & INLINE_IGNORED_PARTS:
        return False
    name = path.name.lower()
    if name == ".env" or any(marker in name for marker in ["secret", "token", "apikey", "api_key"]):
        return False
    return path.suffix.lower() in INLINE_TEXT_SUFFIXES


def _read_project_file_excerpt(path: Path, query: str, max_chars: int = 9000) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"读取失败：{exc}"
    lines = text.splitlines()
    rendered_all = _render_numbered_lines(lines)
    if len(rendered_all) <= max_chars:
        return rendered_all

    tokens = _excerpt_query_tokens(query)
    hit_indexes = []
    for index, line in enumerate(lines):
        lowered = line.lower()
        if any(token in lowered for token in tokens):
            hit_indexes.append(index)

    if not hit_indexes:
        excerpt = _render_numbered_lines(lines[:160])
        return _truncate_excerpt(excerpt, max_chars)

    selected: set[int] = set()
    for index in hit_indexes[:40]:
        start = max(index - 6, 0)
        end = min(index + 7, len(lines))
        selected.update(range(start, end))

    rendered = []
    previous = -2
    used = 0
    for index in sorted(selected):
        if index != previous + 1 and rendered:
            marker = "..."
            if used + len(marker) + 1 > max_chars:
                break
            rendered.append(marker)
            used += len(marker) + 1
        line = f"L{index + 1:04d}: {lines[index]}"
        if used + len(line) + 1 > max_chars:
            rendered.append("...已截断")
            break
        rendered.append(line)
        used += len(line) + 1
        previous = index
    return "\n".join(rendered)


def _render_numbered_lines(lines: list[str]) -> str:
    return "\n".join(f"L{index + 1:04d}: {line}" for index, line in enumerate(lines))


def _truncate_excerpt(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 8)].rstrip() + "\n...已截断"


def _excerpt_query_tokens(query: str) -> set[str]:
    lowered = str(query or "").lower()
    generic_tokens = {
        "analysis",
        "analyze",
        "code",
        "current",
        "file",
        "files",
        "main",
        "project",
        "runtime",
    }
    tokens = {
        token
        for token in re.findall(r"[a-z_][a-z0-9_]{3,}", lowered)
        if token not in generic_tokens
    }
    tokens.update(
        [
            "render_welcome_dashboard",
            "render_welcome",
            "welcome",
            "dashboard",
            "chat_loop",
            "/new",
            "/mode",
            "runtime_settings",
        ]
    )
    for marker in ["欢迎", "界面", "渲染", "状态栏", "仪表盘", "模式", "配置"]:
        if marker in lowered:
            tokens.add(marker)
    return tokens
