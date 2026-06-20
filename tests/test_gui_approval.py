from __future__ import annotations

import asyncio
import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPlainTextEdit, QPushButton  # noqa: E402

from lucode.gui.approval import ApprovalDialog, ApprovalRequestContext  # noqa: E402


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


def test_approval_dialog_uses_english_labels_and_details(app):
    loop = asyncio.new_event_loop()
    try:
        future = loop.create_future()
        context = ApprovalRequestContext(
            prompt="是否批准这次工具调用？",
            tool_name="write_file",
            tool_rule="workspace-write",
            files_touched=[{"path": "README.md", "access": "write", "line_start": 3, "line_end": 5}],
            arguments_summary={"command": "edit"},
            risk={"level": "medium"},
        )

        dialog = ApprovalDialog(context, future, language="en")

        assert dialog.windowTitle() == "Approval required"
        assert dialog.findChild(QPushButton, "ApprovalAllowOnce").text() == "Allow once"
        assert dialog.findChild(QPushButton, "ApprovalAllowSession").text() == "Allow for session"
        assert dialog.findChild(QPushButton, "ApprovalReject").text() == "Reject"
        assert dialog.findChild(QPushButton, "ApprovalEditInstruction").text() == "Edit instruction"
        details = dialog.findChild(QPlainTextEdit, "ApprovalDetails").toPlainText()
        assert "Tool: write_file" in details
        assert "Files:" in details
        assert "Line: 3-5" in details
    finally:
        loop.close()
