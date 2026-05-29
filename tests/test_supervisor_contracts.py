from __future__ import annotations

import asyncio
import json
from pathlib import Path
import unittest

from planning.planner_schema import PlannedTask, PlannerResult
from runtime.events import ExecutionEventBus


class FullSupervisorDataContractTests(unittest.TestCase):
    def test_supervisor_data_contracts_round_trip_without_execution_state(self):
        from runtime.agent.spec import TaskSpec
        from runtime.agent.supervisor import (
            ContextPack,
            ResourceLease,
            SupervisorDecision,
            SupervisorPlanView,
            WorkerReport,
        )

        task = TaskSpec(
            task_id="inspect_runtime",
            goal="Inspect the full mode runner.",
            mode_hint="full",
            read_intent=["runtime/execution"],
            write_intent=[],
            toolset_id="readonly_project_analysis",
            acceptance_criteria=["Explain current parallel behavior"],
        )
        lease = ResourceLease.read("runtime/execution", owner_task_id="inspect_runtime", parallel_group=1)
        pack = ContextPack(
            pack_id="ctx_shared_runtime",
            summary="Shared runtime execution summary.",
            shared_files=[{"path": "runtime/execution/multi_agent_runner.py", "summary": "Runs batches."}],
            source_task_ids=["inspect_runtime"],
        )
        report = WorkerReport(
            task_id="inspect_runtime",
            status="completed",
            summary="The runner still uses gather for parallel batches.",
            evidence_refs=["ctx_shared_runtime"],
        )
        decision = SupervisorDecision(
            action="observe",
            reason="No behavior change in v0.",
            affected_task_ids=["inspect_runtime"],
        )
        view = SupervisorPlanView(
            mode="full",
            route_type="multi_agent",
            task_specs=[task],
            resource_leases=[lease],
            context_packs=[pack],
            worker_reports=[report],
            decisions=[decision],
        )

        restored = SupervisorPlanView.from_dict(view.to_dict())

        self.assertEqual(restored, view)
        self.assertEqual(restored.task_specs[0].toolset_id, "readonly_project_analysis")
        self.assertEqual(restored.resource_leases[0].lease_type, "read")
        self.assertFalse(restored.has_conflicts)


