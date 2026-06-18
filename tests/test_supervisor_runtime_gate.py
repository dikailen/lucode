from __future__ import annotations

import asyncio
import json

import pytest

from planning.planner_schema import PlannedTask, PlannerResult
from runtime.agent.approval import run_with_approval
from runtime.agent.approval_policy import FullModeApprovalPolicy, SupervisorApprovalRequest
from runtime.agent.supervisor import WorkerReport
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
    assert decision.reject is False
    assert decision.requires_supervisor is True
    assert decision.reason == "supervisor_gate_requires_agent"
    assert decision.supervisor_request is not None
    assert decision.supervisor_request.target_paths == ["runtime/secrets.py"]
    assert decision.fallback_decision is not None
    assert decision.fallback_decision.reject is True
    assert "worker-auth" in decision.fallback_decision.rejection_message


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


def test_run_with_approval_allows_out_of_scope_write_when_supervisor_agent_approves(monkeypatch):
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
        final_output = "worker wrote approved file"

    calls = [FirstResult(), FinalResult()]
    captured = {}

    async def fake_run_agent_once(*args, **kwargs):
        del args, kwargs
        return calls.pop(0)

    async def fake_supervisor_decider(request, policy, tool_name, arguments):
        captured["request"] = request
        captured["policy"] = policy
        captured["tool_name"] = tool_name
        captured["arguments"] = arguments
        return "approve", "主管确认该越界写入属于用户目标。"

    monkeypatch.setattr("runtime.agent.approval.run_agent_once", fake_run_agent_once)
    hooks = type("Hooks", (), {"tool_events": []})()
    policy = FullModeApprovalPolicy.from_task(_write_task("worker-auth", "runtime/auth.py"))

    result = asyncio.run(
        run_with_approval(
            "agent",
            "input",
            hooks,
            approval_policy=policy,
            supervisor_approval_decider=fake_supervisor_decider,
        )
    )

    assert result.final_output == "worker wrote approved file"
    assert isinstance(captured["request"], SupervisorApprovalRequest)
    assert captured["request"].target_paths == ["runtime/secrets.py"]
    assert state.approved == [FirstResult.interruptions[0]]
    assert state.rejected == []
    assert hooks.tool_events[-1].status == "supervisor_agent_approved"


