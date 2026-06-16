from __future__ import annotations

from planning.plan_validator import validate_plan
from planning.planner import build_orchestrator_planner
from planning.planner_schema import PlannedTask, PlannerResult


def test_orchestrator_prompt_contains_hard_triage_rules():
    planner = build_orchestrator_planner(model=object())
    instructions = planner.instructions

    assert "难度分诊" in instructions
    assert "单轮问答" in instructions
    assert "单文件只读" in instructions
    assert "禁止 multi_agent" in instructions
    assert "多步骤" in instructions


def test_validator_warns_on_over_split_readonly_single_file_multi_agent():
    plan = PlannerResult(
        route_type="multi_agent",
        reason="过度拆成两个只读任务",
        refined_request="分析 README",
        tasks=[
            PlannedTask(
                id="read_a",
                title="读取 README 第一部分",
                instruction="只读 README",
                skill_id="project_explorer",
                model="missing_model",
                mcp=["project_filesystem_readonly"],
                read_set=["README.md"],
            ),
            PlannedTask(
                id="read_b",
                title="读取 README 第二部分",
                instruction="只读 README",
                skill_id="project_explorer",
                model="missing_model",
                mcp=["project_filesystem_readonly"],
                read_set=["README.md"],
            ),
        ],
        needs_synthesis=True,
        synthesis_instruction="合并两个只读结果",
    )

    validation = validate_plan(plan)

    assert any("过度拆分" in warning and "single_agent" in warning for warning in validation.warnings)
