from __future__ import annotations

from lucode.gui.stream_routing import classify_gui_stream_event


def test_solo_delta_routes_to_answer_even_with_task_id():
    event = {
        "event_type": "AgentMessageDelta",
        "agent": "solo",
        "task_id": "solo_agent",
        "payload": {"text": "hello"},
    }

    assert classify_gui_stream_event(event, mode="solo") == "answer"


def test_worker_delta_routes_to_work_area_outside_solo():
    event = {
        "event_type": "AgentMessageDelta",
        "agent": "worker_a",
        "task_id": "task_1",
        "payload": {"text": "progress"},
    }

    assert classify_gui_stream_event(event, mode="full") == "work_area"


def test_supervisor_and_synthesizer_delta_without_task_id_route_to_answer():
    supervisor_event = {
        "event_type": "AgentMessageDelta",
        "agent": "supervisor",
        "payload": {"text": "final"},
    }
    synthesizer_event = {
        "event_type": "AgentMessageDelta",
        "agent": "final_synthesizer",
        "payload": {"text": "summary"},
    }

    assert classify_gui_stream_event(supervisor_event, mode="full") == "answer"
    assert classify_gui_stream_event(synthesizer_event, mode="serial") == "answer"


def test_factory_named_supervisor_and_synthesizer_delta_route_to_answer():
    supervisor_event = {
        "event_type": "AgentMessageDelta",
        "agent": "full_supervisor_agent",
        "payload": {"text": "final"},
    }
    synthesizer_event = {
        "event_type": "AgentMessageDelta",
        "agent": "final_synthesizer_agent",
        "payload": {"text": "summary"},
    }

    assert classify_gui_stream_event(supervisor_event, mode="full") == "answer"
    assert classify_gui_stream_event(synthesizer_event, mode="serial") == "answer"


def test_unknown_delta_without_task_id_is_ignored():
    event = {
        "event_type": "AgentMessageDelta",
        "agent": "unknown",
        "payload": {"text": "do not guess"},
    }

    assert classify_gui_stream_event(event, mode="full") == "ignore"


def test_lifecycle_events_route_to_status():
    assert classify_gui_stream_event({"event_type": "TurnStarted"}) == "status"
    assert classify_gui_stream_event({"event_type": "PlanningCompleted"}) == "status"
    assert classify_gui_stream_event({"event_type": "ToolApprovalPre"}) == "status"


def test_worker_lifecycle_event_with_task_id_routes_to_work_area():
    event = {"event_type": "TaskStarted", "task_id": "task_1"}

    assert classify_gui_stream_event(event, mode="full") == "work_area"
