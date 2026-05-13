from __future__ import annotations

import re

from runtime.agents.factory import AgentFactory
from runtime.config.settings import RuntimeSettings
from runtime.safety.privacy import PrivacyPolicy


SOLO_READONLY_BUDGET_PROFILE = {
    "max_read_calls": "6",
    "max_files_per_call": "3",
    "max_chars_per_file": "4500",
    "max_total_chars": "18000",
    "max_tree_depth": "2",
    "max_tree_entries": "180",
}

READONLY_MCP_IDS = ["project_filesystem_readonly", "code_locator", "git_tools"]
EDIT_MCP_IDS = ["workspace_edit", "safe_backup"]
COMMAND_MCP_IDS = ["command_runner"]
WEB_MCP_IDS = ["web_search"]

READ_MARKERS = [
    "分析",
    "查看",
    "读取",
    "检查",
    "解释",
    "项目",
    "代码",
    "文件",
    "目录",
    "函数",
    "类",
    "git",
    "diff",
    "status",
    ".py",
    ".md",
    ".json",
    "project",
    "code",
    "file",
    "directory",
    "function",
    "class",
]

EDIT_MARKERS = [
    "修改",
    "修复",
    "新增",
    "创建",
    "删除",
    "重构",
    "改造",
    "写入",
    "替换",
    "patch",
    "edit",
    "fix",
    "modify",
    "create",
    "delete",
    "refactor",
]

COMMAND_MARKERS = [
    "运行",
    "测试",
    "执行",
    "命令",
    "pytest",
    "unittest",
    "python ",
    "pip ",
    "command",
]
COMMAND_WORD_MARKERS = [
    "run",
    "test",
    "tests",
    "pytest",
    "unittest",
    "command",
]
NO_COMMAND_MARKERS = [
    "不要运行",
    "不运行",
    "无需运行",
    "不要执行",
    "不执行",
    "无需执行",
    "不要测试",
    "不测试",
    "无需测试",
    "do not run",
    "don't run",
    "no run",
    "without running",
    "do not test",
    "don't test",
    "no test",
]

WEB_MARKERS = [
    "联网",
    "搜索",
    "检索",
    "最新",
    "官方文档",
    "官方链接",
    "web search",
    "search the web",
    "latest",
    "official docs",
]

NO_EDIT_MARKERS = [
    "不要修改",
    "不修改",
    "无需修改",
    "只读",
    "read only",
    "readonly",
]


async def run_solo_request(
    run_input: str,
    model_registry,
    mcp_manager,
    hooks,
    run_agent,
    settings: RuntimeSettings | None = None,
) -> str:
    """Run one tool-capable Agent without planner/refiner/synthesizer."""

    settings = settings or RuntimeSettings.from_env()
    model_id = settings.select_model_id(model_registry, "orchestrator")
    factory = AgentFactory(model_registry, mcp_manager=mcp_manager)
    mcp_ids = _solo_mcp_ids_for_input(run_input, settings)
    if "project_filesystem_readonly" in mcp_ids:
        mcp_manager.set_readonly_budget_profile("project_filesystem_readonly", SOLO_READONLY_BUDGET_PROFILE)
    servers = await mcp_manager.get_many(mcp_ids)
    agent = factory.create_solo_agent(model_id, mcp_servers=servers)
    result = await run_agent(agent, run_input, hooks, max_turns=20)
    return str(result.final_output)


def _solo_mcp_ids_for_input(user_input: str, settings: RuntimeSettings) -> list[str]:
    text = str(user_input or "").lower()
    edit_blocked = _contains_any(text, NO_EDIT_MARKERS)
    command_blocked = _contains_any(text, NO_COMMAND_MARKERS)
    wants_edit = _contains_any(text, EDIT_MARKERS) and not edit_blocked
    wants_command = (
        _contains_any(text, COMMAND_MARKERS) or _contains_any_word(text, COMMAND_WORD_MARKERS)
    ) and not command_blocked
    wants_web = _contains_any(text, WEB_MARKERS)
    wants_read = wants_edit or wants_command or wants_web or _contains_any(text, READ_MARKERS)

    mcp_ids: list[str] = []
    if wants_read:
        mcp_ids.extend(READONLY_MCP_IDS)
    if wants_edit:
        mcp_ids.extend(EDIT_MCP_IDS)
    if wants_command:
        mcp_ids.extend(COMMAND_MCP_IDS)
    if wants_web and settings.privacy_mode != "offline" and PrivacyPolicy(settings.privacy_mode).allows_network_tools:
        mcp_ids.extend(WEB_MCP_IDS)

    return _dedupe(mcp_ids)


def _contains_any(text: str, markers: list[str]) -> bool:
    return any(marker.lower() in text for marker in markers)


def _contains_any_word(text: str, markers: list[str]) -> bool:
    return any(re.search(rf"(?<![a-z0-9_]){re.escape(marker.lower())}(?![a-z0-9_])", text) for marker in markers)


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
