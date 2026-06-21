from __future__ import annotations

import importlib.util
import os

import pytest

HAS_PYSIDE = importlib.util.find_spec("PySide6") is not None
pytestmark = pytest.mark.skipif(not HAS_PYSIDE, reason="PySide6 is not installed")

if HAS_PYSIDE:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication, QComboBox, QFrame, QLabel, QPushButton, QSplitter, QStackedWidget  # noqa: E402

    from lucode.gui.chat_session import GuiChatSession  # noqa: E402
    from lucode.gui.control_panel import ControlBar  # noqa: E402
    from lucode.gui.main_window import MainWindow  # noqa: E402
    from lucode.gui.settings_panel import SettingsSidePanel  # noqa: E402
    from lucode.gui.settings_dialog import SettingsDialog  # noqa: E402


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


def test_control_bar_is_mode_toolbar_only(app):
    bar = ControlBar()

    assert bar.findChild(QComboBox, "PrivacyModeCombo") is None
    assert bar.findChild(QPushButton, "QueryRefinerToggle") is None
    assert bar.findChild(QPushButton, "ProviderManagerButton") is None
    assert bar.findChild(QPushButton, "SettingsButton") is not None
    assert bar.findChild(QComboBox) is None


def test_settings_dialog_contains_migrated_controls(app, tmp_path):
    session = GuiChatSession(workspace=tmp_path)
    dialog = SettingsDialog(parent=None)
    dialog.set_models(session.list_configured_models())
    dialog.set_initial(
        execution_mode="full",
        privacy_mode="offline",
        role_models={
            "query_refiner": "",
            "orchestrator": "",
            "executor": "",
            "final_synthesizer": "",
        },
        query_refiner_enabled=True,
        worker_pool=[],
    )

    assert dialog.objectName() == "SettingsDialog"
    assert dialog.findChild(QComboBox, "PrivacyModeCombo") is not None
    assert dialog.findChild(QPushButton, "QueryRefinerToggle") is not None
    assert dialog.findChild(QPushButton, "ProviderManagerButton") is not None


def test_settings_dialog_has_workbench_tabs(app):
    dialog = SettingsDialog(parent=None)
    dialog.show()
    app.processEvents()

    nav = dialog.findChild(QFrame, "SettingsNav")
    assert nav is not None
    assert nav.maximumHeight() <= 56
    assert dialog.findChild(QStackedWidget, "SettingsContentStack") is not None
    for tab_name in ("Models", "Privacy", "Providers", "Shortcuts", "About"):
        tab = dialog.findChild(QPushButton, f"SettingsTab{tab_name}")
        page = dialog.findChild(QLabel, f"SettingsPageTitle{tab_name}")
        assert tab is not None
        assert tab.parentWidget() is nav
        assert tab.property("tabBar") is True
        assert page is not None


def test_settings_dialog_defaults_to_chinese_and_exposes_language_choice(app):
    dialog = SettingsDialog(parent=None)

    assert dialog.findChild(QPushButton, "SettingsTabModels").text() == "模型"
    assert dialog.findChild(QPushButton, "SettingsTabLanguage") is not None

    language_tab = dialog.findChild(QPushButton, "SettingsTabLanguage")
    language_tab.click()
    app.processEvents()

    assert dialog.findChild(QLabel, "SettingsPageTitleLanguage").text() == "语言"
    assert dialog.findChild(QPushButton, "LanguageZhButton").text() == "中文"
    assert dialog.findChild(QPushButton, "LanguageEnButton").text() == "英文"
    assert dialog.findChild(QPushButton, "LanguageZhButton").isChecked()

    changed = []
    dialog.language_changed.connect(lambda value: changed.append(value))
    dialog.findChild(QPushButton, "LanguageEnButton").click()
    app.processEvents()

    assert dialog.current_language() == "en"
    assert changed[-1] == "en"


def test_settings_dialog_switches_visible_text_between_languages(app):
    dialog = SettingsDialog(parent=None)

    assert dialog.findChild(QPushButton, "SettingsTabModels").text() == "模型"
    assert dialog.findChild(QPushButton, "SettingsTabLanguage").text() == "语言"
    assert dialog.findChild(QPushButton, "LanguageEnButton").text() == "英文"

    dialog.findChild(QPushButton, "LanguageEnButton").click()
    app.processEvents()

    assert dialog.current_language() == "en"
    assert dialog.windowTitle() == "Settings"
    assert dialog.findChild(QPushButton, "SettingsTabModels").text() == "Models"
    assert dialog.findChild(QPushButton, "SettingsTabLanguage").text() == "Language"
    assert dialog.findChild(QLabel, "SettingsPageTitleLanguage").text() == "Language"
    assert dialog.findChild(QPushButton, "LanguageZhButton").text() == "Chinese"
    assert dialog.findChild(QPushButton, "LanguageEnButton").text() == "English"

    dialog.findChild(QPushButton, "LanguageZhButton").click()
    app.processEvents()

    assert dialog.current_language() == "zh"
    assert dialog.windowTitle() == "设置"
    assert dialog.findChild(QPushButton, "SettingsTabModels").text() == "模型"
    assert dialog.findChild(QPushButton, "LanguageEnButton").text() == "英文"


