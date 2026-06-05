from __future__ import annotations

import unittest
from contextlib import contextmanager


@contextmanager
def _null_status(*args, **kwargs):
    del args, kwargs
    yield


class OutputControllerTests(unittest.TestCase):
    def test_phase_transitions_define_render_permissions(self):
        from runtime.ui.output_controller import OutputController, OutputPhase

        controller = OutputController(mode="full", route="single_agent")

        self.assertEqual(controller.snapshot().phase, OutputPhase.IDLE)
        self.assertFalse(controller.can_render_dynamic())
        self.assertTrue(controller.can_print_persistent())

        controller.enter_planning("refine request")
        self.assertEqual(controller.snapshot().phase, OutputPhase.PLANNING)
        self.assertTrue(controller.can_render_dynamic())
        self.assertTrue(controller.can_print_persistent())

        controller.enter_running(task_id="inspect_dirs", reason="worker started")
        snapshot = controller.snapshot()
        self.assertEqual(snapshot.phase, OutputPhase.RUNNING)
        self.assertEqual(snapshot.active_task_id, "inspect_dirs")
        self.assertTrue(controller.can_render_dynamic())

        controller.enter_approval_waiting("workspace_edit approval")
        self.assertEqual(controller.snapshot().phase, OutputPhase.APPROVAL_WAITING)
        self.assertFalse(controller.can_render_dynamic())
        self.assertFalse(controller.can_print_persistent())

        controller.enter_running(task_id="inspect_dirs")
        controller.enter_interactive_input("main prompt")
        self.assertEqual(controller.snapshot().phase, OutputPhase.INTERACTIVE_INPUT)
        self.assertFalse(controller.can_render_dynamic())
        self.assertFalse(controller.can_print_persistent())

        controller.enter_finalizing("summary")
        self.assertEqual(controller.snapshot().phase, OutputPhase.FINALIZING)
        self.assertTrue(controller.can_render_dynamic())
        self.assertTrue(controller.can_print_persistent())

        controller.enter_completed("done")
        self.assertEqual(controller.snapshot().phase, OutputPhase.COMPLETED)
        self.assertFalse(controller.can_render_dynamic())
        self.assertTrue(controller.can_print_persistent())

    def test_failed_phase_records_reason_and_blocks_dynamic_rendering(self):
        from runtime.ui.output_controller import OutputController, OutputPhase

        controller = OutputController(mode="serial", route="multi_agent")
        controller.enter_running(task_id="edit")
        controller.enter_failed("tool timeout")

        snapshot = controller.snapshot()
        self.assertEqual(snapshot.phase, OutputPhase.FAILED)
        self.assertEqual(snapshot.reason, "tool timeout")
        self.assertFalse(controller.can_render_dynamic())
        self.assertTrue(controller.can_print_persistent())

    def test_temporary_phase_restores_previous_running_state(self):
        from runtime.ui.output_controller import OutputController, OutputPhase

        controller = OutputController(mode="full", route="single_agent")
        controller.enter_running(task_id="inspect", reason="reading")

        token = controller.push_phase(OutputPhase.APPROVAL_WAITING, reason="approval")
        self.assertEqual(controller.snapshot().phase, OutputPhase.APPROVAL_WAITING)
        self.assertFalse(controller.can_print_persistent())

        controller.restore(token)
        snapshot = controller.snapshot()
        self.assertEqual(snapshot.phase, OutputPhase.RUNNING)
        self.assertEqual(snapshot.active_task_id, "inspect")
        self.assertEqual(snapshot.reason, "reading")

    def test_temporary_phase_context_restores_after_exception(self):
        from runtime.ui.output_controller import OutputController, OutputPhase

        controller = OutputController(mode="full", route="single_agent")
        controller.enter_running(task_id="inspect", reason="reading")

        with self.assertRaises(RuntimeError):
            with controller.temporary_phase(OutputPhase.INTERACTIVE_INPUT, reason="main prompt"):
                self.assertEqual(controller.snapshot().phase, OutputPhase.INTERACTIVE_INPUT)
                raise RuntimeError("prompt failed")

        self.assertEqual(controller.snapshot().phase, OutputPhase.RUNNING)
        self.assertEqual(controller.snapshot().active_task_id, "inspect")

    def test_temporary_phase_restore_does_not_overwrite_terminal_failure(self):
        from runtime.ui.output_controller import OutputController, OutputPhase

        controller = OutputController(mode="full", route="single_agent")
        controller.enter_running(task_id="inspect", reason="reading")

        token = controller.push_phase(OutputPhase.APPROVAL_WAITING, reason="approval")
        controller.enter_failed("turn timeout")
        controller.restore(token)

        snapshot = controller.snapshot()
        self.assertEqual(snapshot.phase, OutputPhase.FAILED)
        self.assertEqual(snapshot.reason, "turn timeout")

    def test_pipeline_run_state_creates_output_controller(self):
        from planning.planner_schema import PlannedTask, PlannerResult
        from runtime.execution.pipeline import PipelineRunState
        from runtime.ui.output_controller import OutputPhase

        plan = PlannerResult(
            route_type="single_agent",
            reason="test",
            refined_request="check files",
            tasks=[
                PlannedTask(
                    id="inspect",
                    title="Inspect",
                    instruction="Inspect files.",
                    skill_id="project_explorer",
                    model="model",
                    mcp=["project_filesystem_readonly"],
                )
            ],
        )

        state = PipelineRunState.create("check files", plan)

        self.assertEqual(state.output_controller.snapshot().phase, OutputPhase.IDLE)
        self.assertEqual(state.output_controller.snapshot().route, "single_agent")
        state.output_controller.enter_running(task_id="inspect")
        self.assertEqual(state.to_dict()["output"]["phase"], "running")
        self.assertEqual(state.to_dict()["output"]["active_task_id"], "inspect")

    def test_pipeline_run_state_can_share_outer_output_controller(self):
        from planning.planner_schema import PlannedTask, PlannerResult
        from runtime.execution.pipeline import PipelineRunState
        from runtime.ui.output_controller import OutputController

        task = PlannedTask(
            id="inspect",
            title="Inspect",
            instruction="Inspect files.",
            skill_id="project_explorer",
            model="model",
            mcp=["project_filesystem_readonly"],
        )
        plan = PlannerResult(route_type="single_agent", reason="test", refined_request="check files", tasks=[task])
        controller = OutputController(mode="full")

        state = PipelineRunState.create("check files", plan, mode="full", output_controller=controller)

        self.assertIs(state.output_controller, controller)
        self.assertEqual(state.output_controller.snapshot().route, "single_agent")

    def test_progress_snapshot_is_suppressed_when_controller_disallows_persistent_output(self):
        import contextlib
        import io

        from planning.planner_schema import PlannedTask, PlannerResult
        from runtime.execution.pipeline import PipelineRunState
        from runtime.execution.progress import _print_progress_snapshot
        from runtime.execution.progress import _render_progress_snapshot

        task = PlannedTask(
            id="inspect",
            title="Inspect",
            instruction="Inspect files.",
            skill_id="project_explorer",
            model="model",
            mcp=["project_filesystem_readonly"],
        )
        plan = PlannerResult(route_type="single_agent", reason="test", refined_request="check files", tasks=[task])
        state = PipelineRunState.create("check files", plan)

        state.output_controller.enter_running(task_id="inspect")
        self.assertIn("inspect", _render_progress_snapshot(state, mode="full", attempt=1))

        state.output_controller.enter_interactive_input("main prompt")
        self.assertEqual(_render_progress_snapshot(state, mode="full", attempt=1), "")
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            _print_progress_snapshot(state, mode="full", attempt=1)
        self.assertEqual(buffer.getvalue(), "")

    def test_progress_snapshot_falls_back_when_rich_preview_import_fails(self):
        from unittest.mock import patch

        from planning.planner_schema import PlannedTask, PlannerResult
        from runtime.execution.pipeline import PipelineRunState
        from runtime.execution.progress import _render_progress_snapshot

        task = PlannedTask(
            id="inspect",
            title="Inspect",
            instruction="Inspect files.",
            skill_id="project_explorer",
            model="model",
            mcp=["project_filesystem_readonly"],
        )
        plan = PlannerResult(route_type="single_agent", reason="test", refined_request="check files", tasks=[task])
        state = PipelineRunState.create("check files", plan)
        state.output_controller.enter_running(task_id="inspect")

        def fake_import(name, *args, **kwargs):
            if name == "runtime.ui.rich_live":
                raise ImportError("rich preview unavailable")
            return original_import(name, *args, **kwargs)

        original_import = __import__
        with patch("runtime.execution.progress.normalize_dynamic_ui_mode", return_value="on"), patch(
            "builtins.__import__", side_effect=fake_import
        ):
            rendered = _render_progress_snapshot(state, mode="full", attempt=1)

        self.assertIn("inspect", rendered)
        self.assertNotIn("● Supervisor", rendered)

    def test_progress_snapshot_uses_rich_live_frame_when_enabled(self):
        from unittest.mock import patch

        from planning.planner_schema import PlannedTask, PlannerResult
        from runtime.execution.pipeline import PipelineRunState
        from runtime.execution.progress import _render_progress_snapshot

        task = PlannedTask(
            id="inspect",
            title="Inspect",
            instruction="Inspect files.",
            skill_id="project_explorer",
            model="model",
            mcp=["project_filesystem_readonly"],
        )
        plan = PlannerResult(route_type="single_agent", reason="test", refined_request="check files", tasks=[task])
        state = PipelineRunState.create("check files", plan)
        state.record_task_started(task)
        state.event_bus.emit(
            "ToolInvoked",
            "read file",
            task_id="inspect",
            status="completed",
            payload={"tool": "project_filesystem_readonly.read_file", "action": "read_file"},
        )

        with patch("runtime.execution.progress.normalize_dynamic_ui_mode", return_value="on"):
            rendered = _render_progress_snapshot(state, mode="full", attempt=1, active="Inspect")

        self.assertIn("Plan", rendered)
        self.assertIn("Supervisor", rendered)
        self.assertIn("Worker 1", rendered)
        self.assertNotIn("Tools", rendered)
        self.assertIn("Reading project files", rendered)
        self.assertNotIn("project_filesystem_readonly.read_file", rendered)

    def test_progress_snapshot_never_uses_rich_live_during_input_or_approval(self):
        from unittest.mock import patch

        from planning.planner_schema import PlannedTask, PlannerResult
        from runtime.execution.pipeline import PipelineRunState
        from runtime.execution.progress import _render_progress_snapshot
        from runtime.ui.output_controller import OutputPhase

        task = PlannedTask(
            id="inspect",
            title="Inspect",
            instruction="Inspect files.",
            skill_id="project_explorer",
            model="model",
            mcp=["project_filesystem_readonly"],
        )
        plan = PlannerResult(route_type="single_agent", reason="test", refined_request="check files", tasks=[task])
        state = PipelineRunState.create("check files", plan)
        state.record_task_started(task)

        with patch("runtime.execution.progress.normalize_dynamic_ui_mode", return_value="on"):
            state.output_controller.enter_interactive_input("main input")
            self.assertEqual(_render_progress_snapshot(state, mode="full", attempt=1), "")
            token = state.output_controller.push_phase(OutputPhase.APPROVAL_WAITING, reason="approval")
            try:
                self.assertEqual(_render_progress_snapshot(state, mode="full", attempt=1), "")
            finally:
                state.output_controller.restore(token)

    def test_progress_print_refreshes_rich_live_without_printing_static_frame(self):
        import contextlib
        import io
        from unittest.mock import patch

        from planning.planner_schema import PlannedTask, PlannerResult
        from runtime.execution.pipeline import PipelineRunState
        from runtime.execution.progress import _print_progress_snapshot

        class FakeRuntime:
            def __init__(self):
                self.refresh_calls = []
                self.pause_calls = []
                self.stop_calls = 0

            def refresh(self, run_state, *, mode, attempt, active=""):
                del run_state
                self.refresh_calls.append((mode, attempt, active))
                return True

            def pause(self, reason=""):
                self.pause_calls.append(reason)

            def stop(self):
                self.stop_calls += 1

        task = PlannedTask(
            id="inspect",
            title="Inspect",
            instruction="Inspect files.",
            skill_id="project_explorer",
            model="model",
            mcp=["project_filesystem_readonly"],
        )
        plan = PlannerResult(route_type="single_agent", reason="test", refined_request="check files", tasks=[task])
        state = PipelineRunState.create("check files", plan)
        runtime = FakeRuntime()
        state._rich_live_runtime = runtime
        state.record_task_started(task)

        buffer = io.StringIO()
        with patch("runtime.execution.progress.normalize_dynamic_ui_mode", return_value="on"), contextlib.redirect_stdout(buffer):
            _print_progress_snapshot(state, mode="full", attempt=1, active="Inspect")

        self.assertEqual(buffer.getvalue(), "")
        self.assertEqual(runtime.refresh_calls, [("full", 1, "Inspect")])
        self.assertEqual(runtime.pause_calls, [])
        self.assertEqual(runtime.stop_calls, 0)

    def test_progress_print_pauses_rich_live_during_interactive_or_approval_phase(self):
        import contextlib
        import io
        from unittest.mock import patch

        from planning.planner_schema import PlannedTask, PlannerResult
        from runtime.execution.pipeline import PipelineRunState
        from runtime.execution.progress import _print_progress_snapshot
        from runtime.ui.output_controller import OutputPhase

        class FakeRuntime:
            def __init__(self):
                self.refresh_calls = 0
                self.pause_calls = []
                self.stop_calls = 0

            def refresh(self, *args, **kwargs):
                del args, kwargs
                self.refresh_calls += 1
                return True

            def pause(self, reason=""):
                self.pause_calls.append(reason)

            def stop(self):
                self.stop_calls += 1

        task = PlannedTask(
            id="inspect",
            title="Inspect",
            instruction="Inspect files.",
            skill_id="project_explorer",
            model="model",
            mcp=["project_filesystem_readonly"],
        )
        plan = PlannerResult(route_type="single_agent", reason="test", refined_request="check files", tasks=[task])
        state = PipelineRunState.create("check files", plan)
        runtime = FakeRuntime()
        state._rich_live_runtime = runtime
        state.record_task_started(task)

        buffer = io.StringIO()
        with patch("runtime.execution.progress.normalize_dynamic_ui_mode", return_value="on"), contextlib.redirect_stdout(buffer):
            state.output_controller.enter_interactive_input("main input")
            _print_progress_snapshot(state, mode="full", attempt=1, active="Inspect")
            state.output_controller.enter_running(task_id="inspect")
            token = state.output_controller.push_phase(OutputPhase.APPROVAL_WAITING, reason="approval")
            try:
                _print_progress_snapshot(state, mode="full", attempt=1, active="Inspect")
            finally:
                state.output_controller.restore(token)

        self.assertEqual(buffer.getvalue(), "")
        self.assertEqual(runtime.refresh_calls, 0)
        self.assertEqual(runtime.pause_calls, ["persistent output blocked", "persistent output blocked"])
        self.assertEqual(runtime.stop_calls, 0)

    def test_progress_print_stops_rich_live_on_completed_phase_without_static_frame(self):
        import contextlib
        import io
        from unittest.mock import patch

        from planning.planner_schema import PlannedTask, PlannerResult
        from runtime.execution.pipeline import PipelineRunState
        from runtime.execution.progress import _print_progress_snapshot

        class FakeRuntime:
            def __init__(self):
                self.refresh_calls = 0
                self.pause_calls = []
                self.stop_calls = 0

            def refresh(self, *args, **kwargs):
                del args, kwargs
                self.refresh_calls += 1
                return True

            def pause(self, reason=""):
                self.pause_calls.append(reason)

            def stop(self):
                self.stop_calls += 1

        task = PlannedTask(
            id="inspect",
            title="Inspect",
            instruction="Inspect files.",
            skill_id="project_explorer",
            model="model",
            mcp=["project_filesystem_readonly"],
        )
        plan = PlannerResult(route_type="single_agent", reason="test", refined_request="check files", tasks=[task])
        state = PipelineRunState.create("check files", plan)
        runtime = FakeRuntime()
        state._rich_live_runtime = runtime
        state.record_task_started(task)
        state.output_controller.enter_completed("done")

        buffer = io.StringIO()
        with patch("runtime.execution.progress.normalize_dynamic_ui_mode", return_value="on"), contextlib.redirect_stdout(buffer):
            _print_progress_snapshot(state, mode="full", attempt=1, active="done")

        self.assertEqual(buffer.getvalue(), "")
        self.assertEqual(runtime.refresh_calls, 0)
        self.assertEqual(runtime.pause_calls, [])
        self.assertEqual(runtime.stop_calls, 1)

    def test_single_agent_execution_refreshes_and_stops_rich_live_runtime(self):
        import asyncio
        import contextlib
        import io
        from pathlib import Path
        from unittest.mock import patch

        from planning.planner_schema import PlannedTask, PlannerResult
        from runtime.execution.pipeline import PipelineRunState
        from runtime.execution.single_agent_runner import _run_single_agent

        class FakeRuntime:
            def __init__(self):
                self.refresh_calls = []
                self.pause_calls = []
                self.stop_calls = 0

            def refresh(self, run_state, *, mode, attempt, active=""):
                del run_state
                self.refresh_calls.append((mode, attempt, active))
                return True

            def pause(self, reason=""):
                self.pause_calls.append(reason)

            def stop(self):
                self.stop_calls += 1

        class FakeFactory:
            async def create_task_agent(self, task, execution_mode=""):
                return f"agent:{task.id}:{execution_mode}"

            def create_direct_answer_agent(self, model, instruction):
                return f"direct:{model}:{instruction}"

        class FakeFlywheel:
            def record_pipeline_state(self, state):
                pass

        class FakeResult:
            final_output = "done"

        async def fake_run_agent(agent, prompt, hooks, **kwargs):
            del agent, prompt, hooks, kwargs
            await asyncio.sleep(0)
            return FakeResult()

        task = PlannedTask(
            id="inspect",
            title="Inspect Runtime",
            instruction="Inspect runtime behavior.",
            skill_id="project_explorer",
            model="model",
            mcp=["project_filesystem_readonly"],
        )
        plan = PlannerResult(route_type="single_agent", reason="test", refined_request="request", tasks=[task])
        state = PipelineRunState.create("request", plan, project_root=Path.cwd(), mode="full")
        runtime = FakeRuntime()
        state._rich_live_runtime = runtime

        with patch("runtime.execution.progress.normalize_dynamic_ui_mode", return_value="on"), patch(
            "runtime.execution.single_agent_runner._readonly_fast_path_result", return_value=None
        ), patch(
            "runtime.execution.single_agent_runner._latest_workspace_context", return_value=""
        ), patch(
            "runtime.execution.single_agent_runner._inline_project_file_context", return_value=""
        ), patch(
            "runtime.execution.single_agent_runner.dynamic_status", _null_status
        ), contextlib.redirect_stdout(io.StringIO()):
            asyncio.run(
                _run_single_agent(
                    "request",
                    plan,
                    Path.cwd(),
                    FakeFactory(),
                    hooks=None,
                    run_agent=fake_run_agent,
                    run_state=state,
                    flywheel=FakeFlywheel(),
                    execution_mode="full",
                    show_plan=True,
                    attempt=1,
                )
            )

        self.assertEqual(runtime.refresh_calls, [("full", 1, "Inspect Runtime")])
        self.assertEqual(runtime.pause_calls, [])
        self.assertEqual(runtime.stop_calls, 1)

    def test_runtime_approval_uses_temporary_approval_phase_and_restores_running(self):
        import asyncio
        import contextlib
        import io

        from lucode.shell.input_adapter import RuntimeCommandSession
        from runtime.ui.output_controller import OutputController, OutputPhase

        class FakeConsole:
            interactive = True

            def __init__(self):
                self.approval_seen = None

            def runtime_control_input_enabled(self):
                return False

            async def read_choice_line(self, prompt, choices, **kwargs):
                del prompt, choices, kwargs
                self.approval_seen = controller.snapshot().phase
                return "y"

        controller = OutputController(mode="full")
        controller.enter_running(task_id="edit", reason="worker running")
        console = FakeConsole()
        session = RuntimeCommandSession(console, output_controller=controller)

        async def approval_turn():
            answer = await session.request_approval("approve?")
            return f"answer={answer}"

        with contextlib.redirect_stdout(io.StringIO()):
            result = asyncio.run(session.run(approval_turn()))

        self.assertEqual(result.final_output, "answer=y")
        self.assertEqual(console.approval_seen, OutputPhase.APPROVAL_WAITING)
        snapshot = controller.snapshot()
        self.assertEqual(snapshot.phase, OutputPhase.RUNNING)
        self.assertEqual(snapshot.active_task_id, "edit")

    def test_runtime_stop_during_approval_marks_output_controller_failed(self):
        import asyncio
        import contextlib
        import io

        from lucode.shell.input_adapter import RuntimeCommandSession
        from runtime.ui.output_controller import OutputController, OutputPhase

        class FakeConsole:
            interactive = True

            def __init__(self):
                self.approval_seen = None

            def runtime_control_input_enabled(self):
                return False

            async def read_choice_line(self, prompt, choices, **kwargs):
                del prompt, choices, kwargs
                self.approval_seen = controller.snapshot().phase
                return "/stop"

        controller = OutputController(mode="full")
        controller.enter_running(task_id="edit", reason="worker running")
        console = FakeConsole()
        session = RuntimeCommandSession(console, output_controller=controller)

        async def approval_turn():
            await session.request_approval("approve?")
            return "approved"

        with contextlib.redirect_stdout(io.StringIO()):
            result = asyncio.run(session.run(approval_turn()))

        self.assertTrue(result.stopped)
        self.assertEqual(console.approval_seen, OutputPhase.APPROVAL_WAITING)
        snapshot = controller.snapshot()
        self.assertEqual(snapshot.phase, OutputPhase.FAILED)
        self.assertEqual(snapshot.reason, "stopped")

    def test_runtime_approval_restores_control_reader_after_choice(self):
        import asyncio
        import contextlib
        import io

        from lucode.shell.input_adapter import RuntimeCommandSession
        from runtime.ui.output_controller import OutputController, OutputPhase

        class FakeConsole:
            interactive = True

            def __init__(self):
                self.control_calls = 0
                self.first_control_cancelled = False
                self.post_approval_control_phase = None
                self.approval_seen = None
                self.deferred = []

            def runtime_control_input_enabled(self):
                return True

            async def read_runtime_control_line(self):
                self.control_calls += 1
                if self.control_calls == 1:
                    try:
                        await asyncio.sleep(10)
                    except asyncio.CancelledError:
                        self.first_control_cancelled = True
                        raise
                if self.control_calls == 2:
                    self.post_approval_control_phase = controller.snapshot().phase
                    return "next turn"
                await asyncio.sleep(10)
                return ""

            async def read_choice_line(self, prompt, choices, **kwargs):
                del prompt, choices, kwargs
                self.approval_seen = controller.snapshot().phase
                return "y"

            def defer(self, line):
                self.deferred.append(line)

        controller = OutputController(mode="full")
        controller.enter_running(task_id="edit", reason="worker running")
        console = FakeConsole()
        session = RuntimeCommandSession(console, output_controller=controller)

        async def approval_turn():
            answer = await session.request_approval("approve?")
            await asyncio.sleep(0.05)
            return f"answer={answer}"

        with contextlib.redirect_stdout(io.StringIO()):
            result = asyncio.run(session.run(approval_turn()))

        self.assertEqual(result.final_output, "answer=y")
        self.assertTrue(console.first_control_cancelled)
        self.assertEqual(console.approval_seen, OutputPhase.APPROVAL_WAITING)
        self.assertEqual(console.post_approval_control_phase, OutputPhase.RUNNING)
        self.assertEqual(console.deferred, ["next turn"])
        self.assertEqual(controller.snapshot().phase, OutputPhase.RUNNING)

    def test_runtime_stop_marks_output_controller_failed(self):
        import asyncio
        import contextlib
        import io

        from lucode.shell.input_adapter import RuntimeCommandSession
        from runtime.ui.output_controller import OutputController, OutputPhase

        class FakeConsole:
            interactive = True

            async def read_runtime_line(self):
                return "/stop"

            def defer(self, line):
                raise AssertionError(f"unexpected deferred line: {line}")

        controller = OutputController(mode="full")
        controller.enter_running(task_id="inspect", reason="worker running")
        session = RuntimeCommandSession(FakeConsole(), output_controller=controller)

        async def long_turn():
            await asyncio.sleep(30)
            return "done"

        with contextlib.redirect_stdout(io.StringIO()):
            result = asyncio.run(session.run(long_turn()))

        self.assertTrue(result.stopped)
        snapshot = controller.snapshot()
        self.assertEqual(snapshot.phase, OutputPhase.FAILED)
        self.assertEqual(snapshot.reason, "stopped")

    def test_stdin_console_main_read_enters_interactive_phase_and_restores(self):
        import asyncio
        import contextlib
        import io

        from lucode.shell.input_adapter import StdinConsoleAdapter
        from runtime.ui.output_controller import OutputController, OutputPhase

        class FakeQueue:
            async def get(self):
                self.seen_phase = controller.snapshot().phase
                return "hello"

        controller = OutputController(mode="full")
        controller.enter_running(task_id="inspect", reason="running")
        queue = FakeQueue()
        console = StdinConsoleAdapter(enable_prompt_toolkit=False, output_controller=controller)
        console._queue = queue
        console._ensure_reader = lambda: None

        with contextlib.redirect_stdout(io.StringIO()):
            result = asyncio.run(console.read_line())

        self.assertEqual(result, "hello")
        self.assertEqual(queue.seen_phase, OutputPhase.INTERACTIVE_INPUT)
        self.assertEqual(controller.snapshot().phase, OutputPhase.RUNNING)

    def test_stdin_console_choice_enters_interactive_phase_and_restores(self):
        import asyncio
        import contextlib
        import io

        from lucode.shell.input_adapter import ConsoleChoice, StdinConsoleAdapter
        from runtime.ui.output_controller import OutputController, OutputPhase

        class FakeQueue:
            async def get(self):
                self.seen_phase = controller.snapshot().phase
                return "selected"

        controller = OutputController(mode="full")
        controller.enter_running(task_id="inspect", reason="running")
        queue = FakeQueue()
        console = StdinConsoleAdapter(enable_prompt_toolkit=False, output_controller=controller)
        console._queue = queue
        console._ensure_reader = lambda: None

        with contextlib.redirect_stdout(io.StringIO()):
            result = asyncio.run(
                console.read_choice_line(
                    "choose> ",
                    [ConsoleChoice("selected", "Selected")],
                )
            )

        self.assertEqual(result, "selected")
        self.assertEqual(queue.seen_phase, OutputPhase.INTERACTIVE_INPUT)
        self.assertEqual(controller.snapshot().phase, OutputPhase.RUNNING)

    def test_stdin_console_choice_does_not_override_approval_phase(self):
        import asyncio
        import contextlib
        import io

        from lucode.shell.input_adapter import ConsoleChoice, StdinConsoleAdapter
        from runtime.ui.output_controller import OutputController, OutputPhase

        class FakeQueue:
            async def get(self):
                self.seen_phase = controller.snapshot().phase
                return "y"

        controller = OutputController(mode="full")
        controller.enter_running(task_id="edit", reason="running")
        token = controller.push_phase(OutputPhase.APPROVAL_WAITING, reason="approval")
        queue = FakeQueue()
        console = StdinConsoleAdapter(enable_prompt_toolkit=False, output_controller=controller)
        console._queue = queue
        console._ensure_reader = lambda: None

        with contextlib.redirect_stdout(io.StringIO()):
            result = asyncio.run(
                console.read_choice_line(
                    "approval> ",
                    [ConsoleChoice("y", "yes")],
                )
            )

        controller.restore(token)
        self.assertEqual(result, "y")
        self.assertEqual(queue.seen_phase, OutputPhase.APPROVAL_WAITING)
        self.assertEqual(controller.snapshot().phase, OutputPhase.RUNNING)

    def test_stdin_console_secret_enters_interactive_phase_and_restores(self):
        import asyncio
        import contextlib
        import io

        from lucode.shell.input_adapter import StdinConsoleAdapter
        from runtime.ui.output_controller import OutputController, OutputPhase

        class FakeQueue:
            async def get(self):
                self.seen_phase = controller.snapshot().phase
                return "secret-value"

        controller = OutputController(mode="full")
        controller.enter_running(task_id="inspect", reason="running")
        queue = FakeQueue()
        console = StdinConsoleAdapter(enable_prompt_toolkit=False, output_controller=controller)
        console._queue = queue
        console._ensure_reader = lambda: None

        with contextlib.redirect_stdout(io.StringIO()):
            result = asyncio.run(console.read_secret_line("secret> "))

        self.assertEqual(result, "secret-value")
        self.assertEqual(queue.seen_phase, OutputPhase.INTERACTIVE_INPUT)
        self.assertEqual(controller.snapshot().phase, OutputPhase.RUNNING)

    def test_dynamic_single_agent_attempt_marks_output_completed(self):
        import asyncio
        import contextlib
        import io
        from pathlib import Path
        from unittest.mock import patch

        from planning.planner_schema import PlannedTask, PlannerResult, RefinedRequest
        from runtime.config.settings import RuntimeSettings
        from runtime.execution import dynamic as dynamic_module
        from runtime.execution.dynamic import _execute_dynamic_attempt
        from runtime.safety.privacy import PrivacyPolicy
        from runtime.ui.output_controller import OutputPhase

        class FakeRegistry:
            def first_configured(self, priority):
                return list(priority or ["planner_model"])[0]

            def get_model(self, model_id):
                return f"model:{model_id}"

            def get_model_info(self, model_id):
                return {"id": model_id, "configured": True, "supports_tools": True}

        class FakeFlywheel:
            def record_pipeline_state(self, state):
                pass

        refined = RefinedRequest(raw_user_input="check files", refined_request="check files")
        plan = PlannerResult(
            route_type="single_agent",
            reason="test",
            refined_request="check files",
            tasks=[
                PlannedTask(
                    id="inspect",
                    title="Inspect",
                    instruction="Inspect files.",
                    skill_id="project_explorer",
                    model="planner_model",
                    mcp=["project_filesystem_readonly"],
                )
            ],
        )
        seen = {}

        async def fake_preview_plan(*args, **kwargs):
            return refined, plan

        async def fake_single_agent(*args, **kwargs):
            seen["state"] = args[6]
            return "done", object()

        with patch.object(dynamic_module, "preview_plan", fake_preview_plan), patch(
            "planning.plan_validator.load_skill_catalog",
            return_value={"skills": [{"id": "project_explorer", "assignable": True, "allowed_mcp": ["project_filesystem_readonly"]}]},
        ), patch(
            "planning.plan_validator.load_mcp_catalog",
            return_value={"mcp_servers": [{"id": "project_filesystem_readonly", "implemented": True, "allowed_for_skills": ["project_explorer"]}]},
        ), patch(
            "planning.plan_validator.load_model_catalog",
            return_value={"models": [{"id": "planner_model", "configured": True, "supports_tools": True}]},
        ), patch.object(dynamic_module, "_run_single_agent", fake_single_agent), contextlib.redirect_stdout(io.StringIO()):
            output, audit = asyncio.run(
                _execute_dynamic_attempt(
                    "check files",
                    Path.cwd(),
                    FakeRegistry(),
                    mcp_manager=object(),
                    hooks=object(),
                    run_agent=object(),
                    show_plan=False,
                    settings=RuntimeSettings(
                        execution_mode="full",
                        orchestrator_model_priority=["planner_model"],
                        executor_model_priority=["planner_model"],
                        final_synthesizer_model_priority=["planner_model"],
                    ),
                    privacy_policy=PrivacyPolicy("cloud_allowed"),
                    flywheel=FakeFlywheel(),
                    attempt=1,
                )
            )

        self.assertIn("done", output)
        self.assertIsNotNone(audit)
        self.assertEqual(seen["state"].output_controller.snapshot().phase, OutputPhase.COMPLETED)
        self.assertEqual(seen["state"].output_controller.snapshot().mode, "full")

    def test_dynamic_clarify_attempt_marks_output_completed(self):
        import asyncio
        import contextlib
        import io
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import patch

        from planning.planner_schema import PlannerResult, RefinedRequest
        from runtime.config.settings import RuntimeSettings
        from runtime.execution import dynamic as dynamic_module
        from runtime.execution.dynamic import _execute_dynamic_attempt
        from runtime.safety.privacy import PrivacyPolicy
        from runtime.ui.output_controller import OutputPhase

        class FakeRegistry:
            def first_configured(self, priority):
                return list(priority or ["planner_model"])[0]

            def get_model(self, model_id):
                return f"model:{model_id}"

        class FakeFlywheel:
            def record_pipeline_state(self, state):
                pass

        refined = RefinedRequest(raw_user_input="ambiguous", refined_request="ambiguous")
        plan = PlannerResult(
            route_type="clarify",
            reason="needs detail",
            refined_request="ambiguous",
            clarifying_question="need detail",
        )
        seen = {}
        original_create = dynamic_module.PipelineRunState.create

        async def fake_preview_plan(*args, **kwargs):
            return refined, plan

        def fake_create(*args, **kwargs):
            state = original_create(*args, **kwargs)
            seen["state"] = state
            return state

        with patch.object(dynamic_module, "preview_plan", fake_preview_plan), patch.object(
            dynamic_module, "validate_plan", return_value=SimpleNamespace(valid=True)
        ), patch.object(dynamic_module, "review_plan", return_value=SimpleNamespace(approved=True)), patch.object(
            dynamic_module.PipelineRunState, "create", side_effect=fake_create
        ), contextlib.redirect_stdout(io.StringIO()):
            output, audit = asyncio.run(
                _execute_dynamic_attempt(
                    "ambiguous",
                    Path.cwd(),
                    FakeRegistry(),
                    mcp_manager=object(),
                    hooks=object(),
                    run_agent=object(),
                    show_plan=False,
                    settings=RuntimeSettings(
                        execution_mode="full",
                        orchestrator_model_priority=["planner_model"],
                        executor_model_priority=["planner_model"],
                        final_synthesizer_model_priority=["planner_model"],
                    ),
                    privacy_policy=PrivacyPolicy("cloud_allowed"),
                    flywheel=FakeFlywheel(),
                    attempt=1,
                )
            )

        self.assertIn("need detail", output)
        self.assertIsNone(audit)
        snapshot = seen["state"].output_controller.snapshot()
        self.assertEqual(snapshot.phase, OutputPhase.COMPLETED)
        self.assertEqual(snapshot.reason, "clarification requested")

    def test_dynamic_unknown_route_attempt_marks_output_failed(self):
        import asyncio
        import contextlib
        import io
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import patch

        from planning.planner_schema import PlannerResult, RefinedRequest
        from runtime.config.settings import RuntimeSettings
        from runtime.execution import dynamic as dynamic_module
        from runtime.execution.dynamic import _execute_dynamic_attempt
        from runtime.safety.privacy import PrivacyPolicy
        from runtime.ui.output_controller import OutputPhase

        class FakeRegistry:
            def first_configured(self, priority):
                return list(priority or ["planner_model"])[0]

            def get_model(self, model_id):
                return f"model:{model_id}"

        class FakeFlywheel:
            def record_pipeline_state(self, state):
                pass

        refined = RefinedRequest(raw_user_input="unknown", refined_request="unknown")
        plan = PlannerResult(route_type="unknown", reason="bad route", refined_request="unknown")
        seen = {}
        original_create = dynamic_module.PipelineRunState.create

        async def fake_preview_plan(*args, **kwargs):
            return refined, plan

        def fake_create(*args, **kwargs):
            state = original_create(*args, **kwargs)
            seen["state"] = state
            return state

        with patch.object(dynamic_module, "preview_plan", fake_preview_plan), patch.object(
            dynamic_module, "validate_plan", return_value=SimpleNamespace(valid=True)
        ), patch.object(dynamic_module, "review_plan", return_value=SimpleNamespace(approved=True)), patch.object(
            dynamic_module.PipelineRunState, "create", side_effect=fake_create
        ), contextlib.redirect_stdout(io.StringIO()):
            output, audit = asyncio.run(
                _execute_dynamic_attempt(
                    "unknown",
                    Path.cwd(),
                    FakeRegistry(),
                    mcp_manager=object(),
                    hooks=object(),
                    run_agent=object(),
                    show_plan=False,
                    settings=RuntimeSettings(
                        execution_mode="full",
                        orchestrator_model_priority=["planner_model"],
                        executor_model_priority=["planner_model"],
                        final_synthesizer_model_priority=["planner_model"],
                    ),
                    privacy_policy=PrivacyPolicy("cloud_allowed"),
                    flywheel=FakeFlywheel(),
                    attempt=1,
                )
            )

        self.assertIsNone(audit)
        self.assertNotEqual(str(output).strip(), "")
        snapshot = seen["state"].output_controller.snapshot()
        self.assertEqual(snapshot.phase, OutputPhase.FAILED)
        self.assertEqual(snapshot.reason, "unknown route")

    def test_dynamic_attempt_uses_shared_output_controller(self):
        import asyncio
        import contextlib
        import io
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import patch

        from planning.planner_schema import PlannerResult, RefinedRequest
        from runtime.config.settings import RuntimeSettings
        from runtime.execution import dynamic as dynamic_module
        from runtime.execution.dynamic import _execute_dynamic_attempt
        from runtime.safety.privacy import PrivacyPolicy
        from runtime.ui.output_controller import OutputController, OutputPhase

        class FakeRegistry:
            def first_configured(self, priority):
                return list(priority or ["planner_model"])[0]

            def get_model(self, model_id):
                return f"model:{model_id}"

        class FakeFlywheel:
            def record_pipeline_state(self, state):
                pass

        controller = OutputController(mode="full")
        refined = RefinedRequest(raw_user_input="ambiguous", refined_request="ambiguous")
        plan = PlannerResult(
            route_type="clarify",
            reason="needs detail",
            refined_request="ambiguous",
            clarifying_question="need detail",
        )
        seen = {}
        original_create = dynamic_module.PipelineRunState.create

        async def fake_preview_plan(*args, **kwargs):
            return refined, plan

        def fake_create(*args, **kwargs):
            state = original_create(*args, **kwargs)
            seen["state"] = state
            return state

        with patch.object(dynamic_module, "preview_plan", fake_preview_plan), patch.object(
            dynamic_module, "validate_plan", return_value=SimpleNamespace(valid=True)
        ), patch.object(dynamic_module, "review_plan", return_value=SimpleNamespace(approved=True)), patch.object(
            dynamic_module.PipelineRunState, "create", side_effect=fake_create
        ), contextlib.redirect_stdout(io.StringIO()):
            asyncio.run(
                _execute_dynamic_attempt(
                    "ambiguous",
                    Path.cwd(),
                    FakeRegistry(),
                    mcp_manager=object(),
                    hooks=object(),
                    run_agent=object(),
                    show_plan=False,
                    settings=RuntimeSettings(
                        execution_mode="full",
                        orchestrator_model_priority=["planner_model"],
                        executor_model_priority=["planner_model"],
                        final_synthesizer_model_priority=["planner_model"],
                    ),
                    privacy_policy=PrivacyPolicy("cloud_allowed"),
                    flywheel=FakeFlywheel(),
                    attempt=1,
                    output_controller=controller,
                )
            )

        self.assertIs(seen["state"].output_controller, controller)
        self.assertEqual(controller.snapshot().phase, OutputPhase.COMPLETED)

    def test_chat_loop_passes_session_output_controller_to_kernel(self):
        import asyncio
        import contextlib
        import io
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import patch

        import lucode.shell.chat_loop as chat_loop_module
        from runtime.config.settings import RuntimeSettings
        from runtime.ui.output_controller import OutputController

        seen = {}

        class FakeResponse:
            final_output = "done"
            stopped = False
            mcp_ids_used = []
            run_context_summary = ""
            output_already_printed = False

            def print_summary(self):
                pass

        class FakeKernelFacade:
            def __init__(self, context):
                self.context = context

            async def run_once(self, *args, **kwargs):
                del args
                seen["output_controller"] = kwargs.get("output_controller")
                return FakeResponse()

        class FakeConsole:
            interactive = True

            def __init__(self):
                self.output_controller = OutputController(mode="full")
                self.lines = iter(["do work", "/exit"])

            async def read_line(self, prompt="\n你："):
                del prompt
                try:
                    return next(self.lines)
                except StopIteration:
                    raise EOFError

            def runtime_control_input_enabled(self):
                return False

        console = FakeConsole()
        context = SimpleNamespace(workspace_root=Path.cwd())
        with patch.object(chat_loop_module, "KernelFacade", FakeKernelFacade), contextlib.redirect_stdout(io.StringIO()):
            asyncio.run(
                chat_loop_module.chat_loop(
                    model_registry=object(),
                    quarantine_dir=Path.cwd() / ".agent_quarantine",
                    runtime_settings=RuntimeSettings(execution_mode="full", privacy_mode="cloud_allowed"),
                    console=console,
                    app_home=Path.cwd(),
                    project_root=Path.cwd(),
                    workspace_context=context,
                    use_color=False,
                )
            )

        self.assertIs(seen["output_controller"], console.output_controller)

    def test_kernel_turn_timeout_marks_output_controller_failed(self):
        import asyncio
        import os
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import patch

        import runtime.kernel as kernel_module
        from runtime.ui.output_controller import OutputController, OutputPhase

        class FakeMCPServerManager:
            started_ids = []

            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, traceback):
                return False

        class FakeStrategy:
            mode_name = "full"

            async def execute(self, context):
                context.output_controller.enter_running(reason="slow")
                await asyncio.sleep(1)
                return "late"

        class FakeHooks:
            def print_summary(self):
                pass

        old_timeout = os.environ.get("AGENTS_TURN_TIMEOUT_SECONDS")
        controller = OutputController(mode="full")
        try:
            os.environ["AGENTS_TURN_TIMEOUT_SECONDS"] = "0.01"
            with patch.object(kernel_module, "MCPServerManager", FakeMCPServerManager), patch.object(
                kernel_module, "create_execution_strategy", return_value=FakeStrategy()
            ):
                asyncio.run(
                    kernel_module.KernelFacade(SimpleNamespace(workspace_root=Path.cwd())).run_once(
                        "slow task",
                        model_registry=object(),
                        hooks=FakeHooks(),
                        settings=SimpleNamespace(execution_mode="full"),
                        output_controller=controller,
                    )
                )
        finally:
            if old_timeout is None:
                os.environ.pop("AGENTS_TURN_TIMEOUT_SECONDS", None)
            else:
                os.environ["AGENTS_TURN_TIMEOUT_SECONDS"] = old_timeout

        self.assertEqual(controller.snapshot().phase, OutputPhase.FAILED)
        self.assertIn("timeout", controller.snapshot().reason)


if __name__ == "__main__":
    unittest.main(verbosity=2)
