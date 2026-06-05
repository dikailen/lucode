from __future__ import annotations

from pathlib import Path
import unittest

from planning.planner_schema import PlannedTask, PlannerResult
from runtime.execution.pipeline import PipelineRunState
from runtime.safety.auditor import audit_execution, format_final_report


class AuditorFinalContentTests(unittest.TestCase):
    def test_rejects_process_only_readonly_tool_answer(self):
        task = PlannedTask(
            id="parallel_dir_summary",
            title="并行分析 runtime/ui 和 tests 目录",
            instruction="只读检查 runtime/ui 和 tests 目录，输出结构摘要和内容摘要。",
            skill_id="project_explorer",
            model="local_model",
            mcp=["project_filesystem_readonly", "code_locator"],
            read_set=["runtime/ui", "tests"],
            acceptance_criteria=[
                "准确描述 runtime/ui 和 tests 目录的结构、主要文件及用途",
                "输出清晰的摘要文本，未修改任何文件",
            ],
        )
        plan = PlannerResult(
            route_type="single_agent",
            reason="readonly summary",
            refined_request="用 full 模式并行检查 runtime/ui 和 tests 目录，输出工具执行摘要即可，不要修改文件",
            tasks=[task],
        )
        state = PipelineRunState.create(plan.refined_request, plan, project_root=Path("."))
        process_only = (
            "我会先列出 `runtime/ui` 和 `tests` 两个目录的完整文件树，然后根据需要补充读取未在上下文中出现的核心文件，"
            "最后输出结构摘要和内容摘要。\n\n"
            "正在并行获取两个目录的列表：\n"
            "- `runtime/ui`\n"
            "- `tests`"
        )
        state.record_task_result(task, process_only)

        audit = audit_execution(plan, state, process_only)
        report = format_final_report(process_only, audit)

        self.assertFalse(audit.passed)
        self.assertTrue(audit.needs_replan)
        self.assertTrue(any("只描述准备或正在执行" in issue for issue in audit.remaining_issues))
        self.assertIn("最终审核：未通过", report)

    def test_allows_concrete_readonly_result_even_with_past_tense_process_words(self):
        task = PlannedTask(
            id="dir_summary",
            title="分析 runtime/ui 和 tests",
            instruction="输出目录摘要。",
            skill_id="project_explorer",
            model="local_model",
            mcp=["project_filesystem_readonly", "code_locator"],
            read_set=["runtime/ui", "tests"],
        )
        plan = PlannerResult(
            route_type="single_agent",
            reason="readonly summary",
            refined_request="检查 runtime/ui 和 tests。",
            tasks=[task],
        )
        state = PipelineRunState.create(plan.refined_request, plan, project_root=Path("."))
        concrete = (
            "我已经检查完目录。runtime/ui/final_answer_renderer.py 负责最终答案渲染，"
            "runtime/ui/rich_live.py 负责 Rich Live 面板；"
            "tests/test_supervisor_contracts.py 覆盖 full 主管合约，"
            "tests/test_final_answer_renderer.py 覆盖最终答案不折叠。"
        )
        state.record_task_result(task, concrete)

        audit = audit_execution(plan, state, concrete)

        self.assertTrue(audit.passed, audit.remaining_issues)


if __name__ == "__main__":
    unittest.main()
