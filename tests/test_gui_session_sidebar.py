from __future__ import annotations

import asyncio
import importlib.util
import os
from dataclasses import dataclass
from pathlib import Path

import pytest

HAS_PYSIDE = importlib.util.find_spec("PySide6") is not None
pytestmark = pytest.mark.skipif(not HAS_PYSIDE, reason="PySide6 is not installed")

if HAS_PYSIDE:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication, QLabel, QFrame, QLineEdit, QMessageBox, QPushButton  # noqa: E402
    from lucode.gui.chat_session import GuiChatSession  # noqa: E402
    from lucode.gui.main_window import MainWindow  # noqa: E402
    from lucode.gui.session_sidebar import SessionSidebar  # noqa: E402
    from lucode.gui.widgets import AnswerBlock, MessageBubble  # noqa: E402


@dataclass
class FakeHistoryItem:
    session_id: str
    title: str
    updated_at: str = "2026-06-18T00:00:00Z"
    message_count: int = 2


class FakeSessionStore:
    def __init__(self):
        self.items = [
            FakeHistoryItem("s1", "第一段对话", message_count=4),
            FakeHistoryItem("s2", "第二段对话", message_count=2),
        ]
        self.messages = {
            "s1": [
                {"role": "user", "content": "旧问题"},
                {"role": "assistant", "content": "旧回答"},
            ],
            "s2": [
                {"role": "user", "content": "第二个问题"},
                {"role": "assistant", "content": "第二个回答"},
            ],
        }
        self.recent_turns = {
            "s1": [{"role": "user", "content": "旧问题"}],
            "s2": [{"role": "user", "content": "第二个问题"}],
        }
        self.context_summaries = {"s1": "旧上下文摘要", "s2": "第二个摘要"}
        self.search_queries: list[str] = []
        self.deleted: list[str] = []

    def list_items(self, limit=20):
        del limit
        return list(self.items)

    def search(self, query, limit=20):
        del limit
        self.search_queries.append(query)
        return [item for item in self.items if query in item.title]

    def delete(self, session_id):
        self.deleted.append(session_id)
        self.items = [item for item in self.items if item.session_id != session_id]

    def resolve(self, selector):
        session_id = str(selector or "")
        return session_id if session_id in self.messages else None

    def load_messages(self, session_id, limit=None):
        messages = list(self.messages.get(session_id, []))
        return messages[-int(limit) :] if limit else messages

    def load_recent_turns(self, session_id, max_messages=6):
        del max_messages
        return list(self.recent_turns.get(session_id, []))

    def load_context_summary(self, session_id, max_chars=2400):
        del max_chars
        return self.context_summaries.get(session_id, "")

    def start_session(self, first_input):
        session_id = f"s{len(self.items) + 1}"
        self.items.append(FakeHistoryItem(session_id, first_input, message_count=2))
        self.messages[session_id] = []
        return session_id

    def append_message(self, session_id, role, content, metadata=None):
        del metadata
        self.messages.setdefault(session_id, []).append({"role": role, "content": content})


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


def _isolated_workspace(tmp_path: Path) -> Path:
    (tmp_path / ".lucode").mkdir(parents=True, exist_ok=True)
    return tmp_path


def test_sidebar_renders_history_and_filters_with_search(app):
    store = FakeSessionStore()
    sidebar = SessionSidebar()
    sidebar.set_session_store(store)
    sidebar.refresh()

    assert sidebar.findChild(QLabel, "SidebarEmpty").isHidden()
    buttons = sidebar.findChildren(QPushButton, "SessionRowButton")
    assert len(buttons) == 2
    assert all(button.toolTip() == "" for button in buttons)

    search = sidebar.findChild(QLineEdit, "SessionSearchBox")
    search.setText("第二")
    app.processEvents()

    assert store.search_queries[-1] == "第二"
    buttons = sidebar.findChildren(QPushButton, "SessionRowButton")
    assert len(buttons) == 1
    assert "第二段对话" in buttons[0].text()


