from __future__ import annotations

import os
import importlib.util

import pytest

HAS_PYSIDE = importlib.util.find_spec("PySide6") is not None
pytestmark = pytest.mark.skipif(not HAS_PYSIDE, reason="PySide6 is not installed")

if HAS_PYSIDE:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication, QFrame, QLabel, QPushButton, QSplitter, QWidget  # noqa: E402

    from lucode.gui.chat_session import GuiChatSession  # noqa: E402
    from lucode.gui.main_window import MainWindow  # noqa: E402
    from lucode.gui.session_sidebar import SessionSidebar  # noqa: E402


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


def test_main_window_uses_splitter_with_sidebar_and_chat_pane(app, tmp_path):
    session = GuiChatSession(workspace=tmp_path)
    window = MainWindow(workspace=tmp_path, chat_session=session)
    window.show()
    app.processEvents()

    splitter = window.findChild(QSplitter, "MainSplitter")
    sidebar = window.findChild(SessionSidebar, "SessionSidebar")
    chat_pane = window.findChild(QWidget, "ChatPane")
    toolbar = window.findChild(QFrame, "ComposerToolbar")

    assert splitter is not None
    assert splitter.count() == 3
    assert sidebar is not None
    assert chat_pane is not None
    assert toolbar is not None
    assert window.session_title_label.text() == "新会话"
    assert window.scroll_area.parentWidget() is chat_pane
    assert window.input_box.parentWidget().parentWidget() is chat_pane


def test_sidebar_toggle_hides_and_restores_sidebar(app, tmp_path):
    session = GuiChatSession(workspace=tmp_path)
    window = MainWindow(workspace=tmp_path, chat_session=session)
    window.show()
    app.processEvents()

    sidebar = window.findChild(SessionSidebar, "SessionSidebar")
    toggle = window.findChild(QPushButton, "SidebarToggleButton")

    assert sidebar is not None
    assert toggle is not None
    assert sidebar.isVisible()

    toggle.click()
    app.processEvents()
    assert sidebar.isVisible()
    assert sidebar.property("collapsed") is True
    assert sidebar.maximumWidth() <= 76

    rail_toggle = window.findChild(QPushButton, "SidebarRailToggleButton")
    assert rail_toggle is not None
    assert rail_toggle.isVisible()

    rail_toggle.click()
    app.processEvents()
    assert sidebar.isVisible()
    assert sidebar.property("collapsed") is False
    assert sidebar.maximumWidth() == 294


def test_workbench_shell_matches_concept_geometry(app, tmp_path):
    session = GuiChatSession(workspace=tmp_path)
    window = MainWindow(workspace=tmp_path, chat_session=session)
    window.resize(1600, 1000)
    window.show()
    app.processEvents()

    splitter = window.findChild(QSplitter, "MainSplitter")
    sidebar = window.findChild(SessionSidebar, "SessionSidebar")
    chat_header = window.findChild(QFrame, "ChatHeader")
    status_chip = window.findChild(QLabel, "TopStatusChip")
    mode_host = window.findChild(QWidget, "TopModeHost")
    gear = window.findChild(QPushButton, "TopSettingsButton")

    assert splitter is not None
    assert sidebar is not None
    assert chat_header is not None
    assert status_chip is not None
    assert mode_host is not None
    assert gear is not None

    assert 286 <= sidebar.width() <= 304
    assert chat_header.height() >= 72
    assert window.status.isHidden()

    gear.click()
    app.processEvents()

    sizes = splitter.sizes()
    assert len(sizes) == 3
    assert 286 <= sizes[0] <= 304
    assert 420 <= sizes[2] <= 460
    assert window.settings_panel.isVisible()
