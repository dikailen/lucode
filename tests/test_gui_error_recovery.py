from __future__ import annotations

import os
from dataclasses import dataclass

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QPushButton  # noqa: E402

from lucode.gui.chat_session import GuiTurnResult  # noqa: E402
from lucode.gui.main_window import MainWindow  # noqa: E402
from lucode.gui.widgets import AnswerBlock, ErrorRecoveryPanel  # noqa: E402


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


@dataclass
class FailingSession:
    workspace: object
    settings: object
    current_session_id: str = ""

    def __post_init__(self):
        self.workspace_context = type(
            "WorkspaceContext",
            (),
            {"workspace_root": self.workspace, "user_home": self.workspace},
        )()
        self.history_browser = object()
        self.calls = []

    def list_configured_models(self):
        return [("m1", "Model One")]

    def set_execution_mode(self, mode):
        self.settings.execution_mode = mode
        return mode

    def set_privacy_mode(self, mode):
        self.settings.privacy_mode = mode
        return mode

    def set_query_refiner_enabled(self, enabled):
        self.settings.query_refiner_enabled = bool(enabled)
        return bool(enabled)

    def set_model_for_role(self, role, model_id):
        return [model_id]

    def set_allowed_worker_models(self, model_ids):
        self.settings.allowed_worker_models = list(model_ids)
        return list(model_ids)

    async def run_turn(self, text):
        self.calls.append(text)
        return GuiTurnResult(
            final_output="provider request timed out",
            failed=True,
            execution_mode=self.settings.execution_mode,
        )


def _settings():
    return type(
        "Settings",
        (),
        {
            "execution_mode": "full",
            "privacy_mode": "local_first",
            "query_refiner_enabled": False,
            "allowed_worker_models": [],
            "query_refiner_model_priority": [],
            "orchestrator_model_priority": [],
            "executor_model_priority": [],
            "final_synthesizer_model_priority": [],
        },
    )()


def test_main_window_shows_failed_state_and_recovery_actions(app, tmp_path):
    session = FailingSession(workspace=tmp_path, settings=_settings())
    window = MainWindow(workspace=tmp_path, chat_session=session)

    window.show_failed_state("provider request timed out")
    app.processEvents()

    panel = window.findChild(ErrorRecoveryPanel, "ErrorRecoveryPanel")

    assert panel is not None
    assert "provider request timed out" in panel.findChild(QLabel, "RunFailedReason").text()
    assert window.send_button.isEnabled()
    assert not window.stop_button.isEnabled()
    assert window.input_box.isEnabled()
    assert window.findChild(QPushButton, "RunFailedRetryButton") is not None
    assert window.findChildren(AnswerBlock, "AnswerBlock") == []


def test_retry_prefills_last_failed_prompt_without_resending(app, tmp_path):
    session = FailingSession(workspace=tmp_path, settings=_settings())
    window = MainWindow(workspace=tmp_path, chat_session=session)

    window._last_failed_prompt = "fix loader"
    window.show_failed_state("provider request timed out")
    window.findChild(QPushButton, "RunFailedRetryButton").click()

    assert window.input_box.toPlainText() == "fix loader"
    assert session.calls == []