def test_sidebar_delete_calls_store_and_refreshes(app, monkeypatch):
    store = FakeSessionStore()
    sidebar = SessionSidebar()
    sidebar.set_session_store(store)
    sidebar.refresh()
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)

    delete_buttons = sidebar.findChildren(QPushButton, "SessionDeleteButton")
    assert delete_buttons
    delete_buttons[0].click()
    app.processEvents()

    assert store.deleted == ["s1"]
    assert len(sidebar.findChildren(QPushButton, "SessionRowButton")) == 1


def test_sidebar_refresh_preserves_disabled_state(app):
    store = FakeSessionStore()
    sidebar = SessionSidebar()
    sidebar.set_session_store(store)
    sidebar.refresh()

    sidebar.set_enabled(False)
    sidebar.refresh()

    buttons = sidebar.findChildren(QPushButton, "SessionRowButton")
    delete_buttons = sidebar.findChildren(QPushButton, "SessionDeleteButton")
    assert buttons
    assert all(not button.isEnabled() for button in buttons)
    assert all(not button.isEnabled() for button in delete_buttons)


def test_sidebar_switches_between_chats_skills_and_mcp(app):
    store = FakeSessionStore()
    sidebar = SessionSidebar()
    sidebar.set_session_store(store)
    sidebar.refresh()

    assert sidebar.findChild(QPushButton, "SidebarTabChats").isChecked()
    assert len(sidebar.findChildren(QPushButton, "SessionRowButton")) == 2

    sidebar.findChild(QPushButton, "SidebarTabSkills").click()
    app.processEvents()

    assert sidebar.findChild(QPushButton, "SidebarTabSkills").isChecked()
    assert sidebar.findChild(QLabel, "SkillPanelTitle").text() == "技能库"
    skill_cards = sidebar.findChildren(QPushButton, "SkillCardButton")
    assert [button.property("skill_id") for button in skill_cards[:4]] == [
        "code_engineer",
        "project_explorer",
        "final_synthesizer",
        "skill_creator",
    ]

    sidebar.findChild(QPushButton, "SidebarTabMcp").click()
    app.processEvents()

    assert sidebar.findChild(QPushButton, "SidebarTabMcp").isChecked()
    assert sidebar.findChild(QLabel, "McpPanelTitle").text() == "MCP 服务"
    mcp_rows = sidebar.findChildren(QFrame, "McpStatusRow")
    statuses = {row.property("mcp_id"): row.property("status") for row in mcp_rows}
    assert statuses["filesystem"] == "已连接"
    assert statuses["image_draw"] == "离线"

    sidebar.findChild(QPushButton, "SidebarTabChats").click()
    app.processEvents()

    assert sidebar.findChild(QPushButton, "SidebarTabChats").isChecked()
    assert len(sidebar.findChildren(QPushButton, "SessionRowButton")) == 2




def test_sidebar_collapsed_mode_keeps_icon_rail_visible(app):
    store = FakeSessionStore()
    sidebar = SessionSidebar()
    sidebar.set_session_store(store)
    sidebar.refresh()
    sidebar.show()
    app.processEvents()

    sidebar.set_collapsed(True)
    app.processEvents()

    assert sidebar.property("collapsed") is True
    assert sidebar.maximumWidth() <= 76
    assert sidebar.findChild(QFrame, "SidebarIconRail").isVisible()
    assert not sidebar.new_session_button.isVisible()
    assert not sidebar.search_box.isVisible()
    assert not sidebar.scroll.isVisible()

    sidebar.findChild(QPushButton, "SidebarRailChats").click()
    sidebar.set_collapsed(False)
    app.processEvents()

    assert sidebar.property("collapsed") is False
    assert sidebar.maximumWidth() >= 220
    assert not sidebar.findChild(QFrame, "SidebarIconRail").isVisible()
    assert sidebar.new_session_button.isVisible()
    assert sidebar.search_box.isVisible()
    assert sidebar.scroll.isVisible()


def test_sidebar_tab_switch_marks_transition_state(app):
    sidebar = SessionSidebar()
    sidebar.refresh()

    sidebar.findChild(QPushButton, "SidebarTabSkills").click()
    app.processEvents()

    assert sidebar.property("activeTab") == "skills"
    assert sidebar.property("transitioning") is False
