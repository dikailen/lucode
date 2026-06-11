from __future__ import annotations

import asyncio
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QKeyEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from lucode.gui.approval import GuiApprovalSession, LatestApprovalContext
from lucode.gui.chat_session import GuiChatSession
from lucode.gui.control_panel import ControlBar
from lucode.gui.event_bridge import EventBridge
from lucode.gui.turn_state import TurnStateGuard
from lucode.gui.widgets import MessageBubble, status_style


class ChatInput(QPlainTextEdit):
    submit_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setPlaceholderText("Type a message")
        self.setFixedHeight(82)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in {Qt.Key_Return, Qt.Key_Enter} and not event.modifiers() & Qt.ShiftModifier:
            self.submit_requested.emit()
            return
        super().keyPressEvent(event)


class MainWindow(QMainWindow):
    def __init__(
        self,
        *,
        workspace: Path,
        mode: str = "",
        chat_session: GuiChatSession | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.workspace = Path(workspace).resolve()
        self.event_bridge = EventBridge(parent=self)
        self.event_bridge.event_received.connect(self.handle_runtime_event)
        self.approval_context = LatestApprovalContext()
        self.approval_session = GuiApprovalSession(parent=self, context_store=self.approval_context)
        self.chat_session = chat_session or GuiChatSession(
            workspace=self.workspace,
            mode=mode,
            event_bridge=self.event_bridge,
            approval_session=self.approval_session,
        )
        self.mode = str(mode or getattr(self.chat_session.settings, "execution_mode", "") or "settings")
        self.turn_guard = TurnStateGuard()
        self.current_assistant_bubble: MessageBubble | None = None
        self.work_task: asyncio.Task | None = None
        self.work_task_id = 0
        self.active_bubble_by_turn: dict[int, MessageBubble] = {}
        self._closing = False

        self.setWindowTitle("Lucode")
        self.resize(1040, 720)

        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(18, 18, 18, 10)
        root_layout.setSpacing(12)
        self.setCentralWidget(root)

        header = QLabel("Lucode")
        header.setObjectName("RoleLabel")
        root_layout.addWidget(header)

        self.control_bar = ControlBar()
        self._init_control_bar()
        root_layout.addWidget(self.control_bar)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        root_layout.addWidget(self.scroll_area, 1)

        self.message_host = QWidget()
        self.message_layout = QVBoxLayout(self.message_host)
        self.message_layout.setContentsMargins(0, 0, 0, 0)
        self.message_layout.setSpacing(10)
        self.scroll_area.setWidget(self.message_host)

        self.empty_state = QLabel("No messages yet. Send the first one to start.")
        self.empty_state.setObjectName("EmptyState")
        self.empty_state.setAlignment(Qt.AlignCenter)
        self.message_layout.addWidget(self.empty_state, 1)

        composer = QFrame()
        composer_layout = QHBoxLayout(composer)
        composer_layout.setContentsMargins(0, 0, 0, 0)
        composer_layout.setSpacing(8)
        root_layout.addWidget(composer)

        self.input_box = ChatInput(composer)
        self.input_box.submit_requested.connect(self.send_current_message)
        composer_layout.addWidget(self.input_box, 1)

        self.send_button = QPushButton("Send")
        self.send_button.setObjectName("SendButton")
        self.send_button.clicked.connect(self.send_current_message)
        composer_layout.addWidget(self.send_button)

        self.stop_button = QPushButton("Stop")
        self.stop_button.setObjectName("StopButton")
        self.stop_button.clicked.connect(self.stop_current_turn)
        self.stop_button.setEnabled(False)
        composer_layout.addWidget(self.stop_button)

        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.state_label = QLabel()
        self.event_label = QLabel("Ready")
        self.path_label = QLabel(str(self.workspace))
        self.status.addWidget(self.state_label)
        self.status.addWidget(self.event_label, 1)
        self.status.addPermanentWidget(self.path_label)
        self.set_status("idle", "Ready")

    def _init_control_bar(self) -> None:
        self.control_bar.set_models(self.chat_session.list_configured_models())
        settings = self.chat_session.settings
        role_models = {
            "query_refiner": _first_or_empty(settings.query_refiner_model_priority),
            "orchestrator": _first_or_empty(settings.orchestrator_model_priority),
            "executor": _first_or_empty(settings.executor_model_priority),
            "final_synthesizer": _first_or_empty(settings.final_synthesizer_model_priority),
        }
        self.control_bar.set_initial(
            execution_mode=settings.execution_mode,
            privacy_mode=settings.privacy_mode,
            role_models=role_models,
            query_refiner_enabled=bool(settings.query_refiner_enabled),
            worker_pool=list(getattr(settings, "allowed_worker_models", []) or []),
        )
        self.control_bar.execution_mode_changed.connect(self._on_execution_mode_changed)
        self.control_bar.privacy_mode_changed.connect(self.chat_session.set_privacy_mode)
        self.control_bar.role_model_changed.connect(self.chat_session.set_model_for_role)
        self.control_bar.query_refiner_toggled.connect(self.chat_session.set_query_refiner_enabled)
        self.control_bar.worker_pool_changed.connect(self.chat_session.set_allowed_worker_models)

    def _on_execution_mode_changed(self, mode: str) -> None:
        self.mode = self.chat_session.set_execution_mode(mode)
        self.set_status("idle" if self.turn_guard.can_start_new_turn else "running", self.event_label.text())

    def send_current_message(self) -> None:
        if self._closing:
            return
        text = self.input_box.toPlainText().strip()
        if not text or not self.turn_guard.can_start_new_turn:
            return
        self.input_box.clear()
        self.add_message("user", text)
        turn_id = self.turn_guard.start()
        bubble = self.add_message("assistant", "")
        self.current_assistant_bubble = bubble
        self.active_bubble_by_turn[turn_id] = bubble
        self.set_running(True)
        self.set_status("running", "Starting turn")
        self.work_task_id = turn_id
        self.work_task = asyncio.create_task(self._run_turn(turn_id, text, bubble))

    def stop_current_turn(self) -> None:
        if self._closing:
            return
        if not self.turn_guard.is_running:
            return
        self.turn_guard.request_stop(self.work_task_id)
        self.approval_session.cancel_pending()
        if self.work_task is not None and not self.work_task.done():
            self.work_task.cancel()
        self.set_stopping()

    def add_message(self, role: str, text: str) -> MessageBubble:
        self._hide_empty_state()
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        bubble = MessageBubble(role, text)
        if role == "user":
            row_layout.addStretch(1)
            row_layout.addWidget(bubble)
        else:
            row_layout.addWidget(bubble)
            row_layout.addStretch(1)
        self.message_layout.addWidget(row)
        self._scroll_to_bottom()
        return bubble

    def set_running(self, running: bool) -> None:
        self.send_button.setEnabled(not running)
        self.stop_button.setEnabled(running)
        self.input_box.setEnabled(not running)
        self.control_bar.set_enabled(not running)

    def set_stopping(self) -> None:
        self.send_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        self.input_box.setEnabled(False)
        self.control_bar.set_enabled(False)
        self.set_status("stopped", "Stopping")

    def set_approval_waiting(self) -> None:
        self.send_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.input_box.setEnabled(False)
        self.control_bar.set_enabled(False)
        self.set_status("running", "Waiting for approval")

    def set_status(self, state: str, event: str) -> None:
        labels = {
            "idle": "Idle",
            "running": "Running",
            "stopped": "Stopped",
            "failed": "Failed",
        }
        self.state_label.setText(f"{labels.get(state, state)} | mode {self.mode}")
        self.state_label.setStyleSheet(status_style(state))
        self.event_label.setText(event)

    def handle_runtime_event(self, event: dict) -> None:
        if self._closing:
            return
        event_type = str(event.get("event_type") or "")
        if event_type == "AgentMessageDelta":
            if not self.turn_guard.is_running or self.turn_guard.is_stopping or not self.work_task_id:
                return
            text = _event_text(event)
            if text:
                bubble = self.active_bubble_by_turn.get(self.work_task_id)
                if bubble is None:
                    bubble = self.current_assistant_bubble or self.add_message("assistant", "")
                    self.current_assistant_bubble = bubble
                    self.active_bubble_by_turn[self.work_task_id] = bubble
                bubble.append_text(text)
                self._scroll_to_bottom()
            return
        if event_type == "TurnStarted":
            self.set_status("running", "Turn started")
            return
        if event_type == "TurnEnded":
            if self.turn_guard.is_running and not self.turn_guard.is_stopping:
                payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
                if str(payload.get("status") or "") == "failed":
                    self.set_status("failed", "Turn failed")
                else:
                    self.set_status("idle", "Turn completed")
            return
        if event_type == "ToolApprovalPre":
            self.approval_context.update_from_event(event)
            if not self.turn_guard.is_stopping:
                self.set_approval_waiting()
        summary = _event_summary(event)
        if summary:
            self.event_label.setText(summary)

    async def _run_turn(self, turn_id: int, text: str, bubble: MessageBubble) -> None:
        try:
            result = await self.chat_session.run_turn(text)
            if self._closing or not self.turn_guard.is_current(turn_id):
                return
            if result.final_output:
                bubble.set_text(result.final_output)
            self.mode = result.execution_mode or self.mode
            if result.stopped:
                self.set_status("stopped", "Stopped")
            elif result.failed:
                self.set_status("failed", "Failed")
            else:
                self.set_status("idle", "Turn completed")
        finally:
            if not self._closing:
                self.event_bridge.flush()
            self.active_bubble_by_turn.pop(turn_id, None)
            if self.turn_guard.finish_if_current(turn_id):
                self.work_task = None
                self.work_task_id = 0
                if not self._closing:
                    self.set_running(False)

    def _hide_empty_state(self) -> None:
        if self.empty_state.isVisible():
            self.empty_state.hide()

    def _scroll_to_bottom(self) -> None:
        QTimer.singleShot(0, self._scroll_now)

    def _scroll_now(self) -> None:
        bar = self.scroll_area.verticalScrollBar()
        bar.setValue(bar.maximum())

    def closeEvent(self, event: QCloseEvent) -> None:
        self._closing = True
        self.event_bridge.blockSignals(True)
        if self.work_task is not None and not self.work_task.done():
            self.turn_guard.request_stop(self.work_task_id)
            self.approval_session.cancel_pending()
            self.work_task.cancel()
        super().closeEvent(event)


def _first_or_empty(values) -> str:
    for item in values or []:
        text = str(item or "").strip()
        if text:
            return text
    return ""


def _event_text(event: dict) -> str:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    return str(event.get("text") or payload.get("text") or event.get("message") or "")


def _event_summary(event: dict) -> str:
    event_type = str(event.get("event_type") or "")
    message = str(event.get("message") or "").strip()
    if event_type in {"PlanningStarted", "PlanningCompleted", "PlanningFailed"}:
        return message or event_type
    if event_type in {"ToolInvoked", "ToolApprovalPre", "ToolApprovalPost"}:
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        tool = str(payload.get("tool_name") or payload.get("tool") or "").strip()
        return f"{event_type}: {tool or message}".strip()
    return message
