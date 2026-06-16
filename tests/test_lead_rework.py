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
