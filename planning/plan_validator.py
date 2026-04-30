from dataclasses import dataclass, field

from catalog_system.loader import load_mcp_catalog, load_skill_catalog
from catalog_system.model_catalog import load_model_catalog
from planning.planner_schema import PlannerResult


@dataclass
class PlanValidation:
    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def validate_plan(plan: PlannerResult) -> PlanValidation:
    """Validate a planner result before dynamic execution."""

    errors = []
    warnings = []

    skills = {item["id"]: item for item in load_skill_catalog().get("skills", [])}
    mcps = {item["id"]: item for item in load_mcp_catalog().get("mcp_servers", [])}
    models = {item["id"]: item for item in load_model_catalog().get("models", [])}

    if plan.route_type == "direct_answer":
        if plan.tasks:
            warnings.append("direct_answer 路线不应包含 tasks，执行时会忽略。")
        if plan.needs_synthesis:
            warnings.append("direct_answer 路线不需要汇总副脑。")

    if plan.route_type == "clarify":
        if not plan.clarifying_question:
            errors.append("clarify 路线必须提供 clarifying_question。")
        if plan.tasks:
            warnings.append("clarify 路线不应包含 tasks，执行时会忽略。")

    if plan.route_type == "single_agent":
        if len(plan.tasks) != 1:
            errors.append("single_agent 路线必须且只能包含 1 个任务。")
        if plan.needs_synthesis:
            errors.append("single_agent 路线不应启用汇总副脑。")

    if plan.route_type == "multi_agent":
        if len(plan.tasks) < 2:
            errors.append("multi_agent 路线至少需要 2 个任务。")
        if not plan.needs_synthesis:
            errors.append("multi_agent 路线必须启用汇总副脑。")
        if not plan.synthesis_instruction:
            errors.append("multi_agent 路线必须提供 synthesis_instruction。")

    for task in plan.tasks:
        skill = skills.get(task.skill_id)
        if not skill:
            errors.append(f"未知 skill：{task.skill_id}")
            continue

        if not skill.get("selectable", True):
            errors.append(f"skill 不可由主脑动态选择：{task.skill_id}")

        model = models.get(task.model)
        if not model:
            errors.append(f"未知模型：{task.model}")
        elif not model.get("configured"):
            errors.append(f"模型未在 .env 中完整配置：{task.model}")

        allowed_mcp = set(skill.get("allowed_mcp") or [])
        for mcp_id in task.mcp:
            mcp = mcps.get(mcp_id)
            if not mcp:
                errors.append(f"未知 MCP：{mcp_id}")
                continue

            if not mcp.get("implemented"):
                errors.append(f"MCP 尚未实现：{mcp_id}")

            if mcp_id not in allowed_mcp:
                errors.append(f"skill {task.skill_id} 不允许使用 MCP {mcp_id}")

            allowed_for_skills = set(mcp.get("allowed_for_skills") or [])
            if task.skill_id not in allowed_for_skills:
                errors.append(f"MCP {mcp_id} 未授权给 skill {task.skill_id}")

    return PlanValidation(valid=not errors, errors=errors, warnings=warnings)


def format_validation(validation: PlanValidation) -> str:
    if validation.valid and not validation.warnings:
        return "规划校验：通过"

    lines = ["规划校验：通过" if validation.valid else "规划校验：失败"]
    if validation.errors:
        lines.append("错误：")
        lines.extend(f"- {error}" for error in validation.errors)
    if validation.warnings:
        lines.append("警告：")
        lines.extend(f"- {warning}" for warning in validation.warnings)
    return "\n".join(lines)
