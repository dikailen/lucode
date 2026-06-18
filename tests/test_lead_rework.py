from __future__ import annotations

import asyncio

from planning.planner_schema import PlannedTask, PlannerResult
from runtime.agent.supervisor import WorkerReport
from runtime.execution.lead_reviewer import (
    LeadReviewFinding,
    plan_lead_rework_actions,
    review_worker_reports,
)
from runtime.execution.pipeline import PipelineRunState


def _task(task_id: str = "worker-a") -> PlannedTask:
    return PlannedTask(
        id=task_id,
        title="Fix auth",
        instruction="Fix auth validation",
        skill_id="code_engineer",
        model="worker",
        mcp=["workspace_edit", "project_filesystem_readonly"],
        write_intent=["runtime/auth.py"],
    )


def test_lead_rework_actions_target_reworkable_findings():
    findings = [
        LeadReviewFinding(
            task_id="worker-a",
            severity="error",
            kind="task_failed",
            message="failed",
            evidence="traceback",
        )
    ]

    actions, limits = plan_lead_rework_actions(findings, {"worker-a": 0}, max_attempts=2)

    assert limits == []
    assert len(actions) == 1
    assert actions[0].task_id == "worker-a"
    assert actions[0].attempt == 1
    assert "task_failed" in actions[0].finding_kinds


def test_lead_rework_actions_stop_at_hard_limit():
    findings = [
        LeadReviewFinding(
            task_id="worker-a",
            severity="error",
            kind="unauthorized_write",
            message="out of scope",
            evidence="runtime/secret.py",
        )
    ]

    actions, limits = plan_lead_rework_actions(findings, {"worker-a": 2}, max_attempts=2)

    assert actions == []
    assert len(limits) == 1
    assert limits[0].task_id == "worker-a"
    assert limits[0].max_attempts == 2
    assert "unauthorized_write" in limits[0].finding_kinds


def test_pipeline_record_task_result_clears_previous_task_error():
    task = _task()
    plan = PlannerResult(route_type="multi_agent", reason="", refined_request="fix", tasks=[task])
    state = PipelineRunState.create("fix", plan, project_root=None, mode="full")

    state.record_task_error(task, "first failure")
    state.record_task_result(task, "fixed")

    assert state.tasks[0].status == "completed"
    assert state.tasks[0].error == ""
    assert state.errors == []


def test_lead_rework_runner_replaces_failed_report_after_success(monkeypatch, tmp_path):
    from runtime.execution import multi_agent_runner as runner

    task = _task()
    plan = PlannerResult(route_type="multi_agent", reason="", refined_request="fix", tasks=[task])
    state = PipelineRunState.create("fix", plan, project_root=tmp_path, mode="full")
    state.record_task_error(task, "initial failure")
    first_report = WorkerReport(task_id=task.id, status="failed", summary="initial failure")
    findings = review_worker_reports(plan.tasks, [first_report])

    class Workspace:
        def __init__(self):
            self.outputs = []

        def write_task_output(self, task_id, title, output):
            self.outputs.append((task_id, title, output))

    async def fake_run_planned_task(*args, **kwargs):
        rework_task = args[1]
        assert "主管打回重做要求" in rework_task.instruction
        state.record_task_result(rework_task, "fixed with evidence")
        return rework_task.title, "fixed with evidence"

    def fake_build_worker_report(task, output, *, run_state=None):
        return WorkerReport(
            task_id=task.id,
            status="completed",
            summary=output,
            files_read=["runtime/auth.py"],
            files_written=["runtime/auth.py"],
        )

    monkeypatch.setattr(runner, "_run_planned_task", fake_run_planned_task)
    monkeypatch.setattr(runner, "build_worker_report", fake_build_worker_report)

    worker_outputs, worker_reports, new_findings, limits = asyncio.run(
        runner._run_lead_rework_until_stable(
            refined_request="fix",
            plan=plan,
            project_root=tmp_path,
            factory=object(),
            hooks=None,
            run_agent=None,
            run_state=state,
            ledger=None,
            workspace=Workspace(),
            worker_outputs=[(task.id, task.title, "initial failure")],
            worker_reports=[first_report],
            lead_review_findings=findings,
            approval_policy_factory=None,
            mode="full",
            show_progress=False,
            attempt=1,
        )
    )

    assert limits == []
    assert new_findings == []
    assert worker_reports[0].status == "completed"
    assert worker_outputs[0][2] == "fixed with evidence"
    assert state.errors == []


