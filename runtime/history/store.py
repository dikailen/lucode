from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime.common.text_utils import sanitize_text
from runtime.context.compaction import redact_sensitive_text
from runtime.history.model import HistoryDeleteResult, HistoryItem, HistoryPreview
from runtime.sessions.store import SessionStore, SessionSummary


HISTORY_SCHEMA_VERSION = 1


class HistoryStore(SessionStore):
    """Canonical JSONL history store under .lucode/history."""

    def __init__(self, workspace_root: Path, max_message_chars: int = 20000):
        self.workspace_root = Path(workspace_root).resolve()
        self.history_dir = self.workspace_root / ".lucode" / "history"
        self.index_path = self.history_dir / "index.jsonl"
        self.contexts_dir = self.history_dir / "contexts"
        self.exports_dir = self.history_dir / "exports"
        super().__init__(
            self.workspace_root,
            max_message_chars=max_message_chars,
            sessions_dir=self.history_dir / "sessions",
        )

    def append_event(self, session_id: str, event: dict[str, Any]) -> None:
        super().append_event(session_id, event)
        self._append_context_entry(session_id, event)
        self._append_index_entry(session_id)

    def _append_context_entry(self, session_id: str, event: dict[str, Any]) -> None:
        metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        summary = sanitize_text(str(metadata.get("run_context_summary") or "")).strip()
        if event.get("type") != "message" or str(event.get("role") or "").lower() != "assistant" or not summary:
            return
        payload = {
            "schema_version": HISTORY_SCHEMA_VERSION,
            "type": "run_context_summary",
            "session_id": str(session_id),
            "timestamp": _now_iso(),
            "summary": redact_sensitive_text(summary),
        }
        try:
            self.contexts_dir.mkdir(parents=True, exist_ok=True)
            with self._context_path_for(session_id).open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        except Exception:
            # Context sidecars are an auxiliary cache; the canonical session JSONL stays authoritative.
            return

    def _context_path_for(self, session_id: str) -> Path:
        safe_id = self._validate_session_id(session_id)
        return self.contexts_dir / f"{safe_id}.context.jsonl"

    def _append_index_entry(self, session_id: str) -> None:
        try:
            summary = self._summarize(self._path_for(session_id))
            if summary is None:
                return
            self.history_dir.mkdir(parents=True, exist_ok=True)
            payload = {
                "schema_version": HISTORY_SCHEMA_VERSION,
                "type": "session_index",
                "session_id": summary.session_id,
                "path": str(summary.path.relative_to(self.history_dir)),
                "created_at": summary.created_at,
                "updated_at": summary.updated_at,
                "message_count": summary.message_count,
                "last_user": summary.last_user,
                "last_assistant": summary.last_assistant,
                "indexed_at": _now_iso(),
            }
            with self.index_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        except Exception:
            # Index is only a fast lookup/cache layer; session JSONL remains the source of truth.
            return


