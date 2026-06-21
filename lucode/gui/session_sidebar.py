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
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from lucode.gui.i18n import Translator, normalize_language
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
    settings_requested = Signal()
    collapse_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("SessionSidebar")
        self.setMinimumWidth(294)
        self.setMaximumWidth(294)
        self.setProperty("collapsed", False)
        self.setProperty("activeTab", "chats")
        self.setProperty("transitioning", False)
        self._session_store = None
        self._selected_session_id = ""
        self._enabled = True
        self._active_tab = "chats"
        self._collapsed = False
        self._language = 'zh'
        self._t = Translator(self._language)
        self._items_by_session_id: dict[str, Any] = {}
        self._skill_cards = load_default_skill_cards()
        self._mcp_rows = load_default_mcp_rows()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 28, 12, 18)
        layout.setSpacing(18)

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
        self.rail_settings_button = QPushButton("S")
        self.rail_settings_button.setObjectName("SidebarRailSettingsButton")
        self.rail_settings_button.clicked.connect(self.settings_requested.emit)
        icon_layout.addWidget(self.rail_settings_button)
        self.rail_toggle_button = QPushButton(">")
        self.rail_toggle_button.setObjectName("SidebarRailToggleButton")
        self.rail_toggle_button.clicked.connect(self.collapse_requested.emit)
        icon_layout.addWidget(self.rail_toggle_button)
        self.icon_rail.hide()
        layout.addWidget(self.icon_rail, 1)

        self.full_content = QFrame()
        self.full_content.setObjectName("SidebarFullContent")
        full_layout = QVBoxLayout(self.full_content)
        full_layout.setContentsMargins(0, 0, 0, 0)
        full_layout.setSpacing(14)
        layout.addWidget(self.full_content, 1)

        header = QHBoxLayout()
        title = QLabel("Lucode")
        title.setObjectName("SidebarTitle")
        header.addWidget(title)
        header.addStretch(1)
        full_layout.addLayout(header)

        self.tab_group = QButtonGroup(self)
        self.tab_group.setExclusive(True)
        self.nav = QFrame()
        self.nav.setObjectName("SidebarNav")
        nav_layout = QVBoxLayout(self.nav)
        nav_layout.setContentsMargins(0, 0, 0, 0)
        nav_layout.setSpacing(6)
        self.chats_tab = self._make_tab_button(self._t('sidebar.chats'), "chats", "SidebarTabChats")
        self.skills_tab = self._make_tab_button(self._t('sidebar.skills'), "skills", "SidebarTabSkills")
        self.mcp_tab = self._make_tab_button(self._t('sidebar.mcp'), "mcp", "SidebarTabMcp")
        for button in (self.chats_tab, self.skills_tab, self.mcp_tab):
            nav_layout.addWidget(button)
            self.tab_group.addButton(button)
        self.chats_tab.setChecked(True)
        full_layout.addWidget(self.nav)

        self.new_session_button = QPushButton(self._t('sidebar.new_chat'))
        self.new_session_button.setObjectName("SidebarNewSessionButton")
        self.new_session_button.clicked.connect(self.new_session_requested.emit)
        full_layout.addWidget(self.new_session_button)

        self.search_box = QLineEdit()
        self.search_box.setObjectName("SessionSearchBox")
        self.search_box.setPlaceholderText(self._t('sidebar.search'))
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

        self.utility_bar = QFrame()
        self.utility_bar.setObjectName("SidebarUtilityBar")
        utility_layout = QHBoxLayout(self.utility_bar)
        utility_layout.setContentsMargins(0, 0, 0, 0)
        utility_layout.setSpacing(8)
        self.sidebar_settings_button = QPushButton(self._t('control.settings_tip'))
        self.sidebar_settings_button.setObjectName("SidebarSettingsButton")
        self.sidebar_settings_button.clicked.connect(self.settings_requested.emit)
        utility_layout.addWidget(self.sidebar_settings_button, 1)
        self.sidebar_toggle_button = QPushButton("<")
        self.sidebar_toggle_button.setObjectName("SidebarToggleButton")
        self.sidebar_toggle_button.setToolTip(self._t('main.sidebar.toggle_tip'))
        self.sidebar_toggle_button.clicked.connect(self.collapse_requested.emit)
        utility_layout.addWidget(self.sidebar_toggle_button)
        full_layout.addWidget(self.utility_bar)
        self.sidebar_toggle_button.setVisible(True)

        self.empty_label = QLabel(self._t('sidebar.empty'))
        self.empty_label.setObjectName("SidebarEmpty")
        self.empty_label.setWordWrap(True)
        self.list_layout.addWidget(self.empty_label)
        self.list_layout.addStretch(1)
        self._sync_rail_buttons()

    def set_session_store(self, session_store) -> None:
        self._session_store = session_store

    def set_language(self, language: str) -> None:
        self._language = normalize_language(language)
        self._t = Translator(self._language)
        self.new_session_button.setText(self._t('sidebar.new_chat'))
        self.chats_tab.setText(self._t('sidebar.chats'))
        self.skills_tab.setText(self._t('sidebar.skills'))
        self.mcp_tab.setText(self._t('sidebar.mcp'))
        self.search_box.setPlaceholderText(self._t('sidebar.search'))
        self.sidebar_settings_button.setText(self._t('control.settings_tip'))
        self.sidebar_toggle_button.setToolTip(self._t('main.sidebar.toggle_tip'))
        self.rail_settings_button.setToolTip(self._t('control.settings_tip'))
        self.rail_toggle_button.setToolTip(self._t('main.sidebar.toggle_tip'))
        self._sync_rail_buttons()
        self.refresh()

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
            self.setMinimumWidth(64)
            self.setMaximumWidth(64)
            self.layout().setContentsMargins(12, 18, 12, 18)
        else:
            self.setMinimumWidth(294)
            self.setMaximumWidth(294)
            self.layout().setContentsMargins(20, 28, 12, 18)
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
        button.setProperty("sidebarNavItem", True)
        button.setMinimumHeight(34)
        button.setMaximumHeight(40)
        button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        button.clicked.connect(lambda _checked=False, value=tab_id: self._switch_tab(value))
        return button

    def _make_rail_button(self, text: str, tab_id: str, object_name: str) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName(object_name)
        button.setCheckable(True)
        button.setToolTip({"chats": self._t('sidebar.chats'), "skills": self._t('sidebar.skills'), "mcp": self._t('sidebar.mcp')}.get(tab_id, tab_id))
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
        self.sidebar_settings_button.setEnabled(self._enabled)
        self.sidebar_toggle_button.setEnabled(self._enabled)
        self.rail_settings_button.setEnabled(self._enabled)
        self.rail_toggle_button.setEnabled(self._enabled)
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
        for row in self.findChildren(QFrame, "SessionRow"):
            row.setEnabled(self._enabled)

    def _sync_rail_buttons(self) -> None:
        self.rail_chats_button.setChecked(self._active_tab == "chats")
        self.rail_skills_button.setChecked(self._active_tab == "skills")
        self.rail_mcp_button.setChecked(self._active_tab == "mcp")
        self.sidebar_toggle_button.setText(">" if self._collapsed else "<")
        self.rail_toggle_button.setText(">" if self._collapsed else "<")

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
            self.empty_label.setText(self._t('sidebar.empty'))
            self.empty_label.show()
            self.list_layout.addWidget(self.empty_label)
            self.list_layout.addStretch(1)
            self._apply_enabled_state()
            return
        self.empty_label.hide()
        for item in items:
            row = _SessionRow(item, selected=_item_session_id(item) == self._selected_session_id, language=self._language)
            row.session_selected.connect(self._on_session_selected)
            row.delete_requested.connect(self._on_delete_requested)
            self.list_layout.addWidget(row)
        self.list_layout.addStretch(1)
        self._apply_enabled_state()

    def _render_skills(self) -> None:
        self._clear_list_layout()
        title = QLabel(self._t('sidebar.skill_library'))
        title.setObjectName("SkillPanelTitle")
        self.list_layout.addWidget(title)
        for card in self._skill_cards:
            self.list_layout.addWidget(_SkillCardRow(card))
        self.list_layout.addStretch(1)
        self._apply_enabled_state()

    def _render_mcp(self) -> None:
        self._clear_list_layout()
        title = QLabel(self._t('sidebar.mcp_services'))
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
        answer = QMessageBox.question(self, self._t('sidebar.delete_title'), self._t('sidebar.delete_prompt'))
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

    def __init__(self, item, *, selected: bool = False, language: str = 'zh', parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("SessionRow")
        self.session_id = _item_session_id(item)
        self.setProperty("selected", bool(selected))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMinimumHeight(36)
        self.setMaximumHeight(44)
        self.setCursor(Qt.PointingHandCursor)
        self.style().unpolish(self)
        self.style().polish(self)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 6, 4)
        layout.setSpacing(8)

        self._t = Translator(language)
        title = _item_title(item, language=language)
        meta = _item_meta(item, language=language)

        text_host = QVBoxLayout()
        text_host.setContentsMargins(0, 0, 0, 0)
        text_host.setSpacing(1)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("SessionRowTitle")
        self.title_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        text_host.addWidget(self.title_label)
        self.meta_label = QLabel(meta)
        self.meta_label.setObjectName("SessionRowMeta")
        self.meta_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        text_host.addWidget(self.meta_label)
        layout.addLayout(text_host, 1)

        self.row_button = QPushButton(title, self)
        self.row_button.setObjectName("SessionRowButton")
        self.row_button.setProperty("session_id", self.session_id)
        self.row_button.clicked.connect(lambda: self.session_selected.emit(self.session_id))
        self.row_button.hide()

        self.delete_button = QPushButton("x")
        self.delete_button.setObjectName("SessionDeleteButton")
        self.delete_button.setProperty("session_id", self.session_id)
        self.delete_button.setToolTip(self._t('sidebar.delete'))
        self.delete_button.setMaximumWidth(22)
        self.delete_button.setMinimumWidth(22)
        self.delete_button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.delete_button.clicked.connect(lambda: self.delete_requested.emit(self.session_id))
        layout.addWidget(self.delete_button)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.session_selected.emit(self.session_id)
            event.accept()
            return
        super().mousePressEvent(event)


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


