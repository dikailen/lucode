from __future__ import annotations

import unittest


class OutputViewModelTests(unittest.TestCase):
    def test_groups_runtime_events_without_rendering_text(self):
        from runtime.events import ExecutionEventBus
        from runtime.ui.output_model import OutputItemKind, OutputVisibility
        from runtime.ui.output_view import build_output_view_model

        bus = ExecutionEventBus()
        bus.emit("PlanningStarted", "开始规划", mode="full", agent="orchestrator")
        bus.emit("TaskStarted", "Inspect project", task_id="inspect", status="running")
        bus.emit(
            "ToolInvoked",
            "read file",
            mode="full",
            task_id="inspect",
            status="completed",
            payload={
                "tool": "project_filesystem_readonly.read_file",
                "action": "read_file",
                "files_touched": [{"path": "runtime/ui/progress.py", "access": "read"}],
            },
        )
        bus.emit("TaskCompleted", "Inspect done", task_id="inspect", status="completed")
        bus.emit(
            "LeadReviewFinding",
            "missing evidence",
            task_id="inspect",
            status="warning",
            payload={"kind": "missing_evidence", "severity": "warning"},
        )
        bus.emit("SupervisorObservation", "readonly task can run", agent="supervisor", status="completed")
        bus.emit("ToolApproved", "主管批准只读工具", agent="supervisor", status="supervisor_auto_approved")

        view = build_output_view_model(events=bus.snapshot(), mode="full", route="single_agent")

        self.assertEqual(view.mode, "full")
        self.assertEqual(view.route, "single_agent")
        self.assertTrue(any(item.kind == OutputItemKind.PROGRESS for item in view.items))
        self.assertTrue(any(item.kind == OutputItemKind.TOOL for item in view.items))
        self.assertTrue(any(item.kind == OutputItemKind.WORKER for item in view.items))
        self.assertTrue(any(item.kind == OutputItemKind.LEAD_REVIEW for item in view.items))
        self.assertTrue(any(item.kind == OutputItemKind.SUPERVISOR for item in view.items))
        self.assertTrue(all(item.visibility == OutputVisibility.PERSISTENT for item in view.items))
        self.assertTrue(any(item.kind == OutputItemKind.SUPERVISOR and item.event_type == "ToolApproved" for item in view.items))
        tool_item = next(item for item in view.items if item.kind == OutputItemKind.TOOL)
        self.assertEqual(tool_item.task_id, "inspect")
        self.assertIn("runtime/ui/progress.py", tool_item.summary)
        self.assertEqual(tool_item.metadata["tool"], "project_filesystem_readonly.read_file")
        self.assertEqual(tool_item.metadata["mode"], "full")

    def test_classifies_interactive_panels_and_transient_hints(self):
        from runtime.ui.output_model import OutputItemKind, OutputVisibility
        from runtime.ui.output_view import build_interactive_panel_item, build_transient_hint_item

        panel = build_interactive_panel_item(
            "models",
            title="Lucode 多脑模型调音台",
            body="静态模型调音台快照",
            summary="选择模型",
        )
        hint = build_transient_hint_item("已退出历史面板。", source="history")

        self.assertEqual(panel.kind, OutputItemKind.INTERACTIVE_PANEL)
        self.assertEqual(panel.visibility, OutputVisibility.TRANSIENT)
        self.assertEqual(panel.source, "models")
        self.assertEqual(hint.kind, OutputItemKind.TRANSIENT_HINT)
        self.assertEqual(hint.visibility, OutputVisibility.TRANSIENT)
        self.assertEqual(hint.source, "history")

    def test_keeps_operation_results_persistent(self):
        from runtime.ui.output_model import OutputItemKind, OutputVisibility
        from runtime.ui.output_view import build_operation_result_item

        saved = build_operation_result_item("已保存 Provider 凭据：deepseek", source="connect")
        failed = build_operation_result_item("连接失败：base_url 不可用", source="connect", failed=True)

        self.assertEqual(saved.kind, OutputItemKind.OPERATION_RESULT)
        self.assertEqual(saved.visibility, OutputVisibility.PERSISTENT)
        self.assertEqual(failed.kind, OutputItemKind.DIAGNOSTIC)
        self.assertEqual(failed.visibility, OutputVisibility.PERSISTENT)
        self.assertIn("连接失败", failed.summary)


