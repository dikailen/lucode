from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from runtime.common.conversation import append_recent_turn
from runtime.common.text_utils import sanitize_text


SESSION_SCHEMA_VERSION = 1
SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")


@dataclass(frozen=True)
class SessionSummary:
    session_id: str
    path: Path
    created_at: str
    updated_at: str
    message_count: int
    last_user: str = ""
    last_assistant: str = ""


class SessionStore:
    """Append-only JSONL session store scoped to one Lucode workspace."""

    def __init__(self, workspace_root: Path, max_message_chars: int = 20000):
        self.workspace_root = Path(workspace_root).resolve()
        self.sessions_dir = self.workspace_root / ".lucode" / "sessions"
        self.max_message_chars = max(1000, int(max_message_chars or 20000))

    def start_session(self) -> str:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return f"{timestamp}-{uuid.uuid4().hex[:8]}"

    def append_message(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        normalized_role = str(role or "").strip().lower()
        if normalized_role not in {"user", "assistant", "system", "tool"}:
            raise ValueError(f"不支持的会话消息角色：{role}")
        text = self._truncate(sanitize_text(str(content or "")))
        self.append_event(
            session_id,
            {
                "type": "message",
                "role": normalized_role,
                "content": text,
                "metadata": metadata or {},
            },
        )

    def append_event(self, session_id: str, event: dict[str, Any]) -> None:
        safe_id = self._validate_session_id(session_id)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": SESSION_SCHEMA_VERSION,
            "session_id": safe_id,
            "timestamp": _now_iso(),
            **dict(event or {}),
        }
        with self._path_for(safe_id).open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")

    def list_sessions(self, limit: int = 10) -> list[SessionSummary]:
        if not self.sessions_dir.is_dir():
            return []
        summaries = []
        for path in self.sessions_dir.glob("*.jsonl"):
            summary = self._summarize(path)
            if summary is not None:
                summaries.append(summary)
        summaries.sort(key=lambda item: item.updated_at, reverse=True)
        return summaries[: max(1, int(limit or 10))]

    def resolve_session_id(self, selector: str | None = None) -> str | None:
        query = str(selector or "last").strip()
        summaries = self.list_sessions(limit=100)
        if not summaries:
            return None
        if query.lower() in {"", "last", "latest", "最近"}:
            return summaries[0].session_id
        exact = [item for item in summaries if item.session_id == query]
        if exact:
            return exact[0].session_id
        matches = [item for item in summaries if item.session_id.startswith(query)]
        if not matches:
            return None
        if len(matches) > 1:
            raise ValueError(f"会话前缀不唯一：{query}")
        return matches[0].session_id

    def load_messages(self, session_id: str, limit: int | None = None) -> list[dict[str, str]]:
        safe_id = self._validate_session_id(session_id)
        messages = [
            {"role": str(event.get("role") or ""), "content": str(event.get("content") or "")}
            for event in self._iter_events(self._path_for(safe_id))
            if event.get("type") == "message"
        ]
        messages = [item for item in messages if item["role"] and item["content"]]
        if limit is not None:
            return messages[-max(1, int(limit)) :]
        return messages

    def load_recent_turns(self, session_id: str, max_messages: int = 6) -> list[dict[str, str]]:
        turns: list[dict[str, str]] = []
        for message in self.load_messages(session_id, limit=max_messages):
            append_recent_turn(turns, message["role"], message["content"], max_chars=800)
        return turns

    def load_compacted_context(
        self,
        session_id: str,
        *,
        tail_messages: int = 6,
        max_summary_chars: int = 2400,
    ):
        from runtime.context.compaction import ContextCompactor

        return ContextCompactor(
            tail_messages=tail_messages,
            max_summary_chars=max_summary_chars,
        ).compact(self.load_messages(session_id))

    async def load_tiered_compacted_context(
        self,
        session_id: str,
        *,
        tail_messages: int = 6,
        max_summary_chars: int = 2400,
        model_registry=None,
        runtime_settings=None,
        hooks=None,
        config=None,
        semantic_summarizer=None,
    ):
        from runtime.context.semantic_compaction import compact_messages_tiered

        return await compact_messages_tiered(
            self.load_messages(session_id),
            tail_messages=tail_messages,
            max_summary_chars=max_summary_chars,
            model_registry=model_registry,
            runtime_settings=runtime_settings,
            hooks=hooks,
            config=config,
            semantic_summarizer=semantic_summarizer,
        )

    def _summarize(self, path: Path) -> SessionSummary | None:
        created_at = ""
        updated_at = ""
        message_count = 0
        last_user = ""
        last_assistant = ""
        session_id = path.stem
        for event in self._iter_events(path):
            session_id = str(event.get("session_id") or session_id)
            timestamp = str(event.get("timestamp") or "")
            if timestamp and not created_at:
                created_at = timestamp
            if timestamp:
                updated_at = timestamp
            if event.get("type") != "message":
                continue
            message_count += 1
            role = str(event.get("role") or "").lower()
            content = str(event.get("content") or "")
            if role == "user":
                last_user = content
            elif role == "assistant":
                last_assistant = content
        if message_count <= 0:
            return None
        return SessionSummary(
            session_id=session_id,
            path=path,
            created_at=created_at or _file_time_iso(path),
            updated_at=updated_at or _file_time_iso(path),
            message_count=message_count,
            last_user=self._truncate(last_user, 160),
            last_assistant=self._truncate(last_assistant, 160),
        )

    def _iter_events(self, path: Path) -> Iterable[dict[str, Any]]:
        if not path.is_file():
            return []
        events: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)
        return events

    def _path_for(self, session_id: str) -> Path:
        return self.sessions_dir / f"{session_id}.jsonl"

    def _validate_session_id(self, session_id: str) -> str:
        safe_id = str(session_id or "").strip()
        if not safe_id or not SESSION_ID_PATTERN.match(safe_id):
            raise ValueError("会话 ID 只能包含字母、数字、点、下划线和短横线。")
        return safe_id

    def _truncate(self, text: str, max_chars: int | None = None) -> str:
        limit = max_chars or self.max_message_chars
        if len(text) <= limit:
            return text
        return text[:limit] + f"...[truncated {len(text) - limit} chars]"