def test_run_with_approval_falls_back_when_supervisor_agent_unavailable(monkeypatch):
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

    async def failing_supervisor_decider(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("supervisor unavailable")

    monkeypatch.setattr("runtime.agent.approval.run_agent_once", fake_run_agent_once)
    hooks = type("Hooks", (), {"tool_events": []})()
    policy = FullModeApprovalPolicy.from_task(_write_task("worker-auth", "runtime/auth.py"))

    result = asyncio.run(
        run_with_approval(
            "agent",
            "input",
            hooks,
            approval_policy=policy,
            supervisor_approval_decider=failing_supervisor_decider,
        )
    )

    assert result.final_output == "worker adapted"
    assert state.approved == []
    assert len(state.rejected) == 1
    assert "runtime/secrets.py" in state.rejected[0][1]
    assert hooks.tool_events[-1].status == "supervisor_rejected"


@pytest.mark.parametrize(
    ("agent_decision", "reason"),
    [
        ("reject", "主管拒绝该越界写入。"),
        ("serialize", "主管要求先串行化该冲突写入。"),
    ],
)
def test_run_with_approval_rejects_when_supervisor_agent_blocks_or_serializes(monkeypatch, agent_decision, reason):
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
        final_output = "worker adapted after supervisor decision"

    calls = [FirstResult(), FinalResult()]

    async def fake_run_agent_once(*args, **kwargs):
        del args, kwargs
        return calls.pop(0)

    async def fake_supervisor_decider(*args, **kwargs):
        del args, kwargs
        return agent_decision, reason

    monkeypatch.setattr("runtime.agent.approval.run_agent_once", fake_run_agent_once)
    hooks = type("Hooks", (), {"tool_events": []})()
    policy = FullModeApprovalPolicy.from_task(_write_task("worker-auth", "runtime/auth.py"))

    result = asyncio.run(
        run_with_approval(
            "agent",
            "input",
            hooks,
            approval_policy=policy,
            supervisor_approval_decider=fake_supervisor_decider,
        )
    )

    assert result.final_output == "worker adapted after supervisor decision"
    assert state.approved == []
    assert len(state.rejected) == 1
    assert state.rejected[0][1] == reason
    assert hooks.tool_events[-1].status == "supervisor_agent_rejected"


def test_full_multi_agent_passes_supervisor_decider_to_worker(monkeypatch, tmp_path):
    from runtime.execution import multi_agent_runner as runner
    from runtime.execution.pipeline import PipelineRunState

    task = _write_task("worker-auth", "runtime/auth.py")
    plan = PlannerResult(
        route_type="multi_agent",
        reason="team",
        refined_request="edit auth",
        tasks=[task],
        memory_interface={
            "execution_contract": {
                "supervisor_route": "team",
                "summary_helper": {"enabled": False, "reason": "lead_supervisor_final_answer"},
            }
        },
    )
    captured = {}

    async def fake_run_planned_task(*args, **kwargs):
        captured["approval_policy"] = kwargs.get("approval_policy")
        captured["execution_mode"] = kwargs.get("execution_mode")
        return "Auth", "done"

    monkeypatch.setattr(runner, "_run_planned_task", fake_run_planned_task)
    monkeypatch.setattr(
        runner,
        "build_worker_report",
        lambda task, output, run_state=None: WorkerReport(task_id=task.id, status="completed", summary=output),
    )
    async def fake_finalize_with_supervisor_agent(**kwargs):
        del kwargs
        return ""

    monkeypatch.setattr(runner, "_finalize_with_supervisor_agent", fake_finalize_with_supervisor_agent)

    class FakeFactory:
        def create_supervisor_agent(self, model_id, readonly_servers):
            del model_id, readonly_servers
            return object()

    async def fake_run_agent(*args, **kwargs):
        del args, kwargs
        return type("Result", (), {"final_output": '{"decision":"approve","reason":"ok"}'})()

    output = asyncio.run(
        runner._run_multi_agent(
            "edit auth",
            plan,
            tmp_path,
            "supervisor-model",
            factory=FakeFactory(),
            hooks=None,
            run_agent=fake_run_agent,
            run_state=PipelineRunState.create("edit auth", plan, project_root=tmp_path, mode="full"),
            execution_mode="full",
            show_progress=False,
            approval_policy_factory=FullModeApprovalPolicy.from_task,
        )
    )

    assert "主管最终汇报" in output
    assert captured["execution_mode"] == "full"
    assert captured["approval_policy"] is not None
    assert callable(getattr(captured["approval_policy"], "supervisor_approval_decider", None))


def test_supervisor_approval_prompt_includes_conflict_view():
    from runtime.execution import multi_agent_runner as runner

    tasks = [_write_task("worker-a", "runtime/auth.py"), _write_task("worker-b", "runtime")]
    plan = PlannerResult(route_type="multi_agent", reason="", refined_request="edit auth files", tasks=tasks)
    view = build_supervisor_plan_view(plan, mode="full")
    request = SupervisorApprovalRequest(
        task_id="worker-b",
        tool_name="workspace_edit.write_file",
        operation="write",
        target_paths=["runtime/auth.py"],
        reason="conflicting write request",
    )

    prompt = runner._render_supervisor_approval_prompt(
        refined_request="edit auth files",
        plan=plan,
        run_state=None,
        request=request,
        tool_name="workspace_edit.write_file",
        arguments=json.dumps({"path": "runtime/auth.py", "content": "x = 1"}),
        supervisor_view=view,
    )

    assert "## 冲突视图" in prompt
    assert "write_conflict" in prompt
    assert "worker-a" in prompt
    assert "worker-b" in prompt
