from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from lucode.gui.theme import TOKENS


MAX_MESSAGE_CHARS = 20000


class MessageBubble(QFrame):
    def __init__(self, role: str, text: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.role = role
        self._text = ""
        self._truncated = False
        self.setObjectName("MessageBubble")
        self.setProperty("userRole", role == "user")
        self.setProperty("assistantRole", role != "user")
        self.style().unpolish(self)
        self.style().polish(self)
        self.setMaximumWidth(720)
        # wordWrap 的 QLabel 首选宽度会塌缩成窄条，用最小宽度兜住
        self.setMinimumWidth(240 if role == "user" else 360)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        self.role_label = QLabel("You" if role == "user" else "Lucode")
        self.role_label.setObjectName("RoleLabel")
        self.role_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.role_label)

        self.content_label = QLabel()
        self.content_label.setObjectName("UserText" if role == "user" else "AssistantText")
        self.content_label.setWordWrap(True)
        self.content_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.content_label)

        if text:
            self.set_text(text)

    def append_text(self, text: str) -> None:
        self.set_text(self._text + str(text or ""))

    def set_text(self, text: str) -> None:
        value = str(text or "")
        self._truncated = len(value) > MAX_MESSAGE_CHARS
        if self._truncated:
            value = value[:MAX_MESSAGE_CHARS] + "\n\n[Content truncated in M1 preview]"
        self._text = value
        self.content_label.setText(value)


def status_style(state: str) -> str:
    color = {
        "running": TOKENS["primary"],
        "stopped": TOKENS["warning"],
        "failed": TOKENS["danger"],
        "idle": TOKENS["text_muted"],
    }.get(state, TOKENS["text_muted"])
    return f"color: {color};"
