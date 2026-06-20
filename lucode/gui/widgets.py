from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from lucode.gui.i18n import Translator
from lucode.gui.theme import TOKENS


MAX_MESSAGE_CHARS = 20000


ROUTE_I18N_KEYS = {
    "multi_agent": "widgets.route.multi_agent",
    "single_agent": "widgets.route.single_agent",
    "direct_answer": "widgets.route.direct_answer",
    "clarify": "widgets.route.clarify",
}


BUBBLE_WIDTH_RATIO = 0.62
BUBBLE_MIN_WIDTH = 72
BUBBLE_MAX_WIDTH = 520
BUBBLE_H_PADDING = 28  # layout left+right margins (14 + 14)


class MessageBubble(QFrame):
    def __init__(self, role: str, text: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.role = role
        self._text = ""
        self._truncated = False
        self.setObjectName("MessageBubble")
        self.setProperty("userRole", role == "user")
        self.style().unpolish(self)
        self.style().polish(self)
        self._cap = BUBBLE_MAX_WIDTH
        self.setMaximumWidth(self._cap)
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 8, 14, 8)
        layout.setSpacing(6)

        self.content_label = QLabel()
        self.content_label.setObjectName("UserText")
        self.content_label.setWordWrap(True)
        self.content_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.content_label)

        if text:
            self.set_text(text)

    def set_available_width(self, available: int) -> None:
        if available <= 0:
            return
        cap = int(available * BUBBLE_WIDTH_RATIO)
        cap = max(BUBBLE_MIN_WIDTH, min(cap, BUBBLE_MAX_WIDTH))
        self._cap = cap
        self._resize_to_content()

    def _natural_text_width(self, text: str) -> int:
        lines = str(text or "").splitlines() or [""]
        return max(self.content_label.fontMetrics().horizontalAdvance(line) for line in lines)

    def _resize_to_content(self) -> None:
        content_cap = max(1, self._cap - BUBBLE_H_PADDING)
        natural = self._natural_text_width(self._text)
        content_width = max(1, min(natural, content_cap))
        bubble_width = max(BUBBLE_MIN_WIDTH, min(content_width + BUBBLE_H_PADDING, self._cap))
        self.setFixedWidth(bubble_width)
        self.content_label.setMaximumWidth(max(1, bubble_width - BUBBLE_H_PADDING))
        self.updateGeometry()

    def append_text(self, text: str) -> None:
        self.set_text(self._text + str(text or ""))

    def set_text(self, text: str) -> None:
        value = str(text or "")
        self._truncated = len(value) > MAX_MESSAGE_CHARS
        if self._truncated:
            value = value[:MAX_MESSAGE_CHARS] + Translator()('widgets.truncated')
        self._text = value
        self.content_label.setText(value)
        self._resize_to_content()


STATUS_I18N_KEYS = {
    "waiting": "widgets.status.waiting",
    "running": "widgets.status.running",
    "completed": "widgets.status.completed",
    "failed": "widgets.status.failed",
}

STATUS_TOKEN = {
    "waiting": "text_muted",
    "running": "primary",
    "completed": "success",
    "failed": "danger",
}

MAX_ACTIVITY_CHARS = 500


def _chip(text: str, object_name: str) -> QLabel:
    chip = QLabel(text)
    chip.setObjectName(object_name)
    chip.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return chip


def _group_by_parallel(tasks: list[dict], *, language: str = 'zh') -> list[tuple[str, list[tuple[int, dict]]]]:
    groups: dict[object, list[tuple[int, dict]]] = {}
    order: list[object] = []
    for index, task in enumerate(tasks, start=1):
        raw = task.get("parallel_group")
        key = raw if raw not in (None, "") else None
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append((index, task))
    show_label = len(order) > 1
    result: list[tuple[str, list[tuple[int, dict]]]] = []
    for key in order:
        label = Translator(language)('widgets.parallel_group', group=key) if (key is not None and show_label) else ""
        result.append((label, groups[key]))
    return result


MAX_LATEST_CHARS = 96


def _clean(value) -> str:
    return str(value or "").strip()


def _payload(event: dict) -> dict:
    payload = event.get("payload")
    return payload if isinstance(payload, dict) else {}


