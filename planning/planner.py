from agents import Agent, Runner

from catalog_system.loader import compact_mcp_catalog_for_prompt, compact_skill_catalog_for_prompt
from catalog_system.model_catalog import compact_model_catalog_for_prompt
from planning.plan_validator import PlanValidation, format_validation, validate_plan
from planning.planner_schema import PlannerResult, parse_planner_result, parse_refined_request
from skills.loader import load_skill


def build_query_refiner(model):
    return Agent(
        name="query_refiner_agent",
        instructions=load_skill("query_refiner"),
        model=model,
    )


def build_orchestrator_planner(model):
    skill_catalog = compact_skill_catalog_for_prompt()
    mcp_catalog = compact_mcp_catalog_for_prompt()
    model_catalog = compact_model_catalog_for_prompt()

    instructions = (
        load_skill("orchestrator_planner")
        + "\n\n## Skill 图书馆\n"
        + skill_catalog
        + "\n\n## MCP 图书馆\n"
        + mcp_catalog
        + "\n\n## 模型图书馆\n"
        + model_catalog
    )

    return Agent(
        name="orchestrator_planner_agent",
        instructions=instructions,
        model=model,
    )


async def preview_plan(
    raw_user_input: str,
    refiner_model,
    planner_model,
    hooks=None,
) -> tuple[object, PlannerResult]:
    """Run query refinement and planner preview without creating execution Agents."""

    refiner = build_query_refiner(refiner_model)
    refiner_result = await Runner.run(refiner, raw_user_input, hooks=hooks)
    refined = parse_refined_request(refiner_result.final_output, raw_user_input)

    planner = build_orchestrator_planner(planner_model)
    planner_input = (
        "请根据以下优化后的用户请求输出调度计划。\n\n"
        "运行上下文：当前程序运行在本地项目根目录中。"
        "如果用户说“当前项目”“这个项目”“this project”“本项目”，"
        "可以使用 `project_explorer` 搭配 `project_filesystem_readonly` 读取项目文件，"
        "不要因为用户未粘贴目录树就直接 clarify。\n\n"
        f"原始问题：{refined.raw_user_input}\n"
        f"优化问题：{refined.refined_request}\n"
        f"明确约束：{refined.explicit_constraints}\n"
        f"潜在歧义：{refined.possible_ambiguities}\n"
        f"可能意图：{refined.likely_intent}\n"
    )
    planner_result = await Runner.run(planner, planner_input, hooks=hooks)
    plan = parse_planner_result(planner_result.final_output)

    return refined, plan


def format_plan_preview(refined, plan: PlannerResult) -> str:
    lines = [
        "========== 规划预览 ==========",
        f"优化后的问题：{refined.refined_request}",
        f"可能意图：{refined.likely_intent}",
    ]

    if refined.explicit_constraints:
        lines.append("明确约束：" + "；".join(refined.explicit_constraints))

    if refined.possible_ambiguities:
        lines.append("潜在歧义：" + "；".join(refined.possible_ambiguities))

    lines.extend(
        [
            "",
            f"路线：{plan.route_type}",
            f"原因：{plan.reason}",
        ]
    )

    if plan.route_type == "direct_answer":
        lines.append(f"主脑直接回答指令：{plan.direct_answer_instruction}")

    if plan.route_type == "clarify":
        lines.append(f"需要追问：{plan.clarifying_question}")

    if plan.tasks:
        lines.append("")
        lines.append("计划任务：")
        for task in plan.tasks:
            lines.append(f"- {task.id}｜{task.title}")
            lines.append(f"  skill：{task.skill_id}")
            lines.append(f"  model：{task.model}")
            lines.append(f"  mcp：{', '.join(task.mcp) if task.mcp else '无'}")
            lines.append(f"  并行组：{task.parallel_group}")
            lines.append(f"  指令：{task.instruction}")
            if task.requires_unimplemented_mcp:
                lines.append("  注意：该计划申请了尚未实现的 MCP。")
            if task.risk_notes:
                lines.append(f"  风险：{task.risk_notes}")

    lines.append("")
    lines.append(format_validation(validate_plan(plan)))

    lines.append("")
    lines.append(f"是否需要汇总副脑：{'是' if plan.needs_synthesis else '否'}")
    if plan.synthesis_instruction:
        lines.append(f"汇总要求：{plan.synthesis_instruction}")

    memory = plan.memory_interface or {}
    if memory:
        lines.append("")
        lines.append("知识图谱预留接口：")
        lines.append(f"- 是否建议检索记忆：{memory.get('should_query_memory', False)}")
        lines.append(f"- 检索提示：{memory.get('query_hint', '无')}")

    lines.append("")
    lines.append("说明：这是预览模式，只展示调度计划，不会创建动态 Agent，也不会调用 MCP 执行任务。")
    return "\n".join(lines)


def format_execution_plan(refined, plan: PlannerResult, validation: PlanValidation) -> str:
    lines = [
        "========== 本轮规划 ==========",
        f"优化问题：{refined.refined_request}",
        f"路线：{plan.route_type}",
        f"原因：{plan.reason}",
        format_validation(validation),
    ]

    if plan.route_type == "direct_answer":
        lines.append("执行：主脑直接回答，不创建专家 Agent。")
    elif plan.route_type == "clarify":
        lines.append(f"执行：需要先追问：{plan.clarifying_question}")
    elif plan.tasks:
        lines.append("执行任务：")
        for task in plan.tasks:
            mcp_text = ", ".join(task.mcp) if task.mcp else "无"
            lines.append(
                f"- {task.title} | skill={task.skill_id} | model={task.model} | MCP={mcp_text} | 并行组={task.parallel_group}"
            )

    if plan.needs_synthesis:
        lines.append("汇总：多 Agent 完成后由 final_synthesizer 汇总。")
    else:
        lines.append("汇总：不需要额外汇总副脑。")

    return "\n".join(lines)
