from __future__ import annotations

import asyncio

from lucode.gui.approval import (
    APPROVAL_DECISIONS,
    ApprovalRequestContext,
    LatestApprovalContext,
    safe_resolve_future,
)


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
