from __future__ import annotations

import asyncio

from lucode.gui.chat_session import GuiChatSession
from lucode.gui.event_bridge import DeltaCoalescer, coalesce_events_for_test
from lucode.gui.turn_state import TurnStateGuard


def test_delta_coalescer_flushes_before_non_delta_events():
    events = [
        {"event_type": "AgentMessageDelta", "payload": {"text": "a"}},
        {"event_type": "AgentMessageDelta", "payload": {"text": "b"}},
        {"event_type": "ToolInvoked", "message": "read"},
        {"event_type": "AgentMessageDelta", "payload": {"text": "c"}},
    ]

    out = coalesce_events_for_test(events)

    assert [event["event_type"] for event in out] == [
        "AgentMessageDelta",
        "ToolInvoked",
        "AgentMessageDelta",
    ]
    assert out[0]["payload"]["text"] == "ab"
    assert out[2]["payload"]["text"] == "c"


def test_delta_coalescer_ignores_empty_delta():
    coalescer = DeltaCoalescer()
    coalescer.push({"event_type": "AgentMessageDelta", "payload": {"text": ""}})
    assert coalescer.flush() is None


def test_turn_state_guard_prevents_old_turn_from_mutating_new_turn():
    guard = TurnStateGuard()
    first = guard.start()
    second = guard.start()

    assert not guard.is_current(first)
    assert guard.is_current(second)
    assert not guard.finish_if_current(first)
    assert guard.finish_if_current(second)


def test_turn_state_guard_marks_stopping_until_same_turn_finishes():
    guard = TurnStateGuard()
    first = guard.start()

    assert guard.request_stop(first)
    assert guard.is_stopping
    assert not guard.can_start_new_turn
    assert guard.finish_if_current(first)
    assert guard.can_start_new_turn


def test_turn_state_guard_rejects_new_turn_during_stopping():
    guard = TurnStateGuard()
    first = guard.start()

    assert guard.request_stop(first)
    assert not guard.can_start_new_turn
    assert guard.is_current(first)


def test_gui_chat_session_returns_stopped_result_on_cancel(monkeypatch, tmp_path):
    class FakeKernelFacade:
        def __init__(self, workspace_context):
            del workspace_context

        async def run_once(self, *args, **kwargs):
            del args, kwargs
            raise asyncio.CancelledError()

    monkeypatch.setattr("lucode.gui.chat_session.KernelFacade", FakeKernelFacade)

    session = GuiChatSession(workspace=tmp_path)

    result = asyncio.run(session.run_turn("stop this turn"))

    assert result.stopped is True
    assert result.failed is False
    assert result.final_output == "(Stopped)"