def _item_title(item, *, language: str = 'zh') -> str:
    if isinstance(item, dict):
        title = item.get("title") or item.get("last_user") or item.get("session_id") or ""
    else:
        title = getattr(item, "title", "") or getattr(item, "last_user", "") or getattr(item, "session_id", "")
    text = str(title or "").replace("\n", " ").strip()
    return text[:34] + "..." if len(text) > 36 else text or Translator(language)('sidebar.untitled')


def _item_meta(item, *, language: str = 'zh') -> str:
    if isinstance(item, dict):
        updated = item.get("updated_at") or ""
        count = item.get("message_count") or 0
    else:
        updated = getattr(item, "updated_at", "") or ""
        count = getattr(item, "message_count", 0) or 0
    parts = []
    t = Translator(language)
    relative = _relative_time(updated, language=language)
    if relative:
        parts.append(relative)
    if count:
        parts.append(f"{count} {t('sidebar.messages')}")
    return " - ".join(parts)


def _relative_time(value: str, *, language: str = 'zh') -> str:
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
        return Translator(language)('sidebar.just_now')
    minutes = seconds // 60
    if minutes < 60:
        return Translator(language)('sidebar.minutes_ago', count=minutes)
    hours = minutes // 60
    if hours < 24:
        return Translator(language)('sidebar.hours_ago', count=hours)
    days = hours // 24
    if days < 30:
        return Translator(language)('sidebar.days_ago', count=days)
    return updated.date().isoformat()