def test_lead_rework_runner_stops_at_hard_limit(monkeypatch, tmp_path):
    from runtime.execution import multi_agent_runner as runner

    task = _task()
    plan = PlannerResult(route_type="multi_agent", reason="", refined_request="fix", tasks=[task])
    state = PipelineRunState.create("fix", plan, project_root=tmp_path, mode="full")
    first_report = WorkerReport(task_id=task.id, status="failed", summary="still broken")
    findings = review_worker_reports(plan.tasks, [first_report])
    calls = []

    class Workspace:
        def write_task_output(self, task_id, title, output):
            del task_id, title, output

    async def fake_run_planned_task(*args, **kwargs):
        rework_task = args[1]
        del kwargs
        calls.append(rework_task.instruction)
        state.record_task_error(rework_task, "still broken")
        return rework_task.title, "still broken"

    def fake_build_worker_report(task, output, *, run_state=None):
        del output, run_state
        return WorkerReport(task_id=task.id, status="failed", summary="still broken")

    monkeypatch.setattr(runner, "_run_planned_task", fake_run_planned_task)
    monkeypatch.setattr(runner, "build_worker_report", fake_build_worker_report)

    worker_outputs, worker_reports, new_findings, limits = asyncio.run(
        runner._run_lead_rework_until_stable(
            refined_request="fix",
            plan=plan,
            project_root=tmp_path,
            factory=object(),
            hooks=None,
            run_agent=None,
            run_state=state,
            ledger=None,
            workspace=Workspace(),
            worker_outputs=[(task.id, task.title, "still broken")],
            worker_reports=[first_report],
            lead_review_findings=findings,
            approval_policy_factory=None,
            mode="full",
            show_progress=False,
            attempt=1,
            max_attempts=2,
        )
    )

    assert len(calls) == 2
    assert len(limits) == 1
    assert limits[0].task_id == task.id
    assert new_findings
    assert worker_reports[0].status == "failed"


def test_lead_rework_runner_accepts_supervisor_judgement_without_rule_rework(monkeypatch, tmp_path):
    from runtime.execution import multi_agent_runner as runner

    task = _task()
    plan = PlannerResult(route_type="multi_agent", reason="", refined_request="fix", tasks=[task])
    state = PipelineRunState.create("fix", plan, project_root=tmp_path, mode="full")
    first_report = WorkerReport(task_id=task.id, status="failed", summary="false positive failure")
    findings = review_worker_reports(plan.tasks, [first_report])

    class Workspace:
        def write_task_output(self, task_id, title, output):
            raise AssertionError("accepted supervisor judgement should not run rework")

    async def fake_run_planned_task(*args, **kwargs):
        raise AssertionError("accepted supervisor judgement should not run rework")

    async def fake_supervisor_rework_decider(*args, **kwargs):
        return {"decision": "accept", "reason": "finding is acceptable for this goal"}

    monkeypatch.setattr(runner, "_run_planned_task", fake_run_planned_task)

    worker_outputs, worker_reports, new_findings, limits = asyncio.run(
        runner._run_lead_rework_until_stable(
            refined_request="fix",
            plan=plan,
            project_root=tmp_path,
            factory=object(),
            hooks=None,
            run_agent=None,
            run_state=state,
            ledger=None,
            workspace=Workspace(),
            worker_outputs=[(task.id, task.title, "false positive failure")],
            worker_reports=[first_report],
            lead_review_findings=findings,
            approval_policy_factory=None,
            supervisor_rework_decider=fake_supervisor_rework_decider,
            mode="full",
            show_progress=False,
            attempt=1,
        )
    )

    assert limits == []
    assert new_findings == []
    assert worker_reports == [first_report]
    assert worker_outputs[0][2] == "false positive failure"