def test_main_window_new_session_clears_messages_and_title(app, tmp_path):
    workspace = _isolated_workspace(tmp_path)
    session = GuiChatSession(workspace=workspace)
    session.current_session_id = "existing"
    session.recent_turns = [{"role": "user", "content": "old"}]
    session.resumed_session_summary = "summary"
    window = MainWindow(workspace=workspace, chat_session=session)
    window.show()
    app.processEvents()
    window.add_message("user", "hello")

    window.session_sidebar.new_session_button.click()
    app.processEvents()

    assert session.current_session_id is None
    assert session.recent_turns == []
    assert session.resumed_session_summary == ""
    assert window.session_title_label.text() == "新会话"
    assert window.empty_state.isVisible()


def test_main_window_select_session_restores_messages_and_context(app, tmp_path):
    workspace = _isolated_workspace(tmp_path)
    session = GuiChatSession(workspace=workspace)
    store = FakeSessionStore()
    session.session_store = store
    window = MainWindow(workspace=workspace, chat_session=session)
    window.show()
    app.processEvents()

    window.session_sidebar.session_selected.emit("s1")
    app.processEvents()

    assert session.current_session_id == "s1"
    assert session.recent_turns == [{"role": "user", "content": "旧问题"}]
    assert session.resumed_session_summary == "旧上下文摘要"
    assert window.session_title_label.text() == "第一段对话"
    assert window.empty_state.isHidden()
    bubbles = window.message_host.findChildren(MessageBubble)
    answers = window.message_host.findChildren(AnswerBlock)
    assert len(bubbles) == 1
    assert bubbles[0].content_label.text() == "旧问题"
    assert len(answers) == 1
    assert answers[0].content_label.text() == "旧回答"


def test_main_window_ignores_session_select_while_turn_is_running(app, tmp_path):
    workspace = _isolated_workspace(tmp_path)
    session = GuiChatSession(workspace=workspace)
    store = FakeSessionStore()
    session.session_store = store
    session.current_session_id = "existing"
    window = MainWindow(workspace=workspace, chat_session=session)
    window.show()
    app.processEvents()
    window.turn_guard.start()

    window.session_sidebar.session_selected.emit("s1")
    app.processEvents()

    assert session.current_session_id == "existing"
    assert window.session_title_label.text() == "新会话"


def test_turn_ended_refreshes_sidebar(app, tmp_path):
    workspace = _isolated_workspace(tmp_path)
    session = GuiChatSession(workspace=workspace)
    store = FakeSessionStore()
    session.session_store = store
    window = MainWindow(workspace=workspace, chat_session=session)
    window.show()
    app.processEvents()
    window.session_sidebar.set_session_store(store)
    window.session_sidebar.refresh()
    store.items.append(FakeHistoryItem("s3", "第三段对话"))

    window.handle_runtime_event({"event_type": "TurnEnded", "payload": {"status": "completed"}})
    app.processEvents()

    assert len(window.session_sidebar.findChildren(QPushButton, "SessionRowButton")) == 3


def test_answer_delta_creates_and_appends_answer_block(app, tmp_path):
    workspace = _isolated_workspace(tmp_path)
    session = GuiChatSession(workspace=workspace)
    session.settings.execution_mode = "solo"
    window = MainWindow(workspace=workspace, chat_session=session)
    window.show()
    app.processEvents()
    turn_id = window.turn_guard.start()
    window.work_task_id = turn_id

    window.handle_runtime_event(
        {
            "event_type": "AgentMessageDelta",
            "agent": "solo",
            "task_id": "solo_agent",
            "payload": {"text": "Hello"},
        }
    )
    window.handle_runtime_event(
        {
            "event_type": "AgentMessageDelta",
            "agent": "solo",
            "task_id": "solo_agent",
            "payload": {"text": " world"},
        }
    )
    app.processEvents()

    answers = window.message_host.findChildren(AnswerBlock)
    assert len(answers) == 1
    assert answers[0].content_label.text() == "Hello world"