class PlainTextRendererTests(unittest.TestCase):
    def test_renders_output_view_model_groups(self):
        from runtime.events import ExecutionEventBus
        from runtime.ui.output_view import build_output_view_model
        from runtime.ui.plain_text_renderer import PlainTextRenderOptions, PlainTextRenderer

        bus = ExecutionEventBus()
        bus.emit("TaskStarted", "Inspect project", task_id="inspect", status="running")
        bus.emit(
            "ToolInvoked",
            "read file",
            task_id="inspect",
            status="completed",
            payload={
                "tool": "project_filesystem_readonly.read_file",
                "action": "read_file",
                "files_touched": [{"path": "runtime/ui/progress.py", "access": "read"}],
            },
        )
        bus.emit("TaskCompleted", "Inspect done", task_id="inspect", status="completed")
        bus.emit(
            "LeadReviewFinding",
            "missing evidence",
            task_id="inspect",
            status="warning",
            payload={"kind": "missing_evidence", "severity": "warning", "evidence": "no tests listed"},
        )
        bus.emit("ToolApproved", "supervisor auto approved", agent="supervisor", status="auto_approved")

        view = build_output_view_model(bus.snapshot(), mode="full", route="team")
        rendered = PlainTextRenderer().render_event_summary(view, PlainTextRenderOptions(limit=20))

        self.assertIn("执行摘要", rendered)
        self.assertIn("工具摘要", rendered)
        self.assertIn("project_filesystem_readonly.read_file x1", rendered)
        self.assertIn("runtime/ui/progress.py", rendered)
        self.assertIn("Worker 分组", rendered)
        self.assertIn("inspect: completed", rendered)
        self.assertIn("LeadReview", rendered)
        self.assertIn("warning=1", rendered)
        self.assertIn("Supervisor", rendered)
        self.assertIn("主管审批", rendered)

    def test_parallel_batch_events_render_as_concise_summary(self):
        from runtime.events import ExecutionEventBus
        from runtime.ui.output_model import OutputItemKind
        from runtime.ui.output_view import build_output_view_model
        from runtime.ui.plain_text_renderer import PlainTextRenderOptions, PlainTextRenderer

        bus = ExecutionEventBus()
        bus.emit(
            "ParallelBatchStarted",
            "parallel batch started",
            mode="full",
            agent="supervisor",
            status="running",
            payload={
                "group_id": 1,
                "task_ids": ["inspect_runtime", "inspect_tests"],
                "batch_size": 2,
                "reason": "readonly_no_write_conflict",
            },
        )

        view = build_output_view_model(bus.snapshot(), mode="full", route="team")
        rendered = PlainTextRenderer().render_event_summary(view, PlainTextRenderOptions(limit=20))

        self.assertTrue(any(item.kind == OutputItemKind.PROGRESS for item in view.items))
        self.assertIn("Parallel Batch", rendered)
        self.assertIn("group 1", rendered)
        self.assertIn("2 workers", rendered)
        self.assertIn("readonly_no_write_conflict", rendered)

    def test_worker_output_stored_event_renders_expand_hint(self):
        from runtime.events import ExecutionEventBus
        from runtime.ui.output_model import OutputItemKind
        from runtime.ui.output_view import build_output_view_model
        from runtime.ui.plain_text_renderer import PlainTextRenderOptions, PlainTextRenderer

        bus = ExecutionEventBus()
        bus.emit("TaskStarted", "Inspect runtime", task_id="inspect_runtime", status="running")
        bus.emit(
            "WorkerOutputStored",
            "Worker output saved: /expand worker-inspect_runtime",
            task_id="inspect_runtime",
            status="completed",
            payload={"block_id": "worker-inspect_runtime"},
        )

        view = build_output_view_model(bus.snapshot(), mode="full", route="team")
        rendered = PlainTextRenderer().render_event_summary(view, PlainTextRenderOptions(limit=20))

        self.assertTrue(any(item.kind == OutputItemKind.WORKER for item in view.items))
        self.assertIn("Worker 分组", rendered)
        self.assertIn("/expand worker-inspect_runtime", rendered)

    def test_hides_transient_items_by_default(self):
        from runtime.ui.output_model import OutputViewModel
        from runtime.ui.output_view import build_interactive_panel_item, build_operation_result_item
        from runtime.ui.plain_text_renderer import PlainTextRenderer

        view = OutputViewModel(
            items=[
                build_interactive_panel_item("models", title="Model panel", body="STATIC MODEL PANEL"),
                build_operation_result_item("已保存 Provider 凭据：deepseek", source="connect"),
            ],
            mode="full",
        )

        rendered = PlainTextRenderer().render_operation_items(view)

        self.assertIn("已保存 Provider 凭据", rendered)
        self.assertNotIn("STATIC MODEL PANEL", rendered)

    def test_can_include_transient_items_for_debug(self):
        from runtime.ui.output_model import OutputViewModel
        from runtime.ui.output_view import build_interactive_panel_item
        from runtime.ui.plain_text_renderer import PlainTextRenderOptions, PlainTextRenderer

        view = OutputViewModel(
            items=[build_interactive_panel_item("history", title="History browser", body="Lucode History")],
            mode="full",
        )

        rendered = PlainTextRenderer().render_operation_items(
            view,
            PlainTextRenderOptions(include_transient=True),
        )

        self.assertIn("Lucode History", rendered)


class LiveStatusRenderingTests(unittest.TestCase):
    def test_planning_status_text_is_minimal_and_hides_prompt_and_mode(self):
        from runtime.ui.live_status import render_status_text
        from runtime.ui.plan_display import render_planning_status

        prompt = "用 full 模式并行只读分析 runtime/ui 和 tests 目录，输出工具执行摘要即可"

        self.assertEqual(render_status_text(prompt, mode="full", stage="planning"), "Planning")
        rendered = render_planning_status(prompt, mode="full", stage="planning")

        self.assertEqual(rendered, "Planning")
        self.assertNotIn("full", rendered)
        self.assertNotIn("runtime/ui", rendered)
        self.assertNotIn("tests", rendered)

    def test_planning_dynamic_status_uses_snow_spinner(self):
        import os
        from unittest.mock import patch

        from runtime.ui.live_status import dynamic_status

        calls = []

        class FakeStatus:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        class FakeConsole:
            def __init__(self, **kwargs):
                self.kwargs = dict(kwargs)

            def status(self, *args, **kwargs):
                calls.append((args, kwargs))
                return FakeStatus()

        with patch.dict(os.environ, {"AGENTS_DYNAMIC_UI": "on"}, clear=False), patch("rich.console.Console", FakeConsole):
            with dynamic_status("user prompt", mode="full", stage="planning"):
                pass

        self.assertEqual(calls[0][0], ("Planning",))
        self.assertEqual(calls[0][1]["spinner"], "lucode_snow")


if __name__ == "__main__":
    unittest.main(verbosity=2)