class HistoryFacade:
    """Stable history API backed by canonical history plus legacy JSONL sessions."""

    def __init__(
        self,
        workspace_root: Path,
        session_store: SessionStore | None = None,
        history_store: HistoryStore | None = None,
    ):
        self.workspace_root = Path(workspace_root).resolve()
        self.legacy_sessions_dir = self.workspace_root / ".lucode" / "sessions"
        self.history_dir = self.workspace_root / ".lucode" / "history"
        self.session_store = session_store or SessionStore(self.workspace_root)
        self.history_store = history_store or HistoryStore(self.workspace_root)

    def list_items(self, limit: int = 20) -> list[HistoryItem]:
        limit = max(1, int(limit or 20))
        items: list[HistoryItem] = []
        seen: set[str] = set()
        for storage_kind, store in (("history", self.history_store), ("legacy_session", self.session_store)):
            for summary in store.list_sessions(limit=limit):
                if summary.session_id in seen:
                    continue
                seen.add(summary.session_id)
                items.append(_history_item_from_summary(summary, storage_kind=storage_kind))
        items.sort(key=lambda item: item.updated_at, reverse=True)
        return items[:limit]

    def preview(self, history_id: str) -> HistoryPreview:
        session_id = self.resolve(history_id)
        if not session_id:
            return HistoryPreview(history_id=str(history_id or ""), session_id="")
        store = self._store_for(session_id)
        if store is None:
            return HistoryPreview(history_id=session_id, session_id=session_id)
        first_user = ""
        last_user = ""
        last_assistant = ""
        run_context_summary = ""
        message_count = 0
        updated_at = ""
        for event in store.load_events(session_id):
            timestamp = str(event.get("timestamp") or "")
            if timestamp:
                updated_at = timestamp
            if event.get("type") != "message":
                continue
            role = str(event.get("role") or "").lower()
            content = str(event.get("content") or "")
            metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
            message_count += 1
            if role == "user":
                if not first_user:
                    first_user = content
                last_user = content
            elif role == "assistant":
                last_assistant = content
                context_summary = str(metadata.get("run_context_summary") or "").strip()
                if context_summary:
                    run_context_summary = context_summary
        return HistoryPreview(
            history_id=session_id,
            session_id=session_id,
            first_user=_short(first_user, 180),
            last_user=_short(last_user, 180),
            last_assistant=_short(last_assistant, 220),
            run_context_summary=_short(run_context_summary, 260),
            message_count=message_count,
            updated_at=updated_at,
        )

    def resolve(self, selector: str | None) -> str | None:
        query = str(selector or "last").strip()
        items = self.list_items(limit=200)
        if not items:
            return None
        if query.lower() in {"", "last", "latest", "最近"}:
            return items[0].session_id
        exact = [item for item in items if item.session_id == query]
        if exact:
            return exact[0].session_id
        matches = [item for item in items if item.session_id.startswith(query)]
        if not matches:
            return None
        if len(matches) > 1:
            raise ValueError(f"会话前缀不唯一：{query}")
        return matches[0].session_id

    def delete(self, selector: str) -> HistoryDeleteResult:
        session_id = self.resolve(selector)
        if not session_id:
            raise ValueError(f"没有找到历史会话：{selector}")
        item = next((entry for entry in self.list_items(limit=200) if entry.session_id == session_id), None)
        store = self._store_for(session_id)
        path = (store.sessions_dir if store is not None else self.session_store.sessions_dir) / f"{session_id}.jsonl"
        title = item.title if item is not None else session_id
        deleted = False
        if path.is_file():
            path.unlink()
            deleted = True
        context_path = self.history_store.contexts_dir / f"{session_id}.context.jsonl"
        if context_path.is_file():
            try:
                context_path.unlink()
            except OSError:
                pass
        return HistoryDeleteResult(
            history_id=session_id,
            session_id=session_id,
            title=title,
            path=path,
            deleted=deleted,
        )

    def search(self, query: str, limit: int = 20) -> list[HistoryItem]:
        terms = [term.casefold() for term in sanitize_text(str(query or "")).split() if term.strip()]
        if not terms:
            return self.list_items(limit=limit)
        matches: list[HistoryItem] = []
        for item in self.list_items(limit=200):
            haystack = self._search_text(item.session_id)
            if all(term in haystack for term in terms):
                matches.append(item)
            if len(matches) >= max(1, int(limit or 20)):
                break
        return matches

    def export(self, selector: str, output_path: Path | None = None) -> Path:
        session_id = self.resolve(selector)
        if not session_id:
            raise ValueError(f"没有找到历史会话：{selector}")
        events = self.load_events(session_id)
        preview = self.preview(session_id)
        target = Path(output_path).resolve() if output_path is not None else self.history_store.exports_dir / f"{session_id}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self._render_export_markdown(session_id, preview, events), encoding="utf-8", newline="\n")
        return target

    def load_context_summary(self, history_id: str, max_chars: int = 2400) -> str:
        session_id = self.resolve(history_id)
        if not session_id:
            return ""
        summaries: list[str] = []
        context_path = self.history_store.contexts_dir / f"{session_id}.context.jsonl"
        if context_path.is_file():
            for event in _iter_jsonl(context_path):
                summary = sanitize_text(str(event.get("summary") or "")).strip()
                if summary:
                    summaries.append(summary)
        if not summaries:
            for event in self.load_events(session_id):
                metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
                summary = sanitize_text(str(metadata.get("run_context_summary") or "")).strip()
                if summary:
                    summaries.append(redact_sensitive_text(summary))
        if not summaries:
            return ""
        text = "\n\n".join(summaries[-3:])
        limit = max(400, int(max_chars or 2400))
        if len(text) <= limit:
            return text
        return text[:limit].rstrip() + "..."

    def load_recent_turns(self, history_id: str, max_messages: int = 6) -> list[dict[str, str]]:
        session_id = self.resolve(history_id)
        if not session_id:
            return []
        store = self._store_for(session_id)
        return store.load_recent_turns(session_id, max_messages=max_messages) if store else []

    def load_messages(self, history_id: str, limit: int | None = None) -> list[dict[str, str]]:
        session_id = self.resolve(history_id)
        if not session_id:
            return []
        store = self._store_for(session_id)
        return store.load_messages(session_id, limit=limit) if store else []

    def load_events(self, history_id: str) -> list[dict[str, Any]]:
        session_id = self.resolve(history_id)
        if not session_id:
            return []
        store = self._store_for(session_id)
        return store.load_events(session_id) if store else []

    def as_session_store(self) -> "HistoryFacadeSessionView":
        return HistoryFacadeSessionView(self)

    def migrate_legacy_session(self, selector: str) -> str | None:
        session_id = self.resolve(selector)
        if not session_id:
            return None
        source = self.session_store.sessions_dir / f"{session_id}.jsonl"
        target = self.history_store.sessions_dir / f"{session_id}.jsonl"
        if target.is_file() or not source.is_file():
            return session_id
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        self.history_store._append_index_entry(session_id)
        return session_id

    def _store_for(self, session_id: str) -> SessionStore | None:
        safe_id = str(session_id or "").strip()
        if safe_id and (self.history_store.sessions_dir / f"{safe_id}.jsonl").is_file():
            return self.history_store
        if safe_id and (self.session_store.sessions_dir / f"{safe_id}.jsonl").is_file():
            return self.session_store
        return None

    def _search_text(self, session_id: str) -> str:
        parts = [session_id]
        preview = self.preview(session_id)
        parts.extend(
            [
                preview.first_user,
                preview.last_user,
                preview.last_assistant,
                preview.run_context_summary,
            ]
        )
        for event in self.load_events(session_id):
            parts.append(str(event.get("content") or ""))
            metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
            parts.append(str(metadata.get("run_context_summary") or ""))
        parts.append(self.load_context_summary(session_id))
        return sanitize_text("\n".join(parts)).casefold()

    def _render_export_markdown(self, session_id: str, preview: HistoryPreview, events: list[dict[str, Any]]) -> str:
        lines = [
            f"# Lucode History Export: {session_id}",
            "",
            f"- Updated: {preview.updated_at or 'unknown'}",
            f"- Messages: {preview.message_count}",
        ]
        context_summary = self.load_context_summary(session_id)
        if context_summary:
            lines.extend(["", "## Context Summary", "", context_summary])
        lines.extend(["", "## Messages"])
        for event in events:
            if event.get("type") != "message":
                continue
            role = str(event.get("role") or "message").strip() or "message"
            content = sanitize_text(str(event.get("content") or "")).strip()
            if not content:
                continue
            lines.extend(["", f"### {role}", "", content])
        lines.append("")
        return "\n".join(lines)


