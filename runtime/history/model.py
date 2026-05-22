from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class HistoryItem:
    history_id: str
    session_id: str
    path: Path
    title: str
    created_at: str
    updated_at: str
    message_count: int
    last_user: str = ""
    last_assistant: str = ""
    storage_kind: str = "legacy_session"


@dataclass(frozen=True)
class HistoryPreview:
    history_id: str
    session_id: str
    first_user: str = ""
    last_user: str = ""
    last_assistant: str = ""
    run_context_summary: str = ""
    message_count: int = 0
    updated_at: str = ""


@dataclass(frozen=True)
class HistoryDeleteResult:
    history_id: str
    session_id: str
    title: str
    path: Path
    deleted: bool
