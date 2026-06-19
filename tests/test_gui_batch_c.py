from __future__ import annotations

import asyncio

from lucode.gui.approval import (
    APPROVAL_DECISIONS,
    ApprovalRequestContext,
    LatestApprovalContext,
    _render_context_details,
    safe_resolve_future,
)
from runtime.hooks.event_bridge import emit_tool_event_bridge
from runtime.hooks.tool_events import build_tool_event


def test_latest_approval_context_uses_tool_approval_pre_payload():
    latest = LatestApprovalContext()
    latest.update_from_event(
        {
            "event_type": "ToolApprovalPre",
            "payload": {
                "tool_name": "workspace_edit.write_file",
                "tool_rule": "workspace_edit",
                "arguments_summary": {
                    "path": "runtime/example.py",
                    "content_length": 128,
                    "keys": ["content", "path"],
                },
                "files_touched": [{"path": "runtime/example.py", "access": "write"}],
                "risk": {"risk_level": "medium", "should_deny": False},
            },
        }
    )

    context = latest.snapshot("Approve this tool?")

    assert context.prompt == "Approve this tool?"
    assert context.tool_name == "workspace_edit.write_file"
    assert context.tool_rule == "workspace_edit"
    assert context.arguments_summary["path"] == "runtime/example.py"
    assert context.files_touched == [{"path": "runtime/example.py", "access": "write"}]
    assert context.risk["risk_level"] == "medium"


def test_latest_approval_context_falls_back_to_prompt_without_tool_event():
    context = LatestApprovalContext().snapshot("Approve?")

    assert context == ApprovalRequestContext(prompt="Approve?")


def test_approval_decisions_match_runtime_accepted_values():
    assert APPROVAL_DECISIONS.once == "y"
    assert APPROVAL_DECISIONS.session == "session"
    assert APPROVAL_DECISIONS.rule == "rule"
    assert APPROVAL_DECISIONS.deny == "n"
    assert APPROVAL_DECISIONS.edit == "edit"


def test_safe_resolve_future_ignores_done_future():
    async def run_case():
        future = asyncio.Future()
        future.cancel()
        return safe_resolve_future(future, "y")

    assert asyncio.run(run_case()) is False


def test_safe_resolve_future_sets_pending_future():
    async def run_case():
        future = asyncio.Future()
        resolved = safe_resolve_future(future, "session")
        return resolved, future.result()

    resolved, result = asyncio.run(run_case())

    assert resolved is True
    assert result == "session"


def test_approval_context_details_show_file_lines_and_code_preview():
    context = ApprovalRequestContext(
        prompt="Approve?",
        tool_name="edit_file",
        tool_rule="workspace_edit",
        arguments_summary={
            "path": "loader.py",
            "content": "def load_data():\n    return []\n",
            "mode": "replace",
        },
        files_touched=[
            {
                "path": "loader.py",
                "access": "write",
                "line_start": 1,
                "line_end": 12,
            }
        ],
        risk={"risk_level": "medium"},
    )

    details = _render_context_details(context)

    assert "Tool: edit_file" in details
    assert "Rule: workspace_edit" in details
    assert "File: loader.py" in details
    assert "Access: write" in details
    assert "Lines: 1-12" in details
    assert "Code preview:" in details
    assert "def load_data():" in details
    assert "mode: replace" in details


def test_tool_approval_event_payload_includes_requester_task_id():
    class EventBus:
        def __init__(self):
            self.events = []

        def emit(self, event_type, message, **kwargs):
            self.events.append({"event_type": event_type, "message": message, **kwargs})

    bus = EventBus()
    event = build_tool_event(
        "pre_tool_use",
        "workspace_edit.write_file",
        '{"path": "runtime/example.py", "content": "x"}',
        tool_rule="workspace_edit",
        status="pending",
    )

    emit_tool_event_bridge(bus, event, task_id="worker-edit")

    assert bus.events[0]["event_type"] == "ToolApprovalPre"
    assert bus.events[0]["payload"]["requester"] == "worker-edit"
