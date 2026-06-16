from __future__ import annotations

from runtime.agents.factory import AgentFactory
from catalog_system.loader import load_skill_catalog


class _DummyModelRegistry:
    def get_model(self, model_id):
        return f"model::{model_id}"

    def get_model_info(self, model_id):
        return {"supports_tools": True, "model_name": model_id}


def _instructions(agent) -> str:
    return str(getattr(agent, "instructions", "") or "")


def _skill(skill_id: str) -> dict | None:
    for item in load_skill_catalog().get("skills", []):
        if item.get("id") == skill_id:
            return item
    return None


def test_solo_executor_contract_registered_as_internal_non_assignable():
    item = _skill("solo_executor_contract")

    assert item is not None
    assert item.get("internal") is True
    assert item.get("assignable") is False
    assert item.get("selectable") is False
    assert item.get("planner_visible") is False


def test_solo_agent_prompt_uses_solo_contract_without_claude_branding():
    factory = AgentFactory(_DummyModelRegistry(), mcp_manager=None)

    text = _instructions(factory.create_solo_agent("deepseek"))

    assert "solo 单模型工具 Agent" in text
    assert "solo_executor_contract" in text or "solo 模式" in text
    assert "Claude CLI" not in text
    assert "不要自称 Claude" in text
    assert "不要猜测底层模型品牌" in text


def test_serial_direct_answer_warns_not_to_guess_underlying_model_brand():
    factory = AgentFactory(_DummyModelRegistry(), mcp_manager=None)

    text = _instructions(
        factory.create_direct_answer_agent("deepseek", "回答你是什么模型", execution_mode="serial")
    )

    assert "不要自称 Claude" in text
    assert "不要猜测底层模型品牌" in text
    assert "当前配置的模型" in text
    assert "不要自称 Lucode" not in text
