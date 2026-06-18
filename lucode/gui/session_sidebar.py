from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


class SessionSidebar(QFrame):
    """Conversation history sidebar backed by the existing HistoryStore API."""

    new_session_requested = Signal()
    session_selected = Signal(str)
    session_deleted = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("SessionSidebar")
        self.setMinimumWidth(220)
        self.setMaximumWidth(320)
        self._session_store = None
        self._selected_session_id = ""
        self._enabled = True
        self._items_by_session_id: dict[str, Any] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("会话")
        title.setObjectName("SidebarTitle")
        header.addWidget(title)
        header.addStretch(1)
        layout.addLayout(header)

        self.new_session_button = QPushButton("+ 新会话")
        self.new_session_button.setObjectName("SidebarNewSessionButton")
        self.new_session_button.clicked.connect(self.new_session_requested.emit)
        layout.addWidget(self.new_session_button)

        self.search_box = QLineEdit()
        self.search_box.setObjectName("SessionSearchBox")
        self.search_box.setPlaceholderText("搜索会话")
        self.search_box.textChanged.connect(lambda _text: self.refresh())
        layout.addWidget(self.search_box)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("SessionListScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        layout.addWidget(self.scroll, 1)

        self.list_host = QWidget()
        self.list_layout = QVBoxLayout(self.list_host)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(6)
        self.scroll.setWidget(self.list_host)

        self.empty_label = QLabel("还没有历史会话")
        self.empty_label.setObjectName("SidebarEmpty")
        self.empty_label.setWordWrap(True)
        self.list_layout.addWidget(self.empty_label)
        self.list_layout.addStretch(1)

    def set_session_store(self, session_store) -> None:
        self._session_store = session_store

    def refresh(self) -> None:
        query = self.search_box.text().strip()
        items = []
        if self._session_store is not None:
            try:
                if query and hasattr(self._session_store, "search"):
                    items = list(self._session_store.search(query, limit=50))
                elif hasattr(self._session_store, "list_items"):
                    items = list(self._session_store.list_items(limit=50))
            except Exception:
                items = []
        self._render_items(items)

    def select_session(self, session_id: str) -> None:
        self._selected_session_id = str(session_id or "")
        self.refresh()

    def session_title(self, session_id: str) -> str:
        item = self._items_by_session_id.get(str(session_id or ""))
        return _item_title(item) if item is not None else ""

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        self._apply_enabled_state()

    def _apply_enabled_state(self) -> None:
        self.new_session_button.setEnabled(self._enabled)
        self.search_box.setEnabled(self._enabled)
        for button in self.findChildren(QPushButton, "SessionRowButton"):
            button.setEnabled(self._enabled)
        for button in self.findChildren(QPushButton, "SessionDeleteButton"):
            button.setEnabled(self._enabled)

    def _render_items(self, items: list[Any]) -> None:
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None and widget is not self.empty_label:
                widget.setParent(None)
                widget.deleteLater()
        self._items_by_session_id = {
            _item_session_id(item): item for item in items if _item_session_id(item)
        }
        self.list_layout.addWidget(self.empty_label)
        if not items:
            self.empty_label.setText("还没有历史会话")
            self.empty_label.show()
            self.list_layout.addStretch(1)
            return
        self.empty_label.hide()
        for item in items:
            row = _SessionRow(item, selected=_item_session_id(item) == self._selected_session_id)
            row.session_selected.connect(self._on_session_selected)
            row.delete_requested.connect(self._on_delete_requested)
            self.list_layout.addWidget(row)
        self.list_layout.addStretch(1)
        self._apply_enabled_state()

    def _on_session_selected(self, session_id: str) -> None:
        self._selected_session_id = str(session_id or "")
        self.session_selected.emit(self._selected_session_id)

    def _on_delete_requested(self, session_id: str) -> None:
        if not session_id or self._session_store is None:
            return
        answer = QMessageBox.question(self, "删除会话", "确定删除这段会话吗？")
        if answer != QMessageBox.Yes:
            return
        try:
            self._session_store.delete(session_id)
        except Exception:
            return
        if self._selected_session_id == session_id:
            self._selected_session_id = ""
        self.session_deleted.emit(session_id)
        self.refresh()


class _SessionRow(QFrame):
    session_selected = Signal(str)
    delete_requested = Signal(str)

    def __init__(self, item, *, selected: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("SessionRow")
        self.session_id = _item_session_id(item)
        self.setProperty("selected", bool(selected))
        self.style().unpolish(self)
        self.style().polish(self)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(6)

        title = _item_title(item)
        meta = _item_meta(item)
        self.row_button = QPushButton(f"{title}\n{meta}".strip())
        self.row_button.setObjectName("SessionRowButton")
        self.row_button.setProperty("session_id", self.session_id)
        self.row_button.clicked.connect(lambda: self.session_selected.emit(self.session_id))
        layout.addWidget(self.row_button, 1)

        self.delete_button = QPushButton("删除")
        self.delete_button.setObjectName("SessionDeleteButton")
        self.delete_button.setProperty("session_id", self.session_id)
        self.delete_button.clicked.connect(lambda: self.delete_requested.emit(self.session_id))
        layout.addWidget(self.delete_button)


def _item_session_id(item) -> str:
    if isinstance(item, dict):
        return str(item.get("session_id") or item.get("history_id") or "")
    return str(getattr(item, "session_id", "") or getattr(item, "history_id", "") or "")


def _item_title(item) -> str:
    if isinstance(item, dict):
        title = item.get("title") or item.get("last_user") or item.get("session_id") or ""
    else:
        title = getattr(item, "title", "") or getattr(item, "last_user", "") or getattr(item, "session_id", "")
    text = str(title or "").replace("\n", " ").strip()
    return text[:34] + "..." if len(text) > 36 else text or "未命名会话"


def _item_meta(item) -> str:
    if isinstance(item, dict):
        updated = item.get("updated_at") or ""
        count = item.get("message_count") or 0
    else:
        updated = getattr(item, "updated_at", "") or ""
        count = getattr(item, "message_count", 0) or 0
    parts = []
    relative = _relative_time(updated)
    if relative:
        parts.append(relative)
    if count:
        parts.append(f"{count} 条")
    return " · ".join(parts)


def _relative_time(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        updated = datetime.fromisoformat(text.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        seconds = max(0, int((now - updated).total_seconds()))
    except ValueError:
        return text[:10]
    if seconds < 60:
        return "刚刚"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} 分钟前"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} 小时前"
    days = hours // 24
    if days < 30:
        return f"{days} 天前"
    return updated.date().isoformat()
