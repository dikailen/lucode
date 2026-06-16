from __future__ import annotations

import asyncio
import json

from planning.planner_schema import PlannedTask, PlannerResult
from runtime.agent.approval import run_with_approval
from runtime.agent.approval_policy import FullModeApprovalPolicy
from runtime.execution.supervisor_observer import build_supervisor_plan_view
from runtime.execution.supervisor_scheduler import supervisor_execution_batches_for_full


def _write_task(task_id: str, path: str) -> PlannedTask:
    return PlannedTask(
        id=task_id,
        title=task_id,
        instruction=f"modify {path}",
        skill_id="code_engineer",
        model="worker",
        mcp=["workspace_edit"],
        parallel_group=1,
        write_intent=[path],
    )


def test_supervisor_gate_approves_declared_workspace_write():
    policy = FullModeApprovalPolicy.from_task(_write_task("worker-auth", "runtime/auth.py"))

    decision = policy.decide(
        "workspace_edit.write_file",
        json.dumps({"path": "runtime/auth.py", "content": "x = 1", "reason": "implement auth"}),
    )

    assert decision.approve is True
    assert decision.reason == "supervisor_gate_approved"


def test_supervisor_gate_rejects_out_of_scope_workspace_write():
    policy = FullModeApprovalPolicy.from_task(_write_task("worker-auth", "runtime/auth.py"))

    decision = policy.decide(
        "workspace_edit.write_file",
        json.dumps({"path": "runtime/secrets.py", "content": "TOKEN = 'x'", "reason": "expand scope"}),
    )

    assert decision.approve is False
    assert decision.reject is True
    assert decision.reason == "supervisor_gate_rejected"
    assert "runtime/secrets.py" in decision.rejection_message
    assert "worker-auth" in decision.rejection_message


def test_supervisor_gate_can_be_disabled_for_legacy_prompt_path(monkeypatch):
    monkeypatch.setenv("LUCODE_FULL_SUPERVISOR_GATE", "0")
    policy = FullModeApprovalPolicy.from_task(_write_task("worker-auth", "runtime/auth.py"))

    decision = policy.decide(
        "workspace_edit.write_file",
        json.dumps({"path": "runtime/secrets.py", "content": "TOKEN = 'x'"}),
    )

    assert decision.approve is False
    assert decision.reject is False
    assert decision.reason == "write_path_out_of_scope"


def test_supervisor_conflict_view_marks_conflicts_as_serialized():
    tasks = [_write_task("worker-a", "runtime/auth.py"), _write_task("worker-b", "runtime")]
    plan = PlannerResult(route_type="multi_agent", reason="", refined_request="edit files", tasks=tasks)

    view = build_supervisor_plan_view(plan, mode="full")
    batches = supervisor_execution_batches_for_full(tasks)

    assert len(batches) == 2
    assert view.conflicts
    assert view.decisions[0].action == "serialize_conflict"
    assert "observation-only" not in " ".join(view.notes).lower()


def test_run_with_approval_rejects_out_of_scope_write_without_executing(monkeypatch):
    class FakeItem:
        qualified_name = "workspace_edit.write_file"
        name = "write_file"
        arguments = json.dumps({"path": "runtime/secrets.py", "content": "TOKEN = 'x'"})

    class FakeState:
        def __init__(self):
            self.approved = []
            self.rejected = []

        def approve(self, item):
            self.approved.append(item)

        def reject(self, item, rejection_message=""):
            self.rejected.append((item, rejection_message))

    state = FakeState()

    class FirstResult:
        interruptions = [FakeItem()]

        def to_state(self):
            return state

    class FinalResult:
        interruptions = []
        final_output = "worker adapted"

    calls = [FirstResult(), FinalResult()]

    async def fake_run_agent_once(*args, **kwargs):
        del args, kwargs
        return calls.pop(0)

    monkeypatch.setattr("runtime.agent.approval.run_agent_once", fake_run_agent_once)
    hooks = type("Hooks", (), {"tool_events": []})()
    policy = FullModeApprovalPolicy.from_task(_write_task("worker-auth", "runtime/auth.py"))

    result = asyncio.run(run_with_approval("agent", "input", hooks, approval_policy=policy))

    assert result.final_output == "worker adapted"
    assert state.approved == []
    assert len(state.rejected) == 1
    assert "runtime/secrets.py" in state.rejected[0][1]
    assert hooks.tool_events[-1].status == "supervisor_rejected"
