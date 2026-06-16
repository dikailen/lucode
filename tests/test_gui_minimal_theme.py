from __future__ import annotations

import os
import re

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QSizePolicy  # noqa: E402

from lucode.gui.theme import TOKENS, render_stylesheet  # noqa: E402
from lucode.gui.widgets import AnswerBlock, MessageBubble, ThinkingIndicator  # noqa: E402


def _qss_block(sheet: str, selector: str) -> str:
    match = re.search(rf"{re.escape(selector)}\s*\{{(?P<body>.*?)\n\}}", sheet, re.S)
    assert match is not None, selector
    return match.group("body")


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


def test_user_message_bubble_is_compact_and_has_no_role_label(app):
    bubble = MessageBubble("user", "短消息")

    assert not hasattr(bubble, "role_label")
    assert bubble.findChildren(QLabel, "RoleLabel") == []
    assert 72 <= bubble.width() <= 520
    assert bubble.minimumWidth() == 0
    assert bubble.sizePolicy().horizontalPolicy() == QSizePolicy.Maximum


def test_assistant_message_bubble_keeps_lucode_role_label(app):
    bubble = MessageBubble("assistant", "hello")

    labels = bubble.findChildren(QLabel, "RoleLabel")
    assert len(labels) == 1
    assert labels[0].text() == "Lucode"


def test_short_user_message_bubble_has_readable_minimum_width(app):
    app.setStyleSheet(render_stylesheet())
    bubble = MessageBubble("user", "hi")
    bubble.set_available_width(1000)
    bubble.show()
    app.processEvents()

    assert 72 <= bubble.width() <= 120


def test_short_cjk_user_message_stays_on_one_line_after_stylesheet(app):
    app.setStyleSheet(render_stylesheet())
    bubble = MessageBubble("user", "你好")
    bubble.set_available_width(1000)
    bubble.show()
    app.processEvents()

    required = bubble.content_label.fontMetrics().horizontalAdvance("你好")
    assert bubble.content_label.width() >= required


def test_long_user_message_bubble_caps_at_chat_width(app):
    bubble = MessageBubble("user", "long text " * 20)
    bubble.set_available_width(600)

    assert bubble.width() == int(600 * 0.62)
    assert bubble.content_label.maximumWidth() == bubble.width() - 28


def test_thinking_indicator_uses_readable_width(app):
    indicator = ThinkingIndicator("thinking")
    indicator.set_available_width(1000)

    assert indicator.width() >= 72
    assert indicator.maximumWidth() <= 520


def test_minimal_theme_keeps_user_bubble_neutral():
    sheet = render_stylesheet()
    user_block = _qss_block(sheet, 'QFrame#MessageBubble[userRole="true"]')

    assert "user_surface" not in sheet
    assert f"background: {TOKENS['user_surface']};" in user_block
    assert "border: none;" in user_block
    assert f"color: {TOKENS['text']};" in user_block
    assert f"color: {TOKENS['text']};" in sheet
    assert "QFrame#MessageBubble[userRole=\"true\"] QLabel#RoleLabel" not in sheet
    assert TOKENS["primary"] not in user_block


def test_answer_block_still_renders_markdown(app):
    block = AnswerBlock("**ok**")

    assert block.content_label.textFormat() == Qt.TextFormat.MarkdownText