class FullSupervisorObserverTests(unittest.TestCase):
    def test_execution_contract_strips_mutating_tools_from_explicit_readonly_plan(self):
        from runtime.execution.execution_contract import normalize_execution_contract

        plan = PlannerResult(
            route_type="multi_agent",
            reason="read-only inspection",
            refined_request="Inspect runtime files without changing anything.",
            tasks=[
                PlannedTask(
                    id="inspect_runtime",
                    title="Inspect runtime",
                    instruction="Read-only inspect runtime/execution; do not modify files or run tests.",
                    skill_id="project_explorer",
                    model="executor",
                    mcp=["project_filesystem_readonly", "workspace_edit", "command_runner"],
                    parallel_group=1,
                    read_set=["runtime/execution"],
                    write_intent=["runtime/execution/multi_agent_runner.py"],
                )
            ],
        )

        decision = normalize_execution_contract(
            plan,
            "请只读检查 runtime/execution，不要修改文件，不要运行测试。",
            mode="full",
        )

        task = plan.tasks[0]
        self.assertTrue(decision.readonly_hard_constraint)
        self.assertEqual(decision.full_supervisor_route, "single")
        self.assertNotIn("workspace_edit", task.mcp)
        self.assertNotIn("command_runner", task.mcp)
        self.assertIn("project_filesystem_readonly", task.mcp)
        self.assertIn("code_locator", task.mcp)
        self.assertEqual(task.write_intent, [])

    def test_execution_contract_does_not_treat_readonly_tool_name_as_user_readonly_intent(self):
        from runtime.execution.execution_contract import normalize_execution_contract

        plan = PlannerResult(
            route_type="single_agent",
            reason="Fix JavaScript syntax error.",
            refined_request="Fix src/game.js and run node --check src/game.js.",
            tasks=[
                PlannedTask(
                    id="fix_snake",
                    title="Fix snake game",
                    instruction="Fix the syntax error in src/game.js and verify with node --check src/game.js.",
                    skill_id="jpc_now_skill",
                    model="executor",
                    mcp=["code_locator", "project_filesystem_readonly", "workspace_edit", "command_runner"],
                    read_set=["src/game.js"],
                    write_intent=["src/game.js"],
                )
            ],
        )

        decision = normalize_execution_contract(plan, "请修复 src/game.js，可以修改必要代码并运行 node --check。", mode="full")

        self.assertFalse(decision.readonly_hard_constraint)
        self.assertIn("workspace_edit", plan.tasks[0].mcp)
        self.assertIn("command_runner", plan.tasks[0].mcp)
        self.assertEqual(plan.tasks[0].write_intent, ["src/game.js"])

    def test_execution_contract_removes_opportunistic_fix_acceptance_from_code_repair(self):
        from runtime.execution.execution_contract import normalize_execution_contract

        plan = PlannerResult(
            route_type="single_agent",
            reason="Fix JavaScript syntax error.",
            refined_request="Fix src/game.js and verify with node --check src/game.js.",
            tasks=[
                PlannedTask(
                    id="fix_snake",
                    title="Fix snake game",
                    instruction="Fix src/game.js and verify with node --check src/game.js.",
                    skill_id="jpc_now_skill",
                    model="executor",
                    mcp=["code_locator", "project_filesystem_readonly", "workspace_edit", "command_runner"],
                    read_set=["src/game.js"],
                    write_intent=["src/game.js"],
                    acceptance_criteria=[
                        "node --check src/game.js passes",
                        "Fix at least one potential bug or style issue if any",
                    ],
                )
            ],
        )

        normalize_execution_contract(
            plan,
            "Use full mode to fix src/game.js. Verify with exactly node --check src/game.js.",
            mode="full",
        )

        task = plan.tasks[0]
        joined_acceptance = "\n".join(task.acceptance_criteria).lower()
        self.assertIn("node --check src/game.js", joined_acceptance)
        self.assertNotIn("potential bug", joined_acceptance)
        self.assertIn("只修复用户请求或验证失败直接相关的问题", task.instruction)

    def test_execution_contract_marks_full_multi_task_as_supervised_team_without_required_synthesizer(self):
        from runtime.execution.execution_contract import normalize_execution_contract

        plan = PlannerResult(
            route_type="multi_agent",
            reason="parallel read",
            refined_request="Inspect two runtime areas.",
            needs_synthesis=True,
            synthesis_instruction="Summarize all outputs.",
            tasks=[
                PlannedTask(
                    id="inspect_execution",
                    title="Inspect execution",
                    instruction="Inspect runtime/execution.",
                    skill_id="project_explorer",
                    model="executor",
                    mcp=["project_filesystem_readonly"],
                    parallel_group=1,
                    read_set=["runtime/execution"],
                ),
                PlannedTask(
                    id="inspect_agent",
                    title="Inspect agent",
                    instruction="Inspect runtime/agent.",
                    skill_id="project_explorer",
                    model="executor",
                    mcp=["project_filesystem_readonly"],
                    parallel_group=1,
                    read_set=["runtime/agent"],
                ),
            ],
        )

        decision = normalize_execution_contract(plan, "full 模式并行只读分析两个模块。", mode="full")

        self.assertEqual(decision.full_supervisor_route, "team")
        self.assertFalse(plan.needs_synthesis)
        self.assertEqual(plan.synthesis_instruction, "")
        self.assertFalse(plan.memory_interface["execution_contract"]["summary_helper"]["enabled"])
        self.assertEqual(
            plan.memory_interface["execution_contract"]["supervisor_route"],
            "team",
        )

    def test_validator_allows_supervised_full_team_without_summary_helper(self):
        from planning.plan_validator import validate_plan
        from runtime.execution.execution_contract import normalize_execution_contract
        from runtime.safety.privacy import PrivacyPolicy

        plan = PlannerResult(
            route_type="multi_agent",
            reason="supervised full team",
            refined_request="Use full team workers and let the supervisor finalize.",
            needs_synthesis=True,
            synthesis_instruction="Old planner summary instruction.",
            tasks=[
                PlannedTask(
                    id="expert_a",
                    title="Expert A",
                    instruction="Read runtime/execution only.",
                    skill_id="jpc_now_skill",
                    model="deepseek_v4_flash_model",
                    mcp=["project_filesystem_readonly", "code_locator"],
                    parallel_group=1,
                    read_set=["runtime/execution"],
                ),
                PlannedTask(
                    id="expert_b",
                    title="Expert B",
                    instruction="Read runtime/agent only.",
                    skill_id="jpc_now_skill",
                    model="deepseek_v4_flash_model",
                    mcp=["project_filesystem_readonly", "code_locator"],
                    parallel_group=1,
                    read_set=["runtime/agent"],
                ),
            ],
        )
        normalize_execution_contract(plan, "full supervised team without summary helper", mode="full")

        validation = validate_plan(plan, privacy_policy=PrivacyPolicy("allow_cloud"))

        self.assertTrue(validation.valid, validation.errors)
        self.assertFalse(plan.needs_synthesis)
        self.assertEqual(plan.synthesis_instruction, "")

    def test_observer_converts_plan_to_task_specs_and_reports_write_conflicts(self):
        from runtime.execution.supervisor_observer import build_supervisor_plan_view

        plan = PlannerResult(
            route_type="multi_agent",
            reason="parallel edit",
            refined_request="Update shared config in full mode.",
            tasks=[
                PlannedTask(
                    id="edit_config_a",
                    title="Edit config A",
                    instruction="Update model config.",
                    skill_id="jpc_now_skill",
                    model="executor",
                    mcp=["workspace_edit"],
                    parallel_group=1,
                    write_intent=[".lucode/config.toml"],
                ),
                PlannedTask(
                    id="edit_config_b",
                    title="Edit config B",
                    instruction="Update the same config.",
                    skill_id="jpc_now_skill",
                    model="executor",
                    mcp=["workspace_edit"],
                    parallel_group=1,
                    write_intent=[".lucode/config.toml"],
                ),
                PlannedTask(
                    id="read_runtime",
                    title="Read runtime",
                    instruction="Read runtime execution code.",
                    skill_id="project_explorer",
                    model="executor",
                    mcp=["project_filesystem_readonly"],
                    parallel_group=1,
                    read_set=["runtime/execution"],
                ),
            ],
        )

        view = build_supervisor_plan_view(plan, mode="full")

        self.assertEqual([task.task_id for task in view.task_specs], ["edit_config_a", "edit_config_b", "read_runtime"])
        self.assertTrue(view.has_conflicts)
        self.assertEqual(view.conflicts[0]["kind"], "write_conflict")
        self.assertEqual(set(view.conflicts[0]["task_ids"]), {"edit_config_a", "edit_config_b"})
        self.assertIn(".lucode/config.toml", view.conflicts[0]["resources"])
        self.assertEqual(
            [lease.lease_type for lease in view.resource_leases if lease.owner_task_id.startswith("edit_config")],
            ["write", "write"],
        )

    def test_observer_builds_supervisor_context_pack_for_team_tasks(self):
        from runtime.execution.supervisor_observer import build_supervisor_plan_view

        plan = PlannerResult(
            route_type="multi_agent",
            reason="parallel read",
            refined_request="Inspect execution and agent runtime as a supervised team.",
            tasks=[
                PlannedTask(
                    id="read_execution",
                    title="Read execution",
                    instruction="Read runtime execution code.",
                    skill_id="project_explorer",
                    model="executor",
                    mcp=["project_filesystem_readonly"],
                    parallel_group=1,
                    read_set=["runtime/execution"],
                ),
                PlannedTask(
                    id="read_agent",
                    title="Read agent",
                    instruction="Read runtime agent code.",
                    skill_id="project_explorer",
                    model="executor",
                    mcp=["project_filesystem_readonly"],
                    parallel_group=1,
                    read_set=["runtime/agent"],
                ),
            ],
        )

        view = build_supervisor_plan_view(plan, mode="full")

        self.assertEqual(len(view.context_packs), 1)
        pack = view.context_packs[0]
        self.assertEqual(pack.pack_id, "supervisor_context_pack")
        self.assertIn("主管公共侦察包", pack.summary)
        self.assertEqual(set(pack.source_task_ids), {"read_execution", "read_agent"})
        self.assertEqual(
            [item["path"] for item in pack.shared_files],
            ["runtime/execution", "runtime/agent"],
        )

    def test_observer_emits_non_blocking_event_for_full_mode_only(self):
        from runtime.execution.supervisor_observer import emit_supervisor_observation

        plan = PlannerResult(
            route_type="multi_agent",
            reason="parallel read",
            refined_request="Inspect runtime.",
            tasks=[
                PlannedTask(
                    id="read_runtime",
                    title="Read runtime",
                    instruction="Read runtime execution code.",
                    skill_id="project_explorer",
                    model="executor",
                    mcp=["project_filesystem_readonly"],
                    parallel_group=1,
                    read_set=["runtime/execution"],
                )
            ],
        )
        event_bus = ExecutionEventBus()

        full_view = emit_supervisor_observation(plan, mode="full", event_bus=event_bus)
        serial_view = emit_supervisor_observation(plan, mode="serial", event_bus=event_bus)

        events = event_bus.snapshot()
        self.assertEqual(full_view.mode, "full")
        self.assertIsNone(serial_view)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, "SupervisorObservation")
        self.assertEqual(events[0].payload["task_count"], 1)
        self.assertEqual(events[0].payload["conflict_count"], 0)

    def test_worker_reporter_builds_deterministic_report_from_task_state_and_events(self):
        from runtime.execution.pipeline import PipelineRunState
        from runtime.execution.worker_reporter import build_worker_report

        task = PlannedTask(
            id="repair_runtime",
            title="Repair runtime",
            instruction="Fix runtime execution.",
            skill_id="jpc_now_skill",
            model="executor",
            mcp=["project_filesystem_readonly", "workspace_edit", "command_runner"],
            read_set=["runtime/execution/run_context.py"],
            write_intent=["runtime/execution/run_context.py"],
        )
        plan = PlannerResult(route_type="multi_agent", reason="test", refined_request="Repair runtime.", tasks=[task])
        run_state = PipelineRunState.create("Repair runtime.", plan, project_root=Path("."))
        run_state.record_fast_path_used(task, tool="git", action="diff")
        run_state.emit_event(
            "ToolInvoked",
            "approved",
            task_id="repair_runtime",
            status="completed",
            payload={
                "tool": "command_runner.run_command",
                "action": "run_command",
                "outcome": "approved",
            },
        )
        run_state.emit_event(
            "ToolInvoked",
            "approved",
            task_id="repair_runtime",
            status="completed",
            payload={
                "tool": "workspace_edit.write_file",
                "action": "write_file",
                "outcome": "approved",
                "files_touched": [{"path": "runtime/execution/run_context.py", "access": "write"}],
            },
        )
        run_state.record_task_result(task, "done")

        report = build_worker_report(task, "done", run_state=run_state)

        self.assertEqual(report.task_id, "repair_runtime")
        self.assertEqual(report.status, "completed")
        self.assertIn("runtime/execution/run_context.py", report.files_read)
        self.assertIn("runtime/execution/run_context.py", report.files_written)
        self.assertIn("done", report.summary)
        self.assertTrue(any(call["tool"] == "git" for call in report.tool_calls))
        self.assertTrue(any(call["tool"] == "command_runner.run_command" for call in report.tool_calls))

    def test_worker_reporter_does_not_treat_write_intent_as_actual_write(self):
        from runtime.execution.lead_reviewer import review_worker_reports
        from runtime.execution.pipeline import PipelineRunState
        from runtime.execution.worker_reporter import build_worker_report

        task = PlannedTask(
            id="planned_only",
            title="Planned only",
            instruction="Plan to edit but do not call workspace_edit.",
            skill_id="jpc_now_skill",
            model="executor",
            mcp=["workspace_edit"],
            write_intent=["src/planned.py"],
        )
        plan = PlannerResult(route_type="multi_agent", reason="test", refined_request="Repair.", tasks=[task])
        run_state = PipelineRunState.create("Repair.", plan, project_root=Path("."))
        run_state.record_task_result(task, "no edit happened")

        report = build_worker_report(task, "no edit happened", run_state=run_state)
        findings = review_worker_reports([task], [report])

        self.assertEqual(report.files_written, [])
        self.assertNotIn("write:src/planned.py", report.evidence_refs)
        self.assertFalse(any(finding.kind == "unauthorized_write" for finding in findings))

    def test_worker_reporter_extracts_multiple_actual_workspace_writes(self):
        from runtime.execution.pipeline import PipelineRunState
        from runtime.execution.worker_reporter import build_worker_report

        task = PlannedTask(
            id="edit_two",
            title="Edit two files",
            instruction="Edit two files.",
            skill_id="jpc_now_skill",
            model="executor",
            mcp=["workspace_edit"],
            write_intent=["src/a.py", "src/b.py"],
        )
        plan = PlannerResult(route_type="multi_agent", reason="test", refined_request="Repair.", tasks=[task])
        run_state = PipelineRunState.create("Repair.", plan, project_root=Path("."))
        for action, path in [("write_file", "src/a.py"), ("replace_in_file", "src/b.py")]:
            run_state.emit_event(
                "ToolInvoked",
                f"workspace_edit.{action}",
                task_id="edit_two",
                status="completed",
                payload={
                    "tool": f"workspace_edit.{action}",
                    "action": action,
                    "outcome": "approved",
                    "files_touched": [{"path": path, "access": "write"}],
                },
            )
        run_state.record_task_result(task, "edited")

        report = build_worker_report(task, "edited", run_state=run_state)

        self.assertEqual(report.files_written, ["src/a.py", "src/b.py"])
        self.assertEqual(len([call for call in report.tool_calls if call["tool"].startswith("workspace_edit.")]), 2)

    def test_worker_reporter_keeps_tool_evidence_fields_and_normalizes_windows_paths(self):
        from runtime.execution.pipeline import PipelineRunState
        from runtime.execution.worker_reporter import build_worker_report

        task = PlannedTask(
            id="edit_windows_path",
            title="Edit Windows path",
            instruction="Edit src\\win.py.",
            skill_id="jpc_now_skill",
            model="executor",
            mcp=["workspace_edit"],
            write_intent=["src/win.py"],
        )
        plan = PlannerResult(route_type="multi_agent", reason="test", refined_request="Repair.", tasks=[task])
        run_state = PipelineRunState.create("Repair.", plan, project_root=Path("."))
        run_state.emit_event(
            "ToolInvoked",
            "workspace_edit.write_file",
            task_id="edit_windows_path",
            status="completed",
            payload={
                "tool": "workspace_edit.write_file",
                "tool_rule": "workspace_edit",
                "action": "write_file",
                "outcome": "ok",
                "timestamp": "2026-05-26T12:00:00",
                "arguments_summary": {"target_path": "src\\win.py", "content_length": 2},
                "risk": {"risk_level": "low"},
                "files_touched": [{"path": "src\\win.py", "access": "write"}],
            },
        )
        run_state.record_task_result(task, "edited")

        report = build_worker_report(task, "edited", run_state=run_state)

        self.assertEqual(report.files_written, ["src/win.py"])
        self.assertEqual(len(report.tool_calls), 1)
        self.assertEqual(report.tool_calls[0]["tool_rule"], "workspace_edit")
        self.assertEqual(report.tool_calls[0]["timestamp"], "2026-05-26T12:00:00")
        self.assertEqual(report.tool_calls[0]["arguments_summary"]["target_path"], "src\\win.py")
        self.assertEqual(report.tool_calls[0]["risk"]["risk_level"], "low")

    def test_worker_reporter_ignores_pending_write_events(self):
        from runtime.execution.pipeline import PipelineRunState
        from runtime.execution.worker_reporter import build_worker_report

        task = PlannedTask(
            id="pending_write",
            title="Pending write",
            instruction="Ask to edit src/pending.py.",
            skill_id="jpc_now_skill",
            model="executor",
            mcp=["workspace_edit"],
            write_intent=["src/pending.py"],
        )
        plan = PlannerResult(route_type="multi_agent", reason="test", refined_request="Repair.", tasks=[task])
        run_state = PipelineRunState.create("Repair.", plan, project_root=Path("."))
        run_state.emit_event(
            "ToolInvoked",
            "workspace_edit.write_file",
            task_id="pending_write",
            status="pending",
            payload={
                "tool": "workspace_edit.write_file",
                "action": "write_file",
                "outcome": "pending",
                "files_touched": [{"path": "src/pending.py", "access": "write"}],
            },
        )
        run_state.record_task_result(task, "asked")

        report = build_worker_report(task, "asked", run_state=run_state)

        self.assertEqual(report.files_written, [])

    def test_worker_reporter_actual_out_of_scope_write_is_caught_by_lead_review(self):
        from runtime.execution.lead_reviewer import review_worker_reports
        from runtime.execution.pipeline import PipelineRunState
        from runtime.execution.worker_reporter import build_worker_report

        task = PlannedTask(
            id="edit_scope",
            title="Edit declared scope",
            instruction="Edit only src/allowed.py.",
            skill_id="jpc_now_skill",
            model="executor",
            mcp=["workspace_edit"],
            write_intent=["src/allowed.py"],
        )
        plan = PlannerResult(route_type="multi_agent", reason="test", refined_request="Repair.", tasks=[task])
        run_state = PipelineRunState.create("Repair.", plan, project_root=Path("."))
        run_state.emit_event(
            "ToolInvoked",
            "workspace_edit.write_file",
            task_id="edit_scope",
            status="completed",
            payload={
                "tool": "workspace_edit.write_file",
                "action": "write_file",
                "outcome": "approved",
                "files_touched": [{"path": "src/outside.py", "access": "write"}],
            },
        )
        run_state.record_task_result(task, "edited outside")

        findings = review_worker_reports([task], [build_worker_report(task, "edited outside", run_state=run_state)])

        self.assertTrue(any(finding.kind == "unauthorized_write" for finding in findings))

    def test_planned_task_scoped_hooks_separate_approval_events_from_tool_invocation(self):
        from runtime.execution.pipeline import PipelineRunState
        from runtime.execution.task_runner import _run_planned_task
        from runtime.execution.worker_reporter import build_worker_report
        from runtime.hooks import record_post_tool_use, record_pre_tool_use

        task = PlannedTask(
            id="edit_scope",
            title="Edit scope",
            instruction="Edit src/allowed.py.",
            skill_id="jpc_now_skill",
            model="executor",
            mcp=["workspace_edit"],
            write_intent=["src/allowed.py"],
        )
        plan = PlannerResult(route_type="multi_agent", reason="test", refined_request="Repair.", tasks=[task])
        run_state = PipelineRunState.create("Repair.", plan, project_root=Path("."))

        class FakeFactory:
            async def create_task_agent(self, task, execution_mode=""):
                return object()

        class FakeHooks:
            def __init__(self):
                self.tool_events = []

            def record_tool_event(self, event):
                self.tool_events.append(event)

        async def fake_run_agent(agent, prompt, hooks, **kwargs):
            pre_event = record_pre_tool_use(
                hooks,
                "workspace_edit.write_file",
                json.dumps({"target_path": "src/allowed.py", "content": "ok"}),
                tool_rule="workspace_edit",
            )
            record_post_tool_use(
                hooks,
                pre_event,
                status="supervisor_auto_approved",
                decision="approved",
                reason="test",
            )

            class Result:
                final_output = "edited"

            return Result()

        hooks = FakeHooks()
        asyncio.run(
            _run_planned_task(
                "Repair.",
                task,
                Path("."),
                FakeFactory(),
                hooks,
                fake_run_agent,
                run_state,
            )
        )

        events = [event for event in run_state.event_bus.snapshot() if event.event_type.startswith("Tool")]
        self.assertEqual([event.event_type for event in events], ["ToolApprovalPre", "ToolApprovalPost"])
        self.assertTrue(all(event.task_id == "edit_scope" for event in events))
        self.assertEqual(events[0].payload["tool"], "workspace_edit.write_file")
        self.assertEqual(events[1].payload["outcome"], "approved")
        self.assertEqual(len(hooks.tool_events), 2)

        report = build_worker_report(task, "edited", run_state=run_state)
        self.assertEqual(report.tool_calls, [])
        self.assertEqual(report.files_written, [])

    def test_worker_reporter_counts_one_invocation_when_approval_and_sdk_events_share_tool(self):
        from runtime.execution.pipeline import PipelineRunState
        from runtime.execution.worker_reporter import build_worker_report
        from runtime.hooks import TaskScopedHooks, record_post_tool_use, record_pre_tool_use

        task = PlannedTask(
            id="approval_sdk_edit",
            title="Approval SDK edit",
            instruction="Edit src/allowed.py.",
            skill_id="jpc_now_skill",
            model="executor",
            mcp=["workspace_edit"],
            write_intent=["src/allowed.py"],
        )
        plan = PlannerResult(route_type="multi_agent", reason="test", refined_request="Repair.", tasks=[task])
        run_state = PipelineRunState.create("Repair.", plan, project_root=Path("."))

        class FakeHooks:
            def __init__(self):
                self.tool_events = []

            def record_tool_event(self, event):
                self.tool_events.append(event)

        class FakeAgent:
            name = "worker"

        class FakeTool:
            name = "workspace_edit.write_file"

            @property
            def arguments(self):
                return json.dumps({"target_path": "src/allowed.py", "content": "ok"})

        hooks = FakeHooks()
        scoped_hooks = TaskScopedHooks(hooks, task_id=task.id, event_bus=run_state.event_bus)
        pre_event = record_pre_tool_use(
            scoped_hooks,
            "workspace_edit.write_file",
            json.dumps({"target_path": "src/allowed.py", "content": "ok"}),
            tool_rule="workspace_edit",
        )
        record_post_tool_use(
            scoped_hooks,
            pre_event,
            decision="approved",
            status="supervisor_auto_approved",
            reason="test",
        )
        asyncio.run(scoped_hooks.on_tool_end(None, FakeAgent(), FakeTool(), "ok"))

        events_by_type = {}
        for event in run_state.event_bus.snapshot():
            events_by_type.setdefault(event.event_type, []).append(event)
        self.assertEqual(len(events_by_type.get("ToolApprovalPre", [])), 1)
        self.assertEqual(len(events_by_type.get("ToolApprovalPost", [])), 1)
        self.assertEqual(len(events_by_type.get("ToolInvoked", [])), 1)

        report = build_worker_report(task, "edited", run_state=run_state)
        self.assertEqual(len(report.tool_calls), 1)
        self.assertEqual(report.tool_calls[0]["tool"], "workspace_edit.write_file")
        self.assertEqual(report.files_written, ["src/allowed.py"])

    def test_task_scoped_hooks_is_accepted_by_agents_sdk_validator(self):
        from agents.run import validate_run_hooks
        from runtime.execution.pipeline import PipelineRunState
        from runtime.hooks import TaskScopedHooks
        from runtime.kernel.session import create_token_logger_hooks

        plan = PlannerResult(route_type="multi_agent", reason="test", refined_request="Repair.")
        run_state = PipelineRunState.create("Repair.", plan, project_root=Path("."))
        scoped_hooks = TaskScopedHooks(create_token_logger_hooks(), task_id="sdk_validate", event_bus=run_state.event_bus)

        self.assertIs(validate_run_hooks(scoped_hooks), scoped_hooks)

    def test_planned_task_scoped_hooks_bridge_sdk_tool_end_callback(self):
        from runtime.execution.pipeline import PipelineRunState
        from runtime.execution.task_runner import _run_planned_task

        task = PlannedTask(
            id="sdk_edit",
            title="SDK edit",
            instruction="Edit src/sdk.py.",
            skill_id="jpc_now_skill",
            model="executor",
            mcp=["workspace_edit"],
            write_intent=["src/sdk.py"],
        )
        plan = PlannerResult(route_type="multi_agent", reason="test", refined_request="Repair.", tasks=[task])
        run_state = PipelineRunState.create("Repair.", plan, project_root=Path("."))

        class FakeFactory:
            async def create_task_agent(self, task, execution_mode=""):
                return object()

        class FakeAgent:
            name = "worker"

        class FakeTool:
            name = "workspace_edit.write_file"

            @property
            def arguments(self):
                return json.dumps({"target_path": "src/sdk.py", "content": "ok"})

        class FakeHooks:
            def __init__(self):
                self.ended = []

            async def on_tool_end(self, context, agent, tool, result):
                self.ended.append((agent.name, tool.name, result))

        async def fake_run_agent(agent, prompt, hooks, **kwargs):
            await hooks.on_tool_end(None, FakeAgent(), FakeTool(), "ok")

            class Result:
                final_output = "edited"

            return Result()

        hooks = FakeHooks()
        asyncio.run(
            _run_planned_task(
                "Repair.",
                task,
                Path("."),
                FakeFactory(),
                hooks,
                fake_run_agent,
                run_state,
            )
        )

        tool_events = [event for event in run_state.event_bus.snapshot() if event.event_type == "ToolInvoked"]
        self.assertEqual(len(tool_events), 1)
        self.assertEqual(tool_events[0].task_id, "sdk_edit")
        self.assertEqual(tool_events[0].payload["tool"], "workspace_edit.write_file")
        self.assertEqual(tool_events[0].payload["files_touched"], [{"path": "src/sdk.py", "access": "write"}])
        self.assertEqual(hooks.ended, [("worker", "workspace_edit.write_file", "ok")])

    def test_task_scoped_hooks_reads_sdk_tool_context_arguments(self):
        from runtime.execution.pipeline import PipelineRunState
        from runtime.execution.worker_reporter import build_worker_report
        from runtime.hooks import TaskScopedHooks

        task = PlannedTask(
            id="sdk_context_edit",
            title="SDK context edit",
            instruction="Edit src/context.py.",
            skill_id="jpc_now_skill",
            model="executor",
            mcp=["workspace_edit"],
            write_intent=["src/context.py"],
        )
        plan = PlannerResult(route_type="multi_agent", reason="test", refined_request="Repair.", tasks=[task])
        run_state = PipelineRunState.create("Repair.", plan, project_root=Path("."))

        class FakeContext:
            tool_name = "workspace_edit.write_file"
            tool_arguments = json.dumps({"target_path": "src/context.py", "content": "ok"})

        class FakeAgent:
            name = "worker"

        class FakeTool:
            name = "workspace_edit.write_file"

        scoped_hooks = TaskScopedHooks(object(), task_id=task.id, event_bus=run_state.event_bus)
        asyncio.run(scoped_hooks.on_tool_end(FakeContext(), FakeAgent(), FakeTool(), "ok"))

        tool_events = [event for event in run_state.event_bus.snapshot() if event.event_type == "ToolInvoked"]
        self.assertEqual(len(tool_events), 1)
        self.assertEqual(tool_events[0].payload["tool"], "workspace_edit.write_file")
        self.assertEqual(tool_events[0].payload["arguments_summary"]["target_path"], "src/context.py")
        self.assertEqual(tool_events[0].payload["files_touched"], [{"path": "src/context.py", "access": "write"}])

        report = build_worker_report(task, "edited", run_state=run_state)
        self.assertEqual(report.files_written, ["src/context.py"])

    def test_task_scoped_hooks_falls_back_to_tool_arguments_without_context_arguments(self):
        from runtime.execution.pipeline import PipelineRunState
        from runtime.hooks import TaskScopedHooks

        task = PlannedTask(
            id="sdk_tool_fallback_edit",
            title="SDK tool fallback edit",
            instruction="Edit src/fallback.py.",
            skill_id="jpc_now_skill",
            model="executor",
            mcp=["workspace_edit"],
            write_intent=["src/fallback.py"],
        )
        plan = PlannerResult(route_type="multi_agent", reason="test", refined_request="Repair.", tasks=[task])
        run_state = PipelineRunState.create("Repair.", plan, project_root=Path("."))

        class FakeAgent:
            name = "worker"

        class FakeTool:
            name = "workspace_edit.write_file"
            arguments = json.dumps({"target_path": "src/fallback.py", "content": "ok"})

        scoped_hooks = TaskScopedHooks(object(), task_id=task.id, event_bus=run_state.event_bus)
        asyncio.run(scoped_hooks.on_tool_end(object(), FakeAgent(), FakeTool(), "ok"))

        tool_events = [event for event in run_state.event_bus.snapshot() if event.event_type == "ToolInvoked"]
        self.assertEqual(tool_events[0].payload["files_touched"], [{"path": "src/fallback.py", "access": "write"}])

    def test_worker_reporter_keeps_same_tool_calls_for_different_files(self):
        from runtime.execution.pipeline import PipelineRunState
        from runtime.execution.worker_reporter import build_worker_report

        task = PlannedTask(
            id="same_tool_two_writes",
            title="Same tool two writes",
            instruction="Write two files with the same tool.",
            skill_id="jpc_now_skill",
            model="executor",
            mcp=["workspace_edit"],
            write_intent=["src/a.py", "src/b.py"],
        )
        plan = PlannerResult(route_type="multi_agent", reason="test", refined_request="Repair.", tasks=[task])
        run_state = PipelineRunState.create("Repair.", plan, project_root=Path("."))
        for path in ["src/a.py", "src/b.py"]:
            run_state.emit_event(
                "ToolInvoked",
                "workspace_edit.write_file",
                task_id="same_tool_two_writes",
                status="completed",
                payload={
                    "tool": "workspace_edit.write_file",
                    "action": "write_file",
                    "outcome": "completed",
                    "arguments_summary": {"target_path": path, "content_length": 2},
                    "files_touched": [{"path": path, "access": "write"}],
                },
            )
        run_state.record_task_result(task, "edited")

        report = build_worker_report(task, "edited", run_state=run_state)

        self.assertEqual(report.files_written, ["src/a.py", "src/b.py"])
        self.assertEqual(len(report.tool_calls), 2)
        self.assertEqual(
            [call["files_touched"][0]["path"] for call in report.tool_calls],
            ["src/a.py", "src/b.py"],
        )

    def test_worker_reporter_extracts_claimed_report_without_overriding_evidence(self):
        from runtime.execution.worker_reporter import build_worker_report, render_worker_report

        task = PlannedTask(
            id="repair_runtime",
            title="Repair runtime",
            instruction="Fix runtime execution.",
            skill_id="jpc_now_skill",
            model="executor",
            mcp=["project_filesystem_readonly", "workspace_edit", "command_runner"],
            read_set=["runtime/execution/run_context.py"],
            write_intent=["runtime/execution/run_context.py"],
        )
        output = """
修复已经完成。

## WorkerReport
- 完成内容: 修复了初始化路径
- 读取依据: runtime/execution/run_context.py
- 修改内容: 我没有改文件
- 验证结果: python -m compileall runtime 通过
- 风险/未完成: 需要人工复核边界
"""

        report = build_worker_report(task, output)
        rendered = render_worker_report(report)

        self.assertEqual(report.files_written, [])
        self.assertIn("claimed_completed: 修复了初始化路径", report.artifacts)
        self.assertIn("claimed_verification: python -m compileall runtime 通过", report.artifacts)
        self.assertIn("claimed_risks: 需要人工复核边界", report.artifacts)
        self.assertIn("claims:", rendered)
        self.assertIn("claimed_changes: 我没有改文件", rendered)
        self.assertIn("files_written: none", rendered)

    def test_lead_review_flags_failed_missing_evidence_and_unauthorized_write(self):
        from runtime.agent.supervisor import WorkerReport
        from runtime.execution.lead_reviewer import review_worker_reports

        readonly_task = PlannedTask(
            id="inspect_only",
            title="Inspect only",
            instruction="Inspect the project without editing files.",
            skill_id="project_explorer",
            model="executor",
            mcp=["project_filesystem_readonly"],
            read_set=["README.md"],
            write_intent=[],
        )
        failed_task = PlannedTask(
            id="failed_worker",
            title="Failed worker",
            instruction="Run a worker task.",
            skill_id="project_explorer",
            model="executor",
            mcp=["project_filesystem_readonly"],
        )
        silent_task = PlannedTask(
            id="silent_worker",
            title="Silent worker",
            instruction="Report enough evidence.",
            skill_id="project_explorer",
            model="executor",
            mcp=[],
        )

        findings = review_worker_reports(
            [readonly_task, failed_task, silent_task],
            [
                WorkerReport(
                    task_id="inspect_only",
                    status="completed",
                    summary="Edited a file despite read-only scope.",
                    evidence_refs=["write:src/app.py"],
                    files_written=["src/app.py"],
                ),
                WorkerReport(
                    task_id="failed_worker",
                    status="failed",
                    summary="The worker failed.",
                    blockers=["tool timeout"],
                    tool_calls=[{"tool": "project_filesystem_readonly", "action": "read", "status": "failed"}],
                ),
                WorkerReport(task_id="silent_worker", status="completed", summary="done", evidence_refs=["task:silent_worker"]),
            ],
            readonly_hard_constraint=True,
        )

        finding_keys = {(finding.task_id, finding.kind): finding for finding in findings}
        self.assertEqual(finding_keys[("inspect_only", "unauthorized_write")].severity, "error")
        self.assertEqual(finding_keys[("failed_worker", "task_failed")].severity, "error")
        self.assertEqual(finding_keys[("failed_worker", "blocker")].severity, "warning")
        self.assertEqual(finding_keys[("silent_worker", "missing_evidence")].severity, "warning")

    def test_supervisor_scheduler_runs_readonly_tasks_in_parallel(self):
        from runtime.execution.supervisor_scheduler import supervisor_execution_batches_for_full

        tasks = [
            PlannedTask(
                id="read_execution",
                title="Read execution",
                instruction="Read runtime execution code.",
                skill_id="project_explorer",
                model="executor",
                mcp=["project_filesystem_readonly", "code_locator"],
                parallel_group=1,
                read_set=["runtime/execution"],
            ),
            PlannedTask(
                id="read_agent",
                title="Read agent",
                instruction="Read runtime agent code.",
                skill_id="project_explorer",
                model="executor",
                mcp=["project_filesystem_readonly", "code_locator"],
                parallel_group=1,
                read_set=["runtime/agent"],
            ),
        ]

        batches = supervisor_execution_batches_for_full(tasks)

        self.assertEqual([[task.id for task in batch] for batch in batches], [["read_execution", "read_agent"]])

    def test_supervisor_scheduler_serializes_overlapping_writes(self):
        from runtime.execution.supervisor_scheduler import supervisor_execution_batches_for_full

        tasks = [
            PlannedTask(
                id="edit_dir",
                title="Edit src directory",
                instruction="Edit src directory.",
                skill_id="jpc_now_skill",
                model="executor",
                mcp=["workspace_edit"],
                parallel_group=1,
                write_intent=["src"],
            ),
            PlannedTask(
                id="edit_file",
                title="Edit app file",
                instruction="Edit src/app.py.",
                skill_id="jpc_now_skill",
                model="executor",
                mcp=["workspace_edit"],
                parallel_group=1,
                write_intent=["src/app.py"],
            ),
        ]

        batches = supervisor_execution_batches_for_full(tasks)

        self.assertEqual([[task.id for task in batch] for batch in batches], [["edit_dir"], ["edit_file"]])

    def test_supervisor_scheduler_serializes_command_tasks_by_default(self):
        from runtime.execution.supervisor_scheduler import supervisor_execution_batches_for_full

        tasks = [
            PlannedTask(
                id="run_check",
                title="Run check",
                instruction="Run python -m compileall runtime.",
                skill_id="jpc_now_skill",
                model="executor",
                mcp=["command_runner"],
                parallel_group=1,
            ),
            PlannedTask(
                id="run_tests",
                title="Run tests",
                instruction="Run focused tests.",
                skill_id="jpc_now_skill",
                model="executor",
                mcp=["command_runner"],
                parallel_group=1,
            ),
        ]

        batches = supervisor_execution_batches_for_full(tasks)

        self.assertEqual([[task.id for task in batch] for batch in batches], [["run_check"], ["run_tests"]])

    def test_full_runner_applies_supervisor_read_budget_profile_before_tools_start(self):
        import runtime.execution.multi_agent_runner as runner_module
        from runtime.execution.pipeline import PipelineRunState

        plan = PlannerResult(
            route_type="multi_agent",
            reason="parallel read",
            refined_request="Inspect runtime.",
            synthesis_instruction="Summarize outputs.",
            tasks=[
                PlannedTask(
                    id="read_runtime",
                    title="Read runtime",
                    instruction="Read runtime execution code.",
                    skill_id="project_explorer",
                    model="executor",
                    mcp=["project_filesystem_readonly"],
                    parallel_group=1,
                    read_set=["runtime/execution"],
                ),
            ],
        )
        run_state = PipelineRunState.create("Inspect runtime.", plan)

        class FakeManager:
            def __init__(self):
                self.profiles = []

            def set_readonly_budget_profile(self, mcp_id, profile):
                self.profiles.append((mcp_id, dict(profile)))

            async def get_many(self, mcp_ids):
                return []

        class FakeWorkspace:
            def __init__(self, project_root):
                self.project_root = project_root

            def create(self):
                return Path(self.project_root)

            def write_task_output(self, task_id, title, output):
                pass

            def cleanup(self):
                pass

        class FakeLedger:
            def __init__(self, project_root):
                self.project_root = project_root

        class FakeServer:
            async def __aenter__(self):
                return object()

            async def __aexit__(self, exc_type, exc, traceback):
                return False

        class FakeFactory:
            def __init__(self):
                self.mcp_manager = FakeManager()

            def create_synthesizer_agent(self, model_id, run_workspace_server):
                return object()

        class FakeResult:
            final_output = "final synthesized output"

        async def fake_run_planned_task(*args, **kwargs):
            task = args[1]
            state = args[6]
            state.record_task_result(task, f"done {task.id}")
            return task.title, f"done {task.id}"

        async def fake_run_agent(agent, prompt, hooks, max_turns=10):
            return FakeResult()

        factory = FakeFactory()
        original_workspace = runner_module.RunWorkspace
        original_ledger = runner_module.PatchProposalLedger
        original_server = runner_module.create_readonly_filesystem_server
        original_task_runner = runner_module._run_planned_task
        runner_module.RunWorkspace = FakeWorkspace
        runner_module.PatchProposalLedger = FakeLedger
        runner_module.create_readonly_filesystem_server = lambda *args, **kwargs: FakeServer()
        runner_module._run_planned_task = fake_run_planned_task
        try:
            asyncio.run(
                runner_module._run_multi_agent(
                    "Inspect runtime.",
                    plan,
                    Path("."),
                    "synthesizer",
                    factory,
                    object(),
                    fake_run_agent,
                    run_state,
                    execution_mode="full",
                    show_progress=False,
                )
            )
        finally:
            runner_module.RunWorkspace = original_workspace
            runner_module.PatchProposalLedger = original_ledger
            runner_module.create_readonly_filesystem_server = original_server
            runner_module._run_planned_task = original_task_runner

        self.assertEqual(factory.mcp_manager.profiles[0][0], "project_filesystem_readonly")
        self.assertGreater(int(factory.mcp_manager.profiles[0][1]["max_read_calls"]), 10)
        self.assertEqual(factory.mcp_manager.profiles[0][1]["supervisor_expansion"], "1")

    def test_multi_agent_runner_records_supervisor_observation_before_full_execution(self):
        import runtime.execution.multi_agent_runner as runner_module
        from runtime.execution.pipeline import PipelineRunState

        plan = PlannerResult(
            route_type="multi_agent",
            reason="parallel read",
            refined_request="Inspect runtime.",
            synthesis_instruction="Summarize outputs.",
            tasks=[
                PlannedTask(
                    id="read_runtime_a",
                    title="Read runtime A",
                    instruction="Read runtime execution code.",
                    skill_id="project_explorer",
                    model="executor",
                    mcp=["project_filesystem_readonly"],
                    parallel_group=1,
                    read_set=["runtime/execution"],
                ),
                PlannedTask(
                    id="read_runtime_b",
                    title="Read runtime B",
                    instruction="Read runtime agent code.",
                    skill_id="project_explorer",
                    model="executor",
                    mcp=["project_filesystem_readonly"],
                    parallel_group=1,
                    read_set=["runtime/agent"],
                ),
            ],
        )
        run_state = PipelineRunState.create("Inspect runtime.", plan)

        class FakeWorkspace:
            def __init__(self, project_root):
                self.project_root = project_root

            def create(self):
                return Path(self.project_root)

            def write_task_output(self, task_id, title, output):
                pass

            def cleanup(self):
                pass

        class FakeLedger:
            def __init__(self, project_root):
                self.project_root = project_root

        class FakeServer:
            async def __aenter__(self):
                return object()

            async def __aexit__(self, exc_type, exc, traceback):
                return False

        class FakeFactory:
            def create_synthesizer_agent(self, model_id, run_workspace_server):
                return object()

        class FakeResult:
            final_output = "final synthesized output"

        async def fake_run_planned_task(refined_request, task, project_root, factory, hooks, run_agent, state, ledger):
            state.record_task_result(task, f"done {task.id}")
            return task.title, f"done {task.id}"

        async def fake_run_agent(agent, prompt, hooks, max_turns=10):
            return FakeResult()

        original_workspace = runner_module.RunWorkspace
        original_ledger = runner_module.PatchProposalLedger
        original_server = runner_module.create_readonly_filesystem_server
        original_task_runner = runner_module._run_planned_task
        runner_module.RunWorkspace = FakeWorkspace
        runner_module.PatchProposalLedger = FakeLedger
        runner_module.create_readonly_filesystem_server = lambda *args, **kwargs: FakeServer()
        runner_module._run_planned_task = fake_run_planned_task
        try:
            output = asyncio.run(
                runner_module._run_multi_agent(
                    "Inspect runtime.",
                    plan,
                    Path("."),
                    "synthesizer",
                    FakeFactory(),
                    object(),
                    fake_run_agent,
                    run_state,
                    execution_mode="full",
                    show_progress=False,
                )
            )
        finally:
            runner_module.RunWorkspace = original_workspace
            runner_module.PatchProposalLedger = original_ledger
            runner_module.create_readonly_filesystem_server = original_server
            runner_module._run_planned_task = original_task_runner

        event_types = [event.event_type for event in run_state.event_bus.snapshot()]
        self.assertEqual(output, "final synthesized output")
        self.assertIn("SupervisorObservation", event_types)

    def test_full_runner_uses_lead_supervisor_output_when_summary_helper_disabled(self):
        import runtime.execution.multi_agent_runner as runner_module
        from runtime.execution.pipeline import PipelineRunState

        plan = PlannerResult(
            route_type="multi_agent",
            reason="parallel read",
            refined_request="Inspect runtime.",
            needs_synthesis=False,
            synthesis_instruction="",
            memory_interface={
                "execution_contract": {
                    "supervisor_route": "team",
                    "summary_helper": {"enabled": False, "reason": "lead_supervisor_final_answer"},
                }
            },
            tasks=[
                PlannedTask(
                    id="read_execution",
                    title="Read execution",
                    instruction="Read runtime execution code.",
                    skill_id="project_explorer",
                    model="executor",
                    mcp=["project_filesystem_readonly"],
                    parallel_group=1,
                    read_set=["runtime/execution"],
                ),
                PlannedTask(
                    id="read_agent",
                    title="Read agent",
                    instruction="Read runtime agent code.",
                    skill_id="project_explorer",
                    model="executor",
                    mcp=["project_filesystem_readonly"],
                    parallel_group=1,
                    read_set=["runtime/agent"],
                ),
            ],
        )
        run_state = PipelineRunState.create("Inspect runtime.", plan)

        class FakeWorkspace:
            def __init__(self, project_root):
                self.project_root = project_root

            def create(self):
                return Path(self.project_root)

            def write_task_output(self, task_id, title, output):
                pass

            def cleanup(self):
                pass

        class FakeLedger:
            def __init__(self, project_root):
                self.project_root = project_root

        class FakeFactory:
            def create_synthesizer_agent(self, model_id, run_workspace_server):
                raise AssertionError("summary helper should be skipped for supervised full team")

        async def fake_run_planned_task(refined_request, task, project_root, factory, hooks, run_agent, state, ledger, **kwargs):
            state.record_task_result(task, f"done {task.id}")
            return task.title, f"worker report for {task.id}"

        async def fake_run_agent(*args, **kwargs):
            raise AssertionError("summary helper should not run")

        original_workspace = runner_module.RunWorkspace
        original_ledger = runner_module.PatchProposalLedger
        original_task_runner = runner_module._run_planned_task
        runner_module.RunWorkspace = FakeWorkspace
        runner_module.PatchProposalLedger = FakeLedger
        runner_module._run_planned_task = fake_run_planned_task
        try:
            output = asyncio.run(
                runner_module._run_multi_agent(
                    "Inspect runtime.",
                    plan,
                    Path("."),
                    "synthesizer",
                    FakeFactory(),
                    object(),
                    fake_run_agent,
                    run_state,
                    execution_mode="full",
                    show_progress=False,
                )
            )
        finally:
            runner_module.RunWorkspace = original_workspace
            runner_module.PatchProposalLedger = original_ledger
            runner_module._run_planned_task = original_task_runner

        event_types = [event.event_type for event in run_state.event_bus.snapshot()]
        self.assertIn("LeadFinalizing", event_types)
        self.assertIn("LeadCompleted", event_types)
        self.assertIn("主管最终汇报", output)
        self.assertIn("## 任务判断", output)
        self.assertIn("## Worker 执行结果", output)
        self.assertIn("## 文件影响", output)
        self.assertIn("## 验证结果", output)
        self.assertIn("## 主管审查", output)
        self.assertIn("## 最终结论", output)
        self.assertIn("worker report for read_execution", output)
        self.assertIn("worker report for read_agent", output)

    def test_full_runner_lead_output_uses_deterministic_worker_reports_for_unstructured_output(self):
        import runtime.execution.multi_agent_runner as runner_module
        from runtime.execution.pipeline import PipelineRunState

        plan = PlannerResult(
            route_type="multi_agent",
            reason="parallel repair",
            refined_request="Repair runtime context.",
            needs_synthesis=False,
            memory_interface={
                "execution_contract": {
                    "supervisor_route": "team",
                    "summary_helper": {"enabled": False, "reason": "lead_supervisor_final_answer"},
                }
            },
            tasks=[
                PlannedTask(
                    id="repair_context",
                    title="Repair context",
                    instruction="Fix runtime context store.",
                    skill_id="jpc_now_skill",
                    model="executor",
                    mcp=["project_filesystem_readonly", "workspace_edit", "command_runner"],
                    parallel_group=1,
                    read_set=["runtime/execution/run_context.py"],
                    write_intent=["runtime/execution/run_context.py"],
                ),
            ],
        )
        run_state = PipelineRunState.create("Repair runtime context.", plan, project_root=Path("."))

        class FakeWorkspace:
            def __init__(self, project_root):
                self.project_root = project_root

            def create(self):
                return Path(self.project_root)

            def write_task_output(self, task_id, title, output):
                pass

            def cleanup(self):
                pass

        class FakeLedger:
            def __init__(self, project_root):
                self.project_root = project_root

        class FakeFactory:
            def create_synthesizer_agent(self, model_id, run_workspace_server):
                raise AssertionError("summary helper should be skipped for supervised full team")

        async def fake_run_planned_task(refined_request, task, project_root, factory, hooks, run_agent, state, ledger, **kwargs):
            state.emit_event(
                "ToolInvoked",
                "approved",
                task_id=task.id,
                status="completed",
                payload={"tool": "command_runner.run_command", "action": "run_command", "outcome": "approved"},
            )
            state.emit_event(
                "ToolInvoked",
                "approved",
                task_id=task.id,
                status="completed",
                payload={
                    "tool": "workspace_edit.write_file",
                    "action": "write_file",
                    "outcome": "approved",
                    "files_touched": [{"path": "runtime/execution/run_context.py", "access": "write"}],
                },
            )
            state.record_task_result(task, "done")
            return task.title, "done"

        async def fake_run_agent(*args, **kwargs):
            raise AssertionError("summary helper should not run")

        original_workspace = runner_module.RunWorkspace
        original_ledger = runner_module.PatchProposalLedger
        original_task_runner = runner_module._run_planned_task
        runner_module.RunWorkspace = FakeWorkspace
        runner_module.PatchProposalLedger = FakeLedger
        runner_module._run_planned_task = fake_run_planned_task
        try:
            output = asyncio.run(
                runner_module._run_multi_agent(
                    "Repair runtime context.",
                    plan,
                    Path("."),
                    "synthesizer",
                    FakeFactory(),
                    object(),
                    fake_run_agent,
                    run_state,
                    execution_mode="full",
                    show_progress=False,
                )
            )
        finally:
            runner_module.RunWorkspace = original_workspace
            runner_module.PatchProposalLedger = original_ledger
            runner_module._run_planned_task = original_task_runner

        self.assertIn("WorkerReport", output)
        self.assertIn("repair_context", output)
        self.assertIn("files_read: runtime/execution/run_context.py", output)
        self.assertIn("files_written: runtime/execution/run_context.py", output)
        self.assertIn("tool_calls: command_runner.run_command", output)
        self.assertIn("summary: done", output)

    def test_full_runner_emits_lead_review_findings_without_rework(self):
        import runtime.execution.multi_agent_runner as runner_module
        from runtime.execution.pipeline import PipelineRunState

        plan = PlannerResult(
            route_type="multi_agent",
            reason="supervised lead review",
            refined_request="Inspect runtime and report failures.",
            needs_synthesis=False,
            memory_interface={
                "execution_contract": {
                    "readonly_hard_constraint": True,
                    "supervisor_route": "team",
                    "summary_helper": {"enabled": False, "reason": "lead_supervisor_final_answer"},
                }
            },
            tasks=[
                PlannedTask(
                    id="silent_worker",
                    title="Silent worker",
                    instruction="Return a conclusion with evidence.",
                    skill_id="project_explorer",
                    model="executor",
                    mcp=[],
                    parallel_group=1,
                ),
                PlannedTask(
                    id="failed_worker",
                    title="Failed worker",
                    instruction="Simulate a failed worker.",
                    skill_id="project_explorer",
                    model="executor",
                    mcp=[],
                    parallel_group=1,
                ),
            ],
        )
        run_state = PipelineRunState.create("Inspect runtime and report failures.", plan, project_root=Path("."))
        calls = []

        class FakeWorkspace:
            def __init__(self, project_root):
                self.project_root = project_root

            def create(self):
                return Path(self.project_root)

            def write_task_output(self, task_id, title, output):
                pass

            def cleanup(self):
                pass

        class FakeLedger:
            def __init__(self, project_root):
                self.project_root = project_root

        class FakeFactory:
            def create_synthesizer_agent(self, model_id, run_workspace_server):
                raise AssertionError("summary helper should be skipped for supervised full team")

        async def fake_run_planned_task(refined_request, task, project_root, factory, hooks, run_agent, state, ledger, **kwargs):
            calls.append(task.id)
            if task.id == "failed_worker":
                raise RuntimeError("tool timeout")
            state.record_task_result(task, "done")
            return task.title, "done"

        async def fake_run_agent(*args, **kwargs):
            raise AssertionError("summary helper should not run")

        original_workspace = runner_module.RunWorkspace
        original_ledger = runner_module.PatchProposalLedger
        original_task_runner = runner_module._run_planned_task
        runner_module.RunWorkspace = FakeWorkspace
        runner_module.PatchProposalLedger = FakeLedger
        runner_module._run_planned_task = fake_run_planned_task
        try:
            output = asyncio.run(
                runner_module._run_multi_agent(
                    "Inspect runtime and report failures.",
                    plan,
                    Path("."),
                    "synthesizer",
                    FakeFactory(),
                    object(),
                    fake_run_agent,
                    run_state,
                    execution_mode="full",
                    show_progress=False,
                )
            )
        finally:
            runner_module.RunWorkspace = original_workspace
            runner_module.PatchProposalLedger = original_ledger
            runner_module._run_planned_task = original_task_runner

        event_types = [event.event_type for event in run_state.event_bus.snapshot()]
        self.assertIn("LeadReviewFinding", event_types)
        self.assertIn("LeadReviewCompleted", event_types)
        self.assertIn("LeadReview", output)
        self.assertIn("missing_evidence", output)
        self.assertIn("task_failed", output)
        self.assertEqual(calls.count("silent_worker"), 1)
        self.assertEqual(calls.count("failed_worker"), 1)

    def test_full_runner_runs_lead_review_for_single_supervisor_route(self):
        import runtime.execution.multi_agent_runner as runner_module
        from runtime.execution.pipeline import PipelineRunState

        plan = PlannerResult(
            route_type="multi_agent",
            reason="single supervised repair",
            refined_request="Repair one file.",
            needs_synthesis=False,
            memory_interface={
                "execution_contract": {
                    "supervisor_route": "single",
                    "summary_helper": {"enabled": False, "reason": "lead_supervisor_final_answer"},
                }
            },
            tasks=[
                PlannedTask(
                    id="single_worker",
                    title="Single worker",
                    instruction="Edit only src/allowed.py.",
                    skill_id="jpc_now_skill",
                    model="executor",
                    mcp=["workspace_edit"],
                    parallel_group=1,
                    write_intent=["src/allowed.py"],
                ),
            ],
        )
        run_state = PipelineRunState.create("Repair one file.", plan, project_root=Path("."))

        class FakeWorkspace:
            def __init__(self, project_root):
                self.project_root = project_root

            def create(self):
                return Path(self.project_root)

            def write_task_output(self, task_id, title, output):
                pass

            def cleanup(self):
                pass

        class FakeLedger:
            def __init__(self, project_root):
                self.project_root = project_root

        class FakeServer:
            async def __aenter__(self):
                return object()

            async def __aexit__(self, exc_type, exc, traceback):
                return False

        class FakeFactory:
            def create_synthesizer_agent(self, model_id, run_workspace_server):
                return object()

        class FakeResult:
            final_output = "final synthesized output"

        async def fake_run_planned_task(refined_request, task, project_root, factory, hooks, run_agent, state, ledger, **kwargs):
            state.emit_event(
                "ToolInvoked",
                "workspace_edit.write_file",
                task_id=task.id,
                status="completed",
                payload={
                    "tool": "workspace_edit.write_file",
                    "action": "write_file",
                    "outcome": "approved",
                    "files_touched": [{"path": "src/outside.py", "access": "write"}],
                },
            )
            state.record_task_result(task, "edited outside")
            return task.title, "edited outside"

        async def fake_run_agent(agent, prompt, hooks, max_turns=10):
            return FakeResult()

        original_workspace = runner_module.RunWorkspace
        original_ledger = runner_module.PatchProposalLedger
        original_server = runner_module.create_readonly_filesystem_server
        original_task_runner = runner_module._run_planned_task
        runner_module.RunWorkspace = FakeWorkspace
        runner_module.PatchProposalLedger = FakeLedger
        runner_module.create_readonly_filesystem_server = lambda *args, **kwargs: FakeServer()
        runner_module._run_planned_task = fake_run_planned_task
        try:
            output = asyncio.run(
                runner_module._run_multi_agent(
                    "Repair one file.",
                    plan,
                    Path("."),
                    "synthesizer",
                    FakeFactory(),
                    object(),
                    fake_run_agent,
                    run_state,
                    execution_mode="full",
                    show_progress=False,
                )
            )
        finally:
            runner_module.RunWorkspace = original_workspace
            runner_module.PatchProposalLedger = original_ledger
            runner_module.create_readonly_filesystem_server = original_server
            runner_module._run_planned_task = original_task_runner

        event_types = [event.event_type for event in run_state.event_bus.snapshot()]
        self.assertIn("LeadReviewFinding", event_types)
        self.assertIn("LeadReviewCompleted", event_types)
        self.assertIn("final synthesized output", output)

    def test_full_runner_runs_lead_review_before_summary_helper(self):
        import runtime.execution.multi_agent_runner as runner_module
        from runtime.execution.pipeline import PipelineRunState

        plan = PlannerResult(
            route_type="multi_agent",
            reason="team with summary helper",
            refined_request="Repair and summarize.",
            needs_synthesis=True,
            synthesis_instruction="Summarize worker output.",
            memory_interface={
                "execution_contract": {
                    "supervisor_route": "team",
                    "summary_helper": {"enabled": True, "reason": "summary_helper_requested"},
                }
            },
            tasks=[
                PlannedTask(
                    id="team_worker",
                    title="Team worker",
                    instruction="Edit only src/allowed.py.",
                    skill_id="jpc_now_skill",
                    model="executor",
                    mcp=["workspace_edit"],
                    parallel_group=1,
                    write_intent=["src/allowed.py"],
                ),
            ],
        )
        run_state = PipelineRunState.create("Repair and summarize.", plan, project_root=Path("."))

        class FakeWorkspace:
            def __init__(self, project_root):
                self.project_root = project_root

            def create(self):
                return Path(self.project_root)

            def write_task_output(self, task_id, title, output):
                pass

            def cleanup(self):
                pass

        class FakeLedger:
            def __init__(self, project_root):
                self.project_root = project_root

        class FakeServer:
            async def __aenter__(self):
                return object()

            async def __aexit__(self, exc_type, exc, traceback):
                return False

        class FakeFactory:
            def create_synthesizer_agent(self, model_id, run_workspace_server):
                return object()

        class FakeResult:
            final_output = "final synthesized output"

        async def fake_run_planned_task(refined_request, task, project_root, factory, hooks, run_agent, state, ledger, **kwargs):
            state.emit_event(
                "ToolInvoked",
                "workspace_edit.write_file",
                task_id=task.id,
                status="completed",
                payload={
                    "tool": "workspace_edit.write_file",
                    "action": "write_file",
                    "outcome": "approved",
                    "files_touched": [{"path": "src/outside.py", "access": "write"}],
                },
            )
            state.record_task_result(task, "edited outside")
            return task.title, "edited outside"

        async def fake_run_agent(agent, prompt, hooks, max_turns=10):
            return FakeResult()

        original_workspace = runner_module.RunWorkspace
        original_ledger = runner_module.PatchProposalLedger
        original_server = runner_module.create_readonly_filesystem_server
        original_task_runner = runner_module._run_planned_task
        runner_module.RunWorkspace = FakeWorkspace
        runner_module.PatchProposalLedger = FakeLedger
        runner_module.create_readonly_filesystem_server = lambda *args, **kwargs: FakeServer()
        runner_module._run_planned_task = fake_run_planned_task
        try:
            output = asyncio.run(
                runner_module._run_multi_agent(
                    "Repair and summarize.",
                    plan,
                    Path("."),
                    "synthesizer",
                    FakeFactory(),
                    object(),
                    fake_run_agent,
                    run_state,
                    execution_mode="full",
                    show_progress=False,
                )
            )
        finally:
            runner_module.RunWorkspace = original_workspace
            runner_module.PatchProposalLedger = original_ledger
            runner_module.create_readonly_filesystem_server = original_server
            runner_module._run_planned_task = original_task_runner

        event_types = [event.event_type for event in run_state.event_bus.snapshot()]
        self.assertIn("LeadReviewFinding", event_types)
        self.assertIn("LeadReviewCompleted", event_types)
        self.assertIn("final synthesized output", output)

    def test_full_runner_records_structured_context_pack_once_for_team_workers(self):
        import runtime.execution.multi_agent_runner as runner_module
        from runtime.execution.pipeline import PipelineRunState

        plan = PlannerResult(
            route_type="multi_agent",
            reason="parallel read",
            refined_request="Inspect shared project context.",
            needs_synthesis=False,
            memory_interface={
                "execution_contract": {
                    "supervisor_route": "team",
                    "summary_helper": {"enabled": False, "reason": "lead_supervisor_final_answer"},
                }
            },
            tasks=[
                PlannedTask(
                    id="read_readme",
                    title="Read README",
                    instruction="Read README.md.",
                    skill_id="project_explorer",
                    model="executor",
                    mcp=["project_filesystem_readonly"],
                    parallel_group=1,
                    read_set=["README.md"],
                ),
                PlannedTask(
                    id="read_package",
                    title="Read package",
                    instruction="Read package metadata.",
                    skill_id="project_explorer",
                    model="executor",
                    mcp=["project_filesystem_readonly"],
                    parallel_group=1,
                    read_set=["README.md"],
                ),
            ],
        )
        run_state = PipelineRunState.create("Inspect shared project context.", plan, project_root=Path("."))
        seen_shared = []

        class FakeWorkspace:
            def __init__(self, project_root):
                self.project_root = project_root

            def create(self):
                return Path(self.project_root)

            def write_task_output(self, task_id, title, output):
                pass

            def cleanup(self):
                pass

        class FakeLedger:
            def __init__(self, project_root):
                self.project_root = project_root

        class FakeFactory:
            def create_synthesizer_agent(self, model_id, run_workspace_server):
                raise AssertionError("summary helper should be skipped for supervised full team")

        async def fake_run_planned_task(refined_request, task, project_root, factory, hooks, run_agent, state, ledger, **kwargs):
            shared = state.run_context.render_for_task(task.id)
            seen_shared.append(shared)
            self.assertIn("ContextPack", shared)
            self.assertIn("supervisor_context_pack", shared)
            self.assertIn("README.md", shared)
            state.record_task_result(task, f"done {task.id}")
            return task.title, f"worker report for {task.id}"

        async def fake_run_agent(*args, **kwargs):
            raise AssertionError("summary helper should not run")

        original_workspace = runner_module.RunWorkspace
        original_ledger = runner_module.PatchProposalLedger
        original_task_runner = runner_module._run_planned_task
        runner_module.RunWorkspace = FakeWorkspace
        runner_module.PatchProposalLedger = FakeLedger
        runner_module._run_planned_task = fake_run_planned_task
        try:
            asyncio.run(
                runner_module._run_multi_agent(
                    "Inspect shared project context.",
                    plan,
                    Path("."),
                    "synthesizer",
                    FakeFactory(),
                    object(),
                    fake_run_agent,
                    run_state,
                    execution_mode="full",
                    show_progress=False,
                )
            )
        finally:
            runner_module.RunWorkspace = original_workspace
            runner_module.PatchProposalLedger = original_ledger
            runner_module._run_planned_task = original_task_runner

        self.assertEqual(len(run_state.run_context.context_packs), 1)
        self.assertEqual(len(seen_shared), 2)
        self.assertTrue(all(shared.count("supervisor_context_pack") == 1 for shared in seen_shared))

    def test_full_runner_seeds_supervisor_context_before_team_workers(self):
        import runtime.execution.multi_agent_runner as runner_module
        from runtime.execution.pipeline import PipelineRunState

        plan = PlannerResult(
            route_type="multi_agent",
            reason="parallel read",
            refined_request="Inspect runtime.",
            needs_synthesis=False,
            memory_interface={
                "execution_contract": {
                    "supervisor_route": "team",
                    "summary_helper": {"enabled": False, "reason": "lead_supervisor_final_answer"},
                }
            },
            tasks=[
                PlannedTask(
                    id="read_execution",
                    title="Read execution",
                    instruction="Read runtime execution code.",
                    skill_id="project_explorer",
                    model="executor",
                    mcp=["project_filesystem_readonly"],
                    parallel_group=1,
                    read_set=["runtime/execution"],
                ),
                PlannedTask(
                    id="read_agent",
                    title="Read agent",
                    instruction="Read runtime agent code.",
                    skill_id="project_explorer",
                    model="executor",
                    mcp=["project_filesystem_readonly"],
                    parallel_group=1,
                    read_set=["runtime/agent"],
                ),
            ],
        )
        run_state = PipelineRunState.create("Inspect runtime.", plan, project_root=Path("."))

        class FakeWorkspace:
            def __init__(self, project_root):
                self.project_root = project_root

            def create(self):
                return Path(self.project_root)

            def write_task_output(self, task_id, title, output):
                pass

            def cleanup(self):
                pass

        class FakeLedger:
            def __init__(self, project_root):
                self.project_root = project_root

        class FakeFactory:
            def create_synthesizer_agent(self, model_id, run_workspace_server):
                raise AssertionError("summary helper should be skipped for supervised full team")

        async def fake_run_planned_task(refined_request, task, project_root, factory, hooks, run_agent, state, ledger, **kwargs):
            shared = state.run_context.render_for_task(task.id)
            self.assertIn("主管上下文包", shared)
            self.assertIn("WorkerReport", shared)
            self.assertIn("runtime/execution", shared)
            state.record_task_result(task, f"done {task.id}")
            return task.title, f"worker report for {task.id}"

        async def fake_run_agent(*args, **kwargs):
            raise AssertionError("summary helper should not run")

        original_workspace = runner_module.RunWorkspace
        original_ledger = runner_module.PatchProposalLedger
        original_task_runner = runner_module._run_planned_task
        runner_module.RunWorkspace = FakeWorkspace
        runner_module.PatchProposalLedger = FakeLedger
        runner_module._run_planned_task = fake_run_planned_task
        try:
            output = asyncio.run(
                runner_module._run_multi_agent(
                    "Inspect runtime.",
                    plan,
                    Path("."),
                    "synthesizer",
                    FakeFactory(),
                    object(),
                    fake_run_agent,
                    run_state,
                    execution_mode="full",
                    show_progress=False,
                )
            )
        finally:
            runner_module.RunWorkspace = original_workspace
            runner_module.PatchProposalLedger = original_ledger
            runner_module._run_planned_task = original_task_runner

        self.assertIn("主管最终汇报", output)


if __name__ == "__main__":
    unittest.main()
