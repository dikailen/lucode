from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any

from runtime.common.text_utils import sanitize_text


@dataclass(frozen=True)
class FileSnapshotArtifact:
    artifact_id: str
    path: str
    sha256: str
    summary: str
    task_ids: tuple[str, ...] = field(default_factory=tuple)
    excerpt: str = ""


@dataclass(frozen=True)
class ToolOutputArtifact:
    artifact_id: str
    tool: str
    action: str
    summary: str
    task_ids: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ContextPackArtifact:
    artifact_id: str
    pack_id: str
    summary: str
    shared_files: tuple[dict, ...] = field(default_factory=tuple)
    source_task_ids: tuple[str, ...] = field(default_factory=tuple)
    task_ids: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class SharedBlackboardEntry:
    key: str
    kind: str
    source_task_id: str
    content: str = ""
    summary: str = ""
    path: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "kind": self.kind,
            "source_task_id": self.source_task_id,
            "content": self.content,
            "summary": self.summary,
            "path": self.path,
            "timestamp": self.timestamp,
        }


class SharedBlackboard:
    """Run-scoped shared memory for supervisor and workers."""

    def __init__(
        self,
        project_root: Path,
        *,
        max_entries: int = 48,
        max_content_chars: int = 9000,
        max_render_chars: int = 2600,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.max_entries = max(1, int(max_entries or 48))
        self.max_content_chars = max(400, int(max_content_chars or 9000))
        self.max_render_chars = max(800, int(max_render_chars or 2600))
        self.entries: dict[str, SharedBlackboardEntry] = {}
        self._lock = RLock()

    def put_read(
        self,
        path: str | Path,
        *,
        content: str = "",
        summary: str = "",
        source: str = "",
    ) -> SharedBlackboardEntry:
        normalized = _normalize_blackboard_path(self.project_root, path)
        key = f"read:{normalized}"
        entry = SharedBlackboardEntry(
            key=key,
            kind="read_content",
            source_task_id=str(source or ""),
            content=_limit_text(content, self.max_content_chars),
            summary=_compact_line(summary or f"{normalized} 已读取。", 260),
            path=normalized,
        )
        return self._put(entry)

    def put_insight(self, text: str, *, source: str = "", key: str = "") -> SharedBlackboardEntry:
        clean_text = sanitize_text(str(text or "")).strip()
        digest = hashlib.sha256(f"{source}\n{clean_text}".encode("utf-8")).hexdigest()[:12]
        clean_key = _safe_token(key or f"insight:{source or 'shared'}:{digest}")
        entry = SharedBlackboardEntry(
            key=clean_key,
            kind="insight",
            source_task_id=str(source or ""),
            content=_limit_text(clean_text, self.max_content_chars),
            summary=_compact_line(clean_text, 280),
        )
        return self._put(entry)

    def put_write(
        self,
        path: str | Path,
        *,
        summary: str = "",
        source: str = "",
    ) -> SharedBlackboardEntry:
        normalized = _normalize_blackboard_path(self.project_root, path)
        key = f"write:{normalized}"
        entry = SharedBlackboardEntry(
            key=key,
            kind="written_file",
            source_task_id=str(source or ""),
            summary=_compact_line(summary or f"{normalized} 已写入或修改。", 260),
            path=normalized,
        )
        return self._put(entry)

    def get_read(self, path: str | Path) -> SharedBlackboardEntry | None:
        normalized = _normalize_blackboard_path(self.project_root, path)
        with self._lock:
            return self.entries.get(f"read:{normalized}")

    def render_for_worker(self, *, max_entries: int | None = None) -> str:
        with self._lock:
            entries = list(self.entries.values())[-max(1, int(max_entries or self.max_entries)) :]
        if not entries:
            return ""

        lines = [
            "共享黑板：",
            "下面是主管和前序 worker 已沉淀的公共资料；优先复用，必要时再读取原文件。",
        ]
        _append_blackboard_group(lines, "已读内容", [entry for entry in entries if entry.kind == "read_content"])
        _append_blackboard_group(lines, "共享洞察", [entry for entry in entries if entry.kind == "insight"])
        _append_blackboard_group(lines, "已写入文件", [entry for entry in entries if entry.kind == "written_file"])
        return _limit_text("\n".join(lines), self.max_render_chars)

    def _put(self, entry: SharedBlackboardEntry) -> SharedBlackboardEntry:
        with self._lock:
            self.entries[entry.key] = entry
            while len(self.entries) > self.max_entries:
                oldest = next(iter(self.entries))
                self.entries.pop(oldest, None)
        return entry


class RunContextStore:
    """In-memory context pack shared by tasks in one dynamic execution run."""

    def __init__(
        self,
        project_root: Path,
        *,
        max_items: int = 8,
        max_summary_chars: int = 1800,
        max_excerpt_chars: int = 1200,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.max_items = max(1, int(max_items or 8))
        self.max_summary_chars = max(400, int(max_summary_chars or 1800))
        self.max_excerpt_chars = max(200, int(max_excerpt_chars or 1200))
        self.file_snapshots: dict[str, FileSnapshotArtifact] = {}
        self.tool_outputs: list[ToolOutputArtifact] = []
        self.context_packs: dict[str, ContextPackArtifact] = {}
        self.blackboard = SharedBlackboard(
            self.project_root,
            max_entries=max_items * 6,
            max_content_chars=max_excerpt_chars,
            max_render_chars=max(800, max_summary_chars // 2),
        )

    def record_file_snapshot(
        self,
        *,
        path: Path,
        task_id: str = "",
        summary: str = "",
        excerpt: str = "",
    ) -> FileSnapshotArtifact:
        resolved = Path(path).resolve()
        sha256 = _sha256_file(resolved)
        relative = _relative_path(self.project_root, resolved)
        artifact_id = f"file:{relative}@{sha256[:12]}"
        previous = self.file_snapshots.get(artifact_id)
        task_ids = _append_task_id(previous.task_ids if previous else (), task_id)
        artifact = FileSnapshotArtifact(
            artifact_id=artifact_id,
            path=relative,
            sha256=sha256,
            summary=_compact_line(summary or _default_file_summary(relative, excerpt), 220),
            task_ids=task_ids,
            excerpt=_limit_text(excerpt, self.max_excerpt_chars),
        )
        self.file_snapshots[artifact_id] = artifact
        self.blackboard.put_read(
            relative,
            content=artifact.excerpt,
            summary=artifact.summary,
            source=task_id,
        )
        return artifact

    def record_tool_output(
        self,
        *,
        tool: str,
        action: str,
        summary: str,
        task_id: str = "",
    ) -> ToolOutputArtifact:
        clean_tool = _safe_token(tool or "tool")
        clean_action = _safe_token(action or "output")
        digest = hashlib.sha256(f"{clean_tool}\n{clean_action}\n{summary}".encode("utf-8")).hexdigest()[:12]
        artifact = ToolOutputArtifact(
            artifact_id=f"tool:{clean_tool}:{clean_action}@{digest}",
            tool=clean_tool,
            action=clean_action,
            summary=_compact_line(summary, 280),
            task_ids=_append_task_id((), task_id),
        )
        self.tool_outputs.append(artifact)
        if len(self.tool_outputs) > self.max_items:
            self.tool_outputs = self.tool_outputs[-self.max_items :]
        self.blackboard.put_insight(
            artifact.summary,
            source=task_id,
            key=artifact.artifact_id,
        )
        return artifact

    def record_context_pack(self, pack, *, task_id: str = "") -> ContextPackArtifact:
        pack_id = _safe_token(getattr(pack, "pack_id", "") or "context_pack")
        shared_files = tuple(dict(item) for item in list(getattr(pack, "shared_files", []) or []) if isinstance(item, dict))
        source_task_ids = tuple(str(item) for item in list(getattr(pack, "source_task_ids", []) or []) if str(item).strip())
        artifact_id = f"context_pack:{pack_id}"
        previous = self.context_packs.get(artifact_id)
        task_ids = _append_task_id(previous.task_ids if previous else (), task_id)
        artifact = ContextPackArtifact(
            artifact_id=artifact_id,
            pack_id=pack_id,
            summary=_compact_line(str(getattr(pack, "summary", "") or ""), 360),
            shared_files=shared_files,
            source_task_ids=source_task_ids,
            task_ids=task_ids,
        )
        self.context_packs[artifact_id] = artifact
        if artifact.summary:
            self.blackboard.put_insight(
                artifact.summary,
                source=task_id or ",".join(artifact.source_task_ids),
                key=artifact.artifact_id,
            )
        return artifact

    def put_read(self, path: str | Path, *, content: str = "", summary: str = "", source: str = "") -> SharedBlackboardEntry:
        return self.blackboard.put_read(path, content=content, summary=summary, source=source)

    def put_insight(self, text: str, *, source: str = "", key: str = "") -> SharedBlackboardEntry:
        return self.blackboard.put_insight(text, source=source, key=key)

    def put_write(self, path: str | Path, *, summary: str = "", source: str = "") -> SharedBlackboardEntry:
        return self.blackboard.put_write(path, summary=summary, source=source)

    def get_read(self, path: str | Path) -> SharedBlackboardEntry | None:
        return self.blackboard.get_read(path)

    def render_for_task(self, task_id: str = "") -> str:
        packs = list(self.context_packs.values())[-self.max_items :]
        files = list(self.file_snapshots.values())[-self.max_items :]
        tools = self.tool_outputs[-self.max_items :]
        blackboard = self.blackboard.render_for_worker()
        if not packs and not files and not tools and not blackboard:
            return ""

        lines = [
            "本轮共享上下文：",
            "下面是前序任务已经读取或生成的证据摘要；如需逐字核对，再按需读取原文件。",
        ]
        if blackboard:
            lines.append(blackboard)
        if packs:
            lines.append("ContextPack（主管公共资料包）：")
            for artifact in packs:
                tasks = _format_task_ids(artifact.task_ids or artifact.source_task_ids)
                lines.append(f"- {artifact.pack_id}（来源 {tasks}）：{artifact.summary}")
                shared = _format_shared_files(artifact.shared_files)
                if shared:
                    lines.append(f"  共享资源：{shared}")
        if files:
            lines.append("已读文件：")
            for artifact in files:
                tasks = _format_task_ids(artifact.task_ids)
                lines.append(f"- {artifact.path}（sha256 {artifact.sha256[:12]}，来源 {tasks}）：{artifact.summary}")
        if tools:
            lines.append("已得工具结果：")
            for artifact in tools:
                tasks = _format_task_ids(artifact.task_ids)
                lines.append(f"- {artifact.tool}.{artifact.action}（来源 {tasks}）：{artifact.summary}")
        return _limit_text("\n".join(lines), self.max_summary_chars)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 256), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_path(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def _append_task_id(existing: tuple[str, ...], task_id: str) -> tuple[str, ...]:
    clean = str(task_id or "").strip()
    if not clean or clean in existing:
        return existing
    return (*existing, clean)


def _format_task_ids(task_ids: tuple[str, ...]) -> str:
    return ", ".join(task_ids) if task_ids else "unknown"


def _format_shared_files(shared_files: tuple[dict, ...]) -> str:
    paths = []
    seen = set()
    for item in shared_files:
        path = str(item.get("path") or "").strip()
        if not path or path in seen:
            continue
        seen.add(path)
        paths.append(path)
        if len(paths) >= 8:
            break
    return ", ".join(paths)


def _append_blackboard_group(lines: list[str], title: str, entries: list[SharedBlackboardEntry]) -> None:
    if not entries:
        return
    lines.append(f"{title}：")
    for entry in entries:
        source = entry.source_task_id or "unknown"
        target = f"{entry.path}，" if entry.path else ""
        summary = entry.summary or _compact_line(entry.content, 240)
        lines.append(f"- {target}来源 {source}：{summary}")
        if entry.content and entry.kind == "read_content":
            excerpt = _limit_text(entry.content, 700)
            lines.append(_indent_text(excerpt, "  片段："))


def _indent_text(text: str, first_prefix: str) -> str:
    value = str(text or "")
    rendered = []
    for index, line in enumerate(value.splitlines() or [""]):
        prefix = first_prefix if index == 0 else "  "
        rendered.append(prefix + line)
    return "\n".join(rendered)


def _normalize_blackboard_path(root: Path, path: str | Path) -> str:
    if isinstance(path, Path):
        try:
            return _relative_path(root.resolve(), path.resolve()).replace("\\", "/")
        except OSError:
            return str(path).replace("\\", "/").strip().lstrip("./")
    value = str(path or "").strip().strip("`'\"“”‘’（）()[]<>，,。；;：:")
    value = value.replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    return value or "unknown"


def _default_file_summary(relative: str, excerpt: str) -> str:
    first_line = ""
    for line in str(excerpt or "").splitlines():
        stripped = line.strip()
        if stripped:
            first_line = stripped
            break
    if first_line:
        return f"{relative} 已读取，片段开头：{first_line}"
    return f"{relative} 已读取。"


def _compact_line(text: str, limit: int) -> str:
    normalized = sanitize_text(str(text or "")).replace("\n", " ").strip()
    return _limit_text(normalized, limit)


def _limit_text(text: str, limit: int) -> str:
    value = sanitize_text(str(text or ""))
    if len(value) <= limit:
        return value
    return value[:limit] + f"...[truncated {len(value) - limit} chars]"


def _safe_token(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"_", "-", "."} else "_" for char in str(value or "").strip())
