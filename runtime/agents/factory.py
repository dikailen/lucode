from catalog_system.model_catalog import ModelRegistry
from planning.planner_schema import PlannedTask
from runtime.common.text_utils import sanitize_text
from runtime.agents.sdk import agent_class
from skills.loader import load_skill


class AgentFactory:
    """Create temporary execution Agents from planner tasks."""

    def __init__(self, model_registry: ModelRegistry, mcp_manager):
        self.model_registry = model_registry
        self.mcp_manager = mcp_manager

    async def create_task_agent(self, task: PlannedTask):
        model_info = self.model_registry.get_model_info(task.model)
        if task.mcp and not model_info.get("supports_tools", True):
            raise ValueError(
                "当前任务需要 MCP 工具，但所选模型不支持 tools/function calling："
                f"{task.model}（{model_info.get('model_name') or '未知模型名'}）。"
                "请换用支持工具调用的本地模型，或在隐私模式允许时配置可用云端模型。"
            )
        model = self.model_registry.get_model(task.model)
        servers = await self.mcp_manager.get_many(task.mcp)
        instructions = self._task_instructions(task)

        Agent = agent_class()
        return Agent(
            name=f"dynamic_{task.id}",
            instructions=instructions,
            model=model,
            mcp_servers=servers,
        )

    def _task_instructions(self, task: PlannedTask) -> str:
        return sanitize_text(
            load_skill(task.skill_id)
            + "\n\n## 本次临时任务\n"
            + task.instruction
            + self._execution_contract(task)
            + self._tool_budget(task)
            + self._tool_rules(task)
            + "\n## 输出风格\n"
            + "- 默认使用中文。\n"
            + "- 默认不要使用 emoji。\n"
            + "- 默认不要写过长的报告、夸张开场白或大段横线。\n"
            + "- 用清晰的小标题和短段落回答，重点放在用户真正问的内容。\n"
            + "\n请直接完成本次任务，输出给用户可读的最终结果。"
        )

    def _execution_contract(self, task: PlannedTask) -> str:
        lines = []
        if task.depends_on:
            lines.append("- 依赖任务：" + "；".join(task.depends_on))
        if task.acceptance_criteria:
            lines.append("- 验收标准：" + "；".join(task.acceptance_criteria))
        if task.expected_outputs:
            lines.append("- 预期产出：" + "；".join(task.expected_outputs))
        if task.read_set:
            lines.append("- 预计读取范围：" + "；".join(task.read_set))
        if task.write_intent:
            lines.append("- 预计写入意图：" + "；".join(task.write_intent))
        if "workspace_edit" in task.mcp:
            if task.write_intent:
                lines.append(
                    "- 编辑模式：strict。写入、替换、patch 或删除已有文件前，必须先通过只读文件工具读取目标文件，"
                    "取得当前 sha256，并把该值作为 expected_sha256 或 expected_sha256_by_path 传给 workspace_edit；"
                    "如果没有拿到当前 sha256，不要裸写，先说明无法安全修改。"
                )
            else:
                lines.append(
                    "- 编辑模式：compat。当前计划没有声明 write_intent，只允许非常小心地执行用户明确要求的修改；"
                    "如能读取到目标文件 sha256，仍应传入 expected_sha256。"
                )
        if not lines:
            return ""
        return "\n\n## 本次任务契约\n" + "\n".join(lines)

    def _tool_budget(self, task: PlannedTask) -> str:
        remote_lookup = {"context7_docs", "grep_code_search"}.intersection(task.mcp)
        if "web_search" not in task.mcp and not remote_lookup:
            if "code_locator" in task.mcp and "project_filesystem_readonly" in task.mcp:
                command_budget = ""
                if "command_runner" in task.mcp:
                    command_budget = (
                        "\n"
                        "- `run_command` 最多调用 1 次；命令返回后必须立刻根据结果给出结论。\n"
                        "- 如果用户拒绝审批或审批不可用，不要重复请求同一命令。"
                    )
                return (
                    "\n\n## 本次工具预算\n"
                    "- `locate_code` 最多调用 2 次。\n"
                    "- `get_file_outline` 最多调用 2 次。\n"
                    "- 文件读取前必须先说明要读哪些文件以及为什么。\n"
                    "- 如果目标文件过大，先获取文件信息，再用 locate_code/search_files 定位关键词，"
                    "按相关片段分段读取；不要为了完整性反复读取整份大文件。\n"
                    "- `read_file` / `read_multiple_files` 合计最多 4 次；读取到足够上下文后停止。"
                    "- 如果预算不足以覆盖全文，明确说明只完成了部分分析，不要把部分结论伪装成全文结论。"
                    + command_budget
                )
            if "command_runner" in task.mcp:
                return (
                    "\n\n## 本次工具预算\n"
                    "- `run_command` 最多调用 1 次。\n"
                    "- 命令返回后必须立刻根据 stdout/stderr/returncode 给出最终结果。\n"
                    "- 如果用户拒绝审批或审批不可用，不要重复请求同一命令，直接说明无法执行。"
                )
            return ""

        if remote_lookup and "web_search" not in task.mcp:
            lines = [
                "\n\n## 本次工具预算",
            ]
            if "context7_docs" in task.mcp:
                lines.append("- Context7：先用 `resolve-library-id` 解析库名，再用 `query-docs` 查询文档；每个工具最多调用 1 次。")
            if "grep_code_search" in task.mcp:
                lines.append("- Grep：`searchGitHub` 最多调用 2 次；优先使用具体代码片段、repo、path 或 language 过滤，避免过宽查询。")
            lines.append("- 查询内容会发送到外部远程 MCP；不要提交 API key、密码、私有代码或未公开业务信息。")
            return "\n".join(lines)

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

    def _tool_rules(self, task: PlannedTask) -> str:
        mcp = set(task.mcp)
        lines = [
            "\n\n## 执行收束规则",
            "- 只调用本任务实际分配到的工具；不要请求未分配的工具。",
            "- 拿到足够信息后必须停止调用工具，直接输出最终结果。",
            "- 如果工具结果不完美，也要基于已有可靠信息给出答案，并说明限制。",
        ]
        if "code_locator" in mcp:
            lines.append(
                "- code_locator 可用工具：locate_code、get_file_outline。"
                "先用 locate_code 找相关文件；不要跳过定位直接广泛读取。"
            )
        if "project_filesystem_readonly" in mcp:
            lines.append(
                "- project_filesystem_readonly 可用工具：list_directory、directory_tree、read_file、"
                "read_multiple_files、search_files、get_file_info。只读取最相关的少量文件，注意读取预算会被工具硬性限制。"
            )
        if "web_search" in mcp:
            lines.append("- web_search 可用工具：web_search、web_fetch。最多搜索 2 次；web_fetch 最多读取 3 个网页。")
        if "context7_docs" in mcp:
            lines.append(
                "- context7_docs 可用工具：resolve-library-id、query-docs。"
                "用于公开库文档查询；不要把私有代码、密钥或未公开业务信息发给 Context7。"
            )
        if "grep_code_search" in mcp:
            lines.append(
                "- grep_code_search 可用工具：searchGitHub。"
                "用于公开 GitHub 代码片段搜索；查询要尽量具体，可加 repo/path/language 过滤以避免超时。"
            )
        if "workspace_edit" in mcp:
            lines.append(
                "- workspace_edit 可用工具：create_file、write_file、replace_in_file、apply_unified_patch、delete_file。"
                "必须先说明要改/删的目标、理由和预期影响；"
                "优先使用 replace_in_file 或 apply_unified_patch 做小范围修改。"
            )
        if "safe_backup" in mcp:
            lines.append(
                "- safe_backup 可用工具：safe_delete_file。删除目标必须非常具体；工具会先备份再删除，"
                "不要删除 .env、.git 或 .agent_quarantine。"
            )
        if "command_runner" in mcp:
            lines.append("- command_runner 可用工具：run_command。只运行验证任务真正需要的命令，不要安装依赖或执行未知脚本。")
        if "git_tools" in mcp:
            lines.append(
                "- git_tools 可用工具：git_status、git_diff、git_log、git_commit。"
                "git_commit 只有用户明确要求提交时才使用。"
            )
        if not mcp:
            lines.append("- 本任务没有分配 MCP 工具，请直接基于上文和前序任务输出完成。")
        return "\n".join(lines) + "\n"

    def create_direct_answer_agent(self, model_id: str, instruction: str):
        Agent = agent_class()
        return Agent(
            name="direct_answer_agent",
            instructions=sanitize_text(
                "你是动态多智能体系统的主脑。当前问题不需要创建专家 Agent。"
                "请根据用户问题直接用中文回答，简洁、自然、准确。默认不要使用 emoji。"
                "介绍自己时统一自称“动态多智能体助手”或“主脑规划器”，"
                "不要自称 JPCoder AI、ChatGPT 或其它未由用户指定的品牌名。\n\n"
                f"回答要求：{instruction}"
            ),
            model=self.model_registry.get_model(model_id),
        )

    def create_solo_agent(self, model_id: str, mcp_servers=None):
        servers = list(mcp_servers or [])
        if servers:
            model_info = self.model_registry.get_model_info(model_id)
            if not model_info.get("supports_tools", True):
                raise ValueError(
                    "当前是 solo 单模型工具 Agent 模式，但所选模型不支持 tools/function calling："
                    f"{model_id}（{model_info.get('model_name') or '未知模型名'}）。"
                    "请换用支持工具调用的模型，或不要给 solo 挂载 MCP 工具。"
                )
        Agent = agent_class()
        return Agent(
            name="solo_agent",
            instructions=sanitize_text(
                "你正在 solo 单模型工具 Agent 模式下工作。"
                "本模式类似 Claude CLI：由一个模型独立理解用户需求，并在需要时调用工具完成任务。\n\n"
                "规则：\n"
                "- 默认使用中文，简洁自然，不要使用 emoji。\n"
                "- 可以读写文件、联网、运行命令、查看 git、运行测试和做验证，但必须通过已挂载工具真实完成，不要编造工具结果。\n"
                "- 写入、删除、命令、提交等高风险操作必须等待工具审批流程，不要绕过审批。\n"
                "- 不能创建多个 Agent，不能声称已经启动主脑、专家 Agent、前置副脑或汇总副脑。\n"
                "- 不要自动升级到 serial/full，也不要因为任务复杂就建议切换模式；当前模式下能做就直接单 Agent 做完。\n"
                "- 普通聊天、能力介绍、项目分析和代码任务中，不要主动提 serial/full、多 Agent 或模式切换。\n"
                "- 只有当用户明确要求“创建多个 Agent / 多专家分工 / 多 Agent 并行”时，才说明当前是 solo 单 Agent 模式，无法创建多个 Agent，需要用户显式切换到 serial 或 full。\n"
                "- 介绍自己时可以说你是“solo 单模型工具 Agent”，负责在当前项目中直接协助分析、修改和验证；不要主动列出多 Agent 限制。\n"
            ),
            model=self.model_registry.get_model(model_id),
            mcp_servers=servers,
        )

    def create_synthesizer_agent(self, model_id: str, run_workspace_server):
        Agent = agent_class()
        return Agent(
            name="final_synthesizer_agent",
            instructions=sanitize_text(load_skill("final_synthesizer")),
            model=self.model_registry.get_model(model_id),
            mcp_servers=[run_workspace_server],
        )