def test_lead_rework_runner_uses_supervisor_rework_instruction(monkeypatch, tmp_path):
    from runtime.execution import multi_agent_runner as runner

    task = _task()
    plan = PlannerResult(route_type="multi_agent", reason="", refined_request="fix", tasks=[task])
    state = PipelineRunState.create("fix", plan, project_root=tmp_path, mode="full")
    first_report = WorkerReport(task_id=task.id, status="failed", summary="missing evidence")
    findings = review_worker_reports(plan.tasks, [first_report])

    class Workspace:
        def __init__(self):
            self.outputs = []

        def write_task_output(self, task_id, title, output):
            self.outputs.append((task_id, title, output))

    async def fake_supervisor_rework_decider(*args, **kwargs):
        return {
            "decision": "rework",
            "reason": "missing evidence",
            "rework": [{"task_id": task.id, "instruction": "Add concrete evidence from runtime/auth.py."}],
        }

    async def fake_run_planned_task(*args, **kwargs):
        rework_task = args[1]
        assert "Add concrete evidence from runtime/auth.py." in rework_task.instruction
        state.record_task_result(rework_task, "fixed with evidence")
        return rework_task.title, "fixed with evidence"

    def fake_build_worker_report(task, output, *, run_state=None):
        return WorkerReport(
            task_id=task.id,
            status="completed",
            summary=output,
            files_read=["runtime/auth.py"],
            files_written=["runtime/auth.py"],
        )

    monkeypatch.setattr(runner, "_run_planned_task", fake_run_planned_task)
    monkeypatch.setattr(runner, "build_worker_report", fake_build_worker_report)

    worker_outputs, worker_reports, new_findings, limits = asyncio.run(
        runner._run_lead_rework_until_stable(
            refined_request="fix",
            plan=plan,
            project_root=tmp_path,
            factory=object(),
            hooks=None,
            run_agent=None,
            run_state=state,
            ledger=None,
            workspace=Workspace(),
            worker_outputs=[(task.id, task.title, "missing evidence")],
            worker_reports=[first_report],
            lead_review_findings=findings,
            approval_policy_factory=None,
            supervisor_rework_decider=fake_supervisor_rework_decider,
            mode="full",
            show_progress=False,
            attempt=1,
        )
    )

    assert limits == []
    assert new_findings == []
    assert worker_reports[0].status == "completed"
    assert worker_outputs[0][2] == "fixed with evidence"


def test_lead_rework_runner_supervisor_rework_stops_at_hard_limit(monkeypatch, tmp_path):
    from runtime.execution import multi_agent_runner as runner

    task = _task()
    plan = PlannerResult(route_type="multi_agent", reason="", refined_request="fix", tasks=[task])
    state = PipelineRunState.create("fix", plan, project_root=tmp_path, mode="full")
    first_report = WorkerReport(task_id=task.id, status="failed", summary="still broken")
    findings = review_worker_reports(plan.tasks, [first_report])
    calls = []

    class Workspace:
        def write_task_output(self, task_id, title, output):
            del task_id, title, output

    async def fake_supervisor_rework_decider(*args, **kwargs):
        return {
            "decision": "rework",
            "reason": "still broken",
            "rework": [{"task_id": task.id, "instruction": "Try again with stronger evidence."}],
        }

    async def fake_run_planned_task(*args, **kwargs):
        rework_task = args[1]
        calls.append(rework_task.instruction)
        state.record_task_error(rework_task, "still broken")
        return rework_task.title, "still broken"

    def fake_build_worker_report(task, output, *, run_state=None):
        del output, run_state
        return WorkerReport(task_id=task.id, status="failed", summary="still broken")

    monkeypatch.setattr(runner, "_run_planned_task", fake_run_planned_task)
    monkeypatch.setattr(runner, "build_worker_report", fake_build_worker_report)

    worker_outputs, worker_reports, new_findings, limits = asyncio.run(
        runner._run_lead_rework_until_stable(
            refined_request="fix",
            plan=plan,
            project_root=tmp_path,
            factory=object(),
            hooks=None,
            run_agent=None,
            run_state=state,
            ledger=None,
            workspace=Workspace(),
            worker_outputs=[(task.id, task.title, "still broken")],
            worker_reports=[first_report],
            lead_review_findings=findings,
            approval_policy_factory=None,
            supervisor_rework_decider=fake_supervisor_rework_decider,
            mode="full",
            show_progress=False,
            attempt=1,
            max_attempts=2,
        )
    )

    assert len(calls) == 2
    assert len(limits) == 1
    assert limits[0].task_id == task.id
    assert new_findings
    assert worker_reports[0].status == "failed"


