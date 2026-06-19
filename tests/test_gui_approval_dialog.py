from __future__ import annotations

import asyncio
import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QPushButton  # noqa: E402

from lucode.gui.approval import APPROVAL_DECISIONS, ApprovalDialog, ApprovalRequestContext  # noqa: E402


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


def _future() -> asyncio.Future:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    return loop.create_future()


def _context() -> ApprovalRequestContext:
    return ApprovalRequestContext(
        prompt="Approve this tool call?",
        tool_name="edit_file",
        files_touched=[{"path": "loader.py", "access": "write", "line_start": 1, "line_end": 12}],
        arguments_summary={"content": "def load_data():\n    return []\n"},
    )


def test_approval_dialog_uses_workbench_object_names_and_labels(app):
    future = _future()
    dialog = ApprovalDialog(_context(), future)

    assert dialog.objectName() == "ApprovalDialog"
    assert dialog.findChild(QLabel, "ApprovalTitle").text() == "Approval required"

    buttons = {
        button.objectName(): button.text()
        for button in dialog.findChildren(QPushButton)
        if button.objectName().startswith("Approval")
    }

    assert buttons["ApprovalAllowOnce"] == "Allow once"
    assert buttons["ApprovalAllowSession"] == "Allow session"
    assert buttons["ApprovalReject"] == "Reject"
    assert buttons["ApprovalEditInstruction"] == "Edit instruction"


def test_approval_dialog_close_defaults_to_safe_reject(app):
    future = _future()
    dialog = ApprovalDialog(_context(), future)

    dialog.close()
    app.processEvents()

    assert future.done()
    assert future.result() == APPROVAL_DECISIONS.deny
