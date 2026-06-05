from __future__ import annotations

import unittest
from pathlib import Path

from catalog_system.refresher import build_skill_catalog
from planning.planner_schema import PlannedTask


class ModeSkillContractTests(unittest.TestCase):
    def _task(self) -> PlannedTask:
        return PlannedTask(
            id="inspect_project",
            title="检查项目",
            instruction="检查当前项目结构。",
            skill_id="project_explorer",
            model="executor",
            mcp=["code_locator", "project_filesystem_readonly"],
        )

    def test_serial_task_prompt_includes_serial_contract_without_full_worker_contract(self):
        from runtime.agents.factory import AgentFactory

        factory = AgentFactory(model_registry=object(), mcp_manager=object())

        instructions = factory._task_instructions(self._task(), execution_mode="serial")

        self.assertIn("serial 模式顺序执行 Agent 角色契约", instructions)
        self.assertIn("你是 serial 模式中的顺序执行 Agent", instructions)
        self.assertIn("不要声称正在并行执行", instructions)
        self.assertNotIn("full 团队模式 Worker 角色契约", instructions)
        self.assertNotIn("full 主管模式下，请在最终回答末尾保留", instructions)
        self.assertIn("帮助开发者快速了解新项目的技能", instructions)

    def test_full_task_prompt_keeps_full_worker_contract_not_serial_contract(self):
        from runtime.agents.factory import AgentFactory

        factory = AgentFactory(model_registry=object(), mcp_manager=object())

        instructions = factory._task_instructions(self._task(), execution_mode="full")

        self.assertIn("full 团队模式 Worker 角色契约", instructions)
        self.assertNotIn("serial 模式顺序执行 Agent 角色契约", instructions)

    def test_serial_executor_contract_is_internal_not_assignable(self):
        catalog = build_skill_catalog(project_root=Path.cwd(), use_cache=False)
        skills = {item["id"]: item for item in catalog.get("skills", [])}

        item = skills.get("serial_executor_contract")

        self.assertIsNotNone(item)
        self.assertTrue(item.get("internal"))
        self.assertFalse(item.get("borrowable"))
        self.assertFalse(item.get("assignable"))
        self.assertFalse(item.get("selectable"))
        self.assertFalse(item.get("planner_visible"))

    def test_direct_answer_agent_receives_serial_mode_context(self):
        from runtime.agents.factory import AgentFactory

        class Registry:
            def get_model(self, model_id):
                return model_id

        factory = AgentFactory(model_registry=Registry(), mcp_manager=object())

        agent = factory.create_direct_answer_agent("model", "说明当前能力。", execution_mode="serial")

        self.assertIn("当前模式：serial", agent.instructions)
        self.assertIn("不要声称创建了 Supervisor、Worker、Lead Reviewer 或并行团队", agent.instructions)
        self.assertNotIn("full 团队模式 Worker 角色契约", agent.instructions)

    def test_direct_answer_agent_receives_full_direct_context(self):
        from runtime.agents.factory import AgentFactory

        class Registry:
            def get_model(self, model_id):
                return model_id

        factory = AgentFactory(model_registry=Registry(), mcp_manager=object())

        agent = factory.create_direct_answer_agent("model", "说明当前能力。", execution_mode="full")

        self.assertIn("当前模式：full", agent.instructions)
        self.assertIn("当前问题已被判定为直接回答", agent.instructions)
        self.assertNotIn("serial 模式顺序执行 Agent 角色契约", agent.instructions)


if __name__ == "__main__":
    unittest.main()
