import asyncio
import json
from collections import defaultdict
from pathlib import Path

from mcp_servers import create_readonly_filesystem_server
from mcp_servers.web_search_mcp import web_search
from catalog_system.model_catalog import ModelRegistry
from planning.plan_validator import format_validation, validate_plan
from planning.planner import format_execution_plan, preview_plan
from planning.planner_schema import PlannerResult
from runtime.agent_factory import AgentFactory
from runtime.run_workspace import RunWorkspace


async def execute_dynamic_request(
    raw_user_input: str,
    project_root: Path,
    model_registry: ModelRegistry,
    mcp_manager,
    hooks,
    run_agent,
    show_plan: bool = False,
) -> str:
    """Plan and execute a request using dynamic Agents."""

    refiner_model_id = model_registry.first_configured(
        ["deepseek_V4_flash_model", "deepseek_V4_pro_model", "mimo_model"]
    )
    planner_model_id = model_registry.first_configured(
        ["deepseek_V4_pro_model", "deepseek_V4_flash_model", "mimo_model"]
    )

    refined, plan = await preview_plan(
        raw_user_input,
        refiner_model=model_registry.get_model(refiner_model_id),
        planner_model=model_registry.get_model(planner_model_id),
        hooks=hooks,
    )
    validation = validate_plan(plan)
    if show_plan:
        print(format_execution_plan(refined, plan, validation))

    if not validation.valid:
        return (
            "主脑规划未通过校验，已停止执行。\n\n"
            f"{format_validation(validation)}\n\n"
            "你可以用 /plan 查看规划详情，或把问题说得更具体一点。"
        )

    factory = AgentFactory(model_registry, mcp_manager)

    if plan.route_type == "direct_answer":
        return await _run_direct_answer(raw_user_input, plan, planner_model_id, factory, hooks, run_agent)

    if plan.route_type == "clarify":
        return plan.clarifying_question or "这个问题还需要你补充一点信息。"

    if plan.route_type == "single_agent":
        task = plan.tasks[0]
        if _can_fast_path_url_search(task):
            return _run_url_search_fast_path(refined.refined_request, task)

        agent = await factory.create_task_agent(task)
        result = await run_agent(
            agent,
            _task_prompt(refined.refined_request, task.instruction),
            hooks,
            max_turns=_max_turns_for_task(task),
        )
        return result.final_output

    if plan.route_type == "multi_agent":
        return await _run_multi_agent(
            refined.refined_request,
            plan,
            project_root,
            planner_model_id,
            factory,
            hooks,
            run_agent,
        )

    return "主脑没有给出可执行路线。"


async def _run_direct_answer(raw_user_input, plan: PlannerResult, model_id, factory, hooks, run_agent) -> str:
    agent = factory.create_direct_answer_agent(model_id, plan.direct_answer_instruction)
    result = await run_agent(agent, raw_user_input, hooks)
    return result.final_output


async def _run_multi_agent(refined_request, plan: PlannerResult, project_root, model_id, factory, hooks, run_agent):
    workspace = RunWorkspace(project_root)
    run_dir = workspace.create()

    try:
        for group_id, tasks in _tasks_by_parallel_group(plan).items():
            if len(tasks) == 1 or not _can_run_group_in_parallel(tasks):
                if len(tasks) > 1:
                    print(f"执行并行组 {group_id}：存在审批工具或共享 MCP，改为安全顺序执行。")
                for task in tasks:
                    title, output = await _run_planned_task(refined_request, task, factory, hooks, run_agent)
                    workspace.write_task_output(task.id, title, output)
                continue

            print(f"执行并行组 {group_id}：并行启动 {len(tasks)} 个临时 Agent。")
            results = await asyncio.gather(
                *(_run_planned_task(refined_request, task, factory, hooks, run_agent) for task in tasks)
            )
            for task, (title, output) in zip(tasks, results):
                workspace.write_task_output(task.id, title, output)

        async with create_readonly_filesystem_server(
            run_dir,
            "run_workspace_readonly",
        ) as run_workspace_server:
            synthesizer = factory.create_synthesizer_agent(model_id, run_workspace_server)
            synthesis_prompt = (
                "请读取当前运行工作目录中的所有任务输出文件，按照以下要求汇总：\n"
                f"{plan.synthesis_instruction}\n\n"
                "请输出面向用户的最终中文答案。"
            )
            result = await run_agent(synthesizer, synthesis_prompt, hooks, max_turns=10)
            return result.final_output
    finally:
        workspace.cleanup()


def _task_prompt(refined_request: str, task_instruction: str) -> str:
    return (
        "优化后的用户请求：\n"
        f"{refined_request}\n\n"
        "你的具体任务：\n"
        f"{task_instruction}"
    )


def _tasks_by_parallel_group(plan: PlannerResult) -> dict[int, list]:
    groups = defaultdict(list)
    for task in plan.tasks:
        groups[task.parallel_group].append(task)
    return dict(sorted(groups.items(), key=lambda item: item[0]))


async def _run_planned_task(refined_request, task, factory, hooks, run_agent) -> tuple[str, str]:
    agent = await factory.create_task_agent(task)
    result = await run_agent(
        agent,
        _task_prompt(refined_request, task.instruction),
        hooks,
        max_turns=_max_turns_for_task(task),
    )
    return task.title, str(result.final_output)


def _can_run_group_in_parallel(tasks: list) -> bool:
    used_mcp = set()
    for task in tasks:
        if "safe_backup" in task.mcp:
            return False
        for mcp_id in task.mcp:
            if mcp_id in used_mcp:
                return False
            used_mcp.add(mcp_id)
    return True


def _max_turns_for_task(task) -> int:
    if "web_search" in task.mcp and _is_url_only_task(task):
        return 2
    if task.mcp:
        return 12
    return 6


def _is_url_only_task(task) -> bool:
    text = f"{task.title}\n{task.instruction}".lower()
    return any(
        marker in text
        for marker in [
            "url",
            "urls",
            "链接",
            "地址",
            "top urls",
            "仅返回",
            "只返回",
        ]
    )


def _can_fast_path_url_search(task) -> bool:
    return task.skill_id == "project_explorer" and task.mcp == ["web_search"] and _is_url_only_task(task)


def _run_url_search_fast_path(refined_request: str, task) -> str:
    print("执行优化：URL-only 联网任务直接调用 web_search 一次，避免模型重复搜索。")
    query = _build_url_search_query(refined_request, task)
    print("工具调用：runtime -> web_search")
    raw_result = web_search(query, max_results=5)
    print(f"工具完成：runtime <- web_search（结果约 {len(raw_result)} 字符）")

    try:
        payload = json.loads(raw_result)
    except json.JSONDecodeError:
        return raw_result

    urls = [item.get("url") for item in payload.get("results", []) if item.get("url")]
    if not urls:
        return "没有搜索到可靠 URL。"

    return "\n".join(f"- {url}" for url in urls)


def _build_url_search_query(refined_request: str, task) -> str:
    text = f"{refined_request}\n{task.title}\n{task.instruction}"
    lowered = text.lower()
    if "openai" in lowered and "mcp" in lowered and ("agents" in lowered or "sdk" in lowered):
        return "OpenAI Agents SDK MCP documentation"
    return text[:300]