class HistoryFacadeSessionView:
    """SessionStore-shaped adapter over HistoryFacade for resume compatibility."""

    def __init__(self, facade: HistoryFacade):
        self.facade = facade
        self.sessions_dir = facade.history_store.sessions_dir

    def list_sessions(self, limit: int = 10) -> list[SessionSummary]:
        summaries: list[SessionSummary] = []
        for item in self.facade.list_items(limit=limit):
            summaries.append(
                SessionSummary(
                    session_id=item.session_id,
                    path=item.path,
                    created_at=item.created_at,
                    updated_at=item.updated_at,
                    message_count=item.message_count,
                    last_user=item.last_user,
                    last_assistant=item.last_assistant,
                )
            )
        return summaries

    def resolve_session_id(self, selector: str | None = None) -> str | None:
        return self.facade.resolve(selector)

    def load_messages(self, session_id: str, limit: int | None = None) -> list[dict[str, str]]:
        return self.facade.load_messages(session_id, limit=limit)

    def load_events(self, session_id: str) -> list[dict[str, Any]]:
        return self.facade.load_events(session_id)

    def load_recent_turns(self, session_id: str, max_messages: int = 6) -> list[dict[str, str]]:
        return self.facade.load_recent_turns(session_id, max_messages=max_messages)

    def load_context_summary(self, session_id: str, max_chars: int = 2400) -> str:
        return self.facade.load_context_summary(session_id, max_chars=max_chars)


def _history_item_from_summary(summary: SessionSummary, *, storage_kind: str) -> HistoryItem:
    title = _short(summary.last_user or summary.last_assistant or summary.session_id, 42)
    return HistoryItem(
        history_id=summary.session_id,
        session_id=summary.session_id,
        path=summary.path,
        title=title,
        created_at=summary.created_at,
        updated_at=summary.updated_at,
        message_count=summary.message_count,
        last_user=summary.last_user,
        last_assistant=summary.last_assistant,
        storage_kind=storage_kind,
    )


def _short(text: Any, limit: int = 88) -> str:
    normalized = _clean_truncation_marker(sanitize_text(str(text or "")).replace("\n", " ").strip())
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit].rstrip() + "..."


def _clean_truncation_marker(text: str) -> str:
    return re.sub(r"\.\.\.\[truncated \d+ chars\]", "...", str(text or ""))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _iter_jsonl(path: Path):
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                text = line.strip()
                if not text:
                    continue
                try:
                    yield json.loads(text)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return