def _file_paths(payload: dict, *, access: str = "") -> list[str]:
    files = payload.get("files_touched")
    if not isinstance(files, list):
        return []
    paths: list[str] = []
    for item in files:
        if not isinstance(item, dict):
            continue
        path = _clean(item.get("path"))
        if not path:
            continue
        if access and _clean(item.get("access")).lower() != access:
            continue
        paths.append(path)
    return paths


def _action_group(payload: dict) -> str:
    tool = _clean(payload.get("tool") or payload.get("tool_name")).lower()
    action = _clean(payload.get("action") or payload.get("command")).lower()
    text = f"{tool} {action}"
    files = payload.get("files_touched")
    if isinstance(files, list):
        accesses = {_clean(item.get("access")).lower() for item in files if isinstance(item, dict)}
        if "write" in accesses:
            return "write"
        if "read" in accesses:
            return "read"
    if any(m in text for m in ("write", "edit", "patch", "replace", "delete", "create_file")):
        return "write"
    if any(m in text for m in ("read", "filesystem_readonly", "git_status", "git_diff")):
        return "read"
    if any(m in text for m in ("search", "locate", "grep", "rg", "select-string", "code_locator")):
        return "search"
    if any(m in text for m in ("run_command", "command_runner", "shell", "pytest", "unittest", "python")):
        return "run"
    return ""


def action_label_from_event(event: dict, *, language: str = 'zh') -> str:
    """Return a short user-facing action label for worker events."""

    event_type = _clean(event.get("event_type"))
    payload = _payload(event)
    summary = payload.get("arguments_summary")
    summary = summary if isinstance(summary, dict) else {}
    t = Translator(language)
    if event_type == "FastPathUsed":
        tool = _clean(payload.get("tool") or payload.get("action"))
        return t('widgets.fast_path', tool=tool) if tool else t('widgets.fast_path_default')
    if event_type != "ToolInvoked":
        return ""
    tool = _clean(payload.get("tool") or payload.get("tool_name"))
    paths = _file_paths(payload)
    if tool and paths:
        return t('widgets.tool_path', tool=tool, path=paths[0])
    if tool:
        return t('widgets.tool', tool=tool)
    group = _action_group(payload)
    if group == "read":
        paths = _file_paths(payload, access="read")
        return t('widgets.read', path=paths[0]) if paths else t('widgets.read_default')
    if group == "write":
        paths = _file_paths(payload, access="write")
        return t('widgets.write', path=paths[0]) if paths else t('widgets.write_default')
    if group == "search":
        query = _clean(summary.get("query") or summary.get("pattern") or summary.get("text"))
        return t('widgets.search', query=query) if query else t('widgets.search_default')
    if group == "run":
        command = _clean(summary.get("command") or summary.get("cmd"))
        return t('widgets.run', command=command) if command else t('widgets.run_default')
    return _clean(event.get("message")) or t('widgets.tool_event')

def _truncate_line(text: str) -> str:
    line = _clean(text).splitlines()[-1] if _clean(text) else ""
    return line[: MAX_LATEST_CHARS - 1] + "?" if len(line) > MAX_LATEST_CHARS else line


