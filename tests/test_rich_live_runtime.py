from __future__ import annotations

import unittest


class FakeLive:
    created: list["FakeLive"] = []

    def __init__(self, renderable, **kwargs):
        self.renderable = renderable
        self.kwargs = dict(kwargs)
        self.started = False
        self.stopped = False
        self.start_refresh_values = []
        self.updates = []
        FakeLive.created.append(self)

    def start(self, refresh=False) -> None:
        self.start_refresh_values.append(refresh)
        self.started = True

    def update(self, renderable) -> None:
        self.updates.append(renderable)
        self.renderable = renderable

    def stop(self) -> None:
        self.stopped = True


def _run_state():
    from planning.planner_schema import PlannedTask, PlannerResult
    from runtime.execution.pipeline import PipelineRunState

    task = PlannedTask(
        id="inspect",
        title="Inspect",
        instruction="Inspect files.",
        skill_id="project_explorer",
        model="model",
        mcp=["project_filesystem_readonly"],
    )
    plan = PlannerResult(route_type="single_agent", reason="test", refined_request="check files", tasks=[task])
    state = PipelineRunState.create("check files", plan, mode="full")
    state.record_task_started(task)
    return state


class RichLiveRuntimeTests(unittest.TestCase):
    def setUp(self):
        FakeLive.created.clear()

    def test_start_refresh_and_stop_use_one_transient_live_instance(self):
        from runtime.ui.rich_live_runtime import RichLiveRuntime

        state = _run_state()
        runtime = RichLiveRuntime(enabled=True, live_factory=FakeLive)

        self.assertTrue(runtime.refresh(state, mode="full", attempt=1, active="Inspect"))
        self.assertTrue(runtime.active)
        self.assertEqual(len(FakeLive.created), 1)
        live = FakeLive.created[0]
        self.assertTrue(live.started)
        self.assertEqual(live.start_refresh_values, [True])
        self.assertTrue(live.kwargs["transient"])

        self.assertTrue(runtime.refresh(state, mode="full", attempt=1, active="Still inspecting"))
        self.assertEqual(len(FakeLive.created), 1)
        self.assertEqual(len(live.updates), 1)

        runtime.stop()
        self.assertFalse(runtime.active)
        self.assertTrue(live.stopped)

    def test_pause_stops_live_and_resume_starts_a_new_instance(self):
        from runtime.ui.rich_live_runtime import RichLiveRuntime

        state = _run_state()
        runtime = RichLiveRuntime(enabled=True, live_factory=FakeLive)

        runtime.refresh(state, mode="full", attempt=1, active="Inspect")
        first = FakeLive.created[0]
        runtime.pause("approval")

        self.assertFalse(runtime.active)
        self.assertTrue(first.stopped)

        self.assertTrue(runtime.resume(state, mode="full", attempt=1, active="Inspect again"))
        self.assertTrue(runtime.active)
        self.assertEqual(len(FakeLive.created), 2)
        self.assertIsNot(FakeLive.created[1], first)
        runtime.stop()

    def test_disabled_runtime_is_noop(self):
        from runtime.ui.rich_live_runtime import RichLiveRuntime

        runtime = RichLiveRuntime(enabled=False, live_factory=FakeLive)

        self.assertFalse(runtime.refresh(_run_state(), mode="full", attempt=1, active="Inspect"))
        self.assertFalse(runtime.active)
        self.assertEqual(FakeLive.created, [])

    def test_default_live_path_passes_forced_console(self):
        from runtime.ui.rich_live_runtime import RichLiveRuntime

        consoles = []

        class FakeConsole:
            def __init__(self, **kwargs):
                self.kwargs = dict(kwargs)
                consoles.append(self)

        runtime = RichLiveRuntime(enabled=True, live_factory=FakeLive, console_factory=FakeConsole)

        self.assertTrue(runtime.refresh(_run_state(), mode="full", attempt=1, active="Inspect"))

        self.assertEqual(len(consoles), 1)
        self.assertTrue(consoles[0].kwargs["force_terminal"])
        self.assertEqual(FakeLive.created[0].kwargs["console"], consoles[0])
        runtime.stop()

    def test_live_factory_failure_disables_runtime_without_raising(self):
        from runtime.ui.rich_live_runtime import RichLiveRuntime

        def broken_factory(*args, **kwargs):
            raise RuntimeError("terminal unavailable")

        runtime = RichLiveRuntime(enabled=True, live_factory=broken_factory)

        self.assertFalse(runtime.refresh(_run_state(), mode="full", attempt=1, active="Inspect"))
        self.assertFalse(runtime.active)
        self.assertFalse(runtime.enabled)

    def test_stop_is_idempotent(self):
        from runtime.ui.rich_live_runtime import RichLiveRuntime

        runtime = RichLiveRuntime(enabled=True, live_factory=FakeLive)
        runtime.refresh(_run_state(), mode="full", attempt=1, active="Inspect")
        live = FakeLive.created[0]

        runtime.stop()
        runtime.stop()

        self.assertTrue(live.stopped)
        self.assertFalse(runtime.active)

    def test_dynamic_status_is_suppressed_while_rich_live_owns_terminal(self):
        import os
        from unittest.mock import patch

        from runtime.ui.live_status import dynamic_status
        from runtime.ui.rich_live_runtime import RichLiveRuntime

        status_calls = []

        class FakeStatus:
            def __enter__(self):
                status_calls.append("enter")
                return self

            def __exit__(self, exc_type, exc, tb):
                status_calls.append("exit")
                return False

        class FakeConsole:
            def __init__(self, **kwargs):
                self.kwargs = dict(kwargs)

            def status(self, *args, **kwargs):
                status_calls.append((args, kwargs))
                return FakeStatus()

        runtime = RichLiveRuntime(enabled=True, live_factory=FakeLive)
        self.assertTrue(runtime.refresh(_run_state(), mode="full", attempt=1, active="Inspect"))

        with patch.dict(os.environ, {"AGENTS_DYNAMIC_UI": "on"}, clear=False), patch("rich.console.Console", FakeConsole):
            with dynamic_status("group 1 - 2 workers", mode="full", stage="batch"):
                status_calls.append("inside")

        runtime.stop()
        self.assertEqual(status_calls, ["inside"])

    def test_dynamic_status_resumes_after_rich_live_stops(self):
        import os
        from unittest.mock import patch

        from runtime.ui.live_status import dynamic_status
        from runtime.ui.rich_live_runtime import RichLiveRuntime

        status_calls = []

        class FakeStatus:
            def __enter__(self):
                status_calls.append("enter")
                return self

            def __exit__(self, exc_type, exc, tb):
                status_calls.append("exit")
                return False

        class FakeConsole:
            def __init__(self, **kwargs):
                self.kwargs = dict(kwargs)

            def status(self, *args, **kwargs):
                status_calls.append((args, kwargs))
                return FakeStatus()

        runtime = RichLiveRuntime(enabled=True, live_factory=FakeLive)
        runtime.refresh(_run_state(), mode="full", attempt=1, active="Inspect")
        runtime.stop()

        with patch.dict(os.environ, {"AGENTS_DYNAMIC_UI": "on"}, clear=False), patch("rich.console.Console", FakeConsole):
            with dynamic_status("worker", mode="full", stage="worker"):
                status_calls.append("inside")

        self.assertEqual(len(status_calls), 4)
        self.assertEqual(status_calls[0][0], ("Working  full  worker",))
        self.assertEqual(status_calls[1:], ["enter", "inside", "exit"])

    def test_refresh_advances_frame_index_for_renderable(self):
        from unittest.mock import patch

        from runtime.ui.rich_live_runtime import RichLiveRuntime

        frame_indexes = []

        def fake_renderable(*args, **kwargs):
            frame_indexes.append(kwargs.get("frame_index"))
            return f"frame-{kwargs.get('frame_index')}"

        runtime = RichLiveRuntime(enabled=True, live_factory=FakeLive)
        with patch("runtime.ui.rich_live_runtime.build_rich_live_renderable", side_effect=fake_renderable):
            runtime.refresh(_run_state(), mode="full", attempt=1, active="Inspect")
            runtime.refresh(_run_state(), mode="full", attempt=1, active="Inspect again")
            runtime.refresh(_run_state(), mode="full", attempt=1, active="Still inspecting")

        runtime.stop()
        self.assertEqual(frame_indexes, [0, 1, 2])

    def test_live_renderable_advances_frame_when_rich_repaints(self):
        from rich.console import Console
        from io import StringIO

        from runtime.ui.rich_live_runtime import RichLiveRuntime

        runtime = RichLiveRuntime(enabled=True, live_factory=FakeLive)
        runtime.refresh(_run_state(), mode="full", attempt=1, active="Inspect")
        renderable = FakeLive.created[0].renderable

        first = StringIO()
        second = StringIO()
        Console(file=first, force_terminal=True, color_system=None, width=110).print(renderable)
        Console(file=second, force_terminal=True, color_system=None, width=110).print(renderable)

        runtime.stop()
        self.assertIn("\u2726 Worker 1", first.getvalue())
        self.assertIn("\u2727 Worker 1", second.getvalue())

    def test_actor_model_label_is_rendered_fixed_grey_in_rich_header(self):
        from runtime.ui.rich_live import _build_renderable_from_view
        from runtime.ui.rich_live_view import RichActorBlock, RichLiveView

        view = RichLiveView(
            mode="full",
            route="team",
            attempt=1,
            actor_blocks=[
                RichActorBlock(
                    role="supervisor",
                    title="Supervisor",
                    subtitle="full / team",
                    model_label="DeepSeek V4 Pro",
                    current_action="Waiting for workers",
                    status="running",
                )
            ],
        )

        renderable = _build_renderable_from_view(view, frame_index=0)
        first_line = next(item for item in renderable.renderables if "Supervisor" in item.plain)

        self.assertEqual(first_line.plain, "\u2726 Supervisor  full / team  DeepSeek V4 Pro")
        grey_spans = [
            span
            for span in first_line.spans
            if "DeepSeek V4 Pro" in first_line.plain[span.start : span.end]
            and str(span.style) in {"bright_black", "grey50"}
        ]
        role_spans = [
            span
            for span in first_line.spans
            if "DeepSeek V4 Pro" in first_line.plain[span.start : span.end]
            and str(span.style) in {"bold cyan", "bold magenta", "cyan", "magenta", "dim"}
        ]
        self.assertTrue(grey_spans)
        self.assertFalse(role_spans)


if __name__ == "__main__":
    unittest.main(verbosity=2)
