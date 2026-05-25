from __future__ import annotations

import asyncio
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
        self.assertIn("worker report for read_execution", output)
        self.assertIn("worker report for read_agent", output)

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
