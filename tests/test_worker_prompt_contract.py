from __future__ import annotations

import unittest
from pathlib import Path

from catalog_system.refresher import build_skill_catalog
from planning.planner_schema import PlannedTask


class WorkerPromptContractTests(unittest.TestCase):
    def _project_explorer_task(self) -> PlannedTask:
        return PlannedTask(
            id="inspect_dirs",
            title="检查目录",
            instruction="检查 runtime/ui 和 tests。",
            skill_id="project_explorer",
            model="executor",
            mcp=["code_locator", "project_filesystem_readonly"],
        )

    def test_full_readonly_worker_prompt_does_not_request_visible_read_plan(self):
        from runtime.agents.factory import AgentFactory

        task = self._project_explorer_task()
        factory = AgentFactory(model_registry=object(), mcp_manager=object())

        instructions = factory._task_instructions(task, execution_mode="full")

        self.assertNotIn("先写清读取计划", instructions)
        self.assertIn("用户可见最终输出只写已经拿到的事实", instructions)
        self.assertIn("不要只写“我会先读取/正在获取/接下来分析”", instructions)

    def test_full_worker_prompt_includes_role_contract_and_domain_skill(self):
        from runtime.agents.factory import AgentFactory

        task = self._project_explorer_task()
        factory = AgentFactory(model_registry=object(), mcp_manager=object())

        instructions = factory._task_instructions(task, execution_mode="full")

        self.assertIn("full 团队模式 Worker 角色契约", instructions)
        self.assertIn("你是 full 团队模式中的 Worker", instructions)
        self.assertIn("不要创建、指挥或模拟其他 Agent", instructions)
        self.assertIn("WorkerReport", instructions)
        self.assertIn("帮助开发者快速了解新项目的技能", instructions)

    def test_serial_worker_prompt_does_not_include_full_role_contract(self):
        from runtime.agents.factory import AgentFactory

        task = self._project_explorer_task()
        factory = AgentFactory(model_registry=object(), mcp_manager=object())

        instructions = factory._task_instructions(task, execution_mode="serial")

        self.assertNotIn("full 团队模式 Worker 角色契约", instructions)
        self.assertNotIn("你是 full 团队模式中的 Worker", instructions)
        self.assertIn("帮助开发者快速了解新项目的技能", instructions)

    def test_full_worker_contract_is_internal_not_assignable(self):
        catalog = build_skill_catalog(project_root=Path.cwd(), use_cache=False)
        skills = {item["id"]: item for item in catalog.get("skills", [])}

        item = skills.get("full_worker_contract")

        self.assertIsNotNone(item)
        self.assertTrue(item.get("internal"))
        self.assertFalse(item.get("borrowable"))
        self.assertFalse(item.get("assignable"))
        self.assertFalse(item.get("selectable"))
        self.assertFalse(item.get("planner_visible"))


if __name__ == "__main__":
    unittest.main()