class WorkerNode(QFrame):
    """One worker, collapsible. Shows status + title + latest-action line by
        default; expand to read the full streamed activity and tool log."""

    def __init__(self, index: int, task: dict, model_label: str, *, language: str = 'zh', parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("WorkerNode")
        self._language = language
        self._t = Translator(language)
        self.task_id = str(task.get("id") or "")
        self.status = "waiting"
        self._thinking = ""
        self._actions: list[str] = []
        self._latest = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

        head = QHBoxLayout()
        head.setSpacing(8)
        self.toggle = QPushButton("\u25b8")
        self.toggle.setObjectName("NodeToggle")
        self.toggle.setCheckable(True)
        self.toggle.setChecked(False)
        self.toggle.clicked.connect(self._toggle_body)
        head.addWidget(self.toggle)

        self.dot = QLabel("\u25cf")
        self.dot.setObjectName("PlanDot")
        self._apply_dot("waiting")
        head.addWidget(self.dot)

        title = str(task.get("title") or task.get("id") or self._t('widgets.task', index=index))
        self.title_label = QLabel(f"{index}. {title}")
        self.title_label.setObjectName("PlanTaskTitle")
        self.title_label.setWordWrap(True)
        self.title_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        head.addWidget(self.title_label, 1)

        self.status_label = QLabel(self._t(STATUS_I18N_KEYS['waiting']))
        self.status_label.setObjectName("PlanStatus")
        head.addWidget(self.status_label)
        layout.addLayout(head)

        meta = QHBoxLayout()
        meta.setSpacing(8)
        meta.addWidget(_chip(self._t('widgets.model', model=model_label or self._t('widgets.unassigned')), "PlanChipModel"))
        mcp = [str(item) for item in (task.get("mcp") or []) if str(item)]
        if mcp:
            meta.addWidget(_chip("MCP " + ", ".join(mcp), "PlanChip"))
        depends = [str(item) for item in (task.get("depends_on") or []) if str(item)]
        if depends:
            meta.addWidget(_chip(self._t('widgets.depends') + ", ".join(depends), "PlanChip"))
        meta.addStretch(1)
        layout.addLayout(meta)

        self.latest_label = QLabel("")
        self.latest_label.setObjectName("NodeLatest")
        self.latest_label.setWordWrap(False)
        self.latest_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.latest_label.hide()
        layout.addWidget(self.latest_label)

        self.detail_label = QLabel("")
        self.detail_label.setObjectName("PlanActivity")
        self.detail_label.setWordWrap(True)
        self.detail_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.detail_label.hide()
        layout.addWidget(self.detail_label)

    def _apply_dot(self, status: str) -> None:
        token = STATUS_TOKEN.get(status, "text_muted")
        self.dot.setStyleSheet(f"color: {TOKENS[token]};")

    def _toggle_body(self) -> None:
        expanded = self.toggle.isChecked()
        self.toggle.setText("\u25be" if expanded else "\u25b8")
        self.detail_label.setVisible(expanded and bool(self.detail_label.text()))

    def _set_latest(self, text: str) -> None:
        line = _truncate_line(text)
        if not line:
            return
        self._latest = line
        self.latest_label.setText(line)
        self.latest_label.show()

    def _refresh_detail(self) -> None:
        parts = []
        if self._actions:
            parts.append("\n".join(f"- {a}" for a in self._actions[-12:]))
        if self._thinking.strip():
            parts.append(self._thinking.strip())
        detail = "\n\n".join(parts)[-MAX_ACTIVITY_CHARS:]
        self.detail_label.setText(detail)
        self.detail_label.setVisible(self.toggle.isChecked() and bool(detail))

    def set_status(self, status: str) -> None:
        self.status = status
        self._apply_dot(status)
        self.status_label.setText(self._t(STATUS_I18N_KEYS.get(status, ''),) if status in STATUS_I18N_KEYS else status)

    def append_thinking(self, text: str) -> None:
        self._thinking = (self._thinking + str(text or ""))[-MAX_ACTIVITY_CHARS:]
        self._set_latest(self._thinking)
        self._refresh_detail()

    def add_action(self, label: str) -> None:
        label = _clean(label)
        if not label:
            return
        self._actions.append(label)
        self._set_latest(label)
        self._refresh_detail()


class WorkArea(QFrame):
    """Flat (non-bubble) live execution tree: supervisor root + worker nodes.

    Workers light up and stream their latest action as the turn runs; when the
    turn ends the whole area collapses to a compact completion summary.
    """

    def __init__(
        self,
        payload: dict,
        *,
        model_labels: dict[str, str] | None = None,
        language: str = 'zh',
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setObjectName("WorkArea")
        labels = model_labels or {}
        self._language = language
        self._t = Translator(language)
        self.worker_nodes: dict[str, WorkerNode] = {}
        self._collapsed_summary: str | None = None

        route = str(payload.get("route_type") or "")
        tasks = list(payload.get("tasks") or [])
        self._route_text = self._t(ROUTE_I18N_KEYS.get(route, ''),) if route in ROUTE_I18N_KEYS else (route or self._t('widgets.unplanned'))
        self._task_count = len(tasks)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(6)

        self.header = QPushButton()
        self.header.setObjectName("WorkAreaHeader")
        self.header.setCheckable(True)
        self.header.setChecked(True)
        self.header.clicked.connect(self._toggle_body)
        layout.addWidget(self.header)

        self.body = QWidget()
        body_layout = QVBoxLayout(self.body)
        body_layout.setContentsMargins(14, 0, 0, 0)
        body_layout.setSpacing(6)
        layout.addWidget(self.body)

        self.supervisor_activity = QLabel("")
        self.supervisor_activity.setObjectName("SupervisorActivity")
        self.supervisor_activity.setWordWrap(True)
        self.supervisor_activity.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.supervisor_activity.hide()
        body_layout.addWidget(self.supervisor_activity)

        plan_line = QLabel(self._t('widgets.plan_done', route=self._route_text, count=self._task_count))
        plan_line.setObjectName("PlanGroupLabel")
        body_layout.addWidget(plan_line)

        if not tasks:
            empty = QLabel(self._t('widgets.no_tasks'))
            empty.setObjectName("PlanEmpty")
            body_layout.addWidget(empty)
        else:
            for group_label, group_tasks in _group_by_parallel(tasks, language=language):
                if group_label:
                    gl = QLabel(group_label)
                    gl.setObjectName("PlanGroupLabel")
                    body_layout.addWidget(gl)
                for index, task in group_tasks:
                    model_id = str(task.get("model") or "")
                    node = WorkerNode(index, task, labels.get(model_id, model_id), language=language)
                    if node.task_id:
                        self.worker_nodes[node.task_id] = node
                    body_layout.addWidget(node)
        self._render_header()

    def _render_header(self) -> None:
        if self._collapsed_summary is not None:
            self.header.setText(f"\u25b8 {self._collapsed_summary}")
            return
        arrow = "\u25be" if self.header.isChecked() else "\u25b8"
        self.header.setText(f"{arrow} {self._t('widgets.execution', count=self._task_count)}")

    def _toggle_body(self) -> None:
        self.body.setVisible(self.header.isChecked())
        self._render_header()

    def has_task(self, task_id: str) -> bool:
        return task_id in self.worker_nodes

    def set_task_status(self, task_id: str, status: str) -> None:
        node = self.worker_nodes.get(task_id)
        if node:
            node.set_status(status)

    def append_task_text(self, task_id: str, text: str) -> None:
        node = self.worker_nodes.get(task_id)
        if node:
            node.append_thinking(text)

    def set_supervisor_activity(self, text: str) -> None:
        text = _clean(text)
        if not text:
            return
        self.supervisor_activity.setText(self._t('widgets.supervisor', text=text))
        self.supervisor_activity.show()

    def apply_event(self, event: dict) -> bool:
        """Route a worker-scoped event into its node. Returns True if consumed."""

        payload = _payload(event)
        task_id = str(event.get("task_id") or payload.get("task_id") or "")
        node = self.worker_nodes.get(task_id) if task_id else None
        if node is None:
            return False
        event_type = _clean(event.get("event_type"))
        if event_type == "TaskStarted":
            node.set_status("running")
        elif event_type == "TaskCompleted":
            node.set_status("completed")
        elif event_type == "TaskFailed":
            node.set_status("failed")
        elif event_type == "AgentMessageDelta":
            text = str(event.get("text") or payload.get("text") or event.get("message") or "")
            node.append_thinking(text)
        elif event_type in {"ToolInvoked", "FastPathUsed"}:
            node.add_action(action_label_from_event(event, language=self._language))
        else:
            return False
        return True

    def collapse_done(self, elapsed_seconds: float | None = None) -> None:
        total = len(self.worker_nodes)
        done = sum(1 for n in self.worker_nodes.values() if n.status == "completed")
        failed = sum(1 for n in self.worker_nodes.values() if n.status == "failed")
        if total and failed:
            base = self._t('widgets.completed_failed', done=done, total=total, failed=failed)
        elif total:
            base = self._t('widgets.completed_tasks', total=total)
        else:
            base = self._t('widgets.completed')
        if elapsed_seconds is not None and elapsed_seconds >= 0:
            base += " · " + self._t('widgets.seconds', seconds=int(round(elapsed_seconds)))
        self._collapsed_summary = base
        self.header.setChecked(False)
        self.body.setVisible(False)
        self._render_header()


class AnswerBlock(QFrame):
    """Final answer rendered as flat markdown (not a chat bubble)."""

    def __init__(self, text: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("AnswerBlock")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(0)
        self.content_label = QLabel()
        self.content_label.setObjectName("AnswerText")
        self.content_label.setTextFormat(Qt.TextFormat.MarkdownText)
        self.content_label.setWordWrap(True)
        self.content_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.content_label.setOpenExternalLinks(True)
        layout.addWidget(self.content_label)
        if text:
            self.set_text(text)

    def set_text(self, text: str) -> None:
        value = str(text or "")
        if len(value) > MAX_MESSAGE_CHARS:
            value = value[:MAX_MESSAGE_CHARS] + "\n\n[内容已截断]"
        self.content_label.setText(value)


class ErrorRecoveryPanel(QFrame):
    retry_requested = Signal()
    switch_model_requested = Signal()
    provider_doctor_requested = Signal()

    def __init__(self, reason: str = "", *, language: str = 'zh', parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("ErrorRecoveryPanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        self._language = language
        self._t = Translator(self._language)
        title = QLabel(self._t('widgets.error.title'))
        title.setObjectName("RunFailedTitle")
        title.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(title)

        self.reason_label = QLabel()
        self.reason_label.setObjectName("RunFailedReason")
        self.reason_label.setWordWrap(True)
        self.reason_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.reason_label)

        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        layout.addLayout(action_row)

        retry = QPushButton(self._t('widgets.error.retry'))
        retry.setObjectName("RunFailedRetryButton")
        retry.clicked.connect(self.retry_requested.emit)
        action_row.addWidget(retry)

        switch_model = QPushButton(self._t('widgets.error.switch_model'))
        switch_model.setObjectName("RunFailedSwitchModelButton")
        switch_model.clicked.connect(self.switch_model_requested.emit)
        action_row.addWidget(switch_model)

        provider_doctor = QPushButton(self._t('widgets.error.provider'))
        provider_doctor.setObjectName("RunFailedProviderDoctorButton")
        provider_doctor.clicked.connect(self.provider_doctor_requested.emit)
        action_row.addWidget(provider_doctor)
        action_row.addStretch(1)

        self.set_reason(reason)

    def set_language(self, language: str) -> None:
        self._language = language
        self._t = Translator(language)
        self.findChild(QLabel, 'RunFailedTitle').setText(self._t('widgets.error.title'))
        self.findChild(QPushButton, 'RunFailedRetryButton').setText(self._t('widgets.error.retry'))
        self.findChild(QPushButton, 'RunFailedSwitchModelButton').setText(self._t('widgets.error.switch_model'))
        self.findChild(QPushButton, 'RunFailedProviderDoctorButton').setText(self._t('widgets.error.provider'))

    def set_reason(self, reason: str) -> None:
        value = str(reason or "").strip() or self._t('widgets.error.default_reason')
        if len(value) > 800:
            value = value[:800].rstrip() + self._t('widgets.error.reason_truncated')
        self.reason_label.setText(value)


class ThinkingIndicator(QFrame):
    """Transient 'the brain is thinking' bubble with animated dots.

    Shown right after send so the long planning phase (which emits no events)
    doesn't look frozen. Replaced by the plan card or answer when work appears.
    """

    def __init__(self, text: str = "thinking", parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("ThinkingIndicator")
        self._cap = BUBBLE_MAX_WIDTH
        self.setMaximumWidth(self._cap)
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
        self._base = text
        self._dots = 0

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 8, 14, 8)
        self.label = QLabel(f"{text}...")
        self.label.setObjectName("ThinkingText")
        layout.addWidget(self.label)

        self._timer = QTimer(self)
        self._timer.setInterval(450)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        self._resize_to_content()

    def set_available_width(self, available: int) -> None:
        if available <= 0:
            return
        cap = int(available * BUBBLE_WIDTH_RATIO)
        self._cap = max(BUBBLE_MIN_WIDTH, min(cap, BUBBLE_MAX_WIDTH))
        self.setMaximumWidth(self._cap)
        self._resize_to_content()

    def _resize_to_content(self) -> None:
        natural = self.label.fontMetrics().horizontalAdvance(self.label.text())
        bubble_width = max(BUBBLE_MIN_WIDTH, min(natural + BUBBLE_H_PADDING, self._cap))
        self.setFixedWidth(bubble_width)
        self.updateGeometry()

    def set_base(self, text: str) -> None:
        self._base = text
        self._render()
        self._resize_to_content()

    def _tick(self) -> None:
        self._dots = (self._dots + 1) % 4
        self._render()
        self._resize_to_content()

    def _render(self) -> None:
        self.label.setText(self._base + "." * self._dots)
        self._resize_to_content()

    def stop(self) -> None:
        self._timer.stop()


def status_style(state: str) -> str:
    color = {
        "running": TOKENS["primary"],
        "stopped": TOKENS["warning"],
        "failed": TOKENS["danger"],
        "idle": TOKENS["text_muted"],
    }.get(state, TOKENS["text_muted"])
    return f"color: {color};"
