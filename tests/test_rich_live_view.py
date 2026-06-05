from __future__ import annotations

import unittest


def _state_with_tasks(route_type: str = "multi_agent"):
    from planning.planner_schema import PlannedTask, PlannerResult
    from runtime.execution.pipeline import PipelineRunState

    tasks = [
        PlannedTask(
            id="inspect_runtime",
            title="Inspect runtime UI",
            instruction="Inspect runtime/ui.",
            skill_id="project_explorer",
            model="model",
            mcp=["project_filesystem_readonly"],
        ),
        PlannedTask(
            id="inspect_tests",
            title="Inspect tests",
            instruction="Inspect tests.",
            skill_id="project_explorer",
            model="model",
            mcp=["project_filesystem_readonly"],
        ),
        PlannedTask(
            id="summarize",
            title="Summarize findings",
            instruction="Summarize.",
            skill_id="summary_helper",
            model="model",
            mcp=[],
            depends_on=["inspect_runtime", "inspect_tests"],
        ),
    ]
    plan = PlannerResult(route_type=route_type, reason="test", refined_request="inspect project", tasks=tasks)
    return PipelineRunState.create("inspect project", plan, mode="full"), tasks


class RichLiveViewTests(unittest.TestCase):
    def test_builds_plan_and_separate_supervisor_worker_blocks(self):
        from runtime.ui.rich_live_view import build_rich_live_view

        state, tasks = _state_with_tasks()
        state.event_bus.emit(
            "PlanningStarted",
            "planning",
            mode="full",
            agent="orchestrator",
            payload={"planner_model_id": "deepseek_v4_pro_model"},
        )
        state.record_task_started(tasks[0])
        state.record_task_started(tasks[1])
        state.record_task_result(tasks[0], "runtime done")
        state.event_bus.emit(
            "ToolInvoked",
            "read tests",
            task_id="inspect_tests",
            status="completed",
            payload={
                "tool": "project_filesystem_readonly.read_file",
                "action": "read_file",
                "files_touched": [{"path": "tests/test_output_controller.py", "access": "read"}],
            },
        )

        view = build_rich_live_view(state, mode="full", attempt=1, active="Inspect tests")

        self.assertEqual(view.mode, "full")
        self.assertEqual(view.route, "team")
        self.assertEqual([item.id for item in view.plan_items], ["inspect_runtime", "inspect_tests", "summarize"])
        self.assertEqual([item.status for item in view.plan_items], ["completed", "running", "pending"])
        self.assertEqual(view.actor_blocks[0].role, "supervisor")
        self.assertEqual(view.actor_blocks[0].title, "Supervisor")
        self.assertEqual(view.actor_blocks[0].model_label, "deepseek_v4_pro_model")
        worker_titles = [block.title for block in view.actor_blocks[1:]]
        self.assertEqual(worker_titles, ["Worker 1", "Worker 2"])
        self.assertEqual([block.model_label for block in view.actor_blocks[1:]], ["model", "model"])
        self.assertIn("Reading tests/test_output_controller.py", view.actor_blocks[2].current_action)

    def test_actor_model_labels_render_compactly_without_extra_tree_branches(self):
        from runtime.ui.rich_live import render_rich_live_snapshot

        state, tasks = _state_with_tasks()
        state.event_bus.emit(
            "PlanningStarted",
            "planning",
            mode="full",
            agent="orchestrator",
            payload={"planner_model_id": "deepseek_v4_pro_model"},
        )
        state.record_task_started(tasks[0])

        rendered = render_rich_live_snapshot(state, mode="full", attempt=1, active="Inspect runtime")

        self.assertIn("Supervisor  full / team  deepseek_v4_pro_model", rendered)
        self.assertIn("Worker 1  inspect_runtime  model", rendered)
        self.assertIn("└ Inspect runtime", rendered)
        self.assertEqual(rendered.count("└"), 2)

    def test_long_actor_model_labels_are_compacted(self):
        from runtime.ui.rich_live_view import build_rich_live_view

        state, tasks = _state_with_tasks()
        state.tasks[0].model = "provider_model_with_a_very_long_identifier_that_should_not_fill_the_terminal"
        state.record_task_started(tasks[0])

        view = build_rich_live_view(state, mode="full", attempt=1, active="Inspect runtime")

        self.assertLessEqual(len(view.actor_blocks[1].model_label), 32)
        self.assertTrue(view.actor_blocks[1].model_label.endswith("..."))

    def test_actor_model_labels_prefer_runtime_friendly_names(self):
        from runtime.ui.rich_live_view import build_rich_live_view

        state, tasks = _state_with_tasks()
        state.model_labels = {
            "deepseek_v4_pro_model": "DeepSeek V4 Pro",
            "kimi_k2_model": "Kimi K2",
        }
        state.tasks[0].model = "kimi_k2_model"
        state.event_bus.emit(
            "PlanningStarted",
            "planning",
            mode="full",
            agent="orchestrator",
            payload={"planner_model_id": "deepseek_v4_pro_model"},
        )
        state.record_task_started(tasks[0])

        view = build_rich_live_view(state, mode="full", attempt=1, active="Inspect runtime")

        self.assertEqual(view.actor_blocks[0].model_label, "DeepSeek V4 Pro")
        self.assertEqual(view.actor_blocks[1].model_label, "Kimi K2")

    def test_missing_friendly_model_label_falls_back_to_model_id(self):
        from runtime.ui.rich_live_view import build_rich_live_view

        state, tasks = _state_with_tasks()
        state.model_labels = {"other_model": "Other"}
        state.tasks[0].model = "unknown_model"
        state.record_task_started(tasks[0])

        view = build_rich_live_view(state, mode="full", attempt=1, active="Inspect runtime")

        self.assertEqual(view.actor_blocks[1].model_label, "unknown_model")

    def test_latest_tool_action_wins_without_accumulating_history(self):
        from runtime.ui.rich_live import render_rich_live_snapshot
        from runtime.ui.rich_live_view import build_rich_live_view

        state, tasks = _state_with_tasks()
        state.record_task_started(tasks[0])
        state.event_bus.emit(
            "ToolInvoked",
            "old read",
            task_id="inspect_runtime",
            status="completed",
            payload={
                "tool": "project_filesystem_readonly.read_file",
                "action": "read_file",
                "files_touched": [{"path": "runtime/ui/old.py", "access": "read"}],
            },
        )
        state.event_bus.emit(
            "ToolInvoked",
            "new read",
            task_id="inspect_runtime",
            status="completed",
            payload={
                "tool": "project_filesystem_readonly.read_file",
                "action": "read_file",
                "files_touched": [{"path": "runtime/ui/progress.py", "access": "read"}],
            },
        )

        view = build_rich_live_view(state, mode="full", attempt=1, active="Inspect runtime")
        rendered = render_rich_live_snapshot(state, mode="full", attempt=1, active="Inspect runtime")

        self.assertEqual(view.actor_blocks[1].current_action, "Reading runtime/ui files")
        self.assertIn("Plan", rendered)
        self.assertIn("\u2726 Supervisor", rendered)
        self.assertIn("\u2726 Worker 1", rendered)
        self.assertIn("└ Reading runtime/ui files", rendered)
        self.assertNotIn("runtime/ui/old.py", rendered)
        self.assertNotIn("Tools", rendered)
        self.assertNotIn("Lead Review", rendered)

    def test_multiple_read_events_are_collapsed_into_one_stable_worker_action(self):
        from runtime.ui.rich_live import render_rich_live_snapshot
        from runtime.ui.rich_live_view import build_rich_live_view

        state, tasks = _state_with_tasks()
        state.record_task_started(tasks[0])
        for path in [
            "runtime/ui/plan_display.py",
            "runtime/ui/live_status.py",
            "tests/test_output_rendering.py",
            "tests/test_rich_live_view.py",
        ]:
            state.event_bus.emit(
                "ToolInvoked",
                f"read {path}",
                task_id="inspect_runtime",
                status="completed",
                payload={
                    "tool": "project_filesystem_readonly.read_file",
                    "action": "read_file",
                    "files_touched": [{"path": path, "access": "read"}],
                },
            )

        view = build_rich_live_view(state, mode="full", attempt=1, active="Inspect runtime")
        rendered = render_rich_live_snapshot(state, mode="full", attempt=1, active="Inspect runtime")

        self.assertEqual(view.actor_blocks[1].current_action, "Reading runtime/ui and tests")
        self.assertIn("Reading runtime/ui and tests", rendered)
        self.assertNotIn("tests/test_rich_live_view.py", rendered)
        self.assertNotIn("runtime/ui/live_status.py", rendered)

    def test_task_messages_do_not_override_current_tool_action(self):
        from runtime.ui.rich_live_view import build_rich_live_view

        state, tasks = _state_with_tasks()
        state.record_task_started(tasks[0])
        state.event_bus.emit(
            "ToolInvoked",
            "read file",
            task_id="inspect_runtime",
            status="completed",
            payload={
                "tool": "project_filesystem_readonly.read_file",
                "action": "read_file",
                "files_touched": [{"path": "runtime/ui/rich_live_view.py", "access": "read"}],
            },
        )
        state.event_bus.emit(
            "TaskStarted",
            "分析 runtime/ui 和 tests 目录",
            task_id="inspect_runtime",
            status="running",
        )

        view = build_rich_live_view(state, mode="full", attempt=1, active="Inspect runtime")

        self.assertEqual(view.actor_blocks[1].current_action, "Reading runtime/ui/rich_live_view.py")
        self.assertNotIn("分析 runtime/ui", view.actor_blocks[1].current_action)

    def test_write_search_and_run_actions_are_named_by_action_group(self):
        from runtime.ui.rich_live_view import build_rich_live_view

        state, tasks = _state_with_tasks()
        state.record_task_started(tasks[0])
        state.event_bus.emit(
            "ToolInvoked",
            "search tests",
            task_id="inspect_runtime",
            status="completed",
            payload={"tool": "code_locator.search", "action": "search"},
        )
        self.assertEqual(
            build_rich_live_view(state, mode="full", attempt=1).actor_blocks[1].current_action,
            "Searching project",
        )
        state.event_bus.emit(
            "ToolInvoked",
            "write file",
            task_id="inspect_runtime",
            status="completed",
            payload={
                "tool": "workspace_edit.write_file",
                "action": "write_file",
                "files_touched": [{"path": "runtime/ui/rich_live_view.py", "access": "write"}],
            },
        )
        self.assertEqual(
            build_rich_live_view(state, mode="full", attempt=1).actor_blocks[1].current_action,
            "Writing runtime/ui/rich_live_view.py",
        )
        state.event_bus.emit(
            "ToolInvoked",
            "run tests",
            task_id="inspect_runtime",
            status="completed",
            payload={
                "tool": "command_runner.run_command",
                "action": "run_command",
                "arguments_summary": {"command": "python -m unittest tests.test_rich_live_view"},
            },
        )
        self.assertEqual(
            build_rich_live_view(state, mode="full", attempt=1).actor_blocks[1].current_action,
            "Running python -m unittest tests.test_rich_live_view",
        )

    def test_fast_path_and_fallback_actions_are_compact(self):
        from runtime.ui.rich_live_view import build_rich_live_view

        state, tasks = _state_with_tasks(route_type="single_agent")
        state.record_task_started(tasks[0])
        state.event_bus.emit(
            "FastPathUsed",
            "git diff",
            task_id="inspect_runtime",
            status="completed",
            payload={"tool": "git", "action": "diff"},
        )

        view = build_rich_live_view(state, mode="full", attempt=1, active="Inspect runtime")

        self.assertEqual(view.route, "single_agent")
        self.assertEqual(len(view.actor_blocks), 2)
        self.assertEqual(view.actor_blocks[1].current_action, "Using fast path: git.diff")

    def test_plain_fallback_uses_same_plan_actor_shape_without_internal_marker(self):
        from unittest.mock import patch

        from runtime.ui.rich_live import render_rich_live_snapshot

        state, tasks = _state_with_tasks(route_type="single_agent")
        state.record_task_started(tasks[0])
        original_import = __import__

        def fake_import(name, *args, **kwargs):
            if name == "rich.console":
                raise ImportError("rich unavailable")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            rendered = render_rich_live_snapshot(state, mode="full", attempt=1, active="Inspect runtime")

        self.assertIn("Plan", rendered)
        self.assertIn("\u2726 Supervisor", rendered)
        self.assertIn("\u2726 Worker 1", rendered)
        self.assertIn("└ Inspect runtime", rendered)
        self.assertNotIn("lucode rich live", rendered)


    def test_running_actor_symbol_animates_with_snow_frame(self):
        from runtime.ui.rich_live import render_rich_live_snapshot

        state, tasks = _state_with_tasks(route_type="single_agent")
        state.record_task_started(tasks[0])

        frame_0 = render_rich_live_snapshot(state, mode="full", attempt=1, active="Inspect runtime", frame_index=0)
        frame_3 = render_rich_live_snapshot(state, mode="full", attempt=1, active="Inspect runtime", frame_index=3)

        self.assertIn("Worker 1", frame_0)
        self.assertIn("Worker 1", frame_3)
        self.assertIn("\u2726 Worker 1", frame_0)
        self.assertIn("\u2737 Worker 1", frame_3)
        self.assertNotEqual(frame_0, frame_3)

    def test_completed_failed_and_pending_symbols_do_not_animate(self):
        from runtime.ui.rich_live import render_rich_live_snapshot

        state, tasks = _state_with_tasks()
        state.record_task_started(tasks[0])
        state.record_task_result(tasks[0], "done")
        state.record_task_started(tasks[1])
        state.record_task_error(tasks[1], "failed")

        frame_0 = render_rich_live_snapshot(state, mode="full", attempt=1, active="Inspect", frame_index=0)
        frame_4 = render_rich_live_snapshot(state, mode="full", attempt=1, active="Inspect", frame_index=4)

        for symbol in ("\u2713 Worker 1", "\u00d7 Worker 2", "\u25cb Summarize findings"):
            self.assertIn(symbol, frame_0)
            self.assertIn(symbol, frame_4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
