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
    QSplitter,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from catalog_system.model_catalog import clear_model_catalog_cache
from lucode.gui.approval import GuiApprovalSession, LatestApprovalContext
from lucode.gui.answer_stream import AnswerStreamState
from lucode.gui.chat_session import GuiChatSession
from lucode.gui.control_panel import ControlBar, compact_execution_mode_label, execution_mode_label
from lucode.gui.event_bridge import EventBridge
from lucode.gui.i18n import Translator, load_gui_language, save_gui_language
from lucode.gui.session_sidebar import SessionSidebar
from lucode.gui.settings_dialog import SettingsContent
from lucode.gui.settings_panel import SettingsSidePanel
from lucode.gui.stream_routing import classify_gui_stream_event
from lucode.gui.turn_state import TurnStateGuard
from lucode.gui.widgets import ErrorRecoveryPanel, AnswerBlock, MessageBubble, ThinkingIndicator, WorkArea, status_style


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
        self._language = 'zh'
        self._t = Translator(self._language)
        self.setPlaceholderText(self._t('main.input.placeholder'))
        self.setFixedHeight(56)

    def set_language(self, language: str) -> None:
        self._language = language
        self._t = Translator(language)
        self.setPlaceholderText(self._t('main.input.placeholder'))

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
        self.language = load_gui_language(workspace_root=self.workspace)
        self._t = Translator(self.language)
        self.approval_session.language = self.language
        self.mode = str(mode or getattr(self.chat_session.settings, "execution_mode", "") or "settings")
        self.turn_guard = TurnStateGuard()
        self.work_area: WorkArea | None = None
        self._work_area_row: QWidget | None = None
        self._thinking_row: QWidget | None = None
        self._thinking_indicator: ThinkingIndicator | None = None
        self._stream_answer_block: AnswerBlock | None = None
        self._answer_stream = AnswerStreamState()
        self._bubbles: list[MessageBubble] = []
        self._thinking_indicators: list[ThinkingIndicator] = []
        self._error_panel_row: QWidget | None = None
        self._error_panel: ErrorRecoveryPanel | None = None
        self._last_failed_prompt = ""
        self._status_state = "idle"
        self._status_event_key = "main.ready"
        self.work_task: asyncio.Task | None = None
        self.work_task_id = 0
        self._turn_start: float = 0.0
        self._closing = False

        self.setWindowTitle("Lucode")
        self.resize(1600, 1000)

        self.main_splitter = QSplitter(Qt.Horizontal)
        self.main_splitter.setObjectName("MainSplitter")
        self.setCentralWidget(self.main_splitter)

        self.session_sidebar = SessionSidebar()
        self.session_sidebar.set_session_store(self.chat_session.history_browser)
        self.session_sidebar.new_session_requested.connect(self._start_new_session)
        self.session_sidebar.session_selected.connect(self._resume_selected_session)
        self.session_sidebar.session_deleted.connect(self._on_sidebar_session_deleted)
        self.session_sidebar.settings_requested.connect(self._open_settings_dialog)
        self.session_sidebar.collapse_requested.connect(self._toggle_session_sidebar)
        self.sidebar_toggle_button = self.session_sidebar.sidebar_toggle_button
        self.main_splitter.addWidget(self.session_sidebar)

        chat_pane = QWidget()
        chat_pane.setObjectName("ChatPane")
        root_layout = QVBoxLayout(chat_pane)
        root_layout.setContentsMargins(0, 0, 0, 16)
        root_layout.setSpacing(0)
        self.main_splitter.addWidget(chat_pane)
        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.setSizes([294, 866])

        header = QFrame()
        header.setObjectName("ChatHeader")
        header.setFixedHeight(66)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(24, 0, 18, 0)
        header_layout.setSpacing(12)

        self.session_title_label = QLabel(self._t('main.new_chat'))
        self.session_title_label.setObjectName("SessionTitleLabel")
        header_layout.addWidget(self.session_title_label, 1)

        self.top_status_chip = QLabel()
        self.top_status_chip.setObjectName("TopStatusChip")
        header_layout.addWidget(self.top_status_chip)

        self.top_mode_host = QWidget(header)
        self.top_mode_host.setObjectName("TopModeHost")
        self.top_mode_host.setMaximumWidth(90)
        self.top_mode_host.setMaximumHeight(36)
        mode_layout = QHBoxLayout(self.top_mode_host)
        mode_layout.setContentsMargins(0, 0, 0, 0)
        mode_layout.setSpacing(0)
        self.top_mode_chip = QLabel()
        self.top_mode_chip.setObjectName("TopModeChip")
        self.top_mode_chip.setMaximumWidth(82)
        self.top_mode_chip.setMaximumHeight(34)
        self.top_mode_chip.setAlignment(Qt.AlignCenter)
        mode_layout.addWidget(self.top_mode_chip)
        header_layout.addWidget(self.top_mode_host)

        self.top_settings_button = QPushButton("⚙")
        self.top_settings_button.setObjectName("TopSettingsButton")
        self.top_settings_button.setToolTip(self._t('control.settings_tip'))
        self.top_settings_button.clicked.connect(self._open_settings_dialog)
        header_layout.addWidget(self.top_settings_button)
        root_layout.addWidget(header)

        self.control_bar = ControlBar(language=self.language)
        self.settings_dialog = SettingsContent(parent=self, show_footer=False)
        self.settings_panel_host = QFrame()
        self.settings_panel_host.setObjectName("SettingsPanelHost")
        self.settings_panel_host.setMinimumWidth(0)
        self.settings_panel_host.setMaximumWidth(460)
        settings_panel_host_layout = QHBoxLayout(self.settings_panel_host)
        settings_panel_host_layout.setContentsMargins(8, 14, 16, 14)
        settings_panel_host_layout.setSpacing(0)

        self.settings_panel = SettingsSidePanel(
            settings_content=self.settings_dialog,
            workspace_root=self.chat_session.workspace_context.workspace_root,
            user_home=self.chat_session.workspace_context.user_home,
            privacy_mode=self.chat_session.settings.privacy_mode,
            language=self.language,
            parent=self.settings_panel_host,
        )
        self.settings_panel.providers_changed.connect(self._refresh_configured_models)
        settings_panel_host_layout.addWidget(self.settings_panel)
        self.main_splitter.addWidget(self.settings_panel_host)
        self.main_splitter.setStretchFactor(2, 0)
        self.main_splitter.setSizes([294, 866, 0])
        self._init_control_bar()

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        root_layout.addWidget(self.scroll_area, 1)

        self.message_host = QWidget()
        self.message_host.setObjectName("MessageCanvas")
        self.message_layout = QVBoxLayout(self.message_host)
        self.message_layout.setContentsMargins(24, 20, 24, 20)
        self.message_layout.setSpacing(16)
        self.scroll_area.setWidget(self.message_host)

        self.empty_state = QLabel(self._t('main.empty'))
        self.empty_state.setObjectName("EmptyState")
        self.empty_state.setAlignment(Qt.AlignCenter)
        self.message_layout.addWidget(self.empty_state, 1)

        composer_host = QWidget()
        composer_host.setObjectName("ComposerHost")
        composer_host_layout = QHBoxLayout(composer_host)
        composer_host_layout.setContentsMargins(18, 0, 18, 0)
        composer_host_layout.setSpacing(0)
        root_layout.addWidget(composer_host)

        composer = QFrame(composer_host)
        composer.setObjectName("ComposerShell")
        composer.setMinimumHeight(122)
        composer.setMaximumHeight(128)
        composer_layout = QVBoxLayout(composer)
        composer_layout.setContentsMargins(14, 10, 14, 10)
        composer_layout.setSpacing(6)
        composer_host_layout.addWidget(composer)

        input_row = QFrame(composer)
        input_row.setObjectName("ComposerInputRow")
        input_row_layout = QHBoxLayout(input_row)
        input_row_layout.setContentsMargins(0, 0, 0, 0)
        input_row_layout.setSpacing(0)
        composer_layout.addWidget(input_row, 1)

        self.input_box = ChatInput(input_row)
        self.input_box.submit_requested.connect(self.send_current_message)
        input_row_layout.addWidget(self.input_box, 1)

        toolbar = QFrame(composer)
        toolbar.setObjectName("ComposerToolbar")
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(0, 0, 0, 0)
        toolbar_layout.setSpacing(8)
        toolbar_layout.addWidget(self.control_bar)
        toolbar_layout.addStretch(1)

        self.composer_tool_buttons: list[QPushButton] = []
        for text, name in (("+", "Attach"), ("F", "Files"), ("</>", "Code"), (">_", "Terminal")):
            button = QPushButton(text, toolbar)
            button.setObjectName("ComposerToolButton")
            button.setProperty("toolRole", name)
            self.composer_tool_buttons.append(button)
            toolbar_layout.addWidget(button)

        self.send_button = QPushButton(self._t('main.send'), toolbar)
        self.send_button.setObjectName("SendButton")
        self.send_button.clicked.connect(self.send_current_message)
        toolbar_layout.addWidget(self.send_button)

        self.stop_button = QPushButton(self._t('main.stop'), toolbar)
        self.stop_button.setObjectName("StopButton")
        self.stop_button.clicked.connect(self.stop_current_turn)
        self.stop_button.setEnabled(False)
        toolbar_layout.addWidget(self.stop_button)
        composer_layout.addWidget(toolbar)

        self.status = QStatusBar()
        self.status.hide()
        self.setStatusBar(self.status)
        self.state_label = QLabel()
        self.event_label = QLabel(self._t('main.ready'))
        self.path_label = QLabel(str(self.workspace))
        self.status.addWidget(self.state_label)
        self.status.addWidget(self.event_label, 1)
        self.status.addPermanentWidget(self.path_label)
        self.set_status_i18n("idle", "main.ready")
        self.session_sidebar.set_language(self.language)
        self.settings_dialog.set_language(self.language)
        self.settings_panel.set_language(self.language)
        self.input_box.set_language(self.language)
        self.control_bar.set_language(self.language)
        self._refresh_top_mode_buttons()
        self.session_sidebar.refresh()

    def _init_control_bar(self) -> None:
        models = self.chat_session.list_configured_models()
        self.control_bar.set_models(models)
        self.settings_dialog.set_models(models)
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
        self._sync_top_mode_buttons(settings.execution_mode)
        self.settings_dialog.set_initial(
            execution_mode=settings.execution_mode,
            privacy_mode=settings.privacy_mode,
            role_models=role_models,
            query_refiner_enabled=bool(settings.query_refiner_enabled),
            worker_pool=list(getattr(settings, "allowed_worker_models", []) or []),
        )
        self.control_bar.execution_mode_changed.connect(self._on_execution_mode_changed)
        self.control_bar.settings_requested.connect(self._open_settings_dialog)
        self.settings_dialog.privacy_mode_changed.connect(self.chat_session.set_privacy_mode)
        self.settings_dialog.role_model_changed.connect(self.chat_session.set_model_for_role)
        self.settings_dialog.query_refiner_toggled.connect(self.chat_session.set_query_refiner_enabled)
        self.settings_dialog.worker_pool_changed.connect(self.chat_session.set_allowed_worker_models)
        self.settings_dialog.provider_manager_requested.connect(self._open_provider_manager)
        self.settings_dialog.custom_provider_requested.connect(self._open_custom_provider_manager)
        self.settings_dialog.language_changed.connect(self._on_language_changed)


    def _sync_top_mode_buttons(self, mode: str) -> None:
        normalized = str(mode or self.mode or "").strip()
        compact_label = compact_execution_mode_label(normalized, self.language)
        full_label = execution_mode_label(normalized, self.language)
        self.top_mode_chip.setText(compact_label)
        self.top_mode_chip.setProperty("mode_id", normalized)
        self.top_mode_chip.setToolTip(f"{self._t('main.status.mode')}: {full_label}")

    def _refresh_top_mode_buttons(self) -> None:
        self.top_settings_button.setToolTip(self._t('control.settings_tip'))
        self._sync_top_mode_buttons(self.mode)

    def _on_language_changed(self, language: str) -> None:
        self.language = language
        self._t = Translator(language)
        save_gui_language(language, workspace_root=self.workspace)
        self.input_box.set_language(language)
        self.session_sidebar.set_language(language)
        self.settings_dialog.set_language(language)
        self.settings_panel.set_language(language)
        self.control_bar.set_language(language)
        self._refresh_top_mode_buttons()
        self.approval_session.language = language
        self.send_button.setText(self._t('main.send'))
        self.stop_button.setText(self._t('main.stop'))
        if self.session_title_label.text() in {'新会话', 'New chat'}:
            self.session_title_label.setText(self._t('main.new_chat'))
        self.empty_state.setText(self._t('main.empty'))
        self.set_status_i18n(self._status_state, self._status_event_key)

    def _open_settings_dialog(self) -> None:
        self.settings_panel.show_settings()

    def _open_provider_manager(self) -> None:
        self.settings_panel.set_privacy_mode(self.chat_session.settings.privacy_mode)
        self.settings_panel.show_provider_manager()

    def _open_custom_provider_manager(self) -> None:
        self.settings_panel.set_privacy_mode(self.chat_session.settings.privacy_mode)
        self.settings_panel.show_provider_manager(custom=True)

    def _refresh_configured_models(self) -> None:
        clear_model_catalog_cache()
        models = self.chat_session.list_configured_models()
        self.control_bar.set_models(models)
        self.settings_dialog.set_models(models)

    def _on_execution_mode_changed(self, mode: str) -> None:
        self.mode = self.chat_session.set_execution_mode(mode)
        self.settings_dialog.set_execution_mode(self.mode)
        self.control_bar.set_initial(
            execution_mode=self.mode,
            privacy_mode=self.chat_session.settings.privacy_mode,
            role_models={},
        )
        self._sync_top_mode_buttons(self.mode)
        self.set_status("idle" if self.turn_guard.can_start_new_turn else "running", self.event_label.text())

    def _toggle_session_sidebar(self) -> None:
        collapsed = not bool(self.session_sidebar.property("collapsed"))
        self.session_sidebar.set_collapsed(collapsed)
        self.sidebar_toggle_button.setText("⟩" if collapsed else "⟨")

    def _start_new_session(self) -> None:
        if not self.turn_guard.can_start_new_turn:
            return
        self.chat_session.new_session()
        self.session_sidebar.select_session("")
        self._clear_message_area()
        self.session_title_label.setText(self._t('main.new_chat'))
        self.set_status_i18n("idle", "main.event.new_chat")

    def _resume_selected_session(self, session_id: str) -> None:
        if not self.turn_guard.can_start_new_turn:
            return
        selected_id = str(session_id or "").strip()
        if not selected_id:
            return
        messages = self.chat_session.resume_session(selected_id)
        current_id = str(self.chat_session.current_session_id or "").strip()
        if not current_id:
            return
        self.session_sidebar.select_session(current_id)
        self._clear_message_area(show_empty=not bool(messages))
        for message in messages:
            role = str(message.get("role") or "").strip().lower()
            content = str(message.get("content") or "")
            if not content:
                continue
            if role == "user":
                self.add_message("user", content)
            elif role == "assistant":
                self.add_answer_block(content)
        self.session_title_label.setText(
            self.session_sidebar.session_title(current_id) or _title_from_messages(messages) or self._t('main.history')
        )
        self.set_status_i18n("idle", "main.event.restored")

    def _on_sidebar_session_deleted(self, session_id: str) -> None:
        if str(session_id or "") == str(self.chat_session.current_session_id or ""):
            self._start_new_session()

    def send_current_message(self) -> None:
        if self._closing:
            return
        text = self.input_box.toPlainText().strip()
        if not text or not self.turn_guard.can_start_new_turn:
            return
        self.input_box.clear()
        self._clear_error_panel()
        self._last_failed_prompt = text
        self.add_message("user", text)
        turn_id = self.turn_guard.start()
        self.work_area = None
        self._work_area_row = None
        self._stream_answer_block = None
        self._answer_stream.reset()
        self._turn_start = time.monotonic()
        self._show_thinking(self._t('main.event.thinking'))
        self.set_running(True)
        self.set_status_i18n("running", "main.event.processing")
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
        row.setObjectName("UserMessageRow" if role == "user" else "AssistantMessageRow")
        row.setProperty("visualRole", role)
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
        area = WorkArea(payload, model_labels=model_labels, language=self.language)
        self.work_area = area
        row = QWidget()
        row.setObjectName("ExecutionAreaRow")
        row.setProperty("visualRole", "execution")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 4, 0, 4)
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
        row.setObjectName("AssistantAnswerRow")
        row.setProperty("visualRole", "assistant")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.addWidget(block, 1)
        self.message_layout.addWidget(row)
        self._scroll_to_bottom()
        return block

    def show_failed_state(self, reason: str) -> ErrorRecoveryPanel:
        self._clear_thinking()
        self._hide_empty_state()
        self._clear_error_panel()
        panel = ErrorRecoveryPanel(reason, language=self.language)
        panel.retry_requested.connect(self._prefill_retry_prompt)
        panel.switch_model_requested.connect(self._open_settings_models_page)
        panel.provider_doctor_requested.connect(self._open_provider_manager)
        row = QWidget()
        row.setObjectName("ErrorRecoveryRow")
        row.setProperty("visualRole", "error")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 4, 0, 4)
        row_layout.addWidget(panel, 1)
        self.message_layout.addWidget(row)
        self._error_panel = panel
        self._error_panel_row = row
        self.set_status_i18n("failed", "main.event.failed")
        self.set_running(False)
        self._scroll_to_bottom()
        return panel

    def _clear_error_panel(self) -> None:
        if self._error_panel_row is not None:
            self.message_layout.removeWidget(self._error_panel_row)
            self._error_panel_row.deleteLater()
        self._error_panel_row = None
        self._error_panel = None

    def _prefill_retry_prompt(self) -> None:
        prompt = str(self._last_failed_prompt or "").strip()
        if prompt:
            self.input_box.setPlainText(prompt)
            self.input_box.setFocus()
        self.set_status_i18n("idle", "main.event.retry_prefilled")

    def _open_settings_models_page(self) -> None:
        self.settings_panel.show_settings("Models")

    def _append_stream_answer(self, text: str) -> None:
        if not text:
            return
        current = self._answer_stream.append_delta(text)
        if self._stream_answer_block is None:
            self._stream_answer_block = self.add_answer_block(current)
        else:
            self._stream_answer_block.set_text(current)
            self._scroll_to_bottom()

    def _finalize_stream_answer(self, final_output: str) -> bool:
        if self._stream_answer_block is None and not self._answer_stream.has_streamed:
            return False
        current = self._answer_stream.finalize(final_output)
        if self._stream_answer_block is None:
            self._stream_answer_block = self.add_answer_block(current)
        elif current:
            self._stream_answer_block.set_text(current)
            self._scroll_to_bottom()
        return True

    def _show_thinking(self, text: str) -> None:
        self._clear_thinking()
        self._hide_empty_state()
        indicator = ThinkingIndicator(text)
        indicator.set_available_width(self._chat_viewport_width())
        self._thinking_indicators.append(indicator)
        row = QWidget()
        row.setObjectName("ThinkingRow")
        row.setProperty("visualRole", "thinking")
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
        self.top_settings_button.setEnabled(not running)
        self.send_button.setEnabled(not running)
        self.stop_button.setEnabled(running)
        self.input_box.setEnabled(not running)
        self.control_bar.set_enabled(not running)
        for button in self.composer_tool_buttons:
            button.setEnabled(not running)
        self.settings_panel.set_enabled(not running)
        self.session_sidebar.set_enabled(not running)

    def set_stopping(self) -> None:
        self.top_settings_button.setEnabled(False)
        self.send_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        self.input_box.setEnabled(False)
        self.control_bar.set_enabled(False)
        for button in self.composer_tool_buttons:
            button.setEnabled(False)
        self.settings_panel.set_enabled(False)
        self.session_sidebar.set_enabled(False)
        self.set_status_i18n("stopped", "main.event.stopping")

    def set_approval_waiting(self) -> None:
        self.top_settings_button.setEnabled(False)
        self.send_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.input_box.setEnabled(False)
        self.control_bar.set_enabled(False)
        for button in self.composer_tool_buttons:
            button.setEnabled(False)
        self.settings_panel.set_enabled(False)
        self.session_sidebar.set_enabled(False)
        self.set_status_i18n("running", "main.event.approval")

    def set_status_i18n(self, state: str, event_key: str) -> None:
        self.set_status(state, self._t(event_key), event_key=event_key)

    def set_status(self, state: str, event: str, *, event_key: str = "") -> None:
        self._status_state = state
        self._status_event_key = event_key
        labels = {
            "idle": self._t('main.status.idle'),
            "running": self._t('main.status.running'),
            "stopped": self._t('main.status.stopped'),
            "failed": self._t('main.status.failed'),
        }
        self.state_label.setText(f"{labels.get(state, state)} · {self._t('main.status.mode')} {self.mode}")
        self.state_label.setStyleSheet(status_style(state))
        self.event_label.setText(event)
        self.top_status_chip.setText(f"\u25cf {labels.get(state, state)}")
        self.top_status_chip.setProperty("state", state)
        style = self.top_status_chip.style()
        style.unpolish(self.top_status_chip)
        style.polish(self.top_status_chip)
        self.top_status_chip.update()

    def handle_runtime_event(self, event: dict) -> None:
        if self._closing:
            return
        event_type = str(event.get("event_type") or "")
        task_id = str(event.get("task_id") or "")

        if event_type == "TurnStarted":
            self.set_status_i18n("running", "main.event.turn_started")
            return
        if event_type == "PlanningStarted":
            if self._thinking_indicator is not None:
                self._thinking_indicator.set_base(self._t('main.event.planning'))
        if event_type == "PlanningCompleted":
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            if payload.get("tasks") or payload.get("route_type"):
                self.ensure_work_area(payload)

        if event_type == "AgentMessageDelta":
            if not self.turn_guard.is_running or self.turn_guard.is_stopping or not self.work_task_id:
                return
            route = classify_gui_stream_event(event, mode=self.mode)
            if route == "answer":
                payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
                text = str(payload.get("text") or event.get("text") or event.get("message") or "")
                self._append_stream_answer(text)
            elif route == "work_area" and self.work_area is not None and task_id and self.work_area.has_task(task_id):
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
            self.session_sidebar.refresh()
            if self.turn_guard.is_running and not self.turn_guard.is_stopping:
                payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
                if str(payload.get("status") or "") == "failed":
                    self.show_failed_state(_event_text(event) or self._t('main.turn_failed_default'))
                else:
                    self.set_status_i18n("idle", "main.event.completed")
            return
        if event_type == "ToolApprovalPre":
            self.approval_context.update_from_event(event)
            if not self.turn_guard.is_stopping:
                self.set_approval_waiting()
        if event_type == "ToolApprovalPost":
            if self.turn_guard.is_running and not self.turn_guard.is_stopping:
                self.set_running(True)
                self.set_status_i18n("running", "main.event.approval_done")
        summary = _event_summary(event)
        if summary:
            self.event_label.setText(summary)

    async def _run_turn(self, turn_id: int, text: str) -> None:
        try:
            result = await self.chat_session.run_turn(text)
            if self._closing or not self.turn_guard.is_current(turn_id):
                return
            if result.final_output and not result.failed:
                if not self._finalize_stream_answer(result.final_output):
                    self.add_answer_block(result.final_output)
            self.mode = result.execution_mode or self.mode
            if result.stopped:
                self.set_status_i18n("stopped", "main.event.stopped")
            elif result.failed:
                self.show_failed_state(result.final_output)
            else:
                self.set_status_i18n("idle", "main.event.completed")
            self.session_sidebar.refresh()
            current_id = str(self.chat_session.current_session_id or "").strip()
            if current_id:
                self.session_sidebar.select_session(current_id)
                title = self.session_sidebar.session_title(current_id)
                if title:
                    self.session_title_label.setText(title)
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

    def _create_empty_state(self) -> QLabel:
        label = QLabel(self._t('main.empty'))
        label.setObjectName("EmptyState")
        label.setAlignment(Qt.AlignCenter)
        return label

    def _clear_message_area(self, *, show_empty: bool = True) -> None:
        self._clear_thinking()
        self.work_area = None
        self._work_area_row = None
        self._stream_answer_block = None
        self._answer_stream.reset()
        self._bubbles = []
        self._thinking_indicators = []
        self._error_panel_row = None
        self._error_panel = None
        while self.message_layout.count():
            item = self.message_layout.takeAt(0)
            widget = item.widget()
            if widget is not None and widget is not self.empty_state:
                widget.deleteLater()
        self.message_layout.addWidget(self.empty_state, 1)
        self.empty_state.setVisible(show_empty)

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


def _title_from_messages(messages: list[dict[str, str]]) -> str:
    for message in messages:
        if str(message.get("role") or "").strip().lower() != "user":
            continue
        text = str(message.get("content") or "").replace("\n", " ").strip()
        if not text:
            continue
        return text[:34] + "..." if len(text) > 36 else text
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