def render_session_list(store: SessionStore, limit: int = 8) -> str:
    summaries = store.list_sessions(limit=limit)
    lines = [
        "最近会话",
        f"会话目录：{store.sessions_dir}",
    ]
    if not summaries:
        lines.append("暂无可恢复会话。完成一次普通对话后会自动生成 JSONL 记录。")
        return "\n".join(lines)
    for item in summaries:
        lines.append(f"- {item.session_id[:16]}  更新 {item.updated_at}  消息 {item.message_count}")
        if item.last_user:
            lines.append(f"  用户：{_short(item.last_user)}")
        if item.last_assistant:
            lines.append(f"  助手：{_short(item.last_assistant)}")
    lines.append("输入 /resume <会话ID前缀> 恢复上下文，或 /resume last 恢复最近一次。")
    return "\n".join(lines)


def render_resume_preview(store: SessionStore, session_id: str, max_messages: int = 4, compacted_context=None) -> str:
    if compacted_context is None:
        messages = store.load_messages(session_id, limit=max_messages)
        compacted_count = 0
    else:
        messages = compacted_context.recent_turns[-max_messages:]
        compacted_count = compacted_context.compacted_messages
    lines = [f"已恢复会话：{session_id}", "最近消息："]
    if compacted_count:
        source = "语义压缩" if getattr(compacted_context, "summary_source", "rules") == "semantic" else "规则压缩"
        lines.append(f"已折叠 {compacted_count} 条旧消息，最近 {len(compacted_context.recent_turns)} 条保留原文。")
        lines.append(f"摘要来源：{source}")
    for message in messages:
        label = "用户" if message["role"] == "user" else "助手"
        lines.append(f"- {label}：{_short(message['content'])}")
    return "\n".join(lines)


def _short(text: str, limit: int = 88) -> str:
    normalized = sanitize_text(str(text or "")).replace("\n", " ")
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit] + f"...[truncated {len(normalized) - limit} chars]"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _file_time_iso(path: Path) -> str:
    try:
        timestamp = path.stat().st_mtime
    except OSError:
        return ""
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
