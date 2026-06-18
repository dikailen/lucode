from __future__ import annotations

from typing import Literal


GuiStreamRoute = Literal["answer", "work_area", "status", "ignore"]


WORK_AREA_EVENTS = {"TaskStarted", "TaskCompleted", "TaskFailed", "ToolInvoked", "FastPathUsed"}
STATUS_EVENTS = {
    "TurnStarted",
    "TurnEnded",
    "PlanningStarted",
    "PlanningCompleted",
    "PlanningFailed",
    "ToolApprovalPre",
    "ToolApprovalPost",
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
ANSWER_DELTA_AGENTS = {
    "supervisor",
    "full_supervisor_agent",
    "final_synthesizer",
    "final_synthesizer_agent",
    "solo",
    "solo_agent",
}


def classify_gui_stream_event(event: dict, mode: str = "") -> GuiStreamRoute:
    """Classify runtime events before GUI streaming is wired into widgets."""

    event_type = str(event.get("event_type") or "")
    task_id = str(event.get("task_id") or "")
    agent = str(event.get("agent") or "")
    normalized_mode = str(mode or "").strip().lower()

    if event_type == "AgentMessageDelta":
        if normalized_mode == "solo" or agent == "solo":
            return "answer"
        if task_id:
            return "work_area"
        if agent in ANSWER_DELTA_AGENTS:
            return "answer"
        return "ignore"

    if event_type in WORK_AREA_EVENTS and task_id:
        return "work_area"
    if event_type in STATUS_EVENTS:
        return "status"
    return "ignore"
