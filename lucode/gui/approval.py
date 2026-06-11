from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ApprovalDecisions:
    once: str = "y"
    session: str = "session"
    rule: str = "rule"
    deny: str = "n"
    edit: str = "edit"


APPROVAL_DECISIONS = ApprovalDecisions()


@dataclass
class ApprovalRequestContext:
    prompt: str
    tool_name: str = ""
    tool_rule: str = ""
    arguments_summary: dict[str, Any] = field(default_factory=dict)
    files_touched: list[dict[str, Any]] = field(default_factory=list)
    risk: dict[str, Any] = field(default_factory=dict)


class LatestApprovalContext:
    def __init__(self) -> None:
        self._payload: dict[str, Any] = {}

    def update_from_event(self, event: dict[str, Any]) -> None:
        if str(event.get("event_type") or "") != "ToolApprovalPre":
            return
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        self._payload = dict(payload)

    def snapshot(self, prompt: str) -> ApprovalRequestContext:
        payload = dict(self._payload)
        arguments_summary = payload.get("arguments_summary")
        files_touched = payload.get("files_touched")
        risk = payload.get("risk")
        return ApprovalRequestContext(
            prompt=str(prompt or ""),
            tool_name=str(payload.get("tool_name") or payload.get("tool") or ""),
            tool_rule=str(payload.get("tool_rule") or ""),
            arguments_summary=dict(arguments_summary if isinstance(arguments_summary, dict) else {}),
            files_touched=[
                dict(item)
                for item in (files_touched if isinstance(files_touched, list) else [])
                if isinstance(item, dict)
            ],
            risk=dict(risk if isinstance(risk, dict) else {}),
        )


def safe_resolve_future(future: asyncio.Future, value: str) -> bool:
    if future.done():
        return False
    future.set_result(str(value or APPROVAL_DECISIONS.deny))
    return True


def release_dialog(dialog) -> None:
    if dialog is None:
        return
    # The Qt C++ object may already be gone (parent destroyed during shutdown);
    # touching it then raises RuntimeError, which must not fail the turn.
    try:
        dialog.close()
        dialog.deleteLater()
    except RuntimeError:
        pass


def _qt_base_dialog():
    try:
        from PySide6.QtWidgets import QDialog
    except ModuleNotFoundError:
        class QDialog:  # type: ignore[no-redef]
            pass

    return QDialog


def _qt_modules():
    from PySide6 import QtCore, QtWidgets

    return QtCore, QtWidgets


class GuiApprovalSession:
    def __init__(self, *, parent=None, context_store: LatestApprovalContext | None = None) -> None:
        self.parent = parent
        self.context_store = context_store or LatestApprovalContext()
        self._active_dialog = None
        self._active_future: asyncio.Future | None = None

    async def request_approval(self, prompt: str) -> str:
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._active_future = future
        context = self.context_store.snapshot(prompt)
        dialog = ApprovalDialog(context, future, parent=self.parent)
        self._active_dialog = dialog
        dialog.show()
        try:
            return await future
        finally:
            self._active_future = None
            if self._active_dialog is dialog:
                self._active_dialog = None
            release_dialog(dialog)

    def cancel_pending(self) -> None:
        future = self._active_future
        if future is not None and not future.done():
            future.cancel()
        release_dialog(self._active_dialog)


class ApprovalDialog(_qt_base_dialog()):
    def __init__(self, context: ApprovalRequestContext, future: asyncio.Future, parent=None):
        super().__init__(parent)
        self._future = future
        self._closed_by_decision = False
        self._build(context)

    def _build(self, context: ApprovalRequestContext) -> None:
        _, QtWidgets = _qt_modules()
        self.setWindowTitle("Tool approval")
        self.setModal(False)
        self.setMinimumWidth(560)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QtWidgets.QLabel("Approve tool call")
        title.setObjectName("RoleLabel")
        layout.addWidget(title)

        prompt = QtWidgets.QLabel(context.prompt or "Approve this tool call?")
        prompt.setWordWrap(True)
        layout.addWidget(prompt)

        details = QtWidgets.QPlainTextEdit(_render_context_details(context))
        details.setReadOnly(True)
        details.setMinimumHeight(180)
        layout.addWidget(details)

        button_row = QtWidgets.QHBoxLayout()
        button_row.setSpacing(8)
        layout.addLayout(button_row)

        once = QtWidgets.QPushButton("Allow once")
        once.setObjectName("SendButton")
        once.clicked.connect(lambda: self._decide(APPROVAL_DECISIONS.once))
        button_row.addWidget(once)

        session = QtWidgets.QPushButton("Allow tool")
        session.clicked.connect(lambda: self._decide(APPROVAL_DECISIONS.session))
        button_row.addWidget(session)

        rule = QtWidgets.QPushButton("Allow type")
        rule.clicked.connect(lambda: self._decide(APPROVAL_DECISIONS.rule))
        button_row.addWidget(rule)

        edit = QtWidgets.QPushButton("Edit instruction")
        edit.clicked.connect(lambda: self._decide(APPROVAL_DECISIONS.edit))
        button_row.addWidget(edit)

        deny = QtWidgets.QPushButton("Deny")
        deny.setObjectName("StopButton")
        deny.clicked.connect(lambda: self._decide(APPROVAL_DECISIONS.deny))
        button_row.addWidget(deny)

    def _decide(self, value: str) -> None:
        self._closed_by_decision = True
        safe_resolve_future(self._future, value)
        self.close()

    def closeEvent(self, event) -> None:
        if not self._closed_by_decision:
            safe_resolve_future(self._future, APPROVAL_DECISIONS.deny)
        super().closeEvent(event)


def _render_context_details(context: ApprovalRequestContext) -> str:
    lines = []
    if context.tool_name:
        lines.append(f"Tool: {context.tool_name}")
    if context.tool_rule:
        lines.append(f"Rule: {context.tool_rule}")
    if context.files_touched:
        lines.append("")
        lines.append("Files:")
        for item in context.files_touched:
            path = str(item.get("path") or "").strip()
            access = str(item.get("access") or "").strip()
            if path:
                lines.append(f"- {path}" + (f" ({access})" if access else ""))
    if context.arguments_summary:
        lines.append("")
        lines.append("Arguments summary:")
        for key in sorted(context.arguments_summary):
            value = context.arguments_summary.get(key)
            lines.append(f"- {key}: {_format_summary_value(value)}")
    if context.risk:
        lines.append("")
        lines.append("Risk:")
        for key in sorted(context.risk):
            value = context.risk.get(key)
            lines.append(f"- {key}: {_format_summary_value(value)}")
    if not lines:
        lines.append("No tool context was available for this approval request.")
    return "\n".join(lines)


def _format_summary_value(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value)
    if isinstance(value, dict):
        return ", ".join(f"{key}={value[key]}" for key in sorted(value))
    return str(value)

