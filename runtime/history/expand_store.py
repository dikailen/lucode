from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime.context.compaction import redact_sensitive_text
from runtime.ui.collapse import CollapsedBlock


DEFAULT_SESSION_ID = "default"


@dataclass(frozen=True)
class ExpandBlockRecord:
    block_id: str
    title: str
    kind: str
    path: Path
    created_at: str
    preview: str = ""


class ExpandBlockStore:
    """Local per-session store for collapsed output blocks."""

    def __init__(self, workspace_root: str | Path, session_id: str | None = None):
        self.workspace_root = Path(workspace_root).resolve()
        self.session_id = _safe_segment(session_id or DEFAULT_SESSION_ID)
        self.base_dir = self.workspace_root / ".lucode" / "expand" / self.session_id
        self.index_path = self.base_dir / "index.jsonl"

    def save(self, block: CollapsedBlock) -> ExpandBlockRecord:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        block_id = _safe_segment(block.block_id)
        existing = self.resolve(block_id)
        if existing is not None and existing.path.is_file():
            return existing
        text_path = self.base_dir / f"{block_id}.txt"
        created_at = _now_iso()
        preview = _one_line(block.preview, 160)
        text_path.write_text(redact_sensitive_text(block.full_text), encoding="utf-8", newline="\n")
        record = ExpandBlockRecord(
            block_id=block_id,
            title=str(block.title or block.kind or block_id),
            kind=str(block.kind or "text"),
            path=text_path,
            created_at=created_at,
            preview=preview,
        )
        payload = {
            "block_id": record.block_id,
            "title": record.title,
            "kind": record.kind,
            "path": record.path.name,
            "created_at": record.created_at,
            "preview": record.preview,
        }
        with self.index_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        return record

    def save_text(
        self,
        block_id: str,
        text: str,
        *,
        kind: str = "text",
        title: str = "",
        preview: str = "",
    ) -> ExpandBlockRecord:
        """Save a named expandable text block, replacing older records with the same id."""

        self.base_dir.mkdir(parents=True, exist_ok=True)
        safe_id = _safe_segment(block_id)
        if not safe_id:
            raise ValueError("block_id is required")
        text_path = self.base_dir / f"{safe_id}.txt"
        created_at = _now_iso()
        full_text = redact_sensitive_text(str(text or ""))
        text_path.write_text(full_text, encoding="utf-8", newline="\n")
        record = ExpandBlockRecord(
            block_id=safe_id,
            title=str(title or kind or safe_id),
            kind=str(kind or "text"),
            path=text_path,
            created_at=created_at,
            preview=_one_line(preview or full_text, 160),
        )
        self._write_index_records([item for item in self._load_index() if item.block_id != safe_id] + [record])
        return record

    def list_blocks(self, limit: int = 20) -> list[ExpandBlockRecord]:
        records = self._load_index()
        deduped: dict[str, ExpandBlockRecord] = {}
        for record in records:
            deduped[record.block_id] = record
        items = list(deduped.values())
        items.sort(key=lambda record: record.created_at, reverse=True)
        return items[: max(1, int(limit or 20))]

    def read(self, block_id: str) -> str | None:
        record = self.resolve(block_id)
        if record is None or not record.path.is_file():
            return None
        return record.path.read_text(encoding="utf-8")

    def resolve(self, selector: str) -> ExpandBlockRecord | None:
        query = _safe_segment(selector)
        if not query:
            return None
        matches = [record for record in self.list_blocks(limit=200) if record.block_id == query]
        if matches:
            return matches[0]
        prefix_matches = [record for record in self.list_blocks(limit=200) if record.block_id.startswith(query)]
        if len(prefix_matches) == 1:
            return prefix_matches[0]
        return None

    def clear(self) -> int:
        count = len(self.list_blocks(limit=10000))
        if self.base_dir.exists():
            shutil.rmtree(self.base_dir)
        return count

    def render_list(self, limit: int = 20) -> str:
        items = self.list_blocks(limit=limit)
        lines = ["可展开内容"]
        if not items:
            lines.append("暂无可展开内容。")
            return "\n".join(lines)
        for item in items:
            lines.append(f"- {item.block_id} | {item.kind} | {item.title} | {item.created_at}")
        lines.append("输入 /expand <id> 查看完整内容，或 /expand clear 清理当前会话展开块。")
        return "\n".join(lines)

    def render_detail(self, selector: str) -> str:
        record = self.resolve(selector)
        if record is None:
            return f"没有找到可展开内容：{selector}"
        text = self.read(record.block_id)
        if text is None:
            return f"展开内容文件不存在：{record.block_id}"
        return "\n".join([f"展开内容 {record.block_id} | {record.kind} | {record.title}", "", text])

    def _load_index(self) -> list[ExpandBlockRecord]:
        if not self.index_path.is_file():
            return []
        records: list[ExpandBlockRecord] = []
        try:
            lines = self.index_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        for line in lines:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            block_id = _safe_segment(payload.get("block_id"))
            path_name = _safe_segment(payload.get("path"))
            if not block_id or not path_name:
                continue
            records.append(
                ExpandBlockRecord(
                    block_id=block_id,
                    title=str(payload.get("title") or block_id),
                    kind=str(payload.get("kind") or "text"),
                    path=self.base_dir / path_name,
                    created_at=str(payload.get("created_at") or ""),
                    preview=str(payload.get("preview") or ""),
                )
            )
        return records

    def _write_index_records(self, records: list[ExpandBlockRecord]) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        with self.index_path.open("w", encoding="utf-8", newline="\n") as handle:
            for record in records:
                payload = {
                    "block_id": record.block_id,
                    "title": record.title,
                    "kind": record.kind,
                    "path": record.path.name,
                    "created_at": record.created_at,
                    "preview": record.preview,
                }
                handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def store_for_workspace(workspace_context=None, *, session_id: str | None = None) -> ExpandBlockStore:
    workspace_root = getattr(workspace_context, "workspace_root", None) if workspace_context is not None else None
    return ExpandBlockStore(Path(workspace_root or ".").resolve(), session_id=session_id)


def _safe_segment(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[^A-Za-z0-9_.-]+", "-", text).strip(".-")
    return text[:80]


def _one_line(value: str, limit: int) -> str:
    text = str(value or "").replace("\r", "").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