def test_lead_rework_runner_falls_back_when_supervisor_judge_unavailable(monkeypatch, tmp_path):
    from runtime.execution import multi_agent_runner as runner

    task = _task()
    plan = PlannerResult(route_type="multi_agent", reason="", refined_request="fix", tasks=[task])
    state = PipelineRunState.create("fix", plan, project_root=tmp_path, mode="full")
    first_report = WorkerReport(task_id=task.id, status="failed", summary="initial failure")
    findings = review_worker_reports(plan.tasks, [first_report])
    calls = []

    class Workspace:
        def write_task_output(self, task_id, title, output):
            del task_id, title, output

    async def failing_supervisor_rework_decider(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("supervisor unavailable")

    async def fake_run_planned_task(*args, **kwargs):
        rework_task = args[1]
        calls.append(rework_task.instruction)
        state.record_task_result(rework_task, "fixed after fallback")
        return rework_task.title, "fixed after fallback"

    def fake_build_worker_report(task, output, *, run_state=None):
        return WorkerReport(
            task_id=task.id,
            status="completed",
            summary=output,
            files_read=["runtime/auth.py"],
            files_written=["runtime/auth.py"],
        )

    monkeypatch.setattr(runner, "_run_planned_task", fake_run_planned_task)
    monkeypatch.setattr(runner, "build_worker_report", fake_build_worker_report)

    worker_outputs, worker_reports, new_findings, limits = asyncio.run(
        runner._run_lead_rework_until_stable(
            refined_request="fix",
            plan=plan,
            project_root=tmp_path,
            factory=object(),
            hooks=None,
            run_agent=None,
            run_state=state,
            ledger=None,
            workspace=Workspace(),
            worker_outputs=[(task.id, task.title, "initial failure")],
            worker_reports=[first_report],
            lead_review_findings=findings,
            approval_policy_factory=None,
            supervisor_rework_decider=failing_supervisor_rework_decider,
            mode="full",
            show_progress=False,
            attempt=1,
        )
    )

    assert len(calls) == 1
    assert limits == []
    assert new_findings == []
    assert worker_reports[0].status == "completed"


def test_supervisor_rework_prompt_includes_findings_blackboard_and_attempts(tmp_path):
    from runtime.execution import multi_agent_runner as runner

    task = _task()
    plan = PlannerResult(route_type="multi_agent", reason="", refined_request="fix auth", tasks=[task])
    state = PipelineRunState.create("fix auth", plan, project_root=tmp_path, mode="full")
    state.run_context.put_insight("controller saw auth.py missing evidence", source="supervisor", key="probe")
    report = WorkerReport(task_id=task.id, status="failed", summary="missing evidence")
    findings = review_worker_reports(plan.tasks, [report])

    prompt = runner._render_supervisor_rework_prompt(
        refined_request="fix auth",
        plan=plan,
        run_state=state,
        worker_reports=[report],
        lead_review_findings=findings,
        attempts_by_task={task.id: 1},
        max_attempts=2,
    )

    assert "## WorkerReport" in prompt
    assert "## LeadReview Findings" in prompt
    assert "missing_evidence" in prompt
    assert "controller saw auth.py missing evidence" in prompt
    assert '"max_attempts": 2' in prompt
