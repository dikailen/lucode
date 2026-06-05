from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


CHINESE_FULL_REQUEST = "\u7528 full \u6a21\u5f0f\u5e76\u884c\u53ea\u8bfb\u5206\u6790 runtime/ui \u548c tests \u76ee\u5f55"
CHINESE_FULL_EXPLAIN = "\u89e3\u91ca full \u548c serial \u7684\u533a\u522b"


class TurnModeOverrideTests(unittest.TestCase):
    def test_extracts_explicit_full_mode_from_chinese_request(self):
        from runtime.config.execution_mode import explicit_execution_mode_for_input

        self.assertEqual(explicit_execution_mode_for_input(CHINESE_FULL_REQUEST), "full")

    def test_ignores_plain_mentions_without_mode_intent(self):
        from runtime.config.execution_mode import explicit_execution_mode_for_input

        self.assertEqual(explicit_execution_mode_for_input(CHINESE_FULL_EXPLAIN), "")


class PromptToolkitInputTests(unittest.TestCase):
    def test_main_input_completion_only_runs_for_command_or_reference_tokens(self):
        from runtime.commands.completion import should_complete_main_input_while_typing

        self.assertTrue(should_complete_main_input_while_typing("/mo"))
        self.assertTrue(should_complete_main_input_while_typing("@REA"))
        self.assertTrue(should_complete_main_input_while_typing("#project"))
        self.assertTrue(should_complete_main_input_while_typing("~/Doc"))
        self.assertFalse(should_complete_main_input_while_typing(CHINESE_FULL_REQUEST))

    def test_prompt_toolkit_input_disables_background_control_reader(self):
        from lucode.shell.input_adapter import StdinConsoleAdapter

        console = StdinConsoleAdapter(enable_prompt_toolkit=True)

        with patch("lucode.shell.input_adapter.should_enable_prompt_toolkit", return_value=True):
            self.assertFalse(console.runtime_control_input_enabled())

        fallback = StdinConsoleAdapter(enable_prompt_toolkit=False)
        self.assertTrue(fallback.runtime_control_input_enabled())


class ChatLoopModeOverrideTests(unittest.IsolatedAsyncioTestCase):
    async def test_chat_loop_uses_explicit_turn_mode_without_mutating_persistent_setting(self):
        from lucode.shell import chat_loop as chat_loop_module
        from runtime.config.settings import RuntimeSettings

        calls = []

        class FakeConsole:
            interactive = True

            def __init__(self):
                self.output_controller = None
                self.lines = [CHINESE_FULL_REQUEST, "/exit"]

            async def read_line(self, prompt="\n\u4f60\uff1a"):
                del prompt
                return self.lines.pop(0)

            def runtime_control_input_enabled(self):
                return False

        class FakeKernelFacade:
            def __init__(self, workspace_context):
                del workspace_context

            async def run_once(self, prompt, **kwargs):
                calls.append((prompt, kwargs))
                return SimpleNamespace(
                    final_output="ok",
                    mcp_ids_used=[],
                    run_context_summary="",
                    output_already_printed=False,
                )

        settings = RuntimeSettings(execution_mode="serial", privacy_mode="cloud_allowed")
        workspace_context = SimpleNamespace(workspace_root="D:/pycharm/code/agents_demo")
        console = FakeConsole()

        with patch.object(chat_loop_module, "KernelFacade", FakeKernelFacade), patch.object(
            chat_loop_module, "should_print_final_output", return_value=False
        ), patch.object(chat_loop_module, "render_runtime_statusline", return_value="status"), patch.object(
            chat_loop_module, "create_token_logger_hooks", return_value=SimpleNamespace(print_summary=lambda: None)
        ):
            await chat_loop_module.chat_loop(
                model_registry=object(),
                quarantine_dir=".",
                runtime_settings=settings,
                console=console,
                app_home=Path("."),
                project_root=Path("."),
                workspace_context=workspace_context,
                use_color=False,
            )

        self.assertEqual(settings.execution_mode, "serial")
        self.assertEqual(calls[0][1]["settings"].execution_mode, "full")
        self.assertEqual(calls[0][1]["routing_input"], CHINESE_FULL_REQUEST)


class ReadonlyContractEncodingTests(unittest.TestCase):
    def test_chinese_readonly_request_strips_command_runner(self):
        from planning.planner_schema import PlannedTask, PlannerResult
        from runtime.execution.execution_contract import normalize_execution_contract

        plan = PlannerResult(
            route_type="single_agent",
            reason="readonly dir summary",
            refined_request=CHINESE_FULL_REQUEST + "\uff0c\u4e0d\u8981\u4fee\u6539\u6587\u4ef6",
            tasks=[
                PlannedTask(
                    id="inspect_dirs",
                    title="\u53ea\u8bfb\u68c0\u67e5\u76ee\u5f55",
                    instruction="\u53ea\u8bfb\u68c0\u67e5 runtime/ui \u548c tests\uff0c\u4e0d\u8981\u4fee\u6539\u6587\u4ef6\uff0c\u4e0d\u8981\u8fd0\u884c\u547d\u4ee4\u3002",
                    skill_id="project_explorer",
                    model="executor",
                    mcp=["project_filesystem_readonly", "code_locator", "command_runner"],
                    read_set=["runtime/ui", "tests"],
                )
            ],
        )

        decision = normalize_execution_contract(
            plan,
            CHINESE_FULL_REQUEST + "\uff0c\u4e0d\u8981\u4fee\u6539\u6587\u4ef6\uff0c\u4e0d\u8981\u8fd0\u884c\u547d\u4ee4",
            mode="full",
        )

        self.assertTrue(decision.readonly_hard_constraint)
        self.assertNotIn("command_runner", plan.tasks[0].mcp)
        self.assertIn("project_filesystem_readonly", plan.tasks[0].mcp)
