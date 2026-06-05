from __future__ import annotations

from pathlib import Path
import unittest

from planning.planner_schema import PlannedTask


class ReadonlyDirectoryFastPathTests(unittest.TestCase):
    def test_directory_summary_fast_path_lists_real_files(self):
        from runtime.execution.fast_paths import (
            _can_fast_path_directory_summary,
            _run_directory_summary_fast_path,
        )

        project_root = Path(__file__).resolve().parents[1]
        task = PlannedTask(
            id="inspect_dirs",
            title="检查 runtime/ui 和 tests 目录",
            instruction="输出结构摘要和内容摘要，不要修改文件。",
            skill_id="project_explorer",
            model="executor",
            mcp=["project_filesystem_readonly", "code_locator"],
            read_set=["runtime/ui", "tests"],
        )

        self.assertTrue(_can_fast_path_directory_summary(project_root, task))
        output = _run_directory_summary_fast_path(project_root, task)

        self.assertIn("目录结构摘要（只读 fast path）", output)
        self.assertIn("runtime/ui", output)
        self.assertIn("tests", output)
        self.assertIn("runtime/ui/final_answer_renderer.py", output)
        self.assertIn("tests/test_supervisor_contracts.py", output)


if __name__ == "__main__":
    unittest.main()
