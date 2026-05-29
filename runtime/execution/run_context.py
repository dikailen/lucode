from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

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
        return artifact

    def render_for_task(self, task_id: str = "") -> str:
        packs = list(self.context_packs.values())[-self.max_items :]
        files = list(self.file_snapshots.values())[-self.max_items :]
        tools = self.tool_outputs[-self.max_items :]
        if not packs and not files and not tools:
            return ""

        lines = [
            "本轮共享上下文：",
            "下面是前序任务已经读取或生成的证据摘要；如需逐字核对，再按需读取原文件。",
        ]
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
