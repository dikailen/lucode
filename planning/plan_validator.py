from dataclasses import dataclass, field

from catalog_system.loader import load_mcp_catalog, load_skill_catalog
from catalog_system.model_catalog import load_model_catalog
from planning.planner_schema import PlannerResult
from runtime.config.model_selection import model_runtime_available
from runtime.safety.privacy import NETWORK_MCP_IDS, PrivacyPolicy


@dataclass
class PlanValidation:
    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def validate_plan(plan: PlannerResult, privacy_policy: PrivacyPolicy | None = None) -> PlanValidation:
    """Validate a planner result before dynamic execution."""

    privacy_policy = privacy_policy or PrivacyPolicy.from_env()
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
        if not plan.needs_synthesis and not _uses_supervised_lead_finalization(plan):
            errors.append("multi_agent 路线必须启用汇总副脑。")
        if not plan.synthesis_instruction and not _uses_supervised_lead_finalization(plan):
            errors.append("multi_agent 路线必须提供 synthesis_instruction。")

    task_ids = [task.id for task in plan.tasks]
    duplicates = sorted({task_id for task_id in task_ids if task_ids.count(task_id) > 1})
    for task_id in duplicates:
        errors.append(f"任务 id 重复：{task_id}")

    for task in plan.tasks:
        for dep in task.depends_on:
            if dep not in task_ids:
                errors.append(f"任务 {task.id} 依赖了不存在的任务：{dep}")

        skill = skills.get(task.skill_id)
        if not skill:
            errors.append(f"未知 skill：{task.skill_id}")
            continue

        if not skill.get("assignable", skill.get("selectable", True)):
            if skill.get("internal"):
                errors.append(f"skill 是 Lucode 内核契约，不能作为员工任务执行：{task.skill_id}")
            elif skill.get("borrowable"):
                errors.append(f"skill 只能借阅，不能作为员工任务执行：{task.skill_id}")
            else:
                errors.append(f"skill 不可由主脑动态选择：{task.skill_id}")

        if task.write_intent and "workspace_edit" not in task.mcp:
            errors.append(f"任务 {task.id} 声明了 write_intent，但没有申请 workspace_edit。")

        model = models.get(task.model)
        if not model:
            errors.append(f"模型未在当前有效配置中注册：{task.model}")
            continue
        if not model.get("configured"):
            errors.append(f"模型已注册但未在当前有效配置中完整可用：{task.model}")
            continue
        if not privacy_policy.model_allowed(model):
            errors.append(privacy_policy.model_error(task.model, model))
        if not model_runtime_available(model):
            errors.append(f"模型当前不可运行：{task.model}")
        if task.mcp and model.get("supports_tools") is False:
            errors.append(f"模型不支持工具调用：{task.model}")

        allowed_mcp = set(skill.get("allowed_mcp") or [])
        for mcp_id in task.mcp:
            mcp = mcps.get(mcp_id)
            if not mcp:
                errors.append(f"未知 MCP：{mcp_id}")
                continue

            if not mcp.get("implemented"):
                errors.append(f"MCP 尚未实现：{mcp_id}")

            auth_error = _mcp_authorization_error(task.skill_id, skill, mcp_id, mcp)
            if auth_error:
                errors.append(auth_error)

            if mcp_id in NETWORK_MCP_IDS and privacy_policy.mode == "offline":
                if privacy_policy.mcp_allowed(mcp_id):
                    warnings.append(privacy_policy.mcp_warning(mcp_id))
                else:
                    errors.append(privacy_policy.mcp_warning(mcp_id))

    return PlanValidation(valid=not errors, errors=errors, warnings=warnings)


def _mcp_authorization_error(skill_id: str, skill: dict, mcp_id: str, mcp: dict) -> str | None:
    """Return one clear MCP authorization error, or None when the task is allowed."""

    allowed_mcp = set(skill.get("allowed_mcp") or [])
    if mcp_id not in allowed_mcp:
        return f"skill {skill_id} 不允许使用 MCP {mcp_id}"

    if skill.get("source") in {"user", "workspace"}:
        return None

    allowed_for_skills = set(mcp.get("allowed_for_skills") or [])
    if skill_id not in allowed_for_skills:
        return f"MCP {mcp_id} 未授权给 skill {skill_id}"
    return None


def _uses_supervised_lead_finalization(plan: PlannerResult) -> bool:
    contract = dict((getattr(plan, "memory_interface", {}) or {}).get("execution_contract") or {})
    helper = dict(contract.get("summary_helper") or {})
    return (
        str(contract.get("supervisor_route") or "") == "team"
        and helper.get("enabled") is False
        and str(helper.get("reason") or "") == "lead_supervisor_final_answer"
    )


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