def test_run_turn_updates_streamed_answer_instead_of_adding_duplicate(app, tmp_path):
    from lucode.gui.chat_session import GuiTurnResult

    class StreamingGuiChatSession(GuiChatSession):
        async def run_turn(self, user_input: str):
            del user_input
            window.handle_runtime_event(
                {
                    "event_type": "AgentMessageDelta",
                    "agent": "full_supervisor_agent",
                    "payload": {"text": "Partial"},
                }
            )
            return GuiTurnResult(final_output="Partial final", execution_mode="full")

    workspace = _isolated_workspace(tmp_path)
    session = StreamingGuiChatSession(workspace=workspace)
    session.settings.execution_mode = "full"
    window = MainWindow(workspace=workspace, chat_session=session)
    window.show()
    app.processEvents()

    turn_id = window.turn_guard.start()
    window.work_task_id = turn_id
    asyncio.run(window._run_turn(turn_id, "question"))
    app.processEvents()

    answers = window.message_host.findChildren(AnswerBlock)
    assert len(answers) == 1
    assert answers[0].content_label.text() == "Partial final"


def test_run_turn_completion_refreshes_sidebar_after_history_write(app, tmp_path):
    class RecordingGuiChatSession(GuiChatSession):
        async def run_turn(self, user_input: str):
            self.current_session_id = self.session_store.start_session(user_input)
            self.session_store.append_message(self.current_session_id, "user", user_input)
            self.session_store.append_message(self.current_session_id, "assistant", "回答")
            from lucode.gui.chat_session import GuiTurnResult

            return GuiTurnResult(final_output="回答", execution_mode=self.settings.execution_mode)

    workspace = _isolated_workspace(tmp_path)
    session = RecordingGuiChatSession(workspace=workspace)
    store = FakeSessionStore()
    store.items = []
    session.session_store = store
    window = MainWindow(workspace=workspace, chat_session=session)
    window.session_sidebar.set_session_store(store)
    window.show()
    app.processEvents()

    turn_id = window.turn_guard.start()
    asyncio.run(window._run_turn(turn_id, "新问题"))
    app.processEvents()

    buttons = window.session_sidebar.findChildren(QPushButton, "SessionRowButton")
    assert len(buttons) == 1
    assert "新问题" in buttons[0].text()


def test_run_turn_refreshes_title_while_sidebar_is_on_skills(app, tmp_path):
    class RecordingGuiChatSession(GuiChatSession):
        async def run_turn(self, user_input: str):
            self.current_session_id = self.session_store.start_session(user_input)
            self.session_store.append_message(self.current_session_id, "user", user_input)
            self.session_store.append_message(self.current_session_id, "assistant", "answer")
            from lucode.gui.chat_session import GuiTurnResult

            return GuiTurnResult(final_output="answer", execution_mode=self.settings.execution_mode)

    workspace = _isolated_workspace(tmp_path)
    session = RecordingGuiChatSession(workspace=workspace)
    store = FakeSessionStore()
    store.items = []
    session.session_store = store
    window = MainWindow(workspace=workspace, chat_session=session)
    window.show()
    app.processEvents()
    window.session_sidebar.findChild(QPushButton, "SidebarTabSkills").click()
    app.processEvents()

    turn_id = window.turn_guard.start()
    asyncio.run(window._run_turn(turn_id, "Fresh title"))
    app.processEvents()

    assert window.session_title_label.text() == "Fresh title"
    assert window.session_sidebar.findChild(QPushButton, "SidebarTabSkills").isChecked()


def test_sidebar_uses_history_facade_for_real_gui_session_store(app, tmp_path):
    workspace = _isolated_workspace(tmp_path)
    session = GuiChatSession(workspace=workspace)
    session_id = session.session_store.start_session("真实问题")
    session.session_store.append_message(session_id, "user", "真实问题")
    session.session_store.append_message(session_id, "assistant", "真实回答")

    window = MainWindow(workspace=workspace, chat_session=session)
    window.show()
    app.processEvents()

    buttons = window.session_sidebar.findChildren(QPushButton, "SessionRowButton")
    assert len(buttons) == 1
    assert "真实问题" in buttons[0].text()