def test_settings_dialog_provider_page_exposes_manager_and_custom_provider(app):
    dialog = SettingsDialog(parent=None)
    emitted = []
    dialog.provider_manager_requested.connect(lambda: emitted.append("manager"))
    dialog.custom_provider_requested.connect(lambda: emitted.append("custom"))

    provider_tab = dialog.findChild(QPushButton, "SettingsTabProviders")
    provider_tab.click()
    app.processEvents()

    manager_button = dialog.findChild(QPushButton, "ProviderManagerButton")
    custom_button = dialog.findChild(QPushButton, "CustomProviderButton")

    assert manager_button is not None
    assert custom_button is not None

    manager_button.click()
    custom_button.click()

    assert emitted == ["manager", "custom"]


def test_settings_dialog_privacy_page_keeps_offline_control_and_hint(app):
    dialog = SettingsDialog(parent=None)
    dialog.set_initial(
        execution_mode="solo",
        privacy_mode="offline",
        role_models={"executor": ""},
        query_refiner_enabled=False,
        worker_pool=[],
    )

    privacy_tab = dialog.findChild(QPushButton, "SettingsTabPrivacy")
    privacy_tab.click()
    app.processEvents()

    privacy_combo = dialog.findChild(QComboBox, "PrivacyModeCombo")
    hint = dialog.findChild(QLabel, "PrivacyModeHint")

    assert privacy_combo.currentData() == "offline"
    assert hint is not None
    assert "offline" in hint.text().lower() or "离线" in hint.text()


def test_settings_dialog_signals_still_update_chat_session(app, tmp_path):
    session = GuiChatSession(workspace=tmp_path)
    dialog = SettingsDialog(parent=None)
    dialog.set_models([("m1", "Model One"), ("m2", "Model Two")])
    dialog.set_initial(
        execution_mode="full",
        privacy_mode="local_first",
        role_models={
            "query_refiner": "",
            "orchestrator": "m1",
            "executor": "m1",
            "final_synthesizer": "m1",
        },
        query_refiner_enabled=False,
        worker_pool=["m1"],
    )
    dialog.privacy_mode_changed.connect(session.set_privacy_mode)
    dialog.role_model_changed.connect(session.set_model_for_role)
    dialog.query_refiner_toggled.connect(session.set_query_refiner_enabled)
    dialog.worker_pool_changed.connect(session.set_allowed_worker_models)

    privacy = dialog.findChild(QComboBox, "PrivacyModeCombo")
    assert privacy is not None
    privacy.setCurrentIndex(privacy.findData("offline"))

    assert session.settings.privacy_mode == "offline"


def test_main_window_settings_button_opens_embedded_panel(app, tmp_path):
    session = GuiChatSession(workspace=tmp_path)
    window = MainWindow(workspace=tmp_path, chat_session=session)
    window.show()
    app.processEvents()
    button = window.control_bar.findChild(QPushButton, "SettingsButton")
    splitter = window.findChild(QSplitter, "MainSplitter")
    settings_host = window.findChild(QFrame, "SettingsPanelHost")

    assert button is not None
    assert splitter is not None
    assert settings_host is not None
    assert isinstance(window.settings_panel, SettingsSidePanel)
    assert not window.settings_panel.isVisible()
    assert splitter.sizes()[2] == 0

    button.click()
    app.processEvents()

    assert settings_host.isVisible()
    assert window.settings_panel.isVisible()
    assert window.settings_panel.current_view() == "settings"
    assert window.settings_panel.parentWidget() is settings_host
    assert window.findChild(SettingsDialog, "SettingsDialog") is None

    window.settings_panel.close_button.click()
    app.processEvents()

    assert not settings_host.isVisible()
    assert not window.settings_panel.isVisible()
    assert splitter.sizes()[2] == 0


def test_main_window_provider_manager_stays_inside_settings_panel(app, tmp_path):
    session = GuiChatSession(workspace=tmp_path)
    window = MainWindow(workspace=tmp_path, chat_session=session)
    window.show()
    app.processEvents()

    window._open_settings_dialog()
    window.settings_dialog.select_page("Providers")
    window.settings_dialog.provider_manager_button.click()
    app.processEvents()

    assert window.settings_panel.isVisible()
    assert window.settings_panel.current_view() == "providers"
    assert window.settings_panel.provider_content.isVisible()

    window.settings_panel.back_button.click()
    app.processEvents()

    assert window.settings_panel.current_view() == "settings"


def test_main_window_language_switch_refreshes_and_persists(app, tmp_path):
    session = GuiChatSession(workspace=tmp_path)
    window = MainWindow(workspace=tmp_path, chat_session=session)

    window.settings_dialog.findChild(QPushButton, "LanguageEnButton").click()
    app.processEvents()

    assert window.settings_dialog.current_language() == "en"
    assert window.input_box.placeholderText() == "Type a message, Enter to send, Shift+Enter for newline"
    assert window.send_button.text() == "Send"
    assert window.stop_button.text() == "Stop"
    assert window.session_sidebar.new_session_button.text() == "+ New chat"
    assert window.session_sidebar.findChild(QPushButton, "SidebarTabSkills").text() == "Skills"

    restored = MainWindow(workspace=tmp_path, chat_session=GuiChatSession(workspace=tmp_path))

    assert restored.settings_dialog.current_language() == "en"
    assert restored.send_button.text() == "Send"
    assert restored.control_bar.summary_label.text().startswith("Mode ")
    assert restored.control_bar.settings_button.toolTip() == "Settings"
