from __future__ import annotations

import asyncio
import time
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

from catalog_system.model_catalog import clear_model_catalog_cache
from lucode.gui.approval import GuiApprovalSession, LatestApprovalContext
from lucode.gui.chat_session import GuiChatSession
from lucode.gui.control_panel import ControlBar
from lucode.gui.event_bridge import EventBridge
from lucode.gui.provider_manager import ProviderManagerDialog
from lucode.gui.turn_state import TurnStateGuard
from lucode.gui.widgets import AnswerBlock, MessageBubble, ThinkingIndicator, WorkArea, status_style


WORKER_EVENTS = {"TaskStarted", "TaskCompleted", "TaskFailed", "ToolInvoked", "FastPathUsed"}
SUPERVISOR_EVENTS = {
    "PlanNormalized",
    "ExecutionContractApplied",
    "SupervisorObservation",
    "ParallelBatchStarted",
    "ParallelBatchSerialized",
    "LeadFinalizing",
    "LeadCompleted",
    "LeadReviewStarted",
    "LeadReviewCompleted",
}


class ChatInput(QPlainTextEdit):
    submit_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setPlaceholderText("??????????Shift+????")
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
        self.work_area: WorkArea | None = None
        self._work_area_row: QWidget | None = None
        self._thinking_row: QWidget | None = None
        self._thinking_indicator: ThinkingIndicator | None = None
        self._bubbles: list[MessageBubble] = []
        self._thinking_indicators: list[ThinkingIndicator] = []
        self.work_task: asyncio.Task | None = None
        self.work_task_id = 0
        self._turn_start: float = 0.0
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

        self.empty_state = QLabel("????????????????")
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

        self.send_button = QPushButton("??")
        self.send_button.setObjectName("SendButton")
        self.send_button.clicked.connect(self.send_current_message)
        composer_layout.addWidget(self.send_button)

        self.stop_button = QPushButton("??")
        self.stop_button.setObjectName("StopButton")
        self.stop_button.clicked.connect(self.stop_current_turn)
        self.stop_button.setEnabled(False)
        composer_layout.addWidget(self.stop_button)

        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.state_label = QLabel()
        self.event_label = QLabel("??")
        self.path_label = QLabel(str(self.workspace))
        self.status.addWidget(self.state_label)
        self.status.addWidget(self.event_label, 1)
        self.status.addPermanentWidget(self.path_label)
        self.set_status("idle", "??")

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
        self.control_bar.provider_manager_requested.connect(self._open_provider_manager)

    def _open_provider_manager(self) -> None:
        dialog = ProviderManagerDialog(
            workspace_root=self.chat_session.workspace_context.workspace_root,
            user_home=self.chat_session.workspace_context.user_home,
            privacy_mode=self.chat_session.settings.privacy_mode,
            parent=self,
        )
        dialog.providers_changed.connect(self._refresh_configured_models)
        dialog.exec()

    def _refresh_configured_models(self) -> None:
        clear_model_catalog_cache()
        self.control_bar.set_models(self.chat_session.list_configured_models())

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
        self.work_area = None
        self._work_area_row = None
        self._turn_start = time.monotonic()
        self._show_thinking("?????")
        self.set_running(True)
        self.set_status("running", "????")
        self.work_task_id = turn_id
        self.work_task = asyncio.create_task(self._run_turn(turn_id, text))

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
        bubble.set_available_width(self._chat_viewport_width())
        self._bubbles.append(bubble)
        if role == "user":
            row_layout.addStretch(1)
            row_layout.addWidget(bubble, 0, Qt.AlignRight | Qt.AlignTop)
        else:
            row_layout.addWidget(bubble, 0, Qt.AlignLeft | Qt.AlignTop)
            row_layout.addStretch(1)
        self.message_layout.addWidget(row)
        self._scroll_to_bottom()
        return bubble

    def _chat_viewport_width(self) -> int:
        return self.scroll_area.viewport().width()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        width = self._chat_viewport_width()
        for bubble in self._bubbles:
            bubble.set_available_width(width)
        for indicator in self._thinking_indicators:
            indicator.set_available_width(width)

    def ensure_work_area(self, payload: dict) -> WorkArea:
        self._clear_thinking()
        self._hide_empty_state()
        if self._work_area_row is not None:
            self.message_layout.removeWidget(self._work_area_row)
            self._work_area_row.deleteLater()
            self._work_area_row = None
        model_labels = dict(self.chat_session.list_configured_models())
        area = WorkArea(payload, model_labels=model_labels)
        self.work_area = area
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.addWidget(area, 1)
        self.message_layout.addWidget(row)
        self._work_area_row = row
        self._scroll_to_bottom()
        return area

    def add_answer_block(self, text: str) -> AnswerBlock:
        self._clear_thinking()
        self._hide_empty_state()
        block = AnswerBlock(text)
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.addWidget(block, 1)
        self.message_layout.addWidget(row)
        self._scroll_to_bottom()
        return block

    def _show_thinking(self, text: str) -> None:
        self._clear_thinking()
        self._hide_empty_state()
        indicator = ThinkingIndicator(text)
        indicator.set_available_width(self._chat_viewport_width())
        self._thinking_indicators.append(indicator)
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.addWidget(indicator, 0, Qt.AlignLeft | Qt.AlignTop)
        row_layout.addStretch(1)
        self.message_layout.addWidget(row)
        self._thinking_row = row
        self._thinking_indicator = indicator
        self._scroll_to_bottom()

    def _clear_thinking(self) -> None:
        if self._thinking_indicator is not None:
            self._thinking_indicator.stop()
            if self._thinking_indicator in self._thinking_indicators:
                self._thinking_indicators.remove(self._thinking_indicator)
        if self._thinking_row is not None:
            self.message_layout.removeWidget(self._thinking_row)
            self._thinking_row.deleteLater()
        self._thinking_row = None
        self._thinking_indicator = None

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
        self.set_status("stopped", "正在停止")

    def set_approval_waiting(self) -> None:
        self.send_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.input_box.setEnabled(False)
        self.control_bar.set_enabled(False)
        self.set_status("running", "等待审批")

    def set_status(self, state: str, event: str) -> None:
        labels = {
            "idle": "空闲",
            "running": "运行中",
            "stopped": "已停止",
            "failed": "失败",
        }
        self.state_label.setText(f"{labels.get(state, state)} · 模式 {self.mode}")
        self.state_label.setStyleSheet(status_style(state))
        self.event_label.setText(event)

    def handle_runtime_event(self, event: dict) -> None:
        if self._closing:
            return
        event_type = str(event.get("event_type") or "")
        task_id = str(event.get("task_id") or "")

        if event_type == "TurnStarted":
            self.set_status("running", "本轮开始")
            return
        if event_type == "PlanningStarted":
            if self._thinking_indicator is not None:
                self._thinking_indicator.set_base("主脑正在规划")
        if event_type == "PlanningCompleted":
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            if payload.get("tasks") or payload.get("route_type"):
                self.ensure_work_area(payload)

        if event_type == "AgentMessageDelta":
            if not self.turn_guard.is_running or self.turn_guard.is_stopping or not self.work_task_id:
                return
            if self.work_area is not None and task_id and self.work_area.has_task(task_id):
                self.work_area.apply_event(event)
                self._scroll_to_bottom()
            return

        if self.work_area is not None:
            if event_type in WORKER_EVENTS and task_id and self.work_area.has_task(task_id):
                self.work_area.apply_event(event)
                self._scroll_to_bottom()
            elif event_type in SUPERVISOR_EVENTS:
                self.work_area.set_supervisor_activity(_event_text(event))

        if event_type == "TurnEnded":
            self._clear_thinking()
            if self.work_area is not None:
                elapsed = max(0.0, time.monotonic() - self._turn_start) if self._turn_start else None
                self.work_area.collapse_done(elapsed)
            if self.turn_guard.is_running and not self.turn_guard.is_stopping:
                payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
                if str(payload.get("status") or "") == "failed":
                    self.set_status("failed", "本轮失败")
                else:
                    self.set_status("idle", "本轮完成")
            return
        if event_type == "ToolApprovalPre":
            self.approval_context.update_from_event(event)
            if not self.turn_guard.is_stopping:
                self.set_approval_waiting()
        if event_type == "ToolApprovalPost":
            if self.turn_guard.is_running and not self.turn_guard.is_stopping:
                self.set_running(True)
                self.set_status("running", "审批已处理")
        summary = _event_summary(event)
        if summary:
            self.event_label.setText(summary)

    async def _run_turn(self, turn_id: int, text: str) -> None:
        try:
            result = await self.chat_session.run_turn(text)
            if self._closing or not self.turn_guard.is_current(turn_id):
                return
            if result.final_output:
                self.add_answer_block(result.final_output)
            self.mode = result.execution_mode or self.mode
            if result.stopped:
                self.set_status("stopped", "已停止")
            elif result.failed:
                self.set_status("failed", "失败")
            else:
                self.set_status("idle", "本轮完成")
        finally:
            if not self._closing:
                self.event_bridge.flush()
                self._clear_thinking()
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
