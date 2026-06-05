from __future__ import annotations

import unittest
from io import StringIO


class MarkdownBlockTests(unittest.TestCase):
    def test_split_markdown_blocks_keeps_headings_lists_and_code_blocks(self):
        from runtime.ui.markdown_blocks import split_markdown_blocks

        text = "\n".join(
            [
                "## 检查结果",
                "",
                "- runtime/ui 已检查",
                "- tests 已检查",
                "",
                "```python",
                "print('ok')",
                "```",
                "",
                "最终结论：没有修改文件。",
            ]
        )

        blocks = split_markdown_blocks(text)

        self.assertEqual([block.kind for block in blocks], ["heading", "list", "code", "paragraph"])
        self.assertEqual(blocks[0].text, "## 检查结果")
        self.assertEqual(blocks[2].language, "python")
        self.assertIn("print('ok')", blocks[2].text)

    def test_split_markdown_blocks_keeps_plain_chinese_as_paragraph(self):
        from runtime.ui.markdown_blocks import split_markdown_blocks

        blocks = split_markdown_blocks("这是一个普通最终回答，不应该被破坏。")

        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].kind, "paragraph")
        self.assertEqual(blocks[0].text, "这是一个普通最终回答，不应该被破坏。")


class FinalAnswerRendererTests(unittest.TestCase):
    def test_plain_final_answer_never_collapses_long_output(self):
        from runtime.ui.final_answer_renderer import render_final_answer_text

        output = "\n".join(f"final answer line {index}" for index in range(80))

        rendered = render_final_answer_text(output)

        self.assertIn("最终回答", rendered)
        self.assertIn("final answer line 79", rendered)
        self.assertNotIn("/expand", rendered)

    def test_print_final_answer_uses_injected_console_for_rich_markdown(self):
        from runtime.ui.final_answer_renderer import print_final_answer

        buffer = StringIO()
        output = "## 检查结果\n\n- runtime/ui 已检查\n\n```python\nprint('ok')\n```"

        print_final_answer(output, use_rich=True, file=buffer, force_terminal=False)

        rendered = buffer.getvalue()
        self.assertIn("最终回答", rendered)
        self.assertIn("检查结果", rendered)
        self.assertIn("runtime/ui 已检查", rendered)
        self.assertIn("print('ok')", rendered)
        self.assertNotIn("/expand", rendered)

    def test_chat_loop_uses_final_answer_renderer_instead_of_raw_print(self):
        import asyncio
        import contextlib
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import patch

        import lucode.shell.chat_loop as chat_loop_module
        from runtime.config.settings import RuntimeSettings

        calls = []

        class FakeConsole:
            def __init__(self):
                self.values = iter(["请输出 markdown 最终答案", "/exit"])

            async def read_line(self):
                return next(self.values)

        class FakeKernelResponse:
            final_output = "## 检查结果\n\n- final answer visible"
            mcp_ids_used = []
            run_context_summary = ""

        class FakeKernelFacade:
            def __init__(self, workspace_context):
                self.workspace_context = workspace_context

            async def run_once(self, *args, **kwargs):
                return FakeKernelResponse()

        def fake_print_final_answer(output, **kwargs):
            calls.append((output, kwargs))

        context = SimpleNamespace(workspace_root=Path.cwd())
        with patch.object(chat_loop_module, "KernelFacade", FakeKernelFacade), patch.object(
            chat_loop_module,
            "print_final_answer",
            fake_print_final_answer,
        ), contextlib.redirect_stdout(StringIO()):
            asyncio.run(
                chat_loop_module.chat_loop(
                    model_registry=object(),
                    quarantine_dir=Path.cwd() / ".agent_quarantine",
                    runtime_settings=RuntimeSettings(execution_mode="solo", privacy_mode="cloud_allowed"),
                    console=FakeConsole(),
                    app_home=Path.cwd(),
                    project_root=Path.cwd(),
                    workspace_context=context,
                )
            )

        self.assertEqual(len(calls), 1)
        self.assertIn("final answer visible", calls[0][0])
        self.assertIn("use_rich", calls[0][1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
