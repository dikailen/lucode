from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
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

from lucode.gui.sidebar_data import (
    McpRow,
    SkillCard,
    load_default_mcp_rows,
    load_default_skill_cards,
)


class SessionSidebar(QFrame):
    """Workbench sidebar for conversations, skills, and MCP status."""

    new_session_requested = Signal()
    session_selected = Signal(str)
    session_deleted = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("SessionSidebar")
        self.setMinimumWidth(220)
        self.setMaximumWidth(320)
        self.setProperty("collapsed", False)
        self.setProperty("activeTab", "chats")
        self.setProperty("transitioning", False)
        self._session_store = None
        self._selected_session_id = ""
        self._enabled = True
        self._active_tab = "chats"
        self._collapsed = False
        self._items_by_session_id: dict[str, Any] = {}
        self._skill_cards = load_default_skill_cards()
        self._mcp_rows = load_default_mcp_rows()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        self.icon_rail = QFrame()
        self.icon_rail.setObjectName("SidebarIconRail")
        icon_layout = QVBoxLayout(self.icon_rail)
        icon_layout.setContentsMargins(0, 0, 0, 0)
        icon_layout.setSpacing(8)
        self.icon_logo = QLabel("L")
        self.icon_logo.setObjectName("SidebarRailLogo")
        self.icon_logo.setAlignment(Qt.AlignCenter)
        icon_layout.addWidget(self.icon_logo)
        self.rail_chats_button = self._make_rail_button("聊", "chats", "SidebarRailChats")
        self.rail_skills_button = self._make_rail_button("技", "skills", "SidebarRailSkills")
        self.rail_mcp_button = self._make_rail_button("M", "mcp", "SidebarRailMcp")
        for button in (self.rail_chats_button, self.rail_skills_button, self.rail_mcp_button):
            icon_layout.addWidget(button)
        icon_layout.addStretch(1)
        self.icon_rail.hide()
        layout.addWidget(self.icon_rail)

        self.full_content = QFrame()
        self.full_content.setObjectName("SidebarFullContent")
        full_layout = QVBoxLayout(self.full_content)
        full_layout.setContentsMargins(0, 0, 0, 0)
        full_layout.setSpacing(10)
        layout.addWidget(self.full_content, 1)

        header = QHBoxLayout()
        title = QLabel("Lucode")
        title.setObjectName("SidebarTitle")
        header.addWidget(title)
        header.addStretch(1)
        full_layout.addLayout(header)

        self.new_session_button = QPushButton("+ 新会话")
        self.new_session_button.setObjectName("SidebarNewSessionButton")
        self.new_session_button.clicked.connect(self.new_session_requested.emit)
        full_layout.addWidget(self.new_session_button)

        self.tab_group = QButtonGroup(self)
        self.tab_group.setExclusive(True)
        tab_row = QHBoxLayout()
        tab_row.setSpacing(6)
        self.chats_tab = self._make_tab_button("会话", "chats", "SidebarTabChats")
        self.skills_tab = self._make_tab_button("技能", "skills", "SidebarTabSkills")
        self.mcp_tab = self._make_tab_button("MCP", "mcp", "SidebarTabMcp")
        for button in (self.chats_tab, self.skills_tab, self.mcp_tab):
            tab_row.addWidget(button)
            self.tab_group.addButton(button)
        self.chats_tab.setChecked(True)
        full_layout.addLayout(tab_row)

        self.search_box = QLineEdit()
        self.search_box.setObjectName("SessionSearchBox")
        self.search_box.setPlaceholderText("搜索会话")
        self.search_box.textChanged.connect(lambda _text: self.refresh())
        full_layout.addWidget(self.search_box)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("SessionListScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        full_layout.addWidget(self.scroll, 1)

        self.list_host = QWidget()
        self.list_layout = QVBoxLayout(self.list_host)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(6)
        self.scroll.setWidget(self.list_host)

        self.empty_label = QLabel("暂无会话")
        self.empty_label.setObjectName("SidebarEmpty")
        self.empty_label.setWordWrap(True)
        self.list_layout.addWidget(self.empty_label)
        self.list_layout.addStretch(1)
        self._sync_rail_buttons()

    def set_session_store(self, session_store) -> None:
        self._session_store = session_store

    def refresh(self) -> None:
        if self._active_tab == "skills":
            self._refresh_session_cache()
            self._render_skills()
            return
        if self._active_tab == "mcp":
            self._refresh_session_cache()
            self._render_mcp()
            return

        self._refresh_session_cache()
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
        if self._active_tab == "chats":
            self.refresh()

    def session_title(self, session_id: str) -> str:
        item = self._items_by_session_id.get(str(session_id or ""))
        return _item_title(item) if item is not None else ""

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        self._apply_enabled_state()

    def set_collapsed(self, collapsed: bool) -> None:
        self._collapsed = bool(collapsed)
        if self._collapsed:
            self.setMinimumWidth(56)
            self.setMaximumWidth(64)
        else:
            self.setMinimumWidth(220)
            self.setMaximumWidth(320)
        self.setProperty("collapsed", self._collapsed)
        self.style().unpolish(self)
        self.style().polish(self)
        self.full_content.setVisible(not self._collapsed)
        self.icon_rail.setVisible(self._collapsed)
        self._sync_rail_buttons()
        self._apply_enabled_state()

    def _make_tab_button(self, text: str, tab_id: str, object_name: str) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName(object_name)
        button.setCheckable(True)
        button.clicked.connect(lambda _checked=False, value=tab_id: self._switch_tab(value))
        return button

    def _make_rail_button(self, text: str, tab_id: str, object_name: str) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName(object_name)
        button.setCheckable(True)
        button.setToolTip({"chats": "会话", "skills": "技能", "mcp": "MCP"}.get(tab_id, tab_id))
        button.clicked.connect(lambda _checked=False, value=tab_id: self._switch_tab(value))
        return button

    def _switch_tab(self, tab_id: str) -> None:
        if tab_id not in {"chats", "skills", "mcp"}:
            return
        self.setProperty("transitioning", True)
        self._active_tab = tab_id
        self.setProperty("activeTab", tab_id)
        self.chats_tab.setChecked(tab_id == "chats")
        self.skills_tab.setChecked(tab_id == "skills")
        self.mcp_tab.setChecked(tab_id == "mcp")
        self._sync_rail_buttons()
        self.new_session_button.setVisible(tab_id == "chats")
        self.search_box.setVisible(tab_id == "chats")
        self.refresh()
        self.setProperty("transitioning", False)
        self._apply_enabled_state()

    def _apply_enabled_state(self) -> None:
        self.new_session_button.setEnabled(self._enabled)
        self.search_box.setEnabled(self._enabled)
        for button in (self.chats_tab, self.skills_tab, self.mcp_tab):
            button.setEnabled(self._enabled)
        for button in (self.rail_chats_button, self.rail_skills_button, self.rail_mcp_button):
            button.setEnabled(self._enabled)
        for button in self.findChildren(QPushButton, "SessionRowButton"):
            button.setEnabled(self._enabled)
        for button in self.findChildren(QPushButton, "SessionDeleteButton"):
            button.setEnabled(self._enabled)
        for button in self.findChildren(QPushButton, "SkillCardButton"):
            button.setEnabled(self._enabled)

    def _sync_rail_buttons(self) -> None:
        self.rail_chats_button.setChecked(self._active_tab == "chats")
        self.rail_skills_button.setChecked(self._active_tab == "skills")
        self.rail_mcp_button.setChecked(self._active_tab == "mcp")

    def _clear_list_layout(self) -> None:
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            widget = item.widget()
            if widget is None:
                continue
            widget.setParent(None)
            if widget is not self.empty_label:
                widget.deleteLater()

    def _refresh_session_cache(self) -> None:
        if self._session_store is None or not hasattr(self._session_store, "list_items"):
            return
        try:
            items = list(self._session_store.list_items(limit=50))
        except Exception:
            return
        self._items_by_session_id = {
            _item_session_id(item): item for item in items if _item_session_id(item)
        }

    def _render_items(self, items: list[Any]) -> None:
        self._clear_list_layout()
        self._items_by_session_id = {
            _item_session_id(item): item for item in items if _item_session_id(item)
        }
        if not items:
            self.empty_label.setText("暂无会话")
            self.empty_label.show()
            self.list_layout.addWidget(self.empty_label)
            self.list_layout.addStretch(1)
            self._apply_enabled_state()
            return
        self.empty_label.hide()
        for item in items:
            row = _SessionRow(item, selected=_item_session_id(item) == self._selected_session_id)
            row.session_selected.connect(self._on_session_selected)
            row.delete_requested.connect(self._on_delete_requested)
            self.list_layout.addWidget(row)
        self.list_layout.addStretch(1)
        self._apply_enabled_state()

    def _render_skills(self) -> None:
        self._clear_list_layout()
        title = QLabel("技能库")
        title.setObjectName("SkillPanelTitle")
        self.list_layout.addWidget(title)
        for card in self._skill_cards:
            self.list_layout.addWidget(_SkillCardRow(card))
        self.list_layout.addStretch(1)
        self._apply_enabled_state()

    def _render_mcp(self) -> None:
        self._clear_list_layout()
        title = QLabel("MCP 服务")
        title.setObjectName("McpPanelTitle")
        self.list_layout.addWidget(title)
        for row in self._mcp_rows:
            self.list_layout.addWidget(_McpStatusRow(row))
        self.list_layout.addStretch(1)
        self._apply_enabled_state()

    def _on_session_selected(self, session_id: str) -> None:
        self._selected_session_id = str(session_id or "")
        self.session_selected.emit(self._selected_session_id)

    def _on_delete_requested(self, session_id: str) -> None:
        if not session_id or self._session_store is None:
            return
        answer = QMessageBox.question(self, "删除会话", "确认删除这个会话吗？")
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


class _SkillCardRow(QFrame):
    def __init__(self, card: SkillCard, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("SkillCardRow")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        text = card.title
        if card.description:
            text = f"{text}\n{card.description}"
        if card.chips:
            text = f"{text}\n{'  '.join(card.chips)}"

        self.card_button = QPushButton(text)
        self.card_button.setObjectName("SkillCardButton")
        self.card_button.setProperty("skill_id", card.id)
        layout.addWidget(self.card_button)


class _McpStatusRow(QFrame):
    def __init__(self, row: McpRow, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("McpStatusRow")
        self.setProperty("mcp_id", row.id)
        self.setProperty("status", row.status)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(3)

        top = QHBoxLayout()
        name = QLabel(row.title)
        name.setObjectName("McpName")
        status = QLabel(row.status)
        status.setObjectName("McpStatus")
        top.addWidget(name, 1)
        top.addWidget(status)
        layout.addLayout(top)

        if row.detail:
            detail = QLabel(row.detail)
            detail.setObjectName("McpDetail")
            detail.setWordWrap(True)
            layout.addWidget(detail)


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
        parts.append(f"{count} 条消息")
    return " - ".join(parts)


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
