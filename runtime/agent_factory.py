from agents import Agent

from catalog_system.model_catalog import ModelRegistry
from planning.planner_schema import PlannedTask
from skills.loader import load_skill


class AgentFactory:
    """Create temporary execution Agents from planner tasks."""

    def __init__(self, model_registry: ModelRegistry, mcp_manager):
        self.model_registry = model_registry
        self.mcp_manager = mcp_manager

    async def create_task_agent(self, task: PlannedTask) -> Agent:
        model = self.model_registry.get_model(task.model)
        servers = await self.mcp_manager.get_many(task.mcp)
        instructions = (
            load_skill(task.skill_id)
            + "\n\n## 本次临时任务\n"
            + task.instruction
            + self._tool_budget(task)
            + "\n\n## 执行收束规则\n"
            + "- 只调用完成任务真正需要的工具。\n"
            + "- 拿到足够信息后必须停止调用工具，直接输出最终结果。\n"
            + "- 如果工具结果不完美，也要基于已有可靠信息给出答案，并说明限制。\n"
            + "- 如果使用 web_search，最多搜索 2 次；如果使用 web_fetch，最多读取 3 个网页。\n"
            + "\n## 输出风格\n"
            + "- 默认使用中文。\n"
            + "- 默认不要使用 emoji。\n"
            + "- 默认不要写过长的报告、夸张开场白或大段横线。\n"
            + "- 用清晰的小标题和短段落回答，重点放在用户真正问的内容。\n"
            + "\n请直接完成本次任务，输出给用户可读的最终结果。"
        )

        return Agent(
            name=f"dynamic_{task.id}",
            instructions=instructions,
            model=model,
            mcp_servers=servers,
        )

    def _tool_budget(self, task: PlannedTask) -> str:
        if "web_search" not in task.mcp:
            return ""

        text = f"{task.title}\n{task.instruction}".lower()
        url_only = any(
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
        if url_only:
            return (
                "\n\n## 本次工具预算\n"
                "- 本任务只需要 URL/链接，不需要网页正文。\n"
                "- `web_search` 最多调用 1 次。\n"
                "- 禁止调用 `web_fetch`。\n"
                "- 搜索结果即使不完美，也要基于已有结果立即输出链接列表。"
            )

        return (
            "\n\n## 本次工具预算\n"
            "- `web_search` 最多调用 2 次。\n"
            "- `web_fetch` 最多调用 3 次，只读取最关键来源。\n"
            "- 不要重复搜索同义问题。"
        )

    def create_direct_answer_agent(self, model_id: str, instruction: str) -> Agent:
        return Agent(
            name="direct_answer_agent",
            instructions=(
                "你是动态多智能体系统的主脑。当前问题不需要创建专家 Agent。"
                "请根据用户问题直接用中文回答，简洁、自然、准确。默认不要使用 emoji。"
                "介绍自己时统一自称“动态多智能体助手”或“主脑规划器”，"
                "不要自称 JPCoder AI、ChatGPT 或其它未由用户指定的品牌名。\n\n"
                f"回答要求：{instruction}"
            ),
            model=self.model_registry.get_model(model_id),
        )

    def create_synthesizer_agent(self, model_id: str, run_workspace_server) -> Agent:
        return Agent(
            name="final_synthesizer_agent",
            instructions=load_skill("final_synthesizer"),
            model=self.model_registry.get_model(model_id),
            mcp_servers=[run_workspace_server],
        )
