from __future__ import annotations

import importlib.util
import os

import pytest

HAS_PYSIDE = importlib.util.find_spec("PySide6") is not None
pytestmark = pytest.mark.skipif(not HAS_PYSIDE, reason="PySide6 is not installed")

if HAS_PYSIDE:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication, QComboBox, QPushButton  # noqa: E402

    from lucode.gui.chat_session import GuiChatSession  # noqa: E402
    from lucode.gui.control_panel import ControlBar  # noqa: E402
    from lucode.gui.main_window import MainWindow  # noqa: E402
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


def test_main_window_settings_button_opens_dialog(app, tmp_path):
    session = GuiChatSession(workspace=tmp_path)
    window = MainWindow(workspace=tmp_path, chat_session=session)
    button = window.control_bar.findChild(QPushButton, "SettingsButton")

    assert button is not None
    button.click()
    app.processEvents()

    dialog = window.findChild(SettingsDialog, "SettingsDialog")
    assert dialog is not None
    assert dialog.isVisible()
    dialog.close()
